"""Whale-accumulation detection (strong hands in before retail).

The second pump archetype: large players absorb supply quietly — the
cumulative volume delta (CVD, from per-candle taker-buy volume) climbs
while price stays flat, open interest builds without a dump, top-trader
*position* ratio (size-weighted, a whale proxy) rises while the retail
account ratio is still short — then retail piles in and the move goes
vertical.

Mirrors squeeze.py's split: ``compute_accumulation_columns`` scores the
candle-native components (CVD, OI) per candle; ``latest_whale_score``
folds positioning and spot-leadership components into the newest row.
Scores normalise by the weights of components that had data, so Bybit-fed
symbols without taker-buy volume still get a meaningful (OI-only) score.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_ACCUMULATION_SETTINGS = {
    "enabled": True,
    "lookback": 20,
    "cvd_full_share": 0.12,        # net taker-buy imbalance (share of volume) for max points
    "price_quiet_max_pct": 0.05,   # accumulation should not have run more than this already
    "oi_drop_max_pct": -0.02,      # OI component voided if price dropped more than this
    "accum_score_min": 55,
    "accum_min_flow_points": 15,   # CVD+OI candle points required for the flag
    "retail_long_max": 0.55,       # retail (global) long ratio below this = crowd not in yet
    "spot_full_ratio": 2.0,        # spot/perp volume ratio for max spot-led points
    "spot_led_ratio_min": 1.0,
    "ignition_lookback": 6,
    "ignition_min_prior_score": 45,
    "ignition_volume_zscore": 2.0,
    "weight_cvd": 30,
    "weight_oi_price": 20,
    "weight_top_position": 15,
    "weight_spot": 20,
    "weight_retail_out": 15,
}


def _cfg(settings: dict | None) -> dict:
    return {**DEFAULT_ACCUMULATION_SETTINGS, **(settings or {})}


def compute_accumulation_columns(df: pd.DataFrame, settings: dict | None = None) -> pd.DataFrame:
    """Add per-candle whale-accumulation features and score to an indicator frame."""
    cfg = _cfg(settings)
    out = df.copy()
    if out.empty or "close" not in out.columns:
        return _blank_columns(out)

    n = int(cfg["lookback"])
    weight_cvd = float(cfg["weight_cvd"])
    weight_oi = float(cfg["weight_oi_price"])

    price_change_n = out["close"].pct_change(n).replace([np.inf, -np.inf], np.nan)

    if "taker_buy_volume" in out.columns and out["taker_buy_volume"].notna().any():
        taker = out["taker_buy_volume"]
        volume = out["volume"].replace(0, np.nan)
        out["taker_buy_share"] = (taker / volume).clip(0.0, 1.0)
        cvd_delta = 2.0 * taker - out["volume"]
        out["cvd_slope"] = (
            cvd_delta.rolling(n, min_periods=5).sum() / out["volume"].rolling(n, min_periods=5).sum()
        ).replace([np.inf, -np.inf], np.nan)
        cvd_known = out["cvd_slope"].notna()
    else:
        out["taker_buy_share"] = np.nan
        out["cvd_slope"] = np.nan
        cvd_known = pd.Series(False, index=out.index)

    # Buying pressure absorbed without a run-up = accumulation; once price has
    # already escaped, the same CVD reads as chase, not absorption.
    quiet_max = float(cfg["price_quiet_max_pct"])
    quiet_factor = np.clip((quiet_max - price_change_n.clip(lower=0).fillna(0.0)) / quiet_max, 0.0, 1.0)
    cvd_factor = np.clip(out["cvd_slope"].fillna(0.0) / float(cfg["cvd_full_share"]), 0.0, 1.0)
    points_cvd = weight_cvd * cvd_factor * quiet_factor

    if "open_interest" in out.columns:
        oi_build = out["open_interest"].pct_change(n).replace([np.inf, -np.inf], np.nan)
        price_holding = (price_change_n >= float(cfg["oi_drop_max_pct"])).fillna(False)
        points_oi = weight_oi * np.clip(oi_build.fillna(0.0) / 0.10, 0.0, 1.0) * price_holding.astype(float)
    else:
        points_oi = pd.Series(0.0, index=out.index)

    available = weight_oi + weight_cvd * cvd_known.astype(float)
    flow_points = points_cvd + points_oi
    score = (100.0 * flow_points / available).fillna(0.0)

    out["whale_flow_points"] = flow_points.round(1)
    out["whale_accum_score"] = score.round(1)
    out["whale_accum_flag"] = (
        (out["whale_accum_score"] >= float(cfg["accum_score_min"]))
        & (out["whale_flow_points"] >= float(cfg["accum_min_flow_points"]))
    )
    return out


def latest_whale_score(
    candle_score: float,
    flow_points: float,
    *,
    top_position_long: float,
    top_position_rising: bool,
    global_long_ratio: float,
    spot_perp_vol_ratio: float,
    settings: dict | None = None,
    cvd_available: bool = False,
) -> tuple[float, bool]:
    """Fold positioning + spot-leadership components into a candle-native score."""
    cfg = _cfg(settings)
    weight_cvd = float(cfg["weight_cvd"])
    weight_oi = float(cfg["weight_oi_price"])
    available = weight_oi + (weight_cvd if cvd_available else 0.0)
    points = candle_score / 100.0 * available

    weight_top = float(cfg["weight_top_position"])
    if top_position_long > 0:
        level = np.clip((top_position_long - 0.5) / 0.2, 0.0, 1.0)
        points += weight_top * (0.6 * (1.0 if top_position_rising else 0.0) + 0.4 * level)
        available += weight_top

    weight_spot = float(cfg["weight_spot"])
    if spot_perp_vol_ratio > 0:
        points += weight_spot * np.clip((spot_perp_vol_ratio - 0.5) / (float(cfg["spot_full_ratio"]) - 0.5), 0.0, 1.0)
        available += weight_spot

    weight_retail = float(cfg["weight_retail_out"])
    if global_long_ratio > 0:
        points += weight_retail * np.clip((float(cfg["retail_long_max"]) - global_long_ratio) / 0.15, 0.0, 1.0)
        available += weight_retail

    if available <= 0:
        return candle_score, False
    score = round(float(100.0 * points / available), 1)
    flag = bool(
        score >= float(cfg["accum_score_min"]) and flow_points >= float(cfg["accum_min_flow_points"])
    )
    return score, flag


def whale_pump_ignition(
    history_scores: pd.Series,
    latest_row: pd.Series,
    retail_history: list[dict],
    settings: dict | None = None,
) -> bool:
    """Retail-FOMO ignition: an accumulation phase that just went vertical.

    Requires a recent accumulation read (prior candles), a strong green
    candle with a volume surge, and the retail long ratio turning up —
    the crowd arriving is what makes the move go vertical.
    """
    cfg = _cfg(settings)
    lookback = int(cfg["ignition_lookback"])
    prior = history_scores.iloc[-(lookback + 1) : -1] if len(history_scores) > 1 else history_scores
    if prior.empty or prior.max() < float(cfg["ignition_min_prior_score"]):
        return False
    green = float(latest_row.get("price_return_pct") or 0.0) > 0 and bool(latest_row.get("close_near_high"))
    volume_surge = float(latest_row.get("volume_zscore") or 0.0) >= float(cfg["ignition_volume_zscore"])
    return bool(green and volume_surge and retail_turning_up(retail_history))


def retail_turning_up(points: list[dict], window: int = 6) -> bool:
    """True when the retail (global account) long% just turned upward."""
    values = [p.get("long_pct", 0.0) for p in points[-window:] if p.get("long_pct", 0.0) > 0]
    return len(values) >= 3 and values[-1] > min(values)


def ratio_history_rising(points: list[dict], window: int = 6) -> bool:
    values = [p.get("long_pct", 0.0) for p in points[-window:] if p.get("long_pct", 0.0) > 0]
    return len(values) >= 3 and values[-1] > values[0]


def spot_perp_volume_ratio(spot_volumes: pd.Series | None, perp_volumes: pd.Series, window: int = 30) -> float:
    """Spot volume / perp volume over the recent window (same base unit).

    Spot-led activity is real buying rather than leverage; a rising share
    of spot volume makes a move more sustainable.
    """
    if spot_volumes is None or spot_volumes.empty:
        return 0.0
    perp = pd.to_numeric(perp_volumes, errors="coerce").tail(window).sum()
    spot = pd.to_numeric(spot_volumes, errors="coerce").tail(window).sum()
    if perp <= 0 or spot <= 0:
        return 0.0
    return round(float(spot / perp), 3)


def _blank_columns(out: pd.DataFrame) -> pd.DataFrame:
    for column, default in (
        ("taker_buy_share", np.nan),
        ("cvd_slope", np.nan),
        ("whale_flow_points", 0.0),
        ("whale_accum_score", 0.0),
        ("whale_accum_flag", False),
    ):
        out[column] = default
    return out


__all__ = [
    "DEFAULT_ACCUMULATION_SETTINGS",
    "compute_accumulation_columns",
    "latest_whale_score",
    "whale_pump_ignition",
    "retail_turning_up",
    "ratio_history_rising",
    "spot_perp_volume_ratio",
]
