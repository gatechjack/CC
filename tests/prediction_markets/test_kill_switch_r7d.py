"""Stage 3 R7.d -- PROVE THE KILL SWITCH END TO END, WITH THE DRIVER ATTACHED.

R5 proved arm/disarm against the chokepoint in isolation; R7.c proved the driver re-reads the verdict. R7.d
proves the WHOLE ASSEMBLY (driver `run_live_arm_gated_cycle` / `scheduled_pm_live_loop` + chokepoint
`execution.evaluate` + arm state) STOPS when told to -- placement STUBBED, ZERO real POSTs anywhere.

Each bullet from Jack's R7.d brief is a NAMED test below:
  1. Cold start, NO arm state at all -> DISARMED; nothing posts across a full driver cycle.
  2. Armed then killed MID-CYCLE -> the in-flight cycle stops at the next order boundary. The one order
     already POSTed is NOT recalled -- that window is one order wide and IRREDUCIBLE; the test confirms the
     code behaves as designed rather than pretending the window closed.
  3. The kill PERSISTS ACROSS A SIMULATED RESTART (a fresh `scheduled_pm_live_loop`); the driver does not resume.
  4. Each of the FOUR latching auto-disarm triggers fires, latches, and blocks the next order -- consecutive
     OrderPlacementError, auth-failure (also disarms the ACCOUNT + flags manual exit), COUNT CEILING (newly
     wired -- it was dead code), boot-reconcile mismatch.
  5. A latched disarm CANNOT be cleared by an engine-side caller -- only arm(require_latch_clear=True).
  6. ★ DISARM BLOCKS EXITS TOO (off is off), even though gates 5/6/8 are exit-EXEMPT for BUDGET reasons.
  7. The CLI disarm path works when pm_web is down (pm_cli, not a web route).
  8. ZERO real POSTs across all of it -- asserted in every test (fakes CAPTURE would-be POSTs; no real broker).

★ R7.d CODE CHANGE proven here: `live_driver.run_live_arm_gated_cycle` now fires `arm.latch_count_ceiling`
on a `reject:count_ceiling` (armed) -- the 4th latch had NO caller before (a runaway would reject at gate 8
every ~poll_sec forever while the arm state still read ARMED). See test 5.

Offline; tmp DBs. Arm state lives in a temp LEGACY agent_state DB; the Kalshi client is a fake that records posts."""
import importlib.util as _ilu
import inspect
import os
import sqlite3
import time

import pytest

from trading_corp.prediction_markets import arm, db, execution as ex, live_driver as L
from trading_corp.data import mlb_poly_kalshi_match as M

ACCT, CAT = "kalshi_jack", "mlb"
NOW = 1787900000
GAME_TICKERS = ["KXMLBGAME-26AUG281915SEATOR-SEA", "KXMLBGAME-26AUG281915SEATOR-TOR"]
TOTAL_TICKERS = ["KXMLBTOTAL-26AUG281915SEATOR-9"]
SPREAD_TICKERS = ["KXMLBSPREAD-26AUG281915SEATOR-TOR2"]
T_TOR = "KXMLBGAME-26AUG281915SEATOR-TOR"
T_SEA = "KXMLBGAME-26AUG281915SEATOR-SEA"
MARKETS = {
    T_TOR: {"yes_ask_dollars": 0.55, "yes_bid_dollars": 0.53, "no_ask_dollars": 0.47, "liquidity_dollars": 500},
    T_SEA: {"yes_ask_dollars": 0.47, "yes_bid_dollars": 0.45, "no_ask_dollars": 0.55, "liquidity_dollars": 500},
}
SLUG = "mlb-sea-tor-2026-08-28"


# ── the pm_cli module (scripts/ has no __init__.py -> load it by file path, robustly) ──
def _load_pm_cli():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    spec = _ilu.spec_from_file_location("pm_cli_r7d", os.path.join(root, "trading_corp", "scripts", "pm_cli.py"))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── temp DBs + config ────────────────────────────────────────────────────────
def _legacy(tmp_path):
    p = str(tmp_path / "trading_corp.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE agent_state (agent TEXT NOT NULL, key TEXT NOT NULL, value_json TEXT NOT NULL, "
              "updated_ts TEXT NOT NULL, PRIMARY KEY (agent, key))")
    c.commit(); c.close()
    return p


def _ctx(markets=None):
    return ex.MarketContext(M.build_kalshi_game_index(GAME_TICKERS), M.build_kalshi_total_index(TOTAL_TICKERS),
                            M.build_kalshi_spread_index(SPREAD_TICKERS), frozenset({"2026-08-28"}),
                            MARKETS if markets is None else markets)


def _sub(**over):
    base = dict(account_id=ACCT, category=CAT, market_types=("moneyline", "total", "spread"), sizing_mode="fixed",
                fixed_stake_usd=5.0, per_order_usd_cap=25.0, daily_usd_cap=50.0, max_open_usd=100.0,
                max_orders_per_day=25, max_slippage_cents=2)
    base.update(over)
    return ex.SubConfig(**base)


def _sig(outcome, sid, is_exit=False, wallet="0x16bb9951a36fce71e2ef57890b786145e0ba8492"):
    return ex.CopySignal(wallet=wallet, slug=SLUG, outcome=outcome, condition_id="0xc_" + sid,
                         outcome_index=0, signal_id=sid, is_exit=is_exit)


def _arm_both(leg):
    arm.arm(global_=True, require_latch_clear=True, legacy_db_path=leg)
    arm.arm(ACCT, CAT, require_latch_clear=True, legacy_db_path=leg)


def _seed_money_layer(p):
    """The pm_account / pm_subdivision / pm_subdivision_attachment rows the scheduled loop reads."""
    with db.connect(p) as conn:
        conn.execute("INSERT OR IGNORE INTO pm_account(account_id,venue,secret_ref,label,active,created_ts) "
                     "VALUES(?, 'kalshi','KALSHI','Jack',1,?)", (ACCT, int(time.time())))
        conn.execute("INSERT OR IGNORE INTO pm_subdivision(account_id,category,market_types,sizing_mode,"
                     "fixed_stake_usd,active,created_ts) VALUES(?,?,'moneyline,total,spread','fixed',5.0,1,?)",
                     (ACCT, CAT, int(time.time())))
        conn.execute("INSERT OR IGNORE INTO pm_subdivision_attachment(account_id,category,wallet,active,source,added_ts) "
                     "VALUES(?,?,?,1,'promote_to_live',?)", (ACCT, CAT, "0xWHALE", int(time.time())))
        conn.commit()


# ── fakes (record posts; never a real network call) ──────────────────────────
_FILL = {"order_id": "OID1", "fill_count": "1", "average_fill_price": "0.55", "average_fee_paid": "0.01",
         "remaining_count": "0"}


class _FakePortfolio:
    def __init__(self, positions=None, raise_exc=None):
        self._positions = positions or []; self._raise = raise_exc
    async def get_positions(self, fetch_all=False):
        if self._raise:
            raise self._raise
        return list(self._positions)


class FakeKalshiClient:
    """Records every would-be POST; NEVER hits the network."""
    def __init__(self, *, post_raise=None, positions=None, pos_raise=None, game_markets=None):
        self.posts = []
        self._post_raise = post_raise
        self.portfolio = _FakePortfolio(positions, pos_raise)
        self._game = game_markets or []
    async def post(self, path, body):
        self.posts.append((path, body))
        if self._post_raise:
            raise self._post_raise
        return dict(_FILL)
    async def get_markets(self, series_ticker=None, status=None, limit=None, fetch_all=False, **kw):
        return self._game if series_ticker == "KXMLBGAME" else []


class FakeBroker:
    def __init__(self, client):
        class _R:
            pass
        self._read = _R(); self._read._client = client


class FakeKPos:
    def __init__(self, ticker, position_fp):
        self.ticker = ticker; self.position_fp = position_fp


class FakeMarket:
    def __init__(self, ticker, yes_ask=0.55, no_ask=0.47, liq=500):
        self.ticker = ticker; self.yes_ask_dollars = yes_ask; self.no_ask_dollars = no_ask; self.liquidity_dollars = liq


class FakePos:
    def __init__(self, cid, oidx, outcome, cur=0.5, redeemable=False):
        self.condition_id = cid; self.slug = SLUG; self.outcome = outcome
        self.extra = {"outcomeIndex": oidx, "curPrice": cur, "redeemable": redeemable}


class FakeBook:
    def __init__(self, rows, complete=True):
        self.rows = rows; self.complete = complete; self.n = len(rows); self.pages = 1


class FakePositionsClient:
    def __init__(self, book):
        self._book = book
    async def fetch_positions_book(self, wallet):
        return self._book


def _wp(conn, sid="m1", outcome="Toronto Blue Jays", is_exit=False):
    """Drive execution.evaluate to a gate-passing decision -- to prove a signal WOULD place (so a later
    ZERO-posts is due to the KILL, not to an absent/rejected signal)."""
    j = ex.Journal(conn, [ACCT], NOW)
    return ex.evaluate(_sig(outcome, sid, is_exit=is_exit), _sub(), _ctx(), j, conn, NOW)


# ════════════════════════════════════════════════════════════════════════════════
# 1. COLD START -- no arm state at all -> DISARMED; the driver posts NOTHING across a full cycle.
# ════════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_r7d_cold_start_no_arm_state_driver_posts_nothing(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p); _seed_money_layer(p)
    # the signal the loop will see WOULD place if armed (so zero-posts below is the KILL, not 'no signal'):
    with db.connect(p) as conn:
        assert _wp(conn).status == "dry_run_would_place"
    fake = FakeKalshiClient(positions=[], game_markets=[FakeMarket(T_TOR), FakeMarket(T_SEA)])
    pos_client = FakePositionsClient(FakeBook([FakePos("0xopen", 0, "Toronto Blue Jays", cur=0.5)]))
    # NO arm rows exist at all -> read_arm_verdict is fail-safe DISARMED.
    await L.scheduled_pm_live_loop(p, FakeBroker(fake), pos_client, account_id=ACCT, category=CAT,
                                   poll_sec=0, legacy_db_path=leg, _max_cycles=1)
    assert fake.posts == []                                          # ZERO real POSTs
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False      # still disarmed (the loop armed nothing)
    with db.connect(p) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_order").fetchone()[0] == 0   # nothing journaled


# ════════════════════════════════════════════════════════════════════════════════
# 2. MID-CYCLE KILL -> stops at the next order boundary; the already-POSTed order is NOT recalled.
#    THE WINDOW IS ONE ORDER WIDE AND IRREDUCIBLE: a kill landing after the per-order re-read but before/
#    during the POST lets THAT order land; the re-read stops the NEXT one. We do not pretend order 1 closed.
# ════════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_r7d_mid_cycle_kill_one_order_window(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    fake = FakeKalshiClient()
    _place = L.make_place_fn(fake)
    async def kill_after_first(d):
        r = await _place(d)                                          # order 1 is POSTED to the venue HERE...
        arm.disarm(ACCT, CAT, reason="mid_cycle_kill", legacy_db_path=leg)   # ...then the kill lands
        return r
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        summ = await L.run_live_arm_gated_cycle(conn, _sub(), [_sig("Toronto Blue Jays", "m1"),
                                                _sig("Seattle Mariners", "m2")], _ctx(), j, NOW,
                                                place_fn=kill_after_first, legacy_db_path=leg)
    assert summ["placed"] == 1 and summ["n_disarm_blocked"] == 1     # the 2nd was blocked by the per-order re-read
    assert len(fake.posts) == 1                                      # exactly ONE POST reached the venue and STAYS sent
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False      # the kill persisted


# ════════════════════════════════════════════════════════════════════════════════
# 3. THE KILL PERSISTS ACROSS A SIMULATED RESTART -- a MANUAL disarm, then a fresh scheduled loop does not resume.
# ════════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_r7d_manual_disarm_persists_across_simulated_restart(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p); _seed_money_layer(p)
    _arm_both(leg)
    arm.disarm(ACCT, CAT, reason="operator_kill", legacy_db_path=leg)     # the operator kills, then the process restarts
    fake = FakeKalshiClient(positions=[], game_markets=[FakeMarket(T_TOR), FakeMarket(T_SEA)])
    pos_client = FakePositionsClient(FakeBook([FakePos("0xopen", 0, "Toronto Blue Jays", cur=0.5)]))
    await L.scheduled_pm_live_loop(p, FakeBroker(fake), pos_client, account_id=ACCT, category=CAT,   # 'restart'
                                   poll_sec=0, legacy_db_path=leg, _max_cycles=1)
    assert fake.posts == []                                          # the restarted driver does NOT resume placing
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False      # the persisted disarm is still in force


# ════════════════════════════════════════════════════════════════════════════════
# 4. A LATCH persists across a restart too -- and the driver does NOT clear it (only a human --clear-latch can).
# ════════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_r7d_latch_persists_across_simulated_restart(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p); _seed_money_layer(p)
    _arm_both(leg)
    arm.latch_consecutive_errors(ACCT, CAT, n=3, legacy_db_path=leg)      # an auto-disarm latched before the 'restart'
    fake = FakeKalshiClient(positions=[], game_markets=[FakeMarket(T_TOR), FakeMarket(T_SEA)])
    pos_client = FakePositionsClient(FakeBook([FakePos("0xopen", 0, "Toronto Blue Jays", cur=0.5)]))
    await L.scheduled_pm_live_loop(p, FakeBroker(fake), pos_client, account_id=ACCT, category=CAT,
                                   poll_sec=0, legacy_db_path=leg, _max_cycles=1)                     # 'restart'
    assert fake.posts == []
    row = arm.current_row(ACCT, CAT, legacy_db_path=leg)
    assert row["latched"] is True and row["auto_trigger"] == arm.AUTO_CONSECUTIVE_ERRORS   # NOT cleared by the restart
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False


# ════════════════════════════════════════════════════════════════════════════════
# 5. ★ THE 4TH LATCH (COUNT CEILING) FIRES THROUGH THE DRIVER -- this is the R7.d code change.
#    It was DEAD CODE: arm.latch_count_ceiling had no caller, so a runaway would reject at gate 8 every
#    ~poll_sec forever while the arm state still read ARMED. Now the driver latches + disarms + stops.
# ════════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_r7d_count_ceiling_latches_through_driver_and_blocks_next(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    fake = FakeKalshiClient()
    place_fn = L.make_place_fn(fake)
    sub2 = _sub(max_orders_per_day=2)                               # ceiling of 2; a 3rd would-place trips gate 8
    sigs = [_sig("Toronto Blue Jays", "c%d" % i) for i in range(3)]
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        summ = await L.run_live_arm_gated_cycle(conn, sub2, sigs, _ctx(), j, NOW, place_fn=place_fn, legacy_db_path=leg)
    assert summ["placed"] == 2 and len(fake.posts) == 2             # exactly the ceiling placed
    assert summ["ceiling_latched"] is True                         # the driver fired the latch on the 3rd
    row = arm.current_row(ACCT, CAT, legacy_db_path=leg)
    assert row["latched"] is True and row["auto_trigger"] == arm.AUTO_COUNT_CEILING
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False     # DISARMED until a human --clear-latch
    # the NEXT cycle places nothing more (disarmed + the ceiling holds) -- ZERO further POSTs
    with db.connect(p) as conn:
        j2 = ex.Journal(conn, [ACCT], NOW)
        summ2 = await L.run_live_arm_gated_cycle(conn, sub2, [_sig("Toronto Blue Jays", "c9")], _ctx(), j2, NOW,
                                                 place_fn=place_fn, legacy_db_path=leg)
    assert summ2["posts_sent"] == 0 and len(fake.posts) == 2


# ════════════════════════════════════════════════════════════════════════════════
# 6. EACH of the four latch types DISARMS -> the driver's per-order re-read BLOCKS the next order (ZERO posts).
# ════════════════════════════════════════════════════════════════════════════════
_LATCH_SETTERS = [
    ("consecutive", lambda leg: arm.latch_consecutive_errors(ACCT, CAT, n=3, legacy_db_path=leg), arm.AUTO_CONSECUTIVE_ERRORS),
    ("auth", lambda leg: arm.latch_auth_failure(ACCT, [CAT], detail="401 on POST", legacy_db_path=leg), arm.AUTO_AUTH_FAILURE),
    ("count_ceiling", lambda leg: arm.latch_count_ceiling(ACCT, CAT, count=25, cap=25, legacy_db_path=leg), arm.AUTO_COUNT_CEILING),
    ("boot_reconcile", lambda leg: arm.latch_boot_reconcile_mismatch(ACCT, CAT, legacy_db_path=leg), arm.AUTO_BOOT_RECONCILE),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,setter,trigger", _LATCH_SETTERS)
async def test_r7d_each_latch_type_blocks_the_next_driver_order(tmp_path, name, setter, trigger):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    setter(leg)                                                     # LATCH via this trigger
    row = arm.current_row(ACCT, CAT, legacy_db_path=leg)
    assert row["latched"] is True and row["auto_trigger"] == trigger and arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False
    if name == "auth":
        assert row["manual_exit_required"] is True                 # auth-failure flags open positions for MANUAL exit
    # a fresh driver cycle with a WOULD-PLACE signal is blocked by the per-order re-read -> ZERO posts
    fake = FakeKalshiClient()
    place_fn = L.make_place_fn(fake)
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        summ = await L.run_live_arm_gated_cycle(conn, _sub(), [_sig("Toronto Blue Jays", "n1")], _ctx(), j, NOW,
                                                place_fn=place_fn, legacy_db_path=leg)
    assert summ["n_would_place"] == 1 and summ["placed"] == 0 and summ["n_disarm_blocked"] == 1
    assert fake.posts == []                                         # the next order NEVER left


# ════════════════════════════════════════════════════════════════════════════════
# 7. A LATCHED disarm CANNOT be cleared by an engine-side caller -- only arm(require_latch_clear=True).
#    (R5 fixed this STRUCTURALLY; prove it still holds now that a driver exists that latches.)
# ════════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_r7d_latched_disarm_not_clearable_by_engine_caller(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    # the DRIVER latches (a loud reject storm -> consecutive-error latch):
    async def loud(d):
        raise L.OrderPlacementError("kalshi V2 POST rejected: bad_request 400")
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        await L.run_live_arm_gated_cycle(conn, _sub(), [_sig("Toronto Blue Jays", "e%d" % i) for i in range(3)],
                                         _ctx(), j, NOW, place_fn=loud, legacy_db_path=leg)
    assert arm.current_row(ACCT, CAT, legacy_db_path=leg)["auto_trigger"] == arm.AUTO_CONSECUTIVE_ERRORS
    # an engine-side arm WITHOUT the human ack RAISES -- it cannot silently clear the latch:
    with pytest.raises(arm.LatchedError):
        arm.arm(ACCT, CAT, by="engine_bug", legacy_db_path=leg)
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False
    # and a fresh driver cycle stays blocked while latched:
    fake = FakeKalshiClient()
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        summ = await L.run_live_arm_gated_cycle(conn, _sub(), [_sig("Toronto Blue Jays", "z1")], _ctx(), j, NOW,
                                                place_fn=L.make_place_fn(fake), legacy_db_path=leg)
    assert summ["n_disarm_blocked"] == 1 and fake.posts == []
    # WITH the ack (the CLI's --clear-latch) the human clears it, and the driver can place again:
    arm.arm(ACCT, CAT, by="jack", require_latch_clear=True, legacy_db_path=leg)
    fake2 = FakeKalshiClient()
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        summ2 = await L.run_live_arm_gated_cycle(conn, _sub(), [_sig("Toronto Blue Jays", "z2")], _ctx(), j, NOW,
                                                 place_fn=L.make_place_fn(fake2), legacy_db_path=leg)
    assert summ2["placed"] == 1 and len(fake2.posts) == 1


# ════════════════════════════════════════════════════════════════════════════════
# 8. ★ DISARM BLOCKS EXITS TOO -- through the DRIVER. gates 5/6/8 are exit-EXEMPT (budget), but off is off.
# ════════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_r7d_disarm_blocks_exit_through_the_driver(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    exit_sig = _sig("Toronto Blue Jays", "x", is_exit=True)
    # the exit is exit-EXEMPT -> it PASSES evaluate even under a tiny daily cap (proves the gates don't block it):
    sub_tight = _sub(daily_usd_cap=1.0)
    with db.connect(p) as conn:
        d = ex.evaluate(exit_sig, sub_tight, _ctx(), ex.Journal(conn, [ACCT], NOW), conn, NOW, legacy_db_path=leg)
        assert d.status == "dry_run_would_place" and d.is_exit is True
    # DISARMED: the exit does NOT reach the placer (off is off; the human flattens by hand on Kalshi):
    fake = FakeKalshiClient()
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        summ_off = await L.run_live_arm_gated_cycle(conn, sub_tight, [exit_sig], _ctx(), j, NOW,
                                                    place_fn=L.make_place_fn(fake), legacy_db_path=leg)
    assert summ_off["n_would_place"] == 1 and summ_off["placed"] == 0 and summ_off["n_disarm_blocked"] == 1
    assert fake.posts == []
    # ARMED: the SAME exit DOES place (you must be able to close when the engine is ON) -- reduce_only:
    _arm_both(leg)
    fake2 = FakeKalshiClient()
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        summ_on = await L.run_live_arm_gated_cycle(conn, sub_tight, [exit_sig], _ctx(), j, NOW,
                                                   place_fn=L.make_place_fn(fake2), legacy_db_path=leg)
    assert summ_on["placed"] == 1 and len(fake2.posts) == 1
    assert fake2.posts[0][1].get("reduce_only") is True


# ════════════════════════════════════════════════════════════════════════════════
# 9. THE CLI DISARM PATH WORKS WHEN pm_web IS DOWN -- pm_cli directly, no web server in this test at all.
# ════════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_r7d_cli_disarm_works_when_pm_web_is_down(tmp_path):
    leg = _legacy(tmp_path)
    _arm_both(leg)
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is True
    pm_cli = _load_pm_cli()                                         # a standalone script; NO web import, NO server
    rc = pm_cli.main(["live-disarm", "--account", ACCT, "--category", CAT, "--legacy-db", leg,
                      "--reason", "operator_kill_headless"])
    assert rc == 0
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False     # the CLI kill flipped the PERSISTED state
    # and the master kill works headless too (blocks every account):
    rc2 = pm_cli.main(["live-disarm", "--global", "--legacy-db", leg])
    assert rc2 == 0 and arm.read_status(ACCT, CAT, legacy_db_path=leg)["global_armed"] is False


# ════════════════════════════════════════════════════════════════════════════════
# 10. ADVERSARIAL-REVIEW FIX (MEDIUM): the latch-clear guard FAILS SAFE on an UNREADABLE row.
#     A transient/corrupt read must NOT let a killed (latched) account re-arm without the ack, and a manual
#     disarm over an unreadable row must NOT silently drop a latch. (The old guard read _load_row and skipped
#     on None -- but None also means an INDETERMINATE read, so a read race could re-arm a killed account.)
# ════════════════════════════════════════════════════════════════════════════════
def test_r7d_latch_guard_fails_safe_on_unreadable_row(tmp_path):
    leg = _legacy(tmp_path)
    # a definitively-ABSENT scope STILL arms without the flag (cold start must not be blocked by the fix):
    arm.arm(ACCT, CAT, legacy_db_path=leg)
    assert arm.current_row(ACCT, CAT, legacy_db_path=leg)["armed"] is True
    arm.disarm(ACCT, CAT, legacy_db_path=leg)
    # CORRUPT the sub row so the latch state is INDETERMINATE (json that will not parse):
    c = sqlite3.connect(leg)
    c.execute("INSERT OR REPLACE INTO agent_state(agent,key,value_json,updated_ts) VALUES('pm_live',?,?,?)",
              (arm.sub_key(ACCT, CAT), "CORRUPT{{not json", "2026-08-29T00:00:00Z"))
    c.commit(); c.close()
    # the READ/verdict path still degrades to DISARMED on the unreadable row (unchanged fail-safe):
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False
    # arm() WITHOUT the ack must REFUSE (unreadable latch state -> treat as latched, do NOT re-arm a kill):
    with pytest.raises(arm.LatchedError):
        arm.arm(ACCT, CAT, by="engine_bug", legacy_db_path=leg)
    # disarm() over the unreadable row must PRESERVE a latch (never silently drop it -> never skip the ack):
    arm.disarm(ACCT, CAT, reason="kill_over_unreadable", legacy_db_path=leg)
    row = arm.current_row(ACCT, CAT, legacy_db_path=leg)
    assert row["armed"] is False and row["latched"] is True
    with pytest.raises(arm.LatchedError):                           # a later arm STILL needs the ack
        arm.arm(ACCT, CAT, legacy_db_path=leg)
    # the human ack (the CLI's --clear-latch) is the ONLY path that clears it:
    arm.arm(ACCT, CAT, require_latch_clear=True, legacy_db_path=leg)
    r2 = arm.current_row(ACCT, CAT, legacy_db_path=leg)
    assert r2["armed"] is True and r2["latched"] is False


# ── structural: R7.d added NO broker/rebuild surface and the cycle still reports ceiling_latched ──
def test_r7d_structural_no_broker_and_ceiling_flag_present():
    for banned in ("KalshiLiveBroker", "KalshiBroker", "place_order", "build_v2_event_order"):
        assert banned not in dir(L), banned
    assert "ceiling_latched" in inspect.getsource(L.run_live_arm_gated_cycle)   # the 4th-latch flag is wired
    assert "latch_count_ceiling" in inspect.getsource(L.run_live_arm_gated_cycle)
