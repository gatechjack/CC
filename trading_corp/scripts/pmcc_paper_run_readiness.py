"""PMCC (poor-man's covered call) paper-run readiness check.

Run this BEFORE relying on the `robinhood_pmcc` division's recommendations. It
walks the Bucket-B build's load-bearing wiring + gate reachability and prints a
green/red report plus an always-on KNOWN LIMITATIONS block. Exit code 0 = every
blocking check green, 1 = at least one blocking red.

Usage:

  python -m trading_corp.scripts.pmcc_paper_run_readiness
  python -m trading_corp.scripts.pmcc_paper_run_readiness --db sqlite:///path/test.db
  python -m trading_corp.scripts.pmcc_paper_run_readiness --skip-network

This is a readiness REPORT, NOT a promotion gate — it decides nothing, it reports
state for a human to read. It makes NO live broker queries, NO order proposals,
and NO prod calls. Check 9 exercises the deterministic gate paths against an
in-memory synthetic broker with the earnings source stubbed (network-free).

The four report primitives (CheckResult / ReadinessReport / _run / _format_report)
are COPIED from `ic_paper_run_readiness.py`, deliberately NOT imported: the PMCC
readiness gate must not depend on a division we don't touch (an IC refactor must
not silently break this). Four small duplicated pieces are cheaper than that
cross-division coupling.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# Report primitives — COPIED from ic_paper_run_readiness.py (do NOT import).
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    blocking: bool = True


@dataclass
class ReadinessReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def all_blocking_passed(self) -> bool:
        return all(c.ok for c in self.checks if c.blocking)

    @property
    def soft_failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.ok and not c.blocking]


def _run(name: str, fn: Callable[[], str], *, blocking: bool = True) -> CheckResult:
    try:
        detail = fn() or ""
        return CheckResult(name=name, ok=True, detail=detail, blocking=blocking)
    except Exception as e:
        return CheckResult(name=name, ok=False, detail=str(e), blocking=blocking)


# ---------------------------------------------------------------------------
# KNOWN LIMITATIONS — accepted states, ALWAYS surfaced (never modelled as
# pass/fail: doing so would make "READY (with N soft warnings)" the permanent
# normal output and train the reader to ignore the count).
# ---------------------------------------------------------------------------
KNOWN_LIMITATIONS: list[tuple[str, str]] = [
    ("B10 scheduler loop is COMPILE-VERIFIED ONLY",
     "the 15:00-ET pass's predicates (_terminal_should_fire, _pmcc_pending_symbols) and the "
     "scan subset filter ARE unit-tested; the _scheduled_pmcc_scan_loop while-loop + "
     "_on_terminal_scan glue are not exercisable in the test harness. "
     "VERIFY: boot-smoke + the first live 15:00 fire (terminal_dte_pass_done audit + any "
     "terminal_dte_order_result rows)."),
    ("B2 credit gate is PRE-FEE",
     "the conservative net captures SPREAD only (sell new @bid, buy old @mark); no fee source "
     "exists at proposal time (RH broker: none; FillEvent.fee is post-fill only). The abort/ship "
     "audit carries fees_included:false + a fee_gap note, so a pre-fee net is never called 'net'."),
    ("B9 earnings gate FAILS OPEN on missing yfinance data",
     "thin names lacking an earnings date SHIP the roll (fail-open), but the shipped-roll "
     "pmcc_roll_gates audit records gates.earnings == 'data_unavailable' so a roll that shipped "
     "because the source was DOWN is distinguishable from one that shipped because it was clear."),
]


# ---------------------------------------------------------------------------
# Minimal in-memory broker for check 9 (self-contained; imports nothing from
# tests/). Just enough of Broker + OptionBroker to drive the deterministic roll
# paths offline. It NEVER places an order.
# ---------------------------------------------------------------------------
class _SyntheticOptionBroker:
    name = "synthetic"
    paper = True

    def __init__(self, option_positions, expiry_dates=None, calls=None):
        self._op = option_positions
        self._exp = expiry_dates or {}
        self._calls = calls or {}

    async def connect(self): pass
    async def disconnect(self): pass
    async def quote(self, symbol): return 150.0
    async def cancel_order(self, order_id): return True

    async def snapshot(self):
        from trading_corp.brokers.base import AccountSnapshot
        return AccountSnapshot(account="synthetic", equity=100_000.0,
                               buying_power=50_000.0, cash=50_000.0, positions=[])

    async def place_order(self, order):
        raise AssertionError("readiness check must never place an order")

    async def get_option_positions_detail(self): return self._op
    async def get_expiration_dates(self, symbol): return self._exp.get(symbol, [])
    async def get_calls_for_expiry(self, symbol, expiry):
        return self._calls.get((symbol, expiry), [])


def _iso(days: int) -> str:
    from datetime import date, timedelta
    return (date.today() + timedelta(days=days)).isoformat()


def _opt(symbol, days, strike, qty, *, delta=0.5, avg=1.0, mark=None):
    return {"chain_symbol": symbol, "option_type": "call",
            "expiration_date": _iso(days), "strike_price": strike, "quantity": qty,
            "avg_price": avg, "delta": delta, "mark_price": mark, "dte": days,
            "option_id": f"{symbol}_{days}_{strike}"}


def _call(strike, delta, mark, dte):
    # OI/vol generous enough to clear even the tighter BLACK-SHEEP liquidity gate
    # (prod min_avg_options_volume=10000 for MSTR/TSLA) — check 9 runs against PROD
    # config, unlike the test fixtures' minimal (non-black-sheep) config.
    return {"strike_price": strike, "delta": delta, "mark_price": mark,
            "bid": round(mark - 0.05, 2), "ask": round(mark + 0.05, 2), "dte": dte,
            "open_interest": 20000, "volume": 20000, "option_id": f"c_{strike}_{dte}"}


def _leg_dict(o):
    return {"action": o.extra.get("action"), "strike": o.extra.get("strike"),
            "expiration": o.extra.get("expiration"), "price": o.extra.get("mark_per_share")}


def _has_key(obj, key: str) -> bool:
    if isinstance(obj, dict):
        if key in obj:
            return True
        return any(_has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key(v, key) for v in obj)
    return False


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def run_readiness_checks(
    *,
    db_url: str = "sqlite:///data/trading_corp.db",
    skip_network: bool = False,
) -> ReadinessReport:
    """Execute all readiness checks against the current environment."""
    import yaml

    report = ReadinessReport()

    def _load_strat() -> dict:
        with Path("config/strategies.yaml").open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    # ── BLOCK 1: divisions.yaml wiring ──
    def _check_divisions():
        from trading_corp.utils.divisions import load_divisions
        d = next((x for x in load_divisions() if x.slug == "robinhood_pmcc"), None)
        if d is None:
            raise AssertionError("robinhood_pmcc missing from divisions.yaml")
        if d.strategy != "robinhood_pmcc":
            raise AssertionError(f"strategy={d.strategy!r}, expected 'robinhood_pmcc'")
        if not d.enabled:
            raise AssertionError("robinhood_pmcc is enabled=false")
        return f"strategy={d.strategy}  broker={getattr(d, 'broker', None)}  enabled={d.enabled}"
    report.checks.append(_run("divisions.yaml wiring", _check_divisions))

    # ── BLOCK 2: strategies.yaml block loads with the required keys ──
    def _check_strategies_yaml():
        blk = _load_strat().get("robinhood_pmcc")
        if blk is None:
            raise AssertionError("robinhood_pmcc block missing from strategies.yaml")
        for k in ("enabled", "auto_execute", "universe_source"):
            if k not in blk:
                raise AssertionError(f"robinhood_pmcc.{k} missing")
        return f"enabled={blk['enabled']}  universe_source={blk['universe_source']}"
    report.checks.append(_run("strategies.yaml block", _check_strategies_yaml))

    # ── BLOCK 3: auto_execute is FALSE (paper-mode invariant) ──
    def _check_auto_execute_false():
        blk = _load_strat().get("robinhood_pmcc") or {}
        if blk.get("auto_execute") is not False:
            raise AssertionError(
                f"auto_execute is {blk.get('auto_execute')!r}; MUST be false (paper-mode invariant)"
            )
        return "auto_execute=false"
    report.checks.append(_run("auto_execute is FALSE", _check_auto_execute_false))

    # ── BLOCK 4: risk.yaml pmcc section ──
    def _check_risk_yaml():
        with Path("config/risk.yaml").open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        sec = data.get("pmcc")
        if sec is None:
            raise AssertionError("risk.yaml: missing pmcc section")
        return f"pmcc keys: {sorted(sec.keys())}"
    report.checks.append(_run("risk.yaml pmcc section", _check_risk_yaml))

    # ── BLOCK 5: PMCCAgent instantiates against prod yaml ──
    def _check_agent_instantiates():
        from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent
        a = PMCCAgent(db_url=db_url)
        _ = a._short_target_delta, a._leap_min_dte, a._short_target_dte
        return f"short_target_delta={a._short_target_delta}  leap_min_dte={a._leap_min_dte}"
    report.checks.append(_run("PMCCAgent instantiates", _check_agent_instantiates))

    # ── BLOCK 6: agent_state writable/readable ──
    def _check_agent_state():
        from trading_corp.persistence import db
        import time
        k = f"pmcc_readiness_probe_{int(time.time())}"
        db.set_agent_state("robinhood_pmcc", k, {"probe": True}, db_url=db_url)
        rec = db.load_agent_state("robinhood_pmcc", k, db_url=db_url)
        if rec is None or rec[0].get("probe") is not True:
            raise AssertionError("agent_state round-trip failed")
        return "round-trip OK"
    report.checks.append(_run("agent_state read/write", _check_agent_state))

    # ── BLOCK 7: audit_event table reachable ──
    def _check_audit_table():
        from trading_corp.persistence import db
        with db.connect(db_url) as conn:
            n = conn.execute("SELECT COUNT(*) AS c FROM audit_event").fetchone()["c"]
        return f"{n} existing rows"
    report.checks.append(_run("audit_event table", _check_audit_table))

    # ── BLOCK 8: gate WIRING resolves (symbols + signatures exist) ──
    def _check_gate_wiring():
        from trading_corp.agents.divisions import pmcc_robinhood as P
        from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent
        import trading_corp.main as M
        problems = []
        if P._OVERRIDE_KINDS != ("hold_override", "net_debit_justified", "earnings_override"):
            problems.append(f"_OVERRIDE_KINDS={P._OVERRIDE_KINDS}")
        if P._WEEKLY_FALLBACK_MAX_DTE != 60:
            problems.append(f"_WEEKLY_FALLBACK_MAX_DTE={P._WEEKLY_FALLBACK_MAX_DTE}")
        if not callable(getattr(P, "_short_roll_credit", None)):
            problems.append("_short_roll_credit (B2 shared helper) missing")
        for m in ("_earnings_gate_state", "_deterministic_roll_allowed", "_audit_roll_abort",
                  "_terminal_dte_time_release", "_override_kind", "scan",
                  "propose_orders_for_pair", "_propose_roll_short", "_find_best_weekly"):
            if not callable(getattr(PMCCAgent, m, None)):
                problems.append(f"PMCCAgent.{m} missing")
        if "after_dte" not in inspect.signature(PMCCAgent._find_best_weekly).parameters:
            problems.append("_find_best_weekly missing after_dte (B7)")
        scan_params = inspect.signature(PMCCAgent.scan).parameters
        for p in ("zero_dte_only", "skip_symbols"):
            if p not in scan_params:
                problems.append(f"scan missing {p} (B10)")
        for f in ("_terminal_should_fire", "_pmcc_pending_symbols", "_scan_should_fire"):
            if not callable(getattr(M, f, None)):
                problems.append(f"main.{f} missing (B10/B11)")
        if problems:
            raise AssertionError("gate wiring: " + "; ".join(problems))
        return "gate-0 / B1 / B2 / B4 / B7 / B9 / B10 / B11 symbols + signatures resolve"
    report.checks.append(_run("gate wiring resolves", _check_gate_wiring))

    # ── BLOCK 9: gate BEHAVIOR is clean (synthetic, network-free) ──
    def _check_gate_behavior():
        import trading_corp.utils.market_data as _md
        from datetime import datetime, timezone, timedelta as _td
        _orig = getattr(_md, "get_next_earnings", None)
        # Stub earnings CLEAR (far-future) so the run is deterministic + network-free.
        _md.get_next_earnings = lambda symbol, *a, **k: datetime.now(timezone.utc) + _td(days=90)
        try:
            from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent, PMCCAnalysis
            import tests.pmcc_regression.detectors as D
            agent = PMCCAgent()   # prod config, no db, no logger -> audits no-op

            # 9a: a CLEAN credit roll_leap emits structurally-clean legs.
            leap_exp, wk = _iso(500), _iso(14)
            broker = _SyntheticOptionBroker(
                option_positions=[
                    _opt("MSTR", 400, 160.0, 1.0, delta=0.97, avg=23.8, mark=58.05),  # LEAP
                    _opt("MSTR", 7, 175.0, -1.0, delta=0.30, avg=2.5, mark=1.50),     # short
                ],
                expiry_dates={"MSTR": [wk, leap_exp]},
                calls={("MSTR", leap_exp): [_call(180.0, 0.85, 20.0, 500)],
                       ("MSTR", wk): [_call(190.0, 0.30, 2.00, 14)]},
            )
            analysis = PMCCAnalysis(symbol="MSTR", action="roll_leap", confidence=0.9,
                                    urgency="elevated", summary="", rationale="",
                                    target_delta=0.30, target_dte=14)
            legs = asyncio.run(agent.propose_orders_for_pair(broker, "MSTR", analysis))
            rec = D.RecRecord.from_legs([_leg_dict(o) for o in legs], llm_action="ROLL_LEAP")
            tripped = [n for n, det in (
                ("close_without_recover", D.close_without_recover),
                ("same_expiry_roll", D.same_expiry_roll),
                ("cost_ignorant_leap_roll", D.cost_ignorant_leap_roll),
                ("b4_fully_naked", D.b4_fully_naked),
                ("b4_uncovered", D.b4_uncovered),
            ) if det(rec)]
            if tripped:
                raise AssertionError(
                    f"clean roll_leap tripped detectors {tripped} "
                    f"(legs={[o.extra.get('action') for o in legs]})"
                )
            if len(legs) != 4:
                raise AssertionError(f"clean roll_leap expected 4 legs, got {len(legs)}")

            # 9b: a SPARSE chain aborts atomically (B4) — never a close-without-recover.
            sparse = _SyntheticOptionBroker(option_positions=[
                _opt("MSTR", 400, 160.0, 1.0, delta=0.85, mark=58.05),
                _opt("MSTR", 2, 175.0, -1.0, delta=0.30, avg=2.5, mark=1.50),
            ])
            pos = next(p for p in asyncio.run(agent.detect_existing_legs(sparse))
                       if p.symbol == "MSTR")
            aborted = asyncio.run(agent._propose_roll_short("MSTR", pos, sparse))
            if aborted != []:
                raise AssertionError(
                    f"sparse-chain roll must abort atomically; got {len(aborted)} legs"
                )
            return "clean roll_leap: 0 pathologies (B3/B4/B7); sparse chain: atomic abort (B4)"
        finally:
            if _orig is not None:
                _md.get_next_earnings = _orig
    report.checks.append(_run("gate behavior clean (synthetic)", _check_gate_behavior))

    # ── BLOCK 10: config sanity — retired keys ABSENT, live keys present ──
    def _check_config_sanity():
        data = _load_strat()
        blk = data.get("robinhood_pmcc") or {}
        ll = blk.get("long_leg") or {}
        dead = [k for k in ("delta_min", "delta_max", "delta_high_conviction",
                            "delta_speculative") if k in ll]
        if dead:
            raise AssertionError(f"retired long_leg delta keys REINTRODUCED (B8b): {dead}")
        if not _has_key(blk, "earnings_buffer_days"):
            raise AssertionError("earnings_buffer_days missing from the PMCC block (B9)")
        for k in ("enabled", "universe_source", "position_min_shares"):
            if k not in blk:
                raise AssertionError(f"robinhood_pmcc.{k} missing (block not intact)")
        return "no dead long_leg delta keys; earnings_buffer_days present; block intact"
    report.checks.append(_run("config sanity", _check_config_sanity))

    # ── BLOCK 11: NYSE calendar loads (B11 holiday guard + B10 window depend on it) ──
    def _check_calendar():
        from trading_corp.utils.market_hours import default_calendar
        from datetime import datetime
        cal = default_calendar()
        _ = cal.close_time_et(datetime.now())   # resolves (may be None on a closed day)
        return "default_calendar() loads + close_time_et resolves"
    report.checks.append(_run("NYSE calendar loads", _check_calendar))

    # ── SOFT: network reachability (skipped with --skip-network) ──
    if not skip_network:
        def _check_vix():
            from trading_corp.utils.market_data import get_vix
            v = get_vix()
            if v is None:
                raise AssertionError("get_vix() returned None (yfinance unreachable/rate-limited)")
            return f"VIX={v:.2f}"
        report.checks.append(_run("VIX reachable", _check_vix, blocking=False))

        def _check_earnings_source():
            from trading_corp.utils.market_data import get_next_earnings
            d = get_next_earnings("AAPL")
            if d is None:
                raise AssertionError("get_next_earnings('AAPL') returned None (B9 fails open at runtime)")
            return f"get_next_earnings('AAPL')={d}"
        report.checks.append(_run("yfinance earnings source (B9)", _check_earnings_source,
                                  blocking=False))

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _format_report(report: ReadinessReport) -> str:
    lines = [
        "",
        "PMCC (Poor-Man's Covered Call) - Paper-Run Readiness Check",
        "=" * 78,
    ]
    for c in report.checks:
        marker = " OK " if c.ok else "FAIL"
        tag = "BLOCK" if c.blocking else "SOFT "
        detail = f"   ({c.detail})" if c.detail else ""
        lines.append(f"  [{marker}] [{tag}] {c.name}{detail}")
    lines.append("=" * 78)
    lines.append("  KNOWN LIMITATIONS (accepted state - always surfaced, not pass/fail):")
    for title, desc in KNOWN_LIMITATIONS:
        lines.append(f"    - {title}")
        lines.append(f"        {desc}")
    lines.append("=" * 78)
    if report.all_blocking_passed:
        soft_fail = report.soft_failures
        if soft_fail:
            lines.append(
                f"  STATUS: READY (with {len(soft_fail)} soft warning(s) - see SOFT lines)"
            )
        else:
            lines.append("  STATUS: READY - all blocking checks green")
    else:
        n_red = sum(1 for c in report.checks if not c.ok and c.blocking)
        lines.append(f"  STATUS: NOT READY - {n_red} blocking failure(s)")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="pmcc-readiness",
        description="PMCC paper-run readiness check (report-only; not a promotion gate).",
    )
    p.add_argument("--db", default="sqlite:///data/trading_corp.db",
                   help="SQLite db_url (default %(default)s)")
    p.add_argument("--skip-network", action="store_true",
                   help="skip VIX + yfinance-earnings reachability checks")
    args = p.parse_args(argv)
    report = run_readiness_checks(db_url=args.db, skip_network=args.skip_network)
    print(_format_report(report))
    return 0 if report.all_blocking_passed else 1


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
