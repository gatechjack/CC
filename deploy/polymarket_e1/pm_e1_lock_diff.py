#!/usr/bin/env python3
"""READ-ONLY pre-flight: compare the E1 requirements.lock against what is
currently installed in the prod venv. No network, no install, no side effects.

Run on prod (cwd = trading_corp root) so importlib.metadata sees the venv:
    cd trading_corp; venv/bin/python /tmp/pm_e1_lock_diff.py

Expected, safe result: a list of NEW packages (py-clob-client + eth-* transitives),
CHANGED = 0, plus the ONE allowlisted exception (setuptools downgrade, below). Any
CHANGED entry means prod has drifted from the freeze the lock was built on -> STOP
and review before deploying (deploy_e1_lock.sh also guards this and aborts).

ALLOWED_CHANGES is a SCOPED, named exception — not "ignore setuptools". Only the
exact (name, from, to) transition listed is permitted; any other setuptools change
(different prod baseline, or a different lock target) is treated as drift and still
counts as CHANGED. deploy_e1_lock.sh's inline guard mirrors this set — keep in sync.
"""
import re
from importlib import metadata

LOCK = "/tmp/requirements.lock"

# One-time, scoped exception: the E1 lock intentionally DOWNGRADES setuptools from
# 82.0.1 to 80.10.2 because web3 6.11 imports pkg_resources, which setuptools 81+
# removed (commit fe0666a). This is the EXACT from->to that is allowed; nothing else.
ALLOWED_CHANGES = frozenset({
    ("setuptools", "82.0.1", "80.10.2"),
})

_PIN = re.compile(r"^([A-Za-z0-9._-]+)==([^\s;]+)")


def classify(installed, lock_lines, allowed_changes=ALLOWED_CHANGES):
    """Compare a lock against an installed-package map. Pure / no side effects.

    Returns (new, changed, allowed, same):
      new     - "name==ver" for packages not currently installed (additive).
      changed - "name: cur -> ver" for version changes NOT in allowed_changes;
                any entry here is NON-ADDITIVE and must abort the deploy.
      allowed - "name: cur -> ver" for changes matching allowed_changes exactly;
                intentional, scoped, logged loudly, does NOT abort.
      same    - count of pins already satisfied at the exact installed version.
    """
    inst = {k.lower().replace("_", "-"): v for k, v in installed.items()}
    new, changed, allowed, same = [], [], [], 0
    for line in lock_lines:
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        m = _PIN.match(line)
        if not m:
            continue
        name = m.group(1).lower().replace("_", "-")
        ver = m.group(2)
        cur = inst.get(name)
        if cur is None:
            new.append(f"{name}=={ver}")
        elif cur == ver:
            same += 1
        elif (name, cur, ver) in allowed_changes:
            allowed.append(f"{name}: {cur} -> {ver}")
        else:
            changed.append(f"{name}: {cur} -> {ver}")
    return new, changed, allowed, same


def _installed_map():
    inst = {}
    for d in metadata.distributions():
        name = (d.metadata["Name"] or "").lower().replace("_", "-")
        if name:
            inst[name] = d.version
    return inst


if __name__ == "__main__":
    with open(LOCK) as fh:
        lock_lines = fh.readlines()
    new, changed, allowed, same = classify(_installed_map(), lock_lines)

    print(f"=== E1 lock vs installed ===  (same={same})")
    print(f"\nNEW packages to be ADDED ({len(new)}):")
    for n in sorted(new):
        print("  + " + n)
    if allowed:
        print(f"\nALLOWED EXCEPTIONS — intentional, scoped ({len(allowed)}):")
        for a in sorted(allowed):
            print("  ~ " + a + "   [web3 6.11 pkg_resources fix; see ALLOWED_CHANGES]")
    print(f"\nCHANGED installed packages ({len(changed)}):")
    if changed:
        for c in sorted(changed):
            print("  ! " + c)
        print("\n>>> NON-ADDITIVE: prod drifted from the lock's base freeze. STOP and review.")
    else:
        print("  (none — deploy is purely ADDITIVE / safe)")
