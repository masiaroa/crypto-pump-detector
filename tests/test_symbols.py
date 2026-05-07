from pump_detector.symbols import normalize_symbol


def test_normalize_bybit_usdt_perp():
    market = normalize_symbol("BYBIT:TONUSDT.P")

    assert market.exchange == "BYBIT"
    assert market.base == "TON"
    assert market.quote == "USDT"
    assert market.contract_type == "perp"
    assert market.api_symbol == "TONUSDT"
    assert market.supported is True


def test_marks_unsupported_non_exchange_symbols():
    market = normalize_symbol("CRYPTOCAP:TOTAL3")

    assert market.exchange == "CRYPTOCAP"
    assert market.supported is False
    assert market.api_symbol == "TOTAL3"
