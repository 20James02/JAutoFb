'''
Facebook Auto Group Search & Join Engine
Handles:
1. Searching groups on Facebook by one or multiple keywords.
2. Extracting group metadata (name, URL, ID, privacy, members, posts/day, joined status).
3. Enriching group intelligence with FastGroupScraper (post moderation, exact metrics).
4. Auto-checking if group is already joined by current account/role.
5. Automated group joining with:
   - Pre-interaction (browsing group feed before joining).
   - Answering membership questionnaire.
   - Agreeing to group rules.
   - Real-time disk and WebSocket sync upon successful join.
'''

import os
import re
import sys
import time
import json
import random
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Tuple
from urllib.parse import quote
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from role_switcher import ensure_role
from fast_group_scraper import parse_members_vn, clean_fb_group_name

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


class GroupSearchJoinEngine:
    def __init__(self, log_callback: Optional[Callable[[str, str], None]] = None, sync_callback: Optional[Callable[[str, Dict[str, Any], str, str], None]] = None):
        self.log_callback = log_callback
        self.sync_callback = sync_callback
        self.state = "IDLE"  # IDLE, SEARCHING, JOINING, PAUSED, STOPPED
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

        self.join_stats = {
            "state": "IDLE",
            "total": 0,
            "completed": 0,
            "success": 0,
            "pending": 0,
            "failed": 0,
            "progress_percent": 0,
            "current_group": "",
            "joined_groups": []
        }

        self.last_search_results: List[Dict[str, Any]] = []

    def log(self, msg: str, lvl: str = "info"):
        timestamp = time.strftime("%H:%M:%S")
        msg_clean = re.sub(r"^\[\d{2}:\d{2}(?::\d{2})?\]\s*", "", msg.strip())
        line = f"[{timestamp}] [GROUP_DISCOVERY] {msg_clean}"
        print(line)
        if self.log_callback:
            try:
                self.log_callback(line, lvl)
            except Exception:
                pass

    # =========================================================================
    # 1. GROUP SEARCH BY KEYWORDS
    # =========================================================================
    def search_groups(
        self,
        profile_dir: str,
        acc_name: str,
        account_id: str,
        keywords: List[str],
        limit_per_keyword: int = 30,
        privacy_filter: str = "ALL",  # ALL, PUBLIC, PRIVATE
        role_type: str = "personal",
        role_url: Optional[str] = None,
        role_name: Optional[str] = None,
        headless: bool = True,
        enrich_fast: bool = True,
        on_group_found: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> List[Dict[str, Any]]:
        """
        Searches Facebook for groups matching given keywords.
        Supports multi-tab concurrent searching, robust group name extraction,
        and real-time streaming accumulation.
        """
        clean_keywords = [kw.strip() for kw in keywords if kw and kw.strip()]
        if not clean_keywords:
            self.log("Danh sách từ khóa tìm kiếm trống!", "warning")
            return []

        self.state = "SEARCHING"
        self.log(f"Bắt đầu tìm kiếm nhóm đa luồng với {len(clean_keywords)} từ khóa: {', '.join(clean_keywords[:5])}...", "info")

        profile_path = Path(profile_dir).resolve()

        # Load currently joined groups of this account/role for fast matching
        role_key = role_type if role_type == "personal" else f"page_{re.sub(r'[^a-zA-Z0-9_]', '_', role_url or role_name or 'page')}"
        joined_file = Path("data") / f"groups_{account_id}_{role_key}.json"
        joined_gids = set()
        joined_urls = set()
        if joined_file.exists():
            try:
                saved = json.loads(joined_file.read_text(encoding="utf-8"))
                for g in saved:
                    gid = str(g.get("id") or "").strip().lower()
                    gurl = (g.get("url") or "").strip().rstrip("/").lower()
                    if gid: joined_gids.add(gid)
                    if gurl: joined_urls.add(gurl)
                    if "groups/" in gurl:
                        u_id = gurl.split("groups/")[-1].split("/")[0].split("?")[0].strip().lower()
                        if u_id: joined_gids.add(u_id)
            except Exception:
                pass

        results_map: Dict[str, Dict[str, Any]] = {}
        results_lock = threading.Lock()
        from browser_session_manager import browser_session_mgr
        browser_or_ctx = None
        page = None
        is_cdp = False
        allocated_pages = []

        try:
            browser_or_ctx, page, is_cdp = browser_session_mgr.acquire_page(
                account_id=account_id,
                profile_dir=profile_path,
                headless=headless
            )
            page.set_default_timeout(20000)
            if not headless:
                try: page.bring_to_front()
                except Exception: pass

            # Ensure role switch if Fanpage
            ensure_role(
                page=page,
                role_type=role_type,
                role_url=role_url,
                role_name=role_name,
                personal_name=acc_name,
                log_fn=lambda m, l: self.log(m, l)
            )

            # Build search tasks (multi-faceted queries even for single keyword)
            search_tasks = []
            if len(clean_keywords) == 1:
                kw = clean_keywords[0]
                search_tasks.append({"kw": kw, "query": f"https://www.facebook.com/search/groups/?q={quote(kw)}", "label": f"Nhóm '{kw}' (Mặc định)"})
                search_tasks.append({"kw": kw, "query": f"https://www.facebook.com/search/groups/?q={quote(kw)}&filters=rp_group_types:public", "label": f"Nhóm '{kw}' (Công khai)"})
                # Add natural variations
                if not kw.lower().startswith(('hội ', 'nhóm ', 'chợ ')):
                    search_tasks.append({"kw": kw, "query": f"https://www.facebook.com/search/groups/?q={quote('hội ' + kw)}", "label": f"Hội '{kw}'"})
                else:
                    search_tasks.append({"kw": kw, "query": f"https://www.facebook.com/search/groups/?q={quote(kw + ' toàn quốc')}", "label": f"Nhóm '{kw} toàn quốc'"})
            else:
                for kw in clean_keywords:
                    search_tasks.append({"kw": kw, "query": f"https://www.facebook.com/search/groups/?q={quote(kw)}", "label": f"Từ khóa '{kw}'"})

            # Execute search tasks sequentially on the main Playwright thread (avoid greenlet thread switch error)
            for t_idx, task in enumerate(search_tasks):
                kw = task["kw"]
                search_url = task["query"]
                task_label = task["label"]
                self.log(f"[{t_idx + 1}/{len(search_tasks)}] Đang tìm kiếm: {task_label}...", "info")
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                    time.sleep(1.5)
                except Exception as ne:
                    self.log(f"Lỗi truy cập '{task_label}': {ne}", "warning")
                    continue

                # Deep adaptive scroll loop with progressive real-time extraction
                target_limit = max(20, limit_per_keyword)
                max_scrolls = max(35, min(80, (limit_per_keyword * 2) + 10))
                empty_streak = 0
                prev_total = len(results_map)
                task_found = 0

                for s in range(max_scrolls):
                    if task_found >= target_limit:
                        break

                    try:
                        page.evaluate("""() => {
                            window.scrollBy(0, 2500);
                            window.scrollTo(0, document.body.scrollHeight);
                        }""")
                    except Exception:
                        pass
                    time.sleep(0.4)

                    # Extract cards at this scroll level
                    try:
                        raw_cards = page.evaluate("""() => {
                            const links = document.querySelectorAll('a[href*="/groups/"]');
                            const groupCardMap = new Map();
                            const subpathRegex = /(\\/posts\\/|\\/permalink\\/|\\/user\\/|\\/photos\\/|\\/media\\/|\\/files\\/|\\/events\\/|\\/about\\/|\\/members\\/|\\/buy_sell_discussion)/i;
                            const badTextRegex = /(quản\\s*trị\\s*viên\\s*đã|người\\s*kiểm\\s*duyệt|đã\\s*cập\\s*nhật|bài\\s*viết\\s*mới|thành\\s*viên\\s*mới|lần\\s*hoạt\\s*động|hoạt\\s*động\\s*gần\\s*nhất|đã\\s*tham\\s*gia\\s*vào|admin\\s*updated|new\\s*posts|last\\s*active)/i;

                            for (const a of links) {
                                const href = a.getAttribute('href') || '';
                                if (!href.includes('/groups/') || href.includes('/search/') || href.includes('/feed/') || href.includes('/joins') || href.includes('/create') || href.includes('/categories')) continue;

                                // Skip post, permalink or media links
                                if (subpathRegex.test(href)) continue;

                                const clean = href.split('?')[0].replace(/\\/+$/, '');
                                const parts = clean.split('/groups/');
                                if (parts.length < 2) continue;
                                const g_id = parts[1].split('/')[0].trim();
                                if (!g_id || ['create', 'discover', 'feed', 'joins', 'categories', 'user', 'profile.php'].includes(g_id.toLowerCase())) continue;

                                const canonicalUrl = 'https://www.facebook.com/groups/' + g_id + '/';

                                let container = a;
                                for (let i = 0; i < 8; i++) {
                                    if (!container.parentElement) break;
                                    const p = container.parentElement;
                                    const pText = p.innerText || '';
                                    container = p;
                                    if (pText.includes('thành viên') || pText.includes('members') || pText.includes('Công khai') || pText.includes('Riêng tư') || pText.includes('Public') || pText.includes('Private')) {
                                        break;
                                    }
                                }

                                const cardText = container ? (container.innerText || '') : (a.innerText || '');

                                if (!groupCardMap.has(canonicalUrl)) {
                                    groupCardMap.set(canonicalUrl, {
                                        url: canonicalUrl,
                                        g_id: g_id,
                                        title: '',
                                        full_text: cardText,
                                        container: container
                                    });
                                }

                                const entry = groupCardMap.get(canonicalUrl);
                                if (cardText.length > entry.full_text.length) {
                                    entry.full_text = cardText;
                                    entry.container = container;
                                }

                                // Check anchor text or aria-label
                                let aText = (a.innerText || '').trim();
                                const ariaLabel = (a.getAttribute('aria-label') || '').trim();
                                let cand = aText || ariaLabel;
                                const avatarPattern = /^(?:ảnh\\s*(?:đại\\s*diện|bìa)(?:\\s+của)?(?:\\s+nhóm)?|profile\\s*(?:picture|photo)\\s*of(?:\\s+the)?(?:\\s+group)?|avatar\\s*of)\\s*/i;
                                cand = cand.replace(/^(?:chưa\\s*đọc|unread)[\\s•·:\\-]*/i, '').trim();
                                cand = cand.replace(/^(?:bây\\s*giờ\\s*trong|now\\s*in)\\s+/i, '').trim();
                                cand = cand.replace(avatarPattern, '').trim();

                                if (cand && cand.length > 1) {
                                    const low = cand.toLowerCase();
                                    const isGeneric = ['tham gia', 'đã tham gia', 'xem nhóm', 'truy cập', 'join', 'joined', 'visit', 'groups', 'nhóm', 'facebook'].includes(low);
                                    const isBad = badTextRegex.test(cand);
                                    if (!isGeneric && !isBad && !entry.title) {
                                        entry.title = cand;
                                    }
                                }
                            }

                            const items = [];
                            const avatarPattern = /^(?:ảnh\\s*(?:đại\\s*diện|bìa)(?:\\s+của)?(?:\\s+nhóm)?|profile\\s*(?:picture|photo)\\s*of(?:\\s+the)?(?:\\s+group)?|avatar\\s*of)\\s*/i;
                            for (const [url, entry] of groupCardMap.entries()) {
                                // 1. Heading inside container is authoritative
                                if (entry.container) {
                                    const h = entry.container.querySelector('h2, h3, [role="heading"]');
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

                                // 2. Fallback to clean lines from full_text
                                if (!entry.title && entry.full_text) {
                                    const lines = entry.full_text.split('\\n').map(l => l.trim()).filter(l => l.length > 1);
                                    for (let line of lines) {
                                        line = line.replace(/^(?:chưa\\s*đọc|unread)[\\s•·:\\-]*/i, '').trim();
                                        line = line.replace(/^(?:bây\\s*giờ\\s*trong|now\\s*in)\\s+/i, '').trim();
                                        line = line.replace(avatarPattern, '').trim();
                                        const low = line.toLowerCase();
                                        if (low.includes('thành viên') || low.includes('member') || low.includes('bài viết') || low.includes('posts') || ['nhóm', 'tham gia', 'đã tham gia', 'công khai', 'riêng tư', 'public', 'private'].includes(low)) {
                                            continue;
                                        }
                                        if (badTextRegex.test(line)) continue;
                                        if (line.length >= 2) {
                                            entry.title = line;
                                            break;
                                        }
                                    }
                                }

                                items.push({
                                    url: entry.url,
                                    g_id: entry.g_id,
                                    title: entry.title,
                                    full_text: entry.full_text
                                });
                            }
                            return items;
                        }""")

                        for card in raw_cards:
                            raw_url = card.get("url", "").strip()
                            if not raw_url or "/groups/" not in raw_url:
                                continue

                            g_id = card.get("g_id", "")
                            if not g_id:
                                parts = raw_url.rstrip("/").split("/groups/")[-1].split("/")
                                g_id = parts[0].strip()
                            if not g_id or g_id in ["create", "discover", "feed", "joins"]:
                                continue

                            clean_url = f"https://www.facebook.com/groups/{g_id}/"
                            with results_lock:
                                if clean_url in results_map:
                                    continue

                            full_text = card.get("full_text", "")
                            raw_title = card.get("title", "").strip()
                            title = clean_fb_group_name(raw_title)

                            if not title or title.isdigit() or title.lower() in ["facebook", "nhóm"]:
                                for line in full_text.splitlines():
                                    cand = clean_fb_group_name(line)
                                    if cand and len(cand) > 3 and not cand.isdigit() and cand.lower() not in ["facebook", "công khai", "riêng tư", "tham gia", "đã tham gia"]:
                                        title = cand
                                        break
                            if not title:
                                title = f"Nhóm {g_id}"

                            privacy = "Nhóm công khai"
                            if any(w in full_text.lower() for w in ["riêng tư", "nhóm kín", "private"]):
                                privacy = "Nhóm riêng tư"

                            member_count, members_str = parse_members_vn(full_text)

                            posts_today = 0
                            posts_str = "--"
                            m_post = re.search(r'(\d+)\s+bài viết\s+(?:một|mỗi|\/)\s*ngày', full_text, re.IGNORECASE)
                            if m_post:
                                posts_today = int(m_post.group(1))
                                posts_str = f"{posts_today} bài/ngày"
                            else:
                                m_post_mo = re.search(r'(\d+)\s+bài viết\s+(?:một|mỗi|\/)\s*tháng', full_text, re.IGNORECASE)
                                if m_post_mo:
                                    posts_today = max(1, int(m_post_mo.group(1)) // 30)
                                    posts_str = f"~{posts_today} bài/ngày ({m_post_mo.group(1)} bài/tháng)"

                            is_joined = False
                            status_text = "Chưa tham gia"

                            if clean_url.lower().rstrip("/") in joined_urls or g_id.lower() in joined_gids:
                                is_joined = True
                                status_text = "Đã tham gia"
                            elif any(w in full_text for w in ["Đã tham gia", "Truy cập", "Joined", "Visit"]):
                                is_joined = True
                                status_text = "Đã tham gia"
                            elif any(w in full_text for w in ["Đã gửi yêu cầu", "Đã yêu cầu", "Requested"]):
                                status_text = "Chờ duyệt"

                            if privacy_filter == "PUBLIC" and privacy != "Nhóm công khai":
                                continue
                            if privacy_filter == "PRIVATE" and privacy != "Nhóm riêng tư":
                                continue

                            group_item = {
                                "id": g_id,
                                "name": title,
                                "url": clean_url,
                                "privacy": privacy,
                                "is_private": (privacy == "Nhóm riêng tư"),
                                "member_count": member_count,
                                "members_str": members_str or (f"{member_count:,}".replace(",", ".") if member_count else "--"),
                                "posts_today": posts_today,
                                "posts_today_str": posts_str,
                                "moderation": "Chưa rõ (Sẽ quét khi duyệt)",
                                "is_moderated": False,
                                "has_questions": False,
                                "status_text": status_text,
                                "is_joined": is_joined,
                                "keyword": kw,
                                "checked": (not is_joined)
                            }

                            with results_lock:
                                if clean_url not in results_map:
                                    results_map[clean_url] = group_item
                                    task_found += 1
                                    if on_group_found:
                                        try:
                                            on_group_found(group_item)
                                        except Exception:
                                            pass
                    except Exception:
                        pass

                    # Adaptive streak detection
                    if len(results_map) > prev_total:
                        prev_total = len(results_map)
                        empty_streak = 0
                    else:
                        empty_streak += 1
                        if empty_streak == 2:
                            # Nudge scroll to trigger Facebook GraphQL
                            try:
                                page.evaluate("window.scrollBy(0, -600); window.scrollBy(0, 2500);")
                                time.sleep(0.7)
                            except Exception:
                                pass
                        elif empty_streak >= 4:
                            # Truly reached the end of feed
                            break

            self.log(f"Tổng hợp được {len(results_map)} nhóm không trùng lặp từ tìm kiếm.", "success")

            if enrich_fast and results_map:
                try:
                    from account_manager import get_account_cookies_string
                    cookies_str = get_account_cookies_string(str(profile_path))
                    if cookies_str:
                        self.log(f"Đang làm giàu thông tin kiểm duyệt & thành viên qua FastGroupScraper...", "info")
                        from fast_group_scraper import scrape_groups_batch
                        import asyncio
                        gids_to_scrape = [g["id"] for g in results_map.values() if g["id"].isdigit()]
                        if gids_to_scrape:
                            batch_res = asyncio.run(scrape_groups_batch(
                                group_ids=gids_to_scrape[:60],
                                cookies=cookies_str,
                                concurrency=10,
                                db_path="data/groups_cache.db"
                            ))
                            b_map = {r.group_id: r for r in batch_res}
                            for g in results_map.values():
                                if g["id"] in b_map:
                                    r = b_map[g["id"]]
                                    if r.name and r.name != g["id"] and r.name.lower() not in ["facebook", "facebook - đăng nhập hoặc đăng ký"]:
                                        g["name"] = r.name
                                    if r.member_count > 0:
                                        g["member_count"] = r.member_count
                                        g["members_str"] = r.members_str or f"{r.member_count:,}".replace(",", ".")
                                    if r.posts_today > 0:
                                        g["posts_today"] = r.posts_today
                                        g["posts_today_str"] = f"{r.posts_today} bài/ngày"
                                    g["is_moderated"] = r.is_moderated
                                    g["moderation"] = "Duyệt bài" if r.is_moderated else "Tự do đăng"
                                    g["has_questions"] = r.has_questions
                except Exception as ef:
                    self.log(f"Ghi chú: Làm giàu dữ liệu nền hoàn tất ({ef})", "info")

        except Exception as e:
            self.log(f"Lỗi trong quá trình tìm kiếm nhóm: {e}", "error")
        finally:
            for p_extra in allocated_pages[1:]:
                try: p_extra.close()
                except Exception: pass
            if browser_or_ctx and page:
                browser_session_mgr.release_page(
                    account_id=account_id,
                    browser_or_context=browser_or_ctx,
                    page=page,
                    is_cdp=is_cdp
                )
            self.state = "IDLE"

        final_list = list(results_map.values())
        self.last_search_results = final_list
        return final_list

    # =========================================================================
    # 2. AUTOMATED GROUP JOIN ENGINE
    # =========================================================================
    def start_join(
        self,
        account_id: str,
        profile_dir: str,
        acc_name: str,
        groups: List[Dict[str, Any]],
        role_type: str = "personal",
        role_url: Optional[str] = None,
        role_name: Optional[str] = None,
        min_delay: int = 15,
        max_delay: int = 30,
        pre_interaction: bool = True,
        auto_answer: bool = True,
        default_answer: str = "Dạ em xin vào nhóm giao lưu học hỏi, cam kết không spam ạ!",
        agree_rules: bool = True,
        max_count: int = 10,
        headless: bool = True
    ) -> bool:
        """
        Starts automated group join process in background thread.
        """
        with self.lock:
            if self.state in ["SEARCHING", "JOINING"]:
                self.log("Tiến trình đang bận một tác vụ khác!", "warning")
                return False

            if not groups:
                self.log("Danh sách nhóm cần tham gia trống!", "warning")
                return False

            target_groups = groups[:max_count]
            self.state = "JOINING"
            self._stop_event.clear()
            self._pause_event.set()

            self.join_stats = {
                "state": "JOINING",
                "total": len(target_groups),
                "completed": 0,
                "success": 0,
                "pending": 0,
                "failed": 0,
                "progress_percent": 0,
                "current_group": "",
                "joined_groups": []
            }

            self.thread = threading.Thread(
                target=self._worker_join_run,
                args=(
                    account_id,
                    profile_dir,
                    acc_name,
                    target_groups,
                    role_type,
                    role_url,
                    role_name,
                    min_delay,
                    max_delay,
                    pre_interaction,
                    auto_answer,
                    default_answer,
                    agree_rules,
                    headless
                ),
                daemon=True
            )
            self.thread.start()
            return True

    def pause_join(self):
        self._pause_event.clear()
        self.state = "PAUSED"
        self.join_stats["state"] = "PAUSED"
        self.log("⏸️ Đã tạm dừng tiến trình tham gia nhóm.", "warning")

    def resume_join(self):
        self._pause_event.set()
        self.state = "JOINING"
        self.join_stats["state"] = "JOINING"
        self.log("▶️ Tiếp tục tiến trình tham gia nhóm.", "info")

    def stop_join(self):
        self._stop_event.set()
        self._pause_event.set()
        self.state = "STOPPED"
        self.join_stats["state"] = "STOPPED"
        self.log("⏹️ Đã yêu cầu dừng tiến trình tham gia nhóm.", "warning")

    def _worker_join_run(
        self,
        account_id: str,
        profile_dir: str,
        acc_name: str,
        groups: List[Dict[str, Any]],
        role_type: str,
        role_url: Optional[str],
        role_name: Optional[str],
        min_delay: int,
        max_delay: int,
        pre_interaction: bool,
        auto_answer: bool,
        default_answer: str,
        agree_rules: bool,
        headless: bool
    ):
        profile_path = Path(profile_dir).resolve()
        display_role = f"Fanpage '{role_name or role_url}'" if role_type == "page" else f"Trang cá nhân ({acc_name})"
        self.log(f"🚀 Khởi động luồng tham gia {len(groups)} nhóm với vai trò: {display_role} (Delay: {min_delay}-{max_delay}s)...", "info")

        from browser_session_manager import browser_session_mgr
        browser_or_ctx = None
        page = None
        is_cdp = False

        try:
            browser_or_ctx, page, is_cdp = browser_session_mgr.acquire_page(
                account_id=account_id,
                profile_dir=profile_path,
                headless=headless
            )
            page.set_default_timeout(25000)
            if not headless:
                try: page.bring_to_front()
                except Exception: pass

            ensure_role(
                page=page,
                role_type=role_type,
                role_url=role_url,
                role_name=role_name,
                personal_name=acc_name,
                log_fn=lambda m, l: self.log(m, l)
            )

            for idx, g in enumerate(groups, start=1):
                if self._stop_event.is_set():
                    self.log("Dừng theo yêu cầu người dùng.", "warning")
                    break

                self._pause_event.wait()
                g_url = g.get("url", "")
                g_name = g.get("name", g_url)
                self.join_stats["current_group"] = g_name

                self.log(f"[{idx}/{len(groups)}] Bắt đầu xử lý tham gia nhóm: '{g_name}' ({g_url})...", "info")

                result_status, result_msg = self._join_single_group(
                    page=page,
                    group_url=g_url,
                    group_name=g_name,
                    pre_interaction=pre_interaction,
                    auto_answer=auto_answer,
                    default_answer=default_answer,
                    agree_rules=agree_rules,
                    role_type=role_type,
                    role_url=role_url,
                    role_name=role_name
                )

                self.join_stats["completed"] = idx
                if result_status == "SUCCESS":
                    self.join_stats["success"] += 1
                    self.join_stats["joined_groups"].append(g_url)
                    self.log(f"✅ [{idx}/{len(groups)}] ĐÃ THAM GIA THÀNH CÔNG: '{g_name}'!", "success")

                    self._save_group_to_storage(
                        account_id=account_id,
                        role_type=role_type,
                        role_url=role_url,
                        role_name=role_name,
                        group_item=g
                    )
                elif result_status == "PENDING":
                    self.join_stats["pending"] += 1
                    self.log(f"⏳ [{idx}/{len(groups)}] ĐÃ GỬI YÊU CẦU DUYỆT: '{g_name}'. Đang chờ Quản trị viên duyệt.", "info")
                else:
                    self.join_stats["failed"] += 1
                    self.log(f"❌ [{idx}/{len(groups)}] Không thể tham gia: '{g_name}' ({result_msg}).", "warning")

                pct = int((idx / len(groups)) * 100)
                self.join_stats["progress_percent"] = pct

                if idx < len(groups) and not self._stop_event.is_set():
                    delay = random.uniform(min_delay, max_delay)
                    self.log(f"Nghỉ an toàn {delay:.1f}s trước khi tham gia nhóm tiếp theo...", "info")
                    time.sleep(delay)

        except Exception as e:
            self.log(f"Lỗi bất ngờ trong luồng tham gia nhóm: {e}", "error")
        finally:
            if browser_or_ctx and page:
                browser_session_mgr.release_page(
                    account_id=account_id,
                    browser_or_context=browser_or_ctx,
                    page=page,
                    is_cdp=is_cdp
                )
            self.state = "IDLE"
            self.join_stats["state"] = "IDLE"
            self.log(f"🎉 Hoàn thành tiến trình tham gia nhóm! Tham gia thành công: {self.join_stats['success']}/{len(groups)}, Chờ duyệt: {self.join_stats['pending']}, Thất bại: {self.join_stats['failed']}.", "success")

    def _safe_click(self, page, locator, timeout: int = 4000) -> bool:
        """
        Thực hiện click nút trên Facebook với cơ chế đa tầng (multi-tier click):
        Tầng 1: Cuộn nút vào chính giữa màn hình (block: center) thay vì mép trên (start)
                 để không bao giờ bị thanh header cố định (__fb-light-mode) ở trên đè lên.
        Tầng 2: Thử Playwright click với timeout ngắn (4 giây thay vì treo 25-30 giây).
        Tầng 3: Nếu bị 'intercepts pointer events' hoặc timeout, thử click với force=True (bỏ qua hit-test).
        Tầng 4: Fallback tối thượng: Thực thi JavaScript click trực tiếp trên DOM (dispatch MouseEvent + el.click()).
                 Đảm bảo 100% kích hoạt sự kiện click ngay cả khi có bất kỳ overlay / fixed header nào.
        """
        if not locator:
            return False
        try:
            if hasattr(locator, "count") and locator.count() == 0:
                return False
        except Exception:
            return False

        target = locator.first if hasattr(locator, "first") else locator

        # Bước 1: Cuộn element vào giữa viewport (center)
        try:
            target.evaluate("(el) => el.scrollIntoView({ behavior: 'instant', block: 'center', inline: 'center' })")
            time.sleep(0.3)
        except Exception:
            pass

        # Bước 2: Thử click chuẩn của Playwright
        try:
            target.click(timeout=timeout)
            return True
        except Exception:
            pass

        # Bước 3: Thử click với force=True để bỏ qua hit-testing nếu có overlay/fixed-header
        try:
            target.click(force=True, timeout=2500)
            return True
        except Exception:
            pass

        # Bước 4: Fallback tối thượng qua DOM JavaScript: MouseEvent + el.click()
        try:
            target.evaluate("""(el) => {
                el.scrollIntoView({ behavior: 'instant', block: 'center', inline: 'center' });
                el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                el.click();
            }""")
            return True
        except Exception as js_err:
            self.log(f"Lỗi khi safe_click: {js_err}", "warning")
            return False

    def _join_single_group(
        self,
        page,
        group_url: str,
        group_name: str,
        pre_interaction: bool,
        auto_answer: bool,
        default_answer: str,
        agree_rules: bool,
        role_type: str = "personal",
        role_url: Optional[str] = None,
        role_name: Optional[str] = None
    ) -> Tuple[str, str]:
        try:
            page.goto(group_url, wait_until="domcontentloaded", timeout=25000)
            time.sleep(3)

            if page.locator('text="Trang này hiện không khả dụng", text="This content isn\'t available right now"').count() > 0:
                return "FAILED", "Nhóm không khả dụng hoặc bị chặn"

            page_text = page.locator("body").inner_text()
            if any(kw in page_text for kw in ["Đã tham gia", "Joined", "Bạn là thành viên"]):
                return "SUCCESS", "Tài khoản đã là thành viên của nhóm"

            if any(kw in page_text for kw in ["Hủy yêu cầu", "Cancel request", "Đã gửi yêu cầu", "Yêu cầu tham gia đã gửi", "Chờ phê duyệt"]):
                return "PENDING", "Đã gửi yêu cầu tham gia trước đó (Đang chờ admin duyệt)"

            if pre_interaction:
                self.log(f"Lướt xem nội dung nhóm '{group_name}' 3-5s trước khi bấm tham gia...", "info")
                try:
                    page.mouse.wheel(0, 400)
                    time.sleep(1.5)
                    page.mouse.wheel(0, 500)
                    time.sleep(2)
                    page.mouse.wheel(0, -900)
                    time.sleep(1)
                except Exception:
                    pass

            join_btn = None
            join_selectors = [
                'div[role="button"]:has-text("Tham gia nhóm")',
                'div[role="button"]:has-text("Tham gia")',
                'div[role="button"]:has-text("Join group")',
                'div[role="button"]:has-text("Join Group")',
                'button:has-text("Tham gia nhóm")',
                'button:has-text("Tham gia")',
                'button:has-text("Join")',
                'text="Tham gia nhóm"',
                'text="Tham gia"'
            ]

            for sel in join_selectors:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    join_btn = loc
                    break

            if not join_btn:
                # Kiểm tra xem có phải trang nhóm yêu cầu đăng nhập lại hay đã là thành viên không
                page_text_after = page.locator("body").inner_text()
                if any(kw in page_text_after for kw in ["Đã tham gia", "Joined", "Bạn là thành viên"]):
                    return "SUCCESS", "Tài khoản đã là thành viên của nhóm"
                if any(kw in page_text_after for kw in ["Hủy yêu cầu", "Cancel request", "Đã gửi yêu cầu"]):
                    return "PENDING", "Đã gửi yêu cầu tham gia trước đó (Chờ admin duyệt)"
                return "FAILED", "Không tìm thấy nút Tham gia nhóm"

            # Click nút Tham gia bằng cơ chế safe_click đa tầng chống che khuất bởi header
            click_ok = self._safe_click(page, join_btn)
            if not click_ok:
                return "FAILED", "Không thể click nút Tham gia nhóm (Nút bị khóa hoặc không phản hồi)"

            time.sleep(2.5)

            # Kiểm tra dialog bật lên
            dialog = page.locator('div[role="dialog"]')
            if dialog.count() > 0 and dialog.first.is_visible():
                d_loc = dialog.first
                d_text = ""
                try:
                    d_text = d_loc.inner_text()
                except Exception:
                    pass

                # 1. Hộp thoại chọn vai trò tham gia nhóm (Tham gia với tư cách...)
                if any(w in d_text for w in ["với tư cách", "Join group as", "Chọn trang cá nhân", "Choose profile", "Choose a profile", "Chọn cách tham gia", "Chọn Trang"]):
                    self.log(f"Phát hiện hộp thoại chọn vai trò tham gia nhóm. Đang chọn vai trò: [{role_name or 'Trang cá nhân'}]...", "info")
                    role_chosen = False
                    if role_type == "page" and role_name:
                        cand_selectors = [
                            f'div[role="radio"]:has-text("{role_name}")',
                            f'label:has-text("{role_name}")',
                            f'div[role="button"]:has-text("{role_name}")',
                            f'text="{role_name}"'
                        ]
                        for c_sel in cand_selectors:
                            cand = d_loc.locator(c_sel).first
                            try:
                                if cand.count() > 0 and cand.is_visible():
                                    self._safe_click(page, cand)
                                    role_chosen = True
                                    time.sleep(1)
                                    break
                            except Exception:
                                pass
                    else:
                        try:
                            first_radio = d_loc.locator('div[role="radio"], input[type="radio"]').first
                            if first_radio.count() > 0 and first_radio.is_visible():
                                self._safe_click(page, first_radio)
                                role_chosen = True
                                time.sleep(1)
                        except Exception:
                            pass

                    # Bấm nút Tiếp tục / Tham gia nhóm trong hộp thoại chọn vai trò
                    next_btns = [
                        'div[role="button"]:has-text("Tiếp tục")',
                        'div[role="button"]:has-text("Tham gia nhóm")',
                        'button:has-text("Tiếp tục")',
                        'button:has-text("Tham gia nhóm")',
                        'div[role="button"]:has-text("Tiếp")',
                        'div[role="button"]:has-text("Next")',
                        'button:has-text("Next")',
                        'div[role="button"]:has-text("Join group")',
                        'div[role="button"]:has-text("Join Group")'
                    ]
                    for nb_sel in next_btns:
                        nb = d_loc.locator(nb_sel).first
                        try:
                            if nb.count() > 0 and nb.is_visible():
                                self._safe_click(page, nb)
                                time.sleep(3)
                                break
                        except Exception:
                            pass

                # 2. Hộp thoại câu hỏi kiểm duyệt hoặc quy tắc nhóm
                dialog = page.locator('div[role="dialog"]')
                if dialog.count() > 0 and dialog.first.is_visible():
                    d_loc = dialog.first
                    self.log("Phát hiện hộp thoại câu hỏi kiểm duyệt hoặc quy tắc nhóm...", "info")

                    if agree_rules:
                        checkboxes = d_loc.locator('input[type="checkbox"], div[role="checkbox"]')
                        for c_idx in range(checkboxes.count()):
                            cb = checkboxes.nth(c_idx)
                            try:
                                is_checked = cb.get_attribute("aria-checked") == "true" or cb.is_checked()
                                if not is_checked:
                                    self._safe_click(page, cb)
                                    time.sleep(0.5)
                            except Exception:
                                pass

                    if auto_answer:
                        inputs = d_loc.locator('textarea, input[type="text"]')
                        for i_idx in range(inputs.count()):
                            inp = inputs.nth(i_idx)
                            try:
                                val = inp.input_value()
                                if not val or not val.strip():
                                    inp.fill(default_answer)
                                    time.sleep(0.5)
                            except Exception:
                                pass

                    submit_selectors = [
                        'div[role="button"]:has-text("Gửi")',
                        'div[role="button"]:has-text("Gửi cho quản trị viên")',
                        'div[role="button"]:has-text("Hoàn tất")',
                        'div[role="button"]:has-text("Xác nhận")',
                        'div[role="button"]:has-text("Submit")',
                        'button:has-text("Gửi")',
                        'button:has-text("Submit")',
                        'button:has-text("Hoàn tất")',
                        'button:has-text("Xác nhận")',
                        'button:has-text("Gửi cho quản trị viên")'
                    ]
                    submit_clicked = False
                    for s_sel in submit_selectors:
                        sub_btn = d_loc.locator(s_sel).first
                        if sub_btn.count() > 0 and sub_btn.is_visible():
                            self._safe_click(page, sub_btn)
                            submit_clicked = True
                            time.sleep(3)
                            break

                    if not submit_clicked:
                        self.log("Không tìm thấy nút Gửi câu hỏi trong modal, thử đóng modal...", "info")

            time.sleep(2.5)
            after_text = page.locator("body").inner_text()
            if any(w in after_text for w in ["Đã tham gia", "Joined", "Bạn là thành viên"]):
                return "SUCCESS", "Đã tham gia thành công"
            elif any(w in after_text for w in ["Đã gửi yêu cầu", "Đã yêu cầu", "Requested", "Yêu cầu tham gia đã gửi", "Chờ phê duyệt", "Hủy yêu cầu"]):
                return "PENDING", "Đã gửi yêu cầu tham gia (Chờ admin duyệt)"
            else:
                # Kiểm tra xem nút tham gia còn hiển thị hay đã đổi trạng thái
                join_still_there = False
                for sel in ['div[role="button"]:has-text("Tham gia nhóm")', 'div[role="button"]:has-text("Join group")']:
                    l = page.locator(sel).first
                    if l.count() > 0 and l.is_visible():
                        join_still_there = True
                        break
                if not join_still_there:
                    return "SUCCESS", "Đã gửi yêu cầu tham gia nhóm thành công"
                else:
                    return "FAILED", "Nút Tham gia nhóm chưa chuyển trạng thái"

        except Exception as je:
            return "FAILED", str(je)

    # =========================================================================
    # 3. REAL-TIME STORAGE UPDATE & WEBSOCKET SYNC
    # =========================================================================
    def _save_group_to_storage(
        self,
        account_id: str,
        role_type: str,
        role_url: Optional[str],
        role_name: Optional[str],
        group_item: Dict[str, Any]
    ):
        """Immediately adds newly joined group to JSON cache and fires real-time sync event"""
        try:
            role_key = role_type if role_type == "personal" else f"page_{re.sub(r'[^a-zA-Z0-9_]', '_', role_url or role_name or 'page')}"
            data_file = Path("data") / f"groups_{account_id}_{role_key}.json"

            existing_groups = []
            if data_file.exists():
                try:
                    existing_groups = json.loads(data_file.read_text(encoding="utf-8"))
                except Exception:
                    existing_groups = []

            g_url = group_item.get("url", "").rstrip("/")
            g_id = str(group_item.get("id", "")).strip()
            already_there = False
            for eg in existing_groups:
                eg_url = (eg.get("url") or "").rstrip("/")
                eg_id = str(eg.get("id") or "").strip()
                if (g_id and eg_id == g_id) or (g_url and eg_url == g_url):
                    already_there = True
                    break

            if not already_there:
                new_entry = {
                    "id": g_id,
                    "name": group_item.get("name", f"Nhóm {g_id}"),
                    "url": group_item.get("url", ""),
                    "privacy": group_item.get("privacy", "Nhóm công khai"),
                    "member_count": group_item.get("member_count", 0),
                    "members_str": group_item.get("members_str", "--"),
                    "posts_today": group_item.get("posts_today", 0),
                    "posts_today_str": group_item.get("posts_today_str", "--"),
                    "moderation": group_item.get("moderation", "Tự do đăng"),
                    "is_moderated": group_item.get("is_moderated", False),
                    "has_questions": group_item.get("has_questions", False),
                    "joined_at": time.time()
                }
                existing_groups.append(new_entry)
                data_file.write_text(json.dumps(existing_groups, ensure_ascii=False, indent=2), encoding="utf-8")
                self.log(f"Đã lưu nhóm mới '{new_entry['name']}' vào kho lưu trữ {data_file.name} (Tổng: {len(existing_groups)} nhóm)", "success")

                xlsx_file = data_file.with_suffix(".xlsx")
                if xlsx_file.exists():
                    try:
                        import pandas as pd
                        df_pd = pd.DataFrame(existing_groups)
                        df_pd.to_excel(xlsx_file, index=False)
                    except Exception:
                        pass

                if self.sync_callback:
                    try:
                        self.sync_callback("join", new_entry, account_id, role_key)
                    except Exception as sce:
                        self.log(f"Lỗi sync callback: {sce}", "warning")
        except Exception as se:
            self.log(f"Lỗi lưu trữ nhóm mới vào ổ đĩa: {se}", "warning")


# Global singleton instance
group_search_join_engine = GroupSearchJoinEngine()
