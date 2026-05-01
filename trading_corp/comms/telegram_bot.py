"""Telegram Board channel.

Pushes messages, requests approvals via inline keyboard buttons, and routes
arbitrary chat messages into a CEO message handler.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from trading_corp.comms.base import BoardChannel
from trading_corp.graph.interrupts import ApprovalRequest, BoardDecision

log = logging.getLogger(__name__)

OnMessage = Callable[[str], Awaitable[str]]  # user_text -> reply_text
OnCommand = Callable[[], Awaitable[str]]
# Research firm (v3): takes the parsed `/research <args...>` list
# (e.g. ['candidate', 'robinhood_pmcc', '3']) and returns a markdown
# string to render. v2's tuple-with-inline-keyboard signature is gone —
# v3 has no recommendation-as-a-unit approval flow (per-candidate
# decisions live in division code).
OnResearchCommand = Callable[[list[str]], Awaitable[str]]


class TelegramChannel(BoardChannel):
    name = "telegram"

    def __init__(
        self,
        token: str,
        chat_id: str,
        on_message: OnMessage | None = None,
        on_brief_command: OnCommand | None = None,
        on_scan_command: OnCommand | None = None,
        on_fidelity_scan_command: OnCommand | None = None,
        commands: Any = None,
        on_research_command: OnResearchCommand | None = None,
    ) -> None:
        self._token = token
        self._chat_id = int(chat_id)
        self._on_message = on_message
        self._on_brief = on_brief_command
        self._on_scan = on_scan_command
        self._on_fidelity_scan = on_fidelity_scan_command
        # `commands` is a TelegramCommands instance (rich /equity, /pairs, etc.).
        # Optional — if None, only the legacy callbacks above are wired.
        self._commands = commands
        # Research firm hooks (v3 — CandidateRecommendation only in 1a-1).
        self._on_research = on_research_command
        self._pending: dict[str, asyncio.Future[BoardDecision]] = {}
        self._app = None
        # Set when the polling loop hits an unrecoverable error (e.g. another
        # bot instance is polling the same token). main.py races its idle
        # sleep against this so we shut down promptly instead of spamming logs.
        self._shutdown_event: asyncio.Event | None = None
        self._conflict_detected = False

    async def start(self) -> None:
        # Lazy import — python-telegram-bot is optional in Phase 2 if you only
        # use CLI mode.
        import telegram  # type: ignore
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update  # type: ignore  # noqa: F401
        from telegram.ext import (  # type: ignore
            Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters,
        )

        self._shutdown_event = asyncio.Event()

        self._app = Application.builder().token(self._token).build()
        self._app.add_handler(CallbackQueryHandler(self._on_callback))
        self._app.add_handler(CommandHandler("brief", self._on_brief_cmd))
        self._app.add_handler(CommandHandler("scan", self._on_scan_cmd))
        self._app.add_handler(CommandHandler("fidelityscan", self._on_fidelity_scan_cmd))
        self._app.add_handler(CommandHandler("status", self._on_status_cmd))
        self._app.add_handler(CommandHandler("research", self._on_research_cmd))

        # Rich commands (only wired when a TelegramCommands instance is provided).
        if self._commands is not None:
            self._app.add_handler(CommandHandler("help",      self._cmd_help))
            self._app.add_handler(CommandHandler("start",     self._cmd_help))   # /start = /help
            self._app.add_handler(CommandHandler("equity",    self._cmd_equity))
            self._app.add_handler(CommandHandler("positions", self._cmd_positions))
            self._app.add_handler(CommandHandler("pairs",     self._cmd_pairs))
            self._app.add_handler(CommandHandler("pair",      self._cmd_pair))
            self._app.add_handler(CommandHandler("vix",       self._cmd_vix))
            self._app.add_handler(CommandHandler("regime",    self._cmd_regime))
            self._app.add_handler(CommandHandler("pending",   self._cmd_pending))
            self._app.add_handler(CommandHandler("mode",      self._cmd_mode))
            self._app.add_handler(CommandHandler("halts",     self._cmd_halts))

        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text)
        )

        async def _on_telegram_error(update, context) -> None:  # noqa: ARG001
            """Detect Conflict (another bot polling) and signal shutdown."""
            err = context.error
            if isinstance(err, telegram.error.Conflict):
                if not self._conflict_detected:
                    self._conflict_detected = True
                    log.error(
                        "TELEGRAM CONFLICT: another bot is polling this token. "
                        "Shutting down to avoid log spam. To find the rogue process: "
                        "Windows: tasklist /FI \"IMAGENAME eq python.exe\"  |  "
                        "PowerShell: Get-Process python | Select Id,Path,StartTime"
                    )
                    if self._shutdown_event is not None:
                        self._shutdown_event.set()
                    # Schedule polling stop so the retry loop exits cleanly
                    # instead of looping forever on the same Conflict.
                    try:
                        if self._app is not None and self._app.updater is not None:
                            asyncio.create_task(self._app.updater.stop())
                    except Exception:
                        pass
            else:
                log.warning("Telegram error: %s", err)

        self._app.add_error_handler(_on_telegram_error)

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        log.info("Telegram channel online (chat_id=%s)", self._chat_id)

    async def wait_for_shutdown_signal(self) -> None:
        """Return when polling hits an unrecoverable error (Conflict, etc.)."""
        if self._shutdown_event is None:
            # start() hasn't been called yet; block forever (consistent with base)
            await asyncio.Event().wait()
            return
        await self._shutdown_event.wait()

    async def stop(self) -> None:
        if self._app is None:
            return
        try:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        except Exception as e:
            log.warning("Telegram shutdown error: %s", e)

    async def push(self, text: str) -> None:
        if self._app is None:
            log.warning("push called before start; dropping message")
            return
        # Telegram messages are limited to ~4096 chars.
        await self._app.bot.send_message(
            chat_id=self._chat_id,
            text=text[:4000],
            parse_mode="Markdown",
        )

    async def request_approval(
        self, req: ApprovalRequest, timeout_s: float = 3600.0,
    ) -> BoardDecision:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup  # type: ignore

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("Approve", callback_data=f"approve|{req.order_id}"),
            InlineKeyboardButton("Reject",  callback_data=f"reject|{req.order_id}"),
        ]])
        # The summary is now a multi-line Telegram-Markdown-safe body
        # produced by trading_corp.comms.approval_format. Render it as a
        # plain block (no surrounding backticks — those broke multi-line
        # rendering and made every char monospaced).
        text = (
            "*Approval requested*\n"
            f"{req.summary}\n\n"
            f"🆔 `{req.order_id}`\n"
            "_Tap Approve / Reject below, or use_ "
            "`/approve <id>` `/reject <id>` `/modify <id> <qty>`"
        )
        if self._app is None:
            raise RuntimeError("Telegram not started")
        await self._app.bot.send_message(
            chat_id=self._chat_id, text=text, reply_markup=kb, parse_mode="Markdown",
        )

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[BoardDecision] = loop.create_future()
        self._pending[req.order_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            return BoardDecision(decision="reject", reason="telegram approval timeout")
        finally:
            self._pending.pop(req.order_id, None)

    # -- handlers --
    async def _on_callback(self, update, context) -> None:
        """Route inline-keyboard taps.

        Two callback-data formats coexist:
          1. Legacy approval-flow: 'approve|<order_id>' / 'reject|<order_id>'
             — used by request_approval() inline keyboard.
          2. Rich-commands: 'pair:SYM', 'approve:SYM', 'defer:SYM',
             'div:SLUG', 'pairs', 'positions', 'home' — handled by the
             TelegramCommands router.
        """
        cq = update.callback_query
        await cq.answer()
        data = cq.data or ""

        # v3: no research-firm inline-keyboard approval branch — the
        # `wlrec_*` callback prefix is dropped (per design §8.B). The
        # legacy approval-flow below still serves the order-approval
        # gate, which is unrelated.

        # Legacy: pipe-separated approval/rejection of a specific order_id
        if "|" in data:
            decision_str, order_id = data.split("|", 1)
            if decision_str not in ("approve", "reject"):
                return
            fut = self._pending.get(order_id)
            if fut is not None and not fut.done():
                fut.set_result(BoardDecision(
                    decision=decision_str, reason="via Telegram button",
                ))
            try:
                await cq.edit_message_text(
                    cq.message.text + f"\n\n→ Board: {decision_str.upper()}",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
            return

        # Rich-commands callback: route via TelegramCommands
        if self._commands is None:
            return
        try:
            text, keyboard = await self._commands.handle_callback(data)
        except Exception as e:
            log.warning("callback %r raised: %s", data, e)
            text, keyboard = (f"Error: {e}", None)
        await self._reply_or_edit(cq, text, keyboard)

    # -- Rich command handlers (delegated to TelegramCommands) ----------

    async def _cmd_help(self, update, context) -> None:
        await self._send_or_reply(update, await self._commands.help(), None)

    async def _cmd_equity(self, update, context) -> None:
        await self._send_or_reply(update, await self._commands.equity(), None)

    async def _cmd_positions(self, update, context) -> None:
        # Optional first arg: division slug
        slug = context.args[0] if context.args else None
        text, kb = await self._commands.positions(slug)
        await self._send_or_reply(update, text, kb)

    async def _cmd_pairs(self, update, context) -> None:
        text, kb = await self._commands.pairs()
        await self._send_or_reply(update, text, kb)

    async def _cmd_pair(self, update, context) -> None:
        if not context.args:
            await self._send_or_reply(
                update, "Usage: `/pair SYMBOL` (e.g., `/pair MSTR`)", None,
            )
            return
        text, kb = await self._commands.pair(context.args[0])
        await self._send_or_reply(update, text, kb)

    async def _cmd_vix(self, update, context) -> None:
        await self._send_or_reply(update, await self._commands.vix(), None)

    async def _cmd_regime(self, update, context) -> None:
        await self._send_or_reply(update, await self._commands.regime(), None)

    async def _cmd_pending(self, update, context) -> None:
        await self._send_or_reply(update, await self._commands.pending(), None)

    async def _cmd_mode(self, update, context) -> None:
        await self._send_or_reply(update, await self._commands.mode(), None)

    async def _cmd_halts(self, update, context) -> None:
        await self._send_or_reply(update, await self._commands.halts(), None)

    # -- Reply helpers --

    async def _send_or_reply(self, update, text: str, keyboard: Any) -> None:
        """Send a Markdown message in response to a /command."""
        try:
            await update.message.reply_text(
                text[:4090],
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception as e:
            log.warning("Telegram send failed (%s); retrying without parse_mode", e)
            await update.message.reply_text(text[:4090], reply_markup=keyboard)

    async def _reply_or_edit(self, callback_query, text: str, keyboard: Any) -> None:
        """Edit the original message in place after a button tap."""
        try:
            await callback_query.edit_message_text(
                text[:4090],
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception as e:
            # If edit fails (e.g., text identical, or markdown error), send a fresh message
            log.debug("Telegram edit failed (%s); sending new message instead", e)
            try:
                await callback_query.message.reply_text(
                    text[:4090],
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
            except Exception:
                await callback_query.message.reply_text(text[:4090], reply_markup=keyboard)

    async def _on_brief_cmd(self, update, context) -> None:
        if self._on_brief is None:
            await update.message.reply_text("(no brief handler wired)")
            return
        try:
            text = await self._on_brief()
        except Exception as e:
            text = f"brief failed: {e}"
        await update.message.reply_text(text[:4000], parse_mode="Markdown")

    async def _on_scan_cmd(self, update, context) -> None:
        if self._on_scan is None:
            await update.message.reply_text("(no scan handler wired)")
            return
        await update.message.reply_text("Running PMCC scan...")
        try:
            text = await self._on_scan()
        except Exception as e:
            text = f"scan failed: {e}"
        await update.message.reply_text(text[:4000], parse_mode="Markdown")

    async def _on_fidelity_scan_cmd(self, update, context) -> None:
        if self._on_fidelity_scan is None:
            await update.message.reply_text("(no Fidelity scan handler wired)")
            return
        await update.message.reply_text("Running Fidelity options scan...")
        try:
            text = await self._on_fidelity_scan()
        except Exception as e:
            text = f"fidelity scan failed: {e}"
        await update.message.reply_text(text[:4000], parse_mode="Markdown")

    async def _on_status_cmd(self, update, context) -> None:
        await update.message.reply_text(
            f"CEO online. Pending approvals: {len(self._pending)}."
        )

    async def _on_research_cmd(self, update, context) -> None:
        """`/research <subcommand> [args...]` — Phase 1a-1 wires
        `/research candidate <division> <n>`. The wired callback in
        main.py constructs the EngagementSpec, runs the engagement, and
        returns a markdown body."""
        if self._on_research is None:
            await update.message.reply_text("(research handler not wired)")
            return
        args = list(context.args or [])
        if not args:
            await update.message.reply_text(
                "Usage: `/research candidate <division> <n>` "
                "(e.g. `/research candidate robinhood_pmcc 3`)",
                parse_mode="Markdown",
            )
            return
        try:
            text = await self._on_research(args)
        except Exception as e:
            text = f"research command failed: {e}"
        await self._send_or_reply(update, text[:4090], None)

    async def _on_text(self, update, context) -> None:
        text = (update.message.text or "").strip()
        if text.lower().startswith("/approve "):
            return await self._handle_inline_decision(update, "approve", text[len("/approve "):].strip())
        if text.lower().startswith("/reject "):
            return await self._handle_inline_decision(update, "reject", text[len("/reject "):].strip())
        if text.lower().startswith("/modify "):
            parts = text.split(maxsplit=2)
            if len(parts) >= 3:
                order_id = parts[1]
                try:
                    qty = float(parts[2])
                except ValueError:
                    return await update.message.reply_text("modify: invalid qty")
                fut = self._pending.get(order_id)
                if fut and not fut.done():
                    fut.set_result(BoardDecision(decision="modify", reason="via cmd", new_qty=qty))
                    return await update.message.reply_text(f"modify {order_id} -> {qty}")
                return await update.message.reply_text("no pending approval with that id")
        if self._on_message is None:
            return
        try:
            reply = await self._on_message(text)
        except Exception as e:
            reply = f"CEO error: {e}"
        await update.message.reply_text(reply[:4000], parse_mode="Markdown")

    async def _handle_inline_decision(self, update, decision: str, order_id: str) -> None:
        fut = self._pending.get(order_id)
        if fut and not fut.done():
            fut.set_result(BoardDecision(decision=decision, reason=f"via /{decision}"))
            await update.message.reply_text(f"{decision} {order_id}")
        else:
            await update.message.reply_text("no pending approval with that id")
