"""
Konfiguratsiya — environment variables dan o'qiladi
"""

import logging
import os
from dotenv import load_dotenv

load_dotenv()

_log = logging.getLogger("config")


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
        """Ishga tushishdan oldin barcha kritik sozlamalarni tekshiradi.

        XAVFSIZLIK QOIDASI: kirish nazorati konfiguratsiyasi aniqlanmagan
        holatda ISHGA TUSHMASLIGI kerak. ADMIN_IDS bo'sh bo'lsa avval bot
        "hamma ruxsatli" rejimida ochiq qolardi — bu production uchun xavfli
        standart holat, shuning uchun endi majburiy: yo'q bo'lsa startup
        to'xtaydi, jim tarzda ochiq bot bo'lib qolmaydi."""
        errors = []

        if not cls.BOT_TOKEN:
            errors.append(
                "BOT_TOKEN topilmadi — .env yoki Railway Variables'ga "
                "BOT_TOKEN=<BotFather tokeni> qo'shing."
            )
        elif len(cls.BOT_TOKEN) < 40:
            errors.append("BOT_TOKEN noto'g'ri ko'rinadi (juda qisqa).")

        if not cls.ADMIN_IDS:
            errors.append(
                "ADMIN_IDS topilmadi — bu MAJBURIY. Kamida bitta admin "
                "Telegram ID siz bot kirish nazoratini aniqlay olmaydi va "
                "xavfsiz ishga tushmaydi. .env yoki Railway Variables'ga "
                "ADMIN_IDS=<sizning_telegram_id> qo'shing."
            )

        # CHECK_INTERVAL klass atributida allaqachon min 10s ga cheklangan;
        # bu tekshiruv kelajakda o'sha cheklov olib tashlansa ham himoya beradi.
        if cls.CHECK_INTERVAL < 10:
            errors.append(
                f"CHECK_INTERVAL juda kichik ({cls.CHECK_INTERVAL}s) — "
                "kamida 10 soniya bo'lishi kerak (railway.uz'ga bosim tushirmaslik uchun)."
            )

        if not (1 <= cls.MAX_MONITORS_PER_USER <= 50):
            errors.append(
                f"MAX_MONITORS_PER_USER noto'g'ri ({cls.MAX_MONITORS_PER_USER}) — "
                "1 dan 50 gacha bo'lishi kerak."
            )

        data_dir = cls.DATA_DIR or "."
        try:
            os.makedirs(data_dir, exist_ok=True)
            probe = os.path.join(data_dir, ".write_test")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
        except Exception as e:
            errors.append(
                f"DATA_DIR ('{data_dir}') yozib bo'lmadi: {e}. "
                "Railway'da Volume ulanganini va mount path to'g'ri "
                "ekanini tekshiring."
            )

        if errors:
            raise ValueError(
                "❌ Konfiguratsiya xatolari — bot ishga tushmaydi:\n- "
                + "\n- ".join(errors)
            )

        if not os.getenv("TZ"):
            _log.warning(
                "⚠️ TZ o'zgaruvchisi sozlanmagan — server UTC vaqtida ishlaydi, "
                "kalendar va 'sana/vaqt o'tdi' tekshiruvlari O'zbekiston vaqtidan "
                "(UTC+5) farq qiladi. Railway Variables'ga TZ=Asia/Tashkent qo'shish "
                "tavsiya etiladi."
            )
