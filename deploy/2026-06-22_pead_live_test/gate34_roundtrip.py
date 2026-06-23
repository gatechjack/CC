"""PEAD STEP 3 — GATES 3-4 live round-trip (REAL money, tiny). Phased — run ONE
phase at a time AT THE MARKET OPEN with eyes on it. Argument: entry | check | exit.

  entry : ONE small MARKETABLE buy (fills) via the real engine path
          (data_exec.place -> fixed RobinhoodBroker) -> real position on 680725082;
          writes the 6 extra_json keys + paper_trade_record to the PROD DB (so the
          live dashboard renders it); confirms routing to 680725082 from RH's fill.
  check : builds the REAL dashboard view (web/pead_view.build_pead_view) and proves
          its pressures EQUAL the engine's pead_pressures.compute_pressures on this
          position, computed from the SAME live quote — agree-by-construction on the
          wire, not just in tests.
  exit  : sets the stop level above the live price (deliberate trigger) then runs
          the REAL strat.manage() exit engine -> STOP first-match -> market sell to
          680725082 -> pnl-signed close of the paper_trade_record -> the position
          leaves the dashboard book. The real exit logic, NOT a bypass sell.

Writes to the PROD DB (secrets.db_url) on purpose so the deployed /telemetry/pead
shows the position. execution_mode=live + a TEMP strategies.yaml with
auto_execute:true (the Board blessing, scoped to this supervised run) so manage()
actually places. Aborts if bind != 680725082.

Run via the per-phase runners (entry_run.sh / check_run.sh / exit_run.sh).
Env: PEAD_RT_SYMBOL (default F), PEAD_RT_QTY (default 1).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import yaml

ACCOUNT = "680725082"
SYM = os.environ.get("PEAD_RT_SYMBOL", "F").upper()
QTY = int(os.environ.get("PEAD_RT_QTY", "1"))


class _StubLogger:
    def __init__(self):
        self.events, self.proposed = [], []

    def log_event(self, *a, **k):
        self.events.append((a, k))

    def log_proposed_order(self, order):
        self.proposed.append(order)


def _temp_live_config():
    """Copy config/strategies.yaml, flip robinhood_pead.auto_execute -> true (the
    supervised-run Board blessing), write to a temp file, return its path."""
    with open("config/strategies.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("robinhood_pead", {})["auto_execute"] = True
    p = os.path.join(tempfile.gettempdir(), "pead_rt_strategies.yaml")
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f)
    return p


async def _poll_fill(rh_id, fallback):
    """Poll the order until filled; return the real average fill price."""
    import robin_stocks.robinhood as rs
    for _ in range(20):
        try:
            info = rs.orders.get_stock_order_info(rh_id) or {}
        except Exception:  # noqa: BLE001
            info = {}
        state = info.get("state")
        avg = info.get("average_price")
        if state == "filled" and avg:
            return float(avg)
        if state in ("cancelled", "rejected", "failed"):
            print(f"  order ended {state!r} — not filled")
            return None
        await asyncio.sleep(1.5)
    print("  fill poll timed out — order NOT confirmed filled (no record will be written)")
    return None


async def _setup(secrets, *, want_live_cfg):
    from trading_corp.agents.data_exec import DataExecAgent
    from trading_corp.agents.risk import RiskAgent
    from trading_corp.agents.strategies.pead_strategy import PEADStrategy
    from trading_corp.brokers.robinhood import RobinhoodBroker
    from trading_corp.data.earnings_provider import EarningsProvider

    broker = RobinhoodBroker(
        username=secrets.robinhood_username, password=secrets.robinhood_password,
        mfa_secret=secrets.robinhood_mfa_secret, account_filter=ACCOUNT,
    )
    await broker.connect()
    bound = getattr(broker, "_account_number", "")
    print(f"broker bound account = {bound!r}")
    if bound != ACCOUNT:
        raise SystemExit(f"ABORT: bind {bound!r} != {ACCOUNT!r}")
    data_exec = DataExecAgent(_StubLogger(), dry_run=False)
    data_exec.register_broker("robinhood_pead", broker)
    cfg_path = _temp_live_config() if want_live_cfg else "config/strategies.yaml"
    # secrets.db_url is RELATIVE ('sqlite:///data/...') and resolves wrong from
    # this out-of-tree harness (~/pead_branch); PEAD_DB_URL points at the ABSOLUTE
    # prod DB the deployed dashboard reads. (Harness fix, 2026-06-23.)
    db_url = os.environ.get("PEAD_DB_URL") or secrets.db_url
    print(f"db_url = {db_url}")
    strat = PEADStrategy(
        db_url=db_url, risk_agent=RiskAgent(narrator_enabled=False),
        data_exec=data_exec, logger_agent=_StubLogger(),
        earnings_provider=EarningsProvider(api_key=secrets.eodhd_api_key, db_url=db_url),
        strategies_yaml=cfg_path, execution_mode="live",
    )
    return broker, data_exec, strat


# ── PHASE: ENTRY ─────────────────────────────────────────────────────────────
async def _entry(secrets):
    from trading_corp.persistence.models import ProposedOrder
    from trading_corp.agents.strategies import pead_pressures as pp
    from trading_corp.agents.strategies.pead_strategy import PEADStrategy

    broker, data_exec, strat = await _setup(secrets, want_live_cfg=False)
    # Market-open guard (2026-06-23): a MARKET order placed after the regular
    # session queues and would fill UNWATCHED at the next open. Refuse to place
    # unless NYSE is open right now (holiday/half-day aware via market_hours).
    from trading_corp.utils.market_hours import default_calendar
    now = datetime.now(timezone.utc)
    if not default_calendar().is_open_at(now):
        print(f"ABORT: NYSE is CLOSED at {now.isoformat()} — a market order would queue and "
              f"fill unwatched at the next open. Re-run ENTRY during regular hours (9:30-16:00 ET).")
        return
    print(f"market-open check OK ({now.isoformat()})")
    last = float(await broker.quote(SYM))
    print(f"{SYM} last=${last:.2f}  -> MARKET BUY {QTY} share(s) (~${last * QTY:.2f}) on {ACCOUNT}\n")

    bars = strat._fetch_daily_bars(SYM)
    prim = strat._build_primitives(bars, bars[-3].d, last) if bars and len(bars) >= 16 else None
    if prim is None:
        prim = {"entry_atr_14": round(last * 0.03, 4),
                "post_earnings_swing_low": round(last * 0.95, 2),
                "pre_earnings_close": round(last * 0.97, 2),
                "earnings_gap_top": round(last * 1.02, 2),
                "stop_level": round(last * 0.92, 2)}
    extra = {
        "entry_atr_14": prim["entry_atr_14"],
        "post_earnings_swing_low": prim["post_earnings_swing_low"],
        "pre_earnings_close": prim["pre_earnings_close"],
        "earnings_gap_top": prim["earnings_gap_top"],
        "next_earnings_date": None, "entry_sue": 0.0, "name": SYM,
        "entry_reference_price": last, "stop_price": prim["stop_level"],
        "source_signal": "gate34_roundtrip",
    }
    order = ProposedOrder(strategy=PEADStrategy.SLUG, symbol=SYM, side="buy",
                          qty=float(QTY), order_type="market",
                          rationale="GATE3 live round-trip entry", extra=extra)

    fill = await data_exec.place(order, division="robinhood_pead")  # raises on failure
    print("=== ENTRY PLACED (fixed broker) ===")
    print(f"  RH order id = {fill.broker_order_id!r}")
    print(f"  account     = {fill.account!r}   <-- ROUTING (must be 680725082)")
    if str(fill.account) != ACCOUNT:
        print("  ABORT: routed to the WRONG account — STOP.")
        return
    real_price = await _poll_fill(fill.broker_order_id, fallback=last)
    if real_price is None:
        # No confirmed fill (cancelled/rejected/failed, or poll timeout on a
        # queued order). Cancel so nothing fills unwatched, and write NO record
        # (prevents the phantom result-NULL row seen 2026-06-23).
        print("  NOT a confirmed fill — cancelling the order; NO record written.")
        try:
            import robin_stocks.robinhood as rs
            rs.orders.cancel_stock_order(fill.broker_order_id)
            print(f"  cancelled order {fill.broker_order_id}")
        except Exception as e:  # noqa: BLE001
            print(f"  cancel error: {e} — verify/cancel manually ({fill.broker_order_id})")
        return
    print(f"  FILLED at ${real_price:.4f}")

    order.extra["entry_reference_price"] = real_price
    order.execution_mode = "live"
    strat._write_record(order, max_hold_seconds=pp.MAX_HOLD_TRADING_DAYS * 24 * 3600)
    print(f"\n  paper_trade_record written to PROD DB (order_id={order.id})")
    print("  6 extra_json keys:", json.dumps({k: order.extra[k] for k in
          ("entry_atr_14", "post_earnings_swing_low", "pre_earnings_close",
           "earnings_gap_top", "next_earnings_date", "entry_sue")}, default=str))
    print("\n=== ENTRY DONE — real position open on 680725082; dashboard should render it ===")


# ── PHASE: CHECK (dashboard pressures == engine) ─────────────────────────────
async def _check(secrets):
    from trading_corp.web import pead_view as pv

    broker, data_exec, strat = await _setup(secrets, want_live_cfg=False)
    deps = SimpleNamespace(db_url=strat.db_url, data_exec=data_exec)
    view = await pv.build_pead_view(deps)
    book = [b for b in (view.get("book") or []) if b.get("symbol") == SYM and b.get("complete")]
    if not book:
        print(f"dashboard book has no COMPLETE {SYM} position — entry not visible / primitives missing.")
        print("full book:", json.dumps(view.get("book"), default=str)[:800])
        return
    pos = book[0]
    last = pos["last"]
    print(f"=== DASHBOARD view of {SYM} (build_pead_view) ===")
    print(f"  last=${last}  governing={pos['governing']}  fuse={pos['fuse_pct']:.3f}")
    print(f"  dashboard pressures = {json.dumps(pos['pressures'])}")

    # independently recompute the engine pressures from the SAME live quote
    rows = [r for r in pv.query_open_positions(strat.db_url) if r["symbol"] == SYM]
    r = rows[0]
    prim = pv.primitives_from_extra(r["extra"], r["entry_price"])
    opened = pv._parse_date(r.get("opened_ts")) or datetime.now(timezone.utc).date()
    today = datetime.now(timezone.utc).date()
    held = pv.business_days(opened, today)
    nxt = pv._parse_date((r["extra"] or {}).get("next_earnings_date"))
    d2n = pv.business_days(today, nxt) if nxt else None
    eng = pv.compute_pressures(prim, last, held_trading_days=held, days_to_next_earnings=d2n)
    eng_p = {"stop": eng.stop, "drift": eng.drift, "guard": eng.guard, "time": eng.time}
    print(f"\n  engine compute_pressures (same last) = {json.dumps(eng_p)}")
    match = (eng_p == pos["pressures"] and eng.governing == pos["governing"]
             and abs(eng.fuse_pct - pos["fuse_pct"]) < 1e-12)
    print(f"\n=== AGREE-BY-CONSTRUCTION: {'MATCH' if match else 'MISMATCH'} "
          f"(dashboard pressures/governing/fuse == engine on the wire) ===")
    print("  dashboard: https://trading.jacksumner.com/telemetry/pead")


# ── PHASE: EXIT (real manage() stop trigger) ─────────────────────────────────
async def _exit(secrets):
    import robin_stocks.robinhood as rs
    from trading_corp.agents.strategies import pead_pressures as pp
    from trading_corp.persistence import db

    broker, data_exec, strat = await _setup(secrets, want_live_cfg=True)  # auto_execute:true
    rows = strat._open_rows()
    rows = [r for r in rows if r["symbol"] == SYM]
    if not rows:
        print(f"no open {SYM} position in the book — nothing to exit.")
        return
    r = rows[0]
    oid, entry = r["order_id"], r["entry_price"]
    from datetime import timedelta
    last = float(await broker.quote(SYM))
    print(f"{SYM}: entry=${entry:.4f}  last=${last:.4f}")
    extra = dict(r["extra"])
    if entry > last:
        # downside / at a loss: deliberate STOP trigger — stop_level strictly
        # between last and entry (full precision) so last <= stop_level < entry.
        extra["post_earnings_swing_low"] = (last + entry) / 2.0
        trigger, d2n_dbg = "stop", None
    else:
        # in profit: a DOWNSIDE stop can't fire; deliberately trigger the GUARD
        # exit (flatten before earnings) by putting next earnings inside the lead
        # window. Same real manage() path, fires regardless of price; yields a
        # correct pnl-signed close.
        extra["next_earnings_date"] = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
        trigger, d2n_dbg = "guard", 1
    with db.connect(strat.db_url) as conn:
        conn.execute("UPDATE paper_trade_record SET extra_json=? WHERE order_id=? AND result IS NULL",
                     (json.dumps(extra), oid))
    prim = pp.primitives_from_extra(extra, entry)
    pr = pp.compute_pressures(prim, last, held_trading_days=0, days_to_next_earnings=d2n_dbg)
    print(f"  deliberate {trigger.upper()} trigger (entry=${entry:.4f} last=${last:.4f}) -> "
          f"pressures stop={pr.stop:.2f} guard={pr.guard:.2f} governing={pr.governing} "
          f"(expect {trigger} to fire)\n")

    # capture the sell's routing (manage discards the fill's account)
    cap = {}
    _real = rs.orders.order_sell_market

    def _wrap(*a, **k):
        rr = _real(*a, **k)
        cap["raw"] = rr
        return rr

    rs.orders.order_sell_market = _wrap
    try:
        exits, _cadence = await strat.manage(broker)  # REAL exit engine
    finally:
        rs.orders.order_sell_market = _real

    print("=== manage() result (real exit engine) ===")
    print(f"  fired exits: {[(o.symbol, o.extra.get('exit_reason')) for o in exits]}")
    sell_acct = str((cap.get('raw') or {}).get('account', '')).rstrip('/').rsplit('/', 1)[-1]
    print(f"  sell routed to account = {sell_acct!r}   (must be 680725082)")
    with db.connect(strat.db_url) as conn:
        row = conn.execute("SELECT result, result_price, actual_pnl_dollars FROM "
                           "paper_trade_record WHERE order_id=?", (oid,)).fetchone()
    print(f"  paper_trade_record: result={row['result']!r} result_price={row['result_price']} "
          f"pnl=${row['actual_pnl_dollars']}")
    print("\n=== EXIT DONE — position closed via the REAL manage() stop path; "
          "dashboard book should no longer show it ===")


async def _amain() -> int:
    from trading_corp.utils.secrets import load_secrets
    phase = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    secrets = load_secrets()
    if phase == "entry":
        await _entry(secrets)
    elif phase == "check":
        await _check(secrets)
    elif phase == "exit":
        await _exit(secrets)
    else:
        print("usage: gate34_roundtrip.py {entry|check|exit}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
