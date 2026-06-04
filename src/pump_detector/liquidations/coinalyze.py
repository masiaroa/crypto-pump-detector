"""Historical aggregated liquidations from Coinalyze (free API key).

Coinalyze exposes a free REST API (40 req/min) with bucketed liquidation
history per future-market symbol. Unlike CoinGlass (paywalled) and the
exchange WS feeds (live-only), this lets us paint executed liquidations
on the chart for ANY symbol immediately — no need to wait for events to
occur live.

Setup:
    1. Sign up at https://coinalyze.net and grab a key from
       https://coinalyze.net/account/api-key/
    2. Export it before running scan/streamlit::

        export COINALYZE_API_KEY="..."

The response gives ``{t, l, s}`` per bucket: timestamp (sec), longs USD
liquidated, shorts USD liquidated. We emit two canonical rows per bucket
(one ``side=long``, one ``side=short``) with ``price=NaN`` — the chart
layer snaps NaN prices to the candle close at that timestamp.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from ..config import ROOT
from ..symbols import normalize_symbol
from .schema import LIQUIDATION_COLUMNS, empty_liquidations, to_float


log = logging.getLogger(__name__)

BASE_URL = "https://api.coinalyze.net/v1"
LIQ_ENDPOINT = f"{BASE_URL}/liquidation-history"
MARKETS_ENDPOINT = f"{BASE_URL}/future-markets"
MARKETS_CACHE = ROOT / "data" / "liquidations" / "_coinalyze_markets.json"
MARKETS_TTL_SECONDS = 24 * 60 * 60  # daily refresh is plenty


# Exchange code mapping is dynamic (from /future-markets), but we keep a
# fallback so the common path keeps working even if the markets cache fails.
# These codes come from a manual inspection of `/v1/future-markets`.
EXCHANGE_NAME_HINTS = {
    "BINANCE": ["binance", "a"],
    "BYBIT": ["bybit", "6"],
    "OKX": ["okx", "okex", "3"],
    "BITGET": ["bitget", "k"],
}


@dataclass(frozen=True)
class CoinalyzeDiagnostic:
    provider: str = "coinalyze"
    status: str = "unknown"
    message: str = ""
    raw_symbol: str = ""
    timeframe: str = ""
    resolved_symbol: str | None = None
    rows: int = 0
    notional: float = 0.0
    first_timestamp: pd.Timestamp | None = None
    last_timestamp: pd.Timestamp | None = None
    http_status: int | None = None


def _coinalyze_interval(timeframe: str) -> str:
    return {"1h": "1hour", "4h": "4hour", "1d": "daily"}.get(timeframe, "1hour")


def _lookback_seconds(timeframe: str) -> int:
    # Coinalyze keeps 1500-2000 datapoints for intraday timeframes, so we
    # request a generous window aligned with what the chart shows.
    return {"1h": 7 * 24 * 3600, "4h": 88 * 24 * 3600, "1d": 244 * 24 * 3600}.get(
        timeframe, 7 * 24 * 3600
    )


def _load_markets_cache(session: requests.Session, api_key: str) -> list[dict[str, Any]]:
    """Return Coinalyze's future-markets list, cached for 24h on disk."""
    try:
        if MARKETS_CACHE.exists():
            age = time.time() - MARKETS_CACHE.stat().st_mtime
            if age < MARKETS_TTL_SECONDS:
                with MARKETS_CACHE.open("r", encoding="utf-8") as fh:
                    return json.load(fh)
    except (OSError, json.JSONDecodeError):
        pass

    try:
        response = session.get(
            MARKETS_ENDPOINT,
            headers={"api_key": api_key, "Accept": "application/json"},
            timeout=10,
        )
        if response.status_code != 200:
            return []
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        log.debug("coinalyze /future-markets failed: %s", exc)
        return []

    try:
        MARKETS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with MARKETS_CACHE.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass
    return data if isinstance(data, list) else []


def coinalyze_symbol(
    raw_symbol: str,
    markets: list[dict[str, Any]] | None = None,
) -> str | None:
    """Resolve `EXCHANGE:BASEQUOTE.P` -> Coinalyze symbol like `SANDUSDT_PERP.A`.

    Uses the cached `/future-markets` listing when available. Falls back to
    well-known exchange codes when the listing is missing or stale.
    """
    market = normalize_symbol(raw_symbol)
    if not market.base or market.contract_type != "perp":
        return None
    if market.quote not in {"USDT", "USD"}:
        return None

    base = market.base
    quote = market.quote
    exchange = market.exchange.upper()

    if markets:
        hints = EXCHANGE_NAME_HINTS.get(exchange, [exchange.lower()])
        for entry in markets:
            if not isinstance(entry, dict):
                continue
            if not entry.get("is_perpetual"):
                continue
            if (entry.get("base_asset") or "").upper() != base:
                continue
            if (entry.get("quote_asset") or "").upper() != quote:
                continue
            ex_name = (entry.get("exchange") or "").lower()
            if any(h in ex_name for h in hints):
                sym = entry.get("symbol")
                if isinstance(sym, str) and sym:
                    return sym

    # Fallback: well-known codes (observed empirically on Coinalyze).
    if exchange == "BYBIT":
        return f"{base}{quote}.6"
    fallback_codes = {
        "BINANCE": "A",  # USDM
        "OKX": "3",
        "BITGET": "K",
    }
    code = fallback_codes.get(exchange)
    if not code:
        return None
    suffix = "_PERP" if quote == "USDT" else "_PERP"
    return f"{base}{quote}{suffix}.{code}"


def parse_coinalyze_liquidations(payload: Any) -> pd.DataFrame:
    """Convert Coinalyze ``[{symbol, history:[{t,l,s}]}]`` to canonical rows.

    Emits two rows per bucket so the chart can colour longs vs shorts
    independently. ``price`` is NaN so the chart layer can snap to the
    candle close at that timestamp.
    """
    if not isinstance(payload, list):
        return empty_liquidations()
    rows: list[dict[str, Any]] = []
    for series in payload:
        if not isinstance(series, dict):
            continue
        history = series.get("history") or []
        for bucket in history:
            if not isinstance(bucket, dict):
                continue
            ts_s = int(to_float(bucket.get("t")))
            if ts_s <= 0:
                continue
            ts = pd.to_datetime(ts_s, unit="s", utc=True)
            long_notional = to_float(bucket.get("l"))
            short_notional = to_float(bucket.get("s"))
            if long_notional > 0:
                rows.append(
                    {
                        "timestamp": ts,
                        "price": float("nan"),
                        "quantity": 0.0,
                        "notional": long_notional,
                        "side": "long",
                        "kind": "executed",
                        "source": "coinalyze",
                    }
                )
            if short_notional > 0:
                rows.append(
                    {
                        "timestamp": ts,
                        "price": float("nan"),
                        "quantity": 0.0,
                        "notional": short_notional,
                        "side": "short",
                        "kind": "executed",
                        "source": "coinalyze",
                    }
                )
    if not rows:
        return empty_liquidations()
    return (
        pd.DataFrame(rows, columns=LIQUIDATION_COLUMNS)
        .sort_values("timestamp")
        .reset_index(drop=True)
    )


def fetch_coinalyze_liquidations(
    raw_symbol: str,
    timeframe: str,
    cfg: dict[str, Any] | None = None,
    *,
    session: requests.Session | None = None,
    now_s: int | None = None,
) -> pd.DataFrame:
    """Fetch aggregated executed liquidations for one symbol.

    Returns empty when no API key is set, when the symbol cannot be mapped,
    or when the API errors. Never raises.
    """
    frame, _diagnostic = fetch_coinalyze_liquidations_with_diagnostic(
        raw_symbol,
        timeframe,
        cfg,
        session=session,
        now_s=now_s,
    )
    return frame


def fetch_coinalyze_liquidations_with_diagnostic(
    raw_symbol: str,
    timeframe: str,
    cfg: dict[str, Any] | None = None,
    *,
    session: requests.Session | None = None,
    now_s: int | None = None,
) -> tuple[pd.DataFrame, CoinalyzeDiagnostic]:
    """Fetch aggregated executed liquidations plus a user-facing diagnostic.

    The function never raises. It keeps enough context for the dashboard to
    explain whether Coinalyze worked, returned no rows, or was unavailable.
    """
    cfg = dict(cfg or {})
    base_diag = {
        "raw_symbol": raw_symbol,
        "timeframe": timeframe,
    }
    if not cfg.get("enabled", True):
        return empty_liquidations(), CoinalyzeDiagnostic(
            **base_diag,
            status="disabled",
            message="Coinalyze esta desactivado en la configuracion.",
        )

    api_key = (cfg.get("api_key") or os.environ.get("COINALYZE_API_KEY", "")).strip()
    if not api_key:
        return empty_liquidations(), CoinalyzeDiagnostic(
            **base_diag,
            status="missing_key",
            message="No se ha detectado COINALYZE_API_KEY.",
        )

    http = session or requests.Session()
    markets = _load_markets_cache(http, api_key)
    cg_symbol = coinalyze_symbol(raw_symbol, markets)
    if not cg_symbol:
        return empty_liquidations(), CoinalyzeDiagnostic(
            **base_diag,
            status="symbol_unresolved",
            message="No se pudo mapear el simbolo a Coinalyze.",
        )

    to_s = int(now_s if now_s is not None else time.time())
    from_s = to_s - _lookback_seconds(timeframe)

    params = {
        "symbols": cg_symbol,
        "interval": _coinalyze_interval(timeframe),
        "from": from_s,
        "to": to_s,
        "convert_to_usd": "true",
    }
    try:
        response = http.get(
            LIQ_ENDPOINT,
            headers={"api_key": api_key, "Accept": "application/json"},
            params=params,
            timeout=12,
        )
        if response.status_code != 200:
            log.debug(
                "coinalyze /liquidation-history %s -> %s",
                cg_symbol,
                response.status_code,
            )
            return empty_liquidations(), CoinalyzeDiagnostic(
                **base_diag,
                status="http_error",
                message=f"Coinalyze devolvio HTTP {response.status_code}.",
                resolved_symbol=cg_symbol,
                http_status=response.status_code,
            )
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        log.debug("coinalyze fetch failed for %s: %s", cg_symbol, exc)
        return empty_liquidations(), CoinalyzeDiagnostic(
            **base_diag,
            status="request_error",
            message=f"No se pudo consultar Coinalyze: {exc}",
            resolved_symbol=cg_symbol,
        )
    frame = parse_coinalyze_liquidations(payload)
    if frame.empty:
        return frame, CoinalyzeDiagnostic(
            **base_diag,
            status="empty",
            message="Coinalyze respondio correctamente, pero sin liquidaciones para esta ventana.",
            resolved_symbol=cg_symbol,
            http_status=response.status_code,
        )
    return frame, CoinalyzeDiagnostic(
        **base_diag,
        status="ok",
        message="Coinalyze OK.",
        resolved_symbol=cg_symbol,
        rows=len(frame),
        notional=float(frame["notional"].sum()),
        first_timestamp=frame["timestamp"].min(),
        last_timestamp=frame["timestamp"].max(),
        http_status=response.status_code,
    )


__all__ = [
    "BASE_URL",
    "CoinalyzeDiagnostic",
    "LIQ_ENDPOINT",
    "MARKETS_ENDPOINT",
    "coinalyze_symbol",
    "fetch_coinalyze_liquidations",
    "fetch_coinalyze_liquidations_with_diagnostic",
    "parse_coinalyze_liquidations",
]
