"""Shard money-mgmt RUNG 2 -- gate 6b, the PRE-FLIGHT PER-MARKET shard-funding SKIP (Jack RULED shape (i),
2026-08-30). Before placing, `shard_balances.can_fund(order_shard, notional)`; not fundable -> skip:shard_underfunded
(a LABELLED SKIP, fundable-later, NOT a fault -> must not feed the error-latch). It FAILS CLOSED (unknown market
shard / unknown split / thin shard all skip) and resolves the shard PER MARKET (correction: live markets never
migrate, so an MLB market's shard is a fact of THAT market, not its category). Plus the driver's sustained-
underfunding alarm (SURFACED, not latched) and the review-lens guard: the value that makes the gate pass everything
is shard_balances=None, and the LIVE driver NEVER passes None (a fetch failure -> UNKNOWN, not None). Offline; fakes."""
import logging
import sqlite3

import pytest

from trading_corp.prediction_markets import arm, db, execution as ex, live_driver as L, shard_balance as sb
from trading_corp.data import mlb_poly_kalshi_match as M

ACCT, CAT = "kalshi_jack", "mlb"
NOW = 1787900000
GAME = ["KXMLBGAME-26AUG281915SEATOR-SEA", "KXMLBGAME-26AUG281915SEATOR-TOR"]
TOTAL = ["KXMLBTOTAL-26AUG281915SEATOR-9"]
SPREAD = ["KXMLBSPREAD-26AUG281915SEATOR-TOR2"]
T_TOR = "KXMLBGAME-26AUG281915SEATOR-TOR"
T_SEA = "KXMLBGAME-26AUG281915SEATOR-SEA"
SLUG = "mlb-sea-tor-2026-08-28"


def _markets(tor_shard=3, sea_shard=3, liq=500.0):
    return {T_TOR: {"yes_ask_dollars": 0.55, "yes_bid_dollars": 0.53, "no_ask_dollars": 0.47,
                    "liquidity_dollars": liq, "exchange_index": tor_shard},
            T_SEA: {"yes_ask_dollars": 0.47, "yes_bid_dollars": 0.45, "no_ask_dollars": 0.55,
                    "liquidity_dollars": liq, "exchange_index": sea_shard}}


def _ctx(markets=None):
    return ex.MarketContext(M.build_kalshi_game_index(GAME), M.build_kalshi_total_index(TOTAL),
                            M.build_kalshi_spread_index(SPREAD), frozenset({"2026-08-28"}),
                            _markets() if markets is None else markets)


def _sub(**over):
    base = dict(account_id=ACCT, category=CAT, market_types=("moneyline", "total", "spread"), sizing_mode="fixed",
                fixed_stake_usd=5.0, per_order_usd_cap=25.0, daily_usd_cap=50.0, max_open_usd=100.0,
                max_orders_per_day=25, max_slippage_cents=2)
    base.update(over)
    return ex.SubConfig(**base)


def _sig(outcome="Toronto Blue Jays", sid="s1", is_exit=False, wallet="0xWHALE"):
    return ex.CopySignal(wallet=wallet, slug=SLUG, outcome=outcome, condition_id="0xc_" + sid, outcome_index=0,
                         signal_id=sid, is_exit=is_exit)


def _bal(shards):
    """shards: {exchange_index -> dollars} -> a ShardBalances (via the real parser)."""
    bd = [{"exchange_index": int(k), "balance": "%.4f" % v} for k, v in shards.items()]
    return sb.parse_balance({"balance_dollars": "%.4f" % sum(shards.values()), "balance_breakdown": bd})


def _eval(tmp_path, sub, sig, ctx, shard_balances):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        return ex.evaluate(sig, sub, ctx, ex.Journal(conn, [ACCT], NOW), conn, NOW,
                           shard_balances=shard_balances, legacy_db_path=str(tmp_path / "noleg.db"))


# ── gate 6b at the chokepoint ─────────────────────────────────────────────────
def test_gate6b_funded_would_place(tmp_path):
    d = _eval(tmp_path, _sub(), _sig(), _ctx(_markets(tor_shard=3)), _bal({0: 0.01, 3: 500.0}))
    assert d.status == "dry_run_would_place"


def test_gate6b_underfunded_skips(tmp_path):
    d = _eval(tmp_path, _sub(), _sig(), _ctx(_markets(tor_shard=3)), _bal({0: 500.0, 3: 0.10}))
    assert d.status == "skip:shard_underfunded" and "shard_3" in d.reason


def test_gate6b_unknown_split_skips(tmp_path):
    unknown = sb.parse_balance({"balance_dollars": "500.0"})            # no balance_breakdown -> has_breakdown False
    d = _eval(tmp_path, _sub(), _sig(), _ctx(_markets(tor_shard=3)), unknown)
    assert d.status == "skip:shard_underfunded"                          # can_fund None -> fail-closed


def test_gate6b_market_missing_exchange_index_skips(tmp_path):
    d = _eval(tmp_path, _sub(), _sig(), _ctx(_markets(tor_shard=None)), _bal({3: 500.0}))
    assert d.status == "skip:shard_underfunded"                          # market shard None -> fail-closed


def test_gate6b_none_balances_is_opt_out_would_place(tmp_path):
    # ★ the review-lens value: shard_balances=None DISABLES gate 6b (test/paper opt-out). This is the ONLY value that
    # makes the gate pass everything; the driver never passes it (proven in test_driver_fails_closed_...).
    d = _eval(tmp_path, _sub(), _sig(), _ctx(_markets(tor_shard=3)), None)
    assert d.status == "dry_run_would_place"


def test_gate6b_per_market_not_per_category(tmp_path):
    # ★★ CORRECTION 1: SAME category (mlb), SAME account, SAME balances (funds only on shard 0) -- the ONLY change is
    # the MARKET's exchange_index. A shard-0 market funds+places; a shard-3 market skips. Proves per-MARKET resolution.
    bal = _bal({0: 500.0, 3: 0.0})
    d0 = _eval(tmp_path, _sub(), _sig(sid="t0"), _ctx(_markets(tor_shard=0)), bal)
    d3 = _eval(tmp_path, _sub(), _sig(sid="t3"), _ctx(_markets(tor_shard=3)), bal)
    assert d0.status == "dry_run_would_place"                            # shard 0 funded
    assert d3.status == "skip:shard_underfunded"                         # shard 3 empty -- same everything else


def test_gate6b_exit_is_not_shard_gated(tmp_path):
    # a reduce_only EXIT reduces risk -> gate 6b (entry-only) must NEVER skip it, even on an empty shard
    d = _eval(tmp_path, _sub(), _sig(is_exit=True, sid="ex"), _ctx(_markets(tor_shard=3)), _bal({3: 0.0}))
    assert d.status == "dry_run_would_place" and d.is_exit is True


# ── the market dict now carries exchange_index (per-market shard input) ──
def test_market_quote_dict_carries_exchange_index():
    class _M:
        ticker = T_TOR; yes_ask_dollars = 0.55; no_ask_dollars = 0.47; liquidity_dollars = 500; exchange_index = 3
    assert L._market_quote_dict(_M())["exchange_index"] == 3

    class _Bare:
        ticker = T_SEA                                                   # no exchange_index attr -> None -> fail-closed
    assert L._market_quote_dict(_Bare())["exchange_index"] is None


# ── driver fakes (record posts; never a real network call) ─────────────────────
_FILL = {"order_id": "OID1", "fill_count": "1", "average_fill_price": "0.55", "average_fee_paid": "0.01",
         "remaining_count": "0"}


class _FakePortfolio:
    def __init__(self, positions=None): self._positions = positions or []
    async def get_positions(self, fetch_all=False): return list(self._positions)


class FakeClient:
    def __init__(self, *, balance_resp=None, balance_raise=False, game_markets=None, positions=None):
        self.posts = []; self._balance = balance_resp; self._balance_raise = balance_raise
        self._game = game_markets or []; self.portfolio = _FakePortfolio(positions)
    async def post(self, path, body):
        self.posts.append((path, body)); return dict(_FILL)
    async def get_markets(self, series_ticker=None, status=None, limit=None, fetch_all=False, **kw):
        # OPEN (fetch_all=False) returns the game markets; SETTLED (fetch_all=True) returns [] -- production returns
        # DISJOINT open/settled sets, so the game tickers are NOT duplicated (a dup would look like a doubleheader).
        return self._game if (series_ticker == "KXMLBGAME" and not fetch_all) else []
    async def get(self, path):
        assert path == "/portfolio/balance"
        if self._balance_raise:
            raise RuntimeError("balance read 503")
        return self._balance


class FakeBroker:
    def __init__(self, client):
        class _R: pass
        self._read = _R(); self._read._client = client


class FakeMarket:
    def __init__(self, ticker, exchange_index=3, yes_ask=0.55, no_ask=0.47, liq=500):
        self.ticker = ticker; self.exchange_index = exchange_index
        self.yes_ask_dollars = yes_ask; self.no_ask_dollars = no_ask; self.liquidity_dollars = liq


class FakePos:
    def __init__(self, cid, oidx, outcome, cur=0.5, redeemable=False):
        self.condition_id = cid; self.slug = SLUG; self.outcome = outcome
        self.extra = {"outcomeIndex": oidx, "curPrice": cur, "redeemable": redeemable}


class FakeBook:
    def __init__(self, rows, complete=True):
        self.rows = rows; self.complete = complete; self.n = len(rows); self.pages = 1


class FakePositionsClient:
    def __init__(self, book): self._book = book
    async def fetch_positions_book(self, wallet): return self._book


def _legacy(tmp_path):
    p = str(tmp_path / "trading_corp.db"); c = sqlite3.connect(p)
    c.execute("CREATE TABLE agent_state (agent TEXT NOT NULL, key TEXT NOT NULL, value_json TEXT NOT NULL, "
              "updated_ts TEXT NOT NULL, PRIMARY KEY (agent, key))")
    c.commit(); c.close(); return p


def _arm_both(leg):
    arm.arm(global_=True, require_latch_clear=True, legacy_db_path=leg)
    arm.arm(ACCT, CAT, require_latch_clear=True, legacy_db_path=leg)


def _seed(p):
    import time
    with db.connect(p) as conn:
        conn.execute("INSERT OR IGNORE INTO pm_account(account_id,venue,secret_ref,label,active,created_ts) "
                     "VALUES(?,'kalshi','KALSHI','Jack',1,?)", (ACCT, int(time.time())))
        conn.execute("INSERT OR IGNORE INTO pm_subdivision(account_id,category,market_types,sizing_mode,"
                     "fixed_stake_usd,active,created_ts) VALUES(?,?,'moneyline,total,spread','fixed',5.0,1,?)",
                     (ACCT, CAT, int(time.time())))
        conn.execute("INSERT OR IGNORE INTO pm_subdivision_attachment(account_id,category,wallet,active,source,added_ts) "
                     "VALUES(?,?,?,1,'promote_to_live',?)", (ACCT, CAT, "0xWHALE", int(time.time())))
        conn.commit()


_FUNDED = {"balance_dollars": "500.01",
           "balance_breakdown": [{"exchange_index": 0, "balance": "0.0100"}, {"exchange_index": 3, "balance": "500.0000"}]}
_EMPTY3 = {"balance_dollars": "500.00",
           "balance_breakdown": [{"exchange_index": 0, "balance": "500.0000"}, {"exchange_index": 3, "balance": "0.0000"}]}
_GAMES = None  # built per test with the right shard


# ★★ THE REVIEW-LENS GUARD: ARMED + a would-be-fundable signal, but the balance read FAILS -> the driver must NOT
# place (it fails CLOSED to an UNKNOWN ShardBalances, never None, never blind).
@pytest.mark.asyncio
async def test_driver_fails_closed_when_balance_read_fails(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p); _seed(p)
    _arm_both(leg)
    client = FakeClient(balance_raise=True, positions=[],
                        game_markets=[FakeMarket(T_TOR, exchange_index=3), FakeMarket(T_SEA, exchange_index=3)])
    pos = FakePositionsClient(FakeBook([FakePos("0xopen", 0, "Toronto Blue Jays", cur=0.5)]))
    await L.scheduled_pm_live_loop(p, FakeBroker(client), pos, account_id=ACCT, category=CAT, poll_sec=0,
                                   legacy_db_path=leg, _max_cycles=1)
    assert client.posts == []                                           # UNKNOWN split -> fail-closed -> ZERO posts
    with db.connect(p) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_order").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_driver_places_when_market_shard_funded(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p); _seed(p)
    _arm_both(leg)
    client = FakeClient(balance_resp=_FUNDED, positions=[],
                        game_markets=[FakeMarket(T_TOR, exchange_index=3), FakeMarket(T_SEA, exchange_index=3)])
    pos = FakePositionsClient(FakeBook([FakePos("0xopen", 0, "Toronto Blue Jays", cur=0.5)]))
    await L.scheduled_pm_live_loop(p, FakeBroker(client), pos, account_id=ACCT, category=CAT, poll_sec=0,
                                   legacy_db_path=leg, _max_cycles=1)
    assert len(client.posts) == 1                                       # shard 3 funded -> the order PLACES


@pytest.mark.asyncio
async def test_sustained_underfunding_alarm_fires_at_N_and_is_not_latched(tmp_path, caplog):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p); _seed(p)
    _arm_both(leg)                                                      # ARMED, but shard 3 EMPTY -> skip every cycle
    client = FakeClient(balance_resp=_EMPTY3, positions=[],
                        game_markets=[FakeMarket(T_TOR, exchange_index=3), FakeMarket(T_SEA, exchange_index=3)])
    pos = FakePositionsClient(FakeBook([FakePos("0xopen", 0, "Toronto Blue Jays", cur=0.5)]))
    with caplog.at_level(logging.WARNING):
        await L.scheduled_pm_live_loop(p, FakeBroker(client), pos, account_id=ACCT, category=CAT, poll_sec=0,
                                       legacy_db_path=leg, _max_cycles=3)                 # N=3 -> alarm on cycle 3
    assert any("SUSTAINED SHARD UNDERFUNDING" in r.getMessage() for r in caplog.records)
    assert client.posts == []                                          # nothing placed (all underfunded)
    row = arm.current_row(ACCT, CAT, legacy_db_path=leg)               # SURFACED, NOT latched: a funding gap is not a fault
    assert row["latched"] is False and arm.is_armed(ACCT, CAT, legacy_db_path=leg) is True


# ── structural: the plumbing carries shard_balances + the summary reports underfunded skips ──
def test_summary_reports_shard_underfunded(tmp_path):
    import asyncio
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)

    async def stub(d):
        return None
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        summ = asyncio.run(L.run_live_arm_gated_cycle(conn, _sub(), [_sig()], _ctx(_markets(tor_shard=3)), j, NOW,
                           place_fn=stub, shard_balances=_bal({3: 0.0}), legacy_db_path=leg))
    assert summ["n_shard_underfunded"] == 1 and summ["placed"] == 0 and summ["posts_sent"] == 0
