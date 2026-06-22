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

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
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
from trading_corp.web.pead_view import business_days  # shared trading-day count

log = logging.getLogger(__name__)

_DEFAULT_MANAGE_CADENCE_SEC = 300       # few-min exit cadence
_DEFAULT_POSITION_PCT = 0.10            # 10% of account value per trade
_DEFAULT_MAX_CONCURRENT = 7
_DEFAULT_ENTRY_DELAY_DAYS = 1           # enter 1-2 trading days post-announcement
_DEFAULT_ENTRY_MAX_DELAY_DAYS = 2
_BARS_LOOKBACK_DAYS = 180              # daily bars window for ATR / swing / gap-top


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
                order.execution_mode = "live"
                return True
            except Exception as e:  # noqa: BLE001
                log.warning("pead_strategy: live place failed %s: %s", order.symbol, e)
                return False
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
        position_pct = float(cfg.get("position_pct", _DEFAULT_POSITION_PCT))
        max_concurrent = int(cfg.get("max_concurrent_positions", _DEFAULT_MAX_CONCURRENT))
        emin = int(cfg.get("entry_delay_days", _DEFAULT_ENTRY_DELAY_DAYS))
        emax = int(cfg.get("entry_max_delay_days", _DEFAULT_ENTRY_MAX_DELAY_DAYS))
        today = datetime.now(timezone.utc).date()

        held = self._held_symbols()
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
            eps_rows = self._provider.get_quarterly_eps(sym)
            if not eps_rows:
                continue
            latest = eps_rows[-1]
            ann = getattr(latest, "report_date", None)
            if ann is None:
                continue
            days_ago = business_days(ann, today)
            if not (emin <= days_ago <= emax):
                continue                              # not in the 1-2-day window
            bars = self._fetch_daily_bars(sym)
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
        max_hold_seconds = pp.MAX_HOLD_TRADING_DAYS * 24 * 3600  # informational; live TIME rule uses trading-day count

        placed: list[ProposedOrder] = []
        for cand in ranked:
            if capacity <= 0:
                break
            bars = bars_by[cand.symbol]
            entry_price = float(bars[-1].close)       # daily-scan entry reference (≈ next-open fill)
            if entry_price <= 0:
                continue
            qty = math.floor((position_pct * equity) / entry_price)
            if qty < 1:
                log.info("pead_strategy: %s round-to-zero (px=%.2f, budget=%.2f) — skip",
                         cand.symbol, entry_price, position_pct * equity)
                continue                              # high-priced name; fill the next ranked
            prim = self._build_primitives(bars, ann_by[cand.symbol], entry_price)
            if prim is None:
                continue
            nxt = nxt_by.get(cand.symbol)
            order = ProposedOrder(
                strategy=self.SLUG, symbol=cand.symbol, side="buy", qty=float(qty),
                order_type="market",
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
                    "entry_reference_price": entry_price,
                    "stop_price": prim["stop_level"],
                    "source_signal": "srw_sue",
                },
            )
            if not self._risk_ok(order, equity):
                continue
            if not await self._place_or_paper(order):
                continue
            self._write_record(order, max_hold_seconds=max_hold_seconds)
            self.logger_agent.log_event(
                self.SLUG, "pead_entry",
                {"strategy": self.SLUG, "division": self.SLUG, "symbol": cand.symbol,
                 "qty": qty, "sue": round(float(cand.sue), 3), "entry": entry_price,
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
                strategy=self.SLUG, symbol=r["symbol"], side="sell", qty=r["qty"],
                order_type="market", id=f"{r['order_id']}-exit-{rule}",
                rationale=f"PEAD exit:{rule}",
                extra={"exit_reason": rule, "parent_order_id": r["order_id"],
                       "reduce_only": True},
            )
            if not self._risk_ok(sell, equity):
                continue
            if not await self._place_or_paper(sell):
                continue
            self._close_record(r["order_id"], rule, last, r["entry_price"], r["qty"],
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

    @staticmethod
    def _parse_date(s) -> date | None:
        try:
            return date.fromisoformat(str(s)[:10])
        except (ValueError, TypeError):
            return None
