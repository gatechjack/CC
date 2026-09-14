"""RFI (first-inning-run) matcher + leg-audit tests (2026-09-14).

★ THE POLARITY IS THE WHOLE RISK AND IT IS DIRECT, NOT INVERTED. The Poly slug is tagged `-nrfi` but the live
market TITLE is "Will there be a run scored in the first inning?" (YES = a run) and Kalshi KXMLBRFI YES = "Over 0.5
runs in the 1st" (a run). Same event, same side -> Poly YES -> Kalshi YES. The leg is GATED on the affirmative title
and FAILS CLOSED on any other framing. These tests pin both directions + the fail-closed + the independent audit.
"""
from trading_corp.data import mlb_poly_kalshi_match as M
from trading_corp.prediction_markets import execution
from trading_corp.prediction_markets import live_driver

# a real game shape (from the live probe): SF @ STL, 2026-09-14, stem 26SEP141945SFSTL
GAME_TK = ["KXMLBGAME-26SEP141945SFSTL-SF", "KXMLBGAME-26SEP141945SFSTL-STL"]
RFI_TK = ["KXMLBRFI-26SEP141945SFSTL"]
SLUG = "mlb-sf-stl-2026-09-14-nrfi"
AFF_TITLE = "Will there be a run scored in the first inning?: San Francisco Giants vs. St. Louis Cardinals"
NEG_TITLE = "Will there be no run in the first inning?: San Francisco Giants vs. St. Louis Cardinals"


def _indices():
    ml = M.build_kalshi_game_index(GAME_TK)
    rfi = M.build_kalshi_rfi_index(RFI_TK)
    dates = frozenset(GAME_TK)
    return ml, rfi, dates


# ── parse: DIRECT polarity, both directions ──────────────────────────────────
def test_parse_nrfi_yes_is_direct_yes():
    p = M.parse_poly_mlb_bet(SLUG, "Yes", AFF_TITLE)
    assert p.market_type == "first_inning_run"
    assert p.leg == "yes"          # affirmative title + outcome Yes (a run) -> Kalshi RFI YES
    assert p.fail_reason is None


def test_parse_nrfi_no_is_direct_no():
    p = M.parse_poly_mlb_bet(SLUG, "No", AFF_TITLE)
    assert p.market_type == "first_inning_run"
    assert p.leg == "no"           # outcome No (no run) -> Kalshi RFI NO
    assert p.fail_reason is None


def test_parse_nrfi_yes_run_and_no_run_outcome_text():
    assert M.parse_poly_mlb_bet(SLUG, "Yes Run", AFF_TITLE).leg == "yes"
    assert M.parse_poly_mlb_bet(SLUG, "No Run", AFF_TITLE).leg == "no"


# ── parse: FAIL CLOSED on any non-affirmative framing (the wrong-side guard) ──
def test_parse_nrfi_negative_title_fails_closed():
    p = M.parse_poly_mlb_bet(SLUG, "Yes", NEG_TITLE)
    assert p.market_type == "first_inning_run"
    assert p.leg is None                      # negative-framed title -> leg None -> matcher will skip
    assert p.fail_reason and "not_affirmative" in p.fail_reason


def test_parse_nrfi_empty_title_fails_closed():
    p = M.parse_poly_mlb_bet(SLUG, "Yes", "")
    assert p.leg is None and p.fail_reason      # no title -> cannot confirm framing -> fail closed


def test_parse_nrfi_unknown_outcome_fails_closed():
    p = M.parse_poly_mlb_bet(SLUG, "Maybe", AFF_TITLE)
    assert p.leg is None and "outcome_unresolved" in (p.fail_reason or "")


# ── Kalshi ticker parse + index ──────────────────────────────────────────────
def test_rfi_ticker_parse_and_index():
    assert M.parse_kalshi_rfi_ticker("KXMLBRFI-26SEP141945SFSTL") == "26SEP141945SFSTL"
    assert M.parse_kalshi_rfi_ticker("KXMLBGAME-26SEP141945SFSTL-SF") is None  # not an RFI ticker
    idx = M.build_kalshi_rfi_index(RFI_TK)
    assert idx == {"26SEP141945SFSTL": "KXMLBRFI-26SEP141945SFSTL"}


# ── match: stem-join, both legs, enable gate, inert, miss ─────────────────────
def test_match_rfi_stem_join_yes():
    ml, rfi, dates = _indices()
    p = M.parse_poly_mlb_bet(SLUG, "Yes", AFF_TITLE)
    r = M.match_bet(p, ml, {}, {}, dates, allowed_market_types=("first_inning_run",), rfi_index=rfi)
    assert r.status == "matched"
    assert r.kalshi_ticker == "KXMLBRFI-26SEP141945SFSTL"
    assert r.leg == "yes" and r.market_type == "first_inning_run"


def test_match_rfi_stem_join_no():
    ml, rfi, dates = _indices()
    p = M.parse_poly_mlb_bet(SLUG, "No", AFF_TITLE)
    r = M.match_bet(p, ml, {}, {}, dates, allowed_market_types=("first_inning_run",), rfi_index=rfi)
    assert r.status == "matched" and r.leg == "no"


def test_match_rfi_inert_when_not_enabled():
    ml, rfi, dates = _indices()
    p = M.parse_poly_mlb_bet(SLUG, "Yes", AFF_TITLE)
    # default allowed_market_types excludes first_inning_run for existing subs -> skip_market_type_excluded (INERT)
    r = M.match_bet(p, ml, {}, {}, dates, allowed_market_types=("moneyline", "total", "spread"), rfi_index=rfi)
    assert r.status == "skip_market_type_excluded"


def test_match_rfi_no_kalshi_market_is_safe_skip():
    ml, _, dates = _indices()
    p = M.parse_poly_mlb_bet(SLUG, "Yes", AFF_TITLE)
    r = M.match_bet(p, ml, {}, {}, dates, allowed_market_types=("first_inning_run",), rfi_index={})
    assert r.status == "no_kalshi_contract"      # game found, no RFI ticker -> safe skip, never a wrong fill


def test_match_rfi_failclosed_parse_never_matches():
    ml, rfi, dates = _indices()
    p = M.parse_poly_mlb_bet(SLUG, "Yes", NEG_TITLE)   # leg None
    r = M.match_bet(p, ml, {}, {}, dates, allowed_market_types=("first_inning_run",), rfi_index=rfi)
    assert r.status == "fail"                     # never a matched ticker on a fail-closed parse


def test_first_inning_run_in_copyable_types():
    assert "first_inning_run" in M.COPYABLE_MARKET_TYPES


# ── independent leg-audit (separate transform, keyed off the resolution title) ─
def test_audit_rfi_direct_ok():
    assert live_driver._audit_leg_independent(
        "mlb", "Yes", "KXMLBRFI-26SEP141945SFSTL", "yes", signal_slug=SLUG, signal_title=AFF_TITLE) == "ok"
    assert live_driver._audit_leg_independent(
        "mlb", "No", "KXMLBRFI-26SEP141945SFSTL", "no", signal_slug=SLUG, signal_title=AFF_TITLE) == "ok"


def test_audit_rfi_inversion_is_review():
    v = live_driver._audit_leg_independent(
        "mlb", "Yes", "KXMLBRFI-26SEP141945SFSTL", "no", signal_slug=SLUG, signal_title=AFF_TITLE)
    assert v.startswith("REVIEW")          # chose NO on a "run scored?"-Yes bet -> inversion caught


def test_audit_rfi_negative_framing_inverts_independently():
    # a hypothetical negative-framed market: outcome Yes (=no run) with leg 'no' is CORRECT; leg 'yes' is inversion.
    assert live_driver._audit_leg_independent(
        "mlb", "Yes", "KXMLBRFI-26SEP141945SFSTL", "no", signal_title=NEG_TITLE) == "ok"
    assert live_driver._audit_leg_independent(
        "mlb", "Yes", "KXMLBRFI-26SEP141945SFSTL", "yes", signal_title=NEG_TITLE).startswith("REVIEW")


def test_audit_rfi_unknown_framing_unchecked():
    assert live_driver._audit_leg_independent(
        "mlb", "Yes", "KXMLBRFI-26SEP141945SFSTL", "yes", signal_title="") == "unchecked"


# ── adapter wiring: rfi_index flows through _mlb_match via MarketContext ───────
def test_mlb_adapter_passes_rfi_index():
    ml, rfi, dates = _indices()
    ctx = execution.MarketContext(ml, {}, {}, dates, {}, rfi_index=rfi)
    p = M.parse_poly_mlb_bet(SLUG, "Yes", AFF_TITLE)
    r = execution._mlb_match(p, ctx, ("first_inning_run",))
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBRFI-26SEP141945SFSTL" and r.leg == "yes"


# ── regression: moneyline/total/spread parse + match unchanged ───────────────
def test_regression_moneyline_unchanged():
    ml, rfi, dates = _indices()
    p = M.parse_poly_mlb_bet("mlb-sf-stl-2026-09-14", "San Francisco Giants", "")
    assert p.market_type == "moneyline"
    r = M.match_bet(p, ml, {}, {}, dates, allowed_market_types=("moneyline",), rfi_index=rfi)
    assert r.status == "matched" and r.kalshi_ticker == "KXMLBGAME-26SEP141945SFSTL-SF" and r.leg == "yes"


def test_regression_total_still_parses():
    p = M.parse_poly_mlb_bet("mlb-sf-stl-2026-09-14-total-8pt5", "Over", "")
    assert p.market_type == "total" and p.leg == "yes" and p.line == 8.5


# ── skeptic fix 1: blank market_types must NOT auto-enable the new type (inert guarantee) ─────────────
def test_blank_market_types_does_not_enable_rfi():
    cfg = execution.sub_config_from_row({"account_id": "kalshi_jack", "category": "mlb", "market_types": ""})
    assert "first_inning_run" not in cfg.market_types
    assert cfg.market_types == ("moneyline", "total", "spread")


# ── skeptic fix 2: the whale-EXIT path carries the title so an RFI exit re-parse recovers its leg ─────
def test_exit_path_carries_title_for_rfi():
    cid, oidx = "0xabc", 0
    prior = {(cid, oidx): (5.0, SLUG, "Yes", AFF_TITLE)}          # a held RFI position (size,slug,outcome,title)
    reds = live_driver.detect_position_reductions(prior, [], "0xwhale", 1000)   # rows=[] -> fully sold
    assert len(reds) == 1 and reds[0]["title"] == AFF_TITLE and reds[0]["slug"] == SLUG
    sells = [{"wallet": "0xwhale", "condition_id": cid, "outcome_index": oidx, "ts": 1000, "tx_hash": "0xtx"}]
    exits = execution.detect_exit_signals(sells, reds, window_sec=600)
    assert len(exits) == 1 and exits[0].is_exit and exits[0].title == AFF_TITLE
    # BEFORE the fix the exit CopySignal had title="" -> RFI re-parse fail-closed -> exit never fired. Now it resolves:
    p = M.parse_poly_mlb_bet(exits[0].slug, exits[0].outcome, exits[0].title)
    assert p.market_type == "first_inning_run" and p.leg == "yes"
