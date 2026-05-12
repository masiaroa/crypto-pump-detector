from __future__ import annotations

import os
from typing import Any

import pandas as pd
import requests

from ..symbols import normalize_symbol
from .schema import LIQUIDATION_COLUMNS, empty_liquidations, to_float


OFFICIAL_ENDPOINT = (
    "https://open-api-v4.coinglass.com/api/futures/liquidation/aggregated-heatmap/model2"
)
FRONTEND_ENDPOINT = (
    "https://fapi.coinglass.com/api/futures/liquidation/aggregated-heatmap"
)

# Headers that mimic a real browser hitting coinglass.com. The frontend
# endpoint is not documented but is what their own site calls.
FRONTEND_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.coinglass.com/",
    "Origin": "https://www.coinglass.com",
}


def parse_coinglass_projected(payload: Any) -> pd.DataFrame:
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    rows: list[dict[str, Any]] = []
    for item in _iter_points(data):
        price = to_float(item.get("price") or item.get("liqPrice") or item.get("y"))
        notional = to_float(
            item.get("notional")
            or item.get("amount")
            or item.get("value")
            or item.get("z")
        )
        ts_raw = item.get("timestamp") or item.get("time") or item.get("x")
        if isinstance(ts_raw, (int, float)):
            timestamp = pd.to_datetime(ts_raw, unit="ms", utc=True, errors="coerce")
        else:
            timestamp = pd.to_datetime(ts_raw, utc=True, errors="coerce")
        if pd.isna(timestamp):
            timestamp = pd.Timestamp.utcnow()
        if price <= 0 or notional <= 0:
            continue
        rows.append(
            {
                "timestamp": timestamp,
                "price": price,
                "quantity": 0.0,
                "notional": notional,
                "side": _coinglass_side(item),
                "kind": "projected",
                "source": _source_label(item),
            }
        )
    if not rows:
        return empty_liquidations()
    return (
        pd.DataFrame(rows, columns=LIQUIDATION_COLUMNS)
        .sort_values("price")
        .reset_index(drop=True)
    )


def _iter_points(data: Any):
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield item
            elif isinstance(item, list) and len(item) >= 3:
                yield {"x": item[0], "price": item[1], "notional": item[2]}
    elif isinstance(data, dict):
        for key in ("heatmap", "points", "liquidationHeatMap", "list", "data"):
            value = data.get(key)
            if value is not data and value is not None:
                yield from _iter_points(value)


def _coinglass_side(item: dict[str, Any]) -> str:
    side = str(item.get("side") or item.get("type") or "").lower()
    if "long" in side:
        return "long"
    if "short" in side:
        return "short"
    return "unknown"


def _source_label(item: dict[str, Any]) -> str:
    return str(item.get("_source") or "coinglass")


def _coinglass_range(timeframe: str) -> str:
    return {"1h": "24h", "4h": "3d", "1d": "7d"}.get(timeframe, "3d")


def fetch_projected_heatmap(
    raw_symbol: str,
    timeframe: str,
    cfg: dict[str, Any] | None = None,
    *,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Try the official key-protected endpoint first, fall back to the public
    frontend endpoint when no key is available or the official call errors.
    """
    cfg = dict(cfg or {})
    if not cfg.get("enabled", True):
        return empty_liquidations()

    market = normalize_symbol(raw_symbol)
    if not market.base or market.quote not in {"USDT", "USD"}:
        return empty_liquidations()

    http = session or requests.Session()
    api_key = os.environ.get("COINGLASS_API_KEY", "").strip()
    cg_range = cfg.get("range") or _coinglass_range(timeframe)

    if api_key:
        frame = _try_official(http, market.base, cg_range, api_key)
        if not frame.empty:
            return frame
        # fall through to frontend on official failure
    if cfg.get("use_frontend_endpoint", True):
        return _try_frontend(http, market.base, cg_range, timeframe)
    return empty_liquidations()


def _try_official(
    session: requests.Session, base: str, cg_range: str, api_key: str
) -> pd.DataFrame:
    try:
        response = session.get(
            OFFICIAL_ENDPOINT,
            headers={"CG-API-KEY": api_key, "Accept": "application/json"},
            params={"symbol": base, "range": cg_range},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return empty_liquidations()
    frame = parse_coinglass_projected(payload)
    if not frame.empty:
        frame["source"] = "coinglass"
    return frame


def _try_frontend(
    session: requests.Session, base: str, cg_range: str, timeframe: str
) -> pd.DataFrame:
    params = {
        "symbol": base,
        "interval": _coinglass_interval(timeframe),
        "range": cg_range,
        "exchange_list": "Binance,Bybit,OKX",
    }
    try:
        response = session.get(
            FRONTEND_ENDPOINT,
            headers=FRONTEND_HEADERS,
            params=params,
            timeout=8,
        )
        if response.status_code in {401, 402, 403, 429} or response.status_code >= 500:
            return empty_liquidations()
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return empty_liquidations()
    frame = parse_coinglass_projected(payload)
    if not frame.empty:
        frame["source"] = "coinglass_frontend"
    return frame


def _coinglass_interval(timeframe: str) -> str:
    return {"1h": "1h", "4h": "4h", "1d": "1d"}.get(timeframe, "1h")
