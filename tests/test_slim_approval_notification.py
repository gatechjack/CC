"""Tests for the Phase A slim Telegram notification formatter.

Pins the body shape used when TelegramChannel is constructed with
`notification_only=True` (Board direction 2026-05-03 — HITL moves to
the web app; Telegram becomes notification + deeplink).

Format spec:
    {ACTION_LABEL} · {SYMBOL}[ · {division}]

    [Review on dashboard →]({base_url}/approvals/{order_id})

No order detail in the body — the dashboard is what the Board reads
the rest from. Caller (TelegramChannel.request_approval) wraps this
in the standard "🎲 *Approval needed*\\n...order id..." shell.
"""
from __future__ import annotations

from types import SimpleNamespace

from trading_corp.comms.approval_format import (
    DEFAULT_DASHBOARD_BASE_URL,
    format_slim_approval_notification,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _opt_order(symbol: str = "MSTR", action: str = "roll_short_call_open",
               underlying: str = "MSTR", side: str = "sell",
               qty: float = 1.0) -> SimpleNamespace:
    """ProposedOrder-shaped namespace with the option-extra dict the
    formatter reads from."""
    return SimpleNamespace(
        symbol=symbol, side=side, qty=qty,
        extra={
            "is_option": True, "underlying": underlying,
            "action": action, "option_type": "call",
            "expiration": "2026-05-09", "strike": 170.0,
        },
    )


def _stock_order(symbol: str = "AAPL", side: str = "buy",
                 qty: float = 100.0) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol, side=side, qty=qty, extra={},
    )


# ── Tests ────────────────────────────────────────────────────────────────


def test_slim_format_option_roll_short_with_division():
    out = format_slim_approval_notification(
        order=_opt_order(action="roll_short_call_open"),
        order_id="abc12345",
        division="robinhood_pmcc",
    )
    # Headline carries action label + symbol + division separated by ·
    assert "MSTR" in out
    assert "robinhood_pmcc" in out
    # Markdown deeplink to the production dashboard, default URL
    assert (
        "[Review on dashboard →]"
        "(https://trading.jacksumner.com/approvals/abc12345)"
    ) in out


def test_slim_format_uses_custom_base_url():
    out = format_slim_approval_notification(
        order=_opt_order(),
        order_id="dev-1",
        division="robinhood_pmcc",
        base_url="https://staging.example.com",
    )
    assert "https://staging.example.com/approvals/dev-1" in out
    assert "trading.jacksumner.com" not in out


def test_slim_format_strips_trailing_slash_on_base_url():
    """Caller-provided base URL with trailing / shouldn't double-up
    the path separator."""
    out = format_slim_approval_notification(
        order=_opt_order(),
        order_id="x",
        base_url="https://example.com/",
    )
    assert "https://example.com/approvals/x" in out
    assert "//approvals" not in out


def test_slim_format_no_division_omits_separator():
    out = format_slim_approval_notification(
        order=_opt_order(),
        order_id="abc12345",
        division=None,
    )
    # Headline should not have a trailing " · " when division is None
    headline_line = out.split("\n", 1)[0]
    assert not headline_line.endswith(" · ")
    # Symbol still present
    assert "MSTR" in headline_line


def test_slim_format_falls_back_to_order_symbol_when_no_underlying():
    """Stock orders don't have extra.underlying; the formatter must
    fall back to order.symbol so the headline still names the
    instrument."""
    out = format_slim_approval_notification(
        order=_stock_order(symbol="AAPL"),
        order_id="x",
        division="robinhood_pmcc",
    )
    assert "AAPL" in out


def test_slim_format_body_is_short():
    """Slim format must stay well under Telegram's 4096-char cap and
    well under the rich format's typical ~600-800 chars. Sanity bound:
    <300 chars total — short enough that mobile preview shows the
    whole thing without truncation."""
    out = format_slim_approval_notification(
        order=_opt_order(),
        order_id="abc12345-1234-5678-9abc-def012345678",
        division="robinhood_pmcc",
        base_url="https://trading.jacksumner.com",
    )
    assert len(out) < 300, f"Slim format grew to {len(out)} chars: {out!r}"


def test_slim_format_omits_order_detail():
    """Spec: no order detail in body — strike, expiration, qty, etc.
    are NOT in the slim format. Those live on the dashboard. Pin
    that we don't accidentally regress to a 'rich-with-link' body."""
    out = format_slim_approval_notification(
        order=_opt_order(action="roll_short_call_open"),
        order_id="x",
        division="robinhood_pmcc",
    )
    # Expiration, strike, dollar-amounts, deltas should NOT appear
    assert "2026-05-09" not in out  # expiration
    assert "170.0" not in out and "$170" not in out  # strike
    assert "delta" not in out.lower()


def test_slim_format_uses_default_base_url_when_unspecified():
    """No base_url kwarg → DEFAULT_DASHBOARD_BASE_URL (production)."""
    out = format_slim_approval_notification(
        order=_opt_order(),
        order_id="x",
    )
    assert DEFAULT_DASHBOARD_BASE_URL in out


def test_slim_format_dict_order_works():
    """Caller may pass a dict (DB-row shape) instead of a ProposedOrder
    namespace. Both must work — TelegramChannel constructs from
    `req.detail['order']` which can be either shape."""
    order_dict = {
        "symbol": "RKLB", "side": "buy", "qty": 1.0,
        "extra": {
            "is_option": True, "underlying": "RKLB",
            "action": "open_leap",
        },
    }
    out = format_slim_approval_notification(
        order=order_dict,
        order_id="x",
        division="robinhood_pmcc",
    )
    assert "RKLB" in out


def test_slim_format_handles_empty_order_dict():
    """Defensive: if the caller passes an empty dict (degenerate path),
    formatter still produces a deeplink — the body is the URL plus
    whatever headline parts are recoverable."""
    out = format_slim_approval_notification(
        order={},
        order_id="abc",
        division="robinhood_pmcc",
    )
    assert "abc" in out  # deeplink at minimum
    assert "robinhood_pmcc" in out  # division still rendered


def test_telegram_notification_only_omits_inline_keyboard():
    """Per CLAUDE.md §HITL surface direction, Telegram is one-way:
    no inline keyboard in notification-only mode. Decisions live on
    the web dashboard at /approvals/{order_id}. Pre-fix the keyboard
    was retained as a 'belt-and-suspenders fallback' (deploy_log
    2026-05-05 B.4 entry) but the rule wins per Board direction
    2026-05-08."""
    from types import SimpleNamespace
    from trading_corp.comms.telegram_bot import TelegramChannel
    from trading_corp.graph.interrupts import ApprovalRequest

    chan = TelegramChannel(
        token="dummy", chat_id="123", notification_only=True,
    )
    req = ApprovalRequest(
        order_id="abc12345",
        summary="(rich body — not used in slim mode)",
        detail={
            "division": "robinhood_pmcc",
            "order": SimpleNamespace(
                symbol="MSTR", side="sell", qty=1.0,
                extra={"is_option": True, "underlying": "MSTR",
                       "action": "roll_short_call_open"},
            ),
        },
    )
    text, kb = chan._build_approval_message(req)
    # Notification-only: no keyboard. Period.
    assert kb is None, (
        f"notification-only mode must not return an inline keyboard "
        f"(got {type(kb).__name__})"
    )
    # Body must still be a slim deeplink — not the rich body.
    assert "Review on dashboard" in text
    assert "abc12345" in text
    # No "Tap Approve / Reject" guidance — would be misleading.
    assert "Approve" not in text
    assert "Reject" not in text


def test_telegram_rich_mode_keeps_inline_keyboard():
    """Rich mode (notification_only=False) is the legacy path —
    inline keyboard is the user-facing decision surface there. Pin
    that this didn't accidentally regress when the slim branch was
    refactored."""
    from trading_corp.comms.telegram_bot import TelegramChannel
    from trading_corp.graph.interrupts import ApprovalRequest

    chan = TelegramChannel(
        token="dummy", chat_id="123", notification_only=False,
    )
    req = ApprovalRequest(
        order_id="abc12345",
        summary="*BUY* AAPL × 1 @ $150.00",
        detail={"division": "robinhood_pmcc"},
    )
    text, kb = chan._build_approval_message(req)
    assert kb is not None, "rich mode must produce an inline keyboard"


def test_slim_format_safe_for_legacy_markdown_parse_mode():
    """Telegram legacy Markdown breaks on unescaped underscores in
    arbitrary text — every `_` opens or closes an italic span and
    odd counts trigger 'Can't parse entities'. Underscores appear
    in division slugs (`robinhood_pmcc`, `lord_otter`) — wrap them
    in backticks so legacy Markdown reads them as literal `code`.

    Regression test for the 2026-05-08 incident where every PMCC
    approval ping failed with 'Can't parse entities: can't find end
    of the entity starting at byte offset 26X' because the bare
    `robinhood_pmcc` slug introduced a single unmatched underscore
    that, combined with the trailer line's two underscores, summed
    to an odd count."""
    out = format_slim_approval_notification(
        order=_opt_order(),
        order_id="x",
        division="robinhood_pmcc",
    )
    headline = out.split("\n", 1)[0]
    # Division slug must be backtick-wrapped so its `_` is literal.
    assert "`robinhood_pmcc`" in headline
    # Outside backtick spans (where Markdown is active) the slim body
    # must contribute zero unescaped underscores — the wrapper line
    # `_Tap Approve... link._` in telegram_bot.py adds two of its own,
    # so the slim body must be net-zero to keep the combined total
    # even and the parser happy.
    import re
    bare = re.sub(r"`[^`]*`", "", out)
    assert "_" not in bare, (
        f"unescaped `_` in slim body (outside backticks) would trip "
        f"Telegram parser when combined with wrapper trailer: {out!r}"
    )
