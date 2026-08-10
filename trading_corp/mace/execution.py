"""MACE execution — order lifecycle over the async OptionsBrokerPort.

This is the safety-critical layer. It owns EVERY side effect that touches money:
the entry credit ladder, the emulated-market exit debit ladder, the resting-GTC
profit-target lifecycle, and the reconcile state machine — plus every `mace_rung`
write. It drives the neutral async `OptionsBrokerPort`; it imports domain +
strategy date-helpers + broker_port + notify only. It NEVER imports
`trading_corp.brokers.*` (that boundary lives in `rh_broker.py`; an AST test
enforces it).

Load-bearing invariants (plan § Behavior specifications):

  FAKE-FILL GUARD (absolute). A booking is written ONLY against a broker order
  whose confirmed terminal state == "filled". An HTTP error, a timeout, an
  exception, a partial fill, or an unconfirmable state NEVER books a fill and
  NEVER places a second order that could double-fill. When execution cannot
  prove what the broker did, it leaves the durable `mace_rung` anchor for the
  reconcile loop and (for exits) escalates URGENT rather than guessing.

  FAKE-CANCEL GUARD (absolute, sibling of the above). A cancellation is believed
  ONLY on a confirmed TERMINAL state read back from `port.order_status` — never
  on `port.cancel` returning, and never on any HTTP response (a 200 on the cancel
  POST included). Before any exit, `close_rung` cancels the resting PT and then
  `_poll_until_terminal` on it: a non-terminal / unconfirmable read-back ABORTS
  the exit (it never places a closing order that could double up against a still-
  live PT). The entry cancel-race and the reconcile drain apply the same rule.
  The cancel-path fix (rh_broker cancels via the order's own server `cancel_url`)
  changes HOW the request is issued; it does NOT relax this guard — the truth is
  always the terminal read-back, not the request.

  MARKETABILITY DIRECTION (trap 6). A CREDIT (entry) order is more marketable at
  a LOWER limit — the entry ladder walks the credit DOWN (mid − offset − k·tick)
  toward marketability, never below the 0.30×width credit floor. A DEBIT (exit)
  order is more marketable at a HIGHER limit — the exit ladder walks the debit UP
  from natural, never above the width×ceiling. The $0.01-credit "unfillable"
  inversion the Phase-0 probe caught is impossible here by construction.

  DETERMINISTIC IDS. Every attempt carries a distinct ref_id derived from the
  rung's deterministic combo_id (`mace-{sym}-{expiry}-{strikes}-{yyyymmdd}`):
  entry `-a{k}`, exit `-x{k}`, PT `-pt`. RH dedupes a repeated ref_id, so a fresh
  id per attempt is required; reconcile matches a crash-orphaned `submitting`
  rung against broker orders by that combo_id prefix.

  CREDIT BASIS. `credit_actual` is the marketable limit of the attempt that
  filled. A net-credit order fills at its limit or better, so this is the
  conservative (never-overstated) credit basis, and it is broker-neutral (no raw
  order-dict parsing crosses the port). It is persisted onto the `submitting`
  anchor BEFORE each place so a crash between fill and promote recovers the right
  credit in reconcile. Realized P&L = (credit_actual − exit_debit)·100·contracts.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from typing import Callable, Optional, Sequence

from trading_corp.mace import broker_port as bp
from trading_corp.mace.broker_port import OpenOrder, OptionsBrokerPort, OrderResult
from trading_corp.mace.config import MaceConfig
from trading_corp.mace.domain import (
    CondorSpec, OptionQuote, RungState, iso_week,
    RUNG_ABANDONED, RUNG_CLOSED, RUNG_CLOSING, RUNG_OPEN, RUNG_SUBMITTING,
    EXIT_PT,
)
from trading_corp.mace.notify import MaceNotifier
from trading_corp.mace.strategy import business_sessions_between
from trading_corp.utils.time import now_et, now_utc, to_et

_LOG = logging.getLogger("mace.execution")


class MaceRiskRejected(Exception):
    """Raised by the SINGLE place-funnel when the risk gate rejects (or is absent).
    MACE's execution drives the broker port directly and bypasses data_exec /
    ceo_graph, so the per-leg RiskAgent gate threaded through this funnel is MACE's
    ONLY instance of the platform's single-risk-chokepoint invariant. An
    unevaluated / rejected order RAISES here — it is never placed. Enforced
    structurally (an AST test pins every port.place_condor / place_resting_close
    call to the funnel), not by convention."""

# Horizon (business sessions) past which an un-drained `submitting` anchor with
# no fill and no working order is abandoned + alerted (plan § Reconcile loop).
_ABANDON_HORIZON_SESSIONS = 2


# ── tick rounding ─────────────────────────────────────────────────────────

def round_to_tick(value: float, tick: float, mode: str = "nearest") -> float:
    """Round `value` to the nearest multiple of `tick`.

    All MACE prices are non-negative, so ROUND_UP == ceiling and ROUND_DOWN ==
    floor. `mode`:
      - "down"    — floor to tick (entry credit: never demand more credit than the
                    formula, and floor makes a credit order MORE marketable)
      - "up"      — ceiling to tick (exit debit: the plan's "natural rounded UP to
                    tick", and ceiling makes a debit order MORE marketable)
      - "nearest" — half-up round (PT target)
    """
    if tick <= 0:
        return float(value)
    d = Decimal(str(value)) / Decimal(str(tick))
    if mode == "up":
        n = d.to_integral_value(rounding=ROUND_UP)
    elif mode == "down":
        n = d.to_integral_value(rounding=ROUND_DOWN)
    else:
        n = d.to_integral_value(rounding=ROUND_HALF_UP)
    return float(n * Decimal(str(tick)))


# ── mace_rung persistence (owned here; execution is the ONLY writer) ───────

def _legs_json(spec: CondorSpec) -> str:
    """Serialize the 4 opening legs (type/strike/side/effect). option_id +
    fill_price are unknown at build time (opaque handles never cross the port);
    they stay null — the rung is keyed by the deterministic strikes."""
    legs = [
        {"type": leg.opt_type, "strike": leg.strike, "side": leg.side,
         "effect": leg.effect, "option_id": None, "fill_price": None}
        for leg in spec.opening_legs()
    ]
    return json.dumps(legs)


def _spec_from_legs_json(legs_json_str: Optional[str], symbol: str,
                         expiry_str: str, width_dollars: float) -> CondorSpec:
    """Reconstruct a CondorSpec from a stored legs_json row (mirrors the
    shadow_eval reader). Falls back to a zero-strike placeholder if unparseable
    (non-zero width preserved to avoid div-by-zero downstream)."""
    try:
        legs = json.loads(legs_json_str) if legs_json_str else None
        if isinstance(legs, list) and len(legs) == 4:
            by: dict[tuple[str, str], float] = {}
            for leg in legs:
                t = str(leg.get("type") or leg.get("opt_type") or "").lower()
                s = str(leg.get("side") or "").lower()
                k = leg.get("strike", leg.get("strike_price"))
                if t and s and k is not None:
                    by[(t, s)] = float(k)
            sp, lp = by.get(("put", "sell")), by.get(("put", "buy"))
            sc, lc = by.get(("call", "sell")), by.get(("call", "buy"))
            if None not in (sp, lp, sc, lc):
                return CondorSpec(symbol=symbol, expiry=date.fromisoformat(expiry_str),
                                  short_put=sp, long_put=lp, short_call=sc,
                                  long_call=lc, width_dollars=float(width_dollars))
    except Exception:  # noqa: BLE001 — any parse failure -> placeholder
        pass
    return CondorSpec(symbol=symbol, expiry=date.fromisoformat(expiry_str),
                      short_put=0.0, long_put=0.0, short_call=0.0, long_call=0.0,
                      width_dollars=float(width_dollars))


_RUNG_COLS = (
    "rung_id, symbol, status, expiry, legs_json, width_dollars, contracts, "
    "credit_actual, max_risk_usd, entry_ts, entry_order_id, pt_order_id, "
    "pt_debit, exit_ts, exit_reason, exit_debit, realized_pnl, entry_iso_week"
)


class RungStore:
    """Thin sqlite persistence for `mace_rung`. Autocommit (the house `db.connect`
    opens with `isolation_level=None`), so every write is durable immediately —
    load-bearing for crash recovery. Tests pass an in-memory connection seeded
    with `db.SCHEMA`."""

    def __init__(self, conn) -> None:
        self.conn = conn

    # -- reads --
    def _row_to_rung(self, r) -> RungState:
        spec = _spec_from_legs_json(r["legs_json"], r["symbol"], r["expiry"],
                                    r["width_dollars"])
        return RungState(
            rung_id=r["rung_id"], symbol=r["symbol"], status=r["status"],
            expiry=date.fromisoformat(r["expiry"]), spec=spec,
            width_dollars=float(r["width_dollars"] or 0.0),
            contracts=int(r["contracts"] or 0),
            credit_actual=_f(r["credit_actual"]), max_risk_usd=_f(r["max_risk_usd"]),
            entry_ts=r["entry_ts"], entry_order_id=r["entry_order_id"],
            pt_order_id=r["pt_order_id"], pt_debit=_f(r["pt_debit"]),
            exit_ts=r["exit_ts"], exit_reason=r["exit_reason"],
            exit_debit=_f(r["exit_debit"]), realized_pnl=_f(r["realized_pnl"]),
            entry_iso_week=r["entry_iso_week"],
        )

    def get(self, rung_id: str) -> Optional[RungState]:
        r = self.conn.execute(
            f"SELECT {_RUNG_COLS} FROM mace_rung WHERE rung_id=?", (rung_id,)
        ).fetchone()
        return self._row_to_rung(r) if r is not None else None

    def load_by_status(self, *statuses: str) -> list[RungState]:
        if not statuses:
            return []
        qs = ",".join("?" * len(statuses))
        rows = self.conn.execute(
            f"SELECT {_RUNG_COLS} FROM mace_rung WHERE status IN ({qs})", statuses
        ).fetchall()
        return [self._row_to_rung(r) for r in rows]

    def load_all(self) -> list[RungState]:
        """Every rung, any status — the strategy derives all per-symbol
        aggregates (open counts, weekly budget, cooldown, realized P&L) from this."""
        rows = self.conn.execute(f"SELECT {_RUNG_COLS} FROM mace_rung").fetchall()
        return [self._row_to_rung(r) for r in rows]

    # -- writes --
    def insert_submitting(self, rung_id: str, spec: CondorSpec, contracts: int,
                          *, entry_ts: str, entry_iso_week: str,
                          max_risk_usd: Optional[float]) -> None:
        """Durable crash-recovery anchor written BEFORE the first place. INSERT OR
        IGNORE so a re-entry of the same session/strikes never clobbers a live
        rung."""
        self.conn.execute(
            "INSERT OR IGNORE INTO mace_rung "
            "(rung_id, symbol, status, expiry, legs_json, width_dollars, contracts, "
            " max_risk_usd, entry_ts, entry_iso_week) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rung_id, spec.symbol, RUNG_SUBMITTING, spec.expiry.isoformat(),
             _legs_json(spec), float(spec.width_dollars), int(contracts),
             max_risk_usd, entry_ts, entry_iso_week),
        )

    def set_pending_credit(self, rung_id: str, credit_limit: float) -> None:
        """Persist the prospective credit (this attempt's marketable limit) while
        still `submitting`, BEFORE the place, so a crash between fill and promote
        recovers the right credit basis."""
        self.conn.execute(
            "UPDATE mace_rung SET credit_actual=? WHERE rung_id=? AND status=?",
            (credit_limit, rung_id, RUNG_SUBMITTING),
        )

    def set_entry_order(self, rung_id: str, order_id: str) -> None:
        """Persist the BROKER order id the moment `place` returns it (pending or
        filled), so reconcile can `order_status` a real id after a crash. Only the
        broker id is stored here — never the ref/combo_id — so a crash-drain never
        statuses a non-order string."""
        self.conn.execute(
            "UPDATE mace_rung SET entry_order_id=? WHERE rung_id=? AND status=?",
            (order_id, rung_id, RUNG_SUBMITTING),
        )

    def promote_open(self, rung_id: str, *, credit_actual: float,
                     entry_order_id: Optional[str], entry_ts: str) -> None:
        row = self.conn.execute(
            "SELECT width_dollars, contracts FROM mace_rung WHERE rung_id=?",
            (rung_id,)
        ).fetchone()
        w = float(row["width_dollars"]) if row else 0.0
        c = int(row["contracts"]) if row else 0
        max_risk = (w - credit_actual) * 100.0 * c
        self.conn.execute(
            "UPDATE mace_rung SET status=?, credit_actual=?, max_risk_usd=?, "
            "entry_order_id=?, entry_ts=? WHERE rung_id=?",
            (RUNG_OPEN, credit_actual, max_risk, entry_order_id, entry_ts, rung_id),
        )

    def set_pt(self, rung_id: str, pt_order_id: str, pt_debit: float) -> None:
        self.conn.execute(
            "UPDATE mace_rung SET pt_order_id=?, pt_debit=? WHERE rung_id=?",
            (pt_order_id, pt_debit, rung_id),
        )

    def clear_pt(self, rung_id: str) -> None:
        self.conn.execute(
            "UPDATE mace_rung SET pt_order_id=NULL WHERE rung_id=?", (rung_id,)
        )

    def mark_closing(self, rung_id: str) -> None:
        self.conn.execute(
            "UPDATE mace_rung SET status=? WHERE rung_id=?", (RUNG_CLOSING, rung_id)
        )

    def mark_closed(self, rung_id: str, *, exit_reason: str, exit_debit: float,
                    realized_pnl: float, exit_ts: str) -> None:
        self.conn.execute(
            "UPDATE mace_rung SET status=?, exit_reason=?, exit_debit=?, "
            "realized_pnl=?, exit_ts=? WHERE rung_id=?",
            (RUNG_CLOSED, exit_reason, exit_debit, realized_pnl, exit_ts, rung_id),
        )

    def mark_abandoned(self, rung_id: str, detail: str) -> None:
        self.conn.execute(
            "UPDATE mace_rung SET status=?, extra_json=? WHERE rung_id=?",
            (RUNG_ABANDONED, json.dumps({"abandon_detail": detail}), rung_id),
        )

    def delete_submitting(self, rung_id: str) -> None:
        """Remove a `submitting` anchor after a CLEAN stand-down (every attempt
        confirmed dead, nothing filled, nothing working). Guarded on status so it
        can never delete a promoted/closing rung."""
        self.conn.execute(
            "DELETE FROM mace_rung WHERE rung_id=? AND status=?",
            (rung_id, RUNG_SUBMITTING),
        )


def _f(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── outcome records (returned to the manager + asserted in tests) ─────────

@dataclass(frozen=True)
class EntryOutcome:
    rung_id: str
    filled: bool
    credit: Optional[float] = None
    attempts: int = 0
    order_id: Optional[str] = None
    standdown_reason: Optional[str] = None  # cutoff|credit_floor_drift|exhausted|error|unconfirmed|partial|unpriceable


@dataclass(frozen=True)
class ExitOutcome:
    rung_id: str
    closed: bool
    reason: Optional[str] = None
    exit_debit: Optional[float] = None
    realized_pnl: Optional[float] = None
    attempts: int = 0
    exhausted: bool = False   # ladder ran out / unconfirmed / error -> stays CLOSING + URGENT
    pt_race: bool = False     # PT filled during the pre-exit cancel-and-confirm
    aborted: bool = False     # could not confirm PT dead -> refused to double-close


# ── the executor ──────────────────────────────────────────────────────────

class MaceExecutor:
    """Drives the async port through the entry/exit/PT/reconcile lifecycle and
    writes `mace_rung`. Constructed from a MaceConfig + injected port + store +
    notifier (no singletons, no yaml re-reads — the manager owns wiring). Clocks
    and poll cadence are injected for deterministic tests."""

    def __init__(
        self,
        cfg: MaceConfig,
        port: OptionsBrokerPort,
        store: RungStore,
        notifier: MaceNotifier,
        *,
        risk_gate: Optional[Callable[[CondorSpec, int, str], bool]] = None,
        audit: Optional[Callable[..., None]] = None,
        now_utc_fn: Callable[[], datetime] = now_utc,
        now_et_fn: Callable[[], datetime] = now_et,
        poll_interval_s: float = 1.0,
        poll_timeout_s: float = 30.0,
    ) -> None:
        self.cfg = cfg
        self.port = port
        self.store = store
        self.notifier = notifier
        # The per-leg risk gate: risk_gate(spec, contracts, direction) -> bool.
        # REQUIRED for any placement — None is fail-closed (see _require_risk).
        # The manager builds it from RiskAgent.evaluate over every leg.
        self._risk_gate = risk_gate
        self._audit_fn = audit
        self._now_utc = now_utc_fn
        self._now_et = now_et_fn
        self._poll_interval_s = poll_interval_s
        self._poll_timeout_s = poll_timeout_s

    # -- small helpers --------------------------------------------------------
    def _utc_iso(self) -> str:
        return self._now_utc().isoformat(timespec="seconds")

    def _audit(self, kind: str, **payload) -> None:
        if self._audit_fn is not None:
            try:
                self._audit_fn(kind, **payload)
            except Exception:  # noqa: BLE001 — audit must never break the loop
                _LOG.exception("mace audit hook failed: %s", kind)
        _LOG.info("mace.%s %s", kind, payload)

    async def _sleep(self, secs: float) -> None:
        if secs > 0:
            await asyncio.sleep(secs)

    async def _fresh_quotes(self, spec: CondorSpec) -> dict[str, Optional[OptionQuote]]:
        sp = await self.port.leg_quote(spec.symbol, spec.expiry, "put", spec.short_put)
        lp = await self.port.leg_quote(spec.symbol, spec.expiry, "put", spec.long_put)
        sc = await self.port.leg_quote(spec.symbol, spec.expiry, "call", spec.short_call)
        lc = await self.port.leg_quote(spec.symbol, spec.expiry, "call", spec.long_call)
        return {"sp": sp, "lp": lp, "sc": sc, "lc": lc}

    @staticmethod
    def _credit_mid(q: dict[str, Optional[OptionQuote]]) -> Optional[float]:
        """Net credit at mid = (short mids) − (long mids). None if any leg
        unpriceable. Also the management MARK (cost-to-close at mid)."""
        legs = (q["sp"], q["lp"], q["sc"], q["lc"])
        if any(x is None or x.mid is None for x in legs):
            return None
        return (q["sp"].mid - q["lp"].mid) + (q["sc"].mid - q["lc"].mid)

    @staticmethod
    def _natural_debit(q: dict[str, Optional[OptionQuote]]) -> Optional[float]:
        """Net natural debit to close = buy shorts @ ask, sell wings @ bid. None
        if any required side is missing."""
        sp, lp, sc, lc = q["sp"], q["lp"], q["sc"], q["lc"]
        if sp is None or lp is None or sc is None or lc is None:
            return None
        if sp.ask is None or sc.ask is None or lp.bid is None or lc.bid is None:
            return None
        return (sp.ask + sc.ask) - (lp.bid + lc.bid)

    async def mark(self, spec: CondorSpec) -> Optional[float]:
        """Per-contract cost-to-close at mid, for the management loop's stop
        compare (strategy owns the precedence; execution owns the fresh quotes)."""
        return self._credit_mid(await self._fresh_quotes(spec))

    async def _poll_until_terminal(self, order_id: str) -> Optional[OrderResult]:
        """Poll `order_status` to a terminal state. Returns the terminal
        OrderResult, or the last non-terminal one (or None) if it never confirmed
        within the bounded budget — the caller treats a non-terminal/None return
        as UNCONFIRMED and refuses to place a second order (double-fill guard).
        An exception is swallowed and retried (never books)."""
        polls = max(1, int(self._poll_timeout_s / max(self._poll_interval_s, 1e-9)))
        last: Optional[OrderResult] = None
        for _ in range(polls):
            try:
                r = await self.port.order_status(order_id)
            except Exception as exc:  # noqa: BLE001
                self._audit("mace_poll_error", order_id=order_id, error=str(exc))
                await self._sleep(self._poll_interval_s)
                continue
            last = r
            if r.is_terminal:
                return r
            await self._sleep(self._poll_interval_s)
        return last

    def _pt_debit_for(self, credit: float) -> float:
        return round_to_tick(credit * self.cfg.management.pt_pct_of_credit,
                             self.cfg.execution.entry_tick_usd, mode="nearest")

    def _floor_for(self, spec: CondorSpec) -> float:
        return self.cfg.entry.credit_floor_pct_of_width * spec.width_dollars

    # -- SINGLE RISK CHOKEPOINT (every placement funnels through here) --------
    def _require_risk(self, spec: CondorSpec, contracts: int, direction: str) -> None:
        """The single-chokepoint guard. Fail-CLOSED: a missing gate raises (an
        unevaluated order is never placed), and any leg the gate rejects raises.
        Called by _place / _place_resting BEFORE any broker place — those are the
        ONLY two methods that touch port.place_condor / port.place_resting_close
        (pinned by the AST structural test)."""
        if self._risk_gate is None:
            raise MaceRiskRejected(
                f"{spec.symbol}: no risk gate wired — the MACE single-chokepoint "
                f"invariant refuses to place {direction} x{contracts}")
        try:
            approved = self._risk_gate(spec, contracts, direction)
        except Exception as exc:  # noqa: BLE001 — a gate error is a rejection
            raise MaceRiskRejected(
                f"{spec.symbol}: risk gate raised ({exc}) — refusing to place") from exc
        if not approved:
            raise MaceRiskRejected(
                f"{spec.symbol}: risk gate rejected {direction} x{contracts}")

    async def _place(self, spec: CondorSpec, contracts: int, net_limit: float,
                     combo_id: str, *, direction: str, time_in_force: str,
                     fill_timeout_s: float) -> OrderResult:
        """The ONLY method that calls port.place_condor. Risk-gates every leg first."""
        self._require_risk(spec, contracts, direction)
        return await self.port.place_condor(
            spec, contracts, net_limit, combo_id, direction=direction,
            time_in_force=time_in_force, fill_timeout_s=fill_timeout_s)

    async def _place_resting(self, spec: CondorSpec, contracts: int,
                             net_debit_limit: float, ref_id: str) -> str:
        """The ONLY method that calls port.place_resting_close. Risk-gates first
        (the resting PT is a net-debit close)."""
        self._require_risk(spec, contracts, bp.DIR_DEBIT)
        return await self.port.place_resting_close(spec, contracts, net_debit_limit, ref_id)

    # -- ENTRY LADDER ---------------------------------------------------------
    async def run_entry(self, ev, session_date: date) -> EntryOutcome:
        """Entry credit ladder (plan § Entry ladder). `ev` is a strategy
        EvalResult with `entered=True` carrying spec/contracts/max_risk_usd.
        Writes the durable `submitting` anchor first, walks the credit DOWN toward
        marketability across ≤ entry_max_attempts, books ONLY on a confirmed
        `filled`, and stands down (no fill = no trade) on floor-drift / cutoff /
        exhaustion."""
        spec: CondorSpec = ev.spec
        contracts: int = ev.contracts
        rung_id = spec.rung_id(session_date)
        x = self.cfg.execution
        floor = self._floor_for(spec)
        cutoff = dtime.fromisoformat(self.cfg.entry.entry_cutoff_et)

        self.store.insert_submitting(
            rung_id, spec, contracts, entry_ts=self._utc_iso(),
            entry_iso_week=iso_week(session_date), max_risk_usd=ev.max_risk_usd)
        self._audit("mace_entry_start", rung_id=rung_id, symbol=spec.symbol,
                    contracts=contracts, strikes=spec.strikes_label())

        last_price: Optional[float] = None
        for k in range(1, x.entry_max_attempts + 1):
            # 15:58 cutoff — every prior attempt was confirmed dead, so a clean
            # stand-down can delete the anchor (nothing filled, nothing working).
            if self._now_et().time() >= cutoff:
                return self._entry_standdown(spec, rung_id, k - 1, last_price,
                                             "cutoff", clean=True)

            quotes = await self._fresh_quotes(spec)
            credit_mid = self._credit_mid(quotes)
            if credit_mid is None:
                self._audit("mace_entry_unpriceable", rung_id=rung_id, attempt=k)
                continue  # bounded by attempts; no place without a fresh price

            raw = credit_mid - x.entry_start_offset_usd - (k - 1) * x.entry_tick_usd
            limit = round_to_tick(raw, x.entry_tick_usd, mode="down")
            if limit < floor:
                # Walking further down would cross the 0.30×width floor — stand down.
                return self._entry_standdown(spec, rung_id, k - 1, last_price,
                                             "credit_floor_drift", clean=True)

            last_price = limit
            combo_id = f"{rung_id}-a{k}"
            # Persist the prospective credit BEFORE placing (crash-recovery basis).
            self.store.set_pending_credit(rung_id, limit)

            try:
                res = await self._place(
                    spec, contracts, limit, combo_id, direction=bp.DIR_CREDIT,
                    time_in_force="gfd", fill_timeout_s=x.entry_fill_wait_sec)
            except MaceRiskRejected as rej:
                # Single-chokepoint catch: risk gate rejected -> NO order placed.
                # Clean stand-down (every prior attempt was confirmed dead).
                self._audit("mace_entry_risk_reject", rung_id=rung_id, attempt=k, detail=str(rej))
                self.notifier.reject(symbol=spec.symbol, detail=f"entry risk-rejected: {rej}")
                return self._entry_standdown(spec, rung_id, k - 1, last_price,
                                             "risk_reject", clean=True)
            except Exception as exc:  # noqa: BLE001
                # FAKE-FILL GUARD: an exception NEVER books. The order MIGHT exist
                # at the broker (lost response) -> leave the anchor for reconcile
                # to drain by combo_id; do NOT delete, do NOT place another.
                self._audit("mace_entry_error", rung_id=rung_id, attempt=k, error=str(exc))
                self.notifier.reject(symbol=spec.symbol,
                                     detail=f"entry attempt {k} error: {exc}")
                return EntryOutcome(rung_id, False, attempts=k, order_id=combo_id,
                                    standdown_reason="error")

            # Persist the broker order id the instant we have it (crash-recovery).
            if res.order_id:
                self.store.set_entry_order(rung_id, res.order_id)

            # confirmed filled -> book (the normal in-window fill).
            if res.is_filled:
                return await self._book_entry_fill(rung_id, spec, contracts, limit,
                                                   res, k)
            # a terminal partial = broken structure (naked legs) -> URGENT, never book.
            if self._is_partial(res):
                return self._entry_partial(spec, rung_id, k, res)

            oid = res.order_id
            if oid is not None:
                # not filled in the fill window -> cancel and confirm (cancel race).
                try:
                    await self.port.cancel(oid)
                except Exception as exc:  # noqa: BLE001
                    self._audit("mace_entry_cancel_error", rung_id=rung_id, order_id=oid, error=str(exc))
                confirmed = await self._poll_until_terminal(oid)
                if confirmed is not None and confirmed.is_filled:
                    # Filled in the cancel race — the ONE entry-side manual booking,
                    # guarded by confirmed state == "filled".
                    return await self._book_entry_fill(rung_id, spec, contracts,
                                                       limit, confirmed, k)
                if confirmed is not None and self._is_partial(confirmed):
                    return self._entry_partial(spec, rung_id, k, confirmed)
                if confirmed is None or not confirmed.is_terminal:
                    # UNCONFIRMED terminal -> the order may still be live. Placing
                    # another attempt could double-fill. Stand down THIS run;
                    # reconcile owns the anchor. Do NOT delete.
                    self.notifier.reject(
                        symbol=spec.symbol,
                        detail=f"entry attempt {k}: unconfirmed terminal on {oid}; reconcile owns")
                    self._audit("mace_entry_unconfirmed", rung_id=rung_id, attempt=k, order_id=oid)
                    return EntryOutcome(rung_id, False, attempts=k, order_id=oid,
                                        standdown_reason="unconfirmed")
                # confirmed dead (cancelled/rejected) -> next attempt.
            # no order id (and not filled): treat as a spent attempt -> next.

        # attempts exhausted, all confirmed dead -> clean stand-down.
        return self._entry_standdown(spec, rung_id, x.entry_max_attempts, last_price,
                                     "exhausted", clean=True)

    def _entry_standdown(self, spec: CondorSpec, rung_id: str, attempts: int,
                         last_price: Optional[float], reason: str,
                         *, clean: bool) -> EntryOutcome:
        if clean:
            # Every attempt was confirmed dead and nothing filled -> the anchor
            # never became a position; remove it so reconcile doesn't later abandon+alert.
            self.store.delete_submitting(rung_id)
        self.notifier.standdown(symbol=spec.symbol, attempts=attempts,
                                max_attempts=self.cfg.execution.entry_max_attempts,
                                last_price=last_price)
        self._audit("mace_entry_standdown", rung_id=rung_id, reason=reason,
                    attempts=attempts, last_price=last_price)
        return EntryOutcome(rung_id, False, attempts=attempts, standdown_reason=reason)

    def _entry_partial(self, spec: CondorSpec, rung_id: str, k: int,
                       res: OrderResult) -> EntryOutcome:
        # A partially-filled condor is a broken structure — never book it as a
        # clean open. Leave the anchor (reconcile/manual) and escalate URGENT.
        self.notifier.breaker(
            condition=f"{spec.symbol} ENTRY PARTIAL FILL — broken structure",
            lines=[f"rung {rung_id}", f"attempt {k}", f"order {res.order_id}",
                   f"filled {res.processed_quantity}"],
            suggested_action="inspect/flatten the partial legs manually")
        self._audit("mace_entry_partial", rung_id=rung_id, attempt=k,
                    order_id=res.order_id, processed=res.processed_quantity)
        return EntryOutcome(rung_id, False, attempts=k, order_id=res.order_id,
                            standdown_reason="partial")

    async def _book_entry_fill(self, rung_id: str, spec: CondorSpec, contracts: int,
                               credit: float, res: OrderResult, attempts: int) -> EntryOutcome:
        # FAKE-FILL GUARD: only reached with a confirmed state == "filled".
        entry_ts = self._utc_iso()
        self.store.promote_open(rung_id, credit_actual=credit,
                                entry_order_id=res.order_id or rung_id, entry_ts=entry_ts)
        max_risk = (spec.width_dollars - credit) * 100.0 * contracts
        pt_debit = self._pt_debit_for(credit)
        self.notifier.entry(
            symbol=spec.symbol, expiry=spec.expiry.isoformat(),
            sp=spec.short_put, lp=spec.long_put, sc=spec.short_call, lc=spec.long_call,
            contracts=contracts, credit=credit, floor=self._floor_for(spec),
            pt=pt_debit, max_risk=max_risk)
        self._audit("mace_entry_fill", rung_id=rung_id, credit=credit,
                    order_id=res.order_id, attempts=attempts, max_risk=max_risk)
        await self._ensure_pt(rung_id, spec, contracts, credit, pt_debit=pt_debit)
        return EntryOutcome(rung_id, True, credit=credit, attempts=attempts,
                            order_id=res.order_id)

    # -- RESTING-GTC PROFIT TARGET -------------------------------------------
    async def _ensure_pt(self, rung_id: str, spec: CondorSpec, contracts: int,
                         credit: float, *, pt_debit: Optional[float] = None) -> Optional[str]:
        """Place (or re-place) the resting GTC buy-to-close at pt_pct×credit. A
        failure alerts (non-urgent) and leaves pt_order_id NULL — the reconcile /
        manage loop re-places it next tick. Never blocks the entry booking."""
        if pt_debit is None:
            pt_debit = self._pt_debit_for(credit)
        ref = f"{rung_id}-pt"
        try:
            pt_id = await self._place_resting(spec, contracts, pt_debit, ref)
        except Exception as exc:  # noqa: BLE001
            self.notifier.reject(symbol=spec.symbol,
                                 detail=f"resting PT placement failed: {exc}; reconcile will retry")
            self._audit("mace_pt_error", rung_id=rung_id, error=str(exc))
            return None
        self.store.set_pt(rung_id, pt_id, pt_debit)
        self._audit("mace_pt_placed", rung_id=rung_id, pt_order_id=pt_id, pt_debit=pt_debit)
        return pt_id

    # -- EXIT: cancel PT first, then the emulated-market debit ladder ---------
    async def close_rung(self, rung: RungState, reason: str) -> ExitOutcome:
        """Close a whole condor for a management reason (stop/time/exdiv/gap).
        FIRST cancel-and-confirm the resting PT; if the PT filled in that race,
        book the PT exit and stop. Then walk a marketable DEBIT ladder UP from
        natural (≤ width×ceiling); exhaustion/unconfirmed/error leaves the rung
        CLOSING + URGENT (operator manual backstop). Never books on error/partial."""
        spec, contracts, rung_id = rung.spec, rung.contracts, rung.rung_id

        # 1) resting PT must be provably dead before we place any closing order.
        if rung.pt_order_id:
            try:
                await self.port.cancel(rung.pt_order_id)
            except Exception as exc:  # noqa: BLE001
                self._audit("mace_pt_cancel_error", rung_id=rung_id,
                            pt_order_id=rung.pt_order_id, error=str(exc))
            confirmed = await self._poll_until_terminal(rung.pt_order_id)
            if confirmed is not None and confirmed.is_filled:
                # PT filled during the race — book the PT exit and STOP (do NOT
                # place a second closing order that would double-fill).
                return self._book_pt_exit(rung)
            if confirmed is None or not confirmed.is_terminal:
                # Cannot prove the PT is dead -> a live PT + a new close would
                # double-fill. Abort this tick, retry next manage tick. URGENT.
                self.notifier.breaker(
                    condition=f"{spec.symbol} EXIT ABORTED — PT not confirmed cancelled",
                    lines=[f"rung {rung_id}", f"pt {rung.pt_order_id}", f"reason {reason}"],
                    suggested_action="verify/cancel the resting PT before close; will retry")
                self._audit("mace_exit_abort_pt_unconfirmed", rung_id=rung_id,
                            pt_order_id=rung.pt_order_id, reason=reason)
                return ExitOutcome(rung_id, False, reason=reason, aborted=True)
            # PT confirmed dead -> proceed.
            self.store.clear_pt(rung_id)

        # 2) mark CLOSING (crash-recoverable) then run the debit ladder.
        self.store.mark_closing(rung_id)
        self._audit("mace_exit_start", rung_id=rung_id, reason=reason)
        x = self.cfg.execution
        ceiling = spec.width_dollars * x.exit_hard_ceiling_mult_of_width

        for k in range(1, x.exit_max_attempts + 1):
            quotes = await self._fresh_quotes(spec)
            natural = self._natural_debit(quotes)
            if natural is None:
                self._audit("mace_exit_unpriceable", rung_id=rung_id, attempt=k)
                continue
            raw = natural + (k - 1) * x.entry_tick_usd
            limit = round_to_tick(raw, x.entry_tick_usd, mode="up")
            if limit > ceiling:
                limit = ceiling  # never pay more than max structural value (width)
            combo_id = f"{rung_id}-x{k}"

            try:
                res = await self._place(
                    spec, contracts, limit, combo_id, direction=bp.DIR_DEBIT,
                    time_in_force="gfd", fill_timeout_s=x.exit_fill_wait_sec)
            except MaceRiskRejected as rej:
                # Risk gate blocked the close (unexpected for a risk-reducing close);
                # fail-safe -> stay CLOSING + URGENT, never place.
                self._audit("mace_exit_risk_reject", rung_id=rung_id, attempt=k, detail=str(rej))
                return self._exit_exhausted(spec, rung_id, reason, k)
            except Exception as exc:  # noqa: BLE001
                # FAKE-FILL GUARD: never book. An in-flight order can't be safely
                # superseded -> stay CLOSING + URGENT manual backstop.
                self._audit("mace_exit_error", rung_id=rung_id, attempt=k, error=str(exc))
                self.notifier.error(loop="exit", exc=exc)
                return self._exit_exhausted(spec, rung_id, reason, k)

            if res.is_filled:
                return self._book_exit_fill(rung, reason, limit)
            if self._is_partial(res):
                return self._exit_partial(spec, rung_id, reason, k, res)

            oid = res.order_id
            if oid is not None:
                try:
                    await self.port.cancel(oid)
                except Exception as exc:  # noqa: BLE001
                    self._audit("mace_exit_cancel_error", rung_id=rung_id, order_id=oid, error=str(exc))
                confirmed = await self._poll_until_terminal(oid)
                if confirmed is not None and confirmed.is_filled:
                    return self._book_exit_fill(rung, reason, limit)
                if confirmed is not None and self._is_partial(confirmed):
                    return self._exit_partial(spec, rung_id, reason, k, confirmed)
                if confirmed is None or not confirmed.is_terminal:
                    # unconfirmed -> may still be live; do NOT place another.
                    self._audit("mace_exit_unconfirmed", rung_id=rung_id, attempt=k, order_id=oid)
                    return self._exit_exhausted(spec, rung_id, reason, k)
                # confirmed dead -> next attempt.
            # no order id -> next attempt.

        return self._exit_exhausted(spec, rung_id, reason, x.exit_max_attempts)

    def _exit_exhausted(self, spec: CondorSpec, rung_id: str, reason: str,
                        attempts: int) -> ExitOutcome:
        # Rung STAYS `closing` (already marked) — operator manual action is the backstop.
        self.notifier.close_exhausted(symbol=spec.symbol, expiry=spec.expiry.isoformat(),
                                      contracts=self._contracts_of(rung_id), attempts=attempts)
        self._audit("mace_exit_exhausted", rung_id=rung_id, reason=reason, attempts=attempts)
        return ExitOutcome(rung_id, False, reason=reason, attempts=attempts, exhausted=True)

    def _exit_partial(self, spec: CondorSpec, rung_id: str, reason: str, k: int,
                      res: OrderResult) -> ExitOutcome:
        self.notifier.breaker(
            condition=f"{spec.symbol} EXIT PARTIAL FILL — broken structure",
            lines=[f"rung {rung_id}", f"attempt {k}", f"order {res.order_id}",
                   f"filled {res.processed_quantity}"],
            suggested_action="inspect/flatten the remaining legs manually")
        self._audit("mace_exit_partial", rung_id=rung_id, attempt=k,
                    order_id=res.order_id, processed=res.processed_quantity)
        return ExitOutcome(rung_id, False, reason=reason, attempts=k, exhausted=True)

    def _contracts_of(self, rung_id: str) -> int:
        r = self.store.get(rung_id)
        return r.contracts if r else 0

    def _book_exit_fill(self, rung: RungState, reason: str, exit_debit: float) -> ExitOutcome:
        credit = rung.credit_actual or 0.0
        realized = (credit - exit_debit) * 100.0 * rung.contracts
        exit_ts = self._utc_iso()
        self.store.mark_closed(rung.rung_id, exit_reason=reason, exit_debit=exit_debit,
                               realized_pnl=realized, exit_ts=exit_ts)
        pct = (realized / (credit * 100.0 * rung.contracts) * 100.0) if credit > 0 and rung.contracts else None
        self.notifier.exit(symbol=rung.symbol, expiry=rung.expiry.isoformat(),
                           contracts=rung.contracts, reason=reason, debit=exit_debit,
                           pnl=realized, pct_of_credit=pct)
        self._audit("mace_exit_fill", rung_id=rung.rung_id, reason=reason,
                    exit_debit=exit_debit, realized=realized)
        return ExitOutcome(rung.rung_id, True, reason=reason, exit_debit=exit_debit,
                           realized_pnl=realized)

    def _book_pt_exit(self, rung: RungState) -> ExitOutcome:
        credit = rung.credit_actual or 0.0
        pt_debit = rung.pt_debit if rung.pt_debit is not None else self._pt_debit_for(credit)
        realized = (credit - pt_debit) * 100.0 * rung.contracts
        exit_ts = self._utc_iso()
        self.store.mark_closed(rung.rung_id, exit_reason=EXIT_PT, exit_debit=pt_debit,
                               realized_pnl=realized, exit_ts=exit_ts)
        pct = (realized / (credit * 100.0 * rung.contracts) * 100.0) if credit > 0 and rung.contracts else None
        self.notifier.exit(symbol=rung.symbol, expiry=rung.expiry.isoformat(),
                           contracts=rung.contracts, reason=EXIT_PT, debit=pt_debit,
                           pnl=realized, pct_of_credit=pct)
        self._audit("mace_pt_fill", rung_id=rung.rung_id, pt_debit=pt_debit, realized=realized)
        return ExitOutcome(rung.rung_id, True, reason=EXIT_PT, exit_debit=pt_debit,
                           realized_pnl=realized, pt_race=True)

    @staticmethod
    def _is_partial(res: Optional[OrderResult]) -> bool:
        if res is None:
            return False
        if res.state == bp.STATE_PARTIAL:
            return True
        # terminal, not a clean fill, yet some quantity processed = broken structure.
        return res.is_terminal and not res.is_filled and res.processed_quantity > 0

    # -- RECONCILE STATE MACHINE ---------------------------------------------
    async def reconcile(self, session_date: date) -> None:
        """(A) Poll each open rung's resting PT — a confirmed `filled` books the PT
        exit; an unexpected dead PT re-places; a missing PT is re-placed. (B) Drain
        `submitting` anchors (boot/crash): match by deterministic combo_id, promote
        confirmed fills, abandon+alert past the 2-session horizon. Fake-fill guard
        everywhere — an error/exception books nothing."""
        # (A) resting-PT lifecycle for open rungs.
        for rung in self.store.load_by_status(RUNG_OPEN):
            try:
                await self._reconcile_open_pt(rung)
            except Exception as exc:  # noqa: BLE001 — one rung must not sink the loop
                self._audit("mace_reconcile_pt_error", rung_id=rung.rung_id, error=str(exc))
                self.notifier.error(loop="reconcile", exc=exc)

        # (B) drain submitting anchors.
        try:
            open_ords = await self.port.open_orders()
        except Exception as exc:  # noqa: BLE001 — no working-order list this tick
            self._audit("mace_reconcile_open_orders_error", error=str(exc))
            open_ords = None
        for rung in self.store.load_by_status(RUNG_SUBMITTING):
            try:
                await self._drain_submitting(rung, open_ords, session_date)
            except Exception as exc:  # noqa: BLE001
                self._audit("mace_reconcile_drain_error", rung_id=rung.rung_id, error=str(exc))
                self.notifier.error(loop="reconcile", exc=exc)

    async def _reconcile_open_pt(self, rung: RungState) -> None:
        if not rung.pt_order_id:
            # open with no resting PT (e.g. a prior placement failed) -> place one.
            if rung.credit_actual:
                await self._ensure_pt(rung.rung_id, rung.spec, rung.contracts, rung.credit_actual)
            return
        try:
            r = await self.port.order_status(rung.pt_order_id)
        except Exception as exc:  # noqa: BLE001 — retry next tick, never book on error
            self._audit("mace_reconcile_pt_status_error", rung_id=rung.rung_id, error=str(exc))
            return
        if r.is_filled:
            self._book_pt_exit(rung)
            return
        if r.is_dead:
            # unexpectedly cancelled/rejected -> alert + re-place next.
            self.notifier.reject(symbol=rung.symbol,
                                 detail=f"resting PT {rung.pt_order_id} {r.state}; re-placing")
            self._audit("mace_reconcile_pt_dead", rung_id=rung.rung_id, state=r.state)
            self.store.clear_pt(rung.rung_id)
            if rung.credit_actual:
                await self._ensure_pt(rung.rung_id, rung.spec, rung.contracts, rung.credit_actual)
        # else still working -> leave.

    async def _drain_submitting(self, rung: RungState, open_ords: Optional[Sequence[OpenOrder]],
                                session_date: date) -> None:
        rid = rung.rung_id
        matched_working = []
        if open_ords is not None:
            matched_working = [o for o in open_ords
                               if o.ref_id and o.ref_id.startswith(rid)]

        # Candidate ids to status-check: the stored latest ref + any matched working.
        candidate_ids: list[str] = []
        if rung.entry_order_id:
            candidate_ids.append(rung.entry_order_id)
        for o in matched_working:
            if o.order_id not in candidate_ids:
                candidate_ids.append(o.order_id)

        filled: Optional[OrderResult] = None
        any_working = False
        for oid in candidate_ids:
            try:
                r = await self.port.order_status(oid)
            except Exception as exc:  # noqa: BLE001 — unknown -> treat as in-flight (safe)
                self._audit("mace_drain_status_error", rung_id=rid, order_id=oid, error=str(exc))
                any_working = True
                continue
            if r.is_filled:
                filled = r
                break
            if not r.is_terminal:
                any_working = True

        if filled is not None:
            credit = rung.credit_actual  # prospective limit stored per attempt
            if credit is None:
                # Filled but no credit basis recorded -> can't book safely. URGENT.
                self.notifier.breaker(
                    condition=f"{rung.symbol} FILLED on reconcile but credit basis unknown",
                    lines=[f"rung {rid}", f"order {filled.order_id}"],
                    suggested_action="set credit_actual / book manually")
                self._audit("mace_drain_no_credit", rung_id=rid, order_id=filled.order_id)
                return
            entry_ts = rung.entry_ts or self._utc_iso()
            self.store.promote_open(rid, credit_actual=credit,
                                    entry_order_id=filled.order_id or rung.entry_order_id,
                                    entry_ts=entry_ts)
            max_risk = (rung.spec.width_dollars - credit) * 100.0 * rung.contracts
            self.notifier.entry(
                symbol=rung.symbol, expiry=rung.expiry.isoformat(),
                sp=rung.spec.short_put, lp=rung.spec.long_put,
                sc=rung.spec.short_call, lc=rung.spec.long_call,
                contracts=rung.contracts, credit=credit, floor=self._floor_for(rung.spec),
                pt=self._pt_debit_for(credit), max_risk=max_risk)
            self._audit("mace_drain_promote", rung_id=rid, credit=credit, order_id=filled.order_id)
            await self._ensure_pt(rid, rung.spec, rung.contracts, credit)
            return

        if any_working or matched_working:
            return  # still in flight -> leave the anchor.

        # Nothing filled, nothing working -> abandon past the horizon (+ alert).
        entry_dt = to_et(rung.entry_ts)
        entry_date = entry_dt.date() if entry_dt else session_date
        if business_sessions_between(entry_date, session_date) > _ABANDON_HORIZON_SESSIONS:
            self.store.mark_abandoned(rid, "submitting past 2-session horizon; no fill")
            self.notifier.reject(symbol=rung.symbol,
                                 detail=f"rung {rid} abandoned — no fill past {_ABANDON_HORIZON_SESSIONS}-session horizon")
            self._audit("mace_drain_abandon", rung_id=rid, entry_date=entry_date.isoformat())
        # else within horizon -> leave for a later tick.
