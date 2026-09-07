"""Rung 2 (cs2) matcher: EXACT-normalized pair-key, NEVER fuzzy.

The centrepiece is cs2's Cerundolo case -- an academy/women's/junior/ex- roster is a DISTINCT
entity and must NEVER be matched to its parent org. Also: moneyline-only gating (map / handicap /
spread / total are skips, not matches), the +/-1 day window, the uniqueness guard, and the tiny
data-verified alias table (unifies same-team display forms while leaving academy variants distinct).
"""
import pytest
from trading_corp.data import cs2_poly_kalshi_match as CS


def _idx(markets):
    return CS.build_kalshi_match_index(markets)


def _mk(ticker, org):
    return {"ticker": ticker, "title": "%s wins" % org, "yes_sub_title": org}


def _two(date, blob, a_code, a_org, b_code, b_org):
    return [_mk("KXCS2GAME-%s%s-%s" % (date, blob, a_code), a_org),
            _mk("KXCS2GAME-%s%s-%s" % (date, blob, b_code), b_org)]


# ── the copyable happy path ──────────────────────────────────────────────────────────────────
def test_basic_pair_match_moneyline():
    idx = _idx(_two("26SEP05", "1300ENCEVIT", "ENCE", "ENCE", "VIT", "Vitality"))
    p = CS.parse_poly_cs2_bet("cs2-ence-vit-2026-09-05", "ENCE",
                              "Counter-Strike: ENCE vs Vitality (BO3) - IEM")
    assert p.market_type == "moneyline"
    r = CS.match_bet(p, idx, set(idx))
    assert r.status == "matched" and r.leg == "yes"
    assert r.kalshi_ticker == "KXCS2GAME-26SEP051300ENCEVIT-ENCE", r


# ── cs2's Cerundolo case: academy / fe / NXT / ex- are DISTINCT entities ──────────────────────
@pytest.mark.parametrize("parent,variant", [
    ("ENCE", "ENCE Academy"),
    ("FURIA", "FURIA fe"),
    ("MOUZ", "MOUZ NXT"),
    ("G2", "G2 Ares"),
    ("KRU Esports", "ex-KRU Esports"),
    ("BIG", "BIG EQUIPA"),
    ("FlyQuest", "FlyQuest RED"),
    ("HEROIC", "HEROIC Academy"),
])
def test_variant_never_matches_parent(parent, variant):
    # Kalshi lists ONLY the parent; a Poly bet on the VARIANT must safely MISS, never route to the parent.
    idx = _idx(_two("26SEP05", "1300PARVIT", "PAR", parent, "VIT", "Vitality"))
    p = CS.parse_poly_cs2_bet("cs2-x-vit-2026-09-05", variant,
                              "Counter-Strike: %s vs Vitality (BO3) - Event" % variant)
    r = CS.match_bet(p, idx, set(idx))
    assert r.status != "matched", (variant, parent, r)
    assert r.kalshi_ticker is None, r
    # and the reverse: Kalshi lists ONLY the variant; a bet on the PARENT must not route to the variant
    idx2 = _idx(_two("26SEP05", "1300VARVIT", "VAR", variant, "VIT", "Vitality"))
    p2 = CS.parse_poly_cs2_bet("cs2-x-vit-2026-09-05", parent,
                               "Counter-Strike: %s vs Vitality (BO3) - Event" % parent)
    r2 = CS.match_bet(p2, idx2, set(idx2))
    assert r2.status != "matched" and r2.kalshi_ticker is None, (parent, variant, r2)


def test_parent_and_variant_both_listed_each_routes_correctly():
    # the strongest proof: BOTH markets exist the same day -> each bet resolves to its OWN ticker, never crossed
    mkts = (_two("26SEP05", "1300ENCVIT", "ENC", "ENCE", "VIT", "Vitality")
            + _two("26SEP05", "1600ENAVIT", "ENA", "ENCE Academy", "VI2", "Vitality"))
    idx = _idx(mkts)
    pr = CS.match_bet(CS.parse_poly_cs2_bet("cs2-ence-vit-2026-09-05", "ENCE",
                      "Counter-Strike: ENCE vs Vitality (BO3) - E"), idx, set(idx))
    pv = CS.match_bet(CS.parse_poly_cs2_bet("cs2-encea-vit-2026-09-05", "ENCE Academy",
                      "Counter-Strike: ENCE Academy vs Vitality (BO3) - E"), idx, set(idx))
    assert pr.status == "matched" and pr.kalshi_ticker.endswith("-ENC"), pr
    assert pv.status == "matched" and pv.kalshi_ticker.endswith("-ENA"), pv


# ── moneyline-only: map / handicap / spread / total are skips, not matches ────────────────────
def test_map_bet_slug_suffix_skipped():
    p = CS.parse_poly_cs2_bet("cs2-ence-vit-2026-09-05-game2", "ENCE",
                              "Counter-Strike: ENCE vs Vitality - Map 2 Winner")
    assert p.market_type == "map"
    r = CS.match_bet(p, _idx(_two("26SEP05", "1300ENCVIT", "ENC", "ENCE", "VIT", "Vitality")), {"2026-09-05"})
    assert r.status == "skip_map" and r.kalshi_ticker is None


def test_map_title_skipped_even_without_suffix():
    p = CS.parse_poly_cs2_bet("cs2-ence-vit-2026-09-05", "ENCE",
                              "Counter-Strike: ENCE vs Vitality - Map 2 Winner")
    assert p.market_type == "map"


def test_handicap_outcome_is_non_moneyline():
    # a match title but the outcome is a spread/handicap line -> NOT one of the two sides -> non_moneyline
    for oc in ("Legacy (+1.5)", "Map Handicap: VIT (-1.5)", "ENCE (+3.5)"):
        p = CS.parse_poly_cs2_bet("cs2-ence-vit-2026-09-05", oc,
                                  "Counter-Strike: ENCE vs Vitality (BO3) - E")
        assert p.market_type == "non_moneyline", (oc, p)
        r = CS.match_bet(p, _idx(_two("26SEP05", "1300ENCVIT", "ENC", "ENCE", "VIT", "Vitality")), {"2026-09-05"})
        assert r.status == "skip_non_moneyline" and r.kalshi_ticker is None


def test_total_title_is_non_moneyline():
    p = CS.parse_poly_cs2_bet("cs2-ence-vit-2026-09-05", "Over", "Games Total: O/U 2.5")
    assert p.market_type == "non_moneyline"


def test_market_type_excluded_when_not_in_subdivision_types():
    idx = _idx(_two("26SEP05", "1300ENCVIT", "ENC", "ENCE", "VIT", "Vitality"))
    p = CS.parse_poly_cs2_bet("cs2-ence-vit-2026-09-05", "ENCE", "Counter-Strike: ENCE vs Vitality (BO3) - E")
    r = CS.match_bet(p, idx, set(idx), allowed_market_types=())
    assert r.status == "skip_market_type_excluded" and r.kalshi_ticker is None


# ── +/-1 day window + uniqueness ─────────────────────────────────────────────────────────────
def test_plus_minus_one_day_window():
    idx = _idx(_two("26SEP06", "1300ENCVIT", "ENC", "ENCE", "VIT", "Vitality"))   # Kalshi says the 6th
    for d, ok in (("2026-09-05", True), ("2026-09-06", True), ("2026-09-07", True), ("2026-09-08", False)):
        p = CS.parse_poly_cs2_bet("cs2-ence-vit-%s" % d, "ENCE", "Counter-Strike: ENCE vs Vitality (BO3) - E")
        r = CS.match_bet(p, idx, set(idx))
        assert (r.status == "matched") == ok, (d, r)


def test_rematch_within_window_is_ambiguous_safe_miss():
    # the same two orgs play twice within the window -> two distinct 2-side markets -> ambiguous -> no pick
    mkts = (_two("26SEP05", "1300ENCVIT", "ENC", "ENCE", "VIT", "Vitality")
            + _two("26SEP06", "1600ENCVIT", "EN2", "ENCE", "VI2", "Vitality"))
    idx = _idx(mkts)
    p = CS.parse_poly_cs2_bet("cs2-ence-vit-2026-09-05", "ENCE", "Counter-Strike: ENCE vs Vitality (BO3) - E")
    r = CS.match_bet(p, idx, set(idx))
    assert r.status == "pair_collision_ambiguous" and r.kalshi_ticker is None, r


# ── alias table: same-team unification, academy still distinct ────────────────────────────────
def test_alias_unifies_same_team_forms():
    # Poly "Liquid" -> Kalshi "Team Liquid"; Poly "B8" -> Kalshi "B8 Esports"
    idx = _idx(_two("26SEP05", "1300LIQB8", "LIQ", "Team Liquid", "B8", "B8 Esports"))
    p = CS.parse_poly_cs2_bet("cs2-liquid-b8-2026-09-05", "Liquid",
                              "Counter-Strike: Liquid vs B8 (BO3) - E")
    r = CS.match_bet(p, idx, set(idx))
    assert r.status == "matched" and r.kalshi_ticker.endswith("-LIQ"), r


def test_alias_does_not_bleed_into_academy():
    # "B8" aliases to "B8 Esports", but "B8 Academy" is a DIFFERENT string -> stays distinct (no alias leak)
    idx = _idx(_two("26SEP05", "1300B8AVIT", "B8A", "B8 Academy", "VIT", "Vitality"))
    p = CS.parse_poly_cs2_bet("cs2-b8-vit-2026-09-05", "B8",   # bet on the PARENT
                              "Counter-Strike: B8 vs Vitality (BO3) - E")
    r = CS.match_bet(p, idx, set(idx))
    assert r.status != "matched" and r.kalshi_ticker is None, r


# ── structural safety ────────────────────────────────────────────────────────────────────────
def test_non_cs2_slug_skipped():
    p = CS.parse_poly_cs2_bet("nba-lal-bos-2026-09-05", "Lakers", "Lakers vs Celtics")
    assert p.market_type == "non_cs2"
    assert CS.match_bet(p, {}, set()).status == "skip_non_cs2"


def test_index_skips_non_two_side_blobs_and_non_cs2_tickers():
    # a KXCS2GAME (date,blob) with only ONE side is skipped; a non-cs2 ticker can never enter
    mkts = [_mk("KXCS2GAME-26SEP051300SOLO-SOLO", "Solo Org"),
            _mk("KXUFCFIGHT-26SEP05AAABBB-AAA", "Some Fighter")]
    assert _idx(mkts) == {}
