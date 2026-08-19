"""MACE Robinhood broker adapter — the ONLY OptionsBrokerPort impl, and the
ONLY file under `trading_corp/mace/` permitted to import `trading_corp.brokers.*`
(an AST test enforces the boundary). A future Tasty impl replaces THIS file
alone; nothing above it changes.

It wraps the existing `RobinhoodBroker` (byte-untouched real-money adapter) and
translates the neutral port surface onto it. The four combo methods route through
`RobinhoodBroker.place_multi_leg` / `place_multi_leg_resting` /
`get_option_order_status` (the additive, pre-authorized robinhood.py surface) so
MACE reuses the RAILS without forking the IC/PMCC code. Marketability-direction,
laddering, PT lifecycle, and the fake-fill guard all live ABOVE this file in
execution.py — here we only translate calls and RESULTS, and we never fabricate a
fill (a hard reject propagates as an exception; a pending combo returns a
non-terminal OrderResult; execution decides what to do).

Closing orders are built from `CondorSpec.closing_legs()` (buy back the shorts,
sell the longs) — a single source of truth for the flatten payload, shared by the
exit ladder and the resting PT, which is exactly what the twin-builder
consistency test pins.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime

from trading_corp.brokers.robinhood import RobinhoodBroker, RobinhoodComboPending
from trading_corp.persistence.models import ProposedOrder

from trading_corp.mace.broker_port import (
    AccountInfo, OpenOrder, OpenOptionPosition, OptionsBrokerPort, OrderResult,
    PortSnapshot, DIR_CREDIT, DIR_DEBIT,
    STATE_FILLED, STATE_QUEUED, STATE_UNCONFIRMED,
)
from trading_corp.mace.domain import CondorLeg, CondorSpec, OptionQuote
from trading_corp.mace.strategy import ChainView
from trading_corp.utils.time import now_et

_LOG = logging.getLogger("mace.rh_broker")

# Terminal option-order states (lower-cased) — a cancel is believed ONLY on one of
# these read back (the fake-cancel guard lives in execution; this is used here only
# to sequence the cancel fallback chain + implement raise-on-all-fail).
_TERMINAL_STATES_LC = frozenset(
    {"filled", "partially_filled", "rejected", "cancelled", "canceled", "failed", "voided"}
)


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _fopt(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f != 0.0 else None    # RH reports missing bid/ask as 0 -> None


def _isodate(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except (ValueError, TypeError):
        return False


class RobinhoodOptionsBroker(OptionsBrokerPort):
    """Neutral port over a live/paper `RobinhoodBroker`.

    `dte_min`/`dte_max`/`strike_band_pct` bound `chain()`'s fetch to the target
    expiry + near-money strikes (the entry-eval needs the delta ladder; the
    management ladders use `leg_quote` per strike). `division` is the strategy
    tag stamped on the ProposedOrder legs (never trips the PMCC LEAP guard —
    that is scoped to `robinhood_pmcc`).

    strike_band_pct is CONFIG-DRIVEN as of 2026-08-18: main.py passes
    `mace_cfg.entry.strike_band_pct` (mace.yaml, default 0.25). The 0.15 __init__
    default below is only the fallback for callers that don't pass it (tests);
    prod always overrides it. Too tight a band drops high-IV names' wings from
    the fetched ChainView -> no_wing/no_delta_strike (see mace.yaml comment)."""

    def __init__(self, broker: RobinhoodBroker, *, dte_min: int = 30, dte_max: int = 45,
                 strike_band_pct: float = 0.15, division: str = "robinhood_mace",
                 now_et_fn=now_et, cancel_url_polls: int = 3, cancel_url_poll_s: float = 1.0,
                 cancel_confirm_polls: int = 6, cancel_confirm_poll_s: float = 1.0) -> None:
        self._broker = broker
        self._dte_min = dte_min
        self._dte_max = dte_max
        self._band = strike_band_pct
        self._division = division
        self._now_et = now_et_fn
        # Resilient-cancel cadence (plan Checkpoint-0 cancel-path fix 2026-08-10).
        self._cancel_url_polls = cancel_url_polls
        self._cancel_url_poll_s = cancel_url_poll_s
        self._cancel_confirm_polls = cancel_confirm_polls
        self._cancel_confirm_poll_s = cancel_confirm_poll_s
        self._last_cancel_rung: str | None = None   # diagnostic: which rung took

    def _today(self) -> date:
        return self._now_et().date()

    # ── leg-order construction (single source of truth for both build paths) ──
    def _leg_orders(self, spec: CondorSpec, contracts: int, net_limit: float,
                    combo_id: str, direction: str, legs: tuple[CondorLeg, ...],
                    *, combo_tif: str | None = None,
                    fill_timeout_s: float | None = None) -> list[ProposedOrder]:
        orders: list[ProposedOrder] = []
        for i, leg in enumerate(legs):
            extra = {
                "is_option": True, "is_multi_leg": True, "combo_id": combo_id,
                "combo_direction": direction, "net_limit_price": float(net_limit),
                "underlying": spec.symbol, "expiration": spec.expiry.isoformat(),
                "strike": float(leg.strike), "option_type": leg.opt_type,
                "position_effect": leg.effect, "ratio_quantity": 1,
            }
            if i == 0:
                # MACE-only combo controls read from leg-0 by the additive
                # robinhood.py edit. PMCC/IC never set these -> their spread
                # payloads stay byte-identical (the golden-payload guarantee).
                if combo_tif is not None:
                    extra["combo_time_in_force"] = combo_tif
                if fill_timeout_s is not None:
                    extra["combo_fill_timeout_s"] = float(fill_timeout_s)
            orders.append(ProposedOrder(
                strategy=self._division, symbol=spec.symbol, side=leg.side,
                qty=float(contracts), order_type="limit", limit_price=float(net_limit),
                rationale=f"mace {direction} condor {combo_id}", extra=extra))
        return orders

    # ── market data ──────────────────────────────────────────────────────
    async def chain(self, symbol: str) -> ChainView:
        spot: float | None
        try:
            spot = float(await self._broker.quote(symbol))
        except Exception as exc:  # noqa: BLE001 — spot best-effort; empty chain if missing
            _LOG.warning("mace chain: spot fetch failed for %s: %s", symbol, exc)
            spot = None

        expiry_strs = await self._broker.get_expiration_dates(symbol) or []
        all_expiries = tuple(sorted({date.fromisoformat(e) for e in expiry_strs if _isodate(e)}))

        target = None
        best_dte = -1
        today = self._today()
        for d in all_expiries:
            dte = (d - today).days
            if self._dte_min <= dte <= self._dte_max and dte > best_dte:
                best_dte, target = dte, d

        quotes: dict[tuple[date, str, float], OptionQuote] = {}
        if target is not None and spot:
            lo, hi = spot * (1 - self._band), spot * (1 + self._band)
            for otype in ("put", "call"):
                rows = await self._rows(symbol, target, otype)
                for r in rows:
                    k = _f(r.get("strike_price"))
                    if k <= 0 or not (lo <= k <= hi):
                        continue
                    quotes[(target, otype, round(k, 4))] = OptionQuote(
                        symbol=symbol, expiry=target, strike=k, opt_type=otype,
                        bid=_fopt(r.get("bid")), ask=_fopt(r.get("ask")),
                        delta=_fopt(r.get("delta")), option_id=r.get("option_id"))
        return ChainView(symbol=symbol, spot=spot, expiries=all_expiries, quotes=quotes)

    async def _rows(self, symbol: str, expiry: date, opt_type: str) -> list[dict]:
        if opt_type == "put":
            return await self._broker.get_puts_for_expiry(symbol, expiry.isoformat()) or []
        return await self._broker.get_calls_for_expiry(symbol, expiry.isoformat()) or []

    async def leg_quote(self, symbol: str, expiry: date, opt_type: str,
                        strike: float) -> OptionQuote | None:
        rows = await self._rows(symbol, expiry, opt_type)
        tgt = round(float(strike), 4)
        for r in rows:
            if round(_f(r.get("strike_price")), 4) == tgt:
                return OptionQuote(
                    symbol=symbol, expiry=expiry, strike=float(strike), opt_type=opt_type,
                    bid=_fopt(r.get("bid")), ask=_fopt(r.get("ask")),
                    delta=_fopt(r.get("delta")), option_id=r.get("option_id"))
        return None

    # ── order placement ──────────────────────────────────────────────────
    async def place_condor(self, spec: CondorSpec, contracts: int, net_limit: float,
                           combo_id: str, *, direction: str, time_in_force: str,
                           fill_timeout_s: float) -> OrderResult:
        # DIR_CREDIT = opening (entry); DIR_DEBIT = closing (exit).
        legs = spec.opening_legs() if direction == DIR_CREDIT else spec.closing_legs()
        orders = self._leg_orders(spec, contracts, net_limit, combo_id, direction, legs,
                                  combo_tif=time_in_force, fill_timeout_s=fill_timeout_s)
        try:
            fills = await self._broker.place_multi_leg(orders, ref_id=combo_id)
        except RobinhoodComboPending as pend:
            # Accepted (has an id) but not terminal `filled` within the poll window
            # -> NON-terminal; execution cancels + polls (cancel race). Never booked here.
            return OrderResult(order_id=pend.order_id, state=STATE_QUEUED,
                               processed_quantity=0.0, time_in_force=time_in_force)
        # A hard reject / no-id raises RobinhoodOrderError inside place_multi_leg and
        # PROPAGATES (port contract) -> execution's fake-fill guard books nothing.
        oid = acct = None
        if fills:
            oid = fills[0].broker_order_id or fills[0].order_id
            acct = fills[0].account
        return OrderResult(order_id=oid, state=STATE_FILLED,
                           processed_quantity=float(contracts),
                           time_in_force=time_in_force, account_url=acct)

    async def place_resting_close(self, spec: CondorSpec, contracts: int,
                                  net_debit_limit: float, ref_id: str) -> str:
        legs = spec.closing_legs()                       # buy-to-close at a net debit
        orders = self._leg_orders(spec, contracts, net_debit_limit, ref_id, DIR_DEBIT, legs)
        order_id = await self._broker.place_multi_leg_resting(
            orders, ref_id=ref_id, time_in_force="gtc")
        return str(order_id)

    async def cancel(self, order_id: str) -> None:
        """Resilient cancel — root-caused + FIXED 2026-08-10 (operator devtools capture).

        ── WHY THE BODY IS REQUIRED (capture 2026-08-10, RH web app, single-leg
        subject order 6a7a3ffa, token-redacted; provenance in the plan's Checkpoint-0
        section) ─────────────────────────────────────────────────────────────────
        The endpoint NEVER MOVED. RH's web app POSTs the SAME constructed
        `.../options/orders/{id}/cancel/` URL that robin_stocks builds — the
        difference is the BODY: the web app sends `Content-Type: application/json`
        with `{"account_number": <owning account>}`; robin_stocks POSTs it with NO
        body. Under the brokeback edge sharding the service resolves the order via
        the account CONTEXT and returns 404 (not 400) when the body is absent — which
        is exactly why reads work, the app/web cancel works, and robin_stocks'
        empty-body cancel 404s on BOTH gtc and gfd regardless of order state. Fix =
        POST the constructed URL WITH the account_number body (rung 0 below).

        Ordered fallback, each rung confirmed by a TERMINAL state read-back:
          (0) PRIMARY: POST the constructed cancel URL WITH json body
              {"account_number": <bound account>} — the captured web-app request;
          (a) POST the order's own server-provided `cancel_url` (empty body — the
              pre-fix path; retained as a fallback, harmless if it 404s);
          (b) if cancel_url absent, short bounded re-poll for it to populate;
          (c) last resort, robin_stocks' constructed endpoint (empty body);
          (d) all failing => raise loudly — NEVER silently give up.

        FAKE-CANCEL GUARD (unchanged, ABSOLUTE): the caller (execution) independently
        re-reads to a terminal state before it believes the cancellation; the per-rung
        confirm here only sequences the fallback + raises on genuine failure. NO HTTP
        response — including a 200/204 on the cancel POST — is EVER treated as a
        cancellation by itself; only a terminal `state` read-back is."""
        import robin_stocks.robinhood as rs  # type: ignore
        from robin_stocks.robinhood.helper import request_post  # type: ignore

        async def _read() -> dict:
            return await asyncio.to_thread(rs.orders.get_option_order_info, order_id) or {}

        def _terminal(info: dict) -> bool:
            return str(info.get("state") or "").lower() in _TERMINAL_STATES_LC

        info = await _read()
        if _terminal(info):
            self._last_cancel_rung = "already_terminal"
            return
        cancel_url = info.get("cancel_url") or info.get("cancel")

        # (b) bounded re-poll for cancel_url to populate (state-transition case).
        polls = 0
        while not cancel_url and polls < self._cancel_url_polls:
            await asyncio.sleep(self._cancel_url_poll_s)
            polls += 1
            info = await _read()
            if _terminal(info):
                self._last_cancel_rung = "terminal_during_poll"
                return
            cancel_url = info.get("cancel_url") or info.get("cancel")

        async def _issue_then_confirm(rung: str, issue) -> bool:
            await issue()
            for _ in range(self._cancel_confirm_polls):
                await asyncio.sleep(self._cancel_confirm_poll_s)
                if _terminal(await _read()):
                    self._last_cancel_rung = rung
                    return True
            return False

        # (0) PRIMARY (2026-08-10 capture fix): the constructed cancel URL WITH the
        # account_number JSON body. account_number is sourced from the broker's BOUND
        # account (NEVER hardcoded) — MACE's bound account is the order's owning
        # account. request_post(..., json=True) sets Content-Type: application/json +
        # posts {"account_number": acct} as the body; it swallows the HTTP error and
        # returns None, so the terminal read-back (not the POST result) is what
        # confirms the cancel (fake-cancel guard).
        acct = getattr(self._broker, "_account_number", "") or None
        constructed_url = f"https://api.robinhood.com/options/orders/{order_id}/cancel/"
        if acct and await _issue_then_confirm(
                "constructed_json_body",
                lambda: asyncio.to_thread(
                    request_post, constructed_url, {"account_number": acct}, json=True)):
            return
        # (a) the order's own server-provided cancel_url (pre-fix path, empty body).
        if cancel_url and await _issue_then_confirm(
                "cancel_url", lambda: asyncio.to_thread(request_post, cancel_url)):
            return
        # (c) last resort: robin_stocks' constructed endpoint (empty body).
        if await _issue_then_confirm(
                "constructed", lambda: asyncio.to_thread(rs.orders.cancel_option_order, order_id)):
            return
        # (d) all rungs failed to reach terminal -> raise loudly.
        final = await _read()
        raise RuntimeError(
            f"cancel of {order_id} reached NO terminal state via "
            f"constructed_json_body/cancel_url/constructed; "
            f"last state={str(final.get('state'))!r} cancel_url_present={bool(cancel_url)} "
            f"account_bound={bool(acct)}")

    async def order_status(self, order_id: str) -> OrderResult:
        info = await self._broker.get_option_order_status(order_id) or {}
        state = str(info.get("state") or "").lower() or STATE_UNCONFIRMED
        return OrderResult(
            order_id=str(info.get("id") or order_id), state=state,
            processed_quantity=_f(info.get("processed_quantity")),
            pending_quantity=_f(info.get("pending_quantity")),
            time_in_force=info.get("time_in_force"), raw=info)

    async def open_orders(self) -> list[OpenOrder]:
        import robin_stocks.robinhood as rs  # type: ignore
        acct = getattr(self._broker, "_account_number", "") or None
        raw = await asyncio.to_thread(rs.orders.get_all_open_option_orders, acct) or []
        return [
            OpenOrder(order_id=str(o.get("id") or ""),
                      state=str(o.get("state") or "").lower(),
                      time_in_force=o.get("time_in_force"),
                      ref_id=o.get("ref_id"), raw=o)
            for o in raw
        ]

    async def open_positions(self) -> list[OpenOptionPosition]:
        detail = await self._broker.get_option_positions_detail() or []
        return [
            OpenOptionPosition(symbol=str(d.get("chain_symbol") or "").upper(),
                               option_id=d.get("option_id"),
                               quantity=_f(d.get("quantity")), raw=d)
            for d in detail
        ]

    # ── account ──────────────────────────────────────────────────────────
    async def snapshot(self) -> PortSnapshot:
        s = await self._broker.snapshot()
        # equity = SETTLED, placeable cash (the defined-risk sizing basis; plan
        # § Sizing/E "never intraday buying power"), falling back to portfolio
        # equity only if the broker doesn't expose settled cash.
        equity = s.settled_cash if s.settled_cash is not None else s.equity
        return PortSnapshot(equity=equity, cash=s.cash, market_value=None)

    async def account_assertions(self) -> AccountInfo:
        acct = getattr(self._broker, "_account_number", "") or None
        m = re.search(r"(\d+)", self._broker.option_level or "")
        level = int(m.group(1)) if m else None
        atype = getattr(self._broker, "_account_type", "") or None
        # L3 requires a margin account (plan [A2026-08-09]); the live startup
        # assertion gates on option_level >= 3, so margin is derived + informational.
        margin = level is not None and level >= 3
        return AccountInfo(account_number=acct, option_level=level,
                           account_type=atype, margin=margin)
