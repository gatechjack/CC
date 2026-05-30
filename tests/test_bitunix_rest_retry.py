"""Tests for the BitUnix REST retry/backoff layer (gate (a) sub-item 1, 2026-05-30).

Mocked at the `httpx.AsyncClient` boundary (per `[[mocks-dont-catch-sdk-shape]]`):
the broker's real `_request` body runs unchanged, signs the wire payload,
issues GET/POST against the fake client, and the retry decision logic exercises
real exception types.

Test matrix covers the seven required cases from the gate (a) spec plus three
defense-in-depth checks (audit-row content, wallclock cap, 429 transient).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import httpx
import pytest

from trading_corp.brokers import bitunix as bx
from trading_corp.brokers.bitunix import (
    BitunixAPIError,
    BitunixBroker,
)


# ---------------------------------------------------------------------------
# Mock infra — extends the pattern in tests/test_bitunix_broker_write.py with
# the ability to queue exceptions (raised from the .get/.post call) or
# FakeResp instances that raise from raise_for_status().
# ---------------------------------------------------------------------------

class FakeResp:
    def __init__(self, payload: dict | None = None, *,
                 raise_on_status: Exception | None = None):
        self._payload = payload or {"code": 0, "msg": "Success", "data": {}}
        self._raise_on_status = raise_on_status
        self.status_code = 200 if raise_on_status is None else (
            raise_on_status.response.status_code
            if isinstance(raise_on_status, httpx.HTTPStatusError)
            else 0
        )

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self._raise_on_status is not None:
            raise self._raise_on_status
        return None


class MockClient:
    """Stand-in for httpx.AsyncClient. Records calls and replays a per-path
    queue. A queued entry may be:
      * `dict` — wrapped in FakeResp and returned (envelope success/error)
      * `FakeResp` — returned directly (lets a test set raise_on_status)
      * `Exception` instance — raised from .get/.post (network-level failure)
    Last entry of a queue repeats so steady-state loops keep getting it."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses: dict[str, list[Any]] = {}

    def queue(self, path: str, *items: Any) -> None:
        self._responses.setdefault(path, []).extend(items)

    def _pop(self, path: str) -> Any:
        q = self._responses.get(path)
        if not q:
            return FakeResp({"code": 0, "msg": "Success", "data": {}})
        return q.pop(0) if len(q) > 1 else q[0]

    async def get(self, path, params=None, headers=None):
        self.calls.append({
            "method": "GET", "path": path, "params": params,
            "content": None, "headers": dict(headers or {}),
        })
        item = self._pop(path)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, FakeResp):
            return item
        return FakeResp(item)

    async def post(self, path, content=None, headers=None):
        self.calls.append({
            "method": "POST", "path": path, "params": None,
            "content": content, "headers": dict(headers or {}),
        })
        item = self._pop(path)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, FakeResp):
            return item
        return FakeResp(item)


class FakeLogger:
    """Minimal LoggerAgent stand-in. `log_event` records every call."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log_event(self, *, actor: str, kind: str, payload: dict) -> int:
        self.events.append({"actor": actor, "kind": kind, "payload": payload})
        return len(self.events)  # row_id stand-in


API_KEY = "key_retry"
API_SECRET = "secret_retry"
PATH_GET = "/api/v1/futures/position/get_pending_positions"
PATH_POST = "/api/v1/futures/trade/place_order"


def _make_broker(*, with_logger: bool = True) -> tuple[BitunixBroker, MockClient, FakeLogger | None]:
    logger = FakeLogger() if with_logger else None
    broker = BitunixBroker(api_key=API_KEY, api_secret=API_SECRET, logger=logger)
    client = MockClient()
    broker._client = client  # type: ignore[assignment]
    return broker, client, logger


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://fapi.bitunix.com/")
    resp = httpx.Response(status, request=req)
    return httpx.HTTPStatusError(f"{status}", request=req, response=resp)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Drop real waits — the broker's `await asyncio.sleep(delay)` becomes
    a coroutine that resolves immediately. Tests still observe `delay`
    through the patched `random.uniform`."""
    async def fake_sleep(delay):
        return None
    monkeypatch.setattr(bx.asyncio, "sleep", fake_sleep)


@pytest.fixture(autouse=True)
def _deterministic_jitter(monkeypatch):
    """Pin jitter to the lower bound so backoff durations are predictable
    in tests (delay = base/2, exactly the lo edge of the full-jitter band)."""
    monkeypatch.setattr(
        bx.random, "uniform",
        lambda lo, _hi: lo,
    )


# ---------------------------------------------------------------------------
# Spec test 1 — happy path: 1 attempt succeeds → 0 retries, no audit row.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_happy_path_no_retry_no_audit():
    broker, client, logger = _make_broker()
    client.queue(PATH_GET, {"code": 0, "data": [{"foo": "bar"}]})

    data = await broker._request("GET", PATH_GET)

    assert data == [{"foo": "bar"}]
    assert len(client.calls) == 1
    assert logger.events == []  # no retry → no audit row


# ---------------------------------------------------------------------------
# Spec test 2 — transient 503 then success: 1 retry, 1 audit row.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_503_then_success_retries_once_and_audits():
    broker, client, logger = _make_broker()
    client.queue(
        PATH_GET,
        FakeResp(raise_on_status=_http_status_error(503)),
        {"code": 0, "data": {"ok": True}},
    )

    data = await broker._request("GET", PATH_GET)

    assert data == {"ok": True}
    assert len(client.calls) == 2
    assert len(logger.events) == 1
    ev = logger.events[0]
    assert ev["actor"] == "bitunix_broker"
    assert ev["kind"] == "rest_request_retried"
    assert ev["payload"]["attempts"] == 2
    assert "503" in ev["payload"]["last_error"]
    assert ev["payload"]["method"] == "GET"
    assert ev["payload"]["path"] == PATH_GET
    assert ev["payload"]["wallclock_used_s"] >= 0.0


# ---------------------------------------------------------------------------
# Spec test 3 — hard 401: not transient → 0 retries, raises.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_401_is_not_transient_no_retry():
    broker, client, logger = _make_broker()
    client.queue(
        PATH_GET,
        FakeResp(raise_on_status=_http_status_error(401)),
    )

    with pytest.raises(httpx.HTTPStatusError) as ei:
        await broker._request("GET", PATH_GET)
    assert ei.value.response.status_code == 401
    assert len(client.calls) == 1
    assert logger.events == []


# ---------------------------------------------------------------------------
# Spec test 4 — timeout 4× (initial + 3 retries) exhausts the retry budget.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_timeout_exhausts_retries():
    broker, client, logger = _make_broker()
    # Same TimeoutException repeats (last-item-stays semantic of MockClient).
    client.queue(PATH_GET, httpx.ReadTimeout("connection timed out"))

    with pytest.raises(httpx.ReadTimeout):
        await broker._request("GET", PATH_GET)

    # 1 initial + 3 retries = 4 total attempts.
    assert len(client.calls) == 4
    # No audit row on exhaustion: audit fires only on retried-then-succeeded.
    assert logger.events == []


# ---------------------------------------------------------------------------
# Spec test 5 — POST with clientId + 503 then 30042 (clientId duplicate):
# the retry succeeds at the envelope layer with code=30042; place_order
# turns this into a "treated-as-already-placed" success per the existing
# _IDEMPOTENT_OK_CODES handling. Here we test the _request-level behavior:
# 30042 is raised as BitunixAPIError (not retried), but the retry on the
# initial 503 fired.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_with_clientid_503_then_30042_retries_and_raises_30042():
    broker, client, logger = _make_broker()
    client.queue(
        PATH_POST,
        FakeResp(raise_on_status=_http_status_error(503)),
        {"code": 30042, "msg": "duplicate clientId", "data": None},
    )
    body = {"symbol": "BTCUSDT", "clientId": "tc-abc"}

    with pytest.raises(BitunixAPIError) as ei:
        await broker._request("POST", PATH_POST, body=body)
    assert ei.value.code == 30042
    # 2 calls — retry fired because of 503, then 30042 raised (not transient).
    assert len(client.calls) == 2
    # No audit row: retry was the LAST attempt and it raised, not succeeded.
    assert logger.events == []


# ---------------------------------------------------------------------------
# Spec test 6 — POST without clientId + 503: no retry, raises immediately.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_without_clientid_does_not_retry_on_503():
    broker, client, logger = _make_broker()
    client.queue(
        PATH_POST,
        FakeResp(raise_on_status=_http_status_error(503)),
    )
    body = {"symbol": "BTCUSDT"}  # no clientId

    with pytest.raises(httpx.HTTPStatusError):
        await broker._request("POST", PATH_POST, body=body)
    assert len(client.calls) == 1
    assert logger.events == []


# ---------------------------------------------------------------------------
# Spec test 7 — sign stability: retry uses NEW nonce/timestamp but SAME body.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_uses_new_nonce_timestamp_but_same_body():
    broker, client, _ = _make_broker()
    client.queue(
        PATH_POST,
        FakeResp(raise_on_status=_http_status_error(503)),
        {"code": 0, "data": {"orderId": "OID1"}},
    )
    body = {
        "symbol": "BTCUSDT", "side": "BUY", "qty": "0.001",
        "tradeSide": "OPEN", "clientId": "tc-xyz",
    }

    await broker._request("POST", PATH_POST, body=body)

    posts = [c for c in client.calls if c["method"] == "POST" and c["path"] == PATH_POST]
    assert len(posts) == 2
    # SAME body bytes — exact JSON content unchanged across attempts.
    assert posts[0]["content"] == posts[1]["content"]
    body_str = posts[0]["content"]
    assert body_str == json.dumps(body, separators=(",", ":"))

    # FRESH nonce + timestamp — both must differ across attempts.
    h0, h1 = posts[0]["headers"], posts[1]["headers"]
    assert h0["nonce"] != h1["nonce"], "nonce reused across retry — server would reject"
    # Timestamp normally differs, but on a fast-enough mock the ms could
    # collide — what we actually need is the sign-stability invariant: the
    # signature recomputed by the test from (nonce, timestamp, body) matches.
    for h, p in zip([h0, h1], posts):
        expected_digest = hashlib.sha256(
            (h["nonce"] + h["timestamp"] + API_KEY + "" + p["content"]).encode("utf-8")
        ).hexdigest()
        expected_sign = hashlib.sha256(
            (expected_digest + API_SECRET).encode("utf-8")
        ).hexdigest()
        assert h["sign"] == expected_sign


# ---------------------------------------------------------------------------
# Defense-in-depth — 429 is a transient HTTP status.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_429_retries_then_succeeds():
    broker, client, logger = _make_broker()
    client.queue(
        PATH_GET,
        FakeResp(raise_on_status=_http_status_error(429)),
        {"code": 0, "data": []},
    )

    data = await broker._request("GET", PATH_GET)
    assert data == []
    assert len(client.calls) == 2
    assert len(logger.events) == 1
    assert "429" in logger.events[0]["payload"]["last_error"]


# ---------------------------------------------------------------------------
# Defense-in-depth — BitunixAPIError rate-limit code (10005) is transient.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_api_error_10005_retries_then_succeeds():
    broker, client, logger = _make_broker()
    client.queue(
        PATH_GET,
        {"code": 10005, "msg": "rate limited", "data": None},
        {"code": 0, "data": []},
    )

    data = await broker._request("GET", PATH_GET)
    assert data == []
    assert len(client.calls) == 2
    assert "10005" in logger.events[0]["payload"]["last_error"]


# ---------------------------------------------------------------------------
# Defense-in-depth — non-retryable BitunixAPIError (20003) raises immediately.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_api_error_non_retryable_raises_immediately():
    broker, client, logger = _make_broker()
    client.queue(
        PATH_GET,
        {"code": 20003, "msg": "insufficient balance", "data": None},
    )

    with pytest.raises(BitunixAPIError) as ei:
        await broker._request("GET", PATH_GET)
    assert ei.value.code == 20003
    assert len(client.calls) == 1
    assert logger.events == []


# ---------------------------------------------------------------------------
# Defense-in-depth — wallclock cap fails fast rather than violate the budget.
# Patch the cap and delay so the first retry would already exceed it.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_wallclock_cap_fails_fast(monkeypatch):
    monkeypatch.setattr(bx, "_RETRY_WALLCLOCK_CAP_S", 0.1)
    # Force the delay function to return a value larger than the cap so
    # the wallclock check trips on the first retry decision.
    monkeypatch.setattr(bx, "_retry_backoff_delay", lambda attempt: 0.5)

    broker, client, logger = _make_broker()
    client.queue(PATH_GET, httpx.ReadTimeout("timeout"))

    with pytest.raises(httpx.ReadTimeout):
        await broker._request("GET", PATH_GET)
    # Only the initial attempt — wallclock would exceed cap on retry.
    assert len(client.calls) == 1
    assert logger.events == []


# ---------------------------------------------------------------------------
# Defense-in-depth — audit row is best-effort: a raising logger does NOT
# break the success path.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audit_logger_failure_does_not_block_success():
    broker, client, _ = _make_broker(with_logger=False)

    class RaisingLogger:
        def log_event(self, *, actor, kind, payload):
            raise RuntimeError("audit DB down")

    broker.logger = RaisingLogger()
    client.queue(
        PATH_GET,
        FakeResp(raise_on_status=_http_status_error(503)),
        {"code": 0, "data": {"ok": True}},
    )

    data = await broker._request("GET", PATH_GET)
    assert data == {"ok": True}


# ---------------------------------------------------------------------------
# Defense-in-depth — broker without a logger silently skips audit (no-op).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_broker_without_logger_skips_audit(monkeypatch):
    broker = BitunixBroker(api_key=API_KEY, api_secret=API_SECRET)
    assert broker.logger is None
    client = MockClient()
    broker._client = client  # type: ignore[assignment]
    client.queue(
        PATH_GET,
        FakeResp(raise_on_status=_http_status_error(503)),
        {"code": 0, "data": {"ok": True}},
    )

    data = await broker._request("GET", PATH_GET)
    assert data == {"ok": True}  # success, no exception from missing audit
