"""Kalshi Copy Trader strategy — Phase K3.

Mirrors top Kalshi traders' positions at scaled-down size. Selected whales
are produced by the offline selection loop (`kalshi_whale_stats.score_whale`
→ top-N by composite Wilson-LCB × ROI × category score) and persisted to
`agent_state(selected_whales)`. This strategy CONSUMES that list and emits
`ProposedOrder` rows when a selected whale opens or closes a position.

Per cycle (every `poll_interval_sec`, default 300s on Apify Starter):

  1. Load `selected_whales` from agent_state. Empty list → no-op.
  2. Apify call: `fetch_open_positions(selected_whales)` — returns ~20
     positions per whale (Apify floor).
  3. For each whale: compare to `agent_state(positions:{whale})` snapshot.
     - NEW tickers (entry) → detect side via Kalshi public trade tape,
       compute sizing tier, emit ProposedOrder for entry.
     - REMOVED tickers (exit) → emit ProposedOrder to close our copy
       (size = whatever we opened with).
     - Size-up / size-down within an existing ticker is IGNORED in V1
       (entries + exits only per the locked design).
  4. Persist new snapshot to agent_state.

**Cold-start protection:** First-ever poll for a whale records all current
positions as `last_known` WITHOUT emitting ProposedOrders. Otherwise we'd
copy stale positions the whale entered long ago. Emissions only fire on
deltas detected AFTER the first poll.

**Side detection** uses Kalshi's public market trade tape (free, anonymous
at trader level but exposes `taker_side`). We size-match the whale's new
position contracts against trades in the inter-poll window:
  - unique size-match → `confidence=high`, side = taker_side
  - multiple matches → `confidence=medium`, side = nearest-to-T2 taker_side
  - no match (busy market obscures) → `confidence=low`, SKIP this entry
The skip is conservative — never copy the wrong side.

**Sizing** (Bronze plan, V1):
  Whale contracts <  100    → $1 / leg
  Whale contracts 100-1000  → $2 / leg
  Whale contracts >  1000   → $3 / leg
Rationale: Kalshi contracts trade in [$0.01, $0.99], so contract count is
within ~100x of dollar exposure. Bucket tiers preserve conviction signal
while staying inside risk.yaml kalshi $5/leg cap.

**Audit payload allowlist gotcha** (memory `trading_corp_audit_payload_
allowlist`): ProposedOrder.extra fields surface to audit storage ONLY if
the `_scheduled_kalshi_copy_trader_loop` in main.py adds matching
`ext.get("...")` keys to its base_payload dict. Fields used here:
  - whale_handle
  - whale_position_contracts
  - whale_skill_score
  - copy_size_usd
  - side_detection_confidence
  - is_entry (bool — entries true, exits false)
  - first_seen_iso (when whale's position first appeared in our snapshots)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from trading_corp.agents.strategies._whale_autopause import (
    MAX_TOTAL_PNL,
    MAX_WIN_RATE_PCT,
    MIN_RESOLVED_TRADES,
    should_autopause,
    sqlite_path_from_db_url,
)
from trading_corp.brokers.kalshi import KalshiPublicTrade
from trading_corp.data.kalshi_apify_client import KalshiApifyClient, WhalePosition
from trading_corp.persistence.db import load_agent_state, set_agent_state
from trading_corp.persistence.models import ProposedOrder

import sqlite3

log = logging.getLogger(__name__)


_AGENT_STATE_SELECTED_WHALES = "selected_whales"
_AGENT_STATE_POSITIONS_PREFIX = "positions:"  # per-whale: positions:{nickname}
_AGENT_STATE_LAST_POLL_TS = "last_poll_ts"

# Sizing tier breakpoints (Kalshi contracts -> USD per-leg copy size).
_DEFAULT_TIER_BOUNDARIES = (100, 1000)
_DEFAULT_TIER_SIZES_USD = (1.0, 2.0, 3.0)


def _trade_price_for_side(trade: KalshiPublicTrade) -> float | None:
    """Per-contract dollar price for the inferred side of `trade`.

    Kalshi trade-tape rows carry both legs' prices (yes_price_dollars +
    no_price_dollars); we pick the side the taker hit. None on
    unrecognized taker_side rather than guessing wrong.
    """
    side = (trade.taker_side or "").lower()
    if side == "yes":
        return float(trade.yes_price_dollars)
    if side == "no":
        return float(trade.no_price_dollars)
    return None


@runtime_checkable
class TradeTapeFetcher(Protocol):
    """Structural Protocol matched by `KalshiBroker.get_market_trades`.

    The strategy depends on this Protocol, not the concrete broker, so
    unit tests can pass a stub. Returning [] is acceptable — strategy
    falls back to confidence='low' and skips the entry.
    """

    async def get_market_trades(
        self,
        ticker: str,
        *,
        since: datetime,
        until: datetime,
        limit: int = 100,
    ) -> list[KalshiPublicTrade]: ...



# Sports ticker families to skip — kalshi_sports_scout owns these (2026-05-14).
# Add new prefixes here as Kalshi launches new sport categories.
_SPORTS_TICKER_PREFIXES = (
    "KXMLB", "KXNBA", "KXNHL", "KXNFL", "KXMLS",
    "KXATP", "KXWTA", "KXITF",
    "KXCS2", "KXDOTA", "KXLCS",
    "KXLIGAMX", "KXARGPREM", "KXCOPADOBRASIL", "KXDIMAYOR",
    "KXDENSUPERLIGA", "KXSAUDIPL", "KXURYPD", "KXAPFDDH",
    "KXEPL", "KXUCL", "KXUEL", "KXBUNDESLIGA", "KXLALIGA", "KXSERIEA",
    "KXLIGUE1", "KXJLEAGUE", "KXNCAAF", "KXNCAAB", "KXUFC", "KXBOXING",
)


def _is_sports_ticker(ticker: str) -> bool:
    """True if `ticker` is in a known sports market family.

    Used by K3 to route Sports-category trades to `kalshi_sports_scout`
    (and eventually a dedicated trading division). Kalshi doesn't tag
    `category` on the activity-feed scraper output, so we prefix-match
    on the ticker. Maintenance: add new prefixes as Kalshi launches new
    sport categories.
    """
    if not ticker:
        return False
    return any(ticker.startswith(p) for p in _SPORTS_TICKER_PREFIXES)


class KalshiCopyTraderAgent:
    """Phase K3 copy-trading strategy.

    Strategy config in `strategies.yaml`:

        kalshi_copy_trader:
          enabled: false                    # Board-flip after audit-mode validation
          auto_execute: false               # Phase K5+ before this can flip live
          division: kalshi_copy_trading
          poll_interval_sec: 300            # 5min on Bronze; 3600 (hourly) on FREE
          sizing:
            tier_boundaries_contracts: [100, 1000]
            tier_sizes_usd: [1.0, 2.0, 3.0]
          side_detection:
            size_match_tolerance_pct: 5.0   # ±5% on contract count for trade match
    """

    name = "kalshi_copy_trader"

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
                    self._risk_cfg = (yaml.safe_load(f) or {}).get("kalshi", {}) or {}
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
        return str(self._strat_cfg.get("division", "kalshi_copy_trading"))

    # ── Public scan entry point ───────────────────────────────────────

    async def run_scan_cycle(
        self,
        *,
        apify_client: KalshiApifyClient,
        trade_tape_fetcher: TradeTapeFetcher | None = None,
        logger_agent: Any = None,
    ) -> list[ProposedOrder]:
        """One copy-trader cycle. Returns ProposedOrders for the risk gate.

        `apify_client` is an open async-context KalshiApifyClient.
        `trade_tape_fetcher` provides Kalshi public trade tape for side
        detection; if None, all new-entry detections fall back to
        confidence='low' and skip (no entries copied).
        """
        self._reload()
        if not self.enabled:
            return []

        selected = self._load_selected_whales()
        if not selected:
            log.info("kalshi_copy_trader: no selected whales in agent_state; no-op")
            return []

        selected = self._apply_autopause_filter(
            selected, logger_agent=logger_agent,
        )
        if not selected:
            log.info("kalshi_copy_trader: all whales auto-paused; no-op")
            return []

        try:
            current_positions = await apify_client.fetch_open_positions(selected)
        except Exception as e:
            log.warning("kalshi_copy_trader: apify open_positions fetch failed: %s", e)
            return []

        # Group current positions by whale nickname for delta calc.
        by_whale: dict[str, dict[str, WhalePosition]] = {w: {} for w in selected}
        for p in current_positions:
            if p.name in by_whale and p.is_open:
                by_whale[p.name][p.market_ticker] = p

        now = datetime.now(timezone.utc)
        last_poll_iso = self._get_last_poll_iso()
        last_poll_ts = (
            datetime.fromisoformat(last_poll_iso).replace(tzinfo=timezone.utc)
            if last_poll_iso else now
        )

        proposals: list[ProposedOrder] = []
        for whale, current_by_ticker in by_whale.items():
            prev_snapshot = self._load_whale_snapshot(whale)
            is_cold_start = prev_snapshot is None
            if is_cold_start:
                baseline = {
                    ticker: {
                        "contracts": p.contracts, "pnl": p.pnl,
                        "first_seen_iso": now.isoformat(),
                        "our_side": "", "copy_size_usd": 0.0,
                    }
                    for ticker, p in current_by_ticker.items()
                }
                self._save_whale_snapshot_raw(whale, baseline)
                if logger_agent is not None:
                    logger_agent.log_event(
                        self.name, "kalshi_copy_cold_start",
                        {"strategy": self.name, "division": self.division,
                         "whale": whale, "baseline_positions": len(current_by_ticker)},
                    )
                continue

            prev_tickers = set(prev_snapshot.keys())
            curr_tickers = set(current_by_ticker.keys())
            new_tickers = curr_tickers - prev_tickers
            removed_tickers = prev_tickers - curr_tickers
            carryover_tickers = curr_tickers - new_tickers

            new_snapshot: dict[str, dict[str, Any]] = {}

            # Entries: emit ProposedOrder + persist our_side/size if accepted.
            for ticker in new_tickers:
                # Skip Sports — handled by kalshi_sports_scout (2026-05-14).
                if _is_sports_ticker(ticker):
                    if logger_agent is not None:
                        logger_agent.log_event(
                            self.name, 'kalshi_copy_entry_skipped_sports',
                            {'strategy': self.name, 'division': self.division,
                             'wallet': wallet, 'whale_handle': user_name,
                             'ticker': ticker,
                             'reason': 'sports_routed_to_scout'},
                        )
                    continue
                pos = current_by_ticker[ticker]
                record = {
                    "contracts": pos.contracts, "pnl": pos.pnl,
                    "first_seen_iso": now.isoformat(),
                    "our_side": "", "copy_size_usd": 0.0, "entry_price": None,
                }
                proposal = await self._emit_entry(
                    whale=whale, position=pos,
                    since=last_poll_ts, until=now,
                    trade_fetcher=trade_tape_fetcher,
                    logger_agent=logger_agent,
                )
                if proposal is not None:
                    record["our_side"] = str(proposal.extra.get("outcome", ""))
                    record["copy_size_usd"] = float(proposal.extra.get("copy_size_usd", 0.0))
                    # Carry the entry fill price into the snapshot so the
                    # later exit emission (and the resolver's pairing pass)
                    # has both legs of the round-trip without re-joining
                    # against audit_event.
                    record["entry_price"] = proposal.extra.get("whale_entry_price")
                    proposals.append(proposal)
                new_snapshot[ticker] = record

            # Carryovers: refresh contracts/pnl, preserve our_side/copy_size_usd/entry_price.
            for ticker in carryover_tickers:
                pos = current_by_ticker[ticker]
                prev_rec = prev_snapshot.get(ticker) or {}
                new_snapshot[ticker] = {
                    "contracts": pos.contracts, "pnl": pos.pnl,
                    "first_seen_iso": str(prev_rec.get("first_seen_iso") or now.isoformat()),
                    "our_side": str(prev_rec.get("our_side") or ""),
                    "copy_size_usd": float(prev_rec.get("copy_size_usd") or 0.0),
                    "entry_price": prev_rec.get("entry_price"),
                }

            # Exits: emit close ProposedOrder (if we held a copy). Drop from snapshot.
            # _emit_exit is async now (needs broker.quote for exit price).
            for ticker in removed_tickers:
                prev_rec = prev_snapshot.get(ticker) or {}
                proposal = await self._emit_exit(
                    whale=whale, ticker=ticker, prev_pos=prev_rec,
                    quote_fetcher=trade_tape_fetcher,
                    logger_agent=logger_agent,
                )
                if proposal is not None:
                    proposals.append(proposal)

            self._save_whale_snapshot_raw(whale, new_snapshot)

        self._set_last_poll(now)
        return proposals

    # ── Entry detection (with side inference via trade tape) ─────────

    async def _emit_entry(
        self,
        *,
        whale: str,
        position: WhalePosition,
        since: datetime,
        until: datetime,
        trade_fetcher: TradeTapeFetcher | None,
        logger_agent: Any,
    ) -> ProposedOrder | None:
        side, confidence, entry_price = await self._detect_side(
            ticker=position.market_ticker,
            target_contracts=position.contracts,
            since=since, until=until,
            fetcher=trade_fetcher,
        )
        if confidence == "low" or not side:
            if logger_agent is not None:
                logger_agent.log_event(
                    self.name, "kalshi_copy_entry_skipped_no_side",
                    {"strategy": self.name, "division": self.division,
                     "whale": whale, "ticker": position.market_ticker,
                     "whale_contracts": position.contracts,
                     "reason": "side_detection_low_confidence"},
                )
            return None

        copy_usd = self._size_tier_usd(position.contracts)
        price_str = f" @ ${entry_price:.2f}" if entry_price is not None else ""
        return ProposedOrder(
            strategy=self.name,
            symbol=f"{position.market_ticker}:{side}",
            side="buy",
            qty=copy_usd,
            order_type="market",
            # limit_price = the trade-tape-derived fill price. Down-stream
            # the resolver pairing logic (Fix C) needs this to compute
            # realized PnL when the whale's exit fires; nulling it leaves
            # PnL stuck at 0 forever.
            limit_price=entry_price,
            rationale=(
                f"copy entry: @{whale} opened {position.contracts} contracts "
                f"in {position.market_ticker} ({side.upper()}){price_str}; "
                f"size-detected confidence={confidence}"
            ),
            extra={
                "is_entry": True,
                "outcome": side,
                "ticker": position.market_ticker,
                "whale_handle": whale,
                "whale_position_contracts": position.contracts,
                "whale_position_pnl": position.pnl,
                "whale_entry_price": entry_price,
                "copy_size_usd": copy_usd,
                "side_detection_confidence": confidence,
                "first_seen_iso": until.isoformat(),
                "division": self.division,
            },
        )

    async def _emit_exit(
        self,
        *,
        whale: str,
        ticker: str,
        prev_pos: dict[str, Any],
        quote_fetcher: Any | None,
        logger_agent: Any,
    ) -> ProposedOrder | None:
        # Mirror exit: same ticker, OPPOSITE side from our entry, equal size.
        # The opposite-side close is how Kalshi-style binary markets exit a
        # position without a maker-side limit-cross (you sell YES by buying NO
        # equivalents, or vice versa, depending on liquidity).
        # V1 paper-mode simplification: emit an exit signal with quantity=0
        # marker; downstream paper-execution treats this as a position-close
        # rather than a new open. Live-mode wiring is Phase K5+.
        our_outcome = prev_pos.get("our_side") or ""  # "yes" | "no" we held
        copy_usd = float(prev_pos.get("copy_size_usd", 0.0))
        if not our_outcome or copy_usd <= 0.0:
            # We never opened a copy for this ticker (e.g. side detection
            # skipped). Nothing to close — clean stateless drop.
            return None
        # Exit price priority (Fix 2026-05-14):
        #   1. If market has RESOLVED, the exit value is determined:
        #      $1.00 if our outcome won, $0.00 if it lost, entry-price if void.
        #      Short-duration Kalshi markets (KX*15M crypto bars, sports games)
        #      auto-settle within minutes of the whale closing — broker.quote()
        #      returns $0 on a settled market regardless of which side won,
        #      so falling straight to quote() systematically records every
        #      paired exit as a $0 loss. Use resolution first.
        #   2. If still trading, fall back to current YES-mid (broker.quote
        #      returns YES side; for a NO holding invert with 1-yes_mid).
        #   3. None on both failures → resolver pairing emits a round-trip
        #      with realized_pnl=0 (the pre-2026-05-14 behavior).
        exit_price: float | None = None
        if quote_fetcher is not None and hasattr(quote_fetcher, "get_market_resolution"):
            try:
                res = await quote_fetcher.get_market_resolution(ticker)
                status = (res or {}).get("status")
                if status == "resolved":
                    winner = (res or {}).get("result")
                    exit_price = 1.0 if winner == our_outcome else 0.0
                elif status == "void":
                    # Refund: position closes at the entry price → realized_pnl=0.
                    entry_price = prev_pos.get("entry_price")
                    exit_price = float(entry_price) if entry_price else None
            except Exception as e:
                log.warning(
                    "kalshi_copy_trader: resolution lookup failed for %s: %s", ticker, e,
                )
        if exit_price is None and quote_fetcher is not None and hasattr(quote_fetcher, "quote"):
            try:
                yes_mid = await quote_fetcher.quote(ticker)
                if yes_mid > 0:
                    exit_price = float(yes_mid) if our_outcome == "yes" else float(1.0 - yes_mid)
            except Exception as e:
                log.warning(
                    "kalshi_copy_trader: exit-quote fetch failed for %s: %s", ticker, e,
                )
        price_str = f" @ ${exit_price:.2f}" if exit_price is not None else ""
        return ProposedOrder(
            strategy=self.name,
            symbol=f"{ticker}:{our_outcome}",
            side="sell",
            qty=copy_usd,
            order_type="market",
            limit_price=exit_price,
            rationale=(
                f"copy exit: @{whale} closed position in {ticker}; "
                f"selling our {our_outcome.upper()} copy{price_str}"
            ),
            extra={
                "is_entry": False,
                "outcome": our_outcome,
                "ticker": ticker,
                "whale_handle": whale,
                "whale_position_contracts": 0,
                "whale_exit_price": exit_price,
                # Carry the original entry price through so the resolver's
                # pairing pass doesn't need to re-join on the entry audit
                # row to compute realized PnL.
                "whale_entry_price": prev_pos.get("entry_price"),
                "copy_size_usd": copy_usd,
                "side_detection_confidence": "n/a",
                "first_seen_iso": prev_pos.get("first_seen_iso", ""),
                "division": self.division,
            },
        )

    async def _detect_side(
        self,
        *,
        ticker: str,
        target_contracts: int,
        since: datetime,
        until: datetime,
        fetcher: TradeTapeFetcher | None,
    ) -> tuple[str, str, float | None]:
        """Side inference via Kalshi public trade tape size-match.
        Returns (side_str, confidence, price) where:
          - side_str    in {"yes", "no", ""}
          - confidence  in {"high", "medium", "low"}
          - price       the matched trade's per-contract price in dollars
                        ($0.00-$1.00) for the inferred side, or None when
                        no match (low confidence).
        Price comes from the matched trade itself — same row that resolved
        the side — so dashboard ENTRY column reflects the real fill, not
        a separate quote that could have moved by the time we read it.
        """
        if fetcher is None:
            return ("", "low", None)
        try:
            trades = await fetcher.get_market_trades(ticker, since=since, until=until)
        except Exception as e:
            log.warning("kalshi_copy_trader: trade-tape fetch failed for %s: %s", ticker, e)
            return ("", "low", None)
        if not trades:
            return ("", "low", None)
        tol_pct = float(
            (self._strat_cfg.get("side_detection") or {}).get("size_match_tolerance_pct", 5.0)
        )
        tol_abs = max(1, int(target_contracts * tol_pct / 100.0))
        matches = [t for t in trades if abs(t.count - target_contracts) <= tol_abs]
        if len(matches) == 1:
            picked = matches[0]
            return (picked.taker_side, "high", _trade_price_for_side(picked))
        if len(matches) > 1:
            nearest = min(matches, key=lambda t: abs((until - t.time).total_seconds()))
            return (nearest.taker_side, "medium", _trade_price_for_side(nearest))
        return ("", "low", None)

    # ── Sizing ────────────────────────────────────────────────────────

    def _size_tier_usd(self, contracts: int) -> float:
        sz_cfg = self._strat_cfg.get("sizing") or {}
        bounds = tuple(sz_cfg.get("tier_boundaries_contracts") or _DEFAULT_TIER_BOUNDARIES)
        sizes = tuple(sz_cfg.get("tier_sizes_usd") or _DEFAULT_TIER_SIZES_USD)
        if len(sizes) != len(bounds) + 1:
            return float(_DEFAULT_TIER_SIZES_USD[0])
        for i, b in enumerate(bounds):
            if contracts < b:
                return float(sizes[i])
        return float(sizes[-1])

    # ── Auto-pause filter (2026-05-14 P3) ─────────────────────────────

    def _apply_autopause_filter(
        self, selected: list[str], *, logger_agent: Any,
    ) -> list[str]:
        """Drop whales whose resolved-RT stats trip the auto-pause threshold.

        For each triggered whale: persist updated selected_whales to
        agent_state and emit `kalshi_whale_auto_paused` audit. Runs
        BEFORE the Apify call so we don't pay Apify quota on a
        whale we're about to drop.
        """
        db_path = sqlite_path_from_db_url(self._db_url or "")
        if not db_path:
            return selected

        keep: list[str] = []
        paused: list[tuple[str, dict[str, Any]]] = []
        try:
            with sqlite3.connect(db_path) as conn:
                for whale in selected:
                    triggered, stats = should_autopause(
                        conn,
                        whale_name=whale,
                        table="kalshi_round_trips",
                        name_field="whale_handle",
                        division=self.division,
                    )
                    if triggered:
                        paused.append((whale, stats))
                    else:
                        keep.append(whale)
        except Exception as e:
            log.warning(
                "kalshi_copy_trader: autopause filter errored: %s", e,
            )
            return selected

        if not paused:
            return keep

        try:
            set_agent_state(
                self.name, _AGENT_STATE_SELECTED_WHALES, keep,
                db_url=self._db_url,
            )
        except Exception as e:
            log.error(
                "kalshi_copy_trader: failed to persist auto-paused "
                "selected_whales (will retry next scan): %s", e,
            )
            return selected

        for whale, stats in paused:
            log.warning(
                "kalshi_copy_trader: auto-pausing %s (%d RT, %.1f%% WR, $%.2f)",
                whale, stats["n_resolved"],
                stats["win_rate_pct"] or 0.0, stats["total_realized_pnl"],
            )
            if logger_agent is not None:
                logger_agent.log_event(
                    self.name, "kalshi_whale_auto_paused",
                    {
                        "strategy": self.name,
                        "division": self.division,
                        "whale_handle": whale,
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

    def _load_selected_whales(self) -> list[str]:
        if not self._db_url:
            return []
        rec = load_agent_state(
            self.name, _AGENT_STATE_SELECTED_WHALES, db_url=self._db_url,
        )
        if rec is None:
            return []
        value = rec[0]
        if isinstance(value, list):
            return [str(x) for x in value]
        return []

    def _load_whale_snapshot(self, whale: str) -> dict[str, dict[str, Any]] | None:
        if not self._db_url:
            return None
        rec = load_agent_state(
            self.name, f"{_AGENT_STATE_POSITIONS_PREFIX}{whale}", db_url=self._db_url,
        )
        if rec is None:
            return None
        value = rec[0]
        if isinstance(value, dict):
            return value
        return {}

    def _save_whale_snapshot_raw(
        self, whale: str, snapshot: dict[str, dict[str, Any]],
    ) -> None:
        if not self._db_url:
            return
        set_agent_state(
            self.name, f"{_AGENT_STATE_POSITIONS_PREFIX}{whale}", snapshot,
            db_url=self._db_url,
        )

    def _get_last_poll_iso(self) -> str | None:
        if not self._db_url:
            return None
        rec = load_agent_state(
            self.name, _AGENT_STATE_LAST_POLL_TS, db_url=self._db_url,
        )
        if rec is None:
            return None
        value = rec[0]
        return str(value) if isinstance(value, str) else None

    def _set_last_poll(self, ts: datetime) -> None:
        if not self._db_url:
            return
        set_agent_state(
            self.name, _AGENT_STATE_LAST_POLL_TS, ts.isoformat(), db_url=self._db_url,
        )
