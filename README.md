# 🚆 Railway Monitor Bot

O'zbekiston temir yo'llari (`eticket.railway.uz`) da poyezd joylarini kuzatib, joy chiqqanda Telegram orqali xabar beruvchi bot.

> **Bot faqat kuzatuv qiladi** — bilet sotib olmaydi. Xabar kelgach, siz o'zingiz saytdan olasiz.

---

## Xususiyatlar

- ✅ Poyezd joylarini avtomatik kuzatish
- ✅ Joy chiqqanda darhol Telegram xabar
- ✅ Vagon turi, narx va joy soni filtri
- ✅ Bir vaqtda bir nechta marshrut kuzatuvi
- ✅ Admin panel: foydalanuvchi qo'shish/o'chirish, faoliyatini kuzatish
- ✅ Fail-closed kirish nazorati (admin ID siz bot ishga tushmaydi)
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
ADMIN_IDS=sizning_telegram_id_ingiz
```

**Bot token** — [@BotFather](https://t.me/BotFather) dan oling  
**Telegram ID** — [@userinfobot](https://t.me/userinfobot) ga yozing

> ⚠️ **`ADMIN_IDS` MAJBURIY.** Agar u bo'sh bo'lsa, bot `Config.validate()`
> bosqichida xato ko'tarib ishga tushishdan bosh tortadi — bu ataylab shunday:
> kirish nazorati konfiguratsiyasi aniq bo'lmagan holatda bot "hamma
> ruxsatli" ochiq rejimda jim qolib ketmasligi kerak.

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
| `/privacy` | Qanday ma'lumot saqlanishi haqida (hammaga ochiq) |

### Admin buyruqlari (`ADMIN_IDS` da ko'rsatilgan foydalanuvchilar uchun)

| Buyruq | Vazifasi |
|--------|---------|
| `/addUser <id> [ism]` | Bitta Telegram foydalanuvchini botga qo'shish |
| `/addUsers <id1> <id2> ...` | Bir nechta foydalanuvchini birdaniga qo'shish |
| `/users` | Qo'shilgan foydalanuvchilar va ularning faoliyati (oxirgi faollik, kuzatuvlar soni) |
| `/removeUser <id>` | Foydalanuvchini botdan o'chirish |
| `/logs` | Bot loglarini ko'rish |

---

## Server (doimiy ishlash uchun)

**Railway.app** yoki **Render.com** da bepul joylashtirish mumkin.

```bash
# Procfile (Railway.app uchun)
echo "worker: python bot.py" > Procfile
```

Railway **Variables** bo'limida quyidagilarni qo'ying:

| Variable | Qiymat |
|----------|--------|
| `BOT_TOKEN` | BotFather'dan olingan token |
| `ADMIN_IDS` | Admin Telegram ID lari (vergul bilan) |
| `TZ` | `Asia/Tashkent` — sana/vaqt O'zbekiston vaqtida ishlashi uchun |
| `DATA_DIR` | `/data` — ma'lumotlar Volume'da saqlanishi uchun (quyida) |

### ⚠️ MUHIM: Railway'da Volume ulash (ma'lumotlar o'chib ketmasligi uchun)

Railway konteynerining diski **vaqtinchalik** — har redeploy'da tozalanadi.
Volume ulamasangiz, qo'shilgan foydalanuvchilar va kuzatuvlar har deploy'da o'chib ketadi!

1. Railway'da servisingizni oching → o'ng tomonda **Settings** yoki servis kartochkasida o'ng tugma → **Attach Volume** (yoki **+ New** → **Volume**)
2. **Mount path** ga `/data` yozing
3. **Variables** bo'limiga `DATA_DIR=/data` qo'shing
4. Redeploy qiling — endi `data.json` Volume'da saqlanadi va deploy'lar orasida yo'qolmaydi

> ♻️ Volume ulangandan keyin: bot restart bo'lganda faol kuzatuvlar avtomatik davom etadi,
> sanasi o'tib ketganlari esa ro'yxatdan avtomatik olib tashlanadi.

### ⚠️ MUHIM: Faqat 1 ta replika (instance)

Bot Telegram bilan **polling** orqali ishlaydi (webhook emas) va holatini
**lokal JSON fayl** + `/tmp` fayl qulfi orqali boshqaradi. Bu ikkalasi ham
**bitta process** haqida taxmin qiladi:

- Ikkita instance bir xil Telegram tokendan polling qilsa, Telegram
  ikkalasiga ham bir xil xabarlarni yuborishga urinadi — duplikat javoblar,
  poyezd qidiruv so'rovlari ikki barobar ko'payadi.
- `/tmp/railway_bot.lock` fayl qulfi **faqat bitta konteyner ICHIDA**
  ikkinchi process ishga tushishining oldini oladi (masalan xato bilan
  qayta ishga tushirilgan eski process). U **konteynerlar orasida**
  himoya bermaydi — Railway'da 2 ta alohida replika ishga tushirilsa,
  har biri o'zining `/tmp` fayl tizimiga ega bo'lgani uchun ikkalasi ham
  qulfni muvaffaqiyatli oladi va PARALEL ishlaydi, bu esa yuqoridagi
  muammolarga olib keladi.

**Xulosa:** Railway'da service uchun replika sonini har doim **1** da
qoldiring (standart holat — Railway avtomatik ko'paytirmaydi, lekin
qo'lda oshirmang).

---

## Production Checklist

Ishga tushirishdan oldin:

- [ ] `BOT_TOKEN` — @BotFather'dan olingan, to'g'ri
- [ ] `ADMIN_IDS` — kamida bitta admin Telegram ID (bo'sh bo'lsa bot ishga tushmaydi)
- [ ] Railway'da **Volume** ulangan, mount path va `DATA_DIR` mos keladi
- [ ] `TZ=Asia/Tashkent` qo'yilgan (bo'lmasa server UTC'da ishlaydi)
- [ ] Service **1 ta replika**da ishlayapti (yuqoridagi ogohlantirishga qarang)
- [ ] `pytest` mahalliy/CI'da yashil
- [ ] `.env` fayl `.gitignore`'da va hech qachon commit qilinmagan
- [ ] Railway loglarida (yoki `/logs`) `⚠️`/`❌` ogohlantirish yo'q

Ushbu tekshiruvlar bajarilgan bo'lsa, bot kichik/o'rta yopiq foydalanuvchi
guruhi (do'stlar, oila, yopiq jamoat) uchun production-ready hisoblanadi.

### Ma'lum cheklovlar (kelajakda yaxshilash mumkin)

- **Bitta process, JSON fayl DB** — kichik/o'rta yuklama uchun yetarli,
  lekin ko'p yozuvli parallel yuklamada (yuzlab faol foydalanuvchi) fayl
  qulflash sekinlashishi mumkin. Migratsiya yo'li: **JSON → SQLite →
  PostgreSQL** — SQLite bitta fayl DB sifatida deyarli bir xil deploy
  modelini saqlab, WAL rejimi bilan yozuv performansini oshiradi;
  PostgreSQL esa ko'p replika/gorizontal masshtablashtirish kerak bo'lsa
  tabiiy keyingi qadam (lekin bu holda polling o'rniga webhook + tashqi
  scheduler arxitekturasi ham qayta ko'rib chiqilishi kerak bo'ladi).
- **Global rate limiting yo'q** — har foydalanuvchi 60s oynada ~20 so'rov
  bilan cheklangan (`security.py`), lekin butun bot darajasida umumiy
  chegara yo'q. Kichik yopiq foydalanuvchi guruhi uchun bu yetarli deb
  baholandi; agar foydalanuvchilar soni sezilarli o'ssa, qayta ko'rib
  chiqilishi tavsiya etiladi.

---

## Testlar

```bash
pip install -r requirements-dev.txt
pytest
```

Har bir push/PR da GitHub Actions CI testlarni avtomatik ishga tushiradi.

---

## Muhim

- `.env` faylni **hech qachon** GitHub ga yuklamang
- `data.json` va `bot.log` `.gitignore` da — xavfsiz
- `bot.log` avtomatik aylanadi (`RotatingFileHandler`, 10MB × 5 fayl) —
  konteyner diskini cheksiz to'ldirmaydi. Railway'ning o'z log ko'ruvchisi
  stdout orqali ishlaydi va bundan mustaqil.
- Foydalanuvchi ma'lumotlari qanday saqlanishi haqida — botda `/privacy`
  buyrug'i orqali har kimga (admin bo'lmasa ham) tushuntiriladi.

---

## Litsenziya

MIT — shaxsiy foydalanish uchun erkin.
