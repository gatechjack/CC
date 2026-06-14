"""P1 fix (2026-06-14): reconciler false-divergence on the bot's OWN live position.

The first live BitUnix fill exposed two match failures in
`reconcile_position_state` (bot-tracked rows vs broker truth):

  * Symbol — the bot stores the internal form ``BTC/USDT.P``; BitUnix returns the
    wire form ``BTCUSDT`` → never equal.
  * Side  — `get_pending_positions` negated qty only when ``side == "SHORT"``,
    but a SELL-opened short is labelled (per captured live data) NOT "SHORT", so
    it read back as POSITIVE qty → `_broker_side` = "buy" ≠ the bot's "sell".

Either failure made the reconciler unable to match the bot's own position →
false `position_state_divergence_detected` every ~60s → `_halt_new_orders`
latched → new live entries blocked.

These tests reproduce the EXACT live shape and prove (a) the bot's own position
now MATCHES (no divergence, no halt), and (b) genuine divergences — real orphan,
missing, side-flip, unmapped-symbol orphan — STILL fire and STILL halt.
Complements tests/test_bitunix_broker_get_pending_positions.py (which still
asserts the legacy "SHORT" label for back-compat; the real label is "SELL").
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.agents.divisions.bitunix_position_reconciler import (
    _match_symbol_key,
    reconcile_position_state,
)
from trading_corp.brokers.bitunix import BitunixBroker, _signed_position_qty
from trading_corp.persistence import db


# ─── fixtures / helpers ──────────────────────────────────────────────────


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    p = tmp_path / "recon.db"
    url = f"sqlite:///{p}"
    db.init_db(url)
    return url


def _seed_bot_short(db_url: str, order_id: str = "ord-live-short") -> None:
    """Seed the bot's tracked live row EXACTLY as the first real fill stored it:
    internal symbol ``BTC/USDT.P``, side ``sell`` (NOT the wire form — storing
    the internal form is what made the symbol mismatch real on prod)."""
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record ("
            " order_id, ts, strategy, division, symbol, side, qty, "
            " entry_reference_price, stop_price, tp_price, "
            " max_hold_seconds, result, extra_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                order_id, "2026-06-14T18:24:08+00:00",
                "bitunix_futures", "bitunix_futures",
                "BTC/USDT.P", "sell", 0.000485496950614426,
                63679.4, 63805.3397, 63364.55,
                86400, None,
                json.dumps({"execution_mode": "live",
                            "broker_order_id": order_id}),
            ),
        )


def _broker_returning(raw_positions: list[dict]) -> BitunixBroker:
    """A real BitunixBroker whose signed `get_pending_positions` runs the live
    parse over `raw_positions` (the RAW BitUnix response shape)."""
    broker = BitunixBroker(api_key="k", api_secret="s")
    broker._client = MagicMock()
    broker._request = AsyncMock(return_value=raw_positions)
    broker._halt_new_orders = False
    broker._halt_reason = None
    return broker


# Grounded shape: a SELL-opened short. The `side` label is "SELL" (the strong
# inference — orders use BUY/SELL, and the live short read back as NOT "SHORT");
# qty is the broker-reported positive magnitude.
_RAW_SHORT = {
    "symbol": "BTCUSDT",
    "qty": "0.000485496950614426",
    "side": "SELL",
    "avgOpenPrice": "63678.1",
    "ctime": "1718387048000",
}


# ─── parse layer: _signed_position_qty / get_pending_positions ───────────


def test_signed_qty_sell_is_negative():
    assert _signed_position_qty("SELL", 0.0004855) == pytest.approx(-0.0004855)


def test_signed_qty_buy_is_positive():
    assert _signed_position_qty("BUY", 0.0004855) == pytest.approx(0.0004855)


def test_signed_qty_legacy_short_long_still_handled():
    assert _signed_position_qty("SHORT", 1.0) == -1.0
    assert _signed_position_qty("LONG", 1.0) == 1.0


def test_signed_qty_case_insensitive():
    assert _signed_position_qty("sell", 2.0) == -2.0
    assert _signed_position_qty("Short", 2.0) == -2.0


def test_signed_qty_idempotent_regardless_of_incoming_sign():
    # abs() ⇒ the label alone decides the sign, even if the broker pre-signs.
    assert _signed_position_qty("SELL", -3.0) == -3.0
    assert _signed_position_qty("BUY", -3.0) == 3.0


def test_signed_qty_unknown_label_warns_and_stays_long(caplog):
    with caplog.at_level(logging.WARNING, logger="trading_corp.brokers.bitunix"):
        out = _signed_position_qty("HEDGE_X", 5.0)
    assert out == 5.0  # fail-loud, positive — never silently mis-signed
    assert any("unrecognized side label" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_get_pending_positions_signs_sell_short_negative():
    """Parse-level regression: a SELL-opened short must read as NEGATIVE qty."""
    broker = _broker_returning([_RAW_SHORT])
    positions = await broker.get_pending_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "BTCUSDT"
    assert positions[0].qty == pytest.approx(-0.000485496950614426)


# ─── symbol normalization ────────────────────────────────────────────────


def test_match_symbol_key_internal_and_wire_collapse():
    assert _match_symbol_key("BTC/USDT.P") == "BTCUSDT"
    assert _match_symbol_key("BTCUSDT") == "BTCUSDT"
    assert _match_symbol_key("BTC/USDT.P") == _match_symbol_key("BTCUSDT")


def test_match_symbol_key_unmapped_falls_back_no_crash():
    # An instrument the bot doesn't trade (absent from the SSOT map) must not
    # crash; it normalizes to a deterministic key so it still compares (→ orphan).
    assert _match_symbol_key("SOLUSDT") == "SOLUSDT"
    assert _match_symbol_key("eth/usdt.p") == "ETH/USDT.P"
    assert _match_symbol_key("") == ""


# ─── reconcile_position_state: the bot's own position now MATCHES ────────


@pytest.mark.asyncio
async def test_live_short_matches_broker_no_divergence_no_halt(db_url):
    """End-to-end regression for the P1 bug: bot BTC/USDT.P 'sell' vs broker
    BTCUSDT SELL → exactly ONE match, NO divergence, NO halt latch."""
    _seed_bot_short(db_url)
    broker = _broker_returning([_RAW_SHORT])
    result = await reconcile_position_state(broker, db_url)
    assert not result.has_divergence
    assert len(result.matches) == 1
    assert len(result.missing_on_broker) == 0
    assert len(result.orphan_on_broker) == 0
    assert result.matches[0].order_id == "ord-live-short"
    assert broker._halt_new_orders is False  # the latch that blocked live entries


# ─── genuine divergences STILL fire + STILL halt ─────────────────────────


@pytest.mark.asyncio
async def test_genuine_missing_still_fires_and_halts(db_url):
    _seed_bot_short(db_url)
    broker = _broker_returning([])  # broker flat → the bot's short is missing
    result = await reconcile_position_state(broker, db_url)
    assert result.has_divergence
    assert len(result.missing_on_broker) == 1
    assert broker._halt_new_orders is True


@pytest.mark.asyncio
async def test_genuine_orphan_still_fires_and_halts(db_url):
    # Bot tracks nothing live; broker holds a position → orphan.
    broker = _broker_returning([_RAW_SHORT])
    result = await reconcile_position_state(broker, db_url)
    assert result.has_divergence
    assert len(result.orphan_on_broker) == 1
    assert result.orphan_on_broker[0].symbol == "BTCUSDT"
    assert broker._halt_new_orders is True


@pytest.mark.asyncio
async def test_real_side_flip_still_fires(db_url):
    """Bot is 'sell' but the broker actually holds a LONG (BUY) of the same
    symbol — a TRUE side mismatch must still diverge (not masked by the fix)."""
    _seed_bot_short(db_url)
    raw_long = dict(_RAW_SHORT, side="BUY")  # same symbol/qty, opposite side
    broker = _broker_returning([raw_long])
    result = await reconcile_position_state(broker, db_url)
    assert result.has_divergence
    assert len(result.missing_on_broker) == 1  # the bot's sell, unmatched
    assert len(result.orphan_on_broker) == 1   # the broker's buy, unmatched
    assert broker._halt_new_orders is True


@pytest.mark.asyncio
async def test_unmapped_symbol_orphan_still_fires_while_btc_matches(db_url):
    """A broker position in an instrument the bot doesn't trade is a genuine
    orphan — it must surface (no crash on the unmapped symbol), while the bot's
    real BTC short still matches cleanly."""
    _seed_bot_short(db_url)
    raw_sol = {"symbol": "SOLUSDT", "qty": "1.5", "side": "BUY",
               "avgOpenPrice": "150.0", "ctime": "1718387048000"}
    broker = _broker_returning([dict(_RAW_SHORT), raw_sol])
    result = await reconcile_position_state(broker, db_url)
    assert len(result.matches) == 1
    assert len(result.orphan_on_broker) == 1
    assert result.orphan_on_broker[0].symbol == "SOLUSDT"
    assert result.has_divergence
    assert broker._halt_new_orders is True
