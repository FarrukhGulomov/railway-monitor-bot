"""`_shared_search` koordinatori testlari: route/date deduplikatsiya,
bounded concurrency, kesh TTL'ning CHECK_INTERVAL'ga nisbatan chegaralanishi.

Bu modul `bot.py`dagi `_search_cache`/`_search_inflight`/`_search_semaphore`
kabi modul darajasidagi holatni ishlatadi — shuning uchun har bir test
`fresh_scheduler` fixture orqali bu holatni tozalaydi (aks holda testlar
bir-biriga ta'sir qilishi mumkin)."""

import asyncio
import time

import pytest


@pytest.fixture
def fresh_scheduler(bot_module):
    """Scheduler bilan bog'liq modul darajasidagi global holatni har bir
    testdan oldin/keyin tozalaydi."""
    bot_module._search_cache.clear()
    bot_module._search_inflight.clear()
    bot_module._search_semaphore = None
    yield
    bot_module._search_cache.clear()
    bot_module._search_inflight.clear()
    bot_module._search_semaphore = None


class _FakeClient:
    """`RailwayClient.search_trains` o'rnini bosuvchi — chaqiruvlarni
    hisoblaydi va ixtiyoriy ravishda bloklanishi mumkin (concurrency
    testlari uchun)."""

    def __init__(self, delay=0.0):
        self.delay = delay
        self.calls = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = __import__("threading").Lock()

    def search_trains(self, from_code, to_code, date):
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.calls.append((from_code, to_code, date))
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            self.in_flight -= 1
        from railway_client import SearchResult
        return SearchResult(True, [], "")


class TestSingleFlightDedup:
    def test_same_route_date_concurrent_calls_dedup_to_one_upstream_call(
        self, bot_module, fresh_scheduler
    ):
        client = _FakeClient(delay=0.05)

        async def run():
            return await asyncio.gather(
                bot_module._shared_search(client, "A", "B", "2030-01-01"),
                bot_module._shared_search(client, "A", "B", "2030-01-01"),
                bot_module._shared_search(client, "A", "B", "2030-01-01"),
            )

        results = asyncio.run(run())
        assert all(r.ok for r in results)
        assert len(client.calls) == 1, (
            "Bir xil marshrut+sana uchun deyarli bir vaqtdagi 3 ta so'rov "
            "faqat BITTA haqiqiy upstream chaqiruviga olib kelishi kerak edi"
        )

    def test_different_routes_are_not_deduped(self, bot_module, fresh_scheduler):
        client = _FakeClient()

        async def run():
            return await asyncio.gather(
                bot_module._shared_search(client, "A", "B", "2030-01-01"),
                bot_module._shared_search(client, "C", "D", "2030-01-01"),
            )

        asyncio.run(run())
        assert len(client.calls) == 2

    def test_cache_hit_within_ttl_avoids_second_upstream_call(
        self, bot_module, fresh_scheduler
    ):
        client = _FakeClient()

        async def run():
            r1 = await bot_module._shared_search(client, "A", "B", "2030-01-01")
            r2 = await bot_module._shared_search(client, "A", "B", "2030-01-01")
            return r1, r2

        asyncio.run(run())
        # Ikkinchi (ketma-ket, bir zumda) so'rov kesh ichidan javob olishi
        # kerak — TTL ichida bo'lgani uchun.
        assert len(client.calls) == 1


class TestBoundedConcurrency:
    def test_concurrency_is_bounded_by_config(self, bot_module, fresh_scheduler, monkeypatch):
        monkeypatch.setattr(bot_module.Config, "RAILWAY_MAX_CONCURRENCY", 2)
        client = _FakeClient(delay=0.1)

        async def run():
            # 4 ta MUSTAQIL (turli) marshrut — hech biri dedup qilinmaydi,
            # lekin bir vaqtning o'zida faqat 2 tasi "parvozda" bo'lishi kerak.
            await asyncio.gather(*[
                bot_module._shared_search(client, f"R{i}", "X", "2030-01-01")
                for i in range(4)
            ])

        asyncio.run(run())
        assert client.max_in_flight <= 2, (
            f"Bir vaqtning o'zida {client.max_in_flight} ta so'rov parvozda edi — "
            "RAILWAY_MAX_CONCURRENCY=2 dan oshmasligi kerak edi"
        )
        assert len(client.calls) == 4  # barcha marshrutlar baribir qidirildi

    def test_concurrency_of_one_matches_old_fully_serial_behavior(
        self, bot_module, fresh_scheduler, monkeypatch
    ):
        monkeypatch.setattr(bot_module.Config, "RAILWAY_MAX_CONCURRENCY", 1)
        client = _FakeClient(delay=0.05)

        async def run():
            await asyncio.gather(*[
                bot_module._shared_search(client, f"R{i}", "X", "2030-01-01")
                for i in range(3)
            ])

        asyncio.run(run())
        assert client.max_in_flight == 1


class TestCacheTtlBoundedByCheckInterval:
    """P1: kesh muddati hech qachon CHECK_INTERVAL'ning yarmidan katta
    bo'lmasligi kerak — aks holda bitta monitorning o'z navbatdagi
    tekshiruvi eskirgan natija bilan 'aldanib', aniqlash kechikishini
    sun'iy oshirishi mumkin edi."""

    def test_effective_ttl_capped_at_half_check_interval(self, monkeypatch):
        from config import Config
        monkeypatch.setattr(Config, "SEARCH_CACHE_TTL", 8.0)
        monkeypatch.setattr(Config, "CHECK_INTERVAL", 10)
        assert Config.effective_search_cache_ttl() == 5.0  # 10/2, 8.0 emas

    def test_effective_ttl_uses_configured_value_when_smaller(self, monkeypatch):
        from config import Config
        monkeypatch.setattr(Config, "SEARCH_CACHE_TTL", 3.0)
        monkeypatch.setattr(Config, "CHECK_INTERVAL", 60)
        assert Config.effective_search_cache_ttl() == 3.0

    def test_effective_ttl_has_floor(self, monkeypatch):
        from config import Config
        monkeypatch.setattr(Config, "SEARCH_CACHE_TTL", 0.1)
        monkeypatch.setattr(Config, "CHECK_INTERVAL", 10)
        assert Config.effective_search_cache_ttl() >= 1.0
