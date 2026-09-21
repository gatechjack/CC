"""Watchlist SPLITS (2026-09-21): the pinned-whale paper positions decoded into game x market x side stake/
headcount splits. Offline; pure reader + served-page (TestClient, tmp PM DB). Proves:
  * splits_frame -- side->A/B canonicalization (moneyline/total/spread) and FAIL-CLOSED for props/non-two-team.
  * shape thresholds (UNANIMOUS/CONSENSUS>=70/LEAN>=56/SPLIT/SINGLE), divergence>=18, thin<4 -- boundary exact.
  * trusted filter (R3: trusted=copied), guard (conflicted + copied both sides).
  * unparsed OMITTED + counted (R7); stake = shares*entry price, never notional (R4).
  * stale at 29 vs 31 min (R5); last-refresh ET timestamp rendered; no slug/ticker as a sole label.
  * Kalshi flag ABSENT (R8 dropped); GET-only (no order path); phone hides the heatmap (R10).
"""
import pytest
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db
from trading_corp.prediction_markets.web import live_view as LV

NOW = 1789000000
MIN = 60


# ── pure-reader helpers ───────────────────────────────────────────────────────────────────────────
def _row(wallet, slug, outcome, size, px, ts=NOW - 120):
    return {"wallet": wallet, "slug": slug, "outcome": outcome, "title": None,
            "w_size": size, "w_px": px, "last_observed_ts": ts}


def _build(rows, *, whales=None, trusted=None, scores=None, cat="mlb", now=NOW):
    whales = whales if whales is not None else [{"wallet": w} for w in sorted({r["wallet"] for r in rows})]
    return LV.build_watchlist_splits(rows, whales, trusted or {}, scores or {}, category=cat, now_ts=now)


# ── splits_frame ──────────────────────────────────────────────────────────────────────────────────
def test_frame_moneyline_side_ab():
    ctx = _build([_row("w1", "mlb-sd-cin-2026-09-21", "Reds", 100, 0.5),
                  _row("w2", "mlb-sd-cin-2026-09-21", "Padres", 100, 0.5)])
    r = ctx["rows"][0]
    assert r["market_type"] == "moneyline"
    assert r["A_label"] == "ML CIN" and r["B_label"] == "ML SD"     # A=home, B=away
    assert r["all"]["nA"] == 1 and r["all"]["nB"] == 1


def test_frame_total_over_under():
    ctx = _build([_row("w1", "mlb-sd-cin-2026-09-21-total-8pt5", "Over", 100, 0.5),
                  _row("w2", "mlb-sd-cin-2026-09-21-total-8pt5", "Under", 100, 0.5)])
    r = ctx["rows"][0]
    assert r["market_type"] == "total" and r["A_label"] == "Over 8.5" and r["B_label"] == "Under 8.5"


def test_frame_spread_anchor():
    ctx = _build([_row("w1", "mlb-nyy-bal-2026-09-21-spread-home-1pt5", "Orioles", 100, 0.5)])
    r = ctx["rows"][0]
    assert r["market_type"] == "spread" and "-1.5" in r["A_label"] and "+1.5" in r["B_label"]


def test_frame_fail_closed_prop():
    ctx = _build([_row("w1", "mlb-sd-cin-2026-09-21-nrfi", "Yes", 100, 0.5),
                  _row("w2", "mlb-worldseries-2026-champion", "Reds", 100, 0.5)])
    assert ctx["n_markets"] == 0 and ctx["unparsed_count"] == 2      # R7: omitted + counted


def test_unsupported_category():
    ctx = _build([_row("w1", "atp-x-y-2026-09-21", "X", 100, 0.5)], cat="atp")
    assert ctx["supported"] is False and ctx["n_markets"] == 0


# ── shapes / thresholds (R9) ───────────────────────────────────────────────────────────────────────
def _ml(n_home, n_away):
    rows = [_row("h%d" % i, "mlb-sd-cin-2026-09-21", "Reds", 100, 0.5) for i in range(n_home)]
    rows += [_row("a%d" % i, "mlb-sd-cin-2026-09-21", "Padres", 100, 0.5) for i in range(n_away)]
    return _build(rows)["rows"][0]


def test_shape_single():
    assert _ml(1, 0)["shape"] == "SINGLE"


def test_shape_unanimous():
    assert _ml(5, 0)["shape"] == "UNANIMOUS"


def test_shape_consensus_at_70():
    assert _ml(7, 3)["shape"] == "CONSENSUS"          # 70% exactly


def test_shape_lean_at_56():
    r = _ml(9, 7)                                      # 56.25%
    assert r["shape"] == "LEAN"


def test_shape_split_below_56():
    assert _ml(3, 3)["shape"] == "SPLIT"              # 50%


def test_thin_boundary_3_vs_4():
    assert _ml(3, 0)["thin_all"] is True             # 3 < 4
    assert _ml(4, 0)["thin_all"] is False            # 4 not thin


def test_divergence_boundary_18():
    # 4 whales, even headcount (50/50, cA=50). gap = kA - cA. price 1.0 so cost == size.
    def gap_rows(a1, a2, b1, b2):
        return _build([_row("w1", "mlb-sd-cin-2026-09-21", "Reds", a1, 1.0),
                       _row("w2", "mlb-sd-cin-2026-09-21", "Reds", a2, 1.0),
                       _row("w3", "mlb-sd-cin-2026-09-21", "Padres", b1, 1.0),
                       _row("w4", "mlb-sd-cin-2026-09-21", "Padres", b2, 1.0)])["rows"][0]
    r18 = gap_rows(34, 34, 16, 16)          # A stake 68 / total 100 -> kA 68, gap 18
    assert abs(r18["gap"]) == pytest.approx(18.0) and r18["diverges"] is True
    r17 = gap_rows(33.5, 33.5, 16.5, 16.5)  # A stake 67 -> kA 67, gap 17
    assert abs(r17["gap"]) == pytest.approx(17.0) and r17["diverges"] is False


# ── stake = entry cost, never notional (R4) ─────────────────────────────────────────────────────────
def test_stake_is_shares_times_entry_price():
    ctx = _build([_row("w1", "mlb-sd-cin-2026-09-21", "Reds", 1500, 0.5)])
    assert ctx["rows"][0]["all"]["stake"] == pytest.approx(750.0)   # 1500 * 0.50, NOT 1500 (notional)


# ── trusted (R3) + guard ────────────────────────────────────────────────────────────────────────────
def test_trusted_filter_and_mode():
    rows = [_row("w1", "mlb-sd-cin-2026-09-21", "Reds", 100, 0.5),
            _row("w2", "mlb-sd-cin-2026-09-21", "Reds", 100, 0.5),
            _row("w3", "mlb-nyy-bal-2026-09-21", "Orioles", 100, 0.5)]   # no trusted whale
    trusted = {"w1": ["Jack"]}
    ctx = _build(rows, trusted=trusted)
    o = LV.splits_ordered(ctx, mode="trusted")
    flat = o["games"]
    # trusted mode: the nyy/bal row (no trusted) is dropped; only the cin row (w1 trusted) survives
    keys = [m["matchup"] for g in flat for m in g["markets"]]
    assert keys == ["SD @ CIN"]
    cin = ctx["rows"]
    cinrow = next(r for r in cin if r["matchup"] == "SD @ CIN")
    assert cinrow["trusted"]["n"] == 1 and cinrow["all"]["n"] == 2


def test_guard_conflicted_both_sides_copied():
    rows = [_row("w1", "mlb-sd-cin-2026-09-21", "Reds", 100, 0.5),
            _row("w2", "mlb-sd-cin-2026-09-21", "Padres", 100, 0.5)]     # SPLIT
    trusted = {"w1": ["Jack"], "w2": ["Karen"]}                          # both sides copied
    r = _build(rows, trusted=trusted)["rows"][0]
    assert r["conflicted"] is True and r["guard"] is True


# ── stale (R5) ──────────────────────────────────────────────────────────────────────────────────────
def test_stale_29_vs_31_min():
    fresh = _build([_row("w1", "mlb-sd-cin-2026-09-21", "Reds", 100, 0.5, ts=NOW - 29 * MIN)])
    stale = _build([_row("w1", "mlb-sd-cin-2026-09-21", "Reds", 100, 0.5, ts=NOW - 31 * MIN)])
    assert fresh["stale"] is False and stale["stale"] is True


def test_refresh_et_present():
    ctx = _build([_row("w1", "mlb-sd-cin-2026-09-21", "Reds", 100, 0.5)])
    assert ctx["refresh_et"] and ctx["refresh_et"].endswith("ET")


# ── sort + treemap ──────────────────────────────────────────────────────────────────────────────────
def test_sort_stake_desc():
    rows = [_row("w1", "mlb-sd-cin-2026-09-21", "Reds", 100, 0.5),
            _row("w2", "mlb-nyy-bal-2026-09-21", "Orioles", 5000, 0.5)]
    ctx = _build(rows)
    o = LV.splits_ordered(ctx, sort="stake", group="flat")
    assert o["flat"][0]["matchup"] == "NYY @ BAL"       # biggest stake first


def test_treemap_area_tracks_stake():
    rows = [_row("w1", "mlb-sd-cin-2026-09-21", "Reds", 100, 0.5),
            _row("w2", "mlb-nyy-bal-2026-09-21", "Orioles", 300, 0.5)]
    ctx = _build(rows)
    tiles = LV.splits_treemap(ctx["rows"])
    assert len(tiles) == 2
    area = {t["row"]["matchup"]: t["w"] * t["h"] for t in tiles}
    assert area["NYY @ BAL"] > area["SD @ CIN"]         # 3x stake -> larger tile


# ── served page (TestClient) ─────────────────────────────────────────────────────────────────────────
def _seed_and_client(tmp_path, monkeypatch, ts):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")
    db.init_db(p)
    cid = [0]
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_account (account_id,venue,secret_ref,label,active,created_ts,owner_identity) "
                     "VALUES ('kalshi_jack','kalshi','K','Jack',1,?,NULL)", (NOW,))
        for w, nm, tier in (("w1", "Kingfish", "PROMOTE"), ("w2", "domer-1848", "WATCH"), ("w3", "bpsniper", None)):
            conn.execute("INSERT INTO pm_watchlist (wallet,category,status,active,pinned_ts) VALUES (?,?,'pinned',1,?)", (w, "mlb", NOW))
            conn.execute("INSERT OR IGNORE INTO pm_whale (wallet,user_name) VALUES (?,?)", (w, nm))
            if tier:
                conn.execute("INSERT INTO pm_whale_score (wallet,category,tier,reason,sort_roi,grounded,omission_pct,"
                             "coverage_pct,omission_floor,honest_roi,copy_fills,computed_ts) VALUES (?,?,?,?,0.2,1,0,0.9,0,0.2,0,?)",
                             (w, "mlb", tier, tier, NOW))
        conn.execute("INSERT INTO pm_subdivision_attachment (account_id,category,wallet,active,source,added_ts) "
                     "VALUES ('kalshi_jack','mlb','w1',1,'t',?)", (NOW,))

        def pt(w, slug, outcome, size, px):
            cid[0] += 1
            conn.execute(
                "INSERT INTO pm_paper_trade (wallet,category,condition_id,outcome_index,slug,event_slug,title,outcome,"
                "side,entry_observed_ts,entry_price_avg_at_observation,whale_size_at_observation,size_basis,cost_basis,"
                "poll_interval_sec,entry_basis,market_end_date,last_observed_size,last_observed_ts,status,source,"
                "opened_ts,updated_ts) VALUES (?,?,?,0,?,'',NULL,?,?,?,?,?,100.0,?,1800,'e','2026-12-01',?,?,'open','t',?,?)",
                (w, "mlb", "c%d" % cid[0], slug, outcome, outcome, ts, px, size, 100.0 * px, size, ts, ts, ts))
        pt("w1", "mlb-sd-cin-2026-09-21", "Reds", 2000, 0.5)
        pt("w2", "mlb-sd-cin-2026-09-21", "Reds", 500, 0.5)
        pt("w3", "mlb-sd-cin-2026-09-21", "Padres", 100, 0.5)
        pt("w1", "mlb-sd-cin-2026-09-21-nrfi", "Yes", 100, 0.4)      # prop -> unparsed
        conn.commit()
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app)


def test_served_no_raw_slug_or_ticker(tmp_path, monkeypatch):
    cl = _seed_and_client(tmp_path, monkeypatch, NOW - 120)
    b = cl.get("/farm/mlb/splits", headers={"Remote-User": "jack"}).text
    assert "mlb-sd-cin" not in b and "KXMLB" not in b            # never a raw slug/ticker as a label
    assert "ML CIN" in b and "ML SD" in b                        # team-code labels instead
    assert "1 position not grouped" in b or "not grouped" in b   # unparsed prop counted


def test_served_kalshi_flag_absent(tmp_path, monkeypatch):
    cl = _seed_and_client(tmp_path, monkeypatch, NOW - 120)
    b = cl.get("/farm/mlb/splits", headers={"Remote-User": "jack"}).text
    assert "KALSHI" not in b and "on Kalshi" not in b           # R8: flag dropped entirely


def test_served_refresh_et_and_readonly(tmp_path, monkeypatch):
    cl = _seed_and_client(tmp_path, monkeypatch, NOW - 120)
    r = cl.get("/farm/mlb/splits", headers={"Remote-User": "jack"})
    assert r.status_code == 200
    assert "ET" in r.text and "last refresh" in r.text and "read-only" in r.text
    assert "pinned" not in r.text and "candidate" not in r.text     # F-3 vocab: screen word is "Watchlist"
    # GET-only: POST to the same path is not a route (no order path)
    assert cl.post("/farm/mlb/splits", headers={"Remote-User": "jack"}).status_code in (404, 405)


def test_served_not_analyzed(tmp_path, monkeypatch):
    cl = _seed_and_client(tmp_path, monkeypatch, NOW - 120)
    b = cl.get("/farm/mlb/splits", headers={"Remote-User": "jack"}).text
    # w3 (bpsniper) has no score row -> expand who + summary shows "not analyzed", never a 0/tier for it
    assert "not analyzed" in b


def test_served_phone_hides_heatmap(tmp_path, monkeypatch):
    cl = _seed_and_client(tmp_path, monkeypatch, NOW - 120)
    b = cl.get("/farm/mlb/splits", headers={"Remote-User": "jack"}).text
    assert ".tm{display:none}" in b.replace(" ", "")            # R10 media rule present


def test_served_stale_banner(tmp_path, monkeypatch):
    cl = _seed_and_client(tmp_path, monkeypatch, NOW - 40 * MIN)   # older than 30 min
    b = cl.get("/farm/mlb/splits", headers={"Remote-User": "jack"}).text
    assert "STALE READ" in b
