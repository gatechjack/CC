"""Execution-engine Telegram alerts (observability only — no orders involved).

ONE choke point, `emit_exec_alert(ExecOutcome)`, is called from the execution
engine's terminal-state / abort / integrity paths. It classifies the outcome into
a tier, formats a phone-scannable message whose FIRST LINE is the notification
preview, dedupes the noisy tiers, and sends via the REUSED Board Telegram bot
(`TelegramChannel.push`, wired once at boot via `set_exec_alert_sender`).

HARD GUARANTEE: a send failure — or any error in here — NEVER raises into or
delays the execution path. Everything is wrapped; the send is fire-and-forget.

Tiers (glyph · keyword · dedupe):
  🟢 FILLED     real order reached terminal `filled` (books a position)   — always send
  🟡 ABORTED    self-blocked before the broker; nothing sent              — deduped
  🟠 NO FILL    placed but rested/expired/cancelled unfilled; booked none — deduped
  🔴 EXEC FAIL  reached the broker and rejected / API error               — always send
  🚨 NAKED LEG  post-dispatch integrity: uncovered/half-open combo        — always send
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                                   # pragma: no cover
    _ET = None

log = logging.getLogger(__name__)

# tier -> (glyph, keyword, dedupe?). dedupe=True collapses identical repeats
# inside the window; False = always send (terminal real-money outcomes).
_TIER_META: dict[str, tuple[str, str, bool]] = {
    "FILLED":    ("\U0001F7E2", "FILLED",    False),
    "ABORTED":   ("\U0001F7E1", "ABORTED",   True),
    "NO_FILL":   ("\U0001F7E0", "NO FILL",   True),
    "EXEC_FAIL": ("\U0001F534", "EXEC FAIL", False),
    "NAKED_LEG": ("\U0001F6A8", "NAKED LEG", False),
}
_DEDUPE_WINDOW_S = 900.0                             # ~15 min

# ── module state, wired once at boot ─────────────────────────────────────────
_sender: Callable[..., Awaitable[bool]] | None = None   # async (text, chat_id=None)->bool
_alert_chat_id: int | None = None                        # None = reused Board chat
_tier_enabled: dict[str, bool] = {t: True for t in _TIER_META}
_dedupe: dict[tuple[str, str, str], float] = {}

# Dispatch origin propagates via a ContextVar so it flows through the whole call
# chain of ONE request/task (build-abort AND place_combo) without threading a
# param. A user-initiated dispatch (dashboard Approve / board-approved combo)
# marks this "user"; the autonomous scan loop leaves the default "autonomous".
# user-initiated emits BYPASS dedupe — every Approve must yield a guaranteed
# outcome ping even if identical to a prior one inside the window.
_origin_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "exec_alert_origin", default="autonomous")

# Hold fire-and-forget send handles so they can't be garbage-collected mid-send
# (a create_task / thread with no live reference can be dropped under load).
_pending_tasks: set = set()
_pending_threads: set = set()


@dataclass
class ExecOutcome:
    """Everything a tier needs. `tier` is set by the choke point (it knows the
    outcome); `classify` validates it and builds the first line."""
    tier: str
    symbol: str
    strategy: str
    reason: str
    combo_id: str | None = None
    order_id: str | None = None
    legs: Any = None
    attempted_price: float | None = None
    filled_price: float | None = None
    qty: float | None = None
    broker_error: str | None = None
    position_changed: bool | None = None            # None -> derive from tier
    origin: str | None = None                       # None -> resolve from ContextVar

    @property
    def corr_id(self) -> str:
        return str(self.combo_id or self.order_id or "—")

    @property
    def changed(self) -> bool:
        if self.position_changed is not None:
            return self.position_changed
        return self.tier in ("FILLED", "NAKED_LEG")


def set_exec_alert_sender(fn: Callable[..., Awaitable[bool]] | None) -> None:
    """Wire the reused Board bot's send helper (`TelegramChannel.push`). Called
    once at boot after the channel starts. Signature: async (text, chat_id=None)."""
    global _sender
    _sender = fn


def mark_user_origin():
    """Mark the CURRENT task/context as a user-initiated dispatch (dashboard
    Approve / board-approved combo). All emits in this task's call chain then
    BYPASS dedupe. Returns the ContextVar token (caller may reset it, but a
    per-request task auto-discards its context so a reset is not required)."""
    return _origin_var.set("user")


@contextmanager
def user_dispatch():
    """Scope a user-initiated dispatch: `with user_dispatch(): ...`. Emits inside
    bypass dedupe; the origin resets on exit."""
    token = _origin_var.set("user")
    try:
        yield
    finally:
        _origin_var.reset(token)


def configure(*, chat_id: int | None = None,
              tiers: dict[str, bool] | None = None) -> None:
    """Optional config: override the alert chat_id (default = reused Board chat)
    and per-tier on/off toggles. Unknown tier keys are ignored."""
    global _alert_chat_id
    if chat_id is not None:
        _alert_chat_id = int(chat_id)
    for t, on in (tiers or {}).items():
        key = str(t).upper().replace(" ", "_")
        if key in _tier_enabled:
            _tier_enabled[key] = bool(on)


def reset_dedupe() -> None:
    """Test hook — clear the dedupe memory."""
    _dedupe.clear()


def first_line(o: ExecOutcome) -> str:
    glyph, keyword, _ = _TIER_META.get(o.tier, ("•", o.tier, False))
    return f"{glyph} {keyword} — {o.symbol} {o.strategy} — {o.reason}"


def classify(o: ExecOutcome) -> tuple[str, str]:
    """Return (tier, first_line). Unknown tier -> EXEC_FAIL (fail loud, never drop)."""
    tier = o.tier if o.tier in _TIER_META else "EXEC_FAIL"
    o.tier = tier
    return tier, first_line(o)


def _now_et_str() -> str:
    try:
        dt = datetime.now(_ET) if _ET is not None else datetime.now()
        return dt.strftime("%Y-%m-%d %H:%M:%S %Z").strip() or dt.strftime(
            "%Y-%m-%d %H:%M:%S ET")
    except Exception:                                # pragma: no cover
        return "—"


def _fmt_legs(legs: Any) -> str:
    try:
        parts = []
        for leg in list(legs)[:6]:
            if isinstance(leg, dict):
                parts.append(
                    f"{leg.get('side', '?')} {leg.get('expiration', '')} "
                    f"{leg.get('strike', '')}".strip())
            else:
                parts.append(str(leg))
        return "; ".join(p for p in parts if p)
    except Exception:                                # pragma: no cover
        return str(legs)[:200]


def format_body(o: ExecOutcome) -> str:
    """Full message: first line (preview) + body below the fold."""
    lines = [first_line(o), "", f"id: `{o.corr_id}`"]
    if o.legs:
        lines.append("legs: " + _fmt_legs(o.legs))
    if o.attempted_price is not None or o.filled_price is not None:
        ap = "—" if o.attempted_price is None else f"{o.attempted_price:g}"
        fp = "—" if o.filled_price is None else f"{o.filled_price:g}"
        q = "" if o.qty is None else f"  qty {o.qty:g}"
        lines.append(f"price: attempted {ap} / filled {fp}{q}")
    elif o.qty is not None:
        lines.append(f"qty {o.qty:g}")
    if o.broker_error:
        lines.append(f"error: {str(o.broker_error)[:300]}")
    lines.append(f"\U0001F552 {_now_et_str()}")
    lines.append("position " + ("CHANGED" if o.changed else "UNCHANGED"))
    return "\n".join(lines)


def _should_send(o: ExecOutcome, now: float, origin: str = "autonomous") -> bool:
    """Dedupe: collapse identical (tier, symbol, reason) inside the window for the
    noisy tiers of the AUTONOMOUS scan loop. Bypassed when the tier is a terminal
    real-money outcome OR the dispatch is user-initiated (every Approve gets a
    guaranteed outcome ping, even a repeat inside the window)."""
    _, _, dedupe = _TIER_META.get(o.tier, ("", "", False))
    if not dedupe or origin == "user":
        return True
    key = (o.tier, o.symbol, o.reason)
    last = _dedupe.get(key)
    if last is not None and (now - last) < _DEDUPE_WINDOW_S:
        return False
    _dedupe[key] = now
    return True


async def _safe_send(text: str) -> None:
    try:
        if _sender is None:
            return
        if _alert_chat_id is not None:
            await _sender(text, chat_id=_alert_chat_id)
        else:
            await _sender(text)
    except Exception as e:                           # send failure is isolated
        log.warning("exec_alert send failed (isolated): %s", e)


def _spawn_thread_send(text: str) -> None:
    """No-running-loop fallback: send on a daemon thread (its own loop) so the
    alert still goes out and the caller isn't blocked. Held in a set so the thread
    ref survives until it completes."""
    def _run():
        try:
            asyncio.run(_safe_send(text))
        except Exception as e:
            log.warning("exec_alert thread send failed (isolated): %s", e)
        finally:
            _pending_threads.discard(th)
    th = threading.Thread(target=_run, name="exec-alert-send", daemon=True)
    _pending_threads.add(th)
    th.start()


def flush_for_test(timeout: float = 3.0) -> None:
    """Test hook: join any in-flight background send threads for deterministic
    assertions. Not used in production."""
    for th in list(_pending_threads):
        th.join(timeout)


def emit_exec_alert(outcome: ExecOutcome) -> None:
    """THE choke point. classify -> per-tier toggle -> dedupe (origin-aware) ->
    format -> fire-and-forget send. NEVER raises into or delays the caller."""
    try:
        tier, _ = classify(outcome)
        if not _tier_enabled.get(tier, True):
            return
        origin = outcome.origin or _origin_var.get()
        if not _should_send(outcome, time.monotonic(), origin):
            log.debug("exec_alert deduped: %s %s %s", tier, outcome.symbol, outcome.reason)
            return
        text = format_body(outcome)
        if _sender is None:
            log.info("exec_alert (no sender wired): %s", first_line(outcome))
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # Fire-and-forget on the running loop — but HOLD a reference so the
            # task can't be garbage-collected mid-send; drop it on completion.
            task = loop.create_task(_safe_send(text))
            _pending_tasks.add(task)
            task.add_done_callback(_pending_tasks.discard)
        else:
            _spawn_thread_send(text)                  # no loop → thread (still sends)
    except Exception as e:                           # observability must never break execution
        log.warning("emit_exec_alert failed (isolated): %s", e)
