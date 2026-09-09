'''
Deep Group Scanner Engine for Facebook Groups
Supports:
- Multi-threaded scanning (2 to 5 concurrent browser tabs)
- Extracting members count, posts in last 24h, moderation status, privacy, location, engagement rate
- Non-blocking execution with real-time status reporting
'''

import os
import re
import sys
import time
import json
import threading
from pathlib import Path
from queue import Queue
from typing import List, Dict, Any, Optional, Callable
from playwright.sync_api import sync_playwright
from fast_group_scraper import parse_members_vn, clean_fb_group_name, extract_group_activity

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def parse_number(text: str) -> int:
    """Converts 50,9K or 1.2M or 5,900 into an integer"""
    if not text:
        return 0
    cnt, _ = parse_members_vn(text)
    if cnt > 0:
        return cnt
    clean_digits = re.sub(r"[^\d]", "", text)
    return int(clean_digits) if clean_digits else 0

class DeepGroupScanner:
    def __init__(self, log_callback: Optional[Callable[[str, str], None]] = None):
        self.log_callback = log_callback
        self.state = "IDLE"  # IDLE, SCANNING, STOPPED
        self._stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.stats = {
            "state": "IDLE",
            "total": 0,
            "scanned": 0,
            "success": 0,
            "failed": 0,
            "progress_percent": 0,
            "current_group": ""
        }
        self.enriched_groups: List[Dict[str, Any]] = []

    def log(self, msg: str, lvl: str = "info"):
        timestamp = time.strftime("%H:%M:%S")
        log_line = f"[{timestamp}] [DEEP_SCAN] {msg}"
        print(log_line)
        if self.log_callback:
            self.log_callback(log_line, lvl)

    def start_scan(
        self,
        groups: List[Dict[str, Any]],
        profile_dir: str,
        acc_name: str,
        role_type: str = "personal",
        role_url: Optional[str] = None,
        role_name: Optional[str] = None,
        concurrency: int = 3,
        on_complete: Optional[Callable[[List[Dict[str, Any]]], None]] = None
    ):
        if self.state == "SCANNING":
            self.log("Tiến trình quét chi tiết đang chạy!", "warning")
            return

        if not groups:
            self.log("Danh sách nhóm trống, không có nhóm để quét chi tiết!", "warning")
            return

        self._stop_event.clear()
        self.state = "SCANNING"
        self.stats = {
            "state": "SCANNING",
            "total": len(groups),
            "scanned": 0,
            "success": 0,
            "failed": 0,
            "progress_percent": 0,
            "current_group": ""
        }
        self.enriched_groups = [dict(g) for g in groups]

        self.thread = threading.Thread(
            target=self._worker_runner,
            args=(profile_dir, acc_name, concurrency, on_complete),
            daemon=True
        )
        self.thread.start()

    def stop_scan(self):
        if self.state == "SCANNING":
            self.state = "STOPPED"
            self.stats["state"] = "STOPPED"
            self._stop_event.set()
            self.log("Đã gửi yêu cầu dừng quét chi tiết nhóm.", "warning")

    def _worker_runner(
        self,
        profile_dir: str,
        acc_name: str,
        concurrency: int,
        on_complete: Optional[Callable[[List[Dict[str, Any]]], None]]
    ):
        self.log(f"Bắt đầu quét sâu {len(self.enriched_groups)} nhóm với {concurrency} luồng song song (Chạy ngầm)...", "info")
        profile_path = Path(profile_dir).resolve()
        pw = None
        context = None

        try:
            from account_manager import get_standard_chrome_args, get_natural_user_agent
            pw = sync_playwright().start()
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                channel="chrome",
                headless=True,
                ignore_default_args=["--enable-automation"],
                user_agent=get_natural_user_agent(),
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
                args=get_standard_chrome_args(headless=True)
            )

            # Queue of group indices
            queue = Queue()
            for idx in range(len(self.enriched_groups)):
                queue.put(idx)

            worker_threads = []
            num_workers = min(max(1, concurrency), 5)

            for w_id in range(num_workers):
                # Each worker gets its own page (tab) in the persistent context
                page = context.new_page()
                t = threading.Thread(target=self._single_worker, args=(w_id + 1, page, queue), daemon=True)
                worker_threads.append(t)
                t.start()

            for t in worker_threads:
                t.join()

            self.state = "IDLE"
            self.stats["state"] = "IDLE"
            self.stats["progress_percent"] = 100
            self.log(f"QUÉT SÂU HOÀN TẤT! Đã quét: {self.stats['scanned']}/{self.stats['total']} nhóm.", "success")

            if on_complete:
                on_complete(self.enriched_groups)

        except Exception as ex:
            self.log(f"Lỗi tiến trình quét sâu: {ex}", "error")
            self.state = "IDLE"
            self.stats["state"] = "IDLE"
        finally:
            if context:
                try: context.close()
                except Exception: pass
            if pw:
                try: pw.stop()
                except Exception: pass

    def _single_worker(self, worker_id: int, page, queue: Queue):
        while not queue.empty() and not self._stop_event.is_set():
            try:
                idx = queue.get_nowait()
            except Exception:
                break

            group = self.enriched_groups[idx]
            g_name = group.get("name", "Nhóm")
            g_url = group.get("url", "").rstrip("/")

            with self.lock:
                self.stats["current_group"] = g_name

            details = self._inspect_single_group(page, g_url, g_name, worker_id)

            with self.lock:
                # Update group fields
                group.update(details)
                self.stats["scanned"] += 1
                if details.get("_success", False):
                    self.stats["success"] += 1
                else:
                    self.stats["failed"] += 1

                total = max(1, self.stats["total"])
                self.stats["progress_percent"] = min(100, int((self.stats["scanned"] / total) * 100))

            queue.task_done()
            time.sleep(1)

        try:
            page.close()
        except Exception:
            pass

    def _inspect_single_group(self, page, g_url: str, g_name: str, worker_id: int) -> Dict[str, Any]:
        result = {
            "members_str": "Chưa rõ",
            "members_count": 0,
            "privacy": "Công khai",
            "posts_today_str": "0 bài mới",
            "posts_today": 0,
            "posts_month": 0,
            "moderation": "Tự do đăng",
            "engagement_rate": "80%",
            "engagement_score": 80,
            "location": "Việt Nam",
            "_success": False
        }

        if not g_url or not g_url.startswith("http"):
            return result

        about_url = f"{g_url}/about/"
        try:
            # Navigate to About page
            page.goto(about_url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)

            body_text = page.evaluate("() => document.body.innerText || ''")
            lines = [l.strip() for l in body_text.split("\n") if l.strip()]

            # Clean Name from page title
            try:
                t_str = page.title()
                c_name = clean_fb_group_name(t_str)
                if c_name and c_name != "Facebook":
                    result["name"] = c_name
            except Exception:
                pass

            # 1. Members count
            m_cnt, m_str = parse_members_vn(body_text)
            if m_cnt > 0:
                result["members_count"] = m_cnt
                result["members_str"] = m_str
            else:
                for l in lines:
                    m = re.search(r'([\d.,]+)\s*([KMBkmb]?)\s*thành viên', l, re.IGNORECASE)
                    if not m:
                        m = re.search(r'([\d.,]+)\s*([KMBkmb]?)\s*members?', l, re.IGNORECASE)
                    if m:
                        num_txt = f"{m.group(1)}{m.group(2)}"
                        result["members_str"] = num_txt.strip()
                        result["members_count"] = parse_number(num_txt)
                        break

            # 2. Privacy
            if any("riêng tư" in l.lower() or "private" in l.lower() for l in lines[:20]):
                result["privacy"] = "Riêng tư"
            else:
                result["privacy"] = "Công khai"

            # 3. Posts & Engagement Activity
            try:
                html_source = page.content()
                act = extract_group_activity(html_source)
                result["posts_today"] = act["posts_today"]
                result["posts_month"] = act["posts_month"]
                result["posts_today_str"] = act["posts_today_str"]
                result["engagement_score"] = act["engagement_score"]
                result["engagement_rate"] = act["engagement_rate"]
                result["new_members_week"] = act["new_members_week"]
            except Exception:
                pass

            # 4. Moderation (Kiểm duyệt bài trước khi đăng)
            mod_keywords = [
                "quản trị viên phê duyệt", "phê duyệt bài", "bài viết cần phê duyệt",
                "đang chờ quản trị viên phê duyệt", "pending approval", "admin approval"
            ]
            has_mod = any(any(kw in l.lower() for kw in mod_keywords) for l in lines)
            if has_mod:
                result["moderation"] = "Cần duyệt"
            else:
                result["moderation"] = "Tự do đăng"

            # 5. Location
            for l in lines:
                if any(city in l for city in ["Hà Nội", "Hồ Chí Minh", "TP.HCM", "Đà Nẵng", "Cần Thơ", "Biên Hoà", "Việt Nam", "Cambodia", "Siem Reap", "Phnom Penh"]):
                    result["location"] = l[:30]
                    break

            result["_success"] = True
            self.log(f"[W{worker_id}] Đã quét: {g_name[:25]}... -> {result['members_str']} TV, {result['posts_today_str']}, {result['moderation']}", "info")

        except Exception as ex:
            self.log(f"[W{worker_id}] Lỗi đọc nhóm {g_name[:20]}: {ex}", "warning")

        return result

# Singleton instance
group_deep_scanner = DeepGroupScanner()
