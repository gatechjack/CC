"""LOCAL classification of the LEAP legs (read from planning/pmcc_legs.csv).
Splits B4 close-without-recover into fully_naked vs uncovered and computes a
data-backed cost_ignorant_leap_roll count. Writes an enriched local copy.
No prod access; no writes to the tested pmcc_rec_history.csv."""
import csv
from collections import defaultdict

SRC = r"C:\Users\AA Incorporado\cc\planning\pmcc_legs.csv"
OUT = r"C:\Users\AA Incorporado\cc\planning\pmcc_rec_history_leap.csv"

FAMILY = {
    "roll_short_call_close": "roll_short", "roll_short_call_open": "roll_short",
    "roll_leap_close_short": "roll_leap", "roll_leap_close": "roll_leap",
    "roll_leap_open": "roll_leap", "roll_leap_open_short": "roll_leap",
    "open_leap": "open_pmcc", "open_short_call": "open_short",
    "close_short_urgent": "close_short", "close_leap_urgent": "close_all",
}
NEW_SHORT = {"roll_short_call_open", "roll_leap_open_short", "open_short_call"}
NEW_LEAP = {"roll_leap_open", "open_leap"}


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


rows = list(csv.DictReader(open(SRC, newline="", encoding="utf-8")))
groups = defaultdict(list)
for r in rows:
    pid = r["pair_id"] or ("SOLO:" + r["id"])
    groups[pid].append(r)

recs = []
for pid, legs in groups.items():
    actions = [l["action"] for l in legs if l["action"]]
    fams = set(FAMILY.get(a, "other") for a in actions)
    if "roll_leap" in fams:
        rec_type = "roll_leap"
    elif "roll_short" in fams:
        rec_type = "roll_short"
    elif "open_pmcc" in fams:
        rec_type = "open_pmcc"
    elif "open_short" in fams:
        rec_type = "cover_leap"
    elif "close_short" in fams:
        rec_type = "close_short"
    elif "close_all" in fams:
        rec_type = "close_all"
    else:
        rec_type = "other"

    has_new_short = any(a in NEW_SHORT for a in actions)
    has_new_leap = any(a in NEW_LEAP for a in actions)
    old_leap_close_px = None
    new_leap_open_px = None
    for l in legs:
        if l["action"] == "roll_leap_close":
            old_leap_close_px = fnum(l["limit_price"])
        if l["action"] == "roll_leap_open":
            new_leap_open_px = fnum(l["limit_price"])

    recs.append({
        "pair_id": pid, "ts": min(l["ts"] for l in legs),
        "symbol": legs[0]["symbol"], "rec_type": rec_type,
        "has_new_short": has_new_short, "has_new_leap": has_new_leap,
        "old_leap_close_px": old_leap_close_px, "new_leap_open_px": new_leap_open_px,
        "actions": ",".join(sorted(set(actions))),
    })

rolls = [r for r in recs if r["rec_type"] in ("roll_short", "roll_leap")]
leaps = [r for r in recs if r["rec_type"] == "roll_leap"]

b4 = [r for r in rolls if not r["has_new_short"]]
# split B4
fully_naked = []   # LEAP closed, no new LEAP
uncovered = []     # long intact (or re-opened), but no covering short
for r in b4:
    if r["rec_type"] == "roll_leap" and not r["has_new_leap"]:
        r["b4_subtype"] = "fully_naked"
        fully_naked.append(r)
    else:
        r["b4_subtype"] = "uncovered"
        uncovered.append(r)
for r in recs:
    r.setdefault("b4_subtype", "")

# cost-ignorant LEAP rolls (data-backed): roll_leap whose old-LEAP sell price is 0.0
ci_zero = [r for r in leaps if r["old_leap_close_px"] == 0.0]
ci_nonzero = [r for r in leaps if r["old_leap_close_px"] not in (0.0, None)]
ci_none = [r for r in leaps if r["old_leap_close_px"] is None]

print("=== RECONCILE ===")
print("total recs (pairs):", len(recs), " rolls:", len(rolls), " roll_leap:", len(leaps))
print("B4 close_without_recover (no new short):", len(b4), " (detector baseline = 51)")
print("=== B4 SPLIT ===")
print("fully_naked (LEAP closed, NO new LEAP):", len(fully_naked),
      "symbols:", sorted(set(r["symbol"] for r in fully_naked)))
print("uncovered (long intact/re-opened, NO new short):", len(uncovered),
      " of which roll_short:", sum(1 for r in uncovered if r["rec_type"] == "roll_short"),
      " roll_leap-with-new-leap:", sum(1 for r in uncovered if r["rec_type"] == "roll_leap"))
print("=== COST-IGNORANT LEAP ROLL (data-backed) ===")
print("roll_leap total:", len(leaps))
print("  old-LEAP sell price == 0.0 :", len(ci_zero), "(cost-ignorant)")
print("  old-LEAP sell price  > 0.0 :", len(ci_nonzero))
print("  old-LEAP close leg ABSENT   :", len(ci_none))
print("new-LEAP buy price sample (non-null):",
      [r["new_leap_open_px"] for r in leaps if r["new_leap_open_px"] is not None][:6])

with open(OUT, "w", newline="", encoding="utf-8") as f:
    cols = ["pair_id", "ts", "symbol", "rec_type", "has_new_short", "has_new_leap",
            "old_leap_close_px", "new_leap_open_px", "b4_subtype", "actions"]
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    for r in sorted(recs, key=lambda x: x["ts"]):
        w.writerow({k: r.get(k, "") for k in cols})
print("WROTE", OUT)
