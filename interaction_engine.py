"""
Interaction & Warm-up Engine for Facebook Accounts with AI Contextual Commenting & Image Seeding
Simulates human browsing behaviors:
- Newsfeed scrolling with random likes
- Reading post text and existing comments using AI
- Contextual commenting (AI auto-generates comments matching post context)
- Dual mode: Natural comment OR Natural comment + Seeding comment with image attachment
- Watching Reels / short videos
- Browsing group feeds
- Anti-Detect Stealth with per-profile proxy and WebRTC protection
"""

import os
import sys
import time
import json
import random
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from playwright.sync_api import sync_playwright
from ai_content_engine import ai_engine
from meta_sentinel import meta_sentinel

SEEDING_FILE = Path("data/interaction_seeding.json")

def load_seeding_list() -> List[Dict[str, Any]]:
    if not SEEDING_FILE.exists():
        default_items = [
            {
                "id": "seed_1",
                "text": "Mình cũng đang dùng mẫu này bên Pet Travel, công nhận tiện mà êm ái lắm các bác ạ 🥰",
                "image": "",
                "enabled": True
            },
            {
                "id": "seed_2",
                "text": "Bác nào quan tâm phụ kiện xịn giá tốt tham khảo bên Pet Travel Love xem, đợt này đang freeship á!",
                "image": "",
                "enabled": True
            }
        ]
        save_seeding_list(default_items)
        return default_items
    try:
        with open(SEEDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_seeding_list(items: List[Dict[str, Any]]) -> bool:
    try:
        SEEDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SEEDING_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[Interaction] Lỗi lưu seeding list: {e}")
        return False

class InteractionEngine:
    def __init__(self, log_callback: Optional[Callable[[str, str], None]] = None):
        self.log_callback = log_callback
        self.state = "IDLE"  # IDLE, RUNNING, STOPPED, ERROR
        self._stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.stats = {
            "state": "IDLE",
            "account_name": "",
            "step": "",
            "likes_done": 0,
            "scrolls_done": 0,
            "reels_done": 0,
            "comments_done": 0,
            "seeding_done": 0,
            "last_comment": ""
        }

    def log(self, msg: str, level: str = "info", acc_name: str = ""):
        timestamp = time.strftime("%H:%M:%S")
        prefix = f"[{acc_name}] " if acc_name else ""
        log_line = f"[{timestamp}] [TƯƠNG TÁC] {prefix}{msg}"
        print(log_line)
        if self.log_callback:
            self.log_callback(log_line, level)

    def start(self, account: Dict[str, Any], config: Dict[str, Any]):
        if self.state == "RUNNING":
            self.log("Kịch bản tương tác đang chạy!", "warning")
            return

        self._stop_event.clear()
        self.state = "RUNNING"
        self.stats["state"] = "RUNNING"
        fb_suffix = f" [{account.get('fb_name', '')}]" if account.get("fb_name") else ""
        acc_name = f"{account.get('name', 'FB')}{fb_suffix}"
        self.stats["account_name"] = acc_name
        self.stats["likes_done"] = 0
        self.stats["scrolls_done"] = 0
        self.stats["reels_done"] = 0
        self.stats["comments_done"] = 0
        self.stats["seeding_done"] = 0
        self.stats["last_comment"] = ""

        self.thread = threading.Thread(target=self._run_interaction, args=(account, config), daemon=True)
        self.thread.start()

    def stop(self):
        if self.state == "RUNNING":
            self.state = "STOPPED"
            self.stats["state"] = "STOPPED"
            self._stop_event.set()
            self.log("Đã gửi lệnh dừng kịch bản tương tác.", "warning")

    def _sleep_delay(self, min_d: int, max_d: int, acc_name: str):
        delay = random.randint(min_d, max(min_d, max_d))
        for _ in range(delay):
            if self._stop_event.is_set(): break
            time.sleep(1)

    def _type_human(self, locator, text: str):
        for char in text:
            if self._stop_event.is_set(): break
            locator.type(char, delay=random.randint(30, 90))
            if random.random() < 0.05:
                time.sleep(random.uniform(0.2, 0.5))

    def _run_interaction(self, account: Dict[str, Any], config: Dict[str, Any]):
        fb_suffix = f" [{account.get('fb_name', '')}]" if account.get("fb_name") else ""
        acc_name = f"{account.get('name', 'FB')}{fb_suffix}"
        profile_path = Path(account.get("profile_dir", "profiles/" + account.get("id"))).resolve()
        profile_path.mkdir(parents=True, exist_ok=True)
        headless = config.get("headless", False)

        scroll_times = int(config.get("scroll_newsfeed_times", 8))
        target_likes = int(config.get("like_posts_count", 3))
        read_comments = int(config.get("read_comments_count", 1))
        target_reels = int(config.get("watch_reels_count", 2))
        interact_groups = config.get("interact_groups", True)
        min_d = int(config.get("min_action_delay", 4))
        max_d = int(config.get("max_action_delay", 10))

        # AI Comment Configuration
        ai_comment_enabled = bool(config.get("ai_comment_enabled", True))
        comment_mode = config.get("comment_mode", "natural")  # natural hoặc natural_plus_seeding
        max_comments = int(config.get("max_comments", 2))
        custom_prompt = config.get("custom_prompt", "")
        knowledge_base = config.get("knowledge_base", "")

        seeding_items = config.get("seeding_items")
        if seeding_items is None:
            raw_seeds = load_seeding_list()
            seeding_items = [s for s in raw_seeds if s.get("enabled", True)]

        from account_manager import kill_chrome_for_profile, parse_proxy
        kill_chrome_for_profile(profile_path)

        self.log(f"BẮT ĐẦU KỊCH BẢN TƯƠNG TÁC (Headless={headless}, AI Comment={ai_comment_enabled})...", "info", acc_name)
        pw = None
        context = None

        try:
            pw = sync_playwright().start()
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

            # Bước 1: Lướt Newsfeed & Thả Like
            self.stats["step"] = "Lướt Bảng tin"
            self.log(f"-> Bước 1: Vào Bảng tin (Newsfeed), lướt {scroll_times} lần...", "info", acc_name)
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=45000)
            time.sleep(3)

            likes_done = 0
            comments_done = 0

            for s in range(scroll_times):
                if self._stop_event.is_set(): break
                scroll_px = random.randint(450, 900)
                page.evaluate(f"window.scrollBy(0, {scroll_px})")
                self.stats["scrolls_done"] += 1
                self.log(f"Lướt bảng tin lần {s + 1}/{scroll_times}...", "info", acc_name)
                time.sleep(random.uniform(1.8, 3.5))

                # Like ngẫu nhiên
                if likes_done < target_likes and random.random() < 0.45:
                    like_btns = page.locator('div[aria-label="Thích"], div[aria-label="Like"]').all()
                    for btn in like_btns:
                        try:
                            if btn.is_visible() and random.random() < 0.5:
                                btn.click()
                                likes_done += 1
                                self.stats["likes_done"] = likes_done
                                self.log(f"Đã thả Like bài viết ({likes_done}/{target_likes})!", "success", acc_name)
                                self._sleep_delay(min_d, max_d, acc_name)
                                break
                        except Exception: pass

                # Tương tác Bình luận bằng AI (Nếu bật)
                if ai_comment_enabled and comments_done < max_comments and random.random() < 0.4:
                    success = self._try_ai_comment_on_feed(
                        page=page,
                        acc_name=acc_name,
                        mode=comment_mode,
                        seeding_items=seeding_items,
                        custom_prompt=custom_prompt,
                        knowledge_base=knowledge_base,
                        acc_id=account.get("id") or acc_name
                    )
                    if success:
                        comments_done += 1
                        self.stats["comments_done"] = comments_done
                        self._sleep_delay(min_d * 2, max_d * 2, acc_name)

            # Bước 2: Xem bình luận bài viết
            if read_comments > 0 and not self._stop_event.is_set():
                self.stats["step"] = "Đọc bình luận"
                self.log(f"-> Bước 2: Xem bình luận {read_comments} bài viết...", "info", acc_name)
                comment_btns = page.locator('div[role="button"]:has-text("bình luận"), div[role="button"]:has-text("comment")').all()
                read_count = 0
                for cbtn in comment_btns:
                    if read_count >= read_comments or self._stop_event.is_set(): break
                    try:
                        if cbtn.is_visible():
                            cbtn.click()
                            read_count += 1
                            self.log("Đang đọc luồng bình luận bài viết...", "info", acc_name)
                            time.sleep(random.uniform(3.0, 6.0))
                            page.evaluate("window.scrollBy(0, 300)")
                            self._sleep_delay(min_d, max_d, acc_name)
                    except Exception: pass

            # Bước 3: Xem Reels / Video ngắn
            if target_reels > 0 and not self._stop_event.is_set():
                self.stats["step"] = "Xem Reels"
                self.log(f"-> Bước 3: Xem {target_reels} video Reels giải trí...", "info", acc_name)
                try:
                    page.goto("https://www.facebook.com/reel/", wait_until="domcontentloaded", timeout=40000)
                    time.sleep(3)
                    for r in range(target_reels):
                        if self._stop_event.is_set(): break
                        watch_time = random.randint(8, 16)
                        self.log(f"Đang xem video Reel #{r + 1} ({watch_time}s)...", "info", acc_name)
                        time.sleep(watch_time)
                        self.stats["reels_done"] += 1
                        page.keyboard.press("ArrowDown")
                        time.sleep(2)
                except Exception as ex:
                    self.log(f"Lỗi xem Reels: {ex}", "warning", acc_name)

            # Bước 4: Tương tác Nhóm
            if interact_groups and not self._stop_event.is_set():
                self.stats["step"] = "Lướt Nhóm"
                self.log("-> Bước 4: Vào Bảng tin Nhóm (Groups Feed)...", "info", acc_name)
                try:
                    page.goto("https://www.facebook.com/groups/feed/", wait_until="domcontentloaded", timeout=40000)
                    time.sleep(3)
                    for _ in range(3):
                        if self._stop_event.is_set(): break
                        page.evaluate("window.scrollBy(0, 600)")
                        time.sleep(2)

                    like_btns = page.locator('div[aria-label="Thích"], div[aria-label="Like"]').all()
                    for btn in like_btns:
                        try:
                            if btn.is_visible():
                                btn.click()
                                self.log("Đã thả Like 1 bài trong nhóm!", "success", acc_name)
                                break
                        except Exception: pass
                    self._sleep_delay(min_d, max_d, acc_name)
                except Exception as ex:
                    self.log(f"Lỗi lướt nhóm: {ex}", "warning", acc_name)

            self.state = "IDLE"
            self.stats["state"] = "IDLE"
            self.log(f"HOÀN TẤT PHIÊN TƯƠNG TÁC! (Likes: {likes_done}, Bình luận AI: {comments_done}, Reels: {self.stats['reels_done']})", "success", acc_name)

        except Exception as e:
            self.state = "ERROR"
            self.stats["state"] = "ERROR"
            self.log(f"Lỗi trong kịch bản tương tác: {e}", "error", acc_name)
        finally:
            if context:
                try: context.close()
                except Exception: pass
            if pw:
                try: pw.stop()
                except Exception: pass
            self.state = "IDLE"

    def _try_ai_comment_on_feed(self, page, acc_name: str, mode: str, seeding_items: List[Dict[str, Any]],
                                custom_prompt: str, knowledge_base: str, acc_id: str = "") -> bool:
        """
        Tìm 1 bài viết trên màn hình, trích xuất text + comments,
        dùng AI sinh comment phù hợp và thực hiện bình luận (kèm ảnh seeding nếu có).
        """
        try:
            # 1. Tìm các bài viết đang hiển thị trên feed
            articles = page.locator('div[role="feed"] > div, div[role="article"]').all()
            if not articles:
                articles = page.locator('div[data-ad-preview="message"]').all()

            target_article = None
            post_text = ""

            for art in articles[:6]:
                try:
                    if not art.is_visible(): continue
                    # Trích xuất đoạn text chính của bài viết
                    text_els = art.locator('div[dir="auto"]').all()
                    combined_text = " ".join([t.inner_text() for t in text_els if len(t.inner_text().strip()) > 15])
                    if len(combined_text) >= 20:
                        target_article = art
                        post_text = combined_text[:600]
                        break
                except Exception: continue

            if not target_article or not post_text:
                return False

            self.log(f"AI đang đọc bài viết: '{post_text[:60]}...' để sinh bình luận phù hợp...", "info", acc_name)

            # 2. Thu thập 1 vài comment sẵn có (nếu thấy)
            existing_comments = []
            try:
                cm_els = target_article.locator('div[role="article"] div[dir="auto"], ul div[dir="auto"]').all()
                for c in cm_els[:3]:
                    txt = c.inner_text().strip()
                    if txt and len(txt) > 5 and txt != post_text:
                        existing_comments.append(txt)
            except Exception: pass

            # 3. Gọi AI Content Engine
            ai_res = ai_engine.generate_feed_comment(
                post_text=post_text,
                existing_comments=existing_comments,
                mode=mode,
                seeding_items=seeding_items,
                custom_prompt=custom_prompt,
                knowledge_base=knowledge_base
            )
            natural_comment = ai_res.get("natural_comment", "").strip()
            seeding_comment = ai_res.get("seeding_comment", "").strip()
            image_path = ai_res.get("image_path")

            if not natural_comment:
                return False

            # 4. Định vị ô nhập bình luận của bài viết
            comment_box = target_article.locator('div[role="textbox"][contenteditable="true"]')
            if comment_box.count() == 0:
                # Thử bấm nút "Bình luận" để mở ô nhập
                btn_cm = target_article.locator('div[role="button"]:has-text("Bình luận"), div[role="button"]:has-text("Comment")')
                if btn_cm.count() > 0 and btn_cm.first.is_visible():
                    btn_cm.first.click()
                    time.sleep(1.5)
                    comment_box = target_article.locator('div[role="textbox"][contenteditable="true"]')

            if comment_box.count() == 0:
                # Thử tìm ô bình luận toàn trang đang focus
                comment_box = page.locator('div[role="textbox"][contenteditable="true"]').first

            if comment_box.count() > 0:
                acc_key = acc_id or acc_name
                is_tripped, trip_reason = meta_sentinel.is_circuit_tripped(acc_key)
                if is_tripped:
                    self.log(f"🚨 PHANH AN TOÀN KÍCH HOẠT: Dừng tài khoản '{acc_name}' ({trip_reason})!", "error", acc_name)
                    return False

                risk_info = meta_sentinel.record_action_start(acc_key, "groups_interact", content=natural_comment, delay_seconds=20, account_name=acc_name)
                if risk_info.get("risk_score", 0) >= 70:
                    self.log(f"⚠️ Radar Meta: Rủi ro {risk_info['risk_score']}%. {', '.join(risk_info.get('risk_factors', []))}", "warning", acc_name)

                box = comment_box.first
                box.scroll_into_view_if_needed()
                box.click()
                time.sleep(1)

                # Gõ comment tự nhiên bằng tốc độ người thật
                self.log(f"AI đang gõ bình luận tự nhiên: '{natural_comment}'...", "info", acc_name)
                self._type_human(box, natural_comment)
                time.sleep(1.5)
                box.press("Enter")
                time.sleep(3)
                self.stats["last_comment"] = natural_comment
                self.log(f"Đã đăng bình luận AI thành công: '{natural_comment}'", "success", acc_name)

                # Giám sát phản ứng Meta
                incident = meta_sentinel.inspect_page(page, acc_key, "groups_interact", account_name=acc_name, context_info="Nuôi nick bình luận feed")
                if incident:
                    self.log(f"🚨 Phát hiện phản ứng Meta: {incident.get('error_text')}", "error", acc_name)
                    meta_sentinel.record_action_result(acc_key, "groups_interact", success=False, error_msg=incident.get('error_text'), account_name=acc_name)
                    return False
                else:
                    meta_sentinel.record_action_result(acc_key, "groups_interact", success=True, account_name=acc_name)

                # 5. Nếu chế độ có seeding + ảnh đính kèm
                if mode == "natural_plus_seeding" and (seeding_comment or image_path):
                    time.sleep(random.uniform(4.0, 7.0))
                    self.log(f"Đang tiến hành comment Seeding kèm ảnh/nội dung phụ...", "info", acc_name)

                    # Tìm ô comment mới
                    new_box = target_article.locator('div[role="textbox"][contenteditable="true"]').first
                    if new_box.count() > 0:
                        new_box.click()
                        time.sleep(1)

                        # Tải ảnh đính kèm nếu có
                        if image_path and os.path.exists(image_path):
                            img_input = target_article.locator('input[type="file"][accept*="image"]')
                            if img_input.count() == 0:
                                img_input = page.locator('input[type="file"][accept*="image"]')
                            if img_input.count() > 0:
                                try:
                                    img_input.first.set_input_files(str(Path(image_path).resolve()))
                                    self.log(f"Đã đính kèm ảnh seeding: {Path(image_path).name}", "info", acc_name)
                                    time.sleep(4)
                                except Exception as ie:
                                    self.log(f"Không thể đính kèm ảnh: {ie}", "warning", acc_name)

                        # Gõ nội dung seeding
                        seed_txt = seeding_comment or "Bên mình có sẵn mẫu đẹp lắm, mọi người ghé xem nha!"
                        self._type_human(new_box, seed_txt)
                        time.sleep(1.5)
                        new_box.press("Enter")
                        time.sleep(3)
                        self.stats["seeding_done"] += 1
                        self.log(f"Đã đăng comment Seeding thành công!", "success", acc_name)

                return True

        except Exception as e:
            self.log(f"Lỗi khi thực hiện bình luận AI: {e}", "warning", acc_name)

        return False

interaction_engine = InteractionEngine()
