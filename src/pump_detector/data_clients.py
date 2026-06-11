from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

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


def _timestamp_ms(row: object, timestamp_key: str | int) -> int | None:
    try:
        if isinstance(timestamp_key, int):
            value = row[timestamp_key]  # type: ignore[index]
        elif isinstance(row, dict):
            value = row.get(timestamp_key)
        else:
            value = row[timestamp_key]  # type: ignore[index]
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError, KeyError, IndexError):
        return None


def _collect_paginated_rows(
    fetch_page: Callable[[dict[str, object]], list],
    *,
    limit: int,
    page_size: int,
    timestamp_key: str | int,
    end_param: str,
) -> list:
    """Collect newest `limit` rows by walking exchange APIs backwards."""
    if limit <= 0:
        return []
    rows: list = []
    seen: set[tuple[int, str]] = set()
    end_value: int | None = None

    while len(rows) < limit:
        request_limit = min(page_size, limit - len(rows))
        params: dict[str, object] = {"limit": request_limit}
        if end_value is not None:
            params[end_param] = end_value

        page = list(fetch_page(params) or [])
        if not page:
            break

        page_timestamps: list[int] = []
        added = 0
        for row in page:
            ts = _timestamp_ms(row, timestamp_key)
            if ts is None:
                continue
            page_timestamps.append(ts)
            identity = (ts, repr(row))
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(row)
            added += 1

        if not page_timestamps:
            break
        next_end = min(page_timestamps) - 1
        if end_value is not None and next_end >= end_value:
            break
        end_value = next_end
        if len(page) < request_limit or added == 0:
            break

    rows.sort(key=lambda row: _timestamp_ms(row, timestamp_key) or 0)
    return rows[-limit:]


def _get_paginated_rows(
    url: str,
    base_params: dict[str, object],
    *,
    limit: int,
    page_size: int,
    rows_from_payload: Callable[[object], list],
    timestamp_key: str | int,
    end_param: str = "endTime",
) -> list:
    def fetch_page(page_params: dict[str, object]) -> list:
        payload = _get_json(url, {**base_params, **page_params})
        return rows_from_payload(payload)

    return _collect_paginated_rows(
        fetch_page,
        limit=limit,
        page_size=page_size,
        timestamp_key=timestamp_key,
        end_param=end_param,
    )


def _payload_as_list(payload: object) -> list:
    return payload if isinstance(payload, list) else []


def _result_list(payload: object) -> list:
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    rows = result.get("list")
    return rows if isinstance(rows, list) else []


def _funding_limit_for_timeframe(timeframe: str, candle_limit: int) -> int:
    candles_per_day = {"1h": 24, "4h": 6, "1d": 1}.get(timeframe, 1)
    days = max(1, (candle_limit + candles_per_day - 1) // candles_per_day)
    return max(200, days * 3 + 10)


# Premium-index (basis) history is only needed for a z-score/percentile, so a
# single page per symbol is enough — keeps the extra request budget at +1.
_BASIS_HISTORY_LIMIT = 200


def _basis_frame_from_klines(rows: list, close_index: int = 4) -> pd.DataFrame:
    """[openTime, open, high, low, close, ...] kline rows → [timestamp, basis_pct]."""
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).iloc[:, [0, close_index]]
    frame.columns = ["timestamp", "basis_pct"]
    frame["timestamp"] = pd.to_datetime(frame["timestamp"].astype("int64"), unit="ms", utc=True)
    frame["basis_pct"] = pd.to_numeric(frame["basis_pct"], errors="coerce")
    return frame.dropna(subset=["basis_pct"]).sort_values("timestamp")


def _fetch_bybit_premium_index(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    interval = {"1h": "60", "4h": "240", "1d": "D"}[timeframe]
    rows = _get_paginated_rows(
        "https://api.bybit.com/v5/market/premium-index-price-kline",
        {"category": "linear", "symbol": symbol, "interval": interval},
        limit=min(limit, _BASIS_HISTORY_LIMIT),
        page_size=200,
        rows_from_payload=_result_list,
        timestamp_key=0,
        end_param="end",
    )
    return _basis_frame_from_klines(rows)


def _fetch_binance_premium_index(symbol: str, timeframe: str, limit: int, coin_m: bool = False) -> pd.DataFrame:
    interval = {"1h": "1h", "4h": "4h", "1d": "1d"}[timeframe]
    base = "https://dapi.binance.com/dapi" if coin_m else "https://fapi.binance.com/fapi"
    rows = _get_paginated_rows(
        f"{base}/v1/premiumIndexKlines",
        {"symbol": symbol, "interval": interval},
        limit=min(limit, _BASIS_HISTORY_LIMIT),
        page_size=500,
        rows_from_payload=_payload_as_list,
        timestamp_key=0,
    )
    return _basis_frame_from_klines(rows)


def _fetch_bybit(market: MarketSymbol, timeframe: str, limit: int) -> pd.DataFrame:
    interval = {"1h": "60", "4h": "240", "1d": "D"}[timeframe]
    oi_interval = {"1h": "1h", "4h": "4h", "1d": "1d"}[timeframe]
    symbol = _usdt_symbol(market)
    base = "https://api.bybit.com"

    candles = _get_paginated_rows(
        f"{base}/v5/market/kline",
        {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit},
        limit=limit,
        page_size=1000,
        rows_from_payload=_result_list,
        timestamp_key=0,
        end_param="end",
    )
    ohlcv = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"],
    )
    ohlcv = _numeric_frame(ohlcv, ["open", "high", "low", "close", "volume"])
    ohlcv["timestamp"] = pd.to_datetime(ohlcv["timestamp"].astype("int64"), unit="ms", utc=True)
    ohlcv = ohlcv.sort_values("timestamp")

    oi_raw = _get_paginated_rows(
        f"{base}/v5/market/open-interest",
        {"category": "linear", "symbol": symbol, "intervalTime": oi_interval, "limit": min(limit, 200)},
        limit=limit,
        page_size=200,
        rows_from_payload=_result_list,
        timestamp_key="timestamp",
    )
    oi = pd.DataFrame(oi_raw)
    if oi.empty or "timestamp" not in oi.columns or "openInterest" not in oi.columns:
        # API returned empty or unexpected shape — build a neutral OI frame from kline timestamps
        oi = pd.DataFrame({"timestamp": ohlcv["timestamp"], "open_interest": pd.NA})
    else:
        oi["timestamp"] = pd.to_datetime(oi["timestamp"].astype("int64"), unit="ms", utc=True)
        oi["open_interest"] = pd.to_numeric(oi["openInterest"], errors="coerce")
        oi = oi[["timestamp", "open_interest"]].sort_values("timestamp")

    funding_raw = _get_paginated_rows(
        f"{base}/v5/market/funding/history",
        {"category": "linear", "symbol": symbol, "limit": 200},
        limit=_funding_limit_for_timeframe(timeframe, limit),
        page_size=200,
        rows_from_payload=_result_list,
        timestamp_key="fundingRateTimestamp",
    )
    funding = pd.DataFrame(funding_raw)
    if funding.empty or "fundingRateTimestamp" not in funding.columns:
        funding = pd.DataFrame({"timestamp": ohlcv["timestamp"], "funding_rate": pd.NA})
    else:
        funding["timestamp"] = pd.to_datetime(funding["fundingRateTimestamp"].astype("int64"), unit="ms", utc=True)
        funding["funding_rate"] = pd.to_numeric(funding["fundingRate"], errors="coerce")
        funding = funding[["timestamp", "funding_rate"]].sort_values("timestamp")

    basis = _safe_basis(_fetch_bybit_premium_index, symbol, timeframe, limit)

    return _merge_market_frames(ohlcv, oi, funding, basis)


def _fetch_binance(market: MarketSymbol, timeframe: str, limit: int) -> pd.DataFrame:
    if market.quote == "USD":
        return _fetch_binance_coin_m(market, timeframe, limit)
    return _fetch_binance_usdt_m(market, timeframe, limit)


def _fetch_binance_usdt_m(market: MarketSymbol, timeframe: str, limit: int) -> pd.DataFrame:
    interval = {"1h": "1h", "4h": "4h", "1d": "1d"}[timeframe]
    symbol = _usdt_symbol(market)
    base = "https://fapi.binance.com"

    candles = _get_paginated_rows(
        f"{base}/fapi/v1/klines",
        {"symbol": symbol, "interval": interval, "limit": limit},
        limit=limit,
        page_size=1500,
        rows_from_payload=_payload_as_list,
        timestamp_key=0,
    )
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

    oi_raw = _get_paginated_rows(
        "https://fapi.binance.com/futures/data/openInterestHist",
        {"symbol": symbol, "period": interval, "limit": min(limit, 500)},
        limit=limit,
        page_size=500,
        rows_from_payload=_payload_as_list,
        timestamp_key="timestamp",
    )
    oi = pd.DataFrame(oi_raw)
    if oi.empty or "timestamp" not in oi.columns or "sumOpenInterest" not in oi.columns:
        oi = pd.DataFrame({"timestamp": ohlcv["timestamp"], "open_interest": pd.NA})
    else:
        oi["timestamp"] = pd.to_datetime(oi["timestamp"].astype("int64"), unit="ms", utc=True)
        oi["open_interest"] = pd.to_numeric(oi["sumOpenInterest"], errors="coerce")
        oi = oi[["timestamp", "open_interest"]]

    funding_raw = _get_paginated_rows(
        f"{base}/fapi/v1/fundingRate",
        {"symbol": symbol, "limit": 1000},
        limit=_funding_limit_for_timeframe(timeframe, limit),
        page_size=1000,
        rows_from_payload=_payload_as_list,
        timestamp_key="fundingTime",
    )
    funding = pd.DataFrame(funding_raw)
    if funding.empty or "fundingTime" not in funding.columns:
        funding = pd.DataFrame({"timestamp": ohlcv["timestamp"], "funding_rate": pd.NA})
    else:
        funding["timestamp"] = pd.to_datetime(funding["fundingTime"].astype("int64"), unit="ms", utc=True)
        funding["funding_rate"] = pd.to_numeric(funding["fundingRate"], errors="coerce")
        funding = funding[["timestamp", "funding_rate"]]

    basis = _safe_basis(_fetch_binance_premium_index, symbol, timeframe, limit)

    return _merge_market_frames(ohlcv.sort_values("timestamp"), oi.sort_values("timestamp"), funding.sort_values("timestamp"), basis)


def _fetch_binance_coin_m_daily_oi_ohlc(pair: str, limit: int) -> pd.DataFrame:
    raw = _get_paginated_rows(
        "https://dapi.binance.com/futures/data/openInterestHist",
        {"pair": pair, "contractType": "PERPETUAL", "period": "4h", "limit": min(limit * 6, 500)},
        limit=limit * 6,
        page_size=500,
        rows_from_payload=_payload_as_list,
        timestamp_key="timestamp",
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

    candles = _get_paginated_rows(
        f"{base}/dapi/v1/klines",
        {"symbol": symbol, "interval": interval, "limit": limit},
        limit=limit,
        page_size=1500,
        rows_from_payload=_payload_as_list,
        timestamp_key=0,
    )
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

    oi_raw = _get_paginated_rows(
        f"{base}/futures/data/openInterestHist",
        {"pair": pair, "contractType": "PERPETUAL", "period": interval, "limit": min(limit, 500)},
        limit=limit,
        page_size=500,
        rows_from_payload=_payload_as_list,
        timestamp_key="timestamp",
    )
    oi = pd.DataFrame(oi_raw)
    if oi.empty or "timestamp" not in oi.columns or "sumOpenInterest" not in oi.columns:
        oi = pd.DataFrame({"timestamp": ohlcv["timestamp"], "open_interest": pd.NA})
    else:
        oi["timestamp"] = pd.to_datetime(oi["timestamp"].astype("int64"), unit="ms", utc=True)
        oi["open_interest"] = pd.to_numeric(oi["sumOpenInterest"], errors="coerce")
        oi = oi[["timestamp", "open_interest"]]

    funding_raw = _get_paginated_rows(
        f"{base}/dapi/v1/fundingRate",
        {"symbol": symbol, "limit": 1000},
        limit=_funding_limit_for_timeframe(timeframe, limit),
        page_size=1000,
        rows_from_payload=_payload_as_list,
        timestamp_key="fundingTime",
    )
    funding = pd.DataFrame(funding_raw)
    if funding.empty or "fundingTime" not in funding.columns:
        funding = pd.DataFrame({"timestamp": ohlcv["timestamp"], "funding_rate": pd.NA})
    else:
        funding["timestamp"] = pd.to_datetime(funding["fundingTime"].astype("int64"), unit="ms", utc=True)
        funding["funding_rate"] = pd.to_numeric(funding["fundingRate"], errors="coerce")
    funding = funding[["timestamp", "funding_rate"]]

    basis = _safe_basis(_fetch_binance_premium_index, symbol, timeframe, limit, coin_m=True)

    merged = _merge_market_frames(ohlcv.sort_values("timestamp"), oi.sort_values("timestamp"), funding.sort_values("timestamp"), basis)
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


def _safe_basis(fetcher: Callable[..., pd.DataFrame], symbol: str, timeframe: str, limit: int, **kwargs) -> pd.DataFrame:
    """Premium-index history is an enhancement — never let it kill a symbol."""
    try:
        return fetcher(symbol, timeframe, limit, **kwargs)
    except Exception:  # noqa: BLE001 - degrade to no-basis, same as empty funding
        return pd.DataFrame()


def _merge_market_frames(
    ohlcv: pd.DataFrame,
    oi: pd.DataFrame,
    funding: pd.DataFrame,
    basis: pd.DataFrame | None = None,
) -> pd.DataFrame:
    merged = pd.merge_asof(ohlcv.sort_values("timestamp"), oi.sort_values("timestamp"), on="timestamp", direction="backward")
    merged = pd.merge_asof(merged.sort_values("timestamp"), funding.sort_values("timestamp"), on="timestamp", direction="backward")
    if basis is not None and not basis.empty:
        merged = pd.merge_asof(merged.sort_values("timestamp"), basis.sort_values("timestamp"), on="timestamp", direction="backward")
        merged["basis_pct"] = merged["basis_pct"].ffill()
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
