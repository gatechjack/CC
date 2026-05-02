"""End-to-end UAT for Phase 1d (PositionContext + Otter / Cypher consumption).

What this exercises (against a temp DB, no external APIs):

  1. Engagement graph runs with PositionContextScope and emits a
     PositionContext + audit row.
  2. Pre-emptive cache write/read roundtrip (the contract the on-alert
     read depends on).
  3. Multi-division concurrent prime via prime_all_division_position_contexts.
  4. Real Lord Otter on_alert(payload) populates state.last_position_context
     from the cached row.
  5. Dashboard /research view + research.html template render the new
     PositionContext audit-trail section against real audit rows.

Uses fake macro + sentiment experts so the run is offline + deterministic.
The plumbing is what's load-bearing -yfinance reliability is a Phase 1c
concern handled by the experts themselves.

Run from repo root:
    python scripts/uat_phase_1d.py
"""
from __future__ import annotations

import asyncio
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
from trading_corp.agents.research.position_context_cache import (
    cache_key, read_position_context,
)
from trading_corp.agents.research.prime import (
    prime_all_division_position_contexts,
)
from trading_corp.persistence.db import init_db, load_agent_state

# Reuse the deterministic fake experts from the test fixtures.
sys.path.insert(0, str(_REPO_ROOT / "tests"))
from test_research_engagement_e2e import (  # noqa: E402
    FakeMacroExpert, FakeSentimentExpert,
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


async def main() -> int:
    failures = 0

    with tempfile.TemporaryDirectory() as td:
        db_url = f"sqlite:///{Path(td).as_posix()}/uat.db"
        init_db(db_url)
        logger_agent = LoggerAgent(db_url)
        experts = {
            "macro": FakeMacroExpert(confidence=0.65, lean="bullish"),
            "sentiment": FakeSentimentExpert(confidence=0.5, lean="neutral"),
        }
        graph = build_engagement_graph(
            logger_agent, experts=experts, checkpointer=None,
        )
        deps = ResearchFirmDeps(
            logger_agent=logger_agent, experts=experts, graph=graph,
        )

        # --- 1. Single engagement -> PositionContext product ---
        _section("1. Engagement graph emits PositionContext")
        spec = schemas.EngagementSpec(
            requesting_division="lord_otter",
            product_type="position_context",
            asset_class="crypto_spot",
            scope=schemas.PositionContextScope(
                symbol="BTC/USD",
                time_horizon_hours=4,
                current_position_qty=0.0,
                current_position_avg_price=0.0,
                current_position_age_hours=0.0,
            ),
            triggered_by="division_agent",
            triggered_ts=datetime.now(timezone.utc).isoformat(),
        )
        product = await run_engagement(spec, deps=deps)
        if isinstance(product, schemas.PositionContext) and product.symbol == "BTC/USD":
            _ok("PositionContext returned",
                f"conf={product.confidence_score:.2f} flags={len(product.risk_flags)}")
        else:
            _fail("PositionContext returned", f"got {type(product).__name__}")
            failures += 1

        # --- 2. Audit row written ---
        events = logger_agent.recent_events(limit=40)
        kinds = {e["kind"] for e in events}
        if "research_position_context_emitted" in kinds:
            _ok("research_position_context_emitted audit row present")
        else:
            _fail("audit row missing", f"saw kinds={sorted(kinds)}")
            failures += 1

        # --- 3. Multi-division prime ---
        _section("2. prime_all_division_position_contexts (concurrent)")
        await prime_all_division_position_contexts(
            research_firm=deps,
            db_url=db_url,
            divisions=[
                {"slug": "lord_otter", "asset_class": "crypto_spot",
                 "symbols": ["BTC/USD", "ETH/USD"], "horizon_hours": 4},
                {"slug": "market_cypher", "asset_class": "crypto_spot",
                 "symbols": ["BTC/USD"], "horizon_hours": 24},
            ],
        )

        otter_btc = read_position_context("lord_otter", "BTC/USD", 4, db_url=db_url)
        otter_eth = read_position_context("lord_otter", "ETH/USD", 4, db_url=db_url)
        cypher_btc = read_position_context("market_cypher", "BTC/USD", 24, db_url=db_url)
        cypher_btc_at_4h = read_position_context("market_cypher", "BTC/USD", 4, db_url=db_url)

        if otter_btc is not None and otter_btc.symbol == "BTC/USD":
            _ok("otter / BTC/USD / 4h cached")
        else:
            _fail("otter BTC/USD missing")
            failures += 1
        if otter_eth is not None and otter_eth.symbol == "ETH/USD":
            _ok("otter / ETH/USD / 4h cached")
        else:
            _fail("otter ETH/USD missing")
            failures += 1
        if cypher_btc is not None and cypher_btc.symbol == "BTC/USD":
            _ok("cypher / BTC/USD / 24h cached")
        else:
            _fail("cypher BTC/USD 24h missing")
            failures += 1
        if cypher_btc_at_4h is None:
            _ok("cypher / BTC/USD / 4h CORRECTLY absent",
                "(horizon is part of the key - Otter's 4h must not bleed into Cypher)")
        else:
            _fail("cypher 4h leaked", "horizon-keying is broken")
            failures += 1

        # --- 4. Otter on_alert populates state.last_position_context ---
        _section("3. Lord Otter on_alert -> state.last_position_context")
        from trading_corp.agents.divisions.lord_otter import LordOtterAgent

        otter = LordOtterAgent(db_url=db_url)
        # Direct unit-level read -the simplest path that demonstrates the cache
        # contract from the agent's perspective.
        pc = otter._fetch_position_context("BTC/USD")
        if pc is not None and pc.symbol == "BTC/USD":
            _ok("_fetch_position_context returned cached row",
                f"horizon={pc.time_horizon_hours}h conf={pc.confidence_score:.2f}")
        else:
            _fail("_fetch_position_context returned None")
            failures += 1

        # End-to-end: real on_alert call, verify state.last_position_context
        # gets populated post-_refresh_state_from_signal.
        payload = {
            "signal": "bias_bull",
            "symbol": "BTC/USD",
            "price": "65000.00",
            "time": datetime.now(timezone.utc).isoformat(),
            "interval": "3",
        }
        otter.on_alert(payload, account_equity=100_000.0, held_qty={})
        state = otter.get_state("BTC/USD")
        if state.last_position_context is not None:
            _ok("on_alert populated SymbolState.last_position_context",
                f"symbol={state.last_position_context.symbol}")
        else:
            _fail("SymbolState.last_position_context still None after on_alert")
            failures += 1

        # --- 5. Miss path returns None (fail-soft contract) ---
        _section("4. Cache miss returns None (fail-soft)")
        miss = otter._fetch_position_context("DOGE/USD")  # never primed
        if miss is None:
            _ok("uncached symbol returns None as expected")
        else:
            _fail("expected None for uncached symbol", f"got {miss!r}")
            failures += 1

        # --- 6. Dashboard view + template render ---
        _section("5. Dashboard /research view + template render")
        from trading_corp.web import routes as web_routes
        view = web_routes._build_research_view(deps)  # noqa: SLF001
        position_contexts = view.get("position_contexts") or []

        if position_contexts:
            _ok(f"view has {len(position_contexts)} PositionContext row(s)")
        else:
            _fail("view has no position_contexts rows")
            failures += 1

        # Render the template. Stub `request` so base.html nav highlight works.
        from jinja2 import Environment, FileSystemLoader

        class _DummyURL:
            path = "/research"

        class _DummyReq:
            url = _DummyURL()

        env = Environment(
            loader=FileSystemLoader(str(_REPO_ROOT / "trading_corp" / "web" / "templates"))
        )
        tpl = env.get_template("research.html")
        rendered = tpl.render(
            view=view,
            request=_DummyReq(),
            mode="paper",
            live_brokers=[],
        )

        if "PositionContext audit trail" in rendered:
            _ok("template renders PositionContext section")
        else:
            _fail("template missing PositionContext section header")
            failures += 1
        if "BTC/USD" in rendered:
            _ok("rendered HTML contains BTC/USD row")
        else:
            _fail("rendered HTML missing BTC/USD")
            failures += 1
        if "lord_otter" in rendered:
            _ok("rendered HTML attributes row to lord_otter division")
        else:
            _fail("rendered HTML missing lord_otter attribution")
            failures += 1

    # --- Summary ---
    print()
    if failures == 0:
        print(f"{GREEN}UAT PASSED -Phase 1d ready for review{RESET}")
        return 0
    print(f"{RED}UAT FAILED -{failures} check(s) failed{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
