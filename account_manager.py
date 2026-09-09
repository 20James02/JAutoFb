"""
Account Manager for Multi-Account Facebook Automation
Handles profile directories, account metadata, and login session launching.
"""

import os
import sys
import re
import time
import json
import uuid
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional

ACCOUNTS_FILE = Path("data/accounts.json")
PROFILES_DIR = Path("profiles")

def find_chrome() -> str:
    p1 = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if os.path.exists(p1): return p1
    p2 = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    if os.path.exists(p2): return p2
    return "chrome.exe"


def clean_profile_locks(profile_dir: Path):
    """Xóa các file lock cũ nếu Chrome bị đóng đột ngột"""
    p = Path(profile_dir).resolve()
    for lock_pattern in ["*lock*", "*Singleton*"]:
        for f in p.glob(lock_pattern):
            try:
                if f.is_file():
                    f.unlink(missing_ok=True)
            except Exception:
                pass


def kill_chrome_for_profile(profile_dir: Path):
    """Dọn dẹp các tiến trình Chrome chạy ngầm đang chiếm giữ profile này"""
    abs_profile = str(Path(profile_dir).resolve()).lower()
    try:
        ps_cmd = 'Get-CimInstance Win32_Process -Filter "name=\'chrome.exe\'" | Select-Object ProcessId, CommandLine | ConvertTo-Json'
        out = subprocess.check_output(['powershell', '-NoProfile', '-Command', ps_cmd], text=True, encoding='utf-8', timeout=5)
        if not out.strip():
            clean_profile_locks(profile_dir)
            return
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        for p in data:
            cmd = (p.get('CommandLine') or '').lower()
            if abs_profile in cmd:
                pid = p.get('ProcessId')
                if pid:
                    subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)
    except Exception:
        pass
    clean_profile_locks(profile_dir)


def detect_fb_name(profile_dir: str) -> Dict[str, Any]:
    p = Path(profile_dir).resolve()
    if not p.exists():
        return {"logged_in": False, "fb_name": None, "fb_id": None}
    
    from playwright.sync_api import sync_playwright
    pw = None
    context = None
    try:
        pw = sync_playwright().start()
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(p),
            channel="chrome",
            headless=True,
            ignore_default_args=["--enable-automation"],
            user_agent=get_natural_user_agent(),
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
            args=get_standard_chrome_args(headless=True)
        )
        cookies = context.cookies("https://www.facebook.com")
        c_user = next((c["value"] for c in cookies if c["name"] == "c_user"), None)
        if not c_user:
            return {"logged_in": False, "fb_name": None, "fb_id": None}

        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)

        name = page.evaluate(r"""(uid) => {
            // 1. Look for links with c_user id
            const links = Array.from(document.querySelectorAll(`a[href*="${uid}"]`));
            for (const a of links) {
                const t = a.innerText ? a.innerText.trim() : '';
                if (t && !t.includes('\n') && t.length > 1) return t;
            }
            // 2. Look for aria-label on profile button
            const profBtn = document.querySelector('div[aria-label="Trang cá nhân của bạn"], div[aria-label="Your profile"]');
            if (profBtn && profBtn.innerText) {
                return profBtn.innerText.trim();
            }
            // 3. Navigation link
            const navLinks = Array.from(document.querySelectorAll('div[role="navigation"] a'));
            for (const a of navLinks) {
                const h = a.getAttribute('href') || '';
                if (h.includes('profile.php') || h.includes('/me')) {
                    const t = a.innerText ? a.innerText.trim() : '';
                    if (t && !t.includes('\n')) return t;
                }
            }
            return null;
        }""", c_user)

        return {"logged_in": True, "fb_name": name or "Tài khoản Facebook", "fb_id": c_user}
    except Exception as e:
        return {"logged_in": False, "fb_name": None, "fb_id": None, "error": str(e)}
    finally:
        if context:
            try: context.close()
            except Exception: pass
        if pw:
            try: pw.stop()
            except Exception: pass


def get_account_cookies_string(profile_dir: str) -> str:
    """
    Trích xuất chuỗi cookies 'c_user=...; xs=...' từ Profile Chrome.
    Có bộ nhớ đệm cache trong data/cookies_{safe_name}.txt để tối ưu tốc độ.
    """
    p = Path(profile_dir).resolve()
    if not p.exists():
        return ""
    
    safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', p.name)
    cache_file = Path("data") / f"cookies_{safe_name}.txt"
    # Kiểm tra cache hợp lệ trong 3 giờ
    if cache_file.exists():
        try:
            mtime = cache_file.stat().st_mtime
            if time.time() - mtime < 10800:  # 3 hours
                cached = cache_file.read_text(encoding="utf-8").strip()
                if "c_user=" in cached:
                    return cached
        except Exception:
            pass

    from playwright.sync_api import sync_playwright
    pw = None
    context = None
    try:
        pw = sync_playwright().start()
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(p),
            channel="chrome",
            headless=True,
            ignore_default_args=["--enable-automation"],
            user_agent=get_natural_user_agent(),
            args=get_standard_chrome_args(headless=True)
        )
        cookies = context.cookies("https://www.facebook.com")
        cookie_parts = [f"{c['name']}={c['value']}" for c in cookies if c.get("name") and c.get("value")]
        cookie_str = "; ".join(cookie_parts)
        if "c_user=" in cookie_str:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(cookie_str, encoding="utf-8")
        return cookie_str
    except Exception:
        return ""
    finally:
        if context:
            try: context.close()
            except Exception: pass
        if pw:
            try: pw.stop()
            except Exception: pass

def parse_proxy(raw_proxy: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Parse proxy string into Playwright & Requests compatible dictionary.
    Supports formats:
      - http://user:pass@ip:port
      - socks5://user:pass@ip:port
      - ip:port:user:pass
      - ip:port
      - http://ip:port
    """
    if not raw_proxy or not str(raw_proxy).strip():
        return None
    raw = str(raw_proxy).strip()
    proto = "http"
    for p in ["socks5://", "socks4://", "http://", "https://"]:
        if raw.startswith(p):
            proto = p.rstrip("://")
            raw = raw[len(p):]
            break
            
    parts = raw.split(":")
    if len(parts) == 4 and "@" not in raw:
        ip, port, user, pwd = parts
        return {
            "server": f"{proto}://{ip}:{port}",
            "username": user,
            "password": pwd,
            "raw": str(raw_proxy).strip()
        }
    if "@" in raw:
        auth_part, host_part = raw.split("@", 1)
        user, pwd = auth_part.split(":", 1) if ":" in auth_part else (auth_part, "")
        return {
            "server": f"{proto}://{host_part}",
            "username": user,
            "password": pwd,
            "raw": str(raw_proxy).strip()
        }
    return {
        "server": f"{proto}://{raw}",
        "username": None,
        "password": None,
        "raw": str(raw_proxy).strip()
    }


def check_proxy_health(proxy_str: str) -> Dict[str, Any]:
    """
    Kiểm tra tình trạng kết nối, độ trễ và IP thật sự của Proxy qua API kiểm tra IP.
    """
    parsed = parse_proxy(proxy_str)
    if not parsed:
        return {"success": False, "error": "Định dạng Proxy không hợp lệ"}

    import requests
    server = parsed["server"]
    u = parsed.get("username")
    p = parsed.get("password")
    
    if u and p:
        proto, host = server.split("://", 1)
        full_proxy_url = f"{proto}://{u}:{p}@{host}"
    else:
        full_proxy_url = server

    proxies = {
        "http": full_proxy_url,
        "https": full_proxy_url
    }
    
    t0 = time.time()
    try:
        resp = requests.get("http://ip-api.com/json/?fields=status,message,country,countryCode,query", proxies=proxies, timeout=4)
        latency = int((time.time() - t0) * 1000)
        data = resp.json()
        if data.get("status") == "success":
            return {
                "success": True,
                "ip": data.get("query"),
                "country": data.get("country", "Unknown"),
                "country_code": data.get("countryCode", ""),
                "latency_ms": latency
            }
        else:
            return {"success": False, "error": data.get("message", "Không thể phân giải địa chỉ IP qua Proxy")}
    except Exception as e:
        err_msg = str(e)
        if any(x in err_msg for x in ["ProxyError", "ConnectTimeout", "Max retries", "Connection refused", "Failed to establish", "refused"]):
            return {"success": False, "error": "Không thể kết nối đến máy chủ Proxy (Proxy Die hoặc Sai IP:Port)"}
        try:
            resp = requests.get("https://api.ipify.org?format=json", proxies=proxies, timeout=2.5)
            latency = int((time.time() - t0) * 1000)
            data = resp.json()
            return {
                "success": True,
                "ip": data.get("ip"),
                "country": "Unknown",
                "country_code": "",
                "latency_ms": latency
            }
        except Exception as e2:
            return {"success": False, "error": f"Lỗi kết nối Proxy: {str(e2)[:80]}"}


_CACHED_CHROME_VERSION = None

def get_chrome_version() -> str:
    """Lấy phiên bản Google Chrome thực tế đang cài đặt trên hệ thống Windows"""
    global _CACHED_CHROME_VERSION
    if _CACHED_CHROME_VERSION:
        return _CACHED_CHROME_VERSION
    try:
        exe = find_chrome()
        if exe and Path(exe).exists():
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", f"(Get-Item '{exe}').VersionInfo.ProductVersion"],
                capture_output=True, text=True, timeout=3
            )
            v = res.stdout.strip()
            if v and v[0].isdigit():
                _CACHED_CHROME_VERSION = v
                return v
    except Exception:
        pass
    _CACHED_CHROME_VERSION = "152.0.7977.77"
    return _CACHED_CHROME_VERSION


def get_natural_user_agent() -> str:
    """Tạo User-Agent chuẩn khớp chính xác 100% với bản Chrome đã cài đặt trên máy"""
    v = get_chrome_version()
    major = v.split('.')[0] if '.' in v else "152"
    return f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"


def get_standard_chrome_args(headless: bool = False) -> List[str]:
    """Các cờ dòng lệnh chuẩn tối đa Anti-Detect và bảo vệ phiên trình duyệt"""
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


def get_playwright_launch_args(proxy_parsed: Optional[Dict[str, Any]] = None, headless: bool = False) -> Dict[str, Any]:
    """
    Tạo bộ cấu hình khởi chạy Playwright chuẩn Anti-Detect tối đa và bảo vệ chống rò rỉ WebRTC IP.
    """
    cfg: Dict[str, Any] = {
        "args": get_standard_chrome_args(headless=headless),
        "channel": "chrome",
        "headless": headless,
        "ignore_default_args": ["--enable-automation"],
        "user_agent": get_natural_user_agent(),
        "locale": "vi-VN",
        "timezone_id": "Asia/Ho_Chi_Minh",
        "no_viewport": True if not headless else False,
        "viewport": None if not headless else {"width": 1280, "height": 850},
    }
    if proxy_parsed and proxy_parsed.get("server"):
        pw_proxy = {"server": proxy_parsed["server"]}
        if proxy_parsed.get("username"):
            pw_proxy["username"] = proxy_parsed["username"]
        if proxy_parsed.get("password"):
            pw_proxy["password"] = proxy_parsed["password"]
        cfg["proxy"] = pw_proxy
    return cfg


class AccountManager:
    def __init__(self):
        self.accounts: List[Dict[str, Any]] = []
        self.load_accounts()

    def load_accounts(self):
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
                    "proxy": ""
                },
                {
                    "id": "acc_2",
                    "name": "Tài khoản 2 (Phụ)",
                    "profile_dir": "profiles/acc_2",
                    "enabled": True,
                    "status": "Sẵn sàng",
                    "proxy": ""
                }
            ]
            self.save_accounts()
        else:
            try:
                self.accounts = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.accounts = []

    def save_accounts(self):
        ACCOUNTS_FILE.write_text(json.dumps(self.accounts, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_accounts(self) -> List[Dict[str, Any]]:
        return self.accounts

    def get_account_by_id(self, acc_id: str) -> Optional[Dict[str, Any]]:
        for a in self.accounts:
            if a["id"] == acc_id:
                return a
        return None

    def add_account(self, name: str, proxy: Optional[str] = "") -> Dict[str, Any]:
        acc_id = f"acc_{uuid.uuid4().hex[:6]}"
        profile_path = f"profiles/{acc_id}"
        Path(profile_path).mkdir(parents=True, exist_ok=True)

        acc = {
            "id": acc_id,
            "name": name or f"Tài khoản {len(self.accounts) + 1}",
            "profile_dir": profile_path,
            "enabled": True,
            "status": "Sẵn sàng",
            "proxy": proxy.strip() if proxy else ""
        }
        self.accounts.append(acc)
        self.save_accounts()
        return acc

    def update_account(self, acc_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for a in self.accounts:
            if a["id"] == acc_id:
                for k, v in updates.items():
                    if k != "id":
                        a[k] = v
                self.save_accounts()
                return a
        return None

    def toggle_account(self, acc_id: str, enabled: bool):
        for a in self.accounts:
            if a["id"] == acc_id:
                a["enabled"] = enabled
                self.save_accounts()
                return True
        return False

    def delete_account(self, acc_id: str):
        self.accounts = [a for a in self.accounts if a["id"] != acc_id]
        self.save_accounts()

    def open_login_session(self, acc_id: str) -> bool:
        """Mở cửa sổ Chrome giao diện có Profile của tài khoản để người dùng đăng nhập FB"""
        for a in self.accounts:
            if a["id"] == acc_id:
                profile_dir = Path(a["profile_dir"]).resolve()
                profile_dir.mkdir(parents=True, exist_ok=True)
                
                # 1. Dọn dẹp tiến trình Chrome cũ đang chiếm giữ profile này (nếu có)
                kill_chrome_for_profile(profile_dir)
                
                # 2. Khởi chạy Google Chrome trực tiếp (không qua cmd.exe để tránh lỗi quote)
                chrome_exe = find_chrome()
                cmd = [
                    chrome_exe,
                    f'--user-data-dir={profile_dir}',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-infobars',
                    '--lang=vi-VN,vi',
                    '--no-first-run',
                    '--no-default-browser-check',
                    '--start-maximized',
                    '--webrtc-ip-handling-policy=disable_non_proxied_udp',
                    '--force-webrtc-ip-handling-policy',
                ]
                
                # Nạp proxy vào Chrome nếu có
                if a.get("proxy"):
                    parsed = parse_proxy(a["proxy"])
                    if parsed and parsed.get("server"):
                        cmd.append(f"--proxy-server={parsed['server']}")

                cmd.append('https://www.facebook.com')
                subprocess.Popen(cmd)
                return True
        return False

    def check_account_status(self, acc_id: str) -> Dict[str, Any]:
        for a in self.accounts:
            if a["id"] == acc_id:
                info = detect_fb_name(a.get("profile_dir", ""))
                if info.get("error"):
                    if a.get("fb_name"):
                        return a
                    a["status"] = f"Lỗi: {info.get('error')[:40]}"
                    self.save_accounts()
                    return a
                a["logged_in"] = info.get("logged_in", False)
                a["fb_name"] = info.get("fb_name")
                a["fb_id"] = info.get("fb_id")
                a["status"] = "Đã đăng nhập" if a["logged_in"] else "Chưa đăng nhập"
                self.save_accounts()
                return a
        return {}

account_mgr = AccountManager()

