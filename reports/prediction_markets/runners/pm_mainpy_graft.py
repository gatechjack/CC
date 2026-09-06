"""Build the PM-driver-block graft onto MACE's CURRENT box main.py -- NOT a revert.
Reads the box main.py (as pulled) + the roster main.py (carries the block), extracts the PM
LIVE DRIVER block content-anchored, inserts it at the poly_kalshi anchor, and VERIFIES:
  - MACE's content is byte-identical except the inserted lines (diff = only additions);
  - MACE markers survive by count;
  - the PM driver markers are present;
  - py_compile passes.
Writes the grafted file + prints its CR-stripped sha256. Local only; touches nothing on the box.
"""
import hashlib, io, py_compile, sys

BOX = r"C:\Users\AA Incorporado\cc\_box_main_current.py"
ROSTER = r"C:\Users\AA Incorporado\cc\_roster_main.py"
OUT = r"C:\Users\AA Incorporado\cc\_grafted_main.py"

def readlines_lf(p):
    with io.open(p, "r", encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n").replace("\r", "\n").split("\n")

box = readlines_lf(BOX)
roster = readlines_lf(ROSTER)

# --- extract the PM LIVE DRIVER block from roster (content-anchored) ---
def find_one(lines, needle, what):
    idx = [i for i, l in enumerate(lines) if needle in l]
    assert len(idx) == 1, "expected exactly ONE %s, found %d" % (what, len(idx))
    return idx[0]

r_start = find_one(roster, "# ── Prediction Markets LIVE DRIVER", "PM-block-start in roster")
r_end   = find_one(roster, 'log.exception("PM live driver wiring FAILED', "PM-block-end in roster")
assert r_end > r_start, "block end must follow start"
block = roster[r_start:r_end + 1]            # inclusive; the 94-line driver block
print("PM driver block: roster lines %d..%d (%d lines)" % (r_start + 1, r_end + 1, len(block)))
assert any("scheduled_pm_live_loop" in l for l in block)
assert any("PM LIVE DRIVER WIRED" in l for l in block)
assert not any("SHARD-BALANCE SNAPSHOTS" in l for l in block), "block must NOT include M3"

# --- find the poly_kalshi anchor in the BOX + insert after the blank that follows it ---
a = find_one(box, 'log.exception("Poly->Kalshi MLB copy wiring FAILED', "poly_kalshi anchor in box")
assert box[a + 1].strip() == "", "expected a blank line after the anchor, got %r" % box[a + 1]
# assert PM is genuinely absent from the box before we graft
assert not any("scheduled_pm_live_loop" in l for l in box), "box already has the driver block?!"

grafted = box[:a + 2] + block + [""] + box[a + 2:]   # anchor, blank, [BLOCK], blank, (Phase-2a...)

# --- VERIFY: everything except the inserted lines is byte-identical (diff = pure insertion) ---
import difflib
diff = list(difflib.unified_diff(box, grafted, lineterm=""))
added = [l for l in diff if l.startswith("+") and not l.startswith("+++")]
removed = [l for l in diff if l.startswith("-") and not l.startswith("---")]
print("diff vs box: +%d lines, -%d lines (removed MUST be 0 = MACE content untouched)" % (len(added), len(removed)))
assert len(removed) == 0, "GRAFT REMOVED MACE LINES -- ABORT"
assert len(added) == len(block) + 1, "unexpected added-line count"

# --- MACE markers survive by count ---
def count(lines, s, ci=False):
    return sum((s.lower() in l.lower()) if ci else (s in l) for l in lines)
for m, ci in (("dxfeed", True), ("tastytrade", True), ("mace", True), ("KalshiTailPriceArbAgent", False),
              ("assert_roster_invariant_boot", False), ("Poly->Kalshi MLB copy WIRED", False)):
    b, g = count(box, m, ci), count(grafted, m, ci)
    print("  MACE/base marker %-32s box=%d grafted=%d %s" % (m, b, g, "OK" if b == g else "** CHANGED **"))
    assert b == g, "MACE marker %s changed" % m

# --- PM markers now present ---
for m, exp in (("scheduled_pm_live_loop", 2), ("plan_driver_tasks", 2), ("pm_live_driver", 4),
               ("PM LIVE DRIVER WIRED", 1)):
    g = count(grafted, m)
    print("  PM marker %-28s grafted=%d (expect %d) %s" % (m, g, exp, "OK" if g == exp else "** OFF **"))
    assert g == exp, "PM marker %s = %d, expected %d" % (m, g, exp)
# M3 stays ABSENT (driver-only scope)
assert count(grafted, "SHARD-BALANCE SNAPSHOTS") == 0, "M3 leaked into a driver-only graft"

with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(grafted))
py_compile.compile(OUT, doraise=True)
sha = hashlib.sha256("\n".join(grafted).encode("utf-8")).hexdigest()[:16]
print("py_compile OK ; box lines=%d -> grafted lines=%d" % (len(box), len(grafted)))
print("GRAFTED main.py CR-stripped sha256(16) = %s  -> %s" % (sha, OUT))
