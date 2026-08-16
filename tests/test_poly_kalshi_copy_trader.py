"""CP4 tests for the Poly->Kalshi detection loop (no network; fake client/rows).
Covers incremental detection (cold-start seed + new-only + offset=0 only),
day-rollover, 429 backoff trigger+recover, [G-slip] fail-closed (live), and
[G-halt] daily-loss auto-detection -> persist_halt -> subsequent block."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from trading_corp.data.polymarket_data_api_client import PolymarketRateLimitError
from trading_corp.data.mlb_poly_kalshi_match import build_kalshi_game_index
from trading_corp.persistence import db as _db
from trading_corp.persistence.models import StrategyState
from trading_corp.agents.strategies.poly_kalshi_executor import PolyKalshiExecutor, translate_whale_action
from trading_corp.agents.strategies.poly_kalshi_copy_trader import PolyKalshiCopyTrader, _utc_day

NYYTOR = ["KXMLBGAME-26AUG161337NYYTOR-NYY", "KXMLBGAME-26AUG161337NYYTOR-TOR"]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def hdb(tmp_path):
    p = tmp_path / "loop.db"
    _db.init_db(f"sqlite:///{p}")
    return f"sqlite:///{p}"


def _row(ts, *, side="BUY", slug="mlb-nyy-tor-2026-08-16", outcome="New York Yankees",
         price=0.55, typ="TRADE", tx="0xabc"):
    return SimpleNamespace(timestamp=ts, type=typ, side=side, slug=slug, outcome=outcome,
                           title="New York Yankees vs. Toronto Blue Jays", event_slug=slug,
                           transaction_hash=tx, price=price, condition_id="0xcid")


class FakeClient:
    """Scripted fetch_activity: yields pages[call] (or raises if it's an Exception)."""
    def __init__(self, pages):
        self.pages = pages
        self.offsets = []
        self.calls = 0

    async def fetch_activity(self, wallet, limit, offset):
        self.offsets.append(offset)
        page = self.pages[min(self.calls, len(self.pages) - 1)]
        self.calls += 1
        if isinstance(page, Exception):
            raise page
        return page


def _loop(hdb, *, quote_fn=None, daily_cap=None, day_key_fn=None, now=1000.0, roster=None):
    ex = PolyKalshiExecutor(dry_run=True, db_url=hdb, strategy="poly_kalshi_mlb_looptest")
    # seed the trigger roster (selected_whales) — the loop reads THIS, no hardcoded dict
    if roster is None:
        roster = [{"wallet": "0xwallet", "user_name": "SDTrading", "category": "mlb"}]
    _db.set_agent_state("polymarket_copy_trader", "selected_whales", roster, db_url=hdb)
    kw = {}
    if day_key_fn is not None:
        kw["day_key_fn"] = day_key_fn
    lp = PolyKalshiCopyTrader(executor=ex, poll_interval_sec=5.0, db_url=hdb,
                              stake_usd=2.0, quote_fn=quote_fn, daily_loss_cap_usd=daily_cap,
                              now_fn=lambda: now, **kw)
    lp.set_kalshi_index(*_index())
    return lp, ex


def test_loop_reads_roster_from_selected_whales_and_reloads(hdb):
    lp, ex = _loop(hdb, roster=[{"wallet": "0xA", "user_name": "SDTrading", "category": "mlb"},
                                {"wallet": "0xB", "user_name": "xifutloong3", "category": "mlb"}])
    assert lp._load_roster() == [("SDTrading", "0xA"), ("xifutloong3", "0xB")]
    # per-cycle reload: mutate selected_whales, loop sees it on the next read
    _db.set_agent_state("polymarket_copy_trader", "selected_whales",
                        [{"wallet": "0xC", "user_name": "monkeymashingkeyboard", "category": "mlb"}],
                        db_url=hdb)
    assert lp._load_roster() == [("monkeymashingkeyboard", "0xC")]


def test_poll_iterates_roster_wallet_not_hardcoded(hdb):
    lp, ex = _loop(hdb, roster=[{"wallet": "0xZ", "user_name": "SDTrading", "category": "mlb"}])
    _run(lp.poll_cycle(FakeClient([[_row(100)]])))
    assert lp._last_seen_ts.get("0xZ") == 100      # polled the roster's wallet
    assert "0xwallet" not in lp._last_seen_ts       # no hardcoded default


def _index():
    idx = build_kalshi_game_index(NYYTOR)
    return idx, frozenset(k[0] for k in idx)


# ── incremental detection ────────────────────────────────────────────────
def test_cold_start_seeds_without_emitting(hdb):
    lp, ex = _loop(hdb)
    client = FakeClient([[_row(100), _row(90)]])
    out = _run(lp.poll_cycle(client))
    assert out == [] and lp.shadow_log == []          # boot: no history copied
    assert lp._last_seen_ts["0xwallet"] == 100        # high-water seeded


def test_only_new_actions_emit_and_offset_is_zero(hdb):
    lp, ex = _loop(hdb)
    client = FakeClient([[_row(100)],                 # cold-start seed at 100
                         [_row(150), _row(100)]])      # 150 is new, 100 already seen
    _run(lp.poll_cycle(client))
    out = _run(lp.poll_cycle(client))
    assert len(out) == 1 and out[0]["action_ts"] == 150
    assert out[0]["decision"] == "DRY_RUN_would_place"
    assert set(client.offsets) == {0}                 # never deep-paged toward offset 5000


def test_redeem_rows_not_emitted(hdb):
    lp, ex = _loop(hdb)
    client = FakeClient([[_row(100)],
                         [_row(150, typ="REDEEM", side=""), _row(100)]])
    _run(lp.poll_cycle(client))
    out = _run(lp.poll_cycle(client))
    assert out == []                                  # REDEEM is not a copy signal


# ── day-rollover ─────────────────────────────────────────────────────────
def test_day_rollover_resets_daily_counter(hdb):
    day = {"v": "2026-08-16"}
    lp, ex = _loop(hdb, day_key_fn=lambda: day["v"])
    ex._deployed_usd = 12.0                            # pretend we deployed today
    _run(lp.poll_cycle(FakeClient([[_row(100)]])))     # boot on 08-16, no reset
    assert ex._deployed_usd == 12.0
    day["v"] = "2026-08-17"                            # the clock crosses midnight UTC
    _run(lp.poll_cycle(FakeClient([[_row(100)]])))     # day changed -> reset
    assert ex._deployed_usd == 0.0


# ── 429 backoff ──────────────────────────────────────────────────────────
def test_backoff_triggers_then_recovers(hdb, monkeypatch):
    async def _nosleep(*_a, **_k):
        return None
    monkeypatch.setattr(asyncio, "sleep", _nosleep)  # no real waits
    lp, ex = _loop(hdb)
    rl = PolymarketRateLimitError("429")
    # cold-start poll: two 429s then a page -> backoff twice, then recover
    client = FakeClient([rl, rl, [_row(100)]])
    _run(lp.poll_cycle(client))
    assert len([b for b in lp.backoff_events if "attempt" in b]) == 2   # backed off twice
    assert lp._last_seen_ts["0xwallet"] == 100                          # recovered, seeded


def test_fetch_giveup_returns_empty_no_crash(hdb, monkeypatch):
    async def _nosleep(*_a, **_k):
        return None
    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    lp, ex = _loop(hdb)
    rl = PolymarketRateLimitError("429")
    client = FakeClient([rl, rl, rl, rl, rl])          # exceeds the schedule -> give up
    out = _run(lp.poll_cycle(client))
    assert out == [] and any(b.get("gave_up") for b in lp.backoff_events)


# ── FLAG 2 (Phase 2b CP1): the loop journals the triggering Poly bet ─────────
def test_flag2_pipeline_journals_the_poly_trigger(hdb):
    """The loop wires the triggering Poly bet (slug/outcome/side/market_type) into the
    executor -> the journaled poly_kalshi_order row carries the 'why' persistently,
    not just the in-memory shadow_log."""
    lp, ex = _loop(hdb)
    client = FakeClient([[_row(100)],          # cold-start seed
                         [_row(150)]])          # new TRADE BUY on the NYYTOR game -> placed
    _run(lp.poll_cycle(client))
    out = _run(lp.poll_cycle(client))
    assert out[0]["decision"] == "DRY_RUN_would_place"
    rec = ex.log[-1]                            # the journaled poly_kalshi_order row
    assert rec["status"] == "DRY_RUN_would_place"
    assert rec["poly_slug"] == "mlb-nyy-tor-2026-08-16"
    assert rec["poly_outcome"] == "New York Yankees"
    assert rec["poly_side"] == "BUY"
    assert rec["poly_market_type"] == "moneyline"
    assert lp.shadow_log[-1]["slug"] == "mlb-nyy-tor-2026-08-16"   # shadow_log still populated too


# ── [G-slip] LIVE fail-closed (fetch fails -> reject, POST not reached) ─────
def test_gslip_fail_closed_live_no_quote(hdb):
    posted = []

    class FakeClientPost:
        async def post(self, path, body):
            posted.append(body)
            return {"fill_count": "1"}

    class FakeBroker:
        def _client(self):
            return FakeClientPost()

    ex = PolyKalshiExecutor(dry_run=False, broker=FakeBroker(), db_url=hdb,
                            strategy="poly_kalshi_mlb_slip")
    from trading_corp.agents.strategies.poly_kalshi_executor import translate_whale_action
    o = translate_whale_action(whale="w", whale_wallet="0xW", kalshi_ticker=NYYTOR[0], confidence=1.0,
                               whale_side="BUY", base_price=0.55, stake_usd=2.0)
    r = _run(ex.submit(o, market_quote=None))          # live + no quote
    assert r["status"] == "blocked_slippage_no_quote"
    assert posted == []                                # the V2 POST was never reached


def test_loop_quote_fetch_exception_is_caught(hdb):
    async def boom(_ticker):
        raise RuntimeError("book fetch timeout")
    lp, ex = _loop(hdb, quote_fn=boom)
    client = FakeClient([[_row(100)], [_row(150), _row(100)]])
    _run(lp.poll_cycle(client))
    out = _run(lp.poll_cycle(client))                  # dry-run: None quote -> proceeds, no crash
    assert out[0]["quote"] is None and out[0]["decision"] == "DRY_RUN_would_place"


# ── [G-halt] daily-loss auto-detection -> persist_halt -> block ────────────
# ── fix (a): settlement-sweep -> $100 auto-halt (the SWEEP calls record_realized) ──
def test_settlement_sweep_trips_100_halt_and_blocks(hdb):
    lp, ex = _loop(hdb, daily_cap=100.0)
    strat = ex._strategy
    today = _utc_day()

    async def get_settled():
        return [("KXMLBGAME-A", today + "T20:00:00Z", -60.0),
                ("KXMLBGAME-B", today + "T21:30:00Z", -45.0),      # today sum = -105
                ("KXMLBGAME-OLD", "2026-01-01T00:00:00Z", -999.0)]  # not today -> ignored

    assert _run(lp.run_settlement_sweep(get_settled)) is True       # sweep tripped the halt
    assert StrategyState.from_persistence(strat, db_url=hdb).halted is True
    assert lp._realized_pnl_day == -105.0                            # only today counted
    o = translate_whale_action(whale="w", whale_wallet="0xW", kalshi_ticker=NYYTOR[0],
                               confidence=1.0, whale_side="BUY", base_price=0.55, stake_usd=5.0)
    assert _run(ex.submit(o))["status"] == "blocked_halt"           # enforced downstream


def test_settlement_sweep_idempotent_no_double_count(hdb):
    lp, ex = _loop(hdb, daily_cap=100.0)
    today = _utc_day()

    async def get_settled():
        return [("KXMLBGAME-A", today + "T20:00:00Z", -50.0)]       # -50, above -100

    assert _run(lp.run_settlement_sweep(get_settled)) is False and lp._realized_pnl_day == -50.0
    assert _run(lp.run_settlement_sweep(get_settled)) is False      # re-sweep same settlements
    assert lp._realized_pnl_day == -50.0                            # NOT -100 (delta 0, no double-count)


def test_rollover_resets_trade_count(hdb):
    day = {"v": "2026-08-16"}
    lp, ex = _loop(hdb, day_key_fn=lambda: day["v"])
    ex._orders_today = 10
    _run(lp.poll_cycle(FakeClient([[_row(100)]])))     # boot day, no reset
    assert ex._orders_today == 10
    day["v"] = "2026-08-17"
    _run(lp.poll_cycle(FakeClient([[_row(100)]])))     # rollover -> reset
    assert ex._orders_today == 0


def test_ghalt_autodetect_fires_persist_halt_and_blocks(hdb):
    lp, ex = _loop(hdb, daily_cap=10.0)
    strat = ex._strategy
    assert lp.record_realized(-6.0) is False           # -6 within cap
    assert StrategyState.from_persistence(strat, db_url=hdb).halted is False
    assert lp.record_realized(-5.0) is True            # -11 <= -10 -> auto-halt
    assert StrategyState.from_persistence(strat, db_url=hdb).halted is True
    # enforcement: subsequent submit is blocked by the same halt row
    from trading_corp.agents.strategies.poly_kalshi_executor import translate_whale_action
    o = translate_whale_action(whale="w", whale_wallet="0xW", kalshi_ticker=NYYTOR[0], confidence=1.0,
                               whale_side="BUY", base_price=0.55, stake_usd=2.0)
    assert _run(ex.submit(o))["status"] == "blocked_halt"
