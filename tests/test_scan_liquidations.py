import importlib.util
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scan_script", ROOT / "scripts" / "scan.py")
scan_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(scan_module)


def test_export_liquidations_writes_per_symbol_json(tmp_path):
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
                    "source": "binance",
                }
            ]
        )
    }

    scan_module._export_liquidations(data, tmp_path)

    payload = json.loads((tmp_path / "BINANCE_BTCUSDT_P_4h.json").read_text())
    assert payload["symbol"] == "BINANCE:BTCUSDT.P"
    assert payload["timeframe"] == "4h"
    assert payload["data"][0]["kind"] == "executed"
    assert payload["data"][0]["price"] == 60500.0
