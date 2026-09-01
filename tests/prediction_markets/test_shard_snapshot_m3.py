"""M3 shard-balance snapshots (2026-09-01). Offline, PM DB only, fixture-free/self-runnable. Proves migration 016
creates pm_shard_balance_snapshot; the engine write -> pm_web read round-trip preserves the PER-SHARD split (not
just the total); the reader returns the LATEST with an AGE + staleness BAND (the stale-as-current guard); an UNKNOWN
breakdown stays unknown (never coerced to $0); and every read is defensive (no rows / no table -> None, never a 500)."""
import os
import tempfile

from trading_corp.prediction_markets import db, shard_snapshot as ss
from trading_corp.prediction_markets.shard_balance import ShardBalances


def _db():
    d = tempfile.mkdtemp(); p = os.path.join(d, "pm.db"); os.environ["PM_DB_PATH"] = p; db.init_db(p); return p


def test_migration_016_creates_table_and_head_is_16():
    p = _db()
    with db.connect(p) as c:
        assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 16
        assert c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_shard_balance_snapshot'").fetchone()
    assert db.SCHEMA_HEAD == 16


def test_write_read_round_trip_preserves_per_shard_split():
    p = _db()
    sb = ShardBalances(total_dollars=473.60, by_shard={0: 0.0081, 3: 473.5897}, has_breakdown=True, updated_ts=1788000000)
    with db.connect(p) as c:
        ss.write_snapshot(c, "kalshi_jack", sb, snapshot_ts=1788240000)
        v = ss.read_latest(c, "kalshi_jack", now_ts=1788240000 + 120)      # 2 min later
    assert v is not None
    assert v.by_shard == {0: 0.0081, 3: 473.5897}                          # the SPLIT survived (int keys restored)
    assert abs(v.total_dollars - 473.60) < 1e-9 and v.has_breakdown is True
    assert v.age_sec == 120 and v.age_band == "fresh"                      # age travels with the number


def test_latest_wins_and_age_bands():
    p = _db()
    with db.connect(p) as c:
        ss.write_snapshot(c, "kalshi_jack", ShardBalances(100.0, {3: 100.0}, True, None), 1000)
        ss.write_snapshot(c, "kalshi_jack", ShardBalances(120.0, {3: 120.0}, True, None), 2000)   # newer
        fresh = ss.read_latest(c, "kalshi_jack", now_ts=2000 + 5 * 60)      # 5 min
        stale = ss.read_latest(c, "kalshi_jack", now_ts=2000 + 30 * 60)     # 30 min
        vstale = ss.read_latest(c, "kalshi_jack", now_ts=2000 + 90 * 60)    # 90 min
    assert fresh.total_dollars == 120.0 and fresh.by_shard == {3: 120.0}    # the LATEST snapshot
    assert fresh.age_band == "fresh" and stale.age_band == "stale" and vstale.age_band == "very_stale"


def test_unknown_breakdown_stays_unknown_not_zero():
    p = _db()
    # a subaccount-restricted key -> no breakdown; the split is UNKNOWN, not $0 on every shard
    with db.connect(p) as c:
        ss.write_snapshot(c, "kalshi_karen", ShardBalances(50.0, {}, has_breakdown=False), 1000)
        v = ss.read_latest(c, "kalshi_karen", now_ts=1000)
    assert v.has_breakdown is False and v.by_shard == {} and v.total_dollars == 50.0


def test_reads_are_defensive():
    p = _db()
    with db.connect(p) as c:
        assert ss.read_latest(c, "kalshi_jack") is None                    # table present, no rows -> None
        assert ss.read_latest(c, "nobody") is None
    assert ss.age_band(0) == "fresh" and ss.age_band(20 * 60) == "stale" and ss.age_band(2 * 3600) == "very_stale"


def test_table_present_distinguishes_absent_from_empty():
    # migration 016 applied by init_db -> the table is PRESENT even with 0 rows; read_latest is None. The page uses
    # table_present to tell "no snapshot yet" (present+empty) from "arrives with the writer" (absent).
    p = _db()
    with db.connect(p) as c:
        assert ss.table_present(c) is True
        assert ss.read_latest(c, "kalshi_jack") is None


def test_shard_direction_returning_rising_building():
    p = _db()
    with db.connect(p) as c:
        ss.write_snapshot(c, "one", ShardBalances(100.0, {0: 0.01, 3: 100.0}, True), 1000)
        assert ss.shard_direction(c, "one").verdict == "building"                          # <2 snapshots
        ss.write_snapshot(c, "one", ShardBalances(120.0, {0: 0.01, 3: 120.0}, True), 1000 + 7200)   # +2h, shard-0 flat
        assert ss.shard_direction(c, "one").verdict == "returning"                         # flat -> return-to-3
        ss.write_snapshot(c, "sweep", ShardBalances(50.0, {0: 0.01, 3: 50.0}, True), 2000)
        ss.write_snapshot(c, "sweep", ShardBalances(50.0, {0: 5.00, 3: 45.0}, True), 2000 + 7200)   # shard-0 +$4.99
        assert ss.shard_direction(c, "sweep").verdict == "rising"                          # sweeping to shard 0
        ss.write_snapshot(c, "short", ShardBalances(10.0, {0: 0.01}, True), 3000)
        ss.write_snapshot(c, "short", ShardBalances(10.0, {0: 9.0}, True), 3000 + 600)     # big move but only +10m
        assert ss.shard_direction(c, "short").verdict == "building"                        # span < 1h -> not judged yet


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for f in fns:
        f(); print("PASS", f.__name__)
    print("ALL %d PASS" % len(fns))
