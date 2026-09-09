"""
Published Posts Manager for Facebook Automation
Stores, categorizes, and manages the history of published posts (group, personal wall, fanpage).
Provides tracking for post approval status and bump (comment up) history.
Data is persisted in data/published_posts.json
"""

import json
import time
import uuid
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

DATA_FILE = Path("data/published_posts.json")

class PublishedPostsManager:
    def __init__(self, data_file: Path = DATA_FILE):
        self.data_file = data_file
        self._lock = threading.Lock()
        self._posts: List[Dict[str, Any]] = []
        self._last_mtime: float = 0.0
        self._ensure_loaded_unlocked()

    def _ensure_loaded_unlocked(self):
        if not self.data_file.exists():
            try:
                self.data_file.parent.mkdir(parents=True, exist_ok=True)
                self._posts = []
                self._save_unlocked()
            except Exception:
                pass
            return

        try:
            mtime = self.data_file.stat().st_mtime
            if mtime != self._last_mtime:
                content = self.data_file.read_text(encoding="utf-8")
                self._posts = json.loads(content) if content.strip() else []
                self._last_mtime = mtime
        except Exception as e:
            print(f"[PublishedPostsManager] Lỗi đọc file dữ liệu: {e}")

    def _save_unlocked(self):
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            self.data_file.write_text(json.dumps(self._posts, ensure_ascii=False, indent=2), encoding="utf-8")
            if self.data_file.exists():
                self._last_mtime = self.data_file.stat().st_mtime
        except Exception as e:
            print(f"[PublishedPostsManager] Lỗi lưu file dữ liệu: {e}")

    def add_post(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Thêm một bài viết đã đăng vào lịch sử lưu trữ.
        """
        with self._lock:
            self._ensure_loaded_unlocked()
            now_dt = datetime.now()
            entry = {
                "id": str(uuid.uuid4())[:8],
                "post_url": data.get("post_url", "").strip(),
                "post_id": data.get("post_id"),
                "post_title": data.get("post_title", "").strip()[:150],
                "target_type": data.get("target_type", "group"),  # 'group', 'personal', 'page'
                "target_name": data.get("target_name", "Không rõ").strip(),
                "target_url": data.get("target_url", "").strip(),
                "account_id": data.get("account_id", ""),
                "account_name": data.get("account_name", ""),
                "role_type": data.get("role_type", "personal"),
                "role_name": data.get("role_name", ""),
                "status": data.get("status", "approved"),  # 'approved' (Đã duyệt), 'pending' (Chờ duyệt), 'unknown'
                "is_bumped": False,
                "bump_count": 0,
                "last_bumped_at": None,
                "last_bump_comment": None,
                "created_at": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp": int(now_dt.timestamp())
            }

            # Kiểm tra nếu bài đăng trùng URL thì cập nhật thay vì thêm trùng lặp
            if entry["post_url"] and entry["post_url"].startswith("http") and "facebook.com" in entry["post_url"]:
                for idx, existing in enumerate(self._posts):
                    if existing.get("post_url") == entry["post_url"]:
                        existing.update({
                            "status": entry["status"],
                            "target_name": entry["target_name"] or existing.get("target_name"),
                            "created_at": entry["created_at"],
                            "timestamp": entry["timestamp"]
                        })
                        self._save_unlocked()
                        return existing

            # Chèn bài mới lên đầu danh sách (mới nhất trước)
            self._posts.insert(0, entry)
            # Giới hạn tối đa 2000 bài trong lịch sử
            if len(self._posts) > 2000:
                self._posts = self._posts[:2000]
            self._save_unlocked()
            return entry

    def get_posts(
        self,
        target_type: Optional[str] = None,
        status: Optional[str] = None,
        is_bumped: Optional[bool] = None,
        account_id: Optional[str] = None,
        search: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Lấy danh sách bài đã đăng có phân loại và tìm kiếm.
        Luôn sắp xếp mới nhất lên đầu (theo timestamp giảm dần).
        """
        with self._lock:
            self._ensure_loaded_unlocked()
            results = list(self._posts)

        if target_type and target_type != "all":
            results = [p for p in results if p.get("target_type") == target_type]

        if status and status != "all":
            results = [p for p in results if p.get("status") == status]

        if is_bumped is not None:
            results = [p for p in results if p.get("is_bumped", False) == is_bumped]

        if account_id and account_id != "all":
            results = [p for p in results if p.get("account_id") == account_id]

        if search and search.strip():
            kw = search.strip().lower()
            results = [
                p for p in results
                if kw in p.get("target_name", "").lower()
                or kw in p.get("post_url", "").lower()
                or kw in p.get("post_title", "").lower()
                or kw in p.get("account_name", "").lower()
            ]

        # Đảm bảo sắp xếp mới nhất lên đầu
        results.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return results

    def mark_as_bumped(self, post_url_or_id: str, comment_text: Optional[str] = None) -> bool:
        """
        Ghi nhận bài viết đã được comment up (bump) thành công.
        Cập nhật is_bumped=True, tăng bump_count, lưu mốc thời gian.
        """
        with self._lock:
            self._ensure_loaded_unlocked()
            updated = False
            clean_input = post_url_or_id.strip() if post_url_or_id else ""
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            for p in self._posts:
                # So khớp theo ID hoặc so khớp theo URL bài viết
                if p.get("id") == clean_input or (clean_input and clean_input in p.get("post_url", "")):
                    p["is_bumped"] = True
                    p["bump_count"] = p.get("bump_count", 0) + 1
                    p["last_bumped_at"] = now_str
                    if comment_text:
                        p["last_bump_comment"] = comment_text[:100]
                    updated = True

            if updated:
                self._save_unlocked()
            return updated

    def update_status(self, post_id: str, new_status: str) -> bool:
        """
        Cập nhật trạng thái duyệt bài ('approved' hoặc 'pending').
        """
        with self._lock:
            self._ensure_loaded_unlocked()
            for p in self._posts:
                if p.get("id") == post_id:
                    p["status"] = new_status
                    self._save_unlocked()
                    return True
            return False

    def delete_post(self, post_id: str) -> bool:
        """Xóa 1 bài viết khỏi danh sách lịch sử"""
        with self._lock:
            self._ensure_loaded_unlocked()
            initial_len = len(self._posts)
            self._posts = [p for p in self._posts if p.get("id") != post_id]
            if len(self._posts) < initial_len:
                self._save_unlocked()
                return True
            return False

    def clear_posts(self, target_type: Optional[str] = None) -> int:
        """Xóa toàn bộ bài viết (hoặc theo loại mục)"""
        with self._lock:
            self._ensure_loaded_unlocked()
            if not target_type or target_type == "all":
                count = len(self._posts)
                self._posts = []
            else:
                initial_len = len(self._posts)
                self._posts = [p for p in self._posts if p.get("target_type") != target_type]
                count = initial_len - len(self._posts)
            self._save_unlocked()
            return count

# Global instance
published_posts_mgr = PublishedPostsManager()
