"""Multi-account M2 -- the accounts overview (/) + per-account page (/account/{id}), DISPLAY-ONLY (2026-09-01).
Offline FastAPI TestClient, PM DB (schema 15) + a read-only legacy arm read. Proves, against TWO accounts (jack
populated, karen display-only), the ruled behaviour:
  - / lists both accounts; a PM-traded account shows realized / win-loss / SAMPLE / open-at-cost SEPARATELY with
    the thin-sample caveat travelling WITH the number (R2c discipline); a 0-subdivision account states it is NOT
    traded by PM (display-only) in the COPY, not an empty frame;
  - /account/{id} shows the per-subdivision P&L table (jack) or the display-only limitation (karen);
  - the global arm STATE is visible read-only (R4); there is NO arm/attach/place control on any account page;
  - an unknown account 404s.
"""
import time
from fastapi.testclient import TestClient
from trading_corp.prediction_markets import db


def _seed(conn):
    conn.execute("INSERT INTO pm_account (account_id,venue,secret_ref,label,active,created_ts) "
                 "VALUES ('kalshi_jack','kalshi','KALSHI','Jack (KALSHI)',1,1787000000)")
    conn.execute("INSERT INTO pm_account (account_id,venue,secret_ref,label,active,created_ts) "
                 "VALUES ('kalshi_karen','kalshi','kalshi_karen','Karen',1,1787000000)")
    conn.execute("INSERT INTO pm_subdivision (account_id,category,label,market_types,sizing_mode,fixed_stake_usd,"
                 "active,created_ts) VALUES ('kalshi_jack','mlb','Jack MLB','moneyline','contracts',0.01,1,1787000000)")
    conn.execute("INSERT INTO pm_subdivision_attachment (account_id,category,wallet,active,source,added_ts) "
                 "VALUES ('kalshi_jack','mlb','0xw',1,'seed',1787000000)")
    base = dict(account_id="kalshi_jack", category="mlb", wallet="0xw", condition_id="0xc", outcome_index=1,
                signal_id="s", client_order_id="c", ticker="KXMLBGAME-26AUG311840SDCIN-SD", order_side="bid",
                outcome_leg="yes", is_exit=0, submitted_count=5, submitted_price=0.60, time_in_force="ioc",
                outcome_status="filled", fill_count=5.0, fill_price=0.60, fee=0.0, dry_run=0,
                submitted_ts=1788200000, response_ts=1788200000, close_source=None, realized_pnl=None, won=None)
    def ins(**kw):
        r = dict(base); r.update(kw); cols=",".join(r); conn.execute(
            "INSERT INTO pm_subdivision_order (%s) VALUES (%s)" % (cols, ",".join(["?"]*len(r))), tuple(r.values()))
    ins(client_order_id="c1")                                                              # open entry
    ins(client_order_id="c2", is_exit=1, close_source="settlement", fill_price=1.0, realized_pnl=2.0, won=1)  # win
    ins(client_order_id="c3", is_exit=1, close_source="settlement", fill_price=0.0, realized_pnl=-3.0, won=0,
        ticker="KXMLBGAME-26AUG311840SDCIN-CIN", outcome_index=0)                          # loss


def _client(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db"); monkeypatch.setenv("PM_DB_PATH", p); db.init_db(p)
    with db.connect(p) as conn:
        _seed(conn)
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app)


def test_accounts_overview_lists_both_with_honest_pnl(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    html = cl.get("/").text
    assert ">Accounts</a>" in html                                    # the new top-of-hierarchy nav
    # jack: PM-traded -> realized/win-loss/SAMPLE/open shown separately, caveat with the number
    assert 'href="/account/kalshi_jack"' in html and "Jack (KALSHI)" in html
    assert "Realized" in html and "settled" in html and "1W / 1L" in html   # W/L split (win + loss seeded)
    assert "Open (at cost)" in html
    assert "thin sample" in html or "settled sample" in html          # the caveat travels with the number
    # karen: display-only -> the limitation is in the COPY, not an empty frame
    assert 'href="/account/kalshi_karen"' in html and "Karen" in html
    assert "not traded by Prediction Markets" in html
    # R4: global arm state visible (read-only), NO control
    assert "GLOBAL ARM" in html
    for tok in ("hx-post", "disarm", "/attach/", "place order", "<form"):
        assert tok not in html.lower(), tok


def test_account_page_jack_shows_subdivision_pnl(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    html = cl.get("/account/kalshi_jack").text
    assert "Sub-divisions" in html and "MLB" in html
    assert "1 / 1" in html or "1W / 1L" in html                       # win/loss split visible
    assert "settled" in html.lower()                                  # sample size shown
    assert 'href="/live/kalshi_jack/mlb"' in html                     # links down to the live sub-division
    assert "cost basis" in html.lower()                               # open at cost, not mark
    assert "GLOBAL ARM" in html
    # read-only: no controls
    for tok in ("hx-post", "disarm", "/attach/", "<form"):
        assert tok not in html.lower(), tok


def test_account_page_karen_states_display_only(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    html = cl.get("/account/kalshi_karen").text
    assert "no Prediction Markets sub-divisions" in html
    assert "display-only" in html
    assert "Sub-divisions" not in html or "no Prediction Markets sub-divisions" in html   # no P&L table, the limitation instead
    # it must NOT render a zeroed P&L frame implying it will fill
    assert "Account total" not in html


def test_unknown_account_404(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    r = cl.get("/account/nope")
    assert r.status_code == 404
    assert "Account not found" in r.text


def test_account_pages_are_read_only(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    assert cl.post("/account/kalshi_jack").status_code == 405
    assert cl.post("/").status_code == 405
