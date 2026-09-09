"""
JAutoFb - Centralized Browser Session Manager

Goals:
- Exactly one Session Owner per account/profile inside the JAuto process.
- Reuse one persistent Chrome session instead of launching a new context per task.
- Serialize account-scoped automation by default to prevent profile/session races.
- Keep Chrome alive after a task finishes; only close it explicitly or at application shutdown.
- Reconnect to an already-running Chrome through CDP after an application restart.
- Never delete Chrome Singleton/Lock files while a browser may still be running.

This module intentionally does not implement or improve platform-detection evasion.
It focuses on session integrity, isolation, lifecycle and crash safety.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from playwright.sync_api import Page, Playwright, sync_playwright

from account_manager import get_playwright_launch_args


class BrowserSessionError(RuntimeError):
    """Base error for browser-session failures."""


class BrowserSessionManager:
    _instance: Optional["BrowserSessionManager"] = None
    _singleton_lock = threading.Lock()

    def __new__(cls) -> "BrowserSessionManager":
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_manager()
            return cls._instance

    def _init_manager(self) -> None:
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.base_port = 9300
        self.manager_lock = threading.RLock()
        self.idle_timeout_seconds = 30 * 60

    # ------------------------------------------------------------------
    # Account identity / locking
    # ------------------------------------------------------------------
    def get_port_for_account(self, account_id: str) -> int:
        """Stable localhost CDP port per account."""
        digits = "".join(ch for ch in str(account_id) if ch.isdigit())
        if digits:
            return self.base_port + int(digits)

        # Stable hash without Python's randomized hash().
        import hashlib
        digest = hashlib.sha256(str(account_id).encode("utf-8")).hexdigest()
        return self.base_port + (int(digest[:8], 16) % 500) + 1

    def _get_or_create_record(
        self,
        account_id: str,
        profile_dir: Path,
        proxy_parsed: Optional[Dict[str, Any]],
        headless: bool,
    ) -> Dict[str, Any]:
        profile_path = Path(profile_dir).resolve()
        with self.manager_lock:
            record = self.sessions.get(account_id)
            if record is None:
                record = {
                    "account_id": account_id,
                    "profile_dir": str(profile_path),
                    "port": self.get_port_for_account(account_id),
                    "active_pages": 0,
                    "pages": set(),
                    "pw": None,
                    "context": None,
                    "browser": None,
                    "is_cdp": False,
                    "profile_lock": threading.RLock(),
                    "action_lock": threading.Lock(),
                    "state": "STOPPED",
                    "started_at": None,
                    "last_used_at": None,
                    "headless": bool(headless),
                    "proxy": proxy_parsed,
                }
                self.sessions[account_id] = record
            else:
                # The profile path is immutable for a live session.
                old_profile = Path(record["profile_dir"]).resolve()
                if old_profile != profile_path:
                    raise BrowserSessionError(
                        f"Account {account_id} đã được bind với profile {old_profile}, "
                        f"không thể dùng profile khác trong cùng process."
                    )
            return record

    # ------------------------------------------------------------------
    # CDP / lifecycle helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _read_json(url: str, timeout: float = 1.5) -> Dict[str, Any]:
        req = urllib.request.Request(url, headers={"User-Agent": "JAutoFb-SessionManager"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_cdp_version(self, port: int) -> Optional[Dict[str, Any]]:
        try:
            return self._read_json(f"http://127.0.0.1:{port}/json/version")
        except Exception:
            return None

    def is_port_alive(self, port: int) -> bool:
        data = self.get_cdp_version(port)
        return bool(data and (data.get("webSocketDebuggerUrl") or data.get("Browser")))

    def _context_alive(self, record: Dict[str, Any]) -> bool:
        ctx = record.get("context")
        if ctx is None:
            return False
        try:
            _ = ctx.pages
            return True
        except Exception:
            return False

    def _attach_existing_cdp(self, record: Dict[str, Any]) -> bool:
        port = record["port"]
        if not self.is_port_alive(port):
            return False

        pw = None
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            contexts = browser.contexts
            if not contexts:
                browser.close()
                pw.stop()
                return False

            context = contexts[0]
            record["pw"] = pw
            record["browser"] = browser
            record["context"] = context
            record["is_cdp"] = True
            record["state"] = "READY"
            record["started_at"] = record["started_at"] or time.time()
            record["last_used_at"] = time.time()
            return True
        except Exception:
            if pw:
                try:
                    pw.stop()
                except Exception:
                    pass
            return False

    def _launch_persistent(self, record: Dict[str, Any]) -> None:
        profile_path = Path(record["profile_dir"])
        profile_path.mkdir(parents=True, exist_ok=True)

        # IMPORTANT: never blindly delete Singleton/Lock files. If Chrome is
        # still alive, deleting them can corrupt the profile/session.
        pw = sync_playwright().start()
        try:
            launch_cfg = get_playwright_launch_args(
                proxy_parsed=record.get("proxy"),
                headless=record["headless"],
            )
            args = list(launch_cfg.get("args", []))
            port_arg = f"--remote-debugging-port={record['port']}"
            if not any(a.startswith("--remote-debugging-port=") for a in args):
                args.append(port_arg)
            launch_cfg["args"] = args

            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                **launch_cfg,
            )

            record["pw"] = pw
            record["browser"] = None
            record["context"] = context
            record["is_cdp"] = False
            record["state"] = "READY"
            record["started_at"] = time.time()
            record["last_used_at"] = time.time()
        except Exception:
            try:
                pw.stop()
            except Exception:
                pass
            raise

    def _ensure_session(self, record: Dict[str, Any]) -> None:
        if self._context_alive(record):
            record["state"] = "READY"
            record["last_used_at"] = time.time()
            return

        # First preference: reconnect to a Chrome process that already owns the
        # account's CDP port. This also works after the FastAPI process restarts.
        if self._attach_existing_cdp(record):
            return

        # If our record says a browser exists but the context is gone, clean up
        # only our own Python handles before launching a fresh session.
        self._dispose_handles(record)
        self._launch_persistent(record)

    def _dispose_handles(self, record: Dict[str, Any]) -> None:
        """Dispose our Playwright handles. Does not kill arbitrary Chrome PIDs."""
        browser = record.get("browser")
        context = record.get("context")
        pw = record.get("pw")

        # For a CDP attachment, Browser.close() may affect the target browser
        # depending on the Playwright/runtime combination. We therefore only
        # stop the Playwright-side connection here and leave Chrome untouched.
        if record.get("is_cdp"):
            if pw:
                try:
                    pw.stop()
                except Exception:
                    pass
        else:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            if pw:
                try:
                    pw.stop()
                except Exception:
                    pass

        record["pw"] = None
        record["browser"] = None
        record["context"] = None
        record["pages"] = set()
        record["active_pages"] = 0
        record["state"] = "STOPPED"

    # ------------------------------------------------------------------
    # Public session API
    # ------------------------------------------------------------------
    def acquire_page(
        self,
        account_id: str,
        profile_dir: Path,
        proxy_parsed: Optional[Dict[str, Any]] = None,
        headless: bool = False,
        exclusive: bool = True,
    ) -> Tuple[Any, Page, bool]:
        """
        Return (context_or_browser, page, is_cdp).

        Default behaviour is EXCLUSIVE per account: a caller holds the account
        action lock until release_page(). This prevents multiple engines from
        navigating/switching roles/closing tabs on the same session concurrently.
        """
        record = self._get_or_create_record(account_id, profile_dir, proxy_parsed, headless)

        if exclusive:
            record["action_lock"].acquire()

        try:
            with record["profile_lock"]:
                self._ensure_session(record)
                context = record["context"]
                if context is None:
                    raise BrowserSessionError(f"Không tạo được session cho account {account_id}")

                page = context.new_page()
                record["pages"].add(page)
                record["active_pages"] += 1
                record["last_used_at"] = time.time()
                return (record["browser"] if record["is_cdp"] else context, page, record["is_cdp"])
        except Exception:
            if exclusive:
                try:
                    record["action_lock"].release()
                except Exception:
                    pass
            raise

    def open_interactive_page(
        self,
        account_id: str,
        profile_dir: Path,
        proxy_parsed: Optional[Dict[str, Any]] = None,
    ) -> Page:
        """Open/reuse a visible session for manual login/inspection.

        The returned page is intentionally left open and is not action-locked;
        this method is for the explicit login UI flow only.
        """
        record = self._get_or_create_record(account_id, profile_dir, proxy_parsed, False)
        with record["profile_lock"]:
            record["headless"] = False
            self._ensure_session(record)
            context = record["context"]
            if context is None:
                raise BrowserSessionError(f"Không tạo được interactive session cho {account_id}")
            page = context.new_page()
            record["pages"].add(page)
            record["active_pages"] += 1
            record["last_used_at"] = time.time()
            return page

    def release_page(
        self,
        account_id: str,
        browser_or_context: Any,
        page: Page,
        is_cdp: bool,
        release_lock: bool = True,
    ) -> None:
        """Close only the task page; KEEP the account browser session alive."""
        with self.manager_lock:
            record = self.sessions.get(account_id)

        if not record:
            try:
                page.close()
            except Exception:
                pass
            return

        try:
            with record["profile_lock"]:
                try:
                    page.close()
                except Exception:
                    pass
                record["pages"].discard(page)
                record["active_pages"] = max(0, int(record["active_pages"]) - 1)
                record["last_used_at"] = time.time()
        finally:
            if release_lock:
                try:
                    record["action_lock"].release()
                except RuntimeError:
                    # Caller may have acquired with exclusive=False.
                    pass

    def close_account_session(self, account_id: str, force: bool = False) -> bool:
        """Explicitly close a managed account session."""
        with self.manager_lock:
            record = self.sessions.get(account_id)
        if not record:
            return False

        with record["profile_lock"]:
            if record["active_pages"] > 0 and not force:
                raise BrowserSessionError(
                    f"Account {account_id} còn {record['active_pages']} page đang hoạt động. "
                    "Dùng force=True khi thực sự muốn đóng."
                )
            self._dispose_handles(record)
            return True

    def cleanup_idle_sessions(self, idle_timeout_seconds: Optional[int] = None) -> int:
        """Close sessions that have been explicitly idle for too long."""
        timeout = int(idle_timeout_seconds or self.idle_timeout_seconds)
        now = time.time()
        closed = 0

        with self.manager_lock:
            ids = list(self.sessions.keys())

        for account_id in ids:
            record = self.sessions.get(account_id)
            if not record:
                continue
            if record.get("active_pages", 0) > 0:
                continue
            last_used = record.get("last_used_at") or record.get("started_at") or now
            if now - last_used < timeout:
                continue
            try:
                if self.close_account_session(account_id):
                    closed += 1
            except Exception:
                pass
        return closed

    def get_session_status(self, account_id: str) -> Dict[str, Any]:
        with self.manager_lock:
            record = self.sessions.get(account_id)
            if not record:
                return {"account_id": account_id, "state": "STOPPED"}
            return {
                "account_id": account_id,
                "profile_dir": record["profile_dir"],
                "port": record["port"],
                "state": record["state"],
                "active_pages": record["active_pages"],
                "is_cdp": record["is_cdp"],
                "started_at": record["started_at"],
                "last_used_at": record["last_used_at"],
            }

    def close_all(self, force: bool = True) -> None:
        with self.manager_lock:
            ids = list(self.sessions.keys())
        for account_id in ids:
            try:
                self.close_account_session(account_id, force=force)
            except Exception:
                pass


browser_session_mgr = BrowserSessionManager()
