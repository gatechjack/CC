"""Redesign scoping (R6) + the security fix (inventory add#5) + cache-bust. A Karen (non-admin) login sees only
kalshi_karen on /live AND is refused /live/kalshi_jack/... and its events; an admin sees both. HTTP via TestClient
with a Remote-User header, PM_ADMIN_IDENTITIES=jack. Read-only page throughout."""
import re
import sqlite3

from starlette.testclient import TestClient

from trading_corp.prediction_markets import db


def _legacy(tmp_path):
    lp = str(tmp_path / "legacy.db")
    c = sqlite3.connect(lp)
    c.execute("CREATE TABLE agent_state (agent TEXT, key TEXT, value_json TEXT, PRIMARY KEY(agent,key))")
    c.execute("INSERT INTO agent_state VALUES('pm_live','arm:global','{\"armed\": true}')")
    c.commit(); c.close()
    return lp


def _order(conn, acct, cat, ticker, *, is_exit=0, realized=None, won=None, close_source=None, ts=1787000000):
    conn.execute(
        "INSERT INTO pm_subdivision_order(account_id,category,ticker,outcome_leg,is_exit,outcome_status,"
        "fill_count,fill_price,dry_run,submitted_ts,response_ts,close_source,realized_pnl,won,settled_ts)"
        " VALUES(?,?,?, 'yes',?, 'filled', 1,0.5,0,?,?,?,?,?,?)",
        (acct, cat, ticker, is_exit, ts, ts, close_source, realized, won, ts if is_exit else None))


def _client(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    monkeypatch.setenv("PM_LEGACY_DB_PATH", _legacy(tmp_path))
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_account(account_id,venue,secret_ref,owner_identity,label,active,created_ts)"
                     " VALUES('kalshi_jack','kalshi','K',NULL,'Jack (KALSHI)',1,1)")
        conn.execute("INSERT INTO pm_account(account_id,venue,secret_ref,owner_identity,label,active,created_ts)"
                     " VALUES('kalshi_karen','kalshi','K','karen','Karen (KALSHI)',1,1)")
        for acct in ("kalshi_jack", "kalshi_karen"):
            conn.execute("INSERT INTO pm_subdivision(account_id,category,label,market_types,sizing_mode,"
                         "fixed_stake_usd,active,created_ts) VALUES(?,?,?, 'moneyline','fixed',5.0,1,1)",
                         (acct, "mlb", "MLB"))
            conn.execute("INSERT INTO pm_subdivision_attachment(account_id,category,wallet,active,source,added_ts)"
                         " VALUES(?,?,?,1,'seed',1)", (acct, "mlb", "0xw_" + acct))
            _order(conn, acct, "mlb", "KXMLBGAME-26SEP091835CLEBAL-CLE", is_exit=0)
            _order(conn, acct, "mlb", "KXMLBGAME-26SEP091835CLEBAL-CLE", is_exit=1, realized=2.26, won=1,
                   close_source="settlement")
        conn.commit()
    from trading_corp.prediction_markets.web import app as appmod
    return TestClient(appmod.app, raise_server_exceptions=False)


def test_live_tabs_scoped_admin_sees_both(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    html = cl.get("/live", headers={"Remote-User": "jack"}).text
    assert 'href="?account=kalshi_jack"' in html and 'href="?account=kalshi_karen"' in html
    assert "SINGLE-ACCOUNT" not in html


def test_live_tabs_scoped_nonadmin_sees_only_own(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    html = cl.get("/live", headers={"Remote-User": "karen"}).text
    assert 'href="?account=kalshi_karen"' in html
    assert 'href="?account=kalshi_jack"' not in html                  # jack's tab is not offered
    assert 'href="/live/kalshi_jack/mlb"' not in html                 # nor jack's tiles
    assert 'href="/live/kalshi_karen/mlb"' in html
    assert "SINGLE-ACCOUNT" in html


def test_detail_route_403_for_other_account(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    assert cl.get("/live/kalshi_jack/mlb", headers={"Remote-User": "karen"}).status_code == 403   # add#5 fix
    assert cl.get("/live/kalshi_karen/mlb", headers={"Remote-User": "karen"}).status_code == 200
    assert cl.get("/live/kalshi_jack/mlb", headers={"Remote-User": "jack"}).status_code == 200      # admin ok
    assert cl.get("/live/kalshi_nope/mlb", headers={"Remote-User": "karen"}).status_code == 404      # absent -> 404


def test_events_endpoint_scoped(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    j = cl.get("/live/events?since=0", headers={"Remote-User": "karen"}).json()
    accts = {e["account"] for e in (j["placed"] + j["closed"])}
    assert accts == {"kalshi_karen"}                                  # karen never sees jack's order flow
    ja = cl.get("/live/events?since=0", headers={"Remote-User": "jack"}).json()
    assert {"kalshi_jack", "kalshi_karen"} <= {e["account"] for e in (ja["placed"] + ja["closed"])}


def test_no_raw_ticker_on_live_page(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    html = cl.get("/live", headers={"Remote-User": "jack"}).text
    assert not re.findall(r"KX[A-Z0-9]{2,}-[0-9A-Z-]+", html)          # R2: no ticker leaks onto the tile page


def test_cache_bust_pm_desk_matches_shell(monkeypatch, tmp_path):
    import os, hashlib
    from trading_corp.prediction_markets.web import app as appmod
    shell = open(os.path.join(appmod._STATIC_DIR, "..", "templates", "pm_shell.html"), encoding="utf-8").read()
    m = re.search(r"pm_desk\.css\?v=([0-9a-f]{8})", shell)
    assert m, "pm_desk.css cache-bust tag missing from the shell"
    disk = hashlib.sha256(open(os.path.join(appmod._STATIC_DIR, "pm_desk.css"), "rb").read().replace(b"\r", b"")).hexdigest()[:8]
    assert m.group(1) == disk, "pm_desk.css changed but the shell ?v= was not bumped"
    # logos + JS are versioned (runtime CR-stripped sha8 -> the ?v= can never go stale). Pin the 21-logo roster
    # (incl. the swapped-square BUN/MEX) so a dropped/renamed asset is caught.
    assert set(appmod._LOGO_VERSIONS) == {
        "ATP", "BRA", "BUN", "CFB", "CS2", "EPL", "FED", "FL1", "LAL", "MEX", "MLB", "MLS",
        "NBA", "NFL", "NHL", "SEA", "UCL", "UEL", "UFC", "WNBA", "WTA"}
    assert appmod._SUBS_JS_V and all(v for v in appmod._LOGO_VERSIONS.values())
