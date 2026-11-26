"""BATCH 2c item 5 -- pip dry-run resolution for the 3 HELD-lib upgrade sets.
Runs `pip install --dry-run --report` against the prod-mirror venv (no mutation),
then prints, per target, the full package-change list as current -> proposed."""
import json, subprocess, sys, os, re

VENV = r"C:\Users\AA Incorporado\cc\b2c_mirror"
PY = os.path.join(VENV, "Scripts", "python.exe")
REQS = r"C:\Users\AA Incorporado\cc\b2c_prod_reqs.txt"
OUT = r"C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis"

# current prod versions (lower-cased name -> version)
cur = {}
with open(REQS) as f:
    for line in f:
        line = line.strip()
        m = re.match(r"^([A-Za-z0-9._-]+)==(.+)$", line)
        if m:
            cur[m.group(1).lower().replace("_", "-")] = m.group(2)

TARGETS = [
    ("a_cryptography", ["cryptography>=49"]),
    ("b_aiohttp",      ["aiohttp>=3.14.4"]),
    ("c_fastapi",      ["fastapi>=0.163.2"]),
]

summary = []
for name, spec in TARGETS:
    rep = os.path.join(OUT, f"29_dryrun_{name}.json")
    cmd = [PY, "-m", "pip", "install", "--dry-run", "--upgrade-strategy",
           "only-if-needed", "--report", rep] + spec
    print("\n" + "=" * 72)
    print(f"### TARGET {name}: pip install {' '.join(spec)}")
    print("=" * 72)
    p = subprocess.run(cmd, capture_output=True, text=True)
    tail = (p.stdout + p.stderr).strip().splitlines()
    # show the resolver's human summary lines
    for ln in tail:
        if re.search(r"(Would install|ERROR|Cannot install|conflict|incompatible|ResolutionImpossible|because)", ln, re.I):
            print("  " + ln.strip())
    if p.returncode != 0:
        print(f"  [resolve FAILED rc={p.returncode}] -- see full stderr below")
        print("  " + "\n  ".join(tail[-12:]))
        summary.append((name, "RESOLVE-FAILED", []))
        continue
    try:
        data = json.load(open(rep))
    except Exception as e:
        print(f"  [no report: {e}]")
        continue
    changes = []
    for item in data.get("install", []):
        md = item.get("metadata", {})
        pn = md.get("name", "?").lower().replace("_", "-")
        pv = md.get("version", "?")
        old = cur.get(pn)
        if old is None:
            changes.append((pn, "(absent)", pv, "NEW"))
        elif old != pv:
            changes.append((pn, old, pv, "CHANGE"))
        # same version = already-satisfied, pip may still list it; skip no-ops
    changes.sort(key=lambda x: (x[3] != "NEW", x[0]))
    print(f"  resolved OK; {len(changes)} package change(s):")
    for pn, old, new, kind in changes:
        print(f"    {kind:6} {pn:28} {old} -> {new}")
    summary.append((name, "OK", changes))

print("\n" + "#" * 72)
print("# ITEM 5 SUMMARY (one row per target)")
print("#" * 72)
for name, status, changes in summary:
    if status != "OK":
        print(f"{name}: {status}")
    else:
        chg = ", ".join(f"{p}:{o}->{n}" for p, o, n, k in changes)
        print(f"{name}: OK -- {len(changes)} change(s): {chg}")
