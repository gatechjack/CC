#!/usr/bin/env python3
"""CLI for the SECOND, INDEPENDENT whale scorer: recency-weighted realized edge.

Reads the same realized data the primary pipeline uses (closed-positions spine +
audit-core fills for clean-hold), emits its OWN score, and NEVER calls, modifies,
or gates the primary selection script. Join its output to the primary's
composite_score (by wallet) for the 2x2 durability x recency view.

STRICTLY READ-ONLY: public Polymarket data-api + a read of agent_state for the
roster. No roster writes, no promote/demote, no LLM.

Data design (realized basis throughout):
  * SPINE  = /closed-positions  -> complete resolved history with realized_pnl +
             resolution timestamp (the decay anchor) + avg entry price.
  * ENRICH = /activity -> group_fills_by_decision -> per-decision sell_share
             (clean-hold) + held_to_resolution_pnl (held-inflation), joined by
             (condition_id, outcome_index). Recent trades (which dominate the
             decayed score) are in-window; older trades degrade to realized-only.
  * BASIS ASSERTION: on the overlap, sum(closed realized) / sum(audit realized)
             must be ~1.00x (the GreatestTrader calibration) or the two lenses
             are not basis-consistent for the 2x2 join -- reported per whale.

Usage (run from the trading_corp repo root so trading_corp.* resolves):
  python -m trading_corp.scripts.score_whale_recency --only Hakei --half-life-days 45
  python -m trading_corp.scripts.score_whale_recency --only "llllll,DegenKingBetter,ox1star84" --sweep 30,45,60,90 --as-of 2026-08-15
  python -m trading_corp.scripts.score_whale_recency --wallets "0xabc=Name" --half-life-days 45
  python -m trading_corp.scripts.score_whale_recency --roster --join primary_scores.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

# Dual import: in-repo it's a submodule; streamed to prod /tmp it's top-level.
try:
    from trading_corp.scripts.polymarket_whale_recency import (
        DEFAULT_HALF_LIFE_DAYS, ResolvedTrade, score_recency,
    )
except ModuleNotFoundError:  # pragma: no cover - prod temp-run path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from polymarket_whale_recency import (  # type: ignore
        DEFAULT_HALF_LIFE_DAYS, ResolvedTrade, score_recency,
    )

from trading_corp.data.polymarket_data_api_client import (
    PolymarketDataAPIClient, PolymarketDataAPIError,
)
from trading_corp.data.polymarket_whale_audit import (
    DEFAULT_PARTIAL_SELL_THRESHOLD, group_fills_by_decision,
)
from trading_corp.persistence.db import load_agent_state

_AGENT = "polymarket_copy_trader"
_ACT_PAGE = 500
_CP_PAGE = 50
_ROSTER_KEYS = ("selected_whales", "pinned_whales", "watch_only_whales")


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _iso(ts: int) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _parse_as_of(arg: str | None) -> int:
    if not arg:
        return int(time.time())
    arg = arg.strip()
    if arg.isdigit():
        return int(arg)
    return int(datetime.strptime(arg, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


# ---- roster resolution (read-only) -----------------------------------------
def _load_name_wallet(db_url: str) -> list[tuple[str, str, str]]:
    """Returns [(user_name, wallet, roster_key)] across selected/pinned/watch."""
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for key in _ROSTER_KEYS:
        rec = load_agent_state(_AGENT, key, db_url=db_url)
        val = rec[0] if rec else None
        if not isinstance(val, list):
            continue
        for v in val:
            if isinstance(v, dict) and v.get("wallet"):
                w = str(v["wallet"]).lower()
                if w in seen:
                    continue
                seen.add(w)
                out.append((str(v.get("user_name", "")), w, key))
    return out


def _resolve_whales(only: str, wallets: str, roster: bool, db_url: str):
    """Returns [(user_name, wallet)]. --wallets 'addr=name,...' wins; else --only
    substring match against the roster; else --roster = all."""
    if wallets:
        out = []
        for pair in wallets.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "=" in pair:
                addr, name = pair.split("=", 1)
            else:
                addr, name = pair, pair[:10]
            out.append((name.strip(), addr.strip().lower()))
        return out

    roster_list = _load_name_wallet(db_url)
    if roster:
        return [(n, w) for (n, w, _k) in roster_list]

    if not only:
        _log("ERROR: pass --only <names>, --wallets <addr=name>, or --roster")
        return []
    out = []
    for token in only.split(","):
        t = token.strip().lower()
        if not t:
            continue
        matches = [(n, w) for (n, w, _k) in roster_list if t in n.lower()]
        if not matches:
            _log(f"  WARN --only '{token}' matched no roster whale")
        elif len(matches) > 1:
            _log(f"  NOTE '{token}' matched {len(matches)}: {[m[0] for m in matches]} (using all)")
            out.extend(matches)
        else:
            out.append(matches[0])
    # de-dupe by wallet, preserve order
    seen, uniq = set(), []
    for n, w in out:
        if w not in seen:
            seen.add(w)
            uniq.append((n, w))
    return uniq


# ---- fetch (read-only) -----------------------------------------------------
async def _fetch_activity_all(client, wallet, *, max_pages):
    out, hit = [], False
    for i in range(max_pages):
        try:
            page = await client.fetch_activity(wallet, limit=_ACT_PAGE, offset=i * _ACT_PAGE)
        except PolymarketDataAPIError as e:
            _log(f"    activity offset={i*_ACT_PAGE} stopped: {e}")
            break
        if not page:
            break
        out.extend(page)
        if len(page) < _ACT_PAGE:
            break
    else:
        hit = True
    return out, hit


async def _fetch_closed_all(client, wallet, *, max_pages=80):
    out = []
    for i in range(max_pages):
        try:
            page = await client.fetch_closed_positions(wallet, limit=_CP_PAGE, offset=i * _CP_PAGE)
        except PolymarketDataAPIError as e:
            _log(f"    closed-positions offset={i*_CP_PAGE} stopped: {e}")
            break
        if not page:
            break
        out.extend(page)
        if len(page) < _CP_PAGE:
            break
        await asyncio.sleep(0.4)
    return out


async def _gather_whale(client, name, wallet, *, max_pages):
    """Build the ResolvedTrade spine + whale-level guards. Read-only."""
    closed = await _fetch_closed_all(client, wallet)
    activity, act_capped = await _fetch_activity_all(client, wallet, max_pages=max_pages)
    cids = {a.condition_id for a in activity if a.condition_id}
    resolutions = await client.fetch_market_resolutions(list(cids)) if cids else {}
    decisions = group_fills_by_decision(activity, resolutions) if activity else {}

    dec_by_key = {}
    held_total = realized_window = 0.0
    for (cid, oi), d in decisions.items():
        if not d.is_resolved:
            continue
        dec_by_key[(cid, oi)] = d
        held_total += d.held_to_resolution_pnl
        realized_window += d.realized_pnl
    held_inflation_ratio = ((held_total - realized_window) / max(held_total, 1.0)
                            if held_total else 0.0)

    # Basis rule: use AUDIT realized (redeem-grounded, == the primary's basis) for
    # in-window trades so the recency score is basis-consistent with the primary
    # for the 2x2 join. closed-positions realized is used ONLY for the out-of-window
    # tail (older than the ~5,500-fill activity window), which carries little decayed
    # weight. Resolution timestamp always from closed-positions (the audit core has
    # none). calib_ratio reports closed-vs-audit divergence on the overlap as a
    # diagnostic -- it is ~1.00x for clean-hold whales but < 1 for partial-sell
    # whales (closed-positions accounts partial sells differently).
    trades = []
    ov_closed = ov_audit = 0.0
    n_overlap = 0
    for cp in closed:
        key = (cp.condition_id, cp.outcome_index)
        d = dec_by_key.get(key)
        clean = held = None
        realized = cp.realized_pnl               # out-of-window fallback (closed basis)
        if d is not None:
            clean = d.sell_share < DEFAULT_PARTIAL_SELL_THRESHOLD
            held = d.held_to_resolution_pnl
            realized = d.realized_pnl             # in-window: AUDIT basis (primary-consistent)
            ov_closed += cp.realized_pnl
            ov_audit += d.realized_pnl
            n_overlap += 1
        trades.append(ResolvedTrade(
            condition_id=cp.condition_id, outcome_index=cp.outcome_index,
            realized_pnl=realized, resolution_ts=cp.timestamp,
            avg_price=cp.avg_price, clean_hold=clean, held_pnl=held,
        ))
    calib_ratio = (ov_closed / ov_audit) if abs(ov_audit) > 1e-6 else None
    return {
        "name": name, "wallet": wallet, "trades": trades,
        "held_inflation_ratio": held_inflation_ratio,
        "n_closed": len(closed), "n_activity": len(activity), "activity_capped": act_capped,
        "n_overlap": n_overlap, "calib_ratio": calib_ratio,
    }


# ---- output ----------------------------------------------------------------
def _fmt_ratio(r):
    return "  n/a" if r is None else f"{r:5.2f}"


def _score_row(g, half_life, as_of):
    return score_recency(
        g["wallet"], g["name"], g["trades"],
        half_life_days=half_life, as_of_ts=as_of,
        held_inflation_ratio=g["held_inflation_ratio"],
    )


def _print_single(gathered, half_life, as_of):
    scores = sorted((_score_row(g, half_life, as_of) for g in gathered),
                    key=lambda s: -s.rw_realized)
    print("=" * 108)
    print(f"RECENCY SCORES  half_life={half_life}d  as_of={_iso(as_of)}  (rank = rw_realized desc)")
    print(f"{'whale':<20}{'nRes':>5}{'recN':>5}{'rw_realized':>12}{'rw_mean':>9}"
          f"{'ratio':>7}{'trend':>13}{'cleanHld%':>10}{'favWt':>7}{'heldInf':>8}{'lastAct':>12}{'calib':>7}")
    for s in scores:
        chs = "  n/a" if s.rw_clean_hold_share is None else f"{100*s.rw_clean_hold_share:4.0f}%"
        hi = "  n/a" if s.held_inflation_ratio is None else f"{s.held_inflation_ratio:6.2f}"
        cr = next((_fmt_ratio(g["calib_ratio"]) for g in gathered if g["wallet"] == s.wallet), " n/a")
        print(f"{s.user_name[:19]:<20}{s.n_resolved:>5}{s.recent_n:>5}{s.rw_realized:>12.0f}"
              f"{s.rw_realized_mean:>9.1f}{_fmt_ratio(s.recent_vs_lifetime):>7}{s.trend:>13}"
              f"{chs:>10}{100*s.favorite_farm_weighted:>6.0f}%{hi:>8}{_iso(s.last_active_ts):>12}{cr:>7}")
    return scores


def _print_sweep(gathered, half_lives, as_of):
    print("=" * 108)
    print(f"CALIBRATION SWEEP  as_of={_iso(as_of)}  half_lives={half_lives}")
    print("Per-whale facts (half-life-independent):")
    print(f"{'whale':<20}{'nClosed':>8}{'nOverlap':>9}{'actCap':>7}{'lastAct':>12}{'heldInf':>8}{'calib(closed/audit)':>20}")
    for g in gathered:
        s0 = _score_row(g, half_lives[0], as_of)
        print(f"{g['name'][:19]:<20}{g['n_closed']:>8}{g['n_overlap']:>9}"
              f"{str(g['activity_capped']):>7}{_iso(s0.last_active_ts):>12}"
              f"{g['held_inflation_ratio']:>8.2f}{_fmt_ratio(g['calib_ratio']):>20}")
    print("-" * 108)
    print("Trend / recent_vs_lifetime ratio / rw_realized_mean at each half-life:")
    header = f"{'whale':<20}" + "".join(f"{'HL='+str(h):>22}" for h in half_lives)
    print(header)
    for g in gathered:
        cells = []
        for h in half_lives:
            s = _score_row(g, h, as_of)
            r = "n/a" if s.recent_vs_lifetime is None else f"{s.recent_vs_lifetime:.2f}"
            cells.append(f"{s.trend[:8]:>8} r={r:>5} m={s.rw_realized_mean:>5.0f}")
        print(f"{g['name'][:19]:<20}" + "".join(f"{c:>22}" for c in cells))
    print("-" * 108)
    print("Rank by rw_realized at each half-life (1 = highest recency-weighted edge):")
    for h in half_lives:
        ranked = sorted(gathered, key=lambda g: -_score_row(g, h, as_of).rw_realized)
        order = " > ".join(f"{g['name'][:14]}" for g in ranked)
        print(f"  HL={h:>3}: {order}")
    print("\nHOW TO READ: pick the half-life where a KNOWN-fading whale is flagged 'fading'/'dormant'")
    print("while KNOWN-durable whales stay 'steady'. If no half-life separates them cleanly, that is")
    print("a finding (report it) rather than a forced default. Ratio<1 = fading, >1 = accelerating.")


def _print_join(scores, join_path):
    with open(join_path, encoding="utf-8-sig") as f:
        prim = json.load(f)
    # accept {wallet: composite} or {wallet: {composite_score: ..}}
    def comp(w):
        v = prim.get(w) or prim.get(w.lower())
        if isinstance(v, dict):
            return v.get("composite_score")
        return v
    rows = [(s, comp(s.wallet)) for s in scores]
    rows = [(s, c) for s, c in rows if c is not None]
    if not rows:
        _log("  --join: no wallet overlap with primary scores; skipping 2x2")
        return
    import statistics
    dmed = statistics.median([c for _s, c in rows])
    rmed = statistics.median([s.rw_realized for s, _c in rows])
    print("=" * 108)
    print(f"2x2 durability(primary composite, median={dmed:.3f}) x recency(rw_realized, median={rmed:.0f})")
    quad = {"keepers": [], "fading": [], "discovery": [], "ignore": []}
    for s, c in rows:
        hi_d, hi_r = c >= dmed, s.rw_realized >= rmed
        q = ("keepers" if hi_d and hi_r else "fading" if hi_d and not hi_r
             else "discovery" if not hi_d and hi_r else "ignore")
        quad[q].append(f"{s.user_name}({s.trend})")
    for q, label in (("keepers", "hi-dur hi-rec  KEEPERS"),
                     ("fading", "hi-dur lo-rec  FADING (decline-detect)"),
                     ("discovery", "lo-dur hi-rec  DISCOVERY"),
                     ("ignore", "lo-dur lo-rec  IGNORE")):
        print(f"  {label:<38}: {', '.join(quad[q]) or '-'}")


async def _main_async(args):
    as_of = _parse_as_of(args.as_of)
    whales = _resolve_whales(args.only, args.wallets, args.roster, args.db_url)
    if not whales:
        return 2
    _log(f"[recency] scoring {len(whales)} whale(s) as_of={_iso(as_of)}: "
         f"{[n for n, _w in whales]}")

    gathered = []
    async with PolymarketDataAPIClient() as client:
        for i, (name, wallet) in enumerate(whales):
            _log(f"[{i+1}/{len(whales)}] {name or wallet[:10]} ...")
            try:
                g = await _gather_whale(client, name, wallet, max_pages=args.max_pages)
            except Exception as e:  # noqa: BLE001
                _log(f"    ERROR {type(e).__name__}: {e}")
                continue
            _log(f"    closed={g['n_closed']} activity={g['n_activity']} "
                 f"overlap={g['n_overlap']} calib={g['calib_ratio']}")
            gathered.append(g)
            if i + 1 < len(whales):
                await asyncio.sleep(args.sleep)

    if not gathered:
        _log("no whale data gathered")
        return 2

    half_lives = ([float(x) for x in args.sweep.split(",")] if args.sweep
                  else None)
    if half_lives:
        _print_sweep(gathered, half_lives, as_of)
    scores = _print_single(gathered, args.half_life_days, as_of)

    if args.join:
        _print_join(scores, args.join)

    if args.output_json:
        payload = {"as_of": as_of, "half_life_days": args.half_life_days,
                   "scores": [vars(s) for s in scores]}
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, default=str, indent=2)
        _log(f"wrote {args.output_json}")
    return 0


def _build_parser():
    p = argparse.ArgumentParser(prog="score_whale_recency")
    p.add_argument("--only", default="", help="comma names (substring match against roster)")
    p.add_argument("--wallets", default="", help="comma 'addr=name' pairs (bypass roster)")
    p.add_argument("--roster", action="store_true", help="score all selected+pinned+watch")
    p.add_argument("--half-life-days", type=float, default=DEFAULT_HALF_LIFE_DAYS)
    p.add_argument("--sweep", default="", help="comma half-lives, e.g. 30,45,60,90")
    p.add_argument("--as-of", default="", help="ISO YYYY-MM-DD or unix secs; default now")
    p.add_argument("--join", default="", help="path to primary {wallet: composite} JSON for the 2x2")
    p.add_argument("--max-pages", type=int, default=120, help="activity pages x500 (clean-hold window)")
    p.add_argument("--sleep", type=float, default=1.0)
    p.add_argument("--db-url", default="sqlite:///data/trading_corp.db")
    p.add_argument("--output-json", default="")
    return p


def main():
    return asyncio.run(_main_async(_build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
