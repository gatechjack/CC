"""SentimentExpert unit tests (Phase 1c).

Mocks `yfinance.Ticker.recommendations`, `.news`, and `.info`. Pins:
  1. Strong-buy + above-target + bullish headlines → bullish lean.
  2. Sell-dominant + below-target + bearish headlines → bearish.
  3. Crypto symbol (`BTC/USD`) refuses without fetching.
  4. All sub-sources fail → refusal.
  5. Headline tone keyword matching uses word boundaries (no false-pos).
  6. The "analyst-driven, not crowd" disclosure appears in the summary.
"""
from __future__ import annotations

import sys
import types

import pytest

from trading_corp.agents.research.experts.sentiment import SentimentExpert


def _install_fake_yf(*, recs, news, info, fail_recs=False, fail_news=False, fail_info=False):
    yf = types.ModuleType("yfinance")

    # Build a tiny DataFrame-like object that supports `.head(n)`, len(),
    # column access, and `.empty`. We avoid a real pandas dependency in
    # the test by hand-rolling exactly what _fetch_recommendations needs.
    class _Frame:
        def __init__(self, rows):
            self.rows = rows
            cols = set()
            for r in rows:
                cols.update(r.keys())
            self.columns = list(cols)
            self.empty = len(rows) == 0
        def head(self, n):
            return _Frame(self.rows[:n])
        def __len__(self):
            return len(self.rows)
        def __getitem__(self, col):
            return _Series([r.get(col, 0) for r in self.rows])
    class _Series:
        def __init__(self, vals):
            self.vals = vals
        def sum(self):
            return sum(self.vals)

    class _FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol
        @property
        def recommendations(self):
            if fail_recs:
                raise RuntimeError("recs outage")
            if recs is None:
                return None
            return _Frame(recs)
        @property
        def news(self):
            if fail_news:
                raise RuntimeError("news outage")
            return news
        @property
        def info(self):
            if fail_info:
                raise RuntimeError("info outage")
            return info

    yf.Ticker = _FakeTicker
    sys.modules["yfinance"] = yf
    return yf


@pytest.fixture(autouse=True)
def _clean_yf():
    yield
    sys.modules.pop("yfinance", None)


# ── Happy paths ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bullish_when_buy_dominant_target_above_news_positive():
    _install_fake_yf(
        recs=[
            {"strongBuy": 8, "buy": 10, "hold": 3, "sell": 1, "strongSell": 0},
            {"strongBuy": 6, "buy": 9, "hold": 4, "sell": 1, "strongSell": 0},
        ],
        news=[
            {"title": "Acme beats earnings, surges on guidance raise", "publisher": "X"},
            {"title": "Acme rallies after analyst upgrade", "publisher": "Y"},
            {"title": "Acme wins contract with major partner", "publisher": "Z"},
        ],
        info={
            "currentPrice": 100.0,
            "targetMeanPrice": 130.0,
            "numberOfAnalystOpinions": 25,
        },
    )
    expert = SentimentExpert()
    report, _ = await expert.analyze(
        engagement_id="e1", symbol="ACME", context={},
    )
    assert report.data_sufficiency
    assert report.directional_lean == "bullish"
    assert report.confidence_score > 0.5
    # Disclosure must appear so consumers know the source quality.
    assert "analyst" in report.summary.lower()
    assert "not crowd" in report.summary.lower()


@pytest.mark.asyncio
async def test_bearish_when_sell_share_high_target_below_news_negative():
    _install_fake_yf(
        recs=[
            {"strongBuy": 1, "buy": 2, "hold": 3, "sell": 6, "strongSell": 5},
            {"strongBuy": 0, "buy": 1, "hold": 4, "sell": 7, "strongSell": 4},
        ],
        news=[
            {"title": "Acme misses earnings, plunges on guidance cut"},
            {"title": "Acme downgrade follows lawsuit"},
            {"title": "Acme tumbles amid investigation"},
        ],
        info={
            "currentPrice": 100.0,
            "targetMeanPrice": 75.0,
            "numberOfAnalystOpinions": 22,
        },
    )
    expert = SentimentExpert()
    report, _ = await expert.analyze(
        engagement_id="e1", symbol="ACME", context={},
    )
    assert report.data_sufficiency
    assert report.directional_lean == "bearish"


# ── Refusal paths ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crypto_symbol_refuses_without_fetching():
    fetch_calls: list[str] = []
    yf = types.ModuleType("yfinance")

    class _FakeTicker:
        def __init__(self, symbol):
            fetch_calls.append(symbol)
        @property
        def recommendations(self):
            raise AssertionError("must not fetch on crypto symbol")
        @property
        def news(self):
            raise AssertionError("must not fetch on crypto symbol")
        @property
        def info(self):
            raise AssertionError("must not fetch on crypto symbol")
    yf.Ticker = _FakeTicker
    sys.modules["yfinance"] = yf

    expert = SentimentExpert()
    report, _ = await expert.analyze(
        engagement_id="e1", symbol="BTC/USD", context={},
    )
    assert not report.data_sufficiency
    assert "non-equity" in report.refusal_reason
    assert fetch_calls == []


@pytest.mark.asyncio
async def test_refuses_when_all_subsources_fail():
    _install_fake_yf(
        recs=None, news=None, info=None,
        fail_recs=True, fail_news=True, fail_info=True,
    )
    expert = SentimentExpert()
    report, _ = await expert.analyze(
        engagement_id="e1", symbol="ACME", context={},
    )
    assert not report.data_sufficiency
    assert "all sentiment sub-sources failed" in (report.refusal_reason or "")


@pytest.mark.asyncio
async def test_partial_subsource_failure_still_produces_report():
    """If recommendations are gone but news + info return, we still
    report — partial signal is better than none. Confidence should
    reflect the completeness."""
    _install_fake_yf(
        recs=None, fail_recs=True,
        news=[{"title": "Acme beats expectations"}],
        info={"currentPrice": 100.0, "targetMeanPrice": 110.0, "numberOfAnalystOpinions": 5},
    )
    expert = SentimentExpert()
    report, _ = await expert.analyze(
        engagement_id="e1", symbol="ACME", context={},
    )
    assert report.data_sufficiency
    # 2 of 3 sub-sources resolved → confidence ≤ 2/3.
    assert report.confidence_score <= 0.67


# ── Tone keyword detection ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tone_keywords_use_word_boundaries():
    """`misstep` should NOT match `miss`. `dismisses` should NOT match
    `misses`. False positives here would skew lean."""
    _install_fake_yf(
        recs=[{"strongBuy": 5, "buy": 5, "hold": 3, "sell": 1, "strongSell": 0}],
        news=[
            # Should NOT count as bearish — these are word-boundary fakes.
            {"title": "Acme misstep on supply chain"},
            {"title": "Court dismisses suit against Acme"},
            # This SHOULD count as bullish.
            {"title": "Acme beats Wall Street"},
        ],
        info={"currentPrice": 100.0, "targetMeanPrice": 110.0},
    )
    expert = SentimentExpert()
    report, _ = await expert.analyze(
        engagement_id="e1", symbol="ACME", context={},
    )
    assert report.data_sufficiency
    # Find the news evidence row and verify the count.
    news_evidence = next(
        (e.claim for e in report.key_evidence if "headlines" in e.claim),
        "",
    )
    # 1 bullish ("beats"), 0 bearish (boundaries spared us).
    assert "1 bullish-toned" in news_evidence
    assert "0 bearish-toned" in news_evidence


@pytest.mark.asyncio
async def test_news_under_new_yfinance_content_shape_parsed():
    """Newer yfinance wraps each entry under 'content' with nested
    'provider'. We tolerate both old and new shapes."""
    _install_fake_yf(
        recs=None, fail_recs=True,
        news=[
            {
                "content": {
                    "title": "Acme beats earnings forecast",
                    "provider": {"displayName": "Bloomberg"},
                    "pubDate": "2026-04-30T10:00:00Z",
                },
            },
            {   # old shape
                "title": "Acme rallies on partnership news",
                "publisher": "Reuters",
                "providerPublishTime": 1700000000,
            },
        ],
        info={"currentPrice": 100.0, "targetMeanPrice": 105.0},
    )
    expert = SentimentExpert()
    report, _ = await expert.analyze(
        engagement_id="e1", symbol="ACME", context={},
    )
    assert report.data_sufficiency
    news_ev = next(
        (e.claim for e in report.key_evidence if "headlines" in e.claim),
        "",
    )
    # Both headlines parsed, both bullish-toned.
    assert "2 entries" in news_ev
    assert "2 bullish-toned" in news_ev
