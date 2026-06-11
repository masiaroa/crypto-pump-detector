#!/usr/bin/env python3
"""Build docs/coinalyze.html from cached Coinalyze dashboard JSON."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
COINALYZE_DIR = DATA_DIR / "coinalyze"
DOCS_DIR = ROOT / "docs"


SLUG_OVERRIDES = {
    "ADA": "cardano",
    "BCH": "bitcoin-cash",
    "BTC": "bitcoin",
    "DOGE": "dogecoin",
    "DOT": "polkadot",
    "ETH": "ethereum",
    "LINK": "chainlink",
    "LTC": "litecoin",
    "MANA": "decentraland",
    "NEAR": "near-protocol",
    "SOL": "solana",
    "UNI": "uniswap",
    "XLM": "stellar",
    "XRP": "ripple",
}


def load_snapshots(path: Path | None = None) -> list[dict[str, Any]]:
    cache_dir = path or COINALYZE_DIR
    if not cache_dir.exists():
        return []
    snapshots: list[dict[str, Any]] = []
    for file in sorted(cache_dir.glob("*.json")):
        if file.name.startswith("_"):
            continue
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("base") and payload.get("timeframe"):
            snapshots.append(payload)
    return snapshots


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    manifest_path = path or (COINALYZE_DIR / "_manifest.json")
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build_html(snapshots: list[dict[str, Any]], manifest: dict[str, Any] | None = None) -> str:
    manifest = manifest or {}
    grouped = _group_snapshots(snapshots)
    data_json = json.dumps(grouped, ensure_ascii=False, separators=(",", ":"))
    generated_at = esc(manifest.get("generated_at") or _latest_generated_at(snapshots) or "not generated")

    if not grouped:
        body = _setup_state()
    else:
        overview_rows = "\n".join(_overview_row(base, by_tf, idx + 1) for idx, (base, by_tf) in enumerate(grouped.items()))
        slides = "\n".join(_coin_slide(base, by_tf, idx + 1) for idx, (base, by_tf) in enumerate(grouped.items()))
        body = f"""
        <section class="overview">
          <div class="topbar">
            <div>
              <h1>Coinalyze Metrics Dashboard</h1>
              <p>Transparent project aggregate from official Coinalyze API data. Not an exact clone of Coinalyze Average.</p>
            </div>
            <a class="source-link" href="https://coinalyze.net/" target="_blank" rel="noreferrer">Data source: Coinalyze</a>
          </div>
          <table class="overview-table">
            <thead><tr><th>Coin</th><th>TF</th><th>Price</th><th>OI</th><th>Funding</th><th>Liq L/S</th><th>L/S</th><th>Fresh</th></tr></thead>
            <tbody>{overview_rows}</tbody>
          </table>
        </section>
        {slides}
        """

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Coinalyze Metrics Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.8/dist/chart.umd.min.js"></script>
<style>
{STATIC_CSS}
</style>
</head>
<body>
<main>
{body}
</main>
<script>const COINALYZE_DATA = {data_json};</script>
<script>
{STATIC_JS}
</script>
<footer class="footer">Generated: {generated_at}. Public chart data should cite <a href="https://coinalyze.net/" target="_blank" rel="noreferrer">Coinalyze</a>.</footer>
</body>
</html>"""


def write_html(
    snapshots: list[dict[str, Any]] | None = None,
    manifest: dict[str, Any] | None = None,
    *,
    output_path: Path | None = None,
) -> Path:
    snapshots = load_snapshots() if snapshots is None else snapshots
    manifest = load_manifest() if manifest is None else manifest
    output = output_path or (DOCS_DIR / "coinalyze.html")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_html(snapshots, manifest), encoding="utf-8")
    return output


def _group_snapshots(snapshots: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for snapshot in snapshots:
        base = str(snapshot.get("base") or "").upper()
        timeframe = str(snapshot.get("timeframe") or "")
        if not base or not timeframe:
            continue
        grouped.setdefault(base, {})[timeframe] = snapshot
    return dict(sorted(grouped.items()))


def _setup_state() -> str:
    return """
    <section class="setup-state">
      <h1>Coinalyze Metrics Dashboard</h1>
      <p>No cached Coinalyze data found yet. Set <code>COINALYZE_API_KEY</code>, refresh locally, then rebuild this page.</p>
      <pre><code>PYTHONPATH=src python scripts/coinalyze_refresh.py --force
PYTHONPATH=src python scripts/build_coinalyze_html.py
python scripts/validate_coinalyze_html.py docs/coinalyze.html</code></pre>
      <p class="source-note">Uses the official <a href="https://api.coinalyze.net/v1/doc/" target="_blank" rel="noreferrer">Coinalyze API</a>.</p>
    </section>
    """


def _overview_row(base: str, by_tf: dict[str, dict[str, Any]], idx: int) -> str:
    timeframe = _default_tf(by_tf)
    snapshot = by_tf[timeframe]
    latest = _latest(snapshot)
    return f"""
    <tr>
      <td><a href="#coinalyze-slide-{idx}">{esc(base)}</a></td>
      <td>{esc(timeframe.upper())}</td>
      <td>{format_price(latest.get("close"))}</td>
      <td>{format_money(latest.get("open_interest"))}</td>
      <td>{format_pct(latest.get("funding_rate"), scale=100)}</td>
      <td>{format_money(latest.get("long_liquidations"))} / {format_money(latest.get("short_liquidations"))}</td>
      <td>{format_ratio(latest.get("long_account_ratio"), latest.get("short_account_ratio"))}</td>
      <td>{esc(str(snapshot.get("generated_at", ""))[:16])}</td>
    </tr>"""


def _coin_slide(base: str, by_tf: dict[str, dict[str, Any]], idx: int) -> str:
    default_tf = _default_tf(by_tf)
    default_snapshot = by_tf[default_tf]
    latest = _latest(default_snapshot)
    tf_buttons = "".join(
        f'<button class="tf-btn{" active" if tf == default_tf else ""}" data-tf="{esc(tf)}">{esc(tf.upper())}</button>'
        for tf in sorted(by_tf, key=lambda item: {"4h": 0, "1d": 1}.get(item, 9))
    )
    links = " ".join(
        f'<a href="{esc(coinalyze_metric_href(base, metric))}" target="_blank" rel="noreferrer">{esc(label)}</a>'
        for metric, label in [
            ("open_interest", "Open Interest"),
            ("funding_rate", "Funding Rate"),
            ("liquidations", "Liquidations"),
            ("long_short_ratio", "Long/Short Ratio"),
        ]
    )
    diagnostics = _diagnostics_text(default_snapshot)
    return f"""
    <section class="coin-slide" id="coinalyze-slide-{idx}" data-base="{esc(base)}" data-default-tf="{esc(default_tf)}">
      <header class="coin-header">
        <div>
          <a class="back-link" href="#">Overview</a>
          <h2>{esc(base)}</h2>
          <p>{esc(default_snapshot.get("primary_price_contract", ""))} price, core aggregate metrics</p>
        </div>
        <div class="coin-stats">
          <span>{format_price(latest.get("close"))}</span>
          <span>OI {format_money(latest.get("open_interest"))}</span>
          <span>Funding {format_pct(latest.get("funding_rate"), scale=100)}</span>
          <span>L/S {format_ratio(latest.get("long_account_ratio"), latest.get("short_account_ratio"))}</span>
        </div>
      </header>
      <div class="toolbar">
        <div class="tf-toggle">{tf_buttons}</div>
      </div>
      <div class="chart-stack">
        <div class="price-area">
          <div class="chart-card price-card"><div class="chart-label">Price</div><canvas id="price-{esc(base)}"></canvas></div>
          <div class="chart-card volume-card"><div class="chart-label">Volume</div><canvas id="volume-{esc(base)}"></canvas></div>
        </div>
        <div class="futures-grid">
          <div class="chart-card oi-card"><div class="chart-label">Open Interest</div><canvas id="oi-{esc(base)}"></canvas></div>
          <div class="chart-card funding-card"><div class="chart-label">Funding Rate</div><canvas id="funding-{esc(base)}"></canvas></div>
          <div class="chart-card liquidation-card"><div class="chart-label">Liquidations</div><canvas id="liquidations-{esc(base)}"></canvas></div>
          <div class="chart-card long-short-card"><div class="chart-label">Long/Short Ratio</div><canvas id="long-short-{esc(base)}"></canvas></div>
        </div>
      </div>
      <div class="coverage">
        <span>Transparent project aggregate</span>
        <span>{esc(diagnostics)}</span>
        <span class="external-links">{links}</span>
      </div>
    </section>
    """


def coinalyze_metric_href(base: str, metric: str) -> str:
    slug = SLUG_OVERRIDES.get(base.upper(), base.lower())
    suffix = {
        "open_interest": "open-interest",
        "funding_rate": "funding-rate",
        "liquidations": "liquidations",
        "long_short_ratio": "long-short-ratio",
    }[metric]
    return f"https://coinalyze.net/{slug}/{suffix}/"


def _default_tf(by_tf: dict[str, dict[str, Any]]) -> str:
    return "4h" if "4h" in by_tf else "1d" if "1d" in by_tf else next(iter(by_tf))


def _latest(snapshot: dict[str, Any]) -> dict[str, Any]:
    series = snapshot.get("series") or []
    return series[-1] if series and isinstance(series[-1], dict) else {}


def _latest_generated_at(snapshots: list[dict[str, Any]]) -> str:
    values = [str(snapshot.get("generated_at") or "") for snapshot in snapshots]
    return max(values) if values else ""


def _diagnostics_text(snapshot: dict[str, Any]) -> str:
    diagnostics = snapshot.get("diagnostics") or []
    if not diagnostics:
        return "full available coverage"
    return "; ".join(str(item.get("message") or item.get("code")) for item in diagnostics if isinstance(item, dict))


def esc(value: object) -> str:
    return html.escape(str(value))


def safe_float(value: object) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def format_price(value: object) -> str:
    price = safe_float(value)
    if price >= 1000:
        return f"${price:,.0f}"
    if price >= 1:
        return f"${price:,.2f}"
    return f"${price:.5f}"


def format_money(value: object) -> str:
    amount = safe_float(value)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= 1_000_000_000:
        return f"{sign}${amount / 1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"{sign}${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"{sign}${amount / 1_000:.0f}K"
    return f"{sign}${amount:.0f}"


def format_pct(value: object, *, scale: float = 1.0) -> str:
    return f"{safe_float(value) * scale:+.4f}%"


def format_ratio(long_value: object, short_value: object) -> str:
    long_pct = safe_float(long_value)
    short_pct = safe_float(short_value)
    if long_pct <= 0 and short_pct <= 0:
        return "-"
    return f"{long_pct:.0%} / {short_pct:.0%}"


STATIC_CSS = """
:root {
  color-scheme: dark;
  --bg: #090b0f;
  --panel: #12161d;
  --panel-2: #181d25;
  --border: #2b313b;
  --text: #e7edf4;
  --muted: #8c96a3;
  --blue: #58a6ff;
  --green: #3fb950;
  --red: #f85149;
  --amber: #d29922;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
a { color: var(--blue); }
main { min-height: 100vh; }
.overview, .setup-state { min-height: 100vh; padding: 18px; scroll-margin-top: 0; }
.coin-slide { height: 100vh; padding: 10px 14px; scroll-margin-top: 0; overflow: hidden; }
.topbar, .coin-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; margin-bottom: 6px; }
h1, h2, p { margin: 0; }
h1 { font-size: 24px; }
h2 { font-size: 22px; line-height: 1; }
p, .coverage, .footer { color: var(--muted); }
.source-link, .back-link { display: inline-flex; align-items: center; border: 1px solid var(--border); background: var(--panel); border-radius: 6px; padding: 4px 8px; text-decoration: none; font-weight: 700; font-size: 11px; }
.overview-table { width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--border); }
.overview-table th, .overview-table td { text-align: left; padding: 7px 9px; border-bottom: 1px solid var(--border); white-space: nowrap; }
.overview-table th { color: var(--muted); font-size: 11px; text-transform: uppercase; }
.coin-slide { display: flex; flex-direction: column; gap: 6px; }
.coin-stats { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
.coin-stats span { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 4px 7px; font-weight: 700; font-size: 11px; }
.toolbar { display: flex; align-items: center; justify-content: flex-start; gap: 10px; flex-wrap: wrap; }
.tf-toggle { display: flex; gap: 4px; background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 3px; }
button { border: 0; border-radius: 4px; padding: 5px 9px; background: transparent; color: var(--muted); font-weight: 800; cursor: pointer; font-size: 11px; }
button.active, button:hover { background: var(--panel-2); color: var(--blue); }
.chart-stack { flex: 1; min-height: 0; display: grid; grid-template-rows: minmax(0, 2.9fr) minmax(0, 1.35fr); gap: 6px; }
.price-area { min-height: 0; display: grid; grid-template-rows: minmax(0, 4.8fr) minmax(0, 1fr); gap: 5px; }
.futures-grid { min-height: 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; }
.chart-card { min-height: 0; display: flex; flex-direction: column; background: #030506; border: 1px solid var(--border); border-radius: 6px; padding: 6px; }
.chart-label { color: var(--muted); font-size: 9px; font-weight: 800; text-transform: uppercase; margin-bottom: 2px; letter-spacing: 0.05em; }
canvas { flex: 1; width: 100% !important; min-height: 0; }
.coverage { display: flex; gap: 10px; flex-wrap: nowrap; overflow: hidden; font-size: 10px; white-space: nowrap; }
.external-links { display: flex; gap: 8px; flex-wrap: wrap; }
.setup-state { display: grid; align-content: center; justify-items: start; gap: 12px; max-width: 820px; margin: 0 auto; }
pre { white-space: pre-wrap; background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 12px; }
.footer { padding: 10px 18px; border-top: 1px solid var(--border); background: var(--panel); }
@media (max-width: 720px) {
  .overview, .setup-state { padding: 10px; }
  .coin-slide { padding: 8px; }
  .topbar, .coin-header { flex-direction: column; gap: 5px; }
  .overview-table { font-size: 11px; }
  .overview { overflow-x: auto; }
  .chart-stack { grid-template-rows: minmax(0, 2.2fr) minmax(0, 1.8fr); }
  .futures-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: repeat(2, minmax(0, 1fr)); }
  .coverage { display: none; }
}
"""


STATIC_JS = """
(function () {
  const charts = {};
  const candleColorUp = '#00c2a8';
  const candleColorDown = '#f85149';

  const candlestickPlugin = {
    id: 'candlestickPlugin',
    afterDatasetsDraw(chart) {
      if (chart.config.type !== 'bar' || !chart.data.datasets.some(ds => ds.kind === 'candles')) return;
      const { ctx, chartArea, scales } = chart;
      const xScale = scales.x;
      const yScale = scales.y;
      const candles = chart.data.datasets[0].candles || [];
      if (!candles.length) return;
      const slot = chartArea.width / Math.max(candles.length, 1);
      const bodyWidth = Math.max(2, Math.min(9, slot * 0.62));
      ctx.save();
      candles.forEach((candle, index) => {
        const x = xScale.getPixelForValue(index);
        const open = yScale.getPixelForValue(candle.o);
        const high = yScale.getPixelForValue(candle.h);
        const low = yScale.getPixelForValue(candle.l);
        const close = yScale.getPixelForValue(candle.c);
        const up = candle.c >= candle.o;
        ctx.strokeStyle = up ? candleColorUp : candleColorDown;
        ctx.fillStyle = up ? candleColorUp : candleColorDown;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, high);
        ctx.lineTo(x, low);
        ctx.stroke();
        const top = Math.min(open, close);
        const height = Math.max(1, Math.abs(close - open));
        ctx.fillRect(x - bodyWidth / 2, top, bodyWidth, height);
      });
      ctx.restore();
    }
  };
  Chart.register(candlestickPlugin);

  function rows(base, tf) {
    return (((COINALYZE_DATA || {})[base] || {})[tf] || {}).series || [];
  }

  function labels(series) {
    return series.map(row => String(row.timestamp || '').replace('T', ' ').replace('Z', ''));
  }

  function destroy(key) {
    if (charts[key]) {
      charts[key].destroy();
      delete charts[key];
    }
  }

  function lineChart(canvas, label, data, color) {
    return new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: { labels: data.labels, datasets: [{ label, data: data.values, borderColor: color, backgroundColor: color + '22', pointRadius: 0, borderWidth: 1.6, fill: true, tension: 0.18 }] },
      options: { responsive: true, maintainAspectRatio: false, animation: { duration: 150 }, plugins: { legend: { display: false } }, scales: { x: { ticks: { color: '#6f7885', maxTicksLimit: 8 }, grid: { color: '#151b23' } }, y: { ticks: { color: '#8c96a3' }, grid: { color: '#151b23' } } } }
    });
  }

  function barChart(canvas, label, labels, longs, shorts) {
    return new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: { labels, datasets: [
        { label: 'Long liq', data: longs, backgroundColor: 'rgba(248,81,73,0.72)', borderWidth: 0 },
        { label: 'Short liq', data: shorts, backgroundColor: 'rgba(63,185,80,0.72)', borderWidth: 0 },
      ] },
      options: { responsive: true, maintainAspectRatio: false, animation: { duration: 150 }, plugins: { legend: { labels: { color: '#8c96a3' } } }, scales: { x: { stacked: true, ticks: { color: '#6f7885', maxTicksLimit: 8 }, grid: { color: '#151b23' } }, y: { stacked: true, ticks: { color: '#8c96a3' }, grid: { color: '#151b23' } } } }
    });
  }

  function volumeChart(canvas, labels, values) {
    return new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: { labels, datasets: [{ label: 'Volume', data: values, backgroundColor: 'rgba(139,148,158,0.72)', borderWidth: 0, barPercentage: 1.0, categoryPercentage: 1.0, minBarLength: 2 }] },
      options: { responsive: true, maintainAspectRatio: false, animation: { duration: 150 }, plugins: { legend: { display: false } }, scales: { x: { ticks: { display: false }, grid: { color: '#151b23' } }, y: { ticks: { color: '#8c96a3', maxTicksLimit: 3 }, grid: { color: '#151b23' } } } }
    });
  }

  function candleChart(canvas, labels, candles) {
    return new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          kind: 'candles',
          candles,
          data: candles.map(c => c.c),
          backgroundColor: 'rgba(0,0,0,0)',
          borderWidth: 0,
          barPercentage: 1.0,
          categoryPercentage: 1.0,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 150 },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => {
          const c = candles[ctx.dataIndex] || {};
          return `O ${c.o} H ${c.h} L ${c.l} C ${c.c}`;
        } } } },
        scales: {
          x: { ticks: { color: '#6f7885', maxTicksLimit: 8 }, grid: { color: '#151b23' } },
          y: { min: Math.min(...candles.map(c => c.l)) * 0.995, max: Math.max(...candles.map(c => c.h)) * 1.005, ticks: { color: '#8c96a3' }, grid: { color: '#151b23' } },
        },
      },
    });
  }

  function render(slide) {
    const base = slide.dataset.base;
    const tf = slide.dataset.tf || slide.dataset.defaultTf || '4h';
    const series = rows(base, tf);
    const x = labels(series);

    ['price', 'volume', 'oi', 'funding', 'liquidations', 'longShort'].forEach(key => destroy(base + ':' + key));
    const priceCanvas = slide.querySelector('#price-' + CSS.escape(base));
    const volumeCanvas = slide.querySelector('#volume-' + CSS.escape(base));
    const oiCanvas = slide.querySelector('#oi-' + CSS.escape(base));
    const fundingCanvas = slide.querySelector('#funding-' + CSS.escape(base));
    const liquidationCanvas = slide.querySelector('#liquidations-' + CSS.escape(base));
    const longShortCanvas = slide.querySelector('#long-short-' + CSS.escape(base));
    if (!priceCanvas || !volumeCanvas || !oiCanvas || !fundingCanvas || !liquidationCanvas || !longShortCanvas || !series.length) return;

    charts[base + ':price'] = candleChart(priceCanvas, x, series.map(row => ({ o: +row.open || 0, h: +row.high || 0, l: +row.low || 0, c: +row.close || 0 })));
    charts[base + ':volume'] = volumeChart(volumeCanvas, x, series.map(row => +row.volume || 0));
    charts[base + ':oi'] = lineChart(oiCanvas, 'Open Interest', { labels: x, values: series.map(row => +row.open_interest || 0) }, '#3fb950');
    charts[base + ':funding'] = lineChart(fundingCanvas, 'Funding %', { labels: x, values: series.map(row => (+row.funding_rate || 0) * 100) }, '#d29922');
    charts[base + ':liquidations'] = barChart(liquidationCanvas, 'Liquidations', x, series.map(row => +row.long_liquidations || 0), series.map(row => +row.short_liquidations || 0));
    charts[base + ':longShort'] = lineChart(longShortCanvas, 'L/S Ratio', { labels: x, values: series.map(row => +row.long_short_ratio || 0) }, '#58a6ff');
  }

  document.querySelectorAll('.coin-slide').forEach(slide => {
    slide.dataset.tf = slide.dataset.defaultTf || '4h';
    slide.querySelectorAll('.tf-btn').forEach(btn => btn.addEventListener('click', () => {
      slide.dataset.tf = btn.dataset.tf;
      slide.querySelectorAll('.tf-btn').forEach(item => item.classList.toggle('active', item === btn));
      render(slide);
    }));
    render(slide);
  });
})();
"""


def main() -> int:
    output = write_html()
    print(f"[coinalyze] wrote {output}")
    print(f"[coinalyze] snapshots: {len(load_snapshots())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
