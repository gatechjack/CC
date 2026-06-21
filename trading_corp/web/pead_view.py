"""Data builder for the `robinhood_pead` operations dashboard (read-layer).

`build_pead_view(deps)` fans out everything the `/telemetry/pead` page needs and
returns a plain dict for the Jinja template. The ONLY real logic is the open
book: per open position, compute the four exit pressures via the LOCKED
`pead_pressures` contract (the same module the Phase-2 exit engine fires on),
then sort by governing pressure. Pressure-empty-first: until the Phase-2 engine
writes the `extra_json` primitives, each row renders a graceful placeholder.

Read-only. The only write surface (the kill switch) lives in the route, not here.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone

from trading_corp.agents.strategies.pead_pressures import (
    RULE_COLORS,
    compute_pressures,
    primitives_from_extra,
    stop_level,
)
from trading_corp.persistence.db import connect
from trading_corp.persistence.pead_observability import (
    load_feed_status,
    scan_rejection_tally,
)

log = logging.getLogger(__name__)

DIVISION = "robinhood_pead"
_DEFAULT_DB = "sqlite:///data/trading_corp.db"


# ── trading-day counting (weekday-based; holidays ignored — adequate for the
# time/guard pressures over a 60-day window. The Phase-2 exit engine MUST use
# the same count so the locked contract holds). ────────────────────────────
def business_days(d1: date, d2: date) -> int:
    """Weekday count in the half-open interval [d1, d2). 0 if d2 <= d1."""
    if d2 <= d1:
        return 0
    total = (d2 - d1).days
    weeks, extra = divmod(total, 7)
    count = weeks * 5
    for i in range(extra):
        if (d1 + timedelta(days=weeks * 7 + i)).weekday() < 5:
            count += 1
    return count


def _parse_date(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def query_open_positions(db_url: str, division: str = DIVISION) -> list[dict]:
    """Open PEAD positions = paper_trade_record rows with result NULL.
    Empty until the Phase-2 division/exit-engine writes them."""
    with connect(db_url) as conn:
        rows = conn.execute(
            "SELECT order_id, symbol, qty, entry_reference_price, ts, extra_json "
            "FROM paper_trade_record WHERE division = ? AND result IS NULL",
            (division,),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        extra = {}
        if r["extra_json"]:
            try:
                extra = json.loads(r["extra_json"]) or {}
            except (ValueError, TypeError):
                extra = {}
        out.append({
            "order_id": r["order_id"], "symbol": r["symbol"],
            "qty": float(r["qty"] or 0),
            "entry_price": float(r["entry_reference_price"] or 0),
            "opened_ts": r["ts"], "extra": extra,
        })
    return out


def assemble_book(open_rows: list[dict], quotes: dict[str, float], today: date) -> list[dict]:
    """Pure book assembly: compute pressures per row (LOCKED contract) and sort
    by governing pressure descending. Rows missing the extra_json primitives or
    a live quote render `complete=False` (the pressure-empty placeholder)."""
    book: list[dict] = []
    for r in open_rows:
        sym = r["symbol"]
        last = quotes.get(sym)
        extra = r.get("extra") or {}
        entry = r["entry_price"]
        opened = _parse_date(r.get("opened_ts")) or today
        held = business_days(opened, today)
        pnl_usd = (last - entry) * r["qty"] if last is not None else None
        pnl_pct = (last - entry) / entry if (last is not None and entry > 0) else None
        base = {
            "symbol": sym,
            "name": extra.get("name") or sym,
            "entry_date": opened.strftime("%b %d"),
            "last": last,
            "sue": extra.get("entry_sue"),
            "held_days": held,
            "max_days": 60,
            "qty": r["qty"],
            "market_value": (last * r["qty"]) if last is not None else None,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
        }
        prim = primitives_from_extra(extra, entry)
        if prim is None or last is None:
            book.append({**base, "complete": False, "pressures": None,
                         "governing": None, "fuse_pct": None,
                         "fuse_color": None, "governing_color": None,
                         "open_risk_usd": None})
            continue
        nxt = _parse_date(extra.get("next_earnings_date"))
        days_to_next = business_days(today, nxt) if nxt else None
        pr = compute_pressures(prim, last, held_trading_days=held,
                               days_to_next_earnings=days_to_next)
        book.append({**base, "complete": True,
                     "pressures": {"stop": pr.stop, "drift": pr.drift,
                                   "guard": pr.guard, "time": pr.time},
                     "governing": pr.governing, "fuse_pct": pr.fuse_pct,
                     "fuse_color": pr.fuse_color, "governing_color": pr.governing_color,
                     "open_risk_usd": max(0.0, (entry - stop_level(prim)) * r["qty"])})
    # complete rows first, by governing pressure desc; then placeholders.
    book.sort(key=lambda b: (1 if b["complete"] else 0, b.get("fuse_pct") or 0.0),
              reverse=True)
    return book


async def _fetch_quotes(broker, symbols: list[str]) -> dict[str, float]:
    """Live quotes via the division broker (cached per call). [] / {} safe."""
    if broker is None or not hasattr(broker, "quote") or not symbols:
        return {}
    async def one(s):
        try:
            return s, float(await broker.quote(s))
        except Exception:  # noqa: BLE001
            return s, None
    results = await asyncio.gather(*(one(s) for s in symbols), return_exceptions=True)
    out: dict[str, float] = {}
    for res in results:
        if isinstance(res, tuple) and res[1] is not None:
            out[res[0]] = res[1]
    return out


def _exit_attribution(db_url: str, division: str = DIVISION) -> dict:
    """Closed PEAD trades grouped by the exit rule that closed them.
    Empty (graceful) until the exit engine books closes with extra_json.exit_reason."""
    try:
        with connect(db_url) as conn:
            rows = conn.execute(
                "SELECT extra_json, actual_pnl_dollars FROM paper_trade_record "
                "WHERE division = ? AND result IS NOT NULL",
                (division,),
            ).fetchall()
    except Exception:  # noqa: BLE001
        return {"by_rule": {}, "total": 0, "empty": True}
    by_rule: dict[str, dict] = {}
    for r in rows:
        extra = {}
        if r["extra_json"]:
            try:
                extra = json.loads(r["extra_json"]) or {}
            except (ValueError, TypeError):
                extra = {}
        rule = extra.get("exit_reason") or "unknown"
        b = by_rule.setdefault(rule, {"count": 0, "pnl": 0.0,
                                      "color": RULE_COLORS.get(rule, "#94a3b8")})
        b["count"] += 1
        b["pnl"] += float(r["actual_pnl_dollars"] or 0)
    total = sum(b["count"] for b in by_rule.values())
    return {"by_rule": by_rule, "total": total, "empty": total == 0}


async def build_pead_view(deps, *, today: date | None = None) -> dict:
    """Assemble the full PEAD dashboard view dict. Graceful + read-only:
    every block degrades to an empty state if its data isn't present yet."""
    db_url = getattr(deps, "db_url", _DEFAULT_DB)
    today = today or datetime.now(timezone.utc).date()

    broker = None
    if getattr(deps, "data_exec", None) is not None:
        broker = getattr(deps.data_exec, "brokers", {}).get(DIVISION)

    # mode (paper vs live) — the UNMISSABLE pill
    is_paper = bool(getattr(broker, "paper", True)) if broker is not None else True
    mode = {"paper": is_paper, "label": "PAPER" if is_paper else "LIVE",
            "wired": broker is not None}

    # open book
    open_rows = query_open_positions(db_url)
    quotes = await _fetch_quotes(broker, sorted({r["symbol"] for r in open_rows}))
    book = assemble_book(open_rows, quotes, today)

    # account values (small, real — never mockup magnitudes)
    snap = None
    if broker is not None:
        try:
            snap = await broker.snapshot()
        except Exception as e:  # noqa: BLE001
            log.warning("pead_view: snapshot failed: %s", e)
    equity = float(getattr(snap, "equity", 0) or 0) if snap is not None else None
    open_pnl = sum(b["pnl_usd"] for b in book if b["pnl_usd"] is not None) if book else 0.0
    open_risk = sum(b["open_risk_usd"] for b in book if b.get("open_risk_usd")) or 0.0
    market_value = sum(b["market_value"] for b in book if b.get("market_value")) or 0.0
    account = {
        "equity": equity,
        "open_pnl": open_pnl,
        "day_pnl": None,                      # needs account_state history (graceful None)
        "net_exposure_pct": (market_value / equity) if equity else None,
        "open_risk_usd": open_risk,
        "risk_budget_pct": None,              # wired to risk.yaml caps in a later pass
        "open_count": len(book),
    }

    # Stage-0 health: feed tri-state (+ broker reachability)
    feeds = load_feed_status(db_url)
    health = {
        "eodhd": feeds.get("eodhd", {"status": "unknown", "last_ok_ts": None,
                                     "last_check_ts": None, "detail": "no scan yet"}),
        "tastytrade": feeds.get("tastytrade", {"status": "unknown"}),
        "robinhood": {"status": "live" if snap is not None else
                      ("unknown" if broker is None else "down")},
    }

    # Stage 1-3 funnel + rejection tally
    tally = scan_rejection_tally(db_url=db_url)
    funnel = {"scanned": tally["scanned"], "qualified": tally["qualified"],
              "staged": None, "open": len(book)}
    rejections = {"by_reason": tally["by_reason"], "rejected": tally["rejected"],
                  "reconciles": (tally["scanned"] - tally["qualified"]) == tally["rejected"]}

    return {
        "mode": mode,
        "account": account,
        "health": health,
        "funnel": funnel,
        "rejections": rejections,
        "exit_queue": [],                     # populated by Phase-2 routing (graceful empty)
        "book": book,
        "attribution": _exit_attribution(db_url),
        "edge": {"win_rate": None, "avg_hold": None, "profit_factor": None,
                 "drift_capture": None, "empty": True},
        "equity": {"series": [], "ytd": None, "empty": True},
        "generated_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
