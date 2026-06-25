import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pyapp.core import (  # noqa: E402
    HARD_LIMIT_BYTES,
    PACKAGE_MARGIN_BYTES,
    FileEntry,
    build_initial_groups,
    detect_7z_executable,
    estimate_entry_bytes,
    path_to_zip_name,
    uniquify_archive_paths,
)
from pyapp.lanzou_client import (  # noqa: E402
    BrowserCookieCandidate,
    create_lanzou_client,
    has_required_lanzou_cookie,
    open_lanzou_login_in_browser,
    parse_cookie_string,
    wait_for_browser_login_cookie,
)


def sample_file(name: str, size_mb: int) -> FileEntry:
    return FileEntry(
        source_path=Path(name),
        archive_path=Path(name),
        size=size_mb * 1024 * 1024,
    )


class PackagingTests(unittest.TestCase):
    def test_duplicate_archive_names_are_renamed(self) -> None:
        files = [
            sample_file("same.txt", 1),
            sample_file("same.txt", 1),
            sample_file("same.txt", 1),
        ]

        uniquify_archive_paths(files)
        names = [path_to_zip_name(file.archive_path) for file in files]
        self.assertEqual(names[0], "same.txt")
        self.assertEqual(names[1], "same (2).txt")
        self.assertEqual(names[2], "same (3).txt")

    def test_build_groups_keep_estimated_limit(self) -> None:
        files = [
            sample_file("a.bin", 60),
            sample_file("b.bin", 40),
            sample_file("c.bin", 40),
            sample_file("d.bin", 10),
        ]

        target_limit = HARD_LIMIT_BYTES - PACKAGE_MARGIN_BYTES
        groups = build_initial_groups(files)

        for group in groups:
            if len(group) > 1:
                total = sum(estimate_entry_bytes(file) for file in group)
                self.assertLessEqual(total, target_limit)

    def test_parse_cookie_string(self) -> None:
        cookie = parse_cookie_string("ylogin=123; phpdisk_info=abc; PHPSESSID=xyz")
        self.assertEqual(cookie["ylogin"], "123")
        self.assertEqual(cookie["phpdisk_info"], "abc")
        self.assertEqual(cookie["PHPSESSID"], "xyz")

    def test_has_required_lanzou_cookie(self) -> None:
        self.assertTrue(has_required_lanzou_cookie({"ylogin": "1", "phpdisk_info": "abc"}))
        self.assertFalse(has_required_lanzou_cookie({"ylogin": "1"}))

    def test_detect_7z_executable_prefers_bundled_tool(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir)
            tool_path = runtime_dir / "tools" / "7zip" / "7z.exe"
            tool_path.parent.mkdir(parents=True, exist_ok=True)
            tool_path.write_text("", encoding="utf-8")

            with mock.patch("pyapp.core.get_runtime_dir", return_value=runtime_dir):
                detected = detect_7z_executable()

        self.assertEqual(detected, tool_path)

    def test_create_lanzou_client_reports_failed_login(self) -> None:
        class _Jar:
            def update(self, _c: dict[str, str]) -> None:
                pass

        class FakeCloud:
            SUCCESS = 0
            FAILED = -1
            NETWORK_ERROR = 9
            CAPTCHA_ERROR = 10

            def __init__(self) -> None:
                self._headers: dict[str, str] = {}
                self._session = type("S", (), {"cookies": _Jar()})()
                self._uid = 0

        with (
            mock.patch("pyapp.lanzou_client.get_lanzou_cloud_class", return_value=FakeCloud),
            mock.patch(
                "pyapp.lanzou_client._api_probe",
                return_value={"logged_in": False, "zt": 9, "info": "login not"},
            ),
            mock.patch("pyapp.lanzou_client.diagnose_cookie", return_value="(diag skipped)"),
        ):
            with self.assertRaisesRegex(RuntimeError, "未登录"):
                create_lanzou_client({"ylogin": "1", "phpdisk_info": "abc"})

    def test_wait_for_browser_login_cookie_retries_until_cookie_is_valid(self) -> None:
        candidate = BrowserCookieCandidate(
            cookie={"ylogin": "1", "phpdisk_info": "abc"},
            source="Chrome / pc.woozooo.com",
        )
        monotonic_values = iter([0.0, 0.2, 0.4])

        with (
            mock.patch(
                "pyapp.lanzou_client.build_browser_cookie_candidates",
                side_effect=[[], [candidate], [candidate]],
            ),
            mock.patch(
                "pyapp.lanzou_client.create_lanzou_client",
                side_effect=[RuntimeError("still logging in"), object()],
            ),
            mock.patch("pyapp.lanzou_client.time.monotonic", side_effect=lambda: next(monotonic_values)),
            mock.patch("pyapp.lanzou_client.time.sleep", return_value=None),
        ):
            result = wait_for_browser_login_cookie("chrome", timeout_seconds=5.0, poll_interval_seconds=0.1)

        self.assertEqual(result, (candidate.cookie, candidate.source, True))

    def test_wait_for_browser_login_cookie_reports_validation_failure_after_timeout(self) -> None:
        candidate = BrowserCookieCandidate(
            cookie={"ylogin": "1", "phpdisk_info": "abc"},
            source="Chrome / pc.woozooo.com",
        )
        monotonic_values = iter([0.0, 0.3, 1.1])

        with (
            mock.patch("pyapp.lanzou_client.build_browser_cookie_candidates", return_value=[candidate]),
            mock.patch("pyapp.lanzou_client.create_lanzou_client", side_effect=RuntimeError("bad cookie")),
            mock.patch("pyapp.lanzou_client.time.monotonic", side_effect=lambda: next(monotonic_values)),
            mock.patch("pyapp.lanzou_client.time.sleep", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "都没有通过登录校验"):
                wait_for_browser_login_cookie("chrome", timeout_seconds=1.0, poll_interval_seconds=0.1)

    def test_open_lanzou_login_in_browser_falls_back_to_default_browser(self) -> None:
        with (
            mock.patch("pyapp.lanzou_client.find_browser_executable", return_value=None),
            mock.patch("pyapp.lanzou_client.webbrowser.open", return_value=True),
        ):
            browser_label, watch_browser_name = open_lanzou_login_in_browser("chrome")

        self.assertIn("已改用默认浏览器", browser_label)
        self.assertEqual(watch_browser_name, "auto")


if __name__ == "__main__":
    unittest.main()
