#!/usr/bin/env python
"""pv2 FIRST-HOUR SIMULATION -- READ-ONLY (2026-11-17, pre-deploy question: what will the
engine trade in its first hour after the v2 restart?).

Drives the REAL branch code -- live_driver.positions_to_entry_signals ->
execution.detect_opposing_closes -> execution.evaluate (all 8 gates) with the shared
per-account Journal, categories in live_driver.category_volume_order -- against:
  * live Polymarket v2 books (public, the migrated client on this branch),
  * live Kalshi public market catalogs (a public read-only shim client, throttled),
  * the REAL pm DB (config / attachments / journal / 30-day volume), opened mode=ro,
  * the REAL arm state (arm.read_arm_verdict -> mode=ro reads of legacy agent_state).

READ-ONLY GUARANTEES
  - Both DBs opened with sqlite URI mode=ro (a write raises). The legacy trading_corp.db
    is touched ONLY through arm.py's own mode=ro readers.
  - No place_fn exists here; the write paths (_record_order, dry_run_subdivision,
    mark_opposed_contested) are NEVER called.
  - No authenticated endpoint is touched: shard_balances=None + venue_exposure=None (the
    documented paper/test opt-outs of gates 6b/6-rebase). The book is flat, so the venue
    base the real gate 6 would read is $0 == the journal base used here.
  - Kalshi/Polymarket reads are throttled public GETs with a hard budget; first 429 aborts.

STATED ASSUMPTIONS (bias notes in the report):
  1. Every placed IOC fills at its limit. Fills consume the daily/open caps; a no_fill
     would FREE budget and let the next signal in line take a shot (each signal gets
     exactly ONE attempt ever -- the coid is journaled pre-POST even on no_fill). So the
     simulated committed-$ is the cap-bound ceiling; attempt COUNT is a floor.
  2. No new whale activity during the hour (signals are state-based; state = today's books).
  3. Gate 6b (per-shard funding) not simulated: Kalshi auto-rebalances every ~10s and cash
     >> the cap-bound commitment; omitting it can only make FEWER orders (bias down... as
     orders; the $ ceiling is unchanged).
  4. Whale-EXIT signals: none in cycle 1 by construction (boot-seeded empty snapshot);
     later exits require in-hour whale sells (not knowable; assumed none per 2).
Simulated cycles iterate to a fixed point; cycle N's fills seed cycle N+1's Journal
(filled-only re-seed -- exactly the live semantics). Disarm-blocked would-places consume
IN-CYCLE budget but never persist -- also exactly the live semantics.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import re
import sqlite3
import statistics
import sys
import time
import urllib.parse
from collections import Counter, defaultdict
from types import SimpleNamespace

TREE = os.environ.get("PV2FH_TREE", "/home/azureuser/pv2fh_scratch/tree")
sys.path.insert(0, TREE)

PM_DB = os.environ.get("PV2FH_PM_DB", "/home/azureuser/trading_corp/data/prediction_markets.db")
LEGACY = os.environ.get("PV2FH_LEGACY", "/home/azureuser/trading_corp/data/trading_corp.db")
POLY_BUDGET = 250  # Polymarket data-api GETs from the box (the data-api host is NOT IP-shared-throttled)
MAXC = 16  # simulated-cycle bound (converges in ~2-4)

from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient  # noqa: E402
from trading_corp.prediction_markets import arm, execution, live_driver  # noqa: E402


def ro(path):
    c = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=8.0)
    c.row_factory = sqlite3.Row
    return c


# ---------- Kalshi HARVEST shim (duck-types the pykalshi client surface the ctx builders use) -----
# The box IP is SHARED with the live engine's own Kalshi reads; probing the public catalog from it
# drew 429s at ~4/s AND ~1.4/s (two aborted bursts, 171+146 calls, stopped per rules) -- the
# HEARTBEAT_STALL_2026-09-12 shared-IP throttle class. So the catalog was pulled from the operator
# machine's IP instead (the pm-backtest-2026-10-22 precedent: "all Kalshi data pulled from this
# machine off public endpoints") and shipped over: one file per (series, status, page), settled
# pre-filtered with min_close_ts=now-2d at harvest time. ZERO Kalshi calls leave the box.
HARVEST = os.environ.get("PV2FH_HARVEST", "/home/azureuser/pv2fh_scratch/kharvest")


class PubKalshi:
    def __init__(self):
        self.served = 0
        self.raw_markets = {}  # UPPER ticker -> raw /markets row (close_time etc. for the stats)

    def _pages(self, series, status):
        import glob as _g
        files = sorted(_g.glob(os.path.join(HARVEST, "%s_%s_p*.json" % (series, status))),
                       key=lambda p: int(re.search(r"_p(\d+)\.json$", p).group(1)))
        if not files:  # an EMPTY series harvests as a file with zero markets; NO file = not harvested
            raise RuntimeError("HARVEST MISSING for %s/%s -- refusing to serve an empty catalog" % (series, status))
        rows = []
        for fp in files:
            with open(fp) as f:
                rows.extend(json.load(f).get("markets") or [])
        self.served += len(files)
        return rows

    async def get(self, path):  # the builders' raw-merge entry point (path already querystringed)
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
        series = (qs.get("series_ticker") or [""])[0]
        status = (qs.get("status") or ["open"])[0]
        return {"markets": self._pages(series, status), "cursor": ""}

    @staticmethod
    def _status_str(status):
        v = getattr(status, "value", status)
        s = str(v or "").lower()
        return s.rsplit(".", 1)[-1]  # 'marketstatus.open' -> 'open'

    def _obj(self, rm):
        ns = SimpleNamespace()
        ns.ticker = rm.get("ticker")
        ns.title = rm.get("title")
        ns.yes_sub_title = rm.get("yes_sub_title")
        ns.close_time = rm.get("close_time")
        ns.expected_expiration_time = rm.get("expected_expiration_time")
        for f in ("yes_ask", "yes_bid", "no_ask", "no_bid"):
            d = rm.get(f + "_dollars")
            if d is None:
                c = rm.get(f)
                try:
                    d = (float(c) / 100.0) if c is not None else None
                except (TypeError, ValueError):
                    d = None
            setattr(ns, f + "_dollars", d)
        ns.liquidity_dollars = rm.get("liquidity_dollars")
        return ns

    async def get_markets(self, series_ticker=None, status=None, limit=1000, fetch_all=False,
                          min_close_ts=None, **kw):
        # min_close_ts is IGNORED: the harvest already applied now-2d to the settled fetch at
        # harvest time (minutes earlier) -- the same window, computed once.
        out = []
        for rm in self._pages(series_ticker, self._status_str(status) or "open"):
            tk = str(rm.get("ticker") or "")
            if tk:
                self.raw_markets.setdefault(tk.upper(), rm)
                out.append(self._obj(rm))
        return out


# ---------- ticker event-datetime parse (ET) for the in-progress estimate ------------------------
_TK_DT = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(\d{4})?")
_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def ticker_event_ts(ticker):
    """Best-effort event START epoch from the ticker's encoded ET datetime (KXMLBGAME-26AUG301920...).
    Returns (ts, has_time): date-only tickers get 00:00 ET (date known, start unknown)."""
    m = _TK_DT.search(ticker or "")
    if not m:
        return None, False
    yy, mon, dd, hhmm = m.groups()
    try:
        y, mo, d = 2000 + int(yy), _MON[mon], int(dd)
        hh, mi = (int(hhmm[:2]), int(hhmm[2:])) if hhmm else (0, 0)
        # ET ~ UTC-5 in November (EST). Good to the hour for an in-progress estimate.
        return int(_dt.datetime(y, mo, d, hh, mi, tzinfo=_dt.timezone.utc).timestamp()) + 5 * 3600, bool(hhmm)
    except Exception:
        return None, False


def iso_ts(s):
    try:
        return int(_dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


async def main():
    now = int(time.time())
    conn = ro(PM_DB)
    out = {"now": now, "now_iso": _dt.datetime.fromtimestamp(now, _dt.timezone.utc).isoformat()}

    # ---- roster, config, attachments, arm state (ALL accounts) ----------------------------------
    subs_rows = conn.execute(
        "SELECT s.account_id, s.category, s.active, s.market_types, s.sizing_mode, s.contracts, "
        " s.fixed_stake_usd, s.per_order_usd_cap, s.daily_usd_cap, s.max_open_usd, s.max_orders_per_day, "
        " s.max_slippage_cents, s.liquidity_ratio, "
        " (SELECT COUNT(*) FROM pm_subdivision_attachment at WHERE at.account_id=s.account_id "
        "   AND at.category=s.category AND at.active=1) AS n_attach "
        "FROM pm_subdivision s JOIN pm_account a ON a.account_id=s.account_id AND a.active=1 "
        "WHERE s.active=1 ORDER BY s.account_id, s.category").fetchall()
    cats_by_acct = defaultdict(list)
    subcfg = {}
    cfgdump = []
    for r in subs_rows:
        aid, cat = r["account_id"], r["category"]
        if r["n_attach"] > 0:  # the driver-roster gate: >=1 active attachment
            cats_by_acct[aid].append(cat)
        subcfg[(aid, cat)] = execution.sub_config_from_row(r)
        cfgdump.append({k: r[k] for k in r.keys()})
    out["pm_subdivision"] = cfgdump

    armmap, armdump = {}, []
    for r in subs_rows:
        aid, cat = r["account_id"], r["category"]
        v = arm.read_arm_verdict(aid, cat, legacy_db_path=LEGACY)
        armmap[(aid, cat)] = bool(v.armed)
        armdump.append({"account": aid, "category": cat, "armed": bool(v.armed),
                        "reason": v.reason, "latched": bool(v.latched), "auto_trigger": v.auto_trigger})
    gl = arm.current_row(global_=True, legacy_db_path=LEGACY) or {}
    out["arm_global"] = {"armed": bool(gl.get("armed")), "ts": gl.get("ts")}
    out["arm"] = armdump

    # ---- cash, flat-book check, journal baselines ----------------------------------------------
    out["cash"] = [dict(r) for r in conn.execute(
        "SELECT account_id, total_dollars, has_breakdown, snapshot_ts FROM pm_shard_balance_snapshot s1 "
        "WHERE snapshot_ts=(SELECT MAX(snapshot_ts) FROM pm_shard_balance_snapshot s2 "
        " WHERE s2.account_id=s1.account_id) ORDER BY account_id").fetchall()]
    out["net_open_legs"] = execution.open_positions_needing_manual_exit(conn)  # expect []
    out["journal"] = [dict(r) for r in conn.execute(
        "SELECT account_id, COUNT(*) AS coids, "
        " SUM(CASE WHEN outcome_status='filled' AND is_exit=0 AND response_ts>=? THEN 1 ELSE 0 END) AS filled_today "
        "FROM pm_subdivision_order WHERE dry_run=0 AND client_order_id IS NOT NULL "
        "GROUP BY account_id", (now - (now % 86400),)).fetchall()]

    # ---- category claim order (the real function, real 30-day window) ---------------------------
    volorder = {}
    for aid, cats in sorted(cats_by_acct.items()):
        volorder[aid] = live_driver.category_volume_order(conn, aid, cats, now_ts=now)
    out["claim_order"] = volorder

    sim_accounts = [a for a in sorted(cats_by_acct) if any(armmap.get((a, c)) for c in cats_by_acct[a])]
    out["sim_accounts"] = sim_accounts
    out["latched_only_accounts"] = [a for a in sorted(cats_by_acct) if a not in sim_accounts]

    # ---- Polymarket books (jack+karen's attached wallets; cached) -------------------------------
    books, poly_calls = {}, 0
    wallets_by = {}
    for aid in sim_accounts:
        for cat in cats_by_acct[aid]:
            ws = [r["wallet"] for r in conn.execute(
                "SELECT wallet FROM pm_subdivision_attachment WHERE account_id=? AND category=? AND active=1",
                (aid, cat)).fetchall()]
            wallets_by[(aid, cat)] = ws
    uniq = sorted({w for ws in wallets_by.values() for w in ws})
    incomplete = []
    async with PolymarketDataAPIClient() as poly:
        for w in uniq:
            if poly_calls >= POLY_BUDGET:
                raise RuntimeError("POLYMARKET CALL BUDGET EXCEEDED (%d)" % POLY_BUDGET)
            book = await poly.fetch_positions_book(w)
            poly_calls += getattr(book, "pages", 1)
            books[w] = book
            if not getattr(book, "complete", False):
                incomplete.append(w)
    out["poly"] = {"wallets": len(uniq), "calls": poly_calls, "incomplete": incomplete}

    # ---- signals per (account, category) + source-row lookup ------------------------------------
    sig_by, rowlut, guard_stats = {}, {}, {}
    for (aid, cat), ws in wallets_by.items():
        sigs = []
        for w in ws:
            book = books.get(w)
            if book is None or not getattr(book, "complete", False):
                continue  # the live loop never acts on a partial book
            ss = live_driver.positions_to_entry_signals(book.rows, w)
            sigs.extend(ss)
            for p in book.rows:
                from trading_corp.prediction_markets import paper as _paper
                rowlut[(w, str(getattr(p, "condition_id", "")), _paper.pos_outcome_index(p))] = p
        held = execution.account_held_outcomes(conn, aid, cat)       # {} on a flat book
        mem = execution.account_opposed_cids(conn, aid, cat)
        kept, closes, contested, pre = execution.detect_opposing_closes(sigs, held, mem)
        guard_stats[(aid, cat)] = {"raw": len(sigs), "kept": len(kept),
                                   "contested_mkts": len(contested), "preexisting": len(pre)}
        sig_by[(aid, cat)] = kept + closes                            # closes==[] on a flat book

    # ---- Kalshi market contexts (one per category with signals; shared across accounts) ---------
    kc = PubKalshi()
    ctx_by = {}
    need = sorted({c for (a, c), s in sig_by.items() if s})
    for cat in need:
        bld = live_driver.CATEGORY_CTX_BUILDERS.get(cat)
        if bld is None:
            ctx_by[cat] = None  # the live loop skips it (fail-safe) -- reported
            continue
        t0 = kc.served
        ctx_by[cat] = await bld(kc, now)
        print("  ctx[%s]: %d harvest files" % (cat, kc.served - t0), flush=True)
    out["kalshi_calls"] = {"box_live_calls": 0, "harvest_files_served": kc.served}
    out["ctx_missing_builder"] = [c for c in need if ctx_by.get(c) is None]

    # ---- the cycle simulation to fixed point ----------------------------------------------------
    simfill = defaultdict(lambda: {"usd": 0.0, "n": 0})
    simcoids = set()
    placed_detail = []
    chase_detail = []   # gate C (chase, 2026-11-17): WHICH signals the new gate blocks, and why (cycle 1)
    percycle = []
    first_cycle_tally = {}
    for cycle in range(1, MAXC + 1):
        newly = []
        tally = defaultdict(Counter)
        for aid in sim_accounts:
            j = execution.Journal(conn, [aid], now)
            for (a, c), st in simfill.items():
                if a != aid:
                    continue
                j._daily_usd[(a, c)] = j._daily_usd.get((a, c), 0.0) + st["usd"]
                j._orders_today[(a, c)] = j._orders_today.get((a, c), 0) + st["n"]
                j._open_usd[aid] = j._open_usd.get(aid, 0.0) + st["usd"]
            j._open_usd_seed = dict(j._open_usd)
            j._placed_coids |= simcoids
            for cat in volorder[aid]:
                sub = subcfg[(aid, cat)]
                ctx = ctx_by.get(cat)
                if ctx is None:
                    continue
                for s in sig_by.get((aid, cat), []):
                    d = execution.evaluate(s, sub, ctx, j, conn, now, shard_balances=None,
                                           venue_exposure=None, legacy_db_path=LEGACY)
                    tally[aid][d.status] += 1
                    if cycle == 1 and d.status in ("skip:chase", "skip:chase_unpriced"):
                        chase_detail.append({
                            "account": aid, "category": cat, "wallet": s.wallet[:10],
                            "ticker": d.kalshi_ticker, "leg": d.leg, "count": d.count,
                            "whale_avg_price": s.whale_avg_price,
                            "whale_first_entry_ts": s.whale_first_entry_ts,
                            "reason": d.reason})
                    if d.status == "dry_run_would_place":
                        if armmap.get((aid, cat)):
                            newly.append((aid, cat, s, d))
                        else:
                            tally[aid]["disarm_blocked"] += 1
        if cycle == 1:
            first_cycle_tally = {a: dict(c) for a, c in tally.items()}
        for aid, cat, s, d in newly:
            simfill[(aid, cat)]["usd"] += float(d.notional_usd or 0.0)
            simfill[(aid, cat)]["n"] += 1
            simcoids.add(d.client_order_id)
            p = rowlut.get((s.wallet, s.condition_id, s.outcome_index))
            ex = getattr(p, "extra", {}) or {}
            raw = kc.raw_markets.get((d.kalshi_ticker or "").upper(), {})
            ev_ts, has_time = ticker_event_ts(d.kalshi_ticker)
            close_ts = iso_ts(raw.get("close_time"))
            leg_cost = d.price if d.leg == "yes" else (1.0 - d.price)
            placed_detail.append({
                "cycle": cycle, "account": aid, "category": cat, "wallet": s.wallet[:10],
                "ticker": d.kalshi_ticker, "leg": d.leg, "count": d.count, "yes_price": d.price,
                "leg_cost": round(leg_cost, 4), "notional": round(float(d.notional_usd or 0), 4),
                "whale_avg_price": getattr(p, "avg_price", None),
                "whale_cur_price": ex.get("curPrice"), "end_date": ex.get("endDate"),
                "last_event_at": ex.get("last_event_at"),
                "first_entry_at": ex.get("first_entry_at"),   # v2 TRUE entry ts (exact age, no proxy)
                "event_ts": ev_ts, "event_has_time": has_time, "kalshi_close_ts": close_ts,
                "kalshi_status": raw.get("status"),
            })
        percycle.append({"cycle": cycle, "placed": len(newly),
                         "usd": round(sum(float(d.notional_usd or 0) for *_x, d in newly), 2)})
        print("  cycle %d: %d placed ($%.2f)" % (cycle, len(newly), percycle[-1]["usd"]), flush=True)
        if not newly:
            break

    out["first_cycle_tally"] = first_cycle_tally
    out["guard"] = {"%s/%s" % k: v for k, v in guard_stats.items()}
    out["per_cycle"] = percycle
    out["placed"] = placed_detail
    out["committed"] = {aid: {"usd": round(sum(st["usd"] for (a, c), st in simfill.items() if a == aid), 2),
                              "orders": sum(st["n"] for (a, c), st in simfill.items() if a == aid),
                              "by_cat": {c: {"usd": round(st["usd"], 2), "n": st["n"]}
                                         for (a, c), st in simfill.items() if a == aid and st["n"]}}
                       for aid in sim_accounts}

    out["chase_blocked"] = chase_detail

    # ---- the placed-set age / price / market-state stats ----------------------------------------
    # exact ages via v2 first_entry_at (TRUE entry ts) where present; last_event_at fallback
    ages = [(now - int(p["first_entry_at"] or p["last_event_at"])) / 3600.0 for p in placed_detail
            if (p.get("first_entry_at") or p.get("last_event_at"))
            and int(p.get("first_entry_at") or p["last_event_at"]) > 1e9]
    drift = [p["leg_cost"] - float(p["whale_avg_price"]) for p in placed_detail
             if p.get("whale_avg_price")]
    started = [p for p in placed_detail if p.get("event_ts") and p["event_has_time"] and p["event_ts"] <= now]
    nostart = [p for p in placed_detail if not (p.get("event_ts") and p["event_has_time"])]
    nearclose = [p for p in placed_detail if p.get("kalshi_close_ts") and 0 <= p["kalshi_close_ts"] - now <= 3600]
    pastend = [p for p in placed_detail if p.get("end_date") and (iso_ts(p["end_date"]) or 9e18) < now]
    out["placed_stats"] = {
        "n": len(placed_detail),
        "age_h": {
            "n": len(ages), "median": round(statistics.median(ages), 1) if ages else None,
            "min": round(min(ages), 1) if ages else None, "max": round(max(ages), 1) if ages else None,
            "gt_24h": sum(1 for a in ages if a > 24), "gt_7d": sum(1 for a in ages if a > 168),
            "caveat": "age via v2 first_entry_at (TRUE entry ts) where present; last_event_at fallback"},
        "leg_cost_minus_whale_avg": {
            "n": len(drift), "median": round(statistics.median(drift), 4) if drift else None,
            "min": round(min(drift), 4) if drift else None, "max": round(max(drift), 4) if drift else None,
            "paying_more_than_whale": sum(1 for x in drift if x > 0)},
        "event_started_per_ticker_time": {"n": len(started), "no_start_time_in_ticker": len(nostart)},
        "kalshi_close_within_1h": len(nearclose),
        "poly_end_date_in_past": len(pastend),
    }
    if placed_detail:
        samp = rowlut.get(next(iter(rowlut)))
        out["sample_extra_keys"] = sorted((getattr(samp, "extra", {}) or {}).keys())

    with open(os.environ.get("PV2FH_OUT", "/home/azureuser/pv2fh_scratch/pv2fh_report.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    print("\n==== SUMMARY ====")
    print(json.dumps({k: out[k] for k in ("committed", "per_cycle", "placed_stats", "chase_blocked",
                                          "claim_order", "cash", "poly", "kalshi_calls", "sim_accounts",
                                          "latched_only_accounts")}, indent=1, default=str))
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
