#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str
    slide_count: int = 0
    crypto_slide_count: int = 0
    chart_series_count: int = 0
    tf_toggle_count: int = 0
    dual_tf_symbol_count: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


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

    errors: list[str] = []

    if crypto_slide_count > 0 and not chart_data:
        errors.append("CHART_DATA is empty while crypto slides exist")

    if crypto_slide_count > 0 and chart_series_count <= 0:
        errors.append("crypto slides exist but no chart series were embedded")

    # Count tf-toggle elements (one per crypto slide when dual TF available)
    tf_toggle_count = html.count('class="tf-toggle"')

    # Count symbols with both 1d and 4h keys in CHART_DATA
    dual_tf_symbol_count = 0
    for sym, val in chart_data.items():
        if isinstance(val, dict) and "1d" in val and "4h" in val:
            dual_tf_symbol_count += 1

    # If dual-TF data exists, toggle buttons must be present
    if dual_tf_symbol_count > 0 and tf_toggle_count == 0:
        errors.append(
            f"{dual_tf_symbol_count} symbol(s) have dual TF data but no .tf-toggle found in HTML"
        )

    ok = len(errors) == 0
    message = "; ".join(errors) if errors else "static HTML validation passed"

    return ValidationResult(
        ok=ok,
        message=message,
        slide_count=slide_count,
        crypto_slide_count=crypto_slide_count,
        chart_series_count=chart_series_count,
        tf_toggle_count=tf_toggle_count,
        dual_tf_symbol_count=dual_tf_symbol_count,
        errors=tuple(errors),
    )


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/index.html")
    result = validate_static_html(path)
    print(f"Slides:           {result.slide_count}")
    print(f"Crypto slides:    {result.crypto_slide_count}")
    print(f"Chart series:     {result.chart_series_count}")
    print(f"TF toggles:       {result.tf_toggle_count}")
    print(f"Dual-TF symbols:  {result.dual_tf_symbol_count}")
    print(result.message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
