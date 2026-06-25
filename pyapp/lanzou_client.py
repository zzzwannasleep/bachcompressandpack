"""蓝奏云登录、Cookie 获取与上传逻辑（与界面框架无关）。

Cookie 获取支持三种方式：
1. 直接从用户已登录蓝奏云的本地浏览器读取（browser_cookie3，覆盖主流浏览器）
2. 打开外部浏览器登录后轮询自动抓取（wait_for_browser_login_cookie）
3. 手动粘贴 / 剪贴板
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

LanZouCloud = None
LANZOU_IMPORT_ERROR: Exception | None = None
browser_cookie3 = None
BROWSER_COOKIE_IMPORT_ERROR: Exception | None = None

LANZOU_LOGIN_URL = "https://pc.woozooo.com/account.php"
LANZOU_COOKIE_DOMAINS = (
    "pc.woozooo.com",
    "up.woozooo.com",
    "www.ilanzou.com",
    "ilanzou.com",
    "woozooo.com",
    "pan.lanzouo.com",
    "lanzouo.com",
    "lanzouw.com",
    "lanzoui.com",
    "lanzoux.com",
)
LANZOU_REQUIRED_COOKIE_KEYS = ("ylogin", "phpdisk_info")
LANZOU_ACCOUNT_URL = "https://pc.woozooo.com/account.php"
LANZOU_DOUPLOAD_URL = "https://pc.woozooo.com/doupload.php"
# 用接近真实浏览器的现代 UA（lanzou-api 自带的是 2019 年的 Chrome 75）
LANZOU_MODERN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 显示名称 -> browser_cookie3 函数名。auto 表示遍历全部桌面浏览器。
BROWSER_IMPORTERS: dict[str, str | None] = {
    "auto": None,
    "edge": "Edge",
    "chrome": "Chrome",
    "firefox": "Firefox",
    "brave": "Brave",
    "opera": "Opera",
    "opera_gx": "Opera GX",
    "vivaldi": "Vivaldi",
    "chromium": "Chromium",
    "arc": "Arc",
    "librewolf": "LibreWolf",
}
# auto 模式下尝试的顺序（按国内常见度排序）
AUTO_BROWSER_ORDER = (
    "edge",
    "chrome",
    "firefox",
    "brave",
    "vivaldi",
    "opera",
    "opera_gx",
    "chromium",
    "arc",
    "librewolf",
)

BROWSER_LOGIN_POLL_INTERVAL_SECONDS = 2.0
BROWSER_LOGIN_AUTO_TIMEOUT_SECONDS = 90.0
WINDOWS_BROWSER_EXECUTABLE_NAMES = {
    "edge": ("msedge.exe",),
    "chrome": ("chrome.exe",),
    "firefox": ("firefox.exe",),
    "brave": ("brave.exe",),
    "opera": ("opera.exe", "launcher.exe"),
    "opera_gx": ("opera.exe", "launcher.exe"),
    "vivaldi": ("vivaldi.exe",),
    "chromium": ("chromium.exe", "chrome.exe"),
}
WINDOWS_BROWSER_EXECUTABLE_RELATIVE_PATHS = {
    "edge": ("Microsoft/Edge/Application/msedge.exe",),
    "chrome": ("Google/Chrome/Application/chrome.exe",),
    "firefox": ("Mozilla Firefox/firefox.exe",),
    "brave": ("BraveSoftware/Brave-Browser/Application/brave.exe",),
    "vivaldi": ("Vivaldi/Application/vivaldi.exe",),
}


@dataclass(slots=True)
class ShareLink:
    file_name: str
    url: str
    password: str


@dataclass(slots=True)
class BrowserCookieCandidate:
    cookie: dict[str, str]
    source: str


def normalize_share_url(url: str) -> str:
    value = url.strip()
    if value.startswith(("http://", "https://")):
        return value
    return f"https://{value.lstrip('/')}"


def parse_cookie_string(cookie_text: str) -> dict[str, str]:
    raw = cookie_text.strip()
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1].strip()

    cookie: dict[str, str] = {}
    for chunk in raw.split(";"):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            cookie[key] = value

    missing = [key for key in LANZOU_REQUIRED_COOKIE_KEYS if key not in cookie]
    if missing:
        raise ValueError(f"Cookie 缺少必要字段: {', '.join(missing)}")
    return cookie


def has_required_lanzou_cookie(cookie: dict[str, str]) -> bool:
    return all(cookie.get(key) for key in LANZOU_REQUIRED_COOKIE_KEYS)


def browser_cookie_candidate_signature(cookie: dict[str, str]) -> tuple[tuple[str, str], ...]:
    interesting_keys = sorted(
        key
        for key in cookie
        if key in LANZOU_REQUIRED_COOKIE_KEYS or key in {"PHPSESSID", "phpdisk_z", "acw_tc"}
    )
    return tuple((key, cookie.get(key, "")) for key in interesting_keys)


def parse_folder_id(value: str) -> int:
    text = value.strip()
    if not text:
        return -1
    return int(text)


def format_share_line(link: ShareLink) -> str:
    if link.password:
        return f"{link.file_name} | {link.url} | 提取码: {link.password}"
    return f"{link.file_name} | {link.url}"


# ---------------------------------------------------------------------------
# 延迟导入
# ---------------------------------------------------------------------------
def get_lanzou_cloud_class() -> object:
    global LanZouCloud, LANZOU_IMPORT_ERROR
    if LanZouCloud is not None:
        return LanZouCloud
    try:
        from lanzou.api import LanZouCloud as imported_class
    except Exception as exc:  # pragma: no cover - import failure surfaced in UI
        LANZOU_IMPORT_ERROR = exc
        raise RuntimeError(f"蓝奏云依赖未就绪: {exc}") from exc
    LanZouCloud = imported_class
    LANZOU_IMPORT_ERROR = None
    return imported_class


def get_browser_cookie_module() -> object:
    global browser_cookie3, BROWSER_COOKIE_IMPORT_ERROR
    if browser_cookie3 is not None:
        return browser_cookie3
    try:
        import browser_cookie3 as imported_module
    except Exception as exc:  # pragma: no cover - import failure surfaced in UI
        BROWSER_COOKIE_IMPORT_ERROR = exc
        raise RuntimeError(f"浏览器登录导入依赖不可用: {exc}") from exc
    browser_cookie3 = imported_module
    BROWSER_COOKIE_IMPORT_ERROR = None
    return imported_module


def available_browser_choices() -> list[str]:
    """UI 下拉框使用的浏览器选项。"""
    return ["auto", *AUTO_BROWSER_ORDER]


# ---------------------------------------------------------------------------
# 登录与错误描述
# ---------------------------------------------------------------------------
def describe_lanzou_login_error(login_code: int) -> str:
    cloud_class = get_lanzou_cloud_class()
    if login_code == cloud_class.FAILED:
        return (
            "Cookie 未通过蓝奏云校验。常见原因是浏览器里登录态已过期、导入到了错误域名，"
            "或者当前浏览器并没有登录蓝奏云后台。"
        )
    if login_code == cloud_class.NETWORK_ERROR:
        return "程序没能访问蓝奏云，请检查网络、代理、防火墙，或蓝奏云当前是否可访问。"
    if login_code == cloud_class.CAPTCHA_ERROR:
        return "蓝奏云返回了验证码校验异常，请在浏览器里重新登录后再导入。"
    return "蓝奏云拒绝了当前登录态，请重新登录后再试。"


def _raw_cookie_header(cookie: dict[str, str]) -> str:
    return "; ".join(f"{key}={value}" for key, value in cookie.items() if value)


def _silence_insecure_warnings() -> None:
    try:
        from urllib3 import disable_warnings
        from urllib3.exceptions import InsecureRequestWarning

        disable_warnings(InsecureRequestWarning)
    except Exception:  # noqa: BLE001
        pass


def _api_probe(cookie: dict[str, str], ua: str) -> dict[str, object]:
    """用 doupload.php API（task=5 列根目录）判断登录态。

    蓝奏云已把登录迁到 accounts.woozooo.com，老的 account.php HTML 判断恒为“未登录”，
    所以必须用 API 的 JSON 结果：zt==1 表示已登录，zt==9/info=login not 表示未登录。
    """
    import requests

    _silence_insecure_warnings()
    headers = {
        "User-Agent": ua or LANZOU_MODERN_UA,
        "Referer": "https://pc.woozooo.com/mydisk.php",
        "Cookie": _raw_cookie_header(cookie),
    }
    try:
        r = requests.post(
            LANZOU_DOUPLOAD_URL,
            data={"task": 5, "folder_id": -1, "pg": 1},
            headers=headers,
            verify=False,
            timeout=15,
        )
        try:
            body = r.json()
        except Exception:  # noqa: BLE001
            body = {}
        zt = body.get("zt")
        return {
            "logged_in": zt == 1,
            "zt": zt,
            "info": body.get("info"),
            "status": r.status_code,
        }
    except Exception as exc:  # noqa: BLE001
        return {"logged_in": False, "error": str(exc)}


def account_selftest(cookie: dict[str, str], ua: str) -> dict[str, object]:
    """登录助手用：抓到 Cookie 后立即自检一次，结果随 Cookie 一起回传。"""
    result = _api_probe(cookie, ua)
    result["ua"] = ua
    return result


def diagnose_cookie(cookie: dict[str, str], ua: str = "") -> str:
    """登录失败时，直接用 API 探一探，把确切原因写进日志。"""
    lines = ["—— Cookie 诊断 ——", f"共捕获 {len(cookie)} 个: {', '.join(sorted(cookie))}"]
    for key in LANZOU_REQUIRED_COOKIE_KEYS:
        value = cookie.get(key, "")
        lines.append(f"  {key}: 长度={len(value)}" + ("（空！）" if not value else ""))
    lines.append(f"登录态 UA: {ua or '(无，用默认)'}")
    for label, trial_ua in (("真实UA", ua or LANZOU_MODERN_UA), ("现代UA", LANZOU_MODERN_UA)):
        probe = _api_probe(cookie, trial_ua)
        if "error" in probe:
            lines.append(f"{label} API 异常: {probe['error']}")
        else:
            lines.append(
                f"{label} doupload: zt={probe.get('zt')} info={probe.get('info')!r} "
                f"已登录={probe.get('logged_in')}"
            )
    return "\n".join(lines)


def create_lanzou_client(cookie: dict[str, str], ua: str = "") -> object:
    """构造已登录的蓝奏云客户端。

    用 doupload.php API 校验登录（account.php 的 HTML 判断已被蓝奏云登录迁移搞失效）。
    所有请求统一用现代/真实 UA + 原始 Cookie 头。
    """
    cloud_class = get_lanzou_cloud_class()
    missing = [key for key in LANZOU_REQUIRED_COOKIE_KEYS if not cookie.get(key)]
    if missing:
        raise RuntimeError(f"蓝奏云 Cookie 缺少必要字段: {', '.join(missing)}")

    effective_ua = ua or LANZOU_MODERN_UA

    api = cloud_class()
    api._uid = cookie["ylogin"]
    api._headers = dict(api._headers)
    api._headers["User-Agent"] = effective_ua
    api._headers["Cookie"] = _raw_cookie_header(cookie)
    api._session.cookies.update(cookie)

    probe = _api_probe(cookie, effective_ua)
    if probe.get("logged_in"):
        return api

    if "error" in probe:
        raise RuntimeError(f"蓝奏云登录校验失败：{probe['error']}\n{diagnose_cookie(cookie, ua)}")
    raise RuntimeError(
        f"蓝奏云未登录（API zt={probe.get('zt')}, info={probe.get('info')}）。"
        f"请重新登录后再试。\n{diagnose_cookie(cookie, ua)}"
    )


def format_browser_cookie_validation_error(errors: list[str]) -> str:
    detail = "\n".join(f"- {item}" for item in errors[:5])
    return (
        "已从浏览器读取到蓝奏云 Cookie，但都没有通过登录校验。"
        "请先在浏览器里重新登录蓝奏云后台后再导入，或者改用内置网页登录 / 手动粘贴最新 Cookie。\n"
        f"{detail}"
    )


# ---------------------------------------------------------------------------
# 浏览器可执行文件定位 + 打开登录页
# ---------------------------------------------------------------------------
def find_browser_executable(browser_name: str) -> Path | None:
    executable_names = WINDOWS_BROWSER_EXECUTABLE_NAMES.get(browser_name, ())
    for executable_name in executable_names:
        executable = shutil.which(executable_name)
        if executable:
            return Path(executable)

    if os.name != "nt":
        return None

    relative_paths = WINDOWS_BROWSER_EXECUTABLE_RELATIVE_PATHS.get(browser_name, ())
    install_roots = [
        Path(path_text)
        for path_text in (
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        )
        if path_text
    ]
    for install_root in install_roots:
        for relative_path in relative_paths:
            candidate = install_root / relative_path
            if candidate.exists():
                return candidate
    return None


def open_lanzou_login_in_browser(browser_name: str) -> tuple[str, str]:
    browser_label = BROWSER_IMPORTERS.get(browser_name) or "默认浏览器"
    executable = find_browser_executable(browser_name) if browser_name != "auto" else None

    if executable is not None:
        try:
            subprocess.Popen([str(executable), LANZOU_LOGIN_URL])  # noqa: S603
            return browser_label, browser_name
        except Exception:  # noqa: BLE001
            pass

    if webbrowser.open(LANZOU_LOGIN_URL):
        if browser_name == "auto":
            return browser_label, "auto"
        return f"{browser_label}（未定位到程序，已改用默认浏览器）", "auto"

    raise RuntimeError(f"没能自动打开蓝奏云登录页，请手动访问: {LANZOU_LOGIN_URL}")


# ---------------------------------------------------------------------------
# 从本地浏览器直接读取已登录 Cookie
# ---------------------------------------------------------------------------
def _resolve_browser_importers(browser_name: str) -> list[tuple[str, Callable[..., object]]]:
    module = get_browser_cookie_module()
    keys = list(AUTO_BROWSER_ORDER) if browser_name == "auto" else [browser_name]
    importers: list[tuple[str, Callable[..., object]]] = []
    for key in keys:
        func = getattr(module, key, None)
        if callable(func):
            importers.append((key, func))
    return importers


def build_browser_cookie_candidates(browser_name: str) -> list[BrowserCookieCandidate]:
    importers = _resolve_browser_importers(browser_name)

    candidates: list[BrowserCookieCandidate] = []
    seen_signatures: set[tuple[tuple[str, str], ...]] = set()

    for browser_key, importer in importers:
        browser_label = BROWSER_IMPORTERS.get(browser_key) or browser_key
        merged_cookie: dict[str, str] = {}
        merged_domains: list[str] = []

        for domain in LANZOU_COOKIE_DOMAINS:
            try:
                jar = importer(domain_name=domain)
            except Exception:  # noqa: BLE001 - 浏览器未安装/被占用/解密失败都跳过
                continue

            cookie = {item.name: item.value for item in jar if item.value}
            if not cookie:
                continue

            merged_cookie.update(cookie)
            merged_domains.append(domain)

            if not has_required_lanzou_cookie(cookie):
                continue

            signature = browser_cookie_candidate_signature(cookie)
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            candidates.append(
                BrowserCookieCandidate(cookie=dict(cookie), source=f"{browser_label} / {domain}")
            )

        if has_required_lanzou_cookie(merged_cookie):
            signature = browser_cookie_candidate_signature(merged_cookie)
            if signature not in seen_signatures:
                seen_signatures.add(signature)
                source = f"{browser_label} / 合并导入"
                if merged_domains:
                    source += f"（{', '.join(merged_domains)}）"
                candidates.append(BrowserCookieCandidate(cookie=dict(merged_cookie), source=source))

    return candidates


def probe_browser_import_error(browser_name: str) -> str | None:
    """没读到 Cookie 时，探一下根因，给出可操作的提示。"""
    importers = _resolve_browser_importers(browser_name)
    locked = False
    other: list[str] = []
    for browser_key, importer in importers:
        try:
            importer(domain_name="woozooo.com")
        except Exception as exc:  # noqa: BLE001
            text = f"{type(exc).__name__}: {exc}"
            low = text.lower()
            # 库被浏览器独占锁住 / 需要管理员（卷影复制）
            if (
                "requiresadmin" in low
                or "winerror 32" in low
                or "being used by another process" in low
                or "正在使用此文件" in text
                or "admin" in low
            ):
                locked = True
            elif "failed to find cookies" in low or "could not find" in low:
                continue  # 该浏览器没装，忽略
            else:
                other.append(text)
    if locked:
        return (
            "读取被浏览器占用/加密阻挡了。新版 Edge / Chrome 运行时会独占锁住 Cookie 库，"
            "普通权限读不到。请任选其一再试：\n"
            "① 完全退出 Edge / Chrome（含后台 msedge.exe）后再点“从浏览器导入登录”；\n"
            "② 右键“以管理员身份运行”本程序；\n"
            "③ 直接用“粘贴 Cookie”：浏览器按 F12 → 应用/Application → Cookie → pc.woozooo.com，"
            "复制 ylogin 和 phpdisk_info。"
        )
    if other:
        return "读取浏览器 Cookie 时出错：\n- " + "\n- ".join(other[:4])
    return None


def load_browser_login_cookie(browser_name: str) -> tuple[dict[str, str], str, bool]:
    candidates = build_browser_cookie_candidates(browser_name)
    if not candidates:
        hint = probe_browser_import_error(browser_name)
        if hint:
            raise RuntimeError(hint)
        raise RuntimeError(
            "未从浏览器中找到可用的蓝奏云登录 Cookie。请先在该浏览器登录蓝奏云后台，"
            "或改用“打开网页登录并自动抓取” / “粘贴 Cookie”。"
        )

    errors: list[str] = []
    for candidate in candidates:
        try:
            create_lanzou_client(candidate.cookie)
            return candidate.cookie, candidate.source, True
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{candidate.source}: {exc}")

    raise RuntimeError(format_browser_cookie_validation_error(errors))


def wait_for_browser_login_cookie(
    browser_name: str,
    timeout_seconds: float = BROWSER_LOGIN_AUTO_TIMEOUT_SECONDS,
    poll_interval_seconds: float = BROWSER_LOGIN_POLL_INTERVAL_SECONDS,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[dict[str, str], str, bool] | None:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    last_errors: list[str] = []

    while True:
        if should_stop is not None and should_stop():
            return None

        candidates = build_browser_cookie_candidates(browser_name)
        if candidates:
            current_errors: list[str] = []
            for candidate in candidates:
                try:
                    create_lanzou_client(candidate.cookie)
                    return candidate.cookie, candidate.source, True
                except Exception as exc:  # noqa: BLE001
                    current_errors.append(f"{candidate.source}: {exc}")
            if current_errors:
                last_errors = current_errors

        if time.monotonic() >= deadline:
            break

        time.sleep(max(0.1, poll_interval_seconds))

    if last_errors:
        raise RuntimeError(format_browser_cookie_validation_error(last_errors))
    raise RuntimeError(
        "等待网页登录超时，仍未发现可用的蓝奏云 Cookie。"
        "请确认刚才登录的是当前选择的浏览器，然后点“从浏览器导入登录”手动重试。"
    )


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------
class _EventSink:  # 仅用于类型提示文档：任何带 put((kind, payload)) 的对象都可
    def put(self, item: tuple[str, object]) -> None:  # pragma: no cover
        ...


# 上传端点：蓝奏云已把 fileup.php(404) 换成 html5up.php
LANZOU_UPLOAD_URL = "https://pc.woozooo.com/html5up.php"


def _build_session_headers(cookie: dict[str, str], ua: str) -> dict[str, str]:
    return {
        "User-Agent": ua or LANZOU_MODERN_UA,
        "Referer": "https://pc.woozooo.com/mydisk.php",
        "Cookie": _raw_cookie_header(cookie),
    }


def _upload_one(archive: Path, folder_id: int, headers: dict[str, str]) -> str:
    """上传单个文件到 html5up.php，返回文件 id。"""
    import requests
    from requests_toolbelt import MultipartEncoder

    _silence_insecure_warnings()
    with open(archive, "rb") as fh:
        fields = {
            "task": "1",
            "vie": "2",
            "ve": "2",
            "id": "WU_FILE_0",
            "folder_id_bb_n": str(folder_id),
            "name": archive.name,
            "upload_file": (archive.name, fh, "application/octet-stream"),
        }
        encoder = MultipartEncoder(fields)
        post_headers = {**headers, "Content-Type": encoder.content_type}
        resp = requests.post(
            LANZOU_UPLOAD_URL, data=encoder, headers=post_headers, verify=False, timeout=3600
        )
    if resp.status_code != 200:
        raise RuntimeError(f"{archive.name} 上传失败，HTTP {resp.status_code}")
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"{archive.name} 上传返回异常：{resp.text[:120]}") from exc
    if body.get("zt") != 1:
        raise RuntimeError(f"{archive.name} 上传失败：{body.get('info')}")
    text = body.get("text") or []
    if not text or "id" not in text[0]:
        raise RuntimeError(f"{archive.name} 上传完成，但未返回文件 id。")
    return str(text[0]["id"])


def _get_share(file_id: str, headers: dict[str, str]) -> tuple[str, str]:
    """取分享链接，返回 (url, 提取码)。"""
    import requests

    _silence_insecure_warnings()
    resp = requests.post(
        LANZOU_DOUPLOAD_URL,
        data={"task": 22, "file_id": file_id},
        headers=headers,
        verify=False,
        timeout=20,
    )
    body = resp.json()
    if body.get("zt") != 1:
        raise RuntimeError(f"获取分享链接失败：{body.get('info')}")
    info = body.get("info") or {}
    domain = (info.get("is_newd") or "").rstrip("/")
    f_id = info.get("f_id") or ""
    url = normalize_share_url(f"{domain}/{f_id}") if domain and f_id else ""
    # onof==1 表示开启了提取码
    password = info.get("pwd", "") if str(info.get("onof")) == "1" else ""
    return url, password


def upload_archives(
    archives: list[Path],
    cookie: dict[str, str],
    folder_id_text: str,
    event_queue: "_EventSink",
    ua: str = "",
) -> list[ShareLink]:
    folder_id = parse_folder_id(folder_id_text)
    # 先校验登录（doupload API），未登录会抛出清晰错误
    create_lanzou_client(cookie, ua)
    headers = _build_session_headers(cookie, ua)

    share_links: list[ShareLink] = []
    total = len(archives)
    for index, archive in enumerate(archives, start=1):
        event_queue.put(("upload_progress", (index - 1, total)))
        event_queue.put(("upload_log", f"正在上传 {archive.name} 到蓝奏云...（{index}/{total}）"))

        file_id = _upload_one(archive, folder_id, headers)
        url, password = _get_share(file_id, headers)
        if not url:
            raise RuntimeError(f"{archive.name} 已上传，但没拿到分享链接。")

        link = ShareLink(file_name=archive.name, url=url, password=password)
        share_links.append(link)
        event_queue.put(("upload_link", link))
        event_queue.put(("upload_progress", (index, total)))
        event_queue.put(("upload_log", f"上传完成: {format_share_line(link)}"))

    return share_links
