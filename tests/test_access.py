"""Kirish huquqi (is_admin / has_access) matritsasi testlari"""


def test_admin_from_admin_ids(bot_module):
    bot_module.Config.ADMIN_IDS = [111, 222]
    assert bot_module.is_admin(111) is True
    assert bot_module.is_admin(222) is True
    assert bot_module.is_admin(333) is False
    # ADMIN_IDS sozlanganda eski hardcoded admin endi admin emas
    assert bot_module.is_admin(bot_module.ADMIN_ID) is False


def test_admin_fallback_when_no_admin_ids(bot_module):
    bot_module.Config.ADMIN_IDS = []
    assert bot_module.is_admin(bot_module.ADMIN_ID) is True
    assert bot_module.is_admin(111) is False


def test_admin_always_has_access(bot_module):
    bot_module.Config.ADMIN_IDS = [111]
    bot_module.Config.ALLOWED_USERS = []
    assert bot_module.has_access(111) is True


def test_added_user_has_access(bot_module, tmp_path, monkeypatch):
    from database import Database
    monkeypatch.setattr(bot_module, "db", Database(path=str(tmp_path / "d.json")))
    bot_module.Config.ADMIN_IDS = [111]
    bot_module.Config.ALLOWED_USERS = []
    assert bot_module.has_access(999) is False
    bot_module.db.add_user(999, added_by=111)
    assert bot_module.has_access(999) is True
    bot_module.db.remove_user(999)
    assert bot_module.has_access(999) is False


def test_allowed_users_env(bot_module, tmp_path, monkeypatch):
    from database import Database
    monkeypatch.setattr(bot_module, "db", Database(path=str(tmp_path / "d.json")))
    bot_module.Config.ADMIN_IDS = [111]
    bot_module.Config.ALLOWED_USERS = [555]
    assert bot_module.has_access(555) is True
    assert bot_module.has_access(556) is False


def test_open_bot_when_nothing_configured(bot_module, tmp_path, monkeypatch):
    from database import Database
    monkeypatch.setattr(bot_module, "db", Database(path=str(tmp_path / "d.json")))
    bot_module.Config.ADMIN_IDS = []
    bot_module.Config.ALLOWED_USERS = []
    # hech narsa sozlanmagan — eski xatti-harakat: hamma ruxsatli
    assert bot_module.has_access(12345) is True


def test_closed_bot_when_admin_ids_set(bot_module, tmp_path, monkeypatch):
    from database import Database
    monkeypatch.setattr(bot_module, "db", Database(path=str(tmp_path / "d.json")))
    bot_module.Config.ADMIN_IDS = [111]
    bot_module.Config.ALLOWED_USERS = []
    # admin tizimi yoqilgan — begonalarga yopiq
    assert bot_module.has_access(12345) is False


def test_admin_ids_helper(bot_module):
    bot_module.Config.ADMIN_IDS = [1, 2]
    assert bot_module._admin_ids() == [1, 2]
    bot_module.Config.ADMIN_IDS = []
    assert bot_module._admin_ids() == [bot_module.ADMIN_ID]
