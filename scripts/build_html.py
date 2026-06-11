#!/usr/bin/env python3
"""Generate docs/index.html — a self-contained static dashboard for GitHub Pages.

Run:
    python scripts/build_html.py

Reads:
    data/event_history.csv   – recent signal events
    data/latest_scan.csv     – one row per symbol/timeframe (current state)
    data/charts/*.json       – historical candle data per symbol/timeframe

Writes:
    docs/index.html          – fully self-contained HTML (no server required)
"""
from __future__ import annotations

import html as html_mod
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CHARTS_DIR = DATA_DIR / "charts"
LIQUIDATIONS_DIR = DATA_DIR / "liquidations"
DOCS_DIR = ROOT / "docs"


# ---------------------------------------------------------------------------
# Data loading  (all return nested {symbol: {timeframe: data}})
# ---------------------------------------------------------------------------

def load_events() -> list[dict]:
    p = DATA_DIR / "event_history.csv"
    if not p.exists():
        return []
    df = pd.read_csv(p)
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp", ascending=False)
    return df.fillna("—").head(60).to_dict("records")


def load_scan() -> dict[str, dict[str, dict]]:
    """Returns {symbol: {timeframe: row_dict}}."""
    p = DATA_DIR / "latest_scan.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    df = df.fillna(0)
    result: dict[str, dict[str, dict]] = {}
    has_tf_col = "timeframe" in df.columns
    for _, row in df.iterrows():
        sym = str(row["symbol"])
        tf = str(row["timeframe"]) if has_tf_col else "1d"
        result.setdefault(sym, {})[tf] = row.to_dict()
    return result


def _primary_scan_row(by_tf: dict[str, dict], prefer_tf: str = "4h") -> dict:
    """Pick preferred TF row from nested scan dict."""
    return by_tf.get(prefer_tf) or next(iter(by_tf.values()), {})


_VALID_TFS = {"1h", "4h", "1d"}


def _normalize_charts_input(charts: dict) -> dict[str, dict[str, list]]:
    """Accept old flat {sym: [list]} or new nested {sym: {tf: list}}."""
    result: dict[str, dict[str, list]] = {}
    for sym, val in charts.items():
        if isinstance(val, list):
            result[sym] = {"1d": val}
        elif isinstance(val, dict):
            result[sym] = val
    return result


def _normalize_scan_input(scan: dict) -> dict[str, dict[str, dict]]:
    """Accept old flat {sym: row_dict} or new nested {sym: {tf: row_dict}}.

    Heuristic: if ALL keys of the inner dict are valid TF strings, it's nested;
    otherwise it's a flat scan row.
    """
    result: dict[str, dict[str, dict]] = {}
    for sym, val in scan.items():
        if not isinstance(val, dict):
            continue
        if val and all(k in _VALID_TFS for k in val):
            # Already nested: {tf: row_dict}
            result[sym] = val
        else:
            # Flat row dict — wrap with its own timeframe or default "1d"
            tf = str(val.get("timeframe", "1d"))
            result[sym] = {tf: val}
    return result


def _liq_totals_from_value(val: object) -> dict[str, float]:
    """Accept totals dict or legacy list-of-rows and return {long, short} USD."""
    if isinstance(val, dict):
        if "long_notional" in val or "short_notional" in val:
            return {
                "long": safe_float(val.get("long_notional")),
                "short": safe_float(val.get("short_notional")),
            }
        if "long" in val or "short" in val:
            return {"long": safe_float(val.get("long")), "short": safe_float(val.get("short"))}
    if isinstance(val, list):
        totals = {"long": 0.0, "short": 0.0}
        for row in val:
            if not isinstance(row, dict):
                continue
            side = str(row.get("side", "")).lower()
            if side in totals:
                totals[side] += safe_float(row.get("notional", row.get("amount")))
        return totals
    return {"long": 0.0, "short": 0.0}


def _normalize_liqs_input(liquidations: dict) -> dict[str, dict[str, dict[str, float]]]:
    """Accept legacy shapes and return {sym: {tf: {"long": X, "short": Y}}}."""
    result: dict[str, dict[str, dict[str, float]]] = {}
    for sym, val in liquidations.items():
        if isinstance(val, dict) and val and all(k in _VALID_TFS for k in val):
            # Already nested by timeframe.
            result[sym] = {tf: _liq_totals_from_value(v) for tf, v in val.items()}
        elif isinstance(val, dict) and ("long_notional" in val or "short_notional" in val):
            # Flat totals dict without a timeframe wrapper.
            result[sym] = {"1d": _liq_totals_from_value(val)}
        elif isinstance(val, list):
            result[sym] = {"1d": _liq_totals_from_value(val)}
    return result


_MAX_CANDLES: dict[str, int] = {"1d": 244, "4h": 528, "1h": 360}


def load_charts() -> dict[str, dict[str, list]]:
    """Returns {symbol: {timeframe: [candle_dict, ...]}}."""
    charts: dict[str, dict[str, list]] = {}
    if not CHARTS_DIR.exists():
        return load_embedded_charts()
    for f in sorted(CHARTS_DIR.glob("*.json")):
        try:
            obj = json.loads(f.read_text("utf-8"))
            sym = obj.get("symbol", "")
            tf = str(obj.get("timeframe", "1d"))
            data = obj.get("data", [])
            limit = _MAX_CANDLES.get(tf, 120)
            data = data[-limit:] if len(data) > limit else data
            charts.setdefault(sym, {})[tf] = data
        except Exception:
            pass
    return charts or load_embedded_charts()


def load_embedded_json(global_name: str, path: Path | None = None) -> dict:
    """Recover a JSON object embedded in a previous static HTML build."""
    path = path or (DOCS_DIR / "index.html")
    if not path.exists():
        return {}
    try:
        html = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    pattern = rf"const\s+{re.escape(global_name)}\s*=\s*(\{{.*?\}});</script>"
    match = re.search(pattern, html, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def load_embedded_charts(path: Path | None = None) -> dict[str, dict[str, list]]:
    """Recover chart data embedded in a previous static HTML build.

    Handles both old flat shape {sym: [...]} and new nested shape {sym: {tf: [...]}}.
    """
    data = load_embedded_json("CHART_DATA", path)
    result: dict[str, dict[str, list]] = {}
    for symbol, val in data.items():
        if isinstance(val, list) and val:
            # old flat shape → wrap as {"1d": [...]}
            limit = _MAX_CANDLES.get("1d", 120)
            rows = val[-limit:] if len(val) > limit else val
            result[str(symbol)] = {"1d": rows}
        elif isinstance(val, dict):
            by_tf: dict[str, list] = {}
            for tf, rows in val.items():
                if isinstance(rows, list) and rows:
                    limit = _MAX_CANDLES.get(str(tf), 120)
                    by_tf[str(tf)] = rows[-limit:] if len(rows) > limit else rows
            if by_tf:
                result[str(symbol)] = by_tf
    return result


def load_embedded_liquidations(path: Path | None = None) -> dict[str, dict[str, dict[str, float]]]:
    """Recover liquidation totals embedded in a previous static HTML build."""
    data = load_embedded_json("LIQUIDATION_DATA", path)
    return _normalize_liqs_input(data)


def load_liquidations() -> dict[str, dict[str, dict[str, float]]]:
    """Returns {symbol: {timeframe: {"long": $, "short": $}}}."""
    liquidations: dict[str, dict[str, dict[str, float]]] = {}
    if not LIQUIDATIONS_DIR.exists():
        return load_embedded_liquidations()
    for f in sorted(LIQUIDATIONS_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            obj = json.loads(f.read_text("utf-8"))
        except Exception:
            continue
        sym = obj.get("symbol", "")
        tf = str(obj.get("timeframe", "1d"))
        if "long_notional" in obj or "short_notional" in obj:
            totals = _liq_totals_from_value(obj)
        else:
            # Legacy shape: {"data": [{timestamp, side, notional}, ...]}
            totals = _liq_totals_from_value(obj.get("data", []))
        if totals["long"] <= 0 and totals["short"] <= 0:
            continue
        liquidations.setdefault(sym, {})[tf] = totals
    return liquidations or load_embedded_liquidations()


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def esc(s: object) -> str:
    return html_mod.escape(str(s))


def safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    if isinstance(value, str) and value.strip() in {"", "-", "—", "–"}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def score_color(score: float) -> str:
    if score >= 70:
        return "#3fb950"
    if score >= 40:
        return "#d29922"
    return "#8b949e"


def funding_badge_class(fc: str) -> str:
    return {
        "NEGATIVE": "badge-negative",
        "NEUTRAL":  "badge-neutral",
        "POSITIVE": "badge-positive",
        "HOT":      "badge-hot",
        "EXTREME":  "badge-extreme",
        # Basis (perp premium) buckets reuse the funding palette.
        "DISCOUNT": "badge-negative",
        "FLAT":     "badge-neutral",
        "PREMIUM":  "badge-positive",
    }.get(str(fc), "badge-unknown")


_BASIS_CLASSES = {"DISCOUNT", "FLAT", "PREMIUM", "HOT", "EXTREME"}


def basis_badge_html(bc: str) -> str:
    bc = str(bc)
    if bc not in _BASIS_CLASSES:
        return '<span class="muted">—</span>'
    return f'<span class="badge {funding_badge_class(bc)}">{esc(bc)}</span>'


def format_price(close: float) -> str:
    close = safe_float(close)
    if close >= 1_000:
        return f"${close:,.0f}"
    if close >= 1:
        return f"${close:,.2f}"
    if close > 0:
        return f"${close:.5g}"
    return "—"


def format_pct(value: object) -> str:
    return f"{safe_float(value):+.1%}"


def format_money(value: object) -> str:
    amount = safe_float(value)
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    if amount > 0:
        return f"${amount:,.0f}"
    return "$0"


def positive_float(value: object) -> bool:
    return safe_float(value) > 0


def daily_change_pct(scan_row: dict, candles: list | None) -> float:
    if candles:
        last = candles[-1] or {}
        open_price = safe_float(last.get("open"))
        close_price = safe_float(last.get("close"))
        if open_price > 0 and close_price > 0:
            return close_price / open_price - 1
    return safe_float(scan_row.get("price_return_pct"))


def format_long_short(long_pct: float, short_pct: float) -> str:
    long_pct = safe_float(long_pct)
    short_pct = safe_float(short_pct)
    if long_pct <= 0 and short_pct <= 0:
        return "—"
    return f"{long_pct * 100:.0f}% / {short_pct * 100:.0f}%"


def row_from_event(event: dict) -> dict:
    raw_symbol = str(event.get("raw_symbol", ""))
    exchange = raw_symbol.split(":", 1)[0] if ":" in raw_symbol else ""
    return {
        "symbol": raw_symbol,
        "exchange": exchange,
        "close": event.get("close", 0),
        "early_bullish_score": event.get("early_bullish_score", 0),
        "blowoff_risk_score": event.get("blowoff_risk_score", 0),
        "funding_classification": event.get("funding_classification", "UNKNOWN"),
        "funding_rate": event.get("funding_rate", 0),
        "oi_change_pct": event.get("oi_change_pct", 0),
        "signal_active": event.get("event_type") == "ENTRY",
        "alert_triggered": event.get("event_type") in {"ENTRY", "HOT_PRE_ENTRY"},
        "squeeze_setup_score": event.get("squeeze_setup_score", 0),
        "squeeze_setup_flag": event.get("event_type") == "SQUEEZE_SETUP",
    }


def _classify_funding_simple(rate: float) -> str:
    """Lightweight funding bucket used when the scan CSV is missing.

    Mirrors pump_detector.signals.classify_funding's coarse thresholds without
    needing the rolling-history percentile.
    """
    if rate is None:
        return "UNKNOWN"
    try:
        r = float(rate)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if r != r:
        return "UNKNOWN"
    if r < 0:
        return "NEGATIVE"
    if r <= 0.0001:
        return "NEUTRAL"
    if r <= 0.0005:
        return "POSITIVE"
    return "HOT"


def _three_bar_oi_pct(candles: list) -> float:
    if len(candles) < 4:
        return 0.0
    older = safe_float(candles[-4].get("open_interest"))
    latest = safe_float(candles[-1].get("open_interest"))
    if older <= 0 or latest <= 0:
        return 0.0
    return latest / older - 1.0


def _three_bar_volume_ratio(candles: list, window: int = 50) -> float:
    if len(candles) < 4:
        return 0.0
    last_three = sum(safe_float(c.get("volume")) for c in candles[-3:])
    history = candles[:-3][-window:]
    sums = []
    for i in range(3, len(history) + 1):
        sums.append(sum(safe_float(c.get("volume")) for c in history[i - 3 : i]))
    if not sums:
        return 0.0
    sums.sort()
    median = sums[len(sums) // 2]
    if median <= 0:
        return 0.0
    return last_three / median


def row_from_chart(symbol: str, candles: list) -> dict:
    exchange = symbol.split(":", 1)[0] if ":" in symbol else ""
    last = candles[-1] if candles else {}
    funding_rate = safe_float(last.get("funding_rate"))
    oi_3bar = _three_bar_oi_pct(candles)
    vol_3bar = _three_bar_volume_ratio(candles)
    return {
        "symbol": symbol,
        "exchange": exchange,
        "close": last.get("close", 0),
        "early_bullish_score": 0,
        "blowoff_risk_score": 0,
        "funding_classification": _classify_funding_simple(funding_rate),
        "funding_rate": funding_rate,
        "oi_change_pct": 0,
        "oi_3bar_change_pct": oi_3bar,
        "volume_3bar_ratio": vol_3bar,
        "oi_surge_flag": oi_3bar >= 0.04,
        "volume_surge_flag": vol_3bar >= 2.5,
    }


# ---------------------------------------------------------------------------
# Slides
# ---------------------------------------------------------------------------

def _short_base(symbol: str) -> str:
    ticker = symbol.split(":")[-1].replace(".P", "")
    for suffix in ("USDT", "USD", "USDC"):
        if ticker.endswith(suffix):
            return ticker[: -len(suffix)]
    return ticker


def make_events_slide(events: list[dict], scan: dict[str, dict],
                      symbol_to_slide: dict[str, int]) -> str:
    now_str = pd.Timestamp.now("Europe/Madrid").strftime("%Y-%m-%d %H:%M (Madrid)")

    # Index the most recent event per symbol so the overview table can show
    # "last activity" without needing a separate events table.
    latest_event: dict[str, dict] = {}
    for ev in events:
        raw_sym = str(ev.get("raw_symbol", ""))
        if not raw_sym or raw_sym in latest_event:
            continue  # `events` is already sorted desc by timestamp
        latest_event[raw_sym] = ev

    # Build overview rows — one row per known symbol. Signals are sorted to the top,
    # then OI/volume surges, then bullish score.
    overview_rows = []
    for sym, row in scan.items():
        slide_idx = symbol_to_slide.get(sym, -1)
        has_signal = bool(row.get("signal_active") or row.get("alert_triggered"))
        oi_surge = bool(row.get("oi_surge_flag"))
        vol_surge = bool(row.get("volume_surge_flag"))
        squeeze_flag = bool(row.get("squeeze_setup_flag"))
        priority = (4 if has_signal else 3 if squeeze_flag else 2 if oi_surge else 1 if vol_surge else 0)
        ev = latest_event.get(sym, {})
        overview_rows.append({
            "symbol": sym,
            "slide_idx": slide_idx,
            "priority": priority,
            "has_signal": has_signal,
            "oi_surge": oi_surge,
            "vol_surge": vol_surge,
            "squeeze_flag": squeeze_flag,
            "squeeze": safe_float(row.get("squeeze_setup_score", 0)),
            "bull": safe_float(row.get("early_bullish_score", 0)),
            "risk": safe_float(row.get("blowoff_risk_score", 0)),
            "close": safe_float(row.get("close", 0)),
            "change": safe_float(row.get("price_return_pct", 0)),
            "oi_3bar": safe_float(row.get("oi_3bar_change_pct", 0)),
            "vol_3bar": safe_float(row.get("volume_3bar_ratio", 0)),
            "funding": str(row.get("funding_classification", "UNKNOWN")),
            "basis": str(row.get("basis_classification", "UNKNOWN")),
            "long_pct": safe_float(row.get("long_account_ratio", 0)),
            "short_pct": safe_float(row.get("short_account_ratio", 0)),
            "last_event_type": str(ev.get("event_type", "")),
            "last_event_date": str(ev.get("timestamp", ""))[:10],
        })
    overview_rows.sort(key=lambda r: (-r["priority"], -r["bull"], r["symbol"]))

    signal_count = sum(1 for r in overview_rows if r["has_signal"])
    squeeze_count = sum(1 for r in overview_rows if r["squeeze_flag"] and not r["has_signal"])
    surge_count = sum(1 for r in overview_rows if (r["oi_surge"] or r["vol_surge"]) and not r["has_signal"] and not r["squeeze_flag"])

    overview_rows_html = ""
    for r in overview_rows:
        label = esc(_short_base(r["symbol"]))
        if r["has_signal"]:
            icon = "🟢"
        elif r["squeeze_flag"]:
            icon = "🟣"
        elif r["oi_surge"] or r["vol_surge"]:
            icon = "🟡"
        else:
            icon = "·"
        sig_chips = ""
        if r["has_signal"]:
            sig_chips += '<span class="sig-chip sig-entry">ENTRY</span>'
        if r["squeeze_flag"]:
            sig_chips += f'<span class="sig-chip sig-squeeze" title="Squeeze setup score {r["squeeze"]:.0f} — shorts atrapados">SQUEEZE</span>'
        if r["oi_surge"]:
            sig_chips += f'<span class="sig-chip sig-oi" title="3-bar OI +{r["oi_3bar"]*100:.1f}%">OI&nbsp;SURGE</span>'
        if r["vol_surge"]:
            sig_chips += f'<span class="sig-chip sig-vol" title="3-bar volume {r["vol_3bar"]:.1f}x">VOL&nbsp;SURGE</span>'
        if r["slide_idx"] >= 0:
            sym_cell = f'<td class="sym-cell sym-link" data-goto="{r["slide_idx"]}">{icon} {label}</td>'
        else:
            sym_cell = f'<td class="sym-cell">{icon} {label}</td>'
        change_color = "#3fb950" if r["change"] >= 0 else "#f85149"
        oi3_color = "#3fb950" if r["oi_3bar"] >= 0 else "#f85149"
        ls_label = format_long_short(r["long_pct"], r["short_pct"])
        ls_color = "#3fb950" if r["long_pct"] >= r["short_pct"] else "#f85149"
        et = r["last_event_type"]
        if et:
            et_cls = {
                "ENTRY": "et-entry",
                "HOT_PRE_ENTRY": "et-hot",
                "OI_SURGE": "et-oi",
                "VOLUME_SURGE": "et-vol",
                "SQUEEZE_SETUP": "et-squeeze",
            }.get(et, "et-pre")
            last_event_cell = (
                f'<span class="event-type-badge {et_cls}">{esc(et)}</span>'
                f'<span class="event-date">&nbsp;{esc(r["last_event_date"])}</span>'
            )
        else:
            last_event_cell = '<span class="muted">—</span>'
        overview_rows_html += f"""
          <tr>
            {sym_cell}
            <td>{esc(format_price(r["close"]))}</td>
            <td style="color:{change_color}">{esc(format_pct(r["change"]))}</td>
            <td style="color:{score_color(r["bull"])}">{r["bull"]:.0f}</td>
            <td style="color:#f85149">{r["risk"]:.0f}</td>
            <td style="color:{score_color(r["squeeze"])}">{r["squeeze"]:.0f}</td>
            <td style="color:{oi3_color}">{r["oi_3bar"]*100:+.1f}%</td>
            <td>{r["vol_3bar"]:.1f}x</td>
            <td><span class="badge {funding_badge_class(r["funding"])}">{esc(r["funding"])}</span></td>
            <td>{basis_badge_html(r["basis"])}</td>
            <td style="color:{ls_color}">{esc(ls_label)}</td>
            <td class="sig-cell">{sig_chips}</td>
            <td class="last-event-cell">{last_event_cell}</td>
          </tr>"""

    overview_table_html = ""
    if overview_rows_html:
        overview_table_html = f"""
        <div class="events-table-wrap">
          <h3 class="section-label">Watchlist — click symbol to navigate · {signal_count} signal(s) · {squeeze_count} squeeze(s) · {surge_count} surge(s) · {len(latest_event)} symbols with recent events</h3>
          <table class="overview-table">
            <thead>
              <tr><th>Symbol</th><th>Price</th><th>Chg</th><th>Bull</th><th>Risk</th><th title="Squeeze setup score — shorts atrapados">Sqz</th><th>OI&nbsp;3b</th><th>Vol&nbsp;3b</th><th>Funding</th><th title="Prima del perpetuo (premium index)">Basis</th><th>L/S</th><th>Signals</th><th>Last&nbsp;event</th></tr>
            </thead>
            <tbody>{overview_rows_html}</tbody>
          </table>
        </div>"""

    return f"""
    <section class="slide" id="slide-0" data-idx="0">
      <div class="slide-header">
        <div class="events-title">
          <span class="logo">📈</span>
          <span>Crypto Pump Detector</span>
        </div>
        <div class="header-actions">
          <a class="header-link" href="coinalyze.html">Coinalyze metrics</a>
          <span class="scan-time">Last scan: {esc(now_str)} &middot; {len(scan)} symbols</span>
        </div>
      </div>
      <div class="slide-body">
        {overview_table_html}
      </div>
    </section>"""


def make_crypto_slide(
    idx: int,
    scan_row: dict,
    candles_by_tf: dict[str, list] | None = None,
    liqs_by_tf: dict[str, dict[str, float]] | None = None,
    default_tf: str = "4h",
) -> str:
    candles_by_tf = candles_by_tf or {}
    liqs_by_tf    = liqs_by_tf or {}

    # Use default TF data for header metrics; fall back to first available TF
    candles = candles_by_tf.get(default_tf) or next(iter(candles_by_tf.values()), None) or []
    totals  = liqs_by_tf.get(default_tf) or next(iter(liqs_by_tf.values()), None) or {"long": 0.0, "short": 0.0}

    symbol   = str(scan_row.get("symbol", ""))
    exchange = str(scan_row.get("exchange", ""))

    # Use freshest TF's last candle close (4h > 1d) to avoid stale daily price
    _TF_FRESHNESS = {"1h": 0, "4h": 1, "1d": 2}
    freshest_tf = min(candles_by_tf, key=lambda t: _TF_FRESHNESS.get(t, 3)) if candles_by_tf else None
    freshest_candles = candles_by_tf.get(freshest_tf, []) if freshest_tf else []
    if freshest_candles and safe_float(freshest_candles[-1].get("close")) > 0:
        close = safe_float(freshest_candles[-1]["close"])
    else:
        close = safe_float(scan_row.get("close", 0))

    change   = daily_change_pct(scan_row, candles)
    long_pct = safe_float(scan_row.get("long_account_ratio"))
    short_pct = safe_float(scan_row.get("short_account_ratio"))
    ls_label = format_long_short(long_pct, short_pct)
    ls_color = "#3fb950" if long_pct >= short_pct else "#f85149"

    # Derive readable base name from symbol
    ticker = symbol.split(":")[-1].replace(".P", "")
    for suffix in ("USDT", "USD", "USDC"):
        if ticker.endswith(suffix):
            base = ticker[:-len(suffix)]
            break
    else:
        base = ticker

    icon      = "🟢" if scan_row.get("alert_triggered") else ("🟡" if scan_row.get("signal_active") else "⚪")
    canvas_id = f"s{idx}"

    # TF toggle — show only when more than one TF is available; 1d first, then 4h
    available_tfs = sorted(candles_by_tf.keys(), key=lambda t: (t != "4h", t))
    tf_toggle_html = ""
    if len(available_tfs) > 1:
        buttons = ""
        for tf in available_tfs:
            active_cls = " active" if tf == default_tf else ""
            label = tf.upper()
            buttons += f'<button class="tf-btn{active_cls}" data-tf="{esc(tf)}">{label}</button>'
        tf_toggle_html = f'<div class="tf-toggle">{buttons}</div>'

    liq_data_attrs = " ".join(
        f'data-liq-long-{esc(tf)}="{safe_float(v.get("long")):.0f}" '
        f'data-liq-short-{esc(tf)}="{safe_float(v.get("short")):.0f}"'
        for tf, v in liqs_by_tf.items()
    )

    # On mobile we duplicate the TF toggle as an overlay on the price chart and
    # render a floating "← Overview" button instead of taking header space.
    # The existing click handler syncs all .tf-btn copies within a slide.
    tf_overlay_html = tf_toggle_html.replace('class="tf-toggle"', 'class="tf-toggle tf-toggle-overlay"', 1)
    zoom_reset_html = '<button class="zoom-reset" title="Reset zoom" aria-label="Reset zoom">&#8596;</button>'
    zoom_reset_overlay_html = '<button class="zoom-reset zoom-reset-overlay" title="Reset zoom" aria-label="Reset zoom">&#8596;</button>'

    return f"""
    <section class="slide" id="slide-{idx}" data-idx="{idx}" data-symbol="{esc(symbol)}" data-default-tf="{esc(default_tf)}" {liq_data_attrs}>
      <div class="slide-header crypto-header">
        <div class="crypto-title">
          <button class="back-btn" data-goto="0" title="Back to overview">&#8592; Overview</button>
          <span class="crypto-icon">{icon}</span>
          <span class="crypto-base">{esc(base)}</span>
          <span class="crypto-exchange">{esc(exchange)}</span>
        </div>
        <div class="crypto-meta">
          <span class="crypto-price">{esc(format_price(close))}</span>
          <span class="metric-chip change-chip">Day&nbsp;{esc(format_pct(change))}</span>
          <span class="metric-chip ls-chip" style="color:{ls_color}" title="Top traders long / short accounts">L/S&nbsp;{esc(ls_label)}</span>
          <table class="liq-summary">
            <tbody>
              <tr><th>Longs liquidated</th><td class="liq-long">{esc(format_money(totals["long"]))}</td></tr>
              <tr><th>Shorts liquidated</th><td class="liq-short">{esc(format_money(totals["short"]))}</td></tr>
            </tbody>
          </table>
          {tf_toggle_html}
          {zoom_reset_html}
        </div>
      </div>
      <div class="charts-grid">
        <div class="chart-box price-box">
          <div class="chart-label">Price</div>
          {tf_overlay_html}
          {zoom_reset_overlay_html}
          <canvas id="price-{canvas_id}"></canvas>
        </div>
        <div class="chart-box oi-box">
          <div class="chart-label">Open Interest</div>
          <canvas id="oi-{canvas_id}"></canvas>
        </div>
        <div class="chart-box vol-box">
          <div class="chart-label">Volume</div>
          <canvas id="vol-{canvas_id}"></canvas>
        </div>
        <div class="chart-box funding-box">
          <div class="chart-label">Funding (bars) · Basis (line) — bps</div>
          <canvas id="fr-{canvas_id}"></canvas>
        </div>
      </div>
    </section>"""


# ---------------------------------------------------------------------------
# CSS / JS constants (not f-strings to avoid escaping issues)
# ---------------------------------------------------------------------------

STATIC_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html, body {
  height: 100%; overflow: hidden;
  background: #0d1117; color: #e6edf3;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 14px;
}

/* ── Scroll container ── */
#slides {
  height: 100vh;
  overflow-y: scroll;
  scroll-snap-type: y mandatory;
  scroll-behavior: smooth;
}
#slides::-webkit-scrollbar { display: none; }

/* ── Each slide ── */
.slide {
  height: 100vh;
  scroll-snap-align: start;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ── Header strip ── */
.slide-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 14px;
  background: #161b22;
  border-bottom: 1px solid #30363d;
  flex-shrink: 0;
  gap: 8px;
  flex-wrap: wrap;
  min-height: 46px;
}
.events-title { display: flex; align-items: center; gap: 8px; font-size: 17px; font-weight: 700; }
.logo { font-size: 20px; }
.scan-time { color: #8b949e; font-size: 11px; }
.header-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
.header-link {
  color: #58a6ff; text-decoration: none; font-size: 11px; font-weight: 800;
  background: #21262d; border: 1px solid #30363d; border-radius: 6px;
  padding: 4px 8px;
}
.header-link:hover { color: #79c0ff; border-color: #58a6ff; background: #30363d; }

.crypto-header { gap: 6px; }
.crypto-title { display: flex; align-items: center; gap: 8px; }
.back-btn {
  background: #21262d; color: #58a6ff; border: 1px solid #30363d;
  padding: 3px 8px; font-size: 11px; font-weight: 700; border-radius: 6px;
  cursor: pointer; transition: all 0.15s;
}
.back-btn:hover { background: #30363d; color: #79c0ff; border-color: #58a6ff; }
.ls-chip { font-size: 11px; }
.crypto-icon { font-size: 14px; }
.crypto-base { font-size: 20px; font-weight: 800; letter-spacing: -0.5px; }
.crypto-exchange { color: #8b949e; font-size: 10px; background: #21262d; padding: 2px 6px; border-radius: 4px; }
.crypto-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.crypto-price { font-size: 15px; font-weight: 700; color: #58a6ff; }
.metric-chip { font-size: 12px; font-weight: 600; white-space: nowrap; }
.change-chip { color: #e6edf3; }
.liq-summary {
  border-collapse: collapse;
  font-size: 10px;
  line-height: 1.15;
  background: #0d1117;
  border: 1px solid #30363d;
  border-radius: 6px;
  overflow: hidden;
}
.liq-summary th {
  color: #8b949e;
  font-weight: 600;
  text-align: left;
  padding: 2px 7px;
  white-space: nowrap;
}
.liq-summary td {
  color: #e6edf3;
  font-weight: 700;
  text-align: right;
  padding: 2px 7px;
  white-space: nowrap;
}
.liq-summary tr + tr th,
.liq-summary tr + tr td { border-top: 1px solid #21262d; }

/* ── TF toggle ── */
.tf-toggle { display: flex; gap: 2px; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 2px; }
.tf-btn { background: transparent; color: #8b949e; border: 0; padding: 3px 9px; font-size: 11px; font-weight: 700; border-radius: 4px; cursor: pointer; }
.tf-btn:hover { color: #e6edf3; }
.tf-btn.active { background: #21262d; color: #58a6ff; }

/* ── Badges ── */
.badge { padding: 2px 7px; border-radius: 12px; font-size: 10px; font-weight: 700; white-space: nowrap; }
.badge-negative { background: #1a2f4b; color: #79c0ff; }
.badge-neutral  { background: #21262d; color: #8b949e; }
.badge-positive { background: #0d2a1a; color: #3fb950; }
.badge-hot      { background: #2d1b00; color: #d29922; }
.badge-extreme  { background: #2d0d0d; color: #f85149; }
.badge-unknown  { background: #21262d; color: #6e7681; }

/* ── Events slide body ── */
.slide-body {
  flex: 1; overflow-y: auto; padding: 12px 14px;
  display: flex; flex-direction: column; gap: 14px;
}
.section-label {
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  color: #8b949e; margin-bottom: 8px; letter-spacing: 0.08em;
}
.signal-cards-grid { display: flex; flex-wrap: wrap; gap: 8px; }
.signal-card {
  background: #161b22; border: 1px solid #30363d; border-radius: 8px;
  padding: 10px 12px; min-width: 160px; flex: 1 1 160px;
  transition: border-color 0.15s;
}
.signal-card:hover { border-color: #58a6ff; }
.signal-card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; gap: 6px; }
.signal-sym { font-size: 12px; font-weight: 700; }
.signal-metrics { display: flex; gap: 10px; font-size: 12px; font-weight: 600; }
.no-signals { color: #8b949e; font-style: italic; font-size: 13px; }

.events-table-wrap { overflow-x: auto; }
.events-table { width: 100%; border-collapse: collapse; font-size: 11px; }
.events-table th { text-align: left; padding: 5px 7px; color: #8b949e; border-bottom: 1px solid #30363d; white-space: nowrap; }
.events-table td { padding: 4px 7px; border-bottom: 1px solid #21262d; white-space: nowrap; }
.sym-cell { font-weight: 700; color: #58a6ff; }
.sym-link  { cursor: pointer; text-decoration: underline dotted; }
.sym-link:hover { color: #79c0ff; background: rgba(88,166,255,0.08); }
.event-type-badge { padding: 1px 5px; border-radius: 4px; font-size: 9px; font-weight: 700; }
.et-entry { background: #0d2a1a; color: #3fb950; }
.et-hot   { background: #2d1b00; color: #d29922; }
.et-oi    { background: #1a2f4b; color: #79c0ff; }
.et-vol   { background: #2a1a4b; color: #d2a8ff; }
.et-squeeze { background: #3b1d2e; color: #f778ba; }
.et-pre   { background: #1a1f29; color: #79c0ff; }

/* ── Overview table ── */
.overview-table { width: 100%; border-collapse: collapse; font-size: 11px; }
.overview-table th { text-align: left; padding: 5px 7px; color: #8b949e; border-bottom: 1px solid #30363d; white-space: nowrap; position: sticky; top: 0; background: #0d1117; }
.overview-table td { padding: 4px 7px; border-bottom: 1px solid #21262d; white-space: nowrap; }
.overview-table tbody tr:hover { background: rgba(88,166,255,0.04); }
.sig-cell { display: flex; gap: 4px; flex-wrap: wrap; }
.sig-chip { padding: 1px 5px; border-radius: 4px; font-size: 9px; font-weight: 700; }
.sig-entry { background: #0d2a1a; color: #3fb950; }
.sig-oi    { background: #1a2f4b; color: #79c0ff; }
.sig-vol   { background: #2a1a4b; color: #d2a8ff; }
.sig-squeeze { background: #3b1d2e; color: #f778ba; }
.last-event-cell { white-space: nowrap; }
.event-date { color: #6e7681; font-size: 10px; }
.muted { color: #6e7681; }

/* ── Charts vertical stack ── */
.charts-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 4.2fr) minmax(0, 1.6fr) minmax(0, 0.9fr) minmax(0, 0.9fr);
  grid-template-areas: "price" "oi" "volume" "funding";
  gap: 5px;
  flex: 1;
  padding: 5px;
  min-height: 0;
}
.chart-box {
  background: #161b22; border: 1px solid #30363d; border-radius: 6px;
  padding: 7px; display: flex; flex-direction: column; min-height: 0; overflow: hidden;
  position: relative;
}
.price-box { grid-area: price; }
.oi-box { grid-area: oi; }
.vol-box { grid-area: volume; padding-top: 5px; padding-bottom: 5px; }
.funding-box { grid-area: funding; padding-top: 5px; padding-bottom: 5px; }
.chart-label {
  font-size: 9px; font-weight: 700; text-transform: uppercase;
  color: #8b949e; margin-bottom: 3px; letter-spacing: 0.07em; flex-shrink: 0;
}
.chart-box canvas { flex: 1; min-height: 0; display: block; width: 100% !important; cursor: crosshair; }
.zoom-reset {
  background: #21262d; color: #8b949e; border: 1px solid #30363d;
  width: 28px; height: 26px; border-radius: 6px;
  font-size: 13px; font-weight: 800; line-height: 1;
  display: inline-flex; align-items: center; justify-content: center;
  cursor: pointer;
}
.zoom-reset:hover { color: #58a6ff; border-color: #58a6ff; background: #30363d; }
.zoom-reset-overlay { display: none; }
.zoom-selection {
  display: none;
  position: absolute;
  z-index: 6;
  pointer-events: none;
  background: rgba(88,166,255,0.18);
  border: 1px solid rgba(88,166,255,0.9);
  border-radius: 3px;
  box-shadow: 0 0 0 1px rgba(88,166,255,0.18);
}

/* ── Nav dots ── */
#nav-dots {
  position: fixed; right: 8px; top: 50%;
  transform: translateY(-50%);
  display: flex; flex-direction: column; gap: 5px;
  z-index: 999; max-height: 85vh; overflow: hidden;
}
.nav-dot {
  width: 5px; height: 5px; border-radius: 50%;
  background: rgba(255,255,255,0.18);
  cursor: pointer; transition: all 0.2s; flex-shrink: 0;
}
.nav-dot.active { background: #58a6ff; transform: scale(1.7); }
.nav-dot:hover  { background: rgba(255,255,255,0.5); }

/* ── Slide counter ── */
#slide-counter {
  position: fixed; left: 10px; bottom: 12px;
  color: #8b949e; font-size: 10px; z-index: 999; pointer-events: none;
}

/* ── Swipe hint (mobile) ── */
#swipe-hint {
  position: fixed; bottom: 12px; left: 50%; transform: translateX(-50%);
  color: #6e7681; font-size: 10px; z-index: 998; pointer-events: none;
  opacity: 1; transition: opacity 2s;
}

/* ── Mobile-only floating UI (hidden on desktop) ── */
.tf-toggle-overlay { display: none; }
#back-to-overview {
  display: none;
  position: fixed;
  right: 14px; bottom: 14px;
  z-index: 1000;
  background: #21262d; color: #58a6ff; border: 1px solid #30363d;
  padding: 7px 12px; font-size: 12px; font-weight: 700;
  border-radius: 999px; cursor: pointer;
  box-shadow: 0 4px 12px rgba(0,0,0,0.35);
}
#back-to-overview:hover { background: #30363d; color: #79c0ff; border-color: #58a6ff; }
body.show-back-btn #back-to-overview { display: inline-flex; align-items: center; }

/* ── Mobile: keep the same vertical reading order on narrow screens ── */
@media (max-width: 420px) {
  .charts-grid {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 4.2fr) minmax(0, 1.6fr) minmax(0, 0.9fr) minmax(0, 0.9fr);
  }
  #nav-dots { display: none; }

  /* Header on mobile: no back button, no inline TF toggle — those move to
     a floating button and a chart overlay respectively to free up space. */
  .crypto-header > .crypto-title > .back-btn,
  .crypto-header > .crypto-meta  > .tf-toggle,
  .crypto-header > .crypto-meta  > .zoom-reset { display: none; }

  .slide-header.crypto-header {
    flex-wrap: nowrap;
    gap: 5px;
    padding: 4px 7px;
    min-height: 38px;
    overflow: hidden;
  }
  .crypto-title { gap: 4px; flex-shrink: 0; }
  .crypto-meta  { gap: 4px; flex-wrap: nowrap; min-width: 0; }
  .crypto-icon, .crypto-exchange { display: none; }
  .crypto-base  { font-size: 14px; }
  .crypto-price { font-size: 12px; }
  .metric-chip { font-size: 10px; }

  /* Flatten liq-summary: two <tr> become two inline pills on one line.
     The "Longs/Shorts liquidated" labels collapse to a single L:/S: prefix. */
  .liq-summary { border: 0; background: transparent; }
  .liq-summary tbody { display: flex; gap: 6px; }
  .liq-summary tr { display: inline-flex; align-items: center; gap: 2px; }
  .liq-summary tr + tr th, .liq-summary tr + tr td { border-top: 0; }
  .liq-summary th { display: none; }
  .liq-summary td { padding: 0; font-size: 10px; }
  .liq-summary tr:first-child td::before { content: "L"; color: #f85149; margin-right: 3px; font-weight: 700; }
  .liq-summary tr:last-child td::before  { content: "S"; color: #3fb950; margin-right: 3px; font-weight: 700; }

  /* TF toggle becomes an overlay on the price chart, top-right. */
  .chart-box.price-box { position: relative; }
  .tf-toggle-overlay {
    display: flex;
    position: absolute;
    top: 4px; right: 4px;
    z-index: 5;
    background: rgba(13,17,23,0.85);
    backdrop-filter: blur(2px);
  }
  .tf-toggle-overlay .tf-btn { padding: 2px 8px; font-size: 10px; }
  .zoom-reset-overlay {
    display: inline-flex;
    position: absolute;
    top: 4px; right: 76px;
    z-index: 5;
    width: 26px; height: 22px;
    background: rgba(13,17,23,0.85);
    backdrop-filter: blur(2px);
  }
}
"""

STATIC_JS = r"""
(function () {
  const slidesEl  = document.getElementById('slides');
  const slides    = Array.from(document.querySelectorAll('.slide'));
  const dots      = Array.from(document.querySelectorAll('.nav-dot'));
  const counter   = document.getElementById('slide-counter');
  const hint      = document.getElementById('swipe-hint');
  const N         = slides.length;
  let   current   = 0;
  const inited    = new Set();
  let   wheelLock = false;
  let   touchStartY = null;

  // Exposed globally so onclick="window.goTo(N)" in event table works
  window.goTo = function (idx) {
    idx = Math.max(0, Math.min(N - 1, idx));
    slidesEl.scrollTo({ top: slides[idx].offsetTop, behavior: 'smooth' });
  };

  function updateUI(idx) {
    current = idx;
    dots.forEach((d, i) => d.classList.toggle('active', i === idx));
    counter.textContent = (idx + 1) + ' / ' + N;
    document.body.classList.toggle('show-back-btn', idx > 0);
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); window.goTo(current + 1); }
    if (e.key === 'ArrowUp'   || e.key === 'k') { e.preventDefault(); window.goTo(current - 1); }
  });

  dots.forEach((d, i) => d.addEventListener('click', () => window.goTo(i)));

  function stepSlides(direction) {
    const next = Math.max(0, Math.min(N - 1, current + direction));
    if (next !== current) window.goTo(next);
  }

  slidesEl.addEventListener('wheel', (e) => {
    if (Math.abs(e.deltaY) < Math.abs(e.deltaX) || Math.abs(e.deltaY) < 18) return;
    e.preventDefault();
    if (wheelLock) return;
    wheelLock = true;
    stepSlides(e.deltaY > 0 ? 1 : -1);
    window.setTimeout(() => { wheelLock = false; }, 450);
  }, { passive: false });

  slidesEl.addEventListener('touchstart', (e) => {
    touchStartY = e.changedTouches[0].clientY;
  }, { passive: true });

  slidesEl.addEventListener('touchend', (e) => {
    if (touchStartY === null) return;
    const deltaY = touchStartY - e.changedTouches[0].clientY;
    touchStartY = null;
    if (Math.abs(deltaY) < 45) return;
    stepSlides(deltaY > 0 ? 1 : -1);
  }, { passive: true });

  const io = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        const idx = parseInt(entry.target.dataset.idx, 10);
        updateUI(idx);
        if (!inited.has(idx)) { inited.add(idx); initCharts(entry.target, idx); }
        if (hint) hint.style.opacity = '0';
      }
    }
  }, { root: slidesEl, threshold: 0.5 });

  slides.forEach(s => io.observe(s));
  updateUI(0);

  // ── Liquidation totals: swap header values to match active TF ───────────
  function formatMoney(v) {
    v = +v || 0;
    if (v >= 1e9) return '$' + (v / 1e9).toFixed(1) + 'B';
    if (v >= 1e6) return '$' + (v / 1e6).toFixed(1) + 'M';
    if (v >= 1e3) return '$' + Math.round(v / 1e3) + 'K';
    if (v > 0)    return '$' + Math.round(v).toLocaleString();
    return '$0';
  }
  function syncLiquidationsForTf(slide, tf) {
    const longCell  = slide.querySelector('.liq-long');
    const shortCell = slide.querySelector('.liq-short');
    if (!longCell || !shortCell) return;
    const longVal  = slide.dataset['liqLong'  + tf.toUpperCase()];
    const shortVal = slide.dataset['liqShort' + tf.toUpperCase()];
    if (longVal  !== undefined) longCell.textContent  = formatMoney(longVal);
    if (shortVal !== undefined) shortCell.textContent = formatMoney(shortVal);
  }

  // ── TF toggle handler — syncs all slides ────────────────────────────────
  slidesEl.addEventListener('click', (e) => {
    const resetBtn = e.target.closest('.zoom-reset');
    if (resetBtn) {
      const slide = resetBtn.closest('.slide');
      if (slide) resetZoom(slide);
      return;
    }

    const btn = e.target.closest('.tf-btn');
    if (!btn) return;
    const clickedSlide = btn.closest('.slide');
    if (!clickedSlide) return;
    const tf = btn.dataset.tf;
    if (clickedSlide.dataset.currentTf === tf) return;
    slides.forEach(slide => {
      const idx = parseInt(slide.dataset.idx, 10);
      if (idx === 0) return;
      slide.dataset.currentTf = tf;
      slide.querySelectorAll('.tf-btn').forEach(b => b.classList.toggle('active', b.dataset.tf === tf));
      syncLiquidationsForTf(slide, tf);
      if (inited.has(idx)) {
        if (slide._charts) {
          Object.values(slide._charts).forEach(c => { try { c && c.destroy(); } catch (_) {} });
          slide._charts = null;
        }
        initCharts(slide, idx);
      }
    });
  });

  // ── Shared scale / plugin defaults ──────────────────────────────────────
  function deepClone(o) { return JSON.parse(JSON.stringify(o)); }

  function timeScale(min, max, { showTicks = true } = {}) {
    const scale = {
      type: 'time',
      min: min, max: max,
      time: { unit: 'day', displayFormats: { day: 'MMM d' } },
      ticks: { color: '#6e7681', font: { size: 8 }, maxTicksLimit: 5, maxRotation: 0 },
      grid:  { color: 'rgba(48,54,61,0.6)' },
    };
    if (!showTicks) {
      scale.ticks.display = false;
      scale.grid.drawTicks = false;
      scale.border = { display: false };
    }
    return scale;
  }
  const SCALE_Y = {
    display: true, position: 'right',
    ticks: { color: '#6e7681', font: { size: 8 }, maxTicksLimit: 4 },
    grid:  { color: 'rgba(48,54,61,0.6)' },
  };

  function chartsForSlide(slideEl) {
    return slideEl && slideEl._charts
      ? Object.values(slideEl._charts).filter(Boolean)
      : [];
  }

  function finiteValue(v) {
    const n = +v;
    return Number.isFinite(n) ? n : null;
  }

  function pointValues(raw) {
    if (!raw || typeof raw !== 'object') return [];
    const values = [];
    ['y', 'o', 'h', 'l', 'c'].forEach(key => {
      const value = finiteValue(raw[key]);
      if (value !== null) values.push(value);
    });
    return values;
  }

  function valueRangeForDataset(dataset, min, max) {
    const values = [];
    (dataset.data || []).forEach(raw => {
      const x = finiteValue(raw && raw.x);
      if (x === null || x < min || x > max) return;
      values.push(...pointValues(raw));
    });
    return values;
  }

  function setVisibleYScale(chart, min, max) {
    if (!chart || !chart.options || !chart.options.scales) return;
    Object.entries(chart.options.scales).forEach(([scaleId, scale]) => {
      if (scaleId === 'x') return;
      if (scaleId === 'yls') {
        scale.min = 0;
        scale.max = 1;
        return;
      }

      const values = [];
      (chart.data.datasets || []).forEach(dataset => {
        const axisId = dataset.yAxisID || 'y';
        if (axisId !== scaleId) return;
        values.push(...valueRangeForDataset(dataset, min, max));
      });
      if (!values.length) return;

      const rawLow = Math.min(...values);
      const rawHigh = Math.max(...values);
      let low = rawLow;
      let high = rawHigh;
      if (!Number.isFinite(low) || !Number.isFinite(high)) return;
      if (low === high) {
        const bump = Math.max(Math.abs(high) * 0.05, 1);
        low -= bump;
        high += bump;
      }
      const span = high - low;
      low -= span * 0.08;
      high += span * 0.08;

      const includeZero = chart.config.type === 'bar' || scale.beginAtZero;
      if (includeZero && rawLow >= 0) low = 0;
      if (includeZero && rawHigh <= 0) high = 0;

      delete scale.suggestedMin;
      delete scale.suggestedMax;
      scale.min = low;
      scale.max = high;
    });
  }

  function applyZoomRange(slideEl, min, max) {
    if (!slideEl || !Number.isFinite(min) || !Number.isFinite(max)) return;
    const full = slideEl._fullRange || { min, max };
    let lo = Math.max(full.min, Math.min(min, max));
    let hi = Math.min(full.max, Math.max(min, max));
    if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return;

    slideEl._zoomRange = { min: lo, max: hi };
    chartsForSlide(slideEl).forEach(chart => {
      if (!chart.options.scales || !chart.options.scales.x) return;
      chart.options.scales.x.min = lo;
      chart.options.scales.x.max = hi;
      setVisibleYScale(chart, lo, hi);
      chart.update('none');
    });
    slideEl.querySelectorAll('.zoom-selection').forEach(hideZoomSelection);
  }

  function resetZoom(slideEl) {
    if (!slideEl || !slideEl._fullRange) return;
    const full = slideEl._fullRange;
    slideEl._zoomRange = null;
    chartsForSlide(slideEl).forEach(chart => {
      if (!chart.options.scales || !chart.options.scales.x) return;
      chart.options.scales.x.min = full.min;
      chart.options.scales.x.max = full.max;
      Object.entries(chart.options.scales).forEach(([scaleId, scale]) => {
        if (scaleId === 'x') return;
        if (scaleId === 'yls') {
          scale.min = 0;
          scale.max = 1;
          return;
        }
        delete scale.min;
        delete scale.max;
      });
      chart.update('none');
    });
    slideEl.querySelectorAll('.zoom-selection').forEach(hideZoomSelection);
  }

  function zoomSelectionBox(chart) {
    const parent = chart.canvas.parentElement;
    let selection = parent.querySelector('.zoom-selection');
    if (!selection) {
      selection = document.createElement('div');
      selection.className = 'zoom-selection';
      parent.appendChild(selection);
    }
    return selection;
  }

  function positionZoomSelection(chart, selection, startX, currentX) {
    if (!chart || !selection) return;
    const canvasBox = chart.canvas.getBoundingClientRect();
    const parentBox = chart.canvas.parentElement.getBoundingClientRect();
    const area = chart.chartArea;
    const leftBound = canvasBox.left + area.left;
    const rightBound = canvasBox.left + area.right;
    const top = canvasBox.top + area.top - parentBox.top;
    const height = Math.max(0, area.bottom - area.top);
    const x1 = Math.max(leftBound, Math.min(rightBound, startX));
    const x2 = Math.max(leftBound, Math.min(rightBound, currentX));
    selection.style.display = 'block';
    selection.style.left = (Math.min(x1, x2) - parentBox.left) + 'px';
    selection.style.top = top + 'px';
    selection.style.width = Math.abs(x2 - x1) + 'px';
    selection.style.height = height + 'px';
  }

  function hideZoomSelection(selection) {
    if (!selection) return;
    selection.style.display = 'none';
  }

  function attachZoomSelection(slideEl, chart) {
    if (!slideEl || !chart || !chart.canvas || chart.canvas._zoomSelectionAttached) return;
    const canvas = chart.canvas;
    canvas._zoomSelectionAttached = true;
    let drag = null;

    function activeChart() {
      return Chart.getChart(canvas) || chart;
    }

    function xValueForClientX(active, clientX) {
      const canvasBox = canvas.getBoundingClientRect();
      const area = active.chartArea;
      const pixel = Math.max(area.left, Math.min(area.right, clientX - canvasBox.left));
      return active.scales.x.getValueForPixel(pixel);
    }

    function finishDrag(e, commit) {
      if (!drag) return;
      const active = activeChart();
      const width = Math.abs((e && e.clientX !== undefined ? e.clientX : drag.currentX) - drag.startX);
      if (commit && active && active.scales && active.scales.x && width >= 8) {
        const min = xValueForClientX(active, drag.startX);
        const max = xValueForClientX(active, e.clientX);
        applyZoomRange(slideEl, min, max);
      } else {
        hideZoomSelection(drag.selection);
      }
      try { canvas.releasePointerCapture(e.pointerId); } catch (_) {}
      drag = null;
    }

    canvas.addEventListener('pointerdown', (e) => {
      if (e.pointerType && e.pointerType !== 'mouse') return;
      if (e.button !== 0) return;
      const active = activeChart();
      if (!active || !active.scales || !active.scales.x) return;
      const canvasBox = canvas.getBoundingClientRect();
      const area = active.chartArea;
      const leftBound = canvasBox.left + area.left;
      const rightBound = canvasBox.left + area.right;
      if (e.clientX < leftBound || e.clientX > rightBound) return;
      e.preventDefault();
      e.stopPropagation();
      drag = {
        startX: e.clientX,
        currentX: e.clientX,
        selection: zoomSelectionBox(active),
      };
      positionZoomSelection(active, drag.selection, drag.startX, drag.currentX);
      try { canvas.setPointerCapture(e.pointerId); } catch (_) {}
    });

    canvas.addEventListener('pointermove', (e) => {
      if (!drag) return;
      e.preventDefault();
      const active = activeChart();
      drag.currentX = e.clientX;
      positionZoomSelection(active, drag.selection, drag.startX, drag.currentX);
    });

    canvas.addEventListener('pointerup', (e) => finishDrag(e, true));
    canvas.addEventListener('pointercancel', (e) => finishDrag(e, false));
  }

  // ── Japanese candlestick chart (price or OI) ─────────────────────────────
  function candleChart(id, candleData, xMin, xMax, { showXTicks = true } = {}) {
    const canvas = document.getElementById(id);
    if (!canvas || !candleData.length) return null;
    return new Chart(canvas.getContext('2d'), {
      type: 'candlestick',
      data: {
        datasets: [{
          data: candleData,
          color: { up: '#3fb950', down: '#f85149', unchanged: '#8b949e' },
          borderColor: { up: '#3fb950', down: '#f85149', unchanged: '#8b949e' },
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        animation: { duration: 200 },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const r = ctx.raw;
                const f = (v) => v >= 1000
                  ? (+v).toLocaleString('en', { maximumFractionDigits: 0 })
                  : String(+(+v).toPrecision(5));
                return [`O: ${f(r.o)}`, `H: ${f(r.h)}`, `L: ${f(r.l)}`, `C: ${f(r.c)}`];
              },
            },
          },
        },
        scales: { x: timeScale(xMin, xMax, { showTicks: showXTicks }), y: deepClone(SCALE_Y) },
      },
    });
  }

  // ── Bar chart on a time axis (volume) ────────────────────────────────────
  function compactBarDataset(points, colors) {
    return {
      data: points,
      backgroundColor: colors,
      borderWidth: 0,
      barPercentage: 1.0,
      categoryPercentage: 1.0,
      minBarLength: 2,
    };
  }

  function barYScale({ symmetric = false } = {}) {
    const scale = deepClone(SCALE_Y);
    scale.ticks.maxTicksLimit = 3;
    scale.grace = '8%';
    if (symmetric) {
      scale.suggestedMin = -1;
      scale.suggestedMax = 1;
    } else {
      scale.beginAtZero = true;
    }
    return scale;
  }

  function barChart(id, points, color, xMin, xMax) {
    const canvas = document.getElementById(id);
    if (!canvas) return null;
    return new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: { datasets: [compactBarDataset(points, color + 'cc')] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 200 },
        plugins: { legend: { display: false } },
        scales: { x: timeScale(xMin, xMax, { showTicks: false }), y: barYScale() },
      },
    });
  }

  // ── Funding rate bars + basis (perp premium) line, both in bps ───────────
  function fundingChart(id, points, xMin, xMax, basisPoints) {
    const canvas = document.getElementById(id);
    if (!canvas) return null;
    const colors = points.map(p => p.y >= 0 ? '#d29922cc' : '#f85149cc');
    const datasets = [compactBarDataset(points, colors)];
    if (basisPoints && basisPoints.length) {
      datasets.push({
        type: 'line',
        data: basisPoints,
        borderColor: '#79c0ff',
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.2,
        order: 0,
      });
    }
    return new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: { datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 200 },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (i) => (i.dataset.type === 'line' ? 'basis ' : 'funding ') + (+i.raw.y).toFixed(2) + ' bps' } },
        },
        scales: { x: timeScale(xMin, xMax, { showTicks: false }), y: barYScale({ symmetric: true }) },
      },
    });
  }

  // ── Init charts for a given slide ────────────────────────────────────────
  function initCharts(slideEl, idx) {
    if (idx === 0) return;

    const symbol     = slideEl.dataset.symbol;
    const tf         = slideEl.dataset.currentTf || slideEl.dataset.defaultTf || '4h';
    const chartsByTf = (typeof CHART_DATA !== 'undefined') ? CHART_DATA[symbol] : null;

    // Support both old flat shape (array) and new nested shape ({tf: array})
    let raw;
    if (!chartsByTf) {
      raw = null;
    } else if (Array.isArray(chartsByTf)) {
      raw = chartsByTf;
    } else {
      raw = chartsByTf[tf] || chartsByTf[Object.keys(chartsByTf)[0]] || null;
    }
    if (!raw || !raw.length) return;

    const id  = 's' + idx;

    // Price candlestick first — its full time range becomes the shared x-axis
    // for all 4 subplots so the user can read price ↔ OI ↔ vol ↔ funding at
    // the same x position even when OI/funding have shorter history.
    const priceCandles = raw
      .filter(d => +d.open && +d.high && +d.low && +d.close)
      .map(d => ({ x: Date.parse(d.timestamp), o: +d.open, h: +d.high, l: +d.low, c: +d.close }));
    if (!priceCandles.length) return;
    const xMin = priceCandles[0].x;
    const xMax = priceCandles[priceCandles.length - 1].x;
    const priceChart = candleChart('price-' + id, priceCandles, xMin, xMax, { showXTicks: true });

    // OI candlestick — uses oi_open/high/low/close when available
    let oiChart;
    const hasOiOhlc = raw.some(d => +d.oi_open > 0);
    if (hasOiOhlc) {
      const oiCandles = raw
        .filter(d => +d.oi_open > 0)
        .map(d => ({ x: Date.parse(d.timestamp), o: +d.oi_open, h: +d.oi_high, l: +d.oi_low, c: +(d.oi_close || d.open_interest) }));
      oiChart = candleChart('oi-' + id, oiCandles, xMin, xMax, { showXTicks: false });
    } else {
      // Fallback: simple line with open_interest, also pinned to the price range
      const oiPoints = raw
        .filter(d => +(d.open_interest || 0) > 0)
        .map(d => ({ x: Date.parse(d.timestamp), y: +d.open_interest }));
      const cvs = document.getElementById('oi-' + id);
      if (cvs && oiPoints.length) oiChart = new Chart(cvs.getContext('2d'), {
        type: 'line',
        data: { datasets: [{ data: oiPoints, borderColor: '#3fb950', backgroundColor: '#3fb95018', borderWidth: 1.5, pointRadius: 0, fill: true, tension: 0.15 }] },
        options: {
          responsive: true, maintainAspectRatio: false, animation: { duration: 200 },
          plugins: { legend: { display: false } },
          scales: { x: timeScale(xMin, xMax, { showTicks: false }), y: deepClone(SCALE_Y) },
        },
      });
    }

    const volPoints = raw
      .filter(d => +(d.volume || 0) > 0)
      .map(d => ({ x: Date.parse(d.timestamp), y: +d.volume }));
    const frPoints = raw
      .filter(d => Number.isFinite(+(d.funding_rate || 0)))
      .map(d => ({ x: Date.parse(d.timestamp), y: Math.round(+(d.funding_rate || 0) * 1e6) / 100 }));
    const hasBasis = raw.some(d => d.basis_pct !== undefined && +d.basis_pct !== 0);
    const basisPoints = hasBasis
      ? raw.filter(d => Number.isFinite(+(d.basis_pct || 0)))
           .map(d => ({ x: Date.parse(d.timestamp), y: Math.round(+(d.basis_pct || 0) * 1e6) / 100 }))
      : [];

    const volChart = barChart   ('vol-' + id, volPoints, '#8b949e', xMin, xMax);
    const frChart  = fundingChart('fr-' + id, frPoints, xMin, xMax, basisPoints);

    // L/S ratio overlay on price chart
    const lsLongPts  = raw.filter(d => +d.ls_long  > 0).map(d => ({ x: Date.parse(d.timestamp), y: +d.ls_long  }));
    const lsShortPts = raw.filter(d => +d.ls_short > 0).map(d => ({ x: Date.parse(d.timestamp), y: +d.ls_short }));
    if (lsLongPts.length && priceChart) {
      const lsDataset = (data, color) => ({
        type: 'line',
        data,
        borderColor: color,
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.2,
        yAxisID: 'yls',
        order: 0,
      });
      priceChart.data.datasets.push(lsDataset(lsLongPts,  'rgba(63,185,80,0.7)'));
      priceChart.data.datasets.push(lsDataset(lsShortPts, 'rgba(248,81,73,0.7)'));
      priceChart.options.scales.yls = {
        display: true,
        position: 'left',
        min: 0, max: 1,
        ticks: {
          color: '#6e7681', font: { size: 7 }, maxTicksLimit: 3,
          callback: v => Math.round(v * 100) + '%',
        },
        grid: { display: false },
      };
      priceChart.update('none');
    }

    slideEl._charts = { price: priceChart, oi: oiChart, vol: volChart, fr: frChart };
    slideEl._fullRange = { min: xMin, max: xMax };
    slideEl._zoomRange = null;
    chartsForSlide(slideEl).forEach(chart => attachZoomSelection(slideEl, chart));
  }
})();
"""


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------

def build_html(
    events: list[dict],
    scan: dict[str, dict[str, dict]],
    charts: dict[str, dict[str, list]],
    liquidations: dict[str, dict[str, list]] | None = None,
) -> str:
    # Normalize inputs: accept both old flat {sym: list/row} and new nested {sym: {tf: ...}}
    charts      = _normalize_charts_input(charts)
    scan        = _normalize_scan_input(scan)
    liquidations = _normalize_liqs_input(liquidations or {})

    # Build fallback rows from chart and event data (one row per symbol).
    # Prefer 4h candles so the 3-bar surge fallbacks match the scanner.
    fallback_rows: dict[str, dict] = {}
    for symbol, candles_by_tf in charts.items():
        if not candles_by_tf:
            continue
        candles = (
            candles_by_tf.get("4h")
            or candles_by_tf.get("1h")
            or candles_by_tf.get("1d")
            or next(iter(candles_by_tf.values()), [])
        )
        if candles:
            fallback_rows[symbol] = row_from_chart(symbol, candles)
    for event in events:
        raw_symbol = str(event.get("raw_symbol", ""))
        if raw_symbol and raw_symbol not in fallback_rows:
            fallback_rows[raw_symbol] = row_from_event(event)

    # Build per-crypto slides from live scan rows when possible, then fall back
    # to chart/event data so GitHub Pages remains navigable after cloud API blocks.
    slide_symbols = {
        sym for sym, by_tf in scan.items()
        if any(positive_float(r.get("close", 0)) for r in by_tf.values()) or sym in fallback_rows
    }
    slide_symbols.update(sym for sym, row in fallback_rows.items() if positive_float(row.get("close", 0)))
    valid_symbols = sorted(slide_symbols, key=lambda s: s.split(":")[-1])

    # Flat scan rows for the events slide (prefer 1d)
    slide_rows: dict[str, dict] = {}
    for sym in valid_symbols:
        by_tf = scan.get(sym, {})
        primary_row = dict(_primary_scan_row(by_tf)) if by_tf else {}
        fallback_row = fallback_rows.get(sym, {})
        if positive_float(primary_row.get("close", 0)):
            slide_rows[sym] = primary_row
        else:
            merged = dict(primary_row)
            merged.update(fallback_row)
            slide_rows[sym] = merged

    slides: list[str] = [make_events_slide(events, slide_rows, {sym: i + 1 for i, sym in enumerate(valid_symbols)})]
    for i, sym in enumerate(valid_symbols, start=1):
        slides.append(make_crypto_slide(i, slide_rows[sym], charts.get(sym, {}), liquidations.get(sym, {})))

    n = len(slides)

    # Nav dots (show all slides; they're tiny dots)
    dots_html = "".join(
        f'<div class="nav-dot{"  active" if i == 0 else ""}" title="Slide {i+1}"></div>'
        for i in range(n)
    )

    # Embed chart data as nested JS globals {sym: {tf: [candles]}}
    chart_data_json = json.dumps(charts, ensure_ascii=False, separators=(",", ":"))

    slides_html = "\n".join(slides)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Crypto perpetuals pump detector — live dashboard">
  <title>Crypto Pump Detector</title>
  <link rel="icon" href="data:," />
  <style>{STATIC_CSS}</style>
</head>
<body>
  <div id="nav-dots">{dots_html}</div>
  <div id="slide-counter">1 / {n}</div>
  <div id="swipe-hint">↑ swipe / arrow keys ↓</div>
  <button id="back-to-overview" data-goto="0" title="Back to overview" aria-label="Back to overview">&#8592; Overview</button>

  <div id="slides">
{slides_html}
  </div>

  <!-- Navigation (loads FIRST, before any CDN, so data-goto clicks always work) -->
  <script>
  (function() {{
    var slidesEl = document.getElementById('slides');
    var slides = document.querySelectorAll('.slide');
    window.goTo = function(idx) {{
      idx = Math.max(0, Math.min(slides.length - 1, idx));
      slidesEl.scrollTo({{ top: slides[idx].offsetTop, behavior: 'smooth' }});
    }};
    // Event delegation: any element with data-goto="N" navigates on click
    document.addEventListener('click', function(e) {{
      var el = e.target.closest('[data-goto]');
      if (el) {{
        e.preventDefault();
        window.goTo(parseInt(el.dataset.goto, 10));
      }}
    }});
  }})();
  </script>

  <!-- Chart.js + financial plugin (candlestick) + date adapter -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-chart-financial@0.2.1/dist/chartjs-chart-financial.js"></script>

  <!-- Embedded chart data -->
  <script>const CHART_DATA = {chart_data_json};</script>

  <!-- App logic (keyboard nav, IntersectionObserver, chart init) -->
  <script>{STATIC_JS}</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    events = load_events()
    scan   = load_scan()
    charts = load_charts()
    liquidations = load_liquidations()

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    content = build_html(events, scan, charts, liquidations)
    out = DOCS_DIR / "index.html"
    out.write_text(content, encoding="utf-8")

    size_kb = len(content.encode("utf-8")) // 1024
    n_charts = len(charts)
    n_symbols = len([
        sym for sym, by_tf in scan.items()
        if any(safe_float(r.get("close", 0)) > 0 for r in by_tf.values())
    ])
    print(f"✅  Generated {out}")
    print(f"    Size:    {size_kb} KB")
    print(f"    Symbols: {n_symbols} (with data) / {len(scan)} total")
    print(f"    Charts:  {n_charts} series")
    print(f"    Liqs:    {len(liquidations)} series")
    print(f"    Events:  {len(events)}")
