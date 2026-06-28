"""Phase 1C (and beyond) deploy smoke — catches kwarg/signature drift.

Per `phased_deploy_lesson.md`: Phase 1B's first two deploy attempts
crashed at run() time even though import-only smokes passed:

  Attempt 1: TypeError: BitunixFuturesObserver.__init__() got an
             unexpected keyword argument 'pa_config'
  Attempt 2: TypeError: WebDeps.__init__() got an unexpected keyword
             argument 'bitunix_htf_provider'

Both bugs are call-site/constructor mismatches that surface only when
run() actually executes. This test set guards against the same class
of bug by:

1. AST-parsing main.py to find every call site for the load-bearing
   constructors (BitunixFuturesObserver, WebDeps, _start_web_server),
   then asserting every kwarg name appears in the callee's signature.
2. Importing the Phase 1C-new modules (`bitunix_position_reconciler`,
   web/data.py view builders) — catches top-level Import/SyntaxError.
3. Constructing the FastAPI app via `create_app(WebDeps(...))` —
   triggers route registration (catches route-decorator errors and
   import-at-route-time issues).

Run pre-deploy:  pytest tests/test_boot_smoke.py
Pass = safe to ship the bundle.  Fail = something drifted; fix it
before SCP.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = REPO_ROOT / "trading_corp" / "main.py"


def _find_call_kwargs(source: str, func_name: str) -> list[set[str]]:
    """Return one set of kwarg names per `func_name(...)` call in source.

    Matches bare-name calls (`WebDeps(...)`), not dotted (`mod.WebDeps(...)`)
    — main.py uses bare names for the constructors we care about. Skips
    None-arg keywords (`**unpack` has kw.arg == None).
    """
    tree = ast.parse(source)
    results: list[set[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == func_name:
                results.append({kw.arg for kw in node.keywords if kw.arg})
    return results


def test_main_py_BitunixFuturesObserver_call_kwargs_match_signature():
    """Phase 1B attempt-1 regression guard: `pa_config` kwarg mismatch."""
    from trading_corp.agents.divisions.bitunix_futures_observer import (
        BitunixFuturesObserver,
    )
    sig = inspect.signature(BitunixFuturesObserver.__init__)
    accepted = {p for p in sig.parameters if p != "self"}

    sites = _find_call_kwargs(MAIN_PY.read_text(encoding="utf-8"),
                              "BitunixFuturesObserver")
    assert sites, "no BitunixFuturesObserver() call site found in main.py"

    for kwargs in sites:
        extra = kwargs - accepted
        assert not extra, (
            f"main.py passes kwargs to BitunixFuturesObserver() that the "
            f"constructor does not accept: {sorted(extra)}.\n"
            f"  Constructor accepts: {sorted(accepted)}\n"
            f"  Call site passes:    {sorted(kwargs)}\n"
            "This is the Phase 1B attempt-1 crash signature. Either add "
            "the missing kwargs to BitunixFuturesObserver.__init__, or "
            "drop them from the main.py call site."
        )


def test_main_py_WebDeps_call_kwargs_match_dataclass():
    """Phase 1B attempt-2 regression guard: `bitunix_htf_provider` mismatch."""
    from trading_corp.web.app import WebDeps
    accepted = set(WebDeps.__dataclass_fields__)

    sites = _find_call_kwargs(MAIN_PY.read_text(encoding="utf-8"), "WebDeps")
    assert sites, "no WebDeps() call site found in main.py"

    for kwargs in sites:
        extra = kwargs - accepted
        assert not extra, (
            f"main.py passes kwargs to WebDeps() that the dataclass does "
            f"not declare: {sorted(extra)}.\n"
            f"  WebDeps fields: {sorted(accepted)}\n"
            f"  Call site:      {sorted(kwargs)}\n"
            "This is the Phase 1B attempt-2 crash signature. Either add "
            "the missing fields to WebDeps, or drop them from the main.py "
            "call site."
        )


def test_main_py_start_web_server_call_kwargs_match_signature():
    """Forward-looking guard for the helper that builds WebDeps.

    `_start_web_server` is invoked from run() with ~20 kwargs and
    constructs WebDeps internally. A mismatch between run()'s call
    site and `_start_web_server`'s def would also crash at run() time
    — same class of bug.
    """
    from trading_corp.main import _start_web_server
    sig = inspect.signature(_start_web_server)
    accepted = {p for p in sig.parameters}

    sites = _find_call_kwargs(MAIN_PY.read_text(encoding="utf-8"),
                              "_start_web_server")
    assert sites, "no _start_web_server() call site found in main.py"

    for kwargs in sites:
        extra = kwargs - accepted
        assert not extra, (
            f"main.py passes kwargs to _start_web_server() that the "
            f"function does not accept: {sorted(extra)}.\n"
            f"  Function accepts: {sorted(accepted)}\n"
            f"  Call site:        {sorted(kwargs)}\n"
            "Same class of bug as the Phase 1B WebDeps mismatch — sync "
            "the signatures."
        )


def test_phase1c_new_modules_import_cleanly():
    """Catches top-level ImportError / SyntaxError / NameError in
    Phase 1C-new modules — before they ship and crash boot."""
    # Position reconciler: new file shipped in Phase 1C
    import trading_corp.agents.divisions.bitunix_position_reconciler as recon  # noqa
    assert hasattr(recon, "run_reconciler_loop") or hasattr(recon, "reconciler_tick"), (
        "bitunix_position_reconciler module missing expected entry points "
        "(run_reconciler_loop / reconciler_tick)"
    )

    # New view builders in web/data.py
    from trading_corp.web import data as web_data
    for fn in ("build_bitunix_pa_view",
               "build_bitunix_decision_flow_view",
               "build_bitunix_htf_view"):
        assert hasattr(web_data, fn), (
            f"web.data.{fn} missing — Phase 1C dashboard refresh expects it"
        )


def _make_minimal_webdeps():
    """Build a WebDeps with mock dependencies sufficient for create_app()."""
    from trading_corp.web.app import WebDeps
    return WebDeps(
        db_url="sqlite:///:memory:",
        db_path="/tmp/_boot_smoke.db",
        mode="PAPER",
        logger_agent=MagicMock(),
        data_exec=MagicMock(),
        trend_agent=MagicMock(),
        portfolio=MagicMock(),
        pmcc_agent=MagicMock(),
        fidelity_agent=MagicMock(),
        paper_broker=MagicMock(),
        secrets=MagicMock(),
        risk_agent=MagicMock(),
        dry_run=False,
        lord_otter_agent=MagicMock(),
        market_cypher_agent=MagicMock(),
        telegram_channel=MagicMock(),
        research_firm=MagicMock(),
        pending_registry=MagicMock(),
        bitunix_observer=MagicMock(),
        bitunix_htf_provider=MagicMock(),
    )


def test_webdeps_construction_with_full_kwarg_set():
    """WebDeps accepts every kwarg main.py's call site passes.

    Pairs with the AST check above as belt-and-suspenders: if WebDeps
    grew a required field without a default, this test fails even when
    the AST kwarg-set check passes (because the AST check only flags
    EXTRA kwargs, not MISSING required ones).
    """
    deps = _make_minimal_webdeps()
    assert deps is not None
    # Spot-check the Phase 1B-added field is present and addressable
    assert deps.bitunix_htf_provider is not None
    assert deps.bitunix_observer is not None


def test_fastapi_app_constructs_and_registers_routes():
    """create_app(deps) builds the FastAPI app, loads templates, and
    registers all routes — catches route-time import errors that an
    import-only smoke would miss.
    """
    from trading_corp.web.app import create_app
    deps = _make_minimal_webdeps()
    app = create_app(deps)
    assert app is not None
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    # Sentinels: a few routes that must always exist
    assert "/healthz" in paths, (
        f"/healthz route missing — route registration may have silently "
        f"failed. Routes registered: {sorted(paths)}"
    )


def test_strategies_yaml_phase1c_blocks_parse():
    """Branch's strategies.yaml ships new blocks (pa_validation, htf_gate,
    htf_regime, trade_plan, fees). Verify the loaders accept the schema
    without raising — catches typos in keys or value-type mismatches
    before the YAML hits prod and prevents the service from booting.
    """
    import yaml
    yaml_path = REPO_ROOT / "config" / "strategies.yaml"
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert raw is not None

    bitunix = raw.get("bitunix_futures") or {}

    # PA validation
    from trading_corp.agents.strategies.bitunix_pa_validation import (
        PAValidationConfig,
    )
    PAValidationConfig.from_dict(bitunix.get("pa_validation") or {})

    # HTF regime
    from trading_corp.agents.strategies.bitunix_htf_regime import (
        HTFRegimeConfig,
    )
    HTFRegimeConfig.from_dict(bitunix.get("htf_regime") or {})

    # Trade plan (StrategyConfig + FeeConfig)
    from trading_corp.agents.strategies.trade_plan import (
        StrategyConfig,
        FeeConfig,
    )
    StrategyConfig.from_dict(bitunix.get("trade_plan") or {})
    FeeConfig.from_dict(bitunix.get("fees") or {})


def test_two_state_sfp_comes_up_trading_and_replay_disabled():
    """Two-state collapse (2026-06-27) boot guard.

    ★ The fail-safe default is HALTED, so a missing/non-"trading" `mode` would
    silently NOT start a division's loop. This test is the guard that the
    fail-safe default did NOT kill the LIVE BTC (SFP) edge: it asserts the
    shipped YAML + the EXACT main.py predicates resolve to "SFP trades, futures
    is inert, replay is off", and that main.py actually gates on them.
    """
    import yaml
    raw = yaml.safe_load((REPO_ROOT / "config" / "strategies.yaml").read_text(
        encoding="utf-8"))
    sfp = raw.get("bitunix_sfp") or {}
    fut = raw.get("bitunix_futures") or {}

    # (1) shipped config pins
    assert sfp.get("mode") == "trading", "bitunix_sfp must ship mode: trading"
    assert fut.get("mode") == "halted", "bitunix_futures must ship mode: halted"

    # (2) the EXACT main.py predicates → SFP arms, futures halts (fail-safe)
    sfp_trading = (str(sfp.get("mode", "halted")).lower() == "trading")
    fut_halted = (str(fut.get("mode", "halted")).lower() != "trading")
    assert sfp_trading is True, (
        "the SFP 15m loop would NOT start — the live BTC edge would be halted")
    assert fut_halted is True, "the futures observer must be HALTED-INERT"

    # (3) main.py actually gates on these + disables replay
    src = MAIN_PY.read_text(encoding="utf-8")
    assert 'name="bitunix-sfp-loop"' in src, "SFP loop start vanished from main.py"
    assert "and _sfp_trading" in src, (
        "SFP loop start must be gated on _sfp_trading (mode==trading)")
    assert "halted=_futures_halted" in src, (
        "futures observer must be constructed with halted=_futures_halted")
    assert "_REPLAY_ENABLED = False" in src, (
        "replay must be disabled (one-flag revert) — _REPLAY_ENABLED = False")
