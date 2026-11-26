"""UX audit P0: tasdiq xabarini yakunlash, uzun xabarni bo'laklash,
kalendar strelkalari (tahrirlash rejimida), ruxsatsiz foydalanuvchiga ID."""

import asyncio
from unittest.mock import AsyncMock, MagicMock


class TestChunkMessage:
    def test_short_message_is_single_chunk(self, bot_module):
        assert bot_module._chunk_message(["a", "b", "c"]) == ["a\nb\nc"]

    def test_long_message_split_below_telegram_limit_without_cutting_lines(self, bot_module):
        lines = [f"🚂 poyezd {i} " + "x" * 200 for i in range(100)]
        chunks = bot_module._chunk_message(lines)
        assert len(chunks) > 1
        assert all(len(c) <= 4096 for c in chunks)
        assert "\n".join(chunks) == "\n".join(lines)  # hech narsa yo'qolmaydi

    def test_empty_input(self, bot_module):
        assert bot_module._chunk_message([]) == []


class TestConfirmMessageFinish:
    def test_no_tickets_edits_confirmation_instead_of_new_message(self, bot_module):
        bot_module._confirm_messages["m1"] = (10, 20, f"✅ boshlandi\n\n{bot_module.CHECKING_LINE}")
        app = MagicMock()
        app.bot.edit_message_text = AsyncMock()
        asyncio.run(bot_module._finish_confirm_message(app, "m1", False))
        text = app.bot.edit_message_text.await_args.args[0]
        assert "hozircha mos bilet yo'q" in text
        assert bot_module.CHECKING_LINE not in text
        assert "m1" not in bot_module._confirm_messages

    def test_found_tickets_marks_checked(self, bot_module):
        bot_module._confirm_messages["m2"] = (10, 20, f"x\n{bot_module.CHECKING_LINE}")
        app = MagicMock()
        app.bot.edit_message_text = AsyncMock()
        asyncio.run(bot_module._finish_confirm_message(app, "m2", True))
        assert "yuqoridagi xabarda" in app.bot.edit_message_text.await_args.args[0]

    def test_unknown_monitor_is_noop(self, bot_module):
        app = MagicMock()
        app.bot.edit_message_text = AsyncMock()
        asyncio.run(bot_module._finish_confirm_message(app, "yo'q", False))
        app.bot.edit_message_text.assert_not_awaited()

    def test_edit_failure_is_swallowed(self, bot_module):
        bot_module._confirm_messages["m3"] = (1, 2, bot_module.CHECKING_LINE)
        app = MagicMock()
        app.bot.edit_message_text = AsyncMock(side_effect=RuntimeError("boom"))
        asyncio.run(bot_module._finish_confirm_message(app, "m3", False))  # xato ko'tarilmasligi kerak


class TestNoAccessText:
    def test_contains_user_id(self, bot_module):
        assert "123456" in bot_module._no_access_text(123456)
