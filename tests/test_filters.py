"""Poyezd filtrlari (_time_in_range, _find_all_trains) testlari"""


def _train(number="001", brand="Poyezd", dep="2030-01-10 08:30", cars=None):
    return {
        "number": number,
        "brand": brand,
        "departureDate": dep,
        "arrivalDate": "2030-01-10 12:00",
        "cars": cars if cars is not None else [],
    }


def _car(ctype="Ўриндиқ", free=5, price=100_000, tariff_seats=None):
    return {
        "type": ctype,
        "freeSeats": free,
        "tariffs": [{
            "tariff": price,
            "freeSeats": tariff_seats if tariff_seats is not None else free,
            "classServiceType": ctype,
        }],
    }


class TestTimeInRange:
    def test_full_day_always_true(self, bot_module):
        assert bot_module._time_in_range("2030-01-10 03:15", "00:00", "23:59") is True

    def test_inside_range(self, bot_module):
        assert bot_module._time_in_range("2030-01-10 08:30", "06:00", "11:59") is True

    def test_outside_range(self, bot_module):
        assert bot_module._time_in_range("2030-01-10 14:00", "06:00", "11:59") is False

    def test_overnight_range(self, bot_module):
        # 22:00–02:00 oralig'i yarim tundan o'tadi
        assert bot_module._time_in_range("2030-01-10 23:30", "22:00", "02:00") is True
        assert bot_module._time_in_range("2030-01-10 01:00", "22:00", "02:00") is True
        assert bot_module._time_in_range("2030-01-10 12:00", "22:00", "02:00") is False

    def test_malformed_date_passes(self, bot_module):
        # buzilgan sana filtrni bloklamasligi kerak
        assert bot_module._time_in_range("nonsense", "06:00", "11:59") is True


class TestFindAllTrains:
    def test_any_type_returns_all_with_seats(self, bot_module):
        trains = [_train(cars=[_car()])]
        found = bot_module._find_all_trains(trains, "any")
        assert len(found) == 1

    def test_no_cars_skipped(self, bot_module):
        trains = [_train(cars=[])]
        assert bot_module._find_all_trains(trains, "any") == []

    def test_zero_seats_skipped(self, bot_module):
        trains = [_train(cars=[_car(free=0)])]
        assert bot_module._find_all_trains(trains, "any") == []

    def test_zero_tariff_seats_skipped(self, bot_module):
        trains = [_train(cars=[_car(free=3, tariff_seats=0)])]
        assert bot_module._find_all_trains(trains, "any") == []

    def test_price_cap(self, bot_module):
        trains = [_train(cars=[_car(price=200_000)])]
        assert bot_module._find_all_trains(trains, "any", max_price=150_000) == []
        assert len(bot_module._find_all_trains(trains, "any", max_price=250_000)) == 1

    def test_min_seats_filters_out_insufficient_tariffs(self, bot_module):
        # Foydalanuvchi 2 ta joy so'rasa, 1 tagina joy bor tarif ko'rsatilmasligi kerak
        trains = [_train(cars=[_car(tariff_seats=1)])]
        assert bot_module._find_all_trains(trains, "any", min_seats=2) == []

    def test_min_seats_passes_when_enough(self, bot_module):
        trains = [_train(cars=[_car(tariff_seats=3)])]
        found = bot_module._find_all_trains(trains, "any", min_seats=2)
        assert len(found) == 1
        assert found[0][3] == 3

    def test_min_seats_default_is_one(self, bot_module):
        trains = [_train(cars=[_car(tariff_seats=1)])]
        assert len(bot_module._find_all_trains(trains, "any")) == 1

    def test_car_type_keyword_filter(self, bot_module):
        trains = [_train(cars=[_car(ctype="Купе"), _car(ctype="Ўриндиқ")])]
        found = bot_module._find_all_trains(trains, "platskar")
        assert len(found) == 1
        assert found[0][4] == "Ўриндиқ"

    def test_brand_filter_afrosiyob(self, bot_module):
        trains = [
            _train(number="778", brand="Afrosiyob", cars=[_car(ctype="Econom")]),
            _train(number="001", brand="Oddiy", cars=[_car(ctype="Econom")]),
        ]
        found = bot_module._find_all_trains(trains, "afrosiyob")
        assert len(found) == 1
        assert found[0][0]["number"] == "778"

    def test_sv_matches_cyrillic_type(self, bot_module):
        # railway.uz API vagon turini kirillcha "СВ" deb qaytaradi
        trains = [_train(cars=[_car(ctype="СВ")])]
        found = bot_module._find_all_trains(trains, "sv")
        assert len(found) == 1

    def test_sv_matches_lux_spelling(self, bot_module):
        # 054Ф reysida API vagon turini "Lux" deb ham qaytargani prod loglarida qayd etildi
        trains = [_train(cars=[_car(ctype="Lux")])]
        found = bot_module._find_all_trains(trains, "sv")
        assert len(found) == 1

    def test_platskar_matches_real_api_spellings(self, bot_module):
        # Haqiqiy API "Plaskartli" (lotin) yoki "Плацкартный" (kirill) deb qaytaradi
        trains = [_train(cars=[_car(ctype="Plaskartli")])]
        assert len(bot_module._find_all_trains(trains, "platskar")) == 1
        trains = [_train(cars=[_car(ctype="Плацкартный")])]
        assert len(bot_module._find_all_trains(trains, "platskar")) == 1

    def test_time_range_filter(self, bot_module):
        trains = [
            _train(number="M", dep="2030-01-10 07:00", cars=[_car()]),
            _train(number="E", dep="2030-01-10 20:00", cars=[_car()]),
        ]
        found = bot_module._find_all_trains(trains, "any", time_from="06:00", time_to="11:59")
        assert [f[0]["number"] for f in found] == ["M"]


class TestFindAllTrainsSchemaResilience:
    """P1 audit: API javobi kutilmagan sxemada bo'lsa, funksiya qulamasligi
    va soxta/ishonchsiz ma'lumotli "bilet"ni e'lon qilmasligi kerak —
    lekin boshqa, to'g'ri formatdagi yozuvlarni ishlashda davom etishi kerak."""

    def test_missing_tariffs_does_not_crash_and_is_excluded(self, bot_module):
        # Spec misoli: {"freeSeats": 4, "tariffs": []} — joy bordek
        # ko'rinadi, lekin narx/sinfni ishonchli bilmaymiz.
        car = {"type": "Ўриндиқ", "freeSeats": 4, "tariffs": []}
        trains = [_train(cars=[car])]
        assert bot_module._find_all_trains(trains, "any") == []

    def test_missing_tariffs_key_entirely(self, bot_module):
        car = {"type": "Ўриндиқ", "freeSeats": 4}  # "tariffs" kaliti umuman yo'q
        trains = [_train(cars=[car])]
        assert bot_module._find_all_trains(trains, "any") == []

    def test_missing_cars_key_entirely(self, bot_module):
        train = _train()
        del train["cars"]
        assert bot_module._find_all_trains([train], "any") == []

    def test_malformed_tariff_price_skipped_not_crashed(self, bot_module):
        car = {
            "type": "Ўриндиқ", "freeSeats": 2,
            "tariffs": [{"tariff": "noto'g'ri-narx", "freeSeats": 2, "classServiceType": "Econom"}],
        }
        trains = [_train(cars=[car])]
        assert bot_module._find_all_trains(trains, "any") == []

    def test_one_malformed_tariff_does_not_block_other_valid_ones(self, bot_module):
        car = {
            "type": "Ўриндиқ", "freeSeats": 5,
            "tariffs": [
                {"tariff": None, "freeSeats": 2, "classServiceType": "Bad"},
                {"tariff": 100_000, "freeSeats": 3, "classServiceType": "Good"},
            ],
        }
        trains = [_train(cars=[car])]
        found = bot_module._find_all_trains(trains, "any")
        assert len(found) == 1
        assert found[0][4] == "Good"

    def test_malformed_car_entry_skipped(self, bot_module):
        trains = [_train(cars=["bu_dict_emas", _car()])]
        found = bot_module._find_all_trains(trains, "any")
        assert len(found) == 1  # faqat to'g'ri car yozuvi hisoblanadi

    def test_malformed_train_entry_skipped(self, bot_module):
        trains = ["bu_dict_emas", _train(cars=[_car()])]
        found = bot_module._find_all_trains(trains, "any")
        assert len(found) == 1

    def test_unknown_car_type_still_matches_any_filter(self, bot_module):
        # "any" so'ralganda, hech qaysi ma'lum kategoriyaga mos kelmasa ham
        # (masalan railway.uz yangi sinf qo'shsa) ko'rsatilishi kerak —
        # noma'lum turlarni "any" filtri jim yo'qotib yubormaydi.
        trains = [_train(cars=[_car(ctype="Mutlaqo Yangi Sinf™")])]
        found = bot_module._find_all_trains(trains, "any")
        assert len(found) == 1

    def test_none_trains_list_does_not_crash(self, bot_module):
        assert bot_module._find_all_trains(None, "any") == []

    def test_empty_response_returns_empty(self, bot_module):
        assert bot_module._find_all_trains([], "any") == []


class TestNormalizeCarType:
    """P1: markazlashtirilgan, testlanadigan vagon turi normalizatsiyasi."""

    def test_platskar_variants(self, bot_module):
        for raw in ["Plaskartli", "Плацкартный", "o'rindiq", "seat"]:
            assert bot_module.normalize_car_type(raw) == "platskar"

    def test_coupe_variants(self, bot_module):
        for raw in ["Купе", "kupe", "yotoq"]:
            assert bot_module.normalize_car_type(raw) == "coupe"

    def test_sv_variants(self, bot_module):
        for raw in ["СВ", "Lux", "Lyuks", "VIP"]:
            assert bot_module.normalize_car_type(raw) == "sv"

    def test_unknown_is_explicit_not_silent(self, bot_module):
        assert bot_module.normalize_car_type("Mutlaqo Notanish Qiymat") == "unknown"

    def test_empty_or_none_is_unknown(self, bot_module):
        assert bot_module.normalize_car_type("") == "unknown"
        assert bot_module.normalize_car_type(None) == "unknown"

    def test_non_string_is_unknown_not_crash(self, bot_module):
        assert bot_module.normalize_car_type(12345) == "unknown"

    def test_case_insensitive(self, bot_module):
        assert bot_module.normalize_car_type("KUPE") == "coupe"


class TestTrainFingerprint:
    """audit P0#2: bir xil joy keyingi tekshiruvda qayta 'yangi' deb
    hisoblanmasligi kerak — fingerprint shu solishtirishning asosi."""

    def test_same_train_same_fingerprint(self, bot_module):
        trains = [_train(cars=[_car(price=100_000)])]
        found1 = bot_module._find_all_trains(trains, "any")
        found2 = bot_module._find_all_trains(trains, "any")
        assert bot_module._train_fingerprint(found1[0]) == bot_module._train_fingerprint(found2[0])

    def test_different_price_different_fingerprint(self, bot_module):
        cheap = bot_module._find_all_trains(
            [_train(cars=[_car(price=100_000)])], "any"
        )[0]
        expensive = bot_module._find_all_trains(
            [_train(cars=[_car(price=200_000)])], "any"
        )[0]
        assert bot_module._train_fingerprint(cheap) != bot_module._train_fingerprint(expensive)

    def test_different_train_number_different_fingerprint(self, bot_module):
        a = bot_module._find_all_trains(
            [_train(number="001", cars=[_car()])], "any"
        )[0]
        b = bot_module._find_all_trains(
            [_train(number="002", cars=[_car()])], "any"
        )[0]
        assert bot_module._train_fingerprint(a) != bot_module._train_fingerprint(b)
