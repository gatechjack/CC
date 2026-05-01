"""Market Cypher — TradingView-driven SWING strategy on BTC/USD.

This is the second TV-webhook agent in the system, living alongside
LordOtterAgent. They run independently and can be cross-confirmed
later. Architectural shape mirrors `lord_otter.py` deliberately —
mechanical helpers (state, persistence, sizing, build_order) are
near-identical so future refactoring can extract them to a shared
base. Strategy-specific code (signal vocabulary, `_refresh_state_from_signal`,
`_classify_tier`) is the part that's genuinely different.

Flow:
  TradingView alert (per-signal webhook on 4h or 1D chart)
        ↓
  POST /webhook/tradingview/market-cypher   (in trading_corp.web.webhooks)
        ↓
  MarketCypherAgent.on_alert(payload)
        ├─ updates per-symbol state (bias, sommi, arming, recent alerts)
        ├─ classifies the alert chain into a conviction tier
        ├─ checks halts / cooldowns / news / weekend / time-of-day
        └─ emits a ProposedOrder (or None)
        ↓
  Risk gate → audit log → place (if auto_execute) or notify (if not)

Indicators:
  - VuManChu Cipher A (VMC Cipher_A) — EMA ribbon, divergence diamonds
  - VuManChu Cipher B + Divergences (VMC Cipher_B_Divergences) — wavetrend,
    money flow, divergence-stacked dots, GOLD circle, Sommi (HTF VWAP)

Why swing not scalp:
  Cipher's flagship signals (Green Dot, GOLD circle, Big Green Circle)
  are explicitly TF-gated to 6h+ per the indicator authors. Running this
  on 3m would dilute the signal — primary triggers fire on 4h, bias on
  1D. Hold horizon: hours to days, not minutes.

Phase 1 scope:
  - Spot LONG entries on Coinbase (`direction_policy: long_only`).
  - 8 conviction tiers from `config/strategies.yaml` (0.75%–7.5% equity).
  - Bias asymmetric by design: `mc_a_longema` flips bull (early, single-
    event), `mc_a_blood_diamond` flips bear (decisive, multi-signal stack).
    See BACKLOG.md → "Cypher: bear-bias backup" if Blood Diamond too rare.
  - `auto_execute=false` by default — orders log + Telegram-notify but
    don't place. Same trust-building cadence as Otter.
  - Sommi (HTF VWAP regime) acts as a tier modifier — when Sommi
    disagrees with trade direction, tier downgrades one step.

Phase 2+:
  - Cross-confirmation hooks with LordOtterAgent (e.g. an Otter trade
    that Cypher also likes gets +0.5% size; an Otter trade Cypher
    disagrees with gets HITL-only).
  - Both-direction trading once coinbase_futures is wired.
  - Multi-symbol once we trust BTC behavior.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from trading_corp.data.macro_calendar import MacroCalendar
from trading_corp.persistence.models import ProposedOrder
from trading_corp.utils.time import iso, now_utc

log = logging.getLogger(__name__)

# --------------------------------------------------------------------
# Signal vocabulary — every signal MarketCypherAgent recognizes.
# Keep this list in sync with the alerts wired in TradingView (see the
# 15-alert spec in the conversation that birthed this agent on
# 2026-04-30). Webhook handler validates incoming `signal` against this.
# --------------------------------------------------------------------
KNOWN_SIGNALS = {
    # Cipher A — EMA ribbon + diamond/X markers
    "mc_a_longema",          # EMA bullish cross — bias=bull setter + EMA_FLIP tier trigger
    "mc_a_bluetriangle",     # Trend-change warning — arms long for ~3 bars (4h)
    "mc_a_blood_diamond",    # Red X + Red Diamond stacked — bias=bear setter + 100% close
    "mc_a_red_diamond",      # Bearish — close-long trigger
    "mc_a_redx",             # Bearish caution
    "mc_a_yellow_x",         # Bearish caution / whale manipulation
    # Cipher B — WaveTrend + Money Flow + divergences
    "mc_b_gold_buy",         # GOLD circle — highest-conviction long trigger (RSI<30+WT≤-80+div)
    "mc_b_buy_circle_div",   # Big green circle + divergence — high-conviction long
    "mc_b_buy_circle",       # Big green circle (oversold WT cross) — long trigger
    "mc_b_buy_dot",          # Small green dot (any WT cross) — low-conviction long
    "mc_b_sell_circle_div",  # Big red circle + divergence — high-conviction sell
    "mc_b_sell_circle",      # Big red circle — sell trigger
    "mc_b_sell_dot",         # Small red dot — minor sell signal
    "mc_b_sommi_bull",       # HTF VWAP regime turned bullish — modifier, not a trigger
    "mc_b_sommi_bear",       # HTF VWAP regime turned bearish — modifier, not a trigger
}

# Direction mapping. Bias-side neutral signals (sommi_*) don't appear
# here because they don't drive direction — they're modifiers checked
# inside the tier classifier.
_BULL_SIGNALS = {
    "mc_a_longema", "mc_a_bluetriangle",
    "mc_b_gold_buy", "mc_b_buy_circle_div", "mc_b_buy_circle", "mc_b_buy_dot",
}
_BEAR_SIGNALS = {
    "mc_a_blood_diamond", "mc_a_red_diamond", "mc_a_redx", "mc_a_yellow_x",
    "mc_b_sell_circle_div", "mc_b_sell_circle", "mc_b_sell_dot",
}


def signal_direction(signal: str) -> str:
    """Return 'long' if signal is bullish, 'short' if bearish, '' otherwise.

    Sommi signals return '' — they're state modifiers, not directional
    triggers. The tier classifier reads sommi state directly from
    SymbolState rather than treating it as a directional signal.
    """
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
    """Set when a Cipher A Bluetriangle fires; expires after N bars on the
    primary trigger TF (default 3 bars × 4h = 12 hours)."""
    source: str             # "bluetriangle"
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
    """Mutable state per symbol. Accessed only on the asyncio loop.

    Cypher-specific addition vs Otter: `sommi` field tracking the HTF
    VWAP regime. Sommi is a tier modifier — when it disagrees with the
    trade direction, conviction downgrades one tier.
    """
    symbol: str
    bias: str = "unknown"            # "bull" | "bear" | "unknown"
    sommi: str = "unknown"           # "bull" | "bear" | "unknown"
    armed_long: ArmedState | None = None
    armed_short: ArmedState | None = None
    recent_alerts: list[AlertRecord] = field(default_factory=list)  # ring buffer
    last_trade_at: datetime | None = None      # any direction (legacy compat)
    last_entry_at: datetime | None = None       # last entry order emitted
    last_close_at: datetime | None = None       # last close-long order emitted
    consecutive_losses: int = 0
    daily_realized_pnl_pct: float = 0.0
    daily_pnl_date: date | None = None
    halted_until: datetime | None = None
    halt_reason: str | None = None
    open_long_qty: float = 0.0


# --------------------------------------------------------------------
# Tier classification result
# --------------------------------------------------------------------

@dataclass
class TierVerdict:
    tier: str               # "gold" | "diamond" | "premium" | "big_circle"
                            # | "standard" | "ema_flip" | "solo" |
                            # | "blood_diamond" | "big_red_div" | "big_red"
                            # | "cipher_a_bear" | "standard_bear" | "caution"
                            # | "small_dot"
    direction: str          # "long" | "short"
    size_pct_equity: float  # raw (pre-time-modifier) size %
    rationale: str
    entry_price: float
    payload: dict


# --------------------------------------------------------------------
# Agent
# --------------------------------------------------------------------

class MarketCypherAgent:
    """One agent instance handles all symbols + all timeframes.

    Architectural twin of LordOtterAgent — same lifecycle, same
    persistence, same risk-gate handoff. Different signal vocabulary,
    different tier classifier, different timeframe profile (swing not
    scalp).
    """

    name = "market_cypher"

    # Persistence keys for the agent_state table (see persistence/db.py).
    BIAS_STATE_AGENT_NAME = "market_cypher"
    BIAS_STATE_KEY_PREFIX = "bias:"        # full key: f"bias:{symbol}"
    SOMMI_STATE_KEY_PREFIX = "sommi:"      # full key: f"sommi:{symbol}"
    # Discard persisted state older than this on restart. Cypher's
    # bias is set by 1D events (Longema, Blood Diamond) — anything
    # older than 3 days probably doesn't reflect current regime.
    BIAS_STATE_MAX_AGE = timedelta(days=3)
    # Sommi flips on 1D too — same staleness window.
    SOMMI_STATE_MAX_AGE = timedelta(days=3)

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
        self._db_url = db_url
        self._reload()
        if self._db_url:
            self._restore_bias_state()
            self._restore_sommi_state()

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
            log.warning("MarketCypherAgent: failed to load %s: %s", self._strategies_yaml, e)
            return
        self._cfg = data.get("market_cypher", {}) or {}
        self._mtime = mtime
        log.info(
            "MarketCypherAgent reloaded config: enabled=%s auto_execute=%s "
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
        return str(self._cfg.get("webhook_secret_env", "MARKET_CYPHER_WEBHOOK_SECRET"))

    def is_symbol_allowed(self, symbol: str) -> bool:
        self._reload()
        allowed = {s.upper() for s in (self._cfg.get("symbols") or [])}
        return symbol.upper() in allowed

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
    # Bias + Sommi persistence (see persistence/db.py agent_state table)
    # ------------------------------------------------------------------

    def _persist_bias(self, symbol: str, bias: str) -> None:
        """Best-effort write of latched bias for `symbol`. Failures logged
        but never raise — bias persistence is reliability, not correctness."""
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
                "MarketCypherAgent: failed to persist bias for %s: %s "
                "(in-memory state still valid)", symbol, e,
            )

    def _persist_sommi(self, symbol: str, sommi: str) -> None:
        """Best-effort write of HTF VWAP regime state for `symbol`."""
        if not self._db_url:
            return
        try:
            from trading_corp.persistence.db import set_agent_state
            set_agent_state(
                self.BIAS_STATE_AGENT_NAME,
                f"{self.SOMMI_STATE_KEY_PREFIX}{symbol}",
                {"sommi": sommi, "symbol": symbol},
                db_url=self._db_url,
            )
        except Exception as e:
            log.warning(
                "MarketCypherAgent: failed to persist sommi for %s: %s",
                symbol, e,
            )

    def _restore_bias_state(self) -> None:
        """Restore bias for all configured symbols from DB on startup.
        Skips entries older than `BIAS_STATE_MAX_AGE`. Mirrors Otter's
        equivalent — see lord_otter.py:_restore_bias_state for rationale."""
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
            log.warning("MarketCypherAgent: bias restore unavailable: %s", e)
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
                    "MarketCypherAgent: bias restore failed for %s: %s",
                    symbol, e,
                )
                continue
            if result is None:
                continue
            value, updated_at = result
            age = now - updated_at
            if age > self.BIAS_STATE_MAX_AGE:
                log.info(
                    "MarketCypherAgent: discarding stale bias for %s "
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
                "MarketCypherAgent: restored bias=%s for %s "
                "(set %s ago)", bias, symbol, age,
            )
        if restored or skipped_stale:
            log.info(
                "MarketCypherAgent: bias restore summary — restored=%d skipped_stale=%d",
                restored, skipped_stale,
            )

    def _restore_sommi_state(self) -> None:
        """Restore HTF VWAP regime (sommi) on startup. Same staleness
        treatment as bias — sommi flips on 1D, anything > 3 days old
        is probably no longer reflective of current regime."""
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
            log.warning("MarketCypherAgent: sommi restore unavailable: %s", e)
            return

        now = datetime.now(timezone.utc)
        restored = 0
        for symbol in symbols:
            key = f"{self.SOMMI_STATE_KEY_PREFIX}{symbol}"
            try:
                result = load_agent_state(
                    self.BIAS_STATE_AGENT_NAME, key, db_url=self._db_url,
                )
            except Exception:
                continue
            if result is None:
                continue
            value, updated_at = result
            age = now - updated_at
            if age > self.SOMMI_STATE_MAX_AGE:
                try:
                    delete_agent_state(
                        self.BIAS_STATE_AGENT_NAME, key, db_url=self._db_url,
                    )
                except Exception:
                    pass
                continue
            sommi = (value or {}).get("sommi")
            if sommi not in ("bull", "bear"):
                continue
            state = self.get_state(symbol)
            state.sommi = sommi
            restored += 1
            log.info(
                "MarketCypherAgent: restored sommi=%s for %s (set %s ago)",
                sommi, symbol, age,
            )
        if restored:
            log.info("MarketCypherAgent: sommi restore summary — restored=%d", restored)

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
        """Process one TV alert. Returns (order_or_none, decision_reason)."""
        self._reload()
        if not self.enabled:
            return None, "market_cypher strategy is disabled in config"

        signal = payload.get("signal", "")
        if signal not in KNOWN_SIGNALS:
            return None, f"unknown signal {signal!r} (not in KNOWN_SIGNALS)"

        symbol = payload.get("symbol", "")
        if not self.is_symbol_allowed(symbol):
            return None, f"symbol {symbol!r} not in market_cypher.symbols whitelist"

        ts = self._parse_ts(payload.get("time"))
        price = float(payload.get("price") or 0.0)
        if price <= 0:
            return None, "missing or invalid price"

        state = self.get_state(symbol)
        direction = signal_direction(signal)
        held_qty = held_qty or {}

        # Update state for EVERY alert (regardless of trade outcome).
        self._record_alert(state, signal, direction, price, payload, ts)
        self._refresh_state_from_signal(state, signal, direction, ts)

        # Halt checks
        if state.halted_until and ts < state.halted_until:
            return None, f"strategy halted until {iso(state.halted_until)} ({state.halt_reason})"

        # Daily P&L rollover at UTC midnight
        today = ts.date()
        if state.daily_pnl_date != today:
            state.daily_pnl_date = today
            state.daily_realized_pnl_pct = 0.0
            if state.halted_until and state.halted_until.date() < today:
                state.halted_until = None
                state.halt_reason = None

        # News halt — defer to MacroCalendar
        news_cfg = self._cfg.get("news_halt") or {}
        if news_cfg.get("enabled"):
            in_window, evt = self._macro.is_within_halt_window(
                ts,
                window_minutes=int(news_cfg.get("halt_window_minutes", 30)),
                impact_levels=tuple(news_cfg.get("halt_impact_levels", ["high"])),
            )
            if in_window:
                return None, f"news halt: {evt.name if evt else '?'} at {iso(evt.ts) if evt else '?'}"

        # Sommi-only signals don't drive trades; they're state modifiers
        # checked inside _classify_tier. Acknowledge and exit.
        if signal in ("mc_b_sommi_bull", "mc_b_sommi_bear"):
            return None, f"sommi state updated: {state.sommi} (modifier, no trade emitted)"

        # Direction policy: long_only on Coinbase Spot in Phase 1.
        # Bear signals route to the close-long path with bypass_bias=True.
        direction_policy = str(self._cfg.get("direction_policy", "long_only"))
        if direction_policy == "long_only" and direction == "short":
            return self._handle_close_long(
                state, signal, price, payload, ts, held_qty, account_equity,
            )

        # Entry-path cooldown — Cypher uses HOURS not seconds (default 4h)
        cooldown_sec = int(self._cfg.get("cooldown_seconds", 14400))
        if state.last_entry_at and (ts - state.last_entry_at).total_seconds() < cooldown_sec:
            return None, f"entry cooldown: last entry within {cooldown_sec}s"

        # Chop guard — opposite entries firing within window
        chop_window = int((self._cfg.get("halt_conditions") or {}).get("chop_window_seconds", 1800))
        if self._is_chop(state, ts, chop_window):
            return None, f"chop window: opposite signals within {chop_window}s"

        # Tier classification (entry path: bias-gated)
        verdict = self._classify_tier(
            state, signal, direction, price, payload, ts,
            bypass_bias=False,
        )
        if verdict is None:
            return None, f"signal {signal!r} did not qualify for any tier (likely no bias alignment or insufficient confirmations)"

        # Apply Sommi modifier — if HTF regime disagrees with direction,
        # downgrade tier one step (modeled here as size×0.6).
        size_pct = verdict.size_pct_equity
        sommi_note = ""
        if direction == "long" and state.sommi == "bear":
            size_pct *= 0.6
            sommi_note = " [sommi-downgrade]"
        elif direction == "short" and state.sommi == "bull":
            size_pct *= 0.6
            sommi_note = " [sommi-downgrade]"

        # Apply time-of-day + weekend modifiers
        size_pct = self._apply_time_modifiers(size_pct, ts)

        # Build ProposedOrder
        order = self._build_order(
            verdict, size_pct, price, ts,
            account_equity=account_equity,
        )

        # Mark entry cooldown
        state.last_entry_at = ts
        state.last_trade_at = ts

        return order, (
            f"tier={verdict.tier} direction={verdict.direction} "
            f"size_pct={size_pct:.4f} ({verdict.rationale}){sommi_note}"
        )

    # ------------------------------------------------------------------
    # Bear-signal close-existing-longs path (mirror of Otter's)
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
        """Convert a bear signal into a partial close of any held long."""
        symbol = payload.get("symbol", "")
        held = float(held_qty.get(symbol, 0.0))

        if held <= 0:
            return None, f"bear signal {signal!r} but no long position held in {symbol}"

        # Close cooldown — drop bear-signals firing back-to-back.
        cooldown_sec = int(self._cfg.get("cooldown_seconds", 14400))
        if state.last_close_at and (ts - state.last_close_at).total_seconds() < cooldown_sec:
            return None, f"close cooldown: last close within {cooldown_sec}s"

        # Classify bear conviction tier (bypass bias gate — closes are urgent).
        verdict = self._classify_tier(
            state, signal, "short", price, payload, ts,
            bypass_bias=True,
        )
        if verdict is None:
            return None, (
                f"bear signal {signal!r} did not qualify for any tier "
                f"(no Cipher A bear confirmation in window)"
            )

        # Look up close fraction for this tier.
        close_fractions = self._cfg.get("tier_close_fractions") or {}
        fraction = float(close_fractions.get(verdict.tier, 0.5))
        if fraction <= 0:
            return None, f"tier {verdict.tier} close fraction is 0 (configured no-action)"

        close_qty = round(held * fraction, 8)
        if close_qty <= 0:
            return None, f"close qty rounded to 0 (held={held}, fraction={fraction})"

        state.last_close_at = ts
        state.last_trade_at = ts

        order = ProposedOrder(
            strategy="market_cypher",
            symbol=symbol,
            side="sell",
            qty=close_qty,
            order_type="market",
            limit_price=None,
            rationale=(
                f"Market Cypher {verdict.tier.upper()} CLOSE: {fraction*100:.0f}% "
                f"of {held:.8f} {symbol} held. {verdict.rationale}. "
                f"Signal={signal} @ ${price}."
            ),
            extra={
                "asset_type": "crypto",
                "via": "market_cypher_webhook",
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
        # Cypher window is wider than Otter's — keep more history (50 vs 20)
        # because confirmation lookbacks are 12-24h on 4h+ TFs.
        if len(state.recent_alerts) > 50:
            state.recent_alerts = state.recent_alerts[-50:]

    def _refresh_state_from_signal(
        self,
        state: SymbolState,
        signal: str,
        direction: str,
        ts: datetime,
    ) -> None:
        """Update bias / sommi / arming state based on this alert.

        Cypher's signal-to-state mapping:
          - mc_a_longema       → bias=bull (early single-event)
          - mc_a_blood_diamond → bias=bear (decisive multi-stack)
          - mc_b_sommi_bull    → sommi=bull
          - mc_b_sommi_bear    → sommi=bear
          - mc_a_bluetriangle  → arms long for `arming_window_bars` × 4h
        """
        # Bias updates — see class docstring for asymmetric design rationale.
        if signal == "mc_a_longema":
            state.bias = "bull"
            self._persist_bias(state.symbol, "bull")
        elif signal == "mc_a_blood_diamond":
            state.bias = "bear"
            self._persist_bias(state.symbol, "bear")

        # Sommi (HTF VWAP regime) — modifier, persisted same as bias.
        if signal == "mc_b_sommi_bull":
            state.sommi = "bull"
            self._persist_sommi(state.symbol, "bull")
        elif signal == "mc_b_sommi_bear":
            state.sommi = "bear"
            self._persist_sommi(state.symbol, "bear")

        # Arming — Bluetriangle precedes a Cipher B trigger by design.
        # Arms long-side for `arming_window_bars` bars. No bear arming
        # signal is exposed by the indicator, so bear triggers fire
        # without a prior arm (handled in _classify_tier).
        if signal == "mc_a_bluetriangle":
            state.armed_long = ArmedState(
                source="bluetriangle",
                armed_at=ts,
                expires_at=ts + self._arming_window_duration(),
                direction="long",
            )

        # Expire stale arms.
        if state.armed_long and ts >= state.armed_long.expires_at:
            state.armed_long = None
        if state.armed_short and ts >= state.armed_short.expires_at:
            state.armed_short = None

    def _arming_window_duration(self) -> timedelta:
        """Default 3 bars × 4h = 12h. Cypher operates on 4h primary
        triggers, so the bar duration is hardcoded to 4h here. If we
        later support per-alert TF detection, derive from `interval`
        in the payload like Otter would for 3m."""
        bars = int(self._cfg.get("arming_window_bars", 3))
        return timedelta(hours=bars * 4)

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
    # Tier classification — Cypher's tier ladder
    # ------------------------------------------------------------------

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

        `bypass_bias=True` skips the bias-alignment gate — used for the
        close-long path where bear signals should fire regardless of
        whether bias has flipped yet.
        """
        if direction not in ("long", "short"):
            return None

        sizes = self._cfg.get("tier_sizes", {})
        # Lookback windows for confirmations — much longer than Otter's
        # since this is a swing strategy on 4h/1D bars.
        cipher_a_bull_confirm_seconds = 12 * 3600       # 12h
        cipher_a_bear_confirm_seconds = 12 * 3600       # 12h

        # Bias gate (entry path only — bypassed for closes).
        if not bypass_bias:
            if state.bias == "unknown":
                # No bias observed yet. Allow only the highest-conviction
                # signals to fire on their own (analogous to Otter's
                # solo-otter exception).
                if signal not in ("mc_b_gold_buy", "mc_a_longema"):
                    return None
            else:
                expected_dir = "long" if state.bias == "bull" else "short"
                if direction != expected_dir:
                    return None

        # ===== BULL ENTRY TIERS =====
        if direction == "long":
            # ----- GOLD: highest single-event trigger -----
            if signal == "mc_b_gold_buy":
                return TierVerdict(
                    tier="gold",
                    direction="long",
                    size_pct_equity=float(sizes.get("gold", 0.075)),
                    rationale="GOLD circle — RSI<30 + WT≤-80 + divergence (highest indicator stack)",
                    entry_price=price,
                    payload=payload,
                )

            # Recent Cipher A bullish confirm (Bluetriangle within 12h
            # OR Longema within 24h). Bluetriangle precedes Green Dot;
            # Longema is the regime change itself.
            cipher_a_confirm = self._has_recent_signal(
                state, ts, seconds=cipher_a_bull_confirm_seconds,
                signals=("mc_a_bluetriangle",),
            ) or self._has_recent_signal(
                state, ts, seconds=24 * 3600,
                signals=("mc_a_longema",),
            )

            # ----- DIAMOND / PREMIUM: Big circle + Div -----
            if signal == "mc_b_buy_circle_div":
                if cipher_a_confirm:
                    return TierVerdict(
                        tier="diamond",
                        direction="long",
                        size_pct_equity=float(sizes.get("diamond", 0.05)),
                        rationale="Big green circle + divergence + Cipher A confirm in 12h",
                        entry_price=price,
                        payload=payload,
                    )
                return TierVerdict(
                    tier="premium",
                    direction="long",
                    size_pct_equity=float(sizes.get("premium", 0.04)),
                    rationale="Big green circle + divergence (no Cipher A confirm)",
                    entry_price=price,
                    payload=payload,
                )

            # ----- BIG_CIRCLE / STANDARD: Big circle without div -----
            if signal == "mc_b_buy_circle":
                if cipher_a_confirm:
                    return TierVerdict(
                        tier="big_circle",
                        direction="long",
                        size_pct_equity=float(sizes.get("big_circle", 0.03)),
                        rationale="Big green circle + Cipher A confirm in 12h",
                        entry_price=price,
                        payload=payload,
                    )
                return TierVerdict(
                    tier="standard",
                    direction="long",
                    size_pct_equity=float(sizes.get("standard", 0.02)),
                    rationale="Big green circle alone (no divergence, no Cipher A confirm)",
                    entry_price=price,
                    payload=payload,
                )

            # ----- EMA_FLIP: Longema fires (catching the regime turn) -----
            if signal == "mc_a_longema":
                return TierVerdict(
                    tier="ema_flip",
                    direction="long",
                    size_pct_equity=float(sizes.get("ema_flip", 0.02)),
                    rationale="EMA ribbon flipped bullish — catching regime change early",
                    entry_price=price,
                    payload=payload,
                )

            # ----- SOLO: Small green dot (lowest bull tier) -----
            if signal == "mc_b_buy_dot":
                return TierVerdict(
                    tier="solo",
                    direction="long",
                    size_pct_equity=float(sizes.get("solo", 0.0075)),
                    rationale="Small green dot, bias-aligned, no other confirms",
                    entry_price=price,
                    payload=payload,
                )

            # mc_a_bluetriangle on its own doesn't trade — it just arms.
            # State was already updated in _refresh_state_from_signal.
            return None

        # ===== BEAR CLOSE-LONG TIERS =====
        if direction == "short":
            # ----- BLOOD_DIAMOND: full close (highest bear single-event) -----
            if signal == "mc_a_blood_diamond":
                return TierVerdict(
                    tier="blood_diamond",
                    direction="short",
                    size_pct_equity=0.0,  # close fraction lives in tier_close_fractions
                    rationale="Blood Diamond — Red X + Red Diamond stacked, full exit",
                    entry_price=price,
                    payload=payload,
                )

            # ----- BIG_RED_DIV: Big red circle + divergence (full close) -----
            if signal == "mc_b_sell_circle_div":
                return TierVerdict(
                    tier="big_red_div",
                    direction="short",
                    size_pct_equity=0.0,
                    rationale="Big red circle + divergence (high-conviction reversal)",
                    entry_price=price,
                    payload=payload,
                )

            # Recent Cipher A bear confirm
            cipher_a_bear = self._has_recent_signal(
                state, ts, seconds=cipher_a_bear_confirm_seconds,
                signals=("mc_a_red_diamond", "mc_a_redx", "mc_a_yellow_x"),
            )

            # ----- BIG_RED: Big red circle + Cipher A confirm OR bias=bear -----
            if signal == "mc_b_sell_circle":
                if cipher_a_bear or state.bias == "bear":
                    return TierVerdict(
                        tier="big_red",
                        direction="short",
                        size_pct_equity=0.0,
                        rationale="Big red circle + Cipher A bear confirm OR bias=bear",
                        entry_price=price,
                        payload=payload,
                    )
                return TierVerdict(
                    tier="standard_bear",
                    direction="short",
                    size_pct_equity=0.0,
                    rationale="Big red circle alone (no Cipher A confirm, no bear bias)",
                    entry_price=price,
                    payload=payload,
                )

            # ----- CIPHER_A_BEAR: Red Diamond + (RedX or YellowX) recent -----
            if signal == "mc_a_red_diamond":
                if self._has_recent_signal(
                    state, ts, seconds=cipher_a_bear_confirm_seconds,
                    signals=("mc_a_redx", "mc_a_yellow_x"),
                ):
                    return TierVerdict(
                        tier="cipher_a_bear",
                        direction="short",
                        size_pct_equity=0.0,
                        rationale="Red Diamond + (RedX or YellowX) within 12h",
                        entry_price=price,
                        payload=payload,
                    )
                return TierVerdict(
                    tier="caution",
                    direction="short",
                    size_pct_equity=0.0,
                    rationale="Red Diamond alone (no RedX/YellowX confirm)",
                    entry_price=price,
                    payload=payload,
                )

            # ----- CAUTION: RedX or YellowX alone -----
            if signal in ("mc_a_redx", "mc_a_yellow_x"):
                return TierVerdict(
                    tier="caution",
                    direction="short",
                    size_pct_equity=0.0,
                    rationale=f"{signal} alone (minor bearish caution)",
                    entry_price=price,
                    payload=payload,
                )

            # ----- SMALL_DOT: minor de-risk -----
            if signal == "mc_b_sell_dot":
                return TierVerdict(
                    tier="small_dot",
                    direction="short",
                    size_pct_equity=0.0,
                    rationale="Small red dot — minor de-risk",
                    entry_price=price,
                    payload=payload,
                )

            return None

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
    # Time-of-day modifiers (same shape as Otter)
    # ------------------------------------------------------------------

    def _apply_time_modifiers(self, base_size: float, ts: datetime) -> float:
        tm = self._cfg.get("time_modifiers") or {}
        if not tm.get("enabled"):
            return base_size

        size = base_size

        # Weekend size multiplier
        weekend_mult = float(tm.get("weekend_size_multiplier", 1.0))
        if weekend_mult != 1.0:
            wd = ts.weekday()
            hour = ts.hour
            in_weekend = (
                (wd == 4 and hour >= 22)
                or wd == 5
                or (wd == 6 and hour < 22)
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
                break

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
        """Translate TierVerdict into ProposedOrder.

        Stop loss for swing trades uses a wider buffer than Otter's
        scalp stops:
          long  → stop = bar_low  × (1 - swing_buffer_pct)
          short → stop = bar_high × (1 + swing_buffer_pct)

        Defaults: swing_buffer_pct=0.005 (0.5%), max_loss_pct=0.02 (2%).
        Wider than Otter because trades hold for hours/days, not minutes.
        """
        symbol = verdict.payload.get("symbol", "")
        side = "buy" if verdict.direction == "long" else "sell"

        # Notional from equity
        if account_equity and account_equity > 0:
            notional = float(account_equity) * float(size_pct)
            sizing_basis = "equity_aware"
        else:
            notional = 50.0 * (size_pct / 0.02 if size_pct > 0 else 1.0)
            sizing_basis = "placeholder_50usd"

        qty = notional / price if price > 0 else 0.0
        if qty <= 0:
            qty = 0.0001

        # Technical stop from trigger bar — wider buffer than Otter
        stop_cfg = self._cfg.get("stop_loss") or {}
        buffer_pct = float(stop_cfg.get("swing_buffer_pct", 0.005))
        fallback_pct = float(stop_cfg.get("fallback_stop_distance_pct", 0.02))
        max_loss_pct = float(stop_cfg.get("max_loss_pct_equity", 0.02))

        # Cypher payload uses lowercase OHLC keys (open/high/low). We accept
        # either name for resilience to upstream changes.
        bar_low = float(verdict.payload.get("low") or verdict.payload.get("bar_low") or 0)
        bar_high = float(verdict.payload.get("high") or verdict.payload.get("bar_high") or 0)

        if verdict.direction == "long":
            if bar_low > 0 and bar_low < price:
                technical_stop = bar_low * (1 - buffer_pct)
                stop_basis = "trigger_bar_low"
            else:
                technical_stop = price * (1 - fallback_pct)
                stop_basis = "fallback_pct"
            stop_distance = price - technical_stop
        else:
            if bar_high > price:
                technical_stop = bar_high * (1 + buffer_pct)
                stop_basis = "trigger_bar_high"
            else:
                technical_stop = price * (1 + fallback_pct)
                stop_basis = "fallback_pct"
            stop_distance = technical_stop - price

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

        final_dollar_risk = qty * stop_distance

        # Take-profit target (Phase A — BACKLOG.md 2026-05-01).
        # TP_price = entry ± (stop_distance × tier_r_multiple). Sign by
        # direction. None when stop_distance is 0 (degenerate). No TP
        # order placed yet; this surfaces the target in the
        # `would_have_placed` push card and stashes structured fields
        # for Phase B's paper_trade_record table.
        tp_cfg = self._cfg.get("take_profit") or {}
        tier_r_map = tp_cfg.get("tier_r_multiples") or {}
        default_r = float(tp_cfg.get("default_r_multiple", 2.5))
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
            strategy="market_cypher",
            symbol=symbol,
            side=side,
            qty=qty,
            order_type="market",
            limit_price=None,
            rationale=(
                f"Market Cypher {verdict.tier.upper()} tier: {verdict.rationale}. "
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
                "via": "market_cypher_webhook",
                "tier": verdict.tier,
                "direction": verdict.direction,
                "size_pct_equity": size_pct,
                "notional_target": notional,
                "sizing_basis": sizing_basis,
                "source_signal": verdict.payload.get("signal"),
                "tv_payload": verdict.payload,
                "manual": False,
                "entry_reference_price": price,
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
    # Halt management
    # ------------------------------------------------------------------

    def record_loss(self, symbol: str, loss_pct_equity: float, ts: datetime | None = None) -> None:
        ts = ts or now_utc()
        state = self.get_state(symbol)
        state.consecutive_losses += 1
        state.daily_realized_pnl_pct -= abs(loss_pct_equity)

        halts = self._cfg.get("halt_conditions") or {}
        max_losses = int(halts.get("consecutive_losses", 3))
        pause_hours = int(halts.get("consecutive_loss_pause_hours", 12))  # longer than Otter
        daily_cap = float(halts.get("daily_loss_pct", 0.04))               # wider than Otter

        if state.consecutive_losses >= max_losses:
            state.halted_until = ts + timedelta(hours=pause_hours)
            state.halt_reason = f"{state.consecutive_losses} consecutive losses"
            log.warning(
                "MarketCypherAgent: %s halted until %s (%s)",
                symbol, iso(state.halted_until), state.halt_reason,
            )
        elif abs(state.daily_realized_pnl_pct) >= daily_cap:
            tomorrow = (ts + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0,
            )
            state.halted_until = tomorrow
            state.halt_reason = (
                f"daily loss cap breached: "
                f"{state.daily_realized_pnl_pct*100:.2f}% ≤ -{daily_cap*100:.2f}%"
            )
            log.warning(
                "MarketCypherAgent: %s halted until %s (%s)",
                symbol, iso(state.halted_until), state.halt_reason,
            )

    def record_win(self, symbol: str, win_pct_equity: float, ts: datetime | None = None) -> None:
        ts = ts or now_utc()
        state = self.get_state(symbol)
        state.consecutive_losses = 0
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
