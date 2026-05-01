"""Technical expert — yfinance-backed indicator computation + LLM narration.

Computes a small fixed indicator set (price, 50/200d MA cross, 14d RSI,
20d ATR) deterministically; the LLM only narrates the resulting evidence.
This matches CLAUDE.md §1's deterministic-then-narrate principle.

Data source: yfinance via `Ticker.history(period="1y")`. If yfinance is
unavailable or returns insufficient data, the expert returns an
`ExpertReport` with `data_sufficiency=False` and a specific refusal
reason rather than fabricating values.

`on_data_fetch` callback: per Refinement 4, the engagement runner only
emits `research_data_fetch_attempted` audit rows on FAILURE. Successful
fetches are silent — the resulting ExpertReport with
data_sufficiency=True is itself the evidence of retrieval.
"""
from __future__ import annotations

import logging
from typing import Callable

from trading_corp.agents.research.schemas import (
    EvidenceItem,
    ExpertReport,
)

log = logging.getLogger(__name__)


class TechnicalExpert:
    """Stateless expert — one instance can serve any engagement.

    Construction is cheap; the LLM client is built lazily inside
    `analyze()` so test envs without ANTHROPIC_API_KEY can still
    construct + call the deterministic indicator path (LLM narration
    is optional and degrades to a templated summary).
    """

    role = "technical"

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
        """Return (report, llm_cost_dollars).

        `on_data_fetch` is called only on FAILURE (Refinement 4) so the
        engagement runner can write `research_data_fetch_attempted`.
        Pass None to skip.
        """
        indicators, fetch_ok, fetch_err = _compute_indicators(symbol)

        if not fetch_ok and on_data_fetch is not None:
            try:
                on_data_fetch(
                    source=f"yfinance:{symbol}:1y",
                    ok=False,
                    error=fetch_err,
                )
            except Exception as e:
                log.warning("on_data_fetch callback raised: %s", e)

        if not fetch_ok or indicators is None:
            return (
                ExpertReport(
                    role="technical",
                    engagement_id=engagement_id,
                    symbol=symbol,
                    summary=f"[REFUSED] technical: {fetch_err or 'no data'}",
                    key_evidence=[],
                    confidence_score=0.0,
                    directional_lean=None,
                    data_sufficiency=False,
                    refusal_reason=fetch_err or "yfinance returned no data",
                ),
                0.0,
            )

        # Build deterministic evidence + lean from indicators.
        evidence = _evidence_from_indicators(symbol, indicators)
        lean = _lean_from_indicators(indicators)
        confidence = _confidence_from_indicators(indicators)
        summary = _summary_from_indicators(symbol, indicators, lean)

        # Optional LLM narration to enrich `summary`. Best-effort — failures
        # leave the deterministic summary in place.
        narration_cost = 0.0
        narrated = await _narrate_if_available(symbol, indicators, lean)
        if narrated is not None:
            narration, narration_cost = narrated
            if narration:
                summary = narration

        return (
            ExpertReport(
                role="technical",
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


# ──────────────────────────────────────────────────────────────────────────
# Deterministic indicator math
# ──────────────────────────────────────────────────────────────────────────


def _compute_indicators(symbol: str) -> tuple[dict | None, bool, str | None]:
    """Pull 1y daily OHLC from yfinance and compute a small indicator set.

    Returns (indicators_dict, ok, error_str_or_none). On any failure the
    caller writes a refusal report; we never make up values.
    """
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return None, False, "yfinance not installed"

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1y")
    except Exception as e:
        return None, False, f"yfinance fetch failed: {e}"

    if hist is None or hist.empty or "Close" not in hist.columns:
        return None, False, f"yfinance returned no history for {symbol}"

    closes = hist["Close"].dropna()
    if len(closes) < 50:
        return None, False, f"insufficient history ({len(closes)} bars; need 50+)"

    last = float(closes.iloc[-1])
    ma50 = float(closes.tail(50).mean())
    ma200 = float(closes.tail(200).mean()) if len(closes) >= 200 else None

    # RSI(14)
    delta = closes.diff().dropna()
    up = delta.clip(lower=0).tail(14).mean()
    down = (-delta.clip(upper=0)).tail(14).mean()
    rsi = 100.0 - (100.0 / (1.0 + (up / down))) if down else 100.0
    rsi = float(rsi)

    # ATR(20) approximation: rolling stdev of close-to-close (good enough
    # for narration; we don't need an OHLC-true Wilder ATR here).
    atr20 = float(closes.tail(20).std() or 0.0)
    atr20_pct = atr20 / last if last else 0.0

    if ma200 is None:
        cross_state = "insufficient_history_for_200ma"
    elif ma50 > ma200:
        cross_state = "golden_cross"
    elif ma50 < ma200:
        cross_state = "death_cross"
    else:
        cross_state = "neutral"

    def _ret(n: int) -> float | None:
        if len(closes) <= n:
            return None
        return float(closes.iloc[-1] / closes.iloc[-n] - 1.0)

    ind = {
        "last_price": last,
        "ma50": ma50,
        "ma200": ma200,
        "rsi14": rsi,
        "atr20_pct": atr20_pct,
        "ma_cross_state": cross_state,
        "return_1m": _ret(21),
        "return_3m": _ret(63),
        "return_6m": _ret(126),
        "vs_ma50_pct": (last / ma50 - 1.0) if ma50 else None,
        "vs_ma200_pct": (last / ma200 - 1.0) if ma200 else None,
    }
    return ind, True, None


def _evidence_from_indicators(symbol: str, ind: dict) -> list[EvidenceItem]:
    src = f"yfinance:{symbol}:1y"
    items: list[EvidenceItem] = []

    items.append(EvidenceItem(
        claim=f"price ${ind['last_price']:.2f}, "
              f"{ind['vs_ma50_pct']:+.1%} vs 50d MA"
              if ind.get("vs_ma50_pct") is not None
              else f"price ${ind['last_price']:.2f}",
        source=src,
        confidence=0.9,
    ))
    if ind.get("ma_cross_state") and ind["ma_cross_state"] != "insufficient_history_for_200ma":
        items.append(EvidenceItem(
            claim=f"50/200d MA: {ind['ma_cross_state']}",
            source=src,
            confidence=0.85,
        ))
    items.append(EvidenceItem(
        claim=f"RSI(14) = {ind['rsi14']:.1f}",
        source=src,
        confidence=0.85,
    ))
    if ind.get("atr20_pct") is not None:
        items.append(EvidenceItem(
            claim=f"ATR(20) ≈ {ind['atr20_pct']*100:.2f}% of price (volatility proxy)",
            source=src,
            confidence=0.7,
        ))
    if ind.get("return_3m") is not None:
        items.append(EvidenceItem(
            claim=f"3-month return: {ind['return_3m']:+.1%}",
            source=src,
            confidence=0.95,
        ))
    return items


def _lean_from_indicators(ind: dict) -> str:
    score = 0
    if ind.get("ma_cross_state") == "golden_cross":
        score += 1
    elif ind.get("ma_cross_state") == "death_cross":
        score -= 1

    rsi = ind.get("rsi14")
    if rsi is not None:
        if rsi > 60:
            score += 1
        elif rsi < 40:
            score -= 1

    r3 = ind.get("return_3m")
    if r3 is not None:
        if r3 > 0.05:
            score += 1
        elif r3 < -0.05:
            score -= 1

    if score >= 2:
        return "bullish"
    if score <= -2:
        return "bearish"
    return "neutral"


def _confidence_from_indicators(ind: dict) -> float:
    score_abs = 0
    components = 0
    if ind.get("ma_cross_state") in ("golden_cross", "death_cross"):
        score_abs += 1
        components += 1
    if ind.get("rsi14") is not None:
        components += 1
        rsi = ind["rsi14"]
        if rsi > 60 or rsi < 40:
            score_abs += 1
    if ind.get("return_3m") is not None:
        components += 1
        if abs(ind["return_3m"]) > 0.05:
            score_abs += 1
    if components == 0:
        return 0.0
    return min(1.0, score_abs / max(2, components))


def _summary_from_indicators(symbol: str, ind: dict, lean: str) -> str:
    parts = [f"{symbol}: technical lean={lean}."]
    if ind.get("ma_cross_state") and ind["ma_cross_state"] != "insufficient_history_for_200ma":
        parts.append(f"50/200d MA = {ind['ma_cross_state'].replace('_', ' ')}.")
    parts.append(f"RSI(14) {ind['rsi14']:.1f}.")
    if ind.get("return_3m") is not None:
        parts.append(f"3-month return {ind['return_3m']:+.1%}.")
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
        log.debug("llm narration unavailable: %s", e)
        return None

    try:
        prompt = (
            f"You are a technical expert on a research desk. Summarize the "
            f"price action picture for {symbol} in 1-2 sentences. Stay neutral; "
            f"do not give a buy/sell recommendation. Lean classification: "
            f"{lean}.\n\nIndicators (already computed deterministically — "
            f"narrate, do not recompute):\n"
            f"- last price: ${ind['last_price']:.2f}\n"
            f"- 50d MA: {ind['ma50']:.2f}\n"
            f"- 200d MA: {ind.get('ma200')}\n"
            f"- 50/200d state: {ind.get('ma_cross_state')}\n"
            f"- RSI(14): {ind['rsi14']:.1f}\n"
            f"- ATR(20): {ind.get('atr20_pct', 0)*100:.2f}% of price\n"
            f"- 3m return: {ind.get('return_3m')}\n"
        )
        chat = build_chat_model("research_expert", max_tokens=220)
        response = await chat.ainvoke(prompt)
        text = (response.content or "").strip() if hasattr(response, "content") else str(response)
        usage = getattr(response, "response_metadata", {}).get("usage", {}) or {}
        cost = cost_for_anthropic_usage(model_for_role("research_expert"), usage)
        return text, cost
    except Exception as e:
        log.debug("technical narration LLM call failed: %s", e)
        return None
