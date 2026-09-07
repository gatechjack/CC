"""Stage 3 R3 -- the LIVE sub-division list (read-only) + target-account credential presence, EXTENDED (2026-08-30)
to the real live-trade section: the /live sub-division page now reads pm_subdivision_order (the engine's order
journal) and shows the trades + the journal-derived open positions. R3 was built when that table was EMPTY and the
page hardcoded "no live trades yet"; a green test asserted only the empty state (the standing lesson: a test that
never runs the real path proves nothing). These tests now exercise BOTH paths -- honest-empty AND a REAL filled row
whose shape mirrors the platform's first live fill (KXMLBGAME YES, filled 1 @ 0.60).

Offline, FastAPI TestClient, PM DB only. Proves: honest-empty when migration 010 is NOT deployed (schema 9, tables
absent) AND when there are simply no orders; tile-on-CREATE at schema 10; 404 for a missing sub-division; READ-ONLY
(no POST route, no order/arm controls) EVEN with a filled order present; the real trade + position render; the
sizing display states behaviour (1 contract per copy) not the misleading raw $0.01 stake; the tile shows a trade
count once traded; and that `assert_live_ready` fails LOUD on a missing KALSHI key (without going live).
"""
import pytest
from fastapi.testclient import TestClient

from trading_corp.prediction_markets import db, subdivision


# The platform's FIRST real live fill, field-for-field (the standing-lesson fixture: mirror the REAL row). YES leg,
# entry, submitted 1 @ 0.62 IOC, FILLED 1 @ 0.60, fee 0.0084, dry_run=0. Overridable per test.
_REAL_ORDER = {
    "account_id": "kalshi_jack", "category": "mlb",
    "wallet": "0x16bb9951a36fce71e2ef57890b786145e0ba8492",
    "condition_id": "0x9c62c626cfe36f5273fa016e27803a00c75a19a62a044a1941f83c55706bf97b",
    "outcome_index": 1, "signal_id": "83c8bf91aa7ccc3196b39e9aecae282b",
    "client_order_id": "0752f7f6-b49b-590f-ba10-dd76d3d82b82",
    "ticker": "KXMLBGAME-26AUG301920CINCHC-CHC", "order_side": "bid", "outcome_leg": "yes",
    "is_exit": 0, "submitted_count": 1, "submitted_price": 0.62, "time_in_force": "immediate_or_cancel",
    "outcome_status": "filled", "broker_order_id": "01a054bd-1528-7118-8760-a7a064d75711",
    "fill_count": 1.0, "fill_price": 0.60, "remaining_count": None, "fee": 0.0084, "error_detail": None,
    "dry_run": 0, "submitted_ts": 1788128073, "response_ts": 1788128073,
}


def _insert_order(conn, **overrides):
    row = dict(_REAL_ORDER)
    row.update(overrides)
    cols = ", ".join(row.keys())
    qs = ", ".join(["?"] * len(row))
    conn.execute("INSERT INTO pm_subdivision_order (%s) VALUES (%s)" % (cols, qs), tuple(row.values()))


def _client(monkeypatch, tmp_path, *, schema=10, seed=False, stake=5.0, orders=None):
    p = str(tmp_path / "pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    if schema == 9:
        monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:9])   # stop before migration 010
    db.init_db(p)
    if seed:
        with db.connect(p) as conn:
            conn.execute("INSERT INTO pm_account (account_id, venue, secret_ref, label, active, created_ts) "
                         "VALUES ('kalshi_jack','kalshi','KALSHI','Jack (KALSHI)',1,1787000000)")
            conn.execute("INSERT INTO pm_subdivision (account_id, category, label, market_types, sizing_mode, "
                         "fixed_stake_usd, active, created_ts) VALUES "
                         "('kalshi_jack','mlb','Jack MLB','moneyline,total,spread','fixed',?,1,1787000000)", (stake,))
            # R6 ruling 3: a sub-division is VISIBLE in the /live list only when it has >=1 ACTIVE attachment.
            # Auto-create (ruling 1) always attaches, so a real sub-division always has one -> seed an attachment
            # so tile-on-create (created-with-an-attachment shows immediately) reflects the reconciliation.
            conn.execute("INSERT INTO pm_subdivision_attachment (account_id, category, wallet, active, source, added_ts) "
                         "VALUES ('kalshi_jack','mlb','0xseedwhale',1,'seed',1787000000)")
            for o in (orders or []):
                _insert_order(conn, **o)
    from trading_corp.prediction_markets.web.app import app
    return TestClient(app)


def test_live_list_honest_empty_when_tables_absent_schema9(monkeypatch, tmp_path):
    """Migration 010 NOT deployed (live schema 9): /live must render honest-empty, NOT 500. This is what lets R3
    deploy on a pm_web restart independent of the migration-010 deploy."""
    cl = _client(monkeypatch, tmp_path, schema=9)
    r = cl.get("/live")
    assert r.status_code == 200
    assert "No live sub-divisions yet" in r.text
    assert "Live sub-divisions" in r.text


def test_live_list_honest_empty_when_no_subdivisions_schema10(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path, schema=10, seed=False)
    r = cl.get("/live")
    assert r.status_code == 200
    assert "No live sub-divisions yet" in r.text


def test_live_tile_on_create_schema10(monkeypatch, tmp_path):
    """Tile-on-CREATE: a sub-division that exists but has never traded still shows a tile with an honest empty
    hint -- information, not an error. With no orders the hint stays 'created ... no live trades yet'."""
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True)
    r = cl.get("/live")
    assert r.status_code == 200
    assert 'href="/live/kalshi_jack/mlb"' in r.text
    assert "Jack (KALSHI)" in r.text and "MLB" in r.text
    assert "no live trades yet" in r.text
    assert "No live sub-divisions yet" not in r.text


def test_live_tile_shows_trade_count_once_traded(monkeypatch, tmp_path):
    """★ THE TILE STOPS SAYING 'no live trades yet' ONCE A REAL ORDER EXISTS (the tile-hint half of the defect)."""
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True, orders=[{}])   # one real fill
    r = cl.get("/live")
    assert r.status_code == 200
    assert "1 live trade" in r.text
    assert "no live trades yet" not in r.text


def test_live_subdivision_honest_empty_wording(monkeypatch, tmp_path):
    """Honest-empty for a sub-division that truly has not traded -- but the wording must NOT imply the engine does
    not exist (the old 'Live copies arrive with the execution engine' is gone)."""
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True)   # no orders
    r = cl.get("/live/kalshi_jack/mlb")
    assert r.status_code == 200
    assert "No live trades yet" in r.text
    assert "arrive with the execution engine" not in r.text
    assert "No open positions" in r.text
    # a sub-division that does not exist -> 404, never a fabricated page
    assert cl.get("/live/nope/mlb").status_code == 404
    assert cl.get("/live/kalshi_jack/nba").status_code == 404


def test_live_subdivision_shows_real_filled_order(monkeypatch, tmp_path):
    """★ THE DEFECT, DIRECT: a filled dry_run=0 order MUST render in the Live trades table AND as an open position.
    Fixture mirrors the real first fill (KXMLBGAME YES, filled 1 @ 0.60)."""
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True, stake=0.01, orders=[{}])
    r = cl.get("/live/kalshi_jack/mlb")
    assert r.status_code == 200
    html = r.text
    # the empty placeholder is GONE now that a trade exists
    assert "No live trades yet" not in html
    # the trade row: ticker, derived market type, leg, entry, submitted vs fill, fee, status
    assert "KXMLBGAME-26AUG301920CINCHC-CHC" in html
    assert "moneyline" in html                    # derived from the KXMLBGAME series
    assert "YES" in html and "ENTRY" in html
    assert "$0.62" in html and "$0.60" in html    # submitted vs fill (distinct)
    assert "$0.0084" in html                       # fee
    assert "filled" in html
    # the OPEN POSITION: held 1 YES contract, cost basis $0.60, sourced from the journal (honestly labelled)
    assert "Currently held" in html
    assert "No open positions" not in html
    assert "not a live venue read" in html


def test_live_shows_plain_language_description_and_keeps_raw_ticker(monkeypatch, tmp_path):
    """★ Interim item a (2026-09-01): the /live table renders a PLAIN-LANGUAGE market description (describe_market)
    for each trade + held position, keyed off the held/outcome leg -- WITHOUT dropping the raw ticker (translated
    + raw = honest, and the raw stays for precision). The default fixture ticker is Reds@Cubs, YES=Cubs; a TOTAL
    fixture exercises the over/under phrasing."""
    total_order = {"ticker": "KXMLBTOTAL-26AUG311940DETMIN-9", "outcome_leg": "yes", "condition_id": "0xtot",
                   "client_order_id": "tot-coid", "signal_id": "totsig"}
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True, stake=0.01, orders=[{}, dict(total_order)])
    html = cl.get("/live/kalshi_jack/mlb").text
    # moneyline: the YES-side team named to win, both clubs present
    assert "Chicago Cubs to win" in html and "Cincinnati Reds" in html
    # total: YES = Over, strike N-0.5, teams resolved from the stem
    assert "Over 8.5 runs" in html and "Detroit Tigers" in html
    # the RAW ticker is STILL shown (not replaced) -- translated + raw
    assert "KXMLBGAME-26AUG301920CINCHC-CHC" in html
    assert "KXMLBTOTAL-26AUG311940DETMIN-9" in html


# the Cubs settlement-close, field-for-field (id=8 on live 2026-08-31): is_exit=1 but close_source='settlement',
# NO order was placed (broker_order_id/client_order_id None, no submitted/fill price/fee/TIF), realized -0.6084 lost.
_SETTLEMENT = {
    "is_exit": 1, "close_source": "settlement", "realized_pnl": -0.6084, "won": 0, "settled_ts": 1788144281,
    "order_side": None, "submitted_count": None, "submitted_price": None, "time_in_force": None,
    "broker_order_id": None, "client_order_id": None, "condition_id": None, "outcome_index": None,
    "signal_id": None, "fill_count": 1.0, "fill_price": 0.0, "fee": 0.0, "outcome_status": "filled",
    "submitted_ts": 1788194009, "response_ts": 1788194009,
}


def test_settlement_close_renders_as_SETTLED_not_EXIT(monkeypatch, tmp_path):
    """★ THE CUBS DISPLAY BUG: a settlement-close (is_exit=1, close_source='settlement', no order placed) was
    rendering as 'EXIT' with a $0.00 fill -- conflating 'the game ended' with 'the whale got out'. It must render
    SETTLED (won/lost) with the REALIZED P&L in place of the blank submitted/fill price."""
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True, stake=0.01, orders=[{}, dict(_SETTLEMENT)])
    html = cl.get("/live/kalshi_jack/mlb").text
    assert "SETTLED" in html                                   # the settlement is labelled distinctly
    assert "lost" in html                                      # won=0 -> lost badge
    assert "-0.6084" in html and "realized" in html            # the realized P&L IN PLACE OF a blank price
    assert "ENTRY" in html                                     # the entry row still renders as ENTRY
    # the settlement did NOT invent an order: it must not show a submitted/fill price or a TIF for that close
    assert "immediate_or_cancel" in html                       # (the ENTRY row's TIF still shows -- present, not asserted-absent)


def test_real_whale_exit_still_renders_EXIT_not_settled(monkeypatch, tmp_path):
    """Guard the other side: a REAL whale-exit (is_exit=1 but close_source != 'settlement') keeps rendering EXIT,
    with its submitted/fill price -- only a settlement close is relabelled."""
    exit_row = {"is_exit": 1, "close_source": "whale_exit", "client_order_id": "exit-coid",
                "submitted_price": 0.41, "fill_price": 0.40, "fee": 0.006, "order_side": "ask"}
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True, stake=0.01, orders=[dict(exit_row)])
    html = cl.get("/live/kalshi_jack/mlb").text
    assert "EXIT" in html and "SETTLED" not in html            # a placed exit is EXIT, never SETTLED
    assert "$0.40" in html                                     # its real fill price still shows


def test_sizing_display_states_behaviour_not_misleading_cent(monkeypatch, tmp_path):
    """★ SECOND DEFECT: a $0.01 stake must NOT render as '$0.01/copy' (which reads as 'each copy costs a cent').
    It must state the behaviour -- 1 contract per copy."""
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True, stake=0.01)
    r = cl.get("/live/kalshi_jack/mlb")
    assert r.status_code == 200
    assert "$0.01/copy" not in r.text
    assert "1 contract per copy" in r.text
    assert "flat-contracts" in r.text              # the backlog stand-in is noted, not mistaken for final design


def test_live_subdivision_config_visible(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True)
    r = cl.get("/live/kalshi_jack/mlb")
    assert r.status_code == 200
    assert "Jack (KALSHI)" in r.text and "moneyline,total,spread" in r.text and "fixed" in r.text


def test_dashboard_card_enabled(monkeypatch, tmp_path):
    # M2 R1: / is now the ACCOUNTS OVERVIEW (replaces the old 2-card dashboard). seed=True -> kalshi_jack is a
    # PM-traded account (1 subdivision), so it renders a tile linking to its page, with the global arm state.
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True)
    r = cl.get("/")
    assert r.status_code == 200
    assert ">Accounts</a>" in r.text                     # the new top-of-hierarchy nav
    assert 'href="/account/kalshi_jack"' in r.text       # the seeded account links to its per-account page
    assert "GLOBAL ARM" in r.text                        # R4: global arm state visible (read-only)
    assert "coming in P3" not in r.text


def test_live_is_read_only_no_order_path_even_with_a_fill(monkeypatch, tmp_path):
    """READ-ONLY holds even with a filled order on the page: no form, no order/arm control, no POST route."""
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True, stake=0.01, orders=[{}])
    assert cl.get("/live").status_code == 200
    # no POST route exists on the live surfaces (read-only)
    assert cl.post("/live").status_code == 405
    assert cl.post("/live/kalshi_jack/mlb").status_code == 405
    for path in ("/live", "/live/kalshi_jack/mlb"):
        html = cl.get(path).text.lower()
        for token in ("<form", "place order", "/order", 'type="submit"', "hx-post", "disarm"):
            assert token not in html, (path, token)


def test_vocabulary_no_internal_name_leak(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True, stake=0.01, orders=[{}])
    for path in ("/live", "/live/kalshi_jack/mlb"):
        html = cl.get(path).text
        assert ">Accounts</a>" in html                 # M2 R1: renders under the pm_shell (Accounts nav), one shell
        for leak in ("pm_subdivision", "pm_account", "secret_ref", "owner_identity", "'pinned'", "'candidate'"):
            assert leak not in html, (path, leak)


def test_subdivision_reads_are_defensive(tmp_path):
    """subdivision.* tolerate the money-layer tables being absent -> honest-empty, never an error. This now
    includes the new order/position reads (they must not 500 when pm_subdivision_order is absent)."""
    p = str(tmp_path / "pm.db")
    import trading_corp.prediction_markets.db as _db
    # a DB with NO tables at all
    with _db.connect(p) as conn:
        assert subdivision.list_subdivisions(conn) == []
        assert subdivision.get_subdivision(conn, "kalshi_jack", "mlb") is None
        assert subdivision.live_orders(conn, "kalshi_jack", "mlb") == []
        assert subdivision.live_positions(conn, "kalshi_jack", "mlb") == []


def test_live_positions_signed_convention_and_exit_nets_flat(tmp_path):
    """The journal-derived position uses boot_reconcile's signed convention (+YES / -NO, entry + / exit -). A YES
    entry of 1 -> held 1 YES; a later YES exit of 1 -> FLAT (dropped). A NO entry -> held NO (negative net)."""
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_account (account_id, venue, label, active, created_ts) "
                     "VALUES ('kalshi_jack','kalshi','Jack',1,1)")
        conn.execute("INSERT INTO pm_subdivision (account_id, category, sizing_mode, fixed_stake_usd, active, created_ts) "
                     "VALUES ('kalshi_jack','mlb','fixed',0.01,1,1)")
        # held YES 1 @ 0.60
        _insert_order(conn)
        pos = subdivision.live_positions(conn, "kalshi_jack", "mlb")
        assert len(pos) == 1
        assert pos[0]["held_leg"] == "yes" and pos[0]["contracts"] == 1.0
        assert abs(pos[0]["cost_basis_usd"] - 0.60) < 1e-9
        assert abs(pos[0]["avg_price"] - 0.60) < 1e-9        # cost / contracts, NOT cost / fill_count (guard the divide)
        assert abs(pos[0]["fees_usd"] - 0.0084) < 1e-9
        # a YES exit of 1 on the same ticker -> net 0 -> flat -> dropped
        _insert_order(conn, is_exit=1, client_order_id="exit-1", fill_price=0.65)
        assert subdivision.live_positions(conn, "kalshi_jack", "mlb") == []
        # a NO entry on a different ticker -> held NO (negative net -> held_leg 'no')
        _insert_order(conn, client_order_id="no-1", ticker="KXMLBTOTAL-26AUG301605BALATH-11",
                      outcome_leg="no", order_side="ask", fill_price=0.40)
        pos2 = subdivision.live_positions(conn, "kalshi_jack", "mlb")
        assert len(pos2) == 1 and pos2[0]["held_leg"] == "no"
        assert pos2[0]["market_type"] == "total"


def test_live_tile_count_increments_with_two_orders(monkeypatch, tmp_path):
    """The tile count must be DB-derived, not a hardcoded '1': two real orders -> '2 live trades'."""
    cl = _client(monkeypatch, tmp_path, schema=10, seed=True,
                 orders=[{}, {"client_order_id": "order-2"}])
    r = cl.get("/live")
    assert r.status_code == 200
    assert "2 live trades" in r.text


def test_live_positions_ignores_null_ticker_rows(tmp_path):
    """A filled row with a NULL ticker must NOT become a phantom held position (mirrors boot_reconcile's
    ticker-not-null guard) -- else it buckets under '' and renders garbage."""
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_account (account_id, venue, label, active, created_ts) "
                     "VALUES ('kalshi_jack','kalshi','Jack',1,1)")
        conn.execute("INSERT INTO pm_subdivision (account_id, category, sizing_mode, fixed_stake_usd, active, created_ts) "
                     "VALUES ('kalshi_jack','mlb','fixed',0.01,1,1)")
        _insert_order(conn, ticker=None, client_order_id="null-tk")
        assert subdivision.live_positions(conn, "kalshi_jack", "mlb") == []


def test_live_order_count_and_display_limit(tmp_path):
    """live_order_count is UNCAPPED (feeds the honest 'N of M' notice); live_orders honours its LIMIT. The two
    must be able to diverge on purpose (count 2, shown 1) so the notice is truthful, never a silent cap."""
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_account (account_id, venue, label, active, created_ts) "
                     "VALUES ('kalshi_jack','kalshi','Jack',1,1)")
        conn.execute("INSERT INTO pm_subdivision (account_id, category, sizing_mode, fixed_stake_usd, active, created_ts) "
                     "VALUES ('kalshi_jack','mlb','fixed',0.01,1,1)")
        _insert_order(conn, client_order_id="o1")
        _insert_order(conn, client_order_id="o2")
        assert subdivision.live_order_count(conn, "kalshi_jack", "mlb") == 2
        assert len(subdivision.live_orders(conn, "kalshi_jack", "mlb", limit=1)) == 1


def test_sizing_summary_unit():
    """sizing_summary states BEHAVIOUR across every branch (the branches the HTML tests don't all reach)."""
    f = subdivision.sizing_summary
    cent = f({"sizing_mode": "fixed", "fixed_stake_usd": 0.01})
    assert "1 contract per copy" in cent and "$0.01/copy" not in cent and "flat-contracts" in cent
    big = f({"sizing_mode": "fixed", "fixed_stake_usd": 5.0})
    assert "floor" in big and "minimum one" in big
    assert "unset" in f({"sizing_mode": "fixed", "fixed_stake_usd": None})
    assert "kelly" in f({"sizing_mode": "kelly", "fixed_stake_usd": None})
    # a NULL sizing_mode falls back to 'fixed' (DDL default), not a crash
    assert "fixed" in f({"sizing_mode": None, "fixed_stake_usd": 0.01})


def test_dry_run_orders_excluded_from_live_trades(tmp_path):
    """R4 dry-runs (dry_run=1, logged-not-placed) are NOT live trades -- excluded from the journal read and the
    tile count, so a dry-run can never masquerade as a real fill."""
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_account (account_id, venue, label, active, created_ts) "
                     "VALUES ('kalshi_jack','kalshi','Jack',1,1)")
        conn.execute("INSERT INTO pm_subdivision (account_id, category, sizing_mode, fixed_stake_usd, active, created_ts) "
                     "VALUES ('kalshi_jack','mlb','fixed',0.01,1,1)")
        conn.execute("INSERT INTO pm_subdivision_attachment (account_id, category, wallet, active, source, added_ts) "
                     "VALUES ('kalshi_jack','mlb','0xw',1,'seed',1)")
        _insert_order(conn, dry_run=1, client_order_id="dry-1")
        assert subdivision.live_orders(conn, "kalshi_jack", "mlb") == []
        assert subdivision.live_positions(conn, "kalshi_jack", "mlb") == []   # dry-run filled != a real position
        subs = subdivision.list_subdivisions(conn)
        assert subs and subs[0]["n_live_trades"] == 0


def test_market_type_from_ticker():
    f = subdivision.market_type_from_ticker
    assert f("KXMLBGAME-26AUG301920CINCHC-CHC") == "moneyline"
    assert f("KXMLBTOTAL-26AUG301605BALATH-11") == "total"
    assert f("KXMLBSPREAD-26AUG301920CINCHC-CHC2") == "spread"
    assert f("KXNBAGAME-XYZ") == "moneyline"
    assert f("KXWEIRDSERIES-XYZ") == "kxweirdseries"   # unknown -> verbatim series, never mis-labelled
    assert f("") == "—"


def test_assert_live_ready_kalshi_fails_loud(monkeypatch):
    """Credential presence: assert_live_ready RAISES loud on a missing KALSHI key, passes when present. Tested
    WITHOUT going live (no broker call). assert_live_ready gates on ANTHROPIC_API_KEY FIRST (unconditional), so we
    set it in both cases and match the KALSHI-specific message -- proving the KALSHI branch is what gates here."""
    from trading_corp.utils import secrets as S
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")   # pass the unconditional anthropic gate
    for k in ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PEM", "KEY_VAULT_URI"):
        monkeypatch.delenv(k, raising=False)
    s_missing = S.load_secrets()
    with pytest.raises(RuntimeError, match="KALSHI"):
        S.assert_live_ready(s_missing, ("kalshi",))
    monkeypatch.setenv("KALSHI_API_KEY_ID", "id-not-a-real-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PEM", "-----BEGIN PRIVATE KEY-----\nnot-real\n-----END PRIVATE KEY-----")
    s_present = S.load_secrets()
    S.assert_live_ready(s_present, ("kalshi",))     # anthropic set + kalshi present -> must NOT raise
