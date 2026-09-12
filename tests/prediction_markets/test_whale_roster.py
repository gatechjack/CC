"""Per-whale ROSTER record + Detach (2026-09-12): the "Copies these whales" panel becomes a whale roster.

Offline, PM DB in a tmp path + a fake marks map. Proves:
  * subdivision.whale_live_records -- the journal-filtered per-whale record, grouped ON-ROSTER vs FORMERLY-LIVE,
    with dates from the attachment row, dollars-first figures, honest N-of-M current value, realized-today, and
    unbooked (opposed + whale-exit) never hidden; a re-attached whale appears ONCE on-roster with the original
    added_ts; an attached-but-never-copied whale shows zeros; a formerly-live whale carries its removed_ts.
  * authz.can_act_on_account -- the owner-OR-admin write gate (admin any; owner own; owner other -> deny; no
    identity -> deny; null owner -> deny) -- the first use of owner_identity for AUTHORIZATION not visibility.
  * the Detach route + confirm + owner-or-admin server gate, and the Farm demote-409 reason text pointing here.
"""
import pytest
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, subdivision, farm_actions
from trading_corp.prediction_markets.web import authz


T0 = 1789000000          # attach epoch
TODAY = 1789200000       # "now" for the tests
TODAY_START = 1789180000 # ET-calendar day start the caller would pass (settlements >= this are "today")


class _Mark:
    """A Mark-like stub: only the two bid fields whale_live_records duck-types."""
    def __init__(self, yes_bid=None, no_bid=None):
        self.yes_bid, self.no_bid = yes_bid, no_bid


def _mkdb(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    return p


def _ord(conn, **kw):
    row = {"account_id": "kalshi_jack", "category": "mlb", "dry_run": 0, "outcome_status": "filled",
           "is_exit": 0, "fill_count": 1.0, "fill_price": 0.50, "fee": 0.01}
    row.update(kw)
    cols = ", ".join(row); qs = ", ".join(["?"] * len(row))
    conn.execute("INSERT INTO pm_subdivision_order (%s) VALUES (%s)" % (cols, qs), tuple(row.values()))


def _attach(conn, wallet, active=1, added_ts=T0, removed_ts=None, name=None):
    conn.execute("INSERT INTO pm_subdivision_attachment (account_id, category, wallet, active, source, added_ts, removed_ts) "
                 "VALUES ('kalshi_jack','mlb',?,?,'promote_to_live',?,?)", (wallet, active, added_ts, removed_ts))
    if name is not None:
        conn.execute("INSERT OR IGNORE INTO pm_whale (wallet, user_name) VALUES (?,?)", (wallet, name))


def _seed(conn):
    conn.execute("INSERT INTO pm_account (account_id, venue, secret_ref, label, active, created_ts) "
                 "VALUES ('kalshi_jack','kalshi','KALSHI','Jack',1,?)", (T0,))
    conn.execute("INSERT INTO pm_subdivision (account_id, category, label, active, created_ts) "
                 "VALUES ('kalshi_jack','mlb','Jack MLB',1,?)", (T0,))
    # WHALE A "Alpha" -- ON-ROSTER, the full record: settled W+L, an opposed (unbooked) close, two OPEN positions
    # (one priced, one not -> N-of-M coverage), realized today (the settled win) + all-time.
    _attach(conn, "0xa", active=1, added_ts=T0, name="Alpha")
    _ord(conn, wallet="0xa", ticker="KXMLBGAME-T1-A", outcome_leg="yes")                                  # entry1
    _ord(conn, wallet="0xa", ticker="KXMLBGAME-T1-A", outcome_leg="yes", is_exit=1, close_source="settlement",
         won=1, realized_pnl=5.0, settled_ts=TODAY_START + 100)                                            # settled WIN today
    _ord(conn, wallet="0xa", ticker="KXMLBGAME-T2-A", outcome_leg="yes")                                  # entry2
    _ord(conn, wallet="0xa", ticker="KXMLBGAME-T2-A", outcome_leg="yes", is_exit=1, close_source="settlement",
         won=0, realized_pnl=-3.0, settled_ts=T0 + 50)                                                     # settled LOSS old
    _ord(conn, wallet="0xa", ticker="KXMLBGAME-T3-A", outcome_leg="yes")                                  # entry3
    _ord(conn, wallet="0xa", ticker="KXMLBGAME-T3-A", outcome_leg="yes", is_exit=1, close_source="opposed",
         won=None, realized_pnl=None, settled_ts=None)                                                    # opposed = UNBOOKED
    _ord(conn, wallet="0xa", ticker="KXMLBGAME-T4-A", outcome_leg="yes", fill_count=2.0, fill_price=0.40) # OPEN priced
    _ord(conn, wallet="0xa", ticker="KXMLBGAME-T5-A", outcome_leg="yes", fill_count=1.0, fill_price=0.30) # OPEN unpriced
    # WHALE B "Bravo" -- FORMERLY LIVE (detached): one settled win, no open. removed_ts set.
    _attach(conn, "0xb", active=0, added_ts=T0, removed_ts=T0 + 9000, name="Bravo")
    _ord(conn, wallet="0xb", ticker="KXMLBGAME-T6-B", outcome_leg="yes")
    _ord(conn, wallet="0xb", ticker="KXMLBGAME-T6-B", outcome_leg="yes", is_exit=1, close_source="settlement",
         won=1, realized_pnl=2.0, settled_ts=T0 + 8000)
    # WHALE C "Charlie" -- ON-ROSTER, attached but NEVER copied (all zeros).
    _attach(conn, "0xc", active=1, added_ts=T0 + 1, name="Charlie")
    # WHALE D "Delta" -- RE-ATTACHED (active=1, original added_ts preserved). One settled win. Must appear ONCE on-roster.
    _attach(conn, "0xd", active=1, added_ts=T0 - 5000, removed_ts=None, name="Delta")
    _ord(conn, wallet="0xd", ticker="KXMLBGAME-T7-D", outcome_leg="yes")
    _ord(conn, wallet="0xd", ticker="KXMLBGAME-T7-D", outcome_leg="yes", is_exit=1, close_source="settlement",
         won=1, realized_pnl=1.0, settled_ts=T0 + 7000)


def _records(tmp_path):
    p = _mkdb(tmp_path)
    with db.connect(p) as conn:
        _seed(conn)
        conn.commit()
        marks = {"KXMLBGAME-T4-A": _Mark(yes_bid=0.55)}   # T5 has NO mark -> unpriced
        return subdivision.whale_live_records(conn, "kalshi_jack", "mlb", marks=marks, now_ts=TODAY,
                                              today_start_ts=TODAY_START, thin_floor=50)


# ---------------------------------------------------------------------------------------------------------------
# reader: whale_live_records
# ---------------------------------------------------------------------------------------------------------------
def test_groups_on_roster_vs_formerly_live(tmp_path):
    r = _records(tmp_path)
    on = {x["wallet"] for x in r["on_roster"]}
    fm = {x["wallet"] for x in r["formerly_live"]}
    assert on == {"0xa", "0xc", "0xd"}      # active=1: Alpha, Charlie, re-attached Delta
    assert fm == {"0xb"}                     # active=0: Bravo


def test_reattached_whale_appears_once_with_original_added_ts(tmp_path):
    r = _records(tmp_path)
    delta = [x for x in r["on_roster"] if x["wallet"] == "0xd"]
    assert len(delta) == 1                    # ONE row despite re-attach (schema keeps one row per whale)
    assert delta[0]["added_ts"] == T0 - 5000  # the ORIGINAL added_ts, preserved across re-attach
    assert delta[0]["active"] is True


def test_alpha_full_record_dollars_and_counts(tmp_path):
    a = [x for x in _records(tmp_path)["on_roster"] if x["wallet"] == "0xa"][0]
    assert a["user_name"] == "Alpha"
    assert a["placed"] == 5                    # 5 entries (is_exit=0)
    assert a["booked_closes"] == 2             # two settlements
    assert (a["settled_w"], a["settled_l"]) == (1, 1)
    assert a["unbooked_closes"] == 1           # the opposed close -- counted, never hidden
    assert a["realized_pnl"] == pytest.approx(2.0)     # +5 -3, net of fees convention (settlement fee=0)
    assert a["realized_today"] == pytest.approx(5.0)   # only the settled-today win
    assert a["added_ts"] == T0
    assert a["thin"] is True                   # 2 booked < 50


def test_alpha_open_value_is_n_of_m_priced(tmp_path):
    a = [x for x in _records(tmp_path)["on_roster"] if x["wallet"] == "0xa"][0]
    assert a["n_open"] == 2
    assert a["open_cost_usd"] == pytest.approx(0.40 * 2 + 0.30 * 1)   # 1.10 at cost
    assert a["n_total"] == 2 and a["n_priced"] == 1                    # one priced, one not
    assert a["open_value"] == pytest.approx(2 * 0.55)                  # ONLY the priced leg (1.10), never the cost


def test_open_value_none_when_no_marks(tmp_path):
    p = _mkdb(tmp_path)
    with db.connect(p) as conn:
        _seed(conn); conn.commit()
        r = subdivision.whale_live_records(conn, "kalshi_jack", "mlb", marks=None, now_ts=TODAY,
                                           today_start_ts=TODAY_START)
    a = [x for x in r["on_roster"] if x["wallet"] == "0xa"][0]
    assert a["open_value"] is None and a["n_priced"] == 0 and a["n_total"] == 2   # honest no-mark, never $0


def test_formerly_live_carries_removed_ts(tmp_path):
    b = _records(tmp_path)["formerly_live"][0]
    assert b["wallet"] == "0xb" and b["active"] is False
    assert b["added_ts"] == T0 and b["removed_ts"] == T0 + 9000        # the span
    assert b["placed"] == 1 and b["booked_closes"] == 1 and b["settled_w"] == 1
    assert b["realized_pnl"] == pytest.approx(2.0)


def test_attached_never_copied_shows_zeros(tmp_path):
    c = [x for x in _records(tmp_path)["on_roster"] if x["wallet"] == "0xc"][0]
    assert c["user_name"] == "Charlie" and c["active"] is True
    assert c["placed"] == 0 and c["booked_closes"] == 0 and c["n_open"] == 0
    assert c["realized_pnl"] == 0.0 and c["open_value"] is None


def test_empty_when_no_money_layer(tmp_path):
    # a pre-money-layer DB (schema < 11) -> honest empty, never a 500
    p = str(tmp_path / "pm.db")
    import trading_corp.prediction_markets.db as _db
    orig = _db.MIGRATIONS
    try:
        _db.MIGRATIONS = _db.MIGRATIONS[:10]     # stop before migration 011 (attachment table)
        _db.init_db(p)
        with _db.connect(p) as conn:
            r = subdivision.whale_live_records(conn, "kalshi_jack", "mlb", marks={}, now_ts=TODAY,
                                               today_start_ts=TODAY_START)
    finally:
        _db.MIGRATIONS = orig
    assert r["on_roster"] == [] and r["formerly_live"] == []


# ---------------------------------------------------------------------------------------------------------------
# authz: can_act_on_account -- owner-OR-admin write gate (fail-closed)
# ---------------------------------------------------------------------------------------------------------------
_JACK = {"account_id": "kalshi_jack", "owner_identity": None}      # unowned -> admin-only (like the box)
_KAREN = {"account_id": "kalshi_karen", "owner_identity": "karen"}


def test_authz_admin_may_act_on_any_account():
    assert authz.can_act_on_account("jack", True, _JACK) is True
    assert authz.can_act_on_account("jack", True, _KAREN) is True


def test_authz_owner_may_act_on_own_account():
    assert authz.can_act_on_account("karen", False, _KAREN) is True


def test_authz_owner_may_not_act_on_other_account():
    assert authz.can_act_on_account("karen", False, _JACK) is False      # not her account
    assert authz.can_act_on_account("karen", False, {"account_id": "x", "owner_identity": "someone"}) is False


def test_authz_no_identity_denies():
    assert authz.can_act_on_account(None, False, _KAREN) is False


def test_authz_null_owner_denies_non_admin():
    assert authz.can_act_on_account("karen", False, _JACK) is False      # owner_identity is None -> admin-only
    assert authz.can_act_on_account("karen", False, None) is False       # missing account -> deny


# ---------------------------------------------------------------------------------------------------------------
# routes + render: the roster page, the Detach confirm + POST (owner-or-admin), idempotency, the demote-409 text
# ---------------------------------------------------------------------------------------------------------------
def _acct(conn, aid, owner):
    conn.execute("INSERT INTO pm_account (account_id, venue, secret_ref, label, active, created_ts, owner_identity) "
                 "VALUES (?,?,?,?,1,?,?)", (aid, "kalshi", "KALSHI", aid, T0, owner))


def _sub(conn, aid, cat="mlb"):
    conn.execute("INSERT INTO pm_subdivision (account_id, category, label, active, created_ts) VALUES (?,?,?,1,?)",
                 (aid, cat, aid + " " + cat, T0))


def _client(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")     # jack = admin; karen = a plain owner
    db.init_db(p)
    with db.connect(p) as conn:
        _acct(conn, "kalshi_jack", None)                  # unowned -> admin-only (mirrors the box)
        _acct(conn, "kalshi_karen", "karen")              # owned by 'karen'
        _sub(conn, "kalshi_jack"); _sub(conn, "kalshi_karen")
        # jack/mlb: on-roster Alpha (settled + open) + formerly-live Bravo
        _attach(conn, "0xa", active=1, added_ts=T0, name="Alpha")
        _ord(conn, wallet="0xa", ticker="KXMLBGAME-T1-A", outcome_leg="yes")
        _ord(conn, wallet="0xa", ticker="KXMLBGAME-T1-A", outcome_leg="yes", is_exit=1, close_source="settlement",
             won=1, realized_pnl=5.0, settled_ts=T0 + 100)
        _ord(conn, wallet="0xa", ticker="KXMLBGAME-T4-A", outcome_leg="yes", fill_count=2.0, fill_price=0.40)  # open
        _attach(conn, "0xb", active=0, added_ts=T0, removed_ts=T0 + 900, name="Bravo")
        _ord(conn, wallet="0xb", ticker="KXMLBGAME-T6-B", outcome_leg="yes")                                   # formerly-live record
        _ord(conn, wallet="0xb", ticker="KXMLBGAME-T6-B", outcome_leg="yes", is_exit=1, close_source="settlement",
             won=1, realized_pnl=2.0, settled_ts=T0 + 800)
        # karen/mlb: on-roster Kilo (her own account)
        conn.execute("INSERT INTO pm_subdivision_attachment (account_id, category, wallet, active, source, added_ts) "
                     "VALUES ('kalshi_karen','mlb','0xk',1,'promote_to_live',?)", (T0,))
        conn.commit()
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app), p


def _active(p, aid, wallet):
    with db.connect(p) as conn:
        r = conn.execute("SELECT active FROM pm_subdivision_attachment WHERE account_id=? AND category='mlb' AND wallet=?",
                         (aid, wallet)).fetchone()
    return None if r is None else int(r["active"])


def test_roster_page_renders_groups_and_detach(monkeypatch, tmp_path):
    cl, _ = _client(monkeypatch, tmp_path)
    r = cl.get("/live/kalshi_jack/mlb", headers={"Remote-User": "jack"})
    assert r.status_code == 200
    body = r.text
    assert "Copies these whales" in body and "On roster" in body and "Formerly live" in body
    assert "Alpha" in body and "Bravo" in body               # on-roster + formerly-live whale names
    assert "/live/kalshi_jack/mlb/detach/0xa" in body        # Detach control present (admin) on the on-roster whale
    assert 'data-wallet="0xa"' in body                       # R4: drawer rows carry the wallet for the JS filter


def test_detach_confirm_states_consequences(monkeypatch, tmp_path):
    cl, _ = _client(monkeypatch, tmp_path)
    r = cl.get("/live/kalshi_jack/mlb/detach/0xa", headers={"Remote-User": "jack"})
    assert r.status_code == 200
    b = r.text
    assert "Stops new copies" in b and "NOT" in b and "ride to settlement" in b
    assert "Farm paper list" in b
    assert "<b>1</b> position" in b and "at cost" in b   # Alpha holds one open position (T4); count is inside <b>


def test_detach_post_admin_sets_inactive_and_is_idempotent(monkeypatch, tmp_path):
    cl, p = _client(monkeypatch, tmp_path)
    assert _active(p, "kalshi_jack", "0xa") == 1
    r = cl.post("/live/kalshi_jack/mlb/detach/0xa", headers={"Remote-User": "jack"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/live/kalshi_jack/mlb"
    assert _active(p, "kalshi_jack", "0xa") == 0             # copying stops (active=0); row survives (re-attachable)
    r2 = cl.post("/live/kalshi_jack/mlb/detach/0xa", headers={"Remote-User": "jack"}, follow_redirects=False)
    assert r2.status_code == 303 and _active(p, "kalshi_jack", "0xa") == 0   # idempotent no-op


def test_detach_owner_may_detach_own_account(monkeypatch, tmp_path):
    cl, p = _client(monkeypatch, tmp_path)
    r = cl.post("/live/kalshi_karen/mlb/detach/0xk", headers={"Remote-User": "karen"}, follow_redirects=False)
    assert r.status_code == 303 and _active(p, "kalshi_karen", "0xk") == 0


def test_detach_owner_may_not_detach_other_account(monkeypatch, tmp_path):
    cl, p = _client(monkeypatch, tmp_path)
    r = cl.post("/live/kalshi_jack/mlb/detach/0xa", headers={"Remote-User": "karen"}, follow_redirects=False)
    assert r.status_code == 403
    assert _active(p, "kalshi_jack", "0xa") == 1             # unchanged -- the server gate held
    rg = cl.get("/live/kalshi_jack/mlb/detach/0xa", headers={"Remote-User": "karen"})
    assert rg.status_code == 403                             # the confirm page is gated too


def test_detach_no_identity_denied(monkeypatch, tmp_path):
    cl, p = _client(monkeypatch, tmp_path)
    r = cl.post("/live/kalshi_karen/mlb/detach/0xk", follow_redirects=False)   # no Remote-User
    assert r.status_code == 403 and _active(p, "kalshi_karen", "0xk") == 1


def test_detach_control_hidden_for_formerly_live(monkeypatch, tmp_path):
    cl, _ = _client(monkeypatch, tmp_path)
    b = cl.get("/live/kalshi_jack/mlb", headers={"Remote-User": "jack"}).text
    assert "/live/kalshi_jack/mlb/detach/0xb" not in b       # Bravo is already detached -> no Detach control
