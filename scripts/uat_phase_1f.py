"""End-to-end UAT for Phase 1f (bull/bear debate gate).

What this exercises (against a temp DB, no external APIs by default):

  1. Gate function: aligned -> skip, disagreement -> fire, variance ->
     fire, refused experts excluded from both arms, single-voice skip.
  2. Bull / bear / judge experts produce valid deterministic output
     when no LLM is available — the audit trail must be complete even
     in offline mode.
  3. Engagement graph: gate fires on disagreeing experts and BOTH audit
     rows (research_debate_invoked + research_debate_completed) land.
  4. Synthesizer integration:
       - Thesis carries debate_audit_row_id when fired
       - Thesis carries 'debate (gate fired)' key_driver entry
       - PositionContext surfaces 'debate fired:' risk_flag
       - TradeConfirmation carries debate_audit_row_id
       - CandidateRecommendation NEVER fires gate (multi-symbol policy)
  5. (Optional) when ANTHROPIC_API_KEY is set, run ONE real engagement
     end-to-end with disagreeing experts and print the LLM-narrated
     judge synthesis + bull/bear quality scores.

Uses fake experts (no yfinance, no Anthropic by default) so the
deterministic runs are fast + offline. Real-LLM section is opt-in via
ANTHROPIC_API_KEY in the environment OR in the repo .env.

Run from repo root:
    python scripts/uat_phase_1f.py
    ANTHROPIC_API_KEY=sk-ant-... python scripts/uat_phase_1f.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Auto-load .env so ANTHROPIC_API_KEY (and other secrets) are picked up
# without the user having to export them.
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.research import schemas
from trading_corp.agents.research.debate_gate import evaluate_debate_gate
from trading_corp.agents.research.engagement import (
    ResearchFirmDeps, run_engagement,
)
from trading_corp.agents.research.experts.debate import run_bear, run_bull, run_judge
from trading_corp.agents.research.graph import build_engagement_graph
from trading_corp.persistence.db import init_db

sys.path.insert(0, str(_REPO_ROOT / "tests"))
from test_research_engagement_e2e import (  # noqa: E402
    FakeMacroExpert, FakeSentimentExpert, FakeTechnicalExpert,
)


GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"


def _ok(label: str, detail: str = "") -> None:
    print(f"  {GREEN}PASS{RESET}  {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"  {RED}FAIL{RESET}  {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))


def _section(name: str) -> None:
    print(f"\n{YELLOW}-- {name} --{RESET}")


def _info(label: str, detail: str = "") -> None:
    print(f"  {DIM}info{RESET}  {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))


def _r(role: str, conf: float, lean: str | None, sufficient: bool = True) -> schemas.ExpertReport:
    return schemas.ExpertReport(
        role=role,
        engagement_id="uat",
        symbol="AAPL",
        summary=f"{role} report",
        confidence_score=conf,
        directional_lean=lean,
        data_sufficiency=sufficient,
        refusal_reason=None if sufficient else "no data",
    )


def _build_deps(
    db_url: str, *, leans: dict[str, str | None], confidences: dict[str, float] | None = None,
) -> tuple[ResearchFirmDeps, LoggerAgent]:
    """Build a ResearchFirmDeps with three fake experts. `leans` is
    {role: directional_lean}; `confidences` overrides the default 0.7."""
    confidences = confidences or {}
    logger_agent = LoggerAgent(db_url)
    experts = {
        "technical": FakeTechnicalExpert(
            lean=leans.get("technical", "bullish"),
            confidence=confidences.get("technical", 0.7),
        ),
        "macro": FakeMacroExpert(
            lean=leans.get("macro", "bullish"),
            confidence=confidences.get("macro", 0.65),
        ),
        "sentiment": FakeSentimentExpert(
            lean=leans.get("sentiment", "bullish"),
            confidence=confidences.get("sentiment", 0.6),
        ),
    }
    graph = build_engagement_graph(
        logger_agent, experts=experts, checkpointer=None,
    )
    return (
        ResearchFirmDeps(
            logger_agent=logger_agent, experts=experts, graph=graph,
        ),
        logger_agent,
    )


async def main() -> int:
    failures = 0

    # ── Section 1: gate function ──────────────────────────────────────
    _section("1. evaluate_debate_gate (pure function)")

    aligned = [
        _r("technical", 0.7, "bullish"),
        _r("macro", 0.65, "bullish"),
        _r("sentiment", 0.6, "bullish"),
    ]
    fire, reason = evaluate_debate_gate(aligned)
    if fire is False and reason is None:
        _ok("aligned experts -> skip")
    else:
        _fail("aligned should skip", f"got fire={fire} reason={reason!r}")
        failures += 1

    disagree = [
        _r("technical", 0.7, "bullish"),
        _r("macro", 0.65, "bearish"),
    ]
    fire, reason = evaluate_debate_gate(disagree)
    if fire is True and reason and "leans split" in reason:
        _ok("disagreement -> fire", reason)
    else:
        _fail("disagreement should fire", f"reason={reason!r}")
        failures += 1

    spread = [
        _r("technical", 0.0, "bullish"),
        _r("macro", 1.0, "bullish"),
    ]
    fire, reason = evaluate_debate_gate(spread)
    if fire is True and reason and "variance" in reason:
        _ok("variance arm -> fire", reason)
    else:
        _fail("variance spread should fire", f"reason={reason!r}")
        failures += 1

    refused_disagree = [
        _r("technical", 0.7, "bullish"),
        _r("macro", 0.0, "bearish", sufficient=False),
    ]
    fire, _ = evaluate_debate_gate(refused_disagree)
    if fire is False:
        _ok("refused expert excluded from disagreement arm")
    else:
        _fail("refused expert leaked into gate")
        failures += 1

    fire, _ = evaluate_debate_gate([_r("technical", 0.99, "bullish")])
    if fire is False:
        _ok("single voice cannot fire")
    else:
        _fail("single voice should not fire")
        failures += 1

    # ── Section 2: bull / bear / judge deterministic ───────────────────
    # Force offline mode by clearing the API key for this section so we
    # genuinely test the deterministic fallback. Restore after.
    _section("2. Bull / bear / judge (deterministic, offline)")
    _saved_key = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        bull_text, bull_cost = await run_bull(
            symbol="AAPL", invoked_reason="leans split bullish=1, bearish=1",
            reports=disagree,
        )
        bear_text, bear_cost = await run_bear(
            symbol="AAPL", invoked_reason="leans split bullish=1, bearish=1",
            reports=disagree,
        )
        if bull_text and "stance=bull" in bull_text:
            _ok("bull deterministic argument generated", f"len={len(bull_text)}")
        else:
            _fail("bull deterministic output missing")
            failures += 1
        if bear_text and "stance=bear" in bear_text:
            _ok("bear deterministic argument generated", f"len={len(bear_text)}")
        else:
            _fail("bear deterministic output missing")
            failures += 1

        outcome, judge_cost = await run_judge(
            engagement_id="uat", symbol="AAPL",
            invoked_reason="leans split", bull_case=bull_text, bear_case=bear_text,
        )
        if isinstance(outcome, schemas.DebateOutcome):
            _ok("judge returned DebateOutcome (deterministic)",
                f"bull_evidence={outcome.judge_bull_score.evidence_quality:.2f} "
                f"bear_evidence={outcome.judge_bear_score.evidence_quality:.2f}")
        else:
            _fail("judge returned wrong shape", f"got {type(outcome).__name__}")
            failures += 1

        if bull_cost == 0.0 and bear_cost == 0.0 and judge_cost == 0.0:
            _ok("offline mode pays no LLM cost")
        else:
            _fail("offline mode should be zero cost",
                  f"bull={bull_cost} bear={bear_cost} judge={judge_cost}")
            failures += 1
    finally:
        if _saved_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = _saved_key

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        # ── Section 3: engagement graph - gate skip ──────────────────
        _section("3. Engagement: aligned experts -> gate skips")
        db_url = f"sqlite:///{Path(td).as_posix()}/uat_skip.db"
        init_db(db_url)
        deps, log_skip = _build_deps(
            db_url,
            leans={"technical": "bullish", "macro": "bullish", "sentiment": "bullish"},
        )
        spec = schemas.EngagementSpec(
            requesting_division="board",
            product_type="thesis",
            asset_class="equity",
            scope=schemas.ThesisScope(symbol="AAPL"),
            triggered_by="telegram",
            triggered_ts=datetime.now(timezone.utc).isoformat(),
        )
        product = await run_engagement(spec, deps=deps)
        kinds_skip = {e["kind"] for e in log_skip.recent_events(limit=40)}
        if isinstance(product, schemas.Thesis) and product.debate_audit_row_id is None:
            _ok("Thesis emitted with debate_audit_row_id=None (gate skipped)")
        else:
            _fail("expected Thesis without debate_audit_row_id",
                  f"got {type(product).__name__}")
            failures += 1
        if "research_debate_invoked" not in kinds_skip:
            _ok("no research_debate_invoked audit row written")
        else:
            _fail("debate fired when it shouldn't have")
            failures += 1

        # ── Section 4: engagement graph - gate fire on Thesis ────────
        _section("4. Engagement: disagreement -> gate fires (Thesis)")
        db_url = f"sqlite:///{Path(td).as_posix()}/uat_fire.db"
        init_db(db_url)
        deps, log_fire = _build_deps(
            db_url,
            leans={"technical": "bullish", "macro": "bearish", "sentiment": "bullish"},
        )
        spec = schemas.EngagementSpec(
            requesting_division="board",
            product_type="thesis",
            asset_class="equity",
            scope=schemas.ThesisScope(symbol="AAPL"),
            triggered_by="telegram",
            triggered_ts=datetime.now(timezone.utc).isoformat(),
        )
        product = await run_engagement(spec, deps=deps)
        events_fire = log_fire.recent_events(limit=40)
        kinds_fire = [e["kind"] for e in events_fire]

        if isinstance(product, schemas.Thesis):
            if product.debate_audit_row_id is not None:
                _ok("Thesis carries debate_audit_row_id",
                    f"id={product.debate_audit_row_id}")
            else:
                _fail("Thesis missing debate_audit_row_id")
                failures += 1
            if any("debate" in d.lower() for d in product.key_drivers):
                _ok("Thesis key_drivers includes debate-tagged entry")
            else:
                _fail("Thesis missing debate driver")
                failures += 1
        else:
            _fail("Thesis not emitted")
            failures += 1

        if "research_debate_invoked" in kinds_fire:
            _ok("research_debate_invoked audit row written")
        else:
            _fail("missing research_debate_invoked")
            failures += 1
        if "research_debate_completed" in kinds_fire:
            _ok("research_debate_completed audit row written")
            completed = next(e for e in events_fire if e["kind"] == "research_debate_completed")
            outcome_d = (completed["payload"] or {}).get("outcome") or {}
            for k in ("symbol", "bull_case", "bear_case",
                      "judge_bull_score", "judge_bear_score", "synthesis"):
                if k not in outcome_d:
                    _fail(f"DebateOutcome missing {k} in audit payload")
                    failures += 1
                    break
            else:
                _ok("DebateOutcome serialized fully in audit row")
        else:
            _fail("missing research_debate_completed")
            failures += 1

        # ── Section 5: PositionContext - risk_flag surface ───────────
        _section("5. Engagement: PositionContext surfaces debate in risk_flags")
        db_url = f"sqlite:///{Path(td).as_posix()}/uat_pc.db"
        init_db(db_url)
        logger_pc = LoggerAgent(db_url)
        experts = {
            "macro": FakeMacroExpert(lean="bullish", confidence=0.7),
            "sentiment": FakeSentimentExpert(lean="bearish", confidence=0.6),
        }
        graph = build_engagement_graph(
            logger_pc, experts=experts, checkpointer=None,
        )
        deps = ResearchFirmDeps(
            logger_agent=logger_pc, experts=experts, graph=graph,
        )
        spec = schemas.EngagementSpec(
            requesting_division="lord_otter",
            product_type="position_context",
            asset_class="equity",
            scope=schemas.PositionContextScope(
                symbol="AAPL", time_horizon_hours=4,
                current_position_qty=100.0, current_position_avg_price=150.0,
                current_position_age_hours=12.0,
            ),
            triggered_by="division_agent",
            triggered_ts=datetime.now(timezone.utc).isoformat(),
        )
        product = await run_engagement(spec, deps=deps)
        if isinstance(product, schemas.PositionContext):
            if any("debate" in f.lower() for f in product.risk_flags):
                _ok("PositionContext risk_flags carries debate entry",
                    product.risk_flags[0][:80])
            else:
                _fail("PositionContext missing debate risk_flag",
                      f"flags={product.risk_flags}")
                failures += 1
        else:
            _fail("PositionContext not emitted")
            failures += 1

        # ── Section 6: TradeConfirmation - debate_audit_row_id ───────
        _section("6. Engagement: TradeConfirmation tags debate_audit_row_id")
        db_url = f"sqlite:///{Path(td).as_posix()}/uat_tc.db"
        init_db(db_url)
        deps, log_tc = _build_deps(
            db_url,
            leans={"technical": "bullish", "macro": "bearish", "sentiment": "bullish"},
        )
        spec = schemas.EngagementSpec(
            requesting_division="lord_otter",
            product_type="trade_confirmation",
            asset_class="equity",
            scope=schemas.TradeConfirmationScope(
                proposed_action={"symbol": "AAPL", "side": "buy",
                                 "size_pct_equity": 0.02, "tier": "standard"},
            ),
            triggered_by="division_agent",
            triggered_ts=datetime.now(timezone.utc).isoformat(),
        )
        product = await run_engagement(spec, deps=deps)
        if isinstance(product, schemas.TradeConfirmation):
            if product.debate_audit_row_id is not None:
                _ok("TradeConfirmation carries debate_audit_row_id",
                    f"id={product.debate_audit_row_id} verdict={product.verdict}")
            else:
                _fail("TradeConfirmation missing debate_audit_row_id")
                failures += 1
        else:
            _fail("TradeConfirmation not emitted")
            failures += 1

        # ── Section 7: CandidateRecommendation - gate skip policy ────
        _section("7. CandidateRecommendation: multi-symbol skips gate")
        db_url = f"sqlite:///{Path(td).as_posix()}/uat_cr.db"
        init_db(db_url)
        from trading_corp.agents.research import graph as graph_mod
        # Stub the universe loader so the candidate path runs offline.
        _orig_loader = graph_mod._load_starter_universe
        graph_mod._load_starter_universe = lambda key: ["AAPL", "MSFT", "NVDA", "GOOGL"]
        try:
            from trading_corp.utils import market_data
            _orig_earnings = market_data.get_next_earnings
            market_data.get_next_earnings = lambda *a, **kw: None
            try:
                deps, log_cr = _build_deps(
                    db_url,
                    leans={"technical": "bullish", "macro": "bearish", "sentiment": "bullish"},
                )
                spec = schemas.EngagementSpec(
                    requesting_division="robinhood_pmcc",
                    product_type="candidate_recommendation",
                    asset_class="equity",
                    scope=schemas.CandidateScope(
                        mandate={"category": "large_cap"},
                        capacity_dollars=10_000.0,
                        n_candidates=2,
                        starter_universe_key="large_mid_cap",
                        current_holdings=[],
                    ),
                    triggered_by="telegram",
                    triggered_ts=datetime.now(timezone.utc).isoformat(),
                )
                rec = await run_engagement(spec, deps=deps)
                kinds_cr = [e["kind"] for e in log_cr.recent_events(limit=80)]
                if "research_debate_invoked" not in kinds_cr:
                    _ok("CandidateRecommendation never fires gate (multi-symbol policy)")
                else:
                    _fail("CandidateRecommendation fired gate (should be skipped)")
                    failures += 1
                if rec is None or (
                    isinstance(rec, schemas.CandidateRecommendation)
                    and rec.debate_audit_row_id is None
                ):
                    _ok("CandidateRecommendation.debate_audit_row_id stays None")
                else:
                    _fail("CandidateRecommendation got a debate_audit_row_id")
                    failures += 1
            finally:
                market_data.get_next_earnings = _orig_earnings
        finally:
            graph_mod._load_starter_universe = _orig_loader

        # ── Section 8: optional real-LLM smoke ───────────────────────
        if os.getenv("ANTHROPIC_API_KEY"):
            _section("8. Real LLM smoke (ANTHROPIC_API_KEY detected)")
            try:
                bull_text, bull_cost = await run_bull(
                    symbol="AAPL",
                    invoked_reason="experts split bullish/bearish on macro vs technical",
                    reports=disagree,
                )
                bear_text, bear_cost = await run_bear(
                    symbol="AAPL",
                    invoked_reason="experts split bullish/bearish on macro vs technical",
                    reports=disagree,
                )
                outcome, judge_cost = await run_judge(
                    engagement_id="uat-real",
                    symbol="AAPL",
                    invoked_reason="experts split",
                    bull_case=bull_text, bear_case=bear_text,
                )
                if bull_cost > 0 and bear_cost > 0 and judge_cost > 0:
                    _ok("real LLM run completed",
                        f"bull=${bull_cost:.4f} bear=${bear_cost:.4f} judge=${judge_cost:.4f}")
                else:
                    _info("real LLM ran but some costs were zero",
                          f"bull=${bull_cost:.4f} bear=${bear_cost:.4f} judge=${judge_cost:.4f}")
                if outcome.synthesis and "(no LLM available" not in outcome.synthesis:
                    _ok("judge produced real synthesis",
                        outcome.synthesis[:80])
                else:
                    _fail("judge fell back to deterministic placeholder")
                    failures += 1
                _info("bull_case sample", bull_text[:80].replace("\n", " / "))
                _info("bear_case sample", bear_text[:80].replace("\n", " / "))
                _info("judge_bull",
                      f"evidence={outcome.judge_bull_score.evidence_quality:.2f} "
                      f"logic={outcome.judge_bull_score.logical_consistency:.2f} "
                      f"falsify={outcome.judge_bull_score.falsifiability:.2f}")
                _info("judge_bear",
                      f"evidence={outcome.judge_bear_score.evidence_quality:.2f} "
                      f"logic={outcome.judge_bear_score.logical_consistency:.2f} "
                      f"falsify={outcome.judge_bear_score.falsifiability:.2f}")
            except Exception as e:
                _fail(f"real LLM run raised: {type(e).__name__}", str(e)[:120])
                failures += 1
        else:
            _section("8. Real LLM smoke (skipped)")
            _info("ANTHROPIC_API_KEY not set",
                  "set it in .env or env to run real bull/bear/judge")

    print()
    if failures == 0:
        print(f"{GREEN}UAT PASSED - Phase 1f ready for deploy{RESET}")
        return 0
    print(f"{RED}UAT FAILED - {failures} check(s) failed{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
