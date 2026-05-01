"""Sentiment expert — yfinance-light analyst-and-headline read.

**Disclosure:** this expert reads sell-side analyst snapshots and recent
headline metadata, not crowd sentiment (Reddit / X / chat). Its
`directional_lean` reflects what the sell-side is pricing in plus any
recent headline tilt — NOT what retail believes. The summary text
states this explicitly so consuming divisions can weight it
appropriately. If crowd-sentiment ever becomes the bottleneck, swap
this expert for a paid feed (NewsAPI, Polygon News) — the protocol
is unchanged.

Data sources:
  - `yfinance.Ticker.recommendations` — quarterly aggregated buy/hold/sell
    counts.
  - `yfinance.Ticker.news` — recent article headlines (provider, title,
    publish ts).
  - `yfinance.Ticker.info["targetMeanPrice"]` and `currentPrice` — analyst
    target vs current.

Crypto-spot guard: yfinance has spotty news coverage and no analyst
recommendations for crypto symbols. The expert returns a refusal report
for symbols containing `/` (the unified "BTC/USD" form). The registry
DOES include sentiment for crypto_spot, so the refusal route is
expected behavior, not a bug.

Lean math (deterministic):
  - Buy ratings dominant + target above price + recent headlines positive → bullish
  - Sell dominant + target below price + recent negative → bearish
  - Otherwise neutral

`on_data_fetch` callback: per Refinement 4 only fires on FAILURE.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Callable

from trading_corp.agents.research.schemas import EvidenceItem, ExpertReport

log = logging.getLogger(__name__)


# Headline-tone keyword bag — deliberately small + manual, NOT a
# transformer. The intent is "is the recent headline drumbeat tilted?"
# not "what is the precise sentiment vector?" If the keyword set turns
# out to over- or under-fire, retune it; if it turns out genuine
# sentiment is the bottleneck, swap to a paid feed.
_BULLISH_TERMS = (
    "beats", "beat", "surges", "soars", "rally", "rallies", "jumps",
    "upgrade", "upgrades", "raises guidance", "record", "milestone",
    "exceeds", "outperform", "buyback", "dividend hike", "wins contract",
)
_BEARISH_TERMS = (
    "misses", "miss", "plunges", "tumbles", "falls", "drops",
    "downgrade", "downgrades", "cuts guidance", "warns", "guidance cut",
    "investigation", "lawsuit", "fraud", "probe", "layoffs", "bankruptcy",
    "delays", "recall", "selloff",
)


class SentimentExpert:
    """Stateless. One instance per process, safe for concurrent calls."""

    role = "sentiment"

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
        # Crypto / non-equity: yfinance doesn't surface analyst ratings or
        # reliable news for these. Refuse so the synthesis prompt knows
        # the dimension is unobserved (rather than silently zero-credit).
        if "/" in symbol or " " in symbol:
            return _refuse(
                engagement_id, symbol,
                "yfinance sentiment unreliable for non-equity symbol; "
                "swap to a paid crypto-news feed when prioritized",
            ), 0.0

        recs, recs_ok, recs_err = _fetch_recommendations(symbol)
        if not recs_ok and on_data_fetch is not None:
            try:
                on_data_fetch(
                    source=f"yfinance:{symbol}:recommendations",
                    ok=False, error=recs_err,
                )
            except Exception as e:
                log.warning("on_data_fetch raised: %s", e)

        news, news_ok, news_err = _fetch_news(symbol)
        if not news_ok and on_data_fetch is not None:
            try:
                on_data_fetch(
                    source=f"yfinance:{symbol}:news",
                    ok=False, error=news_err,
                )
            except Exception as e:
                log.warning("on_data_fetch raised: %s", e)

        target_info, info_ok, info_err = _fetch_target_info(symbol)
        if not info_ok and on_data_fetch is not None:
            try:
                on_data_fetch(
                    source=f"yfinance:{symbol}:info",
                    ok=False, error=info_err,
                )
            except Exception as e:
                log.warning("on_data_fetch raised: %s", e)

        # If literally none of the three sub-sources resolved, refuse.
        if not (recs_ok or news_ok or info_ok):
            return _refuse(
                engagement_id, symbol,
                "all sentiment sub-sources failed (recommendations, news, info)",
            ), 0.0

        indicators = _compute_indicators(recs, news, target_info)
        evidence = _evidence_from_indicators(symbol, indicators)
        lean = _lean_from_indicators(indicators)
        confidence = _confidence_from_indicators(indicators)
        summary = _summary_from_indicators(symbol, indicators, lean)

        narration_cost = 0.0
        narrated = await _narrate_if_available(symbol, indicators, lean)
        if narrated is not None:
            narration, narration_cost = narrated
            if narration:
                summary = narration

        return (
            ExpertReport(
                role="sentiment",
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
        role="sentiment",
        engagement_id=engagement_id,
        symbol=symbol,
        summary=f"[REFUSED] sentiment: {reason}",
        key_evidence=[],
        confidence_score=0.0,
        directional_lean=None,
        data_sufficiency=False,
        refusal_reason=reason,
    )


# ──────────────────────────────────────────────────────────────────────────
# Sub-source fetchers
# ──────────────────────────────────────────────────────────────────────────


def _fetch_recommendations(symbol: str) -> tuple[dict | None, bool, str | None]:
    """Pull yfinance .recommendations DataFrame and aggregate into a
    summary dict. yfinance's exact shape varies by version; we tolerate
    DataFrame with 'strongBuy/buy/hold/sell/strongSell' columns OR an
    older shape with a single 'To Grade' column."""
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return None, False, "yfinance not installed"
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.recommendations
    except Exception as e:
        return None, False, f"recommendations fetch failed: {e}"
    if df is None:
        return None, False, "no recommendations returned"
    try:
        # Most recent yfinance returns a DataFrame with period-aggregated
        # counts. We sum the most recent two periods (≈ 6 months) for
        # signal stability vs single-snapshot noise.
        if hasattr(df, "empty") and df.empty:
            return None, False, "recommendations DataFrame empty"
        recent = df.head(2) if len(df) > 2 else df
        cols = {c.lower(): c for c in (recent.columns or [])}
        def _sum(name: str) -> int:
            col = cols.get(name)
            if col is None:
                return 0
            try:
                return int(recent[col].sum())
            except Exception:
                return 0
        strong_buy = _sum("strongbuy")
        buy = _sum("buy")
        hold = _sum("hold")
        sell = _sum("sell")
        strong_sell = _sum("strongsell")
        total = strong_buy + buy + hold + sell + strong_sell
        if total == 0:
            return None, False, "recommendations columns absent or empty"
        return {
            "strong_buy": strong_buy,
            "buy": buy,
            "hold": hold,
            "sell": sell,
            "strong_sell": strong_sell,
            "total": total,
        }, True, None
    except Exception as e:
        return None, False, f"recommendations parse failed: {e}"


def _fetch_news(symbol: str) -> tuple[list[dict] | None, bool, str | None]:
    """Pull recent yfinance .news entries. Each entry is normalized to
    `{title, publisher, ts}`. yfinance returns a list of dicts (newer
    shape) or sometimes wraps under a 'content' key — we handle both."""
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return None, False, "yfinance not installed"
    try:
        ticker = yf.Ticker(symbol)
        raw = ticker.news
    except Exception as e:
        return None, False, f"news fetch failed: {e}"
    if not raw or not isinstance(raw, list):
        return None, False, "no news returned"
    out: list[dict] = []
    for entry in raw[:20]:
        if not isinstance(entry, dict):
            continue
        # New yfinance shape wraps under 'content'.
        body = entry.get("content") if "content" in entry else entry
        if not isinstance(body, dict):
            continue
        title = body.get("title") or entry.get("title") or ""
        publisher = (
            body.get("publisher")
            or (body.get("provider") or {}).get("displayName")
            or entry.get("publisher")
            or ""
        )
        ts = (
            body.get("pubDate")
            or body.get("displayTime")
            or entry.get("providerPublishTime")
            or ""
        )
        out.append({"title": str(title), "publisher": str(publisher), "ts": str(ts)})
    if not out:
        return None, False, "news entries unparseable"
    return out, True, None


def _fetch_target_info(symbol: str) -> tuple[dict | None, bool, str | None]:
    """Pull current price + target mean price from .info."""
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return None, False, "yfinance not installed"
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
    except Exception as e:
        return None, False, f"info fetch failed: {e}"
    if not info or not isinstance(info, dict):
        return None, False, "no info returned"
    try:
        current = info.get("currentPrice") or info.get("regularMarketPrice")
        target = info.get("targetMeanPrice")
        n_analysts = info.get("numberOfAnalystOpinions")
        if current is None and target is None:
            return None, False, "no price/target fields in info"
        return {
            "current_price": float(current) if current is not None else None,
            "target_mean": float(target) if target is not None else None,
            "n_analysts": int(n_analysts) if n_analysts is not None else None,
        }, True, None
    except (TypeError, ValueError) as e:
        return None, False, f"info parse failed: {e}"


# ──────────────────────────────────────────────────────────────────────────
# Indicator math
# ──────────────────────────────────────────────────────────────────────────


def _compute_indicators(
    recs: dict | None, news: list[dict] | None, target_info: dict | None,
) -> dict:
    ind: dict = {
        "recs_total": (recs or {}).get("total"),
        "recs_buy_share": None,
        "recs_sell_share": None,
        "news_count": len(news) if news else 0,
        "news_bull_terms": 0,
        "news_bear_terms": 0,
        "target_premium_pct": None,
        "n_analysts": (target_info or {}).get("n_analysts"),
        "subsources_resolved": 0,
    }

    if recs and recs.get("total"):
        total = recs["total"]
        ind["recs_buy_share"] = (recs.get("strong_buy", 0) + recs.get("buy", 0)) / total
        ind["recs_sell_share"] = (recs.get("strong_sell", 0) + recs.get("sell", 0)) / total
        ind["subsources_resolved"] += 1

    if news:
        bull, bear = _count_tone_keywords(news)
        ind["news_bull_terms"] = bull
        ind["news_bear_terms"] = bear
        ind["subsources_resolved"] += 1

    if target_info:
        cp = target_info.get("current_price")
        tm = target_info.get("target_mean")
        if cp and tm and cp > 0:
            ind["target_premium_pct"] = (tm / cp) - 1.0
            ind["subsources_resolved"] += 1

    return ind


def _count_tone_keywords(news: list[dict]) -> tuple[int, int]:
    """Count headlines with ≥1 bullish/bearish term. Word-boundary
    matching to avoid false positives like 'misstep' for 'miss'."""
    bull = 0
    bear = 0
    for entry in news:
        title = (entry.get("title") or "").lower()
        if not title:
            continue
        if any(re.search(rf"\b{re.escape(t)}\b", title) for t in _BULLISH_TERMS):
            bull += 1
        if any(re.search(rf"\b{re.escape(t)}\b", title) for t in _BEARISH_TERMS):
            bear += 1
    return bull, bear


def _evidence_from_indicators(symbol: str, ind: dict) -> list[EvidenceItem]:
    src_recs = f"yfinance:{symbol}:recommendations"
    src_news = f"yfinance:{symbol}:news"
    src_info = f"yfinance:{symbol}:info"
    items: list[EvidenceItem] = []

    if ind.get("recs_buy_share") is not None:
        bs = ind["recs_buy_share"]
        ss = ind["recs_sell_share"] or 0.0
        items.append(EvidenceItem(
            claim=(
                f"analyst ratings: {bs:.0%} buy / {ss:.0%} sell "
                f"({ind.get('recs_total')} total)"
            ),
            source=src_recs, confidence=0.75,
        ))
    if ind.get("target_premium_pct") is not None:
        items.append(EvidenceItem(
            claim=(
                f"analyst target {ind['target_premium_pct']:+.1%} vs current price"
                + (f" ({ind['n_analysts']} analysts)" if ind.get("n_analysts") else "")
            ),
            source=src_info, confidence=0.7,
        ))
    if (ind.get("news_count") or 0) > 0:
        items.append(EvidenceItem(
            claim=(
                f"recent headlines: {ind['news_count']} entries, "
                f"{ind['news_bull_terms']} bullish-toned / "
                f"{ind['news_bear_terms']} bearish-toned"
            ),
            source=src_news, confidence=0.55,
        ))
    return items


def _lean_from_indicators(ind: dict) -> str:
    score = 0
    bs = ind.get("recs_buy_share")
    ss = ind.get("recs_sell_share")
    if bs is not None and ss is not None:
        if bs > 0.55 and ss < 0.15:
            score += 1
        elif ss > 0.30 or (bs is not None and bs < 0.30):
            score -= 1

    tp = ind.get("target_premium_pct")
    if tp is not None:
        if tp > 0.10:
            score += 1
        elif tp < -0.05:
            score -= 1

    bull = ind.get("news_bull_terms", 0) or 0
    bear = ind.get("news_bear_terms", 0) or 0
    if bull >= 2 and bull > bear * 2:
        score += 1
    elif bear >= 2 and bear > bull * 2:
        score -= 1

    if score >= 2:
        return "bullish"
    if score <= -2:
        return "bearish"
    return "neutral"


def _confidence_from_indicators(ind: dict) -> float:
    n = int(ind.get("subsources_resolved") or 0)
    if n == 0:
        return 0.0
    base = n / 3.0    # 3 sub-sources = full
    # Penalize if both rec-share and news-tone are missing (the two
    # strongest signals). target_premium alone is weak.
    if ind.get("recs_buy_share") is None and (ind.get("news_count") or 0) == 0:
        base = min(base, 0.3)
    return round(min(1.0, base), 4)


def _summary_from_indicators(symbol: str, ind: dict, lean: str) -> str:
    parts = [
        f"{symbol}: sentiment lean={lean} "
        f"(analyst+headline view; not crowd sentiment)."
    ]
    if ind.get("recs_buy_share") is not None:
        parts.append(
            f"Analyst buy/sell shares: {ind['recs_buy_share']:.0%}/"
            f"{(ind.get('recs_sell_share') or 0):.0%}."
        )
    if ind.get("target_premium_pct") is not None:
        parts.append(f"Mean target {ind['target_premium_pct']:+.1%} vs price.")
    if (ind.get("news_count") or 0) > 0:
        parts.append(
            f"Recent news tone: {ind['news_bull_terms']}+/"
            f"{ind['news_bear_terms']}- of {ind['news_count']}."
        )
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
        log.debug("sentiment narration unavailable: %s", e)
        return None

    try:
        prompt = (
            f"You are a sentiment expert on a research desk. Summarize the "
            f"sell-side analyst + recent-headline picture for {symbol} in "
            f"1-2 sentences. Stay neutral; do not give a buy/sell "
            f"recommendation. State explicitly that this is analyst-driven "
            f"sentiment, not crowd. Lean classification: {lean}.\n\n"
            f"Indicators (deterministic — narrate, do not recompute):\n"
            f"- analyst buy share: {ind.get('recs_buy_share')}\n"
            f"- analyst sell share: {ind.get('recs_sell_share')}\n"
            f"- analyst total: {ind.get('recs_total')}\n"
            f"- target premium vs price: {ind.get('target_premium_pct')}\n"
            f"- # analysts: {ind.get('n_analysts')}\n"
            f"- recent news count: {ind.get('news_count')}\n"
            f"- bullish-toned headlines: {ind.get('news_bull_terms')}\n"
            f"- bearish-toned headlines: {ind.get('news_bear_terms')}\n"
        )
        chat = build_chat_model("research_expert", max_tokens=220)
        response = await chat.ainvoke(prompt)
        text = (response.content or "").strip() if hasattr(response, "content") else str(response)
        usage = getattr(response, "response_metadata", {}).get("usage", {}) or {}
        cost = cost_for_anthropic_usage(model_for_role("research_expert"), usage)
        return text, cost
    except Exception as e:
        log.debug("sentiment narration LLM call failed: %s", e)
        return None
