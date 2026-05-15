import importlib.util
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scan_script", ROOT / "scripts" / "scan.py")
scan_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(scan_module)


def test_export_liquidations_writes_long_short_totals_per_timeframe(tmp_path):
    data = {
        ("BINANCE:BTCUSDT.P", "4h"): pd.DataFrame(
            [
                {"timestamp": pd.Timestamp("2026-05-10T00:00:00Z"), "side": "long", "notional": 100.0},
                {"timestamp": pd.Timestamp("2026-05-10T04:00:00Z"), "side": "long", "notional": 50.0},
                {"timestamp": pd.Timestamp("2026-05-10T00:00:00Z"), "side": "short", "notional": 25.0},
            ]
        )
    }

    scan_module._export_liquidations(data, tmp_path)

    payload = json.loads((tmp_path / "BINANCE_BTCUSDT_P_4h.json").read_text())
    assert payload == {
        "symbol": "BINANCE:BTCUSDT.P",
        "timeframe": "4h",
        "long_notional": 150.0,
        "short_notional": 25.0,
    }


def test_export_liquidations_skips_empty_or_zero_frames(tmp_path):
    data = {
        ("BINANCE:BTCUSDT.P", "4h"): pd.DataFrame(),
        ("BYBIT:ETHUSDT.P", "1d"): pd.DataFrame(
            [{"timestamp": pd.Timestamp("2026-05-10T00:00:00Z"), "side": "unknown", "notional": 99.0}]
        ),
    }

    scan_module._export_liquidations(data, tmp_path)

    assert list(tmp_path.iterdir()) == []
