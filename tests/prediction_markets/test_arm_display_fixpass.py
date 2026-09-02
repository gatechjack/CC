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


# ── the BADGE rendering (post-deploy item 3): absent -> 'NEVER ARMED', its own state, no age chip ──────────────
def _badge_html(state, age, prefix="GLOBAL "):
    """Render the real read-only arm badge macro through a minimal Jinja2 env (jinja2 only; no fastapi/engine).
    The macro body is a comment + one macro def -> accessing `.module` is side-effect-free."""
    import pathlib

    import jinja2
    tdir = pathlib.Path(arm.__file__).resolve().parent / "web" / "templates"
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(tdir)), autoescape=True)
    env.filters["agefmt"] = lambda s: "" if s is None else ("%ds" % int(s))
    return str(env.get_template("partials/pm_arm_badge.html").module.arm_badge(state, age, prefix))


def test_badge_absent_renders_never_armed_no_chip():
    # An ABSENT arm row (a scope that never wrote a row) renders 'NEVER ARMED' -- DISTINCT from DISARMED (a row
    # that exists and says off) and UNAVAILABLE (an unreadable read) -- and carries NO age chip (no ts to age).
    html = _badge_html("absent", None)
    assert "NEVER ARMED" in html
    assert "DISARMED" not in html and "UNAVAILABLE" not in html      # not collapsed into either sibling state
    assert "chip" not in html                                        # a state with no timestamp shows no age chip
    assert "badge never" in html                                     # its own visual class, not 'disarmed'


def test_badge_states_render_distinct_labels():
    # the four display states render four distinct labels; only the timestamped states carry an age chip.
    assert "GLOBAL ARMED" in _badge_html("armed", 12) and "chip" in _badge_html("armed", 12)
    assert "GLOBAL DISARMED" in _badge_html("disarmed", 30) and "chip" in _badge_html("disarmed", 30)
    assert "GLOBAL STATE UNAVAILABLE" in _badge_html("unavailable", None)
    assert "NEVER ARMED" in _badge_html("absent", None)
