from __future__ import annotations

from dataclasses import dataclass
import re


SUPPORTED_EXCHANGES = {"BYBIT", "BINANCE", "BITGET", "OKX"}


@dataclass(frozen=True)
class MarketSymbol:
    raw: str
    exchange: str
    ticker: str
    base: str
    quote: str
    contract_type: str
    api_symbol: str
    supported: bool


def normalize_symbol(raw: str) -> MarketSymbol:
    exchange, ticker = _split_symbol(raw)
    clean = ticker.replace(".P", "")
    contract_type = "perp" if ticker.endswith(".P") else "spot_or_index"
    base, quote = _split_base_quote(clean)
    supported = exchange in SUPPORTED_EXCHANGES and contract_type == "perp" and quote in {"USDT", "USD"}
    api_symbol = f"{base}{quote}" if base and quote else clean
    return MarketSymbol(
        raw=raw,
        exchange=exchange,
        ticker=ticker,
        base=base,
        quote=quote,
        contract_type=contract_type,
        api_symbol=api_symbol,
        supported=supported,
    )


def _split_symbol(raw: str) -> tuple[str, str]:
    if ":" not in raw:
        return "", raw.upper()
    exchange, ticker = raw.split(":", 1)
    return exchange.upper(), ticker.upper()


def _split_base_quote(ticker: str) -> tuple[str, str]:
    match = re.match(r"^(.+?)(USDT|USDC|BUSD|USD)$", ticker.upper())
    if not match:
        return ticker.upper(), ""
    return match.group(1), match.group(2)
