from __future__ import annotations

import json
from pathlib import Path

from pump_detector.coinalyze_dashboard import (
    CoinalyzeClient,
    aggregate_dashboard_series,
    interval_for_timeframe,
    refresh_watchlist,
    select_core_contracts,
)


class _Resp:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "headers": headers or {},
                "params": params or {},
                "timeout": timeout,
            }
        )
        if not self.responses:
            return _Resp({}, status_code=500)
        return self.responses.pop(0)


def _markets():
    return [
        {
            "symbol": "BTCUSDT_PERP.A",
            "exchange": "Binance",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "is_perpetual": True,
            "has_ohlcv_data": True,
            "has_long_short_ratio_data": True,
        },
        {
            "symbol": "BTCUSDT.6",
            "exchange": "Bybit",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "is_perpetual": True,
            "has_ohlcv_data": True,
            "has_long_short_ratio_data": True,
        },
        {
            "symbol": "BTCUSDT_PERP.3",
            "exchange": "OKX",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "is_perpetual": True,
            "has_ohlcv_data": True,
            "has_long_short_ratio_data": False,
        },
        {
            "symbol": "ETHUSDT_PERP.A",
            "exchange": "Binance",
            "base_asset": "ETH",
            "quote_asset": "USDT",
            "is_perpetual": True,
        },
    ]


def test_interval_for_timeframe_maps_dashboard_timeframes():
    assert interval_for_timeframe("4h") == "4hour"
    assert interval_for_timeframe("1d") == "daily"


def test_select_core_contracts_prioritizes_watchlist_contract_then_core_exchanges():
    contracts = select_core_contracts(
        "BYBIT:BTCUSDT.P",
        _markets(),
        core_exchanges=["BINANCE", "BYBIT", "OKX"],
    )

    assert [c.symbol for c in contracts] == [
        "BTCUSDT.6",
        "BTCUSDT_PERP.A",
        "BTCUSDT_PERP.3",
    ]
    assert contracts[0].exchange == "BYBIT"


def test_coinalyze_client_retries_once_after_retry_after_header():
    sleeps = []
    session = _Session([
        _Resp({"message": "rate limit"}, status_code=429, headers={"Retry-After": "2"}),
        _Resp([{"ok": True}], status_code=200),
    ])
    client = CoinalyzeClient(api_key="secret", session=session, sleep_fn=sleeps.append)

    payload = client.get("/future-markets")

    assert payload == [{"ok": True}]
    assert sleeps == [2.0]
    assert len(session.calls) == 2
    assert session.calls[0]["headers"]["api_key"] == "secret"


def test_aggregate_dashboard_series_sums_and_weights_core_contracts():
    contracts = select_core_contracts("BYBIT:BTCUSDT.P", _markets())
    ohlcv = [
        {
            "symbol": "BTCUSDT.6",
            "history": [
                {"t": 1700000000, "o": 100, "h": 110, "l": 95, "c": 105, "v": 1000},
                {"t": 1700014400, "o": 105, "h": 120, "l": 104, "c": 118, "v": 1200},
            ],
        }
    ]
    oi = [
        {"symbol": "BTCUSDT.6", "history": [{"t": 1700000000, "c": 1000}, {"t": 1700014400, "c": 1200}]},
        {"symbol": "BTCUSDT_PERP.A", "history": [{"t": 1700000000, "c": 3000}, {"t": 1700014400, "c": 2800}]},
    ]
    funding = [
        {"symbol": "BTCUSDT.6", "history": [{"t": 1700014400, "c": 0.0001}]},
        {"symbol": "BTCUSDT_PERP.A", "history": [{"t": 1700014400, "c": 0.0003}]},
    ]
    liquidations = [
        {"symbol": "BTCUSDT.6", "history": [{"t": 1700014400, "l": 100, "s": 40}]},
        {"symbol": "BTCUSDT_PERP.A", "history": [{"t": 1700014400, "l": 200, "s": 10}]},
    ]
    long_short = [
        {"symbol": "BTCUSDT.6", "history": [{"t": 1700014400, "r": 1.5, "l": 0.6, "s": 0.4}]},
        {"symbol": "BTCUSDT_PERP.A", "history": [{"t": 1700014400, "r": 1.0, "l": 0.5, "s": 0.5}]},
    ]

    snapshot = aggregate_dashboard_series(
        raw_symbol="BYBIT:BTCUSDT.P",
        timeframe="4h",
        contracts=contracts,
        ohlcv_payload=ohlcv,
        oi_payload=oi,
        funding_payload=funding,
        liquidation_payload=liquidations,
        long_short_payload=long_short,
        generated_at="2026-06-05T12:00:00Z",
    )

    latest = snapshot["series"][-1]
    assert snapshot["base"] == "BTC"
    assert snapshot["primary_price_contract"] == "BTCUSDT.6"
    assert latest["open_interest"] == 4000.0
    assert latest["long_liquidations"] == 300.0
    assert latest["short_liquidations"] == 50.0
    assert latest["funding_rate"] == 0.00024
    assert latest["long_account_ratio"] == 0.53
    assert latest["short_account_ratio"] == 0.47
    assert latest["long_short_ratio"] == 1.13
    assert latest["oi_contracts"] == 2
    assert any(d["code"] == "reduced_coverage" for d in snapshot["diagnostics"])


def test_refresh_watchlist_reuses_fresh_cache_without_api_calls(tmp_path):
    cache_dir = tmp_path / "coinalyze"
    cache_dir.mkdir()
    existing = {
        "base": "BTC",
        "raw_symbol": "BYBIT:BTCUSDT.P",
        "timeframe": "4h",
        "generated_at": "2026-06-05T11:30:00Z",
        "contracts": [],
        "primary_price_contract": "",
        "series": [],
        "diagnostics": [],
    }
    (cache_dir / "BTC_4h.json").write_text(json.dumps(existing), encoding="utf-8")

    manifest = refresh_watchlist(
        ["BYBIT:BTCUSDT.P"],
        settings={
            "enabled": True,
            "timeframes": ["4h"],
            "cache_dir": str(cache_dir),
            "max_age_hours": 6,
            "core_exchanges": ["BINANCE", "BYBIT", "OKX"],
        },
        api_key="secret",
        session=_Session([]),
        now_s=1_780_000_000,
        force=False,
    )

    assert manifest["symbols"][0]["status"] == "cached"
    assert manifest["symbols"][0]["path"].endswith("BTC_4h.json")


def test_refresh_watchlist_writes_snapshot_and_manifest_with_mocked_api(tmp_path):
    cache_dir = tmp_path / "coinalyze"
    responses = [
        _Resp(_markets()),
        _Resp([{"symbol": "BTCUSDT.6", "history": [{"t": 1700000000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}]}]),
        _Resp([{"symbol": "BTCUSDT.6", "history": [{"t": 1700000000, "c": 100}]}]),
        _Resp([{"symbol": "BTCUSDT.6", "history": [{"t": 1700000000, "c": 0.0001}]}]),
        _Resp([{"symbol": "BTCUSDT.6", "history": [{"t": 1700000000, "l": 5, "s": 3}]}]),
        _Resp([{"symbol": "BTCUSDT.6", "history": [{"t": 1700000000, "r": 1.2, "l": 0.55, "s": 0.45}]}]),
    ]

    manifest = refresh_watchlist(
        ["BYBIT:BTCUSDT.P"],
        settings={
            "enabled": True,
            "timeframes": ["4h"],
            "cache_dir": str(cache_dir),
            "max_age_hours": 6,
            "core_exchanges": ["BINANCE", "BYBIT", "OKX"],
        },
        api_key="secret",
        session=_Session(responses),
        now_s=1_700_014_400,
        force=True,
    )

    written = json.loads((cache_dir / "BTC_4h.json").read_text(encoding="utf-8"))
    manifest_file = json.loads((cache_dir / "_manifest.json").read_text(encoding="utf-8"))
    assert written["series"][0]["close"] == 1.5
    assert written["series"][0]["open_interest"] == 100.0
    assert manifest["symbols"][0]["status"] == "refreshed"
    assert manifest_file["symbols"][0]["base"] == "BTC"
