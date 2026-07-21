"""Fold LEAP-leg facts into the tested rec-history CSV (join on ts+symbol).
Adds columns: old_leap_px, has_new_leap, sold_leap, closed_short, b4_subtype.
Local only; reads planning/pmcc_legs.csv, rewrites planning/pmcc_rec_history.csv."""
import csv
from collections import defaultdict, Counter

REC = r"C:\Users\AA Incorporado\cc\planning\pmcc_rec_history.csv"
LEGS = r"C:\Users\AA Incorporado\cc\planning\pmcc_legs.csv"

FAMILY = {
    "roll_short_call_close": "roll_short", "roll_short_call_open": "roll_short",
    "roll_leap_close_short": "roll_leap", "roll_leap_close": "roll_leap",
    "roll_leap_open": "roll_leap", "roll_leap_open_short": "roll_leap",
    "open_leap": "open_pmcc", "open_short_call": "open_short",
    "close_short_urgent": "close_short", "close_leap_urgent": "close_all",
}
NEW_SHORT = {"roll_short_call_open", "roll_leap_open_short", "open_short_call"}
NEW_LEAP = {"roll_leap_open", "open_leap"}
CLOSE_SHORT = {"roll_leap_close_short", "roll_short_call_close", "close_short_urgent"}

groups = defaultdict(list)
for r in csv.DictReader(open(LEGS, newline="", encoding="utf-8")):
    groups[r["pair_id"] or ("SOLO:" + r["id"])].append(r)

enr = {}
for pid, legs in groups.items():
    actions = set(l["action"] for l in legs if l["action"])
    fams = set(FAMILY.get(a, "other") for a in actions)
    rec_type = "roll_leap" if "roll_leap" in fams else (
        "roll_short" if "roll_short" in fams else "other")
    has_new_short = any(a in NEW_SHORT for a in actions)
    has_new_leap = any(a in NEW_LEAP for a in actions)
    sold_leap = "roll_leap_close" in actions
    closed_short = any(a in CLOSE_SHORT for a in actions)
    old_leap_px = next((l["limit_price"] for l in legs if l["action"] == "roll_leap_close"), "")
    is_roll = rec_type in ("roll_short", "roll_leap")
    b4 = is_roll and not has_new_short
    if not b4:
        subtype = ""
    elif rec_type == "roll_leap" and sold_leap and not has_new_leap:
        subtype = "fully_naked"
    else:
        subtype = "uncovered"
    enr[(min(l["ts"] for l in legs), legs[0]["symbol"])] = {
        "old_leap_px": old_leap_px,
        "has_new_leap": "1" if has_new_leap else "0",
        "sold_leap": "1" if sold_leap else "0",
        "closed_short": "1" if closed_short else "0",
        "b4_subtype": subtype,
    }

recs = list(csv.DictReader(open(REC, newline="", encoding="utf-8")))
cols = list(recs[0].keys())
NEW = ["old_leap_px", "has_new_leap", "sold_leap", "closed_short", "b4_subtype"]
for c in NEW:
    if c not in cols:
        cols.append(c)

matched, misses, subc = 0, [], Counter()
for r in recs:
    e = enr.get((r["ts"], r["symbol"]))
    if e:
        matched += 1
        r.update(e)
        subc[e["b4_subtype"] or "(none)"] += 1
    else:
        misses.append((r["ts"], r["symbol"]))
        for c in NEW:
            r[c] = ""

print("matched:", matched, "/", len(recs), " misses:", len(misses), misses[:5])
print("b4_subtype counts:", dict(subc))

with open(REC, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    for r in recs:
        w.writerow({k: r.get(k, "") for k in cols})
print("REWROTE", REC, "cols:", len(cols))
