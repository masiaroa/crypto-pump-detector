"""User-account long/short ratio on perpetual futures.

Each exchange publishes how many *accounts* are net long vs short. Unlike OI
or volume, this metric tells you crowd sentiment: 0.65 / 0.35 means 65% of
accounts are long, 35% short. We fetch the latest sample from the same public
endpoints the scanner already uses (Binance USDM/COIN-M, Bybit linear, OKX).

The function is best-effort: any HTTP error or unexpected payload returns
``(0.0, 0.0, "")``, the caller renders ``"—"`` in the UI. Ratios change
slowly (hourly granularity at best), so the scanner fetches once per symbol
and reuses across timeframes.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from .symbols import normalize_symbol


@dataclass(frozen=True)
class LongShortRatio:
    long_pct: float
    short_pct: float
    source: str


_EMPTY = LongShortRatio(long_pct=0.0, short_pct=0.0, source="")


def fetch_long_short_ratio(
    raw_symbol: str,
    period: str = "4h",
    *,
    session: requests.Session | None = None,
) -> LongShortRatio:
    market = normalize_symbol(raw_symbol)
    if not market.supported:
        return _EMPTY
    http = session or requests.Session()
    exchange = market.exchange
    # Try the native exchange first, then Binance USDM as a fallback so we still
    # surface ratios for OKX/Bitget symbols (Binance covers most majors).
    candidates: list[str] = []
    if exchange == "BYBIT":
        candidates = ["BYBIT", "BINANCE"]
    elif exchange == "BINANCE":
        candidates = ["BINANCE"]
    elif exchange == "OKX":
        candidates = ["OKX", "BINANCE"]
    else:
        candidates = ["BINANCE"]
    for provider in candidates:
        try:
            if provider == "BINANCE":
                ratio = _fetch_binance(market, period, http)
            elif provider == "BYBIT":
                ratio = _fetch_bybit(market, period, http)
            elif provider == "OKX":
                ratio = _fetch_okx(market, period, http)
            else:
                continue
            if ratio.long_pct > 0 or ratio.short_pct > 0:
                return ratio
        except Exception:  # noqa: BLE001 - degrade silently, UI shows "—"
            continue
    return _EMPTY


_BINANCE_PERIODS = {"1h": "1h", "4h": "4h", "1d": "1d"}
_BYBIT_PERIODS = {"1h": "1h", "4h": "4h", "1d": "1d"}
_OKX_PERIODS = {"1h": "1H", "4h": "4H", "1d": "1D"}


def _fetch_binance(market, period: str, http: requests.Session) -> LongShortRatio:
    period = _BINANCE_PERIODS.get(period, "4h")
    if market.quote == "USD":
        url = "https://dapi.binance.com/futures/data/topLongShortAccountRatio"
        params = {"pair": f"{market.base}USD", "period": period, "limit": 1}
    else:
        url = "https://fapi.binance.com/futures/data/topLongShortAccountRatio"
        params = {"symbol": f"{market.base}USDT", "period": period, "limit": 1}
    response = http.get(url, params=params, timeout=10)
    if response.status_code != 200:
        return _EMPTY
    payload = response.json()
    if not isinstance(payload, list) or not payload:
        return _EMPTY
    row = payload[-1]
    long_pct = _to_float(row.get("longAccount"))
    short_pct = _to_float(row.get("shortAccount"))
    if long_pct <= 0 and short_pct <= 0:
        return _EMPTY
    return LongShortRatio(long_pct=long_pct, short_pct=short_pct, source="binance")


def _fetch_bybit(market, period: str, http: requests.Session) -> LongShortRatio:
    period = _BYBIT_PERIODS.get(period, "4h")
    url = "https://api.bybit.com/v5/market/account-ratio"
    params = {
        "category": "linear",
        "symbol": f"{market.base}{market.quote}",
        "period": period,
        "limit": 1,
    }
    response = http.get(url, params=params, timeout=10)
    if response.status_code != 200:
        return _EMPTY
    payload = response.json()
    rows = ((payload or {}).get("result") or {}).get("list") or []
    if not rows:
        return _EMPTY
    row = rows[-1]
    long_pct = _to_float(row.get("buyRatio"))
    short_pct = _to_float(row.get("sellRatio"))
    if long_pct <= 0 and short_pct <= 0:
        return _EMPTY
    return LongShortRatio(long_pct=long_pct, short_pct=short_pct, source="bybit")


def _fetch_okx(market, period: str, http: requests.Session) -> LongShortRatio:
    period = _OKX_PERIODS.get(period, "4H")
    url = "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio"
    params = {"ccy": market.base, "period": period}
    response = http.get(url, params=params, timeout=10)
    if response.status_code != 200:
        return _EMPTY
    payload = response.json()
    rows = (payload or {}).get("data") or []
    if not rows:
        return _EMPTY
    # OKX returns [[timestamp, ratio], ...] most-recent-first; ratio = long/short.
    latest = rows[0]
    if len(latest) < 2:
        return _EMPTY
    ratio = _to_float(latest[1])
    if ratio <= 0:
        return _EMPTY
    long_pct = ratio / (1.0 + ratio)
    short_pct = 1.0 - long_pct
    return LongShortRatio(long_pct=long_pct, short_pct=short_pct, source="okx")


def _to_float(value: object) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = ["LongShortRatio", "fetch_long_short_ratio"]
