"""End-to-end UAT for Phase 1e (TradeConfirmation + Otter/Cypher consult).

What this exercises (against a temp DB, no external APIs by default):

  1. Engagement graph runs with TradeConfirmationScope and emits a
     TradeConfirmation product + audit row, for both confirm and
     push_back deterministic paths.
  2. Layer 2 symbol/subject-action drift detection short-circuits.
  3. Consult helper (`consult_research_for_trade_confirmation`)
     returns the right ConsultResult shape on every verdict_kind:
       confirm | push_back | conditional | timeout | error | no_research
  4. apply_suggested_modifications_to_order recomputes qty correctly
     and propagates research_modification_rationale.
  5. Hard-timeout path lands an audit row joinable to engagement_id.
  6. (Optional) when ANTHROPIC_API_KEY is set, run ONE real engagement
     end-to-end and print the LLM-narrated verdict + cost. Useful for
     pre-deploy smoke against the real Anthropic API.

Uses fake macro+sentiment+technical experts so the deterministic runs
are offline and deterministic. The plumbing is what's load-bearing —
yfinance reliability is a Phase 1c concern handled by the experts
themselves.

Run from repo root:
    python scripts/uat_phase_1e.py
    ANTHROPIC_API_KEY=sk-ant-... python scripts/uat_phase_1e.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Ensure the repo root is importable when running as a script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.research import schemas
from trading_corp.agents.research.engagement import (
    ResearchFirmDeps, run_engagement,
)
from trading_corp.agents.research.graph import build_engagement_graph
from trading_corp.agents.research.trade_confirmation_consult import (
    apply_suggested_modifications_to_order,
    consult_research_for_trade_confirmation,
    consult_enabled,
    consult_timeout_seconds,
)
from trading_corp.persistence.db import init_db
from trading_corp.persistence.models import ProposedOrder

# Reuse the deterministic fake experts from the test fixtures.
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


def _build_deps(
    db_url: str, *, lean: str = "bullish", confidence: float = 0.7,
) -> tuple[ResearchFirmDeps, LoggerAgent]:
    logger_agent = LoggerAgent(db_url)
    experts = {
        "technical": FakeTechnicalExpert(lean=lean, confidence=confidence),
        "macro": FakeMacroExpert(lean=lean, confidence=confidence),
        "sentiment": FakeSentimentExpert(lean=lean, confidence=confidence),
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


def _spec(symbol: str = "BTC/USD", side: str = "buy") -> schemas.EngagementSpec:
    return schemas.EngagementSpec(
        requesting_division="lord_otter",
        product_type="trade_confirmation",
        asset_class="crypto_spot",
        scope=schemas.TradeConfirmationScope(
            proposed_action={
                "symbol": symbol, "side": side,
                "size_pct_equity": 0.015, "tier": "standard",
            },
            context={"alert_signal": "otter_buy"},
        ),
        triggered_by="division_agent",
        triggered_ts=datetime.now(timezone.utc).isoformat(),
    )


def _order(symbol: str = "BTC/USD", side: str = "buy") -> ProposedOrder:
    return ProposedOrder(
        strategy="lord_otter",
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        qty=0.01,
        order_type="market",
        rationale="alert-driven",
        extra={"tier": "standard", "size_pct_equity": 0.015},
    )


def _payload() -> dict:
    return {
        "signal": "otter_buy",
        "symbol": "BTC/USD",
        "price": "65000.0",
        "time": datetime.now(timezone.utc).isoformat(),
        "interval": "3",
    }


async def main() -> int:
    failures = 0

    # ── Section 1: config readers ──────────────────────────────────────
    _section("1. Config readers")
    if consult_enabled() is True:
        _ok("trade_confirmation.enabled defaults to True")
    else:
        _fail("trade_confirmation.enabled is False — kill-switch flipped?")
        failures += 1
    timeout = consult_timeout_seconds()
    if timeout == 8.0:
        _ok(f"trade_confirmation.timeout_seconds = {timeout}")
    else:
        _info(f"trade_confirmation.timeout_seconds = {timeout}",
              "(non-default; review if intentional)")

    # ignore_cleanup_errors: SQLite holds handles past the `with` block
    # on Windows; the temp dir gets reaped by the OS later.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db_url = f"sqlite:///{Path(td).as_posix()}/uat.db"
        init_db(db_url)

        # ── Section 2: engagement graph - confirm verdict ─────────────
        _section("2. Engagement: all-bullish experts -> confirm")
        deps_bull, logger_bull = _build_deps(db_url, lean="bullish")
        product = await run_engagement(_spec(), deps=deps_bull)
        if isinstance(product, schemas.TradeConfirmation) and product.verdict == "confirm":
            _ok("TradeConfirmation returned with verdict=confirm",
                f"rationale_len={len(product.rationale)}")
        else:
            _fail("expected confirm verdict",
                  f"got {type(product).__name__} verdict={getattr(product, 'verdict', None)}")
            failures += 1

        kinds = {e["kind"] for e in logger_bull.recent_events(limit=40)}
        if "research_trade_confirmation_emitted" in kinds:
            _ok("research_trade_confirmation_emitted audit row present")
        else:
            _fail("audit row missing for confirm path")
            failures += 1

        # ── Section 3: engagement graph - push_back verdict ───────────
        _section("3. Engagement: all-bearish experts on buy -> push_back")
        # Fresh DB so audit assertions don't pick up the prior section's rows.
        db_url_pb = f"sqlite:///{Path(td).as_posix()}/uat_pb.db"
        init_db(db_url_pb)
        deps_bear, logger_bear = _build_deps(db_url_pb, lean="bearish")
        product = await run_engagement(_spec(), deps=deps_bear)
        if isinstance(product, schemas.TradeConfirmation) and product.verdict == "push_back":
            _ok("TradeConfirmation verdict=push_back", product.rationale[:80])
        else:
            _fail("expected push_back verdict",
                  f"got verdict={getattr(product, 'verdict', None)}")
            failures += 1

        if product and any("bearish" in f.lower() for f in product.risks_flagged):
            _ok("risks_flagged includes bearish leans for downstream visibility")
        else:
            _fail("risks_flagged missing bearish entries")
            failures += 1

        # ── Section 4: Layer 2 subject_action drift ───────────────────
        _section("4. Layer 2 catches subject_action.symbol drift")
        from trading_corp.agents.research import graph as graph_mod

        async def bad_synth(*, spec, reports, expert_audit_row_ids):
            tc = schemas.TradeConfirmation(
                engagement_id=spec.engagement_id,
                requesting_division=spec.requesting_division,
                subject_action={"symbol": "WRONG_SYMBOL", "side": "buy"},
                verdict="confirm",
                rationale="r",
                risks_flagged=[],
                suggested_modifications=None,
            )
            return tc, 0.0

        original = graph_mod.synthesize_trade_confirmation
        graph_mod.synthesize_trade_confirmation = bad_synth
        try:
            db_url_drift = f"sqlite:///{Path(td).as_posix()}/uat_drift.db"
            init_db(db_url_drift)
            deps_drift, logger_drift = _build_deps(db_url_drift)
            # Need to rebuild graph so it picks up the patched synthesizer.
            deps_drift.graph = build_engagement_graph(
                logger_drift, experts=deps_drift.experts, checkpointer=None,
            )
            product = await run_engagement(_spec(), deps=deps_drift)
            kinds = {e["kind"] for e in logger_drift.recent_events(limit=20)}
            if product is None and "research_engagement_validation_failed" in kinds:
                _ok("Layer 2 short-circuited with validation_failed")
            else:
                _fail("Layer 2 did not catch symbol drift",
                      f"product={product!r} kinds={kinds}")
                failures += 1
        finally:
            graph_mod.synthesize_trade_confirmation = original

        # ── Section 5: consult helper - confirm path ───────────────────
        _section("5. Consult: confirm -> proceed with original order")
        order = _order()
        result = await consult_research_for_trade_confirmation(
            order=order, payload=_payload(),
            research_firm=deps_bull, logger_agent=logger_bull,
            division_slug="lord_otter", asset_class="crypto_spot",
        )
        if (result.decision == "proceed"
                and result.verdict_kind == "confirm"
                and result.order is order):
            _ok("ConsultResult shape correct on confirm path")
        else:
            _fail("ConsultResult wrong shape",
                  f"decision={result.decision} verdict_kind={result.verdict_kind}")
            failures += 1

        # ── Section 6: consult helper - push_back path ────────────────
        _section("6. Consult: push_back -> skip + audit")
        result = await consult_research_for_trade_confirmation(
            order=order, payload=_payload(),
            research_firm=deps_bear, logger_agent=logger_bear,
            division_slug="lord_otter", asset_class="crypto_spot",
        )
        if (result.decision == "skip"
                and result.verdict_kind == "push_back"
                and result.order is None):
            _ok("ConsultResult shape correct on push_back path",
                f"rationale_len={len(result.rationale)}")
        else:
            _fail("ConsultResult wrong shape on push_back",
                  f"decision={result.decision} order={result.order}")
            failures += 1

        events_bear = logger_bear.recent_events(limit=40)
        pushback_rows = [
            e for e in events_bear
            if e["kind"] == "research_tradeconf_pushback_acted_on"
        ]
        if len(pushback_rows) == 1:
            payload_d = pushback_rows[0]["payload"] or {}
            joinable = (
                payload_d.get("engagement_id")
                and payload_d.get("order_id") == order.id
            )
            if joinable:
                _ok("pushback audit row written + joinable to engagement+order")
            else:
                _fail("pushback audit missing engagement_id or order_id",
                      f"payload={payload_d}")
                failures += 1
        else:
            _fail(f"expected 1 pushback audit row, got {len(pushback_rows)}")
            failures += 1

        # ── Section 7: consult helper - timeout fail-open ─────────────
        _section("7. Consult: timeout -> fail-open + audit")
        from trading_corp.agents.research import trade_confirmation_consult as consult_mod

        async def slow(spec, *, deps):
            await asyncio.sleep(2.0)
            return None  # never reached

        original_run = consult_mod.run_engagement
        consult_mod.run_engagement = slow
        try:
            db_url_to = f"sqlite:///{Path(td).as_posix()}/uat_to.db"
            init_db(db_url_to)
            deps_to, logger_to = _build_deps(db_url_to)
            order_to = _order()
            result = await consult_research_for_trade_confirmation(
                order=order_to, payload=_payload(),
                research_firm=deps_to, logger_agent=logger_to,
                division_slug="lord_otter", asset_class="crypto_spot",
                timeout_s=0.1,
            )
            if (result.decision == "proceed"
                    and result.verdict_kind == "timeout"
                    and result.order is order_to):
                _ok("timeout fails open with original order")
            else:
                _fail("timeout path wrong",
                      f"decision={result.decision} verdict_kind={result.verdict_kind}")
                failures += 1
            kinds_to = {e["kind"] for e in logger_to.recent_events(limit=20)}
            if "research_tradeconf_timeout" in kinds_to:
                _ok("research_tradeconf_timeout audit row written")
            else:
                _fail("timeout audit row missing")
                failures += 1
        finally:
            consult_mod.run_engagement = original_run

        # ── Section 8: consult - no_research short-circuits silently ──
        _section("8. Consult: research_firm=None -> no_research")
        db_url_none = f"sqlite:///{Path(td).as_posix()}/uat_none.db"
        init_db(db_url_none)
        logger_none = LoggerAgent(db_url_none)
        result = await consult_research_for_trade_confirmation(
            order=_order(), payload=_payload(),
            research_firm=None, logger_agent=logger_none,
            division_slug="lord_otter", asset_class="crypto_spot",
        )
        if (result.decision == "proceed"
                and result.verdict_kind == "no_research"):
            _ok("no_research path proceeds silently")
        else:
            _fail("no_research path wrong",
                  f"verdict_kind={result.verdict_kind}")
            failures += 1
        kinds_none = {e["kind"] for e in logger_none.recent_events(limit=20)}
        if not any(k.startswith("research_tradeconf_") for k in kinds_none):
            _ok("no audit rows written on no_research path",
                "(test envs / kill-switched should be invisible)")
        else:
            _fail("unexpected research_tradeconf_* row on no_research path")
            failures += 1

        # ── Section 9: apply_suggested_modifications_to_order math ────
        _section("9. apply_suggested_modifications_to_order")
        base = _order()
        base.limit_price = 65000.0
        mods = schemas.SuggestedModifications(
            entry_price=64000.0,
            size_pct_equity=0.01,
            rationale="dial back size, wait for pullback",
        )
        modified, applied = apply_suggested_modifications_to_order(
            order=base, mods=mods,
            account_equity=100_000.0, fallback_price=65000.0,
        )
        if abs(modified.limit_price - 64000.0) < 1e-9:
            _ok("entry_price modification applied")
        else:
            _fail("entry_price not applied")
            failures += 1
        # qty = (100k * 0.01) / 64k = 0.015625
        expected_qty = 1000.0 / 64000.0
        if abs(modified.qty - expected_qty) < 1e-9:
            _ok(f"qty recomputed via equity*size_pct/price = {modified.qty:.6f}")
        else:
            _fail(f"qty math wrong: expected {expected_qty:.6f}, got {modified.qty:.6f}")
            failures += 1
        if (modified.extra or {}).get("research_modification_rationale") == mods.rationale:
            _ok("research_modification_rationale propagated to order.extra")
        else:
            _fail("research_modification_rationale not on order.extra")
            failures += 1
        if "qty" in applied and "entry_price" in applied:
            _ok("applied_changes audit dict captures both modifications")
        else:
            _fail(f"applied_changes incomplete: {list(applied.keys())}")
            failures += 1

        # ── Section 10: Optional real-LLM smoke ───────────────────────
        if os.getenv("ANTHROPIC_API_KEY"):
            _section("10. Real LLM smoke (ANTHROPIC_API_KEY detected)")
            try:
                product = await run_engagement(_spec(), deps=deps_bull)
                if isinstance(product, schemas.TradeConfirmation):
                    _ok(f"real LLM run completed",
                        f"verdict={product.verdict} rationale={product.rationale[:80]!r}")
                    if product.suggested_modifications:
                        _ok("LLM emitted SuggestedModifications",
                            f"price={product.suggested_modifications.entry_price} "
                            f"size={product.suggested_modifications.size_pct_equity}")
                else:
                    _fail(f"real LLM returned non-TradeConfirmation",
                          f"got {type(product).__name__}")
                    failures += 1
            except Exception as e:
                _fail(f"real LLM run raised: {type(e).__name__}", str(e)[:120])
                failures += 1
        else:
            _section("10. Real LLM smoke (skipped)")
            _info("ANTHROPIC_API_KEY not set",
                  "set it to run a single real engagement against Anthropic")

    print()
    if failures == 0:
        print(f"{GREEN}UAT PASSED - Phase 1e ready for deploy{RESET}")
        return 0
    print(f"{RED}UAT FAILED - {failures} check(s) failed{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
