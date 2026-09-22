"""Phase B/C binding HARDENING (2026-09-22), after the adversarial skeptics found a cross-team wrong-bind and a
whole-number push hazard in the shared prop core. Proves:
  1. Case A (CRITICAL): whale player ABSENT, only a same-initial/same-surname OPPONENT listed at the exact strike ->
     with the title full-name gate this is EXCLUDED (player_not_found), not a wrong-team match.
  2. Case A positive: the whale's own titled player still matches.
  3. Case B: both same-name players present at asymmetric strikes -> distinct-player-code ambiguity refuses (even
     untitled).
  4. whole-number Poly line -> refused (strike/leg None), mirroring team_total's push-hazard refusal.
  5. first-name compatibility (Cam/Cameron OK; Kaleb/Kevin distinct).
"""
from trading_corp.data import player_props_match as P

STEM, TA, TB = "26SEP24ATLGB", "ATL", "GB"


def _m(idx, suffix, outcome):
    return P.match_prop(P.parse_prop_suffix("nfl", suffix, outcome), STEM, TA, TB, idx)


# ── 1. Case A: whale=Kaleb (GB) absent; only ATL's *Kevin* Johnson listed at 50. Titled -> EXCLUDED, not a wrong bind
def test_caseA_titled_excludes_wrong_team_player():
    tk_a = "KXNFLRSHYDS-26SEP24ATLGB-ATLKJOHNSON1-50"
    idx = P.build_prop_index([tk_a], {tk_a: "Kevin Johnson: 50+ rushing yards"})
    tk, leg, reason = _m(idx, "-ryd-kaleb-johnson-49pt5", "Over")   # wants 50
    assert tk is None and reason == "player_not_found"             # Kevin != Kaleb -> excluded (was: matched ATL)


# ── 2. Case A positive: the whale's own player, titled, matches
def test_caseA_titled_own_player_matches():
    tk_g = "KXNFLRSHYDS-26SEP24ATLGB-GBKJOHNSON26-50"
    idx = P.build_prop_index([tk_g], {tk_g: "Kaleb Johnson: 50+ rushing yards"})
    tk, leg, reason = _m(idx, "-ryd-kaleb-johnson-49pt5", "Over")
    assert tk == tk_g and leg == "yes" and reason is None


# ── 2b. even when BOTH are titled, only the name-matching one is considered (and it matches at its own strike)
def test_caseA_both_titled_binds_only_the_named_one():
    tk_g = "KXNFLRSHYDS-26SEP24ATLGB-GBKJOHNSON26-50"
    tk_a = "KXNFLRSHYDS-26SEP24ATLGB-ATLKJOHNSON1-50"
    idx = P.build_prop_index([tk_g, tk_a],
                             {tk_g: "Kaleb Johnson: 50+", tk_a: "Kevin Johnson: 50+"})
    tk, leg, reason = _m(idx, "-ryd-kaleb-johnson-49pt5", "Over")
    assert tk == tk_g and reason is None                            # Kevin excluded by name -> not ambiguous, binds Kaleb


# ── 3. Case B (titled): both K.Johnson present, ASYMMETRIC strikes -> the opponent (Kevin@50) is EXCLUDED by name, so
#      the whale's Kaleb (only @90) has no rung at 50 -> no_kalshi_strike, NEVER the wrong-team ATL@50 (the old leak).
def test_caseB_titled_excludes_wrong_team_at_wanted_strike():
    tk_g = "KXNFLRSHYDS-26SEP24ATLGB-GBKJOHNSON26-90"
    tk_a = "KXNFLRSHYDS-26SEP24ATLGB-ATLKJOHNSON1-50"
    idx = P.build_prop_index([tk_g, tk_a], {tk_g: "Kaleb Johnson: 90+", tk_a: "Kevin Johnson: 50+"})
    tk, leg, reason = _m(idx, "-ryd-kaleb-johnson-49pt5", "Over")            # wants 50; Kaleb only has 90
    assert tk is None and reason.startswith("no_kalshi_strike")              # was: matched ATL (the leak)


# ── 3b. UNTITLED ladder cannot bind on the initial alone (closes the untitled fallback that re-opened Case A)
def test_caseB_untitled_ladder_excluded():
    idx = P.build_prop_index(["KXNFLRSHYDS-26SEP24ATLGB-ATLKJOHNSON1-50"])   # no titles -> unbindable ladder
    tk, leg, reason = _m(idx, "-ryd-kaleb-johnson-49pt5", "Over")
    assert tk is None and reason == "player_not_found"


# ── 4. whole-number Poly line -> refused (push hazard), like team_total
def test_whole_number_line_refused():
    r = P.parse_prop_suffix("nfl", "-ryd-cam-skattebo-49pt0", "Over")   # whole line 49.0
    assert r["strike"] is None and r["leg"] is None
    r2 = P.parse_prop_suffix("mlb", "-k-gerrit-cole-6pt0", "Over")
    assert r2["strike"] is None and r2["leg"] is None
    # and it becomes a safe miss end-to-end
    tk_g = "KXNFLRSHYDS-26SEP24ATLGB-GBKJOHNSON26-49"
    idx = P.build_prop_index([tk_g], {tk_g: "Kaleb Johnson: 49+"})
    tk, leg, reason = _m(idx, "-ryd-kaleb-johnson-49pt0", "Over")
    assert tk is None                                                    # refused, never binds a 49 rung on a whole line


# ── 4b. half line still works (regression guard)
def test_half_line_still_maps_to_n_plus_1():
    r = P.parse_prop_suffix("nfl", "-ryd-cam-skattebo-49pt5", "Over")
    assert r["strike"] == 50 and r["leg"] == "yes"


# ── 5. exact-equality gate: a nickname-FORM mismatch is a safe MISS (not a wrong bind); distinct first -> excluded
def test_exact_first_name_required():
    tk = "KXNFLRSHYDS-26SEP24ATLGB-GBCSKATTEBO44-50"
    # whale 'cam-skattebo' vs a Kalshi title 'Cameron Skattebo' -> NOT exact -> safe MISS (coverage trade for safety)
    idx = P.build_prop_index([tk], {tk: "Cameron Skattebo: 50+"})
    tk1, _l, r1 = _m(idx, "-ryd-cam-skattebo-49pt5", "Over")
    assert tk1 is None and r1 == "player_not_found"
    # exact form matches
    idx2 = P.build_prop_index([tk], {tk: "Cam Skattebo: 50+"})
    tk2, _l2, r2 = _m(idx2, "-ryd-cam-skattebo-49pt5", "Over")
    assert tk2 == tk and r2 is None


def test_poly_full_key():
    assert P.poly_full_key("kaleb-johnson") == ("kaleb", "johnson")
    assert P.poly_full_key("marvin-harrison-jr") == ("marvin", "harrison")
    assert P.poly_full_key("jaxon-smith-njigba") == ("jaxon", "smithnjigba")


def test_title_name_key():
    assert P._title_name_key("Josh Allen: 90+ rushing yards") == ("josh", "allen")
    assert P._title_name_key("Carlos Rodón: 16+ Outs Recorded?") == ("carlos", "rodon")
    assert P._title_name_key("Marvin Harrison Jr.: 50+") == ("marvin", "harrison")
    assert P._title_name_key("PIT Steelers D/ST: 1st Touchdown") is not None  # not a ladder title; parser still returns a tuple but build_prop_index only calls it for ladder series
    assert P._title_name_key("No Touchdown: 1st Touchdown") == ("no", "touchdown")  # harmless: only applied to ladder series in build_prop_index
