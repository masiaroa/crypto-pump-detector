import numpy as np
import pandas as pd

from pump_detector import data_clients
from pump_detector.signals import classify_basis, compute_indicators, evaluate_latest, positioning_class
from pump_detector.squeeze import compute_squeeze_columns


def test_classify_basis_thresholds():
    assert classify_basis(-0.0005) == "DISCOUNT"
    assert classify_basis(0.0001) == "FLAT"
    assert classify_basis(0.0005) == "PREMIUM"
    assert classify_basis(0.0012) == "HOT"
    assert classify_basis(None) == "UNKNOWN"


def test_classify_basis_extreme_needs_high_percentile_and_premium():
    recent = pd.Series(np.linspace(-0.0005, 0.001, 50))
    assert classify_basis(0.0011, recent) == "EXTREME"
    # 95th percentile of a negative series must not flag a discount as EXTREME
    negative = pd.Series(np.linspace(-0.002, -0.0005, 50))
    assert classify_basis(-0.0005, negative) == "DISCOUNT"


def test_positioning_class_prefers_basis_falls_back_to_funding():
    assert positioning_class("DISCOUNT", "HOT") == "NEGATIVE"
    assert positioning_class("PREMIUM", "NEGATIVE") == "POSITIVE"
    assert positioning_class("UNKNOWN", "HOT") == "HOT"


def test_basis_frame_from_klines_parses_close_column():
    rows = [
        [1_700_000_000_000, "0.0001", "0.0002", "0.0000", "0.00015"],
        [1_700_014_400_000, "0.0002", "0.0003", "0.0001", "-0.0002"],
    ]
    frame = data_clients._basis_frame_from_klines(rows)
    assert list(frame.columns) == ["timestamp", "basis_pct"]
    assert frame["basis_pct"].tolist() == [0.00015, -0.0002]
    assert frame["timestamp"].is_monotonic_increasing


def test_basis_frame_from_klines_empty_payload():
    assert data_clients._basis_frame_from_klines([]).empty


def test_fetch_premium_index_clients_parse_payloads(monkeypatch):
    def fake_get_json(url, params):
        rows = [
            [1_700_000_000_000, "0.0001", "0.0002", "0.0000", "-0.0003"],
            [1_700_014_400_000, "0.0002", "0.0003", "0.0001", "0.0004"],
        ]
        if "bybit" in url:
            return {"retCode": 0, "result": {"list": rows}}
        return rows

    monkeypatch.setattr(data_clients, "_get_json", fake_get_json)
    bybit = data_clients._fetch_bybit_premium_index("SANDUSDT", "4h", 200)
    binance = data_clients._fetch_binance_premium_index("ARBUSDT", "4h", 200)
    coin_m = data_clients._fetch_binance_premium_index("BCHUSD_PERP", "4h", 200, coin_m=True)
    for frame in (bybit, binance, coin_m):
        assert frame["basis_pct"].tolist() == [-0.0003, 0.0004]


def test_safe_basis_degrades_to_empty_on_error():
    def boom(symbol, timeframe, limit):
        raise RuntimeError("blocked")

    frame = data_clients._safe_basis(boom, "SANDUSDT", "4h", 200)
    assert frame.empty


def test_merge_market_frames_with_and_without_basis():
    ts = pd.date_range("2026-01-01", periods=4, freq="4h", tz="UTC")
    ohlcv = pd.DataFrame({"timestamp": ts, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 10.0})
    oi = pd.DataFrame({"timestamp": ts, "open_interest": [100.0, 101.0, 102.0, 103.0]})
    funding = pd.DataFrame({"timestamp": ts[:2], "funding_rate": [0.0001, 0.0002]})
    basis = pd.DataFrame({"timestamp": ts[:2], "basis_pct": [-0.0003, -0.0001]})

    merged = data_clients._merge_market_frames(ohlcv, oi, funding, basis)
    assert merged["basis_pct"].tolist() == [-0.0003, -0.0001, -0.0001, -0.0001]  # ffilled

    plain = data_clients._merge_market_frames(ohlcv, oi, funding, pd.DataFrame())
    assert "basis_pct" not in plain.columns


def _frame_with_basis(basis_value: float) -> pd.DataFrame:
    rows = []
    price, oi = 10.0, 1000.0
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
                "funding_rate": 0.001,  # HOT funding
                "basis_pct": basis_value + i * 1e-7,  # slight drift so zscore is defined
            }
        )
        price *= 1.001
        oi *= 1.001
    rows[-1].update(
        {
            "high": price * 1.18,
            "close": price * 1.16,
            "volume": 450_000,
            "open_interest": oi * 1.22,
        }
    )
    return pd.DataFrame(rows)


def test_evaluate_latest_uses_basis_over_funding_for_scores():
    df_discount = compute_indicators(_frame_with_basis(-0.0005), lookback_stats=100)
    df_no_basis = compute_indicators(_frame_with_basis(-0.0005).drop(columns=["basis_pct"]), lookback_stats=100)

    assert "basis_zscore" in df_discount.columns

    snap_discount = evaluate_latest(df_discount, timeframe="4h", symbol="BYBIT:TONUSDT.P", exchange="BYBIT")
    snap_funding_only = evaluate_latest(df_no_basis, timeframe="4h", symbol="BYBIT:TONUSDT.P", exchange="BYBIT")

    assert snap_discount.basis_classification == "DISCOUNT"
    assert snap_discount.basis_pct < 0
    assert snap_funding_only.basis_classification == "UNKNOWN"
    # Same HOT funding, but the perp discount overrides it: risk drops (the
    # early score saturates at 100 on an impulse this size, so >= there).
    assert snap_discount.early_bullish_score >= snap_funding_only.early_bullish_score
    assert snap_discount.blowoff_risk_score < snap_funding_only.blowoff_risk_score


def test_squeeze_score_gets_basis_discount_points():
    base = _frame_with_basis(-0.0005)
    # Shorts-building tail: OI climbs on red candles
    last_close = base.iloc[-1]["close"]
    extra = []
    oi = base.iloc[-1]["open_interest"]
    for i in range(120, 150):
        oi *= 1.005
        close = last_close * 0.9985
        extra.append(
            {
                "timestamp": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=4 * i),
                "open": last_close,
                "high": last_close * 1.002,
                "low": close * 0.998,
                "close": close,
                "volume": 80_000.0,
                "open_interest": oi,
                "funding_rate": -0.0001,
                "basis_pct": -0.0008 - i * 1e-6,  # deepening discount
            }
        )
        last_close = close
    frame = pd.concat([base, pd.DataFrame(extra)], ignore_index=True)

    with_basis = compute_squeeze_columns(compute_indicators(frame, lookback_stats=100))
    without_basis = compute_squeeze_columns(
        compute_indicators(frame.drop(columns=["basis_pct"]), lookback_stats=100)
    )
    # The deepening discount must contribute points beyond renormalisation.
    assert with_basis.iloc[-1]["squeeze_setup_score"] > without_basis.iloc[-1]["squeeze_setup_score"]
