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

    def test_zero_tariff_seats_with_car_level_seats_uses_car_level_count(self, bot_module):
        # Haqiqiy production API (oddiy yo'lovchi poyezdlari): tarif darajasida
        # freeSeats=0, haqiqiy son vagon darajasida. Avval bu "joy yo'q" deb
        # noto'g'ri tashlab yuborilgan (plaskart/kupe topilmagan).
        trains = [_train(cars=[_car(free=3, tariff_seats=0)])]
        found = bot_module._find_all_trains(trains, "any")
        assert len(found) == 1
        assert found[0][3] == 3

    def test_zero_everywhere_is_skipped(self, bot_module):
        trains = [_train(cars=[_car(free=0, tariff_seats=0)])]
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


class TestSilentDropFixAndDiagnostics:
    """"Yo'lovchi" poyezdlardagi bilet bot tomonidan jim yo'qotilishi muammosi:
    vagon darajasidagi freeSeats 0 bo'lsa-yu, tarifda joy bor bo'lsa, vagon
    endi tashlab yuborilmaydi; hech narsa topilmagan poyezdlarning xom
    ko'rinishi esa (bir marta) diagnostika sifatida log qilinadi."""

    def test_car_level_zero_but_tariff_has_seats_is_found(self, bot_module):
        car = {
            "type": "Плацкартный", "freeSeats": 0,
            "tariffs": [{"tariff": 150_000, "freeSeats": 7, "classServiceType": "Плацкарт"}],
        }
        found = bot_module._find_all_trains([_train(cars=[car])], "platskar")
        assert len(found) == 1
        assert found[0][3] == 7

    def test_car_level_zero_and_tariff_zero_still_skipped(self, bot_module):
        car = {
            "type": "Купе", "freeSeats": 0,
            "tariffs": [{"tariff": 200_000, "freeSeats": 0, "classServiceType": "Купе"}],
        }
        assert bot_module._find_all_trains([_train(cars=[car])], "any") == []

    def test_cars_empty_train_logged_once_with_raw_dump(self, bot_module, caplog):
        import logging
        bot_module._diagnosed_trains.clear()
        train = _train(number="752Ж", brand="Jaloliddin Manguberdi", cars=[])
        with caplog.at_level(logging.WARNING, logger="railway_bot"):
            bot_module._find_all_trains([train], "any")
            bot_module._find_all_trains([train], "any")  # ikkinchi marta — qayta log qilinmaydi
        diag = [r for r in caplog.records if "DIAGNOSTIKA" in r.getMessage()]
        assert len(diag) == 1
        assert "cars_empty" in diag[0].getMessage()
        assert "752Ж" in diag[0].getMessage()

    def test_sold_out_train_with_cars_gets_diagnosed_for_any_filter(self, bot_module, caplog):
        import logging
        bot_module._diagnosed_trains.clear()
        train = _train(number="125Ч", brand="Yo'lovchi", cars=[_car(free=0)])
        with caplog.at_level(logging.WARNING, logger="railway_bot"):
            assert bot_module._find_all_trains([train], "any") == []
        assert any("no_result_with_cars" in r.getMessage() for r in caplog.records)

    def test_no_diagnostic_noise_when_user_filter_explains_absence(self, bot_module, caplog):
        import logging
        bot_module._diagnosed_trains.clear()
        train = _train(number="125Ч", cars=[_car(ctype="Купе")])
        with caplog.at_level(logging.WARNING, logger="railway_bot"):
            bot_module._find_all_trains([train], "sv")  # SV so'ralgan — Kupe mos emas, normal holat
        assert not any("DIAGNOSTIKA" in r.getMessage() for r in caplog.records)


# Haqiqiy production API javobidan (DIAGNOSTIKA loglari, 31.10.2026 Toshkent→Buxoro)
# olingan poyezdlar. Oddiy yo'lovchi poyezdlarida tarif darajasidagi freeSeats
# DOIM 0, haqiqiy joy soni vagon darajasida keladi.
REAL_PASSENGER_056CH = {
    "type": "TY", "number": "056Ч", "brand": "Yo'lovchi",
    "departureDate": "31.10.2026 21:45", "arrivalDate": "01.11.2026 05:12",
    "cars": [
        {"type": "Plaskartli", "freeSeats": 174,
         "tariffs": [{"classServiceType": "3П", "freeSeats": 0, "tariff": 177990}]},
        {"type": "Kupe", "freeSeats": 12,
         "tariffs": [{"classServiceType": "2К", "freeSeats": 0, "tariff": 243750}]},
    ],
}
REAL_PASSENGER_072F = {
    "type": "YLCh", "number": "072Ф", "brand": "Yo'lovchi",
    "departureDate": "30.10.2026 22:34", "arrivalDate": "31.10.2026 06:28",
    "cars": [
        {"type": "Plaskartli", "freeSeats": 88,
         "tariffs": [{"classServiceType": "3П", "freeSeats": 0, "tariff": 177990}]},
        {"type": "Kupe", "freeSeats": 61,
         "tariffs": [{"classServiceType": "2К", "freeSeats": 0, "tariff": 243750}]},
        {"type": "SV", "freeSeats": 1,
         "tariffs": [{"classServiceType": "1Л", "freeSeats": 0, "tariff": 430580}]},
    ],
}
# Afrosiyob uslubi: joy soni TARIF darajasida (haqiqiy log: 710Ф [1С] 123 joy, [1В] 13 joy)
AFROSIYOB_STYLE = {
    "number": "710Ф", "brand": "Afrosiyob",
    "departureDate": "31.10.2026 08:37", "arrivalDate": "31.10.2026 12:40",
    "cars": [{
        "type": "Biznes", "freeSeats": 136,
        "tariffs": [
            {"classServiceType": "1С", "freeSeats": 123, "tariff": 421050},
            {"classServiceType": "1В", "freeSeats": 13, "tariff": 808830},
        ],
    }],
}


class TestRealPassengerTrainPayload:
    """Foydalanuvchi hisoboti: 31.10 da 'Yo'lovchi' poyezdlardagi plaskart/kupe
    bot tomonidan topilmadi (faqat Afrosiyob/Sharq topildi). Sabab: tarif
    darajasidagi freeSeats=0 "joy yo'q" deb talqin qilingan."""

    def test_platskart_found_with_car_level_seat_count(self, bot_module):
        found = bot_module._find_all_trains([REAL_PASSENGER_056CH], "platskar")
        assert len(found) == 1
        _train_, _car_, price, seats, service = found[0]
        assert (price, seats, service) == (177_990, 174, "3П")

    def test_kupe_found_with_car_level_seat_count(self, bot_module):
        found = bot_module._find_all_trains([REAL_PASSENGER_056CH], "coupe")
        assert len(found) == 1
        assert found[0][3] == 12 and found[0][2] == 243_750

    def test_any_returns_both_platskart_and_kupe(self, bot_module):
        found = bot_module._find_all_trains([REAL_PASSENGER_056CH], "any")
        assert {f[4] for f in found} == {"3П", "2К"}

    def test_sv_with_single_seat_found_and_min_seats_respected(self, bot_module):
        assert len(bot_module._find_all_trains([REAL_PASSENGER_072F], "sv")) == 1
        # 1 ta joy bor, 2 ta kerak — chiqmasligi kerak
        assert bot_module._find_all_trains([REAL_PASSENGER_072F], "sv", min_seats=2) == []

    def test_max_price_applies_to_passenger_tariffs(self, bot_module):
        found = bot_module._find_all_trains([REAL_PASSENGER_056CH], "any", max_price=200_000)
        assert [f[4] for f in found] == ["3П"]  # kupe (243,750) narx chegarasidan oshadi

    def test_afrosiyob_style_tariff_level_counts_unchanged(self, bot_module):
        found = bot_module._find_all_trains([AFROSIYOB_STYLE], "any")
        assert {(f[4], f[3]) for f in found} == {("1С", 123), ("1В", 13)}

    def test_mixed_tariffs_zero_class_stays_sold_out(self, bot_module):
        # Bir sinfda joy bor, boshqasi rostdan tugagan (0) — 0 ni car-level
        # son bilan almashtirmaymiz (soxta "joy bor" xabari bo'lmasin).
        train = dict(AFROSIYOB_STYLE)
        train["cars"] = [{
            "type": "Biznes", "freeSeats": 50,
            "tariffs": [
                {"classServiceType": "1С", "freeSeats": 50, "tariff": 421050},
                {"classServiceType": "1В", "freeSeats": 0, "tariff": 808830},
            ],
        }]
        found = bot_module._find_all_trains([train], "any")
        assert [(f[4], f[3]) for f in found] == [("1С", 50)]

    def test_truly_sold_out_car_still_skipped(self, bot_module):
        train = dict(REAL_PASSENGER_056CH)
        train["cars"] = [{
            "type": "Plaskartli", "freeSeats": 0,
            "tariffs": [{"classServiceType": "3П", "freeSeats": 0, "tariff": 177990}],
        }]
        assert bot_module._find_all_trains([train], "any") == []
