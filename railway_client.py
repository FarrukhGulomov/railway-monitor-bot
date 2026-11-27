"""
Railway.uz API Client
curl_cffi orqali — Chrome TLS fingerprint taqlid qiladi

── So'rov rejalashtirish arxitekturasi ──────────────────────────────────────
Ikki xil lock ataylab ajratilgan:

- `_pacing_lock` — faqat "keyingi so'rov qachon ruxsat etiladi" degan vaqt
  belgisini o'qish/yangilash uchun QISQA muddat ushlanadi. Haqiqiy kutish
  (time.sleep) lock TASHQARISIDA amalga oshiriladi.
- `_session_lock` — ulashilgan curl_cffi Session'ning o'zgaruvchan holatini
  (cookie jar, XSRF header) himoya qiladi; faqat HAQIQIY tarmoq chaqiruvi
  (bitta session.get()/session.post()) davomida ushlab turiladi.

Muhim natija: bitta marshrut 429/5xx bo'lib backoff bilan kutayotganda, bu
kutish endi `_session_lock`ni band qilib TURMAYDI — boshqa marshrut o'sha
paytda o'z so'rovini yuborishi mumkin. Bu "bitta sekin marshrut boshqa
hammasini to'sib qo'yadi" muammosini hal qiladi, lekin haqiqiy socket
darajasidagi parallelizm hali ham YO'Q (bitta ulashilgan Session xavfsizligi
uchun ataylab) — chaqiruvchi tomon (bot.py) ustiga qo'shimcha ravishda
asyncio.Semaphore bilan bir nechta so'rovni "parvozda" ushlab turishi mumkin,
lekin ularning navbatdagi HAQIQIY tarmoq chaqiruvi baribir ketma-ket bo'ladi.
"""

import logging
import threading
import time
import os
import uuid
from dataclasses import dataclass, field
from urllib.parse import unquote
from curl_cffi import requests as cffi_requests

from metrics import metrics

logger = logging.getLogger("railway_client")

BASE = "https://eticket.uzrailpass.uz"
# Sayt eski domen (eticket.railway.uz) dan yangisiga ko'chgan (prod loglarida
# tasdiqlangan). Eski manzil zaxira sifatida qoldirilgan: klient redirektni
# kuzatadi va 405 davom etsa zaxira manzilga o'tadi.
FALLBACK_BASES = ["https://eticket.railway.uz"]
PLACES_PATH = "/api/v1/handbook/trains"
SEARCH_PATH = "/api/v3/handbook/trains/list"
SEARCH_URL = f"{BASE}{SEARCH_PATH}"

HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Accept-Language": "uz",
    "Origin": BASE,
    "Referer": f"{BASE}/uz/home",
    "Device-Type": "BROWSER",
}


@dataclass
class SearchResult:
    """So'rov natijasi — muvaffaqiyatli bo'sh natija bilan API/tarmoq xatosini
    ajratish uchun. `ok=False` HECH QACHON "bilet yo'q" deb talqin qilinmasligi kerak."""
    ok: bool
    trains: list = field(default_factory=list)
    error: str = ""
    latency: float = 0.0  # so'rov boshlanishidan natija qaytishigacha (soniya) — diagnostika uchun


def _origin(url: str) -> str:
    """'https://host/path?x' -> 'https://host' (bo'sh/noto'g'ri bo'lsa '')."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return ""
    if parts.scheme in ("http", "https") and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return ""


def _configured_base() -> str:
    try:
        from config import Config
        return (Config.RAILWAY_BASE_URL or BASE).rstrip("/")
    except Exception:
        return BASE


def _default_min_interval() -> float:
    try:
        from config import Config
        return Config.RAILWAY_MIN_REQUEST_INTERVAL
    except Exception:
        return 5.0


class RailwayClient:
    # Sayt osilib qolsa (504 odatda ~15s'dan keyin keladi) uzoq kutmaymiz — monitor
    # sikli o'zi har CHECK_INTERVAL'da qayta urinadi. Ichki retry faqat TEZ
    # (<FAST_FAIL_SECONDS) xatolar uchun, shunda sikl ~5s ritmidan chiqib ketmaydi.
    TIMEOUT = 10
    MAX_RETRIES = 2
    FAST_FAIL_SECONDS = 5.0

    def __init__(self, min_interval: float = None):
        self._session = cffi_requests.Session(impersonate="chrome124")
        self._session.headers.update(HEADERS)
        self._last_request = 0.0
        # Sayt so'rovlar chegarasi (429): butun klient uchun UMUMIY sovutish.
        # Bitta marshrut 429 olsa, boshqalari ham shu IP'dan ketayotgani uchun
        # kutadi; interval esa sekin-asta kengayib, muvaffaqiyatda tiklanadi.
        self._cooldown_until = 0.0
        self._blocked_until_wall = 0.0
        self._consecutive_429 = 0
        self._extra_interval = 0.0
        primary = _configured_base()
        self._bases = [primary] + [b for b in [BASE] + FALLBACK_BASES if b != primary]
        self._base_idx = 0
        self._apply_base_headers()
        self.MIN_INTERVAL = min_interval if min_interval is not None else _default_min_interval()

        # Faqat pasing uchun — "keyingi ruxsat etilgan vaqt"ni himoya qiladi,
        # kutish davomida USHLAB TURILMAYDI.
        self._pacing_lock = threading.Lock()
        # Ulashilgan Session holatini (cookie/XSRF) himoya qiladi — faqat
        # haqiqiy tarmoq chaqiruvi (I/O) davomida ushlab turiladi, retry
        # backoff sleeplari davomida EMAS.
        self._session_lock = threading.Lock()

        proxy_url = os.getenv("PROXY_URL", "").strip()
        if proxy_url:
            self._session.proxies = {"http": proxy_url, "https": proxy_url}
            logger.info("Proxy sozlandi")

        self._init_session()

    @property
    def base(self) -> str:
        return self._bases[self._base_idx]

    def _apply_base_headers(self):
        self._session.headers.update({"Origin": self.base, "Referer": f"{self.base}/uz/home"})

    def _adopt_base(self, origin: str):
        """Sayt boshqa domenga yo'naltirgan bo'lsa, shu manzilga o'tamiz."""
        if origin not in self._bases:
            self._bases.append(origin)
        self._base_idx = self._bases.index(origin)
        self._apply_base_headers()

    def _rotate_base(self):
        """405 davom etsa — zaxira manzilga o'tamiz (domen ko'chgan bo'lishi mumkin)."""
        if len(self._bases) < 2:
            return
        old = self.base
        self._base_idx = (self._base_idx + 1) % len(self._bases)
        self._apply_base_headers()
        with self._session_lock:
            self._session.cookies.clear()
        metrics.incr("railway_base_rotations_total")
        logger.warning(f"405 davom etdi — manzil almashtirildi: {old} → {self.base}")

    def _init_session(self):
        with self._session_lock:
            try:
                r = self._session.get(f"{self.base}/uz/home", timeout=self.TIMEOUT)
                final = _origin(getattr(r, "url", ""))
                if final and final != self.base:
                    logger.warning(f"Sayt yo'naltirdi: {self.base} → {final} (yangi manzilga o'tildi)")
                    self._adopt_base(final)
                logger.info(f"Session init status: {r.status_code} ({self.base})")

                token = self._find_xsrf_token()
                if not token:
                    token = self._extract_token_from_headers(r)
                if not token:
                    token = self._fetch_csrf_token()
                if not token:
                    token = str(uuid.uuid4())
                    logger.info("XSRF token o'zimiz generatsiya qildik")

                self._session.headers["X-Xsrf-Token"] = token
                from urllib.parse import urlsplit
                self._session.cookies.set("XSRF-TOKEN", token, domain=urlsplit(self.base).netloc)
                # MUHIM: tokenning o'zini (hatto qisman ham) hech qachon log qilmaymiz —
                # bu himoya tokeni, log fayli/bot.log orqali oshkor bo'lmasligi kerak.
                logger.info("✅ XSRF token o'rnatildi")

            except Exception as e:
                logger.error(f"Session init xato: {e}")

    def _fetch_csrf_token(self) -> str:
        """Saytning o'z CSRF endpointi (docs/UZRAILPASS_API.md, 1-bo'lim).
        Javob shakli tasdiqlanmagan — cookie yoki JSON ichidan qidiriladi.
        _session_lock allaqachon ushlangan holda chaqiriladi."""
        try:
            r = self._session.get(f"{self.base}/api/v1/csrf-token", timeout=self.TIMEOUT)
            logger.info(f"csrf-token status: {r.status_code}")
            token = self._find_xsrf_token() or self._extract_token_from_headers(r)
            if token or r.status_code != 200:
                return token
            data = r.json()
            if isinstance(data, dict):
                inner = data.get("data", data)
                if isinstance(inner, str):
                    return inner
                if isinstance(inner, dict):
                    for k in ("token", "csrfToken", "xsrfToken", "value"):
                        if isinstance(inner.get(k), str):
                            return inner[k]
        except Exception as e:
            logger.info(f"csrf-token olinmadi: {e}")
        return ""

    def _find_xsrf_token(self) -> str:
        for name in ("XSRF-TOKEN", "csrf_token", "CSRF-TOKEN", "_csrf", "csrftoken"):
            val = self._session.cookies.get(name, "")
            if val:
                return unquote(val)
        return ""

    def _extract_token_from_headers(self, response) -> str:
        try:
            items = (
                response.headers.multi_items()
                if hasattr(response.headers, "multi_items")
                else list(response.headers.items())
            )
            for name, value in items:
                if name.lower() == "set-cookie" and "XSRF-TOKEN" in value:
                    part = value.split("XSRF-TOKEN=", 1)[1]
                    return unquote(part.split(";")[0].strip())
        except Exception:
            pass
        return ""

    MAX_COOLDOWN = 120.0
    MAX_EXTRA_INTERVAL = 10.0

    # Shundan uzun Retry-After yoki sayt "RISK_DENIED" desa — bu tezlik chegarasi
    # emas, saytning xavf tizimi bloki. Uni TO'LIQ hurmat qilamiz: muddat
    # tugaguncha saytga umuman so'rov yubormaymiz (aylanib o'tishga urinmaymiz).
    LONG_BLOCK_SECONDS = 300.0

    def blocked_until(self) -> float:
        """Uzoq blok tugaydigan vaqt (time.time() epoch), blok bo'lmasa 0."""
        return self._blocked_until_wall if self._blocked_until_wall > time.time() else 0.0

    def _on_rate_limited(self, headers, body: str = "") -> float:
        """429 kelganda umumiy sovutish muddatini belgilaydi: Retry-After bo'lsa
        shu (<=MAX_COOLDOWN), bo'lmasa 30s dan boshlab ikki baravar oshib boradi."""
        try:
            wait = float(headers.get("Retry-After") or headers.get("retry-after"))
        except (TypeError, ValueError, AttributeError):
            wait = 30.0 * (2 ** min(self._consecutive_429, 2))
        if "RISK_DENIED" in (body or "") or wait > self.LONG_BLOCK_SECONDS:
            wait = max(wait, self.LONG_BLOCK_SECONDS)
            with self._pacing_lock:
                self._consecutive_429 += 1
                self._blocked_until_wall = time.time() + wait
                self._cooldown_until = time.monotonic() + wait
            metrics.incr("railway_risk_blocks_total")
            logger.error(
                f"⛔ Sayt xavf tizimi so'rovlarni blokladi (RISK_DENIED), muddat {wait/3600:.1f} soat. "
                "Blok tugaguncha so'rov yuborilmaydi."
            )
            return wait
        wait = max(1.0, min(wait, self.MAX_COOLDOWN))
        with self._pacing_lock:
            self._consecutive_429 += 1
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + wait)
            self._extra_interval = min(self._extra_interval + 2.0, self.MAX_EXTRA_INTERVAL)
        return wait

    def _on_success(self):
        with self._pacing_lock:
            self._consecutive_429 = 0
            self._extra_interval = max(0.0, self._extra_interval - 0.25)

    def _throttle(self):
        """Barcha marshrutlar uchun UMUMIY (global) minimal so'rov oralig'ini
        ta'minlaydi. Lock faqat vaqt belgisini o'qish/yangilash uchun qisqa
        muddat ushlanadi — kutishning o'zi lock tashqarisida, shuning uchun
        bu funksiya boshqa threadlarning o'z navbatini band qilishiga
        to'sqinlik qilmaydi (faqat umumiy tezlikni cheklaydi)."""
        while True:
            with self._pacing_lock:
                now = time.monotonic()
                wait = max(
                    self.MIN_INTERVAL + self._extra_interval - (now - self._last_request),
                    self._cooldown_until - now,
                )
                if wait <= 0:
                    self._last_request = now
                    return
            time.sleep(wait)

    def search_trains(self, from_code: str, to_code: str, date: str) -> SearchResult:
        """railway.uz'dan poyezdlarni qidiradi.

        MUHIM: `ok=False` — so'rov muvaffaqiyatsiz bo'lganini bildiradi (tarmoq/API
        xatosi), bu "bilet/joy yo'q" degani EMAS. Faqat `ok=True, trains=[]` haqiqiy
        bo'sh natijani anglatadi. Chaqiruvchi kod bularni aralashtirmasligi kerak.

        Diqqat: bu metod endi butun davomida bitta lock'ni UShLAB TURMAYDI —
        faqat haqiqiy HTTP chaqiruvlari davomida qisqa muddat `_session_lock`ni
        oladi, retry/backoff kutishlari esa lock'siz amalga oshadi. Shu sabab
        bu metodni bir nechta threadda (masalan turli marshrutlar uchun)
        bir vaqtda xavfsiz chaqirish mumkin — ular bir-birining backoff
        kutishini TO'SMAYDI, faqat navbat bilan haqiqiy so'rov yuboradi."""
        start = time.monotonic()
        if self.blocked_until():
            # Blok paytida saytga hech narsa yubormaymiz (thread'ni soatlab ushlab ham turmaymiz).
            return SearchResult(False, [], "risk_blocked")
        result = self._search_trains_impl(from_code, to_code, date)
        result.latency = time.monotonic() - start
        return result

    def _search_trains_impl(self, from_code: str, to_code: str, date: str) -> SearchResult:
        payload = {
            "directions": {
                "forward": {
                    "date": date,
                    "depStationCode": from_code,
                    "arvStationCode": to_code,
                }
            }
        }

        last_error = "noma'lum xato"

        for attempt in range(1, self.MAX_RETRIES + 1):
            self._throttle()
            metrics.incr("railway_requests_total")
            if attempt > 1:
                metrics.incr("railway_retries_total")

            try:
                io_start = time.monotonic()
                with self._session_lock:
                    r = self._session.post(f"{self.base}{SEARCH_PATH}", json=payload, timeout=self.TIMEOUT)
                io_latency = time.monotonic() - io_start
                logger.info(
                    f"Search javobi (urinish {attempt}/{self.MAX_RETRIES}, "
                    f"{io_latency:.2f}s): {r.status_code}"
                )

                if r.status_code == 401:
                    logger.info("401 — session yangilanmoqda")
                    self._init_session()
                    last_error = "401 unauthorized"
                    continue

                if r.status_code == 405:
                    # POST rad etildi (odatda ~1s ichida): eskirgan/yaroqsiz
                    # session yoki himoya qatlami. Cookie'larni tozalab yangi
                    # session bilan bir marta qayta urinamiz.
                    metrics.incr("railway_405_total")
                    logger.warning("405 Method Not Allowed — session qayta yaratilmoqda")
                    last_error = "http_405"
                    if attempt < self.MAX_RETRIES:
                        with self._session_lock:
                            self._session.cookies.clear()
                        self._init_session()
                        continue
                    # Yangi sessiya ham yordam bermadi — keyingi sikl zaxira manzilni sinaydi
                    self._rotate_base()
                    return SearchResult(False, [], last_error)

                if r.status_code == 403:
                    logger.error(f"403 Forbidden: {r.text[:200]}")
                    if "CSRF" in r.text and attempt < self.MAX_RETRIES:
                        with self._session_lock:
                            self._session.cookies.clear()
                        self._init_session()
                        last_error = "403 csrf"
                        continue
                    return SearchResult(False, [], "403 forbidden")

                if r.status_code == 400:
                    error_body = r.text[:200]
                    logger.error(f"400 Bad Request: {error_body}")

                    if "Express" in error_body or "ma'lumot kelmadi" in error_body:
                        # Bu railway.uz serverining ICHKI vaqtinchalik xatosi —
                        # bizning so'rovimiz to'g'ri, lekin ularning Express
                        # (tezyurar poyezdlar) xizmati javob bermayapti.
                        # Session yangilash foydasiz — shunchaki biroz kutib qayta uriniladi.
                        logger.warning(
                            "Sayt backendi vaqtincha javob bermayapti "
                            "(Express xizmati). Keyingi tsiklda qayta sinab ko'riladi."
                        )
                        return SearchResult(False, [], "express_temp_unavailable")

                    if "Unexpected status" in error_body:
                        logger.warning(
                            f"Sayt bu sanani ({date}) qabul qilmayapti. "
                            "Sabab: barcha reyslar o'tib ketgan bo'lishi yoki "
                            "sana formatida muammo bo'lishi mumkin — buni ishonchli "
                            "'bilet yo'q' deb bo'lmaydi, xato sifatida qaytariladi."
                        )
                        return SearchResult(False, [], "unexpected_status")

                    self._init_session()
                    last_error = f"400: {error_body}"
                    continue

                if r.status_code == 429:
                    metrics.incr("railway_429_total")
                    # Cheklov turini aniqlash uchun (IP / sessiya / WAF) javobni ko'rsatamiz.
                    rl_headers = {
                        k: v for k, v in dict(r.headers).items()
                        if k.lower().startswith(("retry", "x-ratelimit", "ratelimit", "server", "cf-", "x-cache"))
                    } if hasattr(r.headers, "items") else {}
                    logger.warning(f"429 tafsiloti: headers={rl_headers} body={r.text[:300]!r}")
                    wait = self._on_rate_limited(r.headers, r.text or "")
                    if self.blocked_until():
                        return SearchResult(False, [], "risk_blocked")
                    if self._consecutive_429 >= 2:
                        # Cheklov sessiyaga bog'liq bo'lishi mumkin — yangi cookie/token bilan urinamiz.
                        logger.warning("Takroriy 429 — session yangilanmoqda")
                        with self._session_lock:
                            self._session.cookies.clear()
                        self._init_session()
                    logger.warning(
                        f"Rate limit (429) — umumiy sovutish {wait:.0f}s, "
                        f"interval +{self._extra_interval:.0f}s"
                    )
                    # Ichida qayta urinmaymiz: bosimni oshirmaslik uchun keyingi
                    # urinish umumiy sovutishdan keyin (_throttle) amalga oshadi.
                    return SearchResult(False, [], "429 rate limited")

                if 500 <= r.status_code < 600:
                    # Serverning o'tkinchi (transient) xatosi — saytga qarshi
                    # bosim o'tkazmasdan, kichik backoff bilan qayta urinamiz.
                    metrics.incr("railway_5xx_total")
                    logger.warning(
                        f"{r.status_code} server xatosi (urinish {attempt}/{self.MAX_RETRIES}): "
                        f"{r.text[:200]}"
                    )
                    last_error = f"http_{r.status_code}"
                    # Tez qaytgan xato (502/503) — bir marta darhol qayta
                    # urinamiz; sekin (504/timeout) bo'lsa — keyingi sikl kutadi.
                    if attempt < self.MAX_RETRIES and io_latency < self.FAST_FAIL_SECONDS:
                        continue
                    return SearchResult(False, [], last_error)

                if r.status_code != 200:
                    logger.error(f"Status {r.status_code}: {r.text[:200]}")
                    return SearchResult(False, [], f"http_{r.status_code}")

                try:
                    data = r.json()
                except ValueError as e:
                    # Sayt 200 qaytardi, lekin javob JSON emas/buzilgan —
                    # bu ham "bilet yo'q" emas, alohida xato sifatida qaytariladi.
                    metrics.incr("railway_malformed_json_total")
                    logger.error(f"Buzilgan JSON javob (status 200): {e}")
                    last_error = "malformed_json"
                    if attempt < self.MAX_RETRIES:
                        time.sleep(2)
                        continue
                    return SearchResult(False, [], last_error)

                self._on_success()
                trains = (
                    data.get("data", {})
                    .get("directions", {})
                    .get("forward", {})
                    .get("trains", [])
                )

                logger.info(f"✅ {from_code}→{to_code} {date} — {len(trains)} poyezd topildi")
                return SearchResult(True, trains, "")

            except Exception as e:
                is_timeout = "timeout" in type(e).__name__.lower() or "timeout" in str(e).lower()
                metrics.incr("railway_timeouts_total" if is_timeout else "railway_exceptions_total")
                logger.error(f"Search xato (urinish {attempt}): {e}")
                last_error = f"exception: {e}"
                if is_timeout:
                    break  # osilib qolgan sayt — keyingi sikl qayta urinadi
                if attempt < self.MAX_RETRIES:
                    time.sleep(1)

        return SearchResult(False, [], last_error)

    def get_train_places(self, train: dict, date_iso: str):
        """Poyezdning vagonlari va bo'sh joy RAQAMLARI (`POST /api/v1/handbook/trains`).
        Faqat mos variant topilgandan keyin chaqiriladi. None — xato/noma'lum
        (bu "joy yo'q" degani EMAS); aks holda `carGroup` ro'yxati."""
        route = train.get("subRoute") or {}
        payload = {
            "depDate": date_iso,
            "depStationCode": route.get("depStationCode"),
            "arvStationCode": route.get("arvStationCode"),
            "trainNumber": train.get("number"),
            "trainId": train.get("trainId"),
        }
        if not payload["depStationCode"] or not payload["arvStationCode"] or not payload["trainNumber"]:
            return None
        if self.blocked_until():
            return None
        try:
            self._throttle()
            metrics.incr("railway_requests_total")
            with self._session_lock:
                r = self._session.post(f"{self.base}{PLACES_PATH}", json=payload, timeout=self.TIMEOUT)
            if r.status_code == 429:
                self._on_rate_limited(r.headers, r.text or "")
                return None
            if r.status_code != 200:
                logger.info(f"Joy tafsiloti: status {r.status_code}")
                return None
            return ((r.json().get("data") or {}).get("train") or {}).get("carGroup")
        except Exception as e:
            logger.info(f"Joy tafsilotini olib bo'lmadi: {e}")
            return None
