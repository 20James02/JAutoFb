"""
Facebook Group Comment / Seeding Engine
Automates commenting on posts in selected Facebook groups.
Supports:
- Role switcher (Personal Profile or Fanpage)
- Spintax random text (|)
- Comment image upload
- Like post before comment
- Headless or GUI Chrome
- Safe delays
"""

import os
import sys
import time
import random
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from role_switcher import ensure_role
from meta_sentinel import meta_sentinel

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


class GroupCommentEngine:
    def __init__(self, log_callback: Optional[Callable[[str, str], None]] = None):
        self.log_callback = log_callback
        self.state = "IDLE"  # IDLE, RUNNING, PAUSED, STOPPED
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

        self.stats = {
            "state": "IDLE",
            "total_groups": 0,
            "current_group_idx": 0,
            "comments_done": 0,
            "failed": 0,
            "current_group": ""
        }

    def log(self, msg: str, lvl: str = "info", acc_name: str = ""):
        timestamp = time.strftime("%H:%M:%S")
        prefix = f"[{acc_name}] " if acc_name else ""
        line = f"[{timestamp}] [COMMENT_NHÓM] {prefix}{msg}"
        print(line)
        if self.log_callback:
            try:
                self.log_callback(line, lvl)
            except Exception:
                pass

    def start_commenting(self, account: Dict[str, Any], config: Dict[str, Any]):
        if self.state == "RUNNING":
            self.log("Tiến trình comment nhóm đang chạy!", "warning")
            return

        self._stop_event.clear()
        self._pause_event.set()
        self.state = "RUNNING"
        self.stats["state"] = "RUNNING"
        self.stats["comments_done"] = 0
        self.stats["failed"] = 0
        self.stats["total_groups"] = len(config.get("group_urls", []))

        self.thread = threading.Thread(target=self._run_commenting, args=(account, config), daemon=True)
        self.thread.start()

    def stop(self):
        if self.state in ["RUNNING", "PAUSED"]:
            self.state = "STOPPED"
            self.stats["state"] = "STOPPED"
            self._stop_event.set()
            self.log("Đã gửi lệnh dừng tiến trình comment nhóm.", "warning")

    def pause(self):
        if self.state == "RUNNING":
            self.state = "PAUSED"
            self.stats["state"] = "PAUSED"
            self._pause_event.clear()
            self.log("Đã tạm dừng comment nhóm.", "warning")

    def resume(self):
        if self.state == "PAUSED":
            self.state = "RUNNING"
            self.stats["state"] = "RUNNING"
            self._pause_event.set()
            self.log("Đã tiếp tục comment nhóm.", "info")

    def _sleep_delay(self, min_d: int, max_d: int, acc_name: str):
        delay = random.randint(min_d, max(min_d, max_d))
        self.log(f"Nghỉ giãn cách {delay}s...", "info", acc_name)
        for _ in range(delay):
            if self._stop_event.is_set():
                break
            self._pause_event.wait()
            time.sleep(1)

    def _pick_spintax(self, text: str) -> str:
        if not text:
            return ""
        parts = [p.strip() for p in text.split("|") if p.strip()]
        return random.choice(parts) if parts else text

    def _run_commenting(self, account: Dict[str, Any], config: Dict[str, Any]):
        fb_suffix = f" [{account.get('fb_name', '')}]" if account.get("fb_name") else ""
        acc_name = f"{account.get('name', 'FB')}{fb_suffix}"
        profile_path = Path(account.get("profile_dir", "profiles/" + account.get("id"))).resolve()
        profile_path.mkdir(parents=True, exist_ok=True)

        headless = config.get("headless", False)
        group_urls = config.get("group_urls", [])
        comments_per_group = int(config.get("comments_per_group", 1))
        comment_text_template = config.get("comment_text", "")
        comment_image = config.get("comment_image", "")
        like_post = config.get("like_post", True)
        min_delay = int(config.get("min_delay", 15))
        max_delay = int(config.get("max_delay", 30))

        role_type = config.get("role_type", "personal")
        role_url = config.get("role_url")

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

            # Chuyển vai trò nếu được chọn
            self.log(f"Kiểm tra và chuẩn bị vai trò [{role_type}]...", "info", acc_name)
            ensure_role(page, role_type=role_type, role_url=role_url, log_callback=lambda m: self.log(m, "info", acc_name))

            for g_idx, g_url in enumerate(group_urls, 1):
                if self._stop_event.is_set():
                    break
                self._pause_event.wait()

                self.stats["current_group_idx"] = g_idx
                self.stats["current_group"] = g_url
                self.log(f"-> Nhóm {g_idx}/{len(group_urls)}: {g_url}...", "info", acc_name)

                try:
                    page.goto(g_url, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(4)

                    # Cuộn nhẹ trang nhóm để nạp bài viết
                    for _ in range(2):
                        page.evaluate("window.scrollBy(0, 500)")
                        time.sleep(1.5)

                    # Tìm các hộp bình luận trên bài viết của nhóm
                    comment_inputs = page.locator('div[role="textbox"][contenteditable="true"]').all()
                    done_in_group = 0

                    for c_box in comment_inputs:
                        if done_in_group >= comments_per_group or self._stop_event.is_set():
                            break
                        self._pause_event.wait()

                        try:
                            if c_box.is_visible():
                                # Thử like bài viết gần đó nếu bật
                                if like_post:
                                    try:
                                        like_btn = page.locator('div[aria-label="Thích"], div[aria-label="Like"]').first
                                        if like_btn.is_visible():
                                            like_btn.click(timeout=2000)
                                            time.sleep(1)
                                    except Exception:
                                        pass

                                text_to_send = self._pick_spintax(comment_text_template)
                                if not text_to_send:
                                    text_to_send = "Quan tâm ạ"

                                # Kiểm tra phanh an toàn và đánh giá rủi ro Meta Sentinel
                                acc_key = account.get("id") or acc_name
                                is_tripped, trip_reason = meta_sentinel.is_circuit_tripped(acc_key)
                                if is_tripped:
                                    self.log(f"🚨 PHANH AN TOÀN KÍCH HOẠT: Dừng tài khoản '{acc_name}' ({trip_reason})!", "error", acc_name)
                                    break

                                risk_info = meta_sentinel.record_action_start(acc_key, "group_comment", content=text_to_send, delay_seconds=min_delay, account_name=acc_name)
                                if risk_info.get("risk_score", 0) >= 70:
                                    self.log(f"⚠️ Radar Meta: Rủi ro {risk_info['risk_score']}%. {', '.join(risk_info.get('risk_factors', []))}", "warning", acc_name)

                                c_box.click()
                                time.sleep(0.5)
                                normalized_cmt = (text_to_send or "").replace("\r\n", "\n").replace("\r", "\n")
                                cmt_lines = normalized_cmt.split("\n")
                                for ci, cline in enumerate(cmt_lines):
                                    if cline:
                                        cwords = cline.split(" ")
                                        for cj, cword in enumerate(cwords):
                                            page.keyboard.insert_text(cword + (" " if cj < len(cwords) - 1 else ""))
                                            time.sleep(0.02)
                                    if ci < len(cmt_lines) - 1:
                                        page.keyboard.press("Shift+Enter")
                                        time.sleep(0.1)
                                time.sleep(1)

                                # Đính kèm ảnh comment nếu có
                                if comment_image and os.path.exists(comment_image):
                                    try:
                                        file_input = page.locator('input[type="file"][accept*="image"]').first
                                        if file_input:
                                            file_input.set_input_files(comment_image)
                                            time.sleep(2)
                                    except Exception as img_err:
                                        self.log(f"Không thể đính kèm ảnh: {img_err}", "warning", acc_name)

                                c_box.press("Enter")
                                time.sleep(2.5)

                                # Giám sát phản ứng Meta sau khi gửi bình luận
                                incident = meta_sentinel.inspect_page(page, acc_key, "group_comment", account_name=acc_name, context_info=f"Bình luận nhóm {g_url}")
                                if incident:
                                    self.log(f"🚨 Phát hiện phản ứng Meta: {incident.get('error_text')}", "error", acc_name)
                                    meta_sentinel.record_action_result(acc_key, "group_comment", success=False, error_msg=incident.get('error_text'), account_name=acc_name)
                                    break
                                else:
                                    meta_sentinel.record_action_result(acc_key, "group_comment", success=True, account_name=acc_name)

                                done_in_group += 1
                                self.stats["comments_done"] += 1
                                self.log(f"Đã bình luận thành công ({done_in_group}/{comments_per_group}) trong nhóm!", "success", acc_name)

                                if done_in_group < comments_per_group:
                                    self._sleep_delay(min_delay, max_delay, acc_name)
                        except Exception as c_err:
                            self.log(f"Lỗi khi gửi bình luận: {c_err}", "warning", acc_name)

                    if done_in_group == 0:
                        self.log("Không tìm thấy bài viết hoặc hộp bình luận khả dụng trong nhóm này.", "warning", acc_name)
                        self.stats["failed"] += 1

                    # Delay giữa các nhóm
                    if g_idx < len(group_urls) and not self._stop_event.is_set():
                        self._sleep_delay(min_delay, max_delay, acc_name)

                except Exception as g_err:
                    self.log(f"Lỗi truy cập nhóm {g_url}: {g_err}", "error", acc_name)
                    self.stats["failed"] += 1

            self.log(f"HOÀN THÀNH TIẾN TRÌNH COMMENT NHÓM! Tổng bình luận: {self.stats['comments_done']}", "success", acc_name)

        except Exception as ex:
            self.log(f"Lỗi trong quá trình comment nhóm: {ex}", "error", acc_name)
        finally:
            self.state = "IDLE"
            self.stats["state"] = "IDLE"
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            if pw:
                try:
                    pw.stop()
                except Exception:
                    pass

group_comment_engine = GroupCommentEngine()
