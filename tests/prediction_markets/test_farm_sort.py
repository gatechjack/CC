"""Phase 1 (2026-09-25): server-side URL sort for the Farm Watchlist + Prospects tables (OQ-1) and the state-aware
Analyze action button on Prospects (OQ-2). Pure sort helpers + bare-Jinja partial renders (NO TestClient -> off the
pre-existing UI-render failure surface, same idiom as test_prospects_score)."""
import os

from trading_corp.prediction_markets.web import app, live_view


# ── OQ-1: server-side sort helpers ───────────────────────────────────────────────────────────────────
def _wl_rows():
    return [
        {"user_name": "Bravo", "wallet": "0xb", "n_open": 2, "n_closed": 10, "win_rate": 0.5, "roi": 0.10,
         "net_paper_pnl": 5.0, "cost_basis": 50.0, "n_stale": 0, "n_void": 0, "score": {"sort_value": -1e12}},
        {"user_name": "Alpha", "wallet": "0xa", "n_open": 1, "n_closed": None, "win_rate": None, "roi": 0.90,
         "net_paper_pnl": None, "cost_basis": None, "n_stale": 1, "n_void": 0, "score": {"sort_value": 5.0}},
        {"user_name": None, "wallet": "0xz", "n_open": 3, "n_closed": 4, "win_rate": 0.25, "roi": None,
         "net_paper_pnl": -2.0, "cost_basis": 20.0, "n_stale": 0, "n_void": 1, "score": {"sort_value": 9.0}},
    ]


def test_watchlist_default_is_name_asc_nameless_last():
    rows = _wl_rows()
    s, d = live_view.sort_watchlist(rows)
    assert (s, d) == ("whale", "asc")
    assert [r["wallet"] for r in rows] == ["0xa", "0xb", "0xz"]   # Alpha, Bravo, then the nameless wallet LAST


def test_watchlist_numeric_sort_puts_none_last_both_ways():
    for direction in ("desc", "asc"):
        rows = _wl_rows()
        live_view.sort_watchlist(rows, "roi", direction)
        assert rows[-1]["roi"] is None                            # None ALWAYS last, both directions
        vals = [r["roi"] for r in rows if r["roi"] is not None]
        assert vals == (sorted(vals, reverse=True) if direction == "desc" else sorted(vals))


def test_watchlist_every_column_is_sortable():
    # OQ-1: the Watchlist is sortable on EVERY column (incl win%). An unknown column falls back to the default.
    for col in ("whale", "judge", "open", "closed", "stale", "void", "winpct", "roi", "netpnl", "cost"):
        rows = _wl_rows()
        s, _ = live_view.sort_watchlist(rows, col, "desc")
        assert s == col
    rows = _wl_rows()
    s, d = live_view.sort_watchlist(rows, "bogus", "desc")
    assert (s, d) == ("whale", "asc")                             # invalid -> default


def _pr_rows():
    return [
        {"user_name": "P", "wallet": "0xp", "roi": 0.20, "n_resolved": 40, "net_realized_pnl": 100.0,
         "last_refresh": {"ts": 10}, "thin_sample": False,
         "score": app._score_cell({"tier": "PROMOTE", "sort_roi": 0.42, "grounded": 1, "coverage_pct": 0.9,
                                    "omission_pct": 0.0, "computed_ts": 900}, 1000)},
        {"user_name": "M", "wallet": "0xm", "roi": 0.88, "n_resolved": 5, "net_realized_pnl": 200.0,
         "last_refresh": {"ts": 20}, "thin_sample": True,
         "score": app._score_cell({"tier": "INSUFFICIENT_DATA", "sort_roi": 0.885, "grounded": 1,
                                   "coverage_pct": 0.36, "omission_pct": 0.8, "computed_ts": 900}, 1000)},
        {"user_name": "U", "wallet": "0xu", "roi": None, "n_resolved": 12, "net_realized_pnl": 50.0,
         "last_refresh": {"ts": 30}, "thin_sample": False, "score": app._score_cell(None, 1000)},
    ]


def test_prospects_default_is_cost_roi_desc_none_last():
    rows = _pr_rows()
    s, d = live_view.sort_prospects(rows)
    assert (s, d) == ("roi", "desc")
    assert [r["roi"] for r in rows] == [0.88, 0.20, None]         # cost-ROI desc, None last (unchanged default)


def test_prospects_winpct_is_not_a_sortable_column():
    # win% stays deliberately NON-sortable (the completed-trade API under-reports losses) -> falls back to default.
    rows = _pr_rows()
    s, d = live_view.sort_prospects(rows, "winpct", "desc")
    assert (s, d) == ("roi", "desc")


# ── bare-Jinja partial renders (header links + JS-off + button states) ────────────────────────────────
def _env():
    from jinja2 import Environment, FileSystemLoader
    tdir = os.path.join(os.path.dirname(app.__file__), "templates")
    return Environment(loader=FileSystemLoader(tdir))


def _render_watchlist(**ctx):
    base = dict(live_accounts=[], live_attach={})
    base.update(ctx)
    return _env().get_template("partials/pm_watchlist_rows.html").render(**base)


def _render_prospects(rows, **ctx):
    base = dict(prospects=rows, loss_omission_caveat="x", thin_sample_floor=50,
               non_single_game_categories={"fed"}, refresh_notice=None)
    base.update(ctx)
    return _env().get_template("partials/pm_prospects_rows.html").render(**base)


def _wl_render_rows():
    rows = _wl_rows()
    for r in rows:
        r["category"] = "mlb"
        r["score"] = app._score_cell(None, 1000)
    return rows


def _pr_render_rows():
    rows = _pr_rows()
    for r in rows:
        r["category"] = "mlb"
        r["win_rate"] = 0.6
        r["loss_omission"] = app._loss_omission_cell(None, 1000)
        r["last_refresh"] = {"ts": r["last_refresh"]["ts"], "band": "green", "note": "", "age_days": 1.0, "iso": "x"}
        r.update({"n_condition_ids": 3, "two_sided_pct": 0.1, "single_game_pct": 0.9, "avg_win_price": 0.6,
                  "chalk": False, "contested": True, "flags": []})
    return rows


def test_watchlist_headers_are_server_side_links_js_off():
    html = _render_watchlist(watchlist=_wl_render_rows(), category="mlb", wsort="whale", wdir="asc")
    # every sortable column is an <a href> carrying ?wsort= -- JS-off safe (no pm_sort.js, no data-sort-value)
    for col in ("whale", "judge", "open", "closed", "winpct", "roi", "netpnl", "cost"):
        assert ("?wsort=" + col) in html
    assert "data-sort-value" not in html and "pm-sortable-table" not in html


def test_watchlist_link_preserves_prospects_params():
    html = _render_watchlist(watchlist=_wl_render_rows(), category="mlb", wsort="whale", wdir="asc",
                             psort="judge", pdir="desc")
    assert "psort=judge&pdir=desc" in html                        # a Watchlist sort never resets the Prospects sort


def test_prospects_headers_server_side_and_winpct_not_sortable():
    html = _render_prospects(_pr_render_rows(), category="mlb", psort="roi", pdir="desc", wsort="whale", wdir="asc")
    for col in ("judge", "n", "roi", "netpnl", "updated", "sample"):
        assert ("?psort=" + col) in html
    assert "?psort=winpct" not in html                            # win% is NOT a sort link (honesty rule preserved)
    assert "wsort=whale&wdir=asc" in html                         # preserves the Watchlist params
    assert "data-sort-value" not in html and "pm-sortable-table" not in html


def test_prospects_action_button_states():
    # scored row -> View result ($0, no force) + Re-analyze (?force=1) + the age; unscored -> a single Analyze.
    scored = _pr_render_rows()[:1]                                # the PROMOTE row (analyzed)
    html = _render_prospects(scored, category="mlb")
    assert ">View result</button>" in html and ">Re-analyze</button>" in html
    assert "force=1" in html and "scored" in html                 # the force re-run + the analysis age
    unscored = _pr_render_rows()[2:3]                             # the un-analyzed row
    html2 = _render_prospects(unscored, category="mlb")
    # the button text distinguishes state (the ACTION-header tooltip mentions all three words, so match the buttons)
    assert ">Analyze</button>" in html2
    assert ">View result</button>" not in html2 and ">Re-analyze</button>" not in html2


def test_farm_tables_use_no_internal_vocabulary():
    # the enforced vocabulary rule: "pinned"/"candidate" are code words, never screen words.
    html = _render_watchlist(watchlist=_wl_render_rows(), category="mlb") + \
        _render_prospects(_pr_render_rows(), category="mlb")
    low = html.lower()
    assert "pinned" not in low and "candidate" not in low
