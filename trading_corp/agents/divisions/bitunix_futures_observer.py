"""BitUnix Futures Phase 3 — observer + order proposer (paper-mode auto-execute).

Phase 3.0 (shipped 2026-05-10 14:19 UTC) — bias-only observer.
Phase 3.1 (this) — adds:
  - CVD volume axis (state machine fed by `cvd_bull_flip` / `cvd_bear_flip` events)
  - Full tier ladder: PREMIUM / STANDARD / WEAK / COUNTER / SKIP
  - Order proposer with structural stop, effective-risk cap, multi-leg-ready tp_plan
  - Daily-risk kill-switch (board-approved cap, NOT per-trade HITL)
  - Telegram notification on placement (paper-mode `would_have_placed`)
  - Audit `bitunix_decided` for every signal, regardless of trade outcome

Design memory: `trading_corp_bitunix_phase3_confluence_model`.

The class name `BitunixFuturesObserver` is preserved from Phase 3.0 to avoid
import churn — but at Phase 3.1 it's a full division agent: receives signals,
maintains state, decides trades, submits to risk gate, emits orders. Renaming
is a Phase 4-or-later concern.

Operating mode: PAPER ONLY. The bitunix_futures division has a paper-exec
broker registered (verified 2026-05-10 14:22 UTC). `auto_execute: true` per
board direction — no per-trade HITL. The board approves the GUARDRAILS
(sizing %, daily loss kill, leverage caps) once; orders flow autonomously
inside those caps. When live mode flips, the same gates apply.

Class entry points:
  `observe_alert(payload, source)`     — sync. Updates bias/CVD state, classifies
                                          triggers, returns TierVerdict | None.
  `observe_and_decide(payload, source)` — async. Calls observe_alert; if a
                                          tradeable tier results, proposes the
                                          order, gates via RiskAgent, simulates
                                          paper fill, logs decisions, notifies.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from trading_corp.persistence import db
from trading_corp.persistence.models import (
    AccountState,
    PaperTradeRecord,
    ProposedOrder,
    StrategyState,
)
# Phase 3.2 — confluence score accumulator (additive; behind feature flag).
# When `scoring_config.enabled` is True, the observer ALSO runs the score
# engine on every alert (in parallel to the Phase 3.1 single-bar classifier).
# When False, only Phase 3.1 fires. This lets us A/B compare in prod paper
# mode before retiring Phase 3.1.
from trading_corp.agents.strategies.bitunix_confluence import (
    AlertEvent as _ScoreAlertEvent,
    BitUnixAlertEvent,
    BitUnixConfluenceConfig,
    PriceContext as _ScorePriceContext,
    Side as _ScoreSide,
    Tier as _ScoreTier,
    evaluate_confluence_futures,
    filter_live_alerts_with_dedupe,
)
from trading_corp.agents.strategies.bitunix_htf_regime import (
    HTFRegimeConfig,
    Regime as _HTFRegime,
    get_trade_permissions,
)
from trading_corp.agents.strategies.bitunix_pa_validation import (
    PAValidationConfig,
    PAValidationDecision,
    evaluate_pa_validation,
)
# PR 4 — adaptive trade plan (MVP + Option C). See
# trading_corp_bitunix_strategy_gaps.md for the decided design.
from trading_corp.agents.strategies.swing import get_recent_swing
from trading_corp.agents.strategies.levels import get_htf_levels
from trading_corp.agents.strategies.trade_plan import (
    FeeConfig,
    StrategyConfig,
    TradePlan,
    build_trade_plan,
)


# ── PR 3c — chart-timeframe normalization ───────────────────────────────


def _normalize_tf(raw: Any) -> str | None:
    """Map TradingView's `{{interval}}` strings to canonical `tf` labels.

    TV emits ints/strings depending on chart: "3", "15", "30", "60",
    "240", "D" or "1D". Canonical labels match `score_timeframes`:
    "3m", "15m", "30m", "1h", "4h", "1d".

    Unknown values pass through verbatim (lowercased) — gives the
    replay script and audit log a chance to spot misconfigured alerts
    without silently dropping them.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    # Numeric minutes
    minute_map = {
        "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
        "60": "1h", "120": "2h", "240": "4h", "360": "6h",
        "480": "8h", "720": "12h",
    }
    if s in minute_map:
        return minute_map[s]
    if s in ("d", "1d", "day", "daily"):
        return "1d"
    if s in ("w", "1w", "week", "weekly"):
        return "1w"
    if s in ("m", "1m"):
        # Ambiguous — TV's "M" is monthly, but "1m" might mean 1-minute.
        # We've already matched "1" → "1m" above, so a literal "1m"
        # string here means whatever the alert author wrote; trust it.
        return s
    # Already-canonical labels pass through.
    return s

log = logging.getLogger(__name__)


# ====================================================================
# Signal vocabulary — tied to existing TV alert names
# ====================================================================

# Cypher BIAS-SETTERS — when a signal in this set fires on 4h or 1D,
# update bias for that TF. Mirrors `_BULL_SIGNALS` / `_BEAR_SIGNALS` from
# `trading_corp/agents/strategies/market_cypher.py`. Dot signals
# (mc_b_buy_dot, mc_b_sell_dot) are EXCLUDED — too low-conviction to
# move HTF bias.
CYPHER_BIAS_BULL = {
    "mc_a_longema",
    "mc_a_bluetriangle",
    "mc_b_gold_buy",
    "mc_b_buy_circle_div",
    "mc_b_buy_circle",
}
CYPHER_BIAS_BEAR = {
    "mc_a_blood_diamond",
    "mc_a_red_diamond",
    "mc_a_redx",
    "mc_a_yellow_x",
    "mc_b_sell_circle_div",
    "mc_b_sell_circle",
}

# Otter TRIGGERS — when a signal in this set fires on 3m, classify it
# against current HTF bias + CVD. Mirrors `_BULL_SIGNALS` / `_BEAR_SIGNALS`
# from `trading_corp/agents/strategies/lord_otter.py` MINUS CVD flips
# (those are routed to the volume axis, not used as entry triggers).
OTTER_TRIGGER_BULL = {
    "otter_buy",
    "spoon_bull",
    "water_buy_small",
    "water_buy_large",
    "money_bag_bottom",
}
OTTER_TRIGGER_BEAR = {
    "otter_sell",
    "spoon_bear",
    "water_sell_small",
    "water_sell_large",
    "money_bag_top",
}

# CVD volume-axis signals (consumed by Phase 3.1, not by tier classifier as triggers)
CVD_FLIP_BULL = "cvd_bull_flip"
CVD_FLIP_BEAR = "cvd_bear_flip"


# ====================================================================
# Tunables (in-code defaults; could be lifted to YAML later if needed)
# ====================================================================

# Bias decay windows — same-direction signals refresh; otherwise neutral.
DECAY_4H_SECONDS = 24 * 3600        # 24h
DECAY_1D_SECONDS = 7 * 86400        # 7d

# CVD direction stale after 30 min — typical 3m CVD-flip cadence.
CVD_DECAY_SECONDS = 30 * 60

# Risk caps (board-approved per memory). These are the gate to
# auto-execution; per-trade HITL is intentionally OFF for bitunix_futures.
EFFECTIVE_RISK_PER_TRADE_PCT = 0.005   # 0.5% account equity per trade
DAILY_RISK_KILL_PCT = 0.03             # 3% account equity per UTC day (cumulative at-risk)

# Stop / TP defaults
ATR_FALLBACK_PCT = 0.0004              # 0.04% × price (placeholder until live OHLCV lands)
ATR_MULTIPLIER = 1.5                   # stop = 1.5 × ATR
STOP_FLOOR_PCT = 0.003                 # 0.3% absolute floor
DEFAULT_TP_R = 2.0                     # 2R take-profit
MIN_RR_RATIO = 1.5                     # refuse trades below this R:R

# Tier sizing — % equity at TARGET (downsized by effective-risk cap if needed)
TIER_SIZING: dict[str, dict[str, float]] = {
    "PREMIUM":  {"size_pct": 0.04, "leverage": 8.0},
    "STANDARD": {"size_pct": 0.02, "leverage": 5.0},
    "WEAK":     {"size_pct": 0.01, "leverage": 2.0},
    "COUNTER":  {"size_pct": 0.005, "leverage": 2.0},  # default OFF
}


# ====================================================================
# Symbol whitelist — Phase 3 is BTC-only per BitUnix vision
# ====================================================================

ALLOWED_SYMBOLS = {"BTC/USD", "BTCUSD", "BTCUSDT", "BTCUSDT.P"}
TRADE_SYMBOL = "BTC/USDT.P"            # canonical symbol used in ProposedOrder


# PR 4 — helper for v2 pre-flight guard skips.
def _make_skip_plan(entry: float, reason: str) -> TradePlan:
    return TradePlan(
        entry=entry, stop_loss=0.0, tp1=0.0, tp2=0.0, tp3=0.0,
        sl_method="", tp2_method="", risk_per_unit=0.0,
        skip_reason=reason,
    )


# ====================================================================
# Schemas (DDL applied at construction)
# ====================================================================

OBSERVER_BIAS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS bitunix_observer_bias (
    timeframe       TEXT NOT NULL,             -- '4h' or '1d'
    side            TEXT NOT NULL,             -- 'bull' or 'bear'
    last_setter_ts  TEXT NOT NULL,             -- ISO8601 UTC; refreshed each same-side fire
    last_signal     TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (timeframe, side)
);
"""

OBSERVER_CVD_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS bitunix_observer_cvd (
    side          TEXT PRIMARY KEY,            -- 'bull' or 'bear'
    last_flip_ts  TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""

OBSERVER_DAILY_RISK_DDL = """
CREATE TABLE IF NOT EXISTS bitunix_observer_daily_risk (
    utc_date                TEXT PRIMARY KEY,         -- 'YYYY-MM-DD'
    cumulative_at_risk_pct  REAL NOT NULL DEFAULT 0,  -- sum of effective-risk across orders
    orders_count            INTEGER NOT NULL DEFAULT 0,
    updated_at              TEXT NOT NULL
);
"""

# Phase 3.2 — append-only ledger of every inbound signal (Otter + Cypher).
# The score engine reads this to compute current bull/bear scores. Only
# touched when `scoring_config.enabled=True`.
OBSERVER_SIGNAL_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS bitunix_signal_ledger (
    ts           TEXT NOT NULL,        -- event time from payload (or insert ts)
    signal       TEXT NOT NULL,        -- lowercase signal name
    source       TEXT NOT NULL,        -- 'lord_otter' | 'market_cypher'
    inserted_at  TEXT NOT NULL,        -- when we ingested it
    tf           TEXT                  -- PR 3c — chart timeframe ("3m"|"15m"|"1d"|...)
);
"""
OBSERVER_SIGNAL_LEDGER_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS bitunix_signal_ledger_ts_idx "
    "ON bitunix_signal_ledger(ts)"
)

# Phase 3.2 — last fire timestamp per side for cooldown enforcement.
OBSERVER_SCORE_COOLDOWN_DDL = """
CREATE TABLE IF NOT EXISTS bitunix_score_cooldown (
    side          TEXT PRIMARY KEY,    -- 'buy' or 'sell'
    last_fire_ts  TEXT NOT NULL,
    last_tier     TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""

# Longest factor TTL — used to bound the ledger read window. Computed
# from the YAML at config load; default 24h if config unavailable.
DEFAULT_MAX_TTL_MINUTES = 1440


# ====================================================================
# Helpers
# ====================================================================


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _utc_today_iso_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _interval_to_tf(interval: str | int | None) -> str | None:
    """Map TradingView `interval` payload field to our TF slug."""
    if interval is None:
        return None
    s = str(interval).strip().upper()
    if s == "3":
        return "3m"
    if s == "240":
        return "4h"
    if s in ("1D", "D"):
        return "1d"
    return None


def _signal_to_bias_side(signal: str) -> str | None:
    if signal in CYPHER_BIAS_BULL:
        return "bull"
    if signal in CYPHER_BIAS_BEAR:
        return "bear"
    return None


def _signal_to_trigger_side(signal: str) -> str | None:
    if signal in OTTER_TRIGGER_BULL:
        return "bull"
    if signal in OTTER_TRIGGER_BEAR:
        return "bear"
    return None


# ====================================================================
# Result dataclasses
# ====================================================================


@dataclass
class BiasSnapshot:
    timeframe: str           # '4h' or '1d'
    side: str                # 'bull' | 'bear' | 'neutral'
    last_setter_ts: str | None
    age_seconds: int | None


@dataclass
class CVDSnapshot:
    side: str                # 'bull' | 'bear' | 'neutral'
    last_flip_ts: str | None
    age_seconds: int | None


@dataclass
class TierVerdict:
    """Result of classifying one Otter trigger event.

    Phase 3.1 ladder: PREMIUM | STANDARD | WEAK | COUNTER | SKIP.
    The Phase 3.0 bias-only labels (STRONG_HTF / MODERATE_HTF / etc.) are
    no longer emitted; the bias dimension is now folded into the full ladder.
    """
    tier: str
    trigger_signal: str
    trigger_side: str
    trigger_tf: str
    trigger_ts: str
    bias_4h: BiasSnapshot
    bias_1d: BiasSnapshot
    cvd: CVDSnapshot
    rationale: str


@dataclass
class OrderProposal:
    """Result of building an order from a TierVerdict + account equity.

    `proposed_order` is None when sizing math rejected the trade
    (e.g. R:R below MIN_RR_RATIO, or qty rounded to 0).
    `reason` describes why None.
    """
    proposed_order: ProposedOrder | None
    reason: str
    effective_risk_pct: float | None = None     # actual after sizing math
    target_size_pct: float | None = None        # tier target before downsizing
    leverage: float | None = None
    stop_distance_pct: float | None = None
    stop_price: float | None = None
    tp_price: float | None = None
    rr_ratio: float | None = None


# ====================================================================
# Observer / division agent
# ====================================================================


class BitunixFuturesObserver:
    """Phase 3.1 BitUnix Futures division agent.

    Construction is cheap; pass at minimum a `db_url`. Live deps
    (risk_agent, data_exec, logger_agent, telegram_channel) are optional —
    when omitted, the class degrades to PURE OBSERVER MODE (no order
    proposing, no risk-gate calls). All deps are required for Phase 3.1
    `observe_and_decide`.

    Every public method wraps in try/except — never raises out to its
    caller. Failures are logged and swallowed so the existing webhook →
    agent path stays unaffected.
    """

    def __init__(
        self,
        db_url: str,
        *,
        risk_agent: Any = None,
        data_exec: Any = None,
        logger_agent: Any = None,
        telegram_channel: Any = None,
        bar_cache: Any = None,            # LiveBarCache | None
        counter_enabled: bool = False,
        max_hold_seconds: int = 24 * 3600,  # default scalp horizon for paper resolution
        scoring_config: BitUnixConfluenceConfig | None = None,
        # PR 3c — HTF + PA gate plumbing. All optional; observer
        # reverts to pre-PR-3c behavior when any are None / mode='off'.
        htf_provider: Any = None,            # BitUnixHTFContextProvider | None
        htf_config: HTFRegimeConfig | None = None,
        pa_config: PAValidationConfig | None = None,
        htf_gate_mode: str = "off",          # "off" | "shadow" | "enforce"
        # PR 4 — adaptive trade plan (MVP + Option C). When both configs
        # are set, the score path uses `_build_proposal_v2` (structure-
        # preferred SL + 3-leg TP) instead of the legacy geometric path.
        # Defaults to None on both so callers without YAML still see
        # pre-PR-4 behavior. Activation = YAML `trade_plan.enabled: true`.
        trade_plan_config: StrategyConfig | None = None,
        fee_config: FeeConfig | None = None,
    ) -> None:
        self.db_url = db_url
        self.risk_agent = risk_agent
        self.data_exec = data_exec
        self.logger_agent = logger_agent
        self.telegram_channel = telegram_channel
        self.bar_cache = bar_cache
        self.counter_enabled = counter_enabled
        self.max_hold_seconds = max_hold_seconds
        self.scoring_config = scoring_config
        # PR 3c additions — all optional so existing callers (tests
        # constructing the observer with only db_url) keep working.
        self.htf_provider = htf_provider
        self.htf_config = htf_config
        self.pa_config = pa_config
        # PR 4 — adaptive trade plan configs. None means legacy path.
        self.trade_plan_config = trade_plan_config
        self.fee_config = fee_config
        # Normalize the gate mode to one of the three known values.
        self.htf_gate_mode = (
            htf_gate_mode.lower() if isinstance(htf_gate_mode, str) else "off"
        )
        if self.htf_gate_mode not in ("off", "shadow", "enforce"):
            log.warning(
                "bitunix_observer: unknown htf_gate_mode %r — defaulting to 'off'",
                htf_gate_mode,
            )
            self.htf_gate_mode = "off"
        # Cache the longest TTL across factors so the ledger read window
        # doesn't drag in stale rows. Falls back to 24h if config absent.
        # PR 3c: also factor in `factor_ttl_per_tf` overrides — the
        # 30m chart's 180m TTL is bigger than any base ttl_minutes.
        self._max_ttl_minutes = DEFAULT_MAX_TTL_MINUTES
        if scoring_config is not None and scoring_config.factors:
            ttls: list[int] = []
            for f in scoring_config.factors.values():
                if f.ttl_minutes > 0:
                    ttls.append(f.ttl_minutes)
                per_tf = scoring_config.factor_ttl_per_tf.get(f.name) or {}
                ttls.extend(int(v) for v in per_tf.values() if int(v) > 0)
            if ttls:
                self._max_ttl_minutes = max(ttls)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
            with db.connect(self.db_url) as conn:
                conn.execute(OBSERVER_BIAS_TABLE_DDL)
                conn.execute(OBSERVER_CVD_TABLE_DDL)
                conn.execute(OBSERVER_DAILY_RISK_DDL)
                conn.execute(OBSERVER_SIGNAL_LEDGER_DDL)
                conn.execute(OBSERVER_SIGNAL_LEDGER_INDEX_DDL)
                conn.execute(OBSERVER_SCORE_COOLDOWN_DDL)
                # PR 3c — idempotent migration: add `tf` column to
                # existing ledger tables. CREATE TABLE IF NOT EXISTS
                # leaves a pre-PR-3c table unchanged, so we ALTER it
                # on first boot. Historical rows get tf=NULL — replay
                # script accepts this caveat (see PR 3c b-ii decision).
                cols = {row[1] for row in conn.execute(
                    "PRAGMA table_info(bitunix_signal_ledger)"
                ).fetchall()}
                if "tf" not in cols:
                    conn.execute(
                        "ALTER TABLE bitunix_signal_ledger ADD COLUMN tf TEXT"
                    )
        except Exception as e:
            log.warning("bitunix_observer: schema init failed: %s", e)

    # ── public sync entry: classify only, no order ────────────────────

    def observe_alert(
        self,
        payload: dict[str, Any],
        *,
        source: str,
    ) -> TierVerdict | None:
        """Process one webhook alert (state-only — no order proposal).

        - Cypher bias-setter (4h/1D) → update bias state, return None.
        - Otter cvd_*_flip (3m) → update CVD state, return None.
        - Otter trigger (3m) → classify, log audit, return TierVerdict.
        - Anything else → ignore, return None.

        Use this when you want classification telemetry without any chance
        of order emission. For the full Phase 3.1 path, call the async
        `observe_and_decide` instead.
        """
        try:
            return self._observe_alert_inner(payload, source=source)
        except Exception as e:
            log.warning("bitunix_observer: observe_alert failed: %s", e, exc_info=True)
            return None

    # ── public async entry: classify + maybe propose order ────────────

    async def observe_and_decide(
        self,
        payload: dict[str, Any],
        *,
        source: str,
    ) -> TierVerdict | None:
        """Phase 3.1 + Phase 3.2 entrypoint.

        Always:
          - Updates bias/CVD state (Phase 3.0+)
          - Appends to signal ledger (Phase 3.2; cheap insert)

        Then dispatches to ONE order-proposal path based on
        `scoring_config.enabled`:
          - True (Phase 3.2): score engine reads ledger, evaluates score
            on every alert (not just Otter triggers), may fire any side
          - False (Phase 3.1): single-bar `_tier_for` classifier runs
            only on Otter triggers (current behavior)

        Returns the Phase 3.1 `TierVerdict` for backwards compatibility
        with the audit row layout (`bitunix_observer_classified`). The
        score-path decision is captured in `bitunix_score_decided`.
        """
        verdict = self.observe_alert(payload, source=source)

        # Append every signal to the ledger regardless of feature flag —
        # gives us replay data to backtest with later even when scoring
        # is off. Cheap insert; bounded by max_ttl row pruning below.
        try:
            self._append_to_ledger(payload, source=source)
        except Exception as e:
            log.warning("bitunix_observer: ledger append failed: %s", e)

        try:
            if self.scoring_config is not None and self.scoring_config.enabled:
                await self._score_and_maybe_propose(payload, source=source)
            elif verdict is not None:
                await self._maybe_propose(verdict, payload)
        except Exception as e:
            log.warning("bitunix_observer: propose path failed: %s", e, exc_info=True)
        return verdict

    # ── Phase 3.2 — signal ledger ─────────────────────────────────────

    def _append_to_ledger(self, payload: dict[str, Any], *, source: str) -> None:
        symbol = payload.get("symbol") or payload.get("ticker") or ""
        if symbol not in ALLOWED_SYMBOLS:
            return
        signal = (payload.get("signal") or "").strip().lower()
        if not signal:
            return
        ts = payload.get("time") or _utc_now_iso()
        now = _utc_now_iso()
        # PR 3c — extract chart TF from the payload's `interval` field.
        # TradingView's `{{interval}}` placeholder yields strings like
        # "3", "15", "30", "60", "240", "D" / "1D". Normalize to the
        # canonical labels used by `score_timeframes` ("3m", "15m",
        # "30m", "1h", "4h", "1d"). Unknown intervals are stored
        # verbatim — replay can decide what to do with them.
        tf = _normalize_tf(payload.get("interval"))
        with db.connect(self.db_url) as conn:
            conn.execute(
                "INSERT INTO bitunix_signal_ledger "
                "(ts, signal, source, inserted_at, tf) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, signal, source, now, tf),
            )

    def _read_live_ledger(self, now: datetime) -> list[BitUnixAlertEvent]:
        """Pull ledger rows within `_max_ttl_minutes` of `now` and return
        as BitUnixAlertEvent list (carries `tf`). The scorer's
        `filter_live_alerts_with_dedupe` handles per-factor TTL,
        TF filter, and dedupe.

        Historical rows (pre-PR-3c) have `tf=NULL` — they pass through
        any `score_timeframes` filter as None, which means the filter
        drops them when active. This is the expected
        backwards-compat behavior; replay script for the cutover
        accepts the caveat (option (b)(ii) in PR 3c plan).
        """
        cutoff = (now - timedelta(minutes=self._max_ttl_minutes)).isoformat()
        with db.connect(self.db_url) as conn:
            rows = conn.execute(
                "SELECT ts, signal, tf FROM bitunix_signal_ledger "
                "WHERE ts >= ? ORDER BY ts",
                (cutoff,),
            ).fetchall()
        out: list[BitUnixAlertEvent] = []
        for r in rows:
            ts = _parse_iso(r["ts"])
            if ts is None:
                continue
            out.append(BitUnixAlertEvent(
                ts=ts, signal_name=r["signal"], tf=r["tf"],
            ))
        return out

    # ── Phase 3.2 — score cooldown state ──────────────────────────────

    def _read_cooldown(self) -> tuple[datetime | None, datetime | None]:
        with db.connect(self.db_url) as conn:
            rows = conn.execute(
                "SELECT side, last_fire_ts FROM bitunix_score_cooldown"
            ).fetchall()
        last = {"buy": None, "sell": None}
        for r in rows:
            last[r["side"]] = _parse_iso(r["last_fire_ts"])
        return last["buy"], last["sell"]

    def _record_score_fire(self, side: str, fire_ts: str, tier: str) -> None:
        now = _utc_now_iso()
        with db.connect(self.db_url) as conn:
            conn.execute(
                """
                INSERT INTO bitunix_score_cooldown (side, last_fire_ts, last_tier, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(side) DO UPDATE SET
                    last_fire_ts = excluded.last_fire_ts,
                    last_tier    = excluded.last_tier,
                    updated_at   = excluded.updated_at
                """,
                (side, fire_ts, tier, now),
            )

    # ── Phase 3.2 — score path ────────────────────────────────────────

    def _log_score_decision(
        self,
        payload: dict[str, Any],
        verdict_score: Any,
        outcome: str,
        *,
        note: str | None = None,
        order_id: str | None = None,
    ) -> None:
        """Audit every score-path evaluation — fire or skip — so we have
        a paper trail to tune from. New audit kind separates these from
        the Phase 3.1 `bitunix_decided` rows."""
        bd = verdict_score.breakdown
        payload_dict = {
            "strategy": "bitunix_futures",
            "division": "bitunix_futures",
            "trigger_signal": (payload.get("signal") or "").strip().lower(),
            "trigger_source": payload.get("_source"),
            "trigger_price": payload.get("price"),
            "tier": verdict_score.tier.value,
            "side": verdict_score.side.value,
            "net_score": bd.net_score,
            "final_buy_score": bd.final_buy_score,
            "final_sell_score": bd.final_sell_score,
            "raw_buy_score": bd.raw_buy_score,
            "raw_sell_score": bd.raw_sell_score,
            "buy_guard_penalty": bd.buy_guard_penalty,
            "sell_guard_penalty": bd.sell_guard_penalty,
            "buy_contributions": bd.buy_contributions,
            "sell_contributions": bd.sell_contributions,
            "cooldown_blocked": verdict_score.cooldown_blocked,
            "outcome": outcome,
            "note": note,
            "order_id": order_id,
            "reason": verdict_score.reason,
        }
        with db.connect(self.db_url) as conn:
            conn.execute(
                "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?, ?, ?, ?)",
                (
                    _utc_now_iso(),
                    "bitunix_futures",
                    "bitunix_score_decided",
                    json.dumps(payload_dict, default=str),
                ),
            )

    def _log_pa_validation(
        self,
        payload: dict[str, Any],
        verdict_score: Any,
        pa_result: Any,
        *,
        enforced: bool,
    ) -> None:
        """PR 3c — `pa_validation_decision` audit row.

        Always written when the gate runs (shadow + enforce both),
        before any branch decision is taken. `enforced=True` means an
        REJECT actually blocked the trade; `enforced=False` (shadow)
        means the result was logged but the trade proceeded.
        """
        payload_dict = {
            "strategy": "bitunix_futures",
            "division": "bitunix_futures",
            "trigger_signal": (payload.get("signal") or "").strip().lower(),
            "trigger_source": payload.get("_source"),
            "score_side": verdict_score.side.value,
            "score_tier": verdict_score.tier.value,
            "decision": pa_result.decision.value,
            "passed": list(pa_result.passed),
            "failed": list(pa_result.failed),
            "rush_fall_triggered": pa_result.rush_fall_triggered,
            "reason": pa_result.reason,
            "mode": "enforce" if enforced else "shadow",
        }
        try:
            with db.connect(self.db_url) as conn:
                conn.execute(
                    "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?, ?, ?, ?)",
                    (
                        _utc_now_iso(),
                        "bitunix_futures",
                        "pa_validation_decision",
                        json.dumps(payload_dict, default=str),
                    ),
                )
        except Exception as e:
            log.warning("bitunix_observer: pa_validation_decision write failed: %s", e)

    def _log_htf_gate(
        self,
        payload: dict[str, Any],
        verdict_score: Any,
        htf_verdict: Any,
        permission: Any,
        *,
        enforced: bool,
    ) -> None:
        """PR 3c — `htf_gate_decision` audit row.

        Captures the full chain: per-TF classifications + composite
        regime + context fields + permission outcome. Written
        unconditionally when the gate runs so shadow audits have the
        same shape as enforce audits — the only thing that changes is
        the `mode` field and whether placement actually saw the
        size_multiplier applied.

        PR 5f — also captures the most-recent CLOSED bar ts_ms for
        each HTF cache so the audit row can be joined to
        `bitunix_bar_history` (PR 5a) for exact-state replay. None
        when the cache is empty (cold start) or the provider isn't
        attached.
        """
        def _tf_summary(tf_class: Any) -> dict[str, Any]:
            return {
                "regime": tf_class.regime.value,
                "ema_alignment": tf_class.ema_alignment,
                "structure": tf_class.structure,
                "adx": tf_class.adx,
                "macd_hist": tf_class.macd_hist,
            }
        # PR 5f — bar-snapshot pointers for replay joins.
        bar_h1_ts: int | None = None
        bar_h4_ts: int | None = None
        bar_d1_ts: int | None = None
        if self.htf_provider is not None:
            try:
                if self.htf_provider.h1_cache.bars:
                    bar_h1_ts = self.htf_provider.h1_cache.bars[-1].ts_ms
                if self.htf_provider.h4_cache.bars:
                    bar_h4_ts = self.htf_provider.h4_cache.bars[-1].ts_ms
                if self.htf_provider.d1_cache.bars:
                    bar_d1_ts = self.htf_provider.d1_cache.bars[-1].ts_ms
            except Exception as e:
                # Defensive — bar pointers are nice-to-have, never
                # block the audit write.
                log.debug("bitunix_observer: bar_pointer capture failed: %s", e)
        payload_dict = {
            "strategy": "bitunix_futures",
            "division": "bitunix_futures",
            "trigger_signal": (payload.get("signal") or "").strip().lower(),
            "trigger_source": payload.get("_source"),
            "score_side": verdict_score.side.value,
            "score_tier": verdict_score.tier.value,
            "regime": htf_verdict.regime.value,
            "composite_score": round(htf_verdict.score, 3),
            "h1": _tf_summary(htf_verdict.h1),
            "h4": _tf_summary(htf_verdict.h4),
            "d1": _tf_summary(htf_verdict.d1),
            "volatility_tier": htf_verdict.volatility_tier.value,
            "atr_pct_d1": htf_verdict.atr_pct_d1,
            "distance_to_resistance_pct": htf_verdict.distance_to_resistance_pct,
            "distance_to_support_pct": htf_verdict.distance_to_support_pct,
            "session": htf_verdict.session.value,
            "funding_rate": htf_verdict.funding_rate,
            "funding_extreme": htf_verdict.funding_extreme,
            "safe_mode_reason": htf_verdict.safe_mode_reason,
            "size_multiplier": permission.size_multiplier,
            "hard_zero_reason": permission.hard_zero_reason,
            "permission_reason": permission.reason,
            "mode": "enforce" if enforced else "shadow",
            # PR 5f — cheap pointers into bitunix_bar_history (PR 5a).
            "bar_h1_last_close_ms": bar_h1_ts,
            "bar_h4_last_close_ms": bar_h4_ts,
            "bar_d1_last_close_ms": bar_d1_ts,
        }
        try:
            with db.connect(self.db_url) as conn:
                conn.execute(
                    "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?, ?, ?, ?)",
                    (
                        _utc_now_iso(),
                        "bitunix_futures",
                        "htf_gate_decision",
                        json.dumps(payload_dict, default=str),
                    ),
                )
        except Exception as e:
            log.warning("bitunix_observer: htf_gate_decision write failed: %s", e)

    async def _score_and_maybe_propose(
        self,
        payload: dict[str, Any],
        *,
        source: str,
    ) -> None:
        """Score engine flow. Always emits a `bitunix_score_decided`
        audit row. Falls back silently on deps-missing / no-broker /
        sizing-rejection — never raises out."""
        if self.scoring_config is None:
            return

        symbol = payload.get("symbol") or payload.get("ticker") or ""
        if symbol not in ALLOWED_SYMBOLS:
            return

        now = datetime.now(timezone.utc)
        # Stash source on payload for the audit log (mutates the dict
        # but we never propagate it out beyond logging).
        payload = dict(payload)
        payload["_source"] = source

        try:
            ledger = self._read_live_ledger(now)
        except Exception as e:
            log.warning("bitunix_observer: ledger read failed: %s", e)
            return

        live = filter_live_alerts_with_dedupe(ledger, self.scoring_config, now)

        # Price context: signal-only scoring for v1 (no live PA factors).
        # When the LiveBarCache gains VWAP/HH-LL helpers in Phase 3.2.2,
        # populate the full context here.
        entry_price = float(payload.get("price") or 0.0)
        # Phase 3.2.2 — populate the PriceContext from the LiveBarCache.
        # Falls back to a zero-filled context when bars are unavailable,
        # matching Phase 3.2.1 behavior (no PA contributions / no guard
        # penalties).
        ctx: _ScorePriceContext | None = None
        try:
            from trading_corp.data.bitunix_price_context import (
                compute_price_context,
            )
            ctx = compute_price_context(
                self.bar_cache,
                sell_on_rush_window_minutes=(
                    self.scoring_config.sell_on_rush.window_minutes
                ),
                buy_on_fall_window_minutes=(
                    self.scoring_config.buy_on_fall.window_minutes
                ),
            )
        except Exception as e:
            log.warning("bitunix_observer: compute_price_context failed: %s", e)
            ctx = None
        if ctx is None:
            ctx = _ScorePriceContext(
                current_price=entry_price,
                pct_change_in_window_sell=0.0,
                pct_change_in_window_buy=0.0,
            )

        last_buy, last_sell = self._read_cooldown()
        verdict_score = evaluate_confluence_futures(
            live_alerts=live,
            price_ctx=ctx,
            config=self.scoring_config,
            now=now,
            last_fire_ts_buy=last_buy,
            last_fire_ts_sell=last_sell,
        )

        if verdict_score.tier == _ScoreTier.SKIP:
            self._log_score_decision(
                payload, verdict_score,
                "skipped_cooldown" if verdict_score.cooldown_blocked else "skipped_score",
            )
            return

        # ── PR 3c: PA validation gate ───────────────────────────────────
        # Runs when configured AND mode != off. Always writes the audit
        # row (so shadow mode can be analyzed offline). In shadow mode
        # the result does NOT affect placement; in enforce mode a
        # REJECT short-circuits the trade before risk/sizing.
        # PA outcome is binary (PASS/REJECT/DISABLED); no multiplier.
        side_str = "buy" if verdict_score.side == _ScoreSide.BUY else "sell"
        if (
            self.pa_config is not None
            and self.pa_config.enabled
            and self.htf_gate_mode in ("shadow", "enforce")
        ):
            try:
                pa_result = evaluate_pa_validation(
                    side=side_str, price_ctx=ctx, config=self.pa_config,
                )
            except Exception as e:
                log.warning("bitunix_observer: PA validation raised: %s", e)
                pa_result = None
            if pa_result is not None:
                self._log_pa_validation(
                    payload, verdict_score, pa_result,
                    enforced=(self.htf_gate_mode == "enforce"),
                )
                if (
                    self.htf_gate_mode == "enforce"
                    and pa_result.decision == PAValidationDecision.REJECT
                ):
                    self._log_score_decision(
                        payload, verdict_score, "skipped_pa_validation",
                        note=pa_result.reason,
                    )
                    return

        # ── PR 3c: HTF regime gate ──────────────────────────────────────
        # Same audit-then-act pattern. SAFE_MODE / proximity / vol-extreme
        # / funding-extreme can force size_multiplier=0 even when the
        # matrix would otherwise allow a half-size trade.
        htf_size_multiplier = 1.0
        htf_funding_rate_at_decision: float | None = None  # PR 5e
        if (
            self.htf_provider is not None
            and self.htf_config is not None
            and self.htf_gate_mode in ("shadow", "enforce")
        ):
            try:
                htf_verdict = self.htf_provider.regime_snapshot(
                    self.htf_config, current_price=entry_price or None,
                )
                permission = get_trade_permissions(
                    htf_verdict, side_str, self.htf_config,
                )
            except Exception as e:
                log.warning("bitunix_observer: HTF gate raised: %s", e)
                htf_verdict = None
                permission = None
            if htf_verdict is not None and permission is not None:
                self._log_htf_gate(
                    payload, verdict_score, htf_verdict, permission,
                    enforced=(self.htf_gate_mode == "enforce"),
                )
                # PR 5e — capture funding for the eventual proposed_order.
                # Survives even in shadow mode so backtests have it.
                htf_funding_rate_at_decision = htf_verdict.funding_rate
                if self.htf_gate_mode == "enforce":
                    if permission.size_multiplier <= 0.0:
                        self._log_score_decision(
                            payload, verdict_score, "skipped_htf_gate",
                            note=permission.reason,
                        )
                        return
                    htf_size_multiplier = permission.size_multiplier

        # ── deps + broker checks (same gates as Phase 3.1) ──
        if not self.data_exec or not self.risk_agent or not self.logger_agent:
            self._log_score_decision(payload, verdict_score, "skipped_no_deps",
                                     note="risk_agent/data_exec/logger_agent missing")
            return
        broker = self.data_exec.brokers.get("bitunix_futures")
        if broker is None:
            self._log_score_decision(payload, verdict_score, "skipped_no_broker")
            return

        try:
            snap = await broker.snapshot()
            account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
        except Exception as e:
            self._log_score_decision(payload, verdict_score, "error_snapshot",
                                     note=f"broker.snapshot failed: {e}")
            return
        if account_equity <= 0:
            self._log_score_decision(payload, verdict_score, "skipped_no_equity",
                                     note=f"equity={account_equity}")
            return
        if entry_price <= 0:
            self._log_score_decision(payload, verdict_score, "skipped_no_price")
            return

        atr_3m: float | None = None
        if self.bar_cache is not None:
            try:
                atr_3m = self.bar_cache.get_atr(period=14)
            except Exception as e:
                log.warning("bitunix_observer: bar_cache.get_atr failed: %s", e)
                atr_3m = None

        # Reuse the Phase 3.1 sizing math — same TIER_SIZING table, same
        # effective-risk cap, same R:R floor.
        trigger_side_str = "bull" if verdict_score.side == _ScoreSide.BUY else "bear"
        # PR 4 — dispatch to adaptive trade-plan path when both configs
        # are wired. Otherwise stick with the legacy geometric path.
        if self.trade_plan_config is not None and self.fee_config is not None:
            proposal, plan, structural_inputs = self._build_proposal_v2(
                tier=verdict_score.tier.value,
                trigger_side=trigger_side_str,
                trigger_signal=(payload.get("signal") or "").strip().lower(),
                entry_price=entry_price,
                account_equity=account_equity,
                atr_3m=atr_3m,
            )
            self._log_trade_plan_decision(payload, plan, structural_inputs, verdict_score)
            if proposal.proposed_order is None:
                self._log_score_decision(payload, verdict_score, "skipped_trade_plan",
                                         note=plan.skip_reason or proposal.reason)
                return
        else:
            proposal = self._build_proposal(
                tier=verdict_score.tier.value,
                trigger_side=trigger_side_str,
                trigger_signal=(payload.get("signal") or "").strip().lower(),
                entry_price=entry_price,
                account_equity=account_equity,
                atr_3m=atr_3m,
            )
            if proposal.proposed_order is None:
                self._log_score_decision(payload, verdict_score, "skipped_sizing",
                                         note=proposal.reason)
                return

        # ── PR 3c: apply HTF size multiplier (enforce mode only) ──
        # Pullback / bounce sizes get scaled down per the matrix. The
        # daily-risk kill below uses the scaled effective_risk_pct so
        # half-size trades correctly count for half their nominal risk.
        if (
            self.htf_gate_mode == "enforce"
            and htf_size_multiplier != 1.0
            and htf_size_multiplier > 0.0
        ):
            proposal.proposed_order.qty = (
                float(proposal.proposed_order.qty) * htf_size_multiplier
            )
            if proposal.effective_risk_pct is not None:
                proposal.effective_risk_pct = (
                    proposal.effective_risk_pct * htf_size_multiplier
                )
            proposal.proposed_order.extra["htf_size_multiplier"] = (
                htf_size_multiplier
            )

        # ── daily-risk kill ──
        utc_date = _utc_today_iso_date()
        cur_at_risk, _ = self._read_daily_risk(utc_date)
        new_total = cur_at_risk + (proposal.effective_risk_pct or 0.0)
        if new_total > DAILY_RISK_KILL_PCT:
            self._log_score_decision(payload, verdict_score, "skipped_daily_kill",
                                     note=f"would push at_risk to {new_total*100:.3f}%")
            return

        # ── risk gate ──
        order = proposal.proposed_order
        try:
            account = AccountState(account="bitunix_futures",
                                   equity=account_equity, peak_equity=account_equity)
            strat_state = StrategyState(strategy="bitunix_futures")
            risk_verdict = self.risk_agent.evaluate(order, account, strat_state, None, None)
        except Exception as e:
            self._log_score_decision(payload, verdict_score, "error_risk_eval",
                                     note=str(e), order_id=order.id)
            return
        if risk_verdict.verdict == "reject":
            order.status = "risk_rejected"
            order.risk_reason = risk_verdict.reason
            self.logger_agent.log_proposed_order(order)
            self._log_score_decision(payload, verdict_score, "rejected_risk",
                                     note=risk_verdict.reason, order_id=order.id)
            return
        if risk_verdict.verdict == "resize" and risk_verdict.new_qty is not None:
            order.qty = float(risk_verdict.new_qty)

        # ── paper-mode placement ──
        order.status = "would_have_placed"
        # Tag the audit lineage so the Phase 3.1 audit + paper_trade_record
        # rows we already query don't have to learn a new format.
        order.rationale = f"[score] {order.rationale}"
        order.extra["score_path"] = True
        order.extra["net_score"] = verdict_score.breakdown.net_score
        # PR 5e — stash funding rate at decision time so backtests can
        # reconstruct without joining audit events. None when HTF gate
        # didn't run (gate_mode='off' or provider missing).
        if htf_funding_rate_at_decision is not None:
            order.extra["funding_rate_at_decision"] = htf_funding_rate_at_decision
        self.logger_agent.log_proposed_order(order)
        self.logger_agent.log_event(
            actor="bitunix_futures",
            kind="would_have_placed",
            payload={
                "strategy": "bitunix_futures",
                "division": "bitunix_futures",
                "order_id": order.id,
                "tier": verdict_score.tier.value,
                "trigger_signal": (payload.get("signal") or "").strip().lower(),
                "side": order.side,
                "qty": order.qty,
                "entry_price": entry_price,
                "stop_price": proposal.stop_price,
                "tp_price": proposal.tp_price,
                "leverage": proposal.leverage,
                "effective_risk_pct": proposal.effective_risk_pct,
                "rr_ratio": proposal.rr_ratio,
                "rationale": order.rationale,
                "via": "bitunix_score",
                "net_score": verdict_score.breakdown.net_score,
            },
        )
        try:
            record = PaperTradeRecord.from_order(
                order, strategy="bitunix_futures", division="bitunix_futures",
                max_hold_seconds=self.max_hold_seconds,
            )
            db.insert_paper_trade_record(record.to_db_row(), db_url=self.db_url)
        except Exception as e:
            log.warning("bitunix_observer: paper_trade_record write failed: %s", e)

        self._record_daily_risk(utc_date, proposal.effective_risk_pct or 0.0)
        self._record_score_fire(order.side, now.isoformat(), verdict_score.tier.value)
        self._log_score_decision(payload, verdict_score, "placed",
                                 note=order.rationale, order_id=order.id)

        if self.telegram_channel is not None:
            try:
                msg = (
                    f"BTC-PERP {verdict_score.tier.value} "
                    f"{'LONG' if order.side == 'buy' else 'SHORT'} (paper, score)\n"
                    f"net_score: {verdict_score.breakdown.net_score} "
                    f"(buy={verdict_score.breakdown.final_buy_score}, "
                    f"sell={verdict_score.breakdown.final_sell_score})\n"
                    f"entry: ${entry_price:,.2f}  qty: {order.qty:.4f}\n"
                    f"stop: ${proposal.stop_price:,.2f}  tp: ${proposal.tp_price:,.2f}\n"
                    f"size: {(proposal.target_size_pct or 0)*100:.2f}%  "
                    f"lev: {proposal.leverage:g}x  "
                    f"eff_risk: {(proposal.effective_risk_pct or 0)*100:.3f}%"
                )
                await self.telegram_channel.push(msg)
            except Exception as e:
                log.warning("bitunix_observer: telegram push failed: %s", e)

    # ── core classification flow ──────────────────────────────────────

    def _observe_alert_inner(
        self,
        payload: dict[str, Any],
        *,
        source: str,
    ) -> TierVerdict | None:
        symbol = payload.get("symbol") or payload.get("ticker") or ""
        if symbol not in ALLOWED_SYMBOLS:
            return None

        signal = (payload.get("signal") or "").strip()
        if not signal:
            return None

        tf = _interval_to_tf(payload.get("interval"))
        ts = (payload.get("time") or _utc_now_iso())

        # ── Cypher bias-setters ──────────────────────────────────────
        if source == "market_cypher":
            bias_side = _signal_to_bias_side(signal)
            if bias_side and tf in ("4h", "1d"):
                self._update_bias(tf, bias_side, ts, signal)
            return None

        # ── Otter CVD-flip volume-axis input ─────────────────────────
        if source == "lord_otter":
            if signal == CVD_FLIP_BULL and tf == "3m":
                self._update_cvd("bull", ts)
                return None
            if signal == CVD_FLIP_BEAR and tf == "3m":
                self._update_cvd("bear", ts)
                return None

            # ── Otter triggers ───────────────────────────────────────
            trigger_side = _signal_to_trigger_side(signal)
            if not trigger_side:
                return None
            if tf != "3m":
                log.info(
                    "bitunix_observer: otter %r on tf=%s (expected 3m); skipping",
                    signal, tf,
                )
                return None
            verdict = self._classify_trigger(signal, trigger_side, tf, ts)
            self._log_classification(verdict, payload)
            return verdict

        return None

    # ── bias state ────────────────────────────────────────────────────

    def _update_bias(self, timeframe: str, side: str, setter_ts: str, signal: str) -> None:
        now = _utc_now_iso()
        with db.connect(self.db_url) as conn:
            conn.execute(
                """
                INSERT INTO bitunix_observer_bias
                  (timeframe, side, last_setter_ts, last_signal, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(timeframe, side) DO UPDATE SET
                    last_setter_ts = excluded.last_setter_ts,
                    last_signal    = excluded.last_signal,
                    updated_at     = excluded.updated_at
                  WHERE excluded.last_setter_ts > bitunix_observer_bias.last_setter_ts
                """,
                (timeframe, side, setter_ts, signal, now),
            )

    def _read_bias(self, timeframe: str, query_ts_iso: str) -> BiasSnapshot:
        decay = DECAY_4H_SECONDS if timeframe == "4h" else DECAY_1D_SECONDS
        query_dt = _parse_iso(query_ts_iso) or datetime.now(timezone.utc)

        with db.connect(self.db_url) as conn:
            rows = conn.execute(
                "SELECT side, last_setter_ts FROM bitunix_observer_bias WHERE timeframe = ?",
                (timeframe,),
            ).fetchall()

        latest = {"bull": None, "bear": None}
        for row in rows:
            latest[row["side"]] = row["last_setter_ts"]

        bull_ts = _parse_iso(latest["bull"])
        bear_ts = _parse_iso(latest["bear"])

        bull_active = bull_ts is not None and (query_dt - bull_ts).total_seconds() <= decay
        bear_active = bear_ts is not None and (query_dt - bear_ts).total_seconds() <= decay

        if bull_active and bear_active:
            if (bull_ts or datetime.min.replace(tzinfo=timezone.utc)) > (
                bear_ts or datetime.min.replace(tzinfo=timezone.utc)
            ):
                side, last_ts = "bull", latest["bull"]
            else:
                side, last_ts = "bear", latest["bear"]
        elif bull_active:
            side, last_ts = "bull", latest["bull"]
        elif bear_active:
            side, last_ts = "bear", latest["bear"]
        else:
            side, last_ts = "neutral", None

        age = int((query_dt - _parse_iso(last_ts)).total_seconds()) if last_ts else None
        return BiasSnapshot(timeframe=timeframe, side=side, last_setter_ts=last_ts, age_seconds=age)

    # ── CVD state (volume axis) ───────────────────────────────────────

    def _update_cvd(self, side: str, flip_ts: str) -> None:
        now = _utc_now_iso()
        with db.connect(self.db_url) as conn:
            conn.execute(
                """
                INSERT INTO bitunix_observer_cvd (side, last_flip_ts, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(side) DO UPDATE SET
                    last_flip_ts = excluded.last_flip_ts,
                    updated_at   = excluded.updated_at
                  WHERE excluded.last_flip_ts > bitunix_observer_cvd.last_flip_ts
                """,
                (side, flip_ts, now),
            )

    def _read_cvd(self, query_ts_iso: str) -> CVDSnapshot:
        query_dt = _parse_iso(query_ts_iso) or datetime.now(timezone.utc)
        with db.connect(self.db_url) as conn:
            rows = conn.execute(
                "SELECT side, last_flip_ts FROM bitunix_observer_cvd"
            ).fetchall()
        latest = {"bull": None, "bear": None}
        for row in rows:
            latest[row["side"]] = row["last_flip_ts"]

        bull_ts = _parse_iso(latest["bull"])
        bear_ts = _parse_iso(latest["bear"])
        bull_active = bull_ts is not None and (query_dt - bull_ts).total_seconds() <= CVD_DECAY_SECONDS
        bear_active = bear_ts is not None and (query_dt - bear_ts).total_seconds() <= CVD_DECAY_SECONDS

        if bull_active and bear_active:
            if (bull_ts or datetime.min.replace(tzinfo=timezone.utc)) > (
                bear_ts or datetime.min.replace(tzinfo=timezone.utc)
            ):
                side, last_ts = "bull", latest["bull"]
            else:
                side, last_ts = "bear", latest["bear"]
        elif bull_active:
            side, last_ts = "bull", latest["bull"]
        elif bear_active:
            side, last_ts = "bear", latest["bear"]
        else:
            side, last_ts = "neutral", None

        age = int((query_dt - _parse_iso(last_ts)).total_seconds()) if last_ts else None
        return CVDSnapshot(side=side, last_flip_ts=last_ts, age_seconds=age)

    # ── classifier ────────────────────────────────────────────────────

    def _classify_trigger(
        self,
        trigger_signal: str,
        trigger_side: str,
        trigger_tf: str,
        trigger_ts: str,
    ) -> TierVerdict:
        bias_4h = self._read_bias("4h", trigger_ts)
        bias_1d = self._read_bias("1d", trigger_ts)
        cvd = self._read_cvd(trigger_ts)
        tier = self._tier_for(trigger_side, bias_4h.side, bias_1d.side, cvd.side, self.counter_enabled)
        rationale = self._rationale_for(trigger_side, bias_4h.side, bias_1d.side, cvd.side, tier)
        return TierVerdict(
            tier=tier,
            trigger_signal=trigger_signal,
            trigger_side=trigger_side,
            trigger_tf=trigger_tf,
            trigger_ts=trigger_ts,
            bias_4h=bias_4h,
            bias_1d=bias_1d,
            cvd=cvd,
            rationale=rationale,
        )

    @staticmethod
    def _tier_for(
        trigger_side: str,
        bias_4h: str,
        bias_1d: str,
        cvd_side: str,
        counter_enabled: bool = False,
    ) -> str:
        """Phase 3.1 full ladder. See memory for the tier table.

        - PREMIUM:  CVD agrees + 4h agrees + 1D agrees
        - STANDARD: CVD agrees + 4h agrees + 1D neutral
        - WEAK:     CVD doesn't agree + 4h agrees + 1D agrees
        - COUNTER:  CVD agrees + HTF contradicts (default OFF)
        - SKIP:     anything else
        """
        confluent = (cvd_side == trigger_side)
        agree_4h = bias_4h == trigger_side
        agree_1d = bias_1d == trigger_side
        contra_4h = bias_4h not in ("neutral", trigger_side)
        contra_1d = bias_1d not in ("neutral", trigger_side)

        # HTF contradicts → either COUNTER (if enabled + confluent) or SKIP
        if contra_4h or contra_1d:
            if confluent and counter_enabled:
                return "COUNTER"
            return "SKIP"

        if confluent and agree_4h and agree_1d:
            return "PREMIUM"
        if confluent and agree_4h and bias_1d == "neutral":
            return "STANDARD"
        if not confluent and agree_4h and agree_1d:
            return "WEAK"
        return "SKIP"

    @staticmethod
    def _rationale_for(
        trigger_side: str, bias_4h: str, bias_1d: str, cvd_side: str, tier: str,
    ) -> str:
        return (
            f"trigger={trigger_side}, cvd={cvd_side}, "
            f"bias_4h={bias_4h}, bias_1d={bias_1d} -> {tier}"
        )

    # ── classification audit (Phase 3.0 + ongoing) ────────────────────

    def _log_classification(self, verdict: TierVerdict, original_payload: dict) -> None:
        payload_dict = {
            "strategy": "bitunix_futures",
            "division": "bitunix_futures",
            "trigger_signal": verdict.trigger_signal,
            "trigger_side": verdict.trigger_side,
            "trigger_tf": verdict.trigger_tf,
            "trigger_ts": verdict.trigger_ts,
            "trigger_price": original_payload.get("price"),
            "tier": verdict.tier,
            "bias_4h_side": verdict.bias_4h.side,
            "bias_4h_setter_ts": verdict.bias_4h.last_setter_ts,
            "bias_4h_age_sec": verdict.bias_4h.age_seconds,
            "bias_1d_side": verdict.bias_1d.side,
            "bias_1d_setter_ts": verdict.bias_1d.last_setter_ts,
            "bias_1d_age_sec": verdict.bias_1d.age_seconds,
            "cvd_side": verdict.cvd.side,
            "cvd_flip_ts": verdict.cvd.last_flip_ts,
            "cvd_age_sec": verdict.cvd.age_seconds,
            "rationale": verdict.rationale,
        }
        with db.connect(self.db_url) as conn:
            conn.execute(
                "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?, ?, ?, ?)",
                (
                    _utc_now_iso(),
                    "bitunix_futures",
                    "bitunix_observer_classified",
                    json.dumps(payload_dict, default=str),
                ),
            )

    # ── decision audit (every signal logs ONE of these) ──────────────

    def _log_decision(
        self,
        verdict: TierVerdict,
        original_payload: dict,
        outcome: str,                 # placed | skipped_tier | skipped_daily_kill |
                                      # skipped_no_deps | skipped_no_broker | skipped_sizing |
                                      # rejected_risk | error_*
        *,
        note: str | None = None,
        order_id: str | None = None,
        proposal_meta: dict | None = None,
    ) -> None:
        payload_dict = {
            "strategy": "bitunix_futures",
            "division": "bitunix_futures",
            "trigger_signal": verdict.trigger_signal,
            "trigger_side": verdict.trigger_side,
            "trigger_price": original_payload.get("price"),
            "tier": verdict.tier,
            "outcome": outcome,
            "note": note,
            "order_id": order_id,
            "proposal": proposal_meta,
        }
        with db.connect(self.db_url) as conn:
            conn.execute(
                "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?, ?, ?, ?)",
                (
                    _utc_now_iso(),
                    "bitunix_futures",
                    "bitunix_decided",
                    json.dumps(payload_dict, default=str),
                ),
            )

    # ── daily-risk kill (board-approved cap) ─────────────────────────

    def _read_daily_risk(self, utc_date: str) -> tuple[float, int]:
        """Returns (cumulative_at_risk_pct, orders_count) for the date."""
        with db.connect(self.db_url) as conn:
            row = conn.execute(
                "SELECT cumulative_at_risk_pct, orders_count "
                "FROM bitunix_observer_daily_risk WHERE utc_date = ?",
                (utc_date,),
            ).fetchone()
        if row is None:
            return (0.0, 0)
        return (float(row["cumulative_at_risk_pct"]), int(row["orders_count"]))

    def _record_daily_risk(self, utc_date: str, additional_risk_pct: float) -> None:
        now = _utc_now_iso()
        with db.connect(self.db_url) as conn:
            conn.execute(
                """
                INSERT INTO bitunix_observer_daily_risk
                  (utc_date, cumulative_at_risk_pct, orders_count, updated_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(utc_date) DO UPDATE SET
                    cumulative_at_risk_pct = cumulative_at_risk_pct + excluded.cumulative_at_risk_pct,
                    orders_count           = orders_count + 1,
                    updated_at             = excluded.updated_at
                """,
                (utc_date, additional_risk_pct, now),
            )

    # ── order proposer ────────────────────────────────────────────────

    @staticmethod
    def _build_proposal(
        *,
        tier: str,
        trigger_side: str,
        trigger_signal: str,
        entry_price: float,
        account_equity: float,
        atr_3m: float | None = None,
    ) -> OrderProposal:
        """Pure function: tier + entry + equity → ProposedOrder | None.

        Applies structural stop, R:R gate, effective-risk cap downsizing,
        multi-leg-ready tp_plan. Returns OrderProposal with `proposed_order=None`
        when math rejects the trade.

        `atr_3m` is the Average True Range from the live 3m bar cache.
        When None, falls back to ATR_FALLBACK_PCT × entry_price (Phase 3.0
        placeholder; floor still wins). When supplied, real ATR drives stop
        sizing — wider stops on volatile bars, tighter on quiet ones.
        """
        if tier not in TIER_SIZING:
            return OrderProposal(proposed_order=None, reason=f"tier {tier!r} not sized")
        if entry_price <= 0:
            return OrderProposal(proposed_order=None, reason="entry_price <= 0")
        if account_equity <= 0:
            return OrderProposal(proposed_order=None, reason="account_equity <= 0")

        cfg = TIER_SIZING[tier]
        target_size_pct = float(cfg["size_pct"])
        leverage = float(cfg["leverage"])

        # Stop distance: max(1.5 × ATR_3m, 0.3% × price)
        atr_used = atr_3m if (atr_3m is not None and atr_3m > 0) else (entry_price * ATR_FALLBACK_PCT)
        atr_source = "live_atr_14" if (atr_3m is not None and atr_3m > 0) else "estimate_0.04pct"
        stop_distance = max(ATR_MULTIPLIER * atr_used, STOP_FLOOR_PCT * entry_price)
        stop_distance_pct = stop_distance / entry_price

        # Take-profit at default R; R:R gate.
        tp_distance = DEFAULT_TP_R * stop_distance
        rr = tp_distance / stop_distance if stop_distance > 0 else 0
        if rr < MIN_RR_RATIO:
            return OrderProposal(
                proposed_order=None,
                reason=f"R:R {rr:.2f} < {MIN_RR_RATIO}",
                stop_distance_pct=stop_distance_pct,
                rr_ratio=rr,
            )

        # Effective-risk cap: downsize position_pct so that
        # position_pct × leverage × stop_distance_pct ≤ EFFECTIVE_RISK_PER_TRADE_PCT.
        denominator = leverage * stop_distance_pct
        if denominator <= 0:
            return OrderProposal(proposed_order=None, reason="zero leverage*stop")
        max_pct_for_risk_cap = EFFECTIVE_RISK_PER_TRADE_PCT / denominator
        actual_size_pct = min(target_size_pct, max_pct_for_risk_cap)
        actual_effective_risk = actual_size_pct * leverage * stop_distance_pct

        notional = account_equity * actual_size_pct * leverage
        qty = notional / entry_price
        if qty <= 0:
            return OrderProposal(proposed_order=None, reason="qty rounded to 0")

        if trigger_side == "bull":
            order_side = "buy"
            stop_price = entry_price - stop_distance
            tp_price = entry_price + tp_distance
        else:
            order_side = "sell"
            stop_price = entry_price + stop_distance
            tp_price = entry_price - tp_distance

        # Multi-leg-ready tp_plan. Phase 3.1: single full-size TP.
        # Phase 3.2 will populate this list with the scale-out plan, e.g.
        #   [{"fraction": 0.25, "target_r": 0.5, "stop_action": "move_to_breakeven"},
        #    {"fraction": 0.50, "target_r": 2.0, "stop_action": "noop"},
        #    {"fraction": 0.25, "target_r": "trail", "stop_action": "trail_atr_2"}]
        tp_plan = [
            {"fraction": 1.0, "target_r": DEFAULT_TP_R, "stop_action": "noop"},
        ]

        order = ProposedOrder(
            strategy="bitunix_futures",
            symbol=TRADE_SYMBOL,
            side=order_side,
            qty=qty,
            order_type="market",
            rationale=(
                f"tier={tier}, trigger={trigger_signal}, "
                f"size={actual_size_pct*100:.3f}% @ {leverage:g}x, "
                f"stop={stop_distance_pct*100:.3f}% (eff_risk={actual_effective_risk*100:.3f}%), "
                f"tp={DEFAULT_TP_R}R, rr={rr:.1f}"
            ),
            extra={
                "tier": tier,
                "trigger_signal": trigger_signal,
                "trigger_side": trigger_side,
                "leverage": leverage,
                "size_pct_equity": actual_size_pct,
                "size_pct_target": target_size_pct,
                "effective_risk_pct": actual_effective_risk,
                "stop_distance_pct": stop_distance_pct,
                "tp_plan": tp_plan,
                "atr_source": atr_source,
                "rr_ratio": rr,
                # Keys harmonized with PaperTradeRecord.from_order so we can
                # write the row directly without a custom adapter:
                "entry_reference_price": entry_price,
                "stop_price": stop_price,
                "take_profit_price": tp_price,
                "tp_r_multiple": DEFAULT_TP_R,
                "source_signal": trigger_signal,
                "max_dollar_risk": account_equity * actual_effective_risk,
                "expected_gain_if_tp_hit": account_equity * actual_effective_risk * DEFAULT_TP_R,
            },
        )
        return OrderProposal(
            proposed_order=order,
            reason="ok",
            effective_risk_pct=actual_effective_risk,
            target_size_pct=target_size_pct,
            leverage=leverage,
            stop_distance_pct=stop_distance_pct,
            stop_price=stop_price,
            tp_price=tp_price,
            rr_ratio=rr,
        )

    # ── PR 4 — adaptive trade plan (MVP + Option C) ───────────────────

    def _build_proposal_v2(
        self,
        *,
        tier: str,
        trigger_side: str,
        trigger_signal: str,
        entry_price: float,
        account_equity: float,
        atr_3m: float | None = None,
    ) -> tuple[OrderProposal, TradePlan, dict[str, Any]]:
        """PR 4 adaptive-trade-plan path. Composes swing.py + levels.py
        + trade_plan.build_trade_plan to produce a TradePlan, then sizes
        per existing tier × effective-risk-cap math.

        Returns (OrderProposal, TradePlan, structural_inputs). The plan
        and inputs are populated even on skips so we can write a full
        audit row regardless of outcome.
        """
        assert self.trade_plan_config is not None and self.fee_config is not None, \
            "_build_proposal_v2 requires both trade_plan_config and fee_config"
        cfg = self.trade_plan_config
        fees = self.fee_config

        # Pre-flight guards (parity with legacy _build_proposal).
        if tier not in TIER_SIZING:
            plan = _make_skip_plan(entry_price, "tier_not_sized")
            return OrderProposal(proposed_order=None, reason=plan.skip_reason), plan, {}
        if entry_price <= 0:
            plan = _make_skip_plan(entry_price, "entry_price_le_0")
            return OrderProposal(proposed_order=None, reason=plan.skip_reason), plan, {}
        if account_equity <= 0:
            plan = _make_skip_plan(entry_price, "account_equity_le_0")
            return OrderProposal(proposed_order=None, reason=plan.skip_reason), plan, {}

        # Structural inputs from the 3m bar cache. Empty cache → all None;
        # build_trade_plan falls back to ATR-only SL + default 1R TP2.
        swing_low: float | None = None
        swing_high: float | None = None
        resistance: float | None = None
        support: float | None = None
        bars = list(self.bar_cache.bars) if self.bar_cache and self.bar_cache.bars else []
        current_idx = len(bars) - 1
        if current_idx >= 0:
            try:
                swing_low = get_recent_swing(
                    bars, current_idx, side="low",
                    n=cfg.swing_n, max_lookback=cfg.swing_max_lookback,
                )
                swing_high = get_recent_swing(
                    bars, current_idx, side="high",
                    n=cfg.swing_n, max_lookback=cfg.swing_max_lookback,
                )
                resistance, support = get_htf_levels(
                    bars, current_idx,
                    htf_minutes=cfg.htf_minutes,
                    lookback_bars_htf=cfg.htf_lookback_bars,
                    n=cfg.swing_n,
                )
            except Exception as e:
                log.warning("bitunix_observer: structural-input compute failed: %s", e)

        atr_used = atr_3m if (atr_3m is not None and atr_3m > 0) else (entry_price * ATR_FALLBACK_PCT)
        atr_source = "live_atr_14" if (atr_3m is not None and atr_3m > 0) else "estimate_0.04pct"

        side = "buy" if trigger_side == "bull" else "sell"
        plan = build_trade_plan(
            entry=entry_price, side=side, atr=atr_used,
            swing_low=swing_low, swing_high=swing_high,
            resistance=resistance, support=support,
            cfg=cfg, fees=fees,
        )

        structural_inputs: dict[str, Any] = {
            "swing_low": swing_low,
            "swing_high": swing_high,
            "resistance": resistance,
            "support": support,
            "atr_used": atr_used,
            "atr_source": atr_source,
        }

        if not plan.should_trade:
            return (
                OrderProposal(
                    proposed_order=None,
                    reason=plan.skip_reason or "trade_plan_skip",
                    stop_distance_pct=(plan.risk_per_unit / entry_price) if entry_price > 0 else None,
                ),
                plan,
                structural_inputs,
            )

        # Sizing — same effective-risk cap math as legacy _build_proposal.
        tier_cfg = TIER_SIZING[tier]
        target_size_pct = float(tier_cfg["size_pct"])
        leverage = float(tier_cfg["leverage"])
        stop_distance_pct = plan.risk_per_unit / entry_price
        denominator = leverage * stop_distance_pct
        if denominator <= 0:
            return (
                OrderProposal(proposed_order=None, reason="zero leverage*stop"),
                plan, structural_inputs,
            )
        max_pct_for_risk_cap = EFFECTIVE_RISK_PER_TRADE_PCT / denominator
        actual_size_pct = min(target_size_pct, max_pct_for_risk_cap)
        actual_effective_risk = actual_size_pct * leverage * stop_distance_pct
        notional = account_equity * actual_size_pct * leverage
        qty = notional / entry_price
        if qty <= 0:
            return (
                OrderProposal(proposed_order=None, reason="qty rounded to 0"),
                plan, structural_inputs,
            )

        order_side = side  # "buy" or "sell"

        # 3-leg tp_plan with prices + per-leg stop_action. Read by the
        # PR 5 reconciler to drive the SL lifecycle (BE → TP1 → trail).
        def _leg_r(price: float) -> float:
            return abs(price - plan.entry) / plan.risk_per_unit if plan.risk_per_unit > 0 else 0.0

        tp_plan_payload = [
            {"leg": "tp1", "fraction": plan.tp1_qty_fraction, "target_r": round(_leg_r(plan.tp1), 3),
             "price": plan.tp1, "stop_action": "move_to_breakeven"},
            {"leg": "tp2", "fraction": plan.tp2_qty_fraction, "target_r": round(_leg_r(plan.tp2), 3),
             "price": plan.tp2, "stop_action": "move_to_tp1"},
            {"leg": "tp3", "fraction": plan.tp3_qty_fraction, "target_r": round(_leg_r(plan.tp3), 3),
             "price": plan.tp3, "stop_action": "trail_atr"},
        ]

        order = ProposedOrder(
            strategy="bitunix_futures",
            symbol=TRADE_SYMBOL,
            side=order_side,
            qty=qty,
            order_type="market",
            rationale=(
                f"tier={tier}, trigger={trigger_signal}, "
                f"sl={plan.sl_method}, tp2={plan.tp2_method}, "
                f"size={actual_size_pct*100:.3f}% @ {leverage:g}x, "
                f"stop={stop_distance_pct*100:.3f}% (eff_risk={actual_effective_risk*100:.3f}%)"
            ),
            extra={
                "tier": tier,
                "trigger_signal": trigger_signal,
                "trigger_side": trigger_side,
                "leverage": leverage,
                "size_pct_equity": actual_size_pct,
                "size_pct_target": target_size_pct,
                "effective_risk_pct": actual_effective_risk,
                "stop_distance_pct": stop_distance_pct,
                "tp_plan": tp_plan_payload,
                "tp_plan_version": "v2",
                "sl_method": plan.sl_method,
                "tp2_method": plan.tp2_method,
                "atr_source": atr_source,
                "entry_reference_price": entry_price,
                "stop_price": plan.stop_loss,
                "tp1_price": plan.tp1,
                "tp2_price": plan.tp2,
                "tp3_price": plan.tp3,
                "take_profit_price": plan.tp3,  # back-compat with paper_trade_record reader
                "source_signal": trigger_signal,
                "max_dollar_risk": account_equity * actual_effective_risk,
            },
        )

        return (
            OrderProposal(
                proposed_order=order,
                reason="ok",
                effective_risk_pct=actual_effective_risk,
                target_size_pct=target_size_pct,
                leverage=leverage,
                stop_distance_pct=stop_distance_pct,
                stop_price=plan.stop_loss,
                tp_price=plan.tp3,
            ),
            plan,
            structural_inputs,
        )

    def _log_trade_plan_decision(
        self,
        payload: dict[str, Any],
        plan: TradePlan,
        structural_inputs: dict[str, Any],
        verdict_score: Any,
    ) -> None:
        """PR 4 — `trade_plan_decision` audit row. Written for every
        v2-path eval (skip or not) so post-deploy review can reconstruct
        WHY a trade fired or got rejected.
        """
        try:
            payload_dict = {
                "strategy": "bitunix_futures",
                "division": "bitunix_futures",
                "trigger_signal": (payload.get("signal") or "").strip().lower(),
                "trigger_source": payload.get("_source"),
                "score_side": verdict_score.side.value if hasattr(verdict_score, "side") else None,
                "score_tier": verdict_score.tier.value if hasattr(verdict_score, "tier") else None,
                "should_trade": plan.should_trade,
                "skip_reason": plan.skip_reason,
                "entry": plan.entry,
                "stop_loss": plan.stop_loss if plan.should_trade else None,
                "tp1": plan.tp1 if plan.should_trade else None,
                "tp2": plan.tp2 if plan.should_trade else None,
                "tp3": plan.tp3 if plan.should_trade else None,
                "sl_method": plan.sl_method,
                "tp2_method": plan.tp2_method,
                "risk_per_unit": plan.risk_per_unit,
                "tp1_qty_fraction": plan.tp1_qty_fraction,
                "tp2_qty_fraction": plan.tp2_qty_fraction,
                "tp3_qty_fraction": plan.tp3_qty_fraction,
                "inputs": structural_inputs,
            }
            with db.connect(self.db_url) as conn:
                conn.execute(
                    "INSERT INTO audit_event (ts, actor, kind, payload_json) VALUES (?, ?, ?, ?)",
                    (
                        _utc_now_iso(),
                        "bitunix_futures",
                        "trade_plan_decision",
                        json.dumps(payload_dict, default=str),
                    ),
                )
        except Exception as e:
            log.warning("bitunix_observer: trade_plan_decision audit failed: %s", e)

    # ── async order flow: classify → propose → risk → place → notify ─

    async def _maybe_propose(
        self,
        verdict: TierVerdict,
        original_payload: dict,
    ) -> None:
        """Order proposer flow. Always logs a `bitunix_decided` audit row."""

        # ── Tier filters ─────────────────────────────────────────────
        if verdict.tier == "SKIP":
            self._log_decision(verdict, original_payload, "skipped_tier",
                               note="tier=SKIP per classifier")
            return
        if verdict.tier == "COUNTER" and not self.counter_enabled:
            self._log_decision(verdict, original_payload, "skipped_tier",
                               note="COUNTER tier disabled (counter_enabled=False)")
            return

        # ── Deps required for live order flow ────────────────────────
        if not self.data_exec or not self.risk_agent or not self.logger_agent:
            self._log_decision(verdict, original_payload, "skipped_no_deps",
                               note="risk_agent/data_exec/logger_agent not wired")
            return

        broker = self.data_exec.brokers.get("bitunix_futures")
        if broker is None:
            self._log_decision(verdict, original_payload, "skipped_no_broker",
                               note="no broker registered for bitunix_futures")
            return

        # ── Account equity from broker snapshot ──────────────────────
        try:
            snap = await broker.snapshot()
            account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
        except Exception as e:
            self._log_decision(verdict, original_payload, "error_snapshot",
                               note=f"broker.snapshot failed: {e}")
            return

        if account_equity <= 0:
            self._log_decision(verdict, original_payload, "skipped_no_equity",
                               note=f"snapshot equity={account_equity}")
            return

        entry_price = float(original_payload.get("price") or 0.0)

        # Live ATR(14) from the bar cache when available; falls back to the
        # 0.04%-of-price estimate inside _build_proposal when None.
        atr_3m: float | None = None
        if self.bar_cache is not None:
            try:
                atr_3m = self.bar_cache.get_atr(period=14)
            except Exception as e:
                log.warning("bitunix_observer: bar_cache.get_atr failed: %s", e)
                atr_3m = None

        proposal = self._build_proposal(
            tier=verdict.tier,
            trigger_side=verdict.trigger_side,
            trigger_signal=verdict.trigger_signal,
            entry_price=entry_price,
            account_equity=account_equity,
            atr_3m=atr_3m,
        )
        if proposal.proposed_order is None:
            self._log_decision(verdict, original_payload, "skipped_sizing",
                               note=proposal.reason,
                               proposal_meta={
                                   "rr_ratio": proposal.rr_ratio,
                                   "stop_distance_pct": proposal.stop_distance_pct,
                               })
            return

        # ── Daily-risk kill ──────────────────────────────────────────
        utc_date = _utc_today_iso_date()
        cur_at_risk, orders_today = self._read_daily_risk(utc_date)
        new_total = cur_at_risk + (proposal.effective_risk_pct or 0.0)
        if new_total > DAILY_RISK_KILL_PCT:
            self._log_decision(verdict, original_payload, "skipped_daily_kill",
                               note=f"would push at_risk to {new_total*100:.3f}% "
                                    f"(cap {DAILY_RISK_KILL_PCT*100:.1f}%); "
                                    f"already {orders_today} orders today")
            return

        # ── Risk gate (single chokepoint per CLAUDE.md) ──────────────
        order = proposal.proposed_order
        try:
            account = AccountState(account="bitunix_futures", equity=account_equity, peak_equity=account_equity)
            strat_state = StrategyState(strategy="bitunix_futures")
            verdict_risk = self.risk_agent.evaluate(order, account, strat_state, None, None)
        except Exception as e:
            self._log_decision(verdict, original_payload, "error_risk_eval",
                               note=f"risk_agent.evaluate failed: {e}",
                               order_id=order.id)
            return

        if verdict_risk.verdict == "reject":
            order.status = "risk_rejected"
            order.risk_reason = verdict_risk.reason
            self.logger_agent.log_proposed_order(order)
            self._log_decision(verdict, original_payload, "rejected_risk",
                               note=verdict_risk.reason, order_id=order.id)
            return

        if verdict_risk.verdict == "resize" and verdict_risk.new_qty is not None:
            order.qty = float(verdict_risk.new_qty)

        # ── Place (paper-mode auto-execute via data_exec) ────────────
        # No HITL approval gate per board direction (memory
        # `trading_corp_bitunix_phase3_confluence_model`). Risk caps are
        # the gate, not per-trade approval.
        order.status = "would_have_placed"
        self.logger_agent.log_proposed_order(order)
        self.logger_agent.log_event(
            actor="bitunix_futures",
            kind="would_have_placed",
            payload={
                "strategy": "bitunix_futures",
                "division": "bitunix_futures",
                "order_id": order.id,
                "tier": verdict.tier,
                "trigger_signal": verdict.trigger_signal,
                "side": order.side,
                "qty": order.qty,
                "entry_price": entry_price,
                "stop_price": proposal.stop_price,
                "tp_price": proposal.tp_price,
                "leverage": proposal.leverage,
                "effective_risk_pct": proposal.effective_risk_pct,
                "rr_ratio": proposal.rr_ratio,
                "rationale": order.rationale,
            },
        )
        # Phase 3.2a: write paper_trade_record so the existing
        # paper_trade_replay loop resolves bitunix paper trades to
        # win/loss outcomes (it walks `paper_trade_record WHERE result IS NULL`
        # — strategy-agnostic). Failures here are logged but don't abort
        # placement; the audit_event remains the source of truth.
        try:
            record = PaperTradeRecord.from_order(
                order,
                strategy="bitunix_futures",
                division="bitunix_futures",
                max_hold_seconds=self.max_hold_seconds,
            )
            db.insert_paper_trade_record(record.to_db_row(), db_url=self.db_url)
        except Exception as e:
            log.warning("bitunix_observer: paper_trade_record write failed: %s", e)

        self._record_daily_risk(utc_date, proposal.effective_risk_pct or 0.0)
        self._log_decision(verdict, original_payload, "placed",
                           note=order.rationale,
                           order_id=order.id,
                           proposal_meta={
                               "tier": verdict.tier,
                               "qty": order.qty,
                               "entry_price": entry_price,
                               "stop_price": proposal.stop_price,
                               "tp_price": proposal.tp_price,
                               "leverage": proposal.leverage,
                               "effective_risk_pct": proposal.effective_risk_pct,
                               "daily_at_risk_after": new_total,
                           })

        # ── Telegram notify ──────────────────────────────────────────
        if self.telegram_channel is not None:
            try:
                msg = self._format_telegram_message(verdict, order, proposal, entry_price)
                await self.telegram_channel.push(msg)
            except Exception as e:
                log.warning("bitunix_observer: telegram push failed: %s", e)

    @staticmethod
    def _format_telegram_message(
        verdict: TierVerdict,
        order: ProposedOrder,
        proposal: OrderProposal,
        entry_price: float,
    ) -> str:
        side_arrow = "LONG" if verdict.trigger_side == "bull" else "SHORT"
        return (
            f"BTC-PERP {verdict.tier} {side_arrow} (paper)\n"
            f"trigger: {verdict.trigger_signal}\n"
            f"entry: ${entry_price:,.2f}  qty: {order.qty:.4f}\n"
            f"stop: ${proposal.stop_price:,.2f}  tp: ${proposal.tp_price:,.2f}\n"
            f"size: {(proposal.target_size_pct or 0)*100:.2f}%  "
            f"lev: {proposal.leverage:g}x  "
            f"eff_risk: {(proposal.effective_risk_pct or 0)*100:.3f}%\n"
            f"bias_4h={verdict.bias_4h.side}  bias_1d={verdict.bias_1d.side}  cvd={verdict.cvd.side}"
        )
