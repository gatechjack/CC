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

from trading_corp.persistence import db
from trading_corp.persistence.models import OpenPosition

log = logging.getLogger(__name__)

POSITION_SL_UPDATE_KIND = "position_sl_update"
RECONCILER_ACTOR = "bitunix_position_reconciler"

# ── position-state reconciliation (Phase 3 Session A, separate concern) ─
# The SL lifecycle reconciler above (`reconciler_tick`) decides per-leg
# stop-loss moves on already-open positions. The position-state reconciler
# below (`reconcile_position_state`) is orthogonal: it compares bot-tracked
# live rows against broker truth to detect symmetry violations on startup
# or after reconnect — bot thinks open, broker doesn't (or vice versa).
POSITION_STATE_RECONCILED_KIND = "position_state_reconciled"
POSITION_STATE_DIVERGENCE_KIND = "position_state_divergence_detected"


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
    timeframe: str,
    limit: int,
) -> list[dict]:
    """Read the most recent `limit` bars at `timeframe`, ascending by ts_ms."""
    try:
        with db.connect(db_url) as conn:
            rows = conn.execute(
                "SELECT ts_ms, open, high, low, close, volume "
                "FROM bitunix_bar_history "
                "WHERE timeframe = ? "
                "ORDER BY ts_ms DESC LIMIT ?",
                (timeframe, int(limit)),
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

    bars = _load_recent_bars(db_url, config.timeframe, config.bar_history_limit)
    atr = _atr_from_bars(bars, config.atr_period) if bars else None

    written = 0
    for pos in positions:
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


def _load_tracked_live_rows(db_url: str) -> list[dict[str, Any]]:
    """Read paper_trade_record rows where:
      * result IS NULL (position still open per bot)
      * extra_json.execution_mode == "live"  (Path C tag)
    Returns parsed dicts; rows with malformed extra_json are skipped.
    """
    try:
        with db.connect(db_url) as conn:
            rows = conn.execute(
                "SELECT order_id, symbol, side, qty, extra_json "
                "FROM paper_trade_record "
                "WHERE result IS NULL AND extra_json IS NOT NULL"
            ).fetchall()
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


async def reconcile_position_state(
    broker: Any,
    db_url: str,
    *,
    halt_on_divergence: bool = True,
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

    tracked = _load_tracked_live_rows(db_url)

    matches: list[PositionStateMatch] = []
    missing: list[PositionStateMissingOnBroker] = []
    matched_broker_keys: set[tuple[str, str]] = set()

    # Walk tracked rows; find broker match by (symbol, side).
    for t in tracked:
        match = None
        for p in broker_positions:
            if p.symbol != t["symbol"]:
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
            matched_broker_keys.add((match.symbol, _broker_side(match.qty)))

    # Walk broker positions; orphans = those not matched to any tracked row.
    orphans: list[PositionStateOrphanOnBroker] = []
    for p in broker_positions:
        key = (p.symbol, _broker_side(p.qty))
        if key in matched_broker_keys:
            continue
        orphans.append(PositionStateOrphanOnBroker(
            symbol=p.symbol,
            broker_qty=abs(p.qty),
            broker_side=_broker_side(p.qty),
        ))

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
                    RECONCILER_ACTOR,
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

    return result
