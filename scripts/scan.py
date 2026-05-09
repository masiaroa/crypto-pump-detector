from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from pump_detector.config import ROOT, load_settings, load_watchlist
from pump_detector.scanner import scan_watchlist


def _sanitize_key(symbol: str, timeframe: str) -> str:
    """'BYBIT:BTCUSDT.P' + '1d' → 'BYBIT_BTCUSDT_P_1d'"""
    return symbol.replace(":", "_").replace(".", "_") + f"_{timeframe}"


def _export_charts(details: dict, charts_dir: Path) -> None:
    """Export per-symbol historical candle data to JSON files for the HTML dashboard."""
    charts_dir.mkdir(parents=True, exist_ok=True)
    cols = ["timestamp", "close", "open", "high", "low", "open_interest", "volume", "funding_rate"]
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


def _export_event_history(details: dict, history_path: Path) -> None:
    """Reconstruct event history from historical signal flags (last 21 days per symbol)."""
    rows = []
    for (raw_symbol, timeframe), hist in details.items():
        if hist.empty:
            continue
        sig_col = "signal_active_flag"
        pre_col = "pre_alert_flag"
        if sig_col not in hist.columns and pre_col not in hist.columns:
            continue
        mask = pd.Series(False, index=hist.index)
        if sig_col in hist.columns:
            mask |= hist[sig_col].astype(bool)
        if pre_col in hist.columns:
            mask |= hist[pre_col].astype(bool)
        events = hist[mask].copy()
        if events.empty:
            continue
        cutoff = events["timestamp"].max() - pd.Timedelta(days=21)
        events = events[events["timestamp"] >= cutoff]
        base = raw_symbol.split(":")[1].replace(".P", "").replace("USDT", "").replace("USD", "")
        for _, row in events.iterrows():
            is_signal = bool(row.get(sig_col, False))
            is_hot = bool(row.get("hot_pre_entry_flag", False))
            et = "ENTRY" if is_signal else ("HOT_PRE_ENTRY" if is_hot else "PRE_ENTRY")
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
