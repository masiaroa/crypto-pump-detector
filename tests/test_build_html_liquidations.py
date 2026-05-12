import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_html", ROOT / "scripts" / "build_html.py")
build_html_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build_html_module)
build_html = build_html_module.build_html


def test_build_html_embeds_liquidation_data_and_overlay_renderer():
    charts = {
        "BINANCE:BTCUSDT.P": [
            {
                "timestamp": "2026-05-10 00:00:00+00:00",
                "open": 60000,
                "high": 62000,
                "low": 59000,
                "close": 61000,
                "volume": 100,
            }
        ]
    }
    liquidations = {
        "BINANCE:BTCUSDT.P": [
            {
                "timestamp": "2026-05-10 00:00:00+00:00",
                "price": 60500,
                "notional": 100000,
                "side": "long",
                "kind": "executed",
                "source": "binance",
            },
            {
                "timestamp": "2026-05-10 00:00:00+00:00",
                "price": 63000,
                "notional": 250000,
                "side": "short",
                "kind": "projected",
                "source": "coinglass",
            },
        ]
    }

    html = build_html([], {}, charts=charts, liquidations=liquidations)

    assert "const LIQUIDATION_DATA =" in html
    assert "liquidationOverlayPlugin" in html
    assert "Liquidations" in html
    assert '"kind":"projected"' in html
    assert '"kind":"executed"' in html


def test_build_html_shows_liquidated_long_and_short_dollar_totals():
    charts = {
        "BINANCE:BTCUSDT.P": [
            {
                "timestamp": "2026-05-10 00:00:00+00:00",
                "open": 60000,
                "high": 62000,
                "low": 59000,
                "close": 61000,
                "volume": 100,
            }
        ]
    }
    liquidations = {
        "BINANCE:BTCUSDT.P": [
            {"notional": 100000, "side": "long"},
            {"notional": 250000, "side": "short"},
            {"notional": 50000, "side": "long"},
            {"notional": 900000, "side": "unknown"},
            {"notional": 800000},
        ]
    }

    html = build_html([], {}, charts=charts, liquidations=liquidations)

    assert "Longs liquidated" in html
    assert "Shorts liquidated" in html
    assert "$150K" in html
    assert "$250K" in html
    assert "$900K" not in html
    assert "notional" not in html.lower()
