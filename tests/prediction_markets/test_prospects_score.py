"""Prospects DISPLAY of the stored promotion-judge score (pm_whale_score, migration 023): the _score_cell builder +
the score_cell / score_badge macros. ★ THE ACCEPTANCE is 0x684baa57c3 -- an INSUFFICIENT_DATA whale with an 88.5%
headline ROI must render FLAGGED and sort BELOW a PROMOTE, never as a healthy top prospect. Imports app (import-time
only -- no request) and renders the macros with a bare Jinja env, so this stays OFF the pre-existing TestClient
UI-render failure surface."""
import os

from trading_corp.prediction_markets import scoring
from trading_corp.prediction_markets.web import app


_PROMOTE = {"tier": "PROMOTE", "sort_roi": 0.42, "honest_roi": 0.40, "grounded": 1, "omission_pct": 0.0,
            "coverage_pct": 0.96, "omission_floor": 0, "dominance_net": 0.05, "two_sided_pct": 0.03,
            "chalk": 0, "copy_fills": 12, "computed_ts": 900}
# the REAL 0x684baa57c3/mlb shape from the live proof: INSUFFICIENT_DATA, 88.5% headline, 80% omission, 36% coverage
_MIRAGE = {"tier": "INSUFFICIENT_DATA", "sort_roi": 0.885, "honest_roi": 0.472, "grounded": 1, "omission_pct": 0.80,
           "coverage_pct": 0.363, "omission_floor": 0, "dominance_net": 0.04, "two_sided_pct": 0.09,
           "chalk": 0, "copy_fills": 222, "computed_ts": 900}
# item 1 (2026-09-15): an ANALYZED-but-UNGROUNDED score (grounding failed / no in-window activity) -> omission is
# UNKNOWN, never 0. The compact badge must SAY so (not a silent 0 that reads as 'no omission').
_UNGROUNDED = {"tier": "WATCH", "sort_roi": 0.30, "honest_roi": None, "grounded": 0, "omission_pct": None,
               "coverage_pct": None, "omission_floor": 0, "dominance_net": 0.10, "two_sided_pct": 0.05,
               "chalk": 0, "copy_fills": 5, "computed_ts": 900}


# ── _score_cell builder ──
def test_score_cell_unanalyzed_is_not_a_zero():
    c = app._score_cell(None, 1000)
    assert c["analyzed"] is False
    assert c["sort_value"] == app._SCORE_UNANALYZED_SORT      # sorts to the bottom; the cell itself reads 'not analyzed'


def test_score_cell_promote_clean_not_flagged():
    c = app._score_cell(_PROMOTE, 1000)
    assert c["analyzed"] and c["tier"] == "PROMOTE" and c["flagged"] is False
    assert c["sort_value"] == scoring.score_sort_key("PROMOTE", 0.42)


def test_score_cell_mirage_flagged_and_sorts_below_promote():
    m = app._score_cell(_MIRAGE, 1000)
    assert m["tier"] == "INSUFFICIENT_DATA" and m["flagged"] is True      # 80% omission + 36% coverage -> flagged
    p = app._score_cell(_PROMOTE, 1000)
    assert m["sort_value"] < p["sort_value"]                             # ★ tier caps the number: 88.5% INSUF < 42% PROMOTE
    assert app._score_cell(None, 1000)["sort_value"] < m["sort_value"]   # un-analyzed is the very bottom


def test_dom_sort_value_ascending_puts_best_first():
    # ★ the shared client sorter (pm_sort.js) sorts ASCENDING on the first click; the JUDGE <td> emits -sort_value,
    # so ascending = best-first. Verify the DOM values order PROMOTE < mirage(INSUF) < un-analyzed (so an ascending
    # click surfaces PROMOTE at the top, the 88.5% mirage in the INSUF band, un-analyzed at the very bottom).
    dom = lambda sc: -app._score_cell(sc, 1000)["sort_value"]
    assert dom(_PROMOTE) < dom(_MIRAGE) < dom(None)


# ── macro rendering (bare Jinja env, no TestClient) ──
def _render(macro, call):
    from jinja2 import Environment, FileSystemLoader
    tdir = os.path.join(os.path.dirname(app.__file__), "templates")
    env = Environment(loader=FileSystemLoader(tdir))
    tmpl = "{% from 'pm_macros.html' import " + macro + " %}" + call
    return env.from_string(tmpl).render(
        sc_p=app._score_cell(_PROMOTE, 1000), sc_m=app._score_cell(_MIRAGE, 1000), sc_u=app._score_cell(None, 1000),
        sc_ug=app._score_cell(_UNGROUNDED, 1000))


def _render_prospects_partial(rows):
    """Render the pm_prospects_rows.html partial with a minimal context (bare Jinja, no TestClient -> off the
    pre-existing UI-render failure surface). Jinja attribute access works on dicts, so rows are plain dicts."""
    from jinja2 import Environment, FileSystemLoader
    tdir = os.path.join(os.path.dirname(app.__file__), "templates")
    env = Environment(loader=FileSystemLoader(tdir))
    return env.get_template("partials/pm_prospects_rows.html").render(
        prospects=rows, loss_omission_caveat="SCREEN-ONLY caveat", thin_sample_floor=50,
        non_single_game_categories={"fed"}, refresh_notice=None)


def _prospect_row(score, loss_omission):
    return {"user_name": "Whale", "wallet": "0xabc", "category": "mlb", "score": score, "loss_omission": loss_omission,
            "n_resolved": 40, "win_rate": 0.6, "roi": 0.2, "net_realized_pnl": 100.0, "n_condition_ids": 3,
            "last_refresh": {"ts": 0, "band": "red", "note": "never", "age_days": None, "iso": None},
            "thin_sample": False, "two_sided_pct": 0.10, "single_game_pct": 0.90, "avg_win_price": 0.60,
            "chalk": False, "contested": True, "flags": []}


def test_macro_mirage_renders_flagged_not_healthy():
    html = _render("score_cell", "{{ score_cell(sc_m, '0x684', 'mlb') }}")
    assert "INSUF DATA" in html and "pm-tier-insufficient-data" in html   # the caveat tier, NOT a green PROMOTE
    assert "+88.5%" in html and "pm-score-flagged" in html and "<sup>*</sup>" in html   # the number FLAGGED on the row
    assert "honest" in html and "+47%" in html and "@36%cov" in html      # the honest number + coverage travel with it


def test_macro_unanalyzed_reads_not_analyzed_with_action_not_zero():
    html = _render("score_cell", "{{ score_cell(sc_u, '0xnew', 'mlb') }}")
    assert "pm-score-unanalyzed" in html and "Analyze" in html            # 'not analyzed' IS the [Analyze] control
    assert "/farm/analyze/0xnew/mlb" in html                              # the action is wired
    assert "pm-score-num" not in html                                     # NO number rendered -> never a 0/low score


def test_macro_promote_clean_no_flag():
    html = _render("score_cell", "{{ score_cell(sc_p, '0xp', 'mlb') }}")
    assert "PROMOTE" in html and "pm-tier-promote" in html and "+42.0%" in html
    assert "pm-score-flagged" not in html and "<sup>*</sup>" not in html  # grounded clean -> not flagged


def test_score_badge_compact_mirage_and_unanalyzed():
    html_m = _render("score_badge", "{{ score_badge(sc_m) }}")
    assert "INSUF DATA" in html_m and "pm-score-flagged" in html_m and "<sup>*</sup>" in html_m
    html_u = _render("score_badge", "{{ score_badge(sc_u) }}")
    assert "not" in html_u and "analyzed" in html_u and "pm-score-num" not in html_u


def test_score_badge_oob_id_for_watchlist_live_update():
    # ★ THE WATCHLIST/ROSTER FIX: with wallet+category the badge carries id="pm-scoreb-{w}-{c}" + hx-swap-oob so the
    # Analyze result can update it IN PLACE. The bug was score_badge had NO id, so a Watchlist row stayed 'not
    # analyzed' after clicking Analyze (only a full reload populated it).
    html = _render("score_badge", "{{ score_badge(sc_m, '0x684baa57c3', oob=True) }}")
    # id is wallet-ONLY (no category slug): unique per single-category page AND keeps the F-3 casing guard honest.
    assert 'id="pm-scoreb-0x684baa57c3"' in html and 'hx-swap-oob="true"' in html
    assert "INSUF DATA" in html and "pm-score-flagged" in html          # still renders the flagged mirage badge
    assert 'id="pm-score-' not in html                                  # distinct from score_cell -> no collision
    # back-compat: no wallet -> no id wrapper (callers that don't need OOB)
    assert 'id="pm-scoreb' not in _render("score_badge", "{{ score_badge(sc_m) }}")


# ── ITEM 1 (2026-09-15): the compact badge (Watchlist + /live roster) now surfaces loss-omission ──
def test_score_badge_surfaces_omission_grounded_figure():
    # grounded + 80% omission -> the FIGURE rides on the compact badge, not just the flagged asterisk.
    html = _render("score_badge", "{{ score_badge(sc_m) }}")
    assert "pm-omit-bad" in html and "80%" in html and "loss" in html          # -80% loss shown on Watchlist/roster
    assert "36% coverage" in html                                              # the coverage bound travels with it


def test_score_badge_ungrounded_reads_unknown_never_zero():
    # ★ the load-bearing item-1 rule: ANALYZED-but-UNGROUNDED must read UNKNOWN, NEVER 0 (a 0 = 'nobody looked').
    html = _render("score_badge", "{{ score_badge(sc_ug) }}")
    assert "pm-omit-unknown" in html and "UNKNOWN" in html                     # explicit UNKNOWN
    assert "pm-omit-bad" not in html and "pm-omit-ok" not in html              # NO figure, NO '0%' when ungrounded
    assert "WATCH" in html                                                     # still shows the tier


def test_score_badge_grounded_clean_reads_verified_zero():
    html = _render("score_badge", "{{ score_badge(sc_p) }}")                   # grounded, 0 omission
    assert "pm-omit-ok" in html                                                # 'omit 0%' verified (grounded clean)


def test_score_badge_unanalyzed_has_no_omit_chip():
    html = _render("score_badge", "{{ score_badge(sc_u) }}")                   # never analyzed
    assert "pm-omit" not in html and "not" in html and "analyzed" in html      # reads 'not analyzed' (implicit unknown)


# ── ITEM 6 (2026-09-15): the persistent Analyze button on the Prospects row ──
def test_prospects_unanalyzed_row_has_action_analyze_button():
    html = _render_prospects_partial([_prospect_row(app._score_cell(None, 1000), app._loss_omission_cell(None, 1000))])
    # the ACTION cell button (pm-analyze-btn, like Watchlist) AND the score_cell un-analyzed control -> 2 hx-posts
    assert "pm-analyze-btn" in html
    assert html.count("/farm/analyze/0xabc/mlb") >= 2
    # item 1 on Prospects: the omission cell rides beside win% and reads UNKNOWN (never 0) when un-analyzed
    assert "omission" in html.lower() and "unknown" in html.lower()


def test_prospects_analyzed_row_still_has_persistent_analyze_button():
    # ★ THE item-6 GAP: once analyzed, score_cell shows the TIER (no [Analyze] there) -- but the ACTION-cell button
    # must PERSIST so an already-graded whale can be re-analyzed (identical to Watchlist). Was missing on Prospects.
    html = _render_prospects_partial([_prospect_row(app._score_cell(_PROMOTE, 1000), app._loss_omission_cell(None, 1000))])
    assert "pm-analyze-btn" in html and "/farm/analyze/0xabc/mlb" in html      # the persistent button survives
    assert "PROMOTE" in html                                                   # the JUDGE column stays (tier shown)


# ── ITEM 3 money-aware tile visibility (_tile_visible; adversarial-review fix 2026-09-15) ──
def test_tile_visible_hides_inert_matcherless_but_never_money():
    # matcherless + NO footprint -> HIDDEN (the kalshi_jack/soccer+tennis mis-attach orphans)
    assert app._tile_visible("soccer", False) is False
    assert app._tile_visible("tennis", False) is False
    assert app._tile_visible("golf", False) is False
    # ★ matcherless + a FOOTPRINT (whales / orders / open position) -> VISIBLE: never hide money at risk, and the
    # account aggregate keeps reconciling (a hidden sub is footprint-free -> contributes 0 to the total).
    assert app._tile_visible("soccer", True) is True
    # tradable is ALWAYS visible, footprint or not (a real un-attached sub still shows so Jack notices it)
    assert app._tile_visible("mlb", False) is True
    assert app._tile_visible("epl", False) is True
