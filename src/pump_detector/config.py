from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"

VALID_TIMEFRAMES = {"1h", "4h", "1d"}


@dataclass(frozen=True)
class Settings:
    timeframes: list[str]
    alert_conditions: dict[str, Any]
    thresholds: dict[str, Any]
    storage: dict[str, Any]


DEFAULT_SETTINGS = {
    "timeframes": ["4h"],
    "alert_conditions": {
        "require_price_impulse": True,
        "require_oi_impulse": True,
        "require_first_impulse": True,
        "require_volume_confirmation": False,
        "require_breakout_20": False,
        "require_sma200_reclaim": False,
        "allowed_funding_classes": ["NEGATIVE", "NEUTRAL", "POSITIVE", "HOT"],
    },
    "thresholds": {
        "lookback_stats": 100,
        "lookback_no_previous_signal": 10,
        "price_zscore_threshold": 2.5,
        "oi_zscore_threshold": 2.5,
        "volume_zscore_threshold": 1.5,
        "close_position_min": 0.65,
        "funding_hot_threshold": 0.0005,
        "funding_extreme_percentile": 95,
        "max_recent_price_run_pct": 0.45,
        "max_consecutive_oi_expansion": 3,
    },
    "storage": {
        "sqlite_path": "data/signals.sqlite",
        "alerts_csv": "data/alerts.csv",
    },
}


def ensure_default_files() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings_path = CONFIG_DIR / "settings.yaml"
    if not settings_path.exists():
        write_yaml(settings_path, DEFAULT_SETTINGS)
    watchlist_path = CONFIG_DIR / "watchlist.yaml"
    if not watchlist_path.exists():
        write_yaml(watchlist_path, {"symbols": []})


def load_settings(path: Path | None = None) -> Settings:
    ensure_default_files()
    raw = read_yaml(path or CONFIG_DIR / "settings.yaml")
    merged = DEFAULT_SETTINGS | (raw or {})

    # Allow env-var override: SCAN_TIMEFRAME=1d  (single value)
    env_tf = os.environ.get("SCAN_TIMEFRAME", "").strip()
    if env_tf:
        if env_tf not in VALID_TIMEFRAMES:
            raise ValueError(
                f"SCAN_TIMEFRAME='{env_tf}' is not valid. "
                f"Allowed values: {sorted(VALID_TIMEFRAMES)}"
            )
        merged["timeframes"] = [env_tf]

    return Settings(
        timeframes=list(merged["timeframes"]),
        alert_conditions=dict(merged["alert_conditions"]),
        thresholds=dict(merged["thresholds"]),
        storage=dict(merged["storage"]),
    )


def load_watchlist(path: Path | None = None) -> list[str]:
    ensure_default_files()
    raw = read_yaml(path or CONFIG_DIR / "watchlist.yaml") or {}
    return list(raw.get("symbols", []))


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)
