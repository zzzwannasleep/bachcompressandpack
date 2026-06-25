"""命令行接口（CLI）：跨平台（Windows / Linux / macOS）的批量压缩打包工具。

设计原则：
- 只依赖 ``core``（纯标准库）即可完成打包；``lanzou_client`` 仅在用到上传时才延迟导入，
  因此在没有安装 GUI/上传依赖的环境里依然可以打包。
- 复用 GUI 走的同一套 ``event_queue.put((kind, payload))`` 事件协议，CLI 用一个把事件
  打印到 stdout 的轻量 sink 适配，业务逻辑零改动。

用法示例::

    # 把若干文件/目录按每包 <=100MB 切分压缩为 zip
    bachpack pack ./photos ./report.pdf -o ./out -f zip

    # 7z 格式，自定义前缀和单包上限
    bachpack pack ./data -f 7z -p backup --limit 50

    # 打包并自动上传到蓝奏云（Cookie 从环境变量读取）
    export LANZOU_COOKIE='ylogin=...; phpdisk_info=...'
    bachpack pack ./data --upload --folder-id 0

    # 仅上传现成压缩包
    bachpack upload out/*.zip --cookie-file cookie.txt
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from . import core

PROG = "bachpack"
__version__ = "1.0.1"


class StreamEventSink:
    """把 ``put((kind, payload))`` 事件实时打印到 stdout/stderr。

    与 GUI 的 EventBridge 实现同一个鸭子类型接口，因此 core / lanzou_client
    完全感知不到自己跑在 CLI 还是 GUI 里。
    """

    def __init__(self, *, quiet: bool = False) -> None:
        self.quiet = quiet
        self.archives: list[Path] = []
        self.share_links: list[object] = []

    def _emit(self, text: str, *, err: bool = False) -> None:
        if self.quiet and not err:
            return
        print(text, file=sys.stderr if err else sys.stdout, flush=True)

    def put(self, item: tuple[str, object]) -> None:
        kind, payload = item
        if kind in ("pack_log", "upload_log"):
            self._emit(str(payload))
        elif kind == "pack_archive":
            self.archives.append(Path(str(payload)))
        elif kind == "upload_link":
            self.share_links.append(payload)
        elif kind == "upload_progress":
            # payload = (done, total)；安静模式下不打印进度
            if not self.quiet and isinstance(payload, tuple) and len(payload) == 2:
                done, total = payload
                self._emit(f"上传进度: {done}/{total}", err=False)
        # 其它事件（pack_done / pack_archives_ready 等）CLI 无需处理


def _eprint(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Cookie 读取
# ---------------------------------------------------------------------------
def resolve_cookie_text(args: argparse.Namespace) -> str:
    """按优先级取 Cookie 文本：--cookie > --cookie-file > 环境变量。"""
    if getattr(args, "cookie", None):
        return args.cookie
    cookie_file = getattr(args, "cookie_file", None)
    if cookie_file:
        path = Path(cookie_file)
        if not path.exists():
            raise SystemExit(f"错误：Cookie 文件不存在: {path}")
        return path.read_text(encoding="utf-8")
    env_cookie = os.environ.get("LANZOU_COOKIE")
    if env_cookie:
        return env_cookie
    raise SystemExit(
        "错误：需要蓝奏云 Cookie。请用 --cookie / --cookie-file 指定，"
        "或设置环境变量 LANZOU_COOKIE（至少包含 ylogin 和 phpdisk_info）。"
    )


def parse_cookie(args: argparse.Namespace) -> dict[str, str]:
    from .lanzou_client import parse_cookie_string

    try:
        return parse_cookie_string(resolve_cookie_text(args))
    except ValueError as exc:
        raise SystemExit(f"错误：{exc}")


# ---------------------------------------------------------------------------
# pack
# ---------------------------------------------------------------------------
def _resolve_inputs(raw_inputs: Sequence[str]) -> list[Path]:
    roots = [Path(item) for item in raw_inputs]
    missing = [str(root) for root in roots if not root.exists()]
    if missing:
        raise SystemExit("错误：以下输入路径不存在：\n  " + "\n  ".join(missing))
    return roots


def cmd_pack(args: argparse.Namespace) -> int:
    roots = _resolve_inputs(args.inputs)

    files, warnings = core.scan_inputs(roots)
    for warning in warnings:
        _eprint(f"警告：{warning}")
    if not files:
        raise SystemExit("错误：没有扫描到任何可压缩的文件。")

    # 输出目录
    if args.output:
        output_dir = Path(args.output)
    else:
        output_dir = core.derive_default_output_dir(roots) or Path.cwd()
    output_dir = output_dir.resolve()

    # 前缀
    prefix = core.sanitize_file_name(args.prefix) if args.prefix else core.archive_prefix(roots)

    # 压缩工具
    try:
        tool_spec = core.resolve_archive_tool(args.format, args.tool_path or "")
    except RuntimeError as exc:
        raise SystemExit(f"错误：{exc}")

    limit_bytes = int(args.limit * 1024 * 1024)
    if limit_bytes <= 0:
        raise SystemExit("错误：--limit 必须为正数（单位 MB）。")

    sink = StreamEventSink(quiet=args.quiet)
    if not args.quiet:
        total_size = core.format_bytes(sum(item.size for item in files))
        _eprint(
            f"扫描到 {len(files)} 个文件（{total_size}），"
            f"格式 {tool_spec.format_name}，工具 {tool_spec.display_name}，"
            f"单包上限 {args.limit:g}MB，输出目录 {output_dir}"
        )

    try:
        archives = core.pack_files(
            files, output_dir, prefix, sink, tool_spec, limit_bytes=limit_bytes
        )
    except RuntimeError as exc:
        raise SystemExit(f"错误：{exc}")

    share_links: list[object] = []
    if args.upload:
        share_links = _do_upload(archives, args, sink)

    if args.json:
        _print_json(archives, share_links)
    elif not args.quiet:
        _eprint(f"完成：共生成 {len(archives)} 个压缩包。")
    else:
        for archive in archives:
            print(archive)
    return 0


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------
def _do_upload(
    archives: list[Path], args: argparse.Namespace, sink: StreamEventSink
) -> list[object]:
    from .lanzou_client import upload_archives

    cookie = parse_cookie(args)
    folder_id = str(args.folder_id) if args.folder_id is not None else ""
    try:
        links = upload_archives(archives, cookie, folder_id, sink, args.ua or "")
    except RuntimeError as exc:
        raise SystemExit(f"错误：上传失败：{exc}")
    return list(links)


def cmd_upload(args: argparse.Namespace) -> int:
    archives = [Path(item) for item in args.archives]
    missing = [str(a) for a in archives if not a.is_file()]
    if missing:
        raise SystemExit("错误：以下压缩包不存在：\n  " + "\n  ".join(missing))

    sink = StreamEventSink(quiet=args.quiet)
    links = _do_upload(archives, args, sink)

    if args.json:
        _print_json(archives, links)
    elif not args.quiet:
        _eprint(f"完成：共上传 {len(links)} 个文件。")
    return 0


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def _print_json(archives: list[Path], share_links: Sequence[object]) -> None:
    payload = {
        "archives": [str(a) for a in archives],
        "share_links": [
            {
                "file_name": getattr(link, "file_name", ""),
                "url": getattr(link, "url", ""),
                "password": getattr(link, "password", ""),
            }
            for link in share_links
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# tools 子命令：查看检测到的压缩工具
# ---------------------------------------------------------------------------
def cmd_tools(args: argparse.Namespace) -> int:
    for fmt in core.ARCHIVE_FORMATS:
        print(core.describe_archive_tool(fmt))
    return 0


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------
def _add_upload_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("蓝奏云上传选项")
    group.add_argument("--cookie", help="蓝奏云 Cookie 字符串（至少含 ylogin 和 phpdisk_info）")
    group.add_argument("--cookie-file", help="从文件读取 Cookie")
    group.add_argument(
        "--folder-id",
        type=int,
        default=None,
        help="目标文件夹 ID，留空上传到根目录",
    )
    group.add_argument("--ua", default="", help="上传使用的 User-Agent（可选）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="批量压缩打包：按每包 ≤ 指定大小切分，可选上传到蓝奏云（跨 Windows/Linux/macOS）。",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # pack
    p_pack = sub.add_parser("pack", help="扫描文件/目录并按大小上限压缩成多个包")
    p_pack.add_argument("inputs", nargs="+", help="要打包的文件或目录")
    p_pack.add_argument(
        "-f", "--format", choices=core.ARCHIVE_FORMATS, default="zip", help="压缩格式（默认 zip）"
    )
    p_pack.add_argument("-o", "--output", help="输出目录（默认放在输入的公共父目录）")
    p_pack.add_argument("-p", "--prefix", help="压缩包文件名前缀（默认按输入推断）")
    p_pack.add_argument(
        "--tool-path", help="手动指定 7z / rar 可执行文件路径（覆盖自动检测）"
    )
    p_pack.add_argument(
        "--limit", type=float, default=100.0, help="单包大小上限，单位 MB（默认 100）"
    )
    p_pack.add_argument("--upload", action="store_true", help="打包后自动上传到蓝奏云")
    p_pack.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    p_pack.add_argument("-q", "--quiet", action="store_true", help="安静模式，仅输出结果")
    _add_upload_options(p_pack)
    p_pack.set_defaults(func=cmd_pack)

    # upload
    p_upload = sub.add_parser("upload", help="上传现成压缩包到蓝奏云")
    p_upload.add_argument("archives", nargs="+", help="要上传的压缩包文件")
    p_upload.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    p_upload.add_argument("-q", "--quiet", action="store_true", help="安静模式")
    _add_upload_options(p_upload)
    p_upload.set_defaults(func=cmd_upload)

    # tools
    p_tools = sub.add_parser("tools", help="显示当前检测到的压缩工具")
    p_tools.set_defaults(func=cmd_tools)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _eprint("\n已取消。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
