"""Shard-aware Kalshi balance read -- Prediction Markets shard money-mgmt RUNG 1 (Option B, 2026-08-30).

★ LOAD-BEARING, and it comes FIRST. Kalshi shards collateral by `exchange_index` (0 Default, 1 Combos, 2 Crypto,
3 Tennis & Baseball). `GET /portfolio/balance` returns a `balance_breakdown` array with the PER-SHARD split -- but
the platform's existing preflight reads only `bal.balance / 100`, the MASKED TOTAL (brokers/kalshi_live.py:278),
and IGNORES the breakdown. A healthy total with an empty market-shard is exactly the state that silently killed the
legacy poly_kalshi_mlb copy division for two days: an MLB order auto-routes to shard 3, finds ~$2 there and 400s,
while the ~$515 total looks fine. This module parses the breakdown so per-shard funding is VISIBLE.

Pure-stdlib. It imports NOTHING from the order path (execution / kalshi_live / live_driver). `parse_balance` is
pure and fully unit-tested; `fetch_shard_balances` is a thin async wrapper over a raw client
`.get('/portfolio/balance')` -- the same raw call the R7 probes proved returns the breakdown (pykalshi's typed
`portfolio.get_balance()` exposes only `.balance`).

★ DESIGN: FAIL LOUD ON CORRUPTION, `None` ONLY FOR THE LEGITIMATE "NO BREAKDOWN". A load-bearing funding read must
never emit a silently-wrong per-shard picture, so ANY ambiguity in the breakdown RAISES (non-list, non-dict entry,
non-integer / duplicate exchange_index, missing / non-finite balance). The ONLY soft signal is `has_breakdown=False`
-- the breakdown was legitimately ABSENT (a subaccount-restricted key omits it) -> the split is UNKNOWN, and
`shard()` / `can_fund()` return **None**.

★ CALLER CONTRACT (for rung 2's chokepoint guard): `can_fund` is a TRI-STATE `True | False | None`. The SAFE gate
is `if can_fund(shard, need) is not True: skip` -- treat BOTH False (too thin) and None (unknown) as "do NOT place."
`None` is falsy, so `if can_fund(...)` happens to skip too, but write `is not True` so the intent survives a reader.
NEVER coerce `None` to fundable.
"""
from __future__ import annotations

import inspect
import math
from dataclasses import dataclass

_CENTS_PER_DOLLAR = 100.0
_BALANCE_PATH = "/portfolio/balance"


def _to_dollars_float(v):
    """A Kalshi money value -> FINITE float dollars, or None if absent/unparseable/non-finite. `balance_breakdown`
    balances and `balance_dollars` are FIXED-POINT DOLLAR STRINGS ('509.8040') -- this does NOT divide by 100 (only
    the integer-cents `balance` field does, handled explicitly in parse_balance). ★ Rejects NaN/Infinity: `float()`
    accepts 'NaN'/'Infinity', and an infinite shard balance would make `can_fund` return True for ANY need -- so a
    non-finite value is returned as None (which then RAISES for a breakdown entry, and falls through for the total)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


@dataclass(frozen=True)
class ShardBalances:
    total_dollars: float            # the masked TOTAL (dollars) -- what the old reader returned
    by_shard: dict                  # {exchange_index:int -> dollars:float}; EMPTY when has_breakdown is False
    has_breakdown: bool             # False = split UNKNOWN (subaccount-restricted key / absent) -> caller fail-safes
    updated_ts: int | None = None

    def shard(self, exchange_index: int):
        """Dollars on ONE shard, or None if the split is UNKNOWN (has_breakdown False). A shard absent from a KNOWN
        breakdown is $0.0 -- Kalshi lists every shard, so absence means empty, not unknown (this ASSUMPTION is
        pinned by test_shard_absent_from_known_breakdown_is_zero)."""
        if not self.has_breakdown:
            return None
        return self.by_shard.get(int(exchange_index), 0.0)

    def can_fund(self, exchange_index: int, need_dollars: float):
        """Tri-state: True/False if THIS shard can fund `need_dollars`; None if the split is UNKNOWN. ★ None means
        CANNOT VERIFY -> caller MUST fail-safe (`is not True` -> skip); never coerce None to True. RAISES on a
        NEGATIVE need (a negative order size is an upstream sign bug and must be loud, not silently "fundable"); a
        zero need is degenerate-but-harmless (always fundable)."""
        nd = float(need_dollars)
        if nd < 0.0:
            raise ValueError("need_dollars must be non-negative; got %r" % (need_dollars,))
        s = self.shard(exchange_index)
        if s is None:
            return None
        return s + 1e-9 >= nd

    def shard_sum(self):
        """Sum of the per-shard balances (dollars), or None if the split is unknown. A large gap vs total_dollars
        can flag a subaccount-scoped read; callers may sanity-check the two."""
        if not self.has_breakdown:
            return None
        return sum(self.by_shard.values())


def parse_balance(resp: dict) -> ShardBalances:
    """Parse a `GET /portfolio/balance` response into a ShardBalances. FAIL-LOUD on any breakdown corruption (see
    module docstring): a non-dict response, a non-list breakdown, a non-dict entry, a non-integer or DUPLICATE
    exchange_index, or a missing / non-finite balance all RAISE (ValueError / TypeError). An ABSENT or EMPTY
    breakdown is NOT an error: it is the subaccount-restricted case -> has_breakdown=False (split unknown)."""
    if resp is not None and not isinstance(resp, dict):
        raise TypeError("balance response is not a dict: %r" % (type(resp),))
    resp = resp or {}
    # total: prefer `balance_dollars` (fixed-point string, $0.0001 precision); else `balance` (integer cents)
    total = _to_dollars_float(resp.get("balance_dollars"))
    if total is None:
        cents = _to_dollars_float(resp.get("balance"))
        total = (cents / _CENTS_PER_DOLLAR) if cents is not None else 0.0
    updated = resp.get("updated_ts")
    try:
        updated = int(updated) if updated is not None else None
    except (TypeError, ValueError):
        updated = None
    bd = resp.get("balance_breakdown")
    if bd is not None and not isinstance(bd, list):
        raise ValueError("balance_breakdown is not a list: %r" % (type(bd),))
    by_shard: dict = {}
    has = False
    if isinstance(bd, list) and bd:
        has = True
        for item in bd:
            if not isinstance(item, dict):
                raise ValueError("balance_breakdown entry is not a dict: %r" % (item,))
            idx = item.get("exchange_index")
            bal = _to_dollars_float(item.get("balance"))
            if idx is None or bal is None:
                raise ValueError("balance_breakdown entry missing/non-finite exchange_index or balance: %r" % (item,))
            if not isinstance(idx, int) or isinstance(idx, bool):
                raise ValueError("exchange_index is not an integer: %r" % (idx,))   # int(3.7)->3 would be a WRONG shard
            if idx in by_shard:
                raise ValueError("duplicate exchange_index %d in balance_breakdown" % idx)
            by_shard[idx] = bal
    return ShardBalances(total_dollars=total, by_shard=by_shard, has_breakdown=has, updated_ts=updated)


async def fetch_shard_balances(client) -> ShardBalances:
    """READ per-shard balances via a raw client `.get('/portfolio/balance')` -- the same raw call the R7 probes
    proved returns `balance_breakdown` (pykalshi's typed `portfolio.get_balance()` exposes only `.balance`).
    `client` is the object `KalshiLiveBroker._client()` returns (duck-typed: has a `.get`). Awaits a coroutine
    result. Parsing + validation is delegated to `parse_balance` (so a non-dict client result RAISES TypeError
    loudly rather than mis-parsing a raw HTTP response)."""
    r = client.get(_BALANCE_PATH)
    if inspect.isawaitable(r):
        r = await r
    return parse_balance(r)
