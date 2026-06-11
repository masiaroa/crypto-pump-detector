"""Liquidation overlay helpers.

The dashboard only needs aggregated long/short nominal USD per bar, which
Coinalyze's free historical endpoint provides for all watchlist symbols. The
older WebSocket burst + JSONL store and the paid CoinGlass projected-map
fetcher were removed because they added ~800 LOC for features the static
dashboard does not render.

Public API:

- ``fetch_liquidation_map`` — DataFrame of canonical rows (one per side, per bar)
- ``fetch_liquidation_report`` — same rows plus a Coinalyze diagnostic for UI
- ``empty_liquidations`` — zero-row DataFrame in the canonical schema
- ``fetch_coinalyze_liquidations`` — direct Coinalyze fetch (used by tests)
"""

from .coinalyze import (
    CoinalyzeDiagnostic,
    coinalyze_symbol,
    fetch_coinalyze_liquidations,
    fetch_coinalyze_liquidations_batch,
    fetch_coinalyze_liquidations_with_diagnostic,
    parse_coinalyze_liquidations,
)
from .fetch import fetch_liquidation_map, fetch_liquidation_report
from .schema import LIQUIDATION_COLUMNS, empty_liquidations


__all__ = [
    "LIQUIDATION_COLUMNS",
    "CoinalyzeDiagnostic",
    "coinalyze_symbol",
    "empty_liquidations",
    "fetch_coinalyze_liquidations",
    "fetch_coinalyze_liquidations_batch",
    "fetch_coinalyze_liquidations_with_diagnostic",
    "fetch_liquidation_map",
    "fetch_liquidation_report",
    "parse_coinalyze_liquidations",
]
