"""BitUnix symbol translation — single source of truth.

The internal canonical symbol (used in `ProposedOrder`, paper-trade records,
and strategy code) is BitUnix's perp *display* form, e.g. ``"BTC/USDT.P"``.
The BitUnix REST API wants the *wire* form, e.g. ``"BTCUSDT"``. This module
is the ONLY place that translation is allowed to happen — there is no ad-hoc
string slicing of symbols in broker or strategy code.

**Strict map, fail-closed.** An unmapped symbol raises `UnknownSymbolError`
rather than being guessed at via string surgery. A new trade symbol (the
SOL/ETH/XRP roadmap) MUST be added to `_INTERNAL_TO_WIRE` before it can be
traded — so an unmapped symbol can never be silently transformed and sent
to the exchange.
"""
from __future__ import annotations


class UnknownSymbolError(ValueError):
    """Raised when a symbol has no mapping in either direction.

    Fail-closed: callers must not fall back to string manipulation. Add the
    symbol to `_INTERNAL_TO_WIRE` instead.
    """


# Internal canonical (ProposedOrder.symbol) -> BitUnix REST wire form.
# Pre-populated with BTC only; add SOL/ETH/XRP entries when those strategies
# come online (see runbooks/2026-05-29_bitunix_live_readiness_audit.md).
_INTERNAL_TO_WIRE: dict[str, str] = {
    "BTC/USDT.P": "BTCUSDT",
    # SOL/ETH/XRP added 2026-06-25 for the bitunix_sfp roadmap: today these are
    # RECORD-ONLY (4-coin 15m+3m bar capture) and reserved for future SFP
    # trading — each must be backtested before it enters a division's tradable
    # `symbols:` list. Capture ≠ trade.
    "SOL/USDT.P": "SOLUSDT",
    "ETH/USDT.P": "ETHUSDT",
    "XRP/USDT.P": "XRPUSDT",
}

_WIRE_TO_INTERNAL: dict[str, str] = {
    wire: internal for internal, wire in _INTERNAL_TO_WIRE.items()
}


def to_wire_format(internal: str) -> str:
    """Map an internal canonical symbol to the BitUnix REST wire form.

    Raises `UnknownSymbolError` if the symbol is not in the map.
    """
    try:
        return _INTERNAL_TO_WIRE[internal]
    except KeyError:
        raise UnknownSymbolError(
            f"no BitUnix wire mapping for internal symbol {internal!r}; "
            f"add it to _INTERNAL_TO_WIRE in bitunix_symbols.py before trading "
            f"it (known: {sorted(_INTERNAL_TO_WIRE)})"
        ) from None


def to_internal_format(wire: str) -> str:
    """Map a BitUnix REST wire symbol back to the internal canonical form.

    Raises `UnknownSymbolError` if the symbol is not in the map.
    """
    try:
        return _WIRE_TO_INTERNAL[wire]
    except KeyError:
        raise UnknownSymbolError(
            f"no internal mapping for BitUnix wire symbol {wire!r}; "
            f"add it to _INTERNAL_TO_WIRE in bitunix_symbols.py "
            f"(known: {sorted(_WIRE_TO_INTERNAL)})"
        ) from None
