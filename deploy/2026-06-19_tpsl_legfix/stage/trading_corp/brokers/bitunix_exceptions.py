"""Shared exception classes raised by the BitUnix broker write path.

Lives in its own module so the safety consumers (DataExecAgent, the bitunix
observer, etc.) can import the exception class WITHOUT requiring the live
write-path implementation. The broker-write branch
(`bitunix-live-engine-stage1-broker-write`) currently defines the same class
inline in `brokers/bitunix.py`; on merge, the broker should import from here
so both `except` and `raise` sites reference the **same class object**
(isinstance / `except` matching depends on class identity).

`BitunixAPIError` is intentionally NOT mirrored here — it's only ever raised
+ caught inside the broker itself, so it can stay local until/unless a
consumer outside the broker needs to catch it.
"""
from __future__ import annotations


class BitunixPositionModeMismatch(RuntimeError):
    """The BitUnix account is not in the expected position mode.

    Raised by `BitunixBroker.place_order` BEFORE any order is sent when the
    account's live `positionMode` is not `ONE_WAY` (e.g. an out-of-band UI
    flip to `HEDGE`). The broker fails closed (refuses to place) and latches
    its own `_halt_new_orders` flag *before* raising — so by the time a
    consumer catches this, the broker is already structurally halted at the
    instance level. The consumer's job is the *response* side: audit row,
    Telegram alert, re-raise.

    Strategy-level halt (cross-process, cross-broker-instance) is a separate
    persistence concern — see BACKLOG #N+1 follow-up.
    """

    def __init__(self, current, expected: str = "ONE_WAY") -> None:
        self.current = current
        self.expected = expected
        super().__init__(
            f"BitUnix position mode mismatch: account is {current!r}, "
            f"expected {expected!r} — refusing to place order"
        )


class BitunixStaleSnapshot(RuntimeError):
    """The BitUnix broker's last successful `snapshot()` is older than the
    configured staleness threshold (`snapshot_staleness_threshold_seconds`
    in `config/strategies.yaml`).

    Raised by `BitunixBroker._assert_snapshot_fresh()` from two sites
    (gate (a) sub-item 2 of the REST resilience track, 2026-05-30):
      * the bitunix observer's pre-trade gate, before routing into
        `data_exec.place()`;
      * `data_exec.place()` as a defense-in-depth re-check, because
        observer-gate-passed-and-then-snapshot-went-stale-between-classification-and-place
        is a real race.

    Like `BitunixPositionModeMismatch`, the broker fails closed: it latches
    `_halt_new_orders=True` and `_halt_reason="snapshot_stale:<age_s>"`
    BEFORE raising. The consumer's job is the response side (audit +
    Telegram) via `data_exec._handle_stale_snapshot()`.

    Recovery semantics: a subsequent successful `snapshot()` will flip
    `is_healthy()` back to True (the timestamp is refreshed), but the halt
    latch is STICKY — operator clears it explicitly via `broker.resume()`.
    The is_healthy() recovery is what lets the dashboard show "live again"
    without forcing a restart; the halt latch is what stops orders from
    sneaking through during a flap.
    """

    def __init__(self, age_s: float, threshold_s: float) -> None:
        self.age_s = age_s
        self.threshold_s = threshold_s
        super().__init__(
            f"BitUnix snapshot is stale: last successful snapshot was "
            f"{age_s:.1f}s ago, threshold is {threshold_s:.1f}s "
            f"— refusing to place order"
        )


class BitunixStuckOrderCancelled(RuntimeError):
    """A live BitUnix order failed to reach a terminal status within the
    poll budget (`_fill_max_polls × _fill_poll_interval_s`) and was
    successfully cancelled by the broker.

    Raised by `BitunixBroker._observe_fill` AFTER the cancel succeeded
    when no fills landed (status is NEW / INIT / None — nothing partial).
    The audit row + telegram fire from the broker before the raise.

    Partial-fill case is NOT raised: when `status == PART_FILLED`, the
    broker cancels the resting remainder, emits the same audit + telegram,
    then returns the (still-partial) tuple normally so `place_order`
    constructs a `bitunix_futures:part_filled` `FillEvent` with the
    real money that landed. Operator sees both the cancel audit + the
    partial fill audit downstream.

    Gate (a) sub-item 3 of the REST resilience track, 2026-05-30.
    """

    def __init__(self, order_id: str | None, status: str | None) -> None:
        self.order_id = order_id
        self.status = status
        super().__init__(
            f"BitUnix order {order_id!r} stuck at status={status!r} "
            f"and was cancelled — caller should treat as not-placed"
        )


class BitunixStuckOrderCancelFailed(RuntimeError):
    """A live BitUnix order failed to reach terminal status AND the cancel
    itself failed (network down, order already in flight, race with venue,
    etc.).

    This is the case where operator intervention may be required: the
    broker cannot prove the order isn't still resting at the venue. The
    audit (`stuck_order_cancel_failed`) + escalated telegram fire before
    the raise.

    Gate (a) sub-item 3 of the REST resilience track, 2026-05-30.
    """

    def __init__(self, order_id: str | None, status: str | None) -> None:
        self.order_id = order_id
        self.status = status
        super().__init__(
            f"BitUnix order {order_id!r} stuck at status={status!r} AND "
            f"cancel attempt failed — operator intervention may be required"
        )


class BitunixMakerEntryUnfilled(RuntimeError):
    """B2 maker-entry: the POST_ONLY maker limit did not fill within the rest
    timeout (and was cancelled), AND the configured fallback mode is
    ``abandon`` (do NOT cross to taker). Raised by
    `BitunixBroker._place_maker_entry` so the caller treats the signal as
    deliberately not-entered (an explicit abandon — NEVER a silent drop).

    Only raised in ``fallback_mode='abandon'``. The default mode
    (``cross_to_taker``) never raises this — it places a taker market entry
    instead so the signal is not missed.
    """

    def __init__(self, order_id: str | None) -> None:
        self.order_id = order_id
        super().__init__(
            f"BitUnix maker entry {order_id!r} unfilled within rest timeout; "
            f"fallback_mode=abandon — signal deliberately not entered"
        )


class BitunixUntrackedTpslOrder(RuntimeError):
    """A `/tpsl/place_order` POST was ACCEPTED by the venue (HTTP ok, code 0) but
    no `orderId` could be extracted from the response, so the bot could not
    capture the resting TP leg's id.

    Raised by `BitunixBroker.place_tpsl_order` ONLY after the POST succeeded — an
    API error or an idempotent-duplicate (30042) is handled separately and does
    NOT raise this. The danger it guards: the leg has very likely RESTED on the
    venue but is now UNTRACKED, and the position reconciler is position-level only
    (it matches positions, not stray TP/SL orders) — so it will not detect it.
    The caller (the bitunix observer) must FLAG it for reconciliation
    (`bracket_tp_leg_untracked` audit), NEVER swallow it as "no leg placed".

    Background — report `c8a426d` (Section-B verification, trade cb6b4d4a): the
    original parse did `(data or {}).get("orderId")` assuming a dict, but the live
    venue returned a LIST and the `AttributeError` fired AFTER the POST reached the
    venue (all 3 legs failed, `legs_placed=0`). The parse is now defensive (dict +
    list, see `_extract_tpsl_order_id`); this exception is the residual safety net
    for any FUTURE unknown response shape, so an uncaptured-but-resting leg can
    never again be silently dropped.

    Fail-soft is preserved at the call site: the B1 entry-attached MARKET stop and
    the managed Position SL still guard the position regardless.
    """

    def __init__(self, *, position_id, symbol, tp_price, tp_qty, raw_response) -> None:
        self.position_id = position_id
        self.symbol = symbol
        self.tp_price = tp_price
        self.tp_qty = tp_qty
        self.raw_response = raw_response
        super().__init__(
            f"BitUnix tpsl/place_order accepted but no orderId extracted "
            f"(positionId={position_id} {symbol} tpPrice={tp_price} "
            f"tpQty={tp_qty} response={raw_response!r}) — TP leg may be resting "
            f"untracked; reconcile required"
        )
