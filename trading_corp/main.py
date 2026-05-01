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
    from trading_corp.agents.divisions.lord_otter import LordOtterAgent
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
    from trading_corp.agents.divisions.market_cypher import MarketCypherAgent
    market_cypher_agent = MarketCypherAgent(db_url=secrets.db_url)

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
        for order in orders:
            status = await _run_order(
                _graph_holder[0], channel, logger_agent, order, division="robinhood_pmcc"
            )
            logger_agent.log_event(
                "pmcc", "scan_order_result",
                {"order_id": order.id, "symbol": order.symbol, "status": status},
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

        channel = TelegramChannel(
            secrets.telegram_bot_token,  # type: ignore[arg-type]
            secrets.telegram_chat_id,    # type: ignore[arg-type]
            on_message=_on_message,
            on_brief_command=_on_brief,
            on_scan_command=_on_scan,
            on_fidelity_scan_command=_on_fidelity_scan,
            commands=tg_commands,
            on_research_command=_on_research,
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

        if args.demo:
            await _run_demo_order(graph, channel, logger_agent)

        # --- Daily pre-open PMCC scan scheduler (weekday mornings, 8:30 ET) ---
        scheduler_task = asyncio.create_task(
            _scheduled_pmcc_scan_loop(_on_scan, channel, logger_agent)
        )

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
