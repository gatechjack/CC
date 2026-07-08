"""BitUnix position reconciler — trade-plan PR 5.

Stateless, idempotent async task that runs on a periodic cadence
(default 60s). Each tick:

  1. Enumerates open BitUnix positions via `broker.list_open_positions`.
  2. For each position, decides the new SL per the lifecycle:
       no legs filled         → SL stays at structural stop
       tp1 filled              → SL → entry price (breakeven)
       tp2 filled, no trail    → SL → tp1 price (post-TP2 floor)
       tp2 filled, trail beats → Chandelier trail (extreme ± trail_atr_mult×ATR)
  3. If the decision changes SL (and ratchets in the correct direction
     for the side), emits a `position_sl_update` audit row.
  4. Phase 4 ADDS a `broker.modify_position_tp_sl_order(...)` call. PR 5
     intentionally does NOT call the broker — the method raises
     NotImplementedError and the reconciler only logs intent.

Invariants:
  - Stateless: no in-process state between ticks.
  - Idempotent: re-running with no change is a no-op.
  - Reads broker truth for leg fills — never inferred from price + plan.
  - SL only ratchets (long: monotonically up; short: monotonically down).

In paper mode (today), the paper resolver treats trades as monolithic
(single `result` field) so `filled_legs` is always `[]` from
`list_open_positions`. The reconciler is therefore dormant in paper
mode — it runs cleanly but emits no audit rows until Phase 4 broker
fill state lands. Decision logic is exercised by unit tests that inject
`filled_legs` directly.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from trading_corp.agents.divisions.bitunix_bracket import (
    classify_exit_kind,
    classify_result,
)
from trading_corp.brokers.bitunix_symbols import (
    UnknownSymbolError,
    to_wire_format,
)
from trading_corp.persistence import db
from trading_corp.persistence.models import OpenPosition

log = logging.getLogger(__name__)

POSITION_SL_UPDATE_KIND = "position_sl_update"
RECONCILER_ACTOR = "bitunix_position_reconciler"


def _recon_actor(division: str | None = None) -> str:
    """Audit actor for the position-state reconciler, scoped per division when
    two bitunix accounts run live (Phase 1, 2026-06-29).

    ``division=None`` → the legacy single actor ``bitunix_position_reconciler``
    (byte-identical to pre-multi-account behavior; the only path used while one
    bitunix division is live). A non-None division → ``…:<division>`` so each
    account's two-consecutive-tick confirm (`_latest_position_state_payload`)
    and audit trail are isolated — a futures tick can never reset an SFP
    confirm, and vice versa. The WRITE and the READ use this same function, so
    they always agree.
    """
    return f"{RECONCILER_ACTOR}:{division}" if division else RECONCILER_ACTOR

# ── position-state reconciliation (Phase 3 Session A, separate concern) ─
# The SL lifecycle reconciler above (`reconciler_tick`) decides per-leg
# stop-loss moves on already-open positions. The position-state reconciler
# below (`reconcile_position_state`) is orthogonal: it compares bot-tracked
# live rows against broker truth to detect symmetry violations on startup
# or after reconnect — bot thinks open, broker doesn't (or vice versa).
POSITION_STATE_RECONCILED_KIND = "position_state_reconciled"
POSITION_STATE_DIVERGENCE_KIND = "position_state_divergence_detected"

# ── P2 auto-book + latch-release (2026-06-14) ───────────────────────────────
# A bot-owned tracked position that closed broker-side (missing_on_broker,
# result IS NULL) is auto-booked at the KNOWN stop level, and on a confirmed-
# clean tick the `_halt_new_orders` latch is released so the engine self-recovers
# WITHOUT a restart. Quick-fix scope: books at the KNOWN level (slippage-
# unreconciled ESTIMATE); the accurate signed-fetch-of-real-fill version is a
# separate BACKLOG item. Both actions require confirmation across TWO consecutive
# ticks (one empty get_pending_positions can be a transient API error, not a
# real flat) — the cross-tick memory lives in the audit trail (stays stateless).
AUTO_BOOK_SERVER_SIDE_CLOSE_KIND = "auto_book_server_side_close"
AUTO_BOOK_DEFERRED_KIND = "auto_book_deferred"
POSITION_STATE_HALT_RELEASED_KIND = "position_state_halt_released"

# ── D1 netted-close double-booking (2026-06-21) ─────────────────────────────
# When several stacked records share ONE server-side netted close, each record's
# auto-book must attribute only ITS share of the close, not the full netted qty —
# otherwise the PnL/fee are booked N times over. The real-fill auto-book books
# `min(record_qty, netted_close_qty)`: for a single record where the recorded qty
# ~ the closed qty (incl. a normal ~5% fill gap), min() == the close qty and the
# economics are BYTE-UNCHANGED vs the prior full-qty booking. A record qty that
# GROSSLY exceeds the netted close (>= this ratio) is a real data error (stale or
# duplicate record), not a fill gap — min() still caps it safely, but we FLAG it
# (log.warning, never defer/crash) so the anomaly is surfaced for review.
D1_QTY_ANOMALY_RATIO = 1.5

# ── D3 role-recording (2026-06-23) ──────────────────────────────────────────
# D3 fee-corroboration reference rates (venue-effective, mirror strategies.yaml
# bitunix_futures.fees taker_pct/maker_pct). Used ONLY to (1) classify a close
# fill that matches NEITHER a TP nor the SL order-id, and (2) flag role/fee
# disagreement — NEVER to derive the primary role (keeps role+fee independent).
D3_TAKER_FEE_REF = 0.00019
D3_MAKER_FEE_REF = 0.00014


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ReconcilerConfig:
    trail_atr_mult: float = 1.5
    atr_period: int = 14
    bar_history_limit: int = 200
    period_seconds: float = 60.0
    timeframe: str = "3m"

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "ReconcilerConfig":
        d = d or {}
        return cls(
            trail_atr_mult=float(d.get("trail_atr_mult", 1.5)),
            atr_period=int(d.get("atr_period", 14)),
            bar_history_limit=int(d.get("bar_history_limit", 200)),
            period_seconds=float(d.get("reconciler_period_seconds", 60.0)),
            timeframe=str(d.get("reconciler_timeframe", "3m")),
        )


@dataclass
class SLDecision:
    new_sl: float
    lifecycle_state: str  # "post_tp1" | "post_tp2_floor" | "post_tp2_trail"
    reason: str


def _leg_price(tp_plan: list[dict], leg: str) -> float | None:
    for entry in tp_plan or []:
        if entry.get("leg") == leg:
            p = entry.get("price")
            try:
                return float(p) if p is not None else None
            except (TypeError, ValueError):
                return None
    return None


def _atr_from_bars(bars: list[dict], period: int = 14) -> float | None:
    """Wilder's ATR over `period` bars. Returns None if insufficient.

    Mirrors `LiveBarCache.get_atr` so the reconciler doesn't depend on
    the in-memory cache — bar_history table is the authoritative source.
    `bars` is ascending by ts_ms; each dict has high/low/close.
    """
    if len(bars) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(bars)):
        b = bars[i]
        prev_c = float(bars[i - 1]["close"])
        trs.append(max(
            float(b["high"]) - float(b["low"]),
            abs(float(b["high"]) - prev_c),
            abs(float(b["low"]) - prev_c),
        ))
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _ratchets(side: str, current_sl: float, new_sl: float) -> bool:
    """Return True iff `new_sl` moves SL in the side-correct direction."""
    if side == "buy":
        return new_sl > current_sl
    return new_sl < current_sl


def decide_sl_action(
    position: OpenPosition,
    atr: float | None,
    extreme_since_tp2: float | None,
    config: ReconcilerConfig,
) -> SLDecision | None:
    """Pure decision function — returns None when no SL move is warranted.

    `extreme_since_tp2`:
      - For LONG (`side='buy'`): max(high) over bars since TP2 fill.
      - For SHORT (`side='sell'`): min(low) over bars since TP2 fill.
      - None if TP2 hasn't filled, no bars yet, or atr unknown — caller
        gets the post-TP2 floor without trail.
    """
    filled = set(position.filled_legs or [])

    if "tp1" not in filled:
        return None

    if "tp2" not in filled:
        candidate_sl = position.entry_price
        if not _ratchets(position.side, position.current_sl, candidate_sl):
            return None
        return SLDecision(
            new_sl=candidate_sl,
            lifecycle_state="post_tp1",
            reason="tp1 filled → SL to entry (breakeven)",
        )

    floor = _leg_price(position.tp_plan, "tp1")
    if floor is None:
        return None

    if extreme_since_tp2 is None or atr is None:
        candidate_sl = floor
        state = "post_tp2_floor"
        reason = "tp2 filled → SL to tp1 price (post-TP2 floor)"
    else:
        if position.side == "buy":
            trail_sl = extreme_since_tp2 - config.trail_atr_mult * atr
            candidate_sl = max(floor, trail_sl)
        else:
            trail_sl = extreme_since_tp2 + config.trail_atr_mult * atr
            candidate_sl = min(floor, trail_sl)
        if candidate_sl == floor:
            state = "post_tp2_floor"
            reason = "tp2 filled → SL to tp1 price (post-TP2 floor; trail not yet beats floor)"
        else:
            state = "post_tp2_trail"
            reason = (
                f"tp2 filled → Chandelier trail "
                f"({'max_high' if position.side == 'buy' else 'min_low'} "
                f"{'-' if position.side == 'buy' else '+'} "
                f"{config.trail_atr_mult}×ATR)"
            )

    if not _ratchets(position.side, position.current_sl, candidate_sl):
        return None

    return SLDecision(new_sl=candidate_sl, lifecycle_state=state, reason=reason)


def _load_recent_bars(
    db_url: str,
    symbol: str,
    timeframe: str,
    limit: int,
) -> list[dict]:
    """Read the most recent `limit` bars for `symbol` at `timeframe`, ascending
    by ts_ms. The `symbol` filter is REQUIRED (2026-06-25 multi-coin
    bitunix_bar_history migration: PK is now (symbol, ts_ms, timeframe)); without
    it the query would interleave other coins' bars and corrupt the SL series."""
    try:
        with db.connect(db_url) as conn:
            rows = conn.execute(
                "SELECT ts_ms, open, high, low, close, volume "
                "FROM bitunix_bar_history "
                "WHERE symbol = ? AND timeframe = ? "
                "ORDER BY ts_ms DESC LIMIT ?",
                (symbol, timeframe, int(limit)),
            ).fetchall()
    except Exception as e:
        log.warning("reconciler: bar history read failed: %s", e)
        return []
    return list(reversed([dict(r) for r in rows]))


def _log_position_sl_update(
    db_url: str,
    position: OpenPosition,
    decision: SLDecision,
    *,
    would_call_broker: bool = False,
) -> None:
    payload = {
        "order_id": position.order_id,
        "symbol": position.symbol,
        "side": position.side,
        "lifecycle_state": decision.lifecycle_state,
        "current_sl": position.current_sl,
        "new_sl": decision.new_sl,
        "reason": decision.reason,
        "filled_legs": list(position.filled_legs or []),
        "would_call_broker": would_call_broker,
    }
    try:
        with db.connect(db_url) as conn:
            conn.execute(
                "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?, ?, ?, ?)",
                (
                    _utc_now_iso(),
                    RECONCILER_ACTOR,
                    POSITION_SL_UPDATE_KIND,
                    json.dumps(payload, default=str),
                ),
            )
    except Exception as e:
        log.warning("reconciler: position_sl_update audit failed: %s", e)


async def reconciler_tick(
    broker: Any,
    db_url: str,
    config: ReconcilerConfig,
) -> int:
    """One reconciliation pass. Returns the count of audit rows emitted.

    Stateless: every input comes from `broker.list_open_positions` and
    `bitunix_bar_history`. Idempotent: a tick with no lifecycle change
    returns 0 and writes nothing.
    """
    try:
        positions = broker.list_open_positions(db_url)
    except Exception as e:
        log.warning("reconciler: list_open_positions failed: %s", e)
        return 0
    if not positions:
        return 0

    written = 0
    for pos in positions:
        # Per-position bar load keyed on the position's WIRE symbol. The
        # bitunix_bar_history table is (symbol, ts_ms, timeframe)-keyed
        # (2026-06-25 multi-coin migration), so bars MUST be symbol-scoped or
        # they interleave other coins and corrupt the trail. BTC-only today.
        bars = _load_recent_bars(
            db_url, _match_symbol_key(pos.symbol),
            config.timeframe, config.bar_history_limit,
        )
        atr = _atr_from_bars(bars, config.atr_period) if bars else None
        extreme = _extreme_since_tp2(pos, bars)
        decision = decide_sl_action(pos, atr, extreme, config)
        if decision is None:
            continue
        _log_position_sl_update(db_url, pos, decision, would_call_broker=False)
        written += 1
    return written


def _extreme_since_tp2(position: OpenPosition, bars: list[dict]) -> float | None:
    """Return max(high) for longs / min(low) for shorts over bars after
    the TP2 fill timestamp.

    PR 5 paper-mode reality: `position.filled_legs` is always `[]`, so
    this never resolves a real value. Phase 4 will populate per-leg
    fill timestamps on `OpenPosition`; for now we look for an optional
    `tp2_filled_ts` attribute on the position and fall back to None.
    """
    if "tp2" not in (position.filled_legs or []):
        return None
    tp2_ts = getattr(position, "tp2_filled_ts", None)
    if not tp2_ts or not bars:
        return None
    try:
        cutoff_ms = int(
            datetime.fromisoformat(tp2_ts.replace("Z", "+00:00")).timestamp() * 1000
        )
    except (TypeError, ValueError):
        return None
    after = [b for b in bars if int(b["ts_ms"]) >= cutoff_ms]
    if not after:
        return None
    if position.side == "buy":
        return max(float(b["high"]) for b in after)
    return min(float(b["low"]) for b in after)


async def run_reconciler_loop(
    broker: Any,
    db_url: str,
    config: ReconcilerConfig,
) -> None:
    """Forever-loop wrapper. Sleeps `period_seconds` between ticks.

    Exceptions per tick are logged and swallowed — one bad tick should
    never take the whole loop down. The stateless design means the next
    tick recovers automatically.
    """
    interval = max(1.0, float(config.period_seconds))
    log.info(
        "bitunix_position_reconciler: starting (interval=%.0fs, "
        "trail_atr_mult=%.2f, timeframe=%s)",
        interval, config.trail_atr_mult, config.timeframe,
    )
    while True:
        try:
            n = await reconciler_tick(broker, db_url, config)
            if n:
                log.info("bitunix_position_reconciler: emitted %d audit rows", n)
        except Exception:
            log.exception("bitunix_position_reconciler: tick failed (continuing)")
        await asyncio.sleep(interval)


# ─── Phase 3 Session A: position-state reconciler (NEW concern) ─────────


@dataclass
class PositionStateMatch:
    """Bot-tracked live row matches a broker position by symbol+side."""
    order_id: str
    symbol: str
    side: str           # "buy" | "sell"
    bot_qty: float
    broker_qty: float


@dataclass
class PositionStateMissingOnBroker:
    """Bot tracks an open live row, but broker has no matching position.

    Causes: (a) broker closed via TP/SL/liquidation while bot was off,
    (b) operator manually closed via BitUnix UI, (c) broker_order_id
    drift (different ONE_WAY mode toggled between sessions). Triggers
    a halt-and-alert; operator must reconcile manually.
    """
    order_id: str
    symbol: str
    side: str
    bot_qty: float


@dataclass
class PositionStateOrphanOnBroker:
    """Broker has an open position, but bot has no matching live row.

    Causes: (a) Path C row write failed silently after a successful
    broker entry (the swallow path in `_place_live`), (b) operator
    placed an order outside the bot, (c) broker auto-opened via
    a residual TP/SL. Triggers a halt-and-alert; reconciler must NOT
    auto-close (the position may be operator-intentional).
    """
    symbol: str
    broker_qty: float
    broker_side: str    # "buy" | "sell"


@dataclass
class PositionStateReconciliation:
    """Result of one position-state reconciler tick."""
    matches: list[PositionStateMatch] = field(default_factory=list)
    missing_on_broker: list[PositionStateMissingOnBroker] = field(
        default_factory=list,
    )
    orphan_on_broker: list[PositionStateOrphanOnBroker] = field(
        default_factory=list,
    )

    @property
    def has_divergence(self) -> bool:
        return bool(self.missing_on_broker or self.orphan_on_broker)

    def to_payload(self) -> dict[str, Any]:
        return {
            "match_count": len(self.matches),
            "missing_on_broker_count": len(self.missing_on_broker),
            "orphan_on_broker_count": len(self.orphan_on_broker),
            "missing_on_broker": [
                {"order_id": m.order_id, "symbol": m.symbol,
                 "side": m.side, "bot_qty": m.bot_qty}
                for m in self.missing_on_broker
            ],
            "orphan_on_broker": [
                {"symbol": o.symbol, "broker_qty": o.broker_qty,
                 "broker_side": o.broker_side}
                for o in self.orphan_on_broker
            ],
        }


def _load_tracked_live_rows(
    db_url: str, division: str | None = None
) -> list[dict[str, Any]]:
    """Read paper_trade_record rows where:
      * result IS NULL (position still open per bot)
      * extra_json.execution_mode == "live"  (Path C tag)
      * division == ``division`` when provided (per-account isolation, 2026-06-29)
    Returns parsed dicts; rows with malformed extra_json are skipped.

    ``division=None`` selects ALL live rows (legacy single-account behavior).
    A non-None division scopes to that division's rows ONLY — so a reconciler
    bound to one bitunix account never sees the other account's open rows
    (both observers already tag the `division` column: bitunix_sfp /
    bitunix_futures).
    """
    sql = (
        "SELECT order_id, symbol, side, qty, extra_json "
        "FROM paper_trade_record "
        "WHERE result IS NULL AND extra_json IS NOT NULL"
    )
    params: tuple[Any, ...] = ()
    if division is not None:
        sql += " AND division = ?"
        params = (division,)
    try:
        with db.connect(db_url) as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception as e:
        log.warning("reconciler: tracked-row read failed: %s", e)
        return []
    tracked: list[dict[str, Any]] = []
    for r in rows:
        try:
            extra = json.loads(r["extra_json"])
        except (TypeError, ValueError):
            continue
        if extra.get("execution_mode") != "live":
            continue
        tracked.append({
            "order_id": r["order_id"],
            "symbol": r["symbol"],
            "side": r["side"],
            "qty": float(r["qty"]),
        })
    return tracked


def _broker_side(qty: float) -> str:
    """Render the side from a signed broker qty (LONG → buy; SHORT → sell)."""
    return "buy" if qty > 0 else "sell"


def _match_symbol_key(symbol: str) -> str:
    """Canonical key for matching a bot-tracked symbol against a BitUnix broker
    symbol (P1 fix 2026-06-14).

    Bot rows store the internal display form (e.g. ``BTC/USDT.P``); BitUnix
    returns the wire form (``BTCUSDT``). Map BOTH to the wire form via the symbol
    SSOT (`bitunix_symbols`) so the same instrument compares equal — every TRADED
    symbol is in that map, so this generalizes beyond BTC without ad-hoc string
    slicing (which `bitunix_symbols` forbids). An unmapped symbol (e.g. a broker
    orphan in an instrument the bot doesn't trade) falls back to the upper-cased
    raw string: it still compares deterministically and surfaces as genuine
    divergence rather than crashing the reconciler.
    """
    if not symbol:
        return ""
    raw = symbol.strip()
    try:
        return to_wire_format(raw)
    except UnknownSymbolError:
        return raw.upper()


def _latest_position_state_payload(
    db_url: str, division: str | None = None
) -> dict[str, Any] | None:
    """The most recent position-state reconcile audit payload (reconciled OR
    divergence), parsed. Used to confirm a state across TWO consecutive ticks
    before auto-booking or releasing the halt — keeps the reconciler stateless
    (the cross-tick memory lives in the audit trail). None if no prior tick or on
    read error (callers treat None conservatively = 'not confirmed')."""
    try:
        with db.connect(db_url) as conn:
            row = conn.execute(
                "SELECT payload_json FROM audit_event "
                "WHERE actor = ? AND kind IN (?, ?) ORDER BY id DESC LIMIT 1",
                (_recon_actor(division), POSITION_STATE_RECONCILED_KIND,
                 POSITION_STATE_DIVERGENCE_KIND),
            ).fetchone()
    except Exception as e:
        log.warning("reconciler: latest-audit read failed: %s", e)
        return None
    if row is None or not row["payload_json"]:
        return None
    try:
        return json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return None


def _resolve_entry_price(extra: dict, ref_price: Any) -> Any:
    """ref-vs-fill (2026-06-22): PnL must book from the ACTUAL entry fill price,
    not the alert/reference price (a systematic per-trade error — e.g. 125b6f9e
    booked at ref 63465.3 vs the real fill 63413.6, 52pt off).

    The real entry fill (broker-observed VWAP) is captured at fill-time on the
    observer path and stored in `extra['actual_entry_fill_price']`. Prefer it
    here. FALLBACK to `ref_price` (entry_reference_price) for records that
    predate this fix OR have no real fill (paper rows book at the signal price by
    design) — never crashes, never mis-books a historical. Orthogonal to D1: D1
    fixes the QTY term (min(qty, q_close)); this fixes the ENTRY-PRICE term. They
    compose as pnl = (actual_entry_fill - vwap) * min(qty, q_close)."""
    aefp = extra.get("actual_entry_fill_price")
    try:
        if aefp is not None and float(aefp) > 0:
            return float(aefp)
    except (TypeError, ValueError):
        pass
    return ref_price


def _autobook_missing_close(db_url: str, order_id: str, now: str) -> str:
    """Auto-book a bot-owned position that closed broker-side, at the KNOWN stop
    level (the B1 server-side stop). Returns 'booked' | 'deferred' | 'skipped'.

    Determination (stored state ONLY — NO price fetch, per the §4 known-level
    scope): a broker-side close with `filled_legs` empty can only be the
    server-side STOP — TPs are bot-side reactive closes that would already be
    booked, so an empty filled_legs means no TP was reached. → book at
    stop_price, result='loss'. If a TP leg WAS reached (filled_legs non-empty)
    the remaining close is ambiguous (deeper TP vs ratcheted stop) → DEFER (leave
    NULL, flag for manual). Likewise defer if the stop level / entry is missing.

    The booked PnL is `(entry − level) × qty` for a short / `(level − entry) × qty`
    for a long — a KNOWN-LEVEL ESTIMATE (the real fill slips past the stop; trade 2
    showed ~138pt), so the row is flagged `result_source='auto_booked_from_stop_level'`,
    `pnl_basis='known_level_estimate'`, `slippage_unreconciled=true` for later true-up.
    """
    try:
        with db.connect(db_url) as conn:
            r = conn.execute(
                "SELECT side, qty, entry_reference_price, stop_price, extra_json "
                "FROM paper_trade_record WHERE order_id = ? AND result IS NULL",
                (order_id,),
            ).fetchone()
            if r is None:
                return "skipped"  # vanished or already booked
            try:
                extra = json.loads(r["extra_json"]) if r["extra_json"] else {}
            except (TypeError, ValueError):
                extra = {}
            filled_legs = extra.get("filled_legs") or []
            side = (r["side"] or "").lower()
            qty = float(r["qty"] or 0.0)
            entry = _resolve_entry_price(extra, r["entry_reference_price"])
            level = r["stop_price"] if r["stop_price"] is not None \
                else extra.get("stop_price")

            # ── close-reason determination ─────────────────────────────────
            if (filled_legs or level is None or float(level) <= 0
                    or entry is None or qty <= 0):
                reason = ("partial_tp_ambiguous" if filled_legs
                          else "no_stop_level_or_entry")
                conn.execute(
                    "UPDATE paper_trade_record SET extra_json = "
                    "json_set(extra_json, '$.autobook_deferred', ?) "
                    "WHERE order_id = ? AND result IS NULL",
                    (reason, order_id),
                )
                conn.execute(
                    "INSERT INTO audit_event (ts, actor, kind, payload_json) "
                    "VALUES (?, ?, ?, ?)",
                    (now, RECONCILER_ACTOR, AUTO_BOOK_DEFERRED_KIND,
                     json.dumps({"order_id": order_id, "reason": reason,
                                 "filled_legs": filled_legs})),
                )
                return "deferred"

            # ── stop-out auto-book at the KNOWN stop level (estimate) ──────
            level = float(level)
            entry = float(entry)
            pnl = ((entry - level) if side == "sell"
                   else (level - entry)) * qty
            mdr = extra.get("max_dollar_risk")
            try:
                r_mult = (pnl / float(mdr)) if mdr else None
            except (TypeError, ValueError, ZeroDivisionError):
                r_mult = None
            exit_side = "buy" if side == "sell" else "sell"
            # result from the PnL SIGN (no fees on the estimate → gross basis),
            # never a literal. exit_kind = 'stop': this path books AT the known
            # stop LEVEL by construction (filled_legs empty, no real fill).
            result_str = classify_result(net_pnl=None, gross_pnl=pnl)
            exit_kind = "stop"
            conn.execute(
                "UPDATE paper_trade_record SET "
                "  result = ?, result_ts = ?, result_price = ?, "
                "  actual_pnl_dollars = ?, actual_r_multiple = ?, "
                "  bars_to_resolution = NULL, "
                "  extra_json = json_set(extra_json, "
                "    '$.result_source', 'auto_booked_from_stop_level', "
                "    '$.pnl_basis', 'known_level_estimate', "
                "    '$.slippage_unreconciled', json('true'), "
                "    '$.exit_method', 'server_side_sl_B1', "
                "    '$.exit_side', ?, '$.autobook_level_type', ?, "
                "    '$.exit_kind', ?, '$.autobook_ts', ?) "
                "WHERE order_id = ? AND result IS NULL",
                (result_str, now, level, pnl, r_mult, exit_side, exit_kind,
                 exit_kind, now, order_id),
            )
            conn.execute(
                "INSERT INTO audit_event (ts, actor, kind, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (now, RECONCILER_ACTOR, AUTO_BOOK_SERVER_SIDE_CLOSE_KIND,
                 json.dumps({"order_id": order_id, "side": side, "entry": entry,
                             "stop_level": level, "qty": qty,
                             "pnl_estimate": pnl, "result": result_str,
                             "exit_kind": exit_kind,
                             "pnl_basis": "known_level_estimate",
                             "slippage_unreconciled": True})),
            )
        return "booked"
    except Exception as e:
        log.warning("reconciler: auto-book failed for order_id=%s: %s",
                    order_id, e)
        return "skipped"


def _iso_to_ms(ts: str | None) -> float | None:
    """ISO-8601 ts → epoch ms (for the since_ms close-fill window). None on error."""
    if not ts:
        return None
    try:
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp() * 1000.0
    except (TypeError, ValueError):
        return None


def _role_summary(maker_qty: float, taker_qty: float) -> str:
    """One-word role tag from POSITIVE per-fill evidence only. No positive
    maker/taker evidence → 'unknown' (NEVER a maker default — D3 fix)."""
    if maker_qty > 0 and taker_qty > 0:
        return "mixed"
    if taker_qty > 0:
        return "taker"
    if maker_qty > 0:
        return "maker"
    return "unknown"


def _aggregate_close_fills(
    fills: list[dict], *, tp_order_ids=None, sl_order_id=None,
) -> dict[str, Any]:
    """Volume-weighted aggregate of N close fills — B2-aware: any number of
    partial fills, each carrying its REAL per-fill fee. Returns:
      vwap_price  — Σ(price·qty)/Σqty (the real exit price, multi-fill VWAP)
      total_fee   — Σ(per-fill fee): the REAL summed fee, reflecting the actual
                    maker/taker mix — never computed from an assumed rate
      total_qty   — Σqty (the actual closed quantity)
      n_fills     — count of valid fills aggregated
      close_order_ids — venue order-ids of the close fills (for tp-vs-stop match)
      exit_role   — 'maker'|'taker'|'mixed'|'unknown' from ORDER SEMANTICS (which
                    order the bot placed: a resting POST_ONLY TP leg = maker, the
                    B1 stop / market reduce = taker), NOT the unreliable venue
                    roleType (D3 fix). A close fill matching neither TP nor SL
                    order-id is corroborated by its fee rate, never defaulted maker.
      fee_implied_role — 'maker'|'taker'|'unknown' from the AGGREGATE fee rate
                    (independent corroboration of exit_role; role+fee stay
                    independent so a fee-model error remains detectable).
      role_fee_mismatch — True iff exit_role and fee_implied_role are both
                    decisive (maker/taker) and disagree.
      maker_taker_mix — {maker_qty, taker_qty, maker_fraction}
    Pure — no I/O. Skips malformed / non-positive fills."""
    tp_ids = set(str(x) for x in (tp_order_ids or []))
    sl = str(sl_order_id) if sl_order_id else None
    notional = total_qty = total_fee = 0.0
    maker_qty = taker_qty = 0.0
    order_ids: list[str] = []
    n = 0
    for f in (fills or []):
        try:
            p = float(f.get("price") or 0.0)
            q = float(f.get("qty") or 0.0)
            fee = float(f.get("fee") or 0.0)
        except (TypeError, ValueError):
            continue
        if p <= 0 or q <= 0:
            continue
        notional += p * q
        total_qty += q
        total_fee += fee
        # D3: role from the ORDER the bot placed, not the venue roleType.
        oid = str(f.get("order_id") or "")
        if oid and oid in tp_ids:
            # resting POST_ONLY limit TP leg → maker
            maker_qty += q
        elif oid and sl and oid == sl:
            # B1 stop / market reduce → taker
            taker_qty += q
        else:
            # NO order-id match (manual close / unknown order): corroborate by
            # fee rate, NEVER default maker. Fee/price/qty unavailable → neither
            # bucket (stays 'unknown').
            if p > 0 and q > 0 and fee > 0:
                rate = fee / (p * q)
                if abs(rate - D3_TAKER_FEE_REF) <= abs(rate - D3_MAKER_FEE_REF):
                    taker_qty += q
                else:
                    maker_qty += q
        if oid:
            order_ids.append(oid)
        n += 1
    vwap = (notional / total_qty) if total_qty > 0 else 0.0
    role_qty = maker_qty + taker_qty
    exit_role = _role_summary(maker_qty, taker_qty)
    # Independent aggregate fee-implied role (corroboration only — never feeds
    # the primary exit_role above, so a fee-model error surfaces as a mismatch).
    if total_fee > 0 and notional > 0:
        agg_rate = total_fee / notional
        fee_implied_role = (
            "taker"
            if abs(agg_rate - D3_TAKER_FEE_REF) <= abs(agg_rate - D3_MAKER_FEE_REF)
            else "maker"
        )
    else:
        fee_implied_role = "unknown"
    role_fee_mismatch = (
        exit_role in ("maker", "taker")
        and fee_implied_role in ("maker", "taker")
        and exit_role != fee_implied_role
    )
    return {"vwap_price": vwap, "total_fee": total_fee,
            "total_qty": total_qty, "n_fills": n,
            "close_order_ids": order_ids,
            "exit_role": exit_role,
            "fee_implied_role": fee_implied_role,
            "role_fee_mismatch": role_fee_mismatch,
            "maker_taker_mix": {
                "maker_qty": round(maker_qty, 7),
                "taker_qty": round(taker_qty, 7),
                "maker_fraction": (maker_qty / role_qty) if role_qty > 0 else None,
            }}


async def _autobook_missing_close_real(
    broker: Any, db_url: str, order_id: str, now: str,
) -> str:
    """Accurate auto-book (#1): book a server-side close from the REAL exchange
    fill(s) — VWAP price, summed REAL per-fill fees, real PnL — replacing the
    optimistic known-level estimate. Returns 'booked' | 'deferred' | 'skipped'.

    Upgrade over `_autobook_missing_close` (which books at the stop LEVEL): on a
    clean stop-out, fetch the actual close fills via the broker's signed
    trade-history (`get_recent_close_fills`), aggregate N fills, and book the
    exact economics with `result_source='auto_booked_from_real_fill'`, clearing
    `slippage_unreconciled` and recording the observed slippage (real-fill vs the
    recorded stop level) so the slippage distribution accumulates.

    SAFETY NET — never leaves a close unbooked because the fetch failed: if the
    row is ambiguous (filled_legs / missing stop|entry) OR the signed fetch
    errors / returns no identifiable close fill, this DELEGATES to
    `_autobook_missing_close` (the deployed, byte-unchanged known-level estimate).
    """
    try:
        with db.connect(db_url) as conn:
            r = conn.execute(
                "SELECT side, qty, symbol, ts, entry_reference_price, stop_price, "
                "extra_json FROM paper_trade_record "
                "WHERE order_id = ? AND result IS NULL",
                (order_id,),
            ).fetchone()
        if r is None:
            return "skipped"  # vanished or already booked
        try:
            extra = json.loads(r["extra_json"]) if r["extra_json"] else {}
        except (TypeError, ValueError):
            extra = {}
        filled_legs = extra.get("filled_legs") or []
        side = (r["side"] or "").lower()
        qty = float(r["qty"] or 0.0)
        entry = _resolve_entry_price(extra, r["entry_reference_price"])
        level = r["stop_price"] if r["stop_price"] is not None \
            else extra.get("stop_price")

        # Ambiguous / missing inputs → defer or estimate via the UNCHANGED
        # deployed path (filled_legs → ambiguous deeper-TP-vs-stop; no stop/entry
        # → can't even estimate). The real fetch is only for a clean stop-out.
        if (filled_legs or level is None or float(level) <= 0
                or entry is None or qty <= 0):
            return _autobook_missing_close(db_url, order_id, now)

        # Clean stop-out → try the REAL close fill(s) (signed). Any failure or no
        # identifiable close fill falls back to the known-level estimate.
        exit_side = "buy" if side == "sell" else "sell"
        try:
            fills = await broker.get_recent_close_fills(
                symbol=r["symbol"], exit_side=exit_side,
                since_ms=_iso_to_ms(r["ts"]),
            )
        except Exception as e:
            log.warning("reconciler: real-fill fetch failed for %s: %s — "
                        "falling back to known-level estimate", order_id, e)
            return _autobook_missing_close(db_url, order_id, now)

        # D3: the bracket order-ids classify each close fill by the ORDER the bot
        # placed (TP legs = maker, B1 stop = taker) — computed here so the same
        # ids feed both the role aggregation and the classify_exit_kind call below
        # (no duplicate compute).
        tp_ids = list((extra.get("bracket_tp_order_ids") or {}).values())
        sl_id = extra.get("bracket_position_sl_order_id")
        agg = _aggregate_close_fills(
            fills, tp_order_ids=tp_ids, sl_order_id=sl_id,
        )
        if agg["n_fills"] <= 0 or agg["vwap_price"] <= 0 or agg["total_qty"] <= 0:
            return _autobook_missing_close(db_url, order_id, now)  # safety net

        entry = float(entry)
        level = float(level)
        vwap = float(agg["vwap_price"])
        # D1 (netted-close double-booking): `q_close` is the TOTAL qty closed by
        # the real netted fill(s); `closed_qty` is THIS record's attributed share,
        # capped at the close qty. When stacked records share one netted close,
        # each books min(its_qty, q_close) so the per-record PnL/fee sum to the
        # netted close ONCE, not N times. For a single record where recorded qty ~
        # the closed qty (incl. a normal fill gap), min() == q_close → BYTE-
        # UNCHANGED vs the prior full-qty booking.
        q_close = float(agg["total_qty"])
        closed_qty = min(qty, q_close)
        # FLAG (do not defer/crash) a record qty that grossly exceeds the netted
        # close: a real data error (stale/duplicate record), not a normal ~5% fill
        # gap. min() above still caps the booked economics safely.
        if qty > q_close * D1_QTY_ANOMALY_RATIO:
            log.warning(
                "reconciler D1: record qty %.10g grossly exceeds netted close "
                "qty %.10g (ratio %.3f >= %.2f) for order_id=%s — capping at "
                "close qty; possible stale/duplicate record or fill-history gap",
                qty, q_close, (qty / q_close) if q_close > 0 else float("inf"),
                D1_QTY_ANOMALY_RATIO, order_id,
            )
        # Exit fee is the proportional share of the netted close fee for THIS
        # record's attributed qty (== the full fee when closed_qty == q_close).
        exit_fee = float(agg["total_fee"]) * (closed_qty / q_close)
        # Gross price PnL on the REAL fill, attributed to THIS record's closed
        # share (same convention as the estimate, which used the stop level): a
        # short loses when the fill is above entry.
        pnl = ((entry - vwap) if side == "sell" else (vwap - entry)) * closed_qty
        # Observed slippage of the real fill vs the RECORDED stop level (signed:
        # positive = adverse / filled worse than the trigger).
        slip_pts = (vwap - level) if side == "sell" else (level - vwap)
        mdr = extra.get("max_dollar_risk")
        try:
            r_mult = (pnl / float(mdr)) if mdr else None
        except (TypeError, ValueError, ZeroDivisionError):
            r_mult = None
        try:
            entry_fee = float(extra.get("entry_fee_usd") or 0.0)
        except (TypeError, ValueError):
            entry_fee = 0.0
        net = pnl - entry_fee - exit_fee

        # result from the NET PnL sign; exit_kind from the ACTUAL fill (order-id
        # match → tp/stop, else price-vs-levels, else 'unknown' — NEVER a literal
        # 'loss'/'stop'). Fixes the P2 mis-sign (report 2026-06-19_p2_classifier).
        tp_prices = [extra.get("tp1_price"), extra.get("tp2_price"),
                     extra.get("tp3_price")]
        # tp_ids / sl_id already computed above the _aggregate_close_fills call
        # (D3) — reuse them here (no duplicate compute).
        result_str = classify_result(net_pnl=net, gross_pnl=pnl)
        exit_kind = classify_exit_kind(
            side=side, vwap_fill=vwap, stop_level=level, tp_prices=tp_prices,
            close_order_ids=agg.get("close_order_ids"),
            tp_order_ids=tp_ids, sl_order_id=sl_id,
        )
        exit_role = agg.get("exit_role", "unknown")
        mix_json = json.dumps(agg.get("maker_taker_mix") or {})
        # D3: independent fee-vs-order-semantics corroboration. Recorded as a bool
        # so a fee-model error (role and fee disagreeing) stays detectable.
        role_fee_mismatch = bool(agg.get("role_fee_mismatch"))

        with db.connect(db_url) as conn:
            conn.execute(
                "UPDATE paper_trade_record SET "
                "  result = ?, result_ts = ?, result_price = ?, "
                "  actual_pnl_dollars = ?, actual_r_multiple = ?, "
                "  bars_to_resolution = NULL, "
                "  extra_json = json_set(extra_json, "
                "    '$.result_source', 'auto_booked_from_real_fill', "
                "    '$.pnl_basis', 'real_fill', "
                "    '$.slippage_unreconciled', json('false'), "
                "    '$.exit_method', 'server_side_sl_B1', "
                "    '$.exit_side', ?, '$.autobook_level_type', ?, "
                "    '$.exit_kind', ?, '$.exit_role', ?, "
                "    '$.maker_taker_mix', json(?), "
                "    '$.fee_implied_role', ?, '$.role_fee_mismatch', json(?), "
                "    '$.exit_fee_usd', ?, '$.net_realized_usd', ?, "
                "    '$.close_fill_count', ?, '$.observed_slippage_pts', ?, "
                "    '$.autobook_ts', ?) "
                "WHERE order_id = ? AND result IS NULL",
                (result_str, now, vwap, pnl, r_mult, exit_side, exit_kind,
                 exit_kind, exit_role, mix_json,
                 agg.get("fee_implied_role", "unknown"),
                 json.dumps(role_fee_mismatch), exit_fee, net,
                 agg["n_fills"], slip_pts, now, order_id),
            )
            conn.execute(
                "INSERT INTO audit_event (ts, actor, kind, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (now, RECONCILER_ACTOR, AUTO_BOOK_SERVER_SIDE_CLOSE_KIND,
                 json.dumps({"order_id": order_id, "side": side, "entry": entry,
                             "stop_level": level, "vwap_fill": vwap,
                             "qty": closed_qty, "netted_close_qty": q_close,
                             "n_fills": agg["n_fills"], "exit_fee": exit_fee,
                             "pnl": pnl, "net_realized_usd": net,
                             "result": result_str, "exit_kind": exit_kind,
                             "exit_role": exit_role,
                             "fee_implied_role": agg.get(
                                 "fee_implied_role", "unknown"),
                             "role_fee_mismatch": role_fee_mismatch,
                             "pnl_basis": "real_fill",
                             "slippage_unreconciled": False,
                             "observed_slippage_pts": slip_pts})),
            )
        return "booked"
    except Exception as e:
        log.warning("reconciler: real auto-book failed for order_id=%s: %s — "
                    "falling back to known-level estimate", order_id, e)
        try:
            return _autobook_missing_close(db_url, order_id, now)
        except Exception:
            return "skipped"


async def reconcile_position_state(
    broker: Any,
    db_url: str,
    *,
    halt_on_divergence: bool = True,
    division: str | None = None,
) -> PositionStateReconciliation:
    """Compare bot-tracked live positions against broker truth.

    For each bot-tracked open live row (Path C tagged), find a matching
    broker position by (symbol, side). For each broker position, find a
    matching bot row. Surface mismatches:
      * matches            — bot + broker both have it (no action)
      * missing_on_broker  — bot has it, broker doesn't (HALT-AND-ALERT)
      * orphan_on_broker   — broker has it, bot doesn't (HALT-AND-ALERT)

    On any divergence (default `halt_on_divergence=True`), sets
    `broker._halt_new_orders = True` so no new entries can land while
    the discrepancy is unresolved. Exits are NOT halted (existing
    positions must be allowed to close per Phase 1a §9c).

    Writes a `position_state_reconciled` audit on a clean tick OR a
    `position_state_divergence_detected` audit on divergence. The audit
    payload carries the full diff so the operator's investigation
    doesn't need to re-run the reconciler.

    This function is independent of the SL lifecycle `reconciler_tick`
    above — they share a module because they're both BitUnix position
    reconciliation concerns, but the SL lifecycle operates on
    already-open positions (no symmetry check) and this one operates
    on the bot↔broker symmetry contract.

    Returns the `PositionStateReconciliation` so callers can act on
    the diff (e.g. surface in the dashboard).
    """
    # Broker truth — extracted public method per Decision 6.4(a).
    try:
        broker_positions = await broker.get_pending_positions()
    except Exception as e:
        log.warning(
            "reconcile_position_state: get_pending_positions failed "
            "(treating as 'no broker positions known'): %s", e,
        )
        broker_positions = []

    tracked = _load_tracked_live_rows(db_url, division=division)

    matches: list[PositionStateMatch] = []
    missing: list[PositionStateMissingOnBroker] = []
    matched_broker_keys: set[tuple[str, str]] = set()

    # Walk tracked rows; find broker match by (canonical symbol, side).
    # Symbols are normalized to the wire form so BTC/USDT.P (bot) matches
    # BTCUSDT (broker) — the P1 false-divergence fix (2026-06-14). Side comes
    # from the (now correctly signed) broker qty via `_broker_side`.
    for t in tracked:
        t_symbol_key = _match_symbol_key(t["symbol"])
        match = None
        for p in broker_positions:
            if _match_symbol_key(p.symbol) != t_symbol_key:
                continue
            if _broker_side(p.qty) != t["side"]:
                continue
            match = p
            break
        if match is None:
            missing.append(PositionStateMissingOnBroker(
                order_id=t["order_id"],
                symbol=t["symbol"],
                side=t["side"],
                bot_qty=t["qty"],
            ))
        else:
            matches.append(PositionStateMatch(
                order_id=t["order_id"],
                symbol=t["symbol"],
                side=t["side"],
                bot_qty=t["qty"],
                broker_qty=abs(match.qty),
            ))
            matched_broker_keys.add(
                (_match_symbol_key(match.symbol), _broker_side(match.qty))
            )

    # Walk broker positions; orphans = those not matched to any tracked row.
    orphans: list[PositionStateOrphanOnBroker] = []
    for p in broker_positions:
        key = (_match_symbol_key(p.symbol), _broker_side(p.qty))
        if key in matched_broker_keys:
            continue
        orphans.append(PositionStateOrphanOnBroker(
            symbol=p.symbol,
            broker_qty=abs(p.qty),
            broker_side=_broker_side(p.qty),
        ))

    # ── P2 auto-book: a bot-owned position missing on the broker (closed
    # server-side, result IS NULL) is booked — but ONLY when confirmed across two
    # consecutive ticks (a single empty get_pending_positions can be a transient
    # API error, not a real flat) and on a real (non-stub) broker. #1 upgrade:
    # `_autobook_missing_close_real` fetches the REAL close fill(s) (signed) and
    # books exact price/PnL/fee, falling back to the known-level estimate if the
    # fetch fails. Booked rows drop out of `missing` this tick, so the audit +
    # halt decision below reflect the post-book state.
    prev = _latest_position_state_payload(db_url, division=division)
    prev_missing_ids = {
        m.get("order_id") for m in (prev.get("missing_on_broker") or [])
    } if prev else set()
    prev_was_clean = bool(prev) and (
        prev.get("missing_on_broker_count", 1) == 0
        and prev.get("orphan_on_broker_count", 1) == 0
    )
    broker_live = not getattr(broker, "_stub", False)
    now = _utc_now_iso()
    if broker_live and missing and halt_on_divergence:
        still_missing: list[PositionStateMissingOnBroker] = []
        for m in missing:
            if m.order_id in prev_missing_ids:  # 2 consecutive ticks → confirmed
                booked = await _autobook_missing_close_real(
                    broker, db_url, m.order_id, now,
                )
                if booked == "booked":
                    continue  # resolved this tick — drop from missing
            still_missing.append(m)
        missing = still_missing

    result = PositionStateReconciliation(
        matches=matches,
        missing_on_broker=missing,
        orphan_on_broker=orphans,
    )

    # Audit row (write-after-side-effect is fine here — the side effect
    # is local-state derivation, and the audit IS the operator-facing
    # record of the check).
    audit_kind = (
        POSITION_STATE_DIVERGENCE_KIND
        if result.has_divergence
        else POSITION_STATE_RECONCILED_KIND
    )
    try:
        with db.connect(db_url) as conn:
            conn.execute(
                "INSERT INTO audit_event (ts, actor, kind, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (
                    _utc_now_iso(),
                    _recon_actor(division),
                    audit_kind,
                    json.dumps(result.to_payload(), default=str),
                ),
            )
    except Exception as e:
        log.warning(
            "reconcile_position_state: audit write failed (%s): %s",
            audit_kind, e,
        )

    # Halt-on-divergence: set the broker's self-halt latch so no new
    # entries can land until the discrepancy is resolved. Exits are
    # NOT halted (Phase 1a §9c). Guarded via getattr — different broker
    # adapters may not expose this attribute.
    if result.has_divergence and halt_on_divergence:
        try:
            if hasattr(broker, "_halt_new_orders"):
                broker._halt_new_orders = True
                if hasattr(broker, "_halt_reason"):
                    broker._halt_reason = "position_state_reconciler_divergence"
        except Exception as e:
            log.warning(
                "reconcile_position_state: halt-set failed: %s", e,
            )
    elif ((not result.has_divergence) and prev_was_clean and broker_live
          and halt_on_divergence):
        # ── latch-release (2026-06-14): this tick is clean AND the prior tick
        # was clean (two consecutive) → books reconciled + broker confirmed free
        # of divergence → release `_halt_new_orders` so the engine self-recovers
        # WITHOUT a restart. A genuine (or transient-hidden) orphan surfaces as
        # divergence → this branch never runs into a real unowned position. Stub
        # broker → no release (no live trading).
        try:
            if getattr(broker, "_halt_new_orders", False):
                broker._halt_new_orders = False
                if hasattr(broker, "_halt_reason"):
                    broker._halt_reason = None
                try:
                    with db.connect(db_url) as conn:
                        conn.execute(
                            "INSERT INTO audit_event "
                            "(ts, actor, kind, payload_json) VALUES (?, ?, ?, ?)",
                            (now, _recon_actor(division),
                             POSITION_STATE_HALT_RELEASED_KIND,
                             json.dumps(
                                 {"reason": "two_consecutive_clean_ticks"})),
                        )
                except Exception as ae:
                    log.warning(
                        "reconcile_position_state: halt-release audit "
                        "failed: %s", ae,
                    )
                log.info(
                    "reconcile_position_state: _halt_new_orders RELEASED "
                    "(two consecutive clean reconcile ticks)",
                )
        except Exception as e:
            log.warning(
                "reconcile_position_state: halt-release failed: %s", e,
            )

    # ── Bracket SL-move-on-TP-fill (price-only; venue auto-reduces qty) ──
    # Runs on the same 60s cadence; bracket-managed rows only; failure-tolerant;
    # never blocks the reconcile (a missed move leaves the SL at its prior price).
    try:
        await move_bracket_sls(broker, db_url, division=division)
    except Exception as e:
        log.warning("reconcile_position_state: bracket SL-move failed: %s", e)

    return result


# ─── Phase 3 Session B Commit 5 (5a): restart-resume cases (a)+(b) ──────


RESTART_RESUME_EXECUTED_KIND = "restart_resume_executed"
ORPHAN_BROKER_POSITION_ON_RESTART_KIND = "orphan_broker_position_on_restart"
RESTART_RESUME_CASE_C_DEFERRED_KIND = "restart_resume_case_c_deferred"


@dataclass
class RestartResumeSummary:
    matched: list[dict[str, Any]] = field(default_factory=list)
    orphan_on_broker: list[dict[str, Any]] = field(default_factory=list)
    case_c_deferred: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_orphan_or_case_c(self) -> bool:
        return bool(self.orphan_on_broker or self.case_c_deferred)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


async def move_bracket_sls(
    broker: Any, db_url: str, division: str | None = None
) -> None:
    """Bracket SL-move-on-TP-fill (the only ongoing bot exit logic in the
    exchange-resting bracket design).

    Detects a TP fill as a REDUCTION in the broker position qty vs the recorded
    entry qty, decides the new SL price per the (b)+(c) hybrid
    (`bitunix_bracket.decide_sl_move`), and moves it via
    `broker.modify_position_sl` (PRICE-ONLY — the venue auto-reduces SL qty).
    Fail-soft / failure-tolerant: a missed move leaves the SL at its prior,
    still-protective price and retries next tick (the TP already filled
    on-exchange, so no profit is lost). Acts ONLY on bracket-managed live rows
    (extra_json has `bracket_entry_qty`); all other positions are untouched.
    """
    from trading_corp.agents.divisions.bitunix_bracket import decide_sl_move

    _sql = (
        "SELECT order_id, symbol, side, qty, stop_price, "
        "entry_reference_price, extra_json FROM paper_trade_record "
        "WHERE result IS NULL AND extra_json IS NOT NULL"
    )
    _params: tuple[Any, ...] = ()
    if division is not None:
        _sql += " AND division = ?"
        _params = (division,)
    try:
        with db.connect(db_url) as conn:
            rows = conn.execute(_sql, _params).fetchall()
    except Exception as e:
        log.warning("bracket SL-move: tracked-row read failed: %s", e)
        return

    bracket_rows: list[tuple[Any, dict]] = []
    for r in rows:
        try:
            extra = json.loads(r["extra_json"])
        except (TypeError, ValueError):
            continue
        if extra.get("execution_mode") != "live":
            continue
        if "bracket_entry_qty" not in extra:
            continue
        bracket_rows.append((r, extra))
    if not bracket_rows:
        return

    try:
        positions = await broker.get_pending_positions()
    except Exception as e:
        log.warning("bracket SL-move: get_pending_positions failed: %s", e)
        return
    pos_qty: dict[tuple[str, str], float] = {}
    pos_id: dict[tuple[str, str], str] = {}
    for p in positions:
        key = (_match_symbol_key(p.symbol), _broker_side(p.qty))
        pos_qty[key] = abs(float(p.qty))
        pid = (p.extra or {}).get("positionId")
        if pid:
            pos_id[key] = str(pid)

    for r, extra in bracket_rows:
        side = r["side"]
        entry_qty = _safe_float(extra.get("bracket_entry_qty"), _safe_float(r["qty"]))
        if entry_qty <= 0:
            continue
        current_qty = pos_qty.get((_match_symbol_key(r["symbol"]), side), 0.0)
        if current_qty >= entry_qty - 1e-12:
            continue  # no TP fill detected this tick
        current_sl = _safe_float(extra.get("current_sl"), _safe_float(r["stop_price"]))
        # ref-vs-fill (2026-07-08): breakeven must be the ACTUAL entry fill, not
        # the signal reference — else the "breakeven" stop sits off by the entry
        # slippage (a stop-out after TP1 then books a small loss, not breakeven).
        # Prefer extra['actual_entry_fill_price'] (present on live futures rows,
        # already trusted by _resolve_entry_price for close P&L); fall back to the
        # reference for paper / pre-fix rows. Tighten-only guard downstream keeps
        # the move safe (long: up; short: down).
        entry_price = _safe_float(
            extra.get("actual_entry_fill_price"),
            _safe_float(extra.get("entry_reference_price"),
                        _safe_float(r["entry_reference_price"])),
        )
        tp1 = _safe_float(extra.get("tp1_price"))
        if tp1 <= 0:
            # fall back to the first bracket leg's price
            legs = extra.get("bracket_legs") or []
            if legs:
                tp1 = _safe_float(legs[0].get("price"))
        new_sl, why = decide_sl_move(
            side=side, entry_price=entry_price, current_sl=current_sl,
            tp1_price=tp1, entry_qty=entry_qty, current_qty=current_qty,
        )
        if new_sl is None:
            continue
        # Thread positionId from the broker Position.extra (required by the
        # corrected modify_position_sl; absent → fail-soft no-op inside the method).
        pos_key = (_match_symbol_key(r["symbol"]), side)
        broker_position_id: str | None = pos_id.get(pos_key)
        moved = False
        if hasattr(broker, "modify_position_sl"):
            try:
                moved = await broker.modify_position_sl(
                    r["symbol"], new_sl, position_id=broker_position_id,
                )
            except Exception as e:  # belt-and-suspenders — modify is fail-soft
                log.warning("bracket SL-move: modify raised: %s", e)
                moved = False
        # Persist the new SL only if it actually moved (so a failed move retries
        # next tick). Always audit (moved True/False) so the operator sees it.
        if moved:
            try:
                extra["current_sl"] = new_sl
                with db.connect(db_url) as conn:
                    conn.execute(
                        "UPDATE paper_trade_record SET extra_json=? WHERE order_id=?",
                        (json.dumps(extra, default=str), r["order_id"]),
                    )
            except Exception as e:
                log.warning("bracket SL-move: persist failed: %s", e)
        try:
            with db.connect(db_url) as conn:
                conn.execute(
                    "INSERT INTO audit_event(ts, actor, kind, payload_json) "
                    "VALUES(?, ?, ?, ?)",
                    (_utc_now_iso(), _recon_actor(division), "position_sl_update",
                     json.dumps({
                         "order_id": r["order_id"], "symbol": r["symbol"],
                         "new_sl": new_sl, "prev_sl": current_sl, "moved": moved,
                         "reason": why, "entry_qty": entry_qty,
                         "current_qty": current_qty, "source": "bracket_sl_move",
                     }, default=str)),
                )
        except Exception as e:
            log.warning("bracket SL-move: audit failed: %s", e)


async def resume_live_positions(
    broker: Any,
    db_url: str,
    *,
    halt_on_orphan_or_case_c: bool = True,
    notifier: Any = None,
    division: str | None = None,
) -> RestartResumeSummary:
    """Restart-resume cases (a) + (b) + (c-defer) per Phase 1b §4.

    On bot start, BEFORE the replay loop processes any record:
      Case (a): broker has position, bot has matching live row
                (matched by broker_order_id stamped by Path C, with
                symbol+side fallback). Already-tracked; clean continue.
      Case (b): broker has position, bot has NO matching row
                (orphan_on_broker). Path C should have prevented this
                (every live entry writes a row); when it fires it's an
                integrity violation. Halt-and-page; operator-resolve.
      Case (c): bot has row, broker has NO position. Defer per
                Phase 1b §4 (the position-state reconciler's
                missing_on_broker surface handles the audit + halt;
                this function adds a `restart_resume_case_c_deferred`
                audit + operator-page telegram for operator visibility).

    Cases (a) + (b) reuse `reconcile_position_state` matches +
    orphan_on_broker; this function adds the broker_order_id-aware
    matching pass + audit kinds + telegram per the Phase 1b spec.

    Per Phase 1a §9c: exits are NOT halted; only entries.
    """
    summary = RestartResumeSummary()

    # Reconcile by (symbol, side) first — reuse Session A logic.
    recon = await reconcile_position_state(
        broker, db_url, halt_on_divergence=False, division=division,
    )

    # Case (a): matches → already-tracked; one per match.
    for m in recon.matches:
        record = {
            "order_id": m.order_id,
            "symbol": m.symbol,
            "side": m.side,
            "bot_qty": m.bot_qty,
            "broker_qty": m.broker_qty,
        }
        summary.matched.append(record)
        try:
            with db.connect(db_url) as conn:
                conn.execute(
                    "INSERT INTO audit_event (ts, actor, kind, payload_json) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        _utc_now_iso(),
                        _recon_actor(division),
                        RESTART_RESUME_EXECUTED_KIND,
                        json.dumps(record, default=str),
                    ),
                )
        except Exception as e:
            log.warning(
                "resume_live_positions: matched audit failed: %s", e,
            )

    # Case (b): orphans on broker.
    for o in recon.orphan_on_broker:
        record = {
            "symbol": o.symbol,
            "broker_qty": o.broker_qty,
            "broker_side": o.broker_side,
            "kind": "orphan_on_broker",
        }
        summary.orphan_on_broker.append(record)
        try:
            with db.connect(db_url) as conn:
                conn.execute(
                    "INSERT INTO audit_event (ts, actor, kind, payload_json) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        _utc_now_iso(),
                        _recon_actor(division),
                        ORPHAN_BROKER_POSITION_ON_RESTART_KIND,
                        json.dumps(record, default=str),
                    ),
                )
        except Exception as e:
            log.warning(
                "resume_live_positions: orphan audit failed: %s", e,
            )

    # Case (c): bot tracks row but broker doesn't (= reconciler's
    # missing_on_broker). Defer per Phase 1b §4 — operator resolves.
    for m in recon.missing_on_broker:
        record = {
            "order_id": m.order_id,
            "symbol": m.symbol,
            "side": m.side,
            "bot_qty": m.bot_qty,
            "deferred_reason": (
                "broker may have closed via TP/SL/liquidation during "
                "downtime; operator must reconcile manually"
            ),
        }
        summary.case_c_deferred.append(record)
        try:
            with db.connect(db_url) as conn:
                conn.execute(
                    "INSERT INTO audit_event (ts, actor, kind, payload_json) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        _utc_now_iso(),
                        _recon_actor(division),
                        RESTART_RESUME_CASE_C_DEFERRED_KIND,
                        json.dumps(record, default=str),
                    ),
                )
        except Exception as e:
            log.warning(
                "resume_live_positions: case-c audit failed: %s", e,
            )

    # Halt latch for cases (b) and (c).
    if summary.has_orphan_or_case_c and halt_on_orphan_or_case_c:
        try:
            if hasattr(broker, "_halt_new_orders"):
                broker._halt_new_orders = True
                if hasattr(broker, "_halt_reason"):
                    broker._halt_reason = "restart_resume_orphan_or_case_c"
        except Exception as e:
            log.warning(
                "resume_live_positions: halt-set failed: %s", e,
            )

    # Telegram summary via the lifecycle notifier (best-effort).
    if notifier is not None and hasattr(notifier, "notify_restart_resume_executed"):
        try:
            await notifier.notify_restart_resume_executed(
                matched_count=len(summary.matched),
                orphan_count=len(summary.orphan_on_broker),
                case_c_count=len(summary.case_c_deferred),
            )
        except Exception as e:
            log.warning(
                "resume_live_positions: notifier push failed: %s", e,
            )

    return summary


# ─── Phase 3 Session B Commit 5 (5b): 60s sanity poll loop ──────────────


async def run_position_state_sanity_poll_loop(
    broker: Any,
    db_url: str,
    *,
    interval_s: float = 60.0,
    notifier: Any = None,
    division: str | None = None,
) -> None:
    """Forever-loop calling `reconcile_position_state` every `interval_s`.

    Catches drift that develops AFTER the startup check passed (e.g.
    broker auto-closed via liquidation while the bot was idle, or an
    operator manually adjusted positions on the BitUnix UI). On
    divergence: the reconciler already sets the halt latch; this loop
    ALSO emits a telegram via `notifier.notify_reconciliation_divergence`
    if the notifier is wired.

    Exceptions per tick are logged + swallowed (a bad tick must not
    kill the loop). Cancellation propagates so the runtime can stop
    the task cleanly.
    """
    # Floor at 0.001s for testability; prod callers pass 60.0.
    interval = max(0.001, float(interval_s))
    log.info(
        "bitunix position-state sanity poll: starting (interval=%.3fs)",
        interval,
    )
    while True:
        try:
            result = await reconcile_position_state(broker, db_url, division=division)
            if result.has_divergence and notifier is not None:
                if hasattr(notifier, "notify_reconciliation_divergence"):
                    for m in result.missing_on_broker:
                        try:
                            await notifier.notify_reconciliation_divergence(
                                order_id=m.order_id,
                                symbol=m.symbol,
                                kind="missing_on_broker",
                                detail=(
                                    f"bot tracks open row but broker "
                                    f"has no matching position "
                                    f"(side={m.side}, qty={m.bot_qty})"
                                ),
                            )
                        except Exception:
                            log.exception(
                                "sanity poll: divergence notify failed"
                            )
                    for o in result.orphan_on_broker:
                        try:
                            await notifier.notify_reconciliation_divergence(
                                order_id=None,
                                symbol=o.symbol,
                                kind="orphan_on_broker",
                                detail=(
                                    f"broker has position but bot does "
                                    f"not track it "
                                    f"(side={o.broker_side}, qty={o.broker_qty})"
                                ),
                            )
                        except Exception:
                            log.exception(
                                "sanity poll: divergence notify failed"
                            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "bitunix position-state sanity poll: tick failed "
                "(continuing)"
            )
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
