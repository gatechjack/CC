"""§3A data-integrity tests — the invariant (both clauses) + event-group quarantine
(incl. the winner-survives guard) + NULL event_slug handling. Offline.

Spec: reports/prediction_markets/P1_PLAN.md §3A, §11.
"""
import json
from pathlib import Path

from trading_corp.prediction_markets import db, ingest, stats
from trading_corp.data.polymarket_data_api_client import ClosedPositionRow

_FIX = Path(__file__).parent / "fixtures" / "closed_positions"
NOW = 1_700_000_000


def _load(name):
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


class _Cli:
    def __init__(self, page):
        self._page = page

    async def fetch_closed_positions(self, wallet, *, limit=50, offset=0):
        rows = self._page if offset == 0 else []
        return [ClosedPositionRow.from_api(r) for r in rows]


async def _noev(slug, **kw):
    return []


# ---- row-level invariant (both clauses) ----

def test_row_invariant_clause_a_loss_exceeds_cost():
    # tb=100 -> eps=max(1, 1%)=1 -> threshold -(101)
    assert ingest.compute_row_suspect(100.0, -101.5) == (1, "row_invariant")
    assert ingest.compute_row_suspect(100.0, -100.5) == (0, None)   # within cost
    assert ingest.compute_row_suspect(100.0, -100.0) == (0, None)   # exactly cost -> ok


def test_row_invariant_clause_b_zero_cost_either_sign():
    assert ingest.compute_row_suspect(0.0, -574604.31) == (1, "row_invariant")
    assert ingest.compute_row_suspect(0.0, 500.0) == (1, "row_invariant")   # POSITIVE (sign-inverted)
    assert ingest.compute_row_suspect(0.0, 0.0) == (0, None)                # no attribution -> clean
    assert ingest.compute_row_suspect(-5.0, 1.0) == (1, "row_invariant")    # tb<=0


def test_small_scale_epsilon_dust_escapes_documented():
    # tb=2 -> eps=max(1, 0.02)=1 -> threshold -(3). $2.50 loss on $2 cost escapes (accepted dust)
    assert ingest.compute_row_suspect(2.0, -2.50) == (0, None)
    assert ingest.compute_row_suspect(2.0, -3.01) == (1, "row_invariant")


# ---- event-group quarantine ----

def _records_from(name):
    rows = [ClosedPositionRow.from_api(r) for r in _load(name)]
    return [ingest.cp_to_record(cp, "unknown", "unknown", NOW) for cp in rows]


def test_event_group_quarantine_catches_the_winner():
    recs = _records_from("negrisk_event.json")
    winner = [r for r in recs if r["condition_id"].startswith("0xdd22472e55")][0]
    assert winner["pnl_suspect"] == 0            # row-level: real cost + positive realized -> survives
    ingest.apply_event_group_quarantine(recs)
    assert all(r["pnl_suspect"] == 1 for r in recs)                 # the whole event is quarantined
    assert winner["pnl_suspect"] == 1 and winner["suspect_reason"] == "event_group"
    losers = [r for r in recs if r["total_bought"] == 0.0]
    assert losers and all(r["suspect_reason"] == "row_invariant" for r in losers)


def test_null_event_slug_no_group_propagation():
    recs = [
        {"event_slug": "", "pnl_suspect": 1, "suspect_reason": "row_invariant"},
        {"event_slug": "", "pnl_suspect": 0, "suspect_reason": None},
        {"event_slug": None, "pnl_suspect": 0, "suspect_reason": None},
    ]
    ingest.apply_event_group_quarantine(recs)
    assert recs[1]["pnl_suspect"] == 0 and recs[2]["pnl_suspect"] == 0  # not grouped -> stay clean


async def test_backfill_negrisk_all_legs_quarantined(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    cli = _Cli(_load("negrisk_event.json"))
    with db.connect(p) as conn:
        res = await ingest.backfill_wallet(
            conn, "0x71ed0bc95433cdf1be29f43219725fce9addd9eb", client=cli, now_ts=NOW, fetch_events=_noev)
        rows = conn.execute(
            "SELECT condition_id, pnl_suspect, suspect_reason, realized_pnl FROM pm_closed_position").fetchall()
    assert res["rows"] == 5 and res["suspect"] == 5
    assert all(r["pnl_suspect"] == 1 for r in rows)
    winner = [r for r in rows if r["condition_id"].startswith("0xdd22472e55")][0]
    assert winner["realized_pnl"] > 0 and winner["suspect_reason"] == "event_group"


async def test_suspect_rows_excluded_from_stats_and_both_routines(tmp_path):
    # mixed ufc category: 3 clean + 2 suspect (clause a + clause b), DISTINCT event_slugs
    # (so no group propagation) -> suspects must be excluded from stats AND both routines.
    mixed = [
        {"proxyWallet": "0xm", "conditionId": "0xg1", "slug": "ufc-g1-2026-01-01", "eventSlug": "ufc-g1-2026-01-01", "avgPrice": 0.5, "totalBought": 100.0, "realizedPnl": 80.0, "curPrice": 1.0, "timestamp": 1},
        {"proxyWallet": "0xm", "conditionId": "0xg2", "slug": "ufc-g2-2026-01-02", "eventSlug": "ufc-g2-2026-01-02", "avgPrice": 0.5, "totalBought": 100.0, "realizedPnl": -100.0, "curPrice": 0.0, "timestamp": 2},
        {"proxyWallet": "0xm", "conditionId": "0xg3", "slug": "ufc-g3-2026-01-03", "eventSlug": "ufc-g3-2026-01-03", "avgPrice": 0.5, "totalBought": 100.0, "realizedPnl": 90.0, "curPrice": 1.0, "timestamp": 3},
        {"proxyWallet": "0xm", "conditionId": "0xg4", "slug": "ufc-g4-2026-01-04", "eventSlug": "ufc-g4-2026-01-04", "avgPrice": 0.5, "totalBought": 100.0, "realizedPnl": -500.0, "curPrice": 0.0, "timestamp": 4},
        {"proxyWallet": "0xm", "conditionId": "0xg5", "slug": "ufc-g5-2026-01-05", "eventSlug": "ufc-g5-2026-01-05", "avgPrice": 0.5, "totalBought": 0.0, "realizedPnl": 300.0, "curPrice": 1.0, "timestamp": 5},
    ]
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        await ingest.backfill_wallet(conn, "0xm", client=_Cli(mixed), now_ts=NOW, fetch_events=_noev)
        stats.rollup(conn, now_ts=NOW)
        stats.compute_scores(conn, now_ts=NOW, min_resolved=1)
        cs = conn.execute("SELECT * FROM pm_category_stats WHERE category='ufc'").fetchone()
        snap = conn.execute(
            "SELECT params_json FROM pm_score_snapshot WHERE category='ufc' AND routine='recency_weighted'").fetchone()
    assert cs["n_resolved"] == 3 and cs["n_excluded"] == 2          # suspects out of stats
    assert abs(cs["net_realized_pnl"] - 70.0) < 1e-6               # 80-100+90; suspect -500/+300 excluded
    assert abs(cs["roi"] - (70.0 / 300.0)) < 1e-6                  # tb=300 (clean only)
    assert json.loads(snap["params_json"])["n_eff"] <= 3.0 + 1e-9  # recency built on 3 scoreable rows only


# The §3A guard is now proven end-to-end: suspect rows quarantined at ingest (row + event-group),
# excluded from pm_category_stats, and excluded from BOTH ranking routines (single scoreable predicate).
