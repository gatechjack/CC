"""Robinhood Joint Iron Condor — 45 DTE neutral premium-selling strategy.

Universe: SPY, QQQ, IWM, GLD, TLT (Tier 1 + Tier 2 ETFs only — no Tier 3
individual stocks in v1).

**Empty scan output is a valid outcome, not an error.** The strategy is
opportunistic and will not produce candidates if IVR is below 30, VIX is
above 30, a high-impact macro event is within 5 trading days, an
ex-dividend window is open on a candidate, or the term structure is
backwardated. Operators should not interpret empty scan output as a
fault. A daily telemetry counter (`scan_passes_with_no_candidates`)
records each filtered-out symbol so the audit log surfaces *why* a quiet
day happened.

Decision-tree branch order (per the parent plan) — evaluated in order,
first match wins, per open IC except where noted:

  0. Portfolio-level catastrophic stop (close ALL open ICs at once).
  1. 50% profit target → close that IC.
  2. 21 DTE → close that IC.
  3. DTE < 7 → close that IC (gamma-risk override of all later branches).
  4. Ex-dividend within 3 trading days AND short call delta > 0.25 →
     close that IC. Put-side ex-div drop risk is NOT handled in v1:
     ETF dividends in the universe (SPY/QQQ/IWM/GLD/TLT) are <0.5% of
     price per ex-date, within normal daily noise. Re-evaluate if the
     universe expands to higher-yield names.
  4.5. Per-position hard stop: combo P&L ≤ −200% × credit_at_entry →
     close that IC. Defense in depth against IV-spike scenarios where
     both sides expand without either short crossing 0.35 delta.
  5. Tested-side identification (output feeds branches 6-9).
  6. |tested_Δ| ∈ [0.25, 0.30) → log "warn" (no order; cadence tightens
     to 15 min via the cadence-hint return tuple).
  7. |tested_Δ| ∈ [0.30, 0.35) AND DTE > 14 AND adjustment_count == 0
     AND untested side has remaining mark > $0.10 → Adjustment 1
     (atomic 4-leg combo: close untested, open new untested at Δ 0.30).
  8. |tested_Δ| ∈ [0.30, 0.35) AND (adjustment exhausted OR untested
     side dead OR DTE ≤ 14) → close tested side only.
  9. |tested_Δ| ≥ 0.35 OR underlying through short strike → close
     tested side only. (The hard stop has already been evaluated at
     branch 4.5; reaching here means the combo is tested but not at
     the hard stop.)

State persistence: a single JSON blob in the `agent_state` table at
`(agent='robinhood_joint_iron_condor', key='state')` carries the open-IC
registry, circuit-breaker counters, and the per-day scan telemetry. The
strategy reads it at the top of every scan/manage tick and writes back
at the bottom (and after the on_combo_filled callback completes).
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import yaml

from trading_corp.brokers.base import Broker
from trading_corp.data.ex_dividend_calendar import ExDividendCalendar
from trading_corp.data.macro_calendar import MacroCalendar
from trading_corp.persistence import db
from trading_corp.persistence.models import FillEvent, ProposedOrder
from trading_corp.utils.iv import calc_atm_iv, calc_iv_rank
from trading_corp.utils.market_data import get_vix

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module constants — defaults backstop strategies.yaml when a key is missing
# ---------------------------------------------------------------------------

STRATEGY_SLUG = "robinhood_joint_iron_condor"
AGENT_STATE_KEY = "state"
DEFAULT_DB_URL = "sqlite:///data/trading_corp.db"

# Cadence (seconds) returned from `manage()`, dispatched on the strategy's
# view of the most-stressed open IC.
_CADENCE_TESTED = 300        # any IC has |Δ| ≥ tested_delta_adjust
_CADENCE_WARN = 900          # any IC has |Δ| in the warn band
_CADENCE_IDLE = 1800         # everything healthy, or no positions

# Defaults — strategy config in strategies.yaml is canonical, these are
# only used when the relevant key is missing or unparseable.
_DEFAULTS = {
    "universe": ["SPY", "QQQ", "IWM", "GLD", "TLT"],
    "entry.target_dte": 45,
    "entry.short_delta": 0.16,
    "entry.min_credit_pct_of_width": 0.33,
    "entry.min_ivr": 30,
    "entry.min_ivp": 50,
    "entry.term_structure_max_diff": 0.05,
    "wing_widths": {"SPY": 3.0, "QQQ": 4.0, "IWM": 2.0, "GLD": 2.0, "TLT": 2.0},
    "portfolio_caps.max_per_trade_pct": 0.05,
    "portfolio_caps.max_bp_pct": 0.40,
    "portfolio_caps.max_concurrent": 3,
    "portfolio_caps.max_correlated": 2,
    "management.profit_target_pct": 0.50,
    "management.force_close_dte": 21,
    "management.short_dte_force_close": 7,
    "management.hard_stop_credit_mult": 2.00,
    "management.catastrophic_stop_account_pct": 0.10,
    "management.tested_delta_warn": 0.25,
    "management.tested_delta_adjust": 0.30,
    "management.tested_delta_close_side": 0.35,
    "management.tested_side_neutral_band": 0.05,
    "management.max_adjustments": 1,
    "management.min_dte_for_adjustment": 14,
    "management.ex_div_force_close_within_trading_days": 3,
    "management.ex_div_force_close_short_call_delta": 0.25,
    "management.adjustment_roll_target_short_delta": 0.30,
    "circuit_breaker.consecutive_loss_pause": 3,
    "circuit_breaker.drawdown_pct_pause": 0.15,
    "circuit_breaker.pause_days": 5,
    "paper_simulation.per_leg_slippage_dollars": 0.03,
}

# Correlation map: pairs in the same set count toward `max_correlated`.
# Conservative v1: SPY+QQQ+IWM cross-correlate; GLD and TLT are independent.
_CORRELATION_GROUPS = [
    {"SPY", "QQQ", "IWM"},
]


# ---------------------------------------------------------------------------
# Option-broker protocol — duck-typed; RobinhoodBroker + PaperExecutionBroker
# both satisfy this without inheriting an explicit interface.
# ---------------------------------------------------------------------------

@runtime_checkable
class OptionBroker(Protocol):
    async def snapshot(self) -> Any: ...
    async def quote(self, symbol: str) -> float: ...
    async def get_expiration_dates(self, symbol: str) -> list[str]: ...
    async def get_calls_for_expiry(self, symbol: str, expiry: str) -> list[dict]: ...
    async def get_puts_for_expiry(self, symbol: str, expiry: str) -> list[dict]: ...
    async def get_option_greeks(self, option_id: str) -> dict[str, float | None]: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _today_iso() -> str:
    return _now_utc().date().isoformat()


def _dte(expiry: str, today: date | None = None) -> int:
    try:
        return max(0, (date.fromisoformat(expiry) - (today or date.today())).days)
    except (ValueError, TypeError):
        return 0


def _get_path(cfg: dict, dotted: str, default: Any) -> Any:
    """Dotted-path lookup with default fallback (matches _DEFAULTS keys)."""
    parts = dotted.split(".")
    cur: Any = cfg
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur if cur is not None else default


def _is_us_market_open(now: datetime) -> bool:
    """Crude check: weekday + 13:30-20:00 UTC (covers both EST/EDT
    08:30-15:00 ET window, biased early to capture session opens during
    DST transitions). Used only for the session_start_mark capture gate
    — for which the conservative direction is "capture sooner rather
    than later"."""
    if now.weekday() >= 5:
        return False
    return 13 * 60 + 30 <= (now.hour * 60 + now.minute) <= 20 * 60


# ---------------------------------------------------------------------------
# Default state shape
# ---------------------------------------------------------------------------

def _default_state() -> dict:
    return {
        "open_ics": {},                  # combo_id -> dict
        "circuit_breaker": {
            "consecutive_losses": 0,
            "recent_pnl": [],            # list of realized P&L per closed combo
            "paused_until": None,        # ISO datetime str or None
            "drawdown_hwm": None,        # float account equity high-water mark
        },
        "scan_telemetry": {},            # date_iso -> {symbol: int}
        "last_seen_ts": _now_utc().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Strategy class
# ---------------------------------------------------------------------------

class RobinhoodJointIronCondorAgent:
    """45 DTE iron-condor strategy for the Robinhood Joint division.

    Lifecycle:
      1. `scan(broker, regime)` produces a list of new-IC combos
         (one inner list per candidate = 4 ProposedOrders sharing a
         combo_id) to be routed through Risk → HITL → place_combo.
      2. `manage(broker)` runs the decision tree on every open IC and
         returns `(action_combos, next_cadence_seconds)`.
      3. After place_combo returns successfully, main.py calls back into
         `on_combo_filled(combo_id, fills, intent)` so agent_state
         updates synchronously with the action — never deferred to the
         next manage() tick.
      4. `startup_catchup(broker)` runs one manage() pass on bot startup
         and tags any exits with `startup_catchup` in audit.
    """

    SLUG = STRATEGY_SLUG

    def __init__(
        self,
        strategies_yaml: Path = Path("config/strategies.yaml"),
        macro_calendar: MacroCalendar | None = None,
        ex_dividend_calendar: ExDividendCalendar | None = None,
        *,
        db_url: str | None = None,
        clock_fn: Any = _now_utc,         # injectable for tests
    ) -> None:
        self._strategies_yaml = Path(strategies_yaml)
        self._mtime: float = 0.0
        self._cfg: dict = {}
        self._db_url = db_url or DEFAULT_DB_URL
        self._macro = macro_calendar or MacroCalendar.load()
        self._exdiv = ex_dividend_calendar or ExDividendCalendar.load()
        self._clock = clock_fn

        # Pending-combo registry: combo_id → state-update payload prepared
        # at proposal time, consumed by on_combo_filled. Kept in process
        # memory because the callback fires within the same scan/manage
        # tick that proposed the combo; a process restart between propose
        # and fill is impossible without first losing the place_combo
        # call too.
        self._pending: dict[str, dict] = {}

        self._reload()

    # ------------------------------------------------------------------
    # Config hot-reload
    # ------------------------------------------------------------------

    def _reload(self) -> None:
        try:
            mtime = self._strategies_yaml.stat().st_mtime
        except FileNotFoundError:
            self._cfg = {}
            return
        if mtime == self._mtime and self._cfg:
            return
        try:
            with self._strategies_yaml.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            log.warning("IronCondor: failed to load %s: %s", self._strategies_yaml, e)
            return
        self._cfg = data.get(STRATEGY_SLUG, {}) or {}
        self._mtime = mtime
        # Validate-and-warn once per mtime change. Only fires when the
        # strategy is enabled — config gaps on a disabled strategy don't
        # need to be loud since nothing is reading them.
        if self._cfg.get("enabled"):
            self._warn_on_missing_config()

    def _warn_on_missing_config(self) -> None:
        """Log a single warning enumerating any required config keys that
        are absent from strategies.yaml and would silently fall back to
        the in-code _DEFAULTS. Fires once per successful reload so the
        operator sees the gap at startup without log spam on each scan.
        """
        missing: list[str] = []
        for dotted in _DEFAULTS.keys():
            # _DEFAULTS keys may be top-level or dotted. Treat strings
            # without "." as top-level and check directly.
            if "." not in dotted:
                if dotted not in self._cfg:
                    missing.append(dotted)
                continue
            parts = dotted.split(".")
            cur: Any = self._cfg
            ok = True
            for p in parts:
                if not isinstance(cur, dict) or p not in cur:
                    ok = False
                    break
                cur = cur[p]
            if not ok:
                missing.append(dotted)
        if missing:
            log.warning(
                "IronCondor config: %d required key(s) missing from %s — "
                "using built-in defaults: %s",
                len(missing), self._strategies_yaml, ", ".join(missing),
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        self._reload()
        return bool(self._cfg.get("enabled", False))

    @property
    def auto_execute(self) -> bool:
        self._reload()
        return bool(self._cfg.get("auto_execute", False))

    @property
    def division(self) -> str:
        self._reload()
        return str(self._cfg.get("division", "robinhood_joint"))

    @property
    def universe(self) -> list[str]:
        self._reload()
        return list(self._cfg.get("universe") or _DEFAULTS["universe"])

    def cfg(self, dotted: str) -> Any:
        """Public config-lookup helper. `dotted` matches _DEFAULTS keys."""
        self._reload()
        return _get_path(self._cfg, dotted, _DEFAULTS[dotted])

    @property
    def wing_widths(self) -> dict[str, float]:
        self._reload()
        return dict(self._cfg.get("wing_widths") or _DEFAULTS["wing_widths"])

    # ------------------------------------------------------------------
    # agent_state I/O — single blob per the strategy slug
    # ------------------------------------------------------------------

    def load_state(self) -> dict:
        loaded = db.load_agent_state(STRATEGY_SLUG, AGENT_STATE_KEY, self._db_url)
        if loaded is None:
            return _default_state()
        value, _ts = loaded
        if not isinstance(value, dict):
            return _default_state()
        state = _default_state()
        state.update(value)
        # Force-fill any missing top-level keys (covers schema drift).
        for k, v in _default_state().items():
            state.setdefault(k, v)
        return state

    def persist_state(self, state: dict) -> None:
        state["last_seen_ts"] = _now_utc().isoformat(timespec="seconds")
        db.set_agent_state(STRATEGY_SLUG, AGENT_STATE_KEY, state, self._db_url)

    # ------------------------------------------------------------------
    # Scan path — daily entry
    # ------------------------------------------------------------------

    async def scan(
        self,
        broker: OptionBroker,
        regime: str = "neutral",
    ) -> list[list[ProposedOrder]]:
        """Daily entry pass. Returns a list of candidate combos.

        Empty result is a valid outcome — does not indicate failure. See
        the module docstring for the full filter list and the
        `scan_passes_with_no_candidates` audit counter.
        """
        if not self.enabled:
            log.info("IronCondor: strategy disabled — scan skipped")
            return []

        state = self.load_state()
        if self._is_paused(state):
            log.info("IronCondor scan: circuit breaker paused until %s",
                     state["circuit_breaker"]["paused_until"])
            return []

        # Global gates: macro calendar + VIX.
        macro_window = self.cfg("management.ex_div_force_close_within_trading_days")
        # 5 trading days ≈ 7200 minutes (calendar; conservative) for halt window
        if self._macro.is_within_halt_window(
            self._clock(), window_minutes=7200, impact_levels=("high",),
        )[0]:
            self._tally_scan_filter(state, self.universe, "macro_halt")
            self.persist_state(state)
            log.info("IronCondor scan: macro halt window — skipping all symbols")
            return []
        vix = get_vix()
        if vix is not None and vix > 30:
            self._tally_scan_filter(state, self.universe, "vix_above_30")
            self.persist_state(state)
            log.info("IronCondor scan: VIX %.2f > 30 — skipping new opens", vix)
            return []

        snap = await broker.snapshot()
        equity = float(getattr(snap, "equity", 0.0) or 0.0)

        candidates: list[list[ProposedOrder]] = []
        for symbol in self.universe:
            try:
                combo = await self._maybe_construct_for_symbol(
                    broker, symbol, equity, state,
                )
            except Exception as e:
                log.warning(
                    "IronCondor scan(%s): error during construction: %s",
                    symbol, e,
                )
                combo = None
            if combo is None:
                continue
            candidates.append(combo)

        self.persist_state(state)
        return candidates

    async def _maybe_construct_for_symbol(
        self,
        broker: OptionBroker,
        symbol: str,
        equity: float,
        state: dict,
    ) -> list[ProposedOrder] | None:
        # IVR gate.
        ivr_decimal = await calc_iv_rank(symbol)
        min_ivr = float(self.cfg("entry.min_ivr"))
        if ivr_decimal * 100 < min_ivr:
            self._tally_scan_filter(state, [symbol], f"ivr_below_{int(min_ivr)}")
            return None

        # Term-structure gate: front-month ATM IV must not exceed
        # 60-90 DTE ATM IV by more than `term_structure_max_diff`.
        if not await self._term_structure_ok(symbol):
            self._tally_scan_filter(state, [symbol], "term_structure_backwardated")
            return None

        # Ex-div window — opening into an ex-div within 3 trading days
        # invites assignment risk we don't yet structurally avoid; skip.
        if self._exdiv.is_within_window(
            symbol, self._clock(),
            trading_days=int(self.cfg("management.ex_div_force_close_within_trading_days")),
        ):
            self._tally_scan_filter(state, [symbol], "ex_dividend_window")
            return None

        # Portfolio caps (sized after we know what we'd propose).
        # We do a coarse-grained underlying-level preflight first;
        # max-loss preflight runs after we've built the legs.
        ok, reason = self._preflight_underlying(state, symbol)
        if not ok:
            self._tally_scan_filter(state, [symbol], f"preflight:{reason}")
            return None

        # Build the IC.
        combo = await self._construct_ic(
            broker, symbol, equity, ivr_decimal, state,
        )
        if combo is None:
            return None
        return combo

    async def _term_structure_ok(self, symbol: str) -> bool:
        target_dte = int(self.cfg("entry.target_dte"))
        max_diff = float(self.cfg("entry.term_structure_max_diff"))
        front = await calc_atm_iv(symbol, target_dte, tolerance_days=7)
        back = await calc_atm_iv(symbol, 75, tolerance_days=15)
        if front is None or back is None:
            # Data gap — fail open (don't block) but log; the macro halt
            # and VIX gates already catch the stress regimes the
            # backwardation check is meant to catch.
            log.info(
                "IronCondor: term-structure check skipped for %s "
                "(front=%s back=%s)", symbol, front, back,
            )
            return True
        return (front - back) <= max_diff

    async def _construct_ic(
        self,
        broker: OptionBroker,
        symbol: str,
        equity: float,
        ivr_decimal: float,
        state: dict,
    ) -> list[ProposedOrder] | None:
        # Pick an expiration close to target DTE.
        expirations = await broker.get_expiration_dates(symbol)
        target_dte = int(self.cfg("entry.target_dte"))
        expiry = self._pick_expiry(expirations, target_dte)
        if expiry is None:
            log.info("IronCondor: no expiration near target_dte for %s", symbol)
            return None
        chain_dte = _dte(expiry, self._clock().date())

        calls = await broker.get_calls_for_expiry(symbol, expiry)
        puts = await broker.get_puts_for_expiry(symbol, expiry)
        if not calls or not puts:
            log.info("IronCondor: empty chain for %s %s", symbol, expiry)
            return None

        # Spot for wing-width offset math.
        spot = await broker.quote(symbol)
        if not spot or spot <= 0:
            log.warning("IronCondor: invalid spot for %s — skipping", symbol)
            return None

        short_target = float(self.cfg("entry.short_delta"))
        wing_width = float(self.wing_widths.get(symbol, 3.0))

        # Pick shorts by delta (call: delta closest to +short_target;
        # put: delta closest to -short_target).
        short_call = self._pick_by_delta(calls, +short_target)
        short_put = self._pick_by_delta(puts, -short_target)
        if not short_call or not short_put:
            log.info("IronCondor: could not pick short strikes for %s", symbol)
            return None

        # Longs are placed by dollar-width offset — NOT by long-delta
        # target (per the plan's blocking-issue-1 resolution). Pick the
        # closest available strike at wing_width above/below the short.
        long_call = self._pick_by_strike(
            calls, float(short_call["strike_price"]) + wing_width,
        )
        long_put = self._pick_by_strike(
            puts, float(short_put["strike_price"]) - wing_width,
        )
        if not long_call or not long_put:
            log.info("IronCondor: could not pick long strikes for %s", symbol)
            return None

        net_credit = self._compute_net_credit(short_put, long_put, short_call, long_call)
        if net_credit is None or net_credit <= 0:
            log.info("IronCondor: non-positive net credit for %s — skipping", symbol)
            return None
        min_credit = float(self.cfg("entry.min_credit_pct_of_width")) * wing_width
        if net_credit < min_credit:
            log.info(
                "IronCondor %s: credit %.2f below min %.2f (%.0f%% of width %.2f)",
                symbol, net_credit, min_credit,
                float(self.cfg("entry.min_credit_pct_of_width")) * 100, wing_width,
            )
            return None

        # Sizing — per-trade risk cap, in contracts.
        max_loss_per_contract_dollars = (wing_width - net_credit) * 100.0
        if max_loss_per_contract_dollars <= 0:
            return None
        cap_dollars = equity * float(self.cfg("portfolio_caps.max_per_trade_pct"))
        max_contracts = int(cap_dollars // max_loss_per_contract_dollars)
        if max_contracts < 1:
            log.info(
                "IronCondor %s: per-trade cap $%.2f below 1-contract max-loss $%.2f",
                symbol, cap_dollars, max_loss_per_contract_dollars,
            )
            return None

        # Portfolio max-loss preflight (BP + concurrent + correlation).
        ok, reason = self._preflight_with_size(
            state, symbol, max_loss_per_contract_dollars * max_contracts,
        )
        if not ok:
            log.info("IronCondor %s: preflight rejected (%s)", symbol, reason)
            return None

        contracts = max_contracts
        combo_id = str(uuid.uuid4())

        legs = self._build_combo_legs(
            symbol=symbol,
            expiry=expiry,
            short_put=short_put,
            long_put=long_put,
            short_call=short_call,
            long_call=long_call,
            contracts=contracts,
            combo_id=combo_id,
            direction="credit",
            net_limit_price=net_credit,
            intent="open",
        )
        # Stamp IVR-at-entry on every opening leg's extra so it propagates
        # to position.extra_json via _persist_combo_positions. Closed
        # combos lose `agent_state.open_ics` after the close-callback
        # pops them, so the position row is the only durable record of
        # entry IVR for the win-rate-by-IVR telemetry query.
        ivr_at_entry_pct = round(float(ivr_decimal) * 100, 1)
        for leg in legs:
            if leg.extra is not None:
                leg.extra["ic_underlying_iv_rank_at_entry"] = ivr_at_entry_pct

        # Stash the open-IC payload in the pending registry for
        # on_combo_filled to commit to agent_state on success.
        long_call_delta = self._safe_float(long_call.get("delta"))
        long_put_delta = self._safe_float(long_put.get("delta"))
        self._pending[combo_id] = {
            "intent": "open",
            "symbol": symbol,
            "expiration": expiry,
            "dte_at_entry": chain_dte,
            "ivr_at_entry": float(ivr_decimal),
            "wing_width": wing_width,
            "credit_at_entry": net_credit,
            "contracts": contracts,
            "max_loss_per_contract": max_loss_per_contract_dollars,
            "short_put_strike": float(short_put["strike_price"]),
            "long_put_strike": float(long_put["strike_price"]),
            "short_call_strike": float(short_call["strike_price"]),
            "long_call_strike": float(long_call["strike_price"]),
            "short_put_option_id": short_put.get("option_id"),
            "long_put_option_id": long_put.get("option_id"),
            "short_call_option_id": short_call.get("option_id"),
            "long_call_option_id": long_call.get("option_id"),
            "short_put_delta_at_entry": self._safe_float(short_put.get("delta")),
            "short_call_delta_at_entry": self._safe_float(short_call.get("delta")),
            "long_put_delta_at_entry": long_put_delta,
            "long_call_delta_at_entry": long_call_delta,
        }
        return legs

    # ------------------------------------------------------------------
    # Manage path — decision tree
    # ------------------------------------------------------------------

    async def manage(
        self, broker: OptionBroker,
    ) -> tuple[list[list[ProposedOrder]], int]:
        """Position-management tick. Returns (combos, next_cadence_seconds).

        Branch order is load-bearing — see module docstring.
        """
        if not self.enabled:
            return [], _CADENCE_IDLE

        state = self.load_state()

        # Capture session_start_mark at the first tick after 09:30 ET
        # each day (imprecise by up to one cadence interval — documented
        # in _compute_session_pnl).
        await self._maybe_capture_session_marks(broker, state)

        # Repause check — circuit breaker can re-fire on pause expiry.
        self._check_repause(state)

        # Branch 0: portfolio-level catastrophic stop.
        actions: list[list[ProposedOrder]] = []
        if state["open_ics"]:
            session_pnl = await self._compute_session_pnl(broker, state)
            snap = await broker.snapshot()
            equity = float(getattr(snap, "equity", 0.0) or 0.0)
            cap_pct = float(self.cfg("management.catastrophic_stop_account_pct"))
            if equity > 0 and session_pnl / equity <= -cap_pct:
                log.warning(
                    "IronCondor branch 0: catastrophic stop — session_pnl=%.2f "
                    "equity=%.2f (%.1f%% threshold)",
                    session_pnl, equity, cap_pct * 100,
                )
                for combo_id, ic in list(state["open_ics"].items()):
                    legs = await self._build_close_combo(
                        broker, combo_id, ic, intent="catastrophic_stop",
                    )
                    if legs:
                        actions.append(legs)
                self.persist_state(state)
                return actions, _CADENCE_TESTED

        # Per-IC branches 1-9.
        for combo_id, ic in list(state["open_ics"].items()):
            result = await self._evaluate_ic(broker, combo_id, ic)
            if result is not None:
                actions.append(result)

        cadence = await self._compute_cadence(broker, state)
        self.persist_state(state)
        return actions, cadence

    async def _evaluate_ic(
        self, broker: OptionBroker, combo_id: str, ic: dict,
    ) -> list[ProposedOrder] | None:
        """Run branches 1-9 in order for one open IC. Returns the close/
        adjustment combo or None if no branch fires."""
        symbol = ic["symbol"]
        credit_at_entry = float(ic["credit_at_entry"])
        wing_width = float(ic["wing_width"])
        dte = _dte(ic["expiration"], self._clock().date())

        # Current mark to close — needed for branches 1 and 4.5.
        close_cost = await self._current_close_cost(broker, ic)
        combo_pnl = (credit_at_entry - close_cost) if close_cost is not None else None

        # Branch 1: 50% profit target.
        profit_target_pct = float(self.cfg("management.profit_target_pct"))
        if combo_pnl is not None and combo_pnl >= credit_at_entry * profit_target_pct:
            log.info("IronCondor branch 1: profit target on %s combo %s",
                     symbol, combo_id)
            return await self._build_close_combo(broker, combo_id, ic,
                                                 intent="profit_target")

        # Branch 2: 21 DTE.
        force_dte = int(self.cfg("management.force_close_dte"))
        if dte <= force_dte and dte >= int(self.cfg("management.short_dte_force_close")):
            log.info("IronCondor branch 2: 21-DTE force close on %s combo %s",
                     symbol, combo_id)
            return await self._build_close_combo(broker, combo_id, ic,
                                                 intent="force_close_dte")

        # Branch 3: DTE < 7.
        if dte < int(self.cfg("management.short_dte_force_close")):
            log.warning(
                "IronCondor branch 3: late-DTE force close on %s combo %s (dte=%d)",
                symbol, combo_id, dte,
            )
            return await self._build_close_combo(broker, combo_id, ic,
                                                 intent="late_dte_force_close")

        # Branch 4: ex-dividend force close (short call assignment risk).
        if self._exdiv.is_within_window(
            symbol, self._clock(),
            trading_days=int(self.cfg("management.ex_div_force_close_within_trading_days")),
        ):
            # Put-side ex-div drop risk is NOT handled here. ETF
            # dividends in our universe are <0.5% of price per ex-date
            # — within normal daily noise. Re-evaluate if the universe
            # expands to higher-yield names.
            short_call_id = ic.get("short_call_option_id")
            current_delta = None
            if short_call_id:
                try:
                    gk = await broker.get_option_greeks(short_call_id)
                    current_delta = self._safe_float(gk.get("delta"))
                except Exception:
                    current_delta = None
            threshold = float(self.cfg("management.ex_div_force_close_short_call_delta"))
            if current_delta is not None and current_delta > threshold:
                log.info(
                    "IronCondor branch 4: ex-div force close on %s combo %s "
                    "(short call Δ %.2f > %.2f)",
                    symbol, combo_id, current_delta, threshold,
                )
                return await self._build_close_combo(broker, combo_id, ic,
                                                     intent="ex_div_force_close")

        # Branch 4.5: per-position hard stop.
        hard_stop_mult = float(self.cfg("management.hard_stop_credit_mult"))
        if combo_pnl is not None and combo_pnl <= -hard_stop_mult * credit_at_entry:
            log.warning(
                "IronCondor branch 4.5: hard stop on %s combo %s "
                "(pnl=%.2f credit=%.2f mult=%.2f)",
                symbol, combo_id, combo_pnl, credit_at_entry, hard_stop_mult,
            )
            return await self._build_close_combo(broker, combo_id, ic,
                                                 intent="hard_stop")

        # Branch 5: tested-side identification.
        tested = await self._identify_tested_side(broker, ic)
        if tested == "neither":
            return None

        # Resolve current tested-side delta + untested-side mark for
        # branch logic below.
        tested_delta = await self._current_short_delta(broker, ic, tested)
        if tested_delta is None:
            return None

        warn = float(self.cfg("management.tested_delta_warn"))
        adjust = float(self.cfg("management.tested_delta_adjust"))
        close_side = float(self.cfg("management.tested_delta_close_side"))
        abs_delta = abs(tested_delta)

        # Branch 6: warn only.
        if warn <= abs_delta < adjust:
            log.info(
                "IronCondor branch 6: warn on %s combo %s (tested Δ %.2f side %s)",
                symbol, combo_id, abs_delta, tested,
            )
            return None

        if adjust <= abs_delta < close_side:
            # Branch 7 vs 8 — depends on adjustment slot + DTE + untested-mark.
            min_dte_adj = int(self.cfg("management.min_dte_for_adjustment"))
            max_adjs = int(self.cfg("management.max_adjustments"))
            adj_count = int(ic.get("adjustment_count", 0))
            untested_mark = await self._untested_side_mark(broker, ic, tested)

            can_adjust = (
                adj_count < max_adjs
                and dte > min_dte_adj
                and untested_mark is not None
                and untested_mark > 0.10
            )
            if can_adjust:
                log.info(
                    "IronCondor branch 7: adjustment 1 on %s combo %s "
                    "(tested %s Δ %.2f dte=%d)",
                    symbol, combo_id, tested, abs_delta, dte,
                )
                return await self._build_adjustment_1(
                    broker, combo_id, ic, tested,
                )
            log.info(
                "IronCondor branch 8: close tested side on %s combo %s "
                "(tested %s Δ %.2f dte=%d adj=%d)",
                symbol, combo_id, tested, abs_delta, dte, adj_count,
            )
            return await self._build_close_tested_side(
                broker, combo_id, ic, tested, intent="close_tested_side",
            )

        # Branch 9: |Δ| ≥ 0.35 OR underlying through short strike.
        spot = await broker.quote(symbol)
        through_strike = False
        if spot:
            if tested == "call" and spot >= float(ic["short_call_strike"]):
                through_strike = True
            if tested == "put" and spot <= float(ic["short_put_strike"]):
                through_strike = True
        if abs_delta >= close_side or through_strike:
            log.info(
                "IronCondor branch 9: close tested side on %s combo %s "
                "(tested %s Δ %.2f through_strike=%s)",
                symbol, combo_id, tested, abs_delta, through_strike,
            )
            return await self._build_close_tested_side(
                broker, combo_id, ic, tested, intent="close_tested_side",
            )
        return None

    # ------------------------------------------------------------------
    # Branch helpers
    # ------------------------------------------------------------------

    async def _identify_tested_side(
        self, broker: OptionBroker, ic: dict,
    ) -> Literal["call", "put", "neither"]:
        """Return which short leg has moved against entry the most.

        Rules:
          - "neither" if both shorts have |current_Δ - entry_Δ| <
            tested_side_neutral_band (default 0.05).
          - Whichever side has |current_Δ| > |entry_Δ| (moved against).
          - If both moved against: whichever has higher absolute current Δ.
        """
        band = float(self.cfg("management.tested_side_neutral_band"))

        sc_id = ic.get("short_call_option_id")
        sp_id = ic.get("short_put_option_id")
        sc_entry = self._safe_float(ic.get("short_call_delta_at_entry"))
        sp_entry = self._safe_float(ic.get("short_put_delta_at_entry"))
        sc_current = await self._current_short_delta(broker, ic, "call")
        sp_current = await self._current_short_delta(broker, ic, "put")

        # Treat missing data as "undetermined".
        if sc_current is None or sp_current is None:
            return "neither"
        if sc_entry is None or sp_entry is None:
            return "neither"

        call_drift = abs(sc_current) - abs(sc_entry)
        put_drift = abs(sp_current) - abs(sp_entry)

        call_quiet = abs(sc_current - sc_entry) < band
        put_quiet = abs(sp_current - sp_entry) < band
        if call_quiet and put_quiet:
            return "neither"

        # Both moved against — pick the higher |current Δ|.
        if call_drift > 0 and put_drift > 0:
            return "call" if abs(sc_current) >= abs(sp_current) else "put"
        if call_drift > 0:
            return "call"
        if put_drift > 0:
            return "put"
        # Both moved IN (Δ shrank toward 0) — not tested.
        return "neither"

    async def _current_short_delta(
        self, broker: OptionBroker, ic: dict, side: str,
    ) -> float | None:
        opt_id = ic.get(f"short_{side}_option_id")
        if not opt_id:
            return None
        try:
            gk = await broker.get_option_greeks(opt_id)
        except Exception:
            return None
        return self._safe_float(gk.get("delta"))

    async def _untested_side_mark(
        self, broker: OptionBroker, ic: dict, tested: str,
    ) -> float | None:
        """Return the mark of the untested-side SHORT leg (the one that
        we'd buy back to close the untested vertical)."""
        untested = "put" if tested == "call" else "call"
        opt_id = ic.get(f"short_{untested}_option_id")
        if not opt_id:
            return None
        try:
            gk = await broker.get_option_greeks(opt_id)
        except Exception:
            return None
        return self._safe_float(gk.get("mark_price"))

    async def _current_close_cost(
        self, broker: OptionBroker, ic: dict,
    ) -> float | None:
        """Return the per-share net debit it would take to close the IC
        as-is (buy back shorts + sell longs). None if any mark missing.
        """
        marks: dict[str, float] = {}
        for role in ("short_put", "long_put", "short_call", "long_call"):
            opt_id = ic.get(f"{role}_option_id")
            if not opt_id:
                return None
            try:
                gk = await broker.get_option_greeks(opt_id)
            except Exception:
                return None
            m = self._safe_float(gk.get("mark_price"))
            if m is None:
                return None
            marks[role] = m
        # Net debit to close = (buy_back_short_call + buy_back_short_put)
        #                    - (sell_long_call + sell_long_put)
        return (
            marks["short_call"] + marks["short_put"]
            - marks["long_call"] - marks["long_put"]
        )

    # ------------------------------------------------------------------
    # Combo construction
    # ------------------------------------------------------------------

    def _build_combo_legs(
        self,
        *,
        symbol: str,
        expiry: str,
        short_put: dict,
        long_put: dict,
        short_call: dict,
        long_call: dict,
        contracts: int,
        combo_id: str,
        direction: str,
        net_limit_price: float,
        intent: str,
        position_effects: dict[str, str] | None = None,
    ) -> list[ProposedOrder]:
        """Construct the 4 ProposedOrders for an IC open. Each leg shares
        combo_id and combo_direction; per-leg position_effect defaults
        to "open" (can be overridden per leg via `position_effects`).
        """
        effects = position_effects or {
            "short_put": "open", "long_put": "open",
            "short_call": "open", "long_call": "open",
        }
        legs_spec = [
            ("short_put",  "sell", short_put,  "put"),
            ("long_put",   "buy",  long_put,   "put"),
            ("short_call", "sell", short_call, "call"),
            ("long_call",  "buy",  long_call,  "call"),
        ]
        out: list[ProposedOrder] = []
        for role, side, contract, otype in legs_spec:
            out.append(self._build_leg(
                symbol=symbol, expiry=expiry,
                strike=float(contract["strike_price"]),
                option_type=otype, side=side, role=role,
                effect=effects[role],
                option_id=contract.get("option_id"),
                contracts=contracts, combo_id=combo_id,
                direction=direction, net_limit_price=net_limit_price,
                intent=intent,
                limit_price=self._safe_float(contract.get("mark_price")) or 0.0,
            ))
        return out

    def _build_leg(
        self, *,
        symbol: str, expiry: str, strike: float, option_type: str,
        side: str, role: str, effect: str, option_id: str | None,
        contracts: int, combo_id: str, direction: str,
        net_limit_price: float, intent: str, limit_price: float,
    ) -> ProposedOrder:
        return ProposedOrder(
            strategy=STRATEGY_SLUG,
            symbol=symbol,
            side=side,   # type: ignore[arg-type]
            qty=float(contracts),
            order_type="limit",
            limit_price=float(limit_price),
            rationale=f"IC {intent} leg {role}",
            extra={
                "is_option": True,
                "is_multi_leg": True,
                "combo_id": combo_id,
                "combo_role": role,
                "combo_direction": direction,
                "combo_intent": intent,
                "net_limit_price": float(net_limit_price),
                "underlying": symbol,
                "expiration": expiry,
                "strike": float(strike),
                "option_type": option_type,
                "position_effect": effect,
                "ratio_quantity": 1,
                "option_id": option_id,
            },
        )

    async def _build_close_combo(
        self, broker: OptionBroker, combo_id: str, ic: dict, *, intent: str,
    ) -> list[ProposedOrder] | None:
        """Build a 4-leg debit close for the entire IC."""
        # Current marks per leg.
        marks = {}
        for role in ("short_put", "long_put", "short_call", "long_call"):
            opt_id = ic.get(f"{role}_option_id")
            if not opt_id:
                return None
            try:
                gk = await broker.get_option_greeks(opt_id)
            except Exception:
                return None
            m = self._safe_float(gk.get("mark_price"))
            if m is None:
                return None
            marks[role] = m

        # Net debit to close = pay to buy shorts back, receive from selling longs.
        net_debit = (
            marks["short_call"] + marks["short_put"]
            - marks["long_call"] - marks["long_put"]
        )
        if net_debit <= 0:
            # The combo is at/below zero cost — set a tiny positive debit
            # so the combo POST validates. The atomic combo will fill at
            # market.
            net_debit = 0.01

        legs = [
            ("short_put",  "buy",  "put",  ic["short_put_strike"],
             ic.get("short_put_option_id"), marks["short_put"]),
            ("long_put",   "sell", "put",  ic["long_put_strike"],
             ic.get("long_put_option_id"), marks["long_put"]),
            ("short_call", "buy",  "call", ic["short_call_strike"],
             ic.get("short_call_option_id"), marks["short_call"]),
            ("long_call",  "sell", "call", ic["long_call_strike"],
             ic.get("long_call_option_id"), marks["long_call"]),
        ]
        out: list[ProposedOrder] = []
        for role, side, otype, strike, opt_id, mark in legs:
            out.append(self._build_leg(
                symbol=ic["symbol"], expiry=ic["expiration"],
                strike=float(strike), option_type=otype, side=side,
                role=role, effect="close", option_id=opt_id,
                contracts=int(ic["contracts"]), combo_id=combo_id,
                direction="debit", net_limit_price=net_debit,
                intent=intent, limit_price=mark,
            ))
        # Stash pending close payload — on_combo_filled will close out
        # the IC in state and compute realized P&L.
        self._pending[combo_id] = {
            "intent": "close",
            "close_kind": intent,
            "combo_id": combo_id,
        }
        return out

    async def _build_close_tested_side(
        self, broker: OptionBroker, combo_id: str, ic: dict, tested: str,
        *, intent: str,
    ) -> list[ProposedOrder] | None:
        """Close just the 2 legs of the tested side (buy back short, sell
        long). Leaves the untested vertical to continue decaying."""
        short_id = ic.get(f"short_{tested}_option_id")
        long_id = ic.get(f"long_{tested}_option_id")
        if not short_id or not long_id:
            return None
        marks: dict[str, float] = {}
        for r, oid in (("short", short_id), ("long", long_id)):
            try:
                gk = await broker.get_option_greeks(oid)
            except Exception:
                return None
            m = self._safe_float(gk.get("mark_price"))
            if m is None:
                return None
            marks[r] = m
        net_debit = marks["short"] - marks["long"]
        if net_debit <= 0:
            net_debit = 0.01

        new_combo_id = str(uuid.uuid4())     # separate combo (2-leg close)
        out: list[ProposedOrder] = []
        legs = [
            ("short", "buy",  ic[f"short_{tested}_strike"], short_id, marks["short"]),
            ("long",  "sell", ic[f"long_{tested}_strike"],  long_id,  marks["long"]),
        ]
        for r, side, strike, opt_id, mark in legs:
            role = f"{r}_{tested}"
            out.append(self._build_leg(
                symbol=ic["symbol"], expiry=ic["expiration"],
                strike=float(strike), option_type=tested, side=side,
                role=role, effect="close", option_id=opt_id,
                contracts=int(ic["contracts"]), combo_id=new_combo_id,
                direction="debit", net_limit_price=net_debit,
                intent=intent, limit_price=mark,
            ))
        self._pending[new_combo_id] = {
            "intent": "close_tested_side",
            "tested_side": tested,
            "parent_combo_id": combo_id,
            "close_kind": intent,
        }
        return out

    async def _build_adjustment_1(
        self, broker: OptionBroker, combo_id: str, ic: dict, tested: str,
    ) -> list[ProposedOrder] | None:
        """Single 4-leg atomic combo: close the untested vertical (2
        legs) + open new untested vertical at Δ 0.30 (2 legs). Mixed
        open/close effects in one POST per the step-1 design.
        """
        untested = "put" if tested == "call" else "call"
        target_delta = float(self.cfg("management.adjustment_roll_target_short_delta"))
        wing_width = float(ic["wing_width"])

        # Need to pick the NEW untested-side short at Δ 0.30 (sign-adjusted).
        if untested == "call":
            chain = await broker.get_calls_for_expiry(ic["symbol"], ic["expiration"])
            sign = +1
        else:
            chain = await broker.get_puts_for_expiry(ic["symbol"], ic["expiration"])
            sign = -1
        if not chain:
            return None
        new_short = self._pick_by_delta(chain, sign * target_delta)
        if new_short is None:
            return None
        if untested == "call":
            new_long_strike = float(new_short["strike_price"]) + wing_width
        else:
            new_long_strike = float(new_short["strike_price"]) - wing_width
        new_long = self._pick_by_strike(chain, new_long_strike)
        if new_long is None:
            return None

        # Old untested-side contract dicts — looked up by stashed strike.
        old_short_id = ic.get(f"short_{untested}_option_id")
        old_long_id = ic.get(f"long_{untested}_option_id")
        if not old_short_id or not old_long_id:
            return None

        # Marks on old legs (close cost) and new legs (open credit).
        async def _mark(oid):
            try:
                gk = await broker.get_option_greeks(oid)
            except Exception:
                return None
            return self._safe_float(gk.get("mark_price"))

        old_short_mark = await _mark(old_short_id)
        old_long_mark = await _mark(old_long_id)
        new_short_mark = self._safe_float(new_short.get("mark_price"))
        new_long_mark = self._safe_float(new_long.get("mark_price"))
        if None in (old_short_mark, old_long_mark, new_short_mark, new_long_mark):
            return None

        # Net cashflow for the adjustment combo (per share):
        #   close untested:  pay old_short_mark, receive old_long_mark
        #   open new:        receive new_short_mark, pay new_long_mark
        net_cashflow = (
            -old_short_mark + old_long_mark
            + new_short_mark - new_long_mark
        )
        # Direction is whichever sign of the net cashflow we'd see.
        if net_cashflow >= 0:
            direction = "credit"
            net_limit_price = max(net_cashflow, 0.01)
        else:
            direction = "debit"
            net_limit_price = max(-net_cashflow, 0.01)

        new_combo_id = str(uuid.uuid4())
        intent = "adjustment_1"

        # Build 4 legs in a canonical order so cashflow signs match
        # combo_direction. The PaperExecutionBroker + RobinhoodBroker
        # accept arbitrary leg order — the combo engine sums signed
        # premiums per ratio.
        legs_spec = [
            (f"old_short_{untested}", "buy",  untested, ic[f"short_{untested}_strike"],
             old_short_id, old_short_mark, "close"),
            (f"old_long_{untested}",  "sell", untested, ic[f"long_{untested}_strike"],
             old_long_id,  old_long_mark,  "close"),
            (f"new_short_{untested}", "sell", untested, float(new_short["strike_price"]),
             new_short.get("option_id"), new_short_mark, "open"),
            (f"new_long_{untested}",  "buy",  untested, float(new_long["strike_price"]),
             new_long.get("option_id"),  new_long_mark,  "open"),
        ]
        out: list[ProposedOrder] = []
        for role, side, otype, strike, opt_id, mark, effect in legs_spec:
            out.append(self._build_leg(
                symbol=ic["symbol"], expiry=ic["expiration"],
                strike=float(strike), option_type=otype, side=side,
                role=role, effect=effect, option_id=opt_id,
                contracts=int(ic["contracts"]), combo_id=new_combo_id,
                direction=direction, net_limit_price=net_limit_price,
                intent=intent, limit_price=mark,
            ))

        self._pending[new_combo_id] = {
            "intent": "adjustment_1",
            "parent_combo_id": combo_id,
            "untested_side": untested,
            "new_short_strike": float(new_short["strike_price"]),
            "new_long_strike": float(new_long["strike_price"]),
            "new_short_option_id": new_short.get("option_id"),
            "new_long_option_id": new_long.get("option_id"),
            "new_short_delta_at_entry": self._safe_float(new_short.get("delta")),
        }
        return out

    # ------------------------------------------------------------------
    # on_combo_filled callback — main.py invokes after place_combo
    # ------------------------------------------------------------------

    def on_combo_filled(
        self, combo_id: str, fills: list[FillEvent],
    ) -> None:
        """Update agent_state for the just-filled combo.

        Synchronous with the action that triggered it — never deferred.
        Realized P&L flows into the circuit-breaker counter for closes
        and adjustments that complete a position.
        """
        pending = self._pending.pop(combo_id, None)
        if not pending:
            log.warning(
                "IronCondor on_combo_filled: no pending entry for combo %s — "
                "state will not be updated (orphaned fill)", combo_id,
            )
            return
        intent = pending["intent"]
        state = self.load_state()

        if intent == "open":
            state["open_ics"][combo_id] = self._pending_to_open_ic_payload(pending)
        elif intent == "close":
            parent_id = combo_id
            ic = state["open_ics"].pop(parent_id, None)
            if ic is not None:
                realized = self._realized_pnl_from_close(ic, fills)
                # Emit the lifecycle-closed audit BEFORE the state mutation
                # so telemetry downstream can reconstruct the close even if
                # the persist_state call later in this method fails.
                contracts = int(ic.get("contracts", 1))
                self._emit_lifecycle_audit("ic_lifecycle_closed", {
                    "combo_id": parent_id,
                    "symbol": ic.get("symbol"),
                    "ivr_at_entry": ic.get("ivr_at_entry"),
                    "dte_at_entry": ic.get("dte_at_entry"),
                    "credit_at_entry": ic.get("credit_at_entry"),
                    "wing_width": ic.get("wing_width"),
                    "contracts": contracts,
                    "adjustment_count": int(ic.get("adjustment_count", 0)),
                    "realized_pnl_per_share": realized,
                    "realized_pnl_dollars": realized * 100.0 * contracts,
                    "close_kind": pending.get("close_kind", "unknown"),
                })
                self._on_combo_closed_pnl(state, realized)
        elif intent == "close_tested_side":
            parent = pending.get("parent_combo_id")
            ic = state["open_ics"].get(parent)
            if ic is not None:
                # Half-close: mark which side is now flat. Future
                # branches will see the closed side has no leg.
                tested = pending.get("tested_side")
                if tested:
                    for role in (f"short_{tested}", f"long_{tested}"):
                        ic[f"{role}_strike"] = None
                        ic[f"{role}_option_id"] = None
                # We don't compute realized P&L on the half-close here —
                # the full IC settles on the remaining-side close later.
        elif intent == "adjustment_1":
            parent = pending.get("parent_combo_id")
            ic = state["open_ics"].get(parent)
            if ic is not None:
                untested = pending["untested_side"]
                ic[f"short_{untested}_strike"] = pending["new_short_strike"]
                ic[f"long_{untested}_strike"] = pending["new_long_strike"]
                ic[f"short_{untested}_option_id"] = pending["new_short_option_id"]
                ic[f"long_{untested}_option_id"] = pending["new_long_option_id"]
                ic[f"short_{untested}_delta_at_entry"] = pending.get(
                    "new_short_delta_at_entry"
                )
                ic["adjustment_count"] = int(ic.get("adjustment_count", 0)) + 1
        else:
            log.warning("IronCondor on_combo_filled: unknown intent %r", intent)
        self.persist_state(state)

    def _pending_to_open_ic_payload(self, pending: dict) -> dict:
        ts = self._clock().isoformat(timespec="seconds")
        return {
            "symbol": pending["symbol"],
            "expiration": pending["expiration"],
            "contracts": pending["contracts"],
            "wing_width": pending["wing_width"],
            "credit_at_entry": pending["credit_at_entry"],
            "dte_at_entry": pending["dte_at_entry"],
            "ivr_at_entry": pending["ivr_at_entry"],
            "max_loss_per_contract": pending["max_loss_per_contract"],
            "short_put_strike": pending["short_put_strike"],
            "long_put_strike": pending["long_put_strike"],
            "short_call_strike": pending["short_call_strike"],
            "long_call_strike": pending["long_call_strike"],
            "short_put_option_id": pending.get("short_put_option_id"),
            "long_put_option_id": pending.get("long_put_option_id"),
            "short_call_option_id": pending.get("short_call_option_id"),
            "long_call_option_id": pending.get("long_call_option_id"),
            "short_put_delta_at_entry": pending.get("short_put_delta_at_entry"),
            "short_call_delta_at_entry": pending.get("short_call_delta_at_entry"),
            "long_put_delta_at_entry": pending.get("long_put_delta_at_entry"),
            "long_call_delta_at_entry": pending.get("long_call_delta_at_entry"),
            "adjustment_count": 0,
            "opened_ts": ts,
            "session_start_mark": None,
            "session_start_date": None,
        }

    def _emit_lifecycle_audit(self, kind: str, payload: dict) -> None:
        """Write a strategy-scoped audit_event row directly.

        Used by `on_combo_filled` for the `ic_lifecycle_closed` event
        that lets the win-rate-by-IVR + adjustment-outcome telemetry
        queries reconstruct the full IC lifecycle without depending on
        agent_state (which loses the entry after the close-callback
        pop). Best-effort: failures are logged and swallowed so a
        transient audit-DB issue doesn't break the close path.
        """
        try:
            ts = self._clock().isoformat(timespec="seconds")
            payload_json = json.dumps(payload, default=str)
            with db.connect(self._db_url) as conn:
                conn.execute(
                    "INSERT INTO audit_event(ts, actor, kind, payload_json) "
                    "VALUES(?,?,?,?)",
                    (ts, STRATEGY_SLUG, kind, payload_json),
                )
        except Exception:
            log.exception(
                "IronCondor _emit_lifecycle_audit failed for kind=%s "
                "combo=%s (state callback continuing)",
                kind, payload.get("combo_id"),
            )

    def _realized_pnl_from_close(
        self, ic: dict, fills: list[FillEvent],
    ) -> float:
        """Compute per-share realized P&L = credit_at_entry - net_close_cost."""
        # cashflow on the close combo: + receive, - pay
        cashflow = 0.0
        for f in fills:
            sign = 1.0 if f.side == "sell" else -1.0
            cashflow += sign * f.price
        # cashflow is negative for a debit close. P&L per share:
        return float(ic["credit_at_entry"]) + cashflow

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def _on_combo_closed_pnl(self, state: dict, realized_pnl: float) -> None:
        cb = state["circuit_breaker"]
        cb.setdefault("recent_pnl", []).append(realized_pnl)
        if realized_pnl > 0:
            cb["consecutive_losses"] = 0
        elif realized_pnl < 0:
            cb["consecutive_losses"] = int(cb.get("consecutive_losses", 0)) + 1

        # Pause if loss streak hits threshold.
        threshold = int(self.cfg("circuit_breaker.consecutive_loss_pause"))
        if cb["consecutive_losses"] >= threshold and not cb.get("paused_until"):
            days = int(self.cfg("circuit_breaker.pause_days"))
            until = (self._clock() + timedelta(days=days)).isoformat(timespec="seconds")
            cb["paused_until"] = until
            log.warning(
                "IronCondor circuit breaker: %d consecutive losses → paused until %s",
                cb["consecutive_losses"], until,
            )

    def _check_repause(self, state: dict) -> None:
        cb = state["circuit_breaker"]
        until_str = cb.get("paused_until")
        if not until_str:
            return
        try:
            until = datetime.fromisoformat(until_str)
        except (TypeError, ValueError):
            cb["paused_until"] = None
            return
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if self._clock() < until:
            return
        # Pause expired: reset HWM. If still in drawdown, re-pause.
        hwm = cb.get("drawdown_hwm")
        # NOTE: drawdown evaluation reads live equity, but we already
        # compute equity in branch 0 + scan paths. For this synchronous
        # check we trust the latest known equity is at least 15% below
        # HWM only if cb says so — without re-querying the broker. A
        # future revision can plumb equity in here. Reset HWM on expiry
        # so the counter resumes from current equity.
        cb["drawdown_hwm"] = None    # caller will re-set on next snapshot
        cb["paused_until"] = None
        cb["consecutive_losses"] = 0
        log.info("IronCondor circuit breaker: pause expired — resumed")

    def reset_circuit_breaker(self, state: dict | None = None) -> None:
        """Kill-switch path: reset all counters."""
        s = state or self.load_state()
        s["circuit_breaker"] = {
            "consecutive_losses": 0,
            "recent_pnl": [],
            "paused_until": None,
            "drawdown_hwm": None,
        }
        if state is None:
            self.persist_state(s)
        log.info("IronCondor circuit breaker: reset via kill-switch")

    def _is_paused(self, state: dict) -> bool:
        until = state.get("circuit_breaker", {}).get("paused_until")
        if not until:
            return False
        try:
            t = datetime.fromisoformat(until)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return False
        return self._clock() < t

    # ------------------------------------------------------------------
    # Portfolio preflight (strategy-side caps; risk gate still runs after)
    # ------------------------------------------------------------------

    def _preflight_underlying(
        self, state: dict, symbol: str,
    ) -> tuple[bool, str]:
        """Symbol-level checks: no duplicate position; concurrency limit;
        correlation limit. Max-loss check is _preflight_with_size."""
        opens = state.get("open_ics", {})
        # 1 position per underlying.
        existing = [ic for ic in opens.values() if ic.get("symbol") == symbol]
        if existing:
            return False, "duplicate_underlying"

        # max_concurrent
        max_concurrent = int(self.cfg("portfolio_caps.max_concurrent"))
        if len(opens) >= max_concurrent:
            return False, "max_concurrent"

        # max_correlated
        max_corr = int(self.cfg("portfolio_caps.max_correlated"))
        for group in _CORRELATION_GROUPS:
            if symbol in group:
                same_group = sum(
                    1 for ic in opens.values()
                    if ic.get("symbol") in group
                )
                if same_group >= max_corr:
                    return False, "max_correlated"
        return True, ""

    def _preflight_with_size(
        self, state: dict, symbol: str, candidate_max_loss_dollars: float,
    ) -> tuple[bool, str]:
        # BP-deployed cap: sum of max-loss across open ICs + this one.
        max_bp_pct = float(self.cfg("portfolio_caps.max_bp_pct"))
        # We need account equity — caller supplied via state? Not here.
        # The per-trade cap was already enforced upstream; the BP-pct
        # cap is informational without equity context. For v1 we do a
        # cheap aggregate: total max-loss across open ICs + new ≤
        # max_bp_pct * (sum_existing_max_loss / max_bp_pct) is
        # circular. Simpler: cap on COUNT of positions (handled in
        # _preflight_underlying) and on per-trade size (handled by
        # caller). Skip BP-pct enforcement here; the risk gate's
        # per-trade cap is the load-bearing check. This is documented
        # as a v1 simplification — the strategy depends on
        # max_concurrent + max_per_trade_pct to keep total deployed
        # under target.
        return True, ""

    # ------------------------------------------------------------------
    # Session P&L
    # ------------------------------------------------------------------

    async def _maybe_capture_session_marks(
        self, broker: OptionBroker, state: dict,
    ) -> None:
        """Capture session_start_mark on the first manage() tick after
        09:30 ET each day. Imprecision (up to one cadence-interval late
        if mid-sleep at market open) is documented in the module
        docstring and is acceptable for the 10%-of-equity catastrophic
        threshold."""
        today_iso = self._clock().date().isoformat()
        if not _is_us_market_open(self._clock()):
            return
        for combo_id, ic in state["open_ics"].items():
            if ic.get("session_start_date") == today_iso:
                continue
            close_cost = await self._current_close_cost(broker, ic)
            if close_cost is None:
                continue
            # Combo MTM (per share) = credit collected - cost to close.
            mtm = float(ic["credit_at_entry"]) - close_cost
            ic["session_start_mark"] = mtm
            ic["session_start_date"] = today_iso

    async def _compute_session_pnl(
        self, broker: OptionBroker, state: dict,
    ) -> float:
        """Total session P&L (dollars) = Σ (current_mtm − session_start_mark)
        × contracts × 100, across all open ICs. Used by branch 0."""
        total = 0.0
        for combo_id, ic in state["open_ics"].items():
            close_cost = await self._current_close_cost(broker, ic)
            if close_cost is None:
                continue
            mtm = float(ic["credit_at_entry"]) - close_cost
            start = ic.get("session_start_mark")
            if start is None:
                continue
            contracts = int(ic.get("contracts", 1))
            total += (mtm - start) * contracts * 100.0
        return total

    # ------------------------------------------------------------------
    # Startup catch-up
    # ------------------------------------------------------------------

    async def startup_catchup(
        self, broker: OptionBroker,
    ) -> tuple[list[list[ProposedOrder]], int]:
        """One immediate manage() pass on startup. Actions returned here
        are tagged `startup_catchup` in extra so main.py can promote
        the audit kind on emission."""
        actions, cadence = await self.manage(broker)
        for combo in actions:
            for o in combo:
                if o.extra is not None:
                    o.extra["startup_catchup"] = True
                    o.extra["audit_severity"] = "warning"
        if actions:
            log.warning(
                "IronCondor startup_catchup: %d action(s) fired on startup",
                len(actions),
            )
        return actions, cadence

    # ------------------------------------------------------------------
    # Cadence
    # ------------------------------------------------------------------

    async def _compute_cadence(
        self, broker: OptionBroker, state: dict,
    ) -> int:
        """Return next sleep duration based on the most-stressed open IC."""
        if not state["open_ics"]:
            return _CADENCE_IDLE
        adjust = float(self.cfg("management.tested_delta_adjust"))
        warn = float(self.cfg("management.tested_delta_warn"))
        worst_abs_delta = 0.0
        for ic in state["open_ics"].values():
            for side in ("call", "put"):
                d = await self._current_short_delta(broker, ic, side)
                if d is None:
                    continue
                worst_abs_delta = max(worst_abs_delta, abs(d))
        if worst_abs_delta >= adjust:
            return _CADENCE_TESTED
        if worst_abs_delta >= warn:
            return _CADENCE_WARN
        return _CADENCE_IDLE

    # ------------------------------------------------------------------
    # Scan telemetry
    # ------------------------------------------------------------------

    def _tally_scan_filter(
        self, state: dict, symbols: list[str], reason: str,
    ) -> None:
        """Increment `scan_passes_with_no_candidates` per symbol per day."""
        day = self._clock().date().isoformat()
        bucket = state.setdefault("scan_telemetry", {}).setdefault(day, {})
        for sym in symbols:
            entry = bucket.setdefault(sym, {"total": 0, "by_reason": {}})
            entry["total"] = int(entry.get("total", 0)) + 1
            entry["by_reason"][reason] = (
                int(entry["by_reason"].get(reason, 0)) + 1
            )

    # ------------------------------------------------------------------
    # Chain-pick helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_expiry(
        expirations: list[str], target_dte: int, tolerance: int = 7,
    ) -> str | None:
        today = date.today()
        candidates: list[tuple[int, str]] = []
        for e in expirations:
            try:
                d = (date.fromisoformat(e) - today).days
            except (TypeError, ValueError):
                continue
            if abs(d - target_dte) <= tolerance:
                candidates.append((abs(d - target_dte), e))
        if not candidates:
            return None
        candidates.sort()
        return candidates[0][1]

    @staticmethod
    def _pick_by_delta(chain: list[dict], target_delta: float) -> dict | None:
        """Pick the contract whose delta is closest to `target_delta`.

        `target_delta` is signed: +0.16 for OTM calls, -0.16 for OTM puts.
        Skips rows with missing delta.
        """
        scored: list[tuple[float, dict]] = []
        for c in chain:
            d = c.get("delta")
            if d is None:
                continue
            scored.append((abs(float(d) - target_delta), c))
        if not scored:
            return None
        scored.sort(key=lambda x: x[0])
        return scored[0][1]

    @staticmethod
    def _pick_by_strike(chain: list[dict], target_strike: float) -> dict | None:
        if not chain:
            return None
        scored = sorted(chain, key=lambda c: abs(
            float(c.get("strike_price") or 0) - target_strike
        ))
        return scored[0] if scored else None

    @staticmethod
    def _compute_net_credit(
        short_put: dict, long_put: dict, short_call: dict, long_call: dict,
    ) -> float | None:
        try:
            sp_mark = float(short_put.get("mark_price") or short_put.get("bid") or 0)
            lp_mark = float(long_put.get("mark_price") or long_put.get("ask") or 0)
            sc_mark = float(short_call.get("mark_price") or short_call.get("bid") or 0)
            lc_mark = float(long_call.get("mark_price") or long_call.get("ask") or 0)
        except (TypeError, ValueError):
            return None
        # Receive short premiums, pay long premiums.
        return sp_mark + sc_mark - lp_mark - lc_mark

    @staticmethod
    def _safe_float(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
