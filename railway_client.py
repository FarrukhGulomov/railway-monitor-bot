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

BASE = "https://eticket.railway.uz"
SEARCH_URL = f"{BASE}/api/v3/handbook/trains/list"

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


def _default_min_interval() -> float:
    try:
        from config import Config
        return Config.RAILWAY_MIN_REQUEST_INTERVAL
    except Exception:
        return 5.0


class RailwayClient:
    TIMEOUT = 20
    MAX_RETRIES = 3

    def __init__(self, min_interval: float = None):
        self._session = cffi_requests.Session(impersonate="chrome124")
        self._session.headers.update(HEADERS)
        self._last_request = 0.0
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

    def _init_session(self):
        with self._session_lock:
            try:
                r = self._session.get(f"{BASE}/uz/home", timeout=self.TIMEOUT)
                logger.info(f"Session init status: {r.status_code}")

                token = self._find_xsrf_token()
                if not token:
                    token = self._extract_token_from_headers(r)
                if not token:
                    token = str(uuid.uuid4())
                    logger.info("XSRF token o'zimiz generatsiya qildik")

                self._session.headers["X-Xsrf-Token"] = token
                self._session.cookies.set("XSRF-TOKEN", token, domain="eticket.railway.uz")
                # MUHIM: tokenning o'zini (hatto qisman ham) hech qachon log qilmaymiz —
                # bu himoya tokeni, log fayli/bot.log orqali oshkor bo'lmasligi kerak.
                logger.info("✅ XSRF token o'rnatildi")

            except Exception as e:
                logger.error(f"Session init xato: {e}")

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

    def _throttle(self):
        """Barcha marshrutlar uchun UMUMIY (global) minimal so'rov oralig'ini
        ta'minlaydi. Lock faqat vaqt belgisini o'qish/yangilash uchun qisqa
        muddat ushlanadi — kutishning o'zi lock tashqarisida, shuning uchun
        bu funksiya boshqa threadlarning o'z navbatini band qilishiga
        to'sqinlik qilmaydi (faqat umumiy tezlikni cheklaydi)."""
        while True:
            with self._pacing_lock:
                now = time.monotonic()
                wait = self.MIN_INTERVAL - (now - self._last_request)
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
                    r = self._session.post(SEARCH_URL, json=payload, timeout=self.TIMEOUT)
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
                    wait = min(int(r.headers.get("Retry-After", 30)), 30)
                    logger.warning(f"Rate limit — {wait}s")
                    time.sleep(wait)
                    last_error = "429 rate limited"
                    continue

                if 500 <= r.status_code < 600:
                    # Serverning o'tkinchi (transient) xatosi — saytga qarshi
                    # bosim o'tkazmasdan, kichik backoff bilan qayta urinamiz.
                    metrics.incr("railway_5xx_total")
                    logger.warning(
                        f"{r.status_code} server xatosi (urinish {attempt}/{self.MAX_RETRIES}): "
                        f"{r.text[:200]}"
                    )
                    last_error = f"http_{r.status_code}"
                    if attempt < self.MAX_RETRIES:
                        time.sleep(2 * attempt)
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
                if attempt < self.MAX_RETRIES:
                    time.sleep(3)

        return SearchResult(False, [], last_error)
