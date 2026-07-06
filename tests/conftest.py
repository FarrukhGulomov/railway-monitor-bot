"""
Test sozlamalari.
- Ish papkasi vaqtinchalik joyga ko'chiriladi (data.json / bot.log repo'ni iflos qilmasin)
- BOT_TOKEN soxta qiymat bilan to'ldiriladi (Config.validate testlarda chaqirilmaydi)
"""

import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "1234567890:AAF" + "x" * 33)

_workdir = tempfile.mkdtemp(prefix="railway-bot-tests-")
os.chdir(_workdir)


@pytest.fixture
def db(tmp_path):
    from database import Database
    return Database(path=str(tmp_path / "data.json"))


@pytest.fixture
def bot_module():
    """bot modulini import qilib, Config holatini testdan keyin tiklaydi"""
    import bot
    saved_admin = list(bot.Config.ADMIN_IDS)
    saved_allowed = list(bot.Config.ALLOWED_USERS)
    saved_notify = dict(bot._last_start_notify)
    yield bot
    bot.Config.ADMIN_IDS = saved_admin
    bot.Config.ALLOWED_USERS = saved_allowed
    bot._last_start_notify.clear()
    bot._last_start_notify.update(saved_notify)
