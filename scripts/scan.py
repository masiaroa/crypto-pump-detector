from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from pump_detector.config import ROOT, load_settings, load_watchlist
from pump_detector.liquidations import fetch_liquidation_map
from pump_detector.positioning import fetch_long_short_history, fetch_long_short_ratio
from pump_detector.scanner import scan_watchlist
from pump_detector.squeeze import latest_score_with_ls, ls_history_falling


def _sanitize_key(symbol: str, timeframe: str) -> str:
    """'BYBIT:BTCUSDT.P' + '1d' → 'BYBIT_BTCUSDT_P_1d'"""
    return symbol.replace(":", "_").replace(".", "_") + f"_{timeframe}"


def _enrich_with_long_short_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """Add long_account_ratio / short_account_ratio columns to the scan frame.

    Fetched once per raw_symbol and broadcast to all timeframes — the ratio
    moves on the scale of hours so the per-timeframe granularity is moot for
    the dashboard's header chip.
    """
    if df.empty or "symbol" not in df.columns:
        return df
    cache: dict[str, tuple[float, float]] = {}
    longs: list[float] = []
    shorts: list[float] = []
    for raw_symbol in df["symbol"].astype(str):
        if raw_symbol not in cache:
            ratio = fetch_long_short_ratio(raw_symbol)
            cache[raw_symbol] = (ratio.long_pct, ratio.short_pct)
        long_pct, short_pct = cache[raw_symbol]
        longs.append(long_pct)
        shorts.append(short_pct)
    df = df.copy()
    df["long_account_ratio"] = longs
    df["short_account_ratio"] = shorts
    return df


def _enrich_with_squeeze(df: pd.DataFrame, ls_map: dict, settings) -> pd.DataFrame:
    """Fold the long/short crowding component into the latest squeeze score.

    The per-candle score from the scanner only covers candle-native
    components (OI build, stop clusters, compression, funding); the L/S
    ratio is fetched separately per symbol so it gets folded in here,
    before latest_scan.csv is written.
    """
    if df.empty or "squeeze_setup_score" not in df.columns:
        return df
    cfg = dict(settings.squeeze or {})
    if not cfg.get("enabled", True):
        return df
    df = df.copy()
    scores: list[float] = []
    flags: list[bool] = []
    for _, row in df.iterrows():
        points = (ls_map or {}).get((str(row.get("symbol")), str(row.get("timeframe"))), [])
        score, flag = latest_score_with_ls(
            float(row.get("squeeze_setup_score") or 0.0),
            float(row.get("squeeze_oi_points") or 0.0),
            float(row.get("long_account_ratio") or 0.0),
            ls_history_falling(points),
            settings=cfg,
            funding_available=str(row.get("funding_classification", "UNKNOWN")) != "UNKNOWN",
        )
        scores.append(score)
        flags.append(flag)
    df["squeeze_setup_score"] = scores
    df["squeeze_setup_flag"] = flags
    return df


def _fetch_ls_history_map(details: dict) -> dict[tuple, list[dict]]:
    """Return {(symbol, timeframe): [{timestamp_ms, long_pct, short_pct}, ...]}."""
    cache: dict[tuple[str, str], list[dict]] = {}
    result: dict[tuple, list[dict]] = {}
    for raw_symbol, timeframe in details:
        key = (raw_symbol, timeframe)
        if key not in cache:
            cache[key] = fetch_long_short_history(raw_symbol, period=timeframe)
        result[key] = cache[key]
    return result


def _export_charts(details: dict, charts_dir: Path, ls_map: dict | None = None) -> None:
    """Export per-symbol historical candle data to JSON files for the HTML dashboard."""
    charts_dir.mkdir(parents=True, exist_ok=True)
    cols = [
        "timestamp",
        "close", "open", "high", "low",
        "open_interest", "oi_open", "oi_high", "oi_low", "oi_close",
        "volume", "funding_rate",
        "squeeze_setup_score", "stop_cluster_level",
    ]
    for (raw_symbol, timeframe), df in details.items():
        available = [c for c in cols if c in df.columns]
        subset = df[available].copy()
        subset["timestamp"] = subset["timestamp"].astype(str)

        # Merge long/short history by timestamp (ms)
        ls_points = (ls_map or {}).get((raw_symbol, timeframe), [])
        if ls_points:
            ts_to_ls = {p["timestamp_ms"]: p for p in ls_points}
            ts_ms = pd.to_datetime(subset["timestamp"]).astype("int64") // 1_000  # us → ms
            subset["ls_long"] = ts_ms.map(lambda ms: ts_to_ls.get(ms, {}).get("long_pct", 0.0))
            subset["ls_short"] = ts_ms.map(lambda ms: ts_to_ls.get(ms, {}).get("short_pct", 0.0))

        out = {
            "symbol": raw_symbol,
            "timeframe": timeframe,
            "data": subset.fillna(0).to_dict(orient="records"),
        }
        fname = _sanitize_key(raw_symbol, timeframe) + ".json"
        (charts_dir / fname).write_text(json.dumps(out), encoding="utf-8")


def _export_liquidations(liquidations: dict, liquidations_dir: Path) -> None:
    """Export totals of liquidated USD long/short per (symbol, timeframe).

    The dashboard only needs the aggregate that matches the chart window. We
    sum across the full timeframe lookback Coinalyze returns — no per-bar
    detail and no rendered overlay on the chart.
    """
    liquidations_dir.mkdir(parents=True, exist_ok=True)
    for (raw_symbol, timeframe), df in liquidations.items():
        if df is None or df.empty:
            continue
        subset = df.copy()
        if "side" not in subset.columns or "notional" not in subset.columns:
            continue
        subset["side"] = subset["side"].astype(str).str.lower()
        long_total = float(subset.loc[subset["side"] == "long", "notional"].sum())
        short_total = float(subset.loc[subset["side"] == "short", "notional"].sum())
        if long_total <= 0 and short_total <= 0:
            continue
        out = {
            "symbol": raw_symbol,
            "timeframe": timeframe,
            "long_notional": long_total,
            "short_notional": short_total,
        }
        fname = _sanitize_key(raw_symbol, timeframe) + ".json"
        (liquidations_dir / fname).write_text(json.dumps(out), encoding="utf-8")


def _fetch_liquidations_for_details(details: dict, settings) -> dict:
    rows = {}
    liq_settings = dict(settings.liquidations or {})
    for raw_symbol, timeframe in details:
        frame = fetch_liquidation_map(raw_symbol, timeframe, settings=liq_settings)
        if not frame.empty:
            rows[(raw_symbol, timeframe)] = frame
    return rows


def _export_event_history(details: dict, history_path: Path) -> None:
    """Reconstruct event history from historical signal flags (last 21 days per symbol)."""
    rows = []
    for (raw_symbol, timeframe), hist in details.items():
        if hist.empty:
            continue
        sig_col = "signal_active_flag"
        pre_col = "pre_alert_flag"
        oi_surge_col = "oi_surge_flag"
        vol_surge_col = "volume_surge_flag"
        squeeze_col = "squeeze_setup_flag"
        relevant_cols = [sig_col, pre_col, oi_surge_col, vol_surge_col, squeeze_col]
        if not any(c in hist.columns for c in relevant_cols):
            continue
        mask = pd.Series(False, index=hist.index)
        for c in relevant_cols:
            if c in hist.columns:
                mask |= hist[c].astype(bool)
        events = hist[mask].copy()
        if events.empty:
            continue
        cutoff = events["timestamp"].max() - pd.Timedelta(days=21)
        events = events[events["timestamp"] >= cutoff]
        base = raw_symbol.split(":")[1].replace(".P", "").replace("USDT", "").replace("USD", "")
        for _, row in events.iterrows():
            is_signal = bool(row.get(sig_col, False))
            is_hot = bool(row.get("hot_pre_entry_flag", False))
            is_oi_surge = bool(row.get(oi_surge_col, False))
            is_vol_surge = bool(row.get(vol_surge_col, False))
            is_squeeze = bool(row.get(squeeze_col, False))
            if is_signal:
                et = "ENTRY"
            elif is_squeeze:
                et = "SQUEEZE_SETUP"
            elif is_oi_surge:
                et = "OI_SURGE"
            elif is_vol_surge:
                et = "VOLUME_SURGE"
            elif is_hot:
                et = "HOT_PRE_ENTRY"
            else:
                et = "PRE_ENTRY"
            rows.append({
                "event_type": et,
                "timestamp": row["timestamp"],
                "symbol": base,
                "raw_symbol": raw_symbol,
                "timeframe": timeframe,
                "close": row.get("close", 0.0),
                "price_return_pct": row.get("price_return_pct", 0.0),
                "oi_change_pct": row.get("oi_change_pct", 0.0),
                "volume_zscore": row.get("volume_zscore", 0.0),
                "volume_ratio": row.get("volume_ratio", 0.0),
                "funding_classification": row.get("funding_classification", "UNKNOWN"),
                "early_bullish_score": row.get("early_bullish_score", 0.0),
                "blowoff_risk_score": row.get("blowoff_risk_score", 0.0),
                "squeeze_setup_score": row.get("squeeze_setup_score", 0.0),
            })
    if not rows:
        return
    df_out = pd.DataFrame(rows).sort_values(["timestamp", "event_type"], ascending=[False, True])
    history_path.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(history_path, index=False)


if __name__ == "__main__":
    import sys
    def log(msg: str) -> None:
        print(msg, flush=True)

    settings = load_settings()
    symbols = load_watchlist()
    log(f"[scan] loaded {len(symbols)} symbols from watchlist")

    df, details = scan_watchlist(symbols=symbols, settings=settings, persist=True)
    log(f"[scan] scan done — {len(details)} (symbol, tf) pairs")

    # Crowd positioning: top-trader long/short account ratio per symbol.
    unique_syms = df["symbol"].nunique() if not df.empty else 0
    log(f"[scan] fetching current L/S ratio for {unique_syms} symbols…")
    df = _enrich_with_long_short_ratio(df)

    # L/S history is fetched before the CSV is written: the squeeze score
    # needs the ratio slope, and the chart export reuses the same map.
    log(f"[scan] fetching L/S history for {len(details)} (symbol, tf) pairs…")
    ls_map = _fetch_ls_history_map(details)
    ls_ok = sum(1 for v in ls_map.values() if v)
    log(f"[scan] L/S history: {ls_ok}/{len(ls_map)} pairs with data")

    df = _enrich_with_squeeze(df, ls_map, settings)
    squeeze_count = int(df["squeeze_setup_flag"].sum()) if "squeeze_setup_flag" in df.columns else 0
    log(f"[scan] squeeze setups: {squeeze_count}")

    # Latest scan CSV
    latest_csv = ROOT / "data" / "latest_scan.csv"
    latest_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(latest_csv, index=False)
    log(f"[scan] latest_scan.csv written ({len(df)} rows)")

    # Chart data for the HTML dashboard (with L/S history overlay)
    _export_charts(details, ROOT / "data" / "charts", ls_map=ls_map)
    log(f"[scan] chart JSONs written")

    # Liquidation overlays for the HTML dashboard. Coinalyze (free REST tier)
    # is the single source: missing key or blocked source degrades to empty
    # output so GitHub Pages still builds.
    log(f"[scan] fetching liquidations for {len(details)} pairs…")
    liquidation_details = _fetch_liquidations_for_details(details, settings)
    _export_liquidations(liquidation_details, ROOT / "data" / "liquidations")
    log(f"[scan] liquidations written ({len(liquidation_details)} pairs)")

    # Event history (used by both app.py and build_html.py)
    _export_event_history(details, ROOT / "data" / "event_history.csv")
    log(f"[scan] event_history.csv written")

    print(
        df[
            [
                "symbol", "timeframe", "close", "oi_change_pct",
                "funding_classification", "early_bullish_score",
                "blowoff_risk_score", "signal_active", "notes",
            ]
        ].to_string(index=False)
    )
