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

# Sizing tier defaults (USDC bet size → USDC copy size).
_DEFAULT_TIER_BOUNDARIES_USDC = (100.0, 1000.0)
_DEFAULT_TIER_SIZES_USDC = (1.0, 2.0, 5.0)

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
          sizing:
            tier_boundaries_usdc: [100.0, 1000.0]
            tier_sizes_usdc: [1.0, 2.0, 5.0]
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
            )
            proposals.extend(whale_proposals)

        return proposals

    # ── Per-whale processing ──────────────────────────────────────────

    async def _process_whale_activity(
        self, *, wallet: str, user_name: str,
        rows: list[ActivityRow], logger_agent: Any,
        market_state_fetcher: Any = None,
    ) -> list[ProposedOrder]:
        """Diff new trades against this whale's persisted state, emit orders."""
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
        if market_state_fetcher is not None and hasattr(
            market_state_fetcher, 'quote'
        ):
            try:
                current_price = await market_state_fetcher.quote(
                    f'{activity.slug}:{activity.outcome}'
                )
                if 0.0 < current_price < 1.0:
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
        copy_usdc = self._size_tier_usdc(activity.usdc_size)
        # Convert USDC bet size → contract count at the whale's entry price.
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
                "outcome": activity.outcome,
                "outcome_index": activity.outcome_index,
                "condition_id": activity.condition_id,
                "whale_wallet": wallet,
                "whale_user_name": user_name,
                "whale_entry_price": activity.price,
                "whale_usdc_size": activity.usdc_size,
                "whale_contracts": activity.size,
                "copy_size_usdc": copy_usdc,
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
                "outcome": activity.outcome,
                "outcome_index": activity.outcome_index,
                "condition_id": activity.condition_id,
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

    # ── Sizing ────────────────────────────────────────────────────────

    def _size_tier_usdc(self, whale_usdc_size: float) -> float:
        sz_cfg = self._strat_cfg.get("sizing") or {}
        bounds = tuple(sz_cfg.get("tier_boundaries_usdc")
                       or _DEFAULT_TIER_BOUNDARIES_USDC)
        sizes = tuple(sz_cfg.get("tier_sizes_usdc")
                      or _DEFAULT_TIER_SIZES_USDC)
        if len(sizes) != len(bounds) + 1:
            return float(_DEFAULT_TIER_SIZES_USDC[0])
        for i, b in enumerate(bounds):
            if whale_usdc_size < float(b):
                return float(sizes[i])
        return float(sizes[-1])

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

        keep: list[dict[str, Any]] = []
        paused: list[tuple[dict[str, Any], dict[str, Any]]] = []
        try:
            with sqlite3.connect(db_path) as conn:
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
                    )
                    if triggered:
                        paused.append((w, stats))
                    else:
                        keep.append(w)
        except Exception as e:
            log.warning(
                "polymarket_copy_trader: autopause filter errored: %s", e,
            )
            return selected_whales

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
