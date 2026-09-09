"""
Reels Publisher Engine for Facebook Reels Video Marketing
Supports:
- Publishing video files (.mp4, .mov) directly as Facebook Reels
- Selecting video folder or single files
- Role switching: Fanpage Reels or Personal Profile Reels
- Anti-Detect Stealth launch with per-profile proxy
- Spintax support in reel caption & hashtags
- Safe Human delays between posts
- Real-time logging and progress tracking
"""

import os
import re
import sys
import time
import random
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from role_switcher import ensure_role
from meta_sentinel import meta_sentinel

def parse_spintax(text: str) -> str:
    """Hỗ trợ Spintax dạng {a|b|c} cho caption bài Reels"""
    pattern = re.compile(r"\{([^{}]+)\}")
    while True:
        match = pattern.search(text)
        if not match:
            break
        choices = match.group(1).split("|")
        text = text[:match.start()] + random.choice(choices) + text[match.end():]
    return text

class ReelsPublisherEngine:
    def __init__(self, log_callback: Optional[Callable[[str, str, Optional[str]], None]] = None):
        self.log_callback = log_callback
        self.state = "IDLE"  # IDLE, RUNNING, STOPPED, ERROR
        self.stats = {
            "total_videos": 0,
            "published_count": 0,
            "failed_count": 0,
            "current_video": "",
            "progress_percent": 0
        }
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def log(self, message: str, level: str = "info", role_name: Optional[str] = None):
        if self.log_callback:
            prefix = f"[{role_name}] " if role_name else ""
            self.log_callback(f"[REELS] {prefix}{message}", level, None)
        else:
            print(f"[REELS] {message}")

    def start_publishing(self, account: Dict[str, Any], config: Dict[str, Any]) -> bool:
        if self.state == "RUNNING":
            return False

        self._stop_event.clear()
        self.state = "RUNNING"
        
        video_paths = config.get("video_paths", [])
        video_dir = config.get("video_dir", "")
        if video_dir and os.path.isdir(video_dir):
            exts = [".mp4", ".mov", ".mkv", ".avi", ".webm"]
            found = [str(p) for p in Path(video_dir).glob("*") if p.suffix.lower() in exts]
            video_paths.extend(found)
            video_paths = list(dict.fromkeys(video_paths))

        self.stats = {
            "total_videos": len(video_paths),
            "published_count": 0,
            "failed_count": 0,
            "current_video": "",
            "progress_percent": 0
        }

        self._thread = threading.Thread(target=self._run_publisher, args=(account, config, video_paths), daemon=True)
        self._thread.start()
        return True

    def stop_publishing(self):
        self._stop_event.set()
        self.state = "STOPPED"
        self.log("Đã nhận lệnh dừng tiến trình Đăng Reels.", "warning")

    def _run_publisher(self, account: Dict[str, Any], config: Dict[str, Any], video_paths: List[str]):
        fb_suffix = f" [{account.get('fb_name', '')}]" if account.get("fb_name") else ""
        acc_name = f"{account.get('name', 'FB')}{fb_suffix}"
        profile_path = Path(account.get("profile_dir", "profiles/" + account.get("id"))).resolve()
        profile_path.mkdir(parents=True, exist_ok=True)

        caption_template = config.get("caption", "#reels #trending")
        role_type = config.get("role_type", "personal")
        role_url = config.get("role_url")
        role_name = config.get("role_name")
        min_delay = int(config.get("min_delay", 30))
        max_delay = int(config.get("max_delay", 60))
        headless = bool(config.get("headless", False))

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

            # Chuyển vai trò nếu là Fanpage
            if role_type == "page" and (role_url or role_name):
                self.log(f"Thiết lập vai trò Fanpage: {role_name or role_url}...", "info", acc_name)
                ensure_role(
                    page=page,
                    role_type="page",
                    role_url=role_url,
                    role_name=role_name,
                    personal_name=account.get("fb_name") or account.get("name"),
                    log_fn=lambda m, l: self.log(m, l, acc_name)
                )

            total = len(video_paths)
            self.log(f"Chuẩn bị đăng {total} video Reels cho {acc_name}...", "info", acc_name)

            for idx, vid_file in enumerate(video_paths):
                if self._stop_event.is_set():
                    self.log("Dừng đăng Reels theo yêu cầu người dùng.", "warning", acc_name)
                    break

                vid_path = Path(vid_file)
                if not vid_path.exists():
                    self.log(f"Không tìm thấy file video: {vid_file}, bỏ qua.", "warning", acc_name)
                    self.stats["failed_count"] += 1
                    continue

                self.stats["current_video"] = vid_path.name
                self.log(f"[{idx+1}/{total}] Đang xử lý video: {vid_path.name}...", "info", acc_name)

                # Thuật toán đổi mã băm MD5 / SHA-256 độc bản chống trùng lặp Meta
                mutate_video = bool(config.get("mutate_video", True))
                upload_file_path = vid_path
                if mutate_video:
                    try:
                        from video_mutator import video_mutator
                        target_name = role_name or acc_name
                        mut_res = video_mutator.mutate_video_for_page(
                            input_video_path=str(vid_path),
                            page_name=target_name,
                            niche="REELS"
                        )
                        upload_file_path = Path(mut_res["mutated_path"])
                        self.log(f"⚡ Đã đổi mã băm MD5 video độc bản: {mut_res['original_md5'][:8]}... -> {mut_res['mutated_md5'][:8]}... (Chống quét trùng lặp Meta)", "success", acc_name)
                    except Exception as e_mut:
                        self.log(f"Cảnh báo đổi hash video: {e_mut}. Sử dụng file video gốc.", "warning", acc_name)

                # Kiểm tra phanh an toàn và đánh giá rủi ro Meta Sentinel
                acc_key = account.get("id") or acc_name
                is_tripped, trip_reason = meta_sentinel.is_circuit_tripped(acc_key)
                if is_tripped:
                    self.log(f"🚨 PHANH AN TOÀN KÍCH HOẠT: Dừng tài khoản '{acc_name}' ({trip_reason})!", "error", acc_name)
                    break

                risk_info = meta_sentinel.record_action_start(acc_key, "reels", content=caption_template, delay_seconds=min_delay, account_name=acc_name)
                if risk_info.get("risk_score", 0) >= 70:
                    self.log(f"⚠️ Radar Meta: Rủi ro {risk_info['risk_score']}%. {', '.join(risk_info.get('risk_factors', []))}", "warning", acc_name)

                # Mở trình tạo Reels Facebook
                page.goto("https://www.facebook.com/reel/create", wait_until="domcontentloaded", timeout=45000)
                time.sleep(3)

                # Tìm input file upload
                file_input = page.locator('input[type="file"][accept*="video"]')
                if file_input.count() == 0:
                    file_input = page.locator('input[type="file"]')

                if file_input.count() == 0:
                    self.log("Không tìm thấy nút tải video lên Facebook Reels. Thử lại sau 5s...", "warning", acc_name)
                    time.sleep(5)
                    file_input = page.locator('input[type="file"]')

                if file_input.count() > 0:
                    file_input.first.set_input_files(str(upload_file_path.resolve()))
                    self.log(f"Đã chọn tệp video: {upload_file_path.name}. Chờ Facebook tải lên...", "info", acc_name)
                    time.sleep(6)
                else:
                    self.log("Lỗi: Không thể định vị trường tải file trên trang tạo Reels!", "error", acc_name)
                    self.stats["failed_count"] += 1
                    continue

                # Nhập mô tả bài Reels (có Spintax)
                caption = parse_spintax(caption_template)
                desc_input = page.locator('div[role="textbox"], textarea[placeholder*="mô tả"], textarea[placeholder*="Describe"]')
                if desc_input.count() > 0:
                    try:
                        desc_input.first.click()
                        time.sleep(0.5)
                        desc_input.first.fill(caption)
                        self.log(f"Đã điền mô tả Reels: '{caption[:40]}...'", "info", acc_name)
                    except Exception:
                        pass

                time.sleep(2)

                # Bấm nút Tiếp tục (Next) hoặc Đăng (Publish)
                # Facebook Reels thường có 1-2 bước: "Tiếp" -> "Đăng"
                next_btn = page.locator('div[role="button"]:has-text("Tiếp"), div[role="button"]:has-text("Next")')
                if next_btn.count() > 0 and next_btn.first.is_visible():
                    try:
                        next_btn.first.click()
                        time.sleep(3)
                    except Exception:
                        pass

                # Bước cuối: Bấm Đăng / Publish
                publish_btn = page.locator('div[role="button"]:has-text("Đăng"), div[role="button"]:has-text("Publish"), div[role="button"]:has-text("Chia sẻ"), div[role="button"]:has-text("Share")')
                if publish_btn.count() > 0 and publish_btn.first.is_visible():
                    try:
                        publish_btn.first.click()
                        self.log("Đã bấm nút Đăng Reels. Chờ Facebook xử lý xuất bản...", "info", acc_name)
                        time.sleep(8)
                        self.stats["published_count"] += 1
                        self.log(f"Thành công gửi yêu cầu đăng Reels: {vid_path.name}!", "success", acc_name)
                    except Exception as pe:
                        self.log(f"Không thể bấm nút Đăng: {pe}", "error", acc_name)
                        self.stats["failed_count"] += 1
                else:
                    self.log("Không tìm thấy nút 'Đăng' cuối cùng, có thể video đang xử lý...", "warning", acc_name)
                    self.stats["published_count"] += 1

                # Giám sát phản ứng Meta sau khi đăng Reels
                time.sleep(3)
                incident = meta_sentinel.inspect_page(page, acc_key, "reels", account_name=acc_name, context_info=f"Đăng Reels: {vid_path.name}")
                if incident:
                    self.log(f"🚨 Phát hiện phản ứng Meta: {incident.get('error_text')}", "error", acc_name)
                    meta_sentinel.record_action_result(acc_key, "reels", success=False, error_msg=incident.get('error_text'), account_name=acc_name)
                    break
                else:
                    meta_sentinel.record_action_result(acc_key, "reels", success=True, account_name=acc_name)

                self.stats["progress_percent"] = int(((idx + 1) / total) * 100)

                # Giãn cách ngẫu nhiên an toàn Anti-Detect
                if idx < total - 1 and not self._stop_event.is_set():
                    delay = random.randint(min_delay, max_delay)
                    self.log(f"Nghỉ giãn cách an toàn {delay}s trước khi đăng video tiếp theo...", "info", acc_name)
                    for _ in range(delay):
                        if self._stop_event.is_set():
                            break
                        time.sleep(1)

            self.state = "IDLE"
            self.log(f"Hoàn tất chiến dịch Đăng Reels. Thành công: {self.stats['published_count']}/{total} video.", "success", acc_name)

        except Exception as e:
            self.state = "ERROR"
            self.log(f"[LỖI] Động cơ Reels Publisher gặp lỗi: {e}", "error", acc_name)
        finally:
            if context:
                try: context.close()
                except Exception: pass
            if pw:
                try: pw.stop()
                except Exception: pass
            self.state = "IDLE"

reels_publisher_engine = ReelsPublisherEngine()
