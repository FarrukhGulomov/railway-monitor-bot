"""Database qatlami testlari"""


class TestUsers:
    def test_add_new_user(self, db):
        assert db.add_user(100, added_by=1, first_name="Ali") is True
        assert db.is_added_user(100) is True
        u = db.get_user(100)
        assert u["first_name"] == "Ali"
        assert u["added_by"] == 1
        assert u["active"] is True

    def test_add_existing_user_reactivates(self, db):
        db.add_user(100, added_by=1)
        db.remove_user(100)
        assert db.is_added_user(100) is False
        assert db.add_user(100, added_by=1) is False  # yangi emas
        assert db.is_added_user(100) is True

    def test_remove_user(self, db):
        db.add_user(100, added_by=1)
        assert db.remove_user(100) is True
        assert db.is_added_user(100) is False
        # ikkinchi marta o'chirish — False
        assert db.remove_user(100) is False

    def test_remove_unknown_user(self, db):
        assert db.remove_user(555) is False

    def test_get_users_active_only(self, db):
        db.add_user(1, added_by=9)
        db.add_user(2, added_by=9)
        db.remove_user(2)
        actives = db.get_users(active_only=True)
        assert [u["tid"] for u in actives] == [1]
        everyone = db.get_users(active_only=False)
        assert {u["tid"] for u in everyone} == {1, 2}

    def test_set_user_phone(self, db):
        db.add_user(100, added_by=1)
        assert db.set_user_phone(100, "+998901234567") is True
        assert db.get_user(100)["phone"] == "+998901234567"
        # ro'yxatda bo'lmagan foydalanuvchi uchun saqlanmaydi
        assert db.set_user_phone(200, "+998900000000") is False

    def test_touch_user_activity(self, db):
        db.add_user(100, added_by=1)
        db.touch_user_activity(100)
        db.touch_user_activity(100)
        u = db.get_user(100)
        assert u["action_count"] == 2
        assert u["last_seen"] is not None

    def test_touch_unknown_user_noop(self, db):
        db.touch_user_activity(777)  # xato bermasligi kerak
        assert db.get_user(777) is None


class TestMonitors:
    MON = {
        "from_name": "A", "from_code": "1", "to_name": "B", "to_code": "2",
        "date": "2030-01-01", "car_type": "any", "active": True,
    }

    def test_save_and_get(self, db):
        mid = db.save_monitor(100, dict(self.MON))
        assert db.is_active(mid) is True
        mons = db.get_active_monitors(100)
        assert len(mons) == 1
        assert mons[0]["id"] == mid

    def test_deactivate_for_user_ownership(self, db):
        mid = db.save_monitor(100, dict(self.MON))
        # boshqa foydalanuvchi o'chira olmaydi
        assert db.deactivate_for_user(999, mid) is False
        assert db.is_active(mid) is True
        assert db.deactivate_for_user(100, mid) is True
        assert db.is_active(mid) is False

    def test_deactivate_all(self, db):
        db.save_monitor(100, dict(self.MON))
        db.save_monitor(100, dict(self.MON))
        db.save_monitor(200, dict(self.MON))
        assert db.deactivate_all(100) == 2
        assert db.get_active_monitors(100) == []
        assert len(db.get_active_monitors(200)) == 1

    def test_get_all_active_monitors(self, db):
        db.save_monitor(100, dict(self.MON))
        db.save_monitor(200, dict(self.MON))
        mid = db.save_monitor(300, dict(self.MON))
        db.deactivate(mid)
        assert len(db.get_all_active_monitors()) == 2

    def test_update_monitor_field(self, db):
        mid = db.save_monitor(100, dict(self.MON))
        assert db.update_monitor_field(100, mid, "max_price", 50000) is True
        assert db.get_active_monitors(100)[0]["max_price"] == 50000
        assert db.update_monitor_field(999, mid, "max_price", 1) is False

    def test_deactivate_expired(self, db):
        old = dict(self.MON, date="2020-01-01")
        future = dict(self.MON, date="2099-01-01")
        db.save_monitor(100, old)
        db.save_monitor(100, dict(old))
        keep_mid = db.save_monitor(100, future)

        removed = db.deactivate_expired("2026-07-06")
        assert len(removed) == 2
        assert all(m["stop_reason"] == "expired" for m in removed)
        active = db.get_active_monitors(100)
        assert [m["id"] for m in active] == [keep_mid]
        # qayta chaqirilganda hech narsa o'chirilmaydi
        assert db.deactivate_expired("2026-07-06") == []

    def test_deactivate_expired_same_day_kept(self, db):
        db.save_monitor(100, dict(self.MON, date="2026-07-06"))
        # bugungi sana hali o'tmagan — o'chirilmasligi kerak
        assert db.deactivate_expired("2026-07-06") == []
        assert len(db.get_active_monitors(100)) == 1

    def test_stats(self, db):
        mid = db.save_monitor(100, dict(self.MON))
        db.increment_check(mid)
        db.increment_check(mid)
        db.save_monitor(100, dict(self.MON))
        db.deactivate(mid)
        stats = db.get_user_monitor_stats(100)
        assert stats == {"total": 2, "active": 1, "total_checks": 2}


class TestResilience:
    def test_nested_data_dir_created(self, tmp_path):
        # DATA_DIR (masalan Railway Volume /data) hali mavjud bo'lmasa ham ishlashi kerak
        from database import Database
        path = tmp_path / "volume" / "sub" / "data.json"
        db = Database(path=str(path))
        assert path.exists()
        assert db.add_user(1, added_by=2) is True

    def test_corrupt_file_returns_empty(self, tmp_path):
        from database import Database
        path = tmp_path / "data.json"
        path.write_text("{buzilgan json!!", encoding="utf-8")
        db = Database(path=str(path))
        assert db.get_active_monitors(1) == []
        assert db.get_users() == []

    def test_missing_users_key_migrated(self, tmp_path):
        # eski format (faqat monitors) bilan ham ishlashi kerak
        from database import Database
        path = tmp_path / "data.json"
        path.write_text('{"monitors": {}}', encoding="utf-8")
        db = Database(path=str(path))
        assert db.get_users() == []
        assert db.add_user(1, added_by=2) is True

    def test_corrupt_file_is_backed_up_not_silently_lost(self, tmp_path):
        # audit P0#4: buzilgan faylni ustidan yozib, ma'lumotni yo'qotib
        # qo'ymasdan, forensik nusxasini saqlashi kerak
        from database import Database
        path = tmp_path / "data.json"
        path.write_text('{"monitors": {"x": {broken', encoding="utf-8")
        db = Database(path=str(path))
        db.get_active_monitors(1)  # _read() ni ishga tushiradi
        backups = list(tmp_path.glob("data.json.corrupt-*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == '{"monitors": {"x": {broken'

    def test_corrupt_file_backed_up_only_once(self, tmp_path):
        from database import Database
        path = tmp_path / "data.json"
        path.write_text("{not json", encoding="utf-8")
        db = Database(path=str(path))
        db.get_active_monitors(1)
        db.get_users()
        db.get_active_monitors(1)
        backups = list(tmp_path.glob("data.json.corrupt-*"))
        assert len(backups) == 1


class TestWriteFailurePropagation:
    """audit P0#4: DB yozuvi muvaffaqiyatsiz bo'lsa, chaqiruvchi kod buni
    bilishi kerak — foydalanuvchiga soxta 'muvaffaqiyat' aytilmasligi uchun."""

    MON = {
        "from_name": "A", "from_code": "1", "to_name": "B", "to_code": "2",
        "date": "2030-01-01", "car_type": "any", "active": True,
    }

    def test_save_monitor_returns_none_on_write_failure(self, db, monkeypatch):
        monkeypatch.setattr(db, "_write", lambda data: False)
        assert db.save_monitor(100, dict(self.MON)) is None

    def test_update_monitor_field_returns_false_on_write_failure(self, db, monkeypatch):
        mid = db.save_monitor(100, dict(self.MON))
        monkeypatch.setattr(db, "_write", lambda data: False)
        assert db.update_monitor_field(100, mid, "max_price", 1) is False

    def test_write_returns_true_on_success(self, db):
        assert db._write({"monitors": {}, "users": {}}) is True
