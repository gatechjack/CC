#!/usr/bin/env python3
"""READ-ONLY pre-flight: compare the E1 requirements.lock against what is
currently installed in the prod venv. No network, no install, no side effects.

Run on prod (cwd = trading_corp root) so importlib.metadata sees the venv:
    cd trading_corp; venv/bin/python /tmp/pm_e1_lock_diff.py

Expected, safe result: a list of NEW packages (py-clob-client + eth-* transitives)
and CHANGED = 0. Any CHANGED entry means prod has drifted from the freeze the lock
was built on -> STOP and review before deploying (the deploy script also guards this).
"""
import re
from importlib import metadata

LOCK = "/tmp/requirements.lock"

inst = {}
for d in metadata.distributions():
    name = (d.metadata["Name"] or "").lower().replace("_", "-")
    if name:
        inst[name] = d.version

pat = re.compile(r"^([A-Za-z0-9._-]+)==([^\s;]+)")
new, changed, same = [], [], 0
with open(LOCK) as fh:
    for line in fh:
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        m = pat.match(line)
        if not m:
            continue
        name = m.group(1).lower().replace("_", "-")
        ver = m.group(2)
        cur = inst.get(name)
        if cur is None:
            new.append(f"{name}=={ver}")
        elif cur != ver:
            changed.append(f"{name}: {cur} -> {ver}")
        else:
            same += 1

print(f"=== E1 lock vs installed ===  (same={same})")
print(f"\nNEW packages to be ADDED ({len(new)}):")
for n in sorted(new):
    print("  + " + n)
print(f"\nCHANGED installed packages ({len(changed)}):")
if changed:
    for c in sorted(changed):
        print("  ! " + c)
    print("\n>>> NON-ADDITIVE: prod drifted from the lock's base freeze. STOP and review.")
else:
    print("  (none — deploy is purely ADDITIVE / safe)")
