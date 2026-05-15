from __future__ import annotations

from typing import Any

import pandas as pd
import requests

from .coinalyze import (
    CoinalyzeDiagnostic,
    fetch_coinalyze_liquidations_with_diagnostic,
)
from .schema import empty_liquidations, lookback_ms


def fetch_liquidation_map(
    raw_symbol: str,
    timeframe: str,
    *,
    settings: dict[str, Any] | None = None,
    session: requests.Session | None = None,
    now_ms: int | None = None,
) -> pd.DataFrame:
    frame, _diagnostics = fetch_liquidation_report(
        raw_symbol,
        timeframe,
        settings=settings,
        session=session,
        now_ms=now_ms,
    )
    return frame


def fetch_liquidation_report(
    raw_symbol: str,
    timeframe: str,
    *,
    settings: dict[str, Any] | None = None,
    session: requests.Session | None = None,
    now_ms: int | None = None,
) -> tuple[pd.DataFrame, list[CoinalyzeDiagnostic]]:
    cfg = settings or {}
    if cfg and not bool(cfg.get("enabled", True)):
        return empty_liquidations(), []
    coinalyze_cfg = dict(cfg.get("coinalyze", {}))
    if not coinalyze_cfg.get("enabled", True):
        return empty_liquidations(), []
    now_s = int(now_ms // 1000) if now_ms is not None else None
    frame, diagnostic = fetch_coinalyze_liquidations_with_diagnostic(
        raw_symbol, timeframe, coinalyze_cfg, session=session, now_s=now_s
    )
    if frame.empty:
        return empty_liquidations(), [diagnostic]
    return (
        frame.sort_values(["timestamp", "side"]).reset_index(drop=True),
        [diagnostic],
    )


__all__ = [
    "fetch_liquidation_map",
    "fetch_liquidation_report",
    "lookback_ms",
    "empty_liquidations",
]
