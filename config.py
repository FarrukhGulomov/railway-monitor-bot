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

    # ── Railway.uz so'rov rejalashtiruvchisi (scheduler) ──────────────────────────
    # Bir vaqtda railway.uz'ga nechta MUSTAQIL (turli marshrut/sana) so'rov
    # "parvoz holatida" bo'lishi mumkinligi. 1 = eski xatti-harakat (to'liq
    # ketma-ket). Standart 2 — bitta sekin/retry qilayotgan marshrut boshqa
    # marshrutlarni butunlay to'smasligi uchun, lekin saytga haddan tashqari
    # yuklama bermaslik uchun hali ham juda kichik.
    RAILWAY_MAX_CONCURRENCY: int = max(1, int(os.getenv("RAILWAY_MAX_CONCURRENCY", "2")))

    # Haqiqiy HTTP so'rovlar orasidagi minimal oraliq (soniya) — barcha
    # marshrutlar uchun UMUMIY (global) chegara. Bu railway.uz'ga nisbatan
    # "xushmuomalalik" chizig'i — konkurrensiya oshsa ham so'rov tezligi
    # shu chegaradan oshmaydi.
    RAILWAY_MIN_REQUEST_INTERVAL: float = float(os.getenv("RAILWAY_MIN_REQUEST_INTERVAL", "5.0"))

    # Bir xil marshrut+sana so'rovlarini qisqa vaqt oynasida birlashtiruvchi
    # (single-flight) kesh muddati (soniya). CHECK_INTERVAL'dan ancha kichik
    # bo'lishi SHART — aks holda bitta monitorning o'z navbatdagi tekshiruvi
    # eskirgan keshlangan natija bilan "aldanib" qolishi mumkin (aniqlash
    # kechikishini sun'iy oshiradi). Shu sabab bot.py buni CHECK_INTERVAL
    # bilan taqqoslab yana ham qisqartiradi (quyida _effective_cache_ttl()).
    SEARCH_CACHE_TTL: float = float(os.getenv("SEARCH_CACHE_TTL", "8.0"))

    @classmethod
    def effective_search_cache_ttl(cls) -> float:
        """Kesh muddati — sozlangan qiymat bilan CHECK_INTERVAL yarmidan
        kichigi. Shunda keshlash HECH QACHON monitorning o'z tsikli bo'yicha
        aniqlash kechikishiga (detection latency) sezilarli ta'sir qilmaydi,
        faqat turli monitorlarning deyarli bir vaqtdagi bir xil so'rovlarini
        birlashtiradi (railway.uz'ga ortiqcha zarba bermaslik uchun)."""
        return max(1.0, min(cls.SEARCH_CACHE_TTL, cls.CHECK_INTERVAL / 2))

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

        if not (1 <= cls.RAILWAY_MAX_CONCURRENCY <= 5):
            errors.append(
                f"RAILWAY_MAX_CONCURRENCY noto'g'ri ({cls.RAILWAY_MAX_CONCURRENCY}) — "
                "1 dan 5 gacha bo'lishi kerak (railway.uz'ga haddan tashqari "
                "yuklama bermaslik uchun yuqori chegara ataylab past qo'yilgan)."
            )

        if cls.RAILWAY_MIN_REQUEST_INTERVAL < 1.0:
            errors.append(
                f"RAILWAY_MIN_REQUEST_INTERVAL juda kichik ({cls.RAILWAY_MIN_REQUEST_INTERVAL}s) — "
                "kamida 1.0 soniya bo'lishi kerak (railway.uz'ga xushmuomalalik uchun)."
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
