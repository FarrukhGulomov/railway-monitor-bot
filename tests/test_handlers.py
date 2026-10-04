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
    update.effective_message = update.message  # @restricted shu orqali javob beradi
    update.callback_query = None  # matn xabari — MagicMock avtomatik "truthy" bermasin
    return update


def _make_context():
    context = MagicMock()
    context.application.bot.send_message = AsyncMock()
    context.user_data = {}
    return context


def _make_callback_update(uid=999, data="mgr_show|abc123", first_name="Ali"):
    """/list va /users boshqaruv tugmalari kabi CallbackQueryHandler
    handlerlarini sinash uchun soxta callback_query bilan Update."""
    user = MagicMock()
    user.id = uid
    user.first_name = first_name
    user.last_name = None
    user.username = None
    user.language_code = "uz"

    q = MagicMock()
    q.from_user = user
    q.data = data
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()

    update = MagicMock()
    update.effective_user = user
    update.callback_query = q
    update.message = None
    return update, q


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
        fresh_db.add_user(999, added_by=111)
        update, context = _make_update(uid=999), _make_context()
        update.message.contact = MagicMock(user_id=12345, phone_number="+998900000000")

        asyncio.run(bot_module.got_contact(update, context))

        assert context.application.bot.send_message.await_args_list == []
        reply = update.message.reply_text.await_args_list[0].args[0]
        assert "o'z raqamingizni" in reply


class TestPriceSkip:
    def test_skip_clears_price_only_in_edit_mode(self, bot_module, fresh_db):
        bot_module.Config.ADMIN_IDS = [999]  # fail-closed default — test uid o'ziga ruxsat kerak
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
        bot_module.Config.ADMIN_IDS = [999]
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
        bot_module.Config.ADMIN_IDS = [999]
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
        bot_module.Config.ADMIN_IDS = [999]
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
        bot_module.Config.ADMIN_IDS = [999]
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
        bot_module.Config.ADMIN_IDS = [999]
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
        bot_module.Config.ADMIN_IDS = [999]
        bot_module.Config.ALLOWED_USERS = []
        monkeypatch.setattr(bot_module, "_spawn_monitor", lambda *a, **kw: None)
        update, context = _make_update(uid=999), _make_context()
        update.message.text = "2"
        context.user_data = dict(self._base_user_data(), max_price=None)

        asyncio.run(bot_module.got_min_seats(update, context))

        monitors = fresh_db.get_active_monitors(999)
        assert monitors[0]["min_seats"] == 2

    def test_invalid_seats_input_rejected(self, bot_module, fresh_db):
        bot_module.Config.ADMIN_IDS = [999]
        bot_module.Config.ALLOWED_USERS = []
        update, context = _make_update(uid=999), _make_context()
        update.message.text = "0"
        context.user_data = dict(self._base_user_data(), max_price=None)

        state = asyncio.run(bot_module.got_min_seats(update, context))

        assert state == bot_module.WAIT_MIN_SEATS
        assert fresh_db.get_active_monitors(999) == []


class TestAccessRevocationBlocksCallbacks:
    """Security audit: /list va /users boshqaruv tugmalari (mgr_*) avval
    @restricted BILAN himoyalanmagan edi — botdan o'chirilgan foydalanuvchi
    eski xabaridagi tugmalar orqali hali ham o'z kuzatuvlarini boshqara
    olardi (authorization bypass / callback ownership gap). Endi har bir
    mgr_* va got_contact handler kirish tekshiruvidan o'tishi SHART."""

    def _revoked_setup(self, bot_module, fresh_db):
        bot_module.Config.ADMIN_IDS = [111]
        bot_module.Config.ALLOWED_USERS = []
        # 999 avval qo'shilgan, keyin o'chirilgan — "ruxsati bekor qilingan" holat
        fresh_db.add_user(999, added_by=111)
        fresh_db.remove_user(999)
        assert bot_module.has_access(999) is False

    @pytest.mark.parametrize("handler_name,cb_data", [
        ("mgr_show", "mgr_show|abc"),
        ("mgr_del", "mgr_del|abc"),
        ("mgr_edit", "mgr_edit|abc"),
        ("mgr_back", "mgr_back"),
    ])
    def test_revoked_user_blocked_from_monitor_callbacks(
        self, bot_module, fresh_db, handler_name, cb_data
    ):
        self._revoked_setup(bot_module, fresh_db)
        # Ruxsat bekor qilingan foydalanuvchining ESKI (hali ham DBda mavjud)
        # kuzatuvi bo'yicha tugma bosishi — endi rad etilishi kerak.
        mid = fresh_db.save_monitor(999, {
            "from_name": "A", "to_name": "B", "date": "2099-01-01",
            "car_type": "any", "active": True,
        })
        cb_data = cb_data.replace("abc", mid) if "abc" in cb_data else cb_data
        update, q = _make_callback_update(uid=999, data=cb_data)

        handler = getattr(bot_module, handler_name)
        asyncio.run(handler(update, _make_context()))

        q.answer.assert_awaited_once()
        assert q.answer.await_args.kwargs.get("show_alert") is True
        # Handlerning o'z ichki mantig'i (masalan monitor ma'lumotini ko'rsatish)
        # ISHGA TUSHMAGAN — faqat rad javobi berilgan
        q.edit_message_text.assert_not_awaited()
        # Kuzatuv hali ham o'zgarishsiz qolgan (mgr_del bo'lsa ham o'chirilmagan)
        assert fresh_db.is_active(mid) is True

    def test_revoked_user_blocked_from_contact_sharing(self, bot_module, fresh_db):
        self._revoked_setup(bot_module, fresh_db)
        update, context = _make_update(uid=999), _make_context()
        update.message.contact = MagicMock(user_id=999, phone_number="+998900000000")

        asyncio.run(bot_module.got_contact(update, context))

        update.message.reply_text.assert_awaited_once_with("⛔ Sizga ruxsat yo'q.")
        assert fresh_db.get_user(999).get("phone") is None

    def test_authorized_user_still_works_on_same_callbacks(self, bot_module, fresh_db):
        # Salomatlik tekshiruvi: ruxsat bor foydalanuvchi uchun hech narsa
        # buzilmagan — regressiya emasligini tasdiqlaydi.
        bot_module.Config.ADMIN_IDS = [111]
        bot_module.Config.ALLOWED_USERS = []
        fresh_db.add_user(999, added_by=111)
        mid = fresh_db.save_monitor(999, {
            "from_name": "A", "to_name": "B", "date": "2099-01-01",
            "car_type": "any", "active": True,
        })
        update, q = _make_callback_update(uid=999, data=f"mgr_show|{mid}")

        asyncio.run(bot_module.mgr_show(update, _make_context()))

        q.edit_message_text.assert_awaited_once()


class TestAdminMutationWriteFailures:
    """Admin foydalanuvchi/kuzatuv boshqaruvida DB yozuvi muvaffaqiyatsiz
    bo'lsa, foydalanuvchiga/adminga soxta 'muvaffaqiyat' emas, aniq
    vaqtinchalik xatolik ko'rsatilishi kerak."""

    def test_add_user_write_failure_shows_temp_error_not_false_success(
        self, bot_module, fresh_db, monkeypatch
    ):
        bot_module.Config.ADMIN_IDS = [111]
        monkeypatch.setattr(fresh_db, "_write", lambda data: False)
        update, context = _make_update(uid=111), _make_context()
        context.args = ["555"]

        asyncio.run(bot_module.cmd_add_user(update, context))

        text = update.message.reply_text.await_args_list[0].args[0]
        assert "✅" not in text
        assert "xatolik" in text
        monkeypatch.undo()
        assert fresh_db.is_added_user(555) is False

    def test_remove_user_write_failure_shows_temp_error_not_false_success(
        self, bot_module, fresh_db, monkeypatch
    ):
        bot_module.Config.ADMIN_IDS = [111]
        fresh_db.add_user(555, added_by=111)
        monkeypatch.setattr(fresh_db, "_write", lambda data: False)
        update, context = _make_update(uid=111), _make_context()
        context.args = ["555"]

        asyncio.run(bot_module.cmd_remove_user(update, context))

        text = update.message.reply_text.await_args_list[0].args[0]
        assert "o'chirildi" not in text
        assert "xatolik" in text
        monkeypatch.undo()
        # Yozuv muvaffaqiyatsiz bo'lgani uchun ruxsat HALI HAM faol qolishi kerak
        assert fresh_db.is_added_user(555) is True

    def test_mgr_del_write_failure_shows_temp_error_not_false_success(
        self, bot_module, fresh_db, monkeypatch
    ):
        bot_module.Config.ADMIN_IDS = [111]
        fresh_db.add_user(999, added_by=111)
        mid = fresh_db.save_monitor(999, {
            "from_name": "A", "to_name": "B", "date": "2099-01-01",
            "car_type": "any", "active": True,
        })
        monkeypatch.setattr(fresh_db, "_write", lambda data: False)
        update, q = _make_callback_update(uid=999, data=f"mgr_del|{mid}")

        asyncio.run(bot_module.mgr_del(update, _make_context()))

        text = q.edit_message_text.await_args.args[0]
        assert "o'chirildi" not in text
        assert "xatolik" in text
        monkeypatch.undo()
        assert fresh_db.is_active(mid) is True  # soxta "o'chirildi" emas
