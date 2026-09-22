"""Phase B/C player-prop matcher core (2026-09-22). Pins against the REAL Kalshi ticker shapes + whale slugs found
by pm_propmap_ro. Exact-strike-only (Poly N.5 -> Kalshi (N+1)+); code-bind (team+initial+lastname, jersey
disambiguates); leg from the outcome; same-name collision + no-rung + player-absent are classified SAFE misses."""
from trading_corp.data import player_props_match as P


# ── parse_prop_suffix: stat token, series, line->strike (N.5 -> N+1), leg from outcome ────────────────────────
def test_nfl_ladder_tokens_and_strikes():
    r = P.parse_prop_suffix("nfl", "-ryd-cam-skattebo-49pt5", "Under")
    assert r["series"] == "KXNFLRSHYDS" and r["strike"] == 50 and r["leg"] == "no" and r["player_slug"] == "cam-skattebo"
    r = P.parse_prop_suffix("nfl", "-recyd-davante-adams-59pt5", "Over")
    assert r["series"] == "KXNFLRECYDS" and r["strike"] == 60 and r["leg"] == "yes"
    r = P.parse_prop_suffix("nfl", "-pyd-joe-burrow-249pt5", "Over")
    assert r["series"] == "KXNFLPASSYDS" and r["strike"] == 250 and r["leg"] == "yes"
    r = P.parse_prop_suffix("nfl", "-rec-dk-metcalf-4pt5", "Over")     # 'rec' NOT 'recyd'
    assert r["series"] == "KXNFLREC" and r["strike"] == 5 and r["leg"] == "yes"


def test_nfl_binary_tokens():
    r = P.parse_prop_suffix("nfl", "-anytime-td-josh-allen", "No")
    assert r["series"] == "KXNFLANYTD" and r["kind"] == "binary" and r["strike"] is None and r["leg"] == "no"
    r = P.parse_prop_suffix("nfl", "-anytime-td-jaxon-smith-njigba", "Yes")
    assert r["series"] == "KXNFLANYTD" and r["leg"] == "yes" and r["player_slug"] == "jaxon-smith-njigba"


def test_mlb_ladder_tokens_and_strikes():
    r = P.parse_prop_suffix("mlb", "-k-mackenzie-gore-6pt5", "Over")
    assert r["series"] == "KXMLBKS" and r["strike"] == 7 and r["leg"] == "yes"
    r = P.parse_prop_suffix("mlb", "-hr-kyle-schwarber-0pt5", "Over")
    assert r["series"] == "KXMLBHR" and r["strike"] == 1 and r["leg"] == "yes"
    r = P.parse_prop_suffix("mlb", "-outs-kyle-harrison-15pt5", "Over")
    assert r["series"] == "KXMLBOUTS" and r["strike"] == 16 and r["leg"] == "yes"


def test_unrecognised_stat_fails_closed():
    assert P.parse_prop_suffix("nfl", "-sacks-micah-parsons-1pt5", "Over") is None
    assert P.parse_prop_suffix("mlb", "-walks-someone-2pt5", "Over") is None
    assert P.parse_prop_suffix("nba", "-pts-someone-20pt5", "Over") is None   # non-prop category


# ── player keys (both sides) ──────────────────────────────────────────────────────────────────────────────────
def test_poly_player_key():
    assert P.poly_player_key("cam-skattebo") == ("c", "skattebo")
    assert P.poly_player_key("jaxon-smith-njigba") == ("j", "smithnjigba")
    assert P.poly_player_key("marvin-harrison-jr") == ("m", "harrison")   # Jr dropped
    assert P.poly_player_key("dk-metcalf") == ("d", "metcalf")


def test_kalshi_player_key():
    assert P.kalshi_player_key("GBKJOHNSON26", "ATL", "GB") == ("k", "johnson")
    assert P.kalshi_player_key("GBCBROOKS30", "ATL", "GB") == ("c", "brooks")
    assert P.kalshi_player_key("GBJLOVE10", "ATL", "GB") == ("j", "love")
    assert P.kalshi_player_key("ATLAHOOPER81", "ATL", "GB") == ("a", "hooper")
    assert P.kalshi_player_key("TBNMARTINEZ28", "TB", "NYY") == ("n", "martinez")
    assert P.kalshi_player_key("TBJMATEO2", "TB", "NYY") == ("j", "mateo")
    assert P.kalshi_player_key("SEAWRONG9", "ATL", "GB") is None            # code not on either team -> None


def test_parse_prop_ticker():
    assert P.parse_prop_ticker("KXNFLRSHYDS-26SEP24ATLGB-GBKJOHNSON26-90") == ("KXNFLRSHYDS", "26SEP24ATLGB", "GBKJOHNSON26", 90)
    assert P.parse_prop_ticker("KXNFLFIRSTTD-26SEP24ATLGB-ATLAHOOPER81") == ("KXNFLFIRSTTD", "26SEP24ATLGB", "ATLAHOOPER81", None)
    assert P.parse_prop_ticker("KXMLBHR-26SEP221305TBNYYG1-TBJMATEO2-2") == ("KXMLBHR", "26SEP221305TBNYYG1", "TBJMATEO2", 2)
    assert P.parse_prop_ticker("KXNFLGAME-26SEP24ATLGB-GB") is None        # not a prop series


# ── end-to-end match against a real-shaped index ──────────────────────────────────────────────────────────────
NFL_TK = [
    "KXNFLRSHYDS-26SEP24ATLGB-GBKJOHNSON26-50", "KXNFLRSHYDS-26SEP24ATLGB-GBKJOHNSON26-90",
    "KXNFLRECYDS-26SEP24ATLGB-GBCBROOKS30-40", "KXNFLPASSYDS-26SEP24ATLGB-GBJLOVE10-350",
    "KXNFLREC-26SEP24ATLGB-GBTKRAFT85-9", "KXNFLFIRSTTD-26SEP24ATLGB-ATLAHOOPER81",
    "KXNFLANYTD-26SEP24ATLGB-GBKJOHNSON26",
]
IDX = P.build_prop_index(NFL_TK)
STEM, TA, TB = "26SEP24ATLGB", "ATL", "GB"


def _m(cat, suffix, outcome):
    return P.match_prop(P.parse_prop_suffix(cat, suffix, outcome), STEM, TA, TB, IDX)


def test_exact_strike_hit():
    tk, leg, reason = _m("nfl", "-ryd-kaleb-johnson-49pt5", "Over")       # 49.5 -> 50; a 50 rung exists
    assert tk == "KXNFLRSHYDS-26SEP24ATLGB-GBKJOHNSON26-50" and leg == "yes" and reason is None


def test_exact_strike_under_flips_leg():
    tk, leg, reason = _m("nfl", "-ryd-kaleb-johnson-49pt5", "Under")
    assert tk == "KXNFLRSHYDS-26SEP24ATLGB-GBKJOHNSON26-50" and leg == "no"


def test_no_kalshi_strike_is_safe_miss_with_rungs():
    tk, leg, reason = _m("nfl", "-ryd-kaleb-johnson-69pt5", "Over")       # 69.5 -> 70; Kalshi has [50,90] only
    assert tk is None and reason.startswith("no_kalshi_strike:want=70") and "50" in reason and "90" in reason


def test_player_not_found_is_safe_miss():
    tk, leg, reason = _m("nfl", "-ryd-some-guy-49pt5", "Over")
    assert tk is None and reason == "player_not_found"


def test_binary_anytime_td_match():
    tk, leg, reason = _m("nfl", "-anytime-td-kaleb-johnson", "Yes")
    assert tk == "KXNFLANYTD-26SEP24ATLGB-GBKJOHNSON26" and leg == "yes" and reason is None


def test_same_name_collision_is_safe_miss():
    idx = P.build_prop_index(["KXNFLRSHYDS-26SEP24ATLGB-GBKJOHNSON26-50",
                              "KXNFLRSHYDS-26SEP24ATLGB-ATLKJOHNSON12-50"])   # two K.Johnson, one per team, same rung
    tk, leg, reason = P.match_prop(P.parse_prop_suffix("nfl", "-ryd-kaleb-johnson-49pt5", "Over"), STEM, TA, TB, idx)
    assert tk is None and reason.startswith("ambiguous_same_name")


def test_mlb_end_to_end():
    tk_list = ["KXMLBKS-26SEP221305TBNYYG1-TBNMARTINEZ28-7", "KXMLBHR-26SEP221305TBNYYG1-TBJMATEO2-1"]
    idx = P.build_prop_index(tk_list)
    tk, leg, r = P.match_prop(P.parse_prop_suffix("mlb", "-k-nick-martinez-6pt5", "Over"), "26SEP221305TBNYYG1", "TB", "NYY", idx)
    assert tk == "KXMLBKS-26SEP221305TBNYYG1-TBNMARTINEZ28-7" and leg == "yes" and r is None
    tk, leg, r = P.match_prop(P.parse_prop_suffix("mlb", "-hr-kyle-schwarber-0pt5", "Over"), "26SEP221305TBNYYG1", "TB", "NYY", idx)
    assert tk is None and r == "player_not_found"                          # Schwarber not in TB@NYY -> safe miss
