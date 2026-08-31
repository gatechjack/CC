"""Stage 3 R7.c -- prove the LIVE DRIVER (the code that PLACES REAL ORDERS) WITHOUT placing anything: stub broker,
disarmed, fake clients that CAPTURE would-be POSTs. Covers the seven the transition doc names + ZERO real POSTs:
  1. the async cycle (run_live_arm_gated_cycle + scheduled_pm_live_loop bounded)
  2. boot-reconcile latches at boot on mismatch
  3. arm-gating: DISARMED means NO POST
  4. ★ the arm state is re-read before EVERY order (a mid-cycle kill stops the NEXT order, not once per cycle)
  5. signal conversion (/positions -> entry CopySignal)
  6. ★ the place_fn POSTs EXACTLY decision.body + decision.client_order_id -- asserted by OBJECT IDENTITY (no
     rebuild), the whole reason option (b) was chosen over wrapping place_order
  7. the auth-failure + consecutive-error latches
and everywhere: ZERO real POSTs (a real Kalshi client is never constructed; the fake CAPTURES posts).

Offline; tmp DBs. Arm state lives in a temp LEGACY agent_state DB; the Kalshi client is a fake that records posts."""
import inspect
import sqlite3

import pytest

from trading_corp.prediction_markets import arm, db, execution as ex, live_driver as L, boot_reconcile as BR
from trading_corp.data import mlb_poly_kalshi_match as M
from trading_corp.data.polymarket_data_api_client import PositionRow, ActivityRow

ACCT, CAT = "kalshi_jack", "mlb"
NOW = 1787900000
GAME_TICKERS = ["KXMLBGAME-26AUG281915SEATOR-SEA", "KXMLBGAME-26AUG281915SEATOR-TOR"]
TOTAL_TICKERS = ["KXMLBTOTAL-26AUG281915SEATOR-9"]
SPREAD_TICKERS = ["KXMLBSPREAD-26AUG281915SEATOR-TOR2"]
T_TOR = "KXMLBGAME-26AUG281915SEATOR-TOR"
T_SEA = "KXMLBGAME-26AUG281915SEATOR-SEA"
MARKETS = {
    T_TOR: {"yes_ask_dollars": 0.55, "yes_bid_dollars": 0.53, "no_ask_dollars": 0.47, "liquidity_dollars": 500, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"},
    T_SEA: {"yes_ask_dollars": 0.47, "yes_bid_dollars": 0.45, "no_ask_dollars": 0.55, "liquidity_dollars": 500, "yes_bid_size_fp": "500.00", "yes_ask_size_fp": "500.00"},
}
SLUG = "mlb-sea-tor-2026-08-28"


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
    """Records every would-be POST; NEVER hits the network. `post` returns a fill or raises a KalshiError."""
    def __init__(self, *, post_raise=None, positions=None, pos_raise=None, game_markets=None, total_markets=None,
                 settlements_raw=None):
        self.posts = []
        self._post_raise = post_raise
        self.portfolio = _FakePortfolio(positions, pos_raise)
        self._game = game_markets or []
        self._total = total_markets or []
        self._settlements = settlements_raw or {}
    async def post(self, path, body):
        self.posts.append((path, body))
        if self._post_raise:
            raise self._post_raise
        return dict(_FILL)
    async def get(self, path, *a, **k):                        # R-d: /portfolio/settlements (raw); {} otherwise
        return self._settlements if "settlements" in str(path) else {}
    async def get_markets(self, series_ticker=None, status=None, limit=None, fetch_all=False, **kw):
        if series_ticker == "KXMLBGAME":
            return self._game
        if series_ticker == "KXMLBTOTAL":
            return self._total
        return []


class FakeBroker:
    def __init__(self, client):
        class _R:
            pass
        self._read = _R(); self._read._client = client


class FakeKPos:                       # a Kalshi portfolio position (boot-reconcile): ticker + signed position_fp
    def __init__(self, ticker, position_fp):
        self.ticker = ticker; self.position_fp = position_fp


class FakeMarket:                     # a pykalshi get_markets market object
    def __init__(self, ticker, yes_ask=0.55, no_ask=0.47, liq=500, yes_bid=0.53):
        self.ticker = ticker; self.yes_ask_dollars = yes_ask; self.no_ask_dollars = no_ask
        self.liquidity_dollars = liq; self.yes_bid_dollars = yes_bid   # yes_bid: the exit prices off the BID


class FakePos:                        # a polymarket /positions row (for is_genuinely_open + pos_outcome_index)
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


def _would_place_decision(conn, leg_outcome="Toronto Blue Jays", sid="m1"):
    """Drive execution.evaluate to a gate-passing 'dry_run_would_place' Decision (with .body + .client_order_id)."""
    j = ex.Journal(conn, [ACCT], NOW)
    d = ex.evaluate(_sig(leg_outcome, sid), _sub(), _ctx(), j, conn, NOW)
    assert d.status == "dry_run_would_place", d.status
    return d


# ── case 6 (★): the place_fn POSTs EXACTLY the approved body (object identity -- no rebuild) ──
@pytest.mark.asyncio
async def test_place_fn_posts_exactly_the_approved_body_by_identity(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        d = _would_place_decision(conn)
    fake = FakeKalshiClient()
    place_fn = L.make_place_fn(fake)
    fill = await place_fn(d)
    assert len(fake.posts) == 1
    path, posted = fake.posts[0]
    assert path == L._V2_ORDERS_PATH
    assert posted is d.body                                    # ★ SAME object -> no reconstruction, byte-identical
    assert posted["client_order_id"] == d.client_order_id      # the APPROVED coid is what is posted
    assert float(getattr(fill, "qty")) == 1.0                  # the pure mapper produced a FillEvent


@pytest.mark.asyncio
async def test_place_fn_error_mapping_reuses_the_split(tmp_path):
    from pykalshi.exceptions import KalshiError
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        d = _would_place_decision(conn)
    # a loud reject -> OrderPlacementError
    with pytest.raises(L.OrderPlacementError):
        await L.make_place_fn(FakeKalshiClient(post_raise=KalshiError("bad_request: 400")))(d)
    # a benign FOK-kill -> KalshiNoFill (the reused split)
    with pytest.raises(L.KalshiNoFill):
        await L.make_place_fn(FakeKalshiClient(
            post_raise=KalshiError("fill_or_kill_insufficient_resting_volume")))(d)


# ── case 5: signal conversion (/positions -> entry CopySignals) ──
def test_signal_conversion_filters_and_maps_stable():
    rows = [FakePos("0xopen", 0, "Toronto Blue Jays", cur=0.5),                 # genuinely open -> kept
            FakePos("0xopen", 0, "Toronto Blue Jays", cur=0.5),                 # DUPLICATE (cid,oidx) -> deduped in-book
            FakePos("0xredeem", 0, "Seattle Mariners", cur=0.5, redeemable=True),  # redeemable -> excluded
            FakePos("0xsettled", 1, "Over", cur=1.0)]                           # curPrice>=1 -> excluded
    sigs = L.positions_to_entry_signals(rows, "0xWHALE")
    assert len(sigs) == 1                                                       # the duplicate did NOT emit a 2nd signal
    s = sigs[0]
    assert s.wallet == "0xWHALE" and s.condition_id == "0xopen" and s.outcome == "Toronto Blue Jays"
    assert s.outcome_index == 0 and s.is_exit is False and s.slug == SLUG
    again = L.positions_to_entry_signals(rows, "0xWHALE")
    assert again[0].signal_id == s.signal_id                                    # restart-STABLE (same position)


def test_market_quote_dict_maps_and_tolerates_missing():
    d = L._market_quote_dict(FakeMarket(T_TOR, yes_ask=0.6, no_ask=0.4, liq=700))
    assert d["yes_ask_dollars"] == 0.6 and d["no_ask_dollars"] == 0.4 and d["liquidity_dollars"] == 700.0
    assert d["yes_bid_dollars"] == 0.53                        # the exit prices off the bid -> it must be mapped
    class _Bare:  # no quote fields -> None (evaluate then skip:no_quote, safe)
        ticker = T_SEA
    assert L._market_quote_dict(_Bare())["yes_ask_dollars"] is None


# ── case 3: DISARMED means NO POST ──
@pytest.mark.asyncio
async def test_disarmed_cycle_places_nothing(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    calls = []
    async def stub(d):
        calls.append(d); return None
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        summ = await L.run_live_arm_gated_cycle(conn, _sub(), [_sig("Toronto Blue Jays", "m1"),
                                                _sig("Seattle Mariners", "m2")], _ctx(), j, NOW,
                                                place_fn=stub, legacy_db_path=leg)   # arm rows ABSENT -> disarmed
    assert summ["n_would_place"] == 2 and summ["placed"] == 0 and summ["n_disarm_blocked"] == 2
    assert summ["posts_sent"] == 0 and calls == []             # the placer was NEVER reached -> ZERO posts


# ── case 1 + 6-chain: ARMED cycle places, records, and the journal coid == the POSTED body coid ──
@pytest.mark.asyncio
async def test_armed_cycle_places_and_journal_coid_matches_posted_body(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    fake = FakeKalshiClient()
    place_fn = L.make_place_fn(fake)
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        summ = await L.run_live_arm_gated_cycle(conn, _sub(), [_sig("Toronto Blue Jays", "m1")], _ctx(), j, NOW,
                                                place_fn=place_fn, legacy_db_path=leg)
        rows = conn.execute("SELECT client_order_id, ticker, order_side, outcome_leg, fill_count, dry_run, "
                            "outcome_status FROM pm_subdivision_order").fetchall()
    assert summ["placed"] == 1 and summ["posts_sent"] == 1 and len(fake.posts) == 1
    posted = fake.posts[0][1]
    assert len(rows) == 1
    r = rows[0]
    assert r["dry_run"] == 0 and r["outcome_status"] == "filled" and r["fill_count"] == 1.0
    assert r["client_order_id"] == posted["client_order_id"]   # ★ journal coid == the coid actually POSTED
    assert r["ticker"] == posted["ticker"] and r["order_side"] == posted["side"]


# ── case 4 (★): the arm state is re-read before EVERY order -- a mid-cycle kill stops the NEXT order ──
@pytest.mark.asyncio
async def test_arm_reread_before_every_order_mid_cycle_kill(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    fake = FakeKalshiClient()
    _place = L.make_place_fn(fake)
    async def kill_after_first(d):
        r = await _place(d)
        arm.disarm(ACCT, CAT, reason="mid_cycle_kill", legacy_db_path=leg)   # kill AFTER the first placement
        return r
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        summ = await L.run_live_arm_gated_cycle(conn, _sub(), [_sig("Toronto Blue Jays", "m1"),
                                                _sig("Seattle Mariners", "m2")], _ctx(), j, NOW,
                                                place_fn=kill_after_first, legacy_db_path=leg)
    assert summ["placed"] == 1 and summ["n_disarm_blocked"] == 1               # the 2nd was blocked by the per-order re-read
    assert len(fake.posts) == 1                                                # exactly ONE POST reached the venue


# ── case 7: the auth-failure + consecutive-error latches ──
@pytest.mark.asyncio
async def test_auth_failure_latches_whole_account_and_stops(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    async def auth_fail(d):
        raise L.OrderPlacementError("kalshi V2 POST rejected: 401 unauthorized")
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        summ = await L.run_live_arm_gated_cycle(conn, _sub(), [_sig("Toronto Blue Jays", "m1"),
                                                _sig("Seattle Mariners", "m2")], _ctx(), j, NOW,
                                                place_fn=auth_fail, legacy_db_path=leg)
    assert summ["placed"] == 0 and summ["errors"] == 1                          # broke after the FIRST auth failure
    row = arm.current_row(ACCT, CAT, legacy_db_path=leg)
    assert row["latched"] is True and row["auto_trigger"] == arm.AUTO_AUTH_FAILURE and row["manual_exit_required"] is True
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False


@pytest.mark.asyncio
async def test_consecutive_errors_latch_after_three(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    async def loud(d):
        raise L.OrderPlacementError("kalshi V2 POST rejected: bad_request 400")
    sigs = [_sig("Toronto Blue Jays", "m%d" % i) for i in range(4)]             # 4 loud errors available
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        summ = await L.run_live_arm_gated_cycle(conn, _sub(), sigs, _ctx(), j, NOW, place_fn=loud, legacy_db_path=leg)
    assert summ["errors"] == 3                                                  # broke at the 3rd consecutive error
    assert arm.current_row(ACCT, CAT, legacy_db_path=leg)["auto_trigger"] == arm.AUTO_CONSECUTIVE_ERRORS


# ── case 2: boot-reconcile latches at boot on mismatch (clean + fetch-failure too) ──
@pytest.mark.asyncio
async def test_boot_reconcile_latches_on_mismatch(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    fake = FakeKalshiClient(positions=[FakeKPos(T_TOR, 2)])                     # Kalshi holds +2; journal EMPTY -> kalshi_only
    with db.connect(p) as conn:
        res = await L.run_boot_reconcile(conn, _sub(), fake, legacy_db_path=leg)
    assert res.reconciled is False and res.latched is True
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False                 # DISARMED by the boot-reconcile latch
    assert arm.current_row(ACCT, CAT, legacy_db_path=leg)["auto_trigger"] == arm.AUTO_BOOT_RECONCILE


@pytest.mark.asyncio
async def test_boot_reconcile_clean_when_matches(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_subdivision_order (account_id,category,ticker,outcome_leg,is_exit,fill_count,"
                     "outcome_status,dry_run,response_ts) VALUES (?,?,?,?,0,2,'filled',0,?)",
                     (ACCT, CAT, T_TOR, "yes", NOW)); conn.commit()             # journal: +2 YES on T_TOR
        res = await L.run_boot_reconcile(conn, _sub(), FakeKalshiClient(positions=[FakeKPos(T_TOR, 2)]),
                                         legacy_db_path=leg)
    assert res.reconciled is True and res.latched is False


@pytest.mark.asyncio
async def test_boot_reconcile_fetch_failure_fails_safe_latches(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        res = await L.run_boot_reconcile(conn, _sub(), FakeKalshiClient(pos_raise=RuntimeError("get_positions 503")),
                                         legacy_db_path=leg)
    assert res.reconciled is False and res.latched is True and res.read_error is not None


# ── case 1 (end-to-end): the bounded scheduled loop, DISARMED -> ZERO real POSTs, no exception ──
@pytest.mark.asyncio
async def test_scheduled_loop_bounded_disarmed_zero_posts(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    import time
    with db.connect(p) as conn:                                                # seed the money-layer rows the loop reads
        conn.execute("INSERT OR IGNORE INTO pm_account(account_id,venue,secret_ref,label,active,created_ts) "
                     "VALUES(?, 'kalshi','KALSHI','Jack',1,?)", (ACCT, int(time.time())))
        conn.execute("INSERT OR IGNORE INTO pm_subdivision(account_id,category,market_types,sizing_mode,"
                     "fixed_stake_usd,active,created_ts) VALUES(?,?,'moneyline,total,spread','fixed',5.0,1,?)",
                     (ACCT, CAT, int(time.time())))
        conn.execute("INSERT OR IGNORE INTO pm_subdivision_attachment(account_id,category,wallet,active,source,added_ts) "
                     "VALUES(?,?,?,1,'promote_to_live',?)", (ACCT, CAT, "0xWHALE", int(time.time())))
        conn.commit()
    fake = FakeKalshiClient(positions=[], game_markets=[FakeMarket(T_TOR), FakeMarket(T_SEA)])
    broker = FakeBroker(fake)
    pos_client = FakePositionsClient(FakeBook([FakePos("0xopen", 0, "Toronto Blue Jays", cur=0.5)]))
    await L.scheduled_pm_live_loop(p, broker, pos_client, account_id=ACCT, category=CAT, poll_sec=0,
                                   legacy_db_path=leg, _max_cycles=1)           # DISARMED (no arm rows)
    assert fake.posts == []                                                     # ZERO real POSTs (boot-reconcile clean + disarmed cycle)
    with db.connect(p) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_order").fetchone()[0] == 0   # nothing placed


# ── STRUCTURAL: this module holds NO broker object and cannot rebuild/place ──
def test_structural_no_broker_object_no_rebuild():
    for banned in ("KalshiLiveBroker", "KalshiBroker", "place_order", "build_v2_event_order"):
        assert banned not in dir(L), banned                                    # no broker, and it does NOT rebuild the body
    sig = inspect.signature(L.make_place_fn).parameters
    assert "client" in sig                                                     # the POST client is injected
    sig2 = inspect.signature(L.scheduled_pm_live_loop).parameters
    assert "positions_client" in sig2                                          # the /positions client is injected (no global)


# ── adversarial-review FIX (HIGH): a transport error (httpx timeout/connect) -> OrderPlacementError, not an escape ──
@pytest.mark.asyncio
async def test_transport_error_maps_to_OrderPlacementError_and_latches(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        d = _would_place_decision(conn)
    class _Boom(Exception):                                                    # a NON-KalshiError transport failure
        pass
    with pytest.raises(L.OrderPlacementError):                                 # make_place_fn wraps it (possibly-placed)
        await L.make_place_fn(FakeKalshiClient(post_raise=_Boom("read timeout")))(d)
    # in the cycle a transport error counts toward the consecutive-error latch (never escapes to the never-die loop)
    _arm_both(leg)
    async def timeout(dd):
        raise L.OrderPlacementError("kalshi V2 POST TRANSPORT error -- POSSIBLY PLACED: ReadTimeout()")
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        summ = await L.run_live_arm_gated_cycle(conn, _sub(), [_sig("Toronto Blue Jays", "m%d" % i) for i in range(4)],
                                                _ctx(), j, NOW, place_fn=timeout, legacy_db_path=leg)
    assert summ["errors"] == 3 and summ["placed"] == 0
    assert arm.current_row(ACCT, CAT, legacy_db_path=leg)["auto_trigger"] == arm.AUTO_CONSECUTIVE_ERRORS


# ── adversarial-review FIX (HIGH): PENDING-first journals the coid BEFORE the POST -> a failure cannot RE-DRIVE it ──
@pytest.mark.asyncio
async def test_pending_row_journaled_pre_post_prevents_redrive(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    async def loud(dd):
        raise L.OrderPlacementError("kalshi V2 POST rejected: bad_request 400")
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], NOW)
        await L.run_live_arm_gated_cycle(conn, _sub(), [_sig("Toronto Blue Jays", "m1")], _ctx(), j, NOW,
                                         place_fn=loud, legacy_db_path=leg)
        rows = conn.execute("SELECT client_order_id, outcome_status, dry_run FROM pm_subdivision_order").fetchall()
        assert len(rows) == 1 and rows[0]["dry_run"] == 0 and rows[0]["outcome_status"] == "error"  # coid JOURNALED despite the fail
        j2 = ex.Journal(conn, [ACCT], NOW)                                      # a fresh Journal ('restart') sees the coid
        assert j2.already_placed(rows[0]["client_order_id"]) is True            # -> gate-4 dedup: the same signal will NOT re-POST


# ── adversarial-review FIX (MEDIUM): a boot-reconcile RAISE (our own DB fault) FORCE-latches -> loop cannot place armed ──
@pytest.mark.asyncio
async def test_boot_reconcile_raise_force_latches(tmp_path):
    import time
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)                                                              # ARM, to prove the fault DISARMS it
    with db.connect(p) as conn:
        conn.execute("INSERT OR IGNORE INTO pm_account(account_id,venue,secret_ref,label,active,created_ts) "
                     "VALUES(?, 'kalshi','KALSHI','Jack',1,?)", (ACCT, int(time.time())))
        conn.execute("INSERT OR IGNORE INTO pm_subdivision(account_id,category,market_types,sizing_mode,"
                     "fixed_stake_usd,active,created_ts) VALUES(?,?,'moneyline','fixed',5.0,1,?)", (ACCT, CAT, int(time.time())))
        conn.execute("DROP TABLE pm_subdivision_order"); conn.commit()         # induce a JOURNAL-read fault at boot-reconcile
    fake = FakeKalshiClient(positions=[], game_markets=[FakeMarket(T_TOR)])
    await L.scheduled_pm_live_loop(p, FakeBroker(fake), FakePositionsClient(FakeBook([])),
                                   account_id=ACCT, category=CAT, poll_sec=0, legacy_db_path=leg, _max_cycles=1)
    row = arm.current_row(ACCT, CAT, legacy_db_path=leg)                        # the fault force-latched -> the armed sub is DISARMED
    assert row["latched"] is True and row["auto_trigger"] == arm.AUTO_BOOT_RECONCILE
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False and fake.posts == []


# ── Option D R-D3: the scheduled loop DETECTS a whale exit (/positions reduction + /activity SELL) and PLACES it ──
def _prow(cid, oidx, size, outcome="Toronto Blue Jays", cur=0.5, slug=SLUG):
    return PositionRow.from_api({"proxyWallet": "0xWHALE", "conditionId": cid, "size": size, "slug": slug,
                                 "outcome": outcome, "outcomeIndex": oidx, "curPrice": cur, "redeemable": False,
                                 "avgPrice": 0.5})


def _arow(cid, oidx, side, ts, tx, typ="TRADE"):
    return ActivityRow.from_api({"proxyWallet": "0xWHALE", "conditionId": cid, "outcomeIndex": oidx, "side": side,
                                 "timestamp": ts, "transactionHash": tx, "type": typ, "size": 5.0, "slug": SLUG,
                                 "outcome": "Toronto Blue Jays"})


class FakeMultiCyclePositionsClient:
    """Returns `books[cycle]` (fetch_positions_book is called once/cycle for one whale) so the position can SHRINK
    across cycles; fetch_activity returns the confirming SELL. Mirrors PolymarketDataAPIClient's two methods."""
    def __init__(self, books, activity):
        self._books = list(books); self._call = 0; self._activity = activity
    async def fetch_positions_book(self, wallet):
        b = self._books[min(self._call, len(self._books) - 1)]; self._call += 1; return b
    async def fetch_activity(self, wallet, **kw):
        return list(self._activity)


def _seed_money_and_hold(conn, held=5):
    import time as _t
    conn.execute("INSERT OR IGNORE INTO pm_account(account_id,venue,secret_ref,label,active,created_ts) "
                 "VALUES(?, 'kalshi','KALSHI','Jack',1,?)", (ACCT, int(_t.time())))
    conn.execute("INSERT OR IGNORE INTO pm_subdivision(account_id,category,market_types,sizing_mode,"
                 "fixed_stake_usd,active,created_ts) VALUES(?,?,'moneyline,total,spread','fixed',5.0,1,?)",
                 (ACCT, CAT, int(_t.time())))
    conn.execute("INSERT OR IGNORE INTO pm_subdivision_attachment(account_id,category,wallet,active,source,added_ts) "
                 "VALUES(?,?,'0xWHALE',1,'promote_to_live',?)", (ACCT, CAT, int(_t.time())))
    conn.execute("INSERT INTO pm_subdivision_order (account_id,category,wallet,ticker,outcome_leg,is_exit,fill_count,"
                 "outcome_status,dry_run,submitted_ts,response_ts) VALUES (?,?,'0xWHALE',?,?,0,?,'filled',0,?,?)",
                 (ACCT, CAT, T_TOR, "yes", held, NOW, NOW))       # wallet 0xWHALE = the attachment the exit copies
    conn.commit()


@pytest.mark.asyncio
async def test_scheduled_loop_detects_and_places_a_whale_exit(tmp_path):
    """R-D3 REAL-PATH (the standing lens: a suite that never runs the armed exit path proves nothing). Cycle 1 the
    whale HOLDS (0xopen size 5) -> the loop seeds its /positions snapshot; cycle 2 the position VANISHES and
    /activity shows a SELL -> the loop DETECTS the confirmed exit and (ARMED) PLACES a reduce_only FULL close of our
    net-open holding (5), on the held leg, via a real POST."""
    import time
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    with db.connect(p) as conn:
        _seed_money_and_hold(conn, held=5)                                       # a REAL +5 YES holding to close
    now = int(time.time())
    pos_client = FakeMultiCyclePositionsClient([FakeBook([_prow("0xopen", 0, 5.0)]), FakeBook([])],
                                               [_arow("0xopen", 0, "SELL", now, "0xtxSELL")])
    fake = FakeKalshiClient(positions=[FakeKPos(T_TOR, 5)],                       # boot-reconcile: journal +5 == kalshi +5 (clean)
                            game_markets=[FakeMarket(T_TOR), FakeMarket(T_SEA)])
    await L.scheduled_pm_live_loop(p, FakeBroker(fake), pos_client, account_id=ACCT, category=CAT,
                                   poll_sec=0, legacy_db_path=leg, _max_cycles=2)
    exit_posts = [b for (_pth, b) in fake.posts if b.get("reduce_only")]         # the exit is reduce_only
    assert len(exit_posts) == 1 and exit_posts[0]["ticker"] == T_TOR and exit_posts[0]["count"] == "5"
    assert exit_posts[0]["side"] == "ask"                                        # sell YES to close = ask
    assert exit_posts[0]["price"] == "0.5100"                                    # ★ marketable: yes_bid 0.53 - slip 0.02
    with db.connect(p) as conn:
        r = conn.execute("SELECT is_exit, outcome_status, fill_count FROM pm_subdivision_order "
                         "WHERE is_exit=1 AND dry_run=0").fetchone()
    assert r is not None and r["outcome_status"] == "filled"                     # the exit was journaled as an is_exit fill


@pytest.mark.asyncio
async def test_scheduled_loop_disarmed_does_not_place_the_exit(tmp_path):
    """The same detection, DISARMED -> the exit is DETECTED but NEVER placed (off is off, at the loop level)."""
    import time
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)   # NO _arm_both -> disarmed
    with db.connect(p) as conn:
        _seed_money_and_hold(conn, held=5)
    now = int(time.time())
    pos_client = FakeMultiCyclePositionsClient([FakeBook([_prow("0xopen", 0, 5.0)]), FakeBook([])],
                                               [_arow("0xopen", 0, "SELL", now, "0xtxSELL")])
    fake = FakeKalshiClient(positions=[FakeKPos(T_TOR, 5)], game_markets=[FakeMarket(T_TOR), FakeMarket(T_SEA)])
    await L.scheduled_pm_live_loop(p, FakeBroker(fake), pos_client, account_id=ACCT, category=CAT,
                                   poll_sec=0, legacy_db_path=leg, _max_cycles=2)
    assert fake.posts == []                                                      # nothing placed while disarmed
    with db.connect(p) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_order WHERE is_exit=1").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_scheduled_loop_places_a_NO_leg_whale_exit(tmp_path):
    """R-D3 NO-LEG (standing lens #1, through the full loop): an Under (NO) total position vanishes + a SELL
    confirms -> the loop places a reduce_only NO exit = side 'bid', priced marketable off the NO bid (1 - yes_ask).
    NO-leg exits are only reachable after a NO ENTRY (which trips the standing NO-leg STOP), but the WIRING +
    total-index match + NO-bid derivation must be correct end-to-end. (Also exercises a NEGATIVE position_fp on the
    boot-reconcile side.)"""
    import time
    T_TOTAL = "KXMLBTOTAL-26AUG281915SEATOR-9"; TOTAL_SLUG = "mlb-sea-tor-2026-08-28-total-8pt5"
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    with db.connect(p) as conn:
        conn.execute("INSERT OR IGNORE INTO pm_account(account_id,venue,secret_ref,label,active,created_ts) "
                     "VALUES(?, 'kalshi','KALSHI','Jack',1,?)", (ACCT, int(time.time())))
        conn.execute("INSERT OR IGNORE INTO pm_subdivision(account_id,category,market_types,sizing_mode,"
                     "fixed_stake_usd,active,created_ts) VALUES(?,?,'moneyline,total,spread','fixed',5.0,1,?)",
                     (ACCT, CAT, int(time.time())))
        conn.execute("INSERT OR IGNORE INTO pm_subdivision_attachment(account_id,category,wallet,active,source,added_ts) "
                     "VALUES(?,?,'0xWHALE',1,'promote_to_live',?)", (ACCT, CAT, int(time.time())))
        conn.execute("INSERT INTO pm_subdivision_order (account_id,category,wallet,ticker,outcome_leg,is_exit,fill_count,"
                     "outcome_status,dry_run,submitted_ts,response_ts) VALUES (?,?,'0xWHALE',?,'no',0,4,'filled',0,?,?)",
                     (ACCT, CAT, T_TOTAL, NOW, NOW))                              # a REAL -4 NO holding (wallet 0xWHALE) to close
        conn.commit()
    now = int(time.time())
    pos_client = FakeMultiCyclePositionsClient(
        [FakeBook([_prow("0xunder", 1, 4.0, outcome="Under", slug=TOTAL_SLUG)]), FakeBook([])],
        [_arow("0xunder", 1, "SELL", now, "0xtxU")])
    fake = FakeKalshiClient(positions=[FakeKPos(T_TOTAL, -4)],                    # boot-reconcile: journal -4 (NO) == kalshi -4
                            game_markets=[FakeMarket(T_TOR), FakeMarket(T_SEA)],  # resolves the total's anchor game
                            total_markets=[FakeMarket(T_TOTAL, yes_ask=0.52, no_ask=0.50, yes_bid=0.50)])
    await L.scheduled_pm_live_loop(p, FakeBroker(fake), pos_client, account_id=ACCT, category=CAT,
                                   poll_sec=0, legacy_db_path=leg, _max_cycles=2)
    exit_posts = [b for (_pth, b) in fake.posts if b.get("reduce_only")]
    assert len(exit_posts) == 1 and exit_posts[0]["ticker"] == T_TOTAL and exit_posts[0]["count"] == "4"
    assert exit_posts[0]["side"] == "bid"                                        # sell NO -> buy-YES side = bid
    assert exit_posts[0]["price"] == "0.5400"                                    # marketable: yes_ask 0.52 + slip 0.02


# ── R-d2: the BOOT settlement-scan books a settled-while-down position -> boot_reconcile comes up CLEAN ──
@pytest.mark.asyncio
async def test_boot_settlement_scan_books_cubs_then_reconcile_is_clean(tmp_path):
    """R-d2 -- THE COMBINED-DEPLOY PROOF at the driver level. A position that SETTLED WHILE THE ENGINE WAS DOWN
    (Cubs: journal +1 YES, venue FLAT since settlement) is booked by the BOOT settlement-scan BEFORE boot_reconcile
    -> reconcile comes up CLEAN and the sub STAYS ARMED. Without R-d this exact restart would latch R-b."""
    import time
    CUBS = "KXMLBGAME-26AUG301920CINCHC-CHC"
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    with db.connect(p) as conn:
        conn.execute("INSERT OR IGNORE INTO pm_account(account_id,venue,secret_ref,label,active,created_ts) "
                     "VALUES(?, 'kalshi','KALSHI','Jack',1,?)", (ACCT, int(time.time())))
        conn.execute("INSERT OR IGNORE INTO pm_subdivision(account_id,category,market_types,sizing_mode,"
                     "fixed_stake_usd,active,created_ts) VALUES(?,?,'moneyline,total,spread','fixed',5.0,1,?)",
                     (ACCT, CAT, int(time.time())))
        conn.execute("INSERT INTO pm_subdivision_order (account_id,category,wallet,ticker,outcome_leg,is_exit,"
                     "fill_count,fill_price,fee,outcome_status,dry_run,submitted_ts,response_ts) "
                     "VALUES (?,?,'0xWHALE',?,'yes',0,1,0.60,0.0084,'filled',0,?,?)", (ACCT, CAT, CUBS, NOW, NOW))
        conn.commit()                                                            # the live Cubs holding (journal +1)
    settlements_raw = {"settlements": [{"ticker": CUBS, "event_ticker": "KXMLBGAME-26AUG301920CINCHC",
                                        "market_result": "no", "settled_time": "2026-08-31T02:44:41Z", "revenue": 0}]}
    fake = FakeKalshiClient(positions=[], settlements_raw=settlements_raw,       # venue FLAT (Cubs settled + gone)
                            game_markets=[FakeMarket(T_TOR)])
    await L.scheduled_pm_live_loop(p, FakeBroker(fake), FakePositionsClient(FakeBook([])),
                                   account_id=ACCT, category=CAT, poll_sec=0, legacy_db_path=leg, _max_cycles=1)
    with db.connect(p) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pm_subdivision_order WHERE close_source='settlement'").fetchone()[0] == 1
        assert BR.journal_signed_positions(conn, ACCT) == {}                     # journal flat on Cubs after booking
    row = arm.current_row(ACCT, CAT, legacy_db_path=leg)
    assert (row is None or not row.get("latched"))                              # NOT latched (reconcile was CLEAN)
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is True                  # still armed -- the deploy comes up trading
