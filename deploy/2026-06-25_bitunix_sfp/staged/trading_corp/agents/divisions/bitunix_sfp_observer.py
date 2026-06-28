"""Bitunix SFP division — engine-side signal loop + slim placement.

Holds, per traded symbol, a REAL and a CONSIDERABLE :class:`SfpDetector`
(``bitunix_sfp.py``) plus a 15m :class:`LiveBarCache`. A single SEQUENTIAL loop,
aligned to the 15m bar close, walks the configured ``symbols`` list, feeds each
symbol's freshly-closed bars to its detectors, and on a BOS-confirmed signal
builds a :class:`ProposedOrder`, runs the MANDATORY risk gate
(``RiskAgent.evaluate`` — the single chokepoint, CLAUDE.md #1), and places via a
slim writer that reuses the shared execution machinery
(``data_exec.place(division="bitunix_sfp")`` → native ``/tpsl/`` bracket + B1
server-side stop). Live entries write a Path-C ``paper_trade_record`` row tagged
``execution_mode="live"`` + ``broker_order_id`` so the existing per-account
reconciler / auto-book / ref-vs-fill track and book the position UNCHANGED.

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
    compute_geometry,
)
from trading_corp.brokers.bitunix_symbols import to_wire_format

log = logging.getLogger(__name__)

DIVISION = "bitunix_sfp"
PEAK_EQUITY_AGENT_STATE_KEY = "account_peak_equity"


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

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "BitunixSfpConfig":
        raw = raw or {}
        syms = raw.get("symbols") or ["BTC/USDT.P"]
        if isinstance(syms, str):
            syms = [syms]
        return cls(
            enabled=bool(raw.get("enabled", False)),
            auto_execute=bool(raw.get("auto_execute", False)),
            execution_mode=str(raw.get("execution_mode", "paper")).lower(),
            division=str(raw.get("division", DIVISION)),
            symbols=tuple(str(s) for s in syms),
            detection_tf=str(raw.get("detection_tf", "15m")),
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
        strategies_yaml_path: str | None = None,
    ) -> None:
        self.db_url = db_url
        self.risk_agent = risk_agent
        self.data_exec = data_exec
        self.logger_agent = logger_agent
        self.config = config
        self.bar_caches = bar_caches
        self._strategies_yaml_path = strategies_yaml_path or str(
            Path(__file__).resolve().parents[3] / "config" / "strategies.yaml"
        )
        wb = _watch_bars_from_hours(config.watch_hours)
        # Two detectors per symbol (REAL + CONSIDERABLE), pooled — mirrors the
        # oracle's two independent passes.
        self._detectors: dict[str, list[SfpDetector]] = {}
        self._last_ts: dict[str, int] = {}
        for sym in config.symbols:
            wire = to_wire_format(sym)
            self._detectors[wire] = [
                SfpDetector(mode=MODE_REAL, pivot_len=config.pivot_len,
                            back_to_break=config.back_to_break, watch_bars=wb),
                SfpDetector(mode=MODE_CONSIDERABLE, pivot_len=config.pivot_len,
                            back_to_break=config.back_to_break, watch_bars=wb),
            ]
            self._last_ts[wire] = 0

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def warm_start_from_cache(self) -> None:
        """Rebuild detector state from each cache's bar history (restart-safe;
        the bars ARE the state). Signals produced during replay are discarded —
        they are historical, already past their entry bar."""
        for sym in self.config.symbols:
            wire = to_wire_format(sym)
            cache = self.bar_caches.get(wire)
            if cache is None:
                continue
            bars = [self._to_sfp_bar(b) for b in getattr(cache, "bars", [])]
            for det in self._detectors[wire]:
                det.warm_start(bars)
            if bars:
                self._last_ts[wire] = bars[-1].ts_ms

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

    async def _process_symbol(self, symbol_display: str, wire: str, cache: Any) -> None:
        last = self._last_ts.get(wire, 0)
        new_bars = [b for b in getattr(cache, "bars", []) if int(b.ts_ms) > last]
        for raw in new_bars:
            bar = self._to_sfp_bar(raw)
            for det in self._detectors[wire]:
                for sig in det.on_closed_bar(bar):
                    await self._handle_signal(symbol_display, wire, sig, bar)
            self._last_ts[wire] = bar.ts_ms

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
                "source_signal": f"sfp_{sig.sfp_mode.lower()}",
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

    def _audit(self, kind: str, payload: dict) -> None:
        payload = {**payload, "strategy": DIVISION, "division": DIVISION}
        try:
            self.logger_agent.log_event(actor=DIVISION, kind=kind, payload=payload)
        except Exception as e:
            log.warning("bitunix_sfp: audit %s failed: %s", kind, e)
