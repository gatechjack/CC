"""K5·4 — feed-health / mass-exit circuit breaker. The non-negotiable safety item:
a suspicious empty/partial Apify payload must NOT synthesize mass exits in live mode.
Network-free (stub apify client + recorder logger; agent on a temp sqlite)."""
from __future__ import annotations

from typing import Any

import pytest

from trading_corp.data.kalshi_apify_client import WhalePosition
from trading_corp.persistence import db as _db


@pytest.fixture
def agent(tmp_path):
    from trading_corp.agents.strategies.kalshi_copy_trader import KalshiCopyTraderAgent
    db_url = f"sqlite:///{tmp_path / 'k5feed.db'}"
    _db.init_db(db_url)
    yaml_path = tmp_path / "strategies.yaml"
    yaml_path.write_text("kalshi_copy_trader:\n  enabled: true\n  poll_interval_sec: 300\n")
    risk_path = tmp_path / "risk.yaml"
    risk_path.write_text("kalshi: {}\n")
    a = KalshiCopyTraderAgent(strategies_yaml=yaml_path, risk_yaml=risk_path, db_url=db_url)
    return a, db_url


def _wp(name, ticker, contracts=100):
    return WhalePosition(market_id=f"m_{ticker}", market_ticker=ticker, name=name,
                         is_open=True, pnl=0.0, contracts=contracts)


class _StubApify:
    def __init__(self, calls):
        self._calls = calls
        self._i = 0

    async def fetch_open_positions(self, names):
        idx = min(self._i, len(self._calls) - 1)
        self._i += 1
        return list(self._calls[idx])


class _RaisingApify:
    async def fetch_open_positions(self, names):
        raise RuntimeError("HTTP 400 — feed cap exhausted")


class _Logger:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []
        self.proposed: list[Any] = []

    def log_event(self, actor, kind, payload):
        self.events.append((actor, kind, payload))

    def log_proposed_order(self, order):
        self.proposed.append(order)

    def kinds(self):
        return [k for _, k, _ in self.events]


def _seed_selected(db_url, whales=("alice",)):
    _db.set_agent_state("kalshi_copy_trader", "selected_whales", list(whales), db_url=db_url)


# ── pure _is_mass_disappearance ──────────────────────────────────────────────


def test_is_mass_disappearance_threshold(agent):
    a, _ = agent
    assert a._is_mass_disappearance({"a", "b", "c"}, {"a"}) is False           # 33% < 60
    assert a._is_mass_disappearance({"a", "b", "c"}, {"a", "b"}) is True        # 66% >= 60
    assert a._is_mass_disappearance({"a", "b", "c"}, {"a", "b", "c"}) is True   # 100%
    assert a._is_mass_disappearance({"a"}, {"a"}) is False                      # below min=2
    assert a._is_mass_disappearance({"a", "b", "c"}, set()) is False            # 0 removed


# ── integration: mass-disappearance suppresses exits + retains snapshot ──────


async def test_empty_feed_suppresses_exits_and_retains_snapshot(agent):
    a, db_url = agent
    _seed_selected(db_url)
    apify = _StubApify([
        [_wp("alice", "T1"), _wp("alice", "T2"), _wp("alice", "T3")],  # poll1: cold start
        [],                                                            # poll2: EMPTY feed
    ])
    lg = _Logger()
    assert await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=None, logger_agent=lg) == []
    orders = await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=None, logger_agent=lg)
    assert orders == []                                  # NO synthetic exits emitted
    snap = _db.load_agent_state("kalshi_copy_trader", "positions:alice", db_url=db_url)[0]
    assert set(snap.keys()) == {"T1", "T2", "T3"}        # snapshot RETAINED, not dropped
    assert "kalshi_copy_feed_anomaly" in lg.kinds()
    alarms = a.drain_feed_alarms()
    assert len(alarms) == 1 and alarms[0]["reason"] == "mass_disappearance"
    assert a.drain_feed_alarms() == []                   # drained


async def test_normal_single_exit_not_suppressed(agent):
    a, db_url = agent
    _seed_selected(db_url)
    apify = _StubApify([
        [_wp("alice", "T1"), _wp("alice", "T2"), _wp("alice", "T3")],  # poll1 cold start
        [_wp("alice", "T1"), _wp("alice", "T2")],                      # poll2: T3 gone (33%)
    ])
    lg = _Logger()
    await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=None, logger_agent=lg)
    await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=None, logger_agent=lg)
    snap = _db.load_agent_state("kalshi_copy_trader", "positions:alice", db_url=db_url)[0]
    assert set(snap.keys()) == {"T1", "T2"}              # T3 dropped normally
    assert "kalshi_copy_feed_anomaly" not in lg.kinds()
    assert a.drain_feed_alarms() == []


# ── consecutive fetch-failure alarm ──────────────────────────────────────────


async def test_consecutive_fetch_failures_alarm(agent):
    a, db_url = agent
    _seed_selected(db_url)
    apify = _RaisingApify()
    lg = _Logger()
    # First two failures: benign, no alarm (default threshold = 3).
    await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=None, logger_agent=lg)
    await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=None, logger_agent=lg)
    assert "kalshi_copy_feed_anomaly" not in lg.kinds()
    # Third: streak crosses threshold -> alarm + audit.
    await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=None, logger_agent=lg)
    assert "kalshi_copy_feed_anomaly" in lg.kinds()
    alarms = a.drain_feed_alarms()
    assert alarms and alarms[-1]["reason"] == "consecutive_fetch_failures"


async def test_fetch_success_resets_failure_streak(agent):
    a, db_url = agent
    _seed_selected(db_url)
    # 2 failures, then a success, then 2 failures => never 3-in-a-row => no alarm.
    fail = _RaisingApify()
    ok = _StubApify([[_wp("alice", "T1")]])
    lg = _Logger()
    await a.run_scan_cycle(apify_client=fail, trade_tape_fetcher=None, logger_agent=lg)
    await a.run_scan_cycle(apify_client=fail, trade_tape_fetcher=None, logger_agent=lg)
    await a.run_scan_cycle(apify_client=ok, trade_tape_fetcher=None, logger_agent=lg)  # resets
    assert a._consecutive_fetch_failures == 0
    await a.run_scan_cycle(apify_client=fail, trade_tape_fetcher=None, logger_agent=lg)
    await a.run_scan_cycle(apify_client=fail, trade_tape_fetcher=None, logger_agent=lg)
    assert "kalshi_copy_feed_anomaly" not in lg.kinds()
