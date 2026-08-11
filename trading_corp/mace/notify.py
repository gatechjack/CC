"""MACE Telegram notifications (plan § Observability, T3).

MACE is zero-HITL: Telegram is the human SAFETY NET (Board decision 4), not an
approval surface. These are NOTIFICATION messages carrying fill/exit/summary
detail per PEAD/PMCC precedent — NOT HITL UX (there are no approval gates in the
MACE order path, so CLAUDE.md's "approvals go to the web app" rule is not
engaged). Breaker/URGENT messages lead with a `URGENT` first line by convention.

Pure formatters (testable without a channel) + a thin `MaceNotifier` that pushes
through an injected channel (duck-typed `.push(str)` / optional `.push_split`).
The channel is injected so nothing here imports comms/telegram.
"""
from __future__ import annotations

from dataclasses import dataclass

# First-line URGENT marker (plan: breakers + fail-closed guards lead with this).
URGENT = "\U0001F6A8 URGENT"          # red siren


def _money(x: float | None) -> str:
    return "-" if x is None else f"${x:,.2f}"


def _signed(x: float | None) -> str:
    if x is None:
        return "-"
    return f"+${x:,.2f}" if x >= 0 else f"-${abs(x):,.2f}"


def format_entry(symbol: str, expiry: str, sp, lp, sc, lc, contracts: int,
                 credit: float, floor: float, pt: float, max_risk: float) -> str:
    return (f"✅ MACE ENTRY {symbol} {expiry} "
            f"{sp:g}/{lp:g}P {sc:g}/{lc:g}C x{contracts} - "
            f"credit {_money(credit)} (floor {_money(floor)}) - "
            f"PT resting {_money(pt)} GTC - maxrisk {_money(max_risk)}")


def format_exit(symbol: str, expiry: str, contracts: int, reason: str,
                debit: float, pnl: float, pct_of_credit: float | None) -> str:
    pct = "" if pct_of_credit is None else f" ({pct_of_credit:+.0f}% of credit)"
    return (f"\U0001F514 MACE EXIT {symbol} {expiry} x{contracts} - "
            f"{reason.upper()} @ {_money(debit)} - P&L {_signed(pnl)}{pct}")


def format_standdown(symbol: str, attempts: int, max_attempts: int,
                     last_price: float | None) -> str:
    return (f"⚠️ MACE {symbol} entry stand-down - "
            f"{attempts}/{max_attempts} attempts unfilled (last {_money(last_price)})")


def format_reject(symbol: str, detail: str) -> str:
    return f"⚠️ MACE {symbol} order rejected - {detail}"


def format_error(loop: str, exc: object) -> str:
    return f"⚠️ MACE ERROR {loop}: {exc}"


def format_close_exhausted(symbol: str, expiry: str, contracts: int,
                           attempts: int) -> str:
    """Exit ladder exhausted -> rung stays 'closing', operator manual backstop."""
    return (f"{URGENT} - MACE {symbol} {expiry} x{contracts} EXIT UNFILLED after "
            f"{attempts} attempts - rung stays CLOSING, MANUAL close needed")


def format_breaker(condition: str, lines: list[str], suggested_action: str,
                   *, urgent: bool = True) -> str:
    head = f"{URGENT} - MACE {condition}" if urgent else f"MACE {condition}"
    body = "\n".join(lines)
    return f"{head}\n{body}\nSuggested: {suggested_action}"


def format_daily_summary(session_date: str, equity: float | None, hwm: float | None,
                         open_rungs: list[str], day_pnl: float,
                         breaker_states: list[str],
                         next_blackouts: list[str]) -> str:
    lines = [f"MACE daily summary {session_date}",
             f"equity {_money(equity)} - HWM {_money(hwm)} - day P&L {_signed(day_pnl)}"]
    if open_rungs:
        lines.append(f"open rungs ({len(open_rungs)}):")
        lines.extend(f"  {r}" for r in open_rungs)
    else:
        lines.append("open rungs: none")
    if breaker_states:
        lines.append("breakers: " + ", ".join(breaker_states))
    lines.append("next-session blackouts: "
                 + (", ".join(next_blackouts) if next_blackouts else "none"))
    return "\n".join(lines)


@dataclass
class MaceNotifier:
    """Pushes MACE notifications through an injected channel. `channel` is
    duck-typed: it must expose `.push(text)` and MAY expose `.push_split(text)`
    (used for long summaries to dodge the 4096-char cut). `enabled=False` makes
    every send a no-op (paper/tests)."""

    channel: object | None = None
    enabled: bool = True

    def _send(self, text: str, *, split: bool = False) -> None:
        if not self.enabled or self.channel is None:
            return
        if split and hasattr(self.channel, "push_split"):
            self.channel.push_split(text)
        else:
            self.channel.push(text)

    def entry(self, **kw) -> None:
        self._send(format_entry(**kw))

    def exit(self, **kw) -> None:
        self._send(format_exit(**kw))

    def standdown(self, **kw) -> None:
        self._send(format_standdown(**kw))

    def reject(self, symbol: str, detail: str) -> None:
        self._send(format_reject(symbol, detail))

    def error(self, loop: str, exc: object) -> None:
        self._send(format_error(loop, exc))

    def close_exhausted(self, **kw) -> None:
        self._send(format_close_exhausted(**kw))

    def breaker(self, **kw) -> None:
        self._send(format_breaker(**kw))

    def daily_summary(self, **kw) -> None:
        self._send(format_daily_summary(**kw), split=True)
