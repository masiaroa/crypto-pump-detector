import importlib.util
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scan_script", ROOT / "scripts" / "scan.py")
scan_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(scan_module)


def test_export_liquidations_writes_minimal_long_short_schema(tmp_path):
    data = {
        ("BINANCE:BTCUSDT.P", "4h"): pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2026-05-10T00:00:00Z"),
                    "price": 60500.0,
                    "quantity": 0.5,
                    "notional": 30250.0,
                    "side": "long",
                    "kind": "executed",
                    "source": "coinalyze",
                },
                {
                    "timestamp": pd.Timestamp("2026-05-10T04:00:00Z"),
                    "price": 60500.0,
                    "quantity": 0.0,
                    "notional": 11000.0,
                    "side": "short",
                    "kind": "executed",
                    "source": "coinalyze",
                },
            ]
        )
    }

    scan_module._export_liquidations(data, tmp_path)

    payload = json.loads((tmp_path / "BINANCE_BTCUSDT_P_4h.json").read_text())
    assert payload["symbol"] == "BINANCE:BTCUSDT.P"
    assert payload["timeframe"] == "4h"
    rows = payload["data"]
    assert len(rows) == 2
    # Only timestamp/side/notional travel to the static dashboard.
    assert set(rows[0].keys()) == {"timestamp", "side", "notional"}
    sides = {r["side"]: r["notional"] for r in rows}
    assert sides == {"long": 30250.0, "short": 11000.0}


def test_export_liquidations_aggregates_multiple_rows_per_bar(tmp_path):
    data = {
        ("BINANCE:BTCUSDT.P", "4h"): pd.DataFrame(
            [
                {"timestamp": pd.Timestamp("2026-05-10T00:00:00Z"), "side": "long", "notional": 100.0},
                {"timestamp": pd.Timestamp("2026-05-10T00:00:00Z"), "side": "long", "notional": 50.0},
                {"timestamp": pd.Timestamp("2026-05-10T00:00:00Z"), "side": "short", "notional": 25.0},
            ]
        )
    }

    scan_module._export_liquidations(data, tmp_path)

    rows = json.loads((tmp_path / "BINANCE_BTCUSDT_P_4h.json").read_text())["data"]
    by_side = {r["side"]: r["notional"] for r in rows}
    assert by_side == {"long": 150.0, "short": 25.0}
