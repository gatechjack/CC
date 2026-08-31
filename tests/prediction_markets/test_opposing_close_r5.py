"""Opposing-pair guard (cancellation-by-disagreement). When two whales take OPPOSITE sides of ONE market the bet
comes OFF THE BOOKS: we CLOSE what we hold (account-level FULL flatten, close_source='opposed') and SKIP both incoming
sides. Proves the LEG DEFINITION per market type via (condition_id, outcome_index) -- moneyline two teams, total
over/under, spread; a different LINE is a different condition_id and must NEVER be flagged -- the multi-copy flatten
(ALL whales' contracts, not one whale's), that the guard NEVER fires on a legitimate same-side copy, the DEFER when
the close has no routing source, and that DISARM still blocks the opposed close. Fixtures use the real SEATOR market
index (standing lens: mirror the real object)."""
import types

import pytest

from trading_corp.prediction_markets import db, execution as ex, live_driver as ld
from trading_corp.data import mlb_poly_kalshi_match as M

W = "0x16bb9951a36fce71e2ef57890b786145e0ba8492"
CID = "0xcond_sea_tor"                      # the SEATOR moneyline market (its two outcomes = SEA / TOR)
TOR_T = "KXMLBGAME-26AUG281915SEATOR-TOR"

_GAME_TICKERS = ["KXMLBGAME-26AUG281915SEATOR-SEA", "KXMLBGAME-26AUG281915SEATOR-TOR"]
_TOTAL_TICKERS = ["KXMLBTOTAL-26AUG281915SEATOR-9", "KXMLBTOTAL-26AUG281915SEATOR-8"]
_SPREAD_TICKERS = ["KXMLBSPREAD-26AUG281915SEATOR-TOR2", "KXMLBSPREAD-26AUG281915SEATOR-SEA2"]
_MARKETS = {
    "KXMLBGAME-26AUG281915SEATOR-TOR": {"yes_ask_dollars": 0.55, "yes_bid_dollars": 0.53, "no_ask_dollars": 0.47, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"},
    "KXMLBGAME-26AUG281915SEATOR-SEA": {"yes_ask_dollars": 0.47, "yes_bid_dollars": 0.45, "no_ask_dollars": 0.55, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"},
}


def _ctx():
    return ex.MarketContext(M.build_kalshi_game_index(_GAME_TICKERS), M.build_kalshi_total_index(_TOTAL_TICKERS),
                            M.build_kalshi_spread_index(_SPREAD_TICKERS), frozenset({"2026-08-28"}), _MARKETS)


def _sub(**over):
    base = dict(account_id="kalshi_jack", category="mlb", market_types=("moneyline", "total", "spread"),
               sizing_mode="contracts", contracts=5, fixed_stake_usd=0.01, per_order_usd_cap=5.5,
               daily_usd_cap=60.0, max_open_usd=60.0, max_orders_per_day=20, max_slippage_cents=2)
    base.update(over)
    return ex.SubConfig(**base)


def _sig(wallet, slug, outcome, cid, oidx, **kw):
    return ex.CopySignal(wallet=wallet, slug=slug, outcome=outcome, condition_id=cid, outcome_index=oidx,
                         signal_id="%s:%s:%s" % (wallet, cid, oidx), **kw)


def _order(conn, wallet, ticker, leg, is_exit, fill, *, cid=None, oidx=0, status="filled"):
    conn.execute("INSERT INTO pm_subdivision_order (account_id,category,wallet,condition_id,outcome_index,ticker,"
                 "outcome_leg,is_exit,fill_count,outcome_status,dry_run,submitted_ts,response_ts) VALUES "
                 "('kalshi_jack','mlb',?,?,?,?,?,?,?,?,0,1,1)", (wallet, cid, oidx, ticker, leg, is_exit, fill, status))
    conn.commit()


# ── the LEG DEFINITION per market type, via (condition_id, outcome_index) -- proven, not reasoned ──────────
def test_moneyline_two_teams_same_cid_diff_oidx_is_contested():
    bal = _sig("0xW1", "mlb-bal-col", "Orioles", "0xbc", 0)          # team A (oidx 0)
    col = _sig("0xW2", "mlb-bal-col", "Rockies", "0xbc", 1)          # team B (oidx 1) -- same market, other outcome
    held = {"0xbc": {1: {"net": 5.0, "ticker": "KXMLBGAME-X-COL", "leg": "yes"}}}   # we hold COL
    kept, closes, contested = ex.detect_opposing_closes([bal, col], held)
    assert contested == {"0xbc"}
    assert kept == []                                                # BOTH incoming sides skipped (off the books)
    assert len(closes) == 1
    c = closes[0]
    assert c.is_exit and c.close_source == "opposed" and c.outcome_index == 1     # close the HELD outcome (COL)
    assert c.slug == "mlb-bal-col" and c.outcome == "Rockies"        # routed from the co-present COL signal


def test_total_over_under_contested_but_different_LINES_not():
    over9 = _sig("0xW1", "mlb-tot-9", "Over", "0xtot9", 1)
    under9 = _sig("0xW2", "mlb-tot-9", "Under", "0xtot9", 0)         # same line (same cid), other outcome -> contested
    over10 = _sig("0xW3", "mlb-tot-10", "Over", "0xtot10", 1)        # DIFFERENT line = DIFFERENT cid -> NOT opposing
    held = {"0xtot9": {1: {"net": 5.0, "ticker": "T9", "leg": "yes"}}}
    kept, closes, contested = ex.detect_opposing_closes([over9, under9, over10], held)
    assert contested == {"0xtot9"}                                   # over/under on line 9 disagree
    assert "0xtot10" not in contested                               # a different line is a different market
    assert over10 in kept and over9 not in kept and under9 not in kept   # the other-line signal survives untouched


def test_guard_NEVER_fires_on_legitimate_same_side_stacking():
    # ★ 3 whales the SAME side (same cid AND same oidx) -> agreement is conviction, NEVER contested. If this ever
    # flagged, it would break the same-side-is-conviction design. All kept, no closes.
    sigs = [_sig(w, "mlb-sea", "Seattle", CID, 0) for w in ("0xA", "0xB", "0xC")]
    held = {CID: {0: {"net": 15.0, "ticker": TOR_T, "leg": "yes"}}}   # we hold the SAME outcome the whales back
    kept, closes, contested = ex.detect_opposing_closes(sigs, held)
    assert contested == set() and closes == [] and kept == sigs


def test_defer_close_when_no_co_present_routing_source():
    # we HOLD the opposing outcome but its whale's book failed this cycle (no co-present signal) -> the incoming
    # opposing side is still skipped (contested), but the close is DEFERRED (never guessed) and retried next cycle.
    bal = _sig("0xW1", "mlb-bc", "Orioles", "0xbc", 0)
    held = {"0xbc": {1: {"net": 5.0, "ticker": "COL", "leg": "yes"}}}   # held COL, but NO COL signal present
    kept, closes, contested = ex.detect_opposing_closes([bal], held)
    assert contested == {"0xbc"} and kept == [] and closes == []       # skip incoming, defer the close


# ── account-level net-open (the multi-copy flatten basis) ─────────────────────────────────────────────────
def test_account_net_open_is_all_whales_but_journal_is_per_wallet(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        for w in ("0xA", "0xB", "0xC"):
            _order(conn, w, TOR_T, "yes", 0, 5.0, cid=CID, oidx=0)     # 3 whales x 5 = 15 account net
        assert ex.account_net_open_contracts(conn, "kalshi_jack", "mlb", TOR_T, "yes") == 15.0   # ALL whales
        assert ex.journal_net_open_contracts(conn, "kalshi_jack", "mlb", TOR_T, "yes", "0xA") == 5.0   # ONE whale
        held = ex.account_held_by_market(conn, "kalshi_jack", "mlb")
        assert held[CID][0]["net"] == 15.0 and held[CID][0]["ticker"] == TOR_T and held[CID][0]["leg"] == "yes"


def test_opposed_close_FLATTENS_ALL_not_one_whale_worth(tmp_path):
    # ★ THE MULTI-COPY RULING (Jack): flat means ALL of it. 3 whales hold 15; an opposed close flattens 15, NOT 5.
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        for w in ("0xA", "0xB", "0xC"):
            _order(conn, w, TOR_T, "yes", 0, 5.0, cid=CID, oidx=0)
        opp = _sig("0xA", "mlb-sea-tor-2026-08-28", "Toronto Blue Jays", CID, 0, is_exit=True, close_source="opposed")
        d = ex.evaluate(opp, _sub(), _ctx(), ex.Journal(conn, ["kalshi_jack"], 1787900000), conn, 1787900000)
    assert d.status == "dry_run_would_place" and d.is_exit is True and d.body.get("reduce_only") is True
    assert int(d.body["count"]) == 15                              # ★ account-level FULL flatten (15), not 5

    # contrast: a whale-EXIT (close_source None) for 0xA closes only A's 5 (per-wallet, unchanged)
    p2 = str(tmp_path / "pm2.db"); db.init_db(p2)
    with db.connect(p2) as conn:
        for w in ("0xA", "0xB", "0xC"):
            _order(conn, w, TOR_T, "yes", 0, 5.0, cid=CID, oidx=0)
        wx = _sig("0xA", "mlb-sea-tor-2026-08-28", "Toronto Blue Jays", CID, 0, is_exit=True)   # close_source=None
        d2 = ex.evaluate(wx, _sub(), _ctx(), ex.Journal(conn, ["kalshi_jack"], 1787900000), conn, 1787900000)
    assert int(d2.body["count"]) == 5                              # per-wallet whale-exit unchanged (F2)


# ── DISARM still blocks the opposed close (it is a reduce_only order through the SAME chokepoint) ──────────
@pytest.mark.asyncio
async def test_disarm_blocks_the_opposed_close(tmp_path, monkeypatch):
    import trading_corp.prediction_markets.arm as arm
    monkeypatch.setattr(arm, "read_arm_verdict", lambda *a, **k: types.SimpleNamespace(armed=False))   # DISARMED
    p = str(tmp_path / "pm.db"); db.init_db(p)
    placed = {"n": 0}

    async def _place(_d):
        placed["n"] += 1                                            # must NOT be reached (disarm blocks first)
        return types.SimpleNamespace(order_id="x", qty=15.0, price=0.53, fee=0.1)

    with db.connect(p) as conn:
        _order(conn, "0xA", TOR_T, "yes", 0, 15.0, cid=CID, oidx=0)   # a held position so the close would-place
        opp = _sig("0xA", "mlb-sea-tor-2026-08-28", "Toronto Blue Jays", CID, 0, is_exit=True, close_source="opposed")
        summ = await ld.run_live_arm_gated_cycle(conn, _sub(), [opp], _ctx(),
                                                 ex.Journal(conn, ["kalshi_jack"], 1787900000), 1787900000,
                                                 place_fn=_place)
        n_rows = conn.execute("SELECT COUNT(*) n FROM pm_subdivision_order WHERE is_exit=1 AND dry_run=0").fetchone()["n"]
    assert summ["n_would_place"] == 1 and summ["n_disarm_blocked"] == 1 and summ["placed"] == 0
    assert placed["n"] == 0 and n_rows == 0                         # off is off: no POST, no journal row
