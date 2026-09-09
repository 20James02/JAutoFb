"""
Video Hash Mutator for Multi-Page Facebook & Instagram Reels Marketing
Anti-Duplicate Content Engine:
Transforms video files with unique MD5/SHA-256 fingerprints, safe metadata padding,
and page-specific tagging to bypass Meta's unoriginal content detection.
"""

import os
import random
import hashlib
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

MUTATED_DIR = Path("data/uploads/mutated_reels")
MUTATED_DIR.mkdir(parents=True, exist_ok=True)

class VideoMutator:
    def __init__(self):
        self.output_dir = MUTATED_DIR

    @staticmethod
    def get_md5(filepath: Path) -> str:
        h = hashlib.md5()
        with open(filepath, "rb") as f:
            while chunk := f.read(16384):
                h.update(chunk)
        return h.hexdigest()

    def mutate_video_for_page(
        self,
        input_video_path: str,
        page_name: str,
        niche: str = "GENERAL"
    ) -> Dict[str, Any]:
        """Tạo một bản sao video độc bản cho 1 Fanpage cụ thể với MD5 hoàn toàn mới"""
        in_p = Path(input_video_path)
        if not in_p.exists():
            raise FileNotFoundError(f"Video không tồn tại: {input_video_path}")

        orig_md5 = self.get_md5(in_p)

        # Đặt tên file xuất riêng biệt
        safe_name = "".join(c for c in page_name if c.isalnum() or c in (" ", "_", "-")).strip()[:20]
        timestamp = int(time.time() * 1000)
        out_filename = f"reel_{safe_name}_{timestamp}_{random.randint(1000, 9999)}{in_p.suffix}"
        out_p = self.output_dir / out_filename

        # Đọc dữ liệu gốc
        with open(in_p, "rb") as f_in:
            data = bytearray(f_in.read())

        # Tạo metadata tag và random noise padding
        # MP4 standard video players ignore trailing bytes after atoms, while MD5 changes 100%
        tag = f"\n[JAutoFb_Reels_{safe_name}_{niche}_{random.randint(100000, 999999)}]\n".encode("utf-8")
        noise = bytearray(os.urandom(random.randint(64, 512)))

        data.extend(tag)
        data.extend(noise)

        # Lưu file mới
        with open(out_p, "wb") as f_out:
            f_out.write(data)

        mutated_md5 = self.get_md5(out_p)

        return {
            "page_name": page_name,
            "niche": niche,
            "original_path": str(in_p.resolve()),
            "original_md5": orig_md5,
            "mutated_path": str(out_p.resolve()),
            "mutated_md5": mutated_md5,
            "size_diff_bytes": len(tag) + len(noise)
        }

    def batch_mutate_for_pages(
        self,
        input_video_path: str,
        pages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Nhân bản video cho một danh sách nhiều Fanpage cùng lúc"""
        results = []
        for p in pages:
            p_name = p.get("name", "Fanpage")
            p_niche = p.get("niche", "GENERAL")
            try:
                res = self.mutate_video_for_page(input_video_path, p_name, p_niche)
                results.append(res)
            except Exception as e:
                print(f"[!] Lỗi mutate video cho {p_name}: {e}")
        return results

    def cleanup_old_mutated_files(self, max_age_hours: int = 24):
        """Xóa các video tạm đã tạo trước đó để tiết kiệm dung lượng ổ cứng"""
        now = time.time()
        for f in self.output_dir.glob("*.*"):
            if f.is_file():
                age_hours = (now - f.stat().st_mtime) / 3600
                if age_hours > max_age_hours:
                    try:
                        f.unlink()
                    except Exception:
                        pass

video_mutator = VideoMutator()
