"""Liquidation overlay helpers.

Public API (backwards compatible with the single-file module):

- ``fetch_liquidation_map`` — historical overlay rows
- ``fetch_liquidation_report`` — rows plus provider diagnostics for UI
- ``empty_liquidations`` — zero-row DataFrame in the canonical schema
- ``fetch_coinalyze_liquidations`` — historical executed liquidations
- ``parse_binance_force_orders`` — legacy REST parser (kept for tests / future)
- ``parse_coinglass_projected`` — optional paid/projected heatmap parser
- ``resolve_binance_force_order_market`` — symbol resolver for COIN-M vs USDM
- ``collect_executed_burst`` — optional debug WS burst writing to JSONL
"""

from .coinalyze import (
    CoinalyzeDiagnostic,
    coinalyze_symbol,
    fetch_coinalyze_liquidations,
    fetch_coinalyze_liquidations_with_diagnostic,
    parse_coinalyze_liquidations,
)
from .executed_store import parse_binance_force_orders, resolve_binance_force_order_market
from .executed_ws import collect_executed_burst, collect_symbol_burst
from .fetch import fetch_liquidation_map, fetch_liquidation_report
from .projected_coinglass import parse_coinglass_projected
from .schema import LIQUIDATION_COLUMNS, empty_liquidations


__all__ = [
    "LIQUIDATION_COLUMNS",
    "coinalyze_symbol",
    "collect_executed_burst",
    "collect_symbol_burst",
    "CoinalyzeDiagnostic",
    "empty_liquidations",
    "fetch_coinalyze_liquidations",
    "fetch_coinalyze_liquidations_with_diagnostic",
    "fetch_liquidation_map",
    "fetch_liquidation_report",
    "parse_binance_force_orders",
    "parse_coinalyze_liquidations",
    "parse_coinglass_projected",
    "resolve_binance_force_order_market",
]
