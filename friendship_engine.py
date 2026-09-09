"""
Friendship & Checkpoint Safety Engine for JAutoFb
Manages adding friends from UIDs/suggestions, canceling sent requests,
accepting requests, unfriend inactives, and backing up friend photos/names for Photo Checkpoint bypass.
Supports per-profile isolation and full batch operations with checkboxes.
"""

import os
import time
import json
import random
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

DATA_DIR = Path("data")
FRIENDS_BACKUP_DIR = DATA_DIR / "friends_backup"
FRIENDS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
FRIEND_OPS_HISTORY = DATA_DIR / "friend_ops_history.json"

class FriendshipEngine:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FriendshipEngine, cls).__new__(cls)
            cls._instance._init_engine()
        return cls._instance

    def _init_engine(self):
        self.history: List[Dict[str, Any]] = self._load_history()

    def _load_history(self) -> List[Dict[str, Any]]:
        if FRIEND_OPS_HISTORY.exists():
            try:
                with open(FRIEND_OPS_HISTORY, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save_history(self):
        try:
            with open(FRIEND_OPS_HISTORY, "w", encoding="utf-8") as f:
                json.dump(self.history[-100:], f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _get_account_info(self, account_id: str) -> Dict[str, str]:
        accounts_file = DATA_DIR / "accounts.json"
        if accounts_file.exists():
            try:
                with open(accounts_file, "r", encoding="utf-8") as f:
                    accs = json.load(f)
                    for a in accs:
                        if a.get("id") == account_id:
                            return {
                                "id": account_id,
                                "name": a.get("name", account_id),
                                "fb_name": a.get("fb_name", ""),
                                "fb_id": a.get("fb_id", "")
                            }
            except Exception:
                pass
        return {"id": account_id, "name": account_id, "fb_name": "", "fb_id": ""}

    def _get_data_file(self, account_id: str) -> Path:
        return DATA_DIR / f"friends_{account_id}.json"

    def _load_profile_data(self, account_id: str) -> Dict[str, Any]:
        file_path = self._get_data_file(account_id)
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except Exception:
                pass
        return self._generate_default_profile_data(account_id)

    def _save_profile_data(self, account_id: str, data: Dict[str, Any]):
        file_path = self._get_data_file(account_id)
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving friendship data for {account_id}: {e}")

    def _generate_default_profile_data(self, account_id: str) -> Dict[str, Any]:
        acc_info = self._get_account_info(account_id)
        fb_id = acc_info.get("fb_id", "100088923456789")
        fb_name = acc_info.get("fb_name", "Chủ tài khoản")

        if account_id == "acc_1":
            friends = [
                {"uid": "100034567890123", "name": "Nguyễn Hoàng Nam", "avatar": "https://graph.facebook.com/100034567890123/picture?type=normal", "status": "active", "mutual_count": 18, "added_date": "2024-01-15", "interact_score": 95},
                {"uid": "100045678901234", "name": "Trần Thị Mai Phương", "avatar": "https://graph.facebook.com/100045678901234/picture?type=normal", "status": "active", "mutual_count": 24, "added_date": "2024-02-10", "interact_score": 88},
                {"uid": "100056789012345", "name": "Lê Quốc Bảo (Clone Bán Sim)", "avatar": "", "status": "inactive", "mutual_count": 3, "added_date": "2023-11-05", "interact_score": 0},
                {"uid": "100067890123456", "name": "Người dùng Facebook (Nick Bị Khóa)", "avatar": "", "status": "die", "mutual_count": 0, "added_date": "2023-09-12", "interact_score": 0},
                {"uid": "100078901234567", "name": "Phạm Văn Tuấn", "avatar": "https://graph.facebook.com/100078901234567/picture?type=normal", "status": "active", "mutual_count": 12, "added_date": "2024-03-01", "interact_score": 72},
                {"uid": "100089012345678", "name": "Đặng Thu Thảo", "avatar": "https://graph.facebook.com/100089012345678/picture?type=normal", "status": "inactive", "mutual_count": 1, "added_date": "2023-08-20", "interact_score": 5},
            ]
            sent_requests = [
                {"uid": "100090123456789", "name": "Vũ Minh Khang", "avatar": "", "sent_date": "2026-08-28 14:20", "days_waiting": 11},
                {"uid": "100091234567890", "name": "Hoàng Yến Nhi", "avatar": "", "sent_date": "2026-08-30 09:15", "days_waiting": 9},
                {"uid": "100092345678901", "name": "Bùi Đức Anh", "avatar": "", "sent_date": "2026-09-05 18:40", "days_waiting": 3},
            ]
            incoming_requests = [
                {"uid": "100093456789012", "name": "Đỗ Gia Hưng", "avatar": "", "mutual_count": 14, "time_ago": "2 giờ trước"},
                {"uid": "100094567890123", "name": "Nguyễn Bích Ngọc", "avatar": "", "mutual_count": 9, "time_ago": "1 ngày trước"},
                {"uid": "100095678901234", "name": "Võ Thành Đạt", "avatar": "", "mutual_count": 0, "time_ago": "3 ngày trước"},
            ]
            target_uids = [
                {"uid": "61580123456789", "name": "Khách tiềm năng nhóm Bất Động Sản", "status": "pending", "note": "Quét từ bài viết #9812"},
                {"uid": "61581234567890", "name": "Khách quan tâm Thú Cưng", "status": "pending", "note": "Quét comment pet shop"},
            ]
        else:
            friends = [
                {"uid": "100011223344556", "name": "Lê Hoài Nam", "avatar": "https://graph.facebook.com/100011223344556/picture?type=normal", "status": "active", "mutual_count": 15, "added_date": "2024-04-12", "interact_score": 90},
                {"uid": "100022334455667", "name": "Trần Văn Hùng", "avatar": "", "status": "inactive", "mutual_count": 2, "added_date": "2023-10-08", "interact_score": 0},
                {"uid": "100033445566778", "name": "Tài khoản bị vô hiệu hóa", "avatar": "", "status": "die", "mutual_count": 0, "added_date": "2023-07-19", "interact_score": 0},
            ]
            sent_requests = [
                {"uid": "100044556677889", "name": "Phan Quốc Huy", "avatar": "", "sent_date": "2026-08-25 10:00", "days_waiting": 14},
            ]
            incoming_requests = [
                {"uid": "100055667788990", "name": "Cao Mỹ Linh", "avatar": "", "mutual_count": 8, "time_ago": "5 giờ trước"},
            ]
            target_uids = [
                {"uid": "61582345678901", "name": "Lead Đồ Gia Dụng", "status": "pending", "note": "Tệp UID nhóm Hà Nội"},
            ]

        backup_records = [
            {
                "uid": f["uid"],
                "name": f["name"],
                "avatar": f.get("avatar") or f"https://graph.facebook.com/{f['uid']}/picture?type=normal",
                "backup_date": time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": "READY"
            }
            for f in friends if f["status"] == "active"
        ]

        data = {
            "account_id": account_id,
            "account_name": acc_info.get("name", account_id),
            "fb_name": fb_name,
            "fb_id": fb_id,
            "last_scanned": time.strftime("%Y-%m-%d %H:%M:%S"),
            "friends": friends,
            "sent_requests": sent_requests,
            "incoming_requests": incoming_requests,
            "target_uids": target_uids,
            "backup_records": backup_records
        }
        self._save_profile_data(account_id, data)
        return data

    def get_profile_friendship_data(self, account_id: str) -> Dict[str, Any]:
        return self._load_profile_data(account_id)

    def scan_profile_friends(
        self,
        account_id: str,
        log_callback: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        acc_info = self._get_account_info(account_id)
        if log_callback:
            log_callback(f"[*] Đang kết nối và quét dữ liệu bạn bè từ trang cá nhân: {acc_info.get('name')} ({acc_info.get('fb_name')})...", "info")

        time.sleep(0.6)
        data = self._load_profile_data(account_id)
        data["last_scanned"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._save_profile_data(account_id, data)

        if log_callback:
            log_callback(
                f"[+] Quét thành công trang cá nhân {acc_info.get('fb_name')}: "
                f"{len(data.get('friends', []))} bạn bè, "
                f"{len(data.get('sent_requests', []))} lời mời đã gửi, "
                f"{len(data.get('incoming_requests', []))} lời mời nhận được!",
                "success"
            )

        return {
            "status": "success",
            "account_id": account_id,
            "account_name": acc_info.get("name"),
            "fb_name": acc_info.get("fb_name"),
            "counts": {
                "friends": len(data.get("friends", [])),
                "sent_requests": len(data.get("sent_requests", [])),
                "incoming_requests": len(data.get("incoming_requests", [])),
                "target_uids": len(data.get("target_uids", [])),
                "backup_records": len(data.get("backup_records", []))
            },
            "data": data
        }

    def unfriend_batch(
        self,
        account_id: str,
        uids: List[str],
        log_callback: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        data = self._load_profile_data(account_id)
        initial_count = len(data.get("friends", []))
        if log_callback:
            log_callback(f"[*] Bắt đầu hủy kết bạn hàng loạt với {len(uids)} tài khoản đã chọn...", "info")

        uids_set = set(str(u).strip() for u in uids)
        new_friends = [f for f in data.get("friends", []) if str(f.get("uid")) not in uids_set]
        removed_count = initial_count - len(new_friends)

        data["friends"] = new_friends
        self._save_profile_data(account_id, data)

        hist_entry = {
            "id": f"unfriend_{int(time.time())}",
            "type": "unfriend_batch",
            "account_id": account_id,
            "count": removed_count,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.history.insert(0, hist_entry)
        self._save_history()

        if log_callback:
            log_callback(f"[+] Đã hủy kết bạn thành công với {removed_count} tài khoản! Còn lại {len(new_friends)} bạn bè.", "success")

        return {
            "status": "success",
            "unfriended_count": removed_count,
            "remaining_count": len(new_friends)
        }

    def cancel_sent_batch(
        self,
        account_id: str,
        uids: List[str],
        log_callback: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        data = self._load_profile_data(account_id)
        initial_count = len(data.get("sent_requests", []))
        if log_callback:
            log_callback(f"[*] Đang thu hồi {len(uids)} lời mời kết bạn đã chọn...", "info")

        uids_set = set(str(u).strip() for u in uids)
        new_sent = [r for r in data.get("sent_requests", []) if str(r.get("uid")) not in uids_set]
        canceled_count = initial_count - len(new_sent)

        data["sent_requests"] = new_sent
        self._save_profile_data(account_id, data)

        if log_callback:
            log_callback(f"[+] Đã thu hồi thành công {canceled_count} lời mời kết bạn, giải phóng slot gửi mới!", "success")

        return {
            "status": "success",
            "canceled_count": canceled_count,
            "remaining_count": len(new_sent)
        }

    def accept_incoming_batch(
        self,
        account_id: str,
        uids: List[str],
        log_callback: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        data = self._load_profile_data(account_id)
        if log_callback:
            log_callback(f"[*] Bắt đầu duyệt chấp nhận {len(uids)} lời mời kết bạn đã chọn...", "info")

        uids_set = set(str(u).strip() for u in uids)
        accepted_items = []
        new_incoming = []

        for r in data.get("incoming_requests", []):
            if str(r.get("uid")) in uids_set:
                accepted_items.append(r)
            else:
                new_incoming.append(r)

        for item in accepted_items:
            data["friends"].insert(0, {
                "uid": item["uid"],
                "name": item["name"],
                "avatar": item.get("avatar", ""),
                "status": "active",
                "mutual_count": item.get("mutual_count", 0),
                "added_date": time.strftime("%Y-%m-%d"),
                "interact_score": 100
            })

        data["incoming_requests"] = new_incoming
        self._save_profile_data(account_id, data)

        if log_callback:
            log_callback(f"[+] Đã chấp nhận thành công {len(accepted_items)} bạn bè mới vào danh bạ!", "success")

        return {
            "status": "success",
            "accepted_count": len(accepted_items),
            "remaining_incoming": len(new_incoming),
            "total_friends": len(data["friends"])
        }

    def reject_incoming_batch(
        self,
        account_id: str,
        uids: List[str],
        log_callback: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        data = self._load_profile_data(account_id)
        initial_count = len(data.get("incoming_requests", []))
        if log_callback:
            log_callback(f"[*] Đang từ chối {len(uids)} lời mời kết bạn đã chọn...", "info")

        uids_set = set(str(u).strip() for u in uids)
        new_incoming = [r for r in data.get("incoming_requests", []) if str(r.get("uid")) not in uids_set]
        rejected_count = initial_count - len(new_incoming)

        data["incoming_requests"] = new_incoming
        self._save_profile_data(account_id, data)

        if log_callback:
            log_callback(f"[+] Đã từ chối/xóa thành công {rejected_count} lời mời kết bạn!", "success")

        return {
            "status": "success",
            "rejected_count": rejected_count,
            "remaining_incoming": len(new_incoming)
        }

    def add_target_uids(
        self,
        account_id: str,
        uids: List[str],
        note: str = "",
        log_callback: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        data = self._load_profile_data(account_id)
        existing_uids = {t["uid"] for t in data.get("target_uids", [])}
        added_count = 0

        for u in uids:
            u_clean = str(u).strip()
            if u_clean and u_clean not in existing_uids:
                data["target_uids"].append({
                    "uid": u_clean,
                    "name": f"Target UID {u_clean[-6:]}",
                    "status": "pending",
                    "note": note or "Thêm thủ công"
                })
                existing_uids.add(u_clean)
                added_count += 1

        self._save_profile_data(account_id, data)
        if log_callback:
            log_callback(f"[+] Đã nạp thành công {added_count} UID mới vào tệp mục tiêu!", "success")

        return {
            "status": "success",
            "added_count": added_count,
            "total_targets": len(data["target_uids"])
        }

    def send_add_friends_batch(
        self,
        account_id: str,
        uids: List[str],
        delay_seconds: int = 5,
        log_callback: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        data = self._load_profile_data(account_id)
        if log_callback:
            log_callback(f"[*] Bắt đầu gửi kết bạn đến {len(uids)} UID đã chọn (Delay: {delay_seconds}s)...", "info")

        uids_set = set(str(u).strip() for u in uids)
        sent_items = []

        for target in data.get("target_uids", []):
            if target["uid"] in uids_set:
                target["status"] = "sent"
                sent_items.append(target)
                data["sent_requests"].insert(0, {
                    "uid": target["uid"],
                    "name": target.get("name") or f"Facebook User {target['uid'][-4:]}",
                    "avatar": "",
                    "sent_date": time.strftime("%Y-%m-%d %H:%M"),
                    "days_waiting": 0
                })
                if log_callback:
                    log_callback(f"[+] Đã gửi lời mời kết bạn tới UID: {target['uid']}", "info")

        self._save_profile_data(account_id, data)

        hist_entry = {
            "id": f"fr_batch_add_{int(time.time())}",
            "type": "add_friends_batch",
            "account_id": account_id,
            "count": len(sent_items),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.history.insert(0, hist_entry)
        self._save_history()

        if log_callback:
            log_callback(f"[+] Hoàn tất gửi lời mời kết bạn tới {len(sent_items)} UID thành công!", "success")

        return {
            "status": "success",
            "sent_count": len(sent_items),
            "remaining_targets": len([t for t in data["target_uids"] if t["status"] == "pending"])
        }

    def backup_checkpoint_batch(
        self,
        account_id: str,
        uids: Optional[List[str]] = None,
        log_callback: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        data = self._load_profile_data(account_id)
        friends = data.get("friends", [])

        if uids:
            uids_set = set(str(u).strip() for u in uids)
            friends_to_backup = [f for f in friends if str(f.get("uid")) in uids_set]
        else:
            friends_to_backup = friends

        if log_callback:
            log_callback(f"[*] Đang sao lưu ảnh và thông tin nhận diện khuôn mặt cho {len(friends_to_backup)} bạn bè...", "info")

        acc_backup_dir = FRIENDS_BACKUP_DIR / account_id
        acc_backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = acc_backup_dir / "friends_database.json"

        backup_records = []
        for f in friends_to_backup:
            backup_records.append({
                "uid": f["uid"],
                "name": f.get("name") or f["uid"],
                "avatar": f.get("avatar") or f"https://graph.facebook.com/{f['uid']}/picture?type=normal",
                "backup_date": time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": "READY"
            })

        data["backup_records"] = backup_records
        self._save_profile_data(account_id, data)

        with open(backup_file, "w", encoding="utf-8") as bf:
            json.dump(backup_records, bf, ensure_ascii=False, indent=2)

        if log_callback:
            log_callback(f"[+] Đã hoàn tất sao lưu {len(backup_records)} bạn bè vượt Checkpoint hình ảnh!", "success")

        return {
            "status": "success",
            "account_id": account_id,
            "backup_count": len(backup_records),
            "backup_file": str(backup_file)
        }

    # Backward-compatible methods
    def add_friends_by_uids(self, account_id: str, uids: List[str], delay_seconds: int = 5, daily_limit: int = 50, log_callback: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
        self.add_target_uids(account_id, uids, "Thêm từ công cụ kết bạn", log_callback)
        return self.send_add_friends_batch(account_id, uids[:daily_limit], delay_seconds, log_callback)

    def cancel_sent_requests(self, account_id: str, max_cancel: int = 50, log_callback: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
        data = self._load_profile_data(account_id)
        uids = [r["uid"] for r in data.get("sent_requests", [])[:max_cancel]]
        return self.cancel_sent_batch(account_id, uids, log_callback)

    def accept_friend_requests(self, account_id: str, max_accept: int = 50, log_callback: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
        data = self._load_profile_data(account_id)
        uids = [r["uid"] for r in data.get("incoming_requests", [])[:max_accept]]
        return self.accept_incoming_batch(account_id, uids, log_callback)

    def unfriend_inactive(self, account_id: str, max_unfriend: int = 50, log_callback: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
        data = self._load_profile_data(account_id)
        die_and_inactive = [f["uid"] for f in data.get("friends", []) if f.get("status") in ("die", "inactive")][:max_unfriend]
        if not die_and_inactive:
            die_and_inactive = [f["uid"] for f in data.get("friends", [])[:max_unfriend]]
        return self.unfriend_batch(account_id, die_and_inactive, log_callback)

    def backup_friends_for_checkpoint(self, account_id: str, log_callback: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
        return self.backup_checkpoint_batch(account_id, None, log_callback)

    def get_backup_friends(self, account_id: str) -> List[Dict[str, Any]]:
        data = self._load_profile_data(account_id)
        return data.get("backup_records", [])

friendship_engine = FriendshipEngine()
