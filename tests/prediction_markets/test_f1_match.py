"""F1 race-winner matcher (2026-09-14): KXF1RACE per-driver binary, DATE-join, driver-by-name, code-anchored.
Offline/synthetic -- no network, no DB. Fixtures mirror the LIVE ticker/title/yes_sub_title/slug shapes probed
2026-09-14 (Italian GP: KXF1RACE-ITAGP26-*; Poly `f1-italian-grand-prix-winner-{driver}-{date}`).

Independence note (Jack's rule): every expected (ticker, leg) is derived from the ticker's OWN -CODE + the
"Yes->buy YES / No->buy NO on that driver" convention, NOT from the matcher's output.

Covers: the DATE-join (incl. the +/-1 UTC boundary), the driver bind (slug surname + title disambiguation),
the code-mislabel refusal, the multiple-races safe-miss, and the INERT `race_winner` token gate (NOT in the
legacy default -> a blank/NULL market_types F1 sub never trades it).
"""
from trading_corp.data import f1_poly_kalshi_match as F1X


# live-shaped Kalshi KXF1RACE (Italian GP, close 2026-09-06). code = ticker's own 3-char; yes_sub_title = full name.
def _mk(code, name, date="2026-09-06", event="ITAGP26"):
    return {"ticker": "KXF1RACE-%s-%s" % (event, code), "yes_sub_title": name, "close_date_iso": date}

ITA = [_mk("VER", "Max Verstappen"), _mk("GAS", "Pierre Gasly"), _mk("ALO", "Fernando Alonso"),
       _mk("ALB", "Alexander Albon"), _mk("SAI", "Carlos Sainz Jr."), _mk("RUS", "George Russell"),
       _mk("PIA", "Oscar Piastri")]


def _idx(markets=None):
    return F1X.build_kalshi_f1_index(markets if markets is not None else ITA)


def _dates(idx):
    return frozenset(idx.keys())


# ── parse ────────────────────────────────────────────────────────────────────
def test_parse_winner_slug():
    p = F1X.parse_poly_f1_bet("f1-italian-grand-prix-winner-gasly-2026-09-06", "Yes",
                              "Will Pierre Gasly win the 2026 F1 Italian Grand Prix?")
    assert p.market_type == "race_winner" and p.date_iso == "2026-09-06"
    assert p.driver_slug == "gasly" and p.driver_full == "Pierre Gasly" and p.leg == "yes"


def test_parse_no_leg():
    p = F1X.parse_poly_f1_bet("f1-italian-grand-prix-winner-gasly-2026-09-06", "No", "Will Pierre Gasly win the ... Grand Prix?")
    assert p.market_type == "race_winner" and p.leg == "no"


def test_parse_sprint_winner_is_non_winner():
    # a sprint-winner is NOT the race winner -> labelled skip (not the race_winner type)
    p = F1X.parse_poly_f1_bet("f1-british-grand-prix-sprint-winner-piastri-2026-07-04", "Yes")
    assert p.market_type == "non_winner"


def test_parse_props_are_non_winner():
    for slug in ("f1-dutch-grand-prix-winning-margin-2026-08-23",
                 "f1-spanish-grand-prix-driver-podium-2026-09-13",
                 "f1-spanish-grand-prix-red-flag-2026-09-13",
                 "f1-british-grand-prix-sprint-qualifying-pole-winner-piastri-2026-07-03"):
        assert F1X.parse_poly_f1_bet(slug, "Yes").market_type == "non_winner", slug


def test_parse_non_f1():
    assert F1X.parse_poly_f1_bet("boxing-allen-carty-2026-09-05", "Carty").market_type == "non_f1"
    assert F1X.parse_poly_f1_bet("will-it-rain-during-the-spanish-grand-prix", "Yes").market_type == "non_f1"


def test_parse_bad_outcome_fail_reason():
    p = F1X.parse_poly_f1_bet("f1-italian-grand-prix-winner-gasly-2026-09-06", "Maybe")
    assert p.market_type == "race_winner" and p.leg is None and p.fail_reason


# ── index build ──────────────────────────────────────────────────────────────
def test_index_date_keyed():
    idx = _idx()
    assert "2026-09-06" in idx
    race = idx["2026-09-06"]
    assert race.event_ticker == "KXF1RACE-ITAGP26" and len(race.drivers) == 7


def test_index_skips_no_date_or_no_name():
    mk = [_mk("VER", "Max Verstappen"), {"ticker": "KXF1RACE-ITAGP26-GAS", "yes_sub_title": "Pierre Gasly", "close_date_iso": ""},
          {"ticker": "KXF1RACE-ITAGP26-ALO", "yes_sub_title": None, "close_date_iso": "2026-09-06"}]
    race = _idx(mk)["2026-09-06"]
    assert len(race.drivers) == 1 and race.drivers[0].code == "VER"


# ── match: happy path ────────────────────────────────────────────────────────
def test_match_yes():
    idx = _idx()
    p = F1X.parse_poly_f1_bet("f1-italian-grand-prix-winner-gasly-2026-09-06", "Yes", "Will Pierre Gasly win the 2026 F1 Italian Grand Prix?")
    r = F1X.match_bet(p, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == "KXF1RACE-ITAGP26-GAS" and r.leg == "yes"


def test_match_no_leg_copies_kalshi_no():
    idx = _idx()
    p = F1X.parse_poly_f1_bet("f1-italian-grand-prix-winner-verstappen-2026-09-06", "No", "Will Max Verstappen win the 2026 F1 Italian Grand Prix?")
    r = F1X.match_bet(p, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == "KXF1RACE-ITAGP26-VER" and r.leg == "no"


def test_match_suffix_surname_sainz():
    idx = _idx()
    p = F1X.parse_poly_f1_bet("f1-italian-grand-prix-winner-sainz-2026-09-06", "Yes", "Will Carlos Sainz Jr. win the 2026 F1 Italian Grand Prix?")
    r = F1X.match_bet(p, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == "KXF1RACE-ITAGP26-SAI"


def test_match_date_window_plus_one():
    # Poly slug date 2026-09-13 (Spanish, local) vs Kalshi close 2026-09-14 (UTC) -> +/-1 window resolves it.
    idx = _idx([_mk("VER", "Max Verstappen", date="2026-09-14", event="SPAGP26"),
                _mk("NOR", "Lando Norris", date="2026-09-14", event="SPAGP26")])
    p = F1X.parse_poly_f1_bet("f1-spanish-grand-prix-winner-norris-2026-09-13", "Yes", "Will Lando Norris win the 2026 F1 Spanish Grand Prix?")
    r = F1X.match_bet(p, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == "KXF1RACE-SPAGP26-NOR"


# ── driver bind edge cases ───────────────────────────────────────────────────
def test_same_surname_disambiguated_by_title():
    mk = ITA + [_mk("NOR", "Lando Norris"), _mk("NRR", "Alex Norris")]   # both surname 'norris', both codes subsequence it
    idx = _idx(mk)
    p = F1X.parse_poly_f1_bet("f1-italian-grand-prix-winner-norris-2026-09-06", "Yes", "Will Lando Norris win the 2026 F1 Italian Grand Prix?")
    r = F1X.match_bet(p, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == "KXF1RACE-ITAGP26-NOR"


def test_same_surname_no_title_is_safe_miss():
    mk = ITA + [_mk("NOR", "Lando Norris"), _mk("NRR", "Alex Norris")]
    idx = _idx(mk)
    p = F1X.parse_poly_f1_bet("f1-italian-grand-prix-winner-norris-2026-09-06", "Yes", None)   # no title -> cannot disambiguate
    r = F1X.match_bet(p, idx, _dates(idx))
    assert r.status == "driver_ambiguous" and r.kalshi_ticker is None


def test_code_mislabel_refused():
    # a Kalshi MISLABEL: the VER-coded ticker carries "Lewis Hamilton" -> code VER does not subsequence 'hamilton' -> refuse
    mk = [{"ticker": "KXF1RACE-ITAGP26-VER", "yes_sub_title": "Lewis Hamilton", "close_date_iso": "2026-09-06"},
          _mk("GAS", "Pierre Gasly")]
    idx = _idx(mk)
    p = F1X.parse_poly_f1_bet("f1-italian-grand-prix-winner-hamilton-2026-09-06", "Yes", "Will Lewis Hamilton win the 2026 F1 Italian Grand Prix?")
    r = F1X.match_bet(p, idx, _dates(idx))
    assert r.status != "matched" and r.kalshi_ticker is None


def test_driver_not_in_race():
    idx = _idx()
    p = F1X.parse_poly_f1_bet("f1-italian-grand-prix-winner-hamilton-2026-09-06", "Yes", "Will Lewis Hamilton win the 2026 F1 Italian Grand Prix?")
    assert F1X.match_bet(p, idx, _dates(idx)).status == "winner_outcome_unresolved"


def test_out_of_window():
    idx = _idx()
    p = F1X.parse_poly_f1_bet("f1-italian-grand-prix-winner-gasly-2020-01-01", "Yes", "Will Pierre Gasly win the 2020 F1 Italian Grand Prix?")
    assert F1X.match_bet(p, idx, _dates(idx)).status == "out_of_window"


def test_multiple_races_in_window_safe_miss():
    # two races within +/-1 day (never happens for F1, but the guard must be a SAFE MISS if it ever did)
    idx = _idx([_mk("GAS", "Pierre Gasly", date="2026-09-06", event="ITAGP26"),
                _mk("GAS", "Pierre Gasly", date="2026-09-07", event="XXXGP26")])
    p = F1X.parse_poly_f1_bet("f1-italian-grand-prix-winner-gasly-2026-09-06", "Yes", "Will Pierre Gasly win the ... Grand Prix?")
    assert F1X.match_bet(p, idx, _dates(idx)).status == "driver_ambiguous"


# ── ★ INERT: race_winner token gate ──────────────────────────────────────────
def test_inert_race_winner_not_in_legacy_default():
    # the legacy default market_types (what a blank/NULL resolves to) does NOT include race_winner -> F1 stays OFF.
    idx = _idx()
    p = F1X.parse_poly_f1_bet("f1-italian-grand-prix-winner-gasly-2026-09-06", "Yes", "Will Pierre Gasly win the ... Grand Prix?")
    r = F1X.match_bet(p, idx, _dates(idx), allowed_market_types=("moneyline", "total", "spread"))
    assert r.status == "skip_market_type_excluded"


def test_enabled_when_token_present():
    idx = _idx()
    p = F1X.parse_poly_f1_bet("f1-italian-grand-prix-winner-gasly-2026-09-06", "Yes", "Will Pierre Gasly win the ... Grand Prix?")
    r = F1X.match_bet(p, idx, _dates(idx), allowed_market_types=("race_winner",))
    assert r.status == "matched"


# ── ★ Q5: END-TO-END through the DEPLOYED evaluate() (not the scratch matcher) ──
def test_deployed_evaluate_e2e_f1():
    """One F1 signal through the REAL execution.evaluate() gate stack + MATCHER_ADAPTERS['f1'] dispatch + the
    ctx.f1_race_index slot, asserting a dry_run_would_place with the independently-expected ticker/leg. The
    race_winner token is enabled here (it is NOT in the legacy default -> INERT until Jack sets it)."""
    import os, tempfile, sqlite3
    from trading_corp.prediction_markets import execution as E
    from trading_corp.prediction_markets import db as DB
    idx = _idx()
    ticker = "KXF1RACE-ITAGP26-GAS"                       # independent: outcome "Yes" on driver Gasly -> the GAS-code ticker, YES leg
    ctx = E.MarketContext({}, {}, {}, _dates(idx), {ticker: {
        "yes_ask_dollars": 0.20, "no_ask_dollars": 0.82, "yes_bid_dollars": 0.18, "no_bid_dollars": 0.80,
        "liquidity_dollars": 0.0, "yes_ask_size_fp": 1000.0, "yes_bid_size_fp": 1000.0, "exchange_index": 0}},
        f1_race_index=idx)
    sub = E.SubConfig(account_id="kalshi_test", category="f1", market_types=("race_winner",),
                      sizing_mode="contracts", fixed_stake_usd=0.0, per_order_usd_cap=100.0, daily_usd_cap=100.0,
                      max_open_usd=100.0, max_orders_per_day=10, max_slippage_cents=5, liquidity_ratio=0.75, contracts=1)
    sig = E.CopySignal(wallet="0xabc", slug="f1-italian-grand-prix-winner-gasly-2026-09-06", outcome="Yes",
                       condition_id="0xcond", outcome_index=0, signal_id="sigf1a", is_exit=False,
                       title="Will Pierre Gasly win the 2026 F1 Italian Grand Prix?")
    d = tempfile.mkdtemp(); path = os.path.join(d, "pm.db"); DB.init_db(path)
    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    jrnl = E.Journal(conn, ["kalshi_test"], 1_800_000_000)
    dec = E.evaluate(sig, sub, ctx, jrnl, conn, 1_800_000_000, shard_balances=None, venue_exposure=None,
                     legacy_db_path=os.path.join(d, "nonexistent_legacy.db"))
    conn.close()
    assert dec.status == "dry_run_would_place", dec.status
    assert dec.kalshi_ticker == ticker and dec.leg == "yes"
    assert (dec.body or {}).get("ticker") == ticker
