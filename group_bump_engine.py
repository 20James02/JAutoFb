"""
Group Bump Engine for Facebook Automation
Automatically visits existing group post URLs and posts comments to bump posts to the top of group feeds.
"""

import os
import time
import random
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from playwright.sync_api import sync_playwright
from meta_sentinel import meta_sentinel

class GroupBumpEngine:
    def __init__(self, log_callback: Optional[Callable[[str, str, Optional[str]], None]] = None):
        self.log_callback = log_callback
        self.state = "IDLE"  # IDLE, RUNNING, STOPPED, ERROR
        self.stats = {
            "total_posts": 0,
            "current_idx": 0,
            "success": 0,
            "failed": 0,
            "current_post_url": ""
        }
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def log(self, message: str, level: str = "info", account_name: Optional[str] = None):
        if self.log_callback:
            prefix = f"[{account_name}] " if account_name else ""
            self.log_callback(f"[BUMP] {prefix}{message}", level, None)
        else:
            print(f"[BUMP] {message}")

    def _pick_spintax(self, text: str) -> str:
        if not text:
            return "."
        parts = [p.strip() for p in text.split("|") if p.strip()]
        return random.choice(parts) if parts else text

    def start_bumping(self, account: Dict[str, Any], config: Dict[str, Any]) -> bool:
        if self.state == "RUNNING":
            return False

        self._stop_event.clear()
        self.state = "RUNNING"
        self.stats = {
            "total_posts": len(config.get("post_urls", [])),
            "current_idx": 0,
            "success": 0,
            "failed": 0,
            "current_post_url": ""
        }

        self._thread = threading.Thread(target=self._run_bumping, args=(account, config), daemon=True)
        self._thread.start()
        return True

    def stop_bumping(self):
        self._stop_event.set()
        self.state = "STOPPED"
        self.log("Đã nhận lệnh dừng tiến trình Bump bài nhóm.", "warning")

    def _run_bumping(self, account: Dict[str, Any], config: Dict[str, Any]):
        fb_suffix = f" [{account.get('fb_name', '')}]" if account.get("fb_name") else ""
        acc_name = f"{account.get('name', 'FB')}{fb_suffix}"
        profile_path = Path(account.get("profile_dir", "profiles/" + account.get("id"))).resolve()
        profile_path.mkdir(parents=True, exist_ok=True)

        headless = config.get("headless", False)
        post_urls = config.get("post_urls", [])
        comment_text = config.get("comment_text", ". | Quan tâm ạ | Check inbox shop ơi")
        like_post = config.get("like_post", True)
        min_delay = int(config.get("min_delay", 15))
        max_delay = int(config.get("max_delay", 30))

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
                self.log(f"Đang kết nối qua Proxy: {proxy_cfg['server']}", "info", acc_name)

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

            for p_idx, p_url in enumerate(post_urls, 1):
                if self._stop_event.is_set():
                    break

                self.stats["current_idx"] = p_idx
                self.stats["current_post_url"] = p_url
                self.log(f"-> Bump bài {p_idx}/{len(post_urls)}: {p_url}...", "info", acc_name)

                try:
                    page.goto(p_url, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(3)

                    # Cuộn nhẹ để nạp tương tác bài viết
                    page.evaluate("window.scrollBy(0, 300)")
                    time.sleep(1.5)

                    # Thả like bài viết nếu bật
                    if like_post:
                        try:
                            like_btn = page.locator('div[aria-label="Thích"], div[aria-label="Like"]').first
                            if like_btn.is_visible():
                                like_btn.click(timeout=2000)
                                time.sleep(1)
                                self.log("Thả cảm xúc Thích bài viết thành công.", "info", acc_name)
                        except Exception:
                            pass

                    # Tìm ô nhập bình luận của bài viết
                    comment_box = page.locator('div[role="textbox"][contenteditable="true"]').first
                    if comment_box.is_visible():
                        bump_text = self._pick_spintax(comment_text)

                        # Kiểm tra phanh an toàn và đánh giá rủi ro Meta Sentinel
                        acc_key = account.get("id") or acc_name
                        is_tripped, trip_reason = meta_sentinel.is_circuit_tripped(acc_key)
                        if is_tripped:
                            self.log(f"🚨 PHANH AN TOÀN KÍCH HOẠT: Dừng tài khoản '{acc_name}' ({trip_reason})!", "error", acc_name)
                            break

                        risk_info = meta_sentinel.record_action_start(acc_key, "group_bump", content=bump_text, delay_seconds=min_delay, account_name=acc_name)
                        if risk_info.get("risk_score", 0) >= 70:
                            self.log(f"⚠️ Radar Meta: Rủi ro {risk_info['risk_score']}%. {', '.join(risk_info.get('risk_factors', []))}", "warning", acc_name)

                        comment_box.click()
                        time.sleep(0.5)
                        normalized_bump = (bump_text or "").replace("\r\n", "\n").replace("\r", "\n")
                        bump_lines = normalized_bump.split("\n")
                        for bi, bline in enumerate(bump_lines):
                            if bline:
                                bwords = bline.split(" ")
                                for bj, bword in enumerate(bwords):
                                    page.keyboard.insert_text(bword + (" " if bj < len(bwords) - 1 else ""))
                                    time.sleep(0.02)
                            if bi < len(bump_lines) - 1:
                                page.keyboard.press("Shift+Enter")
                                time.sleep(0.1)
                        time.sleep(1)
                        page.keyboard.press("Enter")
                        time.sleep(2.5)

                        # Giám sát phản ứng Meta sau khi Bump
                        incident = meta_sentinel.inspect_page(page, acc_key, "group_bump", account_name=acc_name, context_info=f"Bump bài: {p_url}")
                        if incident:
                            self.log(f"🚨 Phát hiện phản ứng Meta: {incident.get('error_text')}", "error", acc_name)
                            meta_sentinel.record_action_result(acc_key, "group_bump", success=False, error_msg=incident.get('error_text'), account_name=acc_name)
                            break
                        else:
                            meta_sentinel.record_action_result(acc_key, "group_bump", success=True, account_name=acc_name)

                        self.stats["success"] += 1
                        self.log(f"[THÀNH CÔNG] Đã comment Bump bài: \"{bump_text}\"", "success", acc_name)
                        try:
                            from published_posts_manager import published_posts_mgr
                            marked = published_posts_mgr.mark_as_bumped(p_url, bump_text)
                            if marked:
                                self.log(f"-> Đã ghi nhận & đánh dấu bài viết vào Lịch sử (Đã Bump)", "info", acc_name)
                        except Exception: pass
                    else:
                        self.stats["failed"] += 1
                        self.log(f"[!] Không tìm thấy khung bình luận (bài bị khóa cmt hoặc admin chưa duyệt): {p_url}", "warning", acc_name)

                except Exception as post_err:
                    self.stats["failed"] += 1
                    self.log(f"[LỖI] Bump bài thất bại: {str(post_err)[:80]}", "error", acc_name)

                # Giãn cách an toàn giữa các bài viết
                if p_idx < len(post_urls) and not self._stop_event.is_set():
                    delay = random.randint(min_delay, max_delay)
                    self.log(f"Chờ an toàn {delay}s trước khi bump bài tiếp theo...", "info", acc_name)
                    for _ in range(delay):
                        if self._stop_event.is_set():
                            break
                        time.sleep(1)

            self.state = "IDLE"
            self.log(f"HOÀN THÀNH TIẾN TRÌNH BUMP BÀI NHÓM! Thành công: {self.stats['success']}, Thất bại: {self.stats['failed']}", "success", acc_name)

        except Exception as e:
            self.state = "ERROR"
            self.log(f"[LỖI HỆ THỐNG] Động cơ Bump gặp sự cố: {e}", "error", acc_name)
        finally:
            if context:
                try: context.close()
                except Exception: pass
            if pw:
                try: pw.stop()
                except Exception: pass
            self.state = "IDLE"

group_bump_engine = GroupBumpEngine()
