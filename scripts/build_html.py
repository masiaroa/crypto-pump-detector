#!/usr/bin/env python3
"""Generate docs/index.html — a self-contained static dashboard for GitHub Pages.

Run:
    python scripts/build_html.py

Reads:
    data/event_history.csv   – recent signal events
    data/latest_scan.csv     – one row per symbol (current state)
    data/charts/*.json       – historical candle data per symbol

Writes:
    docs/index.html          – fully self-contained HTML (no server required)
"""
from __future__ import annotations

import html as html_mod
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CHARTS_DIR = DATA_DIR / "charts"
DOCS_DIR = ROOT / "docs"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_events() -> list[dict]:
    p = DATA_DIR / "event_history.csv"
    if not p.exists():
        return []
    df = pd.read_csv(p)
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp", ascending=False)
    return df.fillna("—").head(60).to_dict("records")


def load_scan() -> dict[str, dict]:
    p = DATA_DIR / "latest_scan.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    df = df.fillna(0)
    return {str(row["symbol"]): row.to_dict() for _, row in df.iterrows()}


def load_charts() -> dict[str, list]:
    """Returns {symbol: [candle_dict, ...]} for last 90 candles per symbol."""
    charts: dict[str, list] = {}
    if not CHARTS_DIR.exists():
        return charts
    for f in sorted(CHARTS_DIR.glob("*.json")):
        try:
            obj = json.loads(f.read_text("utf-8"))
            sym = obj.get("symbol", "")
            data = obj.get("data", [])
            # Only keep last 90 candles for dashboard
            charts[sym] = data[-90:] if len(data) > 90 else data
        except Exception:
            pass
    return charts


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def esc(s: object) -> str:
    return html_mod.escape(str(s))


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
    if close >= 1_000:
        return f"${close:,.0f}"
    if close >= 1:
        return f"${close:,.2f}"
    if close > 0:
        return f"${close:.5g}"
    return "—"


# ---------------------------------------------------------------------------
# Slides
# ---------------------------------------------------------------------------

def make_events_slide(events: list[dict], scan: dict[str, dict]) -> str:
    now_str = pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC")

    # Active signals from latest scan (sorted by score desc)
    active = [v for v in scan.values() if v.get("signal_active") or v.get("alert_triggered")]
    active.sort(key=lambda r: float(r.get("early_bullish_score", 0)), reverse=True)

    signal_cards_html = ""
    if active:
        for row in active[:8]:
            sym = esc(row.get("symbol", ""))
            bull = float(row.get("early_bullish_score", 0))
            risk = float(row.get("blowoff_risk_score", 0))
            fc = str(row.get("funding_classification", "UNKNOWN"))
            close = float(row.get("close", 0))
            icon = "🟢" if row.get("alert_triggered") else "🟡"
            signal_cards_html += f"""
              <div class="signal-card">
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

    # Recent events table
    event_rows_html = ""
    for ev in events[:30]:
        et = str(ev.get("event_type", ""))
        et_cls = "et-entry" if et == "ENTRY" else ("et-hot" if et == "HOT_PRE_ENTRY" else "et-pre")
        sym = esc(ev.get("symbol", ev.get("raw_symbol", "")))
        ts  = str(ev.get("timestamp", ""))[:10]
        bull = float(ev.get("early_bullish_score", 0)) if ev.get("early_bullish_score") != "—" else 0.0
        risk = float(ev.get("blowoff_risk_score", 0))  if ev.get("blowoff_risk_score")  != "—" else 0.0
        fc   = str(ev.get("funding_classification", ""))
        event_rows_html += f"""
          <tr>
            <td><span class="event-type-badge {et_cls}">{esc(et)}</span></td>
            <td class="sym-cell">{sym}</td>
            <td>{ts}</td>
            <td style="color:{score_color(bull)}">{bull:.0f}</td>
            <td style="color:#f85149">{risk:.0f}</td>
            <td><span class="badge {funding_badge_class(fc)}">{esc(fc)}</span></td>
          </tr>"""

    events_table_html = ""
    if event_rows_html:
        events_table_html = f"""
        <div class="events-table-wrap">
          <h3 class="section-label">Recent Events (last 21 days)</h3>
          <table class="events-table">
            <thead>
              <tr><th>Type</th><th>Symbol</th><th>Date</th><th>Bull</th><th>Risk</th><th>Funding</th></tr>
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


def make_crypto_slide(idx: int, scan_row: dict) -> str:
    symbol   = str(scan_row.get("symbol", ""))
    exchange = str(scan_row.get("exchange", ""))
    close    = float(scan_row.get("close", 0))
    bull     = float(scan_row.get("early_bullish_score", 0))
    risk     = float(scan_row.get("blowoff_risk_score", 0))
    fc       = str(scan_row.get("funding_classification", "UNKNOWN"))
    fr       = float(scan_row.get("funding_rate", 0))
    oi_pct   = float(scan_row.get("oi_change_pct", 0))

    # Derive readable base name from symbol
    ticker = symbol.split(":")[-1].replace(".P", "")
    for suffix in ("USDT", "USD", "USDC"):
        if ticker.endswith(suffix):
            base = ticker[:-len(suffix)]
            break
    else:
        base = ticker

    icon = "🟢" if scan_row.get("alert_triggered") else ("🟡" if scan_row.get("signal_active") else "⚪")
    canvas_id = f"s{idx}"

    return f"""
    <section class="slide" id="slide-{idx}" data-idx="{idx}" data-symbol="{esc(symbol)}">
      <div class="slide-header crypto-header">
        <div class="crypto-title">
          <span class="crypto-icon">{icon}</span>
          <span class="crypto-base">{esc(base)}</span>
          <span class="crypto-exchange">{esc(exchange)}</span>
        </div>
        <div class="crypto-meta">
          <span class="crypto-price">{esc(format_price(close))}</span>
          <span class="metric-chip" style="color:{score_color(bull)}">Bull&nbsp;{bull:.0f}</span>
          <span class="metric-chip" style="color:#f85149">Risk&nbsp;{risk:.0f}</span>
          <span class="metric-chip" style="color:#8b949e">OI&nbsp;{oi_pct:+.1%}</span>
          <span class="badge {funding_badge_class(fc)}">{esc(fc)}</span>
        </div>
      </div>
      <div class="charts-grid">
        <div class="chart-box">
          <div class="chart-label">Price (close)</div>
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
}
.signal-card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; gap: 6px; }
.signal-sym { font-size: 12px; font-weight: 700; }
.signal-metrics { display: flex; gap: 10px; font-size: 12px; font-weight: 600; }
.no-signals { color: #8b949e; font-style: italic; font-size: 13px; }

.events-table-wrap { overflow-x: auto; }
.events-table { width: 100%; border-collapse: collapse; font-size: 11px; }
.events-table th { text-align: left; padding: 5px 7px; color: #8b949e; border-bottom: 1px solid #30363d; white-space: nowrap; }
.events-table td { padding: 4px 7px; border-bottom: 1px solid #21262d; white-space: nowrap; }
.sym-cell { font-weight: 700; color: #58a6ff; }
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

  function goTo(idx) {
    idx = Math.max(0, Math.min(N - 1, idx));
    slides[idx].scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function updateUI(idx) {
    current = idx;
    dots.forEach((d, i) => d.classList.toggle('active', i === idx));
    counter.textContent = (idx + 1) + ' / ' + N;
  }

  // Keyboard
  document.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); goTo(current + 1); }
    if (e.key === 'ArrowUp'   || e.key === 'k') { e.preventDefault(); goTo(current - 1); }
  });

  // Dot clicks
  dots.forEach((d, i) => d.addEventListener('click', () => goTo(i)));

  // IntersectionObserver: track current slide + lazy init charts
  const io = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        const idx = parseInt(entry.target.dataset.idx, 10);
        updateUI(idx);
        if (!inited.has(idx)) { inited.add(idx); initCharts(entry.target, idx); }
        // hide swipe hint after first interaction
        if (hint) hint.style.opacity = '0';
      }
    }
  }, { root: slidesEl, threshold: 0.5 });

  slides.forEach(s => io.observe(s));
  updateUI(0);

  // ── Chart defaults ──────────────────────────────────────────────────────
  const BASE_OPTS = {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 250 },
    plugins: {
      legend: { display: false },
      tooltip: {
        mode: 'index', intersect: false,
        callbacks: { title: (items) => String(items[0]?.label || '').slice(0, 10) }
      },
    },
    scales: {
      x: {
        display: true,
        ticks: { color: '#6e7681', font: { size: 8 }, maxTicksLimit: 5, maxRotation: 0 },
        grid:  { color: 'rgba(48,54,61,0.6)' },
      },
      y: {
        display: true,
        position: 'right',
        ticks: { color: '#6e7681', font: { size: 8 }, maxTicksLimit: 4 },
        grid:  { color: 'rgba(48,54,61,0.6)' },
      },
    },
  };

  function deepClone(obj) { return JSON.parse(JSON.stringify(obj)); }

  function lineChart(id, labels, values, color) {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: {
        labels,
        datasets: [{ data: values, borderColor: color, backgroundColor: color + '18',
                     borderWidth: 1.5, pointRadius: 0, fill: true, tension: 0.15 }],
      },
      options: deepClone(BASE_OPTS),
    });
  }

  function barChart(id, labels, values, color) {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: color + 'cc', borderWidth: 0 }],
      },
      options: deepClone(BASE_OPTS),
    });
  }

  function fundingChart(id, labels, values) {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    const colors = values.map(v => v >= 0 ? '#d29922cc' : '#f85149cc');
    const opts = deepClone(BASE_OPTS);
    opts.plugins.tooltip.callbacks.label = (item) => item.raw.toFixed(2) + ' bps';
    new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }],
      },
      options: opts,
    });
  }

  // ── Init charts for a given slide ───────────────────────────────────────
  function initCharts(slideEl, idx) {
    if (idx === 0) return;  // events slide has no charts

    const symbol = slideEl.dataset.symbol;
    const raw    = (typeof CHART_DATA !== 'undefined') ? CHART_DATA[symbol] : null;
    if (!raw || !raw.length) return;

    const id     = 's' + idx;
    const labels = raw.map(d => String(d.timestamp || '').slice(0, 10));
    const close  = raw.map(d => +(d.close          || 0));
    const oi     = raw.map(d => +(d.open_interest  || 0));
    const vol    = raw.map(d => +(d.volume         || 0));
    // Funding rate → basis points (× 10 000), rounded to 2dp
    const fr     = raw.map(d => Math.round(+(d.funding_rate || 0) * 1e6) / 100);

    lineChart('price-' + id, labels, close, '#58a6ff');
    lineChart('oi-'    + id, labels, oi,    '#3fb950');
    barChart ('vol-'   + id, labels, vol,   '#8b949e');
    fundingChart('fr-' + id, labels, fr);
  }
})();
"""


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------

def build_html(events: list[dict], scan: dict[str, dict], charts: dict[str, list]) -> str:
    # Build per-crypto slides: symbols with valid close price, alpha-sorted
    valid_symbols = sorted(
        [sym for sym, row in scan.items() if float(row.get("close", 0)) > 0],
        key=lambda s: s.split(":")[-1],
    )

    slides: list[str] = [make_events_slide(events, scan)]
    for i, sym in enumerate(valid_symbols, start=1):
        slides.append(make_crypto_slide(i, scan[sym]))

    n = len(slides)

    # Nav dots (show all slides; they're tiny dots)
    dots_html = "".join(
        f'<div class="nav-dot{"  active" if i == 0 else ""}" title="Slide {i+1}"></div>'
        for i in range(n)
    )

    # Embed chart data as one JS global
    chart_data_json = json.dumps(charts, ensure_ascii=False, separators=(",", ":"))

    slides_html = "\n".join(slides)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Crypto perpetuals pump detector — live dashboard">
  <title>Crypto Pump Detector</title>
  <style>{STATIC_CSS}</style>
</head>
<body>
  <div id="nav-dots">{dots_html}</div>
  <div id="slide-counter">1 / {n}</div>
  <div id="swipe-hint">↑ swipe / arrow keys ↓</div>

  <div id="slides">
{slides_html}
  </div>

  <!-- Chart.js from CDN -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>

  <!-- Embedded chart data -->
  <script>const CHART_DATA = {chart_data_json};</script>

  <!-- App logic -->
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

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    content = build_html(events, scan, charts)
    out = DOCS_DIR / "index.html"
    out.write_text(content, encoding="utf-8")

    size_kb = len(content.encode("utf-8")) // 1024
    n_charts = len(charts)
    n_symbols = len([v for v in scan.values() if float(v.get("close", 0)) > 0])
    print(f"✅  Generated {out}")
    print(f"    Size:    {size_kb} KB")
    print(f"    Symbols: {n_symbols} (with data) / {len(scan)} total")
    print(f"    Charts:  {n_charts} series")
    print(f"    Events:  {len(events)}")

