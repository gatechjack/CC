"""Tests for PolymarketDataAPIClient.fetch_positions_book -- FULL open-book paging + completeness (T1).

The `/positions` 100-cap was a DEFAULT parameter, not a hard limit; the client pages `limit=page_size` +
`offset` to a terminal (short) page and reports whether it saw the whole book. A test that only exercised a
single small page would NOT have caught the original under-count, so these cover: multi-page, single-page,
the exact-boundary case (a book that is precisely a whole number of pages), cross-page de-dup, and the
incomplete path (max_pages hit while pages stay full -> complete=False, and `fetch_positions` raises rather
than returning a silent partial).

`_get_json` is replaced on the instance so no network/httpx context is needed -- the paging logic is what is
under test.
"""
import pytest

from trading_corp.data.polymarket_data_api_client import (
    PolymarketDataAPIClient, PolymarketIncompletePositionsError,
)


def _raw(i):
    """A raw /positions row with a unique (conditionId, asset) so de-dup and counting are exact."""
    return {"proxyWallet": "0xw", "conditionId": "0x%05d" % i, "asset": "a%d" % i, "size": 100.0,
            "avgPrice": 0.5, "initialValue": 50.0, "currentValue": 55.0, "cashPnl": 5.0,
            "title": "T", "outcome": "Yes", "slug": "s", "eventSlug": "s",
            "outcomeIndex": 0, "redeemable": False, "curPrice": 0.55, "endDate": "2026-09-01"}


def _client_over(total, *, always_full=False):
    """Client whose _get_json serves rows[offset:offset+limit] from a synthetic book of `total` rows.
    always_full=True => every page is exactly `limit` long (the API never returns a short page)."""
    c = PolymarketDataAPIClient()

    async def fake(url, *, params, label):
        off = int(params["offset"]); lim = int(params["limit"])
        if always_full:
            return [_raw(off + j) for j in range(lim)]           # unique per offset -> never terminal
        return [_raw(j) for j in range(off, min(off + lim, total))]

    c._get_json = fake
    return c


@pytest.mark.parametrize("total", [0, 1, 157, 499])
async def test_single_page_complete(total):
    """A book that fits in one page (incl. empty and 1) terminates on the first (short) page."""
    c = _client_over(total)
    book = await c.fetch_positions_book("0xw", page_size=500, max_pages=40)
    assert book.complete and book.n == total and book.pages == 1


async def test_multi_page_complete():
    """BetMechanic-shaped: 1311 positions over 500 + 500 + 311 (short) = 3 pages, all rows, complete."""
    c = _client_over(1311)
    book = await c.fetch_positions_book("0xw", page_size=500, max_pages=40)
    assert book.complete and book.n == 1311 and book.pages == 3


async def test_exact_boundary_needs_trailing_short_page():
    """A book that is an EXACT multiple of page_size needs a trailing (empty) page to confirm the end --
    the classic off-by-one the single-small-book test would miss. 1000 = 500 + 500 + 0(empty)."""
    c = _client_over(1000)
    book = await c.fetch_positions_book("0xw", page_size=500, max_pages=40)
    assert book.complete and book.n == 1000 and book.pages == 3


async def test_dedupe_across_pages():
    """A book that SHIFTS between pages can repeat (conditionId, asset); de-dup keeps the count honest."""
    c = PolymarketDataAPIClient()

    async def fake(url, *, params, label):
        off = int(params["offset"])
        if off == 0:
            return [_raw(j) for j in range(500)]                 # 0..499
        if off == 500:
            return [_raw(j) for j in range(450, 700)]            # 450..699 (50 overlap), 250 rows -> terminal
        return []

    c._get_json = fake
    book = await c.fetch_positions_book("0xw", page_size=500, max_pages=40)
    assert book.complete and book.pages == 2 and book.n == 700   # 0..699 unique, the 50 repeats collapsed


async def test_incomplete_hits_max_pages_and_raises():
    """If pages never go short, max_pages fires -> complete=False; `fetch_positions` REFUSES a partial."""
    c = _client_over(0, always_full=True)
    book = await c.fetch_positions_book("0xw", page_size=500, max_pages=3)
    assert (not book.complete) and book.pages == 3 and book.n == 1500   # 3 full pages, all unique
    with pytest.raises(PolymarketIncompletePositionsError):
        await c.fetch_positions("0xw", page_size=500, max_pages=3)


async def test_fetch_positions_returns_full_book_when_complete():
    """The list contract now returns the COMPLETE book (not the first-page ~100)."""
    c = _client_over(1311)
    rows = await c.fetch_positions("0xw", page_size=500, max_pages=40)
    assert len(rows) == 1311
