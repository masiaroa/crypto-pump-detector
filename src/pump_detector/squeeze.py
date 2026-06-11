"""Short-squeeze setup detection.

Crowded shorts leave a trail in free public data well before the squeeze
fires: open interest grows while price stays flat or falls (shorts
building), funding turns negative (shorts paying), volatility compresses,
and the equal/swing highs sitting just above price act as stop-loss
magnets. This module scores that setup 0-100 per candle using only columns
already produced by ``compute_indicators`` — no extra API calls.

The long/short-ratio component is only known for the latest candle (the
scanner fetches it separately), so ``compute_squeeze_columns`` scores the
candle-native components and ``latest_score_with_ls`` folds the L/S
crowding points into the newest row. Scores are always normalised by the
weights of the components that actually had data, so missing inputs
(basis until it ships, L/S for history) never deflate the score.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_SQUEEZE_SETTINGS = {
    "enabled": True,
    "lookback_divergence": 20,
    "oi_build_min_pct": 0.05,
    "price_flat_max_pct": 0.02,
    "funding_low_percentile": 20,
    "ls_long_max": 0.45,
    "stop_cluster_max_distance_pct": 0.10,
    "stop_cluster_tolerance_pct": 0.015,
    "stop_cluster_pivot_strength": 3,
    "stop_cluster_lookback": 60,
    "compression_percentile": 15,
    "setup_score_min": 55,
    "setup_min_oi_points": 15,
    # Component weights — tune (or zero out) the signals you trust less.
    "weight_oi_build": 35,
    "weight_stop_magnet": 15,
    "weight_compression": 10,
    "weight_basis": 15,
    "weight_funding": 15,
    "weight_ls": 10,
}


def _cfg(settings: dict | None) -> dict:
    return {**DEFAULT_SQUEEZE_SETTINGS, **(settings or {})}


def compute_squeeze_columns(df: pd.DataFrame, settings: dict | None = None) -> pd.DataFrame:
    """Add per-candle squeeze-setup features and score to an indicator frame.

    Expects the output of ``compute_indicators`` (needs close/high/low,
    open_interest, oi_change, price_return_pct; funding_rate optional).
    """
    cfg = _cfg(settings)
    out = df.copy()
    if out.empty or "open_interest" not in out.columns:
        return _blank_squeeze_columns(out)

    n = int(cfg["lookback_divergence"])

    oi_add = out["oi_change"].clip(lower=0).fillna(0.0)
    down_candle = (out["price_return_pct"] < 0).fillna(False)
    added_on_down = (oi_add * down_candle).rolling(n, min_periods=5).sum()
    added_total = oi_add.rolling(n, min_periods=5).sum()
    out["oi_added_on_down_share"] = (
        (added_on_down / added_total).replace([np.inf, -np.inf], np.nan).fillna(0.5)
    )

    oi_build = out["open_interest"].pct_change(n).replace([np.inf, -np.inf], np.nan)
    price_change_n = out["close"].pct_change(n).replace([np.inf, -np.inf], np.nan)
    out["oi_price_divergence_flag"] = (
        (oi_build >= float(cfg["oi_build_min_pct"])) & (price_change_n <= float(cfg["price_flat_max_pct"]))
    ).fillna(False)

    bb_mid = out["close"].rolling(20, min_periods=20).mean()
    bb_std = out["close"].rolling(20, min_periods=20).std(ddof=0)
    out["bb_width_pct"] = (4.0 * bb_std / bb_mid).replace([np.inf, -np.inf], np.nan)
    out["bbw_percentile"] = (
        out["bb_width_pct"]
        .rolling(100, min_periods=30)
        .apply(lambda w: float((w <= w[-1]).mean() * 100), raw=True)
        .fillna(50.0)
    )
    out["coiled_spring_flag"] = out["bbw_percentile"] <= float(cfg["compression_percentile"])

    prev_close = out["close"].shift(1)
    true_range = pd.concat(
        [out["high"] - out["low"], (out["high"] - prev_close).abs(), (out["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    out["natr_14"] = (true_range.rolling(14, min_periods=14).mean() / out["close"] * 100).replace(
        [np.inf, -np.inf], np.nan
    ).fillna(0.0)

    if "funding_rate" in out.columns:
        funding_percentile = (
            out["funding_rate"]
            .rolling(270, min_periods=30)
            .apply(lambda w: float((w <= w[-1]).mean() * 100), raw=True)
        )
    else:
        funding_percentile = pd.Series(np.nan, index=out.index)

    levels, distances, strengths = _nearest_stop_cluster(out, cfg)
    out["stop_cluster_level"] = levels
    out["stop_cluster_distance_pct"] = distances
    out["stop_cluster_strength"] = strengths

    return _score_columns(out, oi_build, funding_percentile, cfg)


def latest_score_with_ls(
    candle_score: float,
    oi_points: float,
    long_ratio: float,
    ls_falling: bool,
    settings: dict | None = None,
    funding_available: bool = True,
    basis_available: bool = False,
) -> tuple[float, bool]:
    """Fold the long/short crowding component into a candle-native score.

    ``candle_score`` is the normalised 0-100 score from
    ``compute_squeeze_columns``; the L/S ratio only exists for the latest
    candle so the renormalisation happens here. Returns (score, setup_flag).
    """
    cfg = _cfg(settings)
    flag = _setup_flag(candle_score, oi_points, cfg)
    weight_ls = float(cfg["weight_ls"])
    if long_ratio is None or long_ratio <= 0 or weight_ls <= 0:
        return candle_score, flag
    available = _candle_available_weight(cfg, funding_available, basis_available)
    if available <= 0:
        return candle_score, flag
    points = candle_score / 100.0 * available
    crowding = np.clip((float(cfg["ls_long_max"]) - long_ratio) / 0.15, 0.0, 1.0)
    points_ls = weight_ls * (0.8 * crowding + (0.2 if ls_falling else 0.0))
    score = round(float(100.0 * (points + points_ls) / (available + weight_ls)), 1)
    return score, _setup_flag(score, oi_points, cfg)


def ls_history_falling(points: list[dict], window: int = 6) -> bool:
    """True when the long% of the fetched L/S history is falling (shorts piling in)."""
    values = [p.get("long_pct", 0.0) for p in points[-window:] if p.get("long_pct", 0.0) > 0]
    return len(values) >= 3 and values[-1] < values[0]


def _candle_available_weight(cfg: dict, funding_available: bool, basis_available: bool = False) -> float:
    weight = float(cfg["weight_oi_build"]) + float(cfg["weight_stop_magnet"]) + float(cfg["weight_compression"])
    if funding_available:
        weight += float(cfg["weight_funding"])
    if basis_available:
        weight += float(cfg["weight_basis"])
    return weight


def _setup_flag(score: float, oi_points: float, cfg: dict) -> bool:
    return bool(score >= float(cfg["setup_score_min"]) and oi_points >= float(cfg["setup_min_oi_points"]))


def _score_columns(
    out: pd.DataFrame,
    oi_build: pd.Series,
    funding_percentile: pd.Series,
    cfg: dict,
) -> pd.DataFrame:
    weight_oi = float(cfg["weight_oi_build"])
    weight_stop = float(cfg["weight_stop_magnet"])
    weight_comp = float(cfg["weight_compression"])
    weight_funding = float(cfg["weight_funding"])
    weight_basis = float(cfg["weight_basis"])
    funding_low = float(cfg["funding_low_percentile"])
    cap = float(cfg["stop_cluster_max_distance_pct"])
    comp_pct = float(cfg["compression_percentile"])

    build_factor = np.clip(oi_build.fillna(0.0) / 0.10, 0.0, 1.0)
    crowd_factor = np.clip((out["oi_added_on_down_share"] - 0.5) / 0.35, 0.0, 1.0)
    points_oi = weight_oi * build_factor * crowd_factor * out["oi_price_divergence_flag"].astype(float)

    if "funding_rate" in out.columns:
        funding_known = out["funding_rate"].notna()
        shorts_paying = (out["funding_rate"] <= 0).fillna(False)
        percentile_factor = np.clip((funding_low - funding_percentile.fillna(50.0)) / funding_low, 0.0, 1.0)
        points_funding = weight_funding * shorts_paying.astype(float) * percentile_factor
    else:
        funding_known = pd.Series(False, index=out.index)
        points_funding = pd.Series(0.0, index=out.index)

    if "basis_pct" in out.columns and "basis_zscore" in out.columns:
        basis_known = out["basis_pct"].notna()
        perp_discount = (out["basis_pct"] < 0).fillna(False)
        depth_factor = np.clip(-out["basis_zscore"].fillna(0.0) / 2.0, 0.0, 1.0)
        points_basis = weight_basis * perp_discount.astype(float) * depth_factor
    else:
        basis_known = pd.Series(False, index=out.index)
        points_basis = pd.Series(0.0, index=out.index)

    strength_factor = np.clip(out["stop_cluster_strength"] / 3.0, 0.0, 1.0)
    distance = out["stop_cluster_distance_pct"]
    proximity_factor = np.clip((cap - distance.fillna(cap)) / cap, 0.0, 1.0)
    points_stop = weight_stop * strength_factor * proximity_factor

    points_comp = weight_comp * np.clip((comp_pct - out["bbw_percentile"]) / comp_pct, 0.0, 1.0)

    base_weight = weight_oi + weight_stop + weight_comp
    available = (
        base_weight
        + weight_funding * funding_known.astype(float)
        + weight_basis * basis_known.astype(float)
    )
    total_points = points_oi + points_funding + points_basis + points_stop + points_comp
    score = (100.0 * total_points / available.replace(0, np.nan)).fillna(0.0)

    out["squeeze_oi_points"] = points_oi.round(1)
    out["squeeze_setup_score"] = score.round(1)
    out["squeeze_setup_flag"] = (
        (out["squeeze_setup_score"] >= float(cfg["setup_score_min"]))
        & (out["squeeze_oi_points"] >= float(cfg["setup_min_oi_points"]))
    )
    return out


def _nearest_stop_cluster(out: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Nearest equal-highs cluster above each candle's close.

    Shorts opened below a swing high tend to park stops just above it; a
    band of equal highs concentrates that liquidity. Strength counts the
    pivot touches in the band, boosted by how much OI was added while price
    has stayed below the level (those are the shorts whose stops sit there).
    """
    k = int(cfg["stop_cluster_pivot_strength"])
    lookback = int(cfg["stop_cluster_lookback"])
    tolerance = float(cfg["stop_cluster_tolerance_pct"])
    cap = float(cfg["stop_cluster_max_distance_pct"])

    high = out["high"]
    is_pivot = pd.Series(True, index=out.index)
    for offset in range(1, k + 1):
        is_pivot &= (high > high.shift(offset)) & (high > high.shift(-offset))
    is_pivot = is_pivot.fillna(False)
    pivot_positions = np.flatnonzero(is_pivot.to_numpy())

    close = out["close"].to_numpy(dtype=float)
    high_values = high.to_numpy(dtype=float)
    oi_add = out["oi_change"].clip(lower=0).fillna(0.0).to_numpy(dtype=float)
    oi_level = out["open_interest"].to_numpy(dtype=float)

    levels = np.full(len(out), np.nan)
    distances = np.full(len(out), np.nan)
    strengths = np.zeros(len(out))

    for i in range(len(out)):
        # A pivot at j is only confirmed k candles later — exclude the tail
        # to keep the per-candle history free of lookahead.
        window = pivot_positions[(pivot_positions >= i - lookback) & (pivot_positions <= i - k)]
        if window.size == 0 or not np.isfinite(close[i]) or close[i] <= 0:
            continue
        candidate_levels = high_values[window]
        above = candidate_levels[(candidate_levels > close[i]) & (candidate_levels / close[i] - 1.0 <= cap)]
        if above.size == 0:
            continue
        nearest = above.min()
        in_band = above[(above >= nearest) & (above <= nearest * (1.0 + tolerance))]
        touches = int(in_band.size)
        level = float(in_band.max())

        # OI added while price has stayed below the cluster level.
        start = i
        floor = max(0, i - lookback)
        while start > floor and close[start - 1] < level:
            start -= 1
        oi_added_below = float(oi_add[start : i + 1].sum())
        oi_base = oi_level[i] if np.isfinite(oi_level[i]) and oi_level[i] > 0 else 0.0
        oi_factor = np.clip((oi_added_below / oi_base) / 0.05, 0.0, 1.0) if oi_base else 0.0

        levels[i] = level
        distances[i] = level / close[i] - 1.0
        strengths[i] = touches * (1.0 + oi_factor)

    index = out.index
    return pd.Series(levels, index=index), pd.Series(distances, index=index), pd.Series(strengths, index=index)


def _blank_squeeze_columns(out: pd.DataFrame) -> pd.DataFrame:
    for column, default in (
        ("oi_added_on_down_share", 0.5),
        ("oi_price_divergence_flag", False),
        ("bb_width_pct", np.nan),
        ("bbw_percentile", 50.0),
        ("coiled_spring_flag", False),
        ("natr_14", 0.0),
        ("stop_cluster_level", np.nan),
        ("stop_cluster_distance_pct", np.nan),
        ("stop_cluster_strength", 0.0),
        ("squeeze_oi_points", 0.0),
        ("squeeze_setup_score", 0.0),
        ("squeeze_setup_flag", False),
    ):
        out[column] = default
    return out


__all__ = [
    "DEFAULT_SQUEEZE_SETTINGS",
    "compute_squeeze_columns",
    "latest_score_with_ls",
    "ls_history_falling",
]
