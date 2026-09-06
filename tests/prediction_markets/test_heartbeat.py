"""L1 -- the DRIVER LIVENESS heartbeat table + helper. Offline (real sqlite in tmp_path, injected clock).

Proves the pieces that make the monitor honest: the migration creates the tables at head 21; the writers upsert
one row per grain; the age band is BOTH-DIRECTIONS (a FUTURE ts reads STALE, never fresh-forever); read_liveness
starts from the EXPECTED (attachment-gated) set so a NEVER-spawned sub is caught; and the six states classify
correctly -- including the incident shape (8 expected subs, NO heartbeats, past grace -> all NEVER, alarm).
"""
from trading_corp.prediction_markets import db, heartbeat as hb

NOW = 1_800_000_000
MIN = 60


def _db(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    return p


def _seed_expected(conn, subs, *, attach_ts=NOW - 3 * 24 * 3600, account_active=1):
    """Seed the attachment-gated EXPECTED set: `subs` = list of (account, category)."""
    accts = {a for a, _ in subs}
    for a in accts:
        conn.execute("INSERT OR IGNORE INTO pm_account (account_id,venue,secret_ref,label,active,created_ts) "
                     "VALUES (?,?,?,?,?,?)", (a, "kalshi", a, a, account_active, NOW - 9 * 24 * 3600))
    for a, c in subs:
        conn.execute("INSERT OR IGNORE INTO pm_subdivision (account_id,category,active,created_ts) VALUES (?,?,1,?)",
                     (a, c, NOW - 9 * 24 * 3600))
        conn.execute("INSERT OR IGNORE INTO pm_subdivision_attachment (account_id,category,wallet,active,added_ts) "
                     "VALUES (?,?,?,1,?)", (a, c, "0xwhale", attach_ts))
    conn.commit()


# ═══════════════════════════ migration + table shape ═══════════════════════════

def test_migration_020_creates_tables_at_head(tmp_path):
    assert db.SCHEMA_HEAD == 20 and (20, db.MIGRATION_020) in db.MIGRATIONS   # contiguous (contested-020 resolved at deploy)
    with db.connect(_db(tmp_path)) as conn:
        assert hb.table_present(conn)
        assert conn.execute("SELECT COUNT(*) FROM pm_driver_task_heartbeat").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM pm_driver_heartbeat").fetchone()[0] == 0
    db.init_db(str(tmp_path / "pm.db"))                            # idempotent re-run


def test_table_absent_reads_honest_empty(tmp_path):
    p = str(tmp_path / "raw.db")
    import sqlite3
    with sqlite3.connect(p) as conn:                              # a DB with NO migrations applied
        assert hb.table_present(conn) is False
        assert hb.read_liveness(conn, now_ts=NOW) == []           # absent tables -> honest-empty, never a crash


# ═══════════════════════════ writers upsert one row per grain ═══════════════════════════

def test_writers_upsert_idempotent(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        hb.upsert_task_alive(conn, "kalshi_jack", NOW)
        hb.upsert_task_alive(conn, "kalshi_jack", NOW + 7)         # UPSERT -> latest wins, one row
        assert tuple(conn.execute("SELECT COUNT(*), MAX(last_cycle_ts) FROM pm_driver_task_heartbeat").fetchone()) == (1, NOW + 7)
        hb.upsert_reached(conn, "kalshi_jack", "mlb", NOW)
        hb.upsert_evaluated(conn, "kalshi_jack", "mlb", NOW + 7,
                            {"n_signals": 5, "placed": 3, "errors": 0, "ceiling_latched": False})
        r = conn.execute("SELECT reached_ts, evaluated_ts, n_signals, placed, state FROM pm_driver_heartbeat "
                         "WHERE account_id='kalshi_jack' AND category='mlb'").fetchone()
        assert tuple(r) == (NOW + 7, NOW + 7, 5, 3, "evaluated")
        assert conn.execute("SELECT COUNT(*) FROM pm_driver_heartbeat").fetchone()[0] == 1   # still one row
        hb.mark_skipped(conn, "kalshi_jack", "ufc", NOW, "skipped_no_ctx")
        r2 = conn.execute("SELECT reached_ts, evaluated_ts, state FROM pm_driver_heartbeat "
                          "WHERE category='ufc'").fetchone()
        assert tuple(r2) == (NOW, None, "skipped_no_ctx")         # reached but NOT evaluated


# ═══════════════════════════ ★ both-directions age band ═══════════════════════════

def test_liveness_band_both_directions(tmp_path):
    assert hb.liveness_band(10) == "fresh"
    assert hb.liveness_band(hb.FRESH_MAX_SEC + 10) == "stale"
    assert hb.liveness_band(hb.STALE_MAX_SEC + 10) == "dead"
    # ★ a FUTURE ts (age far negative) must read 'dead', NEVER 'fresh forever'
    assert hb.liveness_band(-10) == "fresh"                        # tiny skew tolerated
    assert hb.liveness_band(-(hb.STALE_MAX_SEC + 10)) == "dead"    # far-future clock jump -> dead, not fresh


def test_safe_beat_swallows_errors(tmp_path):
    """★ FAIL-SOFT unit: safe_beat must swallow ANY writer error (a liveness write can never kill a trading cycle)."""
    calls = {"n": 0}

    def _raiser(*a, **k):
        calls["n"] += 1
        raise RuntimeError("boom")
    hb.safe_beat(_raiser, 1, 2, log=None)     # must NOT raise
    assert calls["n"] == 1

    def _ok(x):
        calls["ok"] = x
    hb.safe_beat(_ok, 42)
    assert calls["ok"] == 42                   # the happy path still runs


# ═══════════════════════════ the six states ═══════════════════════════

def _one(rows, acct, cat):
    return next(r for r in rows if r.account_id == acct and r.category == cat)


def test_states_running_idle_starved(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        _seed_expected(conn, [("a", "mlb"), ("a", "ufc"), ("a", "atp"), ("a", "wta")])
        hb.upsert_task_alive(conn, "a", NOW)                        # task alive
        hb.upsert_evaluated(conn, "a", "mlb", NOW, {"n_signals": 10, "placed": 3})   # placing -> RUNNING
        hb.upsert_evaluated(conn, "a", "ufc", NOW, {"n_signals": 0, "placed": 0})    # 0 signals -> IDLE
        hb.upsert_evaluated(conn, "a", "atp", NOW, {"n_signals": 4, "placed": 0, "ceiling_latched": True})  # latched -> IDLE (not a fault)
        hb.upsert_reached(conn, "a", "wta", NOW)                    # reached but never evaluated -> STARVED
        rows = hb.read_liveness(conn, now_ts=NOW + 5)
        assert _one(rows, "a", "mlb").state == "RUNNING" and _one(rows, "a", "mlb").placed == 3
        assert _one(rows, "a", "ufc").state == "IDLE"
        assert _one(rows, "a", "atp").state == "IDLE" and _one(rows, "a", "atp").ceiling_latched is True
        assert _one(rows, "a", "wta").state == "CATEGORY_STARVED"
        assert hb.any_alarm(rows) is False                         # none of these is an ALARM


def test_state_stale_when_task_dead(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        _seed_expected(conn, [("a", "mlb")])
        hb.upsert_task_alive(conn, "a", NOW - 20 * MIN)            # task last beat 20 min ago = dead/hung
        hb.upsert_evaluated(conn, "a", "mlb", NOW - 20 * MIN, {"n_signals": 5, "placed": 1})
        rows = hb.read_liveness(conn, now_ts=NOW)
        assert _one(rows, "a", "mlb").state == "STALE" and hb.any_alarm(rows) is True


def test_pending_vs_never_on_attach_grace(tmp_path):
    with db.connect(_db(tmp_path)) as conn:
        # a freshly-attached sub (added just now) with NO heartbeat -> PENDING, not an alarm
        _seed_expected(conn, [("a", "atp")], attach_ts=NOW - 2 * MIN)
        # an old-attached sub with NO heartbeat -> NEVER (the incident shape)
        _seed_expected(conn, [("a", "mlb")], attach_ts=NOW - 3 * 24 * 3600)
        rows = hb.read_liveness(conn, now_ts=NOW)
        assert _one(rows, "a", "atp").state == "PENDING" and hb.any_alarm([_one(rows, "a", "atp")]) is False
        assert _one(rows, "a", "mlb").state == "NEVER" and hb.any_alarm([_one(rows, "a", "mlb")]) is True


# ═══════════════════════════ ★★ THE INCIDENT (unit-level acceptance) ═══════════════════════════

def test_incident_shape_all_never_alarm(tmp_path):
    """2026-09-04: the driver was deleted -> no task spawned -> NO heartbeats for the 8 long-attached expected subs.
    The monitor must render every one as NEVER (alarm), not a green 'armed'. This is the whole point of the feature."""
    with db.connect(_db(tmp_path)) as conn:
        eight = [(a, c) for a in ("kalshi_jack", "kalshi_karen") for c in ("mlb", "ufc", "atp", "wta")]
        _seed_expected(conn, eight, attach_ts=NOW - 3 * 24 * 3600)   # attached long ago, past grace
        # NO heartbeat rows at all (the task never ran)
        rows = hb.read_liveness(conn, now_ts=NOW)
        assert len(rows) == 8
        assert all(r.state == "NEVER" for r in rows)
        assert hb.any_alarm(rows) is True


def test_expected_set_drives_never_detection(tmp_path):
    """read_liveness starts from the EXPECTED (attachment-gated) set: a sub with a stale heartbeat but NO active
    attachment is NOT listed (not expected); an attached sub with NO heartbeat IS listed (NEVER). Starting from
    'rows that have a heartbeat' would MISS the never-spawned one -- the exact miss this feature exists to prevent."""
    with db.connect(_db(tmp_path)) as conn:
        _seed_expected(conn, [("a", "mlb")], attach_ts=NOW - 3 * 24 * 3600)   # expected, no heartbeat -> NEVER
        # a heartbeat for a category that is NOT an attached sub-division (e.g. detached): must NOT appear
        hb.upsert_evaluated(conn, "a", "ghost", NOW, {"n_signals": 1})
        rows = hb.read_liveness(conn, now_ts=NOW)
        # the EXPECTED (attached) sub is surfaced; the non-attached 'ghost' heartbeat is filtered out entirely
        assert [(r.account_id, r.category) for r in rows] == [("a", "mlb")]
        assert _one(rows, "a", "mlb").state == "NEVER" and hb.any_alarm(rows) is True
