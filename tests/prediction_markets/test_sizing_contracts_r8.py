"""Stage 3 R8 prep -- REAL flat-contracts sizing. sizing_mode='contracts' sizes each copy at a FLAT whole-contract
count read PER CYCLE from pm_subdivision.contracts (migration 014, DDL default 5) -- a real third mode alongside
'fixed' (flat DOLLARS, what legacy uses), NOT the fixed_stake_usd-floors-to-1 hack. Changing the count is editing a
number (no engine restart). Offline; tmp DBs; ZERO POSTs (execution holds no broker).

PROVES: contracts mode places exactly N contracts (N=5, then 10) with the body count matching; the count is read
per cycle from the row (UPDATE -> next read sizes differently, no restart); flat-DOLLARS still works unchanged; the
USD caps gate on the leg-correct 5-contract NOTIONAL (gate 2b), not on a stake; build_v2_event_order takes an
explicit count (legacy None-path derives from copy_usd, byte-identical); sub_config_from_row reads contracts
(NULL -> code default 5); migration 014 adds the column with DDL default 5; the /live sizing display states the
behaviour.
"""
import sqlite3

import pytest

from trading_corp.prediction_markets import db, execution as ex, subdivision
from trading_corp.brokers.kalshi_live import build_v2_event_order, usd_to_contracts
from trading_corp.data import mlb_poly_kalshi_match as M

ACCT, CAT = "kalshi_jack", "mlb"
NOW = 1787900000
T_TOR = "KXMLBGAME-26AUG281915SEATOR-TOR"
T_SEA = "KXMLBGAME-26AUG281915SEATOR-SEA"
GAME = [T_SEA, T_TOR]
SLUG = "mlb-sea-tor-2026-08-28"
MARKETS = {
    T_TOR: {"yes_ask_dollars": 0.55, "yes_bid_dollars": 0.53, "no_ask_dollars": 0.47, "no_bid_dollars": 0.45,
            "liquidity_dollars": 500, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00", "exchange_index": 3},
}


def _ctx():
    return ex.MarketContext(M.build_kalshi_game_index(GAME), M.build_kalshi_total_index([]),
                            M.build_kalshi_spread_index([]), frozenset({"2026-08-28"}), MARKETS)


def _sub(**over):
    base = dict(account_id=ACCT, category=CAT, market_types=("moneyline", "total", "spread"), sizing_mode="fixed",
                fixed_stake_usd=5.0, per_order_usd_cap=25.0, daily_usd_cap=50.0, max_open_usd=100.0,
                max_orders_per_day=25, max_slippage_cents=2, liquidity_ratio=0.75, contracts=5)
    base.update(over)
    return ex.SubConfig(**base)


def _sig(sid="s1", outcome="Toronto Blue Jays"):
    return ex.CopySignal(wallet="0xwhale", slug=SLUG, outcome=outcome, condition_id="0xc_" + sid,
                         outcome_index=0, signal_id=sid, is_exit=False)


def _decide(sub):
    p = ":memory:"
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    # a bare in-memory DB: no pm_subdivision_order table -> Journal seeds empty (fine for a single dry-run decision)
    j = ex.Journal(conn, [ACCT], NOW)
    return ex.evaluate(_sig(), sub, _ctx(), j, conn, NOW)


# ══ flat CONTRACTS: exactly N contracts, body matches, notional is leg-correct ══
def test_contracts_mode_places_exactly_5_contracts():
    d = _decide(_sub(sizing_mode="contracts", contracts=5))
    assert d.status == "dry_run_would_place", d.status
    assert d.count == 5 and d.body["count"] == "5"            # FLAT 5 contracts, not floor(stake/price)
    # notional = 5 x the yes-leg limit price (0.55 + 0.02 slip = 0.57)
    assert abs(d.notional_usd - 5 * 0.57) < 1e-6


def test_contracts_count_is_read_per_cycle_change_the_number():
    d5 = _decide(_sub(sizing_mode="contracts", contracts=5))
    d10 = _decide(_sub(sizing_mode="contracts", contracts=10))
    assert d5.count == 5 and d10.count == 10                  # editing the number changes the size -- no restart
    assert d10.body["count"] == "10"


def test_fixed_dollars_mode_still_sizes_by_stake():
    d = _decide(_sub(sizing_mode="fixed", fixed_stake_usd=5.0))
    assert d.status == "dry_run_would_place"
    assert d.count == usd_to_contracts(5.0, 0.55)             # floor(5/0.55)=9 -- legacy behaviour untouched
    assert d.count == 9


# ══ the caps gate on the 5-contract NOTIONAL (gate 2b), not on a stake ══
def test_contracts_notional_is_capped_by_per_order():
    # 5 contracts x 0.57 = $2.85 notional; a $2 per-order cap REJECTS at gate 2b
    d = _decide(_sub(sizing_mode="contracts", contracts=5, per_order_usd_cap=2.0))
    assert d.status == "reject:per_order_cap" and "notional_" in (d.reason or "")
    # a $25 cap passes (gate 2a is skipped in contracts mode -- fixed_stake is irrelevant)
    d2 = _decide(_sub(sizing_mode="contracts", contracts=5, per_order_usd_cap=25.0))
    assert d2.status == "dry_run_would_place"


def test_contracts_mode_ignores_a_null_fixed_stake():
    # fixed_stake_usd is IRRELEVANT in contracts mode -- a None must not crash (gate 2a is skipped)
    d = _decide(_sub(sizing_mode="contracts", contracts=3, fixed_stake_usd=None))
    assert d.status == "dry_run_would_place" and d.count == 3


def test_contracts_mode_via_production_pipeline_with_null_fixed_stake():
    # the PRODUCTION path: a DB row with NULL fixed_stake_usd + sizing_mode='contracts' -> sub_config_from_row
    # substitutes the CONFIG_DEFAULT stake (never delivers None), so evaluate cannot reach float(None). Sizes N.
    cfg = ex.sub_config_from_row({"account_id": ACCT, "category": CAT, "market_types": "moneyline,total,spread",
                                  "sizing_mode": "contracts", "fixed_stake_usd": None, "contracts": 5,
                                  "per_order_usd_cap": 25.0, "daily_usd_cap": 50.0, "max_open_usd": 100.0,
                                  "max_orders_per_day": 25, "max_slippage_cents": 2, "liquidity_ratio": 0.75})
    assert cfg.contracts == 5 and cfg.fixed_stake_usd == 5.0        # NULL stake -> code default (phantom, unused)
    assert _decide(cfg).count == 5                                  # ... and it sizes 5 contracts, no crash


def test_contracts_zero_or_negative_clamps_to_1_loudly(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        d0 = _decide(_sub(sizing_mode="contracts", contracts=0))
    assert d0.status == "dry_run_would_place" and d0.count == 1     # clamped, not a silent 1
    assert any("CLAMPED to 1" in r.getMessage() for r in caplog.records)   # ... and LOUD (the liquidity_ratio=0 lens)
    assert _decide(_sub(sizing_mode="contracts", contracts=-4)).count == 1


def test_contracts_1_boundary():
    d = _decide(_sub(sizing_mode="contracts", contracts=1))
    assert d.status == "dry_run_would_place" and d.count == 1 and d.body["count"] == "1"


def test_sub_config_from_row_tolerates_a_pre014_sqlite_row_missing_contracts():
    # a REAL sqlite3.Row from a schema-13-shaped table (NO contracts column): _row_get catches IndexError -> None
    # -> CONFIG_DEFAULTS 5. Proves the engine path is safe if execution.py ran before migration 014.
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE pm_subdivision (account_id TEXT, category TEXT, market_types TEXT, sizing_mode TEXT, "
                 "fixed_stake_usd REAL, per_order_usd_cap REAL, daily_usd_cap REAL, max_open_usd REAL, "
                 "max_orders_per_day INTEGER, max_slippage_cents INTEGER, liquidity_ratio REAL)")
    conn.execute("INSERT INTO pm_subdivision VALUES ('kalshi_jack','mlb','moneyline','fixed',5.0,25,50,100,25,2,0.75)")
    row = conn.execute("SELECT * FROM pm_subdivision").fetchone()
    assert "contracts" not in row.keys()                           # pre-014 shape
    assert ex.sub_config_from_row(row).contracts == 5              # tolerated -> code default


# ══ build_v2_event_order: explicit count vs the legacy derive-from-copy_usd path ══
def test_build_v2_explicit_count_vs_legacy_derive():
    body_c, cnt_c, _ = build_v2_event_order(ticker=T_TOR, outcome="yes", is_buy=True, base_price=0.55, copy_usd=0.0,
                                            max_slippage_cents=2, tif="immediate_or_cancel", client_order_id="x", count=7)
    assert cnt_c == 7 and body_c["count"] == "7"              # explicit count used verbatim
    body_l, cnt_l, _ = build_v2_event_order(ticker=T_TOR, outcome="yes", is_buy=True, base_price=0.55, copy_usd=5.0,
                                            max_slippage_cents=2, tif="immediate_or_cancel", client_order_id="x")
    assert cnt_l == usd_to_contracts(5.0, 0.55) == 9          # count=None -> derived from copy_usd (legacy, unchanged)


# ══ sub_config_from_row reads contracts (NULL -> code default 5) ══
def test_sub_config_from_row_reads_contracts():
    assert ex.sub_config_from_row({"account_id": ACCT, "category": CAT, "sizing_mode": "contracts",
                                   "contracts": 8}).contracts == 8
    assert ex.sub_config_from_row({"account_id": ACCT, "category": CAT, "sizing_mode": "contracts",
                                   "contracts": None}).contracts == 5      # NULL -> CONFIG_DEFAULTS
    assert ex.CONFIG_DEFAULTS["contracts"] == 5


# ══ migration 014 + the per-cycle DB read ══
def test_migration_014_adds_contracts_default_5_and_reads_per_cycle(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(pm_subdivision)").fetchall()]
        assert "contracts" in cols                            # migration 014 landed
        conn.execute("INSERT INTO pm_subdivision (account_id, category, sizing_mode, active, created_ts) "
                     "VALUES (?,?,'contracts',1,1)", (ACCT, CAT))          # no contracts given -> DDL default
        conn.commit()
        row = dict(conn.execute("SELECT * FROM pm_subdivision WHERE account_id=? AND category=?", (ACCT, CAT)).fetchone())
        assert ex.sub_config_from_row(row).contracts == 5     # DDL DEFAULT 5
        conn.execute("UPDATE pm_subdivision SET contracts=10 WHERE account_id=? AND category=?", (ACCT, CAT))
        conn.commit()
        row2 = dict(conn.execute("SELECT * FROM pm_subdivision WHERE account_id=? AND category=?", (ACCT, CAT)).fetchone())
        assert ex.sub_config_from_row(row2).contracts == 10   # a fresh read picks up the new number (no restart)


# ══ the /live sizing display states the behaviour ══
def test_sizing_summary_contracts_mode():
    s5 = subdivision.sizing_summary({"sizing_mode": "contracts", "contracts": 5})
    assert "5 contract" in s5 and "per copy" in s5 and "no restart" in s5
    s10 = subdivision.sizing_summary({"sizing_mode": "contracts", "contracts": 10})
    assert "10 contracts per copy" in s10
    # absent contracts key -> defaults to 5 (deploy-order tolerant: matches the DDL default)
    assert "5 contract" in subdivision.sizing_summary({"sizing_mode": "contracts"})
    # fixed dollars unchanged
    assert "fixed" in subdivision.sizing_summary({"sizing_mode": "fixed", "fixed_stake_usd": 5.0})
