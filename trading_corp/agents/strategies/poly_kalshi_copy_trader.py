"""Phase-1 Poly->Kalshi MLB copy — detection loop (SHADOW-capable, CP4).

Polls the discovered MLB whales' Polymarket activity on a fast cadence, runs each
NEW action through the CP1 matcher -> CP2 order -> CP3 guardrails -> a shadow log.
`dry_run` executor by default, so this NEVER places. NOT wired to main.py/config
(that's CP5); importing this module has no side effects.

Incremental detection (offset-5000-cap safe): each poll fetches only the newest
page (`offset=0`, small limit) and emits rows with `timestamp` beyond the per-whale
high-water mark. We never deep-page toward the 5000 offset — new actions between
polls are always on page 0, so the cap is irrelevant. Cold start seeds the
high-water mark without emitting (don't copy history on boot).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from trading_corp.data.polymarket_data_api_client import (
    PolymarketDataAPIError, PolymarketRateLimitError,
)
from trading_corp.data.mlb_poly_kalshi_match import match_poly_to_kalshi, parse_poly_mlb_bet
from trading_corp.agents.strategies.poly_kalshi_executor import translate_whale_action
from trading_corp.persistence.models import StrategyState

log = logging.getLogger(__name__)
_BACKOFF_SCHEDULE = (10, 20, 40, 60)   # seconds per 429 retry


def _utc_day(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts else time.time(), tz=timezone.utc).strftime("%Y-%m-%d")


class PolyKalshiCopyTrader:
    def __init__(self, *, executor, poll_interval_sec: float = 7.0,
                 activity_limit: int = 50, stake_usd: float = 5.00,
                 quote_fn=None, day_key_fn=_utc_day, daily_loss_cap_usd: float | None = 100.0,
                 db_url: str = "sqlite:///data/trading_corp.db",
                 roster_actor: str = "polymarket_copy_trader", roster_key: str = "selected_whales",
                 now_fn=time.time):
        # Trigger roster is read from agent_state(selected_whales) each cycle — NO
        # hardcoded whale dict. Idempotency keys on wallet (name is display-only).
        self._executor = executor             # PolyKalshiExecutor (dry_run for shadow)
        self._poll = float(poll_interval_sec)
        self._limit = int(activity_limit)
        self._stake = float(stake_usd)        # CP5 operator gate: $5/trade fixed
        self._quote_fn = quote_fn             # async (ticker) -> {yes_ask,yes_bid} | None
        self._day_key_fn = day_key_fn
        self._daily_loss_cap_usd = daily_loss_cap_usd   # CP5 operator gate: $100 realized loss/day
        self._db_url = db_url
        self._roster_actor = roster_actor
        self._roster_key = roster_key
        self._now = now_fn
        self._last_seen_ts: dict[str, int] = {}   # wallet -> high-water timestamp
        self._kidx: dict = {}
        self._kdates = frozenset()
        self._day_key = None
        self._realized_pnl_day = 0.0
        self.shadow_log: list[dict] = []
        self.backoff_events: list[dict] = []
        self.poll_count = 0

    def set_kalshi_index(self, index: dict, dates) -> None:
        self._kidx, self._kdates = index, frozenset(dates)

    def _load_roster(self) -> list[tuple[str, str]]:
        """Read the trigger roster from agent_state(selected_whales), fresh each
        cycle (mirrors the legacy loop's per-cycle reload). Returns
        [(user_name, wallet), ...]. Tolerates the rich dict form and a bare
        wallet-string list. Idempotency keys on wallet downstream."""
        from trading_corp.persistence.db import load_agent_state
        rec = load_agent_state(self._roster_actor, self._roster_key, db_url=self._db_url)
        if not rec:
            return []
        value = rec[0]
        out: list[tuple[str, str]] = []
        if isinstance(value, list):
            for v in value:
                if isinstance(v, dict) and v.get("wallet"):
                    out.append((str(v.get("user_name") or ""), str(v["wallet"])))
                elif isinstance(v, str):
                    out.append(("", v))
        return out

    # ── day-rollover: reset the in-memory daily counter at the UTC boundary ──
    def _rollover_if_needed(self) -> bool:
        k = self._day_key_fn()
        if self._day_key is None:      # boot: initialize, do NOT reset (counter starts 0)
            self._day_key = k
            return False
        if k != self._day_key:         # genuine day change -> reset the in-memory counter
            self._day_key = k
            self._executor._deployed_usd = 0.0    # [G-daily] counter reset
            self._realized_pnl_day = 0.0
            return True
        return False

    # ── [G-halt] daily-loss DETECTION: compute realized loss, call persist_halt ──
    def record_realized(self, delta_usd: float) -> bool:
        """Accrue realized P&L; when the day's loss breaches the cap, call the SAME
        StrategyState.persist_halt the other divisions use. Returns True if it halted."""
        self._realized_pnl_day += float(delta_usd)
        if (self._daily_loss_cap_usd is not None
                and self._realized_pnl_day <= -abs(self._daily_loss_cap_usd)):
            StrategyState.persist_halt(
                self._executor._strategy,
                f"daily-loss auto-halt: realized {self._realized_pnl_day:.2f} "
                f"<= -{abs(self._daily_loss_cap_usd):.2f}",
                db_url=self._executor._db_url,
            )
            return True
        return False

    async def _fetch(self, client, wallet: str) -> list:
        """Newest page only (offset=0), with 429/Cloudflare backoff. Returns [] on
        give-up/error rather than crashing the loop."""
        for attempt in range(len(_BACKOFF_SCHEDULE) + 1):
            try:
                return await client.fetch_activity(wallet, limit=self._limit, offset=0)
            except PolymarketRateLimitError:
                if attempt == len(_BACKOFF_SCHEDULE):
                    self.backoff_events.append({"wallet": wallet[:10], "gave_up": True, "t": self._now()})
                    return []
                sleep_s = _BACKOFF_SCHEDULE[attempt]
                self.backoff_events.append({"wallet": wallet[:10], "attempt": attempt,
                                            "sleep": sleep_s, "t": self._now()})
                await asyncio.sleep(sleep_s)
            except PolymarketDataAPIError as e:
                log.warning("poly fetch %s failed: %s", wallet[:10], e)
                return []
        return []

    async def _pipeline(self, name: str, wallet: str, r, detected_ts: float, *, backlog: bool = False) -> dict:
        p = parse_poly_mlb_bet(r.slug, r.outcome or "", r.title or "", r.event_slug or "")
        e = {"seen_ts": round(detected_ts, 1), "action_ts": r.timestamp,
             "latency_s": round(detected_ts - r.timestamp, 1), "backlog": backlog, "whale": name,
             "side": r.side, "slug": r.slug, "outcome": r.outcome,
             "tx": (r.transaction_hash or "")[:18], "market_type": p.market_type}
        if p.market_type != "moneyline":
            e.update(stage="skip_non_ml", decision="no_order")
            self.shadow_log.append(e); return e
        m = match_poly_to_kalshi(p, self._kidx, self._kdates)
        e.update(match_status=m.status, confidence=m.confidence, kalshi_ticker=m.kalshi_ticker)
        if m.status != "matched":
            e.update(stage="no_match", decision="no_order")
            self.shadow_log.append(e); return e
        if not (0.0 < float(r.price) < 1.0):
            e.update(stage="bad_price", decision="no_order")
            self.shadow_log.append(e); return e
        order = translate_whale_action(
            whale=name, whale_wallet=wallet, kalshi_ticker=m.kalshi_ticker, confidence=m.confidence,
            whale_side=r.side, base_price=float(r.price), stake_usd=self._stake)
        quote = None
        if self._quote_fn is not None:
            try:
                quote = await self._quote_fn(order.ticker)
            except Exception as ex:  # noqa: BLE001 — fetch failure -> None -> [G-slip] fail-closed (live)
                log.warning("quote fetch failed for %s: %s", order.ticker, ex)
                quote = None
        res = await self._executor.submit(order, market_quote=quote)
        e.update(stage="submitted", quote=quote, gate=res["status"], decision=res["status"],
                 order={"ticker": order.ticker, "v2_side": order.v2_side, "action": order.action,
                        "count": order.count, "limit_price": order.body["price"],
                        "idempotency_key": order.idempotency_key})
        self.shadow_log.append(e); return e

    async def poll_cycle(self, client, *, emit_backlog: bool = False, backlog_n: int = 0) -> list:
        self._rollover_if_needed()
        self.poll_count += 1
        out = []
        for name, wallet in self._load_roster():   # roster reloaded from selected_whales each cycle
            rows = await self._fetch(client, wallet)
            if not rows:
                continue
            newest = max(r.timestamp for r in rows)
            trades = [r for r in rows if r.type == "TRADE" and r.side in ("BUY", "SELL")]
            last = self._last_seen_ts.get(wallet)
            if last is None:                       # cold start — seed, don't emit history
                self._last_seen_ts[wallet] = newest
                if emit_backlog and backlog_n:
                    for r in sorted(trades, key=lambda x: x.timestamp)[-backlog_n:]:
                        out.append(await self._pipeline(name, wallet, r, self._now(), backlog=True))
                continue
            for r in sorted((t for t in trades if t.timestamp > last), key=lambda x: x.timestamp):
                out.append(await self._pipeline(name, wallet, r, self._now()))
            self._last_seen_ts[wallet] = max(newest, last)
        return out

    async def run_for(self, seconds: float, *, client, emit_backlog: bool = False, backlog_n: int = 0):
        start = self._now()
        first = True
        while self._now() - start < seconds:
            try:
                await self.poll_cycle(client, emit_backlog=(emit_backlog and first), backlog_n=backlog_n)
            except Exception as ex:  # noqa: BLE001 — a bad cycle must not kill the loop
                log.warning("poll_cycle error (continuing): %s", ex)
            first = False
            await asyncio.sleep(self._poll)
