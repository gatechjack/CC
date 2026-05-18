"""Iron-condor paper-run readiness check.

Run this BEFORE kicking off the 90-day paper run. It walks through the
step-1..13 build and verifies every load-bearing wiring point resolves
cleanly in the current environment, then prints a green/red status
line plus any failures. Exit code 0 = green, 1 = at least one red.

Usage:

  python -m trading_corp.scripts.ic_paper_run_readiness
  python -m trading_corp.scripts.ic_paper_run_readiness --db sqlite:///path/to/test.db
  python -m trading_corp.scripts.ic_paper_run_readiness --skip-network

Checks (all soft except the "blocking" ones marked BLOCK):

  BLOCK 1.  config/divisions.yaml: robinhood_joint entry present with
            strategy=robinhood_joint_iron_condor, broker=robinhood,
            account_filter=joint, enabled=true.
  BLOCK 2.  config/strategies.yaml: robinhood_joint_iron_condor block
            loads without missing-key warnings.
  BLOCK 3.  config/risk.yaml: overrides.robinhood_joint_iron_condor
            section present with per_trade_risk_pct.
  BLOCK 4.  Strategy module instantiates cleanly against production yaml.
  BLOCK 5.  Division shell instantiates and finds its config.
  BLOCK 6.  agent_state table reachable + writable + readable.
  BLOCK 7.  audit_event table reachable.
        8.  Macro calendar loads (config/macro_calendar.yaml).
        9.  Ex-dividend calendar loads (config/ex_dividend_calendar.yaml).
       10.  IVR utility resolves (without network — just import).
       11.  Combo registry constructs.
       12.  Telegram batcher constructs.
       13.  Telemetry queries return 0-row results cleanly on empty db.
   SOFT 14.  Network: get_vix() returns a number (skipped with --skip-network).

The "BLOCK" checks must all pass before live kickoff. The "SOFT" check
is informational — VIX network reachability would only matter at
runtime, and the strategy fail-safes on `get_vix() is None` by
escalating to Board anyway.
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from typing import Callable


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


def run_readiness_checks(
    *,
    db_url: str = "sqlite:///data/trading_corp.db",
    skip_network: bool = False,
) -> ReadinessReport:
    """Execute all readiness checks against the current environment.

    Returns a `ReadinessReport`; the CLI wraps this with stdout
    formatting + exit-code translation.
    """
    from pathlib import Path

    report = ReadinessReport()

    # ── BLOCK 1: divisions.yaml wiring ──
    def _check_divisions():
        from trading_corp.utils.divisions import load_divisions
        divs = load_divisions()
        rj = next((d for d in divs if d.slug == "robinhood_joint"), None)
        if rj is None:
            raise AssertionError("robinhood_joint missing from divisions.yaml")
        if rj.strategy != "robinhood_joint_iron_condor":
            raise AssertionError(
                f"divisions.yaml: strategy is {rj.strategy!r}, expected "
                f"'robinhood_joint_iron_condor'"
            )
        if rj.broker != "robinhood":
            raise AssertionError(f"broker={rj.broker!r}, expected 'robinhood'")
        if rj.account_filter != "joint":
            raise AssertionError(
                f"account_filter={rj.account_filter!r}, expected 'joint'"
            )
        if not rj.enabled:
            raise AssertionError("robinhood_joint is enabled=false")
        return f"strategy={rj.strategy}  account_filter={rj.account_filter}"
    report.checks.append(_run("divisions.yaml wiring", _check_divisions))

    # ── BLOCK 2: strategies.yaml loads without missing keys ──
    def _check_strategies_yaml():
        import io
        from trading_corp.agents.strategies.robinhood_joint_iron_condor import (
            RobinhoodJointIronCondorAgent,
        )
        # Capture WARNING-level logs from the strategy module so we can
        # detect missing-key warnings.
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setLevel(logging.WARNING)
        log_target = logging.getLogger(
            "trading_corp.agents.strategies.robinhood_joint_iron_condor"
        )
        log_target.addHandler(handler)
        try:
            a = RobinhoodJointIronCondorAgent()
            assert a.enabled, "strategy is enabled=false"
            warnings = buf.getvalue()
        finally:
            log_target.removeHandler(handler)
        if "missing" in warnings and "IronCondor config" in warnings:
            raise AssertionError(
                f"strategies.yaml missing required keys; warnings:\n{warnings}"
            )
        return f"enabled={a.enabled}  auto_execute={a.auto_execute}"
    report.checks.append(_run("strategies.yaml block", _check_strategies_yaml))

    # ── BLOCK 3: risk.yaml override ──
    def _check_risk_yaml():
        import yaml
        with Path("config/risk.yaml").open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        section = (data.get("overrides") or {}).get("robinhood_joint_iron_condor")
        if section is None:
            raise AssertionError("missing overrides.robinhood_joint_iron_condor")
        if "per_trade_risk_pct" not in section:
            raise AssertionError("per_trade_risk_pct missing from override section")
        return f"per_trade_risk_pct={section['per_trade_risk_pct']}"
    report.checks.append(_run("risk.yaml override", _check_risk_yaml))

    # ── BLOCK 4: strategy instantiates ──
    def _check_strategy_instantiates():
        from trading_corp.agents.strategies.robinhood_joint_iron_condor import (
            RobinhoodJointIronCondorAgent,
        )
        a = RobinhoodJointIronCondorAgent(db_url=db_url)
        # Touch a few properties to force config read.
        _ = a.enabled, a.universe, a.wing_widths
        return f"universe={a.universe}"
    report.checks.append(_run("strategy instantiates", _check_strategy_instantiates))

    # ── BLOCK 5: division shell ──
    def _check_division():
        from trading_corp.agents.divisions.robinhood_joint import (
            RobinhoodJointAgent,
        )
        d = RobinhoodJointAgent()
        if not d.enabled:
            raise AssertionError("division enabled=false")
        return f"slug={d.slug}  account_filter={d.account_filter}"
    report.checks.append(_run("division shell", _check_division))

    # ── BLOCK 6: agent_state writable/readable ──
    def _check_agent_state():
        from trading_corp.persistence import db
        import time
        test_key = f"readiness_probe_{int(time.time())}"
        db.set_agent_state(
            "robinhood_joint_iron_condor", test_key, {"probe": True},
            db_url=db_url,
        )
        rec = db.load_agent_state(
            "robinhood_joint_iron_condor", test_key, db_url=db_url,
        )
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

    # ── 8: macro calendar ──
    def _check_macro_calendar():
        from trading_corp.data.macro_calendar import MacroCalendar
        cal = MacroCalendar.load()
        return f"{len(cal._events)} events loaded"
    report.checks.append(_run("macro calendar", _check_macro_calendar))

    # ── 9: ex-dividend calendar ──
    def _check_exdiv_calendar():
        from trading_corp.data.ex_dividend_calendar import ExDividendCalendar
        cal = ExDividendCalendar.load()
        return f"{len(cal._events)} events loaded"
    report.checks.append(_run("ex-dividend calendar", _check_exdiv_calendar))

    # ── 10: IVR utility importable ──
    def _check_iv_util():
        from trading_corp.utils.iv import calc_iv_rank, calc_atm_iv  # noqa
        return "calc_iv_rank + calc_atm_iv importable"
    report.checks.append(_run("IVR utility importable", _check_iv_util))

    # ── 11: combo registry constructs ──
    def _check_combo_registry():
        from trading_corp.comms.pending_combo_registry import PendingComboRegistry
        r = PendingComboRegistry()
        assert r.list_pending() == []
        return "constructs"
    report.checks.append(_run("combo registry", _check_combo_registry))

    # ── 12: Telegram batcher constructs ──
    def _check_telegram_batcher():
        from trading_corp.comms.telegram_batcher import TelegramBatcher

        class _Stub:
            async def push(self, text): pass
        b = TelegramBatcher(_Stub(), batch_window_sec=60.0)
        assert b.pending_count == 0
        return "constructs"
    report.checks.append(_run("Telegram batcher", _check_telegram_batcher))

    # ── 13: telemetry queries against empty db ──
    def _check_telemetry():
        from trading_corp.agents.ic_telemetry import (
            adjustment_outcome_stats, combo_pnl_report,
            combo_slippage_stats, scan_filter_counters, win_rate_by_ivr,
        )
        combo_pnl_report(db_url=db_url)
        win_rate_by_ivr(db_url=db_url)
        adjustment_outcome_stats(db_url=db_url)
        scan_filter_counters(db_url=db_url)
        combo_slippage_stats(db_url=db_url)
        return "all 5 queries return cleanly"
    report.checks.append(_run("telemetry queries", _check_telemetry))

    # ── SOFT 14: network — VIX reachable ──
    if not skip_network:
        def _check_vix():
            from trading_corp.utils.market_data import get_vix
            vix = get_vix()
            if vix is None:
                raise AssertionError(
                    "get_vix() returned None (yfinance unreachable or rate-limited)"
                )
            return f"VIX={vix:.2f}"
        report.checks.append(_run("VIX network reachability", _check_vix,
                                  blocking=False))

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_report(report: ReadinessReport) -> str:
    lines = [
        "",
        "Iron Condor - Paper-Run Readiness Check",
        "=" * 78,
    ]
    for c in report.checks:
        marker = " OK " if c.ok else "FAIL"
        tag = "BLOCK" if c.blocking else "SOFT "
        detail = f"   ({c.detail})" if c.detail else ""
        lines.append(f"  [{marker}] [{tag}] {c.name}{detail}")
    lines.append("=" * 78)
    if report.all_blocking_passed:
        soft_fail = report.soft_failures
        if soft_fail:
            lines.append(
                f"  STATUS: READY (with {len(soft_fail)} soft warning(s) - "
                "see SOFT lines)"
            )
        else:
            lines.append("  STATUS: READY - paper-run kickoff approved")
    else:
        n_red = sum(1 for c in report.checks if not c.ok and c.blocking)
        lines.append(f"  STATUS: NOT READY - {n_red} blocking failure(s)")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="ic-readiness",
        description="Iron-condor paper-run readiness check.",
    )
    p.add_argument("--db", default="sqlite:///data/trading_corp.db",
                   help="SQLite db_url (default %(default)s)")
    p.add_argument("--skip-network", action="store_true",
                   help="skip VIX network reachability check")
    args = p.parse_args(argv)
    report = run_readiness_checks(
        db_url=args.db, skip_network=args.skip_network,
    )
    print(_format_report(report))
    return 0 if report.all_blocking_passed else 1


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
