from __future__ import annotations

from pump_detector.positioning import (
    LongShortRatio,
    _to_float,
    fetch_long_short_ratio,
)


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params or {}})
        if not self.responses:
            return _Resp({}, status_code=500)
        return self.responses.pop(0)


def test_fetch_long_short_ratio_returns_binance_account_split():
    session = _Session([
        _Resp([
            {
                "symbol": "BTCUSDT",
                "longShortRatio": "1.5",
                "longAccount": "0.6",
                "shortAccount": "0.4",
                "timestamp": 1700000000000,
            }
        ])
    ])

    ratio = fetch_long_short_ratio("BINANCE:BTCUSDT.P", session=session)

    assert ratio.long_pct == 0.6
    assert ratio.short_pct == 0.4
    assert ratio.source == "binance"
    assert session.calls[0]["url"].endswith("/topLongShortAccountRatio")
    assert session.calls[0]["params"]["symbol"] == "BTCUSDT"


def test_fetch_long_short_ratio_uses_bybit_for_bybit_symbol():
    session = _Session([
        _Resp({
            "result": {
                "list": [
                    {"symbol": "ETHUSDT", "buyRatio": "0.55", "sellRatio": "0.45", "timestamp": "1700000000000"}
                ]
            }
        })
    ])

    ratio = fetch_long_short_ratio("BYBIT:ETHUSDT.P", session=session)

    assert ratio.long_pct == 0.55
    assert ratio.short_pct == 0.45
    assert ratio.source == "bybit"
    assert "bybit.com" in session.calls[0]["url"]


def test_fetch_long_short_ratio_falls_back_to_binance_when_bybit_empty():
    session = _Session([
        _Resp({"result": {"list": []}}),
        _Resp([
            {"longAccount": "0.7", "shortAccount": "0.3"}
        ]),
    ])

    ratio = fetch_long_short_ratio("BYBIT:ADAUSDT.P", session=session)

    assert ratio.long_pct == 0.7
    assert ratio.short_pct == 0.3
    assert ratio.source == "binance"


def test_fetch_long_short_ratio_okx_converts_ratio_to_pct():
    session = _Session([
        _Resp({"data": [["1700000000", "1.5"]]})
    ])

    ratio = fetch_long_short_ratio("OKX:MANAUSDT.P", session=session)

    assert ratio.source == "okx"
    assert abs(ratio.long_pct - 0.6) < 1e-9
    assert abs(ratio.short_pct - 0.4) < 1e-9


def test_fetch_long_short_ratio_returns_empty_when_all_providers_fail():
    session = _Session([_Resp({}, status_code=500)] * 4)

    ratio = fetch_long_short_ratio("BYBIT:XYZUSDT.P", session=session)

    assert ratio == LongShortRatio(long_pct=0.0, short_pct=0.0, source="")


def test_to_float_tolerates_garbage():
    assert _to_float("0.5") == 0.5
    assert _to_float(None) == 0.0
    assert _to_float("") == 0.0
    assert _to_float("nope") == 0.0
