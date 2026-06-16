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
    p.add_argument("--live-divisions", nargs="*", default=[], dest="live_divisions",
                   help="Division/strategy SLUGS to arm LIVE (slug-level, e.g. "
                        "polymarket_copy_trading). E2·4 anti-half-flip: a division goes "
                        "live ONLY if its slug is listed here AND its family is "
                        "live-capable (--live + --brokers <family>). Absent/empty ⇒ every "
                        "division stays PAPER, even under --brokers. Comma- and/or "
                        "space-separated.")
    return p.parse_args(argv)


def _parse_live_divisions(raw) -> set[str]:
    """Flatten the `--live-divisions` tokens (space- and/or comma-separated) into a
    set of division slugs. Empty/None ⇒ empty set ⇒ NO division arms live (the
    opt-in, paper-by-default contract)."""
    out: set[str] = set()
    for tok in (raw or []):
        for slug in str(tok).split(","):
            slug = slug.strip()
            if slug:
                out.add(slug)
    return out


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


# ── Non-interactive (systemd) LIVE authorization — item 4, 2026-06-13 ─────
# The supervised service has no stdin, so the interactive typed-LIVE prompt
# (confirm_live) hits EOFError. To run live unattended, a DURABLE, EXPLICIT
# authorization is required: env var TC_LIVE_AUTHORIZED must equal the literal
# "LIVE" (mirrors the typed word). DURABLE by operator decision (2026-06-13):
# the var persists across restarts (incl. crash / Restart=on-failure), so an
# unplanned restart resurrects live WITHOUT re-arming. This is NOT "no TTY =>
# skip the prompt": an unset/wrong value never authorizes live — it downgrades
# to PAPER (resolve_live_decision returns "paper", NOT "abort", so systemd
# cannot crash-loop on `return 2`). Revoke by unsetting/changing the var =>
# next restart runs paper. assert_live_ready (creds) still runs on the live path.
LIVE_AUTH_ENV = "TC_LIVE_AUTHORIZED"
LIVE_AUTH_TOKEN = "LIVE"


def _stdin_is_interactive() -> bool:
    """True iff stdin is a real TTY (foreground operator). Robust to a
    closed/None stdin under systemd (returns False)."""
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except (ValueError, OSError):
        return False


def live_authorized_noninteractive(env: dict | None = None) -> bool:
    """True iff the durable non-interactive live authorization is set:
    TC_LIVE_AUTHORIZED == "LIVE" (exact, stripped). Durable — a crash /
    Restart=on-failure re-launch re-reads the still-set env and re-authorizes
    live (no consumption). Unset/changed => False => downgrade to paper."""
    env = os.environ if env is None else env
    return (env.get(LIVE_AUTH_ENV, "") or "").strip() == LIVE_AUTH_TOKEN


def resolve_live_decision(
    *,
    want_live: bool,
    interactive: bool,
    env: dict | None = None,
    input_fn=input,
) -> str:
    """Resolve the startup mode decision. Returns:
      "live"  — authorized live: interactive typed-LIVE, OR non-interactive
                durable TC_LIVE_AUTHORIZED=LIVE.
      "abort" — interactive operator declined the prompt (=> exit 2).
      "paper" — not requested, OR requested non-interactively without a valid
                authorization => DOWNGRADE to paper (NOT exit, so a systemd
                Restart=on-failure cannot crash-loop on `return 2`).
    """
    if not want_live:
        return "paper"
    if interactive:
        return "live" if confirm_live(input_fn) else "abort"
    return "live" if live_authorized_noninteractive(env) else "paper"


async def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sys.stdout.write(DISCLAIMER)
    sys.stdout.flush()

    secrets = load_secrets()
    mode = "PAPER"
    dry_run = bool(args.dry_run)
    interactive = _stdin_is_interactive()
    # item 4 — interactive typed-LIVE (foreground) OR durable non-interactive
    # TC_LIVE_AUTHORIZED=LIVE (systemd). Unauthorized non-interactive --live
    # downgrades to PAPER (never `return 2`, which would crash-loop under
    # Restart=on-failure).
    live_auth = "n/a"
    live_decision = resolve_live_decision(
        want_live=bool(args.live), interactive=interactive,
    )
    if live_decision == "abort":
        sys.stdout.write("LIVE mode NOT confirmed. Exiting (no orders placed).\n")
        return 2
    if live_decision == "live":
        try:
            assert_live_ready(secrets, tuple(args.brokers))
        except RuntimeError as e:
            sys.stdout.write(f"LIVE preflight failed: {e}\n")
            return 3
        mode = "LIVE"
        live_auth = "interactive" if interactive else "env_authorized"
        if dry_run:
            sys.stdout.write(
                "\n*** DRY-RUN ENABLED ***\n"
                "Real broker auth + reads + risk gates will run, but every\n"
                "order will be SKIPPED before broker.place_order(). Synthetic\n"
                "fills will be logged so the UI renders end-to-end.\n"
                "Approve trades freely — nothing routes to the live broker.\n\n"
            )
            sys.stdout.flush()
    else:  # "paper" — not live (not requested, or non-interactive unauthorized)
        if args.live and not interactive:
            live_auth = "downgraded_no_auth"
            sys.stdout.write(
                "LIVE requested but TC_LIVE_AUTHORIZED != LIVE — running PAPER. "
                "Set TC_LIVE_AUTHORIZED=LIVE on the systemd unit to authorize live.\n"
            )
        if dry_run:
            sys.stdout.write(
                "Note: --dry-run has no effect without LIVE; PAPER mode already "
                "uses simulated execution.\n"
            )
            dry_run = False
        sys.stdout.flush()

    # --- DB + agents ---
    db_path = db.init_db(secrets.db_url)
    logger_agent = LoggerAgent(secrets.db_url)
    logger_agent.log_event(
        "system", "startup",
        {"mode": mode, "live_brokers": list(args.brokers), "dry_run": dry_run,
         "live_authorization": live_auth},
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

    # BitUnix Futures Phase 3 division agent. Receives Otter+Cypher webhook
    # signals (additive, runs alongside existing Otter/Cypher agents).
    #   Phase 3.0 (2026-05-10): bias-only observer, no orders.
    #   Phase 3.1: full PREMIUM/STANDARD/WEAK ladder + ProposedOrder
    #              + paper-mode auto-execute (board approves caps, not trades).
    #   Phase 3.2a: live 3m bar cache (Coinbase) + real ATR(14) for stop sizing
    #               + writes paper_trade_record so existing replay loop resolves.
    # Constructed here with the deps available at this point; channel +
    # full risk-execution wiring attaches via the assignment below
    # (after channel/risk_agent are ready). See memory
    # `trading_corp_bitunix_phase3_confluence_model`.
    from trading_corp.agents.divisions.bitunix_futures_observer import (
        BitunixFuturesObserver,
    )
    from trading_corp.agents.strategies.bitunix_confluence import (
        BitUnixConfluenceConfig,
    )
    from trading_corp.data.live_bar_cache import LiveBarCache
    # BitUnix native 3m kline (public REST, no auth). Same venue we trade
    # on — eliminates cross-venue volatility-profile drift. Bybit was the
    # historical EDA source (TV chart data) but is geo-blocked from US
    # IPs, so it's not viable as a live feed. Coinbase only supports
    # {1m, 5m, 15m, 1h, 6h, 1d} — no native 3m.
    # Phase 3.2.2 + PR 4: max_bars=500 covers BOTH the PA-validator
    # session VWAP needs (24h × 60/3 = 480 3m bars) AND the HTF S/R
    # levels.py resample (3m→15m, needs ~120 3m bars). ATR(14) still
    # works at any size ≥14 bars; the larger cache is harmless to all
    # existing consumers.
    bitunix_bar_cache = LiveBarCache(
        symbol="BTCUSDT", timeframe="3m", venue="bitunix", max_bars=500,
    )
    # PR 2 — Higher-Timeframe (HTF) regime caches. Three additional
    # LiveBarCaches polling 1H / 4H / 1D bars; consumed by the new
    # `BitUnixHTFContextProvider` (PR 2) and the HTF regime gate
    # (PR 3). These don't affect the order pipeline today — the
    # `bitunix_observer` doesn't read the HTF provider until PR 3
    # wires the gate. For PR 2 they exist purely to:
    #   (a) build out the data path so a dashboard panel can render
    #       the live regime classification (read-only),
    #   (b) prove the BitUnix kline endpoint accepts higher TFs at
    #       this `max_bars` setting before we start gating on them.
    # `max_bars=250` gives EMA200 a 50-bar warmup margin. If BitUnix
    # caps `limit` lower we'll see it in `last_refresh_count`; not
    # blocking because the classifier handles `Insufficient` per-TF.
    bitunix_h1_cache = LiveBarCache(
        symbol="BTCUSDT", timeframe="1h", venue="bitunix", max_bars=250,
    )
    bitunix_h4_cache = LiveBarCache(
        symbol="BTCUSDT", timeframe="4h", venue="bitunix", max_bars=250,
    )
    bitunix_d1_cache = LiveBarCache(
        symbol="BTCUSDT", timeframe="1d", venue="bitunix", max_bars=250,
    )
    # Phase 3.2 — confluence score accumulator config (off by default;
    # flip `bitunix_futures.scoring.enabled: true` in strategies.yaml
    # after backtest greenlight). When disabled, the observer runs the
    # Phase 3.1 single-bar `_tier_for` classifier unchanged.
    #
    # PR 3c additions: also load PA validation + HTF gate-mode + HTF
    # regime config from the same YAML block. All optional with
    # disabled defaults — observer reverts to pre-PR-3c behavior if
    # the YAML block is missing or the gate mode is "off".
    _scoring_config = None
    _pa_config = None
    _htf_gate_mode = "off"
    _trade_plan_config = None       # PR 4 — adaptive trade plan
    _fee_config = None              # PR 4 — fee schedule for TP1 fee floor
    _bx_block: dict = {}            # PR 4 — surfaced outside try for from_dict downstream
    _execution_mode = "paper"       # Stage-1 N+1 — fail-closed default if YAML load fails
    try:
        import yaml as _yaml
        from pathlib import Path as _Path
        from trading_corp.agents.strategies.bitunix_pa_validation import (
            PAValidationConfig,
        )
        _strat_path = _Path(__file__).resolve().parent.parent / "config" / "strategies.yaml"
        with _strat_path.open() as _f:
            _strat_raw = _yaml.safe_load(_f)
        _bx_block = _strat_raw.get("bitunix_futures", {}) or {}
        _scoring_config = BitUnixConfluenceConfig.from_dict(_bx_block)
        _pa_config = PAValidationConfig.from_dict(_bx_block)
        _htf_gate_mode = str(
            (_bx_block.get("htf_gate") or {}).get("mode", "off")
        ).lower()
        # Stage-1 N+1 commit 2: execution_mode (paper | live). Default
        # paper. Observer's __init__ enforces final fail-closed
        # normalization; this is the YAML read site.
        _execution_mode = str(_bx_block.get("execution_mode", "paper")).lower()
        # PR 4 — adaptive trade plan + fees. Activated only when
        # `bitunix_futures.trade_plan.enabled: true` in YAML. Default
        # (block missing or enabled=false) leaves the legacy geometric
        # path active in the observer.
        _tp_block = _bx_block.get("trade_plan") or {}
        if _tp_block.get("enabled", False):
            from trading_corp.agents.strategies.trade_plan import (
                FeeConfig as _FeeConfig,
                StrategyConfig as _TradePlanConfig,
            )
            _trade_plan_config = _TradePlanConfig.from_dict(_tp_block)
            _fee_config = _FeeConfig.from_dict(_bx_block.get("fees"))
    except Exception as _e:
        log.warning(
            "bitunix scoring/pa/htf/trade_plan config load failed: %s; running pre-PR-3c only",
            _e,
        )
    # PR 4 — HTF regime config from YAML (closes the dormant from_dict
    # gap surfaced 2026-05-14). Block absent → defaults (with enabled=False).
    from trading_corp.agents.strategies.bitunix_htf_regime import HTFRegimeConfig
    try:
        _htf_config = HTFRegimeConfig.from_dict(_bx_block)
    except Exception as _e:
        log.warning("HTFRegimeConfig.from_dict failed: %s; using defaults", _e)
        _htf_config = HTFRegimeConfig.defaults()
    log.info(
        "BitUnix observer wiring: scoring=%s, pa_enabled=%s, htf_gate_mode=%s, "
        "htf_regime_enabled=%s, trade_plan_active=%s, execution_mode=%s",
        bool(_scoring_config and _scoring_config.enabled),
        bool(_pa_config and _pa_config.enabled),
        _htf_gate_mode,
        bool(_htf_config and _htf_config.enabled),
        bool(_trade_plan_config and _fee_config),
        _execution_mode,
    )
    bitunix_observer = BitunixFuturesObserver(
        db_url=secrets.db_url,
        risk_agent=risk_agent,
        data_exec=data_exec,
        logger_agent=logger_agent,
        bar_cache=bitunix_bar_cache,
        scoring_config=_scoring_config,
        # PR 3c — wire HTF gate. htf_provider is constructed below
        # (it's also reused by the dashboard); attach it after the
        # provider exists.
        pa_config=_pa_config,
        htf_config=_htf_config,
        htf_gate_mode=_htf_gate_mode,
        # PR 4 — adaptive trade plan. Both None unless YAML activates them.
        trade_plan_config=_trade_plan_config,
        fee_config=_fee_config,
        # Stage-1 N+1 commit 2 — execution mode wiring. paper-default
        # everywhere; live requires explicit YAML edit + restart.
        execution_mode=_execution_mode,
        # Stage-1 N+1 commit 4 — HITL gate for first-N live orders.
        # Wires the existing PendingApprovalRegistry singleton; the
        # observer only consults it when execution_mode=live AND
        # auto_execute=true AND counter < HITL_FIRST_N_LIVE_ORDERS.
        pending_registry=pending_registry,
        # telegram_channel attached after channel is constructed (below)
    )

    # PR 2 — HTF context provider. Wraps the 1H/4H/1D caches plus a
    # standalone BitunixBroker reference for the public funding-rate
    # endpoint (no auth required). NOT consumed by the observer in
    # PR 2 — only by the dashboard panel for read-only display. PR 3
    # adds the observer integration that gates orders on
    # `provider.regime_snapshot()`.
    from trading_corp.brokers.bitunix import BitunixBroker as _BitunixBrokerForFunding
    from trading_corp.data.bitunix_htf_context import BitUnixHTFContextProvider
    _bitunix_funding_broker = _BitunixBrokerForFunding(
        api_key=secrets.bitunix_futures_api_key,
        api_secret=secrets.bitunix_futures_api_secret,
    )
    bitunix_htf_provider = BitUnixHTFContextProvider(
        h1_cache=bitunix_h1_cache,
        h4_cache=bitunix_h4_cache,
        d1_cache=bitunix_d1_cache,
        broker=_bitunix_funding_broker,
        symbol="BTCUSDT",
        # PR 5b/5c — db_url enables funding-history persistence and the
        # continuous regime-snapshot loop. None = pre-PR-5 behavior.
        db_url=secrets.db_url,
    )
    # PR 5a — bar history archiver. Polls each HTF cache + the existing
    # 3m bar cache, INSERT OR IGNORE'ing every new closed bar into
    # `bitunix_bar_history`. Decoupled from LiveBarCache (which is
    # reused by Coinbase Donchian) so no contamination.
    from trading_corp.data.bitunix_bar_archiver import BitUnixBarArchiver
    bitunix_bar_archiver = BitUnixBarArchiver(
        db_url=secrets.db_url,
        caches=(
            bitunix_bar_cache,    # 3m
            bitunix_h1_cache,
            bitunix_h4_cache,
            bitunix_d1_cache,
        ),
    )
    # PR 3c — attach the provider to the observer after construction
    # (the observer was built before the provider). The observer's
    # gate logic skips when htf_provider is None or htf_gate_mode is
    # 'off', so until both are set + the YAML mode is shadow/enforce,
    # nothing changes.
    bitunix_observer.htf_provider = bitunix_htf_provider

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
    # E2·4 — per-division live-select (slug-level anti-half-flip): only slugs in
    # --live-divisions arm live (and only if their family is also live-capable);
    # everything else stays paper, even under --brokers <family>.
    live_divisions = _parse_live_divisions(getattr(args, "live_divisions", None))
    for d in divisions:
        if not d.enabled:
            continue
        broker = _build_broker_for_division(
            d, secrets, mode, args.brokers, live_divisions,
            logger_agent=logger_agent,
        )
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

    # Attach the now-constructed Telegram/CLI channel to the bitunix
    # division agent so it can push paper-trade notifications. Per
    # board direction, bitunix_futures uses notification (not approval)
    # via Telegram — risk caps are the gate, not per-trade HITL.
    bitunix_observer.telegram_channel = channel
    # Stage-1 N+1 commit 7b: wire the SAME channel singleton as the
    # safety_notifier for data_exec — no parallel TelegramChannel
    # instance (CLAUDE.md Phase-C principle: one channel per process).
    # The safety_notifier slot is consumed on the safety branch's
    # data_exec.py (mode-mismatch + flatten_division handlers); the
    # slot was re-added in commit 7a so this assignment is type-safe
    # right now, before the safety branch lands.
    data_exec.safety_notifier = channel
    # gate (a) sub-item 3 (2026-05-30): the bitunix broker's stuck-order
    # cancel-on-exhaustion path emits `safety_alert` telegrams directly
    # (audit + telegram are local to the broker because PART_FILLED stuck
    # orders can't raise — they return a partial-fill tuple to place_order).
    # Same TelegramChannel singleton as data_exec — no parallel instance.
    _bx_broker = data_exec.brokers.get("bitunix_futures")
    if _bx_broker is not None and hasattr(_bx_broker, "safety_notifier"):
        _bx_broker.safety_notifier = channel

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

        # --- Polymarket Copy Trader scanner (default off) ---
        # Mirrors top Polymarket whales' positions at scaled USDC sizing.
        # Selected whales come from `agent_state(selected_whales)`, populated
        # by `python -m trading_corp.scripts.refresh_polymarket_whales`
        # quarterly. Uses Polymarket's free public Data API for whale
        # activity polling (no auth, no recurring cost). Same K3 audit
        # patterns; division=polymarket_copy_trading, paper-mode initially.
        from trading_corp.agents.strategies.polymarket_copy_trader import (
            PolymarketCopyTraderAgent,
        )
        polymarket_copy_agent = PolymarketCopyTraderAgent(db_url=secrets.db_url)
        polymarket_copy_task = asyncio.create_task(
            _scheduled_polymarket_copy_trader_loop(
                polymarket_copy_agent,
                channel=channel,
                logger_agent=logger_agent,
                data_exec=data_exec,
                risk_agent=risk_agent,
                db_url=secrets.db_url,
            )
        )

        # --- Kalshi Tail-Price Arb scanner (Phase K2.1; default off) ---
        # Detects same-market YES+NO arb at price tails (≤5¢ or ≥95¢)
        # where Kalshi's 1¢ rounding floor compresses round-trip cost
        # to ~2¢. Discovery is category-targeted via broker.list_markets().
        # Each detection emits a PAIR of ProposedOrders (BUY YES + BUY NO)
        # linked via kalshi_pair_id; both legs flow through risk_agent +
        # log as `would_have_placed` (paper). Phase K5+ will branch here
        # to route through a real KalshiLiveBroker.
        from trading_corp.agents.strategies.kalshi_tail_price_arb import (
            KalshiTailPriceArbAgent,
        )
        kalshi_arb_agent = KalshiTailPriceArbAgent(db_url=secrets.db_url)
        kalshi_arb_task = asyncio.create_task(
            _scheduled_kalshi_arb_loop(
                kalshi_arb_agent,
                channel=channel,
                logger_agent=logger_agent,
                data_exec=data_exec,
                risk_agent=risk_agent,
                db_url=secrets.db_url,
            )
        )

        # --- Kalshi Temporal + Bucket Arb scanner (Phase K2.2; default off) ---
        # Sibling of kalshi_tail_price_arb sharing the same broker discovery.
        # Detects pair-wise constraint violations on TEMPORAL events
        # (P(early) > P(late) with later-dated cutoff) and bucket-sum
        # violations on BUCKET events (sum(yes_ask) < $1 - threshold).
        # Same loop pattern as the tail scanner; emits 2-leg or N-leg
        # ProposedOrder sets via `kalshi_arb_set_id`.
        from trading_corp.agents.strategies.kalshi_temporal_bucket_arb import (
            KalshiTemporalBucketArbAgent,
        )
        kalshi_tb_agent = KalshiTemporalBucketArbAgent(db_url=secrets.db_url)
        kalshi_tb_task = asyncio.create_task(
            _scheduled_kalshi_tb_arb_loop(
                kalshi_tb_agent,
                channel=channel,
                logger_agent=logger_agent,
                data_exec=data_exec,
                risk_agent=risk_agent,
                db_url=secrets.db_url,
            )
        )

        # --- Kalshi LLM Arbitrage scanner (Phase K6.1; default off) ---
        # LLM-divergence Kalshi strategy mirroring polymarket_arbitrage.
        # Lives on its own division (kalshi_llm_arbitrage). Reuses
        # polymarket's analyst-persona prompt + warm-and-fan LLM pattern.
        # Audit events: kalshi_llm_scan_cycle (per-cycle bookkeeping) +
        # kalshi_llm_probability_called (per-market LLM result, the rich
        # one) + would_have_placed (when divergence ≥ threshold).
        from trading_corp.agents.strategies.kalshi_llm_arbitrage import (
            KalshiLLMArbitrageAgent,
        )
        kalshi_llm_agent = KalshiLLMArbitrageAgent(db_url=secrets.db_url)
        kalshi_llm_task = asyncio.create_task(
            _scheduled_kalshi_llm_arb_loop(
                kalshi_llm_agent,
                channel=channel,
                logger_agent=logger_agent,
                data_exec=data_exec,
                risk_agent=risk_agent,
                db_url=secrets.db_url,
            )
        )

        # --- Kalshi Weather Arbitrage (2026-05-14) ---
        # Forecast-driven Climate/Weather strategy. Replaces the generic
        # LLM probability call for these markets — uses NWS hourly
        # forecast + Gaussian probability math, no LLM in path.
        from trading_corp.agents.strategies.kalshi_weather_arb import (
            KalshiWeatherArbAgent,
        )
        kalshi_weather_agent = KalshiWeatherArbAgent(db_url=secrets.db_url)
        kalshi_weather_task = asyncio.create_task(
            _scheduled_kalshi_weather_arb_loop(
                kalshi_weather_agent,
                channel=channel,
                logger_agent=logger_agent,
                data_exec=data_exec,
                risk_agent=risk_agent,
                db_url=secrets.db_url,
            )
        )

        # --- Kalshi Crypto Arbitrage (2026-05-14) ---
        # Live-spot-driven Crypto strategy. Replaces the generic LLM call
        # for these markets — uses Coinbase spot + Gaussian probability,
        # no LLM in path.
        from trading_corp.agents.strategies.kalshi_crypto_arb import (
            KalshiCryptoArbAgent,
        )
        kalshi_crypto_agent = KalshiCryptoArbAgent(db_url=secrets.db_url)
        kalshi_crypto_task = asyncio.create_task(
            _scheduled_kalshi_crypto_arb_loop(
                kalshi_crypto_agent,
                channel=channel,
                logger_agent=logger_agent,
                data_exec=data_exec,
                risk_agent=risk_agent,
                db_url=secrets.db_url,
            )
        )

        # --- Kalshi Sports Scout (2026-05-14, read-only observer) ---
        # No order emission. Logs bookmaker vs Kalshi divergence to
        # `kalshi_sports_observed` audit. 7-day pass to validate edge.
        from trading_corp.agents.strategies.kalshi_sports_scout import (
            KalshiSportsScoutAgent,
        )
        kalshi_sports_scout_agent = KalshiSportsScoutAgent(
            odds_api_key=secrets.odds_api_key,
            db_url=secrets.db_url,
        )
        kalshi_sports_scout_task = asyncio.create_task(
            _scheduled_kalshi_sports_scout_loop(
                kalshi_sports_scout_agent,
                logger_agent=logger_agent,
                data_exec=data_exec,
            )
        )

        # --- Kalshi Sports Arbitrage observer (2026-05-23, Phase 0) ---
        # Sibling of the scout, separate division (kalshi_arbitrage).
        # Writes kalshi_sports_arb_observation audit with raw quotes +
        # per-book + EV-at-fill (A + B). NEVER emits orders.
        from trading_corp.agents.strategies.kalshi_sports_arb_observer import (
            KalshiSportsArbObserverAgent,
        )
        kalshi_sports_arb_observer_agent = KalshiSportsArbObserverAgent(
            odds_api_key=secrets.odds_api_key,
            db_url=secrets.db_url,
        )
        kalshi_sports_arb_observer_task = asyncio.create_task(
            _scheduled_kalshi_sports_arb_observer_loop(
                kalshi_sports_arb_observer_agent,
                logger_agent=logger_agent,
                data_exec=data_exec,
            )
        )

        # --- Kalshi Copy Trader scanner (Phase K3; default off) ---
        from trading_corp.agents.strategies.kalshi_copy_trader import (
            KalshiCopyTraderAgent,
        )
        kalshi_copy_agent = KalshiCopyTraderAgent(db_url=secrets.db_url)
        kalshi_copy_task = asyncio.create_task(
            _scheduled_kalshi_copy_trader_loop(
                kalshi_copy_agent,
                apify_token=secrets.apify_api_token,
                channel=channel,
                logger_agent=logger_agent,
                data_exec=data_exec,
                risk_agent=risk_agent,
                db_url=secrets.db_url,
            )
        )

        # --- Robinhood Joint Iron Condor v1 (IC1, 2026-05-17) ---
        # 45 DTE neutral premium-selling on SPY/QQQ/IWM/GLD/TLT.
        # HITL on every action — `auto_execute=false` in strategies.yaml
        # is load-bearing. Two asyncio loops: daily signal scanner fires
        # in the 09:45–09:50 ET window on US market days; dynamic-cadence
        # position manager runs startup_catchup then loops at 5/15/30 min
        # based on the most-stressed open IC's short delta.
        from trading_corp.agents.divisions.robinhood_joint import RobinhoodJointAgent
        from trading_corp.agents.strategies.robinhood_joint_iron_condor import (
            RobinhoodJointIronCondorAgent,
        )
        from trading_corp.agents.strategies._ic_orchestration import (
            run_signal_scanner_loop as _ic_run_signal_scanner_loop,
            run_position_manager_loop as _ic_run_position_manager_loop,
        )
        from trading_corp.comms.pending_combo_registry import PendingComboRegistry
        from trading_corp.comms.telegram_batcher import TelegramBatcher
        from trading_corp.persistence.models import StrategyState as _ICStrategyState

        ic_division = RobinhoodJointAgent()
        ic_strategy = RobinhoodJointIronCondorAgent(db_url=secrets.db_url)
        ic_division.attach_strategy(ic_strategy)

        # Per-strategy Telegram batcher in front of the BoardChannel.
        # 60s window + bypass tags mirror strategies.yaml notifications
        # block. Severe events (catastrophic_stop, late_dte_force_close,
        # circuit_breaker_auto_repause, startup_catchup) ping immediately.
        ic_telegram_batcher = TelegramBatcher(
            channel,
            batch_window_sec=60.0,
            bypass_tags=(
                "circuit_breaker_auto_repause",
                "catastrophic_stop",
                "startup_catchup",
                "late_dte_force_close",
            ),
        )

        # In-process HITL registry — /approvals/combos/{combo_id} routes
        # read + resolve via this. Lost on restart by design (v1
        # simplification per planning/iron_condor_v1_plan.md § 5.3); the
        # strategy re-proposes on the next manage() tick if conditions
        # still hold.
        pending_combo_registry = PendingComboRegistry(logger_agent=logger_agent)

        # Broker for the robinhood_joint division. Falls back to the
        # process paper_broker if the RH connect failed at startup
        # (broker_fallback_to_paper $0-equity path) — IC sizing math will
        # produce qty=0 candidates that the risk gate rejects, so the
        # loops stay running but emit nothing until RH is restored.
        _rj_broker = data_exec.brokers.get(ic_division.slug) or paper_broker

        async def _ic_account_factory():
            return await _rj_broker.snapshot()

        def _ic_strategy_state_factory():
            return _ICStrategyState(strategy=ic_strategy.SLUG, halted=False)

        ic_signal_scanner_task = asyncio.create_task(
            _ic_run_signal_scanner_loop(
                division=ic_division,
                broker=_rj_broker,
                strategy=ic_strategy,
                risk_agent=risk_agent,
                logger_agent=logger_agent,
                data_exec=data_exec,
                account_factory=_ic_account_factory,
                strategy_state_factory=_ic_strategy_state_factory,
                telegram_batcher=ic_telegram_batcher,
                pending_combo_registry=pending_combo_registry,
            ),
            name="ic-signal-scanner",
        )
        ic_position_manager_task = asyncio.create_task(
            _ic_run_position_manager_loop(
                division=ic_division,
                broker=_rj_broker,
                strategy=ic_strategy,
                risk_agent=risk_agent,
                logger_agent=logger_agent,
                data_exec=data_exec,
                account_factory=_ic_account_factory,
                strategy_state_factory=_ic_strategy_state_factory,
                telegram_batcher=ic_telegram_batcher,
                pending_combo_registry=pending_combo_registry,
            ),
            name="ic-position-manager",
        )

        # --- Tasty Options Iron Condor v1 (2026-05-24, Commit 4/5) ---
        # Sibling of robinhood_joint_iron_condor on Tastytrade. Identical
        # decision tree; only difference is the permissive `watchlist`
        # semantic vs the hard-gate `universe` (see strategies.yaml +
        # ic_candidate_grader.py:strict_universe). HITL on every action —
        # auto_execute:false in strategies.yaml is load-bearing. Same two
        # asyncio loops as RH Joint. Reuses the single shared RiskAgent +
        # _ic_orchestration helpers (already division-parameterized via
        # division= kwarg in propose_ic_combo); separate
        # PendingComboRegistry + TelegramBatcher so audit ownership and
        # restart-isolation stay clean across divisions.
        from trading_corp.agents.divisions.tasty_options import TastyOptionsAgent
        from trading_corp.agents.strategies.tasty_options_iron_condor import (
            TastyOptionsIronCondorAgent,
        )

        tasty_division = TastyOptionsAgent()
        tasty_strategy = TastyOptionsIronCondorAgent(db_url=secrets.db_url)
        tasty_division.attach_strategy(tasty_strategy)

        tasty_telegram_batcher = TelegramBatcher(
            channel,
            batch_window_sec=60.0,
            bypass_tags=(
                "circuit_breaker_auto_repause",
                "catastrophic_stop",
                "startup_catchup",
                "late_dte_force_close",
            ),
        )

        tasty_pending_combo_registry = PendingComboRegistry(
            logger_agent=logger_agent,
        )

        # Broker for the tasty_options division. Falls back to the
        # process paper_broker if TT connect failed at startup (same
        # broker_fallback_to_paper $0-equity pattern as RH Joint).
        _tasty_broker = data_exec.brokers.get(tasty_division.slug) or paper_broker

        async def _tasty_account_factory():
            return await _tasty_broker.snapshot()

        def _tasty_strategy_state_factory():
            return _ICStrategyState(strategy=tasty_strategy.SLUG, halted=False)

        tasty_signal_scanner_task = asyncio.create_task(
            _ic_run_signal_scanner_loop(
                division=tasty_division,
                broker=_tasty_broker,
                strategy=tasty_strategy,
                risk_agent=risk_agent,
                logger_agent=logger_agent,
                data_exec=data_exec,
                account_factory=_tasty_account_factory,
                strategy_state_factory=_tasty_strategy_state_factory,
                telegram_batcher=tasty_telegram_batcher,
                pending_combo_registry=tasty_pending_combo_registry,
            ),
            name="tasty-signal-scanner",
        )
        tasty_position_manager_task = asyncio.create_task(
            _ic_run_position_manager_loop(
                division=tasty_division,
                broker=_tasty_broker,
                strategy=tasty_strategy,
                risk_agent=risk_agent,
                logger_agent=logger_agent,
                data_exec=data_exec,
                account_factory=_tasty_account_factory,
                strategy_state_factory=_tasty_strategy_state_factory,
                telegram_batcher=tasty_telegram_batcher,
                pending_combo_registry=tasty_pending_combo_registry,
            ),
            name="tasty-position-manager",
        )


        # --- Polymarket round-trip resolver + equity snapshot writer ---
        # Closes the data gaps for the betmoar-style portfolio dashboard:
        #   - resolver: hourly walk of `would_have_placed` rows whose
        #     market has resolved → INSERT into polymarket_round_trips.
        #   - equity_snapshot: 5-min broker.snapshot() → INSERT into
        #     polymarket_equity_history (equity curve source data).
        # Both are read-only enrichment; failures log + skip. If the
        # broker isn't registered (division wiring missing or stub
        # mode), both tasks no-op but stay running so a later config
        # flip starts capturing data without a restart.
        from trading_corp.agents.polymarket_resolver import (
            start_equity_snapshot_loop,
            start_resolver_loop,
        )
        polymarket_broker_for_resolver = data_exec.brokers.get(
            polymarket_arb_agent.division
        )
        if polymarket_broker_for_resolver is not None:
            polymarket_resolver_task = start_resolver_loop(
                secrets.db_url,
                polymarket_broker_for_resolver,
                interval_sec=3600,
            )
            polymarket_equity_task = start_equity_snapshot_loop(
                secrets.db_url,
                polymarket_arb_agent.division,
                polymarket_broker_for_resolver,
                interval_sec=300,
            )
        else:
            log.warning(
                "Polymarket resolver/equity-snapshot loops not started: "
                "no broker registered for division=%s",
                polymarket_arb_agent.division,
            )
            polymarket_resolver_task = None
            polymarket_equity_task = None

        # --- Kalshi round-trip resolver + equity snapshot writers (Phase K2.4) ---
        # Closes the same data gaps across the Kalshi divisions
        # (kalshi_arbitrage, kalshi_llm_arbitrage, kalshi_weather, kalshi_crypto;
        # kalshi_copy_trading uses the same resolver via paired-exits). One
        # resolver loop scans would_have_placed rows from ALL strategies in
        # `kalshi_resolver._KALSHI_ACTORS` and writes to the shared
        # kalshi_round_trips table. Per-division equity-snapshot loops record
        # paper equity over time. kalshi_arbitrage + kalshi_llm_arbitrage
        # share one funded KalshiBroker; kalshi_weather + kalshi_crypto have
        # their own per-division PaperBrokers (paper_capital=$500 each).
        from trading_corp.agents.kalshi_resolver import (
            start_equity_snapshot_loop as start_kalshi_equity_snapshot_loop,
            start_resolver_loop as start_kalshi_resolver_loop,
        )
        kalshi_broker_for_resolver = data_exec.brokers.get(
            kalshi_arb_agent.division
        )
        if kalshi_broker_for_resolver is not None:
            kalshi_resolver_task = start_kalshi_resolver_loop(
                secrets.db_url,
                kalshi_broker_for_resolver,
                interval_sec=3600,
            )
            kalshi_equity_task_arb = start_kalshi_equity_snapshot_loop(
                secrets.db_url,
                kalshi_arb_agent.division,
                kalshi_broker_for_resolver,
                interval_sec=300,
            )
        else:
            log.warning(
                "Kalshi resolver/equity-snapshot (kalshi_arbitrage) not started: "
                "no broker registered for division=%s",
                kalshi_arb_agent.division,
            )
            kalshi_resolver_task = None
            kalshi_equity_task_arb = None

        kalshi_broker_for_llm = data_exec.brokers.get(
            kalshi_llm_agent.division
        )
        if kalshi_broker_for_llm is not None:
            kalshi_equity_task_llm = start_kalshi_equity_snapshot_loop(
                secrets.db_url,
                kalshi_llm_agent.division,
                kalshi_broker_for_llm,
                interval_sec=300,
            )
        else:
            log.warning(
                "Kalshi equity-snapshot (kalshi_llm_arbitrage) not started: "
                "no broker registered for division=%s",
                kalshi_llm_agent.division,
            )
            kalshi_equity_task_llm = None

        kalshi_broker_for_weather = data_exec.brokers.get(
            kalshi_weather_agent.division
        )
        if kalshi_broker_for_weather is not None:
            kalshi_equity_task_weather = start_kalshi_equity_snapshot_loop(
                secrets.db_url,
                kalshi_weather_agent.division,
                kalshi_broker_for_weather,
                interval_sec=300,
            )
        else:
            log.warning(
                "Kalshi equity-snapshot (kalshi_weather) not started: "
                "no broker registered for division=%s",
                kalshi_weather_agent.division,
            )
            kalshi_equity_task_weather = None

        kalshi_broker_for_crypto = data_exec.brokers.get(
            kalshi_crypto_agent.division
        )
        if kalshi_broker_for_crypto is not None:
            kalshi_equity_task_crypto = start_kalshi_equity_snapshot_loop(
                secrets.db_url,
                kalshi_crypto_agent.division,
                kalshi_broker_for_crypto,
                interval_sec=300,
            )
        else:
            log.warning(
                "Kalshi equity-snapshot (kalshi_crypto) not started: "
                "no broker registered for division=%s",
                kalshi_crypto_agent.division,
            )
            kalshi_equity_task_crypto = None

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
        # Wire the bitunix lifecycle notifier AFTER the startup catch-up so
        # the backfill of resolutions missed during downtime stays silent;
        # only going-forward live resolutions ping Telegram. Observability-
        # only — a notifier failure never blocks the replay.
        try:
            from trading_corp.agents.paper_trade_replay import (
                set_lifecycle_notifier,
            )
            from trading_corp.comms.bitunix_lifecycle_notifier import (
                BitunixLifecycleNotifier,
            )
            set_lifecycle_notifier(
                BitunixLifecycleNotifier(
                    channel,
                    db_url=secrets.db_url,
                    paper_mode=(str(mode).lower() != "live"),
                )
            )
        except Exception:
            log.exception("bitunix lifecycle notifier wiring failed (continuing)")
        # ── Stage-1 N+2 Phase 3 Session B Commit 4: live-exit executor ──
        # Register the bitunix_observer so the replay loop forks live-tagged
        # rows (extra.execution_mode="live" from Path C) to broker close via
        # observer._execute_live_exits. Paper-tagged rows continue to use
        # _update_row + _queue_close_out_notification (Session A behavior
        # unchanged). The registration is unconditional — the fork is
        # additionally gated inside _replay_tick_async on the row's
        # execution_mode tag, so paper rows always take the paper path.
        try:
            from trading_corp.agents.paper_trade_replay import (
                set_live_exit_executor,
            )
            set_live_exit_executor(bitunix_observer)
        except Exception:
            log.exception("bitunix live-exit executor wiring failed (continuing)")
        replay_task = start_replay_loop(secrets.db_url, interval_sec=900)

        # --- BitUnix 3m bar cache poll (Phase 3.2a) ---
        # Coinbase 3m OHLCV pulled every 60s, cached in-process. Powers the
        # real ATR(14) used by the bitunix_futures order proposer for
        # structural stop sizing. Replaces the 0.04%-of-price placeholder
        # from Phase 3.0/3.1.
        # Prime the cache once synchronously so the first inbound trigger
        # has data; then start the periodic loop.
        try:
            await bitunix_bar_cache.refresh()
            log.info(f"bitunix_bar_cache primed: {bitunix_bar_cache.status()}")
        except Exception:
            log.exception("bitunix_bar_cache prime failed (continuing)")
        bitunix_bar_task = asyncio.create_task(
            bitunix_bar_cache.run_poll_loop(interval_s=60.0),
            name="bitunix-bar-cache",
        )

        # --- HTF caches (PR 2) — poll 1H/4H/1D bars + funding rate ---
        # Cadences sized to ~1/3 of each TF's bar duration so a new bar
        # is picked up promptly after close. Funding rate updates every
        # 8h on BitUnix; 30 min poll keeps the cached value warm.
        for _cache, _interval, _name in (
            (bitunix_h1_cache, 300.0, "bitunix-h1-cache"),    # 5 min
            (bitunix_h4_cache, 900.0, "bitunix-h4-cache"),    # 15 min
            (bitunix_d1_cache, 1800.0, "bitunix-d1-cache"),   # 30 min
        ):
            try:
                await _cache.refresh()
                log.info(f"{_name} primed: {_cache.status()}")
            except Exception:
                log.exception(f"{_name} prime failed (continuing)")
            asyncio.create_task(
                _cache.run_poll_loop(interval_s=_interval),
                name=_name,
            )
        try:
            await bitunix_htf_provider.refresh_funding_rate()
            log.info(
                f"bitunix HTF funding primed: rate="
                f"{bitunix_htf_provider._last_funding_rate}"
            )
        except Exception:
            log.exception("bitunix HTF funding prime failed (continuing)")
        asyncio.create_task(
            bitunix_htf_provider.run_funding_poll_loop(interval_s=1800.0),
            name="bitunix-htf-funding",
        )

        # PR 5a — bar archiver. Mirrors the slowest cache poll cadence
        # (60s) so we capture each new bar within ~one tick of when
        # it appears in any cache.
        try:
            n_first = bitunix_bar_archiver.archive_once()
            log.info(f"bitunix_bar_archiver primed: {n_first} bars")
        except Exception:
            log.exception("bitunix_bar_archiver prime failed (continuing)")
        asyncio.create_task(
            bitunix_bar_archiver.run_loop(interval_s=60.0),
            name="bitunix-bar-archiver",
        )

        # PR 5c — continuous HTF regime snapshot loop (10-min cadence).
        # Provides time-series data on the regime classifier outside of
        # fire moments. Skips no-op when htf_config is unwired.
        if _htf_config is not None:
            asyncio.create_task(
                bitunix_htf_provider.run_regime_snapshot_loop(
                    config=_htf_config, interval_s=600.0,
                ),
                name="bitunix-htf-regime-snapshot",
            )

        # Deferred-fire PA redeem loop. When PA rejects a high-score TV
        # alert in enforce mode, the observer caches the payload; this
        # loop re-evaluates against fresh bars every 60s until the score
        # decays (cache cleared in SKIP path) or PA passes (cache cleared,
        # `pa_validation_redeem` audit row written). See observer
        # `run_pa_redeem_loop` for the lifecycle.
        asyncio.create_task(
            bitunix_observer.run_pa_redeem_loop(interval_s=60.0),
            name="bitunix-pa-redeem",
        )

        # ── Stage-1 N+2 Phase 3 Session B Commit 5 (5a): restart-resume ──
        # cases (a) match + (b) orphan + (c-deferred). Runs BEFORE the
        # position-state reconciler so the broker_order_id-aware match
        # pass happens first; reconcile_position_state's startup sweep
        # is then redundant but cheap (idempotent).
        if (
            _execution_mode == "live"
            and _bx_broker is not None
            and hasattr(_bx_broker, "get_pending_positions")
        ):
            try:
                from trading_corp.agents.divisions.bitunix_position_reconciler import (
                    resume_live_positions as _resume_live_positions,
                )
                # Lifecycle notifier may not yet be wired (it lands later
                # in startup); pass None for now — restart-resume telegram
                # is best-effort. The audit kinds carry the operator-facing
                # detail regardless.
                _resume_summary = await _resume_live_positions(
                    _bx_broker, secrets.db_url, notifier=None,
                )
                log.info(
                    "bitunix restart-resume at startup: matched=%d "
                    "orphan=%d case_c_deferred=%d",
                    len(_resume_summary.matched),
                    len(_resume_summary.orphan_on_broker),
                    len(_resume_summary.case_c_deferred),
                )
            except Exception:
                log.exception(
                    "bitunix restart-resume at startup failed (continuing)"
                )

        # ── Stage-1 N+2 Phase 3 Session B Commit 3: position-state ──
        # reconciler one-shot at startup. Compares bot-tracked live rows
        # (paper_trade_record WHERE result IS NULL AND
        # extra.execution_mode='live') against broker.get_pending_positions()
        # truth. On divergence: writes position_state_divergence_detected
        # audit + sets _bx_broker._halt_new_orders=True (entries halt;
        # exits flow per Phase 1a §9c). Awaited (not create_task) so the
        # halt latch is set BEFORE any downstream tasks start.
        #
        # Gated to execution_mode=live: paper mode brokers (PaperExecutionBroker,
        # PaperBroker) don't implement get_pending_positions and have no
        # live-tagged rows to reconcile — skipping avoids spurious paper-mode
        # halts and AttributeError noise. The reconciler itself catches all
        # broker exceptions; the gate is belt-and-suspenders + clarity.
        if (
            _execution_mode == "live"
            and _bx_broker is not None
            and hasattr(_bx_broker, "get_pending_positions")
        ):
            try:
                from trading_corp.agents.divisions.bitunix_position_reconciler import (
                    reconcile_position_state as _reconcile_position_state,
                )
                _recon_result = await _reconcile_position_state(
                    _bx_broker, secrets.db_url,
                )
                if _recon_result.has_divergence:
                    log.warning(
                        "bitunix position-state reconciler at startup: "
                        "DIVERGENCE — %d missing_on_broker, %d orphan_on_broker; "
                        "broker._halt_new_orders=True (entries halted)",
                        len(_recon_result.missing_on_broker),
                        len(_recon_result.orphan_on_broker),
                    )
                else:
                    log.info(
                        "bitunix position-state reconciler at startup: clean "
                        "(%d matched live rows)", len(_recon_result.matches),
                    )
            except Exception:
                # Reconciler failure must not crash startup. The audit-of-
                # nothing is the operator's signal that the check didn't run.
                log.exception(
                    "bitunix position-state reconciler at startup failed "
                    "(continuing; halt latch unchanged)"
                )

        # ── Stage-1 N+2 Phase 3 Session B Commit 5 (5b): 60s sanity poll ──
        # Background task running `reconcile_position_state` every 60s.
        # Catches drift that develops AFTER the startup check passed
        # (broker auto-close during idle, operator UI changes, etc.).
        # Notifier is passed in lazily — wired below as soon as it lands.
        if (
            _execution_mode == "live"
            and _bx_broker is not None
            and hasattr(_bx_broker, "get_pending_positions")
        ):
            try:
                from trading_corp.agents.divisions.bitunix_position_reconciler import (
                    run_position_state_sanity_poll_loop as
                    _run_position_state_sanity_poll_loop,
                )
                asyncio.create_task(
                    _run_position_state_sanity_poll_loop(
                        _bx_broker, secrets.db_url,
                        interval_s=60.0,
                        notifier=None,  # set after lifecycle notifier wires below
                    ),
                    name="bitunix-position-state-sanity-poll",
                )
            except Exception:
                log.exception(
                    "bitunix position-state sanity poll wiring failed "
                    "(continuing)"
                )

        # trade-plan PR 5 — position SL reconciler. Stateless 60s loop
        # that decides SL moves (BE → tp1 → Chandelier trail) per the
        # v2 lifecycle and emits `position_sl_update` audit rows. PR 5
        # does NOT call the broker — Phase 4 wires that. Construct a
        # fresh stub-mode BitunixBroker for paper-mode DB queries; the
        # reconciler's `list_open_positions` path is auth-free.
        if _trade_plan_config is not None:
            from trading_corp.agents.divisions.bitunix_position_reconciler import (
                ReconcilerConfig as _ReconcilerConfig,
                run_reconciler_loop as _run_reconciler_loop,
            )
            from trading_corp.brokers.bitunix import BitunixBroker as _BitunixBroker
            _reconciler_broker = _BitunixBroker(api_key=None, api_secret=None)
            _reconciler_config = _ReconcilerConfig.from_dict(
                _bx_block.get("trade_plan") or {}
            )
            asyncio.create_task(
                _run_reconciler_loop(
                    _reconciler_broker, secrets.db_url, _reconciler_config,
                ),
                name="bitunix-position-reconciler",
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
            pending_registry=pending_registry,
            bitunix_observer=bitunix_observer,
            bitunix_htf_provider=bitunix_htf_provider,
            ic_division=ic_division,
            ic_strategy=ic_strategy,
            ic_telegram_batcher=ic_telegram_batcher,
            pending_combo_registry=pending_combo_registry,
            tasty_division=tasty_division,
            tasty_strategy=tasty_strategy,
            tasty_telegram_batcher=tasty_telegram_batcher,
            tasty_pending_combo_registry=tasty_pending_combo_registry,
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
            kalshi_arb_task.cancel()
            try:
                await kalshi_arb_task
            except (asyncio.CancelledError, Exception):
                pass
            kalshi_tb_task.cancel()
            try:
                await kalshi_tb_task
            except (asyncio.CancelledError, Exception):
                pass
            kalshi_llm_task.cancel()
            try:
                await kalshi_llm_task
            except (asyncio.CancelledError, Exception):
                pass
            if polymarket_resolver_task is not None:
                polymarket_resolver_task.cancel()
                try:
                    await polymarket_resolver_task
                except (asyncio.CancelledError, Exception):
                    pass
            if polymarket_equity_task is not None:
                polymarket_equity_task.cancel()
                try:
                    await polymarket_equity_task
                except (asyncio.CancelledError, Exception):
                    pass
            for _kalshi_task in (
                kalshi_resolver_task,
                kalshi_equity_task_arb,
                kalshi_equity_task_llm,
            ):
                if _kalshi_task is not None:
                    _kalshi_task.cancel()
                    try:
                        await _kalshi_task
                    except (asyncio.CancelledError, Exception):
                        pass
            donchian_task.cancel()
            try:
                await donchian_task
            except (asyncio.CancelledError, Exception):
                pass
            ic_signal_scanner_task.cancel()
            try:
                await ic_signal_scanner_task
            except (asyncio.CancelledError, Exception):
                pass
            ic_position_manager_task.cancel()
            try:
                await ic_position_manager_task
            except (asyncio.CancelledError, Exception):
                pass
            tasty_signal_scanner_task.cancel()
            try:
                await tasty_signal_scanner_task
            except (asyncio.CancelledError, Exception):
                pass
            tasty_position_manager_task.cancel()
            try:
                await tasty_position_manager_task
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
    live_divisions=None,
    *,
    logger_agent=None,
):
    """Build a broker handle for one division, honoring PAPER/LIVE mode.

    PAPER mode wraps real read-only brokers in PaperExecutionBroker so
    snapshots are real but fills are simulated. LIVE mode binds the real
    broker for a division ONLY when it is both family-live-capable AND
    slug-selected (see the E2·4 gate below).

    `logger_agent` is currently consumed only by the BitUnix broker, for the
    REST retry-layer audit (`rest_request_retried`); other adapters ignore it.
    """
    from trading_corp.brokers.coinbase import CoinbaseBroker

    family = division.broker
    # E2·4 — slug-level anti-half-flip. A division arms LIVE iff BOTH hold:
    #   (1) its FAMILY is live-capable: mode == LIVE and --brokers lists the family;
    #   (2) its SLUG is explicitly opted in via --live-divisions.
    # The family check ALONE is NOT sufficient — without the slug a division stays
    # PAPER even under `--brokers <family>`. This prevents a whole family flipping
    # live (e.g. `--brokers polymarket` would otherwise arm the arb division when
    # only polymarket_copy_trading should go live). Empty/absent live_divisions ⇒
    # nothing arms live. `is_live_division` is the SOLE live-vs-paper gate below.
    family_live_capable = (mode == "LIVE" and family in (live_brokers or []))
    is_live_division = (
        family_live_capable and division.slug in (live_divisions or set())
    )

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
        if is_live_division:
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
        if is_live_division:
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
        if is_live_division:
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
            logger=logger_agent,
        )
        if is_live_division:
            return bx
        paper = PaperBroker(account=f"paper_{division.slug}", starting_equity=0.0)
        return PaperExecutionBroker(bx, paper)

    if family == "polymarket":
        # PolymarketBroker (read-only, ReadOnlyBroker) for PAPER/non-selected;
        # PolymarketLiveBroker (Broker, placement-legal) when LIVE + selected
        # (--brokers polymarket). Per-division wallet (item 6): resolve the EOA
        # by slug (RPC shared; unmapped/partial wallet → None creds → stub).
        #
        # ANTI-HALF-FLIP (E1·6): the live branch is REQUIRED. Without it a
        # LIVE+selected polymarket division would silently resolve the READ-ONLY
        # adapter and never place — the Bitunix-half-flip failure mode. PCT goes
        # live via divisions.yaml `broker: paper→polymarket` + mode LIVE +
        # `--brokers polymarket`. No PaperExecutionBroker wrap on the read-only
        # path (no order surface to simulate); the live broker places for real.
        wallet = secrets.polymarket_wallets.get(division.slug)
        if wallet is None:
            log.info(
                "Polymarket division %s has no mapped wallet — broker will stub",
                division.slug,
            )
        pk = wallet.private_key if wallet else None
        funder = wallet.funder_address if wallet else None
        if is_live_division:
            from trading_corp.brokers.polymarket_live import PolymarketLiveBroker
            # E5a — execution discipline sourced from THIS division's config
            # (config/divisions.yaml). Omit each kwarg when unset so the broker
            # ctor default applies → an unconfigured division is byte-identical to
            # pre-E5a. exit_chase forwards the same way once E5b adds the ctor param.
            exec_kwargs = {}
            _ot = getattr(division, "order_type", None)
            if _ot is not None:
                exec_kwargs["order_type"] = _ot
            _fps = getattr(division, "fak_poll_seconds", None)
            if _fps is not None:
                exec_kwargs["fak_poll_seconds"] = _fps
            return PolymarketLiveBroker(
                private_key=pk, funder_address=funder,
                polygon_rpc_url=secrets.polygon_rpc_url, **exec_kwargs,
            )
        from trading_corp.brokers.polymarket import PolymarketBroker
        return PolymarketBroker(
            private_key=pk, funder_address=funder,
            polygon_rpc_url=secrets.polygon_rpc_url,
        )

    if family == "kalshi":
        # Phase K1 read-only Kalshi adapter. KalshiBroker subclasses
        # ReadOnlyBroker (same pattern as Polymarket) — no place_order on
        # the type. Live order placement is Phase K5+ work, gated on
        # observed paper PnL > 0 across Phase K2 arb + Phase K3 copy
        # trading. Demo mode toggle via KALSHI_USE_DEMO=1 env var (defaults
        # to production / kalshi.com).
        from trading_corp.brokers.kalshi import KalshiBroker
        return KalshiBroker(
            api_key_id=secrets.kalshi_api_key_id,
            private_key_pem=secrets.kalshi_private_key_pem,
            demo=os.getenv("KALSHI_USE_DEMO", "").strip() in ("1", "true", "True"),
        )

    if family == "tastytrade":
        # Tasty Options division. Auth via the same OAuth refresh-token env
        # vars the TastytradeDataProvider uses (TASTYTRADE_PROVIDER_SECRET +
        # TASTYTRADE_REFRESH_TOKEN). The broker delegates get_option_greeks
        # to the globally-configured data provider so we don't open a second
        # dxFeed subscription per process. is_test routes Session to TT's
        # cert/sandbox endpoint — Phase-0 smoke is operator-run via
        # scripts/tasty_sandbox_smoke.py, not from this code path.
        from trading_corp.brokers.tastytrade import TastytradeBroker
        from trading_corp.utils.iv import _get_configured_provider
        if not (secrets.tastytrade_provider_secret and secrets.tastytrade_refresh_token):
            log.info(
                "Skipping division %s — no Tastytrade credentials "
                "(TASTYTRADE_PROVIDER_SECRET / TASTYTRADE_REFRESH_TOKEN)",
                division.slug,
            )
            return None
        try:
            tt = TastytradeBroker(
                provider_secret=secrets.tastytrade_provider_secret,
                refresh_token=secrets.tastytrade_refresh_token,
                account_filter=division.account_filter or None,
                is_test=False,
                data_provider=_get_configured_provider(),
            )
        except Exception as e:
            log.warning(
                "TastytradeBroker construction failed for %s: %s — "
                "division will use paper fallback",
                division.slug, e,
            )
            return None
        if is_live_division:
            return tt
        paper = PaperBroker(account=f"paper_{division.slug}", starting_equity=0.0)
        return PaperExecutionBroker(tt, paper)

    if family == "paper":
        return PaperBroker(
            account=f"paper_{division.slug}",
            starting_equity=division.paper_capital,
        )

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
    bitunix_observer: Any = None,
    bitunix_htf_provider: Any = None,
    ic_division: Any = None,
    ic_strategy: Any = None,
    ic_telegram_batcher: Any = None,
    pending_combo_registry: Any = None,
    tasty_division: Any = None,
    tasty_strategy: Any = None,
    tasty_telegram_batcher: Any = None,
    tasty_pending_combo_registry: Any = None,
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
        bitunix_observer=bitunix_observer,
        bitunix_htf_provider=bitunix_htf_provider,
        ic_division=ic_division,
        ic_strategy=ic_strategy,
        ic_telegram_batcher=ic_telegram_batcher,
        pending_combo_registry=pending_combo_registry,
        tasty_division=tasty_division,
        tasty_strategy=tasty_strategy,
        tasty_telegram_batcher=tasty_telegram_batcher,
        tasty_pending_combo_registry=tasty_pending_combo_registry,
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
            strategy_state = StrategyState.from_persistence(agent.name, db_url=logger_agent.db_url)

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
                #
                # Full LLM reasoning ride-along — preserved on the
                # would_have_placed row so future analysis (Backtester
                # post-mortem, fine-tuning data) has the model's
                # justification at the moment of the trade decision.
                # Don't truncate; sqlite handles the size.
                ext = order.extra or {}
                logger_agent.log_event(
                    agent.name, "would_have_placed",
                    {
                        **base_payload,
                        "qty": order.qty,  # post-resize
                        "implied_prob_at_entry": ext.get("implied_prob_at_entry"),
                        "llm_prob_estimate": ext.get("llm_prob_estimate"),
                        "llm_confidence": ext.get("llm_confidence"),
                        "llm_reasoning": ext.get("llm_reasoning"),
                        "key_unknowns": ext.get("key_unknowns"),
                        "outcome": ext.get("outcome"),
                        "market_question": ext.get("market_question"),
                        "condition_id": ext.get("condition_id"),
                        "resolves_at": ext.get("resolves_at"),
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


async def _scheduled_kalshi_arb_loop(
    agent,
    *,
    channel,
    logger_agent,
    data_exec,
    risk_agent,
    db_url: str,
) -> None:
    """Kalshi Tail-Price Arb scanner loop (Phase K2.1).

    Wakes every `poll_interval_sec` (default 300s; from strategies.yaml).
    On each tick:
      - If `enabled: false`, no-op and sleep.
      - Otherwise call `agent.run_scan_cycle(broker)` which refreshes the
        category-targeted discovery cache (every cache_ttl_sec), walks
        non-COLLECTION events, and emits ProposedOrder PAIRS (BUY YES +
        BUY NO sharing `kalshi_pair_id`) for tail-price arbs above the
        per-pair edge threshold.
      - Each leg runs through `risk_agent.evaluate()` directly (no
        per-trade HITL — same Board direction as polymarket). Approved
        legs log `would_have_placed` (paper); rejected legs log
        `kalshi_order_rejected_by_risk`.

    Phase K5+ will branch on `auto_execute` to route approved orders
    through a real KalshiLiveBroker (place_order via pykalshi). Until
    then, every pair is paper-only.
    """
    from trading_corp.persistence.models import AccountState, StrategyState

    log.info(
        "Kalshi arbitrage scanner online (enabled=%s, auto_execute=%s, hitl=DIRECT)",
        agent.enabled, agent.auto_execute,
    )
    while True:
        try:
            poll_sec = float(agent._strat_cfg.get("poll_interval_sec", 300))
            await asyncio.sleep(max(30.0, poll_sec))

            if not agent.enabled:
                continue

            broker = data_exec.brokers.get(agent.division)
            if broker is None:
                log.debug(
                    "Kalshi scanner: no broker registered for division=%s; skipping",
                    agent.division,
                )
                continue

            try:
                orders = await agent.run_scan_cycle(
                    broker, logger_agent=logger_agent,
                )
            except Exception as e:
                log.exception("Kalshi scanner: run_scan_cycle failed: %s", e)
                continue

            if not orders:
                continue

            try:
                snap = await broker.snapshot()
                account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
            except Exception as e:
                log.warning("Kalshi scanner: snapshot failed: %s; assuming $0", e)
                account_equity = 0.0

            account = AccountState(
                account=agent.division,
                equity=account_equity,
                peak_equity=account_equity,
                halted=False,
            )
            strategy_state = StrategyState.from_persistence(agent.name, db_url=logger_agent.db_url)

            # Pairs are interleaved [yes_leg, no_leg, yes_leg, no_leg, ...].
            n_pairs = len(orders) // 2
            log.info(
                "Kalshi scanner: %d tail-arb pair(s) emitted (%d legs)",
                n_pairs, len(orders),
            )

            for order in orders:
                logger_agent.log_proposed_order(order)
                ext = order.extra or {}
                base_payload = {
                    "strategy": agent.name,
                    "division": agent.division,
                    "order_id": order.id,
                    "side": order.side,
                    "qty": order.qty,
                    "limit_price": order.limit_price,
                    "rationale": order.rationale,
                    "ticker": ext.get("ticker"),
                    "event_ticker": ext.get("event_ticker"),
                    "event_title": ext.get("event_title"),
                    "kalshi_pair_id": ext.get("kalshi_pair_id"),
                    "leg": ext.get("leg"),
                    "edge_cents": ext.get("edge_cents"),
                    "sum_asks": ext.get("sum_asks"),
                }

                verdict = risk_agent.evaluate(
                    order, account, strategy_state, db_url=db_url,
                )

                if verdict.verdict == "reject":
                    logger_agent.log_event(
                        agent.name, "kalshi_order_rejected_by_risk",
                        {**base_payload, "risk_reason": verdict.reason},
                    )
                    log.info("Kalshi: risk REJECT %s — %s", order.symbol, verdict.reason)
                    continue

                if verdict.verdict == "resize" and verdict.new_qty is not None:
                    log.info(
                        "Kalshi: risk RESIZE %s qty %.4f -> %.4f (%s)",
                        order.symbol, order.qty, verdict.new_qty, verdict.reason,
                    )
                    order.qty = float(verdict.new_qty)

                logger_agent.log_event(
                    agent.name, "would_have_placed",
                    {
                        **base_payload,
                        "qty": order.qty,
                        "yes_ask": ext.get("yes_ask"),
                        "no_ask": ext.get("no_ask"),
                        "edge_dollars": ext.get("edge_dollars"),
                        "max_dollar_risk": ext.get("max_dollar_risk"),
                        "expires_at": ext.get("expires_at"),
                        "tier": ext.get("tier"),
                        "risk_verdict": verdict.verdict,
                        "risk_reason": verdict.reason,
                    },
                )

            # Telegram ping per PAIR (not per leg) — slim per existing convention.
            seen_pairs: set[str] = set()
            for order in orders:
                pid = (order.extra or {}).get("kalshi_pair_id")
                if not pid or pid in seen_pairs:
                    continue
                seen_pairs.add(pid)
                try:
                    ext = order.extra or {}
                    await channel.push(
                        f"📊 Kalshi tail arb {ext.get('ticker')} "
                        f"(edge {ext.get('edge_cents')}¢, pair {pid}) "
                        f"— logged to activity rail."
                    )
                except Exception as e:
                    log.warning("Kalshi channel push failed: %s", e)

        except asyncio.CancelledError:
            log.info("Kalshi arbitrage scanner cancelled.")
            return
        except Exception as e:
            log.exception("Kalshi scanner loop error (continuing): %s", e)
            await asyncio.sleep(30)


async def _scheduled_kalshi_tb_arb_loop(
    agent,
    *,
    channel,
    logger_agent,
    data_exec,
    risk_agent,
    db_url: str,
) -> None:
    """Kalshi Temporal + Bucket Arb scanner loop (Phase K2.2).

    Same orchestration shape as `_scheduled_kalshi_arb_loop` — different
    detection logic. Emits ProposedOrder SETS (2 legs for temporal,
    N legs for bucket) sharing a `kalshi_arb_set_id`. Each leg flows
    through the risk gate independently.
    """
    from trading_corp.persistence.models import AccountState, StrategyState

    log.info(
        "Kalshi temporal+bucket arb scanner online (enabled=%s, auto_execute=%s, hitl=DIRECT)",
        agent.enabled, agent.auto_execute,
    )
    while True:
        try:
            poll_sec = float(agent._strat_cfg.get("poll_interval_sec", 300))
            await asyncio.sleep(max(30.0, poll_sec))

            if not agent.enabled:
                continue

            broker = data_exec.brokers.get(agent.division)
            if broker is None:
                log.debug(
                    "Kalshi TB scanner: no broker registered for division=%s; skipping",
                    agent.division,
                )
                continue

            try:
                orders = await agent.run_scan_cycle(
                    broker, logger_agent=logger_agent,
                )
            except Exception as e:
                log.exception("Kalshi TB scanner: run_scan_cycle failed: %s", e)
                continue

            if not orders:
                continue

            try:
                snap = await broker.snapshot()
                account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
            except Exception as e:
                log.warning("Kalshi TB scanner: snapshot failed: %s; assuming $0", e)
                account_equity = 0.0

            account = AccountState(
                account=agent.division,
                equity=account_equity,
                peak_equity=account_equity,
                halted=False,
            )
            strategy_state = StrategyState.from_persistence(agent.name, db_url=logger_agent.db_url)

            # Group legs by arb_set_id for cleaner audit + telegram.
            sets: dict[str, list] = {}
            for o in orders:
                sid = (o.extra or {}).get("kalshi_arb_set_id") or "unknown"
                sets.setdefault(sid, []).append(o)
            log.info(
                "Kalshi TB scanner: %d arb set(s), %d total legs",
                len(sets), len(orders),
            )

            for order in orders:
                logger_agent.log_proposed_order(order)
                ext = order.extra or {}
                base_payload = {
                    "strategy": agent.name,
                    "division": agent.division,
                    "order_id": order.id,
                    "side": order.side,
                    "qty": order.qty,
                    "limit_price": order.limit_price,
                    "rationale": order.rationale,
                    "ticker": ext.get("ticker"),
                    "event_ticker": ext.get("event_ticker"),
                    "event_title": ext.get("event_title"),
                    "kalshi_arb_set_id": ext.get("kalshi_arb_set_id"),
                    "kalshi_arb_type": ext.get("kalshi_arb_type"),
                    "leg": ext.get("leg"),
                    "edge_cents": ext.get("edge_cents"),
                }

                verdict = risk_agent.evaluate(
                    order, account, strategy_state, db_url=db_url,
                )

                if verdict.verdict == "reject":
                    logger_agent.log_event(
                        agent.name, "kalshi_tb_order_rejected_by_risk",
                        {**base_payload, "risk_reason": verdict.reason},
                    )
                    log.info("Kalshi TB: risk REJECT %s — %s", order.symbol, verdict.reason)
                    continue

                if verdict.verdict == "resize" and verdict.new_qty is not None:
                    log.info(
                        "Kalshi TB: risk RESIZE %s qty %.4f -> %.4f (%s)",
                        order.symbol, order.qty, verdict.new_qty, verdict.reason,
                    )
                    order.qty = float(verdict.new_qty)

                logger_agent.log_event(
                    agent.name, "would_have_placed",
                    {
                        **base_payload,
                        "qty": order.qty,
                        "edge_dollars": ext.get("edge_dollars"),
                        "max_dollar_risk": ext.get("max_dollar_risk"),
                        "tier": ext.get("tier"),
                        "leg_date": ext.get("leg_date"),
                        "sum_yes_asks": ext.get("sum_yes_asks"),
                        "risk_verdict": verdict.verdict,
                        "risk_reason": verdict.reason,
                    },
                )

            # Telegram: one ping per arb_set (not per leg).
            for sid, legs in sets.items():
                try:
                    first = legs[0].extra or {}
                    arb_type = first.get("kalshi_arb_type", "?")
                    edge = first.get("edge_cents", 0)
                    evt = first.get("event_ticker", "?")
                    await channel.push(
                        f"📊 Kalshi {arb_type} arb {evt} "
                        f"(edge {edge}c, {len(legs)} legs, set {sid}) "
                        f"— logged to activity rail."
                    )
                except Exception as e:
                    log.warning("Kalshi TB channel push failed: %s", e)

        except asyncio.CancelledError:
            log.info("Kalshi TB arbitrage scanner cancelled.")
            return
        except Exception as e:
            log.exception("Kalshi TB scanner loop error (continuing): %s", e)
            await asyncio.sleep(30)


async def _scheduled_kalshi_llm_arb_loop(
    agent,
    *,
    channel,
    logger_agent,
    data_exec,
    risk_agent,
    db_url: str,
) -> None:
    """Kalshi LLM Arbitrage scanner loop (Phase K6.1).

    Mirror of `_scheduled_polymarket_arb_loop` pointed at Kalshi.
    Wakes every `poll_interval_sec` (default 60s; from strategies.yaml).
    On each tick:
      - If `enabled: false`, no-op and sleep.
      - Otherwise call `agent.run_scan_cycle(broker)` which pulls Kalshi
        markets via discovery, deterministic-filters by prob bounds /
        cooldown / TTR, calls Anthropic per survivor (warm-and-fan), and
        emits `ProposedOrder`s on divergence ≥ min_divergence_pct.
      - Each ProposedOrder runs through `risk_agent.evaluate()` directly
        (no per-trade HITL — same Board direction as polymarket). Approved
        orders log `would_have_placed`; rejected log
        `kalshi_llm_order_rejected_by_risk`. Telegram ping per emit.

    Phase K7+ will branch on `auto_execute` to route approved orders
    through a real KalshiLiveBroker; today everything is paper.
    """
    from trading_corp.persistence.models import AccountState, StrategyState

    log.info(
        "Kalshi LLM arbitrage scanner online (enabled=%s, auto_execute=%s, hitl=DIRECT)",
        agent.enabled, agent.auto_execute,
    )
    while True:
        try:
            poll_sec = float(agent._strat_cfg.get("poll_interval_sec", 60))
            await asyncio.sleep(max(5.0, poll_sec))

            if not agent.enabled:
                continue

            broker = data_exec.brokers.get(agent.division)
            if broker is None:
                log.debug(
                    "Kalshi LLM scanner: no broker registered for division=%s; skipping",
                    agent.division,
                )
                continue

            try:
                orders = await agent.run_scan_cycle(
                    broker, logger_agent=logger_agent,
                )
            except Exception as e:
                log.exception("Kalshi LLM scanner: run_scan_cycle failed: %s", e)
                continue

            if not orders:
                continue

            try:
                snap = await broker.snapshot()
                account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
            except Exception as e:
                log.warning("Kalshi LLM scanner: snapshot failed: %s; assuming $0", e)
                account_equity = 0.0

            account = AccountState(
                account=agent.division,
                equity=account_equity,
                peak_equity=account_equity,
                halted=False,
            )
            strategy_state = StrategyState.from_persistence(agent.name, db_url=logger_agent.db_url)

            log.info(
                "Kalshi LLM scanner: %d divergence-based ProposedOrder(s) emitted",
                len(orders),
            )

            for order in orders:
                logger_agent.log_proposed_order(order)
                ext = order.extra or {}
                base_payload = {
                    "strategy": agent.name,
                    "division": agent.division,
                    "order_id": order.id,
                    "side": order.side,
                    "qty": order.qty,
                    "limit_price": order.limit_price,
                    "rationale": order.rationale,
                    "ticker": ext.get("ticker"),
                    "event_ticker": ext.get("event_ticker"),
                    "event_title": ext.get("event_title"),
                    "outcome": ext.get("outcome"),
                    "category": ext.get("category"),
                    "divergence_pct": ext.get("divergence_pct"),
                }

                verdict = risk_agent.evaluate(
                    order, account, strategy_state, db_url=db_url,
                )

                if verdict.verdict == "reject":
                    logger_agent.log_event(
                        agent.name, "kalshi_llm_order_rejected_by_risk",
                        {**base_payload, "risk_reason": verdict.reason},
                    )
                    log.info("Kalshi LLM: risk REJECT %s — %s", order.symbol, verdict.reason)
                    continue

                if verdict.verdict == "resize" and verdict.new_qty is not None:
                    log.info(
                        "Kalshi LLM: risk RESIZE %s qty %.4f -> %.4f (%s)",
                        order.symbol, order.qty, verdict.new_qty, verdict.reason,
                    )
                    order.qty = float(verdict.new_qty)

                logger_agent.log_event(
                    agent.name, "would_have_placed",
                    {
                        **base_payload,
                        "qty": order.qty,
                        "implied_prob_at_entry": ext.get("implied_prob_at_entry"),
                        "llm_prob_estimate": ext.get("llm_prob_estimate"),
                        "llm_confidence": ext.get("llm_confidence"),
                        "llm_reasoning": ext.get("llm_reasoning"),
                        "key_unknowns": ext.get("key_unknowns"),
                        "subtitle": ext.get("subtitle"),
                        "expires_at": ext.get("expires_at"),
                        "risk_verdict": verdict.verdict,
                        "risk_reason": verdict.reason,
                    },
                )

                # Telegram per emit (slim per existing convention).
                try:
                    div_pct = float(ext.get("divergence_pct") or 0)
                    cat = ext.get("category") or "?"
                    await channel.push(
                        f"🤖 Kalshi LLM {order.side.upper()} {order.symbol} "
                        f"(category={cat}, divergence {div_pct:.1f}%) "
                        f"— logged to activity rail."
                    )
                except Exception as e:
                    log.warning("Kalshi LLM channel push failed: %s", e)

        except asyncio.CancelledError:
            log.info("Kalshi LLM arbitrage scanner cancelled.")
            return
        except Exception as e:
            log.exception("Kalshi LLM scanner loop error (continuing): %s", e)
            await asyncio.sleep(30)


async def _scheduled_kalshi_copy_trader_loop(
    agent,
    *,
    apify_token: str | None,
    channel,
    logger_agent,
    data_exec,
    risk_agent,
    db_url: str,
) -> None:
    """Kalshi Copy Trader scanner loop (Phase K3)."""
    from trading_corp.data.kalshi_apify_client import KalshiApifyClient
    from trading_corp.persistence.models import AccountState, StrategyState

    log.info(
        "Kalshi copy trader scanner online (enabled=%s, auto_execute=%s, hitl=DIRECT)",
        agent.enabled, agent.auto_execute,
    )
    async with KalshiApifyClient(apify_token) as apify_client:
        # Lazy-resolved + cached: the first broker exposing `get_market_trades`
        # (i.e. a real KalshiBroker from any of the kalshi_* divisions). This
        # is intentionally NOT the kalshi_copy_trading division's broker — that
        # one is a PaperBroker for paper-mode execution and has no public-trade
        # API access. The two roles are decoupled here.
        trade_tape_fetcher = None
        while True:
            try:
                poll_sec = float(agent._strat_cfg.get("poll_interval_sec", 300))
                await asyncio.sleep(max(60.0, poll_sec))

                if not agent.enabled:
                    continue

                if trade_tape_fetcher is None:
                    for div_name, br in data_exec.brokers.items():
                        if hasattr(br, "get_market_trades"):
                            trade_tape_fetcher = br
                            log.info(
                                "Kalshi copy trader: trade-tape source = %s broker",
                                div_name,
                            )
                            break

                broker = data_exec.brokers.get(agent.division)
                try:
                    orders = await agent.run_scan_cycle(
                        apify_client=apify_client,
                        trade_tape_fetcher=trade_tape_fetcher,
                        logger_agent=logger_agent,
                    )
                except Exception as e:
                    log.exception("Kalshi copy trader: run_scan_cycle failed: %s", e)
                    continue

                if not orders:
                    continue

                if broker is None:
                    account_equity = 0.0
                else:
                    try:
                        snap = await broker.snapshot()
                        account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
                    except Exception as e:
                        log.warning("Kalshi copy trader: snapshot failed: %s; assuming $0", e)
                        account_equity = 0.0

                account = AccountState(
                    account=agent.division, equity=account_equity,
                    peak_equity=account_equity, halted=False,
                )
                strategy_state = StrategyState.from_persistence(agent.name, db_url=logger_agent.db_url)

                log.info(
                    "Kalshi copy trader: %d copy ProposedOrder(s) emitted",
                    len(orders),
                )

                for order in orders:
                    logger_agent.log_proposed_order(order)
                    ext = order.extra or {}
                    base_payload = {
                        "strategy": agent.name,
                        "division": agent.division,
                        "order_id": order.id,
                        "side": order.side,
                        "qty": order.qty,
                        "limit_price": order.limit_price,
                        "rationale": order.rationale,
                        "ticker": ext.get("ticker"),
                        "outcome": ext.get("outcome"),
                        "is_entry": ext.get("is_entry"),
                        "whale_handle": ext.get("whale_handle"),
                        "whale_position_contracts": ext.get("whale_position_contracts"),
                        "whale_position_pnl": ext.get("whale_position_pnl"),
                        # Trade-tape-derived prices (added 2026-05-12). The
                        # resolver pairing pass joins entry's whale_entry_price
                        # with the matching exit's whale_exit_price to compute
                        # realized PnL when the whale closes BEFORE the market
                        # settles. Without these in the allowlist, audit drops
                        # them silently (memory: trading_corp_audit_payload_allowlist).
                        "whale_entry_price": ext.get("whale_entry_price"),
                        "whale_exit_price": ext.get("whale_exit_price"),
                        "copy_size_usd": ext.get("copy_size_usd"),
                        "side_detection_confidence": ext.get("side_detection_confidence"),
                        "first_seen_iso": ext.get("first_seen_iso"),
                    }

                    verdict = risk_agent.evaluate(
                        order, account, strategy_state, db_url=db_url,
                    )

                    if verdict.verdict == "reject":
                        logger_agent.log_event(
                            agent.name, "kalshi_copy_order_rejected_by_risk",
                            {**base_payload, "risk_reason": verdict.reason},
                        )
                        log.info(
                            "Kalshi copy: risk REJECT %s — %s",
                            order.symbol, verdict.reason,
                        )
                        continue

                    if verdict.verdict == "resize" and verdict.new_qty is not None:
                        log.info(
                            "Kalshi copy: risk RESIZE %s qty %.4f -> %.4f (%s)",
                            order.symbol, order.qty, verdict.new_qty, verdict.reason,
                        )
                        order.qty = float(verdict.new_qty)

                    logger_agent.log_event(
                        agent.name, "would_have_placed",
                        {
                            **base_payload,
                            "qty": order.qty,
                            "risk_verdict": verdict.verdict,
                            "risk_reason": verdict.reason,
                        },
                    )

                    try:
                        whale = ext.get("whale_handle") or "?"
                        action = "ENTRY" if ext.get("is_entry") else "EXIT"
                        await channel.push(
                            f"🐋 Kalshi copy {action} {order.side.upper()} "
                            f"{order.symbol} (@{whale}, ${order.qty:.2f}) — logged."
                        )
                    except Exception as e:
                        log.warning("Kalshi copy channel push failed: %s", e)

            except asyncio.CancelledError:
                log.info("Kalshi copy trader scanner cancelled.")
                return
            except Exception as e:
                log.exception("Kalshi copy trader loop error (continuing): %s", e)
                await asyncio.sleep(30)


async def _push_copy_card(channel, order, ext, *, tag: str) -> None:
    """Informational Telegram card for one PCT copy order. Never raises."""
    try:
        user = ext.get("whale_user_name") or (ext.get("whale_wallet") or "?")[:10]
        action = "ENTRY" if ext.get("is_entry") else "EXIT"
        title_short = (ext.get("market_title") or order.symbol)[:50]
        await channel.push(
            f"🟣 Polymarket copy {action} {order.side.upper()} "
            f"@{user} (${ext.get('copy_size_usdc', 0):.2f}) "
            f"on \"{title_short}\" — {tag}."
        )
    except Exception as e:
        log.warning("Polymarket copy channel push failed: %s", e)


async def _handle_copy_order_placement(
    *, agent, order, verdict, is_live_armed: bool,
    data_exec, logger_agent, channel, base_payload: dict,
) -> None:
    """E2·6 — place one PCT copy order, gated on live-arming.

    `is_live_armed` is E2·4's decision (the division's broker is placement-legal,
    i.e. a `Broker`/PolymarketLiveBroker — NOT `broker.paper`):

      * PAPER (not armed): log `would_have_placed` — UNCHANGED behavior.
      * LIVE-armed: route through `data_exec.place()` (which sets `execution_mode`
        and logs the fill + proposed_order, E2·5). A benign synthesized-FAK
        `NoFillInWindow` is SKIPPED — the optimistic position is discarded, a benign
        audit + log.info is written, and the loop CONTINUES to the next order (no
        alarm, no 30s sleep, batch not abandoned). A real fill writes the ACTUAL
        filled qty/price back into the position (entry only). Real placement
        failures (plain `OrderPlacementError` / anything else) PROPAGATE to the
        loop's loud handler — they are NOT swallowed here.
    """
    ext = order.extra or {}
    if not is_live_armed:
        # ── PAPER branch — unchanged ──
        logger_agent.log_event(
            agent.name, "would_have_placed",
            {
                **base_payload,
                "qty": order.qty,
                "risk_verdict": verdict.verdict,
                "risk_reason": verdict.reason,
            },
        )
        await _push_copy_card(channel, order, ext, tag="logged")
        return

    # ── LIVE-armed branch (mocked in tests; a real broker only when operator-armed) ──
    from trading_corp.brokers.polymarket_live import NoFillInWindow

    try:
        fill = await data_exec.place(order, division=agent.division)
    except NoFillInWindow as e:
        # Benign: the synthesized-FAK order did not fill in its window. NOT a
        # failure — drop the optimistically-recorded position and skip this order.
        if ext.get("is_entry"):
            agent.discard_entry(order)
        logger_agent.log_event(
            agent.name, "polymarket_copy_no_fill",
            {**base_payload, "qty": order.qty, "reason": str(e)},
        )
        log.info("Polymarket copy: benign no-fill on %s — skipped (%s)", order.symbol, e)
        return
    # Real fill (full or synthesized-FAK partial). `data_exec.place` already logged
    # proposed_order[status=filled] + the 'filled' audit + execution_mode='live'.
    if ext.get("is_entry"):
        agent.record_entry_fill(order, fill)
    await _push_copy_card(
        channel, order, ext,
        tag=f"PLACED LIVE @ ${float(getattr(fill, 'price', 0.0)):.3f} "
            f"x{float(getattr(fill, 'qty', 0.0)):g}",
    )


async def _scheduled_polymarket_copy_trader_loop(
    agent,
    *,
    channel,
    logger_agent,
    data_exec,
    risk_agent,
    db_url: str,
) -> None:
    """Polymarket Copy Trader scanner loop."""
    from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient
    from trading_corp.persistence.models import AccountState, StrategyState

    log.info(
        "Polymarket copy trader scanner online (enabled=%s, auto_execute=%s, hitl=DIRECT)",
        agent.enabled, agent.auto_execute,
    )
    async with PolymarketDataAPIClient() as data_api_client:
        while True:
            try:
                poll_sec = float(agent._strat_cfg.get("poll_interval_sec", 60))
                await asyncio.sleep(max(15.0, poll_sec))

                if not agent.enabled:
                    continue

                # Lazy-resolve a real PolymarketBroker for the resolution
                # check inside _emit_entry. agent.division is broker:paper;
                # polymarket_arbitrage owns the real PolymarketBroker.
                # Same lazy-resolve pattern as K3 uses for trade-tape.
                market_state_fetcher = None
                for div_name, br in data_exec.brokers.items():
                    if hasattr(br, 'get_market_resolution'):
                        market_state_fetcher = br
                        break

                try:
                    orders = await agent.run_scan_cycle(
                        data_api_client=data_api_client,
                        logger_agent=logger_agent,
                        market_state_fetcher=market_state_fetcher,
                    )
                except Exception as e:
                    log.exception(
                        "Polymarket copy trader: run_scan_cycle failed: %s", e,
                    )
                    continue

                if not orders:
                    continue

                broker = data_exec.brokers.get(agent.division)
                # E2·6 — live-armed iff E2·4 gave this division a PLACEMENT-LEGAL
                # broker (PolymarketLiveBroker is a `Broker`; the read-only paper
                # PolymarketBroker is a `ReadOnlyBroker`, NOT a `Broker`). This
                # REUSES E2·4's --live-divisions decision (the factory already ANDed
                # family + slug into the broker class) — it is NOT `broker.paper`
                # (the read-only adapter has paper=False yet cannot place).
                from trading_corp.brokers.base import Broker as _PlacementLegalBroker
                is_live_armed = isinstance(broker, _PlacementLegalBroker)
                if broker is None:
                    account_equity = 0.0
                else:
                    try:
                        snap = await broker.snapshot()
                        account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
                    except Exception as e:
                        log.warning(
                            "Polymarket copy trader: snapshot failed: %s; assuming $0", e,
                        )
                        account_equity = 0.0

                account = AccountState(
                    account=agent.division, equity=account_equity,
                    peak_equity=account_equity, halted=False,
                )
                strategy_state = StrategyState.from_persistence(agent.name, db_url=logger_agent.db_url)

                log.info(
                    "Polymarket copy trader: %d copy ProposedOrder(s) emitted",
                    len(orders),
                )

                for order in orders:
                    logger_agent.log_proposed_order(order)
                    ext = order.extra or {}
                    base_payload = {
                        "strategy": agent.name,
                        "division": agent.division,
                        "order_id": order.id,
                        "side": order.side,
                        "qty": order.qty,
                        "limit_price": order.limit_price,
                        "rationale": order.rationale,
                        "is_entry": ext.get("is_entry"),
                        "outcome": ext.get("outcome"),
                        "outcome_index": ext.get("outcome_index"),
                        "condition_id": ext.get("condition_id"),
                        "token_id": ext.get("token_id"),  # E2·1: propagate to audit
                        "whale_wallet": ext.get("whale_wallet"),
                        "whale_user_name": ext.get("whale_user_name"),
                        "whale_entry_price": ext.get("whale_entry_price"),
                        "whale_exit_price": ext.get("whale_exit_price"),
                        "whale_usdc_size": ext.get("whale_usdc_size"),
                        "whale_contracts": ext.get("whale_contracts"),
                        "copy_size_usdc": ext.get("copy_size_usdc"),
                        "first_seen_ts": ext.get("first_seen_ts"),
                        "entry_ts": ext.get("entry_ts"),
                        "exit_ts": ext.get("exit_ts"),
                        "market_title": ext.get("market_title"),
                        "market_slug": ext.get("market_slug"),
                        "event_slug": ext.get("event_slug"),
                    }

                    verdict = risk_agent.evaluate(
                        order, account, strategy_state, db_url=db_url,
                    )

                    if verdict.verdict == "reject":
                        logger_agent.log_event(
                            agent.name, "polymarket_copy_order_rejected_by_risk",
                            {**base_payload, "risk_reason": verdict.reason},
                        )
                        log.info(
                            "Polymarket copy: risk REJECT %s — %s",
                            order.symbol, verdict.reason,
                        )
                        continue

                    if verdict.verdict == "resize" and verdict.new_qty is not None:
                        log.info(
                            "Polymarket copy: risk RESIZE %s qty %.4f -> %.4f (%s)",
                            order.symbol, order.qty, verdict.new_qty, verdict.reason,
                        )
                        order.qty = float(verdict.new_qty)

                    # E2·6 — gated live placement vs paper would_have_placed.
                    await _handle_copy_order_placement(
                        agent=agent, order=order, verdict=verdict,
                        is_live_armed=is_live_armed, data_exec=data_exec,
                        logger_agent=logger_agent, channel=channel,
                        base_payload=base_payload,
                    )

            except asyncio.CancelledError:
                log.info("Polymarket copy trader scanner cancelled.")
                return
            except Exception as e:
                log.exception(
                    "Polymarket copy trader loop error (continuing): %s", e,
                )
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




async def _scheduled_kalshi_weather_arb_loop(
    agent,
    *,
    channel,
    logger_agent,
    data_exec,
    risk_agent,
    db_url: str,
) -> None:
    """Kalshi Weather Arbitrage scanner loop.

    Pulls Climate/Weather markets, fetches NWS forecasts, emits orders
    when forecast diverges from implied. No LLM in path — pure math.

    Mirror of `_scheduled_kalshi_llm_arb_loop` but uses a forecast-based
    evaluator instead of the LLM. Risk gate identical (single chokepoint).
    """
    from trading_corp.persistence.models import AccountState, StrategyState

    log.info(
        "Kalshi Weather Arbitrage scanner online (enabled=%s, auto_execute=%s)",
        agent.enabled, agent.auto_execute,
    )
    # Lazy-resolve a real KalshiBroker for market discovery.
    while True:
        try:
            poll_sec = float(agent._strat_cfg.get("poll_interval_sec", 300))
            await asyncio.sleep(max(15.0, poll_sec))

            if not agent.enabled:
                continue

            kalshi_broker = None
            for div_name, br in data_exec.brokers.items():
                if br.__class__.__name__ == "KalshiBroker" and getattr(br, "_client", None):
                    kalshi_broker = br
                    break
            if kalshi_broker is None:
                log.debug("Kalshi Weather: no live KalshiBroker available; skipping")
                continue

            # Snapshot the division's paper broker BEFORE the scan so the
            # Kelly sizer has live equity to scale against.
            div_broker = data_exec.brokers.get(agent.division)
            account_equity = 0.0
            if div_broker is not None:
                try:
                    snap = await div_broker.snapshot()
                    account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
                except Exception as e:
                    log.warning("Kalshi Weather snapshot failed: %s; assuming $0", e)

            try:
                orders = await agent.run_scan_cycle(
                    kalshi_broker, logger_agent=logger_agent,
                    account_equity=account_equity,
                )
            except Exception as e:
                log.exception("Kalshi Weather: run_scan_cycle failed: %s", e)
                continue

            if not orders:
                continue

            account = AccountState(
                account=agent.division, equity=account_equity,
                peak_equity=account_equity, halted=False,
            )
            strategy_state = StrategyState.from_persistence(agent.name, db_url=logger_agent.db_url)

            log.info("Kalshi Weather: %d ProposedOrder(s) emitted", len(orders))
            for order in orders:
                logger_agent.log_proposed_order(order)
                ext = order.extra or {}
                base_payload = {
                    "strategy": agent.name,
                    "division": agent.division,
                    "order_id": order.id,
                    "side": order.side,
                    "qty": order.qty,
                    "limit_price": order.limit_price,
                    "rationale": order.rationale,
                    "ticker": ext.get("ticker"),
                    "event_ticker": ext.get("event_ticker"),
                    "event_title": ext.get("event_title"),
                    "outcome": ext.get("outcome"),
                    "category": ext.get("category"),
                    "divergence_pct": ext.get("divergence_pct"),
                }

                verdict = risk_agent.evaluate(
                    order, account, strategy_state, db_url=db_url,
                )
                if verdict.verdict == "reject":
                    logger_agent.log_event(
                        agent.name, "kalshi_weather_order_rejected_by_risk",
                        {**base_payload, "risk_reason": verdict.reason},
                    )
                    log.info("Kalshi Weather: risk REJECT %s — %s",
                             order.symbol, verdict.reason)
                    continue
                if verdict.verdict == "resize" and verdict.new_qty is not None:
                    log.info("Kalshi Weather: risk RESIZE qty %.4f -> %.4f (%s)",
                             order.qty, verdict.new_qty, verdict.reason)
                    order.qty = float(verdict.new_qty)

                logger_agent.log_event(
                    agent.name, "would_have_placed",
                    {
                        **base_payload,
                        "qty": order.qty,
                        "implied_prob_at_entry": ext.get("implied_prob_at_entry"),
                        "forecast_temp_f": ext.get("forecast_temp_f"),
                        "forecast_sigma_f": ext.get("forecast_sigma_f"),
                        "sigma_used_f": ext.get("sigma_used_f"),
                        "sigma_source": ext.get("sigma_source"),
                        "ensemble_n_members": ext.get("ensemble_n_members"),
                        "ensemble_std_f": ext.get("ensemble_std_f"),
                        "nowcast_blend_w": ext.get("nowcast_blend_w"),
                        "metar_station": ext.get("metar_station"),
                        "metar_latest_temp_f": ext.get("metar_latest_temp_f"),
                        "metar_extrap_f": ext.get("metar_extrap_f"),
                        "threshold_f": ext.get("threshold_f"),
                        "threshold_high_f": ext.get("threshold_high_f"),
                        "direction": ext.get("direction"),
                        "horizon_hours": ext.get("horizon_hours"),
                        "delta_f": ext.get("delta_f"),
                        "prob_yes": ext.get("prob_yes"),
                        "expires_at": ext.get("expires_at"),
                        # TARGET_ISO_INSERTED — resolution-date of the
                        # weather target parsed from ticker (distinct
                        # from expires_at, which is Kalshi's settlement
                        # window the day after). Audit-only.
                        "target_iso": ext.get("target_iso"),
                        "title": ext.get("title"),
                        "max_dollar_risk": ext.get("max_dollar_risk"),
                        "kelly_fraction_used": ext.get("kelly_fraction_used"),
                        "kelly_full_pct": ext.get("kelly_full_pct"),
                        "applied_cap": ext.get("applied_cap"),
                        "account_equity_at_size": ext.get("account_equity_at_size"),
                        # Bucket-aware bet-side guard (2026-05-16):
                        # records when the strategy flipped no→yes or
                        # blocked a smearing-artifact yes. None for
                        # markets that didn't trigger the guard.
                        "bucket_guard": ext.get("bucket_guard"),
                        "risk_verdict": verdict.verdict,
                        "risk_reason": verdict.reason,
                    },
                )
                try:
                    div_pct = float(ext.get("divergence_pct") or 0)
                    await channel.push(
                        f"☀️ Kalshi Weather {order.side.upper()} {order.symbol} "
                        f"(forecast {ext.get('forecast_temp_f','?')}°F vs "
                        f"threshold {ext.get('threshold_f','?')}°F, "
                        f"edge {div_pct:.1f}%) — logged to activity rail."
                    )
                except Exception as e:
                    log.warning("Kalshi Weather channel push failed: %s", e)

        except asyncio.CancelledError:
            log.info("Kalshi Weather Arbitrage scanner cancelled.")
            return
        except Exception as e:
            log.exception("Kalshi Weather loop iteration failed: %s", e)
            await asyncio.sleep(5.0)




async def _scheduled_kalshi_crypto_arb_loop(
    agent,
    *,
    channel,
    logger_agent,
    data_exec,
    risk_agent,
    db_url: str,
) -> None:
    """Kalshi Crypto Arbitrage scanner loop.

    Pulls Crypto-category markets, fetches Coinbase spot for the asset,
    computes P(YES) vs threshold via Gaussian vol. No LLM in path.
    """
    from trading_corp.persistence.models import AccountState, StrategyState

    log.info(
        "Kalshi Crypto Arbitrage scanner online (enabled=%s, auto_execute=%s)",
        agent.enabled, agent.auto_execute,
    )
    while True:
        try:
            poll_sec = float(agent._strat_cfg.get("poll_interval_sec", 60))
            await asyncio.sleep(max(15.0, poll_sec))

            if not agent.enabled:
                continue

            # Lazy-resolve real KalshiBroker + Coinbase quote source.
            # KalshiBroker lives under kalshi_arbitrage / kalshi_llm_arbitrage
            # divisions (paper=False), identified by class name + _client.
            # Coinbase in paper mode is wrapped in PaperExecutionBroker, so
            # look it up by division key — the wrapper proxies quote()
            # through to the underlying live CoinbaseBroker.
            kalshi_broker = None
            for br in data_exec.brokers.values():
                if br.__class__.__name__ == "KalshiBroker" and getattr(br, "_client", None):
                    kalshi_broker = br
                    break
            coinbase_broker = data_exec.brokers.get("coinbase_spot")
            if kalshi_broker is None or coinbase_broker is None:
                log.info(
                    "Kalshi Crypto: missing broker (kalshi=%s coinbase=%s); skipping",
                    bool(kalshi_broker), bool(coinbase_broker),
                )
                continue

            try:
                orders = await agent.run_scan_cycle(
                    kalshi_broker, coinbase_broker,
                    logger_agent=logger_agent,
                )
            except Exception as e:
                log.exception("Kalshi Crypto: run_scan_cycle failed: %s", e)
                continue

            if not orders:
                continue

            div_broker = data_exec.brokers.get(agent.division)
            account_equity = 0.0
            if div_broker is not None:
                try:
                    snap = await div_broker.snapshot()
                    account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
                except Exception as e:
                    log.warning("Kalshi Crypto snapshot failed: %s; assuming $0", e)

            account = AccountState(
                account=agent.division, equity=account_equity,
                peak_equity=account_equity, halted=False,
            )
            strategy_state = StrategyState.from_persistence(agent.name, db_url=logger_agent.db_url)

            log.info("Kalshi Crypto: %d ProposedOrder(s) emitted", len(orders))
            for order in orders:
                logger_agent.log_proposed_order(order)
                ext = order.extra or {}
                base_payload = {
                    "strategy": agent.name,
                    "division": agent.division,
                    "order_id": order.id,
                    "side": order.side,
                    "qty": order.qty,
                    "limit_price": order.limit_price,
                    "rationale": order.rationale,
                    "ticker": ext.get("ticker"),
                    "event_ticker": ext.get("event_ticker"),
                    "event_title": ext.get("event_title"),
                    "outcome": ext.get("outcome"),
                    "category": ext.get("category"),
                    "divergence_pct": ext.get("divergence_pct"),
                }

                verdict = risk_agent.evaluate(
                    order, account, strategy_state, db_url=db_url,
                )
                if verdict.verdict == "reject":
                    logger_agent.log_event(
                        agent.name, "kalshi_crypto_order_rejected_by_risk",
                        {**base_payload, "risk_reason": verdict.reason},
                    )
                    log.info("Kalshi Crypto: risk REJECT %s — %s",
                             order.symbol, verdict.reason)
                    continue
                if verdict.verdict == "resize" and verdict.new_qty is not None:
                    log.info("Kalshi Crypto: risk RESIZE qty %.4f -> %.4f (%s)",
                             order.qty, verdict.new_qty, verdict.reason)
                    order.qty = float(verdict.new_qty)

                logger_agent.log_event(
                    agent.name, "would_have_placed",
                    {
                        **base_payload,
                        "qty": order.qty,
                        "implied_prob_at_entry": ext.get("implied_prob_at_entry"),
                        "asset": ext.get("asset"),
                        "spot_price": ext.get("spot_price"),
                        "spot_sigma_usd": ext.get("spot_sigma_usd"),
                        "sigma_used_usd": ext.get("sigma_used_usd"),
                        "annual_vol": ext.get("annual_vol"),
                        "threshold_usd": ext.get("threshold_usd"),
                        "threshold_high_usd": ext.get("threshold_high_usd"),
                        "direction": ext.get("direction"),
                        "horizon_hours": ext.get("horizon_hours"),
                        "delta_usd": ext.get("delta_usd"),
                        "prob_yes": ext.get("prob_yes"),
                        "expires_at": ext.get("expires_at"),
                        "title": ext.get("title"),
                        # Bucket-aware bet-side guard outcome (2026-05-16;
                        # shared with kalshi_weather).
                        "bucket_guard": ext.get("bucket_guard"),
                        # Vol-v2 drift watch (2026-05-20, paper). Carry the
                        # hardcoded-vol mirror + per-fire classification so
                        # forward paper data can bucket new_fire vs same_fire
                        # outcomes without reconstructing from bars.
                        "hardcoded_av": ext.get("hardcoded_av"),
                        "hardcoded_prob_yes": ext.get("hardcoded_prob_yes"),
                        "hardcoded_edge_pct": ext.get("hardcoded_edge_pct"),
                        "vol_v2_classification": ext.get("vol_v2_classification"),
                        "risk_verdict": verdict.verdict,
                        "risk_reason": verdict.reason,
                    },
                )
                try:
                    div_pct = float(ext.get("divergence_pct") or 0)
                    await channel.push(
                        f"🪙 Kalshi Crypto {order.side.upper()} {order.symbol} "
                        f"(spot ${ext.get('spot_price','?')} vs threshold "
                        f"${ext.get('threshold_usd','?')}, edge {div_pct:.1f}%) "
                        f"— logged to activity rail."
                    )
                except Exception as e:
                    log.warning("Kalshi Crypto channel push failed: %s", e)

        except asyncio.CancelledError:
            log.info("Kalshi Crypto Arbitrage scanner cancelled.")
            return
        except Exception as e:
            log.exception("Kalshi Crypto loop iteration failed: %s", e)
            await asyncio.sleep(5.0)




async def _scheduled_kalshi_sports_scout_loop(
    agent,
    *,
    logger_agent,
    data_exec,
) -> None:
    """Kalshi Sports Scout loop. NO order emission.

    Each cycle: discover Kalshi Sports markets → map to bookmaker games
    via team-code lookup → fetch the-odds-api lines → log divergence.
    The agent owns its OddsAPIClient (closed on cancellation).
    """
    log.info(
        "Kalshi Sports Scout online (enabled=%s, has_credentials=%s)",
        agent.enabled, agent.has_credentials,
    )
    try:
        while True:
            try:
                poll_sec = float(agent._strat_cfg.get("poll_interval_sec", 900))
                await asyncio.sleep(max(30.0, poll_sec))

                if not agent.enabled:
                    continue

                kalshi_broker = None
                for div_name, br in data_exec.brokers.items():
                    if br.__class__.__name__ == "KalshiBroker" and getattr(br, "_client", None):
                        kalshi_broker = br
                        break
                if kalshi_broker is None:
                    log.debug("Sports Scout: no live KalshiBroker available; skipping")
                    continue

                try:
                    await agent.run_scan_cycle(
                        kalshi_broker, logger_agent=logger_agent,
                    )
                except Exception as e:
                    log.exception("Sports Scout: run_scan_cycle failed: %s", e)
                    continue

            except asyncio.CancelledError:
                log.info("Kalshi Sports Scout cancelled.")
                return
            except Exception as e:
                log.exception("Sports Scout loop iteration failed: %s", e)
                await asyncio.sleep(5.0)
    finally:
        try:
            await agent.close()
        except Exception:
            pass


async def _scheduled_kalshi_sports_arb_observer_loop(
    agent,
    *,
    logger_agent,
    data_exec,
) -> None:
    """Kalshi Sports Arbitrage observer loop. NO order emission.

    Sibling of the scout loop above. Writes
    `kalshi_sports_arb_observation` audit with raw Kalshi quotes +
    per-book sportsbook prices + EV-at-fill (Hypotheses A + B) at the
    configured qty. Phase 0 instrument; observer-only.
    """
    log.info(
        "Kalshi Sports Arb Observer online (enabled=%s, has_credentials=%s)",
        agent.enabled, agent.has_credentials,
    )
    try:
        while True:
            try:
                poll_sec = float(agent._strat_cfg.get("poll_interval_sec", 3600))
                await asyncio.sleep(max(30.0, poll_sec))

                if not agent.enabled:
                    continue

                kalshi_broker = None
                for div_name, br in data_exec.brokers.items():
                    if br.__class__.__name__ == "KalshiBroker" and getattr(br, "_client", None):
                        kalshi_broker = br
                        break
                if kalshi_broker is None:
                    log.debug("Sports Arb Observer: no live KalshiBroker; skipping")
                    continue

                try:
                    await agent.run_scan_cycle(
                        kalshi_broker, logger_agent=logger_agent,
                    )
                except Exception as e:
                    log.exception("Sports Arb Observer: run_scan_cycle failed: %s", e)
                    continue

            except asyncio.CancelledError:
                log.info("Kalshi Sports Arb Observer cancelled.")
                return
            except Exception as e:
                log.exception("Sports Arb Observer loop iteration failed: %s", e)
                await asyncio.sleep(5.0)
    finally:
        try:
            await agent.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
