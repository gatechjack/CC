"""Venue OPEN-EXPOSURE read -- Prediction Markets exposure-cap VENUE REBASE (PM_REQUIREMENTS R7; RULING 5, 2026-09-02).

The per-account open-exposure cap (execution gate 6) historically summed PM's OWN journal (pm_subdivision_order) --
correct ONLY while PM is the SOLE trader on the Kalshi account. A co-tenant division (legacy poly_kalshi_mlb shared
Karen's keypair) or a manual trade is INVISIBLE to a journal sum, so PM could over-commit against the real account.
This module reads the ACCOUNT'S TRUE open exposure from the venue (`GET /portfolio/positions`, summing per-position
`market_exposure`) so the cap is correct REGARDLESS of PM-exclusivity. Jack RULED (5): build the venue read, do not
defer -- "a journal sum is only correct while PM is the sole trader; a venue read is correct regardless."

Pure-stdlib; imports NOTHING from the order path (mirrors shard_balance.py). `parse_open_exposure` is pure and
unit-tested; `fetch_open_exposure` is a thin async PAGER over a raw client `.get('/portfolio/positions')`.

★ FIELD / UNIT ASSUMPTION -- VERIFY AT DEPLOY. Kalshi position `market_exposure` is the current cost at risk in
INTEGER CENTS -> dollars = sum(market_exposure)/100. This is the ONE fact I could not confirm on the box tonight
(the Rung-0 read was classifier-blocked). The deploy CROSS-CHECK reads jack's live venue exposure and confirms it
~matches his journal open_usd (~$13 at the last read); a 100x unit error would be glaring. If the field turns out to
be a fixed-point dollar STRING (like balance_dollars), change `_CENTS_PER_DOLLAR` handling before the restart.

★ TRI-STATE like shard_balance.ShardBalances: `has_data` True (a trustworthy sum -- incl. an empty book = flat = $0)
/ False (positions could not be trusted -> the caller FAILS CLOSED at gate 6: skip, never size blind). Fail LOUD on
CORRUPTION (a non-list market_positions, a non-finite market_exposure) -- a load-bearing money gate must never sum a
silently-wrong exposure. `has_data=False` is the ONLY soft signal, reserved for the legitimately-absent case (the
market_positions key missing from the response).
"""
from __future__ import annotations

import inspect
import math
from dataclasses import dataclass

_CENTS_PER_DOLLAR = 100.0
_POSITIONS_PATH = "/portfolio/positions"
_PAGE_LIMIT = 200
_MAX_PAGES = 25   # bound the pager; 25 x 200 = 5000 positions, far beyond any real account


def _cents_to_dollars(v):
    """A Kalshi integer-cents money value -> FINITE float dollars, or None if absent/unparseable/non-finite.
    Rejects NaN/Infinity (an infinite exposure would make the cap reject everything, masking a real read)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f / _CENTS_PER_DOLLAR


@dataclass(frozen=True)
class VenueExposure:
    total_dollars: float     # sum of per-position market_exposure (dollars); the ACCOUNT'S true open exposure
    has_data: bool           # False = UNTRUSTWORTHY / absent -> caller fail-closes (skip); never treat as $0
    n_positions: int = 0     # positions summed (incl. flats), for logging / the deploy cross-check

    def open_dollars(self):
        """The exposure in dollars, or None when unknown (has_data False) -- so gate 6 fails closed on None."""
        return self.total_dollars if self.has_data else None


def parse_open_exposure(market_positions) -> VenueExposure:
    """Sum per-position `market_exposure` -> VenueExposure. FAIL-LOUD on corruption (a non-list, a non-dict entry,
    a non-finite market_exposure). An ABSENT market_positions (None) is the only soft case -> has_data=False. An
    EMPTY list is a KNOWN flat account -> has_data=True, total 0.0 (flat is known, not unknown)."""
    if market_positions is None:
        return VenueExposure(total_dollars=0.0, has_data=False, n_positions=0)
    if not isinstance(market_positions, list):
        raise ValueError("market_positions is not a list: %r" % (type(market_positions),))
    total = 0.0
    for p in market_positions:
        if not isinstance(p, dict):
            raise ValueError("market_positions entry is not a dict: %r" % (p,))
        d = _cents_to_dollars(p.get("market_exposure"))
        if d is None:
            raise ValueError("position missing/non-finite market_exposure: %r" % (p,))
        total += d
    return VenueExposure(total_dollars=total, has_data=True, n_positions=len(market_positions))


async def fetch_open_exposure(client) -> VenueExposure:
    """READ the account's open exposure by paging `GET /portfolio/positions` and summing `market_exposure`.
    `client` is what `KalshiLiveBroker._client()` returns (duck-typed: has `.get`; awaits a coroutine result).
    RAISES on a malformed page or corrupt exposure (the caller -- live_driver -- wraps this and fails CLOSED to a
    has_data=False VenueExposure, so gate 6 skips rather than sizing blind). A response lacking `market_positions`
    on the FIRST page (and no cursor) is the legitimately-absent case -> has_data=False."""
    seen = []
    cursor = None
    pages = 0
    saw_key = False
    while True:
        path = "%s?limit=%d" % (_POSITIONS_PATH, _PAGE_LIMIT) + (("&cursor=%s" % cursor) if cursor else "")
        raw = client.get(path)
        if inspect.isawaitable(raw):
            raw = await raw
        pages += 1
        if not isinstance(raw, dict):
            raise ValueError("positions page is not a dict: %r" % (type(raw),))
        mp = raw.get("market_positions")
        if mp is not None:
            saw_key = True
            if not isinstance(mp, list):
                raise ValueError("market_positions is not a list: %r" % (type(mp),))
            seen.extend(mp)
        cursor = raw.get("cursor")
        if not cursor or pages >= _MAX_PAGES:
            break
    if not saw_key:
        # the response never carried market_positions -> UNKNOWN (subaccount-restricted / shape change) -> fail-closed
        return VenueExposure(total_dollars=0.0, has_data=False, n_positions=0)
    return parse_open_exposure(seen)
