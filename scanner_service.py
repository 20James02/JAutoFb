"""
Scanner Service for Facebook Auto Poster
Handles:
- Scanning Fanpages managed by account
- Scanning Groups joined by Personal Profile or Fanpage
"""

import os
import sys
import time
import json
import re
import random
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from playwright.sync_api import sync_playwright
from fast_group_scraper import clean_fb_group_name

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def scan_account_pages(profile_dir: str, acc_name: str, headless: bool = True, log_cb: Optional[Callable[[str, str], None]] = None) -> List[Dict[str, Any]]:
    def log(msg, lvl="info"):
        if log_cb: log_cb(f"[{acc_name}] {msg}", lvl)
        print(f"[{acc_name}] {msg}")

    pages = []
    mode_desc = "Chạy ngầm" if headless else "Mở Chrome"
    log(f"Bắt đầu quét danh sách Fanpage ({mode_desc})...", "info")
    profile_path = Path(profile_dir).resolve()
    from account_manager import kill_chrome_for_profile
    kill_chrome_for_profile(profile_path)
    pw = None
    context = None
    try:
        pw = sync_playwright().start()
        from account_manager import get_standard_chrome_args, get_natural_user_agent
        args = get_standard_chrome_args(headless=headless)
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            channel="chrome",
            headless=headless,
            ignore_default_args=["--enable-automation"],
            user_agent=get_natural_user_agent(),
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
            no_viewport=True if not headless else False,
            viewport=None if not headless else {"width": 1280, "height": 850},
            args=args
        )
        context.add_init_script("""
            window.chrome = window.chrome || { runtime: {} };
        """)
        page = context.pages[0] if context.pages else context.new_page()
        if not headless:
            try: page.bring_to_front()
            except Exception: pass

        # Điều hướng trang quản lý Page
        log("Đang vào trang danh sách Fanpage...", "info")
        page.goto("https://www.facebook.com/pages/?category=your_pages", wait_until="domcontentloaded")
        time.sleep(3)

        # Kiểm tra đăng nhập
        from role_switcher import check_is_logged_in
        if not check_is_logged_in(page):
            log("🚨 Tài khoản chưa đăng nhập Facebook hoặc phiên đăng nhập đã hết hạn! Vui lòng mở trình duyệt và đăng nhập lại.", "error")
            raise RuntimeError(f"Tài khoản '{acc_name}' chưa đăng nhập Facebook hoặc phiên đăng nhập đã hết hạn!")

        # Lướt nhẹ để tải danh sách - mô phỏng cuộn tự nhiên
        page.evaluate("window.scrollBy({top: 800, behavior: 'smooth'})")
        time.sleep(random.uniform(2.0, 3.5))

        # Quét các thẻ Fanpage chính thống trong khu vực nội dung chính
        main_loc = page.locator('div[role="main"]')
        if main_loc.count() > 0:
            page_elements = main_loc.locator('a[role="link"]').all()
        else:
            page_elements = page.locator('a[role="link"]').all()
        found_map = {}

        # Danh sách các từ khóa / subpath bị loại trừ tuyệt đối khi quét Fanpage
        invalid_subpaths = [
            "/groups/", "/posts/", "/permalink/", "/story.php", "/stories/",
            "/photo", "/video", "/reel", "/events/", "/marketplace/",
            "/gaming/", "/watch/", "/friends/", "/saved/", "/memories/",
            "/notifications/", "/messages/", "/settings/", "/help/",
            "/ads/", "/ad_center/", "/inbox/", "/overview/", "/latest/",
            "business.facebook.com", "category="
        ]
        skip_words = [
            "trang chủ", "home", "xem tất cả", "tạo trang", "cài đặt",
            "thông báo", "watch", "marketplace", "groups", "nhóm", "gaming",
            "menu", "chưa đọc", "quản trị viên", "người kiểm duyệt",
            "phê duyệt", "bài viết mới", "tin nhắn", "bạn bè"
        ]

        for elem in page_elements:
            try:
                href = elem.get_attribute("href") or ""
                text = elem.inner_text().strip()
                if not text or len(text) < 2: continue

                # Bỏ qua nếu text chứa từ khóa hệ thống hoặc thông báo
                text_low = text.lower()
                if any(w in text_low for w in ["chưa đọc", "quản trị viên", "người kiểm duyệt", "phê duyệt", "bài viết mới"]):
                    continue
                if any(w == text_low for w in skip_words):
                    continue

                # Bỏ qua nếu href chứa các subpath không phải Fanpage
                href_low = href.lower()
                if any(sub in href_low for sub in invalid_subpaths):
                    continue

                # Chuẩn hóa link
                if href.startswith("/"):
                    href = f"https://www.facebook.com{href}"

                # Lọc link trang
                clean_url = href.split("?")[0].rstrip("/")
                if clean_url not in found_map and ("facebook.com/" in clean_url):
                    # Kiểm tra slug trang: không phải các trang hệ thống
                    slug = clean_url.replace("https://www.facebook.com/", "").replace("http://www.facebook.com/", "").split("/")[0].strip()
                    if not slug or slug.lower() in ["pages", "groups", "events", "marketplace", "gaming", "watch", "home", "profile.php"]:
                        # Nếu là profile.php?id=xxx thì vẫn là trang kiểu mới hợp lệ
                        if "profile.php" in href and "id=" in href:
                            clean_url = href.split("&")[0]
                        else:
                            continue

                    lines = [l.strip() for l in text.splitlines() if l.strip()]
                    p_name = lines[0] if lines else text
                    if len(p_name) > 1 and not p_name.isdigit() and not p_name.lower().startswith("chưa đọc"):
                        found_map[clean_url] = p_name
            except Exception:
                continue

        for url, name in found_map.items():
            pages.append({
                "id": re.sub(r'[^a-zA-Z0-9_]', '', url.split("/")[-1]),
                "name": name,
                "url": url
            })

        log(f"Quét hoàn tất: Tìm thấy {len(pages)} Fanpage!", "success")
        return pages

    except Exception as ex:
        log(f"Lỗi khi quét Page: {ex}", "error")
        raise
    finally:
        if context:
            try: context.close()
            except Exception: pass
        if pw:
            try: pw.stop()
            except Exception: pass

from role_switcher import ensure_role, check_is_logged_in

def scan_account_groups(
    profile_dir: str,
    acc_name: str,
    role_type: str = "personal",
    role_url: Optional[str] = None,
    role_name: Optional[str] = None,
    personal_name: Optional[str] = None,
    headless: bool = True,
    proxy: Optional[Dict[str, str]] = None,
    log_cb: Optional[Callable[[str, str], None]] = None,
    on_group_found: Optional[Callable[[Dict[str, Any]], None]] = None
) -> List[Dict[str, Any]]:
    def log(msg, lvl="info"):
        if log_cb: log_cb(f"[{acc_name}] {msg}", lvl)
        print(f"[{acc_name}] {msg}")

    groups = []
    display_role = f"Fanpage '{role_name or role_url}'" if role_type == "page" else f"Trang cá nhân ({personal_name or acc_name})"
    mode_desc = "Chạy ngầm" if headless else "Mở Chrome"
    log(f"Bắt đầu quét danh sách Nhóm theo vai trò {display_role} ({mode_desc})...", "info")
    profile_path = Path(profile_dir).resolve()
    from account_manager import kill_chrome_for_profile
    kill_chrome_for_profile(profile_path)
    pw = None
    context = None
    try:
        pw = sync_playwright().start()
        from account_manager import get_standard_chrome_args, get_natural_user_agent
        args = get_standard_chrome_args(headless=headless)
        launch_kwargs = {
            "user_data_dir": str(profile_path),
            "channel": "chrome",
            "headless": headless,
            "ignore_default_args": ["--enable-automation"],
            "user_agent": get_natural_user_agent(),
            "locale": "vi-VN",
            "timezone_id": "Asia/Ho_Chi_Minh",
            "no_viewport": True if not headless else False,
            "viewport": None if not headless else {"width": 1280, "height": 850},
            "args": args
        }
        if proxy:
            launch_kwargs["proxy"] = proxy

        context = pw.chromium.launch_persistent_context(**launch_kwargs)

        # Inject cookies from cache file to ensure session is authenticated
        safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', profile_path.name)
        cookie_file = Path("data") / f"cookies_{safe_name}.txt"
        if not cookie_file.exists():
            cookie_file = Path("data") / "cookies_chrome_fb_profile.txt"
        if cookie_file.exists():
            try:
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
                    context.add_cookies(cookie_list)
            except Exception:
                pass

        context.add_init_script("""
            window.chrome = window.chrome || { runtime: {} };
        """)
        page = context.pages[0] if context.pages else context.new_page()
        if not headless:
            try: page.bring_to_front()
            except Exception: pass

        # Đảm bảo chuyển đúng vai trò (Trang cá nhân hoặc Fanpage)
        role_ok = ensure_role(
            page=page,
            role_type=role_type,
            role_url=role_url,
            role_name=role_name,
            personal_name=personal_name or acc_name,
            log_fn=lambda m, l: log(m, l)
        )
        if not role_ok:
            log(f"🚨 Dừng quét: Không thể chuyển sang vai trò {display_role} hoặc tài khoản chưa đăng nhập Facebook!", "error")
            raise RuntimeError(f"Tài khoản '{acc_name}' chưa đăng nhập Facebook hoặc phiên đăng nhập đã hết hạn. Vui lòng vào 'Quản lý tài khoản' bấm 'Mở trình duyệt đăng nhập' để đăng nhập lại!")

        # Điều hướng tới trang nhóm đã tham gia (kèm cơ chế đợi ổn định phiên)
        log(f"Đang vào trang danh sách nhóm đã tham gia của vai trò {display_role}...", "info")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=6000)
        except Exception: pass

        for attempt in range(3):
            try:
                page.goto("https://www.facebook.com/groups/joins/", wait_until="domcontentloaded", timeout=25000)
                break
            except Exception as e:
                err_str = str(e).lower()
                if ("interrupted" in err_str or "navigation" in err_str) and attempt < 2:
                    log(f"Đang chờ trình duyệt hoàn tất đổi vai trò (thử lại sau 3s)...", "info")
                    time.sleep(3)
                    continue
                else:
                    raise
        time.sleep(3)

        if not check_is_logged_in(page):
            log(f"🚨 Phiên đăng nhập của '{acc_name}' đã hết hạn khi truy cập danh sách nhóm Facebook!", "error")
            raise RuntimeError(f"Tài khoản '{acc_name}' đã hết hạn phiên đăng nhập Facebook. Vui lòng đăng nhập lại!")

        # Cuộn thông minh để nạp toàn bộ danh sách nhóm (tốc độ cao, sâu và không bỏ sót)
        log("Đang quét và cuộn nạp danh sách nhóm (tự động cập nhật thời gian thực)...", "info")
        found_groups = {}
        prev_count = 0
        no_new_rounds = 0
        max_scrolls = 80  # Giới hạn hợp lý, tránh cuộn quá nhiều gây nghi ngờ

        # Delay ngẫu nhiên trước khi bắt đầu cuộn - mô phỏng người dùng đọc trang
        time.sleep(random.uniform(1.5, 3.5))

        for s_idx in range(max_scrolls):
            try:
                raw_items = page.evaluate("""() => {
                    const results = [];
                    const anchors = document.querySelectorAll('a[href*="/groups/"]');
                    const seen = new Map();
                    const subpathRegex = /(\\/posts\\/|\\/permalink\\/|\\/user\\/|\\/photos\\/|\\/media\\/|\\/files\\/|\\/events\\/|\\/about\\/|\\/members\\/|\\/buy_sell_discussion)/i;
                    const badTextRegex = /(quản\\s*trị\\s*viên\\s*đã|người\\s*kiểm\\s*duyệt|đã\\s*cập\\s*nhật|bài\\s*viết\\s*mới|thành\\s*viên\\s*mới|lần\\s*hoạt\\s*động|hoạt\\s*động\\s*gần\\s*nhất|đã\\s*tham\\s*gia\\s*vào|admin\\s*updated|new\\s*posts|last\\s*active)/i;

                    for (const a of anchors) {
                        const h = a.getAttribute('href') || '';
                        if (!h.includes('/groups/') || h.includes('/joins') || h.includes('/feed') || h.includes('/discover') || h.includes('/create') || h.includes('/categories')) continue;

                        // Skip post or permalink sub-links
                        if (subpathRegex.test(h)) continue;

                        const clean = h.split('?')[0].replace(/\\/+$/, '');
                        const parts = clean.split('/groups/');
                        if (parts.length < 2) continue;
                        const g_id = parts[1].split('/')[0].trim();
                        if (!g_id || ['joins', 'feed', 'discover', 'create', 'categories', 'user', 'profile.php'].includes(g_id.toLowerCase())) continue;

                        const canonicalUrl = 'https://www.facebook.com/groups/' + g_id + '/';

                        let container = a;
                        for (let i = 0; i < 6; i++) {
                            if (!container.parentElement) break;
                            container = container.parentElement;
                            if (container.innerText && container.innerText.length > 20) break;
                        }

                        let aText = (a.innerText || '').trim();
                        const ariaLabel = (a.getAttribute('aria-label') || '').trim();
                        const cardText = container ? (container.innerText || '') : aText;

                        if (!seen.has(canonicalUrl)) {
                            seen.set(canonicalUrl, {
                                href: canonicalUrl,
                                g_id: g_id,
                                title: '',
                                full_text: cardText,
                                container: container
                            });
                        }

                        const entry = seen.get(canonicalUrl);
                        if (cardText.length > entry.full_text.length) {
                            entry.full_text = cardText;
                            entry.container = container;
                        }

                        let cand = aText || ariaLabel;
                        const avatarPattern = /^(?:ảnh\\s*(?:đại\\s*diện|bìa)(?:\\s+của)?(?:\\s+nhóm)?|profile\\s*(?:picture|photo)\\s*of(?:\\s+the)?(?:\\s+group)?|avatar\\s*of)\\s*/i;
                        cand = cand.replace(/^(?:chưa\\s*đọc|unread)[\\s•·:\\-]*/i, '').trim();
                        cand = cand.replace(/^(?:bây\\s*giờ\\s*trong|now\\s*in)\\s+/i, '').trim();
                        cand = cand.replace(avatarPattern, '').trim();

                        if (cand && cand.length > 1) {
                            const low = cand.toLowerCase();
                            const isGeneric = ['tham gia', 'đã tham gia', 'nhóm', 'groups', 'xem tất cả', 'facebook'].includes(low);
                            const isBad = badTextRegex.test(cand);
                            if (!isGeneric && !isBad && !entry.title) {
                                entry.title = cand;
                            }
                        }
                    }

                    const avatarPattern = /^(?:ảnh\\s*(?:đại\\s*diện|bìa)(?:\\s+của)?(?:\\s+nhóm)?|profile\\s*(?:picture|photo)\\s*of(?:\\s+the)?(?:\\s+group)?|avatar\\s*of)\\s*/i;
                    for (const [url, entry] of seen.entries()) {
                        // 1. Authoritative check heading inside card
                        if (entry.container) {
                            const h = entry.container.querySelector('h2, h3, [role="heading"], strong, span[dir="auto"]');
                            if (h) {
                                let hText = (h.innerText || '').trim();
                                hText = hText.replace(/^(?:chưa\\s*đọc|unread)[\\s•·:\\-]*/i, '').trim();
                                hText = hText.replace(/^(?:bây\\s*giờ\\s*trong|now\\s*in)\\s+/i, '').trim();
                                hText = hText.replace(avatarPattern, '').trim();
                                if (hText.length > 1 && !badTextRegex.test(hText)) {
                                    entry.title = hText;
                                }
                            }
                        }

                        // 2. Clean lines fallback
                        if (!entry.title && entry.full_text) {
                            const lines = entry.full_text.split('\\n').map(l => l.trim()).filter(l => l.length > 1);
                            for (let line of lines) {
                                line = line.replace(/^(?:chưa\\s*đọc|unread)[\\s•·:\\-]*/i, '').trim();
                                line = line.replace(/^(?:bây\\s*giờ\\s*trong|now\\s*in)\\s+/i, '').trim();
                                line = line.replace(avatarPattern, '').trim();
                                const low = line.toLowerCase();
                                if (low.includes('lần hoạt động') || low.includes('hoạt động') || low.includes('đã tham gia') || ['nhóm', 'groups'].includes(low)) continue;
                                if (badTextRegex.test(line)) continue;
                                if (line.length >= 2) {
                                    entry.title = line;
                                    break;
                                }
                            }
                        }

                        results.push({
                            href: entry.href,
                            g_id: entry.g_id,
                            text: entry.title,
                            full_text: entry.full_text
                        });
                    }

                    return results;
                }""")

                for item in raw_items:
                    clean_url = item.get("href", "")
                    g_id = item.get("g_id", "")
                    text = item.get("text", "")
                    full_text = item.get("full_text", "")

                    if clean_url not in found_groups:
                        g_name = clean_fb_group_name(text)
                        if not g_name or g_name.isdigit() or g_name.lower() in ["facebook", "nhóm"]:
                            for line in full_text.splitlines():
                                cand = clean_fb_group_name(line)
                                if cand and len(cand) > 3 and not cand.isdigit() and cand.lower() not in ["facebook", "công khai", "riêng tư", "tham gia", "đã tham gia"]:
                                    g_name = cand
                                    break
                        if not g_name:
                            g_name = f"Nhóm {g_id}"

                        found_groups[clean_url] = g_name
                        group_obj = {
                            "id": g_id,
                            "name": g_name,
                            "url": clean_url,
                            "checked": True
                        }
                        groups.append(group_obj)
                        if on_group_found:
                            try:
                                on_group_found(group_obj)
                            except Exception:
                                pass
            except Exception:
                pass

            curr_count = len(found_groups)
            if curr_count > prev_count:
                prev_count = curr_count
                no_new_rounds = 0
                log(f"Đã phát hiện {curr_count} nhóm... đang tiếp tục nạp...", "info")
            else:
                no_new_rounds += 1
                if no_new_rounds == 2:
                    # Nudge scroll - mô phỏng người dùng cuộn lên một chút rồi xuống (tự nhiên)
                    try:
                        page.evaluate("""() => {
                            const up = Math.floor(Math.random() * 200) + 200;
                            window.scrollBy({top: -up, behavior: 'smooth'});
                        }""")
                        time.sleep(random.uniform(0.8, 1.5))
                        page.evaluate("""() => {
                            const down = Math.floor(Math.random() * 400) + 600;
                            window.scrollBy({top: down, behavior: 'smooth'});
                        }""")
                        time.sleep(random.uniform(0.6, 1.2))
                    except Exception:
                        pass
                elif no_new_rounds >= 5:
                    # 5 lần cuộn liên tiếp không còn nhóm mới xuất hiện -> Đã nạp hết sạch
                    break

            # Cuộn tự nhiên giống người dùng: khoảng cách ngẫu nhiên, smooth behavior
            scroll_dist = random.randint(500, 1200)
            page.evaluate(f"""() => {{
                window.scrollBy({{top: {scroll_dist}, behavior: 'smooth'}});
                // Chỉ cuộn container chính (role=main hoặc feed) nếu có
                const main = document.querySelector('[role="main"]') || document.querySelector('[role="feed"]');
                if (main && main.scrollHeight > main.clientHeight && main.clientHeight > 200) {{
                    main.scrollBy({{top: {scroll_dist}, behavior: 'smooth'}});
                }}
            }}""")
            time.sleep(random.uniform(1.0, 2.5))

        log(f"Quét hoàn tất: Tìm thấy trọn vẹn {len(groups)} Nhóm đã tham gia!", "success")
        return groups

    except Exception as ex:
        log(f"Lỗi khi quét Nhóm: {ex}", "error")
        raise
    finally:
        if context:
            try: context.close()
            except Exception: pass
        if pw:
            try: pw.stop()
            except Exception: pass
