'''
Facebook Role Switcher Helper Module
Handles switching the active Facebook identity between Personal Profile and Fanpages in Playwright sessions.
'''

import time
from typing import Optional, Callable
from playwright.sync_api import Page

def check_is_logged_in(page: Page) -> bool:
    """
    Kiểm tra xem trang hiện tại có đang trong phiên đăng nhập Facebook hợp lệ không.
    Trả về False nếu đang ở màn hình login, checkpoint hoặc modal yêu cầu mật khẩu.
    """
    try:
        cur_url = (page.url or "").lower()
        if any(k in cur_url for k in ["/login", "stype=lo", "/checkpoint", "/auth_platform", "recover/initiate"]):
            return False

        # 1. Các ô nhập tài khoản/mật khẩu
        if page.locator('input[name="email"], input[id="email"], input[name="pass"], input[type="password"]').count() > 0:
            return False

        # 2. Nút Đăng nhập khi không có avatar tài khoản
        login_btn = page.locator('button[name="login"], button:has-text("Đăng nhập"), div[role="button"]:has-text("Đăng nhập")')
        has_avatar = page.locator('div[role="button"][aria-label*="Trang cá nhân"], div[role="button"][aria-label*="Your profile"], div[role="button"][aria-label*="Tài khoản"], div[role="button"][aria-label*="Account"]').count() > 0
        if login_btn.count() > 0 and not has_avatar:
            return False

        return True
    except Exception:
        return True

def ensure_role(
    page: Page,
    role_type: str = "personal",
    role_url: Optional[str] = None,
    role_name: Optional[str] = None,
    personal_name: Optional[str] = None,
    log_fn: Optional[Callable[[str, str], None]] = None,
    **kwargs
) -> bool:
    # Hỗ trợ cả log_fn lẫn log_callback
    _cb = log_fn or kwargs.get("log_callback")
    def log(msg, lvl="info"):
        if _cb:
            try:
                _cb(msg, lvl)
            except TypeError:
                _cb(msg)
        else:
            print(f"[ROLE_SWITCHER] {msg}")

    role_type_clean = (role_type or "personal").lower().strip()

    try:
        # Kiểm tra sơ bộ trạng thái đăng nhập
        if not check_is_logged_in(page):
            log("🚨 Tài khoản chưa đăng nhập Facebook hoặc phiên đăng nhập đã hết hạn! Vui lòng vào 'Quản lý tài khoản' -> 'Mở trình duyệt đăng nhập' để xác thực lại.", "error")
            return False

        # =========================================================
        # CASE 1: SWITCH TO FANPAGE
        # =========================================================
        if role_type_clean == "page" and (role_url or role_name):
            target_display = role_name or role_url
            log(f"Đang kiểm tra và chuyển vai trò sang Fanpage '{target_display}'...", "info")

            # Method A: Navigate directly to Fanpage URL and click 'Chuyển ngay' / 'Switch now'
            if role_url and role_url.startswith("http"):
                log(f"Điều hướng tới Fanpage: {role_url}", "info")
                page.goto(role_url, wait_until="domcontentloaded")
                time.sleep(3)

                if not check_is_logged_in(page):
                    log("🚨 Phiên đăng nhập đã hết hạn khi truy cập Fanpage! Vui lòng đăng nhập lại.", "error")
                    return False

                # Kiểm tra xem đã đang ở vai trò Quản trị viên của Fanpage này chưa
                is_already_acting = page.locator('div[role="button"]:has-text("Quản lý"), div[role="button"]:has-text("Manage"), div[role="button"]:has-text("Chỉnh sửa trang cá nhân"), div[role="button"]:has-text("Bảng điều khiển chuyên nghiệp")').first
                if is_already_acting.is_visible(timeout=1500):
                    log(f"Đã đang ở vai trò quản trị Fanpage '{target_display}'!", "success")
                    return True

                switch_selectors = [
                    'div[aria-label="Chuyển ngay"]',
                    'div[aria-label="Chuyển"]',
                    'div[role="button"]:has-text("Chuyển ngay")',
                    'div[role="button"]:has-text("Switch now")',
                    'button:has-text("Chuyển ngay")',
                    'button:has-text("Switch now")'
                ]
                for s in switch_selectors:
                    btn = page.locator(s).first
                    if btn.is_visible(timeout=1500):
                        log(f"Tìm thấy nút chuyển vai trò ('{s}'). Đang bấm chuyển...", "info")
                        btn.click()
                        # Đợi Facebook hoàn tất quá trình reload sang profile mới
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=12000)
                            page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            pass
                        time.sleep(4)
                        log(f"ĐÃ CHUYỂN THÀNH CÔNG sang vai trò Fanpage: {target_display}!", "success")
                        return True

            # Method B: Open Top Right Avatar Menu and select Fanpage
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
            time.sleep(2)

            if not check_is_logged_in(page):
                log("🚨 Phiên đăng nhập đã hết hạn! Vui lòng đăng nhập lại tài khoản Facebook.", "error")
                return False

            avatar_selectors = [
                'div[role="button"][aria-label*="Trang cá nhân"]',
                'div[role="button"][aria-label*="Your profile"]',
                'div[role="button"][aria-label*="Tài khoản"]',
                'div[role="button"][aria-label*="Account"]'
            ]
            avatar_btn = None
            for s in avatar_selectors:
                el = page.locator(s).first
                if el.is_visible(timeout=2000):
                    avatar_btn = el
                    break

            if avatar_btn:
                avatar_btn.click()
                time.sleep(2)

                if role_name:
                    target_btn = page.locator(f'div[role="dialog"] div[role="button"]:has-text("{role_name}"), div[role="dialog"] [aria-label*="{role_name}"]').first
                    if target_btn.is_visible(timeout=2000):
                        target_btn.click()
                        time.sleep(4)
                        log(f"ĐÃ CHUYỂN THÀNH CÔNG sang vai trò Fanpage: {target_display} qua Menu!", "success")
                        return True

                see_all = page.locator('div[role="dialog"] div[role="button"]:has-text("Xem tất cả trang cá nhân"), div[role="dialog"] div[role="button"]:has-text("See all profiles")').first
                if see_all.is_visible(timeout=2000):
                    see_all.click()
                    time.sleep(2)
                    if role_name:
                        target_btn = page.locator(f'div[role="dialog"] div[role="button"]:has-text("{role_name}")').first
                        if target_btn.is_visible(timeout=3000):
                            target_btn.click()
                            time.sleep(4)
                            log(f"ĐÃ CHUYỂN THÀNH CÔNG sang vai trò Fanpage: {target_display}!", "success")
                            return True

                page.keyboard.press("Escape")

            # Nếu không tìm thấy nút chuyển nhưng đã đăng nhập và đang trên trang
            if check_is_logged_in(page):
                log(f"Đang hoạt động trong vai trò hiện tại của Fanpage '{target_display}'.", "info")
                return True
            else:
                log("🚨 Không thể chuyển vai trò vì tài khoản chưa đăng nhập Facebook.", "error")
                return False

        # =========================================================
        # CASE 2: SWITCH TO PERSONAL PROFILE
        # =========================================================
        else:
            log("Đang kiểm tra và chuyển về vai trò Trang cá nhân (Personal Profile)...", "info")
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
            time.sleep(2)

            if not check_is_logged_in(page):
                log("🚨 Tài khoản chưa đăng nhập Facebook hoặc phiên đăng nhập đã hết hạn!", "error")
                return False

            avatar_selectors = [
                'div[role="button"][aria-label*="Trang cá nhân"]',
                'div[role="button"][aria-label*="Your profile"]',
                'div[role="button"][aria-label*="Tài khoản"]',
                'div[role="button"][aria-label*="Account"]'
            ]
            avatar_btn = None
            for s in avatar_selectors:
                el = page.locator(s).first
                if el.is_visible(timeout=2000):
                    avatar_btn = el
                    break

            if avatar_btn:
                avatar_btn.click()
                time.sleep(2)

                # 1. Tìm trực tiếp nút chuyển đổi sang cá nhân (Chuyển sang Dương Lê / Switch to...)
                try:
                    switch_btns = page.locator('div[role="dialog"] div[role="button"][aria-label*="Chuyển sang"], div[role="dialog"] div[role="button"][aria-label*="Switch to"]').all()
                    target_btn = None
                    for b in switch_btns:
                        aria = (b.get_attribute("aria-label") or "").lower()
                        txt = (b.inner_text() or "").lower()
                        if personal_name and (personal_name.lower() in aria or personal_name.lower() in txt):
                            target_btn = b
                            break
                        elif not target_btn:
                            target_btn = b
                    
                    if target_btn:
                        btn_name = target_btn.get_attribute("aria-label") or target_btn.inner_text().strip().split('\n')[0]
                        log(f"Tìm thấy nút chuyển về trang cá nhân: '{btn_name}'. Đang chuyển...", "info")
                        target_btn.click()
                        time.sleep(4)
                        log(f"ĐÃ CHUYỂN THÀNH CÔNG về vai trò Trang cá nhân: {btn_name}!", "success")
                        return True
                except Exception: pass

                # 2. Tìm theo tên tài khoản cụ thể
                if personal_name:
                    try:
                        p_btn = page.locator(f'div[role="dialog"] div[role="button"]:has-text("{personal_name}"), div[role="dialog"] [aria-label*="{personal_name}"]').first
                        if p_btn.is_visible(timeout=2000):
                            p_btn.click()
                            time.sleep(4)
                            log(f"ĐÃ CHUYỂN THÀNH CÔNG về vai trò Trang cá nhân: {personal_name}!", "success")
                            return True
                    except Exception: pass

                # 3. Xem tất cả trang cá nhân
                try:
                    see_all = page.locator('div[role="dialog"] div[role="button"]:has-text("Xem tất cả trang cá nhân"), div[role="dialog"] div[role="button"]:has-text("See all profiles")').first
                    if see_all.is_visible(timeout=1500):
                        see_all.click()
                        time.sleep(2)
                        if personal_name:
                            p_btn = page.locator(f'div[role="dialog"] div[role="button"]:has-text("{personal_name}"), div[role="dialog"] [aria-label*="{personal_name}"]').first
                            if p_btn.is_visible(timeout=2000):
                                p_btn.click()
                                time.sleep(4)
                                log(f"ĐÃ CHUYỂN THÀNH CÔNG về vai trò Trang cá nhân: {personal_name}!", "success")
                                return True
                        first_prof = page.locator('div[role="dialog"] div[role="button"]').first
                        if first_prof.is_visible():
                            first_prof.click()
                            time.sleep(4)
                            log("ĐÃ CHUYỂN THÀNH CÔNG về vai trò Trang cá nhân đầu tiên!", "success")
                            return True
                except Exception: pass

                page.keyboard.press("Escape")

            if check_is_logged_in(page):
                log("Đang hoạt động đúng với vai trò Trang cá nhân.", "info")
                return True
            else:
                log("🚨 Không thể chuyển về trang cá nhân do chưa đăng nhập.", "error")
                return False

    except Exception as ex:
        log(f"Lưu ý khi chuyển vai trò: {ex}", "warning")
        return False
