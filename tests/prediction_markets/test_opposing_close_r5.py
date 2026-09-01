"""Opposing-pair guard (cancellation-by-disagreement). When two whales take OPPOSITE sides of ONE market the bet
comes OFF THE BOOKS: we CLOSE what we hold (PER-WALLET closes, one per holding whale, summing to a full account
flatten) with close_source='opposed', and SKIP both incoming sides. Proves the LEG DEFINITION per market type via
(condition_id, outcome_index) -- moneyline two teams, total over/under, spread; a different LINE is a different
condition_id and must NEVER be flagged -- that the guard NEVER fires on a legitimate same-side copy, the PER-WALLET
flatten (all whales, no account-net-under-one-wallet negative), the DEFER when a close has no routing source, that a
SETTLED side nets flat in the (cid,oidx) view (no phantom held outcome -- the review blocker), and that DISARM still
blocks the opposed close. Fixtures use the real SEATOR market index (standing lens: mirror the real object)."""
import types

import pytest

from trading_corp.prediction_markets import db, execution as ex, live_driver as ld

W = "0x16bb9951a36fce71e2ef57890b786145e0ba8492"
CID = "0xcond_sea_tor"                      # the SEATOR moneyline market (its two outcomes = SEA / TOR)
TOR_T = "KXMLBGAME-26AUG281915SEATOR-TOR"

from trading_corp.data import mlb_poly_kalshi_match as M

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


def _order(conn, wallet, ticker, leg, is_exit, fill, *, cid=None, oidx=0, status="filled", close_source=None):
    conn.execute("INSERT INTO pm_subdivision_order (account_id,category,wallet,condition_id,outcome_index,ticker,"
                 "outcome_leg,is_exit,fill_count,outcome_status,close_source,dry_run,submitted_ts,response_ts) VALUES "
                 "('kalshi_jack','mlb',?,?,?,?,?,?,?,?,?,0,1,1)",
                 (wallet, cid, oidx, ticker, leg, is_exit, fill, status, close_source))
    conn.commit()


# ── the LEG DEFINITION per market type, via (condition_id, outcome_index) -- proven, not reasoned ──────────
def test_moneyline_two_teams_same_cid_diff_oidx_is_contested():
    bal = _sig("0xW1", "mlb-bal-col", "Orioles", "0xbc", 0)          # team A (oidx 0)
    col = _sig("0xW2", "mlb-bal-col", "Rockies", "0xbc", 1)          # team B (oidx 1) -- same market, other outcome
    held = {"0xbc": {1}}                                             # we hold COL (oidx 1)
    kept, closes, contested, _pre = ex.detect_opposing_closes([bal, col], held)
    assert contested == {"0xbc"}
    assert kept == []                                                # BOTH incoming sides skipped (off the books)
    assert len(closes) == 1                                          # one holding whale (COL) -> one per-wallet close
    c = closes[0]
    assert c.is_exit and c.close_source == "opposed" and c.outcome_index == 1
    assert c.wallet == "0xW2" and c.slug == "mlb-bal-col" and c.outcome == "Rockies"   # routed from the COL signal


def test_total_over_under_contested_but_different_LINES_not():
    over9 = _sig("0xW1", "mlb-tot-9", "Over", "0xtot9", 1)
    under9 = _sig("0xW2", "mlb-tot-9", "Under", "0xtot9", 0)         # same line (same cid), other outcome -> contested
    over10 = _sig("0xW3", "mlb-tot-10", "Over", "0xtot10", 1)        # DIFFERENT line = DIFFERENT cid -> NOT opposing
    held = {"0xtot9": {1}}
    kept, closes, contested, _pre = ex.detect_opposing_closes([over9, under9, over10], held)
    assert contested == {"0xtot9"}                                   # over/under on line 9 disagree
    assert "0xtot10" not in contested                               # a different line is a different market
    assert over10 in kept and over9 not in kept and under9 not in kept   # the other-line signal survives untouched


def test_guard_NEVER_fires_on_legitimate_same_side_stacking():
    # ★ 3 whales the SAME side (same cid AND same oidx) -> agreement is conviction, NEVER contested. If this ever
    # flagged, it would break the same-side-is-conviction design. All kept, no closes.
    sigs = [_sig(w, "mlb-sea", "Seattle", CID, 0) for w in ("0xA", "0xB", "0xC")]
    held = {CID: {0}}                                               # we hold the SAME outcome the whales back
    kept, closes, contested, _pre = ex.detect_opposing_closes(sigs, held)
    assert contested == set() and closes == [] and kept == sigs


def test_preexisting_pair_is_LEFT_ALONE_not_flattened_retroactively():
    # ★ Jack RULED let a pre-existing pair (BALCOL) SETTLE. We ALREADY hold BOTH sides (oidx 0 AND 1). On the next
    # cycle both whales re-signal their side -> the guard must NOT flatten it (that would be two exit orders into a
    # started game, overriding the ruling). It PREVENTS new pairs, it does not retroactively clean up.
    bal = _sig("0xW1", "mlb-bal-col", "Orioles", "0xbc", 0)         # re-signal of the held BAL side
    col = _sig("0xW2", "mlb-bal-col", "Rockies", "0xbc", 1)         # re-signal of the held COL side
    held = {"0xbc": {0, 1}}                                         # we ALREADY hold BOTH sides (pre-existing pair)
    kept, closes, contested, preexisting = ex.detect_opposing_closes([bal, col], held)
    assert preexisting == {"0xbc"}                                  # recognized as pre-existing
    assert contested == set() and closes == []                     # ★ NOT flattened -- left to settle
    assert bal in kept and col in kept                             # re-entries flow (gate-4 dedups them; no new order)


def test_defer_close_when_no_co_present_routing_source():
    # we HOLD the opposing outcome but its whale's book failed this cycle (no co-present signal) -> the incoming
    # opposing side is still skipped (contested), but the close is DEFERRED (never guessed) and retried next cycle.
    bal = _sig("0xW1", "mlb-bc", "Orioles", "0xbc", 0)
    held = {"0xbc": {1}}                                            # held COL (oidx 1), but NO COL signal present
    kept, closes, contested, _pre = ex.detect_opposing_closes([bal], held)
    assert contested == {"0xbc"} and kept == [] and closes == []   # skip incoming, defer the close


# ── PER-WALLET flatten: one close per holding whale (all of it), not one account-net row ──────────────────
def test_opposed_close_emits_ONE_PER_HOLDING_WHALE_flattening_all():
    # ★ THE MULTI-COPY RULING (Jack): flat means ALL of it. 3 whales hold oidx0; a 4th signals oidx1 -> the guard
    # emits THREE opposed-closes (one per holding whale), each per-wallet -- summing to the full flatten, WITHOUT the
    # account-net-under-one-wallet negative the review rejected.
    A, B, C, D = "0xA", "0xB", "0xC", "0xD"
    entries = [_sig(w, "mlb-sea", "Seattle", CID, 0) for w in (A, B, C)] + [_sig(D, "mlb-tor", "Toronto", CID, 1)]
    held = {CID: {0}}                                               # we hold oidx0 (all three whales' copies)
    kept, closes, contested, _pre = ex.detect_opposing_closes(entries, held)
    assert contested == {CID} and kept == []                        # all 4 incoming skipped
    assert len(closes) == 3                                          # ★ ONE close per holding whale
    assert {c.wallet for c in closes} == {A, B, C}                  # each holding whale, per-wallet
    assert all(c.close_source == "opposed" and c.is_exit and c.outcome_index == 0 for c in closes)


def test_account_held_outcomes_and_per_wallet_close_size(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        for w in ("0xA", "0xB", "0xC"):
            _order(conn, w, TOR_T, "yes", 0, 5.0, cid=CID, oidx=0)     # 3 whales x 5 = 15 account net on oidx0
        held = ex.account_held_outcomes(conn, "kalshi_jack", "mlb")
        assert held == {CID: {0}}                                      # one held outcome on this cid
        # each opposed-close sizes at ITS wallet's net-open (5), NOT the account 15 -- the flatten is the SUM of the 3
        opp = _sig("0xA", "mlb-sea-tor-2026-08-28", "Toronto Blue Jays", CID, 0, is_exit=True, close_source="opposed")
        d = ex.evaluate(opp, _sub(), _ctx(), ex.Journal(conn, ["kalshi_jack"], 1787900000), conn, 1787900000)
    assert d.status == "dry_run_would_place" and d.body.get("reduce_only") is True
    assert int(d.body["count"]) == 5                                  # per-wallet (A's 5); B and C get their own closes


# ── the SETTLED-side phantom (review BLOCKER): a settlement nets flat in the (cid,oidx) view too ──────────
def test_settled_side_nets_flat_no_phantom_held_outcome(tmp_path):
    # ★ REVIEW BLOCKER: a settlement-close now carries cid/oidx, so a settled side nets FLAT in account_held_outcomes
    # (the same as in the ticker-keyed reconcile/UI). Without this, a settled oidx0 lingered as a phantom and could
    # false-contest a legitimate same-side re-signal on the still-live oidx1.
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _order(conn, W, "KXMLBGAME-X-A", "yes", 0, 5.0, cid="0xX", oidx=0)     # held oidx0 (entry)
        _order(conn, W, "KXMLBGAME-X-B", "yes", 0, 5.0, cid="0xX", oidx=1)     # held oidx1 (the other side, historical)
        # oidx0 SETTLES -> a settlement-close carrying cid/oidx0 (as book_settlements now writes it)
        _order(conn, W, "KXMLBGAME-X-A", "yes", 1, 5.0, cid="0xX", oidx=0, close_source="settlement")
        held = ex.account_held_outcomes(conn, "kalshi_jack", "mlb")
    assert held["0xX"] == {1}                                          # ★ oidx0 netted flat; only oidx1 remains
    # a same-side re-signal on the LIVE oidx1 is therefore NOT contested (no phantom oidx0 to disagree with)
    kept, closes, contested, _pre = ex.detect_opposing_closes([_sig(W, "s1", "o1", "0xX", 1)], held)
    assert contested == set() and closes == []


# ── DISARM still blocks the opposed close (it is a reduce_only order through the SAME chokepoint) ──────────
@pytest.mark.asyncio
async def test_disarm_blocks_the_opposed_close(tmp_path, monkeypatch):
    import trading_corp.prediction_markets.arm as arm
    monkeypatch.setattr(arm, "read_arm_verdict", lambda *a, **k: types.SimpleNamespace(armed=False))   # DISARMED
    p = str(tmp_path / "pm.db"); db.init_db(p)
    placed = {"n": 0}

    async def _place(_d):
        placed["n"] += 1                                            # must NOT be reached (disarm blocks first)
        return types.SimpleNamespace(order_id="x", qty=5.0, price=0.53, fee=0.1)

    with db.connect(p) as conn:
        _order(conn, "0xA", TOR_T, "yes", 0, 5.0, cid=CID, oidx=0)   # a held position so the close would-place
        opp = _sig("0xA", "mlb-sea-tor-2026-08-28", "Toronto Blue Jays", CID, 0, is_exit=True, close_source="opposed")
        summ = await ld.run_live_arm_gated_cycle(conn, _sub(), [opp], _ctx(),
                                                 ex.Journal(conn, ["kalshi_jack"], 1787900000), 1787900000,
                                                 place_fn=_place)
        n_rows = conn.execute("SELECT COUNT(*) n FROM pm_subdivision_order WHERE is_exit=1 AND dry_run=0").fetchone()["n"]
    assert summ["n_would_place"] == 1 and summ["n_disarm_blocked"] == 1 and summ["placed"] == 0
    assert placed["n"] == 0 and n_rows == 0                         # off is off: no POST, no journal row


# ── OPPOSED-MEMORY (2026-09-01): the flicker fix + the R7.h-coupling-independent bound ──────────────────────────
def test_opposed_memory_survives_signal_flicker():
    """★ THE FLICKER BUG, fixed. Cycle 1: hold A, B incoming -> contest (close A, skip B). Cycle 2 (FLICKER): only
    B incoming, A's opposing signal GONE, we hold nothing. WITHOUT the memory B reads uncontested and ENTERS (the
    bug -- the overnight enter-close churn). WITH the memory (cid in opposed_cids) B is still SKIPPED."""
    cid = "0xflick"
    A = _sig("0xW1", "mlb-a-b", "TeamA", cid, 0)
    B = _sig("0xW2", "mlb-a-b", "TeamB", cid, 1)
    kept1, closes1, contested1, _ = ex.detect_opposing_closes([A, B], {cid: {0}}, opposed_cids=set())
    assert contested1 == {cid} and kept1 == []                       # cycle 1: same-cycle union -> contested
    assert any(c.close_source == "opposed" for c in closes1)         # A closed
    # cycle 2 without memory -> the BUG: B enters
    kept_bug, _, _, _ = ex.detect_opposing_closes([B], {}, opposed_cids=set())
    assert kept_bug == [B]
    # cycle 2 WITH memory -> FIXED: B skipped, nothing to close (we hold nothing)
    kept_fix, closes_fix, contested_fix, _ = ex.detect_opposing_closes([B], {}, opposed_cids={cid})
    assert contested_fix == {cid} and kept_fix == [] and closes_fix == []


def test_opposed_memory_never_blocks_same_side_agreement():
    """★ MUST NOT fire on agreement. Same-side stacking (same cid+oidx, N wallets) is the design (conviction). The
    memory is keyed on a cid being CONTESTED, so a same-side cid NOT in opposed_cids flows -- even with an
    unrelated opposed cid present in the memory."""
    same = "0xsame"
    s1 = _sig("0xW1", "mlb-x-y", "X", same, 0)
    s2 = _sig("0xW2", "mlb-x-y", "X", same, 0)                        # SAME outcome, different wallet = agreement
    kept, closes, contested, _ = ex.detect_opposing_closes([s1, s2], {same: {0}}, opposed_cids={"0xunrelated"})
    assert contested == set() and kept == [s1, s2] and closes == []  # agreement flows; the unrelated opposed cid is irrelevant


def test_opposed_memory_independent_of_coid_survives_r7h():
    """★ THE COUPLING PROOF. The skip is keyed on the cid being opposed, NOT on the coid -- the guard drops the
    entry from `kept` BEFORE the chokepoint's gate-4 coid dedup. So an incoming re-entry with a BRAND-NEW signal_id
    (simulating R7.h keying entries on an /activity tx_hash, which would defeat the stable-coid dedup) is STILL
    skipped. The bound holds after R7.h -- the whole reason to build this first."""
    cid = "0xopp"
    fresh = ex.CopySignal(wallet="0xW2", slug="mlb-a-b", outcome="TeamB", condition_id=cid, outcome_index=1,
                          signal_id="activity-tx-0xDEADBEEF")         # a distinct coid basis; gate-4 would NOT dedup it
    kept, closes, contested, _ = ex.detect_opposing_closes([fresh], {}, opposed_cids={cid})
    assert contested == {cid} and kept == [] and closes == []        # skipped upstream of gate-4, regardless of signal_id


def test_account_opposed_cids_only_opposed_not_settlement_or_entry(tmp_path, monkeypatch):
    """account_opposed_cids returns ONLY cids with a close_source='opposed' row -- NOT entries, NOT settlement
    closes. So a settled or same-side cid is never falsely off-the-books, and there is no marker table to leave
    dead rows (the opposed close IS the record)."""
    p = str(tmp_path / "pm.db"); monkeypatch.setenv("PM_DB_PATH", p); db.init_db(p)
    with db.connect(p) as conn:
        _order(conn, "0xW1", "KX-A", "yes", 0, 5, cid="0xentry", oidx=0)                              # plain entry
        _order(conn, "0xW1", "KX-B", "yes", 1, 5, cid="0xsettle", oidx=0, close_source="settlement")  # settlement close
        _order(conn, "0xW1", "KX-C", "yes", 1, 5, cid="0xopposed", oidx=0, close_source="opposed")    # opposed close
        assert ex.account_opposed_cids(conn, "kalshi_jack", "mlb") == {"0xopposed"}
