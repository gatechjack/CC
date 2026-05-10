"""Entrypoint for the AI-Powered Trading Corporation.

Usage:
  python -m trading_corp                # PAPER mode (default)
  python -m trading_corp --live         # LIVE mode (requires confirmation)
  python -m trading_corp --demo         # paper + emit a synthetic test order
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

import yaml

_PID_FILE = Path("data/trading_corp.pid")


def _acquire_lock() -> bool:
    """Atomically claim a PID file. Return False if another live instance owns it.

    Uses O_CREAT|O_EXCL so two processes starting in the same millisecond
    can't both claim the lock (which is what happened on 2026-04-27 when
    two `python -m trading_corp` instances launched simultaneously from
    different Python installations and both started polling Telegram).

    If the file exists, checks whether the recorded PID is still alive.
    Stale PID files (process gone) are reaped and the caller retries the
    atomic claim exactly once.
    """
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)

    def _atomic_claim() -> bool:
        """Try to create the PID file with O_EXCL. Returns True on success."""
        try:
            fd = os.open(str(_PID_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
        finally:
            os.close(fd)
        return True

    if _atomic_claim():
        return True

    # File already exists — check whether the recorded PID is alive.
    try:
        old_pid = int(_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        old_pid = -1

    if old_pid == os.getpid():
        return True   # re-entrant call — we already own it

    if old_pid > 0:
        try:
            os.kill(old_pid, 0)   # signal 0 = existence check
            return False          # other process is alive — refuse
        except OSError:
            pass                  # stale PID file — fall through to reap

    # Reap stale file and retry the atomic claim exactly once. If it still
    # fails, another process beat us in the race; refuse rather than risk
    # claiming a lock another live instance just took.
    try:
        _PID_FILE.unlink()
    except OSError:
        return False
    return _atomic_claim()


def _release_lock() -> None:
    try:
        if _PID_FILE.exists() and int(_PID_FILE.read_text().strip()) == os.getpid():
            _PID_FILE.unlink()
    except Exception:
        pass

from trading_corp.agents.backtester import BacktesterAgent
from trading_corp.agents.ceo import CEOAgent
from trading_corp.agents.data_exec import DataExecAgent
from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent
from trading_corp.agents.divisions.fidelity_options import FidelityOptionsAgent
from trading_corp.brokers.fidelity import FidelityBroker
from trading_corp.agents.logger import LoggerAgent
from trading_corp.agents.portfolio import PortfolioAgent
from trading_corp.agents.risk import RiskAgent
from trading_corp.agents.trend_regime import TrendAgent
from trading_corp.brokers.paper import PaperBroker, PaperExecutionBroker
from trading_corp.brokers.robinhood import RobinhoodBroker
from trading_corp.comms.cli import CLIChannel
from trading_corp.comms.pending_registry import PendingApprovalRegistry
from trading_corp.comms.telegram_bot import TelegramChannel
from trading_corp.graph.ceo_graph import build_trade_graph
from trading_corp.persistence import db
from trading_corp.persistence.checkpointer import make_checkpointer
from trading_corp.persistence.models import ProposedOrder
from trading_corp.utils.secrets import (
    RedactingFilter, Secrets, assert_live_ready, load_secrets,
)

DISCLAIMER = """\
================================================================================
  AI-Powered Trading Corporation — Phase 3
  DISCLAIMER: Trading involves SUBSTANTIAL risk of loss. This software is
  experimental. The Board (you) accept all responsibility for any actions
  taken in LIVE mode. The system DEFAULTS to PAPER mode on every startup.
================================================================================
"""


log = logging.getLogger(__name__)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)
    # Tame noisy libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="trading_corp")
    p.add_argument("--live", action="store_true", help="Enable LIVE trading (requires confirmation).")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="Validate the full LIVE pipeline (auth, snapshots, risk, order "
                        "construction) but skip the actual broker.place_order() call. "
                        "Synthetic fills are logged so the dashboard renders end-to-end. "
                        "Only meaningful with --live.")
    p.add_argument("--demo", action="store_true", help="Emit a synthetic ProposedOrder after startup (paper only).")
    p.add_argument("--brokers", nargs="*", default=[],
                   help="Live brokers to require: any of robinhood coinbase fidelity. "
                        "Phase 2 paper mode ignores this.")
    return p.parse_args(argv)


def confirm_live(input_fn=input) -> bool:
    sys.stdout.write(
        "\nLIVE mode requested. This will route real orders to live brokers.\n"
        "Type the word LIVE (uppercase) to proceed, anything else to cancel:\n> "
    )
    sys.stdout.flush()
    try:
        return (input_fn() or "").strip() == "LIVE"
    except EOFError:
        return False


async def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sys.stdout.write(DISCLAIMER)
    sys.stdout.flush()

    secrets = load_secrets()
    mode = "PAPER"
    dry_run = bool(args.dry_run)
    if args.live:
        if not confirm_live():
            sys.stdout.write("LIVE mode NOT confirmed. Exiting (no orders placed).\n")
            return 2
        try:
            assert_live_ready(secrets, tuple(args.brokers))
        except RuntimeError as e:
            sys.stdout.write(f"LIVE preflight failed: {e}\n")
            return 3
        mode = "LIVE"
        if dry_run:
            sys.stdout.write(
                "\n*** DRY-RUN ENABLED ***\n"
                "Real broker auth + reads + risk gates will run, but every\n"
                "order will be SKIPPED before broker.place_order(). Synthetic\n"
                "fills will be logged so the UI renders end-to-end.\n"
                "Approve trades freely — nothing routes to the live broker.\n\n"
            )
            sys.stdout.flush()
    elif dry_run:
        sys.stdout.write(
            "Note: --dry-run has no effect without --live. PAPER mode already "
            "uses simulated execution.\n"
        )
        dry_run = False

    # --- DB + agents ---
    db_path = db.init_db(secrets.db_url)
    logger_agent = LoggerAgent(secrets.db_url)
    logger_agent.log_event(
        "system", "startup",
        {"mode": mode, "live_brokers": list(args.brokers), "dry_run": dry_run},
    )

    risk_agent = RiskAgent(narrator_enabled=bool(secrets.anthropic_api_key))
    trend_agent = TrendAgent()
    backtester = BacktesterAgent()
    data_exec = DataExecAgent(logger_agent, dry_run=dry_run)
    portfolio = PortfolioAgent(data_exec)
    ceo = CEOAgent()

    # Phase B.1 of HITL-in-app — process-wide registry of pending
    # approvals. TelegramChannel registers its message-send as a
    # notifier on .start(); the web /approvals routes read + resolve
    # via the same registry. ONE instance per process; tests
    # construct their own per case.
    pending_registry = PendingApprovalRegistry(logger_agent=logger_agent)
    # PMCC: pass db_url for Phase 2 Telegram approval enrichment — agent
    # queries proposed_order table for prior-roll history when building
    # position_context on roll/sell-weekly proposals. No-op when None.
    pmcc_agent = PMCCAgent(db_url=secrets.db_url)
    fidelity_agent = FidelityOptionsAgent()
    # Lord Otter — TradingView-driven scalping. The agent is always
    # instantiated (cheap construction, hot-reloadable config); the
    # webhook endpoint will refuse traffic until `lord_otter.enabled`
    # is true in strategies.yaml AND a valid LORD_OTTER_WEBHOOK_SECRET
    # is set in the environment.
    from trading_corp.agents.strategies.lord_otter import LordOtterAgent
    # Pass the DB URL so the agent can persist + restore its bias latch
    # across restarts. Without this, every restart wipes the bias state
    # and the strategy goes mute until the next regime-change cross
    # arrives via TradingView (potentially hours/days). 12h staleness
    # gate inside the agent prevents stale bias from contaminating
    # post-restart behavior.
    lord_otter_agent = LordOtterAgent(db_url=secrets.db_url)

    # Market Cypher — second TV-driven agent (swing-style, runs alongside
    # Otter). Same persistence pattern as Otter; bias + sommi state both
    # survive restarts via the agent_state table. 3-day staleness gate
    # since Cypher's bias is set on 1D events.
    from trading_corp.agents.strategies.market_cypher import MarketCypherAgent
    market_cypher_agent = MarketCypherAgent(db_url=secrets.db_url)

    # Coinbase BTC Donchian — poll-driven 6h bar-close strategy on
    # coinbase_spot. Unlike Otter/Cypher this is NOT TradingView-webhook
    # driven; the orchestrator's _scheduled_donchian_loop (below) wakes at
    # 00/06/12/18 UTC, fetches recent OHLCV, and calls on_bar_close. The
    # agent persists CASH↔BTC state + cost_basis to the agent_state table
    # so a restart mid-position resumes correctly.
    from trading_corp.agents.strategies.coinbase_btc_donchian_agent import (
        CoinbaseBTCDonchianAgent,
    )
    donchian_agent = CoinbaseBTCDonchianAgent(db_url=secrets.db_url)

    # --- Brokers (one per division, driven by config/divisions.yaml) ---
    from trading_corp.brokers.coinbase import CoinbaseBroker
    from trading_corp.utils.divisions import load_divisions
    divisions = load_divisions()

    # 'default' division always uses PaperBroker (demo / fallback fills).
    paper_broker = PaperBroker(account="paper-default", starting_equity=100_000.0)
    data_exec.register_broker("default", paper_broker)

    # Each division gets its own broker handle. Brokers within a family
    # (Robinhood, Fidelity) share the underlying login session — see the
    # broker modules for refcount-based session sharing.
    for d in divisions:
        if not d.enabled:
            continue
        broker = _build_broker_for_division(d, secrets, mode, args.brokers)
        if broker is None:
            continue
        data_exec.register_broker(d.slug, broker)

    # connect_all() bootstraps each broker. Robinhood and Fidelity log in
    # exactly once across all instances of the same family thanks to module-
    # level session sharing in their respective broker modules.
    await data_exec.connect_all()

    # Bring-up reconcile vs the live coinbase_spot snapshot. As of
    # 2026-05-09, this is a NO-OP whenever a persisted state row exists
    # (the agent's `restore_from_broker` short-circuits) — the strategy's
    # view is the source of truth from the first persist onward, and
    # Board-driven broker deltas are observed (logged as `balance_change`
    # audit rows) rather than absorbed (state-flipped). The reconcile
    # only fires on a fresh install or after a stale-state purge.
    cb_spot_broker = data_exec.brokers.get("coinbase_spot")
    if cb_spot_broker is not None:
        try:
            snap = await cb_spot_broker.snapshot()
            held_btc = 0.0
            for pos in (snap.positions or []):
                if pos.symbol == donchian_agent.symbol:
                    held_btc = float(pos.qty or 0.0)
                    break
            cash = float(getattr(snap, "cash", 0.0) or 0.0)
            current_price = 0.0
            try:
                current_price = float(
                    await cb_spot_broker.quote(donchian_agent.symbol) or 0.0
                )
            except Exception as e:
                log.warning("Donchian startup quote failed: %s", e)
            if current_price > 0:
                donchian_agent.restore_from_broker(
                    account_equity=float(snap.equity or 0.0),
                    held_btc=held_btc,
                    current_price=current_price,
                    cash=cash,
                )
            else:
                log.warning(
                    "Donchian startup: no price quote available; skipping "
                    "broker reconciliation (will retry on first bar close)"
                )
        except Exception as e:
            log.exception("Donchian startup reconciliation failed: %s", e)

    # --- Comms channel ---
    # _graph_holder is filled after the checkpointer context opens so the scan
    # callback can reference the graph without a circular dependency.
    _graph_holder: list[Any] = [None]
    # Same pattern: research_firm is built inside the checkpointer context
    # but the Telegram callbacks need it at construction time. Holder list
    # gets filled once research_firm is ready.
    _research_holder: list[Any] = [None]
    channel: Any  # set below; the scan callbacks reference it via closure

    # ── Shared scan callbacks (used by Telegram, CLI, and the scheduler) ──
    async def _on_scan() -> str:
        if _graph_holder[0] is None:
            return "System still initializing — try again in a moment."
        # PMCC scans run against the Robinhood PMCC division (the primary
        # aggressive RH account). Other RH divisions (IRA, Joint) follow
        # different strategies and aren't part of this scan command.
        scan_broker = data_exec.brokers.get("robinhood_pmcc") or paper_broker

        # Grab current regime for LLM context
        try:
            reading = trend_agent.read()
            scan_regime = reading.regime
        except Exception:
            scan_regime = "unknown"

        # Push expert portfolio analysis BEFORE routing orders so the
        # Board sees the full picture alongside each approval request.
        try:
            analysis_md = await pmcc_agent.analyze_portfolio(
                scan_broker, regime=scan_regime,
            )
            await channel.push(analysis_md)
        except Exception as e:
            log.warning("PMCC portfolio analysis failed: %s", e)

        orders = await pmcc_agent.scan(scan_broker, regime=scan_regime)
        if not orders:
            return "PMCC scan complete: no actions needed this cycle."
        await channel.push(
            f"PMCC scan: *{len(orders)}* order(s) proposed. Routing for approval..."
        )
        # Phase B.3 — group orders by pmcc_pair_id so paired rolls
        # (close + open sharing the same pair_id) launch in parallel.
        # Both ApprovalRequests land in the registry simultaneously,
        # which lets the web /approvals/{order_id} detail page
        # coalesce siblings into ONE card with Net Debit/Credit and
        # ONE Approve button (eliminating the "approve close, reject
        # open → naked short" failure mode). Solo orders (no pair_id)
        # remain sequential — same blast-radius bound as pre-B.3.
        groups = _group_orders_by_pair_id(orders)
        for group in groups:
            if len(group) == 1:
                order = group[0]
                status = await _run_order(
                    _graph_holder[0], channel, logger_agent, order,
                    division="robinhood_pmcc",
                )
                logger_agent.log_event(
                    "pmcc", "scan_order_result",
                    {"order_id": order.id, "symbol": order.symbol, "status": status},
                )
            else:
                statuses = await asyncio.gather(*(
                    _run_order(
                        _graph_holder[0], channel, logger_agent, o,
                        division="robinhood_pmcc",
                    )
                    for o in group
                ))
                for o, s in zip(group, statuses):
                    logger_agent.log_event(
                        "pmcc", "scan_order_result",
                        {"order_id": o.id, "symbol": o.symbol, "status": s},
                    )
        return f"PMCC scan complete: {len(orders)} order(s) processed."

    if secrets.has_telegram:
        async def _on_message(text: str) -> str:
            ctx_md = await _build_context_md(trend_agent, portfolio, logger_agent)
            return await ceo.reply_to_board(text, ctx_md)

        async def _on_brief() -> str:
            return await _make_morning_brief(trend_agent, portfolio, ceo, logger_agent)

        async def _on_fidelity_scan() -> str:
            if _graph_holder[0] is None:
                return "System still initializing — try again in a moment."
            # Fidelity options scans run against the Joint (aggressive)
            # division — the only Fidelity account where we run options.
            fid_scan_broker = data_exec.brokers.get("fidelity_joint") or paper_broker
            try:
                reading = trend_agent.read()
                regime = reading.regime
            except Exception:
                regime = "neutral"
            orders = await fidelity_agent.scan(fid_scan_broker, regime=regime)
            if not orders:
                return "Fidelity scan complete: no actions needed this cycle."
            await channel.push(
                f"Fidelity scan: *{len(orders)}* order(s) proposed. Routing for approval..."
            )
            for order in orders:
                status = await _run_order(
                    _graph_holder[0], channel, logger_agent, order, division="fidelity_joint"
                )
                logger_agent.log_event(
                    "fidelity", "scan_order_result",
                    {"order_id": order.id, "symbol": order.symbol, "status": status},
                )
            return f"Fidelity scan complete: {len(orders)} order(s) processed."

        # Wire the rich Telegram command set (/help, /equity, /pairs, /pair SYM,
        # /positions, /vix, /regime, /pending, /mode, /halts, plus inline
        # keyboard drill-down + Approve/Defer buttons).
        from trading_corp.comms.telegram_commands import TelegramCommands
        from trading_corp.web.app import WebDeps
        tg_deps = WebDeps(
            db_url=secrets.db_url,
            db_path=str(db_path),
            mode=mode,
            logger_agent=logger_agent,
            data_exec=data_exec,
            trend_agent=trend_agent,
            portfolio=portfolio,
            pmcc_agent=pmcc_agent,
            fidelity_agent=fidelity_agent,
            paper_broker=paper_broker,
            secrets=secrets,
            risk_agent=risk_agent,
            pending_registry=pending_registry,
        )
        tg_commands = TelegramCommands(tg_deps)

        async def _on_research(args: list[str]) -> str:
            """Top-level `/research <subcommand> [args]` handler.

            Subcommands:
              - `candidate <division> <n>` (Phase 1a-1) — emit a
                CandidateRecommendation for the division.
              - `thesis <symbol>` (Phase 1b) — wired into the engagement
                runner + dashboard, but the Telegram surface is
                intentionally not wired in this phase. Use the dashboard
                Thesis library to view emitted Theses; trigger via the
                research engagement runner programmatically (see
                `agents/research/engagement.py:run_engagement`).

            Returns a markdown body. v3 has no recommendation-as-a-unit
            approval flow — divisions decide per candidate.
            """
            from datetime import datetime, timezone
            from trading_corp.agents.research.engagement import run_engagement
            from trading_corp.agents.research.schemas import (
                CandidateScope, EngagementSpec,
            )

            if _research_holder[0] is None:
                return "Research firm still initializing — try again in a moment."

            sub = args[0].lower() if args else ""
            if sub == "thesis":
                # Phase 1b deferred Telegram surface. The engagement runs
                # via `run_engagement` regardless of trigger; emitted
                # Theses appear in the dashboard's Thesis library.
                return (
                    "`/research thesis` is not wired into Telegram in this "
                    "phase. Emitted Theses appear in the dashboard Thesis "
                    "library. Trigger an engagement programmatically via "
                    "`run_engagement` if needed."
                )
            if sub != "candidate":
                return (
                    f"Unknown research subcommand: {sub!r}. "
                    f"Available: `candidate <division> <n>`."
                )

            # `/research candidate <division> <n>` — n optional, default 3
            division = args[1] if len(args) > 1 else "robinhood_pmcc"
            try:
                n = int(args[2]) if len(args) > 2 else 3
            except ValueError:
                return f"Invalid n: {args[2]!r}"
            # Map shorthand "pmcc" → "robinhood_pmcc"
            if division == "pmcc":
                division = "robinhood_pmcc"

            # Pull the division's strategy block as the verbatim mandate
            # (Q4 — research firm doesn't interpret division config).
            # Also pull current holdings from the same strategies.yaml
            # block to seed current_holdings.
            from trading_corp.agents.research.graph import (
                _strategies_universe_for_key,
            )
            try:
                with open("config/strategies.yaml", "r", encoding="utf-8") as f:
                    strats = yaml.safe_load(f) or {}
            except Exception as e:
                return f"strategies.yaml load failed: {e}"
            div_block = (strats.get(division) or {}).get("strategy") or {}
            mandate = div_block.get("underlying_criteria", {}) or div_block
            current_holdings = _strategies_universe_for_key(
                f"{division}.scout.universe"
            )

            try:
                spec = EngagementSpec(
                    requesting_division=division,
                    product_type="candidate_recommendation",
                    asset_class="equity",
                    scope=CandidateScope(
                        mandate=mandate,
                        # Telegram-driven engagements don't have a real
                        # capacity computation — pass a sentinel; the
                        # synthesis prompt uses it as fit context only.
                        capacity_dollars=0.0,
                        current_holdings=current_holdings,
                        n_candidates=n,
                        starter_universe_key="large_mid_cap",
                    ),
                    triggered_by="telegram",
                    triggered_ts=datetime.now(timezone.utc).isoformat(),
                )
            except Exception as e:
                return f"Spec build failed: {e}"

            rec = await run_engagement(spec, deps=_research_holder[0])
            if rec is None:
                return (
                    f"`/research candidate {division} {n}` returned no product. "
                    f"Check the audit log for `research_engagement_*` rows."
                )

            # Build the message body — show conviction + fit_score
            # side-by-side so the high-conviction-low-fit pattern is
            # visible at a glance (per acceptance criterion §1a-1).
            lines = [
                f"*Candidate recommendation* — `{division}`",
                f"engagement_id: `{rec.engagement_id[:8]}...`",
                f"asset_class: `{rec.asset_class}`",
                "",
            ]
            if rec.candidates:
                lines.append("*Candidates:*")
                for c in rec.candidates:
                    thesis_short = (
                        (c.thesis[:300] + "...")
                        if len(c.thesis) > 300 else c.thesis
                    )
                    lines.append(
                        f"• `{c.symbol}` (conviction={c.conviction}, "
                        f"fit={c.fit_score:.2f}) — {thesis_short}"
                    )
                lines.append("")
            else:
                lines.append("_No candidates surfaced._")
            lines.append(
                "_Engagement complete. Phase 1a-2 will wire the PMCC scout "
                "to consume these per-candidate; for now this is a Board "
                "ad-hoc surface only._"
            )
            return "\n".join(lines)

        # Phase A of HITL-in-app direction (Board, 2026-05-03):
        # TELEGRAM_NOTIFICATION_ONLY=true → slim notification + deeplink
        # body. Defaults False (current rich format preserved). Flip
        # ON the day Phase B's web /approvals/{id} page is in place.
        # DASHBOARD_BASE_URL overrides the production default for dev.
        _tg_notify_only = (
            os.getenv("TELEGRAM_NOTIFICATION_ONLY", "false").lower() == "true"
        )
        _dashboard_base = os.getenv("DASHBOARD_BASE_URL") or None
        channel = TelegramChannel(
            secrets.telegram_bot_token,  # type: ignore[arg-type]
            secrets.telegram_chat_id,    # type: ignore[arg-type]
            on_message=_on_message,
            on_brief_command=_on_brief,
            on_scan_command=_on_scan,
            on_fidelity_scan_command=_on_fidelity_scan,
            commands=tg_commands,
            on_research_command=_on_research,
            notification_only=_tg_notify_only,
            dashboard_base_url=_dashboard_base,
            registry=pending_registry,
        )
    else:
        channel = CLIChannel()

    await channel.start()
    await channel.push(
        f"CEO online. Mode: *{mode}*. DB: `{db_path}`. "
        f"{'Telegram' if secrets.has_telegram else 'CLI'} channel active."
    )

    # The trade graph uses LangGraph's native `interrupt()` to suspend at the
    # Board approval gate. The orchestrator (here, _run_demo_order) is
    # responsible for awaiting `channel.request_approval(...)` and resuming
    # the graph with `Command(resume=...)`.

    # --- Build trade graph with checkpointer ---
    async with make_checkpointer(db_path) as saver:
        graph = build_trade_graph(risk_agent, data_exec, logger_agent, checkpointer=saver)
        _graph_holder[0] = graph   # now _on_scan / _on_message can use it

        # --- Research firm (Phase 1a, WatchlistRecommendation only) ---
        # Deliberately NOT sharing the CEO graph's AsyncSqliteSaver
        # (despite design §2.4's note that LangGraph distinguishes graphs
        # by identity, not saver). In production the checkpointer holds a
        # write transaction during HITL `interrupt()` waits, which collides
        # with the research firm's audit-row writes — observed as
        # `database is locked` on `research_engagement_started` during
        # the first prod test. Phase 1a engagements are one-shot
        # (no interrupt, no resume), so checkpointing has no functional
        # value here. If Phase 1b/c ever needs resume, swap to a separate
        # saver instance with its own DB file rather than re-sharing.
        from trading_corp.agents.research.engagement import (
            build_research_firm_deps,
        )
        research_firm = build_research_firm_deps(
            logger_agent, checkpointer=None,
        )
        _research_holder[0] = research_firm

        # Phase 1a-2: wire the research firm into the PMCC scout. With
        # `universe_source: research_on_demand` in strategies.yaml the
        # scout will source new-open candidates from the research firm
        # via run_engagement(CandidateScope) instead of the scout's
        # static universe list. The notify_callback fires only on
        # `pmcc_research_extended_outage` (consecutive failures past
        # threshold) — not on routine research-firm misses.
        async def _pmcc_outage_notify(message: str) -> None:
            try:
                await channel.push(message)
            except Exception as e:
                log.warning("PMCC outage notify push failed: %s", e)

        pmcc_agent.attach_research_firm(
            research_firm,
            logger_agent=logger_agent,
            notify_callback=_pmcc_outage_notify,
        )

        # Phase 1d: prime the PositionContext cache for each TV-driven
        # division on startup (Q7). On-alert reads in Otter/Cypher are
        # fail-soft on miss, so a failed prime isn't a blocker — it just
        # means the next alert runs uninformed until we re-prime. Run
        # in the background so startup isn't gated on yfinance latency.
        from trading_corp.agents.research.prime import (
            prime_all_division_position_contexts,
        )
        asyncio.create_task(prime_all_division_position_contexts(
            research_firm=research_firm,
            db_url=secrets.db_url,
            divisions=[
                {
                    "slug": lord_otter_agent.name,
                    "asset_class": "crypto_spot",
                    "symbols": lord_otter_agent.configured_symbols(),
                    "horizon_hours": lord_otter_agent.POSITION_CONTEXT_HORIZON_HOURS,
                },
                {
                    "slug": market_cypher_agent.name,
                    "asset_class": "crypto_spot",
                    "symbols": market_cypher_agent.configured_symbols(),
                    "horizon_hours": market_cypher_agent.POSITION_CONTEXT_HORIZON_HOURS,
                },
            ],
        ))

        if args.demo:
            await _run_demo_order(graph, channel, logger_agent)

        # --- Daily pre-open PMCC scan scheduler (weekday mornings, 8:30 ET) ---
        scheduler_task = asyncio.create_task(
            _scheduled_pmcc_scan_loop(_on_scan, channel, logger_agent)
        )

        # --- Coinbase BTC Donchian 6h-bar scheduler (00/06/12/18 UTC) ---
        # Wakes shortly after each 6h bar closes, fetches the recent OHLCV
        # window, calls agent.on_bar_close, writes a `donchian_evaluated`
        # audit row regardless of decision (UI tile depends on it), and
        # routes any returned ProposedOrder through the standard risk +
        # HITL graph. Paper-mode by default — `auto_execute: false` in
        # strategies.yaml means orders fire HITL approvals via the web app.
        donchian_task = asyncio.create_task(
            _scheduled_donchian_loop(
                donchian_agent,
                graph=graph,
                channel=channel,
                logger_agent=logger_agent,
                data_exec=data_exec,
            )
        )

        # --- Polymarket Arbitrage scanner (every 30s) ---
        # Phase 2a: pulls open Polymarket markets, deterministic-filters,
        # caps to K=10 survivors, calls Anthropic for a calibrated YES
        # probability per survivor, emits ProposedOrders on divergence.
        # Disabled by default in strategies.yaml — the loop wakes every
        # 30s but does nothing while `enabled: false`.
        from trading_corp.agents.strategies.polymarket_arbitrage import (
            PolymarketArbitrageAgent,
        )
        polymarket_arb_agent = PolymarketArbitrageAgent(db_url=secrets.db_url)
        polymarket_arb_task = asyncio.create_task(
            _scheduled_polymarket_arb_loop(
                polymarket_arb_agent,
                graph=graph,
                channel=channel,
                logger_agent=logger_agent,
                data_exec=data_exec,
                risk_agent=risk_agent,
                db_url=secrets.db_url,
            )
        )

        # --- Paper-trade replay (Phase C of would_have_placed enrichment) ---
        # One-shot startup catch-up: mark legacy pre-Phase-A rows + replay
        # any pending rows that landed during the last downtime. Then spawn
        # a 15-min periodic loop. Failures here are logged but never block
        # main startup — replay is read-only enrichment, not an ordering path.
        from trading_corp.agents.paper_trade_replay import (
            mark_pre_phase_a_rows,
            replay_pending_paper_trades_async,
            start_replay_loop,
        )
        try:
            mark_pre_phase_a_rows(secrets.db_url)
            startup_counts = await replay_pending_paper_trades_async(secrets.db_url)
            # f-string (not %s) — RedactingFilter rewrites dict args
            # into their keys, producing a TypeError on % formatting.
            log.info(f"paper_trade_replay startup catch-up: {startup_counts}")
        except Exception:
            log.exception("paper_trade_replay startup catch-up failed (continuing)")
        replay_task = start_replay_loop(secrets.db_url, interval_sec=900)

        # --- Web command center (FastAPI on :8000, in-process) ---
        web_server, web_task = await _start_web_server(
            mode=mode,
            db_url=secrets.db_url,
            db_path=db_path,
            logger_agent=logger_agent,
            data_exec=data_exec,
            trend_agent=trend_agent,
            portfolio=portfolio,
            pmcc_agent=pmcc_agent,
            fidelity_agent=fidelity_agent,
            paper_broker=paper_broker,
            secrets=secrets,
            risk_agent=risk_agent,
            dry_run=dry_run,
            lord_otter_agent=lord_otter_agent,
            market_cypher_agent=market_cypher_agent,
            telegram_channel=channel,
            research_firm=research_firm,
            pending_registry=pending_registry,
        )
        await channel.push("Web command center: http://localhost:8000")

        # --- Idle loop: keep the process alive, drain feeds, etc. ---
        # Race the 60-second sleep against the channel's shutdown signal so we
        # exit promptly when a Telegram Conflict (another bot polling) fires.
        try:
            while True:
                sleep_task = asyncio.create_task(asyncio.sleep(60))
                shutdown_task = asyncio.create_task(channel.wait_for_shutdown_signal())
                done, pending = await asyncio.wait(
                    {sleep_task, shutdown_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                if shutdown_task in done:
                    log.error(
                        "Channel signaled shutdown — exiting idle loop. "
                        "Check the Telegram-conflict line above to find the rogue process."
                    )
                    break
        except asyncio.CancelledError:
            pass
        except KeyboardInterrupt:
            pass
        finally:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except (asyncio.CancelledError, Exception):
                pass
            polymarket_arb_task.cancel()
            try:
                await polymarket_arb_task
            except (asyncio.CancelledError, Exception):
                pass
            donchian_task.cancel()
            try:
                await donchian_task
            except (asyncio.CancelledError, Exception):
                pass
            replay_task.cancel()
            try:
                await replay_task
            except (asyncio.CancelledError, Exception):
                pass
            # Stop the web server cleanly
            if web_server is not None:
                web_server.should_exit = True
                try:
                    await asyncio.wait_for(web_task, timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    web_task.cancel()
            await channel.push("CEO going offline.")
            await channel.stop()
            await data_exec.disconnect_all()
            logger_agent.log_event("system", "shutdown", {"mode": mode})

    return 0


def _build_broker_for_division(
    division,
    secrets,
    mode: str,
    live_brokers: list[str],
):
    """Build a broker handle for one division, honoring PAPER/LIVE mode.

    PAPER mode wraps real read-only brokers in PaperExecutionBroker so
    snapshots are real but fills are simulated. LIVE mode binds the real
    broker for the listed families only.
    """
    from trading_corp.brokers.coinbase import CoinbaseBroker

    family = division.broker
    is_live_family = (mode == "LIVE" and family in (live_brokers or []))

    if family == "robinhood":
        if not (secrets.robinhood_username and secrets.robinhood_password):
            log.info("Skipping division %s — no Robinhood credentials", division.slug)
            return None
        rh = RobinhoodBroker(
            username=secrets.robinhood_username,
            password=secrets.robinhood_password,
            mfa_secret=secrets.robinhood_mfa_secret,
            account_filter=division.account_filter or None,
        )
        if is_live_family:
            return rh
        paper = PaperBroker(account=f"paper_{division.slug}", starting_equity=0.0)
        return PaperExecutionBroker(rh, paper)

    if family == "fidelity":
        if not (secrets.fidelity_username and secrets.fidelity_password):
            log.info("Skipping division %s — no Fidelity credentials", division.slug)
            return None
        fid = FidelityBroker(
            username=secrets.fidelity_username,
            password=secrets.fidelity_password,
            target_account=division.account_filter or None,
        )
        if is_live_family:
            return fid
        paper = PaperBroker(account=f"paper_{division.slug}", starting_equity=0.0)
        return PaperExecutionBroker(fid, paper)

    if family == "coinbase":
        # Spot and futures are separate portfolios with separate API keys.
        # We deliberately do NOT fall back from futures→spot if the futures
        # creds are missing — spot keys are rejected on futures endpoints,
        # so silently using them would just produce confusing 401s. Missing
        # futures creds → broker initializes as a stub (zeros, no orders).
        mode_str = (division.account_filter or "spot").lower().strip()
        if mode_str == "futures":
            api_key = secrets.coinbase_futures_api_key
            api_secret = secrets.coinbase_futures_api_secret
            passphrase = secrets.coinbase_futures_passphrase
        else:
            api_key = secrets.coinbase_api_key
            api_secret = secrets.coinbase_api_secret
            passphrase = secrets.coinbase_passphrase
        cb = CoinbaseBroker(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            mode=mode_str,
        )
        if is_live_family:
            return cb
        paper = PaperBroker(account=f"paper_{division.slug}", starting_equity=0.0)
        return PaperExecutionBroker(cb, paper)

    if family == "bitunix":
        # Phase 1 read-only. BitUnix Futures broker provides snapshot + quote
        # against the live API; place_order raises NotImplementedError as a
        # backstop. In PAPER mode (default) we wrap with PaperExecutionBroker
        # so the real BitUnix balance/positions render on the dashboard while
        # any orders simulate via PaperBroker. Live order placement lands in
        # Phase 4 per `trading_corp_bitunix_vision.md`.
        from trading_corp.brokers.bitunix import BitunixBroker
        bx = BitunixBroker(
            api_key=secrets.bitunix_futures_api_key,
            api_secret=secrets.bitunix_futures_api_secret,
        )
        if is_live_family:
            return bx
        paper = PaperBroker(account=f"paper_{division.slug}", starting_equity=0.0)
        return PaperExecutionBroker(bx, paper)

    if family == "polymarket":
        # Phase 1 read-only Polymarket adapter. PolymarketBroker subclasses
        # ReadOnlyBroker (NOT Broker) — there is no `place_order` method to
        # call. Live order placement is Phase 3 work and will land as a
        # separate `Broker` subclass when the Backtester verdict +
        # auto_execute_caps memo greenlight it. Until then, no
        # PaperExecutionBroker wrap is needed: read-only adapters don't
        # have an order surface to simulate.
        from trading_corp.brokers.polymarket import PolymarketBroker
        return PolymarketBroker(
            private_key=secrets.polymarket_private_key,
            funder_address=secrets.polymarket_funder_address,
            polygon_rpc_url=secrets.polygon_rpc_url,
        )

    if family == "paper":
        return PaperBroker(account=f"paper_{division.slug}", starting_equity=0.0)

    log.warning("Unknown broker family %r for division %s", family, division.slug)
    return None


async def _start_web_server(
    *,
    mode: str,
    db_url: str,
    db_path: str,
    logger_agent: Any,
    data_exec: Any,
    trend_agent: Any,
    portfolio: Any,
    pmcc_agent: Any,
    fidelity_agent: Any,
    paper_broker: Any,
    secrets: Any,
    risk_agent: Any = None,
    dry_run: bool = False,
    lord_otter_agent: Any = None,
    market_cypher_agent: Any = None,
    telegram_channel: Any = None,
    research_firm: Any = None,
    pending_registry: Any = None,
    host: str = "0.0.0.0",
    port: int = 8000,
):
    """Start the FastAPI command center as an asyncio task in this loop.

    Returns (server, task) so the caller can request a clean shutdown via
    server.should_exit = True; the task awaits the actual stop.
    """
    import uvicorn   # type: ignore
    from trading_corp.web.app import WebDeps, create_app

    deps = WebDeps(
        db_url=db_url,
        db_path=str(db_path),
        mode=mode,
        logger_agent=logger_agent,
        data_exec=data_exec,
        trend_agent=trend_agent,
        portfolio=portfolio,
        pmcc_agent=pmcc_agent,
        fidelity_agent=fidelity_agent,
        paper_broker=paper_broker,
        secrets=secrets,
        risk_agent=risk_agent,
        dry_run=dry_run,
        lord_otter_agent=lord_otter_agent,
        market_cypher_agent=market_cypher_agent,
        telegram_channel=telegram_channel,
        research_firm=research_firm,
        pending_registry=pending_registry,
    )
    app = create_app(deps)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",       # uvicorn's request log is noisy alongside our own
        access_log=False,
        loop="asyncio",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(), name="web-server")
    log.info("Web command center listening on http://%s:%d", host, port)
    return server, task


async def _scheduled_pmcc_scan_loop(
    on_scan_callback,
    channel,
    logger_agent,
    *,
    scan_window_start_et: tuple[int, int] = (8, 30),
    scan_window_end_et: tuple[int, int] = (9, 25),
    poll_interval_sec: int = 300,
) -> None:
    """Daily pre-open PMCC scan scheduler.

    Runs `on_scan_callback` once per US trading day during the pre-open window
    (default 8:30–9:25 AM Eastern, weekdays only). Designed for the
    `scan_schedule: "daily_pre_open"` setting in strategies.yaml. Skips
    weekends and the same trading day if a scan has already fired.

    Note: this does NOT honor US market holidays — yfinance `is_holiday` would
    require an extra dep. The Risk/Data agents will simply find no fresh prices
    on those days; the scan is harmless and bails out cleanly.
    """
    from datetime import datetime, time
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
    except Exception:
        # Fallback: treat local time as ET (warn once)
        log.warning("zoneinfo unavailable; scheduler will use local time as ET")
        et = None

    win_start = time(*scan_window_start_et)
    win_end = time(*scan_window_end_et)
    last_scan_date = None

    log.info(
        "PMCC scan scheduler online: weekdays %02d:%02d–%02d:%02d ET",
        scan_window_start_et[0], scan_window_start_et[1],
        scan_window_end_et[0], scan_window_end_et[1],
    )

    while True:
        try:
            now = datetime.now(et) if et is not None else datetime.now()
            is_weekday = now.weekday() < 5
            in_window = win_start <= now.time() <= win_end
            already_scanned = (last_scan_date == now.date())

            if is_weekday and in_window and not already_scanned:
                last_scan_date = now.date()
                log.info("Scheduler firing daily pre-open PMCC scan...")
                try:
                    await channel.push(
                        f"⏰ Daily pre-open PMCC scan firing ({now.strftime('%H:%M ET')})..."
                    )
                except Exception as e:
                    log.warning("Scheduler channel push failed: %s", e)

                try:
                    result = await on_scan_callback()
                    logger_agent.log_event(
                        "scheduler", "scheduled_scan_done",
                        {"date": str(now.date()), "result": result},
                    )
                    try:
                        await channel.push(f"✅ Scheduled scan: {result}")
                    except Exception:
                        pass
                except Exception as e:
                    log.exception("Scheduled PMCC scan failed: %s", e)
                    logger_agent.log_event(
                        "scheduler", "scheduled_scan_error",
                        {"date": str(now.date()), "error": str(e)},
                    )

            await asyncio.sleep(poll_interval_sec)

        except asyncio.CancelledError:
            log.info("PMCC scan scheduler cancelled.")
            return
        except Exception as e:
            log.exception("Scheduler loop error (continuing): %s", e)
            await asyncio.sleep(poll_interval_sec)


def _seconds_until_next_6h_boundary(now, *, post_close_buffer_sec: int = 120) -> float:
    """Seconds from `now` until the next 6h-bar-close boundary plus a small
    buffer so Coinbase has finalized the closed bar before we fetch it.

    Boundaries: 00:00, 06:00, 12:00, 18:00 UTC. Returns a non-negative float.
    """
    from datetime import datetime, timedelta, timezone
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    candidates = []
    for h in (0, 6, 12, 18):
        cand_today = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if cand_today > now:
            candidates.append(cand_today)
        candidates.append(cand_today + timedelta(days=1))
    next_boundary = min(c for c in candidates if c > now)
    target = next_boundary + timedelta(seconds=post_close_buffer_sec)
    delta = (target - now).total_seconds()
    return max(delta, 1.0)


async def _fetch_recent_btc_6h_bars(symbol: str, limit: int = 200) -> list[dict]:
    """Fetch the most recent `limit` 6h OHLCV bars for `symbol` from Coinbase
    via ccxt's public endpoint (no auth). Returns a chronologically-sorted
    list of {ts, open, high, low, close, volume} dicts. Drops any in-progress
    bar whose close time hasn't passed (defensive — depending on the moment
    we hit the API ccxt may or may not include the live bar).
    """
    from datetime import datetime, timezone
    import ccxt.async_support as ccxt_async  # local import: cold-start cheap
    exchange = ccxt_async.coinbase({"enableRateLimit": True})
    try:
        raw = await exchange.fetch_ohlcv(symbol, timeframe="6h", limit=limit)
    finally:
        await exchange.close()
    bars: list[dict] = []
    granularity_sec = 6 * 3600
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for row in raw or []:
        ts_ms, o, h, l, c, v = row
        # Drop the in-progress bar (close time still in the future).
        if int(ts_ms) + granularity_sec * 1000 > now_ms:
            continue
        bars.append({
            "ts": datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc),
            "open": float(o), "high": float(h), "low": float(l),
            "close": float(c), "volume": float(v),
        })
    return bars


async def _scheduled_donchian_loop(
    agent,
    *,
    graph,
    channel,
    logger_agent,
    data_exec,
) -> None:
    """6h-bar-close scheduler for the Coinbase BTC Donchian strategy.

    Wakes ~2min after each 00/06/12/18 UTC boundary so Coinbase has finalized
    the closed bar. On each tick: fetch the rolling OHLCV window, snapshot
    the coinbase_spot broker for held-BTC + equity, evaluate, audit, and
    route any ProposedOrder through the standard risk + HITL graph.

    Idempotent on re-fire — the agent's internal last_bar_ts dedup ignores
    a second call for the same bar.
    """
    from datetime import datetime, timezone
    log.info(
        "Donchian scheduler online: wakes at 00/06/12/18 UTC + ~2min "
        "(strategy enabled=%s, auto_execute=%s)",
        agent.enabled, agent.auto_execute,
    )
    while True:
        try:
            now = datetime.now(timezone.utc)
            sleep_sec = _seconds_until_next_6h_boundary(now)
            log.info("Donchian scheduler: sleeping %.0fs until next bar close", sleep_sec)
            await asyncio.sleep(sleep_sec)

            if not agent.enabled:
                log.info("Donchian disabled in config — skipping this bar.")
                continue

            await _run_donchian_bar(agent, graph, channel, logger_agent, data_exec)

        except asyncio.CancelledError:
            log.info("Donchian scheduler cancelled.")
            return
        except Exception as e:
            log.exception("Donchian scheduler loop error (continuing): %s", e)
            # Brief sleep before retry so a persistent failure doesn't
            # tight-loop the logs. Next iteration re-computes the sleep
            # against the next boundary.
            await asyncio.sleep(60)


async def _run_donchian_bar(
    agent, graph, channel, logger_agent, data_exec,
) -> None:
    """One Donchian evaluation cycle. Extracted so tests / manual triggers
    can run a single bar without the scheduler loop. Writes the
    `donchian_evaluated` audit row regardless of decision.
    """
    from datetime import datetime, timezone

    cb = data_exec.brokers.get("coinbase_spot")
    if cb is None:
        log.warning("Donchian: no coinbase_spot broker registered; skipping.")
        return

    bars = await _fetch_recent_btc_6h_bars(agent.symbol, limit=200)
    if not bars:
        log.warning("Donchian: empty OHLCV fetch; skipping bar.")
        return

    snap = await cb.snapshot()
    held_btc = 0.0
    for pos in (snap.positions or []):
        if pos.symbol == agent.symbol:
            held_btc = float(pos.qty or 0.0)
            break
    account_equity = float(snap.equity or 0.0)
    cash = float(getattr(snap, "cash", 0.0) or 0.0)

    # Detect Board-driven balance changes (recurring deposits, manual
    # BTC purchases) by diffing this snapshot against the agent's
    # last-known balances. The strategy's own fills update tracked
    # balances via a different path (mark_filled + the next bar's
    # snapshot will already match the post-fill state). Material
    # deltas land as `balance_change` audit rows, attributed to the
    # Board. State is NEVER auto-flipped here — the strategy passively
    # absorbs whatever's on the account at the next BUY/SELL signal.
    delta = agent.record_balance_snapshot(cash=cash, btc_qty=held_btc)
    if delta is not None:
        # Pin the balance_change row to the bar's open time so the
        # decision-log displays it adjacent to its sibling
        # donchian_evaluated row from the same evaluation cycle.
        # Without this, the BAL CHG row shows the audit-row write time
        # (~bar close + 2min) while the decision row shows bar open —
        # both correct, but the 6h visual gap reads as two unrelated
        # events. data.py:build_donchian_view prefers payload.bar_ts.
        delta["bar_ts"] = bars[-1]["ts"].isoformat()
        logger_agent.log_event(agent.name, "balance_change", delta)
        log.info(
            "Donchian balance_change: state=%s delta_cash=%+.2f delta_btc=%+.8f "
            "(new cash=$%.2f, btc=%.8f)",
            delta["state_at_observation"],
            delta["delta_cash"],
            delta["delta_btc"],
            delta["new_cash"],
            delta["new_btc_qty"],
        )

    prev_verdict = agent.last_verdict
    order, reason = agent.on_bar_close(
        bars, account_equity=account_equity, held_btc=held_btc, cash=cash,
    )
    new_verdict = agent.last_verdict

    # Audit-row write — only when evaluate_donchian actually ran (i.e. not
    # a disabled / no-bars / dedup short-circuit). The UI tile reads this
    # kind to populate the per-bar log.
    if new_verdict is not None and new_verdict is not prev_verdict:
        bd = new_verdict.breakdown
        logger_agent.log_event(
            agent.name, "donchian_evaluated",
            {
                "strategy": agent.name,
                "division": agent.division,
                "decision": new_verdict.decision.value,
                "reason": new_verdict.reason,
                "current_close": bd.current_close,
                "donchian_high": bd.donchian_high,
                "donchian_low": bd.donchian_low,
                "trend_filter_sma": bd.trend_filter_sma,
                "trend_filter_passed": bd.trend_filter_passed,
                "bars_considered": bd.bars_considered,
                "bar_ts": bars[-1]["ts"].isoformat(),
                "account_equity": account_equity,
                "held_btc": held_btc,
            },
        )

    if order is None:
        log.info("Donchian @ %s: no order — %s", bars[-1]["ts"].isoformat(), reason)
        return

    logger_agent.log_proposed_order(order)
    logger_agent.log_event(
        agent.name, "donchian_order_proposed",
        {
            "strategy": agent.name,
            "division": agent.division,
            "order_id": order.id,
            "side": order.side,
            "qty": order.qty,
            "limit_price": order.limit_price,
            "reason": reason,
        },
    )
    try:
        await channel.push(
            f"📊 Donchian {order.side.upper()} signal @ ${order.limit_price:,.2f} — "
            f"{reason}. Routing for approval..."
        )
    except Exception as e:
        log.warning("Donchian channel push failed: %s", e)

    final_status = await _run_order(
        graph, channel, logger_agent, order, division=agent.division,
    )
    log.info("Donchian order %s → final_status=%s", order.id, final_status)
    logger_agent.log_event(
        agent.name, "donchian_order_result",
        {
            "strategy": agent.name,
            "division": agent.division,
            "order_id": order.id,
            "side": order.side,
            "final_status": final_status,
        },
    )

    # On a paper or live fill, flip the agent's CASH↔BTC state. The next
    # bar's broker snapshot will reconcile authoritatively, but updating
    # in-memory now keeps the next on_bar_close decision consistent if
    # the snapshot lags.
    if final_status == "filled":
        agent.mark_filled(side=order.side, fill_price=float(order.limit_price or 0.0))


async def _scheduled_polymarket_arb_loop(
    agent,
    *,
    graph,
    channel,
    logger_agent,
    data_exec,
    risk_agent,
    db_url: str,
) -> None:
    """Polymarket Arbitrage scanner loop (Phase 2a).

    Wakes every `poll_interval_sec` (default 30s; from strategies.yaml).
    On each tick:
      - If the strategy is `enabled: false`, no-op and sleep.
      - Otherwise call `agent.run_scan_cycle(broker)` which pulls markets,
        deterministic-filters, calls Anthropic per survivor, emits
        ProposedOrders on divergence ≥ min_divergence_pct.
      - Each ProposedOrder runs through `risk_agent.evaluate()` directly
        (NOT through the LangGraph trade-graph's HITL approval node).
        Polymarket arbitrage was Board-approved 2026-05-10 to skip the
        per-trade approval click given the bounded blast radius
        ($1 fixed sizing × $1K aggregate cap × deterministic-Python
        risk gate). Approved orders log `would_have_placed`; rejected
        orders log `polymarket_order_rejected_by_risk`. Telegram ping
        remains as informational visibility (not a gate).

    Phase 3 (live order placement) will add a separate execution path
    using py-clob-client signing + a daily kill switch + daily summary
    digest. NOT in scope here — current loop produces paper rows only.

    Risk gate is still load-bearing per CLAUDE.md §1: every order
    flows through `RiskAgent.evaluate()`. The 9 polymarket caps in
    `risk.yaml polymarket:` (per-position %, single-market $, daily
    aggregate, total open aggregate, implied-prob bounds) all apply.
    """
    from trading_corp.persistence.models import AccountState, StrategyState

    log.info(
        "Polymarket arbitrage scanner online (enabled=%s, auto_execute=%s, hitl=DIRECT)",
        agent.enabled, agent.auto_execute,
    )
    while True:
        try:
            # Re-read interval each tick so changes in strategies.yaml
            # take effect without a restart.
            poll_sec = float(agent._strat_cfg.get("poll_interval_sec", 30))
            await asyncio.sleep(max(5.0, poll_sec))

            if not agent.enabled:
                continue

            broker = data_exec.brokers.get(agent.division)
            if broker is None:
                log.debug(
                    "Polymarket scanner: no broker registered for division=%s; skipping cycle",
                    agent.division,
                )
                continue

            try:
                orders = await agent.run_scan_cycle(
                    broker, logger_agent=logger_agent,
                )
            except Exception as e:
                log.exception("Polymarket scanner: run_scan_cycle failed: %s", e)
                continue

            if not orders:
                continue

            # Pull account snapshot once for the cycle's risk gate.
            # Per-position % cap reads account.equity; with $1 sizing
            # this won't bind, but we feed real equity for correctness.
            try:
                snap = await broker.snapshot()
                account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
            except Exception as e:
                log.warning("Polymarket scanner: snapshot failed: %s; assuming $0 equity", e)
                account_equity = 0.0

            # Synthetic AccountState — Polymarket division doesn't track
            # peak_equity / drawdown separately yet (that's a Phase 3
            # follow-up if/when we want auto-flatten on the wallet).
            # peak_equity = current equity ⇒ drawdown_pct = 0.
            account = AccountState(
                account=agent.division,
                equity=account_equity,
                peak_equity=account_equity,
                halted=False,
            )
            strategy_state = StrategyState(strategy=agent.name, halted=False)

            log.info(
                "Polymarket scanner: %d divergence-based ProposedOrder(s) emitted",
                len(orders),
            )
            for order in orders:
                logger_agent.log_proposed_order(order)

                # Risk gate — always runs. Caps in risk.yaml polymarket:
                # are deterministic Python; LLM hallucination cannot
                # bypass them.
                verdict = risk_agent.evaluate(
                    order, account, strategy_state, db_url=db_url,
                )
                base_payload = {
                    "strategy": agent.name,
                    "division": agent.division,
                    "order_id": order.id,
                    "side": order.side,
                    "qty": order.qty,
                    "limit_price": order.limit_price,
                    "rationale": order.rationale,
                    "category": (order.extra or {}).get("category"),
                    "series": (order.extra or {}).get("series"),
                    "market_slug": (order.extra or {}).get("market_slug"),
                    "divergence_pct": (order.extra or {}).get("divergence_pct"),
                }

                if verdict.verdict == "reject":
                    logger_agent.log_event(
                        agent.name, "polymarket_order_rejected_by_risk",
                        {**base_payload, "risk_reason": verdict.reason},
                    )
                    log.info(
                        "Polymarket: risk REJECT %s — %s",
                        order.symbol, verdict.reason,
                    )
                    continue

                if verdict.verdict == "resize" and verdict.new_qty is not None:
                    log.info(
                        "Polymarket: risk RESIZE %s qty %.4f -> %.4f (%s)",
                        order.symbol, order.qty, verdict.new_qty, verdict.reason,
                    )
                    order.qty = float(verdict.new_qty)

                # Approve / resize → log as would_have_placed (paper).
                # Phase 3 will branch here on auto_execute_caps to
                # actually place the order; today everything paper.
                logger_agent.log_event(
                    agent.name, "would_have_placed",
                    {
                        **base_payload,
                        "qty": order.qty,  # post-resize
                        "implied_prob_at_entry": (order.extra or {}).get("implied_prob_at_entry"),
                        "llm_prob_estimate": (order.extra or {}).get("llm_prob_estimate"),
                        "llm_confidence": (order.extra or {}).get("llm_confidence"),
                        "outcome": (order.extra or {}).get("outcome"),
                        "condition_id": (order.extra or {}).get("condition_id"),
                        "resolves_at": (order.extra or {}).get("resolves_at"),
                        "risk_verdict": verdict.verdict,
                        "risk_reason": verdict.reason,
                    },
                )

                # Telegram ping — informational only, not a gate. Keep it
                # slim per the existing notification-only convention.
                try:
                    div_pct = float((order.extra or {}).get("divergence_pct", 0))
                    cat = (order.extra or {}).get("category") or "?"
                    await channel.push(
                        f"📊 Polymarket {order.side.upper()} {order.symbol} "
                        f"(category={cat}, divergence {div_pct:.1f}%) "
                        f"— logged to activity rail."
                    )
                except Exception as e:
                    log.warning("Polymarket channel push failed: %s", e)

        except asyncio.CancelledError:
            log.info("Polymarket arbitrage scanner cancelled.")
            return
        except Exception as e:
            log.exception("Polymarket scanner loop error (continuing): %s", e)
            await asyncio.sleep(30)


async def _build_context_md(trend_agent, portfolio, logger_agent) -> str:
    try:
        reading = trend_agent.read()
    except Exception:
        reading = None
    snap = await portfolio.snapshot()
    events = logger_agent.recent_events(limit=10)
    ctx = [
        f"Mode/regime: {getattr(reading, 'regime', 'unknown')}",
        f"Total equity: ${snap.total_equity:,.2f} (gross ${snap.gross_exposure:,.2f}, net ${snap.net_exposure:,.2f})",
        f"Recent events: {len(events)} in audit log",
    ]
    return "\n".join(ctx)


async def _make_morning_brief(trend_agent, portfolio, ceo, logger_agent) -> str:
    try:
        reading = trend_agent.read()
        regime = reading.regime
    except Exception:
        regime = "unknown"
    snap = await portfolio.snapshot()
    events = logger_agent.recent_events(limit=8)
    brief = await ceo.morning_brief(regime, snap, pending_approvals=0, recent_events=events)
    logger_agent.log_brief("morning", brief.body_md)
    return brief.body_md


def _group_orders_by_pair_id(
    orders: list[ProposedOrder],
) -> list[list[ProposedOrder]]:
    """Group `orders` so paired rolls (close + open sharing the same
    `pmcc_pair_id` in `order.extra`) end up in the same sub-list.
    Solo orders (no pair_id) become singleton lists. Result preserves
    the original order's first appearance — the iteration order users
    see in audit logs is unchanged for solo orders, and pair groups
    appear at the position of the first leg's first sighting.
    """
    pair_groups: dict[str, list[ProposedOrder]] = {}
    output: list[list[ProposedOrder]] = []
    for o in orders:
        pid = (o.extra or {}).get("pmcc_pair_id") if isinstance(o.extra, dict) else None
        if not pid:
            output.append([o])
            continue
        if pid not in pair_groups:
            pair_groups[pid] = []
            output.append(pair_groups[pid])
        pair_groups[pid].append(o)
    return output


async def _run_order(
    graph, channel, logger_agent, order: ProposedOrder, division: str = "default"
) -> str:
    """Route one ProposedOrder through risk → board approval → execution.

    Returns the final_status string ('filled', 'risk_rejected', 'board_rejected', etc.).
    """
    from langgraph.types import Command  # type: ignore
    from trading_corp.graph.interrupts import ApprovalRequest  # type: ignore

    state = {
        "proposed_order": order.to_db_row() | {"extra": order.extra},
        "division": division,
        "regime": "unknown",
    }
    config = {"configurable": {"thread_id": order.id}}

    result = await graph.ainvoke(state, config=config)
    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    while interrupts:
        interrupt_obj = interrupts[0]
        req_payload = getattr(interrupt_obj, "value", interrupt_obj)
        req = ApprovalRequest(
            order_id=req_payload["order_id"],
            summary=req_payload["summary"],
            detail=req_payload["detail"],
        )
        decision = await channel.request_approval(req)
        result = await graph.ainvoke(
            Command(resume={
                "decision": decision.decision,
                "reason": decision.reason,
                "new_qty": decision.new_qty,
                "new_limit_price": decision.new_limit_price,
            }),
            config=config,
        )
        interrupts = result.get("__interrupt__") if isinstance(result, dict) else None

    return (result or {}).get("final_status", "unknown")


async def _run_demo_order(graph, channel, logger_agent) -> None:
    """Synthetic order through the graph — exercises the full HITL flow."""
    order = ProposedOrder(
        strategy="demo",
        symbol="SPY",
        side="buy",
        qty=10,
        order_type="limit",
        limit_price=500.0,
        rationale="Phase 3 demo order — paper only.",
    )
    logger_agent.log_proposed_order(order)
    logger_agent.log_event("demo", "synthetic_proposal", {"order_id": order.id})
    await channel.push(
        f"Demo order: `{order.symbol} {order.side} {order.qty} @ ${order.limit_price}`. "
        f"Routing through risk + Board approval..."
    )
    final_status = await _run_order(graph, channel, logger_agent, order, division="default")
    await channel.push(f"Demo order final status: *{final_status}*.")


def main() -> int:
    configure_logging()
    if not _acquire_lock():
        old_pid = int(_PID_FILE.read_text().strip())
        sys.stderr.write(
            f"ERROR: trading_corp is already running (PID {old_pid}). "
            "Stop that instance first, or delete data/trading_corp.pid if it is stale.\n"
        )
        return 1
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0
    finally:
        _release_lock()


if __name__ == "__main__":
    sys.exit(main())
