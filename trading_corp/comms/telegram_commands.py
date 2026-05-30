"""Rich Telegram command handlers — keeps `telegram_bot.py` thin.

Each method returns either a plain string (gets sent as a reply) or a
(text, InlineKeyboardMarkup) tuple (text + tappable buttons under it).

Callback data scheme (inline button → handler):
    pair:SYM         show /pair SYM detail
    approve:SYM      execute the recommended trade for SYM
    defer:SYM        defer SYM for 24h
    div:SLUG         show positions for a specific division
    home             go back to /help

Reuses the web/data accessors so the data shape is identical between
the web dashboard and the Telegram view (one source of truth).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# Telegram message limit is 4096 chars; we cap text bodies a bit earlier
# to leave headroom for the keyboard / formatting tags.
_TG_TEXT_LIMIT = 3800

# Priority dot emoji map (matches the web dashboard)
_PRIORITY_DOT = {
    "urgent":   "🔴",
    "elevated": "🟡",
    "routine":  "⚪",
    "healthy":  "🟢",
}


class TelegramCommands:
    """All the bot's command + callback handlers.

    Instantiated once with the live deps (broker registry, agents, etc.).
    The TelegramChannel registers each method as a command/callback.
    """

    def __init__(self, deps: Any) -> None:
        # `deps` duck-types as WebDeps — has db_url, data_exec, pmcc_agent,
        # trend_agent, logger_agent, risk_agent, mode, etc.
        self.deps = deps

    # ── /help ────────────────────────────────────────────────────────────

    async def help(self) -> str:
        return (
            "🤖 *Trading Corp Bot*\n"
            "\n"
            "📊 *Information*\n"
            "/equity — corp + per-bucket + per-broker totals\n"
            "/positions — pick a division to view its holdings\n"
            "/pairs — PMCC pairs sorted by priority\n"
            "/pair `SYMBOL` — detailed analysis for one pair\n"
            "/vix — SPY · QQQ · BTC · VIX snapshot\n"
            "/regime — current trend reading\n"
            "/pending — orders waiting on Board approval\n"
            "\n"
            "⚡ *Actions*\n"
            "/scan — run PMCC scan now\n"
            "/fidelityscan — run Fidelity options scan\n"
            "/brief — morning brief\n"
            "\n"
            "🔧 *System*\n"
            "/mode — PAPER vs LIVE\n"
            "/halts — active strategy halts\n"
            "/status — bot + brokers status\n"
            "/help — show this menu"
        )

    # ── /equity ──────────────────────────────────────────────────────────

    async def equity(self) -> str:
        from trading_corp.web import data
        snap = await data.build_command_center(self.deps)

        lines = [f"💰 *Total Equity:* {_money(snap.total_equity)}", ""]

        if snap.buckets:
            lines.append("*By bucket*")
            bucket_emoji = {
                "aggressive": "🔥", "retirement": "🛡", "balanced": "⚖",
            }
            for b in snap.buckets:
                ico = bucket_emoji.get(b.intent, "•")
                lines.append(
                    f"  {ico} {b.label:<11} {_money(b.equity):>14}  "
                    f"({b.division_count} div)"
                )
            lines.append("")

        if snap.investment_groups:
            lines.append("*By investment type*")
            group_emoji = {
                "individual": "💼", "crypto": "🪙", "retirement": "🛡",
            }
            for grp in snap.investment_groups:
                ico = group_emoji.get(grp.key, "•")
                eq_str = _money(grp.total_equity) if grp.total_equity else "—"
                lines.append(
                    f"  {ico} {grp.label:<11} {eq_str:>14}  "
                    f"({len(grp.divisions)} div)"
                )
            lines.append("")

        # Market context one-liner
        vix_str = f"{snap.vix:.2f}" if snap.vix is not None else "—"
        lines.append(f"VIX `{vix_str}` · regime `{snap.regime}`")
        return _wrap_code_block_md("\n".join(lines))

    # ── /positions [slug] ────────────────────────────────────────────────

    async def positions(self, slug: str | None = None) -> tuple[str, Any]:
        from trading_corp.web import data
        from trading_corp.utils.divisions import load_divisions

        if not slug:
            # Send a keyboard of all divisions
            divs = load_divisions()
            keyboard = _make_keyboard_2col([
                (d.name, f"div:{d.slug}") for d in divs
            ] + [("◀ Back", "home")])
            return (
                "📋 *Positions* — pick a division:",
                keyboard,
            )

        view = await data.build_division_view(self.deps, slug)
        if view is None:
            return (f"Unknown division: `{slug}`", None)

        lines = [
            f"📋 *{view.division.name}*",
            f"Equity: {_money(view.equity)} · "
            f"Cash: {_money(view.cash)} · BP: {_money(view.buying_power)}",
            "",
        ]

        if view.pmcc_pairs:
            lines.append("*Option pairs* (sorted by priority)")
            for pair in view.pmcc_pairs[:15]:    # cap for readability
                dot = _PRIORITY_DOT.get(pair.priority_label, "•")
                spot = (
                    f"${pair.underlying_price:,.2f}"
                    if pair.underlying_price is not None else "—"
                )
                pnl = (
                    _money_signed(pair.combined_pnl)
                    if pair.combined_pnl is not None else "—"
                )
                lines.append(
                    f"  {dot} `{pair.underlying:<6}` spot {spot}   {pnl}"
                )
            if len(view.pmcc_pairs) > 15:
                lines.append(f"  …and {len(view.pmcc_pairs) - 15} more")
            lines.append("")

        if view.stock_holdings:
            lines.append("*Stock holdings*")
            for h in view.stock_holdings[:15]:
                pnl = (
                    _money_signed(h.unrealized_pnl)
                    if h.unrealized_pnl is not None else "—"
                )
                lines.append(
                    f"  `{h.symbol:<6}` qty {h.qty:g} · {pnl}"
                )
            if len(view.stock_holdings) > 15:
                lines.append(f"  …and {len(view.stock_holdings) - 15} more")
            lines.append("")

        if not view.pmcc_pairs and not view.stock_holdings:
            lines.append("_No positions detected._")

        # Keyboard: back to division picker + (if PMCC) view pairs
        buttons: list[tuple[str, str]] = []
        if slug == "robinhood_pmcc" and view.pmcc_pairs:
            buttons.append(("📊 View pair details", "pairs"))
        buttons.append(("◀ All divisions", "positions"))
        buttons.append(("🏠 Help", "home"))
        return (_wrap_code_block_md("\n".join(lines)), _make_keyboard_2col(buttons))

    # ── /pairs (PMCC priority list) ──────────────────────────────────────

    async def pairs(self) -> tuple[str, Any]:
        from trading_corp.web import data

        view = await data.build_division_view(self.deps, "robinhood_pmcc")
        if view is None or not view.pmcc_pairs:
            return ("No PMCC pairs detected.", None)

        lines = [f"📋 *PMCC Pairs* ({len(view.pmcc_pairs)}, by priority)", ""]
        for pair in view.pmcc_pairs:
            dot = _PRIORITY_DOT.get(pair.priority_label, "•")
            spot = (
                f"${pair.underlying_price:,.2f}"
                if pair.underlying_price is not None else "—"
            )
            pnl = (
                _money_signed(pair.combined_pnl)
                if pair.combined_pnl is not None else "—"
            )
            lines.append(
                f"  {dot} `{pair.underlying:<6}` spot {spot:<10} {pnl}"
            )

        text = _wrap_code_block_md("\n".join(lines))
        text += "\n\nTap any symbol below for detail."

        # Keyboard: 3 columns of symbol buttons
        buttons = [
            (f"{_PRIORITY_DOT.get(p.priority_label, '•')} {p.underlying}",
             f"pair:{p.underlying}")
            for p in view.pmcc_pairs
        ]
        keyboard = _make_keyboard_3col(buttons + [("🏠 Help", "home")])
        return (text, keyboard)

    # ── /pair SYM ────────────────────────────────────────────────────────

    async def pair(self, symbol: str) -> tuple[str, Any]:
        if not symbol:
            return ("Usage: `/pair SYMBOL` (e.g., `/pair MSTR`)", None)

        sym = symbol.upper()
        broker = (
            self.deps.data_exec.brokers.get("robinhood_pmcc")
            if self.deps.data_exec else None
        )
        if broker is None or self.deps.pmcc_agent is None:
            return (
                "PMCC agent not configured. Run `/scan` to verify the broker is wired.",
                None,
            )

        try:
            reading = self.deps.trend_agent.read() if self.deps.trend_agent else None
            regime = getattr(reading, "regime", "unknown") if reading else "unknown"
        except Exception:
            regime = "unknown"

        try:
            analysis = await self.deps.pmcc_agent.analyze_symbol(broker, sym, regime=regime)
        except Exception as e:
            log.warning("/pair %s: analyze_symbol failed: %s", sym, e)
            return (f"Analysis failed for `{sym}`: {e}", None)

        if analysis is None:
            return (
                f"No open position found for `{sym}` in robinhood_pmcc.",
                _make_keyboard_2col([("◀ All pairs", "pairs"), ("🏠 Help", "home")]),
            )

        # Build trade recommendation if action is non-trivial
        rec = None
        action_raw = (analysis.action or "").lower()
        actionable = action_raw not in ("", "hold", "watch")
        if actionable:
            try:
                rec = await self.deps.pmcc_agent.build_trade_recommendation(
                    broker, sym, analysis,
                )
            except Exception as e:
                log.warning("/pair %s: build_trade_recommendation failed: %s", sym, e)

        text = _format_pair_message(sym, analysis, rec)

        # Build action keyboard
        rows: list[list[tuple[str, str]]] = []
        if actionable:
            rows.append([
                ("✅ Approve & Execute", f"approve:{sym}"),
                ("⏸ Defer 24h", f"defer:{sym}"),
            ])
        rows.append([("◀ All pairs", "pairs"), ("🏠 Help", "home")])
        keyboard = _make_keyboard_rows(rows)
        return (text, keyboard)

    # ── /vix ─────────────────────────────────────────────────────────────

    async def vix(self) -> str:
        from trading_corp.utils.market_data import get_market_quote

        rows: list[str] = []
        for sym, label in [
            ("SPY",     "S&P 500"),
            ("QQQ",     "Nasdaq 100"),
            ("BTC-USD", "Bitcoin"),
            ("^VIX",    "VIX"),
        ]:
            q = get_market_quote(sym)
            if not q:
                rows.append(f"  {label:<12} —")
                continue
            price = q.get("price")
            chg = q.get("change_pct")
            arrow = "▲" if chg and chg >= 0 else "▼" if chg else " "
            chg_str = f"{chg*100:+.2f}%" if chg is not None else "—"
            price_str = f"${price:,.2f}" if price else "—"
            rows.append(f"  {label:<12} {price_str:>12}  {arrow} {chg_str}")
        text = "📊 *Market context*\n\n" + "\n".join(rows)
        return _wrap_code_block_md(text)

    # ── /regime ──────────────────────────────────────────────────────────

    async def regime(self) -> str:
        if self.deps.trend_agent is None:
            return "Trend agent not configured."
        try:
            reading = self.deps.trend_agent.read()
        except Exception as e:
            return f"Regime read failed: {e}"
        if reading is None:
            return "No regime reading available."
        return (
            f"📡 *Regime:* `{getattr(reading, 'regime', 'unknown')}`\n"
            f"_{getattr(reading, 'detail', '')}_"
        )

    # ── /pending ─────────────────────────────────────────────────────────

    async def pending(self) -> str:
        if self.deps.logger_agent is None:
            return "Logger not configured."
        from trading_corp.persistence import db
        rows = []
        try:
            with db.connect(self.deps.db_url) as conn:
                rs = conn.execute(
                    """SELECT id, ts, strategy, symbol, side, qty, rationale
                       FROM proposed_order
                       WHERE status='risk_approved'
                       ORDER BY ts DESC LIMIT 25"""
                ).fetchall()
            rows = [dict(r) for r in rs]
        except Exception as e:
            return f"Query failed: {e}"

        if not rows:
            return "✅ No pending approvals."

        lines = [f"⏳ *Pending approvals* ({len(rows)})", ""]
        for r in rows[:15]:
            ts_short = (r.get("ts") or "")[:16]
            lines.append(
                f"  `{r['symbol']}` {r['side'].upper()} ×{r['qty']:g} "
                f"({r.get('strategy', '?')})\n"
                f"      `{r['id'][:10]}` · {ts_short}"
            )
        if len(rows) > 15:
            lines.append(f"  …and {len(rows) - 15} more")
        return "\n".join(lines)

    # ── /mode ────────────────────────────────────────────────────────────

    async def mode(self) -> str:
        m = (getattr(self.deps, "mode", None) or "PAPER").upper()
        if m == "LIVE":
            return "⚠️ *LIVE* mode — orders route to real brokers."
        return "🧪 *PAPER* mode — orders route to PaperBroker (no real fills)."

    # ── /halts ───────────────────────────────────────────────────────────

    async def halts(self) -> str:
        if self.deps.logger_agent is None:
            return "Logger not configured."
        from trading_corp.persistence import db
        try:
            with db.connect(self.deps.db_url) as conn:
                rs = conn.execute(
                    """SELECT strategy, halted, halt_reason, last_loss_ts
                       FROM strategy_state WHERE halted=1"""
                ).fetchall()
            halts = [dict(r) for r in rs]
        except Exception as e:
            return f"Query failed: {e}"
        if not halts:
            return "✅ No active strategy halts."
        lines = ["🛑 *Active halts*", ""]
        for h in halts:
            lines.append(
                f"  `{h['strategy']}` — {h.get('halt_reason') or '(no reason given)'}"
            )
        return "\n".join(lines)

    # ── Callback handler (button taps) ───────────────────────────────────

    async def handle_callback(self, data: str) -> tuple[str, Any]:
        """Route an inline-button callback to the right command."""
        if not data or data == "home":
            return (await self.help(), None)
        if data == "pairs":
            return await self.pairs()
        if data == "positions":
            return await self.positions()

        prefix, _, arg = data.partition(":")
        if prefix == "pair":
            return await self.pair(arg)
        if prefix == "div":
            return await self.positions(arg)
        if prefix == "approve":
            return await self.execute_pair(arg)
        if prefix == "defer":
            return await self.defer_pair(arg)

        return (f"Unknown action: `{data}`", None)

    # ── Approve & Execute (mirrors the web button) ───────────────────────

    async def execute_pair(self, symbol: str) -> tuple[str, Any]:
        sym = (symbol or "").upper()
        if not sym:
            return ("Missing symbol.", None)
        slug = "robinhood_pmcc"
        broker = (
            self.deps.data_exec.brokers.get(slug) if self.deps.data_exec else None
        )
        if broker is None or self.deps.pmcc_agent is None:
            return ("Execute failed: broker/agent not wired.", None)

        try:
            reading = self.deps.trend_agent.read() if self.deps.trend_agent else None
            regime = getattr(reading, "regime", "unknown") if reading else "unknown"
        except Exception:
            regime = "unknown"

        try:
            analysis = await self.deps.pmcc_agent.analyze_symbol(broker, sym, regime=regime)
        except Exception as e:
            return (f"Could not regenerate analysis: {e}", None)
        if analysis is None:
            return (f"No open position for `{sym}`.", None)

        action_raw = (analysis.action or "").lower()
        if action_raw in ("", "hold", "watch"):
            return (
                f"Action is `{action_raw or 'unknown'}` — nothing to execute on `{sym}`.",
                None,
            )

        try:
            orders = await self.deps.pmcc_agent.propose_orders_for_pair(
                broker, sym, analysis,
            )
        except Exception as e:
            return (f"Order build failed: {e}", None)
        if not orders:
            return (
                f"`{sym}`: action `{action_raw}` produced no orders.",
                None,
            )

        # Risk-eval + place each order (same as web route, condensed)
        from trading_corp.persistence.models import AccountState, StrategyState
        try:
            snap = await broker.snapshot()
            equity = float(getattr(snap, "equity", 0.0) or 0.0)
        except Exception:
            equity = 100_000.0
        account = AccountState(
            account=slug, equity=equity or 100_000.0,
            peak_equity=equity or 100_000.0,
        )
        strat_state = StrategyState.from_persistence("robinhood_pmcc", db_url=self.deps.db_url)

        results: list[str] = [f"⚡ *Executing on `{sym}`...*"]
        for order in orders:
            verdict = self.deps.risk_agent.evaluate(
                order, account, strat_state, regime, None,
            )
            order.risk_reason = verdict.reason
            if verdict.verdict == "reject":
                order.status = "risk_rejected"
                self.deps.logger_agent.log_proposed_order(order)
                self.deps.logger_agent.log_event(
                    actor="risk", kind="risk_rejected",
                    payload={"order_id": order.id, "symbol": sym,
                             "reason": verdict.reason, "via": "telegram"},
                )
                results.append(f"  🛑 risk rejected: {verdict.reason}")
                continue
            if verdict.verdict == "resize" and verdict.new_qty is not None:
                order.qty = float(verdict.new_qty)
            order.status = "board_approved"
            order.board_reason = "approved via telegram"
            self.deps.logger_agent.log_proposed_order(order)
            self.deps.logger_agent.log_event(
                actor="board", kind="board_approved",
                payload={"order_id": order.id, "symbol": sym,
                         "via": "telegram", "qty": order.qty},
            )
            try:
                fill = await self.deps.data_exec.place(order, division=slug)
                results.append(
                    f"  ✅ {order.side.upper()} ×{order.qty:g} "
                    f"@ ${fill.price:.2f} ({fill.venue})"
                )
            except Exception as e:
                self.deps.logger_agent.log_event(
                    actor="data_exec", kind="execution_error",
                    payload={"order_id": order.id, "symbol": sym, "error": str(e)},
                )
                results.append(f"  ⚠️ execution error: {e}")

        keyboard = _make_keyboard_2col([("◀ All pairs", "pairs"), ("🏠 Help", "home")])
        return ("\n".join(results), keyboard)

    # ── Defer 24h (mirrors web button) ───────────────────────────────────

    async def defer_pair(self, symbol: str) -> tuple[str, Any]:
        sym = (symbol or "").upper()
        if not sym:
            return ("Missing symbol.", None)
        if self.deps.logger_agent:
            self.deps.logger_agent.log_event(
                actor="board", kind="pair_deferred",
                payload={
                    "slug": "robinhood_pmcc", "symbol": sym,
                    "ttl_hours": 24, "via": "telegram",
                },
            )
        text = (
            f"⏸ *Deferred `{sym}` for 24 hours.*\n"
            "_The system will skip re-analyzing this position until the deferral expires._"
        )
        keyboard = _make_keyboard_2col([("◀ All pairs", "pairs"), ("🏠 Help", "home")])
        return (text, keyboard)


# ── Helpers ──────────────────────────────────────────────────────────────

def _money(v: float | None) -> str:
    if v is None:
        return "—"
    return f"${v:,.2f}"


def _money_signed(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.2f}"


def _wrap_code_block_md(text: str) -> str:
    """Wrap a block of text in Telegram-Markdown so monospace alignment works."""
    return f"```\n{text}\n```"


def _make_keyboard_2col(items: list[tuple[str, str]]):
    """Build a 2-column inline keyboard from (label, callback_data) pairs."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows: list[list] = []
    for i in range(0, len(items), 2):
        chunk = items[i:i + 2]
        rows.append([
            InlineKeyboardButton(label, callback_data=cb)
            for label, cb in chunk
        ])
    return InlineKeyboardMarkup(rows)


def _make_keyboard_3col(items: list[tuple[str, str]]):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows: list[list] = []
    for i in range(0, len(items), 3):
        chunk = items[i:i + 3]
        rows.append([
            InlineKeyboardButton(label, callback_data=cb)
            for label, cb in chunk
        ])
    return InlineKeyboardMarkup(rows)


def _make_keyboard_rows(rows: list[list[tuple[str, str]]]):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=cb) for label, cb in row]
        for row in rows
    ])


def _format_pair_message(sym: str, analysis, rec) -> str:
    """Human-readable Telegram-Markdown summary of a pair's analysis."""
    urgency = (analysis.urgency or "routine").lower()
    dot = _PRIORITY_DOT.get(urgency, "⚪")
    action_str = (analysis.action or "—").upper().replace("_", " ")
    confidence_pct = int((analysis.confidence or 0) * 100)

    lines = [
        f"{dot} *{sym}* — `{action_str}` ({confidence_pct}% conf)",
        "",
        f"_{(analysis.summary or '').strip()}_",
        "",
    ]

    rationale = (analysis.rationale or "").strip()
    if rationale:
        # Trim long rationales to keep Telegram message digestible
        if len(rationale) > 800:
            rationale = rationale[:790] + "…"
        lines.append(rationale)
        lines.append("")

    if analysis.warnings:
        lines.append("*Warnings*")
        for w in analysis.warnings[:5]:
            lines.append(f"⚠️ {w}")
        lines.append("")

    if rec is not None and rec.legs:
        lines.append("*Recommended trade*")
        for leg in rec.legs:
            label = leg.action_label
            strike = f"${leg.strike:,.2f}{leg.option_type[:1].upper()}"
            cost = leg.estimated_dollars
            cost_str = (
                f"-${abs(cost):,.2f}" if cost > 0
                else f"+${abs(cost):,.2f}" if cost < 0
                else "$0.00"
            )
            lines.append(
                f"  • {label}: `{leg.underlying} {leg.expiry} {strike}` "
                f"×{leg.qty} → {cost_str}"
            )
        net = rec.net_cost_dollars
        net_str = (
            f"Net debit -${net:,.2f}" if net > 0
            else f"Net credit +${abs(net):,.2f}" if net < 0
            else "Net $0.00"
        )
        lines.append(f"  *{net_str}* · cost confidence `{rec.cost_confidence}`")
        lines.append("")

        if rec.benefits:
            lines.append("*Expected benefit*")
            for b in rec.benefits[:5]:
                lines.append(f"✓ {b}")

    text = "\n".join(lines).strip()
    if len(text) > _TG_TEXT_LIMIT:
        text = text[:_TG_TEXT_LIMIT - 3] + "…"
    return text
