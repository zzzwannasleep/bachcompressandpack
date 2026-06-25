"""内置网页登录助手（独立子进程运行）。

用系统自带的 WebView2（pywebview 的 edgechromium 后端）打开蓝奏云登录页，
登录成功后通过 WebView2 CookieManager 直接读取 Cookie（含 HttpOnly），
写入结果文件后自动关窗。

之所以用子进程：pywebview 必须独占主线程跑自己的消息循环，会和主程序的
Qt 事件循环冲突，单独进程最干净。主程序通过 ``app.main`` 里的 ``--weblogin``
分支启动本助手，并读取结果文件。
"""

from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path


def _normalize_cookies(jar) -> dict[str, str]:
    cookie: dict[str, str] = {}
    for item in jar or []:
        name = getattr(item, "name", None)
        value = getattr(item, "value", None)
        if name and value:
            cookie[name] = value
            continue
        # 兜底：SimpleCookie / Morsel 形态
        try:
            for key, morsel in item.items():  # type: ignore[attr-defined]
                if key and morsel.value:
                    cookie[key] = morsel.value
        except Exception:  # noqa: BLE001
            continue
    return cookie


def run_weblogin(result_path: str, timeout_seconds: float = 300.0) -> int:
    """打开登录窗口，抓到 Cookie 写入 result_path。成功返回 0。"""
    err_path = result_path + ".err"
    # 允许用环境变量覆盖超时（便于自检）
    try:
        timeout_seconds = float(os.environ.get("WEBLOGIN_TIMEOUT", timeout_seconds))
    except ValueError:
        pass
    try:
        import webview

        from .lanzou_client import LANZOU_LOGIN_URL, LANZOU_REQUIRED_COOKIE_KEYS

        state = {"ok": False}
        window = webview.create_window(
            "登录蓝奏云（登录成功后窗口会自动关闭）",
            LANZOU_LOGIN_URL,
            width=560,
            height=760,
        )

        def worker() -> None:
            deadline = time.monotonic() + timeout_seconds
            try:
                while time.monotonic() < deadline and not state["ok"]:
                    time.sleep(1.2)
                    try:
                        jar = window.get_cookies()
                    except Exception:  # noqa: BLE001 - 页面未就绪时忽略
                        continue
                    cookie = _normalize_cookies(jar)
                    if all(cookie.get(key) for key in LANZOU_REQUIRED_COOKIE_KEYS):
                        # 抓取登录时浏览器真实使用的 UA（蓝奏云会把会话绑定到 UA）
                        ua = ""
                        try:
                            ua = window.evaluate_js("navigator.userAgent") or ""
                        except Exception:  # noqa: BLE001
                            pass
                        # 用真实 UA 立刻自检一次，结果一并回传，便于定位
                        try:
                            from .lanzou_client import account_selftest

                            selftest = account_selftest(cookie, ua)
                        except Exception as exc:  # noqa: BLE001
                            selftest = {"error": str(exc)}
                        payload = {"cookie": cookie, "ua": ua, "selftest": selftest}
                        Path(result_path).write_text(
                            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                        )
                        state["ok"] = True
                        return
            finally:
                # 不论成功或超时，都关掉窗口让进程退出
                try:
                    window.destroy()
                except Exception:  # noqa: BLE001
                    pass

        webview.start(worker)
        return 0 if state["ok"] else 1
    except Exception:  # noqa: BLE001
        try:
            Path(err_path).write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return 2
