"""
Konfiguratsiya — environment variables dan o'qiladi
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Majburiy ────────────────────────────────────────────────────────────────
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # ── Ixtiyoriy ───────────────────────────────────────────────────────────────
    # Faqat shu Telegram ID larga ruxsat (bo'sh = hamma ruxsatli)
    ALLOWED_USERS: list[int] = [
        int(x.strip())
        for x in os.getenv("ALLOWED_USERS", "").split(",")
        if x.strip().isdigit()
    ]

    # Monitoring tekshirish oralig'i (soniya), min 30
    CHECK_INTERVAL: int = max(20, int(os.getenv("CHECK_INTERVAL", "60")))

    # Bir foydalanuvchida max monitoring soni
    MAX_MONITORS_PER_USER: int = int(os.getenv("MAX_MONITORS_PER_USER", "3"))

    @classmethod
    def validate(cls):
        if not cls.BOT_TOKEN:
            raise ValueError(
                "BOT_TOKEN topilmadi!\n"
                ".env faylga BOT_TOKEN=... qo'shing."
            )
        if len(cls.BOT_TOKEN) < 40:
            raise ValueError("BOT_TOKEN noto'g'ri ko'rinadi.")
