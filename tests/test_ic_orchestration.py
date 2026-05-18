"""Tests for trading_corp.agents.strategies._ic_orchestration.

Focus areas (step 11 user instructions):
  - Scan-fire predicate: 09:45 ET on a market day, skipped on weekend
    and on each 2026 US holiday.
  - Position Manager: startup_catchup runs once before the loop body,
    then the cadence-driven loop respects strategy.manage()'s returned
    next_cadence_seconds.
  - State callback: on_combo_filled invoked synchronously after a
    successful place_combo; NOT invoked on empty fills.
  - propose_ic_combo: risk-rejection aborts the whole combo; no audit
    confusion.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:
    _ET = None

from trading_corp.agents.strategies._ic_orchestration import (
    _US_MARKET_HOLIDAYS_2026,
    dispatch_approved_ic_combo,
    is_signal_scan_due,
    is_us_market_day,
    propose_ic_combo,
    run_position_manager_loop,
    run_signal_scanner_loop,
)
from trading_corp.persistence.models import FillEvent, ProposedOrder


# ---------------------------------------------------------------------------
# is_signal_scan_due / is_us_market_day
# ---------------------------------------------------------------------------


def _et(y, m, d, hh, mm) -> datetime:
    if _ET is None:
        return datetime(y, m, d, hh, mm)
    return datetime(y, m, d, hh, mm, tzinfo=_ET)


def test_is_us_market_day_skips_weekends():
    # 2026-05-16 is a Saturday; 2026-05-17 is a Sunday.
    assert is_us_market_day(date(2026, 5, 16)) is False
    assert is_us_market_day(date(2026, 5, 17)) is False
    # 2026-05-18 is a Monday.
    assert is_us_market_day(date(2026, 5, 18)) is True


def test_is_us_market_day_skips_nyse_holidays():
    for holiday in _US_MARKET_HOLIDAYS_2026:
        assert is_us_market_day(holiday) is False, (
            f"{holiday.isoformat()} should be a market holiday"
        )


def test_is_us_market_day_normal_weekday_is_market_day():
    # 2026-05-15 Friday (not a holiday).
    assert is_us_market_day(date(2026, 5, 15)) is True


def test_scan_due_at_0945_on_market_day():
    assert is_signal_scan_due(_et(2026, 5, 15, 9, 45), None) is True
    assert is_signal_scan_due(_et(2026, 5, 15, 9, 50), None) is True


def test_scan_not_due_outside_window():
    # Before 09:45 ET
    assert is_signal_scan_due(_et(2026, 5, 15, 9, 30), None) is False
    # After 09:50 ET
    assert is_signal_scan_due(_et(2026, 5, 15, 9, 51), None) is False


def test_scan_not_due_when_already_scanned_today():
    today = date(2026, 5, 15)
    assert is_signal_scan_due(_et(2026, 5, 15, 9, 45), today) is False


def test_scan_due_after_already_scanned_yesterday():
    yesterday = date(2026, 5, 14)
    assert is_signal_scan_due(_et(2026, 5, 15, 9, 45), yesterday) is True


def test_scan_not_due_on_weekend():
    # 2026-05-16 Sat at 09:45.
    assert is_signal_scan_due(_et(2026, 5, 16, 9, 45), None) is False
    # Sun.
    assert is_signal_scan_due(_et(2026, 5, 17, 9, 45), None) is False


def test_scan_not_due_on_holiday():
    # 2026-07-03 (Independence Day observed) at 09:45 ET — even though
    # it's a Friday weekday, it's a NYSE holiday.
    assert is_signal_scan_due(_et(2026, 7, 3, 9, 45), None) is False


# ---------------------------------------------------------------------------
# propose_ic_combo: risk + audit
# ---------------------------------------------------------------------------


def _leg(role: str = "short_put", combo_id: str = "c1") -> ProposedOrder:
    return ProposedOrder(
        strategy="robinhood_joint_iron_condor",
        symbol="SPY", side="sell", qty=1.0,
        order_type="limit", limit_price=0.50,
        extra={
            "is_option": True, "is_multi_leg": True,
            "combo_id": combo_id, "combo_role": role,
            "combo_direction": "credit", "net_limit_price": 1.20,
            "underlying": "SPY", "expiration": "2026-06-19",
            "strike": 430.0, "option_type": "put",
            "position_effect": "open", "ratio_quantity": 1,
            "combo_intent": "open",
        },
    )


def _verdict(verdict: str, reason: str = "") -> Any:
    v = MagicMock()
    v.verdict = verdict
    v.reason = reason
    v.new_qty = None
    return v


@pytest.mark.asyncio
async def test_propose_ic_combo_happy_path_audits_combo_proposed():
    strategy = MagicMock()
    strategy.SLUG = "robinhood_joint_iron_condor"
    risk_agent = MagicMock()
    risk_agent.evaluate = MagicMock(return_value=_verdict("approve"))
    logger_agent = MagicMock()
    legs = [_leg(role) for role in
            ("short_put", "long_put", "short_call", "long_call")]

    ok = await propose_ic_combo(
        legs, intent="open",
        strategy=strategy, risk_agent=risk_agent,
        logger_agent=logger_agent,
        account=MagicMock(), strategy_state=MagicMock(),
        telegram_batcher=None,
    )
    assert ok is True
    # Risk gate called per leg.
    assert risk_agent.evaluate.call_count == 4
    # Single combo_proposed audit emitted (no combo_rejected_by_risk).
    kinds = [c.args[1] for c in logger_agent.log_event.call_args_list]
    assert "combo_proposed" in kinds
    assert "combo_rejected_by_risk" not in kinds


@pytest.mark.asyncio
async def test_propose_ic_combo_rejects_whole_combo_on_first_risk_reject():
    strategy = MagicMock()
    strategy.SLUG = "robinhood_joint_iron_condor"
    risk_agent = MagicMock()
    # First two pass, third rejects.
    risk_agent.evaluate = MagicMock(
        side_effect=[
            _verdict("approve"), _verdict("approve"),
            _verdict("reject", "leg over cap"),
            _verdict("approve"),
        ],
    )
    logger_agent = MagicMock()
    legs = [_leg(role) for role in
            ("short_put", "long_put", "short_call", "long_call")]

    ok = await propose_ic_combo(
        legs, intent="open",
        strategy=strategy, risk_agent=risk_agent,
        logger_agent=logger_agent,
        account=MagicMock(), strategy_state=MagicMock(),
        telegram_batcher=None,
    )
    assert ok is False
    # Risk evaluation stopped at the rejection.
    assert risk_agent.evaluate.call_count == 3
    # Audited as rejection — not as combo_proposed.
    kinds = [c.args[1] for c in logger_agent.log_event.call_args_list]
    assert "combo_rejected_by_risk" in kinds
    assert "combo_proposed" not in kinds


@pytest.mark.asyncio
async def test_propose_ic_combo_pings_telegram_batcher_with_intent_tag():
    strategy = MagicMock()
    strategy.SLUG = "robinhood_joint_iron_condor"
    risk_agent = MagicMock()
    risk_agent.evaluate = MagicMock(return_value=_verdict("approve"))
    logger_agent = MagicMock()
    batcher = MagicMock()
    batcher.push = AsyncMock()
    legs = [_leg(role) for role in
            ("short_put", "long_put", "short_call", "long_call")]

    await propose_ic_combo(
        legs, intent="late_dte_force_close",
        strategy=strategy, risk_agent=risk_agent,
        logger_agent=logger_agent,
        account=MagicMock(), strategy_state=MagicMock(),
        telegram_batcher=batcher,
    )
    batcher.push.assert_awaited_once()
    # Tag is the intent string, so a configured bypass tag triggers
    # immediate-send at the batcher level.
    _, kwargs = batcher.push.call_args
    assert "late_dte_force_close" in kwargs["tags"]


# ---------------------------------------------------------------------------
# dispatch_approved_ic_combo: state-callback wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_approved_ic_combo_invokes_on_combo_filled_after_success():
    """Synchronous chain: place_combo → fills → on_combo_filled."""
    strategy = MagicMock()
    strategy.on_combo_filled = MagicMock()
    legs = [_leg(role) for role in
            ("short_put", "long_put", "short_call", "long_call")]
    fills = [
        FillEvent(order_id=o.id, symbol="SPY", side=o.side, qty=1.0,
                  price=0.50, ts="t", venue="paper-exec")
        for o in legs
    ]
    data_exec = MagicMock()
    data_exec.place_combo = AsyncMock(return_value=fills)

    out = await dispatch_approved_ic_combo(
        legs, strategy=strategy, data_exec=data_exec,
        division="robinhood_joint",
    )
    assert out == fills
    # Synchronous callback fired exactly once with combo_id + fills.
    strategy.on_combo_filled.assert_called_once_with("c1", fills)
    # Order: place_combo BEFORE on_combo_filled.
    # MagicMock doesn't preserve call order across mocks directly, so we
    # rely on the await + sync call being inseparable by construction.


@pytest.mark.asyncio
async def test_dispatch_approved_ic_combo_skips_callback_on_empty_fills():
    """Empty fills (combo unfilled at venue) → do NOT call on_combo_filled."""
    strategy = MagicMock()
    strategy.on_combo_filled = MagicMock()
    legs = [_leg()]
    data_exec = MagicMock()
    data_exec.place_combo = AsyncMock(return_value=[])

    out = await dispatch_approved_ic_combo(
        legs, strategy=strategy, data_exec=data_exec,
        division="robinhood_joint",
    )
    assert out == []
    strategy.on_combo_filled.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_approved_ic_combo_empty_input_short_circuits():
    strategy = MagicMock()
    strategy.on_combo_filled = MagicMock()
    data_exec = MagicMock()
    data_exec.place_combo = AsyncMock()

    out = await dispatch_approved_ic_combo(
        [], strategy=strategy, data_exec=data_exec,
    )
    assert out == []
    data_exec.place_combo.assert_not_called()
    strategy.on_combo_filled.assert_not_called()


# ---------------------------------------------------------------------------
# run_position_manager_loop: startup_catchup before loop body, cadence-driven
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_position_manager_runs_startup_catchup_once_then_loops():
    """Verify the contract: startup_catchup is awaited exactly once
    before the first manage() call, and the loop sleeps for the
    cadence returned by manage()."""
    strategy = MagicMock()
    strategy.startup_catchup = AsyncMock(return_value=([], 60))
    # manage() returns escalating cadences so we can verify the sleep
    # plumbing reads the returned value (not a hardcoded constant).
    strategy.manage = AsyncMock(side_effect=[
        ([], 5),                  # first tick
        ([], 7),                  # second tick
        ([], 11),                 # third tick
    ])
    division = MagicMock(); division.slug = "robinhood_joint"
    risk_agent = MagicMock()
    risk_agent.evaluate = MagicMock(return_value=_verdict("approve"))
    logger_agent = MagicMock()
    data_exec = MagicMock()

    sleep_durations = []
    import trading_corp.agents.strategies._ic_orchestration as orch
    real_sleep = orch.asyncio.sleep
    async def _record_sleep(secs):
        sleep_durations.append(secs)
        await real_sleep(0)        # yield to event loop, no real sleep
        # After 4 sleeps (one startup + three manage ticks), cancel.
        if len(sleep_durations) >= 4:
            raise asyncio.CancelledError()

    stop_event = asyncio.Event()
    orig_sleep = orch.asyncio.sleep
    orch.asyncio.sleep = _record_sleep  # type: ignore
    try:
        # The loop catches CancelledError internally and returns cleanly,
        # so this awaits to completion rather than raising.
        await run_position_manager_loop(
            division=division, broker=MagicMock(),
            strategy=strategy, risk_agent=risk_agent,
            logger_agent=logger_agent, data_exec=data_exec,
            account_factory=lambda: MagicMock(),
            strategy_state_factory=lambda: MagicMock(),
            telegram_batcher=None,
            stop_event=stop_event,
        )
    finally:
        orch.asyncio.sleep = orig_sleep  # type: ignore

    # startup_catchup called exactly once.
    assert strategy.startup_catchup.call_count == 1
    # Then manage() called 3 times (we cancelled on 4th sleep).
    assert strategy.manage.call_count == 3
    # Sleep durations match the returned cadences in order.
    # First sleep is the post-startup cadence (60); then 5, 7, 11.
    assert sleep_durations == [60, 5, 7, 11]


@pytest.mark.asyncio
async def test_position_manager_dispatches_startup_catchup_actions():
    """If startup_catchup returns actions, they get proposed before the
    loop body runs."""
    leg = _leg()
    strategy = MagicMock()
    strategy.SLUG = "robinhood_joint_iron_condor"
    strategy.startup_catchup = AsyncMock(return_value=([[leg]], 1800))
    strategy.manage = AsyncMock(side_effect=asyncio.CancelledError())
    division = MagicMock(); division.slug = "robinhood_joint"
    risk_agent = MagicMock()
    risk_agent.evaluate = MagicMock(return_value=_verdict("approve"))
    logger_agent = MagicMock()
    data_exec = MagicMock()

    import trading_corp.agents.strategies._ic_orchestration as orch
    real_sleep = orch.asyncio.sleep
    async def _short_sleep(secs):
        await real_sleep(0)
    orig = orch.asyncio.sleep
    orch.asyncio.sleep = _short_sleep  # type: ignore
    try:
        try:
            await run_position_manager_loop(
                division=division, broker=MagicMock(),
                strategy=strategy, risk_agent=risk_agent,
                logger_agent=logger_agent, data_exec=data_exec,
                account_factory=lambda: MagicMock(),
                strategy_state_factory=lambda: MagicMock(),
                telegram_batcher=None,
            )
        except asyncio.CancelledError:
            pass
    finally:
        orch.asyncio.sleep = orig  # type: ignore

    # The startup_catchup action was proposed via the standard pipeline.
    kinds = [c.args[1] for c in logger_agent.log_event.call_args_list]
    assert "combo_proposed" in kinds


# ---------------------------------------------------------------------------
# run_signal_scanner_loop: scan firing window + holiday skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_scanner_fires_at_window_on_market_day():
    """On a market day at 09:45 ET, division.scan() is called once."""
    division = MagicMock()
    division.slug = "robinhood_joint"
    division.scan = AsyncMock(return_value=[])
    strategy = MagicMock()
    strategy.SLUG = "robinhood_joint_iron_condor"
    risk_agent = MagicMock()
    logger_agent = MagicMock()
    data_exec = MagicMock()

    # Clock progresses: 09:30 (no fire), 09:45 (fire), 09:46 (no fire — already scanned).
    clock_values = iter([
        _et(2026, 5, 15, 9, 30),
        _et(2026, 5, 15, 9, 45),
        _et(2026, 5, 15, 9, 46),
    ])
    def _clock():
        return next(clock_values)

    stop_event = asyncio.Event()
    import trading_corp.agents.strategies._ic_orchestration as orch
    real_sleep = orch.asyncio.sleep

    async def _short_sleep(secs):
        await real_sleep(0)
        if division.scan.call_count >= 1:
            stop_event.set()

    orig = orch.asyncio.sleep
    orch.asyncio.sleep = _short_sleep  # type: ignore
    try:
        await run_signal_scanner_loop(
            division=division, broker=MagicMock(),
            strategy=strategy, risk_agent=risk_agent,
            logger_agent=logger_agent, data_exec=data_exec,
            account_factory=lambda: MagicMock(),
            strategy_state_factory=lambda: MagicMock(),
            telegram_batcher=None,
            clock_fn=_clock,
            poll_interval_sec=0.0,
            stop_event=stop_event,
        )
    finally:
        orch.asyncio.sleep = orig  # type: ignore

    # Exactly one scan call (the 09:45 tick).
    assert division.scan.call_count == 1


@pytest.mark.asyncio
async def test_signal_scanner_skips_on_weekend():
    """On a Saturday, the scanner never calls division.scan()."""
    division = MagicMock()
    division.slug = "robinhood_joint"
    division.scan = AsyncMock(return_value=[])
    strategy = MagicMock()
    strategy.SLUG = "robinhood_joint_iron_condor"

    clock_values = iter([_et(2026, 5, 16, 9, 45)] * 3)  # Sat
    def _clock():
        try:
            return next(clock_values)
        except StopIteration:
            return _et(2026, 5, 16, 9, 50)

    stop_event = asyncio.Event()
    tick_count = [0]
    import trading_corp.agents.strategies._ic_orchestration as orch
    real_sleep = orch.asyncio.sleep

    async def _short_sleep(secs):
        tick_count[0] += 1
        if tick_count[0] >= 3:
            stop_event.set()
        await real_sleep(0)

    orig = orch.asyncio.sleep
    orch.asyncio.sleep = _short_sleep  # type: ignore
    try:
        await run_signal_scanner_loop(
            division=division, broker=MagicMock(),
            strategy=strategy, risk_agent=MagicMock(),
            logger_agent=MagicMock(), data_exec=MagicMock(),
            account_factory=lambda: MagicMock(),
            strategy_state_factory=lambda: MagicMock(),
            clock_fn=_clock, poll_interval_sec=0.0,
            stop_event=stop_event,
        )
    finally:
        orch.asyncio.sleep = orig  # type: ignore

    division.scan.assert_not_called()


@pytest.mark.asyncio
async def test_signal_scanner_skips_on_holiday():
    """On Independence Day observed (Fri 2026-07-03), no scan fires."""
    division = MagicMock()
    division.slug = "robinhood_joint"
    division.scan = AsyncMock(return_value=[])
    strategy = MagicMock()
    strategy.SLUG = "robinhood_joint_iron_condor"

    clock_values = iter([_et(2026, 7, 3, 9, 45)] * 3)
    def _clock():
        try:
            return next(clock_values)
        except StopIteration:
            return _et(2026, 7, 3, 9, 50)

    stop_event = asyncio.Event()
    tick = [0]
    import trading_corp.agents.strategies._ic_orchestration as orch
    real_sleep = orch.asyncio.sleep

    async def _short_sleep(secs):
        tick[0] += 1
        if tick[0] >= 3:
            stop_event.set()
        await real_sleep(0)

    orig = orch.asyncio.sleep
    orch.asyncio.sleep = _short_sleep  # type: ignore
    try:
        await run_signal_scanner_loop(
            division=division, broker=MagicMock(),
            strategy=strategy, risk_agent=MagicMock(),
            logger_agent=MagicMock(), data_exec=MagicMock(),
            account_factory=lambda: MagicMock(),
            strategy_state_factory=lambda: MagicMock(),
            clock_fn=_clock, poll_interval_sec=0.0,
            stop_event=stop_event,
        )
    finally:
        orch.asyncio.sleep = orig  # type: ignore

    division.scan.assert_not_called()
