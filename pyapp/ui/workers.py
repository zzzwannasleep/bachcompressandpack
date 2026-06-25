"""后台线程 worker 与事件桥。

设计要点：core / lanzou_client 里的耗时函数沿用 ``event_queue.put((kind, payload))``
事件接口。这里用 :class:`EventBridge` 适配——它的 ``put`` 直接 emit 一个 Qt 信号；
信号跨线程发射时由 Qt 自动以队列连接投递到主线程，因此 UI 更新天然线程安全，
业务逻辑无需为界面框架做任何改动。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..core import ArchiveToolSpec, FileEntry, pack_files
from ..lanzou_client import upload_archives, wait_for_browser_login_cookie


class EventBridge(QObject):
    """把 ``put((kind, payload))`` 转成 Qt 信号。"""

    event = pyqtSignal(str, object)

    def put(self, item: tuple[str, object]) -> None:
        kind, payload = item
        self.event.emit(kind, payload)


class PackWorker(QThread):
    def __init__(
        self,
        files: list[FileEntry],
        output_dir: Path,
        prefix: str,
        tool_spec: ArchiveToolSpec,
        bridge: EventBridge,
        *,
        auto_upload: bool = False,
        cookie: dict[str, str] | None = None,
        folder_id_text: str = "",
        ua: str = "",
    ) -> None:
        super().__init__()
        self._files = files
        self._output_dir = output_dir
        self._prefix = prefix
        self._tool_spec = tool_spec
        self._bridge = bridge
        self._auto_upload = auto_upload
        self._cookie = dict(cookie or {})
        self._folder_id_text = folder_id_text
        self._ua = ua

    def run(self) -> None:  # noqa: D401 - QThread entry
        bridge = self._bridge
        try:
            archives = pack_files(self._files, self._output_dir, self._prefix, bridge, self._tool_spec)
            bridge.put(("pack_archives_ready", archives))
            bridge.put(
                ("pack_log", f"打包完成，共生成 {len(archives)} 个压缩包，输出目录 {self._output_dir}。")
            )
            bridge.put(("upload_log", f"已把本次生成的 {len(archives)} 个压缩包加入待上传列表。"))
            if self._auto_upload:
                bridge.put(("upload_log", "开始自动上传本次生成的压缩包。"))
                upload_archives(archives, self._cookie, self._folder_id_text, bridge, self._ua)
                bridge.put(("upload_log", "蓝奏云自动上传完成。"))
                bridge.put(("upload_finished", True))
        except Exception as exc:  # noqa: BLE001
            bridge.put(("pack_log", f"失败: {exc}"))
            if self._auto_upload:
                bridge.put(("upload_log", f"自动上传失败: {exc}"))
        finally:
            bridge.put(("pack_done", None))


class UploadWorker(QThread):
    def __init__(
        self,
        archives: list[Path],
        cookie: dict[str, str],
        folder_id_text: str,
        bridge: EventBridge,
        ua: str = "",
    ) -> None:
        super().__init__()
        self._archives = archives
        self._cookie = dict(cookie or {})
        self._folder_id_text = folder_id_text
        self._bridge = bridge
        self._ua = ua

    def run(self) -> None:  # noqa: D401
        bridge = self._bridge
        try:
            upload_archives(self._archives, self._cookie, self._folder_id_text, bridge, self._ua)
            bridge.put(("upload_log", "蓝奏云上传完成。"))
            bridge.put(("upload_finished", True))
        except Exception as exc:  # noqa: BLE001
            bridge.put(("upload_log", f"失败: {exc}"))
        finally:
            bridge.put(("upload_done", None))


class BrowserLoginWatcher(QThread):
    """轮询等待用户在外部浏览器完成蓝奏云登录。"""

    succeeded = pyqtSignal(object)  # (cookie, source, verified)
    failed = pyqtSignal(str)

    def __init__(self, browser_name: str) -> None:
        super().__init__()
        self._browser_name = browser_name
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D401
        try:
            result = wait_for_browser_login_cookie(
                self._browser_name,
                should_stop=lambda: self._stop,
            )
        except Exception as exc:  # noqa: BLE001
            if not self._stop:
                self.failed.emit(str(exc))
            return
        if result is not None and not self._stop:
            self.succeeded.emit(result)


class WebLoginWorker(QThread):
    """以子进程启动内置 WebView2 登录窗口，登录成功后取回 Cookie。

    pywebview 必须独占主线程，故用子进程隔离；本线程只负责等待并读取结果。
    """

    succeeded = pyqtSignal(object)  # cookie dict
    failed = pyqtSignal(str)

    def __init__(self, timeout_seconds: int = 360) -> None:
        super().__init__()
        self._timeout = timeout_seconds

    def _build_command(self, result_path: str) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--weblogin", result_path]
        return [sys.executable, "-m", "pyapp", "--weblogin", result_path]

    def run(self) -> None:  # noqa: D401
        result_path = ""
        try:
            fd, result_path = tempfile.mkstemp(suffix=".weblogin.json")
            os.close(fd)
            Path(result_path).unlink(missing_ok=True)

            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            proc = subprocess.run(
                self._build_command(result_path),
                creationflags=creationflags,
                timeout=self._timeout,
                capture_output=True,
            )

            result_file = Path(result_path)
            if result_file.exists():
                payload = json.loads(result_file.read_text(encoding="utf-8"))
                if payload and payload.get("cookie"):
                    self.succeeded.emit(payload)
                    return

            err_file = Path(result_path + ".err")
            if err_file.exists():
                detail = err_file.read_text(encoding="utf-8", errors="ignore").strip()
                tail = detail.splitlines()[-1] if detail else ""
                self.failed.emit(f"内置登录窗口启动失败：{tail or '未知错误'}")
                return

            if proc.returncode == 0:
                self.failed.emit("已关闭登录窗口，但未抓到完整 Cookie。请确认已登录成功。")
            else:
                self.failed.emit("登录窗口已关闭（未完成登录）。")
        except subprocess.TimeoutExpired:
            self.failed.emit("内置登录超时（未在限定时间内完成登录）。")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"内置登录出错：{exc}")
        finally:
            for path in (result_path, result_path + ".err" if result_path else ""):
                if path:
                    Path(path).unlink(missing_ok=True)


class CallableWorker(QThread):
    """通用：在后台跑一个返回值的函数，成功/失败各发一个信号。"""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, func: Callable[[], object]) -> None:
        super().__init__()
        self._func = func

    def run(self) -> None:  # noqa: D401
        try:
            result = self._func()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(result)
