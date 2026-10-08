"""Acceptance tests for the settlement-close CLASS handling (2026-10-08).

Calls the REAL settlement.book_settlements + the REAL boot_reconcile.compare/journal_signed_positions against a
real sqlite journal -- not py_compile, not import, not a dry-run (a NameError inside a function has slipped all
three here before). Expected P&L values are HAND-LITERAL (independent arithmetic), never taken from the code
under test, so no test can grade itself.

Fixture position (every case): 5 YES contracts @ fill_price 0.46, fee 0.087 -> entry_cost 2.387, avg_cost 0.4774.
  scalar value=45 ($0.45/ct): realized = 5*0.45 - 2.387 = -0.137
  yes (won):                  realized = 5*1.00 - 2.387 =  2.613
  no  (yes-leg lost):         realized = 5*0.00 - 2.387 = -2.387
  void (refund=avg_cost):     realized = 5*0.4774 - 2.387 = 0.0
  absent value (WORTHLESS):   realized = 5*0.00 - 2.387 = -2.387  (bias-DOWN, NOT 0)

Runnable two ways: `pytest` OR `python test_settlement_close_class.py` (prints PASS/FAIL + counts, exit!=0 on fail).
"""
import os, sys, sqlite3, logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from trading_corp.prediction_markets import settlement as S
from trading_corp.prediction_markets import boot_reconcile as BR

TICK = "KXTEST-26OCT08AB-A"
ACCT, CAT, WALLET, CID, OIDX = "kalshi_test", "cs2", "0xwhale", "0xcond", 0

_DDL = """
CREATE TABLE pm_subdivision_order (
  id INTEGER PRIMARY KEY, account_id TEXT, category TEXT, wallet TEXT, condition_id TEXT, outcome_index INTEGER,
  ticker TEXT, outcome_leg TEXT, is_exit INTEGER DEFAULT 0, fill_count REAL, fill_price REAL, fee REAL,
  outcome_status TEXT, close_source TEXT, realized_pnl REAL, won INTEGER, settled_ts INTEGER,
  dry_run INTEGER DEFAULT 0, submitted_ts INTEGER, response_ts INTEGER)
"""

def _conn():
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row; c.execute(_DDL); return c

def _entry(c, count=5.0, price=0.46, fee=0.087, leg="yes"):
    c.execute("INSERT INTO pm_subdivision_order (account_id,category,wallet,condition_id,outcome_index,ticker,"
              "outcome_leg,is_exit,fill_count,fill_price,fee,outcome_status,dry_run,submitted_ts,response_ts) "
              "VALUES (?,?,?,?,?,?,?,0,?,?,?,'filled',0,1,1)",
              (ACCT,CAT,WALLET,CID,OIDX,TICK,leg,count,price,fee)); c.commit()

def _rec(result, value_cents=None, settled_ts=1000):
    return S.SettlementRecord(ticker=TICK, event_ticker="KXTEST-26OCT08AB", result=result,
                              settled_ts=settled_ts, revenue=None, value_cents=value_cents)

def _closes(c):
    return c.execute("SELECT * FROM pm_subdivision_order WHERE is_exit=1 ORDER BY id").fetchall()

def _approx(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


def test_scalar_books_at_value():
    c = _conn(); _entry(c)
    r = S.book_settlements(c, ACCT, CAT, [_rec("scalar", 45)], now_ts=2000)
    cl = _closes(c)
    assert len(cl) == 1 and r["n_booked"] == 1, r
    row = cl[0]
    assert row["close_source"] == "settlement_scalar", row["close_source"]
    assert row["won"] is None, row["won"]
    assert _approx(row["fill_price"], 0.45), row["fill_price"]
    assert _approx(row["realized_pnl"], -0.137), row["realized_pnl"]
    assert row["fill_count"] == 5.0 and row["is_exit"] == 1 and row["dry_run"] == 0
    return "scalar -> fill_price 0.45, won NULL, realized -0.137, close_source settlement_scalar"


def test_yes_no_void_unchanged():
    # YES (won)
    c = _conn(); _entry(c); S.book_settlements(c, ACCT, CAT, [_rec("yes", 100)], now_ts=2000)
    y = _closes(c)[0]
    assert y["close_source"] == "settlement" and y["won"] == 1 and _approx(y["fill_price"],1.0) and _approx(y["realized_pnl"],2.613), dict(y)
    # NO (yes-leg lost)
    c = _conn(); _entry(c); S.book_settlements(c, ACCT, CAT, [_rec("no", 0)], now_ts=2000)
    n = _closes(c)[0]
    assert n["close_source"] == "settlement" and n["won"] == 0 and _approx(n["fill_price"],0.0) and _approx(n["realized_pnl"],-2.387), dict(n)
    # VOID (refund; NEVER fired in production -- this is its first exercise)
    c = _conn(); _entry(c); S.book_settlements(c, ACCT, CAT, [_rec("void")], now_ts=2000)
    v = _closes(c)[0]
    assert v["close_source"] == "settlement_void" and v["won"] is None and _approx(v["fill_price"],0.4774) and _approx(v["realized_pnl"],0.0), dict(v)
    return "yes=2.613/won1/settlement ; no=-2.387/won0/settlement ; void=0.0/wonNULL/settlement_void (void: first-ever run)"


def test_unknown_fails_loud_and_books_flat():
    c = _conn(); _entry(c)
    cap = []
    h = logging.Handler(); h.emit = lambda rec: cap.append(rec.getMessage()); S._LOG.addHandler(h); S._LOG.setLevel(logging.WARNING)
    try:
        r = S.book_settlements(c, ACCT, CAT, [_rec("refund", 30)], now_ts=2000)
    finally:
        S._LOG.removeHandler(h)
    cl = _closes(c)
    assert len(cl) == 1, r
    row = cl[0]
    assert row["close_source"] == "settlement_refund", row["close_source"]   # sanitised, queryable
    assert row["won"] is None
    assert _approx(row["fill_price"], 0.30)                                   # booked at value/100 (yes leg)
    assert r["n_nonstandard"] == 1 and r["nonstandard"][0]["market_result"] == "refund", r
    assert any("NON-STANDARD" in m and TICK in m for m in cap), cap          # fail LOUD
    # journal is NOT left silently open -> reconcile clean after
    j = BR.journal_signed_positions(c, ACCT)
    assert BR.compare(j, {}) == [], j                                        # netted flat -> no latch
    return "unknown 'refund' -> booked flat @0.30, close_source settlement_refund, WARN emitted, n_nonstandard=1, reconcile clean"


def test_absent_value_books_worthless_not_avgcost():
    c = _conn(); _entry(c)
    r = S.book_settlements(c, ACCT, CAT, [_rec("mystery", None)], now_ts=2000)   # value ABSENT
    row = _closes(c)[0]
    assert row["close_source"] == "settlement_mystery"
    assert _approx(row["fill_price"], 0.0), row["fill_price"]                 # WORTHLESS, not avg_cost 0.4774
    assert _approx(row["realized_pnl"], -2.387), row["realized_pnl"]         # = -cost basis (bias-DOWN)
    assert row["realized_pnl"] < -2.0 and not _approx(row["realized_pnl"], 0.0)  # explicitly NOT the void (0.0) direction
    return "absent value -> fill_price 0.0, realized -2.387 (bias-DOWN, NOT avg_cost/0.0)"


def test_idempotent_rerun():
    c = _conn(); _entry(c)
    S.book_settlements(c, ACCT, CAT, [_rec("scalar", 45)], now_ts=2000)
    r2 = S.book_settlements(c, ACCT, CAT, [_rec("scalar", 45)], now_ts=3000)   # re-run
    assert r2["n_booked"] == 0 and r2["skipped_flat"] >= 1, r2
    assert len(_closes(c)) == 1                                               # no second close row
    return "re-run: n_booked=0, skipped_flat>=1, still exactly 1 close row"


def test_outage_regression_latch_before_clean_after():
    c = _conn(); _entry(c)                                                   # 5 yes open, no close
    j_before = BR.journal_signed_positions(c, ACCT)
    diffs_before = BR.compare(j_before, {})                                  # venue flat (settled) -> journal_only
    assert j_before.get(TICK) == 5 and len(diffs_before) == 1 and diffs_before[0].classification == BR.JOURNAL_ONLY, (j_before, diffs_before)
    # ^ BEFORE the fix (i.e. if we had SKIPPED this scalar) the journal stays open -> this mismatch LATCHES.
    S.book_settlements(c, ACCT, CAT, [_rec("scalar", 45)], now_ts=2000)       # the fix: book it
    j_after = BR.journal_signed_positions(c, ACCT)
    diffs_after = BR.compare(j_after, {})
    assert j_after.get(TICK, 0) == 0 and diffs_after == [], (j_after, diffs_after)   # AFTER: flat -> clean -> no latch
    return "regression: unbooked scalar -> journal_only DIFF (latch); after book -> 0 DIFFS (clean)"


_TESTS = [test_scalar_books_at_value, test_yes_no_void_unchanged, test_unknown_fails_loud_and_books_flat,
          test_absent_value_books_worthless_not_avgcost, test_idempotent_rerun,
          test_outage_regression_latch_before_clean_after]

if __name__ == "__main__":
    npass = 0; nfail = 0
    for t in _TESTS:
        try:
            msg = t(); npass += 1; print("PASS %-48s %s" % (t.__name__, msg))
        except Exception as e:
            nfail += 1; print("FAIL %-48s %r" % (t.__name__, e))
    print("\nRESULT: %d passed, %d failed, of %d" % (npass, nfail, len(_TESTS)))
    sys.exit(1 if nfail else 0)
