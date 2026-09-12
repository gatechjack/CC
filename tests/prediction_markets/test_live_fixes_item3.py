"""Item 3 (mark cache) -- render side (2026-09-12). The WIP persisted titles + merged marks in ui_cache; these
prove the render now CONSUMES it: a failed/partial poll keeps prior marks WITH their real age, titles survive an
empty cycle, the first cycle reads 'marks loading' (never 'no mark'), and a NON-MLB label is NEVER a raw ticker
(the describe_market '<type>:<ticker>' leak is gone; the floor is '<CATEGORY> <MARKET-TYPE>')."""
import pytest
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, subdivision
from trading_corp.prediction_markets.web import ui_cache, marks as MK, live_view as LV

NOW = 1789200000


def _mk(t, yb=0.50, title="Market title", as_of=NOW):
    return MK.Mark(ticker=t, yes_bid=yb, no_bid=None, yes_ask=None, no_ask=None, last=None, status="active",
                   as_of=as_of, title=title)


def _res(d, ok=True, err=None, as_of=NOW):
    return MK.MarksResult(marks=d, ok=ok, as_of=as_of, error=err)


# ---------------------------------------------------------------------------------------------------------------
# ui_cache merge (the foundation, now exercised)
# ---------------------------------------------------------------------------------------------------------------
def test_failed_fetch_keeps_prior_marks_with_age():
    c = ui_cache.UICache()
    c.update(slates={}, marks=_res({"KXA-1": _mk("KXA-1", as_of=NOW - 300)}), refreshed_ts=NOW - 300)
    c.update(slates={}, marks=_res({}, ok=False, err="KXA:HTTPError"), refreshed_ts=NOW)   # this series FAILED this poll
    s = c.snapshot()
    assert "KXA-1" in s.marks.marks                       # prior mark NOT wiped
    assert s.marks.marks["KXA-1"].as_of == NOW - 300      # its OWN old age preserved (banded amber by the render)
    assert s.marks.ok is False                            # THIS poll's status -> drives the "refresh failed" note


def test_partial_fetch_updates_only_returned_tickers():
    c = ui_cache.UICache()
    c.update(slates={}, marks=_res({"KXA-1": _mk("KXA-1", yb=0.5, as_of=NOW - 100),
                                    "KXB-1": _mk("KXB-1", yb=0.4, as_of=NOW - 100)}), refreshed_ts=NOW - 100)
    c.update(slates={}, marks=_res({"KXA-1": _mk("KXA-1", yb=0.7, as_of=NOW)}), refreshed_ts=NOW)   # only KXA this poll
    m = c.snapshot().marks.marks
    assert m["KXA-1"].yes_bid == 0.7 and m["KXA-1"].as_of == NOW           # fresh
    assert m["KXB-1"].yes_bid == 0.4 and m["KXB-1"].as_of == NOW - 100     # kept prior (series absent this poll)


def test_titles_survive_an_empty_cycle():
    c = ui_cache.UICache()
    c.update(slates={}, marks=_res({"KXA-1": _mk("KXA-1", title="Missouri wins")}), refreshed_ts=NOW - 100)
    c.update(slates={}, marks=_res({}, ok=False), refreshed_ts=NOW)        # empty/failed cycle
    assert c.title("KXA-1") == "Missouri wins"                             # persisted, never evicted


# ---------------------------------------------------------------------------------------------------------------
# render side: no raw ticker floor + titles-from-persisted + marks-loading
# ---------------------------------------------------------------------------------------------------------------
_ORD = {"account_id": "kalshi_jack", "category": "cfb", "wallet": "0xa", "ticker": "KXNCAAFTOTAL-26SEP11MIZZKU-52",
        "outcome_leg": "yes", "is_exit": 0, "dry_run": 0, "outcome_status": "filled", "fill_count": 2.0,
        "fill_price": 0.40, "fee": 0.01}
_POS = {"ticker": "KXNCAAFTOTAL-26SEP11MIZZKU-52", "held_leg": "yes", "contracts": 2.0, "cost_basis_usd": 0.80,
        "avg_price": 0.40, "fees_usd": 0.01, "market_type": "total"}
_POSW = dict(_POS, wallet="0xa", user_name=None)


def _ctx(marks_result, titles=None, poll_status=None):
    return LV.build_live_context(orders=[_ORD], open_positions=[_POS], open_positions_by_whale=[_POSW],
                                 slate=None, marks_result=marks_result, now_ts=NOW, category="cfb",
                                 titles=titles, poll_status=poll_status)


def test_nonmlb_label_never_a_raw_ticker_on_cold_cache_and_failed_poll():
    # Item 2 (2026-09-12): a CFB ticker decodes to matchup + signed shorthand, so the desc is the shorthand
    # ('TOT +51.5', TICKER-derived -> present even on a cold cache/failed poll), never a raw ticker. The
    # '<CATEGORY> <TYPE>' base floor still applies to a no-matchup ticker (tennis/ufc) -- see test_live_fixes_item2.
    ctx = _ctx(_res({}, ok=False, err="KXNCAAFTOTAL:HTTPError"), titles={})   # cold titles + failed poll
    row = ctx["positions_view"]["active"][0]
    assert row["desc"] == "TOT +51.5"                     # signed shorthand (Item 2), NOT a ticker
    assert "KX" not in row["desc"]
    assert row["market_title"] is None                   # no title has ever resolved
    assert row["value_known"] is False and row["ever_priced"] is False


def test_nonmlb_label_uses_persisted_title_when_known():
    ctx = _ctx(_res({}, ok=False), titles={"KXNCAAFTOTAL-26SEP11MIZZKU-52": "Over 51.5 points"})
    row = ctx["positions_view"]["active"][0]
    # Item 2: the signed shorthand is the PRIMARY; the persisted title (which survives the failed poll -- the Item-3
    # merge point) is the SECONDARY line. Both hold; neither is a raw ticker.
    assert row["desc"] == "TOT +51.5" and "KX" not in row["desc"]
    assert row["sub"] == "Over 51.5 points"              # persisted title survives the failed poll (now the secondary)
    assert row["ever_priced"] is True


def test_value_carries_mark_age_for_amber_banding():
    ctx = _ctx(_res({"KXNCAAFTOTAL-26SEP11MIZZKU-52": _mk("KXNCAAFTOTAL-26SEP11MIZZKU-52", yb=0.55, as_of=NOW - 400)},
                    ok=False), titles={})                  # stale mark (400s old), this poll failed
    row = ctx["positions_view"]["active"][0]
    assert row["value_known"] is True and row["current_value"] == pytest.approx(2 * 0.55)
    assert row["age_sec"] == 400                          # the render bands this amber (> 180)


def test_mark_status_surfaces_refresh_failed():
    ctx = _ctx(_res({}, ok=False, err="KXNFLTOTAL:HTTPError"), titles={},
               poll_status={"marks_ok": False, "refreshed_ts": NOW - 120, "last_error": "KXNFLTOTAL:HTTPError"})
    ms = ctx["mark_status"]
    assert ms["ok"] is False and ms["error"] == "KXNFLTOTAL:HTTPError" and ms["age_sec"] == 120


def test_first_cycle_is_warming_marks_loading():
    c = ui_cache.UICache()                                 # cold: never polled
    ctx = LV.build_from_cache(orders=[_ORD], open_positions=[_POS], open_positions_by_whale=[_POSW],
                              cache=c, now_ts=NOW, category="cfb")
    assert ctx["warming"] is True                         # -> template shows "marks loading", never "no mark"
    row = ctx["positions_view"]["active"][0]
    assert row["value_known"] is False and "KX" not in row["desc"]


# ---------------------------------------------------------------------------------------------------------------
# rendered page: zero raw tickers as a visible label on a cold cache + failed poll
# ---------------------------------------------------------------------------------------------------------------
def _client(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_account (account_id,venue,secret_ref,label,active,created_ts,owner_identity) "
                     "VALUES ('kalshi_jack','kalshi','K','Jack',1,?,NULL)", (NOW,))
        conn.execute("INSERT INTO pm_subdivision (account_id,category,label,active,created_ts) "
                     "VALUES ('kalshi_jack','cfb','Jack CFB',1,?)", (NOW,))
        conn.execute("INSERT INTO pm_subdivision_attachment (account_id,category,wallet,active,source,added_ts) "
                     "VALUES ('kalshi_jack','cfb','0xa',1,'seed',?)", (NOW,))
        cols = ", ".join(_ORD); qs = ", ".join(["?"] * len(_ORD))
        conn.execute("INSERT INTO pm_subdivision_order (%s) VALUES (%s)" % (cols, qs), tuple(_ORD.values()))
        conn.commit()
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app)


def test_rendered_cfb_positions_label_has_no_raw_ticker(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)   # cold ui_cache -> warming, no marks
    html = cl.get("/live/kalshi_jack/cfb", headers={"Remote-User": "jack"}).text
    # the positions table's VISIBLE label (the .pt span text) must be the shorthand, never the raw ticker
    import re
    labels = re.findall(r'<span class="pt"[^>]*>([^<]*)</span>', html)
    assert labels, "expected at least one position label rendered"
    assert all("KX" not in lbl for lbl in labels), labels     # zero raw tickers as a visible label
    assert any("TOT" in lbl for lbl in labels)                # Item 2: the signed shorthand market-type tag
    assert "MIZZ @ KU" in html                                # Item 2: the row groups under its game header
