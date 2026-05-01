"""Fundamental expert — yfinance-backed structural-quality read.

Pulls a small fixed set of fundamental metrics deterministically; the
LLM only narrates. Matches CLAUDE.md §1's deterministic-then-narrate
principle.

Data source: yfinance via `Ticker.info` (multi-field snapshot dict). If
yfinance is unavailable or the snapshot is malformed (yfinance shifts
field names between releases), the expert returns a refusal report
rather than fabricating values.

Scope: equity/option-asset-class only. The expert registry does not
include `fundamental` for `crypto_spot` (no balance sheet to read on a
spot crypto symbol), so crypto engagements never invoke this expert.

Lean math (deterministic):
  - Profitable + growing + reasonable leverage → bullish
  - Unprofitable / contracting / over-leveraged → bearish
  - Mixed / insufficient signal → neutral

Confidence is a function of how many indicators resolved against how
many we tried — high confidence requires a relatively complete
snapshot.

`on_data_fetch` callback: per Refinement 4 fires on FAILURE only.
"""
from __future__ import annotations

import logging
from typing import Callable

from trading_corp.agents.research.schemas import EvidenceItem, ExpertReport

log = logging.getLogger(__name__)


class FundamentalExpert:
    """Stateless expert. One instance per process; safe for concurrent calls."""

    role = "fundamental"

    def __init__(self) -> None:
        self._chat = None  # lazy

    async def analyze(
        self,
        *,
        engagement_id: str,
        symbol: str,
        context: dict | None = None,
        on_data_fetch: Callable[..., None] | None = None,
    ) -> tuple[ExpertReport, float]:
        # Crypto / non-equity symbols (e.g. "BTC/USD") have no fundamentals;
        # refuse rather than fetch. The registry already excludes fundamental
        # for crypto_spot, but a defensive guard here avoids wasted calls if
        # a future caller mis-routes.
        if "/" in symbol or " " in symbol:
            return _refuse(
                engagement_id, symbol,
                "fundamentals not applicable to non-equity symbol",
            ), 0.0

        snapshot, fetch_ok, fetch_err = _fetch_snapshot(symbol)

        if not fetch_ok and on_data_fetch is not None:
            try:
                on_data_fetch(
                    source=f"yfinance:{symbol}:info",
                    ok=False,
                    error=fetch_err,
                )
            except Exception as e:
                log.warning("on_data_fetch callback raised: %s", e)

        if not fetch_ok or snapshot is None:
            return _refuse(
                engagement_id, symbol,
                fetch_err or "yfinance returned no fundamentals",
            ), 0.0

        # Compute deterministic indicators from the raw snapshot.
        indicators = _compute_indicators(snapshot)
        if not indicators.get("any_resolved"):
            return _refuse(
                engagement_id, symbol,
                "no usable fundamental fields in yfinance snapshot",
            ), 0.0

        evidence = _evidence_from_indicators(symbol, indicators)
        lean = _lean_from_indicators(indicators)
        confidence = _confidence_from_indicators(indicators)
        summary = _summary_from_indicators(symbol, indicators, lean)

        # Optional LLM narration.
        narration_cost = 0.0
        narrated = await _narrate_if_available(symbol, indicators, lean)
        if narrated is not None:
            narration, narration_cost = narrated
            if narration:
                summary = narration

        return (
            ExpertReport(
                role="fundamental",
                engagement_id=engagement_id,
                symbol=symbol,
                summary=summary,
                key_evidence=evidence,
                confidence_score=confidence,
                directional_lean=lean,
                data_sufficiency=True,
                refusal_reason=None,
            ),
            narration_cost,
        )


def _refuse(engagement_id: str, symbol: str, reason: str) -> ExpertReport:
    return ExpertReport(
        role="fundamental",
        engagement_id=engagement_id,
        symbol=symbol,
        summary=f"[REFUSED] fundamental: {reason}",
        key_evidence=[],
        confidence_score=0.0,
        directional_lean=None,
        data_sufficiency=False,
        refusal_reason=reason,
    )


# ──────────────────────────────────────────────────────────────────────────
# Data fetch
# ──────────────────────────────────────────────────────────────────────────


def _fetch_snapshot(symbol: str) -> tuple[dict | None, bool, str | None]:
    """Pull the yfinance `.info` snapshot. Field names shift between yf
    releases; we tolerate missing keys but require the call itself to
    succeed and return a non-empty dict.
    """
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return None, False, "yfinance not installed"

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
    except Exception as e:
        return None, False, f"yfinance fetch failed: {e}"

    if not info or not isinstance(info, dict):
        return None, False, f"yfinance returned no info dict for {symbol}"
    # yfinance occasionally returns a nearly-empty dict for delisted/unknown
    # symbols ({'symbol': X, 'logo_url': ''} only). Guard against that.
    if len(info) < 5:
        return None, False, f"yfinance info too sparse for {symbol} ({len(info)} keys)"
    return info, True, None


# ──────────────────────────────────────────────────────────────────────────
# Deterministic indicator math
# ──────────────────────────────────────────────────────────────────────────


def _f(snap: dict, *keys: str) -> float | None:
    """Return the first numeric value from snap[keys]; None if all missing
    or non-numeric. yfinance shifts field names across versions; we try
    the common variants in order."""
    for k in keys:
        v = snap.get(k)
        if v is None:
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        # yfinance sometimes returns 0 or NaN as sentinels for "missing."
        if x != x:    # NaN
            continue
        return x
    return None


def _compute_indicators(snap: dict) -> dict:
    """Pull a small fixed set of fundamental metrics from `.info`.

    All fields optional; absence is recorded so the lean math knows
    what's known vs unknown.
    """
    pe = _f(snap, "trailingPE", "forwardPE")
    pb = _f(snap, "priceToBook")
    de = _f(snap, "debtToEquity")    # yfinance reports this as a percentage (e.g., 120 = 1.2x)
    rev_growth = _f(snap, "revenueGrowth")           # yoy fraction (0.12 = 12%)
    earn_growth = _f(snap, "earningsGrowth")
    profit_margin = _f(snap, "profitMargins")
    gross_margin = _f(snap, "grossMargins")
    fcf = _f(snap, "freeCashflow", "operatingCashflow")
    market_cap = _f(snap, "marketCap")

    fields_present = sum(
        1 for v in (pe, pb, de, rev_growth, earn_growth, profit_margin,
                    gross_margin, fcf, market_cap)
        if v is not None
    )

    return {
        "pe": pe,
        "pb": pb,
        "debt_to_equity": de,
        "revenue_growth": rev_growth,
        "earnings_growth": earn_growth,
        "profit_margin": profit_margin,
        "gross_margin": gross_margin,
        "free_cashflow": fcf,
        "market_cap": market_cap,
        "fields_present": fields_present,
        # Need at least 3 fields populated for any useful read; below
        # that, refuse upstream.
        "any_resolved": fields_present >= 3,
    }


def _evidence_from_indicators(symbol: str, ind: dict) -> list[EvidenceItem]:
    src = f"yfinance:{symbol}:info"
    items: list[EvidenceItem] = []
    if ind.get("pe") is not None:
        items.append(EvidenceItem(
            claim=f"P/E {ind['pe']:.1f}", source=src, confidence=0.85,
        ))
    if ind.get("pb") is not None:
        items.append(EvidenceItem(
            claim=f"P/B {ind['pb']:.2f}", source=src, confidence=0.8,
        ))
    if ind.get("debt_to_equity") is not None:
        # yfinance reports D/E as a percentage; render as ratio for readability.
        de_ratio = ind["debt_to_equity"] / 100.0
        items.append(EvidenceItem(
            claim=f"D/E {de_ratio:.2f}x",
            source=src, confidence=0.8,
        ))
    if ind.get("revenue_growth") is not None:
        items.append(EvidenceItem(
            claim=f"revenue growth {ind['revenue_growth']:+.1%} yoy",
            source=src, confidence=0.85,
        ))
    if ind.get("profit_margin") is not None:
        items.append(EvidenceItem(
            claim=f"profit margin {ind['profit_margin']:+.1%}",
            source=src, confidence=0.85,
        ))
    if ind.get("free_cashflow") is not None:
        # FCF in dollars; render with B/M suffix for legibility.
        fcf = ind["free_cashflow"]
        if abs(fcf) >= 1e9:
            disp = f"${fcf / 1e9:+.2f}B"
        elif abs(fcf) >= 1e6:
            disp = f"${fcf / 1e6:+.1f}M"
        else:
            disp = f"${fcf:+.0f}"
        items.append(EvidenceItem(
            claim=f"free cash flow {disp}",
            source=src, confidence=0.8,
        ))
    return items


def _lean_from_indicators(ind: dict) -> str:
    """Deterministic lean: profitable + growing + reasonable leverage =
    bullish; opposite = bearish. Mixed signals = neutral."""
    score = 0
    # Profitability
    pm = ind.get("profit_margin")
    if pm is not None:
        if pm > 0.10:
            score += 1
        elif pm < 0:
            score -= 1
    # Growth
    rg = ind.get("revenue_growth")
    if rg is not None:
        if rg > 0.10:
            score += 1
        elif rg < -0.05:
            score -= 1
    eg = ind.get("earnings_growth")
    if eg is not None:
        if eg > 0.10:
            score += 1
        elif eg < -0.10:
            score -= 1
    # Leverage — yfinance D/E is in percent (120 = 1.2x). Anything
    # > 200 (i.e. 2x equity) is leverage-as-risk for our purposes.
    de = ind.get("debt_to_equity")
    if de is not None:
        if de > 200:
            score -= 1
        elif de < 50:    # very low leverage
            score += 1
    # FCF positive is mildly bullish (positive carry, optionality)
    fcf = ind.get("free_cashflow")
    if fcf is not None:
        if fcf > 0:
            score += 1
        elif fcf < 0:
            score -= 1

    if score >= 2:
        return "bullish"
    if score <= -2:
        return "bearish"
    return "neutral"


def _confidence_from_indicators(ind: dict) -> float:
    """Confidence scales with completeness of the snapshot (more fields =
    more support for the lean) and the strength of the lean signal."""
    n = int(ind.get("fields_present") or 0)
    if n < 3:
        return 0.0
    base = min(1.0, n / 8.0)         # full snapshot ≈ 8/8 → 1.0
    # Modest bump if profit_margin AND revenue_growth both resolved —
    # these are the two strongest individual signals.
    if ind.get("profit_margin") is not None and ind.get("revenue_growth") is not None:
        base = min(1.0, base + 0.1)
    return round(base, 4)


def _summary_from_indicators(symbol: str, ind: dict, lean: str) -> str:
    parts = [f"{symbol}: fundamental lean={lean}."]
    if ind.get("pe") is not None:
        parts.append(f"P/E {ind['pe']:.1f}.")
    if ind.get("revenue_growth") is not None:
        parts.append(f"Revenue {ind['revenue_growth']:+.1%} yoy.")
    if ind.get("profit_margin") is not None:
        parts.append(f"Profit margin {ind['profit_margin']:+.1%}.")
    if ind.get("debt_to_equity") is not None:
        parts.append(f"D/E {ind['debt_to_equity']/100.0:.2f}x.")
    return " ".join(parts)


# ──────────────────────────────────────────────────────────────────────────
# Optional LLM narration
# ──────────────────────────────────────────────────────────────────────────


async def _narrate_if_available(
    symbol: str, ind: dict, lean: str,
) -> tuple[str, float] | None:
    from trading_corp.agents.llm import is_llm_available
    if not is_llm_available():
        return None
    try:
        from trading_corp.agents.llm import build_chat_model
        from trading_corp.agents.research.cost import (
            cost_for_anthropic_usage, model_for_role,
        )
    except Exception as e:
        log.debug("fundamental narration unavailable: %s", e)
        return None

    try:
        prompt = (
            f"You are a fundamental expert on a research desk. Summarize "
            f"the structural-quality picture for {symbol} in 1-2 sentences. "
            f"Stay neutral; do not give a buy/sell recommendation. Lean "
            f"classification: {lean}.\n\n"
            f"Indicators (deterministic — narrate, do not recompute):\n"
            f"- P/E: {ind.get('pe')}\n"
            f"- P/B: {ind.get('pb')}\n"
            f"- D/E (percent): {ind.get('debt_to_equity')}\n"
            f"- revenue growth yoy: {ind.get('revenue_growth')}\n"
            f"- earnings growth yoy: {ind.get('earnings_growth')}\n"
            f"- profit margin: {ind.get('profit_margin')}\n"
            f"- gross margin: {ind.get('gross_margin')}\n"
            f"- free cash flow ($): {ind.get('free_cashflow')}\n"
        )
        chat = build_chat_model("research_expert", max_tokens=220)
        response = await chat.ainvoke(prompt)
        text = (response.content or "").strip() if hasattr(response, "content") else str(response)
        usage = getattr(response, "response_metadata", {}).get("usage", {}) or {}
        cost = cost_for_anthropic_usage(model_for_role("research_expert"), usage)
        return text, cost
    except Exception as e:
        log.debug("fundamental narration LLM call failed: %s", e)
        return None
