"""
Meta Sentinel Core - Hệ Thống Lõi Giám Sát Phản Ứng Meta & Cảnh Báo Sớm Vi Phạm
Cung cấp:
- Giám sát URL & DOM thời gian thực trên Playwright (Checkpoint, Action Block, Toast, Captcha)
- Đánh giá Điểm Rủi Ro (Predictive Risk Score: 0 - 100) theo tần suất thao tác, độ trễ và độ lặp nội dung
- Cơ chế Phanh Khẩn Cấp (Circuit Breaker) tự động tạm dừng để cứu tài khoản không bị die vĩnh viễn
- Lưu trữ lịch sử vi phạm bền vững (data/meta_violations.json, data/meta_telemetry.json)
- Cố vấn thông số thông minh (Adaptive Safe Config Advisor) và áp dụng cấu hình an toàn 1-click
"""

import os
import sys
import time
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Callable

DATA_DIR = Path("data")
VIOLATIONS_FILE = DATA_DIR / "meta_violations.json"
TELEMETRY_FILE = DATA_DIR / "meta_telemetry.json"
SAFE_PRESETS_FILE = DATA_DIR / "meta_safe_presets.json"

META_BLOCKED_KEYWORDS = [
    "bạn tạm thời bị chặn",
    "bị chặn sử dụng tính năng",
    "bạn đang thao tác quá nhanh",
    "hành động này bị chặn",
    "tiêu chuẩn cộng đồng",
    "nội dung này có vẻ là spam",
    "tài khoản của bạn đã bị hạn chế",
    "không thể gửi bình luận",
    "không thể chia sẻ bài viết này",
    "đã xảy ra lỗi khi đăng",
    "vui lòng thử lại sau",
    "chúng tôi giới hạn tần suất",
    "hãy xác nhận danh tính",
    "you're temporarily blocked",
    "action blocked",
    "you are going too fast",
    "community standards",
    "this looks like spam",
    "your account has been restricted",
    "could not be posted",
    "could not share",
    "we limit how often you can do certain things",
    "please try again later",
    "confirm your identity"
]

META_CHECKPOINT_URL_PATTERNS = [
    "/checkpoint/",
    "/login/",
    "/recover/",
    "/two_step_verification/",
    "/help/contact/",
    "facebook.com/identity/"
]

DEFAULT_SAFE_CONFIGS = {
    "groups_post": {
        "min_delay": 60,
        "max_delay": 120,
        "daily_cap": 25,
        "cooldown_after_actions": 8,
        "cooldown_duration_minutes": 20,
        "min_content_diversity": 70
    },
    "group_comment": {
        "min_delay": 45,
        "max_delay": 90,
        "daily_cap": 35,
        "cooldown_after_actions": 10,
        "cooldown_duration_minutes": 15,
        "min_content_diversity": 60
    },
    "group_bump": {
        "min_delay": 40,
        "max_delay": 80,
        "daily_cap": 30,
        "cooldown_after_actions": 10,
        "cooldown_duration_minutes": 15,
        "min_content_diversity": 60
    },
    "reels": {
        "min_delay": 90,
        "max_delay": 180,
        "daily_cap": 12,
        "cooldown_after_actions": 5,
        "cooldown_duration_minutes": 30,
        "min_content_diversity": 80
    },
    "groups_interact": {
        "min_delay": 20,
        "max_delay": 45,
        "daily_cap": 50,
        "cooldown_after_actions": 15,
        "cooldown_duration_minutes": 15,
        "min_content_diversity": 85
    }
}

class MetaSentinelCore:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.alert_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self.log_callback: Optional[Callable[[str, str, Optional[str]], None]] = None
        self.account_state: Dict[str, Dict[str, Any]] = {}
        self.violations: List[Dict[str, Any]] = self._load_violations()
        self.safe_presets: Dict[str, Any] = self._load_safe_presets()

    def _load_violations(self) -> List[Dict[str, Any]]:
        if VIOLATIONS_FILE.exists():
            try:
                with open(VIOLATIONS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save_violations(self):
        try:
            with open(VIOLATIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.violations, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[MetaSentinel] Lỗi lưu violations: {e}")

    def _load_safe_presets(self) -> Dict[str, Any]:
        if SAFE_PRESETS_FILE.exists():
            try:
                with open(SAFE_PRESETS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return DEFAULT_SAFE_CONFIGS.copy()

    def _save_safe_presets(self):
        try:
            with open(SAFE_PRESETS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.safe_presets, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[MetaSentinel] Lỗi lưu safe presets: {e}")

    def _get_account_state(self, account_id: str) -> Dict[str, Any]:
        if account_id not in self.account_state:
            self.account_state[account_id] = {
                "recent_actions": [],
                "recent_contents": [],
                "consecutive_errors": 0,
                "circuit_tripped": False,
                "trip_reason": "",
                "trip_time": None,
                "last_risk_score": 0,
                "last_action_time": 0
            }
        return self.account_state[account_id]

    def inspect_page(self, page, account_id: str, feature: str, account_name: str = "", context_info: str = "") -> Optional[Dict[str, Any]]:
        if not page:
            return None
        try:
            current_url = page.url or ""
            for pattern in META_CHECKPOINT_URL_PATTERNS:
                if pattern in current_url:
                    incident = self.record_violation(
                        account_id=account_id,
                        account_name=account_name,
                        feature=feature,
                        violation_type="CHECKPOINT",
                        severity="CRITICAL",
                        error_text=f"Phát hiện URL Checkpoint/Xác minh danh tính: {current_url}",
                        url=current_url,
                        context_info=context_info
                    )
                    self.trip_circuit_breaker(account_id, f"Bị Checkpoint/Khóa danh tính: {current_url}")
                    return incident

            modal_texts = []
            try:
                dialogs = page.locator('div[role="dialog"], div[role="alert"], div[role="alertdialog"], .uiLayer').all()
                for d in dialogs[:4]:
                    if d.is_visible():
                        txt = d.inner_text(timeout=500)
                        if txt:
                            modal_texts.append(txt.lower())
            except Exception:
                pass

            if modal_texts:
                scan_corpus = " ".join(modal_texts)
            else:
                try:
                    body_handle = page.locator("body")
                    if body_handle.is_visible():
                        scan_corpus = body_handle.inner_text(timeout=800)[:3000].lower()
                    else:
                        scan_corpus = ""
                except Exception:
                    scan_corpus = ""

            matched_keyword = None
            for kw in META_BLOCKED_KEYWORDS:
                if kw in scan_corpus:
                    matched_keyword = kw
                    break

            if matched_keyword:
                is_action_block = any(b in matched_keyword for b in ["bị chặn", "action blocked", "bị hạn chế", "restricted", "quá nhanh", "going too fast"])
                severity = "HIGH" if is_action_block else "MEDIUM"
                violation_type = "ACTION_BLOCK" if is_action_block else "COMMUNITY_WARNING"

                incident = self.record_violation(
                    account_id=account_id,
                    account_name=account_name,
                    feature=feature,
                    violation_type=violation_type,
                    severity=severity,
                    error_text=f"Meta phản hồi hạn chế tính năng (Khớp từ khóa: '{matched_keyword}')",
                    url=current_url,
                    context_info=context_info
                )
                if severity == "HIGH":
                    self.trip_circuit_breaker(account_id, f"Bị chặn tính năng: {matched_keyword}")
                return incident

            try:
                captcha_frames = page.locator('iframe[src*="captcha"], iframe[src*="recaptcha"], div[id*="captcha"]').all()
                if any(cf.is_visible() for cf in captcha_frames):
                    incident = self.record_violation(
                        account_id=account_id,
                        account_name=account_name,
                        feature=feature,
                        violation_type="CAPTCHA_CHALLENGE",
                        severity="HIGH",
                        error_text="Meta yêu cầu giải mã CAPTCHA / xác minh người máy",
                        url=current_url,
                        context_info=context_info
                    )
                    self.trip_circuit_breaker(account_id, "Xuất hiện CAPTCHA của Meta")
                    return incident
            except Exception:
                pass

        except Exception:
            pass
        return None

    def record_action_start(self, account_id: str, feature: str, content: str = "", delay_seconds: float = 0, account_name: str = "") -> Dict[str, Any]:
        now = time.time()
        st = self._get_account_state(account_id)
        time_since_last = (now - st["last_action_time"]) if st["last_action_time"] > 0 else 999
        st["last_action_time"] = now
        st["recent_actions"].append(now)
        st["recent_actions"] = [t for t in st["recent_actions"] if now - t <= 3600]

        content_dup_score = 0
        if content:
            content_hash = hashlib.md5(content.strip().encode("utf-8")).hexdigest()
            st["recent_contents"].append({"hash": content_hash, "time": now})
            st["recent_contents"] = [c for c in st["recent_contents"] if now - c["time"] <= 7200]
            same_hash_count = sum(1 for c in st["recent_contents"] if c["hash"] == content_hash)
            if same_hash_count >= 3:
                content_dup_score = min(35, same_hash_count * 10)

        risk_score, risk_factors = self.calculate_risk_score(account_id, feature, delay_seconds, time_since_last, content_dup_score)
        st["last_risk_score"] = risk_score

        if risk_score >= 65:
            warning_msg = f"⚠️ CẢNH BÁO NGUY CƠ SỚM ({risk_score}%): Tài khoản '{account_name or account_id}' có rủi ro cao bị Meta hạn chế!"
            if risk_factors:
                warning_msg += f" Nguyên nhân: {', '.join(risk_factors)}."
            self._notify_alert({
                "type": "EARLY_WARNING",
                "account_id": account_id,
                "account_name": account_name,
                "feature": feature,
                "risk_score": risk_score,
                "factors": risk_factors,
                "message": warning_msg,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })

        return {
            "risk_score": risk_score,
            "risk_factors": risk_factors,
            "circuit_tripped": st["circuit_tripped"],
            "trip_reason": st["trip_reason"]
        }

    def record_action_result(self, account_id: str, feature: str, success: bool, error_msg: str = "", account_name: str = ""):
        st = self._get_account_state(account_id)
        if success:
            st["consecutive_errors"] = max(0, st["consecutive_errors"] - 1)
        else:
            st["consecutive_errors"] += 1
            if st["consecutive_errors"] >= 3:
                risk_msg = f"Tài khoản '{account_name or account_id}' gặp {st['consecutive_errors']} lỗi liên tiếp trong '{feature}'. Khuyến nghị tạm dừng!"
                self._notify_alert({
                    "type": "CONSECUTIVE_ERRORS",
                    "account_id": account_id,
                    "account_name": account_name,
                    "feature": feature,
                    "risk_score": 75,
                    "factors": [f"{st['consecutive_errors']} lỗi liên tiếp"],
                    "message": risk_msg,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                if st["consecutive_errors"] >= 5:
                    self.trip_circuit_breaker(account_id, f"Gặp {st['consecutive_errors']} lỗi liên tiếp")

    def calculate_risk_score(self, account_id: str, feature: str, delay_seconds: float, time_since_last: float, content_dup_score: int) -> Tuple[int, List[str]]:
        now = time.time()
        st = self._get_account_state(account_id)
        factors = []
        score = 10

        actions_last_5m = sum(1 for t in st["recent_actions"] if now - t <= 300)
        actions_last_15m = sum(1 for t in st["recent_actions"] if now - t <= 900)

        if actions_last_5m >= 4:
            score += 25
            factors.append(f"Tần suất dồn dập ({actions_last_5m} hành động/5 phút)")
        elif actions_last_5m >= 2:
            score += 10

        if actions_last_15m >= 8:
            score += 20
            factors.append(f"Thực hiện quá nhiều trong 15 phút ({actions_last_15m} lần)")

        safe_cfg = self.safe_presets.get(feature, {})
        safe_min_delay = safe_cfg.get("min_delay", 45)
        if 0 < delay_seconds < 15:
            score += 30
            factors.append(f"Delay cực thấp ({int(delay_seconds)}s < ngưỡng an toàn 15s)")
        elif 0 < delay_seconds < safe_min_delay:
            score += 15
            factors.append(f"Delay thấp ({int(delay_seconds)}s < đề xuất {safe_min_delay}s)")

        if content_dup_score > 0:
            score += content_dup_score
            factors.append("Trùng lặp nội dung liên tiếp (thiếu Spintax)")

        if st["consecutive_errors"] >= 2:
            err_points = min(25, st["consecutive_errors"] * 10)
            score += err_points
            factors.append(f"{st['consecutive_errors']} lần thực hiện thất bại liên tiếp")

        recent_violations = [v for v in self.violations if v.get("account_id") == account_id and (now - v.get("timestamp_epoch", 0) <= 86400)]
        if recent_violations:
            crit_count = sum(1 for v in recent_violations if v.get("severity") in ["CRITICAL", "HIGH"])
            if crit_count > 0:
                score += min(30, crit_count * 15)
                factors.append(f"Có {crit_count} vi phạm/hạn chế trong 24 giờ qua")

        final_score = min(100, max(5, score))
        return final_score, factors

    def trip_circuit_breaker(self, account_id: str, reason: str):
        st = self._get_account_state(account_id)
        st["circuit_tripped"] = True
        st["trip_reason"] = reason
        st["trip_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        alert_data = {
            "type": "CIRCUIT_BREAKER_TRIPPED",
            "account_id": account_id,
            "reason": reason,
            "message": f"🚨 ĐÃ KÍCH HOẠT PHANH AN TOÀN (CIRCUIT BREAKER)! Tự động dừng tiến trình tài khoản '{account_id}' để bảo vệ nick. Lý do: {reason}",
            "timestamp": st["trip_time"]
        }
        self._notify_alert(alert_data)

    def reset_circuit_breaker(self, account_id: str):
        st = self._get_account_state(account_id)
        st["circuit_tripped"] = False
        st["trip_reason"] = ""
        st["trip_time"] = None
        st["consecutive_errors"] = 0

    def is_circuit_tripped(self, account_id: str) -> Tuple[bool, str]:
        st = self._get_account_state(account_id)
        return st["circuit_tripped"], st["trip_reason"]

    def record_violation(self, account_id: str, account_name: str, feature: str, violation_type: str, severity: str, error_text: str, url: str = "", context_info: str = "") -> Dict[str, Any]:
        now_dt = datetime.now()
        item_id = f"viol_{int(now_dt.timestamp())}_{hashlib.md5((account_id + str(time.time())).encode()).hexdigest()[:6]}"
        
        incident = {
            "id": item_id,
            "timestamp": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp_epoch": int(now_dt.timestamp()),
            "hour_of_day": now_dt.hour,
            "account_id": account_id,
            "account_name": account_name or account_id,
            "feature": feature,
            "violation_type": violation_type,
            "severity": severity,
            "error_text": error_text,
            "url": url,
            "context_info": context_info,
            "safe_action_taken": "Tự động kích hoạt phanh an toàn & cảnh báo người dùng"
        }
        
        self.violations.insert(0, incident)
        self.violations = self.violations[:300]
        self._save_violations()

        self._notify_alert({
            "type": "VIOLATION_RECORDED",
            "violation": incident,
            "message": f"🚨 [{severity}] Phát hiện phản ứng Meta: {error_text} (Tài khoản: {account_name or account_id})",
            "timestamp": incident["timestamp"]
        })
        return incident

    def _notify_alert(self, alert_data: Dict[str, Any]):
        if self.alert_callback:
            try:
                self.alert_callback(alert_data)
            except Exception as e:
                print(f"[MetaSentinel] Lỗi alert_callback: {e}")
        if self.log_callback:
            try:
                lvl = "error" if alert_data.get("type") in ["VIOLATION_RECORDED", "CIRCUIT_BREAKER_TRIPPED"] else "warning"
                self.log_callback(alert_data.get("message", ""), lvl, alert_data.get("account_id"))
            except Exception:
                pass

    def get_telemetry_summary(self) -> Dict[str, Any]:
        total_violations = len(self.violations)
        now_epoch = time.time()
        
        violations_24h = [v for v in self.violations if now_epoch - v.get("timestamp_epoch", 0) <= 86400]
        critical_count = sum(1 for v in self.violations if v.get("severity") == "CRITICAL")
        action_blocks = sum(1 for v in self.violations if v.get("violation_type") == "ACTION_BLOCK")
        
        by_feature = {}
        for v in self.violations:
            feat = v.get("feature", "other")
            by_feature[feat] = by_feature.get(feat, 0) + 1

        hourly_distribution = {h: 0 for h in range(24)}
        for v in self.violations:
            h = v.get("hour_of_day", 0)
            hourly_distribution[h] = hourly_distribution.get(h, 0) + 1

        health_index = 100
        if len(violations_24h) > 0:
            health_index = max(20, 100 - (len(violations_24h) * 15) - (critical_count * 20))

        risk_level = "LOW"
        if health_index < 50:
            risk_level = "CRITICAL"
        elif health_index < 75:
            risk_level = "HIGH"
        elif health_index < 90:
            risk_level = "MEDIUM"

        return {
            "health_index": health_index,
            "risk_level": risk_level,
            "total_violations": total_violations,
            "violations_24h": len(violations_24h),
            "critical_count": critical_count,
            "action_blocks": action_blocks,
            "by_feature": by_feature,
            "hourly_distribution": hourly_distribution,
            "recent_violations": self.violations[:20],
            "active_trips": {aid: st for aid, st in self.account_state.items() if st.get("circuit_tripped")}
        }

    def generate_recommendations(self) -> Dict[str, Any]:
        summary = self.get_telemetry_summary()
        by_feature = summary["by_feature"]
        hourly_dist = summary["hourly_distribution"]
        
        sorted_hours = sorted(hourly_dist.items(), key=lambda x: x[1], reverse=True)
        risky_hours = [h for h, c in sorted_hours[:3] if c > 0]
        
        recommendations_list = []
        custom_presets = self.safe_presets.copy()

        for feat, count in by_feature.items():
            if count >= 1:
                base_cfg = custom_presets.get(feat, DEFAULT_SAFE_CONFIGS.get(feat, {}))
                new_min = int(base_cfg.get("min_delay", 45) * 1.35)
                new_max = int(base_cfg.get("max_delay", 90) * 1.4)
                new_cap = max(10, int(base_cfg.get("daily_cap", 30) * 0.75))
                
                custom_presets[feat] = {
                    **base_cfg,
                    "min_delay": new_min,
                    "max_delay": new_max,
                    "daily_cap": new_cap,
                    "cooldown_duration_minutes": 25
                }
                
                recommendations_list.append({
                    "target": feat,
                    "type": "DELAY_AND_QUOTA",
                    "title": f"Tăng Delay & Hạ Hạn Mức '{feat}'",
                    "reason": f"Tính năng này đã ghi nhận {count} lần vi phạm/hạn chế.",
                    "suggestion": f"Nâng min_delay lên {new_min}s, max_delay lên {new_max}s và giảm quota xuống {new_cap} hành động/ngày.",
                    "applied_values": custom_presets[feat]
                })

        if risky_hours:
            hours_str = ", ".join([f"{h:02d}:00" for h in risky_hours])
            recommendations_list.append({
                "target": "all",
                "type": "TIME_WINDOW",
                "title": "Tránh chạy Auto vào các khung giờ cao điểm Meta quét",
                "reason": f"Lịch sử vi phạm tập trung nhiều nhất vào các khung giờ: {hours_str}.",
                "suggestion": f"Nên bật 'Khung giờ chạy' và tránh kích hoạt tác vụ nặng vào khung giờ {hours_str}.",
                "applied_values": {"risky_hours": risky_hours}
            })

        recommendations_list.append({
            "target": "content",
            "type": "SPINTAX_DIVERSITY",
            "title": "Gia tăng tính đa dạng Spintax & sử dụng AI Paraphrase",
            "reason": "Meta thường chặn tính năng khi phát hiện cùng nội dung lặp lại trên nhiều nhóm.",
            "suggestion": "Luôn dùng Spintax có tối thiểu 4-6 nhánh biến thể hoặc dùng AI Content Studio tạo sẵn 20 biến thể bài viết.",
            "applied_values": {"min_diversity": 75}
        })

        return {
            "summary": summary,
            "risky_hours": risky_hours,
            "recommendations": recommendations_list,
            "recommended_presets": custom_presets
        }

    def apply_recommended_presets(self, custom_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if custom_values:
            self.safe_presets.update(custom_values)
        else:
            recs = self.generate_recommendations()
            self.safe_presets = recs["recommended_presets"]
        self._save_safe_presets()
        return {"success": True, "presets": self.safe_presets}

meta_sentinel = MetaSentinelCore()
