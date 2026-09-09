"""
Cross-Page Seeding & Share Engine for Enterprise Facebook Fanpage Matrix
Empowers multi-page operations where satellite / spoke pages automatically:
1. Like / React to the Main Hub (Sales) Page's post.
2. Leave authentic, high-converting seed comments based on each Spoke Page's Persona & Niche.
3. Share the post (Cross-Share) to their own Wall/Feed with engaging captions.
Triggers Meta's 30-minute viral velocity discovery algorithm.
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
from browser_session_manager import browser_session_mgr
from ai_content_engine import ai_engine

def parse_spintax(text: Optional[str]) -> str:
    """Xử lý cú pháp Spintax {a|b|c} ngẫu nhiên"""
    if not text:
        return ""
    pattern = re.compile(r"\{([^{}]+)\}")
    while True:
        match = pattern.search(text)
        if not match:
            break
        choices = match.group(1).split("|")
        text = text[:match.start()] + random.choice(choices) + text[match.end():]
    return text

# Fallback comments theo ngách khi chưa có API key AI
NICHE_COMMENT_TEMPLATES = {
    "TIN_TUC": [
        "Thông tin rất hữu ích và kịp thời, cảm ơn shop/trang đã chia sẻ ạ! 👏",
        "Chủ đề này đang rất được quan tâm luôn, lưu lại để theo dõi tiếp.",
        "Bài viết tổng hợp chi tiết quá! Mọi người nên tham khảo.",
        "Thông tin chính xác, chia sẻ về tường cho bạn bè cùng đọc nha! 👍"
    ],
    "BAN_HANG": [
        "Sản phẩm này còn sẵn hàng không ạ? Shop check tin nhắn giúp mình với nhé! ❤️",
        "Có freeship và được kiểm tra hàng trước khi nhận không shop ơi?",
        "Tư vấn cho mình mẫu này với ạ, đang cần mua gấp trong hôm nay!",
        "Inbox mình giá sỉ và chương trình khuyến mãi hiện tại với nha!",
        "Sản phẩm dùng rất thích, ủng hộ shop dài dài! ⭐⭐⭐⭐⭐"
    ],
    "MEO_VAT": [
        "Mẹo này hay quá, giờ mình mới biết cách làm đơn giản thế này! 💡",
        "Lưu lại ngay khi nào cần áp dụng. Cảm ơn chia sẻ bổ ích của trang!",
        "Rất thiết thực cho cuộc sống hàng ngày, like mạnh cho bài viết ạ!",
        "Áp dụng thử thấy hiệu quả bất ngờ luôn mọi người ơi! 💯"
    ],
    "REVIEW": [
        "Đánh giá rất khách quan và chi tiết, 10 điểm cho chất lượng! 👍",
        "Mình cũng đang dùng mẫu này, công nhận chất lượng vượt tầm giá.",
        "Review chuẩn phết, đỡ phải mất công tìm kiếm so sánh nhiều nơi.",
        "Shop phục vụ chu đáo, giao hàng nhanh, xứng đáng 5 sao!"
    ],
    "GIAI_TRI": [
        "Xem mà cười xỉu luôn á trời! 😂 Đỉnh của chóp!",
        "Haha lưu về chia sẻ cho hội bạn thân cùng xem ngay mới được.",
        "Quá hài hước, giải tỏa căng thẳng sau giờ làm việc mệt mỏi! 🤣"
    ],
    "SUC_KHOE": [
        "Kiến thức chăm sóc sức khỏe rất khoa học và dễ hiểu, cảm ơn trang! 🌿",
        "Mình sẽ duy trì thói quen này mỗi ngày để cải thiện sức khỏe.",
        "Bài viết chia sẻ rất có tâm, gia đình mình ai cũng cần đọc cái này."
    ],
    "LAM_DEP": [
        "Tip làm đẹp này đỉnh thật sự, da mịn màng hơn hẳn sau 1 tuần! ✨",
        "Chị em nên tham khảo ngay mẹo này nha, hiệu quả lắm luôn.",
        "Inbox tư vấn liệu trình phù hợp với da dầu giúp mình với shop ơi! 💄"
    ],
    "BAT_DONG_SAN": [
        "Dự án này vị trí đẹp và tiềm năng sinh lời cao, gửi mình thông tin qua inbox nhé!",
        "Pháp lý dự án này thế nào vậy chuyên viên? Có sổ đỏ trao tay không?",
        "Bài phân tích thị trường rất có chiều sâu và minh bạch. 🏢"
    ],
    "MARKETING": [
        "Tư duy chiến lược rất thực chiến và sắc bén! Cảm ơn anh/chị.",
        "Bài học marketing đắt giá cho người khởi nghiệp kinh doanh online. 📈",
        "Lưu lại nghiền ngẫm và áp dụng ngay vào đội ngũ của mình."
    ],
    "TAM_SU": [
        "Đọc mà thấy đồng cảm sâu sắc từng câu chữ... Chúc bạn luôn an yên! ❤️",
        "Cuộc sống ai cũng có những giai đoạn như vậy, cố gắng lên nhé bạn ơi.",
        "Một góc nhìn rất ấm áp và nhân văn, cảm ơn tác giả."
    ]
}

class CrossPageEngine:
    def __init__(self, log_callback: Optional[Callable[[str, str, Optional[str]], None]] = None):
        self.log_callback = log_callback
        self.state = "IDLE"  # IDLE, RUNNING, STOPPED, FINISHED, ERROR
        self.stats = {
            "total_pages": 0,
            "current_index": 0,
            "current_page": "",
            "success_count": 0,
            "failed_count": 0,
            "comments_done": 0,
            "shares_done": 0,
            "reactions_done": 0,
            "progress_percent": 0,
            "active_log": "Hệ thống Seeding Chéo sẵn sàng."
        }
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def log(self, message: str, level: str = "info", page_name: Optional[str] = None):
        self.stats["active_log"] = message
        if self.log_callback:
            prefix = f"[{page_name}] " if page_name else ""
            self.log_callback(f"[SEEDING CHÉO] {prefix}{message}", level, None)
        else:
            print(f"[SEEDING CHÉO] {message}")

    def get_status(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "stats": self.stats
        }

    def start_seeding(self, account: Dict[str, Any], config: Dict[str, Any]) -> bool:
        if self.state == "RUNNING":
            return False

        self._stop_event.clear()
        self.state = "RUNNING"

        spoke_pages = config.get("spoke_pages", [])
        self.stats = {
            "total_pages": len(spoke_pages),
            "current_index": 0,
            "current_page": "",
            "success_count": 0,
            "failed_count": 0,
            "comments_done": 0,
            "shares_done": 0,
            "reactions_done": 0,
            "progress_percent": 0,
            "active_log": f"Bắt đầu chiến dịch Seeding Chéo với {len(spoke_pages)} Trang Phụ..."
        }

        self._thread = threading.Thread(
            target=self._run_seeding,
            args=(account, config, spoke_pages),
            daemon=True
        )
        self._thread.start()
        return True

    def stop_seeding(self):
        self._stop_event.set()
        self.state = "STOPPED"
        self.log("Đã nhận lệnh dừng Chiến Dịch Seeding Chéo.", "warning")

    def _generate_seed_comment(self, page_info: Dict[str, Any], target_post_url: str, custom_text: Optional[str] = None, simulate: bool = False) -> str:
        """Sinh nội dung bình luận mồi: ưu tiên custom -> AI -> Template ngách"""
        if custom_text and custom_text.strip():
            return parse_spintax(custom_text)

        niche = page_info.get("niche", "BAN_HANG")
        templates = NICHE_COMMENT_TEMPLATES.get(niche, NICHE_COMMENT_TEMPLATES["BAN_HANG"])

        if simulate:
            return random.choice(templates)

        persona = page_info.get("persona", "Khách hàng thân thiện")
        page_name = page_info.get("name", "Trang Phụ")

        # Thử sinh bình luận AI thông minh
        try:
            prompt = (
                f"Đóng vai: {persona}. Bạn đang xem một bài đăng bán hàng/chia sẻ tại {target_post_url}. "
                f"Hãy viết 1 câu bình luận ngắn gọn (1-2 câu), tự nhiên như khách hàng thật hoặc người theo dõi trung thành "
                f"(ví dụ hỏi giá, hỏi tư vấn ship, khen sản phẩm, hoặc lưu lại). Có emoji tự nhiên, không lộ liễu quảng cáo."
            )
            ai_res = ai_engine.generate_post_content(
                topic=f"Bình luận seeding mồi cho Fanpage {page_name}",
                framework="BAB",
                tone="THUONG_NGAY",
                custom_prompt=prompt
            )
            content = ai_res.get("content", "") if isinstance(ai_res, dict) else str(ai_res)
            # Làm sạch nếu AI trả về dài dòng
            cleaned = content.strip().split("\n")[0].replace('"', '')
            if len(cleaned) > 10 and len(cleaned) < 180:
                return cleaned
        except Exception:
            pass

        # Fallback vào kho template theo ngách
        templates = NICHE_COMMENT_TEMPLATES.get(niche, NICHE_COMMENT_TEMPLATES["BAN_HANG"])
        return random.choice(templates)

    def _run_seeding(self, account: Dict[str, Any], config: Dict[str, Any], spoke_pages: List[Dict[str, Any]]):
        target_post_url = config.get("target_post_url", "").strip()
        do_reaction = bool(config.get("do_reaction", True))
        do_comment = bool(config.get("do_comment", True))
        comment_custom = config.get("comment_custom") or ""
        do_share = bool(config.get("do_share", True))
        share_caption = config.get("share_caption") or "{Mẹo hay cả nhà ơi|Thông tin hữu ích|Góc gợi ý mua sắm} mọi người tham khảo nhé! #trending #review"
        min_delay = int(config.get("min_delay", 15))
        max_delay = int(config.get("max_delay", 40))
        headless = bool(config.get("headless", False))
        simulate = bool(config.get("simulate", False))

        acc_name = account.get("name", "Facebook Profile")
        acc_id = account.get("id", "default")
        profile_path = Path(account.get("profile_dir", f"profiles/{acc_id}")).resolve()

        total = len(spoke_pages)
        self.log(f"Khởi động phiên Chrome cho {acc_name}. Sẽ điều hướng {total} Trang Phụ tương tác bài viết...", "info")

        if simulate or not target_post_url.startswith("http"):
            # Chế độ mô phỏng kiểm thử nhanh (không mở browser thật)
            self.log("▶ Đang chạy ở chế độ kiểm thử mô phỏng Seeding Chéo...", "info")
            for idx, p in enumerate(spoke_pages):
                if self._stop_event.is_set():
                    break
                p_name = p.get("name", f"Trang Phụ {idx+1}")
                self.stats["current_index"] = idx + 1
                self.stats["current_page"] = p_name
                self.log(f"[{idx+1}/{total}] Chuyển vai trò sang Fanpage: {p_name}...", "info", p_name)
                time.sleep(1)

                if do_reaction:
                    self.stats["reactions_done"] += 1
                    self.log(f"Đã thả tim bài viết chính từ {p_name}", "success", p_name)

                if do_comment:
                    cmt = self._generate_seed_comment(p, target_post_url, comment_custom, simulate=True)
                    self.stats["comments_done"] += 1
                    self.log(f"Đã để lại bình luận mồi: '{cmt}'", "success", p_name)

                if do_share:
                    caption = parse_spintax(share_caption)
                    self.stats["shares_done"] += 1
                    self.log(f"Đã chia sẻ bài viết về Tường {p_name} kèm caption: '{caption}'", "success", p_name)

                self.stats["success_count"] += 1
                self.stats["progress_percent"] = int(((idx + 1) / total) * 100)
                time.sleep(1)

            self.state = "FINISHED"
            self.log(f"🎉 CHIẾN DỊCH HOÀN TẤT! Đã đẩy tương tác qua {self.stats['success_count']}/{total} Trang Phụ.", "success")
            return

        # Vận hành thật qua Playwright CDP
        browser_or_ctx = None
        page = None
        is_cdp = False

        try:
            browser_or_ctx, page, is_cdp = browser_session_mgr.acquire_page(
                account_id=acc_id,
                profile_dir=profile_path,
                headless=headless
            )

            for idx, p in enumerate(spoke_pages):
                if self._stop_event.is_set():
                    self.log("Dừng chiến dịch seeding theo yêu cầu.", "warning")
                    break

                p_name = p.get("name", f"Trang Phụ {idx+1}")
                p_url = p.get("url", "")
                self.stats["current_index"] = idx + 1
                self.stats["current_page"] = p_name
                self.log(f"[{idx+1}/{total}] Chuẩn bị chuyển vai trò sang: {p_name}...", "info", p_name)

                # Phanh an toàn Meta Sentinel
                is_tripped, trip_reason = meta_sentinel.is_circuit_tripped(acc_id)
                if is_tripped:
                    self.log(f"🚨 PHANH AN TOÀN KÍCH HOẠT: Dừng tài khoản do cảnh báo Meta ({trip_reason})!", "error", p_name)
                    break

                meta_sentinel.record_action_start(acc_id, "cross_seeding", delay_seconds=min_delay, account_name=acc_name)

                # 1. Đổi vai trò sang Trang Phụ
                role_ok = ensure_role(
                    page=page,
                    role_type="page",
                    role_url=p_url,
                    role_name=p_name,
                    log_fn=lambda m, l: self.log(m, l, p_name)
                )

                if not role_ok:
                    self.log(f"Không thể chuyển sang vai trò {p_name}. Bỏ qua.", "warning", p_name)
                    self.stats["failed_count"] += 1
                    continue

                # 2. Điều hướng tới bài viết Trang Chính
                self.log(f"Đang mở bài viết mục tiêu: {target_post_url}...", "info", p_name)
                try:
                    page.goto(target_post_url, wait_until="domcontentloaded", timeout=45000)
                    time.sleep(random.uniform(3, 5))
                except Exception as ex:
                    self.log(f"Không thể tải bài viết mục tiêu: {ex}", "error", p_name)
                    self.stats["failed_count"] += 1
                    continue

                # 3. Hành động 1: Thả Like / Love
                if do_reaction:
                    try:
                        like_btn = page.locator('div[role="button"][aria-label*="Thích"], div[role="button"][aria-label*="Like"]').first
                        if like_btn.is_visible(timeout=3000):
                            like_btn.click()
                            self.stats["reactions_done"] += 1
                            self.log(f"Đã thả Thích cho bài viết thành công!", "success", p_name)
                            time.sleep(random.uniform(1.5, 3.0))
                    except Exception as e_like:
                        self.log(f"Bỏ qua thả Thích: {e_like}", "warning", p_name)

                # 4. Hành động 2: Bình luận mồi
                if do_comment:
                    try:
                        comment_text = self._generate_seed_comment(p, target_post_url, comment_custom)
                        self.log(f"Soạn bình luận mồi: '{comment_text}'...", "info", p_name)

                        cmt_box = page.locator(
                            'div[role="textbox"][aria-label*="Viết bình luận"], '
                            'div[role="textbox"][aria-label*="Write a comment"], '
                            'div[aria-label*="Bình luận dưới tên"], '
                            'div[aria-label*="Comment as"], '
                            'div[contenteditable="true"]'
                        ).first

                        if cmt_box.is_visible(timeout=4000):
                            cmt_box.click()
                            time.sleep(0.5)
                            normalized_c = (comment_text or "").replace("\r\n", "\n").replace("\r", "\n")
                            c_lines = normalized_c.split("\n")
                            for ci, cline in enumerate(c_lines):
                                if cline:
                                    cwords = cline.split(" ")
                                    for cj, cword in enumerate(cwords):
                                        page.keyboard.insert_text(cword + (" " if cj < len(cwords) - 1 else ""))
                                        time.sleep(0.02)
                                if ci < len(c_lines) - 1:
                                    page.keyboard.press("Shift+Enter")
                                    time.sleep(0.1)
                            time.sleep(1)
                            page.keyboard.press("Enter")
                            time.sleep(3)
                            self.stats["comments_done"] += 1
                            self.log(f"Đã gửi bình luận mồi thành công!", "success", p_name)
                        else:
                            self.log("Không tìm thấy ô bình luận trên bài viết này.", "warning", p_name)
                    except Exception as e_cmt:
                        self.log(f"Lỗi gửi bình luận mồi: {e_cmt}", "error", p_name)

                # 5. Hành động 3: Chia sẻ về Tường Trang Phụ
                if do_share:
                    try:
                        share_btn = page.locator('div[role="button"][aria-label*="Chia sẻ"], div[role="button"][aria-label*="Share"]').first
                        if share_btn.is_visible(timeout=4000):
                            share_btn.click()
                            time.sleep(2)

                            # Ưu tiên tìm nút 'Chia sẻ ngay' hoặc 'Chia sẻ lên Bảng feed'
                            share_now = page.locator(
                                'div[role="menuitem"]:has-text("Chia sẻ ngay"), '
                                'div[role="button"]:has-text("Chia sẻ ngay"), '
                                'div[role="menuitem"]:has-text("Share now"), '
                                'div[role="button"]:has-text("Share now")'
                            ).first

                            if share_now.is_visible(timeout=2500):
                                share_now.click()
                                time.sleep(4)
                                self.stats["shares_done"] += 1
                                self.log("Đã bấm Chia sẻ ngay về Tường Trang Phụ thành công!", "success", p_name)
                            else:
                                # Nếu có ô nhập caption chia sẻ
                                caption = parse_spintax(share_caption)
                                feed_box = page.locator('div[role="textbox"][aria-label*="Nói gì đó"], div[role="textbox"][aria-label*="Say something"]').first
                                if feed_box.is_visible(timeout=2000):
                                    feed_box.fill(caption)
                                    time.sleep(1)
                                submit_share = page.locator('div[role="button"]:has-text("Đăng"), div[role="button"]:has-text("Post"), div[role="button"]:has-text("Chia sẻ")').first
                                if submit_share.is_visible(timeout=2000):
                                    submit_share.click()
                                    time.sleep(4)
                                    self.stats["shares_done"] += 1
                                    self.log(f"Đã chia sẻ về Tường kèm lời giới thiệu: '{caption}'", "success", p_name)
                        else:
                            self.log("Không tìm thấy nút Chia sẻ trên bài viết.", "warning", p_name)
                    except Exception as e_share:
                        self.log(f"Lỗi chia sẻ bài viết: {e_share}", "warning", p_name)

                self.stats["success_count"] += 1
                self.stats["progress_percent"] = int(((idx + 1) / total) * 100)

                # Giãn cách nghỉ tự nhiên giữa các trang
                if idx < total - 1:
                    sleep_time = random.uniform(min_delay, max_delay)
                    self.log(f"Nghỉ giãn cách tự nhiên {sleep_time:.1f}s trước khi chuyển sang Trang tiếp theo...", "info", p_name)
                    time.sleep(sleep_time)

            self.state = "FINISHED"
            self.log(f"🎉 CHIẾN DỊCH HOÀN TẤT! Đã kéo tương tác thành công qua {self.stats['success_count']}/{total} Trang Phụ.", "success")

        except Exception as e_main:
            self.state = "ERROR"
            self.log(f"Lỗi tiến trình Seeding Chéo: {e_main}", "error")
        finally:
            if page and browser_or_ctx:
                browser_session_mgr.release_page(acc_id, browser_or_ctx, page, is_cdp)

cross_page_engine = CrossPageEngine()
