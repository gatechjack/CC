"""Polymarket Copy Trader strategy.

Mirrors top Polymarket whales' positions at scaled USDC sizing. Selected
whales come from an offline scoring pass over `/v1/leaderboard` +
`/activity` (see `polymarket_whale_stats` + `refresh_polymarket_whales`),
and are persisted to `agent_state(selected_whales)`. This strategy
consumes that list and watches the `/activity` event stream for each
whale to emit `ProposedOrder` rows on entries and exits.

Per cycle (every `poll_interval_sec`, default 60s on free public API):

  1. Load `selected_whales` from agent_state. Empty list → no-op.
  2. For each whale: fetch `/activity?user=<wallet>&limit=N` (cheap, free).
  3. Filter to TRADE rows with `timestamp > last_seen_ts[whale]`.
     Dedup by `transaction_hash` to handle in-flight or replayed events.
  4. For each new row:
     - `side=BUY`  → emit copy ProposedOrder using `outcome_index` directly.
       Sizing tier from `usdc_size` (whale's bet size).
       Record in `our_positions[whale][(condition_id, outcome_index)]`.
     - `side=SELL` → if we have a corresponding open copy, emit close.
       Remove from `our_positions`.
  5. Persist new `last_seen_ts[whale]` and `our_positions[whale]`.

**Cold-start protection:** First poll per whale records `last_seen_ts =
max(activity timestamps)` WITHOUT emitting orders. Otherwise we'd copy
all stale historical trades. Emissions only fire on rows strictly newer
than that baseline.

**Side detection is explicit** — Polymarket's activity row carries `side`
(BUY/SELL) + `outcome_index` (0 or 1) directly. No size-match dance like
Kalshi K3's trade-tape inference.

**Sizing** (USDC, v1, configurable in strategies.yaml):
  Whale bet size <$100   → $1 / leg
  Whale bet size $100-$1k → $2 / leg
  Whale bet size ≥$1k    → $5 / leg

Rationale: Polymarket trades clear in USDC, not Kalshi contracts. The
tier ladder mirrors K3's $1/$2/$3 shape but in USDC and slightly more
aggressive on the top end since Polymarket whale bets are often larger.

**Audit payload allowlist** (memory `trading_corp_audit_payload_allowlist`):
fields below MUST be enumerated in `main.py._scheduled_polymarket_copy_
trader_loop` base_payload or they're silently dropped at audit time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from trading_corp.agents.strategies._whale_autopause import (
    MAX_TOTAL_PNL,
    MAX_WIN_RATE_PCT,
    MIN_RESOLVED_TRADES,
    resolve_epoch,
    should_autopause,
    sqlite_path_from_db_url,
)
from trading_corp.data.polymarket_data_api_client import (
    ActivityRow, PolymarketDataAPIClient,
)
from trading_corp.persistence.db import load_agent_state, set_agent_state
from trading_corp.persistence.models import ProposedOrder

import sqlite3

log = logging.getLogger(__name__)


_AGENT_STATE_SELECTED_WHALES = "selected_whales"
_AGENT_STATE_WHALE_STATE_PREFIX = "whale_state:"  # per-whale state blob
_AGENT_STATE_LAST_POLL_TS = "last_poll_ts"

# E5b — dust threshold for the exit reconcile: a residual below this is
# economically meaningless at the PCT's ~$1 sizing (a held lot is ~2-3 shares), so
# treat it as a full exit (slot stays popped) rather than flag it forever. Kept in
# sync with brokers.polymarket_live._EXIT_RESIDUAL_EPS (same semantic, no x-module import).
_EXIT_RESIDUAL_EPS = 1e-3

# ── E2·3 clamp-sizing defaults (D4) ──────────────────────────────────────
# size = clamp(bankroll_usdc * per_trade_fraction * conviction_mult, min, max).
# Defaults reproduce flat ≈$1/order with conviction OFF (replaced the v1 tier
# ladder, which scaled copy size by the whale's bet size). Arithmetic:
#   120.0 * 0.00833 * 1.0 = 0.9996 ≈ $1.00, within [0.50, 2.00] → ~$1.00.
_DEFAULT_BANKROLL_USDC = 120.0          # static funded USDC.e balance (PCT ~119.98)
_DEFAULT_PER_TRADE_FRACTION = 0.00833   # 120.0 * 0.00833 = 0.9996 ≈ $1.00 / order
_DEFAULT_MIN_SIZE_USDC = 0.50           # clamp floor — tight (0.5×) around the ~$1 default
_DEFAULT_MAX_SIZE_USDC = 2.00           # clamp ceiling — tight (2×) around the ~$1 default
# Conviction (INERT by default; enabled is a config flip, not code):
_DEFAULT_CONVICTION_SIGNAL = "composite_score"  # whale_meta key when enabled
_DEFAULT_CONVICTION_FLOOR = 0.5         # clamp on the derived multiplier
_DEFAULT_CONVICTION_CAP = 2.0

# How many activity rows to fetch per whale per poll. Polymarket sorts
# /activity most-recent first; 20 covers minutes of even the busiest
# whale at 60s poll cadence. Increase if we observe truncation in audit.
_DEFAULT_ACTIVITY_LIMIT = 20


class PolymarketCopyTraderAgent:
    """Polymarket copy-trading strategy.

    Strategy config in `strategies.yaml`:

        polymarket_copy_trader:
          enabled: false                     # Board-flip after audit-mode
          auto_execute: false                # Phase 4+ before live orders
          division: polymarket_copy_trading
          poll_interval_sec: 60              # 60s on free public API
          activity_limit_per_poll: 20
          order_type: fak_synth              # E2·2 broker tif: fak_synth|gtc|fok|gtd
          fak_poll_seconds: 5                # E2·2 FAK-synth poll window (seconds)
          sizing:                            # E2·3 clamp formula (D4); default flat ≈$1
            bankroll_usdc: 120.0             # static funded balance
            per_trade_fraction: 0.00833      # 120 * 0.00833 = 0.9996 ≈ $1.00 / order
            min_size: 0.50                   # clamp floor (USDC)
            max_size: 2.00                   # clamp ceiling (USDC)
            conviction:
              enabled: false                 # OFF → conviction_mult = 1.0 (inert)
              signal: composite_score        # whale_meta key when enabled
              floor: 0.5
              cap: 2.0

    `order_type` / `fak_poll_seconds` are EXECUTION config consumed by
    `PolymarketLiveBroker` (brokers/polymarket_live.py), not this strategy. They
    select the live broker's time-in-force: `fak_synth` (default) synthesizes FAK
    over GTC since py_clob_client 0.17.5 has no native FAK; gtc/fok/gtd pass through
    native. The read->broker wiring lands in E2·6; the broker defaults match these.

    `sizing` (E2·3, D4) sets copy size = clamp(bankroll_usdc * per_trade_fraction *
    conviction_mult, min_size, max_size). Default = flat ≈$1/order with conviction
    OFF (conviction_mult == 1.0) — replaced the v1 tier ladder (size no longer
    scales with the whale's bet size). Enabling conviction-scaled sizing is a CONFIG
    flip (sizing.conviction.enabled), not code — but requires a copy-roster review
    of the pinned whales first (operator prerequisite). NOT wired to the live
    placement path here (emission still feeds paper would_have_placed); E2·6 routes it.
    """

    name = "polymarket_copy_trader"

    def __init__(
        self,
        strategies_yaml: Path = Path("config/strategies.yaml"),
        risk_yaml: Path = Path("config/risk.yaml"),
        db_url: str | None = None,
    ) -> None:
        self._strategies_yaml = Path(strategies_yaml)
        self._risk_yaml = Path(risk_yaml)
        self._db_url = db_url
        self._strat_mtime: float = 0.0
        self._risk_mtime: float = 0.0
        self._strat_cfg: dict[str, Any] = {}
        self._risk_cfg: dict[str, Any] = {}
        self._reload()

    # ── Config (mtime-cached, hot-reloadable) ─────────────────────────

    def _reload(self) -> None:
        try:
            sm = self._strategies_yaml.stat().st_mtime
            if sm != self._strat_mtime:
                with self._strategies_yaml.open("r", encoding="utf-8") as f:
                    self._strat_cfg = (yaml.safe_load(f) or {}).get(self.name, {}) or {}
                self._strat_mtime = sm
        except FileNotFoundError:
            self._strat_cfg = {}
        try:
            rm = self._risk_yaml.stat().st_mtime
            if rm != self._risk_mtime:
                with self._risk_yaml.open("r", encoding="utf-8") as f:
                    self._risk_cfg = (yaml.safe_load(f) or {}).get("polymarket", {}) or {}
                self._risk_mtime = rm
        except FileNotFoundError:
            self._risk_cfg = {}

    @property
    def enabled(self) -> bool:
        self._reload()
        return bool(self._strat_cfg.get("enabled", False))

    @property
    def auto_execute(self) -> bool:
        self._reload()
        return bool(self._strat_cfg.get("auto_execute", False))

    @property
    def division(self) -> str:
        self._reload()
        return str(self._strat_cfg.get("division", "polymarket_copy_trading"))

    # ── Public scan entry point ───────────────────────────────────────

    async def run_scan_cycle(
        self,
        *,
        data_api_client: PolymarketDataAPIClient,
        logger_agent: Any = None,
        market_state_fetcher: Any = None,
    ) -> list[ProposedOrder]:
        """One copy-trader cycle. Returns ProposedOrders for the risk gate.

        `data_api_client` must be an open async-context PolymarketDataAPIClient.

        `market_state_fetcher` is an optional PolymarketBroker-like object
        exposing async `get_market_resolution(condition_id=...)`. When
        provided, `_emit_entry` checks resolution status before placing —
        avoids the K3-class adverse-selection trap where a whale's stale
        activity-feed entry lands on a market that has already settled
        (observed on `btc-updown-5m-*` markets, 0/3 wins).
        """
        self._reload()
        if not self.enabled:
            return []

        selected_whales = self._load_selected_whales()
        if not selected_whales:
            log.info("polymarket_copy_trader: no selected whales; no-op")
            return []

        selected_whales = self._apply_autopause_filter(
            selected_whales, logger_agent=logger_agent,
        )
        if not selected_whales:
            log.info("polymarket_copy_trader: all whales auto-paused; no-op")
            return []

        activity_limit = int(self._strat_cfg.get("activity_limit_per_poll",
                                                 _DEFAULT_ACTIVITY_LIMIT))

        proposals: list[ProposedOrder] = []
        for whale_meta in selected_whales:
            wallet = whale_meta.get("wallet") if isinstance(whale_meta, dict) else None
            user_name = whale_meta.get("user_name", "") if isinstance(whale_meta, dict) else ""
            if not wallet:
                continue

            try:
                rows = await data_api_client.fetch_activity(wallet, limit=activity_limit)
            except Exception as e:
                log.warning(
                    "polymarket_copy_trader: fetch_activity(%s) failed: %s",
                    wallet[:10], e,
                )
                continue

            whale_proposals = await self._process_whale_activity(
                wallet=wallet, user_name=user_name, rows=rows,
                logger_agent=logger_agent,
                market_state_fetcher=market_state_fetcher,
                whale_meta=whale_meta if isinstance(whale_meta, dict) else None,
            )
            proposals.extend(whale_proposals)

        return proposals

    # ── Per-whale processing ──────────────────────────────────────────

    async def _process_whale_activity(
        self, *, wallet: str, user_name: str,
        rows: list[ActivityRow], logger_agent: Any,
        market_state_fetcher: Any = None,
        whale_meta: dict[str, Any] | None = None,
    ) -> list[ProposedOrder]:
        """Diff new trades against this whale's persisted state, emit orders.

        `whale_meta` is the selected-whale dict (carries conviction signals like
        composite_score / decision_win_rate); threaded to `_emit_entry` for the
        E2·3 sizing formula. Inert today (conviction OFF → multiplier 1.0)."""
        state = self._load_whale_state(wallet)
        is_cold_start = state is None

        if is_cold_start:
            # Baseline: highest seen timestamp + empty our_positions.
            max_ts = max((r.timestamp for r in rows if r.type == "TRADE"),
                         default=0)
            self._save_whale_state(wallet, {
                "user_name": user_name,
                "last_seen_ts": int(max_ts),
                "last_seen_txhashes": [],
                "our_positions": {},
            })
            if logger_agent is not None:
                logger_agent.log_event(
                    self.name, "polymarket_copy_cold_start",
                    {"strategy": self.name, "division": self.division,
                     "wallet": wallet, "user_name": user_name,
                     "baseline_ts": int(max_ts),
                     "baseline_rows_seen": len([r for r in rows if r.type == "TRADE"])},
                )
            return []

        last_seen_ts = int(state.get("last_seen_ts") or 0)
        last_seen_txhashes = set(state.get("last_seen_txhashes") or [])
        our_positions: dict[str, dict[str, Any]] = dict(state.get("our_positions") or {})

        # Filter to NEW trade events. Polymarket sorts most-recent first;
        # we sort ascending for processing so entries/exits land in
        # chronological order.
        new_rows = [
            r for r in rows
            if r.type == "TRADE"
            and r.timestamp > last_seen_ts
            and r.transaction_hash
            and r.transaction_hash not in last_seen_txhashes
        ]
        new_rows.sort(key=lambda r: r.timestamp)

        proposals: list[ProposedOrder] = []
        for r in new_rows:
            if r.side == "BUY":
                proposal = await self._emit_entry(
                    wallet=wallet, user_name=user_name, activity=r,
                    market_state_fetcher=market_state_fetcher,
                    logger_agent=logger_agent,
                    whale_meta=whale_meta,
                )
                if proposal is not None:
                    proposals.append(proposal)
                    pos_key = self._position_key(r.condition_id, r.outcome_index)
                    our_positions[pos_key] = {
                        "condition_id": r.condition_id,
                        "outcome_index": r.outcome_index,
                        "outcome": r.outcome,
                        "copy_size_usdc": float(proposal.extra.get("copy_size_usdc", 0.0)),
                        "entry_price": r.price,
                        "entry_ts": r.timestamp,
                        "whale_usdc_size": r.usdc_size,
                    }
            elif r.side == "SELL":
                pos_key = self._position_key(r.condition_id, r.outcome_index)
                prev_pos = our_positions.get(pos_key)
                proposal = self._emit_exit(
                    wallet=wallet, user_name=user_name, activity=r,
                    prev_pos=prev_pos, logger_agent=logger_agent,
                )
                if proposal is not None:
                    proposals.append(proposal)
                    our_positions.pop(pos_key, None)
            # else: ignore other types (deposits, withdrawals, etc.)

        # Bookkeeping: bump last_seen_ts to the freshest event we processed.
        # Keep recent txhashes to dedup if the next poll's window overlaps.
        if new_rows:
            new_last_ts = max(r.timestamp for r in new_rows)
            last_seen_ts = max(last_seen_ts, new_last_ts)
            recent_hashes = (
                [r.transaction_hash for r in new_rows]
                + list(last_seen_txhashes)
            )[:50]  # keep last 50 hashes to bound state size
        else:
            recent_hashes = list(last_seen_txhashes)[:50]

        self._save_whale_state(wallet, {
            "user_name": user_name,
            "last_seen_ts": int(last_seen_ts),
            "last_seen_txhashes": recent_hashes,
            "our_positions": our_positions,
        })
        return proposals

    # ── Entry / exit emission ─────────────────────────────────────────

    async def _emit_entry(
        self, *, wallet: str, user_name: str, activity: ActivityRow,
        market_state_fetcher: Any = None,
        logger_agent: Any = None,
        whale_meta: dict[str, Any] | None = None,
    ) -> ProposedOrder | None:
        # ── Fix 2026-05-14: skip already-resolved markets ──
        # The whale's activity feed surfaces trades with a 10-60s lag.
        # On short-duration markets (e.g. `btc-updown-5m-*`, 5-min bars)
        # the market may have already settled by the time we poll. Our
        # paper-trade then 'enters' at the whale's stale price on a
        # dead market and is guaranteed-loss when paired to a SELL.
        # Observed: 3/3 losses on `btc-updown-5m-*` markets where the
        # market's 5-min window had passed >hours before our poll.
        if market_state_fetcher is not None and hasattr(
            market_state_fetcher, 'get_market_resolution'
        ):
            try:
                res = await market_state_fetcher.get_market_resolution(
                    condition_id=activity.condition_id,
                )
                status = (res or {}).get('status')
                if status in ('resolved', 'void'):
                    if logger_agent is not None:
                        logger_agent.log_event(
                            self.name, 'polymarket_copy_entry_skipped_resolved',
                            {'strategy': self.name, 'division': self.division,
                             'wallet': wallet, 'whale_user_name': user_name,
                             'condition_id': activity.condition_id,
                             'slug': activity.slug,
                             'outcome': activity.outcome,
                             'whale_entry_price': activity.price,
                             'market_status': status,
                             'yes_won': (res or {}).get('yes_won')},
                        )
                    return None
            except Exception as e:
                log.warning(
                    'polymarket_copy_trader: market_state_fetcher failed for %s: %s',
                    activity.condition_id, e,
                )
        # ── Fix 2026-05-14: drift check ──
        # Activity-feed lag (10-60s+) means by our poll time the market may
        # have already moved against the whale's bet. Observed pattern in
        # Pedrobeliever47's political losses (Trump/Xi/Musk insider markets):
        # whale fills, insiders move price, market resolves opposite within
        # minutes. We were paper-trading at whale's stale fill price, which
        # over-states our actual entry. Skip when our outcome's current
        # price has dropped >threshold%% below whale's fill — alpha is gone.
        #
        # copy_quote_price: the real post-lag market price at our poll time.
        # Stored on taken entries so slippage (whale_entry_price vs our
        # actual fill) becomes measurable retrospectively. None when no
        # quote fetcher is available.
        copy_quote_price: float | None = None
        if market_state_fetcher is not None and hasattr(
            market_state_fetcher, 'quote'
        ):
            try:
                current_price = await market_state_fetcher.quote(
                    f'{activity.slug}:{activity.outcome}'
                )
                if 0.0 < current_price < 1.0:
                    # Capture for taken entries; enables post-hoc slippage analysis.
                    copy_quote_price = current_price
                    drift = (current_price - activity.price) / max(
                        activity.price, 0.01
                    )
                    threshold = float(self._strat_cfg.get(
                        'entry_drift_skip_threshold', -0.30,
                    ))
                    if drift < threshold:
                        if logger_agent is not None:
                            logger_agent.log_event(
                                self.name,
                                'polymarket_copy_entry_skipped_drift',
                                {'strategy': self.name,
                                 'division': self.division,
                                 'wallet': wallet,
                                 'whale_user_name': user_name,
                                 'condition_id': activity.condition_id,
                                 'slug': activity.slug,
                                 'outcome': activity.outcome,
                                 'whale_entry_price': activity.price,
                                 'current_price': current_price,
                                 'drift_pct': drift * 100,
                                 'threshold_pct': threshold * 100},
                            )
                        return None
            except Exception as e:
                log.warning(
                    'polymarket_copy_trader: quote drift check failed for %s: %s',
                    activity.condition_id, e,
                )
        copy_usdc = self._compute_copy_size_usdc(whale_meta=whale_meta)
        # Convert USDC copy size → contract count at the whale's entry price.
        # We mirror at the same price (paper-mode simplification) so our
        # contract count is just (our $) / (whale's entry price). The
        # downstream resolver's `pnl = qty * (1-price) if win else -qty*price`
        # math requires qty in CONTRACTS, not USDC — so this normalization
        # keeps the resolver venue-agnostic.
        if activity.price <= 0.0 or activity.price >= 1.0:
            return None  # malformed price → can't size the copy
        contracts = copy_usdc / activity.price
        return ProposedOrder(
            strategy=self.name,
            symbol=f"{activity.condition_id}:{activity.outcome}",
            side="buy",
            qty=contracts,
            order_type="market",
            limit_price=activity.price,
            rationale=(
                f"copy entry: @{user_name or wallet[:10]} bought "
                f"{activity.outcome} at ${activity.price:.2f} "
                f"(${activity.usdc_size:.2f} of {activity.size:.0f} contracts) "
                f"on \"{activity.title[:60]}\""
            ),
            extra={
                "is_entry": True,
                # Route this order into RiskAgent._evaluate_polymarket (the
                # gate keys off is_prediction_market; see risk.py:134).
                # implied_prob_at_entry == the binary outcome's price, which
                # IS the implied probability, and drives the [0.05,0.95] bound.
                "is_prediction_market": True,
                "implied_prob_at_entry": activity.price,
                "outcome": activity.outcome,
                "outcome_index": activity.outcome_index,
                "condition_id": activity.condition_id,
                # E2·1: the whale's ERC-1155 token id. Drives the broker's
                # DIRECT token_id path (brokers/polymarket_live.resolve_token_id);
                # None (asset absent) → gamma-lookup fallback, unchanged.
                "token_id": activity.asset or None,
                "whale_wallet": wallet,
                "whale_user_name": user_name,
                "whale_entry_price": activity.price,
                "whale_usdc_size": activity.usdc_size,
                "whale_contracts": activity.size,
                "copy_size_usdc": copy_usdc,
                # Real post-lag market price at our poll time (vs whale's fill
                # price above). Enables slippage measurement: compare
                # whale_entry_price to copy_quote_price to see how much the
                # market moved between the whale's fill and our copy. None
                # when no quote fetcher is configured.
                "copy_quote_price": copy_quote_price,
                "first_seen_ts": activity.timestamp,
                "market_title": activity.title,
                "market_slug": activity.slug,
                "event_slug": activity.event_slug,
                "division": self.division,
            },
        )

    def _emit_exit(
        self, *, wallet: str, user_name: str, activity: ActivityRow,
        prev_pos: dict[str, Any] | None, logger_agent: Any,
    ) -> ProposedOrder | None:
        if prev_pos is None:
            # Whale sold a position we never opened — nothing to close.
            return None
        copy_usdc = float(prev_pos.get("copy_size_usdc") or 0.0)
        entry_price = float(prev_pos.get("entry_price") or 0.0)
        if copy_usdc <= 0.0 or entry_price <= 0.0:
            return None
        # Match the entry's unit normalization: qty in CONTRACTS, sized so
        # that copy_usdc dollars at our paper-fill entry_price = this many
        # contracts. The SELL closes the same lot.
        contracts = copy_usdc / entry_price
        return ProposedOrder(
            strategy=self.name,
            symbol=f"{activity.condition_id}:{activity.outcome}",
            side="sell",
            qty=contracts,
            order_type="market",
            limit_price=activity.price,
            rationale=(
                f"copy exit: @{user_name or wallet[:10]} closed "
                f"{activity.outcome} at ${activity.price:.2f}; "
                f"selling our ${copy_usdc:.2f} copy ({contracts:.2f} contracts)"
            ),
            extra={
                "is_entry": False,
                # Subject the close to the Polymarket notional caps too.
                # implied_prob_at_entry uses the ORIGINAL entry price (already
                # validated in-bounds at entry), NOT the exit price — sizing an
                # exit's bound off an extreme exit price could spuriously reject
                # a legitimate close.
                "is_prediction_market": True,
                "implied_prob_at_entry": entry_price,
                "outcome": activity.outcome,
                "outcome_index": activity.outcome_index,
                "condition_id": activity.condition_id,
                # E2·1: token id of the outcome being closed (the whale's SELL
                # activity row's asset — same outcome we hold). Drives the
                # broker's direct token_id path; None → gamma fallback.
                "token_id": activity.asset or None,
                "whale_wallet": wallet,
                "whale_user_name": user_name,
                "whale_exit_price": activity.price,
                "whale_usdc_size": activity.usdc_size,
                "copy_size_usdc": copy_usdc,
                "entry_ts": prev_pos.get("entry_ts", 0),
                "exit_ts": activity.timestamp,
                "market_title": activity.title,
                "division": self.division,
            },
        )

    @staticmethod
    def _position_key(condition_id: str, outcome_index: int) -> str:
        return f"{condition_id}|{int(outcome_index)}"

    # ── Sizing (E2·3 — clamp formula, D4) ─────────────────────────────

    def _compute_copy_size_usdc(self, whale_meta: dict[str, Any] | None = None) -> float:
        """USDC copy size per the D4 clamp formula:

            size = clamp(bankroll_usdc * per_trade_fraction * conviction_mult,
                         min_size, max_size)

        DEFAULT config → flat ≈$1/order (120.0 * 0.00833 = 0.9996) with conviction
        OFF (conviction_mult == 1.0). REPLACED the v1 tier ladder (`_size_tier_usdc`,
        removed): copy size no longer scales with the whale's bet size — it's the
        operator-chosen flat base, with the full proportional/conviction schema
        present but inert by config. NOT wired to a LIVE placement path here (still
        feeds `would_have_placed` paper rows); E2·6 routes the emission to the broker.
        """
        sz = self._strat_cfg.get("sizing") or {}
        bankroll = float(sz.get("bankroll_usdc", _DEFAULT_BANKROLL_USDC))
        fraction = float(sz.get("per_trade_fraction", _DEFAULT_PER_TRADE_FRACTION))
        min_size = float(sz.get("min_size", _DEFAULT_MIN_SIZE_USDC))
        max_size = float(sz.get("max_size", _DEFAULT_MAX_SIZE_USDC))
        raw = bankroll * fraction * self._conviction_mult(whale_meta)
        return max(min_size, min(raw, max_size))

    def _conviction_mult(self, whale_meta: dict[str, Any] | None) -> float:
        """Resolve the conviction multiplier for the sizing formula.

        INERT by default → returns 1.0: conviction-scaled sizing stays OFF until
        `sizing.conviction.enabled` is flipped true, which is a CONFIG change, NOT
        code (the whale_meta plumbing into `_compute_copy_size_usdc` is already
        wired). Enabling first requires a copy-roster review of the pinned whales
        (several are truncated/untrustworthy) — an OPERATOR prerequisite, not E2·3's
        concern. See [[polymarket-option-c-phase2]] (copy-roster review).

        When enabled, the multiplier is the whale's conviction signal from
        `whale_meta` (`composite_score` default, or `decision_win_rate`), clamped to
        [floor, cap]. A missing/unparseable signal degrades to 1.0 — never amplify
        sizing on bad data.
        """
        sz = self._strat_cfg.get("sizing") or {}
        conv = sz.get("conviction") or {}
        if not bool(conv.get("enabled", False)):
            return 1.0
        signal = str(conv.get("signal", _DEFAULT_CONVICTION_SIGNAL))
        floor = float(conv.get("floor", _DEFAULT_CONVICTION_FLOOR))
        cap = float(conv.get("cap", _DEFAULT_CONVICTION_CAP))
        try:
            mult = float((whale_meta or {}).get(signal))
        except (TypeError, ValueError):
            return 1.0
        return max(floor, min(mult, cap))

    # ── Auto-pause filter (2026-05-14 P3) ─────────────────────────────

    def _apply_autopause_filter(
        self, selected_whales: list[dict[str, Any]],
        *, logger_agent: Any,
    ) -> list[dict[str, Any]]:
        """Drop whales whose resolved-RT stats trip the auto-pause threshold.

        For each triggered whale: persist updated selected_whales to
        agent_state and emit `polymarket_whale_auto_paused` audit.
        Returns the filtered list (a shallow copy of `selected_whales`
        if nothing triggered).
        """
        db_path = sqlite_path_from_db_url(self._db_url or "")
        if not db_path:
            return selected_whales

        # Evaluate the SAME forward window the operator dashboard shows
        # (entry_ts >= agent_state metrics_epoch; None = all-time when unset).
        # `autopause_mode: shadow` observes-only: it emits
        # `polymarket_whale_would_auto_pause` without removing the whale.
        shadow = (
            str(self._strat_cfg.get("autopause_mode", "active")).strip().lower()
            == "shadow"
        )
        since_ts: str | None = None
        keep: list[dict[str, Any]] = []
        paused: list[tuple[dict[str, Any], dict[str, Any]]] = []
        shadow_hits: list[tuple[dict[str, Any], dict[str, Any]]] = []
        try:
            with sqlite3.connect(db_path) as conn:
                since_ts = resolve_epoch(conn, self.name)
                for w in selected_whales:
                    user_name = (w.get("user_name") or "").strip()
                    if not user_name:
                        keep.append(w)
                        continue
                    triggered, stats = should_autopause(
                        conn,
                        whale_name=user_name,
                        table="polymarket_round_trips",
                        name_field="whale_user_name",
                        division=self.division,
                        since_ts=since_ts,
                    )
                    if triggered and shadow:
                        shadow_hits.append((w, stats))
                        keep.append(w)
                    elif triggered:
                        paused.append((w, stats))
                    else:
                        keep.append(w)
        except Exception as e:
            log.warning(
                "polymarket_copy_trader: autopause filter errored: %s", e,
            )
            return selected_whales

        for w, stats in shadow_hits:
            log.warning(
                "polymarket_copy_trader: [shadow] WOULD auto-pause %s "
                "(%d RT, %.1f%% WR, $%.2f) since=%s",
                w.get("user_name"), stats["n_resolved"],
                stats["win_rate_pct"] or 0.0, stats["total_realized_pnl"],
                since_ts,
            )
            if logger_agent is not None:
                logger_agent.log_event(
                    self.name, "polymarket_whale_would_auto_pause",
                    {
                        "strategy": self.name,
                        "division": self.division,
                        "wallet": w.get("wallet"),
                        "whale_user_name": w.get("user_name"),
                        "category": w.get("category"),
                        "rank": w.get("rank"),
                        "since_ts": since_ts,
                        "thresholds": {
                            "min_trades": MIN_RESOLVED_TRADES,
                            "max_wr_pct": MAX_WIN_RATE_PCT,
                            "max_pnl": MAX_TOTAL_PNL,
                        },
                        **stats,
                    },
                )

        if not paused:
            return keep

        try:
            set_agent_state(
                self.name, _AGENT_STATE_SELECTED_WHALES, keep,
                db_url=self._db_url,
            )
        except Exception as e:
            log.error(
                "polymarket_copy_trader: failed to persist auto-paused "
                "selected_whales (will retry next scan): %s", e,
            )
            return selected_whales

        for w, stats in paused:
            log.warning(
                "polymarket_copy_trader: auto-pausing %s (%d RT, %.1f%% WR, $%.2f)",
                w.get("user_name"), stats["n_resolved"],
                stats["win_rate_pct"] or 0.0, stats["total_realized_pnl"],
            )
            if logger_agent is not None:
                logger_agent.log_event(
                    self.name, "polymarket_whale_auto_paused",
                    {
                        "strategy": self.name,
                        "division": self.division,
                        "wallet": w.get("wallet"),
                        "whale_user_name": w.get("user_name"),
                        "category": w.get("category"),
                        "rank": w.get("rank"),
                        "since_ts": since_ts,
                        "thresholds": {
                            "min_trades": MIN_RESOLVED_TRADES,
                            "max_wr_pct": MAX_WIN_RATE_PCT,
                            "max_pnl": MAX_TOTAL_PNL,
                        },
                        "remaining_whales": len(keep),
                        **stats,
                    },
                )
        return keep

    # ── State (agent_state-backed) ────────────────────────────────────

    def _load_selected_whales(self) -> list[dict[str, Any]]:
        """Returns list of {wallet, user_name, category, ...} dicts.

        The selection script writes a list of dicts (not just wallets) so
        we carry the user_name + category for logging without re-fetching.
        """
        if not self._db_url:
            return []
        rec = load_agent_state(
            self.name, _AGENT_STATE_SELECTED_WHALES, db_url=self._db_url,
        )
        if rec is None:
            return []
        value = rec[0]
        if isinstance(value, list):
            # Tolerate both legacy [wallet, wallet, ...] and the rich
            # [{wallet, user_name, ...}] form.
            out: list[dict[str, Any]] = []
            for v in value:
                if isinstance(v, dict) and v.get("wallet"):
                    out.append(v)
                elif isinstance(v, str):
                    out.append({"wallet": v, "user_name": ""})
            return out
        return []

    def _load_whale_state(self, wallet: str) -> dict[str, Any] | None:
        if not self._db_url:
            return None
        rec = load_agent_state(
            self.name, f"{_AGENT_STATE_WHALE_STATE_PREFIX}{wallet}",
            db_url=self._db_url,
        )
        if rec is None:
            return None
        value = rec[0]
        if isinstance(value, dict):
            return value
        return None

    def _save_whale_state(self, wallet: str, state: dict[str, Any]) -> None:
        if not self._db_url:
            return
        set_agent_state(
            self.name, f"{_AGENT_STATE_WHALE_STATE_PREFIX}{wallet}", state,
            db_url=self._db_url,
        )

    # ── E2·6: post-placement position reconciliation (live path) ──────

    def _position_locator(self, order: ProposedOrder):
        """(wallet, pos_key) for a copy order, from its extra; None if unlocatable."""
        ext = order.extra or {}
        wallet = ext.get("whale_wallet")
        condition_id = ext.get("condition_id")
        outcome_index = ext.get("outcome_index")
        if not wallet or condition_id is None or outcome_index is None:
            return None
        return wallet, self._position_key(condition_id, outcome_index)

    def record_entry_fill(self, order: ProposedOrder, fill: Any) -> None:
        """E2·6 — overwrite a LIVE entry's recorded position with the ACTUAL filled
        qty/price from the FillEvent.

        The BUY branch (`_process_whale_activity`) writes `our_positions[pos_key]`
        OPTIMISTICALLY at emit time from the INTENDED `copy_size_usdc` + the whale's
        price, BEFORE the order is placed. After a real (possibly partial) fill we
        must reconcile to truth. The slot stores `copy_size_usdc` + `entry_price`
        and `_emit_exit` reconstructs the sell qty as `copy_size_usdc / entry_price`,
        so we store `entry_price = fill.price` and `copy_size_usdc = fill.qty *
        fill.price` → a later exit sells EXACTLY `fill.qty` (the real held lot,
        partial OR full). No-op if the position can't be located or the fill is
        empty (an empty/no fill is handled by the loop via `discard_entry`)."""
        loc = self._position_locator(order)
        if loc is None:
            return
        wallet, pos_key = loc
        try:
            fill_qty = float(getattr(fill, "qty", 0.0) or 0.0)
            fill_price = float(getattr(fill, "price", 0.0) or 0.0)
        except (TypeError, ValueError):
            return
        if fill_qty <= 0.0 or fill_price <= 0.0:
            return
        state = self._load_whale_state(wallet)
        if not state:
            return
        our_positions = dict(state.get("our_positions") or {})
        pos = our_positions.get(pos_key)
        if not isinstance(pos, dict):
            return
        pos = dict(pos)
        pos["copy_size_usdc"] = fill_qty * fill_price  # so qty=usdc/price == fill.qty
        pos["entry_price"] = fill_price
        pos["actual_fill_qty"] = fill_qty              # explicit real-lot record
        pos["execution_mode"] = "live"
        our_positions[pos_key] = pos
        state["our_positions"] = our_positions
        self._save_whale_state(wallet, state)

    def discard_entry(self, order: ProposedOrder) -> None:
        """E2·6 — drop the optimistically-recorded position for a LIVE entry that
        did NOT fill (`NoFillInWindow`). The BUY branch records `our_positions`
        BEFORE placement; a no-fill means we hold NOTHING, so the position must be
        removed — else a later `_emit_exit` would try to sell a lot we never
        acquired. No-op if unlocatable or already absent."""
        loc = self._position_locator(order)
        if loc is None:
            return
        wallet, pos_key = loc
        state = self._load_whale_state(wallet)
        if not state:
            return
        our_positions = dict(state.get("our_positions") or {})
        if our_positions.pop(pos_key, None) is not None:
            state["our_positions"] = our_positions
            self._save_whale_state(wallet, state)

    def record_exit_fill(self, order: ProposedOrder, fill: Any) -> float:
        """E5b — reconcile a LIVE exit against its (possibly cumulative) fill: the
        exit-side mirror of `record_entry_fill`.

        Phase A already popped the slot optimistically (`_process_whale_activity`
        :336) at proposal-emit, so we reconstruct the held lot from `order.extra`
        (set by `_emit_exit`): held = `copy_size_usdc / implied_prob_at_entry` (== the
        real held qty, since `record_entry_fill` made `copy_size_usdc / entry_price ==
        the entry fill.qty`). `fill` may be None (a total no-fill exit) → fill_qty 0.

        residual = held − cumulative fill.qty:
          * residual ≤ EPS → leave the slot POPPED (full exit achieved); return 0.0.
          * residual > EPS → RE-INSERT a COMPLETE, flagged residual slot (visible, not
            orphaned) for manual reconcile; auto-retry is DEFERRED.

        Returns the retained residual qty (0.0 on full exit / unlocatable) so the
        caller can surface the manual-reconcile flag (audit + telegram). LIVE only —
        the paper path returns early in `main` and never reaches this."""
        loc = self._position_locator(order)
        if loc is None:
            return 0.0
        wallet, pos_key = loc
        ext = order.extra or {}
        try:
            entry_price = float(ext.get("implied_prob_at_entry") or 0.0)
            copy_usdc = float(ext.get("copy_size_usdc") or 0.0)
        except (TypeError, ValueError):
            return 0.0
        if entry_price <= 0.0 or copy_usdc <= 0.0:
            return 0.0
        held = copy_usdc / entry_price
        try:
            fill_qty = float(getattr(fill, "qty", 0.0) or 0.0)
        except (TypeError, ValueError):
            fill_qty = 0.0
        residual = held - fill_qty
        if residual <= _EXIT_RESIDUAL_EPS:
            return 0.0                                  # full exit → slot stays popped (Phase A)
        state = self._load_whale_state(wallet)
        if not state:
            return 0.0
        our_positions = dict(state.get("our_positions") or {})
        our_positions[pos_key] = {                      # COMPLETE residual record (not a stub)
            "condition_id": ext.get("condition_id"),
            "outcome_index": ext.get("outcome_index"),
            "outcome": ext.get("outcome"),
            "copy_size_usdc": residual * entry_price,   # so a future _emit_exit reconstructs `residual`
            "entry_price": entry_price,                 # original basis carried forward
            "entry_ts": ext.get("entry_ts", 0),
            "whale_usdc_size": ext.get("whale_usdc_size", 0.0),
            "actual_fill_qty": residual,                # explicit residual lot
            "execution_mode": "live",
            "reconcile_needed": True,                   # ── the manual-reconcile flag ──
            "reconcile_reason": "exit_no_fill" if fill_qty <= 0.0 else "exit_partial",
            "reconcile_ts": ext.get("exit_ts", 0),
            "residual_qty": residual,
        }
        state["our_positions"] = our_positions
        self._save_whale_state(wallet, state)
        return residual


def force_close_whale_positions(
    wallet: str,
    *,
    db_url: str,
    logger_agent: Any,
    division: str = "polymarket_copy_trading",
    reason: str = "demoted_via_ui",
) -> dict[str, Any]:
    """Synthesize SELL closes for every tracked open position of a whale.

    Used by the demote endpoint to flatten a whale's paper book before
    removing them from `selected_whales`. For each entry in the whale's
    persisted `our_positions`, emit a `would_have_placed` audit row with
    `side=sell` so `polymarket_resolver` pairs it with the corresponding
    entry into a `polymarket_round_trips` row.

    Exit price = entry_price (zero-PnL synthetic close v1). Future
    iteration could plug in `broker.quote()` for true mark-to-market.

    After emission, reset the whale's `whale_state:<wallet>` slot to a
    fresh-baseline shape (last_seen_ts = now, our_positions = empty) so
    a future re-promotion does not replay history.

    Returns: ``{"n_closed": int, "wallet": str, "positions": list[dict]}``.
    """
    rec = load_agent_state(
        "polymarket_copy_trader",
        f"{_AGENT_STATE_WHALE_STATE_PREFIX}{wallet}",
        db_url=db_url,
    )
    if rec is None:
        return {"n_closed": 0, "wallet": wallet, "positions": []}
    state = rec[0]
    if not isinstance(state, dict):
        return {"n_closed": 0, "wallet": wallet, "positions": []}
    our_positions = state.get("our_positions") or {}
    user_name = str(state.get("user_name") or "")
    closed: list[dict[str, Any]] = []
    now_unix = int(__import__("time").time())
    for pos_key, pos in list(our_positions.items()):
        if not isinstance(pos, dict):
            continue
        condition_id = str(pos.get("condition_id") or "")
        outcome = str(pos.get("outcome") or "")
        outcome_index = int(pos.get("outcome_index") or 0)
        entry_price = float(pos.get("entry_price") or 0.0)
        copy_usdc = float(pos.get("copy_size_usdc") or 0.0)
        if entry_price <= 0.0 or copy_usdc <= 0.0 or not condition_id:
            continue
        contracts = copy_usdc / entry_price
        order_id = f"synthetic_close:{reason}:{wallet}:{pos_key}:{now_unix}"
        payload = {
            "strategy": "polymarket_copy_trader",
            "division": division,
            "order_id": order_id,
            "side": "sell",
            "symbol": f"{condition_id}:{outcome}",
            "qty": contracts,
            "limit_price": entry_price,
            "is_entry": False,
            "is_synthetic_close": True,
            "synthetic_close_reason": reason,
            "outcome": outcome,
            "outcome_index": outcome_index,
            "condition_id": condition_id,
            "whale_wallet": wallet,
            "whale_user_name": user_name,
            "copy_size_usdc": copy_usdc,
            "entry_ts": int(pos.get("entry_ts") or 0),
            "rationale": (
                f"synthetic close ({reason}): @{user_name or wallet[:10]} "
                f"position flattened on demote"
            ),
        }
        if logger_agent is not None:
            try:
                logger_agent.log_event(
                    "polymarket_copy_trader", "would_have_placed", payload,
                )
            except Exception as e:
                log.warning(
                    "polymarket force_close: log_event failed for %s/%s: %s",
                    wallet[:10], pos_key, e,
                )
        closed.append({
            "pos_key": pos_key,
            "symbol": payload["symbol"],
            "contracts": contracts,
            "copy_usdc": copy_usdc,
            "order_id": order_id,
        })
    # Reset state slot so a future re-promotion does not replay history.
    # Keep last_seen_ts at "now" so the strategy treats subsequent activity
    # as new (not as a replay window starting from epoch).
    set_agent_state(
        "polymarket_copy_trader",
        f"{_AGENT_STATE_WHALE_STATE_PREFIX}{wallet}",
        {
            "user_name": user_name,
            "last_seen_ts": now_unix,
            "last_seen_txhashes": [],
            "our_positions": {},
        },
        db_url=db_url,
    )
    return {"n_closed": len(closed), "wallet": wallet, "positions": closed}
