"""Prediction Markets -- SETTLEMENT-CLOSE (R-d): book a filled LIVE position that SETTLED on Kalshi, so the journal
goes flat (boot_reconcile comes up CLEAN instead of latching R-b) and /live stops showing a settled position as
"Currently held".

★ ONE TERMINAL-CLOSE PRIMITIVE, TWO TRIGGERS. A position goes flat in the journal via a row with is_exit=1,
outcome_status='filled', fill_count=held -- the ONLY shape that nets the signed-net both boot_reconcile
(`_JOURNAL_SIGNED_SQL`) and subdivision.live_positions use. Option D's whale-EXIT produces it via a POSTed
reduce_only order; R-d's SETTLEMENT produces the SAME shape WITHOUT a POST (the market resolved). The difference is
the trigger, not the state -- so there is never a second terminal representation to disagree with the first.

★ AUTHORITY = KALSHI'S OWN SETTLEMENT RECORD (Jack RULED, NOT gamma): the money event is a Kalshi fact, the reconcile
already trusts Kalshi's portfolio, and it sidesteps the Poly<->Kalshi (doubleheader) mapping entirely. The result
comes from the RAW `/portfolio/settlements` payload's `market_result` (the SDK model may drop it -- read RAW, per
the standing 'check the raw payload' rule).

★ THE SINGLE "is this position still live?" AUTHORITY = the journal net-open per (wallet, ticker, leg). BOTH triggers
gate on it: Option D's exit (skip:not_held if <=0) AND this scan (skip if net-open <=0). That is what prevents a
DOUBLE-CLOSE -- exit-then-settle and settle-then-exit both see net-open already 0 and no-op. Re-running the scan is
idempotent (a booked settlement is an is_exit=1 row -> counted in `exited` -> net-open 0 -> not re-booked).

STRUCTURAL 'cannot place' (mirrors boot_reconcile / execution): imports NO broker (only stdlib). The CALLER injects
the settlements data (live_driver fetches `/portfolio/settlements` authenticated + parses), exactly like
boot_reconcile injects positions. This module WRITES journal rows (the terminal close) but never an order.

Spec: reports/prediction_markets/RD_DESIGN_2026-08-31.md.
"""
from __future__ import annotations

import calendar
import logging
import time as _time
from dataclasses import dataclass

_LOG = logging.getLogger(__name__)
_EPS = 1e-9
# proceeds-vs-Kalshi-revenue cross-check tolerance (dollars): a divergence beyond this WARNs (our order fees are not
# in Kalshi's settlement `revenue`, so a small gap is expected; a large gap is a booking anomaly to hand-inspect).
_REVENUE_TOL = 0.10


# ── the injected settlement record (parsed from the RAW /portfolio/settlements payload) ──────────────────
@dataclass(frozen=True)
class SettlementRecord:
    ticker: str            # the MARKET ticker (UPPER); may be empty if only event_ticker is present
    event_ticker: str      # the EVENT ticker (UPPER) -- a market ticker starts with it (fallback match)
    result: str            # 'yes' | 'no' | 'void' | '' (lowercased market_result)
    settled_ts: int | None
    revenue: float | None  # Kalshi's settlement revenue (dollars) -- cross-check only, NOT the booked P&L


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso_to_unix(v) -> int | None:
    """Kalshi `settled_time` -> unix seconds. Accepts an int/float (already unix) or an ISO-8601 string
    ('2026-08-31T02:44:41.420484Z'). None/unparseable -> None (the caller stamps now_ts as a fallback)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    s = str(v).strip()
    if not s:
        return None
    s = s.replace("Z", "+0000") if s.endswith("Z") else s
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            import datetime as _dt
            dt = _dt.datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                return calendar.timegm(dt.timetuple())
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


def parse_settlements(raw) -> list:
    """Parse a RAW `GET /portfolio/settlements` response -> [SettlementRecord]. Reads `market_result` (the RAW
    field; the typed SDK model may omit it), `ticker`/`event_ticker`, `settled_time`, `revenue`. FAIL-SAFE: a
    non-dict payload or a non-list `settlements` -> []; a malformed individual record is SKIPPED (never raises) so
    one bad row cannot block booking the others -- a settlement is a bookkeeping event, not a money gate."""
    if not isinstance(raw, dict):
        return []
    items = raw.get("settlements")
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            tk = str(it.get("ticker") or "").upper()
            ev = str(it.get("event_ticker") or "").upper()
            res = str(it.get("market_result") or it.get("result") or "").strip().lower()
            out.append(SettlementRecord(ticker=tk, event_ticker=ev, result=res,
                                        settled_ts=_iso_to_unix(it.get("settled_time")),
                                        revenue=_f(it.get("revenue"))))
        except Exception:  # noqa: BLE001 -- one malformed record must not block the rest
            continue
    return out


def _settlement_for_ticker(ticker: str, by_ticker: dict):
    """Find the settlement for a held MARKET ticker by EXACT ticker match. (An event_ticker-PREFIX fallback was
    DROPPED, reviewer F2 2026-08-31: /portfolio/settlements records are per-MARKET and carry the market `ticker`,
    and a prefix guess could bind the WRONG side of a game -- booking a win as a loss -- if a record lacked a market
    ticker. An unmatched held position is simply left OPEN; if the venue is actually flat, boot_reconcile latches
    R-b, the safe fallback.)"""
    return by_ticker.get(ticker)


# ── the held positions (per wallet x ticker x leg) from OUR journal ──────────────────────────────────────
_HELD_SQL = (
    "SELECT wallet, UPPER(ticker) AS ticker, outcome_leg, "
    "  SUM(CASE WHEN is_exit=0 THEN COALESCE(fill_count,0) ELSE 0 END) AS entered, "
    "  SUM(CASE WHEN is_exit=1 THEN COALESCE(fill_count,0) ELSE 0 END) AS exited, "
    "  SUM(CASE WHEN is_exit=0 THEN COALESCE(fill_count,0)*COALESCE(fill_price,0)+COALESCE(fee,0) "
    "           ELSE 0 END) AS entry_cost "
    "FROM pm_subdivision_order "
    "WHERE account_id=? AND category=? AND dry_run=0 AND outcome_status='filled' AND ticker IS NOT NULL "
    "  AND outcome_leg IN ('yes','no') "
    "GROUP BY wallet, UPPER(ticker), outcome_leg"
)


def _won(leg: str, result: str) -> int:
    return 1 if ((leg == "yes" and result == "yes") or (leg == "no" and result == "no")) else 0


def book_settlements(conn, account_id: str, category: str, settlements, *, now_ts: int) -> dict:
    """INSERT a terminal-close row for every held (wallet, ticker, leg) whose ticker has a Kalshi settlement.

    The row: is_exit=1, outcome_status='filled', fill_count=net_open (nets the position in boot_reconcile AND
    /live), fill_price=the SETTLED per-contract value (won->1.0 / lost->0.0 / void->avg_cost refund), fee=0,
    close_source='settlement'|'settlement_void', realized_pnl, won, settled_ts. PER-WALLET (each whale's cost basis
    differs + per-wallet net-open must go flat so a stale whale-exit later sees skip:not_held).

    realized_pnl = net_open*settled_value - cost_basis_open, cost_basis_open = net_open*avg_cost, avg_cost =
    entry_cost/entered (AVERAGE cost -> a partial-exit-then-settle is correct; the exit already booked its own leg).
    Cross-checks the per-ticker proceeds vs Kalshi's `revenue` and WARNs on a divergence beyond a fee tolerance.

    IDEMPOTENT: a position already flat (net_open<=0 -- a prior scan or a whale-exit closed it) is skipped. Returns
    a summary; `booked` carries each close (for the boot-scan to LOG, esp. the first-ever settlement, hand-inspect)."""
    settlements = list(settlements)
    by_ticker = {r.ticker: r for r in settlements if r.ticker}
    booked, skipped_flat, skipped_no_settlement = [], 0, 0
    proceeds_by_ticker: dict = {}
    for row in conn.execute(_HELD_SQL, (account_id, category)):
        wallet = row["wallet"]; ticker = row["ticker"]; leg = row["outcome_leg"]
        entered = float(row["entered"] or 0.0); exited = float(row["exited"] or 0.0)
        entry_cost = float(row["entry_cost"] or 0.0)
        net_open = round(entered - exited, 6)
        if net_open <= _EPS:
            skipped_flat += 1
            continue
        rec = _settlement_for_ticker(ticker, by_ticker)
        if rec is None or rec.result not in ("yes", "no", "void"):
            skipped_no_settlement += 1
            continue
        avg_cost = (entry_cost / entered) if entered > _EPS else 0.0
        cost_basis_open = net_open * avg_cost
        if rec.result == "void":
            close_source, won, settled_value = "settlement_void", None, avg_cost   # void = refund cost -> pnl 0
        else:
            won = _won(leg, rec.result)
            close_source, settled_value = "settlement", (1.0 if won else 0.0)
        proceeds = net_open * settled_value
        realized = round(proceeds - cost_basis_open, 6)
        settled_ts = rec.settled_ts if rec.settled_ts is not None else int(now_ts)
        conn.execute(
            "INSERT INTO pm_subdivision_order (account_id, category, wallet, ticker, outcome_leg, is_exit, "
            " fill_count, fill_price, fee, outcome_status, close_source, realized_pnl, won, settled_ts, dry_run, "
            " submitted_ts, response_ts) VALUES (?,?,?,?,?,1,?,?,0,'filled',?,?,?,?,0,?,?)",
            (account_id, category, wallet, ticker, leg, net_open, round(settled_value, 4), close_source,
             realized, won, settled_ts, int(now_ts), int(now_ts)))
        proceeds_by_ticker[ticker] = proceeds_by_ticker.get(ticker, 0.0) + proceeds
        booked.append({"wallet": wallet, "ticker": ticker, "leg": leg, "net_open": net_open,
                       "settled_value": settled_value, "realized_pnl": realized, "won": won,
                       "close_source": close_source, "settled_ts": settled_ts,
                       "kalshi_revenue": (rec.revenue if rec else None)})
    if hasattr(conn, "commit") and booked:
        conn.commit()
    # cross-check: our per-ticker settlement PROCEEDS vs Kalshi's revenue (order fees are not in `revenue`, so a
    # small gap is expected; a LARGE gap is a booking anomaly -> WARN + hand-inspect, do NOT block the booking).
    for tk, proceeds in proceeds_by_ticker.items():
        rec = by_ticker.get(tk)
        rev = rec.revenue if rec else None
        if rev is not None and abs(proceeds - rev) > _REVENUE_TOL:
            _LOG.warning("pm settlement: PROCEEDS cross-check divergence on %s: our proceeds=%.4f vs kalshi "
                         "revenue=%.4f (>tol %.2f) -- hand-inspect the booked realized_pnl", tk, proceeds, rev, _REVENUE_TOL)
    return {"account_id": account_id, "category": category, "n_booked": len(booked), "booked": booked,
            "skipped_flat": skipped_flat, "skipped_no_settlement": skipped_no_settlement}
