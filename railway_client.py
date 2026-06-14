"""
Railway.uz API Client — Playwright (haqiqiy brauzer)
Cloudflare himoyasini chetlab o'tadi
"""

import asyncio
import logging
import json
from datetime import datetime
from typing import Optional

logger = logging.getLogger("railway_client")


class RailwayClient:
    BASE = "https://eticket.railway.uz"
    SEARCH_URL = f"{BASE}/api/v3/handbook/trains/list"

    def __init__(self):
        self._browser = None
        self._context = None
        self._page = None
        self._ready = False

    async def start(self):
        """Brauzerni ishga tushirish"""
        try:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ]
            )
            self._context = await self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="uz-UZ",
                timezone_id="Asia/Tashkent",
                viewport={"width": 1280, "height": 800},
            )
            self._page = await self._context.new_page()

            # Automation belgilarini yashirish
            await self._page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
            """)

            # Saytga kirib session olish
            await self._page.goto(self.BASE, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
            self._ready = True
            logger.info("✅ Brauzer tayyor")
            return True

        except Exception as e:
            logger.error(f"Brauzer ishga tushmadi: {e}")
            return False

    async def stop(self):
        """Brauzerni yopish"""
        try:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass

    async def search_trains(self, from_code: str, to_code: str, date: str) -> list:
        """
        Poyezdlarni qidirish — brauzer orqali API ga so'rov
        """
        if not self._ready:
            ok = await self.start()
            if not ok:
                return []

        try:
            # API ga brauzer kontekstida fetch qilish
            result = await self._page.evaluate("""
                async ([url, from_code, to_code, date]) => {
                    try {
                        const resp = await fetch(url, {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'Accept': 'application/json',
                                'Accept-Language': 'uz',
                            },
                            body: JSON.stringify({
                                directions: {
                                    forward: {
                                        date: date,
                                        depStationCode: from_code,
                                        arvStationCode: to_code,
                                    }
                                }
                            })
                        });
                        const data = await resp.json();
                        return {ok: true, status: resp.status, data: data};
                    } catch(e) {
                        return {ok: false, error: e.toString()};
                    }
                }
            """, [self.SEARCH_URL, from_code, to_code, date])

            if not result.get("ok"):
                logger.error(f"Fetch xato: {result.get('error')}")
                # Sessiya yangilash
                await self._page.goto(self.BASE, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2)
                return []

            if result.get("status") == 401:
                logger.info("Sessiya yangilanmoqda...")
                await self._page.goto(self.BASE, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2)
                return []

            trains = (
                result.get("data", {})
                .get("data", {})
                .get("directions", {})
                .get("forward", {})
                .get("trains", [])
            )

            logger.info(f"Topildi: {from_code}→{to_code} {date} — {len(trains)} poyezd")
            return trains

        except Exception as e:
            logger.error(f"search_trains xato: {e}")
            self._ready = False
            return []


# Global client — bir marta ishga tushiriladi
_client: Optional[RailwayClient] = None


async def get_client() -> RailwayClient:
    global _client
    if _client is None:
        _client = RailwayClient()
        await _client.start()
    return _client
