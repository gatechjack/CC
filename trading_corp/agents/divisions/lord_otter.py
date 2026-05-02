"""Lord Otter — TradingView-driven scalping strategy.

Architecture:
  TradingView alert (per-signal webhook)
        ↓
  POST /webhook/tradingview/lord-otter   (in trading_corp.web.webhooks)
        ↓
  LordOtterAgent.on_alert(payload)
        ├─ updates per-symbol state (bias, ribbons, arming, recent alerts)
        ├─ classifies the alert chain into a conviction tier
        ├─ checks halts / cooldowns / news / weekend / time-of-day
        └─ emits a ProposedOrder (or None)
        ↓
  Risk gate → audit log → place (if auto_execute) or notify (if not)

Phase 1 scope:
  - Spot LONG entries on Coinbase. "buy" signals open longs;
    "sell" signals close existing longs (no naked shorts).
  - Conviction tiers from config/strategies.yaml (5%/3%/1.5%/0.75%).
  - Per-trade risk cap from config/risk.yaml (lord_otter override = 5%).
  - News-halt via config/macro_calendar.yaml (hand-maintained).
  - auto_execute=false by default — orders are LOGGED + Telegram-notified
    but NOT placed. Flip to true when you trust the strategy.

Phase 2+:
  - Both-direction trading once coinbase_futures is wired (Phase C).
  - Multiple symbols (ETH, FET, …).
  - 1m and 5m timeframes alongside 3m.
  - Real macro-calendar fetcher (FRED + BLS) replacing the YAML.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from trading_corp.agents.research.position_context_cache import (
    read_position_context,
)
from trading_corp.agents.research.schemas import PositionContext
from trading_corp.data.macro_calendar import MacroCalendar
from trading_corp.persistence.models import ProposedOrder
from trading_corp.utils.time import iso, now_utc

log = logging.getLogger(__name__)

# --------------------------------------------------------------------
# Signal vocabulary — ALL signals the Lord Otter agent recognizes.
# Keep this list in sync with the alerts you create in TradingView;
# the webhook handler validates incoming `signal` field against it.
# --------------------------------------------------------------------
KNOWN_SIGNALS = {
    # Primary triggers
    "otter_buy", "otter_sell",
    # Divergence early-warning
    "spoon_bull", "spoon_bear",
    # Reversal arming
    "pink_box_bull", "pink_box_bear",
    # Multi-TF aligned premium
    "water_buy_small", "water_buy_large",
    "water_sell_small", "water_sell_large",
    # Precision top/bottom
    "money_bag_top", "money_bag_bottom",
    # Bar-by-bar confirmation
    "cvd_bull_flip", "cvd_bear_flip",
    # Higher-TF context
    "bias_bull", "bias_bear",
    "ribbon_exhaustion_bull", "ribbon_exhaustion_bear",
}

# Each direction maps "bull" signals → long, "bear" signals → short.
# We use this sign to test bias-alignment.
_BULL_SIGNALS = {
    "otter_buy", "spoon_bull", "pink_box_bull",
    "water_buy_small", "water_buy_large",
    "money_bag_bottom",
    "cvd_bull_flip", "bias_bull", "ribbon_exhaustion_bull",
}
_BEAR_SIGNALS = {
    "otter_sell", "spoon_bear", "pink_box_bear",
    "water_sell_small", "water_sell_large",
    "money_bag_top",
    "cvd_bear_flip", "bias_bear", "ribbon_exhaustion_bear",
}


def signal_direction(signal: str) -> str:
    """Return 'long' if signal is bullish, 'short' if bearish, '' if neutral."""
    if signal in _BULL_SIGNALS:
        return "long"
    if signal in _BEAR_SIGNALS:
        return "short"
    return ""


# --------------------------------------------------------------------
# Per-symbol state
# --------------------------------------------------------------------

@dataclass
class ArmedState:
    """Set when Pink Box / Spoon fires; expires after N bars."""
    source: str             # "pink_box" | "spoon"
    armed_at: datetime
    expires_at: datetime
    direction: str          # "long" | "short"


@dataclass
class AlertRecord:
    """One alert the agent received. All fields preserved for audit."""
    ts: datetime
    signal: str
    direction: str          # derived from signal
    price: float
    payload: dict           # full normalized webhook body


@dataclass
class SymbolState:
    """Mutable state per symbol. Accessed only on the asyncio loop."""
    symbol: str
    bias: str = "unknown"            # "bull" | "bear" | "unknown"
    ribbon_state: str = "unknown"    # "normal" | "exhaustion" | "mixed"
    armed_long: ArmedState | None = None
    armed_short: ArmedState | None = None
    recent_alerts: list[AlertRecord] = field(default_factory=list)  # ring buffer
    last_trade_at: datetime | None = None      # any direction (legacy, kept for compat)
    last_entry_at: datetime | None = None       # last entry order emitted
    last_close_at: datetime | None = None       # last close-long order emitted
    consecutive_losses: int = 0
    daily_realized_pnl_pct: float = 0.0
    daily_pnl_date: date | None = None
    halted_until: datetime | None = None
    halt_reason: str | None = None
    # Track last placed buy and last sell price so the agent can
    # tell whether we have an open spot position to close.
    open_long_qty: float = 0.0
    # Phase 1d: most-recent PositionContext consulted on alert. Read
    # from the pre-emptive cache; None on miss/stale (per Q7, miss is
    # "no signal", NOT "small bearish signal"). Not yet gating behavior
    # — surfaced for audit and future sizing rules.
    last_position_context: PositionContext | None = None


# --------------------------------------------------------------------
# Tier classification result
# --------------------------------------------------------------------

@dataclass
class TierVerdict:
    tier: str               # "diamond" | "premium" | "water_large" | "water_small"
                            #  | "standard" | "money_bag" | "solo_otter"
    direction: str          # "long" | "short"
    size_pct_equity: float  # raw (pre-time-modifier) size %
    rationale: str          # human-readable why this tier
    # Stash the fields used to build the order (entry price, stop method, etc.)
    entry_price: float
    payload: dict


# --------------------------------------------------------------------
# Agent
# --------------------------------------------------------------------

class LordOtterAgent:
    """One agent instance handles all symbols + all timeframes.

    The webhook layer calls `on_alert(payload)` for each TV alert.
    The agent updates state and returns either a ProposedOrder (caller
    routes through risk + place pipeline) or None (alert was ignored —
    duplicate, halted, off-bias, news-halt, etc.).

    All "ignored" reasons are recorded in the audit log via the caller,
    so post-hoc analysis can distinguish "no signal" from "signal but
    we chose not to act."
    """

    name = "lord_otter"

    # Bias is the only piece of `SymbolState` that's worth persisting
    # right now. Other state (recent_alerts, armed_long/short) is short-
    # lived (15-min arming window) and re-derives quickly from new
    # alerts. Bias is meant to be a regime-level latch — losing it on
    # restart makes the strategy mute until the next regime-change
    # cross, which can be hours or days away.
    BIAS_STATE_AGENT_NAME = "lord_otter"
    BIAS_STATE_KEY_PREFIX = "bias:"      # full key is f"bias:{symbol}"
    # Phase 1d: horizon used when reading PositionContext from cache.
    # Must match what the prime path writes (4h is scalp-relevant).
    POSITION_CONTEXT_HORIZON_HOURS = 4
    # Drop persisted bias older than this. A regime that's older than
    # 12h on restart probably no longer reflects current market
    # conditions, and we'd rather wait for a fresh signal than act on
    # stale state.
    BIAS_STATE_MAX_AGE = timedelta(hours=12)

    def __init__(
        self,
        strategies_yaml: Path = Path("config/strategies.yaml"),
        macro_calendar: MacroCalendar | None = None,
        db_url: str | None = None,
    ) -> None:
        self._strategies_yaml = strategies_yaml
        self._mtime: float = 0.0
        self._cfg: dict[str, Any] = {}
        self._states: dict[str, SymbolState] = {}
        self._macro = macro_calendar or MacroCalendar.load()
        # `db_url=None` disables persistence entirely (used by tests +
        # ad-hoc CLI). Production main.py wires the real DB path.
        self._db_url = db_url
        self._reload()
        if self._db_url:
            self._restore_bias_state()

    # ------------------------------------------------------------------
    # Config loading (hot-reloadable)
    # ------------------------------------------------------------------

    def _reload(self) -> None:
        try:
            mtime = self._strategies_yaml.stat().st_mtime
        except FileNotFoundError:
            self._cfg = {}
            return
        if mtime == self._mtime:
            return
        try:
            with self._strategies_yaml.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            log.warning("LordOtterAgent: failed to load %s: %s", self._strategies_yaml, e)
            return
        self._cfg = data.get("lord_otter", {}) or {}
        self._mtime = mtime
        log.info(
            "LordOtterAgent reloaded config: enabled=%s auto_execute=%s "
            "symbols=%s arming_window_bars=%s",
            self._cfg.get("enabled"), self._cfg.get("auto_execute"),
            self._cfg.get("symbols"), self._cfg.get("arming_window_bars"),
        )

    # ------------------------------------------------------------------
    # Convenience accessors
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
        return str(self._cfg.get("division", "coinbase_spot"))

    @property
    def webhook_secret_env(self) -> str:
        self._reload()
        return str(self._cfg.get("webhook_secret_env", "LORD_OTTER_WEBHOOK_SECRET"))

    def is_symbol_allowed(self, symbol: str) -> bool:
        self._reload()
        allowed = {s.upper() for s in (self._cfg.get("symbols") or [])}
        return symbol.upper() in allowed

    def configured_symbols(self) -> list[str]:
        """Return the symbols this agent is configured to trade. Used by
        the Phase 1d PositionContext prime loop on startup."""
        self._reload()
        return [s.upper() for s in (self._cfg.get("symbols") or [])]

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    def get_state(self, symbol: str) -> SymbolState:
        s = self._states.get(symbol)
        if s is None:
            s = SymbolState(symbol=symbol)
            self._states[symbol] = s
        return s

    # ------------------------------------------------------------------
    # PositionContext cache read (Phase 1d, design Q7)
    # ------------------------------------------------------------------

    def _fetch_position_context(self, symbol: str) -> PositionContext | None:
        """Read the most recent PositionContext for `symbol` from the
        pre-emptive cache. Returns None on miss / stale / no DB.

        Per design Q7: miss is "no signal", NOT "small bearish signal".
        Caller treats None as a no-op. This is fail-soft on every error
        path — the alert pipeline must not block on research-firm
        availability.
        """
        if not self._db_url:
            return None
        try:
            return read_position_context(
                self.name,
                symbol,
                self.POSITION_CONTEXT_HORIZON_HOURS,
                db_url=self._db_url,
            )
        except Exception as e:
            log.warning(
                "LordOtterAgent: position_context read failed for %s: %s",
                symbol, e,
            )
            return None

    # ------------------------------------------------------------------
    # Bias persistence (DB-backed, see persistence/db.py agent_state table)
    # ------------------------------------------------------------------

    def _persist_bias(self, symbol: str, bias: str) -> None:
        """Best-effort write of the latched bias for `symbol` to the DB.

        Failures here are LOGGED but do not raise — bias persistence is
        a UX/reliability feature, not a correctness invariant. If the
        DB is unavailable, the in-memory bias still works for the
        current process; we just lose it on the next restart, which is
        no worse than pre-persistence behavior.
        """
        if not self._db_url:
            return
        try:
            from trading_corp.persistence.db import set_agent_state
            set_agent_state(
                self.BIAS_STATE_AGENT_NAME,
                f"{self.BIAS_STATE_KEY_PREFIX}{symbol}",
                {"bias": bias, "symbol": symbol},
                db_url=self._db_url,
            )
        except Exception as e:
            log.warning(
                "LordOtterAgent: failed to persist bias for %s: %s "
                "(in-memory state still valid)", symbol, e,
            )

    def _restore_bias_state(self) -> None:
        """Restore bias for all configured symbols from the DB on startup.

        Skips entries older than `BIAS_STATE_MAX_AGE` — a stale bias
        from yesterday probably doesn't reflect today's regime, and we
        prefer "wait for fresh signal" over "act on stale state".

        Called once during `__init__`. New symbols added to config later
        won't be retroactively restored — but that only matters if you
        add a symbol AND want a freshly-restarted agent to pick up its
        prior bias, which is an edge case we can fix later if it bites.
        """
        if not self._db_url:
            return
        symbols = [s.upper() for s in (self._cfg.get("symbols") or [])]
        if not symbols:
            return
        try:
            from trading_corp.persistence.db import (
                load_agent_state, delete_agent_state,
            )
        except Exception as e:
            log.warning("LordOtterAgent: bias restore unavailable: %s", e)
            return

        now = datetime.now(timezone.utc)
        restored = 0
        skipped_stale = 0
        for symbol in symbols:
            key = f"{self.BIAS_STATE_KEY_PREFIX}{symbol}"
            try:
                result = load_agent_state(
                    self.BIAS_STATE_AGENT_NAME, key, db_url=self._db_url,
                )
            except Exception as e:
                log.warning(
                    "LordOtterAgent: bias restore failed for %s: %s",
                    symbol, e,
                )
                continue
            if result is None:
                continue
            value, updated_at = result
            age = now - updated_at
            if age > self.BIAS_STATE_MAX_AGE:
                # Stale entries pollute future startups if we leave
                # them; clean up so the next boot doesn't re-evaluate.
                log.info(
                    "LordOtterAgent: discarding stale bias for %s "
                    "(age=%s, max=%s)", symbol, age, self.BIAS_STATE_MAX_AGE,
                )
                try:
                    delete_agent_state(
                        self.BIAS_STATE_AGENT_NAME, key, db_url=self._db_url,
                    )
                except Exception:
                    pass
                skipped_stale += 1
                continue
            bias = (value or {}).get("bias")
            if bias not in ("bull", "bear"):
                continue
            state = self.get_state(symbol)
            state.bias = bias
            restored += 1
            log.info(
                "LordOtterAgent: restored bias=%s for %s "
                "(set %s ago)", bias, symbol, age,
            )
        if restored or skipped_stale:
            log.info(
                "LordOtterAgent: bias restore summary — restored=%d skipped_stale=%d",
                restored, skipped_stale,
            )

    # ------------------------------------------------------------------
    # Core entry point: webhook handler calls this
    # ------------------------------------------------------------------

    def on_alert(
        self,
        payload: dict,
        *,
        account_equity: float | None = None,
        held_qty: dict[str, float] | None = None,
    ) -> tuple[ProposedOrder | None, str]:
        """Process one TV alert.

        Phase 1.5 additions:
          account_equity:  current equity from broker.snapshot(). Used to
                           size orders as `equity × tier_size_pct`. If
                           None or 0, falls back to the Phase 1 placeholder
                           ($50 × tier_factor).
          held_qty:        dict of {symbol: qty} from broker.snapshot()
                           positions. Used by the close-existing-longs
                           path to size SELL orders against the actual
                           held position. Empty dict = no positions.

        Returns (order_or_none, decision_reason). The reason string is
        always populated so the caller can audit-log it whether or not
        an order was emitted.
        """
        self._reload()
        if not self.enabled:
            return None, "lord_otter strategy is disabled in config"

        signal = payload.get("signal", "")
        if signal not in KNOWN_SIGNALS:
            return None, f"unknown signal {signal!r} (not in KNOWN_SIGNALS)"

        symbol = payload.get("symbol", "")
        if not self.is_symbol_allowed(symbol):
            return None, f"symbol {symbol!r} not in lord_otter.symbols whitelist"

        ts = self._parse_ts(payload.get("time"))
        price = float(payload.get("price") or 0.0)
        if price <= 0:
            return None, "missing or invalid price"

        state = self.get_state(symbol)
        direction = signal_direction(signal)
        held_qty = held_qty or {}

        # --- Update state from this alert (regardless of whether we trade) ---
        self._record_alert(state, signal, direction, price, payload, ts)
        self._refresh_state_from_signal(state, signal, direction, ts)
        state.last_position_context = self._fetch_position_context(symbol)

        # --- Halt checks ---
        if state.halted_until and ts < state.halted_until:
            return None, f"strategy halted until {iso(state.halted_until)} ({state.halt_reason})"

        # Roll daily P&L tracking at UTC midnight.
        today = ts.date()
        if state.daily_pnl_date != today:
            state.daily_pnl_date = today
            state.daily_realized_pnl_pct = 0.0
            # Clear an expired halt that was set yesterday on daily-loss.
            if state.halted_until and state.halted_until.date() < today:
                state.halted_until = None
                state.halt_reason = None

        # News halt — defer to MacroCalendar.
        news_cfg = self._cfg.get("news_halt") or {}
        if news_cfg.get("enabled"):
            in_window, evt = self._macro.is_within_halt_window(
                ts,
                window_minutes=int(news_cfg.get("halt_window_minutes", 30)),
                impact_levels=tuple(news_cfg.get("halt_impact_levels", ["high"])),
            )
            if in_window:
                return None, f"news halt: {evt.name if evt else '?'} at {iso(evt.ts) if evt else '?'}"

        # (Chop guard moved into the entry path below. Closes after entries
        # legitimately register both directions in the recent-alerts buffer,
        # so a global chop check here would suppress real exits.)

        # --- Direction policy: spot is long-only in Phase 1 ---
        # Phase 1.5: bear signals now CLOSE existing longs (instead of
        # being skipped). Bias gate is bypassed for closes — we always
        # want to act on a bear signal if we hold a long, regardless of
        # whether the higher-TF bias is still bullish (the bias may not
        # have flipped yet but the entry conditions are clearly broken).
        direction_policy = str(self._cfg.get("direction_policy", "long_only"))
        if direction_policy == "long_only" and direction == "short":
            return self._handle_close_long(
                state, signal, price, payload, ts, held_qty, account_equity,
            )

        # Entry-path cooldown: drop duplicate entries firing back-to-back
        # on the same side. Doesn't block a close that follows an entry.
        cooldown_sec = int(self._cfg.get("cooldown_seconds", 180))
        if state.last_entry_at and (ts - state.last_entry_at).total_seconds() < cooldown_sec:
            return None, f"entry cooldown: last entry within {cooldown_sec}s"

        # Entry-path chop guard: real chop is opposite ENTRIES firing too
        # close together. Catches the case where bull and bear arming
        # signals fire within seconds of each other (indicator confusion).
        chop_window = int((self._cfg.get("halt_conditions") or {}).get("chop_window_seconds", 60))
        if self._is_chop(state, ts, chop_window):
            return None, f"chop window: opposite signals within {chop_window}s"

        # --- Tier classification (entry path: bias-gated) ---
        verdict = self._classify_tier(
            state, signal, direction, price, payload, ts,
            bypass_bias=False,
        )
        if verdict is None:
            return None, f"signal {signal!r} did not qualify for any tier (likely no bias alignment)"

        # --- Apply time-of-day + weekend modifiers to size ---
        size_pct = self._apply_time_modifiers(verdict.size_pct_equity, ts)

        # --- Build ProposedOrder with real equity-aware sizing + stop ---
        order = self._build_order(
            verdict, size_pct, price, ts,
            account_equity=account_equity,
        )

        # Mark entry cooldown.
        state.last_entry_at = ts
        state.last_trade_at = ts  # legacy field

        return order, (
            f"tier={verdict.tier} direction={verdict.direction} "
            f"size_pct={size_pct:.4f} ({verdict.rationale})"
        )

    # ------------------------------------------------------------------
    # Bear-signal close-existing-longs path (Phase 1.5)
    # ------------------------------------------------------------------

    def _handle_close_long(
        self,
        state: SymbolState,
        signal: str,
        price: float,
        payload: dict,
        ts: datetime,
        held_qty: dict[str, float],
        account_equity: float | None,
    ) -> tuple[ProposedOrder | None, str]:
        """Bear signal in long_only mode: emit a SELL of held BTC fraction.

        Tier classification runs with bias bypassed because we always want
        to honor a bear signal if we hold a long — the position got us in,
        the bear says get out.
        """
        symbol = state.symbol
        held = float(held_qty.get(symbol, 0.0) or 0.0)
        if held <= 0:
            return None, (
                f"long-only bear signal received but no open {symbol} position "
                f"(broker snapshot reports {held} held)"
            )

        # Close-path cooldown: don't fire two closes back-to-back on the
        # same position. Independent of entry cooldown so a bear signal
        # after an entry isn't blocked.
        cooldown_sec = int(self._cfg.get("cooldown_seconds", 180))
        if state.last_close_at and (ts - state.last_close_at).total_seconds() < cooldown_sec:
            return None, f"close cooldown: last close within {cooldown_sec}s"

        # Classify the bear signal's conviction tier — bypassing bias
        # because we're closing, not opening.
        verdict = self._classify_tier(
            state, signal, "short", price, payload, ts,
            bypass_bias=True,
        )
        if verdict is None:
            return None, (
                f"bear signal {signal!r} did not qualify for any tier "
                f"(no Otter/Water/MoneyBag confirmation in window)"
            )

        # Look up the close fraction for this tier.
        close_fractions = self._cfg.get("tier_close_fractions") or {}
        fraction = float(close_fractions.get(verdict.tier, 0.5))
        if fraction <= 0:
            return None, f"tier {verdict.tier} close fraction is 0 (configured no-action)"

        close_qty = round(held * fraction, 8)
        if close_qty <= 0:
            return None, f"close qty rounded to 0 (held={held}, fraction={fraction})"

        # Mark close cooldown so we don't immediately fire another close.
        state.last_close_at = ts
        state.last_trade_at = ts  # legacy field

        order = ProposedOrder(
            strategy="lord_otter",
            symbol=symbol,
            side="sell",
            qty=close_qty,
            order_type="market",
            limit_price=None,
            rationale=(
                f"Lord Otter {verdict.tier.upper()} CLOSE: {fraction*100:.0f}% "
                f"of {held:.8f} {symbol} held. {verdict.rationale}. "
                f"Signal={signal} @ ${price}."
            ),
            extra={
                "asset_type": "crypto",
                "via": "lord_otter_webhook",
                "tier": verdict.tier,
                "direction": "close_long",
                "is_close": True,
                "close_fraction": fraction,
                "held_qty_at_decision": held,
                "source_signal": signal,
                "tv_payload": payload,
                "manual": False,
            },
        )

        return order, (
            f"close_long: tier={verdict.tier} fraction={fraction*100:.0f}% "
            f"qty={close_qty:.8f}/{held:.8f} ({verdict.rationale})"
        )

    # ------------------------------------------------------------------
    # State updates
    # ------------------------------------------------------------------

    def _record_alert(
        self,
        state: SymbolState,
        signal: str,
        direction: str,
        price: float,
        payload: dict,
        ts: datetime,
    ) -> None:
        rec = AlertRecord(
            ts=ts, signal=signal, direction=direction,
            price=price, payload=dict(payload),
        )
        state.recent_alerts.append(rec)
        # Keep only the last 20 — enough for combo detection on 3m chart
        # (5-bar arming window = 15 min) without unbounded growth.
        if len(state.recent_alerts) > 20:
            state.recent_alerts = state.recent_alerts[-20:]

    def _refresh_state_from_signal(
        self,
        state: SymbolState,
        signal: str,
        direction: str,
        ts: datetime,
    ) -> None:
        """Update bias / arming / ribbon state based on this alert."""
        # Bias bar updates. We persist EVERY bias signal — even ones
        # that don't flip the latch (e.g. bias_bull arriving while
        # state.bias is already "bull") — so that `updated_ts` always
        # reflects the most recent signal. That keeps the staleness
        # check meaningful: a healthy strategy that re-emits bias every
        # bar (or on regime confirmation) sees its `updated_ts` stay
        # fresh, while a quiet one ages out at 12h.
        if signal == "bias_bull":
            state.bias = "bull"
            self._persist_bias(state.symbol, "bull")
        elif signal == "bias_bear":
            state.bias = "bear"
            self._persist_bias(state.symbol, "bear")

        # Ribbon exhaustion updates
        if signal == "ribbon_exhaustion_bull":
            state.ribbon_state = "exhaustion"
        elif signal == "ribbon_exhaustion_bear":
            state.ribbon_state = "exhaustion"

        # Arming updates (Pink Box + Spoon)
        if signal in ("pink_box_bull", "spoon_bull"):
            state.armed_long = ArmedState(
                source=signal.split("_")[0] + ("_box" if "pink" in signal else ""),
                armed_at=ts,
                expires_at=ts + self._arming_window_duration(),
                direction="long",
            )
        elif signal in ("pink_box_bear", "spoon_bear"):
            state.armed_short = ArmedState(
                source=signal.split("_")[0] + ("_box" if "pink" in signal else ""),
                armed_at=ts,
                expires_at=ts + self._arming_window_duration(),
                direction="short",
            )

        # Expire stale arms.
        if state.armed_long and ts >= state.armed_long.expires_at:
            state.armed_long = None
        if state.armed_short and ts >= state.armed_short.expires_at:
            state.armed_short = None

    def _arming_window_duration(self) -> timedelta:
        # 5 bars on 3m chart = 15 minutes by default. The bar duration
        # is approximated from the alert's `interval` field upstream;
        # for now, use 3m as the assumed timeframe.
        bars = int(self._cfg.get("arming_window_bars", 5))
        return timedelta(minutes=bars * 3)

    # ------------------------------------------------------------------
    # Cooldown / chop guards
    # ------------------------------------------------------------------

    def _in_cooldown(
        self,
        state: SymbolState,
        direction: str,
        ts: datetime,
        cooldown_sec: int,
    ) -> bool:
        if state.last_trade_at is None:
            return False
        return (ts - state.last_trade_at).total_seconds() < cooldown_sec

    def _is_chop(
        self,
        state: SymbolState,
        ts: datetime,
        chop_window_sec: int,
    ) -> bool:
        """True if both directions fired within the chop window."""
        cutoff = ts - timedelta(seconds=chop_window_sec)
        long_recent = any(
            r.ts >= cutoff and r.direction == "long"
            for r in state.recent_alerts
        )
        short_recent = any(
            r.ts >= cutoff and r.direction == "short"
            for r in state.recent_alerts
        )
        return long_recent and short_recent

    # ------------------------------------------------------------------
    # Tier classification — Phase 1 minimum-viable.
    # ------------------------------------------------------------------
    # The full tier matrix from the strategy doc has 7 tiers. Phase 1
    # implements them all but conservatively — only the explicit
    # combinations match. Lower-priority refinements (e.g. ribbon
    # exhaustion as Diamond gate) can be tightened later from observed
    # signal traffic in the audit log.

    def _classify_tier(
        self,
        state: SymbolState,
        signal: str,
        direction: str,
        price: float,
        payload: dict,
        ts: datetime,
        *,
        bypass_bias: bool = False,
    ) -> TierVerdict | None:
        """Return TierVerdict if this signal qualifies, None otherwise.

        `bypass_bias=True` skips the bias-alignment gate. Used for the
        close-existing-longs path where we want to honor any bear signal
        that hits the conviction chain, regardless of whether the higher-TF
        bias has flipped yet (positions get closed faster than bias bars
        update by design).
        """
        if direction not in ("long", "short"):
            return None

        # Bias gate (entry path only — bypassed for closes).
        if not bypass_bias:
            if state.bias == "unknown":
                # Bias not yet observed in this session. Allow trading but
                # only as Solo Otter / Standard tier (no Diamond/Premium
                # without a confirmed bias). Otter is the only signal that
                # can fire on its own without a bias signal upstream.
                if signal not in ("otter_buy", "otter_sell"):
                    return None
            else:
                expected_dir = "long" if state.bias == "bull" else "short"
                if direction != expected_dir:
                    return None

        sizes = self._cfg.get("tier_sizes", {})

        # Track which arming applies for this direction.
        armed = state.armed_long if direction == "long" else state.armed_short

        # CVD flip in this direction within last 2 bars (≈6 min on 3m).
        cvd_recent = self._has_recent_signal(
            state, ts, seconds=360,
            signals=("cvd_bull_flip" if direction == "long" else "cvd_bear_flip",),
        )

        # Money Bag confirmation in this direction within last 3 bars.
        moneybag_recent = self._has_recent_signal(
            state, ts, seconds=540,
            signals=("money_bag_bottom" if direction == "long" else "money_bag_top",),
        )

        # ----- Diamond: full setup chain + Money Bag OR Large Water -----
        # Originally also required `state.ribbon_state == "exhaustion"`, but
        # Lord Otter's "ribbon exhaustion" (white edges on the ribbons) is a
        # purely visual feature — not exposed as an alertable series. Without
        # an exhaustion signal arriving, that gate would never open and
        # Diamond could never fire. Dropping it. The remaining chain (armed
        # via spoon/divergence + cvd flip + super-confirm via money_bag or
        # large_water) is still the highest-conviction stack we can detect
        # automatically. If the indicator author later exposes exhaustion as
        # an alertable series, add a new signal `ribbon_exhaustion_*` and
        # restore the gate.
        if signal == "otter_buy" or signal == "otter_sell":
            if armed and cvd_recent and (
                moneybag_recent
                or self._has_recent_signal(state, ts, seconds=540, signals=(
                    "water_buy_large" if direction == "long" else "water_sell_large",
                ))
            ):
                return TierVerdict(
                    tier="diamond",
                    direction=direction,
                    size_pct_equity=float(sizes.get("diamond", 0.05)),
                    rationale="full chain (armed + cvd_flip + money_bag-or-large_water)",
                    entry_price=price,
                    payload=payload,
                )

            # ----- Premium: bias + arming + Otter + CVD flip -----
            if armed and cvd_recent:
                return TierVerdict(
                    tier="premium",
                    direction=direction,
                    size_pct_equity=float(sizes.get("premium", 0.03)),
                    rationale=f"bias + armed via {armed.source} + otter + cvd_flip",
                    entry_price=price,
                    payload=payload,
                )

            # ----- Standard: bias + Otter + CVD flip -----
            if cvd_recent:
                return TierVerdict(
                    tier="standard",
                    direction=direction,
                    size_pct_equity=float(sizes.get("standard", 0.015)),
                    rationale="bias + otter + cvd_flip (no prior arming)",
                    entry_price=price,
                    payload=payload,
                )

            # ----- Solo Otter: bias + Otter alone -----
            return TierVerdict(
                tier="solo_otter",
                direction=direction,
                size_pct_equity=float(sizes.get("solo_otter", 0.0075)),
                rationale="otter alone, bias-aligned, no other confirms",
                entry_price=price,
                payload=payload,
            )

        # ----- Water Large: bias + Large Water (bypass arming) -----
        if signal in ("water_buy_large", "water_sell_large"):
            return TierVerdict(
                tier="water_large",
                direction=direction,
                size_pct_equity=float(sizes.get("water_large", 0.03)),
                rationale="large water signal, multi-TF aligned",
                entry_price=price,
                payload=payload,
            )

        # ----- Water Small: bias + Small Water + (Otter or MoneyBag confirm) -----
        if signal in ("water_buy_small", "water_sell_small"):
            otter_recent = self._has_recent_signal(
                state, ts, seconds=540,
                signals=("otter_buy" if direction == "long" else "otter_sell",),
            )
            if otter_recent or moneybag_recent:
                return TierVerdict(
                    tier="water_small",
                    direction=direction,
                    size_pct_equity=float(sizes.get("water_small", 0.02)),
                    rationale="small water + (recent otter or money_bag)",
                    entry_price=price,
                    payload=payload,
                )
            # Small water alone doesn't qualify
            return None

        # ----- Money Bag: bias + Money Bag at extreme -----
        if signal in ("money_bag_top", "money_bag_bottom"):
            return TierVerdict(
                tier="money_bag",
                direction=direction,
                size_pct_equity=float(sizes.get("money_bag", 0.015)),
                rationale="money_bag precision reversal, bias-aligned",
                entry_price=price,
                payload=payload,
            )

        # Pink Box / Spoon / CVD flip / bias / ribbon — these update
        # state but don't fire orders themselves. They arm the agent
        # for an Otter trigger.
        return None

    def _has_recent_signal(
        self,
        state: SymbolState,
        ts: datetime,
        seconds: int,
        signals: tuple[str, ...],
    ) -> bool:
        cutoff = ts - timedelta(seconds=seconds)
        return any(
            r.ts >= cutoff and r.signal in signals
            for r in state.recent_alerts
        )

    # ------------------------------------------------------------------
    # Time-of-day modifiers
    # ------------------------------------------------------------------

    def _apply_time_modifiers(self, base_size: float, ts: datetime) -> float:
        """Apply time-of-day + weekend modifiers to the tier size."""
        tm = self._cfg.get("time_modifiers") or {}
        if not tm.get("enabled"):
            return base_size

        size = base_size

        # Weekend: Friday 22:00 UTC → Sunday 22:00 UTC
        weekend_mult = float(tm.get("weekend_size_multiplier", 1.0))
        if weekend_mult != 1.0:
            wd = ts.weekday()  # Mon=0 ... Sun=6
            hour = ts.hour
            in_weekend = (
                (wd == 4 and hour >= 22)            # Fri after 22 UTC
                or wd == 5                           # Sat
                or (wd == 6 and hour < 22)           # Sun before 22 UTC
            )
            if in_weekend:
                size *= weekend_mult

        # Trading-session windows
        for window in tm.get("windows", []) or []:
            uh = int(window.get("utc_hour", 0))
            um = int(window.get("utc_minute", 0))
            window_min = int(window.get("window_minutes", 60))
            mult = float(window.get("size_multiplier", 1.0))
            anchor = ts.replace(hour=uh, minute=um, second=0, microsecond=0)
            half = timedelta(minutes=window_min // 2)
            if anchor - half <= ts <= anchor + half:
                size *= mult
                break  # only apply one window's multiplier

        return size

    # ------------------------------------------------------------------
    # ProposedOrder construction
    # ------------------------------------------------------------------

    def _build_order(
        self,
        verdict: TierVerdict,
        size_pct: float,
        price: float,
        ts: datetime,
        *,
        account_equity: float | None = None,
    ) -> ProposedOrder:
        """Translate a TierVerdict into a ProposedOrder (Phase 1.5).

        Sizing:
          notional = account_equity × size_pct
          qty      = notional / price

          If account_equity is None or 0 (e.g., broker snapshot failed),
          fall back to the Phase 1 placeholder ($50 × tier_factor) so
          we still emit a small but non-zero order for visibility.

        Stop loss:
          long  → stop = bar_low × (1 − swing_buffer_pct)
          short → stop = bar_high × (1 + swing_buffer_pct)
          (bar OHLC pulled from the TV alert payload.)

        Then enforce a hard max-loss cap. If technical stop distance ×
        qty would exceed `max_loss_pct_equity`, SHRINK qty to fit.
        Never widen the stop — the technical stop has structural meaning
        (the trigger bar's swing), widening it past that is just hoping.
        """
        symbol = verdict.payload.get("symbol", "")
        side = "buy" if verdict.direction == "long" else "sell"

        # ── 1. Compute notional from equity ──────────────────────────
        if account_equity and account_equity > 0:
            notional = float(account_equity) * float(size_pct)
            sizing_basis = "equity_aware"
        else:
            # Fallback: deliberately small. Logged so it's traceable.
            notional = 50.0 * (size_pct / 0.015)
            sizing_basis = "placeholder_50usd"

        # ── 2. Compute qty ───────────────────────────────────────────
        qty = notional / price if price > 0 else 0.0
        if qty <= 0:
            qty = 0.0001  # final safety floor

        # ── 3. Compute technical stop from trigger bar ───────────────
        stop_cfg = self._cfg.get("stop_loss") or {}
        buffer_pct = float(stop_cfg.get("swing_buffer_pct", 0.001))
        fallback_pct = float(stop_cfg.get("fallback_stop_distance_pct", 0.003))
        max_loss_pct = float(stop_cfg.get("max_loss_pct_equity", 0.005))

        bar_low = float(verdict.payload.get("bar_low") or 0)
        bar_high = float(verdict.payload.get("bar_high") or 0)

        if verdict.direction == "long":
            if bar_low > 0 and bar_low < price:
                technical_stop = bar_low * (1 - buffer_pct)
                stop_basis = "trigger_bar_low"
            else:
                technical_stop = price * (1 - fallback_pct)
                stop_basis = "fallback_pct"
            stop_distance = price - technical_stop
        else:  # short
            if bar_high > price:
                technical_stop = bar_high * (1 + buffer_pct)
                stop_basis = "trigger_bar_high"
            else:
                technical_stop = price * (1 + fallback_pct)
                stop_basis = "fallback_pct"
            stop_distance = technical_stop - price

        # ── 4. Enforce max-loss cap by shrinking qty (never widen stop) ─
        resized_for_max_loss = False
        if account_equity and account_equity > 0 and stop_distance > 0 and qty > 0:
            max_dollar_loss = account_equity * max_loss_pct
            implied_dollar_loss = qty * stop_distance
            if implied_dollar_loss > max_dollar_loss:
                qty = max_dollar_loss / stop_distance
                resized_for_max_loss = True

        qty = round(qty, 8)
        if qty <= 0:
            qty = 0.0001

        # Recompute final dollar risk after rounding.
        final_dollar_risk = qty * stop_distance

        # ── 5. Compute take-profit target (Phase A — BACKLOG.md 2026-05-01) ─
        # TP_price = entry ± (stop_distance × tier_r_multiple). Sign by
        # direction. Skipped (None) when stop_distance is 0 (degenerate
        # — `_build_order` shouldn't reach here with no stop, but
        # defensive). No TP order placed yet; this surfaces the target
        # in the `would_have_placed` push card and stashes structured
        # fields for Phase B's paper_trade_record table.
        tp_cfg = self._cfg.get("take_profit") or {}
        tier_r_map = tp_cfg.get("tier_r_multiples") or {}
        default_r = float(tp_cfg.get("default_r_multiple", 2.0))
        try:
            tp_r_multiple = float(tier_r_map.get(verdict.tier, default_r))
        except (TypeError, ValueError):
            tp_r_multiple = default_r

        if stop_distance > 0 and tp_r_multiple > 0:
            tp_distance = stop_distance * tp_r_multiple
            if verdict.direction == "long":
                take_profit_price: float | None = price + tp_distance
            else:
                take_profit_price = price - tp_distance
            tp_distance_dollars = tp_distance
            tp_distance_pct = tp_distance / price if price > 0 else 0.0
            expected_gain_if_tp_hit = qty * tp_distance
        else:
            take_profit_price = None
            tp_distance_dollars = 0.0
            tp_distance_pct = 0.0
            expected_gain_if_tp_hit = 0.0

        return ProposedOrder(
            strategy="lord_otter",
            symbol=symbol,
            side=side,
            qty=qty,
            order_type="market",
            limit_price=None,
            rationale=(
                f"Lord Otter {verdict.tier.upper()} tier: {verdict.rationale}. "
                f"Signal={verdict.payload.get('signal')} @ ${price:.2f}. "
                f"Size {size_pct*100:.2f}% equity = ${notional:.2f} notional "
                f"({sizing_basis}). "
                f"Stop ${technical_stop:.2f} ({stop_basis}, "
                f"${stop_distance:.2f} away, ${final_dollar_risk:.2f} risk). "
                + (
                    f"TP ${take_profit_price:.2f} ({tp_r_multiple:.1f}R, "
                    f"+${expected_gain_if_tp_hit:.2f} gain)."
                    if take_profit_price is not None
                    else "TP unavailable (no stop distance)."
                )
                + (" RESIZED for max-loss cap." if resized_for_max_loss else "")
            ),
            extra={
                "asset_type": "crypto",
                "via": "lord_otter_webhook",
                "tier": verdict.tier,
                "direction": verdict.direction,
                "size_pct_equity": size_pct,
                "notional_target": notional,
                "sizing_basis": sizing_basis,
                "source_signal": verdict.payload.get("signal"),
                "tv_payload": verdict.payload,
                "manual": False,
                # Entry reference — `price` arg, captured here so the
                # push formatter and Phase B replay table can read it
                # without re-deriving from the rationale string.
                "entry_reference_price": price,
                # Stop fields — broker layer / future stop-attach agent
                # reads these to place the actual stop order. None of
                # these are placed automatically yet; that's Phase 1.6.
                "stop_price": technical_stop,
                "stop_basis": stop_basis,
                "stop_distance_dollars": stop_distance,
                "stop_distance_pct": stop_distance / price if price > 0 else 0,
                "max_dollar_risk": final_dollar_risk,
                # TP fields (Phase A — BACKLOG.md 2026-05-01).
                "take_profit_price": take_profit_price,
                "tp_basis": "r_multiple" if take_profit_price is not None else "unavailable",
                "tp_r_multiple": tp_r_multiple,
                "tp_distance_dollars": tp_distance_dollars,
                "tp_distance_pct": tp_distance_pct,
                "expected_gain_if_tp_hit": expected_gain_if_tp_hit,
                "expected_loss_if_stopped": -final_dollar_risk,
                "resized_for_max_loss": resized_for_max_loss,
                "is_close": False,
            },
        )

    # ------------------------------------------------------------------
    # Halt management (called by the webhook handler after fills)
    # ------------------------------------------------------------------

    def record_loss(self, symbol: str, loss_pct_equity: float, ts: datetime | None = None) -> None:
        """Update consecutive-loss / daily-loss counters and halt if breached."""
        ts = ts or now_utc()
        state = self.get_state(symbol)
        state.consecutive_losses += 1
        state.daily_realized_pnl_pct -= abs(loss_pct_equity)

        halts = self._cfg.get("halt_conditions") or {}
        max_losses = int(halts.get("consecutive_losses", 3))
        pause_hours = int(halts.get("consecutive_loss_pause_hours", 4))
        daily_cap = float(halts.get("daily_loss_pct", 0.02))

        if state.consecutive_losses >= max_losses:
            state.halted_until = ts + timedelta(hours=pause_hours)
            state.halt_reason = f"{state.consecutive_losses} consecutive losses"
            log.warning(
                "LordOtterAgent: %s halted until %s (%s)",
                symbol, iso(state.halted_until), state.halt_reason,
            )
        elif abs(state.daily_realized_pnl_pct) >= daily_cap:
            # Halt for rest of day (UTC midnight rollover clears it).
            tomorrow = (ts + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0,
            )
            state.halted_until = tomorrow
            state.halt_reason = (
                f"daily loss cap breached: "
                f"{state.daily_realized_pnl_pct*100:.2f}% ≤ -{daily_cap*100:.2f}%"
            )
            log.warning(
                "LordOtterAgent: %s halted until %s (%s)",
                symbol, iso(state.halted_until), state.halt_reason,
            )

    def record_win(self, symbol: str, win_pct_equity: float, ts: datetime | None = None) -> None:
        ts = ts or now_utc()
        state = self.get_state(symbol)
        state.consecutive_losses = 0  # reset on any win
        state.daily_realized_pnl_pct += abs(win_pct_equity)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_ts(raw: Any) -> datetime:
        if raw is None:
            return now_utc()
        if isinstance(raw, datetime):
            return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        except Exception:
            return now_utc()
