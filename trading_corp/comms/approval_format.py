"""Rich approval-request message builder.

Replaces the single-line `{strategy}: {side} {qty} {symbol} (risk: ...)`
summary with a multi-line, decision-quality body. Used in
`graph/ceo_graph.py` when constructing ApprovalRequest, and rendered by
`comms/telegram_bot.py:request_approval` as the Telegram message body.

Telegram-Markdown-safe (legacy parse mode):
  - Only *bold* and `inline code` for emphasis
  - No italics (underscores in tickers break legacy Markdown)
  - No nested markdown
  - Multi-line works (Telegram preserves newlines)
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


# --------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------

def format_approval_message(
    order: Any,
    risk_verdict: dict | None = None,
    division: str | None = None,
    position_context: dict | None = None,
) -> str:
    """Build a multi-line approval message body.

    Args:
        order: ProposedOrder (or DB-row dict with the same shape).
        risk_verdict: dict with at least `reason` and `verdict` keys.
        division: optional division slug (for the header line).
        position_context: optional dict with prior-fill / LEAP details
            for richer context. Caller (ceo_graph) populates this from
            the broker snapshot + audit log when available. None is a
            valid value — the message just omits the context block.

    Returns:
        Multi-line Telegram-Markdown-safe string. The caller wraps it
        in the standard "*Approval requested*\\n...order id..." shell.
    """
    extra = _extra(order)
    is_option = bool(extra.get("is_option"))
    is_crypto = (extra.get("asset_type") == "crypto")

    lines: list[str] = []

    # ── Header: action emoji + action label + symbol ──
    lines.append(_format_header(order, extra, division))
    lines.append("")  # blank line between header and detail

    # ── Body: option / crypto / stock ──
    if is_option:
        lines.extend(_format_option_lines(order, extra))
    elif is_crypto:
        lines.extend(_format_crypto_lines(order, extra))
    else:
        lines.extend(_format_stock_lines(order, extra))

    # ── Position context (LEAP, prior rolls, P&L) — optional ──
    if position_context:
        lines.append("")
        lines.extend(_format_position_context(position_context))

    # ── Risk verdict ──
    lines.append("")
    lines.append(_format_risk(risk_verdict))

    return "\n".join(lines)


# --------------------------------------------------------------------
# Header
# --------------------------------------------------------------------

def _format_header(order: Any, extra: dict, division: str | None) -> str:
    action_emoji = _action_emoji(order, extra)
    action_label = _action_label(order, extra)
    sym = extra.get("underlying", "") or _attr(order, "symbol", "")

    parts = [f"{action_emoji} *{action_label}*"]
    if sym:
        parts.append(f"· `{sym}`")
    if division:
        parts.append(f"· _{_telegram_safe(division)}_".replace("_", ""))
        # ^ legacy Markdown italic uses underscores; division names with
        # underscores would self-destruct. Strip italics in favor of plain.
        parts[-1] = f"· {division}"

    return " ".join(parts)


def _action_emoji(order: Any, extra: dict) -> str:
    """Pick an emoji based on the order's intent."""
    action = (extra.get("action") or "").lower()
    if extra.get("is_close"):
        return "📤"
    if "roll" in action:
        return "🔄"
    if "open" in action:
        return "📥"
    if "close" in action:
        return "📤"
    side = (_attr(order, "side") or "").lower()
    if side == "buy":
        return "📥"
    if side == "sell":
        return "📤"
    return "🎲"


def _action_label(order: Any, extra: dict) -> str:
    """Human-readable label for what this order does."""
    action = (extra.get("action") or "").lower()
    side = (_attr(order, "side") or "").upper()

    # PMCC-specific named actions
    pmcc_labels = {
        "roll_short_call_close": "ROLL: BUY TO CLOSE",
        "roll_short_call_open":  "ROLL: SELL TO OPEN",
        "open_leap":             "BUY LEAP TO OPEN",
        "open_short_call":       "SELL CALL TO OPEN",
        "close_short_call":      "BUY CALL TO CLOSE",
        "close_leap":            "SELL LEAP TO CLOSE",
        "open_pmcc":             "OPEN PMCC PAIR",
    }
    if action in pmcc_labels:
        return pmcc_labels[action]

    # Lord Otter close-long
    if extra.get("is_close"):
        tier = (extra.get("tier") or "").upper()
        if tier:
            return f"CLOSE LONG ({tier})"
        return "CLOSE LONG"

    # Generic
    if extra.get("is_option"):
        pe = (extra.get("position_effect") or "").lower()
        if side == "BUY"  and pe == "open":  return "BUY TO OPEN"
        if side == "BUY"  and pe == "close": return "BUY TO CLOSE"
        if side == "SELL" and pe == "open":  return "SELL TO OPEN"
        if side == "SELL" and pe == "close": return "SELL TO CLOSE"

    return f"{side}"


# --------------------------------------------------------------------
# Option legs
# --------------------------------------------------------------------

def _format_option_lines(order: Any, extra: dict) -> list[str]:
    """Render an option contract: strike, expiration, dte, delta, dollars."""
    qty = float(_attr(order, "qty", 0) or 0)
    side = (_attr(order, "side") or "").lower()
    underlying = extra.get("underlying", "") or _attr(order, "symbol", "")
    expiry = extra.get("expiration", "")
    strike = float(extra.get("strike") or 0)
    otype = (extra.get("option_type") or "call").upper()[:1]   # C or P
    dte = extra.get("dte")
    delta = extra.get("delta")
    mark = extra.get("mark_per_share") or _attr(order, "limit_price")
    bid = extra.get("bid")
    ask = extra.get("ask")

    lines = []

    # Contract row: BUY ×3 RKLB $30C 2026-05-30
    contract = f"`{underlying} ${strike:,.2f}{otype} {expiry}`"
    qty_str = _qty_str(qty)
    lines.append(f"   {side.upper()} *×{qty_str}* {contract}")

    # Greeks / DTE row
    detail_parts = []
    if dte is not None:
        detail_parts.append(f"{int(dte)}d")
    if delta is not None:
        detail_parts.append(f"δ{float(delta):+.2f}")
    if detail_parts:
        lines.append(f"   {' · '.join(detail_parts)}")

    # Pricing row + dollars
    if mark:
        mark = float(mark)
        # Options control 100 shares per contract
        gross = abs(mark * qty * 100)
        bid_ask = ""
        if bid and ask and bid > 0 and ask > 0:
            bid_ask = f" (bid ${float(bid):.2f}/ask ${float(ask):.2f})"
        lines.append(f"   @ *${mark:.2f}*/sh{bid_ask}")
        if side == "buy":
            lines.append(f"   = *−${gross:,.2f}* debit")
        else:
            lines.append(f"   = *+${gross:,.2f}* credit")

    return lines


# --------------------------------------------------------------------
# Crypto / spot
# --------------------------------------------------------------------

def _format_crypto_lines(order: Any, extra: dict) -> list[str]:
    qty = float(_attr(order, "qty", 0) or 0)
    side = (_attr(order, "side") or "").upper()
    sym = _attr(order, "symbol", "")
    tier = (extra.get("tier") or "").upper()

    lines = []

    # Tier badge
    if tier:
        lines.append(f"🦦 Tier *{tier}*")

    # Quantity row
    qty_str = _qty_str(qty, decimals=8)
    lines.append(f"   {side} *×{qty_str}* `{sym}`")

    # Notional / sizing basis
    notional = extra.get("notional_target")
    sizing = extra.get("sizing_basis")
    limit_price = _attr(order, "limit_price")
    if notional:
        notional = float(notional)
        sign = "+" if side == "SELL" else "−"
        label = "credit" if side == "SELL" else "debit"
        sizing_note = f" ({sizing})" if sizing else ""
        lines.append(f"   ≈ *{sign}${notional:,.2f}* {label}{sizing_note}")
    elif limit_price:
        gross = float(limit_price) * qty
        sign = "+" if side == "SELL" else "−"
        label = "credit" if side == "SELL" else "debit"
        lines.append(f"   @ ${float(limit_price):,.2f} = *{sign}${gross:,.2f}* {label}")
    else:
        order_type = (_attr(order, "order_type") or "market").upper()
        lines.append(f"   {order_type}")

    # Stop loss + dollar risk
    stop = extra.get("stop_price")
    if stop:
        stop = float(stop)
        stop_basis = extra.get("stop_basis", "")
        risk = float(extra.get("max_dollar_risk") or 0)
        lines.append(
            f"   🛡 Stop *${stop:,.2f}* ({stop_basis}) · max loss ${risk:.2f}"
        )

    # Resize disclosure
    if extra.get("resized_for_max_loss"):
        lines.append("   ⚙ Position SHRUNK to honor max-loss cap")

    return lines


# --------------------------------------------------------------------
# Stock
# --------------------------------------------------------------------

def _format_stock_lines(order: Any, extra: dict) -> list[str]:
    qty = float(_attr(order, "qty", 0) or 0)
    side = (_attr(order, "side") or "").upper()
    sym = _attr(order, "symbol", "")
    order_type = (_attr(order, "order_type") or "market").upper()
    limit_price = _attr(order, "limit_price")

    qty_str = _qty_str(qty, decimals=4)
    lines = [f"   {side} *×{qty_str}* `{sym}` ({order_type.lower()})"]

    if limit_price:
        gross = abs(float(limit_price) * qty)
        sign = "+" if side == "SELL" else "−"
        label = "credit" if side == "SELL" else "debit"
        lines.append(
            f"   @ ${float(limit_price):,.2f} = *{sign}${gross:,.2f}* {label}"
        )

    return lines


# --------------------------------------------------------------------
# Position context (caller provides — ceo_graph queries broker + DB)
# --------------------------------------------------------------------

def _format_position_context(ctx: dict) -> list[str]:
    """Render the position-context block.

    Expected keys (all optional — present whenever the caller can fill them):
      leap          — {strike, expiration, cost_basis, mark, dte, days_held}
      unrealized_pnl_dollars
      unrealized_pnl_pct
      paired_short  — same structure as leap but for the paired short
      roll_count    — int, how many rolls on this pair
      prior_credit_total  — cumulative credit collected on prior rolls
    """
    lines = ["📊 *Position context*"]

    leap = ctx.get("leap")
    if leap:
        underlying = leap.get("underlying", "")
        strike = float(leap.get("strike") or 0)
        expiry = leap.get("expiration", "")
        cost = leap.get("cost_basis")
        mark = leap.get("mark")
        dte = leap.get("dte")
        days_held = leap.get("days_held")
        bits = [f"`{underlying} ${strike:,.2f}C {expiry}`"]
        if dte is not None:
            bits.append(f"{int(dte)}d")
        if days_held is not None:
            bits.append(f"held {int(days_held)}d")
        lines.append(f"   LEAP: {' · '.join(bits)}")
        if cost is not None and mark is not None:
            cost, mark = float(cost), float(mark)
            pct = ((mark / cost) - 1) * 100 if cost > 0 else 0
            sign = "+" if pct >= 0 else "−"
            lines.append(
                f"   cost ${cost:.2f} · mark ${mark:.2f} · {sign}{abs(pct):.1f}%"
            )

    upl = ctx.get("unrealized_pnl_dollars")
    upl_pct = ctx.get("unrealized_pnl_pct")
    if upl is not None:
        sign = "+" if upl >= 0 else "−"
        pct_str = ""
        if upl_pct is not None:
            pct_sign = "+" if upl_pct >= 0 else "−"
            pct_str = f" ({pct_sign}{abs(float(upl_pct))*100:.1f}%)"
        lines.append(f"   Unrealized: *{sign}${abs(float(upl)):,.2f}*{pct_str}")

    roll_count = ctx.get("roll_count")
    prior_credit = ctx.get("prior_credit_total")
    if roll_count is not None:
        if prior_credit is not None:
            sign = "+" if prior_credit >= 0 else "−"
            lines.append(
                f"   Roll #{int(roll_count) + 1} on this pair · "
                f"prior {int(roll_count)} netted *{sign}${abs(float(prior_credit)):,.2f}*"
            )
        else:
            lines.append(f"   Roll #{int(roll_count) + 1} on this pair")

    return lines


# --------------------------------------------------------------------
# Risk verdict
# --------------------------------------------------------------------

def _format_risk(risk_verdict: dict | None) -> str:
    if not risk_verdict:
        return "⚙ Risk: _not evaluated_"
    decision = (risk_verdict.get("verdict") or "approve").lower()
    reason = risk_verdict.get("reason", "ok")
    icon = {"approve": "✅", "resize": "⚠", "reject": "🛑"}.get(decision, "⚙")
    return f"⚙ Risk: {icon} {reason}"


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------

def _extra(order: Any) -> dict:
    """ProposedOrder dataclass OR dict from to_db_row both work."""
    e = _attr(order, "extra", {})
    return e if isinstance(e, dict) else {}


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Get attribute or dict key — accepts both dataclass and dict shapes."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _qty_str(qty: float, decimals: int = 0) -> str:
    """Human-friendly qty: drop trailing zeros for fractional values."""
    if qty == int(qty) and decimals == 0:
        return f"{int(qty)}"
    if qty < 1 and decimals > 0:
        return f"{qty:.{decimals}f}".rstrip("0").rstrip(".")
    return f"{qty:g}"


def _telegram_safe(s: str) -> str:
    """Strip characters that break Telegram legacy Markdown.

    Currently this is a no-op for division names (which are slugs) but
    centralized here so we have one place to add escapes if Telegram
    parses something unexpected. Avoid `_*[]()` which all have meaning.
    """
    return s
