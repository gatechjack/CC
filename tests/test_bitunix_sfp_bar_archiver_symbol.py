"""Tests for the 2026-06-25 multi-coin bitunix_bar_history migration.

Verifies:
  - BitUnixBarArchiver writes the symbol column and uses PK
    (symbol, ts_ms, timeframe), so BTCUSDT and SOLUSDT bars at the
    SAME ts_ms + timeframe are stored as two distinct rows (no collision).
  - _load_recent_bars is symbol-scoped: querying for SOLUSDT returns
    only SOL bars, not BTC bars.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from trading_corp.agents.divisions.bitunix_position_reconciler import (
    _load_recent_bars,
)
from trading_corp.data.bitunix_bar_archiver import BitUnixBarArchiver
from trading_corp.persistence import db


# ── tiny fake caches so we don't need LiveBarCache ──────────────────────────

SHARED_TS_MS = 1_700_000_000_000   # arbitrary fixed ms timestamp
TIMEFRAME = "15m"


def _make_bar(ts_ms: int, close: float) -> SimpleNamespace:
    """Minimal bar object accepted by BitUnixBarArchiver.archive_once()."""
    return SimpleNamespace(
        ts_ms=ts_ms,
        open=close - 1.0,
        high=close + 2.0,
        low=close - 2.0,
        close=close,
        volume=500.0,
    )


def _make_cache(symbol: str, close: float) -> SimpleNamespace:
    """Fake LiveBarCache with the attributes archive_once() reads."""
    return SimpleNamespace(
        symbol=symbol,
        timeframe=TIMEFRAME,
        bars=[_make_bar(SHARED_TS_MS, close)],
    )


# ── fixture ─────────────────────────────────────────────────────────────────


@pytest.fixture
def sym_db_url(tmp_path: Path) -> str:
    db_path = tmp_path / "t.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    db.init_db(db_url)
    # Confirm db.init_db does NOT pre-create bitunix_bar_history
    # (archiver owns that table via _ensure_schema).
    return db_url


# ── tests ────────────────────────────────────────────────────────────────────


def test_two_symbols_same_ts_no_collision(sym_db_url: str) -> None:
    """BTCUSDT and SOLUSDT at the same ts_ms+timeframe must both persist."""
    btc_cache = _make_cache("BTCUSDT", close=42_000.0)
    sol_cache = _make_cache("SOLUSDT", close=80.0)

    archiver = BitUnixBarArchiver(
        db_url=sym_db_url, caches=(btc_cache, sol_cache)
    )
    written = archiver.archive_once()
    assert written == 2, f"expected 2 rows written, got {written}"

    with db.connect(sym_db_url) as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM bitunix_bar_history "
            "WHERE ts_ms=? AND timeframe=?",
            (SHARED_TS_MS, TIMEFRAME),
        ).fetchone()["n"]
    assert count == 2, (
        f"expected 2 distinct rows (BTC + SOL) at same ts_ms, got {count}"
    )


def test_symbol_per_row_correct(sym_db_url: str) -> None:
    """Each row carries the correct symbol from its cache."""
    btc_cache = _make_cache("BTCUSDT", close=42_000.0)
    sol_cache = _make_cache("SOLUSDT", close=80.0)

    archiver = BitUnixBarArchiver(
        db_url=sym_db_url, caches=(btc_cache, sol_cache)
    )
    archiver.archive_once()

    with db.connect(sym_db_url) as conn:
        symbols = {
            r["symbol"]
            for r in conn.execute(
                "SELECT symbol FROM bitunix_bar_history WHERE ts_ms=?",
                (SHARED_TS_MS,),
            ).fetchall()
        }
    assert symbols == {"BTCUSDT", "SOLUSDT"}


def test_load_recent_bars_symbol_scoped(sym_db_url: str) -> None:
    """_load_recent_bars(db_url, 'SOLUSDT', ...) returns ONLY SOL bars."""
    btc_close = 42_000.0
    sol_close = 80.0

    btc_cache = _make_cache("BTCUSDT", close=btc_close)
    sol_cache = _make_cache("SOLUSDT", close=sol_close)

    archiver = BitUnixBarArchiver(
        db_url=sym_db_url, caches=(btc_cache, sol_cache)
    )
    archiver.archive_once()

    sol_bars = _load_recent_bars(sym_db_url, "SOLUSDT", TIMEFRAME, limit=10)
    assert len(sol_bars) == 1, f"expected 1 SOL bar, got {len(sol_bars)}"
    assert sol_bars[0]["close"] == pytest.approx(sol_close), (
        f"SOL bar close should be {sol_close}, got {sol_bars[0]['close']}"
    )
    # Confirm BTC close did NOT bleed into the SOL result
    assert sol_bars[0]["close"] != pytest.approx(btc_close)


def test_load_recent_bars_btc_scoped(sym_db_url: str) -> None:
    """_load_recent_bars(db_url, 'BTCUSDT', ...) returns ONLY BTC bars."""
    btc_close = 42_000.0
    sol_close = 80.0

    btc_cache = _make_cache("BTCUSDT", close=btc_close)
    sol_cache = _make_cache("SOLUSDT", close=sol_close)

    archiver = BitUnixBarArchiver(
        db_url=sym_db_url, caches=(btc_cache, sol_cache)
    )
    archiver.archive_once()

    btc_bars = _load_recent_bars(sym_db_url, "BTCUSDT", TIMEFRAME, limit=10)
    assert len(btc_bars) == 1, f"expected 1 BTC bar, got {len(btc_bars)}"
    assert btc_bars[0]["close"] == pytest.approx(btc_close)
    assert btc_bars[0]["close"] != pytest.approx(sol_close)


def test_archiver_idempotent_second_call(sym_db_url: str) -> None:
    """Second archive_once() with unchanged cache writes 0 new rows."""
    btc_cache = _make_cache("BTCUSDT", close=42_000.0)
    sol_cache = _make_cache("SOLUSDT", close=80.0)

    archiver = BitUnixBarArchiver(
        db_url=sym_db_url, caches=(btc_cache, sol_cache)
    )
    first = archiver.archive_once()
    second = archiver.archive_once()
    assert first == 2
    assert second == 0, f"second call should write 0 rows (idempotent), got {second}"
