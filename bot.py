"""
🚆 Railway Monitor Bot
Xavfsiz monitoring bot — joy chiqsa Telegram xabar yuboradi
Bron qilmaydi, faqat xabar beradi
"""

import asyncio
import logging
import sys
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

from config import Config
from railway_client import RailwayClient
from database import Database
from security import SecurityMiddleware

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("railway_bot")

# ─── Conversation states ────────────────────────────────────────────────────────
(
    WAIT_FROM,
    WAIT_TO,
    WAIT_DATE,
    WAIT_CAR_TYPE,
    WAIT_MAX_PRICE,
) = range(5)

# ─── Ma'lumotlar ────────────────────────────────────────────────────────────────
STATIONS = {
    "🏙 Toshkent": "2900000",
    "🕌 Samarqand": "2900680",
    "🕌 Buxoro": "2900800",
    "🏔 Andijon": "2900100",
    "🌿 Namangan": "2900200",
    "🌾 Farg'ona": "2900210",
    "🌄 Qarshi": "2900900",
    "🌊 Nukus": "2900350",
    "🌿 Urganch": "2900300",
    "☀️ Termiz": "2901100",
}

CAR_TYPES = {
    "platskar": "🪑 Platskart",
    "coupe": "🛏 Kupe",
    "sv": "💺 SV",
    "afrosiyob": "🚄 Afrosiyob",
    "any": "🔀 Barchasi",
}

db = Database()
security = SecurityMiddleware()


# ─── Dekorator: foydalanuvchi ruxsatini tekshirish ─────────────────────────────
def restricted(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        uid = update.effective_user.id
        if not security.is_allowed(uid):
            logger.warning(f"Ruxsatsiz kirish urinishi: uid={uid}")
            await update.effective_message.reply_text(
                "⛔ Sizga ruxsat yo'q."
            )
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper


def rate_limited(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        uid = update.effective_user.id
        if security.is_rate_limited(uid):
            await update.effective_message.reply_text(
                "⏳ Juda tez bosyapsiz. Biroz kuting."
            )
            return
        return await func(update, context, *args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper


# ─── /start ────────────────────────────────────────────────────────────────────
@restricted
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"Salom, {name}! 🚆\n\n"
        "Men poyezd joylarini kuzatib, joy chiqqanda sizga xabar beraman.\n\n"
        "📌 *Buyruqlar:*\n"
        "/monitor — Yangi kuzatuv boshlash\n"
        "/list — Faol kuzatuvlar\n"
        "/stop — Kuzatuvni to'xtatish\n"
        "/help — Yordam\n",
        parse_mode="Markdown",
    )


# ─── /help ─────────────────────────────────────────────────────────────────────
@restricted
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚆 *Railway Monitor Bot*\n\n"
        "Bot faqat kuzatuv qiladi — joy chiqqanda xabar beradi.\n"
        "Bilet sotib olishni *o'zingiz* qilasiz.\n\n"
        "*Qanday ishlaydi:*\n"
        "1️⃣ /monitor — marshrut va sana kiriting\n"
        "2️⃣ Bot har minutda tekshirib turadi\n"
        "3️⃣ Joy chiqqanda darhol xabar keladi\n"
        "4️⃣ Siz railway.uz ga kirib sotib olasiz\n\n"
        "*Limit:* Bir vaqtda 3 ta kuzatuv\n"
        "*Interval:* 60 soniyada bir marta",
        parse_mode="Markdown",
    )


# ─── /monitor conversation ─────────────────────────────────────────────────────
@restricted
@rate_limited
async def monitor_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    active = db.get_active_monitors(uid)

    if len(active) >= Config.MAX_MONITORS_PER_USER:
        await update.message.reply_text(
            f"⚠️ Maksimal {Config.MAX_MONITORS_PER_USER} ta kuzatuv.\n"
            "Birini to'xtatib, yangi boshlang: /stop"
        )
        return ConversationHandler.END

    context.user_data.clear()
    keyboard = _station_keyboard("from")
    await update.message.reply_text(
        "🚉 *Qayerdan* ketasiz?",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return WAIT_FROM


def _station_keyboard(prefix: str) -> InlineKeyboardMarkup:
    keys = list(STATIONS.keys())
    rows = []
    for i in range(0, len(keys), 2):
        row = [
            InlineKeyboardButton(keys[i], callback_data=f"{prefix}|{keys[i]}")
        ]
        if i + 1 < len(keys):
            row.append(
                InlineKeyboardButton(keys[i + 1], callback_data=f"{prefix}|{keys[i + 1]}")
            )
        rows.append(row)
    return InlineKeyboardMarkup(rows)


@restricted
async def got_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, name = q.data.split("|", 1)
    context.user_data["from_name"] = name
    context.user_data["from_code"] = STATIONS[name]

    keyboard = _station_keyboard("to")
    await q.edit_message_text(
        f"✅ *Qayerdan:* {name}\n\n🚉 *Qayerga* ketasiz?",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return WAIT_TO


@restricted
async def got_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, name = q.data.split("|", 1)

    if name == context.user_data.get("from_name"):
        await q.answer("❌ Bir xil stansiya tanlayolmaysiz!", show_alert=True)
        return WAIT_TO

    context.user_data["to_name"] = name
    context.user_data["to_code"] = STATIONS[name]

    await q.edit_message_text(
        f"✅ *Qayerdan:* {context.user_data['from_name']}\n"
        f"✅ *Qayerga:* {name}\n\n"
        "📅 *Sana* kiriting:\n`YYYY-MM-DD` formatda\nMasalan: `2025-08-15`",
        parse_mode="Markdown",
    )
    return WAIT_DATE


@restricted
async def got_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        date = datetime.strptime(text, "%Y-%m-%d")
        if date.date() < datetime.now().date():
            await update.message.reply_text("❌ O'tgan sana bo'lmaydi.")
            return WAIT_DATE
    except ValueError:
        await update.message.reply_text(
            "❌ Format noto'g'ri. Masalan: `2025-08-15`",
            parse_mode="Markdown",
        )
        return WAIT_DATE

    context.user_data["date"] = text

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🪑 Platskart", callback_data="car|platskar"),
            InlineKeyboardButton("🛏 Kupe", callback_data="car|coupe"),
        ],
        [
            InlineKeyboardButton("💺 SV", callback_data="car|sv"),
            InlineKeyboardButton("🚄 Afrosiyob", callback_data="car|afrosiyob"),
        ],
        [InlineKeyboardButton("🔀 Barchasi", callback_data="car|any")],
    ])
    await update.message.reply_text(
        f"✅ *Sana:* {text}\n\n🚂 *Vagon turi* tanlang:",
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

    await q.edit_message_text(
        f"✅ *Vagon:* {CAR_TYPES[car]}\n\n"
        "💰 *Maksimal narx* (so'm) kiriting\n"
        "Yoki /skip — narx cheki bo'lmaydi:",
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
                "❌ Faqat raqam kiriting. Masalan: `250000`\n"
                "Yoki /skip",
                parse_mode="Markdown",
            )
            return WAIT_MAX_PRICE
        max_price = int(cleaned)
        if max_price < 10000:
            await update.message.reply_text("❌ Narx juda kam. Kamida 10,000 so'm.")
            return WAIT_MAX_PRICE

    context.user_data["max_price"] = max_price
    await _confirm_and_start(update, context)
    return ConversationHandler.END


async def _confirm_and_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ud = context.user_data

    monitor = {
        "uid": uid,
        "from_name": ud["from_name"],
        "from_code": ud["from_code"],
        "to_name": ud["to_name"],
        "to_code": ud["to_code"],
        "date": ud["date"],
        "car_type": ud["car_type"],
        "max_price": ud.get("max_price"),
        "active": True,
        "created_at": datetime.now().isoformat(),
        "check_count": 0,
        "last_check": None,
    }

    mid = db.save_monitor(uid, monitor)

    price_text = f"{ud['max_price']:,} so'm" if ud.get("max_price") else "Cheksiz"
    await update.message.reply_text(
        f"✅ *Kuzatuv boshlandi!*\n\n"
        f"🚉 {ud['from_name']} → {ud['to_name']}\n"
        f"📅 {ud['date']}\n"
        f"🚂 {CAR_TYPES.get(ud['car_type'], ud['car_type'])}\n"
        f"💰 Maks: {price_text}\n"
        f"🆔 `{mid}`\n\n"
        "⏱ Har 60 soniyada tekshiriladi.\n"
        "Joy chiqqanda darhol xabar olasiz!\n\n"
        "/stop — to'xtatish",
        parse_mode="Markdown",
    )

    asyncio.create_task(
        _monitor_loop(uid, mid, monitor, update._application)
    )


# ─── Monitor loop ───────────────────────────────────────────────────────────────
async def _monitor_loop(uid: int, mid: str, data: dict, app):
    client = RailwayClient()
    logger.info(f"Monitor boshlandi: uid={uid} mid={mid}")

    while db.is_active(mid):
        try:
            trains = client.search_trains(
                data["from_code"],
                data["to_code"],
                data["date"],
            )
            db.increment_check(mid)

            found = _find_train(trains, data["car_type"], data.get("max_price"))

            if found:
                train, car, price = found
                link = "https://eticket.railway.uz"
                msg = (
                    f"🎯 *JOY TOPILDI!*\n\n"
                    f"🚂 {train.get('type', '')} {train.get('number', '')}\n"
                    f"⏰ {train.get('departureDate', data['date'])}\n"
                    f"🚉 {data['from_name']} → {data['to_name']}\n"
                    f"💺 {car.get('freeSeats', '?')} ta joy mavjud\n"
                    f"💰 {price:,} so'm\n\n"
                    f"👉 [Bilet sotib olish]({link})\n\n"
                    f"_Kuzatuv ID: {mid} — to'xtatildi_"
                )
                await app.bot.send_message(
                    uid, msg,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
                db.deactivate(mid)
                logger.info(f"Joy topildi, monitor to'xtatildi: mid={mid}")
                return

        except Exception as e:
            logger.error(f"Monitor xato mid={mid}: {e}")

        await asyncio.sleep(Config.CHECK_INTERVAL)

    logger.info(f"Monitor tugadi: mid={mid}")


def _find_train(trains: list, car_type: str, max_price=None):
    for train in trains:
        for car in train.get("cars", []):
            if car.get("freeSeats", 0) <= 0:
                continue
            if car_type != "any":
                ctype = car.get("type", "").lower()
                if car_type not in ctype:
                    continue
            for tariff in car.get("tariffs", []):
                price = tariff.get("tariff", 0)
                if max_price is None or price <= max_price:
                    return (train, car, price)
    return None


# ─── /list ─────────────────────────────────────────────────────────────────────
@restricted
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    monitors = db.get_active_monitors(uid)

    if not monitors:
        await update.message.reply_text(
            "📭 Faol kuzatuv yo'q.\n/monitor — yangi boshlash"
        )
        return

    lines = [f"📊 *Faol kuzatuvlar ({len(monitors)} ta):*\n"]
    for m in monitors:
        price = f"{m['max_price']:,}" if m.get("max_price") else "∞"
        lines.append(
            f"🆔 `{m['id']}`\n"
            f"   {m['from_name']} → {m['to_name']}\n"
            f"   📅 {m['date']} | 💰 {price} so'm\n"
            f"   🔄 {m.get('check_count', 0)} marta tekshirildi\n"
        )
    lines.append("/stop `<id>` — to'xtatish")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─── /stop ─────────────────────────────────────────────────────────────────────
@restricted
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = context.args

    if args:
        mid = args[0].strip()
        if db.deactivate_for_user(uid, mid):
            await update.message.reply_text(f"⏹ Kuzatuv `{mid}` to'xtatildi.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Kuzatuv topilmadi yoki u sizniki emas.")
    else:
        count = db.deactivate_all(uid)
        if count:
            await update.message.reply_text(f"⏹ {count} ta kuzatuv to'xtatildi.")
        else:
            await update.message.reply_text("📭 To'xtatilacak kuzatuv yo'q.")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Bekor qilindi.")
    return ConversationHandler.END


# ─── Error handler ──────────────────────────────────────────────────────────────
async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Xato: {context.error}", exc_info=context.error)


# ─── Main ───────────────────────────────────────────────────────────────────────
def main():
    Config.validate()

    app = Application.builder().token(Config.BOT_TOKEN).build()

    monitor_conv = ConversationHandler(
        entry_points=[CommandHandler("monitor", monitor_start)],
        states={
            WAIT_FROM: [CallbackQueryHandler(got_from, pattern=r"^from\|")],
            WAIT_TO: [CallbackQueryHandler(got_to, pattern=r"^to\|")],
            WAIT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_date)],
            WAIT_CAR_TYPE: [CallbackQueryHandler(got_car_type, pattern=r"^car\|")],
            WAIT_MAX_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_max_price),
                CommandHandler("skip", got_max_price),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        conversation_timeout=300,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(monitor_conv)
    app.add_error_handler(error_handler)

    logger.info("🚆 Railway Monitor Bot ishga tushdi!")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
