"""Rung 3 (soccer) matcher: 3-way (team-win Yes/No + draw->Kalshi TIE), per-league exact club join.
Centerpieces: draw->TIE, the 90-min "Reg Time:" strip, and the named collisions kept distinct
(Ligue 1 PSG/Paris FC, MLS LAFC/Galaxy, UCL Inter Milan/IC Escaldes). Uses the SHIPPED alias tables."""
import pytest
from trading_corp.data import soccer_poly_kalshi_match as SO


def _mk(series, date, blob, code, org):
    return {"ticker": "%s-%s%s-%s" % (series, date, blob, code), "title": "%s wins" % org, "yes_sub_title": org}


def _game(series, date, blob, a_code, a_org, b_code, b_org, tie=True):
    ms = [_mk(series, date, blob, a_code, a_org), _mk(series, date, blob, b_code, b_org)]
    if tie:
        ms.append({"ticker": "%s-%s%s-TIE" % (series, date, blob), "title": "Tie is the result", "yes_sub_title": "Tie"})
    return ms


def _idx(cat, markets):
    return SO.build_game_index(markets, SO.LEAGUES[cat])


def _run(cat, slug, outcome, title, idx):
    cfg = SO.LEAGUES[cat]
    return SO.match_bet(SO.parse_poly_bet(slug, outcome, cfg, title), idx, set(idx), cfg)


# ── win path (Yes -> yes leg, No -> no leg) ──────────────────────────────────────────────────
def test_win_yes_and_no_legs():
    idx = _idx("epl", _game("KXEPLGAME", "26SEP05", "ARSCHE", "ARS", "Arsenal", "CHE", "Chelsea"))
    ry = _run("epl", "epl-ars-che-2026-09-05-ars", "Yes", "Will Arsenal FC win on 2026-09-05?", idx)
    assert ry.status == "matched" and ry.kalshi_ticker.endswith("-ARS") and ry.leg == "yes", ry
    rn = _run("epl", "epl-ars-che-2026-09-05-ars", "No", "Will Arsenal FC win on 2026-09-05?", idx)
    assert rn.status == "matched" and rn.kalshi_ticker.endswith("-ARS") and rn.leg == "no", rn


# ── draw -> Kalshi TIE ───────────────────────────────────────────────────────────────────────
def test_draw_maps_to_tie():
    idx = _idx("epl", _game("KXEPLGAME", "26SEP05", "ARSCHE", "ARS", "Arsenal", "CHE", "Chelsea"))
    r = _run("epl", "epl-ars-che-2026-09-05-draw", "Yes", "Will Arsenal vs. Chelsea end in a draw?", idx)
    assert r.status == "matched" and r.kalshi_ticker.endswith("-TIE") and r.leg == "yes", r


def test_draw_missing_tie_market_is_safe_miss():
    idx = _idx("epl", _game("KXEPLGAME", "26SEP05", "ARSCHE", "ARS", "Arsenal", "CHE", "Chelsea", tie=False))
    r = _run("epl", "epl-ars-che-2026-09-05-draw", "Yes", "Will Arsenal vs. Chelsea end in a draw?", idx)
    assert r.status != "matched" and r.kalshi_ticker is None, r


# ── alias table (formal/local -> Kalshi short) ───────────────────────────────────────────────
def test_alias_formal_names():
    idx = _idx("bun", _game("KXBUNDESLIGAGAME", "26SEP05", "FCBDOR", "FCB", "Bayern Munich", "DOR", "Dortmund"))
    r = _run("bun", "bun-fcb-dor-2026-09-05-fcb", "Yes", "Will FC Bayern München win on 2026-09-05?", idx)
    assert r.status == "matched" and r.kalshi_ticker.endswith("-FCB"), r


def test_alias_inter_and_athletic():
    idx = _idx("sea", _game("KXSERIEAGAME", "26SEP05", "INTMIL", "INT", "Inter", "MIL", "Milan"))
    r = _run("sea", "sea-int-mil-2026-09-05-int", "Yes", "Will FC Internazionale Milano win on 2026-09-05?", idx)
    assert r.status == "matched" and r.kalshi_ticker.endswith("-INT"), r


# ── the named collisions: MUST stay distinct ─────────────────────────────────────────────────
def test_ligue1_psg_vs_paris_fc_distinct():
    # two separate games; PSG must route to PSG, Paris FC to Paris FC -- never cross
    mkts = (_game("KXLIGUE1GAME", "26SEP05", "PSGNIC", "PSG", "PSG", "NIC", "Nice")
            + _game("KXLIGUE1GAME", "26SEP05", "PFCLEN", "PFC", "Paris FC", "LEN", "Lens"))
    idx = _idx("fl1", mkts)
    rp = _run("fl1", "fl1-psg-nic-2026-09-05-psg", "Yes", "Will Paris Saint-Germain FC win on 2026-09-05?", idx)
    rf = _run("fl1", "fl1-pfc-len-2026-09-05-pfc", "Yes", "Will Paris FC win on 2026-09-05?", idx)
    assert rp.status == "matched" and rp.kalshi_ticker.endswith("-PSG"), rp
    assert rf.status == "matched" and rf.kalshi_ticker.endswith("-PFC"), rf


def test_mls_lafc_vs_galaxy_distinct():
    mkts = (_game("KXMLSGAME", "26SEP05", "LAFSEA", "LAF", "Los Angeles F", "SEA", "Seattle")
            + _game("KXMLSGAME", "26SEP05", "LAGRSL", "LAG", "Los Angeles G", "RSL", "Salt Lake"))
    idx = _idx("mls", mkts)
    rf = _run("mls", "mls-laf-sea-2026-09-05-laf", "Yes", "Will Los Angeles FC win on 2026-09-05?", idx)
    rg = _run("mls", "mls-lag-rsl-2026-09-05-lag", "Yes", "Will Los Angeles Galaxy win on 2026-09-05?", idx)
    assert rf.status == "matched" and rf.kalshi_ticker.endswith("-LAF"), rf
    assert rg.status == "matched" and rg.kalshi_ticker.endswith("-LAG"), rg


def test_ucl_inter_milan_vs_ic_escaldes_distinct():
    mkts = (_game("KXUCLGAME", "26SEP05", "INTAJA", "INT", "Inter", "AJA", "Eindhoven")
            + _game("KXUCLGAME", "26SEP05", "ICELAR", "ICE", "IC Escaldes", "LAR", "Larne"))
    idx = _idx("ucl", mkts)
    ri = _run("ucl", "ucl-int-aja-2026-09-05-int", "Yes", "Will FC Internazionale Milano win on 2026-09-05?", idx)
    re = _run("ucl", "ucl-ice-lar-2026-09-05-ice", "Yes", "Will Inter Club d'Escaldes win on 2026-09-05?", idx)
    assert ri.status == "matched" and ri.kalshi_ticker.endswith("-INT"), ri
    # IC Escaldes must NOT route to Inter Milan; it either matches its own ticker or safely misses
    assert re.kalshi_ticker != ri.kalshi_ticker, (ri, re)
    if re.status == "matched":
        assert re.kalshi_ticker.endswith("-ICE"), re


# ── 90-min "Reg Time:" strip ─────────────────────────────────────────────────────────────────
def test_reg_time_prefix_stripped():
    ms = [{"ticker": "KXUCLGAME-26SEP05CELSLO-CEL", "title": "Reg Time: Celtic wins", "yes_sub_title": "Reg Time: Celtic"},
          {"ticker": "KXUCLGAME-26SEP05CELSLO-SLO", "title": "Reg Time: Slovan wins", "yes_sub_title": "Reg Time: Slovan Bratislava"},
          {"ticker": "KXUCLGAME-26SEP05CELSLO-TIE", "title": "Reg Time: Tie", "yes_sub_title": "Reg Time: Tie"}]
    idx = _idx("ucl", ms)
    r = _run("ucl", "ucl-cel-slo-2026-09-05-cel", "Yes", "Will Celtic FC win on 2026-09-05?", idx)
    assert r.status == "matched" and r.kalshi_ticker.endswith("-CEL"), r
    rd = _run("ucl", "ucl-cel-slo-2026-09-05-draw", "Yes", "Will Celtic vs. Slovan Bratislava end in a draw?", idx)
    assert rd.status == "matched" and rd.kalshi_ticker.endswith("-TIE"), rd


# ── moneyline-only gating ────────────────────────────────────────────────────────────────────
def test_totals_and_spreads_skipped():
    idx = _idx("epl", _game("KXEPLGAME", "26SEP05", "ARSCHE", "ARS", "Arsenal", "CHE", "Chelsea"))
    rt = _run("epl", "epl-ars-che-2026-09-05-total-2pt5", "Over", "Arsenal vs. Chelsea: O/U 2.5", idx)
    assert rt.status == "skip_non_moneyline", rt
    rs = _run("epl", "epl-ars-che-2026-09-05-spread-home-1pt5", "Chelsea", "Spread: Arsenal (-1.5)", idx)
    assert rs.status == "skip_non_moneyline", rs


def test_market_type_excluded_when_not_allowed():
    cfg = SO.LEAGUES["epl"]
    idx = _idx("epl", _game("KXEPLGAME", "26SEP05", "ARSCHE", "ARS", "Arsenal", "CHE", "Chelsea"))
    r = SO.match_bet(SO.parse_poly_bet("epl-ars-che-2026-09-05-ars", "Yes", cfg, "Will Arsenal FC win on 2026-09-05?"),
                     idx, set(idx), cfg, allowed_market_types=())
    assert r.status == "skip_market_type_excluded", r


# ── window + fail-safe ───────────────────────────────────────────────────────────────────────
def test_plus_minus_one_day_window():
    idx = _idx("epl", _game("KXEPLGAME", "26SEP06", "ARSCHE", "ARS", "Arsenal", "CHE", "Chelsea"))
    for d, ok in (("2026-09-05", True), ("2026-09-07", True), ("2026-09-08", False)):
        r = _run("epl", "epl-ars-che-%s-ars" % d, "Yes", "Will Arsenal FC win on %s?" % d, idx)
        assert (r.status == "matched") == ok, (d, r)


def test_non_soccer_slug_and_failsafe():
    cfg = SO.LEAGUES["epl"]
    assert SO.parse_poly_bet("nba-lal-bos-2026-09-05", "Yes", cfg, "x").market_type == "non_soccer"
    r = SO.match_bet(SO.parse_poly_bet("epl-ars-che-2026-09-05-ars", "Yes", cfg, "Will Arsenal FC win on 2026-09-05?"),
                     {}, set(), cfg)
    assert r.status in ("out_of_window", "no_kalshi_contract") and r.kalshi_ticker is None, r
