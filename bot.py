"""
🚆 Railway Monitor Bot
Xavfsiz monitoring bot — joy chiqsa Telegram xabar yuboradi
"""

import asyncio
import logging
import sys
import os
import calendar
from datetime import datetime, timedelta

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

# ─── Konstanta ──────────────────────────────────────────────────────────────────
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

CAR_TYPES = {
    "platskar":  "🪑 Platskart",
    "coupe":     "🛏 Kupe",
    "sv":        "💺 SV / Lyuks",
    "afrosiyob": "🚄 Afrosiyob",
    "any":       "🔀 Barchasi",
}

CAR_TYPE_KEYWORDS = {
    "platskar":  ["o'rindiq", "ўриндиқ", "platskart", "seat"],
    "coupe":     ["yotoq", "ётоқ", "kupe", "купе", "compart"],
    "sv":        ["sv", "lyuks", "люкс", "vip"],
    "afrosiyob": ["afrosiyob", "афросиёб"],
    "any":       [],
}

TIME_RANGES = {
    "any":      ("00:00", "23:59", "🕐 Istalgan vaqt"),
    "morning":  ("06:00", "11:59", "🌅 Ertalab 06:00–12:00"),
    "day":      ("12:00", "17:59", "☀️ Kunduz 12:00–18:00"),
    "evening":  ("18:00", "23:59", "🌆 Kechqurun 18:00–00:00"),
    "night":    ("00:00", "05:59", "🌙 Tunda 00:00–06:00"),
    "custom":   (None,    None,    "✏️ O'zim kiritaman"),
}

ADMIN_ID = 370898987

db = Database()
security = SecurityMiddleware()


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
    rows = []

    # Sarlavha — oy/yil + navigatsiya
    month_names = ["", "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
                   "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr"]
    rows.append([
        InlineKeyboardButton("◀️", callback_data=f"cal_prev|{year}|{month}"),
        InlineKeyboardButton(f"{month_names[month]} {year}", callback_data="cal_ignore"),
        InlineKeyboardButton("▶️", callback_data=f"cal_next|{year}|{month}"),
    ])

    # Hafta kunlari
    rows.append([
        InlineKeyboardButton(d, callback_data="cal_ignore")
        for d in ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]
    ])

    # Kunlar
    month_cal = calendar.monthcalendar(year, month)
    for week in month_cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="cal_ignore"))
            else:
                date = datetime(year, month, day).date()
                if date < now:
                    row.append(InlineKeyboardButton("·", callback_data="cal_ignore"))
                elif date == now:
                    row.append(InlineKeyboardButton(f"[{day}]", callback_data=f"cal_pick|{year}-{month:02d}-{day:02d}"))
                else:
                    row.append(InlineKeyboardButton(str(day), callback_data=f"cal_pick|{year}-{month:02d}-{day:02d}"))
        rows.append(row)

    return InlineKeyboardMarkup(rows)


# ─── /start ─────────────────────────────────────────────────────────────────────
@restricted
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    is_admin = update.effective_user.id == ADMIN_ID
    admin_txt = "\n/logs — Loglar (admin)" if is_admin else ""
    await update.message.reply_text(
        f"Salom, {name}! 🚆\n\n"
        "📌 *Buyruqlar:*\n"
        "/monitor — Yangi kuzatuv\n"
        "/list — Faol kuzatuvlar\n"
        "/stop — To'xtatish\n"
        "/help — Yordam" + admin_txt,
        parse_mode="Markdown",
    )


@restricted
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚆 *Railway Monitor Bot*\n\n"
        "1️⃣ /monitor — marshrut, sana, vaqt oralig'i tanlang\n"
        "2️⃣ Bot *hozirda mavjud* biletlarni darhol ko'rsatadi\n"
        "3️⃣ Keyinchalik chiqganlarini ham kuzatib turadi\n"
        "4️⃣ railway.uz ga kirib sotib olasiz\n\n"
        "*Interval:* 60 soniyada bir tekshirish",
        parse_mode="Markdown",
    )


# ─── /logs (admin) ──────────────────────────────────────────────────────────────
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("⛔ Faqat admin uchun.")
        return

    log_file = "bot.log"
    if not os.path.exists(log_file):
        await update.message.reply_text("📭 Log fayl topilmadi.")
        return

    # Oxirgi 50 qatorni yuborish
    with open(log_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    last_lines = lines[-50:] if len(lines) > 50 else lines
    text = "".join(last_lines)

    # 4096 belgidan uzun bo'lsa kesib yuborish
    if len(text) > 4000:
        text = "...(oxirgi qism)...\n" + text[-4000:]

    await update.message.reply_text(
        f"📋 *Bot log (oxirgi {len(last_lines)} qator):*\n\n"
        f"```\n{text}\n```",
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
            f"⚠️ Maksimal {Config.MAX_MONITORS_PER_USER} ta kuzatuv.\n/stop bilan birini to'xtatib qayta urinib ko'ring."
        )
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        "🚉 *Qayerdan* ketasiz?",
        reply_markup=_station_keyboard("from"),
        parse_mode="Markdown",
    )
    return WAIT_FROM


def _station_keyboard(prefix: str) -> InlineKeyboardMarkup:
    keys = list(STATIONS.keys())
    rows = []
    for i in range(0, len(keys), 2):
        row = [InlineKeyboardButton(keys[i], callback_data=f"{prefix}|{keys[i]}")]
        if i + 1 < len(keys):
            row.append(InlineKeyboardButton(keys[i+1], callback_data=f"{prefix}|{keys[i+1]}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


@restricted
async def got_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, name = q.data.split("|", 1)
    context.user_data["from_name"] = name
    context.user_data["from_code"] = STATIONS[name]
    await q.edit_message_text(
        f"✅ *Qayerdan:* {name}\n\n🚉 *Qayerga* ketasiz?",
        reply_markup=_station_keyboard("to"),
        parse_mode="Markdown",
    )
    return WAIT_TO


@restricted
async def got_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, name = q.data.split("|", 1)
    if name == context.user_data.get("from_name"):
        await q.answer("❌ Bir xil stansiya!", show_alert=True)
        return WAIT_TO
    context.user_data["to_name"] = name
    context.user_data["to_code"] = STATIONS[name]

    now = datetime.now()
    await q.edit_message_text(
        f"✅ *Qayerdan:* {context.user_data['from_name']}\n"
        f"✅ *Qayerga:* {name}\n\n"
        "📅 *Sana* tanlang:",
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
        if month < 1:
            month, year = 12, year - 1
    elif action == "cal_next":
        month += 1
        if month > 12:
            month, year = 1, year + 1

    await q.edit_message_reply_markup(
        reply_markup=_calendar_keyboard(year, month)
    )
    return WAIT_DATE


@restricted
async def cal_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, date_str = q.data.split("|", 1)
    context.user_data["date"] = date_str

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🪑 Platskart", callback_data="car|platskar"),
         InlineKeyboardButton("🛏 Kupe", callback_data="car|coupe")],
        [InlineKeyboardButton("💺 SV", callback_data="car|sv"),
         InlineKeyboardButton("🚄 Afrosiyob", callback_data="car|afrosiyob")],
        [InlineKeyboardButton("🔀 Barchasi", callback_data="car|any")],
    ])
    await q.edit_message_text(
        f"✅ *Sana:* {date_str}\n\n🚂 *Vagon turi* tanlang:",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return WAIT_CAR_TYPE


@restricted
async def got_car_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, car = q.data.split("|", 1)
    context.user_data["car_type"] = car

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🕐 Istalgan vaqt", callback_data="time|any")],
        [InlineKeyboardButton("🌅 Ertalab 06:00–12:00", callback_data="time|morning")],
        [InlineKeyboardButton("☀️ Kunduz 12:00–18:00", callback_data="time|day")],
        [InlineKeyboardButton("🌆 Kechqurun 18:00–00:00", callback_data="time|evening")],
        [InlineKeyboardButton("🌙 Tunda 00:00–06:00", callback_data="time|night")],
        [InlineKeyboardButton("✏️ O'zim kiritaman", callback_data="time|custom")],
    ])
    await q.edit_message_text(
        f"✅ *Vagon:* {CAR_TYPES[car]}\n\n⏰ *Vaqt oralig'i* tanlang:",
        reply_markup=keyboard,
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
    context.user_data["time_from"] = t_from
    context.user_data["time_to"] = t_to
    context.user_data["time_label"] = label

    await q.edit_message_text(
        f"✅ *Vaqt:* {label}\n\n"
        "💰 *Maksimal narx* (so'm) kiriting\n"
        "Yoki /skip — cheksiz:",
        parse_mode="Markdown",
    )
    return WAIT_MAX_PRICE


@restricted
async def got_custom_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi o'zi vaqt kiritganda"""
    if context.user_data.get("time_range") != "custom":
        return WAIT_MAX_PRICE

    text = update.message.text.strip()
    try:
        t1, t2 = text.split("-")
        datetime.strptime(t1.strip(), "%H:%M")
        datetime.strptime(t2.strip(), "%H:%M")
        context.user_data["time_from"] = t1.strip()
        context.user_data["time_to"] = t2.strip()
        context.user_data["time_label"] = f"✏️ {t1.strip()}–{t2.strip()}"
    except Exception:
        await update.message.reply_text(
            "❌ Format noto'g'ri. Masalan: `18:00-20:00`",
            parse_mode="Markdown",
        )
        return WAIT_TIME_RANGE

    await update.message.reply_text(
        f"✅ *Vaqt:* {context.user_data['time_label']}\n\n"
        "💰 *Maksimal narx* (so'm) kiriting\n"
        "Yoki /skip — cheksiz:",
        parse_mode="Markdown",
    )
    return WAIT_MAX_PRICE


@restricted
async def got_max_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    max_price = None

    if text != "/skip":
        cleaned = text.replace(" ", "").replace(",", "").replace(".", "")
        if not cleaned.isdigit():
            await update.message.reply_text(
                "❌ Faqat raqam kiriting. Masalan: `250000`\nYoki /skip",
                parse_mode="Markdown",
            )
            return WAIT_MAX_PRICE
        max_price = int(cleaned)
        if max_price < 10000:
            await update.message.reply_text("❌ Narx juda kam.")
            return WAIT_MAX_PRICE

    context.user_data["max_price"] = max_price
    await _confirm_and_start(update, context)
    return ConversationHandler.END


async def _confirm_and_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ud = context.user_data

    time_from = ud.get("time_from", "00:00")
    time_to = ud.get("time_to", "23:59")
    time_label = ud.get("time_label", "🕐 Istalgan vaqt")

    monitor = {
        "uid": uid,
        "from_name": ud["from_name"], "from_code": ud["from_code"],
        "to_name": ud["to_name"],   "to_code": ud["to_code"],
        "date": ud["date"],
        "car_type": ud["car_type"],
        "time_from": time_from, "time_to": time_to, "time_label": time_label,
        "max_price": ud.get("max_price"),
        "active": True,
        "created_at": datetime.now().isoformat(),
        "check_count": 0, "last_check": None,
    }

    mid = db.save_monitor(uid, monitor)
    price_text = f"{ud['max_price']:,} so'm" if ud.get("max_price") else "Cheksiz"

    msg = await update.message.reply_text(
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

    asyncio.create_task(
        _monitor_loop(uid, mid, monitor, context.application)
    )


# ─── Monitor loop ────────────────────────────────────────────────────────────────
async def _monitor_loop(uid: int, mid: str, data: dict, app):
    client = RailwayClient()
    logger.info(f"Monitor boshlandi: uid={uid} mid={mid}")
    first_run = True

    while db.is_active(mid):
        try:
            trains = client.search_trains(
                data["from_code"], data["to_code"], data["date"],
            )
            db.increment_check(mid)

            found = _find_all_trains(
                trains,
                data["car_type"],
                data.get("max_price"),
                data.get("time_from", "00:00"),
                data.get("time_to", "23:59"),
            )

            if found:
                link = "https://eticket.railway.uz"
                if first_run:
                    header = f"📋 *Hozirda mavjud biletlar ({len(found)} ta):*\n"
                else:
                    header = f"🎯 *Yangi joy topildi! ({len(found)} ta poyezd)*\n"

                lines = [header]
                for train, car, price in found:
                    dep = train.get("departureDate", "")
                    arr = train.get("arrivalDate", "")
                    time_str = ""
                    if dep:
                        parts = dep.split(" ")
                        time_str = parts[1] if len(parts) > 1 else dep
                    lines.append(
                        f"🚂 *{train.get('brand', '')} {train.get('number', '')}*\n"
                        f"   ⏰ {time_str} → {arr.split(' ')[1] if arr and ' ' in arr else ''}\n"
                        f"   💺 {car.get('freeSeats', '?')} joy | 💰 {price:,} so'm\n"
                    )
                lines.append(f"🚉 {data['from_name']} → {data['to_name']}")
                lines.append(f"\n👉 [Bilet sotib olish]({link})")

                if not first_run:
                    lines.append(f"\n_Kuzatuv ID: {mid} — to'xtatildi_")

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
                    f"ℹ️ Hozircha mos bilet yo'q.\n"
                    f"Har 60 soniyada kuzatib boraman...\n"
                    f"🆔 `{mid}`",
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
    """Poyezdning jo'nash vaqti oralig'da ekanini tekshirish"""
    if t_from == "00:00" and t_to == "23:59":
        return True
    try:
        # "19.06.2026 18:30" yoki "2026-06-19 18:30"
        parts = dep_date_str.strip().split(" ")
        time_part = parts[1] if len(parts) > 1 else "00:00"
        h, m = map(int, time_part.split(":"))
        dep_minutes = h * 60 + m

        def to_min(t):
            hh, mm = map(int, t.split(":"))
            return hh * 60 + mm

        from_min = to_min(t_from)
        to_min_val = to_min(t_to)

        if from_min <= to_min_val:
            return from_min <= dep_minutes <= to_min_val
        else:
            # Kechagi oraliq (masalan 22:00-02:00)
            return dep_minutes >= from_min or dep_minutes <= to_min_val
    except Exception:
        return True


def _find_all_trains(trains, car_type, max_price=None, time_from="00:00", time_to="23:59"):
    keywords = CAR_TYPE_KEYWORDS.get(car_type, [])
    results = []
    logger.info(f"Filtr: car_type={car_type}, vaqt={time_from}–{time_to}, max_price={max_price}, jami={len(trains)}")

    for train in trains:
        dep = train.get("departureDate", "")

        # Vaqt oralig'i filtri
        if not _time_in_range(dep, time_from, time_to):
            logger.info(f"  ⏭ {train.get('number')} — vaqt {dep} oralig'dan tashqarida")
            continue

        cars = train.get("cars", [])
        if not cars:
            logger.info(f"  ⏭ {train.get('brand')} {train.get('number')} — cars bo'sh")
            continue

        for car in cars:
            free = car.get("freeSeats", 0)
            ctype_raw = car.get("type", "")

            if free <= 0:
                continue

            if keywords:
                if not any(kw in ctype_raw.lower() for kw in keywords):
                    logger.info(f"  ⏭ {train.get('number')} [{ctype_raw}] — tur mos kelmadi")
                    continue

            best_price = None
            for tariff in car.get("tariffs", []):
                price = tariff.get("tariff", 0)
                if max_price is None or price <= max_price:
                    if best_price is None or price < best_price:
                        best_price = price

            if best_price is None:
                continue

            logger.info(f"  ✅ {train.get('number')} [{ctype_raw}] {dep} — {free} joy, {best_price:,} so'm")
            results.append((train, car, best_price))

    return results


# ─── /list ──────────────────────────────────────────────────────────────────────
@restricted
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    monitors = db.get_active_monitors(uid)
    if not monitors:
        await update.message.reply_text("📭 Faol kuzatuv yo'q.\n/monitor — yangi boshlash")
        return
    lines = [f"📊 *Faol kuzatuvlar ({len(monitors)} ta):*\n"]
    for m in monitors:
        price = f"{m['max_price']:,}" if m.get("max_price") else "∞"
        lines.append(
            f"🆔 `{m['id']}`\n"
            f"   {m['from_name']} → {m['to_name']}\n"
            f"   📅 {m['date']} | ⏰ {m.get('time_from','00:00')}–{m.get('time_to','23:59')}\n"
            f"   💰 {price} so'm | 🔄 {m.get('check_count', 0)} marta\n"
        )
    lines.append("/stop `<id>` — to'xtatish")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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


# ─── Main ────────────────────────────────────────────────────────────────────────
def main():
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
    app.add_handler(CommandHandler("list",   cmd_list))
    app.add_handler(CommandHandler("stop",   cmd_stop))
    app.add_handler(CommandHandler("logs",   cmd_logs))
    app.add_handler(monitor_conv)
    app.add_error_handler(error_handler)

    logger.info("🚆 Railway Monitor Bot ishga tushdi!")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
