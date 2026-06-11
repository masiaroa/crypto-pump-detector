from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_coinalyze_html", ROOT / "scripts" / "build_coinalyze_html.py"
)
build_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build_module)


def _snapshot(base: str, timeframe: str) -> dict:
    return {
        "base": base,
        "raw_symbol": f"BYBIT:{base}USDT.P",
        "timeframe": timeframe,
        "generated_at": "2026-06-05T12:00:00Z",
        "contracts": [{"symbol": f"{base}USDT.6", "exchange": "BYBIT"}],
        "primary_price_contract": f"{base}USDT.6",
        "series": [
            {
                "timestamp": "2026-06-05T08:00:00Z",
                "open": 100,
                "high": 110,
                "low": 95,
                "close": 105,
                "volume": 1000,
                "open_interest": 5000,
                "funding_rate": 0.0001,
                "long_liquidations": 100,
                "short_liquidations": 50,
                "long_account_ratio": 0.6,
                "short_account_ratio": 0.4,
                "long_short_ratio": 1.5,
                "contracts_total": 1,
                "oi_contracts": 1,
                "funding_contracts": 1,
                "liquidation_contracts": 1,
                "long_short_contracts": 1,
            }
        ],
        "diagnostics": [],
    }


def test_load_snapshots_reads_cache_files(tmp_path, monkeypatch):
    cache_dir = tmp_path / "coinalyze"
    cache_dir.mkdir()
    (cache_dir / "BTC_4h.json").write_text(json.dumps(_snapshot("BTC", "4h")), encoding="utf-8")
    (cache_dir / "_manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(build_module, "COINALYZE_DIR", cache_dir)

    snapshots = build_module.load_snapshots()

    assert len(snapshots) == 1
    assert snapshots[0]["base"] == "BTC"


def test_build_coinalyze_html_renders_coin_slides_timeframe_toggle_and_stacked_metric_panels():
    html = build_module.build_html(
        [_snapshot("BTC", "4h"), _snapshot("BTC", "1d")],
        manifest={"generated_at": "2026-06-05T12:00:00Z", "symbols": []},
    )

    assert 'id="coinalyze-slide-1"' in html
    assert 'data-base="BTC"' in html
    assert 'class="tf-btn active" data-tf="4h"' in html
    assert 'class="tf-btn" data-tf="1d"' in html
    assert 'class="metric-tabs"' not in html
    assert "data-metric=" not in html
    assert 'class="chart-card price-card"' in html
    assert 'class="chart-card volume-card"' in html
    assert 'class="futures-grid"' in html
    assert 'class="chart-card oi-card"' in html
    assert 'class="chart-card funding-card"' in html
    assert 'class="chart-card liquidation-card"' in html
    assert 'class="chart-card long-short-card"' in html
    assert "candlestickPlugin" in html
    assert "volumeChart" in html
    assert ".coin-slide { height: 100vh;" in html
    assert "Transparent project aggregate" in html
    assert "https://coinalyze.net/" in html
    assert "const COINALYZE_DATA =" in html


def test_build_coinalyze_html_renders_setup_state_without_cache():
    html = build_module.build_html([], manifest={})

    assert "COINALYZE_API_KEY" in html
    assert "python scripts/coinalyze_refresh.py --force" in html
    assert "const COINALYZE_DATA = {}" in html


def test_build_coinalyze_html_writes_output(tmp_path, monkeypatch):
    out = tmp_path / "docs" / "coinalyze.html"
    monkeypatch.setattr(build_module, "DOCS_DIR", out.parent)
    monkeypatch.setattr(build_module, "COINALYZE_DIR", tmp_path / "missing")

    written = build_module.write_html([_snapshot("ETH", "4h")], {}, output_path=out)

    assert written == out
    assert out.exists()
    assert "ETH" in out.read_text(encoding="utf-8")
