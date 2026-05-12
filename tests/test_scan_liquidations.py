import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

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


def test_fetch_liquidations_for_details_uses_existing_ws_history_when_exporting_static(tmp_path):
    history = tmp_path / "_ws_history.jsonl"
    history.write_text(
        json.dumps(
            {
                "timestamp_ms": int(pd.Timestamp.now(tz="UTC").timestamp() * 1000),
                "timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
                "symbol": "BTCUSDT",
                "price": 60500.0,
                "quantity": 0.5,
                "notional": 30250.0,
                "side": "long",
                "kind": "executed",
                "source": "binance_ws",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        liquidations={
            "enabled": True,
            "executed": {
                "enabled": False,
                "history_file": str(history),
                "max_age_days": 14,
            },
            "projected": {"enabled": False},
            "coinalyze": {"enabled": False},
        }
    )

    liquidations = scan_module._fetch_liquidations_for_details(
        {("BINANCE:BTCUSDT.P", "4h"): pd.DataFrame()},
        settings,
    )

    frame = liquidations[("BINANCE:BTCUSDT.P", "4h")]
    assert frame.iloc[0]["notional"] == 30250.0
    assert frame.iloc[0]["side"] == "long"
