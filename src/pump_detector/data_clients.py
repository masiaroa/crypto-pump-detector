from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd
import requests

from .symbols import MarketSymbol, normalize_symbol


class DataUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketData:
    symbol: MarketSymbol
    timeframe: str
    candles: pd.DataFrame
    provider: str
    notes: str = ""


def fetch_market_data(raw_symbol: str, timeframe: str, limit: int = 260) -> MarketData:
    market = normalize_symbol(raw_symbol)
    if not market.supported:
        raise DataUnavailable(f"{raw_symbol} is not a supported perpetual market")

    candidates = _provider_candidates(market)
    errors: list[str] = []
    for provider in candidates:
        try:
            if provider == "BYBIT":
                df = _fetch_bybit(market, timeframe, limit)
            elif provider == "BINANCE":
                df = _fetch_binance(market, timeframe, limit)
            elif provider == "BITGET":
                df = _fetch_bitget(market, timeframe, limit)
            else:
                continue
            if len(df) < 50:
                raise DataUnavailable(f"{provider} returned only {len(df)} candles")
            note = "" if provider == market.exchange else f"data fallback via {provider}"
            return MarketData(symbol=market, timeframe=timeframe, candles=df, provider=provider, notes=note)
        except Exception as exc:  # noqa: BLE001 - collect provider failures for UI notes
            errors.append(f"{provider}: {exc}")
    raise DataUnavailable("; ".join(errors))


def _provider_candidates(market: MarketSymbol) -> list[str]:
    preferred = market.exchange if market.exchange in {"BYBIT", "BINANCE", "BITGET"} else ""
    candidates = [preferred, "BYBIT", "BINANCE", "BITGET"]
    return [item for i, item in enumerate(candidates) if item and item not in candidates[:i]]


def _fetch_bybit(market: MarketSymbol, timeframe: str, limit: int) -> pd.DataFrame:
    interval = {"1h": "60", "4h": "240", "1d": "D"}[timeframe]
    oi_interval = {"1h": "1h", "4h": "4h", "1d": "1d"}[timeframe]
    symbol = _usdt_symbol(market)
    base = "https://api.bybit.com"

    candles = _get_json(
        f"{base}/v5/market/kline",
        {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit},
    )["result"]["list"]
    ohlcv = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"],
    )
    ohlcv = _numeric_frame(ohlcv, ["open", "high", "low", "close", "volume"])
    ohlcv["timestamp"] = pd.to_datetime(ohlcv["timestamp"].astype("int64"), unit="ms", utc=True)
    ohlcv = ohlcv.sort_values("timestamp")

    oi_raw = _get_json(
        f"{base}/v5/market/open-interest",
        {"category": "linear", "symbol": symbol, "intervalTime": oi_interval, "limit": min(limit, 200)},
    )["result"]["list"]
    oi = pd.DataFrame(oi_raw)
    oi["timestamp"] = pd.to_datetime(oi["timestamp"].astype("int64"), unit="ms", utc=True)
    oi["open_interest"] = pd.to_numeric(oi["openInterest"], errors="coerce")
    oi = oi[["timestamp", "open_interest"]].sort_values("timestamp")

    funding_raw = _get_json(
        f"{base}/v5/market/funding/history",
        {"category": "linear", "symbol": symbol, "limit": 200},
    )["result"]["list"]
    funding = pd.DataFrame(funding_raw)
    funding["timestamp"] = pd.to_datetime(funding["fundingRateTimestamp"].astype("int64"), unit="ms", utc=True)
    funding["funding_rate"] = pd.to_numeric(funding["fundingRate"], errors="coerce")
    funding = funding[["timestamp", "funding_rate"]].sort_values("timestamp")

    return _merge_market_frames(ohlcv, oi, funding)


def _fetch_binance(market: MarketSymbol, timeframe: str, limit: int) -> pd.DataFrame:
    if market.quote == "USD":
        return _fetch_binance_coin_m(market, timeframe, limit)
    return _fetch_binance_usdt_m(market, timeframe, limit)


def _fetch_binance_usdt_m(market: MarketSymbol, timeframe: str, limit: int) -> pd.DataFrame:
    interval = {"1h": "1h", "4h": "4h", "1d": "1d"}[timeframe]
    symbol = _usdt_symbol(market)
    base = "https://fapi.binance.com"

    candles = _get_json(f"{base}/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    ohlcv = pd.DataFrame(
        candles,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_base",
            "taker_quote",
            "ignore",
        ],
    )
    ohlcv = _numeric_frame(ohlcv, ["open", "high", "low", "close", "volume"])
    ohlcv["timestamp"] = pd.to_datetime(ohlcv["timestamp"].astype("int64"), unit="ms", utc=True)

    oi_raw = _get_json(
        "https://fapi.binance.com/futures/data/openInterestHist",
        {"symbol": symbol, "period": interval, "limit": min(limit, 500)},
    )
    oi = pd.DataFrame(oi_raw)
    oi["timestamp"] = pd.to_datetime(oi["timestamp"].astype("int64"), unit="ms", utc=True)
    oi["open_interest"] = pd.to_numeric(oi["sumOpenInterest"], errors="coerce")
    oi = oi[["timestamp", "open_interest"]]

    funding_raw = _get_json(f"{base}/fapi/v1/fundingRate", {"symbol": symbol, "limit": 200})
    funding = pd.DataFrame(funding_raw)
    funding["timestamp"] = pd.to_datetime(funding["fundingTime"].astype("int64"), unit="ms", utc=True)
    funding["funding_rate"] = pd.to_numeric(funding["fundingRate"], errors="coerce")
    funding = funding[["timestamp", "funding_rate"]]

    return _merge_market_frames(ohlcv.sort_values("timestamp"), oi.sort_values("timestamp"), funding.sort_values("timestamp"))


def _fetch_binance_coin_m_daily_oi_ohlc(pair: str, limit: int) -> pd.DataFrame:
    raw = _get_json(
        "https://dapi.binance.com/futures/data/openInterestHist",
        {"pair": pair, "contractType": "PERPETUAL", "period": "4h", "limit": min(limit * 6, 500)},
    )
    oi = pd.DataFrame(raw)
    if oi.empty:
        return pd.DataFrame()
    oi["timestamp"] = pd.to_datetime(oi["timestamp"].astype("int64"), unit="ms", utc=True)
    oi["open_interest"] = pd.to_numeric(oi["sumOpenInterest"], errors="coerce")
    ohlc = oi.set_index("timestamp")["open_interest"].resample("1D").ohlc().dropna().reset_index()
    return ohlc.rename(
        columns={
            "open": "oi_open",
            "high": "oi_high",
            "low": "oi_low",
            "close": "oi_close",
        }
    )


def _fetch_binance_coin_m(market: MarketSymbol, timeframe: str, limit: int) -> pd.DataFrame:
    interval = {"1h": "1h", "4h": "4h", "1d": "1d"}[timeframe]
    symbol = f"{market.base}USD_PERP"
    pair = f"{market.base}USD"
    base = "https://dapi.binance.com"

    candles = _get_json(f"{base}/dapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    ohlcv = pd.DataFrame(
        candles,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "base_volume",
            "trades",
            "taker_volume",
            "taker_base_volume",
            "ignore",
        ],
    )
    ohlcv = _numeric_frame(ohlcv, ["open", "high", "low", "close", "volume"])
    ohlcv["timestamp"] = pd.to_datetime(ohlcv["timestamp"].astype("int64"), unit="ms", utc=True)

    oi_raw = _get_json(
        f"{base}/futures/data/openInterestHist",
        {"pair": pair, "contractType": "PERPETUAL", "period": interval, "limit": min(limit, 500)},
    )
    oi = pd.DataFrame(oi_raw)
    oi["timestamp"] = pd.to_datetime(oi["timestamp"].astype("int64"), unit="ms", utc=True)
    oi["open_interest"] = pd.to_numeric(oi["sumOpenInterest"], errors="coerce")
    oi = oi[["timestamp", "open_interest"]]

    funding_raw = _get_json(f"{base}/dapi/v1/fundingRate", {"symbol": symbol, "limit": 200})
    funding = pd.DataFrame(funding_raw)
    funding["timestamp"] = pd.to_datetime(funding["fundingTime"].astype("int64"), unit="ms", utc=True)
    funding["funding_rate"] = pd.to_numeric(funding["fundingRate"], errors="coerce")
    funding = funding[["timestamp", "funding_rate"]]

    merged = _merge_market_frames(ohlcv.sort_values("timestamp"), oi.sort_values("timestamp"), funding.sort_values("timestamp"))
    if timeframe == "1d":
        oi_ohlc = _fetch_binance_coin_m_daily_oi_ohlc(pair, limit)
        if not oi_ohlc.empty:
            merged = merged.merge(oi_ohlc, on="timestamp", how="left")
            merged["open_interest"] = merged["oi_close"].fillna(merged["open_interest"])
    return merged


def _fetch_bitget(market: MarketSymbol, timeframe: str, limit: int) -> pd.DataFrame:
    granularity = {"1h": "1H", "4h": "4H", "1d": "1D"}[timeframe]
    symbol = _usdt_symbol(market)
    base = "https://api.bitget.com"
    candles = _get_json(
        f"{base}/api/v2/mix/market/candles",
        {"symbol": symbol, "productType": "USDT-FUTURES", "granularity": granularity, "limit": limit},
    )["data"]
    ohlcv = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "quote_volume"])
    ohlcv = _numeric_frame(ohlcv, ["open", "high", "low", "close", "volume"])
    ohlcv["timestamp"] = pd.to_datetime(ohlcv["timestamp"].astype("int64"), unit="ms", utc=True)
    ohlcv = ohlcv.sort_values("timestamp")

    funding_raw = _get_json(
        f"{base}/api/v2/mix/market/history-fund-rate",
        {"symbol": symbol, "productType": "USDT-FUTURES", "pageSize": 100},
    ).get("data", [])
    funding = pd.DataFrame(funding_raw)
    if funding.empty:
        funding = pd.DataFrame({"timestamp": ohlcv["timestamp"], "funding_rate": pd.NA})
    else:
        ts_col = "fundingTime" if "fundingTime" in funding.columns else "cTime"
        rate_col = "fundingRate" if "fundingRate" in funding.columns else "fundRate"
        funding["timestamp"] = pd.to_datetime(funding[ts_col].astype("int64"), unit="ms", utc=True)
        funding["funding_rate"] = pd.to_numeric(funding[rate_col], errors="coerce")
        funding = funding[["timestamp", "funding_rate"]].sort_values("timestamp")

    raise DataUnavailable("Bitget public client has no reliable historical OI endpoint in this MVP")


def _merge_market_frames(ohlcv: pd.DataFrame, oi: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    merged = pd.merge_asof(ohlcv.sort_values("timestamp"), oi.sort_values("timestamp"), on="timestamp", direction="backward")
    merged = pd.merge_asof(merged.sort_values("timestamp"), funding.sort_values("timestamp"), on="timestamp", direction="backward")
    merged["open_interest"] = merged["open_interest"].ffill()
    merged["funding_rate"] = merged["funding_rate"].ffill()
    return merged.reset_index(drop=True)


def _numeric_frame(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    out = df.copy()
    for column in columns:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    return out


def _usdt_symbol(market: MarketSymbol) -> str:
    return f"{market.base}USDT"


def _get_json(url: str, params: dict[str, object]) -> object:
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        code = str(payload.get("retCode", payload.get("code", "0")))
        if code not in {"0", "00000"}:
            raise DataUnavailable(payload.get("retMsg") or payload.get("msg") or str(payload))
    return payload
