"""PMCC live-pricing cache + the LLM-free pricing operation (P1, 2026-07-31).

Splits deterministic Robinhood PRICING from expensive LLM JUDGMENT. `price_and_stash`
rebuilds a roll from the STORED judgment's δ-band + DTE (NO Anthropic call), computes
the live debit/credit/net, and writes the consent stash+fingerprint from the SAME
pull — so what the panel shows is what Approve fires. Results are cached per
(slug,symbol) with a short TTL and refreshed on an interval during market hours; the
cache is DISPLAY-ONLY — reprice-at-dispatch stays the FINAL consent guard.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

_TTL_SEC = 45.0            # display-cache freshness window (30–60s tunable)
_STAGGER_SEC = 0.15       # inter-pull delay so ~9 chain pulls stay under RH limits
_CACHE: dict[tuple[str, str], "PricedRoll"] = {}


@dataclass
class PricedRoll:
    """One symbol's live roll pricing snapshot. `buildable` is True only when a
    concrete debit/credit/net estimate was produced (gates the panel Approve)."""
    slug: str
    symbol: str
    priced_at: float                       # time.time() of the pull
    orders: list = field(default_factory=list)
    estimate: dict | None = None           # {debit,credit,net,net_abs,direction,strikes...}
    earnings: dict | None = None
    estimate_reason: str | None = None
    stash_token: tuple | None = None       # (preview_id, fingerprint) or None
    market_closed: bool = False
    buildable: bool = False


def _key(slug: str, symbol: str) -> tuple[str, str]:
    return (slug, (symbol or "").upper())


def market_regular_open(now: Any = None) -> bool:
    """Regular US options session (holiday + half-day aware) — the gate for
    auto-refresh so we never hammer RH pre/post-market or on a closed day."""
    from datetime import datetime, timezone
    from trading_corp.utils.market_hours import default_calendar
    when = now or datetime.now(timezone.utc)
    try:
        return bool(default_calendar().is_open_at(when))
    except Exception:      # noqa: BLE001 — a calendar hiccup must never break a render
        return False


# FIX 2 (2026-08-04): the MANUAL build paths (refresh-pricing + Re-analyze) call
# price_and_stash / propose_orders_for_pair directly, bypassing the auto-refresh
# market-hours gate above. When the options market is CLOSED they must not build a
# priced roll off stale overnight quotes — a priced Approve on stale prices is a
# trap. They short-circuit to `market_closed_extras()` instead: a specific reason,
# NO estimate (so no Approve renders), NO order build attempted.
MARKET_CLOSED_REASON = (
    "market closed — the roll will price at the 9:30 ET open"
)


def market_closed_extras() -> dict:
    """roll_extras for a manual build path when the options market is closed. No
    estimate (nothing was built → no stale-quote pricing), just the specific reason
    so the panel says WHY and offers no priced Approve."""
    return {"earnings": None, "estimate": None, "estimate_reason": MARKET_CLOSED_REASON}


def _analysis_from_record(rec: dict, symbol: str):
    """Reconstruct a `PMCCAnalysis` from a stored judgment (NO LLM). The δ band →
    point (midpoint) + band bounds; a missing band → None → config-default
    selection. Deterministic gates still apply downstream in propose_orders_for_pair."""
    from trading_corp.agents.divisions.pmcc_robinhood import PMCCAnalysis
    lo = rec.get("target_delta_low")
    hi = rec.get("target_delta_high")
    mid = ((float(lo) + float(hi)) / 2.0) if (lo is not None and hi is not None) else None
    return PMCCAnalysis(
        symbol=(symbol or "").upper(),
        action=str(rec.get("status") or "hold"),
        confidence=float(rec.get("confidence") or 0.0),
        urgency=str(rec.get("urgency") or "routine"),
        summary=str(rec.get("summary") or ""),
        rationale=str(rec.get("rationale") or ""),
        warnings=list(rec.get("warnings") or []),
        target_delta=mid,
        target_dte=rec.get("target_dte"),
        target_strike=None,
        target_delta_low=lo,
        target_delta_high=hi,
        override=None,
    )


async def price_and_stash(
    pmcc_agent: Any, broker: Any, slug: str, symbol: str, db_url: str, *, now: float | None = None,
) -> "PricedRoll":
    """Rebuild + price a roll from the STORED judgment (NO Anthropic call) and write
    the consent stash from the SAME pull. Never raises — degrades to a non-buildable
    PricedRoll (Approve stays suppressed). Updates + returns the cache entry."""
    from trading_corp.agents.divisions import _pmcc_status
    from trading_corp.web.pmcc_roll_card import build_pmcc_roll_card_extras
    from trading_corp.web import pmcc_preview
    import types as _types
    ts = now if now is not None else time.time()
    pr = PricedRoll(slug=slug, symbol=(symbol or "").upper(), priced_at=ts)
    try:
        rec = _pmcc_status.load_decision(symbol, db_url=db_url)
    except Exception as e:      # noqa: BLE001
        log.warning("price_and_stash: load_decision(%s) failed: %s", symbol, e)
        rec = None
    action = (rec.get("status") if rec else "") or ""
    # Only actionable rolls are priceable; hold/watch/none → nothing to price.
    if not rec or action.lower() in ("", "hold", "watch"):
        _CACHE[_key(slug, symbol)] = pr
        return pr
    try:
        analysis = _analysis_from_record(rec, symbol)
        orders = await pmcc_agent.propose_orders_for_pair(broker, symbol, analysis, preview=True)
    except Exception as e:      # noqa: BLE001 — pricing must never crash the panel
        log.warning("price_and_stash: propose_orders_for_pair(%s) failed: %s", symbol, e)
        orders = []
    pr.orders = list(orders or [])
    if orders:
        try:
            extras = await build_pmcc_roll_card_extras(
                _types.SimpleNamespace(orders=orders, underlying=symbol), broker, pmcc_agent,
            )
            pr.estimate = extras.get("estimate")
            pr.earnings = extras.get("earnings")
            pr.estimate_reason = extras.get("estimate_reason")
        except Exception as e:      # noqa: BLE001
            log.warning("price_and_stash: extras(%s) failed: %s", symbol, e)
        if pr.estimate is not None:
            pr.buildable = True
            try:
                pr.stash_token = pmcc_preview.stash_preview(
                    slug, symbol, orders, action=action, now=ts,
                )
            except Exception as e:      # noqa: BLE001
                log.warning("price_and_stash: stash(%s) failed: %s", symbol, e)
    else:
        # FIX 3 (2026-08-04): empty orders → a gate aborted the build. Surface the
        # SPECIFIC reason the agent just stashed instead of leaving estimate_reason
        # None (which renders the conflated "market closed, illiquid, or a sparse
        # chain" fallback). No build ran; the roll re-prices at approval.
        try:
            pr.estimate_reason = pmcc_agent.last_roll_abort_reason(symbol)
        except Exception as e:      # noqa: BLE001 — reason is best-effort
            log.warning("price_and_stash: abort-reason(%s) failed: %s", symbol, e)
    _CACHE[_key(slug, symbol)] = pr
    return pr


def cached(slug: str, symbol: str) -> "PricedRoll | None":
    """Read the cache WITHOUT pricing (for a pure render). None if never priced."""
    return _CACHE.get(_key(slug, symbol))


async def get_priced(
    pmcc_agent: Any, broker: Any, slug: str, symbol: str, db_url: str,
    *, ttl: float = _TTL_SEC, now: float | None = None,
) -> "PricedRoll | None":
    """Cache-or-price one symbol. Serves a fresh (< ttl) cache hit; otherwise prices
    during market hours, or marks the last cached value `market_closed` off-hours
    (never hammers RH off-hours)."""
    ts = now if now is not None else time.time()
    hit = _CACHE.get(_key(slug, symbol))
    if hit is not None and (ts - hit.priced_at) < ttl and not hit.market_closed:
        return hit
    if not market_regular_open():
        if hit is not None:
            hit.market_closed = True
            return hit
        pr = PricedRoll(slug=slug, symbol=(symbol or "").upper(), priced_at=ts, market_closed=True)
        _CACHE[_key(slug, symbol)] = pr
        return pr
    return await price_and_stash(pmcc_agent, broker, slug, symbol, db_url, now=ts)


async def refresh_division(
    pmcc_agent: Any, broker: Any, slug: str, symbols: list[str], db_url: str,
    *, ttl: float = _TTL_SEC, now: float | None = None,
) -> None:
    """Price all `symbols` (staggered) into the cache during market hours; no-op
    off-hours (marks entries market_closed). Never raises. Skips symbols whose
    cache is still fresh so repeated page loads reuse the pull."""
    if not market_regular_open():
        for s in symbols:
            hit = _CACHE.get(_key(slug, s))
            if hit is not None:
                hit.market_closed = True
        return
    ts = now if now is not None else time.time()
    for s in symbols:
        hit = _CACHE.get(_key(slug, s))
        if hit is not None and (ts - hit.priced_at) < ttl and not hit.market_closed:
            continue
        try:
            await price_and_stash(pmcc_agent, broker, slug, s, db_url, now=time.time())
        except Exception as e:      # noqa: BLE001
            log.warning("refresh_division: price(%s) failed: %s", s, e)
        await asyncio.sleep(_STAGGER_SEC)


def symbols_for(slug: str) -> list[str]:
    """Symbols currently in the pricing cache for `slug` (populated on the last
    division-page load). The interval refresh re-prices exactly what's on screen."""
    return [k[1] for k in list(_CACHE.keys()) if k[0] == slug]


def tile_pricing_view(pr: "PricedRoll | None", *, ttl: float = _TTL_SEC, now: float | None = None) -> dict:
    """Flatten a `PricedRoll` into the compact dict the tile template renders:
    {state, label, net_abs, direction, strike, buildable, market_closed}."""
    age = pricing_age_state(pr, ttl=ttl, now=now)
    est = (pr.estimate or {}) if pr is not None else {}
    return {
        "state": age["state"],
        "label": age["label"],
        "net_abs": est.get("net_abs"),
        "direction": est.get("direction"),
        "strike": est.get("open_strike"),
        "buildable": bool(pr.buildable) if pr is not None else False,
        "market_closed": bool(pr.market_closed) if pr is not None else False,
    }


def pricing_age_state(pr: "PricedRoll | None", *, ttl: float = _TTL_SEC, now: float | None = None) -> dict:
    """Two-clock PRICING sub-badge: green < ttl, amber < 2×ttl, red beyond — or
    'closed' when the market-hours gate is off. Returns {state,label,age_s}."""
    if pr is None:
        return {"state": "none", "label": "not priced", "age_s": None}
    if pr.market_closed:
        return {"state": "closed", "label": "market closed", "age_s": None}
    ts = now if now is not None else time.time()
    age = max(0.0, ts - pr.priced_at)
    if age < ttl:
        state = "green"
    elif age < 2 * ttl:
        state = "amber"
    else:
        state = "red"
    label = f"{int(age)}s" if age < 90 else f"{int(age // 60)}m"
    return {"state": state, "label": label, "age_s": age}
