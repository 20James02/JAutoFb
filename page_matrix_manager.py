"""
Page Matrix Manager for JAutoFb
Empowers a single operator to command a fleet of 100+ Fanpages across multiple niches
(News, Sales, Lifestyle Tips, Real Estate, Entertainment, Health, Marketing, etc.).
Provides Persona configuration, Hub-and-Spoke routing, AI Content synchronization,
and cross-page seeding orchestration.
"""

import os
import sys
import json
import time
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

DEFAULT_NICHES = [
    {
        "id": "TIN_TUC",
        "name": "📰 Tin Tức & Thời Sự 24h",
        "default_persona": "Nhà báo sắc sảo, giật tít thu hút, tóm tắt sự việc nhanh gọn, kích thích bình luận đa chiều",
        "framework": "STORY",
        "tone": "CHUYEN_NGHIEP",
        "default_keywords": "tin nóng, thời sự, xã hội, sự kiện 24h"
    },
    {
        "id": "BAN_HANG",
        "name": "🛒 Bán Hàng & Thương Mại Điện Tử",
        "default_persona": "Chủ shop tận tâm, am hiểu nỗi đau khách hàng, kích thích nhu cầu (FOMO), chốt đơn trực diện",
        "framework": "PAS",
        "tone": "BAN_HANG",
        "default_keywords": "khuyến mãi, giá tốt, chính hãng, freeship"
    },
    {
        "id": "MEO_VAT",
        "name": "💡 Mẹo Vặt & Đời Sống Thông Minh",
        "default_persona": "Chuyên gia mẹo vặt gia đình, chia sẻ giải pháp tiện ích, dễ hiểu, thực tế, ai cũng làm được",
        "framework": "BAB",
        "tone": "THUONG_NGAY",
        "default_keywords": "mẹo vặt, mẹo hay, kinh nghiệm sống, gia đình"
    },
    {
        "id": "REVIEW",
        "name": "⭐ Đánh Giá & Trải Nghiệm Thực Tế",
        "default_persona": "KOL/Reviewer công tâm, phân tích ưu nhược điểm chi tiết, tư vấn đúng nhu cầu người xem",
        "framework": "AIDA",
        "tone": "CHUYEN_NGHIEP",
        "default_keywords": "review, đánh giá, trải nghiệm, mở hộp"
    },
    {
        "id": "GIAI_TRI",
        "name": "🎬 Giải Trí, Hài Hước & Viral Memes",
        "default_persona": "Gen Z dí dỏm, bắt trend nhanh, phong cách cà khịa hài hước, tạo trào lưu bình luận sôi nổi",
        "framework": "STORY",
        "tone": "HAI_HUOC",
        "default_keywords": "hài hước, cười vỡ bụng, clip viral, meme vui"
    },
    {
        "id": "BAT_DONG_SAN",
        "name": "🏢 Bất Động Sản & Tài Chính Đầu Tư",
        "default_persona": "Chuyên viên tư vấn tài chính & BĐS kinh nghiệm, thông tin minh bạch, phân tích tiềm năng sinh lời",
        "framework": "PAS",
        "tone": "CHUYEN_NGHIEP",
        "default_keywords": "nhà đất, bất động sản, đầu tư sinh lời, sổ đỏ"
    },
    {
        "id": "SUC_KHOE",
        "name": "🧘 Sức Khỏe, Dinh Dưỡng & Yoga",
        "default_persona": "Bác sĩ / Chuyên gia dinh dưỡng chu đáo, phân tích khoa học, hướng dẫn lối sống lành mạnh",
        "framework": "BAB",
        "tone": "CHUYEN_NGHIEP",
        "default_keywords": "sức khỏe, ăn sạch, giảm cân, sống khỏe"
    },
    {
        "id": "LAM_DEP",
        "name": "💄 Làm Đẹp, Skincare & Thời Trang",
        "default_persona": "Beauty blogger sành điệu, chia sẻ bí quyết chăm sóc da và phối đồ theo xu hướng mới nhất",
        "framework": "AIDA",
        "tone": "THUONG_NGAY",
        "default_keywords": "skincare, làm đẹp, dưỡng trắng, thời trang"
    },
    {
        "id": "MARKETING",
        "name": "👨‍💻 Kinh Doanh, Khởi Nghiệp & Marketing",
        "default_persona": "Chiến lược gia tăng trưởng thực chiến, đúc kết bài học kinh doanh ngắn gọn, sâu sắc và thực tế",
        "framework": "FAB",
        "tone": "CHUYEN_NGHIEP",
        "default_keywords": "kinh doanh, marketing, khởi nghiệp, doanh thu"
    },
    {
        "id": "TAM_SU",
        "name": "💬 Góc Tâm Sự & Tình Cảm Gia Đình",
        "default_persona": "Người lắng nghe chân thành, thấu hiểu tâm lý, đưa ra góc nhìn ấm áp, gợi mở sự đồng cảm",
        "framework": "STORY",
        "tone": "THUONG_NGAY",
        "default_keywords": "tâm sự, gia đình, tình yêu, cuộc sống"
    }
]

class PageMatrixManager:
    def __init__(self):
        self.niches = DEFAULT_NICHES

    def get_matrix_file(self, account_id: str) -> Path:
        return DATA_DIR / f"page_matrix_{account_id}.json"

    def get_pages_file(self, account_id: str) -> Path:
        return DATA_DIR / f"pages_{account_id}.json"

    def load_matrix(self, account_id: str) -> Dict[str, Any]:
        """Tải dữ liệu ma trận của một account, kết hợp dữ liệu quét fanpage gốc"""
        pages_file = self.get_pages_file(account_id)
        raw_pages = []
        if pages_file.exists():
            try:
                raw_pages = json.loads(pages_file.read_text(encoding="utf-8"))
            except Exception:
                raw_pages = []

        matrix_file = self.get_matrix_file(account_id)
        matrix_meta = {}
        if matrix_file.exists():
            try:
                matrix_meta = json.loads(matrix_file.read_text(encoding="utf-8"))
            except Exception:
                matrix_meta = {}

        # Merge raw pages with matrix meta
        enriched_pages = []
        niche_counter = {}
        hub_count = 0
        spoke_count = 0

        for p in raw_pages:
            p_url = p.get("url", "")
            meta = matrix_meta.get(p_url, {})
            
            niche_id = meta.get("niche") or "BAN_HANG"
            niche_info = next((n for n in self.niches if n["id"] == niche_id), self.niches[1])
            hub_role = meta.get("hub_role") or "spoke"

            if hub_role == "hub":
                hub_count += 1
            else:
                spoke_count += 1

            niche_counter[niche_id] = niche_counter.get(niche_id, 0) + 1

            enriched = {
                "id": p.get("id"),
                "name": p.get("name"),
                "url": p_url,
                "niche": niche_id,
                "niche_name": niche_info["name"],
                "persona": meta.get("persona") or niche_info["default_persona"],
                "framework": meta.get("framework") or niche_info["framework"],
                "tone": meta.get("tone") or niche_info["tone"],
                "hub_role": hub_role, # "hub" (Page Mẹ) hoặc "spoke" (Page Vệ Tinh)
                "target_hub_url": meta.get("target_hub_url") or "",
                "auto_seed": meta.get("auto_seed", True),
                "daily_posts": meta.get("daily_posts", 2),
                "target_group_keywords": meta.get("target_group_keywords") or niche_info["default_keywords"],
                "category_id": meta.get("category_id") or "",
                "notes": meta.get("notes") or ""
            }
            enriched_pages.append(enriched)

        return {
            "account_id": account_id,
            "total_pages": len(enriched_pages),
            "hub_count": hub_count,
            "spoke_count": spoke_count,
            "niche_stats": niche_counter,
            "niches": self.niches,
            "pages": enriched_pages
        }

    def save_matrix_meta(self, account_id: str, matrix_meta: Dict[str, Any]):
        matrix_file = self.get_matrix_file(account_id)
        matrix_file.write_text(json.dumps(matrix_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def update_page_meta(self, account_id: str, page_url: str, updates: Dict[str, Any]) -> bool:
        matrix_file = self.get_matrix_file(account_id)
        matrix_meta = {}
        if matrix_file.exists():
            try:
                matrix_meta = json.loads(matrix_file.read_text(encoding="utf-8"))
            except Exception:
                matrix_meta = {}

        if page_url not in matrix_meta:
            matrix_meta[page_url] = {}

        matrix_meta[page_url].update(updates)
        self.save_matrix_meta(account_id, matrix_meta)
        return True

    def batch_update_pages(self, account_id: str, page_urls: List[str], updates: Dict[str, Any]) -> int:
        matrix_file = self.get_matrix_file(account_id)
        matrix_meta = {}
        if matrix_file.exists():
            try:
                matrix_meta = json.loads(matrix_file.read_text(encoding="utf-8"))
            except Exception:
                matrix_meta = {}

        updated_count = 0
        for url in page_urls:
            if url not in matrix_meta:
                matrix_meta[url] = {}
            matrix_meta[url].update(updates)
            updated_count += 1

        self.save_matrix_meta(account_id, matrix_meta)
        return updated_count

    def generate_page_content(
        self,
        account_id: str,
        page_url: str,
        topic: Optional[str] = None,
        count: int = 1,
        save_to_category: bool = True
    ) -> List[Dict[str, Any]]:
        """Sinh nội dung tự động bằng AI áp dụng đúng Persona và Ngách của Fanpage đó"""
        data = self.load_matrix(account_id)
        target_page = next((p for p in data["pages"] if p["url"] == page_url), None)
        if not target_page:
            return []

        from ai_content_engine import ai_engine
        from post_category_manager import post_cat_mgr

        niche_id = target_page.get("niche", "BAN_HANG")
        persona = target_page.get("persona") or "Chuyên gia thân thiện"
        framework = target_page.get("framework") or "PAS"
        tone = target_page.get("tone") or "BAN_HANG"
        page_name = target_page.get("name") or "Fanpage"

        topic_prompt = topic or f"Nội dung chia sẻ giá trị hữu ích về {target_page['niche_name']} dành cho cộng đồng theo dõi trang '{page_name}'"

        cta_text = f"\n\n👉 Xem thêm thông tin chi tiết và ưu đãi tại: {target_page['target_hub_url']}" if target_page.get("hub_role") == "spoke" and target_page.get("target_hub_url") else f"\n\n👉 Đừng quên bấm Theo Dõi trang {page_name} để cập nhật thông tin mỗi ngày nhé!"

        generated_posts = []
        for i in range(count):
            custom_instruction = f"Đóng vai: {persona}. Viết bài chuẩn ngách {target_page['niche_name']} cho Fanpage {page_name}. Lồng ghép từ khóa: {target_page.get('target_group_keywords', '')}"
            ai_res = ai_engine.generate_post_content(
                topic=topic_prompt,
                framework=framework,
                tone=tone,
                custom_prompt=custom_instruction
            )
            raw_content = ai_res.get("content", "") if isinstance(ai_res, dict) else str(ai_res)
            full_content = (raw_content + cta_text).strip()

            post_item = {
                "Title": f"[{target_page['niche_name'][:10]}] {page_name} - Bài viết #{i+1}",
                "Content": full_content,
                "Checked": True,
                "Niche": niche_id,
                "PageUrl": page_url,
                "PageName": page_name,
                "CreatedAt": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            generated_posts.append(post_item)

            if save_to_category:
                # Tìm hoặc tạo Danh mục bài viết riêng cho ngách hoặc cho Page này
                cat_id = target_page.get("category_id")
                if not cat_id:
                    # Tự động gán/tạo danh mục bài viết cho page này
                    cat_name = f"Ngách: {target_page['niche_name']} ({page_name[:15]})"
                    existing_cats = post_cat_mgr.get_categories()
                    found_cat = next((c for c in existing_cats if c["name"] == cat_name), None)
                    if found_cat:
                        cat_id = found_cat["id"]
                    else:
                        new_cat = post_cat_mgr.create_category(name=cat_name, description=f"Kho nội dung tự động cho Fanpage {page_name}")
                        cat_id = new_cat["id"]
                    # Lưu lại cat_id vào matrix meta của page
                    self.update_page_meta(account_id, page_url, {"category_id": cat_id})

                post_cat_mgr.save_post(cat_id, {
                    "Title": post_item["Title"],
                    "Content": post_item["Content"],
                    "Checked": True
                })

        return generated_posts


page_matrix_mgr = PageMatrixManager()
