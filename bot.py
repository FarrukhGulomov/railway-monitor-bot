"""
🚆 Railway Monitor Bot — v2
"""

import asyncio
import json
import logging
import sys
import os
import time
import fcntl
import calendar
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Optional

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)
from telegram.helpers import escape_markdown
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler,
)

from config import Config
from railway_client import RailwayClient, SearchResult
from database import Database
from security import SecurityMiddleware
from metrics import metrics

# ─── Logging ────────────────────────────────────────────────────────────────────
# Railway'ning o'z log ko'ruvchisi stdout'ni o'qiydi — bu doim ishlaydi.
# Fayl logi (bot.log, /logs buyrug'i uchun) endi RotatingFileHandler bilan —
# cheksiz o'sib, konteyner diskini to'ldirib yubormasligi uchun (10MB x 5 fayl).
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            "bot.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("railway_bot")

# MUHIM (xavfsizlik): httpx har bir Telegram so'rovining TO'LIQ URL'ini INFO
# darajasida log qiladi, URL ichida esa bot tokeni bor
# (https://api.telegram.org/bot<TOKEN>/sendMessage) — bu token Railway loglariga
# va admin /logs buyrug'iga tushib qolardi. Shu sabab ularni WARNING'ga tushiramiz.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# ─── States ─────────────────────────────────────────────────────────────────────
(
    WAIT_FROM, WAIT_TO, WAIT_DATE,
    WAIT_CAR_TYPE, WAIT_TIME_RANGE, WAIT_MAX_PRICE, WAIT_MIN_SEATS,
) = range(7)

EDIT_MENU, EDIT_FIELD, EDIT_VALUE = range(7, 10)

# ─── Konstantalar ───────────────────────────────────────────────────────────────
STATIONS = {
    "🏙 Toshkent":   "2900000",
    "🕌 Samarqand":  "2900680",
    "🕌 Buxoro":     "2900800",
    "🏔 Andijon":    "2900100",
    "🌿 Namangan":   "2900200",
    "🌾 Farg'ona":   "2900210",
    "🌄 Qarshi":     "2900900",
    "🌊 Nukus":      "2900350",
    "🌿 Urganch":    "2900300",
    "☀️ Termiz":     "2901100",
}
STATIONS_REV = {v: k for k, v in STATIONS.items()}

CAR_TYPES = {
    "platskar":  "🪑 Platskart",
    "coupe":     "🛏 Kupe",
    "sv":        "💺 SV / Lyuks",
    "afrosiyob": "🚄 Afrosiyob",
    "sharq":     "🚅 Sharq",
    "any":       "🔀 Barchasi",
}

CAR_TYPE_KEYWORDS = {
    "platskar":  ["o'rindiq", "ўриндиқ", "platskart", "plaskart", "плацкарт", "seat"],
    "coupe":     ["yotoq", "ётоқ", "kupe", "купе", "compart"],
    "sv":        ["sv", "св", "lyuks", "люкс", "vip", "lux"],
    "afrosiyob": [],
    "sharq":     [],
    "any":       [],
}

# Brand orqali filtrlanadigan turlar (vagon type emas, poyezd brendi tekshiriladi)
BRAND_FILTERS = {
    "afrosiyob": ["afrosiyob", "афросиёб"],
    "sharq":     ["sharq", "шарк", "шарқ"],
}

# uid -> set("shu turdagi ctype_raw qiymat allaqachon 'unknown' deb log qilindi")
# — har bir yangi, tanib bo'lmaydigan vagon turi yozuvini faqat BIR MARTA log
# qilish uchun (aks holda bir xil anomaliya har tekshiruvda qayta-qayta
# loglarni to'ldirib yuboradi).
_logged_unknown_car_types: set = set()


def normalize_car_type(raw: str) -> str:
    """Railway.uz'dan kelgan turli yozilishdagi (lotin/kirill, turli
    orfografiya) vagon turi matnini bitta aniq kategoriyaga keltiradi:
    'platskar' | 'coupe' | 'sv' | 'unknown'.

    MUHIM: bu funksiya faqat DIAGNOSTIKA/KUZATUV (anomaliyalarni log qilish,
    kelajakda yangi yozilishlarni ko'rish) uchun ishlatiladi. `_find_all_trains`
    ichidagi haqiqiy FILTRLASH mantig'i ataylab o'zgartirilmagan (har bir
    so'ralgan car_type o'z keyword ro'yxati bo'yicha mustaqil tekshiriladi) —
    bu orqali mavjud, ishlab turgan filtrlash xatti-harakati 100% saqlanadi.

    Noma'lum qiymat jim tashlab yuborilmaydi — aniq 'unknown' qaytariladi,
    shunda chaqiruvchi buni ko'rinadigan tarzda log qila oladi."""
    if not raw or not isinstance(raw, str):
        return "unknown"
    text = raw.strip().lower()
    if not text:
        return "unknown"
    for category, keywords in CAR_TYPE_KEYWORDS.items():
        if category == "any" or not keywords:
            continue
        if any(kw in text for kw in keywords):
            return category
    return "unknown"


def _log_unknown_car_type_once(raw: str, train_number: str = ""):
    """Tanib bo'lmaydigan vagon turi yozuvini (railway.uz yangi sxema
    qo'shgan bo'lishi mumkin) bir marta ko'rinadigan tarzda log qiladi va
    hisoblagichni oshiradi — jim yo'qolib ketmasligi uchun."""
    key = (raw or "").strip().lower()
    if key in _logged_unknown_car_types:
        return
    _logged_unknown_car_types.add(key)
    metrics.incr("schema_anomalies_unknown_car_type_total")
    logger.warning(
        f"Noma'lum vagon turi yozilishi uchradi: {raw!r} "
        f"(poyezd {train_number or '?'}) — normalize_car_type'ga yangi "
        "keyword qo'shish kerak bo'lishi mumkin."
    )


TIME_RANGES = {
    "any":     ("00:00", "23:59", "🕐 Istalgan vaqt"),
    "morning": ("06:00", "11:59", "🌅 Ertalab 06:00–12:00"),
    "day":     ("12:00", "17:59", "☀️ Kunduz 12:00–18:00"),
    "evening": ("18:00", "23:59", "🌆 Kechqurun 18:00–00:00"),
    "night":   ("00:00", "05:59", "🌙 Tunda 00:00–06:00"),
    "custom":  (None, None,       "✏️ O'zim kiritaman"),
}

LOCK_FILE = "/tmp/railway_bot.lock"

db = Database(path=os.path.join(Config.DATA_DIR, "data.json"))
security = SecurityMiddleware()

# ─── Markaziy Railway API koordinatori (scheduler) ───────────────────────────────
# Arxitektura: monitor tasklari → marshrut/sana bo'yicha agregatsiya →
# deduplikatsiyalangan qidiruv navbati → bounded concurrency → global pacing
# (railway_client.py ichida) → ulashilgan single-flight kesh.
#
# - Har bir monitor o'zining alohida RailwayClient'ini OCHMAYDI — bitta
#   umumiy client (cookie/XSRF sessiyasi bo'lishiladi).
# - Bir xil marshrut+sana so'rovlari qisqa oynada (odatda CHECK_INTERVAL'dan
#   ancha qisqa) bitta natijani bo'lishadi (single-flight + kesh) — railway.uz
#   ortiqcha so'rov olmaydi.
# - `_search_semaphore` bir vaqtda nechta MUSTAQIL (turli marshrut/sana)
#   qidiruv "parvozda" bo'lishi mumkinligini chegaralaydi (RAILWAY_MAX_CONCURRENCY,
#   standart 2). asyncio.Semaphore ichki navbati FIFO — shuning uchun hech bir
#   marshrut doimiy ravishda boshqalaridan keyinga surilib qolmaydi (fairness).
# - Haqiqiy tarmoq so'rovlari esa railway_client.py ichida hali ham umumiy
#   pacing bilan tartiblanadi (bir vaqtning o'zida socket darajasida bir nechta
#   so'rov yubormaymiz — ulashilgan sessiya xavfsizligi uchun), LEKIN bitta
#   marshrutning retry/backoff KUTISHI endi boshqalarni to'smaydi (railway_client.py
#   ichidagi _pacing_lock/_session_lock ajratilishiga qarang).
_railway_client: Optional[RailwayClient] = None
_railway_client_lock = asyncio.Lock()

_search_cache: dict = {}      # (from,to,date) -> (monotonic_ts, SearchResult)
_search_inflight: dict = {}   # (from,to,date) -> asyncio.Future
_search_coord_lock = asyncio.Lock()
_search_semaphore: Optional[asyncio.Semaphore] = None  # lazy — event loop ichida yaratiladi

# Barcha faol monitor asyncio tasklari — graceful shutdown uchun kuzatiladi
_monitor_tasks: dict = {}  # mid -> asyncio.Task


async def _get_railway_client() -> RailwayClient:
    global _railway_client
    if _railway_client is None:
        async with _railway_client_lock:
            if _railway_client is None:
                _railway_client = await asyncio.to_thread(RailwayClient)
    return _railway_client


def _get_search_semaphore() -> asyncio.Semaphore:
    global _search_semaphore
    if _search_semaphore is None:
        _search_semaphore = asyncio.Semaphore(Config.RAILWAY_MAX_CONCURRENCY)
    return _search_semaphore


async def _shared_search(client: RailwayClient, from_code: str, to_code: str, date: str) -> SearchResult:
    """Bir nechta monitor bir xil marshrut+sanani deyarli bir vaqtda so'rasa,
    faqat bitta haqiqiy HTTP so'rov yuboriladi, qolganlari natijani bo'lishadi
    (single-flight). Kesh muddati CHECK_INTERVAL'ga nisbatan chegaralangan
    (Config.effective_search_cache_ttl) — shuning uchun hech qachon bitta
    monitorning o'z navbatdagi tekshiruvini sun'iy ravishda kechiktirmaydi,
    faqat deyarli bir vaqtdagi takroriy so'rovlarni birlashtiradi."""
    key = (from_code, to_code, date)
    now = time.monotonic()
    ttl = Config.effective_search_cache_ttl()

    async with _search_coord_lock:
        cached = _search_cache.get(key)
        if cached and now - cached[0] < ttl:
            metrics.incr("search_cache_hits_total")
            return cached[1]
        fut = _search_inflight.get(key)
        owner = fut is None
        if owner:
            fut = asyncio.get_event_loop().create_future()
            _search_inflight[key] = fut

    if not owner:
        # Boshqa monitor allaqachon xuddi shu marshrut+sanani so'ramoqda —
        # alohida HTTP so'rov yubormasdan, uning natijasini kutib olamiz.
        metrics.incr("search_singleflight_joins_total")
        return await fut

    queue_wait_start = time.monotonic()
    sem = _get_search_semaphore()
    async with sem:
        queue_wait = time.monotonic() - queue_wait_start
        if queue_wait > 1.0:
            # Navbatda sezilarli kutish bo'lsa — diagnostika uchun log qilamiz
            # ("bot nega sekin ishladi" degan savolga javob berish uchun).
            logger.info(
                f"Qidiruv navbatida {queue_wait:.2f}s kutildi "
                f"({from_code}→{to_code} {date}, RAILWAY_MAX_CONCURRENCY={Config.RAILWAY_MAX_CONCURRENCY})"
            )
        metrics.set_gauge("search_queue_wait_seconds", queue_wait)
        metrics.incr("search_dispatched_total")

        try:
            result = await asyncio.to_thread(client.search_trains, from_code, to_code, date)
        except Exception as e:
            logger.error(f"Kutilmagan xato search_trains chaqiruvida: {e}")
            result = SearchResult(False, [], f"exception: {e}")

    metrics.set_gauge("search_upstream_latency_seconds", result.latency)

    async with _search_coord_lock:
        _search_cache[key] = (time.monotonic(), result)
        _search_inflight.pop(key, None)
    if not fut.done():
        fut.set_result(result)
    return result


def _spawn_monitor(uid: int, mid: str, monitor: dict, app) -> "asyncio.Task":
    task = asyncio.create_task(_monitor_loop(uid, mid, monitor, app))
    _monitor_tasks[mid] = task
    task.add_done_callback(lambda t, mid=mid: _monitor_tasks.pop(mid, None))
    return task


async def _shutdown_monitors(app):
    """Bot to'xtatilganda barcha monitor tasklarini tartibli yakunlash."""
    tasks = list(_monitor_tasks.values())
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    logger.info(f"🛑 {len(tasks)} ta monitor vazifasi to'xtatildi (shutdown)")


def is_admin(uid: int) -> bool:
    """Foydalanuvchi admin (Railway ADMIN_IDS o'zgaruvchisidan) mi?
    ADMIN_IDS bo'sh bo'lsa (Config.validate() buni ishga tushishda taqiqlaydi,
    lekin himoya sifatida) — hech kim admin emas, FAIL-CLOSED."""
    return uid in Config.ADMIN_IDS


def has_access(uid: int) -> bool:
    """Foydalanuvchi botdan foydalanishga ruxsatlimi?

    XAVFSIZLIK QOIDASI (fail-closed): kirish konfiguratsiyasi aniqlanmagan
    yoki bo'sh bo'lgani uchun HECH QACHON `True` qaytarilmaydi. Ruxsat faqat
    quyidagi aniq manbalardan birida bo'lishi kerak:
    - admin (ADMIN_IDS);
    - admin tomonidan /addUser(s) orqali qo'shilgan (DB, active);
    - ALLOWED_USERS statik ro'yxatida (ixtiyoriy qo'shimcha whitelisting).
    Boshqa har qanday holatda — ruxsat yo'q."""
    if is_admin(uid):
        return True
    if db.is_added_user(uid):
        return True
    if Config.ALLOWED_USERS:
        return uid in Config.ALLOWED_USERS
    return False


def _admin_ids() -> list[int]:
    return Config.ADMIN_IDS


# ─── Dekoratorlar ───────────────────────────────────────────────────────────────
def restricted(func):
    """Har bir handler — matn buyrug'i BO'LSIN, inline tugma bosilishi
    BO'LSIN — shu orqali o'tishi kerak. Bu callback handlerlar (/list va
    /users boshqaruv tugmalari, kontakt ulashish) uchun ham amal qiladi:
    aks holda botdan o'chirilgan foydalanuvchi eski xabaridagi tugmalar
    orqali hali ham o'z kuzatuvlarini boshqarishda davom eta olardi —
    ruxsat bekor qilingandan keyin ham amal qiladigan "orphan" boshqaruv
    yo'li qolmasligi kerak."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        uid = update.effective_user.id
        if not has_access(uid):
            logger.warning(f"Ruxsatsiz: uid={uid}")
            if update.callback_query:
                try:
                    await update.callback_query.answer("⛔ Sizga ruxsat yo'q.", show_alert=True)
                except Exception:
                    pass
            else:
                await update.effective_message.reply_text("⛔ Sizga ruxsat yo'q.")
            return ConversationHandler.END
        if not db.touch_user_activity(uid):
            logger.warning(f"Faollik statistikasi saqlanmadi (DB write xato): uid={uid}")
        return await func(update, context, *args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper


def rate_limited(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        uid = update.effective_user.id
        if security.is_rate_limited(uid):
            await update.effective_message.reply_text("⏳ Juda tez bosyapsiz.")
            return
        return await func(update, context, *args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper


def _user_card(user) -> str:
    """Foydalanuvchining ochiq ma'lumotlari — Markdown xavfsiz (ism/username escape qilinadi)"""
    full_name = " ".join(filter(None, [user.first_name, user.last_name])) or "—"
    username = f"@{user.username}" if user.username else "—"
    return (
        f"👤 FIO: {escape_markdown(full_name)}\n"
        f"🆔 ID: `{user.id}`\n"
        f"🔗 Username: {escape_markdown(username)}"
    )


# uid -> oxirgi notifikatsiya vaqti; /start spam bilan adminni bezovta qilmaslik uchun
_START_NOTIFY_INTERVAL = 6 * 3600
_last_start_notify: dict[int, float] = {}


async def _notify_admins_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi /start bosganda adminlarga uning ochiq ma'lumotlarini yuborish"""
    user = update.effective_user
    if user.id in _admin_ids():
        return  # Admin o'zi haqida xabar olmasin
    now = time.time()
    if now - _last_start_notify.get(user.id, 0.0) < _START_NOTIFY_INTERVAL:
        return
    _last_start_notify[user.id] = now
    status = "✅ ruxsatli" if has_access(user.id) else "⛔ ruxsatsiz"
    text = (
        f"🆕 *Foydalanuvchi /start bosdi* ({status})\n\n"
        f"{_user_card(user)}\n"
        f"🌐 Til: {user.language_code or '—'}"
    )
    for aid in _admin_ids():
        try:
            await context.application.bot.send_message(aid, text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Admin {aid} ga /start xabari yuborilmadi: {e}")


@restricted
async def got_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi telefon raqamini ulashganda adminlarga yuborish va saqlash"""
    contact = update.message.contact
    user = update.effective_user
    if not contact:
        return
    if contact.user_id != user.id:
        await update.message.reply_text("❌ Iltimos, tugma orqali o'z raqamingizni ulashing.")
        return
    phone_saved = db.set_user_phone(user.id, contact.phone_number)
    if phone_saved is None:
        await update.message.reply_text(
            "⚠️ Vaqtinchalik xatolik — raqamni saqlab bo'lmadi. Birozdan so'ng qaytadan urinib ko'ring."
        )
        return
    text = (
        "📱 *Telefon raqami ulashildi:*\n\n"
        f"{_user_card(user)}\n"
        f"📞 Raqam: {escape_markdown(contact.phone_number)}"
    )
    for aid in _admin_ids():
        if aid == user.id:
            continue
        try:
            await context.application.bot.send_message(aid, text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Admin {aid} ga kontakt xabari yuborilmadi: {e}")
    await update.message.reply_text("✅ Rahmat!", reply_markup=ReplyKeyboardRemove())


# ─── Singleton lock ──────────────────────────────────────────────────────────────
def acquire_lock():
    """Faqat bitta process ishlashini ta'minlash"""
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        return lock_fd
    except IOError:
        logger.error("Bot allaqachon ishlamoqda! Avvalgi processni to'xtatib qayta ishga tushiring.")
        sys.exit(1)


# ─── Kalendar ───────────────────────────────────────────────────────────────────
def _calendar_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    now = datetime.now().date()
    month_names = ["","Yanvar","Fevral","Mart","Aprel","May","Iyun",
                   "Iyul","Avgust","Sentabr","Oktabr","Noyabr","Dekabr"]
    rows = [[
        InlineKeyboardButton("◀️", callback_data=f"cal_prev|{year}|{month}"),
        InlineKeyboardButton(f"{month_names[month]} {year}", callback_data="cal_ignore"),
        InlineKeyboardButton("▶️", callback_data=f"cal_next|{year}|{month}"),
    ],[
        InlineKeyboardButton(d, callback_data="cal_ignore")
        for d in ["Du","Se","Ch","Pa","Ju","Sh","Ya"]
    ]]
    for week in calendar.monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="cal_ignore"))
            else:
                date = datetime(year, month, day).date()
                if date < now:
                    row.append(InlineKeyboardButton("·", callback_data="cal_ignore"))
                else:
                    label = f"[{day}]" if date == now else str(day)
                    row.append(InlineKeyboardButton(label, callback_data=f"cal_pick|{year}-{month:02d}-{day:02d}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


# ─── Yordamchi ──────────────────────────────────────────────────────────────────
def _station_keyboard(prefix: str, exclude: str = "") -> InlineKeyboardMarkup:
    keys = [k for k in STATIONS if k != exclude]
    rows = []
    for i in range(0, len(keys), 2):
        row = [InlineKeyboardButton(keys[i], callback_data=f"{prefix}|{keys[i]}")]
        if i + 1 < len(keys):
            row.append(InlineKeyboardButton(keys[i+1], callback_data=f"{prefix}|{keys[i+1]}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _monitor_summary(m: dict) -> str:
    price = f"{m['max_price']:,}" if m.get("max_price") else "∞"
    checks = m.get("check_count", 0)
    min_seats = m.get("min_seats", 1)
    seats_line = f"  🎟 Kamida {min_seats} ta joy\n" if min_seats > 1 else ""
    return (
        f"🆔 `{m['id']}`\n"
        f"  🚉 {m['from_name']} → {m['to_name']}\n"
        f"  📅 {m['date']} | ⏰ {m.get('time_from','00:00')}–{m.get('time_to','23:59')}\n"
        f"  🚂 {CAR_TYPES.get(m.get('car_type','any'), m.get('car_type',''))}\n"
        f"{seats_line}"
        f"  💰 {price} so'm | 🔄 {checks} tekshirildi"
    )


# ─── /start ─────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    await _notify_admins_start(update, context)

    if not has_access(uid):
        logger.warning(f"Ruxsatsiz: uid={uid}")
        await update.message.reply_text("⛔ Sizga ruxsat yo'q.")
        return
    if not db.touch_user_activity(uid):
        logger.warning(f"Faollik statistikasi saqlanmadi (DB write xato): uid={uid}")

    extra = (
        "\n\n👑 *Admin buyruqlari:*\n"
        "/addUser — Bitta foydalanuvchi qo'shish\n"
        "/addUsers — Bir nechta foydalanuvchi qo'shish\n"
        "/users — Foydalanuvchilar va faoliyati\n"
        "/removeUser — Foydalanuvchini o'chirish\n"
        "/logs — Loglar\n"
        "/metrics — Monitoring ko'rsatkichlari"
    ) if is_admin(uid) else ""
    await update.message.reply_text(
        f"Salom, {escape_markdown(user.first_name or '')}! 🚆\n\n"
        "📌 *Buyruqlar:*\n"
        "/monitor — Yangi kuzatuv\n"
        "/list — Faol kuzatuvlar (tahrirlash/o'chirish)\n"
        "/stop — Barchasini to'xtatish\n"
        "/help — Yordam\n"
        "/privacy — Maxfiylik siyosati" + extra,
        parse_mode="Markdown",
    )

    u = db.get_user(uid)
    if not is_admin(uid) and not (u and u.get("phone")):
        contact_kb = ReplyKeyboardMarkup(
            [[KeyboardButton("📱 Telefon raqamni ulashish", request_contact=True)]],
            resize_keyboard=True, one_time_keyboard=True,
        )
        await update.message.reply_text(
            "📱 Aloqa uchun telefon raqamingizni ulashasizmi? (ixtiyoriy)",
            reply_markup=contact_kb,
        )


@restricted
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    extra = (
        "\n\n👑 *Admin buyruqlari:*\n"
        "/addUser `<id> [ism]` — bitta foydalanuvchi qo'shish\n"
        "/addUsers `<id1> <id2> ...` — bir nechtasini qo'shish\n"
        "/users — ro'yxat va faoliyat statistikasi\n"
        "/removeUser `<id>` — foydalanuvchini o'chirish\n"
        "/logs — bot loglari\n"
        "/metrics — monitoring ko'rsatkichlari"
    ) if is_admin(update.effective_user.id) else ""
    await update.message.reply_text(
        "🚆 *Railway Monitor Bot*\n\n"
        "1️⃣ /monitor — sana, marshrut, vaqt tanlang\n"
        "2️⃣ Bot hozirda mavjud biletlarni darhol ko'rsatadi\n"
        "3️⃣ Muntazam kuzatib, yangi chiqqanda xabar beradi\n"
        "4️⃣ /list — kuzatuvlarni ko'rish, tahrirlash, o'chirish\n\n"
        f"*Interval:* ~{Config.CHECK_INTERVAL} soniyada bir tekshirish (yuklama katta "
        "bo'lsa biroz farq qilishi mumkin)\n"
        f"*Limit:* Bir vaqtda {Config.MAX_MONITORS_PER_USER} ta kuzatuv\n\n"
        "🔒 /privacy — qanday ma'lumotlaringiz saqlanishi haqida" + extra,
        parse_mode="Markdown",
    )


# ─── /privacy ───────────────────────────────────────────────────────────────────
async def cmd_privacy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Har kimga ochiq — hatto botdan hali ruxsat olmagan foydalanuvchiga ham
    qanday ma'lumot saqlanishi haqida shaffof javob berish kerak."""
    await update.message.reply_text(
        "🔒 *Maxfiylik va ma'lumotlar*\n\n"
        "Ushbu bot quyidagilarni saqlaydi (faqat botdan foydalanish uchun):\n"
        "• Telegram ID, ism, username, til — kirish nazorati va admin panel uchun;\n"
        "• Telefon raqami — FAQAT o'zingiz \"Telefon raqamni ulashish\" tugmasini "
        "bosib, ixtiyoriy ravishda yuborsangiz;\n"
        "• Siz yaratgan kuzatuvlar (marshrut, sana, filtrlar) va ularning tarixi;\n"
        "• So'nggi faollik vaqti va amallar soni — admin uchun statistika.\n\n"
        "Ma'lumotlar botni ishlatgan xizmat ko'rsatuvchisi (server) diskida "
        "saqlanadi va uchinchi shaxslarga uzatilmaydi. Admin sizni botdan "
        "o'chirsa, bundan buyon botdan foydalana olmaysiz; mavjud yozuvlar "
        "kelajakda qayta faollashtirish/audit uchun arxivda qolishi mumkin — "
        "agar ma'lumotlaringizni butunlay o'chirishni so'ramoqchi bo'lsangiz, "
        "adminga to'g'ridan-to'g'ri murojaat qiling.\n\n"
        "Telefon raqami ulashish har doim ixtiyoriy — rad etsangiz ham "
        "botning barcha funksiyalaridan foydalanishda davom etasiz.",
        parse_mode="Markdown",
    )


# ─── /logs ──────────────────────────────────────────────────────────────────────
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Faqat admin uchun.")
        return
    if not os.path.exists("bot.log"):
        await update.message.reply_text("📭 Log fayl topilmadi.")
        return
    with open("bot.log", "r", encoding="utf-8") as f:
        lines = f.readlines()
    last = lines[-50:] if len(lines) > 50 else lines
    text = "".join(last)
    if len(text) > 4000:
        text = "...(oxirgi qism)...\n" + text[-4000:]
    await update.message.reply_text(
        f"📋 *Bot log (oxirgi {len(last)} qator):*\n\n```\n{text}\n```",
        parse_mode="Markdown",
    )


# ─── /metrics ───────────────────────────────────────────────────────────────────
async def cmd_metrics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin uchun: bot haqiqatan tez ishlayaptimi — so'rovlar, qayta
    urinishlar, navbat kutishi, sikl davomiyligi kabi ko'rsatkichlar
    (process xotirasida, restart'da nolga tushadi)."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Faqat admin uchun.")
        return
    snap = metrics.snapshot()
    active_monitors = len(db.get_all_active_monitors())
    unique_routes = len(_search_cache) + len(_search_inflight)

    def g(name, fmt="{}"):
        val = snap.get(name)
        return fmt.format(val) if val is not None else "—"

    lines = [
        "📊 *Monitoring ko'rsatkichlari* (jarayon boshlanganidan beri)\n",
        f"🔭 Faol kuzatuvlar: {active_monitors}",
        f"🧭 Noyob marshrut/sana kalitlari (kesh/navbat): {unique_routes}",
        f"⚙️ RAILWAY_MAX_CONCURRENCY: {Config.RAILWAY_MAX_CONCURRENCY}",
        f"⏱ RAILWAY_MIN_REQUEST_INTERVAL: {Config.RAILWAY_MIN_REQUEST_INTERVAL}s",
        "",
        "*Railway.uz so'rovlari:*",
        f"  Jami so'rov: {g('railway_requests_total')}",
        f"  Qayta urinishlar: {g('railway_retries_total')}",
        f"  429 (rate limit): {g('railway_429_total')}",
        f"  5xx server xato: {g('railway_5xx_total')}",
        f"  Timeout: {g('railway_timeouts_total')}",
        f"  Boshqa exception: {g('railway_exceptions_total')}",
        f"  Buzilgan JSON: {g('railway_malformed_json_total')}",
        "",
        "*Scheduler:*",
        f"  Dispatch qilingan qidiruvlar: {g('search_dispatched_total')}",
        f"  Kesh orqali javob berildi: {g('search_cache_hits_total')}",
        f"  Single-flight qo'shilgan: {g('search_singleflight_joins_total')}",
        f"  Oxirgi navbat kutishi: {g('last_search_queue_wait_seconds', '{:.2f}s')}",
        f"  Oxirgi yuqori oqim javobi: {g('last_search_upstream_latency_seconds', '{:.2f}s')}",
        f"  Oxirgi tekshiruv sikli: {g('last_monitor_cycle_seconds', '{:.2f}s')}",
        "",
        "*Sxema anomaliyalari:*",
        f"  Noma'lum vagon turi: {g('schema_anomalies_unknown_car_type_total')}",
        f"  Tariflar yo'q/bo'sh: {g('schema_anomalies_missing_tariffs_total')}",
        f"  Boshqa anomaliyalar: {sum(v for k, v in snap.items() if k.startswith('schema_anomalies_') and k not in ('schema_anomalies_unknown_car_type_total', 'schema_anomalies_missing_tariffs_total'))}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─── Admin: foydalanuvchilarni boshqarish ───────────────────────────────────────
def _fmt_dt(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    return iso[:16].replace("T", " ")


async def cmd_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Faqat admin uchun.")
        return
    args = context.args
    if not args or not args[0].lstrip("-").isdigit():
        await update.message.reply_text(
            "❗ Foydalanish: `/addUser <telegram_id> [ism]`\n"
            "Masalan: `/addUser 123456789 Aziz`",
            parse_mode="Markdown",
        )
        return
    tid = int(args[0])
    name = " ".join(args[1:]) if len(args) > 1 else ""
    result = db.add_user(tid, added_by=uid, first_name=name)
    if result is None:
        await update.message.reply_text(
            "❌ Foydalanuvchini saqlab bo'lmadi — serverda vaqtinchalik xatolik.\n"
            "Iltimos, birozdan so'ng qaytadan urinib ko'ring."
        )
        return
    verb = "qo'shildi" if result else "allaqachon ro'yxatda edi (qayta faollashtirildi)"
    name_suffix = f" — {escape_markdown(name)}" if name else ""
    await update.message.reply_text(
        f"✅ Foydalanuvchi `{tid}` {verb}{name_suffix}",
        parse_mode="Markdown",
    )
    try:
        await context.application.bot.send_message(
            tid, "✅ Sizga botdan foydalanish huquqi berildi!\n/start bosing."
        )
    except Exception:
        pass


async def cmd_add_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Faqat admin uchun.")
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "❗ Foydalanish: `/addUsers <id1> <id2> <id3> ...`\n"
            "Masalan: `/addUsers 111111111 222222222 333333333`",
            parse_mode="Markdown",
        )
        return
    ids = []
    for a in args:
        for part in a.replace(",", " ").split():
            if part.isdigit() and int(part) not in ids:
                ids.append(int(part))
    if not ids:
        await update.message.reply_text("❌ To'g'ri telegram ID topilmadi.")
        return

    added, reactivated, failed = 0, 0, 0
    succeeded_ids = []
    for tid in ids:
        result = db.add_user(tid, added_by=uid)
        if result is True:
            added += 1
            succeeded_ids.append(tid)
        elif result is False:
            reactivated += 1
            succeeded_ids.append(tid)
        else:  # None — yozuv muvaffaqiyatsiz
            failed += 1

    msg = f"✅ {added} ta yangi qo'shildi, {reactivated} ta qayta faollashtirildi."
    if failed:
        msg += f"\n⚠️ {failed} ta ID saqlanmadi (vaqtinchalik xatolik) — qaytadan urinib ko'ring."
    await update.message.reply_text(msg)

    # Faqat DISKKA HAQIQATAN saqlangan foydalanuvchilarga xabar yuboramiz —
    # yozuvi muvaffaqiyatsiz bo'lganlarga soxta "ruxsat berildi" demaymiz.
    for tid in succeeded_ids:
        try:
            await context.application.bot.send_message(
                tid, "✅ Sizga botdan foydalanish huquqi berildi!\n/start bosing."
            )
        except Exception:
            pass


async def _admin_remove_user(tid: int, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Foydalanuvchini botdan o'chirish umumiy oqimi (/removeUser va /users
    panelidagi tugma bir xil mantiqni ishlatadi). Ruxsatni bekor qilish
    DISKKA muvaffaqiyatli yozilmaguncha "o'chirildi" deb HECH QACHON
    aytilmaydi — chaqiruvchiga ko'rsatiladigan matnni qaytaradi."""
    result = db.remove_user(tid)
    if result is None:
        return "⚠️ Vaqtinchalik xatolik — foydalanuvchini o'chirib bo'lmadi. Qaytadan urinib ko'ring."
    if result is False:
        return "❌ Topilmadi yoki allaqachon o'chirilgan."

    # Ruxsat muvaffaqiyatli bekor qilindi (eng muhim xavfsizlik amali bajarildi).
    # Uning faol kuzatuvlarini to'xtatish — ikkinchi darajali tozalash;
    # muvaffaqiyatsiz bo'lsa ham ruxsat bekor qilingani rost qoladi, faqat log qilinadi.
    monitors_result = db.deactivate_all(tid)
    if monitors_result is None:
        logger.error(f"Foydalanuvchi {tid} o'chirildi, lekin uning kuzatuvlarini to'xtatib bo'lmadi")

    try:
        await context.application.bot.send_message(
            tid, "⛔ Sizning botdan foydalanish huquqingiz bekor qilindi."
        )
    except Exception:
        pass
    return f"✅ Foydalanuvchi `{tid}` botdan o'chirildi."


async def cmd_remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Faqat admin uchun.")
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text(
            "❗ Foydalanish: `/removeUser <telegram_id>`", parse_mode="Markdown"
        )
        return
    tid = int(args[0])
    text = await _admin_remove_user(tid, context)
    await update.message.reply_text(text, parse_mode="Markdown")


def _users_keyboard(users: list) -> InlineKeyboardMarkup:
    rows = []
    for u in users:
        label = f"👤 {u['tid']}" + (f" — {u['first_name']}" if u.get("first_name") else "")
        rows.append([InlineKeyboardButton(label, callback_data=f"usr_show|{u['tid']}")])
    return InlineKeyboardMarkup(rows)


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Faqat admin uchun.")
        return
    users = db.get_users(active_only=True)
    if not users:
        await update.message.reply_text("📭 Hali hech kim qo'shilmagan.\n/addUser — qo'shish")
        return
    await update.message.reply_text(
        f"👥 *Ruxsatli foydalanuvchilar ({len(users)} ta):*\n\nFaoliyatini ko'rish uchun tanlang:",
        parse_mode="Markdown",
        reply_markup=_users_keyboard(users),
    )


def _monitor_admin_line(idx: int, m: dict) -> str:
    price = f"{m['max_price']:,} so'm" if m.get("max_price") else "cheksiz"
    status = "🟢 faol" if m.get("active") else "⚪ tugagan"
    min_seats = m.get("min_seats", 1)
    seats_text = f" | 🎟 {min_seats} joy" if min_seats > 1 else ""
    return (
        f"{idx}. {status} | 🚉 {m.get('from_name','?')} → {m.get('to_name','?')}\n"
        f"    📅 {m.get('date','—')} | ⏰ {m.get('time_from','00:00')}–{m.get('time_to','23:59')}\n"
        f"    🚂 {CAR_TYPES.get(m.get('car_type','any'), m.get('car_type',''))} | 💰 {price}{seats_text}\n"
        f"    🔄 {m.get('check_count', 0)} marta tekshirildi | 🕐 tanlagan: {_fmt_dt(m.get('created_at'))}"
    )


MAX_ADMIN_MONITORS_SHOWN = 12


async def usr_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("⛔ Faqat admin uchun.")
        return
    _, tid_str = q.data.split("|", 1)
    tid = int(tid_str)
    u = db.get_user(tid)
    if not u:
        await q.edit_message_text("❌ Topilmadi.")
        return
    stats = db.get_user_monitor_stats(tid)
    monitors = db.get_user_monitors(tid)

    text = (
        f"👤 *Foydalanuvchi:* `{tid}`\n"
        f"📛 Ism: {escape_markdown(u.get('first_name') or '—')}\n"
        f"📞 Tel: {escape_markdown(u.get('phone') or '—')}\n"
        f"📅 Qo'shilgan: {_fmt_dt(u.get('added_at'))}\n"
        f"🕐 Oxirgi faollik: {_fmt_dt(u.get('last_seen'))}\n"
        f"🔢 Amallar soni: {u.get('action_count', 0)}\n"
        f"📊 Kuzatuvlar: {stats['active']} faol / {stats['total']} jami\n"
        f"🔄 Jami tekshiruvlar: {stats['total_checks']}"
    )

    if monitors:
        shown = monitors[:MAX_ADMIN_MONITORS_SHOWN]
        text += "\n\n📋 *Tanlagan yo'nalishlari:*\n\n"
        text += "\n\n".join(_monitor_admin_line(i, m) for i, m in enumerate(shown, 1))
        if len(monitors) > MAX_ADMIN_MONITORS_SHOWN:
            text += f"\n\n… yana {len(monitors) - MAX_ADMIN_MONITORS_SHOWN} ta kuzatuv bor"
    else:
        text += "\n\n📭 Hali birorta kuzatuv qo'shmagan."

    await q.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Botdan o'chirish", callback_data=f"usr_del|{tid}")],
            [InlineKeyboardButton("◀️ Orqaga", callback_data="usr_back")],
        ]),
    )


async def usr_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("⛔ Faqat admin uchun.")
        return
    _, tid_str = q.data.split("|", 1)
    tid = int(tid_str)
    text = await _admin_remove_user(tid, context)
    await q.edit_message_text(text, parse_mode="Markdown")


async def usr_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    users = db.get_users(active_only=True)
    if not users:
        await q.edit_message_text("📭 Hali hech kim qo'shilmagan.")
        return
    await q.edit_message_text(
        f"👥 *Ruxsatli foydalanuvchilar ({len(users)} ta):*\n\nFaoliyatini ko'rish uchun tanlang:",
        parse_mode="Markdown",
        reply_markup=_users_keyboard(users),
    )


# ─── /monitor conversation ──────────────────────────────────────────────────────
@restricted
@rate_limited
async def monitor_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    active = db.get_active_monitors(uid)
    if len(active) >= Config.MAX_MONITORS_PER_USER:
        await update.message.reply_text(
            f"⚠️ Maksimal {Config.MAX_MONITORS_PER_USER} ta kuzatuv.\n"
            "/list — ko'rish va o'chirish"
        )
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        "🚉 *Qayerdan* ketasiz?",
        reply_markup=_station_keyboard("from"),
        parse_mode="Markdown",
    )
    return WAIT_FROM


@restricted
async def got_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, name = q.data.split("|", 1)
    context.user_data["from_name"] = name
    context.user_data["from_code"] = STATIONS[name]
    await q.edit_message_text(
        f"✅ *Qayerdan:* {name}\n\n🚉 *Qayerga* ketasiz?",
        reply_markup=_station_keyboard("to", exclude=name),
        parse_mode="Markdown",
    )
    return WAIT_TO


@restricted
async def got_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, name = q.data.split("|", 1)
    context.user_data["to_name"] = name
    context.user_data["to_code"] = STATIONS[name]
    now = datetime.now()
    await q.edit_message_text(
        f"✅ *Qayerdan:* {context.user_data['from_name']}\n"
        f"✅ *Qayerga:* {name}\n\n📅 *Sana* tanlang:",
        reply_markup=_calendar_keyboard(now.year, now.month),
        parse_mode="Markdown",
    )
    return WAIT_DATE


@restricted
async def cal_navigate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split("|")
    action, year, month = parts[0], int(parts[1]), int(parts[2])
    if action == "cal_prev":
        month -= 1
        if month < 1: month, year = 12, year - 1
    else:
        month += 1
        if month > 12: month, year = 1, year + 1
    await q.edit_message_reply_markup(reply_markup=_calendar_keyboard(year, month))
    return WAIT_DATE


@restricted
async def cal_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, date_str = q.data.split("|", 1)
    context.user_data["date"] = date_str
    await q.edit_message_text(
        f"✅ *Sana:* {date_str}\n\n🚂 *Vagon turi* tanlang:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🪑 Platskart", callback_data="car|platskar"),
             InlineKeyboardButton("🛏 Kupe", callback_data="car|coupe")],
            [InlineKeyboardButton("💺 SV", callback_data="car|sv"),
             InlineKeyboardButton("🚄 Afrosiyob", callback_data="car|afrosiyob")],
            [InlineKeyboardButton("🚅 Sharq", callback_data="car|sharq"),
             InlineKeyboardButton("🔀 Barchasi", callback_data="car|any")],
        ]),
        parse_mode="Markdown",
    )
    return WAIT_CAR_TYPE


@restricted
async def got_car_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, car = q.data.split("|", 1)
    context.user_data["car_type"] = car
    await q.edit_message_text(
        f"✅ *Vagon:* {CAR_TYPES[car]}\n\n⏰ *Vaqt oralig'i* tanlang:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🕐 Istalgan vaqt",       callback_data="time|any")],
            [InlineKeyboardButton("🌅 Ertalab 06:00–12:00", callback_data="time|morning")],
            [InlineKeyboardButton("☀️ Kunduz 12:00–18:00",  callback_data="time|day")],
            [InlineKeyboardButton("🌆 Kechqurun 18:00–00:00", callback_data="time|evening")],
            [InlineKeyboardButton("🌙 Tunda 00:00–06:00",   callback_data="time|night")],
            [InlineKeyboardButton("✏️ O'zim kiritaman",     callback_data="time|custom")],
        ]),
        parse_mode="Markdown",
    )
    return WAIT_TIME_RANGE


@restricted
async def got_time_range(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, trange = q.data.split("|", 1)
    if trange == "custom":
        context.user_data["time_range"] = "custom"
        await q.edit_message_text(
            "✏️ Vaqt oralig'ini kiriting:\nFormat: `HH:MM-HH:MM`\nMasalan: `18:00-20:00`",
            parse_mode="Markdown",
        )
        return WAIT_TIME_RANGE
    t_from, t_to, label = TIME_RANGES[trange]
    context.user_data.update(time_from=t_from, time_to=t_to, time_label=label)
    await q.edit_message_text(
        f"✅ *Vaqt:* {label}\n\n💰 *Maksimal narx* kiriting\nYoki /skip — cheksiz:",
        parse_mode="Markdown",
    )
    return WAIT_MAX_PRICE


@restricted
async def got_custom_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("time_range") != "custom":
        return WAIT_MAX_PRICE
    text = update.message.text.strip()
    try:
        t1, t2 = text.split("-")
        datetime.strptime(t1.strip(), "%H:%M")
        datetime.strptime(t2.strip(), "%H:%M")
        label = f"✏️ {t1.strip()}–{t2.strip()}"
        context.user_data.update(time_from=t1.strip(), time_to=t2.strip(), time_label=label)
    except Exception:
        await update.message.reply_text("❌ Format: `18:00-20:00`", parse_mode="Markdown")
        return WAIT_TIME_RANGE
    await update.message.reply_text(
        f"✅ *Vaqt:* {label}\n\n💰 *Maksimal narx* kiriting\nYoki /skip — cheksiz:",
        parse_mode="Markdown",
    )
    return WAIT_MAX_PRICE


MAX_SEATS_LIMIT = 20


@restricted
async def got_max_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    max_price = None
    if text != "/skip":
        cleaned = text.replace(" ","").replace(",","").replace(".","")
        if not cleaned.isdigit():
            await update.message.reply_text("❌ Faqat raqam yoki /skip", parse_mode="Markdown")
            return WAIT_MAX_PRICE
        max_price = int(cleaned)
        if max_price < 10000:
            await update.message.reply_text("❌ Kamida 10,000 so'm.")
            return WAIT_MAX_PRICE
    context.user_data["max_price"] = max_price
    await update.message.reply_text(
        "🎟 *Nechta joy kerak?*\n"
        "Masalan, 2 kishi birga sayohat qilsangiz — `2` deb kiriting.\n"
        "Shuncha (yoki ko'proq) joy bitta poyezd/sinfda chiqqandagina xabar beraman.\n\n"
        "Yoki /skip — 1 ta joy yetarli:",
        parse_mode="Markdown",
    )
    return WAIT_MIN_SEATS


@restricted
async def got_min_seats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    min_seats = 1
    if text != "/skip":
        if not text.isdigit() or int(text) < 1:
            await update.message.reply_text("❌ Faqat musbat butun son yoki /skip", parse_mode="Markdown")
            return WAIT_MIN_SEATS
        min_seats = int(text)
        if min_seats > MAX_SEATS_LIMIT:
            await update.message.reply_text(f"❌ Ko'pi bilan {MAX_SEATS_LIMIT} ta.")
            return WAIT_MIN_SEATS
    context.user_data["min_seats"] = min_seats
    await _confirm_and_start(update, context)
    return ConversationHandler.END


async def _confirm_and_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ud = context.user_data
    time_from = ud.get("time_from", "00:00")
    time_to   = ud.get("time_to", "23:59")
    time_label = ud.get("time_label", "🕐 Istalgan vaqt")
    monitor = {
        "uid": uid,
        "from_name": ud["from_name"], "from_code": ud["from_code"],
        "to_name":   ud["to_name"],   "to_code":   ud["to_code"],
        "date": ud["date"], "car_type": ud["car_type"],
        "time_from": time_from, "time_to": time_to, "time_label": time_label,
        "max_price": ud.get("max_price"),
        "min_seats": ud.get("min_seats", 1),
        "active": True, "created_at": datetime.now().isoformat(),
        "check_count": 0, "last_check": None,
    }
    mid = db.save_monitor(uid, monitor)
    if mid is None:
        # DB yozuvi muvaffaqiyatsiz bo'ldi — foydalanuvchiga yolg'on "boshlandi"
        # demaymiz va monitor task'ini ishga tushirmaymiz (u DB'da yo'q).
        logger.error(f"Monitor saqlanmadi (DB write xato): uid={uid}")
        await update.message.reply_text(
            "❌ Kuzatuvni saqlab bo'lmadi — serverda vaqtinchalik xatolik.\n"
            "Iltimos, birozdan so'ng /monitor bilan qaytadan urinib ko'ring."
        )
        return
    price_text = f"{ud['max_price']:,} so'm" if ud.get("max_price") else "Cheksiz"
    min_seats = ud.get("min_seats", 1)
    await update.message.reply_text(
        f"✅ *Kuzatuv boshlandi!*\n\n"
        f"🚉 {ud['from_name']} → {ud['to_name']}\n"
        f"📅 {ud['date']}\n"
        f"🚂 {CAR_TYPES.get(ud['car_type'], ud['car_type'])}\n"
        f"⏰ {time_label}\n"
        f"💰 Maks: {price_text}\n"
        f"🎟 Kamida: {min_seats} ta joy\n"
        f"🆔 `{mid}`\n\n"
        "⏳ Hozirgi mavjud biletlar tekshirilmoqda...",
        parse_mode="Markdown",
    )
    _spawn_monitor(uid, mid, monitor, context.application)


# ─── /list — ko'rish, tahrirlash, o'chirish ─────────────────────────────────────
@restricted
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db.deactivate_expired(datetime.now().strftime("%Y-%m-%d"))  # eski sanalarni avtomatik tozalash
    monitors = db.get_active_monitors(uid)
    if not monitors:
        await update.message.reply_text("📭 Faol kuzatuv yo'q.\n/monitor — yangi boshlash")
        return
    await update.message.reply_text(
        f"📊 *Faol kuzatuvlar ({len(monitors)} ta):*\n\n"
        "Boshqarish uchun bitta ID ni tanlang:",
        parse_mode="Markdown",
        reply_markup=_monitors_keyboard(monitors),
    )


def _monitors_keyboard(monitors: list) -> InlineKeyboardMarkup:
    rows = []
    for m in monitors:
        label = f"🚉 {m['from_name'].split()[-1]}→{m['to_name'].split()[-1]} | {m['date']} | {CAR_TYPES.get(m.get('car_type','any'),'')}"
        rows.append([InlineKeyboardButton(label, callback_data=f"mgr_show|{m['id']}")])
    return InlineKeyboardMarkup(rows)


@restricted
async def mgr_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, mid = q.data.split("|", 1)
    uid = q.from_user.id
    monitors = db.get_active_monitors(uid)
    m = next((x for x in monitors if x["id"] == mid), None)
    if not m:
        await q.edit_message_text("❌ Kuzatuv topilmadi.")
        return
    context.user_data["edit_mid"] = mid
    await q.edit_message_text(
        f"📋 *Kuzatuv ma'lumotlari:*\n\n{_monitor_summary(m)}\n\n"
        "Nima qilmoqchisiz?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 O'chirish", callback_data=f"mgr_del|{mid}"),
             InlineKeyboardButton("✏️ Tahrirlash", callback_data=f"mgr_edit|{mid}")],
            [InlineKeyboardButton("◀️ Orqaga", callback_data="mgr_back")],
        ]),
    )


@restricted
async def mgr_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, mid = q.data.split("|", 1)
    uid = q.from_user.id
    result = db.deactivate_for_user(uid, mid)
    if result is True:
        await q.edit_message_text(f"✅ Kuzatuv `{mid}` o'chirildi.", parse_mode="Markdown")
    elif result is None:
        await q.edit_message_text("⚠️ Vaqtinchalik xatolik — o'chirib bo'lmadi. Qaytadan urinib ko'ring.")
    else:
        await q.edit_message_text("❌ Topilmadi.")


@restricted
async def mgr_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, mid = q.data.split("|", 1)
    context.user_data["edit_mid"] = mid
    await q.edit_message_text(
        "✏️ *Nimani o'zgartirmoqchisiz?*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 Sana",         callback_data=f"mgr_ef|date|{mid}")],
            [InlineKeyboardButton("⏰ Vaqt oralig'i", callback_data=f"mgr_ef|time|{mid}")],
            [InlineKeyboardButton("🚂 Vagon turi",    callback_data=f"mgr_ef|car|{mid}")],
            [InlineKeyboardButton("💰 Maks narx",     callback_data=f"mgr_ef|price|{mid}")],
            [InlineKeyboardButton("🎟 Joy soni",      callback_data=f"mgr_ef|seats|{mid}")],
            [InlineKeyboardButton("◀️ Orqaga",        callback_data=f"mgr_show|{mid}")],
        ]),
    )


@restricted
async def mgr_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split("|")
    _, field, mid = parts[0], parts[1], parts[2]
    context.user_data["edit_mid"] = mid
    context.user_data["edit_field"] = field

    if field == "date":
        now = datetime.now()
        await q.edit_message_text(
            "📅 Yangi sana tanlang:",
            reply_markup=_calendar_keyboard(now.year, now.month),
        )
    elif field == "time":
        await q.edit_message_text(
            "⏰ Yangi vaqt oralig'i:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🕐 Istalgan vaqt",        callback_data="mgr_tv|any")],
                [InlineKeyboardButton("🌅 Ertalab 06:00–12:00",  callback_data="mgr_tv|morning")],
                [InlineKeyboardButton("☀️ Kunduz 12:00–18:00",   callback_data="mgr_tv|day")],
                [InlineKeyboardButton("🌆 Kechqurun 18:00–00:00",callback_data="mgr_tv|evening")],
                [InlineKeyboardButton("🌙 Tunda 00:00–06:00",    callback_data="mgr_tv|night")],
            ]),
        )
    elif field == "car":
        await q.edit_message_text(
            "🚂 Yangi vagon turi:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🪑 Platskart", callback_data="mgr_cv|platskar"),
                 InlineKeyboardButton("🛏 Kupe",      callback_data="mgr_cv|coupe")],
                [InlineKeyboardButton("💺 SV",        callback_data="mgr_cv|sv"),
                 InlineKeyboardButton("🚄 Afrosiyob", callback_data="mgr_cv|afrosiyob")],
                [InlineKeyboardButton("🚅 Sharq",     callback_data="mgr_cv|sharq"),
                 InlineKeyboardButton("🔀 Barchasi",  callback_data="mgr_cv|any")],
            ]),
        )
    elif field == "price":
        await q.edit_message_text(
            "💰 Yangi maksimal narx kiriting (so'm)\nYoki /skip — cheksiz:"
        )
    elif field == "seats":
        await q.edit_message_text(
            "🎟 Kamida nechta joy kerakligini kiriting\nYoki /skip — 1 ta:"
        )


@restricted
async def mgr_time_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, trange = q.data.split("|", 1)
    t_from, t_to, label = TIME_RANGES[trange]
    mid = context.user_data.get("edit_mid")
    uid = q.from_user.id
    # Uchala maydon (time_from/time_to/time_label) BITTA tranzaksiyada
    # yoziladi — aks holda yozuv o'rtada muvaffaqiyatsiz bo'lib qolsa,
    # monitor nomuvofiq holatda (masalan yangi time_from, eski time_label)
    # qolib ketishi mumkin edi.
    ok = db.update_monitor_fields(uid, mid, {
        "time_from": t_from, "time_to": t_to, "time_label": label,
    })
    if ok:
        await q.edit_message_text(f"✅ Vaqt oralig'i yangilandi: {label}\n\n/list — ro'yxatga qaytish")
    else:
        await q.edit_message_text("⚠️ Vaqtinchalik xatolik — saqlab bo'lmadi. Qaytadan urinib ko'ring.")


@restricted
async def mgr_car_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, car = q.data.split("|", 1)
    mid = context.user_data.get("edit_mid")
    uid = q.from_user.id
    if db.update_monitor_field(uid, mid, "car_type", car):
        await q.edit_message_text(f"✅ Vagon turi yangilandi: {CAR_TYPES[car]}\n\n/list — ro'yxatga qaytish")
    else:
        await q.edit_message_text("⚠️ Vaqtinchalik xatolik — saqlab bo'lmadi. Qaytadan urinib ko'ring.")


@restricted
async def mgr_cal_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tahrirlash rejimida sana tanlash"""
    q = update.callback_query
    await q.answer()
    _, date_str = q.data.split("|", 1)
    mid = context.user_data.get("edit_mid")
    field = context.user_data.get("edit_field")
    if field == "date" and mid:
        uid = q.from_user.id
        if db.update_monitor_field(uid, mid, "date", date_str):
            await q.edit_message_text(f"✅ Sana yangilandi: {date_str}\n\n/list — ro'yxatga qaytish")
        else:
            await q.edit_message_text("⚠️ Vaqtinchalik xatolik — saqlab bo'lmadi. Qaytadan urinib ko'ring.")
    else:
        # Oddiy /monitor flow
        await cal_pick(update, context)


@restricted
async def mgr_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    db.deactivate_expired(datetime.now().strftime("%Y-%m-%d"))
    monitors = db.get_active_monitors(uid)
    if not monitors:
        await q.edit_message_text("📭 Faol kuzatuv yo'q.")
        return
    await q.edit_message_text(
        f"📊 *Faol kuzatuvlar ({len(monitors)} ta):*\n\nBoshqarish uchun tanlang:",
        parse_mode="Markdown",
        reply_markup=_monitors_keyboard(monitors),
    )


@restricted
async def mgr_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Narx yoki joy soni tahrirlash uchun matn kiritish"""
    field = context.user_data.get("edit_field")
    if field not in ("price", "seats"):
        return
    text = update.message.text.strip()
    mid = context.user_data.get("edit_mid")
    uid = update.effective_user.id

    if field == "price":
        cleaned = text.replace(" ","").replace(",","")
        if not cleaned.isdigit():
            await update.message.reply_text("❌ Faqat raqam yoki /skip")
            return
        if not db.update_monitor_field(uid, mid, "max_price", int(cleaned)):
            await update.message.reply_text("⚠️ Vaqtinchalik xatolik — saqlab bo'lmadi. Qaytadan urinib ko'ring.")
            return
        await update.message.reply_text(f"✅ Narx yangilandi: {int(cleaned):,} so'm\n\n/list — ro'yxatga qaytish")
    else:  # seats
        if not text.isdigit() or int(text) < 1:
            await update.message.reply_text("❌ Faqat musbat butun son yoki /skip")
            return
        n = int(text)
        if n > MAX_SEATS_LIMIT:
            await update.message.reply_text(f"❌ Ko'pi bilan {MAX_SEATS_LIMIT} ta.")
            return
        if not db.update_monitor_field(uid, mid, "min_seats", n):
            await update.message.reply_text("⚠️ Vaqtinchalik xatolik — saqlab bo'lmadi. Qaytadan urinib ko'ring.")
            return
        await update.message.reply_text(f"✅ Joy soni yangilandi: {n} ta\n\n/list — ro'yxatga qaytish")

    context.user_data.pop("edit_field", None)
    context.user_data.pop("edit_mid", None)


@restricted
async def mgr_edit_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Narx/joy soni tahrirlashda /skip — standart qiymatga qaytarish.
    (Buyruqlar filters.TEXT ga tushmaydi, shuning uchun alohida handler kerak)"""
    field = context.user_data.get("edit_field")
    if field not in ("price", "seats"):
        return
    mid = context.user_data.get("edit_mid")
    uid = update.effective_user.id
    if field == "price":
        ok = db.update_monitor_field(uid, mid, "max_price", None)
        msg = "✅ Narx cheki olib tashlandi." if ok else None
    else:
        ok = db.update_monitor_field(uid, mid, "min_seats", 1)
        msg = "✅ Joy soni: 1 ta (standart)." if ok else None
    if not ok:
        await update.message.reply_text("⚠️ Vaqtinchalik xatolik — saqlab bo'lmadi. Qaytadan urinib ko'ring.")
        return
    context.user_data.pop("edit_field", None)
    context.user_data.pop("edit_mid", None)
    await update.message.reply_text(f"{msg}\n\n/list — ro'yxatga qaytish")


# ─── /stop ──────────────────────────────────────────────────────────────────────
@restricted
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = context.args
    if args:
        mid = args[0].strip()
        result = db.deactivate_for_user(uid, mid)
        if result is True:
            await update.message.reply_text(f"⏹ `{mid}` to'xtatildi.", parse_mode="Markdown")
        elif result is None:
            await update.message.reply_text("⚠️ Vaqtinchalik xatolik — to'xtatib bo'lmadi. Qaytadan urinib ko'ring.")
        else:
            await update.message.reply_text("❌ Topilmadi.")
    else:
        count = db.deactivate_all(uid)
        if count is None:
            await update.message.reply_text("⚠️ Vaqtinchalik xatolik — to'xtatib bo'lmadi. Qaytadan urinib ko'ring.")
        elif count:
            await update.message.reply_text(f"⏹ {count} ta kuzatuv to'xtatildi.")
        else:
            await update.message.reply_text("📭 Faol kuzatuv yo'q.")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Bekor qilindi.")
    return ConversationHandler.END


async def cal_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Xato: {context.error}", exc_info=context.error)


# ─── Monitor loop ────────────────────────────────────────────────────────────────
MAX_CONSECUTIVE_ERRORS_BEFORE_NOTICE = 5
MAX_ERROR_BACKOFF = 300  # soniya — API xatosi davom etsa kutish shu chegaradan oshmaydi


def _fmt_iso_date(iso: str) -> str:
    """'2026-10-31' -> '31.10.2026' (xabarlarda sana ko'rinishi uchun)."""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return iso or "—"


def _split_dt(value: str):
    """'31.10.2026 08:37' -> ('31.10.2026', '08:37'); format boshqacha bo'lsa ('', value)."""
    value = value or ""
    if " " in value:
        d, t = value.split(" ", 1)
        return d, t
    return "", value


def _train_fingerprint(found_item) -> str:
    """Bitta topilgan (poyezd, variant) juftligini o'ziga xos aniqlaydi —
    shu identifikator orqali ketma-ket tekshiruvlar orasida "bu HAQIQATAN
    YANGI joymi yoki avvaldan bor edi" deb solishtiramiz.

    ── Identity modeli (ataylab tanlangan) ──────────────────────────────────
    Kiritilgan: poyezd raqami + jo'nash vaqti + xizmat/tarif sinfi + narx.
    - Poyezd raqami + jo'nash vaqti — bir xil kunda bir xil raqamli poyezd
      boshqa vaqtda jo'namaydi, shuning uchun bu ikkisi reys identifikatori.
    - `service_type` (tarifning classServiceType'i, yo'qsa vagon turi) —
      bir xil poyezdda bir nechta sinf (Platskart/Kupe/SV/Biznes/VIP va h.k.)
      bo'lishi mumkin, ular alohida "bilet" sifatida ko'rsatiladi va alohida
      e'lon qilinishi kerak.
    - Narx — xuddi shu sinf ichida turli tariflar (masalan chegirmali va
      to'liq) bo'lishi mumkin; narx o'zgarishi odatda boshqa tarif/joylashuv
      ekanini anglatadi.

    Kiritilmagan (ataylab): joy soni (tariff_seats) — bu BARQAROR EMAS, har
    tekshiruvda tabiiy ravishda o'zgarib turadi (1→3→2 va h.k.) va agar
    fingerprint'ga kiritilsa, HAR BIR son o'zgarishi soxta "yangi bilet"
    deb hisoblanib, keraksiz bildirishnomalar (va monitorni bekorga
    to'xtatish) yaratgan bo'lardi. Joy sonining min_seats chegarasidan
    o'tishi/o'tmasligi — buning o'rniga `_find_all_trains` darajasida (joy
    yetarli bo'lmasa, variant umuman `found`ga kiritilmaydi) hal qilinadi;
    shu orqali "1→3 joy, min_seats=2" kabi holatlar ham to'g'ri aniqlanadi,
    narx/sinf o'zgarmagan taqdirda esa ortiqcha bildirishnoma bo'lmaydi."""
    train, car, price, tariff_seats, service_type = found_item
    return f"{train.get('number')}|{train.get('departureDate')}|{service_type}|{price}"


async def _monitor_loop(uid: int, mid: str, data: dict, app):
    client = await _get_railway_client()
    logger.info(f"Monitor boshlandi: uid={uid} mid={mid}")
    first_run = True
    consecutive_empty_date_errors = 0
    consecutive_errors = 0
    error_notice_sent = False
    # SNAPSHOT modeli (cumulative emas!): bu — "OXIRGI tugallangan tekshiruvda
    # nimalar bor edi" degan holat, "umuman qachondir ko'rilganmi" emas.
    # Shu farq muhim: agar joy g'oyib bo'lib, keyin qayta paydo bo'lsa
    # (A → bo'sh → A), bu snapshot {A}→{}→{A} bo'lib o'zgaradi va ikkinchi
    # "A" to'g'ri "yangi" deb aniqlanadi. Eski kumulyativ (|=) usul esa "A"
    # ni abadiy "ko'rilgan" deb saqlab, qayta paydo bo'lishini sezmasdi.
    previous_fingerprints: set = set()

    while db.is_active(mid):
        sleep_duration = Config.CHECK_INTERVAL
        cycle_start = time.monotonic()
        try:
            # 1) HAR DOIM avval DB'dan eng so'nggi holatni olib kelamiz —
            # va faqat SHUNDAN KEYIN muddat tugash (expiry) qarorini
            # qabul qilamiz. Aks holda: foydalanuvchi /list orqali
            # kuzatuvni (sana/vaqt/filtr) tahrirlasa-yu, ishlab turgan
            # monitor hali eski (stale) `data` bilan ishlasa, tahrirlangan
            # kuzatuv ESKI sana/vaqt asosida noto'g'ri "muddati tugadi"
            # deb bekorga to'xtatilishi mumkin edi.
            monitors = db.get_active_monitors(uid)
            current = next((m for m in monitors if m["id"] == mid), None)
            if current is None:
                # Boshqa yo'l bilan (foydalanuvchi /stop, admin o'chirgan,
                # ruxsat bekor qilingan va h.k.) allaqachon faolsiz —
                # alohida xabar yubormasdan jim tugaymiz (buni amalga
                # oshirgan kod o'z tasdiqini allaqachon yuborgan).
                logger.info(f"Monitor endi topilmadi/faol emas, tugadi: mid={mid}")
                return
            data = current

            # 2) Muddat tugash (expiry) tekshiruvi — ENDI YANGILANGAN
            # `data` bilan. Sana bugun bo'lsa-da, belgilangan vaqt
            # oralig'i (time_to) allaqachon o'tib ketgan bo'lishi mumkin,
            # bu holda ham qidirishning ma'nosi yo'q.
            try:
                mon_dt = datetime.strptime(data["date"], "%Y-%m-%d")
                time_from_str = data.get("time_from", "00:00")
                time_to_str = data.get("time_to", "23:59")
                try:
                    f_h, f_m = map(int, time_from_str.split(":"))
                    t_h, t_m = map(int, time_to_str.split(":"))
                except Exception:
                    f_h, f_m, t_h, t_m = 0, 0, 23, 59
                mon_end = mon_dt.replace(hour=t_h, minute=t_m)
                if (f_h, f_m) > (t_h, t_m):
                    mon_end += timedelta(days=1)  # kechayarim oshadigan oraliq
                if datetime.now() > mon_end:
                    await app.bot.send_message(
                        uid,
                        f"⚠️ Kuzatuv to'xtatildi — belgilangan sana/vaqt "
                        f"({data['date']} {time_from_str}–{time_to_str}) o'tib ketdi.\n"
                        f"🆔 `{mid}`",
                        parse_mode="Markdown",
                    )
                    if not db.deactivate(mid):
                        # DBda hali "faol" deb qolgan bo'lishi mumkin — asyncio task
                        # baribir tugaydi (foydalanuvchi allaqachon xabar oldi va vaqt
                        # o'tgan, qayta qidirishning ma'nosi yo'q). Operator qo'lda
                        # tekshira olishi uchun CRITICAL darajada log qilamiz.
                        logger.critical(
                            f"mid={mid}: vaqt tugagani sababli deactivate yozib "
                            "bo'lmadi — DB hali 'faol' deb ko'rsatishi mumkin"
                        )
                    logger.info(f"Vaqt o'tib ketgani uchun to'xtatildi: mid={mid}")
                    return
            except ValueError:
                pass

            # 3) Qidiruv — umumiy koordinator orqali (bir xil marshrut+sanani
            # so'rayotgan boshqa monitorlar bilan natija bo'lishiladi).
            result = await _shared_search(client, data["from_code"], data["to_code"], data["date"])
            db.increment_check(mid)

            if not result.ok:
                # Railway.uz bilan bog'lanish/API xatosi — bu HECH QACHON
                # "joy/bilet yo'q" deb talqin qilinmaydi.
                consecutive_errors += 1
                consecutive_empty_date_errors = 0
                logger.warning(
                    f"mid={mid}: qidiruv muvaffaqiyatsiz ({result.error}), "
                    f"ketma-ket xato={consecutive_errors}"
                )
                if consecutive_errors == MAX_CONSECUTIVE_ERRORS_BEFORE_NOTICE and not error_notice_sent:
                    await app.bot.send_message(
                        uid,
                        "⚠️ Railway.uz sayti bilan bog'lanishda vaqtinchalik muammo bor.\n"
                        "Bu joy yo'qligini anglatmaydi — kuzatuvni davom ettiryapman, "
                        f"tiklanishi bilan xabar beraman.\n🆔 `{mid}`",
                        parse_mode="Markdown",
                    )
                    error_notice_sent = True
                sleep_duration = min(
                    Config.CHECK_INTERVAL * min(consecutive_errors, 5), MAX_ERROR_BACKOFF
                )
            else:
                if error_notice_sent:
                    await app.bot.send_message(
                        uid,
                        f"✅ Railway.uz bilan bog'lanish tiklandi, kuzatuv normal davom etmoqda.\n🆔 `{mid}`",
                        parse_mode="Markdown",
                    )
                    error_notice_sent = False
                consecutive_errors = 0

                trains = result.trains

                # Agar sayt doimiy bo'sh natija qaytarsa (sana o'tib ketgan yoki
                # o'sha kunga reyslar tugagan bo'lishi mumkin)
                if not trains:
                    consecutive_empty_date_errors += 1
                    if consecutive_empty_date_errors == 10:
                        mon_date_str = data.get("date", "")
                        await app.bot.send_message(
                            uid,
                            f"⚠️ Diqqat: 10 marta ketma-ket bo'sh natija.\n"
                            f"Ehtimol {mon_date_str} sanasi uchun reyslar tugagan.\n\n"
                            f"Kuzatuv davom etadi — joy chiqsa, avtomatik aniqlanadi.\n🆔 `{mid}`",
                            parse_mode="Markdown",
                        )
                        consecutive_empty_date_errors = 0  # qayta ogohlantirish uchun reset
                else:
                    consecutive_empty_date_errors = 0

                found = _find_all_trains(
                    trains, data["car_type"], data.get("max_price"),
                    data.get("time_from", "00:00"), data.get("time_to", "23:59"),
                    data.get("min_seats", 1),
                )

                # SNAPSHOT solishtiruvi: "hozir nima bor" ni "oldingi
                # tekshiruvda nima bor edi"ga solishtiramiz (kumulyativ
                # "qachondir ko'rilganlar" emas) — shu orqali g'oyib bo'lib
                # qayta paydo bo'lgan joy to'g'ri "yangi" deb aniqlanadi.
                current_fingerprints = {_train_fingerprint(f) for f in found}
                new_fingerprints = current_fingerprints - previous_fingerprints

                if new_fingerprints:
                    # Faqat birinchi safar HAMMASI, keyingi safarlarda faqat
                    # HAQIQATAN yangi (oldingi tekshiruvda yo'q edi) paydo
                    # bo'lgan variantlar ko'rsatiladi.
                    to_show = found if first_run else [
                        f for f in found if _train_fingerprint(f) in new_fingerprints
                    ]
                    link = "https://eticket.railway.uz"
                    header = (
                        f"📋 *Hozirda mavjud biletlar ({len(to_show)} ta variant):*\n"
                        if first_run else
                        f"🎯 *Yangi joy topildi! ({len(to_show)} ta variant)*\n"
                    )
                    lines = [header]
                    prev_key = None
                    for train, car, price, tariff_seats, service_type in to_show:
                        dep_date, time_str = _split_dt(train.get("departureDate", ""))
                        arr_date, arr_time = _split_dt(train.get("arrivalDate", ""))
                        number = train.get("number", "")
                        # Kelish boshqa kunga tushsa (tungi poyezd) — kunini ham ko'rsatamiz
                        arr_str = arr_time if (not arr_date or arr_date == dep_date) else f"{arr_date[:5]} {arr_time}"
                        key = (number, dep_date, time_str)
                        if key != prev_key:
                            lines.append(
                                f"🚂 *{train.get('brand','')} {number}*\n"
                                f"   📅 {dep_date or _fmt_iso_date(data.get('date'))}  ⏰ {time_str} → {arr_str}"
                            )
                            prev_key = key
                        ctype_label = car.get("type") if isinstance(car, dict) else None
                        label = f"{ctype_label} ({service_type})" if ctype_label and ctype_label != service_type else service_type
                        lines.append(f"   💺 {label}: {tariff_seats} joy | 💰 {price:,} so'm")

                    lines.append(f"\n🚉 {data['from_name']} → {data['to_name']}  |  📅 {_fmt_iso_date(data.get('date'))}")
                    lines.append(f"\n👉 [Bilet sotib olish]({link})")
                    if not first_run:
                        lines.append(f"\n_Kuzatuv to'xtatildi: {mid}_")

                    await app.bot.send_message(
                        uid, "\n".join(lines),
                        parse_mode="Markdown",
                        disable_web_page_preview=True,
                    )
                    if not first_run:
                        # Mahsulot qoidasi (ataylab saqlangan): birinchi
                        # tekshiruvdan KEYIN haqiqatan yangi mos joy
                        # topilsa, monitor bir marta xabar berib to'xtaydi
                        # (foydalanuvchi o'zi saytdan sotib oladi).
                        if not db.deactivate(mid):
                            logger.critical(
                                f"mid={mid}: joy topilgani uchun deactivate yozib "
                                "bo'lmadi — DB hali 'faol' deb ko'rsatishi mumkin"
                            )
                        logger.info(f"Yangi joy topildi, monitor to'xtatildi: mid={mid}")
                        return
                elif first_run:
                    await app.bot.send_message(
                        uid,
                        f"ℹ️ Hozircha mos bilet yo'q.\n🚉 {data['from_name']} → {data['to_name']}  |  📅 {_fmt_iso_date(data.get('date'))}\n"
                        f"Har {Config.CHECK_INTERVAL} soniyada kuzatib boraman...\n🆔 `{mid}`",
                        parse_mode="Markdown",
                    )
                # Snapshot'ni HAR DOIM joriy holatga yangilaymiz (topilsin,
                # topilmasin) — bu eski `seen |= fingerprints` kumulyativ
                # xatoning aynan o'rnini bosuvchi qator.
                previous_fingerprints = current_fingerprints
                first_run = False

            cycle_duration = time.monotonic() - cycle_start
            metrics.set_gauge("monitor_cycle_seconds", cycle_duration)
            if cycle_duration > Config.CHECK_INTERVAL:
                # Va'da qilingan "har N soniyada tekshiraman" bilan haqiqiy
                # xatti-harakat orasidagi tafovutni ko'rinadigan qilish uchun.
                logger.warning(
                    f"mid={mid}: tekshiruv sikli {cycle_duration:.1f}s davom etdi "
                    f"(va'da qilingan CHECK_INTERVAL={Config.CHECK_INTERVAL}s dan uzoqroq) — "
                    "navbat/qayta urinish kechikishi haqiqiy monitoring oralig'ini oshirmoqda."
                )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Monitor xato mid={mid}: {e}")
            first_run = False

        await asyncio.sleep(sleep_duration)

    logger.info(f"Monitor tugadi: mid={mid}")


# ─── Filtr funksiyalari ──────────────────────────────────────────────────────────
def _time_in_range(dep_date_str: str, t_from: str, t_to: str) -> bool:
    if t_from == "00:00" and t_to == "23:59":
        return True
    try:
        parts = dep_date_str.strip().split(" ")
        if len(parts) < 2:
            return True  # vaqt qismi yo'q — poyezdni filtrlab tashlamaymiz
        h, m = map(int, parts[1].split(":"))
        dep_min = h * 60 + m
        def to_min(t):
            hh, mm = map(int, t.split(":")); return hh * 60 + mm
        f_min = to_min(t_from); t_min = to_min(t_to)
        return dep_min >= f_min and dep_min <= t_min if f_min <= t_min else dep_min >= f_min or dep_min <= t_min
    except Exception:
        return True


# (train_number, anomaly_turi) -> har bir alohida sxema anomaliyasini faqat
# BIR MARTA log qilish uchun (aks holda bir xil anomaliya har tekshiruv
# siklida qayta-qayta loglarni to'ldirib yuboradi).
_logged_schema_anomalies: set = set()


def _log_schema_anomaly_once(number: str, kind: str, detail: str):
    key = (number, kind)
    if key in _logged_schema_anomalies:
        return
    _logged_schema_anomalies.add(key)
    metrics.incr(f"schema_anomalies_{kind}_total")
    logger.warning(f"⚠️ API sxema anomaliyasi [{kind}] poyezd {number}: {detail}")


# Diagnostika: API javobida kutilgan shaklda bo'lmagan / jim o'tkazib yuborilgan
# poyezdning XOM ko'rinishini (ommaviy bilet ma'lumoti, maxfiy emas) har bir
# poyezd+sana+sabab uchun faqat BIR MARTA log qiladi — "bilet bor edi, lekin bot
# topmadi" kabi holatlarda haqiqiy API sxemasini ko'rib, aniq tuzatish uchun.
_diagnosed_trains: set = set()
_DIAG_MAX_RAW = 1800


def _diagnose_train_once(train: dict, reason: str):
    key = (train.get("number"), train.get("departureDate"), reason)
    if key in _diagnosed_trains:
        return
    if len(_diagnosed_trains) > 500:
        _diagnosed_trains.clear()
    _diagnosed_trains.add(key)
    metrics.incr(f"diagnosed_trains_{reason}_total")
    try:
        raw = json.dumps(train, ensure_ascii=False, default=str)
    except Exception:
        raw = repr(train)
    logger.warning(
        f"🔎 DIAGNOSTIKA [{reason}] poyezd {train.get('number')} "
        f"({train.get('brand')}): kalitlar={sorted(train.keys())} "
        f"raw={raw[:_DIAG_MAX_RAW]}"
    )


def _any_tariff_has_seats(tariffs) -> bool:
    """Vagon darajasidagi freeSeats 0/yo'q bo'lsa ham, tariflardan birida
    bo'sh joy bo'lishi mumkin (ba'zi poyezd turlarida joy soni faqat tarif
    darajasida beriladi). Shunda vagonni jim tashlab yubormaymiz."""
    if not isinstance(tariffs, list):
        return False
    for t in tariffs:
        if isinstance(t, dict):
            try:
                if int(t.get("freeSeats", 0)) > 0:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _all_tariffs_zero(tariffs) -> bool:
    """Barcha tariflarning freeSeats qiymati 0/yo'q/noto'g'rimi?

    HAQIQIY API (diagnostikadan): oddiy yo'lovchi poyezdlarida (Plaskartli/Kupe/SV)
    tarif darajasidagi freeSeats DOIM 0, haqiqiy bo'sh joy soni esa VAGON
    darajasidagi freeSeats'da keladi (masalan 143). Afrosiyob/Sharq'da esa
    aksincha — joy soni tarif (1С/2Е/...) darajasida beriladi. Shu sabab
    "barcha tarif 0, lekin vagonda joy bor" holatida vagon sonini ishlatamiz;
    aralash holatda (bir tarifda joy bor, boshqasida 0) esa tarif qiymatiga
    ishonamiz — 0 bo'lgani rostdan tugagan sinf."""
    if not isinstance(tariffs, list):
        return False
    for t in tariffs:
        if isinstance(t, dict):
            try:
                if int(t.get("freeSeats", 0)) > 0:
                    return False
            except (TypeError, ValueError):
                continue
    return True


def _find_all_trains(trains, car_type, max_price=None, time_from="00:00", time_to="23:59", min_seats=1):
    """Railway.uz javobidan foydalanuvchi filtriga mos variantlarni ajratadi.

    Himoyalangan (defensive) parsing: API javobi kutilmagan sxemada bo'lsa
    (tariffs yo'q/bo'sh, narx noto'g'ri formatda va h.k.), funksiya
    QULAMAYDI va soxta/noaniq ma'lumotli "bilet"ni HAM e'lon qilmaydi —
    buning o'rniga anomaliyani (bir marta) log qiladi, hisoblagichni
    oshiradi va o'sha YOZUVNI o'tkazib yuboradi, qolgan ma'lumotlarni
    ishlashda davom etadi."""
    keywords    = CAR_TYPE_KEYWORDS.get(car_type, [])
    brand_kws   = BRAND_FILTERS.get(car_type)  # None bo'lsa brand filtri yo'q
    results = []
    trains = trains or []
    logger.info(
        f"Filtr: car_type={car_type}, vaqt={time_from}–{time_to}, "
        f"max_price={max_price}, min_seats={min_seats}, jami={len(trains)}"
    )

    for train in trains:
        if not isinstance(train, dict):
            _log_schema_anomaly_once("?", "malformed_train_entry", f"kutilmagan tur: {type(train).__name__}")
            continue

        dep    = train.get("departureDate", "") or ""
        brand  = (train.get("brand") or "").lower()
        number = train.get("number", "") or "?"

        if not _time_in_range(dep, time_from, time_to):
            logger.info(f"  ⏭ {number} — vaqt {dep} oralig'dan tashqarida")
            continue

        if brand_kws and not any(kw in brand for kw in brand_kws):
            logger.info(f"  ⏭ {number} [{train.get('brand')}] — {CAR_TYPES.get(car_type)} emas")
            continue

        cars = train.get("cars")
        if not cars or not isinstance(cars, list):
            logger.info(f"  ⏭ {train.get('brand')} {number} — cars bo'sh")
            _diagnose_train_once(train, "cars_empty")
            continue

        results_before = len(results)

        for car in cars:
            if not isinstance(car, dict):
                _log_schema_anomaly_once(number, "malformed_car_entry", f"kutilmagan tur: {type(car).__name__}")
                continue

            free_raw = car.get("freeSeats", 0)
            try:
                free = int(free_raw)
            except (TypeError, ValueError):
                _log_schema_anomaly_once(number, "malformed_free_seats", f"freeSeats={free_raw!r}")
                continue

            ctype_raw = car.get("type") or ""
            if not isinstance(ctype_raw, str):
                ctype_raw = str(ctype_raw)
            if free <= 0 and not _any_tariff_has_seats(car.get("tariffs")):
                logger.info(f"  ⏭ {number} [{ctype_raw}] — vagonda bo'sh joy yo'q (freeSeats={free_raw!r})")
                continue

            # Diagnostika: bu raw qiymat hech qaysi ma'lum kategoriyaga mos
            # kelmasa, bir marta ko'rinadigan tarzda log qilamiz — lekin
            # haqiqiy FILTRLASH quyida eski (ishlab turgan) usul bilan davom
            # etadi, bu funksiya natijasi faqat kuzatuv/diagnostika uchun.
            if normalize_car_type(ctype_raw) == "unknown" and not brand_kws:
                _log_unknown_car_type_once(ctype_raw, number)

            if keywords and not brand_kws:
                if not any(kw in ctype_raw.lower() for kw in keywords):
                    logger.info(f"  ⏭ {number} [{ctype_raw}] — tur mos kelmadi")
                    continue

            tariffs = car.get("tariffs")
            if not tariffs:
                # Vagonda bo'sh joy bor (freeSeats>0), lekin tarif ro'yxati
                # yo'q/bo'sh — narx/sinfni ishonchli bilmasdan "bilet" deb
                # E'LON QILMAYMIZ (noto'g'ri narx ko'rsatish xavfi), lekin
                # bu holatni ham JIM yo'qotib yubormaymiz — ko'rinadigan
                # tarzda log qilib, operator keyinchalik sxemani ko'rib
                # chiqishi mumkin bo'ladi.
                _log_schema_anomaly_once(
                    number, "missing_tariffs",
                    f"car.type={ctype_raw!r} freeSeats={free} lekin tariffs bo'sh/yo'q",
                )
                continue
            if not isinstance(tariffs, list):
                _log_schema_anomaly_once(number, "malformed_tariffs", f"kutilmagan tur: {type(tariffs).__name__}")
                continue

            tariffs_all_zero = _all_tariffs_zero(tariffs)
            for tariff in tariffs:
                if not isinstance(tariff, dict):
                    _log_schema_anomaly_once(number, "malformed_tariff_entry", f"kutilmagan tur: {type(tariff).__name__}")
                    continue

                price_raw = tariff.get("tariff", 0)
                try:
                    price = int(price_raw)
                except (TypeError, ValueError):
                    _log_schema_anomaly_once(number, "malformed_price", f"tariff={price_raw!r}")
                    continue

                seats_raw = tariff.get("freeSeats", free)
                try:
                    tariff_seats = int(seats_raw)
                except (TypeError, ValueError):
                    _log_schema_anomaly_once(number, "malformed_tariff_seats", f"freeSeats={seats_raw!r}")
                    continue
                if tariff_seats <= 0 < free and tariffs_all_zero:
                    # Oddiy yo'lovchi poyezdi: tarif darajasida joy soni 0,
                    # haqiqiy son vagon darajasida (qarang _all_tariffs_zero).
                    tariff_seats = free

                service_type = tariff.get("classServiceType", ctype_raw) or ctype_raw
                if not isinstance(service_type, str):
                    service_type = str(service_type)

                if max_price is not None and price > max_price:
                    logger.info(f"  ⏭ {number} [{service_type}] — narx {price:,} > maks {max_price:,}")
                    continue
                if tariff_seats < min_seats:
                    if tariff_seats > 0:
                        logger.info(
                            f"  ⏭ {number} [{service_type}] — {tariff_seats} joy, "
                            f"kerakli {min_seats} tadan kam"
                        )
                    continue
                logger.info(f"  ✅ {number} [{service_type}] {dep} — {tariff_seats} joy, {price:,} so'm")
                results.append((train, car, price, tariff_seats, service_type))

        if len(results) == results_before and car_type == "any" and max_price is None and min_seats <= 1:
            # Hech qanday cheklov qo'yilmagan, lekin poyezdning vagonlari bor-u
            # birorta ham variant chiqmadi — bu "bilet bor edi, bot topmadi"
            # holatining belgisi bo'lishi mumkin; xom ko'rinishini bir marta log qilamiz.
            _diagnose_train_once(train, "no_result_with_cars")

    return results


# ─── Main ────────────────────────────────────────────────────────────────────────
async def _resume_monitors(app):
    """Bot (qayta) ishga tushganda data.json dagi faol kuzatuvlarni davom ettirish.
    Aks holda Railway restartidan keyin kuzatuvlar 'faol' ko'rinsa ham ishlamaydi."""
    expired = db.deactivate_expired(datetime.now().strftime("%Y-%m-%d"))
    if expired:
        logger.info(f"🗑 {len(expired)} ta muddati o'tgan kuzatuv avtomatik o'chirildi")
    monitors = db.get_all_active_monitors()
    for m in monitors:
        _spawn_monitor(m["uid"], m["id"], m, app)
    if monitors:
        logger.info(f"♻️ {len(monitors)} ta faol kuzatuv restartdan keyin tiklandi")


def main():
    lock_fd = acquire_lock()  # Faqat bitta instance

    Config.validate()
    if os.getenv("RAILWAY_ENVIRONMENT") and Config.DATA_DIR in (".", ""):
        logger.warning(
            "⚠️ DATA_DIR sozlanmagan — Railway'da data.json har redeploy'da O'CHIB KETADI! "
            "Service → Volume ulang (mount path: /data) va Variables'ga DATA_DIR=/data qo'ying."
        )
    app = (
        Application.builder()
        .token(Config.BOT_TOKEN)
        .post_init(_resume_monitors)
        .post_shutdown(_shutdown_monitors)
        .build()
    )

    monitor_conv = ConversationHandler(
        entry_points=[CommandHandler("monitor", monitor_start)],
        states={
            WAIT_FROM: [CallbackQueryHandler(got_from, pattern=r"^from\|")],
            WAIT_TO:   [CallbackQueryHandler(got_to,   pattern=r"^to\|")],
            WAIT_DATE: [
                CallbackQueryHandler(cal_navigate, pattern=r"^cal_(prev|next)\|"),
                CallbackQueryHandler(cal_pick,     pattern=r"^cal_pick\|"),
                CallbackQueryHandler(cal_ignore,   pattern=r"^cal_ignore$"),
            ],
            WAIT_CAR_TYPE:   [CallbackQueryHandler(got_car_type,   pattern=r"^car\|")],
            WAIT_TIME_RANGE: [
                CallbackQueryHandler(got_time_range, pattern=r"^time\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_custom_time),
            ],
            WAIT_MAX_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_max_price),
                CommandHandler("skip", got_max_price),
            ],
            WAIT_MIN_SEATS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_min_seats),
                CommandHandler("skip", got_min_seats),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("monitor", monitor_start),  # qayta /monitor — eskisini bekor qilib qayta boshlaydi
        ],
        conversation_timeout=180,
    )

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("privacy", cmd_privacy))
    app.add_handler(CommandHandler("stop",   cmd_stop))
    app.add_handler(CommandHandler("logs",   cmd_logs))
    app.add_handler(CommandHandler("metrics", cmd_metrics))
    app.add_handler(CommandHandler("list",   cmd_list))
    app.add_handler(monitor_conv)

    # Admin: foydalanuvchilarni boshqarish
    app.add_handler(CommandHandler("addUser",    cmd_add_user))
    app.add_handler(CommandHandler("addUsers",   cmd_add_users))
    app.add_handler(CommandHandler("removeUser", cmd_remove_user))
    app.add_handler(CommandHandler("users",      cmd_users))
    app.add_handler(CallbackQueryHandler(usr_show, pattern=r"^usr_show\|"))
    app.add_handler(CallbackQueryHandler(usr_del,  pattern=r"^usr_del\|"))
    app.add_handler(CallbackQueryHandler(usr_back, pattern=r"^usr_back$"))

    # /list manager handlers
    app.add_handler(CallbackQueryHandler(mgr_show,       pattern=r"^mgr_show\|"))
    app.add_handler(CallbackQueryHandler(mgr_del,        pattern=r"^mgr_del\|"))
    app.add_handler(CallbackQueryHandler(mgr_edit,       pattern=r"^mgr_edit\|"))
    app.add_handler(CallbackQueryHandler(mgr_edit_field, pattern=r"^mgr_ef\|"))
    app.add_handler(CallbackQueryHandler(mgr_time_value, pattern=r"^mgr_tv\|"))
    app.add_handler(CallbackQueryHandler(mgr_car_value,  pattern=r"^mgr_cv\|"))
    app.add_handler(CallbackQueryHandler(mgr_back,       pattern=r"^mgr_back$"))
    app.add_handler(CallbackQueryHandler(mgr_cal_pick,   pattern=r"^cal_pick\|"))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        mgr_edit_text,
    ))
    app.add_handler(CommandHandler("skip", mgr_edit_skip))
    app.add_handler(MessageHandler(filters.CONTACT, got_contact))

    app.add_error_handler(error_handler)

    logger.info("🚆 Railway Monitor Bot ishga tushdi!")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

    lock_fd.close()


if __name__ == "__main__":
    main()
