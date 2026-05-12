#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str
    slide_count: int = 0
    crypto_slide_count: int = 0
    chart_series_count: int = 0


def _extract_chart_data(html: str) -> dict:
    match = re.search(r"const\s+CHART_DATA\s*=\s*(\{.*?\});</script>", html, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def validate_static_html(path: Path) -> ValidationResult:
    if not path.exists():
        return ValidationResult(False, f"{path} does not exist")

    html = path.read_text(encoding="utf-8")
    slide_count = html.count('class="slide"')
    crypto_slide_count = html.count("data-symbol=")
    chart_data = _extract_chart_data(html)
    chart_series_count = len(chart_data)

    if crypto_slide_count > 0 and not chart_data:
        return ValidationResult(
            False,
            "CHART_DATA is empty while crypto slides exist",
            slide_count,
            crypto_slide_count,
            chart_series_count,
        )

    if crypto_slide_count > 0 and chart_series_count <= 0:
        return ValidationResult(
            False,
            "crypto slides exist but no chart series were embedded",
            slide_count,
            crypto_slide_count,
            chart_series_count,
        )

    return ValidationResult(
        True,
        "static HTML validation passed",
        slide_count,
        crypto_slide_count,
        chart_series_count,
    )


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/index.html")
    result = validate_static_html(path)
    print(f"Slides: {result.slide_count}")
    print(f"Crypto slides: {result.crypto_slide_count}")
    print(f"Embedded chart series: {result.chart_series_count}")
    print(result.message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
