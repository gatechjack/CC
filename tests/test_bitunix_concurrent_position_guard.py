"""D4 concurrent-position guard — unit tests for
`BitunixFuturesObserver._concurrent_position_guard_verdict`.

Board-ruled rule under test: block iff (VENUE shows an open same-symbol
SAME-SIDE position) AND (bot has a tracked open same-side live row). Venue is
authoritative; engine corroborates only; fail-CLOSED on an unknown/incomplete
venue read; same-side only (close-and-reverse allowed); manual / not-bot-opened
is deferred to the reconciler orphan-halt; dormant when the flag is OFF.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trading_corp.agents.divisions.bitunix_futures_observer import (
    BitunixFuturesObserver,
)
from trading_corp.persistence import db


def _make_observer(tmp_path: Path, *, enabled: bool) -> BitunixFuturesObserver:
    db_path = tmp_path / "d4_guard.db"
    db.init_db(f"sqlite:///{db_path}")
    return BitunixFuturesObserver(
        db_url=f"sqlite:///{db_path}",
        concurrent_position_guard_enabled=enabled,
    )


def _seed_open_live_row(obs: BitunixFuturesObserver, *, symbol: str, side: str) -> None:
    """Insert a tracked OPEN (result NULL) live paper_trade_record row — what
    `_load_tracked_live_rows` reads as 'a position the bot opened'."""
    with db.connect(obs.db_url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record "
            "(order_id, ts, strategy, division, symbol, side, qty, "
            " execution_mode, extra_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"row-{symbol}-{side}",
                "2026-06-20T00:00:00+00:00",
                "bitunix_futures",
                "bitunix_futures",
                symbol,
                side,
                0.001,
                "live",
                json.dumps({"execution_mode": "live"}),
            ),
        )
        conn.commit()


def _snap(positions, *, equity_complete: bool = True):
    return SimpleNamespace(positions=positions, equity_complete=equity_complete)


def _pos(symbol: str, qty: float):
    # Broker truth: SHORT renders negative qty, LONG positive (the P1 sign fix).
    return SimpleNamespace(symbol=symbol, qty=qty)


# ── dormant when disabled ───────────────────────────────────────────────
def test_guard_off_never_blocks(tmp_path: Path):
    obs = _make_observer(tmp_path, enabled=False)
    _seed_open_live_row(obs, symbol="BTC/USDT.P", side="sell")
    blocked, _info = obs._concurrent_position_guard_verdict(
        _snap([_pos("BTCUSDT", -0.001)]), "BTC/USDT.P", "sell",
    )
    assert blocked is False


# ── the defect: bot-on-bot same-side stack → BLOCK ──────────────────────
def test_blocks_bot_own_same_side_stack(tmp_path: Path):
    obs = _make_observer(tmp_path, enabled=True)
    _seed_open_live_row(obs, symbol="BTC/USDT.P", side="sell")
    blocked, info = obs._concurrent_position_guard_verdict(
        _snap([_pos("BTCUSDT", -0.0019)]), "BTC/USDT.P", "sell",
    )
    assert blocked is True
    assert info["reason"] == "bot_own_same_side_position_open"
    assert info["source"] == "venue+engine"


# ── venue flat → never block (post-manual-close engine-row lag) ─────────
def test_venue_flat_not_blocked_even_if_engine_row_lags(tmp_path: Path):
    obs = _make_observer(tmp_path, enabled=True)
    _seed_open_live_row(obs, symbol="BTC/USDT.P", side="sell")  # engine lags open
    blocked, _info = obs._concurrent_position_guard_verdict(
        _snap([]), "BTC/USDT.P", "sell",  # VENUE flat → authoritative
    )
    assert blocked is False


# ── venue same-side open but NO bot row → manual/orphan, D4 defers ──────
def test_manual_position_not_blocked_no_bot_row(tmp_path: Path):
    obs = _make_observer(tmp_path, enabled=True)  # no engine row seeded
    blocked, _info = obs._concurrent_position_guard_verdict(
        _snap([_pos("BTCUSDT", -0.001)]), "BTC/USDT.P", "sell",
    )
    assert blocked is False  # reconciler orphan-halt owns this, not D4


# ── reversal: opposite side → never block ───────────────────────────────
def test_reversal_opposite_side_not_blocked(tmp_path: Path):
    obs = _make_observer(tmp_path, enabled=True)
    _seed_open_live_row(obs, symbol="BTC/USDT.P", side="sell")  # bot short
    blocked, _info = obs._concurrent_position_guard_verdict(
        _snap([_pos("BTCUSDT", -0.001)]), "BTC/USDT.P", "buy",  # close-and-reverse
    )
    assert blocked is False


# ── symbol-keyed: a different-symbol position doesn't block ─────────────
def test_different_symbol_not_blocked(tmp_path: Path):
    obs = _make_observer(tmp_path, enabled=True)
    _seed_open_live_row(obs, symbol="ETH/USDT.P", side="sell")
    blocked, _info = obs._concurrent_position_guard_verdict(
        _snap([_pos("ETHUSDT", -0.001)]), "BTC/USDT.P", "sell",  # new BTC entry
    )
    assert blocked is False


# ── fail-CLOSED on an unknown/incomplete venue read ─────────────────────
@pytest.mark.parametrize("bad_snap", [
    None,
    SimpleNamespace(positions=None, equity_complete=True),
    SimpleNamespace(positions=[], equity_complete=False),
])
def test_fail_closed_unknown_venue_blocks(tmp_path: Path, bad_snap):
    obs = _make_observer(tmp_path, enabled=True)
    blocked, info = obs._concurrent_position_guard_verdict(
        bad_snap, "BTC/USDT.P", "sell",
    )
    assert blocked is True
    assert info["reason"] == "venue_state_unknown_fail_closed"
