import pandas as pd

from pump_detector.signals import classify_funding, compute_indicators, evaluate_latest, mark_signal_history


def test_classify_funding_thresholds():
    assert classify_funding(-0.0001) == "NEGATIVE"
    assert classify_funding(0.00005) == "NEUTRAL"
    assert classify_funding(0.0002) == "POSITIVE"
    assert classify_funding(0.0008) == "HOT"


def test_evaluate_latest_flags_first_leveraged_impulse():
    rows = []
    price = 10.0
    oi = 1000.0
    for i in range(120):
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=4 * i),
                "open": price,
                "high": price * 1.011,
                "low": price * 0.995,
                "close": price * 1.004,
                "volume": 100_000 + i,
                "open_interest": oi,
                "funding_rate": 0.00004,
            }
        )
        price *= 1.001
        oi *= 1.001

    rows[-1].update(
        {
            "open": price,
            "high": price * 1.18,
            "low": price * 0.99,
            "close": price * 1.16,
            "volume": 450_000,
            "open_interest": oi * 1.22,
            "funding_rate": 0.00004,
        }
    )
    df = compute_indicators(pd.DataFrame(rows), lookback_stats=100)

    signal = evaluate_latest(
        df,
        timeframe="4h",
        symbol="BYBIT:TONUSDT.P",
        exchange="BYBIT",
        lookback_no_previous_signal=10,
        price_zscore_threshold=2.5,
        oi_zscore_threshold=2.5,
        close_position_min=0.65,
    )

    assert signal.price_impulse is True
    assert signal.oi_impulse is True
    assert signal.first_impulse is True
    assert signal.signal_active is True
    assert signal.early_bullish_score > signal.blowoff_risk_score


def test_mark_signal_history_marks_first_impulse_but_not_late_followup():
    rows = []
    price = 10.0
    oi = 1000.0
    for i in range(150):
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=4 * i),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.995,
                "close": price * 1.002,
                "volume": 100_000,
                "open_interest": oi,
                "funding_rate": 0.00002,
            }
        )
        price *= 1.0005
        oi *= 1.0005

    for idx in [130, 133]:
        rows[idx].update(
            {
                "open": rows[idx]["open"],
                "high": rows[idx]["open"] * 1.17,
                "low": rows[idx]["open"] * 0.99,
                "close": rows[idx]["open"] * 1.15,
                "volume": 500_000,
                "open_interest": rows[idx - 1]["open_interest"] * 1.25,
            }
        )
    df = compute_indicators(pd.DataFrame(rows), lookback_stats=100)

    marked = mark_signal_history(
        df,
        lookback_no_previous_signal=10,
        price_zscore_threshold=2.5,
        oi_zscore_threshold=2.5,
        close_position_min=0.65,
    )

    active_timestamps = marked.loc[marked["signal_active_flag"], "timestamp"].tolist()
    assert rows[130]["timestamp"] in active_timestamps
    assert rows[133]["timestamp"] not in active_timestamps
    assert bool(marked.loc[130, "price_impulse_flag"]) is True
    assert bool(marked.loc[130, "oi_impulse_flag"]) is True


def test_mark_signal_history_marks_price_breakout_pre_alert_before_oi_confirms():
    rows = []
    price = 10.0
    oi = 1000.0
    for i in range(130):
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=4 * i),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.995,
                "close": price * 1.002,
                "volume": 100_000,
                "open_interest": oi,
                "funding_rate": -0.00002,
            }
        )
        price *= 1.0003
        oi *= 1.0002

    rows[-1].update(
        {
            "open": price,
            "high": price * 1.12,
            "low": price * 0.99,
            "close": price * 1.10,
            "volume": 800_000,
            "open_interest": oi * 0.99,
            "funding_rate": -0.0001,
        }
    )
    marked = mark_signal_history(compute_indicators(pd.DataFrame(rows), lookback_stats=100))

    assert bool(marked.iloc[-1]["pre_alert_flag"]) is True
    assert bool(marked.iloc[-1]["signal_active_flag"]) is False
    assert marked.iloc[-1]["oi_open"] == marked.iloc[-2]["open_interest"]
    assert marked.iloc[-1]["oi_close"] == marked.iloc[-1]["open_interest"]


def test_mark_signal_history_marks_volume_acceleration_pre_alert():
    rows = []
    price = 10.0
    oi = 1000.0
    for i in range(130):
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=4 * i),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.995,
                "close": price * 1.002,
                "volume": 100_000,
                "open_interest": oi,
                "funding_rate": 0.00005,
            }
        )
        price *= 1.0002
        oi *= 1.0001

    rows[-1].update(
        {
            "open": price,
            "high": price * 1.08,
            "low": price * 0.99,
            "close": price * 1.075,
            "volume": 900_000,
            "open_interest": rows[-2]["open_interest"],
            "funding_rate": 0.00005,
        }
    )
    marked = mark_signal_history(compute_indicators(pd.DataFrame(rows), lookback_stats=100))

    assert bool(marked.iloc[-1]["price_impulse_flag"]) is True
    assert bool(marked.iloc[-1]["oi_impulse_flag"]) is False
    assert bool(marked.iloc[-1]["pre_alert_flag"]) is True
    assert bool(marked.iloc[-1]["hot_pre_entry_flag"]) is True


def test_mark_signal_history_confirms_when_price_follows_recent_oi_impulse():
    rows = []
    price = 10.0
    oi = 1000.0
    for i in range(130):
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=i),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.995,
                "close": price * 1.002,
                "volume": 100_000,
                "open_interest": oi,
                "funding_rate": 0.00005,
            }
        )
        price *= 1.0002
        oi *= 1.0001

    rows[-2].update(
        {
            "open": price,
            "high": price * 1.02,
            "low": price * 0.99,
            "close": price * 1.005,
            "open_interest": oi * 1.5,
        }
    )
    rows[-1].update(
        {
            "open": price * 1.005,
            "high": price * 1.20,
            "low": price * 0.99,
            "close": price * 1.16,
            "volume": 500_000,
            "open_interest": oi * 1.52,
        }
    )
    marked = mark_signal_history(compute_indicators(pd.DataFrame(rows), lookback_stats=100))

    assert bool(marked.iloc[-2]["oi_impulse_flag"]) is True
    assert bool(marked.iloc[-2]["pre_alert_flag"]) is True
    assert bool(marked.iloc[-1]["price_impulse_flag"]) is True
    assert bool(marked.iloc[-1]["recent_oi_impulse_flag"]) is True
    assert bool(marked.iloc[-1]["signal_active_flag"]) is True


def test_oi_lead_pre_alert_requires_non_bearish_price_candle():
    rows = []
    price = 10.0
    oi = 1000.0
    for i in range(130):
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=i),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price * 1.001,
                "volume": 100_000,
                "open_interest": oi,
                "funding_rate": 0.00005,
            }
        )
        price *= 1.0002
        oi *= 1.0001

    rows[-1].update(
        {
            "open": price,
            "high": price * 1.01,
            "low": price * 0.96,
            "close": price * 0.97,
            "open_interest": oi * 1.08,
        }
    )
    marked = mark_signal_history(compute_indicators(pd.DataFrame(rows), lookback_stats=100))

    assert bool(marked.iloc[-1]["oi_impulse_flag"]) is True
    assert bool(marked.iloc[-1]["pre_alert_flag"]) is False


def test_breakout_price_and_oi_extremes_confirm_even_after_prior_price_alert():
    rows = []
    price = 10.0
    oi = 1000.0
    for i in range(140):
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=i),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.995,
                "close": price * 1.001,
                "volume": 100_000,
                "open_interest": oi,
                "funding_rate": 0.0001,
            }
        )
        price *= 1.0002
        oi *= 1.0001

    rows[-20].update(
        {
            "open": price,
            "high": price * 1.08,
            "low": price * 0.99,
            "close": price * 1.07,
            "volume": 300_000,
            "open_interest": oi * 0.98,
        }
    )
    rows[-1].update(
        {
            "open": price * 1.07,
            "high": price * 1.32,
            "low": price * 1.05,
            "close": price * 1.28,
            "volume": 1_000_000,
            "open_interest": oi * 1.30,
        }
    )
    marked = mark_signal_history(compute_indicators(pd.DataFrame(rows), lookback_stats=100))

    assert bool(marked.iloc[-1]["price_impulse_flag"]) is True
    assert bool(marked.iloc[-1]["oi_impulse_flag"]) is True
    assert bool(marked.iloc[-1]["breakout_20_flag"]) is True
    assert bool(marked.iloc[-1]["signal_active_flag"]) is True


def test_daily_entry_rejects_small_non_breakout_price_move_even_with_recent_oi():
    rows = []
    price = 10.0
    oi = 1000.0
    for i in range(140):
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=i),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.995,
                "close": price * 1.001,
                "volume": 100_000,
                "open_interest": oi,
                "funding_rate": 0.00005,
            }
        )
        price *= 1.0002
        oi *= 1.0001

    rows[-2].update(
        {
            "open": price,
            "high": price * 1.03,
            "low": price * 0.99,
            "close": price * 1.02,
            "open_interest": oi * 1.15,
        }
    )
    rows[-1].update(
        {
            "open": price * 1.02,
            "high": price * 1.075,
            "low": price * 1.01,
            "close": price * 1.06,
            "volume": 150_000,
            "open_interest": oi * 1.16,
        }
    )
    marked = mark_signal_history(compute_indicators(pd.DataFrame(rows), lookback_stats=100), timeframe="1d")

    assert bool(marked.iloc[-1]["price_impulse_flag"]) is True
    assert bool(marked.iloc[-1]["signal_active_flag"]) is False
