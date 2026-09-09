"""
AI Content Engine for Enterprise Facebook Marketing
Provides:
1. Knowledge Base & Custom Prompt Settings Management
2. Post Content Generator (AIDA, PAS, Storytelling, Promotion)
3. Wall Post Generator for Profile Nurturing / Warm-up
4. Auto Paraphraser (Multi-variants)
5. 3-Role Seeding Dialogue Generator
6. Feed Context Comment Generator for Smart Interaction
Supports both Gemini / OpenAI API and high-quality built-in NLP variation generator.
"""

import os
import re
import json
import random
import time
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional

SETTINGS_FILE = Path("data/ai_settings.json")

DEFAULT_SETTINGS = {
    "api_key": "",
    "system_prompt_general": "Bạn là chuyên gia Facebook Marketing và Growth Hacking hàng đầu cho Doanh Nghiệp. Hãy viết bằng tiếng Việt tự nhiên, hấp dẫn, đúng tâm lý khách hàng, giàu cảm xúc, sử dụng emoji hợp lý và tuân thủ tiêu chuẩn cộng đồng Facebook.",
    "knowledge_base": """[TÀI LIỆU GỐC DOANH NGHIỆP & SẢN PHẨM CHỦ LỰC]:
- Thương hiệu: Pet Travel - Tổng kho Phụ Kiện Thú Cưng Cao Cấp
- Sản phẩm chính: Balo phi thuyền trong suốt, nệm lông êm ái, chuồng vận chuyển gấp gọn, thức ăn hạt dinh dưỡng, bình nước du lịch.
- Điểm vượt trội (USP): Chất liệu bền đẹp, an toàn tuyệt đối cho boss, chống nước, thông gió thoáng mát.
- Cam kết & Chính sách: Bảo hành 12 tháng lỗi 1 đổi 1, đồng kiểm trước khi nhận hàng (ship COD toàn quốc), freeship đơn từ 299k.
- Hotline/Zalo hỗ trợ: 0988.xxx.xxx
- Kênh liên hệ: Fanpage Pet Travel Love hoặc nhắn tin trực tiếp để nhận ưu đãi sỉ/lẻ.""",
    "prompt_post_content": "Viết bài đăng Facebook bán hàng / chia sẻ chuyên sâu theo cấu trúc marketing yêu cầu. Kết hợp khéo léo thông tin từ Tài liệu gốc, nêu bật lợi ích thiết thực, tạo niềm tin và thôi thúc khách hàng để lại bình luận hoặc nhắn tin ngay.",
    "prompt_seeding": "Dựng kịch bản Seeding hội thoại từ 2 đến 3 người bình luận dưới bài viết. Các vai diễn tự nhiên, chân thật, một người hỏi han về sản phẩm, một người khen ngợi chất lượng và Fanpage trả lời thân thiện mời check inbox.",
    "prompt_wall_post": "Viết bài đăng status lên trang cá nhân (Personal Wall) để nuôi nick, gia tăng độ uy tín và tương tác tự nhiên. Nội dung mang tính đời thường, chia sẻ mẹo vặt, đặt câu hỏi gần gũi như một người dùng thật, không lộ liễu bán hàng.",
    "prompt_interaction": "Phân tích kỹ nội dung bài viết và các bình luận sẵn có trên bảng tin Facebook. Đóng vai một người bạn trên mạng xã hội bình luận một câu ngắn gọn (1-2 câu), tự nhiên, hài hước hoặc đồng cảm sâu sắc với tác giả bài viết. Nếu có yêu cầu seeding, hãy lồng ghép khéo léo thông tin hữu ích."
}

class AIContentEngine:
    def __init__(self):
        self.gemini_key = os.environ.get("GEMINI_API_KEY", "")
        self.openai_key = os.environ.get("OPENAI_API_KEY", "")
        self._ensure_settings()

    def _ensure_settings(self):
        if not SETTINGS_FILE.parent.exists():
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not SETTINGS_FILE.exists():
            self.save_settings(DEFAULT_SETTINGS)

    def load_settings(self) -> Dict[str, Any]:
        self._ensure_settings()
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                merged = DEFAULT_SETTINGS.copy()
                merged.update(data)
                return merged
        except Exception:
            return DEFAULT_SETTINGS.copy()

    def save_settings(self, settings: Dict[str, Any]) -> bool:
        try:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"[AI Content] Lỗi lưu settings: {e}")
            return False

    def get_effective_key(self, custom_key: Optional[str] = None) -> str:
        if custom_key and custom_key.strip():
            return custom_key.strip()
        settings = self.load_settings()
        if settings.get("api_key"):
            return settings["api_key"].strip()
        return self.gemini_key

    # =========================================================================
    # 1. BÀI ĐĂNG BÁN HÀNG & CHIA SẺ (POST CONTENT GENERATOR)
    # =========================================================================
    def generate_post_content(self, topic: str, framework: str = "AIDA", tone: str = "hap_dan",
                              custom_prompt: Optional[str] = None, knowledge_base: Optional[str] = None,
                              api_key: Optional[str] = None) -> Dict[str, Any]:
        settings = self.load_settings()
        key = self.get_effective_key(api_key)
        kb = knowledge_base if (knowledge_base and knowledge_base.strip()) else settings.get("knowledge_base", "")
        system_prompt = settings.get("system_prompt_general", "")
        post_prompt = custom_prompt if (custom_prompt and custom_prompt.strip()) else settings.get("prompt_post_content", "")

        framework_desc = {
            "AIDA": "Cấu trúc AIDA (Attention: Gây chú ý giật tít -> Interest: Tạo hứng thú thông tin -> Desire: Khơi gợi khao khát sở hữu -> Action: Kêu gọi hành động rõ ràng).",
            "PAS": "Cấu trúc PAS (Problem: Nêu rõ nỗi đau/vấn đề khách gặp phải -> Agitate: Xoáy sâu hệ quả nếu không giải quyết -> Solution: Giới thiệu giải pháp hoàn hảo).",
            "STORYTELLING": "Cấu trúc Kể chuyện (Câu chuyện khách hàng thực tế hoặc trải nghiệm bản thân, cảm xúc chân thật, bài học và giải pháp).",
            "PROMOTION": "Cấu trúc Ưu Đãi / Khuyến Mãi (Tiêu đề bùng nổ, danh sách quà tặng/giảm giá có hạn, lý do phải mua ngay hôm nay, hotline/CTA)."
        }.get(framework.upper(), "Cấu trúc bán hàng hấp dẫn, chuyên nghiệp.")

        if key:
            try:
                full_prompt = f"""{system_prompt}
{post_prompt}

[KHUNG BÀI VIẾT ÁP DỤNG]:
{framework_desc}

[TÀI LIỆU GỐC / THÔNG TIN SẢN PHẨM DOANH NGHIỆP]:
{kb}

[CHỦ ĐỀ BÀI VIẾT]:
"{topic}"
[GIỌNG ĐIỆU]: {tone}

Yêu cầu:
1. Viết 1 bài đăng Facebook hoàn chỉnh, có tiêu đề hấp dẫn, phân đoạn rõ ràng, sử dụng biểu tượng cảm xúc chuyên nghiệp, có lời kêu gọi hành động (CTA) và 3-5 hashtag liên quan.
2. Trả về đúng định dạng JSON:
{{
  "title": "Tiêu đề giật tít thu hút",
  "content": "Nội dung bài viết đầy đủ",
  "hashtags": "#hashtag1 #hashtag2 #hashtag3",
  "framework": "{framework}"
}}
Không thêm bất kỳ giải thích nào ngoài JSON."""
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": [{"text": full_prompt}]}],
                    "generationConfig": {"temperature": 0.8, "maxOutputTokens": 2500}
                }
                resp = requests.post(url, json=payload, timeout=25)
                data = resp.json()
                raw = data["candidates"][0]["content"]["parts"][0]["text"]
                raw = re.sub(r"^```json\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                return json.loads(raw)
            except Exception as e:
                print(f"[AI Content] Lỗi gọi Gemini Post Content: {e}, chuyển sang NLP Built-in.")

        return self._smart_post_fallback(topic, framework, kb)

    # =========================================================================
    # 2. BÀI ĐĂNG TƯỜNG CÁ NHÂN NUÔI NICK (WALL POST GENERATOR)
    # =========================================================================
    def generate_wall_post(self, topic: str, category: str = "daily", tone: str = "gan_gui",
                           custom_prompt: Optional[str] = None, knowledge_base: Optional[str] = None,
                           api_key: Optional[str] = None) -> Dict[str, Any]:
        settings = self.load_settings()
        key = self.get_effective_key(api_key)
        kb = knowledge_base if (knowledge_base and knowledge_base.strip()) else settings.get("knowledge_base", "")
        system_prompt = settings.get("system_prompt_general", "")
        wall_prompt = custom_prompt if (custom_prompt and custom_prompt.strip()) else settings.get("prompt_wall_post", "")

        cat_desc = {
            "daily": "Status đời thường, cảm xúc, hoạt động thường ngày, góc nhìn vui vẻ.",
            "tips": "Chia sẻ mẹo vặt, kiến thức hữu ích ngắn gọn dễ áp dụng.",
            "question": "Đặt câu hỏi mở, đố vui, thăm dò ý kiến kích thích bạn bè vào comment.",
            "story": "Câu chuyện ngắn ý nghĩa về cuộc sống, khách hàng hoặc công việc."
        }.get(category, "Status trang cá nhân tự nhiên.")

        if key:
            try:
                full_prompt = f"""{system_prompt}
{wall_prompt}

[MỤC TIÊU]: Viết bài đăng lên tường trang cá nhân Facebook (Personal Profile) để nuôi nick tương tác thật, tăng độ trust, KHÔNG đăng bài dạng quảng cáo bán hàng lộ liễu.
[THỂ LOẠI]: {cat_desc}
[CHỦ ĐỀ]: "{topic}"
[GIỌNG ĐIỆU]: {tone}
[TÀI LIỆU THAM KHẢO (NẾU CẦN LỒNG GHÉP KINH NGHIỆM)]:
{kb}

Trả về định dạng JSON:
{{
  "title": "Tiêu đề ngắn hoặc câu mở bài",
  "content": "Nội dung status tự nhiên (từ 3 đến 8 câu)",
  "engagement_question": "Câu hỏi ở cuối bài để kéo comment",
  "category": "{category}"
}}"""
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": [{"text": full_prompt}]}],
                    "generationConfig": {"temperature": 0.85, "maxOutputTokens": 1500}
                }
                resp = requests.post(url, json=payload, timeout=25)
                data = resp.json()
                raw = data["candidates"][0]["content"]["parts"][0]["text"]
                raw = re.sub(r"^```json\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                return json.loads(raw)
            except Exception as e:
                print(f"[AI Content] Lỗi gọi Gemini Wall Post: {e}, chuyển sang NLP Built-in.")

        return self._smart_wall_post_fallback(topic, category)

    # =========================================================================
    # 3. KỊCH BẢN SEEDING HỘI THOẠI 3 VAI (SEEDING GENERATOR)
    # =========================================================================
    def generate_seeding_dialogue(self, topic: str, tone: str = "tu_nhien",
                                  custom_prompt: Optional[str] = None, knowledge_base: Optional[str] = None,
                                  api_key: Optional[str] = None) -> Dict[str, Any]:
        settings = self.load_settings()
        key = self.get_effective_key(api_key)
        kb = knowledge_base if (knowledge_base and knowledge_base.strip()) else settings.get("knowledge_base", "")
        system_prompt = settings.get("system_prompt_general", "")
        seeding_prompt = custom_prompt if (custom_prompt and custom_prompt.strip()) else settings.get("prompt_seeding", "")

        if key:
            try:
                full_prompt = f"""{system_prompt}
{seeding_prompt}

[CHỦ ĐỀ CẦN SEEDING]: "{topic}"
[TÀI LIỆU GỐC SẢN PHẨM/DOANH NGHIỆP]:
{kb}

Hãy tạo kịch bản Seeding 3 vai tự nhiên bằng tiếng Việt:
1. role_ask (Khách hàng tiềm năng): 3 lựa chọn câu hỏi tự nhiên về giá/mẫu mã/chất lượng/ship.
2. role_review (Khách hàng cũ): 3 lựa chọn feedback khen ngợi trải nghiệm thực tế đúng với ưu điểm trong tài liệu gốc.
3. role_page (Fanpage trả lời): 3 lựa chọn trả lời tận tình và mời inbox.

Trả về duy nhất định dạng JSON thuần:
{{
  "topic": "{topic}",
  "role_ask": ["câu 1", "câu 2", "câu 3"],
  "role_review": ["câu 1", "câu 2", "câu 3"],
  "role_page": ["câu 1", "câu 2", "câu 3"],
  "combined_spintax": "{{câu hỏi 1|câu hỏi 2}} ... {{khen 1|khen 2}} ... {{rep 1|rep 2}}"
}}"""
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": [{"text": full_prompt}]}],
                    "generationConfig": {"temperature": 0.75, "maxOutputTokens": 2048}
                }
                resp = requests.post(url, json=payload, timeout=25)
                data = resp.json()
                raw = data["candidates"][0]["content"]["parts"][0]["text"]
                raw = re.sub(r"^```json\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                return json.loads(raw)
            except Exception as e:
                print(f"[AI Content] Lỗi gọi Gemini Seeding: {e}, chuyển sang Smart Seeding.")

        return self._smart_seeding_generator(topic)

    # =========================================================================
    # 4. TẠO BIẾN THỂ BÀI VIẾT (PARAPHRASER)
    # =========================================================================
    def generate_variants(self, source_text: str, count: int = 10,
                          custom_prompt: Optional[str] = None, knowledge_base: Optional[str] = None,
                          api_key: Optional[str] = None) -> List[str]:
        settings = self.load_settings()
        key = self.get_effective_key(api_key)
        kb = knowledge_base if (knowledge_base and knowledge_base.strip()) else settings.get("knowledge_base", "")
        system_prompt = settings.get("system_prompt_general", "")

        if key:
            try:
                full_prompt = f"""{system_prompt}
Hãy viết lại bài đăng quảng cáo dưới đây thành {count} biến thể KHÁC NHAU HOÀN TOÀN về cách mở bài, cấu trúc câu, từ ngữ và biểu tượng cảm xúc, nhưng giữ nguyên thông điệp chính và thông tin liên hệ.
Có thể bổ sung tinh tế điểm mạnh từ Tài liệu gốc:
{kb}

Mỗi biến thể trả về phân cách bằng chuỗi '===VARIANT==='. Không thêm số thứ tự hay giải thích gì khác.

Nội dung gốc:
{source_text}"""
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": [{"text": full_prompt}]}],
                    "generationConfig": {"temperature": 0.85, "maxOutputTokens": 4096}
                }
                resp = requests.post(url, json=payload, timeout=25)
                data = resp.json()
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                parts = [p.strip() for p in raw_text.split("===VARIANT===") if p.strip()]
                if parts and len(parts) >= 2:
                    return parts[:count]
            except Exception as e:
                print(f"[AI Content] Lỗi gọi Gemini Paraphrase: {e}, chuyển sang Smart NLP.")

        return self._smart_nlp_paraphrase(source_text, count)

    # =========================================================================
    # 5. SINH BÌNH LUẬN FEED THÔNG MINH CHO NUÔI NICK & TƯƠNG TÁC
    # =========================================================================
    def generate_feed_comment(self, post_text: str, existing_comments: Optional[List[str]] = None,
                              mode: str = "natural", seeding_items: Optional[List[Dict[str, str]]] = None,
                              custom_prompt: Optional[str] = None, knowledge_base: Optional[str] = None,
                              api_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Đọc nội dung bài viết và các bình luận sẵn có, sinh comment tự nhiên
        hoặc kết hợp comment seeding có ảnh.
        """
        settings = self.load_settings()
        key = self.get_effective_key(api_key)
        kb = knowledge_base if (knowledge_base and knowledge_base.strip()) else settings.get("knowledge_base", "")
        interaction_prompt = custom_prompt if (custom_prompt and custom_prompt.strip()) else settings.get("prompt_interaction", "")

        post_snippet = (post_text[:500] + "...") if len(post_text) > 500 else post_text
        comments_snippet = "\n".join([f"- {c}" for c in (existing_comments or [])[:3]])

        result = {
            "natural_comment": "",
            "seeding_comment": "",
            "image_path": None
        }

        # Nếu có danh sách seeding, chọn ngẫu nhiên 1 seeding item
        chosen_seeding = None
        if seeding_items and len(seeding_items) > 0:
            chosen_seeding = random.choice(seeding_items)
            result["seeding_comment"] = chosen_seeding.get("text", "")
            result["image_path"] = chosen_seeding.get("image", None)

        if key:
            try:
                prompt = f"""{interaction_prompt}

[TÀI LIỆU THAM KHẢO THƯƠNG HIỆU (NẾU CẦN KẾT NỐI)]:
{kb}

[BÀI VIẾT TRÊN BẢNG TIN]:
"{post_snippet}"

[MỘT VÀI BÌNH LUẬN SẴN CÓ]:
{comments_snippet if comments_snippet else "(Chưa có bình luận nào)"}

Yêu cầu:
1. Đọc hiểu chủ đề bài viết và cảm xúc của bài đăng.
2. Viết 1 câu bình luận tương tác cực kỳ tự nhiên (từ 1 đến 2 câu ngắn), như một người bạn thật trên Facebook (khen ngợi, đồng tình, chia sẻ cảm xúc, hỏi vui...).
3. Trả về đúng định dạng JSON:
{{
  "natural_comment": "Nội dung bình luận tự nhiên"
}}"""
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.85, "maxOutputTokens": 600}
                }
                resp = requests.post(url, json=payload, timeout=15)
                data = resp.json()
                raw = data["candidates"][0]["content"]["parts"][0]["text"]
                raw = re.sub(r"^```json\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                parsed = json.loads(raw)
                result["natural_comment"] = parsed.get("natural_comment", "")
            except Exception as e:
                print(f"[AI Content] Lỗi gọi Gemini Feed Comment: {e}, chuyển sang smart fallback.")

        if not result["natural_comment"]:
            result["natural_comment"] = self._smart_comment_fallback(post_text)

        return result

    # =========================================================================
    # FALLBACK NLP GENERATORS
    # =========================================================================
    def _smart_post_fallback(self, topic: str, framework: str, kb: str) -> Dict[str, Any]:
        hooks = [
            f"🔥 [SIÊU PHẨM MỚI] Bật mí giải pháp tuyệt vời cho {topic}!",
            f"✨ BẠN ĐANG TÌM KIẾM SỰ KHÁC BIỆT? Trải nghiệm ngay {topic}!",
            f"⚡ ĐỪNG BỎ LỠ: Bí quyết tối ưu chi phí & chất lượng với {topic}!"
        ]
        hook = random.choice(hooks)
        body = f"""📌 Nếu bạn đang tìm kiếm giải pháp tối ưu cho "{topic}", đây chính là sự lựa chọn không thể bỏ qua:
✔️ Chất lượng hàng đầu, độ bền cao và an toàn tuyệt đối.
✔️ Thiết kế thông minh, tiện lợi cho mọi nhu cầu sử dụng.
✔️ Cam kết bảo hành chính hãng, hỗ trợ tận tâm 24/7.

🎁 ĐẶC BIỆT: Miễn phí vận chuyển toàn quốc và tặng kèm ưu đãi cực lớn cho 50 khách hàng đầu tiên trong tuần này!"""
        cta = "👉 Nhắn tin ngay cho shop hoặc để lại dấu [.] dưới bài viết để nhận tư vấn và báo giá chi tiết nhất nhé!"
        tags = f"#{re.sub(r'[^a-zA-Z0-9]', '', topic).lower()} #uytin #chatluong #khuyenmai"
        content = f"{hook}\n\n{body}\n\n{cta}\n\n{tags}"

        return {
            "title": hook,
            "content": content,
            "hashtags": tags,
            "framework": framework
        }

    def _smart_wall_post_fallback(self, topic: str, category: str) -> Dict[str, Any]:
        templates = [
            f"Mỗi ngày đều là một trải nghiệm mới. Hôm nay tự nhiên có chút thời gian tìm hiểu về {topic}, thấy nhiều cái hay ghê cả nhà ạ! 🌿 Chúc mọi người một ngày làm việc thật nhiều năng lượng nha ❤️",
            f"Góc nhỏ chia sẻ hôm nay: Cả nhà mình đã ai thử trải nghiệm {topic} chưa? Cho em xin ít cảm nhận với review thực tế với nha! Thấy mọi người khen nhiều quá mà chưa kịp thử nè 😆",
            f"Cuộc sống đôi khi chỉ cần những điều giản dị như thế này thôi. Chiều nay rảnh rỗi ngồi ngắm lại {topic}, thấy nhẹ nhàng hẳn. Mọi người hôm nay thế nào rồi? ☕✨"
        ]
        selected = random.choice(templates)
        return {
            "title": f"Chia sẻ về {topic}",
            "content": selected,
            "engagement_question": "Cả nhà thấy thế nào? Để lại bình luận bên dưới nhé!",
            "category": category
        }

    def _smart_comment_fallback(self, post_text: str) -> str:
        templates = [
            "Bài viết hữu ích quá bạn ơi, thả tim nha ❤️",
            "Đồng quan điểm với bạn luôn, chia sẻ rất thực tế ạ!",
            "Tuyệt vời quá, cảm ơn thông tin hữu ích của bạn nhé 👍",
            "Nhìn cưng xỉu luôn á, chúc bạn ngày mới nhiều năng lượng nhé!",
            "Ý tưởng hay quá, mình cũng đang quan tâm chủ đề này 🥰",
            "Rất chuẩn luôn ạ, lưu lại học hỏi ngay thôi!"
        ]
        return random.choice(templates)

    def _smart_nlp_paraphrase(self, source_text: str, count: int) -> List[str]:
        hooks = [
            "🔥 [SIÊU HOT] CƠ HỘI ĐẶC BIỆT DÀNH CHO CẢ NHÀ!",
            "✨ BẬT MÍ GIẢI PHÁP TỐI ƯU NHẤT HIỆN NAY:",
            "⚡ DUY NHẤT HÔM NAY - ĐỪNG BỎ LỠ!",
            "🐾 TIN VUI DÀNH RIÊNG CHO CÁC TÍN ĐỒ YÊU THÍCH:",
            "💥 CẬP NHẬT MỚI NHẤT - HÀNG CẬP BẾN CỰC CHẤT:",
            "🌟 BẠN ĐANG TÌM KIẾM ĐIỀU NÀY? XEM NGAY NHÉ:",
            "📢 THÔNG BÁO KHẨN: ƯU ĐÃI KHỦNG CHƯA TỪNG CÓ!",
            "🎯 CHẤT LƯỢNG HÀNG ĐẦU - GIÁ TẬN GỐC TẬN KHO:",
            "💖 DÀNH CHO NHỮNG AI ĐANG QUAN TÂM ĐẾN SẢN PHẨM / DỊCH VỤ:",
            "🚀 BỨT PHÁ TRẢI NGHIỆM MỚI VỚI DÒNG SẢN PHẨM ĐỘC QUYỀN:"
        ]
        ctas = [
            "👉 Nhắn tin ngay cho Shop để nhận báo giá chi tiết và ưu đãi!",
            "📩 Để lại dấu [.] hoặc inbox trực tiếp, bên mình hỗ trợ 24/7 nha!",
            "☎️ Liên hệ ngay hotline/Zalo để được tư vấn tận tình nhất!",
            "🚀 Số lượng có hạn, inbox shop ngay hôm nay để giữ chỗ nhé!",
            "💬 Comment ngay dưới bài viết hoặc nhắn tin để nhận mã giảm giá!"
        ]
        lines = [l.strip() for l in source_text.splitlines() if l.strip()]
        core_body = "\n".join(lines[1:-1]) if len(lines) > 2 else source_text

        variants = []
        random.seed(int(time.time()))
        shuffled = hooks.copy()
        random.shuffle(shuffled)

        for i in range(count):
            h = shuffled[i % len(shuffled)]
            c = random.choice(ctas)
            v = f"{h}\n\n{core_body}\n\n{c}\n\n#trending #hot #uytin #chatluong"
            variants.append(v)

        return variants

    def _smart_seeding_generator(self, topic: str) -> Dict[str, Any]:
        role_ask_templates = [
            f"Bên shop còn {topic} không ạ? Giá thế nào shop ơi?",
            f"Sản phẩm {topic} này có ship về các tỉnh xa không shop? Em đang cần gấp.",
            f"Cho em xin thông tin chi tiết và giá sỉ của {topic} với ạ!",
            f"Shop tư vấn giúp em về {topic} với nhé, mẫu này có bảo hành không ạ?",
            f"Check ib em với shop ơi, em muốn đặt {topic} ạ."
        ]
        role_review_templates = [
            f"Mình vừa nhận hàng {topic} hôm qua nè, chất lượng xịn sò lắm nha mọi người, vote shop 5 sao!",
            f"Shop này bán {topic} uy tín cực kỳ, mình mua lần thứ 3 rồi vẫn ưng ý, đóng gói rất cẩn thận.",
            f"Đã trải nghiệm dịch vụ bên shop, nhân viên tư vấn nhiệt tình, {topic} dùng rất bền và ưng ý.",
            f"Mọi người yên tâm mua nhé, mình test thử rồi chuẩn đét như hình quảng cáo luôn ạ 👍",
            f"Giao hàng nhanh bất ngờ luôn, {topic} đẹp hơn cả mong đợi, sẽ ủng hộ shop dài dài."
        ]
        role_page_templates = [
            f"Dạ shop chào bạn ạ! Shop đã nhắn tin gửi đầy đủ bảng giá và ưu đãi qua inbox rồi nhé, bạn check tin nhắn chờ giúp shop nha ❤️",
            f"Dạ vâng shop sẵn hàng bạn nhé! Bên mình có ship COD toàn quốc kiểm tra trước khi nhận ạ. Bạn check inbox để shop hỗ trợ tư vấn ngay nhé!",
            f"Dạ shop cảm ơn bạn nhiều vì đã tin tưởng ủng hộ shop ạ! Chúc bạn và gia đình có một ngày thật nhiều niềm vui nhé 🥰",
            f"Dạ chào bạn, shop đã gửi thông tin chi tiết vào hộp thư tin nhắn rồi ạ, bạn kiểm tra giúp shop nhé!"
        ]
        spintax_ask = "{" + "|".join(role_ask_templates[:3]) + "}"
        spintax_review = "{" + "|".join(role_review_templates[:3]) + "}"
        spintax_page = "{" + "|".join(role_page_templates[:3]) + "}"

        return {
            "topic": topic,
            "role_ask": role_ask_templates,
            "role_review": role_review_templates,
            "role_page": role_page_templates,
            "comments": [
                {"role": "Khách hàng 1 (Hỏi mua)", "text": role_ask_templates[0]},
                {"role": "Khách hàng 2 (Khen ngợi/Feedback)", "text": role_review_templates[0]},
                {"role": "Fanpage (Shop trả lời)", "text": role_page_templates[0]}
            ],
            "combined_spintax": f"{spintax_ask}\n{spintax_review}\n{spintax_page}"
        }

ai_engine = AIContentEngine()
