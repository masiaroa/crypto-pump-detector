import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_html", ROOT / "scripts" / "build_html.py")
build_html_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build_html_module)
build_html = build_html_module.build_html


def test_build_html_uses_event_history_as_slide_fallback_when_scan_has_no_price():
    events = [
        {
            "event_type": "ENTRY",
            "timestamp": "2026-05-10 00:00:00+00:00",
            "symbol": "BTC",
            "raw_symbol": "BYBIT:BTCUSDT.P",
            "timeframe": "1d",
            "close": 81234.5,
            "oi_change_pct": 0.12,
            "funding_classification": "NEUTRAL",
            "early_bullish_score": 88,
            "blowoff_risk_score": 42,
        }
    ]
    scan = {
        "BYBIT:BTCUSDT.P": {
            "symbol": "BYBIT:BTCUSDT.P",
            "exchange": "BYBIT",
            "close": 0,
            "early_bullish_score": 0,
            "blowoff_risk_score": 0,
        }
    }

    html = build_html(events, scan, charts={})

    assert 'id="slide-1"' in html
    assert 'data-symbol="BYBIT:BTCUSDT.P"' in html
    assert 'data-goto="1"' in html
    assert "1 / 2" in html


def test_build_html_includes_explicit_wheel_and_touch_slide_navigation():
    html = build_html([], {}, charts={})

    assert "slidesEl.scrollTo" in html
    assert "addEventListener('wheel'" in html
    assert "addEventListener('touchstart'" in html
    assert "addEventListener('touchend'" in html


def test_build_html_handles_placeholder_metrics_and_renders_market_header():
    charts = {
        "BYBIT:ADAUSDT.P": [
            {
                "timestamp": "2026-05-12 00:00:00+00:00",
                "open": 0.2700,
                "high": 0.2820,
                "low": 0.2600,
                "close": 0.2798,
                "volume": 100,
            }
        ]
    }
    scan = {
        "BYBIT:ADAUSDT.P": {
            "symbol": "BYBIT:ADAUSDT.P",
            "exchange": "BYBIT",
            "close": "0.2798",
            "price_return_pct": "\u2014",
            "early_bullish_score": "\u2014",
            "blowoff_risk_score": "\u2014",
            "oi_change_pct": "\u2014",
            "funding_classification": "NEGATIVE",
        }
    }

    html = build_html([], scan, charts=charts)

    # Scope placeholder-metric assertions to the per-crypto slide; the new
    # overview table on slide 0 legitimately shows funding/OI/Bull/Risk columns.
    crypto_slide = html.split('id="slide-1"', 1)[1] if 'id="slide-1"' in html else ""

    assert "$0.2798" in crypto_slide
    assert "+3.6%" in crypto_slide
    assert "Bull&nbsp;" not in crypto_slide
    assert "Risk&nbsp;" not in crypto_slide
    assert "OI&nbsp;" not in crypto_slide
    assert "NEGATIVE" not in crypto_slide


def test_build_html_daily_change_falls_back_to_scan_return_without_chart_ohlc():
    scan = {
        "BYBIT:BTCUSDT.P": {
            "symbol": "BYBIT:BTCUSDT.P",
            "exchange": "BYBIT",
            "close": 81234.5,
            "price_return_pct": -0.0123,
        }
    }

    html = build_html([], scan, charts={})

    assert "-1.2%" in html


def test_load_charts_falls_back_to_previous_embedded_chart_data(monkeypatch, tmp_path):
    docs_dir = tmp_path / "docs"
    charts_dir = tmp_path / "data" / "charts"
    docs_dir.mkdir(parents=True)
    docs_dir.joinpath("index.html").write_text(
        '<script>const CHART_DATA = {"BYBIT:ADAUSDT.P":[{"close":0.25}]};</script>',
        encoding="utf-8",
    )
    monkeypatch.setattr(build_html_module, "DOCS_DIR", docs_dir)
    monkeypatch.setattr(build_html_module, "CHARTS_DIR", charts_dir)

    charts = build_html_module.load_charts()

    assert charts == {"BYBIT:ADAUSDT.P": {"1d": [{"close": 0.25}]}}


def test_build_html_embeds_multi_timeframe_chart_data_and_toggle():
    charts = {
        "BYBIT:BTCUSDT.P": {
            "1d": [
                {
                    "timestamp": "2026-05-12 00:00:00+00:00",
                    "open": 100,
                    "high": 110,
                    "low": 90,
                    "close": 105,
                    "volume": 1000,
                }
            ],
            "4h": [
                {
                    "timestamp": "2026-05-12 04:00:00+00:00",
                    "open": 104,
                    "high": 108,
                    "low": 101,
                    "close": 107,
                    "volume": 400,
                }
            ],
        }
    }

    html = build_html([], {}, charts=charts)

    assert 'data-default-tf="4h"' in html
    assert 'class="tf-btn active" data-tf="4h"' in html
    assert 'class="tf-btn" data-tf="1d"' in html
    assert '"BYBIT:BTCUSDT.P":{"1d":[{' in html
    assert '"4h":[{' in html
    assert "slide.dataset.currentTf" in html


def test_load_charts_keeps_eight_month_daily_and_shorter_proportional_4h_windows(monkeypatch, tmp_path):
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    daily_rows = [{"timestamp": f"2025-01-{(i % 28) + 1:02d}", "close": i} for i in range(600)]
    four_hour_rows = [{"timestamp": f"2025-01-01 {i % 24:02d}:00:00+00:00", "close": i} for i in range(3400)]
    charts_dir.joinpath("BYBIT_BTCUSDT_P_1d.json").write_text(
        '{"symbol":"BYBIT:BTCUSDT.P","timeframe":"1d","data":' + __import__("json").dumps(daily_rows) + "}",
        encoding="utf-8",
    )
    charts_dir.joinpath("BYBIT_BTCUSDT_P_4h.json").write_text(
        '{"symbol":"BYBIT:BTCUSDT.P","timeframe":"4h","data":' + __import__("json").dumps(four_hour_rows) + "}",
        encoding="utf-8",
    )
    monkeypatch.setattr(build_html_module, "CHARTS_DIR", charts_dir)

    charts = build_html_module.load_charts()

    assert len(charts["BYBIT:BTCUSDT.P"]["1d"]) == 244
    assert charts["BYBIT:BTCUSDT.P"]["1d"][0]["close"] == 356
    assert len(charts["BYBIT:BTCUSDT.P"]["4h"]) == 528
    assert charts["BYBIT:BTCUSDT.P"]["4h"][0]["close"] == 2872


def test_build_html_uses_vertical_desktop_chart_stack_with_compact_lower_panes():
    charts = {
        "BYBIT:BTCUSDT.P": {
            "4h": [
                {
                    "timestamp": "2026-05-12 04:00:00+00:00",
                    "open": 104,
                    "high": 108,
                    "low": 101,
                    "close": 107,
                    "volume": 400,
                    "funding_rate": 0.0001,
                }
            ]
        }
    }

    html = build_html([], {}, charts=charts)

    assert 'class="chart-box price-box"' in html
    assert 'class="chart-box oi-box"' in html
    assert 'class="chart-box vol-box"' in html
    assert 'class="chart-box funding-box"' in html
    assert "grid-template-columns: minmax(0, 1fr);" in html
    assert "grid-template-rows: minmax(0, 4.2fr) minmax(0, 1.6fr) minmax(0, 0.9fr) minmax(0, 0.9fr);" in html
    assert 'grid-template-areas: "price" "oi" "volume" "funding";' in html


def test_build_html_hides_repeated_time_labels_on_lower_charts():
    html = build_html([], {}, charts={})

    assert "function timeScale(min, max, { showTicks = true } = {})" in html
    assert "ticks.display = false;" in html
    assert "candleChart('price-' + id, priceCandles, xMin, xMax, { showXTicks: true })" in html
    assert "candleChart('oi-' + id, oiCandles, xMin, xMax, { showXTicks: false })" in html
    assert "timeScale(xMin, xMax, { showTicks: false })" in html


def test_build_html_makes_volume_and_funding_bars_more_readable():
    html = build_html([], {}, charts={})

    assert "function compactBarDataset(points, colors)" in html
    assert "barPercentage: 1.0" in html
    assert "categoryPercentage: 1.0" in html
    assert "minBarLength: 2" in html


def test_build_html_includes_drag_zoom_selection_and_reset_controls():
    charts = {
        "BYBIT:BTCUSDT.P": {
            "4h": [
                {
                    "timestamp": "2026-05-12 04:00:00+00:00",
                    "open": 104,
                    "high": 108,
                    "low": 101,
                    "close": 107,
                    "volume": 400,
                    "funding_rate": 0.0001,
                }
            ]
        }
    }

    html = build_html([], {}, charts=charts)
    crypto_slide = html.split('id="slide-1"', 1)[1]

    assert 'class="zoom-reset"' in crypto_slide
    assert 'class="zoom-reset zoom-reset-overlay"' in crypto_slide
    assert ".zoom-selection" in html
    assert "function attachZoomSelection(slideEl, chart)" in html
    assert "function applyZoomRange(slideEl, min, max)" in html
    assert "function setVisibleYScale(chart, min, max)" in html
    assert "Chart.getChart(canvas)" in html


def test_build_html_overview_table_lists_all_symbols_with_clickable_tickers():
    """First slide must show every watchlist symbol, even without signal."""
    charts = {
        "BYBIT:ADAUSDT.P": {"4h": [{"timestamp": "2026-05-12 00:00:00+00:00", "open": 0.27, "high": 0.28, "low": 0.26, "close": 0.28, "volume": 100}]},
        "BINANCE:NEARUSD.P": {"4h": [{"timestamp": "2026-05-12 00:00:00+00:00", "open": 1.26, "high": 1.30, "low": 1.25, "close": 1.29, "volume": 200}]},
    }
    scan = {
        "BYBIT:ADAUSDT.P": {"symbol": "BYBIT:ADAUSDT.P", "close": 0.28, "signal_active": False},
        "BINANCE:NEARUSD.P": {"symbol": "BINANCE:NEARUSD.P", "close": 1.29, "signal_active": False},
    }

    html = build_html([], scan, charts=charts)

    overview = html.split('<table class="overview-table">', 1)[1].split("</table>", 1)[0]
    # Both symbols must appear
    assert "ADA" in overview
    assert "NEAR" in overview
    # Tickers must be clickable (data-goto attribute) so user navigates to slide
    assert overview.count("sym-link") >= 2
    assert 'data-goto="' in overview


def test_build_html_overview_table_sorts_signals_and_surges_to_top():
    """Rows with active signal come first, then OI/volume surges, then the rest."""
    charts = {
        "BYBIT:AAA.P": {"4h": [{"timestamp": "2026-05-12 00:00:00+00:00", "open": 1, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 1}]},
        "BYBIT:BBB.P": {"4h": [{"timestamp": "2026-05-12 00:00:00+00:00", "open": 1, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 1}]},
        "BYBIT:CCC.P": {"4h": [{"timestamp": "2026-05-12 00:00:00+00:00", "open": 1, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 1}]},
    }
    scan = {
        "BYBIT:AAA.P": {"symbol": "BYBIT:AAA.P", "close": 1.05, "signal_active": False},
        "BYBIT:BBB.P": {"symbol": "BYBIT:BBB.P", "close": 1.05, "signal_active": False, "oi_surge_flag": True, "oi_3bar_change_pct": 0.06},
        "BYBIT:CCC.P": {"symbol": "BYBIT:CCC.P", "close": 1.05, "signal_active": True, "alert_triggered": True, "early_bullish_score": 80},
    }

    html = build_html([], scan, charts=charts)
    overview = html.split('<table class="overview-table">', 1)[1].split("</table>", 1)[0]

    pos_signal = overview.find("CCC")
    pos_surge  = overview.find("BBB")
    pos_plain  = overview.find("AAA")

    assert 0 < pos_signal < pos_surge < pos_plain
    assert "OI&nbsp;SURGE" in overview
    assert "ENTRY" in overview


def test_build_html_renders_back_to_overview_button_on_each_crypto_slide():
    charts = {
        "BYBIT:BTCUSDT.P": {
            "4h": [
                {"timestamp": "2026-05-12 00:00:00+00:00", "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000}
            ]
        }
    }
    scan = {"BYBIT:BTCUSDT.P": {"symbol": "BYBIT:BTCUSDT.P", "close": 105}}

    html = build_html([], scan, charts=charts)

    crypto_slide = html.split('id="slide-1"', 1)[1]
    assert 'class="back-btn"' in crypto_slide
    assert 'data-goto="0"' in crypto_slide
    # Overview itself must not get a back button on slide 0.
    overview_slide = html.split('id="slide-0"', 1)[1].split('id="slide-1"', 1)[0]
    assert 'class="back-btn"' not in overview_slide


def test_build_html_renders_long_short_ratio_chip_when_scan_has_ratio():
    charts = {
        "BYBIT:BTCUSDT.P": {
            "4h": [
                {"timestamp": "2026-05-12 00:00:00+00:00", "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000}
            ]
        }
    }
    scan = {
        "BYBIT:BTCUSDT.P": {
            "symbol": "BYBIT:BTCUSDT.P",
            "close": 105,
            "long_account_ratio": 0.65,
            "short_account_ratio": 0.35,
        }
    }

    html = build_html([], scan, charts=charts)

    crypto_slide = html.split('id="slide-1"', 1)[1]
    assert "L/S&nbsp;65% / 35%" in crypto_slide
    # Overview row mirrors the same data.
    overview = html.split('<table class="overview-table">', 1)[1].split("</table>", 1)[0]
    assert "65% / 35%" in overview


def test_build_html_long_short_chip_falls_back_to_em_dash_when_ratio_missing():
    charts = {
        "BYBIT:BTCUSDT.P": {
            "4h": [
                {"timestamp": "2026-05-12 00:00:00+00:00", "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000}
            ]
        }
    }
    scan = {"BYBIT:BTCUSDT.P": {"symbol": "BYBIT:BTCUSDT.P", "close": 105}}

    html = build_html([], scan, charts=charts)

    crypto_slide = html.split('id="slide-1"', 1)[1]
    assert "L/S&nbsp;—" in crypto_slide


def test_overview_table_falls_back_to_chart_metrics_when_scan_csv_missing():
    """When latest_scan.csv is absent, the overview table must still show
    funding, OI 3-bar and volume 3-bar derived from chart candles."""
    candles = []
    for i in range(60):
        candles.append({
            "timestamp": f"2026-05-{i // 24 + 1:02d} {i % 24:02d}:00:00+00:00",
            "open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0,
            "volume": 100, "open_interest": 1000.0,
            "funding_rate": -0.00002,
        })
    candles[-3]["open_interest"] = 1020
    candles[-2]["open_interest"] = 1040
    candles[-1]["open_interest"] = 1060
    candles[-3]["volume"] = 800
    candles[-2]["volume"] = 800
    candles[-1]["volume"] = 800

    html = build_html([], {}, charts={"BINANCE:NEARUSD.P": {"4h": candles}})

    overview = html.split('<table class="overview-table">', 1)[1].split("</table>", 1)[0]
    assert "NEAR" in overview
    assert "+6.0%" in overview
    assert "VOL&nbsp;SURGE" in overview
    assert "OI&nbsp;SURGE" in overview
    assert "NEGATIVE" in overview
