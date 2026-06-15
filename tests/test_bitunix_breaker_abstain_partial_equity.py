"""Drawdown-breaker ABSTAIN-on-partial-equity safety fix (2026-06-15).

Built from scoping `c2f2a88`
(runbooks/2026-06-15_breaker_abstain_on_partial_equity_scoping.md).

The bug: a BitUnix 10006 on a stablecoin makes `snapshot()` drop that coin →
`equity` is UNDER-reported → the drawdown breaker computes a phantom drawdown
vs the peak HWM and could FALSE-FLATTEN a live account (e.g. equity reads ~$25
of a real ~$3,382 → ~99% apparent drawdown → flatten).

The fix, two layers:
  1. broker — `BitunixBroker.snapshot()` sets `AccountSnapshot.equity_complete`
     from the stablecoin reads (False if any errored and was dropped).
  2. observer — `_abstain_on_incomplete_equity()` returns True (abstain + emit a
     `breaker_abstain_incomplete_equity` audit) on an incomplete read, False
     (proceed → the real breaker runs) on a complete one. It is called as an
     early-return guard in BOTH propose paths
     (`_score_and_maybe_propose_locked`, `_maybe_propose`) BEFORE the risk eval /
     flatten dispatch — so helper=True means the propose method returns before
     the breaker can act. Abstain is conditioned ONLY on incompleteness: a
     complete read showing real drawdown still flattens.

Mocked + fundless.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
)
from trading_corp.agents.risk import RiskAgent
from trading_corp.brokers.base import AccountSnapshot
from trading_corp.brokers.bitunix import BitunixBroker
from trading_corp.persistence import db
from trading_corp.persistence.models import (
    AccountState,
    ProposedOrder,
    StrategyState,
)

# ─── broker layer: the completeness signal ──────────────────────────────

_ACCOUNT = "/api/v1/futures/account"
_POSITION = "/api/v1/futures/position/get_pending_positions"
_AVAIL = {"USDT": 25.27, "USDC": 3356.70}  # 2026-05-10 live shape; sum 3381.97


def _resp(payload):
    r = MagicMock()
    r.raise_for_status = MagicMock(return_value=None)
    r.json = MagicMock(return_value=payload)
    return r


def _broker_with(coin_codes, *, positions_code=0):
    """A BitunixBroker whose `_client.get` returns the given BitUnix envelope
    code per stablecoin ('USDT'/'USDC') and for the position endpoint."""
    broker = BitunixBroker(api_key="k", api_secret="s")

    async def fake_get(url, params=None, headers=None):
        if url == _ACCOUNT:
            coin = (params or {}).get("marginCoin")
            code = coin_codes.get(coin, 0)
            data = {"available": _AVAIL.get(coin, 0.0)} if code == 0 else {}
            return _resp({"code": code, "data": data, "msg": "x"})
        return _resp({"code": positions_code, "data": []})

    client = MagicMock()
    client.get = fake_get
    broker._client = client
    return broker


@pytest.mark.asyncio
async def test_snapshot_all_coins_ok_is_complete():
    snap = await _broker_with({"USDT": 0, "USDC": 0}).snapshot()
    assert snap.equity_complete is True
    assert snap.equity == pytest.approx(_AVAIL["USDT"] + _AVAIL["USDC"])


@pytest.mark.asyncio
async def test_snapshot_coin_10006_is_incomplete_and_underreports():
    # USDC (high balance) 10006s → dropped → equity ≈ USDT only (under-reported).
    snap = await _broker_with({"USDT": 0, "USDC": 10006}).snapshot()
    assert snap.equity_complete is False
    assert snap.equity == pytest.approx(_AVAIL["USDT"])


@pytest.mark.asyncio
async def test_snapshot_position_error_does_not_mark_equity_incomplete():
    # Position read errors but both stablecoin reads are fine → equity is whole,
    # so equity_complete stays True (position errors are not equity errors).
    snap = await _broker_with({"USDT": 0, "USDC": 0}, positions_code=10006).snapshot()
    assert snap.equity_complete is True


@pytest.mark.asyncio
async def test_stub_snapshot_is_complete():
    snap = await BitunixBroker(api_key=None, api_secret=None).snapshot()  # stub
    assert snap.equity_complete is True
    assert snap.equity == 0.0


# ─── observer layer: the abstain decision + audit ───────────────────────


@pytest.fixture
def observer(tmp_path):
    db_path = tmp_path / "abstain.db"
    db.init_db(f"sqlite:///{db_path}")
    obs = BitunixFuturesObserver(db_url=f"sqlite:///{db_path}")
    obs.logger_agent = MagicMock()  # capture log_event calls
    return obs


def _snap(equity, complete):
    return AccountSnapshot(
        account="bitunix-futures", equity=equity, buying_power=equity,
        cash=equity, positions=[], equity_complete=complete,
    )


def _order():
    return ProposedOrder(strategy="demo", symbol="SPY", side="buy", qty=10,
                         order_type="limit", limit_price=500.0)


def _strategy():
    return StrategyState(strategy="demo", halted=False, realized_pnl=0.0)


def test_abstain_false_on_complete_read(observer):
    """A COMPLETE read does NOT abstain → the propose flow proceeds to the real
    breaker. No abstain audit emitted."""
    assert observer._abstain_on_incomplete_equity(_snap(3382.0, True)) is False
    observer.logger_agent.log_event.assert_not_called()


def test_abstain_true_on_incomplete_read_and_emits_audit(observer):
    """A PARTIAL read abstains (returns True) and emits the
    `breaker_abstain_incomplete_equity` safety audit."""
    assert observer._abstain_on_incomplete_equity(_snap(25.27, False)) is True
    observer.logger_agent.log_event.assert_called_once()
    kwargs = observer.logger_agent.log_event.call_args.kwargs
    assert kwargs["kind"] == "breaker_abstain_incomplete_equity"
    assert kwargs["payload"]["equity_complete"] is False
    assert kwargs["payload"]["equity_read"] == pytest.approx(25.27)


def test_abstain_missing_field_defaults_to_proceed(observer):
    """A snapshot object predating the `equity_complete` field (getattr default
    True) must NOT abstain — backward-compatible."""
    legacy = MagicMock(spec=["equity"])
    legacy.equity = 1000.0
    assert observer._abstain_on_incomplete_equity(legacy) is False


def test_abstain_bug_case_phantom_99pct(observer):
    """THE bug: equity reads ~$25 (USDC dropped) vs a stored peak of ~$3,382.
    The breaker ABSTAINS (no flatten) and the audit records the ~99% phantom
    drawdown it avoided — rather than flattening on it."""
    observer._tracked_peak_equity(3381.97)  # establish the real peak HWM
    assert observer._abstain_on_incomplete_equity(_snap(25.27, False)) is True
    payload = observer.logger_agent.log_event.call_args.kwargs["payload"]
    assert payload["would_be_drawdown_pct"] == pytest.approx(
        (3381.97 - 25.27) / 3381.97, rel=1e-3,
    )
    assert payload["would_be_drawdown_pct"] > 0.98  # ~99% phantom, abstained


def test_abstain_does_not_ratchet_the_peak(observer):
    """Abstaining must be side-effect-free on the HWM: the peak read for the
    audit is a PURE read, never a ratchet. An under-reported equity must not
    touch the stored peak."""
    observer._tracked_peak_equity(3381.97)
    observer._abstain_on_incomplete_equity(_snap(25.27, False))
    loaded = db.load_agent_state("bitunix_futures", "account_peak_equity",
                                 db_url=observer.db_url)
    assert loaded[0]["peak"] == pytest.approx(3381.97)  # unchanged


# ─── preserved: a COMPLETE read with real drawdown still flattens ───────


def test_complete_read_real_drawdown_still_flattens(observer, tmp_risk_yaml):
    """Safety net preserved: the helper does NOT short-circuit a COMPLETE read,
    and a genuine ≥15% drawdown from that complete read still produces
    flatten_account=True from the real RiskAgent."""
    assert observer._abstain_on_incomplete_equity(_snap(85_000.0, True)) is False

    observer._tracked_peak_equity(100_000.0)
    peak = observer._tracked_peak_equity(85_000.0)  # dip → peak held at 100k
    acct = AccountState(account="bitunix_futures", equity=85_000.0, peak_equity=peak)
    assert acct.drawdown_pct() == pytest.approx(0.15)
    v = RiskAgent(risk_yaml=tmp_risk_yaml, narrator_enabled=False).evaluate(
        _order(), acct, _strategy())
    assert v.flatten_account is True  # real breaker still fires on a complete read


def test_complete_read_healthy_equity_no_action(observer, tmp_risk_yaml):
    """Complete read at the peak → no abstain AND no flatten."""
    assert observer._abstain_on_incomplete_equity(_snap(100_000.0, True)) is False

    observer._tracked_peak_equity(100_000.0)
    peak = observer._tracked_peak_equity(100_000.0)
    acct = AccountState(account="bitunix_futures", equity=100_000.0, peak_equity=peak)
    v = RiskAgent(risk_yaml=tmp_risk_yaml, narrator_enabled=False).evaluate(
        _order(), acct, _strategy())
    assert v.flatten_account is False
