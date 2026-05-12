"""Public WebSocket collector for executed liquidations.

Listens to Binance / Bybit / OKX public force-order streams for a short
burst, normalises events to the project schema, and appends them to a
JSONL store. No API keys, no auth.

Failures in any exchange are swallowed — the collector returns what it
managed to capture. The scan must keep working with an empty result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from .executed_store import append_records, per_exchange_symbols, prune
from .schema import to_float


log = logging.getLogger(__name__)


BINANCE_USDM_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
BINANCE_COINM_URL = "wss://dstream.binance.com/ws/!forceOrder@arr"
BYBIT_LINEAR_URL = "wss://stream.bybit.com/v5/public/linear"
OKX_PUBLIC_URL = "wss://ws.okx.com:8443/ws/v5/public"


SideMapper = Callable[[str], str]


def _binance_side(s: str) -> str:
    s = s.upper()
    if s == "SELL":
        return "long"
    if s == "BUY":
        return "short"
    return "unknown"


def _bybit_side(s: str) -> str:
    s = s.lower()
    if s in {"sell", "short"}:
        return "long"
    if s in {"buy", "long"}:
        return "short"
    return "unknown"


def _okx_side(s: str) -> str:
    s = s.lower()
    if s == "sell":
        return "long"
    if s == "buy":
        return "short"
    return "unknown"


def parse_binance_ws(msg: dict[str, Any]) -> dict[str, Any] | None:
    order = msg.get("o") or {}
    if not order:
        return None
    price = to_float(order.get("ap") or order.get("p"))
    qty = to_float(order.get("z") or order.get("q"))
    ts_ms = int(to_float(order.get("T") or msg.get("E")))
    if price <= 0 or qty <= 0 or ts_ms <= 0:
        return None
    return {
        "timestamp_ms": ts_ms,
        "price": price,
        "quantity": qty,
        "notional": price * qty,
        "side": _binance_side(str(order.get("S", ""))),
        "kind": "executed",
        "source": "binance_ws",
        "symbol": str(order.get("s", "")),
    }


def parse_bybit_ws(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Bybit v5 allLiquidation pushes a list per symbol."""
    out: list[dict[str, Any]] = []
    data = msg.get("data")
    if not data:
        return out
    items = data if isinstance(data, list) else [data]
    for item in items:
        price = to_float(item.get("price") or item.get("p"))
        qty = to_float(item.get("size") or item.get("v") or item.get("q"))
        ts_ms = int(to_float(item.get("updatedTime") or item.get("T") or msg.get("ts")))
        if price <= 0 or qty <= 0 or ts_ms <= 0:
            continue
        out.append(
            {
                "timestamp_ms": ts_ms,
                "price": price,
                "quantity": qty,
                "notional": price * qty,
                "side": _bybit_side(str(item.get("side") or item.get("S", ""))),
                "kind": "executed",
                "source": "bybit_ws",
                "symbol": str(item.get("symbol") or item.get("s", "")),
            }
        )
    return out


def parse_okx_ws(msg: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in msg.get("data") or []:
        inst = str(entry.get("instId", ""))
        for detail in entry.get("details") or []:
            price = to_float(detail.get("bkPx"))
            qty = to_float(detail.get("sz"))
            ts_ms = int(to_float(detail.get("ts")))
            if price <= 0 or qty <= 0 or ts_ms <= 0:
                continue
            out.append(
                {
                    "timestamp_ms": ts_ms,
                    "price": price,
                    "quantity": qty,
                    "notional": price * qty,
                    "side": _okx_side(str(detail.get("side", ""))),
                    "kind": "executed",
                    "source": "okx_ws",
                    "symbol": inst,
                }
            )
    return out


async def _consume_binance(url: str, sink: list[dict[str, Any]]) -> None:
    import websockets

    async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            rec = parse_binance_ws(msg)
            if rec is not None:
                sink.append(rec)


async def _consume_bybit_symbol(symbol: str, sink: list[dict[str, Any]]) -> None:
    import websockets

    async with websockets.connect(BYBIT_LINEAR_URL, ping_interval=20, ping_timeout=20) as ws:
        await ws.send(json.dumps({"op": "subscribe", "args": [f"allLiquidation.{symbol}"]}))
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not msg.get("topic", "").startswith("allLiquidation"):
                continue
            sink.extend(parse_bybit_ws(msg))


async def _consume_okx_symbol(inst_id: str, sink: list[dict[str, Any]]) -> None:
    """OKX does not expose per-symbol liquidation channels; we subscribe to the
    SWAP firehose and filter client-side.
    """
    import websockets

    async with websockets.connect(OKX_PUBLIC_URL, ping_interval=20, ping_timeout=20) as ws:
        await ws.send(
            json.dumps(
                {
                    "op": "subscribe",
                    "args": [{"channel": "liquidation-orders", "instType": "SWAP"}],
                }
            )
        )
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("event") in {"subscribe", "error"}:
                continue
            for rec in parse_okx_ws(msg):
                if rec.get("symbol") == inst_id:
                    sink.append(rec)


async def _consume_bybit(sink: list[dict[str, Any]]) -> None:
    import websockets

    async with websockets.connect(BYBIT_LINEAR_URL, ping_interval=20, ping_timeout=20) as ws:
        await ws.send(json.dumps({"op": "subscribe", "args": ["allLiquidation.linear"]}))
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not msg.get("topic", "").startswith("allLiquidation"):
                continue
            sink.extend(parse_bybit_ws(msg))


async def _consume_okx(sink: list[dict[str, Any]]) -> None:
    import websockets

    async with websockets.connect(OKX_PUBLIC_URL, ping_interval=20, ping_timeout=20) as ws:
        await ws.send(
            json.dumps(
                {
                    "op": "subscribe",
                    "args": [{"channel": "liquidation-orders", "instType": "SWAP"}],
                }
            )
        )
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("event") in {"subscribe", "error"}:
                continue
            sink.extend(parse_okx_ws(msg))


async def _run_with_timeout(coro: Awaitable[None], duration_s: float, label: str) -> None:
    try:
        await asyncio.wait_for(coro, timeout=duration_s)
    except asyncio.TimeoutError:
        pass  # expected: timeout ends the burst
    except Exception as exc:  # noqa: BLE001 - tolerate any provider failure
        log.debug("liquidation WS %s failed: %s", label, exc)


async def _collect_async(
    duration_s: float, exchanges: list[str]
) -> list[dict[str, Any]]:
    sink: list[dict[str, Any]] = []
    tasks: list[asyncio.Task[None]] = []
    if "binance_ws" in exchanges:
        tasks.append(asyncio.create_task(_run_with_timeout(_consume_binance(BINANCE_USDM_URL, sink), duration_s, "binance_usdm")))
        tasks.append(asyncio.create_task(_run_with_timeout(_consume_binance(BINANCE_COINM_URL, sink), duration_s, "binance_coinm")))
    if "bybit_ws" in exchanges:
        tasks.append(asyncio.create_task(_run_with_timeout(_consume_bybit(sink), duration_s, "bybit")))
    if "okx_ws" in exchanges:
        tasks.append(asyncio.create_task(_run_with_timeout(_consume_okx(sink), duration_s, "okx")))
    if not tasks:
        return sink
    # Hard safety net: even if a provider swallows cancellation, the outer
    # wait_for forces the whole gather to terminate.
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=duration_s + 5,
        )
    except asyncio.TimeoutError:
        for t in tasks:
            t.cancel()
    return sink


def collect_executed_burst(
    duration_s: float,
    exchanges: list[str],
    out_path: Path,
    *,
    max_age_days: int = 14,
    progress: bool = True,
) -> int:
    """Run a synchronous burst collection and append to JSONL.

    Safe to call from non-async code. Returns the number of records written.
    Never raises on collector failures — it just returns 0.

    Prints a single progress line so users running the scan don't think it
    hung during the silent WS wait.
    """
    if duration_s <= 0 or not exchanges:
        return 0
    if progress:
        print(
            f"[liquidations] burst {int(duration_s)}s on {','.join(exchanges)} "
            f"-> {out_path}",
            flush=True,
        )
    try:
        records = asyncio.run(_collect_async(duration_s, list(exchanges)))
    except Exception as exc:  # noqa: BLE001
        log.debug("liquidation burst aborted: %s", exc)
        return 0
    out_path = Path(out_path)
    try:
        prune(out_path, max_age_days=max_age_days)
    except Exception as exc:  # noqa: BLE001
        log.debug("liquidation prune failed: %s", exc)
    if not records:
        if progress:
            print("[liquidations] burst captured 0 records", flush=True)
        return 0
    try:
        written = append_records(out_path, records)
    except Exception as exc:  # noqa: BLE001
        log.debug("liquidation append failed: %s", exc)
        return 0
    if progress:
        print(f"[liquidations] burst captured {written} records", flush=True)
    return written


async def _collect_symbol_async(
    raw_symbol: str, duration_s: float
) -> list[dict[str, Any]]:
    sink: list[dict[str, Any]] = []
    forms = per_exchange_symbols(raw_symbol)
    if not forms:
        return sink
    tasks: list[asyncio.Task[None]] = []
    if "binance" in forms:
        url = f"wss://fstream.binance.com/ws/{forms['binance'].lower()}@forceOrder"
        tasks.append(asyncio.create_task(_run_with_timeout(_consume_binance(url, sink), duration_s, "binance_usdm_sym")))
    if "binance_coinm" in forms:
        url = f"wss://dstream.binance.com/ws/{forms['binance_coinm'].lower()}@forceOrder"
        tasks.append(asyncio.create_task(_run_with_timeout(_consume_binance(url, sink), duration_s, "binance_coinm_sym")))
    if "bybit" in forms:
        tasks.append(asyncio.create_task(_run_with_timeout(_consume_bybit_symbol(forms["bybit"], sink), duration_s, "bybit_sym")))
    if "okx" in forms:
        tasks.append(asyncio.create_task(_run_with_timeout(_consume_okx_symbol(forms["okx"], sink), duration_s, "okx_sym")))
    if not tasks:
        return sink
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=duration_s + 5,
        )
    except asyncio.TimeoutError:
        for t in tasks:
            t.cancel()
    return sink


def collect_symbol_burst(
    raw_symbol: str,
    duration_s: float,
    out_path: Path,
    *,
    max_age_days: int = 14,
) -> int:
    """Focused per-symbol burst: subscribe only to streams for ``raw_symbol``.

    Much more efficient for sporadic coins than the global firehose because
    quiet altcoins can be invisible for minutes in the global stream but a
    per-symbol subscription guarantees we hear every single liquidation.

    Returns the number of records appended to the JSONL store.
    """
    if duration_s <= 0:
        return 0
    out_path = Path(out_path)
    try:
        records = asyncio.run(_collect_symbol_async(raw_symbol, duration_s))
    except Exception as exc:  # noqa: BLE001
        log.debug("symbol burst aborted for %s: %s", raw_symbol, exc)
        return 0
    try:
        prune(out_path, max_age_days=max_age_days)
    except Exception:
        pass
    if not records:
        return 0
    try:
        return append_records(out_path, records)
    except Exception as exc:  # noqa: BLE001
        log.debug("symbol burst append failed: %s", exc)
        return 0


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Public WS liquidation burst collector")
    parser.add_argument("--duration", type=float, default=60.0, help="Burst seconds")
    parser.add_argument(
        "--out",
        default="data/liquidations/_ws_history.jsonl",
        help="JSONL output path",
    )
    parser.add_argument(
        "--exchanges",
        default="binance_ws,bybit_ws,okx_ws",
        help="Comma-separated list",
    )
    parser.add_argument("--max-age-days", type=int, default=14)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    written = collect_executed_burst(
        duration_s=args.duration,
        exchanges=[e.strip() for e in args.exchanges.split(",") if e.strip()],
        out_path=Path(args.out),
        max_age_days=args.max_age_days,
    )
    print(f"wrote {written} liquidations to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
