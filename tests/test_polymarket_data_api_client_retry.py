"""Tests for the Cloudflare-403 retry path in PolymarketDataAPIClient.

Covers:
  - `_get_json` retries on 403+cf-ray header, then succeeds.
  - `_get_json` raises `PolymarketRateLimitError` after the retry budget.
  - `fetch_market_resolutions` keeps partial results when one chunk's
    retries are exhausted (missing condition_ids fall through to the
    `not_found` sentinel).
  - `_is_cloudflare_block` heuristic: cf-ray header alone, body marker
    alone, neither (returns False).

Network-free; uses a fake AsyncClient.get that returns canned httpx.Response
objects.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from trading_corp.data import polymarket_data_api_client as pmc
from trading_corp.data.polymarket_data_api_client import (
    PolymarketDataAPIClient,
    PolymarketDataAPIError,
    PolymarketRateLimitError,
    _is_cloudflare_block,
)


def _response(status: int, *, headers: dict[str, str] | None = None,
              body: Any = None, body_text: str | None = None) -> httpx.Response:
    """Build an httpx.Response with the given status, headers, and body."""
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
    """Async callable; returns the next canned response on each call."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def __call__(self, url: str, params: Any = None) -> httpx.Response:
        self.calls += 1
        if not self._responses:
            raise AssertionError(
                f"FakeGet called more times than canned responses ({self.calls})"
            )
        return self._responses.pop(0)


# ── _is_cloudflare_block ──────────────────────────────────────────────────


def test_is_cloudflare_block_cf_ray_header():
    resp = _response(403, headers={"cf-ray": "abc123"}, body_text="anything")
    assert _is_cloudflare_block(resp) is True


def test_is_cloudflare_block_server_header():
    resp = _response(403, headers={"server": "cloudflare"}, body_text="x")
    assert _is_cloudflare_block(resp) is True


def test_is_cloudflare_block_body_marker():
    resp = _response(
        403, body_text="<html><body>Attention Required! | Cloudflare</body></html>",
    )
    assert _is_cloudflare_block(resp) is True


def test_is_cloudflare_block_returns_false_when_no_markers():
    resp = _response(403, headers={"server": "nginx"}, body_text="not us")
    assert _is_cloudflare_block(resp) is False


# ── _get_json retry behavior ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_json_retries_then_succeeds(monkeypatch):
    # Two 403+cf-ray retries, then 200 success.
    cf_resp = _response(
        403, headers={"cf-ray": "x"}, body_text="Cloudflare blocked",
    )
    ok_resp = _response(200, body=[{"hello": "world"}])
    fake = _FakeGet([cf_resp, cf_resp, ok_resp])
    sleeps: list[float] = []

    async def _no_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(pmc, "_CLOUDFLARE_RETRY_DELAYS_SEC", (0.01, 0.02, 0.03))
    monkeypatch.setattr(pmc.asyncio, "sleep", _no_sleep)

    async with PolymarketDataAPIClient() as client:
        client._client.get = fake  # type: ignore[assignment]
        payload = await client._get_json(
            "https://example/x", params={}, label="test",
        )

    assert payload == [{"hello": "world"}]
    assert fake.calls == 3
    assert sleeps == [0.01, 0.02]  # one sleep before each retry, not before success


@pytest.mark.asyncio
async def test_get_json_raises_rate_limit_after_exhaustion(monkeypatch):
    # All retries return cf-ray 403 — should raise PolymarketRateLimitError.
    cf_resp = _response(403, headers={"cf-ray": "x"}, body_text="Cloudflare")
    # budget = (0.01,) means 1 initial + 1 retry = 2 total attempts
    fake = _FakeGet([cf_resp, cf_resp])

    async def _no_sleep(d: float) -> None:
        pass

    monkeypatch.setattr(pmc, "_CLOUDFLARE_RETRY_DELAYS_SEC", (0.01,))
    monkeypatch.setattr(pmc.asyncio, "sleep", _no_sleep)

    async with PolymarketDataAPIClient() as client:
        client._client.get = fake  # type: ignore[assignment]
        with pytest.raises(PolymarketRateLimitError) as exc_info:
            await client._get_json("https://example/x", params={}, label="test")

    assert "Cloudflare" in str(exc_info.value)
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_get_json_non_cloudflare_403_does_not_retry(monkeypatch):
    # 403 WITHOUT Cloudflare markers should NOT retry — caller's fault.
    plain_403 = _response(
        403, headers={"server": "nginx"}, body_text="forbidden",
    )
    fake = _FakeGet([plain_403])

    monkeypatch.setattr(pmc, "_CLOUDFLARE_RETRY_DELAYS_SEC", (0.01, 0.02))

    async with PolymarketDataAPIClient() as client:
        client._client.get = fake  # type: ignore[assignment]
        with pytest.raises(PolymarketDataAPIError) as exc_info:
            await client._get_json("https://example/x", params={}, label="test")

    assert "HTTP 403" in str(exc_info.value)
    assert "Cloudflare" not in str(exc_info.value)  # generic error, not rate-limit
    assert fake.calls == 1  # no retry


# ── fetch_market_resolutions partial-coverage swallow ─────────────────────


@pytest.mark.asyncio
async def test_fetch_market_resolutions_partial_on_chunk_rate_limit(monkeypatch):
    """When one chunk's open/closed call exhausts the retry budget, the
    chunk's condition_ids fall through to `not_found` and the OTHER chunk's
    resolutions are returned cleanly."""
    # chunk_size=2 means [a,b] is chunk 0 and [c,d] is chunk 1.
    # Order of calls: chunk-0-open, chunk-0-closed, chunk-1-open, chunk-1-closed.
    chunk0_open = _response(200, body=[
        {"conditionId": "a", "closed": False, "outcomePrices": "[\"0.4\",\"0.6\"]",
         "outcomes": "[\"Yes\",\"No\"]", "question": "Q-a"},
        {"conditionId": "b", "closed": False, "outcomePrices": "[\"0.3\",\"0.7\"]",
         "outcomes": "[\"Yes\",\"No\"]", "question": "Q-b"},
    ])
    chunk0_closed = _response(200, body=[])
    cf = _response(403, headers={"cf-ray": "x"}, body_text="Cloudflare")
    # chunk 1 open: rate-limited (1 initial + 1 retry, both 403)
    # chunk 1 closed: also rate-limited
    fake = _FakeGet([
        chunk0_open, chunk0_closed,
        cf, cf,  # chunk-1-open: 1 initial + 1 retry → raises
        cf, cf,  # chunk-1-closed: same
    ])

    async def _no_sleep(d: float) -> None:
        pass

    monkeypatch.setattr(pmc, "_CLOUDFLARE_RETRY_DELAYS_SEC", (0.01,))
    monkeypatch.setattr(pmc.asyncio, "sleep", _no_sleep)

    async with PolymarketDataAPIClient() as client:
        client._client.get = fake  # type: ignore[assignment]
        result = await client.fetch_market_resolutions(
            ["a", "b", "c", "d"], chunk_size=2,
        )

    # Chunk 0 resolved cleanly.
    assert result["a"]["status"] == "pending"
    assert result["b"]["status"] == "pending"
    # Chunk 1 fell through to not_found because both retries on both variants
    # exhausted the budget.
    assert result["c"]["status"] == "not_found"
    assert result["d"]["status"] == "not_found"
    # Result is a complete dict — caller can iterate without KeyError.
    assert set(result.keys()) == {"a", "b", "c", "d"}


# ── _merge_watchlists ─────────────────────────────────────────────────────


def test_merge_preserves_included_iso_on_existing_entries():
    from trading_corp.scripts.seed_polymarket_watchlist_deep import (
        _merge_watchlists,
    )
    existing = [
        {
            "rank": 1, "proxy_wallet": "0xA", "user_name": "Alice",
            "realized_pnl_usdc": 5000.0, "included_iso": "2026-04-01T00:00:00+00:00",
        },
    ]
    fresh = [
        {
            "rank": 1, "proxy_wallet": "0xA", "user_name": "Alice",
            "realized_pnl_usdc": 6000.0,  # fresh has more PnL
            "included_iso": "2026-05-17T13:00:00+00:00",  # newer iso
        },
        {
            "rank": 2, "proxy_wallet": "0xB", "user_name": "Bob",
            "realized_pnl_usdc": 4000.0,
            "included_iso": "2026-05-17T13:00:00+00:00",
        },
    ]
    merged, stats = _merge_watchlists(existing, fresh, max_total=None)
    by_wallet = {m["proxy_wallet"]: m for m in merged}
    # 0xA preserves the OLD included_iso (we've been watching since April)
    assert by_wallet["0xA"]["included_iso"] == "2026-04-01T00:00:00+00:00"
    # but takes the FRESH realized_pnl_usdc (most-recent stats)
    assert by_wallet["0xA"]["realized_pnl_usdc"] == 6000.0
    # 0xB is new — gets the fresh iso.
    assert by_wallet["0xB"]["included_iso"] == "2026-05-17T13:00:00+00:00"
    assert stats["added"] == 1
    assert stats["replaced"] == 1
    assert stats["dropped"] == 0


def test_merge_ranks_by_realized_pnl_desc():
    from trading_corp.scripts.seed_polymarket_watchlist_deep import (
        _merge_watchlists,
    )
    existing = [
        {"proxy_wallet": "0xA", "realized_pnl_usdc": 100.0,
         "included_iso": "2026-04-01T00:00:00+00:00"},
        {"proxy_wallet": "0xB", "realized_pnl_usdc": 500.0,
         "included_iso": "2026-04-01T00:00:00+00:00"},
    ]
    fresh = [
        {"proxy_wallet": "0xC", "realized_pnl_usdc": 300.0,
         "included_iso": "2026-05-17T00:00:00+00:00"},
    ]
    merged, _stats = _merge_watchlists(existing, fresh, max_total=None)
    assert [m["proxy_wallet"] for m in merged] == ["0xB", "0xC", "0xA"]
    assert [m["rank"] for m in merged] == [1, 2, 3]


def test_merge_max_total_trims_lowest_pnl():
    from trading_corp.scripts.seed_polymarket_watchlist_deep import (
        _merge_watchlists,
    )
    existing = [
        {"proxy_wallet": "0xA", "realized_pnl_usdc": 100.0,
         "included_iso": "2026-04-01T00:00:00+00:00"},
        {"proxy_wallet": "0xB", "realized_pnl_usdc": 500.0,
         "included_iso": "2026-04-01T00:00:00+00:00"},
    ]
    fresh = [
        {"proxy_wallet": "0xC", "realized_pnl_usdc": 300.0,
         "included_iso": "2026-05-17T00:00:00+00:00"},
    ]
    merged, stats = _merge_watchlists(existing, fresh, max_total=2)
    assert [m["proxy_wallet"] for m in merged] == ["0xB", "0xC"]
    assert stats["dropped"] == 1
    # 0xA was the lowest-PnL entry, so it got dropped.
    assert all(m["proxy_wallet"] != "0xA" for m in merged)


def test_merge_handles_none_existing():
    """First-ever merge call (no existing slot) should treat as empty."""
    from trading_corp.scripts.seed_polymarket_watchlist_deep import (
        _merge_watchlists,
    )
    fresh = [
        {"proxy_wallet": "0xA", "realized_pnl_usdc": 100.0,
         "included_iso": "2026-05-17T00:00:00+00:00"},
    ]
    merged, stats = _merge_watchlists(None, fresh, max_total=None)
    assert len(merged) == 1
    assert merged[0]["proxy_wallet"] == "0xA"
    assert stats["added"] == 1
    assert stats["replaced"] == 0
