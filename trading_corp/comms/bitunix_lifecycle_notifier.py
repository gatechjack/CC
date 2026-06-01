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
    # N+2 Phase 3 Session B Commit 5 (5c): 8 operational alerts
    # ------------------------------------------------------------------
    # Per Phase 1b §5: 8 new notifier methods for the live exit lifecycle.
    # All route through `_send` → `_channel.push` → existing confirmed-
    # delivery audit semantics ([[telegram-audit-success-is-confirmed-delivery]]).
    # No new auditing code; only new payload shapes + audit_path tags.

    async def notify_exit_order_placed(
        self,
        *,
        order_id: str,
        parent_order_id: str,
        symbol: str,
        side: str,
        exit_kind: str,
        qty: float,
    ) -> None:
        """Intent: an exit order has been submitted to the broker
        (BEFORE the broker confirms fill)."""
        body = (
            f"{symbol.upper()} {side} · exit:{exit_kind} PLACED\n"
            f"qty: {qty}\n"
            f"parent_order_id: {parent_order_id}"
        )
        await self._send(
            body,
            notification_type="exit_order_placed",
            order_id=str(order_id),
        )

    async def notify_exit_order_filled(
        self,
        *,
        order_id: str,
        parent_order_id: str,
        symbol: str,
        side: str,
        exit_kind: str,
        real_fill_price: float,
        real_qty: float,
        real_fee_usd: float,
        live_exit_counter: int | None = None,
        live_exit_counter_total: int | None = None,
    ) -> None:
        """Broker-confirmed fill of an exit order. Counter suffix
        `(exit #N/M)` for the first-N elevated visibility window
        (Phase 1a §8)."""
        suffix = ""
        if live_exit_counter is not None and live_exit_counter_total is not None:
            suffix = f" (exit #{live_exit_counter}/{live_exit_counter_total})"
        body = (
            f"{symbol.upper()} {side} · exit:{exit_kind} FILLED{suffix}\n"
            f"price: ${real_fill_price:,.2f}\n"
            f"qty: {real_qty}\n"
            f"fee: ${real_fee_usd:,.4f}\n"
            f"parent_order_id: {parent_order_id}"
        )
        await self._send(
            body,
            notification_type="exit_order_filled",
            order_id=str(order_id),
        )

    async def notify_exit_order_rejected(
        self,
        *,
        order_id: str,
        parent_order_id: str,
        symbol: str,
        exit_kind: str,
        bitunix_code: str | None,
        bitunix_msg: str,
    ) -> None:
        """BitUnix returned an error on the exit order. Position
        remains open per broker; replay loop will retry next tick."""
        code_str = f"code {bitunix_code}: " if bitunix_code else ""
        body = (
            f"{symbol.upper()} · EXIT REJECTED ({exit_kind})\n"
            f"{code_str}{bitunix_msg}\n"
            f"parent_order_id: {parent_order_id}"
        )
        await self._send(
            body,
            notification_type="exit_order_rejected",
            order_id=str(order_id),
        )

    async def notify_exit_partial_fill(
        self,
        *,
        order_id: str,
        parent_order_id: str,
        symbol: str,
        exit_kind: str,
        expected_qty: float,
        actual_qty: float,
    ) -> None:
        """Broker filled less than requested — pending continued fill."""
        body = (
            f"{symbol.upper()} · EXIT PARTIAL ({exit_kind})\n"
            f"expected: {expected_qty}, filled: {actual_qty}\n"
            f"pending continued fill\n"
            f"parent_order_id: {parent_order_id}"
        )
        await self._send(
            body,
            notification_type="exit_partial_fill",
            order_id=str(order_id),
        )

    async def notify_position_closed_with_pnl(
        self,
        *,
        order_id: str,
        symbol: str,
        side: str,
        result: str,
        gross_pnl_usd: float,
        total_fee_usd: float,
        total_funding_usd: float,
        net_pnl_usd: float,
    ) -> None:
        """Final close-out after ALL legs filled (live equivalent of
        `notify_close_out` but with real fees + funding)."""
        result_label = result.upper()
        gross_sign = "+" if gross_pnl_usd >= 0 else "-"
        net_sign = "+" if net_pnl_usd >= 0 else "-"
        funding_sign = "+" if total_funding_usd >= 0 else "-"
        body = (
            f"{symbol.upper()} {side} · CLOSED ({result_label})\n"
            f"Gross PnL: {gross_sign}${abs(gross_pnl_usd):,.2f}\n"
            f"Fees: ${total_fee_usd:,.4f}\n"
            f"Funding: {funding_sign}${abs(total_funding_usd):,.4f}\n"
            f"Net PnL: {net_sign}${abs(net_pnl_usd):,.2f}"
        )
        await self._send(
            body,
            notification_type="position_closed_with_pnl",
            order_id=str(order_id),
        )

    async def notify_reconciliation_divergence(
        self,
        *,
        order_id: str | None,
        symbol: str | None,
        kind: str,
        detail: str,
    ) -> None:
        """Reconciler surfaced a bot↔broker mismatch. `kind` is one of
        `missing_on_broker`, `orphan_on_broker`, `qty_divergence`, or
        `price_divergence`."""
        sym = symbol.upper() if symbol else "(unknown)"
        oid = order_id if order_id else "(unknown)"
        body = (
            f"🚨 RECON DIVERGENCE: {kind}\n"
            f"{sym} order_id={oid}\n"
            f"{detail}\n"
            f"broker._halt_new_orders=True (entries halted; exits flow)"
        )
        await self._send(
            body,
            notification_type="reconciliation_divergence",
            order_id=str(order_id) if order_id else "",
        )

    async def notify_cost_accrual_recorded(
        self,
        *,
        order_id: str,
        symbol: str,
        fee_usd: float,
        funding_usd: float,
        cumulative_fee_usd: float,
        cumulative_funding_usd: float,
    ) -> None:
        """Per-interval cost accrual recorded (stub for N+3 Layer 2
        funding work)."""
        body = (
            f"{symbol.upper()} · cost accrual\n"
            f"fee: ${fee_usd:,.4f} (cumulative ${cumulative_fee_usd:,.4f})\n"
            f"funding: ${funding_usd:+,.4f} (cumulative ${cumulative_funding_usd:+,.4f})\n"
            f"parent_order_id: {order_id}"
        )
        await self._send(
            body,
            notification_type="cost_accrual_recorded",
            order_id=str(order_id),
        )

    async def notify_restart_resume_executed(
        self,
        *,
        matched_count: int,
        orphan_count: int,
        case_c_count: int = 0,
    ) -> None:
        """Restart-resume summary at process start. Matched = case (a);
        orphan = case (b); case_c = bot tracks but broker doesn't (the
        position-state reconciler's `missing_on_broker` surface)."""
        body = (
            f"🔄 RESTART RESUME\n"
            f"matched: {matched_count}\n"
            f"orphan on broker: {orphan_count}\n"
            f"case (c) deferred (operator-resolve): {case_c_count}"
        )
        await self._send(
            body,
            notification_type="restart_resume_executed",
            order_id="",
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
