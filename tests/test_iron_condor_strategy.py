"""Tests for the robinhood_joint_iron_condor strategy module (step 9).

Uses a fake broker that returns deterministic option chains, Greeks, and
expirations. Tests focus on decision-tree branch coverage, portfolio
preflight, tested-side identification, term-structure, ex-div, circuit
breaker reset semantics, startup catch-up, cadence computation, and the
on_combo_filled state-update callback.

Strategy config is injected via per-test tmp strategies.yaml fixtures so
no production config is touched.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_corp.agents.strategies.robinhood_joint_iron_condor import (
    AGENT_STATE_KEY,
    RobinhoodJointIronCondorAgent,
    STRATEGY_SLUG,
    _CADENCE_IDLE,
    _CADENCE_TESTED,
    _CADENCE_WARN,
)
from trading_corp.data.ex_dividend_calendar import ExDividendCalendar
from trading_corp.data.macro_calendar import MacroCalendar
from trading_corp.persistence import db
from trading_corp.persistence.models import FillEvent


# ---------------------------------------------------------------------------
# Schema init (mirror the place_combo test pattern)
# ---------------------------------------------------------------------------


def _ensure_schema(db_url: str) -> None:
    from trading_corp.persistence.db import SCHEMA
    with db.connect(db_url) as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def strategies_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "strategies.yaml"
    p.write_text(
        """
robinhood_joint_iron_condor:
  enabled: true
  auto_execute: false
  division: robinhood_joint
  universe: [SPY]
  entry:
    target_dte: 45
    short_delta: 0.16
    min_credit_pct_of_width: 0.33
    min_ivr: 30
    min_ivp: 50
    term_structure_max_diff: 0.05
  wing_widths:
    SPY: 3.0
  portfolio_caps:
    max_per_trade_pct: 0.05
    max_bp_pct: 0.40
    max_concurrent: 3
    max_correlated: 2
  management:
    profit_target_pct: 0.50
    force_close_dte: 21
    short_dte_force_close: 7
    hard_stop_credit_mult: 2.00
    catastrophic_stop_account_pct: 0.10
    tested_delta_warn: 0.25
    tested_delta_adjust: 0.30
    tested_delta_close_side: 0.35
    tested_side_neutral_band: 0.05
    max_adjustments: 1
    min_dte_for_adjustment: 14
    ex_div_force_close_within_trading_days: 3
    ex_div_force_close_short_call_delta: 0.25
    adjustment_roll_target_short_delta: 0.30
  circuit_breaker:
    consecutive_loss_pause: 3
    drawdown_pct_pause: 0.15
    pause_days: 5
  paper_simulation:
    per_leg_slippage_dollars: 0.03
""".strip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def macro_cal(tmp_path: Path) -> MacroCalendar:
    p = tmp_path / "macro_empty.yaml"
    p.write_text("events: []\n", encoding="utf-8")
    return MacroCalendar.load(p)


@pytest.fixture
def exdiv_cal(tmp_path: Path) -> ExDividendCalendar:
    p = tmp_path / "exdiv_empty.yaml"
    p.write_text("ex_dividends: []\n", encoding="utf-8")
    return ExDividendCalendar.load(p)


@pytest.fixture
def agent(strategies_yaml, macro_cal, exdiv_cal, tmp_db):
    _ensure_schema(tmp_db)
    a = RobinhoodJointIronCondorAgent(
        strategies_yaml=strategies_yaml,
        macro_calendar=macro_cal,
        ex_dividend_calendar=exdiv_cal,
        db_url=tmp_db,
        clock_fn=lambda: datetime(2026, 5, 15, 15, 0, tzinfo=timezone.utc),
    )
    return a


def _option(*, strike: float, delta: float, mark: float = 0.50,
            option_id: str | None = None,
            expiration: str = "2026-06-19") -> dict:
    return {
        "option_id": option_id or f"OPT-{strike}-{delta}",
        "expiration_date": expiration,
        "strike_price": strike,
        "delta": delta,
        "mark_price": mark,
        "bid": mark - 0.02,
        "ask": mark + 0.02,
        "open_interest": 1000,
        "volume": 500,
        "implied_volatility": 0.25,
        "theta": -0.02,
        "gamma": 0.01,
        "vega": 0.10,
        "dte": 45,
    }


def _fake_broker(*,
                 equity: float = 5000.0,
                 spot: float = 450.0,
                 expirations: list[str] | None = None,
                 calls: list[dict] | None = None,
                 puts: list[dict] | None = None,
                 greeks: dict[str, dict] | None = None) -> MagicMock:
    b = MagicMock()

    snap = MagicMock()
    snap.equity = equity
    b.snapshot = AsyncMock(return_value=snap)
    b.quote = AsyncMock(return_value=spot)
    b.get_expiration_dates = AsyncMock(return_value=expirations or ["2026-06-19"])
    b.get_calls_for_expiry = AsyncMock(return_value=calls or [])
    b.get_puts_for_expiry = AsyncMock(return_value=puts or [])

    g = greeks or {}
    async def _gk(opt_id):
        return g.get(opt_id, {
            "delta": None, "gamma": None, "theta": None,
            "vega": None, "iv": None, "mark_price": None,
        })
    b.get_option_greeks = _gk
    return b


# ---------------------------------------------------------------------------
# Empty scan is not an error + telemetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_returns_empty_when_disabled(strategies_yaml, macro_cal,
                                                exdiv_cal, tmp_db):
    _ensure_schema(tmp_db)
    # Override enabled=false
    strategies_yaml.write_text(
        strategies_yaml.read_text().replace("enabled: true", "enabled: false"),
        encoding="utf-8",
    )
    a = RobinhoodJointIronCondorAgent(
        strategies_yaml=strategies_yaml, macro_calendar=macro_cal,
        ex_dividend_calendar=exdiv_cal, db_url=tmp_db,
    )
    out = await a.scan(_fake_broker())
    assert out == []


@pytest.mark.asyncio
async def test_scan_skips_when_vix_above_30(agent):
    with patch(
        "trading_corp.agents.strategies.robinhood_joint_iron_condor.get_vix",
        return_value=42.0,
    ):
        out = await agent.scan(_fake_broker())
    assert out == []
    state = agent.load_state()
    bucket = list(state["scan_telemetry"].values())[0]
    assert "SPY" in bucket
    assert "vix_above_30" in bucket["SPY"]["by_reason"]


@pytest.mark.asyncio
async def test_scan_skips_during_macro_halt_window(strategies_yaml,
                                                   exdiv_cal, tmp_path, tmp_db):
    _ensure_schema(tmp_db)
    # Macro yaml with an event at "now".
    p = tmp_path / "macro.yaml"
    p.write_text(
        "events:\n"
        '  - ts: "2026-05-15T15:00:00Z"\n'
        '    impact: "high"\n'
        '    name: "Test FOMC"\n',
        encoding="utf-8",
    )
    mc = MacroCalendar.load(p)
    a = RobinhoodJointIronCondorAgent(
        strategies_yaml=strategies_yaml, macro_calendar=mc,
        ex_dividend_calendar=exdiv_cal, db_url=tmp_db,
        clock_fn=lambda: datetime(2026, 5, 15, 15, 0, tzinfo=timezone.utc),
    )
    with patch(
        "trading_corp.agents.strategies.robinhood_joint_iron_condor.get_vix",
        return_value=18.0,
    ):
        out = await a.scan(_fake_broker())
    assert out == []
    state = a.load_state()
    bucket = list(state["scan_telemetry"].values())[0]
    assert any("macro_halt" in r["by_reason"] for r in bucket.values())


@pytest.mark.asyncio
async def test_scan_returns_empty_when_iv_rank_below_threshold(agent):
    with patch(
        "trading_corp.agents.strategies.robinhood_joint_iron_condor.get_vix",
        return_value=18.0,
    ), patch(
        "trading_corp.agents.strategies.robinhood_joint_iron_condor.calc_iv_rank",
        new=AsyncMock(return_value=0.10),     # 10% — below 30 threshold
    ):
        out = await agent.scan(_fake_broker())
    assert out == []
    state = agent.load_state()
    bucket = list(state["scan_telemetry"].values())[0]
    assert "ivr_below_30" in bucket["SPY"]["by_reason"]


@pytest.mark.asyncio
async def test_scan_constructs_ic_on_happy_path(agent):
    # Calls with deltas spanning the range; ATM ~450 spot.
    # Short marks chosen so net credit ≥ 1/3 × wing_width (3.0) = $1.00.
    # Agent clock is 2026-05-15; 45 DTE → 2026-06-29.
    expiry = "2026-06-29"
    calls = [
        _option(strike=470, delta=0.16, mark=1.10, option_id="C-470", expiration=expiry),
        _option(strike=473, delta=0.08, mark=0.20, option_id="C-473", expiration=expiry),
        _option(strike=465, delta=0.25, mark=1.80, option_id="C-465", expiration=expiry),
    ]
    puts = [
        _option(strike=430, delta=-0.16, mark=1.10, option_id="P-430", expiration=expiry),
        _option(strike=427, delta=-0.08, mark=0.20, option_id="P-427", expiration=expiry),
        _option(strike=435, delta=-0.25, mark=1.80, option_id="P-435", expiration=expiry),
    ]
    broker = _fake_broker(calls=calls, puts=puts, expirations=[expiry])

    with patch(
        "trading_corp.agents.strategies.robinhood_joint_iron_condor.get_vix",
        return_value=18.0,
    ), patch(
        "trading_corp.agents.strategies.robinhood_joint_iron_condor.calc_iv_rank",
        new=AsyncMock(return_value=0.55),
    ), patch(
        "trading_corp.agents.strategies.robinhood_joint_iron_condor.calc_atm_iv",
        new=AsyncMock(return_value=0.20),    # term structure flat → pass
    ):
        out = await agent.scan(broker)

    assert len(out) == 1
    combo = out[0]
    assert len(combo) == 4
    roles = [o.extra["combo_role"] for o in combo]
    assert sorted(roles) == ["long_call", "long_put", "short_call", "short_put"]
    # All share combo_id
    combo_ids = {o.extra["combo_id"] for o in combo}
    assert len(combo_ids) == 1
    # Net credit = 1.10 + 1.10 - 0.20 - 0.20 = 1.80 ≥ 0.33 × 3.0 = 0.99 ✓
    assert all(o.extra["combo_direction"] == "credit" for o in combo)


# ---------------------------------------------------------------------------
# Decision-tree branches — set up state directly, mock broker per branch
# ---------------------------------------------------------------------------


def _seed_open_ic(
    agent: RobinhoodJointIronCondorAgent, *,
    combo_id: str = "ic-1",
    symbol: str = "SPY",
    expiration: str = "2026-06-19",
    credit: float = 1.20,
    wing: float = 3.0,
    contracts: int = 1,
    short_put_strike: float = 430,
    long_put_strike: float = 427,
    short_call_strike: float = 470,
    long_call_strike: float = 473,
    adjustment_count: int = 0,
    short_put_delta: float = -0.16,
    short_call_delta: float = 0.16,
) -> dict:
    state = agent.load_state()
    state["open_ics"][combo_id] = {
        "symbol": symbol,
        "expiration": expiration,
        "contracts": contracts,
        "wing_width": wing,
        "credit_at_entry": credit,
        "dte_at_entry": 45,
        "ivr_at_entry": 0.55,
        "max_loss_per_contract": (wing - credit) * 100,
        "short_put_strike": short_put_strike,
        "long_put_strike": long_put_strike,
        "short_call_strike": short_call_strike,
        "long_call_strike": long_call_strike,
        "short_put_option_id": "P-SP",
        "long_put_option_id": "P-LP",
        "short_call_option_id": "C-SC",
        "long_call_option_id": "C-LC",
        "short_put_delta_at_entry": short_put_delta,
        "short_call_delta_at_entry": short_call_delta,
        "long_put_delta_at_entry": -0.08,
        "long_call_delta_at_entry": 0.08,
        "adjustment_count": adjustment_count,
        "opened_ts": "2026-04-30T15:00:00",
        "session_start_mark": 0.0,
        "session_start_date": "2026-05-15",
    }
    agent.persist_state(state)
    return state["open_ics"][combo_id]


@pytest.mark.asyncio
async def test_branch_1_profit_target_closes_ic(agent):
    """Profit target 50% → close. Credit 1.20, close cost 0.55 →
    P&L 0.65 ≥ 0.60 = 50% × credit."""
    _seed_open_ic(agent)
    greeks = {
        # Close cost = 0.60 + 0.20 - 0.15 - 0.10 = 0.55 → P&L 0.65 ≥ 0.60
        "P-SP": {"mark_price": 0.20, "delta": -0.10},
        "P-LP": {"mark_price": 0.15, "delta": -0.05},
        "C-SC": {"mark_price": 0.60, "delta": 0.12},
        "C-LC": {"mark_price": 0.10, "delta": 0.04},
    }
    broker = _fake_broker(greeks=greeks)
    actions, _ = await agent.manage(broker)
    assert len(actions) == 1
    intents = {o.extra["combo_intent"] for o in actions[0]}
    assert intents == {"profit_target"}


@pytest.mark.asyncio
async def test_branch_2_force_close_at_21_dte(agent):
    """DTE ≤ force_close_dte (21) → close."""
    # Use an expiration 18 days out.
    target = (agent._clock().date() + timedelta(days=18)).isoformat()
    ic = _seed_open_ic(agent, expiration=target)
    greeks = {
        "P-SP": {"mark_price": 0.50, "delta": -0.12},
        "P-LP": {"mark_price": 0.20, "delta": -0.05},
        "C-SC": {"mark_price": 0.50, "delta": 0.12},
        "C-LC": {"mark_price": 0.20, "delta": 0.05},
    }
    broker = _fake_broker(greeks=greeks)
    actions, _ = await agent.manage(broker)
    assert len(actions) == 1
    assert {o.extra["combo_intent"] for o in actions[0]} == {"force_close_dte"}


@pytest.mark.asyncio
async def test_branch_3_late_dte_force_close(agent):
    """DTE < short_dte_force_close (7) → close, severity warning tag.

    Close cost must be high enough that branch 1 (profit target) doesn't
    fire first: P&L < 50% × credit → close_cost > 50% × credit.
    """
    target = (agent._clock().date() + timedelta(days=5)).isoformat()
    _seed_open_ic(agent, expiration=target, credit=1.20)
    # close_cost = 0.50 + 0.50 - 0.10 - 0.10 = 0.80; P&L = 0.40 < 0.60.
    greeks = {
        "P-SP": {"mark_price": 0.50, "delta": -0.10},
        "P-LP": {"mark_price": 0.10, "delta": -0.03},
        "C-SC": {"mark_price": 0.50, "delta": 0.10},
        "C-LC": {"mark_price": 0.10, "delta": 0.03},
    }
    broker = _fake_broker(greeks=greeks)
    actions, _ = await agent.manage(broker)
    assert len(actions) == 1
    assert {o.extra["combo_intent"] for o in actions[0]} == {"late_dte_force_close"}


@pytest.mark.asyncio
async def test_branch_4_ex_div_force_close(strategies_yaml, macro_cal,
                                           tmp_path, tmp_db):
    """Ex-div within 3 trading days AND short call delta > 0.25 → close."""
    _ensure_schema(tmp_db)
    clock = datetime(2026, 5, 15, 15, 0, tzinfo=timezone.utc)
    ex_date = (clock.date() + timedelta(days=2)).isoformat()
    p = tmp_path / "exdiv.yaml"
    p.write_text(
        f"ex_dividends:\n"
        f"  - symbol: SPY\n"
        f"    ex_date: \"{ex_date}\"\n"
        f"    pay_date: \"\"\n"
        f"    confirmed: true\n"
        f"    source: test\n",
        encoding="utf-8",
    )
    exdiv = ExDividendCalendar.load(p)
    a = RobinhoodJointIronCondorAgent(
        strategies_yaml=strategies_yaml, macro_calendar=macro_cal,
        ex_dividend_calendar=exdiv, db_url=tmp_db, clock_fn=lambda: clock,
    )
    _seed_open_ic(a)
    greeks = {
        "P-SP": {"mark_price": 0.30, "delta": -0.10},
        "P-LP": {"mark_price": 0.10, "delta": -0.03},
        "C-SC": {"mark_price": 1.20, "delta": 0.32},      # ITM-trending → close
        "C-LC": {"mark_price": 0.20, "delta": 0.10},
    }
    broker = _fake_broker(greeks=greeks)
    actions, _ = await a.manage(broker)
    assert len(actions) == 1
    assert {o.extra["combo_intent"] for o in actions[0]} == {"ex_div_force_close"}


@pytest.mark.asyncio
async def test_branch_4_ex_div_does_not_fire_when_short_call_low_delta(
        strategies_yaml, macro_cal, tmp_path, tmp_db):
    """Ex-div imminent but short call delta still 0.16 → no force close."""
    _ensure_schema(tmp_db)
    clock = datetime(2026, 5, 15, 15, 0, tzinfo=timezone.utc)
    ex_date = (clock.date() + timedelta(days=2)).isoformat()
    p = tmp_path / "exdiv.yaml"
    p.write_text(
        f"ex_dividends:\n"
        f"  - symbol: SPY\n"
        f"    ex_date: \"{ex_date}\"\n"
        f"    pay_date: \"\"\n"
        f"    confirmed: true\n"
        f"    source: test\n",
        encoding="utf-8",
    )
    exdiv = ExDividendCalendar.load(p)
    a = RobinhoodJointIronCondorAgent(
        strategies_yaml=strategies_yaml, macro_calendar=macro_cal,
        ex_dividend_calendar=exdiv, db_url=tmp_db, clock_fn=lambda: clock,
    )
    _seed_open_ic(a)
    greeks = {
        "P-SP": {"mark_price": 0.30, "delta": -0.10},
        "P-LP": {"mark_price": 0.10, "delta": -0.03},
        "C-SC": {"mark_price": 0.50, "delta": 0.18},      # under 0.25
        "C-LC": {"mark_price": 0.20, "delta": 0.06},
    }
    broker = _fake_broker(greeks=greeks)
    actions, _ = await a.manage(broker)
    # No ex-div close; might match another branch or not. Verify it
    # wasn't ex_div_force_close at least.
    if actions:
        intents = {o.extra["combo_intent"] for o in actions[0]}
        assert "ex_div_force_close" not in intents


@pytest.mark.asyncio
async def test_branch_4_5_hard_stop_fires_independent_of_tested_side(agent):
    """Combo P&L ≤ -2x credit, neither short above 0.35 Δ → branch 4.5
    must still fire (it's evaluated BEFORE tested-side identification)."""
    _seed_open_ic(agent, credit=1.00)
    greeks = {
        # Close cost = 1.50 + 0.20 + 1.60 - 0.30 = 3.00. Wait:
        # close_cost = short_call + short_put - long_call - long_put
        #            = 1.60 + 1.50 - 0.30 - 0.20 = 2.60
        # P&L = credit_at_entry - close_cost = 1.00 - 2.60 = -1.60
        # hard_stop threshold = -2 * 1.00 = -2.00 → -1.60 > -2.00, not hit.
        # Need close cost ≥ 3.00 → P&L ≤ -2.00. Set shorts at 1.80 each.
        "P-SP": {"mark_price": 1.80, "delta": -0.18},
        "P-LP": {"mark_price": 0.20, "delta": -0.05},
        "C-SC": {"mark_price": 1.80, "delta": 0.18},      # not tested (Δ<0.30)
        "C-LC": {"mark_price": 0.20, "delta": 0.05},
    }
    broker = _fake_broker(greeks=greeks)
    actions, _ = await agent.manage(broker)
    assert len(actions) == 1
    assert {o.extra["combo_intent"] for o in actions[0]} == {"hard_stop"}


@pytest.mark.asyncio
async def test_branch_0_catastrophic_stop_closes_all_open_ics(agent):
    """Total session P&L below -10% equity → close ALL open ICs."""
    # Two open positions.
    _seed_open_ic(agent, combo_id="ic-a", credit=1.20)
    _seed_open_ic(agent, combo_id="ic-b", credit=1.20,
                   symbol="SPY", short_put_strike=420, long_put_strike=417,
                   short_call_strike=480, long_call_strike=483)
    # Force the second IC's symbol to be unique so preflight is OK
    state = agent.load_state()
    state["open_ics"]["ic-b"]["symbol"] = "QQQ"
    # Set session_start_marks 0.0 so current MTM losses drag total down.
    for k in ("ic-a", "ic-b"):
        state["open_ics"][k]["session_start_mark"] = 0.0
        state["open_ics"][k]["session_start_date"] = agent._clock().date().isoformat()
    agent.persist_state(state)

    # Equity 5000; each combo at -$300 session P&L → total -$600 → -12% > -10%.
    # close_cost - credit = mtm loss per share; ×100×contracts = dollars.
    # We want mtm_now − session_start_mark = -3.0 per share per combo
    # → -3.00 × 100 × 1 contract = -$300 per combo → -$600 total → -12% ✓
    # mtm_now = credit - close_cost; want mtm_now = -3.0 → close_cost = credit + 3.0 = 4.20
    # close_cost = short_call + short_put - long_call - long_put = 4.20
    greeks = {
        "P-SP": {"mark_price": 2.20, "delta": -0.20},
        "P-LP": {"mark_price": 0.10, "delta": -0.05},
        "C-SC": {"mark_price": 2.20, "delta": 0.20},
        "C-LC": {"mark_price": 0.10, "delta": 0.05},
    }
    broker = _fake_broker(equity=5000.0, greeks=greeks)
    actions, _ = await agent.manage(broker)
    assert len(actions) == 2
    intents = [o.extra["combo_intent"] for combo in actions for o in combo]
    assert all(i == "catastrophic_stop" for i in intents)


# ---------------------------------------------------------------------------
# Tested-side identification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tested_side_neither_when_both_within_band(agent):
    ic = _seed_open_ic(agent)
    greeks = {
        "P-SP": {"mark_price": 0.50, "delta": -0.18},   # within 0.05 of -0.16
        "C-SC": {"mark_price": 0.50, "delta": 0.18},    # within 0.05 of 0.16
    }
    broker = _fake_broker(greeks=greeks)
    side = await agent._identify_tested_side(broker, ic)
    assert side == "neither"


@pytest.mark.asyncio
async def test_tested_side_call_when_call_drifted(agent):
    ic = _seed_open_ic(agent)
    greeks = {
        "P-SP": {"mark_price": 0.50, "delta": -0.18},
        "C-SC": {"mark_price": 1.20, "delta": 0.32},
    }
    broker = _fake_broker(greeks=greeks)
    side = await agent._identify_tested_side(broker, ic)
    assert side == "call"


@pytest.mark.asyncio
async def test_tested_side_put_when_only_put_drifted(agent):
    ic = _seed_open_ic(agent)
    greeks = {
        "P-SP": {"mark_price": 1.20, "delta": -0.32},
        "C-SC": {"mark_price": 0.50, "delta": 0.18},
    }
    broker = _fake_broker(greeks=greeks)
    side = await agent._identify_tested_side(broker, ic)
    assert side == "put"


@pytest.mark.asyncio
async def test_tested_side_picks_higher_when_both_moved_against(agent):
    ic = _seed_open_ic(agent)
    greeks = {
        "P-SP": {"mark_price": 1.10, "delta": -0.28},   # |Δ| = 0.28
        "C-SC": {"mark_price": 1.30, "delta": 0.34},    # |Δ| = 0.34 (higher)
    }
    broker = _fake_broker(greeks=greeks)
    side = await agent._identify_tested_side(broker, ic)
    assert side == "call"


# ---------------------------------------------------------------------------
# Branches 6, 7, 8, 9
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_branch_6_warn_no_action(agent):
    _seed_open_ic(agent)
    greeks = {
        "P-SP": {"mark_price": 0.60, "delta": -0.18},
        "P-LP": {"mark_price": 0.20, "delta": -0.05},
        "C-SC": {"mark_price": 0.95, "delta": 0.27},      # warn zone (0.25–0.30)
        "C-LC": {"mark_price": 0.30, "delta": 0.10},
    }
    broker = _fake_broker(greeks=greeks)
    actions, cadence = await agent.manage(broker)
    assert actions == []
    assert cadence == _CADENCE_WARN


@pytest.mark.asyncio
async def test_branch_7_adjustment_1_proposed(agent):
    """Tested Δ ∈ [0.30, 0.35), DTE > 14, adj_count=0, untested mark > $0.10."""
    target = (agent._clock().date() + timedelta(days=30)).isoformat()
    _seed_open_ic(agent, expiration=target)
    greeks = {
        "P-SP": {"mark_price": 0.40, "delta": -0.16},     # untested, mark > 0.10
        "P-LP": {"mark_price": 0.15, "delta": -0.05},
        "C-SC": {"mark_price": 1.30, "delta": 0.32},      # tested call
        "C-LC": {"mark_price": 0.35, "delta": 0.10},
    }
    # Chain for adjustment 1's pick of new untested-side (put) Δ=0.30.
    puts = [
        _option(strike=440, delta=-0.30, mark=0.95, option_id="P-NEW-S"),
        _option(strike=437, delta=-0.20, mark=0.40, option_id="P-NEW-L"),
    ]
    broker = _fake_broker(greeks=greeks, puts=puts, expirations=[target])
    actions, _ = await agent.manage(broker)
    assert len(actions) == 1
    intents = {o.extra["combo_intent"] for o in actions[0]}
    assert intents == {"adjustment_1"}
    # 4 legs: 2 close (old put vertical) + 2 open (new put vertical).
    effects = [o.extra["position_effect"] for o in actions[0]]
    assert sorted(effects) == ["close", "close", "open", "open"]


@pytest.mark.asyncio
async def test_branch_8_close_tested_when_adjustment_exhausted(agent):
    _seed_open_ic(agent, adjustment_count=1)            # already adjusted
    greeks = {
        "P-SP": {"mark_price": 0.40, "delta": -0.18},
        "P-LP": {"mark_price": 0.15, "delta": -0.05},
        "C-SC": {"mark_price": 1.30, "delta": 0.32},
        "C-LC": {"mark_price": 0.35, "delta": 0.10},
    }
    broker = _fake_broker(greeks=greeks)
    actions, _ = await agent.manage(broker)
    assert len(actions) == 1
    intents = {o.extra["combo_intent"] for o in actions[0]}
    assert intents == {"close_tested_side"}


@pytest.mark.asyncio
async def test_branch_9_close_tested_at_high_delta(agent):
    """|Δ| ≥ 0.35 → close tested side only (hard stop already evaluated)."""
    _seed_open_ic(agent)
    greeks = {
        "P-SP": {"mark_price": 0.40, "delta": -0.18},
        "P-LP": {"mark_price": 0.15, "delta": -0.05},
        "C-SC": {"mark_price": 1.60, "delta": 0.40},
        "C-LC": {"mark_price": 0.50, "delta": 0.12},
    }
    broker = _fake_broker(greeks=greeks)
    actions, _ = await agent.manage(broker)
    assert len(actions) == 1
    intents = {o.extra["combo_intent"] for o in actions[0]}
    assert intents == {"close_tested_side"}


# ---------------------------------------------------------------------------
# Cadence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cadence_idle_when_no_positions(agent):
    actions, cadence = await agent.manage(_fake_broker())
    assert actions == []
    assert cadence == _CADENCE_IDLE


@pytest.mark.asyncio
async def test_cadence_tested_when_position_at_high_delta(agent):
    _seed_open_ic(agent)
    greeks = {
        "P-SP": {"mark_price": 0.40, "delta": -0.18},
        "P-LP": {"mark_price": 0.15, "delta": -0.05},
        "C-SC": {"mark_price": 1.30, "delta": 0.32},
        "C-LC": {"mark_price": 0.35, "delta": 0.10},
    }
    broker = _fake_broker(greeks=greeks)
    _, cadence = await agent.manage(broker)
    assert cadence == _CADENCE_TESTED


# ---------------------------------------------------------------------------
# Portfolio preflight
# ---------------------------------------------------------------------------


def test_preflight_rejects_duplicate_underlying(agent):
    _seed_open_ic(agent, symbol="SPY")
    state = agent.load_state()
    ok, reason = agent._preflight_underlying(state, "SPY")
    assert ok is False
    assert reason == "duplicate_underlying"


def test_preflight_rejects_above_max_concurrent(agent):
    state = agent.load_state()
    for i, sym in enumerate(["AAA", "BBB", "CCC"]):
        state["open_ics"][f"ic-{i}"] = {"symbol": sym}
    agent.persist_state(state)
    ok, reason = agent._preflight_underlying(state, "DDD")
    assert ok is False
    assert reason == "max_concurrent"


def test_preflight_rejects_above_max_correlated(agent):
    state = agent.load_state()
    state["open_ics"]["ic-a"] = {"symbol": "SPY"}
    state["open_ics"]["ic-b"] = {"symbol": "QQQ"}
    agent.persist_state(state)
    # IWM is correlated with SPY+QQQ; max_correlated=2 → reject.
    ok, reason = agent._preflight_underlying(state, "IWM")
    assert ok is False
    assert reason == "max_correlated"


def test_preflight_accepts_uncorrelated(agent):
    state = agent.load_state()
    state["open_ics"]["ic-a"] = {"symbol": "SPY"}
    state["open_ics"]["ic-b"] = {"symbol": "QQQ"}
    agent.persist_state(state)
    ok, _ = agent._preflight_underlying(state, "GLD")
    assert ok is True


# ---------------------------------------------------------------------------
# Circuit breaker reset semantics
# ---------------------------------------------------------------------------


def test_circuit_breaker_resets_on_winning_trade(agent):
    state = agent.load_state()
    state["circuit_breaker"]["consecutive_losses"] = 2
    agent._on_combo_closed_pnl(state, +0.50)             # winner
    assert state["circuit_breaker"]["consecutive_losses"] == 0


def test_circuit_breaker_increments_on_loss(agent):
    state = agent.load_state()
    agent._on_combo_closed_pnl(state, -1.00)
    agent._on_combo_closed_pnl(state, -0.50)
    assert state["circuit_breaker"]["consecutive_losses"] == 2


def test_circuit_breaker_pauses_after_3_losses(agent):
    state = agent.load_state()
    for _ in range(3):
        agent._on_combo_closed_pnl(state, -0.50)
    assert state["circuit_breaker"]["consecutive_losses"] == 3
    assert state["circuit_breaker"]["paused_until"] is not None


def test_circuit_breaker_kill_switch_resets_all(agent):
    state = agent.load_state()
    for _ in range(3):
        agent._on_combo_closed_pnl(state, -0.50)
    agent.persist_state(state)
    agent.reset_circuit_breaker()
    s = agent.load_state()
    assert s["circuit_breaker"]["consecutive_losses"] == 0
    assert s["circuit_breaker"]["paused_until"] is None


def test_circuit_breaker_repause_check_resets_when_expired(agent):
    state = agent.load_state()
    # Pause in the past.
    state["circuit_breaker"]["paused_until"] = (
        agent._clock() - timedelta(days=1)
    ).isoformat(timespec="seconds")
    state["circuit_breaker"]["consecutive_losses"] = 5
    agent._check_repause(state)
    assert state["circuit_breaker"]["paused_until"] is None
    assert state["circuit_breaker"]["consecutive_losses"] == 0


@pytest.mark.asyncio
async def test_scan_blocks_when_paused(agent):
    state = agent.load_state()
    state["circuit_breaker"]["paused_until"] = (
        agent._clock() + timedelta(days=1)
    ).isoformat(timespec="seconds")
    agent.persist_state(state)
    # No matter what the broker says, scan returns [].
    out = await agent.scan(_fake_broker())
    assert out == []


# ---------------------------------------------------------------------------
# on_combo_filled state updates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_combo_filled_open_registers_ic_in_state(agent):
    """An "open" pending → on_combo_filled adds to open_ics registry."""
    combo_id = "new-combo"
    agent._pending[combo_id] = {
        "intent": "open",
        "symbol": "SPY",
        "expiration": "2026-06-19",
        "dte_at_entry": 45,
        "ivr_at_entry": 0.55,
        "wing_width": 3.0,
        "credit_at_entry": 1.20,
        "contracts": 1,
        "max_loss_per_contract": 180.0,
        "short_put_strike": 430.0,
        "long_put_strike": 427.0,
        "short_call_strike": 470.0,
        "long_call_strike": 473.0,
        "short_put_option_id": "P-SP",
        "long_put_option_id": "P-LP",
        "short_call_option_id": "C-SC",
        "long_call_option_id": "C-LC",
        "short_put_delta_at_entry": -0.16,
        "short_call_delta_at_entry": 0.16,
        "long_put_delta_at_entry": -0.08,
        "long_call_delta_at_entry": 0.08,
    }
    fills = [
        FillEvent(order_id=f"o{i}", symbol="SPY", side="sell" if i % 2 == 0 else "buy",
                  qty=1.0, price=0.40, ts="2026-05-15T15:00:00", venue="paper-exec")
        for i in range(4)
    ]
    agent.on_combo_filled(combo_id, fills)
    state = agent.load_state()
    assert combo_id in state["open_ics"]
    ic = state["open_ics"][combo_id]
    assert ic["symbol"] == "SPY"
    assert ic["credit_at_entry"] == 1.20
    assert ic["adjustment_count"] == 0
    # Pending cleared
    assert combo_id not in agent._pending


@pytest.mark.asyncio
async def test_on_combo_filled_close_removes_ic_and_updates_circuit_breaker(agent):
    combo_id = "ic-1"
    _seed_open_ic(agent, combo_id=combo_id, credit=1.20)
    agent._pending[combo_id] = {"intent": "close", "close_kind": "profit_target"}
    # Net close cashflow = -0.50 (we paid 0.50 to close).
    # Realized = credit_at_entry + cashflow = 1.20 + (-0.50) = 0.70 → winner.
    fills = [
        FillEvent(order_id="o1", symbol="SPY", side="buy",  qty=1, price=0.30,
                  ts="t", venue="v"),   # buy back short put (pay 0.30)
        FillEvent(order_id="o2", symbol="SPY", side="sell", qty=1, price=0.10,
                  ts="t", venue="v"),   # sell long put     (recv 0.10)
        FillEvent(order_id="o3", symbol="SPY", side="buy",  qty=1, price=0.30,
                  ts="t", venue="v"),   # buy back short call
        FillEvent(order_id="o4", symbol="SPY", side="sell", qty=1, price=0.00,
                  ts="t", venue="v"),
    ]
    agent.on_combo_filled(combo_id, fills)
    state = agent.load_state()
    assert combo_id not in state["open_ics"]
    # Winner — consecutive_losses reset (was 0, stays 0).
    assert state["circuit_breaker"]["consecutive_losses"] == 0
    assert state["circuit_breaker"]["recent_pnl"][-1] == pytest.approx(0.70)


@pytest.mark.asyncio
async def test_on_combo_filled_adjustment_increments_count_and_swaps_strikes(agent):
    parent_id = "ic-parent"
    _seed_open_ic(agent, combo_id=parent_id)
    adj_combo_id = "adj-1"
    agent._pending[adj_combo_id] = {
        "intent": "adjustment_1",
        "parent_combo_id": parent_id,
        "untested_side": "put",
        "new_short_strike": 440.0,
        "new_long_strike": 437.0,
        "new_short_option_id": "P-NEW-S",
        "new_long_option_id": "P-NEW-L",
        "new_short_delta_at_entry": -0.30,
    }
    fills = [
        FillEvent(order_id=f"o{i}", symbol="SPY",
                  side="buy" if i in (0, 3) else "sell",
                  qty=1.0, price=0.30, ts="t", venue="v")
        for i in range(4)
    ]
    agent.on_combo_filled(adj_combo_id, fills)
    state = agent.load_state()
    ic = state["open_ics"][parent_id]
    assert ic["adjustment_count"] == 1
    assert ic["short_put_strike"] == 440.0
    assert ic["long_put_strike"] == 437.0
    assert ic["short_put_option_id"] == "P-NEW-S"
    assert ic["short_put_delta_at_entry"] == -0.30


# ---------------------------------------------------------------------------
# Startup catch-up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_catchup_tags_actions(agent):
    """An overdue DTE position triggers a close on startup, tagged
    startup_catchup."""
    target = (agent._clock().date() + timedelta(days=5)).isoformat()
    _seed_open_ic(agent, expiration=target)
    greeks = {
        "P-SP": {"mark_price": 0.30, "delta": -0.10},
        "P-LP": {"mark_price": 0.10, "delta": -0.03},
        "C-SC": {"mark_price": 0.30, "delta": 0.10},
        "C-LC": {"mark_price": 0.10, "delta": 0.03},
    }
    broker = _fake_broker(greeks=greeks)
    actions, _ = await agent.startup_catchup(broker)
    assert len(actions) == 1
    # Every leg flagged.
    assert all(o.extra.get("startup_catchup") is True for o in actions[0])
    assert all(o.extra.get("audit_severity") == "warning" for o in actions[0])


# ---------------------------------------------------------------------------
# IVR data unavailable — None from calc_iv_rank → tally ivr_data_unavailable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_tallies_ivr_data_unavailable_when_calc_iv_rank_returns_none(agent):
    """calc_iv_rank returning None → strategy tallies ivr_data_unavailable, returns no combo."""
    with patch(
        "trading_corp.agents.strategies.robinhood_joint_iron_condor.get_vix",
        return_value=18.0,
    ), patch(
        "trading_corp.agents.strategies.robinhood_joint_iron_condor.calc_iv_rank",
        new=AsyncMock(return_value=None),   # provider returns None (data unavailable)
    ):
        out = await agent.scan(_fake_broker())

    assert out == [], "expected no combos when IV rank data is unavailable"
    state = agent.load_state()
    bucket = list(state["scan_telemetry"].values())[0]
    assert "ivr_data_unavailable" in bucket["SPY"]["by_reason"], (
        "expected ivr_data_unavailable tally when calc_iv_rank returns None"
    )


# ---------------------------------------------------------------------------
# Chain-too-shallow guard — _construct_ic returns None + tallies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_shallow_call_side_skips_symbol(agent):
    """Call side delta +0.30 (not within 0.05 of target +0.16) → chain_too_shallow."""
    expiry = "2026-06-29"
    # Shallowest available call has delta 0.30 — outside ±0.05 of target 0.16
    calls = [
        _option(strike=465, delta=0.30, mark=1.80, option_id="C-465", expiration=expiry),
        _option(strike=468, delta=0.22, mark=1.20, option_id="C-468", expiration=expiry),
    ]
    puts = [
        _option(strike=430, delta=-0.16, mark=1.10, option_id="P-430", expiration=expiry),
        _option(strike=427, delta=-0.08, mark=0.20, option_id="P-427", expiration=expiry),
    ]
    broker = _fake_broker(calls=calls, puts=puts, expirations=[expiry])

    with patch(
        "trading_corp.agents.strategies.robinhood_joint_iron_condor.get_vix",
        return_value=18.0,
    ), patch(
        "trading_corp.agents.strategies.robinhood_joint_iron_condor.calc_iv_rank",
        new=AsyncMock(return_value=0.55),
    ), patch(
        "trading_corp.agents.strategies.robinhood_joint_iron_condor.calc_atm_iv",
        new=AsyncMock(return_value=0.20),
    ):
        out = await agent.scan(broker)

    assert out == [], "expected no combos when call chain too shallow"
    state = agent.load_state()
    bucket = list(state["scan_telemetry"].values())[0]
    assert "chain_too_shallow" in bucket["SPY"]["by_reason"], (
        "expected chain_too_shallow tally when call-side delta outside tolerance"
    )


@pytest.mark.asyncio
async def test_chain_shallow_put_side_skips_symbol(agent):
    """Put side delta -0.30 (not within 0.05 of target -0.16) → chain_too_shallow."""
    expiry = "2026-06-29"
    calls = [
        _option(strike=470, delta=0.16, mark=1.10, option_id="C-470", expiration=expiry),
        _option(strike=473, delta=0.08, mark=0.20, option_id="C-473", expiration=expiry),
    ]
    # Shallowest available put has delta -0.30 — outside ±0.05 of target -0.16
    puts = [
        _option(strike=435, delta=-0.30, mark=1.80, option_id="P-435", expiration=expiry),
        _option(strike=432, delta=-0.22, mark=1.20, option_id="P-432", expiration=expiry),
    ]
    broker = _fake_broker(calls=calls, puts=puts, expirations=[expiry])

    with patch(
        "trading_corp.agents.strategies.robinhood_joint_iron_condor.get_vix",
        return_value=18.0,
    ), patch(
        "trading_corp.agents.strategies.robinhood_joint_iron_condor.calc_iv_rank",
        new=AsyncMock(return_value=0.55),
    ), patch(
        "trading_corp.agents.strategies.robinhood_joint_iron_condor.calc_atm_iv",
        new=AsyncMock(return_value=0.20),
    ):
        out = await agent.scan(broker)

    assert out == [], "expected no combos when put chain too shallow"
    state = agent.load_state()
    bucket = list(state["scan_telemetry"].values())[0]
    assert "chain_too_shallow" in bucket["SPY"]["by_reason"], (
        "expected chain_too_shallow tally when put-side delta outside tolerance"
    )
