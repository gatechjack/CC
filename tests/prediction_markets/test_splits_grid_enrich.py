"""Phase 2 (2026-09-25, OQ-6): the Splits Whale-Grid header is enriched with (a) a LIVE highlight + the copying
account(s) on hover for live-traded whales, and (b) each whale's PAPER W-L record + win% (the SAME reader the Farm
Watchlist uses -- farm.farm_rows(PINNED) -> pm_paper_category_stats), "--" at zero closed and THIN under 50.
Pure builder + served-page (TestClient, tmp PM DB -- rendering works; the pre-existing 24 failures are stale
assertions, not render crashes)."""
import os

from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db
from trading_corp.prediction_markets.web import live_view as LV

NOW = 1789000000


# ── pure builder: the per-whale position dict carries the paper W-L + trusted/accounts ──────────────
def _row(wallet, slug, outcome, size=100, px=0.5, ts=NOW - 120):
    return {"wallet": wallet, "slug": slug, "outcome": outcome, "title": None,
            "w_size": size, "w_px": px, "last_observed_ts": ts}


def test_builder_carries_paper_wl_and_accounts():
    rows = [_row("w1", "mlb-sd-cin-2026-09-21", "Reds"), _row("w2", "mlb-sd-cin-2026-09-21", "Padres")]
    scores = {"w1": {"tier": "PROMOTE", "analyzed": True, "name": "Kingfish",
                     "n_closed": 60, "wins": 40, "losses": 20, "win_rate": 0.6667},
              "w2": {"tier": None, "analyzed": False, "name": "domer",
                     "n_closed": 0, "wins": 0, "losses": 0, "win_rate": None}}
    trusted = {"w1": ["Jack", "Karen"]}
    ctx = LV.build_watchlist_splits(rows, [{"wallet": "w1"}, {"wallet": "w2"}], trusted, scores,
                                    category="mlb", now_ts=NOW)
    pos = {p["wallet"]: p for r in ctx["rows"] for p in r["positions"]}
    assert pos["w1"]["n_closed"] == 60 and pos["w1"]["wins"] == 40 and pos["w1"]["losses"] == 20
    assert abs(pos["w1"]["win_rate"] - 0.6667) < 1e-6
    assert pos["w1"]["trusted"] is True and pos["w1"]["copied_by"] == ["Jack", "Karen"]      # accounts for hover
    assert pos["w2"]["n_closed"] == 0 and pos["w2"]["win_rate"] is None and pos["w2"]["trusted"] is False


# ── served page: the grid header renders live highlight + accounts + W-L, "--" at 0, THIN under 50 ──
def _pt(conn, cid, w, slug, outcome, size, px, ts):
    conn.execute(
        "INSERT INTO pm_paper_trade (wallet,category,condition_id,outcome_index,slug,event_slug,title,outcome,"
        "side,entry_observed_ts,entry_price_avg_at_observation,whale_size_at_observation,size_basis,cost_basis,"
        "poll_interval_sec,entry_basis,market_end_date,last_observed_size,last_observed_ts,status,source,"
        "opened_ts,updated_ts) VALUES (?,?,?,0,?,'',NULL,?,?,?,?,?,100.0,?,1800,'e','2026-12-01',?,?,'open','t',?,?)",
        (w, "mlb", "c%d" % cid, slug, outcome, outcome, ts, px, size, 100.0 * px, size, ts, ts, ts))


def _stats(conn, w, n_closed, wins, losses, win_rate):
    conn.execute("INSERT INTO pm_paper_category_stats (wallet,category,n_closed,wins,losses,win_rate) "
                 "VALUES (?,?,?,?,?,?)", (w, "mlb", n_closed, wins, losses, win_rate))


def _grid(tmp_path, monkeypatch):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")
    db.init_db(p)
    ts = NOW - 120
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
                     "VALUES ('kalshi_jack','mlb','w1',1,'t',?)", (NOW,))                      # w1 is live-copied
        _pt(conn, 1, "w1", "mlb-sd-cin-2026-09-21", "Reds", 2000, 0.5, ts)
        _pt(conn, 2, "w2", "mlb-sd-cin-2026-09-21", "Reds", 500, 0.5, ts)
        _pt(conn, 3, "w3", "mlb-sd-cin-2026-09-21", "Padres", 100, 0.5, ts)
        _stats(conn, "w1", 60, 40, 20, 0.6667)          # >=50 closed -> NOT thin
        _stats(conn, "w2", 10, 6, 4, 0.60)              # <50 closed -> THIN
        _stats(conn, "w3", 0, 0, 0, None)               # 0 closed -> "--"
        conn.commit()
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app).get("/farm/mlb/splits?view=grid", headers={"Remote-User": "jack"})


def test_served_grid_live_highlight_and_accounts(tmp_path, monkeypatch):
    r = _grid(tmp_path, monkeypatch)
    assert r.status_code == 200
    b = r.text
    assert 'class="live"' in b                       # the live-copied whale's header is highlighted
    assert "LIVE on Jack" in b                        # the copying account is on hover (title)


def test_served_grid_paper_wl_thin_and_zero(tmp_path, monkeypatch):
    b = _grid(tmp_path, monkeypatch).text
    assert '<div class="th3">40&ndash;20 &middot; 67%</div>' in b            # w1: 60 closed -> W-L + win%, NOT thin
    assert '<div class="th3">6&ndash;4 &middot; 60% &middot; thin</div>' in b   # w2: 10 closed -> THIN under 50
    assert '<div class="th3">&mdash;</div>' in b                            # w3: 0 closed -> "--"


# ── column order (2026-09-25, Jack): live-copied (trusted) whales are the FIRST columns, all group modes ──
def test_col_order_live_whales_first():
    slug = "mlb-sd-cin-2026-09-21"
    rows = [_row("w2", slug, "Padres"), _row("w1", slug, "Reds"), _row("w3", slug, "Reds")]  # w2 untrusted, first-seen
    def _sc():
        return {"tier": None, "analyzed": False, "name": None,
                "n_closed": 0, "wins": 0, "losses": 0, "win_rate": None}
    scores = {"w1": _sc(), "w2": _sc(), "w3": _sc()}
    trusted = {"w1": ["Jack"], "w3": ["Karen"]}                                              # w1, w3 are live-copied
    ctx = LV.build_watchlist_splits(rows, [{"wallet": "w1"}, {"wallet": "w2"}, {"wallet": "w3"}],
                                    trusted, scores, category="mlb", now_ts=NOW)
    for group in ("game", "flat", "shape"):
        O = LV.splits_ordered(ctx, mode="all", sort="divergence", group=group)
        order = O["col_order"]
        assert set(order) == {"w1", "w2", "w3"}, (group, order)
        tr = [order.index(w) for w in ("w1", "w3")]
        assert max(tr) < order.index("w2"), (group, order)                                   # every live col precedes untrusted
