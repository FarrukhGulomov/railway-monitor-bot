"""
Xavfsizlik qatlami
- Faqat ruxsat etilgan foydalanuvchilar
- Rate limiting
- Input sanitization
"""

import time
import re
import logging
from collections import defaultdict
from config import Config

logger = logging.getLogger("security")


class SecurityMiddleware:
    def __init__(self):
        # uid -> [timestamp, timestamp, ...]
        self._requests: dict[int, list] = defaultdict(list)
        self._window = 60       # soniya
        self._max_requests = 20  # oynada max so'rov

    def is_allowed(self, uid: int) -> bool:
        """Foydalanuvchiga ruxsat bormi?"""
        if not Config.ALLOWED_USERS:
            return True  # Ro'yxat bo'sh = hamma ruxsatli
        return uid in Config.ALLOWED_USERS

    def is_rate_limited(self, uid: int) -> bool:
        """Juda ko'p so'rov yubormoqdami?"""
        now = time.time()
        window_start = now - self._window

        # Eski so'rovlarni tozalash
        self._requests[uid] = [
            t for t in self._requests[uid] if t > window_start
        ]

        if len(self._requests[uid]) >= self._max_requests:
            logger.warning(f"Rate limit: uid={uid}")
            return True

        self._requests[uid].append(now)
        return False

    @staticmethod
    def sanitize_text(text: str, max_len: int = 200) -> str:
        """Kirish matnini tozalash"""
        if not text:
            return ""
        # Faqat ruxsat etilgan belgilar
        text = text.strip()[:max_len]
        return text

    @staticmethod
    def is_valid_date(date_str: str) -> bool:
        """YYYY-MM-DD format tekshirish"""
        return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", date_str))

    @staticmethod
    def is_valid_price(price_str: str) -> bool:
        """Narx faqat raqam"""
        cleaned = price_str.replace(" ", "").replace(",", "")
        return cleaned.isdigit()
