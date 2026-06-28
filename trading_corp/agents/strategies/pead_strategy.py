"""PEAD strategy — long-only post-earnings-announcement-drift ENTRY + the live
EXIT engine for the `robinhood_pead` division.

Posture = the bitunix posture (inline-placed, no HITL):
  - `RiskAgent.evaluate` is the ONLY gate (safety: sizing/caps/halts/DD).
  - live-vs-paper = `execution_mode=="live" AND auto_execute(yaml)` — paper path
    NEVER calls `data_exec.place` (the structural safety claim).
  - the position ledger is `paper_trade_record`, carrying the LOCKED
    `pead_pressures` primitives the dashboard AND this exit engine both read.

The exit engine IMPORTS `pead_pressures` — it never re-implements the math, so a
position fires at the exact price the dashboard shows it approaching.

Daily OHLC bars stay on yfinance (the code-safety / backtest source); the live
`last` quote for exits comes from the broker.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import yaml

from trading_corp.agents.strategies import pead_pressures as pp
from trading_corp.agents.strategies.pead_signal import (
    ScreenInputs,
    rank_wave,
    screen_params_from_config,
    standardized_ue,
    sue_params_from_config,
)
from trading_corp.data.earnings_provider import EarningsProvider
from trading_corp.persistence import db
from trading_corp.persistence.models import (
    AccountState,
    PaperTradeRecord,
    ProposedOrder,
    StrategyState,
)
from trading_corp.utils.market_hours import ET, default_calendar
from trading_corp.web.pead_view import business_days  # shared trading-day count

log = logging.getLogger(__name__)

_DEFAULT_MANAGE_CADENCE_SEC = 300       # few-min exit cadence
_DEFAULT_POSITION_PCT = 0.10            # 10% of account value per trade
_DEFAULT_MAX_CONCURRENT = 7
_DEFAULT_ENTRY_DELAY_DAYS = 1           # enter 1-2 trading days post-announcement
_DEFAULT_ENTRY_MAX_DELAY_DAYS = 2
_BARS_LOOKBACK_DAYS = 180              # daily bars window for ATR / swing / gap-top
_DEFAULT_RECONCILE_POLL_SEC = 30                  # reconcile-loop tick while pending orders exist
_DEFAULT_RECONCILE_DEADLINE_AFTER_OPEN_SEC = 300  # wait past the 9:30 ET open before collar-miss cancel
_DEFAULT_RECONCILE_PARTIAL_WARN_FRAC = 0.90       # warn when realized $ < this fraction of requested
_DEFAULT_INTENT_BUFFER_SEC = 60        # seconds after open before placing intent orders (~9:31 ET)


@dataclass
class _Bar:
    d: date
    open: float
    high: float
    low: float
    close: float
    volume: float


class PEADStrategy:
    """Owns the real entry + exit logic; the division shell just gates + routes."""

    SLUG = "robinhood_pead"

    def __init__(
        self,
        *,
        db_url: str,
        risk_agent,
        data_exec,
        logger_agent,
        earnings_provider: EarningsProvider | None = None,
        strategies_yaml: Path = Path("config/strategies.yaml"),
        execution_mode: str = "paper",
    ) -> None:
        self.db_url = db_url
        self.risk_agent = risk_agent
        self.data_exec = data_exec
        self.logger_agent = logger_agent
        self._provider = earnings_provider or EarningsProvider(
            api_key=os.environ.get("EODHD_API_KEY"), db_url=db_url,
        )
        self._strategies_yaml = Path(strategies_yaml)
        self._execution_mode = execution_mode
        self._peak_equity = 0.0

    # ── config (fresh-read each call: runtime retune w/o restart) ─────────
    def _cfg(self) -> dict:
        try:
            with self._strategies_yaml.open(encoding="utf-8") as f:
                return (yaml.safe_load(f) or {}).get("robinhood_pead", {}) or {}
        except Exception as e:  # noqa: BLE001
            log.warning("pead_strategy: config read failed: %s", e)
            return {}

    def _yaml_auto_execute(self) -> bool:
        """The runtime kill-switch / Board blessing — fresh-read every decision
        (mirrors bitunix). LIVE placement requires this True AND execution_mode
        live; otherwise the paper path runs (no data_exec.place)."""
        return bool(self._cfg().get("auto_execute", False))

    def _is_live(self) -> bool:
        return self._execution_mode == "live" and self._yaml_auto_execute()

    def _universe(self) -> list[str]:
        cfg = self._cfg()
        spec = cfg.get("universe") or cfg.get("universe_file")
        if isinstance(spec, list):
            return [str(s).strip().upper() for s in spec if str(s).strip()]
        if isinstance(spec, str) and spec:
            try:
                p = spec[1:] if spec.startswith("@") else spec
                with open(p, encoding="utf-8") as f:
                    return [ln.strip().upper() for ln in f
                            if ln.strip() and not ln.startswith("#")]
            except Exception as e:  # noqa: BLE001
                log.warning("pead_strategy: universe load failed (%s): %s", spec, e)
        return []

    # ── daily bars (yfinance) + ATR ──────────────────────────────────────
    @staticmethod
    def _fetch_daily_bars(symbol: str, lookback_days: int = _BARS_LOOKBACK_DAYS) -> list[_Bar]:
        try:
            import yfinance as yf  # type: ignore
            from datetime import timedelta
            end = date.today()
            start = end - timedelta(days=lookback_days)
            dfr = yf.download(symbol, start=start.isoformat(), end=end.isoformat(),
                              progress=False, auto_adjust=False)
        except Exception as e:  # noqa: BLE001
            log.debug("pead_strategy._fetch_daily_bars(%s) failed: %s", symbol, e)
            return []
        if dfr is None or getattr(dfr, "empty", True):
            return []

        def _cell(row, col):
            v = row[col]
            return float(v.iloc[0]) if hasattr(v, "iloc") else float(v)
        bars: list[_Bar] = []
        for idx, row in dfr.iterrows():
            try:
                d = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
                bars.append(_Bar(d, _cell(row, "Open"), _cell(row, "High"),
                                 _cell(row, "Low"), _cell(row, "Close"), _cell(row, "Volume")))
            except Exception:  # noqa: BLE001
                continue
        return bars

    @staticmethod
    def _atr14(bars: list[_Bar], upto_idx: int, period: int = 14) -> float | None:
        if upto_idx < period:
            return None
        trs: list[float] = []
        for i in range(upto_idx - period + 1, upto_idx + 1):
            prev_close = bars[i - 1].close
            tr = max(bars[i].high - bars[i].low,
                     abs(bars[i].high - prev_close),
                     abs(bars[i].low - prev_close))
            trs.append(tr)
        return sum(trs) / len(trs) if trs else None

    @staticmethod
    def _index_on_or_after(bars: list[_Bar], d: date) -> int | None:
        for i, b in enumerate(bars):
            if b.d >= d:
                return i
        return None

    # ── ledger helpers ───────────────────────────────────────────────────
    def _open_rows(self) -> list[dict]:
        with db.connect(self.db_url) as conn:
            rows = conn.execute(
                "SELECT order_id, symbol, qty, entry_reference_price, ts, extra_json "
                "FROM paper_trade_record WHERE division=? AND result IS NULL",
                (self.SLUG,),
            ).fetchall()
        out = []
        for r in rows:
            extra = {}
            if r["extra_json"]:
                try:
                    extra = json.loads(r["extra_json"]) or {}
                except (ValueError, TypeError):
                    extra = {}
            out.append({"order_id": r["order_id"], "symbol": r["symbol"],
                        "qty": float(r["qty"] or 0),
                        "entry_price": float(r["entry_reference_price"] or 0),
                        "opened_ts": r["ts"], "extra": extra})
        return out

    def _held_symbols(self) -> set[str]:
        return {r["symbol"] for r in self._open_rows()}

    # ── risk gate (the ONLY gate; no HITL) ───────────────────────────────
    def _risk_ok(self, order: ProposedOrder, equity: float) -> bool:
        self._peak_equity = max(self._peak_equity, equity)
        account = AccountState(account=self.SLUG, equity=equity,
                               peak_equity=self._peak_equity)
        strat_state = StrategyState.from_persistence(self.SLUG, db_url=self.db_url)
        verdict = self.risk_agent.evaluate(order, account, strat_state, None, None,
                                           db_url=self.db_url)
        if verdict.verdict == "reject":
            order.status = "risk_rejected"
            order.risk_reason = verdict.reason
            self.logger_agent.log_proposed_order(order)
            log.info("pead_strategy: risk REJECT %s: %s", order.symbol, verdict.reason)
            return False
        if verdict.verdict == "resize" and verdict.new_qty is not None:
            order.qty = float(verdict.new_qty)
        return True

    async def _place_or_paper(self, order: ProposedOrder) -> bool:
        """LIVE → data_exec.place (real order); PAPER → no place (record only).
        Returns True if the order should be ledgered (placed or paper-accepted)."""
        if self._is_live():
            try:
                fill = await self.data_exec.place(order, division=self.SLUG)
                if fill is not None and getattr(fill, "price", None) is not None:
                    order.fill_price = float(fill.price)
                # Fractional/notional: RH's POLLED fill is the ONLY source of truth —
                # adopt the REALIZED qty, executed $, and (buy) realized avg entry
                # price. Never the client-computed request qty. Whole-share keeps qty.
                if getattr(order, "fractional", False) and fill is not None:
                    fq = getattr(fill, "qty", None)
                    if fq:
                        order.qty = float(fq)
                    en = getattr(fill, "executed_notional", None)
                    if en is not None:
                        order.extra["executed_notional"] = float(en)
                    if order.side == "buy" and getattr(fill, "price", None):
                        # FLAG 1: anchor entry on the REALIZED fill — and re-anchor the
                        # ledger stop the same way (stop = 2.5*ATR below ENTRY) via the
                        # LOCKED pead_pressures contract, so the stored stop matches the
                        # level the engine fires at (which already recomputes from entry).
                        rp = float(fill.price)
                        order.extra["entry_reference_price"] = rp
                        _pr = pp.primitives_from_extra(order.extra, rp)
                        if _pr is not None:
                            order.extra["stop_price"] = pp.stop_level(_pr)
                order.execution_mode = "live"
                return True
            except Exception as e:  # noqa: BLE001
                log.warning("pead_strategy: live place failed %s: %s", order.symbol, e)
                return False
        # PAPER: no real fill — estimate the qty from the notional so the paper record
        # is sane (paper P&L is illustrative; the live path always overwrites realized).
        if getattr(order, "fractional", False) and float(order.qty or 0) <= 0:
            ref = float(order.extra.get("entry_reference_price") or 0)
            if order.notional_usd and ref > 0:
                order.qty = round(float(order.notional_usd) / ref, 6)
        order.execution_mode = "paper"
        return True

    def _write_record(self, order: ProposedOrder, *, max_hold_seconds: int) -> None:
        rec = PaperTradeRecord.from_order(
            order, strategy=self.SLUG, division=self.SLUG,
            max_hold_seconds=max_hold_seconds,
        )
        rec.extra = dict(order.extra)            # carry the 6 locked primitives
        rec.execution_mode = order.execution_mode
        db.insert_paper_trade_record(rec.to_db_row(), db_url=self.db_url)

    # ── ENTRY scan ───────────────────────────────────────────────────────
    async def scan(self, broker, regime: str = "neutral") -> list[ProposedOrder]:
        cfg = self._cfg()
        universe = self._universe()
        if not universe:
            log.warning("pead_strategy.scan: empty universe — nothing to do")
            return []
        screen_params = screen_params_from_config(cfg.get("screen", {}) or {})
        sue_params = sue_params_from_config(cfg.get("signal", {}) or {})
        max_concurrent = int(cfg.get("max_concurrent_positions", _DEFAULT_MAX_CONCURRENT))
        emin = int(cfg.get("entry_delay_days", _DEFAULT_ENTRY_DELAY_DAYS))
        emax = int(cfg.get("entry_max_delay_days", _DEFAULT_ENTRY_MAX_DELAY_DAYS))
        today = datetime.now(timezone.utc).date()

        held = self._held_symbols() | self._pending_symbols()   # pending+intent entries reserve a slot too
        capacity = max_concurrent - len(held)
        if capacity <= 0:
            log.info("pead_strategy.scan: book full (%d) — no entries", len(held))
            return []

        # ── wave: names whose latest reportDate is emin..emax trading days ago ──
        eps_by: dict[str, list[float]] = {}
        screens: dict[str, ScreenInputs] = {}
        bars_by: dict[str, list[_Bar]] = {}
        ann_by: dict[str, date] = {}
        nxt_by: dict[str, date | None] = {}
        for sym in universe:
            if sym in held:
                continue
            eps_rows = await asyncio.to_thread(self._provider.get_quarterly_eps, sym)
            if not eps_rows:
                continue
            latest = eps_rows[-1]
            ann = getattr(latest, "report_date", None)
            if ann is None:
                continue
            days_ago = business_days(ann, today)
            if not (emin <= days_ago <= emax):
                continue                              # not in the 1-2-day window
            bars = await asyncio.to_thread(self._fetch_daily_bars, sym)
            if not bars:
                continue
            facts = self._provider.get_company_facts(sym) or {}
            nxt = self._provider.get_next_earnings_date(sym, asof=today)
            d2n = business_days(today, nxt) if nxt else None
            last_close = bars[-1].close
            avg_vol = (sum(b.volume for b in bars[-30:]) / min(30, len(bars))) if bars else None
            eps_by[sym] = [float(q.actual_eps) for q in eps_rows]
            screens[sym] = ScreenInputs(
                symbol=sym, price=last_close, avg_daily_volume_30d=avg_vol,
                market_cap=facts.get("market_cap"), sector=facts.get("sector"),
                days_to_next_earnings=d2n,
            )
            bars_by[sym] = bars
            ann_by[sym] = ann
            nxt_by[sym] = nxt

        ranked = rank_wave(eps_by, screens, sue_params=sue_params,
                           screen_params=screen_params)
        if not ranked:
            log.info("pead_strategy.scan: no candidates cleared screen+SUE")
            return []

        snap = await broker.snapshot()
        equity = float(getattr(snap, "equity", 0.0) or 0.0)
        available_bp = getattr(snap, "buying_power", None)  # settled BP; None = no guard (paper/unknown)
        notional_budget = self._notional_budget(cfg, equity)             # equal-$ per name
        max_hold_seconds = pp.MAX_HOLD_TRADING_DAYS * 24 * 3600  # informational; live TIME rule uses trading-day count

        placed: list[ProposedOrder] = []
        for cand in ranked:
            if capacity <= 0:
                break
            bars = bars_by[cand.symbol]
            entry_price = float(bars[-1].close)       # daily-scan entry reference (≈ next-open fill)
            if entry_price <= 0:
                continue
            prim = self._build_primitives(bars, ann_by[cand.symbol], entry_price)
            if prim is None:
                continue
            # ── equal-DOLLAR notional sizing (config-driven, same $ per candidate) ──
            if notional_budget < 1.0:                 # below RH's $1 fractional minimum
                log.info("pead_strategy: notional $%.2f < $1 — skip %s", notional_budget, cand.symbol)
                continue
            elig = getattr(broker, "fractional_eligible", None)   # #6 (cached on broker)
            if elig is not None and not await elig(cand.symbol):
                log.info("pead_strategy: %s not fractional-eligible — skip", cand.symbol)
                continue
            if available_bp is not None and notional_budget > float(available_bp) + 1e-9:
                log.info("pead_strategy: %s settled BP $%.2f < notional $%.2f — skip",  # #5
                         cand.symbol, float(available_bp), notional_budget)
                continue
            nxt = nxt_by.get(cand.symbol)
            order = ProposedOrder(
                strategy=self.SLUG, symbol=cand.symbol, side="buy", qty=0.0,
                order_type="market", notional_usd=notional_budget, fractional=True,
                rationale=f"PEAD entry SUE={cand.sue:.2f}",
                extra={
                    # the 6 LOCKED extra_json keys the dashboard + exit engine read
                    "entry_atr_14": prim["entry_atr_14"],
                    "post_earnings_swing_low": prim["post_earnings_swing_low"],
                    "pre_earnings_close": prim["pre_earnings_close"],
                    "earnings_gap_top": prim["earnings_gap_top"],
                    "next_earnings_date": nxt.isoformat() if nxt else None,
                    "entry_sue": float(cand.sue),
                    "name": cand.symbol,
                    # ledger trade-card fields
                    "entry_reference_price": entry_price,  # overwritten with realized fill (live)
                    "stop_price": prim["stop_level"],
                    "source_signal": "srw_sue",
                    "notional_usd": notional_budget,
                },
            )
            if not self._risk_ok(order, equity):
                continue
            # Entry-fix (LIVE): RH REJECTS fractional market_hours='regular_hours' orders
            # submitted pre-market (accepts the POST but immediately sets state=rejected —
            # there is no robin_stocks path to queue a fractional order pre-market). Fix:
            # write an INTENT row now (NO broker call); reconcile() Phase-1 submits the
            # real order at open+buffer (~9:31 ET) via _place_or_paper → data_exec.place.
            # The PAPER path is UNCHANGED (no real order; estimate qty + record now).
            if self._is_live():
                self._write_intent(order, max_hold_seconds=max_hold_seconds)
                if available_bp is not None:             # RESERVE settled BP on the requested notional (#5)
                    available_bp = float(available_bp) - notional_budget
                self.logger_agent.log_event(
                    self.SLUG, "pead_intent",
                    {"strategy": self.SLUG, "division": self.SLUG, "symbol": cand.symbol,
                     "notional": notional_budget,
                     "sue": round(float(cand.sue), 3),
                     "entry_reference_price": order.extra.get("entry_reference_price")},
                )
                placed.append(order)
                capacity -= 1
                continue
            if not await self._place_or_paper(order):
                continue
            # PAPER: _place_or_paper estimated qty from the notional; record it now.
            # (The record reflects the paper estimate; the live realized path is the
            # reconcile promote above, never the requested notional.)
            self._write_record(order, max_hold_seconds=max_hold_seconds)
            if available_bp is not None:                 # decrement settled BP as we fill (#5)
                available_bp = float(available_bp) - float(order.extra.get("executed_notional") or notional_budget)
            self.logger_agent.log_event(
                self.SLUG, "pead_entry",
                {"strategy": self.SLUG, "division": self.SLUG, "symbol": cand.symbol,
                 "qty": order.qty, "notional": notional_budget,
                 "executed_notional": order.extra.get("executed_notional"),
                 "sue": round(float(cand.sue), 3),
                 "entry": order.extra.get("entry_reference_price"),
                 "execution_mode": order.execution_mode},
            )
            placed.append(order)
            capacity -= 1
        log.info("pead_strategy.scan: entered %d position(s)", len(placed))
        return placed

    def _build_primitives(self, bars: list[_Bar], announcement: date, entry_price: float) -> dict | None:
        """The locked entry primitives. `earnings_gap_top` = close of the first
        full-reaction session — the announcement-date bar `a`, the SAME bar the
        backtest re-align uses, so dashboard/engine/backtest agree. ATR(14) and
        the post-earnings swing-low run through the LATEST bar (the live entry
        day); `entry_price` is the live entry reference (current price)."""
        a = self._index_on_or_after(bars, announcement)
        if a is None or a < 1:
            return None
        last_idx = len(bars) - 1
        atr = self._atr14(bars, last_idx)
        if atr is None:
            return None
        pre_earnings_close = bars[a - 1].close
        earnings_gap_top = bars[a].close
        swing_low = min(b.low for b in bars[a:last_idx + 1])
        stop_level = max(entry_price - 2.5 * atr, swing_low)
        return {
            "entry_atr_14": float(atr),
            "post_earnings_swing_low": float(swing_low),
            "pre_earnings_close": float(pre_earnings_close),
            "earnings_gap_top": float(earnings_gap_top),
            "stop_level": float(stop_level),
        }

    # ── EXIT engine (manage) — imports pead_pressures, fires at contract px ──
    async def manage(self, broker) -> tuple[list[ProposedOrder], int]:
        cfg = self._cfg()
        cadence = int(cfg.get("manage_cadence_sec", _DEFAULT_MANAGE_CADENCE_SEC))
        rows = self._open_rows()
        if not rows:
            return [], cadence
        today = datetime.now(timezone.utc).date()
        snap = await broker.snapshot()
        equity = float(getattr(snap, "equity", 0.0) or 0.0)

        exits: list[ProposedOrder] = []
        for r in rows:
            extra = r["extra"]
            prim = pp.primitives_from_extra(extra, r["entry_price"])
            if prim is None:
                continue                              # not a PEAD-managed row yet
            try:
                last = float(await broker.quote(r["symbol"]))
            except Exception as e:  # noqa: BLE001
                log.debug("pead_strategy.manage: quote(%s) failed: %s", r["symbol"], e)
                continue
            opened = self._parse_date(r["opened_ts"]) or today
            held = business_days(opened, today)
            nxt = self._parse_date(extra.get("next_earnings_date"))
            d2n = business_days(today, nxt) if nxt else None
            pr = pp.compute_pressures(prim, last, held_trading_days=held,
                                      days_to_next_earnings=d2n)
            rule = self._fired_rule(pr, d2n, held)
            if rule is None:
                continue                              # no exit yet
            sell = ProposedOrder(
                strategy=self.SLUG, symbol=r["symbol"], side="sell", qty=float(r["qty"]),
                order_type="market", id=f"{r['order_id']}-exit-{rule}", fractional=True,
                rationale=f"PEAD exit:{rule}",
                extra={"exit_reason": rule, "parent_order_id": r["order_id"],
                       "reduce_only": True},
            )
            if not self._risk_ok(sell, equity):
                continue
            if not await self._place_or_paper(sell):
                continue
            held_qty = float(r["qty"])
            # #4: exit price = REALIZED avg fill (polled), not the decision-time quote;
            # realized sold qty from the fill. Paper falls back to last / held qty.
            live = (sell.execution_mode == "live")
            exit_price = float(sell.fill_price) if (live and sell.fill_price) else last
            sold_qty = float(sell.qty) if (live and sell.qty) else held_qty
            if live and sold_qty + 1e-6 < held_qty:
                # partial fractional sell — accept realized, leave the residual open
                # for the next manage tick (decision #2 on the sell side).
                log.warning("pead_strategy: PARTIAL exit %s sold %.6f of %.6f — residual stays open",
                            r["symbol"], sold_qty, held_qty)
                self._reduce_open_qty(r["order_id"], held_qty - sold_qty)
                exits.append(sell)
                continue
            self._close_record(r["order_id"], rule, exit_price, r["entry_price"], held_qty,
                               sell.execution_mode)
            self.logger_agent.log_event(
                self.SLUG, "pead_exit",
                {"strategy": self.SLUG, "division": self.SLUG, "symbol": r["symbol"],
                 "rule": rule, "exit": last, "held_days": held,
                 "execution_mode": sell.execution_mode},
            )
            exits.append(sell)
        return exits, cadence

    @staticmethod
    def _fired_rule(pr: "pp.Pressures", days_to_next, held) -> str | None:
        """Top-down first-match-wins; fire when a pressure reaches 1.0 (stop /
        drift / time) or the guard date arrives (≤ GUARD_LEAD_DAYS)."""
        if pr.stop >= 1.0:
            return "stop"
        if pr.drift >= 1.0:
            return "drift"
        if days_to_next is not None and days_to_next <= pp.GUARD_LEAD_DAYS:
            return "guard"
        if pr.time >= 1.0 or held >= pp.MAX_HOLD_TRADING_DAYS:
            return "time"
        return None

    def _close_record(self, order_id: str, rule: str, exit_price: float,
                      entry_price: float, qty: float, mode) -> None:
        now = datetime.now(timezone.utc).isoformat()
        pnl = (exit_price - entry_price) * qty
        result = "win" if pnl >= 0 else "loss"        # pnl-signed (long-only)
        with db.connect(self.db_url) as conn:
            conn.execute(
                "UPDATE paper_trade_record SET result=?, result_ts=?, result_price=?, "
                "actual_pnl_dollars=?, "
                "extra_json=json_set(COALESCE(extra_json,'{}'),'$.exit_reason',?) "
                "WHERE order_id=? AND result IS NULL",
                (result, now, exit_price, pnl, rule, order_id),
            )

    def _reduce_open_qty(self, order_id: str, residual_qty: float) -> None:
        """Shrink an open row's qty to the residual after a PARTIAL fractional exit so
        the next manage tick sells the remainder (never re-sells the full position)."""
        with db.connect(self.db_url) as conn:
            conn.execute(
                "UPDATE paper_trade_record SET qty=? WHERE order_id=? AND result IS NULL",
                (float(residual_qty), order_id),
            )

    # ── Flag-2 / Entry-fix: intent → at-open placement ───────────────────────────
    # RH REJECTS fractional market_hours='regular_hours' orders submitted pre-market
    # (accepts the POST but immediately sets state=rejected — no path to queue pre-open).
    # Fix: scan() writes an INTENT row (NO broker call at all). reconcile() Phase-1
    # submits the real order at open+buffer (~9:31 ET default) via _place_or_paper →
    # data_exec.place (the same regular-hours path that filled in the 2026-06-24 probe).
    # An intent row is NEVER counted in the position book; it becomes a real
    # paper_trade_record ONLY on a confirmed fill — no confirmed fill = no position.

    def _write_pending(self, order: ProposedOrder, rh_id: str | None, *,
                       max_hold_seconds: int, state: str = "pending") -> None:
        """INSERT the order into `pending_order` (NOT the book). `trading_date` is the
        ET session whose 9:30 open reconciles it. INSERT OR IGNORE keyed on order_id
        keeps a restart-replayed write idempotent. `state` is 'pending' (already placed,
        awaiting fill confirmation) or 'intent' (not yet placed, awaiting open+buffer)."""
        trading_date = datetime.now(ET).date().isoformat()
        with db.connect(self.db_url) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO pending_order (order_id, ts, strategy, division, "
                "symbol, side, order_type, notional_usd, broker_order_id, trading_date, "
                "max_hold_seconds, rationale, state, extra_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (order.id, order.ts, self.SLUG, self.SLUG, order.symbol, order.side,
                 order.order_type, float(order.notional_usd or 0.0), rh_id, trading_date,
                 int(max_hold_seconds), order.rationale, state, json.dumps(order.extra)),
            )

    def _write_intent(self, order: ProposedOrder, *, max_hold_seconds: int) -> None:
        """Write a pre-market INTENT row (state='intent', broker_order_id=NULL).
        reconcile() Phase-1 places the real order at open+buffer and promotes this
        row to a paper_trade_record on fill (idempotent INSERT OR IGNORE on order_id)."""
        self._write_pending(order, None, max_hold_seconds=max_hold_seconds, state="intent")

    def _pending_rows(self) -> list[dict]:
        with db.connect(self.db_url) as conn:
            rows = conn.execute(
                "SELECT order_id, symbol, side, order_type, notional_usd, broker_order_id, "
                "trading_date, max_hold_seconds, rationale, extra_json "
                "FROM pending_order WHERE division=? AND state='pending'",
                (self.SLUG,),
            ).fetchall()
        out = []
        for r in rows:
            extra = {}
            if r["extra_json"]:
                try:
                    extra = json.loads(r["extra_json"]) or {}
                except (ValueError, TypeError):
                    extra = {}
            out.append({"order_id": r["order_id"], "symbol": r["symbol"], "side": r["side"],
                        "order_type": r["order_type"],
                        "notional_usd": float(r["notional_usd"] or 0.0),
                        "broker_order_id": r["broker_order_id"],
                        "trading_date": r["trading_date"],
                        "max_hold_seconds": r["max_hold_seconds"],
                        "rationale": r["rationale"], "extra": extra})
        return out

    def _intent_rows(self) -> list[dict]:
        """INTENT rows (scan-written, not yet placed) for reconcile Phase-1."""
        with db.connect(self.db_url) as conn:
            rows = conn.execute(
                "SELECT order_id, symbol, side, order_type, notional_usd, "
                "trading_date, max_hold_seconds, rationale, extra_json "
                "FROM pending_order WHERE division=? AND state='intent'",
                (self.SLUG,),
            ).fetchall()
        out = []
        for r in rows:
            extra = {}
            if r["extra_json"]:
                try:
                    extra = json.loads(r["extra_json"]) or {}
                except (ValueError, TypeError):
                    extra = {}
            out.append({"order_id": r["order_id"], "symbol": r["symbol"], "side": r["side"],
                        "order_type": r["order_type"],
                        "notional_usd": float(r["notional_usd"] or 0.0),
                        "trading_date": r["trading_date"],
                        "max_hold_seconds": r["max_hold_seconds"],
                        "rationale": r["rationale"], "extra": extra})
        return out

    def _pending_symbols(self) -> set[str]:
        """Open PENDING+INTENT entry symbols — folded into the scan's `held` set so a
        name with a queued or intent entry is neither re-scanned nor double-counted
        against max_concurrent (slot reservation across the pre-open→open gap)."""
        with db.connect(self.db_url) as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM pending_order "
                "WHERE division=? AND state IN ('pending','intent')",
                (self.SLUG,),
            ).fetchall()
        return {r["symbol"] for r in rows}

    def _delete_pending(self, order_id: str) -> None:
        with db.connect(self.db_url) as conn:
            conn.execute("DELETE FROM pending_order WHERE order_id=?", (order_id,))

    @staticmethod
    def _session_open_et(trading_date: str) -> datetime | None:
        """The 9:30 ET open datetime for a YYYY-MM-DD session, or None if that date is
        not an NYSE trading day. The collar-miss deadline is anchored HERE (open +
        deadline), NOT at placement — placement is pre-open, so a placement-anchored
        deadline would expire before the market opens and cancel every queued order."""
        d = PEADStrategy._parse_date(trading_date)
        if d is None:
            return None
        if default_calendar().close_time_et(d) is None:
            return None                                # closed day — no open
        return datetime.combine(d, time(9, 30), tzinfo=ET)

    async def reconcile(self, broker) -> tuple[list[ProposedOrder], int]:
        """Drain pending entries at/after the open.

        Phase 1 — intent → at-open placement: intent rows written by scan() (no
        broker call yet) are submitted via _place_or_paper at/after open+buffer
        (~9:31 ET by default) and promoted to real records on a confirmed fill.
        Placement failures and intents past the deadline are dropped (no phantom).

        Phase 2 — pending → fill confirmation: rows already placed with a real
        RH order id are polled at/after the open; confirmed fills are promoted, a
        terminal-zero-fill is dropped, and an order still open past the open+
        deadline is the >5%% collar miss → cancel the resting GFD order (else it
        could fill UNWATCHED = phantom), then record any realized partial or drop.

        NO-OP pre-open (cancelling a queued order before 9:30 is the bug this
        method must not repeat). Returns (promoted, next_poll_seconds)."""
        cfg = self._cfg()
        poll = int(cfg.get("reconcile_poll_interval_sec", _DEFAULT_RECONCILE_POLL_SEC))
        promoted: list[ProposedOrder] = []
        now = datetime.now(timezone.utc)
        is_open = default_calendar().is_open_at(now)
        now_et = now.astimezone(ET)

        # ── Phase 1: intent → at-open placement ──────────────────────────
        intent_rows = self._intent_rows()
        if intent_rows and is_open:
            buffer_sec = int(cfg.get("intent_open_buffer_sec", _DEFAULT_INTENT_BUFFER_SEC))
            deadline_sec_i = int(cfg.get("reconcile_deadline_after_open_sec",
                                         _DEFAULT_RECONCILE_DEADLINE_AFTER_OPEN_SEC))
            warn_frac_i = float(cfg.get("reconcile_partial_warn_frac",
                                        _DEFAULT_RECONCILE_PARTIAL_WARN_FRAC))
            for r in intent_rows:
                open_et = self._session_open_et(r["trading_date"])
                if open_et is None:
                    continue
                if now_et >= open_et + timedelta(seconds=deadline_sec_i):
                    # past deadline — drop without placing (no phantom)
                    log.info("pead_strategy.reconcile: intent %s past open+%ds deadline — dropped",
                             r["symbol"], deadline_sec_i)
                    self.logger_agent.log_event(
                        self.SLUG, "pead_pending_dropped",
                        {"division": self.SLUG, "symbol": r["symbol"],
                         "reason": "intent_past_deadline"})
                    self._delete_pending(r["order_id"])
                    continue
                if now_et < open_et + timedelta(seconds=buffer_sec):
                    continue                            # within buffer — not yet time to place
                # ── Place at open+buffer ──────────────────────────────────
                max_hold = (int(r["max_hold_seconds"]) if r["max_hold_seconds"] is not None
                            else pp.MAX_HOLD_TRADING_DAYS * 24 * 3600)
                order = ProposedOrder(
                    strategy=self.SLUG, symbol=r["symbol"], side=r["side"], qty=0.0,
                    order_type=r["order_type"] or "market", notional_usd=r["notional_usd"],
                    fractional=True, id=r["order_id"],
                    rationale=r["rationale"] or "PEAD entry (intent)", extra=dict(r["extra"]),
                )
                ok = await self._place_or_paper(order)
                placed_qty = float(order.qty or 0)
                if not ok or placed_qty <= 0:
                    reason = "rejected" if (ok and placed_qty <= 0) else "placement_failed"
                    log.warning("pead_strategy.reconcile: intent %s %s — dropped (no record)",
                                r["symbol"], reason)
                    self.logger_agent.log_event(
                        self.SLUG, "pead_pending_dropped",
                        {"division": self.SLUG, "symbol": r["symbol"], "reason": reason})
                    self._delete_pending(r["order_id"])
                    continue
                # Fill confirmed — write record (idempotent INSERT OR IGNORE on order_id)
                req = float(r["notional_usd"] or 0.0)
                en = order.extra.get("executed_notional")
                if en is not None and req > 0 and float(en) < warn_frac_i * req:
                    log.warning("pead_strategy.reconcile: PARTIAL intent entry %s realized "
                                "$%.2f < %.0f%% of requested $%.2f (qty=%.6f) — recorded realized",
                                r["symbol"], float(en), warn_frac_i * 100, req, placed_qty)
                self._write_record(order, max_hold_seconds=max_hold)
                self._delete_pending(r["order_id"])
                self.logger_agent.log_event(
                    self.SLUG, "pead_entry",
                    {"strategy": self.SLUG, "division": self.SLUG, "symbol": r["symbol"],
                     "qty": order.qty, "notional": req,
                     "executed_notional": en,
                     "entry": order.extra.get("entry_reference_price"),
                     "execution_mode": order.execution_mode, "via_intent": True})
                promoted.append(order)

        # ── Phase 2: already-placed pending (state='pending') ────────────
        rows = self._pending_rows()
        if not rows:
            return promoted, poll
        if not is_open:
            return promoted, poll                      # pre-open / closed — leave queued
        if getattr(broker, "read_fractional_order", None) is None:
            log.warning("pead_strategy.reconcile: broker has no read_fractional_order — skip")
            return promoted, poll
        deadline_sec = int(cfg.get("reconcile_deadline_after_open_sec",
                                   _DEFAULT_RECONCILE_DEADLINE_AFTER_OPEN_SEC))
        warn_frac = float(cfg.get("reconcile_partial_warn_frac",
                                  _DEFAULT_RECONCILE_PARTIAL_WARN_FRAC))
        for r in rows:
            rh_id = r["broker_order_id"]
            try:
                info = await broker.read_fractional_order(rh_id)
            except Exception as e:  # noqa: BLE001
                log.warning("pead_strategy.reconcile: read(%s) failed: %s — retry next tick",
                            r["symbol"], e)
                continue
            state = str(info.get("state") or "").lower()
            cum = float(info.get("filled_qty") or 0.0)
            if state == "filled" and cum > 0:
                promoted.append(self._promote_pending(r, info, warn_frac))   # confirmed full fill
                continue
            if state in ("cancelled", "canceled", "rejected", "failed"):
                if cum > 0:                                                   # realized partial — keep it (#2)
                    promoted.append(self._promote_pending(r, info, warn_frac))
                else:
                    log.info("pead_strategy.reconcile: %s terminal %s, 0 filled — dropped",
                             r["symbol"], state)
                    self.logger_agent.log_event(
                        self.SLUG, "pead_pending_dropped",
                        {"division": self.SLUG, "symbol": r["symbol"], "reason": state,
                         "broker_order_id": rh_id})
                    self._delete_pending(r["order_id"])
                continue
            # non-terminal (queued / partially_filled) — collar-miss deadline check,
            # ANCHORED AT THE 9:30 OPEN (not placement).
            open_et = self._session_open_et(r["trading_date"])
            if open_et is None or now_et < open_et + timedelta(seconds=deadline_sec):
                continue                                   # within the window — still queued, poll next tick
            # past open + deadline → >5% collar miss: cancel the resting order, then
            # re-read the FINAL realized (mirror the synchronous cancel-then-read).
            canceller = getattr(broker, "cancel_fractional_order", None)
            cancelled = False
            if canceller is not None:
                try:
                    cancelled = bool(await canceller(rh_id))
                except Exception as e:  # noqa: BLE001
                    log.warning("pead_strategy.reconcile: cancel(%s) failed: %s", r["symbol"], e)
            try:
                info = await broker.read_fractional_order(rh_id)
                cum = float(info.get("filled_qty") or 0.0)
            except Exception:  # noqa: BLE001
                pass
            if cum > 0:                                    # partial filled before the deadline cancel — record it
                log.warning("pead_strategy.reconcile: %s collar partial — filled %.6f before "
                            "open+%ds cancel; recorded realized", r["symbol"], cum, deadline_sec)
                promoted.append(self._promote_pending(r, info, warn_frac))
            else:                                          # true collar miss — nothing filled
                log.warning("pead_strategy.reconcile: %s unfilled past open+%ds (>5%% collar miss) "
                            "— cancelled=%s, dropped (no record)", r["symbol"], deadline_sec, cancelled)
                self.logger_agent.log_event(
                    self.SLUG, "pead_pending_collar_miss",
                    {"division": self.SLUG, "symbol": r["symbol"], "broker_order_id": rh_id,
                     "cancelled": cancelled, "deadline_after_open_sec": deadline_sec})
                self._delete_pending(r["order_id"])
        return promoted, poll

    def _promote_pending(self, row: dict, info: dict, warn_frac: float) -> ProposedOrder:
        """Promote a CONFIRMED pending fill into a real open record: rebuild the order
        (SAME order_id → idempotent INSERT OR IGNORE), adopt the REALIZED qty / avg
        entry price / executed notional, re-anchor the stop on the realized entry
        (Flag 1, via the locked contract), write the record, drop the pending row.
        Warns when realized $ < warn_frac of requested (decision #2 observability)."""
        cum = float(info.get("filled_qty") or 0.0)
        avg = float(info.get("avg_price") or 0.0)
        en = info.get("executed_notional")
        order = ProposedOrder(
            strategy=self.SLUG, symbol=row["symbol"], side=row["side"], qty=0.0,
            order_type=row["order_type"] or "market", notional_usd=row["notional_usd"],
            fractional=True, id=row["order_id"],
            rationale=row["rationale"] or "PEAD entry (reconciled)", extra=dict(row["extra"]),
        )
        order.qty = cum
        if en is not None:
            order.extra["executed_notional"] = float(en)
        if avg > 0:
            order.extra["entry_reference_price"] = avg
            _pr = pp.primitives_from_extra(order.extra, avg)
            if _pr is not None:
                order.extra["stop_price"] = pp.stop_level(_pr)
        order.execution_mode = "live"
        req = float(row["notional_usd"] or 0.0)
        if en is not None and req > 0 and float(en) < warn_frac * req:
            log.warning("pead_strategy.reconcile: PARTIAL entry %s realized $%.2f < %.0f%% of "
                        "requested $%.2f (qty=%.6f) — recorded realized, no top-up",
                        row["symbol"], float(en), warn_frac * 100, req, cum)
        max_hold = (int(row["max_hold_seconds"]) if row["max_hold_seconds"] is not None
                    else pp.MAX_HOLD_TRADING_DAYS * 24 * 3600)
        self._write_record(order, max_hold_seconds=max_hold)
        self._delete_pending(row["order_id"])
        self.logger_agent.log_event(
            self.SLUG, "pead_entry",
            {"strategy": self.SLUG, "division": self.SLUG, "symbol": row["symbol"],
             "qty": order.qty, "notional": req,
             "executed_notional": order.extra.get("executed_notional"),
             "entry": order.extra.get("entry_reference_price"),
             "account": info.get("account"), "execution_mode": "live", "reconciled": True})
        return order

    @staticmethod
    def _notional_budget(cfg: dict, equity: float) -> float:
        """Equal-dollar notional per candidate (same value for every candidate in a
        scan). `position_notional` (fixed $) overrides; else position_pct × equity."""
        fixed = cfg.get("position_notional")
        if fixed is not None:
            try:
                return max(0.0, float(fixed))
            except (TypeError, ValueError):
                pass
        position_pct = float(cfg.get("position_pct", _DEFAULT_POSITION_PCT))
        return max(0.0, position_pct * float(equity or 0.0))

    @staticmethod
    def _parse_date(s) -> date | None:
        try:
            return date.fromisoformat(str(s)[:10])
        except (ValueError, TypeError):
            return None
