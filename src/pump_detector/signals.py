from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SignalSnapshot:
    symbol: str
    exchange: str
    timeframe: str
    timestamp: pd.Timestamp
    close: float
    price_return_pct: float
    price_return_zscore: float
    oi: float
    oi_change_pct: float
    oi_change_zscore: float
    volume_zscore: float
    funding_rate: float
    funding_classification: str
    funding_percentile_90d: float
    sma200: float
    distance_to_sma200_pct: float
    breakout_10_flag: bool
    breakout_20_flag: bool
    sma200_reclaim_flag: bool
    close_near_high: bool
    price_impulse: bool
    oi_impulse: bool
    first_impulse: bool
    early_bullish_score: float
    blowoff_risk_score: float
    signal_active: bool
    alert_triggered: bool
    last_signal_time: str
    notes: str
    forward_return_1_candle: float | None = None
    forward_return_3_candles: float | None = None
    forward_return_5_candles: float | None = None
    max_favorable_excursion: float | None = None
    max_adverse_excursion: float | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data


def classify_funding(rate: float | None, recent: pd.Series | None = None, hot_threshold: float = 0.0005) -> str:
    if rate is None or pd.isna(rate):
        return "UNKNOWN"
    if recent is not None and len(recent.dropna()) >= 20 and recent.dropna().nunique() >= 5:
        percentile_95 = np.nanpercentile(recent.dropna(), 95)
        if rate >= percentile_95:
            return "EXTREME"
    if rate < 0:
        return "NEGATIVE"
    if rate <= 0.0001:
        return "NEUTRAL"
    if rate <= hot_threshold:
        return "POSITIVE"
    return "HOT"


def compute_indicators(df: pd.DataFrame, lookback_stats: int = 100) -> pd.DataFrame:
    out = df.copy().sort_values("timestamp").reset_index(drop=True)
    out["price_return_pct"] = out["close"].pct_change()
    out["oi_change"] = out["open_interest"].diff()
    out["oi_change_pct"] = out["open_interest"].pct_change()
    if not {"oi_open", "oi_high", "oi_low", "oi_close"}.issubset(out.columns):
        out["oi_open"] = out["open_interest"].shift(1).fillna(out["open_interest"])
        out["oi_close"] = out["open_interest"]
        out["oi_high"] = out[["oi_open", "oi_close"]].max(axis=1)
        out["oi_low"] = out[["oi_open", "oi_close"]].min(axis=1)
    else:
        out["oi_open"] = out["oi_open"].fillna(out["open_interest"].shift(1)).fillna(out["open_interest"])
        out["oi_close"] = out["oi_close"].fillna(out["open_interest"])
        out["oi_high"] = out["oi_high"].fillna(out[["oi_open", "oi_close"]].max(axis=1))
        out["oi_low"] = out["oi_low"].fillna(out[["oi_open", "oi_close"]].min(axis=1))
        out["open_interest"] = out["oi_close"].fillna(out["open_interest"])
        out["oi_change"] = out["open_interest"].diff()
        out["oi_change_pct"] = out["open_interest"].pct_change()
    out["sma200"] = out["close"].rolling(200, min_periods=20).mean()
    out["distance_to_sma200_pct"] = (out["close"] / out["sma200"] - 1.0).replace([np.inf, -np.inf], np.nan)
    out["close_position"] = ((out["close"] - out["low"]) / (out["high"] - out["low"])).replace([np.inf, -np.inf], np.nan)
    out["breakout_10_flag"] = out["close"] > out["high"].shift(1).rolling(10, min_periods=5).max()
    out["breakout_20_flag"] = out["close"] > out["high"].shift(1).rolling(20, min_periods=10).max()
    out["sma200_reclaim_flag"] = (out["close"] > out["sma200"]) & (out["close"].shift(1) <= out["sma200"].shift(1))
    out["price_return_zscore"] = _rolling_zscore(out["price_return_pct"], lookback_stats)
    out["oi_change_zscore"] = _rolling_zscore(out["oi_change_pct"], lookback_stats)
    out["volume_zscore"] = _rolling_zscore(out["volume"], lookback_stats)
    volume_median = out["volume"].shift(1).rolling(lookback_stats, min_periods=max(10, lookback_stats // 5)).median()
    out["volume_ratio"] = (out["volume"] / volume_median).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out["recent_price_run_pct"] = out["close"].pct_change(10)
    out["oi_expanding"] = out["oi_change"] > 0
    oi_median = out["oi_change_pct"].shift(1).rolling(lookback_stats, min_periods=max(10, lookback_stats // 5)).median()
    out["oi_strong_expansion"] = (out["oi_change_zscore"] > 1.0) & (out["oi_change_pct"] > oi_median)
    return out


def mark_signal_history(
    df: pd.DataFrame,
    timeframe: str = "4h",
    lookback_no_previous_signal: int = 10,
    price_zscore_threshold: float = 2.5,
    oi_zscore_threshold: float = 2.5,
    close_position_min: float = 0.65,
    max_recent_price_run_pct: float = 0.45,
    max_consecutive_oi_expansion: int = 3,
) -> pd.DataFrame:
    """Mark historical signals using only data available at each candle close."""
    out = df.copy().sort_values("timestamp").reset_index(drop=True)
    price_threshold = out["price_return_pct"].shift(1).rolling(100, min_periods=20).quantile(0.9)
    oi_threshold = out["oi_change_pct"].shift(1).rolling(100, min_periods=20).quantile(0.9)
    out["price_impulse_flag"] = (
        (out["close"] > out["open"])
        & (out["close_position"] >= close_position_min)
        & ((out["price_return_zscore"] >= price_zscore_threshold) | (out["price_return_pct"] >= price_threshold))
    ).fillna(False)
    out["oi_impulse_flag"] = (
        (out["oi_change"] > 0)
        & ((out["oi_change_zscore"] >= oi_zscore_threshold) | (out["oi_change_pct"] >= oi_threshold))
    ).fillna(False)
    strong_oi_impulse = out["oi_impulse_flag"] & ((out["oi_change_zscore"] >= 0.5) | (out["oi_change_pct"] >= 0.02))
    out["recent_oi_impulse_flag"] = strong_oi_impulse.shift(1).rolling(3, min_periods=1).max().fillna(False).astype(bool)
    previous_impulse = (out["price_impulse_flag"] & out["oi_impulse_flag"]).shift(1)
    previous_like_count = previous_impulse.rolling(lookback_no_previous_signal, min_periods=1).sum()
    recent_run_ok = out["recent_price_run_pct"].isna() | (out["recent_price_run_pct"] <= max_recent_price_run_pct)
    prior_consecutive_oi = _prior_trailing_true_counts(out["oi_strong_expansion"])
    out["first_impulse_flag"] = (
        (previous_like_count.fillna(0) == 0)
        & recent_run_ok
        & (prior_consecutive_oi <= max_consecutive_oi_expansion)
    )
    out["funding_classification"] = [
        classify_funding(row.funding_rate, out["funding_rate"].iloc[max(0, idx - 270) : idx])
        for idx, row in out.iterrows()
    ]
    out["early_bullish_score"] = [
        round(_early_score(row, row["funding_classification"], bool(row["first_impulse_flag"])), 1)
        for _, row in out.iterrows()
    ]
    out["blowoff_risk_score"] = [
        round(_risk_score(row, row["funding_classification"]), 1)
        for _, row in out.iterrows()
    ]
    explosive_confirmation = (
        out["price_impulse_flag"]
        & (out["oi_impulse_flag"] | out["recent_oi_impulse_flag"])
        & out["breakout_20_flag"]
        & (out["price_return_zscore"] >= price_zscore_threshold)
        & ((out["oi_change_zscore"] >= 1.0) | (out["oi_change_pct"] >= 0.05) | out["recent_oi_impulse_flag"])
    )
    entry_price_quality = (
        (timeframe != "1d")
        | out["sma200_reclaim_flag"]
        | (out["price_return_pct"] >= 0.08)
        | (out["breakout_20_flag"] & (out["volume_zscore"] >= 2.0) & (out["price_return_pct"] >= 0.05))
    )
    out["signal_active_flag"] = (
        out["price_impulse_flag"]
        & (out["oi_impulse_flag"] | out["recent_oi_impulse_flag"])
        & (out["first_impulse_flag"] | explosive_confirmation)
        & entry_price_quality
    )
    funding_ok = out["funding_classification"].isin(["NEGATIVE", "NEUTRAL", "POSITIVE"])
    volume_acceleration = (out["volume_zscore"] >= 4.0) | (out["volume_ratio"] >= 3.0)
    price_lead_alert = (
        out["price_impulse_flag"]
        & (funding_ok | volume_acceleration)
        & (out["oi_change_zscore"] > -1.0)
        & (
            out["breakout_20_flag"]
            | (out["volume_zscore"] >= 2.0)
            | (out["oi_change_zscore"] >= 1.0)
        )
        & ~out["signal_active_flag"]
    )
    price_not_bearish = (out["close"] >= out["open"]) & (out["price_return_pct"] >= 0)
    oi_lead_alert = out["oi_impulse_flag"] & ~out["price_impulse_flag"] & out["first_impulse_flag"] & price_not_bearish
    out["pre_alert_flag"] = price_lead_alert | oi_lead_alert
    out["hot_pre_entry_flag"] = out["pre_alert_flag"] & ((out["volume_zscore"] >= 2.0) | (out["volume_ratio"] >= 2.5))
    return out


def evaluate_latest(
    df: pd.DataFrame,
    timeframe: str,
    symbol: str,
    exchange: str,
    lookback_no_previous_signal: int = 10,
    price_zscore_threshold: float = 2.5,
    oi_zscore_threshold: float = 2.5,
    close_position_min: float = 0.65,
    volume_zscore_threshold: float = 1.5,
    max_recent_price_run_pct: float = 0.45,
    max_consecutive_oi_expansion: int = 3,
    allowed_funding_classes: list[str] | None = None,
    require_volume_confirmation: bool = False,
    require_breakout_20: bool = False,
    require_sma200_reclaim: bool = False,
    notes: str = "",
) -> SignalSnapshot:
    allowed_funding_classes = allowed_funding_classes or ["NEGATIVE", "NEUTRAL", "POSITIVE", "HOT"]
    latest = df.iloc[-1]
    history = df.iloc[:-1]
    close_near_high = bool(latest["close_position"] >= close_position_min)
    price_impulse = bool(
        latest["close"] > latest["open"]
        and close_near_high
        and (
            latest["price_return_zscore"] >= price_zscore_threshold
            or latest["price_return_pct"] >= history["price_return_pct"].quantile(0.9)
        )
    )
    oi_impulse = bool(
        latest["oi_change"] > 0
        and (
            latest["oi_change_zscore"] >= oi_zscore_threshold
            or latest["oi_change_pct"] >= history["oi_change_pct"].quantile(0.9)
        )
    )
    previous_like = _previous_like_signal(
        df.iloc[-(lookback_no_previous_signal + 1) : -1],
        price_zscore_threshold,
        oi_zscore_threshold,
        close_position_min,
    )
    recent_run_ok = bool(pd.isna(latest["recent_price_run_pct"]) or latest["recent_price_run_pct"] <= max_recent_price_run_pct)
    consecutive_oi = _trailing_true_count(df["oi_strong_expansion"].iloc[:-1])
    first_impulse = not previous_like and recent_run_ok and consecutive_oi <= max_consecutive_oi_expansion

    funding_recent = df["funding_rate"].tail(270) if "funding_rate" in df else None
    funding_class = classify_funding(latest.get("funding_rate"), funding_recent)
    funding_percentile = _percentile_rank(funding_recent, latest.get("funding_rate"))
    volume_ok = bool(latest["volume_zscore"] >= volume_zscore_threshold)
    optional_ok = (
        (not require_volume_confirmation or volume_ok)
        and (not require_breakout_20 or bool(latest["breakout_20_flag"]))
        and (not require_sma200_reclaim or bool(latest["sma200_reclaim_flag"]))
        and funding_class in allowed_funding_classes
    )

    early_score = _early_score(latest, funding_class, first_impulse)
    risk_score = _risk_score(latest, funding_class)
    signal_active = bool(price_impulse and oi_impulse and first_impulse)
    alert_triggered = bool(signal_active and optional_ok)
    last_signal_time = latest["timestamp"].isoformat() if signal_active else ""
    reasons = _notes(price_impulse, oi_impulse, first_impulse, funding_class, latest, notes)

    return SignalSnapshot(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        timestamp=latest["timestamp"],
        close=float(latest["close"]),
        price_return_pct=_float(latest["price_return_pct"]),
        price_return_zscore=_float(latest["price_return_zscore"]),
        oi=_float(latest["open_interest"]),
        oi_change_pct=_float(latest["oi_change_pct"]),
        oi_change_zscore=_float(latest["oi_change_zscore"]),
        volume_zscore=_float(latest["volume_zscore"]),
        funding_rate=_float(latest.get("funding_rate")),
        funding_classification=funding_class,
        funding_percentile_90d=funding_percentile,
        sma200=_float(latest["sma200"]),
        distance_to_sma200_pct=_float(latest["distance_to_sma200_pct"]),
        breakout_10_flag=bool(latest["breakout_10_flag"]),
        breakout_20_flag=bool(latest["breakout_20_flag"]),
        sma200_reclaim_flag=bool(latest["sma200_reclaim_flag"]),
        close_near_high=close_near_high,
        price_impulse=price_impulse,
        oi_impulse=oi_impulse,
        first_impulse=first_impulse,
        early_bullish_score=round(early_score, 1),
        blowoff_risk_score=round(risk_score, 1),
        signal_active=signal_active,
        alert_triggered=alert_triggered,
        last_signal_time=last_signal_time,
        notes=reasons,
    )


def _rolling_zscore(series: pd.Series, lookback: int) -> pd.Series:
    shifted = series.shift(1)
    mean = shifted.rolling(lookback, min_periods=max(10, lookback // 5)).mean()
    std = shifted.rolling(lookback, min_periods=max(10, lookback // 5)).std(ddof=0)
    return ((series - mean) / std).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _previous_like_signal(df: pd.DataFrame, price_z: float, oi_z: float, close_position_min: float) -> bool:
    if df.empty:
        return False
    return bool(
        (
            (df["close"] > df["open"])
            & (df["close_position"] >= close_position_min)
            & (df["price_return_zscore"] >= price_z)
            & (df["oi_change_zscore"] >= oi_z)
        ).any()
    )


def _trailing_true_count(series: pd.Series) -> int:
    count = 0
    for value in reversed(series.fillna(False).tolist()):
        if not value:
            break
        count += 1
    return count


def _prior_trailing_true_counts(series: pd.Series) -> pd.Series:
    counts: list[int] = []
    running = 0
    for value in series.fillna(False).tolist():
        counts.append(running)
        running = running + 1 if value else 0
    return pd.Series(counts, index=series.index)


def _early_score(row: pd.Series, funding_class: str, first_impulse: bool) -> float:
    score = min(max(row["price_return_zscore"], 0), 5) * 16
    score += min(max(row["oi_change_zscore"], 0), 5) * 16
    score += min(max(row["volume_zscore"], 0), 4) * 6
    score += 10 if funding_class in {"NEGATIVE", "NEUTRAL"} else 2 if funding_class == "POSITIVE" else -8
    score += 8 if row["breakout_20_flag"] else 0
    score += 6 if row["sma200_reclaim_flag"] else 0
    score += 10 if first_impulse else -20
    return float(np.clip(score, 0, 100))


def _risk_score(row: pd.Series, funding_class: str) -> float:
    score = min(max(row["price_return_zscore"], 0), 6) * 7
    score += min(max(row["oi_change_zscore"], 0), 6) * 7
    score += min(max(row["volume_zscore"], 0), 5) * 4
    score += {"NEGATIVE": 0, "NEUTRAL": 4, "POSITIVE": 12, "HOT": 24, "EXTREME": 35}.get(funding_class, 8)
    distance = row.get("distance_to_sma200_pct")
    if pd.notna(distance) and distance > 0:
        score += min(distance * 100, 25)
    return float(np.clip(score, 0, 100))


def _percentile_rank(series: pd.Series | None, value: float) -> float:
    if series is None or value is None or pd.isna(value):
        return 0.0
    clean = series.dropna()
    if clean.empty:
        return 0.0
    return round(float((clean <= value).mean() * 100), 2)


def _notes(price_impulse: bool, oi_impulse: bool, first_impulse: bool, funding_class: str, row: pd.Series, extra: str) -> str:
    parts = []
    parts.append("price impulse" if price_impulse else "no price impulse")
    parts.append("OI impulse" if oi_impulse else "no OI impulse")
    parts.append("first impulse" if first_impulse else "late impulse risk")
    parts.append(f"funding {funding_class}")
    if row["breakout_20_flag"]:
        parts.append("breakout 20")
    if row["sma200_reclaim_flag"]:
        parts.append("SMA200 reclaim")
    if extra:
        parts.append(extra)
    return "; ".join(parts)


def _float(value: object) -> float:
    if value is None or pd.isna(value):
        return 0.0
    return float(value)
