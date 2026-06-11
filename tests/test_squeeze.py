import numpy as np
import pandas as pd

from pump_detector.signals import compute_indicators
from pump_detector.squeeze import (
    compute_squeeze_columns,
    latest_score_with_ls,
    ls_history_falling,
    short_liq_zscore,
    squeeze_ignition,
    taker_ratio_zscore,
)


def _frame(shorts_building: bool) -> pd.DataFrame:
    """120 noisy candles, then 30 where OI climbs while price drifts.

    With ``shorts_building`` the drift is down (red candles → shorts piling
    in); otherwise the same OI climb happens on green candles (longs).
    """
    rows = []
    price = 10.0
    oi = 1_000_000.0
    funding = 0.0003
    rng = np.random.default_rng(7)
    for i in range(120):
        swing = 1.0 + 0.03 * np.sin(i / 3.0) + rng.normal(0, 0.004)
        open_ = price * swing
        close = open_ * (1.0 + rng.normal(0, 0.006))
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=4 * i),
                "open": open_,
                "high": max(open_, close) * 1.012,
                "low": min(open_, close) * 0.988,
                "close": close,
                "volume": 100_000.0,
                "open_interest": oi,
                "funding_rate": funding,
            }
        )
    last_close = rows[-1]["close"]
    drift = 0.9985 if shorts_building else 1.004
    for i in range(120, 150):
        open_ = last_close
        close = open_ * drift
        funding = max(funding - 0.00002, -0.0002) if shorts_building else funding
        oi *= 1.005
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=4 * i),
                "open": open_,
                "high": max(open_, close) * 1.002,
                "low": min(open_, close) * 0.998,
                "close": close,
                "volume": 80_000.0,
                "open_interest": oi,
                "funding_rate": funding,
            }
        )
        last_close = close
    return pd.DataFrame(rows)


def test_squeeze_score_high_when_oi_builds_on_red_candles():
    df = compute_indicators(_frame(shorts_building=True), lookback_stats=100)
    out = compute_squeeze_columns(df)
    latest = out.iloc[-1]

    assert latest["oi_added_on_down_share"] > 0.8
    assert bool(latest["oi_price_divergence_flag"]) is True
    assert latest["squeeze_oi_points"] >= 15
    assert latest["squeeze_setup_score"] >= 55
    assert bool(latest["squeeze_setup_flag"]) is True


def test_squeeze_score_low_when_oi_builds_on_green_candles():
    df = compute_indicators(_frame(shorts_building=False), lookback_stats=100)
    out = compute_squeeze_columns(df)
    latest = out.iloc[-1]

    assert bool(latest["oi_price_divergence_flag"]) is False
    assert latest["squeeze_oi_points"] == 0
    assert bool(latest["squeeze_setup_flag"]) is False


def test_squeeze_detects_stop_cluster_above_price():
    df = compute_indicators(_frame(shorts_building=True), lookback_stats=100)
    out = compute_squeeze_columns(df)
    latest = out.iloc[-1]

    assert latest["stop_cluster_level"] > latest["close"]
    assert 0 < latest["stop_cluster_distance_pct"] <= 0.10
    assert latest["stop_cluster_strength"] > 0


def test_compression_flag_after_volatility_contraction():
    df = compute_indicators(_frame(shorts_building=True), lookback_stats=100)
    out = compute_squeeze_columns(df)
    latest = out.iloc[-1]

    assert latest["bbw_percentile"] <= 15
    assert bool(latest["coiled_spring_flag"]) is True


def test_latest_score_with_ls_adds_crowding_points():
    base_score, oi_points = 60.0, 20.0
    boosted, boosted_flag = latest_score_with_ls(base_score, oi_points, long_ratio=0.30, ls_falling=True)
    neutral, _ = latest_score_with_ls(base_score, oi_points, long_ratio=0.60, ls_falling=False)
    missing, missing_flag = latest_score_with_ls(base_score, oi_points, long_ratio=0.0, ls_falling=False)

    assert boosted > neutral
    assert boosted_flag is True
    # No L/S data → score passes through untouched.
    assert missing == base_score
    assert missing_flag is True


def test_ls_history_falling():
    falling = [{"long_pct": 0.55}, {"long_pct": 0.50}, {"long_pct": 0.44}]
    rising = [{"long_pct": 0.40}, {"long_pct": 0.45}, {"long_pct": 0.50}]
    assert ls_history_falling(falling) is True
    assert ls_history_falling(rising) is False
    assert ls_history_falling([]) is False


def test_squeeze_ignition_fires_on_short_liq_spike_after_setup():
    history = pd.Series([10.0] * 20 + [50.0, 58.0, 47.0, 20.0, 12.0, 8.0])
    latest = pd.Series(
        {"price_return_pct": 0.035, "close_near_high": True, "price_return_zscore": 2.0, "oi_change_pct": 0.01}
    )
    assert squeeze_ignition(history, latest, short_liq_z=3.0, taker_z=0.0) is True
    # No confirming trigger → no ignition
    assert squeeze_ignition(history, latest, short_liq_z=0.5, taker_z=0.5) is False
    # Short covering (price up, OI down) is a valid trigger on its own
    covering = latest.copy()
    covering["oi_change_pct"] = -0.03
    assert squeeze_ignition(history, covering, short_liq_z=0.0, taker_z=0.0) is True
    # Without a prior setup nothing fires
    flat = pd.Series([10.0] * 26)
    assert squeeze_ignition(flat, latest, short_liq_z=3.0, taker_z=3.0) is False


def test_short_liq_zscore_detects_spike_on_latest_candle():
    ts = pd.Series(pd.date_range("2026-01-01", periods=60, freq="4h", tz="UTC"))
    rows = [
        {"timestamp": ts.iloc[i], "side": "short", "notional": 10_000.0 + (i % 7) * 1_000}
        for i in range(59)
    ]
    rows.append({"timestamp": ts.iloc[59], "side": "short", "notional": 500_000.0})
    frame = pd.DataFrame(rows)
    assert short_liq_zscore(frame, ts) >= 2.0
    assert short_liq_zscore(pd.DataFrame(), ts) == 0.0
    assert short_liq_zscore(None, ts) == 0.0


def test_taker_ratio_zscore():
    quiet = [{"timestamp_ms": i, "buy_ratio": 0.50 + (i % 5) * 0.01} for i in range(40)]
    spike = quiet + [{"timestamp_ms": 41, "buy_ratio": 0.72}]
    assert taker_ratio_zscore(spike) >= 2.0
    assert taker_ratio_zscore(quiet[:5]) == 0.0


def test_blank_columns_when_no_open_interest():
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=5, freq="4h", tz="UTC"),
            "close": [1.0, 1.1, 1.2, 1.1, 1.0],
        }
    )
    out = compute_squeeze_columns(df)
    assert (out["squeeze_setup_score"] == 0.0).all()
    assert not out["squeeze_setup_flag"].any()
