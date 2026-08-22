#!/usr/bin/env python3
# pm_realizedpnl_probe.py -- Prediction Markets P1 PRE-BUILD characterization of
# /closed-positions realizedPnl semantics: is realizedPnl PER-LEG REAL, or MIRRORED
# across negRisk legs (which would inflate per-category net_pnl/roi by ~leg count)?
# READ-ONLY. Reuses PolymarketDataAPIClient (NO hand-rolled HTTP). No DB, no writes,
# no engine contact. Evidence-first output. Probes A-D per Jack's spec 2026-08-22.
import asyncio
import re
from collections import defaultdict

from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient

DATE = re.compile(r"-\d{4}-\d{2}-\d{2}$")

EVANNG = "0x43e0f84fe8fb4623a5ff485fe9f7bc0f4b458618"
D1K21 = "0x71ed0bc95433cdf1be29f43219725fce9addd9eb"
KICKSTAND7 = "0xd1acd3925d895de9aec98ff95f3a30c5279d08d5"
PAKO = "0x71edffd0d70a1da823ff07a3c6fc81457294d338"

CAT_PREFIXES = ["mlb", "nba", "nfl", "nhl", "ufc", "cs2", "atp", "wta", "cbb",
                "fifwc", "epl", "ucl", "nascar", "f1"]


def category_of(row):
    s = (row.event_slug or row.slug or "").lower()
    if s.startswith("fed-decision") or s.startswith("fed-interest-rates") or s.startswith("fed-rate") or s.startswith("fed-"):
        return "fed"
    for p in CAT_PREFIXES:
        if s.startswith(p + "-") or s == p:
            return p
    return (s.split("-")[0] if s else "?")


def is_ufc_single(slug):
    s = slug or ""
    return s.startswith("ufc-") and DATE.search(s) is not None and "-go-the-distance" not in s


def is_ufc_broad(slug):
    return (slug or "").startswith("ufc-")


def is_fed_event(eslug):
    e = (eslug or "").lower()
    if "ecb" in e:
        return False
    return e.startswith("fed-decision-in") or e.startswith("fed-interest-rates-") or e.startswith("fed-rate")


def money(x):
    return "%+.2f" % x


def act_amt(a):
    v = a.usdc_size
    if not v:
        v = a.size * a.price
    return v or 0.0


async def pull_closed(c, wallet, cap=8000):
    rows = []
    for off in range(0, cap, 50):
        page = await c.fetch_closed_positions(wallet, limit=50, offset=off)
        if not page:
            break
        rows.extend(page)
        if len(page) < 50:
            break
    return rows


async def pull_activity(c, wallet, cap=5000):
    rows = []
    trunc = False
    for off in range(0, cap, 500):
        page = await c.fetch_activity(wallet, limit=500, offset=off)
        if not page:
            break
        rows.extend(page)
        if len(page) < 500:
            break
        if off >= cap - 500:
            trunc = True
    return rows, trunc


async def probe_a(c):
    print("\n\n########## PROBE A: evanng closed-positions vs activity reconciliation ##########")
    rows = await pull_closed(c, EVANNG)
    allsum = sum(r.realized_pnl for r in rows)
    print("evanng closed-positions total rows=%d  ALL-CATEGORY sum_realized_pnl=%s" % (len(rows), money(allsum)))
    cat = defaultdict(lambda: [0, 0.0])
    for r in rows:
        k = category_of(r)
        cat[k][0] += 1
        cat[k][1] += r.realized_pnl
    print("PER-CATEGORY breakdown (event_slug/slug prefix):")
    for k in sorted(cat, key=lambda k: cat[k][0], reverse=True):
        print("   %-10s n=%-4d sum_realized_pnl=%s" % (k, cat[k][0], money(cat[k][1])))
    f1 = [r for r in rows if is_ufc_single(r.slug)]
    f2 = [r for r in rows if is_ufc_broad(r.slug)]
    s1 = sum(r.realized_pnl for r in f1)
    s2 = sum(r.realized_pnl for r in f2)
    print("\nUFC F1 (single-fight ML ufc-...-YYYY-MM-DD, excl -go-the-distance): n=%d sum_realized_pnl=%s" % (len(f1), money(s1)))
    print("UFC F2 (broad startswith 'ufc-' == scout STAGE-2 scoring filter):     n=%d sum_realized_pnl=%s" % (len(f2), money(s2)))
    print("scout activity-method figure (STAGE-2 broad ufc-, from SCOUT_RESULTS): -13706.51")
    print("delta closed-F2 vs scout = %s   delta closed-F1 vs scout = %s" % (money(s2 - (-13706.51)), money(s1 - (-13706.51))))
    acts, trunc = await pull_activity(c, EVANNG)
    print("\nevanng activity rows pulled=%d (truncated_at_5000=%s)" % (len(acts), trunc))
    act = defaultdict(lambda: {"buy": 0.0, "sell": 0.0, "redeem": 0.0})
    for a in acts:
        if not (a.slug or "").startswith("ufc-"):
            continue
        d = act[a.condition_id]
        amt = act_amt(a)
        if a.type == "TRADE" and a.side == "BUY":
            d["buy"] += amt
        elif a.type == "TRADE" and a.side == "SELL":
            d["sell"] += amt
        elif a.type == "REDEEM":
            d["redeem"] += amt
    closed_by_cid = {r.condition_id: r for r in f2}
    print("\nPER-CID reconciliation (UFC cids in BOTH closed-positions & activity):")
    print("  %-18s %16s %16s %12s" % ("cid[:18]", "closed_realized", "act(S+R-B)", "delta"))
    both = 0
    sum_c = 0.0
    sum_x = 0.0
    for cid, r in closed_by_cid.items():
        if cid in act:
            d = act[cid]
            xnet = d["sell"] + d["redeem"] - d["buy"]
            both += 1
            sum_c += r.realized_pnl
            sum_x += xnet
            if both <= 20:
                print("  %-18s %16.2f %16.2f %12.2f" % (cid[:18], r.realized_pnl, xnet, r.realized_pnl - xnet))
    print("  matched_cids=%d" % both)
    print("  AGGREGATE matched: Sum closed_realized=%s  Sum activity(S+R-B)=%s  delta=%s" % (money(sum_c), money(sum_x), money(sum_c - sum_x)))
    ok = both > 0 and abs(sum_c - sum_x) < max(50.0, 0.02 * abs(sum_x) if sum_x else 50.0)
    print("VERDICT-A: closed-positions UFC realized_pnl %s activity(S+R-B) per-cid (tolerance ~fees~0 + rounding + open-excl)" % ("RECONCILES with" if ok else "DOES NOT reconcile with"))


async def probe_b(c):
    print("\n\n########## PROBE B: d1k21 election -574604.31 rows FULL FIELD DUMP ##########")
    rows = await pull_closed(c, D1K21)
    print("d1k21 closed-positions total rows=%d" % len(rows))
    TARGET = -574604.31
    targ = [r for r in rows if abs(r.realized_pnl - TARGET) < 0.005]
    elec = [r for r in rows if "win the 2024" in (r.title or "").lower() and "election" in (r.title or "").lower()]
    print("rows with realized_pnl == %.2f (to the cent): %d" % (TARGET, len(targ)))
    print("rows whose title matches 'win the 2024 ... election': %d" % len(elec))
    dump = targ if targ else elec
    for i, r in enumerate(dump[:12]):
        print("\n--- ROW %d ---" % i)
        print("  condition_id     = %s" % r.condition_id)
        print("  slug             = %s" % r.slug)
        print("  event_slug       = %s" % r.event_slug)
        print("  title            = %s" % r.title)
        print("  outcome          = %s (index %d)  opposite=%s" % (r.outcome, r.outcome_index, r.opposite_outcome))
        print("  asset            = %s" % r.asset)
        print("  avg_price        = %s" % r.avg_price)
        print("  total_bought     = %s" % r.total_bought)
        print("  realized_pnl     = %s" % r.realized_pnl)
        print("  cur_price        = %s" % r.cur_price)
        print("  end_date         = %s   timestamp=%s" % (r.end_date, r.timestamp))
        print("  EXTRA (all unmapped raw fields, incl any negRisk*): %s" % dict(r.extra))
    es = sorted(set(r.event_slug for r in dump))
    tb = sorted(set(round(r.total_bought, 2) for r in dump))
    ap = sorted(set(round(r.avg_price, 4) for r in dump))
    cidn = len(set(r.condition_id for r in dump))
    print("\nANSWERS (Probe B):")
    print("  distinct event_slugs among these rows: %d -> %s" % (len(es), es))
    print("  distinct condition_ids: %d" % cidn)
    print("  total_bought value-set: %s" % tb)
    print("  avg_price value-set:    %s" % ap)
    if len(tb) > 1:
        print("  SIGNATURE => MIRRORING: identical realized_pnl to the cent across DIFFERING total_bought is impossible for honest per-leg PnL")
    else:
        print("  SIGNATURE => EQUAL-LEGS: identical realized_pnl AND identical total_bought (real equal bets, OR mirrored across equal legs -- Probe C/structure disambiguates)")
    for e in es:
        grp = [r for r in rows if r.event_slug == e]
        print("\n  FULL EVENT '%s': %d legs (%d distinct cids)" % (e, len(grp), len(set(r.condition_id for r in grp))))
        for r in sorted(grp, key=lambda r: r.realized_pnl)[:40]:
            print("    cid=%s outcome=%-26s avg=%.4f bought=%.2f realized=%+.2f cur=%.2f" % (r.condition_id[:12], (r.outcome or "")[:26], r.avg_price, r.total_bought, r.realized_pnl, r.cur_price))
    return dump


async def probe_c(c, election_rows):
    print("\n\n########## PROBE C: cross-source BUY-fill reconstruction (the decisive check) ##########")
    if not election_rows:
        print("  no election rows from Probe B; skipping")
        return
    acts, trunc = await pull_activity(c, D1K21)
    print("d1k21 activity rows pulled=%d (truncated_at_5000=%s)" % (len(acts), trunc))
    if not acts:
        print("  d1k21 /activity returned 0 rows (consistent with the scout's n=0 mega-whale truncation).")
    anyrows = defaultdict(int)
    buys = defaultdict(float)
    for a in acts:
        anyrows[a.condition_id] += 1
        if a.type == "TRADE" and a.side == "BUY":
            buys[a.condition_id] += act_amt(a)
    targets = election_rows[:3]
    for r in targets:
        cid = r.condition_id
        print("  cid=%s activity_rows=%d buy_usdc=%.2f (closed realized_pnl=%.2f total_bought=%.2f)" % (cid[:18], anyrows.get(cid, 0), buys.get(cid, 0.0), r.realized_pnl, r.total_bought))
    found = sum(1 for r in targets if anyrows.get(r.condition_id, 0) > 0)
    print("  election cids with ANY activity in the recent 5000-window: %d/%d" % (found, len(targets)))
    if found == 0:
        print("  VERDICT-C: INCONCLUSIVE via /activity -- 2024 election cids predate the 5000-row window (mega-whale truncation). Probe B's total_bought signature is the decisive evidence, not this.")
    else:
        print("  VERDICT-C: compare buy_usdc vs total_bought/realized above -- if buy_usdc ~ total_bought per cid, the leg is a real independent bet.")


async def probe_d(c):
    print("\n\n########## PROBE D: Fed negRisk reach (Kickstand7 + pako, live seed roster) ##########")
    for name, w in (("Kickstand7", KICKSTAND7), ("pako", PAKO)):
        print("\n=== %s  %s ===" % (name, w))
        rows = await pull_closed(c, w)
        fed = [r for r in rows if is_fed_event(r.event_slug) or is_fed_event(r.slug)]
        print("  total closed rows=%d  fed rows=%d" % (len(rows), len(fed)))
        by_ev = defaultdict(list)
        for r in fed:
            by_ev[r.event_slug].append(r)
        print("  distinct fed event_slugs=%d" % len(by_ev))
        for e in sorted(by_ev):
            grp = by_ev[e]
            ncids = len(set(r.condition_id for r in grp))
            print("    EVENT '%s': %d legs (%d distinct cids)" % (e, len(grp), ncids))
            for r in sorted(grp, key=lambda r: r.realized_pnl):
                negx = {k: v for k, v in r.extra.items() if "neg" in k.lower()}
                print("      cid=%s outcome=%-22s avg=%.4f bought=%.2f realized=%+.2f neg=%s" % (r.condition_id[:12], (r.outcome or "")[:22], r.avg_price, r.total_bought, r.realized_pnl, negx or "-"))
            vals = defaultdict(set)
            tbmap = defaultdict(set)
            for r in grp:
                vals[round(r.realized_pnl, 2)].add(r.condition_id)
                tbmap[round(r.realized_pnl, 2)].add(round(r.total_bought, 2))
            for v, cids in vals.items():
                if len(cids) >= 2:
                    sig = "MIRRORING (diff bought)" if len(tbmap[v]) > 1 else "equal-legs"
                    print("      >>> REPEATED realized=%.2f across %d distinct cids; total_bought set=%s => %s" % (v, len(cids), sorted(tbmap[v]), sig))
        negkeys = sorted({k for r in fed for k in r.extra.keys() if "neg" in k.lower()})
        print("  negRisk-related keys in fed rows' extra: %s" % (negkeys or "NONE"))


async def main():
    async with PolymarketDataAPIClient() as c:
        try:
            await probe_a(c)
        except Exception as e:
            import traceback
            print("PROBE A ERROR:", repr(e))
            traceback.print_exc()
        election = []
        try:
            election = await probe_b(c)
        except Exception as e:
            import traceback
            print("PROBE B ERROR:", repr(e))
            traceback.print_exc()
        try:
            await probe_c(c, election or [])
        except Exception as e:
            import traceback
            print("PROBE C ERROR:", repr(e))
            traceback.print_exc()
        try:
            await probe_d(c)
        except Exception as e:
            import traceback
            print("PROBE D ERROR:", repr(e))
            traceback.print_exc()
    print("\n\n########## PROBE COMPLETE -- read-only, no writes ##########")


if __name__ == "__main__":
    asyncio.run(main())
