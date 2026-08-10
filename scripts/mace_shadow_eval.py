#!/usr/bin/env python3
"""MACE iron-condor Checkpoint-2 shadow eval — READ-ONLY dry run.

Runs the full MACE entry pipeline against live broker/Tasty data and
prints a per-symbol decision table.  PLACES NOTHING.  WRITES NOTHING.

Run this on the prod box (needs robin_stocks stored session + Tasty creds +
prod DB).  It cannot be run locally — it is a mechanics verification only.
Today's run on stale Friday chains is mechanics-only; the operator re-runs
live on Monday.

Usage:
    python scripts/mace_shadow_eval.py
    python scripts/mace_shadow_eval.py --db sqlite:///data/trading_corp.db
    python scripts/mace_shadow_eval.py --equity 12000
    python scripts/mace_shadow_eval.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── lightweight imports only (no broker, no tasty, no DB) ───────────────────
from trading_corp.mace.config import load_mace_config
from trading_corp.mace.domain import CondorSpec, OptionQuote, RungState
from trading_corp.mace import strategy as st
from trading_corp.mace import ivr_provider as ivr
from trading_corp.persistence import db
from trading_corp.utils.time import now_et, ET


# ── helpers ──────────────────────────────────────────────────────────────────

def _safe_float(v) -> float | None:
    """Coerce a string/None/numeric to float; return None on failure."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


# ── robin_stocks chain builder ────────────────────────────────────────────────

def _build_chain_view(sym: str, dte_min: int, dte_max: int,
                      session_date: date) -> st.ChainView:
    """Fetch spot + enumerate near-money strikes for ONE target expiry.

    All robin_stocks imports are inside this function so the module imports
    cleanly anywhere.  Strikes are bounded to spot*0.85 .. spot*1.15 to cap
    API calls.  Strikes whose market data fetch fails are silently skipped
    (a debug count is printed).
    """
    import robin_stocks.robinhood as rs  # stored session — login() is a no-op if cached

    # spot
    price_list = rs.stocks.get_latest_price(sym) or ["0"]
    spot = _safe_float(price_list[0] if isinstance(price_list, list) else price_list)
    if not spot:
        print(f"  [{sym}] spot fetch failed — returning empty chain")
        return st.ChainView(sym, None, (), {})

    # expiries: choose highest DTE in [dte_min, dte_max]
    chains_data = rs.options.get_chains(sym) or {}
    expiry_strings: list[str] = chains_data.get("expiration_dates") or []
    target_expiry_str: str | None = None
    target_dte = -1
    for es in expiry_strings:
        try:
            d = date.fromisoformat(es)
        except ValueError:
            continue
        dte = (d - session_date).days
        if dte_min <= dte <= dte_max and dte > target_dte:
            target_dte = dte
            target_expiry_str = es

    if target_expiry_str is None:
        print(f"  [{sym}] no expiry in DTE [{dte_min},{dte_max}] — chain empty (will emit no_expiry skip)")
        all_expiry_dates = tuple(
            date.fromisoformat(es) for es in expiry_strings
            if _date_ok(es)
        )
        return st.ChainView(sym, spot, all_expiry_dates, {})

    expiry_date = date.fromisoformat(target_expiry_str)
    print(f"  [{sym}] spot={spot:.2f}  target_expiry={target_expiry_str} (DTE {target_dte})")

    lo_bound = spot * 0.85
    hi_bound = spot * 1.15
    quotes: dict[tuple[date, str, float], OptionQuote] = {}
    fail_count = 0

    for opt_type in ("put", "call"):
        # enumerate strikes for this expiry
        instruments: list[dict] = []
        try:
            instruments = rs.options.find_options_by_expiration(
                sym, expirationDate=target_expiry_str, optionType=opt_type
            ) or []
        except Exception:
            try:
                instruments = rs.options.find_tradable_options(
                    sym, expirationDate=target_expiry_str, optionType=opt_type
                ) or []
            except Exception:
                pass

        strikes_in_band: list[float] = []
        for inst in instruments:
            k = _safe_float(inst.get("strike_price"))
            if k is not None and lo_bound <= k <= hi_bound:
                strikes_in_band.append(k)

        for k in sorted(set(strikes_in_band)):
            try:
                md = rs.options.get_option_market_data(sym, target_expiry_str, str(k), opt_type)
                # unwrap nested list (robin_stocks sometimes double-wraps)
                while isinstance(md, list) and md:
                    md = md[0]
                if not isinstance(md, dict):
                    fail_count += 1
                    continue
                delta = _safe_float(md.get("delta"))
                bid = _safe_float(md.get("bid_price"))
                ask = _safe_float(md.get("ask_price"))
                q = OptionQuote(
                    symbol=sym,
                    expiry=expiry_date,
                    strike=k,
                    opt_type=opt_type,
                    bid=bid,
                    ask=ask,
                    delta=delta,
                )
                quotes[(expiry_date, opt_type, round(k, 4))] = q
            except Exception:
                fail_count += 1

    if fail_count:
        print(f"  [{sym}] {fail_count} strike(s) skipped (market data fetch failed)")

    print(f"  [{sym}] quotes loaded: {len(quotes)} ({opt_type} side last; both sides combined)")
    return st.ChainView(sym, spot, (expiry_date,), quotes)


def _date_ok(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


# ── Tasty IVR fetch closure ──────────────────────────────────────────────────

def _make_fetch_metrics():
    """Build a fetch_metrics(symbols) -> list closure backed by Tasty creds.

    Reads /etc/trading-corp/tastytrade.env for credentials.  Creates a
    fresh tastytrade.Session each call (token management is outside scope).
    Uses asyncio.run() if get_market_metrics is a coroutine function.
    Heavy imports are INSIDE the closure so the module stays import-clean.
    """
    def fetch_metrics(symbols_list):
        import asyncio
        import inspect
        from tastytrade import Session
        from tastytrade.metrics import get_market_metrics

        creds: dict[str, str] = {}
        with open("/etc/trading-corp/tastytrade.env") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    creds[k.strip()] = v.strip().strip('"').strip("'")

        ps = creds.get("TASTYTRADE_PROVIDER_SECRET")
        rt = creds.get("TASTYTRADE_REFRESH_TOKEN")
        if not (ps and rt):
            raise RuntimeError("TASTYTRADE_PROVIDER_SECRET or TASTYTRADE_REFRESH_TOKEN missing")

        session = Session(provider_secret=ps, refresh_token=rt)
        result = (
            asyncio.run(get_market_metrics(session, list(symbols_list)))
            if inspect.iscoroutinefunction(get_market_metrics)
            else get_market_metrics(session, list(symbols_list))
        )
        return result

    return fetch_metrics


# ── DB readers (READ-ONLY; no INSERT/UPDATE/DELETE) ──────────────────────────

def _load_rungs(conn) -> list[RungState]:
    """Load open mace_rung rows (status IN submitting/open/closing)."""
    rows = conn.execute(
        "SELECT rung_id, symbol, status, expiry, legs_json, width_dollars, "
        "       contracts, credit_actual, max_risk_usd, entry_ts, entry_order_id, "
        "       pt_order_id, pt_debit, exit_ts, exit_reason, realized_pnl, "
        "       entry_iso_week "
        "FROM mace_rung "
        "WHERE status IN ('submitting','open','closing')"
    ).fetchall()

    rungs: list[RungState] = []
    for r in rows:
        # Build a minimal CondorSpec from legs_json if possible; else placeholder.
        spec = _spec_from_legs_json(r["legs_json"], r["symbol"],
                                    r["expiry"], r["width_dollars"])
        rungs.append(RungState(
            rung_id=r["rung_id"],
            symbol=r["symbol"],
            status=r["status"],
            expiry=date.fromisoformat(r["expiry"]),
            spec=spec,
            width_dollars=float(r["width_dollars"] or 0.0),
            contracts=int(r["contracts"] or 0),
            credit_actual=_safe_float(r["credit_actual"]),
            max_risk_usd=_safe_float(r["max_risk_usd"]),
            entry_ts=r["entry_ts"],
            entry_order_id=r["entry_order_id"],
            pt_order_id=r["pt_order_id"],
            pt_debit=_safe_float(r["pt_debit"]),
            exit_ts=r["exit_ts"],
            exit_reason=r["exit_reason"],
            realized_pnl=_safe_float(r["realized_pnl"]),
            entry_iso_week=r["entry_iso_week"],
        ))
    return rungs


def _spec_from_legs_json(legs_json_str: str | None, symbol: str,
                         expiry_str: str, width_dollars: float) -> CondorSpec:
    """Parse legs_json into a CondorSpec; fall back to a zero-strike placeholder."""
    try:
        if legs_json_str:
            legs = json.loads(legs_json_str)
            if isinstance(legs, list) and len(legs) == 4:
                # Legs: [short_put, long_put, short_call, long_call] by convention
                # legs_json schema: [{type, strike, side, effect, ...}, ...]
                by_type_side: dict[tuple[str, str], float] = {}
                for leg in legs:
                    opt_type = str(leg.get("type") or leg.get("opt_type") or "").lower()
                    side = str(leg.get("side") or "").lower()
                    strike = _safe_float(leg.get("strike") or leg.get("strike_price"))
                    if opt_type and side and strike is not None:
                        by_type_side[(opt_type, side)] = strike
                sp = by_type_side.get(("put", "sell"))
                lp = by_type_side.get(("put", "buy"))
                sc = by_type_side.get(("call", "sell"))
                lc = by_type_side.get(("call", "buy"))
                if sp and lp and sc and lc:
                    return CondorSpec(
                        symbol=symbol,
                        expiry=date.fromisoformat(expiry_str),
                        short_put=sp, long_put=lp,
                        short_call=sc, long_call=lc,
                        width_dollars=float(width_dollars),
                    )
    except Exception:
        pass
    # Placeholder CondorSpec (non-zero fields to avoid division by zero in mgmt)
    return CondorSpec(
        symbol=symbol,
        expiry=date.fromisoformat(expiry_str),
        short_put=0.0, long_put=0.0,
        short_call=0.0, long_call=0.0,
        width_dollars=float(width_dollars),
    )


def _load_events(conn) -> list[dict]:
    """Load all economic_event rows as plain dicts."""
    rows = conn.execute(
        "SELECT event_type, symbol_scope, event_date, source FROM economic_event"
    ).fetchall()
    return [
        {"event_type": r["event_type"],
         "symbol_scope": r["symbol_scope"],
         "event_date": r["event_date"]}
        for r in rows
    ]


def _load_equity(conn) -> float | None:
    """Read the latest mace_equity_snapshot.equity; None if the table is empty."""
    try:
        row = conn.execute(
            "SELECT equity FROM mace_equity_snapshot ORDER BY snap_date DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return _safe_float(row["equity"])
    except Exception:
        return None


# ── RH login (inside a function so import is lazy) ───────────────────────────

def _rh_login():
    """Login robin_stocks using the stored session.  Returns the rs module."""
    import robin_stocks.robinhood as rs
    rs.login(store_session=True)
    return rs


# ── output helpers ────────────────────────────────────────────────────────────

_COL_W = {
    "SYMBOL":    7,
    "DECISION":  22,
    "STRIKES":   22,
    "CREDIT":    7,
    "CONTRACTS": 9,
    "MAXRISK":   9,
    "IVR":       16,
    "DETAIL":    40,
}


def _row(*cells) -> str:
    cols = list(_COL_W.keys())
    parts = []
    for i, (col, val) in enumerate(zip(cols, cells)):
        w = _COL_W[col]
        s = str(val)
        if i == len(cols) - 1:  # last column: no pad
            parts.append(s)
        else:
            parts.append(s[:w].ljust(w))
    return "  ".join(parts)


def _header_line() -> str:
    return _row(*_COL_W.keys())


def _separator() -> str:
    return "  ".join("-" * w for w in _COL_W.values())


def _result_row(r: "st.EvalResult") -> str:
    decision = "ENTER" if r.entered else (r.skip_reason or "?")
    strikes = r.spec.strikes_label() if r.spec else "-"
    credit = f"{r.credit_mid:.2f}" if r.credit_mid is not None else "-"
    contracts = str(r.contracts) if r.contracts else "-"
    maxrisk = f"${r.max_risk_usd:.0f}" if r.max_risk_usd is not None else "-"
    ivr_str = f"{r.ivr_status}"
    if r.ivr_value is not None:
        ivr_str += f"/{r.ivr_value:.1f}"
    return _row(r.symbol, decision, strikes, credit, contracts, maxrisk,
                ivr_str, r.detail[:40])


def _result_to_dict(r: "st.EvalResult") -> dict:
    return {
        "symbol": r.symbol,
        "entered": r.entered,
        "skip_reason": r.skip_reason,
        "strikes": r.spec.strikes_label() if r.spec else None,
        "credit_mid": r.credit_mid,
        "contracts": r.contracts,
        "max_risk_usd": r.max_risk_usd,
        "ivr_status": r.ivr_status,
        "ivr_value": r.ivr_value,
        "overflow": r.overflow,
        "detail": r.detail,
    }


# ── main pipeline ─────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> int:
    # 1. Load config
    cfg_path = ROOT / "config" / "mace.yaml"
    exdiv_path = ROOT / "config" / "ex_dividend_calendar.yaml"
    print(f"[shadow_eval] loading config: {cfg_path}")
    try:
        cfg = load_mace_config(str(cfg_path), exdiv_calendar_path=str(exdiv_path))
    except Exception as exc:
        print(f"[shadow_eval] FATAL: config load failed: {exc}")
        return 1
    print(f"[shadow_eval] config_hash={cfg.config_hash[:12]}  universe={list(cfg.universe)}")

    session_date = now_et().date()
    print(f"[shadow_eval] session_date={session_date}")

    # 2. DB: load rungs + events + equity (READ-ONLY)
    equity: float | None = args.equity  # CLI override wins
    rungs: list[RungState] = []
    events: list[dict] = []
    db_url = args.db

    print(f"[shadow_eval] connecting to DB: {db_url}")
    try:
        with db.connect(db_url) as conn:
            rungs = _load_rungs(conn)
            events = _load_events(conn)
            if equity is None:
                equity = _load_equity(conn)
    except Exception as exc:
        print(f"[shadow_eval] WARNING: DB read failed ({type(exc).__name__}: {exc}) — "
              f"proceeding with empty rungs/events (expect no_equity_snapshot skips)")
        traceback.print_exc()

    if equity is None:
        print("[shadow_eval] equity: None (no snapshot + no --equity override; "
              "expect no_equity_snapshot skips for all symbols)")
    else:
        print(f"[shadow_eval] equity=${equity:.2f}  open_rungs={len(rungs)}  events={len(events)}")

    # 3. robin_stocks login + chain build (lazy import)
    print("[shadow_eval] logging in to robin_stocks (stored session) ...")
    try:
        _rh_login()
        print("[shadow_eval] robin_stocks login OK")
    except Exception as exc:
        print(f"[shadow_eval] FATAL: robin_stocks login failed: {exc}")
        return 1

    chains: dict[str, st.ChainView] = {}
    for sym in cfg.universe:
        print(f"[shadow_eval] building chain: {sym}")
        try:
            chain = _build_chain_view(
                sym, cfg.entry.dte_min, cfg.entry.dte_max, session_date
            )
            chains[sym] = chain
        except Exception as exc:
            print(f"  [{sym}] chain build ERROR: {type(exc).__name__}: {exc}")
            chains[sym] = st.ChainView(sym, None, (), {})

    # also build chains for overflow_only symbols that may receive capital
    for sym, sym_cfg in cfg.symbols.items():
        if sym_cfg.enabled and sym not in chains:
            print(f"[shadow_eval] building chain for overflow symbol: {sym}")
            try:
                chains[sym] = _build_chain_view(
                    sym, cfg.entry.dte_min, cfg.entry.dte_max, session_date
                )
            except Exception as exc:
                print(f"  [{sym}] chain build ERROR: {type(exc).__name__}: {exc}")
                chains[sym] = st.ChainView(sym, None, (), {})

    # 4. IVR via Tasty
    all_symbols = list({s for s, c in cfg.symbols.items() if c.enabled})
    print(f"[shadow_eval] fetching Tasty IVR for {all_symbols} ...")
    try:
        fetch_metrics = _make_fetch_metrics()
        ivr_readings = ivr.read_metrics(fetch_metrics, all_symbols)
        for sym, reading in ivr_readings.items():
            print(f"  {sym}: {reading.status}  ivr={reading.ivr}  detail={reading.detail[:60]}")
    except Exception as exc:
        print(f"[shadow_eval] WARNING: IVR fetch setup failed ({type(exc).__name__}: {exc}); "
              f"all symbols will be IVR_UNAVAILABLE")
        ivr_readings = {s: _unavailable_ivr(s, str(exc)) for s in all_symbols}

    # 5. Build EntryContext
    ctx = st.EntryContext(
        session_date=session_date,
        equity=equity,
        rungs=rungs,
        events=events,
        ivr=ivr_readings,
        chains=chains,
        next_session_date=None,   # strategy.next_session() will derive it
        risk_gate=None,
    )

    # 6. Evaluate all primary universe symbols
    primary_results: list[st.EvalResult] = []
    for sym in cfg.universe:
        result = st.evaluate_entry(sym, cfg, ctx)
        primary_results.append(result)

    # 7. Overflow routing (inert at launch with universe=[SPY], but included per spec)
    overflow_results: list[st.EvalResult] = st.route_overflow(primary_results, cfg, ctx)

    # 8. Output
    if args.json:
        out = {
            "config_hash": cfg.config_hash,
            "session_date": session_date.isoformat(),
            "equity": equity,
            "universe": list(cfg.universe),
            "primary": [_result_to_dict(r) for r in primary_results],
            "overflow": [_result_to_dict(r) for r in overflow_results],
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

    # human-readable table
    print()
    print(f"config_hash={cfg.config_hash[:12]}  session_date={session_date}"
          f"  equity={('$'+f'{equity:.0f}') if equity is not None else 'None'}"
          f"  universe={list(cfg.universe)}")
    print()
    print(_header_line())
    print(_separator())
    for r in primary_results:
        print(_result_row(r))
    print()

    if overflow_results:
        print("overflow:")
        print(_header_line())
        print(_separator())
        for r in overflow_results:
            print(_result_row(r))
    else:
        print("overflow: none (inert)")

    print()
    print("[shadow_eval] COMPLETE — 0 orders placed, 0 DB writes")
    return 0


def _unavailable_ivr(sym: str, reason: str):
    """Return a minimal IvrReading-compatible object when Tasty setup fails."""
    from trading_corp.mace.ivr_provider import IvrReading
    from trading_corp.mace.domain import IVR_UNAVAILABLE
    return IvrReading(sym, IVR_UNAVAILABLE, None, None, None, None,
                      f"fetch setup failed: {reason}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--db",
        default="sqlite:///data/trading_corp.db",
        help="DB URL or path (default: sqlite:///data/trading_corp.db)",
    )
    ap.add_argument(
        "--equity",
        type=float,
        default=None,
        help="equity override in USD (bypasses mace_equity_snapshot; optional)",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of the human-readable table",
    )
    args = ap.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
