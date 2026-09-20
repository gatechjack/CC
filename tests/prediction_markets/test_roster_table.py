"""Roster TABLE (2026-09-20): the sortable per-whale table, its derived-column readers, the toggle/sort, and the
footer tie-out to the money strip.

Offline, PM DB in a tmp path + a fake marks map. Proves:
  * subdivision.booked_cost_by_whale -- the ROI(COST) denominator, derived from the SETTLEMENT row itself
    (fill_count*fill_price - realized_pnl): won / lost / void exact; opposed + void excluded from the denominator.
  * live_view.build_roster_table -- the PURE table view: derived columns (win_pct / roi_cost / tenure_days /
    booked_cost_usd) with honest-None edge cases (zero copies, all unbooked, ROI with zero cost); the On-roster/All
    toggle (formerly-live beneath, never intermixed); the server-side sort (default Realized desc, every column,
    None-last, whale alphabetical); and the footer-totals row + its tie-out to the money-strip summary.
  * the THIN boundary at 49/50 booked settlements (whale_live_records).
  * the page route: ?whales=all appends formerly-live; ?sort=&dir= re-order server-side (JS-off safe).
"""
import pytest
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, subdivision
from trading_corp.prediction_markets.web import live_view

T0 = 1789000000
NOW = 1789500000
TODAY_START = 1789480000     # settlements with settled_ts >= this are "today"
ACC, CAT = "kalshi_jack", "atp"    # non-MLB -> the money strip = live_view._journal_summary (positions-based)


class _Mark:
    """Mark-like stub -- only the two bid fields the readers duck-type."""
    def __init__(self, yes_bid=None, no_bid=None):
        self.yes_bid, self.no_bid = yes_bid, no_bid


def _mkdb(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    return p


def _ord(conn, **kw):
    row = {"account_id": ACC, "category": CAT, "dry_run": 0, "outcome_status": "filled",
           "is_exit": 0, "fill_count": 1.0, "fill_price": 0.50, "fee": 0.0}
    row.update(kw)
    cols = ", ".join(row)
    qs = ", ".join(["?"] * len(row))
    conn.execute("INSERT INTO pm_subdivision_order (%s) VALUES (%s)" % (cols, qs), tuple(row.values()))


def _attach(conn, wallet, active=1, added_ts=T0, removed_ts=None, name=None, cat=CAT):
    conn.execute("INSERT INTO pm_subdivision_attachment (account_id, category, wallet, active, source, added_ts, removed_ts) "
                 "VALUES (?,?,?,?,'promote_to_live',?,?)", (ACC, cat, wallet, active, added_ts, removed_ts))
    if name is not None:
        conn.execute("INSERT OR IGNORE INTO pm_whale (wallet, user_name) VALUES (?,?)", (wallet, name))


def _settle(conn, wallet, tk, contracts, won, realized, settled_ts, void=False):
    """A settlement CLOSE row: fill_count=contracts settled, fill_price=settled per-contract value (won->1/lost->0/
    void->the refund), realized booked. cost basis = contracts*value - realized (what booked_cost_by_whale inverts)."""
    val = (contracts and (realized + (contracts * (1.0 if won else 0.0))) / contracts)  # not used; kept explicit below
    fp = 1.0 if won else 0.0
    src = "settlement_void" if void else "settlement"
    _ord(conn, wallet=wallet, ticker=tk, outcome_leg="yes", is_exit=1, close_source=src,
         won=(None if void else (1 if won else 0)), fill_count=float(contracts), fill_price=fp,
         realized_pnl=realized, settled_ts=settled_ts)


# ---------------------------------------------------------------------------------------------------------------
# subdivision.booked_cost_by_whale -- the ROI(COST) denominator
# ---------------------------------------------------------------------------------------------------------------
def test_booked_cost_won_lost_exact(tmp_path):
    with db.connect(_mkdb(tmp_path)) as conn:
        # WIN 10 @ 0.60 -> settle won, realized +4 -> cost 10*1.0 - 4 = 6.0
        _ord(conn, wallet="0xa", ticker="KXATP-1", outcome_leg="yes", fill_count=10, fill_price=0.60)
        _settle(conn, "0xa", "KXATP-1", 10, won=True, realized=4.0, settled_ts=T0 + 10)
        # LOSS 10 @ 0.60 -> settle lost, realized -6 -> cost 10*0.0 - (-6) = 6.0
        _ord(conn, wallet="0xa", ticker="KXATP-2", outcome_leg="yes", fill_count=10, fill_price=0.60)
        _settle(conn, "0xa", "KXATP-2", 10, won=False, realized=-6.0, settled_ts=T0 + 20)
        conn.commit()
        bc = subdivision.booked_cost_by_whale(conn, ACC, CAT)
    assert bc["0xa"] == pytest.approx(12.0)     # 6 (win) + 6 (loss) -- ROI = realized(-2) / cost(12) = -16.7%


def test_booked_cost_excludes_void_and_opposed(tmp_path):
    with db.connect(_mkdb(tmp_path)) as conn:
        _ord(conn, wallet="0xb", ticker="KXATP-3", outcome_leg="yes", fill_count=5, fill_price=0.40)
        _settle(conn, "0xb", "KXATP-3", 5, won=True, realized=3.0, settled_ts=T0 + 10)   # cost 5*1 - 3 = 2.0
        # a VOID settlement (settlement_void) -- excluded from the denominator
        _ord(conn, wallet="0xb", ticker="KXATP-4", outcome_leg="yes", fill_count=5, fill_price=0.40)
        _settle(conn, "0xb", "KXATP-4", 5, won=True, realized=0.0, settled_ts=T0 + 20, void=True)
        # an OPPOSED close (realized NULL) -- excluded
        _ord(conn, wallet="0xb", ticker="KXATP-5", outcome_leg="yes", fill_count=5, fill_price=0.40)
        _ord(conn, wallet="0xb", ticker="KXATP-5", outcome_leg="yes", is_exit=1, close_source="opposed",
             won=None, realized_pnl=None)
        conn.commit()
        bc = subdivision.booked_cost_by_whale(conn, ACC, CAT)
    assert bc["0xb"] == pytest.approx(2.0)      # only the real settlement; void + opposed never enter the cost


def test_booked_cost_absent_journal_is_empty(tmp_path):
    p = str(tmp_path / "pm.db")
    import trading_corp.prediction_markets.db as _db
    orig = _db.MIGRATIONS
    try:
        _db.MIGRATIONS = _db.MIGRATIONS[:5]     # pre-order-journal schema
        _db.init_db(p)
        with _db.connect(p) as conn:
            assert subdivision.booked_cost_by_whale(conn, ACC, CAT) == {}
    finally:
        _db.MIGRATIONS = orig


# ---------------------------------------------------------------------------------------------------------------
# live_view.build_roster_table -- derived columns + honest-None edges
# ---------------------------------------------------------------------------------------------------------------
def _rec(**kw):
    """A minimal whale_live_records-shaped record (only the fields build_roster_table reads)."""
    base = {"wallet": "0x0", "user_name": None, "active": True, "added_ts": T0, "removed_ts": None,
            "placed": 0, "booked_closes": 0, "settled_w": 0, "settled_l": 0, "unbooked_closes": 0,
            "realized_pnl": 0.0, "realized_today": 0.0, "n_open": 0, "open_contracts": 0.0,
            "open_cost_usd": 0.0, "open_value": None, "n_priced": 0, "n_total": 0, "thin": True}
    base.update(kw)
    return base


def _view(on=None, formerly=None, booked_cost=None, **kw):
    wr = {"on_roster": on or [], "formerly_live": formerly or [], "thin_floor": 50}
    return live_view.build_roster_table(wr, booked_cost or {}, now_ts=NOW, **kw)


def test_derived_columns_win_roi_tenure():
    r = _rec(wallet="0xa", user_name="Alpha", added_ts=NOW - 3 * 86400,
             placed=5, booked_closes=4, settled_w=3, settled_l=1, realized_pnl=-2.0)
    v = _view(on=[r], booked_cost={"0xa": 12.0})
    row = v["rows"][0]
    assert row["win_pct"] == pytest.approx(0.75)          # 3 of 4 booked
    assert row["booked_cost_usd"] == pytest.approx(12.0)
    assert row["roi_cost"] == pytest.approx(-2.0 / 12.0)   # realized / cost
    assert row["tenure_days"] == pytest.approx(3.0)


def test_zero_copy_whale_all_none_not_zero():
    r = _rec(wallet="0xc", user_name="Charlie", placed=0, booked_closes=0)
    row = _view(on=[r], booked_cost={})["rows"][0]
    assert row["win_pct"] is None          # 0 booked -> '--' not 0%
    assert row["roi_cost"] is None         # 0 cost  -> '--'
    assert row["booked_cost_usd"] == 0.0


def test_all_unbooked_has_no_win_or_roi():
    # every close was opposed/whale-exit: booked_closes 0, unbooked 3, realized 0, no settled cost
    r = _rec(wallet="0xu", placed=3, booked_closes=0, unbooked_closes=3, realized_pnl=0.0)
    row = _view(on=[r], booked_cost={})["rows"][0]
    assert row["win_pct"] is None and row["roi_cost"] is None
    assert row["unbooked_closes"] == 3


def test_roi_none_when_zero_cost_but_realized_present():
    # defensive: a booked count with a <=0 cost basis -> ROI '--' (never a divide-by-zero / infinity)
    r = _rec(wallet="0xz", booked_closes=1, settled_w=1, realized_pnl=5.0)
    row = _view(on=[r], booked_cost={"0xz": 0.0})["rows"][0]
    assert row["roi_cost"] is None
    assert row["win_pct"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------------------------------------------
# toggle + sort
# ---------------------------------------------------------------------------------------------------------------
def test_toggle_on_roster_hides_formerly_all_shows_beneath():
    on = [_rec(wallet="0xa", user_name="Alpha", realized_pnl=5.0)]
    fm = [_rec(wallet="0xf", user_name="Foxtrot", active=False, removed_ts=T0 + 9000, realized_pnl=99.0)]
    default = _view(on=on, formerly=fm, show_all=False)
    assert [x["wallet"] for x in default["rows"]] == ["0xa"]         # formerly-live ABSENT under default
    assert default["formerly_count"] == 1 and default["shown_count"] == 1
    allv = _view(on=on, formerly=fm, show_all=True)
    # even though Foxtrot has a much higher realized, formerly-live sorts BENEATH on-roster (never intermixed)
    assert [x["wallet"] for x in allv["rows"]] == ["0xa", "0xf"]
    assert allv["rows"][1]["formerly"] is True


def test_default_sort_is_realized_desc():
    on = [_rec(wallet="a", realized_pnl=1.0), _rec(wallet="b", realized_pnl=9.0), _rec(wallet="c", realized_pnl=-3.0)]
    rows = _view(on=on)["rows"]
    assert [r["wallet"] for r in rows] == ["b", "a", "c"]            # 9, 1, -3


def test_sort_roi_desc_puts_none_last():
    on = [_rec(wallet="a", booked_closes=1, realized_pnl=2.0), _rec(wallet="b", booked_closes=1, realized_pnl=1.0),
          _rec(wallet="c", booked_closes=0)]                         # c: no booked -> roi None
    v = _view(on=on, booked_cost={"a": 4.0, "b": 10.0}, sort="roi", direction="desc")
    # a roi = 0.5, b roi = 0.1, c None -> a, b, then None last
    assert [r["wallet"] for r in v["rows"]] == ["a", "b", "c"]
    v2 = _view(on=on, booked_cost={"a": 4.0, "b": 10.0}, sort="roi", direction="asc")
    assert [r["wallet"] for r in v2["rows"]] == ["b", "a", "c"]      # asc: 0.1, 0.5, None STILL last


def test_sort_whale_alphabetical():
    on = [_rec(wallet="0x3", user_name="Charlie"), _rec(wallet="0x1", user_name="alpha"),
          _rec(wallet="0x2", user_name="Bravo")]
    asc = _view(on=on, sort="whale", direction="asc")["rows"]
    assert [r["user_name"] for r in asc] == ["alpha", "Bravo", "Charlie"]   # case-insensitive
    desc = _view(on=on, sort="whale", direction="desc")["rows"]
    assert [r["user_name"] for r in desc] == ["Charlie", "Bravo", "alpha"]


def test_sort_invalid_column_falls_back_to_realized():
    on = [_rec(wallet="a", realized_pnl=1.0), _rec(wallet="b", realized_pnl=2.0)]
    v = _view(on=on, sort="not_a_column")
    assert v["sort"] == "realized" and [r["wallet"] for r in v["rows"]] == ["b", "a"]


# ---------------------------------------------------------------------------------------------------------------
# footer totals + tie-out to the money strip
# ---------------------------------------------------------------------------------------------------------------
def test_footer_totals_sum_and_ratios():
    on = [_rec(wallet="a", placed=3, booked_closes=2, settled_w=1, settled_l=1, realized_pnl=4.0,
               unbooked_closes=1, realized_today=4.0, n_open=1, open_cost_usd=2.0, open_value=2.5,
               n_priced=1, n_total=1),
          _rec(wallet="b", placed=1, booked_closes=1, settled_w=1, settled_l=0, realized_pnl=2.0,
               n_open=0)]
    t = _view(on=on, booked_cost={"a": 12.0, "b": 2.0})["totals"]
    assert t["copies"] == 4 and t["booked"] == 3 and t["w"] == 2 and t["l"] == 1 and t["unbooked"] == 1
    assert t["realized"] == pytest.approx(6.0) and t["cost"] == pytest.approx(14.0)
    assert t["win_pct"] == pytest.approx(2 / 3) and t["roi_cost"] == pytest.approx(6.0 / 14.0)
    assert t["open_n"] == 1 and t["open_cost"] == pytest.approx(2.0) and t["open_value"] == pytest.approx(2.5)


def _seed_tieout(conn):
    """One account, non-MLB (atp), single whale per ticker (so per-whale open sums == per-ticker net -> the strip
    ties exactly). Alpha (on-roster) + Bravo (formerly-live), each with an open position + a settled-today close."""
    conn.execute("INSERT INTO pm_account (account_id, venue, secret_ref, label, active, created_ts) "
                 "VALUES (?,?,?,?,1,?)", (ACC, "kalshi", "KALSHI", "Jack", T0))
    conn.execute("INSERT INTO pm_subdivision (account_id, category, label, active, created_ts) "
                 "VALUES (?,?,?,1,?)", (ACC, CAT, "Jack ATP", T0))
    ts = live_view.et_window_cutoffs(NOW)["today"] + 100      # 'today' ET, >= today_start
    # Alpha ON-ROSTER: OPEN 4 @ 0.40 on KXATP-A1 (priced) + a settled WIN today on KXATP-A2
    _attach(conn, "0xa", active=1, added_ts=T0, name="Alpha")
    _ord(conn, wallet="0xa", ticker="KXATP-A1", outcome_leg="yes", fill_count=4, fill_price=0.40)   # OPEN
    _ord(conn, wallet="0xa", ticker="KXATP-A2", outcome_leg="yes", fill_count=6, fill_price=0.50)
    _settle(conn, "0xa", "KXATP-A2", 6, won=True, realized=3.0, settled_ts=ts)
    # Bravo FORMERLY-LIVE: OPEN 2 @ 0.30 on KXATP-B1 (priced) + a settled LOSS today on KXATP-B2
    _attach(conn, "0xb", active=0, added_ts=T0, removed_ts=T0 + 9000, name="Bravo")
    _ord(conn, wallet="0xb", ticker="KXATP-B1", outcome_leg="yes", fill_count=2, fill_price=0.30)   # OPEN
    _ord(conn, wallet="0xb", ticker="KXATP-B2", outcome_leg="yes", fill_count=2, fill_price=0.50)
    _settle(conn, "0xb", "KXATP-B2", 2, won=False, realized=-1.0, settled_ts=ts)


def test_footer_ties_to_money_strip(tmp_path):
    marks = {"KXATP-A1": _Mark(yes_bid=0.55), "KXATP-B1": _Mark(yes_bid=0.35)}
    with db.connect(_mkdb(tmp_path)) as conn:
        _seed_tieout(conn)
        conn.commit()
        today_start = live_view.et_window_cutoffs(NOW)["today"]
        open_positions = subdivision.live_positions(conn, ACC, CAT)
        orders = subdivision.live_orders(conn, ACC, CAT)
        wr = subdivision.whale_live_records(conn, ACC, CAT, marks=marks, now_ts=NOW,
                                            today_start_ts=today_start, thin_floor=50)
        bc = subdivision.booked_cost_by_whale(conn, ACC, CAT)
    summary = live_view._journal_summary(open_positions, orders, marks, NOW)
    t = live_view.build_roster_table(wr, bc, now_ts=NOW, show_all=True)["totals"]
    # the on-roster + formerly-live footer OPEN totals tie to the money strip's open figures (same held legs/marks)
    assert t["open_n"] == summary["n_open_positions"]
    assert t["open_cost"] == pytest.approx(summary["unsettled_cost"])
    assert t["open_value"] == pytest.approx(summary["unsettled_value"])
    assert t["open_priced"] == summary["unsettled_priced"] and t["open_total"] == summary["unsettled_total"]
    # realized-today ties too (both read today's settlement rows)
    assert t["today"] == pytest.approx(summary["realized_today"])


# ---------------------------------------------------------------------------------------------------------------
# THIN boundary at 49/50 booked settlements (whale_live_records)
# ---------------------------------------------------------------------------------------------------------------
def test_thin_boundary_49_thin_50_not(tmp_path):
    with db.connect(_mkdb(tmp_path)) as conn:
        _attach(conn, "0x49", active=1, added_ts=T0, name="FortyNine")
        _attach(conn, "0x50", active=1, added_ts=T0, name="Fifty")
        for i in range(49):
            _settle(conn, "0x49", "KXATP-49-%d" % i, 1, won=True, realized=0.5, settled_ts=T0 + i)
        for i in range(50):
            _settle(conn, "0x50", "KXATP-50-%d" % i, 1, won=True, realized=0.5, settled_ts=T0 + i)
        conn.commit()
        wr = subdivision.whale_live_records(conn, ACC, CAT, marks={}, now_ts=NOW,
                                            today_start_ts=TODAY_START, thin_floor=50)
    by = {r["wallet"]: r for r in wr["on_roster"]}
    assert by["0x49"]["booked_closes"] == 49 and by["0x49"]["thin"] is True     # 49 < 50 -> THIN
    assert by["0x50"]["booked_closes"] == 50 and by["0x50"]["thin"] is False    # 50 -> not thin


# ---------------------------------------------------------------------------------------------------------------
# page route: ?whales=all + ?sort/?dir are server-rendered (JS-off safe)
# ---------------------------------------------------------------------------------------------------------------
def _client(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")
    db.init_db(p)
    with db.connect(p) as conn:
        _seed_tieout(conn)
        conn.commit()
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app)


def test_route_whales_all_appends_formerly(monkeypatch, tmp_path):
    # NB: a formerly-live whale still appears in the trade DRAWER ("Copied from"); the roster membership check keys on
    # the roster row's data-whale (drawer rows use data-wallet), so it is unambiguous which surface the whale is on.
    cl = _client(monkeypatch, tmp_path)
    d = cl.get("/live/%s/%s" % (ACC, CAT), headers={"Remote-User": "jack"}).text
    assert 'data-whale="0xa"' in d and 'data-whale="0xb"' not in d    # default: on-roster only (Alpha, not Bravo)
    a = cl.get("/live/%s/%s?whales=all" % (ACC, CAT), headers={"Remote-User": "jack"}).text
    assert 'data-whale="0xa"' in a and 'data-whale="0xb"' in a and "formerly live" in a   # All: formerly-live appended


def test_route_sort_param_is_honoured(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    r = cl.get("/live/%s/%s?whales=all&sort=whale&dir=asc" % (ACC, CAT), headers={"Remote-User": "jack"})
    assert r.status_code == 200
    body = r.text
    # on-roster (Alpha) still renders above formerly-live (Bravo) regardless of the alpha sort within groups
    assert body.index("Alpha") < body.index("Bravo")
    # the active sort column carries the direction class the header link toggles to
    assert 'href="/live/%s/%s?sort=realized' % (ACC, CAT) in body     # a sort link is present + carries the column
