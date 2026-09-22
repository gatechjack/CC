"""PHASE 1 (2026-09-22): NFL quarter/2H markets (1q/2q/3q/4q/2h -- winner/total/spread) + TEAM TOTALS.
Matcher-level proofs (offline; data-side import only -- no broker). The deployed-path proofs (MATCHER_ADAPTERS
dispatch parity, evaluate() e2e, ctx-builder import) live in test_nfl_periods_dispatch.py and run on box-scratch
where execution.py/live_driver.py import. Proves: exact period token parse; route-only (a period/team-total bet
can NEVER bind a full-game or other-period ticker); EXACT-STRIKE (no rung -> safe miss, never nearest); leg from the
OUTCOME text (Over->yes/Under->no); winner 3-way incl TIE; team-total team AND strike each an independent safe miss;
and the enable-token gate (blank/legacy market_types never auto-enables a new token)."""
from trading_corp.data import sports_structural_match as SS

cfg = SS.LEAGUES["nfl"]
STEM = "26SEP21NYGLAR"          # away=NYG, home=LAR
DATE = "2026-09-21"
ALLOW = ("moneyline", "total", "spread", "first_half", "q1", "q2", "q3", "q4", "h2", "team_total")


def _ctx():
    gi = SS.build_game_index(["KXNFLGAME-%s-NYG" % STEM, "KXNFLGAME-%s-LAR" % STEM], cfg)
    dates = frozenset(k[0] for k in gi)
    period_idx = SS.build_period_indices({
        "q1": {"win": ["KXNFL1Q-%s-NYG" % STEM, "KXNFL1Q-%s-LAR" % STEM, "KXNFL1Q-%s-TIE" % STEM],
               "total": ["KXNFL1QTOTAL-%s-8" % STEM], "spread": ["KXNFL1QSPREAD-%s-NYG8" % STEM]},
        "q2": {"win": ["KXNFL2Q-%s-NYG" % STEM], "total": [], "spread": []},
    }, cfg)
    tt_idx = SS.build_team_total_index(["KXNFLTEAMTOTAL-%s-NYG18" % STEM], cfg)
    full_tot = SS.build_total_index(["KXNFLTOTAL-%s-8" % STEM], cfg)   # full-game 7.5 present -> must stay unreachable
    return gi, dates, period_idx, tt_idx, full_tot


def _run(slug, outcome, allow=ALLOW):
    gi, dates, period_idx, tt_idx, full_tot = _ctx()
    pb = SS.parse_poly_bet(slug, outcome, cfg)
    r = SS.match_bet(pb, gi, dates, cfg, allowed_market_types=allow,
                     period_indices=period_idx, team_total_index=tt_idx, total_index=full_tot)
    return pb, r


# ── parse: exact tokens -> market types; unknown -> non_moneyline (fail-closed) ──
def test_parse_period_tokens():
    for tok, pk in (("1q", "q1"), ("2q", "q2"), ("3q", "q3"), ("4q", "q4"), ("2h", "h2")):
        assert SS.parse_poly_bet("nfl-nyg-lar-%s-%s-moneyline" % (DATE, tok), "New York Giants", cfg).market_type == "%s_winner" % pk
        assert SS.parse_poly_bet("nfl-nyg-lar-%s-%s-total-7pt5" % (DATE, tok), "Over", cfg).market_type == "%s_total" % pk
        assert SS.parse_poly_bet("nfl-nyg-lar-%s-%s-spread-away-7pt5" % (DATE, tok), "New York Giants", cfg).market_type == "%s_spread" % pk


def test_parse_teamtotal_and_unknown():
    assert SS.parse_poly_bet("nfl-nyg-lar-%s-team-total-nyg-17pt5" % DATE, "Over", cfg).market_type == "team_total"
    # unknown period-ish token fails closed (never silently moneyline/matched)
    assert SS.parse_poly_bet("nfl-nyg-lar-%s-q1-moneyline" % DATE, "x", cfg).market_type == "non_moneyline"   # 'q1' not '1q'
    assert SS.parse_poly_bet("nfl-nyg-lar-%s-overtime-moneyline" % DATE, "x", cfg).market_type == "non_moneyline"


# ── match: winner / total / spread / team_total happy paths ──
def test_match_winner_and_tie():
    _, r = _run("nfl-nyg-lar-%s-1q-moneyline" % DATE, "New York Giants")
    assert r.status == "matched" and r.kalshi_ticker == "KXNFL1Q-%s-NYG" % STEM and r.leg == "yes"
    _, r = _run("nfl-nyg-lar-%s-1q-moneyline" % DATE, "Tie")
    assert r.status == "matched" and r.kalshi_ticker == "KXNFL1Q-%s-TIE" % STEM


def test_match_total_leg_from_outcome():
    _, over = _run("nfl-nyg-lar-%s-1q-total-7pt5" % DATE, "Over")
    _, under = _run("nfl-nyg-lar-%s-1q-total-7pt5" % DATE, "Under")
    assert over.status == "matched" and over.leg == "yes" and over.kalshi_ticker == "KXNFL1QTOTAL-%s-8" % STEM
    assert under.status == "matched" and under.leg == "no" and under.kalshi_ticker == "KXNFL1QTOTAL-%s-8" % STEM


def test_match_spread():
    _, r = _run("nfl-nyg-lar-%s-1q-spread-away-7pt5" % DATE, "New York Giants")
    assert r.status == "matched" and r.kalshi_ticker == "KXNFL1QSPREAD-%s-NYG8" % STEM and r.leg == "yes"


def test_match_teamtotal_leg_and_strike():
    _, over = _run("nfl-nyg-lar-%s-team-total-nyg-17pt5" % DATE, "Over")
    _, under = _run("nfl-nyg-lar-%s-team-total-nyg-17pt5" % DATE, "Under")
    assert over.status == "matched" and over.leg == "yes" and over.kalshi_ticker == "KXNFLTEAMTOTAL-%s-NYG18" % STEM
    assert under.status == "matched" and under.leg == "no"


# ── EXACT-STRIKE ONLY: no rung -> safe miss, never nearest ──
def test_exact_strike_only_period():
    _, r = _run("nfl-nyg-lar-%s-1q-total-8pt5" % DATE, "Over")   # need N=9 rung; only 7.5(-8) exists
    assert r.status == "no_kalshi_strike" and r.kalshi_ticker is None


def test_exact_strike_only_teamtotal():
    _, r = _run("nfl-nyg-lar-%s-team-total-nyg-18pt5" % DATE, "Over")   # need NYG19; only NYG18 exists
    assert r.status == "no_kalshi_strike"


# ── ROUTE-ONLY: a period/team-total bet can NEVER bind a full-game or other-period ticker ──
def test_route_only_period_never_fullgame():
    _, r = _run("nfl-nyg-lar-%s-1q-total-7pt5" % DATE, "Over")
    assert r.kalshi_ticker == "KXNFL1QTOTAL-%s-8" % STEM      # NOT KXNFLTOTAL-...-8 (also 7.5, present in ctx)
    assert not r.kalshi_ticker.startswith("KXNFLTOTAL-")


def test_route_only_wrong_period_is_miss():
    _, r = _run("nfl-nyg-lar-%s-2q-total-7pt5" % DATE, "Over")   # q2 total idx is empty
    assert r.status == "no_kalshi_strike" and r.kalshi_ticker is None


# ── TEAM + STRIKE are two INDEPENDENT safe misses ──
def test_teamtotal_wrong_team_safe_miss():
    pb, r = _run("nfl-nyg-lar-%s-team-total-bal-17pt5" % DATE, "Over")   # BAL not in this game
    assert r.status == "fail" and "team_total_team_not_in_game" in (r.reason or "")


def test_teamtotal_whole_number_line_safe_miss():
    # Finding 1 (2026-09-22): Poly team totals resolve Over on "equal to OR exceeds" -> a whole-number line makes the
    # push an Over win, which Kalshi's N+ rung does not mirror. A whole line (Npt0) is refused, never copied.
    pb = SS.parse_poly_bet("nfl-nyg-lar-%s-team-total-nyg-17pt0" % DATE, "Over", cfg)
    assert pb.market_type == "team_total" and "whole_number" in (pb.fail_reason or "")
    _, r = _run("nfl-nyg-lar-%s-team-total-nyg-17pt0" % DATE, "Over")
    assert r.status == "fail" and r.kalshi_ticker is None


# ── ENABLE-TOKEN GATE: blank/legacy market_types never auto-enables a new token ──
def test_blank_default_never_autoenables():
    for tok_type in ("1q-moneyline", "2h-total-7pt5", "team-total-nyg-17pt5"):
        _, r = _run("nfl-nyg-lar-%s-%s" % (DATE, tok_type), "New York Giants", allow=("moneyline", "total", "spread"))
        assert r.status == "skip_market_type_excluded"


def test_period_types_registered():
    for pk in ("q1", "q2", "q3", "q4", "h2"):
        for m in ("winner", "total", "spread"):
            assert "%s_%s" % (pk, m) in SS.COPYABLE_MARKET_TYPES
    assert "team_total" in SS.COPYABLE_MARKET_TYPES
    # first_half untouched (shipped)
    assert "first_half_winner" in SS.COPYABLE_MARKET_TYPES
