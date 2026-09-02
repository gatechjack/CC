"""R7 EXPOSURE-CAP VENUE REBASE (RULING 5, 2026-09-02). Gate 6's open-exposure base is the ACCOUNT'S TRUE open
exposure read from the venue (co-tenant + manual + PM), not PM's journal sum -- correct regardless of PM-exclusivity.
Proven offline (no pykalshi, no network): the venue_exposure parser/pager + gate 6 through the real `evaluate`.

The load-bearing proof is `test_gate6_cotenant_venue_exposure_blocks_pm_with_empty_journal`: with PM's journal EMPTY
(open_usd 0), a co-tenant's venue exposure over the cap now BLOCKS PM -- the exact over-commit the old journal-only
cap could not see."""
import sqlite3

import pytest

from trading_corp.prediction_markets import db, execution as ex, venue_exposure as V
from trading_corp.data import mlb_poly_kalshi_match as M

ACCT, CAT = "kalshi_jack", "mlb"
NOW = 1787900000
GAME = ["KXMLBGAME-26AUG281915SEATOR-SEA", "KXMLBGAME-26AUG281915SEATOR-TOR"]
TOTAL = ["KXMLBTOTAL-26AUG281915SEATOR-9"]
SPREAD = ["KXMLBSPREAD-26AUG281915SEATOR-TOR2"]
T_TOR = "KXMLBGAME-26AUG281915SEATOR-TOR"
T_SEA = "KXMLBGAME-26AUG281915SEATOR-SEA"
SLUG = "mlb-sea-tor-2026-08-28"


# ---- venue_exposure module (pure) ------------------------------------------------------------------------------

def test_parse_sums_market_exposure_cents_to_dollars():
    r = V.parse_open_exposure([{"market_exposure": 1340}, {"market_exposure": 500}, {"market_exposure": 0}])
    assert r.has_data and abs(r.total_dollars - 18.40) < 1e-9 and r.n_positions == 3


def test_parse_empty_list_is_known_flat_zero():
    r = V.parse_open_exposure([])
    assert r.has_data is True and r.total_dollars == 0.0 and r.open_dollars() == 0.0


def test_parse_none_is_unknown_fail_closed_signal():
    r = V.parse_open_exposure(None)
    assert r.has_data is False and r.open_dollars() is None


def test_parse_raises_on_corruption():
    for bad in ("notalist", [123], [{"market_exposure": None}], [{"nope": 1}], [{"market_exposure": float("inf")}]):
        with pytest.raises((ValueError, TypeError)):
            V.parse_open_exposure(bad)


class _FakeCli:
    """Duck-typed venue client returning canned /portfolio/positions pages (sync .get)."""
    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = 0

    def get(self, path):
        self.calls += 1
        return self._pages.pop(0)


def test_fetch_single_page():
    import asyncio
    r = asyncio.run(V.fetch_open_exposure(_FakeCli([{"market_positions": [{"market_exposure": 2500}]}])))
    assert r.has_data and abs(r.total_dollars - 25.0) < 1e-9


def test_fetch_pages_via_cursor():
    import asyncio
    pages = [{"market_positions": [{"market_exposure": 1000}], "cursor": "c1"},
             {"market_positions": [{"market_exposure": 2000}]}]
    cli = _FakeCli(pages)
    r = asyncio.run(V.fetch_open_exposure(cli))
    assert cli.calls == 2 and abs(r.total_dollars - 30.0) < 1e-9 and r.n_positions == 2


def test_fetch_missing_market_positions_key_is_unknown():
    import asyncio
    r = asyncio.run(V.fetch_open_exposure(_FakeCli([{"cursor": None}])))    # no market_positions key at all
    assert r.has_data is False


# ---- gate 6 through the real evaluate ---------------------------------------------------------------------------

def _markets(shard=3, size="500.00"):
    return {T_TOR: {"yes_ask_dollars": 0.55, "yes_bid_dollars": 0.53, "no_ask_dollars": 0.47,
                    "yes_ask_size_fp": size, "yes_bid_size_fp": size, "exchange_index": shard},
            T_SEA: {"yes_ask_dollars": 0.47, "yes_bid_dollars": 0.45, "no_ask_dollars": 0.55,
                    "yes_ask_size_fp": size, "yes_bid_size_fp": size, "exchange_index": shard}}


def _ctx():
    return ex.MarketContext(M.build_kalshi_game_index(GAME), M.build_kalshi_total_index(TOTAL),
                            M.build_kalshi_spread_index(SPREAD), frozenset({"2026-08-28"}), _markets())


def _sub(**over):
    base = dict(account_id=ACCT, category=CAT, market_types=("moneyline", "total", "spread"), sizing_mode="fixed",
                fixed_stake_usd=5.0, per_order_usd_cap=25.0, daily_usd_cap=50.0, max_open_usd=100.0,
                max_orders_per_day=25, max_slippage_cents=2)
    base.update(over)
    return ex.SubConfig(**base)


def _sig(sid="s1"):
    return ex.CopySignal(wallet="0xWHALE", slug=SLUG, outcome="Toronto Blue Jays", condition_id="0xc_" + sid,
                         outcome_index=0, signal_id=sid, is_exit=False)


def _funded_shards():
    from trading_corp.prediction_markets import shard_balance as sb
    return sb.parse_balance({"balance_dollars": "500.0",
                             "balance_breakdown": [{"exchange_index": 3, "balance": "500.0"},
                                                   {"exchange_index": 0, "balance": "0.01"}]})


def _eval(tmp_path, venue_exp, sub=None, sig=None):
    """Evaluate ONE signal past gate 6b (funded shard 3) so gate 6 (venue exposure) decides. Fresh DB per call;
    evaluate performs NO DB writes so a reused temp file stays empty (each call re-seeds an empty Journal)."""
    sub = sub or _sub()
    sig = sig or _sig()
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        return ex.evaluate(sig, sub, _ctx(), ex.Journal(conn, [ACCT], NOW), conn, NOW,
                           shard_balances=_funded_shards(), venue_exposure=venue_exp,
                           legacy_db_path=str(tmp_path / "noleg.db"))


def _notional(tmp_path):
    d = _eval(tmp_path, V.VenueExposure(0.0, True))
    assert d.status == "dry_run_would_place"
    return d.notional_usd


def test_gate6_low_venue_exposure_would_place(tmp_path):
    assert _eval(tmp_path, V.VenueExposure(total_dollars=1.0, has_data=True)).status == "dry_run_would_place"


def test_gate6_cotenant_venue_exposure_blocks_pm_with_empty_journal(tmp_path):
    # ★ THE R7 PROOF: PM's journal is EMPTY (fresh DB -> open_usd 0), but the VENUE shows exposure OVER the cap
    # (a co-tenant / manual position the journal cannot see). Old journal-only gate 6 would have WOULD-PLACED
    # (over-committing the account); the venue rebase REJECTS.
    d = _eval(tmp_path, V.VenueExposure(total_dollars=150.0, has_data=True), sub=_sub(max_open_usd=100.0))
    assert d.status == "reject:exposure_cap"


def test_gate6_venue_unknown_fails_closed(tmp_path):
    assert _eval(tmp_path, V.VenueExposure(total_dollars=0.0, has_data=False)).status == "skip:exposure_unknown"


def test_gate6_none_venue_is_paper_optout_uses_journal_base(tmp_path):
    # venue_exposure=None DISABLES the rebase (paper/dry-run/test opt-out, mirroring shard_balances=None). Empty
    # journal -> would_place. This is the ONLY value that bypasses the venue base; the live driver never passes it.
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        d = ex.evaluate(_sig(), _sub(), _ctx(), ex.Journal(conn, [ACCT], NOW), conn, NOW,
                        shard_balances=_funded_shards(), venue_exposure=None,
                        legacy_db_path=str(tmp_path / "noleg.db"))
    assert d.status == "dry_run_would_place"


def test_gate6_boundary(tmp_path):
    n = _notional(tmp_path)
    cap = 100.0
    # venue base leaves EXACTLY room for one notional -> place; a couple cents more -> reject.
    assert _eval(tmp_path, V.VenueExposure(cap - n, True), sub=_sub(max_open_usd=cap)).status == "dry_run_would_place"
    assert _eval(tmp_path, V.VenueExposure(cap - n + 0.02, True),
                 sub=_sub(max_open_usd=cap)).status == "reject:exposure_cap"


def test_gate6_in_cycle_accumulates_on_top_of_venue_base(tmp_path):
    # Two entries in ONE cycle (shared Journal + conn): the venue base is fixed; the 2nd order must see the 1st
    # order's in-flight notional added (commit_would_place -> in_cycle_open_usd), so it trips the cap.
    n = _notional(tmp_path)
    base = 90.0
    cap = base + n + (n / 2.0)   # room for exactly ONE order over the base: base + n <= cap < base + 2n
    sub = _sub(max_open_usd=cap)
    ve = V.VenueExposure(base, True)
    p = str(tmp_path / "cyc.db"); db.init_db(p)
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        d1 = ex.evaluate(_sig("a"), sub, _ctx(), j, conn, NOW, shard_balances=_funded_shards(),
                         venue_exposure=ve, legacy_db_path=str(tmp_path / "noleg.db"))
        d2 = ex.evaluate(_sig("b"), sub, _ctx(), j, conn, NOW, shard_balances=_funded_shards(),
                         venue_exposure=ve, legacy_db_path=str(tmp_path / "noleg.db"))
    assert d1.status == "dry_run_would_place"
    assert d2.status == "reject:exposure_cap"       # base + in_cycle(n) + n > cap


def test_journal_in_cycle_open_usd_isolates_increments():
    conn = sqlite3.connect(":memory:")
    j = ex.Journal(conn, [ACCT], NOW)               # no pm_subdivision_order table -> empty seed
    assert j.in_cycle_open_usd(ACCT) == 0.0
    j.commit_would_place(ACCT, CAT, 3.0)
    j.commit_would_place(ACCT, CAT, 2.5)
    assert abs(j.in_cycle_open_usd(ACCT) - 5.5) < 1e-9
    assert abs(j.open_usd(ACCT) - 5.5) < 1e-9        # seed was 0 so open == in_cycle here
