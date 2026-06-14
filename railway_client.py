"""
Railway.uz API Client — faqat qidirish
Hech qanday bron yoki to'lov amalga oshirilmaydi
"""

import requests
import logging
import time

logger = logging.getLogger("railway_client")

_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Accept-Language": "uz",
    "Origin": "https://eticket.railway.uz",
    "Referer": "https://eticket.railway.uz/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


class RailwayClient:
    BASE = "https://eticket.railway.uz"
    SEARCH_URL = f"{BASE}/api/v3/handbook/trains/list"
    TIMEOUT = 20
    MAX_RETRIES = 3

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        self._last_request = 0.0
        self._min_interval = 5.0  # so'rovlar orasida min 5 soniya
        self._init_session()

    def _init_session(self):
        """XSRF token olish"""
        try:
            resp = self._session.get(self.BASE, timeout=self.TIMEOUT)
            token = self._session.cookies.get("XSRF-TOKEN")
            if token:
                self._session.headers["X-XSRF-TOKEN"] = token
                logger.info("XSRF token olindi")
        except Exception as e:
            logger.warning(f"Session init: {e}")

    def _throttle(self):
        """So'rovlar orasida kutish — saytni yuklamaslik uchun"""
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def search_trains(self, from_code: str, to_code: str, date: str) -> list:
        """
        Poyezdlarni qidirish.
        Faqat o'qish — hech narsa yozilmaydi.
        """
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
                resp = self._session.post(
                    self.SEARCH_URL,
                    json=payload,
                    timeout=self.TIMEOUT,
                )

                if resp.status_code == 401:
                    logger.info("Sessiya yangilanmoqda...")
                    self._init_session()
                    continue

                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 60))
                    logger.warning(f"Rate limit, {wait}s kutilmoqda...")
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()

                trains = (
                    data.get("data", {})
                    .get("directions", {})
                    .get("forward", {})
                    .get("trains", [])
                )
                logger.info(f"Qidiruv: {from_code}→{to_code} {date} — {len(trains)} poyezd")
                return trains

            except requests.exceptions.Timeout:
                logger.warning(f"Timeout (urinish {attempt}/{self.MAX_RETRIES})")
                if attempt < self.MAX_RETRIES:
                    time.sleep(10 * attempt)

            except requests.exceptions.ConnectionError:
                logger.warning(f"Ulanish xatosi (urinish {attempt}/{self.MAX_RETRIES})")
                if attempt < self.MAX_RETRIES:
                    time.sleep(15)

            except Exception as e:
                logger.error(f"Search xato: {e}")
                break

        return []
