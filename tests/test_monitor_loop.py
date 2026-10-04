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


class TestAvailabilityLifecycle:
    """P0: 'reappearing ticket' snapshot modeli. Eski kumulyativ
    (`seen |= fingerprints`) usul g'oyib bo'lib qaytgan joyni abadiy
    'ko'rilgan' deb saqlab, qayta paydo bo'lishini sezmasdi — bu klass
    aynan shu stsenariylarni tekshiradi."""

    def _run(self, bot_module, fresh_db, monkeypatch, sequence, mon_overrides=None):
        """`sequence` — har bir tekshiruv uchun qaytariladigan train
        ro'yxatlari ketma-ketligi (masalan [[A], [], [A]]). Oxirgi
        elementdan keyin ham shu natija qaytaveradi; test o'zini-o'zi
        cheklovchi xavfsizlik chegarasi bilan tugaydi (monitor "yangi joy"
        topib o'zi to'xtamasa ham abadiy aylanib qolmasligi uchun)."""
        mon = dict(MON, **(mon_overrides or {}))
        mid = fresh_db.save_monitor(100, mon)
        calls = {"n": 0}

        async def fake_search(client, *a, **kw):
            idx = min(calls["n"], len(sequence) - 1)
            calls["n"] += 1
            return bot_module.SearchResult(True, sequence[idx], "")

        async def fake_get_client():
            return object()

        iterations = {"n": 0}

        async def fake_sleep(_):
            iterations["n"] += 1
            if iterations["n"] > len(sequence) + 2:
                fresh_db.deactivate(mid)

        monkeypatch.setattr(bot_module, "_shared_search", fake_search)
        monkeypatch.setattr(bot_module, "_get_railway_client", fake_get_client)
        monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)

        app = MagicMock()
        app.bot.send_message = AsyncMock()
        asyncio.run(bot_module._monitor_loop(100, mid, dict(mon, id=mid), app))
        texts = [c.args[1] for c in app.bot.send_message.await_args_list]
        return texts, mid

    def test_a_then_a_no_duplicate_notification(self, bot_module, fresh_db, monkeypatch):
        a = _train("A1", 100_000)
        texts, _ = self._run(bot_module, fresh_db, monkeypatch, [[a], [a], [a]])
        assert sum("Hozirda mavjud" in t for t in texts) == 1
        assert sum("Yangi joy topildi" in t for t in texts) == 0

    def test_none_then_a_is_detected_as_first_availability(self, bot_module, fresh_db, monkeypatch):
        a = _train("A1", 100_000)
        texts, _ = self._run(bot_module, fresh_db, monkeypatch, [[], [a]])
        # Birinchi tekshiruv bo'sh — "hozircha yo'q", ikkinchisida paydo
        # bo'lgani uchun monitor hali ham "Hozirda mavjud" (first_run)
        # emas, balki keyingi tekshiruvda "Yangi joy topildi" deb topadi.
        assert any("mos bilet yo'q" in t for t in texts)
        assert sum("Yangi joy topildi" in t for t in texts) == 1

    def test_a_then_disappears_then_reappears_is_detected_again(
        self, bot_module, fresh_db, monkeypatch
    ):
        a = _train("A1", 100_000)
        texts, mid = self._run(bot_module, fresh_db, monkeypatch, [[a], [], [a]])

        assert sum("Hozirda mavjud" in t for t in texts) == 1
        new_msgs = [t for t in texts if "Yangi joy topildi" in t]
        assert len(new_msgs) == 1, (
            "A g'oyib bo'lib qaytib kelgach qayta 'yangi' deb aniqlanishi kerak edi "
            "(eski kumulyativ usulda bu yerda hech qanday xabar bo'lmas edi)"
        )
        assert "A1" in new_msgs[0]
        # Haqiqatan yangi deb topilgandan keyin mahsulot qoidasi bo'yicha to'xtaydi
        assert fresh_db.is_active(mid) is False

    def test_a_then_b_only_b_is_new(self, bot_module, fresh_db, monkeypatch):
        a, b = _train("A1", 100_000), _train("B2", 50_000)
        texts, _ = self._run(bot_module, fresh_db, monkeypatch, [[a], [b]])

        new_msgs = [t for t in texts if "Yangi joy topildi" in t]
        assert len(new_msgs) == 1
        assert "B2" in new_msgs[0]
        assert "A1" not in new_msgs[0]

    def test_a_then_a_plus_b_only_b_is_new(self, bot_module, fresh_db, monkeypatch):
        a, b = _train("A1", 100_000), _train("B2", 50_000)
        texts, _ = self._run(bot_module, fresh_db, monkeypatch, [[a], [a, b]])

        assert sum("Hozirda mavjud" in t for t in texts) == 1
        new_msgs = [t for t in texts if "Yangi joy topildi" in t]
        assert len(new_msgs) == 1
        assert "B2" in new_msgs[0]
        assert "A1" not in new_msgs[0]


class TestSeatThreshold:
    """P1: min_seats chegarasidan o'tish/o'tmasligi to'g'ri aniqlanishi
    kerak — joy soni fingerprint'ning o'zida emas, `_find_all_trains`
    filtrida hal qilinadi (fingerprint barqarorligi uchun), lekin oxirgi
    natija baribir to'g'ri bo'lishi kerak."""

    def _train_with_seats(self, number, seats):
        return {
            "number": number, "brand": "X",
            "departureDate": "2099-01-01 08:00", "arrivalDate": "2099-01-01 10:00",
            "cars": [{
                "type": "any", "freeSeats": seats,
                "tariffs": [{"tariff": 100_000, "freeSeats": seats, "classServiceType": "any"}],
            }],
        }

    def test_below_threshold_no_alert_then_above_threshold_alerts(
        self, bot_module, fresh_db, monkeypatch
    ):
        one_seat = self._train_with_seats("A1", 1)
        three_seats = self._train_with_seats("A1", 3)
        mon = dict(MON, min_seats=2)
        mid = fresh_db.save_monitor(100, mon)

        calls = {"n": 0}
        sequence = [[one_seat], [three_seats]]

        async def fake_search(client, *a, **kw):
            idx = min(calls["n"], len(sequence) - 1)
            calls["n"] += 1
            return bot_module.SearchResult(True, sequence[idx], "")

        async def fake_get_client():
            return object()

        iterations = {"n": 0}

        async def fake_sleep(_):
            iterations["n"] += 1
            if iterations["n"] > 3:
                fresh_db.deactivate(mid)

        monkeypatch.setattr(bot_module, "_shared_search", fake_search)
        monkeypatch.setattr(bot_module, "_get_railway_client", fake_get_client)
        monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)

        app = MagicMock()
        app.bot.send_message = AsyncMock()
        asyncio.run(bot_module._monitor_loop(100, mid, dict(mon, id=mid), app))

        texts = [c.args[1] for c in app.bot.send_message.await_args_list]
        # 1 ta joy, min_seats=2 — hali hech narsa ko'rsatilmaydi (birinchi
        # tekshiruv "hozircha mos bilet yo'q" deydi, chunki 1 joy yetarli emas)
        assert any("mos bilet yo'q" in t for t in texts)
        # 3 ta joyga chiqqach — chegaradan o'tdi, aniqlanadi
        assert sum("Yangi joy topildi" in t for t in texts) == 1

    def test_seats_drop_below_then_recover_above_alerts_again(
        self, bot_module, fresh_db, monkeypatch
    ):
        # 3 → 0 → 3: oraliqda chegaradan pastga tushadi (filtr tomonidan
        # butunlay chetlab o'tiladi), keyin qayta chegaradan oshadi —
        # qayta aniqlanishi kerak (reappearance logikasi bilan bir xil).
        three = self._train_with_seats("A1", 3)
        zero = self._train_with_seats("A1", 0)
        mon = dict(MON, min_seats=2)
        mid = fresh_db.save_monitor(100, mon)
        sequence = [[three], [zero], [three]]
        calls = {"n": 0}

        async def fake_search(client, *a, **kw):
            idx = min(calls["n"], len(sequence) - 1)
            calls["n"] += 1
            return bot_module.SearchResult(True, sequence[idx], "")

        async def fake_get_client():
            return object()

        iterations = {"n": 0}

        async def fake_sleep(_):
            iterations["n"] += 1
            if iterations["n"] > 4:
                fresh_db.deactivate(mid)

        monkeypatch.setattr(bot_module, "_shared_search", fake_search)
        monkeypatch.setattr(bot_module, "_get_railway_client", fake_get_client)
        monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)

        app = MagicMock()
        app.bot.send_message = AsyncMock()
        asyncio.run(bot_module._monitor_loop(100, mid, dict(mon, id=mid), app))

        texts = [c.args[1] for c in app.bot.send_message.await_args_list]
        assert sum("Hozirda mavjud" in t for t in texts) == 1  # 1-tekshiruv: 3 joy, darhol ko'rsatiladi
        assert sum("Yangi joy topildi" in t for t in texts) == 1  # 3-tekshiruv: qayta aniqlandi


class TestMonitorReflectsLiveEdits:
    """Monitoring & Scheduler Reliability audit: foydalanuvchi /list orqali
    ishlab turgan kuzatuvni tahrirlasa (masalan max_price'ni oshirsa),
    o'sha kuzatuv keyingi tekshiruvda ESKI emas, YANGI filtr bilan
    ishlashi kerak — monitor qayta ishga tushirilishini talab qilmasdan."""

    def test_updated_max_price_applied_on_next_check_without_restart(
        self, bot_module, fresh_db, monkeypatch
    ):
        # Boshlang'ich narx chegarasi (50_000) — poyezd (100_000) mos kelmaydi.
        mon = dict(MON, max_price=50_000)
        mid = fresh_db.save_monitor(100, mon)
        expensive_train = _train("E1", 100_000)

        checks = {"n": 0}

        async def fake_search(client, *a, **kw):
            checks["n"] += 1
            if checks["n"] == 1:
                # Birinchi tekshiruvdan KEYIN, ikkinchisidan OLDIN foydalanuvchi
                # narx chegarasini oshiradi — xuddi /list orqali tahrirlagandek.
                fresh_db.update_monitor_field(100, mid, "max_price", 150_000)
            return bot_module.SearchResult(True, [expensive_train], "")

        async def fake_get_client():
            return object()

        stop_after_second = {"n": 0}

        async def fake_sleep(_):
            stop_after_second["n"] += 1
            if stop_after_second["n"] >= 2:
                fresh_db.deactivate(mid)

        monkeypatch.setattr(bot_module, "_shared_search", fake_search)
        monkeypatch.setattr(bot_module, "_get_railway_client", fake_get_client)
        monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)

        app = MagicMock()
        app.bot.send_message = AsyncMock()

        asyncio.run(bot_module._monitor_loop(100, mid, dict(mon, id=mid), app))

        texts = [c.args[1] for c in app.bot.send_message.await_args_list]
        # 1-tekshiruv: 100_000 so'mlik poyezd hali eski (50_000) chegaradan
        # tashqarida — "hozircha mos bilet yo'q" deb boshlang'ich xabar.
        assert any("mos bilet yo'q" in t for t in texts)
        # 2-tekshiruv: max_price DBda 150_000 ga yangilangan — endi mos keladi
        # va "Yangi joy topildi" deb yuboriladi (restart shart emas edi).
        assert any("Yangi joy topildi" in t for t in texts)
        assert any("E1" in t for t in texts)

    def test_updated_min_seats_applied_on_next_check(self, bot_module, fresh_db, monkeypatch):
        mon = dict(MON, min_seats=5)  # boshida juda ko'p joy talab qilinadi
        mid = fresh_db.save_monitor(100, mon)
        train_3_seats = _train("E1", 100_000)
        train_3_seats["cars"][0]["freeSeats"] = 3
        train_3_seats["cars"][0]["tariffs"][0]["freeSeats"] = 3

        checks = {"n": 0}

        async def fake_search(client, *a, **kw):
            checks["n"] += 1
            if checks["n"] == 1:
                fresh_db.update_monitor_field(100, mid, "min_seats", 2)
            return bot_module.SearchResult(True, [train_3_seats], "")

        async def fake_get_client():
            return object()

        counter = {"n": 0}

        async def fake_sleep(_):
            counter["n"] += 1
            if counter["n"] >= 2:
                fresh_db.deactivate(mid)

        monkeypatch.setattr(bot_module, "_shared_search", fake_search)
        monkeypatch.setattr(bot_module, "_get_railway_client", fake_get_client)
        monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)

        app = MagicMock()
        app.bot.send_message = AsyncMock()
        asyncio.run(bot_module._monitor_loop(100, mid, dict(mon, id=mid), app))

        texts = [c.args[1] for c in app.bot.send_message.await_args_list]
        assert any("mos bilet yo'q" in t for t in texts)  # 1-tekshiruv: 3<5, mos emas
        assert any("Yangi joy topildi" in t for t in texts)  # 2-tekshiruv: 3>=2, mos

    def test_updated_car_type_applied_on_next_check(self, bot_module, fresh_db, monkeypatch):
        mon = dict(MON, car_type="sv")  # boshida faqat SV qidiriladi
        mid = fresh_db.save_monitor(100, mon)
        platskar_train = {
            "number": "P1", "brand": "X",
            "departureDate": "2099-01-01 08:00", "arrivalDate": "2099-01-01 10:00",
            "cars": [{
                "type": "Plaskartli", "freeSeats": 2,
                "tariffs": [{"tariff": 80_000, "freeSeats": 2, "classServiceType": "Plaskartli"}],
            }],
        }

        checks = {"n": 0}

        async def fake_search(client, *a, **kw):
            checks["n"] += 1
            if checks["n"] == 1:
                fresh_db.update_monitor_field(100, mid, "car_type", "platskar")
            return bot_module.SearchResult(True, [platskar_train], "")

        async def fake_get_client():
            return object()

        counter = {"n": 0}

        async def fake_sleep(_):
            counter["n"] += 1
            if counter["n"] >= 2:
                fresh_db.deactivate(mid)

        monkeypatch.setattr(bot_module, "_shared_search", fake_search)
        monkeypatch.setattr(bot_module, "_get_railway_client", fake_get_client)
        monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)

        app = MagicMock()
        app.bot.send_message = AsyncMock()
        asyncio.run(bot_module._monitor_loop(100, mid, dict(mon, id=mid), app))

        texts = [c.args[1] for c in app.bot.send_message.await_args_list]
        assert any("mos bilet yo'q" in t for t in texts)  # 1-tekshiruv: SV so'ralgan, Plaskart kelmaydi
        assert any("Yangi joy topildi" in t for t in texts)  # 2-tekshiruv: platskar'ga o'zgartirilgan

    def test_updated_time_range_applied_on_next_check(self, bot_module, fresh_db, monkeypatch):
        mon = dict(MON, time_from="06:00", time_to="08:00")  # ertalabki tor oraliq
        mid = fresh_db.save_monitor(100, mon)
        evening_train = _train("EV1", 100_000)
        evening_train["departureDate"] = "2099-01-01 20:00"

        checks = {"n": 0}

        async def fake_search(client, *a, **kw):
            checks["n"] += 1
            if checks["n"] == 1:
                fresh_db.update_monitor_field(100, mid, "time_from", "18:00")
                fresh_db.update_monitor_field(100, mid, "time_to", "23:59")
            return bot_module.SearchResult(True, [evening_train], "")

        async def fake_get_client():
            return object()

        counter = {"n": 0}

        async def fake_sleep(_):
            counter["n"] += 1
            if counter["n"] >= 2:
                fresh_db.deactivate(mid)

        monkeypatch.setattr(bot_module, "_shared_search", fake_search)
        monkeypatch.setattr(bot_module, "_get_railway_client", fake_get_client)
        monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)

        app = MagicMock()
        app.bot.send_message = AsyncMock()
        asyncio.run(bot_module._monitor_loop(100, mid, dict(mon, id=mid), app))

        texts = [c.args[1] for c in app.bot.send_message.await_args_list]
        assert any("mos bilet yo'q" in t for t in texts)  # 06:00-08:00 da 20:00 reys mos emas
        assert any("Yangi joy topildi" in t for t in texts)  # kechqurungi oraliqqa o'zgartirilgach mos

    def test_updated_route_date_used_in_next_search_call(self, bot_module, fresh_db, monkeypatch):
        """Qidiruv so'rovining o'zi (marshrut/sana) ham tahrirlangan
        qiymatni ishlatishi kerak — eski (spawn vaqtidagi) emas."""
        mon = dict(MON, date="2099-01-01")
        mid = fresh_db.save_monitor(100, mon)
        seen_dates = []

        async def fake_search(client, from_code, to_code, date):
            seen_dates.append(date)
            if len(seen_dates) == 1:
                fresh_db.update_monitor_field(100, mid, "date", "2099-02-15")
            return bot_module.SearchResult(True, [], "")

        async def fake_get_client():
            return object()

        counter = {"n": 0}

        async def fake_sleep(_):
            counter["n"] += 1
            if counter["n"] >= 2:
                fresh_db.deactivate(mid)

        monkeypatch.setattr(bot_module, "_shared_search", fake_search)
        monkeypatch.setattr(bot_module, "_get_railway_client", fake_get_client)
        monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)

        app = MagicMock()
        app.bot.send_message = AsyncMock()
        asyncio.run(bot_module._monitor_loop(100, mid, dict(mon, id=mid), app))

        assert seen_dates == ["2099-01-01", "2099-02-15"]


class TestExpiryUsesFreshState:
    """P0: muddat tugash (expiry) qarori HAR DOIM DB'dan yangilangan holat
    bilan qabul qilinishi kerak — aks holda foydalanuvchi kuzatuvni
    (masalan ertangi sanaga) tahrirlasa-yu, ishlab turgan task hali eski
    (allaqachon o'tib ketgan) sana/vaqt bilan uni bekorga to'xtatib qo'yishi
    mumkin edi."""

    def test_edited_monitor_not_incorrectly_expired_using_stale_snapshot(
        self, bot_module, fresh_db, monkeypatch
    ):
        # ESKI (asyncio task spawn bo'lgandagi) holat: 2030-01-10, 12:00-15:00
        # — bu allaqachon "o'tgan" bo'lardi, agar frozen "now" 16:00 bo'lsa.
        stale_snapshot = dict(
            MON, date="2030-01-10", time_from="12:00", time_to="15:00",
        )
        mid = fresh_db.save_monitor(100, dict(stale_snapshot))

        # Foydalanuvchi /list orqali ERTANGI kunga va kengroq vaqtga
        # ko'chirdi — bu DB'dagi HAQIQIY joriy holat.
        fresh_db.update_monitor_field(100, mid, "date", "2030-01-11")
        fresh_db.update_monitor_field(100, mid, "time_from", "12:00")
        fresh_db.update_monitor_field(100, mid, "time_to", "18:00")

        monkeypatch.setattr(bot_module, "datetime", _fixed_now(bot_module, 2030, 1, 10, 16, 0))

        search_called = {"n": 0}

        async def fake_search(client, *a, **kw):
            search_called["n"] += 1
            return bot_module.SearchResult(True, [], "")

        async def fake_get_client():
            return object()

        counter = {"n": 0}

        async def fake_sleep(_):
            counter["n"] += 1
            if counter["n"] >= 1:
                fresh_db.deactivate(mid)

        monkeypatch.setattr(bot_module, "_shared_search", fake_search)
        monkeypatch.setattr(bot_module, "_get_railway_client", fake_get_client)
        monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)

        app = MagicMock()
        app.bot.send_message = AsyncMock()

        # `_monitor_loop`ga ESKI (stale) snapshot `data` sifatida uzatiladi —
        # xuddi asyncio task tahrirlashdan OLDIN spawn bo'lgandek. Tuzatishdan
        # KEYIN, birinchi iteratsiyaning o'zidayoq DB'dan yangilangan holat
        # o'qilishi va shu asosda (hali tugamagan!) qaror qabul qilinishi kerak.
        asyncio.run(
            bot_module._monitor_loop(100, mid, dict(stale_snapshot, id=mid), app)
        )

        texts = [c.args[1] for c in app.bot.send_message.await_args_list]
        assert not any("o'tib ketdi" in t for t in texts), (
            "Monitor eski (stale) holat asosida noto'g'ri 'muddati tugadi' deb "
            "to'xtatildi — tahrirlangan (yangi, hali tugamagan) sana e'tiborsiz qoldirildi"
        )
        assert search_called["n"] >= 1  # qidiruv HAQIQATDA amalga oshdi, bekorga to'xtatilmadi
