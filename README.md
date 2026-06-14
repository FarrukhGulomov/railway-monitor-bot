# 🚆 Railway Monitor Bot

O'zbekiston temir yo'llari (`eticket.railway.uz`) da poyezd joylarini kuzatib, joy chiqqanda Telegram orqali xabar beruvchi bot.

> **Bot faqat kuzatuv qiladi** — bilet sotib olmaydi. Xabar kelgach, siz o'zingiz saytdan olasiz.

---

## Xususiyatlar

- ✅ Poyezd joylarini avtomatik kuzatish
- ✅ Joy chiqqanda darhol Telegram xabar
- ✅ Vagon turi va narx filtri
- ✅ Bir vaqtda bir nechta marshrut kuzatuvi
- ✅ Foydalanuvchi whitelisti (xavfsizlik)
- ✅ Rate limiting himoyasi

---

## O'rnatish

### 1. Repozitoriyani klonlash
```bash
git clone https://github.com/SIZNING_USERNAME/railway-monitor-bot.git
cd railway-monitor-bot
```

### 2. Virtual muhit
```bash
python -m venv venv
source venv/bin/activate   # Linux/Mac
venv\Scripts\activate      # Windows
```

### 3. Kutubxonalar
```bash
pip install -r requirements.txt
```

### 4. Sozlamalar
```bash
cp .env.example .env
# .env faylni tahrirlang
```

`.env` faylga quyidagilarni kiriting:
```env
BOT_TOKEN=sizning_bot_tokeningiz
ALLOWED_USERS=sizning_telegram_id_ingiz
```

**Bot token** — [@BotFather](https://t.me/BotFather) dan oling  
**Telegram ID** — [@userinfobot](https://t.me/userinfobot) ga yozing

### 5. Ishga tushirish
```bash
python bot.py
```

---

## Buyruqlar

| Buyruq | Vazifasi |
|--------|---------|
| `/start` | Botni ishga tushirish |
| `/monitor` | Yangi kuzatuv boshlash |
| `/list` | Faol kuzatuvlar ro'yxati |
| `/stop <id>` | Kuzatuvni to'xtatish |
| `/help` | Yordam |

---

## Server (doimiy ishlash uchun)

**Railway.app** yoki **Render.com** da bepul joylashtirish mumkin.

```bash
# Procfile (Railway.app uchun)
echo "worker: python bot.py" > Procfile
```

---

## Muhim

- `.env` faylni **hech qachon** GitHub ga yuklamang
- `data.json` va `bot.log` `.gitignore` da — xavfsiz

---

## Litsenziya

MIT — shaxsiy foydalanish uchun erkin.
