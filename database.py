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

    def __init__(self):
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(self.FILE):
            self._write({"monitors": {}})

    def _read(self) -> dict:
        try:
            with open(self.FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"DB read xato: {e}")
            return {"monitors": {}}

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
