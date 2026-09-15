"""Boxing matcher (2026-09-14): KXBOXING winner-only, surname-tolerant bind, code-anchored. Offline/synthetic --
no network, no DB. Fixtures mirror the LIVE ticker/title/yes_sub_title/slug shapes probed 2026-09-14 (Zuffa Boxing
Garcia/Morales + Sandoval/Collazo cards; Poly `zuffa-`/`boxing-` slugs with SURNAME outcomes).

Independence note (Jack's rule -- the check must not share the transform): every expected (ticker, leg) is
hardcoded from the TICKER'S OWN -CODE suffix + the "buy YES on the bet fighter" convention, NOT from the matcher's
output -- so a transform bug cannot pass by agreeing with itself.

★ The board-flagged SURNAME RISK is proven a SAFE MISS in two places (never a wrong-competitor pick):
  - test_collision_same_surname_two_bouts_on_card  (cross-bout: two 'Garcia's in different bouts)
  - test_collision_same_surname_within_one_bout     (within-bout: a Garcia-vs-Garcia bout)
Plus the code-anchor refusal (test_code_swapped_bout_refused) and the INERT market_types gate (test_inert_*).
"""
from trading_corp.data import boxing_poly_kalshi_match as BX


# ── live-shaped Kalshi KXBOXING fixtures ─────────────────────────────────────
# Codes are the ticker's OWN variable-length surname prefixes (probed: SANDOV, COLLAZ, SCHOFI, BAHDI, GARCIA, MORALE).
# yes_sub_title carries the FULL name. One 2-bout date + a 1-bout date on a different day.
KXBOXING = [
    {"ticker": "KXBOXING-26SEP12GARCIAMORALE-GARCIA", "title": "Sean Garcia wins", "yes_sub_title": "Sean Garcia"},
    {"ticker": "KXBOXING-26SEP12GARCIAMORALE-MORALE", "title": "Abraham Morales wins", "yes_sub_title": "Abraham Morales"},
    {"ticker": "KXBOXING-26SEP12PANINLINGER-PANIN", "title": "Vlad Panin wins", "yes_sub_title": "Vlad Panin"},   # 2nd bout, same date
    {"ticker": "KXBOXING-26SEP12PANINLINGER-LINGER", "title": "Dakota Linger wins", "yes_sub_title": "Dakota Linger"},
    {"ticker": "KXBOXING-26OCT10SANDOVCOLLAZ-SANDOV", "title": "Ricardo Rafael Sandoval wins",
     "yes_sub_title": "Ricardo Rafael Sandoval"},                                  # a 1-bout date, 3-token full name
    {"ticker": "KXBOXING-26OCT10SANDOVCOLLAZ-COLLAZ", "title": "Oscar Collazo wins", "yes_sub_title": "Oscar Collazo"},
]


def _idx(markets=None):
    return BX.build_kalshi_boxing_index(markets if markets is not None else KXBOXING)


def _dates(idx):
    return frozenset(k[0] for k in idx)


# ── parse ────────────────────────────────────────────────────────────────────
def test_parse_zuffa_moneyline():
    p = BX.parse_poly_boxing_bet("zuffa-garci1-moral1-2026-09-12", "Garcia")
    assert p.market_type == "moneyline" and p.date_iso == "2026-09-12"
    assert p.outcome_name == "Garcia" and p.leg == "yes" and p.fail_reason is None


def test_parse_boxing_prefix_moneyline():
    p = BX.parse_poly_boxing_bet("boxing-allen-carty-2026-09-05", "Carty")
    assert p.market_type == "moneyline" and p.date_iso == "2026-09-05" and p.outcome_name == "Carty"


def test_parse_noise_suffix_still_moneyline():
    # Poly appends a trailing "-<digits>" dedup fragment on a slug collision -> still a winner bet.
    p = BX.parse_poly_boxing_bet("zuffa-garci1-moral1-2026-09-12-947", "Garcia")
    assert p.market_type == "moneyline" and p.date_iso == "2026-09-12"


def test_parse_real_suffix_is_prop_skip():
    # Boxing is winner-only; any real (non-noise) market suffix is a labelled skip, never a fail.
    p = BX.parse_poly_boxing_bet("zuffa-garci1-moral1-2026-09-12-method-ko", "Yes")
    assert p.market_type == "prop"


def test_parse_non_boxing_slug():
    assert BX.parse_poly_boxing_bet("ufc-dan6-salpar-2026-09-05", "Daniel Hooker").market_type == "non_boxing"
    assert BX.parse_poly_boxing_bet("mvp-jake-paul-2026-11-15", "Jake Paul").market_type == "non_boxing"  # novelty prefix


def test_parse_no_date_unparseable():
    assert BX.parse_poly_boxing_bet("zuffa-garci1-moral1", "Garcia").market_type == "unparseable"


def test_parse_empty_outcome_fail_reason():
    p = BX.parse_poly_boxing_bet("zuffa-garci1-moral1-2026-09-12", "")
    assert p.market_type == "moneyline" and p.fail_reason == "empty_outcome" and p.outcome_name is None


# ── index build ──────────────────────────────────────────────────────────────
def test_index_binds_via_yes_sub_title():
    idx = _idx()
    bout = idx[BX._bout_key("2026-09-12", "Sean Garcia", "Abraham Morales")]
    assert {bout.name_a, bout.name_b} == {"Sean Garcia", "Abraham Morales"}
    assert {bout.code_a, bout.code_b} == {"GARCIA", "MORALE"}
    assert bout.ticker_a.endswith("-" + bout.code_a) and bout.ticker_b.endswith("-" + bout.code_b)


def test_index_title_fallback_when_no_yes_sub_title():
    mk = [{"ticker": "KXBOXING-26SEP12GARCIAMORALE-GARCIA", "title": "Sean Garcia wins", "yes_sub_title": None},
          {"ticker": "KXBOXING-26SEP12GARCIAMORALE-MORALE", "title": "Abraham Morales wins", "yes_sub_title": ""}]
    idx = _idx(mk)
    bout = idx[BX._bout_key("2026-09-12", "Sean Garcia", "Abraham Morales")]
    assert {bout.name_a, bout.name_b} == {"Sean Garcia", "Abraham Morales"}


def test_index_skips_partial_and_malformed():
    mk = [{"ticker": "KXBOXING-26SEP12GARCIAMORALE-GARCIA", "title": "Sean Garcia wins", "yes_sub_title": "Sean Garcia"},
          {"ticker": "KXUFCFIGHT-26SEP12SILDEL-SIL", "title": "Jean Silva wins", "yes_sub_title": "Jean Silva"},  # non-boxing
          {"ticker": "garbage", "title": "x", "yes_sub_title": "y"}]
    # only ONE side of the Garcia bout -> partial -> skipped entirely
    assert _idx(mk) == {}


# ── match: happy path (surname outcome -> full-name Kalshi) ──────────────────
def test_match_surname_outcome_resolves_side():
    idx = _idx()
    # independent expectation: "Garcia" -> the ticker whose OWN code is GARCIA, YES leg.
    p = BX.parse_poly_boxing_bet("zuffa-garci1-moral1-2026-09-12", "Garcia")
    r = BX.match_bet(p, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == "KXBOXING-26SEP12GARCIAMORALE-GARCIA" and r.leg == "yes"


def test_match_other_side():
    idx = _idx()
    p = BX.parse_poly_boxing_bet("zuffa-garci1-moral1-2026-09-12", "Morales")
    r = BX.match_bet(p, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == "KXBOXING-26SEP12GARCIAMORALE-MORALE" and r.leg == "yes"


def test_match_full_name_outcome_also_works():
    # robustness: if Poly ever sends a full name, the UFC-strict multi-token path still binds correctly.
    idx = _idx()
    p = BX.parse_poly_boxing_bet("boxing-sando-colla-2026-10-10", "Ricardo Rafael Sandoval")
    r = BX.match_bet(p, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == "KXBOXING-26OCT10SANDOVCOLLAZ-SANDOV"


def test_match_surname_with_generational_suffix_in_kalshi():
    mk = [{"ticker": "KXBOXING-26SEP12GARCIAMORALE-GARCIA", "title": "Sean Garcia Jr wins", "yes_sub_title": "Sean Garcia Jr"},
          {"ticker": "KXBOXING-26SEP12GARCIAMORALE-MORALE", "title": "Abraham Morales wins", "yes_sub_title": "Abraham Morales"}]
    idx = _idx(mk)
    p = BX.parse_poly_boxing_bet("zuffa-garci1-moral1-2026-09-12", "Garcia")
    r = BX.match_bet(p, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == "KXBOXING-26SEP12GARCIAMORALE-GARCIA"


# ── ★ the SURNAME RISK: both collision shapes are a SAFE MISS, never a pick ──
def test_collision_same_surname_two_bouts_on_card():
    # Two different 'Garcia' fighters in DIFFERENT bouts on the same date -> which bout? UNKNOWABLE -> safe miss.
    mk = KXBOXING + [
        {"ticker": "KXBOXING-26SEP12GARCIALOPEZZ-GARCIA", "title": "Ryan Garcia wins", "yes_sub_title": "Ryan Garcia"},
        {"ticker": "KXBOXING-26SEP12GARCIALOPEZZ-LOPEZZ", "title": "Teofimo Lopez wins", "yes_sub_title": "Teofimo Lopez"},
    ]
    idx = _idx(mk)
    p = BX.parse_poly_boxing_bet("zuffa-garci1-lopez-2026-09-12", "Garcia")
    r = BX.match_bet(p, idx, _dates(idx))
    assert r.status == "abbrev_collision_ambiguous" and r.kalshi_ticker is None


def test_collision_same_surname_within_one_bout():
    # A Garcia-vs-Garcia bout: the surname matches BOTH fighters -> which side? UNKNOWABLE -> safe miss.
    mk = [{"ticker": "KXBOXING-26SEP12GARCIAGARCIB-GARCIA", "title": "Sean Garcia wins", "yes_sub_title": "Sean Garcia"},
          {"ticker": "KXBOXING-26SEP12GARCIAGARCIB-GARCIB", "title": "Ryan Garcia wins", "yes_sub_title": "Ryan Garcia"}]
    idx = _idx(mk)
    p = BX.parse_poly_boxing_bet("zuffa-sgarc-rgarc-2026-09-12", "Garcia")
    r = BX.match_bet(p, idx, _dates(idx))
    assert r.status == "abbrev_collision_ambiguous" and r.kalshi_ticker is None


def test_code_swapped_bout_refused():
    # A Kalshi MISLABEL: the two yes_sub_titles are bound to the WRONG -CODE tickers. Code-anchor refuses -> safe miss.
    mk = [{"ticker": "KXBOXING-26SEP12GARCIAMORALE-GARCIA", "title": "Abraham Morales wins", "yes_sub_title": "Abraham Morales"},
          {"ticker": "KXBOXING-26SEP12GARCIAMORALE-MORALE", "title": "Sean Garcia wins", "yes_sub_title": "Sean Garcia"}]
    idx = _idx(mk)
    p = BX.parse_poly_boxing_bet("zuffa-garci1-moral1-2026-09-12", "Garcia")
    r = BX.match_bet(p, idx, _dates(idx))
    assert r.status != "matched" and r.kalshi_ticker is None      # refused (never a wrong-competitor order)


# ── misses classified by reason ──────────────────────────────────────────────
def test_out_of_window_vs_no_contract():
    idx = _idx()
    # a date with NO bouts in the index and not in kalshi_dates -> out_of_window
    p = BX.parse_poly_boxing_bet("zuffa-a-b-2020-01-01", "Garcia")
    assert BX.match_bet(p, idx, _dates(idx)).status == "out_of_window"
    # a date that IS in the fetch window but has no matching bout -> no_kalshi_contract
    p2 = BX.parse_poly_boxing_bet("zuffa-a-b-2026-09-12", "Nonesuch")
    assert BX.match_bet(p2, idx, _dates(idx)).status == "winner_outcome_unresolved"


def test_surname_not_in_any_bout():
    idx = _idx()
    p = BX.parse_poly_boxing_bet("zuffa-x-y-2026-09-12", "Fury")
    assert BX.match_bet(p, idx, _dates(idx)).status == "winner_outcome_unresolved"


# ── ★ INERT: the market_types gate + the legacy-default guarantee ────────────
def test_inert_market_type_excluded_when_not_enabled():
    # A boxing sub whose market_types does NOT include 'moneyline' -> skip_market_type_excluded (never places).
    idx = _idx()
    p = BX.parse_poly_boxing_bet("zuffa-garci1-moral1-2026-09-12", "Garcia")
    r = BX.match_bet(p, idx, _dates(idx), allowed_market_types=("first_inning_run",))
    assert r.status == "skip_market_type_excluded"


def test_inert_default_allowed_types_include_moneyline():
    # The legacy default set (what a blank/NULL market_types resolves to) copies the boxing WINNER once armed.
    idx = _idx()
    p = BX.parse_poly_boxing_bet("zuffa-garci1-moral1-2026-09-12", "Garcia")
    r = BX.match_bet(p, idx, _dates(idx), allowed_market_types=("moneyline", "total", "spread"))
    assert r.status == "matched"


# ── ★ "Surname Initial." Kalshi format fix (2026-09-14) + proof it did NOT weaken the bind ──
# Live shape probed on the 09-12 main card: KXBOXING-26SEP12MA-M / -MA-A (2-char blob, 1-char code),
# yes_sub_title "Magsayo M." / "Cortes A." -- surname LEADS, a trailing initial follows. 7 of 300 markets.
_INITFMT = [
    {"ticker": "KXBOXING-26SEP12MA-M", "title": "Magsayo M. wins", "yes_sub_title": "Magsayo M."},
    {"ticker": "KXBOXING-26SEP12MA-A", "title": "Cortes A. wins", "yes_sub_title": "Cortes A."},
]


def test_surname_initial_format_binds():
    # the near-miss the audit found: whale "Cortes" now binds to the "Cortes A." (initial-format) ticker.
    idx = _idx(_INITFMT)
    p = BX.parse_poly_boxing_bet("zuffa-magsa-corte-2026-09-12", "Cortes")
    r = BX.match_bet(p, idx, _dates(idx))
    assert r.status == "matched" and r.kalshi_ticker == "KXBOXING-26SEP12MA-A" and r.leg == "yes"
    # and the other side binds to Magsayo (independent: the ticker code is M -> the Magsayo market)
    p2 = BX.parse_poly_boxing_bet("zuffa-magsa-corte-2026-09-12", "Magsayo")
    assert BX.match_bet(p2, idx, _dates(idx)).kalshi_ticker == "KXBOXING-26SEP12MA-M"


def test_surname_initial_fix_does_not_break_first_last():
    # regression: the fix must NOT change the normal "First Last" bind ("Sean Garcia" surname stays 'garcia').
    idx = _idx()   # the standard First-Last fixture
    p = BX.parse_poly_boxing_bet("zuffa-garci1-moral1-2026-09-12", "Garcia")
    assert BX.match_bet(p, idx, _dates(idx)).kalshi_ticker == "KXBOXING-26SEP12GARCIAMORALE-GARCIA"


def test_surname_initial_fix_did_NOT_weaken_collision_guard():
    # ★ board requirement: prove the looser parse still SAFE-MISSES a same-card same-surname collision.
    # Two 'Cortes' fighters on the card (one First-Last, one Initial-format) -> "Cortes" matches both -> safe miss.
    mk = _INITFMT + [
        {"ticker": "KXBOXING-26SEP12CORTESRUBIO-CORTES", "title": "Danny Cortes wins", "yes_sub_title": "Danny Cortes"},
        {"ticker": "KXBOXING-26SEP12CORTESRUBIO-RUBIO", "title": "Pablo Rubio wins", "yes_sub_title": "Pablo Rubio"},
    ]
    idx = _idx(mk)
    p = BX.parse_poly_boxing_bet("zuffa-x-corte-2026-09-12", "Cortes")
    r = BX.match_bet(p, idx, _dates(idx))
    assert r.status == "abbrev_collision_ambiguous" and r.kalshi_ticker is None


# ── ★ Q5: END-TO-END through the DEPLOYED evaluate() (not the scratch matcher) ──
def test_deployed_evaluate_e2e_boxing():
    """One boxing signal through the REAL execution.evaluate() gate stack (arm/sizing/quote/liquidity/dedup/caps
    + MATCHER_ADAPTERS dispatch + the ctx.boxing_index slot), asserting a dry_run_would_place with the
    independently-expected ticker/leg. Closes the wiring blind spot the scratch-matcher dry-runs left."""
    import os, tempfile, sqlite3
    from trading_corp.prediction_markets import execution as E
    from trading_corp.prediction_markets import db as DB
    idx = _idx()
    ticker = "KXBOXING-26SEP12GARCIAMORALE-GARCIA"        # independent expectation: outcome "Garcia" -> the GARCIA-code ticker, YES
    ctx = E.MarketContext({}, {}, {}, _dates(idx), {ticker: {
        "yes_ask_dollars": 0.50, "no_ask_dollars": 0.50, "yes_bid_dollars": 0.49, "no_bid_dollars": 0.49,
        "liquidity_dollars": 0.0, "yes_ask_size_fp": 1000.0, "yes_bid_size_fp": 1000.0, "exchange_index": 0}},
        boxing_index=idx)
    sub = E.SubConfig(account_id="kalshi_test", category="boxing", market_types=("moneyline",),
                      sizing_mode="contracts", fixed_stake_usd=0.0, per_order_usd_cap=100.0, daily_usd_cap=100.0,
                      max_open_usd=100.0, max_orders_per_day=10, max_slippage_cents=5, liquidity_ratio=0.75, contracts=1)
    sig = E.CopySignal(wallet="0xabc", slug="zuffa-garci1-moral1-2026-09-12", outcome="Garcia",
                       condition_id="0xcond", outcome_index=0,
                       signal_id="sigbox1", is_exit=False, title="Zuffa Boxing: Garcia vs. Morales")
    d = tempfile.mkdtemp(); path = os.path.join(d, "pm.db"); DB.init_db(path)
    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    jrnl = E.Journal(conn, ["kalshi_test"], 1_800_000_000)
    dec = E.evaluate(sig, sub, ctx, jrnl, conn, 1_800_000_000, shard_balances=None, venue_exposure=None,
                     legacy_db_path=os.path.join(d, "nonexistent_legacy.db"))   # arm fail-safe False; dry-run still computes
    conn.close()
    assert dec.status == "dry_run_would_place", dec.status
    assert dec.kalshi_ticker == ticker and dec.leg == "yes"
    assert (dec.body or {}).get("ticker") == ticker
