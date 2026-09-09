"""
Account Manager for JAutoFb.

Key session rule:
- AccountManager NEVER opens a second persistent browser for an existing profile.
- detect_fb_name() and get_account_cookies_string() go through BrowserSessionManager.
- open_login_session() also goes through the centralized session owner.
- Lock files are NOT blindly deleted and Chrome is NOT force-killed during normal startup.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

ACCOUNTS_FILE = Path("data/accounts.json")
PROFILES_DIR = Path("profiles")


def find_chrome() -> str:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return "chrome.exe"


def clean_profile_locks(profile_dir: Path) -> bool:
    """Deprecated compatibility function.

    DO NOT delete Singleton/lock files automatically. Chrome owns them while the
    profile is in use, and deleting them can make a healthy profile unsafe.
    """
    return False


def kill_chrome_for_profile(profile_dir: Path, force: bool = False) -> int:
    """Compatibility helper; normal application flow never calls this with force=True."""
    if not force:
        return 0

    abs_profile = str(Path(profile_dir).resolve()).lower()
    killed = 0
    try:
        ps_cmd = (
            'Get-CimInstance Win32_Process -Filter "name=\'chrome.exe\'" '
            '| Select-Object ProcessId, CommandLine | ConvertTo-Json'
        )
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        if not out.strip():
            return 0
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        for proc in data:
            cmd = (proc.get("CommandLine") or "").lower()
            if abs_profile in cmd:
                pid = proc.get("ProcessId")
                if pid:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
                    killed += 1
    except Exception:
        pass
    return killed


def _account_id_for_profile(profile_dir: Path) -> str:
    target = profile_dir.resolve()
    try:
        if ACCOUNTS_FILE.exists():
            accounts = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
            for account in accounts:
                try:
                    if Path(account.get("profile_dir", "")).resolve() == target:
                        return str(account["id"])
                except Exception:
                    continue
    except Exception:
        pass

    # Fallback for paths like profiles/acc_123.
    name = target.name.strip()
    if name:
        return name
    return f"profile_{abs(hash(str(target))) % 1000000}"


def detect_fb_name(profile_dir: str) -> Dict[str, Any]:
    """Read FB session state through the central browser session owner."""
    p = Path(profile_dir).resolve()
    if not p.exists():
        return {"logged_in": False, "fb_name": None, "fb_id": None}

    from browser_session_manager import browser_session_mgr, BrowserSessionError

    account_id = _account_id_for_profile(p)
    browser_or_context = None
    page = None
    is_cdp = False
    try:
        browser_or_context, page, is_cdp = browser_session_mgr.acquire_page(
            account_id=account_id,
            profile_dir=p,
            headless=False,
        )
        page.set_default_timeout(10000)

        # Use current browser cookies only; no second persistent context.
        cookies = page.context.cookies("https://www.facebook.com")
        c_user = next((c.get("value") for c in cookies if c.get("name") == "c_user"), None)
        if not c_user:
            return {"logged_in": False, "fb_name": None, "fb_id": None}

        page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=15000)
        try:
            page.wait_for_timeout(1500)
        except Exception:
            pass

        name = page.evaluate(
            """
            (uid) => {
                const links = Array.from(document.querySelectorAll(`a[href*="${uid}"]`));
                for (const a of links) {
                    const t = a.innerText ? a.innerText.trim() : '';
                    if (t && !t.includes('\\n') && t.length > 1) return t;
                }

                const profBtn = document.querySelector(
                    'div[aria-label="Trang cá nhân của bạn"], div[aria-label="Your profile"]'
                );
                if (profBtn && profBtn.innerText) return profBtn.innerText.trim();

                const navLinks = Array.from(document.querySelectorAll('div[role="navigation"] a'));
                for (const a of navLinks) {
                    const h = a.getAttribute('href') || '';
                    if (h.includes('profile.php') || h.includes('/me')) {
                        const t = a.innerText ? a.innerText.trim() : '';
                        if (t && !t.includes('\\n')) return t;
                    }
                }
                return null;
            }
            """,
            c_user,
        )
        return {
            "logged_in": True,
            "fb_name": name or "Tài khoản Facebook",
            "fb_id": c_user,
        }
    except BrowserSessionError as exc:
        return {"logged_in": False, "fb_name": None, "fb_id": None, "error": str(exc)}
    except Exception as exc:
        return {"logged_in": False, "fb_name": None, "fb_id": None, "error": str(exc)}
    finally:
        if browser_or_context is not None and page is not None:
            try:
                browser_session_mgr.release_page(
                    account_id=account_id,
                    browser_or_context=browser_or_context,
                    page=page,
                    is_cdp=is_cdp,
                )
            except Exception:
                pass


def get_account_cookies_string(profile_dir: str) -> str:
    """Get current Facebook cookies from the managed session.

    We intentionally do not cache authentication cookies to data/*.txt anymore.
    Stale copies of authentication cookies can outlive the browser session and
    create confusing state/race conditions in other engines.
    """
    p = Path(profile_dir).resolve()
    if not p.exists():
        return ""

    from browser_session_manager import browser_session_mgr

    account_id = _account_id_for_profile(p)
    browser_or_context = None
    page = None
    is_cdp = False
    try:
        browser_or_context, page, is_cdp = browser_session_mgr.acquire_page(
            account_id=account_id,
            profile_dir=p,
            headless=False,
        )
        cookies = page.context.cookies("https://www.facebook.com")
        return "; ".join(
            f"{c['name']}={c['value']}"
            for c in cookies
            if c.get("name") and c.get("value")
        )
    except Exception:
        return ""
    finally:
        if browser_or_context is not None and page is not None:
            try:
                browser_session_mgr.release_page(
                    account_id=account_id,
                    browser_or_context=browser_or_context,
                    page=page,
                    is_cdp=is_cdp,
                )
            except Exception:
                pass


def parse_proxy(raw_proxy: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw_proxy or not str(raw_proxy).strip():
        return None

    raw = str(raw_proxy).strip()
    proto = "http"
    for prefix in ("socks5://", "socks4://", "http://", "https://"):
        if raw.startswith(prefix):
            proto = prefix[:-3]
            raw = raw[len(prefix):]
            break

    parts = raw.split(":")
    if len(parts) == 4 and "@" not in raw:
        ip, port, user, pwd = parts
        return {
            "server": f"{proto}://{ip}:{port}",
            "username": user,
            "password": pwd,
            "raw": str(raw_proxy).strip(),
        }

    if "@" in raw:
        auth_part, host_part = raw.split("@", 1)
        user, pwd = auth_part.split(":", 1) if ":" in auth_part else (auth_part, "")
        return {
            "server": f"{proto}://{host_part}",
            "username": user,
            "password": pwd,
            "raw": str(raw_proxy).strip(),
        }

    return {
        "server": f"{proto}://{raw}",
        "username": None,
        "password": None,
        "raw": str(raw_proxy).strip(),
    }


def check_proxy_health(proxy_str: str) -> Dict[str, Any]:
    parsed = parse_proxy(proxy_str)
    if not parsed:
        return {"success": False, "error": "Định dạng Proxy không hợp lệ"}

    import requests

    server = parsed["server"]
    user = parsed.get("username")
    password = parsed.get("password")
    if user and password:
        proto, host = server.split("://", 1)
        full_proxy_url = f"{proto}://{user}:{password}@{host}"
    else:
        full_proxy_url = server

    proxies = {"http": full_proxy_url, "https": full_proxy_url}
    t0 = time.time()
    try:
        resp = requests.get(
            "http://ip-api.com/json/?fields=status,message,country,countryCode,query",
            proxies=proxies,
            timeout=4,
        )
        latency = int((time.time() - t0) * 1000)
        data = resp.json()
        if data.get("status") == "success":
            return {
                "success": True,
                "ip": data.get("query"),
                "country": data.get("country", "Unknown"),
                "country_code": data.get("countryCode", ""),
                "latency_ms": latency,
            }
        return {"success": False, "error": data.get("message", "Proxy check failed")}
    except Exception as exc:
        try:
            resp = requests.get("https://api.ipify.org?format=json", proxies=proxies, timeout=3)
            latency = int((time.time() - t0) * 1000)
            return {
                "success": True,
                "ip": resp.json().get("ip"),
                "country": "Unknown",
                "country_code": "",
                "latency_ms": latency,
            }
        except Exception as exc2:
            return {"success": False, "error": f"Lỗi kết nối Proxy: {str(exc2)[:120]}"}


_CACHED_CHROME_VERSION: Optional[str] = None


def get_chrome_version() -> str:
    global _CACHED_CHROME_VERSION
    if _CACHED_CHROME_VERSION:
        return _CACHED_CHROME_VERSION

    try:
        exe = find_chrome()
        if exe and Path(exe).exists():
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    f"(Get-Item '{exe}').VersionInfo.ProductVersion",
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )
            version = result.stdout.strip()
            if version and version[0].isdigit():
                _CACHED_CHROME_VERSION = version
                return version
    except Exception:
        pass

    _CACHED_CHROME_VERSION = "152.0.7977.77"
    return _CACHED_CHROME_VERSION


def get_natural_user_agent() -> str:
    v = get_chrome_version()
    major = v.split(".")[0] if "." in v else "152"
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    )


def get_standard_chrome_args(headless: bool = False) -> List[str]:
    """Stable browser flags retained for compatibility with the existing project."""
    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--lang=vi-VN,vi",
        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--force-webrtc-ip-handling-policy",
        "--disable-infobars",
        "--disable-notifications",
        "--disable-popup-blocking",
        "--no-first-run",
        "--no-default-browser-check",
        "--password-store=basic",
    ]
    if not headless:
        args.extend(["--start-maximized", "--window-position=50,50", "--window-size=1280,850"])
    return args


def get_playwright_launch_args(
    proxy_parsed: Optional[Dict[str, Any]] = None,
    headless: bool = False,
) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "args": get_standard_chrome_args(headless=headless),
        "channel": "chrome",
        "headless": headless,
        "ignore_default_args": ["--enable-automation"],
        "user_agent": get_natural_user_agent(),
        "locale": "vi-VN",
        "timezone_id": "Asia/Ho_Chi_Minh",
        "no_viewport": not headless,
        "viewport": None if not headless else {"width": 1280, "height": 850},
    }
    if proxy_parsed and proxy_parsed.get("server"):
        cfg["proxy"] = {"server": proxy_parsed["server"]}
        if proxy_parsed.get("username"):
            cfg["proxy"]["username"] = proxy_parsed["username"]
        if proxy_parsed.get("password"):
            cfg["proxy"]["password"] = proxy_parsed["password"]
    return cfg


class AccountManager:
    def __init__(self) -> None:
        self.accounts: List[Dict[str, Any]] = []
        self.load_accounts()

    def load_accounts(self) -> None:
        ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)

        if not ACCOUNTS_FILE.exists():
            p1_dir = "chrome_fb_profile" if Path("chrome_fb_profile").exists() else "profiles/acc_1"
            self.accounts = [
                {
                    "id": "acc_1",
                    "name": "Tài khoản 1 (Chính)",
                    "profile_dir": p1_dir,
                    "enabled": True,
                    "status": "Sẵn sàng",
                    "proxy": "",
                },
                {
                    "id": "acc_2",
                    "name": "Tài khoản 2 (Phụ)",
                    "profile_dir": "profiles/acc_2",
                    "enabled": True,
                    "status": "Sẵn sàng",
                    "proxy": "",
                },
            ]
            self.save_accounts()
            return

        try:
            raw = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
            self.accounts = raw if isinstance(raw, list) else []
        except Exception:
            self.accounts = []

    def save_accounts(self) -> None:
        ACCOUNTS_FILE.write_text(
            json.dumps(self.accounts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_accounts(self) -> List[Dict[str, Any]]:
        return self.accounts

    def get_account_by_id(self, acc_id: str) -> Optional[Dict[str, Any]]:
        return next((a for a in self.accounts if a.get("id") == acc_id), None)

    def add_account(self, name: str, proxy: Optional[str] = "") -> Dict[str, Any]:
        acc_id = f"acc_{uuid.uuid4().hex[:6]}"
        profile_path = f"profiles/{acc_id}"
        Path(profile_path).mkdir(parents=True, exist_ok=True)
        account = {
            "id": acc_id,
            "name": name or f"Tài khoản {len(self.accounts) + 1}",
            "profile_dir": profile_path,
            "enabled": True,
            "status": "Sẵn sàng",
            "proxy": proxy.strip() if proxy else "",
        }
        self.accounts.append(account)
        self.save_accounts()
        return account

    def update_account(self, acc_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        account = self.get_account_by_id(acc_id)
        if not account:
            return None
        for key, value in updates.items():
            if key != "id":
                account[key] = value
        self.save_accounts()
        return account

    def toggle_account(self, acc_id: str, enabled: bool) -> bool:
        account = self.get_account_by_id(acc_id)
        if not account:
            return False
        account["enabled"] = enabled
        self.save_accounts()
        return True

    def delete_account(self, acc_id: str) -> None:
        # Explicit session shutdown before removing account metadata.
        try:
            from browser_session_manager import browser_session_mgr
            browser_session_mgr.close_account_session(acc_id, force=True)
        except Exception:
            pass
        self.accounts = [a for a in self.accounts if a.get("id") != acc_id]
        self.save_accounts()

    def open_login_session(self, acc_id: str) -> bool:
        """Open the account's single managed Chrome session for manual login."""
        account = self.get_account_by_id(acc_id)
        if not account:
            return False

        profile_dir = Path(account["profile_dir"]).resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        proxy = parse_proxy(account.get("proxy", ""))

        from browser_session_manager import browser_session_mgr, BrowserSessionError
        try:
            page = browser_session_mgr.open_interactive_page(
                account_id=acc_id,
                profile_dir=profile_dir,
                proxy_parsed=proxy,
            )
            page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=25000)
            return True
        except BrowserSessionError:
            return False
        except Exception:
            return False

    def check_account_status(self, acc_id: str) -> Dict[str, Any]:
        account = self.get_account_by_id(acc_id)
        if not account:
            return {}

        info = detect_fb_name(account.get("profile_dir", ""))
        if info.get("error"):
            if account.get("fb_name"):
                return account
            account["status"] = f"Lỗi: {str(info.get('error'))[:80]}"
            self.save_accounts()
            return account

        account["logged_in"] = bool(info.get("logged_in"))
        account["fb_name"] = info.get("fb_name")
        account["fb_id"] = info.get("fb_id")
        account["status"] = "Đã đăng nhập" if account["logged_in"] else "Chưa đăng nhập"
        self.save_accounts()
        return account


account_mgr = AccountManager()
