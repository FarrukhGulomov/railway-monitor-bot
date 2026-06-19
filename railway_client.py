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
            r = self._session.get(f"{BASE}/uz/home", timeout=self.TIMEOUT)
            logger.info(f"Session init status: {r.status_code}")
            logger.info(f"Cookies after /uz/home: {list(self._session.cookies.keys())}")

            token = self._find_xsrf_token()

            if not token:
                token = self._extract_token_from_headers(r)

            if not token:
                # Double-submit CSRF pattern: ko'p tizimlarda server tokenni
                # o'zi generatsiya qilmaydi, faqat cookie==header tekshiradi.
                # Shu sababli o'zimiz UUID generatsiya qilib yuboramiz.
                import uuid
                token = str(uuid.uuid4())
                logger.info(f"XSRF token serverdan kelmadi — o'zimiz generatsiya qildik: {token}")

            self._session.headers["X-Xsrf-Token"] = token
            self._session.cookies.set("XSRF-TOKEN", token, domain="eticket.railway.uz")
            logger.info(f"✅ XSRF token o'rnatildi: {token[:15]}...")

        except Exception as e:
            logger.error(f"Session init xato: {e}")

    def _extract_token_from_headers(self, response) -> str:
        """Set-Cookie headerlardan to'g'ridan-to'g'ri XSRF-TOKEN qiymatini ajratib olish"""
        try:
            raw_headers = (
                response.headers.multi_items()
                if hasattr(response.headers, "multi_items")
                else list(response.headers.items())
            )
            for name, value in raw_headers:
                if name.lower() == "set-cookie" and "XSRF-TOKEN" in value:
                    # format: XSRF-TOKEN=xxxxx; Path=/; ...
                    part = value.split("XSRF-TOKEN=", 1)[1]
                    token = part.split(";")[0].strip()
                    from urllib.parse import unquote
                    return unquote(token)
        except Exception as e:
            logger.warning(f"Header parse xato: {e}")
        return ""

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

                # cars bo'sh bo'lgan poyezdlar uchun alohida so'rov
                trains = self._fill_empty_cars(trains, from_code, to_code, date)
                logger.info(
                    f"✅ {from_code}→{to_code} {date} — {len(trains)} poyezd topildi"
                )
                return trains

            except Exception as e:
                logger.error(f"Search xato (urinish {attempt}): {e}")
                if attempt < self.MAX_RETRIES:
                    time.sleep(10)

        return []

    def _fill_empty_cars(self, trains: list, from_code: str, to_code: str, date: str) -> list:
        """
        Cars bo'sh bo'lgan poyezdlar uchun alohida so'rov yuboradi.
        Ko'p hollarda sayt trains/list da cars ni to'liq qaytarmaydi —
        poyezd bosilganda alohida so'rov bilan yuklanadi.
        """
        empty = [t for t in trains if not t.get("cars")]
        if not empty:
            return trains

        logger.info(f"Cars bo'sh poyezdlar: {len(empty)} ta — alohida so'rov yuborilmoqda")

        for train in empty:
            number = train.get("number", "")
            dep_date = train.get("departureDate", "").split(" ")[0]
            if dep_date and "." in dep_date:
                # Format: "19.06.2026" -> "2026-06-19"
                parts = dep_date.split(".")
                if len(parts) == 3:
                    dep_date = f"{parts[2]}-{parts[1]}-{parts[0]}"

            try:
                # Variant 1: raqam orqali poyezd cars ni olish
                r = self._session.post(
                    f"{BASE}/api/v3/handbook/trains/list",
                    json={
                        "directions": {
                            "forward": {
                                "date": date,
                                "depStationCode": from_code,
                                "arvStationCode": to_code,
                                "trainNumber": number,
                            }
                        }
                    },
                    timeout=self.TIMEOUT,
                )
                if r.status_code == 200:
                    detail_trains = (
                        r.json().get("data", {})
                        .get("directions", {})
                        .get("forward", {})
                        .get("trains", [])
                    )
                    for dt in detail_trains:
                        if dt.get("number") == number and dt.get("cars"):
                            train["cars"] = dt["cars"]
                            logger.info(f"  ✅ {number} uchun {len(dt['cars'])} vagon olindi")
                            break
                    else:
                        logger.info(f"  ⚠️ {number} uchun trainNumber filter ishlamadi")

            except Exception as e:
                logger.warning(f"  {number} cars olishda xato: {e}")

        return trains
