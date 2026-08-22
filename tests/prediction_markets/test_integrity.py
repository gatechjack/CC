"""§3A data-integrity tests. Clause (b) [zero-cost] QUARANTINES (pnl_suspect) + event-group
propagates. Clause (a) [loss-exceeds-cost] is DEMOTED (2026-08-22, §13A(f)) to a non-excluding,
non-propagated anomaly FLAG (pnl_anomaly). data_quality is $-weighted OR count-weighted. Offline.

Spec: reports/prediction_markets/P1_PLAN.md §3A, §11; QUARANTINE_RECONCILE_2026-08-22.md.
"""
import json
from pathlib import Path

from trading_corp.prediction_markets import db, ingest, stats
from trading_corp.data.polymarket_data_api_client import ClosedPositionRow

_FIX = Path(__file__).parent / "fixtures" / "closed_positions"
NOW = 1_700_000_000


def _load(name):
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


def _raw(cid, event, tb, rp, cur=0.0):
    return {"proxyWallet": "0xw", "conditionId": cid, "slug": event, "eventSlug": event,
            "avgPrice": 0.5, "totalBought": tb, "realizedPnl": rp, "curPrice": cur, "timestamp": 1}


def _cp(cid, event, tb, rp, cur=0.0):
    return ClosedPositionRow.from_api(_raw(cid, event, tb, rp, cur))


class _Cli:
    def __init__(self, page):
        self._page = page

    async def fetch_closed_positions(self, wallet, *, limit=50, offset=0):
        rows = self._page if offset == 0 else []
        return [ClosedPositionRow.from_api(r) for r in rows]


async def _noev(slug, **kw):
    return []


# ---- clause (b): the QUARANTINE trigger (unchanged) ----

def test_clause_b_zero_cost_either_sign_is_suspect():
    assert ingest.compute_row_suspect(0.0, -574604.31) == (1, "row_invariant")
    assert ingest.compute_row_suspect(0.0, 500.0) == (1, "row_invariant")    # POSITIVE (sign-inverted phantom)
    assert ingest.compute_row_suspect(0.0, 0.0) == (0, None)                 # no attribution -> clean
    assert ingest.compute_row_suspect(-5.0, 1.0) == (1, "row_invariant")     # tb<=0


# ---- clause (a): DEMOTED to a flag, never a quarantine (§13A(f)) ----

def test_clause_a_demoted_to_anomaly_flag():
    # tb=100 -> eps=max(1, 1%)=1 -> threshold -(101). Clause (a) NO LONGER quarantines.
    assert ingest.compute_row_suspect(100.0, -101.5) == (0, None)            # NOT suspect
    assert ingest.compute_row_anomaly(100.0, -101.5) == (1, "loss_exceeds_cost")
    assert ingest.compute_row_anomaly(100.0, -100.5) == (0, None)            # within cost -> no anomaly
    assert ingest.compute_row_anomaly(100.0, -100.0) == (0, None)            # exactly cost -> ok
    assert ingest.compute_row_anomaly(0.0, -5.0) == (0, None)                # zero-cost is clause (b)'s domain


def test_clause_a_small_scale_dust_escapes():
    # tb=2 -> eps=max(1, 0.02)=1 -> threshold -(3). $2.50 on $2 escapes (accepted dust)
    assert ingest.compute_row_anomaly(2.0, -2.50) == (0, None)
    assert ingest.compute_row_anomaly(2.0, -3.01) == (1, "loss_exceeds_cost")
    assert ingest.compute_row_suspect(2.0, -3.01) == (0, None)               # never a quarantine


def test_clause_a_flags_but_not_excluded_or_propagated():
    recs = [
        ingest.cp_to_record(_cp("0x1", "mlb-x-y-2026-01-01", 100.0, -500.0), "mlb", "slug_prefix", NOW),  # clause (a)
        ingest.cp_to_record(_cp("0x2", "mlb-x-y-2026-01-01", 100.0, -50.0), "mlb", "slug_prefix", NOW),   # clean sibling
    ]
    assert recs[0]["pnl_anomaly"] == 1 and recs[0]["anomaly_reason"] == "loss_exceeds_cost"
    assert recs[0]["pnl_suspect"] == 0                       # a clause-(a) row is NOT quarantined
    ingest.apply_event_group_quarantine(recs)
    assert all(r["pnl_suspect"] == 0 for r in recs)          # ...and does NOT propagate to its sibling


# ---- clause (b) event-group quarantine (unchanged: winner-survives guard) ----

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
    assert losers and all(r["suspect_reason"] == "row_invariant" for r in losers)   # tb=0 -> clause (b)


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


# ---- the ruling end-to-end: clause (b) EXCLUDED, clause (a) INCLUDED + flagged ----

async def test_clause_b_excluded_clause_a_included_in_stats(tmp_path):
    # ufc slice, DISTINCT event_slugs (no propagation): 2 wins + 1 clean loss + 1 clause-(a) loss + 1 clause-(b).
    mixed = [
        _raw("0xg1", "ufc-g1-2026-01-01", 100.0, 80.0, 1.0),    # win, clean
        _raw("0xg2", "ufc-g2-2026-01-02", 100.0, -100.0, 0.0),  # loss, clean (== cost, not < -(101))
        _raw("0xg3", "ufc-g3-2026-01-03", 100.0, 90.0, 1.0),    # win, clean
        _raw("0xg4", "ufc-g4-2026-01-04", 100.0, -500.0, 0.0),  # clause (a): loss exceeds cost -> ANOMALY, INCLUDED
        _raw("0xg5", "ufc-g5-2026-01-05", 0.0, 300.0, 1.0),     # clause (b): zero-cost -> EXCLUDED
    ]
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        await ingest.backfill_wallet(conn, "0xm", client=_Cli(mixed), now_ts=NOW, fetch_events=_noev)
        stats.rollup(conn, now_ts=NOW)
        stats.compute_scores(conn, now_ts=NOW, min_resolved=1)
        cs = conn.execute("SELECT * FROM pm_category_stats WHERE category='ufc'").fetchone()
        g4 = conn.execute("SELECT pnl_suspect, pnl_anomaly, anomaly_reason FROM pm_closed_position WHERE condition_id='0xg4'").fetchone()
        g5 = conn.execute("SELECT pnl_suspect, pnl_anomaly FROM pm_closed_position WHERE condition_id='0xg5'").fetchone()
    # clause (a) g4: flagged, NOT excluded
    assert g4["pnl_suspect"] == 0 and g4["pnl_anomaly"] == 1 and g4["anomaly_reason"] == "loss_exceeds_cost"
    # clause (b) g5: excluded
    assert g5["pnl_suspect"] == 1
    assert cs["n_resolved"] == 4 and cs["n_excluded"] == 1 and cs["n_anomaly"] == 1
    # g4's -500 REAL loss is now INCLUDED (the whole point: do not silently drop real losses)
    assert abs(cs["net_realized_pnl"] - (80.0 - 100.0 + 90.0 - 500.0)) < 1e-6   # = -430.0
    assert abs(cs["total_bought"] - 400.0) < 1e-6                                # NOTIONAL g1..g4; g5 tb=0 excluded
    assert abs(cs["cost_basis"] - 200.0) < 1e-6                                  # 4 scoreable * (100 * 0.5 avg)
    assert abs(cs["roi"] - (-430.0 / 200.0)) < 1e-6                              # RANKED cost-based; net-loser -> negative
    assert abs(cs["roi_notional"] - (-430.0 / 400.0)) < 1e-6                     # notional (NOT ranked)


async def test_data_quality_is_dollar_weighted(tmp_path):
    # count fraction < 10% BUT $ fraction > 10% -> contaminated (the Kickstand7-Fed shape). Plus a clean control.
    rows = [_raw("0xc%d" % i, "mlb-c%d-2026-01-%02d" % (i, i + 1), 20.0, 10.0, 1.0) for i in range(12)]  # 12 clean wins
    rows.append(_raw("0xphantom", "mlb-p-2026-02-01", 0.0, 300.0, 1.0))   # clause (b), big $, 1/13 rows
    rows += [_raw("0xn%d" % i, "nba-n%d-2026-01-%02d" % (i, i + 1), 20.0, 10.0, 1.0) for i in range(3)]   # clean nba control
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        await ingest.backfill_wallet(conn, "0xm", client=_Cli(rows), now_ts=NOW, fetch_events=_noev)
        stats.rollup(conn, now_ts=NOW)
        mlb = conn.execute("SELECT * FROM pm_category_stats WHERE category='mlb'").fetchone()
        nba = conn.execute("SELECT * FROM pm_category_stats WHERE category='nba'").fetchone()
    assert mlb["n_excluded"] == 1
    assert mlb["dq_count_pct"] < 0.10          # 1/13 = 7.7% -> count alone would NOT flag
    assert mlb["dq_dollar_pct"] > 0.10         # 300/(120+300) = 71% -> $ flags it
    assert mlb["data_quality"] == "contaminated"
    assert nba["data_quality"] is None and nba["n_excluded"] == 0   # clean control not flagged
