"""Poyezd (brend) va sinf (vagon turi) alohida filtr sifatida."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_handlers import _make_callback_update, _make_context


def _car(ctype, seats=10, price=500_000, cls="X"):
    return {"type": ctype, "freeSeats": seats,
            "tariffs": [{"classServiceType": cls, "freeSeats": seats, "tariff": price}]}


AFRO = {"number": "778", "brand": "Afrosiyob", "departureDate": "30.11.2026 08:00",
        "arrivalDate": "30.11.2026 10:30",
        "cars": [_car("Biznes", 5, 900_000, "1B"), _car("Econom", 40, 400_000, "2E")]}
MANG = {"number": "751M", "brand": "Jaloliddin Manguberdi", "departureDate": "30.11.2026 07:10",
        "arrivalDate": "30.11.2026 15:22",
        "cars": [_car("Biznes", 29, 1_006_000, "1B"), _car("Ekonom", 152, 780_000, "2E")]}
PASS = {"number": "056Ч", "brand": "Yo'lovchi", "departureDate": "30.11.2026 21:45",
        "arrivalDate": "01.12.2026 05:12",
        "cars": [_car("Plaskartli", 174, 177_990, "3П"), _car("Kupe", 12, 243_750, "2К")]}
ALL = [AFRO, MANG, PASS]


def numbers(found):
    return sorted({f[0]["number"] for f in found})


class TestFilterCombination:
    def test_brand_only(self, bot_module):
        assert numbers(bot_module._find_all_trains(ALL, "any", brand_filter="manguberdi")) == ["751M"]

    def test_class_only_across_brands(self, bot_module):
        found = bot_module._find_all_trains(ALL, "biznes")
        assert numbers(found) == ["751M", "778"]
        assert all(f[1]["type"] == "Biznes" for f in found)

    def test_brand_and_class_together(self, bot_module):
        found = bot_module._find_all_trains(ALL, "econom", brand_filter="manguberdi")
        assert numbers(found) == ["751M"]
        assert [f[3] for f in found] == [152]

    def test_brand_and_class_mismatch_is_empty(self, bot_module):
        assert bot_module._find_all_trains(ALL, "platskar", brand_filter="afrosiyob") == []

    def test_ordinary_passenger_brand(self, bot_module):
        assert numbers(bot_module._find_all_trains(ALL, "any", brand_filter="yolovchi")) == ["056Ч"]

    def test_passenger_brand_plus_kupe(self, bot_module):
        found = bot_module._find_all_trains(ALL, "coupe", brand_filter="yolovchi")
        assert [f[1]["type"] for f in found] == ["Kupe"]

    def test_default_returns_everything(self, bot_module):
        assert numbers(bot_module._find_all_trains(ALL, "any")) == ["056Ч", "751M", "778"]

    def test_legacy_brand_in_car_type_still_works(self, bot_module):
        # Eski kuzatuvlar: car_type="afrosiyob", brand maydoni yo'q
        assert numbers(bot_module._find_all_trains(ALL, "afrosiyob")) == ["778"]


class TestLabels:
    def test_both(self, bot_module):
        assert bot_module._filter_label({"brand": "afrosiyob", "car_type": "biznes"}) == "🚄 Afrosiyob · 💼 Biznes"

    def test_brand_only_and_class_only(self, bot_module):
        assert "Manguberdi" in bot_module._filter_label({"brand": "manguberdi", "car_type": "any"})
        assert bot_module._filter_label({"car_type": "coupe"}) == "🛏 Kupe"

    def test_legacy_format(self, bot_module):
        assert bot_module._filter_label({"car_type": "sharq"}) == "🚅 Sharq"

    def test_nothing(self, bot_module):
        assert "Barchasi" in bot_module._filter_label({"car_type": "any"})


class TestFlow:
    def test_brand_step_follows_date_and_precedes_class(self, bot_module):
        s = bot_module.STEPS
        assert s.index("date") < s.index("brand") < s.index("car") < s.index("time")

    def test_brand_step_has_nav(self, bot_module):
        _, kb = bot_module._step_view("brand", {})
        datas = [b.callback_data for r in kb.inline_keyboard for b in r]
        assert "nav|back" in datas and "brand|manguberdi" in datas and "brand|yolovchi" in datas

    def test_got_brand_stores_and_moves_to_car(self, bot_module):
        update, q = _make_callback_update(uid=999, data="brand|afrosiyob")
        bot_module.Config.ADMIN_IDS = [999]
        ctx = _make_context()
        ctx.user_data = {"_step": "brand"}
        assert asyncio.run(bot_module.got_brand(update, ctx)) == bot_module.WAIT_CAR_TYPE
        assert ctx.user_data["brand"] == "afrosiyob"


@pytest.fixture
def fresh_db(bot_module, tmp_path, monkeypatch):
    from database import Database
    d = Database(path=str(tmp_path / "d.json"))
    monkeypatch.setattr(bot_module, "db", d)
    monkeypatch.setattr(bot_module.Config, "ADMIN_IDS", [999])
    return d


BASE = {"from_name": "A", "to_name": "B", "date": "2099-01-01", "active": True}


class TestEditing:
    def test_edit_class_keeps_legacy_brand(self, bot_module, fresh_db):
        mid = fresh_db.save_monitor(999, dict(BASE, car_type="afrosiyob"))
        upd, q = _make_callback_update(uid=999, data="mgr_cv|biznes")
        ctx = _make_context(); ctx.user_data = {"edit_mid": mid}
        asyncio.run(bot_module.mgr_car_value(upd, ctx))
        m = fresh_db.get_active_monitors(999)[0]
        assert (m["brand"], m["car_type"]) == ("afrosiyob", "biznes")

    def test_edit_brand_clears_legacy_brand_in_car_type(self, bot_module, fresh_db):
        mid = fresh_db.save_monitor(999, dict(BASE, car_type="sharq"))
        upd, q = _make_callback_update(uid=999, data="mgr_bv|manguberdi")
        ctx = _make_context(); ctx.user_data = {"edit_mid": mid}
        asyncio.run(bot_module.mgr_brand_value(upd, ctx))
        m = fresh_db.get_active_monitors(999)[0]
        assert (m["brand"], m["car_type"]) == ("manguberdi", "any")

    def test_edit_menu_has_brand_and_class(self, bot_module, fresh_db):
        mid = fresh_db.save_monitor(999, dict(BASE, car_type="any"))
        upd, q = _make_callback_update(uid=999, data=f"mgr_edit|{mid}")
        asyncio.run(bot_module.mgr_edit(upd, _make_context()))
        kb = q.edit_message_text.await_args.kwargs["reply_markup"]
        datas = [b.callback_data for r in kb.inline_keyboard for b in r]
        assert f"mgr_ef|brand|{mid}" in datas and f"mgr_ef|car|{mid}" in datas


class TestMonitorLoopUsesBrand:
    def test_loop_passes_brand_to_filter(self, bot_module, fresh_db, monkeypatch):
        seen = {}
        orig = bot_module._find_all_trains

        def spy(*a, **kw):
            seen.update(kw)
            return orig(*a, **kw)

        monkeypatch.setattr(bot_module, "_find_all_trains", spy)

        async def fake_search(client, *a, **kw):
            return bot_module.SearchResult(True, ALL, "")

        async def fake_client():
            return object()

        from datetime import datetime as real_dt
        mon = dict(BASE, car_type="econom", brand="manguberdi", date="2099-01-01",
                   from_code="1", to_code="2", time_from="00:00", time_to="23:59",
                   max_price=None, min_seats=1)
        mid = fresh_db.save_monitor(999, mon)

        async def stop(_):
            fresh_db.deactivate(mid)

        monkeypatch.setattr(bot_module, "_shared_search", fake_search)
        monkeypatch.setattr(bot_module, "_get_railway_client", fake_client)
        monkeypatch.setattr(bot_module.asyncio, "sleep", stop)
        app = MagicMock(); app.bot.send_message = AsyncMock()
        asyncio.run(bot_module._monitor_loop(999, mid, dict(mon, id=mid), app))
        assert seen.get("brand_filter") == "manguberdi"
        text = app.bot.send_message.await_args_list[0].args[1]
        assert "751M" in text and "778" not in text
