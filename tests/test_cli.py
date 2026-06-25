import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pyapp import cli  # noqa: E402
from pyapp.core import (  # noqa: E402
    build_initial_groups,
    detect_7z_executable,
    pack_files,
)


def _write_file(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)


class CliPackTests(unittest.TestCase):
    def test_pack_creates_zip_archive(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = base / "src"
            _write_file(src / "a.bin", 1024)
            _write_file(src / "sub" / "b.bin", 2048)
            out = base / "out"

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli.main(
                    ["pack", str(src), "-o", str(out), "-f", "zip", "-p", "demo", "--quiet"]
                )

            self.assertEqual(code, 0)
            archives = sorted(out.glob("*.zip"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0].name, "demo_001.zip")

    def test_pack_json_output_lists_archives(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = base / "data"
            _write_file(src / "x.bin", 4096)
            out = base / "out"

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli.main(["pack", str(src), "-o", str(out), "--json", "--quiet"])

            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(len(payload["archives"]), 1)
            self.assertEqual(payload["share_links"], [])

    def test_pack_limit_splits_into_multiple_archives(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = base / "big"
            for i in range(3):
                _write_file(src / f"f{i}.bin", 1024 * 1024)  # 1 MB each
            out = base / "out"

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli.main(
                    ["pack", str(src), "-o", str(out), "-f", "zip", "--limit", "1.5", "--quiet"]
                )

            self.assertEqual(code, 0)
            # 3 MB 内容、每包 ≤1.5MB（实际不可压缩）→ 必然拆成多个包
            archives = list(out.glob("*.zip"))
            self.assertGreaterEqual(len(archives), 2)

    def test_pack_missing_input_exits_with_error(self) -> None:
        with self.assertRaises(SystemExit):
            cli.main(["pack", "this-path-does-not-exist-xyz", "--quiet"])

    def test_tools_command_runs(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["tools"])
        self.assertEqual(code, 0)
        self.assertIn("当前压缩程序", buf.getvalue())

    def test_no_command_prints_help(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main([])
        self.assertEqual(code, 1)


class CrossPlatformDetectionTests(unittest.TestCase):
    def test_command_name_lists_are_platform_appropriate(self) -> None:
        from pyapp import core

        if core.IS_WINDOWS:
            self.assertIn("7z.exe", core.SEVEN_ZIP_COMMAND_NAMES)
        else:
            self.assertIn("7zz", core.SEVEN_ZIP_COMMAND_NAMES)
            self.assertNotIn("7z.exe", core.SEVEN_ZIP_COMMAND_NAMES)

    def test_bundled_paths_include_tools_subdirs(self) -> None:
        from pyapp import core

        joined = "\n".join(core.BUNDLED_7Z_RELATIVE_PATHS)
        self.assertIn("tools/7zip/", joined)
        self.assertIn("tools/7z/", joined)


if __name__ == "__main__":
    unittest.main()
