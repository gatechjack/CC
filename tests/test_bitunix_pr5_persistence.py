"""Tests for the PR 5 backtesting-persistence layer.

Covers:
  - 5a: BitUnixBarArchiver — schema init, idempotent writes,
        high-water-mark deduplication across calls.
  - 5b: BitUnixHTFContextProvider funding-rate persistence
        (`bitunix_funding_history`) with INSERT OR IGNORE behavior.
  - 5c: Continuous HTF regime snapshot loop —
        `_snapshot_regime_to_audit` writes `htf_regime_snapshot` audit
        rows with the expected payload shape (incl. bar pointers).
  - 5f: `_log_htf_gate` includes `bar_h*_last_close_ms` pointers when
        the HTF provider has bars (and None when caches are empty).

PR 5e (funding_rate_at_decision on order extras) is exercised by the
observer integration path; not isolated here because it requires a
full score-engine fire + risk gate setup which already lives in
test_bitunix_futures_observer.py. Adding a unit test there isn't
worth the fixture surface.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
)
from trading_corp.agents.strategies.bitunix_htf_regime import (
    HTFRegimeConfig,
    Regime,
    RegimeVerdict,
    Session,
    TimeframeClassification,
    TimeframeRegime,
    TradePermission,
    VolatilityTier,
)
from trading_corp.data.bitunix_bar_archiver import BitUnixBarArchiver
from trading_corp.data.bitunix_htf_context import BitUnixHTFContextProvider
from trading_corp.data.live_bar_cache import Bar, LiveBarCache
from trading_corp.persistence import db


# ─── shared helpers ─────────────────────────────────────────────────────


def _filled_cache(
    timeframe: str, n: int = 10, base: float = 100.0,
) -> LiveBarCache:
    cache = LiveBarCache(symbol="BTCUSDT", timeframe=timeframe, max_bars=n)
    tf_seconds = cache.timeframe_seconds
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_close_ms = (now_ms // (tf_seconds * 1000)) * (tf_seconds * 1000)
    cache.bars = [
        Bar(
            ts_ms=last_close_ms - (n - 1 - i) * tf_seconds * 1000 - tf_seconds * 1000,
            open=base + i, high=(base + i) * 1.005,
            low=(base + i) * 0.995, close=base + i, volume=1000.0,
        )
        for i in range(n)
    ]
    return cache


def _empty_cache(timeframe: str) -> LiveBarCache:
    return LiveBarCache(symbol="BTCUSDT", timeframe=timeframe, max_bars=10)


# ─── PR 5a — BitUnixBarArchiver ─────────────────────────────────────────


@pytest.fixture
def bar_db_path(tmp_path: Path) -> str:
    db_path = tmp_path / "test_archiver.db"
    db.init_db(f"sqlite:///{db_path}")
    return f"sqlite:///{db_path}"


def test_archiver_schema_init_idempotent(bar_db_path):
    """Running archive_once on an empty caches list still creates the
    table without error; second call is a no-op.
    New schema (2026-06-25): leading `symbol` column, PK (symbol, ts_ms, timeframe).
    """
    arc = BitUnixBarArchiver(db_url=bar_db_path, caches=())
    arc.archive_once()
    arc.archive_once()
    with db.connect(bar_db_path) as conn:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(bitunix_bar_history)"
        ).fetchall()}
    assert {"symbol", "ts_ms", "timeframe", "open", "high", "low", "close",
            "volume", "inserted_at"} <= cols


def test_archiver_writes_all_bars_first_call(bar_db_path):
    cache = _filled_cache("3m", n=5)
    arc = BitUnixBarArchiver(db_url=bar_db_path, caches=(cache,))
    n = arc.archive_once()
    assert n == 5
    with db.connect(bar_db_path) as conn:
        rows = conn.execute(
            "SELECT symbol, ts_ms, timeframe FROM bitunix_bar_history "
            "ORDER BY ts_ms"
        ).fetchall()
    assert len(rows) == 5
    # New schema (2026-06-25): archiver writes cache.symbol into the symbol column.
    assert all(r["symbol"] == "BTCUSDT" for r in rows)
    assert all(r["timeframe"] == "3m" for r in rows)
    # Sorted ascending
    ts_list = [r["ts_ms"] for r in rows]
    assert ts_list == sorted(ts_list)


def test_archiver_skips_already_seen_on_subsequent_calls(bar_db_path):
    cache = _filled_cache("1h", n=3)
    arc = BitUnixBarArchiver(db_url=bar_db_path, caches=(cache,))
    assert arc.archive_once() == 3
    # Second call with no new bars in cache → 0 new writes
    assert arc.archive_once() == 0


def test_archiver_picks_up_new_bars_after_first_call(bar_db_path):
    cache = _filled_cache("4h", n=3)
    arc = BitUnixBarArchiver(db_url=bar_db_path, caches=(cache,))
    arc.archive_once()
    # Append a new bar (newer ts) and re-archive
    last = cache.bars[-1]
    new_bar = Bar(
        ts_ms=last.ts_ms + cache.timeframe_seconds * 1000,
        open=200, high=201, low=199, close=200.5, volume=2000,
    )
    cache.bars.append(new_bar)
    n = arc.archive_once()
    assert n == 1
    with db.connect(bar_db_path) as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM bitunix_bar_history"
        ).fetchone()["c"]
    assert total == 4


def test_archiver_dedupes_across_caches_by_pk(bar_db_path):
    """Two caches with overlapping ts_ms but different timeframes both
    persist. New schema PK is (symbol, ts_ms, timeframe): same symbol+TF+ts
    is the dedupe key; different TF lands as a separate row."""
    cache_3m = _filled_cache("3m", n=2)
    # Force-create a 1h cache with the same ts_ms — different TF, both
    # rows should land (PK differs on timeframe).
    cache_1h = LiveBarCache(symbol="BTCUSDT", timeframe="1h", max_bars=10)
    cache_1h.bars = [Bar(
        ts_ms=cache_3m.bars[0].ts_ms,
        open=100, high=101, low=99, close=100, volume=1000,
    )]
    arc = BitUnixBarArchiver(
        db_url=bar_db_path, caches=(cache_3m, cache_1h),
    )
    arc.archive_once()
    with db.connect(bar_db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM bitunix_bar_history"
        ).fetchone()["c"]
    assert n == 3       # 2 from 3m + 1 from 1h


def test_archiver_handles_empty_cache_silently(bar_db_path):
    arc = BitUnixBarArchiver(db_url=bar_db_path, caches=(_empty_cache("3m"),))
    assert arc.archive_once() == 0     # no bars, no writes


# ─── PR 5b — Funding-rate persistence ───────────────────────────────────


@pytest.fixture
def htf_db_path(tmp_path: Path) -> str:
    db_path = tmp_path / "test_htf.db"
    db.init_db(f"sqlite:///{db_path}")
    return f"sqlite:///{db_path}"


def _make_provider(db_url: str | None) -> BitUnixHTFContextProvider:
    broker = MagicMock()
    broker.get_funding_rate = AsyncMock(return_value=0.0003)
    return BitUnixHTFContextProvider(
        h1_cache=_empty_cache("1h"),
        h4_cache=_empty_cache("4h"),
        d1_cache=_empty_cache("1d"),
        broker=broker,
        symbol="BTCUSDT",
        db_url=db_url,
    )


@pytest.mark.asyncio
async def test_funding_refresh_writes_history_when_db_url_set(htf_db_path):
    p = _make_provider(htf_db_path)
    await p.refresh_funding_rate()
    with db.connect(htf_db_path) as conn:
        rows = conn.execute(
            "SELECT ts, symbol, rate FROM bitunix_funding_history"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["rate"] == pytest.approx(0.0003)


@pytest.mark.asyncio
async def test_funding_refresh_no_db_url_does_not_write(htf_db_path):
    """Provider with db_url=None doesn't try to persist (test envs)."""
    p = _make_provider(None)
    await p.refresh_funding_rate()
    # No table even created since db_url=None
    with db.connect(htf_db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "bitunix_funding_history" not in tables


@pytest.mark.asyncio
async def test_funding_refresh_failed_does_not_persist(htf_db_path):
    """A None return from broker (e.g., API error) keeps the cached
    value but writes nothing new."""
    broker = MagicMock()
    broker.get_funding_rate = AsyncMock(side_effect=[0.0001, None])
    p = BitUnixHTFContextProvider(
        h1_cache=_empty_cache("1h"), h4_cache=_empty_cache("4h"),
        d1_cache=_empty_cache("1d"), broker=broker, db_url=htf_db_path,
    )
    await p.refresh_funding_rate()        # writes 0.0001
    await p.refresh_funding_rate()        # broker returns None → no write
    with db.connect(htf_db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM bitunix_funding_history"
        ).fetchone()["c"]
    assert n == 1


# ─── PR 5c — Regime snapshot loop ───────────────────────────────────────


def test_snapshot_regime_to_audit_writes_row_with_full_payload(htf_db_path):
    """Direct test of the sync snapshot fn (not the async loop)."""
    p = BitUnixHTFContextProvider(
        h1_cache=_filled_cache("1h", n=10),     # not enough bars for EMA200
        h4_cache=_empty_cache("4h"),
        d1_cache=_empty_cache("1d"),
        broker=MagicMock(),
        db_url=htf_db_path,
    )
    config = HTFRegimeConfig.defaults()
    p._snapshot_regime_to_audit(config)
    with db.connect(htf_db_path) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='htf_regime_snapshot'"
        ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    # Required fields present
    expected = {
        "regime", "composite_score", "h1", "h4", "d1",
        "volatility_tier", "atr_pct_d1", "session",
        "funding_rate", "funding_extreme", "safe_mode_reason",
        "bar_h1_last_close_ms", "bar_h4_last_close_ms",
        "bar_d1_last_close_ms",
    }
    assert expected <= set(payload.keys())
    # h1 has bars → pointer set; h4/d1 empty → None
    assert payload["bar_h1_last_close_ms"] is not None
    assert payload["bar_h4_last_close_ms"] is None
    assert payload["bar_d1_last_close_ms"] is None


def test_snapshot_regime_safe_mode_when_all_caches_empty(htf_db_path):
    """All caches empty → SAFE_MODE regime; audit still written."""
    p = BitUnixHTFContextProvider(
        h1_cache=_empty_cache("1h"), h4_cache=_empty_cache("4h"),
        d1_cache=_empty_cache("1d"), broker=MagicMock(),
        db_url=htf_db_path,
    )
    config = HTFRegimeConfig.defaults()
    p._snapshot_regime_to_audit(config)
    with db.connect(htf_db_path) as conn:
        payload = json.loads(conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='htf_regime_snapshot'"
        ).fetchone()["payload_json"])
    assert payload["regime"] == "SAFE_MODE"
    assert payload["safe_mode_reason"] is not None


# ─── PR 5f — Bar pointers in htf_gate_decision audit ────────────────────


def _verdict_score_buy_premium():
    s = MagicMock()
    s.tier = MagicMock(value="PREMIUM")
    s.side = MagicMock(value="buy")
    s.breakdown = MagicMock(
        net_score=12, final_buy_score=12, final_sell_score=0,
    )
    s.cooldown_blocked = False
    s.reason = "PREMIUM"
    return s


def _htf_verdict():
    tf = TimeframeClassification(
        timeframe="1h", regime=TimeframeRegime.Bull,
        ema20=None, ema50=None, ema200=None,
        ema_alignment="bull", structure="bull",
        adx=25.0, macd_hist=0.001, reason="synth",
    )
    return RegimeVerdict(
        regime=Regime.STRONG_BULL, score=1.0,
        h1=tf, h4=tf, d1=tf,
        volatility_tier=VolatilityTier.Normal, atr_pct_d1=1.2,
        nearest_resistance=72000.0, nearest_support=68000.0,
        distance_to_resistance_pct=2.0, distance_to_support_pct=2.0,
        session=Session.NewYork,
        funding_rate=0.0001, funding_extreme=False,
        safe_mode_reason=None,
    )


def test_log_htf_gate_includes_bar_pointers_when_provider_has_bars(tmp_path):
    db_path = tmp_path / "test_log.db"
    db.init_db(f"sqlite:///{db_path}")
    obs = BitunixFuturesObserver(db_url=f"sqlite:///{db_path}")
    # Wire a provider with bars present
    obs.htf_provider = BitUnixHTFContextProvider(
        h1_cache=_filled_cache("1h", n=5),
        h4_cache=_filled_cache("4h", n=5),
        d1_cache=_filled_cache("1d", n=5),
        broker=MagicMock(),
    )
    permission = TradePermission(
        allow_long=True, allow_short=False, size_multiplier=1.0,
        reason="STRONG_BULL", hard_zero_reason=None,
    )
    obs._log_htf_gate(
        {"signal": "otter_buy", "_source": "lord_otter"},
        _verdict_score_buy_premium(), _htf_verdict(),
        permission, enforced=False,
    )
    with db.connect(f"sqlite:///{db_path}") as conn:
        payload = json.loads(conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='htf_gate_decision'"
        ).fetchone()["payload_json"])
    assert payload["bar_h1_last_close_ms"] is not None
    assert payload["bar_h4_last_close_ms"] is not None
    assert payload["bar_d1_last_close_ms"] is not None
    # Pointers should be the latest bar's ts_ms, which is an integer
    assert isinstance(payload["bar_h1_last_close_ms"], int)


def test_log_htf_gate_bar_pointers_none_when_no_provider(tmp_path):
    db_path = tmp_path / "test_log2.db"
    db.init_db(f"sqlite:///{db_path}")
    obs = BitunixFuturesObserver(db_url=f"sqlite:///{db_path}")
    # No provider attached
    permission = TradePermission(
        allow_long=True, allow_short=False, size_multiplier=1.0,
        reason="STRONG_BULL", hard_zero_reason=None,
    )
    obs._log_htf_gate(
        {"signal": "otter_buy", "_source": "lord_otter"},
        _verdict_score_buy_premium(), _htf_verdict(),
        permission, enforced=True,
    )
    with db.connect(f"sqlite:///{db_path}") as conn:
        payload = json.loads(conn.execute(
            "SELECT payload_json FROM audit_event "
            "WHERE kind='htf_gate_decision'"
        ).fetchone()["payload_json"])
    assert payload["bar_h1_last_close_ms"] is None
    assert payload["bar_h4_last_close_ms"] is None
    assert payload["bar_d1_last_close_ms"] is None
