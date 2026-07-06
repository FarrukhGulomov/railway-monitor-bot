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

    # Admin huquqiga ega Telegram ID lar (Railway "Variables" bo'limida beriladi)
    # Admin /addUser va /addUsers orqali foydalanuvchi qo'sha oladi, /users orqali
    # ularning faoliyatini kuzatadi va botdan o'chira oladi. Bir nechta: 111,222
    ADMIN_IDS: list[int] = [
        int(x.strip())
        for x in os.getenv("ADMIN_IDS", "").split(",")
        if x.strip().isdigit()
    ]

    # Ma'lumotlar papkasi — data.json shu yerda saqlanadi.
    # Railway'da Volume ulang (masalan /data) va DATA_DIR=/data qo'ying,
    # aks holda har redeploy'da foydalanuvchilar va kuzatuvlar o'chib ketadi!
    DATA_DIR: str = os.getenv("DATA_DIR", ".").strip() or "."

    # Monitoring tekshirish oralig'i (soniya), min 30
    CHECK_INTERVAL: int = max(10, int(os.getenv("CHECK_INTERVAL", "60")))

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
