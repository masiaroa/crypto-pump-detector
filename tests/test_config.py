import pytest

from pump_detector.config import VALID_TIMEFRAMES, load_settings, load_watchlist
from pump_detector.symbols import normalize_symbol


REQUIRED_WATCHLIST_BASES = {
    "AAVE",
    "ADA",
    "APT",
    "ARB",
    "ATOM",
    "AVAX",
    "BCH",
    "BTC",
    "DOGE",
    "DOT",
    "ETC",
    "ETH",
    "FIL",
    "S",
    "HBAR",
    "INJ",
    "LINK",
    "LTC",
    "MANA",
    "POL",
    "NEAR",
    "NEO",
    "ONDO",
    "OP",
    "RUNE",
    "SAND",
    "SUI",
    "SOL",
    "TAO",
    "TON",
    "THETA",
    "UNI",
    "VIRTUAL",
    "XLM",
    "XRP",
    "ZEC",
}


def test_load_settings_default_timeframes_include_4h_and_1d(monkeypatch):
    """Without env-var override the default timeframes should include 4h and 1d."""
    monkeypatch.delenv("SCAN_TIMEFRAME", raising=False)
    settings = load_settings()
    assert settings.timeframes == ["4h", "1d"]


def test_watchlist_includes_required_crypto_bases_as_supported_markets():
    supported_bases = {
        market.base
        for market in (normalize_symbol(raw_symbol) for raw_symbol in load_watchlist())
        if market.supported
    }

    assert REQUIRED_WATCHLIST_BASES <= supported_bases


def test_scan_timeframe_env_overrides_yaml(monkeypatch):
    """SCAN_TIMEFRAME env var replaces the YAML/default timeframes list."""
    monkeypatch.setenv("SCAN_TIMEFRAME", "1d")
    settings = load_settings()
    assert settings.timeframes == ["1d"]


def test_scan_timeframe_env_accepts_csv_preserving_order(monkeypatch):
    """SCAN_TIMEFRAME can force multiple timeframes for CI/manual recalculates."""
    monkeypatch.setenv("SCAN_TIMEFRAME", "4h, 1d")
    settings = load_settings()
    assert settings.timeframes == ["4h", "1d"]


def test_scan_timeframe_env_invalid_raises(monkeypatch):
    """An invalid SCAN_TIMEFRAME value must raise ValueError."""
    monkeypatch.setenv("SCAN_TIMEFRAME", "4h,15m")
    with pytest.raises(ValueError, match="not valid"):
        load_settings()


def test_scan_timeframe_env_empty_string_is_ignored(monkeypatch):
    """Empty string should be treated as 'not set'."""
    monkeypatch.setenv("SCAN_TIMEFRAME", "  ")
    settings = load_settings()
    # Falls back to YAML / default
    assert len(settings.timeframes) >= 1


def test_valid_timeframes_constant():
    assert VALID_TIMEFRAMES == {"1h", "4h", "1d"}


def test_load_settings_includes_liquidation_defaults(monkeypatch):
    monkeypatch.delenv("SCAN_TIMEFRAME", raising=False)
    settings = load_settings()

    assert settings.liquidations["enabled"] is True
    assert settings.liquidations["coinalyze"]["enabled"] is True
    # WS burst + projected blocks were removed in favour of coinalyze-only.
    assert "executed" not in settings.liquidations
    assert "projected" not in settings.liquidations


def test_load_settings_includes_coinalyze_dashboard_defaults(monkeypatch):
    monkeypatch.delenv("SCAN_TIMEFRAME", raising=False)
    settings = load_settings()

    assert settings.coinalyze_dashboard["enabled"] is True
    assert settings.coinalyze_dashboard["timeframes"] == ["4h", "1d"]
    assert settings.coinalyze_dashboard["cache_dir"] == "data/coinalyze"
    assert settings.coinalyze_dashboard["max_age_hours"] == 6
    assert settings.coinalyze_dashboard["core_exchanges"] == ["BINANCE", "BYBIT", "OKX"]
