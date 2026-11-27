"""UX audit P2: buyruqlar menyusi, bosh menyu, kalendar cheklovi, oxirgi tekshiruv vaqti."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from tests.test_handlers import _make_context, _make_update


class TestCalendarPastGuard:
    def test_current_month_has_no_prev_arrow(self, bot_module):
        now = datetime.now()
        kb = bot_module._calendar_keyboard(now.year, now.month)
        first_row = [b.callback_data for b in kb.inline_keyboard[0]]
        assert not any(d.startswith("cal_prev") for d in first_row)
        assert any(d.startswith("cal_next") for d in first_row)

    def test_future_month_has_prev_arrow(self, bot_module):
        future = datetime.now() + timedelta(days=40)
        kb = bot_module._calendar_keyboard(future.year, future.month)
        assert any(b.callback_data.startswith("cal_prev") for b in kb.inline_keyboard[0])

    def test_edit_mode_calendar_has_no_nav_row(self, bot_module):
        now = datetime.now()
        with_nav = bot_module._calendar_keyboard(now.year, now.month, nav=True)
        without = bot_module._calendar_keyboard(now.year, now.month, nav=False)
        assert len(with_nav.inline_keyboard) == len(without.inline_keyboard) + 1


class TestAgo:
    def test_formats(self, bot_module):
        now = datetime.now()
        assert bot_module._ago((now - timedelta(seconds=5)).isoformat()) == "hozirgina"
        assert bot_module._ago((now - timedelta(minutes=3, seconds=5)).isoformat()) == "3 daq oldin"
        assert bot_module._ago((now - timedelta(hours=2, minutes=1)).isoformat()) == "2 soat oldin"
        assert bot_module._ago(None) == ""
        assert bot_module._ago("bad") == ""

    def test_summary_shows_last_check(self, bot_module):
        m = {"id": "x", "from_name": "A", "to_name": "B", "date": "2099-01-01",
             "car_type": "any", "check_count": 5,
             "last_check": (datetime.now() - timedelta(minutes=4, seconds=2)).isoformat()}
        assert "4 daq oldin" in bot_module._monitor_summary(m)


class TestCommandsMenu:
    def test_admin_menu_is_superset_of_user_menu(self, bot_module):
        user = {c.command for c in bot_module.USER_COMMANDS}
        admin = {c.command for c in bot_module.ADMIN_COMMANDS}
        assert user < admin and {"users", "adduser", "logs"} <= admin

    def test_set_commands_sets_default_and_per_admin(self, bot_module, monkeypatch):
        monkeypatch.setattr(bot_module.Config, "ADMIN_IDS", [7, 8])
        app = MagicMock()
        app.bot.set_my_commands = AsyncMock()
        asyncio.run(bot_module._set_commands(app))
        assert app.bot.set_my_commands.await_count == 3  # 1 umumiy + 2 admin

    def test_set_commands_failure_does_not_crash(self, bot_module):
        app = MagicMock()
        app.bot.set_my_commands = AsyncMock(side_effect=RuntimeError("net"))
        asyncio.run(bot_module._set_commands(app))


class TestStartMenu:
    def test_start_shows_quick_action_buttons(self, bot_module, tmp_path, monkeypatch):
        from database import Database
        monkeypatch.setattr(bot_module, "db", Database(path=str(tmp_path / "d.json")))
        monkeypatch.setattr(bot_module.Config, "ADMIN_IDS", [999])
        update, context = _make_update(uid=999), _make_context()
        asyncio.run(bot_module.cmd_start(update, context))
        markup = update.message.reply_text.await_args_list[0].kwargs["reply_markup"]
        datas = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert "menu|monitor" in datas and "menu|list" in datas


class TestCommandNamesValidForTelegram:
    def test_all_menu_commands_match_telegram_rules(self, bot_module):
        import re
        for c in bot_module.ADMIN_COMMANDS:
            assert re.fullmatch(r"[a-z0-9_]{1,32}", c.command), c.command


class TestSiteRulesFromDocs:
    def test_seat_limit_is_four_per_order(self, bot_module):
        assert bot_module.MAX_SEATS_LIMIT == 4

    def test_tech_break_window(self, bot_module):
        from datetime import datetime as dt
        w = bot_module._tech_break_wait
        assert w(dt(2030, 1, 1, 2, 29, 59)) == 0
        assert w(dt(2030, 1, 1, 2, 30, 0)) == 1800
        assert w(dt(2030, 1, 1, 2, 45, 0)) == 900
        assert w(dt(2030, 1, 1, 3, 0, 0)) == 0

    def test_sales_note_only_beyond_window(self, bot_module):
        near = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        far = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")
        assert bot_module._sales_note(near) == ""
        assert "sotuv hali ochilmagan" in bot_module._sales_note(far)
        assert bot_module._sales_note("bad") == ""

    def test_confirm_step_warns_for_far_date(self, bot_module):
        far = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")
        text, _ = bot_module._step_view("confirm", {"date": far})
        assert "sotuv hali ochilmagan" in text

    def test_monitor_skips_search_during_tech_break(self, bot_module, tmp_path, monkeypatch):
        from database import Database
        from tests.test_monitor_loop import MON, _fixed_now
        db = Database(path=str(tmp_path / "d.json"))
        monkeypatch.setattr(bot_module, "db", db)
        monkeypatch.setattr(bot_module, "datetime", _fixed_now(bot_module, 2030, 1, 10, 2, 40))
        calls = {"n": 0}

        async def fake_search(*a, **kw):
            calls["n"] += 1
            return bot_module.SearchResult(False, [], "http_504")

        async def fake_client():
            return object()

        mon = dict(MON, date="2030-01-10")
        mid = db.save_monitor(100, mon)
        slept = []

        async def fake_sleep(sec):
            slept.append(sec)
            db.deactivate(mid)

        monkeypatch.setattr(bot_module, "_shared_search", fake_search)
        monkeypatch.setattr(bot_module, "_get_railway_client", fake_client)
        monkeypatch.setattr(bot_module.asyncio, "sleep", fake_sleep)
        app = MagicMock(); app.bot.send_message = AsyncMock()
        asyncio.run(bot_module._monitor_loop(100, mid, dict(mon, id=mid), app))
        assert calls["n"] == 0                # tanaffusda so'rov yo'q
        assert slept == [60]                   # 20 daqiqa qolgan — 60s dan oshmaydi
        app.bot.send_message.assert_not_awaited()
