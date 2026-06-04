from pump_detector.liquidations import coinalyze


def test_coinalyze_lookback_matches_chart_windows():
    day = 24 * 3600

    assert coinalyze._lookback_seconds("1d") == 244 * day
    assert coinalyze._lookback_seconds("4h") == 88 * day
