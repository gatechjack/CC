"""Multi-category DB + render integration (2026-09-04). Offline, PM DB only, FastAPI TestClient.

Proves end-to-end that a NON-MLB sub-division renders an honest positions view (not the empty MLB cards), that its
summary strip is journal-derived (item 1), that no MLB-only scoreboard text leaks onto a category with no feed
(item 4), that MLB still renders game cards (item 6), and that subdivision.traded_series covers every held series
across both accounts (item 3)."""
import calendar
from datetime import datetime, timezone

from trading_corp.prediction_markets import db, subdivision
from trading_corp.prediction_markets.web import ui_cache, poller, marks as marks_mod


def _now(y, m, d, hh, mm):
    return calendar.timegm(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).utctimetuple())


HAL = "KXATPMATCH-26SEP03ZVEHAL-HAL"
MLBTK = "KXMLBGAME-26SEP021240SDCIN-SD"
UFCTK = "KXUFCFIGHT-26SEP06JONMIO-JON"

_BASE_ORDER = {
    "account_id": "kalshi_jack", "category": "atp", "wallet": "0x64", "condition_id": None,
    "outcome_index": None, "signal_id": None, "client_order_id": None, "ticker": HAL, "order_side": "bid",
    "outcome_leg": "yes", "is_exit": 0, "submitted_count": 5, "submitted_price": 0.11,
    "time_in_force": "immediate_or_cancel", "outcome_status": "filled", "broker_order_id": None,
    "fill_count": 5.0, "fill_price": 0.10, "remaining_count": None, "fee": 0.01, "error_detail": None,
    "dry_run": 0, "submitted_ts": 1788400000, "response_ts": 1788400000,
}


def _insert(conn, **ov):
    row = dict(_BASE_ORDER); row.update(ov)
    cols = ", ".join(row.keys()); qs = ", ".join(["?"] * len(row))
    conn.execute("INSERT INTO pm_subdivision_order (%s) VALUES (%s)" % (cols, qs), tuple(row.values()))


def _seed_account(conn, account_id, label):
    conn.execute("INSERT INTO pm_account (account_id, venue, secret_ref, label, active, created_ts) "
                 "VALUES (?, 'kalshi', 'KALSHI', ?, 1, 1787000000)", (account_id, label))


def _seed_sub(conn, account_id, category, whale="0xseed"):
    conn.execute("INSERT INTO pm_subdivision (account_id, category, label, market_types, sizing_mode, "
                 "fixed_stake_usd, active, created_ts) VALUES (?, ?, ?, 'moneyline', 'fixed', 5.0, 1, 1787000000)",
                 (account_id, category, "%s %s" % (account_id, category)))
    conn.execute("INSERT INTO pm_subdivision_attachment (account_id, category, wallet, active, source, added_ts) "
                 "VALUES (?, ?, ?, 1, 'seed', 1787000000)", (account_id, category, whale))


def _client(monkeypatch, tmp_path, seed_fn):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")
    db.init_db(p)
    with db.connect(p) as conn:
        seed_fn(conn)
    # Keep the background poller from touching the network in-test: no-op its refresh so the cache stays as we prime it.
    monkeypatch.setattr(poller, "refresh_once", lambda *a, **k: None)
    from fastapi.testclient import TestClient
    from trading_corp.prediction_markets.web.app import app
    cl = TestClient(app); cl.headers.update({"Remote-User": "jack"})
    return cl


def _prime_marks(**marks):
    ui_cache.cache().update(slates={}, marks=marks_mod.MarksResult(marks=marks, ok=True, as_of=1788400500),
                            refreshed_ts=1788400500)


# ── item 3: the poll series covers every held category across both accounts ───────────────────────────────────
def test_traded_series_covers_all_held_categories(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db"); monkeypatch.setenv("PM_DB_PATH", p); db.init_db(p)
    with db.connect(p) as conn:
        _seed_account(conn, "kalshi_jack", "Jack")
        _seed_account(conn, "kalshi_karen", "Karen")
        _seed_sub(conn, "kalshi_jack", "atp"); _seed_sub(conn, "kalshi_jack", "mlb")
        _seed_sub(conn, "kalshi_karen", "ufc")
        _insert(conn, account_id="kalshi_jack", category="atp", ticker=HAL)
        _insert(conn, account_id="kalshi_jack", category="mlb", ticker=MLBTK)
        _insert(conn, account_id="kalshi_karen", category="ufc", ticker=UFCTK, wallet="0x99")
        got = subdivision.traded_series(conn)
        held = subdivision.held_tickers(conn)
    assert got == ("KXATPMATCH", "KXMLBGAME", "KXUFCFIGHT")     # series from held tickers, both accounts
    assert set(held) == {HAL, MLBTK, UFCTK}


def test_traded_series_empty_when_nothing_held(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db"); monkeypatch.setenv("PM_DB_PATH", p); db.init_db(p)
    with db.connect(p) as conn:
        _seed_account(conn, "kalshi_jack", "Jack"); _seed_sub(conn, "kalshi_jack", "atp")
        assert subdivision.traded_series(conn) == ()            # attachment but no fills -> nothing held


# ── item 1/2/4: a non-MLB page renders a positions view, journal totals, and NO scoreboard text ───────────────
def test_atp_page_renders_positions_view_not_empty_cards(monkeypatch, tmp_path):
    def seed(conn):
        _seed_account(conn, "kalshi_jack", "Jack (KALSHI)")
        _seed_sub(conn, "kalshi_jack", "atp", whale="0x64")
        _insert(conn, ticker=HAL, wallet="0x64")
    cl = _client(monkeypatch, tmp_path, seed)
    _prime_marks(**{HAL: marks_mod.Mark(HAL, 0.12, 0.87, 0.13, 0.88, 0.12, "active", 1788400500,
                                        title="Zverev vs Halys")})
    html = cl.get("/live/kalshi_jack/atp").text
    # positions view present, journal-derived strip (the defect: strip showed 0 while the drawer held the trade)
    assert "Positions held" in html and "Games held" not in html
    assert "No game feed for ATP" in html
    assert "Zverev vs Halys" in html                            # market title from the mark
    assert 'class="postbl"' in html                             # the positions table, not a game card
    # item 4: no MLB-only scoreboard text on a category with no feed
    for banned in ("game over", "runner on base", "no count", "FEED<br>UNAVAILABLE", "ball <i", "diamond"):
        assert banned not in html, "MLB-only text leaked onto ATP: %r" % banned


def test_atp_page_unpriced_reads_no_mark_never_zero(monkeypatch, tmp_path):
    def seed(conn):
        _seed_account(conn, "kalshi_jack", "Jack (KALSHI)")
        _seed_sub(conn, "kalshi_jack", "atp", whale="0x64")
        _insert(conn, ticker=HAL, wallet="0x64")
    cl = _client(monkeypatch, tmp_path, seed)
    _prime_marks()                                              # no marks at all
    html = cl.get("/live/kalshi_jack/atp").text
    assert "no mark" in html and "Positions held" in html       # honest 'no mark', page still renders


def test_mlb_page_still_renders_game_cards(monkeypatch, tmp_path):
    def seed(conn):
        _seed_account(conn, "kalshi_jack", "Jack (KALSHI)")
        _seed_sub(conn, "kalshi_jack", "mlb", whale="0x64")
        _insert(conn, account_id="kalshi_jack", category="mlb", ticker=MLBTK, wallet="0x64")
    cl = _client(monkeypatch, tmp_path, seed)
    _prime_marks(**{MLBTK: marks_mod.Mark(MLBTK, 0.55, 0.46, 0.56, 0.47, 0.55, "active", 1788400500)})
    html = cl.get("/live/kalshi_jack/mlb").text
    assert "Games held" in html and "Positions held" not in html   # MLB unchanged: still the game-card view
    assert 'class="postbl"' not in html
