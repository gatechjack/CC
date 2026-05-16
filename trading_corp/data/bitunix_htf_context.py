"""HTF context provider for the BitUnix Futures division.

Impure boundary that takes three `LiveBarCache` instances (1H / 4H / 1D)
plus a `BitunixBroker` reference, and produces a typed `HTFContext` the
pure-function HTF regime classifier consumes.

This is the I/O side of the design. The pure side
(`agents/strategies/bitunix_htf_regime.py`) only knows about typed
inputs; this module is what pulls them out of caches and the broker.

Design points:
  - Bar caches feed in directly. Their poll loops are owned by `main.py`
    (so cache lifecycle stays in one place); this provider just READS.
  - Funding rate is fetched on its own poll loop because it changes
    every 8h and needs only one HTTP call. Cached value is kept on the
    provider; the sync `snapshot()` reads the cached value.
  - `snapshot()` is sync (returns immediately from cached state). This
    keeps the dashboard view-builder synchronous and the observer's
    score path non-blocking.
  - Bar staleness is checked per-cache: a cache that hasn't refreshed
    in `staleness_threshold_seconds` is treated as "missing" — the
    classifier sees `None` for that timeframe and contributes 0 to
    composite (or SAFE_MODE if all three are stale).
  - Funding rate freshness: if no successful fetch in
    `funding_max_age_seconds`, the cached value is dropped to None
    (HTF gate sees "unknown funding" and skips the funding-extreme
    override but doesn't fail-closed — the regime gate's other hard-
    zero checks still apply).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from trading_corp.agents.strategies.bitunix_htf_regime import (
    HTFContext,
    HTFRegimeConfig,
    RegimeVerdict,
    TimeframeBars,
    compute_regime,
)
from trading_corp.brokers.bitunix import BitunixBroker
from trading_corp.data.live_bar_cache import LiveBarCache
from trading_corp.persistence import db

log = logging.getLogger(__name__)


# PR 5b — funding-rate history table.
FUNDING_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS bitunix_funding_history (
    ts           TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    rate         REAL NOT NULL,
    PRIMARY KEY (ts, symbol)
);
"""
FUNDING_HISTORY_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS bitunix_funding_history_symbol_ts_idx "
    "ON bitunix_funding_history(symbol, ts)"
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

# Default per-TF staleness thresholds (seconds). Any cache that hasn't
# refreshed within this window of wall-clock time is treated as missing.
# Sized at ~2x the natural bar-close cadence so a single missed poll
# tick doesn't spuriously trip SAFE_MODE.
_DEFAULT_STALENESS = {
    "1h": 7200,        # 2 hours
    "4h": 28800,       # 8 hours
    "1d": 172800,      # 2 days
}

# Funding rate considered fresh for this many seconds after fetch.
# BitUnix funding rate updates every 8h (28800s); 12h gives 50% buffer.
_FUNDING_MAX_AGE_S = 43200       # 12h


@dataclass
class BitUnixHTFContextProvider:
    """Holds caches + broker; produces HTFContext snapshots for the
    pure HTF regime classifier.

    Caches are owned externally (main.py builds them and runs their
    poll loops). This provider is a *view* over them plus a thin
    funding-rate fetcher.

    PR 5b/5c additions: when `db_url` is set, the provider also
    persists every successful funding-rate fetch to
    `bitunix_funding_history` and (via the new
    `run_regime_snapshot_loop`) writes periodic `htf_regime_snapshot`
    audit rows so we have a continuous regime time series outside of
    fire moments. Both behaviors are no-ops when `db_url` is None
    (test envs / pre-PR-5 wiring).
    """
    h1_cache: LiveBarCache
    h4_cache: LiveBarCache
    d1_cache: LiveBarCache
    broker: BitunixBroker
    symbol: str = "BTCUSDT"           # for the funding fetch
    db_url: str | None = None         # PR 5b/5c — when set, enables persistence
    staleness_thresholds: dict[str, int] = field(
        default_factory=lambda: dict(_DEFAULT_STALENESS),
    )
    funding_max_age_seconds: int = _FUNDING_MAX_AGE_S

    _last_funding_rate: float | None = None
    _last_funding_fetch_monotonic: float | None = None
    _schema_ready: bool = False

    def _ensure_schema(self) -> None:
        if self._schema_ready or not self.db_url:
            return
        try:
            with db.connect(self.db_url) as conn:
                conn.execute(FUNDING_HISTORY_DDL)
                conn.execute(FUNDING_HISTORY_INDEX_DDL)
            self._schema_ready = True
        except Exception as e:
            log.warning("HTF context: funding-history schema init failed: %s", e)

    # ── funding rate poll ────────────────────────────────────────────

    async def refresh_funding_rate(self) -> float | None:
        """One-shot funding-rate refresh. Called by `run_funding_poll_loop`
        and once at startup for warmup. Returns the new value (or the
        last-known cached value if this fetch failed).

        PR 5b: when `db_url` is set, every SUCCESSFUL fetch also writes
        a row to `bitunix_funding_history` (PRIMARY KEY (ts, symbol),
        so duplicate-second fetches collapse cleanly via INSERT OR IGNORE).
        """
        try:
            rate = await self.broker.get_funding_rate(self.symbol)
        except Exception as e:
            log.warning("HTF context: funding refresh raised: %s", e)
            return self._last_funding_rate
        if rate is not None:
            self._last_funding_rate = rate
            self._last_funding_fetch_monotonic = time.monotonic()
            if self.db_url:
                self._persist_funding_rate(rate)
        return self._last_funding_rate

    def _persist_funding_rate(self, rate: float) -> None:
        """PR 5b — append the latest funding observation to history."""
        self._ensure_schema()
        if not self._schema_ready:
            return
        try:
            with db.connect(self.db_url) as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO bitunix_funding_history "
                    "(ts, symbol, rate) VALUES (?, ?, ?)",
                    (_utc_now_iso(), self.symbol, float(rate)),
                )
        except Exception as e:
            log.warning("HTF context: funding-history write failed: %s", e)

    async def run_funding_poll_loop(self, interval_s: float = 1800.0) -> None:
        """Background task: refresh funding rate every `interval_s`
        seconds. BitUnix funding rate updates every 8h so 30 min is
        well-overpolled — keeps the cached value reliably warm.
        """
        log.info(
            "HTF funding-rate poll online (symbol=%s, interval=%ss)",
            self.symbol, interval_s,
        )
        try:
            while True:
                await self.refresh_funding_rate()
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            log.info("HTF funding-rate poll cancelled")
            raise

    # ── PR 5c — continuous regime snapshot loop ────────────────────

    async def run_regime_snapshot_loop(
        self, config: HTFRegimeConfig, interval_s: float = 600.0,
    ) -> None:
        """Background task: every `interval_s` seconds, snapshot the
        current HTF regime + write `audit_event(kind='htf_regime_snapshot')`.

        Distinct from `htf_gate_decision` — that audit fires only when
        a score-engine decision is being gated. This loop runs
        unconditionally so we have a continuous time series for tuning
        the classifier itself ("would different EMA periods have
        changed how often we landed in NEUTRAL?").

        Default 10-min cadence = ~144 rows/day. Trivial volume.

        No-op when `db_url` is None.
        """
        if not self.db_url:
            log.info(
                "HTF regime-snapshot loop SKIPPED — no db_url configured"
            )
            return
        log.info(
            "HTF regime-snapshot loop online (interval=%ss)", interval_s,
        )
        try:
            while True:
                try:
                    self._snapshot_regime_to_audit(config)
                except Exception as e:
                    log.warning("HTF regime snapshot tick raised: %s", e)
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            log.info("HTF regime-snapshot loop cancelled")
            raise

    def _snapshot_regime_to_audit(self, config: HTFRegimeConfig) -> None:
        """Compute current regime + write a non-gating audit row.

        Schema mirrors `htf_gate_decision` minus the `permission` /
        `score_*` / `mode` fields (no decision being gated here)."""
        verdict = self.regime_snapshot(config)

        def _tf_summary(tf_class) -> dict:
            return {
                "regime": tf_class.regime.value,
                "ema_alignment": tf_class.ema_alignment,
                "structure": tf_class.structure,
                "adx": tf_class.adx,
                "macd_hist": tf_class.macd_hist,
            }

        payload = {
            "strategy": "bitunix_futures",
            "division": "bitunix_futures",
            "regime": verdict.regime.value,
            "composite_score": round(verdict.score, 3),
            "h1": _tf_summary(verdict.h1),
            "h4": _tf_summary(verdict.h4),
            "d1": _tf_summary(verdict.d1),
            "volatility_tier": verdict.volatility_tier.value,
            "atr_pct_d1": verdict.atr_pct_d1,
            "distance_to_resistance_pct": verdict.distance_to_resistance_pct,
            "distance_to_support_pct": verdict.distance_to_support_pct,
            "session": verdict.session.value,
            "funding_rate": verdict.funding_rate,
            "funding_extreme": verdict.funding_extreme,
            "safe_mode_reason": verdict.safe_mode_reason,
            # Bar pointers (PR 5f — for joining to bitunix_bar_history).
            # Capture the most-recent CLOSED bar's ts per TF; None when
            # the cache is empty (e.g. cold start).
            "bar_h1_last_close_ms": (
                self.h1_cache.bars[-1].ts_ms if self.h1_cache.bars else None
            ),
            "bar_h4_last_close_ms": (
                self.h4_cache.bars[-1].ts_ms if self.h4_cache.bars else None
            ),
            "bar_d1_last_close_ms": (
                self.d1_cache.bars[-1].ts_ms if self.d1_cache.bars else None
            ),
        }
        try:
            with db.connect(self.db_url) as conn:
                conn.execute(
                    "INSERT INTO audit_event (ts, actor, kind, payload_json) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        _utc_now_iso(),
                        "bitunix_futures",
                        "htf_regime_snapshot",
                        json.dumps(payload, default=str),
                    ),
                )
        except Exception as e:
            log.warning("HTF context: regime snapshot write failed: %s", e)

    # ── snapshot construction ────────────────────────────────────────

    def _cache_to_bars(
        self, cache: LiveBarCache, tf_label: str,
    ) -> TimeframeBars | None:
        """Convert a LiveBarCache's bars to a TimeframeBars for the
        classifier. Returns None if the cache is empty or stale.
        """
        if not cache.bars:
            return None

        # Staleness check: compare wall-clock now against the most-recent
        # bar's close time. If older than the configured threshold, treat
        # as missing data.
        latest_bar_close_ms = cache.bars[-1].ts_ms + cache.timeframe_seconds * 1000
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        age_seconds = (now_ms - latest_bar_close_ms) / 1000.0
        threshold = self.staleness_thresholds.get(tf_label, 7200)
        if age_seconds > threshold:
            log.warning(
                "HTF context: %s cache stale (%.0fs > %ds threshold) — "
                "treating as missing", tf_label, age_seconds, threshold,
            )
            return None

        opens = tuple(b.open for b in cache.bars)
        highs = tuple(b.high for b in cache.bars)
        lows = tuple(b.low for b in cache.bars)
        closes = tuple(b.close for b in cache.bars)
        volumes = tuple(b.volume for b in cache.bars)
        last_close_ts = datetime.fromtimestamp(
            latest_bar_close_ms / 1000.0, tz=timezone.utc,
        )
        return TimeframeBars(
            timeframe=tf_label,
            opens=opens, highs=highs, lows=lows,
            closes=closes, volumes=volumes,
            last_bar_close_ts=last_close_ts,
        )

    def _funding_fresh(self) -> float | None:
        """Return cached funding if within max-age window, else None."""
        if self._last_funding_rate is None:
            return None
        if self._last_funding_fetch_monotonic is None:
            return None
        age = time.monotonic() - self._last_funding_fetch_monotonic
        if age > self.funding_max_age_seconds:
            return None
        return self._last_funding_rate

    def snapshot(self, current_price: float | None = None) -> HTFContext:
        """Build an HTFContext from current cache state. Sync — call
        freely from any context.

        `current_price` defaults to the latest 1H close, then 4H, then
        1D. If all caches are empty, falls back to 0.0 — but in that
        case the classifier will return SAFE_MODE anyway (all TFs missing).
        """
        h1 = self._cache_to_bars(self.h1_cache, "1h")
        h4 = self._cache_to_bars(self.h4_cache, "4h")
        d1 = self._cache_to_bars(self.d1_cache, "1d")

        if current_price is None:
            for cache in (self.h1_cache, self.h4_cache, self.d1_cache):
                if cache.bars:
                    current_price = cache.bars[-1].close
                    break
            else:
                current_price = 0.0

        prior_day_high, prior_day_low = self._prior_day_high_low()

        return HTFContext(
            h1=h1, h4=h4, d1=d1,
            current_price=current_price,
            prior_day_high=prior_day_high,
            prior_day_low=prior_day_low,
            funding_rate=self._funding_fresh(),
            ts=datetime.now(timezone.utc),
        )

    def _prior_day_high_low(self) -> tuple[float | None, float | None]:
        """Pull yesterday's H/L from the 1D cache. The most recent
        closed 1D bar IS yesterday — the in-progress current-day bar
        was already dropped by LiveBarCache.refresh().

        Returns (None, None) if 1D cache has no bars.
        """
        if not self.d1_cache.bars:
            return None, None
        last = self.d1_cache.bars[-1]
        return last.high, last.low

    def regime_snapshot(
        self, config: HTFRegimeConfig, current_price: float | None = None,
    ) -> RegimeVerdict:
        """Convenience: snapshot + compute_regime in one call."""
        return compute_regime(self.snapshot(current_price), config)
