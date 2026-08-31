"""Stage 5 R2c -- the /farm/analyze ROUTE wiring for loss-grounding. Proves at the web layer: on a cache MISS the
route re-grounds the loss set from /activity and the honest-loss block renders (recovered a_only losses + the
measured omission + the completeness bound); on a cache HIT the /activity fetch is SKIPPED entirely (a hit spends
nothing on network, not just on the LLM); and a grounding FAILURE is fail-soft (Analyze still renders, ungrounded).
Offline: a fake data client is injected (no wire); tmp PM DB only.
"""
import dataclasses

from fastapi.testclient import TestClient

from trading_corp.prediction_markets import analyze, db
from trading_corp.data.polymarket_data_api_client import ActivityRow, ClosedPositionRow

NOW = 1_700_000_000
W = "0xgroundwhaleaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


# ── seeding (minimal; mirrors test_analyze._cp shape) ────────────────────────────────────────────────
def _whale(conn, wallet, name="GroundWhale"):
    conn.execute("INSERT OR REPLACE INTO pm_whale (wallet, user_name, first_seen_ts, backfill_complete) "
                 "VALUES (?,?,?,1)", (wallet, name, NOW))


def _cp(conn, wallet, category, cid, *, oi=0, price=0.6, won=1, slug="mlb-x", event_slug="mlb-x"):
    cb = 100.0 * price
    realized = (1.0 - price) * 100.0 if won == 1 else -price * 100.0
    conn.execute(
        "INSERT INTO pm_closed_position (wallet, condition_id, outcome_index, category, slug, event_slug, "
        "title, avg_price, total_bought, cost_basis, realized_pnl, won, pnl_suspect, suspect_reason, "
        "pnl_anomaly, resolved_ts, ingested_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (wallet, cid, oi, category, slug, event_slug, "Market", price, 100.0, cb, realized, won,
         0, None, 0, NOW, NOW, NOW))


def _seed(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        _whale(conn, W)
        _cp(conn, W, "mlb", "cidW", oi=1, price=0.5, won=1)   # a closed WIN
        _cp(conn, W, "mlb", "cidL", oi=0, price=0.5, won=0)   # a closed LOSS (core losses = 1)
        conn.commit()
    return p


def _act(cid, oi, side, size, slug="mlb-x"):
    return ActivityRow.from_api({"conditionId": cid, "outcomeIndex": oi, "side": side, "size": size,
                                 "type": "TRADE", "slug": slug, "eventSlug": slug, "proxyWallet": W})


def _closed(cid, oi, cur, slug="mlb-x"):
    return ClosedPositionRow.from_api({"conditionId": cid, "outcomeIndex": oi, "curPrice": cur,
                                       "slug": slug, "eventSlug": slug, "proxyWallet": W})


def _res(win_idx):
    return {"status": "resolved", "winning_outcome_index": win_idx}


class _GroundClient:
    """Returns /activity + /closed + resolutions that recover ONE dropped held loss (a_only). cidD is held+resolved
    in /activity but ABSENT from /closed -> a_only loss; cidL/cidW are in both. honest = 1W/2L, omission 50%."""
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetch_activity(self, wallet, *, limit, offset):
        if offset:
            return []
        return [_act("cidL", 0, "BUY", 10), _act("cidW", 1, "BUY", 10), _act("cidD", 0, "BUY", 10),
                _act("cidNBA", 0, "BUY", 10, slug="nba-y")]   # an nba row that MUST be category-filtered out

    async def fetch_closed_positions(self, wallet, *, limit, offset):
        if offset:
            return []
        return [_closed("cidW", 1, 1.0), _closed("cidL", 0, 0.0)]

    async def fetch_market_resolutions(self, cids, **kw):
        table = {"cidL": _res(1), "cidW": _res(1), "cidD": _res(1), "cidNBA": _res(1)}
        return {c: table[c] for c in cids if c in table}


def _client(tmp_path, monkeypatch, client_cls):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)          # llm_unavailable path (today's prod state)
    monkeypatch.setenv("PM_DB_PATH", _seed(tmp_path))
    monkeypatch.setattr("trading_corp.data.polymarket_data_api_client.PolymarketDataAPIClient", client_cls)
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app)


def test_route_grounds_on_miss_and_renders_recovered_losses(tmp_path, monkeypatch):
    html = _client(tmp_path, monkeypatch, _GroundClient).post("/farm/analyze/%s/mlb" % W).text
    assert 'data-loss-grounded="1"' in html                         # the honest-loss block rendered
    assert "over-stated" in html                                    # a_only_losses > 0 -> the inflation is named
    assert "recovers <strong>1</strong>" in html                   # one dropped held-to-worthless loss recovered
    assert "50%" in html                                            # measured omission 1 of 2 honest losses
    assert "promotion judge" in html                                # the ONE ruled F-1 caveat (search.LOSS_OMISSION_CAVEAT)
    assert "completeness: complete" in html                         # /activity exhausted within window -> not a lower bound


def test_route_cache_hit_skips_the_activity_fetch(tmp_path, monkeypatch):
    """A cache HIT must skip the /activity grounding fetch entirely -- a hit spends nothing on network either."""
    constructed = {"n": 0}

    class _NoFetch(_GroundClient):
        def __init__(self, *a, **k):
            constructed["n"] += 1

    cl = _client(tmp_path, monkeypatch, _NoFetch)
    # pre-seed a cached verdict for (W, mlb, current skill_version) so the route hits the cache path
    with db.connect(db.pm_db_path()) as conn:                       # same PM_DB_PATH the app uses
        rep = analyze.build_pm_analysis(conn, W, "mlb", now_ts=NOW)
        analyze._cache_put(conn, dataclasses.replace(rep, verdict="a pre-cached verdict", null_reason=None,
                                                     model="test"))
        conn.commit()
    html = cl.post("/farm/analyze/%s/mlb" % W).text
    assert "a pre-cached verdict" in html and "cached" in html      # served from cache
    assert constructed["n"] == 0                                    # the network client was NEVER even constructed


def test_route_force_regrounds_even_when_cached(tmp_path, monkeypatch):
    """?force=1 rebuilds -> it must re-ground (the cache peek is bypassed), so the block reappears on a re-analyze."""
    constructed = {"n": 0}

    class _CountingGround(_GroundClient):
        def __init__(self, *a, **k):
            constructed["n"] += 1

    cl = _client(tmp_path, monkeypatch, _CountingGround)
    with db.connect(db.pm_db_path()) as conn:
        rep = analyze.build_pm_analysis(conn, W, "mlb", now_ts=NOW)
        analyze._cache_put(conn, dataclasses.replace(rep, verdict="stale", null_reason=None, model="test"))
        conn.commit()
    html = cl.post("/farm/analyze/%s/mlb?force=1" % W).text
    assert constructed["n"] == 1                                    # force re-grounded despite the cache row
    assert 'data-loss-grounded="1"' in html


def test_route_grounding_failure_is_fail_soft(tmp_path, monkeypatch):
    """A raised /activity fetch must NOT break Analyze -- it degrades to ungrounded (no block), report still 200."""
    class _Boom(_GroundClient):
        async def fetch_activity(self, *a, **k):
            raise RuntimeError("activity feed down")

    r = _client(tmp_path, monkeypatch, _Boom).post("/farm/analyze/%s/mlb" % W)
    assert r.status_code == 200
    assert 'data-loss-grounded="1"' not in r.text                   # no grounded block
    assert "roi (cost)" in r.text                                   # the deterministic report still renders in full
