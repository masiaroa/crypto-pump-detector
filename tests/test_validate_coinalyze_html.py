from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_coinalyze_html", ROOT / "scripts" / "validate_coinalyze_html.py"
)
validate_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = validate_module
SPEC.loader.exec_module(validate_module)


def test_validate_coinalyze_html_accepts_setup_state_without_data(tmp_path):
    html_path = tmp_path / "coinalyze.html"
    html_path.write_text(
        '<main class="setup-state">COINALYZE_API_KEY</main>'
        '<script>const COINALYZE_DATA = {};</script>',
        encoding="utf-8",
    )

    result = validate_module.validate_coinalyze_html(html_path)

    assert result.ok is True
    assert result.coin_count == 0
    assert result.setup_state is True


def test_validate_coinalyze_html_rejects_coin_slide_without_data():
    result = validate_module.validate_coinalyze_html_text(
        '<section class="coin-slide" data-base="BTC"></section>'
        '<script>const COINALYZE_DATA = {};</script>'
    )

    assert result.ok is False
    assert "no data" in result.message


def test_validate_coinalyze_html_requires_stacked_metric_panels_for_data():
    result = validate_module.validate_coinalyze_html_text(
        '<section class="coin-slide" data-base="BTC"></section>'
        '<script>const COINALYZE_DATA = {"BTC":{"4h":{"series":[{"close":1}]}}};</script>'
    )

    assert result.ok is False
    assert "metric panels" in result.message


def test_validate_coinalyze_html_accepts_data_with_required_ui():
    html = (
        '<section class="coin-slide" data-base="BTC">'
        '<div class="chart-card volume-card"></div>'
        '<div class="futures-grid">'
        '<div class="chart-card oi-card"></div>'
        '<div class="chart-card funding-card"></div>'
        '<div class="chart-card liquidation-card"></div>'
        '<div class="chart-card long-short-card"></div>'
        '</div>'
        '<button class="tf-btn" data-tf="4h"></button>'
        '<a href="https://coinalyze.net/">Coinalyze</a>'
        '</section>'
        '<script>const COINALYZE_DATA = {"BTC":{"4h":{"series":[{"close":1}]}}};</script>'
    )

    result = validate_module.validate_coinalyze_html_text(html)

    assert result.ok is True
    assert result.coin_count == 1
