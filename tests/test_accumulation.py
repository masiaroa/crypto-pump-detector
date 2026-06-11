import pandas as pd

from pump_detector.accumulation import (
    compute_accumulation_columns,
    latest_whale_score,
    ratio_history_rising,
    retail_turning_up,
    spot_perp_volume_ratio,
    whale_pump_ignition,
)
from pump_detector.signals import compute_indicators


def _frame(taker_share: float, with_taker: bool = True, oi_growth: float = 1.004) -> pd.DataFrame:
    """120 flat candles then 30 candles of quiet absorption."""
    rows = []
    price, oi = 10.0, 1_000_000.0
    for i in range(150):
        absorbing = i >= 120
        volume = 100_000.0
        share = taker_share if absorbing else 0.5
        if absorbing:
            oi *= oi_growth
        row = {
            "timestamp": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=4 * i),
            "open": price,
            "high": price * 1.006,
            "low": price * 0.994,
            "close": price * (1.0006 if absorbing else 1.0),
            "volume": volume,
            "open_interest": oi,
            "funding_rate": 0.0001,
        }
        if with_taker:
            row["taker_buy_volume"] = volume * share
        rows.append(row)
        price = row["close"]
    return pd.DataFrame(rows)


def test_whale_score_high_when_cvd_climbs_with_flat_price():
    df = compute_indicators(_frame(taker_share=0.62), lookback_stats=100)
    out = compute_accumulation_columns(df)
    latest = out.iloc[-1]

    assert latest["taker_buy_share"] > 0.55
    assert latest["cvd_slope"] > 0.15
    assert latest["whale_accum_score"] >= 55
    assert bool(latest["whale_accum_flag"]) is True


def test_whale_score_low_with_balanced_flow():
    df = compute_indicators(_frame(taker_share=0.50, oi_growth=1.0), lookback_stats=100)
    out = compute_accumulation_columns(df)
    latest = out.iloc[-1]

    assert abs(latest["cvd_slope"]) < 0.05
    assert latest["whale_accum_score"] < 30
    assert bool(latest["whale_accum_flag"]) is False


def test_whale_score_renormalises_without_taker_data():
    df = compute_indicators(_frame(taker_share=0.62, with_taker=False, oi_growth=1.006), lookback_stats=100)
    out = compute_accumulation_columns(df)
    latest = out.iloc[-1]

    assert pd.isna(latest["cvd_slope"])
    # OI-only score: +12% OI build over 20 candles with price holding maxes the component.
    assert latest["whale_accum_score"] >= 55


def test_latest_whale_score_folds_positioning_and_spot():
    boosted, flag = latest_whale_score(
        60.0,
        20.0,
        top_position_long=0.65,
        top_position_rising=True,
        global_long_ratio=0.40,
        spot_perp_vol_ratio=1.8,
        cvd_available=True,
    )
    bare, _ = latest_whale_score(
        60.0,
        20.0,
        top_position_long=0.0,
        top_position_rising=False,
        global_long_ratio=0.0,
        spot_perp_vol_ratio=0.0,
        cvd_available=True,
    )
    assert boosted > bare
    assert bare == 60.0  # nothing available → candle score passes through
    assert flag is True


def test_whale_pump_ignition_requires_prior_accum_and_retail_turn():
    history = pd.Series([10.0] * 20 + [55.0, 60.0, 58.0, 20.0, 15.0, 10.0])
    latest = pd.Series({"price_return_pct": 0.04, "close_near_high": True, "volume_zscore": 3.0})
    retail_up = [{"long_pct": 0.42}, {"long_pct": 0.40}, {"long_pct": 0.46}]
    retail_down = [{"long_pct": 0.50}, {"long_pct": 0.46}, {"long_pct": 0.42}]

    assert whale_pump_ignition(history, latest, retail_up) is True
    assert whale_pump_ignition(history, latest, retail_down) is False
    no_accum = pd.Series([10.0] * 26)
    assert whale_pump_ignition(no_accum, latest, retail_up) is False
    quiet = pd.Series({"price_return_pct": 0.001, "close_near_high": False, "volume_zscore": 0.5})
    assert whale_pump_ignition(history, quiet, retail_up) is False


def test_ratio_helpers():
    rising = [{"long_pct": 0.50}, {"long_pct": 0.55}, {"long_pct": 0.60}]
    falling = [{"long_pct": 0.60}, {"long_pct": 0.55}, {"long_pct": 0.50}]
    assert ratio_history_rising(rising) is True
    assert ratio_history_rising(falling) is False
    assert retail_turning_up(falling + [{"long_pct": 0.53}]) is True
    assert retail_turning_up([]) is False


def test_spot_perp_volume_ratio():
    perp = pd.Series([100.0] * 30)
    spot = pd.Series([150.0] * 30)
    assert spot_perp_volume_ratio(spot, perp) == 1.5
    assert spot_perp_volume_ratio(pd.Series(dtype=float), perp) == 0.0
    assert spot_perp_volume_ratio(None, perp) == 0.0
