"""RailwayClient.search_trains — API xatosi bilan haqiqiy bo'sh natijani
ajratish testlari (audit P0#1: bular hech qachon bir xil narsa emas)."""

from railway_client import RailwayClient


class _FakeResponse:
    def __init__(self, status_code, text="", json_data=None, headers=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data or {}
        self.headers = headers or {}

    def json(self):
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

    def test_500_is_error(self, monkeypatch):
        client = _make_client(monkeypatch, [_FakeResponse(500, text="server error")])
        result = client.search_trains("A", "B", "2030-01-01")
        assert result.ok is False

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
