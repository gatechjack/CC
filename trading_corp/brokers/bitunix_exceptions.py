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
