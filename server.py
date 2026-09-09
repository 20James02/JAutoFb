"""
FastAPI Server for Facebook Auto Poster Web Dashboard
Provides REST API & WebSocket for real-time monitoring and control.
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import os
import re
import json
import time
import asyncio
import subprocess
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import quote
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel

from bulk_engine import BulkPosterEngine
from account_manager import account_mgr, get_account_cookies_string, check_proxy_health, parse_proxy
from scanner_service import scan_account_pages, scan_account_groups
from group_deep_scanner import group_deep_scanner
from interaction_engine import interaction_engine
from fast_group_scraper import scrape_groups_batch, export_groups_to_excel, GroupInfo
from group_leave_engine import group_leave_engine
from post_category_manager import post_cat_mgr
from group_comment_engine import group_comment_engine
from group_bump_engine import group_bump_engine
from comment_shield_engine import comment_shield_engine
from reels_publisher_engine import reels_publisher_engine
from meta_sentinel import meta_sentinel
from group_search_join_engine import group_search_join_engine
from page_matrix_manager import page_matrix_mgr
from multi_platform_manager import instagram_mgr, threads_mgr, zalo_mgr
from cross_page_engine import cross_page_engine
from video_mutator import video_mutator
from uid_scraper_engine import uid_scraper_engine
from marketplace_engine import marketplace_engine
from messenger_engine import messenger_engine
from friendship_engine import friendship_engine
from community_invite_engine import community_invite_engine
from content_clone_engine import content_clone_engine
from token_tool_engine import token_tool_engine
from published_posts_manager import published_posts_mgr

app = FastAPI(title="Facebook Auto Poster Dashboard")

# Mount Uploads directory for images and videos
uploads_dir = Path("data/uploads")
uploads_dir.mkdir(parents=True, exist_ok=True)
for sub in ["marketplace", "reels", "posts", "comments", "inbox", "instagram", "general"]:
    (uploads_dir / sub).mkdir(parents=True, exist_ok=True)

app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")
app.mount("/data/uploads", StaticFiles(directory=str(uploads_dir)), name="data_uploads")

# Global variables & connected websockets with tab-isolation mapping
from collections import deque
active_connections: List[WebSocket] = []
ws_clients: Dict[WebSocket, Dict[str, Any]] = {}
recent_logs: deque = deque(maxlen=300)
loop: Optional[asyncio.AbstractEventLoop] = None

def broadcast_log(line: str, level: str = "info", account_id: Optional[str] = None, channel: Optional[str] = None, tab_id: Optional[str] = None):
    global loop, active_connections, ws_clients, recent_logs
    if not line:
        return

    time_str = time.strftime("%H:%M:%S")

    if not channel:
        lower_line = line.lower()
        if "[leave_group]" in lower_line or "rời nhóm" in lower_line:
            channel = "leave"
        elif "[gi]" in lower_line or "tương tác nhóm" in lower_line or "lướt newsfeed" in lower_line or ("nuôi nick" in lower_line and "threads" not in lower_line and "instagram" not in lower_line):
            channel = "interact"
        elif "[gc]" in lower_line or "comment nhóm" in lower_line or "bình luận nhóm" in lower_line:
            channel = "comment"
        elif "[bump]" in lower_line or "đẩy top" in lower_line or "bump" in lower_line:
            channel = "bump"
        elif "[reels]" in lower_line or "reels" in lower_line:
            channel = "reels"
        elif "[shield]" in lower_line or "khiên" in lower_line or "ẩn comment" in lower_line or "sentinel" in lower_line:
            channel = "shield"
        elif "[group_discovery]" in lower_line or "tìm kiếm nhóm" in lower_line or "tham gia nhóm" in lower_line:
            channel = "join"
        elif ("đăng bài" in lower_line or "đăng nhóm" in lower_line or "bulk" in lower_line or 
              "khởi động trình duyệt" in lower_line or "xác nhận đã đăng nhập" in lower_line or
              "thiết lập vai trò" in lower_line or "điều hướng tới" in lower_line or
              "lướt xem nội dung" in lower_line or "nhập nội dung bài viết" in lower_line or
              "đính kèm" in lower_line or "nhấn nút đăng" in lower_line or
              "đã đăng thành công" in lower_line or "thả cảm xúc" in lower_line or
              "tự động bình luận" in lower_line or "gửi bình luận" in lower_line or "bình luận #" in lower_line or
              "nghỉ giãn cách" in lower_line or "bài id #" in lower_line or "đang đăng" in lower_line):
            channel = "post"
        elif "quét nhóm" in lower_line or "deep scan" in lower_line or "làm giàu dữ liệu" in lower_line or "quét siêu tốc" in lower_line or ("quét" in lower_line and "uid" not in lower_line and "reaction" not in lower_line and "bạn bè" not in lower_line):
            channel = "scan"
        elif "[instagram]" in lower_line or "[ig]" in lower_line or "instagram" in lower_line:
            channel = "instagram"
        elif "[threads]" in lower_line or "[th]" in lower_line or "threads" in lower_line:
            channel = "threads"
        elif "[marketplace]" in lower_line or "marketplace" in lower_line or "rao vặt" in lower_line or "niêm yết" in lower_line:
            channel = "marketplace"
        elif "[friendship]" in lower_line or "[backup checkpoint]" in lower_line or "kết bạn" in lower_line or "checkpoint" in lower_line or ("bạn bè" in lower_line and "mời" not in lower_line):
            channel = "friendship"
        elif "[uid scraper]" in lower_line or "[scraper]" in lower_line or "quét uid" in lower_line or "cào uid" in lower_line or "reactions" in lower_line:
            channel = "scraper"
        elif "[mời bạn bè]" in lower_line or "[duyệt nhóm]" in lower_line or "[block admin]" in lower_line or "mời vào nhóm" in lower_line or "mời thích trang" in lower_line or "mời page" in lower_line:
            channel = "invite"
        elif "[clone content]" in lower_line or "[repost content]" in lower_line or "cào bài" in lower_line or "reup" in lower_line:
            channel = "clone"
        elif "[messenger" in lower_line or "[inbox]" in lower_line or "tin nhắn" in lower_line or "auto-reply" in lower_line:
            channel = "inbox"
        elif "[token tool]" in lower_line or "[cookie tool]" in lower_line or "[dcom ip]" in lower_line or "check token" in lower_line or "check cookie" in lower_line or "đổi ip" in lower_line:
            channel = "token"
        elif "[zalo" in lower_line or "zalo" in lower_line:
            channel = "zalo"
        else:
            channel = "general"

    recent_logs.append({
        "line": line,
        "time": time_str,
        "level": level,
        "account_id": account_id,
        "channel": channel,
        "tab_id": tab_id
    })

    if not active_connections:
        return

    msg = json.dumps({
        "type": "log",
        "line": line,
        "time": time_str,
        "level": level,
        "account_id": account_id,
        "channel": channel,
        "tab_id": tab_id
    }, ensure_ascii=False)

    for ws in list(active_connections):
        try:
            client_meta = ws_clients.get(ws, {})
            client_tab = client_meta.get("tab_id")

            # Chỉ lọc tab_id cho các tác vụ đơn lẻ tạm thời (scan, join, leave) nếu client_tab khác tab_id
            if tab_id and client_tab and channel in ["scan", "join", "leave"]:
                if client_tab != tab_id:
                    continue

            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(ws.send_text(msg), loop)
        except Exception:
            pass

def broadcast_group_sync(action: str, group: Dict[str, Any], account_id: str, role_key: str):
    """Phát sóng sự kiện đồng bộ nhóm thời gian thực (rời nhóm hoặc tham gia nhóm) tới toàn bộ WebSocket clients"""
    global loop, active_connections
    if not active_connections or not loop:
        return
    msg = json.dumps({
        "type": "group_sync",
        "action": action,
        "group": group,
        "account_id": account_id,
        "role_key": role_key,
        "timestamp": time.time()
    }, ensure_ascii=False)
    for ws in list(active_connections):
        try:
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(ws.send_text(msg), loop)
        except Exception:
            pass

def broadcast_group_discover_stream(group: Dict[str, Any], account_id: Optional[str] = None, tab_id: Optional[str] = None):
    global loop, active_connections
    if not active_connections or not loop:
        return
    msg = json.dumps({
        "type": "group_discover_stream",
        "group": group,
        "account_id": account_id,
        "tab_id": tab_id,
        "timestamp": time.time()
    }, ensure_ascii=False)
    for ws in list(active_connections):
        try:
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(ws.send_text(msg), loop)
        except Exception:
            pass

def broadcast_group_scan_stream(group: Dict[str, Any], account_id: Optional[str] = None, tab_id: Optional[str] = None):
    global loop, active_connections
    if not active_connections or not loop:
        return
    msg = json.dumps({
        "type": "group_scan_stream",
        "group": group,
        "account_id": account_id,
        "tab_id": tab_id,
        "timestamp": time.time()
    }, ensure_ascii=False)
    for ws in list(active_connections):
        try:
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(ws.send_text(msg), loop)
        except Exception:
            pass

# Cài đặt callback đồng bộ thời gian thực cho các engines
group_leave_engine.sync_callback = broadcast_group_sync
group_search_join_engine.sync_callback = broadcast_group_sync
group_search_join_engine.log_callback = lambda l, lvl: broadcast_log(l, lvl, channel="join")

def broadcast_status(stats: Dict[str, Any], account_id: Optional[str] = None):
    global loop, active_connections
    if not active_connections:
        return
    msg = json.dumps({"type": "status", "stats": stats, "account_id": account_id})
    for ws in list(active_connections):
        try:
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(ws.send_text(msg), loop)
        except Exception:
            pass

active_engines: Dict[str, BulkPosterEngine] = {}

def get_engine_for_account(account_id: str, tab_id: Optional[str] = None) -> BulkPosterEngine:
    if account_id not in active_engines:
        eng = BulkPosterEngine(account_id=account_id)
        active_engines[account_id] = eng
    else:
        eng = active_engines[account_id]

    if tab_id:
        eng.tab_id = tab_id

    def acc_log(line: str, level: str = "info", aid: str = None):
        broadcast_log(line, level, account_id=aid or account_id, channel="post")

    def acc_status(stats: Dict[str, Any], aid: str = None):
        broadcast_status(stats, aid or account_id)

    eng.log_callback = acc_log
    eng.status_callback = acc_status
    return eng

# Default engine for posts API and backward compatibility
engine = get_engine_for_account("default")
interaction_engine.log_callback = lambda l, lvl="info", aid=None: broadcast_log(l, lvl, account_id=aid, channel="interact")
group_deep_scanner.log_callback = lambda l, lvl="info", aid=None: broadcast_log(l, lvl, account_id=aid, channel="scan")
group_leave_engine.log_callback = lambda l, lvl="info", aid=None: broadcast_log(l, lvl, account_id=aid, channel="leave")
group_comment_engine.log_callback = lambda l, lvl="info", aid=None: broadcast_log(l, lvl, account_id=aid, channel="comment")
group_bump_engine.log_callback = lambda l, lvl="info", aid=None: broadcast_log(l, lvl, account_id=aid, channel="bump")
comment_shield_engine.log_callback = lambda l, lvl="info", aid=None: broadcast_log(l, lvl, account_id=aid, channel="shield")
reels_publisher_engine.log_callback = lambda l, lvl="info", aid=None: broadcast_log(l, lvl, account_id=aid, channel="reels")
cross_page_engine.log_callback = lambda l, lvl="info", aid=None: broadcast_log(l, lvl, account_id=aid, channel="clone")

def broadcast_sentinel_alert(alert_data: Dict[str, Any]):
    global loop, active_connections
    if not active_connections:
        return
    msg = json.dumps({"type": "meta_sentinel_alert", "alert": alert_data})
    for ws in list(active_connections):
        try:
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(ws.send_text(msg), loop)
        except Exception:
            pass

meta_sentinel.alert_callback = broadcast_sentinel_alert
meta_sentinel.log_callback = broadcast_log

@app.on_event("startup")
async def startup_event():
    global loop
    loop = asyncio.get_running_loop()

    async def schedule_runner():
        while True:
            try:
                def cb_ig(m, lvl="info"): broadcast_log(f"📸 [Lịch Hẹn IG] {m}", lvl)
                def cb_th(m, lvl="info"): broadcast_log(f"🧵 [Lịch Hẹn Threads] {m}", lvl)
                instagram_mgr.run_due_schedules(cb_ig)
                threads_mgr.run_due_schedules(cb_th)
            except Exception:
                pass
            await asyncio.sleep(20)

    asyncio.create_task(schedule_runner())

# Serve Frontend HTML
@app.get("/", response_class=HTMLResponse)
async def get_index():
    html_file = Path(__file__).parent / "static" / "index.html"
    if html_file.exists():
        return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dashboard UI file not found</h1>", status_code=404)

# WebSocket for Logs & Live Stats with Tab Scoping
@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    tab_id = websocket.query_params.get("tab_id")
    profile_id = websocket.query_params.get("profile_id")
    active_connections.append(websocket)
    ws_clients[websocket] = {"tab_id": tab_id, "profile_id": profile_id}
    
    # Send current stats immediately for relevant engine
    target_engine = active_engines.get(profile_id) if (profile_id and profile_id in active_engines) else engine
    try:
        await websocket.send_text(json.dumps({"type": "status", "stats": target_engine.stats, "account_id": profile_id}))
    except Exception:
        pass

    # Replay recent activity logs for this client
    try:
        replay_list = list(recent_logs)[-60:]
        for old_log in replay_list:
            if old_log.get("tab_id") and tab_id and old_log.get("channel") in ["scan", "join", "leave"] and old_log.get("tab_id") != tab_id:
                continue
            await websocket.send_text(json.dumps({
                "type": "log",
                "line": old_log["line"],
                "time": old_log["time"],
                "level": old_log["level"],
                "account_id": old_log.get("account_id"),
                "channel": old_log.get("channel"),
                "tab_id": old_log.get("tab_id")
            }, ensure_ascii=False))
    except Exception:
        pass

    try:
        while True:
            msg_text = await websocket.receive_text()
            try:
                data = json.loads(msg_text)
                if data.get("type") == "register_tab":
                    ws_clients[websocket]["tab_id"] = data.get("tab_id")
                    ws_clients[websocket]["profile_id"] = data.get("profile_id")
            except Exception:
                pass
    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)
        if websocket in ws_clients:
            del ws_clients[websocket]

@app.get("/api/logs/recent")
def get_recent_logs(account_id: Optional[str] = None, channel: Optional[str] = None, limit: int = 100):
    logs = list(recent_logs)
    if channel:
        logs = [l for l in logs if l.get("channel") == channel]
    if account_id:
        logs = [l for l in logs if not l.get("account_id") or l.get("account_id") == account_id]
    return {"logs": logs[-limit:]}

# =========================================================
# PUBLISHED POSTS HISTORY APIs (LỊCH SỬ BÀI ĐÃ ĐĂNG)
# =========================================================
class UpdatePublishedPostStatusRequest(BaseModel):
    post_id: Optional[str] = None
    id: Optional[str] = None
    status: str  # "approved" hoặc "pending"

class MarkPostBumpedRequest(BaseModel):
    post_url_or_id: Optional[str] = None
    post_url: Optional[str] = None
    url: Optional[str] = None
    id: Optional[str] = None
    comment_text: Optional[str] = None

class ClearPublishedPostsRequest(BaseModel):
    target_type: Optional[str] = None

@app.get("/api/published-posts")
def api_get_published_posts(
    target_type: Optional[str] = None,
    status: Optional[str] = None,
    is_bumped: Optional[bool] = None,
    account_id: Optional[str] = None,
    search: Optional[str] = None
):
    posts = published_posts_mgr.get_posts(
        target_type=target_type,
        status=status,
        is_bumped=is_bumped,
        account_id=account_id,
        search=search
    )
    return {
        "success": True,
        "total": len(posts),
        "posts": posts
    }

@app.post("/api/published-posts/update-status")
def api_update_published_post_status(req: UpdatePublishedPostStatusRequest):
    target_id = req.post_id or req.id
    if not target_id:
        return {"success": False, "error": "Thiếu post_id hoặc id"}
    ok = published_posts_mgr.update_status(target_id, req.status)
    return {"success": ok}

@app.post("/api/published-posts/mark-bumped")
def api_mark_published_post_bumped(req: MarkPostBumpedRequest):
    target_key = req.post_url_or_id or req.post_url or req.url or req.id
    if not target_key:
        return {"success": False, "error": "Thiếu thông tin nhận diện bài viết"}
    ok = published_posts_mgr.mark_as_bumped(target_key, req.comment_text)
    return {"success": ok}

@app.delete("/api/published-posts/{post_id}")
def api_delete_published_post(post_id: str):
    ok = published_posts_mgr.delete_post(post_id)
    return {"success": ok}

@app.post("/api/published-posts/clear")
def api_clear_published_posts(req: ClearPublishedPostsRequest = ClearPublishedPostsRequest()):
    count = published_posts_mgr.clear_posts(req.target_type)
    return {"success": True, "cleared_count": count}

# Post Data APIs
@app.get("/api/posts")
def get_posts():
    return {
        "file_path": engine.active_file_path,
        "total": len(engine.posts),
        "posts": engine.posts
    }

class PostUpdateRequest(BaseModel):
    post_id: int
    fields: Dict[str, Any]

@app.post("/api/posts/update")
def update_post(req: PostUpdateRequest):
    for p in engine.posts:
        if p.get("Id") == req.post_id:
            p.update(req.fields)
            engine.save_posts()
            engine.stats["checked"] = sum(1 for x in engine.posts if x.get("Checked", False))
            engine.update_status()
            return {"success": True, "post": p}
    return JSONResponse(status_code=404, content={"error": "Post not found"})

class ToggleAllRequest(BaseModel):
    checked: bool

@app.post("/api/posts/toggle-all")
def toggle_all_posts(req: ToggleAllRequest):
    for p in engine.posts:
        p["Checked"] = req.checked
    engine.save_posts()
    engine.stats["checked"] = sum(1 for x in engine.posts if x.get("Checked", False))
    engine.update_status()
    return {"success": True, "checked": req.checked, "count": len(engine.posts)}

@app.post("/api/upload")
async def upload_jsonl(file: UploadFile = File(...)):
    dest = Path("data") / file.filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    dest.write_bytes(content)
    engine.load_posts(str(dest))
    return {"success": True, "filename": file.filename, "total_posts": len(engine.posts)}

@app.post("/api/upload-media")
async def api_upload_media(file: UploadFile = File(...), folder: str = "general"):
    allowed_folders = {"marketplace", "reels", "posts", "comments", "inbox", "instagram", "threads", "general"}
    if folder not in allowed_folders:
        folder = "general"
    
    target_dir = Path("data/uploads") / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    
    orig_name = Path(file.filename).name if file.filename else "upload.dat"
    clean_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", orig_name)
    ext = Path(clean_name).suffix.lower()
    ts = int(time.time() * 1000)
    safe_name = f"{ts}_{clean_name}"
    dest = target_dir / safe_name
    
    content = await file.read()
    dest.write_bytes(content)
    
    rel_url = f"/uploads/{folder}/{safe_name}"
    rel_path = f"data/uploads/{folder}/{safe_name}"
    
    return {
        "success": True,
        "filename": orig_name,
        "saved_as": safe_name,
        "url": rel_url,
        "path": rel_path,
        "size": len(content),
        "content_type": file.content_type
    }

@app.post("/api/upload-media-batch")
async def api_upload_media_batch(files: List[UploadFile] = File(...), folder: str = "general"):
    results = []
    for f in files:
        res = await api_upload_media(f, folder)
        results.append(res)
    return {"success": True, "uploaded": results, "count": len(results)}


# =========================================================
# Local File & Folder Browser Endpoints (Windows Native Dialogs)
# =========================================================
class BrowseFolderRequest(BaseModel):
    initial_dir: Optional[str] = ""

class BrowseFilesRequest(BaseModel):
    initial_dir: Optional[str] = ""

class ScanFolderRequest(BaseModel):
    folder_path: str

def _run_native_folder_picker(initial_dir: str = "") -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes('-topmost', 1)
        folder = filedialog.askdirectory(initialdir=initial_dir or None, title="Chọn thư mục chứa hình ảnh/video")
        root.destroy()
        return folder.replace('\\', '/') if folder else ""
    except Exception as e:
        print(f"Error opening folder picker: {e}")
        return ""

def _run_native_files_picker(initial_dir: str = "") -> List[str]:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes('-topmost', 1)
        filetypes = [
            ("Hình ảnh & Video", "*.png;*.jpg;*.jpeg;*.webp;*.gif;*.jfif;*.mp4;*.mov"),
            ("Tất cả tệp", "*.*")
        ]
        files = filedialog.askopenfilenames(initialdir=initial_dir or None, title="Chọn các file ảnh/video từ máy tính", filetypes=filetypes)
        root.destroy()
        return [f.replace('\\', '/') for f in files] if files else []
    except Exception as e:
        print(f"Error opening file picker: {e}")
        return []

@app.post("/api/utils/browse-folder")
async def api_browse_folder(req: BrowseFolderRequest = BrowseFolderRequest()):
    folder = await asyncio.to_thread(_run_native_folder_picker, req.initial_dir or "")
    if not folder:
        return {"success": False, "cancelled": True, "path": ""}
    
    p = Path(folder)
    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".jfif", ".mp4", ".mov"}
    imgs = [f.name for f in p.iterdir() if f.is_file() and f.suffix.lower() in valid_exts] if p.exists() and p.is_dir() else []
    return {
        "success": True,
        "cancelled": False,
        "path": folder,
        "image_count": len(imgs),
        "sample_files": imgs[:8]
    }

@app.post("/api/utils/browse-files")
async def api_browse_files(req: BrowseFilesRequest = BrowseFilesRequest()):
    files = await asyncio.to_thread(_run_native_files_picker, req.initial_dir or "")
    if not files:
        return {"success": False, "cancelled": True, "paths": []}
    return {
        "success": True,
        "cancelled": False,
        "paths": files,
        "count": len(files)
    }

@app.post("/api/utils/scan-folder")
def api_scan_folder(req: ScanFolderRequest):
    raw_path = req.folder_path.strip().strip('"').strip("'")
    if not raw_path:
        return {"success": False, "exists": False, "error": "Chưa nhập đường dẫn"}
    p = Path(raw_path)
    if not p.exists():
        return {"success": False, "exists": False, "error": "Thư mục không tồn tại trên máy tính"}
    if not p.is_dir():
        return {"success": False, "exists": False, "error": "Đường dẫn không phải là thư mục"}
    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".jfif", ".mp4", ".mov"}
    try:
        files = [f.name for f in p.iterdir() if f.is_file() and f.suffix.lower() in valid_exts]
        return {
            "success": True,
            "exists": True,
            "path": str(p.resolve()).replace('\\', '/'),
            "image_count": len(files),
            "sample_files": files[:8]
        }
    except Exception as e:
        return {"success": False, "exists": False, "error": str(e)}

@app.get("/api/utils/view-local-image")
def api_view_local_image(path: str):
    p = Path(path.strip().strip('"').strip("'"))
    if p.exists() and p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".jfif"}:
        return FileResponse(path=str(p.resolve()))
    return JSONResponse(status_code=404, content={"error": "File not found or invalid type"})


# Category & Post Management APIs
class CategoryCreateRequest(BaseModel):
    name: str
    description: str = ""

class CategoryUpdateRequest(BaseModel):
    id: str
    name: str
    description: str = ""

class CategoryDeleteRequest(BaseModel):
    id: str

@app.get("/api/categories")
def get_categories():
    return {"categories": post_cat_mgr.get_categories()}

@app.post("/api/categories/create")
def create_category(req: CategoryCreateRequest):
    cat = post_cat_mgr.create_category(req.name, req.description)
    return {"success": True, "category": cat}

@app.post("/api/categories/update")
def update_category(req: CategoryUpdateRequest):
    cat = post_cat_mgr.update_category(req.id, req.name, req.description)
    if cat:
        return {"success": True, "category": cat}
    return JSONResponse(status_code=404, content={"error": "Mục không tồn tại"})

@app.post("/api/categories/delete")
def delete_category(req: CategoryDeleteRequest):
    ok = post_cat_mgr.delete_category(req.id)
    return {"success": ok}

@app.get("/api/categories/{cat_id}/posts")
def get_category_posts(cat_id: str):
    posts = post_cat_mgr.get_posts(cat_id)
    return {"cat_id": cat_id, "total": len(posts), "posts": posts}

class CategoryPostSaveRequest(BaseModel):
    post: Dict[str, Any]

@app.post("/api/categories/{cat_id}/posts/save")
def save_category_post(cat_id: str, req: CategoryPostSaveRequest):
    saved = post_cat_mgr.save_post(cat_id, req.post)
    return {"success": True, "post": saved}

class CategoryPostDeleteRequest(BaseModel):
    post_id: int

@app.post("/api/categories/{cat_id}/posts/delete")
def delete_category_post(cat_id: str, req: CategoryPostDeleteRequest):
    ok = post_cat_mgr.delete_post(cat_id, req.post_id)
    return {"success": ok}

class CategoryPostToggleAllRequest(BaseModel):
    checked: bool

@app.post("/api/categories/{cat_id}/posts/toggle-all")
def toggle_category_posts(cat_id: str, req: CategoryPostToggleAllRequest):
    count = post_cat_mgr.toggle_all_posts(cat_id, req.checked)
    return {"success": True, "checked": req.checked, "count": count}

@app.post("/api/categories/{cat_id}/upload")
async def upload_category_jsonl(cat_id: str, file: UploadFile = File(...)):
    content = await file.read()
    text = content.decode("utf-8", errors="ignore")
    imported = post_cat_mgr.import_jsonl(cat_id, text)
    posts = post_cat_mgr.get_posts(cat_id)
    return {"success": True, "imported_count": imported, "total_posts": len(posts)}

# AI Content Studio APIs & Settings
from ai_content_engine import ai_engine

@app.get("/api/ai/settings")
def api_get_ai_settings():
    return {"success": True, "settings": ai_engine.load_settings()}

@app.post("/api/ai/settings")
def api_save_ai_settings(settings: Dict[str, Any]):
    ok = ai_engine.save_settings(settings)
    return {"success": ok, "message": "Đã lưu cấu hình Prompt & Tài liệu gốc thành công!"}

class AIGeneratePostRequest(BaseModel):
    topic: str
    framework: str = "AIDA"
    tone: str = "hap_dan"
    custom_prompt: Optional[str] = None
    knowledge_base: Optional[str] = None
    target_category_id: Optional[str] = None
    api_key: Optional[str] = None

@app.post("/api/ai/generate-post")
def api_generate_post(req: AIGeneratePostRequest):
    res = ai_engine.generate_post_content(
        topic=req.topic,
        framework=req.framework,
        tone=req.tone,
        custom_prompt=req.custom_prompt,
        knowledge_base=req.knowledge_base,
        api_key=req.api_key
    )
    saved = False
    if req.target_category_id and res.get("content"):
        post_data = {
            "Content": res.get("content", ""),
            "Checked": True,
            "MediaFiles": [],
            "CommentSpintax": "",
            "CommentMedia": ""
        }
        post_cat_mgr.save_post(req.target_category_id, post_data)
        saved = True
    return {"success": True, "data": res, "saved_to_category": saved}

class AIGenerateWallPostRequest(BaseModel):
    topic: str
    category: str = "daily"
    tone: str = "gan_gui"
    custom_prompt: Optional[str] = None
    knowledge_base: Optional[str] = None
    api_key: Optional[str] = None

@app.post("/api/ai/generate-wall-post")
def api_generate_wall_post(req: AIGenerateWallPostRequest):
    res = ai_engine.generate_wall_post(
        topic=req.topic,
        category=req.category,
        tone=req.tone,
        custom_prompt=req.custom_prompt,
        knowledge_base=req.knowledge_base,
        api_key=req.api_key
    )
    return {"success": True, "data": res}

class AIGenerateVariantsRequest(BaseModel):
    source_content: str
    count: int = 10
    target_category_id: Optional[str] = None
    custom_prompt: Optional[str] = None
    knowledge_base: Optional[str] = None
    api_key: Optional[str] = None

@app.post("/api/ai/generate-variants")
def api_generate_variants(req: AIGenerateVariantsRequest):
    variants = ai_engine.generate_variants(
        source_text=req.source_content,
        count=req.count,
        custom_prompt=req.custom_prompt,
        knowledge_base=req.knowledge_base,
        api_key=req.api_key
    )
    saved_count = 0
    if req.target_category_id:
        for v in variants:
            post_data = {
                "Content": v,
                "Checked": True,
                "MediaFiles": [],
                "CommentSpintax": "",
                "CommentMedia": ""
            }
            post_cat_mgr.save_post(req.target_category_id, post_data)
            saved_count += 1
    return {
        "success": True,
        "count": len(variants),
        "variants": variants,
        "saved_count": saved_count
    }

class AIGenerateSeedingRequest(BaseModel):
    product_topic: str
    tone: str = "tu_nhien"
    custom_prompt: Optional[str] = None
    knowledge_base: Optional[str] = None
    api_key: Optional[str] = None

@app.post("/api/ai/generate-seeding")
def api_generate_seeding(req: AIGenerateSeedingRequest):
    res = ai_engine.generate_seeding_dialogue(
        topic=req.product_topic,
        tone=req.tone,
        custom_prompt=req.custom_prompt,
        knowledge_base=req.knowledge_base,
        api_key=req.api_key
    )
    return {"success": True, "data": res}

# Chrome Management APIs
@app.get("/api/chrome/status")
def get_chrome_status(port: int = 9222):
    url = f"http://127.0.0.1:{port}/json/version"
    ready = False
    details = {}
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            if resp.status == 200:
                ready = True
                details = json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass
    return {"ready": ready, "port": port, "details": details}

@app.post("/api/chrome/launch")
def launch_chrome():
    bat_path = Path("start_chrome.bat").resolve()
    if bat_path.exists():
        subprocess.Popen(f'cmd.exe /c start "" "{bat_path}"', shell=True)
        return {"success": True, "message": "Đã gửi lệnh mở Chrome"}
    return JSONResponse(status_code=500, content={"error": "start_chrome.bat not found"})

# Account Management APIs
@app.get("/api/accounts")
def get_accounts():
    return {"accounts": account_mgr.get_accounts()}

@app.get("/api/accounts/{account_id}/roles")
def get_account_roles(account_id: str):
    """Trả về danh sách vai trò khả dụng (Trang cá nhân + các Fanpage) của một Profile cụ thể"""
    acc = next((a for a in account_mgr.get_accounts() if a["id"] == account_id), None)
    if not acc:
        return JSONResponse(status_code=404, content={"success": False, "error": "Account not found", "roles": []})

    pages = []
    pages_file = Path("data") / f"pages_{account_id}.json"
    if pages_file.exists():
        try:
            pages = json.loads(pages_file.read_text(encoding="utf-8"))
        except Exception:
            pages = []

    personal_label = f"👤 Trang cá nhân ({acc.get('name', 'Chính')})"
    roles = [{
        "type": "personal",
        "name": personal_label,
        "url": "",
        "value": "personal"
    }]
    for p in pages:
        p_name = p.get("name", "Fanpage")
        p_url = p.get("url", "")
        roles.append({
            "type": "page",
            "name": f"🚩 Fanpage: {p_name}",
            "url": p_url,
            "value": f"page|{p_url}|{p_name}"
        })

    return {"success": True, "account": acc, "roles": roles}


class AddAccountRequest(BaseModel):
    name: str
    proxy: Optional[str] = ""

@app.post("/api/accounts/add")
def add_account(req: AddAccountRequest):
    acc = account_mgr.add_account(req.name, req.proxy)
    return {"success": True, "account": acc}

class UpdateAccountRequest(BaseModel):
    account_id: str
    name: Optional[str] = None
    proxy: Optional[str] = None
    enabled: Optional[bool] = None

@app.post("/api/accounts/update")
def api_update_account(req: UpdateAccountRequest):
    updates = {}
    if req.name is not None: updates["name"] = req.name
    if req.proxy is not None: updates["proxy"] = req.proxy
    if req.enabled is not None: updates["enabled"] = req.enabled
    
    acc = account_mgr.update_account(req.account_id, updates)
    if acc:
        return {"success": True, "account": acc}
    return JSONResponse(status_code=404, content={"success": False, "error": "Account not found"})

class CheckProxyRequest(BaseModel):
    proxy: str

@app.post("/api/accounts/check-proxy")
def api_check_proxy(req: CheckProxyRequest):
    res = check_proxy_health(req.proxy)
    return res

class ToggleAccountRequest(BaseModel):
    account_id: str
    enabled: bool

@app.post("/api/accounts/toggle")
def toggle_account(req: ToggleAccountRequest):
    ok = account_mgr.toggle_account(req.account_id, req.enabled)
    return {"success": ok}

class DeleteAccountRequest(BaseModel):
    account_id: str

@app.post("/api/accounts/delete")
def delete_account(req: DeleteAccountRequest):
    account_mgr.delete_account(req.account_id)
    return {"success": True}

class LoginSessionRequest(BaseModel):
    account_id: str

@app.post("/api/accounts/login-session")
def open_account_login(req: LoginSessionRequest):
    ok = account_mgr.open_login_session(req.account_id)
    return {"success": ok, "message": "Đã mở cửa sổ đăng nhập Chrome"}

class CheckAccountStatusRequest(BaseModel):
    account_id: str

@app.post("/api/accounts/check-status")
def api_check_account_status(req: CheckAccountStatusRequest):
    acc = account_mgr.check_account_status(req.account_id)
    return {"success": True, "account": acc}

@app.get("/api/dashboard/stats")
def get_dashboard_stats():
    accounts = account_mgr.get_accounts()
    total_acc = len(accounts)
    logged_acc = sum(1 for a in accounts if a.get("logged_in"))
    proxy_acc = sum(1 for a in accounts if a.get("proxy"))
    
    categories = post_cat_mgr.get_categories()
    total_cats = len(categories)
    total_posts = sum(c.get("post_count", 0) for c in categories)
    
    trust_scores = []
    for a in accounts:
        score = 40
        if a.get("logged_in") and a.get("fb_name"):
            score += 40
        if a.get("proxy"):
            score += 20
        trust_scores.append(score)
    avg_trust = round(sum(trust_scores) / max(len(trust_scores), 1))
    
    return {
        "success": True,
        "accounts": {
            "total": total_acc,
            "logged_in": logged_acc,
            "unlogged": total_acc - logged_acc,
            "proxy_count": proxy_acc,
            "avg_trust_score": avg_trust
        },
        "content": {
            "categories": total_cats,
            "posts": total_posts
        },
        "system": {
            "anti_detect_enabled": True,
            "webrtc_protection": True
        }
    }

# Bulk Posting Controls
class StartBulkRequest(BaseModel):
    tab_id: Optional[str] = None
    target_type: str = "personal"
    role_type: str = "personal"
    role_url: Optional[str] = None
    role_name: Optional[str] = None
    group_urls: List[str] = []
    page_url: str = ""
    min_delay: int = 30
    max_delay: int = 60
    dry_run: bool = False
    auto_comment: bool = True
    port: int = 9222
    headless: bool = False
    concurrency: int = 1
    selected_accounts: List[str] = []
    # Tùy chọn mới
    reaction_enabled: bool = False
    reaction_type: str = "LIKE"
    interact_during_cooldown: bool = True
    like_other_posts: bool = False
    location_enabled: bool = False
    location_name: str = ""
    comment_enabled: bool = True
    comment_count: int = 1
    comment_text: str = ""
    comment_image: str = ""
    comment_image_mode: str = "single"  # single, folder_each_comment, folder_separate_images
    comment_attach_text: bool = True
    loop_groups: bool = False
    randomize_groups: bool = False
    error_handling: str = "continue"
    error_cooldown: int = 60
    account_id: Optional[str] = None
    schedule_enabled: bool = False
    schedule_start: str = "08:00"
    schedule_end: str = "22:00"
    category_id: Optional[str] = None
    custom_posts: Optional[List[Dict[str, Any]]] = None

@app.post("/api/bulk/start")
def start_bulk(req: StartBulkRequest):
    acc_id = req.account_id or (req.selected_accounts[0] if req.selected_accounts else "default")
    eng = get_engine_for_account(acc_id, tab_id=req.tab_id)

    if eng.state == "RUNNING":
        return {"success": False, "message": f"Tài khoản [{acc_id}] đang có tiến trình chạy!"}

    # Kiểm tra tránh trùng lặp vai trò Fanpage giữa các tab đang chạy song song
    if req.role_type == "page" and req.role_url:
        for other_aid, other_eng in active_engines.items():
            if other_aid != acc_id and other_eng.state in ["RUNNING", "PAUSED"]:
                other_role_type = other_eng.config.get("role_type")
                other_role_url = other_eng.config.get("role_url")
                if other_role_type == "page" and other_role_url == req.role_url:
                    page_name = req.role_name or req.role_url
                    return {
                        "success": False,
                        "message": f"⚠️ Vai trò Fanpage '{page_name}' hiện đang được sử dụng ở tiến trình của tab tài khoản [{other_aid}]! Vui lòng chọn vai trò khác để tránh trùng lặp vai trò giữa các tab."
                    }

    # Đảm bảo bài viết được đồng bộ từ mục đã chọn hoặc dữ liệu truyền lên
    if req.custom_posts is not None:
        eng.posts = req.custom_posts
        eng.stats["checked"] = sum(1 for x in eng.posts if x.get("Checked", False))
    elif req.category_id:
        cat_posts = post_cat_mgr.get_posts(req.category_id)
        if cat_posts:
            eng.posts = cat_posts
            eng.stats["checked"] = sum(1 for x in eng.posts if x.get("Checked", False))
    elif not eng.posts:
        eng.load_posts(engine.active_file_path)

    config = req.dict()
    # Nếu tab chỉ chọn 1 tài khoản cụ thể thì gán selected_accounts cho eng
    if not config.get("selected_accounts") and acc_id != "default":
        config["selected_accounts"] = [acc_id]

    eng.start_bulk(config, account_mgr.get_accounts())
    return {"success": True, "message": f"Đã bắt đầu tiến trình đăng hàng loạt cho tài khoản [{acc_id}].", "account_id": acc_id}

@app.get("/api/bulk/active-roles")
def get_active_roles():
    active = []
    for aid, eng in active_engines.items():
        if eng.state in ["RUNNING", "PAUSED"]:
            active.append({
                "account_id": aid,
                "role_type": eng.config.get("role_type", "personal"),
                "role_url": eng.config.get("role_url"),
                "role_name": eng.config.get("role_name"),
                "state": eng.state
            })
    return {"active_roles": active}

@app.post("/api/bulk/pause")
def pause_bulk(account_id: Optional[str] = None):
    if account_id and account_id in active_engines:
        eng = active_engines[account_id]
        if eng.state == "RUNNING":
            eng.pause()
            return {"success": True, "message": f"Đã tạm dừng tài khoản [{account_id}].", "account_id": account_id}
        elif eng.state == "PAUSED":
            eng.resume()
            return {"success": True, "message": f"Đã tiếp tục tài khoản [{account_id}].", "account_id": account_id}
        return {"success": False, "message": "Tiến trình không ở trạng thái chạy hoặc tạm dừng."}

    paused_any = False
    resumed_any = False
    for aid, eng in active_engines.items():
        if eng.state == "RUNNING":
            eng.pause()
            paused_any = True
        elif eng.state == "PAUSED":
            eng.resume()
            resumed_any = True
    if paused_any:
        return {"success": True, "message": "Đã tạm dừng các tiến trình đang chạy."}
    if resumed_any:
        return {"success": True, "message": "Đã tiếp tục các tiến trình."}
    return {"success": False, "message": "Không có tiến trình nào đang chạy hoặc tạm dừng."}

@app.post("/api/bulk/stop")
def stop_bulk(account_id: Optional[str] = None):
    if account_id and account_id in active_engines:
        active_engines[account_id].stop()
        return {"success": True, "message": f"Đã gửi lệnh dừng tiến trình cho tài khoản [{account_id}].", "account_id": account_id}

    for aid, eng in active_engines.items():
        if eng.state in ["RUNNING", "PAUSED"]:
            eng.stop()
    return {"success": True, "message": "Đã gửi lệnh dừng tất cả tiến trình."}

@app.get("/api/bulk/status")
def get_bulk_status(account_id: Optional[str] = None):
    if account_id and account_id in active_engines:
        return active_engines[account_id].stats
    return engine.stats

@app.post("/api/upload-comment-image")
async def upload_comment_image(file: UploadFile = File(...)):
    dest = Path("data/uploads") / file.filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    dest.write_bytes(content)
    return {"success": True, "file_path": str(dest.resolve())}

@app.post("/api/upload-comment-folder")
async def upload_comment_folder(files: List[UploadFile] = File(...)):
    folder_id = f"folder_{int(time.time()*1000)}"
    folder_dir = Path("data/uploads") / folder_id
    folder_dir.mkdir(parents=True, exist_ok=True)
    saved_files = []
    for f in files:
        if not f.filename:
            continue
        dest = folder_dir / Path(f.filename).name
        content = await f.read()
        dest.write_bytes(content)
        saved_files.append(str(dest.resolve()))
    return {
        "success": True,
        "folder_path": str(folder_dir.resolve()),
        "total_files": len(saved_files),
        "files": saved_files
    }

class CheckLocalPathRequest(BaseModel):
    path: str

@app.post("/api/check-local-path")
def check_local_path(req: CheckLocalPathRequest):
    p = Path(req.path.strip().strip('"').strip("'"))
    if not p.exists():
        return {"exists": False, "error": "Đường dẫn không tồn tại trên máy tính"}
    if p.is_dir():
        imgs = [f.name for f in p.iterdir() if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp", ".gif"]]
        return {"exists": True, "is_dir": True, "image_count": len(imgs), "sample_images": imgs[:5], "path": str(p.resolve())}
    else:
        return {"exists": True, "is_dir": False, "is_image": p.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp", ".gif"], "path": str(p.resolve())}

# =========================================================
# COMMENT PRESETS APIS
# =========================================================
COMMENT_PRESETS_FILE = Path("data") / "comment_presets.json"

def load_comment_presets() -> List[Dict[str, Any]]:
    if COMMENT_PRESETS_FILE.exists():
        try:
            return json.loads(COMMENT_PRESETS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    default_presets = [
        {
            "id": "preset_sales_default",
            "name": "Mẫu Bán Hàng Cơ Bản",
            "comment_count": 2,
            "comment_text": "Ib em nha 🌸 | Quan tâm ib shop tư vấn liền nhé! 📦 | Check tin nhắn giúp shop ạ ✨",
            "image_mode": "single",
            "image_path": "",
            "attach_text": True
        },
        {
            "id": "preset_feedback_proof",
            "name": "Mẫu Feedback & Đính Kèm Ảnh",
            "comment_count": 3,
            "comment_text": "Khách nhận hàng khen nức nở ạ 🥰 | Feedback xịn sò từ khách thân yêu 💖 | Hàng luôn sẵn ship ngay!",
            "image_mode": "folder_separate_images",
            "image_path": "",
            "attach_text": True
        }
    ]
    COMMENT_PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    COMMENT_PRESETS_FILE.write_text(json.dumps(default_presets, ensure_ascii=False, indent=2), encoding="utf-8")
    return default_presets

def save_comment_presets(presets: List[Dict[str, Any]]):
    COMMENT_PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    COMMENT_PRESETS_FILE.write_text(json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8")

class CommentPresetRequest(BaseModel):
    id: Optional[str] = None
    name: str
    comment_count: int = 1
    comment_text: str = ""
    image_mode: str = "single"
    image_path: str = ""
    attach_text: bool = True

@app.get("/api/comment/presets")
def api_get_comment_presets():
    return {"success": True, "presets": load_comment_presets()}

@app.post("/api/comment/presets")
def api_save_comment_preset(req: CommentPresetRequest):
    presets = load_comment_presets()
    p_id = req.id or f"preset_{int(time.time()*1000)}"
    existing = next((p for p in presets if p["id"] == p_id), None)
    preset_data = {
        "id": p_id,
        "name": req.name.strip() or "Mẫu bình luận",
        "comment_count": max(1, req.comment_count),
        "comment_text": req.comment_text,
        "image_mode": req.image_mode,
        "image_path": req.image_path,
        "attach_text": req.attach_text,
        "updated_at": int(time.time())
    }
    if existing:
        existing.update(preset_data)
    else:
        presets.insert(0, preset_data)
    save_comment_presets(presets)
    return {"success": True, "preset": preset_data, "presets": presets}

@app.delete("/api/comment/presets/{preset_id}")
def api_delete_comment_preset(preset_id: str):
    presets = load_comment_presets()
    filtered = [p for p in presets if p["id"] != preset_id]
    save_comment_presets(filtered)
    return {"success": True, "presets": filtered}



# Scanner APIs (Pages & Groups)
class ScanPagesRequest(BaseModel):
    account_id: str
    headless: bool = True

@app.post("/api/scanner/pages")
async def api_scan_pages(req: ScanPagesRequest):
    try:
        acc = next((a for a in account_mgr.get_accounts() if a["id"] == req.account_id), None)
        if not acc:
            return JSONResponse(status_code=404, content={"error": "Account not found"})

        pages = await asyncio.to_thread(scan_account_pages, acc["profile_dir"], acc["name"], headless=req.headless, log_cb=broadcast_log)
        data_file = Path("data") / f"pages_{req.account_id}.json"
        if pages or not data_file.exists():
            data_file.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"success": True, "pages": pages}
    except Exception as ex:
        broadcast_log(f"Lỗi API quét Fanpage: {ex}", "error")
        return JSONResponse(status_code=500, content={"success": False, "error": str(ex), "pages": []})

@app.get("/api/scanner/pages")
def api_get_pages(account_id: str):
    data_file = Path("data") / f"pages_{account_id}.json"
    if data_file.exists():
        try:
            pages = json.loads(data_file.read_text(encoding="utf-8"))
            clean_pages = []
            for p in pages:
                u = (p.get("url") or "").lower()
                n = (p.get("name") or "")
                if "/groups/" in u or "/posts/" in u or "/permalink/" in u or n.startswith("Chưa đọc") or "bài viết mới" in n:
                    continue
                clean_pages.append(p)
            return {"pages": clean_pages}
        except Exception: pass
    return {"pages": []}

class UpdatePageMatrixRequest(BaseModel):
    account_id: str
    page_url: str
    updates: Dict[str, Any]

class BatchUpdatePageMatrixRequest(BaseModel):
    account_id: str
    page_urls: List[str]
    updates: Dict[str, Any]

class GeneratePageContentRequest(BaseModel):
    account_id: str
    page_url: str
    topic: Optional[str] = None
    count: int = 1
    save_to_category: bool = True

class BatchGenerateContentRequest(BaseModel):
    account_id: str
    page_urls: List[str]
    topic: Optional[str] = None
    count_per_page: int = 1
    save_to_category: bool = True

@app.get("/api/matrix/pages")
def api_get_page_matrix(account_id: str):
    return page_matrix_mgr.load_matrix(account_id)

@app.post("/api/matrix/pages/update")
def api_update_page_matrix(req: UpdatePageMatrixRequest):
    ok = page_matrix_mgr.update_page_meta(req.account_id, req.page_url, req.updates)
    return {"success": ok}

@app.post("/api/matrix/pages/batch-update")
def api_batch_update_page_matrix(req: BatchUpdatePageMatrixRequest):
    count = page_matrix_mgr.batch_update_pages(req.account_id, req.page_urls, req.updates)
    return {"success": True, "updated_count": count}

@app.post("/api/matrix/pages/generate-content")
def api_generate_page_content(req: GeneratePageContentRequest):
    posts = page_matrix_mgr.generate_page_content(
        account_id=req.account_id,
        page_url=req.page_url,
        topic=req.topic,
        count=req.count,
        save_to_category=req.save_to_category
    )
    return {"success": True, "posts": posts}

@app.post("/api/matrix/pages/batch-generate")
def api_batch_generate_content(req: BatchGenerateContentRequest):
    all_posts = []
    for p_url in req.page_urls:
        posts = page_matrix_mgr.generate_page_content(
            account_id=req.account_id,
            page_url=p_url,
            topic=req.topic,
            count=req.count_per_page,
            save_to_category=req.save_to_category
        )
        all_posts.extend(posts)
    return {"success": True, "total_generated": len(all_posts), "posts": all_posts}

# =========================================================
# MULTI-PLATFORM EXPANSION APIS (INSTAGRAM, THREADS, ZALO)
# =========================================================
@app.get("/api/platforms/status")
def api_get_platforms_status():
    return {
        "instagram": instagram_mgr.get_status(),
        "threads": threads_mgr.get_status(),
        "zalo": zalo_mgr.get_status()
    }

# ----------------- INSTAGRAM -----------------
class AddInstagramAccountRequest(BaseModel):
    username: str
    name: Optional[str] = None
    proxy: Optional[str] = ""
    cookies: Optional[str] = ""
    notes: Optional[str] = ""

class UpdateInstagramAccountRequest(BaseModel):
    id: str
    name: Optional[str] = None
    proxy: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None

class PublishInstagramFeedRequest(BaseModel):
    account_id: str
    media_paths: List[str] = []
    media_type: str = "image"
    caption: str = ""
    music: Optional[str] = ""
    hide_likes_and_views: bool = False
    disable_comments: bool = False
    auto_share_threads: bool = False
    auto_share_facebook: bool = False

class PublishInstagramReelRequest(BaseModel):
    account_id: str
    video_path: str
    caption: str = "#reels #instagram #viral"
    music: Optional[str] = ""
    mutate_hash: bool = True
    auto_share_threads: bool = False
    auto_share_facebook: bool = False

class ScheduleInstagramPostRequest(BaseModel):
    account_id: str
    type: str = "feed"
    media_paths: List[str] = []
    caption: str = ""
    music: Optional[str] = ""
    hide_likes_and_views: bool = False
    disable_comments: bool = False
    auto_share_threads: bool = False
    auto_share_facebook: bool = False
    scheduled_time: str

class StartInstagramWarmupRequest(BaseModel):
    account_id: str
    scroll_minutes: int = 10
    like_count: int = 5
    reels_watch_count: int = 8
    min_delay: int = 3
    max_delay: int = 7

class StartInstagramSeedingRequest(BaseModel):
    target: str
    comments: List[str]
    account_ids: List[str] = []
    delay: int = 15

@app.get("/api/instagram/accounts")
def api_get_instagram_accounts():
    return {"accounts": instagram_mgr.get_accounts()}

@app.post("/api/instagram/accounts/add")
def api_add_instagram_account(req: AddInstagramAccountRequest):
    if not req.username:
        return JSONResponse(status_code=400, content={"error": "Vui lòng nhập tên tài khoản Instagram"})
    acc = instagram_mgr.add_account(req.username, req.name, req.proxy or "", req.cookies or "", req.notes or "")
    return {"success": True, "account": acc}

@app.post("/api/instagram/accounts/update")
def api_update_instagram_account(req: UpdateInstagramAccountRequest):
    ok = instagram_mgr.update_account(req.id, req.dict(exclude_unset=True))
    return {"success": ok}

@app.post("/api/instagram/accounts/delete")
def api_delete_instagram_account(payload: Dict[str, Any]):
    acc_id = payload.get("id") or payload.get("username")
    ok = instagram_mgr.delete_account(acc_id)
    return {"success": ok}

# --- DEDICATED PROFILES, COOKIE CHECK & BATCH ENGINE FOR INSTAGRAM & THREADS ---
class OpenMetaBrowserRequest(BaseModel):
    account_id: str
    platform: Optional[str] = "instagram"

class CheckMetaCookieRequest(BaseModel):
    account_id: str

class BatchMetaAccountsRequest(BaseModel):
    account_ids: List[str]
    action: str  # "check", "delete", "assign_proxy"
    proxy: Optional[str] = ""

class PublishBatchInstagramRequest(BaseModel):
    account_ids: List[str]
    media_paths: List[str]
    media_type: Optional[str] = "image"
    caption: Optional[str] = ""
    music: Optional[str] = ""
    distribution_mode: Optional[str] = "round_robin"
    delay_min: Optional[int] = 15
    delay_max: Optional[int] = 45
    mutate_hash: Optional[bool] = True
    spintax_independent: Optional[bool] = True
    hide_likes_and_views: Optional[bool] = False
    disable_comments: Optional[bool] = False
    auto_share_threads: Optional[bool] = False
    auto_share_facebook: Optional[bool] = False

class PublishBatchThreadsRequest(BaseModel):
    profile_ids: List[str]
    content: Optional[str] = ""
    media_paths: Optional[List[str]] = []
    cta_link: Optional[str] = ""
    cta_first_reply: Optional[str] = ""
    hide_like_count: Optional[bool] = False
    reply_control: Optional[str] = "anyone"
    auto_share_instagram: Optional[bool] = False
    distribution_mode: Optional[str] = "round_robin"
    delay_min: Optional[int] = 15
    delay_max: Optional[int] = 45
    spintax_independent: Optional[bool] = True

@app.post("/api/instagram/accounts/open-browser")
def api_open_instagram_browser(req: OpenMetaBrowserRequest):
    return instagram_mgr.open_browser(req.account_id, platform=req.platform or "instagram")

@app.post("/api/instagram/accounts/check-cookie")
def api_check_instagram_cookie(req: CheckMetaCookieRequest):
    return instagram_mgr.check_cookie(req.account_id)

@app.post("/api/instagram/accounts/batch-action")
def api_batch_instagram_accounts(req: BatchMetaAccountsRequest):
    if req.action == "check":
        return instagram_mgr.batch_check_cookies(req.account_ids)
    elif req.action == "assign_proxy":
        return instagram_mgr.batch_assign_proxy(req.account_ids, req.proxy or "")
    elif req.action == "delete":
        return instagram_mgr.batch_delete_accounts(req.account_ids)
    return {"success": False, "error": f"Hành động '{req.action}' không hợp lệ"}

@app.post("/api/instagram/publish-batch")
def api_publish_batch_instagram(req: PublishBatchInstagramRequest):
    return instagram_mgr.publish_batch_feed(
        account_ids=req.account_ids,
        media_paths=req.media_paths,
        media_type=req.media_type or "image",
        caption=req.caption or "",
        music=req.music or "",
        distribution_mode=req.distribution_mode or "round_robin",
        delay_min=req.delay_min or 15,
        delay_max=req.delay_max or 45,
        mutate_hash=req.mutate_hash,
        spintax_independent=req.spintax_independent,
        hide_likes_and_views=req.hide_likes_and_views,
        disable_comments=req.disable_comments,
        auto_share_threads=req.auto_share_threads,
        auto_share_facebook=req.auto_share_facebook
    )

@app.post("/api/threads/profiles/open-browser")
def api_open_threads_browser(req: OpenMetaBrowserRequest):
    return threads_mgr.open_browser(req.account_id)

@app.post("/api/threads/profiles/check-cookie")
def api_check_threads_cookie(req: CheckMetaCookieRequest):
    return threads_mgr.check_cookie(req.account_id)

@app.post("/api/threads/profiles/batch-action")
def api_batch_threads_profiles(req: BatchMetaAccountsRequest):
    if req.action == "check":
        return threads_mgr.batch_check_cookies(req.account_ids)
    elif req.action == "assign_proxy":
        return threads_mgr.batch_assign_proxy(req.account_ids, req.proxy or "")
    elif req.action == "delete":
        return threads_mgr.batch_delete_profiles(req.account_ids)
    return {"success": False, "error": f"Hành động '{req.action}' không hợp lệ"}

@app.post("/api/threads/publish-batch")
def api_publish_batch_threads(req: PublishBatchThreadsRequest):
    return threads_mgr.publish_batch_threads(
        profile_ids=req.profile_ids,
        content=req.content or "",
        media_paths=req.media_paths or [],
        cta_link=req.cta_link or "",
        cta_first_reply=req.cta_first_reply or "",
        hide_like_count=req.hide_like_count,
        reply_control=req.reply_control or "anyone",
        auto_share_instagram=req.auto_share_instagram,
        distribution_mode=req.distribution_mode or "round_robin",
        delay_min=req.delay_min or 15,
        delay_max=req.delay_max or 45,
        spintax_independent=req.spintax_independent
    )


@app.post("/api/instagram/publish-feed")
def api_publish_instagram_feed(req: PublishInstagramFeedRequest):
    def cb(msg, lvl="info"): broadcast_log(f"📸 [Instagram Feed] {msg}", lvl, req.account_id)
    res = instagram_mgr.publish_feed(
        account_id=req.account_id,
        media_paths=req.media_paths,
        media_type=req.media_type,
        caption=req.caption,
        music=req.music or "",
        hide_likes_and_views=req.hide_likes_and_views,
        disable_comments=req.disable_comments,
        auto_share_threads=req.auto_share_threads,
        auto_share_facebook=req.auto_share_facebook,
        log_callback=cb
    )
    return res

@app.post("/api/instagram/publish-reel")
def api_publish_instagram_reel(req: PublishInstagramReelRequest):
    def cb(msg, lvl="info"): broadcast_log(f"📸 [Instagram Reels] {msg}", lvl, req.account_id)
    res = instagram_mgr.publish_reel(
        account_id=req.account_id,
        video_path=req.video_path,
        caption=req.caption,
        music=req.music or "",
        mutate_hash=req.mutate_hash,
        auto_share_threads=req.auto_share_threads,
        auto_share_facebook=req.auto_share_facebook,
        log_callback=cb
    )
    return res

@app.get("/api/instagram/schedules")
def api_get_instagram_schedules():
    return {"schedules": instagram_mgr.get_schedules()}

@app.post("/api/instagram/schedule")
def api_schedule_instagram_post(req: ScheduleInstagramPostRequest):
    item = instagram_mgr.schedule_post(req.dict())
    return {"success": True, "schedule": item}

@app.post("/api/instagram/schedule/cancel")
def api_cancel_instagram_schedule(payload: Dict[str, Any]):
    ok = instagram_mgr.cancel_schedule(payload.get("id", ""))
    return {"success": ok}

@app.post("/api/instagram/warmup/start")
def api_start_instagram_warmup(req: StartInstagramWarmupRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🚀 [Instagram Warmup] {msg}", lvl, req.account_id)
    ok = instagram_mgr.start_warmup(req.account_id, req.dict(), cb)
    return {"success": ok, "message": "Đã bắt đầu phiên nuôi nick Instagram!" if ok else "Đang có tiến trình nuôi nick chạy!"}

@app.post("/api/instagram/warmup/stop")
def api_stop_instagram_warmup():
    ok = instagram_mgr.stop_warmup()
    return {"success": ok, "message": "Đã dừng phiên nuôi nick Instagram."}

@app.get("/api/instagram/warmup/status")
def api_get_instagram_warmup_status():
    return instagram_mgr.get_warmup_status()

@app.post("/api/instagram/seeding/start")
def api_start_instagram_seeding(req: StartInstagramSeedingRequest):
    def cb(msg, lvl="info"): broadcast_log(f"💬 [Instagram Seeding] {msg}", lvl)
    ok = instagram_mgr.start_seeding(req.target, req.comments, req.account_ids, req.delay, cb)
    return {"success": ok, "message": "Đã bắt đầu chiến dịch seeding bình luận Instagram!" if ok else "Đang có tiến trình seeding chạy!"}

@app.post("/api/instagram/seeding/stop")
def api_stop_instagram_seeding():
    ok = instagram_mgr.stop_seeding()
    return {"success": ok, "message": "Đã dừng tiến trình seeding Instagram."}

@app.get("/api/instagram/history")
def api_get_instagram_history():
    return {"history": instagram_mgr.get_history()}


# ----------------- THREADS -----------------
class AddThreadsProfileRequest(BaseModel):
    username: str
    name: Optional[str] = None
    bio: Optional[str] = ""
    proxy: Optional[str] = ""

class UpdateThreadsProfileRequest(BaseModel):
    id: str
    name: Optional[str] = None
    bio: Optional[str] = None
    proxy: Optional[str] = None
    status: Optional[str] = None

class CreateThreadPostRequest(BaseModel):
    profile_id: str
    content: str
    media_paths: Optional[List[str]] = []
    hide_like_count: bool = False
    reply_control: str = "anyone"
    auto_share_instagram: bool = False
    cta_link: Optional[str] = ""
    cta_first_reply: Optional[str] = ""

class ScheduleThreadPostRequest(BaseModel):
    profile_id: str
    content: str
    media_paths: Optional[List[str]] = []
    hide_like_count: bool = False
    reply_control: str = "anyone"
    auto_share_instagram: bool = False
    cta_link: Optional[str] = ""
    cta_first_reply: Optional[str] = ""
    scheduled_time: str

class StartThreadsWarmupRequest(BaseModel):
    profile_id: str
    like_count: int = 6
    repost_count: int = 2
    min_delay: int = 3
    max_delay: int = 8

class StartThreadsSeedingRequest(BaseModel):
    target: str
    comments: List[str]
    profile_ids: List[str] = []
    delay: int = 15

@app.get("/api/threads/profiles")
def api_get_threads_profiles():
    return {"profiles": threads_mgr.get_profiles()}

@app.post("/api/threads/profiles/add")
def api_add_threads_profile(req: AddThreadsProfileRequest):
    if not req.username:
        return JSONResponse(status_code=400, content={"error": "Vui lòng nhập tên tài khoản Threads"})
    prof = threads_mgr.add_profile(req.username, req.name, req.bio or "", req.proxy or "")
    return {"success": True, "profile": prof}

@app.post("/api/threads/profiles/update")
def api_update_threads_profile(req: UpdateThreadsProfileRequest):
    ok = threads_mgr.update_profile(req.id, req.dict(exclude_unset=True))
    return {"success": ok}

@app.post("/api/threads/profiles/delete")
def api_delete_threads_profile(payload: Dict[str, Any]):
    prof_id = payload.get("id") or payload.get("username")
    ok = threads_mgr.delete_profile(prof_id)
    return {"success": ok}

@app.post("/api/threads/post")
def api_post_thread(req: CreateThreadPostRequest):
    if not req.content:
        return JSONResponse(status_code=400, content={"error": "Nội dung Thread không được để trống"})
    def cb(msg, lvl="info"): broadcast_log(f"🧵 [Threads] {msg}", lvl, req.profile_id)
    res = threads_mgr.create_thread(
        profile_id=req.profile_id,
        content=req.content,
        media_paths=req.media_paths or [],
        hide_like_count=req.hide_like_count,
        reply_control=req.reply_control or "anyone",
        auto_share_instagram=req.auto_share_instagram,
        cta_link=req.cta_link or "",
        cta_first_reply=req.cta_first_reply or "",
        log_callback=cb
    )
    return res

@app.get("/api/threads/schedules")
def api_get_threads_schedules():
    return {"schedules": threads_mgr.get_schedules()}

@app.post("/api/threads/schedule")
def api_schedule_thread(req: ScheduleThreadPostRequest):
    item = threads_mgr.schedule_thread(req.dict())
    return {"success": True, "schedule": item}

@app.post("/api/threads/schedule/cancel")
def api_cancel_threads_schedule(payload: Dict[str, Any]):
    ok = threads_mgr.cancel_schedule(payload.get("id", ""))
    return {"success": ok}

@app.post("/api/threads/warmup/start")
def api_start_threads_warmup(req: StartThreadsWarmupRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🚀 [Threads Warmup] {msg}", lvl, req.profile_id)
    ok = threads_mgr.start_warmup(req.profile_id, req.dict(), cb)
    return {"success": ok, "message": "Đã bắt đầu nuôi nick Threads!" if ok else "Đang có tiến trình nuôi nick chạy!"}

@app.post("/api/threads/warmup/stop")
def api_stop_threads_warmup():
    ok = threads_mgr.stop_warmup()
    return {"success": ok, "message": "Đã dừng nuôi nick Threads."}

@app.get("/api/threads/warmup/status")
def api_get_threads_warmup_status():
    return threads_mgr.get_warmup_status()

@app.post("/api/threads/seeding/start")
def api_start_threads_seeding(req: StartThreadsSeedingRequest):
    def cb(msg, lvl="info"): broadcast_log(f"💬 [Threads Seeding] {msg}", lvl)
    ok = threads_mgr.start_seeding(req.target, req.comments, req.profile_ids, req.delay, cb)
    return {"success": ok, "message": "Đã bắt đầu seeding bình luận Threads!" if ok else "Đang có tiến trình seeding chạy!"}

@app.post("/api/threads/seeding/stop")
def api_stop_threads_seeding():
    ok = threads_mgr.stop_seeding()
    return {"success": ok, "message": "Đã dừng tiến trình seeding Threads."}

@app.get("/api/threads/history")
def api_get_threads_history():
    return {"history": threads_mgr.get_history()}

# ----------------- ZALO CRM -----------------
class ImportZaloLeadsRequest(BaseModel):
    phones: List[str]
    tag: Optional[str] = "Facebook Lead"

class AddZaloCustomerRequest(BaseModel):
    phone: str
    name: Optional[str] = ""
    tag: Optional[str] = "Facebook Lead"
    notes: Optional[str] = ""

class UpdateZaloStatusRequest(BaseModel):
    phone: str
    status: str
    notes: Optional[str] = None

class SaveZaloTemplateRequest(BaseModel):
    id: Optional[str] = None
    title: str
    content: str

@app.get("/api/zalo/customers")
def api_get_zalo_customers():
    return {"customers": zalo_mgr.get_customers()}

@app.post("/api/zalo/customers/add")
def api_add_zalo_customer(req: AddZaloCustomerRequest):
    if not req.phone:
        return JSONResponse(status_code=400, content={"error": "Vui lòng nhập số điện thoại"})
    c = zalo_mgr.add_customer(req.phone, req.name or "", req.tag or "Facebook Lead", req.notes or "")
    return {"success": True, "customer": c}

@app.post("/api/zalo/customers/import")
def api_import_zalo_customers(req: ImportZaloLeadsRequest):
    count = zalo_mgr.import_phones(req.phones, tag=req.tag or "Facebook Lead")
    return {"success": True, "imported_count": count}

@app.post("/api/zalo/customers/update-status")
def api_update_zalo_status(req: UpdateZaloStatusRequest):
    ok = zalo_mgr.update_status(req.phone, req.status, req.notes)
    return {"success": ok}

@app.post("/api/zalo/customers/delete")
def api_delete_zalo_customer(payload: Dict[str, Any]):
    phone = payload.get("phone", "")
    ok = zalo_mgr.delete_customer(phone)
    return {"success": ok}

@app.get("/api/zalo/templates")
def api_get_zalo_templates():
    return {"templates": zalo_mgr.get_templates()}

@app.post("/api/zalo/templates/save")
def api_save_zalo_template(req: SaveZaloTemplateRequest):
    tpl = zalo_mgr.save_template(req.title, req.content, req.id)
    return {"success": True, "template": tpl}

@app.post("/api/zalo/templates/delete")
def api_delete_zalo_template(payload: Dict[str, Any]):
    ok = zalo_mgr.delete_template(payload.get("id", ""))
    return {"success": ok}

@app.get("/api/zalo/customers/export-excel")
def api_export_zalo_excel():
    excel_path = Path("data/zalo_danh_ba_khach_hang.xlsx")
    try:
        zalo_mgr.export_to_excel(str(excel_path.resolve()))
        return FileResponse(
            path=str(excel_path),
            filename="zalo_danh_ba_khach_hang.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Lỗi xuất Excel: {e}"})

@app.post("/api/zalo/sync-from-shield")
def api_sync_zalo_from_shield():
    shield_history = comment_shield_engine.history
    phones = [h.get("phone") for h in shield_history if h.get("phone")]
    added = zalo_mgr.import_phones(phones, tag="Lá Chắn SĐT Fanpage", notes="Khách để lại SĐT dưới bài viết Fanpage")
    broadcast_log(f"💬 [Zalo CRM] Đã đồng bộ {added} khách hàng mới từ Lá Chắn Facebook sang Zalo CRM!", "success")
    return {"success": True, "synced_count": added, "total_leads": len(zalo_mgr.get_customers())}


# =========================================================
# CHIẾN DỊCH SEEDING CHÉO & KÉO KHÁCH (DÀN TRANG PHỤ -> TRANG CHÍNH)
# =========================================================
class StartCrossPageRequest(BaseModel):
    account_id: str
    target_post_url: str
    spoke_pages: List[Dict[str, Any]] = []
    do_reaction: bool = True
    do_comment: bool = True
    comment_custom: Optional[str] = None
    do_share: bool = True
    share_caption: Optional[str] = None
    min_delay: int = 15
    max_delay: int = 35
    headless: bool = False
    simulate: bool = False

@app.post("/api/cross-page/start")
def api_start_cross_page(req: StartCrossPageRequest):
    acc = next((a for a in account_mgr.get_accounts() if a["id"] == req.account_id), None)
    if not acc and req.simulate:
        acc = {"id": req.account_id, "name": f"Tài Khoản Mô Phỏng ({req.account_id})"}
    elif not acc:
        return JSONResponse(status_code=404, content={"error": "Tài khoản không tồn tại"})
    if not req.target_post_url:
        return JSONResponse(status_code=400, content={"error": "Vui lòng nhập link bài viết Trang Chính cần kéo tương tác"})
    if not req.spoke_pages:
        return JSONResponse(status_code=400, content={"error": "Vui lòng chọn ít nhất 1 Trang Phụ tham gia chiến dịch"})

    ok = cross_page_engine.start_seeding(acc, req.dict())
    return {"success": ok, "message": f"Đã bắt đầu Chiến Dịch Seeding Chéo với {len(req.spoke_pages)} Trang Phụ!"}

@app.post("/api/cross-page/stop")
def api_stop_cross_page():
    cross_page_engine.stop_seeding()
    return {"success": True, "message": "Đã gửi lệnh dừng Chiến Dịch Seeding Chéo."}

@app.get("/api/cross-page/status")
def api_cross_page_status():
    return cross_page_engine.get_status()

class MutateVideoRequest(BaseModel):
    video_path: str
    pages: List[Dict[str, Any]] = []

@app.post("/api/reels/mutate-video")
def api_mutate_video(req: MutateVideoRequest):
    if not req.video_path or not os.path.exists(req.video_path):
        return JSONResponse(status_code=400, content={"error": "File video không tồn tại"})
    results = video_mutator.batch_mutate_for_pages(req.video_path, req.pages)
    return {"success": True, "results": results}

class ScanGroupsRequest(BaseModel):
    tab_id: Optional[str] = None
    account_id: str
    role_type: str = "personal"
    role_url: Optional[str] = None
    role_name: Optional[str] = None
    headless: bool = True

@app.post("/api/scanner/groups")
async def api_scan_groups(req: ScanGroupsRequest):
    try:
        acc = next((a for a in account_mgr.get_accounts() if a["id"] == req.account_id), None)
        if not acc:
            return JSONResponse(status_code=404, content={"success": False, "error": "Account not found", "groups": []})

        log_cb = lambda l, lvl="info": broadcast_log(l, lvl, account_id=req.account_id, channel="scan", tab_id=req.tab_id)
        scan_stream_cb = lambda g: broadcast_group_scan_stream(g, account_id=req.account_id, tab_id=req.tab_id)
        groups = await asyncio.to_thread(
            scan_account_groups,
            profile_dir=acc["profile_dir"],
            acc_name=acc["name"],
            role_type=req.role_type,
            role_url=req.role_url,
            role_name=req.role_name,
            personal_name=acc.get("fb_name"),
            headless=req.headless,
            proxy=parse_proxy(acc.get("proxy")),
            log_cb=log_cb,
            on_group_found=scan_stream_cb
        )
        role_key = req.role_type if req.role_type == "personal" else f"page_{re.sub(r'[^a-zA-Z0-9_]', '_', req.role_url or req.role_name or 'page')}"
        data_file = Path("data") / f"groups_{req.account_id}_{role_key}.json"

        # Tự động làm giàu dữ liệu siêu tốc bằng FastGroupScraper (áp dụng cho cả Page & Profile cá nhân)
        if groups:
            broadcast_log(f"⚡ [Siêu tốc] Đang tự động quét thông tin (thành viên, kiểm duyệt, quyền) cho {len(groups)} nhóm...", "info")
            try:
                group_ids = [str(g.get("id") or g.get("url", "").split("/groups/")[-1].strip("/")) for g in groups]
                group_ids = [gid for gid in group_ids if gid]
                cookies_str = await asyncio.to_thread(get_account_cookies_string, acc.get("profile_dir", ""))
                excel_file = Path("data") / f"groups_{req.account_id}_{role_key}.xlsx"
                db_file = "data/groups_cache.db"

                results = await scrape_groups_batch(
                    group_ids=group_ids,
                    cookies=cookies_str,
                    concurrency=15,
                    db_path=db_file,
                    excel_path=str(excel_file)
                )
                res_map = {r.group_id: r for r in results}
                for g in groups:
                    gid = str(g.get("id") or g.get("url", "").split("/groups/")[-1].strip("/"))
                    if gid in res_map:
                        r = res_map[gid]
                        if r.name and r.name != gid:
                            g["name"] = r.name
                        g["member_count"] = r.member_count
                        g["members_str"] = r.members_str or (f"{r.member_count:,}".replace(",", ".") if r.member_count else "--")
                        g["posts_today"] = r.posts_today
                        g["posts_month"] = r.posts_month
                        g["posts_today_str"] = r.posts_today_str or (f"{r.posts_today} bài" if r.posts_today is not None else "--")
                        g["engagement_score"] = r.engagement_score
                        g["engagement_rate"] = r.engagement_rate or (f"{r.engagement_score}%" if r.engagement_score else "--")
                        g["new_members_week"] = r.new_members_week
                        g["language"] = r.language
                        g["language_str"] = r.language_str
                        g["privacy"] = r.privacy.value if hasattr(r.privacy, "value") else str(r.privacy)
                        g["is_moderated"] = r.is_moderated
                        g["moderation"] = "Duyệt bài" if r.is_moderated else "Tự do đăng"
                        g["has_questions"] = r.has_questions
                        if r.cover_url:
                            g["cover_url"] = r.cover_url
                        g["fast_scraped"] = True
                broadcast_log(f"⚡ [Siêu tốc] Hoàn tất làm giàu dữ liệu và tạo file Excel cho {len(groups)} nhóm!", "success")
            except Exception as fe:
                broadcast_log(f"Lưu ý làm giàu tự động: {fe}", "warning")

        if groups or not data_file.exists():
            data_file.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")
            if req.role_type == "page":
                for sibling_file in Path("data").glob(f"groups_*_{role_key}.json"):
                    try:
                        sibling_file.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception: pass
        else:
            try:
                old_groups = json.loads(data_file.read_text(encoding="utf-8")) if data_file.exists() else []
                if not old_groups:
                    data_file.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")
                else:
                    broadcast_log("⚠️ Lưu ý: Quét được 0 nhóm mới, hệ thống giữ nguyên danh sách nhóm đã lưu trước đó để bảo toàn dữ liệu.", "warning")
            except Exception:
                data_file.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")

        return {"success": True, "groups": groups, "total": len(groups)}
    except Exception as ex:
        broadcast_log(f"Lỗi API quét nhóm: {ex}", "error")
        return JSONResponse(status_code=500, content={"success": False, "error": str(ex), "groups": []})

@app.get("/api/scanner/groups")
def api_get_groups(account_id: str, role_type: str = "personal", role_url: Optional[str] = None, role_name: Optional[str] = None):
    role_key = role_type if role_type == "personal" else f"page_{re.sub(r'[^a-zA-Z0-9_]', '_', role_url or role_name or 'page')}"
    data_file = Path("data") / f"groups_{account_id}_{role_key}.json"
    if data_file.exists():
        try:
            return {"groups": json.loads(data_file.read_text(encoding="utf-8"))}
        except Exception: pass
    
    # Nếu là Fanpage dùng chung giữa các nick, fallback sang file cache của nick khác
    if role_type == "page":
        for sibling_file in Path("data").glob(f"groups_*_{role_key}.json"):
            if sibling_file.exists():
                try:
                    return {"groups": json.loads(sibling_file.read_text(encoding="utf-8"))}
                except Exception: pass

    # Fallback legacy
    legacy_file = Path("data") / f"groups_{account_id}_{role_type}.json"
    if legacy_file.exists():
        try:
            return {"groups": json.loads(legacy_file.read_text(encoding="utf-8"))}
        except Exception: pass
    return {"groups": []}

# Deep Group Scanner APIs
class DeepScanRequest(BaseModel):
    account_id: str
    role_type: str = "personal"
    role_url: Optional[str] = None
    role_name: Optional[str] = None
    concurrency: int = 3

@app.post("/api/scanner/groups/deep-scan")
def api_start_deep_scan(req: DeepScanRequest):
    acc = next((a for a in account_mgr.get_accounts() if a["id"] == req.account_id), None)
    if not acc:
        return JSONResponse(status_code=404, content={"success": False, "error": "Account not found"})

    role_key = req.role_type if req.role_type == "personal" else f"page_{re.sub(r'[^a-zA-Z0-9_]', '_', req.role_url or req.role_name or 'page')}"
    data_file = Path("data") / f"groups_{req.account_id}_{role_key}.json"
    if not data_file.exists():
        legacy_file = Path("data") / f"groups_{req.account_id}_{req.role_type}.json"
        if legacy_file.exists():
            data_file = legacy_file
        else:
            return JSONResponse(status_code=400, content={"success": False, "error": "Chưa có danh sách nhóm để quét chi tiết. Hãy quét nhóm trước!"})

    try:
        groups = json.loads(data_file.read_text(encoding="utf-8"))
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": f"Lỗi đọc file nhóm: {e}"})

    def on_scan_done(updated_groups):
        try:
            data_file.write_text(json.dumps(updated_groups, ensure_ascii=False, indent=2), encoding="utf-8")
            broadcast_log(f"Đã lưu thông tin chi tiết của {len(updated_groups)} nhóm vào dữ liệu!", "success")
        except Exception as err:
            broadcast_log(f"Lỗi lưu file chi tiết nhóm: {err}", "error")

    group_deep_scanner.log_callback = broadcast_log
    group_deep_scanner.start_scan(
        groups=groups,
        profile_dir=acc["profile_dir"],
        acc_name=acc["name"],
        role_type=req.role_type,
        role_url=req.role_url,
        role_name=req.role_name,
        concurrency=req.concurrency,
        on_complete=on_scan_done
    )

    return {"success": True, "message": f"Bắt đầu quét sâu {len(groups)} nhóm với {req.concurrency} luồng song song!"}

@app.get("/api/scanner/groups/deep-scan/status")
def api_get_deep_scan_status():
    return {
        "stats": group_deep_scanner.stats,
        "enriched_groups": group_deep_scanner.enriched_groups
    }

@app.post("/api/scanner/groups/deep-scan/stop")
def api_stop_deep_scan():
    group_deep_scanner.stop_scan()
    return {"success": True, "message": "Đã gửi lệnh dừng quét sâu nhóm."}


# Auto Leave Groups APIs
class LeaveGroupsRequest(BaseModel):
    tab_id: Optional[str] = None
    account_id: str
    role_type: str = "personal"
    role_url: Optional[str] = None
    role_name: Optional[str] = None
    groups: List[Dict[str, Any]]
    min_delay: int = 5
    max_delay: int = 10
    headless: bool = True
    prevent_readd: bool = True

@app.post("/api/scanner/groups/leave/start")
async def api_start_leave_groups(req: LeaveGroupsRequest):
    acc = next((a for a in account_mgr.get_accounts() if a["id"] == req.account_id), None)
    if not acc:
        return JSONResponse(status_code=404, content={"success": False, "error": "Tài khoản không tồn tại!"})

    if not req.groups:
        return JSONResponse(status_code=400, content={"success": False, "error": "Vui lòng chọn ít nhất 1 nhóm để rời!"})

    group_leave_engine.log_callback = lambda l, lvl="info": broadcast_log(l, lvl, account_id=req.account_id, channel="leave", tab_id=req.tab_id)
    success = group_leave_engine.start_leave(
        account_id=req.account_id,
        profile_dir=acc["profile_dir"],
        acc_name=acc["name"],
        groups=req.groups,
        role_type=req.role_type,
        role_url=req.role_url,
        role_name=req.role_name,
        personal_name=acc.get("fb_name"),
        min_delay=req.min_delay,
        max_delay=req.max_delay,
        headless=req.headless,
        prevent_readd=req.prevent_readd
    )
    if not success:
        return JSONResponse(status_code=400, content={"success": False, "error": "Tiến trình rời nhóm đang bận hoặc không thể khởi động."})
    return {"success": True, "message": f"Đã bắt đầu rời {len(req.groups)} nhóm.", "total": len(req.groups)}

@app.post("/api/scanner/groups/leave/pause")
def api_pause_leave_groups():
    success = group_leave_engine.pause()
    return {"success": success}

@app.post("/api/scanner/groups/leave/resume")
def api_resume_leave_groups():
    success = group_leave_engine.resume()
    return {"success": success}

@app.post("/api/scanner/groups/leave/stop")
def api_stop_leave_groups():
    success = group_leave_engine.stop()
    return {"success": success}

@app.get("/api/scanner/groups/leave/status")
def api_get_leave_groups_status():
    return {"success": True, "stats": group_leave_engine.stats}


# =====================================================================
# GROUP DISCOVERY (SEARCH) & AUTO JOIN APIS
# =====================================================================
class GroupSearchRequest(BaseModel):
    tab_id: Optional[str] = None
    account_id: str
    keywords: List[str]
    limit_per_keyword: int = 25
    privacy_filter: str = "ALL"  # ALL, PUBLIC, PRIVATE
    role_type: str = "personal"
    role_url: Optional[str] = None
    role_name: Optional[str] = None
    headless: bool = True
    enrich_fast: bool = True

class GroupJoinRequest(BaseModel):
    tab_id: Optional[str] = None
    account_id: str
    groups: List[Dict[str, Any]]
    role_type: str = "personal"
    role_url: Optional[str] = None
    role_name: Optional[str] = None
    min_delay: int = 15
    max_delay: int = 30
    pre_interaction: bool = True
    auto_answer: bool = True
    default_answer: str = "Dạ em xin vào nhóm giao lưu học hỏi, cam kết không spam ạ!"
    agree_rules: bool = True
    max_count: int = 10
    headless: bool = True

@app.post("/api/groups/discover/search")
async def api_search_groups(req: GroupSearchRequest):
    group_search_join_engine.log_callback = lambda l, lvl="info": broadcast_log(l, lvl, account_id=req.account_id, channel="join", tab_id=req.tab_id)
    acc = next((a for a in account_mgr.get_accounts() if a["id"] == req.account_id), None)
    if not acc:
        return JSONResponse(status_code=404, content={"success": False, "error": "Tài khoản không tồn tại!"})

    try:
        discover_stream_cb = lambda g: broadcast_group_discover_stream(g, account_id=req.account_id, tab_id=req.tab_id)
        results = await asyncio.to_thread(
            group_search_join_engine.search_groups,
            profile_dir=acc.get("profile_dir", ""),
            acc_name=acc.get("name", "Account"),
            account_id=req.account_id,
            keywords=req.keywords,
            limit_per_keyword=req.limit_per_keyword,
            privacy_filter=req.privacy_filter,
            role_type=req.role_type,
            role_url=req.role_url,
            role_name=req.role_name,
            headless=req.headless,
            enrich_fast=req.enrich_fast,
            on_group_found=discover_stream_cb
        )
        return {"success": True, "groups": results, "total": len(results)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/groups/discover/results")
def api_get_search_results():
    return {"success": True, "groups": group_search_join_engine.last_search_results}

@app.post("/api/groups/join/start")
def api_start_join_groups(req: GroupJoinRequest):
    group_search_join_engine.log_callback = lambda l, lvl="info": broadcast_log(l, lvl, account_id=req.account_id, channel="join", tab_id=req.tab_id)
    acc = next((a for a in account_mgr.get_accounts() if a["id"] == req.account_id), None)
    if not acc:
        return JSONResponse(status_code=404, content={"success": False, "error": "Tài khoản không tồn tại!"})

    success = group_search_join_engine.start_join(
        account_id=req.account_id,
        profile_dir=acc.get("profile_dir", ""),
        acc_name=acc.get("name", "Account"),
        groups=req.groups,
        role_type=req.role_type,
        role_url=req.role_url,
        role_name=req.role_name,
        min_delay=req.min_delay,
        max_delay=req.max_delay,
        pre_interaction=req.pre_interaction,
        auto_answer=req.auto_answer,
        default_answer=req.default_answer,
        agree_rules=req.agree_rules,
        max_count=req.max_count,
        headless=req.headless
    )
    if not success:
        return JSONResponse(status_code=400, content={"success": False, "error": "Không thể bắt đầu tiến trình tham gia nhóm hoặc đang có tiến trình bận."})
    return {"success": True, "message": f"Đã bắt đầu tiến trình tham gia {min(len(req.groups), req.max_count)} nhóm."}

@app.post("/api/groups/join/pause")
def api_pause_join_groups():
    group_search_join_engine.pause_join()
    return {"success": True}

@app.post("/api/groups/join/resume")
def api_resume_join_groups():
    group_search_join_engine.resume_join()
    return {"success": True}

@app.post("/api/groups/join/stop")
def api_stop_join_groups():
    group_search_join_engine.stop_join()
    return {"success": True}

@app.get("/api/groups/join/status")
def api_get_join_groups_status():
    return {"success": True, "stats": group_search_join_engine.join_stats}


# Fast Group Scanner APIs (No-Browser / Headless-Free 50-200 groups/sec)
class FastScanRequest(BaseModel):
    account_id: str
    role_type: str = "personal"
    role_url: Optional[str] = None
    role_name: Optional[str] = None
    concurrency: int = 15

@app.post("/api/scanner/groups/fast-scan")
async def api_fast_scan_groups(req: FastScanRequest):
    acc = next((a for a in account_mgr.get_accounts() if a["id"] == req.account_id), None)
    if not acc:
        return JSONResponse(status_code=404, content={"success": False, "error": "Account not found"})

    role_key = req.role_type if req.role_type == "personal" else f"page_{re.sub(r'[^a-zA-Z0-9_]', '_', req.role_url or req.role_name or 'page')}"
    data_file = Path("data") / f"groups_{req.account_id}_{role_key}.json"
    if not data_file.exists():
        legacy_file = Path("data") / f"groups_{req.account_id}_{req.role_type}.json"
        if legacy_file.exists():
            data_file = legacy_file
        else:
            return JSONResponse(status_code=400, content={"success": False, "error": "Chưa có danh sách nhóm để quét. Hãy bấm 'Quét DS Nhóm' trước!"})

    try:
        groups = json.loads(data_file.read_text(encoding="utf-8"))
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": f"Lỗi đọc file nhóm: {e}"})

    if not groups:
        return JSONResponse(status_code=400, content={"success": False, "error": "Danh sách nhóm trống."})

    group_ids = [str(g.get("id") or g.get("url", "").split("/groups/")[-1].strip("/")) for g in groups]
    group_ids = [gid for gid in group_ids if gid]

    broadcast_log(f"⚡ [Siêu tốc No-Browser] Bắt đầu quét {len(group_ids)} nhóm (Tốc độ 50-200 nhóm/giây)...", "info")

    # Lấy cookies tài khoản
    cookies_str = await asyncio.to_thread(get_account_cookies_string, acc.get("profile_dir", ""))

    excel_file = Path("data") / f"groups_{req.account_id}_{role_key}.xlsx"
    db_file = "data/groups_cache.db"

    t0 = asyncio.get_event_loop().time()
    try:
        results = await scrape_groups_batch(
            group_ids=group_ids,
            cookies=cookies_str,
            concurrency=req.concurrency,
            db_path=db_file,
            excel_path=str(excel_file)
        )
        elapsed = asyncio.get_event_loop().time() - t0
        speed = len(group_ids) / max(elapsed, 0.001)

        # Map results back to groups list
        res_map = {r.group_id: r for r in results}
        for g in groups:
            gid = str(g.get("id") or g.get("url", "").split("/groups/")[-1].strip("/"))
            if gid in res_map:
                r = res_map[gid]
                if r.name and r.name != gid:
                    g["name"] = r.name
                g["member_count"] = r.member_count
                g["members_str"] = r.members_str or (f"{r.member_count:,}".replace(",", ".") if r.member_count else "--")
                g["posts_today"] = r.posts_today
                g["posts_month"] = r.posts_month
                g["posts_today_str"] = r.posts_today_str or (f"{r.posts_today} bài" if r.posts_today is not None else "--")
                g["engagement_score"] = r.engagement_score
                g["engagement_rate"] = r.engagement_rate or (f"{r.engagement_score}%" if r.engagement_score else "--")
                g["new_members_week"] = r.new_members_week
                g["language"] = r.language
                g["language_str"] = r.language_str
                g["privacy"] = r.privacy.value if hasattr(r.privacy, "value") else str(r.privacy)
                g["is_moderated"] = r.is_moderated
                g["moderation"] = "Duyệt bài" if r.is_moderated else "Tự do đăng"
                g["has_questions"] = r.has_questions
                if r.cover_url:
                    g["cover_url"] = r.cover_url
                g["fast_scraped"] = True

        data_file.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")
        broadcast_log(f"⚡ [Siêu tốc] Hoàn tất quét {len(results)}/{len(group_ids)} nhóm trong {elapsed:.2f}s (~{speed:.1f} nhóm/giây)! File Excel đã sẵn sàng.", "success")
        return {
            "success": True,
            "total": len(groups),
            "updated": len(results),
            "elapsed_seconds": round(elapsed, 2),
            "speed": round(speed, 1),
            "groups": groups,
            "excel_path": f"/api/scanner/groups/export-excel?account_id={req.account_id}&role_type={req.role_type}&role_url={quote(req.role_url or '')}&role_name={quote(req.role_name or '')}"
        }
    except Exception as ex:
        broadcast_log(f"Lỗi quét siêu tốc: {ex}", "error")
        return JSONResponse(status_code=500, content={"success": False, "error": str(ex)})


@app.get("/api/scanner/groups/export-excel")
def api_export_groups_excel(account_id: str, role_type: str = "personal", role_url: Optional[str] = None, role_name: Optional[str] = None):
    role_key = role_type if role_type == "personal" else f"page_{re.sub(r'[^a-zA-Z0-9_]', '_', role_url or role_name or 'page')}"
    excel_file = Path("data") / f"groups_{account_id}_{role_key}.xlsx"
    data_file = Path("data") / f"groups_{account_id}_{role_key}.json"

    if not excel_file.exists():
        if data_file.exists():
            try:
                groups_data = json.loads(data_file.read_text(encoding="utf-8"))
                group_infos = []
                for g in groups_data:
                    gid = str(g.get("id") or g.get("url", "").split("/groups/")[-1].strip("/"))
                    group_infos.append(GroupInfo(
                        group_id=str(gid),
                        name=g.get("name") or "",
                        language=g.get("language", "vi"),
                        language_str=g.get("language_str", "Tiếng Việt"),
                        privacy=g.get("privacy", "UNKNOWN"),
                        member_count=int(g.get("member_count") or 0),
                        members_str=str(g.get("members_str") or ""),
                        posts_today=int(g.get("posts_today") or 0),
                        posts_month=int(g.get("posts_month") or 0),
                        posts_today_str=str(g.get("posts_today_str") or ""),
                        engagement_score=int(g.get("engagement_score") or 0),
                        engagement_rate=str(g.get("engagement_rate") or ""),
                        new_members_week=str(g.get("new_members_week") or ""),
                        is_moderated=bool(g.get("is_moderated", False)),
                        has_questions=bool(g.get("has_questions", False)),
                        cover_url=g.get("cover_url", "")
                    ))
                export_groups_to_excel(group_infos, str(excel_file))
            except Exception as e:
                return JSONResponse(status_code=500, content={"error": f"Không thể tạo file Excel: {e}"})
        else:
            return JSONResponse(status_code=404, content={"error": "Chưa có dữ liệu nhóm để xuất Excel"})

    return FileResponse(
        path=str(excel_file),
        filename=f"danh_sach_nhom_{role_key}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# Interaction Engine APIs (Warmup / Seeding)
class StartInteractionRequest(BaseModel):
    account_id: str
    config: Dict[str, Any]


# Interaction Seeding List APIs
from interaction_engine import load_seeding_list, save_seeding_list

@app.get("/api/interaction/seeding-list")
def api_get_seeding_list():
    return {"success": True, "items": load_seeding_list()}

@app.post("/api/interaction/seeding-list")
def api_save_seeding_list(payload: Dict[str, Any]):
    items = payload.get("items", [])
    ok = save_seeding_list(items)
    return {"success": ok, "message": "Đã lưu danh sách Comment Seeding thành công!"}

@app.post("/api/interaction/start")
def api_start_interaction(req: StartInteractionRequest):
    acc = next((a for a in account_mgr.get_accounts() if a["id"] == req.account_id), None)
    if not acc:
        return JSONResponse(status_code=404, content={"error": "Account not found"})

    interaction_engine.start(acc, req.config)
    return {"success": True, "message": f"Đã bắt đầu kịch bản tương tác cho {acc['name']}!"}

@app.post("/api/interaction/stop")
def api_stop_interaction():
    interaction_engine.stop()
    return {"success": True, "message": "Đã dừng kịch bản tương tác."}

@app.get("/api/interaction/status")
def api_interaction_status():
    return interaction_engine.stats


# Group Comment / Seeding APIs
class StartGroupCommentRequest(BaseModel):
    account_id: str
    role_type: str = "personal"
    role_url: Optional[str] = None
    group_urls: List[str] = []
    comments_per_group: int = 1
    comment_text: str = ""
    comment_image: str = ""
    like_post: bool = True
    min_delay: int = 15
    max_delay: int = 30
    headless: bool = False

@app.post("/api/group-comment/start")
def api_start_group_comment(req: StartGroupCommentRequest):
    acc = next((a for a in account_mgr.get_accounts() if a["id"] == req.account_id), None)
    if not acc:
        return JSONResponse(status_code=404, content={"error": "Tài khoản không tồn tại"})
    if not req.group_urls:
        return JSONResponse(status_code=400, content={"error": "Vui lòng chọn ít nhất 1 nhóm để comment"})

    group_comment_engine.start_commenting(acc, req.dict())
    return {"success": True, "message": f"Đã bắt đầu tiến trình comment nhóm cho {acc['name']}!"}

@app.post("/api/group-comment/pause")
def api_pause_group_comment():
    if group_comment_engine.state == "RUNNING":
        group_comment_engine.pause()
        return {"success": True, "message": "Đã tạm dừng comment nhóm."}
    elif group_comment_engine.state == "PAUSED":
        group_comment_engine.resume()
        return {"success": True, "message": "Đã tiếp tục comment nhóm."}
    return {"success": False, "message": "Tiến trình không ở trạng thái chạy hoặc tạm dừng."}

@app.post("/api/group-comment/stop")
def api_stop_group_comment():
    group_comment_engine.stop()
    return {"success": True, "message": "Đã gửi lệnh dừng comment nhóm."}

@app.get("/api/group-comment/status")
def api_group_comment_status():
    return group_comment_engine.stats

# Group Bump APIs
class StartGroupBumpRequest(BaseModel):
    account_id: str
    post_urls: List[str]
    comment_text: str = ". | Quan tâm ạ | Check inbox shop ơi | Hàng sẵn nhé ạ"
    like_post: bool = True
    min_delay: int = 15
    max_delay: int = 30
    headless: bool = False

@app.post("/api/group-bump/start")
def api_start_group_bump(req: StartGroupBumpRequest):
    acc = next((a for a in account_mgr.get_accounts() if a["id"] == req.account_id), None)
    if not acc:
        return JSONResponse(status_code=404, content={"error": "Tài khoản không tồn tại"})
    if not req.post_urls:
        return JSONResponse(status_code=400, content={"error": "Vui lòng nhập ít nhất 1 đường link bài viết nhóm"})

    ok = group_bump_engine.start_bumping(acc, req.dict())
    return {"success": ok, "message": f"Đã bắt đầu tiến trình Bump bài nhóm cho {acc['name']}!"}

@app.post("/api/group-bump/stop")
def api_stop_group_bump():
    group_bump_engine.stop_bumping()
    return {"success": True, "message": "Đã gửi lệnh dừng Bump bài nhóm."}

@app.get("/api/group-bump/status")
def api_group_bump_status():
    return {
        "state": group_bump_engine.state,
        "stats": group_bump_engine.stats
    }



# Comment Shield APIs (Ẩn bình luận SĐT bảo vệ khách)
class StartShieldRequest(BaseModel):
    account_id: str
    page_url: str
    phone_regex: str = r"(?:(?:\+84|84|0)[3|5|7|8|9])(?:[\s.-]?\d){8}\b"
    poll_interval: int = 15
    headless: bool = True

@app.post("/api/shield/start")
def api_start_shield(req: StartShieldRequest):
    acc = next((a for a in account_mgr.get_accounts() if a["id"] == req.account_id), None)
    if not acc:
        return JSONResponse(status_code=404, content={"error": "Tài khoản không tồn tại"})
    if not req.page_url:
        return JSONResponse(status_code=400, content={"error": "Vui lòng nhập URL Fanpage cần bảo vệ"})

    ok = comment_shield_engine.start_shield(acc, req.dict())
    return {"success": ok, "message": f"Đã kích hoạt lá chắn bảo vệ Fanpage cho {acc['name']}!"}

@app.post("/api/shield/stop")
def api_stop_shield():
    comment_shield_engine.stop_shield()
    return {"success": True, "message": "Đã dừng giám sát bảo vệ Fanpage."}

@app.get("/api/shield/status")
def api_shield_status():
    return {
        "state": comment_shield_engine.state,
        "stats": comment_shield_engine.stats
    }

# Reels Publisher APIs (Đăng video ngắn Reels)
class StartReelsRequest(BaseModel):
    account_id: str
    video_paths: List[str] = []
    video_dir: str = ""
    caption: str = "#reels #trending #viral"
    role_type: str = "personal"
    role_url: Optional[str] = None
    role_name: Optional[str] = None
    min_delay: int = 30
    max_delay: int = 60
    headless: bool = False

@app.post("/api/reels/start")
def api_start_reels(req: StartReelsRequest):
    acc = next((a for a in account_mgr.get_accounts() if a["id"] == req.account_id), None)
    if not acc:
        return JSONResponse(status_code=404, content={"error": "Tài khoản không tồn tại"})
    if not req.video_paths and not req.video_dir:
        return JSONResponse(status_code=400, content={"error": "Vui lòng chọn tệp video hoặc thư mục video"})

    ok = reels_publisher_engine.start_publishing(acc, req.dict())
    return {"success": ok, "message": f"Đã bắt đầu tiến trình đăng Reels cho {acc['name']}!"}

@app.post("/api/reels/stop")
def api_stop_reels():
    reels_publisher_engine.stop_publishing()
    return {"success": True, "message": "Đã dừng tiến trình đăng Reels."}

@app.get("/api/reels/status")
def api_reels_status():
    return {
        "state": reels_publisher_engine.state,
        "stats": reels_publisher_engine.stats
    }

# =========================================================
# META SENTINEL & TELEMETRY APIS
# =========================================================
class ResetCircuitRequest(BaseModel):
    account_id: str

class SimulateViolationRequest(BaseModel):
    account_id: str
    feature: str = "groups_post"
    violation_type: str = "ACTION_BLOCK"
    severity: str = "HIGH"
    error_text: str = "Bạn tạm thời bị chặn sử dụng tính năng này do vi phạm Tiêu chuẩn cộng đồng"

@app.get("/api/meta/telemetry/summary")
def api_meta_summary():
    return meta_sentinel.get_telemetry_summary()

@app.get("/api/meta/telemetry/violations")
def api_meta_violations(limit: int = 50):
    return {"violations": meta_sentinel.violations[:limit]}

@app.get("/api/meta/telemetry/recommendations")
def api_meta_recommendations():
    return meta_sentinel.generate_recommendations()

@app.post("/api/meta/telemetry/apply-recommendations")
def api_meta_apply_recommendations(req: Optional[Dict[str, Any]] = None):
    res = meta_sentinel.apply_recommended_presets(req)
    broadcast_log("🛡️ [Meta Sentinel] Đã áp dụng các thông số cấu hình an toàn khuyến nghị vào hệ thống!", "success")
    return res

@app.post("/api/meta/telemetry/reset-circuit-breaker")
def api_meta_reset_circuit(req: ResetCircuitRequest):
    meta_sentinel.reset_circuit_breaker(req.account_id)
    broadcast_log(f"✅ [Meta Sentinel] Đã mở lại phanh an toàn cho tài khoản '{req.account_id}'.", "info", req.account_id)
    return {"success": True, "message": f"Đã mở lại phanh an toàn cho tài khoản {req.account_id}"}

@app.post("/api/meta/telemetry/simulate-violation")
def api_meta_simulate_violation(req: SimulateViolationRequest):
    acc = next((a for a in account_mgr.get_accounts() if a["id"] == req.account_id), None)
    acc_name = acc["name"] if acc else req.account_id
    v = meta_sentinel.record_violation(
        account_id=req.account_id,
        account_name=acc_name,
        feature=req.feature,
        violation_type=req.violation_type,
        severity=req.severity,
        error_text=req.error_text,
        url="https://www.facebook.com/",
        context_info="Giả lập kiểm thử cảnh báo phản ứng Meta"
    )
    if req.severity in ["HIGH", "CRITICAL"]:
        meta_sentinel.trip_circuit_breaker(req.account_id, req.error_text)
    return {"success": True, "violation": v}


# =========================================================
# PHÂN HỆ 1: QUÉT UID & GRAPH SEARCH (UIDScraperEngine)
# =========================================================
class ScrapeReactionsRequest(BaseModel):
    post_url: str
    account_id: Optional[str] = "acc_1"
    reaction_type: Optional[str] = "ALL"
    max_count: Optional[int] = 50

class ScrapeCommentsRequest(BaseModel):
    post_url: str
    account_id: Optional[str] = "acc_1"
    max_count: Optional[int] = 50

class ScrapeGroupMembersRequest(BaseModel):
    group_url: str
    account_id: Optional[str] = "acc_1"
    role_filter: Optional[str] = "ALL"
    max_count: Optional[int] = 50

class ScrapeFriendsRequest(BaseModel):
    target_uid: str
    account_id: Optional[str] = "acc_1"
    max_count: Optional[int] = 50

class ExportUIDsRequest(BaseModel):
    data: List[Dict[str, Any]]
    format: Optional[str] = "excel"
    file_prefix: Optional[str] = "uids_export"

@app.post("/api/scraper/reactions")
def api_scrape_reactions(req: ScrapeReactionsRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🔍 [UID Scraper] {msg}", lvl, req.account_id)
    res = uid_scraper_engine.scrape_post_reactions(
        post_url=req.post_url,
        account_id=req.account_id,
        reaction_type=req.reaction_type,
        max_count=req.max_count,
        log_callback=cb
    )
    return res

@app.post("/api/scraper/comments")
def api_scrape_comments(req: ScrapeCommentsRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🔍 [UID Scraper] {msg}", lvl, req.account_id)
    res = uid_scraper_engine.scrape_post_comments(
        post_url=req.post_url,
        account_id=req.account_id,
        max_count=req.max_count,
        log_callback=cb
    )
    return res

@app.post("/api/scraper/group-members")
def api_scrape_group_members(req: ScrapeGroupMembersRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🔍 [UID Scraper] {msg}", lvl, req.account_id)
    res = uid_scraper_engine.scrape_group_members(
        group_url_or_id=req.group_url,
        account_id=req.account_id,
        role_filter=req.role_filter,
        max_count=req.max_count,
        log_callback=cb
    )
    return res

@app.post("/api/scraper/friends")
def api_scrape_friends(req: ScrapeFriendsRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🔍 [UID Scraper] {msg}", lvl, req.account_id)
    res = uid_scraper_engine.scrape_user_friends(
        target_uid=req.target_uid,
        account_id=req.account_id,
        max_count=req.max_count,
        log_callback=cb
    )
    return res

@app.post("/api/scraper/export")
def api_export_scraped_uids(req: ExportUIDsRequest):
    file_path = uid_scraper_engine.export_data(req.data, req.format, req.file_prefix)
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if req.format == "excel" else "text/plain"
    filename = Path(file_path).name
    return FileResponse(path=file_path, filename=filename, media_type=media_type)


# =========================================================
# PHÂN HỆ 2: MARKETPLACE & NHÓM RAO VẶT (MarketplaceEngine)
# =========================================================
class PublishMarketplaceRequest(BaseModel):
    account_id: str
    title_spintax: str
    price: int
    category: str
    condition: str = "Mới 100%"
    description_spintax: str
    location: Optional[str] = "Hà Nội"
    hide_from_friends: Optional[bool] = True
    image_paths: Optional[List[str]] = []

class PublishGroupForSaleRequest(BaseModel):
    account_id: str
    group_id: str
    title_spintax: str
    price: int
    description_spintax: str
    location: Optional[str] = "Hà Nội"
    image_paths: Optional[List[str]] = []

@app.get("/api/marketplace/listings")
def api_get_marketplace_listings():
    return {"listings": marketplace_engine.get_listings()}

@app.post("/api/marketplace/publish")
def api_publish_marketplace(req: PublishMarketplaceRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🛒 [Marketplace] {msg}", lvl, req.account_id)
    res = marketplace_engine.publish_listing(
        account_id=req.account_id,
        title_spintax=req.title_spintax,
        price=req.price,
        category=req.category,
        condition=req.condition,
        description_spintax=req.description_spintax,
        location=req.location or "Hà Nội",
        hide_from_friends=req.hide_from_friends,
        image_paths=req.image_paths,
        log_callback=cb
    )
    return res

@app.post("/api/marketplace/group-for-sale")
def api_publish_group_for_sale(req: PublishGroupForSaleRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🛒 [Marketplace Group] {msg}", lvl, req.account_id)
    res = marketplace_engine.publish_group_for_sale(
        account_id=req.account_id,
        group_id=req.group_id,
        title_spintax=req.title_spintax,
        price=req.price,
        description_spintax=req.description_spintax,
        location=req.location or "Hà Nội",
        image_paths=req.image_paths,
        log_callback=cb
    )
    return res

@app.post("/api/marketplace/delete")
def api_delete_marketplace_listing(payload: Dict[str, Any]):
    ok = marketplace_engine.delete_listing(payload.get("id", ""))
    return {"success": ok}


# =========================================================
# PHÂN HỆ 3: NHẮN TIN TỰ ĐỘNG & MESSENGER (MessengerEngine)
# =========================================================
class SendBulkDMRequest(BaseModel):
    account_id: str
    uids: List[str]
    message_spintax: str
    image_paths: Optional[List[str]] = []
    delay_seconds: Optional[int] = 5

class AutoReplyInboxCommentRequest(BaseModel):
    account_id: str
    post_id: str
    comment_id: str
    public_reply: str
    private_inbox: str

@app.get("/api/messenger/history")
def api_get_messenger_history():
    return {"history": messenger_engine.get_history()}

@app.post("/api/messenger/send-bulk")
def api_send_bulk_dm(req: SendBulkDMRequest):
    def cb(msg, lvl="info"): broadcast_log(f"💬 [Messenger DM] {msg}", lvl, req.account_id)
    res = messenger_engine.send_bulk_dm(
        account_id=req.account_id,
        uids=req.uids,
        message_spintax=req.message_spintax,
        image_paths=req.image_paths,
        delay_seconds=req.delay_seconds or 5,
        log_callback=cb
    )
    return res

@app.post("/api/messenger/auto-reply-comment")
def api_auto_reply_inbox(req: AutoReplyInboxCommentRequest):
    def cb(msg, lvl="info"): broadcast_log(f"💬 [Messenger Auto-Reply] {msg}", lvl, req.account_id)
    res = messenger_engine.auto_reply_and_inbox_comment(
        account_id=req.account_id,
        post_id=req.post_id,
        comment_id=req.comment_id,
        public_reply_spintax=req.public_reply,
        private_inbox_spintax=req.private_inbox,
        log_callback=cb
    )
    return res


# =========================================================
# PHÂN HỆ 4: QUẢN TRỊ BẠN BÈ & VƯỢT CHECKPOINT (FriendshipEngine)
# =========================================================
class AddFriendsRequest(BaseModel):
    account_id: str
    uids: List[str]
    delay_seconds: Optional[int] = 5
    daily_limit: Optional[int] = 50

class CancelSentRequestsRequest(BaseModel):
    account_id: str
    max_cancel: Optional[int] = 50

class AcceptFriendsRequest(BaseModel):
    account_id: str
    max_accept: Optional[int] = 50

class UnfriendRequest(BaseModel):
    account_id: str
    max_unfriend: Optional[int] = 50

class BatchFriendUidsRequest(BaseModel):
    account_id: str
    uids: List[str]

class BatchAddTargetUidsRequest(BaseModel):
    account_id: str
    uids: List[str]
    note: Optional[str] = ""

class BatchSendAddFriendsRequest(BaseModel):
    account_id: str
    uids: List[str]
    delay_seconds: Optional[int] = 5

class ScanFriendshipProfileRequest(BaseModel):
    account_id: str

@app.get("/api/friendship/data")
def api_get_friendship_data(account_id: str = "acc_1"):
    return friendship_engine.get_profile_friendship_data(account_id)

@app.post("/api/friendship/scan-profile")
def api_scan_friendship_profile(req: ScanFriendshipProfileRequest):
    def cb(msg, lvl="info"): broadcast_log(f"👥 [Friendship] {msg}", lvl, req.account_id)
    return friendship_engine.scan_profile_friends(req.account_id, cb)

@app.post("/api/friendship/unfriend-batch")
def api_friendship_unfriend_batch(req: BatchFriendUidsRequest):
    def cb(msg, lvl="info"): broadcast_log(f"👥 [Friendship] {msg}", lvl, req.account_id)
    return friendship_engine.unfriend_batch(req.account_id, req.uids, cb)

@app.post("/api/friendship/cancel-sent-batch")
def api_friendship_cancel_sent_batch(req: BatchFriendUidsRequest):
    def cb(msg, lvl="info"): broadcast_log(f"👥 [Friendship] {msg}", lvl, req.account_id)
    return friendship_engine.cancel_sent_batch(req.account_id, req.uids, cb)

@app.post("/api/friendship/accept-incoming-batch")
def api_friendship_accept_incoming_batch(req: BatchFriendUidsRequest):
    def cb(msg, lvl="info"): broadcast_log(f"👥 [Friendship] {msg}", lvl, req.account_id)
    return friendship_engine.accept_incoming_batch(req.account_id, req.uids, cb)

@app.post("/api/friendship/reject-incoming-batch")
def api_friendship_reject_incoming_batch(req: BatchFriendUidsRequest):
    def cb(msg, lvl="info"): broadcast_log(f"👥 [Friendship] {msg}", lvl, req.account_id)
    return friendship_engine.reject_incoming_batch(req.account_id, req.uids, cb)

@app.post("/api/friendship/add-target-uids")
def api_friendship_add_targets(req: BatchAddTargetUidsRequest):
    def cb(msg, lvl="info"): broadcast_log(f"👥 [Friendship] {msg}", lvl, req.account_id)
    return friendship_engine.add_target_uids(req.account_id, req.uids, req.note or "", cb)

@app.post("/api/friendship/send-add-friends-batch")
def api_friendship_send_add_batch(req: BatchSendAddFriendsRequest):
    def cb(msg, lvl="info"): broadcast_log(f"👥 [Friendship] {msg}", lvl, req.account_id)
    return friendship_engine.send_add_friends_batch(req.account_id, req.uids, req.delay_seconds or 5, cb)

@app.post("/api/friendship/backup-checkpoint-batch")
def api_friendship_backup_batch(req: BatchFriendUidsRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🛡️ [Backup Checkpoint] {msg}", lvl, req.account_id)
    return friendship_engine.backup_checkpoint_batch(req.account_id, req.uids, cb)

@app.post("/api/friendship/add-by-uids")
def api_friendship_add_uids(req: AddFriendsRequest):
    def cb(msg, lvl="info"): broadcast_log(f"👥 [Friendship] {msg}", lvl, req.account_id)
    res = friendship_engine.add_friends_by_uids(
        account_id=req.account_id,
        uids=req.uids,
        delay_seconds=req.delay_seconds or 5,
        daily_limit=req.daily_limit or 50,
        log_callback=cb
    )
    return res

@app.post("/api/friendship/cancel-sent")
def api_friendship_cancel_sent(req: CancelSentRequestsRequest):
    def cb(msg, lvl="info"): broadcast_log(f"👥 [Friendship] {msg}", lvl, req.account_id)
    return friendship_engine.cancel_sent_requests(req.account_id, req.max_cancel or 50, cb)

@app.post("/api/friendship/accept-all")
def api_friendship_accept(req: AcceptFriendsRequest):
    def cb(msg, lvl="info"): broadcast_log(f"👥 [Friendship] {msg}", lvl, req.account_id)
    return friendship_engine.accept_friend_requests(req.account_id, req.max_accept or 50, cb)

@app.post("/api/friendship/unfriend")
def api_friendship_unfriend(req: UnfriendRequest):
    def cb(msg, lvl="info"): broadcast_log(f"👥 [Friendship] {msg}", lvl, req.account_id)
    return friendship_engine.unfriend_inactive(req.account_id, req.max_unfriend or 50, cb)

@app.post("/api/friendship/backup-checkpoint")
def api_friendship_backup(payload: Dict[str, Any]):
    acc_id = payload.get("account_id", "acc_1")
    def cb(msg, lvl="info"): broadcast_log(f"🛡️ [Backup Checkpoint] {msg}", lvl, acc_id)
    res = friendship_engine.backup_friends_for_checkpoint(acc_id, cb)
    return res

@app.get("/api/friendship/backup-friends")
def api_get_backup_friends(account_id: str = "acc_1"):
    return {"friends": friendship_engine.get_backup_friends(account_id)}



# =========================================================
# PHÂN HỆ 5: MỜI BẠN BÈ & QUẢN TRỊ GROUP (CommunityInviteEngine)
# =========================================================
class InvitePageRequest(BaseModel):
    account_id: str
    page_id_or_url: str
    max_invites: Optional[int] = 100

class InviteGroupRequest(BaseModel):
    account_id: str
    group_id_or_url: str
    max_invites: Optional[int] = 100

class ApproveMembersRequest(BaseModel):
    account_id: str
    group_id: str
    min_months: Optional[int] = 3
    must_have_avatar: Optional[bool] = True

class BlockAdminsRequest(BaseModel):
    account_id: str
    group_id: str

@app.post("/api/community/invite-page")
def api_invite_page(req: InvitePageRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🤝 [Mời Bạn Bè] {msg}", lvl, req.account_id)
    return community_invite_engine.invite_friends_to_page(req.account_id, req.page_id_or_url, req.max_invites or 100, cb)

@app.post("/api/community/invite-group")
def api_invite_group(req: InviteGroupRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🤝 [Mời Bạn Bè] {msg}", lvl, req.account_id)
    return community_invite_engine.invite_friends_to_group(req.account_id, req.group_id_or_url, req.max_invites or 100, cb)

@app.post("/api/community/approve-members")
def api_approve_members(req: ApproveMembersRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🛡️ [Duyệt Nhóm] {msg}", lvl, req.account_id)
    return community_invite_engine.auto_approve_group_members(
        req.account_id, req.group_id, req.min_months or 3, req.must_have_avatar, cb
    )

@app.post("/api/community/block-admins")
def api_block_admins(req: BlockAdminsRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🛡️ [Block Admin] {msg}", lvl, req.account_id)
    return community_invite_engine.block_group_admins(req.account_id, req.group_id, cb)


# =========================================================
# PHÂN HỆ 6: CÀO & REUP BÀI VIẾT (ContentCloneEngine)
# =========================================================
class ScrapeTargetPostsRequest(BaseModel):
    target_url: str
    max_posts: Optional[int] = 5

class CleanCaptionRequest(BaseModel):
    caption: str
    replace_phone: Optional[str] = ""
    replace_link: Optional[str] = ""
    extra_hashtags: Optional[str] = ""

class RepostRequest(BaseModel):
    account_id: str
    post_id: str
    destination_type: Optional[str] = "feed"
    destination_id: Optional[str] = ""
    custom_caption: Optional[str] = ""

@app.get("/api/clone/posts")
def api_get_cloned_posts():
    return {"posts": content_clone_engine.get_cloned_posts()}

@app.post("/api/clone/scrape-target")
def api_scrape_clone_target(req: ScrapeTargetPostsRequest):
    def cb(msg, lvl="info"): broadcast_log(f"📑 [Clone Content] {msg}", lvl)
    return content_clone_engine.scrape_target_posts(req.target_url, req.max_posts or 5, cb)

@app.post("/api/clone/clean-caption")
def api_clean_caption(req: CleanCaptionRequest):
    res = content_clone_engine.clean_and_spin_caption(
        req.caption, req.replace_phone or "", req.replace_link or "", req.extra_hashtags or ""
    )
    return {"cleaned_caption": res}

@app.post("/api/clone/repost")
def api_repost_content(req: RepostRequest):
    def cb(msg, lvl="info"): broadcast_log(f"📑 [Repost Content] {msg}", lvl, req.account_id)
    return content_clone_engine.repost_content(
        account_id=req.account_id,
        post_id=req.post_id,
        destination_type=req.destination_type or "feed",
        destination_id=req.destination_id or "",
        custom_caption=req.custom_caption or "",
        log_callback=cb
    )


# =========================================================
# PHÂN HỆ 7: TOKEN, COOKIE & ĐỔI IP DCOM (TokenToolEngine)
# =========================================================
class CheckTokensRequest(BaseModel):
    tokens: List[str]

class CheckCookiesRequest(BaseModel):
    cookies: List[str]

class ReconnectDcomRequest(BaseModel):
    hilink_ip: Optional[str] = "192.168.8.1"

@app.post("/api/token-tool/check-tokens")
def api_check_tokens(req: CheckTokensRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🔑 [Token Tool] {msg}", lvl)
    return token_tool_engine.check_tokens_status(req.tokens, cb)

@app.post("/api/token-tool/check-cookies")
def api_check_cookies(req: CheckCookiesRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🔑 [Cookie Tool] {msg}", lvl)
    return token_tool_engine.check_cookies_status(req.cookies, cb)

@app.post("/api/token-tool/dcom-reconnect")
def api_reconnect_dcom(req: ReconnectDcomRequest):
    def cb(msg, lvl="info"): broadcast_log(f"🔄 [Dcom IP] {msg}", lvl)
    return token_tool_engine.reconnect_dcom(req.hilink_ip or "192.168.8.1", cb)

if __name__ == "__main__":
    import uvicorn
    print("Khởi động Facebook Auto Poster Dashboard tại http://127.0.0.1:8000 ...")
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
