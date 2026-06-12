"""Shared Polymarket whale-screening primitives (activity-acquisition layer).

Both Polymarket whale rosters walk each candidate wallet's `/activity`
window and accumulate the condition_ids of its BUYs the same way:
  - the copy roster        — `scripts/refresh_polymarket_whales.py`
  - the observation roster  — `scripts/seed_polymarket_watchlist_deep.py`

This module is the single home for that shared activity-acquisition step,
extracted in option (c) Phase 3 (see
`reports/2026-06-11_polymarket_option_c_phase3_phaseA_duplication_map.md`):

  - `_fetch_wallet_activity_windowed` — the paginated single-wallet walk
    (exhaustion / page ceiling / optional legacy BUY-row early-stop). It
    previously lived in `seed_polymarket_watchlist_deep` and was imported
    from there by `refresh_polymarket_whales` — a script-imports-script
    coupling this module removes. `seed_polymarket_watchlist_deep`
    re-exports it so existing `from ...seed_... import ...` imports keep
    working until a later cleanup.
  - `fetch_activity_window_for_candidates` — the per-candidate loop
    wrapper: compute the effective walk target, walk each wallet, derive
    the `window_truncated` flag, and accumulate unique BUY condition_ids.

The two callers differ in exactly two behaviors, both PARAMETERIZED here
so each caller's output stays byte-for-byte identical to its
pre-extraction behavior:
  - `broad_catch` — `refresh` wraps each walk in a broad `except Exception`
    (-> `[], "fetch_error"`) on top of the walk's own, narrower
    `PolymarketDataAPIError` catch; `seed` does not.
  - `on_termination` — `seed` records per-wallet `termination_reasons` +
    `with_activity` telemetry; `refresh` does not.

The legitimately-different stages (leaderboard fetch/dedup, audit-input
prep, scoring invocation, selection/output) and the post-loop
`n_truncated` count + warn (whose wording differs per caller) stay in the
callers — unifying them would change behavior, not just structure.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

from trading_corp.data.polymarket_data_api_client import (
    ActivityRow,
    PolymarketDataAPIClient,
    PolymarketDataAPIError,
)

log = logging.getLogger(__name__)


async def _fetch_wallet_activity_windowed(
    client: PolymarketDataAPIClient,
    wallet: str,
    *,
    activity_limit: int,
    max_pages: int,
    target_buy_rows: int,
) -> tuple[list[ActivityRow], int, str]:
    """Walk `/activity` until enough BUY trades or feed exhausted or ceiling.

    Three explicit stop conditions, whichever fires first:
      - Cumulative TRADE+BUY row count >= target_buy_rows
        (a buffer above the eventual window_size to account for some BUYs
        being unresolved when we batch-fetch resolutions)
      - A page returns 0 rows (feed exhausted)
      - page index reaches max_pages (the hard ceiling on examined rows)

    Returns (rows, pages_fetched, termination_reason). The
    termination_reason is one of {"target_buys_reached", "exhausted",
    "max_pages_hit", "fetch_error"}.
    """
    out: list[ActivityRow] = []
    buy_count = 0
    pages_fetched = 0
    for page_idx in range(max_pages):
        offset = page_idx * activity_limit
        try:
            page = await client.fetch_activity(
                wallet, limit=activity_limit, offset=offset,
            )
        except PolymarketDataAPIError as e:
            log.warning(
                "activity fetch failed at offset=%d for %s: %s",
                offset, wallet[:10], e,
            )
            return out, pages_fetched, "fetch_error"
        pages_fetched += 1
        if not page:
            return out, pages_fetched, "exhausted"
        out.extend(page)
        for a in page:
            if a.type == "TRADE" and a.side == "BUY":
                buy_count += 1
        if buy_count >= target_buy_rows:
            return out, pages_fetched, "target_buys_reached"
        if len(page) < activity_limit:
            return out, pages_fetched, "exhausted"
    return out, pages_fetched, "max_pages_hit"


async def fetch_activity_window_for_candidates(
    client: PolymarketDataAPIClient,
    wallets: Iterable[str],
    *,
    activity_limit: int,
    max_pages: int,
    target_buy_rows: int | None,
    broad_catch: bool = False,
    on_termination: Callable[[str, str, list[ActivityRow]], None] | None = None,
) -> tuple[dict[str, list[ActivityRow]], dict[str, bool], set[str]]:
    """Walk each candidate wallet's `/activity` window; collect BUY condition_ids.

    Returns `(activity_by_wallet, truncated_by_wallet, all_condition_ids)`.

    The effective walk target is `target_buy_rows` if set, else
    `max_pages * activity_limit + 1` — i.e. walk to EXHAUSTION bounded by
    `max_pages`; a fixed `target_buy_rows` just moves the truncation cliff
    (the Phase E reconciliation finding). A wallet whose walk terminates at
    `max_pages_hit` or `fetch_error` leaves an INCOMPLETE window (partial
    rows -> floor-bounded realized) and is flagged `window_truncated`.

    `broad_catch=True` wraps each walk in a broad `except Exception` ->
    `([], "fetch_error")` and logs a per-wallet warning — `refresh`'s
    pre-extraction behavior, broader than the walk's own
    `PolymarketDataAPIError` catch. `on_termination(wallet, reason, acts)`,
    if given, is invoked once per wallet with the termination reason and
    fetched rows — `seed` uses it for `termination_reasons` +
    `with_activity` telemetry. The caller owns the post-loop `n_truncated`
    count + warn (its wording differs per caller) and every downstream stage.
    """
    eff_target = (
        target_buy_rows if target_buy_rows is not None
        else max_pages * activity_limit + 1
    )
    activity_by_wallet: dict[str, list[ActivityRow]] = {}
    truncated_by_wallet: dict[str, bool] = {}
    all_condition_ids: set[str] = set()
    for wallet in wallets:
        if broad_catch:
            try:
                acts, _pages, reason = await _fetch_wallet_activity_windowed(
                    client, wallet, activity_limit=activity_limit,
                    max_pages=max_pages, target_buy_rows=eff_target,
                )
            except Exception as e:
                log.warning("activity fetch failed for %s: %s", wallet[:10], e)
                acts, reason = [], "fetch_error"
        else:
            acts, _pages, reason = await _fetch_wallet_activity_windowed(
                client, wallet, activity_limit=activity_limit,
                max_pages=max_pages, target_buy_rows=eff_target,
            )
        activity_by_wallet[wallet] = acts
        # Both max_pages_hit AND fetch_error leave an incomplete window, so the
        # realized PnL downstream is read as a floor-bounded estimate.
        truncated_by_wallet[wallet] = reason in ("max_pages_hit", "fetch_error")
        if on_termination is not None:
            on_termination(wallet, reason, acts)
        for a in acts:
            if a.type == "TRADE" and a.side == "BUY" and a.condition_id:
                all_condition_ids.add(a.condition_id)
    return activity_by_wallet, truncated_by_wallet, all_condition_ids
