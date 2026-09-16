"""Item 2 (2026-09-15): attachment SPAN HISTORY -- the append-only pm_subdivision_attachment_event log written by
farm_actions.promote_to_live/detach_from_live (migration 024), and its span reconstruction. Direct farm_actions
calls + a sqlite fixture (no TestClient -> off the pre-existing UI-render failure surface).

Proves: the single-row pm_subdivision_attachment LOSES the earlier span's DATES on a re-attach (row keeps the
original added_ts, clears removed_ts), but the EVENT LOG recovers every span; a repeat/no-op writes nothing; and
the DRIVER's roster query (pm_subdivision_attachment WHERE active=1) is UNAFFECTED (never reads the event table)."""
from trading_corp.prediction_markets import db, farm, farm_actions

W = "0x16bb9951a36fce71e2ef57890b786145e0ba8492"


def _mk(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_watchlist(wallet,category,status,active,added_ts,updated_ts) VALUES(?,?,?,1,1,1)",
                     (W, "mlb", farm.PINNED))
        conn.execute("INSERT INTO pm_account(account_id,venue,label,active,created_ts) VALUES('kalshi_jack','kalshi','Jack',1,1)")
    return p


def _events(p, wallet=W):
    with db.connect(p) as conn:
        return farm_actions.read_attachment_events(conn, "kalshi_jack", "mlb", wallet)


def test_migration_024_at_head_and_table_empty(tmp_path):
    p = _mk(tmp_path)
    with db.connect(p) as conn:
        assert db.SCHEMA_HEAD == 24
        assert db._current_version(conn) == 24
        assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_attachment_event").fetchone()[0] == 0


def test_attach_writes_one_attach_event(tmp_path):
    p = _mk(tmp_path)
    with db.connect(p) as conn:
        r = farm_actions.promote_to_live(conn, "kalshi_jack", "mlb", W, 1000, actor="jack")
        assert r["ok"] and r["changed"]
    ev = _events(p)
    assert len(ev) == 1
    assert ev[0]["action"] == "attach" and ev[0]["ts"] == 1000
    assert ev[0]["source"] == "promote_to_live" and ev[0]["actor"] == "jack"
    assert ev[0]["wallet"] == W and ev[0]["account_id"] == "kalshi_jack" and ev[0]["category"] == "mlb"


def test_repeat_attach_writes_no_second_event(tmp_path):
    p = _mk(tmp_path)
    with db.connect(p) as conn:
        farm_actions.promote_to_live(conn, "kalshi_jack", "mlb", W, 1000, actor="jack")
    with db.connect(p) as conn:
        r = farm_actions.promote_to_live(conn, "kalshi_jack", "mlb", W, 2000, actor="jack")   # already active
        assert r["reason"] == "already_attached" and not r["changed"]
    assert len(_events(p)) == 1                                    # idempotent repeat -> NO new event


def test_detach_then_reattach_records_two_spans_the_row_cannot(tmp_path):
    p = _mk(tmp_path)
    with db.connect(p) as conn:
        farm_actions.promote_to_live(conn, "kalshi_jack", "mlb", W, 1000, actor="jack")    # span 1 open
    with db.connect(p) as conn:
        farm_actions.detach_from_live(conn, "kalshi_jack", "mlb", W, 2000, actor="jack")    # span 1 close
    with db.connect(p) as conn:
        farm_actions.promote_to_live(conn, "kalshi_jack", "mlb", W, 3000, actor="cli")      # span 2 open (re-attach)
    # ★ the single attachment ROW preserves added_ts=1000 and clears removed_ts -> span-1's dates are LOST from it
    with db.connect(p) as conn:
        row = conn.execute("SELECT added_ts, removed_ts, active FROM pm_subdivision_attachment WHERE wallet=?", (W,)).fetchone()
    assert row["added_ts"] == 1000 and row["active"] == 1 and row["removed_ts"] is None
    # ...but the EVENT LOG has all three -> both spans (1000..2000) closed + (3000..open) are reconstructible
    ev = _events(p)
    assert [e["action"] for e in ev] == ["attach", "detach", "attach"]
    assert [e["ts"] for e in ev] == [1000, 2000, 3000]
    assert ev[2]["actor"] == "cli"                                # the re-attach's actor is captured


def test_noop_detach_writes_no_event(tmp_path):
    p = _mk(tmp_path)
    with db.connect(p) as conn:
        r = farm_actions.detach_from_live(conn, "kalshi_jack", "mlb", W, 500)               # never attached
        assert r["reason"] == "not_attached" and not r["changed"]
    assert len(_events(p)) == 0


def test_driver_roster_query_unaffected_by_event_table(tmp_path):
    # ★ the DRIVER's roster read is pm_subdivision_attachment WHERE active=1; it NEVER touches the event table.
    p = _mk(tmp_path)
    with db.connect(p) as conn:
        farm_actions.promote_to_live(conn, "kalshi_jack", "mlb", W, 1000, actor="jack")
    with db.connect(p) as conn:
        active = conn.execute("SELECT account_id, category, wallet FROM pm_subdivision_attachment WHERE active=1").fetchall()
    assert len(active) == 1 and active[0]["wallet"] == W          # roster sees the attach; the event table is irrelevant to it


def test_reader_all_whales_and_honest_empty(tmp_path):
    p = _mk(tmp_path)
    with db.connect(p) as conn:
        farm_actions.promote_to_live(conn, "kalshi_jack", "mlb", W, 1000, actor="jack")
    with db.connect(p) as conn:
        allw = farm_actions.read_attachment_events(conn, "kalshi_jack", "mlb")              # wallet=None -> all
        none_here = farm_actions.read_attachment_events(conn, "kalshi_jack", "ufc")         # no events -> []
    assert len(allw) == 1 and none_here == []
