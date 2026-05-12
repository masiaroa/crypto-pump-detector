from __future__ import annotations

from pump_detector.liquidations.projected_coinglass import (
    FRONTEND_ENDPOINT,
    OFFICIAL_ENDPOINT,
    fetch_projected_heatmap,
)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def json(self):
        return self._payload


class _Session:
    """Returns responses from a queue keyed by URL prefix."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params or {}, "headers": headers or {}})
        return self._responses.pop(0)


def test_frontend_endpoint_used_when_no_api_key(monkeypatch):
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    payload = {
        "data": {
            "heatmap": [
                {"x": 1710000000000, "price": "62000", "notional": "900000", "side": "long"}
            ]
        }
    }
    session = _Session([_Response(payload, 200)])

    frame = fetch_projected_heatmap("BINANCE:BTCUSDT.P", "4h", {}, session=session)

    assert len(session.calls) == 1
    assert session.calls[0]["url"] == FRONTEND_ENDPOINT
    assert session.calls[0]["headers"]["Referer"] == "https://www.coinglass.com/"
    assert len(frame) == 1
    assert frame.iloc[0]["kind"] == "projected"
    assert frame.iloc[0]["source"] == "coinglass_frontend"


def test_official_endpoint_used_first_when_key_present(monkeypatch):
    monkeypatch.setenv("COINGLASS_API_KEY", "abc")
    payload = {
        "data": {
            "points": [
                {"x": 1710000000000, "price": "62000", "notional": "900000", "side": "short"}
            ]
        }
    }
    session = _Session([_Response(payload, 200)])

    frame = fetch_projected_heatmap("BINANCE:BTCUSDT.P", "4h", {}, session=session)

    assert session.calls[0]["url"] == OFFICIAL_ENDPOINT
    assert session.calls[0]["headers"]["CG-API-KEY"] == "abc"
    assert frame.iloc[0]["source"] == "coinglass"


def test_frontend_paywall_response_returns_empty(monkeypatch):
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    session = _Session([_Response({"msg": "plan required"}, 403)])

    frame = fetch_projected_heatmap("BYBIT:BTCUSDT.P", "1h", {}, session=session)

    assert frame.empty
    assert session.calls[0]["url"] == FRONTEND_ENDPOINT


def test_frontend_disabled_when_flag_off(monkeypatch):
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    session = _Session([])  # would fail if called

    frame = fetch_projected_heatmap(
        "BYBIT:BTCUSDT.P",
        "1h",
        {"use_frontend_endpoint": False},
        session=session,
    )

    assert frame.empty
    assert session.calls == []
