"""Roster TENURE = multi-span from the migration-024 attachment-event log (2026-09-20).

Proves live_view.build_spans (pure span pairing + the permanent pre-024 attachment-row fallback), build_roster_table's
spans + tenure_sort wiring (R1/R5), the served roster page's multi-span Tenure cell, and the R4 regressions that must
survive the edit (score 'not analyzed' not 0; a flagged score renders flagged; Detach is admin-or-owner).
"""
import pytest
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db
from trading_corp.prediction_markets.web import live_view as LV

NOW = 1789900000
DAY = 86400


def ev(action, ts, wallet="0xa"):
    return {"action": action, "ts": ts, "wallet": wallet}


# ── build_spans: pairing + fallback + malformed (R1/R2/R6) ──────────────────────────────────────────────────────
def test_fallback_open_when_no_events_and_active():
    s = LV.build_spans([], NOW - 3 * DAY, None, True, NOW)
    assert len(s) == 1 and s[0]["start"] == NOW - 3 * DAY and s[0]["end"] is None and round(s[0]["days"]) == 3


def test_fallback_closed_when_no_events_and_detached():
    s = LV.build_spans([], NOW - 10 * DAY, NOW - 2 * DAY, False, NOW)
    assert len(s) == 1 and s[0]["start"] == NOW - 10 * DAY and s[0]["end"] == NOW - 2 * DAY and round(s[0]["days"]) == 8


def test_fallback_empty_when_no_events_and_no_added():
    assert LV.build_spans([], None, None, True, NOW) == []


def test_one_open_span_from_events_overrides_attachment_row():
    s = LV.build_spans([ev("attach", NOW - 5 * DAY)], NOW - 9 * DAY, None, True, NOW)
    assert len(s) == 1 and s[0]["start"] == NOW - 5 * DAY and s[0]["end"] is None   # events win when present


def test_two_closed_spans_newest_first():
    s = LV.build_spans([ev("attach", 100), ev("detach", 200), ev("attach", 300), ev("detach", 400)], None, None, False, NOW)
    assert [(x["start"], x["end"]) for x in s] == [(300, 400), (100, 200)]


def test_closed_then_open_newest_is_open():
    s = LV.build_spans([ev("attach", 100), ev("detach", 200), ev("attach", 300)], None, None, True, NOW)
    assert [(x["start"], x["end"]) for x in s] == [(300, None), (100, 200)]


def test_out_of_order_events_sorted_by_ts():
    s = LV.build_spans([ev("detach", 400), ev("attach", 300), ev("detach", 200), ev("attach", 100)], None, None, False, NOW)
    assert [(x["start"], x["end"]) for x in s] == [(300, 400), (100, 200)]


def test_malformed_double_attach_no_invented_detach():
    # [attach, attach, detach] -> ONE closed span (100..300, the REAL detach); the redundant attach is ignored, and
    # NO detach is fabricated between the two attaches (R1/R6).
    s = LV.build_spans([ev("attach", 100), ev("attach", 200), ev("detach", 300)], None, None, False, NOW)
    assert [(x["start"], x["end"]) for x in s] == [(100, 300)]


def test_malformed_double_attach_open_stays_open():
    s = LV.build_spans([ev("attach", 100), ev("attach", 200)], None, None, True, NOW)
    assert [(x["start"], x["end"]) for x in s] == [(100, None)]   # one open span, no fabricated detach


def test_malformed_lone_detach_falls_back_to_attachment_row():
    s = LV.build_spans([ev("detach", 500)], NOW - 4 * DAY, None, True, NOW)   # yields no span -> fallback
    assert len(s) == 1 and s[0]["start"] == NOW - 4 * DAY and s[0]["end"] is None


# ── build_roster_table: spans + tenure_sort (R1/R5) ─────────────────────────────────────────────────────────────
def _rec(**kw):
    base = {"wallet": "0xa", "user_name": None, "active": True, "added_ts": 1000, "removed_ts": None,
            "placed": 0, "booked_closes": 0, "settled_w": 0, "settled_l": 0, "unbooked_closes": 0,
            "realized_pnl": 0.0, "realized_today": 0.0, "n_open": 0, "open_contracts": 0.0,
            "open_cost_usd": 0.0, "open_value": None, "n_priced": 0, "n_total": 0, "thin": True}
    base.update(kw)
    return base


def _view(on=None, formerly=None, events=None, **kw):
    wr = {"on_roster": on or [], "formerly_live": formerly or [], "thin_floor": 50}
    return LV.build_roster_table(wr, {}, now_ts=NOW, events=events, **kw)


def test_spans_attached_and_tenure_sort_open_start():
    on = [_rec(wallet="0xa", added_ts=1000, active=True)]
    v = _view(on=on, events=[ev("attach", 5000, "0xa")])
    row = v["rows"][0]
    assert row["spans"][0]["start"] == 5000 and row["spans"][0]["end"] is None
    assert row["tenure_sort"] == 5000.0                      # on-roster sorts by the OPEN span's start


def test_tenure_sort_formerly_uses_latest_end_and_two_spans():
    fm = [_rec(wallet="0xf", active=False, added_ts=100, removed_ts=200)]
    events = [ev("attach", 100, "0xf"), ev("detach", 200, "0xf"), ev("attach", 300, "0xf"), ev("detach", 400, "0xf")]
    v = _view(formerly=fm, show_all=True, events=events)
    row = [r for r in v["rows"] if r["formerly"]][0]
    assert row["tenure_sort"] == 400.0                       # formerly sorts by the LATEST span's end
    assert [(s["start"], s["end"]) for s in row["spans"]] == [(300, 400), (100, 200)]


def test_tenure_column_sorts_on_roster_by_open_start_desc():
    on = [_rec(wallet="a", added_ts=1000), _rec(wallet="b", added_ts=3000), _rec(wallet="c", added_ts=2000)]
    v = _view(on=on, sort="tenure", direction="desc")        # no events -> fallback spans from added_ts
    assert [r["wallet"] for r in v["rows"]] == ["b", "c", "a"]


def test_no_events_param_preserves_single_span_fallback():
    on = [_rec(wallet="0xa", added_ts=NOW - 3 * DAY, active=True)]
    row = _view(on=on)["rows"][0]                            # events=None default
    assert len(row["spans"]) == 1 and row["spans"][0]["end"] is None
    assert round(row["tenure_days"]) == 3                    # unchanged from the pre-024 single-span behaviour


# ── served page: multi-span render + R4 regressions (score not-analyzed / flagged; detach authz) ─────────────────
T0 = 1789000000


def _seed(conn):
    conn.execute("INSERT INTO pm_account (account_id,venue,secret_ref,label,active,created_ts,owner_identity) VALUES "
                 "('kalshi_jack','kalshi','K','Jack',1,?,NULL)", (T0,))
    conn.execute("INSERT INTO pm_account (account_id,venue,secret_ref,label,active,created_ts,owner_identity) VALUES "
                 "('kalshi_karen','kalshi','K','Karen',1,?,'karen')", (T0,))
    for aid in ("kalshi_jack", "kalshi_karen"):
        conn.execute("INSERT INTO pm_subdivision (account_id,category,label,active,created_ts) VALUES (?,?,?,1,?)",
                     (aid, "mlb", aid + " mlb", T0))

    def attach(aid, wal, active=1, added=T0, removed=None, name=None):
        conn.execute("INSERT INTO pm_subdivision_attachment (account_id,category,wallet,active,source,added_ts,removed_ts) "
                     "VALUES (?,?,?,?,'promote_to_live',?,?)", (aid, "mlb", wal, active, added, removed))
        if name:
            conn.execute("INSERT OR IGNORE INTO pm_whale (wallet,user_name) VALUES (?,?)", (wal, name))

    def evrow(aid, wal, action, ts):
        conn.execute("INSERT INTO pm_subdivision_attachment_event (account_id,category,wallet,action,source,actor,ts) "
                     "VALUES (?,?,?,?,'test','jack',?)", (aid, "mlb", wal, action, ts))

    def score(wal, tier, grounded=1, omission=0.0):
        conn.execute("INSERT INTO pm_whale_score (wallet,category,tier,reason,sort_roi,grounded,omission_pct,coverage_pct,"
                     "omission_floor,honest_roi,copy_fills,computed_ts) VALUES (?,?,?,?,?,?,?,?,0,?,0,?)",
                     (wal, "mlb", tier, tier, 0.2, grounded, omission, 0.9, 0.2, T0))

    # W3SPAN: on-roster, THREE-event history attach/detach/attach(open) -> 1 open + 1 earlier span
    attach("kalshi_jack", "0x3span", active=1, added=T0, name="TriSpan")
    for a, t in (("attach", T0), ("detach", T0 + 5 * DAY), ("attach", T0 + 9 * DAY)):
        evrow("kalshi_jack", "0x3span", a, t)
    score("0x3span", "PROMOTE")
    # WUNAN: on-roster, un-analyzed (no score row) -> "not analyzed"
    attach("kalshi_jack", "0xunan", active=1, added=T0, name="UnAnalyzed")
    evrow("kalshi_jack", "0xunan", "attach", T0)
    # WFLAG: on-roster, ungrounded score -> flagged (omit UNKNOWN)
    attach("kalshi_jack", "0xflag", active=1, added=T0, name="FlagWhale")
    evrow("kalshi_jack", "0xflag", "attach", T0)
    score("0xflag", "INSUFFICIENT_DATA", grounded=0)
    # W2SPAN: formerly-live, TWO closed spans
    attach("kalshi_jack", "0x2span", active=0, added=T0, removed=T0 + 12 * DAY, name="DuoSpan")
    for a, t in (("attach", T0), ("detach", T0 + 3 * DAY), ("attach", T0 + 8 * DAY), ("detach", T0 + 12 * DAY)):
        evrow("kalshi_jack", "0x2span", a, t)
    # karen own whale (detach authz)
    attach("kalshi_karen", "0xk", active=1, added=T0, name="Kilo")
    evrow("kalshi_karen", "0xk", "attach", T0)


def _client(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")
    db.init_db(p)
    with db.connect(p) as conn:
        _seed(conn); conn.commit()
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app)


def test_served_roster_renders_multi_span(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    b = cl.get("/live/kalshi_jack/mlb?whales=all", headers={"Remote-User": "jack"}).text
    # W3SPAN (on-roster): an OPEN current span ("attached ... Nd") + one earlier closed span (dimmed). Count span
    # ELEMENTS precisely -- `rt-span"` (the current) + `rt-span earlier"` (the earlier); not the rt-spans/-more wrappers.
    i = b.find('data-whale="0x3span"'); seg = b[i:b.find("</tr>", i)]
    assert seg.count('class="rt-span"') == 1 and seg.count('class="rt-span earlier"') == 1
    assert "attached" in seg and "&ndash;" in seg           # one open ("attached ... Nd") + one closed range
    # W2SPAN (formerly-live): TWO closed spans, both a date range (no open "attached ... Nd" line)
    j = b.find('data-whale="0x2span"'); seg2 = b[j:b.find("</tr>", j)]
    assert seg2.count('class="rt-span"') == 1 and seg2.count('class="rt-span earlier"') == 1
    tcell = seg2[seg2.find('class="rt-tenure"'):seg2.find("</td>", seg2.find('class="rt-tenure"'))]
    assert tcell.count("&ndash;") == 2 and "attached" not in tcell   # both closed ranges, no open line


def test_R4_score_not_analyzed_and_flagged(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    b = cl.get("/live/kalshi_jack/mlb", headers={"Remote-User": "jack"}).text
    iu = b.find('data-whale="0xunan"'); su = b[iu:b.find("</tr>", iu)]
    assert "not" in su and "analyzed" in su                  # un-analyzed reads "not analyzed", never a 0
    ifl = b.find('data-whale="0xflag"'); sf = b[ifl:b.find("</tr>", ifl)]
    assert "omit" in sf.lower() and "UNKNOWN" in sf          # ungrounded score renders FLAGGED (omit UNKNOWN)


def test_R4_detach_admin_or_owner(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    assert cl.get("/live/kalshi_karen/mlb/detach/0xk", headers={"Remote-User": "karen"}).status_code == 200   # own
    assert cl.get("/live/kalshi_jack/mlb/detach/0x3span", headers={"Remote-User": "karen"}).status_code == 403  # not hers
