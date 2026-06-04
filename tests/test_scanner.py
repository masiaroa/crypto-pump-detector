from pump_detector import scanner
from pump_detector.config import Settings
from pump_detector.data_clients import DataUnavailable


def _settings(timeframes: list[str]) -> Settings:
    return Settings(timeframes=timeframes, alert_conditions={}, thresholds={}, storage={})


def test_scan_watchlist_uses_eighteen_month_limits_by_timeframe(monkeypatch):
    calls: list[tuple[str, int]] = []

    def fake_fetch(raw_symbol: str, timeframe: str, limit: int):
        calls.append((timeframe, limit))
        raise DataUnavailable("offline")

    monkeypatch.setattr(scanner, "fetch_market_data", fake_fetch)

    scanner.scan_watchlist(
        symbols=["BYBIT:BTCUSDT.P"],
        settings=_settings(["4h", "1d"]),
        persist=False,
    )

    assert calls == [("4h", 3288), ("1d", 548)]


def test_scan_watchlist_preserves_explicit_limit_override(monkeypatch):
    calls: list[tuple[str, int]] = []

    def fake_fetch(raw_symbol: str, timeframe: str, limit: int):
        calls.append((timeframe, limit))
        raise DataUnavailable("offline")

    monkeypatch.setattr(scanner, "fetch_market_data", fake_fetch)

    scanner.scan_watchlist(
        symbols=["BYBIT:BTCUSDT.P"],
        settings=_settings(["4h", "1d"]),
        persist=False,
        limit=42,
    )

    assert calls == [("4h", 42), ("1d", 42)]
