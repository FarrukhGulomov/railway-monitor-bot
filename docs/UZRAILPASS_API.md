# eticket.uzrailpass.uz — RAILWAY-MONITOR-BOT uchun API qo'llanma

Tahlil sanasi: 2026-11-27. Bot: `FarrukhGulomov/railway-monitor-bot`, commit `2bfd999`.
Manba: brauzerdagi frontend JS kodi, tabning so'rovlar tarixi va bir nechta o'qish so'rovi.
Bu rasmiy hujjat emas, API istalgan payt o'zgarishi mumkin.

Belgilar: ✅ real so'rov bilan tekshirildi · 📄 faqat frontend kodidan o'qildi (sinalmagan)

## 0. Avval tuzatiladigan xato: stansiya kodlari

| Stansiya | Botda hozir | To'g'ri kod | Tekshiruv |
|---|---|---|---|
| Toshkent | 2900000 | 2900000 (qidiruvda ishlaydi) | ✅ |
| Samarqand | 2900680 ❌ (bu Andijon) | **2900700** | ✅ qidiruv |
| Andijon | 2900100 ❌ | **2900680** | ✅ qidiruv |
| Buxoro | 2900800 | 2900800 | ✅ qidiruv |
| Namangan | 2900200 ❌ | **2900940** | ✅ stations/list |
| Qarshi | 2900900 ❌ | **2900750** | ✅ stations/list |
| Nukus | 2900350 ❌ | **2900970** | ✅ stations/list |
| Urganch | 2900300 ❌ | **2900790** | ✅ stations/list |
| Termiz | 2901100 ❌ | **2900255** | ✅ stations/list |
| Farg'ona | 2900210 (0 poyezd qaytdi) | aniqlanmadi, Margilon = 2900920 | ⚠️ |
| Xiva | yo'q | 2900172 | ✅ |
| Navoiy | yo'q | 2900930 | ✅ |
| Jizzax | yo'q | 2900720 | ✅ |
| Guliston | yo'q | 2900236 | ✅ |
| Qo'qon | yo'q | 2900880 (so'rov: `qoqon`) | ✅ |
| Margilon | yo'q | 2900920 | ✅ |
| Pop | yo'q | 2900693 | ✅ |

Bazadagi eski faol kuzatuvlarda noto'g'ri kod saqlangan. `_resume_monitors` oldidan
migratsiya qiling. Migratsiyani bir marta va bitta o'tishda bajaring, chunki `2900680`
eski ma'noda Samarqand, yangisida Andijon:

```python
STATION_FIX = {
    "2900680": "2900700", "2900100": "2900680", "2900200": "2900940",
    "2900900": "2900750", "2900350": "2900970", "2900300": "2900790",
    "2901100": "2900255",
}
# har monitor uchun: if not m.get("stations_v2"):
#   m["from_code"] = STATION_FIX.get(m["from_code"], m["from_code"])  (to_code ham)
#   m["stations_v2"] = True
```

## 1. Umumiy sozlamalar

- Asosiy manzil: `https://eticket.uzrailpass.uz` (Railway Variables: `RAILWAY_BASE_URL`).
  `eticket.railway.uz` holati bu tahlilda tekshirilmadi.
- Headerlar: `Content-Type: application/json`, `Accept: application/json`,
  `Accept-Language: uz`, `X-XSRF-TOKEN: <XSRF-TOKEN cookie qiymati>`.
- 📄 CSRF: `GET /api/v1/csrf-token`. Bot hozir topolmasa tokenni o'zi generatsiya qiladi;
  shu endpointdan olish to'g'riroq.
- ✅ Quyidagi 2.1–2.3 so'rovlari `Authorization` headersiz 200 qaytardi
  (brauzer cookie'lari bilan; toza klientdan sinalmagan).
- ✅ `/api/v1/handbook/time` va `/api/v1/handbook/trains/route` headersiz **401** qaytardi.

## 2. Monitoring metodlari

### 2.1 ✅ Poyezdlar qidiruvi (botda bor)
`POST /api/v3/handbook/trains/list`
```json
{"directions":{"forward":{"date":"2026-11-30","depStationCode":"2900000","arvStationCode":"2900800"}}}
```
Javob: `data.directions.forward.trains[]`
- `number`, `type`, `brand`, `departureDate` (`dd.MM.yyyy HH:mm`), `arrivalDate`, `timeOnWay`
- `trainId`, `providerType` (`EXPRESS`; kodda `SORBON` ham bor)
- `subRoute.{depStationCode,arvStationCode,depStationName,arvStationName}`
- `originRoute`
- `cars[]`: `type`, `freeSeats`, `tariffs[]{classServiceType,freeSeats,tariff}`,
  `seatDetail{down,up,lateralDn,lateralUp,freeComp,undef}`

Yangi ficha: `seatDetail` orqali "faqat pastki joy" filtri.

### 2.2 ✅ Vagon va aniq joy raqamlari (yangi)
`POST /api/v1/handbook/trains`
```json
{"depDate":"2026-11-30","depStationCode":"2900000","arvStationCode":"2900800",
 "trainNumber":"752Ж","trainId":null}
```
Qiymatlarni 2.1 javobidan oling (`subRoute`, `number`, `trainId`).

Javob: `data.train.carGroup[]`
- `type`, `typeShow`, `tariff`, `services`, `occupancyType`
- `cars[]`: `number`, `places[]` (bo'sh joy raqamlari), `seatDetail`, `carId`,
  `schema{service,name,seatsCount,brandName}`

Yangi fichalar: xabarda vagon va joy raqami, yonma-yon joylarni topish, pastki joy.
Faqat 2.1 mos variant topganda chaqiring, shunda saytga yuk oshmaydi.

### 2.3 ✅ Stansiya qidirish (yangi)
`POST /api/v1/handbook/stations/list`
(kodda `POST /api/v1/stations/list` ham bor, u sinalmagan 📄)
```json
{"name":"bux"}
```
Javob: `data.stations[]{code,name}`. Natija yo'q bo'lsa **204**; 2 harfli so'rov **400** qaytardi.

### 2.4 📄 Qo'shimcha
| Metod | Yo'l | Body |
|---|---|---|
| POST | `/api/v1/handbook/trains/route` (auth) | `{"number":"766Ф"}` |
| GET | `/api/v1/handbook/stations/schedule/{...}` | — |
| GET | `/api/v1/handbook/time` (auth) | — |
| POST | `/api/v1/handbook/trains/suburban-list`, `/suburban` | — |

## 3. Bron qilish 📄 (birortasi men tomonimdan chaqirilmadi)

Tab tarixida bu oqim (reserve → payment-end-time → select-payment-type → payme → cancel)
ko'ringan, ya'ni yo'llar saytda amalda ishlatiladi.

### 3.1 Kirish
`POST /api/v1/auth/login` → `{"username":"...","password":"..."}`
Javobdagi JWT keyingi so'rovlarda: `Authorization: Bearer <token>`.
Token ichidan `id` (userId) va `sub` (username) olinadi. Javobning aniq shakli sinalmagan.
Saytda reCAPTCHA ulangan. Login yoki bron paytida talab qilinsa, uni chetlab o'tmang:
bu holda yarim avtomat variantga (5-bo'lim) o'ting.

### 3.2 Bron yaratish (EXPRESS)
`POST /api/v3/universal-orders/reserve`
```json
{
  "channel":"WEB","orderKind":"RETAIL","paymentMode":"CARD",
  "groupType":"INDIVIDUALS","orderType":"INDIVIDUAL",
  "providerType":"EXPRESS","documentMode":"NAMED",
  "subItems":[{
    "trainInfo":{"trainNumber":"766Ф","carNumber":"01","carType":"<...>",
                 "serviceClass":"<...>","directionSequence":1,"brand":"Afrosiyob"},
    "departure":{"code":"2900000","name":"<...>","date":"2026-11-30","time":"HH:mm:ss"},
    "arrival":{"code":"2900800","name":"<...>","date":"2026-11-30","time":"HH:mm:ss"},
    "hasInsurance":false,"hasCateringOrder":false,"hasGreenTicket":false,
    "reserveSeatRequirements":{"seatsRange":"5-6","seatsBottom":2},
    "tickets":[{
      "passengerInfo":{
        "fullName":"FAMILIYA=ISM=OTASINING_ISMI","documentId":"<pasport>",
        "documentType":"PASSPORT","gender":"MALE","citizenship":"UZ",
        "birthday":"YYYY-MM-DD","regionId":"03","discountType":"REGULAR",
        "referenceDocument":null,"prefix":null,"tariffCode":null,"child":null},
      "pricingMode":"FULL","hasPatronymics":true
    }]
  }],
  "webCustomer":{"id":"<userId>","username":"<username>","ip":"eticket.uzrailpass.uz"}
}
```
- `fullName`: uch qism `=` bilan birlashtiriladi.
- `seatsRange`: tanlangan eng kichik va eng katta joy. `seatsTop` / `seatsBottom` ixtiyoriy.
- `pricingMode`: `FULL`, `DISCOUNTED`, `CHILD`. `gender`: `MALE`, `FEMALE`, `NONE`.
- `carType` = carGroup `actualType`, bo'lmasa `type`; `serviceClass` = vagon yoki guruh `type`.
  Tekshirilgan poyezdda `actualType` bo'sh edi, aniq qiymatni bitta haqiqiy bronni
  DevTools Network'da ko'rib tasdiqlang.
- Javob: `response` = `orderId`. Natija WebSocket orqali keladi.

### 3.3 SORBON provayderi (`trainId` bo'sh bo'lmasa)
1. `POST /api/v3/universal-orders/hold` (`channel`, `orderKind`, `providerType:"SORBON"`, `subItems`)
2. `GET /api/v3/universal-orders/query/get/hold-end-time/{orderId}`
3. `POST /api/v3/universal-orders/add-passenger-info`
   (`orderId`, `reserveHoldItems[]{itemId,bundleId,...,tickets}`; `itemId`, `bundleId` WebSocket'dan)

### 3.4 Holat
WebSocket: `wss://eticket.uzrailpass.uz/ws`, ulangach `{"group":"<userId>","subscribe":1}`.
Muvaffaqiyat: `status:"Success"`, `state:"ORDER_COMPLETED_SUCCESSFULLY"`.

| Metod | Yo'l |
|---|---|
| GET | `/api/v1/universal-orders/get/{orderId}` — bron ma'lumoti |
| GET | `/api/v3/universal-orders/query/get/payment-end-time/{orderId}` |
| POST | `/api/v1/universal-orders/cancel/{orderId}` — body `{"orderId":"...","userId":"..."}` |

### 3.5 Yo'lovchilar
`GET /api/v1/users/get` · `/api/v1/users/friend/list` · `friend/create` · `friend/remove`

## 4. To'lov va chiptalar 📄
Botga to'lovni qo'shishni tavsiya qilmayman: karta va SMS kod bot orqali o'tmasligi kerak.
Foydalanuvchi to'lovni saytda yoki ilovada o'zi qilsin.

- To'lov turlari: `POST /api/v3/payment-type/list`
- Faol chiptalar: `GET /api/v1/query/railway/orders/active/tickets/list`
- Buyurtmalar: `POST /api/v2/query/orders/list`
- Qaytarish ma'lumoti: `POST /api/v2/query/orders/return-info`
- PDF: `GET /api/v1/document/{orderId}/pdf`

## 5. Botga qo'shish rejasi

1. **Stansiya kodlari + migratsiya** (0-bo'lim).
2. **Asosiy domen** → `eticket.uzrailpass.uz`.
3. **Joy tafsiloti**: `get_train_places()` (2.2), xabarda vagon va joylar.
4. **Pastki joy filtri** (`seatDetail.down`).
5. **Yarim avtomat bron**: joy topilganda tayyor ma'lumot va saytga tugma.
6. **Avto-bron** (ixtiyoriy, faqat o'z akkauntingiz uchun): "Bron qilish" tugmasi → tasdiq →
   3.2 → WebSocket → to'lov muddati va "Bekor qilish" tugmasi.

2.2 uchun kod:
```python
PLACES_PATH = "/api/v1/handbook/trains"

def get_train_places(self, train: dict, date_iso: str):
    route = train["subRoute"]
    payload = {"depDate": date_iso,
               "depStationCode": route["depStationCode"],
               "arvStationCode": route["arvStationCode"],
               "trainNumber": train["number"],
               "trainId": train.get("trainId")}
    self._throttle()
    with self._session_lock:
        r = self._session.post(f"{self.base}{PLACES_PATH}", json=payload, timeout=self.TIMEOUT)
    if r.status_code != 200:
        return None   # xato "joy yo'q" degani emas
    return ((r.json().get("data") or {}).get("train") or {}).get("carGroup")
```

### Avto-bron xavfsizligi
- Avval saytning ofertasida avtomatik bron taqiqlanmaganini tekshiring.
- Faqat admin/o'zingiz uchun; har bron oldidan tasdiq; bir vaqtda bitta bron; qayta urinish yo'q
  (timeout bo'lsa avval buyurtma holatini tekshiring).
- Parol, token va pasport ma'lumotlarini loglarga yozmang, `data.json`ga ochiq saqlamang.
- Chiptani band qilib turib qayta-qayta bron qilmang: akkaunt bloklanishi mumkin.
