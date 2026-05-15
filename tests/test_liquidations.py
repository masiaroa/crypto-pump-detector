from __future__ import annotations

import pandas as pd

from pump_detector.liquidations import (
    empty_liquidations,
    fetch_liquidation_map,
    fetch_liquidation_report,
    parse_coinalyze_liquidations,
)


def test_empty_liquidations_has_canonical_columns():
    frame = empty_liquidations()
    assert list(frame.columns) == [
        "timestamp",
        "price",
        "quantity",
        "notional",
        "side",
        "kind",
        "source",
    ]


def test_fetch_liquidation_map_returns_empty_when_coinalyze_disabled():
    settings = {"enabled": True, "coinalyze": {"enabled": False}}
    frame = fetch_liquidation_map(
        "BINANCE:BTCUSDT.P", "4h", settings=settings, now_ms=1710003600000
    )
    assert frame.empty


def test_fetch_liquidation_report_returns_coinalyze_diagnostics(monkeypatch):
    monkeypatch.delenv("COINALYZE_API_KEY", raising=False)
    settings = {"enabled": True, "coinalyze": {"enabled": True}}

    frame, diagnostics = fetch_liquidation_report(
        "BINANCE:BTCUSDT.P", "4h", settings=settings, now_ms=1710003600000
    )

    assert frame.empty
    assert len(diagnostics) == 1
    assert diagnostics[0].provider == "coinalyze"
    assert diagnostics[0].status == "missing_key"


def test_parse_coinalyze_liquidations_emits_one_row_per_side():
    payload = [
        {
            "symbol": "BTCUSDT_PERP.A",
            "history": [
                {"t": 1710000000, "l": 12000.0, "s": 9000.0},
                {"t": 1710003600, "l": 0.0, "s": 5500.0},
            ],
        }
    ]
    frame = parse_coinalyze_liquidations(payload)

    # First bucket → 2 rows (long + short); second bucket → 1 row (short only).
    assert len(frame) == 3
    assert set(frame["side"]) == {"long", "short"}
    assert frame.iloc[0]["timestamp"] == pd.Timestamp("2024-03-09T16:00:00Z")
    assert frame["notional"].sum() == pd.Series([12000.0, 9000.0, 5500.0]).sum()
    assert (frame["source"] == "coinalyze").all()
    assert (frame["kind"] == "executed").all()
