from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from ..symbols import normalize_symbol
from .schema import LIQUIDATION_COLUMNS, empty_liquidations, lookback_ms, to_float


def resolve_binance_force_order_market(raw_symbol: str) -> tuple[str, str] | None:
    market = normalize_symbol(raw_symbol)
    if market.contract_type != "perp" or not market.base:
        return None
    if market.quote == "USDT":
        return "usdm", f"{market.base}USDT"
    if market.quote == "USD":
        return "coinm", f"{market.base}USD_PERP"
    return None


def parse_binance_force_orders(payload: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in payload:
        price = to_float(item.get("averagePrice") or item.get("avgPrice") or item.get("price"))
        quantity = to_float(item.get("executedQty") or item.get("origQty"))
        timestamp = pd.to_datetime(item.get("time"), unit="ms", utc=True, errors="coerce")
        if pd.isna(timestamp) or price <= 0 or quantity <= 0:
            continue
        rows.append(
            {
                "timestamp": timestamp,
                "price": price,
                "quantity": quantity,
                "notional": price * quantity,
                "side": _binance_liquidated_side(str(item.get("side", ""))),
                "kind": "executed",
                "source": "binance",
            }
        )
    if not rows:
        return empty_liquidations()
    return (
        pd.DataFrame(rows, columns=LIQUIDATION_COLUMNS)
        .sort_values("timestamp")
        .reset_index(drop=True)
    )


def _binance_liquidated_side(side: str) -> str:
    side = side.upper()
    if side == "SELL":
        return "long"
    if side == "BUY":
        return "short"
    return "unknown"


def per_exchange_symbols(raw_symbol: str) -> dict[str, str]:
    """Return the symbol form each exchange uses for per-symbol WS subscriptions.

    Returns {} when the symbol cannot be represented as a USDT/USD perp.
    """
    market = normalize_symbol(raw_symbol)
    if not market.base or market.contract_type != "perp":
        return {}
    base = market.base
    quote = market.quote or ""
    if quote == "USDT":
        return {
            "binance": f"{base}USDT",
            "bybit": f"{base}USDT",
            "okx": f"{base}-USDT-SWAP",
        }
    if quote == "USD":
        # COIN-M / inverse perps live on the dapi host on Binance
        return {
            "binance_coinm": f"{base}USD_PERP",
        }
    return {}


def canonical_symbols(raw_symbol: str) -> set[str]:
    """All representations of a symbol that a WS event might carry.

    Used to filter the JSONL store. We keep the comparison loose so a record
    written with the Bybit/OKX convention still matches a watchlist entry like
    `BINANCE:BTCUSDT.P`.
    """
    market = normalize_symbol(raw_symbol)
    forms = {raw_symbol}
    if ":" in raw_symbol:
        forms.add(raw_symbol.split(":", 1)[1])
    if not market.base:
        return forms
    base = market.base
    quote = market.quote or ""
    if quote == "USDT":
        forms.update({f"{base}USDT", f"{base}-USDT-SWAP", f"{base}USDT.P"})
    if quote == "USD":
        forms.update({f"{base}USD_PERP", f"{base}USD-PERP", f"{base}USD.P"})
    return forms


def append_records(path: Path, records: Iterable[dict[str, Any]]) -> int:
    """Append liquidation records to the JSONL store. Returns rows written.

    Uses fcntl.flock when available so concurrent burst + Streamlit reads do
    not corrupt lines. Records must already be JSON-serialisable.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        import fcntl  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - non-POSIX fallback
        fcntl = None  # type: ignore[assignment]
    with path.open("a", encoding="utf-8") as fh:
        if fcntl is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass
        for rec in records:
            fh.write(json.dumps(rec, separators=(",", ":")))
            fh.write("\n")
            written += 1
        if fcntl is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    return written


def read_recent(
    path: Path,
    raw_symbol: str,
    timeframe: str,
    *,
    now_ms: int | None = None,
) -> pd.DataFrame:
    """Read the JSONL store and return executed liquidations for one symbol."""
    path = Path(path)
    if not path.exists():
        return empty_liquidations()
    forms = canonical_symbols(raw_symbol)
    end_ms = now_ms or int(pd.Timestamp.utcnow().timestamp() * 1000)
    start_ms = end_ms - lookback_ms(timeframe)
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("symbol") not in forms:
                    continue
                ts_ms = int(to_float(rec.get("timestamp_ms") or rec.get("timestamp")))
                if ts_ms < start_ms or ts_ms > end_ms:
                    continue
                rows.append(_normalize_stored_record(rec, ts_ms))
    except OSError:
        return empty_liquidations()
    if not rows:
        return empty_liquidations()
    return (
        pd.DataFrame(rows, columns=LIQUIDATION_COLUMNS)
        .sort_values("timestamp")
        .reset_index(drop=True)
    )


def _normalize_stored_record(rec: dict[str, Any], ts_ms: int) -> dict[str, Any]:
    price = to_float(rec.get("price"))
    quantity = to_float(rec.get("quantity"))
    notional = to_float(rec.get("notional")) or price * quantity
    return {
        "timestamp": pd.to_datetime(ts_ms, unit="ms", utc=True),
        "price": price,
        "quantity": quantity,
        "notional": notional,
        "side": str(rec.get("side") or "unknown"),
        "kind": "executed",
        "source": str(rec.get("source") or "ws"),
    }


def prune(path: Path, *, max_age_days: int = 14, now_ms: int | None = None) -> int:
    """Drop lines older than max_age_days. Returns kept-line count."""
    path = Path(path)
    if not path.exists():
        return 0
    end_ms = now_ms or int(pd.Timestamp.utcnow().timestamp() * 1000)
    cutoff_ms = end_ms - max_age_days * 24 * 60 * 60 * 1000
    tmp = path.with_suffix(path.suffix + ".tmp")
    kept = 0
    with path.open("r", encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as dst:
        for line in src:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rec = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            ts_ms = int(to_float(rec.get("timestamp_ms") or rec.get("timestamp")))
            if ts_ms < cutoff_ms:
                continue
            dst.write(stripped + "\n")
            kept += 1
    os.replace(tmp, path)
    return kept
