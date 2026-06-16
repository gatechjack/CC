"""E5b — exit escalating-chase + reconciliation (Option 1: re-insert on live reconcile).

Fundless, fully mocked — no live SDK, no real order, no division flipped live, no
exit_chase armed in config (the mechanism ships OFF by default; these tests arm it
in-process only). Mirrors test_polymarket_copy_e2_6_loop_wiring.py.

Coverage:
  * central invariant — partial chase accumulating to full → slot popped; not
    clearing → slot RETAINED (actual residual + reconcile_needed), never popped while
    residual > EPS;
  * chase mechanics — descending best-bid pricing, per-attempt no-fill escalation,
    cumulative VWAP, terminal aggressiveness, total no-fill raises, best-bid≤0 stops;
  * the CLAMP/tick-round hard requirement — a step below min_price is clamped (never
    posts ≤0), an unpriceable book stops the chase (no ValueError);
  * record_exit_fill — decrements by ACTUAL not intended, complete flagged slot,
    fill=None → whole lot retained (exit_no_fill), EPS dust → stays popped;
  * gating — entries never hit the chase even when armed (E2·6 byte-identical),
    unset exit_chase → single-shot;
  * paper byte-identical — a paper exit returns early and NEVER reconciles.

⚠ get_price(SELL) best-bid DIRECTION is LIVE-VERIFY-at-OP·E: these tests mock
best_bid and therefore do NOT establish that get_price(token_id, SELL) returns the
best bid vs the best ask. See PolymarketBroker.best_bid.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import trading_corp.brokers.polymarket_live as pl
from trading_corp.agents.strategies.polymarket_copy_trader import (
    PolymarketCopyTraderAgent,
)
from trading_corp.brokers.polymarket_live import (
    _EXIT_MAX_PRICE,
    NoFillInWindow,
    PolymarketLiveBroker,
    _clamp_exit_price,
)
from trading_corp.data.polymarket_data_api_client import ActivityRow
from trading_corp.main import _handle_copy_order_placement
from trading_corp.persistence import db as _db
from trading_corp.persistence.db import set_agent_state
from trading_corp.persistence.models import FillEvent, ProposedOrder


# ── shared builders ──────────────────────────────────────────────────────────


def _fe(qty, price, *, symbol="cidX:Yes"):
    return FillEvent(order_id="x", symbol=symbol, side="sell", qty=qty, price=price,
                     ts="2026-06-16T00:00:00+00:00", venue="polymarket")


def _armed_broker(**ec):
    cfg = {"enabled": True, "max_attempts": 3, "spread_fraction": 0.25,
           "terminal_aggressiveness": 0.5, "min_price": 0.01}
    cfg.update(ec)
    b = PolymarketLiveBroker(
        private_key="0xk", funder_address="0xf", polygon_rpc_url="http://rpc",
        exit_chase=cfg,
    )
    b._clob = MagicMock()
    b._connected = True
    b._read = MagicMock()
    return b


def _exit_order(qty=1.0, **extra):
    ext = {"is_entry": False, "token_id": "TID", "condition_id": "cidX",
           "outcome_index": 0}
    ext.update(extra)
    return ProposedOrder(
        strategy="polymarket_copy_trader", symbol="cidX:Yes", side="sell",
        qty=qty, order_type="market", limit_price=0.5, extra=ext,
    )


# ── ctor: the chase is OFF unless explicitly enabled ─────────────────────────


def test_exit_chase_disabled_or_absent_is_inert():
    mk = lambda **kw: PolymarketLiveBroker(
        private_key="k", funder_address="f", polygon_rpc_url="r", **kw)
    assert mk()._exit_chase is None                                     # unset
    assert mk(exit_chase={"enabled": False, "max_attempts": 3})._exit_chase is None
    assert mk(exit_chase={"max_attempts": 3})._exit_chase is None       # no enabled key
    assert mk(exit_chase={"enabled": True})._exit_chase == {"enabled": True}


# ── gating: entries & unarmed exits take the single-shot path (E2·6 unchanged) ─


@pytest.mark.asyncio
async def test_entry_never_hits_chase_even_when_armed(monkeypatch):
    b = _armed_broker()
    b._read.best_bid = AsyncMock(return_value=0.5)
    synth = AsyncMock(return_value="ENTRY_FILL")
    monkeypatch.setattr(pl, "_place_order_fak_synth_fn", synth)
    chase = AsyncMock()
    monkeypatch.setattr(PolymarketLiveBroker, "_run_exit_chase", chase)
    entry = ProposedOrder(strategy="s", symbol="x:Yes", side="buy", qty=1.0,
                          limit_price=0.4, extra={"is_entry": True, "token_id": "T"})
    result = await b.place_order(entry)
    assert result == "ENTRY_FILL"            # single-shot fak_synth
    chase.assert_not_awaited()               # NEVER the chase
    synth.assert_awaited_once()


@pytest.mark.asyncio
async def test_unset_exit_chase_exit_uses_single_shot(monkeypatch):
    b = PolymarketLiveBroker(private_key="0xk", funder_address="0xf",
                             polygon_rpc_url="http://rpc")
    assert b._exit_chase is None
    b._clob = MagicMock()
    b._connected = True
    synth = AsyncMock(return_value="EXIT_FILL")
    monkeypatch.setattr(pl, "_place_order_fak_synth_fn", synth)
    exit_o = _exit_order()
    result = await b.place_order(exit_o)
    assert result == "EXIT_FILL"             # today's single-shot path, unchanged
    synth.assert_awaited_once_with(b._clob, exit_o, poll_seconds=b._fak_poll_seconds)


@pytest.mark.asyncio
async def test_missing_is_entry_does_not_divert_to_chase(monkeypatch):
    # STRICT `is False`: a missing is_entry (None) must NOT trigger the chase.
    b = _armed_broker()
    chase = AsyncMock()
    monkeypatch.setattr(PolymarketLiveBroker, "_run_exit_chase", chase)
    synth = AsyncMock(return_value="FILL")
    monkeypatch.setattr(pl, "_place_order_fak_synth_fn", synth)
    order = ProposedOrder(strategy="s", symbol="x:Yes", side="sell", qty=1.0,
                          limit_price=0.4, extra={"token_id": "T"})  # no is_entry
    await b.place_order(order)
    chase.assert_not_awaited()
    synth.assert_awaited_once()


# ── chase mechanics ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chase_accumulates_partials_to_cumulative_vwap(monkeypatch):
    b = _armed_broker(max_attempts=3)
    b._read.best_bid = AsyncMock(return_value=0.50)
    fills = iter([_fe(0.3, 0.50), _fe(0.3, 0.48), _fe(0.4, 0.45)])

    async def fake(client, order, *, poll_seconds):
        return next(fills)

    monkeypatch.setattr(pl, "place_order_fak_synth", fake)
    result = await b.place_order(_exit_order(qty=1.0))
    assert result.qty == pytest.approx(1.0)
    assert result.side == "sell"
    expected_vwap = (0.3 * 0.50 + 0.3 * 0.48 + 0.4 * 0.45) / 1.0
    assert result.price == pytest.approx(expected_vwap)


@pytest.mark.asyncio
async def test_chase_prices_descend_off_best_bid(monkeypatch):
    b = _armed_broker(max_attempts=3, spread_fraction=0.2, terminal_aggressiveness=0.5,
                      min_price=0.01)
    b._read.best_bid = AsyncMock(return_value=0.50)
    calls = []

    async def fake(client, order, *, poll_seconds):
        calls.append(order.limit_price)
        raise NoFillInWindow("nf")           # never fills → run all attempts incl terminal

    monkeypatch.setattr(pl, "place_order_fak_synth", fake)
    with pytest.raises(NoFillInWindow):      # total no-fill
        await b.place_order(_exit_order())
    # attempt 0: conc 0 → 0.50; 1: 0.2 → 0.40; 2: 0.4 → 0.30; terminal(3): 0.5 → 0.25
    assert calls == [pytest.approx(0.50), pytest.approx(0.40),
                     pytest.approx(0.30), pytest.approx(0.25)]
    assert calls == sorted(calls, reverse=True)   # strictly descending (more aggressive)


@pytest.mark.asyncio
async def test_chase_terminal_step_uses_terminal_aggressiveness(monkeypatch):
    b = _armed_broker(max_attempts=1, spread_fraction=0.25, terminal_aggressiveness=0.5)
    b._read.best_bid = AsyncMock(return_value=0.40)
    calls = []

    async def fake(client, order, *, poll_seconds):
        calls.append(order.limit_price)
        raise NoFillInWindow("nf")

    monkeypatch.setattr(pl, "place_order_fak_synth", fake)
    with pytest.raises(NoFillInWindow):
        await b.place_order(_exit_order())
    # attempt 0 (patient): conc 0 → 0.40; attempt 1 (terminal): conc 0.5 → 0.20
    assert calls == [pytest.approx(0.40), pytest.approx(0.20)]


@pytest.mark.asyncio
async def test_chase_per_attempt_nofill_escalates_not_aborts(monkeypatch):
    b = _armed_broker(max_attempts=3)
    b._read.best_bid = AsyncMock(return_value=0.50)
    seq = [NoFillInWindow("nf"), _fe(1.0, 0.40)]

    async def fake(client, order, *, poll_seconds):
        x = seq.pop(0)
        if isinstance(x, Exception):
            raise x
        return x

    monkeypatch.setattr(pl, "place_order_fak_synth", fake)
    result = await b.place_order(_exit_order(qty=1.0))
    assert result.qty == pytest.approx(1.0)  # 1st attempt no-fill, 2nd filled → continued


@pytest.mark.asyncio
async def test_chase_total_nofill_raises(monkeypatch):
    b = _armed_broker()
    b._read.best_bid = AsyncMock(return_value=0.50)

    async def fake(*a, **k):
        raise NoFillInWindow("nf")

    monkeypatch.setattr(pl, "place_order_fak_synth", fake)
    with pytest.raises(NoFillInWindow):
        await b.place_order(_exit_order())


@pytest.mark.asyncio
async def test_chase_real_placement_error_propagates(monkeypatch):
    # A real OrderPlacementError (rejected/unmatched) is NOT caught → propagates loudly.
    b = _armed_broker()
    b._read.best_bid = AsyncMock(return_value=0.50)

    async def fake(*a, **k):
        raise pl.OrderPlacementError("rejected: insufficient balance")

    monkeypatch.setattr(pl, "place_order_fak_synth", fake)
    with pytest.raises(pl.OrderPlacementError):
        await b.place_order(_exit_order())


@pytest.mark.asyncio
async def test_chase_best_bid_zero_stops_without_placing(monkeypatch):
    # No priceable book at all → total no-fill (raises), and NO ValueError, NO place.
    b = _armed_broker()
    b._read.best_bid = AsyncMock(return_value=0.0)
    fak = AsyncMock()
    monkeypatch.setattr(pl, "place_order_fak_synth", fak)
    with pytest.raises(NoFillInWindow):
        await b.place_order(_exit_order())
    fak.assert_not_awaited()                 # never posts an invalid ≤0 price


@pytest.mark.asyncio
async def test_chase_best_bid_drops_returns_cumulative(monkeypatch):
    # Partial fill then the book vanishes (best_bid→0): return what filled, retain rest.
    b = _armed_broker()
    b._read.best_bid = AsyncMock(side_effect=[0.50, 0.0])
    fills = iter([_fe(0.3, 0.50)])

    async def fake(client, order, *, poll_seconds):
        return next(fills)

    monkeypatch.setattr(pl, "place_order_fak_synth", fake)
    result = await b.place_order(_exit_order(qty=1.0))
    assert result.qty == pytest.approx(0.3)  # cumulative partial, not a throw


# ── the CLAMP / tick-round hard requirement ──────────────────────────────────


def test_clamp_floors_at_min_price():
    # 0.02*(1-0.9)=0.002 would be ≤ min_price → clamped UP to min_price, never ≤0
    assert _clamp_exit_price(0.02 * (1 - 0.9), 0.01) == pytest.approx(0.01)


def test_clamp_caps_below_one():
    assert _clamp_exit_price(1.5, 0.01) == pytest.approx(_EXIT_MAX_PRICE)
    assert _clamp_exit_price(0.9999, 0.01) <= _EXIT_MAX_PRICE


def test_clamp_rounds_to_tick():
    assert _clamp_exit_price(0.12345, 0.01) == pytest.approx(0.123)


@pytest.mark.asyncio
async def test_chase_step_below_min_price_is_clamped_not_thrown(monkeypatch):
    # A deep terminal off a low best-bid would math BELOW min_price; assert the price
    # actually posted is the clamped min_price (>0) and NO ValueError escapes.
    b = _armed_broker(max_attempts=0, terminal_aggressiveness=0.99, min_price=0.05)
    b._read.best_bid = AsyncMock(return_value=0.10)   # 0.10*(1-0.99)=0.001 → clamp→0.05
    calls = []

    async def fake(client, order, *, poll_seconds):
        calls.append(order.limit_price)
        raise NoFillInWindow("nf")

    monkeypatch.setattr(pl, "place_order_fak_synth", fake)
    with pytest.raises(NoFillInWindow):
        await b.place_order(_exit_order())
    assert calls == [pytest.approx(0.05)]    # floored at min_price, never ≤0


# ── record_exit_fill (direct) ────────────────────────────────────────────────


@pytest.fixture
def strategy(tmp_path):
    db_path = tmp_path / "pme5b.db"
    db_url = f"sqlite:///{db_path}"
    _db.init_db(db_url)
    yaml_path = tmp_path / "strategies.yaml"
    yaml_path.write_text(
        "polymarket_copy_trader:\n  enabled: true\n  poll_interval_sec: 60\n"
    )
    risk_path = tmp_path / "risk.yaml"
    risk_path.write_text("polymarket: {}\n")
    agent = PolymarketCopyTraderAgent(
        strategies_yaml=yaml_path, risk_yaml=risk_path, db_url=db_url,
    )
    return agent, db_url


def _seed(agent, our_positions=None):
    agent._save_whale_state("0xW", {
        "user_name": "alice", "last_seen_ts": 1, "last_seen_txhashes": [],
        "our_positions": our_positions or {},
    })


def _exit_proposal(copy_usdc=0.42, entry_price=0.42):
    # held = copy_usdc / entry_price (1.0 here) — mirrors _emit_exit's reconstruction.
    return ProposedOrder(
        strategy="polymarket_copy_trader", symbol="cidX:Yes", side="sell",
        qty=copy_usdc / entry_price, order_type="market", limit_price=0.55,
        extra={"is_entry": False, "whale_wallet": "0xW", "condition_id": "cidX",
               "outcome_index": 0, "outcome": "Yes", "implied_prob_at_entry": entry_price,
               "copy_size_usdc": copy_usdc, "entry_ts": 100, "exit_ts": 200,
               "whale_usdc_size": 50.0},
    )


def _pos(agent, cid="cidX", oi=0):
    st = agent._load_whale_state("0xW") or {}
    return (st.get("our_positions") or {}).get(agent._position_key(cid, oi))


def test_record_exit_fill_partial_retains_complete_flagged_residual(strategy):
    agent, _ = strategy
    _seed(agent)
    residual = agent.record_exit_fill(_exit_proposal(), _fe(0.6, 0.55))
    assert residual == pytest.approx(0.4)               # decrement by ACTUAL
    pos = _pos(agent)
    assert pos is not None                               # RETAINED, not popped
    assert pos["actual_fill_qty"] == pytest.approx(0.4)
    assert pos["residual_qty"] == pytest.approx(0.4)
    assert pos["copy_size_usdc"] == pytest.approx(0.4 * 0.42)
    assert pos["entry_price"] == pytest.approx(0.42)     # original basis carried forward
    assert pos["reconcile_needed"] is True               # the flag
    assert pos["reconcile_reason"] == "exit_partial"
    assert pos["reconcile_ts"] == 200
    assert pos["execution_mode"] == "live"
    # complete record — carries every entry-slot field a future reconcile needs
    for k in ("condition_id", "outcome_index", "outcome", "entry_ts", "whale_usdc_size"):
        assert k in pos


def test_record_exit_fill_decrements_by_actual_not_intended(strategy):
    agent, _ = strategy
    _seed(agent)
    # held 1.0, a 0.7 fill → residual 0.3 (by ACTUAL), NOT 0 (the intended full close).
    assert agent.record_exit_fill(_exit_proposal(), _fe(0.7, 0.55)) == pytest.approx(0.3)


def test_record_exit_fill_full_clear_stays_popped(strategy):
    agent, _ = strategy
    _seed(agent)                                         # slot already popped (Phase A)
    assert agent.record_exit_fill(_exit_proposal(), _fe(1.0, 0.55)) == 0.0
    assert _pos(agent) is None                           # full exit → stays popped


def test_record_exit_fill_none_fill_retains_whole_lot(strategy):
    agent, _ = strategy
    _seed(agent)
    residual = agent.record_exit_fill(_exit_proposal(), None)   # total no-fill
    assert residual == pytest.approx(1.0)
    pos = _pos(agent)
    assert pos["reconcile_reason"] == "exit_no_fill"
    assert pos["actual_fill_qty"] == pytest.approx(1.0)
    assert pos["reconcile_needed"] is True


def test_record_exit_fill_residual_below_eps_stays_popped(strategy):
    agent, _ = strategy
    _seed(agent)
    # held 1.0, fill 0.9995 → residual 0.0005 ≤ EPS (1e-3) → treated as full exit
    assert agent.record_exit_fill(_exit_proposal(), _fe(0.9995, 0.55)) == 0.0
    assert _pos(agent) is None


def test_record_exit_fill_unlocatable_returns_zero(strategy):
    agent, _ = strategy
    order = ProposedOrder(strategy="s", symbol="x:Yes", side="sell", qty=1.0,
                          extra={"is_entry": False})   # no wallet/condition_id
    assert agent.record_exit_fill(order, _fe(0.5, 0.5)) == 0.0


# ── single-shot exit reconcile (the OP·E-live path: chase OFF) ───────────────


@pytest.mark.asyncio
async def test_single_shot_exit_partial_retains_flagged(strategy, monkeypatch):
    """OP·E-live path. A LIVE exit with exit_chase DISABLED goes through the SINGLE-SHOT
    place_order_fak_synth (the gate short-circuits — _run_exit_chase is NOT entered), and
    the is_entry-gated reconcile FLOOR still retains + flags a partial. Proves the
    reconcile works on the simple single-shot exit that is actually live at the shakedown,
    not only on the chase cumulative (Deviation #2: the reconcile floor is on at the 3B
    cutover, independent of the chase)."""
    agent, _ = strategy
    order = _exit_proposal()                       # held = 0.42 / 0.42 = 1.0

    # ── (A) gate OFF: exit_chase unset → single-shot path, chase NOT entered ──
    b = PolymarketLiveBroker(private_key="0xk", funder_address="0xf",
                             polygon_rpc_url="http://rpc")     # NO exit_chase
    assert b._exit_chase is None
    b._clob = MagicMock()
    b._connected = True
    single_shot = AsyncMock(return_value=_fe(0.4, 0.55))       # ONE partial FillEvent
    monkeypatch.setattr(pl, "_place_order_fak_synth_fn", single_shot)
    chase = AsyncMock()
    monkeypatch.setattr(PolymarketLiveBroker, "_run_exit_chase", chase)

    fill = await b.place_order(order)
    chase.assert_not_awaited()                                 # the chase loop was NOT entered
    single_shot.assert_awaited_once_with(b._clob, order, poll_seconds=b._fak_poll_seconds)
    assert fill.qty == pytest.approx(0.4)

    # ── (B) reconcile FLOOR retains the partial (is_entry-gated, not chase-gated) ──
    _seed(agent)
    residual = agent.record_exit_fill(order, fill)
    assert residual == pytest.approx(0.6)
    pos = _pos(agent)
    assert pos is not None                                     # RETAINED, not popped
    assert pos["actual_fill_qty"] == pytest.approx(0.6)
    assert pos["residual_qty"] == pytest.approx(0.6)
    assert pos["reconcile_needed"] is True
    assert pos["reconcile_reason"] == "exit_partial"


@pytest.mark.asyncio
async def test_single_shot_exit_full_pops(strategy, monkeypatch):
    """Boundary: a single-shot exit (chase OFF) that fills the WHOLE lot → slot stays
    popped, no reconcile flag."""
    agent, _ = strategy
    order = _exit_proposal()                       # held = 1.0
    b = PolymarketLiveBroker(private_key="0xk", funder_address="0xf",
                             polygon_rpc_url="http://rpc")
    assert b._exit_chase is None
    b._clob = MagicMock()
    b._connected = True
    monkeypatch.setattr(pl, "_place_order_fak_synth_fn",
                        AsyncMock(return_value=_fe(1.0, 0.55)))
    chase = AsyncMock()
    monkeypatch.setattr(PolymarketLiveBroker, "_run_exit_chase", chase)

    fill = await b.place_order(order)
    chase.assert_not_awaited()                                 # single-shot, no chase
    assert fill.qty == pytest.approx(1.0)

    _seed(agent)
    assert agent.record_exit_fill(order, fill) == 0.0
    assert _pos(agent) is None                                 # full single-shot exit → popped


# ── central invariant (end-to-end via the scan + Phase-A pop) ────────────────


class _StubDataAPI:
    def __init__(self, by_wallet):
        self._by = by_wallet

    async def fetch_activity(self, wallet, *, limit=20, offset=0):
        return list(self._by.get(wallet, []))


def _act(condition_id, outcome_index, side="BUY", price=0.5, size=100.0, ts=1000,
         asset="TID"):
    return ActivityRow(
        proxy_wallet="0xW", timestamp=ts, condition_id=condition_id, type="TRADE",
        size=size, usdc_size=size * price, transaction_hash=f"tx-{condition_id}-{side}-{ts}",
        price=price, asset=asset, side=side, outcome_index=outcome_index,
        title="t", slug="", event_slug="",
        outcome="Yes" if outcome_index == 0 else "No", name="alice",
    )


async def _entry_then_sell(agent, db_url, *, cid="cidX", entry_price=0.42, fill_qty=1.0):
    """Cold-start → BUY entry → record_entry_fill (the real held lot) → whale SELL
    scan. Returns the exit ProposedOrder; the slot has been popped by Phase A (:336)."""
    set_agent_state("polymarket_copy_trader", "selected_whales",
                    [{"wallet": "0xW", "user_name": "alice"}], db_url=db_url)
    await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": []}))   # cold start
    entry_orders = await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": [
        _act(cid, 0, price=entry_price, size=1250, ts=2000),
    ]}))
    agent.record_entry_fill(entry_orders[0], _fe(fill_qty, entry_price, symbol=entry_orders[0].symbol))
    exit_orders = await agent.run_scan_cycle(data_api_client=_StubDataAPI({"0xW": [
        _act(cid, 0, price=0.55, size=1250, ts=3000, side="SELL"),
    ]}))
    assert len(exit_orders) == 1 and exit_orders[0].side == "sell"
    return exit_orders[0]


@pytest.mark.asyncio
async def test_central_invariant_partial_exit_retains_flagged(strategy):
    """MANDATORY (half 1): a cumulative exit < held → RETAINED + flagged, never popped
    while residual > EPS. Exercises the FULL chain: entry → record_entry_fill → whale
    SELL scan (Phase-A pop at :336) → record_exit_fill (Phase-B reconcile)."""
    agent, db_url = strategy
    exit_order = await _entry_then_sell(agent, db_url, fill_qty=1.0)
    assert _pos(agent) is None                                  # Phase A popped it
    residual = agent.record_exit_fill(exit_order, _fe(0.6, 0.55))
    assert residual == pytest.approx(0.4)
    pos = _pos(agent)
    assert pos is not None and pos["reconcile_needed"] is True   # RETAINED + flagged
    assert pos["actual_fill_qty"] == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_central_invariant_full_exit_pops(strategy):
    """MANDATORY (half 2): a cumulative exit == held → stays POPPED (full exit)."""
    agent, db_url = strategy
    exit_order = await _entry_then_sell(agent, db_url, fill_qty=1.0)
    assert agent.record_exit_fill(exit_order, _fe(1.0, 0.55)) == 0.0
    assert _pos(agent) is None                                  # full exit → popped


# ── handler arms (MagicMock agent; mirror E2·6 _mocks) ───────────────────────


def _mocks():
    agent = MagicMock()
    agent.name = "polymarket_copy_trader"
    agent.division = "polymarket_copy_trading"
    data_exec = MagicMock()
    logger_agent = MagicMock()
    channel = MagicMock()
    channel.push = AsyncMock()
    verdict = SimpleNamespace(verdict="approve", reason="")
    return agent, data_exec, logger_agent, channel, verdict


def _logged_kinds(logger_agent):
    return [c.args[1] for c in logger_agent.log_event.call_args_list]


def _live_exit_order():
    return ProposedOrder(
        strategy="polymarket_copy_trader", symbol="cidX:Yes", side="sell",
        qty=1.0, order_type="market", limit_price=0.55,
        extra={"is_entry": False, "whale_wallet": "0xW", "condition_id": "cidX",
               "outcome_index": 0, "market_title": "M", "copy_size_usdc": 0.42,
               "implied_prob_at_entry": 0.42, "whale_user_name": "alice"},
    )


def _live_entry_order():
    return ProposedOrder(
        strategy="polymarket_copy_trader", symbol="cidX:Yes", side="buy",
        qty=2.5, order_type="market", limit_price=0.40,
        extra={"is_entry": True, "whale_wallet": "0xW", "condition_id": "cidX",
               "outcome_index": 0, "market_title": "M", "copy_size_usdc": 1.0,
               "whale_user_name": "alice"},
    )


@pytest.mark.asyncio
async def test_handler_exit_partial_reconciles_and_flags():
    agent, data_exec, logger_agent, channel, verdict = _mocks()
    order = _live_exit_order()
    fill = _fe(0.6, 0.55)
    data_exec.place = AsyncMock(return_value=fill)
    agent.record_exit_fill = MagicMock(return_value=0.4)
    await _handle_copy_order_placement(
        agent=agent, order=order, verdict=verdict, is_live_armed=True,
        data_exec=data_exec, logger_agent=logger_agent, channel=channel, base_payload={},
    )
    agent.record_exit_fill.assert_called_once_with(order, fill)
    agent.record_entry_fill.assert_not_called()
    assert "polymarket_copy_exit_residual" in _logged_kinds(logger_agent)


@pytest.mark.asyncio
async def test_handler_exit_full_fill_no_residual_no_flag():
    agent, data_exec, logger_agent, channel, verdict = _mocks()
    order = _live_exit_order()
    fill = _fe(1.0, 0.55)
    data_exec.place = AsyncMock(return_value=fill)
    agent.record_exit_fill = MagicMock(return_value=0.0)    # full exit
    await _handle_copy_order_placement(
        agent=agent, order=order, verdict=verdict, is_live_armed=True,
        data_exec=data_exec, logger_agent=logger_agent, channel=channel, base_payload={},
    )
    agent.record_exit_fill.assert_called_once_with(order, fill)
    assert "polymarket_copy_exit_residual" not in _logged_kinds(logger_agent)


@pytest.mark.asyncio
async def test_handler_exit_total_nofill_retains_not_discards():
    agent, data_exec, logger_agent, channel, verdict = _mocks()
    order = _live_exit_order()
    data_exec.place = AsyncMock(side_effect=NoFillInWindow("did not fill"))
    agent.record_exit_fill = MagicMock(return_value=1.0)
    # returns NORMALLY (no raise) — benign, loop continues
    await _handle_copy_order_placement(
        agent=agent, order=order, verdict=verdict, is_live_armed=True,
        data_exec=data_exec, logger_agent=logger_agent, channel=channel, base_payload={},
    )
    agent.record_exit_fill.assert_called_once_with(order, None)   # whole lot retained
    agent.discard_entry.assert_not_called()                        # exits are NOT discarded
    kinds = _logged_kinds(logger_agent)
    assert "polymarket_copy_exit_residual" in kinds
    assert "polymarket_copy_no_fill" not in kinds                  # exit path, not the entry no_fill


@pytest.mark.asyncio
async def test_handler_entry_real_fill_unchanged():
    # E2·6 byte-identical: entry real fill → record_entry_fill, NEVER record_exit_fill.
    agent, data_exec, logger_agent, channel, verdict = _mocks()
    order = _live_entry_order()
    fill = _fe(1.0, 0.42)
    data_exec.place = AsyncMock(return_value=fill)
    agent.record_exit_fill = MagicMock()
    await _handle_copy_order_placement(
        agent=agent, order=order, verdict=verdict, is_live_armed=True,
        data_exec=data_exec, logger_agent=logger_agent, channel=channel, base_payload={},
    )
    agent.record_entry_fill.assert_called_once_with(order, fill)
    agent.record_exit_fill.assert_not_called()


@pytest.mark.asyncio
async def test_handler_entry_nofill_unchanged_discards():
    # E2·6 byte-identical: entry no-fill → discard_entry + no_fill audit, NOT exit path.
    agent, data_exec, logger_agent, channel, verdict = _mocks()
    order = _live_entry_order()
    data_exec.place = AsyncMock(side_effect=NoFillInWindow("did not fill"))
    agent.record_exit_fill = MagicMock()
    await _handle_copy_order_placement(
        agent=agent, order=order, verdict=verdict, is_live_armed=True,
        data_exec=data_exec, logger_agent=logger_agent, channel=channel, base_payload={},
    )
    agent.discard_entry.assert_called_once_with(order)
    agent.record_exit_fill.assert_not_called()
    assert "polymarket_copy_no_fill" in _logged_kinds(logger_agent)


# ── paper byte-identical: a paper exit NEVER reconciles ──────────────────────


@pytest.mark.asyncio
async def test_paper_exit_never_reconciles():
    agent, data_exec, logger_agent, channel, verdict = _mocks()
    data_exec.place = AsyncMock()
    agent.record_exit_fill = MagicMock()
    order = _live_exit_order()
    await _handle_copy_order_placement(
        agent=agent, order=order, verdict=verdict, is_live_armed=False,    # PAPER
        data_exec=data_exec, logger_agent=logger_agent, channel=channel, base_payload={},
    )
    data_exec.place.assert_not_awaited()                # paper NEVER places (returns at :3428)
    agent.record_exit_fill.assert_not_called()          # … so NEVER reconciles
    agent.record_entry_fill.assert_not_called()
    assert "would_have_placed" in _logged_kinds(logger_agent)
