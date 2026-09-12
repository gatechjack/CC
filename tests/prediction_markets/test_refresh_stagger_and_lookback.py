"""b1 (2-day SETTLED lookback) + the cross-account refresh STAGGER (2026-09-12).

WHY (see reports/prediction_markets/HEARTBEAT_STALL_INVESTIGATION_2026-09-12.md): the two account
tasks boot aligned and each seeds all its 900s per-category refresh timers to the same instant, so
their refreshes fire together; the big Kalshi get_markets burst (dominated by the 160-day SETTLED
fetch, ~20k MLB markets / ~19 pages) tripped shared-IP rate-limit backoff into ~200s WHOLE-ACCOUNT
stalls, and because the accounts were phase-aligned it blanked BOTH at once (~6x/6h).

b1 shrinks the settled fetch (160d -> 2d): the settled portion of the ctx is FUNCTIONALLY INERT (a
whale never signals on a settled game; a settled ticker can't be entered/exited; nothing but the
matcher's skip-reason reads it), so this only shrinks the fetch. Proven here: the constant flows into
the SETTLED min_close_ts AND the OPEN fetch stays DATE-UNBOUNDED (a still-open game can never be made
unreachable by the cut).

STAGGER: _account_refresh_phase_sec ranks the account over the DRIVER ROSTER
(driver_roster.active_driver_subdivisions -- the set main.py spawns tasks from) and returns
rank*interval/N: deterministic, restart-stable, evenly-spaced (jack->0, karen->interval/2), and it
EXCLUDES detached/orphan accounts so the spacing matches the running set.

★ COVERAGE NOTE: the boot APPLICATION of the phase (live_driver seeds last_idx = time - phase) is a
1-line wiring on top of this unit-tested helper; its END-TO-END effect (the two accounts' ~200s
events no longer coincide) is the required post-restart re-measurement acceptance, not asserted here
(driving scheduled_pm_live_loop's boot needs an authenticated venue-fetch + full journal harness).
The helper's offset value -- the load-bearing new logic -- IS unit-tested below.

Offline (the b1 builder test needs pykalshi.MarketStatus -> runs on the box like test_ctx_pagination_fix;
the phase-helper tests use only a temp sqlite).
"""
import asyncio
import sqlite3

from trading_corp.prediction_markets import live_driver as LD


# ── b1: the settled lookback is 2 days and flows into the SETTLED fetch (OPEN stays unbounded) ──
def test_settled_lookback_is_two_days():
    assert LD._SETTLED_LOOKBACK_SEC == 2 * 86400


class _CaptureClient:
    """Records the (status, fetch_all, extra) each get_markets receives; empty catalogs (we only
    check the min_close_ts filter, not the contents)."""
    def __init__(self):
        self.calls = []

    async def get_markets(self, series_ticker, status, limit=1000, fetch_all=False, **extra):
        self.calls.append({"series": series_ticker, "status": str(status),
                           "fetch_all": fetch_all, "extra": dict(extra)})
        return []

    def get(self, path):
        return {"markets": [], "cursor": ""}


def test_settled_fetch_uses_two_day_min_close_ts_and_open_is_unbounded():
    now = 1789200000
    fake = _CaptureClient()
    asyncio.run(LD.fetch_market_context(fake, now))
    settled = [c for c in fake.calls if c["status"].endswith("SETTLED")]
    openc = [c for c in fake.calls if c["status"].endswith("OPEN")]
    assert settled and openc, fake.calls
    # SETTLED carries min_close_ts == now - 2 days (the shrunk lookback flows through every series)
    for c in settled:
        assert c["extra"].get("min_close_ts") == now - 2 * 86400, c
    # OPEN is DATE-UNBOUNDED (no min_close_ts) -- cutting settled can NEVER strand a still-open game
    for c in openc:
        assert "min_close_ts" not in c["extra"], c
    # b1 does NOT touch the 2026-09-11 pagination fix: both statuses still fetch_all=True
    assert all(c["fetch_all"] for c in fake.calls), fake.calls


# ── stagger: the deterministic per-account phase helper (ranks over the DRIVER ROSTER) ──
def _mk_roster_db(path, rows):
    """rows: (account_id, account_active, category, attach_active). Builds the three tables
    active_driver_subdivisions reads so the helper ranks the real roster set."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE pm_account (account_id TEXT PRIMARY KEY, active INTEGER, secret_ref TEXT)")
    conn.execute("CREATE TABLE pm_subdivision (account_id TEXT, category TEXT, active INTEGER)")
    conn.execute("CREATE TABLE pm_subdivision_attachment (account_id TEXT, category TEXT, active INTEGER)")
    seen = set()
    for aid, aactive, cat, atactive in rows:
        if aid not in seen:
            conn.execute("INSERT INTO pm_account (account_id, active, secret_ref) VALUES (?,?,?)", (aid, aactive, aid))
            seen.add(aid)
        conn.execute("INSERT INTO pm_subdivision (account_id, category, active) VALUES (?,?,1)", (aid, cat))
        conn.execute("INSERT INTO pm_subdivision_attachment (account_id, category, active) VALUES (?,?,?)",
                     (aid, cat, atactive))
    conn.commit()
    conn.close()


def test_phase_two_accounts_are_half_interval_apart(tmp_path):
    db = str(tmp_path / "pm.db")
    _mk_roster_db(db, [("kalshi_jack", 1, "mlb", 1), ("kalshi_karen", 1, "mlb", 1)])
    assert LD._account_refresh_phase_sec(db, "kalshi_jack", 900.0) == 0.0
    assert LD._account_refresh_phase_sec(db, "kalshi_karen", 900.0) == 450.0
    # restart-stable: identical on a second call (pure function of the sorted roster + rank)
    assert LD._account_refresh_phase_sec(db, "kalshi_karen", 900.0) == 450.0


def test_phase_single_account_is_zero(tmp_path):
    db = str(tmp_path / "pm.db")
    _mk_roster_db(db, [("kalshi_jack", 1, "mlb", 1)])
    assert LD._account_refresh_phase_sec(db, "kalshi_jack", 900.0) == 0.0


def test_phase_orphan_account_excluded_keeps_running_pair_evenly_spaced(tmp_path):
    # ★ the review-#1 fix: an account that is active in pm_account but has NO active attachment
    # (detached/orphan) is NOT in the driver roster -> must NOT be counted, so the two RUNNING
    # accounts stay half-the-interval apart (not squeezed to interval/3).
    db = str(tmp_path / "pm.db")
    _mk_roster_db(db, [("kalshi_jack", 1, "mlb", 1),
                       ("kalshi_karen", 1, "mlb", 1),
                       ("kalshi_orphan", 1, "mlb", 0),   # active account row, attachment INACTIVE -> not driven
                       ("kalshi_inactive", 0, "mlb", 1)])  # inactive account -> not driven
    assert LD._account_refresh_phase_sec(db, "kalshi_jack", 900.0) == 0.0
    assert LD._account_refresh_phase_sec(db, "kalshi_karen", 900.0) == 450.0
    # the excluded accounts get no offset (they are not in the ranked roster)
    assert LD._account_refresh_phase_sec(db, "kalshi_orphan", 900.0) == 0.0
    assert LD._account_refresh_phase_sec(db, "kalshi_inactive", 900.0) == 0.0


def test_phase_unknown_account_or_bad_db_falls_back_to_zero(tmp_path):
    db = str(tmp_path / "pm.db")
    _mk_roster_db(db, [("kalshi_jack", 1, "mlb", 1), ("kalshi_karen", 1, "mlb", 1)])
    # an account not in the roster -> no offset (never a spurious stagger)
    assert LD._account_refresh_phase_sec(db, "not_an_account", 900.0) == 0.0
    # an unreadable DB -> 0.0 (the stagger must never break the driver; safe degrade to prior behaviour)
    assert LD._account_refresh_phase_sec(str(tmp_path / "missing.db"), "kalshi_jack", 900.0) == 0.0


def test_phase_scales_with_interval(tmp_path):
    db = str(tmp_path / "pm.db")
    _mk_roster_db(db, [("kalshi_jack", 1, "mlb", 1), ("kalshi_karen", 1, "mlb", 1)])
    # the offset is a fraction of whatever interval is configured (rank/N * interval)
    assert LD._account_refresh_phase_sec(db, "kalshi_karen", 600.0) == 300.0
