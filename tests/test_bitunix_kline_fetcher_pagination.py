"""Bug-reproduction + fix test for _bitunix_kline_fetcher.

Bug evidence (verified 2026-05-20 against the live BitUnix kline endpoint):

    GET /api/v1/futures/market/kline?symbol=BTCUSDT&interval=1m
        &startTime=1779121442000&endTime=1779181440000&limit=1000

    → code=0, msg='Success', rows_returned=200 (NOT 1000)
      First row time = 1779181380000 (NEWEST of the requested window)
      Last row time  = 1779169440000 (200 min earlier — the OLDEST returned)

The BitUnix endpoint silently caps responses at ~200 bars per call regardless
of the `limit` parameter. The old fetcher's `if len(page) < this_page: break`
treated this as "end of data" and exited after one page, returning only the
NEWEST 200 bars within the requested window — silently dropping the earlier
~85% of the requested range.

For paper-mode v2 trade resolution, this meant the classifier walked bars
from a window that did NOT overlap the trade's actual entry/early-life price
action. TP1/TP2 fills that happened in the early window were silently missed;
the classifier saw only late bars where the SL had long been violated and
returned `loss` immediately on the first bar walked (bars_to_resolution=1).

These tests:
  1. Reproduce the bug at the fetcher layer (canned 200-cap response).
  2. Assert the fix correctly pages through the entire requested window.

Network-free; uses a fake httpx.AsyncClient that simulates the server's
200-bar cap + descending-newest-first ordering.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from trading_corp.agents import paper_trade_replay as ptr


# ── fake BitUnix kline server ──────────────────────────────────────────


class _FakeBitunixServer:
    """Simulates BitUnix kline endpoint behavior: returns up to SERVER_CAP
    bars per call, from the newest end of the requested [startTime, endTime]
    window in DESCENDING ORDER (newest first)."""

    SERVER_CAP = 200

    def __init__(self, *, all_bars: list[dict]):
        # all_bars is the full universe of bars the "server" knows about,
        # in ASCENDING order by time.
        self.all_bars = sorted(all_bars, key=lambda b: int(b["time"]))
        self.requests: list[dict[str, Any]] = []

    def respond(self, params: dict[str, Any]) -> httpx.Response:
        self.requests.append(dict(params))
        start = int(params.get("startTime", 0))
        end = int(params.get("endTime", 10**14))
        limit = int(params.get("limit", 1000))
        # Bars within [start, end), then take the NEWEST min(SERVER_CAP, limit).
        in_window = [b for b in self.all_bars if start <= int(b["time"]) < end]
        in_window.sort(key=lambda b: int(b["time"]), reverse=True)
        clip = min(self.SERVER_CAP, limit)
        page = in_window[:clip]
        return httpx.Response(
            200,
            json={"code": 0, "msg": "Success", "data": page},
            request=httpx.Request(
                "GET", "https://fapi.bitunix.com/api/v1/futures/market/kline",
                params=params,
            ),
        )


class _FakeAsyncClient:
    """Drop-in replacement for httpx.AsyncClient — routes .get() through
    a _FakeBitunixServer."""

    def __init__(self, *args, server: _FakeBitunixServer, **kwargs):
        self._server = server

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        return self._server.respond(params or {})


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, server: _FakeBitunixServer):
    def _factory(*args, **kwargs):
        return _FakeAsyncClient(server=server)
    monkeypatch.setattr(ptr.httpx if hasattr(ptr, "httpx") else httpx, "AsyncClient", _factory)
    # The fetcher does `import httpx` inside the function; patch at module scope too.
    import trading_corp.agents.paper_trade_replay  # noqa: F401
    monkeypatch.setattr(httpx, "AsyncClient", _factory)


def _build_bars(n: int, start_ts_ms: int, tf_ms: int, base_price: float = 100.0) -> list[dict]:
    """Generate n consecutive bars at tf_ms spacing. Prices walk a small sine
    for variation; only `time` matters for these tests."""
    bars = []
    for i in range(n):
        ts = start_ts_ms + i * tf_ms
        p = base_price + (i % 7) * 0.1
        bars.append({
            "time": str(ts),
            "open": str(p),
            "high": str(p + 0.3),
            "low": str(p - 0.3),
            "close": str(p + 0.1),
            "baseVol": "1000.0",
        })
    return bars


# ── reproduction tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kline_fetcher_returns_full_window_when_server_caps_at_200(monkeypatch):
    """REGRESSION TEST: when the BitUnix server caps each response at 200
    bars but the trade's full window needs more, the fetcher must page
    forward and return the entire window.

    BEFORE FIX: returns only the newest 200 bars (bug).
    AFTER FIX: returns all 1000 bars across multiple chunked calls.
    """
    tf_ms = 60_000  # 1m
    start_ts = 1_779_000_000_000  # arbitrary
    # 1000 minutes of bars = the full window the fetcher requests.
    all_bars = _build_bars(1000, start_ts, tf_ms)
    server = _FakeBitunixServer(all_bars=all_bars)
    _patch_httpx(monkeypatch, server)

    bars = await ptr._bitunix_kline_fetcher(
        symbol="BTCUSDT.P", timeframe="1m",
        since_ms=start_ts, limit=1000,
    )

    # Bug: returns 200. Fix: returns 1000.
    assert len(bars) == 1000, (
        f"Expected 1000 bars covering full window; got {len(bars)} — "
        f"the server cap is being treated as end-of-data."
    )
    # Confirm chronological order + full coverage from the oldest bar.
    assert bars[0][0] == start_ts, (
        f"Expected first bar at {start_ts} (start of window); "
        f"got {bars[0][0]} — fetcher missed the earliest bars."
    )
    assert bars[-1][0] == start_ts + 999 * tf_ms, (
        f"Expected last bar at end of 1000-min window; got {bars[-1][0]}."
    )


@pytest.mark.asyncio
async def test_kline_fetcher_short_window_uses_one_call(monkeypatch):
    """Sanity: when window fits in one server response (≤200 bars),
    the fetcher still returns the right slice."""
    tf_ms = 60_000
    start_ts = 1_779_000_000_000
    all_bars = _build_bars(50, start_ts, tf_ms)
    server = _FakeBitunixServer(all_bars=all_bars)
    _patch_httpx(monkeypatch, server)

    bars = await ptr._bitunix_kline_fetcher(
        symbol="BTCUSDT.P", timeframe="1m",
        since_ms=start_ts, limit=50,
    )
    assert len(bars) == 50
    assert bars[0][0] == start_ts
    assert bars[-1][0] == start_ts + 49 * tf_ms


@pytest.mark.asyncio
async def test_kline_fetcher_empty_server_response_returns_empty_list(monkeypatch):
    """Sanity: empty server response (e.g. no data in requested window)
    yields an empty result without raising."""
    server = _FakeBitunixServer(all_bars=[])
    _patch_httpx(monkeypatch, server)
    bars = await ptr._bitunix_kline_fetcher(
        symbol="BTCUSDT.P", timeframe="1m",
        since_ms=1_779_000_000_000, limit=1000,
    )
    assert bars == []


# ── exact bug-reproduction at the trade-#1 layer ─────────────────────


def _trade1_pending_row() -> ptr._PendingRow:
    """Build a _PendingRow mirroring the actual prod row for trade #1
    (35aa49c9). All values from prod query 2026-05-20."""
    import json as _json
    tp_plan = [
        {"leg": "tp1", "fraction": 0.25, "target_r": 0.676,
         "price": 76269.86667999999, "stop_action": "move_to_breakeven"},
        {"leg": "tp2", "fraction": 0.50, "target_r": 1.0,
         "price": 76203.8963, "stop_action": "move_to_tp1"},
        {"leg": "tp3", "fraction": 0.25, "target_r": 2.5,
         "price": 75898.64074999999, "stop_action": "trail_atr"},
    ]
    return ptr._PendingRow(
        order_id="35aa49c9-bb62-4084-865f-5d839515cd81",
        ts="2026-05-18T16:24:02+00:00",
        strategy="bitunix_futures", division="bitunix_futures",
        symbol="BTC/USDT.P", side="sell", qty=0.00134421931122516,
        stop_price=76610.9037, tp_price=75898.64075,
        tp_r_multiple=2.5,
        entry_reference_price=76407.4,
        expected_loss=-0.547107206891546,
        expected_gain=None,  # prod row had NULL
        max_hold_seconds=86400,
        extra_json=_json.dumps({
            "tier": "STANDARD",
            "trigger_signal": "mc_b_sell_circle",
            "tp_plan": tp_plan,
            "tp_plan_version": "v2",
            "filled_legs": [],
            "current_sl": 76610.9037,
            "entry_reference_price": 76407.4,
            "stop_price": 76610.9037,
        }),
    )


@pytest.mark.asyncio
async def test_trade1_reproduces_observed_bug_with_truncated_bars(monkeypatch):
    """Reproduce the recorded prod outcome: filled_legs=[], result=loss,
    bars_to_resolution=1 — when the fetcher returns only the late bars
    (mimicking the bitunix 200-cap behavior at resolution time).

    This is the EXACT bug manifestation observed in prod for trade #1.
    """
    import json as _json
    row = _trade1_pending_row()
    extra = _json.loads(row.extra_json)
    # Simulate what the buggy fetcher returned at resolution time: a
    # single 1m bar at 5/19 05:44 whose high crosses original SL.
    # (Prod data shows the SL bar at this time had high ≈ 76,975-77,000.)
    bars = [
        [1779169440000, 76920.0, 76998.0, 76900.0, 76950.0, 1000.0],
        # ↑ single bar at 5/19 05:44, high 76,998 ≥ SL 76,610.9
    ]
    verdict = ptr._classify_v2_multi_leg(row, bars, extra)
    assert verdict.result == "loss", "Bug: with truncated bars, first walked bar's high ≥ original SL triggers immediate loss"
    assert verdict.actual_r_multiple == -1.0
    assert verdict.bars_to_resolution == 1
    assert verdict.extra_json_updates["filled_legs"] == []
    assert verdict.extra_json_updates["current_sl"] == 76610.9037  # unchanged
    # This matches the prod row 35aa49c9 exactly.


@pytest.mark.asyncio
async def test_trade1_with_correct_bars_yields_partial_win(monkeypatch):
    """When the fetcher returns the FULL trade window (entry 5/18 16:24
    through resolution 5/19 05:44 ≈ 800 1m bars), the v2 lifecycle SHOULD
    fill TP1 + TP2 in early bars, advance SL to TP1 floor (76,269.87),
    then close at TP1 when price retraces above current_sl.

    Expected R per Option C arithmetic (using plan's actual target_r
    values 0.676 / 1.0 / 0.676-via-aggregation):
        tp1: 0.25 × 0.676 = 0.169
        tp2: 0.50 × 1.000 = 0.500
        tp3 remainder (25%) exits at tp1 price (which is +0.676R from entry)
            = 0.25 × 0.676 = 0.169
        Total ≈ 0.838R win.

    Using the REAL 3m bar action from prod bitunix_bar_history (which has
    been verified end-to-end against price truth in the audit-integrity
    report), reconstructed here as a synthetic 1m-equivalent path:
      - 5/18 16:24: bar low 76,255 (below tp1 76,269.87)
      - 5/18 16:27: bar low 76,182 (below tp2 76,203.90)
      - 5/18 17:15: bar high 76,665 (above original SL — but now SL is at
        tp1 76,269.87, which was crossed by the SAME bar's open 76,328 or
        intra-bar movement)
    """
    import json as _json
    row = _trade1_pending_row()
    extra = _json.loads(row.extra_json)

    # Synthetic 1m bars approximating the actual price path. Spread the
    # 3m bar info across constituent 1m bars conservatively.
    # NB: only `time, open, high, low, close, volume` matter for the
    # classifier. Bars are in chronological order.
    bars = [
        # 5/18 16:24 — entry bar, low touches TP1 (76,269.87)
        [1779121440000, 76419.2, 76482.1, 76255.0, 76255.0, 1000.0],
        # 5/18 16:27 — low touches TP2 (76,203.90)
        [1779121620000, 76255.0, 76323.6, 76182.4, 76248.9, 1000.0],
        # 5/18 17:15 — bar that violates current_sl (which after TP2 fill
        # has been advanced to TP1 price 76,269.87). This bar's high 76,665
        # is well above 76,269.87 → SL hit at the TP1 floor.
        [1779124500000, 76528.5, 76665.2, 76528.5, 76616.5, 1000.0],
    ]

    verdict = ptr._classify_v2_multi_leg(row, bars, extra)
    # Lifecycle should have:
    assert verdict.extra_json_updates["filled_legs"] == ["tp1", "tp2"], (
        f"Expected TP1+TP2 fills; got {verdict.extra_json_updates['filled_legs']}"
    )
    # SL should have advanced to TP1 floor price.
    assert verdict.extra_json_updates["current_sl"] == 76269.86667999999, (
        f"Expected SL at TP1 floor 76269.87; got {verdict.extra_json_updates['current_sl']}"
    )
    # Net result: positive R (partial win), not -1.0 full loss.
    assert verdict.actual_r_multiple > 0, (
        f"Expected positive R (partial win); got {verdict.actual_r_multiple}"
    )
    assert verdict.result == "win"


@pytest.mark.asyncio
async def test_multi_tp_in_one_walk_yields_correct_sl_at_tp1_floor(monkeypatch):
    """Critical edge case: when TP1 and TP2 fill in the SAME walk (multiple
    bars but a single classifier pass), the SL must end at TP1 (not stuck at
    entry, not double-moved). Mirrors trade-#1's actual lifecycle where TP1
    and TP2 fill 3 minutes apart in the same replay tick."""
    import json as _json
    row = _trade1_pending_row()
    extra = _json.loads(row.extra_json)
    bars = [
        # bar 1: low touches TP1 → fill TP1, SL → entry
        [1779121440000, 76419.2, 76482.1, 76255.0, 76255.0, 1000.0],
        # bar 2: low touches TP2 → fill TP2, SL → TP1
        [1779121620000, 76255.0, 76323.6, 76182.4, 76248.9, 1000.0],
        # bar 3: nothing critical, just continues
        [1779121800000, 76248.9, 76388.8, 76200.0, 76373.6, 1000.0],
    ]
    verdict = ptr._classify_v2_multi_leg(row, bars, extra)
    # SL must land at TP1 floor after TP2 fills (NOT stuck at entry).
    assert verdict.extra_json_updates["current_sl"] == 76269.86667999999, (
        f"After TP1+TP2 fills in same walk, SL should be at TP1 floor "
        f"76,269.87; got {verdict.extra_json_updates['current_sl']}"
    )
    assert verdict.extra_json_updates["filled_legs"] == ["tp1", "tp2"]
