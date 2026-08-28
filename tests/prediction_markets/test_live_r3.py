"""Stage 3 R3 -- the LIVE sub-division list (read-only) + target-account credential presence.

Offline, FastAPI TestClient, PM DB only. Proves: honest-empty when migration 010 is NOT deployed (schema 9,
tables absent) AND when there are simply no sub-divisions; tile-on-CREATE at schema 10; 404 for a missing
sub-division; READ-ONLY (no POST route, no order/arm controls); vocabulary ("Live sub-divisions", no internal
table/status names leaked); and that `assert_live_ready` fails LOUD on a missing KALSHI key (without going live).
"""
import pytest
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, subdivision


def _client(monkeypatch, tmp_path, *, schema=10, seed=False):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    if schema == 9:
        monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:9])   # stop before migration 010
    db.init_db(p)
    if seed:
        with db.connect(p) as conn:
            conn.execute("INSERT INTO pm_account (account_id, venue, secret_ref, label, active, created_ts) "
                         "VALUES ('kalshi_jack','kalshi','KALSHI','Jack (KALSHI)',1,1787000000)")
            conn.execute("INSERT INTO pm_subdivision (account_id, category, label, market_types, sizing_mode, "
                         "fixed_stake_usd, active, created_ts) VALUES "
                         "('kalshi_jack','mlb','Jack MLB','moneyline,total,spread','fixed',5.0,1,1787000000)")
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app)


def test_live_list_honest_empty_when_tables_absent_schema9(monkeypatch, tmp_path):
    """Migration 010 NOT deployed (live schema 9): /live must render honest-empty, NOT 500. This is what lets R3
    deploy on a pm_web restart independent of the migration-010 deploy."""
    cl = _client(monkeypatch, tmp_path, schema=9)
    r = cl.get("/live")
    assert r.status_code == 200
    assert "No live sub-divisions yet" in r.text
    assert "Live sub-divisions" in r.text


def test_live_list_honest_empty_when_no_subdivisions_schema10(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path, schema=10, seed=False)
    r = cl.get("/live")
    assert r.status_code == 200
    assert "No live sub-divisions yet" in r.text


def test_live_tile_on_create_schema10(monkeypatch, tmp_path):
    """Tile-on-CREATE: a sub-division that exists but has never traded still shows a tile with an honest empty
    hint -- information, not an error."""
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True)
    r = cl.get("/live")
    assert r.status_code == 200
    assert 'href="/live/kalshi_jack/mlb"' in r.text
    assert "Jack (KALSHI)" in r.text and "MLB" in r.text
    assert "no live trades yet" in r.text
    assert "No live sub-divisions yet" not in r.text


def test_live_subdivision_detail_and_404(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True)
    r = cl.get("/live/kalshi_jack/mlb")
    assert r.status_code == 200
    assert "Jack (KALSHI)" in r.text and "moneyline,total,spread" in r.text and "fixed" in r.text
    assert "no live trades yet" in r.text          # honest-empty live list (P3 not built)
    # a sub-division that does not exist -> 404, never a fabricated page
    assert cl.get("/live/nope/mlb").status_code == 404
    assert cl.get("/live/kalshi_jack/nba").status_code == 404


def test_dashboard_card_enabled(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True)
    r = cl.get("/")
    assert r.status_code == 200
    assert 'href="/live"' in r.text                 # card + nav now link out
    assert "coming in P3" not in r.text             # the disabled card is gone


def test_live_is_read_only_no_order_path(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True)
    assert cl.get("/live").status_code == 200
    # no POST route exists on the live surfaces (read-only)
    assert cl.post("/live").status_code == 405
    assert cl.post("/live/kalshi_jack/mlb").status_code == 405
    for path in ("/live", "/live/kalshi_jack/mlb"):
        html = cl.get(path).text.lower()
        for token in ("<form", "place order", "/order", 'type="submit"', "hx-post", "disarm"):
            assert token not in html, (path, token)


def test_vocabulary_no_internal_name_leak(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True)
    for path in ("/live", "/live/kalshi_jack/mlb"):
        html = cl.get(path).text
        assert "Live sub-divisions" in html
        for leak in ("pm_subdivision", "pm_account", "secret_ref", "owner_identity", "'pinned'", "'candidate'"):
            assert leak not in html, (path, leak)


def test_subdivision_reads_are_defensive(tmp_path):
    """subdivision.* tolerate the money-layer tables being absent -> honest-empty, never an error."""
    p = str(tmp_path / "pm.db")
    import trading_corp.prediction_markets.db as _db
    # a DB with NO tables at all
    with _db.connect(p) as conn:
        assert subdivision.list_subdivisions(conn) == []
        assert subdivision.get_subdivision(conn, "kalshi_jack", "mlb") is None


def test_assert_live_ready_kalshi_fails_loud(monkeypatch):
    """Credential presence: assert_live_ready RAISES loud on a missing KALSHI key, passes when present. Tested
    WITHOUT going live (no broker call). assert_live_ready gates on ANTHROPIC_API_KEY FIRST (unconditional), so we
    set it in both cases and match the KALSHI-specific message -- proving the KALSHI branch is what gates here."""
    from trading_corp.utils import secrets as S
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")   # pass the unconditional anthropic gate
    for k in ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PEM", "KEY_VAULT_URI"):
        monkeypatch.delenv(k, raising=False)
    s_missing = S.load_secrets()
    with pytest.raises(RuntimeError, match="KALSHI"):
        S.assert_live_ready(s_missing, ("kalshi",))
    monkeypatch.setenv("KALSHI_API_KEY_ID", "id-not-a-real-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PEM", "-----BEGIN PRIVATE KEY-----\nnot-real\n-----END PRIVATE KEY-----")
    s_present = S.load_secrets()
    S.assert_live_ready(s_present, ("kalshi",))     # anthropic set + kalshi present -> must NOT raise
