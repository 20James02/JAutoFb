"""
Comment Shield Engine for Fanpage Protection
Continuously monitors Fanpage posts, detects customer phone numbers via Regex,
and automatically hides comments to prevent competitors from stealing customer leads.
"""

import os
import re
import time
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from playwright.sync_api import sync_playwright

DEFAULT_PHONE_REGEX = r"(?:(?:\+84|84|0)[3|5|7|8|9])(?:[\s.-]?\d){8}\b"

class CommentShieldEngine:
    def __init__(self, log_callback: Optional[Callable[[str, str, Optional[str]], None]] = None):
        self.log_callback = log_callback
        self.state = "IDLE"  # IDLE, RUNNING, STOPPED, ERROR
        self.stats = {
            "scanned_comments": 0,
            "hidden_leads": 0,
            "last_detected": None,
            "page_url": ""
        }
        self.detected_leads: List[Dict[str, Any]] = []
        self.history: List[Dict[str, Any]] = []
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def log(self, message: str, level: str = "info", page_name: Optional[str] = None):
        if self.log_callback:
            prefix = f"[{page_name}] " if page_name else ""
            self.log_callback(f"[SHIELD] {prefix}{message}", level, None)
        else:
            print(f"[SHIELD] {message}")

    def start_shield(self, account: Dict[str, Any], config: Dict[str, Any]) -> bool:
        if self.state == "RUNNING":
            return False

        self._stop_event.clear()
        self.state = "RUNNING"
        self.stats = {
            "scanned_comments": 0,
            "hidden_leads": 0,
            "last_detected": None,
            "page_url": config.get("page_url", "")
        }

        self._thread = threading.Thread(target=self._run_shield, args=(account, config), daemon=True)
        self._thread.start()
        return True

    def stop_shield(self):
        self._stop_event.set()
        self.state = "STOPPED"
        self.log("Đã nhận lệnh dừng giám sát bảo vệ Fanpage.", "warning")

    def _mask_phone(self, phone: str) -> str:
        clean = re.sub(r"\D", "", phone)
        if len(clean) >= 7:
            return clean[:3] + "****" + clean[-3:]
        return phone

    def _run_shield(self, account: Dict[str, Any], config: Dict[str, Any]):
        fb_suffix = f" [{account.get('fb_name', '')}]" if account.get("fb_name") else ""
        acc_name = f"{account.get('name', 'FB')}{fb_suffix}"
        profile_path = Path(account.get("profile_dir", "profiles/" + account.get("id"))).resolve()
        profile_path.mkdir(parents=True, exist_ok=True)

        page_url = config.get("page_url", "")
        if not page_url.startswith("http"):
            page_url = f"https://www.facebook.com/{page_url.strip('/')}"

        phone_regex = config.get("phone_regex", DEFAULT_PHONE_REGEX)
        pattern = re.compile(phone_regex, re.IGNORECASE)
        poll_interval = int(config.get("poll_interval", 15))
        headless = config.get("headless", True)

        from account_manager import kill_chrome_for_profile
        kill_chrome_for_profile(profile_path)

        pw = None
        context = None

        try:
            pw = sync_playwright().start()
            from account_manager import parse_proxy
            proxy_cfg = parse_proxy(account.get("proxy"))
            pw_proxy = None
            if proxy_cfg and proxy_cfg.get("server"):
                pw_proxy = {"server": proxy_cfg["server"]}
                if proxy_cfg.get("username"):
                    pw_proxy["username"] = proxy_cfg["username"]
                if proxy_cfg.get("password"):
                    pw_proxy["password"] = proxy_cfg["password"]

            from account_manager import get_standard_chrome_args, get_natural_user_agent
            launch_args = get_standard_chrome_args(headless=headless)

            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                channel="chrome",
                headless=headless,
                ignore_default_args=["--enable-automation"],
                user_agent=get_natural_user_agent(),
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
                no_viewport=True if not headless else False,
                viewport=None if not headless else {"width": 1280, "height": 850},
                args=launch_args,
                proxy=pw_proxy
            )
            context.add_init_script("""
                window.chrome = window.chrome || { runtime: {} };
            """)

            page = context.pages[0] if context.pages else context.new_page()

            self.log(f"Bắt đầu giám sát Fanpage: {page_url}...", "info", acc_name)
            page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)

            while not self._stop_event.is_set():
                try:
                    # Cuộn nhẹ để nạp các bình luận mới
                    page.evaluate("window.scrollBy(0, 400)")
                    time.sleep(2)

                    # Quét toàn bộ khối bình luận hiển thị trên trang
                    comment_elements = page.locator('div[role="article"]').all()
                    self.stats["scanned_comments"] += len(comment_elements)

                    for c_el in comment_elements:
                        if self._stop_event.is_set():
                            break
                        try:
                            text = c_el.inner_text()
                            match = pattern.search(text)
                            if match:
                                raw_phone = match.group(0)
                                masked = self._mask_phone(raw_phone)
                                self.log(f"⚠️ Phát hiện SĐT khách hàng: {masked}. Đang kích hoạt ẩn bình luận...", "warning", acc_name)

                                # Tìm nút 3 chấm (...) của bình luận này để ẩn
                                action_btn = c_el.locator('div[aria-label="Hành động khác đối với bình luận"], div[aria-label="More options for comment"], div[aria-label="Tùy chọn"]').first
                                if action_btn and action_btn.is_visible():
                                    action_btn.click()
                                    time.sleep(1)

                                    hide_opt = page.locator('span:has-text("Ẩn bình luận"), span:has-text("Hide comment")').first
                                    if hide_opt and hide_opt.is_visible():
                                        hide_opt.click()
                                        time.sleep(1)
                                        self.stats["hidden_leads"] += 1
                                        self.stats["last_detected"] = time.strftime("%H:%M:%S")
                                        self.detected_leads.append({"phone": raw_phone, "detected_at": time.time()})
                                        self.history.append({"phone": raw_phone, "detected_at": time.time()})
                                        self.log(f"🛡️ [BẢO VỆ THÀNH CÔNG] Đã ẩn bình luận chứa SĐT {masked} chống đối thủ cướp khách!", "success", acc_name)
                        except Exception:
                            pass

                except Exception as loop_err:
                    self.log(f"Lỗi trong chu kỳ giám sát: {str(loop_err)[:80]}", "warning", acc_name)

                # Chờ chu kỳ tiếp theo
                for _ in range(poll_interval):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)

            self.state = "IDLE"
            self.log("Đã kết thúc phiên giám sát Fanpage an toàn.", "info", acc_name)

        except Exception as e:
            self.state = "ERROR"
            self.log(f"[LỖI HỆ THỐNG] Động cơ Shield gặp lỗi: {e}", "error", acc_name)
        finally:
            if context:
                try: context.close()
                except Exception: pass
            if pw:
                try: pw.stop()
                except Exception: pass
            self.state = "IDLE"

comment_shield_engine = CommentShieldEngine()
