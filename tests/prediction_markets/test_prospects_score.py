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


# ── macro rendering (bare Jinja env, no TestClient) ──
def _render(macro, call):
    from jinja2 import Environment, FileSystemLoader
    tdir = os.path.join(os.path.dirname(app.__file__), "templates")
    env = Environment(loader=FileSystemLoader(tdir))
    tmpl = "{% from 'pm_macros.html' import " + macro + " %}" + call
    return env.from_string(tmpl).render(
        sc_p=app._score_cell(_PROMOTE, 1000), sc_m=app._score_cell(_MIRAGE, 1000), sc_u=app._score_cell(None, 1000))


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
