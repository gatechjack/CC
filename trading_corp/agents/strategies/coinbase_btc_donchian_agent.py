"""Coinbase BTC Donchian — poll-driven swing-trade agent.

Trigger model differs from Lord Otter / Market Cypher: those are
TradingView-webhook-driven (alerts arrive); this is poll-driven —
the orchestrator checks for a new 6h-bar close and calls
`on_bar_close()`. Closer to the PMCC scheduled-scan pattern.

Strategy spec is locked in `config/strategies.yaml` under the
`coinbase_btc_donchian` key (see deploy_log.md commit 0eb7692).
The decision math itself lives in `donchian_btc.py` — this module
is the "agent class" wrapper that:

  - Reads the YAML config (hot-reloadable via mtime).
  - Persists CASH↔BTC state + cost_basis across process restarts
    via the `agent_state` table.
  - Reconciles state from a broker snapshot on startup
    (`restore_from_broker`) — per Board direction, if we boot
    holding BTC, cost_basis is seeded to the current market price
    (no historical-entry tracking).
  - Emits `ProposedOrder` instances for the rest of the system to
    risk-gate, HITL-approve, and place.

Public API:
  agent = CoinbaseBTCDonchianAgent(strategies_yaml=..., db_url=...)
  await agent.restore_from_broker(broker)
  order, reason = agent.on_bar_close(
      bars, account_equity=cash_or_equity_usd, held_btc=held_qty_btc,
  )

Phase 1 status: agent class is built, NOT yet wired into main.py
or webhooks. Risk-gate per-strategy overrides are NOT yet in
risk.yaml. `enabled: false` in strategies.yaml. This module is
unit-test ready; production wiring lands in the next session.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from trading_corp.agents.strategies.donchian_btc import (
    Decision,
    DonchianConfig,
    State,
    evaluate_donchian,
)
from trading_corp.persistence.db import (
    delete_agent_state,
    load_agent_state,
    set_agent_state,
)
from trading_corp.persistence.models import ProposedOrder

log = logging.getLogger(__name__)


# ── Persistence ────────────────────────────────────────────────────


_STATE_KEY = "state"           # value: {"state": "cash"|"btc", "cost_basis": float|None}
_LAST_BAR_KEY = "last_bar_ts"  # ISO ts of the last bar we evaluated — dedup guard


@dataclass
class PersistedState:
    state: State
    cost_basis: float | None
    last_bar_ts: datetime | None

    def to_value(self) -> dict:
        return {
            "state": self.state.value,
            "cost_basis": self.cost_basis,
        }

    @classmethod
    def cash_default(cls) -> PersistedState:
        return cls(state=State.CASH, cost_basis=None, last_bar_ts=None)


# ── Agent ──────────────────────────────────────────────────────────


class CoinbaseBTCDonchianAgent:
    """One agent instance handles the BTC accumulator on coinbase_spot.

    Single instrument, single decision type per call. The orchestrator
    is responsible for:
      - Detecting a new 6h-bar close (timestamp boundary check) and
        calling `on_bar_close()` exactly once per new bar.
      - Supplying the rolling OHLCV window (most recent N bars,
        chronological, ending with the bar just closed).
      - Reading the broker snapshot for `account_equity` + `held_btc`.
      - Routing the returned ProposedOrder through the risk gate +
        HITL approval pipeline (existing infrastructure, unchanged).
    """

    name = "coinbase_btc_donchian"

    # If a persisted state's last_bar_ts is older than this on
    # restart, we discard the persisted state and re-derive from
    # the broker snapshot. Protects against a bug or stale-process
    # crash where state in DB diverged from real broker holdings.
    STATE_MAX_AGE = timedelta(days=7)

    def __init__(
        self,
        strategies_yaml: Path = Path("config/strategies.yaml"),
        db_url: str | None = None,
    ) -> None:
        """Construct.

        `db_url=None` disables persistence (used by tests + ad-hoc
        scripts). Production wires the real DB path so state survives
        process restarts.
        """
        self._strategies_yaml = Path(strategies_yaml)
        self._db_url = db_url
        self._mtime: float = 0.0
        self._cfg: dict[str, Any] = {}
        self._state: PersistedState = PersistedState.cash_default()
        # Most recent DonchianVerdict (set inside on_bar_close after the
        # decision module runs). The orchestrator reads this to write the
        # `donchian_evaluated` audit row with the channel highs/lows even
        # on SKIP decisions, where no ProposedOrder is emitted.
        self._last_verdict: Any = None
        self._reload()
        if self._db_url:
            self._restore_from_db()

    # -- Config loading (hot-reloadable) -----------------------------

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
            log.warning("CoinbaseBTCDonchianAgent: failed to load %s: %s",
                        self._strategies_yaml, e)
            return
        self._cfg = data.get("coinbase_btc_donchian", {}) or {}
        self._mtime = mtime
        log.info(
            "CoinbaseBTCDonchianAgent reloaded: enabled=%s auto_execute=%s "
            "entry=%s exit=%s trend_filter=%s granularity=%s",
            self._cfg.get("enabled"),
            self._cfg.get("auto_execute"),
            self._donchian_param("entry_lookback"),
            self._donchian_param("exit_lookback"),
            self._donchian_param("trend_filter_lookback"),
            self._donchian_param("granularity_seconds"),
        )

    def _donchian_param(self, key: str, default: Any = None) -> Any:
        return (self._cfg.get("donchian") or {}).get(key, default)

    def _donchian_config(self) -> DonchianConfig:
        return DonchianConfig(
            entry_lookback=int(self._donchian_param("entry_lookback", 20)),
            exit_lookback=int(self._donchian_param("exit_lookback", 6)),
            trend_filter_lookback=(
                int(self._donchian_param("trend_filter_lookback"))
                if self._donchian_param("trend_filter_lookback") is not None
                else None
            ),
            granularity_seconds=int(self._donchian_param("granularity_seconds", 21600)),
        )

    # -- Convenience accessors ---------------------------------------

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
    def symbol(self) -> str:
        self._reload()
        return str(self._cfg.get("symbol", "BTC/USD"))

    @property
    def log_skip_decisions(self) -> bool:
        self._reload()
        return bool((self._cfg.get("audit") or {}).get("log_skip_decisions", True))

    def get_state(self) -> tuple[State, float | None]:
        """Current (state, cost_basis). cost_basis is None when in CASH."""
        return self._state.state, self._state.cost_basis

    @property
    def last_verdict(self):
        """Most recent DonchianVerdict (or None if no bar has been
        evaluated yet, or the agent short-circuited on disabled / no-bars
        / dedup before evaluate_donchian ran). The orchestrator reads
        this to compose the per-bar `donchian_evaluated` audit row."""
        return self._last_verdict

    # -- State persistence ------------------------------------------

    def _restore_from_db(self) -> None:
        """Load persisted state on startup. Stale entries (older than
        STATE_MAX_AGE) are deleted and we fall back to defaults — the
        orchestrator should call `restore_from_broker()` after this to
        reconcile against actual broker holdings."""
        if not self._db_url:
            return
        row = load_agent_state(self.name, _STATE_KEY, db_url=self._db_url)
        if row is None:
            log.info("CoinbaseBTCDonchianAgent: no persisted state; defaulting to CASH")
            return
        value, updated_ts = row
        age = datetime.now(timezone.utc) - updated_ts
        if age > self.STATE_MAX_AGE:
            log.warning(
                "CoinbaseBTCDonchianAgent: persisted state is %s old (>%s); "
                "deleting — caller should reconcile via restore_from_broker()",
                age, self.STATE_MAX_AGE,
            )
            delete_agent_state(self.name, _STATE_KEY, db_url=self._db_url)
            delete_agent_state(self.name, _LAST_BAR_KEY, db_url=self._db_url)
            return
        try:
            state = State(value.get("state", "cash"))
        except ValueError:
            log.warning("CoinbaseBTCDonchianAgent: corrupt state value %r; defaulting", value)
            return
        cost_basis = value.get("cost_basis")
        last_bar_row = load_agent_state(self.name, _LAST_BAR_KEY, db_url=self._db_url)
        last_bar_ts: datetime | None = None
        if last_bar_row is not None:
            last_bar_value, _ = last_bar_row
            try:
                last_bar_ts = datetime.fromisoformat(last_bar_value.get("ts"))
            except (TypeError, ValueError):
                last_bar_ts = None
        self._state = PersistedState(
            state=state,
            cost_basis=float(cost_basis) if cost_basis is not None else None,
            last_bar_ts=last_bar_ts,
        )
        log.info(
            "CoinbaseBTCDonchianAgent: restored state=%s cost_basis=%s last_bar=%s",
            self._state.state.value, self._state.cost_basis, self._state.last_bar_ts,
        )

    def _persist_state(self, last_bar_ts: datetime | None = None) -> None:
        if not self._db_url:
            return
        set_agent_state(
            self.name, _STATE_KEY, self._state.to_value(), db_url=self._db_url,
        )
        if last_bar_ts is not None:
            set_agent_state(
                self.name, _LAST_BAR_KEY,
                {"ts": last_bar_ts.isoformat()},
                db_url=self._db_url,
            )
            self._state.last_bar_ts = last_bar_ts

    # -- Startup reconciliation -------------------------------------

    def restore_from_broker(
        self, *, account_equity: float, held_btc: float, current_price: float,
    ) -> None:
        """Reconcile in-memory state with what the broker actually
        reports. Per Board direction (chat 2026-05-08): if we boot
        holding BTC, cost_basis is seeded to the CURRENT market
        price — we don't try to track historical entry across
        restarts. Subsequent sells benchmark vs that price.

        Args:
            account_equity: total USD equity from broker snapshot
                (cash + held_btc × current_price).
            held_btc: BTC quantity from broker snapshot. 0.0 means
                we're in CASH.
            current_price: BTC/USD spot at the moment of reconciliation.
        """
        # Threshold: anything below this is treated as dust/rounding,
        # NOT a real position. Protects against tiny leftover BTC from
        # imperfect fills that would otherwise pin us in BTC state.
        DUST_USD_THRESHOLD = 1.0
        held_value_usd = held_btc * current_price
        if held_value_usd > DUST_USD_THRESHOLD:
            self._state = PersistedState(
                state=State.BTC,
                cost_basis=current_price,
                last_bar_ts=self._state.last_bar_ts,
            )
            log.info(
                "CoinbaseBTCDonchianAgent: reconciled to BTC state — "
                "held=%.8f BTC ($%.2f) @ cost_basis=$%.2f",
                held_btc, held_value_usd, current_price,
            )
        else:
            self._state = PersistedState(
                state=State.CASH,
                cost_basis=None,
                last_bar_ts=self._state.last_bar_ts,
            )
            log.info(
                "CoinbaseBTCDonchianAgent: reconciled to CASH state — "
                "held=%.8f BTC < $%.2f dust threshold",
                held_btc, DUST_USD_THRESHOLD,
            )
        self._persist_state()

    # -- Main entry point -------------------------------------------

    def on_bar_close(
        self,
        bars: list[dict],
        *,
        account_equity: float,
        held_btc: float,
    ) -> tuple[ProposedOrder | None, str]:
        """Evaluate one bar-close. Caller MUST supply chronologically-
        sorted OHLCV bars ending with the bar just closed (no future
        bars; the donchian engine has its own look-ahead guard but
        feeding it future data would still be a caller bug).

        Args:
            bars: list of {ts: datetime, open, high, low, close, volume}.
                Length must be ≥ max(entry_lookback, exit_lookback,
                trend_filter_lookback or 0) + 1, else returns SKIP.
            account_equity: total USD equity (cash + BTC value).
                Used to size BUYs (full equity → BTC).
            held_btc: BTC qty held. Used to size SELLs (close 100%
                of position).

        Returns (ProposedOrder | None, reason). The reason string is
        always populated so the caller can audit-log it whether or
        not an order was emitted. None = no action (skip / disabled
        / duplicate-bar).
        """
        self._reload()
        if not self.enabled:
            return None, "coinbase_btc_donchian disabled in config"
        if not bars:
            return None, "no bars supplied"

        # Dedup: if last_bar_ts == current bar's ts, we already
        # evaluated this bar (e.g. orchestrator double-call). Skip.
        current_bar = bars[-1]
        current_ts = current_bar["ts"]
        if (
            self._state.last_bar_ts is not None
            and current_ts == self._state.last_bar_ts
        ):
            return None, f"already evaluated bar {current_ts.isoformat()}"

        config = self._donchian_config()
        verdict = evaluate_donchian(
            state=self._state.state,
            bars_window=bars,
            config=config,
            now=current_ts,
        )
        # Stash for orchestrator audit-row write (see __init__ comment).
        self._last_verdict = verdict

        # Persist the bar timestamp regardless — even SKIP decisions
        # advance the dedup pointer so we don't re-evaluate.
        self._state.last_bar_ts = current_ts
        self._persist_state(last_bar_ts=current_ts)

        if verdict.decision == Decision.SKIP:
            return None, verdict.reason

        # Build ProposedOrder
        current_close = current_bar["close"]
        if verdict.decision == Decision.BUY:
            if account_equity <= 0:
                return None, f"buy fired but account_equity={account_equity} ≤ 0"
            qty = account_equity / current_close
            order = ProposedOrder(
                strategy=self.name,
                symbol=self.symbol,
                side="buy",
                qty=qty,
                order_type="market",
                limit_price=current_close,    # for risk-gate notional math
                rationale=verdict.reason,
                extra={
                    "asset_type": "crypto",
                    "underlying": self.symbol,
                    "decision": verdict.decision.value,
                    "donchian_high": verdict.breakdown.donchian_high,
                    "donchian_low": verdict.breakdown.donchian_low,
                    "trend_filter_sma": verdict.breakdown.trend_filter_sma,
                    "trend_filter_passed": verdict.breakdown.trend_filter_passed,
                    "current_close": verdict.breakdown.current_close,
                    "config": {
                        "entry_lookback": config.entry_lookback,
                        "exit_lookback": config.exit_lookback,
                        "trend_filter_lookback": config.trend_filter_lookback,
                        "granularity_seconds": config.granularity_seconds,
                    },
                },
            )
            # State flip happens AFTER the order is filled (caller's
            # job to call `mark_filled()`). We don't pre-flip here
            # because risk gate or HITL might still reject.
            return order, verdict.reason

        # decision == SELL
        if held_btc <= 0:
            return None, f"sell fired but held_btc={held_btc} ≤ 0 — broker drift?"
        order = ProposedOrder(
            strategy=self.name,
            symbol=self.symbol,
            side="sell",
            qty=held_btc,
            order_type="market",
            limit_price=current_close,
            rationale=verdict.reason,
            extra={
                "asset_type": "crypto",
                "underlying": self.symbol,
                "decision": verdict.decision.value,
                "donchian_high": verdict.breakdown.donchian_high,
                "donchian_low": verdict.breakdown.donchian_low,
                "trend_filter_sma": verdict.breakdown.trend_filter_sma,
                "trend_filter_passed": verdict.breakdown.trend_filter_passed,
                "current_close": verdict.breakdown.current_close,
                "cost_basis": self._state.cost_basis,
                "realized_pnl_estimate": (
                    (current_close - (self._state.cost_basis or current_close))
                    * held_btc
                ),
                "config": {
                    "entry_lookback": config.entry_lookback,
                    "exit_lookback": config.exit_lookback,
                    "trend_filter_lookback": config.trend_filter_lookback,
                    "granularity_seconds": config.granularity_seconds,
                },
            },
        )
        return order, verdict.reason

    # -- Post-fill state transition ---------------------------------

    def mark_filled(self, *, side: str, fill_price: float) -> None:
        """Caller invokes this AFTER a ProposedOrder has actually been
        filled by the broker (or paper-broker). Updates internal state
        and persists.

        BUY → state=BTC, cost_basis=fill_price.
        SELL → state=CASH, cost_basis=None.

        Per Board direction: cost_basis on the next round-trip
        benchmarks against the prior sell's price (or, on startup
        reconciliation, the current market price). Historical entry
        is NOT preserved across sells — each round-trip is its own
        unit.
        """
        if side == "buy":
            self._state.state = State.BTC
            self._state.cost_basis = float(fill_price)
            log.info(
                "CoinbaseBTCDonchianAgent: filled BUY @ $%.2f → state=BTC",
                fill_price,
            )
        elif side == "sell":
            self._state.state = State.CASH
            self._state.cost_basis = None
            log.info(
                "CoinbaseBTCDonchianAgent: filled SELL @ $%.2f → state=CASH",
                fill_price,
            )
        else:
            log.warning(
                "CoinbaseBTCDonchianAgent: mark_filled with unknown side=%s; ignoring",
                side,
            )
            return
        self._persist_state()
