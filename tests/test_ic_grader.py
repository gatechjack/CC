"""Tests for ic_candidate_grader.

Coverage groups:
  - Parser (markdown links, brackets, commas, row cap, date format)
  - Universe cheap-gate (zero provider calls for non-universe rows)
  - Expiration gate (also doubles as resolvability check)
  - IVR gate (failure carries measured value)
  - Term-structure gate (operand order pinned with realistic numbers;
    one-leg-None and both-legs-None → NEEDS_LIVE_DATA)
  - Delta-proximity gate (chain delta vs get_greeks fallback)
  - Budget + timeout
  - Per-run cache reuse
  - Stale-paste warning
  - Audit summary written without raw paste content
  - Cfg snapshot frozen during run
  - No-execution invariant (AST walk)
  - Operator's "0 of 21 PASS" sample
"""
from __future__ import annotations

import ast
import asyncio
import inspect
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.strategies import ic_candidate_grader as g
from trading_corp.data.market_data_provider import OptionContract


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_strategy(
    *,
    universe=("SPY", "QQQ", "IWM", "GLD", "TLT"),
    wing_widths=None,
    min_ivr=30,
    target_dte=45,
    short_delta=0.16,
    min_credit_pct_of_width=0.33,
    term_structure_max_diff=0.05,
):
    if wing_widths is None:
        wing_widths = {
            "SPY": 3.0, "QQQ": 4.0, "IWM": 2.0, "GLD": 2.0, "TLT": 2.0,
        }
    strategy = MagicMock()
    strategy.universe = list(universe)
    strategy.wing_widths = dict(wing_widths)
    cfg_table = {
        "entry.target_dte": target_dte,
        "entry.short_delta": short_delta,
        "entry.min_credit_pct_of_width": min_credit_pct_of_width,
        "entry.min_ivr": min_ivr,
        "entry.term_structure_max_diff": term_structure_max_diff,
    }
    strategy.cfg = lambda dotted: cfg_table[dotted]
    return strategy


def _frozen_clock(dt=None):
    dt = dt or datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc)
    return lambda: dt


def _contract(
    strike, opt_type, *,
    delta=None, mark=None, dte=45,
    option_id=None, expiration="2026-07-06",
):
    return OptionContract(
        option_id=option_id or f"{opt_type}-{strike}",
        expiration_date=expiration,
        strike=strike,
        option_type=opt_type,
        delta=delta,
        gamma=None, theta=None, vega=None, iv=None,
        mark=mark, bid=None, ask=None,
        bid_size=None, ask_size=None,
        open_interest=None, volume=None,
        dte=dte,
    )


def _full_chain(*, dte=45):
    """Chain for the SPY 445/440 puts + 455/460 calls layout.

    Mids: short_put=1.00, short_call=1.10, long_put=0.50, long_call=0.50.
    Net credit = (1.00 + 1.10) - (0.50 + 0.50) = 1.10 USD.
    Width = $3.0 (SPY wing_width). Credit ratio = 1.10/3.0 = 36.7% > 33%.
    Deltas: shorts at ±0.16 exactly; longs at ±0.10. All chain rows
    carry delta so the chain-first path is exercised by default.
    """
    return [
        _contract(440.0, "put",  delta=-0.10, mark=0.50, dte=dte, option_id="lp"),
        _contract(445.0, "put",  delta=-0.16, mark=1.00, dte=dte, option_id="sp"),
        _contract(455.0, "call", delta=0.16,  mark=1.10, dte=dte, option_id="sc"),
        _contract(460.0, "call", delta=0.10,  mark=0.50, dte=dte, option_id="lc"),
    ]


# One canonical in-universe paste row used across many tests. 45 DTE
# at frozen clock 2026-05-22 → expiration ~2026-07-06.
SPY_ROW = "SPY  450.00  07/06/26 (45)  35%  445/440  455/460  $1.10"


def _all_async_mocks(provider):
    """Make sure every provider method is an AsyncMock so assert_not_called
    works correctly even if the test path never touches them."""
    provider.get_option_chain = AsyncMock()
    provider.get_iv_rank = AsyncMock()
    provider.get_greeks = AsyncMock()
    provider.get_atm_iv = AsyncMock()
    provider.get_underlying_price = AsyncMock()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parser_strips_markdown_links():
    rows, _ = g.parse_paste(
        "[NVDA](http://x.com) 450 07/06/26 (45) 35% 445/440 455/460 $0.85"
    )
    assert len(rows) == 1
    assert rows[0].symbol == "NVDA"


def test_parser_strips_markdown_links_with_spaces_in_url():
    rows, _ = g.parse_paste(
        "[NVDA](http://x.com/path with spaces?q=1) 450 07/06/26 (45) "
        "35% 445/440 455/460 $0.85"
    )
    assert len(rows) == 1
    assert rows[0].symbol == "NVDA"


def test_parser_handles_unclosed_brackets():
    rows, _ = g.parse_paste(
        "[NVDA 450 07/06/26 (45) 35% 445/440 455/460 $0.85"
    )
    assert len(rows) == 1
    assert "unclosed_bracket" in rows[0].parse_errors


def test_parser_strips_commas_in_numbers():
    rows, _ = g.parse_paste(
        "SPY  1,234.50  07/06/26 (45)  35%  445/440  455/460  $0.85"
    )
    assert len(rows) == 1
    assert rows[0].symbol == "SPY"
    assert rows[0].expiration == date(2026, 7, 6)


def test_parser_rejects_above_25_rows():
    paste = "\n".join(
        ["SPY 450 07/06/26 (45) 35% 445/440 455/460 $0.85"] * 26
    )
    rows, warns = g.parse_paste(paste)
    assert rows == []
    assert any("trim to 25 rows (got 26)" in w for w in warns)


def test_parser_date_dte_format():
    rows, _ = g.parse_paste(
        "SPY 450 09/19/25 (45) 35% 445/440 455/460 $0.85"
    )
    assert rows[0].expiration == date(2025, 9, 19)
    assert rows[0].pasted_dte == 45


def test_parser_handles_malformed_row_without_crash():
    paste = "\n".join((
        SPY_ROW,
        "$$$ malformed garbage row %% no date no strikes",
        SPY_ROW,
    ))
    rows, _ = g.parse_paste(paste)
    assert len(rows) == 3
    # The malformed row should be present with parse errors, not crash.
    assert rows[1].parse_errors != ()


# ---------------------------------------------------------------------------
# Universe cheap-gate (load-bearing invariant)
# ---------------------------------------------------------------------------


async def test_universe_gate_makes_zero_provider_calls():
    """Non-universe rows must FAIL on universe without ANY provider call.
    This is the cheap-gates-first invariant — provider must stay untouched."""
    paste = "\n".join(
        f"{sym} 100 07/06/26 (45) 35% 100/95 105/110 $0.85"
        for sym in (
            "NVDA", "TSLA", "AAPL", "META", "AMZN",
            "GOOG", "MSFT", "AMD", "INTC", "PLTR",
        )
    )
    strategy = _make_strategy()
    provider = MagicMock()
    _all_async_mocks(provider)

    result = await g.grade_paste(
        paste, strategy=strategy, provider=provider, clock=_frozen_clock(),
    )

    assert len(result.rows) == 10
    assert all(r.verdict == "FAIL" for r in result.rows)
    assert all(r.failed_gate == "universe" for r in result.rows)
    provider.get_option_chain.assert_not_called()
    provider.get_iv_rank.assert_not_called()
    provider.get_greeks.assert_not_called()
    provider.get_atm_iv.assert_not_called()
    assert result.provider_calls_total == 0


# ---------------------------------------------------------------------------
# Expiration gate doubles as resolvability check
# ---------------------------------------------------------------------------


async def test_unknown_in_universe_symbol_fails_expiration_gate():
    """In-universe symbol with empty live chain → expiration_not_available.
    Validates gate 2 as the resolvability path (no separate get_underlying_price
    gate is needed)."""
    strategy = _make_strategy()
    provider = MagicMock()
    provider.get_option_chain = AsyncMock(return_value=[])
    provider.get_iv_rank = AsyncMock()
    provider.get_greeks = AsyncMock()
    provider.get_atm_iv = AsyncMock()

    result = await g.grade_paste(
        SPY_ROW, strategy=strategy, provider=provider, clock=_frozen_clock(),
    )
    assert len(result.rows) == 1
    assert result.rows[0].verdict == "FAIL"
    assert result.rows[0].failed_gate == "expiration_not_available"


# ---------------------------------------------------------------------------
# IVR gate
# ---------------------------------------------------------------------------


async def test_ivr_failure_carries_measured_value():
    """IVR gate failure must include the actual measured IVR in both
    the human reason string and the structured measurements dict."""
    strategy = _make_strategy(min_ivr=30)
    provider = MagicMock()
    provider.get_option_chain = AsyncMock(return_value=_full_chain())
    provider.get_iv_rank = AsyncMock(return_value=0.22)  # 22 % < 30
    provider.get_greeks = AsyncMock()
    provider.get_atm_iv = AsyncMock()

    result = await g.grade_paste(
        SPY_ROW, strategy=strategy, provider=provider, clock=_frozen_clock(),
    )
    row = result.rows[0]
    assert row.verdict == "FAIL"
    assert row.failed_gate == "ivr"
    assert "live IVR 22 < 30" in row.reason
    assert row.measurements["live_ivr_pct"] == pytest.approx(22.0)


# ---------------------------------------------------------------------------
# Term-structure gate — operand order pinned by realistic numbers
# ---------------------------------------------------------------------------


async def test_term_structure_backwardation_fails():
    """Realistic backwardation: front=0.30, back=0.22 → spread=+0.08 > 0.05 → FAIL.

    Pins gate-7 operand direction. If the comparison were inverted
    (back-front instead of front-back), this row would PASS instead.
    """
    strategy = _make_strategy()
    provider = MagicMock()
    provider.get_option_chain = AsyncMock(return_value=_full_chain())
    provider.get_iv_rank = AsyncMock(return_value=0.45)  # 45 % > 30
    provider.get_greeks = AsyncMock()
    provider.get_atm_iv = AsyncMock(side_effect=[0.30, 0.22])  # front, back

    result = await g.grade_paste(
        SPY_ROW, strategy=strategy, provider=provider, clock=_frozen_clock(),
    )
    row = result.rows[0]
    assert row.verdict == "FAIL"
    assert row.failed_gate == "term_structure"
    assert "+0.08 > 0.05" in row.reason
    assert row.measurements["front_atm_iv"] == 0.30
    assert row.measurements["back_atm_iv"] == 0.22
    assert row.measurements["spread"] == pytest.approx(0.08)


async def test_term_structure_normal_contango_passes_to_pass():
    """Normal contango: front=0.20, back=0.22 → spread=-0.02 ≤ 0.05 → pass gate-7,
    continue to credit gate. With the synthetic full_chain (credit=$1.10,
    width=$3.0, ratio=36.7% > 33%), the row should land as PASS overall.

    Pairs with test_term_structure_backwardation_fails to pin operand
    direction from both sides — fail when spread > max_diff, pass when
    spread <= max_diff.
    """
    strategy = _make_strategy()
    provider = MagicMock()
    provider.get_option_chain = AsyncMock(return_value=_full_chain())
    provider.get_iv_rank = AsyncMock(return_value=0.45)
    provider.get_greeks = AsyncMock()
    provider.get_atm_iv = AsyncMock(side_effect=[0.20, 0.22])

    result = await g.grade_paste(
        SPY_ROW, strategy=strategy, provider=provider, clock=_frozen_clock(),
    )
    row = result.rows[0]
    assert row.verdict == "PASS", (
        f"expected PASS, got {row.verdict} on gate {row.failed_gate}: "
        f"{row.reason}"
    )
    assert row.failed_gate is None


async def test_term_structure_front_none_becomes_needs_live_data():
    """Per Q2 design decision: one-leg-None is treated identically to
    both-legs-None. Diverges from strategy's fail-open by intent."""
    strategy = _make_strategy()
    provider = MagicMock()
    provider.get_option_chain = AsyncMock(return_value=_full_chain())
    provider.get_iv_rank = AsyncMock(return_value=0.45)
    provider.get_greeks = AsyncMock()
    provider.get_atm_iv = AsyncMock(side_effect=[None, 0.22])

    result = await g.grade_paste(
        SPY_ROW, strategy=strategy, provider=provider, clock=_frozen_clock(),
    )
    row = result.rows[0]
    assert row.verdict == "NEEDS_LIVE_DATA"
    assert row.failed_gate == "term_structure"
    assert "term-structure data unavailable" in row.reason


async def test_term_structure_back_none_becomes_needs_live_data():
    strategy = _make_strategy()
    provider = MagicMock()
    provider.get_option_chain = AsyncMock(return_value=_full_chain())
    provider.get_iv_rank = AsyncMock(return_value=0.45)
    provider.get_greeks = AsyncMock()
    provider.get_atm_iv = AsyncMock(side_effect=[0.20, None])

    result = await g.grade_paste(
        SPY_ROW, strategy=strategy, provider=provider, clock=_frozen_clock(),
    )
    row = result.rows[0]
    assert row.verdict == "NEEDS_LIVE_DATA"
    assert row.failed_gate == "term_structure"
    assert "back=None" in row.reason


# ---------------------------------------------------------------------------
# Delta-proximity: chain-delta vs get_greeks fallback
# ---------------------------------------------------------------------------


async def test_delta_proximity_uses_chain_delta_when_present():
    """When OptionContract.delta is populated, get_greeks must NOT be
    called — chain delta is the cheap path."""
    strategy = _make_strategy()
    chain = _full_chain()  # all deltas populated
    provider = MagicMock()
    provider.get_option_chain = AsyncMock(return_value=chain)
    provider.get_iv_rank = AsyncMock(return_value=0.45)
    provider.get_greeks = AsyncMock()  # must stay untouched
    provider.get_atm_iv = AsyncMock(side_effect=[0.20, 0.22])

    result = await g.grade_paste(
        SPY_ROW, strategy=strategy, provider=provider, clock=_frozen_clock(),
    )
    assert result.rows[0].verdict == "PASS"
    provider.get_greeks.assert_not_called()


async def test_delta_proximity_falls_back_to_get_greeks_when_chain_delta_none():
    """When OptionContract.delta is None, gate 6 must call
    provider.get_greeks(option_id) to recover delta and continue grading."""
    strategy = _make_strategy()
    chain = [
        _contract(440.0, "put",  delta=-0.10, mark=0.50, option_id="lp"),
        _contract(445.0, "put",  delta=None,  mark=1.00, option_id="sp"),  # None
        _contract(455.0, "call", delta=None,  mark=1.10, option_id="sc"),  # None
        _contract(460.0, "call", delta=0.10,  mark=0.50, option_id="lc"),
    ]
    provider = MagicMock()
    provider.get_option_chain = AsyncMock(return_value=chain)
    provider.get_iv_rank = AsyncMock(return_value=0.45)
    provider.get_greeks = AsyncMock(side_effect=[
        {"delta": -0.16, "gamma": None, "theta": None,
         "vega": None, "iv": None, "mark_price": None},
        {"delta":  0.16, "gamma": None, "theta": None,
         "vega": None, "iv": None, "mark_price": None},
    ])
    provider.get_atm_iv = AsyncMock(side_effect=[0.20, 0.22])

    result = await g.grade_paste(
        SPY_ROW, strategy=strategy, provider=provider, clock=_frozen_clock(),
    )
    assert result.rows[0].verdict == "PASS", (
        f"expected PASS after greeks fallback, got "
        f"{result.rows[0].verdict}/{result.rows[0].failed_gate}: "
        f"{result.rows[0].reason}"
    )
    assert provider.get_greeks.call_count == 2
    called_with = {
        call.args[0] for call in provider.get_greeks.call_args_list
    }
    assert called_with == {"sp", "sc"}


# ---------------------------------------------------------------------------
# Budget + timeout
# ---------------------------------------------------------------------------


async def test_per_call_timeout_yields_needs_live_data():
    strategy = _make_strategy()

    async def slow_chain(*args, **kwargs):
        await asyncio.sleep(10)  # well beyond per_call_timeout=0.1
        return _full_chain()

    provider = MagicMock()
    provider.get_option_chain = slow_chain
    provider.get_iv_rank = AsyncMock(return_value=0.45)
    provider.get_greeks = AsyncMock()
    provider.get_atm_iv = AsyncMock()

    result = await g.grade_paste(
        SPY_ROW, strategy=strategy, provider=provider,
        per_call_timeout=0.1, clock=_frozen_clock(),
    )
    row = result.rows[0]
    assert row.verdict == "NEEDS_LIVE_DATA"
    assert "provider timeout" in row.reason


async def test_call_budget_exhausts_remaining_rows():
    """3 distinct in-universe rows with budget=1.  Row 1 spends budget on
    chain fetch; row 2 and beyond receive NEEDS_LIVE_DATA — call budget."""
    paste = "\n".join((
        "SPY 450 07/06/26 (45) 35% 445/440 455/460 $0.85",
        "QQQ 500 07/13/26 (45) 35% 495/490 505/510 $0.85",
        "IWM 200 07/20/26 (45) 35% 195/190 205/210 $0.85",
    ))
    strategy = _make_strategy()
    provider = MagicMock()
    provider.get_option_chain = AsyncMock(return_value=_full_chain())
    provider.get_iv_rank = AsyncMock(return_value=0.45)
    provider.get_greeks = AsyncMock()
    provider.get_atm_iv = AsyncMock(side_effect=[0.20, 0.22])

    result = await g.grade_paste(
        paste, strategy=strategy, provider=provider,
        call_budget=1, clock=_frozen_clock(),
    )
    # Row 0 used the only budget on its chain fetch; rows 1+2 hit
    # BudgetExhausted on their first provider call.
    assert result.rows[1].verdict == "NEEDS_LIVE_DATA"
    assert "call budget" in result.rows[1].reason
    assert result.rows[2].verdict == "NEEDS_LIVE_DATA"
    assert "call budget" in result.rows[2].reason


# ---------------------------------------------------------------------------
# Per-run cache reuse
# ---------------------------------------------------------------------------


async def test_cache_reuse_two_rows_same_symbol_expiration():
    """Two rows with the same (symbol, expiration) → exactly one chain
    fetch.  Validates the grader's per-run cache; tests run against
    MagicMock providers that don't have their own TTL cache, so the
    grader-side cache is what makes this assertion pass."""
    paste = "\n".join((SPY_ROW, SPY_ROW))
    strategy = _make_strategy()
    provider = MagicMock()
    provider.get_option_chain = AsyncMock(return_value=_full_chain())
    provider.get_iv_rank = AsyncMock(return_value=0.45)
    provider.get_greeks = AsyncMock()
    provider.get_atm_iv = AsyncMock(side_effect=[0.20, 0.22])

    result = await g.grade_paste(
        paste, strategy=strategy, provider=provider, clock=_frozen_clock(),
    )
    # Both rows should grade identically with one chain + one ivr +
    # two atm_iv calls total.
    assert provider.get_option_chain.call_count == 1
    assert provider.get_iv_rank.call_count == 1
    assert provider.get_atm_iv.call_count == 2  # front + back
    assert result.rows[0].verdict == result.rows[1].verdict == "PASS"


# ---------------------------------------------------------------------------
# Stale-paste warning
# ---------------------------------------------------------------------------


def test_stale_paste_warning_for_past_expiration():
    rows, warns = g.parse_paste(
        "SPY 450 01/01/24 (45) 35% 445/440 455/460 $0.85",
        clock=_frozen_clock(),
    )
    assert any("contains expired dates" in w for w in warns)
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Audit summary — no raw paste content
# ---------------------------------------------------------------------------


async def test_summary_audit_written_without_raw_paste():
    """Audit row must carry the per-run counters but NOT the raw paste
    text — paste history is out of scope and could leak external data."""
    strategy = _make_strategy()
    provider = MagicMock()
    provider.get_option_chain = AsyncMock(return_value=[])
    provider.get_iv_rank = AsyncMock()
    provider.get_greeks = AsyncMock()
    provider.get_atm_iv = AsyncMock()
    logger = MagicMock()
    logger.log_event = MagicMock()

    secret_paste = "SECRET_STRINGINSIDE_PASTE  " + SPY_ROW
    await g.grade_paste(
        secret_paste, strategy=strategy, provider=provider,
        logger=logger, clock=_frozen_clock(),
    )

    logger.log_event.assert_called_once()
    call_kwargs = logger.log_event.call_args.kwargs
    assert call_kwargs["actor"] == "ic_candidate_grader"
    assert call_kwargs["kind"] == "ic_grader_run"
    payload = call_kwargs["payload"]
    assert "paste" not in payload
    assert payload["strategy"] == "robinhood_joint_iron_condor"
    assert payload["division"] == "robinhood_joint"
    # Belt-and-suspenders: serialise payload and confirm the secret
    # string is nowhere in it.
    import json
    assert "SECRET_STRING" not in json.dumps(payload)


# ---------------------------------------------------------------------------
# Cfg snapshot frozen during run
# ---------------------------------------------------------------------------


async def test_cfg_snapshot_frozen_during_run():
    """If strategy.cfg's return values change mid-run, the grader still
    uses the snapshot taken at start.  This is the within-run
    consistency property of decision Q1."""
    cfg_state = {"min_ivr": 30}

    def cfg(dotted):
        if dotted == "entry.min_ivr":
            return cfg_state["min_ivr"]
        return {
            "entry.target_dte": 45,
            "entry.short_delta": 0.16,
            "entry.min_credit_pct_of_width": 0.33,
            "entry.term_structure_max_diff": 0.05,
        }[dotted]

    strategy = MagicMock()
    strategy.universe = ["SPY"]
    strategy.wing_widths = {"SPY": 3.0}
    strategy.cfg = cfg

    chain = _full_chain()

    async def chain_fetch(*args, **kwargs):
        # Mid-run YAML hot-reload simulation: bump min_ivr to 50
        # between the first row's chain fetch and any subsequent reads.
        cfg_state["min_ivr"] = 50
        return chain

    provider = MagicMock()
    provider.get_option_chain = AsyncMock(side_effect=chain_fetch)
    # Live IVR at 40 % — passes snapshot (min_ivr=30 frozen) but would
    # FAIL under the mutated value (min_ivr=50).
    provider.get_iv_rank = AsyncMock(return_value=0.40)
    provider.get_greeks = AsyncMock()
    provider.get_atm_iv = AsyncMock(side_effect=[0.20, 0.22, 0.20, 0.22])

    paste = "\n".join((SPY_ROW, SPY_ROW.replace("445/440", "446/441")))
    result = await g.grade_paste(
        paste, strategy=strategy, provider=provider, clock=_frozen_clock(),
    )

    # Row 0 ran against snapshot (min_ivr=30) → PASS.
    assert result.rows[0].verdict == "PASS"
    # Row 1 — even though strategy.cfg would now report min_ivr=50,
    # the grader still uses the frozen snapshot.  IVR 40% > 30 = PASS.
    # (Row 1 may fail on strikes_exist because 446/441 aren't on the
    # synthetic chain — that's fine, we just need it not to fail on IVR.)
    assert result.rows[1].failed_gate != "ivr"


# ---------------------------------------------------------------------------
# No-execution invariant (structural via AST walk)
# ---------------------------------------------------------------------------


def test_no_execution_invariant():
    """ic_candidate_grader.py must NOT import any order-surface name.

    This is the structural guarantee that the grader cannot accidentally
    grow an execution path during future maintenance.  Update the
    forbidden set only with explicit Board sign-off — a relaxation here
    is the kind of change that needs the [PROJECT_CONTEXT.md] memo
    treatment described in CLAUDE.md § 1.
    """
    module_source = inspect.getsource(g)
    tree = ast.parse(module_source)
    forbidden = {
        "place_combo",
        "PendingComboRegistry",
        "dispatch_approved_ic_combo",
        "data_exec",
    }
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name)
                if alias.asname:
                    imported_names.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_names.add(node.module)
                # Also add each segment so deep paths are caught.
                for part in node.module.split("."):
                    imported_names.add(part)
            for alias in node.names:
                imported_names.add(alias.name)
                if alias.asname:
                    imported_names.add(alias.asname)
    for forbidden_name in forbidden:
        assert forbidden_name not in imported_names, (
            f"ic_candidate_grader.py imports forbidden name "
            f"{forbidden_name!r} — breaks no-execution invariant"
        )


# ---------------------------------------------------------------------------
# Operator's realistic "0 of 21 PASS" sample
# ---------------------------------------------------------------------------


async def test_route_returns_html_fragment_with_grader_result(monkeypatch, tmp_path):
    """Full FastAPI route smoke: POST /telemetry/iron_condor/grade with a
    non-universe paste → 200 + result panel HTML containing the summary."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from trading_corp.agents.logger import LoggerAgent
    from trading_corp.web.app import WebDeps, create_app
    import trading_corp.utils.iv as _iv_module

    # Patch the provider factory to return a MagicMock provider.
    fake_provider = MagicMock()
    _all_async_mocks(fake_provider)
    fake_provider.get_iv_rank = AsyncMock(return_value=0.45)
    monkeypatch.setattr(
        _iv_module, "_get_configured_provider", lambda: fake_provider,
    )

    # Build deps with our fake strategy.  Ensure the audit_event
    # schema exists so log_event doesn't warn (the route would still
    # 200 either way, but a clean run lets us assert on the audit row).
    tmp_db = f"sqlite:///{tmp_path}/test.db"
    from trading_corp.persistence import db as _db
    from trading_corp.persistence.db import SCHEMA
    with _db.connect(tmp_db) as conn:
        conn.executescript(SCHEMA)

    fake_strategy = _make_strategy()
    deps = WebDeps(
        db_url=tmp_db, db_path=str(tmp_path / "test.db"), mode="PAPER",
        logger_agent=LoggerAgent(db_url=tmp_db),
        data_exec=MagicMock(brokers={}),
        trend_agent=None, portfolio=None, pmcc_agent=None,
        fidelity_agent=None, paper_broker=MagicMock(),
        secrets=None, pending_registry=MagicMock(),
        pending_combo_registry=None,
        ic_strategy=fake_strategy,
    )
    app = create_app(deps)
    client = TestClient(app)

    paste = "\n".join((
        "NVDA 450 07/06/26 (45) 62% 445/440 455/460 $0.85",
        "TSLA 200 07/06/26 (45) 60% 195/190 205/210 $0.85",
    ))
    resp = client.post(
        "/telemetry/iron_condor/grade", data={"paste": paste},
    )
    assert resp.status_code == 200, resp.text[:500]
    body = resp.text
    # Summary line — number is wrapped in a <span> so check the
    # post-span text rather than literal "2 pasted".
    assert "</span> pasted" in body
    assert body.count("FAIL") >= 2
    assert "universe" in body
    # Footer is present.
    assert "Graded against config" in body
    # Cheap-gate invariant: provider must not be called.
    fake_provider.get_option_chain.assert_not_called()
    fake_provider.get_iv_rank.assert_not_called()

    # Audit row was written with the expected payload shape.
    with _db.connect(tmp_db) as conn:
        cur = conn.execute(
            "SELECT actor, kind, payload_json FROM audit_event "
            "WHERE kind = 'ic_grader_run'"
        )
        audit_rows = list(cur.fetchall())
    assert len(audit_rows) == 1
    import json as _json
    payload = _json.loads(audit_rows[0]["payload_json"])
    assert payload["strategy"] == "robinhood_joint_iron_condor"
    assert payload["rows_pasted"] == 2
    assert payload["rows_failed"] == 2
    assert "paste" not in payload


async def test_operator_sample_zero_pass_no_provider_calls():
    """An actual realistic morning paste of market-wide candidates:
    all 5 are non-universe → 0 pass → 0 provider calls.

    This is the test that proves the discipline function — Barchart's
    market-wide output ought to fail by design against an IC universe of
    [SPY, QQQ, IWM, GLD, TLT]."""
    paste = "\n".join((
        "[NVDA](http://x.com) 450 07/06/26 (45) 62% 445/440 455/460 $0.85",
        "TSLA 200 07/06/26 (45) 60% 195/190 205/210 $0.85",
        "META 300 07/06/26 (45) 50% 295/290 305/310 $0.85",
        "PLTR 20 07/06/26 (45) 45% 19/18 21/22 $0.50",
        "MU 80 07/06/26 (45) 55% 78/76 82/84 $0.50",
    ))
    strategy = _make_strategy()
    provider = MagicMock()
    _all_async_mocks(provider)

    result = await g.grade_paste(
        paste, strategy=strategy, provider=provider, clock=_frozen_clock(),
    )

    assert result.summary["rows_passed"] == 0
    assert result.summary["rows_failed"] == 5
    assert result.summary["failure_breakdown"]["universe"] == 5
    assert result.provider_calls_total == 0
    # Every provider method untouched.
    provider.get_option_chain.assert_not_called()
    provider.get_iv_rank.assert_not_called()
    provider.get_greeks.assert_not_called()
    provider.get_atm_iv.assert_not_called()


# ---------------------------------------------------------------------------
# division / strategy_slug stamping + strict_universe knob (Tasty Options)
# ---------------------------------------------------------------------------


async def test_grade_paste_with_tasty_options_division_stamps_audit():
    """Tasty Options passes division="tasty_options" — audit reflects it."""
    strategy = _make_strategy()
    provider = MagicMock()
    _all_async_mocks(provider)
    logger = MagicMock()

    paste = "ZZZZ 100 07/06/26 (45) 35% 100/95 105/110 $0.85"
    await g.grade_paste(
        paste, strategy=strategy, provider=provider, clock=_frozen_clock(),
        logger=logger,
        division="tasty_options",
        strategy_slug="tasty_options_iron_condor",
    )

    assert logger.log_event.called
    call = logger.log_event.call_args
    payload = call.kwargs["payload"]
    assert payload["division"] == "tasty_options"
    assert payload["strategy"] == "tasty_options_iron_condor"


async def test_grade_paste_off_watchlist_warns_not_fails_when_strict_false():
    """strict_universe=False: off-universe symbol skips gate 1 and is
    tagged watchlist_membership=off in measurements. Empty chain still
    fails at gate 2 — which is the expected outcome for ZZZZ — but the
    failure is `expiration_not_available`, NOT `universe`."""
    strategy = _make_strategy()
    strategy.strict_universe = False
    provider = MagicMock()
    _all_async_mocks(provider)
    provider.get_option_chain.return_value = []  # unresolvable

    paste = "ZZZZ 100 07/06/26 (45) 35% 100/95 105/110 $0.85"
    result = await g.grade_paste(
        paste, strategy=strategy, provider=provider, clock=_frozen_clock(),
    )

    assert len(result.rows) == 1
    row = result.rows[0]
    # Gate 1 didn't fire — gate 2 (expiration_on_chain) did instead because
    # the chain came back empty.
    assert row.failed_gate == "expiration_not_available"
    assert row.measurements.get("watchlist_membership") == "off"
    # Provider WAS called — proves the universe gate did not short-circuit.
    provider.get_option_chain.assert_called_once()


async def test_grade_paste_strict_true_still_fails_off_universe():
    """Regression guard: RH Joint behavior (strict_universe defaults True)
    MUST keep failing off-universe rows at gate 1 with zero provider calls."""
    strategy = _make_strategy()  # no strict_universe attribute → default True
    provider = MagicMock()
    _all_async_mocks(provider)
    logger = MagicMock()

    paste = "ZZZZ 100 07/06/26 (45) 35% 100/95 105/110 $0.85"
    result = await g.grade_paste(
        paste, strategy=strategy, provider=provider, clock=_frozen_clock(),
        logger=logger,
    )

    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.verdict == "FAIL"
    assert row.failed_gate == "universe"
    assert row.measurements.get("watchlist_membership") == "off"
    provider.get_option_chain.assert_not_called()

    # Audit defaults to RH Joint stamps when division/strategy_slug omitted.
    payload = logger.log_event.call_args.kwargs["payload"]
    assert payload["division"] == "robinhood_joint"
    assert payload["strategy"] == "robinhood_joint_iron_condor"
