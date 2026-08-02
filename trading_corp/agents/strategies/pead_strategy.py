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

Daily OHLC bars come from Robinhood (SPLIT-ADJUSTED, via the shared
`data.rh_bars` fetcher — IDENTICAL to the backtest source, no yfinance); the live
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
from trading_corp.agents.strategies import pead_sizing
from trading_corp.agents.strategies.pead_signal import (
    ScreenInputs,
    _percentile,
    confirmation_verdict,
    passes_screen,
    rank_wave,
    reaction_index,
    screen_params_from_config,
    standardized_ue,
    sue_params_from_config,
)
from trading_corp.persistence.pead_observability import insert_scan_evaluation
from trading_corp.data.rh_bars import RHBarsError, fetch_rh_daily_bars
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
_DEFAULT_POSITION_PCT = 0.10            # RETIRED as sizer (see _notional_budget); kept for the fixed-$ override path only
_DEFAULT_MAX_CONCURRENT = 7
_DEFAULT_SAFETY_FACTOR = 0.95           # derived sizer: per_name = settled_cash / open_slots * this
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

    # ── daily bars (Robinhood, SPLIT-ADJUSTED, shared with the backtest) + ATR ──
    @staticmethod
    def _fetch_daily_bars(symbol: str, lookback_days: int = _BARS_LOOKBACK_DAYS) -> list[_Bar]:
        # SPLIT-ADJUSTED daily bars from Robinhood via the shared data.rh_bars fetcher,
        # so live and backtest see IDENTICAL bars. Called by scan() through
        # asyncio.to_thread — the HTTP + pacing run OFF the event loop. Reuses the
        # engine's existing robin_stocks session (no login here). NO yfinance / banned
        # fallback: the fetcher raises on failure; we log and skip the symbol.
        from datetime import timedelta
        cutoff = date.today() - timedelta(days=lookback_days)
        span = "year" if lookback_days <= 340 else "5year"
        try:
            rows = fetch_rh_daily_bars(symbol, span=span, bounds="regular")
        except RHBarsError as e:
            log.warning("pead_strategy._fetch_daily_bars(%s): Robinhood fetch failed — skipping (%s)",
                        symbol, e)
            return []
        return [_Bar(r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"])
                for r in rows if r["date"] >= cutoff]

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

    def _log_scan_funnel(self, session_ts, eps_by, screens, ranked, sue_params, screen_params,
                         gate_verdicts=None) -> None:
        """Persist this scan's per-name signal funnel into scan_evaluation (FORWARD-ONLY, side-effect
        only). verdict='passed' for names in `ranked` (cleared screen + SUE>threshold + top-quintile);
        else 'rejected' with the earliest failing gate as reason_code. Records per-name SUE + the wave
        size + the quintile cutoff in metrics so 'was the quintile gate binding?' is answerable per
        entry. Observability MUST NEVER break the scan — every failure is swallowed."""
        try:
            passed = {c.symbol for c in ranked}
            info: dict[str, tuple] = {}
            wave_sues: list[float] = []
            for sym in eps_by:
                sue = standardized_ue(eps_by[sym], lookback=sue_params.lookback)
                inp = screens.get(sym)
                ok, reason = (passes_screen(inp, screen_params) if inp is not None
                              else (False, "missing_screen_inputs"))
                info[sym] = (sue, ok, reason)
                if ok and sue is not None:
                    wave_sues.append(sue)
            cutoff = (_percentile(sorted(wave_sues), sue_params.quintile_pct)
                      if (sue_params.top_quintile and wave_sues) else None)
            gv_map = gate_verdicts or {}
            for sym, (sue, ok, reason) in info.items():
                if sym in passed:
                    gv = gv_map.get(sym)
                    if gate_verdicts is None or gv == "pass":
                        verdict, rcode = "passed", None
                    elif gv == "reject_gate":
                        verdict, rcode = "rejected", "rejected_by_gate"
                    elif gv == "reject_no_slot":
                        verdict, rcode = "rejected", "rejected_no_slot"
                    else:  # reject_no_bar / unexpected
                        verdict, rcode = "rejected", "rejected_no_bar"
                elif not ok:
                    verdict, rcode = "rejected", reason
                elif sue is None:
                    verdict, rcode = "rejected", "insufficient_sue_history"
                elif sue <= sue_params.sue_threshold:
                    verdict, rcode = "rejected", "below_sue_threshold"
                else:
                    verdict, rcode = "rejected", "below_top_quintile"
                insert_scan_evaluation(
                    session_ts, sym, verdict, reason_code=rcode,
                    metrics={"sue": sue, "screen_ok": ok, "screen_reason": reason,
                             "wave_size": len(wave_sues), "quintile_cutoff": cutoff,
                             "sue_threshold": sue_params.sue_threshold,
                             "gate_verdict": gv_map.get(sym)},
                    db_url=self.db_url)
            log.info("pead_strategy.scan: logged funnel — %d evaluated / %d passed / wave=%d cutoff=%s",
                     len(info), len(passed), len(wave_sues),
                     ("%.3f" % cutoff) if cutoff is not None else "n/a")
        except Exception as e:  # noqa: BLE001 — observability must NEVER break the scan
            log.warning("pead_strategy.scan: scan_evaluation logging failed (non-fatal): %s", e)

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
                        order.extra["earnings_gap_top"] = rp   # re-anchor DRIFT gap to the REALIZED fill (entry_open), like the stop
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
        # Live dashboard dial (Part B): an agent_state override wins over the yaml
        # value; read fresh each scan (no restart), falls back to yaml when unset.
        _mc_override = pead_sizing.read_max_concurrent_override(self.db_url)
        if _mc_override is not None:
            max_concurrent = _mc_override
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
        slot_by: dict[str, str | None] = {}   # BMO/AMC reporting slot per symbol (None = unknown)
        name_by: dict[str, str | None] = {}   # ticker -> company name (General::Name, already-cached facts; display-only)
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
            name_by[sym] = facts.get("name")   # off the already-fetched 24h-cached facts — NO extra HTTP
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
            slot_by[sym] = getattr(latest, "report_time", None)   # carry BMO/AMC slot (from calendar via QuarterlyEPS)

        ranked = rank_wave(eps_by, screens, sue_params=sue_params,
                           screen_params=screen_params)
        # ── post-reaction CONFIRMATION GATE (config-gated; DEFAULT OFF) ──
        # Enter only if the post-reaction session closed above the pre-earnings
        # close (reaction = day a for BeforeMarket, a+1 for AfterMarket; no slot
        # -> excluded). Pure computation on already-fetched bars — NO new HTTP in
        # scan(). IDENTICAL rule as the backtest (shared confirmation_verdict).
        gate_on = bool(cfg.get("confirmation_gate", False))
        gate_verdicts: dict[str, str] = {}
        if gate_on:
            for c in ranked:
                _bars = bars_by[c.symbol]
                _a = self._index_on_or_after(_bars, ann_by[c.symbol])
                gate_verdicts[c.symbol] = confirmation_verdict(
                    slot_by.get(c.symbol), [b.close for b in _bars], _a)
            gated = [c for c in ranked if gate_verdicts.get(c.symbol) == "pass"]
        else:
            gated = ranked
        # persist the per-name signal funnel (forward-only, side-effect only; never breaks the scan)
        self._log_scan_funnel(datetime.now(timezone.utc).isoformat(),
                              eps_by, screens, ranked, sue_params, screen_params,
                              gate_verdicts=(gate_verdicts if gate_on else None))
        if not ranked:
            log.info("pead_strategy.scan: no candidates cleared screen+SUE")
            return []

        snap = await broker.snapshot()
        equity = float(getattr(snap, "equity", 0.0) or 0.0)
        available_bp = getattr(snap, "buying_power", None)  # portfolio-profile BP (settled-cash fallback only)
        # ── DERIVED, SELF-BALANCING SIZING (Part A) ──────────────────────────
        # Size against SETTLED cash (RobinhoodBroker derives it from
        # load_account_profile: cash − unsettled_funds − cash_held_for_orders),
        # NOT the retired position_pct × equity. per_name is recomputed per entry
        # inside the loop as cash + slots deplete, so the last slot is fundable by
        # construction. `position_notional` (fixed $) remains an explicit equal-$
        # override; position_pct no longer drives sizing.
        _settled = getattr(snap, "settled_cash", None)
        cash_remaining = (float(_settled) if _settled is not None
                          else (float(available_bp) if available_bp is not None else 0.0))
        safety_factor = float(cfg.get("size_safety_factor", _DEFAULT_SAFETY_FACTOR))
        fixed_notional = cfg.get("position_notional")   # equal-$ escape hatch; None => derived
        max_hold_seconds = pp.MAX_HOLD_TRADING_DAYS * 24 * 3600  # informational; live TIME rule uses trading-day count

        placed: list[ProposedOrder] = []
        for cand in gated:
            if capacity <= 0:
                break
            bars = bars_by[cand.symbol]
            entry_price = float(bars[-1].close)       # daily-scan entry reference (≈ next-open fill)
            if entry_price <= 0:
                continue
            prim = self._build_primitives(bars, ann_by[cand.symbol], entry_price,
                                          slot_by.get(cand.symbol))
            if prim is None:
                continue
            # ── derived per-name size: settled_cash / remaining_slots × safety ──
            # (recomputed each iteration against ACTUAL remaining cash + slots)
            if fixed_notional is not None:            # explicit equal-$ override (position_notional)
                try:
                    per_name = max(0.0, float(fixed_notional))
                except (TypeError, ValueError):
                    per_name = 0.0
            else:
                per_name = (cash_remaining / capacity) * safety_factor if capacity > 0 else 0.0
            if per_name < 1.0:                        # below RH's $1 fractional min — clean skip (every later slot too)
                log.info("pead_strategy: derived size $%.2f < $1 (settled $%.2f / %d slots) — skip %s",
                         per_name, cash_remaining, capacity, cand.symbol)
                continue
            elig = getattr(broker, "fractional_eligible", None)   # #6 (cached on broker)
            if elig is not None and not await elig(cand.symbol):
                log.info("pead_strategy: %s not fractional-eligible — skip", cand.symbol)
                continue
            if per_name > cash_remaining + 1e-9:      # never size beyond settled placeable cash  # #5
                log.info("pead_strategy: %s size $%.2f > settled cash $%.2f — skip",
                         cand.symbol, per_name, cash_remaining)
                continue
            nxt = nxt_by.get(cand.symbol)
            order = ProposedOrder(
                strategy=self.SLUG, symbol=cand.symbol, side="buy", qty=0.0,
                order_type="market", notional_usd=per_name, fractional=True,
                rationale=f"PEAD entry SUE={cand.sue:.2f}",
                extra={
                    # the 6 LOCKED extra_json keys the dashboard + exit engine read
                    "entry_atr_14": prim["entry_atr_14"],
                    "post_earnings_swing_low": prim["post_earnings_swing_low"],
                    "pre_earnings_close": prim["pre_earnings_close"],
                    "earnings_gap_top": prim["earnings_gap_top"],
                    "next_earnings_date": nxt.isoformat() if nxt else None,
                    "entry_sue": float(cand.sue),
                    "report_time": slot_by.get(cand.symbol),   # BMO/AMC slot carried for a future confirmation gate; None = unknown (do NOT default)
                    "name": cand.symbol,
                    "company_name": name_by.get(cand.symbol),   # General::Name (display-only; ticker stays the identity)
                    # ledger trade-card fields
                    "entry_reference_price": entry_price,  # overwritten with realized fill (live)
                    "stop_price": prim["stop_level"],
                    "source_signal": "srw_sue",
                    "notional_usd": per_name,
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
                cash_remaining -= per_name               # deplete settled cash so the next name resizes (#5)
                self.logger_agent.log_event(
                    self.SLUG, "pead_intent",
                    {"strategy": self.SLUG, "division": self.SLUG, "symbol": cand.symbol,
                     "notional": per_name,
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
            cash_remaining -= float(order.extra.get("executed_notional") or per_name)  # deplete settled cash (#5)
            self.logger_agent.log_event(
                self.SLUG, "pead_entry",
                {"strategy": self.SLUG, "division": self.SLUG, "symbol": cand.symbol,
                 "qty": order.qty, "notional": per_name,
                 "executed_notional": order.extra.get("executed_notional"),
                 "sue": round(float(cand.sue), 3),
                 "entry": order.extra.get("entry_reference_price"),
                 "execution_mode": order.execution_mode},
            )
            placed.append(order)
            capacity -= 1
        log.info("pead_strategy.scan: entered %d position(s)", len(placed))
        return placed

    def _build_primitives(self, bars: list[_Bar], announcement: date, entry_price: float,
                          report_time: str | None) -> dict | None:
        """The locked entry primitives. `earnings_gap_top` = the entry_open
        (re-anchored to the realized fill at both fill sites). `pre_earnings_close`
        is SLOT-AWARE — the gate's own bar0 (AMC=a, BMO=a-1), so the drift gap is
        measured from the SAME pre-earnings close the confirmation gate judges.
        ATR(14) and the post-earnings swing-low run through the LATEST bar (the
        live entry day); `entry_price` is the live entry reference (current price)."""
        a = self._index_on_or_after(bars, announcement)
        if a is None or a < 1:
            return None
        last_idx = len(bars) - 1
        atr = self._atr14(bars, last_idx)
        if atr is None:
            return None
        # DRIFT baseline = the gate's slot-aware bar0, using the SAME reaction_index
        # confirmation_verdict uses (bar0 = reaction_index - 1: AMC=a, BMO=a-1). One
        # definition, both consumers — no second, drifting slot rule. Unknown slot
        # -> a-1 (unchanged; entry behaviour for un-slotted names is untouched).
        _bar1 = reaction_index(report_time, a)
        _bar0 = (_bar1 - 1) if _bar1 is not None else (a - 1)
        if _bar0 < 0:
            return None
        pre_earnings_close = bars[_bar0].close
        earnings_gap_top = entry_price   # DRIFT anchor = entry_open (validated backtest semantics); re-anchored to the realized fill at both fill sites
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
            # DRIFT is a DAILY-CLOSE rule: never on the entry day (held < 1) and at
            # most once per newly-COMPLETED daily bar. Stop/guard/time are unchanged
            # (stop reads the intraday quote `last`; guard/time read the calendar).
            # The daily close comes from the SAME split-adjusted RH fetcher scan()
            # uses, OFF the event loop via asyncio.to_thread — no HTTP on the loop.
            drift_close: float | None = None
            drift_evaluated = False
            if held >= 1:
                marker = extra.get("drift_last_daily")
                # Cheap guard: only touch the RH daily fetcher when a new session may
                # have completed since we last evaluated (marker != the most-recent
                # weekday before today). Avoids a per-tick HTTP fetch in steady state.
                if marker != self._prev_weekday(today).isoformat():
                    dbars = await asyncio.to_thread(self._fetch_daily_bars, r["symbol"])
                    lower = marker or opened.isoformat()   # never evaluate the entry day or earlier
                    pending = [b for b in dbars
                               if b.d < today and b.d.isoformat() > lower]   # completed, post-entry, not-yet-seen
                    if pending:
                        self._mark_drift_daily(r["order_id"], pending[-1].d.isoformat())
                        # Fire drift on the FIRST completed close that crosses the
                        # drift-dead level (parity with the backtest, which checks each
                        # bar's CLOSE in order). No crossing close -> drift suppressed.
                        level = pp.drift_dead_level(prim)
                        if pp.earnings_gap_usd(prim) > 0:
                            for b in pending:
                                if b.close <= level:
                                    drift_evaluated = True
                                    drift_close = float(b.close)
                                    break
            pr = pp.compute_pressures(prim, last, held_trading_days=held,
                                      days_to_next_earnings=d2n,
                                      drift_price=(drift_close if drift_evaluated else None))
            rule = self._fired_rule(pr, d2n, held, drift_evaluated=drift_evaluated)
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
    def _fired_rule(pr: "pp.Pressures", days_to_next, held,
                    drift_evaluated: bool = True) -> str | None:
        """Top-down first-match-wins; fire when a pressure reaches 1.0 (stop /
        drift / time) or the guard date arrives (≤ GUARD_LEAD_DAYS). `drift_evaluated`
        gates the DRIFT branch: drift is honoured ONLY on a completed-daily-bar tick
        (manage() sets it False on the entry day and on intraday ticks with no new
        completed daily bar), so a same-day intraday dip can never fire drift."""
        if pr.stop >= 1.0:
            return "stop"
        if drift_evaluated and pr.drift >= 1.0:
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

    @staticmethod
    def _prev_weekday(d: date) -> date:
        """The most recent weekday strictly before `d` (Mon->Fri). Used only as a
        cheap 'has a new session likely completed?' guard so manage() does not hit the
        RH daily fetcher every 5-min tick. Holidays only cause an occasional harmless
        EXTRA fetch (never a missed one — the fetched bars are still filtered to real
        completed sessions strictly before today). Drift signals on session D's close
        are therefore acted on from D+1's ticks: a ≤1-session pickup lag, immaterial
        for a slow multi-day rule and safe (never fires on a still-forming bar)."""
        from datetime import timedelta
        x = d - timedelta(days=1)
        while x.weekday() >= 5:   # Sat=5, Sun=6
            x -= timedelta(days=1)
        return x

    def _mark_drift_daily(self, order_id: str, day_iso: str) -> None:
        """Record (in the row's extra_json) the completed daily bar last evaluated for
        DRIFT, so drift fires at most once per new daily close — not every manage tick."""
        with db.connect(self.db_url) as conn:
            conn.execute(
                "UPDATE paper_trade_record SET "
                "extra_json=json_set(COALESCE(extra_json,'{}'),'$.drift_last_daily',?) "
                "WHERE order_id=? AND result IS NULL",
                (day_iso, order_id),
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
            order.extra["earnings_gap_top"] = avg   # re-anchor DRIFT gap to the REALIZED fill (entry_open), like the stop
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
        """RETIRED sizer (2026-08-02) — NO LONGER CALLED BY scan(), which now uses
        the settled-cash derived sizer (see the DERIVED SIZING block in scan). Kept
        only so `position_pct` / `position_notional` stay readable and for any
        external/back-compat caller. `position_notional` (fixed $) overrides; else
        the retired position_pct × equity."""
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
