"""Stage 3 R5 -- ARM / KILL-SWITCH. Proves: cold-start DISARMED (absent state), global AND sub both
required, persisted disarm survives a 'restart', each auto-disarm trigger latches, a latched row needs a
human arm to clear, the chokepoint reads the REAL arm state, a disarmed dry-run places NOTHING, an armed
cycle reaches the (stubbed) placer once per copy and honours a MID-CYCLE kill, and -- the design ruling --
a DISARM blocks an EXIT even though the exit-exempt budget gates pass. Structurally still no broker.

Arm state lives in the LEGACY agent_state (a temp copy here); nothing real is placed (place_fn is a stub)."""
import inspect
import sqlite3

import pytest

from trading_corp.prediction_markets import arm, db, execution as ex
from trading_corp.data import mlb_poly_kalshi_match as M

GAME_TICKERS = ["KXMLBGAME-26AUG281915SEATOR-SEA", "KXMLBGAME-26AUG281915SEATOR-TOR"]
TOTAL_TICKERS = ["KXMLBTOTAL-26AUG281915SEATOR-9", "KXMLBTOTAL-26AUG281915SEATOR-8"]
SPREAD_TICKERS = ["KXMLBSPREAD-26AUG281915SEATOR-TOR2", "KXMLBSPREAD-26AUG281915SEATOR-SEA2"]
MARKETS = {
    "KXMLBGAME-26AUG281915SEATOR-TOR": {"yes_ask_dollars": 0.55, "yes_bid_dollars": 0.53, "no_ask_dollars": 0.47, "liquidity_dollars": 500},
    "KXMLBGAME-26AUG281915SEATOR-SEA": {"yes_ask_dollars": 0.47, "yes_bid_dollars": 0.45, "no_ask_dollars": 0.55, "liquidity_dollars": 500},
    "KXMLBTOTAL-26AUG281915SEATOR-9":  {"yes_ask_dollars": 0.52, "yes_bid_dollars": 0.50, "no_ask_dollars": 0.50, "liquidity_dollars": 500},
    "KXMLBSPREAD-26AUG281915SEATOR-TOR2": {"yes_ask_dollars": 0.40, "yes_bid_dollars": 0.38, "no_ask_dollars": 0.62, "liquidity_dollars": 500},
}
ACCT, CAT = "kalshi_jack", "mlb"


def _legacy(tmp_path):
    """A temp LEGACY DB with just the agent_state table (the exact engine DDL)."""
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
    base = dict(account_id=ACCT, category=CAT, market_types=("moneyline", "total", "spread"),
                sizing_mode="fixed", fixed_stake_usd=5.0, per_order_usd_cap=25.0, daily_usd_cap=50.0,
                max_open_usd=100.0, max_orders_per_day=25, max_slippage_cents=2)
    base.update(over)
    return ex.SubConfig(**base)


def _sig(slug, outcome, sid="s1", is_exit=False, wallet="0x16bb9951a36fce71e2ef57890b786145e0ba8492"):
    return ex.CopySignal(wallet=wallet, slug=slug, outcome=outcome, condition_id="0xcond_" + sid,
                         outcome_index=0, signal_id=sid, is_exit=is_exit)


def _arm_both(leg):
    # a test helper that FORCE-arms to a known state (acknowledges any prior latch -- like the CLI's
    # --clear-latch); the structural latch guard is exercised explicitly in the latch test below.
    arm.arm(global_=True, require_latch_clear=True, legacy_db_path=leg)
    arm.arm(ACCT, CAT, require_latch_clear=True, legacy_db_path=leg)


# ── the default is DISARMED (the inversion) ──────────────────────────────────
def test_cold_start_absent_state_is_disarmed(tmp_path):
    leg = _legacy(tmp_path)
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False       # absent rows -> disarmed
    v = arm.read_arm_verdict(ACCT, CAT, legacy_db_path=leg)
    assert v.armed is False and v.scope == "global" and v.reason == "absent_global"
    # a MISSING legacy DB file also reads as disarmed (never fails closed into 'armed')
    assert arm.is_armed(ACCT, CAT, legacy_db_path=str(tmp_path / "does_not_exist.db")) is False


def test_requires_global_AND_sub(tmp_path):
    leg = _legacy(tmp_path)
    arm.arm(global_=True, legacy_db_path=leg)
    assert arm.read_arm_verdict(ACCT, CAT, legacy_db_path=leg).scope == "sub"   # sub still off
    arm.disarm(global_=True, legacy_db_path=leg)
    arm.arm(ACCT, CAT, legacy_db_path=leg)
    assert arm.read_arm_verdict(ACCT, CAT, legacy_db_path=leg).scope == "global"  # global off dominates
    _arm_both(leg)
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is True


def test_disarm_either_scope_kills(tmp_path):
    leg = _legacy(tmp_path)
    _arm_both(leg)
    arm.disarm(global_=True, reason="master_kill", legacy_db_path=leg)
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False
    arm.arm(global_=True, legacy_db_path=leg)                          # re-arm master
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is True
    arm.disarm(ACCT, CAT, reason="sub_kill", legacy_db_path=leg)
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False


def test_persisted_disarm_survives_restart(tmp_path):
    """Persistence is on-disk: a fresh (stateless) read after a write is exactly a 'restart' read."""
    leg = _legacy(tmp_path)
    _arm_both(leg)
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is True         # a fresh read post-write sees armed
    arm.disarm(ACCT, CAT, legacy_db_path=leg)
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False        # and a fresh read sees the disarm


# ── auto-disarm: the four latching triggers (legacy has none of the first three) ─────────────
def test_each_auto_disarm_trigger_latches(tmp_path):
    leg = _legacy(tmp_path)
    _arm_both(leg)
    arm.latch_consecutive_errors(ACCT, CAT, n=3, legacy_db_path=leg)
    row = arm.current_row(ACCT, CAT, legacy_db_path=leg)
    assert row["armed"] is False and row["latched"] is True and row["auto_trigger"] == arm.AUTO_CONSECUTIVE_ERRORS

    _arm_both(leg)
    arm.latch_count_ceiling(ACCT, CAT, count=25, cap=25, legacy_db_path=leg)
    assert arm.current_row(ACCT, CAT, legacy_db_path=leg)["auto_trigger"] == arm.AUTO_COUNT_CEILING

    _arm_both(leg)
    arm.latch_boot_reconcile_mismatch(ACCT, CAT, legacy_db_path=leg)
    assert arm.current_row(ACCT, CAT, legacy_db_path=leg)["auto_trigger"] == arm.AUTO_BOOT_RECONCILE

    _arm_both(leg)
    arm.latch_auth_failure(ACCT, [CAT, "nba"], detail="401 on POST", legacy_db_path=leg)   # disarms EVERY cat on the account
    for c in (CAT, "nba"):
        r = arm.current_row(ACCT, c, legacy_db_path=leg)
        assert r["armed"] is False and r["latched"] is True and r["auto_trigger"] == arm.AUTO_AUTH_FAILURE
        assert r["manual_exit_required"] is True                       # flag open positions for MANUAL exit


def test_latched_arm_is_structurally_guarded_not_cli_only(tmp_path):
    """A latched auto-disarm can be cleared ONLY by arm(require_latch_clear=True) -- the guard lives in
    arm.py, so an engine-side caller that forgets the ack FAILS LOUD, never silently re-arms."""
    leg = _legacy(tmp_path)
    _arm_both(leg)
    arm.latch_auth_failure(ACCT, [CAT], legacy_db_path=leg)
    assert arm.current_row(ACCT, CAT, legacy_db_path=leg)["latched"] is True
    # a direct (engine-side) arm WITHOUT the ack RAISES -- it cannot silently clear the latch
    with pytest.raises(arm.LatchedError):
        arm.arm(ACCT, CAT, by="bug", legacy_db_path=leg)
    assert arm.is_armed(ACCT, CAT, legacy_db_path=leg) is False        # still disarmed after the refused arm
    # WITH the ack (the CLI's --clear-latch), the human clears it
    arm.arm(ACCT, CAT, by="jack", require_latch_clear=True, legacy_db_path=leg)
    row = arm.current_row(ACCT, CAT, legacy_db_path=leg)
    assert row["armed"] is True and row["latched"] is False and row["auto_trigger"] is None


def test_manual_disarm_preserves_an_existing_latch(tmp_path):
    """A manual disarm on top of an auth-failure latch keeps the latch (invariant: only an acknowledged
    arm clears it) -- so you cannot sidestep the ack by disarm-then-arm."""
    leg = _legacy(tmp_path)
    _arm_both(leg)
    arm.latch_auth_failure(ACCT, [CAT], legacy_db_path=leg)
    arm.disarm(ACCT, CAT, reason="operator_also_disarms", legacy_db_path=leg)
    row = arm.current_row(ACCT, CAT, legacy_db_path=leg)
    assert row["armed"] is False and row["latched"] is True and row["manual_exit_required"] is True
    with pytest.raises(arm.LatchedError):                             # still needs the ack to arm
        arm.arm(ACCT, CAT, legacy_db_path=leg)
    # a manual disarm of a NON-latched scope stays non-latched (arms freely afterwards)
    arm.disarm("kalshi_jack", "nba", legacy_db_path=leg)
    assert arm.current_row("kalshi_jack", "nba", legacy_db_path=leg)["latched"] is False
    arm.arm("kalshi_jack", "nba", legacy_db_path=leg)                 # no latch -> no ack needed


# ── the chokepoint reads the REAL arm state ─────────────────────────────────
def test_evaluate_reads_real_arm_state(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], 1787900000)
        _arm_both(leg)
        d_on = ex.evaluate(_sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays"), _sub(), _ctx(), j, conn,
                           1787900000, legacy_db_path=leg)
        arm.disarm(ACCT, CAT, legacy_db_path=leg)
        d_off = ex.evaluate(_sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays"), _sub(), _ctx(), j, conn,
                            1787900000, legacy_db_path=leg)
    assert d_on.disarm_armed is True and d_off.disarm_armed is False   # the recorded verdict is now REAL


# ── the arm-gated cycle: disarmed places nothing; armed reaches the stub once per copy ───────
def _wp_signals():
    return [_sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays", sid="m"),
            _sig("mlb-sea-tor-2026-08-28-total-8pt5", "Under", sid="t"),
            _sig("mlb-sea-tor-2026-08-28-spread-home-1pt5", "Seattle Mariners", sid="s"),
            _sig("mlb-sea-tor-2020-01-01", "Toronto Blue Jays", sid="oow")]        # 3 would-place, 1 skip


def test_disarmed_full_cycle_places_nothing(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    calls = []
    with db.connect(p) as conn:                                        # arm rows ABSENT -> disarmed
        summ = ex.run_arm_gated_cycle(conn, _sub(), _wp_signals(), _ctx(), 1787900000,
                                      place_fn=lambda d: calls.append(d), legacy_db_path=leg)
    assert summ["n_would_place"] == 3 and summ["placements_attempted"] == 0 and summ["n_disarm_blocked"] == 3
    assert summ["posts_sent"] == 0 and calls == []                     # NOTHING reached the placer


def test_armed_reaches_stub_once_per_copy(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    calls = []
    with db.connect(p) as conn:
        summ = ex.run_arm_gated_cycle(conn, _sub(), _wp_signals(), _ctx(), 1787900000,
                                      place_fn=lambda d: calls.append(d), legacy_db_path=leg)
    assert summ["placements_attempted"] == 3 and len(calls) == 3 and summ["n_disarm_blocked"] == 0
    assert summ["posts_sent"] == 0                                     # a STUB placer -- still zero real posts


def test_mid_cycle_kill_stops_the_next_order(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    calls = []

    def kill_after_first(d):
        calls.append(d)
        arm.disarm(ACCT, CAT, reason="mid_cycle_kill", legacy_db_path=leg)   # kill lands mid-cycle
    with db.connect(p) as conn:
        summ = ex.run_arm_gated_cycle(conn, _sub(), _wp_signals(), _ctx(), 1787900000,
                                      place_fn=kill_after_first, legacy_db_path=leg)
    assert summ["placements_attempted"] == 1 and len(calls) == 1       # only the FIRST placed; re-read stopped the rest
    assert summ["n_disarm_blocked"] == 2


# ── THE DESIGN RULING: disarm blocks an EXIT even though the exit-exempt gates pass ─────────
def test_disarm_blocks_exit_though_exit_exempt_gates_pass(tmp_path):
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    sub = _sub(daily_usd_cap=1.0)                                      # a cap that rejects any ENTRY
    exit_sig = _sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays", sid="x", is_exit=True)
    with db.connect(p) as conn:
        j = ex.Journal(conn, [ACCT], 1787900000)
        # (a) an ENTRY is rejected by the daily cap; (b) the EXIT is exit-EXEMPT -> passes evaluate
        d_entry = ex.evaluate(_sig("mlb-sea-tor-2026-08-28", "Toronto Blue Jays", sid="e"), sub, _ctx(), j,
                              conn, 1787900000, legacy_db_path=leg)
        d_exit = ex.evaluate(exit_sig, sub, _ctx(), j, conn, 1787900000, legacy_db_path=leg)
        assert d_entry.status == "reject:daily_cap"
        assert d_exit.status == "dry_run_would_place" and d_exit.is_exit is True   # gates 5/6/8 do NOT block the exit

        # (c) DISARMED: the exit does NOT reach the placer (off is off -- human flattens by hand)
        blocked = []
        summ_off = ex.run_arm_gated_cycle(conn, sub, [exit_sig], _ctx(), 1787900000,
                                          place_fn=lambda d: blocked.append(d), legacy_db_path=leg)
        assert summ_off["placements_attempted"] == 0 and blocked == []
        # (d) ARMED: the same exit DOES reach the placer (you must be able to close when the engine is ON)
        _arm_both(leg)
        placed = []
        summ_on = ex.run_arm_gated_cycle(conn, sub, [exit_sig], _ctx(), 1787900000,
                                         place_fn=lambda d: placed.append(d), legacy_db_path=leg)
    assert summ_on["placements_attempted"] == 1 and len(placed) == 1 and placed[0].body.get("reduce_only") is True


# ── structural: the seam is arm-guarded + injected, the module still holds no broker ────────
def test_place_fn_seam_is_injected_not_a_broker(tmp_path):
    assert "place_fn" in inspect.signature(ex.run_arm_gated_cycle).parameters
    assert "broker" not in inspect.signature(ex.run_arm_gated_cycle).parameters
    assert "KalshiLiveBroker" not in dir(ex) and "place_order" not in dir(ex)      # no broker in the namespace
    assert "httpx" not in dir(ex) and "requests" not in dir(ex) and "asyncio" not in dir(ex)
    leg = _legacy(tmp_path); p = str(tmp_path / "pm.db"); db.init_db(p)
    _arm_both(leg)
    with db.connect(p) as conn:                                        # armed but NO placer -> pure dry-run, nothing 'placed'
        summ = ex.run_arm_gated_cycle(conn, _sub(), _wp_signals(), _ctx(), 1787900000, place_fn=None, legacy_db_path=leg)
    assert summ["placements_attempted"] == 0 and summ["posts_sent"] == 0 and summ["n_would_place"] == 3


# ── the manual-exit FLAG surface (auth-failure companion) ────────────────────
def test_open_positions_needing_manual_exit(tmp_path):
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        assert ex.open_positions_needing_manual_exit(conn) == []       # nothing live ever placed (R5)
        conn.execute("INSERT INTO pm_subdivision_order (account_id,category,ticker,outcome_leg,is_exit,"
                     "fill_count,outcome_status,dry_run,response_ts) VALUES (?,?,?,?,0,3,'filled',0,?)",
                     (ACCT, CAT, "KXMLBGAME-26AUG281915SEATOR-TOR", "yes", 1787900000))
        conn.commit()
        need = ex.open_positions_needing_manual_exit(conn)
        assert len(need) == 1 and abs(need[0]["net_open_contracts"] - 3.0) < 1e-9
        conn.execute("INSERT INTO pm_subdivision_order (account_id,category,ticker,outcome_leg,is_exit,"
                     "fill_count,outcome_status,dry_run,response_ts) VALUES (?,?,?,?,1,3,'filled',0,?)",
                     (ACCT, CAT, "KXMLBGAME-26AUG281915SEATOR-TOR", "yes", 1787900001))
        conn.commit()
        assert ex.open_positions_needing_manual_exit(conn) == []        # covered by a filled exit


def test_journal_seed_is_leg_aware_no_leg_not_undercounted(tmp_path):
    """The $163.84 lens on the RESTART/seed path: a previously-placed NO leg must seed the daily/open
    counters at count*(1 - yes_price), NOT count*yes_price -- else NO-leg exposure under-seeds and gates
    5/6 are bypassable after a restart. (The R4 caps bug, surfaced by the R5 review, in the seed query.)"""
    p = str(tmp_path / "pm.db"); db.init_db(p)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_subdivision_order (account_id,category,ticker,outcome_leg,is_exit,"
                     "submitted_count,submitted_price,outcome_status,dry_run,response_ts) "
                     "VALUES (?,?,?,?,0,10,0.48,'filled',0,?)",         # NO leg: cost 10*(1-0.48)=$5.20
                     (ACCT, CAT, "KXMLBTOTAL-26AUG281915SEATOR-9", "no", 1787900000))
        conn.execute("INSERT INTO pm_subdivision_order (account_id,category,ticker,outcome_leg,is_exit,"
                     "submitted_count,submitted_price,outcome_status,dry_run,response_ts) "
                     "VALUES (?,?,?,?,0,9,0.57,'filled',0,?)",          # YES leg: cost 9*0.57=$5.13
                     (ACCT, CAT, "KXMLBGAME-26AUG281915SEATOR-TOR", "yes", 1787900000))
        conn.commit()
        j = ex.Journal(conn, [ACCT], 1787900000)
    assert abs(j.daily_usd(ACCT, CAT) - 10.33) < 1e-6                   # 5.20 + 5.13, NOT the buggy 4.80 + 5.13
    assert abs(j.open_usd(ACCT) - 10.33) < 1e-6


def test_read_status_shape(tmp_path):
    leg = _legacy(tmp_path)
    _arm_both(leg)
    st = arm.read_status(ACCT, CAT, legacy_db_path=leg)
    assert st["global_armed"] is True and st["effective_armed"] is True and st["blocking_scope"] is None
    arm.disarm(global_=True, legacy_db_path=leg)
    st2 = arm.read_status(ACCT, CAT, legacy_db_path=leg)
    assert st2["effective_armed"] is False and st2["blocking_scope"] == "global"
