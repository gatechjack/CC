"""Structured-dict builder for the HITL approval surface.

Phase B.2 of HITL-in-app. The web `/approvals/{order_id}` template
needs the same data the Telegram rich body renders, but in a shape
HTML can iterate (rather than pre-formatted Markdown lines). This
module shapes `ApprovalRequest.detail` (and the embedded
`order.extra_json`) into a structured dict that the Jinja template
consumes directly.

Telegram's `comms/approval_format.py` continues to use its existing
string-output formatters in B.2 — no behavior change there. A future
B.x polish pass can refactor Telegram to consume this dict too.

Returned dict shape (all sub-fields optional):

    {
      "headline":  {"emoji", "label", "symbol", "division"},
      "trade":     {"asset_class", "legs": [...], "net_dollars"},
      "context":   {"leap": {...}, "unrealized_pnl_dollars",
                    "unrealized_pnl_pct", "roll_count",
                    "prior_credit_total"},
      "risk":      {"verdict", "reason"},
      "warnings":  [...],   # strings — analysis.warnings if present
      "pmcc_pair_id":   str | None,   # for sibling lookup at render time
      "raw_extra": dict,             # full decoded extra for debug/expand
    }

Each leg entry inside `trade.legs`:
    {
      "side":          "buy" | "sell",
      "qty":           float,
      "qty_str":       human-friendly qty string,
      "asset_class":   "option" | "crypto" | "stock",
      "symbol":        str,                    # underlying for options
      "action_label":  "BUY TO OPEN" / "ROLL: SELL TO OPEN" / etc,
      "option":        {"strike", "expiration", "option_type",
                        "dte", "delta"} | None,
      "mark":          float | None,           # per-share/per-unit
      "bid":           float | None,
      "ask":           float | None,
      "gross_dollars": float,                  # absolute dollar magnitude
      "side_sign":     +1 (sell/credit) | -1 (buy/debit),
      "rationale":     str | None,
    }
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)


# ── Public entry point ────────────────────────────────────────────────


def build_approval_view(detail: dict) -> dict:
    """Build the structured view dict for ONE pending approval.

    `detail` is `ApprovalRequest.detail` produced by graph/ceo_graph.py:
        {"order": <to_db_row dict>, "risk_verdict": dict, "division": str}

    Returns the structured dict shape documented at the module level.
    Defensive against missing/malformed fields — the template can
    safely iterate even when upstream omits values.
    """
    order_row = detail.get("order") or {}
    risk_verdict = detail.get("risk_verdict") or {}
    division = detail.get("division")

    extra = _decode_extra(order_row)
    asset_class = _classify_asset(extra)
    leg = _build_leg(order_row, extra, asset_class)

    return {
        "headline": _build_headline(order_row, extra, division, asset_class),
        "trade": {
            "asset_class": asset_class,
            "legs": [leg],
            "net_dollars": leg["gross_dollars"] * leg["side_sign"],
        },
        "context": _build_context_block(extra),
        "risk": _build_risk(risk_verdict),
        "warnings": list(extra.get("warnings") or []),
        "pmcc_pair_id": extra.get("pmcc_pair_id"),
        # Phase A: a roll_leap leg is ADVISORY — the operator executes the LEAP
        # roll manually; the agent never places it. Keyed on the action prefix
        # (survives the ceo_graph reconstruct path, same signal the fail-closed
        # dispatch guard uses). The card renders a banner + a disabled Approve.
        "advisory": str(extra.get("action") or "").startswith("roll_leap"),
        "raw_extra": extra,
    }


def coalesce_paired_view(views: list[dict]) -> dict:
    """Combine two views (close + open of a PMCC roll) into one card.

    Caller has already verified the two views share `pmcc_pair_id`.
    Returns a view shape compatible with the single-leg one — same keys,
    but `trade.legs` carries both legs and `net_dollars` is the sum of
    each leg's signed gross.

    Headline is taken from the close leg (typically the "anchor" of the
    pair); risk + context use the close leg's values too (both legs
    carry identical position_context per pmcc_robinhood scan path).
    """
    if not views:
        raise ValueError("coalesce_paired_view: no views to coalesce")
    if len(views) == 1:
        return views[0]

    # Close-first ordering: buy-to-close before sell-to-open feels right
    # for a roll. Sort by side (buy = close = first).
    sorted_views = sorted(
        views,
        key=lambda v: 0 if v["trade"]["legs"][0]["side"] == "buy" else 1,
    )

    anchor = sorted_views[0]
    all_legs = [v["trade"]["legs"][0] for v in sorted_views]
    net = sum(leg["gross_dollars"] * leg["side_sign"] for leg in all_legs)

    headline = dict(anchor["headline"])
    headline["label"] = "ROLL · CLOSE + OPEN"
    headline["emoji"] = "🔄"

    return {
        "headline": headline,
        "trade": {
            "asset_class": anchor["trade"]["asset_class"],
            "legs": all_legs,
            "net_dollars": net,
        },
        "context": anchor["context"],
        "risk": anchor["risk"],   # both legs were risk-evaluated independently;
                                  # the close leg's verdict is shown as the
                                  # primary. B.x polish: surface both verdicts.
        "warnings": list(set(
            w for v in sorted_views for w in v["warnings"]
        )),
        "pmcc_pair_id": anchor["pmcc_pair_id"],
        # Phase A: advisory if ANY coalesced leg is a roll_leap leg.
        "advisory": any(v.get("advisory") for v in sorted_views),
        "raw_extra": anchor["raw_extra"],
        "is_paired": True,
        "paired_order_ids": [
            v["raw_extra"].get("_order_id_for_paired") or v.get("_order_id")
            for v in sorted_views
        ],
    }


# ── Block builders ────────────────────────────────────────────────────


def _build_headline(
    order_row: dict, extra: dict, division: str | None, asset_class: str,
) -> dict:
    return {
        "emoji": _action_emoji(order_row, extra),
        "label": _action_label(order_row, extra),
        "symbol": (
            extra.get("underlying") or order_row.get("symbol") or ""
        ),
        "division": division,
    }


def _build_leg(order_row: dict, extra: dict, asset_class: str) -> dict:
    side = (order_row.get("side") or "").lower()
    qty = float(order_row.get("qty") or 0)
    side_sign = +1 if side == "sell" else -1   # sell = credit, buy = debit

    leg: dict[str, Any] = {
        "side": side,
        "qty": qty,
        "qty_str": _qty_str(qty, decimals=8 if asset_class == "crypto" else 0),
        "asset_class": asset_class,
        "symbol": (
            extra.get("underlying") or order_row.get("symbol") or ""
        ),
        "action_label": _action_label(order_row, extra),
        "option": None,
        "mark": None,
        "bid": None,
        "ask": None,
        "gross_dollars": 0.0,
        "side_sign": side_sign,
        "rationale": order_row.get("rationale"),
    }

    if asset_class == "option":
        strike = _safe_float(extra.get("strike"))
        leg["option"] = {
            "strike": strike,
            "expiration": extra.get("expiration"),
            "option_type": (extra.get("option_type") or "call").upper(),
            "dte": extra.get("dte"),
            "delta": extra.get("delta"),
            "position_effect": extra.get("position_effect"),
            "action": extra.get("action"),
        }
        mark = _safe_float(
            extra.get("mark_per_share") or order_row.get("limit_price")
        )
        if mark is not None:
            leg["mark"] = mark
            # Options control 100 shares per contract.
            leg["gross_dollars"] = abs(mark * qty * 100)
        leg["bid"] = _safe_float(extra.get("bid"))
        leg["ask"] = _safe_float(extra.get("ask"))
    elif asset_class == "crypto":
        notional = _safe_float(extra.get("notional_target"))
        limit = _safe_float(order_row.get("limit_price"))
        if notional is not None:
            leg["gross_dollars"] = abs(notional)
        elif limit is not None:
            leg["mark"] = limit
            leg["gross_dollars"] = abs(limit * qty)
        leg["sizing_basis"] = extra.get("sizing_basis")
        leg["tier"] = extra.get("tier")
        leg["stop_price"] = _safe_float(extra.get("stop_price"))
        leg["stop_basis"] = extra.get("stop_basis")
        leg["max_dollar_risk"] = _safe_float(extra.get("max_dollar_risk"))
        leg["resized_for_max_loss"] = bool(extra.get("resized_for_max_loss"))
    else:  # stock
        limit = _safe_float(order_row.get("limit_price"))
        if limit is not None:
            leg["mark"] = limit
            leg["gross_dollars"] = abs(limit * qty)
        leg["order_type"] = order_row.get("order_type")

    return leg


def _build_context_block(extra: dict) -> dict | None:
    """Position context dict — same keys as the Telegram formatter
    consumes, returned in structured shape for the template. None when
    upstream didn't populate context (no LEAP / no roll history)."""
    raw = extra.get("position_context")
    if not raw:
        return None
    out: dict[str, Any] = {}
    leap = raw.get("leap")
    if leap:
        out["leap"] = {
            "underlying": leap.get("underlying"),
            "strike": _safe_float(leap.get("strike")),
            "expiration": leap.get("expiration"),
            "cost_basis": _safe_float(leap.get("cost_basis")),
            "mark": _safe_float(leap.get("mark")),
            "dte": leap.get("dte"),
            "days_held": leap.get("days_held"),
        }
        cost = out["leap"]["cost_basis"]
        mark = out["leap"]["mark"]
        if cost and mark and cost > 0:
            out["leap"]["pnl_pct"] = ((mark / cost) - 1) * 100
    out["unrealized_pnl_dollars"] = _safe_float(
        raw.get("unrealized_pnl_dollars"),
    )
    out["unrealized_pnl_pct"] = _safe_float(raw.get("unrealized_pnl_pct"))
    out["roll_count"] = raw.get("roll_count")
    out["prior_credit_total"] = _safe_float(raw.get("prior_credit_total"))
    return out


def _build_risk(verdict: dict) -> dict:
    decision = (verdict.get("verdict") or "").lower()
    icon_map = {
        "approve": "✓", "approved": "✓",
        "resize": "⚠", "resized": "⚠",
        "reject": "✗", "rejected": "✗",
    }
    return {
        "verdict": decision or "evaluated",
        "reason": verdict.get("reason") or "",
        "icon": icon_map.get(decision, "·"),
        "color": (
            "gain" if decision in ("approve", "approved") else
            "warn" if decision in ("resize", "resized") else
            "loss" if decision in ("reject", "rejected") else
            "muted"
        ),
    }


# ── Helpers (mirror approval_format.py shapes) ───────────────────────


def _decode_extra(order_row: dict) -> dict:
    """`order.to_db_row()` produces extra_json (string). Decode it.
    Defensive: missing or malformed JSON yields {}."""
    raw = order_row.get("extra_json")
    if raw is None:
        # Some test paths pass extra directly — accept that too.
        e = order_row.get("extra")
        return e if isinstance(e, dict) else {}
    if isinstance(raw, dict):
        return raw
    try:
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, ValueError):
        return {}


def _classify_asset(extra: dict) -> str:
    if extra.get("is_option"):
        return "option"
    if extra.get("asset_type") == "crypto":
        return "crypto"
    return "stock"


def _action_emoji(order_row: dict, extra: dict) -> str:
    action = (extra.get("action") or "").lower()
    if extra.get("is_close"):
        return "📤"
    if "roll" in action:
        return "🔄"
    if "open" in action:
        return "📥"
    if "close" in action:
        return "📤"
    side = (order_row.get("side") or "").lower()
    return "📥" if side == "buy" else "📤" if side == "sell" else "🎲"


def _action_label(order_row: dict, extra: dict) -> str:
    action = (extra.get("action") or "").lower()
    side = (order_row.get("side") or "").upper()
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
    if extra.get("is_close"):
        tier = (extra.get("tier") or "").upper()
        return f"CLOSE LONG ({tier})" if tier else "CLOSE LONG"
    if extra.get("is_option"):
        pe = (extra.get("position_effect") or "").lower()
        if side == "BUY"  and pe == "open":  return "BUY TO OPEN"
        if side == "BUY"  and pe == "close": return "BUY TO CLOSE"
        if side == "SELL" and pe == "open":  return "SELL TO OPEN"
        if side == "SELL" and pe == "close": return "SELL TO CLOSE"
    return side or "TRADE"


def _qty_str(qty: float, decimals: int = 0) -> str:
    if qty == int(qty) and decimals == 0:
        return f"{int(qty)}"
    if qty < 1 and decimals > 0:
        return f"{qty:.{decimals}f}".rstrip("0").rstrip(".")
    return f"{qty:g}"


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
