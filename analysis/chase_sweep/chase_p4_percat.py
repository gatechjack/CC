"""Chase-rule section-3 per-category pass at the surviving threshold (abs > 2c), + in-play split + ages."""
import json, datetime, collections

SP = r"C:\Users\AAINCO~1\AppData\Local\Temp\claude\758809bcb234d7b1\43e4cf7d-4856-418a-a247-7081cd0839d4\scratchpad"
SPLIT = int(datetime.datetime(2026, 10, 16, tzinfo=datetime.timezone.utc).timestamp())
d = json.load(open(SP + r"\chase_records.json"))
F = d["priced"]

def chase(r):
    phi = r["phi"] if r["phi"] is not None else (0.07 * r["fill"] * (1 - r["fill"]))
    return r["fill"] + phi - r["w"]

def agg(recs):
    n = len(recs); ev = len({r["cid"] for r in recs}); ct = sum(r["ct"] for r in recs)
    gross = sum(r["gross"] for r in recs if r["gross"] is not None)
    fee = sum(r["fee"] for r in recs if r["gross"] is not None)
    net = sum(r["net"] for r in recs if r["net"] is not None)
    n_out = sum(1 for r in recs if r["net"] is not None)
    wins = sum(1 for r in recs if (r["won"] or 0) > 0.5 and r["net"] is not None)
    netct = (net / ct) if ct else None
    return n, ev, ct, gross, fee, net, (100.0*wins/n_out if n_out else None), netct

def row(label, recs):
    n, ev, ct, g, f, net, win, netct = agg(recs)
    print("  %-14s n=%4d ev=%4d ct=%6.0f gross=%+9.2f fee=%7.2f net=%+9.2f win=%s net/ct=%s"
          % (label, n, ev, ct, g, f, net,
             ("%4.1f%%" % win) if win is not None else "  -- ",
             ("%+7.4f" % netct) if netct is not None else "  --  "))

X = 0.02
print("==== PER CATEGORY at abs chase > 2c (TRAIN + HOLDOUT merged; B=blocked K=kept) ====")
bycat = collections.defaultdict(list)
for r in F:
    bycat[r["cat"]].append(r)
for cat in sorted(bycat, key=lambda c: -len(bycat[c])):
    recs = bycat[cat]
    B = [r for r in recs if chase(r) > X + 1e-12]; K = [r for r in recs if chase(r) <= X + 1e-12]
    print("%s:" % cat)
    row("blocked", B); row("kept", K)

print("\n==== ITF detail (both eras) ====")
itf = bycat.get("itf", [])
for era, lo, hi in (("train", 0, SPLIT), ("holdout", SPLIT, 1 << 62)):
    sel = [r for r in itf if lo <= r["ts"] < hi]
    B = [r for r in sel if chase(r) > X + 1e-12]; K = [r for r in sel if chase(r) <= X + 1e-12]
    print(" itf %s:" % era); row("blocked", B); row("kept", K)

print("\n==== IN-PLAY vs PRE-GAME at abs > 2c (all cats, both eras) ====")
for flag, label in ((True, "in-play"), (False, "pre-game")):
    sel = [r for r in F if r["inplay"] == flag]
    B = [r for r in sel if chase(r) > X + 1e-12]; K = [r for r in sel if chase(r) <= X + 1e-12]
    print(" %s:" % label); row("blocked", B); row("kept", K)

print("\n==== holdout-confirmation per era at 2c, netct spread ====")
for era, lo, hi in (("train", 0, SPLIT), ("holdout", SPLIT, 1 << 62)):
    sel = [r for r in F if lo <= r["ts"] < hi]
    B = [r for r in sel if chase(r) > X + 1e-12]; K = [r for r in sel if chase(r) <= X + 1e-12]
    bn = agg(B); kn = agg(K)
    print(" %s: blocked netct %+0.4f (n=%d) vs kept netct %+0.4f (n=%d)  -> blocked worse by %+0.4f/ct"
          % (era, bn[7], bn[0], kn[7], kn[0], (kn[7] - bn[7])))

# fee saved at 2c overall
B = [r for r in F if chase(r) > X + 1e-12]
print("\nfees on blocked (saved if gated): $%.2f of $%.2f total (%.1f%%)"
      % (sum(r["fee"] for r in B), sum(r["fee"] for r in F),
         100.0 * sum(r["fee"] for r in B) / sum(r["fee"] for r in F)))
