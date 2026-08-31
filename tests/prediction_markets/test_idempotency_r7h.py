"""Stage 3 R7.h -- IDEMPOTENCY ACROSS RESTART. Same signal, kill, restart, NO double order. Placement is STUBBED
everywhere; ZERO real POSTs (no real Kalshi client is ever constructed). The fixture basis is the platform's ONE
real order (id=1, coid 0752f7f6-b49b-590f-ba10-dd76d3d82b82) per the standing lesson -- the derivation must
reproduce the REAL key, not an invented one.

PROVES (each a named test):
  1. the same signal -> a BYTE-IDENTICAL coid across two SEPARATE process runs (a fresh interpreter, not just
     equal-within-a-run), and that coid == the REAL journaled coid.
  2. a FILLED signal is deduped by gate 4 on the next cycle -- no second order.
  3. THE CRASH WINDOW: a 'submitting' row (POST sent, journal-write never completed) -> on restart gate 4 dedups
     it (the coid seeds despite no outcome), AND boot_reconcile ADJUDICATES the residual (KALSHI_ONLY -> latch).
     The position CANNOT be entered twice.
  4. restart with the in-memory counters GONE: a fresh Journal reseeds the caps AND the placed-coid set from the
     durable journal, so an already-placed signal does not place again.
  5. THE INVERSE: dedup CAN refuse a legitimate same-market re-entry, because the /positions entry key carries no
     per-entry identity -- stability-across-restart and distinctness-across-re-entry CONFLICT under the current
     key. A DIFFERENT market is NOT deduped. (Documented LIMITATION; fix = key on an /activity tx_hash.)

Both standing lenses:
  * fails-open (dedup that silently stops checking): the seed counts EXACTLY dry_run=0 rows -- a dry-run does NOT
    block a real order, and a dry_run=0 'submitting' row DOES seed (the crash-window is caught). The value that
    makes dedup pass-everything is an empty placed set (a key that never matches / a seed that misses the rows);
    proven not to happen.
  * NO-leg lens: the coid derives correctly + stably for a NO copy, and is DISTINCT from the YES coid on the same
    ticker (the leg is in the key). That path was never exercised in production (the only real order was a YES).
"""
import os
import subprocess
import sys

import pytest

from trading_corp.prediction_markets import arm, boot_reconcile as BR, db, execution as ex, live_driver as L
from trading_corp.brokers.kalshi_live import client_order_id
from trading_corp.data import mlb_poly_kalshi_match as M

# ── the REAL order (id=1) as the fixture basis ────────────────────────────────
ACCT, CAT, DIV = "kalshi_jack", "mlb", "kalshi_jack:mlb"
WALLET = "0x16bb9951a36fce71e2ef57890b786145e0ba8492"
CID = "0x9c62c626cfe36f5273fa016e27803a00c75a19a62a044a1941f83c55706bf97b"
OIDX = 1
TICKER = "KXMLBGAME-26AUG301920CINCHC-CHC"
TICKER_OPP = "KXMLBGAME-26AUG301920CINCHC-CIN"
LEG = "yes"
SLUG = "mlb-cin-chc-2026-08-30"
OUTCOME = "Chicago Cubs"
REAL_SIGNAL_ID = "83c8bf91aa7ccc3196b39e9aecae282b"
REAL_COID = "0752f7f6-b49b-590f-ba10-dd76d3d82b82"
NOW = 1788128073 + 3600            # same UTC day as the real fill -> the filled row seeds orders_today

MARKETS = {
    TICKER: {"yes_ask_dollars": 0.60, "yes_bid_dollars": 0.58, "no_ask_dollars": 0.42, "no_bid_dollars": 0.40,
             "liquidity_dollars": 500, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00", "exchange_index": 3},
}


def _ctx(markets=None):
    return ex.MarketContext(M.build_kalshi_game_index([TICKER, TICKER_OPP]),
                            M.build_kalshi_total_index([]), M.build_kalshi_spread_index([]),
                            frozenset({"2026-08-30"}), MARKETS if markets is None else markets)


def _sub(**over):
    base = dict(account_id=ACCT, category=CAT, market_types=("moneyline", "total", "spread"), sizing_mode="fixed",
                fixed_stake_usd=5.0, per_order_usd_cap=25.0, daily_usd_cap=50.0, max_open_usd=100.0,
                max_orders_per_day=25, max_slippage_cents=2)
    base.update(over)
    return ex.SubConfig(**base)


class _Pos:                       # a polymarket /positions row (paper.is_genuinely_open + pos_outcome_index)
    def __init__(self, cid=CID, oidx=OIDX, outcome=OUTCOME, slug=SLUG, cur=0.595, redeemable=False):
        self.condition_id = cid; self.slug = slug; self.outcome = outcome
        self.extra = {"outcomeIndex": oidx, "curPrice": cur, "redeemable": redeemable}


class _FakeKPos:                  # a Kalshi portfolio position for boot_reconcile: ticker + signed position_fp
    def __init__(self, ticker, position_fp):
        self.ticker = ticker; self.position_fp = position_fp


def _real_signal():
    """The REAL entry signal, derived through the PRODUCTION path (positions -> CopySignal). Its signal_id is the
    stable derivation -- so evaluate() computes the REAL coid from it."""
    sigs = L.positions_to_entry_signals([_Pos()], WALLET)
    assert len(sigs) == 1
    return sigs[0]


def _legacy(tmp_path):
    import sqlite3
    p = str(tmp_path / "trading_corp.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE agent_state (agent TEXT NOT NULL, key TEXT NOT NULL, value_json TEXT NOT NULL, "
              "updated_ts TEXT NOT NULL, PRIMARY KEY (agent, key))")
    c.commit(); c.close()
    return p


def _arm_both(leg):
    arm.arm(global_=True, require_latch_clear=True, legacy_db_path=leg)
    arm.arm(ACCT, CAT, require_latch_clear=True, legacy_db_path=leg)


def _insert_order(conn, *, outcome_status, coid=REAL_COID, signal_id=REAL_SIGNAL_ID, ticker=TICKER, leg=LEG,
                  cid=CID, oidx=OIDX, is_exit=0, submitted_count=1, submitted_price=0.62,
                  fill_count=1.0, fill_price=0.60, fee=0.0084, dry_run=0, ts=1788128073):
    """Insert a pm_subdivision_order row mirroring the real fill. For a 'submitting' residual, the fill fields are
    NULL (the crash-window state: coid journaled, outcome not yet stamped)."""
    submitting = outcome_status == "submitting"
    conn.execute(
        "INSERT INTO pm_subdivision_order (account_id, category, wallet, condition_id, outcome_index, signal_id, "
        " client_order_id, ticker, order_side, outcome_leg, is_exit, submitted_count, submitted_price, "
        " time_in_force, outcome_status, broker_order_id, fill_count, fill_price, fee, dry_run, submitted_ts, response_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ACCT, CAT, WALLET, cid, oidx, signal_id, coid, ticker, "bid", leg, is_exit, submitted_count, submitted_price,
         "immediate_or_cancel", outcome_status,
         (None if submitting else "01a054bd-1528-7118-8760-a7a064d75711"),
         (None if submitting else fill_count), (None if submitting else fill_price), (None if submitting else fee),
         dry_run, ts, ts))
    conn.commit()


def _coid_in_fresh_process(*, wallet=WALLET, cid=CID, oidx=OIDX, division=DIV, ticker=TICKER, leg=LEG):
    """Derive the coid in a FRESH interpreter via the REAL production functions (stable_signal_id + the real
    _stable_entry_key + client_order_id). Proves cross-PROCESS determinism, not equal-within-a-run."""
    code = (
        "from trading_corp.prediction_markets import execution as ex, live_driver as L;"
        "from trading_corp.brokers.kalshi_live import client_order_id;"
        "sid=ex.stable_signal_id(%r,%r,%r,L._stable_entry_key(%r,%r));"
        "print(client_order_id(%r,%r,%r,%r,sid))" % (wallet, cid, oidx, cid, oidx, division, wallet, ticker, leg)
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=dict(os.environ))
    assert r.returncode == 0, "subprocess failed: %s" % r.stderr
    return r.stdout.strip()


# ══ PROPERTY 1: byte-identical coid across two SEPARATE process runs, == the REAL coid ══
def test_1_coid_is_byte_identical_across_processes_and_matches_the_real_order():
    # in-process derivation via the production path
    sid = ex.stable_signal_id(WALLET, CID, OIDX, L._stable_entry_key(CID, OIDX))
    coid = client_order_id(DIV, WALLET, TICKER, LEG, sid)
    assert sid == REAL_SIGNAL_ID                      # reproduces the REAL journaled signal_id (real fixture)
    assert coid == REAL_COID                          # ... and the REAL journaled coid
    # a FRESH interpreter, twice -- must derive the SAME key from the SAME inputs (not equal-within-a-run)
    c1 = _coid_in_fresh_process()
    c2 = _coid_in_fresh_process()
    assert c1 == REAL_COID and c2 == REAL_COID and c1 == c2


def test_1b_evaluate_computes_the_real_coid_on_a_clean_journal(tmp_path):
    """The chokepoint itself derives REAL_COID for the real signal (empty journal -> it would place, coid == real)."""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        d = ex.evaluate(_real_signal(), _sub(), _ctx(), j, conn, NOW)
    assert d.status == "dry_run_would_place", d.status          # ctx maps the real signal to the real ticker/leg
    assert d.kalshi_ticker == TICKER and d.leg == LEG
    assert d.client_order_id == REAL_COID                       # the coid the driver would POST == the real one


# ══ PROPERTY 2: a FILLED signal is deduped by gate 4 -- no second order ══
def test_2_filled_signal_is_deduped_by_gate4(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _insert_order(conn, outcome_status="filled")           # the real fill, journaled
        j = ex.Journal(conn, [ACCT], NOW)
        assert j.already_placed(REAL_COID) is True             # reseeded from the durable journal
        d = ex.evaluate(_real_signal(), _sub(), _ctx(), j, conn, NOW)
    assert d.status == "skip:duplicate" and d.reason == "already_placed"
    assert d.client_order_id == REAL_COID


@pytest.mark.asyncio
async def test_2b_armed_cycle_does_not_replace_a_filled_signal(tmp_path):
    """End-to-end: ARMED + gates pass, but the already-filled signal is deduped -> the stub placer is NEVER called."""
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    calls = []
    async def stub(d):
        calls.append(d); return None
    with db.connect(p) as conn:
        _insert_order(conn, outcome_status="filled")
        j = ex.Journal(conn, [ACCT], NOW)
        summ = await L.run_live_arm_gated_cycle(conn, _sub(), [_real_signal()], _ctx(), j, NOW,
                                                place_fn=stub, legacy_db_path=leg)
        n = conn.execute("SELECT COUNT(*) FROM pm_subdivision_order").fetchone()[0]
    assert calls == [] and summ["placed"] == 0 and summ["n_would_place"] == 0   # deduped BEFORE would-place
    assert n == 1                                                               # still exactly the one real row


# ══ PROPERTY 3 (★): THE CRASH WINDOW -- 'submitting' with no recorded outcome ══
def test_3_crash_window_submitting_row_seeds_dedup_no_second_order(tmp_path):
    """A POST sent + the journal-write never completed leaves a 'submitting' row. On restart the coid MUST seed
    (the seed has NO outcome_status filter) so gate 4 dedups -> the position is NOT entered twice."""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _insert_order(conn, outcome_status="submitting")       # crash-window residual: coid present, fill NULL
        j = ex.Journal(conn, [ACCT], NOW)                      # 'restart'
        assert REAL_COID in j._placed_coids                    # ★ seeded DESPITE 'submitting' (dry_run=0 + coid)
        assert j.already_placed(REAL_COID) is True
        assert j.orders_today(ACCT, CAT) == 0                  # ... but NOT counted as filled (no budget/position)
        d = ex.evaluate(_real_signal(), _sub(), _ctx(), j, conn, NOW)
    assert d.status == "skip:duplicate"                        # ★ NO second order across the crash window


@pytest.mark.asyncio
async def test_3b_crash_window_armed_cycle_places_nothing(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    calls = []
    async def stub(d):
        calls.append(d); return None
    with db.connect(p) as conn:
        _insert_order(conn, outcome_status="submitting")
        j = ex.Journal(conn, [ACCT], NOW)
        summ = await L.run_live_arm_gated_cycle(conn, _sub(), [_real_signal()], _ctx(), j, NOW,
                                                place_fn=stub, legacy_db_path=leg)
    assert calls == [] and summ["placed"] == 0                 # armed, gates pass, yet the crash-window coid dedups


def test_3c_boot_reconcile_adjudicates_the_submitting_residual(tmp_path):
    """The OTHER half: a 'submitting' residual means journal_signed is FLAT (only 'filled' counts) while Kalshi
    actually holds the position -> boot_reconcile sees KALSHI_ONLY -> latch. The residual is surfaced, never
    silently dropped. (Pure compare -- no latch written here.)"""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _insert_order(conn, outcome_status="submitting")       # POST reached Kalshi; our journal-write crashed
        jsig = BR.journal_signed_positions(conn, ACCT)
    assert jsig == {}                                          # 'submitting' is NOT 'filled' -> journal reads FLAT
    ksig = BR.kalshi_signed_positions([_FakeKPos(TICKER, 1)])  # Kalshi DOES hold +1 (the POST filled pre-crash)
    diffs = BR.compare(jsig, ksig)
    assert len(diffs) == 1 and diffs[0].classification == BR.KALSHI_ONLY   # -> reconcile_account would LATCH


@pytest.mark.asyncio
async def test_3d_boot_reconcile_run_latches_on_the_residual(tmp_path):
    """Same, through the production entry point run_boot_reconcile: a real Kalshi position absent from the (filled)
    journal DISARMS via the boot_reconcile latch (fail-safe until a human clears it)."""
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)

    class _FP:
        def __init__(self, positions): self._p = positions
        async def get_positions(self, fetch_all=False): return list(self._p)

    class _FC:
        def __init__(self, positions): self.portfolio = _FP(positions); self.posts = []
        async def post(self, path, body): self.posts.append((path, body)); return {}
    with db.connect(p) as conn:
        _insert_order(conn, outcome_status="submitting")       # journal FLAT (filtered), Kalshi holds +1
        res = await L.run_boot_reconcile(conn, _sub(), _FC([_FakeKPos(TICKER, 1)]), legacy_db_path=leg)
    assert res.reconciled is False and res.latched is True
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False


# ══ PROPERTY 4: restart with in-memory counters gone -- the journal reseeds, no re-place ══
def test_4_restart_reseeds_counters_and_coids_from_the_journal(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _insert_order(conn, outcome_status="filled")
        j_fresh = ex.Journal(conn, [ACCT], NOW)                # a brand-new process: nothing in memory
        assert j_fresh.orders_today(ACCT, CAT) == 1            # count reseeded from the durable filled row
        assert j_fresh.daily_usd(ACCT, CAT) > 0.0              # ... and the daily USD
        assert j_fresh.already_placed(REAL_COID) is True       # ... and the placed-coid set
        d = ex.evaluate(_real_signal(), _sub(), _ctx(), j_fresh, conn, NOW)
    assert d.status == "skip:duplicate"                        # the already-placed signal does not place again


# ══ PROPERTY 5 (★): the INVERSE -- can dedup block a LEGITIMATE re-entry? ══
def test_5_same_market_reentry_is_refused_the_documented_conflict(tmp_path):
    """FINDING (not a detail): the /positions entry key `pos:{cid}:{oidx}` carries NO per-entry identity, so a
    close-then-reopen of the SAME (cid, oidx) derives the SAME signal_id -> SAME coid -> gate-4 dedup REFUSES the
    legitimate second order. Stability-across-restart and distinctness-across-re-entry CONFLICT under this key; the
    design resolves it toward safety (a missed re-entry, never a double). Fix = key on an /activity tx_hash."""
    entry_a = L.positions_to_entry_signals([_Pos()], WALLET)[0]          # first entry into Cubs
    entry_b = L.positions_to_entry_signals([_Pos()], WALLET)[0]          # a LATER re-entry into the SAME market
    assert entry_a.signal_id == entry_b.signal_id                        # ... identical -- no field distinguishes them
    coid_a = client_order_id(DIV, WALLET, TICKER, LEG, entry_a.signal_id)
    coid_b = client_order_id(DIV, WALLET, TICKER, LEG, entry_b.signal_id)
    assert coid_a == coid_b == REAL_COID
    # so once the first is journaled, the genuine re-entry is deduped (REFUSED) -- the accepted safe failure
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _insert_order(conn, outcome_status="filled")
        j = ex.Journal(conn, [ACCT], NOW)
        d = ex.evaluate(entry_b, _sub(), _ctx(), j, conn, NOW)
    assert d.status == "skip:duplicate"                                  # the legitimate re-entry is refused


def test_5b_a_different_market_is_not_deduped(tmp_path):
    """The scope check: a DIFFERENT market (different condition_id OR outcome_index) derives a DIFFERENT coid and is
    NOT deduped -- so a re-entry into a DIFFERENT game IS copied. Dedup is per (market, leg, entry), not a blanket."""
    other_cid = "0xDIFFERENTCONDITION0000000000000000000000000000000000000000000000"
    sig_other = ex.CopySignal(wallet=WALLET, slug=SLUG, outcome=OUTCOME, condition_id=other_cid,
                              outcome_index=OIDX,
                              signal_id=ex.stable_signal_id(WALLET, other_cid, OIDX, L._stable_entry_key(other_cid, OIDX)),
                              is_exit=False)
    coid_other = client_order_id(DIV, WALLET, TICKER, LEG, sig_other.signal_id)
    assert coid_other != REAL_COID                                       # different market -> different key
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _insert_order(conn, outcome_status="filled")                    # the Cubs order is journaled
        j = ex.Journal(conn, [ACCT], NOW)
        assert j.already_placed(coid_other) is False                    # the OTHER market's coid is NOT deduped


# ══ LENS 1 (fails-open): the seed counts EXACTLY dry_run=0 rows ══
def test_lens_dryrun_row_does_not_block_a_real_order(tmp_path):
    """A dry_run=1 row must NOT seed the placed-coid set -- else a dry-run preview would silently BLOCK the real
    order that follows (dedup over-firing). The seed's `dry_run=0` filter is the guard."""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _insert_order(conn, outcome_status="filled", dry_run=1)         # a DRY-RUN log row with the same coid
        j = ex.Journal(conn, [ACCT], NOW)
        assert j.already_placed(REAL_COID) is False                     # dry-run does NOT block a real order
        d = ex.evaluate(_real_signal(), _sub(), _ctx(), j, conn, NOW)
    assert d.status == "dry_run_would_place"                            # the real order is free to place


def test_lens_empty_journal_dedups_nothing_and_a_real_row_seeds(tmp_path):
    """The pass-everything value for dedup is an EMPTY placed set. Prove the boundary: with no journal, dedup fires
    for nothing (would-place); with the real dry_run=0 row present, the SAME coid IS caught. Dedup neither over-
    nor under-counts -- it tracks exactly the durable dry_run=0 coids."""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        j0 = ex.Journal(conn, [ACCT], NOW)
        assert j0._placed_coids == set() and j0.already_placed(REAL_COID) is False
        d0 = ex.evaluate(_real_signal(), _sub(), _ctx(), j0, conn, NOW)
        assert d0.status == "dry_run_would_place"                       # empty set -> dedup blocks nothing
        _insert_order(conn, outcome_status="filled")
        j1 = ex.Journal(conn, [ACCT], NOW)
        assert j1.already_placed(REAL_COID) is True                     # the real row IS seeded -> dedup fires


# ══ LENS 2 (NO-leg): the coid derives correctly + stably + DISTINCTLY for a NO copy ══
def test_lens_no_leg_coid_is_stable_and_distinct_from_yes():
    """The coid for a NO copy (the unexercised path -- the only real order was a YES) is stable across processes AND
    DISTINCT from the YES coid on the same ticker, because `leg` is part of the coid key. A collision would let a
    NO copy silently dedup against a YES position."""
    sid = ex.stable_signal_id(WALLET, CID, OIDX, L._stable_entry_key(CID, OIDX))
    coid_yes = client_order_id(DIV, WALLET, TICKER, "yes", sid)
    coid_no = client_order_id(DIV, WALLET, TICKER, "no", sid)
    assert coid_no != coid_yes                                          # the leg is in the key -> no cross-leg collision
    assert coid_yes == REAL_COID
    # a fresh interpreter derives the SAME no-leg coid (cross-process stable)
    c_no_fresh = _coid_in_fresh_process(leg="no")
    assert c_no_fresh == coid_no
