"""Regression test: sports-skip branch NameError (commit a220dcf → e5efa06).

The bug: inside `run_scan_cycle`, the sports-skip audit payload referenced
`wallet` and `user_name` (names from the polymarket sibling) instead of the
in-scope `whale`.  Any selected whale with a NEW sports-prefix ticker caused a
NameError that aborted the entire scan cycle.

The fix (e5efa06): replaced those two keys with `'whale': whale`.

This test drives the real code path (run_scan_cycle end-to-end) and verifies:
  (a) No exception is raised.
  (b) log_event is called with kind='kalshi_copy_entry_skipped_sports'.
  (c) The payload contains 'whale' with the expected value and does NOT contain
      the broken keys 'wallet' or 'whale_handle' (in the sports-skip payload).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from trading_corp.data.kalshi_apify_client import WhalePosition
from trading_corp.persistence import db as _db


# ── Helpers copied from the existing test suite conventions ─────────────────


def _wp(name: str, ticker: str, pnl: float, contracts: int,
        is_open: bool = True) -> WhalePosition:
    return WhalePosition(
        market_id=f"m_{ticker}", market_ticker=ticker, name=name,
        is_open=is_open, pnl=pnl, contracts=contracts,
    )


class _StubApifyClient:
    """Returns a fixed list of positions on every call."""

    def __init__(self, positions: list[WhalePosition]):
        self._positions = positions
        self._calls: list[list[str]] = []

    async def fetch_open_positions(self, names: list[str]) -> list[WhalePosition]:
        self._calls.append(list(names))
        return list(self._positions)


class _StubLogger:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def log_event(self, actor: str, kind: str, payload: dict) -> None:
        self.events.append((actor, kind, payload))


# ── Fixture: isolated agent with tmp sqlite DB ───────────────────────────────


@pytest.fixture
def strategy(tmp_path):
    """A KalshiCopyTraderAgent bound to a fresh isolated SQLite DB.

    Mirrors the pattern in tests/test_kalshi_copy_trader.py::strategy.
    """
    from trading_corp.agents.strategies.kalshi_copy_trader import KalshiCopyTraderAgent

    db_path = tmp_path / "k3_sports_skip_test.db"
    db_url = f"sqlite:///{db_path}"
    _db.init_db(db_url)

    yaml_path = tmp_path / "strategies.yaml"
    yaml_path.write_text(
        "kalshi_copy_trader:\n  enabled: true\n  poll_interval_sec: 300\n",
        encoding="utf-8",
    )
    risk_path = tmp_path / "risk.yaml"
    risk_path.write_text("kalshi: {}\n", encoding="utf-8")

    agent = KalshiCopyTraderAgent(
        strategies_yaml=yaml_path, risk_yaml=risk_path, db_url=db_url,
    )
    return agent, db_url


# ── The regression test ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sports_skip_no_name_error(strategy):
    """Sports-prefix ticker on a non-cold-start whale must NOT raise NameError.

    Arrangement:
      - Whale 'bigfish' has been seen before (snapshot already persisted).
      - On the next poll, 'bigfish' has a new position in KXMLB-24-GAME7:YES
        (KXMLB is a known sports prefix).
      - No trade-tape fetcher is provided (irrelevant — sports skip fires
        before side detection).

    Expected:
      (a) run_scan_cycle returns without raising an exception.
      (b) logger.log_event was called with kind='kalshi_copy_entry_skipped_sports'.
      (c) The payload for that event contains key 'whale' == 'bigfish'
          and does NOT contain 'wallet' or 'whale_handle' as keys of the
          sports-skip payload itself.
    """
    agent, db_url = strategy
    whale = "bigfish"

    # Register 'bigfish' as a selected whale.
    _db.set_agent_state(
        "kalshi_copy_trader", "selected_whales", [whale], db_url=db_url,
    )

    # Pre-populate a non-empty snapshot so this is NOT a cold start.
    # The snapshot has one existing non-sports position already known.
    existing_snapshot = {
        "KXBTC-24-T1": {
            "contracts": 50, "pnl": 0.0,
            "first_seen_iso": "2026-05-01T00:00:00",
            "our_side": "yes", "copy_size_usd": 1.0, "entry_price": 0.55,
        },
    }
    _db.set_agent_state(
        "kalshi_copy_trader", f"positions:{whale}", existing_snapshot,
        db_url=db_url,
    )

    # Poll: bigfish still has the old position PLUS a new KXMLB sports ticker.
    # KXMLB is the first entry in _SPORTS_TICKER_PREFIXES.
    sports_ticker = "KXMLB-24-GAME7-YES"
    apify = _StubApifyClient([
        _wp(whale, "KXBTC-24-T1", 0.5, 50, is_open=True),
        _wp(whale, sports_ticker, 0.0, 200, is_open=True),
    ])
    logger = _StubLogger()

    # The critical assertion: this must NOT raise NameError.
    orders = await agent.run_scan_cycle(
        apify_client=apify,
        trade_tape_fetcher=None,
        logger_agent=logger,
    )

    # (a) No exception: we got here.

    # (b) The sports-skip audit event was emitted.
    sports_skip_events = [
        (actor, kind, payload)
        for actor, kind, payload in logger.events
        if kind == "kalshi_copy_entry_skipped_sports"
    ]
    assert len(sports_skip_events) == 1, (
        f"Expected exactly 1 'kalshi_copy_entry_skipped_sports' event; "
        f"got {len(sports_skip_events)}. All events: "
        f"{[k for _, k, _ in logger.events]}"
    )

    _, _, payload = sports_skip_events[0]

    # (c) Payload has the correct in-scope key 'whale', not the broken names.
    assert payload.get("whale") == whale, (
        f"Expected payload['whale'] == {whale!r}; got {payload.get('whale')!r}"
    )
    # These keys must NOT appear in the sports-skip payload
    # (they are the names from the broken commit a220dcf).
    assert "wallet" not in payload, (
        f"'wallet' key found in sports-skip payload: {payload}"
    )
    assert "whale_handle" not in payload, (
        f"'whale_handle' key found in sports-skip payload: {payload}"
    )

    # Bonus: the skipped ticker should also be recorded.
    assert payload.get("ticker") == sports_ticker

    # The non-sports, non-cold-start carryover position is handled without
    # error (old position stays in snapshot, no exit emitted since it's still
    # present, no entry since it's not new).
    assert orders == []  # no side detection → no entries; old pos is carryover
