"""Telegram lifecycle notifier for Bitunix paper trades.

Observability-only: a send failure MUST NEVER raise or propagate.
push() now owns success/failure auditing and never raises; _send is
a thin wrapper that prepends the prefix and passes audit metadata.

Reuses an existing async Telegram channel (channel.push(text) -> bool).
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class BitunixLifecycleNotifier:
    """Send Telegram notifications for the lifecycle of Bitunix paper trades.

    Parameters
    ----------
    channel:
        Object with ``async def push(self, text: str) -> None``.
    db_url:
        SQLite URL string for the ``telegram_notification_failed`` audit row.
        May be ``None`` (no audit write will happen).
    paper_mode:
        If ``True`` (default), prefix every message with "📄 [PAPER]".
        If ``False``, prefix with "💸 [LIVE]".
    """

    def __init__(
        self,
        channel: Any,
        *,
        db_url: str | None = None,
        paper_mode: bool = True,
    ) -> None:
        self._channel = channel
        self._db_url = db_url
        self._prefix = "📄 [PAPER]" if paper_mode else "💸 [LIVE]"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def notify_tp_fill(
        self,
        *,
        order_id: str,
        symbol: str,
        side: str,
        leg: str,
        entry_price: float,
        leg_price: float,
        r_so_far: float,
        old_sl: float,
        new_sl: float,
        new_sl_label: str,
        percent_closed: int,
    ) -> None:
        """Notify a TP1 or TP2 partial fill."""
        raw_pct = (leg_price - entry_price) / entry_price * 100
        pct_str = f"{raw_pct:+.2f}%"
        r_str = f"{r_so_far:+.1f}R"
        sym = symbol.upper()
        old_sl_fmt = f"{old_sl:,.2f}"
        new_sl_fmt = f"{new_sl:,.2f}"
        leg_price_fmt = f"{leg_price:,.2f}"

        if leg == "tp1":
            entry_price_fmt = f"{entry_price:,.2f}"
            body = (
                f"{sym} {side} · TP1 filled\n"
                f"Entry: ${entry_price_fmt} → TP1: ${leg_price_fmt} ({pct_str})\n"
                f"R so far: {r_str}\n"
                f"SL moved: ${old_sl_fmt} → ${new_sl_fmt} ({new_sl_label})\n"
                f"Position: {percent_closed}% closed"
            )
        else:
            body = (
                f"{sym} {side} · TP2 filled\n"
                f"TP2: ${leg_price_fmt} ({pct_str})\n"
                f"R so far: {r_str}\n"
                f"SL moved: ${old_sl_fmt} → ${new_sl_fmt} ({new_sl_label})\n"
                f"Position: {percent_closed}% closed"
            )

        await self._send(
            body,
            notification_type=f"tp_fill_{leg}",
            order_id=str(order_id),
        )

    async def notify_close_out(
        self,
        *,
        order_id: str,
        symbol: str,
        side: str,
        result: str,
        entry_price: float,
        exit_price: float,
        exit_reason: str,
        path: list[tuple],
        r_multiple: float,
        pnl_dollars: float | None,
        held_seconds: int | None,
    ) -> None:
        """Notify a final trade close-out."""
        sym = symbol.upper()

        # Header label
        if result == "win" and "TP3" in exit_reason:
            header_label = "CLOSED · TP3 filled (WIN)"
        elif result == "win":
            header_label = f"CLOSED · {exit_reason} (WIN)"
        elif result == "loss":
            header_label = "CLOSED · STOPPED OUT (LOSS)"
        else:  # expired
            header_label = "CLOSED · EXPIRED (max_hold)"

        # Path block
        path_rows = []
        for label, price, pct in path:
            label_padded = label.ljust(6)
            if price is None:
                row = f"  {label_padded} → {exit_reason}"
            elif pct is None:
                row = f"  {label_padded} → ${price:,.2f}"
            else:
                row = f"  {label_padded} → ${price:,.2f}  (+{pct:.2f}%)"
            path_rows.append(row)
        path_block = "Path:\n" + "\n".join(path_rows)

        # PnL string
        if pnl_dollars is None:
            pnl_str = "pending persistence"
        else:
            sign = "+" if pnl_dollars >= 0 else "-"
            pnl_str = f"{sign}${abs(pnl_dollars):,.2f}"

        # Held string
        if held_seconds is None:
            held_str = "n/a"
        else:
            hours = held_seconds // 3600
            minutes = (held_seconds % 3600) // 60
            held_str = f"{hours}h {minutes}m"

        body = (
            f"{sym} {side} · {header_label}\n"
            f"\n"
            f"{path_block}\n"
            f"\n"
            f"R-multiple: {r_multiple:+.2f}R\n"
            f"PnL: {pnl_str}\n"
            f"Fees: not tracked in paper\n"
            f"Funding: not tracked in paper\n"
            f"\n"
            f"Held: {held_str}"
        )

        await self._send(
            body,
            notification_type="close_out",
            order_id=str(order_id),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _send(
        self,
        body: str,
        *,
        notification_type: str,
        order_id: str,
    ) -> None:
        """Prepend prefix and push to the channel. NEVER raises.

        push() now owns success/failure auditing and never raises itself,
        so a bare await is sufficient here.
        """
        full = f"{self._prefix} {body}"
        await self._channel.push(
            full,
            audit_path=f"lifecycle_{notification_type}",
            audit_context={"order_id": order_id},
        )
