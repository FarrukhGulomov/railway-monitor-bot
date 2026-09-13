"""Handler darajasidagi testlar (Telegram mock obyektlari bilan)"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def fresh_db(bot_module, tmp_path, monkeypatch):
    from database import Database
    new_db = Database(path=str(tmp_path / "data.json"))
    monkeypatch.setattr(bot_module, "db", new_db)
    return new_db


def _make_update(uid=999, first_name="Ali", last_name="Vali", username="alivali"):
    user = MagicMock()
    user.id = uid
    user.first_name = first_name
    user.last_name = last_name
    user.username = username
    user.language_code = "uz"
    update = MagicMock()
    update.effective_user = user
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


def _make_context():
    context = MagicMock()
    context.application.bot.send_message = AsyncMock()
    context.user_data = {}
    return context


class TestCmdStart:
    def test_unauthorized_user_notifies_admin_and_denied(self, bot_module, fresh_db):
        bot_module.Config.ADMIN_IDS = [111]
        bot_module.Config.ALLOWED_USERS = []
        bot_module._last_start_notify.clear()
        update, context = _make_update(uid=999), _make_context()

        asyncio.run(bot_module.cmd_start(update, context))

        sends = context.application.bot.send_message.await_args_list
        assert len(sends) == 1
        assert sends[0].args[0] == 111
        assert "ruxsatsiz" in sends[0].args[1]
        update.message.reply_text.assert_awaited_once_with("⛔ Sizga ruxsat yo'q.")

    def test_markdown_unsafe_name_escaped(self, bot_module, fresh_db):
        bot_module.Config.ADMIN_IDS = [111]
        bot_module._last_start_notify.clear()
        update = _make_update(uid=999, first_name="Ali_*[test", username="ali_vali")
        context = _make_context()

        asyncio.run(bot_module.cmd_start(update, context))

        text = context.application.bot.send_message.await_args_list[0].args[1]
        assert r"Ali\_\*\[test" in text
        assert r"@ali\_vali" in text

    def test_start_notify_throttled(self, bot_module, fresh_db):
        bot_module.Config.ADMIN_IDS = [111]
        bot_module._last_start_notify.clear()
        update, context = _make_update(uid=999), _make_context()

        asyncio.run(bot_module.cmd_start(update, context))
        asyncio.run(bot_module.cmd_start(update, context))

        # ikkinchi /start oralig'i ichida — adminga faqat 1 marta yuboriladi
        assert len(context.application.bot.send_message.await_args_list) == 1

    def test_admin_start_no_self_notify(self, bot_module, fresh_db):
        bot_module.Config.ADMIN_IDS = [111]
        bot_module._last_start_notify.clear()
        update, context = _make_update(uid=111, first_name="Admin"), _make_context()

        asyncio.run(bot_module.cmd_start(update, context))

        assert context.application.bot.send_message.await_args_list == []
        # admin salomlashuv xabarini oladi, kontakt so'ralmaydi
        assert len(update.message.reply_text.await_args_list) == 1

    def test_authorized_user_asked_for_phone_once(self, bot_module, fresh_db):
        bot_module.Config.ADMIN_IDS = [111]
        bot_module._last_start_notify.clear()
        fresh_db.add_user(999, added_by=111)
        update, context = _make_update(uid=999), _make_context()

        asyncio.run(bot_module.cmd_start(update, context))
        # salomlashuv + kontakt so'rovi
        assert len(update.message.reply_text.await_args_list) == 2

        # telefon saqlangach — endi kontakt so'ralmaydi
        fresh_db.set_user_phone(999, "+998901234567")
        bot_module._last_start_notify.clear()
        update2, context2 = _make_update(uid=999), _make_context()
        asyncio.run(bot_module.cmd_start(update2, context2))
        assert len(update2.message.reply_text.await_args_list) == 1


class TestGotContact:
    def test_own_contact_saved_and_forwarded(self, bot_module, fresh_db):
        bot_module.Config.ADMIN_IDS = [111]
        fresh_db.add_user(999, added_by=111)
        update, context = _make_update(uid=999), _make_context()
        update.message.contact = MagicMock(user_id=999, phone_number="+998901234567")

        asyncio.run(bot_module.got_contact(update, context))

        assert fresh_db.get_user(999)["phone"] == "+998901234567"
        sends = context.application.bot.send_message.await_args_list
        assert len(sends) == 1 and "+998901234567" in sends[0].args[1]
        update.message.reply_text.assert_awaited_once()

    def test_foreign_contact_rejected(self, bot_module, fresh_db):
        bot_module.Config.ADMIN_IDS = [111]
        update, context = _make_update(uid=999), _make_context()
        update.message.contact = MagicMock(user_id=12345, phone_number="+998900000000")

        asyncio.run(bot_module.got_contact(update, context))

        assert context.application.bot.send_message.await_args_list == []
        reply = update.message.reply_text.await_args_list[0].args[0]
        assert "o'z raqamingizni" in reply


class TestPriceSkip:
    def test_skip_clears_price_only_in_edit_mode(self, bot_module, fresh_db):
        mid = fresh_db.save_monitor(999, {
            "from_name": "A", "to_name": "B", "date": "2030-01-01",
            "car_type": "any", "active": True, "max_price": 50000,
        })
        update, context = _make_update(uid=999), _make_context()

        # edit rejimida emas — hech narsa qilmaydi
        asyncio.run(bot_module.mgr_edit_skip(update, context))
        assert fresh_db.get_active_monitors(999)[0]["max_price"] == 50000

        # edit rejimida — chekni olib tashlaydi
        context.user_data = {"edit_field": "price", "edit_mid": mid}
        asyncio.run(bot_module.mgr_edit_skip(update, context))
        assert fresh_db.get_active_monitors(999)[0]["max_price"] is None
        assert "edit_field" not in context.user_data


class TestSeatsEdit:
    def test_skip_resets_min_seats_to_one(self, bot_module, fresh_db):
        mid = fresh_db.save_monitor(999, {
            "from_name": "A", "to_name": "B", "date": "2030-01-01",
            "car_type": "any", "active": True, "min_seats": 3,
        })
        update, context = _make_update(uid=999), _make_context()
        context.user_data = {"edit_field": "seats", "edit_mid": mid}

        asyncio.run(bot_module.mgr_edit_skip(update, context))

        assert fresh_db.get_active_monitors(999)[0]["min_seats"] == 1
        assert "edit_field" not in context.user_data

    def test_text_updates_min_seats(self, bot_module, fresh_db):
        mid = fresh_db.save_monitor(999, {
            "from_name": "A", "to_name": "B", "date": "2030-01-01",
            "car_type": "any", "active": True, "min_seats": 1,
        })
        update, context = _make_update(uid=999), _make_context()
        update.message.text = "2"
        context.user_data = {"edit_field": "seats", "edit_mid": mid}

        asyncio.run(bot_module.mgr_edit_text(update, context))

        assert fresh_db.get_active_monitors(999)[0]["min_seats"] == 2
        assert "edit_field" not in context.user_data

    def test_text_rejects_non_positive_input(self, bot_module, fresh_db):
        mid = fresh_db.save_monitor(999, {
            "from_name": "A", "to_name": "B", "date": "2030-01-01",
            "car_type": "any", "active": True, "min_seats": 1,
        })
        update, context = _make_update(uid=999), _make_context()
        update.message.text = "0"
        context.user_data = {"edit_field": "seats", "edit_mid": mid}

        asyncio.run(bot_module.mgr_edit_text(update, context))

        assert fresh_db.get_active_monitors(999)[0]["min_seats"] == 1
        assert context.user_data.get("edit_field") == "seats"  # hali tugallanmagan


class TestMonitorFlowSeats:
    """/monitor oqimida narxdan keyin 'nechta joy kerak' bosqichi (audit: bitta
    joy chiqishi bilanoq monitor to'xtab, 2+ joy izlagan foydalanuvchi
    talabini qondirmasligi muammosi)."""

    def _base_user_data(self):
        return {
            "from_name": "🏙 Toshkent", "from_code": "1",
            "to_name": "🕌 Buxoro", "to_code": "2",
            "date": "2099-01-01", "car_type": "any",
            "time_from": "00:00", "time_to": "23:59", "time_label": "🕐 Istalgan vaqt",
        }

    def test_price_step_moves_to_seats_step_not_confirm(self, bot_module, fresh_db):
        bot_module.Config.ADMIN_IDS = []
        bot_module.Config.ALLOWED_USERS = []
        update, context = _make_update(uid=999), _make_context()
        update.message.text = "/skip"
        context.user_data = self._base_user_data()

        state = asyncio.run(bot_module.got_max_price(update, context))

        assert state == bot_module.WAIT_MIN_SEATS
        assert fresh_db.get_active_monitors(999) == []  # hali saqlanmagan
        prompt = update.message.reply_text.await_args_list[0].args[0]
        assert "joy" in prompt.lower()

    def test_skip_seats_defaults_to_one_and_starts_monitor(self, bot_module, fresh_db, monkeypatch):
        bot_module.Config.ADMIN_IDS = []
        bot_module.Config.ALLOWED_USERS = []
        monkeypatch.setattr(bot_module, "_spawn_monitor", lambda *a, **kw: None)
        update, context = _make_update(uid=999), _make_context()
        update.message.text = "/skip"
        context.user_data = dict(self._base_user_data(), max_price=None)

        state = asyncio.run(bot_module.got_min_seats(update, context))

        assert state == bot_module.ConversationHandler.END
        monitors = fresh_db.get_active_monitors(999)
        assert len(monitors) == 1
        assert monitors[0]["min_seats"] == 1

    def test_explicit_seats_saved_on_monitor(self, bot_module, fresh_db, monkeypatch):
        bot_module.Config.ADMIN_IDS = []
        bot_module.Config.ALLOWED_USERS = []
        monkeypatch.setattr(bot_module, "_spawn_monitor", lambda *a, **kw: None)
        update, context = _make_update(uid=999), _make_context()
        update.message.text = "2"
        context.user_data = dict(self._base_user_data(), max_price=None)

        asyncio.run(bot_module.got_min_seats(update, context))

        monitors = fresh_db.get_active_monitors(999)
        assert monitors[0]["min_seats"] == 2

    def test_invalid_seats_input_rejected(self, bot_module, fresh_db):
        bot_module.Config.ADMIN_IDS = []
        bot_module.Config.ALLOWED_USERS = []
        update, context = _make_update(uid=999), _make_context()
        update.message.text = "0"
        context.user_data = dict(self._base_user_data(), max_price=None)

        state = asyncio.run(bot_module.got_min_seats(update, context))

        assert state == bot_module.WAIT_MIN_SEATS
        assert fresh_db.get_active_monitors(999) == []
