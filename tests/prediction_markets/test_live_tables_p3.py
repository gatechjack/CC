"""Phase 3 (2026-09-25): the non-MLB /live Active/Complete tabs become FLAT sortable tables (OQ-3/4/5) + the trade
drawer's series-tag leak is floored (3c). Pure sort helper + build_live_context integration + _trade_rows floor
(offline; no TestClient -> off the stale-assertion UI surface). MLB is out of scope (cards, unchanged)."""
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db
from trading_corp.prediction_markets.web import live_view as LV, marks as MK

NOW = 1789200000
G = "KXNCAAFGAME-26SEP11MIZZKU-MIZZ"
T = "KXNCAAFTOTAL-26SEP11MIZZKU-52"
S = "KXNCAAFSPREAD-26SEP11MIZZKU-MIZZ7"
ATP = "KXATPMATCH-25SEP07ALCSIN-ALC"


# ── sort_positions (OQ-4 defaults, OQ-5 flat + game-by-event-date, None-last) ────────────────────────
def _r(**kw):
    base = {"placed_ts": None, "settled_at": None, "game_key": None, "matchup": None, "short": "",
            "desc": "", "contracts": None, "avg_fill": None, "cost": None, "current_value": None,
            "realized": None, "status": "open", "whale_tag": "", "order_id": None}
    base.update(kw)
    return base


def test_active_default_is_event_date_asc_undated_last():
    rows = [_r(matchup="B @ C", game_key=("B @ C", "2026-09-12")),
            _r(matchup="A @ D", game_key=("A @ D", "2026-09-10")),
            _r(matchup=None, game_key=None)]                       # tennis-style undated
    s, d = LV.sort_positions(rows, None, None, tab="active")
    assert (s, d) == ("game", "asc")
    assert [(r["game_key"][1] if r["game_key"] else None) for r in rows] == ["2026-09-10", "2026-09-12", None]


def test_complete_default_is_settle_date_desc():
    rows = [_r(settled_at=100), _r(settled_at=300), _r(settled_at=None)]
    s, d = LV.sort_positions(rows, None, None, tab="complete")
    assert (s, d) == ("settled", "desc")
    assert [r["settled_at"] for r in rows] == [300, 100, None]     # newest first, None last


def test_numeric_none_last_both_directions():
    for direction in ("asc", "desc"):
        rows = [_r(cost=10.0), _r(cost=None), _r(cost=5.0)]
        LV.sort_positions(rows, "cost", direction, tab="active")
        assert rows[-1]["cost"] is None                            # None ALWAYS last
        vals = [r["cost"] for r in rows if r["cost"] is not None]
        assert vals == (sorted(vals, reverse=True) if direction == "desc" else sorted(vals))


def test_game_desc_keeps_undated_last():
    rows = [_r(game_key=("X", "2026-01-01")), _r(game_key=None), _r(game_key=("Y", "2026-02-01"))]
    LV.sort_positions(rows, "game", "desc", tab="active")
    assert rows[0]["game_key"][1] == "2026-02-01"                  # desc -> latest event first
    assert rows[-1]["game_key"] is None                            # undated still last


def test_invalid_column_falls_back_to_tab_default():
    assert LV.sort_positions([_r()], "bogus", "x", tab="active") == ("game", "asc")
    assert LV.sort_positions([_r()], "bogus", "x", tab="complete") == ("settled", "desc")


def test_text_columns_sort():
    rows = [_r(whale_tag="Zed"), _r(whale_tag="Ann")]
    LV.sort_positions(rows, "whale", "asc", tab="active")
    assert [r["whale_tag"] for r in rows] == ["Ann", "Zed"]


# ── _positions_view carries placed_ts + order_id (Phase 3a columns) ──────────────────────────────────
def _orders(*tks, exit_map=None):
    return [{"account_id": "kalshi_jack", "category": "cfb", "wallet": "0xa", "ticker": tk, "outcome_leg": "yes",
             "is_exit": 0, "outcome_status": "filled", "fill_count": 2.0, "fill_price": 0.4, "fee": 0.01,
             "id": 40 + i, "submitted_ts": NOW - 100 + i, "response_ts": NOW - 90 + i}
            for i, tk in enumerate(tks, 1)]


def _pos(*tks):
    return [{"ticker": tk, "held_leg": "yes", "contracts": 2.0, "cost_basis_usd": 0.8, "avg_price": 0.4,
             "fees_usd": 0.01, "market_type": LV._kind(tk)} for tk in tks]


def _posw(*tks):
    return [dict(p, wallet="0xa", user_name=None) for p in _pos(*tks)]


def _ctx(cat, *tks):
    return LV.build_live_context(orders=_orders(*tks), open_positions=_pos(*tks),
                                 open_positions_by_whale=_posw(*tks), slate=None,
                                 marks_result=MK.MarksResult(marks={}, ok=True, as_of=NOW, error=None),
                                 now_ts=NOW, category=cat, titles={})


def test_positions_view_carries_placed_ts_and_order_id():
    row = _ctx("cfb", T)["positions_view"]["active"][0]
    assert row["placed_ts"] == NOW - 99 and row["order_id"] == 41   # earliest entry fill's submitted_ts + id


# ── 3c: the drawer floor -- a non-structural row never renders the raw series tag ────────────────────
def _drawer(tk, *, titles=None, category=None, leg="yes"):
    order = {"id": 9, "ticker": tk, "wallet": "0xa", "user_name": None, "outcome_leg": leg, "is_exit": 0,
             "outcome_status": "filled", "fill_count": 3.0, "fill_price": 0.5, "submitted_price": 0.49,
             "response_ts": NOW}
    return LV._trade_rows([order], {}, {}, {}, {}, NOW, titles=titles or {}, category=category)[0]


def test_drawer_non_structural_floored_no_series_tag():
    r = _drawer(ATP, category="atp")
    assert "KX" not in r["label"] and "KX" not in r["desc"]        # no raw KXATPMATCH... in EITHER column
    assert r["label"] == "ATP"                                     # category floor (no matchup, no title)


def test_drawer_uses_title_when_present():
    r = _drawer(ATP, titles={ATP: "Alcaraz vs Sinner"}, category="atp")
    assert r["label"] == "Alcaraz vs Sinner" and "KX" not in r["desc"]   # title beats the bare floor; still ticker-free


def test_drawer_structural_label_unchanged_desc_floored():
    r = _drawer(S, category="cfb", leg="no")
    assert r["label"] == "SPR +6.5 KU"                             # structural shorthand primary -- byte-unchanged
    assert "KX" not in r["desc"]                                   # desc no longer leaks the raw ticker


# ── served page (TestClient): the FLAT table + drawer render end-to-end (rendering works; the pre-existing
#    24 failures are stale assertions). Proves the new macros parse + the sort headers + the drawer floor. ──
_ORD = {
    "account_id": "kalshi_jack", "category": "cfb", "wallet": "0xa",
    "order_side": "bid", "outcome_leg": "yes", "is_exit": 0, "submitted_count": 2,
    "submitted_price": 0.40, "time_in_force": "immediate_or_cancel", "outcome_status": "filled",
    "fill_count": 2.0, "fill_price": 0.40, "fee": 0.01, "dry_run": 0,
    "submitted_ts": NOW - 100, "response_ts": NOW - 100,
}


def _ins(conn, **ov):
    row = dict(_ORD)
    row.update(ov)
    cols = ", ".join(row.keys())
    conn.execute("INSERT INTO pm_subdivision_order (%s) VALUES (%s)" % (cols, ", ".join(["?"] * len(row))),
                 tuple(row.values()))


def _live_client(tmp_path, monkeypatch):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_account (account_id,venue,secret_ref,label,active,created_ts,owner_identity) "
                     "VALUES ('kalshi_jack','kalshi','K','Jack',1,?,NULL)", (NOW,))
        for cat in ("cfb", "atp"):
            conn.execute("INSERT INTO pm_subdivision (account_id,category,label,market_types,sizing_mode,"
                         "fixed_stake_usd,active,created_ts) VALUES ('kalshi_jack',?,?, 'moneyline,total,spread',"
                         "'fixed',5.0,1,?)", (cat, "Jack " + cat.upper(), NOW))
            conn.execute("INSERT INTO pm_subdivision_attachment (account_id,category,wallet,active,source,added_ts) "
                         "VALUES ('kalshi_jack',?,'0xa',1,'seed',?)", (cat, NOW))
        # cfb: two STRUCTURAL open positions (a total + a spread) -> the flat Active table
        _ins(conn, category="cfb", ticker=T, condition_id="0xc1", client_order_id="c1", broker_order_id="b1")
        _ins(conn, category="cfb", ticker=S, condition_id="0xc2", client_order_id="c2", broker_order_id="b2")
        # atp: a NON-structural open position -> the drawer/table must FLOOR the raw series tag
        _ins(conn, category="atp", ticker=ATP, condition_id="0xc3", client_order_id="c3", broker_order_id="b3")
        conn.commit()
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app)


def test_served_cfb_flat_active_table_and_sort(tmp_path, monkeypatch):
    cl = _live_client(tmp_path, monkeypatch)
    r = cl.get("/live/kalshi_jack/cfb", headers={"Remote-User": "jack"})
    assert r.status_code == 200
    b = r.text
    assert "?psort=game" in b and "?psort=placed" in b and "?psort=bet" in b   # server-side sort links (JS-off)
    assert "Placed" in b and "Game" in b and "Bet" in b and "Order" in b       # flat-table headers
    assert "SPR -6.5 MIZZ" in b or "TOT +51.5" in b                            # structural shorthand rows render
    # Complete tab + a sort param are accepted (server-rendered)
    assert cl.get("/live/kalshi_jack/cfb?tab=complete&psort=settled&pdir=desc",
                  headers={"Remote-User": "jack"}).status_code == 200


def test_served_atp_drawer_and_bet_floored(tmp_path, monkeypatch):
    cl = _live_client(tmp_path, monkeypatch)
    b = cl.get("/live/kalshi_jack/atp", headers={"Remote-User": "jack"}).text
    assert "<td>ATP</td>" in b                        # drawer Type cell = the category floor, NOT the raw KXATPMATCH tag
    assert ">ATP</span>" in b                          # the flat table's Bet cell also floors to "ATP"
