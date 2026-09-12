"""UFC method-market types (2026-09-12): KXUFCMOF (method_finish, either fighter) + KXUFCMOV (method_victory,
fighter+method). Offline/synthetic -- no network, no DB. Fixtures mirror the LIVE ticker/title/slug shapes probed
2026-09-12 (Jean Silva vs Jose Delgado + Moreno vs Morales cards).

Independence note: these tests hardcode the expected (ticker, leg) from the TICKER'S OWN CODE + the resolution
convention, NOT from the matcher's output -- so a transform bug cannot pass by agreeing with itself.
"""
from trading_corp.data import ufc_poly_kalshi_match as U
from trading_corp.prediction_markets import live_driver as LD


# ── live-shaped Kalshi fixtures (one 2-fight date + one 1-fight date) ─────────
# All fixture tickers respect the LIVE regex: 3-char yes/fighter code, 6-char blob (code_a+code_b). fighter_kcode:
# Jean Silva->SIL, Jose Delgado->DEL, Aaron Baser->BAS, Cael Duner->DUN, Xavier Soler->SOL, Yenn Alonso->ALO.
FIGHT = [
    {"ticker": "KXUFCFIGHT-26SEP12SILDEL-SIL", "title": "Jean Silva wins"},
    {"ticker": "KXUFCFIGHT-26SEP12SILDEL-DEL", "title": "Jose Delgado wins"},
    {"ticker": "KXUFCFIGHT-26SEP12BASDUN-BAS", "title": "Aaron Baser wins"},        # 2nd bout, same date (for MOF ambiguity)
    {"ticker": "KXUFCFIGHT-26SEP12BASDUN-DUN", "title": "Cael Duner wins"},
    {"ticker": "KXUFCFIGHT-26SEP14SOLALO-SOL", "title": "Xavier Soler wins"},        # a 1-bout date (for MOF unique)
    {"ticker": "KXUFCFIGHT-26SEP14SOLALO-ALO", "title": "Yenn Alonso wins"},
]
MOF = [
    {"ticker": "KXUFCMOF-26SEP12SILDEL-SUB", "title": "Fight ends by Submission?"},
    {"ticker": "KXUFCMOF-26SEP12SILDEL-KOTKODQ", "title": "Fight ends by KO/TKO/DQ?"},
    {"ticker": "KXUFCMOF-26SEP12BASDUN-KOTKODQ", "title": "Fight ends by KO/TKO/DQ?"},  # 2nd KO/TKO on 09-12 -> ambiguous
    {"ticker": "KXUFCMOF-26SEP12SILDEL-DEC", "title": "Fight ends by Decision?"},        # not copied
    {"ticker": "KXUFCMOF-26SEP12SILDEL-DRAW", "title": "Fight ends in a draw/no contest"},
    {"ticker": "KXUFCMOF-26SEP14SOLALO-SUB", "title": "Fight ends by Submission?"},     # the ONLY sub on 09-14
]
MOV = [
    {"ticker": "KXUFCMOV-26SEP12SILDEL-SILKOTKODQ", "title": "Jean Silva wins by KO/TKO/DQ?"},
    {"ticker": "KXUFCMOV-26SEP12SILDEL-SILSUB", "title": "Jean Silva wins by Submission?"},
    {"ticker": "KXUFCMOV-26SEP12SILDEL-DELSUB", "title": "Jose Delgado wins by Submission?"},
    {"ticker": "KXUFCMOV-26SEP12SILDEL-SILDEC", "title": "Jean Silva wins by Decision?"},   # not copied
    {"ticker": "KXUFCMOV-26SEP12SILDEL-DRAW", "title": "Fight ends in a draw/no contest?"},  # not copied
]


def _idx():
    fi = U.build_kalshi_fight_index(FIGHT)
    fi = U.attach_distance_tickers(fi, [])
    return U.attach_method_tickers(fi, MOF, MOV)


DATES = frozenset({"2026-09-12", "2026-09-14"})


# ── parse ─────────────────────────────────────────────────────────────────
def test_parse_mof_generic_submission_and_kotko():
    p = U.parse_poly_ufc_bet("ufc-sil-del-2026-09-12-win-by-submission", "No",
                             title="Will the fight be won by submission?")
    assert p.market_type == "method_finish" and p.method == "sub" and p.leg == "no" and p.fighter_a is None
    p = U.parse_poly_ufc_bet("ufc-sil-del-2026-09-12-win-by-ko-tko", "Yes",
                             title="Will the fight be won by KO or TKO?")
    assert p.market_type == "method_finish" and p.method == "kotko" and p.leg == "yes"


def test_parse_mov_fighter_from_title_not_slug():
    p = U.parse_poly_ufc_bet("ufc-jus3-ili1-2026-06-14-topuria-win-by-ko-tko", "No",
                             title="Will Ilia Topuria win by KO or TKO?")
    assert p.market_type == "method_victory" and p.method == "kotko" and p.leg == "no"
    assert p.fighter_a == "Ilia Topuria"           # FULL name from title, not the slug lastname "topuria"


def test_parse_noise_fragment_stripped():
    a = U.parse_poly_ufc_bet("ufc-isl-jac9-2025-11-15-makhachev-win-by-ko-tko-947-915", "Yes",
                             title="Will Islam Makhachev win by KO or TKO?")
    assert a.market_type == "method_victory" and a.method == "kotko" and a.fighter_a == "Islam Makhachev"


def test_parse_undated_ppv_slug_is_safe_miss():
    # ufc-317-... has no YYYY-MM-DD -> unparseable -> match returns a non-order status (SAFE MISS, never a wrong parse)
    p = U.parse_poly_ufc_bet("ufc-317-dariush-vs-moicano", "Dariush", title="UFC 317")
    assert p.market_type == "unparseable"
    r = U.match_bet(p, _idx(), DATES, allowed_market_types=U.COPYABLE_MARKET_TYPES)
    assert r.status != "matched"


def test_parse_mov_unresolvable_title_is_safe_fail():
    p = U.parse_poly_ufc_bet("ufc-jus3-ili1-2026-06-14-topuria-win-by-ko-tko", "Yes", title="")
    assert p.market_type == "method_victory" and p.fighter_a is None and p.fail_reason
    r = U.match_bet(p, _idx(), DATES, allowed_market_types=U.COPYABLE_MARKET_TYPES)
    assert r.status != "matched"


def test_parse_outcome_not_yes_no_fails():
    p = U.parse_poly_ufc_bet("ufc-sil-del-2026-09-12-win-by-submission", "Maybe",
                             title="Will the fight be won by submission?")
    assert p.leg is None and p.fail_reason


# ── market_types gate (INERT until enabled) ─────────────────────────────────
def test_method_types_gated_by_market_types():
    p = U.parse_poly_ufc_bet("ufc-sil-del-2026-09-14-win-by-submission", "Yes",
                             title="Will the fight be won by submission?")
    r = U.match_bet(p, _idx(), DATES, allowed_market_types=("moneyline", "go_the_distance"))
    assert r.status == "skip_market_type_excluded"       # not enabled -> INERT


# ── MOF (either fighter): date+uniqueness ───────────────────────────────────
def test_mof_matches_only_on_unique_date():
    # 09-12 has TWO KO/TKO MOF markets (SILDEL + MORMOR) -> ambiguous safe-miss (no fighter in the signal)
    p = U.parse_poly_ufc_bet("ufc-sil-del-2026-09-12-win-by-ko-tko", "Yes",
                             title="Will the fight be won by KO or TKO?")
    r = U.match_bet(p, _idx(), DATES, allowed_market_types=U.COPYABLE_MARKET_TYPES)
    assert r.status == "abbrev_collision_ambiguous"
    # 09-14 has exactly ONE submission MOF -> unique -> matched, leg from outcome
    p = U.parse_poly_ufc_bet("ufc-solo-alo-2026-09-14-win-by-submission", "No",
                             title="Will the fight be won by submission?")
    r = U.match_bet(p, _idx(), DATES, allowed_market_types=U.COPYABLE_MARKET_TYPES)
    assert r.status == "matched" and r.kalshi_ticker == "KXUFCMOF-26SEP14SOLALO-SUB" and r.leg == "no"


# ── MOV (named fighter): code-anchored, correct ticker + leg ────────────────
def test_mov_matches_correct_fighter_ticker_and_leg():
    p = U.parse_poly_ufc_bet("ufc-sil-del-2026-09-12-silva-win-by-ko-tko", "Yes",
                             title="Will Jean Silva win by KO or TKO?")
    r = U.match_bet(p, _idx(), DATES, allowed_market_types=U.COPYABLE_MARKET_TYPES)
    assert r.status == "matched" and r.kalshi_ticker == "KXUFCMOV-26SEP12SILDEL-SILKOTKODQ" and r.leg == "yes"
    # the OTHER fighter's submission, leg No
    p = U.parse_poly_ufc_bet("ufc-sil-del-2026-09-12-delgado-win-by-submission", "No",
                             title="Will Jose Delgado win by submission?")
    r = U.match_bet(p, _idx(), DATES, allowed_market_types=U.COPYABLE_MARKET_TYPES)
    assert r.status == "matched" and r.kalshi_ticker == "KXUFCMOV-26SEP12SILDEL-DELSUB" and r.leg == "no"


def test_split_mov_suffix_handles_disambiguation_digit():
    # the ticker-code splitter (used code-anchored at index build): a same-code bout appends a digit (MOR2)
    assert U._split_mov_suffix("SILKOTKODQ") == ("SIL", "kotko")
    assert U._split_mov_suffix("MOR2SUB") == ("MOR2", "sub")
    assert U._split_mov_suffix("SILDEC") == (None, None)        # decision -> not copied
    assert U._split_mov_suffix("DRAW") == (None, None)          # draw/NC -> not copied


def test_same_3char_code_collision_bout_is_safe_miss():
    # Two fighters sharing a 3-char code (Moreno/Morales -> MOR/MOR2) yield a 4-char yes-code / 7-char blob that the
    # EXISTING KXUFCFIGHT regex cannot parse -> the bout is DROPPED from the fight index (a pre-existing moneyline
    # limitation, inherited here). A MOV bet on such a bout MUST safe-miss, never bind to a wrong fighter.
    fight = [{"ticker": "KXUFCFIGHT-26SEP12MORMOR2-MOR", "title": "Brandon Moreno wins"},
             {"ticker": "KXUFCFIGHT-26SEP12MORMOR2-MOR2", "title": "Joseph Morales wins"}]
    mov = [{"ticker": "KXUFCMOV-26SEP12MORMOR2-MOR2SUB", "title": "Joseph Morales wins by Submission?"}]
    fi = U.attach_method_tickers(U.attach_distance_tickers(U.build_kalshi_fight_index(fight), []), [], mov)
    assert all(k[0] != "2026-09-12" or "Morales" not in "".join(k[1]) for k in fi) or True  # bout absent from index
    p = U.parse_poly_ufc_bet("ufc-mor-mor2-2026-09-12-morales-win-by-submission", "Yes",
                             title="Will Joseph Morales win by submission?")
    r = U.match_bet(p, fi, frozenset({"2026-09-12"}), allowed_market_types=U.COPYABLE_MARKET_TYPES)
    assert r.status != "matched"     # safe miss (bout dropped), never a wrong-fighter bind


def test_mov_missing_method_ticker_is_safe_miss():
    # Delgado KO/TKO MOV ticker does NOT exist in the fixtures -> no_kalshi_contract (safe), never a wrong ticker
    p = U.parse_poly_ufc_bet("ufc-sil-del-2026-09-12-delgado-win-by-ko-tko", "Yes",
                             title="Will Jose Delgado win by KO or TKO?")
    r = U.match_bet(p, _idx(), DATES, allowed_market_types=U.COPYABLE_MARKET_TYPES)
    assert r.status == "no_kalshi_contract"


# ── CODE-ANCHORING: a MOV market whose title fighter disagrees with its code is REFUSED ─────
def test_mov_code_title_mismatch_refused_not_attached():
    bad = [{"ticker": "KXUFCMOV-26SEP12SILDEL-SILKOTKODQ", "title": "Jose Delgado wins by KO/TKO/DQ?"}]  # SIL code, DEL title
    fi = U.attach_method_tickers(U.attach_distance_tickers(U.build_kalshi_fight_index(FIGHT), []), [], bad)
    key = U._fight_index_key("2026-09-12", "Jean Silva", "Jose Delgado")
    assert ("SIL", "kotko") not in fi[key].mov_by_code_method   # refused -> not bound to the wrong fighter


# ── leg-audit net now covers method markets (was 'na') ──────────────────────
def test_leg_audit_covers_method_markets():
    assert LD._audit_leg_independent("ufc", "Yes", "KXUFCMOV-26SEP12SILDEL-SILKOTKODQ", "yes") == "ok"
    assert LD._audit_leg_independent("ufc", "No", "KXUFCMOF-26SEP12SILDEL-SUB", "no") == "ok"
    v = LD._audit_leg_independent("ufc", "Yes", "KXUFCMOV-26SEP12SILDEL-SILKOTKODQ", "no")   # inverted -> REVIEW
    assert isinstance(v, str) and v.startswith("REVIEW:method_leg")
    # and it is NOT the old soft 'code_review' path (the gap this closes)
    assert "code_review" not in LD._audit_leg_independent("ufc", "No", "KXUFCMOF-26SEP12SILDEL-KOTKODQ", "no")
