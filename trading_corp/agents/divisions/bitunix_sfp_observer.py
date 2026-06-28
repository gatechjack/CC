"""Bitunix SFP division — engine-side signal loop + slim placement.

Holds, per traded symbol, a REAL and a CONSIDERABLE :class:`SfpDetector`
(``bitunix_sfp.py``) plus a 15m :class:`LiveBarCache`. A single SEQUENTIAL loop,
aligned to the 15m bar close, walks the configured ``symbols`` list, feeds each
symbol's freshly-closed bars to its detectors, and on a BOS-confirmed signal
builds a :class:`ProposedOrder`, runs the MANDATORY risk gate
(``RiskAgent.evaluate`` — the single chokepoint, CLAUDE.md #1), and places via a
slim writer that reuses the shared execution machinery: ``data_exec.place(
division="bitunix_sfp")`` places the entry + the atomic B1 server-side stop, and
``_place`` THEN rests a SINGLE full-qty native ``/tpsl/`` reduce-only TP leg
post-fill (OCO with the B1 stop) so a winning trade actually closes at 2R on the
venue — not just on the stop. ``place_order`` itself attaches ONLY the B1 stop;
the TP is a separate post-fill ``place_tpsl_order`` call (fail-soft + LOUD: any
TP-place failure leaves the filled entry + B1 stop intact and alerts, never
silent, never unwinds the entry). Live entries write a Path-C
``paper_trade_record`` row tagged ``execution_mode="live"`` + ``broker_order_id``
so the existing per-account reconciler / auto-book / ref-vs-fill track and book
the position UNCHANGED.

This is a NEW signal + division wired into proven execution — NOT new execution
logic. It deliberately does NOT reuse the 3500-line confluence observer (a
prod-surface md5-manifest file that hardcodes ``bitunix_futures``); the slim
placement writer below is the narrow slice it actually needs.

Symbol-agnostic by construction: the loop iterates the YAML ``symbols`` list
(BTC-only for now); nothing here hardcodes a coin. The per-(symbol,side)
concurrent-position guard prevents the division stacking a second same-side
position on any symbol.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trading_corp.persistence import db
from trading_corp.persistence.models import (
    AccountState,
    PaperTradeRecord,
    ProposedOrder,
    StrategyState,
)
from trading_corp.agents.strategies.bitunix_sfp import (
    MODE_CONSIDERABLE,
    MODE_REAL,
    SfpBar,
    SfpDetector,
    SfpEntrySignal,
    SfpModeBDetector,
    compute_geometry,
)
from trading_corp.brokers.bitunix_symbols import to_wire_format

log = logging.getLogger(__name__)

DIVISION = "bitunix_sfp"
PEAK_EQUITY_AGENT_STATE_KEY = "account_peak_equity"
LOOP_HEARTBEAT_AGENT_STATE_KEY = "loop_last_evaluated"

# ── sfp_watch_state — OBSERVE-ONLY dashboard Tier-B persistence ─────────────
# One logical row per armed watch, UPSERT'd through ARMED → terminal by a stable
# watch_id = f"{symbol}:{mode}:{fired_bar_ts_ms}". CREATE IF NOT EXISTS is run
# defensively at observer init (the gated migration also creates it).
_SFP_WATCH_DDL = (
    "CREATE TABLE IF NOT EXISTS sfp_watch_state ("
    " watch_id TEXT PRIMARY KEY,"
    " fired_bar_ts INTEGER NOT NULL,"
    " symbol TEXT NOT NULL,"
    " mode TEXT NOT NULL,"
    " swept_level REAL NOT NULL,"
    " swept_wick REAL NOT NULL,"
    " bos_watch_level REAL,"
    " status TEXT NOT NULL,"
    " status_ts TEXT NOT NULL,"
    " armed_ts TEXT NOT NULL,"
    " terminal_bar_ts INTEGER,"
    " extra_json TEXT)"
)
_SFP_WATCH_IX1 = ("CREATE INDEX IF NOT EXISTS ix_sfp_watch_state_status "
                  "ON sfp_watch_state(status, status_ts)")
_SFP_WATCH_IX2 = ("CREATE INDEX IF NOT EXISTS ix_sfp_watch_state_symbol "
                  "ON sfp_watch_state(symbol, status)")
# armed_ts is NOT in the UPDATE set — the original ARMED insert's value is kept.
_SFP_WATCH_UPSERT = (
    "INSERT INTO sfp_watch_state "
    "(watch_id, fired_bar_ts, symbol, mode, swept_level, swept_wick, bos_watch_level, "
    " status, status_ts, armed_ts, terminal_bar_ts, extra_json) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(watch_id) DO UPDATE SET "
    "  status=excluded.status, "
    "  status_ts=excluded.status_ts, "
    "  bos_watch_level=COALESCE(excluded.bos_watch_level, sfp_watch_state.bos_watch_level), "
    "  terminal_bar_ts=COALESCE(excluded.terminal_bar_ts, sfp_watch_state.terminal_bar_ts), "
    "  extra_json=COALESCE(excluded.extra_json, sfp_watch_state.extra_json)"
)
_WATCH_RECENT_WINDOW_SEC = 24 * 3600   # warm-start (b): only persist last 24h


def _watch_bars_from_hours(watch_hours: float, tf_minutes: int = 15) -> int:
    return int(watch_hours * 60 / tf_minutes)


@dataclass
class BitunixSfpConfig:
    """Parsed ``bitunix_sfp`` block from strategies.yaml. p6 ports
    (``pivot_len``/``back_to_break``/``stop_buffer_pct``/``tp_r``/``watch_hours``)
    must not drift without re-running the detector parity test."""
    enabled: bool = False
    auto_execute: bool = False
    execution_mode: str = "paper"           # "paper" | "live"
    division: str = DIVISION
    symbols: tuple[str, ...] = ("BTC/USDT.P",)
    detection_tf: str = "15m"
    pivot_len: int = 50
    back_to_break: int = 4
    stop_buffer_pct: float = 0.001
    tp_r: float = 2.0
    watch_hours: float = 12.0
    side: str = "long"
    risk_pct_real: float = 0.005
    risk_pct_considerable: float = 0.005
    leverage: float = 5.0
    max_hold_seconds: int = 604_800
    bar_cache_max_bars: int = 160
    loop_settle_seconds: int = 20
    # Mode B (15m SFP → 3m BOS). ``symbol_modes`` is OPTIONAL and BACKWARD-COMPAT:
    # when empty, every symbol uses (bos_tf=detection_tf, arm="trading") — today's
    # behavior exactly. Keyed by WIRE symbol → (bos_tf, arm) with
    # bos_tf ∈ {"15m","3m"}, arm ∈ {"trading","watch"} ("watch" runs the detector
    # but forces PAPER — a no-order forward-track).
    watch_hours_3m: float = 12.0
    symbol_modes: dict[str, tuple[str, str]] = field(default_factory=dict)

    def mode_for(self, wire: str) -> tuple[str, str]:
        """(bos_tf, arm) for a wire symbol; default (detection_tf, 'trading')."""
        return self.symbol_modes.get(wire, (self.detection_tf, "trading"))

    @property
    def uses_mode_b(self) -> bool:
        return any(bos_tf == "3m" for bos_tf, _arm in self.symbol_modes.values())

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "BitunixSfpConfig":
        raw = raw or {}
        syms = raw.get("symbols") or ["BTC/USDT.P"]
        if isinstance(syms, str):
            syms = [syms]
        detection_tf = str(raw.get("detection_tf", "15m"))
        # Parse the optional per-symbol mode map (wire-keyed, validated).
        symbol_modes: dict[str, tuple[str, str]] = {}
        for disp, spec in (raw.get("symbol_modes") or {}).items():
            spec = spec or {}
            wire = to_wire_format(str(disp))
            bos_tf = str(spec.get("bos_tf", detection_tf)).lower()
            arm = str(spec.get("arm", "trading")).lower()
            if bos_tf not in ("15m", "3m"):
                raise ValueError(
                    f"bitunix_sfp.symbol_modes[{disp}].bos_tf must be 15m|3m, got {bos_tf!r}")
            if arm not in ("trading", "watch"):
                raise ValueError(
                    f"bitunix_sfp.symbol_modes[{disp}].arm must be trading|watch, got {arm!r}")
            symbol_modes[wire] = (bos_tf, arm)
        return cls(
            enabled=bool(raw.get("enabled", False)),
            auto_execute=bool(raw.get("auto_execute", False)),
            execution_mode=str(raw.get("execution_mode", "paper")).lower(),
            division=str(raw.get("division", DIVISION)),
            symbols=tuple(str(s) for s in syms),
            detection_tf=detection_tf,
            pivot_len=int(raw.get("pivot_len", 50)),
            back_to_break=int(raw.get("back_to_break", 4)),
            stop_buffer_pct=float(raw.get("stop_buffer_pct", 0.001)),
            tp_r=float(raw.get("tp_r", 2.0)),
            watch_hours=float(raw.get("watch_hours", 12.0)),
            side=str(raw.get("side", "long")).lower(),
            risk_pct_real=float(raw.get("risk_pct_real", 0.005)),
            risk_pct_considerable=float(raw.get("risk_pct_considerable", 0.005)),
            leverage=float(raw.get("leverage", 5.0)),
            max_hold_seconds=int(raw.get("max_hold_seconds", 604_800)),
            bar_cache_max_bars=int(raw.get("bar_cache_max_bars", 160)),
            loop_settle_seconds=int(raw.get("loop_settle_seconds", 20)),
            watch_hours_3m=float(raw.get("watch_hours_3m", 12.0)),
            symbol_modes=symbol_modes,
        )


class BitunixSfpObserver:
    """Engine-side SFP signal generator + slim placement for ``bitunix_sfp``."""

    def __init__(
        self,
        *,
        db_url: str,
        risk_agent: Any,
        data_exec: Any,
        logger_agent: Any,
        config: BitunixSfpConfig,
        bar_caches: dict[str, Any],        # wire symbol -> LiveBarCache (15m)
        bar_caches_3m: dict[str, Any] | None = None,  # wire -> LiveBarCache (3m), Mode B
        strategies_yaml_path: str | None = None,
    ) -> None:
        self.db_url = db_url
        self.risk_agent = risk_agent
        self.data_exec = data_exec
        self.logger_agent = logger_agent
        self.config = config
        self.bar_caches = bar_caches
        self.bar_caches_3m = bar_caches_3m or {}
        self._strategies_yaml_path = strategies_yaml_path or str(
            Path(__file__).resolve().parents[3] / "config" / "strategies.yaml"
        )
        wb = _watch_bars_from_hours(config.watch_hours)
        wb3 = int(config.watch_hours_3m * 60 / 3)
        # Two detectors per symbol (REAL + CONSIDERABLE), pooled — mirrors the
        # oracle's two independent passes. Per symbol the BOS timeframe selects the
        # detector class: 15m → Mode-A SfpDetector (unchanged path); 3m → Mode-B
        # SfpModeBDetector. Backward-compat: an absent symbol_modes map leaves every
        # symbol on Mode-A, so _detectors/_last_ts and the existing loop are byte-
        # identical to today.
        self._detectors: dict[str, list[SfpDetector]] = {}
        self._detectors_b: dict[str, list[SfpModeBDetector]] = {}
        self._symbol_arm: dict[str, str] = {}
        self._symbol_bos_tf: dict[str, str] = {}
        self._last_ts: dict[str, int] = {}
        self._last_ts3: dict[str, int] = {}
        for sym in config.symbols:
            wire = to_wire_format(sym)
            bos_tf, arm = config.mode_for(wire)
            self._symbol_arm[wire] = arm
            self._symbol_bos_tf[wire] = bos_tf
            self._last_ts[wire] = 0
            if bos_tf == "3m":
                self._detectors_b[wire] = [
                    SfpModeBDetector(mode=MODE_REAL, pivot_len=config.pivot_len,
                                     back_to_break=config.back_to_break, watch_bars_3m=wb3),
                    SfpModeBDetector(mode=MODE_CONSIDERABLE, pivot_len=config.pivot_len,
                                     back_to_break=config.back_to_break, watch_bars_3m=wb3),
                ]
                self._last_ts3[wire] = 0
            else:
                self._detectors[wire] = [
                    SfpDetector(mode=MODE_REAL, pivot_len=config.pivot_len,
                                back_to_break=config.back_to_break, watch_bars=wb),
                    SfpDetector(mode=MODE_CONSIDERABLE, pivot_len=config.pivot_len,
                                back_to_break=config.back_to_break, watch_bars=wb),
                ]
        self.uses_mode_b = bool(self._detectors_b)
        # OBSERVE-ONLY: ensure the dashboard watch-state table exists (idempotent;
        # the gated migration also creates it). Fail-soft, never raises.
        self._ensure_watch_schema()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def warm_start_from_cache(self) -> None:
        """Rebuild detector state from each cache's bar history (restart-safe;
        the bars ARE the state). Signals produced during replay are discarded —
        they are historical, already past their entry bar."""
        for sym in self.config.symbols:
            wire = to_wire_format(sym)
            if self._symbol_bos_tf.get(wire) == "3m":
                self._warm_start_b(wire)
                continue
            cache = self.bar_caches.get(wire)
            if cache is None:
                continue
            bars = [self._to_sfp_bar(b) for b in getattr(cache, "bars", [])]
            for det in self._detectors[wire]:
                det.warm_start(bars)
                # OBSERVE-ONLY (warm-start decision b): persist only RECENT (24h)
                # transitions so a restart fills the dashboard without flooding
                # ancient backtest history. Idempotent UPSERT by watch_id.
                self._emit_watch_transitions(wire, det.drain_transitions(), recent_only=True)
            if bars:
                self._last_ts[wire] = bars[-1].ts_ms

    def _warm_start_b(self, wire: str) -> None:
        """Mode-B warm-start: replay ALL cached 15m bars (arm watches) then ALL
        cached 3m bars (bind + advance). The contiguity guard drops any 15m fire
        whose t0 3m bar predates the (shallower) 3m cache."""
        c15 = self.bar_caches.get(wire)
        c3 = self.bar_caches_3m.get(wire)
        bars15 = [self._to_sfp_bar(b) for b in getattr(c15, "bars", [])] if c15 else []
        bars3 = [self._to_sfp_bar(b) for b in getattr(c3, "bars", [])] if c3 else []
        for det in self._detectors_b[wire]:
            det.warm_start(bars15, bars3)
            self._emit_watch_transitions(wire, det.drain_transitions(), recent_only=True)
        if bars15:
            self._last_ts[wire] = bars15[-1].ts_ms
        if bars3:
            self._last_ts3[wire] = bars3[-1].ts_ms

    async def run_loop(self) -> None:
        """Sequential 15m-close-aligned loop. One pass per closed bar."""
        while True:
            try:
                await self._sleep_to_next_boundary()
                await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("bitunix_sfp loop tick failed (continuing)")

    async def _sleep_to_next_boundary(self, tf_seconds: int = 900) -> None:
        now = datetime.now(timezone.utc).timestamp()
        nxt = (int(now // tf_seconds) + 1) * tf_seconds + self.config.loop_settle_seconds
        await asyncio.sleep(max(1.0, nxt - now))

    async def process_once(self) -> None:
        """Refresh caches and feed any new closed bars to the detectors,
        SEQUENTIALLY across symbols (no parallel per-coin tasks — avoids racing
        the shared account-equity snapshot)."""
        for sym in self.config.symbols:
            wire = to_wire_format(sym)
            cache = self.bar_caches.get(wire)
            if cache is None:
                continue
            try:
                await cache.refresh()
            except Exception as e:
                log.warning("bitunix_sfp: %s cache refresh failed: %s", wire, e)
            await self._process_symbol(sym, wire, cache)
        # OBSERVE-ONLY heartbeat: one cheap agent_state write per loop cycle so the
        # dashboard can show an honest "loop last evaluated" age. Fail-soft.
        self._write_heartbeat()

    async def _process_symbol(self, symbol_display: str, wire: str, cache: Any) -> None:
        last = self._last_ts.get(wire, 0)
        new_bars = [b for b in getattr(cache, "bars", []) if int(b.ts_ms) > last]
        for raw in new_bars:
            bar = self._to_sfp_bar(raw)
            for det in self._detectors[wire]:
                sigs = det.on_closed_bar(bar)        # decision path UNCHANGED
                for sig in sigs:
                    await self._handle_signal(symbol_display, wire, sig, bar)
                # OBSERVE-ONLY: drain + persist lifecycle transitions. Fail-soft —
                # CANNOT raise into the loop; a persist failure never affects trading.
                self._emit_watch_transitions(wire, det.drain_transitions())
            self._last_ts[wire] = bar.ts_ms

    # ------------------------------------------------------------------ #
    # Mode B — ONE 3m-boundary master loop. Drives Mode-A symbols on their 15m
    # bars AND Mode-B symbols (15m arm + 3m BOS) in a SINGLE sequential task, so
    # two order-placement paths can never race the shared equity snapshot. The
    # existing run_loop / process_once / _process_symbol stay byte-unchanged and
    # are reused; main.py spawns run_loop_master iff any symbol is bos_tf=3m.
    # ------------------------------------------------------------------ #
    async def run_loop_master(self) -> None:
        """3m-aligned master loop. Mode-A symbols only act on NEW 15m bars (the
        _last_ts filter makes the extra ticks a no-op); Mode-B symbols arm on NEW
        15m bars and confirm on NEW 3m bars."""
        while True:
            try:
                await self._sleep_to_next_boundary(tf_seconds=180)
                await self.process_once_master()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("bitunix_sfp master loop tick failed (continuing)")

    async def process_once_master(self) -> None:
        for sym in self.config.symbols:
            wire = to_wire_format(sym)
            if self._symbol_bos_tf.get(wire) == "3m":
                c15 = self.bar_caches.get(wire)
                c3 = self.bar_caches_3m.get(wire)
                for c in (c15, c3):
                    if c is None:
                        continue
                    try:
                        await c.refresh()
                    except Exception as e:
                        log.warning("bitunix_sfp: %s 3m-path cache refresh failed: %s", wire, e)
                await self._process_symbol_b(sym, wire, c15, c3)
            else:
                cache = self.bar_caches.get(wire)
                if cache is None:
                    continue
                try:
                    await cache.refresh()
                except Exception as e:
                    log.warning("bitunix_sfp: %s cache refresh failed: %s", wire, e)
                await self._process_symbol(sym, wire, cache)
        self._write_heartbeat()

    async def _process_symbol_b(self, symbol_display: str, wire: str,
                                c15: Any, c3: Any) -> None:
        """Mode-B per-symbol pass: (1) feed NEW 15m bars to ARM watches, THEN
        (2) feed NEW 3m bars to ADVANCE/CONFIRM. Arm-before-advance matches the
        oracle and the parity test's interleaving. Entries fire only in step 2."""
        if c15 is not None:
            last15 = self._last_ts.get(wire, 0)
            for raw in [b for b in getattr(c15, "bars", []) if int(b.ts_ms) > last15]:
                bar15 = self._to_sfp_bar(raw)
                for det in self._detectors_b[wire]:
                    det.on_closed_15m_bar(bar15)        # arm only — returns []
                    self._emit_watch_transitions(wire, det.drain_transitions())
                self._last_ts[wire] = bar15.ts_ms
        if c3 is not None:
            last3 = self._last_ts3.get(wire, 0)
            for raw in [b for b in getattr(c3, "bars", []) if int(b.ts_ms) > last3]:
                bar3 = self._to_sfp_bar(raw)
                for det in self._detectors_b[wire]:
                    sigs = det.on_closed_3m_bar(bar3)
                    for sig in sigs:
                        await self._handle_signal(symbol_display, wire, sig, bar3)
                    self._emit_watch_transitions(wire, det.drain_transitions())
                self._last_ts3[wire] = bar3.ts_ms

    @staticmethod
    def _to_sfp_bar(b: Any) -> SfpBar:
        return SfpBar(ts_ms=int(b.ts_ms), open=float(b.open), high=float(b.high),
                      low=float(b.low), close=float(b.close))

    # ------------------------------------------------------------------ #
    # Signal → order → risk → place
    # ------------------------------------------------------------------ #
    async def _handle_signal(
        self, symbol_display: str, wire: str, sig: SfpEntrySignal, bar: SfpBar
    ) -> None:
        # Entry reference = the BOS bar close (best estimate of next-bar open;
        # the real fill is the live anchor — the reconciler's ref-vs-fill
        # captures it). Geometry from the swept wick low.
        entry_ref = bar.close
        geo = compute_geometry(
            entry_ref, sig.swept_low,
            stop_buffer_pct=self.config.stop_buffer_pct, tp_r=self.config.tp_r,
        )
        if geo is None:
            self._audit("sfp_skip_invalid_geometry", {
                "symbol": symbol_display, "sfp_mode": sig.sfp_mode,
                "entry_ref": entry_ref, "swept_low": sig.swept_low})
            return
        stop_price, tp_price, r_unit = geo

        broker = self.data_exec.brokers.get(DIVISION) if hasattr(self.data_exec, "brokers") else None
        if broker is None:
            self._audit("sfp_skip_no_broker", {"symbol": symbol_display})
            return
        try:
            snap = await broker.snapshot()
            equity = float(getattr(snap, "equity", 0.0) or 0.0)
        except Exception as e:
            # No phantom-equity fallback on a live path — SKIP and surface.
            self._audit("sfp_skip_no_equity", {"symbol": symbol_display, "error": str(e)})
            return
        if equity <= 0:
            self._audit("sfp_skip_no_equity", {"symbol": symbol_display, "equity": equity})
            return

        risk_pct = (self.config.risk_pct_real if sig.sfp_mode == MODE_REAL
                    else self.config.risk_pct_considerable)
        qty = (equity * risk_pct) / r_unit if r_unit > 0 else 0.0
        if qty <= 0:
            self._audit("sfp_skip_nonpositive_qty", {
                "symbol": symbol_display, "qty": qty, "equity": equity,
                "risk_pct": risk_pct, "r_unit": r_unit})
            return

        # Per-(symbol,side) concurrent-position guard (the symbol-aware D4).
        if self._has_open_live_same_side(symbol_display, "buy"):
            self._audit("sfp_concurrent_position_blocked", {
                "symbol": symbol_display, "side": "buy", "sfp_mode": sig.sfp_mode,
                "reason": "bot_own_same_side_position_open"})
            return

        max_dollar_risk = equity * risk_pct
        order = ProposedOrder(
            strategy=DIVISION,
            symbol=symbol_display,
            side="buy",
            qty=qty,
            order_type="market",
            rationale=(f"SFP-{sig.sfp_mode} BOS long {symbol_display}; "
                       f"swept={sig.swept_low:.4f} lvl={sig.swept_swing_level:.4f} "
                       f"stop={stop_price:.4f} tp2R={tp_price:.4f}"),
            extra={
                "stop_price": stop_price,           # B1 server-side stop reads this
                "take_profit_price": tp_price,
                "tp_r_multiple": self.config.tp_r,
                "rr_ratio": self.config.tp_r,
                "sfp_mode": sig.sfp_mode,
                "swept_low": sig.swept_low,
                "swept_swing_level": sig.swept_swing_level,
                "bos_ref_high": sig.bos_ref_high,
                "bos_bar_ts": sig.bos_bar_ts_ms,
                "entry_reference_price": entry_ref,
                "reduce_only": False,
                "leverage": self.config.leverage,
                "source_signal": (f"sfp_{sig.sfp_mode.lower()}"
                                  + ("_3m_bos" if getattr(sig, "bos_tf", "15m") == "3m" else "")),
                "bos_tf": getattr(sig, "bos_tf", "15m"),
                "max_dollar_risk": max_dollar_risk,
                "expected_gain_if_tp_hit": max_dollar_risk * self.config.tp_r,
            },
        )

        # ── MANDATORY risk gate (single chokepoint) ──
        try:
            account = AccountState(account=DIVISION, equity=equity,
                                   peak_equity=self._tracked_peak_equity(equity))
            strat_state = StrategyState.from_persistence(DIVISION, db_url=self.db_url)
            verdict = self.risk_agent.evaluate(order, account, strat_state, None, None,
                                               db_url=self.db_url)
        except Exception as e:
            self._audit("sfp_skip_risk_error", {"symbol": symbol_display, "error": str(e),
                                                "order_id": order.id})
            return
        # Drawdown-breach (flatten_account) → BLOCK the new entry. v1 does NOT
        # auto-flatten; the open position rides its B1 server-side stop.
        if getattr(verdict, "flatten_account", False):
            order.status = "risk_rejected"
            order.risk_reason = getattr(verdict, "reason", "flatten_account")
            self.logger_agent.log_proposed_order(order)
            self._audit("sfp_drawdown_breach_block", {
                "symbol": symbol_display, "order_id": order.id,
                "reason": order.risk_reason})
            return
        if verdict.verdict == "reject":
            order.status = "risk_rejected"
            order.risk_reason = verdict.reason
            self.logger_agent.log_proposed_order(order)
            self._audit("sfp_risk_rejected", {"symbol": symbol_display,
                                              "order_id": order.id, "reason": verdict.reason})
            return
        if verdict.verdict == "resize" and getattr(verdict, "new_qty", None) is not None:
            order.qty = float(verdict.new_qty)

        await self._place(order, symbol_display, sig)

    async def _place(self, order: ProposedOrder, symbol_display: str, sig: SfpEntrySignal) -> None:
        """Slim placement writer. Paper → would_have_placed (never touches the
        broker). Live → data_exec.place + Path-C live row.

        Live placement requires ALL of: execution_mode:live, the runtime
        auto_execute kill switch ON, AND the registered broker actually being
        live (``paper=False``). The last clause prevents a half-flip
        (execution_mode:live but the slug absent from ``--live-divisions`` → a
        PaperExecutionBroker) from writing a mislabeled-live record."""
        broker = self.data_exec.brokers.get(DIVISION) if hasattr(self.data_exec, "brokers") else None
        broker_is_live = broker is not None and not getattr(broker, "paper", True)
        cfg_live = (self.config.execution_mode == "live") and self._yaml_auto_execute()
        if cfg_live and not broker_is_live:
            log.warning("bitunix_sfp: execution_mode=live + auto_execute but the "
                        "broker is paper/missing (slug not in --live-divisions?) — "
                        "routing PAPER to avoid a mislabeled-live record")
        is_live = cfg_live and broker_is_live
        # Mode-B watch-only symbols forward-track in PAPER (never live) regardless
        # of execution_mode — the detector runs and writes a paper record, but no
        # real order is placed. Default arm is "trading" → a no-op for armed symbols.
        if is_live and self._symbol_arm.get(to_wire_format(symbol_display)) == "watch":
            is_live = False
            self._audit("sfp_signal_watch_only", {
                "symbol": symbol_display, "sfp_mode": sig.sfp_mode,
                "order_id": order.id, "reason": "arm=watch_forward_track_paper"})
        intent = {
            "symbol": symbol_display, "side": order.side, "qty": order.qty,
            "sfp_mode": sig.sfp_mode, "order_id": order.id,
            "stop_price": order.extra.get("stop_price"),
            "take_profit_price": order.extra.get("take_profit_price"),
        }
        if not is_live:
            order.status = "would_have_placed"
            self.logger_agent.log_proposed_order(order)
            self._audit("would_have_placed", intent)
            self._write_record(order, live=False, fill=None)
            return

        intent_live = dict(intent)
        intent_live["execution_mode"] = "live"
        self._audit("live_order_placed", intent_live)   # write-ahead intent
        self.logger_agent.log_proposed_order(order)
        try:
            fill = await self.data_exec.place(order, division=DIVISION)
        except Exception as e:
            order.status = "live_order_rejected"
            self._audit("live_order_rejected", {**intent_live, "error": str(e),
                                                "error_type": type(e).__name__})
            return
        self._write_record(order, live=True, fill=fill)
        # ── Post-fill TP placement ──────────────────────────────────────────
        # The entry + atomic B1 stop are now live. place_order does NOT submit a
        # TP, so rest the real /tpsl/ reduce-only TP leg here. Fail-soft + LOUD:
        # any failure leaves the entry + B1 stop intact (downside capped at the
        # structural stop) and alerts — NEVER silent, NEVER unwinds the entry.
        await self._place_tp_leg(order, symbol_display)

    def _write_record(self, order: ProposedOrder, *, live: bool, fill: Any) -> None:
        try:
            record = PaperTradeRecord.from_order(
                order, strategy=DIVISION, division=DIVISION,
                max_hold_seconds=self.config.max_hold_seconds,
            )
            record.extra = dict(order.extra)
            if live:
                record.extra["execution_mode"] = "live"
                if fill is not None:
                    record.extra["broker_order_id"] = getattr(fill, "order_id", None)
                    record.extra["entry_fee_usd"] = float(getattr(fill, "fee", 0.0) or 0.0)
            db.insert_paper_trade_record(record.to_db_row(), db_url=self.db_url)
        except Exception as e:
            log.warning("bitunix_sfp: paper_trade_record write failed "
                        "(live=%s; broker may have placed): %s", live, e)

    # ------------------------------------------------------------------ #
    # Post-fill TP leg (the SFP edge depends on the 2R TP firing live)
    # ------------------------------------------------------------------ #
    async def _place_tp_leg(self, order: ProposedOrder, symbol_display: str) -> None:
        """Rest ONE full-qty native /tpsl/ reduce-only TP leg after the live entry
        fills (OCO with the atomic B1 stop, which is UNCHANGED). Fail-soft + LOUD
        on EVERY failure path: leave the position SL-only (downside capped at the
        structural stop) and alert (audit + telegram) — never silent, and never
        unwind the filled entry. NO retry loop (place_tpsl_order is idempotency-
        aware at the venue + the concurrent-position guard blocks re-entry)."""
        try:
            broker = (self.data_exec.brokers.get(DIVISION)
                      if hasattr(self.data_exec, "brokers") else None)
            tp_price = float(order.extra.get("take_profit_price") or 0.0)
            if broker is None or not hasattr(broker, "place_tpsl_order") or tp_price <= 0.0:
                await self._tp_alert("sfp_tp_unsupported", order,
                    f"no /tpsl/ broker or tp_price={tp_price} — position is SL-only",
                    {"tp_price": tp_price})
                return

            position_id, pos_qty = await self._resolve_position(broker, order)
            if not position_id:
                await self._tp_alert("sfp_tp_unresolved_position", order,
                    "venue positionId unresolved after fill — SL-only (B1 guards)",
                    {"requested_qty": float(order.qty)})
                return

            # Single full-qty leg; reuse build_bracket_legs for the 0.0003 BTC
            # min-leg floor + the 0-legs->SL-only branch (single source of truth).
            from trading_corp.agents.divisions.bitunix_bracket import build_bracket_legs
            qty = float(pos_qty or order.qty)
            legs, note = build_bracket_legs(
                qty, [{"leg": "tp1", "price": tp_price, "fraction": 1.0}])
            if not legs:
                await self._tp_alert("sfp_tp_skipped_submin", order,
                    f"entry qty {qty} below min leg — SL-only ({note})",
                    {"qty": qty, "note": note})
                return
            leg = legs[0]

            from trading_corp.brokers.bitunix_exceptions import BitunixUntrackedTpslOrder
            try:
                tp_order_id = await broker.place_tpsl_order(
                    symbol=order.symbol, position_id=position_id,
                    tp_price=tp_price, tp_qty=leg.qty,
                )
            except BitunixUntrackedTpslOrder as e:
                await self._tp_alert("sfp_tp_untracked", order,
                    "TP leg POST reached the venue but its id was uncaptured — the "
                    "leg may be RESTING UNTRACKED; RECONCILE. B1 stop still guards.",
                    {"position_id": position_id, "tp_price": tp_price,
                     "tp_qty": leg.qty, "error": str(e), "error_type": type(e).__name__})
                return
            except Exception as e:
                await self._tp_alert("sfp_tp_place_failed", order,
                    f"TP leg placement FAILED — SL-only (B1 stop guards): {e}",
                    {"position_id": position_id, "tp_price": tp_price,
                     "tp_qty": leg.qty, "error": str(e), "error_type": type(e).__name__})
                return

            if not tp_order_id:
                # Empty id WITHOUT an exception = idempotent duplicate (leg already
                # resting from a prior attempt). Not a new order; don't double-count.
                log.warning("bitunix_sfp: TP leg returned no id (idempotent "
                            "duplicate / already resting) order_id=%s", order.id)

            self._persist_tp(order, position_id, tp_price, leg.qty, tp_order_id or "")
            self._audit("sfp_bracket_placed", {
                "order_id": order.id, "symbol": symbol_display,
                "tp_order_id": tp_order_id or "", "tp_price": tp_price,
                "tp_qty": leg.qty, "position_id": position_id, "degrade_note": note})
        except Exception as e:
            # Belt-and-suspenders: the TP path must NEVER crash the loop or unwind
            # the (already filled) entry. Any uncaught error -> loud alert, entry intact.
            try:
                await self._tp_alert("sfp_tp_unexpected_error", order,
                    f"unexpected error placing TP — SL-only (B1 guards): {e}",
                    {"error": str(e), "error_type": type(e).__name__})
            except Exception:
                log.exception("bitunix_sfp: TP placement AND its alert both failed "
                              "(entry intact, B1 guards) order_id=%s",
                              getattr(order, "id", None))

    async def _resolve_position(self, broker: Any, order: ProposedOrder
                                ) -> tuple[str | None, float | None]:
        """Match the just-opened venue position by wire-symbol + side; return
        (positionId, qty). (None, None) if unresolved — caller leaves SL-only."""
        try:
            order_wire = to_wire_format(order.symbol or "")
        except Exception:
            order_wire = (order.symbol or "").upper()
        entry_side = (order.side or "").lower()
        try:
            positions = await broker.get_pending_positions()
        except Exception as e:
            log.warning("bitunix_sfp: get_pending_positions failed: %s", e)
            return None, None
        for p in positions or []:
            try:
                p_wire = to_wire_format(getattr(p, "symbol", "") or "")
            except Exception:
                p_wire = (getattr(p, "symbol", "") or "").upper()
            p_side_raw = str((getattr(p, "extra", None) or {}).get("side", "")).upper()
            p_side = "buy" if p_side_raw in ("LONG", "BUY") else "sell"
            if p_wire == order_wire and p_side == entry_side:
                pid = (getattr(p, "extra", None) or {}).get("positionId")
                if pid:
                    return str(pid), abs(float(getattr(p, "qty", 0.0) or 0.0))
        return None, None

    def _persist_tp(self, order: ProposedOrder, position_id: str,
                    tp_price: float, tp_qty: float, tp_order_id: str) -> None:
        """Inline UPDATE of the entry row's extra_json with the bracket TP state
        (mirrors the futures observer's inline db.connect UPDATE; no db.py helper).
        The row already exists (written at entry) so this MUST be an UPDATE."""
        import json
        try:
            with db.connect(self.db_url) as conn:
                row = conn.execute(
                    "SELECT extra_json FROM paper_trade_record WHERE order_id=?",
                    (order.id,),
                ).fetchone()
                extra: dict = {}
                if row and row["extra_json"]:
                    try:
                        extra = json.loads(row["extra_json"])
                    except (TypeError, ValueError):
                        extra = {}
                extra["bracket_tp_order_id"] = tp_order_id
                extra["bracket_position_id"] = position_id
                extra["bracket_tp_qty"] = tp_qty
                extra["bracket_tp_price"] = tp_price
                conn.execute(
                    "UPDATE paper_trade_record SET extra_json=? WHERE order_id=?",
                    (json.dumps(extra, default=str), order.id),
                )
        except Exception as e:
            log.error("bitunix_sfp: TP bracket-state persist failed "
                      "(leg may be placed at venue): %s", e)

    async def _tp_alert(self, kind: str, order: ProposedOrder, text: str,
                        payload: dict) -> None:
        """LOUD, never-silent alert for a TP-placement issue: audit_event (dashboard)
        + log.error + best-effort Telegram via data_exec.safety_notifier (guarded,
        never raises). The filled entry + B1 stop are intact regardless."""
        full = {"order_id": getattr(order, "id", None),
                "symbol": getattr(order, "symbol", None), **(payload or {})}
        self._audit(kind, full)
        log.error("bitunix_sfp: %s — %s (order_id=%s)",
                  kind, text, getattr(order, "id", None))
        notifier = getattr(self.data_exec, "safety_notifier", None)
        if notifier is not None:
            try:
                await notifier.push(
                    f"⚠ SFP TP NOT PLACED ({kind})\n{text}\n"
                    f"order_id={getattr(order, 'id', None)}",
                    audit_path="bitunix_sfp", audit_context=full,
                )
            except Exception as e:
                log.warning("bitunix_sfp: safety_notifier.push raised: %s", e)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _has_open_live_same_side(self, symbol_display: str, side: str) -> bool:
        """True if this division already holds an OPEN live position on the same
        (symbol, side). Symbol-agnostic — keyed purely on the row's symbol+side."""
        try:
            with db.connect(self.db_url) as conn:
                rows = conn.execute(
                    "SELECT symbol, side, extra_json FROM paper_trade_record "
                    "WHERE division = ? AND result IS NULL AND extra_json IS NOT NULL",
                    (DIVISION,),
                ).fetchall()
        except Exception as e:
            # Fail SAFE: if we cannot verify, assume a position MAY exist and
            # block (never stack blind).
            log.warning("bitunix_sfp: concurrent-guard read failed: %s", e)
            return True
        import json
        for r in rows:
            try:
                extra = json.loads(r["extra_json"])
            except (TypeError, ValueError):
                continue
            if extra.get("execution_mode") != "live":
                continue
            if str(r["symbol"]) == symbol_display and str(r["side"]) == side:
                return True
        return False

    def _tracked_peak_equity(self, current_equity: float) -> float:
        """Account high-water-mark for the drawdown breaker (per-division key).
        FAIL-SAFE: read failure → current (peak==current ⇒ drawdown 0 ⇒ no false
        flatten), mirroring the futures observer's helper."""
        try:
            loaded = db.load_agent_state(DIVISION, PEAK_EQUITY_AGENT_STATE_KEY,
                                         db_url=self.db_url)
        except Exception as e:
            log.warning("bitunix_sfp: peak-equity read failed: %s", e)
            return current_equity
        stored_peak = 0.0
        if loaded is not None:
            value, _updated = loaded
            try:
                stored_peak = (float(value.get("peak", 0.0))
                               if isinstance(value, dict) else float(value))
            except (TypeError, ValueError):
                stored_peak = 0.0
        peak = max(stored_peak, current_equity)
        if peak > stored_peak:
            try:
                db.set_agent_state(DIVISION, PEAK_EQUITY_AGENT_STATE_KEY,
                                   {"peak": peak}, db_url=self.db_url)
            except Exception as e:
                log.warning("bitunix_sfp: peak-equity write failed %.2f→%.2f: %s",
                            stored_peak, peak, e)
        return peak

    def _yaml_auto_execute(self) -> bool:
        """Fresh-read ``bitunix_sfp.auto_execute`` (the runtime kill switch).
        Fail-CLOSED to False on any error."""
        try:
            import yaml
            with open(self._strategies_yaml_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            return bool((raw.get(DIVISION) or {}).get("auto_execute", False))
        except Exception as e:
            log.warning("bitunix_sfp: auto_execute read failed (fail-closed): %s", e)
            return False

    # ------------------------------------------------------------------ #
    # OBSERVE-ONLY emit: watch-state + heartbeat (dashboard Tier-B).
    # NONE of these read into or alter any trade decision; all fail-soft.
    # ------------------------------------------------------------------ #
    def _ensure_watch_schema(self) -> None:
        """Idempotently create sfp_watch_state (defensive; the gated migration
        also creates it). Fail-soft — a schema error must not break the observer."""
        try:
            with db.connect(self.db_url) as conn:
                conn.execute(_SFP_WATCH_DDL)
                conn.execute(_SFP_WATCH_IX1)
                conn.execute(_SFP_WATCH_IX2)
        except Exception as e:
            log.warning("bitunix_sfp: sfp_watch_state ensure-schema failed "
                        "(emit will no-op until present): %s", e)

    def _write_heartbeat(self) -> None:
        """One cheap agent_state write per loop cycle (honest dashboard heartbeat).
        Fail-soft — observe-only."""
        try:
            db.set_agent_state(DIVISION, LOOP_HEARTBEAT_AGENT_STATE_KEY,
                               {"ts": datetime.now(timezone.utc).isoformat()},
                               db_url=self.db_url)
        except Exception as e:
            log.warning("bitunix_sfp: heartbeat write failed (observe-only): %s", e)

    def _emit_watch_transitions(self, wire: str, transitions: list,
                                *, recent_only: bool = False) -> None:
        """OBSERVE-ONLY: UPSERT lifecycle transitions into sfp_watch_state, keyed by
        watch_id = f"{symbol}:{mode}:{fired_bar_ts_ms}". MUST NOT raise into the
        trading loop — any failure is logged and swallowed. ``recent_only``
        (warm-start, decision b) skips transitions whose arming bar is older than
        24h so a restart does not flood ancient history."""
        if not transitions:
            return
        try:
            import json
            now_iso = datetime.now(timezone.utc).isoformat()
            cutoff_ms = None
            if recent_only:
                cutoff_ms = int(
                    (datetime.now(timezone.utc).timestamp() - _WATCH_RECENT_WINDOW_SEC) * 1000)
            with db.connect(self.db_url) as conn:
                for t in transitions:
                    fired_ms = int(getattr(t, "fired_bar_ts_ms", 0) or 0)
                    if cutoff_ms is not None and fired_ms < cutoff_ms:
                        continue
                    watch_id = f"{wire}:{t.mode}:{fired_ms}"
                    is_armed = (t.status == "ARMED")
                    terminal_bar = None if is_armed else int(t.status_bar_ts_ms)
                    extra = None
                    if t.status == "CONFIRMED":
                        extra = json.dumps({"bos_ref_high": t.bos_ref_high,
                                            "entry_bar_index": t.entry_bar_index})
                    bos = (float(t.bos_watch_level)
                           if t.bos_watch_level is not None else None)
                    conn.execute(_SFP_WATCH_UPSERT, (
                        watch_id, fired_ms, wire, t.mode,
                        float(t.swept_level), float(t.swept_wick), bos,
                        t.status, now_iso, now_iso, terminal_bar, extra,
                    ))
        except Exception as e:
            log.warning("bitunix_sfp: watch-state emit failed "
                        "(observe-only, trading unaffected): %s", e)

    def _audit(self, kind: str, payload: dict) -> None:
        payload = {**payload, "strategy": DIVISION, "division": DIVISION}
        try:
            self.logger_agent.log_event(actor=DIVISION, kind=kind, payload=payload)
        except Exception as e:
            log.warning("bitunix_sfp: audit %s failed: %s", kind, e)
