"""Uzrailpass API hujjati asosidagi o'zgarishlar: stansiya kodlari + migratsiya,
429 moslashuvchan tezlik, vagon/joy raqamlari."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from railway_client import RailwayClient
from tests.test_railway_client import _FakeResponse, _make_client


class TestStationCodes:
    @pytest.mark.parametrize("name,code", [
        ("🕌 Samarqand", "2900700"), ("🏔 Andijon", "2900680"), ("🌿 Namangan", "2900940"),
        ("🌄 Qarshi", "2900750"), ("🌊 Nukus", "2900970"), ("🌿 Urganch", "2900790"),
        ("☀️ Termiz", "2900255"), ("🏯 Xiva", "2900172"), ("🏙 Navoiy", "2900930"),
        ("🌳 Jizzax", "2900720"), ("🌆 Guliston", "2900236"), ("🍇 Qo'qon", "2900880"),
        ("🌾 Marg'ilon", "2900920"), ("🏙 Toshkent", "2900000"), ("🕌 Buxoro", "2900800"),
    ])
    def test_verified_codes(self, bot_module, name, code):
        assert bot_module.STATIONS[name] == code

    def test_codes_unique(self, bot_module):
        codes = list(bot_module.STATIONS.values())
        assert len(codes) == len(set(codes))

    def test_unverified_ferghana_removed(self, bot_module):
        assert not any("Farg'ona" in k for k in bot_module.STATIONS)


class TestStationMigration:
    def test_by_name_old_samarkand_becomes_new_samarkand_not_andijan(self, bot_module):
        m = {"from_name": "🕌 Samarqand", "from_code": "2900680",
             "to_name": "🏔 Andijon", "to_code": "2900100"}
        ch = bot_module._migrate_station_codes(m)
        assert ch["from_code"] == "2900700" and ch["to_code"] == "2900680"
        assert ch["stations_v2"] is True

    def test_fallback_by_old_code_when_name_unknown(self, bot_module):
        m = {"from_name": "?", "from_code": "2900900", "to_name": "?", "to_code": "2900800"}
        ch = bot_module._migrate_station_codes(m)
        assert ch["from_code"] == "2900750" and "to_code" not in ch  # Buxoro o'zgarmaydi

    def test_idempotent_with_flag(self, bot_module):
        assert bot_module._migrate_station_codes({"stations_v2": True, "from_code": "2900680"}) is None

    def test_db_migration_runs_once_in_single_pass(self, bot_module, tmp_path):
        from database import Database
        db = Database(path=str(tmp_path / "d.json"))
        mid = db.save_monitor(1, {"from_name": "🕌 Samarqand", "from_code": "2900680",
                                  "to_name": "🏙 Toshkent", "to_code": "2900000", "active": True})
        assert db.migrate_monitors(bot_module._migrate_station_codes) == 1
        assert db.migrate_monitors(bot_module._migrate_station_codes) == 0  # ikkinchi marta — yo'q
        m = db.get_active_monitors(1)[0]
        assert m["from_code"] == "2900700" and m["stations_v2"] is True

    def test_new_monitors_are_flagged(self, bot_module):
        import inspect
        assert '"stations_v2": True' in inspect.getsource(bot_module._confirm_and_start)


class TestRateLimitAdaptation:
    def test_429_returns_immediately_and_sets_global_cooldown(self, monkeypatch):
        client = _make_client(monkeypatch, [_FakeResponse(429, headers={})] * 3)
        r = client.search_trains("A", "B", "2030-01-01")
        assert r.ok is False and r.error == "429 rate limited"
        assert client._session.calls == 1                       # ichida qayta urinmaydi
        assert client._cooldown_until > 0 and client._extra_interval == 2.0

    def test_retry_after_header_respected_and_capped(self, monkeypatch):
        client = _make_client(monkeypatch, [])
        assert client._on_rate_limited({"Retry-After": "7"}) == 7
        assert client._on_rate_limited({"Retry-After": "9999"}) == client.MAX_COOLDOWN

    def test_backoff_doubles_without_header(self, monkeypatch):
        client = _make_client(monkeypatch, [])
        waits = [client._on_rate_limited({}) for _ in range(4)]
        assert waits == [30.0, 60.0, 120.0, 120.0]

    def test_success_decays_extra_interval_and_resets_counter(self, monkeypatch):
        payload = {"data": {"directions": {"forward": {"trains": []}}}}
        client = _make_client(monkeypatch, [_FakeResponse(200, json_data=payload)])
        client._extra_interval = 4.0
        client._consecutive_429 = 3
        client.search_trains("A", "B", "2030-01-01")
        assert client._extra_interval == 3.75 and client._consecutive_429 == 0

    def test_extra_interval_capped(self, monkeypatch):
        client = _make_client(monkeypatch, [])
        for _ in range(20):
            client._on_rate_limited({"Retry-After": "1"})
        assert client._extra_interval == client.MAX_EXTRA_INTERVAL


class TestPlaces:
    GROUPS = [
        {"type": "Biznes", "typeShow": "Biznes", "cars": [
            {"number": "01", "places": [1, 2, 3]}, {"number": "02", "places": []}]},
        {"type": "Econom", "cars": [
            {"number": "04", "places": list(range(1, 15))}, {"number": "05", "places": [7]}]},
    ]

    def test_line_for_matching_class(self, bot_module):
        assert bot_module._places_line(self.GROUPS, "Biznes") == "🚃 Vagon 01: 1, 2, 3"

    def test_line_truncates_long_place_lists(self, bot_module):
        line = bot_module._places_line(self.GROUPS, "Econom")
        assert "04: 1, 2, 3, 4, 5, 6, 7, 8 …" in line and "05: 7" in line

    def test_no_match_or_bad_input_is_empty(self, bot_module):
        assert bot_module._places_line(self.GROUPS, "VIP") == ""
        assert bot_module._places_line(None, "Biznes") == ""
        assert bot_module._places_line("x", None) == ""

    def test_client_places_success_and_failure(self, monkeypatch):
        ok = {"data": {"train": {"carGroup": [{"type": "Biznes"}]}}}
        client = _make_client(monkeypatch, [_FakeResponse(200, json_data=ok), _FakeResponse(500)])
        tr = {"number": "766Ф", "trainId": None,
              "subRoute": {"depStationCode": "2900000", "arvStationCode": "2900800"}}
        assert client.get_train_places(tr, "2030-01-01") == [{"type": "Biznes"}]
        assert client.get_train_places(tr, "2030-01-01") is None      # xato — "joy yo'q" emas
        assert client.get_train_places({"number": "x"}, "2030-01-01") is None  # subRoute yo'q

    def test_fetch_is_limited_and_tolerates_failures(self, bot_module):
        calls = []

        class C:
            def get_train_places(self, train, d):
                calls.append(train["number"])
                if train["number"] == "bad":
                    raise RuntimeError("x")
                return [{"type": "Biznes"}]

        items = [({"number": n}, {}, 1, 1, "x") for n in ["bad", "a", "a", "b", "c", "d"]]
        out = asyncio.run(bot_module._fetch_places(C(), items, "2030-01-01"))
        assert calls == ["bad", "a", "b"]              # eng ko'pi bilan 3 ta poyezd
        assert set(out) == {"a", "b"}

    def test_client_without_method_is_ignored(self, bot_module):
        assert asyncio.run(bot_module._fetch_places(object(), [({"number": "1"},)], "d")) == {}


class TestFoundMessageShowsPlaces:
    def test_vagon_and_seat_numbers_appear_in_alert(self, bot_module, tmp_path, monkeypatch):
        from database import Database
        from tests.test_monitor_loop import MON, _fixed_now
        db = Database(path=str(tmp_path / "d.json"))
        monkeypatch.setattr(bot_module, "db", db)
        monkeypatch.setattr(bot_module, "datetime", _fixed_now(bot_module, 2030, 1, 10, 10, 0))
        train = {"number": "766Ф", "brand": "Afrosiyob", "trainId": None,
                 "departureDate": "10.01.2030 12:00", "arrivalDate": "10.01.2030 16:00",
                 "subRoute": {"depStationCode": "1", "arvStationCode": "2"},
                 "cars": [{"type": "Biznes", "freeSeats": 3,
                           "tariffs": [{"classServiceType": "1B", "freeSeats": 3, "tariff": 900000}]}]}

        class Client:
            def get_train_places(self, tr, d):
                return [{"type": "Biznes", "cars": [{"number": "03", "places": [11, 12, 13]}]}]

        async def fake_search(client, *a, **kw):
            return bot_module.SearchResult(True, [train], "")

        async def fake_client():
            return Client()

        mon = dict(MON, date="2030-01-10")
        mid = db.save_monitor(100, mon)

        async def stop(_):
            db.deactivate(mid)

        monkeypatch.setattr(bot_module, "_shared_search", fake_search)
        monkeypatch.setattr(bot_module, "_get_railway_client", fake_client)
        monkeypatch.setattr(bot_module.asyncio, "sleep", stop)
        app = MagicMock(); app.bot.send_message = AsyncMock()
        asyncio.run(bot_module._monitor_loop(100, mid, dict(mon, id=mid), app))
        text = app.bot.send_message.await_args_list[0].args[1]
        assert "🚃 Vagon 03: 11, 12, 13" in text


class TestRepeated429RefreshesSession:
    def test_second_429_resets_session(self, monkeypatch):
        client = _make_client(monkeypatch, [_FakeResponse(429, text="Too Many")] * 3)
        inits = []
        monkeypatch.setattr(RailwayClient, "_init_session", lambda self: inits.append(1))
        client.search_trains("A", "B", "2030-01-01")
        assert inits == []                      # birinchi 429 — faqat sovutish
        client._cooldown_until = 0
        client.search_trains("A", "B", "2030-01-01")
        assert inits == [1]                     # ikkinchisida sessiya yangilandi
