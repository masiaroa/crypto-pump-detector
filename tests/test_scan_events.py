"""Event-history export: per-candle flags become typed events with priority."""

import importlib.util
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scan_script_events", ROOT / "scripts" / "scan.py")
scan_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(scan_module)


def _history(**flag_columns) -> pd.DataFrame:
    n = 5
    base = {
        "timestamp": pd.date_range("2026-06-01", periods=n, freq="4h", tz="UTC"),
        "close": [10.0] * n,
        "price_return_pct": [0.01] * n,
        "oi_change_pct": [0.01] * n,
        "volume_zscore": [0.5] * n,
        "volume_ratio": [1.0] * n,
        "funding_classification": ["NEUTRAL"] * n,
        "early_bullish_score": [10.0] * n,
        "blowoff_risk_score": [5.0] * n,
        "squeeze_setup_score": [20.0] * n,
        "whale_accum_score": [20.0] * n,
        "signal_active_flag": [False] * n,
        "pre_alert_flag": [False] * n,
        "oi_surge_flag": [False] * n,
        "volume_surge_flag": [False] * n,
        "squeeze_setup_flag": [False] * n,
        "whale_accum_flag": [False] * n,
        "squeeze_ignition_flag": [False] * n,
        "whale_pump_flag": [False] * n,
    }
    for column, true_positions in flag_columns.items():
        base[column] = [i in true_positions for i in range(n)]
    return pd.DataFrame(base)


def test_event_history_emits_ignition_and_pump_events(tmp_path):
    details = {
        ("BYBIT:AAAUSDT.P", "4h"): _history(squeeze_ignition_flag={1}),
        ("BYBIT:BBBUSDT.P", "4h"): _history(whale_pump_flag={2}),
    }
    out = tmp_path / "event_history.csv"

    scan_module._export_event_history(details, out)

    df = pd.read_csv(out)
    assert set(df["event_type"]) == {"SQUEEZE_IGNITION", "WHALE_PUMP"}
    assert df.loc[df["event_type"] == "SQUEEZE_IGNITION", "symbol"].iloc[0] == "AAA"
    assert df.loc[df["event_type"] == "WHALE_PUMP", "symbol"].iloc[0] == "BBB"


def test_event_history_priority_entry_beats_ignition_beats_setup(tmp_path):
    # All flags true on the same candle → one event with the top type.
    details = {
        ("BYBIT:CCCUSDT.P", "4h"): _history(
            signal_active_flag={3},
            squeeze_ignition_flag={3},
            whale_pump_flag={3},
            squeeze_setup_flag={3},
            whale_accum_flag={3},
        ),
        ("BYBIT:DDDUSDT.P", "4h"): _history(
            squeeze_ignition_flag={3},
            whale_pump_flag={3},
            squeeze_setup_flag={3},
        ),
        ("BYBIT:EEEUSDT.P", "4h"): _history(
            whale_pump_flag={3},
            whale_accum_flag={3},
        ),
    }
    out = tmp_path / "event_history.csv"

    scan_module._export_event_history(details, out)

    df = pd.read_csv(out).set_index("symbol")
    assert df.loc["CCC", "event_type"] == "ENTRY"
    assert df.loc["DDD", "event_type"] == "SQUEEZE_IGNITION"
    assert df.loc["EEE", "event_type"] == "WHALE_PUMP"
