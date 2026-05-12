from __future__ import annotations

import pandas as pd

from pump_detector.liquidations import (
    fetch_liquidation_map,
    fetch_liquidation_report,
    parse_coinglass_projected,
    parse_binance_force_orders,
    resolve_binance_force_order_market,
)


class DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def json(self):
        return self._payload


class DummySession:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params or {}, "headers": headers or {}, "timeout": timeout})
        return DummyResponse(self.payload, self.status_code)


def test_resolve_binance_force_order_market_for_usdm_and_coinm():
    assert resolve_binance_force_order_market("BINANCE:BTCUSDT.P") == ("usdm", "BTCUSDT")
    assert resolve_binance_force_order_market("BINANCE:BCHUSD.P") == ("coinm", "BCHUSD_PERP")
    assert resolve_binance_force_order_market("BYBIT:BTCUSDT.P") == ("usdm", "BTCUSDT")
    assert resolve_binance_force_order_market("CRYPTOCAP:TOTAL3") is None


def test_parse_binance_force_orders_normalizes_rows():
    rows = parse_binance_force_orders(
        [
            {
                "symbol": "BTCUSDT",
                "side": "SELL",
                "averagePrice": "62000.5",
                "executedQty": "0.25",
                "time": 1710000000000,
            }
        ]
    )

    assert rows.to_dict("records") == [
        {
            "timestamp": pd.Timestamp("2024-03-09T16:00:00Z"),
            "price": 62000.5,
            "quantity": 0.25,
            "notional": 15500.125,
            "side": "long",
            "kind": "executed",
            "source": "binance",
        }
    ]


def test_fetch_liquidation_map_reads_jsonl_history_when_no_coinglass_key(monkeypatch, tmp_path):
    """Without a COINGLASS_API_KEY, executed comes from the JSONL store and the
    frontend fallback is attempted (returns nothing → empty projected)."""
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    monkeypatch.delenv("COINALYZE_API_KEY", raising=False)

    history = tmp_path / "_ws_history.jsonl"
    history.write_text(
        '{"timestamp_ms":1710000000000,"price":61000,"quantity":0.1,"notional":6100,'
        '"side":"short","kind":"executed","source":"binance_ws","symbol":"BTCUSDT"}\n',
        encoding="utf-8",
    )

    settings = {
        "enabled": True,
        "executed": {
            "enabled": True,
            "history_file": str(history),
        },
        "projected": {"enabled": False},
    }

    frame = fetch_liquidation_map(
        "BINANCE:BTCUSDT.P", "4h", settings=settings, now_ms=1710003600000
    )

    assert list(frame["kind"].unique()) == ["executed"]
    assert frame.iloc[0]["side"] == "short"
    assert frame.iloc[0]["source"] == "binance_ws"


def test_fetch_liquidation_report_returns_coinalyze_diagnostics(monkeypatch, tmp_path):
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    monkeypatch.delenv("COINALYZE_API_KEY", raising=False)

    settings = {
        "enabled": True,
        "executed": {"enabled": False},
        "projected": {"enabled": False},
        "coinalyze": {"enabled": True},
    }

    frame, diagnostics = fetch_liquidation_report(
        "BINANCE:BTCUSDT.P", "4h", settings=settings, now_ms=1710003600000
    )

    assert frame.empty
    assert len(diagnostics) == 1
    assert diagnostics[0].provider == "coinalyze"
    assert diagnostics[0].status == "missing_key"


def test_parse_coinglass_projected_normalizes_heatmap_points():
    frame = parse_coinglass_projected(
        {
            "data": {
                "points": [
                    {"x": 1710000000000, "price": "62000", "notional": "900000", "side": "short"}
                ]
            }
        }
    )

    assert frame.to_dict("records") == [
        {
            "timestamp": pd.Timestamp("2024-03-09T16:00:00Z"),
            "price": 62000.0,
            "quantity": 0.0,
            "notional": 900000.0,
            "side": "short",
            "kind": "projected",
            "source": "coinglass",
        }
    ]


def test_fetch_liquidation_map_falls_back_to_frontend_when_official_fails(monkeypatch, tmp_path):
    """With a CoinGlass key, the official endpoint is tried first. On failure
    the public frontend endpoint is attempted; if it also fails the layer is
    empty but the scan does not raise."""
    monkeypatch.setenv("COINGLASS_API_KEY", "free-test-key")
    monkeypatch.delenv("COINALYZE_API_KEY", raising=False)
    session = DummySession({"code": "403", "msg": "plan required"}, status_code=403)

    settings = {
        "enabled": True,
        "executed": {
            "enabled": True,
            "history_file": str(tmp_path / "missing.jsonl"),
        },
        "projected": {"enabled": True, "use_frontend_endpoint": True},
        "coinalyze": {"enabled": False},
    }

    frame = fetch_liquidation_map(
        "BYBIT:BTCUSDT.P", "4h", settings=settings, session=session, now_ms=1710003600000
    )

    assert frame.empty
    assert session.calls[0]["url"].endswith("/api/futures/liquidation/aggregated-heatmap/model2")
    assert session.calls[0]["headers"]["CG-API-KEY"] == "free-test-key"
    assert session.calls[1]["url"].endswith("/api/futures/liquidation/aggregated-heatmap")
    assert "User-Agent" in session.calls[1]["headers"]
