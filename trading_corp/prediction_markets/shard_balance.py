"""Shard-aware Kalshi balance read -- Prediction Markets shard money-mgmt RUNG 1 (Option B, 2026-08-30).

★ LOAD-BEARING, and it comes FIRST. Kalshi shards collateral by `exchange_index` (0 Default, 1 Combos, 2 Crypto,
3 Tennis & Baseball). `GET /portfolio/balance` returns a `balance_breakdown` array with the PER-SHARD split -- but
the platform's existing preflight reads only `bal.balance / 100`, the MASKED TOTAL (brokers/kalshi_live.py:278),
and IGNORES the breakdown. A healthy total with an empty market-shard is exactly the state that silently killed the
legacy poly_kalshi_mlb copy division for two days: an MLB order auto-routes to shard 3, finds ~$2 there and 400s,
while the ~$515 total looks fine. This module parses the breakdown so per-shard funding is VISIBLE.

Pure-stdlib. It imports NOTHING from the order path (execution / kalshi_live / live_driver). `parse_balance` is
pure and fully unit-tested; `fetch_shard_balances` is a 3-line async wrapper over a raw client
`.get('/portfolio/balance')` -- the same raw call the R7 probes proved returns the breakdown (pykalshi's typed
`portfolio.get_balance()` exposes only `.balance`).

★ FAIL-SAFE CONTRACT: when the breakdown is ABSENT (a subaccount-restricted API key omits it) the split is UNKNOWN
-> `has_breakdown=False`, and `shard()` / `can_fund()` return None. A None from `can_fund` MUST be read by the
caller as "cannot verify -> do NOT place" (never coerce None to fundable). Seeing a masked total is NOT the same as
seeing the shard.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass

_CENTS_PER_DOLLAR = 100.0
_BALANCE_PATH = "/portfolio/balance"


def _to_dollars_float(v):
    """A Kalshi money value -> float dollars, or None if unparseable. `balance_breakdown` balances and
    `balance_dollars` are FIXED-POINT DOLLAR STRINGS ('509.8040') -- this does NOT divide by 100 (only the
    integer-cents `balance` field does, handled explicitly in parse_balance)."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ShardBalances:
    total_dollars: float            # the masked TOTAL (dollars) -- what the old reader returned
    by_shard: dict                  # {exchange_index:int -> dollars:float}; EMPTY when has_breakdown is False
    has_breakdown: bool             # False = split UNKNOWN (subaccount-restricted key / missing) -> caller fail-safes
    updated_ts: int | None = None

    def shard(self, exchange_index: int):
        """Dollars on ONE shard, or None if the split is UNKNOWN (has_breakdown False). A shard present in the
        account but absent from a KNOWN breakdown is $0.0 (Kalshi lists every shard)."""
        if not self.has_breakdown:
            return None
        return self.by_shard.get(int(exchange_index), 0.0)

    def can_fund(self, exchange_index: int, need_dollars: float):
        """True/False if THIS shard can fund `need_dollars`; None if the split is UNKNOWN. ★ A None means CANNOT
        VERIFY -> the caller MUST fail-safe (do not place). Never coerce None to True."""
        s = self.shard(exchange_index)
        if s is None:
            return None
        return s + 1e-9 >= float(need_dollars)

    def shard_sum(self):
        """Sum of the per-shard balances (dollars), or None if the split is unknown. A large gap vs
        total_dollars can flag a subaccount-scoped read; callers may sanity-check the two."""
        if not self.has_breakdown:
            return None
        return sum(self.by_shard.values())


def parse_balance(resp: dict) -> ShardBalances:
    """Parse a `GET /portfolio/balance` response into a ShardBalances. STRICT on the breakdown: a malformed entry
    RAISES ValueError (a load-bearing read must never emit a silently-wrong per-shard picture -- fail loud so the
    caller fail-safes). An ABSENT or EMPTY breakdown is NOT an error: it is the subaccount-restricted case ->
    has_breakdown=False (split unknown)."""
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
                raise ValueError("balance_breakdown entry missing exchange_index/balance: %r" % (item,))
            by_shard[int(idx)] = bal
    return ShardBalances(total_dollars=total, by_shard=by_shard, has_breakdown=has, updated_ts=updated)


async def fetch_shard_balances(client) -> ShardBalances:
    """READ per-shard balances via a raw client `.get('/portfolio/balance')` -- the same raw call the R7 probes
    proved returns `balance_breakdown` (pykalshi's typed `portfolio.get_balance()` exposes only `.balance`).
    `client` is the object `KalshiLiveBroker._client()` returns (duck-typed: has a `.get`). Awaits a coroutine
    result. Parsing + validation is delegated to `parse_balance` (so the pure logic stays fully unit-tested)."""
    r = client.get(_BALANCE_PATH)
    if inspect.isawaitable(r):
        r = await r
    return parse_balance(r)
