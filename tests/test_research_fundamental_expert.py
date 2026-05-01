"""FundamentalExpert unit tests (Phase 1c).

Mocks `yfinance.Ticker.info` directly so tests are deterministic +
offline. Pins:
  1. Happy path with full snapshot → bullish lean + populated evidence.
  2. Bearish snapshot (negative margin, contracting revenue) → bearish.
  3. Sparse snapshot (< 3 fields) → refusal, not fabrication.
  4. yfinance import / fetch failure → refusal with the failure reason.
  5. Non-equity symbols (containing "/" or " ") refuse without fetching.
  6. The `on_data_fetch` callback fires only on FAILURE (Refinement 4).
"""
from __future__ import annotations

import sys
import types

import pytest

from trading_corp.agents.research.experts.fundamental import FundamentalExpert


def _install_fake_yf(info_dict, *, raise_on_fetch=False):
    """Install a minimal `yfinance` shim returning a controllable .info dict."""
    yf = types.ModuleType("yfinance")

    class _FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol
            if raise_on_fetch:
                raise RuntimeError("simulated yfinance outage")

        @property
        def info(self):
            return info_dict

    yf.Ticker = _FakeTicker
    sys.modules["yfinance"] = yf
    return yf


def _uninstall_yf():
    sys.modules.pop("yfinance", None)


@pytest.fixture(autouse=True)
def _clean_yf():
    yield
    _uninstall_yf()


# ── Happy path ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_bullish_lean():
    """Profitable, growing, low leverage, positive FCF → bullish."""
    _install_fake_yf({
        "trailingPE": 22.5,
        "priceToBook": 6.1,
        "debtToEquity": 30.0,            # 0.30x — low leverage
        "revenueGrowth": 0.18,           # +18% yoy
        "earningsGrowth": 0.25,
        "profitMargins": 0.22,
        "grossMargins": 0.55,
        "freeCashflow": 30_000_000_000.0,
        "marketCap": 2_500_000_000_000.0,
        "symbol": "AAPL",
        "shortName": "Apple Inc.",
    })
    expert = FundamentalExpert()
    report, cost = await expert.analyze(
        engagement_id="e1", symbol="AAPL", context={},
    )
    assert report.data_sufficiency
    assert report.directional_lean == "bullish"
    assert report.confidence_score > 0.5
    assert report.refusal_reason is None
    # Evidence items should reference what we fed in.
    claims = " ".join(e.claim for e in report.key_evidence)
    assert "P/E 22.5" in claims
    assert "+18.0%" in claims    # revenue growth
    # No LLM in test env — narration cost is 0.
    assert cost == 0.0


@pytest.mark.asyncio
async def test_bearish_lean_when_unprofitable_contracting():
    _install_fake_yf({
        "trailingPE": None,              # often missing for unprofitable names
        "debtToEquity": 350.0,           # 3.5x — high leverage
        "revenueGrowth": -0.12,          # -12% yoy
        "earningsGrowth": -0.40,
        "profitMargins": -0.08,
        "grossMargins": 0.20,
        "freeCashflow": -500_000_000.0,
        "marketCap": 1_000_000_000.0,
        "symbol": "RISKY",
        "shortName": "Risky Co",
    })
    expert = FundamentalExpert()
    report, _ = await expert.analyze(
        engagement_id="e1", symbol="RISKY", context={},
    )
    assert report.data_sufficiency
    assert report.directional_lean == "bearish"


@pytest.mark.asyncio
async def test_neutral_lean_on_mixed_signals():
    _install_fake_yf({
        "trailingPE": 30.0,
        "profitMargins": 0.05,           # modest profit
        "revenueGrowth": 0.02,           # ~flat
        "freeCashflow": 1_000_000.0,     # modestly positive
        "symbol": "MIXED",
    })
    expert = FundamentalExpert()
    report, _ = await expert.analyze(
        engagement_id="e1", symbol="MIXED", context={},
    )
    # 4 fields populated → not refused. Lean: small positives, weak score → neutral.
    assert report.data_sufficiency
    assert report.directional_lean == "neutral"


# ── Refusal paths ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refuses_on_sparse_snapshot():
    """yfinance occasionally returns near-empty dict for delisted symbols."""
    _install_fake_yf({"symbol": "GHOST", "logo_url": ""})
    expert = FundamentalExpert()
    report, _ = await expert.analyze(
        engagement_id="e1", symbol="GHOST", context={},
    )
    assert not report.data_sufficiency
    assert report.refusal_reason
    assert "sparse" in report.refusal_reason or "no info" in report.refusal_reason


@pytest.mark.asyncio
async def test_refuses_on_yfinance_fetch_failure():
    _install_fake_yf({}, raise_on_fetch=True)
    expert = FundamentalExpert()
    report, _ = await expert.analyze(
        engagement_id="e1", symbol="AAPL", context={},
    )
    assert not report.data_sufficiency
    assert "yfinance fetch failed" in (report.refusal_reason or "")


@pytest.mark.asyncio
async def test_refuses_when_yfinance_not_installed():
    _uninstall_yf()
    # Now blocking import: prevent yfinance from being importable at all.
    sys.modules["yfinance"] = None    # type: ignore[assignment]
    try:
        expert = FundamentalExpert()
        report, _ = await expert.analyze(
            engagement_id="e1", symbol="AAPL", context={},
        )
        assert not report.data_sufficiency
        assert "not installed" in (report.refusal_reason or "")
    finally:
        sys.modules.pop("yfinance", None)


@pytest.mark.asyncio
async def test_refuses_on_non_equity_symbol_without_fetching():
    """`BTC/USD` and option symbols (containing space) skip the fetch
    entirely. Pin this so we don't waste yfinance calls on guaranteed
    refusals."""
    fetch_calls: list[str] = []

    yf = types.ModuleType("yfinance")

    class _FakeTicker:
        def __init__(self, symbol):
            fetch_calls.append(symbol)
            self.symbol = symbol

        @property
        def info(self):
            return {"symbol": self.symbol, "trailingPE": 20.0}
    yf.Ticker = _FakeTicker
    sys.modules["yfinance"] = yf

    expert = FundamentalExpert()
    report1, _ = await expert.analyze(
        engagement_id="e1", symbol="BTC/USD", context={},
    )
    report2, _ = await expert.analyze(
        engagement_id="e1", symbol="AAPL 2027-01-15 C 200.00", context={},
    )
    assert not report1.data_sufficiency
    assert not report2.data_sufficiency
    assert "not applicable" in report1.refusal_reason
    # Critical: we never hit yfinance for these symbols.
    assert fetch_calls == []


# ── Refinement 4: failure-only data-fetch callback ────────────────────────


@pytest.mark.asyncio
async def test_on_data_fetch_fires_only_on_failure():
    """Successful fetch → callback NOT called (the ExpertReport itself is
    evidence of retrieval). Failed fetch → callback called."""
    calls: list[dict] = []
    def cb(*, source, ok, error=None):
        calls.append({"source": source, "ok": ok, "error": error})

    # Success first.
    _install_fake_yf({
        "trailingPE": 20.0, "profitMargins": 0.10,
        "revenueGrowth": 0.08, "freeCashflow": 1e9, "marketCap": 1e10,
        "debtToEquity": 50.0,
    })
    expert = FundamentalExpert()
    report, _ = await expert.analyze(
        engagement_id="e1", symbol="AAPL", context={}, on_data_fetch=cb,
    )
    assert report.data_sufficiency
    assert calls == []

    # Now failure: callback fires.
    _install_fake_yf({}, raise_on_fetch=True)
    report, _ = await expert.analyze(
        engagement_id="e1", symbol="AAPL", context={}, on_data_fetch=cb,
    )
    assert not report.data_sufficiency
    assert len(calls) == 1
    assert calls[0]["ok"] is False
    assert "AAPL" in calls[0]["source"]
