from __future__ import annotations

from pump_detector.liquidations.executed_ws import (
    parse_binance_ws,
    parse_bybit_ws,
    parse_okx_ws,
)


def test_parse_binance_ws_normalizes_force_order():
    msg = {
        "e": "forceOrder",
        "E": 1710000000000,
        "o": {
            "s": "BTCUSDT",
            "S": "SELL",
            "ap": "62000.5",
            "z": "0.25",
            "T": 1710000000000,
        },
    }
    rec = parse_binance_ws(msg)
    assert rec is not None
    assert rec["timestamp_ms"] == 1710000000000
    assert rec["price"] == 62000.5
    assert rec["quantity"] == 0.25
    assert rec["notional"] == 62000.5 * 0.25
    assert rec["side"] == "long"
    assert rec["source"] == "binance_ws"
    assert rec["symbol"] == "BTCUSDT"


def test_parse_binance_ws_returns_none_when_qty_missing():
    assert parse_binance_ws({"o": {"s": "X", "S": "BUY", "ap": "10", "z": "0", "T": 1}}) is None


def test_parse_bybit_ws_list_payload():
    msg = {
        "topic": "allLiquidation.linear",
        "ts": 1710000000000,
        "data": [
            {
                "symbol": "BTCUSDT",
                "side": "Buy",
                "price": "61000",
                "size": "0.1",
                "updatedTime": 1710000000000,
            }
        ],
    }
    rows = parse_bybit_ws(msg)
    assert len(rows) == 1
    assert rows[0]["side"] == "short"
    assert rows[0]["source"] == "bybit_ws"
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["notional"] == 6100.0


def test_parse_okx_ws_nested_details():
    msg = {
        "arg": {"channel": "liquidation-orders", "instType": "SWAP"},
        "data": [
            {
                "instId": "BTC-USDT-SWAP",
                "details": [
                    {"side": "sell", "bkPx": "62000", "sz": "0.1", "ts": "1710000000000"}
                ],
            }
        ],
    }
    rows = parse_okx_ws(msg)
    assert len(rows) == 1
    assert rows[0]["side"] == "long"
    assert rows[0]["source"] == "okx_ws"
    assert rows[0]["symbol"] == "BTC-USDT-SWAP"
    assert rows[0]["timestamp_ms"] == 1710000000000
