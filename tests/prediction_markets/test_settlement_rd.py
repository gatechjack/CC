"""R-d settlement-close. Proves: parse the RAW /portfolio/settlements payload; book a settled position as a
terminal-close row (is_exit=1, filled, fill_count=held) with the Cubs realized -$0.6084; ★ THE TEST THAT MATTERS --
boot_reconcile comes up CLEAN after booking (it LATCHED before); /live drops the settled ticker; idempotency;
double-close-avoidance; per-wallet; won/lost/void. The Cubs fixture is the live case: 1 YES @0.60 + fee 0.0084,
result=no -> settled $0 -> realized -0.6084."""
import calendar
import sqlite3

import pytest

from trading_corp.prediction_markets import db, settlement as S, boot_reconcile as BR, subdivision

ACCT, CAT = "kalshi_jack", "mlb"
NOW = 1788200000
SDT = "0x16bb9951a36fce71e2ef57890b786145e0ba8492"
CUBS = "KXMLBGAME-26AUG301920CINCHC-CHC"
CUBS_EVENT = "KXMLBGAME-26AUG301920CINCHC"
SETTLED_TS = calendar.timegm((2026, 8, 31, 2, 44, 41, 0, 0, 0))     # 2026-08-31T02:44:41Z (the real Cubs settled_time)


def _legacy(tmp_path):
    p = str(tmp_path / "trading_corp.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE agent_state (agent TEXT NOT NULL, key TEXT NOT NULL, value_json TEXT NOT NULL, "
              "updated_ts TEXT NOT NULL, PRIMARY KEY (agent, key))")
    c.commit(); c.close()
    return p


def _entry(conn, ticker, leg, count, price, fee, *, wallet=SDT):
    conn.execute("INSERT INTO pm_subdivision_order (account_id,category,wallet,ticker,outcome_leg,is_exit,"
                 "fill_count,fill_price,fee,outcome_status,dry_run,submitted_ts,response_ts) "
                 "VALUES (?,?,?,?,?,0,?,?,?,'filled',0,?,?)",
                 (ACCT, CAT, wallet, ticker, leg, count, price, fee, NOW, NOW)); conn.commit()


def _raw(ticker=CUBS, event=CUBS_EVENT, result="no", settled="2026-08-31T02:44:41.420484Z", revenue=0):
    return {"settlements": [{"ticker": ticker, "event_ticker": event, "market_result": result,
                             "settled_time": settled, "revenue": revenue}]}


# ── parse ─────────────────────────────────────────────────────────────────────
def test_parse_settlements_reads_raw_fields_and_failsafe():
    recs = S.parse_settlements(_raw())
    assert len(recs) == 1
    r = recs[0]
    assert r.ticker == CUBS and r.event_ticker == CUBS_EVENT and r.result == "no" and r.revenue == 0.0
    assert r.settled_ts == SETTLED_TS            # 2026-08-31T02:44:41Z (seconds; sub-second dropped)
    assert S.parse_settlements(None) == [] and S.parse_settlements({"settlements": "x"}) == []
    assert S.parse_settlements({"settlements": [42, {"ticker": "T", "market_result": "yes"}]})[0].ticker == "T"


def test_iso_to_unix():
    assert S._iso_to_unix("2026-08-31T02:44:41.420484Z") == SETTLED_TS
    assert S._iso_to_unix(SETTLED_TS) == SETTLED_TS
    assert S._iso_to_unix(None) is None and S._iso_to_unix("garbage") is None


# ── the Cubs fixture: book the settlement ──────────────────────────────────────
def test_book_cubs_loss_realized_minus_6084(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _entry(conn, CUBS, "yes", 1, 0.60, 0.0084)                       # the live Cubs entry
        summ = S.book_settlements(conn, ACCT, CAT, S.parse_settlements(_raw()), now_ts=NOW)
        assert summ["n_booked"] == 1
        r = conn.execute("SELECT is_exit,outcome_status,fill_count,fill_price,fee,close_source,realized_pnl,won,"
                         "settled_ts,wallet FROM pm_subdivision_order WHERE close_source IS NOT NULL").fetchone()
    assert r["is_exit"] == 1 and r["outcome_status"] == "filled" and r["fill_count"] == 1.0
    assert r["fill_price"] == 0.0 and r["fee"] == 0.0 and r["close_source"] == "settlement" and r["won"] == 0
    assert abs(r["realized_pnl"] - (-0.6084)) < 1e-9                     # 0 proceeds - (0.60 + 0.0084)
    assert r["settled_ts"] == SETTLED_TS and r["wallet"] == SDT


# ── ★ THE TEST THAT MATTERS: boot_reconcile CLEAN after booking (LATCHED before) ──
def test_boot_reconcile_latches_before_and_is_clean_after_booking(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _entry(conn, CUBS, "yes", 1, 0.60, 0.0084)
        # BEFORE: journal holds +1, venue is FLAT (settled) -> JOURNAL_ONLY mismatch -> LATCH (R-b, the current gap)
        res_before = BR.reconcile_account(conn, ACCT, CAT, fetch_positions=lambda: [], legacy_db_path=leg)
        assert res_before.reconciled is False and res_before.latched is True
        # book the settlement (R-d) -> journal goes flat
        S.book_settlements(conn, ACCT, CAT, S.parse_settlements(_raw()), now_ts=NOW)
        assert BR.journal_signed_positions(conn, ACCT) == {}             # journal now flat on Cubs
        # AFTER: journal flat + venue flat -> CLEAN, no latch. This is the whole point of the combined deploy.
        res_after = BR.reconcile_account(conn, ACCT, CAT, fetch_positions=lambda: [], legacy_db_path=leg)
    assert res_after.reconciled is True and res_after.latched is False


# ── /live drops the settled ticker ─────────────────────────────────────────────
def test_live_positions_drops_a_settled_position(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _entry(conn, CUBS, "yes", 1, 0.60, 0.0084)
        assert any(x["ticker"] == CUBS for x in subdivision.live_positions(conn, ACCT, CAT))   # held before
        S.book_settlements(conn, ACCT, CAT, S.parse_settlements(_raw()), now_ts=NOW)
        assert not any(x["ticker"] == CUBS for x in subdivision.live_positions(conn, ACCT, CAT))  # gone after


# ── idempotency + double-close avoidance ───────────────────────────────────────
def test_booking_is_idempotent(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _entry(conn, CUBS, "yes", 1, 0.60, 0.0084)
        assert S.book_settlements(conn, ACCT, CAT, S.parse_settlements(_raw()), now_ts=NOW)["n_booked"] == 1
        second = S.book_settlements(conn, ACCT, CAT, S.parse_settlements(_raw()), now_ts=NOW)
        assert second["n_booked"] == 0 and second["skipped_flat"] == 1   # net-open already 0 -> not re-booked
        assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_order WHERE close_source IS NOT NULL").fetchone()[0] == 1


def test_whale_exit_already_closed_then_settlement_is_noop(tmp_path):
    # exit-then-settle: a whale-exit already netted the position flat -> the settlement-scan sees net-open 0 -> skip
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _entry(conn, CUBS, "yes", 1, 0.60, 0.0084)
        conn.execute("INSERT INTO pm_subdivision_order (account_id,category,wallet,ticker,outcome_leg,is_exit,"
                     "fill_count,outcome_status,dry_run,submitted_ts,response_ts) VALUES (?,?,?,?,?,1,1,'filled',0,?,?)",
                     (ACCT, CAT, SDT, CUBS, "yes", NOW, NOW)); conn.commit()   # a whale-exit already closed it
        summ = S.book_settlements(conn, ACCT, CAT, S.parse_settlements(_raw()), now_ts=NOW)
    assert summ["n_booked"] == 0 and summ["skipped_flat"] == 1


# ── per-wallet + won/lost/void ─────────────────────────────────────────────────
def test_per_wallet_books_each_whale_separately(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    A, B = "0xAAA", "0xBBB"
    with db.connect(p) as conn:
        _entry(conn, CUBS, "yes", 1, 0.60, 0.0084, wallet=A)             # A: 1 @ 0.60
        _entry(conn, CUBS, "yes", 2, 0.50, 0.01, wallet=B)               # B: 2 @ 0.50
        summ = S.book_settlements(conn, ACCT, CAT, S.parse_settlements(_raw()), now_ts=NOW)
        rows = {r["wallet"]: r for r in conn.execute(
            "SELECT wallet,fill_count,realized_pnl FROM pm_subdivision_order WHERE close_source IS NOT NULL")}
    assert summ["n_booked"] == 2
    assert rows[A]["fill_count"] == 1.0 and abs(rows[A]["realized_pnl"] - (-0.6084)) < 1e-9
    assert rows[B]["fill_count"] == 2.0 and abs(rows[B]["realized_pnl"] - (-(2*0.50 + 0.01))) < 1e-9   # lost 2 @ 0.50 + fee


def test_won_and_void(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    T2 = "KXMLBGAME-26AUG301920CINCHC-CIN"     # a DIFFERENT ticker so it does not collide with CUBS
    with db.connect(p) as conn:
        # a YES that WON (result=yes): realized = 1*1 - 0.6084 = +0.3916
        _entry(conn, T2, "yes", 1, 0.60, 0.0084)
        S.book_settlements(conn, ACCT, CAT, S.parse_settlements(_raw(ticker=T2, event="", result="yes", revenue=1.0)), now_ts=NOW)
        w = conn.execute("SELECT won,fill_price,realized_pnl FROM pm_subdivision_order WHERE ticker=? AND close_source IS NOT NULL", (T2,)).fetchone()
        assert w["won"] == 1 and w["fill_price"] == 1.0 and abs(w["realized_pnl"] - 0.3916) < 1e-9
        # a VOID: refund -> realized 0, won NULL, close_source settlement_void
        T3 = "KXMLBGAME-26AUG301920CINCHC-VOID"
        _entry(conn, T3, "yes", 3, 0.40, 0.0, wallet="0xV")
        S.book_settlements(conn, ACCT, CAT, S.parse_settlements(_raw(ticker=T3, event="", result="void")), now_ts=NOW)
        v = conn.execute("SELECT won,close_source,realized_pnl FROM pm_subdivision_order WHERE ticker=? AND close_source IS NOT NULL", (T3,)).fetchone()
    assert v["won"] is None and v["close_source"] == "settlement_void" and abs(v["realized_pnl"]) < 1e-9


def test_unsettled_position_is_left_open(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        _entry(conn, CUBS, "yes", 1, 0.60, 0.0084)
        summ = S.book_settlements(conn, ACCT, CAT, [], now_ts=NOW)       # no settlement records -> nothing booked
    assert summ["n_booked"] == 0 and summ["skipped_no_settlement"] == 1
