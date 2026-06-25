from datetime import datetime
from pathlib import Path
import site
import sys
import traceback


ROOT = Path(__file__).resolve().parent
LOCAL_DEPS = ROOT / ".python_deps"
if LOCAL_DEPS.exists():
    site.addsitedir(str(LOCAL_DEPS))
    sys.path.insert(0, str(LOCAL_DEPS))


def get_runtime_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return ROOT


def write_startup_log(exc: BaseException) -> Path | None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    content = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    body = (
        f"timestamp: {datetime.now().isoformat()}\n"
        f"python: {sys.version}\n"
        f"executable: {sys.executable}\n"
        f"cwd: {Path.cwd()}\n\n"
        f"{content}"
    )

    for base_dir in (get_runtime_dir(), ROOT):
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
            log_path = base_dir / f"batch-packager-startup-error-{stamp}.log"
            log_path.write_text(body, encoding="utf-8")
            return log_path
        except OSError:
            continue
    return None


def show_startup_error(message: str) -> None:
    # 用系统原生弹窗，避免把 tkinter / tcl-tk 打进发布包
    if sys.platform == "win32":
        try:
            from ctypes import windll

            windll.user32.MessageBoxW(0, message, "自动打包启动失败", 0x10)
        except Exception:
            pass

    try:
        print(message, file=sys.stderr)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        from pyapp.app import main

        main()
    except Exception as exc:  # noqa: BLE001
        log_path = write_startup_log(exc)
        message = "程序启动失败。"
        if log_path is not None:
            message += f"\n错误日志已写入:\n{log_path}"
        show_startup_error(message)
        raise SystemExit(1)
