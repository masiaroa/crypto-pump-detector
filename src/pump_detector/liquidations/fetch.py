from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import requests

from ..config import ROOT
from .coinalyze import (
    CoinalyzeDiagnostic,
    fetch_coinalyze_liquidations_with_diagnostic,
)
from .executed_store import (
    parse_binance_force_orders,
    read_recent,
    resolve_binance_force_order_market,
)
from .projected_coinglass import fetch_projected_heatmap, parse_coinglass_projected
from .schema import empty_liquidations, lookback_ms


DEFAULT_HISTORY_FILE = "data/liquidations/_ws_history.jsonl"


def _resolve_history_path(cfg: dict[str, Any]) -> Path:
    raw = cfg.get("history_file") or DEFAULT_HISTORY_FILE
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


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

    executed_cfg = dict(cfg.get("executed", {}))
    projected_cfg = dict(cfg.get("projected", {}))
    coinalyze_cfg = dict(cfg.get("coinalyze", {}))

    frames: list[pd.DataFrame] = []
    diagnostics: list[CoinalyzeDiagnostic] = []
    if executed_cfg.get("enabled", True):
        history_path = _resolve_history_path(executed_cfg)
        frames.append(read_recent(history_path, raw_symbol, timeframe, now_ms=now_ms))
    if projected_cfg.get("enabled", True):
        frames.append(
            fetch_projected_heatmap(raw_symbol, timeframe, projected_cfg, session=session)
        )
    if coinalyze_cfg.get("enabled", True):
        now_s = int(now_ms // 1000) if now_ms is not None else None
        frame, diagnostic = fetch_coinalyze_liquidations_with_diagnostic(
            raw_symbol, timeframe, coinalyze_cfg, session=session, now_s=now_s
        )
        frames.append(frame)
        diagnostics.append(diagnostic)

    frames = [frame for frame in frames if frame is not None and not frame.empty]
    if not frames:
        return empty_liquidations(), diagnostics
    return (
        pd.concat(frames, ignore_index=True)
        .sort_values(["kind", "timestamp", "price"])
        .reset_index(drop=True)
    ), diagnostics


__all__ = [
    "fetch_liquidation_map",
    "fetch_liquidation_report",
    "parse_binance_force_orders",
    "parse_coinglass_projected",
    "resolve_binance_force_order_market",
    "lookback_ms",
    "empty_liquidations",
]
