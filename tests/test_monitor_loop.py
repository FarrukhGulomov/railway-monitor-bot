"""_monitor_loop integratsion testlari (audit P0#1 va P0#2 acceptance criteria):

- API/tarmoq xatosi hech qachon "bilet/joy yo'q" deb ko'rsatilmasligi kerak.
- Bir xil mavjud joy keyingi tekshiruvda qayta "yangi joy topildi" deb
  yuborilmasligi, monitorni bekorga to'xtatmasligi kerak.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


MON = {
    "from_name": "A", "from_code": "1", "to_name": "B", "to_code": "2",
    "date": "2099-01-01", "car_type": "any", "active": True,
    "check_count": 0, "last_check": None,
}


def _train(number, price):
    return {
        "number": number, "brand": "X",
        "departureDate": "2099-01-01 08:00", "arrivalDate": "2099-01-01 10:00",
        "cars": [{
            "type": "any", "freeSeats": 1,
            "tariffs": [{"tariff": price, "freeSeats": 1, "classServiceType": "any"}],
        }],
    }


@pytest.fixture
def fresh_db(bot_module, tmp_path, monkeypatch):
    from database import Database
    new_db = Database(path=str(tmp_path / "data.json"))
    monkeypatch.setattr(bot_module, "db", new_db)
    return new_db


class TestMonitorLoopReliability:
    def test_errors_dont_say_no_tickets_and_dedup_prevents_false_new_seat(
        self, bot_module, fresh_db, monkeypatch
    ):
        SearchResult = bot_module.SearchResult
        train_a = _train("A1", 100_000)
        train_b = _train("B2", 50_000)

        responses = (
            [SearchResult(False, [], "network")] * 5      # xato — "bilet yo'q" demasin
            + [SearchResult(True, [train_a], "")]          # 1-muvaffaqiyat: "hozirda mavjud"
            + [SearchResult(True, [train_a], "")]          # bir xil joy — jim turishi kerak
            + [SearchResult(True, [train_a, train_b], "")]  # train_b yangi — xabar + to'xtash
        )
        calls = {"n": 0}

        async def fake_search(client, *a, **kw):
            r = responses[calls["n"]]
            calls["n"] += 1
            return r

        async def fake_get_client():
            return object()

        async def fast_sleep(_):
            return None

        monkeypatch.setattr(bot_module, "_shared_search", fake_search)
        monkeypatch.setattr(bot_module, "_get_railway_client", fake_get_client)
        monkeypatch.setattr(bot_module.asyncio, "sleep", fast_sleep)

        mid = fresh_db.save_monitor(100, dict(MON))
        app = MagicMock()
        app.bot.send_message = AsyncMock()

        asyncio.run(bot_module._monitor_loop(100, mid, dict(MON, id=mid), app))

        assert calls["n"] == len(responses)
        texts = [c.args[1] for c in app.bot.send_message.await_args_list]

        # P0#1: xato holatlarida HECH QACHON "bilet yo'q" deb da'vo qilinmasligi kerak
        # (ogohlantirish xabari "bu joy yo'qligini anglatmaydi" deb aniq aytadi — shu farqli)
        assert not any("mos bilet yo'q" in t for t in texts)
        assert any("vaqtinchalik muammo" in t for t in texts)
        assert any("tiklandi" in t for t in texts)

        # P0#2: bir xil joy qayta "yangi" deb yuborilmadi — jami faqat 4 xabar:
        # (xato ogohlantirish, tiklandi, hozirda mavjud, yangi joy) — dublikat yo'q
        assert len(texts) == 4
        assert sum("Hozirda mavjud" in t for t in texts) == 1
        assert sum("Yangi joy topildi" in t for t in texts) == 1

        # Yangi joy xabarida faqat haqiqatan yangi (B2) ko'rsatilgan, A1 emas
        new_seat_msg = next(t for t in texts if "Yangi joy topildi" in t)
        assert "B2" in new_seat_msg
        assert "A1" not in new_seat_msg

        # Monitor haqiqatan yangi joy topilgach to'xtagan
        assert fresh_db.is_active(mid) is False


def _fixed_now(bot_module, *args):
    """bot_module.datetime.now()ni belgilangan qiymatga qotirib qo'yish uchun
    subklass. Asl `datetime` klassi monkeypatch'dan OLDIN olinadi."""
    real_datetime = bot_module.datetime
    frozen = real_datetime(*args)

    class _FixedDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    return _FixedDatetime


class TestMonitorExpiry:
    """Sana bugun bo'lsa-da, belgilangan vaqt oralig'i (time_to) allaqachon
    o'tib ketgan bo'lsa, monitor bekorga qidirishni davom ettirmasligi,
    avtomatik to'xtashi kerak (foydalanuvchi hisoboti: 12:00-17:59 oralig'i
    o'tib ketgan bo'lsa ham, 23:30'da hali "10 marta ketma-ket bo'sh natija"
    deb qidiraverdi)."""

    def test_same_day_expired_time_range_stops_without_searching(
        self, bot_module, fresh_db, monkeypatch
    ):
        monkeypatch.setattr(bot_module, "datetime", _fixed_now(bot_module, 2030, 1, 10, 20, 0))

        search_called = {"n": 0}

        async def fake_search(client, *a, **kw):
            search_called["n"] += 1
            return bot_module.SearchResult(True, [], "")

        async def fake_get_client():
            return object()

        monkeypatch.setattr(bot_module, "_shared_search", fake_search)
        monkeypatch.setattr(bot_module, "_get_railway_client", fake_get_client)

        mon = dict(MON, date="2030-01-10", time_from="12:00", time_to="17:59")
        mid = fresh_db.save_monitor(100, mon)
        app = MagicMock()
        app.bot.send_message = AsyncMock()

        asyncio.run(bot_module._monitor_loop(100, mid, dict(mon, id=mid), app))

        assert search_called["n"] == 0  # umuman qidirmasligi kerak
        assert fresh_db.is_active(mid) is False
        text = app.bot.send_message.await_args_list[0].args[1]
        assert "o'tib ketdi" in text

    def test_multi_day_old_date_still_expires(self, bot_module, fresh_db, monkeypatch):
        monkeypatch.setattr(bot_module, "datetime", _fixed_now(bot_module, 2030, 1, 15, 10, 0))

        mon = dict(MON, date="2030-01-10", time_from="00:00", time_to="23:59")
        mid = fresh_db.save_monitor(100, mon)
        app = MagicMock()
        app.bot.send_message = AsyncMock()

        asyncio.run(bot_module._monitor_loop(100, mid, dict(mon, id=mid), app))

        assert fresh_db.is_active(mid) is False

    def test_overnight_range_not_prematurely_expired(self, bot_module, fresh_db, monkeypatch):
        # 22:00-02:00 oralig'i, hozir 23:00 — hali oraliq ichida, to'xtamasligi kerak
        monkeypatch.setattr(bot_module, "datetime", _fixed_now(bot_module, 2030, 1, 10, 23, 0))

        async def fake_search(client, *a, **kw):
            return bot_module.SearchResult(True, [], "")

        async def fake_get_client():
            return object()

        monkeypatch.setattr(bot_module, "_shared_search", fake_search)
        monkeypatch.setattr(bot_module, "_get_railway_client", fake_get_client)

        mon = dict(MON, date="2030-01-10", time_from="22:00", time_to="02:00")
        mid = fresh_db.save_monitor(100, mon)
        sleep_called = {"n": 0}

        async def fake_sleep_then_stop(_):
            sleep_called["n"] += 1
            fresh_db.deactivate(mid)

        monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep_then_stop)

        app = MagicMock()
        app.bot.send_message = AsyncMock()

        asyncio.run(bot_module._monitor_loop(100, mid, dict(mon, id=mid), app))

        # Tsikl tabiiy tarzda sleep'gacha yetib bordi — muddati tugagan deb
        # erta to'xtatilmadi (bo'sh natija xabari — kutilgan, aloqasi yo'q)
        assert sleep_called["n"] == 1
        texts = [c.args[1] for c in app.bot.send_message.await_args_list]
        assert not any("o'tib ketdi" in t for t in texts)
