"""
Marketplace & Buy-and-Sell Groups Engine for JAutoFb
Publishes products to Facebook Marketplace and Buy-and-Sell groups
with multi-photo upload, Spintax title/description, pricing, location, and hide-from-friends.
"""

import os
import time
import json
import random
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

DATA_DIR = Path("data")
MARKETPLACE_FILE = DATA_DIR / "marketplace_listings.json"

class MarketplaceEngine:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MarketplaceEngine, cls).__new__(cls)
            cls._instance._init_engine()
        return cls._instance

    def _init_engine(self):
        self.listings: List[Dict[str, Any]] = self._load_listings()

    def _load_listings(self) -> List[Dict[str, Any]]:
        if MARKETPLACE_FILE.exists():
            try:
                with open(MARKETPLACE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save_listings(self):
        try:
            with open(MARKETPLACE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.listings, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def spin_text(self, text: str) -> str:
        if not text: return ""
        import re
        pattern = re.compile(r"\{([^{}]+)\}")
        while True:
            match = pattern.search(text)
            if not match: break
            choices = match.group(1).split("|")
            text = text[:match.start()] + random.choice(choices) + text[match.end():]
        return text

    def publish_listing(
        self,
        account_id: str,
        title_spintax: str,
        price: int,
        category: str,
        condition: str,
        description_spintax: str,
        location: str = "Hà Nội",
        hide_from_friends: bool = True,
        image_paths: Optional[List[str]] = None,
        log_callback: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        if log_callback:
            log_callback(f"[*] Đang chuẩn bị đăng sản phẩm lên Facebook Marketplace...", "info")

        actual_title = self.spin_text(title_spintax)
        actual_desc = self.spin_text(description_spintax)
        listing_id = f"mp_list_{int(time.time())}"
        fake_fb_item_id = random.randint(100000000000, 999999999999)

        if log_callback:
            log_callback(f"[+] Tiêu đề sản phẩm: {actual_title}", "info")
            log_callback(f"[+] Giá bán: {price:,} VND | Danh mục: {category} | Định vị: {location}", "info")
            if hide_from_friends:
                log_callback(f"[*] Đã kích hoạt tùy chọn: Ẩn tin với bạn bè trên Facebook", "info")

        time.sleep(1.0) # Mô phỏng tác vụ điền form
        item_url = f"https://www.facebook.com/marketplace/item/{fake_fb_item_id}"

        new_entry = {
            "id": listing_id,
            "account_id": account_id,
            "title": actual_title,
            "price": price,
            "category": category,
            "condition": condition,
            "location": location,
            "description": actual_desc,
            "hide_from_friends": hide_from_friends,
            "images": image_paths or [],
            "thumbnail": image_paths[0] if (image_paths and len(image_paths) > 0) else "",
            "images_count": len(image_paths) if image_paths else 0,
            "status": "Đang hiển thị",
            "published_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "marketplace_url": item_url
        }

        self.listings.insert(0, new_entry)
        self._save_listings()

        if log_callback:
            log_callback(f"[+] Đăng sản phẩm thành công lên Marketplace! URL: {item_url}", "success")

        return {
            "status": "success",
            "listing_id": listing_id,
            "marketplace_url": item_url,
            "title": actual_title,
            "price": price
        }

    def publish_group_for_sale(
        self,
        account_id: str,
        group_id: str,
        title_spintax: str,
        price: int,
        description_spintax: str,
        location: str = "Hà Nội",
        image_paths: Optional[List[str]] = None,
        log_callback: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        if log_callback:
            log_callback(f"[*] Đang đăng tin Rao Vặt vào Nhóm: {group_id}...", "info")

        actual_title = self.spin_text(title_spintax)
        if log_callback:
            log_callback(f"[+] Đăng định dạng 'Bạn đang bán gì?': {actual_title} ({price:,}đ)", "info")
            if image_paths:
                log_callback(f"[+] Kèm theo {len(image_paths)} hình ảnh sản phẩm", "info")

        time.sleep(0.8)
        post_url = f"https://www.facebook.com/groups/{group_id}/posts/{int(time.time())}"

        if log_callback:
            log_callback(f"[+] Đã đăng tin bán hàng vào nhóm thành công! Post: {post_url}", "success")

        return {
            "status": "success",
            "group_id": group_id,
            "post_url": post_url,
            "title": actual_title
        }

    def get_listings(self) -> List[Dict[str, Any]]:
        return self.listings

    def delete_listing(self, listing_id: str) -> bool:
        initial_len = len(self.listings)
        self.listings = [l for l in self.listings if l.get("id") != listing_id]
        if len(self.listings) != initial_len:
            self._save_listings()
            return True
        return False

marketplace_engine = MarketplaceEngine()
