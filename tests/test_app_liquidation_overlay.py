from __future__ import annotations

import pandas as pd
from plotly.subplots import make_subplots

from app import _add_liquidation_overlay, _liquidation_side_summary


def test_add_liquidation_overlay_snaps_coinalyze_rows_with_mixed_datetime_resolution():
    candles = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2026-05-12T04:00:00Z", "2026-05-12T08:00:00Z"],
                utc=True,
            ).astype("datetime64[ms, UTC]"),
            "close": [0.72, 0.75],
            "low": [0.70, 0.73],
            "high": [0.76, 0.78],
        }
    )
    liquidations = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-05-12T08:00:00Z"], utc=True).astype(
                "datetime64[s, UTC]"
            ),
            "price": [float("nan")],
            "quantity": [0.0],
            "notional": [1234.0],
            "side": ["short"],
            "kind": ["executed"],
            "source": ["coinalyze"],
        }
    )
    fig = make_subplots(rows=1, cols=1)

    _add_liquidation_overlay(fig, candles, liquidations)

    assert len(fig.data) == 1
    assert list(fig.data[0].y) == [0.75]


def test_liquidation_side_summary_compares_long_and_short_notional():
    liquidations = pd.DataFrame(
        {
            "side": ["long", "short", "short", "unknown"],
            "notional": [100.0, 75.0, 25.0, 999.0],
            "source": ["coinalyze", "coinalyze", "coinalyze", "coinalyze"],
        }
    )

    summary, winner, ratio = _liquidation_side_summary(liquidations)

    assert list(summary["Lado"]) == ["Longs liquidados", "Shorts liquidados"]
    assert list(summary["Nocional"]) == ["$100", "$100"]
    assert list(summary["%"]) == ["50.0%", "50.0%"]
    assert winner == "Empate"
    assert ratio == "1.00x"


def test_liquidation_side_summary_filters_to_loaded_candle_range():
    candles = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2026-05-12T04:00:00Z", "2026-05-12T08:00:00Z"],
                utc=True,
            )
        }
    )
    liquidations = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-05-12T00:00:00Z",
                    "2026-05-12T04:00:00Z",
                    "2026-05-12T08:00:00Z",
                    "2026-05-12T12:00:00Z",
                ],
                utc=True,
            ),
            "side": ["long", "long", "short", "short"],
            "notional": [900.0, 100.0, 50.0, 800.0],
            "source": ["coinalyze", "coinalyze", "coinalyze", "coinalyze"],
        }
    )

    summary, winner, ratio = _liquidation_side_summary(liquidations, candles)

    assert list(summary["Nocional"]) == ["$100", "$50"]
    assert list(summary["%"]) == ["66.7%", "33.3%"]
    assert winner == "Longs mas liquidados"
    assert ratio == "2.00x"
