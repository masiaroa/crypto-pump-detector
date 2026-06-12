from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except ImportError:  # pragma: no cover
    pass


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
    liquidations: dict[str, Any] = field(default_factory=dict)
    coinalyze_dashboard: dict[str, Any] = field(default_factory=dict)
    basis: dict[str, Any] = field(default_factory=dict)
    squeeze: dict[str, Any] = field(default_factory=dict)
    accumulation: dict[str, Any] = field(default_factory=dict)


DEFAULT_SETTINGS = {
    "timeframes": ["4h", "1d"],
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
        "oi_surge_3bar_pct": 0.04,
        "volume_surge_3bar_ratio": 2.5,
    },
    "storage": {
        "sqlite_path": "data/signals.sqlite",
        "alerts_csv": "data/alerts.csv",
    },
    "liquidations": {
        "enabled": True,
        "coinalyze": {
            # Single source: Coinalyze historical aggregated liquidations
            # (free tier). Requires COINALYZE_API_KEY in the environment.
            "enabled": True,
        },
    },
    "coinalyze_dashboard": {
        "enabled": True,
        "timeframes": ["4h", "1d"],
        "cache_dir": "data/coinalyze",
        "max_age_hours": 6,
        "core_exchanges": ["BINANCE", "BYBIT", "OKX"],
    },
    "basis": {
        # Perp premium index vs spot — the primary positioning signal
        # (funding stays as fallback when no basis data exists).
        "enabled": True,
        "hot_threshold": 0.0008,
        "history_limit": 200,
    },
    "squeeze": {
        "enabled": True,
        "lookback_divergence": 20,
        "oi_build_min_pct": 0.05,
        "price_flat_max_pct": 0.02,
        "funding_low_percentile": 20,
        "ls_long_max": 0.45,
        "stop_cluster_max_distance_pct": 0.10,
        "compression_percentile": 15,
        "setup_score_min": 55,
        "setup_min_oi_points": 15,
        # Component weights; zero out the ones you don't trust.
        "weight_oi_build": 35,
        "weight_stop_magnet": 15,
        "weight_compression": 10,
        "weight_basis": 15,
        "weight_funding": 15,
        "weight_ls": 10,
    },
    "accumulation": {
        "enabled": True,
        "lookback": 20,
        "cvd_full_share": 0.12,
        "price_quiet_max_pct": 0.05,
        "accum_score_min": 55,
        "accum_min_flow_points": 15,
        "retail_long_max": 0.55,
        "spot_full_ratio": 2.0,
        "spot_led_ratio_min": 1.0,
        "ignition_lookback": 6,
        "ignition_min_prior_score": 45,
        "ignition_volume_zscore": 2.0,
        "weight_cvd": 30,
        "weight_oi_price": 20,
        "weight_top_position": 15,
        "weight_spot": 20,
        "weight_retail_out": 15,
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
    for key in ("alert_conditions", "thresholds", "storage", "liquidations", "coinalyze_dashboard", "basis", "squeeze", "accumulation"):
        merged[key] = dict(DEFAULT_SETTINGS.get(key, {})) | dict((raw or {}).get(key, {}) or {})

    # Allow env-var override: SCAN_TIMEFRAME=1d or SCAN_TIMEFRAME=4h,1d
    env_tf = os.environ.get("SCAN_TIMEFRAME", "").strip()
    if env_tf:
        requested_timeframes = [part.strip() for part in env_tf.split(",") if part.strip()]
        invalid_timeframes = [tf for tf in requested_timeframes if tf not in VALID_TIMEFRAMES]
        if invalid_timeframes:
            raise ValueError(
                f"SCAN_TIMEFRAME='{env_tf}' is not valid. "
                f"Allowed values: {sorted(VALID_TIMEFRAMES)}"
            )
        merged["timeframes"] = requested_timeframes

    return Settings(
        timeframes=list(merged["timeframes"]),
        alert_conditions=dict(merged["alert_conditions"]),
        thresholds=dict(merged["thresholds"]),
        storage=dict(merged["storage"]),
        liquidations=dict(merged["liquidations"]),
        coinalyze_dashboard=dict(merged["coinalyze_dashboard"]),
        basis=dict(merged["basis"]),
        squeeze=dict(merged["squeeze"]),
        accumulation=dict(merged["accumulation"]),
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
