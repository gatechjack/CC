"""Polymarket Data API v1 -> v2 migration tests (2026-11-17).

Covers the five migration parts of `trading_corp/data/polymarket_data_api_client.py`
and the consumer contracts the build rule pins ("every consumer must see exactly
what it saw from v1"):

  A. ENVELOPE — `{"data": [...], "pagination": {...}}` unwrap; a malformed
     envelope RAISES (never a silent empty that reads as "whale holds nothing"
     and diffs as a whale EXIT).
  B. CURSOR PAGINATION — v2 rejects `offset=` (HTTP 400, live-verified
     2026-11-17); the public offset contract is translated internally. No call
     may ever carry an `offset` param; sequential paging costs one request per
     page via the cursor cache; terminal = absence of `next_cursor`, never row
     count.
  C. FIELD REMAP — real captured v2 payloads (live, from the prod box,
     2026-11-17, probes analysis/pmh8.sh + analysis/pv2_probe2.sh) parse into
     dataclasses with v1 names/units/meanings, including the four v1-era
     `extra` keys `prediction_markets.paper` reads by name.
  D. TIMESTAMPS — v2 serves epoch SECONDS (same as v1). Tests fail if the unit
     were wrong by 1000x in EITHER direction, at both consumer sites that do
     arithmetic on client-delivered times:
       - execution.detect_exit_signals 300s window (live_driver.py:662 ->
         execution.py:875)
       - ingest.cp_to_record resolved_ts (ingest.py:143) -> the 30d recency
         gate (search.py:261)
     (Checked NOT client-fed, no test needed: `_SETTLED_LOOKBACK_SEC` filters
     KALSHI catalog close_ts, not Polymarket data; paper's grace/poll windows
     use our own clock; `pm_open_position.refreshed_ts` is our clock.)
  E. FAILURE IS LOUD — 410 / non-retryable 4xx / exhausted 5xx / exhausted 429
     / timeout / malformed envelope each raise AND emit a rate-limited
     `HARD-FAIL` ERROR log; 429 backs off with jitter (no hot loop); no path
     returns a silent empty.

Network-free; uses canned httpx.Response objects (pattern from
tests/test_polymarket_data_api_client_retry.py). Matcher-level side/leg
equivalence lives in tests/prediction_markets/test_v2_matcher_equivalence.py.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from trading_corp.data import polymarket_data_api_client as pmc
from trading_corp.data.polymarket_data_api_client import (
    ActivityRow,
    ClosedPositionRow,
    LeaderboardEntry,
    PolymarketDataAPIClient,
    PolymarketDataAPIError,
    PolymarketIncompletePositionsError,
    PolymarketRateLimitError,
    PositionRow,
)
from trading_corp.prediction_markets import paper
from trading_corp.prediction_markets import execution as pm_execution
from trading_corp.prediction_markets import ingest as pm_ingest
from trading_corp.prediction_markets import search as pm_search
from trading_corp.prediction_markets import live_driver as pm_live_driver


# ── REAL v2 payload fixtures (captured LIVE from the prod box, 2026-11-17, ──
# ── probes analysis/pmh8.sh + analysis/pv2_probe2.sh; all HTTP 200).       ──

# /v2/positions OPEN row — ITF whale 0x7462... "W75 Sydney: Aoi Ito vs
# Kristiana Sidorova" (outcome_index 0). opposite_token_id was truncated in
# the capture and is OMITTED (PositionRow does not consume it).
V2_POS_OPEN_ITF: dict[str, Any] = {
    "proxy_wallet": "0x746295855be6f49fe2da2accd69ea34b3cf23ac3",
    "token_id": "65871345570745679802811495716260998674370065504411551893093447824806706299043",
    "condition_id": "0x40f68115e597ab92f45c1adabbfa42bcf877a376feda37804af21ad79a6d6666",
    "current_size": 1742.0, "avg_price": 0.6, "entry_cost_usdc": 1045.2,
    "entry_fees_usdc": 20.904, "total_cost_usdc": 1066.104,
    "current_price": 0.9995, "current_value": 1741.129, "total_size": 1742.0,
    "realized_pnl": 0.0, "unrealized_pnl": 695.929, "total_pnl": 695.929,
    "percent_pnl": 66.5833, "percent_realized_pnl": 66.5833,
    "status": "OPEN", "redeemable": False, "mergeable": False,
    "negative_risk": False, "archived": False,
    "title": "W75 Sydney: Aoi Ito vs Kristiana Sidorova",
    "slug": "itf-ito1-sidoro1-2026-11-17",
    "event_id": "1400927", "event_slug": "itf-ito1-sidoro1-2026-11-17",
    "outcome": "Aoi Ito", "outcome_index": 0,
    "opposite_outcome": "Kristiana Sidorova",
    "end_date": "2026-11-17", "last_event_at": 1794886306,
    "first_entry_at": 1794884528, "name": "0x746...-1762436472554",
    "profile_image": "", "verified": False,
}

# /v2/positions OPEN row — whale 0x21f3... "Pepperdine Waves vs. Oregon Ducks:
# O/U 156.5", outcome "Under" at outcome_index 1 (the index-1 leg case).
V2_POS_OPEN_UNDER: dict[str, Any] = {
    "proxy_wallet": "0x21f333307ac6d2e65ac82f014b3dd2b64cbc35e3",
    "token_id": "20898142226554455622276391916568315719199832683600817907211912188944674134387",
    "condition_id": "0x51e7cd917fffae7e290f691042eaba4110f0c394cb3c560a628295a1cc4de85a",
    "current_size": 25613.3443, "avg_price": 0.5405, "entry_cost_usdc": 13846.3178,
    "entry_fees_usdc": 0.0, "total_cost_usdc": 13846.3178,
    "current_price": 0.505, "current_value": 12934.7388, "total_size": 25613.3443,
    "realized_pnl": 0.0, "unrealized_pnl": -911.579, "total_pnl": -911.579,
    "percent_pnl": -6.5835, "percent_realized_pnl": -6.5835,
    "status": "OPEN", "redeemable": False, "mergeable": False,
    "negative_risk": False, "archived": False,
    "title": "Pepperdine Waves vs. Oregon Ducks: O/U 156.5",
    "slug": "cbb-pepp-ore-2026-11-15-total-156pt5",
    "event_id": "1344915", "event_slug": "cbb-pepp-ore-2026-11-15",
    "outcome": "Under", "outcome_index": 1, "opposite_outcome": "Over",
    "opposite_token_id": "4557835200044398284775444978100598473242327216682455237474734118387176041928",
    "end_date": "2026-11-15", "last_event_at": 1794769463,
    "first_entry_at": 1794760652, "name": "hi774c",
    "profile_image": "", "verified": False,
}

# /v2/positions?status=CLOSED row — whale 0x21f3... "Will Belgium win on
# 2026-06-15?", holder's side "No" WON (current_price 1.0). Note v2 zeroes the
# cost fields on closed rows; total_size carries lifetime SHARES.
V2_POS_CLOSED_WIN: dict[str, Any] = {
    "proxy_wallet": "0x21f333307ac6d2e65ac82f014b3dd2b64cbc35e3",
    "token_id": "89400087379560118244676285293515988828989065128224151976744339630651750394158",
    "condition_id": "0x3ceb5b631318be5000758200590efe2b308a581d6787c3e7bc050bfd8f627627",
    "current_size": 0.0, "avg_price": 0.3416, "entry_cost_usdc": 0.0,
    "entry_fees_usdc": 0.0, "total_cost_usdc": 0.0,
    "current_price": 1.0, "current_value": 0.0, "total_size": 133299.2984,
    "realized_pnl": 87752.7943, "unrealized_pnl": 0.0, "total_pnl": 87752.7943,
    "percent_pnl": 0.0, "percent_realized_pnl": 192.6663,
    "status": "CLOSED", "redeemable": False, "mergeable": False,
    "negative_risk": True, "archived": False,
    "title": "Will Belgium win on 2026-06-15?",
    "slug": "fifwc-bel-egy-2026-06-15-bel",
    "event_id": "351728", "event_slug": "fifwc-bel-egy-2026-06-15",
    "outcome": "No", "outcome_index": 1, "opposite_outcome": "Yes",
    "opposite_token_id": "5699266164849713169703648234886411053444418385408086203990916861286572010447",
    "end_date": "2026-06-15", "last_event_at": 1781574820,
    "first_entry_at": 1781498086, "name": "hi774c",
    "profile_image": "", "verified": False,
}

# /v2/activity row — whale 0x21f3... "Pistons vs. Spurs" BUY.
V2_ACTIVITY_BUY: dict[str, Any] = {
    "proxy_wallet": "0x21f333307ac6d2e65ac82f014b3dd2b64cbc35e3",
    "timestamp": 1794890329,
    "condition_id": "0xef2c36e28301beaf3dfbf8cd9c98e31bddd47902840e25a5ea490ef7157f2ba6",
    "type": "TRADE", "size": 5.263158, "usdc_size": 1.263158,
    "transaction_hash": "0x942a7f10a00223222a5f8ff91dbf461e7a765e8f0517603a775f1fadfbe04ec2",
    "price": 0.2400000152,
    "token_id": "5153531828622363315073356742021002245977998988515638684104251992164726161155",
    "side": "BUY", "outcome_index": 0,
    "title": "Pistons vs. Spurs", "slug": "nba-det-sas-2026-11-17",
    "event_slug": "nba-det-sas-2026-11-17", "outcome": "Pistons",
    "name": "hi774c", "pseudonym": "Stylish-Jiffy", "bio": "",
    "profile_image": "", "profile_image_optimized": "",
}

# /v2/leaderboard row.
V2_LEADERBOARD_ROW: dict[str, Any] = {
    "rank": 1, "user_id": "0x6ac5bb06a9eb05641fd5e82640268b92f3ab4b6e",
    "pnl": 391235.4633065503, "volume": 556254.958979,
    "user_name": "robloxgamblemaster-2008-10-18", "profile_image": "",
    "x_username": "", "verified": False,
}


def _env(rows: list, next_cursor: str | None = None, **pg: Any) -> dict:
    """A v2 response envelope as the live API serves it."""
    pagination: dict[str, Any] = {"limit": len(rows), "offset": 0, **pg}
    pagination["has_more"] = next_cursor is not None
    pagination["next_cursor"] = next_cursor
    return {"data": rows, "pagination": pagination}


def _response(status: int, *, headers: dict[str, str] | None = None,
              body: Any = None, body_text: str | None = None) -> httpx.Response:
    if body_text is not None:
        content = body_text.encode("utf-8")
        h = dict(headers or {})
        h.setdefault("content-type", "text/html; charset=utf-8")
        return httpx.Response(status_code=status, headers=h, content=content)
    payload = json.dumps(body if body is not None else []).encode("utf-8")
    h = dict(headers or {})
    h.setdefault("content-type", "application/json")
    return httpx.Response(status_code=status, headers=h, content=payload)


class _FakeGet:
    """Async callable; returns (or raises) the next canned item; records
    every (url, params) so tests can assert cursor/offset behavior."""

    def __init__(self, items: list) -> None:
        self._items = list(items)
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, url: str, params: Any = None) -> httpx.Response:
        self.calls.append((url, dict(params or {})))
        if not self._items:
            raise AssertionError(f"FakeGet exhausted after {len(self.calls)} calls")
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _reset_hard_fail_logger():
    """The HARD-FAIL ERROR logger rate-limits per endpoint via module state;
    reset it so every test observes its own first-emit behavior."""
    pmc._err_log_state.clear()
    yield
    pmc._err_log_state.clear()


async def _with_client(fake: _FakeGet, coro_fn):
    async with PolymarketDataAPIClient() as client:
        client._client.get = fake  # type: ignore[assignment]
        return await coro_fn(client)


# ════════════════════════════════════ A. ENVELOPE ════════════════════════════


@pytest.mark.asyncio
async def test_positions_book_malformed_envelope_raises_not_empty(caplog):
    """THE regression test for the silent-empty hazard: under v1 code a dict
    payload became rows=[] + complete=True — an 'empty book', which the
    snapshot diff reads as the whale EXITING every position."""
    fake = _FakeGet([_response(200, body={"error": "nope"})])
    with caplog.at_level(logging.ERROR, logger=pmc.__name__):
        with pytest.raises(PolymarketDataAPIError) as ei:
            await _with_client(fake, lambda c: c.fetch_positions_book("0xabc"))
    assert "malformed v2 envelope" in str(ei.value)
    assert any("HARD-FAIL" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_activity_malformed_envelope_raises():
    fake = _FakeGet([_response(200, body={"data": "not-a-list"})])
    with pytest.raises(PolymarketDataAPIError):
        await _with_client(fake, lambda c: c.fetch_activity("0xabc"))


@pytest.mark.asyncio
async def test_closed_positions_malformed_envelope_raises():
    fake = _FakeGet([_response(200, body_text="null")])  # a JSON null payload
    with pytest.raises(PolymarketDataAPIError):
        await _with_client(fake, lambda c: c.fetch_closed_positions("0xabc"))


@pytest.mark.asyncio
async def test_empty_data_envelope_is_a_legitimate_empty():
    """A WELL-FORMED envelope with data=[] is a true 'wallet holds nothing' —
    allowed, and the book is complete (no cursor offered)."""
    fake = _FakeGet([_response(200, body=_env([]))])
    book = await _with_client(fake, lambda c: c.fetch_positions_book("0xabc"))
    assert book.complete is True and book.rows == [] and book.pages == 1


# ═══════════════════════════════ B. CURSOR PAGINATION ════════════════════════


@pytest.mark.asyncio
async def test_positions_book_cursor_pagination_three_pages():
    rows1 = [dict(V2_POS_OPEN_ITF, condition_id="0xc1"), dict(V2_POS_OPEN_UNDER, condition_id="0xc2")]
    rows2 = [dict(V2_POS_OPEN_ITF, condition_id="0xc3"), dict(V2_POS_OPEN_UNDER, condition_id="0xc4")]
    rows3 = [dict(V2_POS_OPEN_ITF, condition_id="0xc5")]
    fake = _FakeGet([
        _response(200, body=_env(rows1, next_cursor="CUR1")),
        _response(200, body=_env(rows2, next_cursor="CUR2")),
        _response(200, body=_env(rows3, next_cursor=None)),
    ])
    book = await _with_client(fake, lambda c: c.fetch_positions_book("0xabc", page_size=2))
    assert book.complete is True and book.pages == 3 and book.n == 5
    # call 1 has no cursor; calls 2-3 carry the prior next_cursor; NO offset ever.
    assert "cursor" not in fake.calls[0][1]
    assert fake.calls[1][1]["cursor"] == "CUR1"
    assert fake.calls[2][1]["cursor"] == "CUR2"
    assert all("offset" not in p for _u, p in fake.calls)
    assert all(u.endswith("/v2/positions") for u, _p in fake.calls)


@pytest.mark.asyncio
async def test_positions_book_short_page_with_cursor_keeps_paging():
    """Row count must NOT be read as terminal: a silently-capped `limit`
    serves short pages WITH a next_cursor. Only cursor absence ends the book."""
    rows_short = [dict(V2_POS_OPEN_ITF, condition_id="0xs1")]  # 1 row < page_size 5
    rows_last = [dict(V2_POS_OPEN_UNDER, condition_id="0xs2")]
    fake = _FakeGet([
        _response(200, body=_env(rows_short, next_cursor="MORE")),
        _response(200, body=_env(rows_last, next_cursor=None)),
    ])
    book = await _with_client(fake, lambda c: c.fetch_positions_book("0xabc", page_size=5))
    assert book.complete is True and book.pages == 2 and book.n == 2


@pytest.mark.asyncio
async def test_positions_book_max_pages_with_cursor_is_incomplete():
    pages = [_response(200, body=_env([dict(V2_POS_OPEN_ITF, condition_id=f"0xp{i}")],
                                      next_cursor=f"CUR{i}")) for i in range(3)]
    fake = _FakeGet(pages)
    book = await _with_client(
        fake, lambda c: c.fetch_positions_book("0xabc", page_size=1, max_pages=3))
    assert book.complete is False and book.pages == 3


@pytest.mark.asyncio
async def test_fetch_positions_raises_on_incomplete_book():
    pages = [_response(200, body=_env([dict(V2_POS_OPEN_ITF, condition_id=f"0xq{i}")],
                                      next_cursor=f"CUR{i}")) for i in range(2)]
    fake = _FakeGet(pages)
    with pytest.raises(PolymarketIncompletePositionsError):
        await _with_client(
            fake, lambda c: c.fetch_positions("0xabc", page_size=1, max_pages=2))


@pytest.mark.asyncio
async def test_positions_book_dedupes_on_v2_keys():
    """De-dupe key is the RAW v2 (condition_id, token_id) — a book that shifts
    between pages cannot double-count."""
    dup = dict(V2_POS_OPEN_ITF)  # same condition_id + token_id on both pages
    fake = _FakeGet([
        _response(200, body=_env([dup], next_cursor="CUR1")),
        _response(200, body=_env([dup], next_cursor=None)),
    ])
    book = await _with_client(fake, lambda c: c.fetch_positions_book("0xabc", page_size=1))
    assert book.n == 1 and book.complete is True


@pytest.mark.asyncio
async def test_offset_zero_is_a_single_call_without_cursor():
    fake = _FakeGet([_response(200, body=_env([V2_ACTIVITY_BUY], next_cursor="N1"))])
    rows = await _with_client(fake, lambda c: c.fetch_activity("0xAbC", limit=1, offset=0))
    assert len(rows) == 1 and len(fake.calls) == 1
    assert "cursor" not in fake.calls[0][1] and "offset" not in fake.calls[0][1]
    assert fake.calls[0][0].endswith("/v2/activity")


@pytest.mark.asyncio
async def test_sequential_offsets_hit_cursor_cache_one_call_per_page():
    """The paging shape every caller uses (loss_grounding.py:161,171;
    ingest.py:230): offset 0, L, 2L... must cost ONE request per page."""
    a2 = dict(V2_ACTIVITY_BUY, transaction_hash="0xbbb")
    a3 = dict(V2_ACTIVITY_BUY, transaction_hash="0xccc")
    fake = _FakeGet([
        _response(200, body=_env([V2_ACTIVITY_BUY], next_cursor="N1")),
        _response(200, body=_env([a2], next_cursor="N2")),
        _response(200, body=_env([a3], next_cursor=None)),
    ])

    async def run(c):
        out = []
        for off in (0, 1, 2):
            out.append(await c.fetch_activity("0xabc", limit=1, offset=off))
        return out

    pages = await _with_client(fake, run)
    assert [r[0].transaction_hash for r in pages] == [
        V2_ACTIVITY_BUY["transaction_hash"], "0xbbb", "0xccc"]
    assert len(fake.calls) == 3                      # one request per page — no walk
    assert fake.calls[1][1]["cursor"] == "N1"
    assert fake.calls[2][1]["cursor"] == "N2"
    assert all("offset" not in p for _u, p in fake.calls)


@pytest.mark.asyncio
async def test_cold_nonzero_offset_walks_forward_and_slices():
    """A cold offset (fresh client, no cache) walks from 0 and returns the
    right slice — v1-identical output, bounded cost."""
    a2 = dict(V2_ACTIVITY_BUY, transaction_hash="0xbbb")
    fake = _FakeGet([
        _response(200, body=_env([V2_ACTIVITY_BUY], next_cursor="N1")),
        _response(200, body=_env([a2], next_cursor=None)),
    ])
    rows = await _with_client(fake, lambda c: c.fetch_activity("0xabc", limit=1, offset=1))
    assert len(rows) == 1 and rows[0].transaction_hash == "0xbbb"
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_offset_at_or_past_end_returns_empty_like_v1():
    """The paging loops break on an empty page; an offset past the end must
    return [] (v1 semantics), not raise and not loop."""
    fake = _FakeGet([_response(200, body=_env([V2_ACTIVITY_BUY], next_cursor=None))])
    rows = await _with_client(fake, lambda c: c.fetch_activity("0xabc", limit=1, offset=5))
    assert rows == []


@pytest.mark.asyncio
async def test_closed_positions_sends_status_closed_on_v2_positions_path():
    fake = _FakeGet([_response(200, body=_env([V2_POS_CLOSED_WIN], next_cursor=None))])
    rows = await _with_client(fake, lambda c: c.fetch_closed_positions("0xabc", limit=50))
    assert len(rows) == 1
    url, params = fake.calls[0]
    assert url.endswith("/v2/positions") and params["status"] == "CLOSED"
    assert "offset" not in params


@pytest.mark.asyncio
async def test_leaderboard_v2_path_category_and_sort_params():
    fake = _FakeGet([_response(200, body=_env([V2_LEADERBOARD_ROW], next_cursor=None))])
    rows = await _with_client(
        fake, lambda c: c.fetch_leaderboard(category="Sports", limit=1, sort_by="pnl"))
    assert len(rows) == 1
    url, params = fake.calls[0]
    assert url.endswith("/v2/leaderboard")
    assert params["category"] == "Sports"
    assert params["sort_by"] == "pnl" and "sortBy" not in params
    assert "offset" not in params


# ═══════════════════════════════ C. FIELD REMAP ══════════════════════════════


def test_position_row_field_remap_real_itf_row():
    p = PositionRow.from_api(V2_POS_OPEN_ITF)
    assert p.proxy_wallet == "0x746295855be6f49fe2da2accd69ea34b3cf23ac3"
    assert p.condition_id == V2_POS_OPEN_ITF["condition_id"]
    assert p.asset == V2_POS_OPEN_ITF["token_id"]          # asset <- token_id
    assert p.size == pytest.approx(1742.0)                  # size <- current_size
    assert p.avg_price == pytest.approx(0.6)
    assert p.initial_value == pytest.approx(1045.2)         # <- entry_cost_usdc
    assert p.current_value == pytest.approx(1741.129)
    assert p.pnl == pytest.approx(695.929)                  # <- unrealized_pnl
    assert p.title == "W75 Sydney: Aoi Ito vs Kristiana Sidorova"
    assert p.outcome == "Aoi Ito"
    assert p.slug == "itf-ito1-sidoro1-2026-11-17"
    assert p.event_slug == "itf-ito1-sidoro1-2026-11-17"
    # v1 value identities, on the real payload: initialValue = size*avgPrice
    # (fee-exclusive) and cashPnl = currentValue - initialValue.
    assert p.initial_value == pytest.approx(p.size * p.avg_price, abs=0.01)
    assert p.pnl == pytest.approx(p.current_value - p.initial_value, abs=0.001)


def test_position_row_extra_contract_drives_real_paper_consumers():
    """The four v1-era extra keys paper.py reads BY NAME (paper.py:95-113).
    Without the synthesis, outcome_index defaults to 0 (WRONG LEG for every
    index-1 position) and curPrice to 0.0 (every position reads settled ->
    zero signals, silently)."""
    under = PositionRow.from_api(V2_POS_OPEN_UNDER)
    itf = PositionRow.from_api(V2_POS_OPEN_ITF)
    assert paper.pos_outcome_index(under) == 1      # the index-1 leg case
    assert paper.pos_outcome_index(itf) == 0
    assert paper.is_genuinely_open(under) is True   # curPrice 0.505
    assert paper.is_genuinely_open(itf) is True     # 0.9995 — open, barely
    assert paper.pos_end_date(under) == "2026-11-15"
    settled = PositionRow.from_api(dict(V2_POS_OPEN_UNDER, current_price=1.0))
    assert paper.is_genuinely_open(settled) is False
    zeroed = PositionRow.from_api(dict(V2_POS_OPEN_UNDER, current_price=0.0))
    assert paper.is_genuinely_open(zeroed) is False
    redeem = PositionRow.from_api(dict(V2_POS_OPEN_UNDER, redeemable=True))
    assert paper.is_genuinely_open(redeem) is False


def test_closed_row_field_remap_real_belgium_row():
    cp = ClosedPositionRow.from_api(V2_POS_CLOSED_WIN)
    assert cp.proxy_wallet == "0x21f333307ac6d2e65ac82f014b3dd2b64cbc35e3"
    assert cp.asset == V2_POS_CLOSED_WIN["token_id"]
    assert cp.total_bought == pytest.approx(133299.2984)    # <- total_size (SHARES)
    assert cp.realized_pnl == pytest.approx(87752.7943)
    assert cp.cur_price == pytest.approx(1.0)               # <- current_price (settled)
    assert cp.outcome == "No" and cp.outcome_index == 1
    assert cp.opposite_outcome == "Yes"
    assert cp.opposite_asset == V2_POS_CLOSED_WIN["opposite_token_id"]
    assert cp.end_date == "2026-06-15"
    assert cp.timestamp == 1781574820                       # <- last_event_at, SECONDS


def test_closed_row_shares_mapping_vs_apis_own_percentage():
    """total_bought <- total_size (SHARES) is proven against the API's OWN
    percent_realized_pnl on the same real row: realized / (shares*avg) must
    reproduce it. (This is the §13A(g) shares-notional semantic ingest
    depends on; a USDC mis-mapping would be off by ~3x here.)"""
    cp = ClosedPositionRow.from_api(V2_POS_CLOSED_WIN)
    cost = cp.total_bought * cp.avg_price
    assert cp.realized_pnl / cost == pytest.approx(
        V2_POS_CLOSED_WIN["percent_realized_pnl"] / 100.0, rel=1e-3)


def test_ingest_cp_to_record_value_parity_on_real_row():
    """ingest writes the same derived values v1 produced (ingest.py:111-146):
    cost_basis = total_bought*avg_price, shares_derived = total_bought/avg,
    won from cur_price>=0.9, resolved_ts from .timestamp."""
    cp = ClosedPositionRow.from_api(V2_POS_CLOSED_WIN)
    rec = pm_ingest.cp_to_record(cp, "soccer", "test", now_ts=1794900000)
    assert rec["wallet"] == cp.proxy_wallet
    assert rec["outcome_index"] == 1
    assert rec["total_bought"] == pytest.approx(133299.2984)
    assert rec["cost_basis"] == pytest.approx(133299.2984 * 0.3416, rel=1e-6)
    assert rec["shares_derived"] == pytest.approx(133299.2984 / 0.3416, rel=1e-6)
    assert rec["won"] == 1
    assert rec["resolved_ts"] == 1781574820


def test_activity_row_field_remap_real_row():
    a = ActivityRow.from_api(V2_ACTIVITY_BUY)
    assert a.proxy_wallet == "0x21f333307ac6d2e65ac82f014b3dd2b64cbc35e3"
    assert a.timestamp == 1794890329
    assert a.type == "TRADE" and a.side == "BUY"
    assert a.size == pytest.approx(5.263158)
    assert a.usdc_size == pytest.approx(1.263158)           # <- usdc_size
    assert a.transaction_hash.startswith("0x942a7f10")
    assert a.price == pytest.approx(0.2400000152)
    assert a.asset == V2_ACTIVITY_BUY["token_id"]           # <- token_id
    assert a.outcome_index == 0
    assert a.outcome == "Pistons" and a.slug == "nba-det-sas-2026-11-17"
    assert a.event_slug == "nba-det-sas-2026-11-17"


def test_leaderboard_entry_remap_real_row():
    e = LeaderboardEntry.from_api(V2_LEADERBOARD_ROW)
    assert e.proxy_wallet == "0x6ac5bb06a9eb05641fd5e82640268b92f3ab4b6e"  # <- user_id
    assert e.user_name == "robloxgamblemaster-2008-10-18"
    assert e.vol == pytest.approx(556254.958979)            # <- volume
    assert e.pnl == pytest.approx(391235.4633065503)
    assert e.verified_badge is False                         # <- verified
    assert e.rank == 1


def test_positions_to_entry_signals_from_real_v2_rows():
    """The live entry pipeline end-to-end at the client boundary: real v2 rows
    -> PositionRow -> CopySignals with the exact venue strings + leg index."""
    rows = [PositionRow.from_api(V2_POS_OPEN_ITF), PositionRow.from_api(V2_POS_OPEN_UNDER)]
    sigs = pm_live_driver.positions_to_entry_signals(rows, "0xwallet")
    assert len(sigs) == 2
    by_cid = {s.condition_id: s for s in sigs}
    itf = by_cid[V2_POS_OPEN_ITF["condition_id"]]
    under = by_cid[V2_POS_OPEN_UNDER["condition_id"]]
    assert itf.outcome == "Aoi Ito" and itf.outcome_index == 0
    assert itf.slug == "itf-ito1-sidoro1-2026-11-17"
    assert itf.title == "W75 Sydney: Aoi Ito vs Kristiana Sidorova"
    assert under.outcome == "Under" and under.outcome_index == 1
    assert not itf.is_exit and not under.is_exit
    # Determinism: the same book yields the same signal ids (idempotency root).
    sigs2 = pm_live_driver.positions_to_entry_signals(rows, "0xwallet")
    assert [s.signal_id for s in sigs2] == [s.signal_id for s in sigs]


# ═══════════════════════════════ D. TIMESTAMPS ═══════════════════════════════

_EPOCH_2017 = 1_500_000_000          # seconds-range guard rails
_EPOCH_2039 = 2_200_000_000


def test_v2_timestamps_parse_in_epoch_seconds_range():
    """Fails if the unit were ms (1.79e12) OR if a mistaken /1000 produced
    1.79e6 — in EITHER direction, for both consumed timestamp fields."""
    a = ActivityRow.from_api(V2_ACTIVITY_BUY)
    cp = ClosedPositionRow.from_api(V2_POS_CLOSED_WIN)
    assert _EPOCH_2017 < a.timestamp < _EPOCH_2039
    assert _EPOCH_2017 < cp.timestamp < _EPOCH_2039


def test_exit_window_consumer_pairs_in_seconds():
    """Consumer site 1: live_driver.activity_sells_from_activity (:662) ->
    execution.detect_exit_signals (:875) pairs |sell.ts - reduction.ts| <=
    300s. With the real v2 seconds value and a reduction detected 120s later
    the exit pairs; with a ms-corrupted sell ts it must NOT pair."""
    a = ActivityRow.from_api(dict(V2_ACTIVITY_BUY, side="SELL"))
    sells = pm_live_driver.activity_sells_from_activity([a], "0xw")
    assert sells and sells[0]["ts"] == 1794890329
    red = {"wallet": "0xw", "condition_id": a.condition_id,
           "outcome_index": a.outcome_index, "ts": a.timestamp + 120,
           "slug": a.slug, "outcome": a.outcome, "title": a.title}
    exits = pm_execution.detect_exit_signals(sells, [red], window_sec=300)
    assert len(exits) == 1 and exits[0].is_exit
    # ms corruption in either direction -> no pairing -> no exit.
    sells_ms = [dict(sells[0], ts=sells[0]["ts"] * 1000)]
    assert pm_execution.detect_exit_signals(sells_ms, [red], window_sec=300) == []
    sells_div = [dict(sells[0], ts=sells[0]["ts"] // 1000)]
    assert pm_execution.detect_exit_signals(sells_div, [red], window_sec=300) == []


def test_recency_gate_consumer_in_seconds():
    """Consumer site 2: ingest resolved_ts (:143) -> pm_category_stats ->
    search._is_recent (:261) `lt >= now - 30d*86400`. Seconds parity: recent
    inside 30d, not recent outside; a ms value would wrongly read 'recent'
    forever — pinned by the range test above plus the 40d case here."""
    cp = ClosedPositionRow.from_api(V2_POS_CLOSED_WIN)
    rec = pm_ingest.cp_to_record(cp, "soccer", "test", now_ts=1794900000)
    lt = rec["resolved_ts"]
    stat = SimpleNamespace(has_open_position=False, last_resolved_ts=lt)
    ok_10d, why = pm_search._is_recent(stat, now_ts=lt + 10 * 86400, window_days=30)
    assert ok_10d is True and why == "settled_recent"
    ok_40d, _ = pm_search._is_recent(stat, now_ts=lt + 40 * 86400, window_days=30)
    assert ok_40d is False
    # The ms-corruption direction: a ms timestamp would defeat the 40d cutoff.
    stat_ms = SimpleNamespace(has_open_position=False, last_resolved_ts=lt * 1000)
    bad, _ = pm_search._is_recent(stat_ms, now_ts=lt + 40 * 86400, window_days=30)
    assert bad is True  # documents WHY the seconds unit is load-bearing


# ═══════════════════════════════ E. FAILURE IS LOUD ══════════════════════════


@pytest.mark.asyncio
async def test_410_raises_tagged_and_error_logged(caplog):
    body = ('{"error":"Data API v1 has been retired",'
            '"migration":"https://data-api.polymarket.com/v2/"}')
    fake = _FakeGet([_response(410, body_text=body)])
    with caplog.at_level(logging.ERROR, logger=pmc.__name__):
        with pytest.raises(PolymarketDataAPIError) as ei:
            await _with_client(fake, lambda c: c.fetch_activity("0xabc"))
    msg = str(ei.value)
    assert "HTTP 410" in msg and "retired/gone" in msg
    hard = [r for r in caplog.records if "HARD-FAIL" in r.message]
    assert hard and "410" in hard[0].message
    assert fake.calls and len(fake.calls) == 1              # non-retryable: no retry


@pytest.mark.asyncio
async def test_410_on_positions_book_raises_never_empty_book():
    fake = _FakeGet([_response(410, body_text="gone")])
    with pytest.raises(PolymarketDataAPIError):
        await _with_client(fake, lambda c: c.fetch_positions_book("0xabc"))


@pytest.mark.asyncio
async def test_429_backs_off_with_jitter_then_raises(monkeypatch, caplog):
    fake = _FakeGet([_response(429), _response(429), _response(429)])
    sleeps: list[float] = []

    async def _no_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(pmc, "_RATE_LIMIT_RETRY_DELAYS_SEC", (0.01, 0.02))
    monkeypatch.setattr(pmc.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(pmc.random, "uniform", lambda a, b: 0.005)
    with caplog.at_level(logging.ERROR, logger=pmc.__name__):
        with pytest.raises(PolymarketRateLimitError) as ei:
            await _with_client(fake, lambda c: c._get_json(
                "https://data-api.polymarket.com/v2/activity", params={}, label="t"))
    assert "429" in str(ei.value)
    assert sleeps == [pytest.approx(0.015), pytest.approx(0.025)]  # base + jitter, EVERY retry sleeps
    assert len(fake.calls) == 3
    assert any("HARD-FAIL" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_429_then_success_retries_once(monkeypatch):
    ok = _response(200, body=_env([], next_cursor=None))
    fake = _FakeGet([_response(429), ok])
    sleeps: list[float] = []

    async def _no_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(pmc, "_RATE_LIMIT_RETRY_DELAYS_SEC", (0.01,))
    monkeypatch.setattr(pmc.asyncio, "sleep", _no_sleep)
    rows = await _with_client(fake, lambda c: c.fetch_activity("0xabc"))
    assert rows == [] and len(fake.calls) == 2 and len(sleeps) == 1


@pytest.mark.asyncio
async def test_5xx_exhaustion_raises_and_error_logs(monkeypatch, caplog):
    fake = _FakeGet([_response(502), _response(502), _response(502)])

    async def _no_sleep(d: float) -> None:
        pass

    monkeypatch.setattr(pmc, "_SERVER_ERROR_RETRY_DELAYS_SEC", (0.0, 0.0))
    monkeypatch.setattr(pmc.asyncio, "sleep", _no_sleep)
    with caplog.at_level(logging.ERROR, logger=pmc.__name__):
        with pytest.raises(PolymarketDataAPIError):
            await _with_client(fake, lambda c: c.fetch_activity("0xabc"))
    assert any("HARD-FAIL" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_timeout_raises_and_error_logs(caplog):
    fake = _FakeGet([httpx.ConnectTimeout("boom")])
    with caplog.at_level(logging.ERROR, logger=pmc.__name__):
        with pytest.raises(PolymarketDataAPIError) as ei:
            await _with_client(fake, lambda c: c.fetch_activity("0xabc"))
    assert "timeout" in str(ei.value).lower()
    assert any("HARD-FAIL" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_hard_fail_error_log_is_rate_limited_per_endpoint(monkeypatch, caplog):
    """One ERROR line per endpoint per interval; suppressed failures are
    counted on the next emitted line. (The v1 retirement produced ~19
    WARNING lines/sec for two weeks — this is the flood guard.)"""
    t = {"now": 1000.0}
    monkeypatch.setattr(pmc.time, "monotonic", lambda: t["now"])
    with caplog.at_level(logging.ERROR, logger=pmc.__name__):
        pmc._log_hard_failure("data-api/v2/positions", "m1")
        pmc._log_hard_failure("data-api/v2/positions", "m2")   # inside interval -> suppressed
        pmc._log_hard_failure("data-api/v2/positions", "m3")   # suppressed
        t["now"] += pmc._ERROR_LOG_INTERVAL_SEC + 1
        pmc._log_hard_failure("data-api/v2/positions", "m4")   # emits + reports 2 suppressed
    msgs = [r.message for r in caplog.records if "HARD-FAIL" in r.message]
    assert len(msgs) == 2
    assert "m1" in msgs[0]
    assert "m4" in msgs[1] and "+2 similar suppressed" in msgs[1]


@pytest.mark.asyncio
async def test_non_cloudflare_403_still_raises_without_retry():
    """v1 behavior preserved: a plain 403 (no CF markers) is the caller's
    problem — one attempt, generic error."""
    fake = _FakeGet([_response(403, headers={"server": "nginx"}, body_text="forbidden")])
    with pytest.raises(PolymarketDataAPIError) as ei:
        await _with_client(fake, lambda c: c.fetch_activity("0xabc"))
    assert "HTTP 403" in str(ei.value) and len(fake.calls) == 1
