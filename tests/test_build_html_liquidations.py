import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_html", ROOT / "scripts" / "build_html.py")
build_html_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build_html_module)
build_html = build_html_module.build_html


def _btc_chart() -> dict:
    return {
        "BINANCE:BTCUSDT.P": [
            {
                "timestamp": "2026-05-10 00:00:00+00:00",
                "open": 60000, "high": 62000, "low": 59000, "close": 61000, "volume": 100,
            }
        ]
    }


def test_build_html_renders_liquidation_totals_in_header():
    liquidations = {
        "BINANCE:BTCUSDT.P": {"long_notional": 150000, "short_notional": 250000},
    }

    html = build_html([], {}, charts=_btc_chart(), liquidations=liquidations)

    assert "Longs liquidated" in html
    assert "Shorts liquidated" in html
    assert "$150K" in html
    assert "$250K" in html


def test_build_html_no_longer_embeds_per_bar_liquidation_data():
    """The dashboard only needs totals — no per-bar array / overlay plugin / global."""
    liquidations = {"BINANCE:BTCUSDT.P": {"long_notional": 1.0, "short_notional": 2.0}}

    html = build_html([], {}, charts=_btc_chart(), liquidations=liquidations)

    assert "LIQUIDATION_DATA" not in html
    assert "liquidationOverlayPlugin" not in html


def test_build_html_embeds_per_tf_totals_as_data_attributes():
    """Each crypto slide carries 4h+1d totals so JS can swap them when TF changes."""
    charts = {
        "BINANCE:BTCUSDT.P": {
            "4h": [{"timestamp": "2026-05-10 00:00:00+00:00", "open": 60000, "high": 62000, "low": 59000, "close": 61000, "volume": 100}],
            "1d": [{"timestamp": "2026-05-10 00:00:00+00:00", "open": 60000, "high": 62000, "low": 59000, "close": 61000, "volume": 100}],
        }
    }
    liquidations = {
        "BINANCE:BTCUSDT.P": {
            "4h": {"long_notional": 12000, "short_notional": 3000},
            "1d": {"long_notional": 90000, "short_notional": 80000},
        }
    }

    html = build_html([], {}, charts=charts, liquidations=liquidations)

    crypto_slide = html.split('id="slide-1"', 1)[1]
    assert 'data-liq-long-4h="12000"' in crypto_slide
    assert 'data-liq-short-4h="3000"' in crypto_slide
    assert 'data-liq-long-1d="90000"' in crypto_slide
    assert 'data-liq-short-1d="80000"' in crypto_slide
    # 4h is the default TF — header should mirror it on first render.
    assert "$12K" in crypto_slide
    assert "$3K" in crypto_slide


def test_load_liquidations_reads_totals_files(tmp_path, monkeypatch):
    liq_dir = tmp_path / "liquidations"
    liq_dir.mkdir()
    (liq_dir / "BINANCE_BTCUSDT_P_4h.json").write_text(
        '{"symbol":"BINANCE:BTCUSDT.P","timeframe":"4h","long_notional":7500,"short_notional":1200}',
        encoding="utf-8",
    )
    monkeypatch.setattr(build_html_module, "LIQUIDATIONS_DIR", liq_dir)

    liquidations = build_html_module.load_liquidations()

    assert liquidations == {"BINANCE:BTCUSDT.P": {"4h": {"long": 7500.0, "short": 1200.0}}}


def test_load_liquidations_falls_back_to_legacy_per_bar_files(tmp_path, monkeypatch):
    """Older JSON files written before the schema change still sum to totals."""
    liq_dir = tmp_path / "liquidations"
    liq_dir.mkdir()
    (liq_dir / "BINANCE_BTCUSDT_P_4h.json").write_text(
        '{"symbol":"BINANCE:BTCUSDT.P","timeframe":"4h","data":['
        '{"timestamp":"2026-05-10T00:00:00Z","side":"long","notional":150},'
        '{"timestamp":"2026-05-10T04:00:00Z","side":"short","notional":75}'
        "]}",
        encoding="utf-8",
    )
    monkeypatch.setattr(build_html_module, "LIQUIDATIONS_DIR", liq_dir)

    liquidations = build_html_module.load_liquidations()

    assert liquidations["BINANCE:BTCUSDT.P"]["4h"] == {"long": 150.0, "short": 75.0}
