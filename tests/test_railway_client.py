"""RailwayClient.search_trains — API xatosi bilan haqiqiy bo'sh natijani
ajratish testlari (audit P0#1: bular hech qachon bir xil narsa emas)."""

from railway_client import RailwayClient


class _FakeResponse:
    def __init__(self, status_code, text="", json_data=None, headers=None, json_error=False):
        self.status_code = status_code
        self.text = text
        self._json = json_data or {}
        self.headers = headers or {}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("buzilgan JSON")
        return self._json


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.cookies = {}
        self.headers = {}
        self.calls = 0

    def post(self, *a, **kw):
        self.calls += 1
        return self._responses.pop(0)

    def get(self, *a, **kw):
        return _FakeResponse(200)


def _make_client(monkeypatch, responses):
    monkeypatch.setattr(RailwayClient, "_init_session", lambda self: None)
    monkeypatch.setattr("railway_client.time.sleep", lambda *_: None)
    client = RailwayClient()
    client._session = _FakeSession(responses)
    client.MIN_INTERVAL = 0
    return client


class TestSearchResultStatus:
    def test_success_with_trains(self, monkeypatch):
        payload = {"data": {"directions": {"forward": {"trains": [{"number": "1"}]}}}}
        client = _make_client(monkeypatch, [_FakeResponse(200, json_data=payload)])
        result = client.search_trains("A", "B", "2030-01-01")
        assert result.ok is True
        assert result.trains == [{"number": "1"}]

    def test_genuine_empty_result_is_ok_not_error(self, monkeypatch):
        # Haqiqatan hech qanday reys yo'q — bu XATO emas, oddiy bo'sh natija.
        payload = {"data": {"directions": {"forward": {"trains": []}}}}
        client = _make_client(monkeypatch, [_FakeResponse(200, json_data=payload)])
        result = client.search_trains("A", "B", "2030-01-01")
        assert result.ok is True
        assert result.trains == []

    def test_403_is_error_not_empty(self, monkeypatch):
        client = _make_client(monkeypatch, [_FakeResponse(403, text="forbidden")])
        result = client.search_trains("A", "B", "2030-01-01")
        assert result.ok is False
        assert result.trains == []
        assert result.error

    def test_429_exhausted_retries_is_error(self, monkeypatch):
        responses = [_FakeResponse(429, headers={"Retry-After": "1"})] * 3
        client = _make_client(monkeypatch, responses)
        result = client.search_trains("A", "B", "2030-01-01")
        assert result.ok is False

    def test_500_exhausts_retries_is_error(self, monkeypatch):
        # 5xx endi transient deb qayta uriniladi (MAX_RETRIES=3) — uchalasi
        # ham 500 bo'lsa, retrylar tugagach xato qaytariladi.
        responses = [_FakeResponse(500, text="server error")] * 3
        client = _make_client(monkeypatch, responses)
        result = client.search_trains("A", "B", "2030-01-01")
        assert result.ok is False
        assert client._session.calls == 3

    def test_5xx_retries_then_recovers(self, monkeypatch):
        # Birinchi urinish 503 (transient), ikkinchisi muvaffaqiyatli —
        # railway.uz'ning vaqtinchalik xatosidan keyin tiklanishi kerak.
        payload = {"data": {"directions": {"forward": {"trains": [{"number": "7"}]}}}}
        responses = [_FakeResponse(503, text="bad gateway"), _FakeResponse(200, json_data=payload)]
        client = _make_client(monkeypatch, responses)
        result = client.search_trains("A", "B", "2030-01-01")
        assert result.ok is True
        assert result.trains == [{"number": "7"}]
        assert client._session.calls == 2

    def test_malformed_json_is_error_not_empty(self, monkeypatch):
        # Status 200 lekin javob tanasi buzilgan JSON — bu ham "reys yo'q" emas.
        responses = [_FakeResponse(200, json_error=True)] * 3
        client = _make_client(monkeypatch, responses)
        result = client.search_trains("A", "B", "2030-01-01")
        assert result.ok is False
        assert result.trains == []

    def test_400_express_temp_unavailable_is_error_not_empty(self, monkeypatch):
        # Sayt Express xizmati vaqtincha javob bermayapti — bu ham "reys yo'q" emas.
        client = _make_client(
            monkeypatch, [_FakeResponse(400, text="Express: ma'lumot kelmadi")]
        )
        result = client.search_trains("A", "B", "2030-01-01")
        assert result.ok is False
        assert result.trains == []

    def test_400_unexpected_status_is_error_not_empty(self, monkeypatch):
        client = _make_client(
            monkeypatch, [_FakeResponse(400, text="Unexpected status: FOO")]
        )
        result = client.search_trains("A", "B", "2030-01-01")
        assert result.ok is False

    def test_exception_during_request_is_error(self, monkeypatch):
        monkeypatch.setattr(RailwayClient, "_init_session", lambda self: None)
        monkeypatch.setattr("railway_client.time.sleep", lambda *_: None)

        class _RaisingSession(_FakeSession):
            def post(self, *a, **kw):
                raise ConnectionError("network down")

        client = RailwayClient()
        client._session = _RaisingSession([])
        client.MIN_INTERVAL = 0
        result = client.search_trains("A", "B", "2030-01-01")
        assert result.ok is False
        assert result.trains == []

    def test_latency_is_recorded_on_result(self, monkeypatch):
        payload = {"data": {"directions": {"forward": {"trains": []}}}}
        client = _make_client(monkeypatch, [_FakeResponse(200, json_data=payload)])
        result = client.search_trains("A", "B", "2030-01-01")
        assert result.latency >= 0.0


class TestRetryDoesNotBlockOtherRoutes:
    """P1 audit: bitta marshrutning retry/backoff KUTISHI (masalan 429 dan
    keyingi Retry-After) boshqa marshrutlarning HAQIQIY tarmoq so'rovini
    to'smasligi kerak. Buning uchun REAL (lekin qisqa) time.sleep ishlatiladi
    — vaqt monkeypatch bilan o'chirilmaydi, aks holda bu xatti-harakatni
    sinab bo'lmaydi."""

    def test_route_b_completes_while_route_a_is_in_retry_backoff(self, monkeypatch):
        import threading
        import time as real_time

        monkeypatch.setattr(RailwayClient, "_init_session", lambda self: None)
        client = RailwayClient()
        client.MIN_INTERVAL = 0  # pacing bu testga aloqasi yo'q — faqat lock xatti-harakatini tekshiramiz

        payload_ok = {"data": {"directions": {"forward": {"trains": []}}}}
        route_a_started = threading.Event()

        class _RoutingSession(_FakeSession):
            """from_code orqali qaysi marshrut ekanini aniqlab, har biriga
            boshqa xatti-harakat qaytaradi: A — 429 (REAL ~1s kutish
            talab qiladi), B — darhol muvaffaqiyatli."""

            def post(self, url, json=None, **kw):
                from_code = json["directions"]["forward"]["depStationCode"]
                if from_code == "ROUTE_A":
                    route_a_started.set()
                    return _FakeResponse(429, headers={"Retry-After": "1"})
                return _FakeResponse(200, json_data=payload_ok)

        client._session = _RoutingSession([])

        results = {}

        def run_route_a():
            # A — 429 oladi, REAL time.sleep(1) bilan backoff qiladi,
            # keyin retrylar tugab xato bilan qaytadi (test faqat B ning
            # A dan oldin tugashini tekshiradi, A ning yakuniy natijasi
            # muhim emas).
            results["a"] = client.search_trains("ROUTE_A", "X", "2030-01-01")

        thread_a = threading.Thread(target=run_route_a)
        thread_a.start()
        assert route_a_started.wait(timeout=2), "Route A so'rovni boshlamadi"

        # A endi 429 dan keyin REAL ~1s backoff'da — shu payt B o'z
        # so'rovini yuborishi va TEZDA (A ning butun backoff davridan
        # ANCHA oldin) tugashi kerak, agar lock retry-sleep davomida
        # band bo'lmasa.
        b_start = real_time.monotonic()
        result_b = client.search_trains("ROUTE_B", "Y", "2030-01-01")
        b_duration = real_time.monotonic() - b_start

        thread_a.join(timeout=10)

        assert result_b.ok is True
        assert b_duration < 0.5, (
            f"Route B {b_duration:.2f}s davom etdi — bu Route A ning retry "
            "backoff kutishi bilan to'silgandek ko'rinadi (session_lock "
            "retry sleep davomida ushlab turilgan bo'lishi mumkin)"
        )
