"""Item 1: drill the 24 fully_naked roll_leap events. For each, use the actual
legs to decide: did it SELL the old LEAP, and did it CLOSE the old short?
  flat         = LEAP sold AND short closed -> no position (bookkeeping gap)
  naked_short  = LEAP sold AND short NOT closed while a short existed -> risk
  uncovered*   = LEAP NOT sold (long intact) -> reclassify (was mislabeled)
Reads planning/pmcc_legs.csv. No prod, no writes to tested files."""
import csv
from collections import defaultdict

SRC = r"C:\Users\AA Incorporado\cc\planning\pmcc_legs.csv"
FAMILY = {
    "roll_short_call_close": "roll_short", "roll_short_call_open": "roll_short",
    "roll_leap_close_short": "roll_leap", "roll_leap_close": "roll_leap",
    "roll_leap_open": "roll_leap", "roll_leap_open_short": "roll_leap",
    "open_leap": "open_pmcc", "open_short_call": "open_short",
    "close_short_urgent": "close_short", "close_leap_urgent": "close_all",
}
NEW_SHORT = {"roll_short_call_open", "roll_leap_open_short", "open_short_call"}
NEW_LEAP = {"roll_leap_open", "open_leap"}

rows = list(csv.DictReader(open(SRC, newline="", encoding="utf-8")))
groups = defaultdict(list)
for r in rows:
    groups[r["pair_id"] or ("SOLO:" + r["id"])].append(r)

drill = []
for pid, legs in groups.items():
    actions = set(l["action"] for l in legs if l["action"])
    fams = set(FAMILY.get(a, "other") for a in actions)
    rec_type = "roll_leap" if "roll_leap" in fams else (
        "roll_short" if "roll_short" in fams else "other")
    if rec_type != "roll_leap":
        continue
    has_new_short = any(a in NEW_SHORT for a in actions)
    has_new_leap = any(a in NEW_LEAP for a in actions)
    if has_new_short or has_new_leap:
        continue  # not a fully_naked candidate
    sold_leap = "roll_leap_close" in actions
    closed_short = "roll_leap_close_short" in actions
    if not sold_leap:
        cls = "uncovered_reclassified"   # LEAP intact -> not fully_naked
    elif closed_short:
        cls = "flat"                      # both old legs closed
    else:
        cls = "naked_short_candidate"     # LEAP sold, no short-close leg
    drill.append({
        "pid": pid, "ts": min(l["ts"] for l in legs), "symbol": legs[0]["symbol"],
        "sold_leap": sold_leap, "closed_short": closed_short, "class": cls,
        "actions": ",".join(sorted(actions)),
        "short_strike": next((l["strike"] for l in legs if l["action"] == "roll_leap_close_short"), ""),
        "short_exp": next((l["expiration"] for l in legs if l["action"] == "roll_leap_close_short"), ""),
    })

from collections import Counter
print("fully_naked candidates examined:", len(drill))
print("class split:", dict(Counter(d["class"] for d in drill)))
print("per-symbol:", dict(Counter(d["symbol"] for d in drill)))
print()
print("flat count:", sum(1 for d in drill if d["class"] == "flat"))
print("naked_short candidates:", sum(1 for d in drill if d["class"] == "naked_short_candidate"))
print("uncovered_reclassified (LEAP not sold):", sum(1 for d in drill if d["class"] == "uncovered_reclassified"))
print()
for d in drill:
    if d["class"] in ("naked_short_candidate", "uncovered_reclassified"):
        print("  ", d["class"], d["ts"][:16], d["symbol"],
              "shortK=", d["short_strike"], "shortExp=", d["short_exp"],
              "| actions:", d["actions"])
