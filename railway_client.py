"""
Railway.uz API Client
O'zbekiston proxy orqali ishlaydi
"""

import requests
import logging
import time
import os

logger = logging.getLogger("railway_client")

BASE = "https://eticket.railway.uz"
SEARCH_URL = f"{BASE}/api/v3/handbook/trains/list"

HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Accept-Language": "uz",
    "Origin": BASE,
    "Referer": f"{BASE}/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


class RailwayClient:
    TIMEOUT = 20
    MAX_RETRIES = 3
    MIN_INTERVAL = 5.0

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        self._last_request = 0.0

        # Proxy sozlash
        proxy_url = os.getenv("PROXY_URL", "").strip()
        if proxy_url:
            self._session.proxies = {
                "http": proxy_url,
                "https": proxy_url,
            }
            logger.info(f"Proxy: {proxy_url}")
        else:
            logger.warning("PROXY_URL sozlanmagan — to'g'ridan uriniladi")

        self._init_session()

    def _init_session(self):
        try:
            r = self._session.get(BASE, timeout=self.TIMEOUT)
            token = self._session.cookies.get("XSRF-TOKEN", "")
            if token:
                self._session.headers["X-XSRF-TOKEN"] = token
                logger.info("✅ Session va XSRF token olindi")
            else:
                logger.warning("XSRF token kelmadi")
        except Exception as e:
            logger.error(f"Session init xato: {e}")

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

                if r.status_code == 401:
                    logger.info("401 — session yangilanmoqda")
                    self._init_session()
                    continue

                if r.status_code == 403:
                    logger.error(
                        "403 Forbidden — proxy ishlamayapti yoki IP blokda.\n"
                        "Railway → Variables → PROXY_URL ni tekshiring."
                    )
                    return []

                if r.status_code == 429:
                    wait = int(r.headers.get("Retry-After", 60))
                    logger.warning(f"Rate limit — {wait}s kutilmoqda")
                    time.sleep(wait)
                    continue

                r.raise_for_status()
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

            except requests.exceptions.ProxyError as e:
                logger.error(f"Proxy xato: {e} — PROXY_URL ni tekshiring")
                return []

            except requests.exceptions.Timeout:
                logger.warning(f"Timeout (urinish {attempt}/{self.MAX_RETRIES})")
                if attempt < self.MAX_RETRIES:
                    time.sleep(10 * attempt)

            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Ulanish xatosi: {e}")
                if attempt < self.MAX_RETRIES:
                    time.sleep(15)

            except Exception as e:
                logger.error(f"Search xato: {e}")
                break

        return []
