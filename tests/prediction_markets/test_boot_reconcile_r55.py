"""Stage 3 R5.5 -- BOOT-RECONCILE. Proves the MISSING half of boot-reconcile: the journal-vs-Kalshi POSITION
comparison. The seed half (R4 Journal) and the LATCH (R5 arm.py) already exist and are NOT retested here.

Rulings under test (Jack, 2026-08-29): EXACT K=0 (R-a); journal-only + kalshi-only both mismatch (R-b, R-c);
FULL-ACCOUNT kalshi-only (R-c); latch on any mismatch -> forces DISARM until a human clears it; SIGNED-net
compare so a YES/NO SIDE-FLIP is caught (the 5th NO-leg-lens instance); COUNT-ONLY, latch the reconciled
(account, category) (R-f); FAIL-SAFE on a portfolio-read failure. Plus the adversarial-review hardening:
a negative per-leg net is SURFACED (not dropped -> false match), ticker case is normalised, a non-integer
position_fp latches (not silently rounded), a missing journal table RAISES, and the production entry point has
NO latch-off switch. Structural: injected fetcher, NO broker.

Offline; tmp DBs. Arm state lives in a temp LEGACY agent_state DB; NOTHING real is placed (no broker exists)."""
import inspect
import sqlite3

import pytest

from trading_corp.prediction_markets import arm, boot_reconcile as br, db

ACCT, CAT = "kalshi_jack", "mlb"
T_TOR = "KXMLBGAME-26AUG281915SEATOR-TOR"
T_SEA = "KXMLBGAME-26AUG281915SEATOR-SEA"
T_TOT = "KXMLBTOTAL-26AUG281915SEATOR-9"
NOW = 1787900000


def _legacy(tmp_path):
    """A temp LEGACY DB with just the agent_state table (the exact engine DDL) -- where arm state lives."""
    p = str(tmp_path / "trading_corp.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE agent_state (agent TEXT NOT NULL, key TEXT NOT NULL, value_json TEXT NOT NULL, "
              "updated_ts TEXT NOT NULL, PRIMARY KEY (agent, key))")
    c.commit(); c.close()
    return p


def _seed(conn, ticker, leg, count, *, is_exit=0, category=CAT, account=ACCT, ts=NOW):
    """Seed one FILLED, real (dry_run=0) journal leg -- the same shape execution.py writes at fill time."""
    conn.execute("INSERT INTO pm_subdivision_order (account_id,category,ticker,outcome_leg,is_exit,"
                 "fill_count,outcome_status,dry_run,response_ts) VALUES (?,?,?,?,?,?,'filled',0,?)",
                 (account, category, ticker, leg, is_exit, count, ts))


def _kpos(ticker, position_fp):
    """A fake Kalshi position record (the injected fetcher's element): ticker + SIGNED position_fp."""
    return {"ticker": ticker, "position_fp": position_fp}


def _reconcile(tmp_path, seed_fn, kalshi, *, arm_first=False):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    if arm_first:
        arm.arm(global_=True, require_latch_clear=True, legacy_db_path=leg)
        arm.arm(ACCT, CAT, require_latch_clear=True, legacy_db_path=leg)
    with db.connect(p) as conn:
        seed_fn(conn); conn.commit()
        res = br.reconcile_account(conn, ACCT, CAT, fetch_positions=lambda: kalshi, legacy_db_path=leg)
    return res, leg


# ── the leg mapping, proven on BOTH sides (the NO-leg lens) ──────────────────
def test_journal_yes_leg_is_positive_no_leg_is_negative(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _seed(conn, T_TOR, "yes", 3)
        _seed(conn, T_TOT, "no", 4)
        conn.commit()
        j = br.journal_signed_positions(conn, ACCT)
    assert j == {T_TOR: 3, T_TOT: -4}                       # YES -> +, NO -> - (the journal side of the lens)


def test_kalshi_position_fp_sign_and_fixed_point_string_parse():
    k = br.kalshi_signed_positions([_kpos(T_TOR, "3.00"), _kpos(T_TOT, -4.0), _kpos(T_SEA, 0)])
    assert k == {T_TOR: 3, T_TOT: -4}                       # +/- signed; '3.00' parses; a 0/flat is dropped


# ── R-a EXACT match (K=0) both legs ──────────────────────────────────────────
def test_reconciled_when_signed_nets_match_yes_and_no(tmp_path):
    res, _ = _reconcile(tmp_path, lambda c: (_seed(c, T_TOR, "yes", 3), _seed(c, T_TOT, "no", 4)),
                        [_kpos(T_TOR, 3), _kpos(T_TOT, -4)])
    assert res.reconciled is True and res.diffs == () and res.latched is False


# ── THE REQUIRED TEST: the signed compare catches a YES/NO side-flip a magnitude compare would pass ──
def test_signed_compare_catches_side_flip_journalNO3_vs_kalshiYES3_a_magnitude_compare_would_PASS(tmp_path):
    """journal holds 3 NO (signed -3); Kalshi holds 3 YES (position_fp +3). |journal|==|kalshi|==3, so a
    MAGNITUDE compare would call it a MATCH and pass a real reversal SILENTLY. The SIGNED compare FAILS it."""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _seed(conn, T_TOT, "no", 3); conn.commit()
        j = br.journal_signed_positions(conn, ACCT)
    k = br.kalshi_signed_positions([_kpos(T_TOT, 3)])
    assert j == {T_TOT: -3} and k == {T_TOT: 3}
    assert abs(next(iter(j.values()))) == abs(next(iter(k.values())))      # the trap: magnitudes are equal
    diffs = br.compare(j, k)
    assert len(diffs) == 1 and diffs[0].classification == br.COUNT_MISMATCH  # ... signed catches the flip
    assert diffs[0].journal_signed == -3 and diffs[0].kalshi_signed == 3


# ── R-b journal-only (Kalshi flat) -> mismatch -> latch ──────────────────────
def test_journal_only_is_mismatch_R_b(tmp_path):
    res, _ = _reconcile(tmp_path, lambda c: _seed(c, T_TOR, "yes", 3), [])   # Kalshi returns NOTHING
    assert res.reconciled is False and res.latched is True
    assert len(res.diffs) == 1 and res.diffs[0].classification == br.JOURNAL_ONLY
    assert res.diffs[0].journal_signed == 3 and res.diffs[0].kalshi_signed == 0


# ── R-c kalshi-only (journal flat) -> mismatch, FULL ACCOUNT (a ticker PM never touched) ──
def test_kalshi_only_is_mismatch_full_account_R_c(tmp_path):
    res, _ = _reconcile(tmp_path, lambda c: None, [_kpos("KXMLBGAME-99DEC010000XXXYYY-XXX", 2)])  # journal EMPTY
    assert res.reconciled is False and res.latched is True
    assert len(res.diffs) == 1 and res.diffs[0].classification == br.KALSHI_ONLY
    assert res.diffs[0].journal_signed == 0 and res.diffs[0].kalshi_signed == 2


# ── count mismatch (both hold, nets differ) ──────────────────────────────────
def test_count_mismatch_when_nets_differ(tmp_path):
    res, _ = _reconcile(tmp_path, lambda c: _seed(c, T_TOR, "yes", 3), [_kpos(T_TOR, 5)])
    assert res.reconciled is False and len(res.diffs) == 1
    assert res.diffs[0].classification == br.COUNT_MISMATCH
    assert res.diffs[0].journal_signed == 3 and res.diffs[0].kalshi_signed == 5


# ── netting: same ticker YES and NO -> Kalshi's single NET, and it matches ────
def test_same_ticker_yes_and_no_net_matches_kalshi_net(tmp_path):
    # journal: +5 YES and +3 NO on one ticker -> signed net +2; Kalshi auto-nets to +2 -> reconciled
    res, _ = _reconcile(tmp_path, lambda c: (_seed(c, T_TOR, "yes", 5), _seed(c, T_TOR, "no", 3)),
                        [_kpos(T_TOR, 2)])
    assert res.reconciled is True and res.diffs == ()


# ── exits reduce the journal net (filled entry minus filled exit) ────────────
def test_journal_net_is_entries_minus_exits(tmp_path):
    # 5 YES entered, 2 YES exited -> net 3; Kalshi +3 -> reconciled
    res, _ = _reconcile(tmp_path, lambda c: (_seed(c, T_TOR, "yes", 5),
                                             _seed(c, T_TOR, "yes", 2, is_exit=1)), [_kpos(T_TOR, 3)])
    assert res.reconciled is True and res.diffs == ()


# ── ADVERSARIAL FIX 1: an OVER-EXITED leg (net < 0) is SURFACED, not dropped into a false MATCH ──
def test_over_exit_negative_signed_net_is_surfaced_not_a_false_match(tmp_path):
    """A leg over-exited in the journal (net < 0, a booking anomaly) must be SURFACED, not silently dropped.
    The old reuse of open_positions_needing_manual_exit (its net>0 per-leg filter) would DROP it -> the journal
    reads flat -> a false MATCH against a flat Kalshi. The direct signed-net query KEEPS the -2 so it latches."""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _seed(conn, T_TOR, "yes", 3)                 # entered 3
        _seed(conn, T_TOR, "yes", 5, is_exit=1)      # exited 5 -> net -2 (anomaly the reconcile must catch)
        conn.commit()
        j = br.journal_signed_positions(conn, ACCT)
    assert j == {T_TOR: -2}                           # KEPT (a net>0 filter would have dropped it to {})
    diffs = br.compare(j, {})                          # vs a flat Kalshi
    assert len(diffs) == 1 and diffs[0].classification == br.JOURNAL_ONLY and diffs[0].journal_signed == -2


# ── ADVERSARIAL FIX 2: ticker case is normalised on BOTH sides ───────────────
def test_ticker_case_normalised_both_sides_still_reconciles(tmp_path):
    """The order path sends str(ticker).upper() to Kalshi; the journal may store any case. Reconcile UPPER-cases
    both sides so a pure case divergence does NOT spuriously double-latch (journal_only + kalshi_only)."""
    res, _ = _reconcile(tmp_path, lambda c: _seed(c, "kxmlbgame-scratch-tor", "yes", 3),
                        [_kpos("KXMLBGAME-SCRATCH-TOR", 3)])
    assert res.reconciled is True and res.diffs == () and res.latched is False


# ── ADVERSARIAL FIX 3: a non-integer position_fp is REFUSED -> fail-safe latch (not rounded to 0, dropped) ──
def test_fractional_kalshi_position_fails_safe_latches(tmp_path):
    res, leg = _reconcile(tmp_path, lambda c: None, [_kpos("KXMLBGAME-FRACTION-XYZ", "0.4")])  # would round->0->drop
    assert res.reconciled is False and res.latched is True and res.read_error is not None
    assert arm.current_row(ACCT, CAT, legacy_db_path=leg)["auto_trigger"] == arm.AUTO_BOOT_RECONCILE


# ── ADVERSARIAL FIX 4: a MISSING journal table RAISES (our own DB; never a silent 'flat') ──
def test_missing_journal_table_raises_loudly_not_silent_flat():
    conn = sqlite3.connect(":memory:")                # a bare DB with NO pm_subdivision_order table
    with pytest.raises(Exception):
        br.journal_signed_positions(conn, ACCT)
    conn.close()


# ── FULL-ACCOUNT journal side: positions aggregate across categories on the account ──
def test_journal_aggregates_across_categories_on_the_account(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _seed(conn, T_TOR, "yes", 3, category="mlb")
        _seed(conn, "KXNBA-FOO-BAR", "yes", 1, category="nba")   # a DIFFERENT category, SAME account
        conn.commit()
        j = br.journal_signed_positions(conn, ACCT)
    assert j == {T_TOR: 3, "KXNBA-FOO-BAR": 1}                    # account-wide, both categories present


def test_journal_excludes_other_accounts_and_dry_run_and_unfilled(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _seed(conn, T_TOR, "yes", 3)                                          # ours, filled, real -> counts
        _seed(conn, T_SEA, "yes", 9, account="someone_else")                  # other account -> excluded
        conn.execute("INSERT INTO pm_subdivision_order (account_id,category,ticker,outcome_leg,is_exit,"
                     "fill_count,outcome_status,dry_run,response_ts) VALUES (?,?,?,?,0,7,'filled',1,?)",
                     (ACCT, CAT, T_TOT, "yes", NOW))                          # dry_run=1 -> excluded
        conn.execute("INSERT INTO pm_subdivision_order (account_id,category,ticker,outcome_leg,is_exit,"
                     "fill_count,outcome_status,dry_run,response_ts) VALUES (?,?,?,?,0,7,'rejected',0,?)",
                     (ACCT, CAT, T_TOT, "yes", NOW))                          # not filled -> excluded
        conn.commit()
        j = br.journal_signed_positions(conn, ACCT)
    assert j == {T_TOR: 3}                                        # only the real, filled, this-account leg


# ── the LATCH path, end to end: mismatch -> DISARM -> re-arm refused without ack -> human clears ──
def test_mismatch_latches_boot_reconcile_and_forces_disarm_end_to_end(tmp_path):
    res, leg = _reconcile(tmp_path, lambda c: _seed(c, T_TOR, "yes", 3), [], arm_first=True)
    assert res.reconciled is False and res.latched is True
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False               # DISARMED by the latch
    row = arm.current_row(ACCT, CAT, legacy_db_path=leg)
    assert row["latched"] is True and row["auto_trigger"] == arm.AUTO_BOOT_RECONCILE
    with pytest.raises(arm.LatchedError):                                     # re-arm REFUSED without ack
        arm.arm(ACCT, CAT, legacy_db_path=leg)
    arm.arm(ACCT, CAT, require_latch_clear=True, legacy_db_path=leg)          # a human acknowledges + clears
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is True


# ── a CLEAN reconcile writes NOTHING and never arms ──────────────────────────
def test_clean_reconcile_writes_nothing_and_never_arms(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _seed(conn, T_TOR, "yes", 3); conn.commit()
        res = br.reconcile_account(conn, ACCT, CAT, fetch_positions=lambda: [_kpos(T_TOR, 3)], legacy_db_path=leg)
    assert res.reconciled is True and res.latched is False
    assert arm.current_row(ACCT, CAT, legacy_db_path=leg) is None             # NOTHING written by a clean pass
    assert arm.current_row(None, None, global_=True, legacy_db_path=leg) is None
    arm.arm(ACCT, CAT, legacy_db_path=leg)                                    # freely armable (no latch)
    assert arm.current_row(ACCT, CAT, legacy_db_path=leg)["armed"] is True


# ── FAIL-SAFE: a portfolio read/parse failure is NOT 'reconciled' -- it latches ──
def test_portfolio_read_failure_fails_safe_latches(tmp_path):
    def _boom():
        raise RuntimeError("kalshi get_positions 503")
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    arm.arm(global_=True, require_latch_clear=True, legacy_db_path=leg)
    arm.arm(ACCT, CAT, require_latch_clear=True, legacy_db_path=leg)
    with db.connect(p) as conn:
        _seed(conn, T_TOR, "yes", 3); conn.commit()
        res = br.reconcile_account(conn, ACCT, CAT, fetch_positions=_boom, legacy_db_path=leg)
    assert res.reconciled is False and res.latched is True and res.read_error is not None
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False               # a read failure never leaves it armed


def test_malformed_kalshi_record_fails_safe_latches(tmp_path):
    res, leg = _reconcile(tmp_path, lambda c: _seed(c, T_TOR, "yes", 3), [{"ticker": T_TOR}])  # no position_fp
    assert res.reconciled is False and res.latched is True and res.read_error is not None


# ── compare() is PURE (the only latch-free path; there is NO latch-off switch on reconcile_account) ──
def test_compare_is_pure_no_side_effect(tmp_path):
    diffs = br.compare({T_TOR: 3}, {T_TOR: 5})
    assert len(diffs) == 1 and diffs[0].classification == br.COUNT_MISMATCH
    leg = _legacy(tmp_path)
    br.compare({T_TOR: 3}, {})                                    # calling compare touches no arm state
    assert arm.current_row(ACCT, CAT, legacy_db_path=leg) is None


# ── a mixed portfolio: match + journal_only + kalshi_only + count_mismatch at once ──
def test_mixed_portfolio_classifies_each_ticker(tmp_path):
    def seed(c):
        _seed(c, T_TOR, "yes", 3)      # will MATCH (kalshi +3)
        _seed(c, T_SEA, "yes", 2)      # journal_only (kalshi flat)
        _seed(c, T_TOT, "no", 4)       # count_mismatch (journal -4 vs kalshi -1)
    kalshi = [_kpos(T_TOR, 3), _kpos(T_TOT, -1), _kpos("KXMLBGAME-OTHER-ZZZ", 5)]  # last = kalshi_only
    res, _ = _reconcile(tmp_path, seed, kalshi)
    by = {d.ticker: d.classification for d in res.diffs}
    assert T_TOR not in by                                        # matched -> not a diff
    assert by[T_SEA] == br.JOURNAL_ONLY
    assert by[T_TOT] == br.COUNT_MISMATCH
    assert by["KXMLBGAME-OTHER-ZZZ"] == br.KALSHI_ONLY
    assert res.reconciled is False and res.latched is True and len(res.diffs) == 3


# ── STRUCTURAL: injected fetcher, NO latch-off switch, this module holds NO broker ──
def test_structural_no_broker_no_latch_off_switch():
    sig = inspect.signature(br.reconcile_account).parameters
    assert "fetch_positions" in sig                              # the read seam is INJECTED
    assert "broker" not in sig and "place_fn" not in sig         # no broker object, no placer
    assert "latch_on_mismatch" not in sig                        # NO latch-off footgun on the production entry point
    for banned in ("KalshiLiveBroker", "KalshiBroker", "place_order", "cancel_order",
                   "httpx", "requests", "asyncio", "pykalshi"):
        assert banned not in dir(br), banned                     # none of them in this module's namespace


# ── M3 (2026-09-02): WHOLE-ACCOUNT latch -- a mismatch/read-failure disarms EVERY active category on the shared
# keypair, not just the reconciled one. The comparison is already account-wide; only the latch target widens.
# boot_reconcile is sport-agnostic (string-keyed on UPPER(ticker)), so a real UFC ticker exercises it directly. ──
T_UFC = "KXUFCFIGHT-26SEP05HOOPAR-HOO"


def _arm_cats(leg, *cats):
    arm.arm(global_=True, require_latch_clear=True, legacy_db_path=leg)
    for c in cats:
        arm.arm(ACCT, c, require_latch_clear=True, legacy_db_path=leg)


def test_m3_whole_account_mismatch_latches_ALL_categories(tmp_path):
    """A KALSHI_ONLY ufc position (in the book, absent from the account-wide journal) is a whole-book mismatch.
    With latch_categories=[mlb,ufc] BOTH categories latch -- closes the 2nd-category-on-one-account missed-latch."""
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_cats(leg, "mlb", "ufc")
    with db.connect(p) as conn:
        _seed(conn, T_TOR, "yes", 3, category="mlb"); conn.commit()                 # journal: mlb only
        res = br.reconcile_account(conn, ACCT, "mlb",                                # book: mlb + an un-journaled ufc
                                   fetch_positions=lambda: [_kpos(T_TOR, 3), _kpos(T_UFC, 2)],
                                   legacy_db_path=leg, latch_categories=["mlb", "ufc"])
    assert res.reconciled is False and res.latched is True and set(res.latched_categories) == {"mlb", "ufc"}
    for c in ("mlb", "ufc"):                                                         # ★ the SIBLING ufc latched too
        assert arm.is_armed(ACCT, c, legacy_db_path=leg) is False
        assert arm.current_row(ACCT, c, legacy_db_path=leg)["auto_trigger"] == arm.AUTO_BOOT_RECONCILE


def test_m3_clean_two_category_account_does_NOT_false_latch(tmp_path):
    """The account-wide journal read means a ufc position present in BOTH the journal and the book reconciles as
    MATCH -- widening the latch introduces NO false latch on a clean two-category account."""
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_cats(leg, "mlb", "ufc")
    with db.connect(p) as conn:
        _seed(conn, T_TOR, "yes", 3, category="mlb")
        _seed(conn, T_UFC, "yes", 2, category="ufc"); conn.commit()                 # account-wide journal: mlb + ufc
        res = br.reconcile_account(conn, ACCT, "mlb",                                # book matches BOTH
                                   fetch_positions=lambda: [_kpos(T_TOR, 3), _kpos(T_UFC, 2)],
                                   legacy_db_path=leg, latch_categories=["mlb", "ufc"])
    assert res.reconciled is True and res.latched is False and res.latched_categories == ()
    for c in ("mlb", "ufc"):
        assert arm.is_armed(ACCT, c, legacy_db_path=leg) is True                     # ★ NO false latch


def test_m3_read_failure_latches_ALL_categories(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_cats(leg, "mlb", "ufc")
    def _boom():
        raise RuntimeError("venue read down")
    with db.connect(p) as conn:
        _seed(conn, T_TOR, "yes", 3, category="mlb"); conn.commit()
        res = br.reconcile_account(conn, ACCT, "mlb", fetch_positions=_boom, legacy_db_path=leg,
                                   latch_categories=["mlb", "ufc"])
    assert res.reconciled is False and res.latched is True and set(res.latched_categories) == {"mlb", "ufc"}
    for c in ("mlb", "ufc"):
        assert arm.is_armed(ACCT, c, legacy_db_path=leg) is False


def test_m3_default_latch_categories_is_backward_compatible(tmp_path):
    """latch_categories=None -> only [category] latches (the pre-M3 default; direct callers/tests unchanged)."""
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_cats(leg, "mlb", "ufc")
    with db.connect(p) as conn:
        _seed(conn, T_TOR, "yes", 3, category="mlb"); conn.commit()
        res = br.reconcile_account(conn, ACCT, "mlb", fetch_positions=lambda: [], legacy_db_path=leg)  # journal_only
    assert res.latched is True and res.latched_categories == ("mlb",)
    assert arm.is_armed(ACCT, "mlb", legacy_db_path=leg) is False
    assert arm.is_armed(ACCT, "ufc", legacy_db_path=leg) is True                     # ufc NOT latched (single-category default)
