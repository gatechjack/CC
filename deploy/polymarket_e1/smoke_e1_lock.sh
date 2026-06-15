#!/usr/bin/env bash
# OFF-PROD smoke for the CORRECTED E1 dependency lock (setuptools 80.10.2 — the
# web3 6.11 pkg_resources fix). Proves `pip install --require-hashes` resolves the
# hash-pinned lock cleanly into a FRESH EMPTY venv on linux/py3.12, and that web3
# imports WITHOUT the pkg_resources failure setuptools 82 caused.
#
# Touches ONLY a self-made mktemp scratch dir + a throwaway venv inside it. It does
# NOT touch /home/azureuser/trading_corp/venv or any running service. An empty venv
# has no baseline, so this is guard-INDEPENDENT: it validates hash resolution +
# import coherence only, separate from the additive-guard (which runs vs prod's
# installed set at deploy time).
#
# This is the runbook 0.3 step. It is an OPERATOR action (it installs/downloads on
# whatever box it runs on); the agent that wrote it has read-only prod SSH and does
# not run it. Run on prod-in-a-scratch-dir, or on an identical throwaway linux box.
#
# Usage (linux, py3.12 to match prod 3.12.13):
#     bash smoke_e1_lock.sh [/path/to/requirements.lock]
#   defaults to the requirements.lock next to this script.
#   override interpreter with PYBIN=/path/to/python3.12 bash smoke_e1_lock.sh
set -euo pipefail

LOCK="${1:-$(dirname "$0")/requirements.lock}"
EXP_LOCK_MD5="a47fc93e2103bd4687ac8bd8717759c4"
PYBIN="${PYBIN:-python3.12}"
PROD_VENV="/home/azureuser/trading_corp/venv"

echo "== E1 corrected-lock off-prod smoke =="

# 1) confirm this is the corrected lock (md5 gate + setuptools pin)
[ -f "$LOCK" ] || { echo "ABORT: lock not found: $LOCK"; exit 1; }
got=$(md5sum "$LOCK" | cut -d" " -f1)
[ "$got" = "$EXP_LOCK_MD5" ] || { echo "ABORT: lock md5 $got != $EXP_LOCK_MD5 (not the corrected lock)"; exit 2; }
grep -q "^setuptools==80.10.2 " "$LOCK" || { echo "ABORT: setuptools==80.10.2 pin not found in lock"; exit 2; }
echo "lock OK: md5=$got, setuptools==80.10.2 present"

# 2) interpreter — must match prod (py3.12.13) as closely as the box allows
command -v "$PYBIN" >/dev/null 2>&1 || { echo "ABORT: $PYBIN not found (need a 3.12.x interpreter)"; exit 3; }
PYVER=$("$PYBIN" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')
case "$PYVER" in
  3.12.13) echo "interpreter: $PYBIN -> $PYVER (matches prod 3.12.13)";;
  3.12.*)  echo "interpreter: $PYBIN -> $PYVER  NOTE: != prod 3.12.13 (closest 3.12.x); the operator's real smoke must use prod's exact interpreter";;
  *) echo "ABORT: $PYVER is not 3.12.x — the lock targets x86_64-linux / py3.12"; exit 3;;
esac

# 3) throwaway venv in an mktemp scratch dir — NOTHING under the prod path
SCRATCH=$(mktemp -d -t e1smoke.XXXXXX)
trap 'rm -rf "$SCRATCH"; echo "torn down scratch: $SCRATCH"' EXIT
case "$SCRATCH" in "$PROD_VENV"*|/home/azureuser/trading_corp*) echo "ABORT: scratch under prod path: $SCRATCH"; exit 4;; esac
echo "scratch venv root: $SCRATCH (prod venv $PROD_VENV is NOT touched)"
"$PYBIN" -m venv "$SCRATCH/venv"
VPIP="$SCRATCH/venv/bin/pip"
VPY="$SCRATCH/venv/bin/python"

# 4) the hashed install — the actual smoke (full pip output shown so hash errors surface)
echo "-- pip install --require-hashes -r $LOCK --"
"$VPIP" install --require-hashes -r "$LOCK"
echo "hashed install exit: $? (0 = every pkg resolved against its hash)"

# 5) assert the 4 E1 packages + the corrected setuptools landed at locked versions
"$VPY" - <<'PYCHK'
import importlib.metadata as m
expect = [("py-clob-client","0.17.5"),("py-order-utils","0.3.2"),
          ("web3","6.11.0"),("eth-account","0.13.1"),("setuptools","80.10.2")]
bad = 0
for name, exp in expect:
    try:
        got = m.version(name)
    except Exception as e:
        got = f"MISSING ({e})"
    ok = "OK " if got == exp else "BAD"
    if got != exp: bad += 1
    print(f"  {ok} {name}=={got} (expected {exp})")
assert bad == 0, f"{bad} package(s) not at locked version"
print("package versions OK")
PYCHK

# 6) THE POINT: web3 + pkg_resources import — proof the setuptools downgrade fixes
#    the documented blocker (setuptools 82 removed pkg_resources, which web3 6.11 imports)
echo "-- web3 / pkg_resources import smoke --"
"$VPY" -c "import pkg_resources, web3, py_clob_client, eth_account; print('imports OK: web3', web3.__version__, '| pkg_resources present (setuptools', __import__('setuptools').__version__ + ')')"

echo "== SMOKE PASSED == (scratch torn down on exit; prod venv + service untouched)"
