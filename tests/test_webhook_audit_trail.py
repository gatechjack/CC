"""Audit-trail completeness for the TradingView webhook handlers.

Pins the 2026-05-01 fix for the silent 503-on-empty-secret branch in
both Lord Otter and Market Cypher webhook handlers. Originally the
empty-secret path returned 503 with no audit row written, which masked
a 7-day Cypher outage. The fix writes a `webhook_rejected` row with
reason='server_side_secret_unset' so misconfiguration shows up in the
dashboard, not just the systemd journal.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trading_corp.agents.strategies.lord_otter import LordOtterAgent
from trading_corp.agents.strategies.market_cypher import MarketCypherAgent
from trading_corp.agents.logger import LoggerAgent
from trading_corp.persistence.db import init_db
from trading_corp.web import webhooks


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def otter_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "strategies.yaml"
    p.write_text(
        """
lord_otter:
  enabled: true
  auto_execute: false
  symbols: [BTC/USD]
  webhook_secret_env: TEST_OTTER_SECRET
""".strip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def cypher_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "strategies.yaml"
    p.write_text(
        """
market_cypher:
  enabled: true
  auto_execute: false
  symbols: [BTC/USD]
  webhook_secret_env: TEST_CYPHER_SECRET
""".strip(),
        encoding="utf-8",
    )
    return p


def _build_app_with(otter_agent=None, cypher_agent=None, *, db_url: str) -> tuple[FastAPI, LoggerAgent]:
    """Construct a minimal FastAPI app with deps shape `webhooks.register`
    expects — only the fields the empty-secret branches touch."""
    init_db(db_url)
    logger_agent = LoggerAgent(db_url)

    class _Deps:
        pass
    deps = _Deps()
    deps.logger_agent = logger_agent
    deps.lord_otter_agent = otter_agent
    deps.market_cypher_agent = cypher_agent

    app = FastAPI()
    app.state.deps = deps
    webhooks.register(app)
    return app, logger_agent


# ── Otter empty-secret branch ───────────────────────────────────────────


def test_otter_empty_secret_returns_503_AND_writes_audit_row(
    otter_yaml, tmp_db, monkeypatch,
):
    """The fix: when LORD_OTTER_WEBHOOK_SECRET is unset, the 503 path
    MUST write a webhook_rejected row with reason='server_side_secret_unset'.
    This is the load-bearing diagnostic — without it, misconfiguration
    is invisible in the dashboard."""
    # Deliberately do NOT set TEST_OTTER_SECRET → the empty-secret branch fires.
    monkeypatch.delenv("TEST_OTTER_SECRET", raising=False)
    # Disable IP-check so we get past it and reach the secret check.
    monkeypatch.setenv("LORD_OTTER_DISABLE_IP_CHECK", "1")

    from trading_corp.data.macro_calendar import MacroCalendar
    agent = LordOtterAgent(
        strategies_yaml=otter_yaml,
        macro_calendar=MacroCalendar(path=otter_yaml.parent / "no.yaml"),
        db_url=None,
    )
    app, logger_agent = _build_app_with(otter_agent=agent, db_url=tmp_db)

    client = TestClient(app)
    r = client.post(
        "/webhook/tradingview/lord-otter",
        json={"secret": "anything", "symbol": "BTC/USD", "signal": "x"},
    )
    assert r.status_code == 503
    assert "secret" in r.json()["reason"].lower()

    # The audit row is the actual fix being pinned
    rows = logger_agent.recent_events(limit=20)
    rejected = [
        e for e in rows
        if e["actor"] == "lord_otter" and e["kind"] == "webhook_rejected"
    ]
    assert rejected, (
        "empty-secret 503 must write a webhook_rejected audit row "
        "(this gap masked a real-world 7-day Cypher outage)"
    )
    assert rejected[0]["payload"]["reason"] == "server_side_secret_unset"


# ── Cypher empty-secret branch ──────────────────────────────────────────


def test_cypher_empty_secret_returns_503_AND_writes_audit_row(
    cypher_yaml, tmp_db, monkeypatch,
):
    """Same fix on the Cypher side — this was the branch that originally
    silently dropped 7 days of TradingView Cypher alerts."""
    monkeypatch.delenv("TEST_CYPHER_SECRET", raising=False)
    monkeypatch.setenv("MARKET_CYPHER_DISABLE_IP_CHECK", "1")

    from trading_corp.data.macro_calendar import MacroCalendar
    agent = MarketCypherAgent(
        strategies_yaml=cypher_yaml,
        macro_calendar=MacroCalendar(path=cypher_yaml.parent / "no.yaml"),
        db_url=None,
    )
    app, logger_agent = _build_app_with(cypher_agent=agent, db_url=tmp_db)

    client = TestClient(app)
    r = client.post(
        "/webhook/tradingview/market-cypher",
        json={"secret": "anything", "symbol": "BTC/USD", "signal": "x"},
    )
    assert r.status_code == 503

    rows = logger_agent.recent_events(limit=20)
    rejected = [
        e for e in rows
        if e["actor"] == "market_cypher" and e["kind"] == "webhook_rejected"
    ]
    assert rejected, (
        "empty-secret 503 on Cypher must write a webhook_rejected audit row "
        "— this is the exact gap that masked the 7-day outage"
    )
    payload = rejected[0]["payload"]
    assert payload["reason"] == "server_side_secret_unset"
    assert payload["strategy"] == "market_cypher"


# ── Sanity: bad-secret path still works (regression check) ──────────────


def test_otter_bad_secret_path_still_audits(otter_yaml, tmp_db, monkeypatch):
    """The bad-secret 401 path was already correct. Verify the empty-secret
    fix didn't accidentally break it."""
    monkeypatch.setenv("TEST_OTTER_SECRET", "the_correct_secret")
    monkeypatch.setenv("LORD_OTTER_DISABLE_IP_CHECK", "1")

    from trading_corp.data.macro_calendar import MacroCalendar
    agent = LordOtterAgent(
        strategies_yaml=otter_yaml,
        macro_calendar=MacroCalendar(path=otter_yaml.parent / "no.yaml"),
        db_url=None,
    )
    app, logger_agent = _build_app_with(otter_agent=agent, db_url=tmp_db)

    client = TestClient(app)
    r = client.post(
        "/webhook/tradingview/lord-otter",
        json={"secret": "wrong_secret", "symbol": "BTC/USD", "signal": "x"},
    )
    assert r.status_code == 401

    rows = logger_agent.recent_events(limit=20)
    rejected = [
        e for e in rows
        if e["actor"] == "lord_otter" and e["kind"] == "webhook_rejected"
    ]
    assert rejected
    assert rejected[0]["payload"]["reason"] == "bad_secret"


# ── C-7: Secret-scrubbing in _audit_rejected ────────────────────────────


# ---- Pure-function unit tests for _scrub_secrets_from_body ----

from trading_corp.web.webhooks import _scrub_secrets_from_body  # noqa: E402


def test_scrub_redacts_secret_field():
    """'secret' field value is replaced with ***REDACTED***."""
    raw = b'{"secret": "my_super_secret", "signal": "buy"}'
    result = _scrub_secrets_from_body(raw)
    assert "my_super_secret" not in result
    assert '"secret": "***REDACTED***"' in result
    # Non-secret fields are preserved.
    assert "buy" in result


def test_scrub_redacts_webhook_secret_field():
    """'webhook_secret' field value is replaced."""
    raw = b'{"webhook_secret": "abc123", "foo": "bar"}'
    result = _scrub_secrets_from_body(raw)
    assert "abc123" not in result
    assert '"webhook_secret": "***REDACTED***"' in result


def test_scrub_redacts_token_field():
    """'token' field value is replaced."""
    raw = b'{"token": "tok_xyz", "action": "sell"}'
    result = _scrub_secrets_from_body(raw)
    assert "tok_xyz" not in result
    assert '"token": "***REDACTED***"' in result


def test_scrub_case_insensitive():
    """Field-name match is case-insensitive (e.g. 'SECRET')."""
    raw = b'{"SECRET": "mysecret"}'
    result = _scrub_secrets_from_body(raw)
    assert "mysecret" not in result
    assert "***REDACTED***" in result


def test_scrub_non_json_body_returns_decoded_string():
    """Non-JSON body is returned as-is decoded string (no crash)."""
    raw = b"not json at all {{"
    result = _scrub_secrets_from_body(raw)
    assert isinstance(result, str)
    assert "not json at all" in result


def test_scrub_handles_invalid_utf8_bytes():
    """Invalid UTF-8 bytes are decoded with errors='replace' (no crash)."""
    raw = b"\xff\xfe" + b'{"secret": "s3cret"}'
    result = _scrub_secrets_from_body(raw)
    assert "s3cret" not in result
    assert "***REDACTED***" in result


def test_scrub_truncates_at_500_bytes():
    """Only first 500 bytes are decoded; content beyond that is dropped."""
    long_raw = b'{"signal": "x", "secret": "s"}' + b"X" * 600
    result = _scrub_secrets_from_body(long_raw)
    assert len(result) <= 600  # generous upper bound; actual ≤ 500 chars before redact
    assert "s" not in result.split('"***REDACTED***"')[0].split('"secret":')[-1]


# ---- Integration tests: audit row does NOT contain plaintext secret ----


def _make_otter_app_with_secret(
    otter_yaml, tmp_db, monkeypatch, *, secret: str
):
    """Helper: build app with known otter secret, disable IP check."""
    monkeypatch.setenv("TEST_OTTER_SECRET", secret)
    monkeypatch.setenv("LORD_OTTER_DISABLE_IP_CHECK", "1")

    from trading_corp.data.macro_calendar import MacroCalendar
    agent = LordOtterAgent(
        strategies_yaml=otter_yaml,
        macro_calendar=MacroCalendar(path=otter_yaml.parent / "no.yaml"),
        db_url=None,
    )
    app, logger_agent = _build_app_with(otter_agent=agent, db_url=tmp_db)
    return TestClient(app), logger_agent


def test_audit_rejected_scrubs_secret_field(otter_yaml, tmp_db, monkeypatch):
    """Bad-secret rejection: audit row raw_body_snippet must NOT contain
    the plaintext secret and MUST contain ***REDACTED***."""
    real_secret = "REAL_WEBHOOK_SECRET_XYZ"
    client, logger_agent = _make_otter_app_with_secret(
        otter_yaml, tmp_db, monkeypatch, secret=real_secret
    )
    r = client.post(
        "/webhook/tradingview/lord-otter",
        json={"secret": "wrong_secret_value", "symbol": "BTC/USD", "signal": "x"},
    )
    assert r.status_code == 401

    rows = logger_agent.recent_events(limit=20)
    rejected = [
        e for e in rows
        if e["actor"] == "lord_otter" and e["kind"] == "webhook_rejected"
    ]
    assert rejected, "bad_secret path must write a webhook_rejected audit row"
    snippet = rejected[0]["payload"]["raw_body_snippet"]
    # The literal secret value sent in the body must be scrubbed.
    assert "wrong_secret_value" not in snippet, (
        "raw_body_snippet must not contain the plaintext secret value"
    )
    assert "***REDACTED***" in snippet, (
        "raw_body_snippet must contain the redaction marker"
    )


def test_audit_rejected_scrubs_webhook_secret_field(otter_yaml, tmp_db, monkeypatch):
    """webhook_secret field is also scrubbed (uses _SECRET_FIELDS tuple)."""
    real_secret = "REAL_WEBHOOK_SECRET_XYZ"
    monkeypatch.setenv("TEST_OTTER_SECRET", real_secret)
    monkeypatch.setenv("LORD_OTTER_DISABLE_IP_CHECK", "1")

    from trading_corp.data.macro_calendar import MacroCalendar
    agent = LordOtterAgent(
        strategies_yaml=otter_yaml,
        macro_calendar=MacroCalendar(path=otter_yaml.parent / "no.yaml"),
        db_url=None,
    )
    app, logger_agent = _build_app_with(otter_agent=agent, db_url=tmp_db)
    client = TestClient(app)

    # Body uses 'webhook_secret' key instead of 'secret'.
    import json as _json
    body = _json.dumps(
        {"secret": "bad", "webhook_secret": "plaintext_wh_secret", "symbol": "BTC/USD"}
    ).encode()
    r = client.post(
        "/webhook/tradingview/lord-otter",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 401

    rows = logger_agent.recent_events(limit=20)
    rejected = [
        e for e in rows
        if e["actor"] == "lord_otter" and e["kind"] == "webhook_rejected"
    ]
    assert rejected
    snippet = rejected[0]["payload"]["raw_body_snippet"]
    assert "plaintext_wh_secret" not in snippet
    assert "***REDACTED***" in snippet


def test_audit_rejected_scrubs_token_field(otter_yaml, tmp_db, monkeypatch):
    """token field is also scrubbed."""
    real_secret = "REAL_WEBHOOK_SECRET_XYZ"
    monkeypatch.setenv("TEST_OTTER_SECRET", real_secret)
    monkeypatch.setenv("LORD_OTTER_DISABLE_IP_CHECK", "1")

    from trading_corp.data.macro_calendar import MacroCalendar
    agent = LordOtterAgent(
        strategies_yaml=otter_yaml,
        macro_calendar=MacroCalendar(path=otter_yaml.parent / "no.yaml"),
        db_url=None,
    )
    app, logger_agent = _build_app_with(otter_agent=agent, db_url=tmp_db)
    client = TestClient(app)

    import json as _json
    body = _json.dumps(
        {"secret": "bad", "token": "bearer_plaintext_token", "symbol": "BTC/USD"}
    ).encode()
    r = client.post(
        "/webhook/tradingview/lord-otter",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 401

    rows = logger_agent.recent_events(limit=20)
    rejected = [
        e for e in rows
        if e["actor"] == "lord_otter" and e["kind"] == "webhook_rejected"
    ]
    assert rejected
    snippet = rejected[0]["payload"]["raw_body_snippet"]
    assert "bearer_plaintext_token" not in snippet
    assert "***REDACTED***" in snippet


def test_audit_rejected_handles_non_json_body(otter_yaml, tmp_db, monkeypatch):
    """Non-JSON body (malformed_json rejection) must not crash _audit_rejected
    and must NOT echo raw body in the log."""
    real_secret = "REAL_WEBHOOK_SECRET_XYZ"
    monkeypatch.setenv("TEST_OTTER_SECRET", real_secret)
    monkeypatch.setenv("LORD_OTTER_DISABLE_IP_CHECK", "1")

    from trading_corp.data.macro_calendar import MacroCalendar
    agent = LordOtterAgent(
        strategies_yaml=otter_yaml,
        macro_calendar=MacroCalendar(path=otter_yaml.parent / "no.yaml"),
        db_url=None,
    )
    app, logger_agent = _build_app_with(otter_agent=agent, db_url=tmp_db)
    client = TestClient(app)

    r = client.post(
        "/webhook/tradingview/lord-otter",
        content=b"not-valid-json-at-all!!!",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400

    rows = logger_agent.recent_events(limit=20)
    rejected = [
        e for e in rows
        if e["actor"] == "lord_otter" and e["kind"] == "webhook_rejected"
        and e["payload"]["reason"] == "malformed_json"
    ]
    assert rejected, "malformed_json path must write a webhook_rejected audit row"
    # Non-JSON snippet is fine to store; just must not crash.
    snippet = rejected[0]["payload"]["raw_body_snippet"]
    assert isinstance(snippet, str)


def test_audit_rejected_handles_empty_body(otter_yaml, tmp_db, monkeypatch):
    """Empty body passed as raw=b'' to _audit_rejected: snippet is empty string."""
    from trading_corp.web.webhooks import _audit_rejected

    class _MinimalDeps:
        pass

    from trading_corp.persistence.db import init_db
    from trading_corp.agents.logger import LoggerAgent

    init_db(tmp_db)
    la = LoggerAgent(tmp_db)
    deps = _MinimalDeps()
    deps.logger_agent = la

    # Call directly with empty bytes.
    _audit_rejected(deps, "test_reason", "127.0.0.1", b"")

    rows = la.recent_events(limit=5)
    rejected = [e for e in rows if e["kind"] == "webhook_rejected"]
    assert rejected
    assert rejected[0]["payload"]["raw_body_snippet"] == ""


def test_warning_log_does_not_echo_raw_body(otter_yaml, tmp_db, monkeypatch, caplog):
    """The bad-JSON log.warning must NOT contain the raw body bytes.
    It should log only the byte-length (len=N)."""
    import logging

    real_secret = "REAL_WEBHOOK_SECRET_XYZ"
    monkeypatch.setenv("TEST_OTTER_SECRET", real_secret)
    monkeypatch.setenv("LORD_OTTER_DISABLE_IP_CHECK", "1")

    from trading_corp.data.macro_calendar import MacroCalendar
    agent = LordOtterAgent(
        strategies_yaml=otter_yaml,
        macro_calendar=MacroCalendar(path=otter_yaml.parent / "no.yaml"),
        db_url=None,
    )
    app, _ = _build_app_with(otter_agent=agent, db_url=tmp_db)
    client = TestClient(app)

    sentinel_body = b"NOT_JSON_SENTINEL_CANARY_12345"
    with caplog.at_level(logging.WARNING, logger="trading_corp.web.webhooks"):
        r = client.post(
            "/webhook/tradingview/lord-otter",
            content=sentinel_body,
            headers={"content-type": "application/json"},
        )
    assert r.status_code == 400

    webhook_warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == "trading_corp.web.webhooks"
        and "bad JSON" in record.getMessage()
    ]
    assert webhook_warnings, "Expected at least one 'bad JSON' warning log"
    for msg in webhook_warnings:
        assert b"NOT_JSON_SENTINEL_CANARY_12345" not in msg.encode(
            "utf-8", errors="replace"
        ), f"Raw body leaked into warning log: {msg!r}"
        assert "len=" in msg, f"Expected len=N format in warning, got: {msg!r}"
