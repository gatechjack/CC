#!/usr/bin/env python3
"""MACE Phase-0 capability probe — ARCHIVAL RECORD (plan Phase 0, executed 2026-08-09).

Committed for the record per planning/mace_v1_plan.md § Phase 1. This is the
consolidation of the two operator-run probe scripts (stage A read-only battery +
stage B the one unfillable GTC test order) that were streamed to the prod venv
python over SSH (robin_stocks stored session; NOT run in local pytest — it needs
prod creds + prod SDK). Broker/Tasty/yfinance imports live INSIDE the functions
so this module imports cleanly anywhere and never disturbs test collection.

=========================================================================
MARKETABILITY DIRECTION (Board ruling 1 — pinned so the inversion that this
ruling caught can NEVER recur in the entry/exit ladder implementations):
  NET-CREDIT order: LOWER limit = more marketable. The limit is the MINIMUM
    credit you will accept; a $0.01 credit limit is instantly marketable and
    FILLS. A resting/unfillable credit order needs a HIGH limit (e.g. 0.95 x
    width). The entry ladder walks the credit limit DOWN toward marketability
    (mid - 0.02, then -$0.01/attempt), never below the 0.30 x width floor.
  NET-DEBIT order: HIGHER limit = more marketable. The limit is the MAXIMUM
    debit you will pay. The exit ladder walks the debit limit UP toward
    marketability (natural, then +$0.01/attempt).
=========================================================================

Phase-0 steps (Amendment A2026-08-09 resequencing — drift/Tasty/EODHD/USO first;
the test order runs ONLY after the operator confirms joint IC is disabled):
  1. Drift gate (md5 sweep, operator-run over read-only SSH — NOT in this file).
     RESULT 2026-08-09: 11/11 MATCH vs 7d34d82; brokers/robinhood.py = 5862d2e8
     both sides -> additive pre-authorization condition (a) satisfied.
  2. RH account resolution -> resolve the repo 'joint' keyword filter to the
     concrete account number; assert margin + option_level >= 3; echo it for
     operator confirmation. RESULT: Joint = 116637293063 (option_level_3, margin).
  2b. Foreign-position baseline (open option positions + orders). RESULT: CLEAN
     (0 positions, 0 orders).
  3. Stage B: the ONE deliberately-unfillable GTC condor (proves 4-leg accept,
     account routing, GTC combos [V11], cancel/status). Places EXACTLY ONE order,
     no retry; cancels and polls to terminal.
  4. Tasty market-metrics (IVR) for 7 symbols. RESULT: prod SDK = tastytrade
     12.4.1; rank fields are 0-1 scale (SPY 0.272 == IVR 27.2 -> normalize x100);
     canonical field implied_volatility_index_rank (== tos); tw diverges (never tw).
  5. EODHD economic-events plan check. RESULT: /api/economic-events HTTP 200
     (available); OQ-1 stands (launch on manual+seed+rule; feed deferred).
  6. USO distributions. RESULT: zero on record -> exdiv_guard: false confirmed.

Stage-B run history (both clean aborts, NOTHING placed): run 1 = $5-grid finding
(far-OTM SPY calls list $5 strikes only); run 2 = Joint UNFUNDED
("not enough overnight buying power" — payload accept + routing implicitly proven;
V11 GTC/cancel round-trip still pending funding).

Usage (on prod, as azureuser, prod venv python):
    python scripts/mace_phase0_probe.py --stage a        # read-only battery
    python scripts/mace_phase0_probe.py --stage b        # the one test order
"""
from __future__ import annotations

import argparse
import datetime
import json
import time
import traceback
import uuid

SYMS = ["SPY", "TLT", "GLD", "USO", "EWZ", "FXI", "IBIT"]
EXPECTED_ACCT = "116637293063"   # Joint — operator-CONFIRMED 2026-08-09
WIDTH = 5.0                      # $5 wings: far-OTM SPY lists $5-grid strikes ONLY
CREDIT_LIMIT = 4.75              # 0.95 x width — deliberately UNFILLABLE (see block)
ALLOWED_STATES = {"queued", "unconfirmed", "confirmed"}
TERMINAL = {"filled", "partially_filled", "rejected", "cancelled", "canceled",
            "failed", "voided"}


def _sec(t: str) -> None:
    print("\n======== " + t + " ========")


def _req_get(rs):
    try:
        return rs.request_get
    except AttributeError:
        from robin_stocks.robinhood.helper import request_get
        return request_get


def _resolve_joint(rs):
    """Resolve the 'joint' keyword filter to the concrete account dict."""
    get = _req_get(rs)
    accounts = get(
        "https://api.robinhood.com/accounts/?default_to_all_accounts=true",
        "results") or []
    for a in accounts:
        typ = str(a.get("brokerage_account_type") or a.get("type") or "")
        if "joint" in typ.lower():
            return a, accounts
    return None, accounts


# ── stage A: read-only capability battery ───────────────────────────────

def probe_account_and_positions() -> dict:
    out: dict = {}
    _sec("RH ACCOUNT RESOLUTION")
    try:
        import robin_stocks.robinhood as rs
        rs.login(store_session=True)
        joint, accounts = _resolve_joint(rs)
        for a in accounts:
            print("account", a.get("account_number"),
                  "| type", str(a.get("brokerage_account_type") or a.get("type") or ""),
                  "| option_level", a.get("option_level"),
                  "| margin", "present" if a.get("margin_balances") else "absent",
                  "| deactivated", a.get("deactivated"))
        if joint is None:
            print("RESULT: NO 'joint' account — STOP, report to operator")
            return out
        out["joint"] = joint.get("account_number")
        print("RESOLVED JOINT:", out["joint"], "| option_level",
              joint.get("option_level"), "(assert >= 3)")
        print("  OPERATOR: confirm this number — Phase-1 numeric hard-bind")

        _sec("FOREIGN-POSITION BASELINE (open option positions + orders)")
        j = out["joint"]
        pos = rs.options.get_open_option_positions(account_number=j) or []
        print("open option positions:", len(pos))
        oo = rs.orders.get_all_open_option_orders(account_number=j) or []
        print("open option orders:", len(oo))
    except Exception:
        traceback.print_exc()
    return out


def probe_tasty_ivr() -> None:
    _sec("TASTYTRADE MARKET-METRICS (IVR) — 7 symbols")
    try:
        creds = {}
        try:
            with open("/etc/trading-corp/tastytrade.env") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        creds[k.strip()] = v.strip().strip('"').strip("'")
        except PermissionError:
            print("PERMISSION DENIED reading tastytrade.env (root path required)")
            return
        except FileNotFoundError:
            print("tastytrade.env NOT FOUND")
            return
        ps_, rt_ = creds.get("TASTYTRADE_PROVIDER_SECRET"), creds.get("TASTYTRADE_REFRESH_TOKEN")
        print("tasty creds loaded:", bool(ps_ and rt_), "(values not printed)")
        if not (ps_ and rt_):
            return
        import asyncio
        import inspect
        from tastytrade import Session
        from tastytrade.metrics import get_market_metrics
        s = Session(provider_secret=ps_, refresh_token=rt_)
        mm = (asyncio.run(get_market_metrics(s, SYMS))
              if inspect.iscoroutinefunction(get_market_metrics)
              else get_market_metrics(s, SYMS))
        for m in mm:
            print(getattr(m, "symbol", "?"),
                  "| ivr", getattr(m, "implied_volatility_index_rank", None),
                  "| tos", getattr(m, "tos_implied_volatility_index_rank", None),
                  "| tw", getattr(m, "tw_implied_volatility_index_rank", None),
                  "(NEVER use tw)",
                  "| updated", getattr(m, "updated_at", None))
    except Exception:
        traceback.print_exc()


def probe_uso() -> None:
    _sec("USO DISTRIBUTIONS (yfinance)")
    try:
        import yfinance as yf
        div = yf.Ticker("USO").dividends
        if div is None or len(div) == 0:
            print("USO: zero distributions -> exdiv_guard: false CONFIRMED")
        else:
            print("USO HAS distributions (last 3):")
            print(div.tail(3))
    except Exception:
        traceback.print_exc()


def stage_a() -> None:
    probe_account_and_positions()
    probe_tasty_ivr()
    probe_uso()
    _sec("STAGE A COMPLETE")


# ── stage B: the one deliberately-unfillable GTC test order ─────────────

def stage_b(account: str = EXPECTED_ACCT, width: float = WIDTH,
            credit_limit: float = CREDIT_LIMIT) -> None:
    import robin_stocks.robinhood as rs
    import robin_stocks.robinhood.orders as O

    _sec("GATE 1: joint IC disabled in strategies.yaml")
    raw = open("/home/azureuser/trading_corp/config/strategies.yaml", "rb").read()
    if b"robinhood_joint_iron_condor:\n  enabled: false" not in raw:
        raise SystemExit("ABORT: joint IC NOT disabled — run mace_p0b1 first")
    print("joint IC enabled: false — gate passed")

    rs.login(store_session=True)
    _sec("GATE 2: account + clean-order baseline")
    joint, _ = _resolve_joint(rs)
    if not joint or joint.get("account_number") != account:
        raise SystemExit("ABORT: joint resolution != " + account)
    print("joint account confirmed:", account)
    oo = rs.orders.get_all_open_option_orders(account_number=account) or []
    if oo:
        raise SystemExit("ABORT: %d open option orders pre-exist — baseline dirty" % len(oo))
    print("open option orders: 0 — baseline clean")

    _sec("BUILD: SPY condor, highest DTE in [30,45], far-OTM ~5-delta shorts")
    spot = float((rs.stocks.get_latest_price("SPY") or ["0"])[0])
    print("SPY spot:", spot)
    chains = rs.options.get_chains("SPY") or {}
    today = datetime.date.today()
    cands = [(d, e) for e in (chains.get("expiration_dates") or [])
             if 30 <= (d := (datetime.date.fromisoformat(e) - today).days) <= 45]
    if not cands:
        raise SystemExit("ABORT: no SPY expiry in 30-45 DTE")
    dte, expiry = max(cands)
    print("expiry:", expiry, "(DTE %d)" % dte)
    sp = int(round(spot * 0.92 / 5.0)) * 5   # short put ~8% OTM, snapped to $5 grid
    sc = int(round(spot * 1.08 / 5.0)) * 5   # short call ~8% OTM, $5 grid
    legs_spec = [("put", sp, "sell"), ("put", sp - width, "buy"),
                 ("call", sc, "sell"), ("call", sc + width, "buy")]
    print("strikes: sp %s / lp %s / sc %s / lc %s (width %s)"
          % (sp, sp - width, sc, sc + width, width))
    for typ, k, _side in (legs_spec[0], legs_spec[2]):   # delta check on the shorts
        try:
            md = rs.options.get_option_market_data("SPY", expiry, str(k), typ)
            while isinstance(md, list) and md:
                md = md[0]
            dlt = (md or {}).get("delta")
            print("short %s %s delta: %s" % (typ, k, dlt))
            if dlt is not None and abs(float(dlt)) > 0.10:
                raise SystemExit("ABORT: short %s delta %s > 0.10" % (typ, dlt))
        except SystemExit:
            raise
        except Exception as e:
            print("  delta lookup failed (%s) — credit limit is the safety" % type(e).__name__)

    legs = []
    for typ, k, side in legs_spec:
        oid = O.id_for_option("SPY", expiry, str(k), typ)
        if not oid:
            raise SystemExit("ABORT: strike not listed: %s %s %s" % (typ, k, expiry))
        legs.append({"position_effect": "open", "side": side, "ratio_quantity": 1,
                     "option": O.option_instruments_url(oid)})
    print("all 4 legs resolved")

    _sec("PLACE: ONE GTC net-credit limit @ %s (UNFILLABLE)" % credit_limit)
    ref_id = str(uuid.uuid4())
    payload = {
        "account": O.load_account_profile(account_number=account, info="url"),
        "direction": "credit",           # net-credit: lower limit = more marketable
        "time_in_force": "gtc",          # V11: the GTC-combo proof
        "legs": legs,
        "type": "limit",
        "trigger": "immediate",
        "price": credit_limit,           # 0.95 x width — rests, cannot fill
        "quantity": 1,
        "override_day_trade_checks": False,
        "override_dtbp_checks": False,
        "ref_id": ref_id,
    }
    print("ref_id:", ref_id)
    resp = O.request_post(O.option_orders_url(account_number=account), payload,
                          json=True, jsonify_data=True)
    if not isinstance(resp, dict) or not resp.get("id"):
        print("POST result (no id — NOT retrying; checking for silent creation):")
        print(json.dumps(resp, indent=1, default=str)[:1500])
        oo2 = rs.orders.get_all_open_option_orders(account_number=account) or []
        print("open option orders now:", len(oo2))
        raise SystemExit("ABORT: placement failed or ambiguous — report")
    oid_ = resp["id"]
    acct_url = str(resp.get("account") or "")
    print("order id:", oid_, "| state:", resp.get("state"),
          "| tif:", resp.get("time_in_force"), "| processed:", resp.get("processed_quantity"))
    assert resp.get("time_in_force") == "gtc", "GTC NOT echoed"
    assert account in acct_url, "routed to WRONG account: " + acct_url
    assert str(resp.get("state") or "").lower() in ALLOWED_STATES, "unexpected state"
    assert float(resp.get("processed_quantity") or 0) == 0.0, "NON-ZERO FILLS"
    print("ASSERTIONS PASS: id + joint routing + gtc echo + zero fills")

    _sec("CANCEL + poll to terminal")
    rs.orders.cancel_option_order(oid_)
    final = None
    deadline = time.time() + 45
    while time.time() < deadline:
        time.sleep(2)
        info = rs.orders.get_option_order_info(oid_) or {}
        s = str(info.get("state") or "").lower()
        print("  poll:", s)
        if s in TERMINAL:
            final = info
            break
    if final is None:
        raise SystemExit("ABORT: order %s not terminal after 45s — MANUAL CHECK" % oid_)
    assert str(final.get("state") or "").lower() in ("cancelled", "canceled"), "not cancelled"
    assert float(final.get("processed_quantity") or 0) == 0.0, "fills on cancel race"
    print("STAGE B COMPLETE: 4-leg + joint routing + GTC + cancel/status PROVEN")
    print("Order id for deploy_log:", oid_)


def main() -> int:
    ap = argparse.ArgumentParser(description="MACE Phase-0 probe (archival record).")
    ap.add_argument("--stage", choices=["a", "b"], required=True,
                    help="a = read-only battery; b = the one unfillable GTC test order")
    ap.add_argument("--account", default=EXPECTED_ACCT)
    ap.add_argument("--width", type=float, default=WIDTH)
    ap.add_argument("--credit-limit", type=float, default=CREDIT_LIMIT)
    args = ap.parse_args()
    if args.stage == "a":
        stage_a()
    else:
        stage_b(account=args.account, width=args.width, credit_limit=args.credit_limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
