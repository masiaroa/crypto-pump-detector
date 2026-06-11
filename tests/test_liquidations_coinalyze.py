"""Tests for the Coinalyze historical liquidation provider."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from pump_detector.liquidations import coinalyze as cz_mod
from pump_detector.liquidations.coinalyze import (
    coinalyze_symbol,
    fetch_coinalyze_liquidations,
    fetch_coinalyze_liquidations_batch,
    fetch_coinalyze_liquidations_with_diagnostic,
    parse_coinalyze_liquidations,
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
    """Records request order and dispenses scripted payloads per URL."""

    def __init__(self, route_payloads: dict[str, tuple[object, int]]):
        self._route_payloads = route_payloads
        self.calls: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(
            {"url": url, "params": params or {}, "headers": headers or {}, "timeout": timeout}
        )
        for route, (payload, status) in self._route_payloads.items():
            if route in url:
                return DummyResponse(payload, status_code=status)
        return DummyResponse({}, status_code=404)


def test_coinalyze_symbol_uses_market_listing_when_available():
    markets = [
        {
            "symbol": "SANDUSDT_PERP.A",
            "exchange": "Binance",
            "base_asset": "SAND",
            "quote_asset": "USDT",
            "is_perpetual": True,
        },
        {
            "symbol": "SANDUSDT.6",
            "exchange": "Bybit",
            "base_asset": "SAND",
            "quote_asset": "USDT",
            "is_perpetual": True,
        },
    ]
    assert coinalyze_symbol("BINANCE:SANDUSDT.P", markets) == "SANDUSDT_PERP.A"
    assert coinalyze_symbol("BYBIT:SANDUSDT.P", markets) == "SANDUSDT.6"


def test_coinalyze_symbol_matches_exchange_code_market_listing():
    markets = [
        {
            "symbol": "OPUSDT.6",
            "exchange": "6",
            "base_asset": "OP",
            "quote_asset": "USDT",
            "is_perpetual": True,
        }
    ]
    assert coinalyze_symbol("BYBIT:OPUSDT.P", markets) == "OPUSDT.6"


def test_coinalyze_symbol_falls_back_to_known_codes_without_markets():
    """When the markets cache is unavailable, the resolver still emits a guess
    so we can at least try the most common exchange code per venue."""
    assert coinalyze_symbol("BINANCE:SANDUSDT.P", []) == "SANDUSDT_PERP.A"
    assert coinalyze_symbol("BYBIT:SANDUSDT.P", []) == "SANDUSDT.6"
    assert coinalyze_symbol("OKX:SANDUSDT.P", []) == "SANDUSDT_PERP.3"


def test_coinalyze_symbol_rejects_unsupported_quotes():
    assert coinalyze_symbol("BINANCE:BTCBUSD.P", []) is None
    assert coinalyze_symbol("BYBIT:ETHUSDC.P", []) is None


def test_parse_coinalyze_liquidations_emits_two_rows_per_bucket():
    payload = [
        {
            "symbol": "SANDUSDT_PERP.A",
            "history": [
                {"t": 1746000000, "l": 12345.0, "s": 678.0},
                {"t": 1746003600, "l": 0.0, "s": 9999.0},
                {"t": 1746007200, "l": 0.0, "s": 0.0},
            ],
        }
    ]
    frame = parse_coinalyze_liquidations(payload)
    assert len(frame) == 3
    # First bucket -> long row + short row
    longs = frame[frame["side"] == "long"].reset_index(drop=True)
    shorts = frame[frame["side"] == "short"].reset_index(drop=True)
    assert list(longs["notional"]) == [12345.0]
    assert list(shorts["notional"]) == [678.0, 9999.0]
    # Price is NaN — the chart layer snaps to candle close.
    assert frame["price"].isna().all()
    assert (frame["kind"] == "executed").all()
    assert (frame["source"] == "coinalyze").all()


def test_parse_coinalyze_liquidations_handles_empty_or_malformed():
    assert parse_coinalyze_liquidations([]).empty
    assert parse_coinalyze_liquidations(None).empty
    assert parse_coinalyze_liquidations([{"symbol": "X", "history": []}]).empty


def test_fetch_coinalyze_returns_empty_without_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("COINALYZE_API_KEY", raising=False)
    session = DummySession({})
    frame = fetch_coinalyze_liquidations(
        "BINANCE:SANDUSDT.P", "4h", cfg={"enabled": True}, session=session
    )
    assert frame.empty
    assert session.calls == []


def test_fetch_coinalyze_diagnostic_reports_missing_key(monkeypatch):
    monkeypatch.delenv("COINALYZE_API_KEY", raising=False)
    session = DummySession({})

    frame, diagnostic = fetch_coinalyze_liquidations_with_diagnostic(
        "BINANCE:SANDUSDT.P", "4h", cfg={"enabled": True}, session=session
    )

    assert frame.empty
    assert diagnostic.status == "missing_key"
    assert diagnostic.rows == 0
    assert session.calls == []


def test_fetch_coinalyze_calls_markets_then_liquidations(monkeypatch, tmp_path):
    """End-to-end happy path: cache markets, then fetch buckets, then parse."""
    monkeypatch.setenv("COINALYZE_API_KEY", "k_test")
    # Force the markets cache to live in a tmp dir so the test is hermetic.
    monkeypatch.setattr(cz_mod, "MARKETS_CACHE", tmp_path / "markets.json")

    markets_payload = [
        {
            "symbol": "SANDUSDT_PERP.A",
            "exchange": "Binance",
            "base_asset": "SAND",
            "quote_asset": "USDT",
            "is_perpetual": True,
        }
    ]
    liq_payload = [
        {
            "symbol": "SANDUSDT_PERP.A",
            "history": [
                {"t": 1746000000, "l": 5000.0, "s": 1000.0},
                {"t": 1746003600, "l": 0.0, "s": 2500.0},
            ],
        }
    ]
    session = DummySession(
        {
            "/future-markets": (markets_payload, 200),
            "/liquidation-history": (liq_payload, 200),
        }
    )

    frame = fetch_coinalyze_liquidations(
        "BINANCE:SANDUSDT.P",
        "4h",
        cfg={"enabled": True},
        session=session,
        now_s=1746010000,
    )
    assert len(frame) == 3
    assert session.calls[0]["url"].endswith("/future-markets")
    assert session.calls[0]["headers"]["api_key"] == "k_test"
    assert session.calls[1]["url"].endswith("/liquidation-history")
    params = session.calls[1]["params"]
    assert params["symbols"] == "SANDUSDT_PERP.A"
    assert params["interval"] == "4hour"
    assert params["convert_to_usd"] == "true"
    assert int(params["to"]) == 1746010000

    # Markets payload was cached to disk so the next call should not refetch.
    session.calls.clear()
    session_2 = DummySession(
        {"/liquidation-history": (liq_payload, 200)}
    )
    fetch_coinalyze_liquidations(
        "BINANCE:SANDUSDT.P",
        "4h",
        cfg={"enabled": True},
        session=session_2,
        now_s=1746010000,
    )
    # Only the liquidation endpoint is hit on the second call.
    assert all("/liquidation-history" in c["url"] for c in session_2.calls)


def test_fetch_coinalyze_diagnostic_reports_success(monkeypatch, tmp_path):
    monkeypatch.setenv("COINALYZE_API_KEY", "k_test")
    monkeypatch.setattr(cz_mod, "MARKETS_CACHE", tmp_path / "markets.json")
    session = DummySession(
        {
            "/future-markets": (
                [
                    {
                        "symbol": "OPUSDT.6",
                        "exchange": "6",
                        "base_asset": "OP",
                        "quote_asset": "USDT",
                        "is_perpetual": True,
                    }
                ],
                200,
            ),
            "/liquidation-history": (
                [
                    {
                        "symbol": "OPUSDT.6",
                        "history": [
                            {"t": 1746000000, "l": 5000.0, "s": 1000.0},
                            {"t": 1746003600, "l": 0.0, "s": 2500.0},
                        ],
                    }
                ],
                200,
            ),
        }
    )

    frame, diagnostic = fetch_coinalyze_liquidations_with_diagnostic(
        "BYBIT:OPUSDT.P",
        "4h",
        cfg={"enabled": True},
        session=session,
        now_s=1746010000,
    )

    assert len(frame) == 3
    assert diagnostic.status == "ok"
    assert diagnostic.resolved_symbol == "OPUSDT.6"
    assert diagnostic.rows == 3
    assert diagnostic.notional == 8500.0
    assert diagnostic.first_timestamp == pd.Timestamp("2025-04-30T08:00:00Z")
    assert diagnostic.last_timestamp == pd.Timestamp("2025-04-30T09:00:00Z")


def test_fetch_coinalyze_swallows_http_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("COINALYZE_API_KEY", "k_test")
    monkeypatch.setattr(cz_mod, "MARKETS_CACHE", tmp_path / "markets.json")
    session = DummySession(
        {
            "/future-markets": ([], 200),
            "/liquidation-history": ({"message": "rate limit"}, 429),
        }
    )
    frame = fetch_coinalyze_liquidations(
        "BINANCE:SANDUSDT.P",
        "4h",
        cfg={"enabled": True},
        session=session,
        now_s=1746010000,
    )
    assert frame.empty


def test_fetch_coinalyze_diagnostic_reports_http_error(monkeypatch, tmp_path):
    monkeypatch.setenv("COINALYZE_API_KEY", "k_test")
    monkeypatch.setattr(cz_mod, "MARKETS_CACHE", tmp_path / "markets.json")
    session = DummySession(
        {
            "/future-markets": ([], 200),
            "/liquidation-history": ({"message": "rate limit"}, 429),
        }
    )

    frame, diagnostic = fetch_coinalyze_liquidations_with_diagnostic(
        "BINANCE:SANDUSDT.P",
        "4h",
        cfg={"enabled": True},
        session=session,
        now_s=1746010000,
    )

    assert frame.empty
    assert diagnostic.status == "http_error"
    assert diagnostic.http_status == 429
    assert diagnostic.resolved_symbol == "SANDUSDT_PERP.A"


def test_fetch_coinalyze_diagnostic_reports_empty_response(monkeypatch, tmp_path):
    monkeypatch.setenv("COINALYZE_API_KEY", "k_test")
    monkeypatch.setattr(cz_mod, "MARKETS_CACHE", tmp_path / "markets.json")
    session = DummySession(
        {
            "/future-markets": ([], 200),
            "/liquidation-history": ([], 200),
        }
    )

    frame, diagnostic = fetch_coinalyze_liquidations_with_diagnostic(
        "BINANCE:SANDUSDT.P",
        "4h",
        cfg={"enabled": True},
        session=session,
        now_s=1746010000,
    )

    assert frame.empty
    assert diagnostic.status == "empty"
    assert diagnostic.http_status == 200
    assert diagnostic.rows == 0


def test_fetch_coinalyze_batch_groups_symbols_into_one_request(monkeypatch, tmp_path):
    monkeypatch.setenv("COINALYZE_API_KEY", "k_test")
    monkeypatch.setattr(cz_mod, "MARKETS_CACHE", tmp_path / "markets.json")
    markets = [
        {"symbol": "SANDUSDT_PERP.A", "exchange": "Binance", "base_asset": "SAND",
         "quote_asset": "USDT", "is_perpetual": True},
        {"symbol": "ARBUSDT_PERP.A", "exchange": "Binance", "base_asset": "ARB",
         "quote_asset": "USDT", "is_perpetual": True},
    ]
    payload = [
        {"symbol": "SANDUSDT_PERP.A", "history": [{"t": 1746000000, "l": 1000.0, "s": 2000.0}]},
        {"symbol": "ARBUSDT_PERP.A", "history": [{"t": 1746000000, "l": 0.0, "s": 500.0}]},
    ]
    session = DummySession(
        {
            "/future-markets": (markets, 200),
            "/liquidation-history": (payload, 200),
        }
    )

    result = fetch_coinalyze_liquidations_batch(
        ["BINANCE:SANDUSDT.P", "BINANCE:ARBUSDT.P"],
        "4h",
        cfg={"enabled": True},
        session=session,
        now_s=1746010000,
    )

    liq_calls = [c for c in session.calls if "/liquidation-history" in c["url"]]
    assert len(liq_calls) == 1  # both symbols comma-joined into one request
    assert set(liq_calls[0]["params"]["symbols"].split(",")) == {"SANDUSDT_PERP.A", "ARBUSDT_PERP.A"}
    assert set(result) == {"BINANCE:SANDUSDT.P", "BINANCE:ARBUSDT.P"}
    assert float(result["BINANCE:SANDUSDT.P"]["notional"].sum()) == 3000.0
    short_rows = result["BINANCE:ARBUSDT.P"]
    assert (short_rows["side"] == "short").all()


def test_fetch_coinalyze_batch_without_key_returns_empty(monkeypatch):
    monkeypatch.delenv("COINALYZE_API_KEY", raising=False)
    assert fetch_coinalyze_liquidations_batch(["BINANCE:SANDUSDT.P"], "4h", cfg={"enabled": True}) == {}
