"""
🚆 Railway Monitor Bot — v2
"""

import asyncio
import logging
import sys
import os
import fcntl
import calendar
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler,
)

from config import Config
from railway_client import RailwayClient
from database import Database
from security import SecurityMiddleware

# ─── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("railway_bot")

# ─── States ─────────────────────────────────────────────────────────────────────
(
    WAIT_FROM, WAIT_TO, WAIT_DATE,
    WAIT_CAR_TYPE, WAIT_TIME_RANGE, WAIT_MAX_PRICE,
) = range(6)

EDIT_MENU, EDIT_FIELD, EDIT_VALUE = range(6, 9)

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
    "platskar":  ["o'rindiq", "ўриндиқ", "platskart", "seat"],
    "coupe":     ["yotoq", "ётоқ", "kupe", "купе", "compart"],
    "sv":        ["sv", "lyuks", "люкс", "vip"],
    "afrosiyob": [],
    "sharq":     [],
    "any":       [],
}

# Brand orqali filtrlanadigan turlar (vagon type emas, poyezd brendi tekshiriladi)
BRAND_FILTERS = {
    "afrosiyob": ["afrosiyob", "афросиёб"],
    "sharq":     ["sharq", "шарк", "шарқ"],
}


TIME_RANGES = {
    "any":     ("00:00", "23:59", "🕐 Istalgan vaqt"),
    "morning": ("06:00", "11:59", "🌅 Ertalab 06:00–12:00"),
    "day":     ("12:00", "17:59", "☀️ Kunduz 12:00–18:00"),
    "evening": ("18:00", "23:59", "🌆 Kechqurun 18:00–00:00"),
    "night":   ("00:00", "05:59", "🌙 Tunda 00:00–06:00"),
    "custom":  (None, None,       "✏️ O'zim kiritaman"),
}

ADMIN_ID = 370898987
LOCK_FILE = "/tmp/railway_bot.lock"

db = Database()
security = SecurityMiddleware()


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


# ─── Dekoratorlar ───────────────────────────────────────────────────────────────
def restricted(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        uid = update.effective_user.id
        if not security.is_allowed(uid):
            logger.warning(f"Ruxsatsiz: uid={uid}")
            await update.effective_message.reply_text("⛔ Sizga ruxsat yo'q.")
            return ConversationHandler.END
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
    return (
        f"🆔 `{m['id']}`\n"
        f"  🚉 {m['from_name']} → {m['to_name']}\n"
        f"  📅 {m['date']} | ⏰ {m.get('time_from','00:00')}–{m.get('time_to','23:59')}\n"
        f"  🚂 {CAR_TYPES.get(m.get('car_type','any'), m.get('car_type',''))}\n"
        f"  💰 {price} so'm | 🔄 {checks} tekshirildi"
    )


# ─── /start ─────────────────────────────────────────────────────────────────────
@restricted
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    is_admin = update.effective_user.id == ADMIN_ID
    extra = "\n/logs — Loglar (admin)" if is_admin else ""
    await update.message.reply_text(
        f"Salom, {name}! 🚆\n\n"
        "📌 *Buyruqlar:*\n"
        "/monitor — Yangi kuzatuv\n"
        "/list — Faol kuzatuvlar (tahrirlash/o'chirish)\n"
        "/stop — Barchasini to'xtatish\n"
        "/help — Yordam" + extra,
        parse_mode="Markdown",
    )


@restricted
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚆 *Railway Monitor Bot*\n\n"
        "1️⃣ /monitor — sana, marshrut, vaqt tanlang\n"
        "2️⃣ Bot hozirda mavjud biletlarni darhol ko'rsatadi\n"
        "3️⃣ Har minutda kuzatib, yangi chiqqanda xabar beradi\n"
        "4️⃣ /list — kuzatuvlarni ko'rish, tahrirlash, o'chirish\n\n"
        "*Interval:* 60 soniyada bir tekshirish\n"
        "*Limit:* Bir vaqtda 5 ta kuzatuv",
        parse_mode="Markdown",
    )


# ─── /logs ──────────────────────────────────────────────────────────────────────
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
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
        "active": True, "created_at": datetime.now().isoformat(),
        "check_count": 0, "last_check": None,
    }
    mid = db.save_monitor(uid, monitor)
    price_text = f"{ud['max_price']:,} so'm" if ud.get("max_price") else "Cheksiz"
    await update.message.reply_text(
        f"✅ *Kuzatuv boshlandi!*\n\n"
        f"🚉 {ud['from_name']} → {ud['to_name']}\n"
        f"📅 {ud['date']}\n"
        f"🚂 {CAR_TYPES.get(ud['car_type'], ud['car_type'])}\n"
        f"⏰ {time_label}\n"
        f"💰 Maks: {price_text}\n"
        f"🆔 `{mid}`\n\n"
        "⏳ Hozirgi mavjud biletlar tekshirilmoqda...",
        parse_mode="Markdown",
    )
    asyncio.create_task(_monitor_loop(uid, mid, monitor, context.application))


# ─── /list — ko'rish, tahrirlash, o'chirish ─────────────────────────────────────
@restricted
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
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


async def mgr_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, mid = q.data.split("|", 1)
    uid = q.from_user.id
    if db.deactivate_for_user(uid, mid):
        await q.edit_message_text(f"✅ Kuzatuv `{mid}` o'chirildi.", parse_mode="Markdown")
    else:
        await q.edit_message_text("❌ Topilmadi.")


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
            [InlineKeyboardButton("◀️ Orqaga",        callback_data=f"mgr_show|{mid}")],
        ]),
    )


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


async def mgr_time_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, trange = q.data.split("|", 1)
    t_from, t_to, label = TIME_RANGES[trange]
    mid = context.user_data.get("edit_mid")
    uid = q.from_user.id
    db.update_monitor_field(uid, mid, "time_from", t_from)
    db.update_monitor_field(uid, mid, "time_to", t_to)
    db.update_monitor_field(uid, mid, "time_label", label)
    await q.edit_message_text(f"✅ Vaqt oralig'i yangilandi: {label}\n\n/list — ro'yxatga qaytish")


async def mgr_car_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, car = q.data.split("|", 1)
    mid = context.user_data.get("edit_mid")
    uid = q.from_user.id
    db.update_monitor_field(uid, mid, "car_type", car)
    await q.edit_message_text(f"✅ Vagon turi yangilandi: {CAR_TYPES[car]}\n\n/list — ro'yxatga qaytish")


async def mgr_cal_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tahrirlash rejimida sana tanlash"""
    q = update.callback_query
    await q.answer()
    _, date_str = q.data.split("|", 1)
    mid = context.user_data.get("edit_mid")
    field = context.user_data.get("edit_field")
    if field == "date" and mid:
        uid = q.from_user.id
        db.update_monitor_field(uid, mid, "date", date_str)
        await q.edit_message_text(f"✅ Sana yangilandi: {date_str}\n\n/list — ro'yxatga qaytish")
    else:
        # Oddiy /monitor flow
        await cal_pick(update, context)


async def mgr_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    monitors = db.get_active_monitors(uid)
    if not monitors:
        await q.edit_message_text("📭 Faol kuzatuv yo'q.")
        return
    await q.edit_message_text(
        f"📊 *Faol kuzatuvlar ({len(monitors)} ta):*\n\nBoshqarish uchun tanlang:",
        parse_mode="Markdown",
        reply_markup=_monitors_keyboard(monitors),
    )


async def mgr_price_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Narx tahrirlash text input"""
    if context.user_data.get("edit_field") != "price":
        return
    text = update.message.text.strip()
    mid = context.user_data.get("edit_mid")
    uid = update.effective_user.id
    if text == "/skip":
        db.update_monitor_field(uid, mid, "max_price", None)
        await update.message.reply_text("✅ Narx cheki olib tashlandi.\n\n/list — ro'yxatga qaytish")
    else:
        cleaned = text.replace(" ","").replace(",","")
        if not cleaned.isdigit():
            await update.message.reply_text("❌ Faqat raqam yoki /skip")
            return
        db.update_monitor_field(uid, mid, "max_price", int(cleaned))
        await update.message.reply_text(f"✅ Narx yangilandi: {int(cleaned):,} so'm\n\n/list — ro'yxatga qaytish")
    context.user_data.pop("edit_field", None)
    context.user_data.pop("edit_mid", None)


# ─── /stop ──────────────────────────────────────────────────────────────────────
@restricted
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = context.args
    if args:
        mid = args[0].strip()
        if db.deactivate_for_user(uid, mid):
            await update.message.reply_text(f"⏹ `{mid}` to'xtatildi.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Topilmadi.")
    else:
        count = db.deactivate_all(uid)
        await update.message.reply_text(f"⏹ {count} ta kuzatuv to'xtatildi." if count else "📭 Faol kuzatuv yo'q.")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Bekor qilindi.")
    return ConversationHandler.END


async def cal_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Xato: {context.error}", exc_info=context.error)


# ─── Monitor loop ────────────────────────────────────────────────────────────────
async def _monitor_loop(uid: int, mid: str, data: dict, app):
    client = await asyncio.to_thread(RailwayClient)
    logger.info(f"Monitor boshlandi: uid={uid} mid={mid}")
    first_run = True
    consecutive_empty_date_errors = 0

    while db.is_active(mid):
        try:
            # Sana o'tib ketganmi tekshirish
            try:
                mon_date = datetime.strptime(data["date"], "%Y-%m-%d").date()
                if mon_date < datetime.now().date():
                    await app.bot.send_message(
                        uid,
                        f"⚠️ Kuzatuv to'xtatildi — sana ({data['date']}) o'tib ketdi.\n"
                        f"🆔 `{mid}`",
                        parse_mode="Markdown",
                    )
                    db.deactivate(mid)
                    logger.info(f"Sana o'tib ketgani uchun to'xtatildi: mid={mid}")
                    return
            except ValueError:
                pass

            # DB dan yangi ma'lumotlarni olish (tahrirlangan bo'lishi mumkin)
            monitors = db.get_active_monitors(uid)
            current = next((m for m in monitors if m["id"] == mid), None)
            if current:
                data = current

            # Sinxron (bloklovchi) so'rovni alohida threadda bajaramiz —
            # shunda bot va boshqa monitorlar to'xtab qolmaydi
            trains = await asyncio.to_thread(
                client.search_trains, data["from_code"], data["to_code"], data["date"]
            )
            db.increment_check(mid)

            # Agar sayt doimiy 400/bo'sh natija qaytarsa (masalan bugungi
            # kun uchun barcha reyslar o'tib ketgan bo'lsa) — ogohlantirish
            if not trains:
                consecutive_empty_date_errors += 1
                if consecutive_empty_date_errors == 10:
                    mon_date_str = data.get("date", "")
                    is_today = mon_date_str == datetime.now().strftime("%Y-%m-%d")
                    if is_today:
                        await app.bot.send_message(
                            uid,
                            f"⚠️ Diqqat: 10 marta ketma-ket natija kelmadi.\n"
                            f"Ehtimol bugungi ({mon_date_str}) kun uchun barcha "
                            f"reyslar allaqachon jo'nab ketgan.\n\n"
                            f"Sanani ertangi kunga o'zgartirishni xohlasangiz "
                            f"/list orqali tahrirlang.\n🆔 `{mid}`",
                            parse_mode="Markdown",
                        )
            else:
                consecutive_empty_date_errors = 0

            found = _find_all_trains(
                trains, data["car_type"], data.get("max_price"),
                data.get("time_from", "00:00"), data.get("time_to", "23:59"),
            )

            if found:
                link = "https://eticket.railway.uz"
                header = (
                    f"📋 *Hozirda mavjud biletlar ({len(found)} ta variant):*\n"
                    if first_run else
                    f"🎯 *Yangi joy topildi! ({len(found)} ta variant)*\n"
                )
                lines = [header]
                prev_number = None
                for train, car, price, tariff_seats, service_type in found:
                    dep = train.get("departureDate", "")
                    arr = train.get("arrivalDate", "")
                    number = train.get("number", "")
                    time_str = dep.split(" ")[1] if " " in dep else dep
                    arr_str  = arr.split(" ")[1] if " " in arr else arr
                    if number != prev_number:
                        lines.append(f"🚂 *{train.get('brand','')} {number}*\n   ⏰ {time_str} → {arr_str}")
                        prev_number = number
                    lines.append(f"   💺 {service_type}: {tariff_seats} joy | 💰 {price:,} so'm")

                lines.append(f"\n🚉 {data['from_name']} → {data['to_name']}")
                lines.append(f"\n👉 [Bilet sotib olish]({link})")
                if not first_run:
                    lines.append(f"\n_Kuzatuv to'xtatildi: {mid}_")

                await app.bot.send_message(
                    uid, "\n".join(lines),
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
                if not first_run:
                    db.deactivate(mid)
                    logger.info(f"Joy topildi, monitor to'xtatildi: mid={mid}")
                    return
            elif first_run:
                await app.bot.send_message(
                    uid,
                    f"ℹ️ Hozircha mos bilet yo'q.\nHar 60 soniyada kuzatib boraman...\n🆔 `{mid}`",
                    parse_mode="Markdown",
                )
            first_run = False

        except Exception as e:
            logger.error(f"Monitor xato mid={mid}: {e}")
            first_run = False

        await asyncio.sleep(Config.CHECK_INTERVAL)

    logger.info(f"Monitor tugadi: mid={mid}")


# ─── Filtr funksiyalari ──────────────────────────────────────────────────────────
def _time_in_range(dep_date_str: str, t_from: str, t_to: str) -> bool:
    if t_from == "00:00" and t_to == "23:59":
        return True
    try:
        parts = dep_date_str.strip().split(" ")
        time_part = parts[1] if len(parts) > 1 else "00:00"
        h, m = map(int, time_part.split(":"))
        dep_min = h * 60 + m
        def to_min(t):
            hh, mm = map(int, t.split(":")); return hh * 60 + mm
        f_min = to_min(t_from); t_min = to_min(t_to)
        return dep_min >= f_min and dep_min <= t_min if f_min <= t_min else dep_min >= f_min or dep_min <= t_min
    except Exception:
        return True


def _find_all_trains(trains, car_type, max_price=None, time_from="00:00", time_to="23:59"):
    keywords    = CAR_TYPE_KEYWORDS.get(car_type, [])
    brand_kws   = BRAND_FILTERS.get(car_type)  # None bo'lsa brand filtri yo'q
    results = []
    logger.info(f"Filtr: car_type={car_type}, vaqt={time_from}–{time_to}, max_price={max_price}, jami={len(trains)}")

    for train in trains:
        dep    = train.get("departureDate", "")
        brand  = train.get("brand", "").lower()
        number = train.get("number", "")

        if not _time_in_range(dep, time_from, time_to):
            logger.info(f"  ⏭ {number} — vaqt {dep} oralig'dan tashqarida")
            continue

        if brand_kws and not any(kw in brand for kw in brand_kws):
            logger.info(f"  ⏭ {number} [{train.get('brand')}] — {CAR_TYPES.get(car_type)} emas")
            continue

        cars = train.get("cars", [])
        if not cars:
            logger.info(f"  ⏭ {train.get('brand')} {number} — cars bo'sh")
            continue

        for car in cars:
            free     = car.get("freeSeats", 0)
            ctype_raw = car.get("type", "")
            if free <= 0:
                continue
            if keywords and not brand_kws:
                if not any(kw in ctype_raw.lower() for kw in keywords):
                    logger.info(f"  ⏭ {number} [{ctype_raw}] — tur mos kelmadi")
                    continue

            for tariff in car.get("tariffs", []):
                price         = tariff.get("tariff", 0)
                tariff_seats  = tariff.get("freeSeats", free)
                service_type  = tariff.get("classServiceType", ctype_raw)
                if max_price is not None and price > max_price:
                    logger.info(f"  ⏭ {number} [{service_type}] — narx {price:,} > maks {max_price:,}")
                    continue
                if tariff_seats <= 0:
                    continue
                logger.info(f"  ✅ {number} [{service_type}] {dep} — {tariff_seats} joy, {price:,} so'm")
                results.append((train, car, price, tariff_seats, service_type))

    return results


# ─── Main ────────────────────────────────────────────────────────────────────────
def main():
    lock_fd = acquire_lock()  # Faqat bitta instance

    Config.validate()
    app = Application.builder().token(Config.BOT_TOKEN).build()

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
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        conversation_timeout=300,
    )

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("stop",   cmd_stop))
    app.add_handler(CommandHandler("logs",   cmd_logs))
    app.add_handler(CommandHandler("list",   cmd_list))
    app.add_handler(monitor_conv)

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
        mgr_price_text,
    ))

    app.add_error_handler(error_handler)

    logger.info("🚆 Railway Monitor Bot ishga tushdi!")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

    lock_fd.close()


if __name__ == "__main__":
    main()
