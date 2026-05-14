from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from pump_detector.config import ROOT, load_settings, load_watchlist
from pump_detector.liquidations import collect_executed_burst, fetch_liquidation_map
from pump_detector.scanner import scan_watchlist


def _sanitize_key(symbol: str, timeframe: str) -> str:
    """'BYBIT:BTCUSDT.P' + '1d' → 'BYBIT_BTCUSDT_P_1d'"""
    return symbol.replace(":", "_").replace(".", "_") + f"_{timeframe}"


def _export_charts(details: dict, charts_dir: Path) -> None:
    """Export per-symbol historical candle data to JSON files for the HTML dashboard."""
    charts_dir.mkdir(parents=True, exist_ok=True)
    cols = [
        "timestamp",
        "close", "open", "high", "low",
        "open_interest", "oi_open", "oi_high", "oi_low", "oi_close",
        "volume", "funding_rate",
    ]
    for (raw_symbol, timeframe), df in details.items():
        available = [c for c in cols if c in df.columns]
        subset = df[available].copy()
        subset["timestamp"] = subset["timestamp"].astype(str)
        out = {
            "symbol": raw_symbol,
            "timeframe": timeframe,
            "data": subset.fillna(0).to_dict(orient="records"),
        }
        fname = _sanitize_key(raw_symbol, timeframe) + ".json"
        (charts_dir / fname).write_text(json.dumps(out), encoding="utf-8")


def _export_liquidations(liquidations: dict, liquidations_dir: Path) -> None:
    """Export per-symbol liquidation map data to JSON files for the HTML dashboard."""
    liquidations_dir.mkdir(parents=True, exist_ok=True)
    for (raw_symbol, timeframe), df in liquidations.items():
        if df is None or df.empty:
            continue
        subset = df.copy()
        if "timestamp" in subset.columns:
            subset["timestamp"] = subset["timestamp"].astype(str)
        out = {
            "symbol": raw_symbol,
            "timeframe": timeframe,
            "data": subset.fillna(0).to_dict(orient="records"),
        }
        fname = _sanitize_key(raw_symbol, timeframe) + ".json"
        (liquidations_dir / fname).write_text(json.dumps(out), encoding="utf-8")


def _liquidation_settings_for_static_export(settings) -> dict:
    """Use WS history for static exports when it exists, without changing app defaults."""
    liq_cfg = dict(settings.liquidations or {})
    if not liq_cfg.get("enabled", True):
        return liq_cfg

    executed_cfg = dict(liq_cfg.get("executed") or {})
    history_file = executed_cfg.get("history_file") or "data/liquidations/_ws_history.jsonl"
    history_path = Path(history_file)
    if not history_path.is_absolute():
        history_path = ROOT / history_path
    if history_path.exists():
        executed_cfg["enabled"] = True
    liq_cfg["executed"] = executed_cfg
    return liq_cfg


def _fetch_liquidations_for_details(details: dict, settings) -> dict:
    rows = {}
    liq_settings = _liquidation_settings_for_static_export(settings)
    for raw_symbol, timeframe in details:
        frame = fetch_liquidation_map(raw_symbol, timeframe, settings=liq_settings)
        if not frame.empty:
            rows[(raw_symbol, timeframe)] = frame
    return rows


def _run_liquidation_burst(settings) -> int:
    """Run a short WS burst before fetching per-symbol liquidations.

    The burst is best-effort: any collector failure is swallowed and the
    scan continues with whatever JSONL history already exists on disk.
    """
    import os

    liq_cfg = settings.liquidations or {}
    if not liq_cfg.get("enabled", True):
        return 0
    executed_cfg = liq_cfg.get("executed") or {}
    if not executed_cfg.get("enabled", True):
        return 0
    duration = float(executed_cfg.get("burst_seconds") or 0)
    # Env override: SCAN_BURST_SECONDS=0 disables the burst entirely,
    # SCAN_BURST_SECONDS=10 shortens it.
    env_burst = os.environ.get("SCAN_BURST_SECONDS", "").strip()
    if env_burst:
        try:
            duration = float(env_burst)
        except ValueError:
            pass
    if duration <= 0:
        return 0
    providers = [
        p
        for p in executed_cfg.get("providers", ["binance_ws", "bybit_ws", "okx_ws"])
        if str(p).endswith("_ws")
    ]
    if not providers:
        return 0
    history_file = executed_cfg.get("history_file") or "data/liquidations/_ws_history.jsonl"
    out_path = Path(history_file)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    try:
        return collect_executed_burst(
            duration_s=duration,
            exchanges=providers,
            out_path=out_path,
            max_age_days=int(executed_cfg.get("max_age_days") or 14),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"liquidation burst skipped: {exc}")
        return 0


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
        relevant_cols = [sig_col, pre_col, oi_surge_col, vol_surge_col]
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
            if is_signal:
                et = "ENTRY"
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
            })
    if not rows:
        return
    df_out = pd.DataFrame(rows).sort_values(["timestamp", "event_type"], ascending=[False, True])
    history_path.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(history_path, index=False)


if __name__ == "__main__":
    settings = load_settings()
    symbols = load_watchlist()

    df, details = scan_watchlist(symbols=symbols, settings=settings, persist=True)

    # Latest scan CSV
    latest_csv = ROOT / "data" / "latest_scan.csv"
    latest_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(latest_csv, index=False)

    # Chart data for the HTML dashboard
    _export_charts(details, ROOT / "data" / "charts")

    # Liquidation overlays for the HTML dashboard. The WS burst runs first so
    # the per-symbol read below can find fresh executed liquidations. Missing
    # or blocked sources degrade to empty output so GitHub Pages still builds.
    burst_written = _run_liquidation_burst(settings)
    if burst_written:
        print(f"liquidation burst: wrote {burst_written} records")
    liquidation_details = _fetch_liquidations_for_details(details, settings)
    _export_liquidations(liquidation_details, ROOT / "data" / "liquidations")

    # Event history (used by both app.py and build_html.py)
    _export_event_history(details, ROOT / "data" / "event_history.csv")

    print(
        df[
            [
                "symbol", "timeframe", "close", "oi_change_pct",
                "funding_classification", "early_bullish_score",
                "blowoff_risk_score", "signal_active", "notes",
            ]
        ].to_string(index=False)
    )
