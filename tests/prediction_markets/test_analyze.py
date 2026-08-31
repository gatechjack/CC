"""CP3b-2 ANALYZE (forked whale narrator over RESOLVED positions). Offline; tmp DB only; NO network, NO
real LLM (a fake chat is injected for the success/error paths, and ANTHROPIC_API_KEY is cleared so the
llm_unavailable gate fires exactly as it does in production today -- the key is not wired, e3).

Covers: the deterministic aggregate MATCHES stats.rollup (Analyze can't diverge from the scoreboard); the
three data-states (ok/thin/empty) + the quarantine-zero refusal; fresh two-sided / one-sided / data-quality;
reconciliation vs a stale rollup; all FIVE reasoned-nulls + their ORDER (empty refuses before the LLM gate);
the $20/day cost ledger + cap; cache-hit-never-spends; only-successful-verdicts-are-cached; skill_version
invalidation; force re-analyze; and the /farm/analyze route rendering.

Spec: reports/prediction_markets/P2_PLAN.md §7.4 (amended); CP3b-2 rulings 2026-08-25.
"""
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import analyze, db, stats

NOW = 1_700_000_000


# ── seeding ───────────────────────────────────────────────────────────────────────────────────────────
def _whale(conn, wallet, name="Whale", backfill=1):
    conn.execute("INSERT OR REPLACE INTO pm_whale (wallet, user_name, first_seen_ts, backfill_complete) "
                 "VALUES (?,?,?,?)", (wallet, name, NOW, backfill))


def _cp(conn, wallet, category, cid, *, oi=0, price=0.6, won=1, suspect=0, anomaly=0, realized=None,
        total_bought=100.0, resolved_ts=NOW, title="Market", event_slug="evt", slug="slug"):
    """Insert one resolved position. cost_basis = total_bought*price (real USDC cost); realized defaults to
    the held-to-resolution value so net/roi are sensible."""
    cb = (total_bought or 0.0) * (price or 0.0)
    if realized is None:
        realized = (1.0 - price) * total_bought if won == 1 else -(price or 0.0) * total_bought
    conn.execute(
        "INSERT INTO pm_closed_position (wallet, condition_id, outcome_index, category, slug, event_slug, "
        "title, avg_price, total_bought, cost_basis, realized_pnl, won, pnl_suspect, suspect_reason, "
        "pnl_anomaly, resolved_ts, ingested_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (wallet, cid, oi, category, slug, event_slug, title, price, total_bought, cb, realized, won,
         suspect, ("row_invariant" if suspect else None), anomaly, resolved_ts, NOW, NOW))


def _seed(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        # thick, mixed: 10 one-sided scoreable (7 win / 3 loss) + 1 two-sided cid (2 rows) + 1 suspect (excluded)
        _whale(conn, "0xbet", "BetMechanic")
        for i in range(7):
            _cp(conn, "0xbet", "nba", "cid_w%d" % i, price=0.55, won=1)
        for i in range(3):
            _cp(conn, "0xbet", "nba", "cid_l%d" % i, price=0.55, won=0)
        _cp(conn, "0xbet", "nba", "cid_ts", oi=0, price=0.5, won=1)   # two-sided: same cid, two legs
        _cp(conn, "0xbet", "nba", "cid_ts", oi=1, price=0.5, won=0)
        _cp(conn, "0xbet", "nba", "cid_susp", price=0.9, won=0, suspect=1)   # quarantined -> n_excluded
        # thin: 3 scoreable
        _whale(conn, "0xthin", "Thin")
        for i in range(3):
            _cp(conn, "0xthin", "mlb", "t%d" % i, price=0.7, won=(1 if i < 2 else 0))
        # empty-by-quarantine (the 4751346/nfl shape): rows exist but ALL suspect -> 0 scoreable
        _whale(conn, "0xquar", "Quar")
        for i in range(4):
            _cp(conn, "0xquar", "nfl", "q%d" % i, price=0.6, won=0, suspect=1)
        conn.commit()
    return p


class _FakeResp:
    def __init__(self, content, usage):
        self.content = content
        self.response_metadata = {"usage": usage}
        self.usage_metadata = {}


class _FakeChat:
    """Injected chat: .invoke([...]) -> object with .content + .response_metadata. Counts calls so a test
    can prove a cache hit did NOT re-invoke it."""
    def __init__(self, content="This whale farms favorites with a thin edge.", usage=None, raise_exc=False):
        self.content = content
        self.usage = usage or {"input_tokens": 1000, "output_tokens": 50}
        self.raise_exc = raise_exc
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.raise_exc:
            raise RuntimeError("boom")
        return _FakeResp(self.content, self.usage)


# ── deterministic parity with the rollup ───────────────────────────────────────────────────────────────
def test_build_matches_rollup(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        stats.rollup(conn, now_ts=NOW)   # writes pm_category_stats + pm_category_onesided_stats
        cs = dict(conn.execute("SELECT * FROM pm_category_stats WHERE wallet='0xbet' AND category='nba'").fetchone())
        os = dict(conn.execute("SELECT * FROM pm_category_onesided_stats WHERE wallet='0xbet' AND category='nba'").fetchone())
        rep = analyze.build_pm_analysis(conn, "0xbet", "nba", now_ts=NOW)
    assert rep.n_resolved == cs["n_resolved"]
    assert rep.wins == cs["wins"] and rep.losses == cs["losses"]
    assert rep.n_excluded == cs["n_excluded"] == 1
    assert rep.net_realized_pnl == cs["net_realized_pnl"]
    assert rep.cost_basis == cs["cost_basis"]
    assert (rep.roi is None and cs["roi"] is None) or abs(rep.roi - cs["roi"]) < 1e-9
    assert rep.two_sided_pct is not None and abs(rep.two_sided_pct - cs["two_sided_pct"]) < 1e-9
    assert rep.n_condition_ids == cs["n_condition_ids"]
    # one-sided directional slice matches the rollup companion (the two-sided cid excluded from BOTH)
    assert rep.onesided_n == os["n_resolved"]
    assert (rep.onesided_roi is None and os["roi"] is None) or abs(rep.onesided_roi - os["roi"]) < 1e-9


def test_reconcile_flags_stale_rollup(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        stats.rollup(conn, now_ts=NOW)
        assert analyze.build_pm_analysis(conn, "0xbet", "nba", now_ts=NOW).reconciled is True
        # add a scoreable row WITHOUT re-rolling -> live rows now exceed the rollup's n_resolved
        _cp(conn, "0xbet", "nba", "cid_new", price=0.5, won=1)
        conn.commit()
        rep = analyze.build_pm_analysis(conn, "0xbet", "nba", now_ts=NOW)
    assert rep.reconciled is False
    assert rep.recon_note and "stale" in rep.recon_note.lower()
    assert rep.rollup_n_resolved == rep.n_resolved - 1


# ── data states ─────────────────────────────────────────────────────────────────────────────────────
def test_data_states_ok_thin_empty(tmp_path):
    with db.connect(_seed(tmp_path)) as conn:
        ok = analyze.build_pm_analysis(conn, "0xbet", "nba", now_ts=NOW)
        thin = analyze.build_pm_analysis(conn, "0xthin", "mlb", now_ts=NOW)
        empty = analyze.build_pm_analysis(conn, "0xquar", "nfl", now_ts=NOW)
    assert ok.data_state == "ok" and ok.n_resolved >= stats.DEFAULT_MIN_RESOLVED
    assert thin.data_state == "thin" and 0 < thin.n_resolved < stats.DEFAULT_MIN_RESOLVED
    assert empty.data_state == "empty" and empty.n_resolved == 0
    # the 4751346/nfl shape: 0 scoreable but rows exist and are all quarantined -> a REASON, not "no activity"
    assert empty.all_quarantined is True and empty.n_excluded == 4 and empty.n_total_rows == 4


def test_roi_guarded_on_zero_cost(tmp_path):
    p = str(tmp_path / "z.db")
    db.init_db(p)
    with db.connect(p) as conn:
        _whale(conn, "0xz")
        _cp(conn, "0xz", "mlb", "z0", price=0.0, won=1, total_bought=100.0)  # cost_basis == 0
        conn.commit()
        rep = analyze.build_pm_analysis(conn, "0xz", "mlb", now_ts=NOW)
    assert rep.n_resolved == 1 and rep.cost_basis == 0.0
    assert rep.roi is None          # net/cost_basis guarded (never divide by zero), mirrors stats.rollup


# ── narration gates (all five) + gate order ──────────────────────────────────────────────────────────
def _rep(conn, wallet, category):
    return analyze.build_pm_analysis(conn, wallet, category, now_ts=NOW)


def test_gate_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with db.connect(_seed(tmp_path)) as conn:
        nr = analyze.narrate(_rep(conn, "0xbet", "nba"), narrator_enabled=False)
    assert nr.null_reason == analyze.NULL_DISABLED and nr.narration is None and nr.cost_usd == 0.0


def test_gate_empty_refuses_before_llm(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with db.connect(_seed(tmp_path)) as conn:
        rep = _rep(conn, "0xquar", "nfl")
        # even WITH a working chat injected, an empty slice refuses on DATA, not the LLM (order matters)
        nr = analyze.narrate(rep, chat=_FakeChat())
    assert nr.null_reason == analyze.NULL_NO_DATA and nr.narration is None


def test_gate_llm_unavailable_when_key_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)   # the production state today (key not wired, e3)
    with db.connect(_seed(tmp_path)) as conn:
        nr = analyze.narrate(_rep(conn, "0xbet", "nba"))     # chat=None -> is_llm_available() False
    assert nr.null_reason == analyze.NULL_UNAVAILABLE and nr.narration is None and nr.cost_usd == 0.0


def test_gate_cap_hit(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with db.connect(_seed(tmp_path)) as conn:
        nr = analyze.narrate(_rep(conn, "0xbet", "nba"), chat=_FakeChat(), cap_hit=True)
    assert nr.null_reason == analyze.NULL_CAP and nr.narration is None


def test_gate_llm_error_on_raise_and_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with db.connect(_seed(tmp_path)) as conn:
        rep = _rep(conn, "0xbet", "nba")
        raised = analyze.narrate(rep, chat=_FakeChat(raise_exc=True))
        empty = analyze.narrate(rep, chat=_FakeChat(content="   "))    # whitespace-only -> empty
    assert raised.null_reason == analyze.NULL_ERROR
    assert empty.null_reason == analyze.NULL_ERROR


def test_gate_success_costs_and_narrates(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with db.connect(_seed(tmp_path)) as conn:
        nr = analyze.narrate(_rep(conn, "0xbet", "nba"), chat=_FakeChat(usage={"input_tokens": 1000, "output_tokens": 50}))
    assert nr.narration and nr.null_reason is None
    assert nr.model == analyze.PM_ANALYZE_MODEL
    # Haiku: 1000 in * $0.80/M + 50 out * $4.00/M = 0.0008 + 0.0002 = 0.001
    assert abs(nr.cost_usd - 0.001) < 1e-9 and nr.tokens_in == 1000 and nr.tokens_out == 50


# ── cost ledger + cap ─────────────────────────────────────────────────────────────────────────────────
def test_cost_ledger_accumulates_and_caps(tmp_path):
    p = _seed(tmp_path)
    day = analyze._utc_day(NOW)
    with db.connect(p) as conn:
        assert analyze.daily_cost(conn, day) == (0.0, 0)
        analyze._book_cost(conn, day, 12.5, NOW)
        analyze._book_cost(conn, day, 8.0, NOW)
        conn.commit()
        usd, n = analyze.daily_cost(conn, day)
    assert abs(usd - 20.5) < 1e-9 and n == 2
    with db.connect(p) as conn:
        assert analyze._cap_hit(conn, day, analyze.PM_ANALYZE_DAILY_CAP_USD) is True   # 20.5 >= 20


def test_analyze_whale_cap_blocks_spend(tmp_path):
    p = _seed(tmp_path)
    day = analyze._utc_day(NOW)
    with db.connect(p) as conn:
        analyze._book_cost(conn, day, 20.0, NOW)   # already at the cap
        conn.commit()
        chat = _FakeChat()
        rep = analyze.analyze_whale(conn, "0xbet", "nba", now_ts=NOW, chat=chat)
        usd, _ = analyze.daily_cost(conn, day)
    assert rep.null_reason == analyze.NULL_CAP and chat.calls == 0   # never called the LLM
    assert abs(usd - 20.0) < 1e-9                                    # no new spend


# ── cache: hit never spends, only success cached, skill_version, force ─────────────────────────────────
def test_cache_hit_never_spends(tmp_path):
    p = _seed(tmp_path)
    chat = _FakeChat()
    with db.connect(p) as conn:
        first = analyze.analyze_whale(conn, "0xbet", "nba", now_ts=NOW, chat=chat)
        day = analyze._utc_day(NOW)
        spent_after_first, _ = analyze.daily_cost(conn, day)
        second = analyze.analyze_whale(conn, "0xbet", "nba", now_ts=NOW, chat=chat)
        spent_after_second, _ = analyze.daily_cost(conn, day)
    assert first.verdict and first.served_from_cache is False and chat.calls == 1
    assert second.served_from_cache is True and second.verdict == first.verdict
    assert chat.calls == 1                                   # the hit did NOT re-invoke the LLM
    assert abs(spent_after_first - spent_after_second) < 1e-12   # the hit spent nothing


def test_reasoned_null_not_cached(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = _seed(tmp_path)
    with db.connect(p) as conn:
        rep = analyze.analyze_whale(conn, "0xbet", "nba", now_ts=NOW)   # chat=None -> llm_unavailable
        n_cached = conn.execute("SELECT COUNT(*) FROM pm_analysis_cache WHERE wallet='0xbet'").fetchone()[0]
    assert rep.null_reason == analyze.NULL_UNAVAILABLE
    assert n_cached == 0    # nulls are NOT cached -> the moment the key is wired the next analyze narrates fresh


def test_skill_version_invalidates(tmp_path):
    p = _seed(tmp_path)
    chat = _FakeChat()
    with db.connect(p) as conn:
        analyze.analyze_whale(conn, "0xbet", "nba", now_ts=NOW, chat=chat, skill_version="1")
        # a different skill_version is a different cache key -> MISS -> recompute (chat called again)
        rep2 = analyze.analyze_whale(conn, "0xbet", "nba", now_ts=NOW, chat=chat, skill_version="2")
    assert chat.calls == 2 and rep2.served_from_cache is False and rep2.skill_version == "2"


def test_force_reanalyzes(tmp_path):
    p = _seed(tmp_path)
    chat = _FakeChat()
    with db.connect(p) as conn:
        analyze.analyze_whale(conn, "0xbet", "nba", now_ts=NOW, chat=chat)
        forced = analyze.analyze_whale(conn, "0xbet", "nba", now_ts=NOW, chat=chat, force=True)
    assert chat.calls == 2 and forced.served_from_cache is False


# ── the route ──────────────────────────────────────────────────────────────────────────────────────────
class _EmptyDataClient:
    """No-network stand-in for PolymarketDataAPIClient: Stage 5 made /farm/analyze re-ground losses from /activity
    on a cache MISS, so an offline route test must inject a client that returns nothing (else it would hit the wire).
    A zero-decision fetch renders UNGROUNDED (no loss-completeness block), so these pre-Stage-5 assertions hold."""
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetch_activity(self, *a, **k):
        return []

    async def fetch_closed_positions(self, *a, **k):
        return []

    async def fetch_market_resolutions(self, *a, **k):
        return {}


def _client(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)   # llm_unavailable path (production state today)
    monkeypatch.setenv("PM_DB_PATH", _seed(tmp_path))
    monkeypatch.setattr("trading_corp.data.polymarket_data_api_client.PolymarketDataAPIClient", _EmptyDataClient)
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app)


def test_route_thick_pair_renders_llm_unavailable(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch).post("/farm/analyze/0xbet/nba")
    assert r.status_code == 200
    html = r.text
    assert 'data-null-reason="llm_unavailable"' in html
    assert "capability, not a working token" in html          # the e3 caveat is NOT softened
    assert "roi (cost)" in html and "two-sided%" in html       # deterministic report still renders in full


def test_route_empty_pair_refuses_honestly(tmp_path, monkeypatch):
    html = _client(tmp_path, monkeypatch).post("/farm/analyze/0xquar/nfl").text
    assert 'data-null-reason="no_resolved_positions"' in html   # data refusal, NOT llm_unavailable
    assert "No resolved positions" in html and "quarantined" in html


def test_route_force_param(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.post("/farm/analyze/0xthin/mlb?force=1").status_code == 200
