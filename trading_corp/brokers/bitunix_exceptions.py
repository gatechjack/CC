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
