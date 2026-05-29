"""Tests for TelegramChannel push() audit-success semantics.

Verifies that:
- send_message returns a Message → telegram_notification_success row written
  with http_status=200 and message_id in detail; push returns True.
- send_message raises BadRequest → telegram_notification_failed row with
  http_status=400 and error in response_detail; push returns False.
- send_message raises TimedOut → failed row, http_status=None; returns False.
- _app is None → failed row, detail mentions "not started"; returns False.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.comms.telegram_bot import TelegramChannel
from trading_corp.persistence import db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREATE_AUDIT = """
CREATE TABLE IF NOT EXISTS audit_event (
    id           INTEGER PRIMARY KEY,
    ts           TEXT    NOT NULL,
    actor        TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    payload_json TEXT    NOT NULL
)
"""


def _make_channel(tmp_path: Path) -> TelegramChannel:
    """Create a TelegramChannel with a temp DB but no real Telegram app."""
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    # Create the audit_event table
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.execute(_CREATE_AUDIT)
    conn.commit()
    conn.close()
    chan = TelegramChannel(token="dummy", chat_id="123", db_url=db_url)
    return chan, db_url


def _read_audit_rows(db_url: str) -> list[dict]:
    from trading_corp.persistence.db import resolve_db_path
    path = resolve_db_path(db_url)
    conn = sqlite3.connect(str(path))
    rows = conn.execute(
        "SELECT kind, actor, payload_json FROM audit_event ORDER BY id"
    ).fetchall()
    conn.close()
    return [{"kind": r[0], "actor": r[1], "payload": json.loads(r[2])} for r in rows]


def _make_fake_app(send_message_mock: AsyncMock) -> MagicMock:
    app = MagicMock()
    app.bot = MagicMock()
    app.bot.send_message = send_message_mock
    return app


# ---------------------------------------------------------------------------
# Test 1: success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_success_writes_success_audit(tmp_path: Path):
    """send_message returns a Message → success audit row, push returns True."""
    import telegram  # noqa: F401  — needed for telegram.Message shape

    chan, db_url = _make_channel(tmp_path)
    msg_mock = SimpleNamespace(message_id=123)
    send_mock = AsyncMock(return_value=msg_mock)
    chan._app = _make_fake_app(send_mock)

    result = await chan.push("hello world")

    assert result is True
    rows = _read_audit_rows(db_url)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "telegram_notification_success"
    assert row["actor"] == "telegram_channel"
    assert row["payload"]["http_status"] == 200
    assert row["payload"]["ok"] is True
    assert "message_id=123" in row["payload"]["response_detail"]


# ---------------------------------------------------------------------------
# Test 2: BadRequest → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_bad_request_writes_failed_audit_400(tmp_path: Path):
    """BadRequest raises → failed row with http_status=400."""
    import telegram.error  # noqa: F401

    chan, db_url = _make_channel(tmp_path)
    err = telegram.error.BadRequest("Bad Request: can't parse entities")
    send_mock = AsyncMock(side_effect=err)
    chan._app = _make_fake_app(send_mock)

    result = await chan.push("bad msg")

    assert result is False
    rows = _read_audit_rows(db_url)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "telegram_notification_failed"
    assert row["payload"]["http_status"] == 400
    assert row["payload"]["ok"] is False
    assert "BadRequest" in row["payload"]["response_detail"]
    assert "parse entities" in row["payload"]["response_detail"].lower()


# ---------------------------------------------------------------------------
# Test 3: TimedOut → None http_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_timed_out_writes_failed_audit_no_status(tmp_path: Path):
    """TimedOut raises → failed row with http_status=None."""
    import telegram.error  # noqa: F401

    chan, db_url = _make_channel(tmp_path)
    err = telegram.error.TimedOut()
    send_mock = AsyncMock(side_effect=err)
    chan._app = _make_fake_app(send_mock)

    result = await chan.push("timeout msg")

    assert result is False
    rows = _read_audit_rows(db_url)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "telegram_notification_failed"
    assert row["payload"]["http_status"] is None
    assert row["payload"]["ok"] is False
    assert "TimedOut" in row["payload"]["response_detail"]


# ---------------------------------------------------------------------------
# Test 4: _app is None → drop + failed audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_app_none_writes_failed_audit(tmp_path: Path):
    """_app is None → drop silently + write failed audit, return False."""
    chan, db_url = _make_channel(tmp_path)
    # _app is already None at construction

    result = await chan.push("dropped")

    assert result is False
    rows = _read_audit_rows(db_url)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "telegram_notification_failed"
    assert row["payload"]["ok"] is False
    assert "not started" in row["payload"]["response_detail"].lower()
    assert row["payload"]["http_status"] is None


# ---------------------------------------------------------------------------
# Test 5: audit_path + audit_context are forwarded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_forwards_audit_path_and_context(tmp_path: Path):
    """audit_path and audit_context keys end up in the payload."""
    chan, db_url = _make_channel(tmp_path)
    msg_mock = SimpleNamespace(message_id=456)
    send_mock = AsyncMock(return_value=msg_mock)
    chan._app = _make_fake_app(send_mock)

    result = await chan.push(
        "test",
        audit_path="lifecycle_close_out",
        audit_context={"order_id": "ord-999"},
    )

    assert result is True
    rows = _read_audit_rows(db_url)
    assert rows[0]["payload"]["path"] == "lifecycle_close_out"
    assert rows[0]["payload"]["order_id"] == "ord-999"


# ---------------------------------------------------------------------------
# Test 6: Markdown 400 → plain-text fallback succeeds → success audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_markdown_fail_plain_fallback_succeeds(tmp_path: Path):
    """First send (Markdown) raises BadRequest; retry without parse_mode
    succeeds → ONE success row (plain fallback), push returns True."""
    import telegram.error  # noqa: F401

    chan, db_url = _make_channel(tmp_path)
    err = telegram.error.BadRequest("Bad Request: can't parse entities")
    msg_mock = SimpleNamespace(message_id=789)
    send_mock = AsyncMock(side_effect=[err, msg_mock])
    chan._app = _make_fake_app(send_mock)

    result = await chan.push("[PAPER] msg with brackets")

    assert result is True
    assert send_mock.await_count == 2  # markdown attempt + plain retry
    rows = _read_audit_rows(db_url)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "telegram_notification_success"
    assert row["payload"]["http_status"] == 200
    assert "message_id=789" in row["payload"]["response_detail"]
    assert "plain fallback" in row["payload"]["response_detail"]
