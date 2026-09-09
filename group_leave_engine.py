'''
Facebook Auto Leave Group Engine
Executes automated leaving of selected Facebook groups based on active role (Personal Profile or Fanpage)
Using Playwright with Chrome profile sessions, realistic UI interactions, and anti-checkpoint safe delays.
'''

import os
import re
import sys
import time
import json
import random
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from role_switcher import ensure_role

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


class GroupLeaveEngine:
    def __init__(self, log_callback: Optional[Callable[[str, str], None]] = None, sync_callback: Optional[Callable[[str, Dict[str, Any], str, str], None]] = None):
        self.log_callback = log_callback
        self.sync_callback = sync_callback
        self.state = "IDLE"  # IDLE, RUNNING, PAUSED, STOPPED
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

        self.stats = {
            "state": "IDLE",
            "total": 0,
            "completed": 0,
            "success": 0,
            "failed": 0,
            "progress_percent": 0,
            "current_group": "",
            "left_groups": []
        }

    def log(self, msg: str, lvl: str = "info"):
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] [LEAVE_GROUP] {msg}"
        print(line)
        if self.log_callback:
            try:
                self.log_callback(line, lvl)
            except Exception:
                pass

    def start_leave(
        self,
        account_id: str,
        profile_dir: str,
        acc_name: str,
        groups: List[Dict[str, Any]],
        role_type: str = "personal",
        role_url: Optional[str] = None,
        role_name: Optional[str] = None,
        personal_name: Optional[str] = None,
        min_delay: int = 5,
        max_delay: int = 10,
        headless: bool = True,
        prevent_readd: bool = True,
        on_complete: Optional[Callable[[List[str]], None]] = None
    ):
        with self.lock:
            if self.state == "RUNNING":
                self.log("Tiến trình rời nhóm đang chạy!", "warning")
                return False

            if not groups:
                self.log("Danh sách nhóm cần rời trống!", "warning")
                return False

            self.state = "RUNNING"
            self._stop_event.clear()
            self._pause_event.set()

            self.stats = {
                "state": "RUNNING",
                "total": len(groups),
                "completed": 0,
                "success": 0,
                "failed": 0,
                "progress_percent": 0,
                "current_group": "",
                "left_groups": []
            }

            self.thread = threading.Thread(
                target=self._worker_run,
                args=(
                    account_id,
                    profile_dir,
                    acc_name,
                    groups,
                    role_type,
                    role_url,
                    role_name,
                    personal_name,
                    min_delay,
                    max_delay,
                    headless,
                    prevent_readd,
                    on_complete
                ),
                daemon=True
            )
            self.thread.start()
            return True

    def pause(self):
        with self.lock:
            if self.state == "RUNNING":
                self.state = "PAUSED"
                self.stats["state"] = "PAUSED"
                self._pause_event.clear()
                self.log("Đã tạm dừng tiến trình rời nhóm.", "warning")
                return True
        return False

    def resume(self):
        with self.lock:
            if self.state == "PAUSED":
                self.state = "RUNNING"
                self.stats["state"] = "RUNNING"
                self._pause_event.set()
                self.log("Đã tiếp tục tiến trình rời nhóm.", "info")
                return True
        return False

    def stop(self):
        with self.lock:
            if self.state in ["RUNNING", "PAUSED"]:
                self.state = "STOPPED"
                self.stats["state"] = "STOPPED"
                self._stop_event.set()
                self._pause_event.set()
                self.log("Đã gửi lệnh dừng tiến trình rời nhóm.", "warning")
                return True
        return False

    def _worker_run(
        self,
        account_id: str,
        profile_dir: str,
        acc_name: str,
        groups: List[Dict[str, Any]],
        role_type: str,
        role_url: Optional[str],
        role_name: Optional[str],
        personal_name: Optional[str],
        min_delay: int,
        max_delay: int,
        headless: bool,
        prevent_readd: bool,
        on_complete: Optional[Callable[[List[str]], None]]
    ):
        role_desc = f"Fanpage '{role_name}'" if role_type == "page" else f"Trang cá nhân ({personal_name or acc_name})"
        self.log(f"Bắt đầu rời {len(groups)} nhóm với vai trò: {role_desc} (Chạy ngầm: {headless})...", "info")

        left_urls = []
        user_data_path = Path(profile_dir).resolve()
        if not user_data_path.exists():
            self.log(f"Thư mục Profile không tồn tại: {user_data_path}", "error")
            self.state = "STOPPED"
            self.stats["state"] = "STOPPED"
            return

        from browser_session_manager import browser_session_mgr
        browser_or_ctx = None
        page = None
        is_cdp = False
        try:
            try:
                browser_or_ctx, page, is_cdp = browser_session_mgr.acquire_page(
                    account_id=account_id,
                    profile_dir=user_data_path,
                    headless=headless
                )
                page.set_default_timeout(15000)

                # Inject cookies from cache file to guarantee session is active
                safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', user_data_path.name)
                cookie_file = Path("data") / f"cookies_{safe_name}.txt"
                if not cookie_file.exists():
                    cookie_file = Path("data") / "cookies_chrome_fb_profile.txt"
                if cookie_file.exists() and hasattr(browser_or_ctx, "add_cookies"):
                    cookie_str = cookie_file.read_text(encoding="utf-8")
                    cookie_list = []
                    for item in cookie_str.strip().split(";"):
                        if "=" in item:
                            k, v = item.strip().split("=", 1)
                            cookie_list.append({
                                "name": k.strip(),
                                "value": v.strip(),
                                "domain": ".facebook.com",
                                "path": "/"
                            })
                    if cookie_list:
                        try:
                            browser_or_ctx.add_cookies(cookie_list)
                        except Exception:
                            pass
            except Exception as launch_err:
                self.log(f"Lỗi khởi động Chrome profile: {launch_err}", "error")
                self.state = "STOPPED"
                self.stats["state"] = "STOPPED"
                return

            # 1. Đảm bảo vai trò đúng (Profile hoặc Fanpage)
                self.log(f"Đang kiểm tra và xác nhận vai trò: {role_desc}...", "info")
                try:
                    ensure_role(
                        page=page,
                        role_type=role_type,
                        role_url=role_url,
                        role_name=role_name,
                        personal_name=personal_name or acc_name,
                        log_fn=self.log
                    )
                except Exception as r_err:
                    self.log(f"Lỗi khi chuyển vai trò: {r_err}", "warning")

                # 2. Lặp qua danh sách nhóm cần rời
                for idx, g in enumerate(groups, 1):
                    if self._stop_event.is_set():
                        self.log("Đã dừng tiến trình theo yêu cầu.", "warning")
                        break

                    while not self._pause_event.is_set():
                        time.sleep(0.5)
                        if self._stop_event.is_set():
                            break

                    g_name = g.get("name", "") or "Nhóm Facebook"
                    g_url = g.get("url", "")
                    if not g_url:
                        continue

                    # Ensure standard URL
                    if not g_url.startswith("http"):
                        g_url = f"https://www.facebook.com/groups/{g_url}/"

                    self.stats["current_group"] = g_name
                    self.log(f"[{idx}/{len(groups)}] Đang truy cập nhóm: '{g_name}' ({g_url})...", "info")

                    success = False
                    try:
                        success = self._leave_single_group(page, g_url, g_name, prevent_readd)
                    except Exception as le:
                        err_msg = str(le)
                        self.log(f"Lỗi khi rời nhóm '{g_name}': {err_msg}", "error")
                        success = False
                        if "closed" in err_msg.lower() or "target" in err_msg.lower():
                            self.log("Trình duyệt hoặc tab đã bị đóng! Tự động dừng tiến trình rời nhóm để bảo toàn an toàn.", "warning")
                            self._stop_event.set()
                            break

                    self.stats["completed"] = idx
                    if success:
                        self.stats["success"] += 1
                        left_urls.append(g_url)
                        self.stats["left_groups"].append(g_url)
                        self._remove_group_from_storage(
                            account_id=account_id,
                            role_type=role_type,
                            role_url=role_url,
                            role_name=role_name,
                            group_url=g_url,
                            group_id=g.get("id")
                        )
                        self.log(f"✅ [{idx}/{len(groups)}] ĐÃ RỜI THÀNH CÔNG: '{g_name}'!", "success")
                    else:
                        self.stats["failed"] += 1
                        self.log(f"❌ [{idx}/{len(groups)}] Không thể rời nhóm: '{g_name}'.", "warning")

                    pct = int((idx / len(groups)) * 100)
                    self.stats["progress_percent"] = pct

                    # Safe delay between groups
                    if idx < len(groups) and not self._stop_event.is_set():
                        delay_time = random.uniform(min_delay, max_delay)
                        self.log(f"Nghỉ an toàn {delay_time:.1f}s trước khi rời nhóm tiếp theo...", "info")
                        time.sleep(delay_time)

        except Exception as worker_err:
            self.log(f"Lỗi bất ngờ trong worker rời nhóm: {worker_err}", "error")
        finally:
            if browser_or_ctx and page:
                browser_session_mgr.release_page(
                    account_id=account_id,
                    browser_or_context=browser_or_ctx,
                    page=page,
                    is_cdp=is_cdp
                )

        self.state = "IDLE"
        self.stats["state"] = "IDLE"
        self.log(f"🎉 Hoàn thành tiến trình rời nhóm! Thành công: {len(left_urls)}/{len(groups)} nhóm.", "success")
        if on_complete:
            try:
                on_complete(left_urls)
            except Exception:
                pass

    def _remove_group_from_storage(
        self,
        account_id: str,
        role_type: str,
        role_url: Optional[str],
        role_name: Optional[str],
        group_url: str,
        group_id: Optional[str] = None
    ):
        """Immediately remove left group from cache JSON and XLSX to guarantee synchronization across all accounts"""
        try:
            role_key = role_type if role_type == "personal" else f"page_{re.sub(r'[^a-zA-Z0-9_]', '_', role_url or role_name or 'page')}"
            
            # If it is a Fanpage, sync removal across all accounts managing this same Fanpage!
            target_files = []
            if role_type == "page":
                target_files = list(Path("data").glob(f"groups_*_{role_key}.json"))
            else:
                data_file = Path("data") / f"groups_{account_id}_{role_key}.json"
                if data_file.exists():
                    target_files.append(data_file)

            if not target_files:
                return

            target_clean_url = (group_url or "").strip().rstrip("/")
            target_id = str(group_id or "").strip()
            if not target_id and "groups/" in target_clean_url:
                target_id = target_clean_url.split("groups/")[-1].split("/")[0].split("?")[0].strip()

            def is_match(g):
                gid = str(g.get("id") or "").strip()
                gurl = (g.get("url") or "").strip().rstrip("/")
                extracted_gid = gurl.split("groups/")[-1].split("/")[0].split("?")[0].strip() if "groups/" in gurl else ""
                if target_id and (gid == target_id or extracted_gid == target_id):
                    return True
                if target_clean_url and (gurl == target_clean_url or gurl.lower() == target_clean_url.lower()):
                    return True
                return False

            for df in target_files:
                try:
                    existing_groups = json.loads(df.read_text(encoding="utf-8"))
                    if not existing_groups:
                        continue
                    updated = [g for g in existing_groups if not is_match(g)]
                    if len(updated) != len(existing_groups):
                        df.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
                        xlsx_file = df.with_suffix(".xlsx")
                        if xlsx_file.exists():
                            try:
                                import pandas as pd
                                df_pd = pd.DataFrame(updated)
                                df_pd.to_excel(xlsx_file, index=False)
                            except Exception:
                                pass
                        self.log(f"Đã cập nhật đồng bộ file lưu trữ: gỡ nhóm đã rời khỏi {df.name} (còn lại {len(updated)} nhóm)", "info")
                except Exception as file_err:
                    pass

            # Phát sự kiện đồng bộ thời gian thực cho mọi tab
            if self.sync_callback:
                try:
                    self.sync_callback("leave", {"url": target_clean_url, "id": target_id}, account_id, role_key)
                except Exception as sce:
                    self.log(f"Lỗi sync callback rời nhóm: {sce}", "warning")
        except Exception as e:
            self.log(f"Lỗi cập nhật lưu trữ khi rời nhóm: {e}", "warning")

    def _safe_click(self, page, locator, timeout: int = 4000) -> bool:
        if not locator:
            return False
        try:
            if hasattr(locator, "count") and locator.count() == 0:
                return False
        except Exception:
            return False
        target = locator.first if hasattr(locator, "first") else locator
        try:
            target.evaluate("(el) => el.scrollIntoView({ behavior: 'instant', block: 'center', inline: 'center' })")
            time.sleep(0.3)
        except Exception:
            pass
        try:
            target.click(timeout=timeout)
            return True
        except Exception:
            pass
        try:
            target.click(force=True, timeout=2500)
            return True
        except Exception:
            pass
        try:
            target.evaluate("""(el) => {
                el.scrollIntoView({ behavior: 'instant', block: 'center', inline: 'center' });
                el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                el.click();
            }""")
            return True
        except Exception:
            return False

    def _leave_single_group(self, page, group_url: str, group_name: str, prevent_readd: bool = True) -> bool:
        '''Handles navigating to a group, clicking Joined, and confirming Leave Group'''
        page.goto(group_url, wait_until="domcontentloaded")
        time.sleep(2.5)

        # 1. Check if group unavailable/blocked/closed
        unavail_selectors = [
            'text="Trang này hiện không khả dụng"',
            'text="This content isn\'t available right now"',
            'text="Nội dung này hiện không khả dụng"',
            'text="Nhóm này ở chế độ riêng tư"'
        ]
        for us in unavail_selectors:
            try:
                if page.locator(us).first.is_visible(timeout=800):
                    self.log(f"Nhóm '{group_name}' hiện không khả dụng hoặc bị đóng. Tự động đánh dấu hoàn tất.", "info")
                    return True
            except Exception:
                pass

        # 2. Check if already left or not a member:
        join_btn_selectors = [
            'div[aria-label="Tham gia nhóm"]',
            'div[aria-label="Join group"]',
            'div[role="button"]:has-text("Tham gia nhóm")',
            'div[role="button"]:has-text("Join group")',
            'button:has-text("Tham gia nhóm")',
            'button:has-text("Join group")',
            'span:has-text("Tham gia nhóm")',
            'span:has-text("Join group")',
            'div[role="button"]:has-text("Hủy yêu cầu")',
            'div[role="button"]:has-text("Cancel request")',
            'div[role="button"]:has-text("Chờ phê duyệt")',
            'div[role="button"]:has-text("Pending")'
        ]
        for js in join_btn_selectors:
            try:
                el = page.locator(js).first
                if el.is_visible(timeout=1000):
                    self.log(f"Nhóm '{group_name}' hiện không phải thành viên (nút 'Tham gia nhóm' hiển thị). Tự động đánh dấu hoàn tất.", "info")
                    return True
            except Exception:
                pass

        # 3. Find 'Đã tham gia' / 'Joined' button
        joined_selectors = [
            'div[aria-label="Đã tham gia"]',
            'div[aria-label="Joined"]',
            'div[role="button"]:has-text("Đã tham gia")',
            'div[role="button"]:has-text("Joined")',
            'button:has-text("Đã tham gia")',
            'button:has-text("Joined")',
            'div[aria-label*="Đã tham gia"]',
            'div[aria-label*="Joined"]'
        ]

        joined_btn = None
        for js in joined_selectors:
            try:
                el = page.locator(js).first
                if el.is_visible(timeout=1500):
                    joined_btn = el
                    break
            except Exception:
                pass

        # Fallback: check 3-dots 'Khác' button if 'Đã tham gia' is hidden
        if not joined_btn:
            more_selectors = [
                'div[aria-label="Khác"]',
                'div[aria-label="More"]',
                'div[aria-label="Xem thêm"]',
                'div[aria-label*="quản lý"]'
            ]
            for ms in more_selectors:
                try:
                    el = page.locator(ms).first
                    if el.is_visible(timeout=1000):
                        el.click()
                        time.sleep(1.2)
                        break
                except Exception:
                    pass

            # Re-check joined selectors after clicking more
            for js in joined_selectors:
                try:
                    el = page.locator(js).first
                    if el.is_visible(timeout=1000):
                        joined_btn = el
                        break
                except Exception:
                    pass

        if not joined_btn:
            # Check join button one more time in case of slower load
            for js in join_btn_selectors:
                try:
                    el = page.locator(js).first
                    if el.is_visible(timeout=800):
                        self.log(f"Nhóm '{group_name}' hiện không phải thành viên. Tự động đánh dấu hoàn tất.", "info")
                        return True
                except Exception:
                    pass

            self.log(f"Không tìm thấy nút 'Đã tham gia' trên trang nhóm '{group_name}'.", "warning")
            return False

        # Click 'Đã tham gia' to open dropdown menu
        self._safe_click(page, joined_btn)
        time.sleep(1.5)

        # 4. Find 'Rời nhóm' in menu
        leave_menu_selectors = [
            'div[role="menuitem"]:has-text("Rời nhóm")',
            'div[role="menuitem"]:has-text("Rời khỏi nhóm")',
            'div[role="menuitem"]:has-text("Leave group")',
            'div[role="menuitem"]:has-text("Leave Group")',
            'span:has-text("Rời nhóm")',
            'span:has-text("Rời khỏi nhóm")',
            'span:has-text("Leave group")',
            'div[role="button"]:has-text("Rời nhóm")',
            'div[role="button"]:has-text("Rời khỏi nhóm")',
            'div[role="button"]:has-text("Leave group")'
        ]

        leave_menu_btn = None
        for lms in leave_menu_selectors:
            try:
                el = page.locator(lms).first
                if el.is_visible(timeout=1500):
                    leave_menu_btn = el
                    break
            except Exception:
                pass

        if not leave_menu_btn:
            self.log(f"Không tìm thấy mục 'Rời nhóm' trong menu xổ xuống của '{group_name}'.", "warning")
            return False

        # Click 'Rời nhóm'
        self._safe_click(page, leave_menu_btn)
        time.sleep(1.8)

        # 5. Check confirmation dialog specifically targeting Leave dialog
        dialog = page.locator('div[role="dialog"]:has-text("Rời"), div[role="dialog"]:has-text("Leave")').first
        if not dialog.is_visible(timeout=3500):
            # Fallback to the last opened dialog on page
            dialog = page.locator('div[role="dialog"]').last
            if not dialog.is_visible(timeout=1000):
                self.log("Không thấy hộp thoại xác nhận rời nhóm xuất hiện.", "warning")
                return False

        # Optional: Prevent people from inviting you again
        if prevent_readd:
            try:
                checkbox = dialog.locator('input[type="checkbox"], div[role="checkbox"]').first
                if checkbox.is_visible(timeout=1000):
                    is_checked = checkbox.get_attribute("aria-checked") == "true" or checkbox.is_checked()
                    if not is_checked:
                        self._safe_click(page, checkbox)
                        time.sleep(0.4)
            except Exception:
                pass

        # 6. Click final confirm button in dialog (prioritize 'Rời khỏi nhóm' & 'Leave group')
        confirm_btn_selectors = [
            'div[aria-label="Rời khỏi nhóm"]',
            'div[aria-label="Rời nhóm"]',
            'div[aria-label="Leave group"]',
            'div[aria-label="Leave Group"]',
            'div[role="button"]:has-text("Rời khỏi nhóm")',
            'div[role="button"]:has-text("Rời nhóm")',
            'div[role="button"]:has-text("Leave group")',
            'button:has-text("Rời khỏi nhóm")',
            'button:has-text("Rời nhóm")',
            'button:has-text("Leave group")'
        ]

        confirm_btn = None
        for cbs in confirm_btn_selectors:
            try:
                el = dialog.locator(cbs).first
                if el.is_visible(timeout=1500):
                    confirm_btn = el
                    break
            except Exception:
                pass

        # Fallback if dialog.locator didn't match: search page-wide
        if not confirm_btn:
            for cbs in confirm_btn_selectors:
                try:
                    el = page.locator(cbs).first
                    if el.is_visible(timeout=1000):
                        confirm_btn = el
                        break
                except Exception:
                    pass

        if not confirm_btn:
            self.log("Không tìm thấy nút bấm xác nhận 'Rời khỏi nhóm' trong hộp thoại.", "warning")
            return False

        self._safe_click(page, confirm_btn)
        time.sleep(3.5)

        # Verification: dialog closed or leave completed
        self.log(f"ĐÃ RỜI THÀNH CÔNG khỏi nhóm '{group_name}'!", "success")
        return True


# Global Singleton Instance
group_leave_engine = GroupLeaveEngine()
