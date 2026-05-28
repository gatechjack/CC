"""Phase C of the would_have_placed enrichment (BACKLOG.md 2026-05-01).

Walks `paper_trade_record` rows where `result IS NULL` and replays the
post-alert price path to decide whether the trade would have hit its
take-profit, hit its stop-loss, or expired without resolution. Updates
the row's result_* columns in place.

Public entry points:
- `replay_pending_paper_trades(db_url, *, ohlcv_fetcher=None) -> dict`
  Single tick. Returns counts: scanned, resolved_win, resolved_loss,
  resolved_expired, marked_pre_phase_a, errors.
- `mark_pre_phase_a_rows(db_url) -> int`
  One-shot startup helper: marks rows that lack tp_price OR stop_price
  with `result='pre_phase_a'` so they're never re-scanned (the replay
  can't make a win/loss call without those fields).
- `start_replay_loop(db_url, *, interval_sec=900, ohlcv_fetcher=None) ->
  asyncio.Task` — spawn the periodic background tick. Mirrors the
  PMCC scan scheduler pattern in main.py.

**Tie-handling (conservative).** When a single 1m bar's high reaches
the take-profit AND the bar's low reaches the stop-loss, we cannot tell
intra-bar order from OHLC alone. We resolve to LOSS for longs (buy)
and LOSS for shorts (sell) — i.e. assume the worse outcome. This biases
the win-rate stat downward, which is the safer direction for an
auto_execute=true gating decision.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable

from trading_corp.persistence import db as _db

log = logging.getLogger(__name__)

# Type alias for the OHLCV fetcher we inject. Returns a list of
# [ts_ms, open, high, low, close, volume] entries, ccxt-shaped.
OhlcvFetcher = Callable[[str, str, int, int], Awaitable[list[list[float]]]]


@dataclass
class _PendingRow:
    order_id: str
    ts: str
    strategy: str
    division: str
    symbol: str
    side: str
    qty: float
    stop_price: float | None
    tp_price: float | None
    tp_r_multiple: float | None
    entry_reference_price: float | None
    expected_loss: float | None
    expected_gain: float | None
    max_hold_seconds: int | None
    # `extra_json` raw string from the DB. Parsed lazily by the
    # multi-leg classifier; legacy single-leg path ignores it.
    extra_json: str | None


@dataclass
class _Resolved:
    # 'win' | 'loss' | 'expired' | 'pre_phase_a' | 'still_open'
    #
    # 'still_open' is a TRANSIENT verdict — the trade has neither hit
    # TP/SL nor exhausted its max_hold window yet. Caller MUST NOT
    # update the row when it sees this; the row stays at result=NULL
    # so the next replay tick picks it up again (when more bars are
    # available from the venue).
    result: str
    result_ts: str | None
    result_price: float | None
    actual_pnl_dollars: float | None
    actual_r_multiple: float | None
    bars_to_resolution: int | None
    # Multi-leg only: snapshot of what to persist back into
    # `paper_trade_record.extra_json` so the next replay tick (and the
    # reconciler) see the updated lifecycle state. None for the
    # legacy single-leg path.
    extra_json_updates: dict | None = None


# ── public entry points ────────────────────────────────────────────────


def mark_pre_phase_a_rows(
    db_url: str = "sqlite:///data/trading_corp.db",
) -> int:
    """Set result='pre_phase_a' on rows that are missing tp_price OR
    stop_price (Phase A wasn't shipped at the alert time, so the replay
    can't decide win/loss). Idempotent: only updates rows where
    `result IS NULL`. Returns rows updated."""
    with _db.connect(db_url) as conn:
        cur = conn.execute(
            "UPDATE paper_trade_record SET result='pre_phase_a' "
            "WHERE result IS NULL AND (tp_price IS NULL OR stop_price IS NULL)"
        )
        return cur.rowcount or 0


def replay_pending_paper_trades(
    db_url: str = "sqlite:///data/trading_corp.db",
    *,
    ohlcv_fetcher: OhlcvFetcher | None = None,
) -> dict:
    """Single tick. Returns counts dict.

    Synchronous wrapper around the async core so callers in non-async
    contexts (CLI, tests) can use it cleanly. Inside the project's
    asyncio main loop, prefer `_replay_tick_async` directly.
    """
    return asyncio.run(
        _replay_tick_async(db_url, ohlcv_fetcher=ohlcv_fetcher)
    )


async def replay_pending_paper_trades_async(
    db_url: str = "sqlite:///data/trading_corp.db",
    *,
    ohlcv_fetcher: OhlcvFetcher | None = None,
) -> dict:
    """Async-native version for use inside the existing event loop."""
    return await _replay_tick_async(db_url, ohlcv_fetcher=ohlcv_fetcher)


def start_replay_loop(
    db_url: str = "sqlite:///data/trading_corp.db",
    *,
    interval_sec: int = 900,
    ohlcv_fetcher: OhlcvFetcher | None = None,
) -> asyncio.Task:
    """Spawn the periodic background replay task. Caller (main.py)
    is responsible for cancelling it on shutdown."""
    return asyncio.create_task(
        _replay_loop(db_url, interval_sec, ohlcv_fetcher),
        name="paper_trade_replay_loop",
    )


# ── core: walk-forward classifier (synchronous, pure-function) ─────────


def _classify(
    row: _PendingRow,
    bars: list[list[float]],
) -> _Resolved:
    """Walk OHLCV bars [ts_ms, o, h, l, c, v] forward from row.ts.
    Bars MUST be in ascending ts order and SHOULD start at or after
    row.ts. Returns the resolved verdict.

    For a 'buy' (long): TP if bar.high >= tp_price, SL if bar.low <=
    stop_price. For a 'sell' (short): TP if bar.low <= tp_price, SL
    if bar.high >= stop_price. Tie within a single bar resolves to
    LOSS — see module docstring."""
    if row.tp_price is None or row.stop_price is None:
        return _Resolved("pre_phase_a", None, None, None, None, None)

    side = (row.side or "").lower()
    tp = float(row.tp_price)
    sl = float(row.stop_price)
    expected_loss = float(row.expected_loss or 0.0)
    expected_gain = float(row.expected_gain or 0.0)
    tp_r = float(row.tp_r_multiple or 0.0)

    for idx, bar in enumerate(bars):
        if len(bar) < 5:
            continue
        ts_ms = int(bar[0])
        high = float(bar[2])
        low = float(bar[3])
        close = float(bar[4])
        bar_ts_iso = (
            datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            .isoformat(timespec="seconds")
        )

        if side == "buy":
            tp_hit = high >= tp
            sl_hit = low <= sl
        elif side == "sell":
            tp_hit = low <= tp
            sl_hit = high >= sl
        else:
            # Unknown side — bail out as expired-style.
            continue

        if tp_hit and sl_hit:
            # Same-bar both — assume worse: LOSS.
            return _Resolved(
                result="loss",
                result_ts=bar_ts_iso,
                result_price=sl,
                actual_pnl_dollars=expected_loss,  # already negative
                actual_r_multiple=-1.0,
                bars_to_resolution=idx + 1,
            )
        if tp_hit:
            return _Resolved(
                result="win",
                result_ts=bar_ts_iso,
                result_price=tp,
                actual_pnl_dollars=expected_gain,
                actual_r_multiple=tp_r if tp_r else None,
                bars_to_resolution=idx + 1,
            )
        if sl_hit:
            return _Resolved(
                result="loss",
                result_ts=bar_ts_iso,
                result_price=sl,
                actual_pnl_dollars=expected_loss,
                actual_r_multiple=-1.0,
                bars_to_resolution=idx + 1,
            )

    # Walked to the end of available bars without a hit. The verdict
    # depends on whether the full max_hold window has actually elapsed
    # in wall-clock time, NOT just whether we ran out of fetched bars:
    #   - elapsed >= max_hold  → genuinely expired
    #   - elapsed <  max_hold  → still open (more bars will arrive;
    #     the next replay tick will re-evaluate)
    # The previous version unconditionally returned 'expired', which
    # prematurely marked trades that were still inside their hold
    # window — caught 2026-05-11 on the bitunix paper trades.
    max_hold = int(row.max_hold_seconds or 0)
    alert_dt = _parse_row_ts(row.ts)
    now = datetime.now(timezone.utc)
    elapsed = (now - alert_dt).total_seconds() if alert_dt else 0
    fully_elapsed = max_hold > 0 and elapsed >= max_hold

    if not bars:
        last_ts_iso = row.ts
        last_close = None
        bars_n = 0
    else:
        last_bar = bars[-1]
        last_ts_iso = (
            datetime.fromtimestamp(int(last_bar[0]) / 1000.0, tz=timezone.utc)
            .isoformat(timespec="seconds")
        )
        last_close = float(last_bar[4])
        bars_n = len(bars)

    if not fully_elapsed:
        # Still inside the hold window — leave the row at result=NULL
        # so we re-evaluate on the next tick (more bars will be live
        # at the venue by then).
        return _Resolved(
            result="still_open",
            result_ts=last_ts_iso,
            result_price=last_close,
            actual_pnl_dollars=None,
            actual_r_multiple=None,
            bars_to_resolution=None,
        )

    return _Resolved(
        result="expired",
        result_ts=last_ts_iso,
        result_price=last_close,
        actual_pnl_dollars=0.0,
        actual_r_multiple=0.0,
        bars_to_resolution=bars_n,
    )


def _parse_row_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


# ── multi-leg (trade-plan v2) classifier ───────────────────────────────


def _leg_price(tp_plan: list[dict], leg: str) -> float | None:
    """Find the `price` field for the named leg in a v2 tp_plan."""
    for entry in tp_plan or []:
        if entry.get("leg") == leg:
            p = entry.get("price")
            try:
                return float(p) if p is not None else None
            except (TypeError, ValueError):
                return None
    return None


def _leg_fraction(tp_plan: list[dict], leg: str) -> float:
    for entry in tp_plan or []:
        if entry.get("leg") == leg:
            try:
                return float(entry.get("fraction") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _leg_target_r(tp_plan: list[dict], leg: str) -> float:
    for entry in tp_plan or []:
        if entry.get("leg") == leg:
            try:
                return float(entry.get("target_r") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _r_at_price(
    side: str, entry: float, original_sl: float, exit_price: float,
) -> float:
    """Realized R for the unfilled portion exiting at `exit_price`.
    R = (exit - entry) / |entry - original_sl|, signed by side."""
    risk_per_unit = abs(entry - original_sl)
    if risk_per_unit <= 0:
        return 0.0
    sign = 1.0 if side == "buy" else -1.0
    return sign * (exit_price - entry) / risk_per_unit


def _decide_lifecycle_sl(
    *,
    side: str,
    entry_price: float,
    original_sl: float,
    current_sl: float,
    filled_legs: list[str],
    tp_plan: list[dict],
) -> tuple[float, str | None, str | None]:
    """Floor-only lifecycle (no Chandelier trail in paper replay v1 —
    follow-up if data argues for it):
      - no tp filled → current_sl unchanged
      - tp1 filled, tp2 not → SL → entry (BE)
      - tp2 filled → SL → tp1 price (floor)
    Returns (new_sl, lifecycle_state, reason). lifecycle_state/reason are
    None when no transition is warranted (idempotent re-eval).
    The decision still respects the ratchet rule (long: monotone up;
    short: monotone down).
    """
    filled = set(filled_legs or [])

    def _ratchets(candidate: float) -> bool:
        if side == "buy":
            return candidate > current_sl
        return candidate < current_sl

    if "tp1" not in filled:
        return current_sl, None, None

    if "tp2" not in filled:
        if not _ratchets(entry_price):
            return current_sl, None, None
        return entry_price, "post_tp1", "tp1 filled → SL to entry (breakeven)"

    tp1_price = _leg_price(tp_plan, "tp1")
    if tp1_price is None:
        return current_sl, None, None
    if not _ratchets(tp1_price):
        return current_sl, None, None
    return tp1_price, "post_tp2_floor", "tp2 filled → SL to tp1 price (post-TP2 floor)"


def _aggregate_multi_leg_r(
    *,
    side: str,
    entry_price: float,
    original_sl: float,
    tp_plan: list[dict],
    filled_legs: list[str],
    exit_price: float,
) -> float:
    """Weighted R across the 3 legs.

    Each filled leg contributes its `target_r * fraction`; the unfilled
    remainder exits at `exit_price` and contributes
    `r_at(exit) * unfilled_fraction`. Mirrors the Option C worst-case
    arithmetic from the strategy_gaps memo (0.125 + 0.5 + 0.125 = 0.75R
    when tp2 hits and the runner stops at tp1-price)."""
    total_r = 0.0
    filled_fraction = 0.0
    for leg in ("tp1", "tp2", "tp3"):
        frac = _leg_fraction(tp_plan, leg)
        if leg in filled_legs:
            total_r += _leg_target_r(tp_plan, leg) * frac
            filled_fraction += frac
    unfilled_fraction = max(0.0, 1.0 - filled_fraction)
    if unfilled_fraction > 0:
        exit_r = _r_at_price(side, entry_price, original_sl, exit_price)
        total_r += exit_r * unfilled_fraction
    return total_r


def _classify_v2_multi_leg(
    row: _PendingRow,
    bars: list[list[float]],
    extra: dict,
) -> _Resolved:
    """v2 lifecycle replay: detect tp1 → tp2 → tp3 fills, advance SL
    per the Option C floor lifecycle, close the trade when SL is hit or
    tp3 fills or max_hold elapses. Emits `position_sl_update` audit rows
    at each lifecycle transition (via the reconciler's helper) so the
    dashboard reads the same telemetry whether the SL move came from
    paper-replay or live broker truth.

    Side effects: returns the lifecycle delta in `extra_json_updates` so
    the caller can persist it back into `paper_trade_record.extra_json`
    (filled_legs + current_sl). Audit rows are written inline as
    transitions happen — those persist regardless of whether the final
    row update lands.
    """
    tp_plan = extra.get("tp_plan") or []
    side = (row.side or "").lower()
    entry_price = float(
        row.entry_reference_price
        if row.entry_reference_price is not None
        else (extra.get("entry_reference_price") or 0.0)
    )
    original_sl = float(row.stop_price or 0.0)
    if entry_price <= 0 or original_sl <= 0 or not tp_plan:
        return _Resolved("pre_phase_a", None, None, None, None, None)

    # State at start of replay. `filled_legs` may carry rows from a
    # prior partial replay (e.g. tp1 filled on yesterday's tick, tp2 not
    # yet) — load from extra_json and resume.
    filled_legs: list[str] = list(extra.get("filled_legs") or [])
    current_sl_raw = extra.get("current_sl")
    try:
        current_sl = float(current_sl_raw) if current_sl_raw is not None else original_sl
    except (TypeError, ValueError):
        current_sl = original_sl

    expected_loss = float(row.expected_loss or 0.0)

    # Defensive: lazy-import the reconciler's audit helper so test
    # environments without the reconciler module wired still run the
    # classifier (audit writes become no-ops by exception swallow).
    _log_audit = _import_reconciler_logger()

    def _emit_audit(
        ts_iso: str,
        new_sl: float,
        lifecycle_state: str,
        reason: str,
    ) -> None:
        if _log_audit is None:
            return
        try:
            _log_audit(
                db_url=None,  # placeholder; outer _update_row writes via shared connection
                order_id=row.order_id,
                symbol=row.symbol,
                side=side,
                current_sl=current_sl,
                new_sl=new_sl,
                lifecycle_state=lifecycle_state,
                reason=reason,
                filled_legs=list(filled_legs),
                ts_iso=ts_iso,
            )
        except Exception as e:
            log.warning(
                "v2 replay: position_sl_update audit failed for order_id=%s: %s",
                row.order_id, e,
            )
        # Observability hook: queue a TP-fill lifecycle notification for
        # the post-tick async drain. `current_sl` here is still the OLD sl
        # (reassigned by the caller only after this returns).
        _queue_tp_fill_notification(
            row=row, side=side, entry_price=entry_price,
            original_sl=original_sl, tp_plan=tp_plan,
            filled_legs=filled_legs, old_sl=current_sl, new_sl=new_sl,
            lifecycle_state=lifecycle_state,
        )

    leg_targets = {leg: _leg_price(tp_plan, leg) for leg in ("tp1", "tp2", "tp3")}
    if any(p is None for p in leg_targets.values()):
        return _Resolved("pre_phase_a", None, None, None, None, None)

    for idx, bar in enumerate(bars):
        if len(bar) < 5:
            continue
        ts_ms = int(bar[0])
        high = float(bar[2])
        low = float(bar[3])
        bar_ts_iso = (
            datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            .isoformat(timespec="seconds")
        )

        # 1. Detect SL hit at start of bar (using the SL from prior bar's
        #    state — same convention as single-leg: bar's low/high vs
        #    current_sl). If both TP and SL would hit in the same bar, the
        #    conservative tie-handling from single-leg replay (assume worse)
        #    applies — check SL first.
        sl_hit_this_bar = (
            (side == "buy" and low <= current_sl) or
            (side == "sell" and high >= current_sl)
        )

        # 2. Detect leg fills in order (tp1 → tp2 → tp3).
        legs_filled_this_bar: list[str] = []
        for leg_name in ("tp1", "tp2", "tp3"):
            if leg_name in filled_legs:
                continue
            target = leg_targets[leg_name]
            hit = (
                (side == "buy" and high >= target) or
                (side == "sell" and low <= target)
            )
            if hit:
                legs_filled_this_bar.append(leg_name)
            else:
                break  # legs are ordered; once one doesn't hit, later ones haven't either

        # Tie handling for same-bar SL + TP: if a TP also hit in this bar,
        # the conservative assumption is SL hit FIRST (worse outcome).
        # Mirror single-leg semantics: bias to LOSS on ambiguous bars.
        if sl_hit_this_bar:
            # Close at SL price. Any legs that would have filled this bar
            # don't count (conservative).
            exit_price = current_sl
            actual_r = _aggregate_multi_leg_r(
                side=side, entry_price=entry_price,
                original_sl=original_sl, tp_plan=tp_plan,
                filled_legs=filled_legs, exit_price=exit_price,
            )
            actual_pnl = expected_loss * abs(actual_r) if actual_r < 0 else 0.0
            # Use a positive proxy for $-pnl on the partial-win path
            # (TP1 hit then SL at BE = small positive R; expected_gain
            # is a per-leg full-fill projection so we scale by realized R).
            if actual_r > 0:
                if row.expected_gain:
                    actual_pnl = float(row.expected_gain) * (actual_r / max(1e-9, float(row.tp_r_multiple or 1.0)))
                else:
                    log.warning(
                        "v2 replay: order_id=%s partial-win actual_r=%.4f but expected_gain missing; actual_pnl_dollars falling to 0",
                        row.order_id, actual_r,
                    )
            result = "win" if actual_r > 0 else "loss"
            return _Resolved(
                result=result,
                result_ts=bar_ts_iso,
                result_price=exit_price,
                actual_pnl_dollars=actual_pnl,
                actual_r_multiple=round(actual_r, 4),
                bars_to_resolution=idx + 1,
                extra_json_updates={
                    "filled_legs": filled_legs,
                    "current_sl": current_sl,
                },
            )

        # 3. Apply leg fills + lifecycle SL transitions.
        for leg_name in legs_filled_this_bar:
            filled_legs.append(leg_name)
            new_sl, lifecycle_state, reason = _decide_lifecycle_sl(
                side=side,
                entry_price=entry_price,
                original_sl=original_sl,
                current_sl=current_sl,
                filled_legs=filled_legs,
                tp_plan=tp_plan,
            )
            if lifecycle_state is not None:
                _emit_audit(bar_ts_iso, new_sl, lifecycle_state, reason or "")
                current_sl = new_sl

        # 4. If tp3 filled, trade is fully closed.
        if "tp3" in filled_legs:
            exit_price = leg_targets["tp3"]
            actual_r = _aggregate_multi_leg_r(
                side=side, entry_price=entry_price,
                original_sl=original_sl, tp_plan=tp_plan,
                filled_legs=filled_legs, exit_price=exit_price,
            )
            if row.expected_gain:
                actual_pnl = float(row.expected_gain) * (
                    actual_r / max(1e-9, float(row.tp_r_multiple or 1.0))
                )
            else:
                log.warning(
                    "v2 replay: order_id=%s TP3 filled (actual_r=%.4f) but expected_gain missing; actual_pnl_dollars falling to 0",
                    row.order_id, actual_r,
                )
                actual_pnl = 0.0
            return _Resolved(
                result="win",
                result_ts=bar_ts_iso,
                result_price=exit_price,
                actual_pnl_dollars=actual_pnl,
                actual_r_multiple=round(actual_r, 4),
                bars_to_resolution=idx + 1,
                extra_json_updates={
                    "filled_legs": filled_legs,
                    "current_sl": current_sl,
                },
            )

    # Walked all bars without finalization. Still-open vs expired check
    # mirrors the single-leg path.
    max_hold = int(row.max_hold_seconds or 0)
    alert_dt = _parse_row_ts(row.ts)
    now = datetime.now(timezone.utc)
    elapsed = (now - alert_dt).total_seconds() if alert_dt else 0
    fully_elapsed = max_hold > 0 and elapsed >= max_hold

    if not bars:
        last_ts_iso = row.ts
        last_close = None
        bars_n = 0
    else:
        last_bar = bars[-1]
        last_ts_iso = (
            datetime.fromtimestamp(int(last_bar[0]) / 1000.0, tz=timezone.utc)
            .isoformat(timespec="seconds")
        )
        last_close = float(last_bar[4])
        bars_n = len(bars)

    # Persist filled_legs / current_sl progress even on still_open so the
    # next replay tick resumes from the latched lifecycle state.
    extra_updates = {
        "filled_legs": filled_legs,
        "current_sl": current_sl,
    }

    if not fully_elapsed:
        return _Resolved(
            result="still_open",
            result_ts=last_ts_iso,
            result_price=last_close,
            actual_pnl_dollars=None,
            actual_r_multiple=None,
            bars_to_resolution=None,
            extra_json_updates=extra_updates,
        )

    # Expired with partial fills → realize R on the unfilled remainder
    # exiting at the last bar's close (best-available proxy).
    exit_price = last_close if last_close is not None else original_sl
    actual_r = _aggregate_multi_leg_r(
        side=side, entry_price=entry_price,
        original_sl=original_sl, tp_plan=tp_plan,
        filled_legs=filled_legs, exit_price=exit_price,
    )
    return _Resolved(
        result="expired",
        result_ts=last_ts_iso,
        result_price=last_close,
        actual_pnl_dollars=0.0 if not filled_legs else None,
        actual_r_multiple=round(actual_r, 4) if filled_legs else 0.0,
        bars_to_resolution=bars_n,
        extra_json_updates=extra_updates,
    )


def _import_reconciler_logger():
    """Lazy import of the reconciler's audit helper. Wrapped because
    test environments may not have the full module graph available."""
    try:
        from trading_corp.agents.divisions import bitunix_position_reconciler as _rec
        return _v2_audit_writer(_rec)
    except Exception as e:
        log.debug("v2 replay: reconciler import failed (audits will skip): %s", e)
        return None


def _v2_audit_writer(rec_module):
    """Adapt the reconciler's `_log_position_sl_update` to a flat-kwarg
    signature so the replay classifier doesn't need an OpenPosition /
    SLDecision dance for each emission."""
    POSITION_SL_UPDATE_KIND = rec_module.POSITION_SL_UPDATE_KIND
    RECONCILER_ACTOR = rec_module.RECONCILER_ACTOR

    def _write(
        *, db_url, order_id, symbol, side, current_sl, new_sl,
        lifecycle_state, reason, filled_legs, ts_iso,
    ):
        payload = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "lifecycle_state": lifecycle_state,
            "current_sl": current_sl,
            "new_sl": new_sl,
            "reason": reason,
            "filled_legs": list(filled_legs or []),
            "would_call_broker": False,
            "source": "paper_trade_replay",
        }
        # Use the module's own DB url — the replay tick passes it in
        # through a module-level var rather than threading it through
        # the classifier signature. Set at start of each tick.
        target_db = _REPLAY_DB_URL_CTX["db_url"]
        if target_db is None:
            return
        try:
            with _db.connect(target_db) as conn:
                conn.execute(
                    "INSERT INTO audit_event (ts, actor, kind, payload_json) "
                    "VALUES (?, ?, ?, ?)",
                    (ts_iso, RECONCILER_ACTOR, POSITION_SL_UPDATE_KIND, json.dumps(payload, default=str)),
                )
        except Exception as e:
            log.warning("v2 replay: audit write failed: %s", e)

    return _write


# Module-level context for db_url passed into the classifier via the
# audit-emission closure. Replay tick sets this at the start of each
# pass; tests can poke it directly.
_REPLAY_DB_URL_CTX: dict[str, str | None] = {"db_url": None}


# Lifecycle notifier (Telegram), wired once at startup via
# set_lifecycle_notifier(); None in tests/CLI unless injected. The
# classifier queues events into _NOTIFY_QUEUE during the (sync) bar walk;
# _replay_tick_async drains + sends them (async) after each pass.
# Observability-only — a queue/send failure never blocks the replay.
_LIFECYCLE_NOTIFIER: dict = {"notifier": None}
_NOTIFY_QUEUE: list[dict] = []


def set_lifecycle_notifier(notifier) -> None:
    """Wire the lifecycle notifier (or None to disable)."""
    _LIFECYCLE_NOTIFIER["notifier"] = notifier


def _queue_tp_fill_notification(
    *, row, side, entry_price, original_sl, tp_plan,
    filled_legs, old_sl, new_sl, lifecycle_state,
) -> None:
    """Assemble + queue a TP-fill notification. Never raises. Fires only
    on the tick where a leg transitions to filled (the classifier resumes
    from persisted filled_legs, so subsequent ticks don't re-emit)."""
    if _LIFECYCLE_NOTIFIER["notifier"] is None:
        return
    try:
        leg = "tp1" if lifecycle_state == "post_tp1" else "tp2"
        leg_price = _leg_price(tp_plan, leg)
        if leg_price is None:
            return
        r_so_far = _aggregate_multi_leg_r(
            side=side, entry_price=entry_price, original_sl=original_sl,
            tp_plan=tp_plan, filled_legs=filled_legs, exit_price=new_sl,
        )
        pct_closed = round(
            sum(_leg_fraction(tp_plan, leg_name) for leg_name in filled_legs) * 100
        )
        new_sl_label = (
            "breakeven" if lifecycle_state == "post_tp1" else "post-TP1 floor"
        )
        _NOTIFY_QUEUE.append({
            "kind": "tp_fill",
            "order_id": row.order_id,
            "symbol": row.symbol,
            "side": side,
            "leg": leg,
            "entry_price": entry_price,
            "leg_price": leg_price,
            "r_so_far": r_so_far,
            "old_sl": old_sl,
            "new_sl": new_sl,
            "new_sl_label": new_sl_label,
            "percent_closed": pct_closed,
        })
    except Exception as e:
        log.warning(
            "lifecycle tp-fill queue failed for %s: %s",
            getattr(row, "order_id", "?"), e,
        )


def _queue_close_out_notification(row, verdict, extra: dict) -> None:
    """Assemble + queue a close-out notification. Never raises."""
    if _LIFECYCLE_NOTIFIER["notifier"] is None:
        return
    try:
        tp_plan = extra.get("tp_plan") or []
        side = (row.side or "").lower()
        entry_price = float(
            row.entry_reference_price
            if row.entry_reference_price is not None
            else (extra.get("entry_reference_price") or 0.0)
        )
        filled = list(
            (verdict.extra_json_updates or {}).get("filled_legs")
            or extra.get("filled_legs") or []
        )
        result = verdict.result
        if result == "win" and "tp3" in filled:
            exit_reason = "TP3 hit"
        elif result == "win":
            exit_reason = "SL hit (after partial)"
        elif result == "loss":
            exit_reason = "SL hit"
        else:
            exit_reason = "max_hold"
        path: list[tuple] = [("Entry", entry_price, None)]
        for leg in ("tp1", "tp2", "tp3"):
            if leg in filled:
                lp = _leg_price(tp_plan, leg)
                if lp is None:
                    continue
                pct = abs((lp - entry_price) / entry_price * 100) if entry_price else 0.0
                path.append((leg.upper(), lp, pct))
        path.append(("Exit", None, None))
        held_seconds = None
        st = _parse_row_ts(row.ts)
        et = _parse_row_ts(verdict.result_ts)
        if st and et:
            held_seconds = max(0, int((et - st).total_seconds()))
        _NOTIFY_QUEUE.append({
            "kind": "close_out",
            "order_id": row.order_id,
            "symbol": row.symbol,
            "side": side,
            "result": result,
            "entry_price": entry_price,
            "exit_price": verdict.result_price,
            "exit_reason": exit_reason,
            "path": path,
            "r_multiple": verdict.actual_r_multiple,
            "pnl_dollars": verdict.actual_pnl_dollars,
            "held_seconds": held_seconds,
        })
    except Exception as e:
        log.warning(
            "lifecycle close-out queue failed for %s: %s",
            getattr(row, "order_id", "?"), e,
        )


async def _drain_notify_queue() -> None:
    """Send all queued lifecycle notifications, then clear. Never raises —
    the notifier's own send is also failure-safe; this wrapper guards
    against malformed-event formatting errors so the replay can't break."""
    notifier = _LIFECYCLE_NOTIFIER["notifier"]
    events = list(_NOTIFY_QUEUE)
    _NOTIFY_QUEUE.clear()
    if notifier is None or not events:
        return
    for ev in events:
        try:
            kind = ev.pop("kind", None)
            if kind == "tp_fill":
                await notifier.notify_tp_fill(**ev)
            elif kind == "close_out":
                await notifier.notify_close_out(**ev)
        except Exception as e:
            log.warning("lifecycle notify drain failed: %s", e)


# ── async core ─────────────────────────────────────────────────────────


async def _replay_tick_async(
    db_url: str,
    *,
    ohlcv_fetcher: OhlcvFetcher | None,
) -> dict:
    fetcher = ohlcv_fetcher or _default_router_fetcher

    # Mark pre-Phase-A rows first so we don't try to fetch bars for them.
    pre_phase_a_marked = mark_pre_phase_a_rows(db_url)

    # Stash db_url for the v2 audit-emission closure (avoids threading
    # it through the pure classifier signature).
    _REPLAY_DB_URL_CTX["db_url"] = db_url
    _NOTIFY_QUEUE.clear()

    pending = _load_pending(db_url)
    counts = {
        "scanned": len(pending),
        "resolved_win": 0,
        "resolved_loss": 0,
        "resolved_expired": 0,
        "marked_pre_phase_a": pre_phase_a_marked,
        "v2_partial_progress": 0,    # tp1+ filled but trade not yet closed
        "errors": 0,
    }

    for row in pending:
        try:
            since_ts_ms = _iso_to_ms(row.ts)
            max_hold = int(row.max_hold_seconds or 0)
            if max_hold <= 0:
                # No window configured — can't bound the fetch. Skip.
                counts["errors"] += 1
                continue
            bars_needed = max(1, max_hold // 60)  # 1m bars
            bars = await fetcher(row.symbol, "1m", since_ts_ms, bars_needed)

            # Route on v2 marker in extra_json. Legacy single-leg path
            # stays the default for Otter / Cypher / Donchian / pre-PR-4
            # bitunix trades.
            extra = _parse_extra(row.extra_json)
            is_v2 = (
                row.division == "bitunix_futures"
                and bool(extra.get("tp_plan"))
                and extra.get("tp_plan_version") == "v2"
            )
            if is_v2:
                verdict = _classify_v2_multi_leg(row, bars, extra)
            else:
                verdict = _classify(row, bars)
            if verdict.result == "win":
                counts["resolved_win"] += 1
            elif verdict.result == "loss":
                counts["resolved_loss"] += 1
            elif verdict.result == "expired":
                counts["resolved_expired"] += 1
            elif verdict.result == "pre_phase_a":
                counts["marked_pre_phase_a"] += 1
            elif verdict.result == "still_open":
                # Inside max_hold window — leave the result column NULL.
                # BUT if multi-leg lifecycle advanced (filled_legs or
                # current_sl changed), persist the delta back into
                # extra_json so the next tick + the reconciler resume
                # from the right state.
                if verdict.extra_json_updates:
                    delta = _extra_json_delta(extra, verdict.extra_json_updates)
                    if delta is not None:
                        _persist_extra_json(db_url, row.order_id, delta)
                        if delta.get("filled_legs"):
                            counts["v2_partial_progress"] += 1
                counts.setdefault("still_open", 0)
                counts["still_open"] += 1
                continue

            _update_row(db_url, row.order_id, verdict)
            if (
                verdict.result in ("win", "loss", "expired")
                and is_v2
                and row.division == "bitunix_futures"
            ):
                _queue_close_out_notification(row, verdict, extra)
        except Exception as e:
            log.exception("replay failed for order_id=%s: %s", row.order_id, e)
            counts["errors"] += 1

    await _drain_notify_queue()
    _REPLAY_DB_URL_CTX["db_url"] = None
    return counts


def _parse_extra(extra_json: str | None) -> dict:
    if not extra_json:
        return {}
    try:
        return json.loads(extra_json)
    except (TypeError, ValueError):
        return {}


def _extra_json_delta(
    prior: dict, updates: dict,
) -> dict | None:
    """Return the merged extra_json IF anything actually changed, else
    None (caller skips the DB write to keep `still_open` tick free)."""
    changed = False
    for k, v in updates.items():
        if prior.get(k) != v:
            changed = True
            break
    if not changed:
        return None
    merged = dict(prior)
    merged.update(updates)
    return merged


def _persist_extra_json(
    db_url: str, order_id: str, full_extra: dict,
) -> None:
    """Write the merged extra_json back to paper_trade_record. Used by
    the v2 classifier when lifecycle advances on a still_open tick."""
    try:
        with _db.connect(db_url) as conn:
            conn.execute(
                "UPDATE paper_trade_record SET extra_json = ? "
                "WHERE order_id = ?",
                (json.dumps(full_extra, default=str), order_id),
            )
    except Exception as e:
        log.warning(
            "v2 replay: extra_json persist failed for order_id=%s: %s",
            order_id, e,
        )


async def _replay_loop(
    db_url: str,
    interval_sec: int,
    ohlcv_fetcher: OhlcvFetcher | None,
) -> None:
    log.info("paper_trade_replay loop online: interval=%ss", interval_sec)
    try:
        while True:
            try:
                counts = await _replay_tick_async(
                    db_url, ohlcv_fetcher=ohlcv_fetcher
                )
                # f-string (not %s) — RedactingFilter rewrites dict args
                # into their keys, producing a TypeError on % formatting.
                log.info(f"paper_trade_replay tick: {counts}")
            except Exception:
                log.exception("paper_trade_replay tick raised")
            await asyncio.sleep(interval_sec)
    except asyncio.CancelledError:
        log.info("paper_trade_replay loop cancelled.")
        raise


# ── OHLCV fetchers — venue-aware router ───────────────────────────────
#
# Different strategies trade on different venues, and replay bars MUST
# come from the same venue the order was placed against (otherwise the
# win/loss verdict reflects the wrong price tape):
#   Lord Otter / Market Cypher / Coinbase BTC Donchian → Coinbase BTC/USD
#   BitUnix Futures observer                          → BitUnix BTC/USDT.P
#
# Each concrete fetcher conforms to OhlcvFetcher
# (symbol, timeframe, since_ms, limit) → list[[ts_ms,o,h,l,c,v]].
# `_default_router_fetcher` picks the right one per call based on the
# symbol shape — perps end in `.P`, spot does not.


async def _coinbase_ccxt_fetcher(
    symbol: str,
    timeframe: str,
    since_ms: int,
    limit: int,
) -> list[list[float]]:
    """Coinbase spot OHLCV via ccxt public endpoint.

    Coinbase's fetch_ohlcv caps at ~300 bars per call, so we page if
    `limit` exceeds that. Cypher's 7-day window in 1m bars = 10080 bars
    → ~34 pages. Cheap, all read-only, no auth required.
    """
    import ccxt.async_support as ccxt_async
    exchange = ccxt_async.coinbase({"enableRateLimit": True})
    try:
        out: list[list[float]] = []
        page_size = 300
        cursor = since_ms
        remaining = limit
        while remaining > 0:
            this_page = min(page_size, remaining)
            page = await exchange.fetch_ohlcv(
                symbol, timeframe=timeframe,
                since=cursor, limit=this_page,
            )
            if not page:
                break
            out.extend(page)
            cursor = int(page[-1][0]) + _timeframe_ms(timeframe)
            remaining -= len(page)
            if len(page) < this_page:
                break  # ran out of data
        return out
    finally:
        await exchange.close()


async def _bitunix_kline_fetcher(
    symbol: str,
    timeframe: str,
    since_ms: int,
    limit: int,
) -> list[list[float]]:
    """BitUnix Futures OHLCV via the public kline endpoint (no auth).

    Same data source LiveBarCache uses for live ATR — keeps the
    replay's price path consistent with what the trade saw at
    placement.

    Pagination contract: BitUnix's `/api/v1/futures/market/kline`
    silently caps each response at `SERVER_PAGE_CAP` bars regardless
    of the `limit` query param, returning the NEWEST bars within the
    requested [startTime, endTime] window in descending order. To
    walk a window larger than that cap, we slice the requested range
    into ≤cap-bar sub-windows and iterate forward in time, advancing
    the cursor to the next sub-window end regardless of how many bars
    came back (bars can be missing within a sub-window without
    indicating end-of-data).

    Verified server cap: 200 bars (probed 2026-05-20). Treating the
    cap as end-of-data was the silent v2 lifecycle bug — for trades
    with max_hold_seconds=86400 (24h × 60 = 1440 1m bars) the legacy
    one-shot pagination returned only the newest 200 minutes, so the
    classifier never walked the early bars where TP1/TP2 filled. See
    `tests/test_bitunix_kline_fetcher_pagination.py` for the
    reproduction.
    """
    import httpx
    bu_symbol = _to_bitunix_symbol(symbol)
    tf_ms = _timeframe_ms(timeframe)
    out: list[list[float]] = []
    SERVER_PAGE_CAP = 200
    total_end_ms = since_ms + limit * tf_ms
    cursor = since_ms
    async with httpx.AsyncClient(base_url="https://fapi.bitunix.com", timeout=20.0) as client:
        while cursor < total_end_ms:
            window_end = min(cursor + SERVER_PAGE_CAP * tf_ms, total_end_ms)
            r = await client.get(
                "/api/v1/futures/market/kline",
                params={
                    "symbol": bu_symbol,
                    "interval": timeframe,
                    "startTime": cursor,
                    "endTime": window_end,
                    "limit": SERVER_PAGE_CAP,
                },
            )
            r.raise_for_status()
            data = r.json()
            if data.get("code") != 0:
                raise RuntimeError(
                    f"bitunix kline err: code={data.get('code')} msg={data.get('msg')!r}"
                )
            page = data.get("data") or []
            for row in page:
                out.append([
                    int(row["time"]),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    # BitUnix returns `baseVol` (USDT notional). Use it
                    # for parity with ccxt's volume convention.
                    float(row.get("baseVol") or 0.0),
                ])
            # Advance to next sub-window regardless of returned count.
            # Gaps in the response don't imply end-of-data.
            cursor = window_end
    # Defensive: ensure chronological order (the replay classifier walks forward)
    out.sort(key=lambda r: r[0])
    return out


def _to_bitunix_symbol(symbol: str) -> str:
    """Normalize the order's symbol to BitUnix REST format (no slash, no .P).

    `BTC/USDT.P` → `BTCUSDT`. `BTCUSDT.P` → `BTCUSDT`. `BTCUSDT` → `BTCUSDT`.
    """
    s = symbol.replace("/", "").upper()
    if s.endswith(".P"):
        s = s[:-2]
    return s


def _is_bitunix_symbol(symbol: str) -> bool:
    """True iff the symbol is a BitUnix perp (ends in `.P`)."""
    return symbol.upper().endswith(".P")


async def _default_router_fetcher(
    symbol: str,
    timeframe: str,
    since_ms: int,
    limit: int,
) -> list[list[float]]:
    """Symbol-aware default fetcher. Routes BitUnix perps to BitUnix's
    native kline endpoint and everything else to Coinbase spot via ccxt."""
    if _is_bitunix_symbol(symbol):
        return await _bitunix_kline_fetcher(symbol, timeframe, since_ms, limit)
    return await _coinbase_ccxt_fetcher(symbol, timeframe, since_ms, limit)


def _timeframe_ms(tf: str) -> int:
    if tf.endswith("m"):
        return int(tf[:-1]) * 60_000
    if tf.endswith("h"):
        return int(tf[:-1]) * 3_600_000
    if tf.endswith("d"):
        return int(tf[:-1]) * 86_400_000
    return 60_000


# ── DB helpers ─────────────────────────────────────────────────────────


def _load_pending(db_url: str) -> list[_PendingRow]:
    with _db.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT order_id, ts, strategy, division, symbol, side, qty, "
            "       stop_price, tp_price, tp_r_multiple, "
            "       entry_reference_price, expected_loss, "
            "       expected_gain, max_hold_seconds, extra_json "
            "FROM paper_trade_record WHERE result IS NULL "
            "ORDER BY ts ASC"
        ).fetchall()
    return [
        _PendingRow(
            order_id=r["order_id"], ts=r["ts"],
            strategy=r["strategy"], division=r["division"],
            symbol=r["symbol"],
            side=r["side"], qty=r["qty"],
            stop_price=r["stop_price"], tp_price=r["tp_price"],
            tp_r_multiple=r["tp_r_multiple"],
            entry_reference_price=r["entry_reference_price"],
            expected_loss=r["expected_loss"],
            expected_gain=r["expected_gain"],
            max_hold_seconds=r["max_hold_seconds"],
            extra_json=r["extra_json"],
        ) for r in rows
    ]


def _update_row(db_url: str, order_id: str, v: _Resolved) -> None:
    with _db.connect(db_url) as conn:
        conn.execute(
            "UPDATE paper_trade_record SET "
            "  result=?, result_ts=?, result_price=?, "
            "  actual_pnl_dollars=?, actual_r_multiple=?, "
            "  bars_to_resolution=? "
            "WHERE order_id=?",
            (
                v.result, v.result_ts, v.result_price,
                v.actual_pnl_dollars, v.actual_r_multiple,
                v.bars_to_resolution, order_id,
            ),
        )
        # Multi-leg final-resolution: persist filled_legs + current_sl
        # so post-mortem queries see the lifecycle state at close. Loads
        # existing extra_json and merges (other strategy-specific fields
        # like score_path / net_score must survive).
        if v.extra_json_updates:
            row = conn.execute(
                "SELECT extra_json FROM paper_trade_record WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            prior = {}
            if row and row["extra_json"]:
                try:
                    prior = json.loads(row["extra_json"])
                except (TypeError, ValueError):
                    prior = {}
            merged = dict(prior)
            merged.update(v.extra_json_updates)
            conn.execute(
                "UPDATE paper_trade_record SET extra_json = ? WHERE order_id = ?",
                (json.dumps(merged, default=str), order_id),
            )


def _iso_to_ms(ts: str) -> int:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
