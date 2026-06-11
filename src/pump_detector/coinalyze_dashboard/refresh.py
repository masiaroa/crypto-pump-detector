from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from pump_detector.symbols import normalize_symbol

from .client import CoinalyzeApiError, CoinalyzeClient
from .data import (
    DEFAULT_CORE_EXCHANGES,
    aggregate_dashboard_series,
    cache_file_for,
    interval_for_timeframe,
    lookback_seconds,
    select_core_contracts,
)


DEFAULT_SETTINGS = {
    "enabled": True,
    "timeframes": ["4h", "1d"],
    "cache_dir": "data/coinalyze",
    "max_age_hours": 6,
    "core_exchanges": DEFAULT_CORE_EXCHANGES,
}


def refresh_watchlist(
    watchlist: list[str],
    *,
    settings: dict[str, Any] | None = None,
    api_key: str | None = None,
    session: requests.Session | None = None,
    now_s: int | None = None,
    force: bool = False,
    sleep_fn=None,
) -> dict[str, Any]:
    cfg = DEFAULT_SETTINGS | dict(settings or {})
    cache_dir = Path(cfg["cache_dir"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    now_s = int(now_s if now_s is not None else datetime.now(timezone.utc).timestamp())
    generated_at = _iso(now_s)

    manifest: dict[str, Any] = {
        "generated_at": generated_at,
        "enabled": bool(cfg.get("enabled", True)),
        "cache_dir": str(cache_dir),
        "symbols": [],
        "diagnostics": [],
    }
    if not cfg.get("enabled", True):
        manifest["diagnostics"].append({"code": "disabled", "message": "Coinalyze dashboard refresh is disabled."})
        _write_manifest(cache_dir, manifest)
        return manifest

    timeframes = list(cfg.get("timeframes") or ["4h", "1d"])
    max_age_hours = float(cfg.get("max_age_hours", 6))
    core_exchanges = list(cfg.get("core_exchanges") or DEFAULT_CORE_EXCHANGES)
    api_key = (api_key or os.environ.get("COINALYZE_API_KEY", "")).strip()

    work: list[tuple[str, str, Path]] = []
    for raw_symbol in watchlist:
        market = normalize_symbol(raw_symbol)
        if not market.supported or not market.base:
            continue
        for timeframe in timeframes:
            path = cache_file_for(cache_dir, market.base, timeframe)
            if not force and _is_cache_fresh(path, now_s, max_age_hours):
                manifest["symbols"].append(
                    {
                        "base": market.base,
                        "raw_symbol": raw_symbol,
                        "timeframe": timeframe,
                        "status": "cached",
                        "path": str(path),
                    }
                )
                continue
            work.append((raw_symbol, timeframe, path))

    if not work:
        _write_manifest(cache_dir, manifest)
        return manifest

    if not api_key:
        manifest["diagnostics"].append({"code": "missing_key", "message": "COINALYZE_API_KEY is required to refresh."})
        for raw_symbol, timeframe, path in work:
            market = normalize_symbol(raw_symbol)
            manifest["symbols"].append(
                {
                    "base": market.base,
                    "raw_symbol": raw_symbol,
                    "timeframe": timeframe,
                    "status": "missing_key",
                    "path": str(path),
                }
            )
        _write_manifest(cache_dir, manifest)
        return manifest

    client = CoinalyzeClient(api_key=api_key, session=session, sleep_fn=sleep_fn)
    try:
        markets = client.get("/future-markets")
    except Exception as exc:  # noqa: BLE001
        manifest["diagnostics"].append({"code": "markets_error", "message": str(exc)})
        _write_manifest(cache_dir, manifest)
        return manifest

    for raw_symbol, timeframe, path in work:
        market = normalize_symbol(raw_symbol)
        try:
            contracts = select_core_contracts(raw_symbol, markets, core_exchanges)
            if not contracts:
                raise CoinalyzeApiError("No Coinalyze core contracts resolved.")
            snapshot = _fetch_snapshot(
                client=client,
                raw_symbol=raw_symbol,
                timeframe=timeframe,
                contracts=contracts,
                now_s=now_s,
                generated_at=generated_at,
            )
            path.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            manifest["symbols"].append(
                {
                    "base": market.base,
                    "raw_symbol": raw_symbol,
                    "timeframe": timeframe,
                    "status": "refreshed",
                    "path": str(path),
                    "rows": len(snapshot.get("series") or []),
                    "contracts": len(contracts),
                }
            )
        except Exception as exc:  # noqa: BLE001
            manifest["symbols"].append(
                {
                    "base": market.base,
                    "raw_symbol": raw_symbol,
                    "timeframe": timeframe,
                    "status": "error",
                    "path": str(path),
                    "message": str(exc),
                }
            )

    _write_manifest(cache_dir, manifest)
    return manifest


def _fetch_snapshot(*, client: CoinalyzeClient, raw_symbol: str, timeframe: str, contracts, now_s: int, generated_at: str) -> dict[str, Any]:
    interval = interval_for_timeframe(timeframe)
    from_s = now_s - lookback_seconds(timeframe)
    all_symbols = ",".join(contract.symbol for contract in contracts)
    price_symbol = contracts[0].symbol
    ls_symbols = ",".join(contract.symbol for contract in contracts if contract.has_long_short_ratio_data) or all_symbols
    common = {"interval": interval, "from": from_s, "to": now_s}

    ohlcv_payload = client.get("/ohlcv-history", {"symbols": price_symbol, **common})
    oi_payload = client.get("/open-interest-history", {"symbols": all_symbols, "convert_to_usd": "true", **common})
    funding_payload = client.get("/funding-rate-history", {"symbols": all_symbols, **common})
    liquidation_payload = client.get("/liquidation-history", {"symbols": all_symbols, "convert_to_usd": "true", **common})
    long_short_payload = client.get("/long-short-ratio-history", {"symbols": ls_symbols, **common})

    return aggregate_dashboard_series(
        raw_symbol=raw_symbol,
        timeframe=timeframe,
        contracts=contracts,
        ohlcv_payload=ohlcv_payload,
        oi_payload=oi_payload,
        funding_payload=funding_payload,
        liquidation_payload=liquidation_payload,
        long_short_payload=long_short_payload,
        generated_at=generated_at,
    )


def _is_cache_fresh(path: Path, now_s: int, max_age_hours: float) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        generated_at = str(payload.get("generated_at") or "")
        generated_s = _parse_iso(generated_at)
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    age_hours = (now_s - generated_s) / 3600
    return age_hours <= max_age_hours


def _write_manifest(cache_dir: Path, manifest: dict[str, Any]) -> None:
    (cache_dir / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _iso(ts_s: int) -> str:
    return datetime.fromtimestamp(ts_s, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> int:
    normalized = value.replace("Z", "+00:00")
    return int(datetime.fromisoformat(normalized).timestamp())
