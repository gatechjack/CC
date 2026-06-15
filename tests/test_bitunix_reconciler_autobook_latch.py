"""P2 auto-book + latch-release (2026-06-14): the reconciler self-recovers after
a server-side stop close instead of forcing a manual book + restart (trades 1 & 2).

Covers:
  * server-side stop close on a bot-owned position, CONFIRMED across two
    consecutive ticks → auto-booked at the KNOWN stop level (loss, sign-correct
    PnL, flagged `auto_booked_from_stop_level` / `known_level_estimate` /
    `slippage_unreconciled`);
  * single-tick (unconfirmed) missing → NOT booked (transient-API-error guard);
  * partial-TP / no-stop close → DEFERRED (NULL + flag, no guess);
  * two consecutive clean ticks → `_halt_new_orders` RELEASED (self-resume, no
    restart) — and the full close→auto-book→release recovery;
  * GENUINE orphan (an unowned position, the manual-short case) → divergence →
    halt STAYS set, release does NOT fire.

NB on TP-level booking: per the BitUnix architecture TPs are bot-side reactive
closes that get booked via the normal path, so a broker-side close with NO TP
leg filled can only be the server-side stop. A close where a TP WAS reached
(filled_legs non-empty) is genuinely ambiguous from stored state without a price
fetch → DEFERRED, not guessed (the accurate signed-fetch version is a BACKLOG item).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions.bitunix_position_reconciler import (
    AUTO_BOOK_SERVER_SIDE_CLOSE_KIND,
    POSITION_STATE_DIVERGENCE_KIND,
    POSITION_STATE_HALT_RELEASED_KIND,
    POSITION_STATE_RECONCILED_KIND,
    RECONCILER_ACTOR,
    reconcile_position_state,
)
from trading_corp.brokers.bitunix import BitunixBroker
from trading_corp.persistence import db


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    p = tmp_path / "p2.db"
    url = f"sqlite:///{p}"
    db.init_db(url)
    return url


def _seed_short(db_url, order_id="ord-short", *, filled_legs=None,
                stop_price=65004.47635, entry=64752.7, qty=0.000423,
                max_dollar_risk=0.213) -> None:
    """Seed a bot-owned live SHORT exactly like trade 2's open-state row."""
    extra = {
        "execution_mode": "live", "broker_order_id": order_id,
        "filled_legs": filled_legs or [], "current_sl": stop_price,
        "stop_price": stop_price, "max_dollar_risk": max_dollar_risk,
        "entry_reference_price": entry,
    }
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record ("
            " order_id, ts, strategy, division, symbol, side, qty, "
            " entry_reference_price, stop_price, tp_price, max_hold_seconds, "
            " result, extra_json"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, "2026-06-14T21:30:02+00:00", "bitunix_futures",
             "bitunix_futures", "BTC/USDT.P", "sell", qty,
             entry, stop_price, 64123.26, 86400, None, json.dumps(extra)),
        )


def _seed_prior_audit(db_url, kind, missing_ids=()) -> None:
    """Seed the previous tick's position-state audit (for the 2-tick confirm)."""
    payload = {
        "match_count": 0,
        "missing_on_broker_count": len(missing_ids),
        "orphan_on_broker_count": 0,
        "missing_on_broker": [{"order_id": oid, "symbol": "BTC/USDT.P",
                               "side": "sell", "bot_qty": 0.000423}
                              for oid in missing_ids],
        "orphan_on_broker": [],
    }
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) "
            "VALUES (?,?,?,?)",
            ("2026-06-14T21:39:00+00:00", RECONCILER_ACTOR, kind,
             json.dumps(payload)),
        )


def _broker(raw_positions, halted=False) -> BitunixBroker:
    b = BitunixBroker(api_key="k", api_secret="s")
    b._client = MagicMock()
    b._request = AsyncMock(return_value=raw_positions)
    b._halt_new_orders = halted
    b._halt_reason = "position_state_reconciler_divergence" if halted else None
    return b


_RAW_ORPHAN_SHORT = {"symbol": "BTCUSDT", "qty": "0.098", "side": "SELL",
                     "avgOpenPrice": "65000", "ctime": "1718000000000"}


def _row(db_url, order_id):
    with db.connect(db_url) as conn:
        r = conn.execute(
            "SELECT result, result_price, actual_pnl_dollars, extra_json "
            "FROM paper_trade_record WHERE order_id=?", (order_id,)).fetchone()
    extra = json.loads(r["extra_json"]) if r and r["extra_json"] else {}
    return r, extra


def _audit_kinds(db_url):
    with db.connect(db_url) as conn:
        return [r["kind"] for r in conn.execute(
            "SELECT kind FROM audit_event WHERE actor=? ORDER BY id",
            (RECONCILER_ACTOR,)).fetchall()]


# ─── auto-book ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirmed_stop_close_autobooks_at_stop_level(db_url):
    _seed_short(db_url, "ord-short")
    _seed_prior_audit(db_url, POSITION_STATE_DIVERGENCE_KIND, ["ord-short"])
    broker = _broker([])  # broker flat → the bot's short is missing
    result = await reconcile_position_state(broker, db_url)
    r, extra = _row(db_url, "ord-short")
    assert r["result"] == "loss"
    assert r["result_price"] == pytest.approx(65004.47635)            # at the stop
    assert r["actual_pnl_dollars"] == pytest.approx(
        (64752.7 - 65004.47635) * 0.000423)                          # short, sign-correct
    assert r["actual_pnl_dollars"] < 0
    assert extra["result_source"] == "auto_booked_from_stop_level"   # NOT operator_manual_booking
    assert extra["pnl_basis"] == "known_level_estimate"
    assert extra["slippage_unreconciled"] is True
    assert extra["exit_side"] == "buy"                               # buy-to-close
    assert not result.has_divergence                                 # missing resolved this tick
    assert AUTO_BOOK_SERVER_SIDE_CLOSE_KIND in _audit_kinds(db_url)


@pytest.mark.asyncio
async def test_long_stop_close_pnl_sign(db_url):
    # a LONG stopped out below entry → loss, (level - entry) * qty
    extra = {"execution_mode": "live", "filled_legs": [],
             "stop_price": 64000.0, "max_dollar_risk": 0.2}
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record (order_id, ts, strategy, division, "
            "symbol, side, qty, entry_reference_price, stop_price, tp_price, "
            "max_hold_seconds, result, extra_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ord-long", "2026-06-14T21:00:00+00:00", "bitunix_futures",
             "bitunix_futures", "BTC/USDT.P", "buy", 0.0004,
             64500.0, 64000.0, 65000.0, 86400, None, json.dumps(extra)),
        )
    _seed_prior_audit(db_url, POSITION_STATE_DIVERGENCE_KIND, ["ord-long"])
    broker = _broker([])
    await reconcile_position_state(broker, db_url)
    r, extra = _row(db_url, "ord-long")
    assert r["result"] == "loss"
    assert r["actual_pnl_dollars"] == pytest.approx((64000.0 - 64500.0) * 0.0004)
    assert r["actual_pnl_dollars"] < 0
    assert extra["exit_side"] == "sell"                              # sell-to-close a long


@pytest.mark.asyncio
async def test_unconfirmed_missing_not_booked_first_tick(db_url):
    _seed_short(db_url, "ord-short")
    # NO prior-missing audit → first time missing → must NOT book (transient guard)
    broker = _broker([])
    result = await reconcile_position_state(broker, db_url)
    r, _ = _row(db_url, "ord-short")
    assert r["result"] is None                                       # NOT booked
    assert result.has_divergence
    assert len(result.missing_on_broker) == 1


@pytest.mark.asyncio
async def test_partial_tp_close_defers_not_guesses(db_url):
    _seed_short(db_url, "ord-tp", filled_legs=["tp1"])
    _seed_prior_audit(db_url, POSITION_STATE_DIVERGENCE_KIND, ["ord-tp"])
    broker = _broker([])
    result = await reconcile_position_state(broker, db_url)
    r, extra = _row(db_url, "ord-tp")
    assert r["result"] is None                                       # DEFERRED
    assert extra.get("autobook_deferred") == "partial_tp_ambiguous"
    assert result.has_divergence                                     # stays missing


# ─── latch-release ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_consecutive_clean_releases_halt(db_url):
    _seed_prior_audit(db_url, POSITION_STATE_RECONCILED_KIND)  # prior clean
    broker = _broker([], halted=True)                          # this tick clean (flat, no rows)
    result = await reconcile_position_state(broker, db_url)
    assert not result.has_divergence
    assert broker._halt_new_orders is False                    # RELEASED
    assert POSITION_STATE_HALT_RELEASED_KIND in _audit_kinds(db_url)


@pytest.mark.asyncio
async def test_single_clean_tick_does_not_release(db_url):
    _seed_prior_audit(db_url, POSITION_STATE_DIVERGENCE_KIND, ["x"])  # prior NOT clean
    broker = _broker([], halted=True)
    result = await reconcile_position_state(broker, db_url)
    assert not result.has_divergence
    assert broker._halt_new_orders is True                     # only 1 clean tick → stays latched


@pytest.mark.asyncio
async def test_autobook_then_release_full_recovery(db_url):
    _seed_short(db_url, "ord-short")
    _seed_prior_audit(db_url, POSITION_STATE_DIVERGENCE_KIND, ["ord-short"])
    broker = _broker([], halted=True)
    # tick 1: confirmed missing → auto-book → clean, but prior was divergence → no release yet
    r1 = await reconcile_position_state(broker, db_url)
    assert not r1.has_divergence
    assert _row(db_url, "ord-short")[0]["result"] == "loss"
    assert broker._halt_new_orders is True                     # not released yet
    # tick 2: clean again, prior (tick 1) clean → self-recover
    r2 = await reconcile_position_state(broker, db_url)
    assert not r2.has_divergence
    assert broker._halt_new_orders is False                    # released — no restart needed


# ─── safety: a genuine orphan keeps the halt set ─────────────────────────


@pytest.mark.asyncio
async def test_genuine_orphan_stays_halted_no_release(db_url):
    """The manual-short case: an unowned broker position → orphan divergence →
    halt STAYS set even though the prior tick was clean; release does NOT fire."""
    _seed_prior_audit(db_url, POSITION_STATE_RECONCILED_KIND)   # prior clean
    broker = _broker([_RAW_ORPHAN_SHORT], halted=True)
    result = await reconcile_position_state(broker, db_url)
    assert result.has_divergence
    assert len(result.orphan_on_broker) == 1
    assert broker._halt_new_orders is True                     # NOT released into a real orphan
    assert POSITION_STATE_HALT_RELEASED_KIND not in _audit_kinds(db_url)


@pytest.mark.asyncio
async def test_orphan_does_not_autobook_a_bot_row(db_url):
    # a confirmed-missing bot row (BTC) AND a genuine orphan in a DIFFERENT
    # instrument (ETH, so it doesn't match the BTC row): the bot row auto-books,
    # the ETH orphan still diverges → halt stays.
    _seed_short(db_url, "ord-short")
    _seed_prior_audit(db_url, POSITION_STATE_DIVERGENCE_KIND, ["ord-short"])
    eth_orphan = {"symbol": "ETHUSDT", "qty": "1.5", "side": "SELL",
                  "avgOpenPrice": "3000", "ctime": "1718000000000"}
    broker = _broker([eth_orphan], halted=True)
    result = await reconcile_position_state(broker, db_url)
    assert _row(db_url, "ord-short")[0]["result"] == "loss"     # bot row booked
    assert result.has_divergence                               # ETH orphan remains
    assert len(result.orphan_on_broker) == 1
    assert result.orphan_on_broker[0].symbol == "ETHUSDT"
    assert broker._halt_new_orders is True                     # stays halted (orphan)
