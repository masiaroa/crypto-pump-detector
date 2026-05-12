from __future__ import annotations

from typing import Any

import pandas as pd


LIQUIDATION_COLUMNS = [
    "timestamp",
    "price",
    "quantity",
    "notional",
    "side",
    "kind",
    "source",
]


def empty_liquidations() -> pd.DataFrame:
    return pd.DataFrame(columns=LIQUIDATION_COLUMNS)


def to_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def lookback_ms(timeframe: str) -> int:
    days = {"1h": 1, "4h": 3, "1d": 7}.get(timeframe, 3)
    return days * 24 * 60 * 60 * 1000
