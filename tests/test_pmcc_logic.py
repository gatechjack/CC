"""PMCC division logic tests — no real API calls, no LLM calls."""
from __future__ import annotations

import pytest
from datetime import date, timedelta
from pathlib import Path

from trading_corp.agents.divisions.pmcc_robinhood import (
    PMCCAgent,
    PMCCAnalysis,
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
    # open_interest + volume populated so the test chains pass
    # _passes_liquidity (default min OI=100, min vol=50, max spread=10%).
    # Bid/ask is mark ±0.05 (not ±0.10) so low-mark weeklies (~$1.50)
    # stay inside the 10% spread cap.
    return {
        "strike_price": strike,
        "delta": delta,
        "mark_price": mark,
        "bid": round(mark - 0.05, 2),
        "ask": round(mark + 0.05, 2),
        "dte": dte,
        "open_interest": 5000,
        "volume": 1000,
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


@pytest.fixture
def clear_earnings(monkeypatch):
    """B9 (2026-07-21): pin the earnings gate to CLEAR (a far-future earnings date,
    well outside the buffer) so roll-MECHANICS tests assert roll behavior without
    coupling to the LIVE earnings calendar — `_earnings_gate_state` →
    `get_next_earnings` hits yfinance, which would otherwise flake a roll test ~4×/yr
    if that real symbol happened to have earnings within the buffer at run time.
    Scoped to roll tests that request it; the OPEN-path tests are intentionally left
    untouched. The dedicated B9 tests below patch `get_next_earnings` directly to
    inject a within-buffer date / None instead of using this fixture."""
    from datetime import datetime, timezone, timedelta as _td
    monkeypatch.setattr(
        "trading_corp.utils.market_data.get_next_earnings",
        lambda symbol, *a, **k: datetime.now(timezone.utc) + _td(days=90),
    )


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
    """Build a mock broker that returns a LEAP chain and TWO weekly chains for
    each stock symbol.

    B7 (2026-07-21) added a second, strictly-later weekly at 35 DTE alongside the
    original 14-DTE weekly. Rationale: several roll tests here hold a short at
    14–30 DTE and expect a roll to be PROPOSED. Under B7's roll-out rule the new
    short must expire strictly later than the current short, so a chain whose only
    weekly is 14 DTE cannot satisfy a roll of a 14–30-DTE short. The 35-DTE weekly
    is the roll-out target; the 14-DTE weekly is retained so the OPEN path (which
    picks the nearest in-window weekly, after_dte=None) behaves exactly as before.
    Both weeklies share strike 175 / 0.28 delta so no test's strike assertion
    shifts — only a later expiry is now available for rolls."""
    leap_expiry = _future(400)
    weekly_expiry = _future(14)
    rollout_weekly_expiry = _future(35)  # B7: strictly-later roll-out target
    stock_positions = [_stock_pos(s) for s in stock_syms]
    expiry_dates = {s: [_future(14), _future(35), _future(400)] for s in stock_syms}
    calls = {}
    for s in stock_syms:
        calls[(s, leap_expiry)] = [_call(130.0, 0.85, 25.0, dte=400)]
        calls[(s, weekly_expiry)] = [_call(175.0, 0.28, 1.50, dte=14)]
        calls[(s, rollout_weekly_expiry)] = [_call(175.0, 0.28, 1.50, dte=35)]
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
async def test_scan_proposes_roll_at_21_dte(agent: PMCCAgent, clear_earnings):
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
async def test_scan_proposes_roll_at_50pct_profit(agent: PMCCAgent, clear_earnings):
    """50% credit captured → roll triggered even with healthy DTE."""
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0, delta=0.82),
        _opt_position("AAPL", expiry_days=30,  strike=175.0, qty=-1.0,
                      delta=0.28, avg_price=1.45, mark_price=0.72),   # ~50.3% captured
    ]
    # B7: 30-DTE short rolls OUT to the shared helper's 35-DTE weekly (the
    # 14-DTE weekly is correctly refused as a roll-IN). Intent unchanged — a
    # 50%-captured short still triggers a roll.
    broker = _broker_with_chains(stock_syms=["AAPL"], opt_positions=opt_positions)
    orders = await agent.scan(broker)
    # B7 tighten (was lenient `close in actions`): assert exact leg count +
    # both named legs so a dropped open leg can't slip through.
    assert len(orders) == 2, [o.extra.get("action") for o in orders]
    actions = [o.extra["action"] for o in orders]
    assert set(actions) == {"roll_short_call_close", "roll_short_call_open"}


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
async def test_universe_skips_hodl_crypto_positions(agent: PMCCAgent):
    """Crypto held as HODL (e.g. ETH/USD from Robinhood crypto branch
    added 2026-05-01) must NOT enter the PMCC scan universe and must
    NOT pre-empt the leg-underlyings fallback. Reproduces the
    2026-05-04→05-08 prod regression where a single ETH/USD position
    masked all 13 PMCC legs from the order-construction loop."""
    opt_positions = [
        _opt_position("ASTS", expiry_days=400, strike=30.0, qty=1.0, delta=0.85),
        _opt_position("MARA", expiry_days=400, strike=20.0, qty=1.0, delta=0.85),
    ]
    crypto_position = Position(
        account="mock",
        symbol="ETH/USD",
        qty=0.5,
        avg_price=3000.0,
        opened_ts="2026-05-01T00:00:00+00:00",
        extra={"asset_type": "crypto"},
    )
    broker = MockOptionBroker(
        option_positions=opt_positions,
        stock_positions=[crypto_position],
        equity=100_000.0,
    )
    universe = await agent.get_universe(broker)
    # Crypto excluded; falls through to long-call-underlyings branch
    assert "ETH/USD" not in universe
    assert set(universe) == {"ASTS", "MARA"}


@pytest.mark.asyncio
async def test_scan_rolls_existing_pmcc_in_options_only_account(agent: PMCCAgent, clear_earnings):
    """Options-only account with a PMCC needing a roll → 2 roll orders."""
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0,
                      delta=0.82, avg_price=22.50),
        _opt_position("AAPL", expiry_days=21,  strike=175.0, qty=-1.0,
                      delta=0.28, avg_price=1.45, mark_price=1.20),
    ]
    leap_expiry = _future(400)
    # B7 (2026-07-21): the current short is 21 DTE, so the roll target must
    # expire strictly later. Added a 35-DTE weekly (was: only a 14-DTE weekly,
    # which is a roll-IN and is now correctly refused). Intent unchanged — a
    # 21-DTE short still rolls; it just rolls OUT to 35 DTE instead of in to 14.
    rollout_weekly_expiry = _future(35)
    broker = MockOptionBroker(
        option_positions=opt_positions,
        stock_positions=[],   # options-only account
        equity=100_000.0,
        expiry_dates={"AAPL": [_future(35), _future(400)]},
        calls={
            ("AAPL", leap_expiry): [_call(130.0, 0.85, 25.0, dte=400)],
            ("AAPL", rollout_weekly_expiry): [_call(175.0, 0.28, 1.50, dte=35)],
        },
    )
    orders = await agent.scan(broker)
    # B7 tighten (was lenient `in actions` subset): assert exact leg count +
    # named actions so a future structural regression can't hide behind a
    # loose membership check.
    assert len(orders) == 2, [o.extra.get("action") for o in orders]
    actions = [o.extra["action"] for o in orders]
    assert set(actions) == {"roll_short_call_close", "roll_short_call_open"}


# ---------------------------------------------------------------------------
# Terminal-DTE wall-clock time gate (Board direction 2026-05-01)
#
# Phase 2 (2026-05-02): refactored from hardcoded 15:00/15:30 ET to
# market-calendar-aware (close - release_offset_min, close - hard_offset_min)
# + cycle-continuity release on extrinsic <= threshold. Tests below pin both
# paths and the P1 release condition.
# ---------------------------------------------------------------------------

class _FakeCalendar:
    """Test double for MarketHoursCalendar. Returns a fixed close time
    in ET for any date — simpler than mocking pandas_market_calendars.
    Set close=None to simulate a closed market day."""
    def __init__(self, close_hour: int = 16, close_minute: int = 0,
                 closed: bool = False):
        self.close_hour = close_hour
        self.close_minute = close_minute
        self.closed = closed

    def close_time_et(self, when):
        if self.closed:
            return None
        from datetime import datetime as _dt
        from trading_corp.utils.time import ET as _ET
        d = when.date() if hasattr(when, "date") else when
        return _dt(d.year, d.month, d.day, self.close_hour, self.close_minute,
                   tzinfo=_ET)


# Default fake calendar for tests: regular 4pm ET close.
_REGULAR_CAL = _FakeCalendar(close_hour=16, close_minute=0)


def _0dte_pmcc(action: str = "hold", urgency: str = "routine",
               short_leg_mark: float = 0.50) -> tuple[PMCCPosition, PMCCAnalysis]:
    """Build a (leg, analysis) pair where the short is 0 DTE inside the
    ATM zone — the canonical scenario the time gate guards.

    Default `short_leg_mark=0.50` is ABOVE the cycle-continuity threshold
    ($0.15) so time-gate tests aren't accidentally triggering the P1
    release. P1 tests pass `short_leg_mark=0.10` explicitly.
    """
    leg = PMCCPosition(
        symbol="CIFR",
        long_leg_expiry=_future(259), long_leg_strike=7.0,
        long_leg_delta=0.93, long_leg_dte=259, long_leg_qty=2.0,
        long_leg_avg_price=7.35, long_leg_symbol="CIFR ... C 7.00",
        short_leg_expiry=_future(0), short_leg_strike=18.0,
        short_leg_dte=0, short_leg_pnl_pct=0.92,
        short_leg_qty=-2.0, short_leg_mark=short_leg_mark,
        short_leg_avg_price=1.58, short_leg_symbol="CIFR ... C 18.00",
    )
    analysis = PMCCAnalysis(
        symbol="CIFR", action=action, confidence=0.93, urgency=urgency,
        summary="...", rationale="...",
    )
    return leg, analysis


def _et(hour: int, minute: int):
    """Construct a wall-clock ET datetime on a known weekday for tests.
    Uses 2026-05-01 (Friday) — a non-DST-transition date."""
    from datetime import datetime
    from trading_corp.utils.time import ET
    return datetime(2026, 5, 1, hour, minute, 0, tzinfo=ET)


def test_time_gate_no_op_before_release_threshold(agent: PMCCAgent):
    """Before close - release_offset (default 60min) the gate is
    inactive — HOLD stays HOLD even on a 0-DTE position inside the
    ATM zone. On a regular 16:00 ET close that's anything < 15:00 ET."""
    leg, analysis = _0dte_pmcc(action="hold")
    out = agent._terminal_dte_time_release(
        analysis, leg, now_et_dt=_et(14, 59), calendar=_REGULAR_CAL,
    )
    assert out.action == "hold"
    assert out.urgency == "routine"


def test_time_gate_no_op_when_dte_not_zero(agent: PMCCAgent):
    """The gate only fires on 0 DTE. A 1-DTE HOLD past the release
    threshold stays HOLD."""
    leg, analysis = _0dte_pmcc(action="hold")
    leg = PMCCPosition(**{**leg.__dict__, "short_leg_dte": 1})
    out = agent._terminal_dte_time_release(
        analysis, leg, now_et_dt=_et(15, 30), calendar=_REGULAR_CAL,
    )
    assert out.action == "hold"


def test_time_gate_no_op_when_action_already_roll(agent: PMCCAgent):
    """If the LLM already chose roll_short, the gate doesn't interfere
    (don't downgrade urgency / overwrite an already-correct decision)."""
    leg, analysis = _0dte_pmcc(action="roll_short", urgency="elevated")
    out = agent._terminal_dte_time_release(
        analysis, leg, now_et_dt=_et(15, 45), calendar=_REGULAR_CAL,
    )
    assert out.action == "roll_short"
    assert out.urgency == "elevated"   # unchanged


def test_time_gate_releases_at_close_minus_60_forces_roll_short(agent: PMCCAgent):
    """On a regular 16:00 ET close day: at 15:00 ET (= close - 60min),
    HOLD on a 0-DTE position becomes roll_short."""
    leg, analysis = _0dte_pmcc(action="hold")
    out = agent._terminal_dte_time_release(
        analysis, leg, now_et_dt=_et(15, 0), calendar=_REGULAR_CAL,
    )
    assert out.action == "roll_short"
    # Override explanation appended
    assert any("Terminal-DTE Override released" in w for w in out.warnings)


def test_time_gate_releases_in_window_release_to_hard(agent: PMCCAgent):
    """Anywhere in [release_threshold, hard_deadline) ET: HOLD/WATCH → roll_short.
    On 16:00 close that window is [15:00, 15:30)."""
    leg, analysis = _0dte_pmcc(action="watch")
    out = agent._terminal_dte_time_release(
        analysis, leg, now_et_dt=_et(15, 15), calendar=_REGULAR_CAL,
    )
    assert out.action == "roll_short"


def test_time_gate_hard_deadline_forces_close_urgent(agent: PMCCAgent):
    """At hard_deadline (close - 30min, i.e. 15:30 ET on regular days):
    action becomes close_short, urgency escalates to urgent."""
    leg, analysis = _0dte_pmcc(action="hold")
    out = agent._terminal_dte_time_release(
        analysis, leg, now_et_dt=_et(15, 30), calendar=_REGULAR_CAL,
    )
    assert out.action == "close_short"
    assert out.urgency == "urgent"
    assert any("hard deadline breached" in w for w in out.warnings)


def test_time_gate_past_hard_deadline_still_close_urgent(agent: PMCCAgent):
    """Past hard_deadline (e.g. 15:45 ET on a regular day) the
    hard-deadline branch keeps firing."""
    leg, analysis = _0dte_pmcc(action="hold")
    out = agent._terminal_dte_time_release(
        analysis, leg, now_et_dt=_et(15, 45), calendar=_REGULAR_CAL,
    )
    assert out.action == "close_short"
    assert out.urgency == "urgent"


def test_time_gate_no_op_when_analysis_or_leg_none(agent: PMCCAgent):
    """Defensive: missing analysis or leg returns the input unchanged."""
    assert agent._terminal_dte_time_release(
        None, _0dte_pmcc()[0], calendar=_REGULAR_CAL,
    ) is None
    leg, analysis = _0dte_pmcc()
    assert agent._terminal_dte_time_release(
        analysis, None, calendar=_REGULAR_CAL,
    ).action == "hold"


# ── Half-day / holiday-aware tests ──────────────────────────────────


def test_time_gate_half_day_close_shifts_thresholds_to_1pm(agent: PMCCAgent):
    """On a 13:00 ET half-day close (e.g. day after Thanksgiving), the
    release threshold is 12:00 ET and the hard deadline is 12:30 ET.
    A 12:15 HOLD must become roll_short, a 12:35 must become close_short."""
    half_day_cal = _FakeCalendar(close_hour=13, close_minute=0)
    leg, analysis = _0dte_pmcc(action="hold")

    # Before 12:00 ET — too early
    pre = agent._terminal_dte_time_release(
        analysis, leg, now_et_dt=_et(11, 59), calendar=half_day_cal,
    )
    assert pre.action == "hold"

    # 12:15 ET is in the [12:00, 12:30) release window
    rel = agent._terminal_dte_time_release(
        analysis, leg, now_et_dt=_et(12, 15), calendar=half_day_cal,
    )
    assert rel.action == "roll_short"

    # 12:35 ET is past hard_deadline
    hard = agent._terminal_dte_time_release(
        analysis, leg, now_et_dt=_et(12, 35), calendar=half_day_cal,
    )
    assert hard.action == "close_short"
    assert hard.urgency == "urgent"


def test_time_gate_no_op_on_closed_market_day(agent: PMCCAgent):
    """If the calendar reports the market is closed (holiday, weekend),
    the time gate doesn't fire even past the would-be threshold —
    there's nothing to roll on a closed day."""
    closed_cal = _FakeCalendar(closed=True)
    leg, analysis = _0dte_pmcc(action="hold")
    out = agent._terminal_dte_time_release(
        analysis, leg, now_et_dt=_et(15, 30), calendar=closed_cal,
    )
    assert out.action == "hold"


# ── P1: cycle-continuity release ────────────────────────────────────


def test_cycle_continuity_release_on_low_extrinsic(agent: PMCCAgent):
    """Mark <= cycle_continuity_extrinsic_threshold ($0.15 default) on a
    0-DTE short forces roll_short regardless of time. This is the
    P1 path: capture next-cycle premium NOW instead of waiting for the
    time gate."""
    # 11 AM ET — well before any time-gate fires. Mark 0.10 <= 0.15.
    leg, analysis = _0dte_pmcc(action="hold", short_leg_mark=0.10)
    out = agent._terminal_dte_time_release(
        analysis, leg, now_et_dt=_et(11, 0), calendar=_REGULAR_CAL,
    )
    assert out.action == "roll_short"
    assert any("Cycle-continuity release" in w for w in out.warnings)


def test_cycle_continuity_no_fire_above_threshold(agent: PMCCAgent):
    """Mark above threshold doesn't trigger the P1 release. At 11 AM
    with no time-gate fire, HOLD stays HOLD."""
    leg, analysis = _0dte_pmcc(action="hold", short_leg_mark=0.50)
    out = agent._terminal_dte_time_release(
        analysis, leg, now_et_dt=_et(11, 0), calendar=_REGULAR_CAL,
    )
    assert out.action == "hold"


def test_cycle_continuity_takes_precedence_over_time_gate(agent: PMCCAgent):
    """When BOTH conditions are true (mark<=threshold AND past
    release_threshold), P1 fires first — the warning text mentions
    cycle-continuity, not the time-gate."""
    leg, analysis = _0dte_pmcc(action="hold", short_leg_mark=0.10)
    out = agent._terminal_dte_time_release(
        analysis, leg, now_et_dt=_et(15, 15), calendar=_REGULAR_CAL,
    )
    assert out.action == "roll_short"
    # P1 wording wins
    assert any("Cycle-continuity release" in w for w in out.warnings)


def test_cycle_continuity_no_fire_when_dte_not_zero(agent: PMCCAgent):
    """Cycle-continuity is gated on short_leg_dte == 0 — same as time
    gate. A 1-DTE position with mark $0.10 should NOT roll early."""
    leg, analysis = _0dte_pmcc(action="hold", short_leg_mark=0.10)
    leg = PMCCPosition(**{**leg.__dict__, "short_leg_dte": 1})
    out = agent._terminal_dte_time_release(
        analysis, leg, now_et_dt=_et(11, 0), calendar=_REGULAR_CAL,
    )
    assert out.action == "hold"


def test_time_gate_dst_aware_uses_local_eastern_clock():
    """The release threshold must track local Eastern time across DST.
    Construct datetimes from the same wall-clock ET on either side of
    the DST boundary; the gate output must follow LOCAL ET wall-clock,
    not UTC offset.

    DST for America/New_York: EST (UTC-5) Nov→Mar, EDT (UTC-4) Mar→Nov.
    The helper computes thresholds via close_dt - timedelta — the
    arithmetic operates on tz-aware ET datetimes, so the result is
    stable across DST."""
    from datetime import datetime
    from trading_corp.utils.time import ET

    pre_dst = datetime(2026, 2, 1, 14, 59, 0, tzinfo=ET)   # EST
    post_dst = datetime(2026, 6, 1, 14, 59, 0, tzinfo=ET)  # EDT

    p = PMCCAgent.__new__(PMCCAgent)   # bare instance — no config needed
    p._cfg = {}                        # required by the helper's cfg lookup
    leg, analysis = _0dte_pmcc(action="hold")
    assert p._terminal_dte_time_release(
        analysis, leg, now_et_dt=pre_dst, calendar=_REGULAR_CAL,
    ).action == "hold"
    assert p._terminal_dte_time_release(
        analysis, leg, now_et_dt=post_dst, calendar=_REGULAR_CAL,
    ).action == "hold"

    # Same wall-clock 15:00 ET — both fire release regardless of season
    pre_dst_active = datetime(2026, 2, 1, 15, 0, 0, tzinfo=ET)
    post_dst_active = datetime(2026, 6, 1, 15, 0, 0, tzinfo=ET)
    assert p._terminal_dte_time_release(
        analysis, leg, now_et_dt=pre_dst_active, calendar=_REGULAR_CAL,
    ).action == "roll_short"
    assert p._terminal_dte_time_release(
        analysis, leg, now_et_dt=post_dst_active, calendar=_REGULAR_CAL,
    ).action == "roll_short"


# ---------------------------------------------------------------------------
# Item 2 (2026-05-02) — LEAP Hard Rule promotion
#   Promotes roll_short / roll_short_early → roll_leap when LEAP delta>=0.95
#   OR long_leg_dte<120. Standard Rule 5 / LEAP Management Rule.
# ---------------------------------------------------------------------------


def _leg_with_leap(
    *, long_delta: float = 0.85, long_dte: int = 400,
    short_dte: int = 7, short_strike: float = 175.0,
    short_mark: float = 1.50,
) -> PMCCPosition:
    """Construct a PMCCPosition with full LEAP + short, with knobs for the
    Hard-Rule and cooldown gates."""
    return PMCCPosition(
        symbol="MSTR",
        long_leg_expiry=_future(long_dte), long_leg_strike=160.0,
        long_leg_delta=long_delta, long_leg_dte=long_dte, long_leg_qty=1.0,
        long_leg_avg_price=2380.0, long_leg_symbol="MSTR ... C 160.00",
        long_leg_mark=58.05,
        short_leg_expiry=_future(short_dte), short_leg_strike=short_strike,
        short_leg_dte=short_dte, short_leg_pnl_pct=0.20,
        short_leg_qty=-1.0, short_leg_mark=short_mark,
        short_leg_avg_price=2.50, short_leg_symbol="MSTR ... C 175.00",
    )


def test_leap_promote_fires_on_high_delta(agent: PMCCAgent):
    """LEAP delta >= 0.95 with action=roll_short → action=roll_leap.
    Warning text mentions the delta reason."""
    leg = _leg_with_leap(long_delta=0.96)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.80,
        urgency="elevated", summary="...", rationale="...",
    )
    out = agent._promote_to_roll_leap_if_hard_rule(analysis, leg)
    assert out.action == "roll_leap"
    assert any("LEAP delta" in w and "0.96" in w for w in out.warnings)


def test_leap_promote_fires_on_low_dte(agent: PMCCAgent):
    """long_leg_dte < 120 with action=roll_short → action=roll_leap.
    Warning text mentions the DTE reason."""
    leg = _leg_with_leap(long_dte=100)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.80,
        urgency="elevated", summary="...", rationale="...",
    )
    out = agent._promote_to_roll_leap_if_hard_rule(analysis, leg)
    assert out.action == "roll_leap"
    assert any("DTE" in w and "120" in w for w in out.warnings)


def test_leap_promote_fires_on_both_conditions(agent: PMCCAgent):
    """Both delta and DTE conditions hold — warning lists both reasons."""
    leg = _leg_with_leap(long_delta=0.97, long_dte=80)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.80,
        urgency="elevated", summary="...", rationale="...",
    )
    out = agent._promote_to_roll_leap_if_hard_rule(analysis, leg)
    assert out.action == "roll_leap"
    w = " ".join(out.warnings)
    assert "0.97" in w and "80" in w


def test_leap_promote_no_op_below_thresholds(agent: PMCCAgent):
    """Healthy LEAP (delta 0.85, DTE 400) → action stays roll_short."""
    leg = _leg_with_leap(long_delta=0.85, long_dte=400)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.80,
        urgency="elevated", summary="...", rationale="...",
    )
    out = agent._promote_to_roll_leap_if_hard_rule(analysis, leg)
    assert out.action == "roll_short"
    assert out.warnings == []


def test_leap_promote_no_op_when_action_not_roll_short(agent: PMCCAgent):
    """A 'hold' on a deep-ITM LEAP shouldn't be promoted to roll_leap —
    the promotion only lifts roll_short / roll_short_early."""
    leg = _leg_with_leap(long_delta=0.96)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="hold", confidence=0.80,
        urgency="routine", summary="...", rationale="...",
    )
    out = agent._promote_to_roll_leap_if_hard_rule(analysis, leg)
    assert out.action == "hold"


def test_leap_promote_lifts_roll_short_early(agent: PMCCAgent):
    """roll_short_early is also lifted (same dispatch arm in
    propose_orders_for_pair as roll_short)."""
    leg = _leg_with_leap(long_dte=100)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short_early", confidence=0.80,
        urgency="elevated", summary="...", rationale="...",
    )
    out = agent._promote_to_roll_leap_if_hard_rule(analysis, leg)
    assert out.action == "roll_leap"


def test_leap_promote_no_op_on_none_inputs(agent: PMCCAgent):
    """Defensive: missing analysis or leg returns the input unchanged."""
    leg = _leg_with_leap(long_delta=0.96)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.80,
        urgency="elevated", summary="...", rationale="...",
    )
    assert agent._promote_to_roll_leap_if_hard_rule(None, leg) is None
    out = agent._promote_to_roll_leap_if_hard_rule(analysis, None)
    assert out.action == "roll_short"


# ---------------------------------------------------------------------------
# Item 1 (2026-05-02) — Halfway-roll cooldown + ROLL HISTORY prompt block
#   Backstop for the COOLDOWN clause in Rule 6 / BREACH POLICY: downgrade
#   roll_short → hold when a recent roll-up was executed within the
#   cooldown window AND short DTE > 2 AND extrinsic > floor.
# ---------------------------------------------------------------------------

import json as _json
import sqlite3 as _sqlite3
from pathlib import Path as _Path

from trading_corp.persistence.db import init_db as _init_db
from trading_corp.persistence.db import resolve_db_path as _resolve_db_path


def _insert_roll_pair_with_strikes(
    db_url: str,
    pair_id: str,
    symbol: str,
    *,
    close_strike: float,
    open_strike: float,
    fill_ts: str,
    close_price: float = 1.00,
    open_price: float = 1.50,
    leap_lifetime_key: str | None = None,
) -> None:
    """Seed a synthetic roll pair carrying the strike fields the
    detailed-history query reads. Identical SQL shape to the existing
    test_pmcc_position_context._insert_roll_pair helper but populates
    extra.strike + extra.action='roll_short_call_close' / '..._open'."""
    path = _resolve_db_path(db_url)
    extra_close = {
        "is_option": True, "underlying": symbol,
        "action": "roll_short_call_close",
        "pmcc_pair_id": pair_id,
        "strike": close_strike,
    }
    extra_open = {
        "is_option": True, "underlying": symbol,
        "action": "roll_short_call_open",
        "pmcc_pair_id": pair_id,
        "strike": open_strike,
    }
    if leap_lifetime_key:
        extra_close["leap_lifetime_key"] = leap_lifetime_key
        extra_open["leap_lifetime_key"] = leap_lifetime_key
    with _sqlite3.connect(path) as conn:
        conn.execute(
            """INSERT INTO proposed_order
               (id, ts, strategy, symbol, side, qty, order_type, limit_price,
                rationale, status, fill_price, fill_ts, extra_json)
               VALUES (?, ?, 'robinhood_pmcc', ?, 'buy', 1, 'limit', NULL,
                       'roll close', 'filled', ?, ?, ?)""",
            (f"close-{pair_id}", fill_ts, symbol, close_price, fill_ts,
             _json.dumps(extra_close)),
        )
        conn.execute(
            """INSERT INTO proposed_order
               (id, ts, strategy, symbol, side, qty, order_type, limit_price,
                rationale, status, fill_price, fill_ts, extra_json)
               VALUES (?, ?, 'robinhood_pmcc', ?, 'sell', 1, 'limit', NULL,
                       'roll open', 'filled', ?, ?, ?)""",
            (f"open-{pair_id}", fill_ts, symbol, open_price, fill_ts,
             _json.dumps(extra_open)),
        )
        conn.commit()


def _ts_days_ago(days: int) -> str:
    """ISO ts N days before now_utc — for synthesizing recent rolls."""
    from datetime import datetime, timedelta, timezone
    return (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).isoformat(timespec="seconds")


@pytest.fixture
def agent_with_db(strategies_yaml: _Path, risk_yaml: _Path, tmp_db: str) -> PMCCAgent:
    """PMCCAgent variant with db_url wired so cooldown queries work."""
    _init_db(tmp_db)
    return PMCCAgent(
        strategies_yaml=strategies_yaml, risk_yaml=risk_yaml, db_url=tmp_db,
    )


def test_query_prior_rolls_detailed_returns_full_metadata(
    agent_with_db: PMCCAgent, tmp_db: str,
):
    """Single roll pair → detailed query returns count, net_dollars,
    last_roll_ts, before/after strikes, strike_change, days_since."""
    fill_ts = _ts_days_ago(3)
    _insert_roll_pair_with_strikes(
        tmp_db, "p1", "MSTR",
        close_strike=160.0, open_strike=170.0,
        fill_ts=fill_ts, close_price=1.00, open_price=2.00,
    )
    out = agent_with_db._query_prior_rolls_detailed("MSTR")
    assert out["roll_count"] == 1
    assert out["net_dollars"] == pytest.approx(100.0)  # -100 + 200
    assert out["last_roll_ts"] == fill_ts
    assert out["last_roll_short_strike_before"] == pytest.approx(160.0)
    assert out["last_roll_short_strike_after"] == pytest.approx(170.0)
    assert out["last_roll_strike_change"] == pytest.approx(10.0)
    assert out["days_since_last_roll"] in (2, 3)  # tolerance for clock drift


def test_query_prior_rolls_detailed_picks_most_recent_pair(
    agent_with_db: PMCCAgent, tmp_db: str,
):
    """Two rolls 10d and 2d ago → last_roll_* reflects the 2d-ago pair."""
    _insert_roll_pair_with_strikes(
        tmp_db, "old", "MSTR",
        close_strike=140.0, open_strike=150.0,
        fill_ts=_ts_days_ago(10),
    )
    _insert_roll_pair_with_strikes(
        tmp_db, "new", "MSTR",
        close_strike=170.0, open_strike=180.0,
        fill_ts=_ts_days_ago(2),
    )
    out = agent_with_db._query_prior_rolls_detailed("MSTR")
    assert out["roll_count"] == 2
    assert out["last_roll_short_strike_before"] == pytest.approx(170.0)
    assert out["last_roll_short_strike_after"] == pytest.approx(180.0)
    assert out["days_since_last_roll"] in (1, 2)


def test_query_prior_rolls_detailed_empty_db_returns_zeros(
    agent_with_db: PMCCAgent,
):
    """No rolls in DB → all fields return defaults, no exception."""
    out = agent_with_db._query_prior_rolls_detailed("MSTR")
    assert out["roll_count"] == 0
    assert out["net_dollars"] == 0.0
    assert out["last_roll_ts"] is None
    assert out["last_roll_strike_change"] is None
    assert out["days_since_last_roll"] is None


def test_query_prior_rolls_detailed_no_db_returns_zeros(agent: PMCCAgent):
    """db_url=None → returns defaults, doesn't try to query."""
    out = agent._query_prior_rolls_detailed("MSTR")
    assert out["roll_count"] == 0
    assert out["last_roll_ts"] is None


def test_cooldown_fires_within_window_with_recent_rollup(
    agent_with_db: PMCCAgent, tmp_db: str,
):
    """Recent roll-up (3d ago, +$10 strike change), short DTE 5,
    extrinsic $1.50/sh, action=roll_short → action=hold + cooldown
    warning."""
    _insert_roll_pair_with_strikes(
        tmp_db, "p1", "MSTR",
        close_strike=160.0, open_strike=170.0,
        fill_ts=_ts_days_ago(3),
    )
    leg = _leg_with_leap(short_dte=5, short_mark=1.50)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.85,
        urgency="elevated", summary="...", rationale="...",
    )
    out = agent_with_db._recent_halfway_roll_cooldown(analysis, leg)
    assert out.action == "hold"
    assert any("cooldown" in w.lower() for w in out.warnings)
    assert any("$160.00" in w and "$170.00" in w for w in out.warnings)


def test_cooldown_no_fire_outside_window(
    agent_with_db: PMCCAgent, tmp_db: str,
):
    """Roll 10 days ago > cooldown_days(7) → no override."""
    _insert_roll_pair_with_strikes(
        tmp_db, "p1", "MSTR",
        close_strike=160.0, open_strike=170.0,
        fill_ts=_ts_days_ago(10),
    )
    leg = _leg_with_leap(short_dte=5, short_mark=1.50)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.85,
        urgency="elevated", summary="...", rationale="...",
    )
    out = agent_with_db._recent_halfway_roll_cooldown(analysis, leg)
    assert out.action == "roll_short"
    assert out.warnings == []


def test_cooldown_no_fire_when_short_dte_at_floor(
    agent_with_db: PMCCAgent, tmp_db: str,
):
    """short_leg_dte == terminal_dte_floor (default 2) → don't block
    deadline-driven roll. The terminal-DTE override owns this case."""
    _insert_roll_pair_with_strikes(
        tmp_db, "p1", "MSTR",
        close_strike=160.0, open_strike=170.0,
        fill_ts=_ts_days_ago(3),
    )
    leg = _leg_with_leap(short_dte=2, short_mark=1.50)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.85,
        urgency="elevated", summary="...", rationale="...",
    )
    out = agent_with_db._recent_halfway_roll_cooldown(analysis, leg)
    assert out.action == "roll_short"


def test_cooldown_no_fire_when_extrinsic_below_floor(
    agent_with_db: PMCCAgent, tmp_db: str,
):
    """short_leg_mark <= extrinsic_floor (0.50) → don't block; the
    cycle-continuity gate elsewhere wants the roll."""
    _insert_roll_pair_with_strikes(
        tmp_db, "p1", "MSTR",
        close_strike=160.0, open_strike=170.0,
        fill_ts=_ts_days_ago(3),
    )
    leg = _leg_with_leap(short_dte=5, short_mark=0.30)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.85,
        urgency="elevated", summary="...", rationale="...",
    )
    out = agent_with_db._recent_halfway_roll_cooldown(analysis, leg)
    assert out.action == "roll_short"


def test_cooldown_no_fire_when_strike_change_too_small(
    agent_with_db: PMCCAgent, tmp_db: str,
):
    """Last roll moved strike by $0.50 < min_strike_change(1.0) — that's
    normal cycle drift, not a halfway-style roll-up. Don't block."""
    _insert_roll_pair_with_strikes(
        tmp_db, "p1", "MSTR",
        close_strike=170.0, open_strike=170.50,
        fill_ts=_ts_days_ago(3),
    )
    leg = _leg_with_leap(short_dte=5, short_mark=1.50)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.85,
        urgency="elevated", summary="...", rationale="...",
    )
    out = agent_with_db._recent_halfway_roll_cooldown(analysis, leg)
    assert out.action == "roll_short"


def test_cooldown_no_fire_on_roll_down(
    agent_with_db: PMCCAgent, tmp_db: str,
):
    """Roll-DOWN (negative strike_change) is a defensive close, not a
    halfway-style up-roll. Don't block."""
    _insert_roll_pair_with_strikes(
        tmp_db, "p1", "MSTR",
        close_strike=180.0, open_strike=170.0,  # rolled DOWN $10
        fill_ts=_ts_days_ago(3),
    )
    leg = _leg_with_leap(short_dte=5, short_mark=1.50)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.85,
        urgency="elevated", summary="...", rationale="...",
    )
    out = agent_with_db._recent_halfway_roll_cooldown(analysis, leg)
    assert out.action == "roll_short"


def test_cooldown_no_fire_when_no_history(
    agent_with_db: PMCCAgent,
):
    """Empty DB → cooldown can't compute days_since_last_roll, no-op."""
    leg = _leg_with_leap(short_dte=5, short_mark=1.50)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.85,
        urgency="elevated", summary="...", rationale="...",
    )
    out = agent_with_db._recent_halfway_roll_cooldown(analysis, leg)
    assert out.action == "roll_short"


def test_cooldown_no_fire_when_action_not_roll_short(
    agent_with_db: PMCCAgent, tmp_db: str,
):
    """action='hold' → cooldown is a no-op (nothing to downgrade)."""
    _insert_roll_pair_with_strikes(
        tmp_db, "p1", "MSTR",
        close_strike=160.0, open_strike=170.0,
        fill_ts=_ts_days_ago(3),
    )
    leg = _leg_with_leap(short_dte=5, short_mark=1.50)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="hold", confidence=0.85,
        urgency="routine", summary="...", rationale="...",
    )
    out = agent_with_db._recent_halfway_roll_cooldown(analysis, leg)
    assert out.action == "hold"


def test_cooldown_no_op_on_none_inputs(agent_with_db: PMCCAgent):
    leg = _leg_with_leap()
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.85,
        urgency="elevated", summary="...", rationale="...",
    )
    assert agent_with_db._recent_halfway_roll_cooldown(None, leg) is None
    out = agent_with_db._recent_halfway_roll_cooldown(analysis, None)
    assert out.action == "roll_short"


# ---------------------------------------------------------------------------
# Item 1 — ROLL HISTORY prompt block formatter
# ---------------------------------------------------------------------------


def test_format_roll_history_block_no_history(agent_with_db: PMCCAgent):
    """No prior rolls in DB → returns 'No prior rolls' empty-state copy."""
    leg = _leg_with_leap()
    block = agent_with_db._format_roll_history_block(leg)
    assert "ROLL HISTORY" in block
    assert "No prior rolls" in block


def test_format_roll_history_block_with_recent_rollup(
    agent_with_db: PMCCAgent, tmp_db: str,
):
    """One recent roll-up → block includes count, net dollars, the
    most-recent strike change with 'roll-up' label."""
    _insert_roll_pair_with_strikes(
        tmp_db, "p1", "MSTR",
        close_strike=160.0, open_strike=170.0,
        fill_ts=_ts_days_ago(3),
        close_price=1.00, open_price=2.00,
    )
    leg = _leg_with_leap()
    block = agent_with_db._format_roll_history_block(leg)
    assert "Total prior rolls: 1" in block
    assert "$+100" in block or "$+100.00" in block or "+$100" in block or "$100" in block
    assert "$160.00" in block and "$170.00" in block
    assert "roll-up" in block


def test_format_roll_history_block_no_db_returns_empty(agent: PMCCAgent):
    """db_url=None → returns empty string (don't pollute prompt for
    fixture/test code paths)."""
    leg = _leg_with_leap()
    block = agent._format_roll_history_block(leg)
    assert block == ""


# ---------------------------------------------------------------------------
# Item 2 — roll_leap action produces 4-leg compound (close short + close
# LEAP + open new LEAP + open new short)
# ---------------------------------------------------------------------------


def _liquid_call(strike: float, delta: float, mark: float, dte: int = 14) -> dict:
    """Like _call but also populates open_interest + volume so the test
    chains pass _passes_liquidity (default min OI=100, min vol=50,
    max spread=10%). The shared _call helper omits those, which is why
    the pre-existing test_pmcc_logic scan tests fail at liquidity."""
    return {
        "strike_price": strike,
        "delta": delta,
        "mark_price": mark,
        "bid": round(mark - 0.05, 2),
        "ask": round(mark + 0.05, 2),
        "dte": dte,
        "open_interest": 5000,
        "volume": 1000,
        "option_id": f"liquid_{strike}_{dte}",
    }


@pytest.mark.asyncio
async def test_roll_leap_propose_emits_4_legs(agent: PMCCAgent, clear_earnings):
    # clear_earnings (Phase 2.5): B9 now runs on the roll_leap path (site 1) — pin the
    # earnings gate CLEAR so this asserts roll_leap MECHANICS without a live
    # get_next_earnings/yfinance call and the ~4x/yr flake when MSTR earnings ∈ 7d.
    """When propose_orders_for_pair gets action=roll_leap and the broker
    has both a qualifying new LEAP and a qualifying new weekly,
    4 ProposedOrders are emitted (vs the prior 3-leg compound):
       1. buy-to-close existing short
       2. sell-to-close existing LEAP
       3. buy-to-open new LEAP
       4. sell-to-open new short on the new LEAP
    Mirrors the BACKLOG verification: "the same RIOT scenario today
    should produce a 4-leg recommendation"."""
    today = date.today()
    leap_expiry = (today + timedelta(days=400)).isoformat()
    new_leap_expiry = (today + timedelta(days=500)).isoformat()
    # B7 (2026-07-21): current short is 7 DTE, so the new weekly must expire
    # strictly later. Was 7 DTE (a same-expiry roll, now correctly refused);
    # moved to 14 DTE (+ target_dte 7→14). Intent unchanged — a roll_leap with a
    # qualifying new LEAP and new weekly still emits all 4 legs; it just rolls the
    # short OUT (7→14) as a real roll_leap does, instead of onto the same expiry.
    new_weekly_expiry = (today + timedelta(days=14)).isoformat()

    broker = MockOptionBroker(
        option_positions=[
            _opt_position("MSTR", 100, 160.0, qty=1.0, delta=0.97,
                          avg_price=23.80, mark_price=58.05),  # LEAP
            _opt_position("MSTR", 7, 175.0, qty=-1.0, delta=0.30,
                          avg_price=2.50, mark_price=1.50),    # short
        ],
        expiry_dates={"MSTR": [new_weekly_expiry, new_leap_expiry]},
        calls={
            ("MSTR", new_leap_expiry): [
                _liquid_call(strike=180.0, delta=0.85, mark=20.0, dte=500),
            ],
            ("MSTR", new_weekly_expiry): [
                _liquid_call(strike=190.0, delta=0.30, mark=2.00, dte=14),
            ],
        },
    )
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_leap", confidence=0.92,
        urgency="elevated", summary="...", rationale="...",
        target_delta=0.30, target_dte=14,
    )

    orders = await agent.propose_orders_for_pair(broker, "MSTR", analysis)

    assert len(orders) == 4, (
        f"Expected 4 legs (close short + close LEAP + open new LEAP + "
        f"open new short on new LEAP), got {len(orders)}: "
        f"{[o.extra.get('action') for o in orders]}"
    )
    actions = [o.extra.get("action") for o in orders]
    assert actions == [
        "roll_leap_close_short",
        "roll_leap_close",
        "roll_leap_open",
        "roll_leap_open_short",
    ]
    # All 4 legs share the same pmcc_pair_id (compound-roll lineage)
    pair_ids = {o.extra.get("pmcc_pair_id") for o in orders}
    assert len(pair_ids) == 1


@pytest.mark.asyncio
async def test_phase_a_roll_leap_legs_are_advisory(agent: PMCCAgent, clear_earnings):
    """Phase A: every roll_leap leg is dispatch='advisory' (the operator executes
    the LEAP roll manually; the agent never places these) and is NOT combo-tagged
    (roll_leap never rides the atomic combo path)."""
    today = date.today()
    new_leap_expiry = (today + timedelta(days=500)).isoformat()
    new_weekly_expiry = (today + timedelta(days=14)).isoformat()
    broker = MockOptionBroker(
        option_positions=[
            _opt_position("MSTR", 100, 160.0, qty=1.0, delta=0.97,
                          avg_price=23.80, mark_price=58.05),   # LEAP
            _opt_position("MSTR", 7, 175.0, qty=-1.0, delta=0.30,
                          avg_price=2.50, mark_price=1.50),      # short
        ],
        expiry_dates={"MSTR": [new_weekly_expiry, new_leap_expiry]},
        calls={
            ("MSTR", new_leap_expiry): [
                _liquid_call(strike=180.0, delta=0.85, mark=20.0, dte=500)],
            ("MSTR", new_weekly_expiry): [
                _liquid_call(strike=190.0, delta=0.30, mark=2.00, dte=14)],
        },
    )
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_leap", confidence=0.92,
        urgency="elevated", summary="...", rationale="...",
        target_delta=0.30, target_dte=14,
    )
    orders = await agent.propose_orders_for_pair(broker, "MSTR", analysis)
    assert len(orders) == 4
    for o in orders:
        assert o.dispatch == "advisory", o.extra.get("action")
        assert not o.extra.get("is_multi_leg")


@pytest.mark.asyncio
async def test_roll_leap_aborts_when_no_qualifying_weekly(agent_logged, cap_logger, clear_earnings):
    # clear_earnings (Phase 2.5): B9 now runs FIRST on the roll_leap path — pin earnings
    # CLEAR so the assertion reaches the B4/B7 abort reason instead of a live-earnings
    # flake flipping it to earnings_window (removes a live get_next_earnings call).
    """A roll_leap whose only future expiry is a LEAP-DTE contract (no true
    weekly) ABORTS atomically — 0 legs proposed + a pmcc_roll_aborted audit
    with reason sparse_chain_no_weekly_for_new_leap.

    HISTORY (why this is a corrected test, not a loosened one): before Phase 2
    this test was named `test_roll_leap_emits_3_legs_when_no_qualifying_weekly`
    and asserted that a 3-leg compound (close short + close LEAP + open new LEAP)
    still SHIPPED when no weekly existed, leaving the fresh LEAP uncovered. Its
    own docstring documented that `_find_best_weekly`'s fallback would "pick the
    LEAP date as the only future expiry" — i.e. it encoded the LEAP-as-weekly
    fallback pathology as expected behavior, and its lenient `in actions` assert
    (accept 3 OR 4 legs) masked it. Phase-1 B4 made roll_leap atomic (no partial
    ship) and Phase-2 B7's DTE-ceiling fallback (`_WEEKLY_FALLBACK_MAX_DTE`)
    stopped a 500-DTE LEAP from being taken as a "weekly". The correct behavior
    is now an atomic abort, which this rewrite pins exactly."""
    today = date.today()
    new_leap_expiry = (today + timedelta(days=500)).isoformat()

    broker = MockOptionBroker(
        option_positions=[
            _opt_position("MSTR", 100, 160.0, qty=1.0, delta=0.97,
                          avg_price=23.80, mark_price=58.05),
            _opt_position("MSTR", 7, 175.0, qty=-1.0, delta=0.30,
                          avg_price=2.50, mark_price=1.50),
        ],
        expiry_dates={"MSTR": [new_leap_expiry]},  # ONLY a LEAP-DTE expiry
        calls={
            ("MSTR", new_leap_expiry): [
                _liquid_call(strike=180.0, delta=0.85, mark=20.0, dte=500),
            ],
        },
    )
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_leap", confidence=0.92,
        urgency="elevated", summary="...", rationale="...",
    )
    orders = await agent_logged.propose_orders_for_pair(broker, "MSTR", analysis)

    # Atomic abort: nothing ships (never a close-only "roll" leaving the LEAP
    # uncovered, never the 500-DTE LEAP taken as a weekly).
    assert orders == [], [o.extra.get("action") for o in orders]
    ev = _abort_event(cap_logger)
    assert ev["payload"]["reason"] == "sparse_chain_no_weekly_for_new_leap"
    assert ev["payload"]["missing_leg"] == "new_short_on_new_leap"
    # The abort reason distinguishes "dates exist but none is a rollout weekly"
    # from an empty chain — here the only date is a LEAP, beyond the ceiling.
    assert ev["payload"]["chain_state"]["reason"] == "no_rollout_weekly"


# ---------------------------------------------------------------------------
# Composition: terminal-DTE → Hard-Rule promotion → cooldown ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_orders_promotes_roll_short_to_roll_leap_via_hard_rule(
    agent: PMCCAgent, clear_earnings,
):
    # clear_earnings (Phase 2.5): Hard-Rule promotion routes into the roll_leap path where
    # B9 now runs — pin earnings CLEAR to avoid a live get_next_earnings call / ~4x/yr flake.
    """End-to-end: LLM emits action=roll_short on a position with a
    deep-ITM LEAP (delta 0.96). propose_orders_for_pair applies the
    Hard-Rule promotion before dispatch, so the resulting orders are
    the 4-leg roll_leap compound, not the 2-leg roll_short."""
    today = date.today()
    new_leap_expiry = (today + timedelta(days=500)).isoformat()
    # B7 (2026-07-21): current short is 7 DTE; the new weekly must expire strictly
    # later. Was 7 DTE (same-expiry, now refused); moved to 14 DTE (+ target_dte
    # 7→14). Intent unchanged — Hard-Rule still promotes roll_short → 4-leg
    # roll_leap; the new short just rolls OUT (7→14) as a real roll does.
    new_weekly_expiry = (today + timedelta(days=14)).isoformat()

    broker = MockOptionBroker(
        option_positions=[
            _opt_position("MSTR", 100, 160.0, qty=1.0, delta=0.97,
                          avg_price=23.80, mark_price=58.05),
            _opt_position("MSTR", 7, 175.0, qty=-1.0, delta=0.30,
                          avg_price=2.50, mark_price=1.50),
        ],
        expiry_dates={"MSTR": [new_weekly_expiry, new_leap_expiry]},
        calls={
            ("MSTR", new_leap_expiry): [
                _liquid_call(strike=180.0, delta=0.85, mark=20.0, dte=500),
            ],
            ("MSTR", new_weekly_expiry): [
                _liquid_call(strike=190.0, delta=0.30, mark=2.00, dte=14),
            ],
        },
    )
    # LLM emitted roll_short — Hard-Rule promotion should lift it.
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.85,
        urgency="elevated", summary="...", rationale="...",
        target_delta=0.30, target_dte=14,
    )
    orders = await agent.propose_orders_for_pair(broker, "MSTR", analysis)

    actions = [o.extra.get("action") for o in orders]
    assert actions == [
        "roll_leap_close_short", "roll_leap_close",
        "roll_leap_open", "roll_leap_open_short",
    ], (
        "Expected Hard-Rule promotion to route roll_short → roll_leap → "
        f"4-leg compound. Got {actions}"
    )


def test_cooldown_does_not_fire_after_hard_rule_promotion(
    agent_with_db: PMCCAgent, tmp_db: str,
):
    """When BOTH guards would apply, Hard-Rule promotion runs first and
    moves action to roll_leap; cooldown is then a no-op (it only acts
    on roll_short / roll_short_early). Pin the composition order
    that propose_orders_for_pair uses so a future re-order doesn't
    silently let cooldown veto a needed LEAP roll."""
    _insert_roll_pair_with_strikes(
        tmp_db, "p1", "MSTR",
        close_strike=160.0, open_strike=170.0,
        fill_ts=_ts_days_ago(3),
    )
    leg = _leg_with_leap(long_delta=0.97, short_dte=5, short_mark=1.50)
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.85,
        urgency="elevated", summary="...", rationale="...",
    )
    promoted = agent_with_db._promote_to_roll_leap_if_hard_rule(analysis, leg)
    assert promoted.action == "roll_leap"
    after_cooldown = agent_with_db._recent_halfway_roll_cooldown(promoted, leg)
    assert after_cooldown.action == "roll_leap", (
        "Cooldown must be a no-op on roll_leap so a needed LEAP roll "
        "isn't silently vetoed by the back-to-back-halfway guard."
    )


# ---------------------------------------------------------------------------
# Item 3 (2026-05-03) — target_strike honors LLM's rule-driven strike
# (halfway-rule strike drift fix). Adds target_strike to PMCCAnalysis +
# threads through _select_weekly_strike + _find_best_weekly so a Major
# Breach halfway midpoint isn't silently overridden by delta ranking.
# ---------------------------------------------------------------------------


def test_select_weekly_strike_honors_target_strike():
    """When target_strike is set, picker selects the listed strike
    closest to it — even if its delta is far from target_delta."""
    calls = [
        _liquid_call(strike=170.0, delta=0.45, mark=4.00, dte=7),  # closest to 169
        _liquid_call(strike=180.0, delta=0.35, mark=2.50, dte=7),
        _liquid_call(strike=190.0, delta=0.25, mark=1.50, dte=7),
    ]
    best = _select_weekly_strike(calls, target_delta=0.30, target_strike=169.0)
    assert best is not None
    assert best["strike_price"] == 170.0   # honored target_strike, not target_delta


def test_select_weekly_strike_falls_back_to_delta_when_no_target_strike():
    """target_strike=None → original delta-distance behavior (OTM-only)."""
    calls = [
        _liquid_call(strike=170.0, delta=0.45, mark=4.00, dte=7),  # ITM/borderline
        _liquid_call(strike=180.0, delta=0.30, mark=2.50, dte=7),  # closest to 0.30
        _liquid_call(strike=190.0, delta=0.20, mark=1.50, dte=7),
    ]
    best = _select_weekly_strike(calls, target_delta=0.30, target_strike=None)
    assert best is not None
    assert best["strike_price"] == 180.0


def test_select_weekly_strike_target_strike_picks_nearest_listed():
    """Target $169.25 between listed strikes — picks the closer one."""
    calls = [
        _liquid_call(strike=165.0, delta=0.55, mark=8.00, dte=7),
        _liquid_call(strike=167.5, delta=0.50, mark=6.50, dte=7),  # 1.75 from 169.25
        _liquid_call(strike=170.0, delta=0.45, mark=5.00, dte=7),  # 0.75 from 169.25
        _liquid_call(strike=172.5, delta=0.40, mark=4.00, dte=7),
    ]
    best = _select_weekly_strike(calls, target_strike=169.25)
    assert best is not None
    assert best["strike_price"] == 170.0


def test_select_weekly_strike_target_strike_honors_even_itm():
    """target_strike doesn't second-guess: if the LLM cites an ITM
    strike (e.g. defensive halfway-roll INTO the breach), picker
    honors it. Caller is responsible for sanity — the LLM cited it
    per the rules."""
    calls = [
        _liquid_call(strike=160.0, delta=0.60, mark=10.0, dte=7),  # ITM
        _liquid_call(strike=180.0, delta=0.30, mark=2.50, dte=7),
    ]
    best = _select_weekly_strike(calls, target_strike=162.0)
    assert best is not None
    assert best["strike_price"] == 160.0


def test_pmcc_analysis_dataclass_default_target_strike_none():
    """PMCCAnalysis instances without target_strike default to None
    (backwards-compat for callers that don't supply it)."""
    a = PMCCAnalysis(
        symbol="X", action="hold", confidence=0.5, urgency="routine",
        summary="", rationale="",
    )
    assert a.target_strike is None


def test_pmcc_analysis_carries_target_strike_when_set():
    a = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.85,
        urgency="elevated", summary="...", rationale="...",
        target_delta=0.35, target_dte=7, target_strike=169.25,
    )
    assert a.target_strike == 169.25


@pytest.mark.asyncio
async def test_find_best_weekly_threads_target_strike_through(agent: PMCCAgent):
    """End-to-end: _find_best_weekly with target_strike set picks the
    listed strike closest to it, ignoring target_delta."""
    today = date.today()
    expiry = (today + timedelta(days=7)).isoformat()
    broker = MockOptionBroker(
        expiry_dates={"MSTR": [expiry]},
        calls={
            ("MSTR", expiry): [
                _liquid_call(strike=170.0, delta=0.45, mark=4.00, dte=7),
                _liquid_call(strike=180.0, delta=0.30, mark=2.50, dte=7),
                _liquid_call(strike=190.0, delta=0.25, mark=1.50, dte=7),
            ],
        },
    )
    best = await agent._find_best_weekly(
        "MSTR", broker, target_delta=0.30, target_dte=7, target_strike=169.0,
    )
    assert best is not None
    assert best["strike_price"] == 170.0   # not 180 (the delta-best)


@pytest.mark.asyncio
async def test_propose_roll_short_uses_target_strike_when_set(agent: PMCCAgent, clear_earnings):
    """End-to-end through _propose_roll_short: when analysis.target_strike
    is set (e.g. LLM cited halfway midpoint $169.00), the open leg is
    sold at the listed strike closest to $169.00 — not the 0.30-delta
    default. Mirrors the BACKLOG-cited MSTR symptom.

    This test asserts strike SELECTION. The fixture's marks (buy back a deep-ITM
    short @17.80, sell the new short @~10.5) make the roll a NET DEBIT, which is
    incidental to what's asserted — so the analysis carries a `net_debit_justified`
    override (B9→B7→B2 gate order; B2 would otherwise block the debit). This models
    the real halfway-roll-on-breach path: the LLM prescribes an ITM midpoint AND
    authorizes the debit. The strike assertion is unchanged."""
    today = date.today()
    new_weekly_expiry = (today + timedelta(days=7)).isoformat()
    broker = MockOptionBroker(
        option_positions=[
            _opt_position("MSTR", 400, 160.0, qty=1.0, delta=0.85,
                          avg_price=23.80, mark_price=58.05),
            _opt_position("MSTR", 0, 162.50, qty=-1.0, delta=0.95,
                          avg_price=15.83, mark_price=17.80),  # the breached short
        ],
        expiry_dates={"MSTR": [new_weekly_expiry]},
        calls={
            ("MSTR", new_weekly_expiry): [
                _liquid_call(strike=170.0, delta=0.45, mark=10.5, dte=7),
                _liquid_call(strike=180.0, delta=0.35, mark=6.50, dte=7),
                _liquid_call(strike=187.5, delta=0.30, mark=4.50, dte=7),  # 0.30-delta default
            ],
        },
    )
    legs = await agent.detect_existing_legs(broker)
    pos = next(p for p in legs if p.symbol == "MSTR")

    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.85,
        urgency="elevated",
        summary="Major Breach — halfway roll to ~$169",
        rationale="...", target_delta=0.30, target_dte=7,
        target_strike=169.0,
        # B2: this deep-breach halfway roll is a net debit by construction; the
        # LLM authorizes it (the designed valve). Incidental to the strike assert.
        override={"kind": "net_debit_justified", "reason": "halfway roll on deep breach"},
    )
    orders = await agent._propose_roll_short("MSTR", pos, broker, analysis)
    open_leg = next(o for o in orders if o.extra.get("action") == "roll_short_call_open")
    assert open_leg.extra["strike"] == 170.0, (
        f"Expected open strike 170.0 (closest to target_strike=169.0); "
        f"got {open_leg.extra['strike']} (likely the 0.30-delta default)"
    )


@pytest.mark.asyncio
async def test_propose_roll_short_falls_back_to_delta_when_target_strike_none(
    agent: PMCCAgent, clear_earnings,
):
    """No target_strike set → original delta-distance behavior. Pin
    backwards-compat so existing recommendations don't shift.

    Same deep-ITM breached fixture → net-debit roll → `net_debit_justified`
    override (see the sibling test). Asserts the delta-distance PICK is unchanged."""
    today = date.today()
    new_weekly_expiry = (today + timedelta(days=7)).isoformat()
    broker = MockOptionBroker(
        option_positions=[
            _opt_position("MSTR", 400, 160.0, qty=1.0, delta=0.85,
                          avg_price=23.80, mark_price=58.05),
            _opt_position("MSTR", 0, 162.50, qty=-1.0, delta=0.95,
                          avg_price=15.83, mark_price=17.80),
        ],
        expiry_dates={"MSTR": [new_weekly_expiry]},
        calls={
            ("MSTR", new_weekly_expiry): [
                _liquid_call(strike=170.0, delta=0.45, mark=10.5, dte=7),
                _liquid_call(strike=180.0, delta=0.35, mark=6.50, dte=7),
                _liquid_call(strike=187.5, delta=0.30, mark=4.50, dte=7),
            ],
        },
    )
    legs = await agent.detect_existing_legs(broker)
    pos = next(p for p in legs if p.symbol == "MSTR")

    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.85,
        urgency="elevated", summary="...", rationale="...",
        target_delta=0.30, target_dte=7,
        # target_strike NOT set
        # B2: same deep-breach net-debit fixture — authorize the debit (see sibling).
        override={"kind": "net_debit_justified", "reason": "halfway roll on deep breach"},
    )
    orders = await agent._propose_roll_short("MSTR", pos, broker, analysis)
    open_leg = next(o for o in orders if o.extra.get("action") == "roll_short_call_open")
    assert open_leg.extra["strike"] == 187.5  # delta-distance pick


# ===========================================================================
# Phase 2 — B2 (credit gate) + B9 (earnings gate) on the roll path.
# Gate order in `_propose_roll_short`: B9 earnings → B7 selection → B2 credit.
# B9/B2 are overridable via the PMCCAnalysis.override contract
# (earnings_override / net_debit_justified); B7 is hard-enforced.
# ===========================================================================


def _credit_roll_broker() -> MockOptionBroker:
    """AAPL PMCC (short 14 DTE @175, mark 1.20) with a credit-positive, rolled-out
    weekly at 21 DTE (@175, bid 1.75 → +0.55 conservative net credit). Used by the
    B9 tests where the ONLY variable under test is the earnings gate."""
    later = _future(21)
    return MockOptionBroker(
        option_positions=[
            _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0,
                          delta=0.82, avg_price=22.50),
            _opt_position("AAPL", expiry_days=14, strike=175.0, qty=-1.0,
                          delta=0.28, avg_price=1.45, mark_price=1.20),
        ],
        expiry_dates={"AAPL": [_future(14), later, _future(400)]},
        calls={
            ("AAPL", _future(14)): [
                _liquid_call(strike=175.0, delta=0.28, mark=1.20, dte=14)],
            ("AAPL", later): [_liquid_call(strike=175.0, delta=0.28, mark=1.80, dte=21)],
            ("AAPL", _future(400)): [
                _liquid_call(strike=130.0, delta=0.85, mark=25.0, dte=400)],
        },
    )


def _deep_itm_breach_broker() -> MockOptionBroker:
    """MSTR with a deep-ITM breached short (@162.50, mark 17.80) rolling up to a
    lower-premium weekly — a NET DEBIT roll by construction (buy back 17.80, sell
    ~10.5). Shared by the B2 authorized/unauthorized pair. Mirrors the target_strike
    fixture above."""
    weekly = _future(7)
    return MockOptionBroker(
        option_positions=[
            _opt_position("MSTR", 400, 160.0, qty=1.0, delta=0.85,
                          avg_price=23.80, mark_price=58.05),
            _opt_position("MSTR", 0, 162.50, qty=-1.0, delta=0.95,
                          avg_price=15.83, mark_price=17.80),
        ],
        expiry_dates={"MSTR": [weekly]},
        calls={("MSTR", weekly): [
            _liquid_call(strike=170.0, delta=0.45, mark=10.5, dte=7),
            _liquid_call(strike=180.0, delta=0.35, mark=6.50, dte=7),
            _liquid_call(strike=187.5, delta=0.30, mark=4.50, dte=7),
        ]},
    )


@pytest.mark.asyncio
async def test_b2_unauthorized_net_debit_roll_blocks(agent_logged, cap_logger, clear_earnings):
    """B2: the SAME deep-ITM breach fixture as the target_strike tests, but with NO
    override → the net-debit roll ABORTS atomically (0 legs) + a `pmcc_roll_aborted`
    audit reason `net_debit_roll`, carrying BOTH conservative_net and mark_net. This
    is the pathology B2 exists for (37 historical net-debit rolls). Paired with the
    override'd target_strike tests (authorized ships / unauthorized blocks, same
    fixture) it is the behavioral proof B2 blocks rather than merely not-firing."""
    broker = _deep_itm_breach_broker()
    legs = await agent_logged.detect_existing_legs(broker)
    pos = next(p for p in legs if p.symbol == "MSTR")
    analysis = PMCCAnalysis(
        symbol="MSTR", action="roll_short", confidence=0.85, urgency="elevated",
        summary="Major Breach — halfway roll", rationale="...",
        target_delta=0.30, target_dte=7, target_strike=169.0,
        # NO override → B2 must block.
    )
    orders = await agent_logged._propose_roll_short("MSTR", pos, broker, analysis)
    assert orders == [], [o.extra.get("action") for o in orders]
    ev = _abort_event(cap_logger)
    assert ev["payload"]["reason"] == "net_debit_roll"
    assert ev["payload"]["conservative_net"] < 0
    assert "mark_net" in ev["payload"]                     # both figures recorded
    assert ev["payload"]["fees_included"] is False         # pre-fee, stated
    # gate order + map: earnings & selection passed, credit blocked
    assert ev["payload"]["gates"] == {
        "earnings": "clear", "selection": "ok", "credit": "blocked"}


@pytest.mark.asyncio
async def test_b2_net_credit_roll_ships(agent, clear_earnings):
    """B2: a credit-positive, rolled-out roll ships BOTH legs — the gate does not
    over-block a normal credit roll."""
    broker = _credit_roll_broker()
    legs = await agent.detect_existing_legs(broker)
    pos = next(p for p in legs if p.symbol == "AAPL")
    orders = await agent._propose_roll_short("AAPL", pos, broker)
    assert len(orders) == 2
    assert {o.extra["action"] for o in orders} == {
        "roll_short_call_close", "roll_short_call_open"}


@pytest.mark.asyncio
async def test_phase_a_roll_short_legs_are_combo_tagged(agent, clear_earnings):
    """Phase A: the two roll_short legs carry atomic-combo tags so they dispatch
    through place_combo (one all-or-nothing order); validate_combo_cohesion
    accepts the pair. dispatch stays 'executable' (the agent DOES place these)."""
    from trading_corp.brokers.base import validate_combo_cohesion
    broker = _credit_roll_broker()
    legs = await agent.detect_existing_legs(broker)
    pos = next(p for p in legs if p.symbol == "AAPL")
    orders = await agent._propose_roll_short("AAPL", pos, broker)
    assert len(orders) == 2
    for o in orders:
        assert o.extra["is_multi_leg"] is True
        assert o.extra["combo_id"] == o.extra["pmcc_pair_id"]
        assert o.extra["combo_direction"] in ("credit", "debit")
        assert isinstance(o.extra["net_limit_price"], float)
        assert o.extra["ratio_quantity"] == 1
        assert o.dispatch == "executable"
    assert len({o.extra["combo_id"] for o in orders}) == 1
    combo = validate_combo_cohesion(orders)          # the place_multi_leg validator
    assert combo.combo_id == orders[0].extra["combo_id"]


@pytest.mark.asyncio
async def test_b9_roll_blocked_within_earnings_buffer(agent_logged, cap_logger, monkeypatch):
    """B9: a roll inside the earnings buffer aborts (0 legs) + `pmcc_roll_aborted`
    reason `earnings_window`, gates.earnings == 'blocked'. B9 runs FIRST, so it
    aborts before selection/credit are even evaluated (skill HARD RULE L257: no new
    short premium within the earnings buffer)."""
    from datetime import datetime, timezone, timedelta as _td
    monkeypatch.setattr("trading_corp.utils.market_data.get_next_earnings",
                        lambda symbol, *a, **k: datetime.now(timezone.utc) + _td(days=3))
    broker = _credit_roll_broker()
    legs = await agent_logged.detect_existing_legs(broker)
    pos = next(p for p in legs if p.symbol == "AAPL")
    orders = await agent_logged._propose_roll_short("AAPL", pos, broker)
    assert orders == [], [o.extra.get("action") for o in orders]
    ev = _abort_event(cap_logger)
    assert ev["payload"]["reason"] == "earnings_window"
    assert ev["payload"]["gates"]["earnings"] == "blocked"
    assert "selection" not in ev["payload"]["gates"]   # aborted before selection


@pytest.mark.asyncio
async def test_b9_roll_ships_with_earnings_override(agent, monkeypatch):
    """B9: `earnings_override` lets a within-buffer roll proceed (a black-sheep
    perpetual roll that must not let a breached short run into earnings). Ships."""
    from datetime import datetime, timezone, timedelta as _td
    monkeypatch.setattr("trading_corp.utils.market_data.get_next_earnings",
                        lambda symbol, *a, **k: datetime.now(timezone.utc) + _td(days=3))
    broker = _credit_roll_broker()
    legs = await agent.detect_existing_legs(broker)
    pos = next(p for p in legs if p.symbol == "AAPL")
    analysis = PMCCAnalysis(
        symbol="AAPL", action="roll_short", confidence=0.8, urgency="routine",
        summary="", rationale="",
        override={"kind": "earnings_override",
                  "reason": "perpetual roll — breached short must not run into earnings"},
    )
    orders = await agent._propose_roll_short("AAPL", pos, broker, analysis)
    assert len(orders) == 2
    assert {o.extra["action"] for o in orders} == {
        "roll_short_call_close", "roll_short_call_open"}


@pytest.mark.asyncio
async def test_b9_data_unavailable_recorded_on_shipped_roll(agent_logged, cap_logger, monkeypatch):
    """B9 fail-open observability (amendment 4): when the earnings source returns no
    data, the roll SHIPS (fail-open) but the shipped-roll `pmcc_roll_gates` audit
    records gates.earnings == 'data_unavailable' — so a roll that shipped because the
    source was DOWN is distinguishable from one that shipped because earnings were
    genuinely clear."""
    monkeypatch.setattr("trading_corp.utils.market_data.get_next_earnings",
                        lambda symbol, *a, **k: None)
    broker = _credit_roll_broker()
    legs = await agent_logged.detect_existing_legs(broker)
    pos = next(p for p in legs if p.symbol == "AAPL")
    orders = await agent_logged._propose_roll_short("AAPL", pos, broker)
    assert len(orders) == 2                                  # fail-open: shipped
    gate_ev = next(e for e in cap_logger.events if e["kind"] == "pmcc_roll_gates")
    assert gate_ev["payload"]["gates"]["earnings"] == "data_unavailable"
    assert gate_ev["payload"]["gates"]["credit"] == "clear"


def test_select_weekly_strike_handles_calls_without_strike():
    """Defensive: if the call dict somehow lacks strike_price, the
    target_strike branch falls through gracefully (returns None)."""
    calls = [{"delta": 0.30, "mark_price": 1.0}]  # no strike_price
    best = _select_weekly_strike(calls, target_strike=170.0)
    assert best is None


# ===========================================================================
# Phase 2 — B7 (roll-out enforcement): the new short must expire STRICTLY LATER
# than the current short. `after_dte` carries the current short's DTE on roll
# paths (opens pass None). The DTE-ceiling fallback (_WEEKLY_FALLBACK_MAX_DTE)
# additionally blocks a LEAP-DTE contract from being taken as a "weekly".
# B7 has NO override — it is hard-enforced.
# ===========================================================================


@pytest.mark.asyncio
async def test_b7_find_best_weekly_selects_strictly_later_on_roll(agent: PMCCAgent):
    """A chain offering a same-DTE weekly (== current short) AND a strictly
    later one: the roll path (after_dte set) skips the same-DTE expiry and picks
    the later one, while the open path (after_dte=None) still picks the nearest.
    Proves the roll-out constraint is exactly what shifts the selection."""
    today = date.today()
    same = (today + timedelta(days=14)).isoformat()   # == current short DTE
    later = (today + timedelta(days=21)).isoformat()  # strictly later
    broker = MockOptionBroker(
        expiry_dates={"AAPL": [same, later, _future(400)]},
        calls={
            ("AAPL", same): [_liquid_call(strike=175.0, delta=0.28, mark=1.50, dte=14)],
            ("AAPL", later): [_liquid_call(strike=175.0, delta=0.28, mark=1.80, dte=21)],
            ("AAPL", _future(400)): [
                _liquid_call(strike=130.0, delta=0.85, mark=25.0, dte=400)],
        },
    )
    # Open path (after_dte=None): nearest in-window weekly.
    opened = await agent._find_best_weekly("AAPL", broker)
    assert opened is not None and opened["expiration_date"] == same
    # Roll path (after_dte=14): same-DTE refused, strictly-later selected.
    rolled = await agent._find_best_weekly("AAPL", broker, after_dte=14)
    assert rolled is not None and rolled["expiration_date"] == later


@pytest.mark.asyncio
async def test_b7_propose_roll_short_opens_strictly_later(agent: PMCCAgent, clear_earnings):
    """End-to-end: the roll's OPEN leg expires strictly later than the short it
    closes (never a same-expiry roll)."""
    today = date.today()
    later = (today + timedelta(days=21)).isoformat()
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0,
                      delta=0.82, avg_price=22.50),
        _opt_position("AAPL", expiry_days=14, strike=175.0, qty=-1.0,
                      delta=0.28, avg_price=1.45, mark_price=1.20),
    ]
    broker = MockOptionBroker(
        option_positions=opt_positions,
        expiry_dates={"AAPL": [_future(14), later, _future(400)]},
        calls={
            ("AAPL", _future(14)): [
                _liquid_call(strike=175.0, delta=0.28, mark=1.20, dte=14)],
            ("AAPL", later): [_liquid_call(strike=175.0, delta=0.28, mark=1.80, dte=21)],
            ("AAPL", _future(400)): [
                _liquid_call(strike=130.0, delta=0.85, mark=25.0, dte=400)],
        },
    )
    legs = await agent.detect_existing_legs(broker)
    pos = next(p for p in legs if p.symbol == "AAPL")
    orders = await agent._propose_roll_short("AAPL", pos, broker)
    assert len(orders) == 2
    close_leg = next(o for o in orders if o.extra["action"] == "roll_short_call_close")
    open_leg = next(o for o in orders if o.extra["action"] == "roll_short_call_open")
    assert open_leg.extra["expiration"] > close_leg.extra["expiration"]  # rolled OUT
    assert open_leg.extra["expiration"] == later


@pytest.mark.asyncio
async def test_b7_same_expiry_only_chain_aborts(agent_logged, cap_logger, clear_earnings):
    """When the ONLY weekly in the chain sits at the current short's expiry (a
    roll-IN / same-expiry), the roll aborts atomically rather than rolling to an
    equal-or-earlier expiry — and the LEAP (400 DTE) is NOT taken as a weekly."""
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0,
                      delta=0.82, avg_price=22.50),
        _opt_position("AAPL", expiry_days=14, strike=175.0, qty=-1.0,
                      delta=0.28, avg_price=1.45, mark_price=1.20),
    ]
    broker = MockOptionBroker(
        option_positions=opt_positions,
        expiry_dates={"AAPL": [_future(14), _future(400)]},   # only same-DTE weekly + LEAP
        calls={
            ("AAPL", _future(14)): [
                _liquid_call(strike=175.0, delta=0.28, mark=1.20, dte=14)],
            ("AAPL", _future(400)): [
                _liquid_call(strike=130.0, delta=0.85, mark=25.0, dte=400)],
        },
    )
    legs = await agent_logged.detect_existing_legs(broker)
    pos = next(p for p in legs if p.symbol == "AAPL")
    orders = await agent_logged._propose_roll_short("AAPL", pos, broker)
    assert orders == [], [o.extra.get("action") for o in orders]
    ev = _abort_event(cap_logger)
    assert ev["payload"]["reason"] == "sparse_chain_no_weekly"
    assert ev["payload"]["chain_state"]["reason"] == "no_rollout_weekly"


# ===========================================================================
# Phase 1 — B4 (atomic rolls/opens) + B1 (HOLD precedence) + B11 (holiday guard)
# ===========================================================================

class _CaptureLogger:
    """Fake LoggerAgent capturing log_event(actor, kind, payload) calls."""
    def __init__(self):
        self.events = []

    def log_event(self, actor=None, kind=None, payload=None):
        self.events.append({"actor": actor, "kind": kind, "payload": payload})


@pytest.fixture
def cap_logger():
    return _CaptureLogger()


@pytest.fixture
def agent_logged(strategies_yaml: Path, risk_yaml: Path, cap_logger) -> PMCCAgent:
    return PMCCAgent(strategies_yaml=strategies_yaml, risk_yaml=risk_yaml,
                     logger_agent=cap_logger)


def _roll_leap_analysis(symbol="AAPL"):
    return PMCCAnalysis(symbol=symbol, action="roll_leap", confidence=0.8,
                        urgency="routine", summary="", rationale="")


def _abort_event(cap_logger):
    return next(e for e in cap_logger.events if e["kind"] == "pmcc_roll_aborted")


# ---- B4 (a): roll_short aborts when no re-open weekly (no close-only) ----
@pytest.mark.asyncio
async def test_b4a_roll_short_aborts_when_no_weekly(agent_logged, cap_logger, clear_earnings):
    leg = _pmcc_with_short(dte=2, pnl_pct=0.10)   # AAPL
    broker = MockOptionBroker()                    # empty chains
    orders = await agent_logged._propose_roll_short("AAPL", leg, broker)
    assert orders == []                            # aborted, NOT a close-only roll
    ev = _abort_event(cap_logger)
    assert ev["payload"]["reason"] == "sparse_chain_no_weekly"
    assert ev["payload"]["missing_leg"] == "new_short"
    assert ev["payload"]["chain_state"]["reason"] == "no_future_expiry_dates"


# ---- B4 (b): roll_leap aborts when no new LEAP ----
@pytest.mark.asyncio
async def test_b4b_roll_leap_aborts_when_no_leap(agent_logged, cap_logger, clear_earnings):
    # clear_earnings (Phase 2.5): B9 now runs FIRST on the roll_leap path — pin earnings
    # CLEAR so the B4 abort reason is reached, not a live-earnings flake (removes a live
    # get_next_earnings/yfinance call, ~4x/yr flake window).
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0,
                      delta=0.82, avg_price=22.50),
        _opt_position("AAPL", expiry_days=14, strike=175.0, qty=-1.0,
                      delta=0.28, avg_price=1.45, mark_price=1.20),
    ]
    # weekly chain present but NO leap-eligible expiry (>=365 DTE)
    broker = MockOptionBroker(
        option_positions=opt_positions,
        expiry_dates={"AAPL": [_future(14)]},
        calls={("AAPL", _future(14)): [_call(175.0, 0.28, 1.50, dte=14)]},
    )
    orders = await agent_logged.propose_orders_for_pair(
        broker, "AAPL", _roll_leap_analysis())
    assert orders == []
    assert _abort_event(cap_logger)["payload"]["reason"] == "sparse_chain_no_leap"


# ---- B4 (c): roll_leap aborts when LEAP found but no weekly for the new short ----
@pytest.mark.asyncio
async def test_b4c_roll_leap_aborts_when_no_weekly_for_new_leap(agent_logged, cap_logger, clear_earnings):
    # clear_earnings (Phase 2.5): B9 now runs FIRST on the roll_leap path — pin earnings
    # CLEAR so the B4 abort reason is reached, not a live-earnings flake (removes a live
    # get_next_earnings/yfinance call, ~4x/yr flake window).
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0,
                      delta=0.82, avg_price=22.50),
        _opt_position("AAPL", expiry_days=14, strike=175.0, qty=-1.0,
                      delta=0.28, avg_price=1.45, mark_price=1.20),
    ]
    # leap chain present; the weekly-window expiry exists but has NO calls
    broker = MockOptionBroker(
        option_positions=opt_positions,
        expiry_dates={"AAPL": [_future(14), _future(400)]},
        calls={("AAPL", _future(400)): [_call(130.0, 0.85, 25.0, dte=400)]},
    )
    orders = await agent_logged.propose_orders_for_pair(
        broker, "AAPL", _roll_leap_analysis())
    assert orders == []
    assert _abort_event(cap_logger)["payload"]["reason"] == \
        "sparse_chain_no_weekly_for_new_leap"


# ---- B4 (d): open_pmcc aborts when no weekly (never a LEAP without a short) ----
@pytest.mark.asyncio
async def test_b4d_open_pmcc_aborts_when_no_weekly(agent_logged, cap_logger):
    broker = MockOptionBroker(
        expiry_dates={"TESTX": [_future(14), _future(400)]},
        calls={("TESTX", _future(400)): [_call(130.0, 0.85, 25.0, dte=400)]},
    )
    orders, reason = await agent_logged._propose_open_pmcc("TESTX", broker, contracts=1)
    assert orders == []
    assert reason == "weekly_unavailable"
    assert _abort_event(cap_logger)["payload"]["reason"] == "sparse_chain_no_weekly_for_open"


# ---- B4: a scheduled scan on a sparse chain aborts rather than close-only ----
@pytest.mark.asyncio
async def test_b4_scan_roll_aborts_on_sparse_chain(agent_logged, cap_logger, clear_earnings):
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0, delta=0.82),
        _opt_position("AAPL", expiry_days=2, strike=175.0, qty=-1.0,
                      delta=0.28, avg_price=1.45, mark_price=1.20),
    ]
    broker = MockOptionBroker(option_positions=opt_positions)  # no chains
    orders = await agent_logged.scan(broker)
    assert orders == []                                        # no close-only leg shipped
    assert any(e["kind"] == "pmcc_roll_aborted" for e in cap_logger.events)


# ---- B1 (a): LLM HOLD is NOT overridden by the DTE<=2 trigger ----
@pytest.mark.asyncio
async def test_b1a_hold_not_overridden_at_terminal_dte(agent, monkeypatch):
    async def _fake_hold(pos, price, regime, vix=None):
        return PMCCAnalysis(symbol=pos.symbol, action="hold", confidence=0.8,
                            urgency="routine", summary="", rationale="")
    monkeypatch.setattr(agent, "_llm_analyze_position", _fake_hold)
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0, delta=0.82),
        _opt_position("AAPL", expiry_days=2, strike=175.0, qty=-1.0,
                      delta=0.28, avg_price=1.45, mark_price=1.20),
    ]
    broker = _broker_with_chains(["AAPL"], opt_positions)   # complete chain (COULD roll)
    orders = await agent.scan(broker)
    assert orders == []                                     # HOLD respected


# ---- B1 (b): LLM HOLD is NOT overridden by the >=50%-profit trigger ----
@pytest.mark.asyncio
async def test_b1b_hold_not_overridden_at_profit(agent, monkeypatch):
    async def _fake_hold(pos, price, regime, vix=None):
        return PMCCAnalysis(symbol=pos.symbol, action="hold", confidence=0.8,
                            urgency="routine", summary="", rationale="")
    monkeypatch.setattr(agent, "_llm_analyze_position", _fake_hold)
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0, delta=0.82),
        _opt_position("AAPL", expiry_days=30, strike=175.0, qty=-1.0,
                      delta=0.28, avg_price=1.45, mark_price=0.72),  # ~50% captured
    ]
    broker = _broker_with_chains(["AAPL"], opt_positions)
    orders = await agent.scan(broker)
    assert orders == []


# ---- B1 (c): with NO LLM verdict (unavailable), the deterministic roll fires ----
@pytest.mark.asyncio
async def test_b1c_deterministic_roll_fires_when_llm_unavailable(agent, clear_earnings):
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0, delta=0.82),
        _opt_position("AAPL", expiry_days=2, strike=175.0, qty=-1.0,
                      delta=0.28, avg_price=1.45, mark_price=1.20),
    ]
    broker = _broker_with_chains(["AAPL"], opt_positions)  # LLM stays None in tests
    orders = await agent.scan(broker)
    actions = {o.extra["action"] for o in orders}
    assert "roll_short_call_close" in actions and "roll_short_call_open" in actions


# ---- B1 (d): an LLM early roll still rolls (unchanged) ----
@pytest.mark.asyncio
async def test_b1d_llm_early_roll_still_rolls(agent, monkeypatch, clear_earnings):
    async def _fake_early(pos, price, regime, vix=None):
        return PMCCAnalysis(symbol=pos.symbol, action="roll_short_early",
                            confidence=0.8, urgency="routine", summary="", rationale="")
    monkeypatch.setattr(agent, "_llm_analyze_position", _fake_early)
    opt_positions = [
        _opt_position("AAPL", expiry_days=400, strike=150.0, qty=1.0, delta=0.82),
        _opt_position("AAPL", expiry_days=30, strike=175.0, qty=-1.0,  # healthy DTE
                      delta=0.28, avg_price=1.45, mark_price=1.30),
    ]
    # B7: 30-DTE short rolls OUT to the shared helper's 35-DTE weekly.
    broker = _broker_with_chains(["AAPL"], opt_positions)
    orders = await agent.scan(broker)
    # B7 tighten (was lenient subset `<=`): assert exact leg count + both named
    # legs. An early roll must produce the full close+open pair, nothing extra.
    assert len(orders) == 2, [o.extra.get("action") for o in orders]
    assert set(o.extra["action"] for o in orders) == {
        "roll_short_call_close", "roll_short_call_open"}


# ---- B1 (e): precedence rule (0-DTE HOLD is handled upstream by the time gate,
#      which rewrites HOLD->roll_short; _deterministic_roll_allowed then permits it) ----
def test_b1e_deterministic_roll_allowed_precedence(agent):
    def mk(action):
        return PMCCAnalysis(symbol="X", action=action, confidence=0.5,
                            urgency="routine", summary="", rationale="")
    assert agent._deterministic_roll_allowed(None) is True         # LLM down -> fallback
    assert agent._deterministic_roll_allowed(mk("hold")) is False  # HOLD respected
    assert agent._deterministic_roll_allowed(mk("watch")) is False
    assert agent._deterministic_roll_allowed(mk("roll_short")) is True   # 0-DTE gate rewrite
    assert agent._deterministic_roll_allowed(mk("roll_leap")) is True


# ---- B1 (f): analysis present but action empty/missing -> NOT an explicit
#      HOLD/WATCH, so the deterministic roll is ALLOWED (only an explicit
#      hold/watch suppresses it; an absent verdict must not silently block a
#      DTE<=2 roll). This pins the fall-through behavior. ----
def test_b1f_deterministic_roll_allowed_empty_or_missing_action(agent):
    def mk(action):
        return PMCCAnalysis(symbol="X", action=action, confidence=0.5,
                            urgency="routine", summary="", rationale="")
    assert agent._deterministic_roll_allowed(mk("")) is True       # empty string
    assert agent._deterministic_roll_allowed(mk(None)) is True     # missing/None
    assert agent._deterministic_roll_allowed(mk("HOLD")) is False  # case-insensitive guard


# ---- Phase-2 override contract (gate 0) ----
def test_override_kind_validation(agent):
    def mk(ov):
        return PMCCAnalysis(symbol="X", action="hold", confidence=0.5,
                            urgency="routine", summary="", rationale="", override=ov)
    assert agent._override_kind(mk({"kind": "hold_override", "reason": "MR play"})) == "hold_override"
    assert agent._override_kind(mk({"kind": "net_debit_justified", "reason": "x"})) == "net_debit_justified"
    assert agent._override_kind(mk({"kind": "earnings_override", "reason": "x"})) == "earnings_override"
    assert agent._override_kind(mk(None)) is None
    assert agent._override_kind(mk({"kind": "bogus", "reason": "x"})) is None            # unknown kind
    assert agent._override_kind(mk({"kind": "hold_override"})) is None                   # missing reason
    assert agent._override_kind(mk({"kind": "hold_override", "reason": "  "})) is None   # blank reason
    assert agent._override_kind(mk("not a dict")) is None                               # malformed
    assert agent._override_kind(None) is None


def test_hold_override_permits_deterministic_roll(agent):
    base = dict(symbol="X", action="hold", confidence=0.5, urgency="routine",
                summary="", rationale="")
    assert agent._deterministic_roll_allowed(PMCCAnalysis(**base)) is False
    assert agent._deterministic_roll_allowed(
        PMCCAnalysis(**base, override={"kind": "hold_override", "reason": "accel past prior range"})
    ) is True
    # a NON-hold override on a HOLD does not unblock it (wrong kind)
    assert agent._deterministic_roll_allowed(
        PMCCAnalysis(**base, override={"kind": "net_debit_justified", "reason": "x"})
    ) is False


# ---- B11: holiday guard skips full closures; calendar None fires (guard off) ----
def test_b11_scan_should_fire_skips_full_closure():
    from datetime import time as _t
    from trading_corp.main import _scan_should_fire
    win_s, win_e = _t(8, 30), _t(9, 25)
    dt = _et(9, 0)   # Friday 2026-05-01 09:00 ET, in window
    assert _scan_should_fire(dt, None, win_s, win_e, _FakeCalendar(closed=False)) is True
    assert _scan_should_fire(dt, None, win_s, win_e, _FakeCalendar(closed=True)) is False
    assert _scan_should_fire(dt, None, win_s, win_e, None) is True  # guard off = original


class _HolidayCal:
    """Date-aware calendar: close_time_et -> None on listed holidays, else 4pm ET."""
    def __init__(self, holidays):
        self.h = set(holidays)

    def close_time_et(self, when):
        from datetime import datetime as _dt
        from trading_corp.utils.time import ET
        d = when.date() if hasattr(when, "date") else when
        if d.isoformat() in self.h:
            return None
        return _dt(d.year, d.month, d.day, 16, 0, tzinfo=ET)


def test_b11_strict_tightening_over_a_month():
    """Walk every day of May 2026 (contains Memorial Day 05-25) through the OLD
    predicate and the new _scan_should_fire; assert the new one NEVER adds a
    fire-day and only ever removes full-closure weekdays."""
    from datetime import date as _d, datetime as _dtm, time as _t, timedelta as _td
    from trading_corp.utils.time import ET
    from trading_corp.main import _scan_should_fire
    win_s, win_e = _t(8, 30), _t(9, 25)
    cal = _HolidayCal({"2026-05-25"})   # Memorial Day (Monday)
    removed = []
    x = _d(2026, 5, 1)
    while x <= _d(2026, 5, 31):
        now = _dtm(x.year, x.month, x.day, 9, 0, tzinfo=ET)  # 09:00 ET, in window
        old = (now.weekday() < 5) and (win_s <= now.time() <= win_e) and (None != now.date())
        new = _scan_should_fire(now, None, win_s, win_e, cal)
        assert not (new and not old), f"{x}: new predicate ADDED a fire-day"
        if old and not new:
            removed.append(x.isoformat())
        x += _td(days=1)
    assert removed == ["2026-05-25"]   # the ONLY removed fire-day is the closure


# ============================================================================
# Phase 2.5 — B9 earnings + B2 short-leg credit gates on the roll_leap path.
# B7 (roll-out) + B4 (atomic legs) already covered roll_leap; these add the two
# gates that Phase 2 built into `_propose_roll_short` ONLY. Covered on BOTH
# sites: site 1 = propose_orders_for_pair, site 2 = the scan loop (which had NO
# prior roll_leap emit coverage — not assumed to parity site 1). The credit
# basis is the close-old-short / open-new-short pair ONLY; the LEAP legs (2+3)
# are B3's domain and are never part of this net.
# ============================================================================

def _roll_leap_credit_broker() -> MockOptionBroker:
    """MSTR roll_leap: old short mark 1.50, new weekly bid 1.95 (mark 2.00) →
    +0.45 conservative net CREDIT; qualifying new LEAP (delta 0.85, 500 DTE).
    Ships all 4 legs when the B9/B2 gates clear."""
    today = date.today()
    leap_exp = (today + timedelta(days=500)).isoformat()
    wk = (today + timedelta(days=14)).isoformat()
    return MockOptionBroker(
        option_positions=[
            _opt_position("MSTR", 100, 160.0, qty=1.0, delta=0.97,
                          avg_price=23.80, mark_price=58.05),
            _opt_position("MSTR", 7, 175.0, qty=-1.0, delta=0.30,
                          avg_price=2.50, mark_price=1.50),
        ],
        expiry_dates={"MSTR": [wk, leap_exp]},
        calls={
            ("MSTR", leap_exp): [_liquid_call(strike=180.0, delta=0.85, mark=20.0, dte=500)],
            ("MSTR", wk): [_liquid_call(strike=190.0, delta=0.30, mark=2.00, dte=14)],
        },
    )


def _roll_leap_debit_broker() -> MockOptionBroker:
    """Same MSTR roll_leap but the old short is expensive to buy back (mark 5.00)
    vs a cheap new weekly (bid 1.95) → conservative_net = 1.95 - 5.00 < 0 = a
    NET-DEBIT short leg. B2 must block unless net_debit_justified. (The LEAP swap
    legs are irrelevant to this net — that is B3's domain.)"""
    today = date.today()
    leap_exp = (today + timedelta(days=500)).isoformat()
    wk = (today + timedelta(days=14)).isoformat()
    return MockOptionBroker(
        option_positions=[
            _opt_position("MSTR", 100, 160.0, qty=1.0, delta=0.97,
                          avg_price=23.80, mark_price=58.05),
            _opt_position("MSTR", 7, 175.0, qty=-1.0, delta=0.30,
                          avg_price=2.50, mark_price=5.00),
        ],
        expiry_dates={"MSTR": [wk, leap_exp]},
        calls={
            ("MSTR", leap_exp): [_liquid_call(strike=180.0, delta=0.85, mark=20.0, dte=500)],
            ("MSTR", wk): [_liquid_call(strike=190.0, delta=0.30, mark=2.00, dte=14)],
        },
    )


def _roll_leap_no_short_broker() -> MockOptionBroker:
    """An uncovered LEAP (NO short leg) rolled to a new LEAP + fresh short. With
    no old short to buy back, close_mark=0 → conservative_net = new bid ≥ 0 =
    always a credit, so B2 passes without special-casing (the short_leg_expiry-
    is-None edge). Ships 3 legs (no close-short leg)."""
    today = date.today()
    leap_exp = (today + timedelta(days=500)).isoformat()
    wk = (today + timedelta(days=14)).isoformat()
    return MockOptionBroker(
        option_positions=[
            _opt_position("MSTR", 100, 160.0, qty=1.0, delta=0.97,
                          avg_price=23.80, mark_price=58.05),
        ],
        expiry_dates={"MSTR": [wk, leap_exp]},
        calls={
            ("MSTR", leap_exp): [_liquid_call(strike=180.0, delta=0.85, mark=20.0, dte=500)],
            ("MSTR", wk): [_liquid_call(strike=190.0, delta=0.30, mark=2.00, dte=14)],
        },
    )


def _rl_analysis(override=None, symbol="MSTR"):
    return PMCCAnalysis(symbol=symbol, action="roll_leap", confidence=0.9,
                        urgency="elevated", summary="", rationale="",
                        target_delta=0.30, target_dte=14, override=override)


def _patch_earnings(monkeypatch, days):
    """days=int → earnings that many days out (within-buffer if <=7); days=None →
    no data (fail-open / data_unavailable)."""
    from datetime import datetime, timezone, timedelta as _td
    fn = ((lambda symbol, *a, **k: None) if days is None
          else (lambda symbol, *a, **k: datetime.now(timezone.utc) + _td(days=days)))
    monkeypatch.setattr("trading_corp.utils.market_data.get_next_earnings", fn)


def _inject_roll_leap(monkeypatch, agent, override=None):
    """Scan (site 2) obtains its per-leg verdict from `_llm_analyze_position`;
    stub it to a roll_leap analysis so the scan roll_leap branch fires without an
    LLM call."""
    async def _fake(leg, price, regime, vix=None):
        return PMCCAnalysis(symbol=leg.symbol, action="roll_leap", confidence=0.9,
                            urgency="elevated", summary="", rationale="",
                            target_delta=0.30, target_dte=14, override=override)
    monkeypatch.setattr(agent, "_llm_analyze_position", _fake)


_RL_4 = ["roll_leap_close_short", "roll_leap_close", "roll_leap_open", "roll_leap_open_short"]


# ---- Site 1: propose_orders_for_pair ----

@pytest.mark.asyncio
async def test_b9_roll_leap_blocked_within_earnings_buffer(agent_logged, cap_logger, monkeypatch):
    """B9 (roll_leap, site 1): a LEAP roll inside the earnings buffer aborts (0
    legs) + reason earnings_window, gates.earnings == 'blocked'. B9 runs FIRST —
    before the LEAP is resolved (skill L257: the roll_leap's 4th leg opens new
    short premium)."""
    _patch_earnings(monkeypatch, 3)
    orders = await agent_logged.propose_orders_for_pair(
        _roll_leap_credit_broker(), "MSTR", _rl_analysis())
    assert orders == [], [o.extra.get("action") for o in orders]
    ev = _abort_event(cap_logger)
    assert ev["payload"]["reason"] == "earnings_window"
    assert ev["payload"]["gates"]["earnings"] == "blocked"


@pytest.mark.asyncio
async def test_b9_roll_leap_ships_with_earnings_override(agent, monkeypatch):
    """B9 (roll_leap): earnings_override lets a within-buffer LEAP roll proceed."""
    _patch_earnings(monkeypatch, 3)
    orders = await agent.propose_orders_for_pair(
        _roll_leap_credit_broker(), "MSTR",
        _rl_analysis(override={"kind": "earnings_override", "reason": "perpetual roll"}))
    assert [o.extra.get("action") for o in orders] == _RL_4


@pytest.mark.asyncio
async def test_b9_roll_leap_data_unavailable_recorded(agent_logged, cap_logger, monkeypatch):
    """B9 fail-open (roll_leap): no earnings data → the roll SHIPS but the
    shipped-roll pmcc_roll_gates audit records gates.earnings == 'data_unavailable'
    (distinguishable from a genuinely-clear ship)."""
    _patch_earnings(monkeypatch, None)
    orders = await agent_logged.propose_orders_for_pair(
        _roll_leap_credit_broker(), "MSTR", _rl_analysis())
    assert len(orders) == 4
    gate_ev = next(e for e in cap_logger.events if e["kind"] == "pmcc_roll_gates")
    assert gate_ev["payload"]["gates"]["earnings"] == "data_unavailable"
    assert gate_ev["payload"]["gates"]["credit"] == "clear"


@pytest.mark.asyncio
async def test_b2_roll_leap_net_debit_blocks(agent_logged, cap_logger, clear_earnings):
    """B2 (roll_leap, site 1): a net-debit new short (buy back old @5.00, sell new
    @~1.95) aborts (0 legs) + reason net_debit_roll; gates = earnings clear /
    selection ok / credit blocked."""
    orders = await agent_logged.propose_orders_for_pair(
        _roll_leap_debit_broker(), "MSTR", _rl_analysis())
    assert orders == [], [o.extra.get("action") for o in orders]
    ev = _abort_event(cap_logger)
    assert ev["payload"]["reason"] == "net_debit_roll"
    assert ev["payload"]["conservative_net"] < 0
    assert ev["payload"]["gates"] == {
        "earnings": "clear", "selection": "ok", "credit": "blocked"}


@pytest.mark.asyncio
async def test_b2_roll_leap_ships_with_net_debit_justified(agent, clear_earnings):
    """B2 (roll_leap): net_debit_justified authorizes the net-debit LEAP roll."""
    orders = await agent.propose_orders_for_pair(
        _roll_leap_debit_broker(), "MSTR",
        _rl_analysis(override={"kind": "net_debit_justified", "reason": "<=8% LEAP debit"}))
    assert [o.extra.get("action") for o in orders] == _RL_4


@pytest.mark.asyncio
async def test_b2_roll_leap_net_credit_ships_and_audits(agent_logged, cap_logger, clear_earnings):
    """B2 (roll_leap): a credit LEAP roll ships all 4 legs AND writes a
    pmcc_roll_gates audit with all-clear gates + the conservative/mark nets."""
    orders = await agent_logged.propose_orders_for_pair(
        _roll_leap_credit_broker(), "MSTR", _rl_analysis())
    assert [o.extra.get("action") for o in orders] == _RL_4
    gate_ev = next(e for e in cap_logger.events if e["kind"] == "pmcc_roll_gates")
    assert gate_ev["payload"]["gates"] == {
        "earnings": "clear", "selection": "ok", "credit": "clear"}
    assert gate_ev["payload"]["conservative_net"] > 0


@pytest.mark.asyncio
async def test_b2_roll_leap_no_old_short_passes(agent, clear_earnings):
    """B2 short_leg_expiry-is-None edge (roll_leap): an uncovered LEAP rolled to a
    new LEAP + fresh short has NO old short to buy back → close_mark=0 →
    conservative_net = new bid ≥ 0 = always a credit → B2 passes (3 legs, no
    close-short)."""
    orders = await agent.propose_orders_for_pair(
        _roll_leap_no_short_broker(), "MSTR", _rl_analysis())
    assert [o.extra.get("action") for o in orders] == [
        "roll_leap_close", "roll_leap_open", "roll_leap_open_short"]


@pytest.mark.asyncio
async def test_roll_leap_two_gate_interaction_earnings_first(agent_logged, cap_logger, monkeypatch):
    """Interaction (roll_leap): within-buffer earnings AND net-debit both trip, but
    B9 runs FIRST → aborts earnings_window with only `earnings` in the gates map
    (selection/credit not yet evaluated). A single net_debit_justified override
    can't unblock earnings → the conservative fail-safe abort still fires."""
    _patch_earnings(monkeypatch, 3)
    orders = await agent_logged.propose_orders_for_pair(
        _roll_leap_debit_broker(), "MSTR",
        _rl_analysis(override={"kind": "net_debit_justified", "reason": "..."}))
    assert orders == []
    ev = _abort_event(cap_logger)
    assert ev["payload"]["reason"] == "earnings_window"
    assert "selection" not in ev["payload"]["gates"]


# ---- Site 2: the scan loop (fresh coverage — no prior roll_leap scan tests) ----

@pytest.mark.asyncio
async def test_b9_scan_roll_leap_blocked_within_earnings_buffer(agent_logged, cap_logger, monkeypatch):
    """B9 (roll_leap, site 2 = scan): parity with site 1 — a within-buffer LEAP
    roll surfaced by the scan aborts + earnings_window; no roll_leap legs ship."""
    _patch_earnings(monkeypatch, 3)
    _inject_roll_leap(monkeypatch, agent_logged)
    orders = await agent_logged.scan(_roll_leap_credit_broker())
    assert not any(str(o.extra.get("action", "")).startswith("roll_leap") for o in orders)
    ev = _abort_event(cap_logger)
    assert ev["payload"]["reason"] == "earnings_window"


@pytest.mark.asyncio
async def test_b9_scan_roll_leap_ships_with_override(agent_logged, monkeypatch):
    """B9 (roll_leap, site 2): earnings_override lets the scan-surfaced within-buffer
    LEAP roll ship all 4 legs."""
    _patch_earnings(monkeypatch, 3)
    _inject_roll_leap(monkeypatch, agent_logged,
                      override={"kind": "earnings_override", "reason": "x"})
    orders = await agent_logged.scan(_roll_leap_credit_broker())
    assert [o.extra.get("action") for o in orders] == _RL_4


@pytest.mark.asyncio
async def test_b2_scan_roll_leap_net_debit_blocks(agent_logged, cap_logger, monkeypatch, clear_earnings):
    """B2 (roll_leap, site 2 = scan): a net-debit LEAP roll surfaced by the scan
    aborts + net_debit_roll; gates = earnings clear / selection ok / credit blocked."""
    _inject_roll_leap(monkeypatch, agent_logged)
    orders = await agent_logged.scan(_roll_leap_debit_broker())
    assert not any(str(o.extra.get("action", "")).startswith("roll_leap") for o in orders)
    ev = _abort_event(cap_logger)
    assert ev["payload"]["reason"] == "net_debit_roll"
    assert ev["payload"]["gates"] == {
        "earnings": "clear", "selection": "ok", "credit": "blocked"}


@pytest.mark.asyncio
async def test_b2_scan_roll_leap_net_credit_ships_and_audits(agent_logged, cap_logger, monkeypatch, clear_earnings):
    """B2 (roll_leap, site 2 = scan): a credit LEAP roll ships all 4 legs AND writes
    the pmcc_roll_gates audit with all-clear gates."""
    _inject_roll_leap(monkeypatch, agent_logged)
    orders = await agent_logged.scan(_roll_leap_credit_broker())
    assert [o.extra.get("action") for o in orders] == _RL_4
    gate_ev = next(e for e in cap_logger.events if e["kind"] == "pmcc_roll_gates")
    assert gate_ev["payload"]["gates"] == {
        "earnings": "clear", "selection": "ok", "credit": "clear"}


# ============================================================================
# Final phase (2026-07-22) — B3: record the old-LEAP sell mark (data fix),
# DECOUPLED from execution on the urgent path via `preserve_market_sell`.
# roll_leap_close -> real mark + limit-at-mark; close_leap_urgent -> record the
# mark for cost visibility BUT keep the 0.0 market-sell so an urgent close fills.
# ============================================================================

def _roll_leap_none_leapmark_broker() -> MockOptionBroker:
    """A roll_leap where the held LEAP has NO mark (`long_leg_mark` None) — the
    old-LEAP sell must record `mark_unavailable`, NOT a silent 0.0."""
    today = date.today()
    leap_exp = (today + timedelta(days=500)).isoformat()
    wk = (today + timedelta(days=14)).isoformat()
    return MockOptionBroker(
        option_positions=[
            _opt_position("MSTR", 100, 160.0, qty=1.0, delta=0.97, avg_price=23.80),  # mark_price None
            _opt_position("MSTR", 7, 175.0, qty=-1.0, delta=0.30, avg_price=2.50, mark_price=1.50),
        ],
        expiry_dates={"MSTR": [wk, leap_exp]},
        calls={
            ("MSTR", leap_exp): [_liquid_call(strike=180.0, delta=0.85, mark=20.0, dte=500)],
            ("MSTR", wk): [_liquid_call(strike=190.0, delta=0.30, mark=2.00, dte=14)],
        },
    )


def _close_all_broker() -> MockOptionBroker:
    """A held MSTR PMCC pair (LEAP mark 58.05) for action=close_all → close_short
    + close_leap_urgent (a market sell)."""
    return MockOptionBroker(
        option_positions=[
            _opt_position("MSTR", 400, 160.0, qty=1.0, delta=0.97, avg_price=23.80, mark_price=58.05),
            _opt_position("MSTR", 7, 175.0, qty=-1.0, delta=0.30, avg_price=2.50, mark_price=1.50),
        ],
    )


def _leg_by_action(orders, action):
    return next(o for o in orders if o.extra.get("action") == action)


@pytest.mark.asyncio
async def test_b3_roll_leap_close_records_real_mark_limit_at_mark(agent, clear_earnings):
    """B3: the roll_leap_close (old-LEAP sell) records the real held-LEAP mark
    (`pos.long_leg_mark` = 58.05) as mark_per_share AND prices the leg limit-at-mark
    (58.05) — consistent with every other roll leg (was 0.0 for both)."""
    orders = await agent.propose_orders_for_pair(
        _roll_leap_credit_broker(), "MSTR", _rl_analysis())
    close_leap = _leg_by_action(orders, "roll_leap_close")
    assert close_leap.extra["mark_per_share"] == 58.05          # recorded, not 0.0
    assert close_leap.limit_price == 58.05                      # limit-at-mark
    assert "mark_unavailable" not in close_leap.extra


@pytest.mark.asyncio
async def test_b3_unavailable_mark_records_flag_not_zero(agent, clear_earnings):
    """B3: when the held LEAP has no mark, the roll_leap_close leg records
    mark_unavailable=True + mark_per_share None (DISTINGUISHABLE) — never a silent 0.0."""
    orders = await agent.propose_orders_for_pair(
        _roll_leap_none_leapmark_broker(), "MSTR", _rl_analysis())
    close_leap = _leg_by_action(orders, "roll_leap_close")
    assert close_leap.extra["mark_unavailable"] is True
    assert close_leap.extra["mark_per_share"] is None           # not 0.0
    assert close_leap.limit_price is None                       # no phantom 0.0 limit


@pytest.mark.asyncio
async def test_b3_close_leap_urgent_records_mark_but_keeps_market_sell(agent):
    """B3 decoupling (the whole point of the split): close_leap_urgent RECORDS the
    real mark (cost visibility) BUT PRESERVES the market-sell (limit_price 0.0) so
    an urgent structural close still fills. Assert BOTH."""
    orders = await agent.propose_orders_for_pair(
        _close_all_broker(), "MSTR",
        PMCCAnalysis(symbol="MSTR", action="close_all", confidence=0.9,
                     urgency="urgent", summary="", rationale=""))
    close_leap = _leg_by_action(orders, "close_leap_urgent")
    assert close_leap.extra["mark_per_share"] == 58.05          # recorded for cost visibility
    assert close_leap.limit_price == 0.0                        # market-sell PRESERVED (must fill)


# ============================================================================
# Final phase (2026-07-22) — B10: the 15:00-ET terminal-DTE pass evaluates ONLY
# 0-DTE positions (a subset filter, not a second full scan). The scheduler wiring
# lives in main.py (compile-verified); these cover the scan-level subset filter.
# ============================================================================

def _mixed_dte_broker() -> MockOptionBroker:
    """AAPL short is 0-DTE (terminal); MSTR short is 7-DTE (non-terminal). Both are
    covered pairs held in the account."""
    return MockOptionBroker(option_positions=[
        _opt_position("AAPL", 400, 150.0, qty=1.0, delta=0.82, avg_price=22.5, mark_price=30.0),
        _opt_position("AAPL", 0, 175.0, qty=-1.0, delta=0.30, avg_price=1.45, mark_price=1.20),
        _opt_position("MSTR", 400, 160.0, qty=1.0, delta=0.85, avg_price=23.8, mark_price=58.05),
        _opt_position("MSTR", 7, 175.0, qty=-1.0, delta=0.30, avg_price=2.50, mark_price=1.50),
    ])


@pytest.mark.asyncio
async def test_b10_scan_zero_dte_only_filters_to_terminal(agent_logged, monkeypatch):
    """B10: scan(zero_dte_only=True) evaluates ONLY 0-DTE positions — the non-0-DTE
    leg (MSTR, 7 DTE) is never even analyzed. Subset filter, not a full scan."""
    analyzed = []
    async def _fake(leg, price, regime, vix=None):
        analyzed.append(leg.symbol)
        return PMCCAnalysis(symbol=leg.symbol, action="hold", confidence=0.8,
                            urgency="routine", summary="", rationale="")
    monkeypatch.setattr(agent_logged, "_llm_analyze_position", _fake)
    await agent_logged.scan(_mixed_dte_broker(), zero_dte_only=True)
    assert analyzed == ["AAPL"]   # MSTR (7-DTE) never evaluated at 15:00


@pytest.mark.asyncio
async def test_b10_scan_skip_symbols_suppresses_pending(agent_logged, monkeypatch):
    """B10: skip_symbols (positions already in the HITL queue) are suppressed — not
    re-analyzed and not re-proposed at 15:00."""
    analyzed = []
    async def _fake(leg, price, regime, vix=None):
        analyzed.append(leg.symbol)
        return PMCCAnalysis(symbol=leg.symbol, action="hold", confidence=0.8,
                            urgency="routine", summary="", rationale="")
    monkeypatch.setattr(agent_logged, "_llm_analyze_position", _fake)
    orders = await agent_logged.scan(
        _mixed_dte_broker(), zero_dte_only=True, skip_symbols={"AAPL"})
    assert analyzed == []          # AAPL (the only 0-DTE) suppressed as pending
    assert orders == []


@pytest.mark.asyncio
async def test_b10_scan_zero_dte_only_no_new_opens(agent_logged, monkeypatch):
    """B10: the terminal pass never proposes NEW opens — with a held 0-DTE leg but a
    non-empty stock universe, zero_dte_only evaluates only the 0-DTE leg (no opens)."""
    analyzed = []
    async def _fake(leg, price, regime, vix=None):
        analyzed.append(leg.symbol)
        return PMCCAnalysis(symbol=leg.symbol, action="hold", confidence=0.8,
                            urgency="routine", summary="", rationale="")
    monkeypatch.setattr(agent_logged, "_llm_analyze_position", _fake)
    # A stock position (would normally seed a new-open in a full scan) + a 0-DTE leg.
    broker = _mixed_dte_broker()
    broker._stock_positions = [_stock_pos("NVDA", qty=100)]
    await agent_logged.scan(broker, zero_dte_only=True)
    assert "NVDA" not in analyzed  # no new-open evaluation at 15:00
    assert analyzed == ["AAPL"]


def test_b10_pmcc_pending_symbols_real_detail_shape():
    """B10 suppress: `_pmcc_pending_symbols` extracts the symbol from the REAL
    ApprovalRequest.detail shape — ceo_graph builds `detail['order'] = order.to_db_row()`,
    so the symbol is at detail['order']['symbol'] (NOT a top-level detail['symbol']). Built
    from real objects, not a hand-shaped dict that matches a guess. A non-pmcc pending entry
    is ignored."""
    from trading_corp.main import _pmcc_pending_symbols
    from trading_corp.graph.interrupts import ApprovalRequest
    from trading_corp.comms.pending_registry import PendingEntry
    from trading_corp.persistence.models import ProposedOrder
    pmcc_order = ProposedOrder(strategy="robinhood_pmcc", symbol="AAPL", side="buy", qty=1.0)
    pmcc_entry = PendingEntry(
        request=ApprovalRequest(
            order_id=pmcc_order.id, summary="roll",
            detail={"order": pmcc_order.to_db_row(), "risk_verdict": {},
                    "division": "robinhood_pmcc"}),
        future=object(), division="robinhood_pmcc")
    other_order = ProposedOrder(strategy="bitunix_futures", symbol="BTCUSDT", side="buy", qty=1.0)
    other_entry = PendingEntry(
        request=ApprovalRequest(
            order_id=other_order.id, summary="x",
            detail={"order": other_order.to_db_row(), "division": "bitunix_futures"}),
        future=object(), division="bitunix_futures")
    assert _pmcc_pending_symbols([pmcc_entry, other_entry]) == {"AAPL"}


def _weekday_on_or_after(d):
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def test_b10_terminal_should_fire_normal_day():
    """B10: on a 4pm-close day the terminal pass opens at 15:00 ET (close-60m), stays open
    until 16:00, and dedups — identical to a wall-clock 15:00 but DERIVED from the calendar."""
    from trading_corp.main import _terminal_should_fire
    from trading_corp.utils.time import ET
    from datetime import datetime
    cal = _FakeCalendar(close_hour=16)
    d = _weekday_on_or_after(date(2026, 7, 20))
    def at(h, m):
        return datetime(d.year, d.month, d.day, h, m, tzinfo=ET)
    assert _terminal_should_fire(at(14, 59), None, cal) is False   # too early
    assert _terminal_should_fire(at(15, 0), None, cal) is True     # opens at close-60m
    assert _terminal_should_fire(at(15, 30), None, cal) is True    # still inside the window
    assert _terminal_should_fire(at(16, 0), None, cal) is False    # at/after close
    assert _terminal_should_fire(at(15, 0), d, cal) is False       # dedup: already fired today


def test_b10_terminal_should_fire_half_day():
    """B10: on a 1pm-close HALF-DAY the window opens at 12:00 ET (close-60m), NOT wall-clock
    15:00 — a fixed 15:00 would run post-close and no-fire (the B11-shaped bug). Closed day
    never fires."""
    from trading_corp.main import _terminal_should_fire
    from trading_corp.utils.time import ET
    from datetime import datetime
    cal = _FakeCalendar(close_hour=13)
    d = _weekday_on_or_after(date(2026, 7, 20))
    def at(h, m):
        return datetime(d.year, d.month, d.day, h, m, tzinfo=ET)
    assert _terminal_should_fire(at(11, 59), None, cal) is False
    assert _terminal_should_fire(at(12, 0), None, cal) is True     # opens at close-60m = 12:00
    assert _terminal_should_fire(at(13, 0), None, cal) is False    # at/after the 1pm close
    assert _terminal_should_fire(at(15, 0), None, cal) is False    # wall-clock 15:00 = post-close: NO fire
    assert _terminal_should_fire(at(12, 0), None, _FakeCalendar(closed=True)) is False
