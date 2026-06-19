"""
Railway.uz API Client
curl_cffi orqali — Chrome TLS fingerprint taqlid qiladi
"""

import logging
import time
import os
import uuid
from urllib.parse import unquote
from curl_cffi import requests as cffi_requests

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


class RailwayClient:
    TIMEOUT = 20
    MAX_RETRIES = 3
    MIN_INTERVAL = 5.0

    def __init__(self):
        self._session = cffi_requests.Session(impersonate="chrome124")
        self._session.headers.update(HEADERS)
        self._last_request = 0.0

        proxy_url = os.getenv("PROXY_URL", "").strip()
        if proxy_url:
            self._session.proxies = {"http": proxy_url, "https": proxy_url}
            logger.info(f"Proxy: {proxy_url}")

        self._init_session()

    def _init_session(self):
        try:
            r = self._session.get(f"{BASE}/uz/home", timeout=self.TIMEOUT)
            logger.info(f"Session init status: {r.status_code}")

            token = self._find_xsrf_token()
            if not token:
                token = self._extract_token_from_headers(r)
            if not token:
                token = str(uuid.uuid4())
                logger.info(f"XSRF token o'zimiz generatsiya qildik: {token}")

            self._session.headers["X-Xsrf-Token"] = token
            self._session.cookies.set("XSRF-TOKEN", token, domain="eticket.railway.uz")
            logger.info(f"✅ XSRF token o'rnatildi: {token[:15]}...")

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
        elapsed = time.time() - self._last_request
        if elapsed < self.MIN_INTERVAL:
            time.sleep(self.MIN_INTERVAL - elapsed)
        self._last_request = time.time()

    def search_trains(self, from_code: str, to_code: str, date: str) -> list:
        self._throttle()

        payload = {
            "directions": {
                "forward": {
                    "date": date,
                    "depStationCode": from_code,
                    "arvStationCode": to_code,
                }
            }
        }

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                r = self._session.post(SEARCH_URL, json=payload, timeout=self.TIMEOUT)
                logger.info(f"Search javobi: {r.status_code}")

                if r.status_code == 401:
                    logger.info("401 — session yangilanmoqda")
                    self._init_session()
                    continue

                if r.status_code == 403:
                    logger.error(f"403 Forbidden: {r.text[:200]}")
                    if "CSRF" in r.text and attempt < self.MAX_RETRIES:
                        self._session.cookies.clear()
                        self._init_session()
                        continue
                    return []

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
                        return []

                    if "Unexpected status" in error_body:
                        logger.warning(
                            f"Sayt bu sanani ({date}) qabul qilmayapti. "
                            "Sabab: barcha reyslar o'tib ketgan bo'lishi yoki "
                            "sana formatida muammo bo'lishi mumkin."
                        )
                        return []

                    self._init_session()
                    continue

                if r.status_code == 429:
                    wait = min(int(r.headers.get("Retry-After", 30)), 30)
                    logger.warning(f"Rate limit — {wait}s")
                    time.sleep(wait)
                    continue

                if r.status_code != 200:
                    logger.error(f"Status {r.status_code}: {r.text[:200]}")
                    return []

                data = r.json()
                trains = (
                    data.get("data", {})
                    .get("directions", {})
                    .get("forward", {})
                    .get("trains", [])
                )

                logger.info(f"✅ {from_code}→{to_code} {date} — {len(trains)} poyezd topildi")
                return trains

            except Exception as e:
                logger.error(f"Search xato (urinish {attempt}): {e}")
                if attempt < self.MAX_RETRIES:
                    time.sleep(3)

        return []
