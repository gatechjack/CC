"""Stage 3 R7.i -- THE DISARM PROOF against the system AS IT ACTUALLY IS: real armed state, a real filled order in
the journal, a real position held. Placement STUBBED; ZERO real POSTs. Also carries the RULING A hardening test
(2026-08-31): a double-fault at boot (boot-reconcile raises AND the force-latch write raises) must REFUSE to enter
the trading loop, never fall through into an armed cycle on an unverified journal.

PROVES:
  1. DISARM WHILE A POSITION IS OPEN closes nothing -- disarm blocks EXITS too (off is off); the operator
     flattens by hand. An exit signal on the held position is disarm-blocked, never placed.
  2. RE-ARM after a manual disarm, with the count-ceiling latch STILL set, REFUSES without --clear-latch
     (LatchedError) and only succeeds with require_latch_clear=True. The structural guard holds now that a real
     journal + driver exist.
  3. THE LATCH SURVIVES A RESTART: a fresh read sees latched=True (durable agent_state, not in-memory), and a
     clean boot-reconcile pass does NOT clear it -- the sub comes back LATCHED, not merely disarmed.
  4. ★ RULING A: on a boot-reconcile fault whose force-latch ALSO fails, scheduled_pm_live_loop RETURNS before the
     while loop -- it does not reach a cycle, so it cannot trade on an unverified journal.
"""
import sqlite3
import time as _t

import pytest

from trading_corp.prediction_markets import arm, db, execution as ex, live_driver as L
import trading_corp.prediction_markets.shard_balance as SB
from trading_corp.data import mlb_poly_kalshi_match as M

ACCT, CAT, DIV = "kalshi_jack", "mlb", "kalshi_jack:mlb"
WALLET = "0x16bb9951a36fce71e2ef57890b786145e0ba8492"
CID = "0x9c62c626cfe36f5273fa016e27803a00c75a19a62a044a1941f83c55706bf97b"
OIDX = 1
TICKER = "KXMLBGAME-26AUG301920CINCHC-CHC"
TICKER_OPP = "KXMLBGAME-26AUG301920CINCHC-CIN"
LEG, SLUG, OUTCOME = "yes", "mlb-cin-chc-2026-08-30", "Chicago Cubs"
REAL_COID = "0752f7f6-b49b-590f-ba10-dd76d3d82b82"
NOW = 1788128073 + 3600
MARKETS = {
    TICKER: {"yes_ask_dollars": 0.60, "yes_bid_dollars": 0.58, "no_ask_dollars": 0.42, "no_bid_dollars": 0.40,
             "liquidity_dollars": 500, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00", "exchange_index": 3},
}


def _ctx():
    return ex.MarketContext(M.build_kalshi_game_index([TICKER, TICKER_OPP]),
                            M.build_kalshi_total_index([]), M.build_kalshi_spread_index([]),
                            frozenset({"2026-08-30"}), MARKETS)


def _sub(**over):
    base = dict(account_id=ACCT, category=CAT, market_types=("moneyline", "total", "spread"), sizing_mode="fixed",
                fixed_stake_usd=5.0, per_order_usd_cap=25.0, daily_usd_cap=50.0, max_open_usd=100.0,
                max_orders_per_day=25, max_slippage_cents=2)
    base.update(over)
    return ex.SubConfig(**base)


class _Pos:
    def __init__(self, cid=CID, oidx=OIDX, outcome=OUTCOME, slug=SLUG, cur=0.595, redeemable=False):
        self.condition_id = cid; self.slug = slug; self.outcome = outcome
        self.extra = {"outcomeIndex": oidx, "curPrice": cur, "redeemable": redeemable}


class _FakeKPos:
    def __init__(self, ticker, position_fp): self.ticker = ticker; self.position_fp = position_fp


class _Fill:
    order_id = "OID"; qty = 1.0; price = 0.60; fee = 0.0084


class _FakePortfolio:
    def __init__(self, positions=None): self._positions = positions or []
    async def get_positions(self, fetch_all=False): return list(self._positions)


class _FakeClient:
    def __init__(self, *, positions=None, game_markets=None):
        self.posts = []; self.portfolio = _FakePortfolio(positions); self._game = game_markets or []
    async def post(self, path, body):
        self.posts.append((path, body))
        return {"order_id": "OID", "fill_count": "1", "average_fill_price": "0.60", "average_fee_paid": "0.0084",
                "remaining_count": "0"}
    async def get_markets(self, series_ticker=None, status=None, limit=None, fetch_all=False, **kw):
        return self._game if series_ticker == "KXMLBGAME" else []


class _FakeBroker:
    def __init__(self, client):
        class _R: pass
        self._read = _R(); self._read._client = client


class _FakeMarket:
    def __init__(self, ticker): self.ticker = ticker; self.yes_ask_dollars = 0.60; self.no_ask_dollars = 0.40; self.liquidity_dollars = 500


class _FakeBook:
    def __init__(self, rows): self.rows = rows; self.complete = True; self.n = len(rows); self.pages = 1


class _FakePositionsClient:
    def __init__(self, book): self._book = book
    async def fetch_positions_book(self, wallet): return self._book


def _legacy(tmp_path):
    p = str(tmp_path / "trading_corp.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE agent_state (agent TEXT NOT NULL, key TEXT NOT NULL, value_json TEXT NOT NULL, "
              "updated_ts TEXT NOT NULL, PRIMARY KEY (agent, key))")
    c.commit(); c.close()
    return p


def _arm_both(leg):
    arm.arm(global_=True, require_latch_clear=True, legacy_db_path=leg)
    arm.arm(ACCT, CAT, require_latch_clear=True, legacy_db_path=leg)


def _insert_filled(conn, *, coid=REAL_COID, ticker=TICKER, leg=LEG):
    conn.execute(
        "INSERT INTO pm_subdivision_order (account_id, category, wallet, condition_id, outcome_index, signal_id, "
        " client_order_id, ticker, order_side, outcome_leg, is_exit, submitted_count, submitted_price, "
        " time_in_force, outcome_status, broker_order_id, fill_count, fill_price, fee, dry_run, submitted_ts, response_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,0,1,0.62,'immediate_or_cancel','filled','01a054bd',1.0,0.60,0.0084,0,?,?)",
        (ACCT, CAT, WALLET, CID, OIDX, "83c8bf91aa7ccc3196b39e9aecae282b", coid, ticker, "bid", leg, 1788128073, 1788128073))
    conn.commit()


def _seed_money_rows(conn):
    conn.execute("INSERT OR IGNORE INTO pm_account(account_id,venue,secret_ref,label,active,created_ts) "
                 "VALUES(?, 'kalshi','KALSHI','Jack',1,?)", (ACCT, int(_t.time())))
    conn.execute("INSERT OR IGNORE INTO pm_subdivision(account_id,category,market_types,sizing_mode,"
                 "fixed_stake_usd,active,created_ts) VALUES(?,?,'moneyline,total,spread','fixed',5.0,1,?)",
                 (ACCT, CAT, int(_t.time())))
    conn.execute("INSERT OR IGNORE INTO pm_subdivision_attachment(account_id,category,wallet,active,source,added_ts) "
                 "VALUES(?,?,?,1,'promote_to_live',?)", (ACCT, CAT, WALLET, int(_t.time())))
    conn.commit()


# ══ 1: DISARM WHILE A POSITION IS OPEN -> nothing closes it (disarm blocks the EXIT too) ══
@pytest.mark.asyncio
async def test_1_disarm_blocks_the_exit_of_an_open_position(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    calls = []
    async def stub(d):
        calls.append(d); return _Fill()
    exit_sig = ex.CopySignal(wallet=WALLET, slug=SLUG, outcome=OUTCOME, condition_id=CID, outcome_index=OIDX,
                             signal_id="exit_sig_0001", is_exit=True)      # a genuine exit signal (distinct coid)
    with db.connect(p) as conn:
        _insert_filled(conn)                                              # a REAL open position (+1 YES)
        arm.disarm(global_=True, legacy_db_path=leg)                      # operator STOP while the position is open
        j = ex.Journal(conn, [ACCT], NOW)
        summ = await L.run_live_arm_gated_cycle(conn, _sub(), [exit_sig], _ctx(), j, NOW,
                                                place_fn=stub, legacy_db_path=leg)
        n = conn.execute("SELECT COUNT(*) FROM pm_subdivision_order").fetchone()[0]
    assert summ["n_would_place"] == 1                                     # the exit PASSED the gates (would place)...
    assert calls == [] and summ["placed"] == 0 and summ["n_disarm_blocked"] == 1   # ...but DISARM blocked it -> no close
    assert n == 1                                                         # no new row; the position is untouched (hand-flatten)


# ══ 2: RE-ARM with the count-ceiling latch set REFUSES without --clear-latch ══
def test_2_rearm_refuses_while_count_ceiling_latched(tmp_path):
    leg = _legacy(tmp_path)
    arm.arm(global_=True, require_latch_clear=True, legacy_db_path=leg)
    arm.latch_count_ceiling(ACCT, CAT, count=1, cap=1, legacy_db_path=leg)     # the REAL live latch (orders/day 1>=1)
    row = arm.current_row(ACCT, CAT, legacy_db_path=leg)
    assert row["latched"] is True and row["auto_trigger"] == arm.AUTO_COUNT_CEILING and row["armed"] is False
    with pytest.raises(arm.LatchedError):                                     # a plain arm() REFUSES a latched scope
        arm.arm(ACCT, CAT, legacy_db_path=leg)
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False               # still disarmed after the refused arm
    arm.arm(ACCT, CAT, require_latch_clear=True, legacy_db_path=leg)          # ONLY the explicit ack clears + arms
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is True
    assert arm.current_row(ACCT, CAT, legacy_db_path=leg)["latched"] is False


# ══ 3: THE LATCH SURVIVES A RESTART (durable) + a clean boot-reconcile does NOT clear it ══
@pytest.mark.asyncio
async def test_3_latched_state_survives_restart(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    arm.arm(global_=True, require_latch_clear=True, legacy_db_path=leg)
    arm.latch_count_ceiling(ACCT, CAT, count=1, cap=1, legacy_db_path=leg)
    # 'restart' = a fresh read of the durable legacy DB (no in-memory carry): the latch is still there
    v = arm.read_arm_verdict(ACCT, CAT, legacy_db_path=leg)
    assert v.armed is False and v.latched is True and v.auto_trigger == arm.AUTO_COUNT_CEILING
    # a full boot cycle: a CLEAN boot-reconcile (journal +1 == venue +1) writes NOTHING -> the latch is preserved
    with db.connect(p) as conn:
        _seed_money_rows(conn); _insert_filled(conn)                          # journal holds +1 YES on the ticker
    client = _FakeClient(positions=[_FakeKPos(TICKER, 1)], game_markets=[_FakeMarket(TICKER)])
    await L.scheduled_pm_live_loop(p, _FakeBroker(client), _FakePositionsClient(_FakeBook([_Pos()])),
                                   account_id=ACCT, category=CAT, poll_sec=0, legacy_db_path=leg, _max_cycles=1)
    assert client.posts == []                                                # latched -> the cycle places nothing
    row = arm.current_row(ACCT, CAT, legacy_db_path=leg)
    assert row["latched"] is True and row["auto_trigger"] == arm.AUTO_COUNT_CEILING   # comes back LATCHED, not merely disarmed


# ══ 4 (★ RULING A): a double fault at boot REFUSES to enter the cycle (no trade on an unverified journal) ══
@pytest.mark.asyncio
async def test_4_ruling_a_double_fault_refuses_to_enter_the_cycle(tmp_path, monkeypatch):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)                                                            # ARMED -> the double fault must NOT let it trade
    with db.connect(p) as conn:
        _seed_money_rows(conn)
    async def _boom_recon(*a, **k):
        raise RuntimeError("boot-reconcile system fault (journal unreadable)")
    def _boom_latch(*a, **k):
        raise RuntimeError("legacy agent_state unwritable")
    monkeypatch.setattr(L, "run_boot_reconcile", _boom_recon)                 # boot-reconcile RAISES
    monkeypatch.setattr(L.arm, "latch_boot_reconcile_mismatch", _boom_latch)  # ...and the force-latch ALSO raises
    entered = []
    real_SB = SB.ShardBalances
    def _rec(*a, **k):                                                        # ShardBalances = the FIRST statement in the while loop
        entered.append(1); return real_SB(*a, **k)
    monkeypatch.setattr(L.shard_balance, "ShardBalances", _rec)
    client = _FakeClient(positions=[], game_markets=[])
    await L.scheduled_pm_live_loop(p, _FakeBroker(client), _FakePositionsClient(_FakeBook([])),
                                   account_id=ACCT, category=CAT, poll_sec=0, legacy_db_path=leg, _max_cycles=1)
    assert entered == []                                                      # ★ the while loop was NEVER entered
    assert client.posts == []                                                 # ...so nothing was placed on the unverified journal
