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
