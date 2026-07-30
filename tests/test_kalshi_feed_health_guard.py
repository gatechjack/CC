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


# ── R1 settlement-aware + R2 confirm-and-advance (2026-07-30) ─────────────────
#
# Root cause of the MaggieTheEagle latch: two KXFEDDECISION-26JUL markets SETTLED
# at the Fed announcement, so 2/3 tracked positions vanished in one cycle (>=60%),
# tripping the breaker. The old breaker retained the stale snapshot and re-fired
# every cycle forever. R1 recognises a resolved/void market as a legitimate exit
# (advance, no alarm); R2 gives a recovery path for still-active disappearances.


class _StubResolver:
    """Stub trade_tape_fetcher exposing get_market_resolution (R1) — maps
    ticker -> Kalshi resolution status. Unlisted tickers default to 'pending'
    (still-active / unconfirmed = suspicious), matching the production default.
    Also usable as an exit quote_fetcher (get_market_resolution priced exits)."""

    def __init__(self, statuses=None, raise_on=None):
        self._statuses = dict(statuses or {})
        self._raise_on = set(raise_on or ())

    async def get_market_resolution(self, ticker):
        if ticker in self._raise_on:
            raise RuntimeError("kalshi api unreachable")
        status = self._statuses.get(ticker, "pending")
        result = {"resolved": "yes", "void": "void"}.get(status)
        return {"status": status, "result": result, "ticker": ticker,
                "close_time": "", "expiration_time": ""}


async def test_settled_disappearance_advances_snapshot_no_alarm(agent):
    """(a) R1: every vanished ticker resolved -> snapshot advances, ZERO alarms."""
    a, db_url = agent
    _seed_selected(db_url)
    apify = _StubApify([
        [_wp("alice", "T1"), _wp("alice", "T2"), _wp("alice", "T3")],  # cold start
        [_wp("alice", "T3")],                                          # T1,T2 vanished (66%)
    ])
    resolver = _StubResolver({"T1": "resolved", "T2": "resolved", "T3": "pending"})
    lg = _Logger()
    await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=resolver, logger_agent=lg)
    orders = await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=resolver, logger_agent=lg)
    assert orders == []                                            # unheld -> no-op exits
    snap = _db.load_agent_state("kalshi_copy_trader", "positions:alice", db_url=db_url)[0]
    assert set(snap.keys()) == {"T3"}                             # ADVANCED, T1/T2 dropped
    assert "kalshi_copy_feed_anomaly" not in lg.kinds()           # settlement = non-event
    assert a.drain_feed_alarms() == []
    assert a._load_anomaly_streak("alice") == {}                  # no streak started


async def test_active_disappearance_alarms_as_suspicious(agent):
    """(b) The REAL feed-anomaly case still works: still-active tickers vanish ->
    alarm once + retain (do not break the safety case while fixing false positives)."""
    a, db_url = agent
    _seed_selected(db_url)
    apify = _StubApify([
        [_wp("alice", "T1"), _wp("alice", "T2"), _wp("alice", "T3")],  # cold start
        [_wp("alice", "T3")],                                          # T1,T2 vanished
    ])
    resolver = _StubResolver({"T1": "pending", "T2": "pending", "T3": "pending"})  # still active
    lg = _Logger()
    await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=resolver, logger_agent=lg)
    await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=resolver, logger_agent=lg)
    snap = _db.load_agent_state("kalshi_copy_trader", "positions:alice", db_url=db_url)[0]
    assert set(snap.keys()) == {"T1", "T2", "T3"}                 # suspicious tickers RETAINED
    alarms = a.drain_feed_alarms()
    assert len(alarms) == 1 and alarms[0]["reason"] == "mass_disappearance"
    assert a._load_anomaly_streak("alice").get("count") == 1


async def test_suspicious_persists_confirms_after_n_cycles(agent):
    """(c) Inconclusive disappearance persists N=3 cycles -> accept + single
    confirmed alarm; snapshot advances; exactly two alarms total (no per-cycle spam)."""
    a, db_url = agent
    _seed_selected(db_url)
    apify = _StubApify([
        [_wp("alice", "T1"), _wp("alice", "T2"), _wp("alice", "T3")],  # poll1 cold
        [_wp("alice", "T3")],                                          # poll2+ T1,T2 gone, active
    ])
    resolver = _StubResolver({"T1": "pending", "T2": "pending", "T3": "pending"})
    lg = _Logger()
    await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=resolver, logger_agent=lg)  # cold
    await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=resolver, logger_agent=lg)  # streak 1 -> alarm
    await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=resolver, logger_agent=lg)  # streak 2 -> silent
    await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=resolver, logger_agent=lg)  # streak 3=N -> confirm
    snap = _db.load_agent_state("kalshi_copy_trader", "positions:alice", db_url=db_url)[0]
    assert set(snap.keys()) == {"T3"}                             # advanced after confirm
    reasons = [x["reason"] for x in a.drain_feed_alarms()]
    assert reasons == ["mass_disappearance", "confirmed_real_after_n_cycles"]
    assert a._load_anomaly_streak("alice") == {}                  # streak cleared on confirm


async def test_maggie_fed_settlement_self_heals(agent):
    """(d) The exact incident: MaggieTheEagle's two 26JUL Fed markets settle while
    26SEP stays active -> latch self-heals to {26SEP} on the first post-fix cycle."""
    a, db_url = agent
    _seed_selected(db_url, ("MaggieTheEagle",))
    jul0, jul25, sep = (
        "KXFEDDECISION-26JUL-H0", "KXFEDDECISION-26JUL-H25", "KXFEDDECISION-26SEP-H0",
    )
    apify = _StubApify([
        [_wp("MaggieTheEagle", jul0), _wp("MaggieTheEagle", jul25), _wp("MaggieTheEagle", sep)],
        [_wp("MaggieTheEagle", sep)],
    ])
    resolver = _StubResolver({jul0: "resolved", jul25: "resolved", sep: "pending"})
    lg = _Logger()
    await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=resolver, logger_agent=lg)  # cold
    orders = await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=resolver, logger_agent=lg)
    assert orders == []
    snap = _db.load_agent_state("kalshi_copy_trader", "positions:MaggieTheEagle", db_url=db_url)[0]
    assert set(snap.keys()) == {sep}                             # latch cleared silently
    assert "kalshi_copy_feed_anomaly" not in lg.kinds()
    assert a.drain_feed_alarms() == []


async def test_held_copy_settled_still_exits(agent):
    """(e) R1 must not swallow a REAL exit: a settled market we HELD a copy of
    still emits a priced exit and advances out of the book."""
    a, db_url = agent
    _seed_selected(db_url)
    _db.set_agent_state("kalshi_copy_trader", "positions:alice", {
        "T1": {"contracts": 100, "pnl": 0.0, "first_seen_iso": "2026-01-01T00:00:00+00:00",
               "our_side": "yes", "copy_size_usd": 2.0, "entry_price": 0.5},
        "T2": {"contracts": 100, "pnl": 0.0, "first_seen_iso": "2026-01-01T00:00:00+00:00",
               "our_side": "", "copy_size_usd": 0.0, "entry_price": None},
        "T3": {"contracts": 100, "pnl": 0.0, "first_seen_iso": "2026-01-01T00:00:00+00:00",
               "our_side": "", "copy_size_usd": 0.0, "entry_price": None},
    }, db_url=db_url)
    apify = _StubApify([[_wp("alice", "T3")]])                    # T1,T2 vanished; not cold (snap seeded)
    resolver = _StubResolver({"T1": "resolved", "T2": "resolved", "T3": "pending"})
    lg = _Logger()
    orders = await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=resolver, logger_agent=lg)
    assert len(orders) == 1 and orders[0].symbol.startswith("T1")  # our held T1 copy exits
    assert orders[0].limit_price == 1.0                            # resolved YES win -> $1.00
    snap = _db.load_agent_state("kalshi_copy_trader", "positions:alice", db_url=db_url)[0]
    assert set(snap.keys()) == {"T3"}
    assert "kalshi_copy_feed_anomaly" not in lg.kinds()


async def test_disappearance_suspicious_when_resolution_unavailable(agent):
    """(f) Safe direction: if settlement can't be confirmed (no fetcher), a
    disappearance is treated as suspicious (retain + alarm), never auto-dropped."""
    a, db_url = agent
    _seed_selected(db_url)
    apify = _StubApify([
        [_wp("alice", "T1"), _wp("alice", "T2"), _wp("alice", "T3")],
        [_wp("alice", "T3")],
    ])
    lg = _Logger()
    await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=None, logger_agent=lg)  # cold
    await a.run_scan_cycle(apify_client=apify, trade_tape_fetcher=None, logger_agent=lg)
    snap = _db.load_agent_state("kalshi_copy_trader", "positions:alice", db_url=db_url)[0]
    assert set(snap.keys()) == {"T1", "T2", "T3"}                 # retained, NOT dropped
    alarms = a.drain_feed_alarms()
    assert len(alarms) == 1 and alarms[0]["reason"] == "mass_disappearance"
