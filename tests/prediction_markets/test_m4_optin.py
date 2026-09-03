"""M4 (2026-09-03): the per-account MULTI-CATEGORY opt-in that relaxes the driver guard. FAIL-CLOSED, OFF by default:
with no opt-in the roster returns exactly one category per account (the 2nd refused) -- BYTE-IDENTICAL to pre-M4. An
account gets a 2nd category ONLY because `pm_account.multi_category_ok=1` (migration 019) says so, and then main.py's
by-account grouping puts both on ONE Option-C task. Tolerant of a pre-019 schema (column absent -> 0 -> still refuse)."""
from trading_corp.prediction_markets import db, driver_roster as R


def _row(aid, cat, optin=0, secret="sr"):
    return {"account_id": aid, "category": cat, "secret_ref": secret, "multi_category_ok": optin}


def test_m4_default_off_refuses_second_category_byte_identical():
    roster = [_row("jack", "mlb", 0), _row("jack", "ufc", 0), _row("karen", "mlb", 0)]
    spawn, skips = R.plan_driver_tasks(roster, {"jack", "karen"})
    assert spawn == [{"account_id": "jack", "category": "mlb"}, {"account_id": "karen", "category": "mlb"}]
    assert [(s["account_id"], s["category"], s["reason"]) for s in skips] == [("jack", "ufc", "second_subdivision_on_account")]


def test_m4_optin_groups_second_category_onto_one_task():
    roster = [_row("jack", "mlb", 1), _row("jack", "ufc", 1), _row("karen", "mlb", 0)]
    spawn, skips = R.plan_driver_tasks(roster, {"jack", "karen"})
    assert skips == []
    by_acct = {}
    for s in spawn:
        by_acct.setdefault(s["account_id"], []).append(s["category"])          # main.py's by-account grouping
    assert by_acct == {"jack": ["mlb", "ufc"], "karen": ["mlb"]}               # jack -> ONE task iterating [mlb, ufc]


def test_m4_optin_is_per_account_not_global():
    # jack opted in, karen NOT -> jack groups both, karen's 2nd category is still refused
    roster = [_row("jack", "mlb", 1), _row("jack", "ufc", 1), _row("karen", "mlb", 0), _row("karen", "ufc", 0)]
    spawn, skips = R.plan_driver_tasks(roster, {"jack", "karen"})
    got = {(s["account_id"], s["category"]) for s in spawn}
    assert got == {("jack", "mlb"), ("jack", "ufc"), ("karen", "mlb")}
    assert [(s["account_id"], s["category"]) for s in skips] == [("karen", "ufc")]


def test_m4_absent_optin_key_fails_closed():
    # a roster row WITHOUT the multi_category_ok key (old caller / pre-migration) -> .get() None -> refuse
    roster = [{"account_id": "jack", "category": "mlb", "secret_ref": "s"},
              {"account_id": "jack", "category": "ufc", "secret_ref": "s"}]
    spawn, skips = R.plan_driver_tasks(roster, {"jack"})
    assert [s["category"] for s in spawn] == ["mlb"]
    assert skips[0]["reason"] == "second_subdivision_on_account"


def test_m4_no_keys_still_fails_closed_first():
    roster = [_row("jack", "mlb", 1), _row("jack", "ufc", 1)]
    spawn, skips = R.plan_driver_tasks(roster, set())                          # no keys resolved
    assert spawn == [] and {s["reason"] for s in skips} == {"no_keys"}


def test_m4_migration_019_column_and_tolerant_helper(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(pm_account)").fetchall()]
        assert "multi_category_ok" in cols                                     # migration 019 added the column
        assert R._column_exists(conn, "pm_account", "multi_category_ok") is True
        assert R._column_exists(conn, "pm_account", "nonexistent_col") is False   # tolerant -> False, no raise
        assert R._column_exists(conn, "nonexistent_table", "x") is False          # tolerant -> False, no raise


def test_m4_active_driver_subdivisions_carries_optin(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_account (account_id, active, secret_ref, multi_category_ok) VALUES (?,1,?,1)",
                     ("kalshi_jack", "kv-jack"))
        for c in ("mlb", "ufc"):
            conn.execute("INSERT INTO pm_subdivision (account_id, category, active) VALUES (?,?,1)", ("kalshi_jack", c))
            conn.execute("INSERT INTO pm_subdivision_attachment (account_id, category, wallet, active) VALUES (?,?,?,1)",
                         ("kalshi_jack", c, "0xW"))
        conn.commit()
        roster = R.active_driver_subdivisions(conn)
    assert len(roster) == 2 and all(r["multi_category_ok"] == 1 for r in roster)   # opt-in carried on every row
    spawn, _ = R.plan_driver_tasks(roster, {"kalshi_jack"})
    assert {(s["account_id"], s["category"]) for s in spawn} == {("kalshi_jack", "mlb"), ("kalshi_jack", "ufc")}
