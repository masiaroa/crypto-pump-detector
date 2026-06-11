from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pump_detector.symbols import normalize_symbol


DEFAULT_CORE_EXCHANGES = ["BINANCE", "BYBIT", "OKX"]
INTERVALS = {"1h": "1hour", "4h": "4hour", "1d": "daily"}
LOOKBACK_SECONDS = {"1h": 7 * 24 * 3600, "4h": 88 * 24 * 3600, "1d": 244 * 24 * 3600}

EXCHANGE_HINTS = {
    "BINANCE": ("binance", ".a", "_perp.a"),
    "BYBIT": ("bybit", ".6"),
    "OKX": ("okx", "okex", ".3"),
    "BITGET": ("bitget", ".k"),
}


@dataclass(frozen=True)
class Contract:
    symbol: str
    exchange: str
    base: str
    quote: str
    has_ohlcv_data: bool = True
    has_long_short_ratio_data: bool = True


def interval_for_timeframe(timeframe: str) -> str:
    if timeframe not in INTERVALS:
        raise ValueError(f"Unsupported Coinalyze dashboard timeframe: {timeframe}")
    return INTERVALS[timeframe]


def lookback_seconds(timeframe: str) -> int:
    return LOOKBACK_SECONDS.get(timeframe, LOOKBACK_SECONDS["4h"])


def cache_file_for(cache_dir: str | Path, base: str, timeframe: str) -> Path:
    safe_base = "".join(ch for ch in base.upper() if ch.isalnum() or ch in {"_", "-"})
    return Path(cache_dir) / f"{safe_base}_{timeframe}.json"


def select_core_contracts(
    raw_symbol: str,
    markets: list[dict[str, Any]],
    core_exchanges: Iterable[str] | None = None,
) -> list[Contract]:
    market = normalize_symbol(raw_symbol)
    if not market.supported or market.contract_type != "perp":
        return []
    if market.quote not in {"USDT", "USD"}:
        return []

    core = [exchange.upper() for exchange in (core_exchanges or DEFAULT_CORE_EXCHANGES)]
    order = [market.exchange.upper()] + [exchange for exchange in core if exchange != market.exchange.upper()]
    candidates: list[Contract] = []

    for entry in markets:
        if not isinstance(entry, dict):
            continue
        if not entry.get("is_perpetual", False):
            continue
        if (entry.get("base_asset") or "").upper() != market.base:
            continue
        if (entry.get("quote_asset") or "").upper() != market.quote:
            continue
        exchange = _entry_exchange_code(entry)
        if exchange not in order:
            continue
        symbol = entry.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            continue
        candidates.append(
            Contract(
                symbol=symbol,
                exchange=exchange,
                base=market.base,
                quote=market.quote,
                has_ohlcv_data=bool(entry.get("has_ohlcv_data", True)),
                has_long_short_ratio_data=bool(entry.get("has_long_short_ratio_data", True)),
            )
        )

    by_symbol: dict[str, Contract] = {}
    for item in candidates:
        by_symbol.setdefault(item.symbol, item)

    def sort_key(contract: Contract) -> tuple[int, str]:
        try:
            position = order.index(contract.exchange)
        except ValueError:
            position = len(order)
        return position, contract.symbol

    return sorted(by_symbol.values(), key=sort_key)


def aggregate_dashboard_series(
    *,
    raw_symbol: str,
    timeframe: str,
    contracts: list[Contract],
    ohlcv_payload: Any,
    oi_payload: Any,
    funding_payload: Any,
    liquidation_payload: Any,
    long_short_payload: Any,
    generated_at: str | None = None,
) -> dict[str, Any]:
    market = normalize_symbol(raw_symbol)
    generated_at = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    diagnostics: list[dict[str, str]] = []
    contract_symbols = [contract.symbol for contract in contracts]

    ohlcv_by_symbol = _history_by_symbol(ohlcv_payload)
    primary = _primary_price_contract(contracts, ohlcv_by_symbol)
    price_rows = ohlcv_by_symbol.get(primary, {}) if primary else {}
    if not primary or not price_rows:
        diagnostics.append({"code": "no_price_data", "message": "No OHLCV data returned for the selected contracts."})

    oi_by_symbol = _history_by_symbol(oi_payload)
    funding_by_symbol = _history_by_symbol(funding_payload)
    liquidation_by_symbol = _history_by_symbol(liquidation_payload)
    ls_by_symbol = _history_by_symbol(long_short_payload)

    series: list[dict[str, Any]] = []
    for ts in sorted(price_rows):
        candle = price_rows[ts]
        oi_values = {
            symbol: _float((oi_by_symbol.get(symbol) or {}).get(ts, {}).get("c"))
            for symbol in contract_symbols
        }
        oi_values = {symbol: value for symbol, value in oi_values.items() if value > 0}
        total_oi = sum(oi_values.values())

        funding_values = {
            symbol: _float((funding_by_symbol.get(symbol) or {}).get(ts, {}).get("c"))
            for symbol in contract_symbols
            if ts in (funding_by_symbol.get(symbol) or {})
        }
        funding = _weighted_average(funding_values, oi_values)

        long_liq = 0.0
        short_liq = 0.0
        liq_contracts = 0
        for symbol in contract_symbols:
            liq_row = (liquidation_by_symbol.get(symbol) or {}).get(ts)
            if liq_row is None:
                continue
            liq_contracts += 1
            long_liq += _float(liq_row.get("l"))
            short_liq += _float(liq_row.get("s"))

        ls_longs = {}
        ls_shorts = {}
        for symbol in contract_symbols:
            ls_row = (ls_by_symbol.get(symbol) or {}).get(ts)
            if ls_row is None:
                continue
            long_pct = _float(ls_row.get("l"))
            short_pct = _float(ls_row.get("s"))
            if long_pct > 0 or short_pct > 0:
                ls_longs[symbol] = long_pct
                ls_shorts[symbol] = short_pct
        long_account = _weighted_average(ls_longs, oi_values)
        short_account = _weighted_average(ls_shorts, oi_values)
        ratio = long_account / short_account if short_account > 0 else _float((next(iter(ls_by_symbol.values()), {}) or {}).get(ts, {}).get("r"))

        row = {
            "timestamp": _timestamp_iso(ts),
            "open": _round(_float(candle.get("o"))),
            "high": _round(_float(candle.get("h"))),
            "low": _round(_float(candle.get("l"))),
            "close": _round(_float(candle.get("c"))),
            "volume": _round(_float(candle.get("v"))),
            "open_interest": _round(total_oi),
            "funding_rate": _round(funding, 10),
            "long_liquidations": _round(long_liq),
            "short_liquidations": _round(short_liq),
            "long_account_ratio": _round(long_account, 4),
            "short_account_ratio": _round(short_account, 4),
            "long_short_ratio": _round(ratio, 2),
            "contracts_total": len(contract_symbols),
            "oi_contracts": len(oi_values),
            "funding_contracts": len(funding_values),
            "liquidation_contracts": liq_contracts,
            "long_short_contracts": len(ls_longs),
        }
        series.append(row)

    if _has_reduced_coverage(series):
        diagnostics.append(
            {
                "code": "reduced_coverage",
                "message": "Some aggregate metrics use fewer contracts than the configured core set.",
            }
        )

    return {
        "base": market.base,
        "raw_symbol": raw_symbol,
        "timeframe": timeframe,
        "generated_at": generated_at,
        "contracts": [asdict(contract) for contract in contracts],
        "primary_price_contract": primary or "",
        "series": series,
        "diagnostics": diagnostics,
    }


def _entry_exchange_code(entry: dict[str, Any]) -> str:
    exchange = str(entry.get("exchange") or "").lower()
    symbol = str(entry.get("symbol") or "").lower()
    for code, hints in EXCHANGE_HINTS.items():
        if any(hint in exchange or symbol.endswith(hint) for hint in hints):
            return code
    return exchange.upper()


def _history_by_symbol(payload: Any) -> dict[str, dict[int, dict[str, Any]]]:
    result: dict[str, dict[int, dict[str, Any]]] = {}
    if not isinstance(payload, list):
        return result
    for series in payload:
        if not isinstance(series, dict):
            continue
        symbol = series.get("symbol")
        history = series.get("history") or []
        if not isinstance(symbol, str) or not isinstance(history, list):
            continue
        rows: dict[int, dict[str, Any]] = {}
        for row in history:
            if not isinstance(row, dict):
                continue
            ts = int(_float(row.get("t")))
            if ts > 0:
                rows[ts] = row
        result[symbol] = rows
    return result


def _primary_price_contract(contracts: list[Contract], ohlcv_by_symbol: dict[str, dict[int, dict[str, Any]]]) -> str:
    for contract in contracts:
        if contract.symbol in ohlcv_by_symbol and ohlcv_by_symbol[contract.symbol]:
            return contract.symbol
    return ""


def _weighted_average(values: dict[str, float], weights: dict[str, float]) -> float:
    clean_values = {key: value for key, value in values.items() if math.isfinite(value)}
    if not clean_values:
        return 0.0
    weight_total = sum(weights.get(key, 0.0) for key in clean_values)
    if weight_total > 0:
        return sum(value * weights.get(key, 0.0) for key, value in clean_values.items()) / weight_total
    return sum(clean_values.values()) / len(clean_values)


def _has_reduced_coverage(series: list[dict[str, Any]]) -> bool:
    for row in series:
        total = int(row.get("contracts_total") or 0)
        if total <= 0:
            continue
        for key in ("oi_contracts", "funding_contracts", "liquidation_contracts", "long_short_contracts"):
            if int(row.get(key) or 0) < total:
                return True
    return False


def _timestamp_iso(ts_s: int) -> str:
    return datetime.fromtimestamp(ts_s, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _float(value: object) -> float:
    try:
        if value is None or value == "":
            return 0.0
        parsed = float(value)
        return parsed if math.isfinite(parsed) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _round(value: float, ndigits: int = 6) -> float:
    return round(value, ndigits)
