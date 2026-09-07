"""Phase-2 Live-Sub-divisions TILE readers (subdivision.tiles_all / subdivision_pnl_all / realized_24h_all +
arm.read_display_all). Offline, temp PM DB + a temp legacy agent_state DB for arm. Proves: the tile set includes
UNATTACHED sub-divisions (R2); booked vs unbooked closes are split and W-L is settlements-only (R3); the 24h window
is settlement+settled_ts and its edge is exclusive-below; and the batched arm read classifies armed/disarmed/
never-armed/unavailable identically to read_display (never a false disarm)."""
import sqlite3

from trading_corp.prediction_markets import db, subdivision, arm

JACK = "kalshi_jack"


def _pm(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_account(account_id,venue,label,active,created_ts) VALUES(?,?,?,1,1)",
                     (JACK, "kalshi", "Jack (KALSHI)"))
        for cat in ("mlb", "atp", "ufc"):     # mlb+atp will be attached; ufc left unattached
            conn.execute("INSERT INTO pm_subdivision(account_id,category,label,sizing_mode,fixed_stake_usd,active,created_ts) "
                         "VALUES(?,?,?,'fixed',5.0,1,1)", (JACK, cat, "Jack " + cat.upper()))
        for cat in ("mlb", "atp"):
            conn.execute("INSERT INTO pm_subdivision_attachment(account_id,category,wallet,active,added_ts) VALUES(?,?,?,1,1)",
                         (JACK, cat, "0xwhale_" + cat))
    return p


def _order(conn, cat, *, is_exit, leg="yes", fc=5, fp=0.5, fee=0.0, close_source=None, realized=None, won=None,
           settled_ts=None, response_ts=1000):
    conn.execute(
        "INSERT INTO pm_subdivision_order(account_id,category,wallet,ticker,outcome_leg,is_exit,fill_count,fill_price,"
        "fee,outcome_status,close_source,realized_pnl,won,settled_ts,dry_run,submitted_ts,response_ts) "
        "VALUES(?,?,?,?,?,?,?,?,?,'filled',?,?,?,?,0,?,?)",
        (JACK, cat, "0xw", "KX" + cat.upper() + "-T", leg, is_exit, fc, fp, fee, close_source, realized, won,
         settled_ts, response_ts, response_ts))


def test_tiles_all_includes_unattached(monkeypatch, tmp_path):
    p = _pm(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        rows = {r["category"]: r for r in subdivision.tiles_all(conn)}
    assert set(rows) == {"mlb", "atp", "ufc"}                    # R2: ALL active subs, incl the unattached ufc
    assert rows["mlb"]["n_whales"] == 1 and rows["atp"]["n_whales"] == 1
    assert rows["ufc"]["n_whales"] == 0                          # unattached -> 0, still present (not hidden)
    assert rows["mlb"]["account_label"] == "Jack (KALSHI)"


def test_subdivision_pnl_all_splits_booked_unbooked_and_wl_from_settlements(monkeypatch, tmp_path):
    p = _pm(monkeypatch, tmp_path)
    with db.connect(p) as conn:
        _order(conn, "mlb", is_exit=0)                                                    # an ENTRY -> not a close
        _order(conn, "mlb", is_exit=1, close_source="settlement", realized=3.0, won=1, settled_ts=100)   # settle win
        _order(conn, "mlb", is_exit=1, close_source="settlement", realized=-1.0, won=0, settled_ts=100)  # settle loss
        _order(conn, "mlb", is_exit=1, close_source="whale_exit", realized=0.5, won=None, settled_ts=None)  # booked exit, won NULL
        _order(conn, "mlb", is_exit=1, close_source="opposed", realized=None, won=None, settled_ts=None)    # UNBOOKED
        conn.commit()
        allp = subdivision.subdivision_pnl_all(conn)
    m = allp[(JACK, "mlb")]
    assert round(m["realized"], 2) == 2.50            # 3.0 - 1.0 + 0.5 (booked only; the opposed NULL is excluded)
    assert m["booked_closes"] == 3                    # 2 settlements + 1 whale-exit (realized_pnl NOT NULL)
    assert m["wins"] == 1 and m["losses"] == 1        # W-L is SETTLEMENTS only (won non-NULL) -> < booked_closes
    assert m["unbooked_closes"] == 1                  # the opposed close, realized_pnl NULL, counted separately


def test_realized_24h_all_window_edge(monkeypatch, tmp_path):
    p = _pm(monkeypatch, tmp_path)
    now = 1_000_000
    with db.connect(p) as conn:
        _order(conn, "mlb", is_exit=1, close_source="settlement", realized=2.0, won=1, settled_ts=now - 100)      # inside
        _order(conn, "mlb", is_exit=1, close_source="settlement", realized=9.0, won=1, settled_ts=now - 86400)    # exactly 24h -> INCLUDED (>=)
        _order(conn, "mlb", is_exit=1, close_source="settlement", realized=5.0, won=1, settled_ts=now - 86401)    # older -> excluded
        _order(conn, "mlb", is_exit=1, close_source="whale_exit", realized=7.0, won=None, settled_ts=None, response_ts=now)  # not a settlement -> excluded
        conn.commit()
        r = subdivision.realized_24h_all(conn, now)
    m = r[(JACK, "mlb")]
    assert m["n_24h"] == 2 and round(m["realized_24h"], 2) == 11.0     # the -100 and the exactly-86400 rows only


def _legacy(tmp_path, rows):
    """A temp legacy agent_state DB with the given (key, value_json) rows."""
    lp = str(tmp_path / "legacy.db")
    c = sqlite3.connect(lp)
    c.execute("CREATE TABLE agent_state (agent TEXT, key TEXT, value_json TEXT, PRIMARY KEY(agent,key))")
    for k, v in rows:
        c.execute("INSERT INTO agent_state(agent,key,value_json) VALUES('pm_live',?,?)", (k, v))
    c.commit(); c.close()
    return lp


def test_read_display_all_states(monkeypatch, tmp_path):
    lp = _legacy(tmp_path, [
        ("arm:global", '{"armed": true, "ts": "2026-08-31T02:35:38+00:00"}'),
        ("arm:kalshi_jack:mlb", '{"armed": true, "ts": "2026-09-04T04:31:42+00:00"}'),
        ("arm:kalshi_jack:atp", '{"armed": false, "ts": "2026-09-04T04:31:43+00:00"}'),
        # no row for (jack, ufc) -> absent / never armed
    ])
    monkeypatch.setenv("PM_LEGACY_DB_PATH", lp)
    d = arm.read_display_all([(JACK, "mlb"), (JACK, "atp"), (JACK, "ufc")])
    assert d["global"]["state"] == "armed"
    assert d["subs"][(JACK, "mlb")]["sub_state"] == "armed" and d["subs"][(JACK, "mlb")]["effective_state"] == "armed"
    assert d["subs"][(JACK, "atp")]["sub_state"] == "disarmed" and d["subs"][(JACK, "atp")]["effective_state"] == "disarmed"
    assert d["subs"][(JACK, "ufc")]["sub_state"] == "absent" and d["subs"][(JACK, "ufc")]["effective_state"] == "disarmed"


def test_read_display_all_global_off_makes_everything_effective_disarmed(monkeypatch, tmp_path):
    lp = _legacy(tmp_path, [
        ("arm:global", '{"armed": false}'),
        ("arm:kalshi_jack:mlb", '{"armed": true}'),
    ])
    monkeypatch.setenv("PM_LEGACY_DB_PATH", lp)
    d = arm.read_display_all([(JACK, "mlb")])
    assert d["subs"][(JACK, "mlb")]["sub_state"] == "armed"           # its own row is armed ...
    assert d["subs"][(JACK, "mlb")]["effective_state"] == "disarmed"  # ... but the global master is off


def test_read_display_all_absent_db_is_absent_not_error(monkeypatch, tmp_path):
    monkeypatch.setenv("PM_LEGACY_DB_PATH", str(tmp_path / "does_not_exist.db"))
    d = arm.read_display_all([(JACK, "mlb")])
    assert d["global"]["state"] == "absent"
    assert d["subs"][(JACK, "mlb")]["sub_state"] == "absent" and d["subs"][(JACK, "mlb")]["effective_state"] == "disarmed"


def test_read_display_all_corrupt_row_is_unavailable_never_disarm(monkeypatch, tmp_path):
    lp = _legacy(tmp_path, [("arm:global", '{"armed": true}'), ("arm:kalshi_jack:mlb", "{not json")])
    monkeypatch.setenv("PM_LEGACY_DB_PATH", lp)
    d = arm.read_display_all([(JACK, "mlb")])
    assert d["subs"][(JACK, "mlb")]["sub_state"] == "unavailable"      # corrupt -> unavailable, NEVER a false disarm
    assert d["subs"][(JACK, "mlb")]["effective_state"] == "unavailable"
