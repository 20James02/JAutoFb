"""
Multi-Account & Multi-Chrome Bulk Posting Worker Engine for Facebook
Supports:
- Multi-Account concurrency (worker per account)
- Headless mode (run background without opening browser window)
- Individual Chrome profiles per account
- Safe random delay between posts
- ImageFile & ImageFolder extraction
- First comment execution (SellCustomObj)
"""

import os
import sys
import time
import json
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

class ImageFolderPool:
    """
    Quản lý kho ảnh ngẫu nhiên từ thư mục để đảm bảo mỗi lần lấy là các ảnh khác nhau,
    không bị trùng lặp giữa các lần chạy liên tiếp (dùng hàng đợi xáo trộn Shuffled Queue).
    """
    def __init__(self):
        self._pools: Dict[str, List[str]] = {}
        self._lock = threading.Lock()

    def get_random_distinct_images(self, folder_path: str, count: int, valid_exts: set = None) -> List[str]:
        if not folder_path:
            return []
        if valid_exts is None:
            valid_exts = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
        clean_folder = folder_path.strip().strip('"').strip("'")
        p_folder = Path(clean_folder)
        if not p_folder.exists() or not p_folder.is_dir():
            return []

        all_files = [str(f.resolve()) for f in p_folder.iterdir() if f.is_file() and f.suffix.lower() in valid_exts]
        if not all_files:
            return []

        key = str(p_folder.resolve()).lower()
        with self._lock:
            # Nếu chưa có pool hoặc pool còn ít hơn count, nạp lại toàn bộ ảnh và xáo trộn ngẫu nhiên
            if key not in self._pools or len(self._pools[key]) < count:
                shuffled = list(all_files)
                random.shuffle(shuffled)
                self._pools[key] = shuffled

            selected: List[str] = []
            target_count = min(count, len(all_files))
            while len(selected) < target_count and self._pools[key]:
                img = self._pools[key].pop(0)
                if img not in selected:
                    selected.append(img)

            # Nếu trong pool bị thiếu do all_files nhỏ, lấy bổ sung từ all_files không trùng
            if len(selected) < target_count:
                remaining = [f for f in all_files if f not in selected]
                if remaining:
                    needed = target_count - len(selected)
                    selected.extend(random.sample(remaining, min(needed, len(remaining))))

            return selected

image_folder_pool = ImageFolderPool()

class MultiAccountBulkEngine:
    def __init__(self, log_callback: Optional[Callable[..., None]] = None, status_callback: Optional[Callable[..., None]] = None, account_id: Optional[str] = None):
        self.account_id = account_id
        self.log_callback = log_callback
        self.status_callback = status_callback
        self.state = "IDLE"  # IDLE, RUNNING, PAUSED, STOPPED
        self.active_threads: List[threading.Thread] = []
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()

        self.posts: List[Dict[str, Any]] = []
        self.active_file_path = "data/sample_posts.jsonl"
        self.config = {
            "target_type": "personal",
            "role_type": "personal",
            "role_url": None,
            "role_name": None,
            "group_urls": [],
            "page_url": "",
            "min_delay": 30,
            "max_delay": 60,
            "dry_run": False,
            "auto_comment": True,
            "headless": False,
            "concurrency": 1,
            "selected_accounts": [],
            # Tùy chọn mới
            "reaction_enabled": False,
            "reaction_type": "LIKE",
            "location_enabled": False,
            "location_name": "",
            "comment_enabled": True,
            "comment_count": 1,
            "comment_text": "",
            "comment_image": "",
            "loop_groups": False,
            "randomize_groups": False,
            "error_handling": "continue",  # "continue", "stop", "cooldown"
            "error_cooldown": 60
        }
        self.stats = {
            "total": 0,
            "checked": 0,
            "success": 0,
            "failed": 0,
            "current_index": 0,
            "current_id": None,
            "progress_percent": 0,
            "state": "IDLE"
        }
        self.lock = threading.Lock()
        self.load_posts()

    def log(self, message: str, level: str = "info", acc_name: str = ""):
        timestamp = time.strftime("%H:%M:%S")
        prefix = f"[{acc_name}] " if acc_name else ""
        log_line = f"[{timestamp}] {prefix}{message}"
        print(log_line)
        if self.log_callback:
            try:
                self.log_callback(log_line, level, self.account_id)
            except TypeError:
                self.log_callback(log_line, level)

    def update_status(self):
        self.stats["state"] = self.state
        if self.status_callback:
            try:
                self.status_callback(self.stats, self.account_id)
            except TypeError:
                self.status_callback(self.stats)

    def load_posts(self, file_path: Optional[str] = None):
        if file_path:
            self.active_file_path = file_path
        
        p = Path(self.active_file_path)
        if not p.exists():
            self.posts = []
            return

        loaded = []
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        loaded.append(json.loads(line))
                    except Exception:
                        pass
        self.posts = loaded
        self.stats["total"] = len(self.posts)
        self.stats["checked"] = sum(1 for x in self.posts if x.get("Checked", False))
        self.update_status()

    def save_posts(self):
        p = Path(self.active_file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            for item in self.posts:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def resolve_media_files(self, post: Dict[str, Any]) -> List[str]:
        media_list = []
        valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".jfif", ".mp4", ".mov"}

        def _add_valid_file(raw_path: Any):
            if not raw_path:
                return
            p_str = str(raw_path).strip().strip('"').strip("'")
            if not p_str:
                return
            
            p = Path(p_str)
            if p.exists() and p.is_file():
                resolved = str(p.resolve())
                if resolved not in media_list:
                    media_list.append(resolved)
                return
            
            # Check relative to data or uploads
            if p_str.startswith("/uploads/") or p_str.startswith("uploads/"):
                clean = p_str.lstrip("/")
                alt = Path("data") / clean
                if alt.exists() and alt.is_file():
                    resolved = str(alt.resolve())
                    if resolved not in media_list:
                        media_list.append(resolved)
                    return

            if p_str.startswith("/data/"):
                alt = Path(p_str.lstrip("/"))
                if alt.exists() and alt.is_file():
                    resolved = str(alt.resolve())
                    if resolved not in media_list:
                        media_list.append(resolved)
                    return

        raw_images = post.get("Images") or []
        if isinstance(raw_images, list):
            for item in raw_images:
                _add_valid_file(item)

        img_file_str = post.get("ImageFile") or ""
        if img_file_str:
            for item in img_file_str.split(","):
                _add_valid_file(item)

        folder_path = post.get("ImageFolder") or ""
        try:
            folder_count = int(post.get("ImageFolderCount") or 5)
        except (ValueError, TypeError):
            folder_count = 5

        if folder_path:
            # Luôn mặc định lấy random folder_count ảnh khác nhau không trùng lặp qua ImageFolderPool
            selected = image_folder_pool.get_random_distinct_images(folder_path, folder_count, valid_exts)
            for s in selected:
                if s not in media_list:
                    media_list.append(s)

        return media_list

    def start_bulk(self, config: Dict[str, Any], accounts_list: List[Dict[str, Any]]):
        if self.state == "RUNNING":
            self.log("Tiến trình đang chạy!", "warning")
            return

        self.config.update(config)
        self._stop_event.clear()
        self._pause_event.set()
        self.state = "RUNNING"
        self.stats["success"] = 0
        self.stats["failed"] = 0
        self.stats["current_index"] = 0
        self.update_status()

        target_posts = [p for p in self.posts if p.get("Checked", False)]
        if not target_posts:
            self.log("Không có bài viết nào được chọn (Checked = True)!", "error")
            self.state = "IDLE"
            self.update_status()
            return

        sel_acc_ids = self.config.get("selected_accounts", [])
        if sel_acc_ids:
            active_accounts = [a for a in accounts_list if a["id"] in sel_acc_ids]
        else:
            active_accounts = [a for a in accounts_list if a.get("enabled", True)]

        if not active_accounts:
            self.log("Không có tài khoản nào được kích hoạt để đăng bài!", "error")
            self.state = "IDLE"
            self.update_status()
            return

        max_workers = min(int(self.config.get("concurrency", 1)), len(active_accounts))
        workers_accounts = active_accounts[:max_workers]

        # Xáo trộn thứ tự nhóm nếu được cấu hình
        group_urls = list(self.config.get("group_urls", []))
        if group_urls and self.config.get("randomize_groups", False):
            random.shuffle(group_urls)
            self.config["group_urls"] = group_urls
            self.log(f"Đã xáo trộn ngẫu nhiên thứ tự {len(group_urls)} nhóm.", "info")

        self.stats["checked"] = len(target_posts)
        mode_str = "Ẩn danh ngầm (Headless)" if self.config.get("headless", False) else "Mở cửa sổ Chrome"
        self.log(f"Bắt đầu đăng hàng loạt với {len(workers_accounts)} tài khoản chạy đồng thời. Chế độ: {mode_str}.", "info")

        # Phân chia bài viết cho từng worker
        account_queues: Dict[str, List[Dict[str, Any]]] = {a["id"]: [] for a in workers_accounts}
        for i, post in enumerate(target_posts):
            acc = workers_accounts[i % len(workers_accounts)]
            account_queues[acc["id"]].append(post)

        self.active_threads = []
        master_thread = threading.Thread(target=self._master_runner, args=(workers_accounts, account_queues), daemon=True)
        master_thread.start()

    def pause(self):
        if self.state == "RUNNING":
            self.state = "PAUSED"
            self._pause_event.clear()
            self.log("Đã tạm dừng toàn bộ luồng đăng.", "warning")
            self.update_status()

    def resume(self):
        if self.state == "PAUSED":
            self.state = "RUNNING"
            self._pause_event.set()
            self.log("Đã tiếp tục tiến trình đăng.", "info")
            self.update_status()

    def stop(self):
        if self.state in ["RUNNING", "PAUSED"]:
            self.state = "STOPPED"
            self._stop_event.set()
            self._pause_event.set()
            self.log("Đang gửi yêu cầu dừng tất cả các luồng...", "warning")
            self.update_status()

    def _master_runner(self, workers_accounts: List[Dict[str, Any]], account_queues: Dict[str, List[Dict[str, Any]]]):
        worker_threads = []
        for acc in workers_accounts:
            q = account_queues[acc["id"]]
            if not q:
                continue
            t = threading.Thread(target=self._account_worker, args=(acc, q), daemon=True)
            worker_threads.append(t)
            t.start()

        for t in worker_threads:
            t.join()

        self.state = "IDLE"
        self.update_status()
        self.log(f"TẤT CẢ LUỒNG HOÀN THÀNH! Thành công: {self.stats['success']}, Lỗi: {self.stats['failed']}", "success")

    def _account_worker(self, account: Dict[str, Any], post_queue: List[Dict[str, Any]]):
        fb_suffix = f" [{account['fb_name']}]" if account.get("fb_name") else ""
        acc_name = f"{account.get('name', account.get('id'))}{fb_suffix}"
        profile_path = Path(account.get("profile_dir", "profiles/" + account.get("id"))).resolve()
        profile_path.mkdir(parents=True, exist_ok=True)
        headless = self.config.get("headless", False)
        if isinstance(headless, str):
            headless = headless.lower() in ("true", "1", "yes")
        else:
            headless = bool(headless)

        mode_text = "Chạy ngầm (Ẩn Chrome)" if headless else "Mở cửa sổ Chrome"
        self.log(f"Khởi động trình duyệt [{mode_text}]...", "info", acc_name)
        # Đảm bảo dọn sạch tiến trình Chrome treo/zombie đang khóa profile trước khi khởi chạy
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

            # Đảm bảo môi trường runtime window.chrome tự nhiên
            context.add_init_script("""
                window.chrome = window.chrome || { runtime: {} };
            """)

            page = context.pages[0] if context.pages else context.new_page()
            if not headless:
                try:
                    page.bring_to_front()
                except Exception:
                    pass

            # Nạp cookie lưu trữ nếu có để phiên đăng nhập Facebook luôn ổn định
            import re
            safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', profile_path.name)
            cookie_candidates = [
                Path("data") / f"cookies_{safe_name}.txt",
                Path("data/cookies_chrome_fb_profile.txt")
            ]
            for c_path in cookie_candidates:
                if c_path.exists():
                    try:
                        c_str = c_path.read_text(encoding="utf-8").strip()
                        c_list = []
                        for item in c_str.split(';'):
                            if '=' in item:
                                k, v = item.strip().split('=', 1)
                                c_list.append({'name': k.strip(), 'value': v.strip(), 'domain': '.facebook.com', 'path': '/'})
                        if c_list:
                            context.add_cookies(c_list)
                            break
                    except Exception:
                        pass

            # Kiểm tra đăng nhập
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
            time.sleep(2)
            if page.locator('input[name="email"], input[id="email"]').count() > 0:
                self.log("CHƯA ĐĂNG NHẬP FACEBOOK! Vui lòng dùng nút 'Đăng nhập' trên giao diện.", "error", acc_name)
                return

            self.log("Xác nhận đã đăng nhập Facebook. Bắt đầu xử lý bài...", "success", acc_name)

            # Đảm bảo chuyển sang vai trò được chọn (Trang cá nhân hoặc Fanpage)
            role_type = self.config.get("role_type", "personal")
            role_url = self.config.get("role_url")
            role_name = self.config.get("role_name")
            display_role = f"Fanpage '{role_name or role_url}'" if role_type == "page" else f"Trang cá nhân ({account.get('fb_name') or acc_name})"
            self.log(f"Thiết lập vai trò thao tác: {display_role}...", "info", acc_name)
            ensure_role(
                page=page,
                role_type=role_type,
                role_url=role_url,
                role_name=role_name,
                personal_name=account.get("fb_name") or account.get("name"),
                log_fn=lambda m, l: self.log(m, l, acc_name)
            )

            target_type = self.config.get("target_type", "personal")
            group_urls = self.config.get("group_urls", [])
            loop_groups = bool(self.config.get("loop_groups", False))

            for idx, post in enumerate(post_queue):
                if self._stop_event.is_set():
                    self.log("Dừng xử lý theo lệnh người dùng.", "warning", acc_name)
                    break

                self._pause_event.wait()

                # Kiểm tra khung giờ chạy nếu được bật
                if self.config.get("schedule_enabled", False):
                    sched_start = self.config.get("schedule_start", "08:00")
                    sched_end = self.config.get("schedule_end", "22:00")
                    while not self._stop_event.is_set():
                        now_hm = time.strftime("%H:%M")
                        if sched_start <= sched_end:
                            in_window = (sched_start <= now_hm <= sched_end)
                        else:
                            in_window = (now_hm >= sched_start or now_hm <= sched_end)

                        if in_window:
                            break
                        self.log(f"⏰ Hiện tại [{now_hm}] ngoài khung giờ chạy [{sched_start} - {sched_end}]. Tạm nghỉ 30s...", "warning", acc_name)
                        for _ in range(30):
                            if self._stop_event.is_set(): break
                            time.sleep(1)
                    if self._stop_event.is_set():
                        break

                # Nếu là đăng nhóm và không bật lặp nhóm: dừng khi đã đăng hết danh sách nhóm
                if target_type == "group" and group_urls:
                    if not loop_groups and idx >= len(group_urls):
                        self.log(f"Đã đăng đủ {len(group_urls)} nhóm trong danh sách và cấu hình [Không lặp lại nhóm]. Hoàn thành sớm.", "info", acc_name)
                        break

                with self.lock:
                    self.stats["current_index"] += 1
                    self.stats["current_id"] = post.get("Id")
                    total_checked = max(1, self.stats["checked"])
                    self.stats["progress_percent"] = min(100, int((self.stats["current_index"] / total_checked) * 100))
                    self.update_status()

                # Kiểm tra phanh an toàn Meta Sentinel
                acc_key = account.get("id") or self.account_id or acc_name
                is_tripped, trip_reason = meta_sentinel.is_circuit_tripped(acc_key)
                if is_tripped:
                    self.log(f"🚨 PHANH AN TOÀN KÍCH HOẠT: Dừng tài khoản '{acc_name}' để bảo vệ nick ({trip_reason})!", "error", acc_name)
                    break

                min_d = int(self.config.get("min_delay", 30))
                risk_info = meta_sentinel.record_action_start(acc_key, "groups_post", content=post.get("PostContent", ""), delay_seconds=min_d, account_name=acc_name)
                if risk_info.get("risk_score", 0) >= 70:
                    self.log(f"⚠️ Radar Meta: Rủi ro {risk_info['risk_score']}%. {', '.join(risk_info.get('risk_factors', []))}", "warning", acc_name)

                self.log(f"Đang đăng bài ID #{post.get('Id')} ({idx + 1}/{len(post_queue)} của tài khoản này)...", "info", acc_name)
                success = self._post_single(page, post, acc_name, group_index=idx, acc_id=acc_key)

                # Ghi nhận kết quả vào Meta Sentinel
                meta_sentinel.record_action_result(acc_key, "groups_post", success=success, error_msg="" if success else "Lỗi đăng bài", account_name=acc_name)

                with self.lock:
                    if success:
                        self.stats["success"] += 1
                        post["_status"] = "Thành công"
                    else:
                        self.stats["failed"] += 1
                        post["_status"] = "Lỗi"
                    self.update_status()

                # Xử lý khi gặp lỗi
                if not success:
                    err_handling = self.config.get("error_handling", "continue")
                    if err_handling == "stop":
                        self.log("⚠️ Gặp lỗi đăng bài! DỪNG HẲN tiến trình theo cấu hình cài đặt.", "error", acc_name)
                        self._stop_event.set()
                        break
                    elif err_handling == "cooldown":
                        cooldown_sec = int(self.config.get("error_cooldown", 60))
                        self.log(f"⚠️ Gặp lỗi đăng bài! Tạm nghỉ an toàn {cooldown_sec}s trước bài tiếp theo...", "warning", acc_name)
                        for rem in range(cooldown_sec, 0, -1):
                            if self._stop_event.is_set(): break
                            self._pause_event.wait()
                            time.sleep(1)
                        continue
                    else:
                        # Tạm nghỉ ngắn an toàn khi bỏ qua bài lỗi
                        time.sleep(random.uniform(4, 7))
                        continue

                # Khi đăng bài thành công: thực hiện tương tác bài vừa đăng và lướt nuôi nhóm trong suốt thời gian giãn cách
                is_last = (idx == len(post_queue) - 1)
                self._handle_post_cooldown_and_interactions(page, post, acc_name, is_last=is_last)

        except Exception as ex:
            self.log(f"Lỗi luồng tài khoản: {ex}", "error", acc_name)
        finally:
            if context:
                try: context.close()
                except Exception: pass
            if pw:
                try: pw.stop()
                except Exception: pass
            self.log("Đã đóng luồng tài khoản an toàn.", "info", acc_name)

    def _human_scroll_read(self, page, min_scrolls: int = 2, max_scrolls: int = 4):
        """Mô phỏng người thật lướt xem bảng tin hoặc nhóm trước khi tạo bài"""
        try:
            num_scrolls = random.randint(min_scrolls, max_scrolls)
            for _ in range(num_scrolls):
                if self._stop_event.is_set(): break
                px = random.randint(180, 420)
                page.evaluate(f"window.scrollBy({{ top: {px}, behavior: 'smooth' }})")
                time.sleep(random.uniform(1.2, 2.4))
            # Cuộn nhẹ ngược lên lại khu vực soạn bài
            page.evaluate("window.scrollTo({ top: 0, behavior: 'smooth' })")
            time.sleep(random.uniform(1.0, 1.8))
        except Exception:
            pass

    def _human_click(self, page, locator) -> bool:
        """Di chuyển chuột mô phỏng (smooth mouse move) đến phần tử rồi click"""
        try:
            locator.scroll_into_view_if_needed(timeout=3000)
            time.sleep(random.uniform(0.1, 0.2))
            box = locator.bounding_box()
            if box:
                target_x = box["x"] + random.uniform(box["width"] * 0.25, box["width"] * 0.75)
                target_y = box["y"] + random.uniform(box["height"] * 0.25, box["height"] * 0.75)
                vp = page.viewport_size or {"width": 1280, "height": 800}
                if 0 <= target_x <= vp["width"] and 0 <= target_y <= vp["height"]:
                    steps = random.randint(6, 12)
                    page.mouse.move(target_x, target_y, steps=steps)
                    time.sleep(random.uniform(0.1, 0.25))
            locator.click(delay=random.randint(50, 120))
            return True
        except Exception:
            try:
                locator.click(force=True)
                return True
            except Exception:
                return False

    def _human_type(self, page, text: str, newline_key: str = "Shift+Enter"):
        """Gõ văn bản mô phỏng tốc độ gõ phím con người có độ trễ ngẫu nhiên và ngắt nghỉ tự nhiên,
        bảo toàn tuyệt đối định dạng xuống dòng, khoảng trắng, số điện thoại và emoji cho Facebook Lexical editor"""
        if not text:
            return
        try:
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            lines = normalized.split("\n")
            
            for i, line in enumerate(lines):
                if self._stop_event.is_set():
                    break
                
                # Gõ từng từ trong dòng (đảm bảo không chứa \n để tránh bị Lexical editor nuốt chữ)
                if line:
                    words = line.split(" ")
                    for j, word in enumerate(words):
                        if self._stop_event.is_set():
                            break
                        page.keyboard.insert_text(word + (" " if j < len(words) - 1 else ""))
                        if any(p in word for p in [".", ",", "!", "?", ":", ";"]):
                            time.sleep(random.uniform(0.1, 0.25))
                        else:
                            time.sleep(random.uniform(0.02, 0.05))
                
                # Tạo ngắt dòng an toàn giữa các dòng
                if i < len(lines) - 1:
                    time.sleep(random.uniform(0.1, 0.2))
                    page.keyboard.press(newline_key)
                    time.sleep(random.uniform(0.1, 0.2))
        except Exception:
            try:
                normalized = text.replace("\r\n", "\n").replace("\r", "\n")
                lines = normalized.split("\n")
                for i, line in enumerate(lines):
                    if line:
                        page.keyboard.insert_text(line)
                    if i < len(lines) - 1:
                        page.keyboard.press(newline_key)
            except Exception:
                page.keyboard.insert_text(text.replace("\n", " "))

    def _post_single(self, page, post: Dict[str, Any], acc_name: str, group_index: int = 0, acc_id: str = "") -> bool:
        try:
            target_type = self.config.get("target_type", "personal")
            target_url = None

            if target_type == "group":
                group_urls = self.config.get("group_urls", [])
                if group_urls:
                    target_url = group_urls[group_index % len(group_urls)]
            elif target_type == "page":
                target_url = self.config.get("page_url")

            dialog = self._open_modal(page, target_type, target_url, acc_name)
            if not dialog:
                return False

            content = post.get("PostContent", "")
            if content:
                editor = dialog.locator('div[role="textbox"][contenteditable="true"], div[role="textbox"]').first
                editor.wait_for(state="visible", timeout=7000)
                self._human_click(page, editor)
                time.sleep(random.uniform(0.6, 1.2))
                self.log(f"Đang nhập nội dung bài viết (mô phỏng gõ phím tự nhiên)...", "info", acc_name)
                self._human_type(page, content)
                # Dừng ngắm bài như người thật
                time.sleep(random.uniform(1.2, 2.5))

            # Gắn vị trí (Check-in) nếu được cấu hình
            if self.config.get("location_enabled", False):
                loc_name = (self.config.get("location_name") or "").strip()
                if loc_name:
                    self._handle_location(page, dialog, loc_name, acc_name)

            media_files = self.resolve_media_files(post)
            if media_files:
                self.log(f"Đính kèm {len(media_files)} ảnh/video...", "info", acc_name)
                self._upload_files(page, dialog, media_files)
                time.sleep(random.uniform(2.0, 3.5))

            if self.config.get("dry_run", False):
                self.log("[DRY-RUN] Chế độ xem trước: Bỏ qua bấm Đăng!", "warning", acc_name)
                time.sleep(2)
                try:
                    close_btn = dialog.locator('div[aria-label="Đóng"], div[aria-label="Close"]').first
                    if close_btn.is_visible():
                        self._human_click(page, close_btn)
                except Exception: pass
                return True

            post_btn_selectors = [
                'div[aria-label="Đăng"]', 'div[aria-label="Post"]',
                'div[role="button"]:has-text("Đăng")', 'div[role="button"]:has-text("Post")',
                'div[aria-label="Tiếp"]', 'div[role="button"]:has-text("Tiếp")',
                'div[role="button"]:has-text("Chia sẻ")'
            ]
            post_btn = None
            for s in post_btn_selectors:
                try:
                    btn = dialog.locator(s).first
                    if btn.is_visible(timeout=2000):
                        post_btn = btn
                        break
                except Exception: pass

            if not post_btn:
                self.log("Không tìm thấy nút 'Đăng'!", "error", acc_name)
                return False

            time.sleep(random.uniform(1.0, 1.8))
            self._human_click(page, post_btn)
            self.log("Đã nhấn nút ĐĂNG BÀI!", "info", acc_name)

            # Xử lý nếu bấm "Tiếp" hiện thêm bước "Chia sẻ" / "Đăng"
            try:
                time.sleep(1.8)
                sub_post = page.locator('div[role="dialog"] div[aria-label="Đăng"], div[role="dialog"] div[role="button"]:has-text("Đăng"), div[role="dialog"] div[role="button"]:has-text("Chia sẻ")').first
                if sub_post.is_visible(timeout=2000):
                    self._human_click(page, sub_post)
                    self.log("Đã nhấn xác nhận Chia sẻ/Đăng bài bước 2!", "info", acc_name)
            except Exception: pass

            try:
                dialog.wait_for(state="hidden", timeout=25000)
                self.log(f"Bài ID #{post.get('Id')} ĐÃ ĐĂNG THÀNH CÔNG!", "success", acc_name)
            except PlaywrightTimeoutError:
                self.log("Dialog đóng quá 25s, tiếp tục...", "warning", acc_name)

            # Kiểm tra trạng thái duyệt bài & trích xuất URL bài vừa đăng
            post_status = "approved"
            pending_keywords = [
                "chờ phê duyệt", "chờ quản trị viên", "đang chờ duyệt",
                "pending approval", "đã gửi và đang chờ", "submitted for approval"
            ]
            time.sleep(2.0)
            try:
                alert_text = page.locator('div[role="alert"], div[role="status"]').all_inner_texts()
                alert_combined = " ".join(alert_text).lower()
                if any(kw in alert_combined for kw in pending_keywords):
                    post_status = "pending"
            except Exception: pass

            if post_status == "approved" and target_type == "group":
                try:
                    banner = page.locator('text="đang chờ phê duyệt", text="chờ phê duyệt", text="chờ quản trị viên duyệt"').first
                    if banner.is_visible(timeout=1000):
                        post_status = "pending"
                except Exception: pass

            # Trích xuất permalink URL bài viết vừa đăng
            published_post_url = ""
            try:
                top_article = page.locator('div[role="feed"] div[role="article"], div[role="article"]').first
                if top_article.is_visible(timeout=2500):
                    links = top_article.locator('a[href*="/posts/"], a[href*="/permalink/"], a[href*="story_fbid="], a[href*="/videos/"]').all()
                    for lk in links:
                        href = lk.get_attribute("href")
                        if href and ("/posts/" in href or "/permalink/" in href or "story_fbid=" in href):
                            if href.startswith("/"):
                                href = "https://www.facebook.com" + href
                            clean_href = href.split("?")[0] if "?" in href and "story_fbid" not in href else href
                            published_post_url = clean_href
                            break
            except Exception: pass

            if not published_post_url:
                published_post_url = target_url or ""

            # Lấy tên nhóm / Fanpage / Tường
            target_name = ""
            if target_type == "group":
                try:
                    h1 = page.locator('h1').first
                    if h1.is_visible(timeout=1000):
                        target_name = h1.inner_text().strip()
                except Exception: pass
                if not target_name:
                    target_name = "Nhóm Facebook"
            elif target_type == "page":
                target_name = self.config.get("role_name") or "Fanpage"
            else:
                target_name = "Trang cá nhân"

            post["_target_name"] = target_name

            # Lưu vào Lịch sử bài đã đăng (Published Posts Manager)
            try:
                from published_posts_manager import published_posts_mgr
                status_vn = "ĐÃ DUYỆT" if post_status == "approved" else "CHỜ DUYỆT"
                post_title_preview = (post.get("PostContent") or post.get("Content") or "")[:120]
                published_posts_mgr.add_post({
                    "post_url": published_post_url,
                    "post_id": post.get("Id"),
                    "post_title": post_title_preview,
                    "target_type": target_type,
                    "target_name": target_name,
                    "target_url": target_url or "",
                    "account_id": self.account_id or acc_id,
                    "account_name": acc_name,
                    "role_type": self.config.get("role_type", "personal"),
                    "role_name": self.config.get("role_name") or acc_name,
                    "status": post_status,
                })
                self.log(f"Đã lưu bài viết vào Lịch sử bài đăng: [{status_vn}] - Nơi đăng: '{target_name}'", "info", acc_name)
            except Exception as save_err:
                self.log(f"Lưu lịch sử bài đăng thất bại: {save_err}", "warning", acc_name)

            # Giám sát phản ứng của Meta trên DOM & URL
            incident = meta_sentinel.inspect_page(page, acc_id or acc_name, "groups_post", account_name=acc_name, context_info=f"Đăng bài ID #{post.get('Id')}")
            if incident:
                self.log(f"🚨 Phát hiện phản ứng Meta: {incident.get('error_text')}", "error", acc_name)
                return False

            return True

        except Exception as ex:
            self.log(f"Lỗi đăng bài #{post.get('Id')}: {ex}", "error", acc_name)
            return False

    def _open_modal(self, page, target_type, target_url, acc_name):
        try:
            if target_type == "personal":
                page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
                time.sleep(2)
                self._human_scroll_read(page, 1, 2)
                triggers = [
                    'div[role="button"]:has-text("Bạn đang nghĩ gì")',
                    'div[role="button"]:has-text("What\'s on your mind")',
                    'div[role="button"]:has-text("Tạo bài viết")',
                    'span:has-text("Bạn đang nghĩ gì")',
                    'span:has-text("What\'s on your mind")',
                    'span:has-text("Tạo bài viết")',
                ]
                for s in triggers:
                    try:
                        el = page.locator(s).first
                        if el.is_visible(timeout=1500):
                            self._human_click(page, el)
                            break
                    except Exception: pass

            elif target_type == "group":
                if not target_url:
                    self.log("Chưa có URL Nhóm!", "error", acc_name)
                    return None
                if not target_url.startswith("http"):
                    target_url = f"https://www.facebook.com/groups/{target_url}"
                page.goto(target_url, wait_until="domcontentloaded")
                time.sleep(2.5)
                
                # Mô phỏng lướt đọc bài nhóm tự nhiên
                self.log("Lướt xem nội dung nhóm tự nhiên...", "info", acc_name)
                self._human_scroll_read(page, 2, 3)

                # Kiểm tra nếu nhóm cấm Page tham gia và yêu cầu chuyển sang trang cá nhân
                try:
                    page_restrict = page.locator('text="Nhóm này không cho phép các trang tham gia", text="Hãy chuyển sang trang cá nhân chính"').first
                    if page_restrict.is_visible(timeout=1000):
                        self.log("Nhóm chỉ cho phép Trang cá nhân (không cho Fanpage). Đang tự động chuyển sang Trang cá nhân...", "warning", acc_name)
                        ensure_role(page, role_type="personal", personal_name="Dương Lê", log_fn=lambda m, l: self.log(m, l, acc_name))
                        page.goto(target_url, wait_until="domcontentloaded")
                        time.sleep(3)
                except Exception: pass

                triggers = [
                    'div[role="button"]:has-text("Bạn viết gì đi")',
                    'div[role="button"]:has-text("Tạo bài viết công khai")',
                    'div[role="button"]:has-text("Tạo bài viết")',
                    'div[role="button"]:has-text("Viết gì đó")',
                    'div[role="button"]:has-text("Write something")',
                    'div[role="button"]:has-text("Create a public post")',
                    'div[role="button"]:has-text("Create post")',
                    'span:has-text("Bạn viết gì đi")',
                    'span:has-text("Tạo bài viết công khai")',
                    'span:has-text("Tạo bài viết")',
                    'span:has-text("Viết gì đó")',
                    'span:has-text("Write something")',
                    'span:has-text("Create a public post")',
                    'span:has-text("Create post")'
                ]
                clicked = False
                for s in triggers:
                    try:
                        el = page.locator(s).first
                        if el.is_visible(timeout=1500):
                            clicked = self._human_click(page, el)
                            if clicked: break
                    except Exception: pass

            elif target_type == "page":
                if not target_url:
                    self.log("Chưa có URL Fanpage!", "error", acc_name)
                    return None
                page.goto(target_url, wait_until="domcontentloaded")
                time.sleep(2.5)

                # Tự động đóng popup chào mừng "Dùng Trang" nếu có
                try:
                    use_page_btn = page.locator('div[aria-label="Dùng Trang"], div[role="button"]:has-text("Dùng Trang"), button:has-text("Dùng Trang")').first
                    if use_page_btn.is_visible(timeout=1500):
                        use_page_btn.click()
                        time.sleep(2)
                except Exception: pass
                
                # Mô phỏng lướt Fanpage tự nhiên
                self.log("Lướt xem Fanpage tự nhiên...", "info", acc_name)
                self._human_scroll_read(page, 2, 3)

                triggers = [
                    'div[role="button"]:has-text("Bạn đang nghĩ gì")',
                    'div[role="button"]:has-text("Tạo bài viết")',
                    'div[role="button"]:has-text("Create post")',
                    'div[role="button"]:has-text("Viết gì đó")',
                    'div[role="button"]:has-text("Write something")',
                    'span:has-text("Bạn đang nghĩ gì")',
                    'span:has-text("Tạo bài viết")',
                    'span:has-text("Create post")',
                ]
                for s in triggers:
                    try:
                        el = page.locator(s).first
                        if el.is_visible(timeout=1500):
                            self._human_click(page, el)
                            break
                    except Exception: pass

            # Đợi và nhận diện hộp thoại (soạn bài hoặc cảnh báo bảo mật từ Meta)
            dialog = None
            compose_selectors = [
                'div[role="dialog"]:has(div[role="textbox"])',
                'div[role="dialog"]:has-text("Tạo bài viết")',
                'div[role="dialog"]:has-text("Create post")',
                'div[role="dialog"]:has-text("Create a public post")',
                'div[role="dialog"]:has-text("Tạo bài viết công khai")'
            ]
            checkpoint_keywords = [
                "xác nhận danh tính",
                "hoạt động bất thường",
                "bị hạn chế",
                "thiết bị di động",
                "confirm your identity",
                "unusual activity"
            ]

            start_wait = time.time()
            while time.time() - start_wait < 8.0:
                if self._stop_event.is_set(): return None

                # 1. Quét cảnh báo bảo mật/xác minh danh tính
                for kw in checkpoint_keywords:
                    try:
                        cp = page.locator(f'div[role="dialog"]:has-text("{kw}")').first
                        if cp.is_visible():
                            self.log(
                                f"⚠️ CẢNH BÁO FACEBOOK: Phát hiện thông báo ({kw})! Vui lòng mở Facebook trên điện thoại bấm OK / 'Đây là tôi' để xác nhận danh tính.",
                                "warning",
                                acc_name
                            )
                            try:
                                dismiss_btn = cp.locator('div[role="button"]:has-text("OK"), div[aria-label="OK"], div[role="button"]:has-text("Đóng"), div[aria-label="Đóng"], div[aria-label="Close"]').first
                                if dismiss_btn.is_visible():
                                    dismiss_btn.click()
                            except Exception: pass
                            return None
                    except Exception: pass

                # 2. Kiểm tra hộp thoại soạn bài
                for sel in compose_selectors:
                    try:
                        d = page.locator(sel).first
                        if d.is_visible():
                            dialog = d
                            break
                    except Exception: pass

                if dialog:
                    break
                time.sleep(0.5)

            if not dialog:
                self.log("Không thể mở hộp thoại soạn bài: Không tìm thấy khung soạn thảo hoặc bài viết bị kiểm duyệt.", "error", acc_name)
                return None

            return dialog
        except Exception as e:
            self.log(f"Không thể mở hộp thoại soạn bài: {e}", "error", acc_name)
            return None

    def _upload_files(self, page, dialog, media_files):
        try:
            file_inputs = dialog.locator('input[type="file"]')
            if file_inputs.count() == 0:
                photo_btn = dialog.locator('div[aria-label="Ảnh/video"], div[aria-label="Photo/video"]').first
                if photo_btn.is_visible(timeout=2000):
                    photo_btn.click()
                    time.sleep(1.5)

            file_input = dialog.locator('input[type="file"]').first
            file_input.wait_for(state="attached", timeout=8000)
            file_input.set_input_files(media_files)
            time.sleep(3)
        except Exception as e:
            pass

    def _handle_location(self, page, dialog, location_name: str, acc_name: str):
        if not location_name:
            return
        try:
            self.log(f"Đang gắn vị trí (Check-in): '{location_name}'...", "info", acc_name)
            loc_btn_selectors = [
                'div[aria-label="Check in"]',
                'div[aria-label="Vào"]',
                'div[aria-label="Vị trí"]',
                'div[aria-label="Thêm vị trí"]',
                'div[role="button"]:has-text("Check in")',
                'div[role="button"]:has-text("Vị trí")'
            ]
            loc_btn = None
            for s in loc_btn_selectors:
                try:
                    b = dialog.locator(s).first
                    if b.is_visible(timeout=1000):
                        loc_btn = b
                        break
                except Exception:
                    pass

            if not loc_btn:
                # Thử mở rộng menu "Xem thêm vào bài viết của bạn" (...)
                try:
                    more_btn = dialog.locator('div[aria-label="Xem thêm vào bài viết của bạn"], div[aria-label="More"], div[aria-label="Xem thêm"]').first
                    if more_btn.is_visible(timeout=1000):
                        more_btn.click()
                        time.sleep(1)
                        for s in loc_btn_selectors:
                            b = dialog.locator(s).first
                            if b.is_visible(timeout=1000):
                                loc_btn = b
                                break
                except Exception:
                    pass

            if loc_btn:
                self._human_click(page, loc_btn)
                time.sleep(1.5)

                search_selectors = [
                    'input[placeholder*="ở đâu"]',
                    'input[placeholder*="Where are you"]',
                    'input[aria-label*="ở đâu"]',
                    'input[aria-label*="Where are you"]',
                    'div[role="dialog"] input[type="text"]'
                ]
                search_input = None
                for s in search_selectors:
                    try:
                        inp = page.locator(s).first
                        if inp.is_visible(timeout=2000):
                            search_input = inp
                            break
                    except Exception:
                        pass

                if search_input:
                    search_input.click()
                    time.sleep(0.5)
                    self._human_type(page, location_name)
                    time.sleep(2.0)

                    result_item = page.locator('div[role="listbox"] div[role="option"], div[role="dialog"] div[role="button"]:has(span), div[role="dialog"] ul li div[role="button"]').first
                    if result_item.is_visible(timeout=3000):
                        self._human_click(page, result_item)
                        time.sleep(1.0)
                        self.log(f"Đã gắn vị trí '{location_name}' vào bài viết!", "success", acc_name)
                    else:
                        page.keyboard.press("Enter")
                        time.sleep(1.0)
                        self.log(f"Đã nhập vị trí '{location_name}'.", "info", acc_name)
                else:
                    self.log("Không tìm thấy ô tìm kiếm vị trí.", "warning", acc_name)
            else:
                self.log("Không tìm thấy nút Check-in vị trí trong hộp thoại.", "warning", acc_name)
        except Exception as e:
            self.log(f"Lỗi khi gắn vị trí: {e}", "warning", acc_name)

    def _surf_group_during_cooldown(self, page, acc_name: str, target_name: str, duration_seconds: float, like_other: bool = False):
        """Lướt xem nội dung trong nhóm, đọc bài viết và tương tác nhẹ trong suốt thời gian chờ"""
        deadline = time.time() + duration_seconds
        liked_other_count = 0
        last_log_time = 0

        self.log(f"Bắt đầu lướt tương tác nhóm '{target_name}' trong thời gian chờ ({int(duration_seconds)}s)...", "info", acc_name)

        while time.time() < deadline:
            if self._stop_event.is_set():
                break
            self._pause_event.wait()

            rem = int(deadline - time.time())
            if rem <= 1:
                break

            # Log định kỳ mỗi 15s để người dùng thấy rõ tiến trình đếm ngược thời gian
            now = time.time()
            if now - last_log_time >= 15 or last_log_time == 0:
                self.log(f"⏳ Giãn cách an toàn: Còn {rem}s | Đang lướt xem nhóm '{target_name}'...", "info", acc_name)
                last_log_time = now

            # 1. Cuộn chuột xuống mượt mà mô phỏng người thật đọc bảng tin
            scroll_px = random.randint(220, 480)
            try:
                page.evaluate(f"window.scrollBy({{ top: {scroll_px}, behavior: 'smooth' }})")
            except Exception:
                pass

            # 2. Dừng lại đọc bài viết (ngẫu nhiên 2.5 - 4.5s)
            read_time = min(rem, random.uniform(2.5, 4.5))
            step_end = time.time() + read_time
            while time.time() < step_end:
                if self._stop_event.is_set():
                    break
                time.sleep(0.5)

            if time.time() >= deadline or self._stop_event.is_set():
                break

            # 3. Like dạo 1 bài viết khác trong nhóm (nếu được bật và chưa like quá 1 bài)
            if like_other and liked_other_count < 1 and (deadline - time.time()) > 8:
                try:
                    feed_articles = page.locator('div[role="feed"] div[role="article"]').all()
                    if len(feed_articles) > 1:
                        target_art = feed_articles[random.randint(1, min(3, len(feed_articles) - 1))]
                        other_like_btn = target_art.locator('div[role="button"][aria-label*="Thích"], div[role="button"][aria-label*="Like"]').first
                        if other_like_btn.is_visible(timeout=1500):
                            self._human_click(page, other_like_btn)
                            liked_other_count += 1
                            self.log(f"👍 Đã thả like tương tác 1 bài viết của thành viên khác trong nhóm '{target_name}'!", "success", acc_name)
                            time.sleep(random.uniform(1.5, 2.5))
                except Exception:
                    pass

            # 4. Thỉnh thoảng cuộn nhẹ ngược lên một chút (mô phỏng mắt lướt lại bài cũ)
            if random.random() < 0.25 and (deadline - time.time()) > 4:
                try:
                    up_px = random.randint(100, 200)
                    page.evaluate(f"window.scrollBy({{ top: -{up_px}, behavior: 'smooth' }})")
                    time.sleep(random.uniform(1.0, 1.8))
                except Exception:
                    pass

        self.log(f"⏱️ Đã hoàn thành thời gian giãn cách nhóm '{target_name}'. Chuyển sang bài/nhóm tiếp theo!", "info", acc_name)

    def _handle_post_cooldown_and_interactions(self, page, post: Dict[str, Any], acc_name: str, is_last: bool = False):
        """Xử lý tương tác bài vừa đăng và duy trì lướt tương tác nhóm trong suốt thời gian giãn cách"""
        post_finish_time = time.time()

        # 1. Tính toán thời gian giãn cách nếu còn bài tiếp theo
        if not is_last and not self._stop_event.is_set():
            min_d = int(self.config.get("min_delay", 30))
            max_d = int(self.config.get("max_delay", 60))
            cooldown_seconds = random.randint(min_d, max(min_d, max_d))
            deadline = post_finish_time + cooldown_seconds
            self.log(f"⏱️ Bắt đầu thời gian giãn cách {cooldown_seconds}s (tính từ lúc đăng thành công). Bắt đầu tương tác nhóm...", "info", acc_name)
        else:
            cooldown_seconds = 0
            deadline = 0

        # 2. Thả cảm xúc vào bài viết vừa đăng nếu được bật
        if self.config.get("reaction_enabled", False) and not self._stop_event.is_set():
            self._pause_event.wait()
            react_type = self.config.get("reaction_type", "LIKE")
            time.sleep(random.uniform(2.0, 3.5))
            self._handle_reaction(page, react_type, acc_name)

        # 3. Tự động bình luận vào bài viết vừa đăng nếu được bật
        if (self.config.get("auto_comment", False) or self.config.get("comment_enabled", False)) and not self._stop_event.is_set():
            self._pause_event.wait()
            time.sleep(random.uniform(2.5, 4.0))
            self._handle_auto_comment(page, post, acc_name)

        # 4. Nếu là bài cuối cùng: lướt nhẹ một chút rồi kết thúc
        if is_last:
            if self.config.get("interact_during_cooldown", True) and not self._stop_event.is_set():
                self._human_scroll_read(page, 1, 2)
            return

        # 5. Lướt tương tác nhóm trong khoảng thời gian chờ còn lại
        rem_time = deadline - time.time()
        if rem_time > 1 and not self._stop_event.is_set():
            target_name = post.get("_target_name") or "Nhóm Facebook"
            interact_enabled = self.config.get("interact_during_cooldown", True)
            like_other = self.config.get("like_other_posts", False)

            if interact_enabled:
                self._surf_group_during_cooldown(page, acc_name, target_name, rem_time, like_other=like_other)
            else:
                self.log(f"Nghỉ giãn cách {int(rem_time)}s trước bài tiếp theo...", "info", acc_name)
                for _ in range(int(rem_time)):
                    if self._stop_event.is_set(): break
                    self._pause_event.wait()
                    time.sleep(1)

    def _handle_reaction(self, page, reaction_type: str, acc_name: str):
        try:
            reaction_type = (reaction_type or "LIKE").upper()
            time.sleep(1.5)

            # Cuộn nhẹ lên đầu để đảm bảo bài viết vừa đăng nằm ngay trong tầm nhìn
            try:
                page.evaluate("window.scrollTo({ top: 0, behavior: 'smooth' })")
                time.sleep(0.8)
            except Exception:
                pass

            like_selectors = [
                'div[role="feed"] div[role="article"] div[role="button"][aria-label*="Thích"]',
                'div[role="feed"] div[role="article"] div[role="button"][aria-label*="Like"]',
                'div[role="article"] div[role="button"][aria-label*="Thích"]',
                'div[role="article"] div[role="button"][aria-label*="Like"]',
                'div[role="button"][aria-label*="Thích"]',
                'div[role="button"][aria-label*="Like"]',
                'div[role="button"]:has-text("Thích")',
                'div[role="button"]:has-text("Like")'
            ]
            like_btn = None
            for s in like_selectors:
                try:
                    btn = page.locator(s).first
                    if btn.is_visible(timeout=2000):
                        like_btn = btn
                        break
                except Exception:
                    pass

            if not like_btn:
                self.log("Không tìm thấy nút Thích của bài viết để thả cảm xúc.", "warning", acc_name)
                return

            if reaction_type == "LIKE":
                self._human_click(page, like_btn)
                time.sleep(1)
                self.log("Đã thả cảm xúc [Thích] vào bài viết thành công!", "success", acc_name)
                return

            try:
                like_btn.hover()
                time.sleep(1.2)
            except Exception:
                pass

            reaction_labels = {
                "LOVE": ["Yêu thích", "Love"],
                "CARE": ["Thương thương", "Care"],
                "HAHA": ["Haha"],
                "WOW": ["Wow"],
                "SAD": ["Buồn", "Sad"],
                "ANGRY": ["Phẫn nộ", "Angry"]
            }
            labels = reaction_labels.get(reaction_type, ["Yêu thích"])
            clicked_react = False
            for lbl in labels:
                try:
                    react_el = page.locator(f'div[aria-label="{lbl}"], div[role="button"][aria-label*="{lbl}"]').first
                    if react_el.is_visible(timeout=1500):
                        self._human_click(page, react_el)
                        clicked_react = True
                        break
                except Exception:
                    pass

            if clicked_react:
                self.log(f"Đã thả cảm xúc [{reaction_type}] vào bài viết thành công!", "success", acc_name)
            else:
                self._human_click(page, like_btn)
                self.log(f"Không mở được menu cảm xúc, đã thả cảm xúc [Thích] mặc định.", "info", acc_name)
            time.sleep(1.0)
        except Exception as e:
            self.log(f"Lỗi thả cảm xúc: {e}", "warning", acc_name)

    def _handle_auto_comment(self, page, post: Dict[str, Any], acc_name: str):
        try:
            count = max(1, int(self.config.get("comment_count", 1)))
            raw_text = (self.config.get("comment_text") or "").strip()
            comment_image = (self.config.get("comment_image") or "").strip()
            comment_image_mode = self.config.get("comment_image_mode", "single")  # single, folder_each_comment, folder_separate_images
            comment_attach_text = self.config.get("comment_attach_text", True)

            # Nếu không có nội dung từ cấu hình, thử lấy từ SellCustomObj của bài viết
            if not raw_text and not comment_image:
                custom_str = post.get("SellCustomObj")
                if custom_str:
                    try:
                        custom_data = json.loads(custom_str) if isinstance(custom_str, str) else custom_str
                        if custom_data.get("CommentPosted", False):
                            raw_text = custom_data.get("Comment1") or custom_data.get("Comment2") or ""
                    except Exception:
                        pass

            if not raw_text and not comment_image:
                return

            # Kiểm tra xem comment_image là file đơn hay thư mục ảnh
            folder_images = []
            if comment_image:
                c_path = Path(comment_image).resolve()
                if c_path.exists() and c_path.is_dir():
                    valid_img_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
                    # Sử dụng ImageFolderPool để lấy random các ảnh khác nhau không trùng lặp
                    folder_images = image_folder_pool.get_random_distinct_images(comment_image, max(count, 10), valid_img_exts)
                    if folder_images:
                        self.log(f"Đã nạp {len(folder_images)} ảnh ngẫu nhiên từ thư mục bình luận: '{c_path.name}'.", "info", acc_name)
                    else:
                        self.log(f"Thư mục '{c_path.name}' không chứa file ảnh hợp lệ (.png, .jpg, .webp)!", "warning", acc_name)

            # Tách Spintax bằng dấu |
            spintax_parts = [p.strip() for p in raw_text.split('|') if p.strip()]

            # Xác định tổng số comment thực tế
            if folder_images and comment_image_mode == "folder_separate_images":
                # Bình luận riêng từng ảnh trong folder (mỗi ảnh là 1 bình luận độc lập)
                total_comments = len(folder_images) if count <= 1 else min(count, len(folder_images))
            else:
                total_comments = count

            self.log(f"Bắt đầu tự động bình luận ({total_comments} bình luận, chế độ ảnh: {comment_image_mode})...", "info", acc_name)
            time.sleep(2.5)

            for c_idx in range(total_comments):
                if self._stop_event.is_set():
                    break

                # Xác định ảnh cho lượt comment này (lấy ngẫu nhiên tuần tự không trùng lặp)
                img_for_this_comment = None
                if folder_images:
                    img_for_this_comment = folder_images[c_idx % len(folder_images)]
                elif comment_image and Path(comment_image).is_file():
                    img_for_this_comment = str(Path(comment_image).resolve())

                # Xác định nội dung text
                chosen_text = ""
                if comment_attach_text or not img_for_this_comment:
                    chosen_text = random.choice(spintax_parts) if spintax_parts else raw_text

                comment_box_selectors = [
                    'div[role="textbox"][aria-label*="bình luận"]',
                    'div[role="textbox"][aria-label*="comment"]',
                    'div[role="textbox"][aria-label*="Viết câu trả lời"]',
                    'div[role="textbox"][aria-label*="Write a comment"]'
                ]
                comment_box = None
                for s in comment_box_selectors:
                    try:
                        cb = page.locator(s).first
                        if cb.is_visible(timeout=3500):
                            comment_box = cb
                            break
                    except Exception:
                        pass

                if not comment_box:
                    self.log(f"Không tìm thấy ô bình luận (#{c_idx+1}/{total_comments})!", "warning", acc_name)
                    break

                self._human_click(page, comment_box)
                time.sleep(0.6)

                # Đính kèm ảnh nếu có
                if img_for_this_comment and Path(img_for_this_comment).exists():
                    try:
                        file_input = page.locator('div[role="presentation"] input[type="file"], form input[type="file"], input[type="file"]').last
                        if file_input.count() > 0:
                            file_input.set_input_files(img_for_this_comment)
                            time.sleep(2.0)
                            img_display_name = Path(img_for_this_comment).name
                            self.log(f"Đã đính kèm ảnh '{img_display_name}' cho bình luận #{c_idx+1}.", "info", acc_name)
                    except Exception as img_err:
                        self.log(f"Không thể đính kèm ảnh bình luận: {img_err}", "warning", acc_name)

                if chosen_text:
                    self._human_type(page, chosen_text)
                    time.sleep(0.8)

                page.keyboard.press("Enter")
                time.sleep(2.5)
                preview = chosen_text[:35] + ("..." if len(chosen_text) > 35 else "") if chosen_text else "(Chỉ gửi ảnh)"
                self.log(f"Đã gửi bình luận #{c_idx+1}/{total_comments}: '{preview}'", "success", acc_name)

                if c_idx < total_comments - 1:
                    sleep_between = random.randint(3, 6)
                    time.sleep(sleep_between)

        except Exception as ex:
            self.log(f"Lỗi bình luận mồi: {ex}", "warning", acc_name)

# Alias for backward compatibility
BulkPosterEngine = MultiAccountBulkEngine

