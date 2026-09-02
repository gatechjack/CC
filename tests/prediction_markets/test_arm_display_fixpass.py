"""Fix-pass item 2: the pm_web arm BADGE reads the PERSISTED agent_state row and DISTINGUISHES an unreadable
state ('unavailable') from a real disarm -- so a mode=ro read near a restart (the engine team's false-disarm)
is NEVER shown as DISARMED. Pure PM-package test (no engine, no pykalshi).
"""
import json
import sqlite3

from trading_corp.prediction_markets import arm


def _write_row(path, key, value):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS agent_state (agent TEXT, key TEXT, value_json TEXT, PRIMARY KEY(agent,key))")
    c.execute("INSERT OR REPLACE INTO agent_state(agent,key,value_json) VALUES ('pm_live',?,?)", (key, json.dumps(value)))
    c.commit()
    c.close()


def test_absent_db_is_absent_not_unavailable(tmp_path):
    # no legacy DB file at all -> definitively ABSENT (cold start -> disarmed-by-absence), NOT 'unavailable'
    d = arm.read_display(legacy_db_path=str(tmp_path / "nope.db"))
    assert d["global_state"] == "absent" and d["global_ts"] is None


def test_indeterminate_read_is_unavailable_never_disarmed(tmp_path):
    # a file that EXISTS but has no agent_state table -> the read cannot be determined -> 'unavailable'.
    # This is the false-disarm the engine team flagged: read_status()/the gate collapse this to DISARMED, but
    # the DISPLAY must show 'unavailable' so a transient read near a restart is never mistaken for a human kill.
    p = str(tmp_path / "bad.db")
    sqlite3.connect(p).close()                                          # valid empty db, no agent_state table
    assert arm.read_display(legacy_db_path=p)["global_state"] == "unavailable"


def test_armed_and_disarmed_rows_read_true_state_with_ts(tmp_path):
    p = str(tmp_path / "ok.db")
    _write_row(p, "arm:global", {"armed": True, "ts": "2026-09-02T12:00:00+00:00"})
    d = arm.read_display(legacy_db_path=p)
    assert d["global_state"] == "armed" and d["global_ts"] == "2026-09-02T12:00:00+00:00"
    _write_row(p, "arm:global", {"armed": False, "ts": "2026-09-02T13:00:00+00:00", "reason": "operator_disarm"})
    d2 = arm.read_display(legacy_db_path=p)
    assert d2["global_state"] == "disarmed" and d2["global_ts"] == "2026-09-02T13:00:00+00:00"


def test_effective_unavailable_if_either_scope_unreadable(tmp_path):
    # global armed row present, but the SUB read errors -> effective is 'unavailable', not a fabricated disarm.
    p = str(tmp_path / "eff.db")
    _write_row(p, "arm:global", {"armed": True, "ts": "2026-09-02T12:00:00+00:00"})
    # a real armed sub -> effective armed
    _write_row(p, "arm:kalshi_jack:mlb", {"armed": True, "ts": "2026-09-02T12:05:00+00:00"})
    assert arm.read_display("kalshi_jack", "mlb", legacy_db_path=p)["effective_state"] == "armed"
    # sub row says armed False -> effective disarmed (a REAL disarm)
    _write_row(p, "arm:kalshi_jack:mlb", {"armed": False, "ts": "2026-09-02T12:06:00+00:00"})
    assert arm.read_display("kalshi_jack", "mlb", legacy_db_path=p)["effective_state"] == "disarmed"
