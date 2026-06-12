from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from pump_detector.accumulation import (
    latest_whale_score,
    ratio_history_rising,
    spot_cvd_rising,
    spot_perp_volume_ratio,
    whale_pump_ignition_series,
)
from pump_detector.config import ROOT, load_settings, load_watchlist
from pump_detector.data_clients import fetch_spot_flows
from pump_detector.liquidations import fetch_coinalyze_liquidations_batch, fetch_liquidation_map
from pump_detector.positioning import (
    fetch_global_long_short_history,
    fetch_long_short_history,
    fetch_long_short_ratio,
    fetch_taker_ratio_history,
    fetch_top_position_ratio_history,
)
from pump_detector.scanner import scan_watchlist
from pump_detector.squeeze import (
    latest_score_with_ls,
    ls_history_falling,
    squeeze_ignition_series,
)


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
            basis_available=str(row.get("basis_classification", "UNKNOWN")) != "UNKNOWN",
        )
        scores.append(score)
        flags.append(flag)
    df["squeeze_setup_score"] = scores
    df["squeeze_setup_flag"] = flags
    return df


def _fetch_whale_inputs(details: dict) -> tuple[dict, dict, dict, dict]:
    """Per-symbol whale-score inputs, one fetch per unique symbol (4h only).

    Returns (top_position_map, global_ls_map, spot_ratio_map, spot_cvd_map).
    All sources degrade to empty on failure — the whale score renormalises
    around whatever is available.
    """
    symbols = sorted({raw_symbol for raw_symbol, _ in details})
    top_map: dict[str, list[dict]] = {}
    global_map: dict[str, list[dict]] = {}
    spot_map: dict[str, float] = {}
    spot_cvd_map: dict[str, bool | None] = {}
    for raw_symbol in symbols:
        top_map[raw_symbol] = fetch_top_position_ratio_history(raw_symbol, period="4h")
        global_map[raw_symbol] = fetch_global_long_short_history(raw_symbol, period="4h")
        perp_frame = details.get((raw_symbol, "4h"))
        spot_map[raw_symbol] = 0.0
        spot_cvd_map[raw_symbol] = None
        if perp_frame is not None and not perp_frame.empty and "volume" in perp_frame.columns:
            spot_flows = fetch_spot_flows(raw_symbol, timeframe="4h", limit=60)
            if not spot_flows.empty:
                spot_map[raw_symbol] = spot_perp_volume_ratio(spot_flows["volume"], perp_frame["volume"])
                spot_cvd_map[raw_symbol] = spot_cvd_rising(spot_flows)
    return top_map, global_map, spot_map, spot_cvd_map


def _enrich_with_whale(
    df: pd.DataFrame,
    details: dict,
    top_map: dict,
    global_map: dict,
    spot_map: dict,
    spot_cvd_map: dict,
    settings,
) -> pd.DataFrame:
    """Fold positioning + spot components into the latest whale score, and
    evaluate the retail-FOMO ignition (whale_pump_flag) per candle.

    The per-candle ignition series is written back into the ``details``
    frames so the event history can emit WHALE_PUMP events.
    """
    if df.empty or "whale_accum_score" not in df.columns:
        return df
    cfg = dict(settings.accumulation or {})
    if not cfg.get("enabled", True):
        return df
    df = df.copy()
    scores, accum_flags, pump_flags = [], [], []
    top_longs, global_longs, divergences, spot_ratios, spot_leds = [], [], [], [], []
    spot_led_min = float(cfg.get("spot_led_ratio_min", 1.0))
    for _, row in df.iterrows():
        symbol = str(row.get("symbol"))
        timeframe = str(row.get("timeframe"))
        top_points = (top_map or {}).get(symbol, [])
        global_points = (global_map or {}).get(symbol, [])
        top_long = top_points[-1]["long_pct"] if top_points else 0.0
        global_long = global_points[-1]["long_pct"] if global_points else 0.0
        spot_ratio = float((spot_map or {}).get(symbol, 0.0))
        score, accum_flag = latest_whale_score(
            float(row.get("whale_accum_score") or 0.0),
            float(row.get("whale_flow_points") or 0.0),
            top_position_long=top_long,
            top_position_rising=ratio_history_rising(top_points),
            global_long_ratio=global_long,
            spot_perp_vol_ratio=spot_ratio,
            settings=cfg,
            cvd_available=float(row.get("taker_buy_share") or 0.0) > 0,
            spot_cvd_rising=(spot_cvd_map or {}).get(symbol),
        )
        history = details.get((symbol, timeframe))
        pump_flag = False
        if history is not None and "whale_accum_score" in history.columns:
            pump_series = whale_pump_ignition_series(history, global_points, settings=cfg)
            history["whale_pump_flag"] = pump_series
            pump_flag = bool(pump_series.iloc[-1]) if len(pump_series) else False
        scores.append(score)
        accum_flags.append(accum_flag)
        pump_flags.append(pump_flag)
        top_longs.append(top_long)
        global_longs.append(global_long)
        divergences.append(round(float(row.get("long_account_ratio") or 0.0) - global_long, 4) if global_long > 0 else 0.0)
        spot_ratios.append(spot_ratio)
        spot_leds.append(spot_ratio >= spot_led_min)
    df["whale_accum_score"] = scores
    df["whale_accum_flag"] = accum_flags
    df["whale_pump_flag"] = pump_flags
    df["top_position_long_pct"] = top_longs
    df["global_long_ratio"] = global_longs
    df["retail_top_divergence"] = divergences
    df["spot_perp_vol_ratio"] = spot_ratios
    df["spot_led_flag"] = spot_leds
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
        "volume", "funding_rate", "basis_pct",
        "squeeze_setup_score", "stop_cluster_level",
        "whale_accum_score", "cvd", "cvd_slope",
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
    """Coinalyze liquidations per (symbol, timeframe), batched per timeframe.

    Batching comma-joined symbols turns ~80 calls into ~4 on a 40-symbol
    watchlist — the free tier allows 40 req/min. Falls back to per-symbol
    fetches if the batch path returns nothing (e.g. older config shapes).
    """
    liq_settings = dict(settings.liquidations or {})
    if not liq_settings.get("enabled", True):
        return {}
    coinalyze_cfg = dict(liq_settings.get("coinalyze", {}) or {})
    rows: dict[tuple, pd.DataFrame] = {}
    timeframes: dict[str, list[str]] = {}
    for raw_symbol, timeframe in details:
        timeframes.setdefault(timeframe, []).append(raw_symbol)
    for timeframe, symbols in timeframes.items():
        batch = fetch_coinalyze_liquidations_batch(symbols, timeframe, coinalyze_cfg)
        for raw_symbol, frame in batch.items():
            rows[(raw_symbol, timeframe)] = frame
    if rows:
        return rows
    for raw_symbol, timeframe in details:
        frame = fetch_liquidation_map(raw_symbol, timeframe, settings=liq_settings)
        if not frame.empty:
            rows[(raw_symbol, timeframe)] = frame
    return rows


def _enrich_with_squeeze_ignition(
    df: pd.DataFrame,
    details: dict,
    liquidation_details: dict,
    taker_map: dict,
    settings,
) -> pd.DataFrame:
    """Evaluate the squeeze ignition for the newest candle of each pair.

    Needs the per-candle setup-score history (details), the Coinalyze
    liquidation frames and the taker buy-ratio history — that's why it runs
    after those fetches and right before latest_scan.csv is written.
    """
    if df.empty or "squeeze_setup_score" not in df.columns:
        return df
    cfg = dict(settings.squeeze or {})
    if not cfg.get("enabled", True):
        return df
    df = df.copy()
    flags, liq_zs, taker_zs = [], [], []
    for _, row in df.iterrows():
        symbol = str(row.get("symbol"))
        timeframe = str(row.get("timeframe"))
        history = details.get((symbol, timeframe))
        liq_z = 0.0
        taker_z = 0.0
        flag = False
        if history is not None and "squeeze_setup_score" in history.columns:
            liq_frame = (liquidation_details or {}).get((symbol, timeframe))
            flag_series, liq_series, taker_series = squeeze_ignition_series(
                history,
                liq_frame,
                (taker_map or {}).get(symbol, []),
                settings=cfg,
            )
            # Written back per candle so the event history can emit
            # SQUEEZE_IGNITION events.
            history["squeeze_ignition_flag"] = flag_series
            if len(flag_series):
                flag = bool(flag_series.iloc[-1])
                liq_z = float(liq_series.iloc[-1])
                taker_z = float(taker_series.iloc[-1])
        flags.append(flag)
        liq_zs.append(liq_z)
        taker_zs.append(taker_z)
    df["squeeze_ignition_flag"] = flags
    df["short_liq_zscore"] = liq_zs
    df["taker_buy_ratio_zscore"] = taker_zs
    return df


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
        whale_col = "whale_accum_flag"
        ignition_col = "squeeze_ignition_flag"
        pump_col = "whale_pump_flag"
        relevant_cols = [sig_col, pre_col, oi_surge_col, vol_surge_col, squeeze_col, whale_col, ignition_col, pump_col]
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
            is_whale = bool(row.get(whale_col, False))
            is_ignition = bool(row.get(ignition_col, False))
            is_pump = bool(row.get(pump_col, False))
            if is_signal:
                et = "ENTRY"
            elif is_ignition:
                et = "SQUEEZE_IGNITION"
            elif is_pump:
                et = "WHALE_PUMP"
            elif is_squeeze:
                et = "SQUEEZE_SETUP"
            elif is_whale:
                et = "WHALE_ACCUM"
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
                "whale_accum_score": row.get("whale_accum_score", 0.0),
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

    # Whale accumulation: top-trader position ratio, retail ratio and spot
    # volume leadership (one fetch per symbol, 4h granularity).
    log(f"[scan] fetching whale inputs (position/retail/spot) …")
    top_map, global_map, spot_map, spot_cvd_map = _fetch_whale_inputs(details)
    df = _enrich_with_whale(df, details, top_map, global_map, spot_map, spot_cvd_map, settings)
    whale_count = int(df["whale_accum_flag"].sum()) if "whale_accum_flag" in df.columns else 0
    pump_count = int(df["whale_pump_flag"].sum()) if "whale_pump_flag" in df.columns else 0
    log(f"[scan] whale accumulations: {whale_count} · whale pumps: {pump_count}")

    # Liquidations (Coinalyze, batched) + taker ratio: fetched before the CSV
    # because the squeeze ignition reads both. Missing key or blocked source
    # degrades to empty output so GitHub Pages still builds.
    log(f"[scan] fetching liquidations for {len(details)} pairs…")
    liquidation_details = _fetch_liquidations_for_details(details, settings)
    log(f"[scan] liquidations fetched ({len(liquidation_details)} pairs)")
    taker_map = {sym: fetch_taker_ratio_history(sym, period="4h") for sym in sorted({s for s, _ in details})}
    df = _enrich_with_squeeze_ignition(df, details, liquidation_details, taker_map, settings)
    ignition_count = int(df["squeeze_ignition_flag"].sum()) if "squeeze_ignition_flag" in df.columns else 0
    log(f"[scan] squeeze ignitions: {ignition_count}")

    # Latest scan CSV
    latest_csv = ROOT / "data" / "latest_scan.csv"
    latest_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(latest_csv, index=False)
    log(f"[scan] latest_scan.csv written ({len(df)} rows)")

    # Chart data for the HTML dashboard (with L/S history overlay)
    _export_charts(details, ROOT / "data" / "charts", ls_map=ls_map)
    log(f"[scan] chart JSONs written")

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
