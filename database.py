"""
Ma'lumotlar bazasi — JSON fayl asosida
Thread-safe, xavfsiz
"""

import json
import os
import uuid
import threading
import logging
from typing import Optional
from datetime import datetime

logger = logging.getLogger("database")
_lock = threading.Lock()


class Database:
    FILE = "data.json"

    def __init__(self, path: Optional[str] = None):
        if path:
            self.FILE = path
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(self.FILE):
            self._write({"monitors": {}, "users": {}})

    def _read(self) -> dict:
        try:
            with open(self.FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"DB read xato: {e}")
            data = {}
        data.setdefault("monitors", {})
        data.setdefault("users", {})
        return data

    def _write(self, data: dict):
        try:
            # Avval tmp faylga yoz, keyin rename — atomic write
            tmp = self.FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.FILE)
        except Exception as e:
            logger.error(f"DB write xato: {e}")

    # ─── Monitors ───────────────────────────────────────────────────────────────
    def save_monitor(self, uid: int, monitor: dict) -> str:
        mid = uuid.uuid4().hex[:8]
        with _lock:
            data = self._read()
            data["monitors"][mid] = {**monitor, "id": mid, "uid": uid}
            self._write(data)
        return mid

    def get_active_monitors(self, uid: int) -> list:
        with _lock:
            data = self._read()
        return [
            m for m in data["monitors"].values()
            if m.get("uid") == uid and m.get("active")
        ]

    def get_all_active_monitors(self) -> list:
        """Barcha foydalanuvchilarning faol kuzatuvlari (restartdan keyin tiklash uchun)"""
        with _lock:
            data = self._read()
        return [m for m in data["monitors"].values() if m.get("active")]

    def deactivate_expired(self, today: str) -> list:
        """Sanasi o'tib ketgan faol kuzatuvlarni avtomatik o'chirish.
        `today` — YYYY-MM-DD; o'chirilgan kuzatuvlar ro'yxatini qaytaradi."""
        removed = []
        with _lock:
            data = self._read()
            for m in data["monitors"].values():
                if m.get("active") and m.get("date") and m["date"] < today:
                    m["active"] = False
                    m["stopped_at"] = datetime.now().isoformat()
                    m["stop_reason"] = "expired"
                    removed.append(dict(m))
            if removed:
                self._write(data)
        return removed

    def is_active(self, mid: str) -> bool:
        with _lock:
            data = self._read()
        return data["monitors"].get(mid, {}).get("active", False)

    def deactivate(self, mid: str):
        with _lock:
            data = self._read()
            if mid in data["monitors"]:
                data["monitors"][mid]["active"] = False
                data["monitors"][mid]["stopped_at"] = datetime.now().isoformat()
                self._write(data)

    def deactivate_for_user(self, uid: int, mid: str) -> bool:
        with _lock:
            data = self._read()
            m = data["monitors"].get(mid)
            if m and m.get("uid") == uid and m.get("active"):
                m["active"] = False
                m["stopped_at"] = datetime.now().isoformat()
                self._write(data)
                return True
        return False

    def deactivate_all(self, uid: int) -> int:
        count = 0
        with _lock:
            data = self._read()
            for m in data["monitors"].values():
                if m.get("uid") == uid and m.get("active"):
                    m["active"] = False
                    m["stopped_at"] = datetime.now().isoformat()
                    count += 1
            if count:
                self._write(data)
        return count

    def increment_check(self, mid: str):
        with _lock:
            data = self._read()
            if mid in data["monitors"]:
                data["monitors"][mid]["check_count"] = (
                    data["monitors"][mid].get("check_count", 0) + 1
                )
                data["monitors"][mid]["last_check"] = datetime.now().isoformat()
                self._write(data)

    def update_monitor_field(self, uid: int, mid: str, field: str, value):
        """Monitor bitta maydonini yangilash"""
        with _lock:
            data = self._read()
            m = data["monitors"].get(mid)
            if m and m.get("uid") == uid:
                m[field] = value
                m["updated_at"] = datetime.now().isoformat()
                self._write(data)
                return True
        return False

    # ─── Users (admin tomonidan qo'shilgan foydalanuvchilar) ───────────────────────
    def add_user(self, tid: int, added_by: int, username: str = "", first_name: str = "") -> bool:
        """Foydalanuvchini ruxsat etilganlar ro'yxatiga qo'shish.
        Qaytadi: True — yangi qo'shildi, False — allaqachon bor edi (faollashtirildi)."""
        with _lock:
            data = self._read()
            key = str(tid)
            existing = data["users"].get(key)
            if existing:
                existing["active"] = True
                existing["updated_at"] = datetime.now().isoformat()
                if username:
                    existing["username"] = username
                if first_name:
                    existing["first_name"] = first_name
                self._write(data)
                return False
            data["users"][key] = {
                "tid": tid,
                "username": username,
                "first_name": first_name,
                "added_by": added_by,
                "added_at": datetime.now().isoformat(),
                "active": True,
                "last_seen": None,
                "action_count": 0,
            }
            self._write(data)
            return True

    def remove_user(self, tid: int) -> bool:
        """Foydalanuvchini botdan o'chirish (ruxsatini bekor qilish)"""
        with _lock:
            data = self._read()
            key = str(tid)
            u = data["users"].get(key)
            if u and u.get("active"):
                u["active"] = False
                u["removed_at"] = datetime.now().isoformat()
                self._write(data)
                return True
        return False

    def is_added_user(self, tid: int) -> bool:
        with _lock:
            data = self._read()
        u = data["users"].get(str(tid))
        return bool(u and u.get("active"))

    def get_users(self, active_only: bool = True) -> list:
        with _lock:
            data = self._read()
        users = list(data["users"].values())
        if active_only:
            users = [u for u in users if u.get("active")]
        return sorted(users, key=lambda u: u.get("added_at") or "", reverse=True)

    def get_user(self, tid: int) -> Optional[dict]:
        with _lock:
            data = self._read()
        return data["users"].get(str(tid))

    def set_user_phone(self, tid: int, phone: str) -> bool:
        """Foydalanuvchi telefon raqamini saqlash (faqat ro'yxatda bo'lsa)"""
        with _lock:
            data = self._read()
            u = data["users"].get(str(tid))
            if u:
                u["phone"] = phone
                self._write(data)
                return True
        return False

    def touch_user_activity(self, tid: int):
        """Foydalanuvchi faolligini qayd qilish (oxirgi faollik, amallar soni)"""
        with _lock:
            data = self._read()
            key = str(tid)
            if key in data["users"]:
                data["users"][key]["last_seen"] = datetime.now().isoformat()
                data["users"][key]["action_count"] = data["users"][key].get("action_count", 0) + 1
                self._write(data)

    def get_user_monitor_stats(self, tid: int) -> dict:
        """Foydalanuvchining kuzatuvlar bo'yicha statistikasi"""
        with _lock:
            data = self._read()
        monitors = [m for m in data["monitors"].values() if m.get("uid") == tid]
        active = [m for m in monitors if m.get("active")]
        total_checks = sum(m.get("check_count", 0) for m in monitors)
        return {
            "total": len(monitors),
            "active": len(active),
            "total_checks": total_checks,
        }
