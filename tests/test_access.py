"""Kirish huquqi (is_admin / has_access) matritsasi testlari.

P0 xavfsizlik qoidasi: kirish nazorati konfiguratsiyasi aniqlanmagan yoki
bo'sh bo'lgan holatda bot HECH QACHON "hamma ruxsatli" bo'lib qolmasligi
kerak (fail-closed). Bu fayl aynan shu kafolatni tekshiradi."""


def test_admin_from_admin_ids(bot_module):
    bot_module.Config.ADMIN_IDS = [111, 222]
    assert bot_module.is_admin(111) is True
    assert bot_module.is_admin(222) is True
    assert bot_module.is_admin(333) is False


def test_no_hardcoded_fallback_admin(bot_module):
    # Eski hardcoded ADMIN_ID butunlay olib tashlangan — endi faqat
    # ADMIN_IDS orqali (Railway Variables) beriladi, boshqa fallback yo'q.
    assert not hasattr(bot_module, "ADMIN_ID")


def test_is_admin_fails_closed_when_admin_ids_empty(bot_module):
    # Himoya qatlami: agar negadir ADMIN_IDS bo'sh bo'lib qolsa (masalan
    # Config.validate() chetlab o'tilgan holatda), hech kim admin emas —
    # "hamma admin" kabi xavfli standart holatga qaytilmaydi.
    bot_module.Config.ADMIN_IDS = []
    assert bot_module.is_admin(111) is False
    assert bot_module.is_admin(0) is False


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


def test_unknown_user_denied_when_nothing_configured(bot_module, tmp_path, monkeypatch):
    """P0: eski xatti-harakat (hech narsa sozlanmagan bo'lsa — hamma
    ruxsatli, 'ochiq bot') ATAYLAB olib tashlandi. Endi bu holat ham
    yopiq: kirish nazorati konfiguratsiyasi bo'lmasa, hech kim (admin
    ham) kira olmaydi — bu Config.validate() orqali ishga tushishning
    o'zida oldini olinadi, lekin has_access() ham mustaqil ravishda
    fail-closed bo'lishi kerak (himoya qatlamlari mustaqil ishlashi uchun)."""
    from database import Database
    monkeypatch.setattr(bot_module, "db", Database(path=str(tmp_path / "d.json")))
    bot_module.Config.ADMIN_IDS = []
    bot_module.Config.ALLOWED_USERS = []
    assert bot_module.has_access(12345) is False


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
    assert bot_module._admin_ids() == []


class TestConfigValidateFailsClosed:
    """Config.validate() ishga tushishning o'zida kritik sozlamalarni
    tekshiradi — ADMIN_IDS yo'qligi ishga tushishni to'xtatishi kerak."""

    def _base_env(self, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN", "1234567890:AAF" + "x" * 33)

    def test_missing_admin_ids_blocks_startup(self, monkeypatch, tmp_path):
        import importlib
        self._base_env(monkeypatch)
        monkeypatch.setenv("ADMIN_IDS", "")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import config
        importlib.reload(config)
        try:
            with __import__("pytest").raises(ValueError, match="ADMIN_IDS"):
                config.Config.validate()
        finally:
            importlib.reload(config)  # keyingi testlar uchun asl holatga qaytarish

    def test_missing_bot_token_blocks_startup(self, monkeypatch, tmp_path):
        import importlib
        monkeypatch.setenv("BOT_TOKEN", "")
        monkeypatch.setenv("ADMIN_IDS", "111")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import config
        importlib.reload(config)
        try:
            with __import__("pytest").raises(ValueError, match="BOT_TOKEN"):
                config.Config.validate()
        finally:
            importlib.reload(config)

    def test_valid_config_passes(self, monkeypatch, tmp_path):
        import importlib
        self._base_env(monkeypatch)
        monkeypatch.setenv("ADMIN_IDS", "111,222")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import config
        importlib.reload(config)
        try:
            config.Config.validate()  # xato ko'tarmasligi kerak
            assert config.Config.ADMIN_IDS == [111, 222]
        finally:
            importlib.reload(config)

    def test_unwritable_data_dir_blocks_startup(self, monkeypatch, tmp_path):
        # root sifatida ishlaydigan muhitlarda chmod orqali "yozib bo'lmaydigan"
        # papka hosil qilib bo'lmaydi (root cheklovlarni chetlab o'tadi), shuning
        # uchun buning o'rniga fayl tizimi darajasidagi haqiqiy xatoni ishlatamiz:
        # DATA_DIR yo'lining bir qismi sifatida ODDIY FAYL (papka emas) qo'yamiz —
        # os.makedirs bunda har doim (hatto root uchun ham) muvaffaqiyatsiz bo'ladi.
        import importlib
        self._base_env(monkeypatch)
        monkeypatch.setenv("ADMIN_IDS", "111")
        blocker_file = tmp_path / "not_a_directory"
        blocker_file.write_text("men papka emasman")
        monkeypatch.setenv("DATA_DIR", str(blocker_file / "sub"))
        import config
        importlib.reload(config)
        try:
            with __import__("pytest").raises(ValueError, match="DATA_DIR"):
                config.Config.validate()
        finally:
            importlib.reload(config)
