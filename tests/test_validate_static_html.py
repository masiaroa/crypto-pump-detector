import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_static_html", ROOT / "scripts" / "validate_static_html.py"
)
validate_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = validate_module
SPEC.loader.exec_module(validate_module)


def test_validate_static_html_rejects_slides_without_chart_data(tmp_path):
    html_path = tmp_path / "index.html"
    html_path.write_text(
        '<section class="slide" id="slide-1" data-symbol="BYBIT:ADAUSDT.P"></section>'
        "<script>const CHART_DATA = {};</script>",
        encoding="utf-8",
    )

    result = validate_module.validate_static_html(html_path)

    assert result.ok is False
    assert "CHART_DATA is empty" in result.message


def test_validate_static_html_accepts_embedded_chart_data(tmp_path):
    html_path = tmp_path / "index.html"
    html_path.write_text(
        '<section class="slide" id="slide-1" data-symbol="BYBIT:ADAUSDT.P"></section>'
        '<script>const CHART_DATA = {"BYBIT:ADAUSDT.P":[{"close":0.25}]};</script>',
        encoding="utf-8",
    )

    result = validate_module.validate_static_html(html_path)

    assert result.ok is True
    assert result.slide_count == 1
    assert result.chart_series_count == 1
