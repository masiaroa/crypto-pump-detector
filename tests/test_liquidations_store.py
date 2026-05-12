from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from pump_detector.liquidations.executed_store import (
    append_records,
    canonical_symbols,
    per_exchange_symbols,
    prune,
    read_recent,
)


def test_canonical_symbols_covers_exchange_representations():
    forms = canonical_symbols("BINANCE:BTCUSDT.P")
    assert "BTCUSDT" in forms
    assert "BTC-USDT-SWAP" in forms
    assert "BINANCE:BTCUSDT.P" in forms


def test_per_exchange_symbols_usdt_perp():
    forms = per_exchange_symbols("BYBIT:SANDUSDT.P")
    assert forms == {
        "binance": "SANDUSDT",
        "bybit": "SANDUSDT",
        "okx": "SAND-USDT-SWAP",
    }


def test_per_exchange_symbols_coinm_perp():
    forms = per_exchange_symbols("BINANCE:BCHUSD.P")
    assert forms == {"binance_coinm": "BCHUSD_PERP"}


def test_per_exchange_symbols_skips_unsupported():
    assert per_exchange_symbols("CRYPTOCAP:TOTAL3") == {}


def test_append_then_read_recent_filters_by_symbol_and_window(tmp_path):
    path = tmp_path / "_ws_history.jsonl"
    now_ms = 1710003600000

    records = [
        {
            "timestamp_ms": now_ms - 1000,
            "price": 61000.0,
            "quantity": 0.1,
            "notional": 6100.0,
            "side": "long",
            "kind": "executed",
            "source": "binance_ws",
            "symbol": "BTCUSDT",
        },
        {
            # different symbol, must be filtered out
            "timestamp_ms": now_ms - 2000,
            "price": 1.0,
            "quantity": 1.0,
            "notional": 1.0,
            "side": "short",
            "kind": "executed",
            "source": "bybit_ws",
            "symbol": "DOGEUSDT",
        },
        {
            # too old for a 1h timeframe lookback (1 day window)
            "timestamp_ms": now_ms - 5 * 24 * 60 * 60 * 1000,
            "price": 60000.0,
            "quantity": 0.5,
            "notional": 30000.0,
            "side": "short",
            "kind": "executed",
            "source": "okx_ws",
            "symbol": "BTC-USDT-SWAP",
        },
    ]
    assert append_records(path, records) == 3

    frame = read_recent(path, "BINANCE:BTCUSDT.P", "1h", now_ms=now_ms)
    assert len(frame) == 1
    assert frame.iloc[0]["price"] == 61000.0
    assert frame.iloc[0]["side"] == "long"
    assert frame.iloc[0]["kind"] == "executed"


def test_read_recent_returns_empty_when_file_missing(tmp_path):
    frame = read_recent(tmp_path / "nope.jsonl", "BINANCE:BTCUSDT.P", "4h")
    assert frame.empty


def test_prune_drops_old_lines(tmp_path):
    path = tmp_path / "_ws_history.jsonl"
    now_ms = 1710003600000
    old = now_ms - 30 * 24 * 60 * 60 * 1000
    fresh = now_ms - 60 * 1000
    path.write_text(
        "\n".join(
            [
                json.dumps({"timestamp_ms": old, "price": 1, "quantity": 1, "side": "long", "symbol": "X"}),
                json.dumps({"timestamp_ms": fresh, "price": 2, "quantity": 2, "side": "short", "symbol": "Y"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    kept = prune(path, max_age_days=14, now_ms=now_ms)
    assert kept == 1
    remaining = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(remaining) == 1
    assert remaining[0]["symbol"] == "Y"
