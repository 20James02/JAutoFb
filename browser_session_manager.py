"""
Browser Session Manager for JAutoFb
Manages shared Chrome browser instances per account using Chrome DevTools Protocol (CDP).
Eliminates profile lock collisions (SingletonLock) and enables multiple parallel tasks/tabs
to run concurrently on the SAME profile with minimal memory/CPU overhead.
"""

import os
import sys
import time
import json
import urllib.request
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from playwright.sync_api import sync_playwright, BrowserContext, Page, Playwright

from account_manager import (
    clean_profile_locks,
    get_playwright_launch_args,
    PROFILES_DIR
)

class BrowserSessionManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(BrowserSessionManager, cls).__new__(cls)
                cls._instance._init_manager()
            return cls._instance

    def _init_manager(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.base_port = 9300
        self.manager_lock = threading.Lock()

    def get_port_for_account(self, account_id: str) -> int:
        """Sinh cổng cố định duy nhất cho từng tài khoản dựa trên mã id"""
        try:
            num_part = ''.join(c for c in account_id if c.isdigit())
            if num_part:
                return self.base_port + int(num_part)
        except Exception:
            pass
        return self.base_port + (abs(hash(account_id)) % 100) + 1

    def is_port_alive(self, port: int) -> bool:
        """Kiểm tra xem Chrome trên cổng CDP có đang phản hồi không"""
        try:
            url = f"http://127.0.0.1:{port}/json/version"
            req = urllib.request.Request(url, headers={"User-Agent": "BrowserManager"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode('utf-8'))
                    return "webSocketDebuggerUrl" in data or "Browser" in data
        except Exception:
            pass
        return False

    def acquire_page(
        self,
        account_id: str,
        profile_dir: Path,
        proxy_parsed: Optional[Dict[str, Any]] = None,
        headless: bool = False
    ) -> Tuple[Any, Page, bool]:
        """
        Lấy hoặc khởi tạo 1 Page cho account_id.
        Nếu Chrome của account_id đã chạy -> Kết nối qua CDP và tạo Page mới trong Chrome đó.
        Nếu Chrome chưa chạy -> Khởi động Chrome với remote-debugging-port và trả về Page đầu tiên.
        Trả về: (browser_or_context, page, is_cdp)
        """
        port = self.get_port_for_account(account_id)
        profile_path = Path(profile_dir).resolve()

        with self.manager_lock:
            if account_id not in self.sessions:
                self.sessions[account_id] = {
                    "port": port,
                    "active_pages": 0,
                    "pw": None,
                    "context": None,
                    "lock": threading.Lock()
                }
            acc_info = self.sessions[account_id]

        with acc_info["lock"]:
            # 1. Kiểm tra nếu Chrome trên port này đã chạy -> dùng CDP
            if self.is_port_alive(port):
                try:
                    pw = sync_playwright().start()
                    cdp_browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                    if cdp_browser.contexts:
                        ctx = cdp_browser.contexts[0]
                    else:
                        ctx = cdp_browser.new_context()
                    page = ctx.new_page()
                    acc_info["active_pages"] += 1
                    return (cdp_browser, page, True)
                except Exception:
                    pass

            # 2. Chưa chạy hoặc port không phản hồi: Khởi chạy Chrome mới
            clean_profile_locks(profile_path)
            pw = sync_playwright().start()
            launch_cfg = get_playwright_launch_args(proxy_parsed=proxy_parsed, headless=headless)
            
            # Bổ sung cờ remote debugging port
            args = list(launch_cfg.get("args", []))
            port_arg = f"--remote-debugging-port={port}"
            if not any(a.startswith("--remote-debugging-port") for a in args):
                args.append(port_arg)
            launch_cfg["args"] = args

            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                **launch_cfg
            )

            # Lấy trang có sẵn hoặc tạo mới
            if context.pages:
                page = context.pages[0]
            else:
                page = context.new_page()

            acc_info["active_pages"] = 1
            acc_info["pw"] = pw
            acc_info["context"] = context
            return (context, page, False)

    def release_page(
        self,
        account_id: str,
        browser_or_context: Any,
        page: Page,
        is_cdp: bool
    ):
        """Đóng trang và dọn dẹp tài nguyên an toàn"""
        try:
            if page:
                page.close()
        except Exception:
            pass

        with self.manager_lock:
            acc_info = self.sessions.get(account_id)

        if acc_info:
            with acc_info["lock"]:
                acc_info["active_pages"] = max(0, acc_info["active_pages"] - 1)
                
                if is_cdp:
                    try:
                        if browser_or_context:
                            browser_or_context.close()
                    except Exception:
                        pass
                else:
                    if acc_info["active_pages"] == 0:
                        try:
                            if browser_or_context:
                                browser_or_context.close()
                        except Exception:
                            pass
                        try:
                            pw = acc_info.get("pw")
                            if pw:
                                pw.stop()
                        except Exception:
                            pass
                        acc_info["context"] = None
                        acc_info["pw"] = None


browser_session_mgr = BrowserSessionManager()
