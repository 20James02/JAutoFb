"""
Post Category Manager for Facebook Auto Poster
Handles hierarchical post management (Categories -> Posts).
Stores data in data/categories/ directory.
"""

import os
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

CATEGORIES_DIR = Path("data/categories")
CATEGORIES_INDEX = CATEGORIES_DIR / "index.json"

class PostCategoryManager:
    def __init__(self):
        CATEGORIES_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_default_category()

    def _ensure_default_category(self):
        """Khởi tạo mục mặc định và nạp sample_posts.jsonl nếu chưa có dữ liệu"""
        if not CATEGORIES_INDEX.exists():
            default_cat = {
                "id": "cat_default",
                "name": "Mục mẫu - Sỉ Pet Travel",
                "description": "Bài viết sỉ phụ kiện thú cưng (dữ liệu mẫu)",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            CATEGORIES_INDEX.write_text(json.dumps([default_cat], ensure_ascii=False, indent=2), encoding="utf-8")

            sample_file = Path("data/sample_posts.jsonl")
            posts = []
            if sample_file.exists():
                try:
                    for line in sample_file.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line:
                            posts.append(json.loads(line))
                except Exception as e:
                    print(f"Lỗi đọc sample_posts.jsonl: {e}")

            default_posts_file = CATEGORIES_DIR / "cat_default.json"
            default_posts_file.write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_categories(self) -> List[Dict[str, Any]]:
        self._ensure_default_category()
        try:
            cats = json.loads(CATEGORIES_INDEX.read_text(encoding="utf-8"))
        except Exception:
            cats = []

        for cat in cats:
            post_file = CATEGORIES_DIR / f"{cat['id']}.json"
            if post_file.exists():
                try:
                    p_list = json.loads(post_file.read_text(encoding="utf-8"))
                    cat["post_count"] = len(p_list)
                except Exception:
                    cat["post_count"] = 0
            else:
                cat["post_count"] = 0
        return cats

    def create_category(self, name: str, description: str = "") -> Dict[str, Any]:
        cats = self.get_categories()
        cat_id = f"cat_{int(time.time())}"
        new_cat = {
            "id": cat_id,
            "name": name.strip() or f"Mục bài viết {len(cats) + 1}",
            "description": description.strip(),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "post_count": 0
        }
        cats.append(new_cat)
        save_cats = [{k: v for k, v in c.items() if k != "post_count"} for c in cats]
        CATEGORIES_INDEX.write_text(json.dumps(save_cats, ensure_ascii=False, indent=2), encoding="utf-8")
        (CATEGORIES_DIR / f"{cat_id}.json").write_text("[]", encoding="utf-8")
        return new_cat

    def update_category(self, cat_id: str, name: str, description: str = "") -> Optional[Dict[str, Any]]:
        cats = self.get_categories()
        target = None
        for c in cats:
            if c["id"] == cat_id:
                c["name"] = name.strip() or c["name"]
                c["description"] = description.strip()
                target = c
                break
        if target:
            save_cats = [{k: v for k, v in c.items() if k != "post_count"} for c in cats]
            CATEGORIES_INDEX.write_text(json.dumps(save_cats, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def delete_category(self, cat_id: str) -> bool:
        cats = self.get_categories()
        new_cats = [c for c in cats if c["id"] != cat_id]
        if len(new_cats) == len(cats):
            return False
        save_cats = [{k: v for k, v in c.items() if k != "post_count"} for c in new_cats]
        CATEGORIES_INDEX.write_text(json.dumps(save_cats, ensure_ascii=False, indent=2), encoding="utf-8")
        p_file = CATEGORIES_DIR / f"{cat_id}.json"
        if p_file.exists():
            try:
                p_file.unlink()
            except Exception:
                pass
        return True

    def get_posts(self, cat_id: str) -> List[Dict[str, Any]]:
        p_file = CATEGORIES_DIR / f"{cat_id}.json"
        if not p_file.exists():
            return []
        try:
            return json.loads(p_file.read_text(encoding="utf-8"))
        except Exception:
            return []

    def save_post(self, cat_id: str, post_data: Dict[str, Any]) -> Dict[str, Any]:
        posts = self.get_posts(cat_id)
        post_id = post_data.get("Id")

        if post_id:
            found = False
            for idx, p in enumerate(posts):
                if p.get("Id") == post_id:
                    posts[idx].update(post_data)
                    found = True
                    post_data = posts[idx]
                    break
            if not found:
                posts.append(post_data)
        else:
            max_id = max([p.get("Id", 0) for p in posts], default=0)
            post_data["Id"] = max_id + 1
            if "Checked" not in post_data:
                post_data["Checked"] = True
            if "DateCreated" not in post_data:
                post_data["DateCreated"] = time.strftime("%Y-%m-%dT%H:%M:%S+07:00")
            posts.append(post_data)

        p_file = CATEGORIES_DIR / f"{cat_id}.json"
        p_file.write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
        return post_data

    def delete_post(self, cat_id: str, post_id: int) -> bool:
        posts = self.get_posts(cat_id)
        new_posts = [p for p in posts if p.get("Id") != post_id]
        if len(new_posts) == len(posts):
            return False
        p_file = CATEGORIES_DIR / f"{cat_id}.json"
        p_file.write_text(json.dumps(new_posts, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    def toggle_all_posts(self, cat_id: str, checked: bool) -> int:
        posts = self.get_posts(cat_id)
        for p in posts:
            p["Checked"] = checked
        p_file = CATEGORIES_DIR / f"{cat_id}.json"
        p_file.write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(posts)

    def import_jsonl(self, cat_id: str, jsonl_content: str) -> int:
        posts = self.get_posts(cat_id)
        max_id = max([p.get("Id", 0) for p in posts], default=0)
        imported_count = 0

        for line in jsonl_content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                max_id += 1
                item["Id"] = max_id
                if "Checked" not in item:
                    item["Checked"] = True
                posts.append(item)
                imported_count += 1
            except Exception:
                continue

        p_file = CATEGORIES_DIR / f"{cat_id}.json"
        p_file.write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
        return imported_count

post_cat_mgr = PostCategoryManager()
