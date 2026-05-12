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

    assert "$0.2798" in html
    assert "+3.6%" in html
    assert "Bull&nbsp;" not in html
    assert "Risk&nbsp;" not in html
    assert "OI&nbsp;" not in html
    assert "NEGATIVE" not in html


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

    assert charts == {"BYBIT:ADAUSDT.P": [{"close": 0.25}]}
