from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import ROOT, Settings, load_settings, load_watchlist
from .data_clients import DataUnavailable, fetch_market_data
from .signals import SignalSnapshot, compute_indicators, evaluate_latest, mark_signal_history
from .storage import append_snapshots
from .symbols import normalize_symbol


EIGHT_MONTH_DAILY_CANDLES = 244
_DEFAULT_TIMEFRAME_LIMITS = {"1d": EIGHT_MONTH_DAILY_CANDLES, "4h": 528}
_LEGACY_DEFAULT_LIMIT = 260


def _limit_for_timeframe(timeframe: str, explicit_limit: int | None = None) -> int:
    if explicit_limit is not None:
        return explicit_limit
    return _DEFAULT_TIMEFRAME_LIMITS.get(timeframe, _LEGACY_DEFAULT_LIMIT)


def scan_watchlist(
    symbols: list[str] | None = None,
    settings: Settings | None = None,
    persist: bool = False,
    limit: int | None = None,
) -> tuple[pd.DataFrame, dict[tuple[str, str], pd.DataFrame]]:
    settings = settings or load_settings()
    symbols = symbols or load_watchlist()
    snapshots: list[SignalSnapshot] = []
    details: dict[tuple[str, str], pd.DataFrame] = {}

    total = len(symbols)
    print(f"[scan] {total} symbols × {len(settings.timeframes)} timeframes", flush=True)

    for sym_idx, raw_symbol in enumerate(symbols, 1):
        market = normalize_symbol(raw_symbol)
        ticker = raw_symbol.split(":")[-1].replace(".P", "")
        print(f"[scan] [{sym_idx}/{total}] {ticker}", flush=True)
        if not market.supported:
            for timeframe in settings.timeframes:
                snapshots.append(_unsupported_snapshot(raw_symbol, market.exchange, timeframe))
            continue
        for timeframe in settings.timeframes:
            try:
                data = fetch_market_data(raw_symbol, timeframe, limit=_limit_for_timeframe(timeframe, limit))
                indicators = compute_indicators(data.candles, lookback_stats=int(settings.thresholds["lookback_stats"]))
                marked = mark_signal_history(
                    indicators,
                    timeframe=timeframe,
                    lookback_no_previous_signal=int(settings.thresholds["lookback_no_previous_signal"]),
                    price_zscore_threshold=float(settings.thresholds["price_zscore_threshold"]),
                    oi_zscore_threshold=float(settings.thresholds["oi_zscore_threshold"]),
                    close_position_min=float(settings.thresholds["close_position_min"]),
                    max_recent_price_run_pct=float(settings.thresholds["max_recent_price_run_pct"]),
                    max_consecutive_oi_expansion=int(settings.thresholds["max_consecutive_oi_expansion"]),
                    oi_surge_3bar_pct=float(settings.thresholds.get("oi_surge_3bar_pct", 0.04)),
                    volume_surge_3bar_ratio=float(settings.thresholds.get("volume_surge_3bar_ratio", 2.5)),
                )
                snapshot = evaluate_latest(
                    marked,
                    timeframe=timeframe,
                    symbol=raw_symbol,
                    exchange=market.exchange,
                    lookback_no_previous_signal=int(settings.thresholds["lookback_no_previous_signal"]),
                    price_zscore_threshold=float(settings.thresholds["price_zscore_threshold"]),
                    oi_zscore_threshold=float(settings.thresholds["oi_zscore_threshold"]),
                    close_position_min=float(settings.thresholds["close_position_min"]),
                    volume_zscore_threshold=float(settings.thresholds["volume_zscore_threshold"]),
                    max_recent_price_run_pct=float(settings.thresholds["max_recent_price_run_pct"]),
                    max_consecutive_oi_expansion=int(settings.thresholds["max_consecutive_oi_expansion"]),
                    allowed_funding_classes=list(settings.alert_conditions["allowed_funding_classes"]),
                    require_volume_confirmation=bool(settings.alert_conditions["require_volume_confirmation"]),
                    require_breakout_20=bool(settings.alert_conditions["require_breakout_20"]),
                    require_sma200_reclaim=bool(settings.alert_conditions["require_sma200_reclaim"]),
                    oi_surge_3bar_pct=float(settings.thresholds.get("oi_surge_3bar_pct", 0.04)),
                    volume_surge_3bar_ratio=float(settings.thresholds.get("volume_surge_3bar_ratio", 2.5)),
                    notes=data.notes,
                )
                snapshots.append(snapshot)
                details[(raw_symbol, timeframe)] = marked
            except Exception as exc:  # noqa: BLE001 - any failure (DataUnavailable, KeyError, etc.) must not crash the full scan
                snapshots.append(_error_snapshot(raw_symbol, market.exchange, timeframe, str(exc)))

    if persist:
        append_snapshots(
            snapshots,
            ROOT / settings.storage["sqlite_path"],
            ROOT / settings.storage["alerts_csv"],
        )
    return pd.DataFrame([snapshot.to_dict() for snapshot in snapshots]), details


def _unsupported_snapshot(symbol: str, exchange: str, timeframe: str) -> SignalSnapshot:
    return _blank_snapshot(symbol, exchange, timeframe, "unsupported symbol or exchange")


def _error_snapshot(symbol: str, exchange: str, timeframe: str, note: str) -> SignalSnapshot:
    return _blank_snapshot(symbol, exchange, timeframe, note)


def _blank_snapshot(symbol: str, exchange: str, timeframe: str, note: str) -> SignalSnapshot:
    return SignalSnapshot(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        timestamp=pd.Timestamp.utcnow(),
        close=0.0,
        price_return_pct=0.0,
        price_return_zscore=0.0,
        oi=0.0,
        oi_change_pct=0.0,
        oi_change_zscore=0.0,
        oi_3bar_change_pct=0.0,
        volume_zscore=0.0,
        volume_3bar_ratio=0.0,
        oi_surge_flag=False,
        volume_surge_flag=False,
        funding_rate=0.0,
        funding_classification="UNKNOWN",
        funding_percentile_90d=0.0,
        sma200=0.0,
        distance_to_sma200_pct=0.0,
        breakout_10_flag=False,
        breakout_20_flag=False,
        sma200_reclaim_flag=False,
        close_near_high=False,
        price_impulse=False,
        oi_impulse=False,
        first_impulse=False,
        early_bullish_score=0.0,
        blowoff_risk_score=0.0,
        signal_active=False,
        alert_triggered=False,
        last_signal_time="",
        notes=note,
    )


def scan_to_csv(output_path: Path, persist: bool = True) -> pd.DataFrame:
    df, _ = scan_watchlist(persist=persist)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    return df
