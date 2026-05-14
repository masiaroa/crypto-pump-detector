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

try:
    from pump_detector.liquidations.executed_store import read_recent as read_recent_liquidations
except Exception:  # pragma: no cover - build still works without PYTHONPATH=src
    read_recent_liquidations = None

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


def _normalize_liqs_input(liquidations: dict) -> dict[str, dict[str, list]]:
    """Accept old flat {sym: [list]} or new nested {sym: {tf: list}}."""
    result: dict[str, dict[str, list]] = {}
    for sym, val in liquidations.items():
        if isinstance(val, list):
            result[sym] = {"1d": val}
        elif isinstance(val, dict):
            result[sym] = val
    return result


_MAX_CANDLES: dict[str, int] = {"1d": 120, "4h": 260, "1h": 360}


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


def _normalize_liq_rows(rows: list) -> list:
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        r = dict(row)
        if "notional" not in r and "amount" in r:
            r["notional"] = r["amount"]
        normalized.append(r)
    return normalized


def load_embedded_liquidations(path: Path | None = None) -> dict[str, dict[str, list]]:
    """Recover liquidation data embedded in a previous static HTML build."""
    data = load_embedded_json("LIQUIDATION_DATA", path)
    result: dict[str, dict[str, list]] = {}
    for symbol, val in data.items():
        if isinstance(val, list) and val:
            # old flat shape
            normalized = _normalize_liq_rows(val[-250:])
            if normalized:
                result[str(symbol)] = {"1d": normalized}
        elif isinstance(val, dict):
            by_tf: dict[str, list] = {}
            for tf, rows in val.items():
                if isinstance(rows, list) and rows:
                    normalized = _normalize_liq_rows(rows[-250:])
                    if normalized:
                        by_tf[str(tf)] = normalized
            if by_tf:
                result[str(symbol)] = by_tf
    return result


def load_ws_history_liquidations(symbols: dict[str, dict] | None = None) -> dict[str, dict[str, list]]:
    """Build per-symbol liquidation rows from the rolling WS history file."""
    if read_recent_liquidations is None:
        return {}
    history_path = LIQUIDATIONS_DIR / "_ws_history.jsonl"
    if not history_path.exists():
        return {}
    liquidations: dict[str, dict[str, list]] = {}
    for symbol, scan_row in (symbols or {}).items():
        timeframe = str(scan_row.get("timeframe") or "1d")
        try:
            frame = read_recent_liquidations(history_path, symbol, timeframe)
        except Exception:
            continue
        if frame.empty:
            continue
        rows = frame.tail(250).copy()
        if "timestamp" in rows.columns:
            rows["timestamp"] = rows["timestamp"].astype(str)
        liq_rows = rows.fillna(0).to_dict(orient="records")
        liquidations.setdefault(symbol, {})[timeframe] = liq_rows
    return liquidations


def load_liquidations(symbols: dict[str, dict] | None = None) -> dict[str, dict[str, list]]:
    """Returns {symbol: {timeframe: [liquidation_dict, ...]}} for static overlays."""
    liquidations: dict[str, dict[str, list]] = {}
    if not LIQUIDATIONS_DIR.exists():
        return load_embedded_liquidations()
    for f in sorted(LIQUIDATIONS_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            obj = json.loads(f.read_text("utf-8"))
            sym = obj.get("symbol", "")
            tf = str(obj.get("timeframe", "1d"))
            data = obj.get("data", [])
            data = data[-250:] if len(data) > 250 else data
            liquidations.setdefault(sym, {})[tf] = data
        except Exception:
            pass
    return liquidations or load_ws_history_liquidations(symbols) or load_embedded_liquidations()


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
    }.get(str(fc), "badge-unknown")


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


def liquidation_totals(rows: list | None) -> dict[str, float]:
    totals = {"long": 0.0, "short": 0.0}
    for row in rows or []:
        side = str(row.get("side", "")).lower()
        if side in totals:
            totals[side] += safe_float(row.get("notional"))
    return totals


def rows_for_client(rows: list | None) -> list:
    client_rows = []
    for row in rows or []:
        out = dict(row)
        if "notional" in out:
            out["amount"] = out.pop("notional")
        client_rows.append(out)
    return client_rows


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
    }


def row_from_chart(symbol: str, candles: list) -> dict:
    exchange = symbol.split(":", 1)[0] if ":" in symbol else ""
    last = candles[-1] if candles else {}
    return {
        "symbol": symbol,
        "exchange": exchange,
        "close": last.get("close", 0),
        "early_bullish_score": 0,
        "blowoff_risk_score": 0,
        "funding_classification": "UNKNOWN",
        "funding_rate": last.get("funding_rate", 0),
        "oi_change_pct": 0,
    }


# ---------------------------------------------------------------------------
# Slides
# ---------------------------------------------------------------------------

def make_events_slide(events: list[dict], scan: dict[str, dict],
                      symbol_to_slide: dict[str, int]) -> str:
    now_str = pd.Timestamp.now("Europe/Madrid").strftime("%Y-%m-%d %H:%M (Madrid)")

    # Active signals from latest scan (sorted by score desc)
    active = [v for v in scan.values() if v.get("signal_active") or v.get("alert_triggered")]
    active.sort(key=lambda r: safe_float(r.get("early_bullish_score", 0)), reverse=True)

    signal_cards_html = ""
    if active:
        for row in active[:8]:
            sym   = esc(row.get("symbol", ""))
            bull  = safe_float(row.get("early_bullish_score", 0))
            risk  = safe_float(row.get("blowoff_risk_score", 0))
            fc    = str(row.get("funding_classification", "UNKNOWN"))
            close = safe_float(row.get("close", 0))
            icon  = "🟢" if row.get("alert_triggered") else "🟡"
            slide_idx = symbol_to_slide.get(str(row.get("symbol", "")), -1)
            goto_attr = f'data-goto="{slide_idx}" style="cursor:pointer"' if slide_idx >= 0 else ""
            signal_cards_html += f"""
              <div class="signal-card" {goto_attr}>
                <div class="signal-card-head">
                  <span class="signal-sym">{icon} {sym}</span>
                  <span class="badge {funding_badge_class(fc)}">{esc(fc)}</span>
                </div>
                <div class="signal-metrics">
                  <span style="color:{score_color(bull)}">Bull {bull:.0f}</span>
                  <span style="color:#f85149">Risk {risk:.0f}</span>
                  <span style="color:#58a6ff">{esc(format_price(close))}</span>
                </div>
              </div>"""
    else:
        signal_cards_html = '<p class="no-signals">No active signals right now</p>'

    # Events table — Symbol FIRST column, clickable → navigate to crypto slide
    event_rows_html = ""
    for ev in events[:30]:
        raw_sym   = str(ev.get("raw_symbol", ""))
        sym_label = esc(ev.get("symbol", raw_sym))
        slide_idx = symbol_to_slide.get(raw_sym, -1)
        et     = str(ev.get("event_type", ""))
        et_cls = "et-entry" if et == "ENTRY" else ("et-hot" if et == "HOT_PRE_ENTRY" else "et-pre")
        ts   = str(ev.get("timestamp", ""))[:10]
        bull = safe_float(ev.get("early_bullish_score", 0))
        risk = safe_float(ev.get("blowoff_risk_score", 0))
        fc   = str(ev.get("funding_classification", ""))

        if slide_idx >= 0:
            sym_cell = f'<td class="sym-cell sym-link" data-goto="{slide_idx}">{sym_label}</td>'
        else:
            sym_cell = f'<td class="sym-cell">{sym_label}</td>'

        event_rows_html += f"""
          <tr>
            {sym_cell}
            <td><span class="event-type-badge {et_cls}">{esc(et)}</span></td>
            <td>{ts}</td>
            <td style="color:{score_color(bull)}">{bull:.0f}</td>
            <td style="color:#f85149">{risk:.0f}</td>
            <td><span class="badge {funding_badge_class(fc)}">{esc(fc)}</span></td>
          </tr>"""

    events_table_html = ""
    if event_rows_html:
        events_table_html = f"""
        <div class="events-table-wrap">
          <h3 class="section-label">Recent Events — click symbol to navigate</h3>
          <table class="events-table">
            <thead>
              <tr><th>Symbol</th><th>Type</th><th>Date</th><th>Bull</th><th>Risk</th><th>Funding</th></tr>
            </thead>
            <tbody>{event_rows_html}</tbody>
          </table>
        </div>"""

    return f"""
    <section class="slide" id="slide-0" data-idx="0">
      <div class="slide-header">
        <div class="events-title">
          <span class="logo">📈</span>
          <span>Crypto Pump Detector</span>
        </div>
        <span class="scan-time">Last scan: {esc(now_str)} &middot; {len(scan)} symbols</span>
      </div>
      <div class="slide-body">
        <div class="signals-section">
          <h3 class="section-label">Active Signals ({len(active)})</h3>
          <div class="signal-cards-grid">{signal_cards_html}</div>
        </div>
        {events_table_html}
      </div>
    </section>"""


def make_crypto_slide(
    idx: int,
    scan_row: dict,
    candles_by_tf: dict[str, list] | None = None,
    liqs_by_tf: dict[str, list] | None = None,
    default_tf: str = "4h",
) -> str:
    candles_by_tf = candles_by_tf or {}
    liqs_by_tf    = liqs_by_tf or {}

    # Use default TF data for header metrics; fall back to first available TF
    candles          = candles_by_tf.get(default_tf) or next(iter(candles_by_tf.values()), None) or []
    liquidation_rows = liqs_by_tf.get(default_tf)   or next(iter(liqs_by_tf.values()),   None) or []

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
    totals   = liquidation_totals(liquidation_rows)

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

    return f"""
    <section class="slide" id="slide-{idx}" data-idx="{idx}" data-symbol="{esc(symbol)}" data-default-tf="{esc(default_tf)}">
      <div class="slide-header crypto-header">
        <div class="crypto-title">
          <span class="crypto-icon">{icon}</span>
          <span class="crypto-base">{esc(base)}</span>
          <span class="crypto-exchange">{esc(exchange)}</span>
        </div>
        <div class="crypto-meta">
          <span class="crypto-price">{esc(format_price(close))}</span>
          <span class="metric-chip change-chip">Day&nbsp;{esc(format_pct(change))}</span>
          <table class="liq-summary">
            <tbody>
              <tr><th>Longs liquidated</th><td>{esc(format_money(totals["long"]))}</td></tr>
              <tr><th>Shorts liquidated</th><td>{esc(format_money(totals["short"]))}</td></tr>
            </tbody>
          </table>
          {tf_toggle_html}
        </div>
      </div>
      <div class="charts-grid">
        <div class="chart-box">
          <div class="chart-label">Price + Liquidations</div>
          <canvas id="price-{canvas_id}"></canvas>
        </div>
        <div class="chart-box">
          <div class="chart-label">Open Interest</div>
          <canvas id="oi-{canvas_id}"></canvas>
        </div>
        <div class="chart-box">
          <div class="chart-label">Volume</div>
          <canvas id="vol-{canvas_id}"></canvas>
        </div>
        <div class="chart-box">
          <div class="chart-label">Funding Rate (bps)</div>
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

.crypto-header { gap: 6px; }
.crypto-title { display: flex; align-items: center; gap: 8px; }
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
.et-pre   { background: #1a1f29; color: #79c0ff; }

/* ── Charts 2x2 grid ── */
.charts-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr 1fr;
  gap: 5px;
  flex: 1;
  padding: 5px;
  min-height: 0;
}
.chart-box {
  background: #161b22; border: 1px solid #30363d; border-radius: 6px;
  padding: 7px; display: flex; flex-direction: column; min-height: 0; overflow: hidden;
}
.chart-label {
  font-size: 9px; font-weight: 700; text-transform: uppercase;
  color: #8b949e; margin-bottom: 3px; letter-spacing: 0.07em; flex-shrink: 0;
}
.chart-box canvas { flex: 1; min-height: 0; display: block; width: 100% !important; }

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

/* ── Mobile: stack to 1 column on narrow screens ── */
@media (max-width: 420px) {
  .charts-grid { grid-template-columns: 1fr; grid-template-rows: repeat(4, 1fr); }
  .crypto-base { font-size: 16px; }
  #nav-dots { display: none; }
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

  // ── TF toggle handler — syncs all slides ────────────────────────────────
  slidesEl.addEventListener('click', (e) => {
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

  const SCALE_X_CAT = {
    display: true,
    ticks: { color: '#6e7681', font: { size: 8 }, maxTicksLimit: 5, maxRotation: 0 },
    grid:  { color: 'rgba(48,54,61,0.6)' },
  };
  const SCALE_X_TIME = {
    type: 'time',
    time: { unit: 'day', displayFormats: { day: 'MMM d' } },
    ticks: { color: '#6e7681', font: { size: 8 }, maxTicksLimit: 5, maxRotation: 0 },
    grid:  { color: 'rgba(48,54,61,0.6)' },
  };
  const SCALE_Y = {
    display: true, position: 'right',
    ticks: { color: '#6e7681', font: { size: 8 }, maxTicksLimit: 4 },
    grid:  { color: 'rgba(48,54,61,0.6)' },
  };

  const liquidationOverlayPlugin = {
    id: 'liquidationOverlayPlugin',
    beforeDatasetsDraw(chart, args, opts) {
      const rows = (opts && opts.rows) || [];
      if (!rows.length) return;
      const xScale = chart.scales.x;
      const yScale = chart.scales.y;
      const area = chart.chartArea;
      const maxAmount = Math.max(...rows.map(r => +(r.amount || 0)), 1);
      const minX = xScale.min;
      const maxX = xScale.max;
      const fallbackX = area.left + (area.right - area.left) * 0.06;
      const ctx = chart.ctx;
      ctx.save();
      for (const row of rows) {
        const price = +(row.price || 0);
        const amount = +(row.amount || 0);
        if (!price || !amount) continue;
        const y = yScale.getPixelForValue(price);
        if (y < area.top || y > area.bottom) continue;
        const rawX = Date.parse(row.timestamp || '');
        const x = Number.isFinite(rawX) && rawX >= minX && rawX <= maxX ? xScale.getPixelForValue(rawX) : fallbackX;
        const strength = Math.max(0.18, Math.min(0.85, amount / maxAmount));
        const isProjected = row.kind === 'projected';
        const color = row.side === 'short' ? '34,197,94' : row.side === 'long' ? '239,68,68' : '245,158,11';
        ctx.globalAlpha = isProjected ? 0.10 + strength * 0.28 : 0.16 + strength * 0.34;
        ctx.fillStyle = 'rgba(' + color + ',1)';
        if (isProjected) {
          ctx.fillRect(area.left, y - 1.5, area.right - area.left, 3);
        } else {
          const radius = 3 + strength * 8;
          ctx.beginPath();
          ctx.arc(x, y, radius, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.restore();
    }
  };

  // ── Japanese candlestick chart (price or OI) ─────────────────────────────
  function candleChart(id, candleData, liquidationRows) {
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
          liquidationOverlayPlugin: { rows: liquidationRows || [] },
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
        scales: { x: deepClone(SCALE_X_TIME), y: deepClone(SCALE_Y) },
      },
      plugins: liquidationRows && liquidationRows.length ? [liquidationOverlayPlugin] : [],
    });
  }

  // ── Bar chart (volume) ────────────────────────────────────────────────────
  function barChart(id, labels, values, color) {
    const canvas = document.getElementById(id);
    if (!canvas) return null;
    return new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: { labels, datasets: [{ data: values, backgroundColor: color + 'cc', borderWidth: 0 }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 200 },
        plugins: { legend: { display: false } },
        scales: { x: deepClone(SCALE_X_CAT), y: deepClone(SCALE_Y) },
      },
    });
  }

  // ── Funding rate bar chart (bps) ──────────────────────────────────────────
  function fundingChart(id, labels, values) {
    const canvas = document.getElementById(id);
    if (!canvas) return null;
    const colors = values.map(v => v >= 0 ? '#d29922cc' : '#f85149cc');
    return new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: { labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 200 },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (i) => i.raw.toFixed(2) + ' bps' } },
        },
        scales: { x: deepClone(SCALE_X_CAT), y: deepClone(SCALE_Y) },
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

    const liqsByTf = (typeof LIQUIDATION_DATA !== 'undefined') ? LIQUIDATION_DATA[symbol] : null;
    let liqs;
    if (!liqsByTf) {
      liqs = [];
    } else if (Array.isArray(liqsByTf)) {
      liqs = liqsByTf;
    } else {
      liqs = liqsByTf[tf] || [];
    }

    const id  = 's' + idx;
    const vol = raw.map(d => +(d.volume || 0));
    const fr  = raw.map(d => Math.round(+(d.funding_rate || 0) * 1e6) / 100);
    const lbl = raw.map(d => String(d.timestamp || '').slice(0, 10));

    // Price candlestick
    const priceCandles = raw
      .filter(d => +d.open && +d.high && +d.low && +d.close)
      .map(d => ({ x: Date.parse(d.timestamp), o: +d.open, h: +d.high, l: +d.low, c: +d.close }));
    const priceChart = candleChart('price-' + id, priceCandles, liqs);

    // OI candlestick — uses oi_open/high/low/close when available
    let oiChart;
    const hasOiOhlc = raw.some(d => +d.oi_open > 0);
    if (hasOiOhlc) {
      const oiCandles = raw
        .filter(d => +d.oi_open > 0)
        .map(d => ({ x: Date.parse(d.timestamp), o: +d.oi_open, h: +d.oi_high, l: +d.oi_low, c: +(d.oi_close || d.open_interest) }));
      oiChart = candleChart('oi-' + id, oiCandles, []);
    } else {
      // Fallback: simple line with open_interest
      const oiVals = raw.map(d => +(d.open_interest || 0));
      const cvs = document.getElementById('oi-' + id);
      if (cvs) oiChart = new Chart(cvs.getContext('2d'), {
        type: 'line',
        data: { labels: lbl, datasets: [{ data: oiVals, borderColor: '#3fb950', backgroundColor: '#3fb95018', borderWidth: 1.5, pointRadius: 0, fill: true, tension: 0.15 }] },
        options: { responsive: true, maintainAspectRatio: false, animation: { duration: 200 }, plugins: { legend: { display: false } }, scales: { x: deepClone(SCALE_X_CAT), y: deepClone(SCALE_Y) } },
      });
    }

    const volChart = barChart    ('vol-' + id, lbl, vol, '#8b949e');
    const frChart  = fundingChart('fr-'  + id, lbl, fr);
    slideEl._charts = { price: priceChart, oi: oiChart, vol: volChart, fr: frChart };
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

    # Build fallback rows from chart and event data (one row per symbol, any TF)
    fallback_rows: dict[str, dict] = {}
    for symbol, candles_by_tf in charts.items():
        any_candles = next(iter(candles_by_tf.values()), []) if candles_by_tf else []
        if any_candles:
            fallback_rows[symbol] = row_from_chart(symbol, any_candles)
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
    # Convert liquidations: {sym: {tf: rows}} → {sym: {tf: client_rows}}
    client_liquidations = {
        sym: {tf: rows_for_client(rows) for tf, rows in by_tf.items()}
        for sym, by_tf in liquidations.items()
    }
    liquidation_data_json = json.dumps(client_liquidations, ensure_ascii=False, separators=(",", ":"))

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
  <script>const LIQUIDATION_DATA = {liquidation_data_json};</script>

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
    # Flat scan rows (primary TF) for liquidation WS history lookup
    liquidation_symbols = {
        sym: _primary_scan_row(scan[sym]) if sym in scan else {}
        for sym in sorted(set(scan) | set(charts))
    }
    liquidations = load_liquidations(liquidation_symbols)

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
