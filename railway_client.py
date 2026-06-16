"""
Railway.uz API Client
curl_cffi orqali — Chrome TLS fingerprint taqlid qiladi
(oddiy requests kutubxonasi 403 bilan bloklanadi)
"""

import logging
import time
import os
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
        # impersonate="chrome" — Chrome brauzerining TLS/HTTP2 izini ishlatadi
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
            # Real brauzer xuddi shu sahifaga kiradi: /uz/home
            r = self._session.get(f"{BASE}/uz/home", timeout=self.TIMEOUT)
            logger.info(f"Session init status: {r.status_code}")
            logger.info(f"Cookies after /uz/home: {list(self._session.cookies.keys())}")

            set_cookie = r.headers.get("set-cookie", "")
            if set_cookie:
                logger.info(f"Raw Set-Cookie: {set_cookie[:200]}")

            token = self._find_xsrf_token()

            if token:
                self._session.headers["X-Xsrf-Token"] = token
                logger.info(f"✅ XSRF token olindi: {token[:15]}...")
            else:
                logger.warning(f"XSRF token kelmadi. Mavjud cookielar: {list(self._session.cookies.keys())}")

        except Exception as e:
            logger.error(f"Session init xato: {e}")

    def _find_xsrf_token(self) -> str:
        """Turli nomdagi CSRF cookie larni qidirish"""
        for name in ("XSRF-TOKEN", "csrf_token", "CSRF-TOKEN", "_csrf", "csrftoken", "X-CSRF-TOKEN"):
            val = self._session.cookies.get(name, "")
            if val:
                try:
                    from urllib.parse import unquote
                    return unquote(val)
                except Exception:
                    return val
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
                r = self._session.post(
                    SEARCH_URL,
                    json=payload,
                    timeout=self.TIMEOUT,
                )

                logger.info(f"Search javobi: {r.status_code}")

                if r.status_code == 401:
                    logger.info("401 — session yangilanmoqda")
                    self._init_session()
                    continue

                if r.status_code == 403:
                    logger.error(f"403 Forbidden. Body: {r.text[:300]}")
                    if "CSRF" in r.text and attempt < self.MAX_RETRIES:
                        logger.info("CSRF xato — session butunlay yangilanmoqda")
                        self._session.cookies.clear()
                        self._init_session()
                        continue
                    return []

                if r.status_code == 429:
                    wait = int(r.headers.get("Retry-After", 60))
                    logger.warning(f"Rate limit — {wait}s kutilmoqda")
                    time.sleep(wait)
                    continue

                if r.status_code != 200:
                    logger.error(f"Kutilmagan status {r.status_code}: {r.text[:300]}")
                    return []

                data = r.json()
                trains = (
                    data.get("data", {})
                    .get("directions", {})
                    .get("forward", {})
                    .get("trains", [])
                )
                logger.info(
                    f"✅ {from_code}→{to_code} {date} — {len(trains)} poyezd topildi"
                )
                return trains

            except Exception as e:
                logger.error(f"Search xato (urinish {attempt}): {e}")
                if attempt < self.MAX_RETRIES:
                    time.sleep(10)

        return []
