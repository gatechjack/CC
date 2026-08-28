"""Stage 3 R6 -- the three farm actions (promote-to-watchlist / demote / promote-to-live) + detach.

Proves the mutation SEMANTICS and -- load-bearing -- the THREE-BASES INVARIANT: each action moves a pair between
LISTS (pm_watchlist funnel / attachment) and leaves the OTHER data bases UNTOUCHED. Demote PRESERVES paper (F-5).
Every action is idempotent; promote-to-live joins ON CATEGORY; detach reverses it. No action reaches execution.
"""
import inspect
import sqlite3

from trading_corp.prediction_markets import db, farm, farm_actions

WALLET = "0x16bb9951a36fce71e2ef57890b786145e0ba8492"
NOW = 1787900000


def _seed(tmp_path):
    """A schema-head PM DB with: a candidate + a pinned pair; a completed-stats row; two paper rows; an account +
    a sub-division. So each action can be shown to touch ONE list and leave the other two bases intact. Returns a
    LONG-LIVED plain connection (db.connect is a context manager -- calling .__enter__() on a throwaway would GC-
    close it immediately; a plain sqlite3 conn stays open for the whole test)."""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    # funnel: one CANDIDATE (promotable), one PINNED (demotable / attachable)
    conn.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts) VALUES(?,?,?,1,?,?)",
                 (WALLET, "mlb", farm.PINNED, NOW, NOW))
    conn.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts) VALUES(?,?,?,1,?,?)",
                 ("0xcand", "mlb", farm.CANDIDATE, NOW, NOW))
    # COMPLETED base (Prospect basis) -- must be untouched by every action
    conn.execute("INSERT INTO pm_category_stats(wallet,category,n_resolved,wins,losses,updated_ts) VALUES(?,?,?,?,?,?)",
                 (WALLET, "mlb", 10, 6, 4, NOW))
    # PAPER base (Watchlist basis) -- must be untouched (F-5) by demote
    for cid in ("0xpa", "0xpb"):
        conn.execute("INSERT INTO pm_paper_trade(wallet,category,condition_id,outcome_index,entry_observed_ts,"
                     "status,opened_ts) VALUES(?,?,?,0,?,?,?)", (WALLET, "mlb", cid, NOW, "open", NOW))
    # LIVE base -- an account + a sub-division to attach to
    conn.execute("INSERT INTO pm_account(account_id,venue,secret_ref,label,active,created_ts) "
                 "VALUES('kalshi_jack','kalshi','KALSHI','Jack (KALSHI)',1,?)", (NOW,))
    conn.execute("INSERT INTO pm_subdivision(account_id,category,label,market_types,sizing_mode,fixed_stake_usd,"
                 "active,created_ts) VALUES('kalshi_jack','mlb','Jack MLB','moneyline,total,spread','fixed',5.0,1,?)", (NOW,))
    conn.commit()
    return conn


def _counts(conn):
    return {
        "completed": conn.execute("SELECT COUNT(*) FROM pm_category_stats").fetchone()[0],
        "completed_sig": conn.execute("SELECT COALESCE(SUM(n_resolved+wins+losses),0) FROM pm_category_stats").fetchone()[0],
        "paper": conn.execute("SELECT COUNT(*) FROM pm_paper_trade").fetchone()[0],
        "attach": conn.execute("SELECT COUNT(*) FROM pm_subdivision_attachment WHERE active=1").fetchone()[0],
        "orders": conn.execute("SELECT COUNT(*) FROM pm_subdivision_order").fetchone()[0],
    }


def _status(conn, wallet, category):
    r = conn.execute("SELECT status FROM pm_watchlist WHERE wallet=? AND category=?", (wallet, category)).fetchone()
    return r["status"] if r else None


# ── promote-to-watchlist: flips ONLY the funnel; other two bases untouched ───────────────────
def test_promote_to_watchlist_touches_only_funnel(tmp_path):
    conn = _seed(tmp_path)
    before = _counts(conn)
    res = farm_actions.promote_to_watchlist(conn, "0xcand", "mlb", NOW)
    assert res["changed"] is True and _status(conn, "0xcand", "mlb") == farm.PINNED
    after = _counts(conn)
    assert after == before          # completed, paper, attachment, orders ALL unchanged (only pm_watchlist.status flipped)
    # idempotent: promoting an already-pinned pair is a no-op with a reason
    res2 = farm_actions.promote_to_watchlist(conn, "0xcand", "mlb", NOW)
    assert res2["changed"] is False and res2["reason"] == "already_pinned"
    # a non-candidate / absent pair is an honest no-op
    assert farm_actions.promote_to_watchlist(conn, "0xnope", "mlb", NOW)["reason"] == "not_a_candidate"


# ── demote: flips ONLY the funnel; pm_paper_trade PRESERVED (F-5, the STOP) ───────────────────
def test_demote_preserves_paper_F5_and_touches_only_funnel(tmp_path):
    conn = _seed(tmp_path)
    before = _counts(conn)
    res = farm_actions.demote_to_prospect(conn, WALLET, "mlb", NOW)
    assert res["changed"] is True and _status(conn, WALLET, "mlb") == farm.CANDIDATE
    after = _counts(conn)
    assert after["paper"] == before["paper"] == 2       # ★ F-5: paper rows SURVIVE a demote (the STOP condition)
    assert after == before                              # completed, attachment, orders also untouched
    # idempotent
    assert farm_actions.demote_to_prospect(conn, WALLET, "mlb", NOW)["reason"] == "already_candidate"
    assert farm_actions.demote_to_prospect(conn, "0xnope", "mlb", NOW)["reason"] == "not_pinned"


# ── promote-to-live: writes ONLY the attachment; joined ON CATEGORY; NEVER an order ───────────
def test_promote_to_live_attaches_only_and_joins_on_category(tmp_path):
    conn = _seed(tmp_path)
    before = _counts(conn)
    res = farm_actions.promote_to_live(conn, "kalshi_jack", "mlb", WALLET, NOW)
    assert res["ok"] and res["changed"] and res["reason"] == "attached"
    after = _counts(conn)
    assert after["attach"] == 1 and after["orders"] == before["orders"]     # ★ an ATTACHMENT, never an order
    assert after["completed"] == before["completed"] and after["paper"] == before["paper"]   # other bases untouched
    assert after["completed_sig"] == before["completed_sig"]
    assert _status(conn, WALLET, "mlb") == farm.PINNED                      # the pair STAYS pinned (all three at once)
    # idempotent: a re-attach keeps ONE row AND reports changed=False (consistent with promote/demote no-ops)
    res2 = farm_actions.promote_to_live(conn, "kalshi_jack", "mlb", WALLET, NOW)
    assert res2["changed"] is False and res2["reason"] == "already_attached"
    assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_attachment").fetchone()[0] == 1


def test_promote_to_live_category_join_and_missing_subdivision(tmp_path):
    conn = _seed(tmp_path)
    # a whale pinned in UFC (not mlb) cannot attach to the mlb sub-division -> the join refuses
    conn.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts) VALUES('0xufc','ufc',?,1,?,?)",
                 (farm.PINNED, NOW, NOW)); conn.commit()
    assert farm_actions.promote_to_live(conn, "kalshi_jack", "mlb", "0xufc", NOW)["reason"] == "whale_not_pinned_in_category"
    # a sub-division that does not exist -> no_such_subdivision (no write)
    assert farm_actions.promote_to_live(conn, "kalshi_jack", "nba", WALLET, NOW)["reason"] == "no_such_subdivision"
    # a non-pinned (candidate) whale cannot go live
    assert farm_actions.promote_to_live(conn, "kalshi_jack", "mlb", "0xcand", NOW)["reason"] == "whale_not_pinned_in_category"
    assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_attachment").fetchone()[0] == 0   # nothing written


def test_same_whale_category_attaches_to_multiple_subdivisions(tmp_path):
    conn = _seed(tmp_path)
    # a SECOND mlb sub-division on a different account
    conn.execute("INSERT INTO pm_account(account_id,venue,label,active,created_ts) VALUES('kalshi_two','kalshi','Two',1,?)", (NOW,))
    conn.execute("INSERT INTO pm_subdivision(account_id,category,label,sizing_mode,fixed_stake_usd,active,created_ts) "
                 "VALUES('kalshi_two','mlb','Two MLB','fixed',5.0,1,?)", (NOW,)); conn.commit()
    assert farm_actions.promote_to_live(conn, "kalshi_jack", "mlb", WALLET, NOW)["changed"]
    assert farm_actions.promote_to_live(conn, "kalshi_two", "mlb", WALLET, NOW)["changed"]
    assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_attachment WHERE wallet=? AND active=1", (WALLET,)).fetchone()[0] == 2


# ── detach: promote-to-live's inverse, reversible ────────────────────────────
def test_detach_reverses_and_is_reversible(tmp_path):
    conn = _seed(tmp_path)
    farm_actions.promote_to_live(conn, "kalshi_jack", "mlb", WALLET, NOW)           # original added_ts = NOW
    res = farm_actions.detach_from_live(conn, "kalshi_jack", "mlb", WALLET, NOW + 100)
    assert res["changed"] and res["reason"] == "detached"
    assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_attachment WHERE active=1").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_attachment").fetchone()[0] == 1   # row SURVIVES (reversible)
    # idempotent detach
    assert farm_actions.detach_from_live(conn, "kalshi_jack", "mlb", WALLET, NOW + 100)["reason"] == "not_attached"
    # re-attach restores it (active=1), still ONE row, reports changed=True (reactivation IS a change)
    assert farm_actions.promote_to_live(conn, "kalshi_jack", "mlb", WALLET, NOW + 200)["changed"] is True
    row = conn.execute("SELECT active, added_ts, removed_ts FROM pm_subdivision_attachment "
                       "WHERE account_id='kalshi_jack' AND category='mlb' AND wallet=?", (WALLET,)).fetchone()
    assert row["active"] == 1 and row["removed_ts"] is None
    assert row["added_ts"] == NOW                                                   # ★ original attach ts PRESERVED across detach->re-attach


def test_off_funnel_candidate_not_promotable(tmp_path):
    conn = _seed(tmp_path)
    conn.execute("UPDATE pm_watchlist SET active=0 WHERE wallet='0xcand'"); conn.commit()
    res = farm_actions.promote_to_watchlist(conn, "0xcand", "mlb", NOW)
    assert res["changed"] is False and res["reason"] == "off_funnel"


# ── structural: no farm action can reach the execution path ──────────────────
def test_farm_actions_cannot_reach_execution(tmp_path):
    src = inspect.getsource(farm_actions)
    assert "KalshiLiveBroker" not in src and "place_order" not in src and "pm_subdivision_order" not in src
    assert "kalshi_live" not in src and "execution" not in src
    # and no order row is ever created by any action (proven live)
    conn = _seed(tmp_path)
    farm_actions.promote_to_watchlist(conn, "0xcand", "mlb", NOW)
    farm_actions.demote_to_prospect(conn, WALLET, "mlb", NOW)
    conn.execute("UPDATE pm_watchlist SET status=? WHERE wallet=?", (farm.PINNED, WALLET)); conn.commit()
    farm_actions.promote_to_live(conn, "kalshi_jack", "mlb", WALLET, NOW)
    assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_order").fetchone()[0] == 0
