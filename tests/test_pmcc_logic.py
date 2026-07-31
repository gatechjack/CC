"""PMCC division logic tests — no real API calls, no LLM calls."""
from __future__ import annotations

import pytest
from datetime import date, timedelta
from pathlib import Path

from trading_corp.agents.divisions.pmcc_robinhood import (
    PMCCAgent,
    PMCCPosition,
    _select_leap_strike,
    _select_weekly_strike,
)
from trading_corp.brokers.base import AccountSnapshot, Broker
from trading_corp.persistence.models import FillEvent, Position, ProposedOrder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _future(days: int) -> str:
    """ISO date string N days from today — keeps tests time-independent."""
    return (date.today() + timedelta(days=days)).isoformat()


def _call(strike: float, delta: float, mark: float, dte: int = 14) -> dict:
    return {
        "strike_price": strike,
        "delta": delta,
        "mark_price": mark,
        "bid": round(mark - 0.10, 2),
        "ask": round(mark + 0.10, 2),
        "dte": dte,
        "option_id": f"opt_{strike}_{dte}",
    }


def _opt_position(
    symbol: str,
    expiry_days: int,
    strike: float,
    qty: float,         # positive = long, negative = short
    delta: float = 0.50,
    avg_price: float = 1.00,
    mark_price: float | None = None,
) -> dict:
    return {
        "chain_symbol": symbol,
        "option_type": "call",
        "expiration_date": _future(expiry_days),
        "strike_price": strike,
        "quantity": qty,
        "avg_price": avg_price,
        "delta": delta,
        "mark_price": mark_price,
        "dte": expiry_days,
        "option_id": f"opt_{symbol}_{expiry_days}_{strike}",
    }


def _stock_pos(symbol: str, qty: float = 100.0, avg_price: float = 150.0) -> Position:
    return Position(account="mock", symbol=symbol, qty=qty, avg_price=avg_price, opened_ts="")


# ---------------------------------------------------------------------------
# Mock broker
# ---------------------------------------------------------------------------

class MockOptionBroker(Broker):
    """Minimal broker satisfying both Broker and OptionBroker protocols."""
    name = "mock"
    paper = True

    def __init__(
        self,
        option_positions: list[dict] | None = None,
        stock_positions: list[Position] | None = None,
        equity: float = 100_000.0,
        expiry_dates: dict[str, list[str]] | None = None,
        calls: dict[tuple[str, str], list[dict]] | None = None,
    ) -> None:
        self._option_positions = option_positions or []
        self._stock_positions = stock_positions or []
        self._equity = equity
        self._expiry_dates = expiry_dates or {}
        self._calls = calls or {}

    async def connect(self) -> None: pass
    async def disconnect(self) -> None: pass
    async def quote(self, symbol: str) -> float: return 150.0
    async def cancel_order(self, order_id: str) -> bool: return True

    async def snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            account="mock",
            equity=self._equity,
            buying_power=self._equity * 0.5,
            cash=self._equity * 0.5,
            positions=self._stock_positions,
        )

    async def place_order(self, order: ProposedOrder) -> FillEvent:
        from datetime import datetime, timezone
        return FillEvent(
            order_id=order.id, symbol=order.symbol, side=order.side,
            qty=order.qty, price=order.limit_price or 0.0,
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            venue="mock",
        )

    # OptionBroker protocol
    async def get_option_positions_detail(self) -> list[dict]:
        return self._option_positions

    async def get_expiration_dates(self, symbol: str) -> list[str]:
        return self._expiry_dates.get(symbol, [])

    async def get_calls_for_expiry(self, symbol: str, expiry: str) -> list[dict]:
        return self._calls.get((symbol, expiry), [])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def strategies_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "strategies.yaml"
    p.write_text(
        """
robinhood_pmcc:
  enabled: true
  auto_execute: false
  universe_source: positions
  watchlist: []
  position_exclude: []
  position_min_shares: 1
""".strip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def risk_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "risk.yaml"
    p.write_text(
        """
global:
  per_trade_risk_pct: 0.015
  per_strategy_daily_loss_pct: 0.03
  per_account_max_drawdown_pct: 0.15
  correlation_cap: 0.7
  target_annualized_vol: 0.25
trend_alignment:
  counter_trend_size_multiplier: 0.5
pmcc:
  contracts_per_25k_equity: 1
  short_call_roll_dte: 21
  short_call_roll_profit_pct: 0.50
  long_call_min_dte: 365
  long_call_min_delta: 0.80
  short_call_target_delta: 0.30
overrides: {}
""".strip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def agent(strategies_yaml: Path, risk_yaml: Path) -> PMCCAgent:
    return PMCCAgent(strategies_yaml=strategies_yaml, risk_yaml=risk_yaml)


# ---------------------------------------------------------------------------
# Strike selection helpers
# ---------------------------------------------------------------------------

def test_select_leap_picks_deepest_qualifying_itm():
    calls = [
        _call(strike=130, delta=0.90, mark=25.0),
        _call(strike=140, delta=0.85, mark=18.0),
        _call(strike=160, delta=0.72, mark=10.0),  # delta too low
    ]
    best = _select_leap_strike(calls)
    assert best is not None
    assert best["strike_price"] == 130   # deepest ITM among qualifying


def test_select_leap_fallback_when_no_delta_threshold():
    calls = [
        _call(strike=160, delta=0.65, mark=10.0),
        _call(strike=170, delta=0.55, mark=7.0),
    ]
    best = _select_leap_strike(calls)
    assert best is not None
    assert best["delta"] == 0.65   # highest delta fallback


def test_select_weekly_picks_closest_to_target():
    calls = [
        _call(strike=165, delta=0.40, mark=3.0),  # too high delta
        _call(strike=170, delta=0.30, mark=1.80),  # perfect
        _call(strike=175, delta=0.20, mark=0.90),
    ]
    best = _select_weekly_strike(calls, target_delta=0.30)
    assert best is not None
    assert best["strike_price"] == 170


def test_select_weekly_avoids_itm():
    calls = [
        _call(strike=150, delta=0.55, mark=6.0),   # ITM-ish, delta >= 0.40
        _call(strike=155, delta=0.42, mark=4.0),   # borderline
        _call(strike=165, delta=0.28, mark=1.50),  # good
    ]
    best = _select_weekly_strike(calls, target_delta=0.30)
    assert best is not None
    assert best["delta"] < 0.40


# ---------------------------------------------------------------------------
# detect_existing_legs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_no_legs_empty_positions(agent: PMCCAgent):
    broker = MockOptionBroker(option_positions=[])
    legs = await agent.detect_existing_legs(broker)
    assert legs == []


@pytest.mark.asyncio
async def test_detect_uncovered_leap(agent: PMCCAgent):
    """Long LEAP call with no short → PMCCPosition with short_leg=None."""
    positions = [_opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0, delta=0.82)]
    broker = MockOptionBroker(option_positions=positions)
    legs = await agent.detect_existing_legs(broker)
    assert len(legs) == 1
    leg = legs[0]
    assert leg.symbol == "AAPL"
    assert leg.long_leg_strike == 150.0
    assert leg.short_leg_expiry is None
    assert leg.short_leg_symbol is None


@pytest.mark.asyncio
async def test_detect_paired_pmcc(agent: PMCCAgent):
    """Long LEAP + short weekly → fully paired PMCCPosition with PnL calc."""
    positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0,
                      delta=0.82, avg_price=22.50),
        _opt_position("AAPL", expiry_days=10,  strike=175.0, qty=-1.0,
                      delta=0.28, avg_price=1.45, mark_price=0.72),
    ]
    broker = MockOptionBroker(option_positions=positions)
    legs = await agent.detect_existing_legs(broker)
    assert len(legs) == 1
    leg = legs[0]
    assert leg.long_leg_strike == 150.0
    assert leg.short_leg_strike == 175.0
    assert leg.short_leg_pnl_pct is not None
    assert abs(leg.short_leg_pnl_pct - (1 - 0.72 / 1.45)) < 0.001


@pytest.mark.asyncio
async def test_detect_multiple_underlyings(agent: PMCCAgent):
    positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0, delta=0.82),
        _opt_position("NVDA", expiry_days=380, strike=500.0, qty=1.0, delta=0.81),
        _opt_position("NVDA", expiry_days=12,  strike=600.0, qty=-1.0, delta=0.29),
    ]
    broker = MockOptionBroker(option_positions=positions)
    legs = await agent.detect_existing_legs(broker)
    symbols = {leg.symbol for leg in legs}
    assert symbols == {"AAPL", "NVDA"}


@pytest.mark.asyncio
async def test_detect_skips_puts(agent: PMCCAgent):
    """Put positions should be ignored by PMCC detector."""
    pos = _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0)
    pos["option_type"] = "put"
    broker = MockOptionBroker(option_positions=[pos])
    legs = await agent.detect_existing_legs(broker)
    assert legs == []


# ---------------------------------------------------------------------------
# Roll conditions
# ---------------------------------------------------------------------------

def _pmcc_with_short(dte: int, pnl_pct: float) -> PMCCPosition:
    return PMCCPosition(
        symbol="AAPL",
        long_leg_expiry=_future(400), long_leg_strike=150.0,
        long_leg_delta=0.82, long_leg_dte=400, long_leg_qty=1.0,
        long_leg_avg_price=22.50, long_leg_symbol="AAPL ... C 150.00",
        short_leg_expiry=_future(dte), short_leg_strike=175.0,
        short_leg_dte=dte, short_leg_pnl_pct=pnl_pct,
        short_leg_qty=-1.0, short_leg_mark=1.45 * (1 - pnl_pct),
        short_leg_avg_price=1.45, short_leg_symbol="AAPL ... C 175.00",
    )


def test_should_roll_at_21_dte(agent: PMCCAgent):
    assert agent._should_roll(_pmcc_with_short(dte=21, pnl_pct=0.10)) is True


def test_should_roll_below_21_dte(agent: PMCCAgent):
    assert agent._should_roll(_pmcc_with_short(dte=5, pnl_pct=0.10)) is True


def test_should_roll_at_50pct_profit(agent: PMCCAgent):
    assert agent._should_roll(_pmcc_with_short(dte=30, pnl_pct=0.50)) is True


def test_no_roll_when_healthy(agent: PMCCAgent):
    assert agent._should_roll(_pmcc_with_short(dte=30, pnl_pct=0.20)) is False


# ---------------------------------------------------------------------------
# scan — integration with mock broker
# ---------------------------------------------------------------------------

def _broker_with_chains(
    stock_syms: list[str],
    opt_positions: list[dict],
    equity: float = 100_000.0,
) -> MockOptionBroker:
    """Build a mock broker that returns a LEAP chain and a weekly chain for each stock symbol."""
    leap_expiry = _future(400)
    weekly_expiry = _future(14)
    stock_positions = [_stock_pos(s) for s in stock_syms]
    expiry_dates = {s: [_future(14), _future(400)] for s in stock_syms}
    calls = {}
    for s in stock_syms:
        calls[(s, leap_expiry)] = [_call(130.0, 0.85, 25.0, dte=400)]
        calls[(s, weekly_expiry)] = [_call(175.0, 0.28, 1.50, dte=14)]
    return MockOptionBroker(
        option_positions=opt_positions,
        stock_positions=stock_positions,
        equity=equity,
        expiry_dates=expiry_dates,
        calls=calls,
    )


@pytest.mark.asyncio
async def test_scan_empty_universe_no_orders(agent: PMCCAgent):
    broker = MockOptionBroker()   # no positions
    orders = await agent.scan(broker)
    assert orders == []


@pytest.mark.asyncio
async def test_scan_proposes_open_pmcc_for_stock(agent: PMCCAgent):
    """Stock held, no PMCC yet → propose LEAP buy + weekly sell (2 orders)."""
    broker = _broker_with_chains(stock_syms=["AAPL"], opt_positions=[])
    orders = await agent.scan(broker)
    assert len(orders) == 2
    actions = {o.extra["action"] for o in orders}
    assert "open_leap" in actions
    assert "open_short_call" in actions
    # All orders must have is_option=True and correct underlying
    for o in orders:
        assert o.extra["is_option"] is True
        assert o.extra["underlying"] == "AAPL"
        assert o.extra["option_type"] == "call"


@pytest.mark.asyncio
async def test_scan_open_pmcc_orders_share_pair_id(agent: PMCCAgent):
    """LEAP + weekly from same setup share a pmcc_pair_id for board grouping."""
    broker = _broker_with_chains(stock_syms=["AAPL"], opt_positions=[])
    orders = await agent.scan(broker)
    pair_ids = {o.extra.get("pmcc_pair_id") for o in orders}
    assert len(pair_ids) == 1
    assert None not in pair_ids


@pytest.mark.asyncio
async def test_scan_proposes_weekly_for_uncovered_leap(agent: PMCCAgent):
    """Existing uncovered LEAP → one sell order, no LEAP purchase."""
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0, delta=0.82),
    ]
    broker = _broker_with_chains(stock_syms=["AAPL"], opt_positions=opt_positions)
    orders = await agent.scan(broker)
    assert len(orders) == 1
    assert orders[0].side == "sell"
    assert orders[0].extra["action"] == "open_short_call"


@pytest.mark.asyncio
async def test_scan_proposes_roll_at_21_dte(agent: PMCCAgent):
    """Existing PMCC with short call at 21 DTE → buy-to-close + new sell (2 orders)."""
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0,
                      delta=0.82, avg_price=22.50),
        _opt_position("AAPL", expiry_days=21,  strike=175.0, qty=-1.0,
                      delta=0.28, avg_price=1.45, mark_price=1.20),
    ]
    broker = _broker_with_chains(stock_syms=["AAPL"], opt_positions=opt_positions)
    orders = await agent.scan(broker)
    assert len(orders) == 2
    actions = {o.extra["action"] for o in orders}
    assert "roll_short_call_close" in actions
    assert "roll_short_call_open" in actions
    close_order = next(o for o in orders if o.extra["action"] == "roll_short_call_close")
    assert close_order.side == "buy"
    assert close_order.extra["position_effect"] == "close"


@pytest.mark.asyncio
async def test_scan_proposes_roll_at_50pct_profit(agent: PMCCAgent):
    """50% credit captured → roll triggered even with healthy DTE."""
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0, delta=0.82),
        _opt_position("AAPL", expiry_days=30,  strike=175.0, qty=-1.0,
                      delta=0.28, avg_price=1.45, mark_price=0.72),   # ~50.3% captured
    ]
    broker = _broker_with_chains(stock_syms=["AAPL"], opt_positions=opt_positions)
    orders = await agent.scan(broker)
    actions = {o.extra["action"] for o in orders}
    assert "roll_short_call_close" in actions


@pytest.mark.asyncio
async def test_scan_no_action_for_healthy_pmcc(agent: PMCCAgent):
    """Healthy PMCC (DTE=30, PnL=20%) → no orders."""
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0, delta=0.82),
        _opt_position("AAPL", expiry_days=30,  strike=175.0, qty=-1.0,
                      delta=0.28, avg_price=1.45, mark_price=1.16),   # ~20% captured
    ]
    broker = _broker_with_chains(stock_syms=["AAPL"], opt_positions=opt_positions)
    orders = await agent.scan(broker)
    assert orders == []


@pytest.mark.asyncio
async def test_scan_excludes_position_exclude(strategies_yaml: Path, risk_yaml: Path):
    """Symbols in position_exclude are skipped even if held."""
    strategies_yaml.write_text(
        """
robinhood_pmcc:
  enabled: true
  auto_execute: false
  universe_source: positions
  watchlist: []
  position_exclude: [AAPL]
  position_min_shares: 1
""".strip(),
        encoding="utf-8",
    )
    agent = PMCCAgent(strategies_yaml=strategies_yaml, risk_yaml=risk_yaml)
    broker = _broker_with_chains(stock_syms=["AAPL"], opt_positions=[])
    orders = await agent.scan(broker)
    assert orders == []


@pytest.mark.asyncio
async def test_scan_watchlist_mode(strategies_yaml: Path, risk_yaml: Path):
    """universe_source=watchlist uses config list instead of live positions."""
    strategies_yaml.write_text(
        """
robinhood_pmcc:
  enabled: true
  auto_execute: false
  universe_source: watchlist
  watchlist: [NVDA]
  position_exclude: []
  position_min_shares: 1
""".strip(),
        encoding="utf-8",
    )
    agent = PMCCAgent(strategies_yaml=strategies_yaml, risk_yaml=risk_yaml)
    # No stock position for NVDA, but watchlist forces it into universe.
    # scan() skips "open PMCC" when no stock position is held — so 0 orders.
    broker = MockOptionBroker(
        option_positions=[],
        stock_positions=[],   # no NVDA stock → can't open PMCC
        expiry_dates={"NVDA": [_future(14), _future(400)]},
    )
    orders = await agent.scan(broker)
    # No stock position → can't size → 0 orders proposed
    assert orders == []


@pytest.mark.asyncio
async def test_order_strategy_tag(agent: PMCCAgent):
    """All proposed orders must be tagged with the correct strategy name."""
    broker = _broker_with_chains(stock_syms=["AAPL"], opt_positions=[])
    orders = await agent.scan(broker)
    for order in orders:
        assert order.strategy == "robinhood_pmcc"


# ---------------------------------------------------------------------------
# get_universe — fallback to option underlyings when no stock positions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_universe_falls_back_to_long_calls_when_no_stocks(agent: PMCCAgent):
    """Options-only account: universe derived from long call underlyings."""
    opt_positions = [
        _opt_position("NVDA", expiry_days=400, strike=500.0, qty=1.0, delta=0.82),
        _opt_position("AAPL", expiry_days=380, strike=150.0, qty=1.0, delta=0.85),
        _opt_position("AAPL", expiry_days=14,  strike=175.0, qty=-1.0, delta=0.28),
    ]
    broker = MockOptionBroker(
        option_positions=opt_positions,
        stock_positions=[],   # no stocks
        equity=100_000.0,
    )
    universe = await agent.get_universe(broker)
    assert set(universe) == {"NVDA", "AAPL"}


@pytest.mark.asyncio
async def test_universe_fallback_ignores_puts(agent: PMCCAgent):
    """Put options should not contribute to the fallback universe."""
    opt_positions = [
        _opt_position("SPY", expiry_days=400, strike=500.0, qty=1.0, delta=0.82),
    ]
    opt_positions[0]["option_type"] = "put"   # make it a put
    broker = MockOptionBroker(
        option_positions=opt_positions,
        stock_positions=[],
        equity=100_000.0,
    )
    universe = await agent.get_universe(broker)
    assert universe == []


@pytest.mark.asyncio
async def test_universe_fallback_ignores_short_positions(agent: PMCCAgent):
    """Short option positions should not seed the universe (only long calls)."""
    opt_positions = [
        _opt_position("TSLA", expiry_days=14, strike=300.0, qty=-1.0, delta=0.30),
    ]
    broker = MockOptionBroker(
        option_positions=opt_positions,
        stock_positions=[],
        equity=100_000.0,
    )
    universe = await agent.get_universe(broker)
    assert universe == []


@pytest.mark.asyncio
async def test_scan_rolls_existing_pmcc_in_options_only_account(agent: PMCCAgent):
    """Options-only account with a PMCC needing a roll → 2 roll orders."""
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0,
                      delta=0.82, avg_price=22.50),
        _opt_position("AAPL", expiry_days=21,  strike=175.0, qty=-1.0,
                      delta=0.28, avg_price=1.45, mark_price=1.20),
    ]
    leap_expiry = _future(400)
    weekly_expiry = _future(14)
    broker = MockOptionBroker(
        option_positions=opt_positions,
        stock_positions=[],   # options-only account
        equity=100_000.0,
        expiry_dates={"AAPL": [_future(14), _future(400)]},
        calls={
            ("AAPL", leap_expiry): [_call(130.0, 0.85, 25.0, dte=400)],
            ("AAPL", weekly_expiry): [_call(175.0, 0.28, 1.50, dte=14)],
        },
    )
    orders = await agent.scan(broker)
    actions = {o.extra["action"] for o in orders}
    assert "roll_short_call_close" in actions
    assert "roll_short_call_open" in actions
