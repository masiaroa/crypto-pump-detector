"""Tests for the cross-exchange aggregated funding rate."""
from __future__ import annotations

import pandas as pd
import pytest

from pump_detector import data_clients
from pump_detector.symbols import normalize_symbol


def _funding_feed(timestamps: list[str], rates: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.to_datetime(timestamps, utc=True),
        "funding_rate": rates,
    })


def _candles(timestamps: list[str], rates: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.to_datetime(timestamps, utc=True),
        "open": [1.0] * len(timestamps),
        "high": [1.0] * len(timestamps),
        "low": [1.0] * len(timestamps),
        "close": [1.0] * len(timestamps),
        "volume": [1.0] * len(timestamps),
        "funding_rate": rates,
    })


def test_aggregated_funding_replaces_with_mean_across_three_exchanges(monkeypatch):
    market = normalize_symbol("BYBIT:ETHUSDT.P")
    candles = _candles(
        ["2026-05-12 00:00:00", "2026-05-12 04:00:00", "2026-05-12 08:00:00"],
        [0.0001, 0.0001, 0.0001],  # primary venue funding (ignored after agg)
    )

    monkeypatch.setattr(data_clients, "_fetch_funding_history_bybit",
                        lambda s: _funding_feed(
                            ["2026-05-12 00:00:00", "2026-05-12 08:00:00"],
                            [0.0001, 0.0003],
                        ))
    monkeypatch.setattr(data_clients, "_fetch_funding_history_binance",
                        lambda s: _funding_feed(
                            ["2026-05-12 00:00:00", "2026-05-12 08:00:00"],
                            [0.0003, 0.0005],
                        ))
    monkeypatch.setattr(data_clients, "_fetch_funding_history_bitget",
                        lambda s: _funding_feed(
                            ["2026-05-12 00:00:00", "2026-05-12 08:00:00"],
                            [0.0005, 0.0007],
                        ))

    out = data_clients._replace_with_aggregated_funding(market, candles, primary="BYBIT")

    # 00:00 → mean(0.0001, 0.0003, 0.0005) = 0.0003
    # 04:00 → forward-fill same values → 0.0003
    # 08:00 → mean(0.0003, 0.0005, 0.0007) = 0.0005
    expected = [0.0003, 0.0003, 0.0005]
    assert out["funding_rate"].tolist() == pytest.approx(expected, abs=1e-9)


def test_aggregated_funding_keeps_primary_when_only_one_exchange_responds(monkeypatch):
    market = normalize_symbol("BYBIT:NEWCOINUSDT.P")
    candles = _candles(
        ["2026-05-12 00:00:00", "2026-05-12 04:00:00"],
        [0.0002, 0.0002],
    )

    # Only Bybit returns data — niche coin not listed on Binance/Bitget
    monkeypatch.setattr(data_clients, "_fetch_funding_history_bybit",
                        lambda s: _funding_feed(["2026-05-12 00:00:00"], [0.0009]))
    monkeypatch.setattr(data_clients, "_fetch_funding_history_binance",
                        lambda s: data_clients._EMPTY_FUNDING.copy())
    monkeypatch.setattr(data_clients, "_fetch_funding_history_bitget",
                        lambda s: data_clients._EMPTY_FUNDING.copy())

    out = data_clients._replace_with_aggregated_funding(market, candles, primary="BYBIT")

    # < 2 venues with data → keep primary funding untouched
    assert out["funding_rate"].tolist() == [0.0002, 0.0002]


def test_aggregated_funding_handles_partial_history(monkeypatch):
    """Bitget only started listing the coin mid-window — earlier candles must
    average the venues that actually had data at that point."""
    market = normalize_symbol("BYBIT:ETHUSDT.P")
    candles = _candles(
        ["2026-05-12 00:00:00", "2026-05-12 04:00:00", "2026-05-12 08:00:00"],
        [0.0001, 0.0001, 0.0001],
    )

    monkeypatch.setattr(data_clients, "_fetch_funding_history_bybit",
                        lambda s: _funding_feed(["2026-05-12 00:00:00"], [0.0002]))
    monkeypatch.setattr(data_clients, "_fetch_funding_history_binance",
                        lambda s: _funding_feed(["2026-05-12 00:00:00"], [0.0004]))
    # Bitget only fires at 08:00 — earlier candles should average only Bybit+Binance
    monkeypatch.setattr(data_clients, "_fetch_funding_history_bitget",
                        lambda s: _funding_feed(["2026-05-12 08:00:00"], [0.0009]))

    out = data_clients._replace_with_aggregated_funding(market, candles, primary="BYBIT")

    # 00:00 + 04:00 → mean(0.0002, 0.0004) = 0.0003 (Bitget absent yet)
    # 08:00 → mean(0.0002, 0.0004, 0.0009) = 0.0005
    expected = [0.0003, 0.0003, 0.0005]
    assert out["funding_rate"].tolist() == pytest.approx(expected, abs=1e-9)


def test_aggregated_funding_skips_non_usdt_quotes():
    """Coin-margined USD perps don't share a symbol with USDT venues — skip."""
    market = normalize_symbol("BINANCE:BTCUSD_PERP")
    candles = _candles(["2026-05-12 00:00:00"], [0.0002])

    out = data_clients._replace_with_aggregated_funding(market, candles, primary="BINANCE")

    assert out["funding_rate"].tolist() == [0.0002]
