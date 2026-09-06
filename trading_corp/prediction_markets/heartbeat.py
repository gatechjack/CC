"""PM driver LIVENESS heartbeat -- L1 (2026-09-06). The ENGINE writes (inside the driver task's OWN body, so a
write cannot happen if the loop is dead or never-spawned); pm_web READS the latest + bands the AGE. It is the
answer to a question arm state cannot answer: NOT "is this sub-division supposed to trade?" (arm) but "is the
driver actually running and evaluating it right now?". Born from 2026-09-04: the driver block was deleted from
main.py and PM did not trade for ~28h, undetected -- nine arm rows all correctly armed the whole time.

THREE GRAINS, because a per-sub heartbeat ALONE is a LIAR. The driver cycle body sits inside ONE try/except
(live_driver.py "a bad cycle must never kill the loop"), so if category #1 of 4 throws every cycle, categories
#2-4 never reach their write and read STALE while the task is perfectly alive. So:
  - task_alive (per ACCOUNT)          -> the task is running (written at the TOP of the while-loop, outside the try)
  - reached    (per account,category) -> the loop reached this category this cycle (before the no-catalog continues)
  - evaluated  (per account,category) -> this category fully evaluated + a cheap summary (IDLE vs PLACING)

Pure sqlite + json + stdlib -- imports NO broker, NO engine (pm_web-safe, same isolation as shard_snapshot). Every
read tolerates the table being absent (pre-migration-020) -> honest-empty, never a 500. ★ BOTH-DIRECTIONS age: a
FUTURE ts (clock jump / bad write) reads STALE, NEVER 'fresh forever' -- a liveness monitor a clock-jump could pin
GREEN is the worst version of this feature (the search_run._is_live_lock principle, applied here self-contained).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

_TABLE_ACCT = "pm_driver_task_heartbeat"     # per account
_TABLE_CAT = "pm_driver_heartbeat"           # per (account, category)

# staleness bands (seconds). The driver polls ~7s and a healthy sub-division updates every ~7-30s, so 'fresh' sits
# well above one cycle (transient hiccups don't cry wolf) and 'dead' is unambiguous. Overridable per call for tests.
FRESH_MAX_SEC = 90
STALE_MAX_SEC = 300
# a freshly-attached sub-division has no heartbeat until the NEXT engine restart re-reads the roster -> show PENDING
# (not an alarm) within this grace of its latest active attachment. Beyond it, no-heartbeat = a real never-spawned.
ATTACH_GRACE_SEC = 20 * 60


def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def table_present(conn) -> bool:
    """Whether the per-category heartbeat table EXISTS. Distinguishes ABSENT (migration 021 not applied -> 'arrives
    with the engine writer / a skipped migration') from PRESENT-but-empty (applied, engine hasn't written yet). A
    blank reads the same for both; a lying-monitor-because-the-migration-silently-skipped must NOT look like idle."""
    return _table_exists(conn, _TABLE_CAT)


# ═══════════════════════════ ENGINE WRITE side (in the driver task's own body) ═══════════════════════════

def upsert_task_alive(conn, account_id: str, ts: int) -> None:
    """Per-ACCOUNT 'the task is alive' beat. Written at the TOP of the while-loop, OUTSIDE the cycle try/except, so
    it proves the task lives independent of whether any category completed. UPSERT one row per account. Commits
    (pm_web reads via mode=ro and only sees COMMITTED WAL frames)."""
    conn.execute(
        "INSERT INTO %s (account_id, last_cycle_ts, updated_ts) VALUES (?,?,?) "
        "ON CONFLICT(account_id) DO UPDATE SET last_cycle_ts=excluded.last_cycle_ts, updated_ts=excluded.updated_ts"
        % _TABLE_ACCT, (account_id, int(ts), int(ts)))
    conn.commit()


def upsert_reached(conn, account_id: str, category: str, ts: int) -> None:
    """Per-(account,category) 'the loop reached this category' beat. Written as the FIRST thing in the category loop,
    BEFORE the no-catalog/no-ctx continues, so 'reached but skipped' is distinguishable from 'never reached'. Bumps
    ONLY reached_ts (leaves evaluated_ts + summary). UPSERT."""
    conn.execute(
        "INSERT INTO %s (account_id, category, reached_ts, updated_ts) VALUES (?,?,?,?) "
        "ON CONFLICT(account_id, category) DO UPDATE SET reached_ts=excluded.reached_ts, updated_ts=excluded.updated_ts"
        % _TABLE_CAT, (account_id, category, int(ts), int(ts)))
    conn.commit()


def upsert_evaluated(conn, account_id: str, category: str, ts: int, summ: dict | None = None) -> None:
    """Per-(account,category) 'this category fully evaluated' beat + the cheap summary from the arm-gated cycle
    (n_signals/placed/errors/ceiling_latched) so IDLE (0 signals) is distinguishable from PLACING (placed>0) and a
    latched order-ceiling (intentionally not placing) is not read as a fault. Bumps reached_ts too (it was reached).
    UPSERT. `state`='evaluated'."""
    s = summ or {}
    conn.execute(
        "INSERT INTO %s (account_id, category, reached_ts, evaluated_ts, n_signals, placed, errors, "
        "ceiling_latched, state, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(account_id, category) DO UPDATE SET reached_ts=excluded.reached_ts, "
        "evaluated_ts=excluded.evaluated_ts, n_signals=excluded.n_signals, placed=excluded.placed, "
        "errors=excluded.errors, ceiling_latched=excluded.ceiling_latched, state=excluded.state, "
        "updated_ts=excluded.updated_ts" % _TABLE_CAT,
        (account_id, category, int(ts), int(ts), int(s.get("n_signals") or 0), int(s.get("placed") or 0),
         int(s.get("errors") or 0), 1 if s.get("ceiling_latched") else 0, "evaluated", int(ts)))
    conn.commit()


def mark_skipped(conn, account_id: str, category: str, ts: int, reason: str) -> None:
    """Per-(account,category) 'reached but skipped this cycle' (no catalog builder / no ctx). Records the reach +
    a skip state so 'loop reached it but had no catalog' is distinct from 'loop never reached it' (dead). Bumps
    reached_ts, sets state, does NOT advance evaluated_ts (it did not evaluate). UPSERT."""
    conn.execute(
        "INSERT INTO %s (account_id, category, reached_ts, state, updated_ts) VALUES (?,?,?,?,?) "
        "ON CONFLICT(account_id, category) DO UPDATE SET reached_ts=excluded.reached_ts, state=excluded.state, "
        "updated_ts=excluded.updated_ts" % _TABLE_CAT,
        (account_id, category, int(ts), str(reason), int(ts)))
    conn.commit()


# ═══════════════════════════ age banding (both directions) ═══════════════════════════

def liveness_band(age_sec: int, *, fresh: int = FRESH_MAX_SEC, stale: int = STALE_MAX_SEC) -> str:
    """'fresh' | 'stale' | 'dead' from age = now - ts. ★ BOTH DIRECTIONS: a ts far in the FUTURE (age well
    negative -- a clock jump or a bad write) reads 'dead', NEVER 'fresh forever'. Small skew (same box) is tolerated
    inside the window. This is the one place the monitor could otherwise report healthy-while-dead."""
    a = int(age_sec)
    if -fresh < a < fresh:
        return "fresh"
    if -stale < a < stale:
        return "stale"
    return "dead"


# ═══════════════════════════ pm_web READ side (credential-free, mode=ro-safe) ═══════════════════════════

@dataclass(frozen=True)
class SubLiveness:
    account_id: str
    category: str
    state: str            # RUNNING | IDLE | CATEGORY_STARVED | STALE | NEVER | PENDING
    band: str             # 'fresh' | 'stale' | 'dead' | 'none'
    age_sec: int | None   # since this sub last cycled (evaluated_ts, else task last_cycle_ts); None if never
    n_signals: int | None
    placed: int | None
    ceiling_latched: bool
    detail: str           # short human note for the display


# the six states, precedence highest-severity-last so the display can sort/colour uniformly
_STATE_SEVERITY = {"RUNNING": 0, "IDLE": 1, "PENDING": 2, "CATEGORY_STARVED": 3, "STALE": 4, "NEVER": 5}


def read_liveness(conn, *, now_ts: int | None = None, fresh: int = FRESH_MAX_SEC, stale: int = STALE_MAX_SEC,
                  grace: int = ATTACH_GRACE_SEC) -> list[SubLiveness]:
    """For EVERY EXPECTED sub-division (attachment-gated, the same set the driver would run -- read WITHOUT the
    engine), join the heartbeats and classify liveness. Starting from the EXPECTED set (not from rows that HAVE a
    heartbeat) is what catches a NEVER-spawned sub: it has no heartbeat but is expected -> alarm. Read-only;
    tolerant of any table being absent (honest-empty). Returns worst-state-last for a stable display order."""
    now = int(now_ts if now_ts is not None else time.time())
    # EXPECTED set = active sub-divisions with >=1 active attachment (mirrors driver_roster.active_driver_subdivisions),
    # carrying the latest active-attachment ts for the fresh-attach grace. Absent tables -> no expected subs.
    if not _table_exists(conn, "pm_subdivision") or not _table_exists(conn, "pm_subdivision_attachment"):
        return []
    expected = conn.execute(
        "SELECT s.account_id, s.category, MAX(a.added_ts) AS latest_attach_ts "
        "FROM pm_subdivision s "
        "JOIN pm_account acc ON acc.account_id=s.account_id AND acc.active=1 "
        "JOIN pm_subdivision_attachment a ON a.account_id=s.account_id AND a.category=s.category AND a.active=1 "
        "WHERE s.active=1 GROUP BY s.account_id, s.category ORDER BY s.account_id, s.category").fetchall()
    task_hb = {}
    if _table_exists(conn, _TABLE_ACCT):
        for r in conn.execute("SELECT account_id, last_cycle_ts FROM %s" % _TABLE_ACCT):
            task_hb[r[0]] = int(r[1]) if r[1] is not None else None
    cat_hb = {}
    if _table_exists(conn, _TABLE_CAT):
        for r in conn.execute(
            "SELECT account_id, category, reached_ts, evaluated_ts, n_signals, placed, ceiling_latched, state "
            "FROM %s" % _TABLE_CAT):
            cat_hb[(r[0], r[1])] = {"reached_ts": r[2], "evaluated_ts": r[3], "n_signals": r[4], "placed": r[5],
                                    "ceiling_latched": bool(r[6]), "state": r[7]}

    out: list[SubLiveness] = []
    for acct, cat, latest_attach_ts in expected:
        c = cat_hb.get((acct, cat))
        task_ts = task_hb.get(acct)
        task_fresh = task_ts is not None and liveness_band(now - task_ts, fresh=fresh, stale=stale) == "fresh"
        ev_ts = (c or {}).get("evaluated_ts")
        ev_fresh = ev_ts is not None and liveness_band(now - int(ev_ts), fresh=fresh, stale=stale) == "fresh"
        n_sig = (c or {}).get("n_signals")
        placed = (c or {}).get("placed")
        latched = bool((c or {}).get("ceiling_latched"))
        age = None
        cand = [int(t) for t in (ev_ts, task_ts) if t is not None]
        if cand:
            age = now - max(cand)   # may be negative for a future ts -- the band ('dead'/'stale') conveys that anomaly

        if c is None and task_ts is None:
            # never cycled for this sub at all
            if latest_attach_ts is not None and (now - int(latest_attach_ts)) < grace:
                state, band, detail = "PENDING", "none", "attached; awaiting the next engine restart to spawn"
            else:
                state, band, detail = "NEVER", "none", "NO heartbeat ever -- the driver never spawned this sub-division"
        elif not task_fresh:
            state, band = "STALE", liveness_band(now - int(task_ts), fresh=fresh, stale=stale) if task_ts else "dead"
            detail = "the account task is not cycling -- last beat %ss ago (driver dead/hung)" % (
                (now - int(task_ts)) if task_ts else "?")
        elif ev_fresh:
            band = liveness_band(now - int(ev_ts), fresh=fresh, stale=stale)
            if latched:
                state, detail = "IDLE", "cycling; order-ceiling latched -- intentionally not placing (NOT a fault)"
            elif (placed or 0) > 0:
                state, detail = "RUNNING", "cycling and PLACING (%s placed, %s signals)" % (placed, n_sig)
            elif (n_sig or 0) > 0:
                state, detail = "RUNNING", "cycling; %s signals, 0 placed this cycle" % n_sig
            else:
                state, detail = "IDLE", "cycling; 0 signals (nothing to copy right now)"
        else:
            # task alive but this category is not completing evaluation (no catalog / erroring before its write /
            # an earlier category aborting the cycle) -- a category-level signal, NOT 'driver dead'.
            band = "stale"
            state, detail = "CATEGORY_STARVED", "task alive but this category has not evaluated recently (no catalog?)"
        out.append(SubLiveness(account_id=acct, category=cat, state=state, band=band, age_sec=age,
                               n_signals=n_sig, placed=placed, ceiling_latched=latched, detail=detail))
    out.sort(key=lambda x: (_STATE_SEVERITY.get(x.state, 9), x.account_id, x.category))
    return out


def any_alarm(rows: list[SubLiveness]) -> bool:
    """True iff any expected sub-division is in an ALARM state (STALE or NEVER) -- the account-level red dot."""
    return any(r.state in ("STALE", "NEVER") for r in rows)
