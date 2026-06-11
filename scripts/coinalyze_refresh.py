#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from pump_detector.config import load_settings, load_watchlist
from pump_detector.coinalyze_dashboard import refresh_watchlist


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh Coinalyze dashboard cache.")
    parser.add_argument("--force", action="store_true", help="Ignore fresh cache files and refetch from Coinalyze.")
    args = parser.parse_args(argv)

    settings = load_settings()
    manifest = refresh_watchlist(
        load_watchlist(),
        settings=settings.coinalyze_dashboard,
        force=args.force,
    )

    refreshed = sum(1 for row in manifest.get("symbols", []) if row.get("status") == "refreshed")
    cached = sum(1 for row in manifest.get("symbols", []) if row.get("status") == "cached")
    errors = [row for row in manifest.get("symbols", []) if row.get("status") in {"error", "missing_key"}]
    print(f"[coinalyze] refreshed={refreshed} cached={cached} issues={len(errors)}")
    if manifest.get("diagnostics"):
        for diagnostic in manifest["diagnostics"]:
            print(f"[coinalyze] {diagnostic.get('code')}: {diagnostic.get('message')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
