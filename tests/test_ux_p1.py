"""UX audit P1: Orqaga/Bekor, narx/joy tugmalari, tasdiqlash bosqichi,
yagona sana formati, tahrirlash tenglashtirilishi, o'chirish tasdig'i,
topilgandan keyingi tugmalar."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_handlers import _make_callback_update, _make_context, _make_update  # noqa: F401


@pytest.fixture
def fresh_db(bot_module, tmp_path, monkeypatch):
    from database import Database
    new_db = Database(path=str(tmp_path / "data.json"))
    monkeypatch.setattr(bot_module, "db", new_db)
    bot_module.Config.ADMIN_IDS = [999]
    return new_db


MON = {
    "from_name": "🏙 Toshkent", "from_code": "1", "to_name": "🕌 Buxoro", "to_code": "2",
    "date": "2099-01-05", "car_type": "any", "time_from": "00:00", "time_to": "23:59",
    "time_label": "🕐 Istalgan vaqt", "active": True, "max_price": None, "min_seats": 1,
    "created_at": "2026-11-26T10:00:00", "check_count": 0, "last_check": None,
}


class TestHelpers:
    def test_date_has_weekday_and_dotted_format(self, bot_module):
        assert bot_module._fmt_date_wd("2026-11-30") == "30.11.2026 (Du)"
        assert bot_module._fmt_date_wd("bad") == "bad"

    @pytest.mark.parametrize("text", ["150000", "150 000", "150,000", "150.000"])
    def test_price_formats_accepted(self, bot_module, text):
        assert bot_module._parse_price(text) == (150000, None)

    def test_price_rejects_text_and_too_small(self, bot_module):
        assert bot_module._parse_price("abc")[0] is None
        assert bot_module._parse_price("500")[0] is None

    def test_time_range_parse(self, bot_module):
        assert bot_module._parse_time_range("18:00-20:00") == ("18:00", "20:00")
        assert bot_module._parse_time_range("25:00-20:00") is None
        assert bot_module._parse_time_range("x") is None


class TestFlowNavigation:
    def test_every_step_after_first_has_back_and_cancel(self, bot_module):
        for step in bot_module.STEPS[1:-1]:  # confirm'da alohida tugmalar
            _, markup = bot_module._step_view(step, dict(MON))
            datas = [b.callback_data for row in markup.inline_keyboard for b in row]
            assert "nav|back" in datas and "nav|cancel" in datas, step

    def test_first_step_has_cancel_only(self, bot_module):
        _, markup = bot_module._step_view("from", {})
        datas = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert "nav|cancel" in datas and "nav|back" not in datas

    def test_back_goes_to_previous_step(self, bot_module, fresh_db):
        update, q = _make_callback_update(uid=999, data="nav|back")
        context = _make_context()
        context.user_data = dict(MON, _step="price")
        state = asyncio.run(bot_module.nav_back(update, context))
        assert state == bot_module.WAIT_TIME_RANGE
        assert context.user_data["_step"] == "time"

    def test_cancel_clears_state_and_ends(self, bot_module, fresh_db):
        update, q = _make_callback_update(uid=999, data="nav|cancel")
        context = _make_context()
        context.user_data = dict(MON, _step="car")
        state = asyncio.run(bot_module.nav_cancel(update, context))
        assert state == bot_module.ConversationHandler.END
        assert context.user_data == {}

    def test_price_buttons_set_value_and_go_to_seats(self, bot_module, fresh_db):
        update, q = _make_callback_update(uid=999, data="price|300000")
        context = _make_context()
        context.user_data = dict(MON, _step="price")
        assert asyncio.run(bot_module.got_price_button(update, context)) == bot_module.WAIT_MIN_SEATS
        assert context.user_data["max_price"] == 300000

    def test_price_unlimited_button(self, bot_module, fresh_db):
        update, q = _make_callback_update(uid=999, data="price|any")
        context = _make_context()
        context.user_data = dict(MON, _step="price")
        asyncio.run(bot_module.got_price_button(update, context))
        assert context.user_data["max_price"] is None

    def test_seats_button_goes_to_confirm(self, bot_module, fresh_db):
        update, q = _make_callback_update(uid=999, data="seats|3")
        context = _make_context()
        context.user_data = dict(MON, _step="seats")
        assert asyncio.run(bot_module.got_seats_button(update, context)) == bot_module.WAIT_CONFIRM
        assert context.user_data["min_seats"] == 3

    def test_confirm_restart_returns_to_first_step(self, bot_module, fresh_db):
        update, q = _make_callback_update(uid=999, data="go|restart")
        context = _make_context()
        context.user_data = dict(MON, _step="confirm")
        assert asyncio.run(bot_module.go_confirm(update, context)) == bot_module.WAIT_FROM


class TestDeleteAndStopConfirmation:
    def test_delete_asks_before_removing(self, bot_module, fresh_db):
        mid = fresh_db.save_monitor(999, dict(MON))
        update, q = _make_callback_update(uid=999, data=f"mgr_del|{mid}")
        asyncio.run(bot_module.mgr_del(update, _make_context()))
        assert fresh_db.is_active(mid) is True
        assert "komilmi" in q.edit_message_text.await_args.args[0]

    def test_delete_confirmed_removes(self, bot_module, fresh_db):
        mid = fresh_db.save_monitor(999, dict(MON))
        update, q = _make_callback_update(uid=999, data=f"mgr_delok|{mid}")
        asyncio.run(bot_module.mgr_del_confirmed(update, _make_context()))
        assert fresh_db.is_active(mid) is False

    def test_stop_all_asks_first(self, bot_module, fresh_db):
        mid = fresh_db.save_monitor(999, dict(MON))
        update, context = _make_update(uid=999), _make_context()
        context.args = []
        asyncio.run(bot_module.cmd_stop(update, context))
        assert fresh_db.is_active(mid) is True
        assert "komilmi" in update.message.reply_text.await_args.args[0]

    def test_stop_all_yes_stops_and_no_keeps(self, bot_module, fresh_db):
        mid = fresh_db.save_monitor(999, dict(MON))
        upd, q = _make_callback_update(uid=999, data="stop_all|no")
        asyncio.run(bot_module.stop_all_cb(upd, _make_context()))
        assert fresh_db.is_active(mid) is True
        upd, q = _make_callback_update(uid=999, data="stop_all|yes")
        asyncio.run(bot_module.stop_all_cb(upd, _make_context()))
        assert fresh_db.is_active(mid) is False


class TestEditParity:
    def test_edit_price_accepts_dotted_and_returns_card(self, bot_module, fresh_db):
        mid = fresh_db.save_monitor(999, dict(MON))
        update, context = _make_update(uid=999), _make_context()
        update.message.text = "150.000"
        context.user_data = {"edit_field": "price", "edit_mid": mid}
        asyncio.run(bot_module.mgr_edit_text(update, context))
        assert fresh_db.get_active_monitors(999)[0]["max_price"] == 150000
        sent = update.message.reply_text.await_args
        assert "Kuzatuv ma'lumotlari" in sent.args[0]
        assert sent.kwargs["reply_markup"] is not None  # kartaga qaytish tugmalari

    def test_edit_price_rejects_below_minimum(self, bot_module, fresh_db):
        mid = fresh_db.save_monitor(999, dict(MON))
        update, context = _make_update(uid=999), _make_context()
        update.message.text = "500"
        context.user_data = {"edit_field": "price", "edit_mid": mid}
        asyncio.run(bot_module.mgr_edit_text(update, context))
        assert fresh_db.get_active_monitors(999)[0]["max_price"] is None

    def test_edit_custom_time_via_text(self, bot_module, fresh_db):
        mid = fresh_db.save_monitor(999, dict(MON))
        upd, q = _make_callback_update(uid=999, data="mgr_tv|custom")
        context = _make_context()
        context.user_data = {"edit_mid": mid}
        asyncio.run(bot_module.mgr_time_value(upd, context))
        assert context.user_data["edit_field"] == "time"
        update = _make_update(uid=999)
        update.message.text = "18:00-20:00"
        asyncio.run(bot_module.mgr_edit_text(update, context))
        m = fresh_db.get_active_monitors(999)[0]
        assert (m["time_from"], m["time_to"]) == ("18:00", "20:00")

    def test_summary_uses_dotted_date_with_weekday(self, bot_module):
        assert "05.01.2099" in bot_module._monitor_summary(dict(MON, id="x"))


class TestRepeatAfterFound:
    def test_repeat_clones_stopped_monitor_and_spawns(self, bot_module, fresh_db, monkeypatch):
        mid = fresh_db.save_monitor(999, dict(MON))
        fresh_db.deactivate(mid)
        spawned = []
        monkeypatch.setattr(bot_module, "_spawn_monitor", lambda *a, **kw: spawned.append(a))
        upd, q = _make_callback_update(uid=999, data=f"rep|{mid}")
        q.message = MagicMock()
        q.message.reply_text = AsyncMock()
        asyncio.run(bot_module.repeat_cb(upd, _make_context()))
        active = fresh_db.get_active_monitors(999)
        assert len(active) == 1 and active[0]["id"] != mid
        assert active[0]["check_count"] == 0
        assert len(spawned) == 1

    def test_repeat_respects_monitor_limit(self, bot_module, fresh_db, monkeypatch):
        monkeypatch.setattr(bot_module.Config, "MAX_MONITORS_PER_USER", 1)
        mid = fresh_db.save_monitor(999, dict(MON))  # allaqachon faol
        monkeypatch.setattr(bot_module, "_spawn_monitor", lambda *a, **kw: pytest.fail("spawn bo'lmasligi kerak"))
        upd, q = _make_callback_update(uid=999, data=f"rep|{mid}")
        asyncio.run(bot_module.repeat_cb(upd, _make_context()))
        assert len(fresh_db.get_active_monitors(999)) == 1


class TestSiteErrorHandling:
    def test_error_sleep_stays_near_interval(self, bot_module):
        f = bot_module._error_sleep
        assert f(5, 1) == 5 and f(5, 5) == 5
        assert f(5, 6) == 10 and f(5, 12) == 15
        assert f(5, 100) == 15  # 3x dan oshmaydi

    def test_first_failed_check_edits_confirmation(self, bot_module):
        bot_module._confirm_messages["e1"] = (1, 2, f"x\n{bot_module.CHECKING_LINE}")
        app = MagicMock(); app.bot.edit_message_text = AsyncMock()
        asyncio.run(bot_module._mark_confirm_site_down(app, "e1"))
        asyncio.run(bot_module._mark_confirm_site_down(app, "e1"))  # ikkinchi marta — qayta tahrirlamaydi
        assert app.bot.edit_message_text.await_count == 1
        assert "javob bermayapti" in app.bot.edit_message_text.await_args.args[0]
        assert "e1" in bot_module._confirm_messages  # tiklanganda yakunlash uchun saqlanadi

    def test_recovery_replaces_site_down_line(self, bot_module):
        bot_module._confirm_messages["e2"] = (1, 2, f"x\n{bot_module.SITE_DOWN_LINE}")
        app = MagicMock(); app.bot.edit_message_text = AsyncMock()
        asyncio.run(bot_module._finish_confirm_message(app, "e2", False))
        text = app.bot.edit_message_text.await_args.args[0]
        assert "javob bermayapti" not in text and "hozircha mos bilet yo'q" in text
