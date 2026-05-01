"""Macro expert — MacroCalendar + VIX + earnings-window backed.

Reads `config/macro_calendar.yaml` (FOMC, CPI, NFP). Pulls cached VIX
from `utils/market_data.py:get_vix()`. Pulls next earnings date via
`utils/market_data.py:get_next_earnings()` so the expert can flag
earnings within `earnings_buffer_days`.

Deterministic indicator first, optional LLM narration second — same
shape as the technical expert.

`earnings_buffer_days` is read from `context['earnings_buffer_days']`
when present (CandidateScope provides it); falls back to 7.

`on_data_fetch` callback: per Refinement 4 only fires on FAILURE.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from trading_corp.agents.research.schemas import EvidenceItem, ExpertReport
from trading_corp.data.macro_calendar import MacroCalendar
from trading_corp.utils.market_data import get_next_earnings, get_vix

log = logging.getLogger(__name__)


class MacroExpert:
    role = "macro"

    def __init__(self, calendar: MacroCalendar | None = None) -> None:
        self._calendar = calendar
        self._chat = None  # lazy

    @property
    def calendar(self) -> MacroCalendar:
        if self._calendar is None:
            self._calendar = MacroCalendar.load()
        return self._calendar

    async def analyze(
        self,
        *,
        engagement_id: str,
        symbol: str,
        context: dict | None = None,
        on_data_fetch: Callable[..., None] | None = None,
    ) -> tuple[ExpertReport, float]:
        """Return (report, llm_cost_dollars)."""
        ctx = context or {}
        earnings_buffer_days = int(ctx.get("earnings_buffer_days", 7))

        now = datetime.now(timezone.utc)

        # ---- Calendar lookup (next 14d high-impact) ----
        try:
            upcoming = self.calendar.upcoming(
                now, within_minutes=14 * 24 * 60, impact_levels=("high",),
            )
            cal_ok, cal_err = True, None
        except Exception as e:
            upcoming = []
            cal_ok, cal_err = False, str(e)
        if not cal_ok:
            _emit_fetch(on_data_fetch, "macro_calendar:14d_high", False, cal_err)

        # ---- VIX ----
        try:
            vix = get_vix()
            vix_ok, vix_err = (vix is not None), (None if vix is not None else "VIX unavailable")
        except Exception as e:
            vix, vix_ok, vix_err = None, False, str(e)
        if not vix_ok:
            _emit_fetch(on_data_fetch, "yfinance:^VIX", False, vix_err)

        # ---- Earnings ----
        try:
            next_earnings = get_next_earnings(symbol)
            earn_ok, earn_err = True, None
        except Exception as e:
            next_earnings, earn_ok, earn_err = None, False, str(e)
        if not earn_ok:
            _emit_fetch(on_data_fetch, f"yfinance:{symbol}:earnings_dates", False, earn_err)

        days_to_earnings: float | None = None
        if next_earnings is not None:
            days_to_earnings = (next_earnings - now).total_seconds() / 86400.0

        earnings_window_clear = (
            days_to_earnings is None or days_to_earnings >= earnings_buffer_days
        )

        # If literally everything failed, refuse rather than fabricate.
        if not (cal_ok or vix_ok or earn_ok):
            return (
                ExpertReport(
                    role="macro",
                    engagement_id=engagement_id,
                    symbol=symbol,
                    summary=f"[REFUSED] macro: all data sources failed ({cal_err}; {vix_err}; {earn_err})",
                    key_evidence=[],
                    confidence_score=0.0,
                    directional_lean=None,
                    data_sufficiency=False,
                    refusal_reason="macro_calendar + VIX + earnings all unavailable",
                ),
                0.0,
            )

        # ---- Build evidence ----
        evidence: list[EvidenceItem] = []
        if vix is not None:
            evidence.append(EvidenceItem(
                claim=f"VIX = {vix:.2f}",
                source="yfinance:^VIX",
                source_ts=now.isoformat(),
                confidence=0.95,
            ))
        for evt in upcoming[:3]:
            evidence.append(EvidenceItem(
                claim=f"upcoming high-impact event: {evt.name} @ {evt.ts.isoformat()}",
                source=f"macro_calendar:{evt.source or 'config'}",
                source_ts=evt.ts.isoformat(),
                confidence=0.95,
            ))
        if days_to_earnings is not None:
            evidence.append(EvidenceItem(
                claim=f"next earnings in {days_to_earnings:.1f} days "
                      f"({'clear' if earnings_window_clear else 'INSIDE buffer'})",
                source=f"yfinance:{symbol}:earnings_dates",
                source_ts=next_earnings.isoformat() if next_earnings else None,
                confidence=0.85,
            ))
        elif earn_ok:
            evidence.append(EvidenceItem(
                claim="no earnings date available from yfinance",
                source=f"yfinance:{symbol}:earnings_dates",
                confidence=0.5,
            ))

        # ---- Lean + confidence ----
        lean, confidence = _macro_lean(vix, upcoming, days_to_earnings, earnings_buffer_days)

        # Deterministic summary
        parts = [f"{symbol}: macro lean={lean}."]
        if vix is not None:
            parts.append(f"VIX {vix:.1f}.")
        if upcoming:
            parts.append(f"{len(upcoming)} high-impact event(s) in next 14d.")
        if days_to_earnings is not None:
            parts.append(
                f"Earnings in {days_to_earnings:.0f}d "
                f"({'clear' if earnings_window_clear else 'INSIDE buffer'})."
            )
        summary = " ".join(parts)

        # Optional narration (best-effort)
        narration_cost = 0.0
        narrated = await _narrate_macro_if_available(
            symbol, vix, upcoming, days_to_earnings, earnings_window_clear, lean,
        )
        if narrated is not None:
            text, narration_cost = narrated
            if text:
                summary = text

        return (
            ExpertReport(
                role="macro",
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


def _macro_lean(
    vix: float | None,
    upcoming: list,
    days_to_earnings: float | None,
    earnings_buffer_days: int,
) -> tuple[str, float]:
    """Macro lean is bearish if VIX>25 AND high-impact events within 7d AND
    earnings imminent. Bullish if VIX<15 AND no events AND earnings clear.
    Otherwise neutral."""
    score = 0
    components = 0

    if vix is not None:
        components += 1
        if vix < 15:
            score += 1
        elif vix > 25:
            score -= 1

    components += 1
    near_events = [e for e in upcoming if (e.ts - datetime.now(timezone.utc)).days <= 7]
    if not upcoming:
        score += 1
    elif near_events:
        score -= 1

    if days_to_earnings is not None:
        components += 1
        if days_to_earnings >= earnings_buffer_days:
            score += 1
        else:
            score -= 1

    if score >= 2:
        return "bullish", min(1.0, abs(score) / max(2, components))
    if score <= -2:
        return "bearish", min(1.0, abs(score) / max(2, components))
    return "neutral", min(1.0, abs(score) / max(2, components))


def _emit_fetch(cb, source: str, ok: bool, error: str | None) -> None:
    if cb is None:
        return
    try:
        cb(source=source, ok=ok, error=error)
    except Exception as e:
        log.warning("macro on_data_fetch callback raised: %s", e)


async def _narrate_macro_if_available(
    symbol: str,
    vix: float | None,
    upcoming: list,
    days_to_earnings: float | None,
    earnings_clear: bool,
    lean: str,
) -> tuple[str, float] | None:
    from trading_corp.agents.llm import is_llm_available
    if not is_llm_available():
        return None
    try:
        from trading_corp.agents.llm import build_chat_model
        from trading_corp.agents.research.cost import (
            cost_for_anthropic_usage, model_for_role,
        )
    except Exception:
        return None

    try:
        events_str = "; ".join(
            f"{e.name} @ {e.ts.isoformat()}" for e in upcoming[:5]
        ) or "none in next 14d"
        prompt = (
            f"You are a macro expert on a research desk. In 1-2 sentences, "
            f"narrate the macro picture for {symbol}. Stay neutral; do not give "
            f"a buy/sell recommendation. Lean classification: {lean}.\n\n"
            f"Inputs (already computed deterministically):\n"
            f"- VIX: {vix}\n"
            f"- High-impact events next 14d: {events_str}\n"
            f"- Days to next earnings: {days_to_earnings}\n"
            f"- Earnings window clear: {earnings_clear}\n"
        )
        chat = build_chat_model("research_expert", max_tokens=220)
        response = await chat.ainvoke(prompt)
        text = (response.content or "").strip() if hasattr(response, "content") else str(response)
        usage = getattr(response, "response_metadata", {}).get("usage", {}) or {}
        cost = cost_for_anthropic_usage(model_for_role("research_expert"), usage)
        return text, cost
    except Exception as e:
        log.debug("macro narration LLM call failed: %s", e)
        return None
