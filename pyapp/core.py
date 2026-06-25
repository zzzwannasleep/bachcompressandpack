"""打包相关的纯逻辑：与界面框架完全无关，便于单元测试。

这里集中了文件扫描、分组装箱、归档工具检测与压缩执行等能力，
原先散落在 app.py 中。UI 层（PyQt6）只调用本模块，不反向依赖。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]

HARD_LIMIT_BYTES = 100 * 1024 * 1024
PACKAGE_MARGIN_BYTES = 512 * 1024
ARCHIVE_FORMATS = ("zip", "7z", "rar")

IS_WINDOWS = os.name == "nt"

# 各平台可执行文件名。Windows 带 .exe；Linux/macOS 用裸名（7zz 是官方跨平台 CLI）。
if IS_WINDOWS:
    SEVEN_ZIP_COMMAND_NAMES = ("7z.exe", "7z", "7zz.exe", "7zz", "7za.exe", "7za")
    RAR_COMMAND_NAMES = ("rar.exe", "rar", "WinRAR.exe", "WinRAR")
else:
    SEVEN_ZIP_COMMAND_NAMES = ("7zz", "7z", "7za")
    RAR_COMMAND_NAMES = ("rar",)

# 随程序分发时，可把压缩工具放到这些子目录（相对运行目录）下，开箱即用。
_SEVEN_ZIP_BUNDLE_DIRS = ("tools/7zip", "tools/7z", "tools", "")
_RAR_BUNDLE_DIRS = ("tools/rar", "tools/winrar", "tools", "")


def _build_bundle_paths(dirs: tuple[str, ...], names: tuple[str, ...]) -> tuple[str, ...]:
    paths: list[str] = []
    for directory in dirs:
        for name in names:
            paths.append(f"{directory}/{name}" if directory else name)
    return tuple(paths)


BUNDLED_7Z_RELATIVE_PATHS = _build_bundle_paths(_SEVEN_ZIP_BUNDLE_DIRS, SEVEN_ZIP_COMMAND_NAMES)
BUNDLED_RAR_RELATIVE_PATHS = _build_bundle_paths(_RAR_BUNDLE_DIRS, RAR_COMMAND_NAMES)

# 非 Windows 上 GUI/双击启动时 PATH 往往很精简，这里补充常见安装目录。
_POSIX_KNOWN_BIN_DIRS = (
    "/usr/bin",
    "/usr/local/bin",
    "/opt/homebrew/bin",  # Apple Silicon Homebrew
    "/bin",
    "/snap/bin",
)


@dataclass(slots=True)
class FileEntry:
    source_path: Path
    archive_path: Path
    size: int
    compressed_hint: int | None = None


@dataclass(slots=True)
class ArchiveToolSpec:
    format_name: str
    extension: str
    backend: str
    executable: Path | None
    display_name: str


def format_bytes(size: int) -> str:
    kb = 1024.0
    mb = kb * 1024.0
    gb = mb * 1024.0
    value = float(size)
    if value >= gb:
        return f"{value / gb:.2f} GB"
    if value >= mb:
        return f"{value / mb:.2f} MB"
    if value >= kb:
        return f"{value / kb:.2f} KB"
    return f"{size} B"


def get_runtime_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return REPO_ROOT


def iter_bundled_tool_matches(relative_paths: Iterable[str]) -> list[Path]:
    runtime_dir = get_runtime_dir()
    matches: list[Path] = []
    seen: set[str] = set()
    for relative_path in relative_paths:
        candidate = runtime_dir / relative_path
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            matches.append(candidate)
    return matches


def path_to_archive_name(path: Path) -> str:
    return path.as_posix()


# 兼容旧名称：测试与历史代码使用 path_to_zip_name
def path_to_zip_name(path: Path) -> str:
    return path_to_archive_name(path)


def append_suffix(path: Path, index: int) -> Path:
    stem = path.stem or "file"
    suffix = path.suffix
    name = f"{stem} ({index}){suffix}"
    if path.parent == Path("."):
        return Path(name)
    return path.parent / name


def sanitize_file_name(name: str) -> str:
    forbidden = '<>:"/\\|?*'
    cleaned = "".join("_" if char in forbidden else char for char in name).strip()
    return cleaned or "batch_package"


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def common_ancestor(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    first = paths[0].resolve()
    candidates = [first, *first.parents]
    resolved = [path.resolve() for path in paths]
    for candidate in candidates:
        if all(is_relative_to(path, candidate) for path in resolved):
            return candidate
    return None


def derive_default_output_dir(roots: list[Path]) -> Path | None:
    base_dirs: list[Path] = []
    for root in roots:
        if root.is_dir():
            base_dirs.append(root)
        elif root.parent != root:
            base_dirs.append(root.parent)
    return common_ancestor(base_dirs) or (base_dirs[0] if base_dirs else None)


def archive_prefix(roots: list[Path]) -> str:
    if len(roots) == 1:
        source = roots[0]
        name = source.stem or source.name or "batch_package"
    else:
        name = "batch_package"
    return sanitize_file_name(name)


def estimate_entry_bytes(file: FileEntry) -> int:
    base_size = file.compressed_hint if file.compressed_hint is not None else file.size
    return base_size + 256 + len(path_to_archive_name(file.archive_path).encode("utf-8"))


def build_initial_groups(
    files: list[FileEntry],
    limit_bytes: int = HARD_LIMIT_BYTES,
    margin_bytes: int = PACKAGE_MARGIN_BYTES,
) -> list[list[FileEntry]]:
    if not files:
        return []
    target_limit = limit_bytes - margin_bytes
    sorted_files = sorted(
        files,
        key=lambda item: (-estimate_entry_bytes(item), path_to_archive_name(item.archive_path)),
    )
    groups: list[tuple[int, list[FileEntry]]] = []
    for file in sorted_files:
        estimate = estimate_entry_bytes(file)
        placed = False
        if estimate <= target_limit:
            for index, (used, group) in enumerate(groups):
                if used + estimate <= target_limit:
                    group.append(file)
                    groups[index] = (used + estimate, group)
                    placed = True
                    break
        if not placed:
            groups.append((estimate, [file]))
    return [group for _, group in groups]


def choose_split_index(group: list[FileEntry]) -> int:
    if len(group) <= 1:
        return 1
    half = sum(estimate_entry_bytes(item) for item in group) // 2
    collected = 0
    for index, file in enumerate(group, start=1):
        collected += estimate_entry_bytes(file)
        if collected >= half:
            return min(index, len(group) - 1)
    return len(group) // 2


def uniquify_archive_paths(files: list[FileEntry]) -> None:
    used: set[str] = set()
    counters: dict[str, int] = {}
    for entry in files:
        original = entry.archive_path
        key = path_to_archive_name(original)
        counters.setdefault(key, 1)
        candidate = original
        while path_to_archive_name(candidate) in used:
            counters[key] += 1
            candidate = append_suffix(original, counters[key])
        entry.archive_path = candidate
        used.add(path_to_archive_name(candidate))


def push_file_entry(
    source_path: Path,
    archive_path: Path,
    files: list[FileEntry],
    warnings: list[str],
    seen_sources: set[Path],
) -> None:
    try:
        dedupe_key = source_path.resolve()
    except OSError:
        dedupe_key = source_path
    if dedupe_key in seen_sources:
        return
    seen_sources.add(dedupe_key)
    try:
        stat = source_path.stat()
    except OSError as exc:
        warnings.append(f"无法读取文件 {source_path}: {exc}")
        return
    files.append(FileEntry(source_path=source_path, archive_path=archive_path, size=stat.st_size))


def scan_inputs(roots: Iterable[Path]) -> tuple[list[FileEntry], list[str]]:
    files: list[FileEntry] = []
    warnings: list[str] = []
    seen_sources: set[Path] = set()
    root_list = [Path(root) for root in roots]

    for root in root_list:
        if not root.exists():
            warnings.append(f"已跳过不存在的路径: {root}")
            continue
        if root.is_file():
            push_file_entry(root, Path(root.name), files, warnings, seen_sources)
            continue

        root_name = Path(root.name or "files")
        for child in root.rglob("*"):
            if child.is_file():
                relative = child.relative_to(root)
                push_file_entry(child, root_name / relative, files, warnings, seen_sources)

    files.sort(key=lambda item: path_to_archive_name(item.archive_path))
    uniquify_archive_paths(files)
    return files, warnings


# ---------------------------------------------------------------------------
# 归档工具检测与解析
# ---------------------------------------------------------------------------
def iter_program_matches(command_names: tuple[str, ...], known_paths: list[Path]) -> list[Path]:
    matches: list[Path] = []
    seen: set[str] = set()

    for command_name in command_names:
        resolved = shutil.which(command_name)
        if not resolved:
            continue
        path = Path(resolved)
        key = str(path).lower()
        if key not in seen and path.exists():
            matches.append(path)
            seen.add(key)

    for path in known_paths:
        key = str(path).lower()
        if key not in seen and path.exists():
            matches.append(path)
            seen.add(key)

    return matches


def _posix_known_tool_paths(names: tuple[str, ...]) -> list[Path]:
    return [Path(directory) / name for directory in _POSIX_KNOWN_BIN_DIRS for name in names]


def detect_7z_executable() -> Path | None:
    bundled_matches = iter_bundled_tool_matches(BUNDLED_7Z_RELATIVE_PATHS)
    if bundled_matches:
        return bundled_matches[0]

    known_paths: list[Path] = []
    if IS_WINDOWS:
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_name)
            if base:
                known_paths.append(Path(base) / "7-Zip" / "7z.exe")
    else:
        known_paths.extend(_posix_known_tool_paths(SEVEN_ZIP_COMMAND_NAMES))
    matches = iter_program_matches(SEVEN_ZIP_COMMAND_NAMES, known_paths)
    return matches[0] if matches else None


def detect_rar_executable() -> Path | None:
    bundled_matches = iter_bundled_tool_matches(BUNDLED_RAR_RELATIVE_PATHS)
    if bundled_matches:
        return bundled_matches[0]

    known_paths: list[Path] = []
    if IS_WINDOWS:
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_name)
            if base:
                known_paths.extend(
                    [Path(base) / "WinRAR" / "rar.exe", Path(base) / "WinRAR" / "WinRAR.exe"]
                )
    else:
        known_paths.extend(_posix_known_tool_paths(RAR_COMMAND_NAMES))
    matches = iter_program_matches(RAR_COMMAND_NAMES, known_paths)
    return matches[0] if matches else None


def looks_like_7z_executable(path: Path) -> bool:
    return path.name.lower() in {"7z", "7z.exe", "7za", "7za.exe", "7zz", "7zz.exe"}


def looks_like_rar_executable(path: Path) -> bool:
    return path.name.lower() in {"rar", "rar.exe", "winrar", "winrar.exe"}


def normalize_tool_path(path_text: str) -> Path | None:
    text = path_text.strip().strip('"')
    if not text:
        return None
    return Path(text)


def resolve_archive_tool(format_name: str, path_text: str = "") -> ArchiveToolSpec:
    if format_name not in ARCHIVE_FORMATS:
        raise RuntimeError(f"不支持的压缩格式: {format_name}")

    user_path = normalize_tool_path(path_text)
    if user_path is not None:
        if not user_path.exists():
            raise RuntimeError("压缩程序路径不存在。")
        if format_name == "rar":
            if not looks_like_rar_executable(user_path):
                raise RuntimeError("RAR 格式需要 rar.exe 或 WinRAR.exe。")
            return ArchiveToolSpec("rar", "rar", "rar", user_path, "RAR/WinRAR")
        if not looks_like_7z_executable(user_path):
            raise RuntimeError("ZIP/7Z 请选择 7z.exe、7zz 或 7za。")
        return ArchiveToolSpec(format_name, format_name, "seven_zip", user_path, "7-Zip")

    if format_name == "zip":
        detected = detect_7z_executable()
        if detected is None:
            return ArchiveToolSpec("zip", "zip", "builtin_zip", None, "内置 ZIP")
        return ArchiveToolSpec("zip", "zip", "seven_zip", detected, "7-Zip")

    if format_name == "7z":
        detected = detect_7z_executable()
        if detected is None:
            raise RuntimeError("未找到 7-Zip，请安装 7-Zip 或手动指定 7z.exe。")
        return ArchiveToolSpec("7z", "7z", "seven_zip", detected, "7-Zip")

    detected = detect_rar_executable()
    if detected is None:
        raise RuntimeError("未找到 WinRAR / rar，请安装后再使用 RAR 格式。")
    return ArchiveToolSpec("rar", "rar", "rar", detected, "RAR/WinRAR")


def describe_archive_tool(format_name: str, path_text: str = "") -> str:
    try:
        spec = resolve_archive_tool(format_name, path_text)
    except Exception as exc:  # noqa: BLE001
        return f"当前压缩程序: {exc}"
    if spec.executable is None:
        return f"当前压缩程序: {spec.display_name}"
    return f"当前压缩程序: {spec.display_name} | {spec.executable}"


# ---------------------------------------------------------------------------
# 压缩执行
# ---------------------------------------------------------------------------
def next_available_archive_path(output_dir: Path, prefix: str, index: int, extension: str) -> Path:
    base_name = f"{prefix}_{index:03}"
    candidate = output_dir / f"{base_name}.{extension}"
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        candidate = output_dir / f"{base_name}_{suffix}.{extension}"
        if not candidate.exists():
            return candidate
        suffix += 1


def materialize_group(group: list[FileEntry], staging_dir: Path) -> None:
    for file in group:
        target = staging_dir / file.archive_path
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(file.source_path, target)
        except OSError:
            shutil.copy2(file.source_path, target)


def write_stage_listfile(staging_dir: Path) -> Path:
    items = sorted((path.name for path in staging_dir.iterdir()), key=str.lower)
    if not items:
        raise RuntimeError("没有可压缩的文件。")
    listfile = staging_dir / "__archive_input_list.txt"
    listfile.write_text("\n".join(items), encoding="utf-8")
    return listfile


def _archiver_creationflags() -> int:
    # Windows 下避免外部压缩程序弹出控制台窗口
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def run_external_archiver(command: list[str], cwd: Path, tool_name: str) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
        creationflags=_archiver_creationflags(),
    )
    if completed.returncode == 0:
        return
    detail = completed.stderr.strip() or completed.stdout.strip() or f"退出码 {completed.returncode}"
    raise RuntimeError(f"{tool_name} 执行失败: {detail}")


def write_builtin_zip(group: list[FileEntry], archive_path: Path) -> None:
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as zf:
        for file in group:
            zf.write(file.source_path, arcname=path_to_archive_name(file.archive_path))


def write_external_archive(group: list[FileEntry], archive_path: Path, tool_spec: ArchiveToolSpec) -> None:
    with TemporaryDirectory(dir=archive_path.parent, prefix="pack_stage_") as staging_path:
        staging_dir = Path(staging_path)
        materialize_group(group, staging_dir)
        listfile = write_stage_listfile(staging_dir)

        if tool_spec.backend == "seven_zip":
            command = [
                str(tool_spec.executable),
                "a",
                "-scsUTF-8",
                f"-t{tool_spec.format_name}",
                str(archive_path),
                f"@{listfile.name}",
                "-y",
                "-bd",
                "-bso0",
                "-bsp0",
            ]
            run_external_archiver(command, staging_dir, "7-Zip")
        elif tool_spec.backend == "rar":
            command = [
                str(tool_spec.executable),
                "a",
                "-ma5",
                "-r",
                "-idq",
                str(archive_path),
                f"@{listfile.name}",
            ]
            run_external_archiver(command, staging_dir, "RAR/WinRAR")
        else:  # pragma: no cover - guarded by resolve_archive_tool
            raise RuntimeError(f"未知压缩后端: {tool_spec.backend}")

    if not archive_path.exists():
        raise RuntimeError("压缩程序执行完成，但没有生成归档文件。")


def create_archive(group: list[FileEntry], archive_path: Path, tool_spec: ArchiveToolSpec) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if tool_spec.backend == "builtin_zip":
        write_builtin_zip(group, archive_path)
        return
    write_external_archive(group, archive_path, tool_spec)


def write_group_recursive(
    group: list[FileEntry],
    output_dir: Path,
    prefix: str,
    archive_index: list[int],
    created_archives: list[Path],
    event_queue: "Queue[tuple[str, object]]",
    tool_spec: ArchiveToolSpec,
    limit_bytes: int = HARD_LIMIT_BYTES,
) -> None:
    if not group:
        return

    final_path = next_available_archive_path(output_dir, prefix, archive_index[0], tool_spec.extension)
    with TemporaryDirectory(dir=output_dir, prefix="pack_probe_") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        temp_path = temp_dir / f"probe.{tool_spec.extension}"
        create_archive(group, temp_path, tool_spec)
        actual_size = temp_path.stat().st_size
        if actual_size <= limit_bytes:
            shutil.move(str(temp_path), str(final_path))
            created_archives.append(final_path)
            event_queue.put(("pack_archive", final_path))
            event_queue.put(
                (
                    "pack_log",
                    f"已生成 {final_path.name}，包含 {len(group)} 个文件，大小 {format_bytes(actual_size)}。",
                )
            )
            archive_index[0] += 1
            return

    limit_mb = limit_bytes / (1024 * 1024)
    if len(group) == 1:
        raise RuntimeError(
            f"文件 {group[0].source_path} 单独压缩后仍然超过 {limit_mb:.0f}MB，无法满足规则。"
        )

    event_queue.put(
        ("pack_log", f"检测到一组压缩后超过 {limit_mb:.0f}MB，正在自动拆小（{len(group)} 个文件）。")
    )
    split_index = choose_split_index(group)
    write_group_recursive(
        group[:split_index], output_dir, prefix, archive_index, created_archives, event_queue, tool_spec, limit_bytes
    )
    write_group_recursive(
        group[split_index:], output_dir, prefix, archive_index, created_archives, event_queue, tool_spec, limit_bytes
    )


def pack_files(
    files: list[FileEntry],
    output_dir: Path,
    prefix: str,
    event_queue: "Queue[tuple[str, object]]",
    tool_spec: ArchiveToolSpec,
    limit_bytes: int = HARD_LIMIT_BYTES,
    margin_bytes: int = PACKAGE_MARGIN_BYTES,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    groups = build_initial_groups(files, limit_bytes, margin_bytes)
    archive_index = [1]
    created_archives: list[Path] = []
    for group in groups:
        write_group_recursive(
            group, output_dir, prefix, archive_index, created_archives, event_queue, tool_spec, limit_bytes
        )
    return created_archives
