#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


REQUIRED_PANELS = ("volume-card", "oi-card", "funding-card", "liquidation-card", "long-short-card")


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str
    coin_count: int = 0
    setup_state: bool = False
    errors: tuple[str, ...] = field(default_factory=tuple)


def _extract_data(html: str) -> dict:
    match = re.search(r"const\s+COINALYZE_DATA\s*=\s*(\{.*?\});</script>", html, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def validate_coinalyze_html_text(html: str) -> ValidationResult:
    data = _extract_data(html)
    coin_count = len(data)
    setup_state = 'class="setup-state"' in html
    slide_count = html.count('class="coin-slide"')
    errors: list[str] = []

    if slide_count and not data:
        errors.append("coin slides exist but no data was embedded")

    if not data and not setup_state:
        errors.append("no Coinalyze data and no setup state")

    if data:
        missing_panels = [panel for panel in REQUIRED_PANELS if panel not in html]
        if missing_panels:
            errors.append(f"missing metric panels: {', '.join(missing_panels)}")
        if 'class="futures-grid"' not in html:
            errors.append("missing futures grid")
        if 'class="tf-btn"' not in html:
            errors.append("missing timeframe buttons")
        if "https://coinalyze.net/" not in html:
            errors.append("missing Coinalyze source link")

    ok = not errors
    return ValidationResult(
        ok=ok,
        message="coinalyze HTML validation passed" if ok else "; ".join(errors),
        coin_count=coin_count,
        setup_state=setup_state,
        errors=tuple(errors),
    )


def validate_coinalyze_html(path: Path) -> ValidationResult:
    if not path.exists():
        return ValidationResult(False, f"{path} does not exist")
    return validate_coinalyze_html_text(path.read_text(encoding="utf-8"))


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/coinalyze.html")
    result = validate_coinalyze_html(path)
    print(f"Coins:       {result.coin_count}")
    print(f"Setup state: {result.setup_state}")
    print(result.message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
