"""Gate C -- the MAXIMUM-CHASE rule (2026-11-17): never pay materially more than the whale did for the SAME claim.

    chase = (leg ask + per-contract taker fee) - whale_avg_price
    skip when chase > max_chase_cents (absolute) or > chase_upside_pct% of (1 - whale price) (relative).

THE CONVERSION OBLIGATION (same claim, same scale). The whale's price is for its HELD Polymarket outcome TOKEN;
our price is for the Kalshi (ticker, leg) the matcher chose. Two facts make the comparison valid with NO extra
transform, and this file PROVES both per matcher family:
  (1) v2 `avg_price` is HELD-TOKEN basis -- an index-1 "No"/"Under" row carries THAT token's price, not the
      yes-side price. Proven from a REAL captured payload's own arithmetic (test_avg_price_is_held_token_basis):
      percent_realized_pnl == (1 - avg_price)/avg_price for a winning "No" holder.
  (2) every matcher is CLAIM-FAITHFUL -- the whale's held-token claim binds a (ticker, leg) carrying the SAME
      claim, or safe-misses (the swap-regression suites); so the complement handling lives ENTIRELY on our side:
      a no-leg order is priced at the NO ask (execution._leg_ask), already the cost of the whale's claim.
Per the independence rule (Jack's): every expected (ticker, leg) below is derived from the Kalshi ticker's OWN
-CODE / title / yes_sub_title + the whale-buys-YES-on-the-named-side convention -- NEVER from the matcher's
output -- and every expected chase number is a HAND-COMPUTED literal from the payload numbers, never read back
from the code under test.

Fee model literals: fee = mult * 0.07 * p * (1-p) per contract; mult 0.50 for KXMLBGAME/KXMLBSPREAD/KXMLBTOTAL,
1.00 otherwise (Kalshi fee schedule, harvested 2026-10-17; re-verified against 3,472/3,681 actual fill fees).
"""
import asyncio
import logging
import os
import sqlite3
import tempfile

from trading_corp.prediction_markets import db as DB
from trading_corp.prediction_markets import execution as E
from trading_corp.prediction_markets import heartbeat as HB
from trading_corp.prediction_markets import live_driver as LD
from trading_corp.data.polymarket_data_api_client import PositionRow


# ══════════════════════════════════════════════════════════════════════════════════════════
# REAL captured v2 /positions payloads (prod box 2026-11-17, probes analysis/pmh8.sh +
# analysis/pv2_probe2.sh; copied verbatim from tests/test_polymarket_data_api_v2_migration.py).
# ══════════════════════════════════════════════════════════════════════════════════════════
V2_POS_OPEN_ITF = {
    "proxy_wallet": "0x746295855be6f49fe2da2accd69ea34b3cf23ac3",
    "token_id": "65871345570745679802811495716260998674370065504411551893093447824806706299043",
    "condition_id": "0x40f68115e597ab92f45c1adabbfa42bcf877a376feda37804af21ad79a6d6666",
    "current_size": 1742.0, "avg_price": 0.6, "entry_cost_usdc": 1045.2,
    "entry_fees_usdc": 20.904, "total_cost_usdc": 1066.104,
    "current_price": 0.9995, "current_value": 1741.129, "total_size": 1742.0,
    "realized_pnl": 0.0, "unrealized_pnl": 695.929, "total_pnl": 695.929,
    "percent_pnl": 66.5833, "percent_realized_pnl": 66.5833,
    "status": "OPEN", "redeemable": False, "mergeable": False,
    "negative_risk": False, "archived": False,
    "title": "W75 Sydney: Aoi Ito vs Kristiana Sidorova",
    "slug": "itf-ito1-sidoro1-2026-11-17",
    "event_id": "1400927", "event_slug": "itf-ito1-sidoro1-2026-11-17",
    "outcome": "Aoi Ito", "outcome_index": 0,
    "opposite_outcome": "Kristiana Sidorova",
    "end_date": "2026-11-17", "last_event_at": 1794886306,
    "first_entry_at": 1794884528, "name": "0x746...-1762436472554",
    "profile_image": "", "verified": False,
}
V2_POS_OPEN_UNDER = {
    "proxy_wallet": "0x21f333307ac6d2e65ac82f014b3dd2b64cbc35e3",
    "token_id": "20898142226554455622276391916568315719199832683600817907211912188944674134387",
    "condition_id": "0x51e7cd917fffae7e290f691042eaba4110f0c394cb3c560a628295a1cc4de85a",
    "current_size": 25613.3443, "avg_price": 0.5405, "entry_cost_usdc": 13846.3178,
    "entry_fees_usdc": 0.0, "total_cost_usdc": 13846.3178,
    "current_price": 0.505, "current_value": 12934.7388, "total_size": 25613.3443,
    "realized_pnl": 0.0, "unrealized_pnl": -911.579, "total_pnl": -911.579,
    "percent_pnl": -6.5835, "percent_realized_pnl": -6.5835,
    "status": "OPEN", "redeemable": False, "mergeable": False,
    "negative_risk": False, "archived": False,
    "title": "Pepperdine Waves vs. Oregon Ducks: O/U 156.5",
    "slug": "cbb-pepp-ore-2026-11-15-total-156pt5",
    "event_id": "1344915", "event_slug": "cbb-pepp-ore-2026-11-15",
    "outcome": "Under", "outcome_index": 1, "opposite_outcome": "Over",
    "opposite_token_id": "4557835200044398284775444978100598473242327216682455237474734118387176041928",
    "end_date": "2026-11-15", "last_event_at": 1794769463,
    "first_entry_at": 1794760652, "name": "hi774c",
    "profile_image": "", "verified": False,
}
V2_POS_CLOSED_WIN = {
    "proxy_wallet": "0x21f333307ac6d2e65ac82f014b3dd2b64cbc35e3",
    "token_id": "89400087379560118244676285293515988828989065128224151976744339630651750394158",
    "condition_id": "0x3ceb5b631318be5000758200590efe2b308a581d6787c3e7bc050bfd8f627627",
    "current_size": 0.0, "avg_price": 0.3416, "entry_cost_usdc": 0.0,
    "entry_fees_usdc": 0.0, "total_cost_usdc": 0.0,
    "current_price": 1.0, "current_value": 0.0, "total_size": 133299.2984,
    "realized_pnl": 87752.7943, "unrealized_pnl": 0.0, "total_pnl": 87752.7943,
    "percent_pnl": 0.0, "percent_realized_pnl": 192.6663,
    "status": "CLOSED", "redeemable": False, "mergeable": False,
    "negative_risk": True, "archived": False,
    "title": "Will Belgium win on 2026-06-15?",
    "slug": "fifwc-bel-egy-2026-06-15-bel",
    "event_id": "351728", "event_slug": "fifwc-bel-egy-2026-06-15",
    "outcome": "No", "outcome_index": 1, "opposite_outcome": "Yes",
    "opposite_token_id": "5699266164849713169703648234886411053444418385408086203990916861286572010447",
    "end_date": "2026-06-15", "last_event_at": 1781574820,
    "first_entry_at": 1781498086, "name": "hi774c",
    "profile_image": "", "verified": False,
}


# ══════════════════════════════════════════════════════════════════════════════════════════
# 0. the held-token-basis proof + the signal carry (real payloads, no matcher, no gate)
# ══════════════════════════════════════════════════════════════════════════════════════════
def test_avg_price_is_held_token_basis():
    """INDEPENDENT proof from the payload's OWN arithmetic: the holder of the index-1 "No" token WON
    (current_price 1.0) and the API's percent_realized_pnl is 192.6663 ~= (1 - 0.3416)/0.3416 * 100 = 192.74
    (avg_price is served ROUNDED to 4dp while the percent is computed from the unrounded average -- the implied
    unrounded avg is 0.34170; the identity holds to the rounding, |diff| < 0.2). Were avg_price yes-side (the
    "Yes" token cost ~0.6584), the return would print ~51.9% -- over 140 points away. So v2 avg_price prices
    the HELD token -- the whale's actual claim."""
    avg = V2_POS_CLOSED_WIN["avg_price"]
    assert abs((1.0 - avg) / avg * 100.0 - V2_POS_CLOSED_WIN["percent_realized_pnl"]) < 0.2
    assert abs((1.0 - 0.6584) / 0.6584 * 100.0 - V2_POS_CLOSED_WIN["percent_realized_pnl"]) > 100.0


def test_signal_carries_whale_price_and_entry_ts_from_real_rows():
    rows = [PositionRow.from_api(V2_POS_OPEN_ITF), PositionRow.from_api(V2_POS_OPEN_UNDER)]
    sigs = LD.positions_to_entry_signals(rows, "0xwallet")
    by_cid = {s.condition_id: s for s in sigs}
    itf = by_cid[V2_POS_OPEN_ITF["condition_id"]]
    under = by_cid[V2_POS_OPEN_UNDER["condition_id"]]
    assert itf.whale_avg_price == 0.6 and itf.whale_first_entry_ts == 1794884528
    # the index-1 "Under" row carries ITS OWN token's price -- not 1 - 0.5405
    assert under.whale_avg_price == 0.5405 and under.whale_first_entry_ts == 1794760652


def test_signal_carry_junk_and_zero_avg_price_become_none():
    # the client coerces a MISSING avg_price to 0.0 -- never a real entry price -> None (gate C fails closed)
    for bad_avg in (0.0, 1.0, -0.2, "junk", None):
        raw = dict(V2_POS_OPEN_ITF, avg_price=bad_avg)
        sigs = LD.positions_to_entry_signals([PositionRow.from_api(raw)], "0xw")
        assert len(sigs) == 1 and sigs[0].whale_avg_price is None, bad_avg
    raw = dict(V2_POS_OPEN_ITF, first_entry_at="garbage")
    sigs = LD.positions_to_entry_signals([PositionRow.from_api(raw)], "0xw")
    assert sigs[0].whale_first_entry_ts is None and sigs[0].whale_avg_price == 0.6


def test_fee_per_contract_literals():
    # KXITFWMATCH @0.90: 0.07*0.90*0.10 = 0.0063 (mult 1.0)
    assert abs(E.kalshi_taker_fee_per_contract("KXITFWMATCH-26NOV17COLSCH-COL", 0.90) - 0.0063) < 1e-12
    # KXMLBGAME @0.60: 0.5*0.07*0.60*0.40 = 0.0084 -- matches the ACTUAL per-contract fee on the 2026-08-30
    # KXMLBGAME-26AUG301920CINCHC-CHC fill (phi 0.0084, backtest entries_enriched row 1)
    assert abs(E.kalshi_taker_fee_per_contract("KXMLBGAME-26AUG301920CINCHC-CHC", 0.60) - 0.0084) < 1e-12
    # unknown series -> mult 1.0 (conservative-HIGH: overstates chase, blocks more)
    assert abs(E.kalshi_taker_fee_per_contract("KXNEWSERIES-XX-Y", 0.50) - 0.0175) < 1e-12
    assert E.kalshi_taker_fee_per_contract("KXITFMATCH-X-Y", "junk") == 0.0
    assert E.kalshi_taker_fee_per_contract("KXITFMATCH-X-Y", 0.0) == 0.0


# ══════════════════════════════════════════════════════════════════════════════════════════
# the e2e harness: one signal through the REAL execution.evaluate() (gates 1..8 + C)
# ══════════════════════════════════════════════════════════════════════════════════════════
def _mk(yes_ask, yes_bid, shard=0):
    return {"yes_ask_dollars": yes_ask, "yes_bid_dollars": yes_bid,
            "no_ask_dollars": round(1.0 - yes_bid, 4), "no_bid_dollars": round(1.0 - yes_ask, 4),
            "liquidity_dollars": 0.0, "yes_ask_size_fp": 1000.0, "yes_bid_size_fp": 1000.0,
            "exchange_index": shard}


def _sub(category, market_types, **over):
    kw = dict(account_id="kalshi_test", category=category, market_types=market_types,
              sizing_mode="contracts", fixed_stake_usd=0.0, per_order_usd_cap=100.0, daily_usd_cap=100.0,
              max_open_usd=100.0, max_orders_per_day=10, max_slippage_cents=2, liquidity_ratio=0.75, contracts=1)
    kw.update(over)
    return E.SubConfig(**kw)


def _eval(sig, sub, ctx):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "pm.db")
    DB.init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    jrnl = E.Journal(conn, [sub.account_id], 1_800_000_000)
    dec = E.evaluate(sig, sub, ctx, jrnl, conn, 1_800_000_000, shard_balances=None, venue_exposure=None,
                     legacy_db_path=os.path.join(d, "no_legacy.db"))
    conn.close()
    return dec, jrnl


def _sig(slug, outcome, title, oidx, whale=None, sid="sigchase", wallet="0xabc"):
    return E.CopySignal(wallet=wallet, slug=slug, outcome=outcome, condition_id="0xcondchase", outcome_index=oidx,
                        signal_id=sid, is_exit=False, title=title, whale_avg_price=whale)


def _blocked_then_kept(mk_sig, sub, ctx, ticker, leg, whale_blocked, whale_kept, chase_blocked, chase_kept):
    """Run the SAME market twice: a whale price that must BLOCK (chase > 2c) and one that must KEEP.
    chase_* are the hand-computed literals; the blocked reason must carry them to 4dp."""
    dec, _ = _eval(mk_sig(whale_blocked), sub, ctx)
    assert dec.status == "skip:chase", (dec.status, dec.reason)
    assert dec.kalshi_ticker == ticker and dec.leg == leg
    assert ("chase_%.4f" % chase_blocked) in dec.reason and "gt_abs_cap_2.0c" in dec.reason, dec.reason
    dec2, _ = _eval(mk_sig(whale_kept), sub, ctx)
    assert dec2.status == "dry_run_would_place", (dec2.status, dec2.reason)
    assert dec2.kalshi_ticker == ticker and dec2.leg == leg
    # sanity: the kept chase literal is inside the 2c default
    assert chase_kept <= 0.02 + 1e-9


# ── 1. MLB moneyline (2-way) ─ whale holds "San Diego" -> the ticker whose OWN code is -SD, YES leg.
#      ask 0.60, mult 0.5 -> fee 0.0084. blocked: whale 0.52 -> chase 0.60+0.0084-0.52 = 0.0884.
#      kept: whale 0.60 -> chase 0.0084.
def test_chase_mlb_moneyline_2way():
    from trading_corp.data import mlb_poly_kalshi_match as M
    stem = "26SEP222210SDLAD"
    tk = "KXMLBGAME-%s-SD" % stem
    gi = M.build_kalshi_game_index([tk, "KXMLBGAME-%s-LAD" % stem])
    dates = frozenset(["2026-09-22", tk, "KXMLBGAME-%s-LAD" % stem])
    ctx = E.MarketContext(gi, {}, {}, dates, {tk: _mk(0.60, 0.58)})
    sub = _sub("mlb", ("moneyline", "total", "spread"))

    def mk_sig(w):
        return _sig("mlb-sd-lad-2026-09-22", "San Diego", "Padres vs. Dodgers", 0, whale=w)
    _blocked_then_kept(mk_sig, sub, ctx, tk, "yes", 0.52, 0.60, 0.0884, 0.0084)


# ── 2. soccer 3-way with tie (mls) ─ COMPLEMENT leg: whale holds the "No" TOKEN of "Will Los Angeles F win?"
#      (3-way: No = draw OR opponent). Matcher -> the -LAFC ticker, NO leg -- the SAME claim ("LAFC does not
#      win", the TIE market exists separately). OUR cost is the NO ask = 0.46 (NOT the yes-side price: that IS
#      the conversion). fee 0.07*0.46*0.54 = 0.0173880. blocked: whale-No-avg 0.40 -> chase 0.0773880.
#      kept: whale 0.46 -> chase 0.0173880.
def test_chase_soccer_3way_win_no_complement_leg():
    from trading_corp.data import soccer_poly_kalshi_match as SOC
    cfg = SOC.LEAGUES["mls"]
    tk = "KXMLSGAME-26SEP09LAFCNYRB-LAFC"
    markets = [{"ticker": tk, "yes_sub_title": "Los Angeles F"},
               {"ticker": "KXMLSGAME-26SEP09LAFCNYRB-NYRB", "yes_sub_title": "New York RB"},
               {"ticker": "KXMLSGAME-26SEP09LAFCNYRB-TIE", "yes_sub_title": "Tie"}]
    idx = SOC.build_game_index(markets, cfg)
    ctx = E.MarketContext({}, {}, {}, frozenset(idx.keys()), {tk: _mk(0.57, 0.54)}, soccer_index=idx)
    sub = _sub("mls", ("moneyline",))

    def mk_sig(w):
        return _sig("mls-laf-nyr-2026-09-09-laf", "No", "Will Los Angeles F win on 2026-09-09?", 1, whale=w)
    _blocked_then_kept(mk_sig, sub, ctx, tk, "no", 0.40, 0.46, 0.0773880, 0.0173880)


# ── 3. NFL spread ─ whale holds "Seahawks" on the home -6.5 spread -> KXNFLSPREAD ...-SEA7 (strike N-0.5),
#      YES leg. ask 0.55, mult 1.0 (KXNFLSPREAD is NOT an MLB series) -> fee 0.07*0.55*0.45 = 0.0173250.
#      blocked: whale 0.50 -> chase 0.0673250. kept: whale 0.55 -> chase 0.0173250.
def test_chase_nfl_spread():
    from trading_corp.data import sports_structural_match as SS
    cfg = SS.LEAGUES["nfl"]
    tk = "KXNFLSPREAD-26SEP09NESEA-SEA7"
    gi = SS.build_game_index(["KXNFLGAME-26SEP09NESEA-NE", "KXNFLGAME-26SEP09NESEA-SEA"], cfg)
    si = SS.build_spread_index([tk], cfg)
    ctx = E.MarketContext({}, {}, si, frozenset(k[0] for k in gi.keys()), {tk: _mk(0.55, 0.53)},
                          structural_index=gi)
    sub = _sub("nfl", ("moneyline", "total", "spread"))

    def mk_sig(w):
        return _sig("nfl-ne-sea-2026-09-10-spread-home-6pt5", "Seahawks", "Patriots vs. Seahawks", 0, whale=w)
    _blocked_then_kept(mk_sig, sub, ctx, tk, "yes", 0.50, 0.55, 0.0673250, 0.0173250)


# ── 4. NFL total, UNDER ─ COMPLEMENT leg: whale holds the "Under" TOKEN at line 49.5 -> KXNFLTOTAL ...-50
#      (strike N-0.5), NO leg -- same claim ("49 or fewer"). OUR cost = NO ask 0.48. fee 0.07*0.48*0.52 =
#      0.0174720. blocked: whale-Under-avg 0.44 -> chase 0.0574720. kept: whale 0.48 -> chase 0.0174720.
#      (The REAL index-1 Under payload above proves the token basis the whale side of this comparison uses.)
def test_chase_nfl_total_under_complement_leg():
    from trading_corp.data import sports_structural_match as SS
    cfg = SS.LEAGUES["nfl"]
    tk = "KXNFLTOTAL-26SEP13NODET-50"
    gi = SS.build_game_index(["KXNFLGAME-26SEP13NODET-NO", "KXNFLGAME-26SEP13NODET-DET"], cfg)
    ti = SS.build_total_index([tk], cfg)
    ctx = E.MarketContext({}, ti, {}, frozenset(k[0] for k in gi.keys()), {tk: _mk(0.54, 0.52)},
                          structural_index=gi)
    sub = _sub("nfl", ("moneyline", "total", "spread"))

    def mk_sig(w):
        return _sig("nfl-no-det-2026-09-13-total-49pt5", "Under", "Saints vs. Lions: O/U 49.5", 1, whale=w)
    _blocked_then_kept(mk_sig, sub, ctx, tk, "no", 0.44, 0.48, 0.0574720, 0.0174720)


# ── 5. ITF ─ the LIVE first-hour replay numbers: ask 0.88, whale avg 0.7809 (the 2026-11-17 pre-deploy
#      simulation's KXITFWMATCH-26NOV17COLSCH-COL copy: limit 0.90 = ask 0.88 + 2c slip, whale 0.7809).
#      fee 0.07*0.88*0.12 = 0.0073920 -> chase 0.88+0.0073920-0.7809 = 0.1064920: BLOCKED at the 2c default
#      (and would be at ANY swept threshold <= 10c). kept control: whale 0.875 -> chase 0.0123920.
#      Fixture pair (WENTOS, test_itf_match.py provenance); the claim/leg logic is identical.
def test_chase_itf_live_first_hour_replay():
    from trading_corp.data import itf_poly_kalshi_match as ITF
    tk = "KXITFMATCH-26SEP16WENTOS-TOS"
    idx = ITF.build_kalshi_itf_index([
        {"ticker": "KXITFMATCH-26SEP16WENTOS-WEN", "title": "Jefferson Wendler Filho wins"},
        {"ticker": tk, "title": "Marco Tosetti wins"}])
    ctx = E.MarketContext({}, {}, {}, frozenset(idx.keys()), {tk: _mk(0.88, 0.86)}, itf_index=idx)
    sub = _sub("itf", ("itf_moneyline",))

    def mk_sig(w):
        return _sig("itf-wendler-tosetti-2026-09-16", "Marco Tosetti",
                    "ITF: Jefferson Wendler Filho vs Marco Tosetti", 0, whale=w)
    _blocked_then_kept(mk_sig, sub, ctx, tk, "yes", 0.7809, 0.875, 0.1064920, 0.0123920)


# ── 6. CS2 ─ whale "magic" -> the -MGC ticker (yes_sub_title "magic"), YES leg. ask 0.35 ->
#      fee 0.07*0.35*0.65 = 0.0159250. blocked: whale 0.30 -> chase 0.0659250. kept: whale 0.35 -> 0.0159250.
def test_chase_cs2():
    from trading_corp.data import cs2_poly_kalshi_match as CS2
    tk = "KXCS2GAME-26SEP082300FAZEMGC-MGC"
    idx = CS2.build_kalshi_match_index([
        {"ticker": "KXCS2GAME-26SEP082300FAZEMGC-FAZE", "yes_sub_title": "FaZe"},
        {"ticker": tk, "yes_sub_title": "magic"}])
    ctx = E.MarketContext({}, {}, {}, frozenset(idx.keys()), {tk: _mk(0.35, 0.33)}, cs2_index=idx)
    sub = _sub("cs2", ("moneyline",))

    def mk_sig(w):
        return _sig("cs2-mgc-faze-2026-09-08", "magic",
                    "Counter-Strike: magic vs FaZe (BO3) - FISSURE PLAYGROUND Group B", 0, whale=w)
    _blocked_then_kept(mk_sig, sub, ctx, tk, "yes", 0.30, 0.35, 0.0659250, 0.0159250)


# ── 7. UFC ─ whale "Salahdine Parnasse" -> the -PAR ticker, YES leg. ask 0.62 ->
#      fee 0.07*0.62*0.38 = 0.0164920. blocked: whale 0.55 -> chase 0.0864920. kept: whale 0.62 -> 0.0164920.
def test_chase_ufc():
    from trading_corp.data import ufc_poly_kalshi_match as UFC
    tk = "KXUFCFIGHT-26SEP05HOOPAR-PAR"
    idx = UFC.build_kalshi_fight_index([
        {"ticker": "KXUFCFIGHT-26SEP05HOOPAR-HOO", "title": "Daniel Hooker wins"},
        {"ticker": tk, "title": "Salahdine Parnasse wins"}])
    ctx = E.MarketContext({}, {}, {}, frozenset(k[0] for k in idx.keys()), {tk: _mk(0.62, 0.60)},
                          fight_index=idx)
    sub = _sub("ufc", ("moneyline",))

    def mk_sig(w):
        return _sig("ufc-dan6-salpar-2026-09-05", "Salahdine Parnasse",
                    "UFC Fight Night: Daniel Hooker vs. Salahdine Parnasse", 1, whale=w)
    _blocked_then_kept(mk_sig, sub, ctx, tk, "yes", 0.55, 0.62, 0.0864920, 0.0164920)


# ── 8. boxing ─ whale "Garcia" -> the ticker whose OWN code is GARCIA, YES leg. ask 0.50 ->
#      fee 0.07*0.50*0.50 = 0.0175. blocked: whale 0.43 -> chase 0.0875. kept: whale 0.52 -> chase -0.0025
#      (paying LESS than the whale: negative chase always passes).
def test_chase_boxing():
    from trading_corp.data import boxing_poly_kalshi_match as BX
    tk = "KXBOXING-26SEP12GARCIAMORALE-GARCIA"
    idx = BX.build_kalshi_boxing_index([
        {"ticker": tk, "title": "Sean Garcia wins", "yes_sub_title": "Sean Garcia"},
        {"ticker": "KXBOXING-26SEP12GARCIAMORALE-MORALE", "title": "Abraham Morales wins",
         "yes_sub_title": "Abraham Morales"}])
    ctx = E.MarketContext({}, {}, {}, frozenset(k[0] for k in idx), {tk: _mk(0.50, 0.48)}, boxing_index=idx)
    sub = _sub("boxing", ("moneyline",))

    def mk_sig(w):
        return _sig("zuffa-garci1-moral1-2026-09-12", "Garcia", "Sean Garcia vs. Abraham Morales", 0, whale=w)
    _blocked_then_kept(mk_sig, sub, ctx, tk, "yes", 0.43, 0.52, 0.0875, -0.0025)


# ── 9. F1 ─ COMPLEMENT leg: whale holds the "No" TOKEN of "Will Max Verstappen win ...?" -> the -VER ticker,
#      NO leg (same claim: "Verstappen does not win"). OUR cost = NO ask 0.25 (1 - yes_bid 0.75).
#      fee 0.07*0.25*0.75 = 0.0131250. blocked: whale-No-avg 0.20 -> chase 0.0631250. kept: whale 0.25 -> 0.0131250.
def test_chase_f1_no_token_complement_leg():
    from trading_corp.data import f1_poly_kalshi_match as F1X
    tk = "KXF1RACE-ITAGP26-VER"
    idx = F1X.build_kalshi_f1_index([
        {"ticker": tk, "yes_sub_title": "Max Verstappen", "close_date_iso": "2026-09-06"},
        {"ticker": "KXF1RACE-ITAGP26-GAS", "yes_sub_title": "Pierre Gasly", "close_date_iso": "2026-09-06"}])
    ctx = E.MarketContext({}, {}, {}, frozenset(idx.keys()), {tk: _mk(0.78, 0.75)}, f1_race_index=idx)
    sub = _sub("f1", ("race_winner",))

    def mk_sig(w):
        return _sig("f1-italian-grand-prix-winner-verstappen-2026-09-06", "No",
                    "Will Max Verstappen win the 2026 F1 Italian Grand Prix?", 1, whale=w)
    _blocked_then_kept(mk_sig, sub, ctx, tk, "no", 0.20, 0.25, 0.0631250, 0.0131250)


# ── 10. RFI (first-inning run) ─ COMPLEMENT leg: whale holds the "No" TOKEN ("no run in the 1st") under the
#      AFFIRMATIVE title -> KXMLBRFI stem ticker, NO leg (same claim). OUR cost = NO ask 0.57 (1 - yes_bid 0.43).
#      KXMLBRFI is NOT in the fee-mult map -> mult 1.0 (conservative-high). fee 0.07*0.57*0.43 = 0.0171570.
#      blocked: whale 0.50 -> chase 0.0871570. kept: whale 0.57 -> 0.0171570.
def test_chase_rfi_no_leg():
    from trading_corp.data import mlb_poly_kalshi_match as M
    tk = "KXMLBRFI-26SEP141945SFSTL"
    gi = M.build_kalshi_game_index(["KXMLBGAME-26SEP141945SFSTL-SF", "KXMLBGAME-26SEP141945SFSTL-STL"])
    rfi = M.build_kalshi_rfi_index([tk])
    dates = frozenset(["KXMLBGAME-26SEP141945SFSTL-SF", "KXMLBGAME-26SEP141945SFSTL-STL"])
    ctx = E.MarketContext(gi, {}, {}, dates, {tk: _mk(0.45, 0.43)}, rfi_index=rfi)
    sub = _sub("mlb", ("first_inning_run",))

    def mk_sig(w):
        return _sig("mlb-sf-stl-2026-09-14-nrfi", "No",
                    "Will there be a run scored in the first inning?: San Francisco Giants vs. St. Louis "
                    "Cardinals", 1, whale=w)
    _blocked_then_kept(mk_sig, sub, ctx, tk, "no", 0.50, 0.57, 0.0871570, 0.0171570)


# ══════════════════════════════════════════════════════════════════════════════════════════
# gate semantics: fail-closed, forms, budget, exits, config
# ══════════════════════════════════════════════════════════════════════════════════════════
def _itf_ctx_and_sub(yes_ask=0.88, yes_bid=0.86, **sub_over):
    from trading_corp.data import itf_poly_kalshi_match as ITF
    tk = "KXITFMATCH-26SEP16WENTOS-TOS"
    idx = ITF.build_kalshi_itf_index([
        {"ticker": "KXITFMATCH-26SEP16WENTOS-WEN", "title": "Jefferson Wendler Filho wins"},
        {"ticker": tk, "title": "Marco Tosetti wins"}])
    ctx = E.MarketContext({}, {}, {}, frozenset(idx.keys()), {tk: _mk(yes_ask, yes_bid)}, itf_index=idx)
    return tk, ctx, _sub("itf", ("itf_moneyline",), **sub_over)


def _itf_sig(whale=None, **kw):
    return _sig("itf-wendler-tosetti-2026-09-16", "Marco Tosetti",
                "ITF: Jefferson Wendler Filho vs Marco Tosetti", 0, whale=whale, **kw)


def test_missing_whale_price_fails_closed_loudly():
    tk, ctx, sub = _itf_ctx_and_sub()
    dec, _ = _eval(_itf_sig(whale=None), sub, ctx)
    assert dec.status == "skip:chase_unpriced", dec.status
    assert dec.kalshi_ticker == tk and "fail_closed" in dec.reason


def test_gate_inert_when_both_forms_disabled():
    # both thresholds <= 0 -> the gate is OFF: a 30c chase AND a missing whale price both pass through
    _, ctx, sub = _itf_ctx_and_sub(max_chase_cents=0.0, chase_upside_pct=0.0)
    dec, _ = _eval(_itf_sig(whale=0.58), sub, ctx)      # chase = 0.88+0.0073920-0.58 = 0.3073920
    assert dec.status == "dry_run_would_place", dec.status
    dec2, _ = _eval(_itf_sig(whale=None), sub, ctx)
    assert dec2.status == "dry_run_would_place", dec2.status


def test_relative_form_alone():
    # rel 50% of upside, abs OFF. whale 0.50 -> upside 0.50, cap 0.25.
    # ask 0.80: fee 0.07*0.80*0.20 = 0.0112 -> chase 0.3112 > 0.25 -> blocked, reason names the % form.
    _, ctx, sub = _itf_ctx_and_sub(yes_ask=0.80, yes_bid=0.78, max_chase_cents=0.0, chase_upside_pct=50.0)
    dec, _ = _eval(_itf_sig(whale=0.50), sub, ctx)
    assert dec.status == "skip:chase" and "gt_50%_of_upside_0.5000" in dec.reason, (dec.status, dec.reason)
    # ask 0.70: fee 0.07*0.70*0.30 = 0.0147 -> chase 0.2147 <= 0.25 -> kept
    _, ctx2, _ = _itf_ctx_and_sub(yes_ask=0.70, yes_bid=0.68)
    dec2, _ = _eval(_itf_sig(whale=0.50), sub, ctx2)
    assert dec2.status == "dry_run_would_place", (dec2.status, dec2.reason)


def test_chase_skip_consumes_no_budget_and_journals_no_coid():
    tk, ctx, sub = _itf_ctx_and_sub()
    dec, jrnl = _eval(_itf_sig(whale=0.7809), sub, ctx)
    assert dec.status == "skip:chase"
    assert jrnl.daily_usd("kalshi_test", "itf") == 0.0 and jrnl.orders_today("kalshi_test", "itf") == 0
    assert jrnl.open_usd("kalshi_test") == 0.0
    # the SAME signal places once the price comes back inside: state-based retry, nothing was journaled.
    # ask 0.78: fee 0.07*0.78*0.22 = 0.0120120 -> chase 0.78+0.0120120-0.7809 = 0.0111120 <= 2c -> places
    _, ctx3, _ = _itf_ctx_and_sub(yes_ask=0.78, yes_bid=0.76)
    dec3, _ = _eval(_itf_sig(whale=0.7809), sub, ctx3)
    assert dec3.status == "dry_run_would_place", (dec3.status, dec3.reason)


def test_exits_are_exempt_from_chase():
    # an EXIT with NO whale price and a huge would-be chase must NEVER be chase-blocked (capping a
    # risk-reducing exit strands it). With nothing held it lands on the holding guard -- NOT on gate C.
    _, ctx, sub = _itf_ctx_and_sub()
    ex = E.CopySignal(wallet="0xabc", slug="itf-wendler-tosetti-2026-09-16", outcome="Marco Tosetti",
                      condition_id="0xcondchase", outcome_index=0, signal_id="sigexit", is_exit=True,
                      title="ITF: Jefferson Wendler Filho vs Marco Tosetti")
    dec, _ = _eval(ex, sub, ctx)
    assert dec.status == "skip:not_held", dec.status      # passed gate C untouched; stopped by the holding guard


def test_sub_config_chase_columns_null_missing_and_disable():
    # NULL column -> code default; MISSING column (pre-migration-025 row) -> code default; <= 0 -> disabled
    assert E.CONFIG_DEFAULTS["max_chase_cents"] == 2.0 and E.CONFIG_DEFAULTS["chase_upside_pct"] == 0.0
    base = dict(account_id="kalshi_test", category="itf", market_types="itf_moneyline", sizing_mode="contracts",
                fixed_stake_usd=None, contracts=None, per_order_usd_cap=None, daily_usd_cap=None,
                max_open_usd=None, max_orders_per_day=None, max_slippage_cents=None, liquidity_ratio=None)
    sub_null = E.sub_config_from_row(dict(base, max_chase_cents=None, chase_upside_pct=None))
    assert sub_null.max_chase_cents == 2.0 and sub_null.chase_upside_pct == 0.0
    sub_missing = E.sub_config_from_row(dict(base))                        # no such keys at all
    assert sub_missing.max_chase_cents == 2.0 and sub_missing.chase_upside_pct == 0.0
    sub_off = E.sub_config_from_row(dict(base, max_chase_cents=-1.0, chase_upside_pct=0.0))
    assert sub_off.max_chase_cents == -1.0                                 # <= 0 reads back verbatim = disabled
    sub_set = E.sub_config_from_row(dict(base, max_chase_cents=6.0, chase_upside_pct=75.0))
    assert sub_set.max_chase_cents == 6.0 and sub_set.chase_upside_pct == 75.0


# ══════════════════════════════════════════════════════════════════════════════════════════
# observability: the summary counter, the rate-limited log, the heartbeat column (+ pre-025 fallback)
# ══════════════════════════════════════════════════════════════════════════════════════════
def _cycle(conn, sub, sigs, ctx, now_ts, log):
    return asyncio.run(LD.run_live_arm_gated_cycle(
        conn, sub, sigs, ctx, E.Journal(conn, [sub.account_id], now_ts), now_ts,
        place_fn=None, shard_balances=None, venue_exposure=None,
        legacy_db_path=os.path.join(tempfile.mkdtemp(), "no_legacy.db"), log=log))


def test_cycle_counts_chase_skips_and_rate_limits_the_log(caplog):
    LD._chase_log_last.clear()
    tk, ctx, sub = _itf_ctx_and_sub()
    d = tempfile.mkdtemp()
    path = os.path.join(d, "pm.db")
    DB.init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    log = logging.getLogger("test_chase_cycle")
    with caplog.at_level(logging.WARNING, logger="test_chase_cycle"):
        s1 = _cycle(conn, sub, [_itf_sig(whale=0.7809, sid="sigA"), _itf_sig(whale=None, sid="sigB")],
                    ctx, 1_800_000_000, log)
        assert s1["chase_skips"] == 2 and s1["n_skip"] == 2 and s1["placed"] == 0
        lines = [r.message for r in caplog.records if "gate-C CHASE skip" in r.message]
        assert len(lines) == 1, lines                      # rate-limited: ONE line for the two skips
        # the line carries the ticker + the whale/our/threshold numbers (via the Decision reason)
        assert tk in lines[0] and "chase_0.1065" in lines[0] and "gt_abs_cap_2.0c" in lines[0], lines[0]
        # a second cycle inside the window: counted again, NOT logged again
        s2 = _cycle(conn, sub, [_itf_sig(whale=0.7809, sid="sigA")], ctx, 1_800_000_005, log)
        assert s2["chase_skips"] == 1
        lines2 = [r.message for r in caplog.records if "gate-C CHASE skip" in r.message]
        assert len(lines2) == 1, lines2
    conn.close()


def test_heartbeat_persists_chase_skips_and_tolerates_pre_025():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "pm.db")
    DB.init_db(path)                                       # head 25: chase_skips column present
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    HB.upsert_evaluated(conn, "kalshi_test", "itf", 1_800_000_000, {"n_signals": 5, "chase_skips": 3})
    r = conn.execute("SELECT chase_skips, n_signals FROM pm_driver_heartbeat "
                     "WHERE account_id='kalshi_test' AND category='itf'").fetchone()
    assert r["chase_skips"] == 3 and r["n_signals"] == 5
    # absent from summ -> written as 0 (NULL means pre-migration, 0 means evaluated-no-skips)
    HB.upsert_evaluated(conn, "kalshi_test", "itf", 1_800_000_007, {"n_signals": 5})
    assert conn.execute("SELECT chase_skips FROM pm_driver_heartbeat WHERE account_id='kalshi_test' "
                        "AND category='itf'").fetchone()[0] == 0
    conn.close()
    # PRE-MIGRATION-025 box: the pre-025 DDL (no chase_skips column) -> the write FALLS BACK, never raises
    conn2 = sqlite3.connect(":memory:")
    conn2.row_factory = sqlite3.Row
    conn2.execute("CREATE TABLE pm_driver_heartbeat (account_id TEXT NOT NULL, category TEXT NOT NULL,"
                  " reached_ts INTEGER, evaluated_ts INTEGER, n_signals INTEGER, placed INTEGER,"
                  " errors INTEGER, ceiling_latched INTEGER, state TEXT, updated_ts INTEGER,"
                  " PRIMARY KEY (account_id, category))")
    HB.upsert_evaluated(conn2, "kalshi_test", "itf", 1_800_000_000, {"n_signals": 2, "chase_skips": 9})
    r2 = conn2.execute("SELECT n_signals FROM pm_driver_heartbeat").fetchone()
    assert r2["n_signals"] == 2
    conn2.close()


def test_migration_025_columns_and_head():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "pm.db")
    DB.init_db(path)
    conn = sqlite3.connect(path)
    sub_cols = {r[1] for r in conn.execute("PRAGMA table_info(pm_subdivision)")}
    hb_cols = {r[1] for r in conn.execute("PRAGMA table_info(pm_driver_heartbeat)")}
    assert {"max_chase_cents", "chase_upside_pct"} <= sub_cols
    assert "chase_skips" in hb_cols
    assert DB.SCHEMA_HEAD == 25
    assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 25
    conn.close()
