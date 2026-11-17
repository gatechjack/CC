"""Chase-rule section-3 sweep (READ-ONLY, stdlib): what would the maximum-chase gate have blocked/kept?

Inputs: entries_enriched.csv (backtest per-fill dataset), scratch prediction_markets.db (RO: wallet/cid/oidx per row),
        chase_v2_whale.json (v2-recovered whale avg prices + first_entry_at).
Fill basis: chase = fill_price + per-contract fee - whale_price (whale held-token avg; claim-faithful matching).
Hold-out: train < 2026-10-16 UTC <= holdout (the backtest's own M4 cut).
CLV: pre-game fills only (in-play CLV undefined -- the close precedes entry; the backtest's own caveat #1).
"""
import csv, json, math, sqlite3, collections, datetime

SCR = r"C:\Users\AA Incorporado\cc-pm-backtest-2026-10-22-wt\scratch"
DB = (SCR + r"\prediction_markets.db").replace("\\", "/")
SP = r"C:\Users\AAINCO~1\AppData\Local\Temp\claude\758809bcb234d7b1\43e4cf7d-4856-418a-a247-7081cd0839d4\scratchpad"
SPLIT = int(datetime.datetime(2026, 10, 16, tzinfo=datetime.timezone.utc).timestamp())

def ff(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

rows = list(csv.DictReader(open(SCR + r"\entries_enriched.csv", newline="", encoding="utf-8")))
con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
con.row_factory = sqlite3.Row
ident = {r["id"]: (str(r["wallet"]).lower(), str(r["condition_id"] or "").lower(), r["outcome_index"])
         for r in con.execute("SELECT id, wallet, condition_id, outcome_index FROM pm_subdivision_order "
                              "WHERE dry_run=0 AND is_exit=0")}
con.close()
v2 = json.load(open(SP + r"\chase_v2_whale.json"))
v2rows = {}
for k, v in v2["rows"].items():
    w, cid, oidx = k.rsplit("|", 2)
    v2rows[(w, cid, int(oidx))] = v
print("v2 fetch: calls=%s rows=%s" % (v2["calls"], len(v2rows)))

# ── assemble per-fill records ────────────────────────────────────────────────
F = []          # filled copies with whale price (from wpx else v2)
UNPRICED = []   # filled copies with NO whale price from either source (the gate would fail-closed skip these)
xcheck = []     # (wpx, v2_avg) where both exist
v2_only = wpx_only = both = neither = 0
fe_have = 0
for r in rows:
    if r["status"] != "filled":
        continue
    key = ident[int(r["id"])]
    oidx = key[2]
    key = (key[0], key[1], int(oidx) if oidx is not None else 0)
    wpx = ff(r.get("wpx"))
    vr = v2rows.get(key)
    v2avg = ff(vr["v2_avg"]) if vr else None
    if v2avg is not None and not (0.0 < v2avg < 1.0):
        v2avg = None
    if vr and vr.get("v2_first_entry_at"):
        fe_have += 1
    if wpx is not None and v2avg is not None:
        both += 1; xcheck.append((wpx, v2avg))
    elif wpx is not None:
        wpx_only += 1
    elif v2avg is not None:
        v2_only += 1
    else:
        neither += 1
    w = wpx if wpx is not None else v2avg
    rec = {
        "ts": int(float(r["submitted_ts"])), "cat": r["cat"], "acct": r["acct"], "cid": key[1],
        "ct": ff(r["ct"]) or 0.0, "fill": ff(r["fill"]), "fee": ff(r["fee"]) or 0.0,
        "phi": ff(r.get("phi")), "intended": ff(r.get("intended")),
        "gross": ff(r.get("gross_cf")), "net": ff(r.get("net_cf")), "won": ff(r.get("won_cf")),
        "cmid": ff(r.get("cmid")), "inplay": (r.get("inplay") == "True"), "w": w,
        "wsrc": ("obs" if wpx is not None else ("v2" if v2avg is not None else None)),
        "ticker": r["ticker"], "leg": r["leg"],
    }
    (F if w is not None else UNPRICED).append(rec)

print("filled total:", len(F) + len(UNPRICED))
print("whale price: obs-only %d | both %d | v2-only(NEW) %d | neither %d" % (wpx_only, both, v2_only, neither))
print("v2 first_entry_at present on filled:", fe_have)

# cross-check wpx (observation-time) vs v2 (final avg)
if xcheck:
    diffs = sorted(abs(a - b) for a, b in xcheck)
    agree = sum(1 for a, b in xcheck if abs(a - b) <= 0.005)
    print("xcheck wpx vs v2_avg: n=%d  <=0.5c agree=%d (%.1f%%)  median|diff|=%.4f  p90=%.4f  max=%.4f"
          % (len(xcheck), agree, 100.0 * agree / len(xcheck), diffs[len(diffs)//2], diffs[int(0.9*len(diffs))], diffs[-1]))

# fee-formula instrument check: phi vs mult*0.07*fill*(1-fill)
fees = json.load(open(SCR + r"\kalshi_cache\series_fees.json"))
def series(t):
    return t.split("-")[0]
ok = bad = 0; bad_ex = []
for rec in F + UNPRICED:
    if not rec["phi"] or not rec["fill"]:
        continue
    mult = (fees.get(series(rec["ticker"]), {}) or {}).get("fee_multiplier", 1)
    # Kalshi rounds the ORDER fee up to the next cent: phi = ceil(mult*0.07*ct*p*(1-p)*100)/100/ct
    ct = rec["ct"] or 1.0
    pred = math.ceil(mult * 0.07 * ct * rec["fill"] * (1 - rec["fill"]) * 100 - 1e-9) / 100.0 / ct
    if abs(pred - rec["phi"]) <= 0.0051:
        ok += 1
    else:
        bad += 1
        if len(bad_ex) < 5:
            bad_ex.append((rec["ticker"], rec["fill"], rec["ct"], rec["phi"], pred))
print("fee formula check (ceil(mult*0.07*ct*p*(1-p)) per order): ok=%d bad=%d" % (ok, bad))
for b in bad_ex:
    print("   fee mismatch:", b)

# intended-vs-fill (slippage actually paid): gate uses ask-basis; measure (intended - fill)
dd = sorted((rec["intended"] - rec["fill"]) for rec in F + UNPRICED if rec["intended"] is not None and rec["fill"] is not None)
if dd:
    print("limit(intended)-fill: median=%.4f p10=%.4f p90=%.4f  (gate uses ask-basis ~= fill)" %
          (dd[len(dd)//2], dd[int(0.1*len(dd))], dd[int(0.9*len(dd))]))

# ── the sweep ────────────────────────────────────────────────────────────────
def chase(rec):
    phi = rec["phi"] if rec["phi"] is not None else (0.07 * rec["fill"] * (1 - rec["fill"]))
    return rec["fill"] + phi - rec["w"]

def agg(recs):
    n = len(recs)
    ev = len({r["cid"] for r in recs})
    ct = sum(r["ct"] for r in recs)
    gross = sum(r["gross"] for r in recs if r["gross"] is not None)
    fee = sum(r["fee"] for r in recs if r["gross"] is not None)
    net = sum(r["net"] for r in recs if r["net"] is not None)
    n_out = sum(1 for r in recs if r["net"] is not None)
    wins = sum(1 for r in recs if (r["won"] or 0) > 0.5 and r["net"] is not None)
    pg = [r["cmid"] - r["fill"] for r in recs if (not r["inplay"]) and r["cmid"] is not None and r["fill"] is not None]
    clv = (sum(pg) / len(pg)) if pg else None
    netct = (net / ct) if ct else None
    return dict(n=n, ev=ev, ct=ct, gross=gross, fee=fee, net=net, n_out=n_out,
                win=(100.0 * wins / n_out if n_out else None), clv=(100 * clv if clv is not None else None),
                nclv=len(pg), netct=netct)

def fmt(d):
    return ("n=%4d ev=%4d ct=%6.0f gross=%+9.2f fee=%7.2f net=%+9.2f win=%s clv=%s(n=%d) net/ct=%s"
            % (d["n"], d["ev"], d["ct"], d["gross"], d["fee"], d["net"],
               ("%4.1f%%" % d["win"]) if d["win"] is not None else "  -- ",
               ("%+5.2fc" % d["clv"]) if d["clv"] is not None else " -- ", d["nclv"],
               ("%+7.4f" % d["netct"]) if d["netct"] is not None else "  --  "))

for era, lo, hi in (("TRAIN <10-16", 0, SPLIT), ("HOLDOUT >=10-16", SPLIT, 1 << 62)):
    sel = [r for r in F if lo <= r["ts"] < hi]
    print("\n==== %s: %d priced fills (unpriced excluded: %d) ====" %
          (era, len(sel), sum(1 for r in UNPRICED if lo <= r["ts"] < hi)))
    print("  ALL      ", fmt(agg(sel)))
    for X in (0, 2, 4, 6, 8, 10):
        blocked = [r for r in sel if chase(r) > X / 100.0 + 1e-12]
        kept = [r for r in sel if chase(r) <= X / 100.0 + 1e-12]
        print("  abs>%2dc B" % X, fmt(agg(blocked)))
        print("          K", fmt(agg(kept)))
    for Y in (25, 50, 75):
        blocked = [r for r in sel if r["w"] < 1 and chase(r) > (Y / 100.0) * (1 - r["w"]) + 1e-12]
        kept = [r for r in sel if not (r["w"] < 1 and chase(r) > (Y / 100.0) * (1 - r["w"]) + 1e-12)]
        print("  rel>%2d%% B" % Y, fmt(agg(blocked)))
        print("          K", fmt(agg(kept)))

# unpriced copies: what would fail-closed have cost?
print("\n==== UNPRICED (gate fail-closed skips these) ====")
print("  ", fmt(agg(UNPRICED)))

# chase distribution
ch = sorted(chase(r) for r in F)
print("\nchase distribution (all priced fills): p10=%.3f p25=%.3f median=%.3f p75=%.3f p90=%.3f  >0: %.1f%%"
      % (ch[int(0.1*len(ch))], ch[int(0.25*len(ch))], ch[len(ch)//2], ch[int(0.75*len(ch))],
         ch[int(0.9*len(ch))], 100.0 * sum(1 for c in ch if c > 0) / len(ch)))

# save the assembled records for the per-category pass
with open(SP + r"\chase_records.json", "w") as fh:
    json.dump({"priced": F, "unpriced": UNPRICED}, fh)
print("\nrecords saved -> chase_records.json")
