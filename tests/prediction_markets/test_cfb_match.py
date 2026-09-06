"""cfb (college football) structural matcher tests. Pure, no network.

★ THE ACCEPTANCE TEST for cfb is the COLLISION PROOF (Jack 2026-09-06): with ~130 FBS programs and 272
Poly codes, a wrong pick would come from team naming. Every named collision pair must resolve to the RIGHT
school or a SAFE MISS -- never to the other one. cfb is the only thing in rung 1 that could place a wrong
order, so these tests are the gate.
"""
from trading_corp.data import sports_structural_match as ssm
from trading_corp.data.cfb_teams import CFB_TEAMS

CFB = ssm.LEAGUES["cfb"]


def test_named_collision_pairs_map_to_distinct_schools():
    """Miami FL/OH, the four Mississippis, the three OSU-States, Michigan/Missouri St, Kansas/Kentucky,
    Washington/Colorado +/- State, San Diego/San Jose/South Dakota -- each code resolves to its OWN school
    (or is absent = safe miss), and no two of a pair share a canonical."""
    def canon(code): return CFB_TEAMS.get(code)   # None = safe miss (dropped/unmapped)
    pairs = [
        ("MIA", "MIAOH"), ("MIAMI", "MOH"),                       # Miami (FL) vs Miami (OH)
        ("MISS", "MSST"), ("MISS", "MVSU"), ("MSST", "MVSU"), ("MISS", "USM"),   # Ole Miss / Miss St / Valley / Southern
        ("OSU", "OKST"), ("OSU", "ORST"), ("OKST", "ORST"),        # Ohio / Oklahoma / Oregon State
        ("MSU", "MSST"), ("MSU", "MSRST"),                         # Michigan St vs Mississippi St vs Missouri St
        ("KSU", "UK"), ("KSU", "KU"),                              # Kansas St vs Kentucky vs Kansas
        ("WASH", "WSU"), ("COL", "CSU"),                           # school vs its State
        ("SDSU", "SDKST"), ("SJSU", "SDKST"), ("SDSU", "SJSU"),    # San Diego / San Jose / South Dakota
    ]
    for a, b in pairs:
        ca, cb = canon(a), canon(b)
        # both must exist AND differ; a None (safe miss) is acceptable but they must never be EQUAL.
        assert ca != cb, ("COLLISION: %s and %s both -> %r" % (a, b, ca))
    # spot the exact expected canonicals
    assert CFB_TEAMS["MIA"] == "Miami" and CFB_TEAMS["MIAOH"] == "Miami (OH)"
    assert CFB_TEAMS["MISS"] == "Ole Miss" and CFB_TEAMS["MSST"] == "Mississippi State"
    assert CFB_TEAMS["OSU"] == "Ohio State" and CFB_TEAMS["OKST"] == "Oklahoma State" and CFB_TEAMS["ORST"] == "Oregon State"
    assert CFB_TEAMS["KSU"] == "Kansas State" and CFB_TEAMS["UK"] == "Kentucky"


def test_sdst_collision_dropped_is_a_safe_miss():
    # the one genuine cross-venue collision: `sdst` (Poly=San Diego, Kalshi=South Dakota) is DROPPED.
    assert "SDST" not in CFB_TEAMS
    tk = ["KXNCAAFGAME-26SEP12SDSTUNLV-SDST", "KXNCAAFGAME-26SEP12SDSTUNLV-UNLV"]
    idx = ssm.build_game_index(tk, CFB)
    # a Poly bet spelled with the collided `sdst` code -> unrecognized -> fail (never a wrong San/South pick)
    r = ssm.match_bet(ssm.parse_poly_bet("cfb-sdst-unlv-2026-09-12", "San Diego State", CFB),
                      idx, frozenset({"2026-09-12"}), CFB)
    assert r.status == "fail", r
    # San Diego State stays reachable via `sdsu`
    tk2 = ["KXNCAAFGAME-26SEP12SDSUUNLV-SDSU", "KXNCAAFGAME-26SEP12SDSUUNLV-UNLV"]
    idx2 = ssm.build_game_index(tk2, CFB)
    r2 = ssm.match_bet(ssm.parse_poly_bet("cfb-sdsu-unlv-2026-09-12", "San Diego State", CFB),
                       idx2, frozenset({"2026-09-12"}), CFB)
    assert r2.kalshi_ticker == "KXNCAAFGAME-26SEP12SDSUUNLV-SDSU", r2


def test_ambiguous_kalshi_code_cannot_wrong_match():
    """KSU is reused on Kalshi (Kansas St FBS + Kentucky St FCS). Even if a Kentucky-St game's KSU ticker
    is mislabeled 'Kansas State', the both-team join key stops a wrong pick: a Poly Kansas-St-vs-Baylor bet
    matches ONLY the Baylor game, never the (mislabeled) KSU-vs-Troy game."""
    tk = [
        "KXNCAAFGAME-26OCT10KSUBAY-KSU", "KXNCAAFGAME-26OCT10KSUBAY-BAY",   # real Kansas St vs Baylor
        "KXNCAAFGAME-26OCT10KSUTROY-KSU", "KXNCAAFGAME-26OCT10KSUTROY-TROY", # (mislabeled) KSU vs Troy
    ]
    idx = ssm.build_game_index(tk, CFB)
    r = ssm.match_bet(ssm.parse_poly_bet("cfb-ksu-bay-2026-10-10", "Kansas State", CFB),
                      idx, frozenset({"2026-10-10"}), CFB)
    assert r.status == "matched" and r.kalshi_ticker == "KXNCAAFGAME-26OCT10KSUBAY-KSU", r
    # a Poly bet on Kansas-St-vs-Troy would (correctly) match the Troy game -- but a Kentucky-St whale bet
    # does not exist on Polymarket, so the mislabeled game is only ever an unmatched, harmless row.


def test_cfb_basic_match_real_ticker():
    tk = ["KXNCAAFGAME-26SEP19PURUCLA-UCLA", "KXNCAAFGAME-26SEP19PURUCLA-PUR"]
    idx = ssm.build_game_index(tk, CFB)
    dates = frozenset({"2026-09-19"})
    assert ssm.match_bet(ssm.parse_poly_bet("cfb-pur-ucla-2026-09-19", "UCLA", CFB), idx, dates, CFB).kalshi_ticker == "KXNCAAFGAME-26SEP19PURUCLA-UCLA"
    assert ssm.match_bet(ssm.parse_poly_bet("cfb-pur-ucla-2026-09-19", "Purdue", CFB), idx, dates, CFB).kalshi_ticker == "KXNCAAFGAME-26SEP19PURUCLA-PUR"


def test_miami_fl_never_matches_miami_oh():
    # Kalshi has a Miami (OH) game; a Polymarket Miami (FL) bet must NOT match it (different school key).
    tk = ["KXNCAAFGAME-26SEP12MOHCIN-MOH", "KXNCAAFGAME-26SEP12MOHCIN-CIN"]   # Miami (OH) vs Cincinnati
    idx = ssm.build_game_index(tk, CFB)
    r = ssm.match_bet(ssm.parse_poly_bet("cfb-mia-cin-2026-09-12", "Miami", CFB), idx, frozenset({"2026-09-12"}), CFB)
    # Poly `mia`=Miami (FL); the only Kalshi game that day is Miami (OH) vs Cincy -> the key {Miami, Cincinnati}
    # is not present ({Miami (OH), Cincinnati} is) -> no_kalshi_contract, NOT a wrong match.
    assert r.status == "no_kalshi_contract", r


def test_cfb_registered():
    assert ssm.LEAGUES["cfb"].game_series == "KXNCAAFGAME" and ssm.LEAGUES["cfb"].has_doubleheader is False
    assert len(CFB_TEAMS) > 250
