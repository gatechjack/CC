"""ITF match-winner matcher (2026-09-16): KX(ITFMATCH|ITFWMATCH) pair-keyed, men + women in ONE category.
Clone of the tennis matcher; offline/synthetic -- no network, no DB. Fixtures mirror the LIVE ticker/title/slug
shapes probed 2026-09-15/16 (men KXITFMATCH-26SEP16WENTOS-WEN "Jefferson Wendler Filho wins"; women
KXITFWMATCH-...; Poly slug `itf-{p1}-{p2}-{YYYY-MM-DD}` for BOTH tours).

Independence (Jack's rule): every expected (ticker, leg) is derived from the ticker's OWN -CODE + the title
"A vs B" + the "whale-buys-YES-on-the-player-named" convention, NOT from the matcher's output.

★ SEPARATION FROM atp/wta IS PROVEN BY CONSTRUCTION (route-only tests that FAIL if the slug OR ticker anchors
were shared with the tennis matcher), not by observing the right answer. ITF and atp/wta share NO regex, NO
ctx index field, and NO adapter -- an ITF slug/ticker can never reach the atp/wta path and vice-versa.
"""
import os
import sqlite3
import tempfile

from trading_corp.data import itf_poly_kalshi_match as ITF
from trading_corp.data import tennis_poly_kalshi_match as TEN


# ── live-shaped fixtures (men KXITFMATCH + women KXITFWMATCH, same date, merged into one index) ──
MEN = [{"ticker": "KXITFMATCH-26SEP16WENTOS-WEN", "title": "Jefferson Wendler Filho wins"},
       {"ticker": "KXITFMATCH-26SEP16WENTOS-TOS", "title": "Marco Tosetti wins"}]
WOMEN = [{"ticker": "KXITFWMATCH-26SEP16ZELPRE-ZEL", "title": "Darja Zeltina wins"},
         {"ticker": "KXITFWMATCH-26SEP16ZELPRE-PRE", "title": "Amarni Pree wins"}]


def _idx(markets=None):
    return ITF.build_kalshi_itf_index(markets if markets is not None else (MEN + WOMEN))


def _dates(idx):
    return frozenset(idx.keys())


def _match(markets, slug, outcome, title=None):
    idx = ITF.build_kalshi_itf_index(markets)
    parsed = ITF.parse_poly_itf_bet(slug, outcome, title)
    return ITF.match_bet(parsed, idx, _dates(idx), allowed_market_types=ITF.COPYABLE_MARKET_TYPES)


# ── parse ──────────────────────────────────────────────────────────────────────
def test_parse_winner_slug():
    p = ITF.parse_poly_itf_bet("itf-wendler-tosetti-2026-09-16", "Jefferson Wendler Filho",
                               "ITF: Jefferson Wendler Filho vs Marco Tosetti")
    assert p.market_type == "itf_moneyline" and p.date_iso == "2026-09-16"
    assert p.player_a == "Jefferson Wendler Filho" and p.player_b == "Marco Tosetti"
    assert p.outcome_name == "Jefferson Wendler Filho"


def test_parse_prop_suffix_is_prop():
    # a trailing suffix after the date = a prop (set/game/handicap) -> out of scope
    p = ITF.parse_poly_itf_bet("itf-wendler-tosetti-2026-09-16-total-games-over", "Over")
    assert p.market_type == "prop"


def test_parse_non_itf_atp_slug():
    # ★ an atp-/wta- slug is non_itf HERE -> it can never reach the ITF index (separation by construction)
    assert ITF.parse_poly_itf_bet("atp-med-rin-2026-09-04", "Daniil Medvedev").market_type == "non_itf"
    assert ITF.parse_poly_itf_bet("wta-swi-gau-2026-09-04", "Iga Swiatek").market_type == "non_itf"


def test_parse_futures_no_date_is_unparseable():
    # a futures/outright itf- slug WITHOUT a match date -> unparseable -> a SAFE MISS at match time (never a pick)
    p = ITF.parse_poly_itf_bet("itf-w75-madrid-outright-winner", "Some Player")
    assert p.market_type == "unparseable" and p.fail_reason == "slug_no_date"


# ── index build (men + women merged) ─────────────────────────────────────────────
def test_index_merges_men_and_women():
    idx = _idx()
    assert "2026-09-16" in idx
    tickers = {t for km in idx["2026-09-16"] for t in (km.ticker_a, km.ticker_b)}
    assert tickers == {"KXITFMATCH-26SEP16WENTOS-WEN", "KXITFMATCH-26SEP16WENTOS-TOS",
                       "KXITFWMATCH-26SEP16ZELPRE-ZEL", "KXITFWMATCH-26SEP16ZELPRE-PRE"}
    assert len(idx["2026-09-16"]) == 2                     # two distinct matches (one men's, one women's)


def test_index_skips_incomplete_and_malformed():
    mk = [MEN[0],                                          # only ONE side of the WENTOS bout -> blob has !=2 sides
          {"ticker": "KXITFMATCH-26SEP16WENTOS-WEN", "title": ""},      # empty title dropped
          {"ticker": "GARBAGE-TICKER", "title": "Nobody wins"}]         # non-ITF ticker dropped
    assert _idx(mk) == {}


# ── match: happy path (men AND women, same merged index) ─────────────────────────
def test_match_mens_winner():
    # independent: outcome "Marco Tosetti" -> the TOS-code ticker (TOS subsequences 'tosetti'), YES leg
    r = _match(MEN + WOMEN, "itf-wendler-tosetti-2026-09-16", "Marco Tosetti",
               "ITF: Jefferson Wendler Filho vs Marco Tosetti")
    assert r.status == "matched" and r.kalshi_ticker == "KXITFMATCH-26SEP16WENTOS-TOS" and r.leg == "yes", r


def test_match_womens_winner_same_index():
    # the SAME merged index resolves a women's bet -> proves ONE matcher covers both series
    r = _match(MEN + WOMEN, "itf-zeltina-pree-2026-09-16", "Darja Zeltina",
               "ITF: Darja Zeltina vs Amarni Pree")
    assert r.status == "matched" and r.kalshi_ticker == "KXITFWMATCH-26SEP16ZELPRE-ZEL" and r.leg == "yes", r


def test_match_surname_recovery_with_title():
    # a SURNAME-ONLY outcome resolves ONLY because the title pins the pair (pair_pinned surname recovery)
    r = _match(WOMEN, "itf-zeltina-pree-2026-09-16", "Zeltina", "ITF: Darja Zeltina vs Amarni Pree")
    assert r.status == "matched" and r.kalshi_ticker == "KXITFWMATCH-26SEP16ZELPRE-ZEL", r


def test_match_date_window_plus_one():
    # Poly slug date -1 vs Kalshi card date -> the +/-1 window resolves it (the tennis Poly/Kalshi divergence)
    r = _match(MEN, "itf-wendler-tosetti-2026-09-15", "Marco Tosetti",
               "ITF: Jefferson Wendler Filho vs Marco Tosetti")
    assert r.status == "matched" and r.kalshi_ticker == "KXITFMATCH-26SEP16WENTOS-TOS", r


# ── ★ NAME BIND: the cs2/opponent-buy failure mode, guarded by the ticker CODE ───
def test_code_mislabel_swap_refused():
    """Kalshi MISLABELS: -WEN carries 'Marco Tosetti', -TOS carries 'Jefferson Wendler Filho'. The whale bets
    'Marco Tosetti'. Code-anchor (labels_code_swapped) sees the cross assignment scores higher -> REFUSE the
    whole market (safe miss). Guards the exact bug that made cs2 buy the OPPONENT."""
    mislabeled = [{"ticker": "KXITFMATCH-26SEP16WENTOS-WEN", "title": "Marco Tosetti wins"},
                  {"ticker": "KXITFMATCH-26SEP16WENTOS-TOS", "title": "Jefferson Wendler Filho wins"}]
    r = _match(mislabeled, "itf-wendler-tosetti-2026-09-16", "Marco Tosetti",
               "ITF: Jefferson Wendler Filho vs Marco Tosetti")
    assert not (r.status == "matched" and r.kalshi_ticker == "KXITFMATCH-26SEP16WENTOS-WEN"), \
        "bought Wendler's ticker for a Tosetti bet: %r" % (r,)
    assert r.kalshi_ticker is None


# ── ★ SAME-DAY SURNAME COLLISION -> SAFE MISS (never a pick) ──────────────────────
def test_same_day_full_name_collision_is_safe_miss():
    """Two DIFFERENT same-day matches each contain a 'Nicolas Garcia' (coincidental namesakes). Whale bets the
    full name 'Nicolas Garcia' with NO title to disambiguate -> the single-player fallback finds it in TWO
    matches -> abbrev_collision_ambiguous (a SAFE MISS, never a guess)."""
    mk = [{"ticker": "KXITFMATCH-26SEP16GARONE-GAR", "title": "Nicolas Garcia wins"},
          {"ticker": "KXITFMATCH-26SEP16GARONE-ONE", "title": "Pavel Oneshko wins"},
          {"ticker": "KXITFWMATCH-26SEP16GARTWO-GAR", "title": "Nicolas Garcia wins"},
          {"ticker": "KXITFWMATCH-26SEP16GARTWO-TWO", "title": "Lucia Twosky wins"}]
    r = _match(mk, "itf-garcia-oneshko-2026-09-16", "Nicolas Garcia", None)   # no title -> cannot pin a match
    assert r.status == "abbrev_collision_ambiguous" and r.kalshi_ticker is None, r


def test_bare_surname_without_title_is_safe_miss():
    # a surname-only outcome with NO title cannot bind (match_fighter_name needs surname+first) -> safe miss
    r = _match(MEN, "itf-wendler-tosetti-2026-09-16", "Tosetti", None)
    assert r.status != "matched" and r.kalshi_ticker is None, r


def test_within_match_same_surname_brothers_safe_miss():
    # both sides of ONE match share a surname (the Cerundolo-brothers case): pair pins the match but the surname
    # cannot pick a side -> winner_outcome_unresolved (safe miss), never a coin-flip pick
    mk = [{"ticker": "KXITFMATCH-26SEP16CERCER-CE1", "title": "Juan Cerundolo wins"},
          {"ticker": "KXITFMATCH-26SEP16CERCER-CE2", "title": "Francisco Cerundolo wins"}]
    r = _match(mk, "itf-cerundolo-cerundolo-2026-09-16", "Cerundolo",
               "ITF: Juan Cerundolo vs Francisco Cerundolo")
    assert r.status == "winner_outcome_unresolved" and r.kalshi_ticker is None, r


# ── ★ FUTURES / OUT-OF-SCOPE -> SAFE MISS ────────────────────────────────────────
def test_futures_player_not_in_any_match_is_safe_miss():
    # a dated itf- outright whose outcome player is NOT in any same-day KXITF match -> winner_outcome_unresolved
    r = _match(MEN, "itf-madrid-w75-2026-09-16", "Rafael Outright", None)
    assert r.status == "winner_outcome_unresolved" and r.kalshi_ticker is None, r


def test_out_of_window():
    r = _match(MEN, "itf-wendler-tosetti-2020-01-01", "Marco Tosetti",
               "ITF: Jefferson Wendler Filho vs Marco Tosetti")
    assert r.status == "out_of_window", r


# ── ★ INERT: itf_moneyline token gate (NOT in the legacy default) ─────────────────
def test_inert_itf_moneyline_not_in_legacy_default():
    # the legacy default a blank/NULL market_types resolves to does NOT include itf_moneyline -> ITF stays OFF
    idx = _idx(MEN)
    p = ITF.parse_poly_itf_bet("itf-wendler-tosetti-2026-09-16", "Marco Tosetti",
                               "ITF: Jefferson Wendler Filho vs Marco Tosetti")
    r = ITF.match_bet(p, idx, _dates(idx), allowed_market_types=("moneyline", "total", "spread"))
    assert r.status == "skip_market_type_excluded", r


def test_enabled_when_token_present():
    idx = _idx(MEN)
    p = ITF.parse_poly_itf_bet("itf-wendler-tosetti-2026-09-16", "Marco Tosetti",
                               "ITF: Jefferson Wendler Filho vs Marco Tosetti")
    r = ITF.match_bet(p, idx, _dates(idx), allowed_market_types=("itf_moneyline",))
    assert r.status == "matched"


# ══════════════════════════════════════════════════════════════════════════════════════════
# ★ ROUTE-ONLY SEPARATION FROM atp/wta -- BY CONSTRUCTION (fails if the anchors were shared)
# ══════════════════════════════════════════════════════════════════════════════════════════
def test_itf_ticker_cannot_enter_the_tennis_index():
    # feed ITF tickers to the ATP/WTA index builder -> DROPPED (its regex is anchored to KX(ATP|WTA)MATCH).
    # This FAILS if the tennis _K_RE were widened to admit KXITF* -> proves the ticker separation by construction.
    assert TEN.build_kalshi_match_index(MEN + WOMEN) == {}


def test_atp_wta_ticker_cannot_enter_the_itf_index():
    # feed ATP + WTA tickers to the ITF index builder -> DROPPED (anchored to KX(ITFMATCH|ITFWMATCH)).
    atp_wta = [{"ticker": "KXATPMATCH-26SEP04MEDRIN-MED", "title": "Daniil Medvedev wins"},
               {"ticker": "KXATPMATCH-26SEP04MEDRIN-RIN", "title": "Arthur Rinderknech wins"},
               {"ticker": "KXWTAMATCH-26SEP04SWIGAU-SWI", "title": "Iga Swiatek wins"},
               {"ticker": "KXWTAMATCH-26SEP04SWIGAU-GAU", "title": "Coco Gauff wins"}]
    assert ITF.build_kalshi_itf_index(atp_wta) == {}


def test_itf_slug_is_non_tennis_to_the_tennis_matcher():
    # the atp/wta matcher REJECTS an itf- slug at parse (slug_not_tennis) -> an ITF bet can never bind an atp/wta market
    p = TEN.parse_poly_tennis_bet("itf-wendler-tosetti-2026-09-16", "Marco Tosetti",
                                  "ITF: Jefferson Wendler Filho vs Marco Tosetti")
    assert p.market_type == "non_tennis"


def test_atp_slug_is_non_itf_to_the_itf_matcher():
    # the ITF matcher REJECTS an atp- slug at parse (slug_not_itf) -> an atp/wta bet can never bind an ITF market
    p = ITF.parse_poly_itf_bet("atp-med-rin-2026-09-04", "Daniil Medvedev",
                               "ATP: Daniil Medvedev vs Arthur Rinderknech")
    assert p.market_type == "non_itf"


# ══════════════════════════════════════════════════════════════════════════════════════════
# ★ Q5: END-TO-END through the DEPLOYED evaluate() + MATCHER_ADAPTERS['itf'] (not a scratch copy)
# ══════════════════════════════════════════════════════════════════════════════════════════
def test_deployed_evaluate_e2e_itf():
    """One ITF signal through the REAL execution.evaluate() gate stack + MATCHER_ADAPTERS['itf'] dispatch + the
    ctx.itf_index slot, asserting a dry_run_would_place with the independently-expected ticker/leg. The
    itf_moneyline token is enabled here (it is NOT in the legacy default -> INERT until Jack sets it)."""
    from trading_corp.prediction_markets import execution as E
    from trading_corp.prediction_markets import db as DB
    idx = _idx(MEN)
    ticker = "KXITFMATCH-26SEP16WENTOS-TOS"               # independent: outcome "Marco Tosetti" -> TOS-code ticker, YES leg
    ctx = E.MarketContext({}, {}, {}, _dates(idx), {ticker: {
        "yes_ask_dollars": 0.20, "no_ask_dollars": 0.82, "yes_bid_dollars": 0.18, "no_bid_dollars": 0.80,
        "liquidity_dollars": 0.0, "yes_ask_size_fp": 1000.0, "yes_bid_size_fp": 1000.0, "exchange_index": 3}},
        itf_index=idx)
    sub = E.SubConfig(account_id="kalshi_test", category="itf", market_types=("itf_moneyline",),
                      sizing_mode="contracts", fixed_stake_usd=0.0, per_order_usd_cap=100.0, daily_usd_cap=100.0,
                      max_open_usd=100.0, max_orders_per_day=10, max_slippage_cents=5, liquidity_ratio=0.75, contracts=1)
    sig = E.CopySignal(wallet="0xfb07f48542", slug="itf-wendler-tosetti-2026-09-16", outcome="Marco Tosetti",
                       condition_id="0xcond", outcome_index=0, signal_id="sigitfa", is_exit=False,
                       title="ITF: Jefferson Wendler Filho vs Marco Tosetti")
    d = tempfile.mkdtemp(); path = os.path.join(d, "pm.db"); DB.init_db(path)
    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    jrnl = E.Journal(conn, ["kalshi_test"], 1_800_000_000)
    dec = E.evaluate(sig, sub, ctx, jrnl, conn, 1_800_000_000, shard_balances=None, venue_exposure=None,
                     legacy_db_path=os.path.join(d, "nonexistent_legacy.db"))
    conn.close()
    assert dec.status == "dry_run_would_place", dec.status
    assert dec.kalshi_ticker == ticker and dec.leg == "yes"
    assert (dec.body or {}).get("ticker") == ticker


def test_deployed_dispatch_atp_adapter_skips_itf_signal():
    """Route an ITF signal through the atp ADAPTER (MATCHER_ADAPTERS['atp']) against an ITF ctx -> it must SKIP
    (non_tennis), never match. Proves the adapter-level separation: even mis-routed, the atp matcher cannot bind
    an ITF bet. FAILS by construction if atp/itf shared a parser."""
    from trading_corp.prediction_markets import execution as E
    idx = _idx(MEN)
    ctx = E.MarketContext({}, {}, {}, _dates(idx), {}, itf_index=idx)   # note: match_index is None here (atp reads it)
    parse_atp, match_atp = E.MATCHER_ADAPTERS["atp"]
    parsed = parse_atp("itf-wendler-tosetti-2026-09-16", "Marco Tosetti",
                       "ITF: Jefferson Wendler Filho vs Marco Tosetti")
    r = match_atp(parsed, ctx, ("moneyline",))
    assert r.status == "skip_non_tennis" and r.kalshi_ticker is None, r
