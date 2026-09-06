"""L3 -- the DRIVER LIVENESS panel in pm_web, via the FastAPI TestClient. Offline, PM DB only, admin operator.

★ THE ACCEPTANCE TEST IS THE INCIDENT (2026-09-04): eight long-attached expected sub-divisions, the driver never
spawned -> NO heartbeats -> the accounts overview must render every one RED (NEVER) and say the driver is not
running -- NOT a green 'armed'. This suite proves that, plus the two ways the panel must NOT cry wolf (a fresh
attach reads PENDING; a latched order-ceiling reads IDLE, never a fault) and that a not-deployed monitor (table
absent) reads NEUTRAL, not red. All ages are wall-clock offsets (the route reads time.time(); no clock injection)."""
import time

from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, heartbeat as hb

CATS = ("mlb", "ufc", "atp", "wta")
EIGHT = [(a, c) for a in ("kalshi_jack", "kalshi_karen") for c in CATS]


def _mk(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    db.init_db(p)
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")            # admin sees every account (both jack + karen)
    from trading_corp.prediction_markets.web.app import app
    cl = TestClient(app); cl.headers.update({"Remote-User": "jack"})
    return cl, p


def _seed_expected(p, subs, *, attach_age=3 * 24 * 3600):
    """Seed the attachment-gated EXPECTED set so read_liveness lists these subs. attach_age seconds ago (default
    3 days = well past the 20-min attach grace)."""
    now = int(time.time())
    with db.connect(p) as conn:
        for a in {a for a, _ in subs}:
            conn.execute("INSERT OR IGNORE INTO pm_account (account_id,venue,secret_ref,label,active,created_ts) "
                         "VALUES (?,?,?,?,1,?)", (a, "kalshi", a, a, now - 9 * 24 * 3600))
        for a, c in subs:
            conn.execute("INSERT OR IGNORE INTO pm_subdivision (account_id,category,active,created_ts) "
                         "VALUES (?,?,1,?)", (a, c, now - 9 * 24 * 3600))
            conn.execute("INSERT OR IGNORE INTO pm_subdivision_attachment (account_id,category,wallet,active,added_ts) "
                         "VALUES (?,?,?,1,?)", (a, c, "0xwhale", now - attach_age))
        conn.commit()


def _beat(p, *, task_age, subs, ev_age=None, summ=None, skipped=None):
    """Write heartbeats at wall-clock offsets: task_alive `task_age`s ago for each account; and for each sub either
    evaluated `ev_age`s ago with `summ`, or mark_skipped `skipped`."""
    now = int(time.time())
    with db.connect(p) as conn:
        for a in {a for a, _ in subs}:
            hb.upsert_task_alive(conn, a, now - task_age)
        for a, c in subs:
            if skipped is not None:
                hb.mark_skipped(conn, a, c, now - (ev_age or task_age), skipped)
            elif ev_age is not None:
                hb.upsert_evaluated(conn, a, c, now - ev_age, summ or {})


# ═══════════════════════════ ★★ THE INCIDENT (web-level acceptance) ═══════════════════════════

def test_incident_all_never_renders_red(monkeypatch, tmp_path):
    """2026-09-04 exactly: 8 long-attached subs, driver never spawned -> NO heartbeats -> the overview panel is RED
    and says the driver is not running. This is the whole feature: the alarm arm state could never give."""
    cl, p = _mk(monkeypatch, tmp_path)
    _seed_expected(p, EIGHT)                                     # attached 3 days ago, past grace; NO heartbeats
    html = cl.get("/").text
    assert 'class="pm-lv-panel"' in html or 'pm-lv-panel' in html
    assert 'data-liveness-present="1"' in html                  # the monitor IS deployed (table present)
    assert 'data-liveness-alarm="1"' in html                    # ★ the panel is RED
    assert html.count('data-liveness-state="NEVER"') == 8       # every expected sub renders NEVER
    assert 'driver NOT running' in html                         # unmistakable wording
    assert '&#10003; all' not in html                           # NOT a green 'all cycling' ok summary


def test_incident_stale_28h_renders_red_with_age(monkeypatch, tmp_path):
    """The other incident shape: the driver WAS running, then died -> heartbeats frozen ~28h ago -> STALE, age
    reads '28h ago'. 'age climbing past 28 hours' rendered red, literally."""
    cl, p = _mk(monkeypatch, tmp_path)
    _seed_expected(p, EIGHT)
    _beat(p, task_age=28 * 3600, subs=EIGHT, ev_age=28 * 3600, summ={"n_signals": 5, "placed": 1})
    html = cl.get("/").text
    assert 'data-liveness-alarm="1"' in html
    assert html.count('data-liveness-state="STALE"') == 8
    assert '28h ago' in html                                    # the age is shown, climbing past 28h


# ═══════════════════════════ ★ the boot window (must NOT alarm) ═══════════════════════════

def test_boot_window_renders_booting_not_alarm(monkeypatch, tmp_path):
    """★ THE BOOT-WINDOW FINDING IN THE DISPLAY: after a restart the driver boots for a few minutes (catalog build +
    reconcile) before the while-loop's first heartbeat, and the prior rows age. The panel must render BOOTING (amber,
    informational), NEVER the red alarm -- a monitor that reds on every normal restart gets ignored, which is the
    same as no monitor (the failure this feature exists to prevent). Seed beats ~3 min old (within the 10-min grace)."""
    cl, p = _mk(monkeypatch, tmp_path)
    _seed_expected(p, EIGHT)
    _beat(p, task_age=3 * 60, subs=EIGHT, ev_age=3 * 60, summ={"n_signals": 5, "placed": 1})
    html = cl.get("/").text
    assert 'data-liveness-alarm="0"' in html                    # ★ NOT red during a legitimate boot
    assert 'data-liveness-alarm="1"' not in html
    assert 'data-liveness-booting="1"' in html
    assert html.count('data-liveness-state="BOOTING"') == 8
    assert 'driver restarting' in html
    assert 'driver NOT running' not in html


# ═══════════════════════════ healthy: no false alarm ═══════════════════════════

def test_all_running_renders_green_no_alarm(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    _seed_expected(p, EIGHT)
    _beat(p, task_age=5, subs=EIGHT, ev_age=5, summ={"n_signals": 3, "placed": 2})   # cycling + placing
    html = cl.get("/").text
    assert 'data-liveness-alarm="0"' in html
    assert 'data-liveness-alarm="1"' not in html
    assert html.count('data-liveness-state="RUNNING"') == 8
    assert 'all 8 sub-divisions cycling' in html
    assert 'driver NOT running' not in html


# ═══════════════════════════ ★ don't cry wolf ═══════════════════════════

def test_pending_on_fresh_attach_not_alarm(monkeypatch, tmp_path):
    """A sub attached 2 min ago has no heartbeat until the next engine restart spawns it -> PENDING, NOT an alarm.
    The roster is read only at engine boot, so a fresh attach legitimately has no task yet -- surfacing that as a
    red 'never spawned' would cry wolf on every attach."""
    cl, p = _mk(monkeypatch, tmp_path)
    _seed_expected(p, [("kalshi_jack", "atp")], attach_age=2 * 60)    # within the 20-min grace
    html = cl.get("/").text
    assert 'data-liveness-state="PENDING"' in html
    assert 'data-liveness-alarm="0"' in html                    # PENDING is informational, not an alarm


def test_ceiling_latched_reads_idle_not_fault(monkeypatch, tmp_path):
    """★ ceiling_latched=True is 'alive but intentionally not placing' (the per-cycle order ceiling latched). It
    must read IDLE, never STALE/NEVER -- a latched sub is working as designed, not a fault."""
    cl, p = _mk(monkeypatch, tmp_path)
    _seed_expected(p, [("kalshi_jack", "mlb")])
    _beat(p, task_age=5, subs=[("kalshi_jack", "mlb")], ev_age=5,
          summ={"n_signals": 4, "placed": 0, "ceiling_latched": True})
    html = cl.get("/").text
    assert 'data-liveness-state="IDLE"' in html
    assert 'data-liveness-state="STALE"' not in html and 'data-liveness-state="NEVER"' not in html
    assert 'data-liveness-alarm="0"' in html


# ═══════════════════════════ ★ absent-vs-empty: not-deployed reads neutral ═══════════════════════════

def test_table_absent_reads_not_deployed_not_red(monkeypatch, tmp_path):
    """If the heartbeat table is ABSENT (migration 020 not applied here), read_liveness still classes the expected
    set NEVER -- but that must read NEUTRAL 'monitor not deployed', NOT a red alarm. Don't cry wolf about the
    monitor's own absence (the very hazard a silently-skipped migration would create)."""
    cl, p = _mk(monkeypatch, tmp_path)
    _seed_expected(p, EIGHT)
    with db.connect(p) as conn:                                 # drop the heartbeat tables -> table_present False
        conn.execute("DROP TABLE IF EXISTS pm_driver_heartbeat")
        conn.execute("DROP TABLE IF EXISTS pm_driver_task_heartbeat")
        conn.commit()
    html = cl.get("/").text
    assert 'data-liveness-present="0"' in html
    assert 'data-liveness-alarm="0"' in html                    # NEUTRAL, not red
    assert 'monitor not deployed' in html
    assert 'data-liveness-state="NEVER"' not in html            # the grid is not rendered when absent


# ═══════════════════════════ the other pages carry the badge ═══════════════════════════

def test_account_page_all_never_alarms(monkeypatch, tmp_path):
    """The account-scoped panel: with the task never spawned, every sub is NEVER -> the account page is red too
    (the incident is visible at the account level, not only on the overview)."""
    cl, p = _mk(monkeypatch, tmp_path)
    jack = [("kalshi_jack", c) for c in CATS]
    _seed_expected(p, jack)                                      # no heartbeats -> all NEVER
    html = cl.get("/account/kalshi_jack").text
    assert 'data-liveness-alarm="1"' in html
    assert html.count('data-liveness-state="NEVER"') == 4


def test_account_page_starved_is_not_an_alarm(monkeypatch, tmp_path):
    """★ CATEGORY_STARVED is NOT an alarm: with the account task ALIVE but one category not completing evaluation,
    that category reads CATEGORY_STARVED (a soft, category-level signal) while its siblings run -- the account
    stays alarm=0. A dead/absent account TASK is the alarm; a single starved category is not. (And by design an
    alive task means no sub can be NEVER/STALE, so RUNNING + a hard alarm never co-occur on one account.)"""
    cl, p = _mk(monkeypatch, tmp_path)
    jack = [("kalshi_jack", c) for c in CATS]
    _seed_expected(p, jack)
    _beat(p, task_age=5, subs=jack[:3], ev_age=5, summ={"n_signals": 1, "placed": 1})   # task alive; 3 of 4 evaluated
    html = cl.get("/account/kalshi_jack").text
    assert html.count('data-liveness-state="RUNNING"') == 3
    assert 'data-liveness-state="CATEGORY_STARVED"' in html      # the un-evaluated 4th -> starved (not NEVER)
    assert 'data-liveness-alarm="0"' in html                     # ...and the account is NOT alarmed


def test_live_subdivision_page_shows_single_badge(monkeypatch, tmp_path):
    cl, p = _mk(monkeypatch, tmp_path)
    _seed_expected(p, [("kalshi_jack", "mlb")])
    _beat(p, task_age=5, subs=[("kalshi_jack", "mlb")], ev_age=5, summ={"n_signals": 0, "placed": 0})
    html = cl.get("/live/kalshi_jack/mlb").text
    assert 'data-liveness-state="IDLE"' in html                 # cycling, 0 signals -> IDLE
    assert '>Driver<' in html                                   # the Driver section heading rendered
