#!/usr/bin/env bash
# Operator-run deploy: place the E1 (py-clob-client) dependency lock on prod and
# install it. READ-FIRST: run pm_e1_lock_diff.py first and confirm the changes are
# additive-only. This script ALSO re-checks additive-only and ABORTS if any already
# installed package would change version. It does NOT restart the service.
#
# Pattern mirrors the D1/HITL deploys: backup -> md5 gate -> atomic mv -> install.
# Inputs expected at /tmp (scp'd from deploy/polymarket_e1/): requirements.lock,
# requirements.txt. Hashes below are the LF-normalized staged copies.
set -euo pipefail

BASE=/home/azureuser/trading_corp
PY="$BASE/venv/bin/python"
PIP="$BASE/venv/bin/pip"
TS=$(date -u +%Y%m%d-%H%M)
TAG="pre-e1-lock-${TS}"

EXP_LOCK_MD5="a47fc93e2103bd4687ac8bd8717759c4"
EXP_TXT_MD5="2aee61909bc22cf4fdf6f68ca5166fa3"

echo "== E1 lock deploy ${TS}Z =="

# 1) Inputs present?
for f in /tmp/requirements.lock /tmp/requirements.txt; do
  [ -f "$f" ] || { echo "ABORT: missing $f (scp it first)"; exit 1; }
done

# 2) md5 gate (transfer integrity)
got_lock=$(md5sum /tmp/requirements.lock | cut -d" " -f1)
got_txt=$(md5sum /tmp/requirements.txt | cut -d" " -f1)
[ "$got_lock" = "$EXP_LOCK_MD5" ] || { echo "ABORT: requirements.lock md5 $got_lock != $EXP_LOCK_MD5"; exit 2; }
[ "$got_txt"  = "$EXP_TXT_MD5"  ] || { echo "ABORT: requirements.txt md5 $got_txt != $EXP_TXT_MD5"; exit 2; }
echo "md5 gate OK (lock=$got_lock txt=$got_txt)"

# 3) ADDITIVE-ONLY guard: abort if applying the lock would change/downgrade any
#    already-installed package. Only NEW packages are allowed.
echo "-- additive-only check vs installed --"
"$PY" - <<'PY'
import re, sys
from importlib import metadata
inst = {d.metadata["Name"].lower().replace("_","-"): d.version for d in metadata.distributions()}
changed = []
new = []
pat = re.compile(r"^([A-Za-z0-9._-]+)==([^\s;]+)")
for line in open("/tmp/requirements.lock"):
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("--"):
        continue
    m = pat.match(line)
    if not m:
        continue
    name = m.group(1).lower().replace("_","-"); ver = m.group(2)
    cur = inst.get(name)
    if cur is None:
        new.append(f"{name}=={ver}")
    elif cur != ver:
        changed.append(f"{name}: {cur} -> {ver}")
if changed:
    print("NON-ADDITIVE — these installed packages would CHANGE:")
    for c in sorted(changed): print("  "+c)
    print(f"({len(new)} new packages would also be added)")
    sys.exit(3)
print(f"ADDITIVE OK — {len(new)} new packages to add, 0 existing changed:")
for n in sorted(new): print("  "+n)
PY
echo "additive-only check passed"

# 4) backup existing files (if present), then atomic place
for f in requirements.lock requirements.txt; do
  if [ -f "$BASE/$f" ]; then
    cp -p "$BASE/$f" "$BASE/$f.$TAG"
    echo "backed up $BASE/$f -> $BASE/$f.$TAG"
  fi
done
mv /tmp/requirements.lock "$BASE/requirements.lock"
mv /tmp/requirements.txt  "$BASE/requirements.txt"
chmod 644 "$BASE/requirements.lock" "$BASE/requirements.txt"
echo "placed (post-mv md5): $(md5sum "$BASE/requirements.lock" | cut -d" " -f1)"

# 5) install with hash verification (additive in practice — already-satisfied pins
#    are skipped; only the new E1 packages + transitives are downloaded/installed)
echo "-- pip install --require-hashes --"
"$PIP" install --require-hashes -r "$BASE/requirements.lock"

# 6) import smoke test (does NOT touch the running service)
echo "-- import smoke test --"
"$PY" -c "import py_clob_client, py_order_utils, web3, eth_account; print('E1 imports OK:', web3.__version__)"

cat <<EOF

== DONE (no restart performed) ==
Deps are on disk + importable by a NEW interpreter. The LIVE process
(trading-corp.service) will NOT import py_clob_client until it is restarted.
A restart bounces ALL live divisions (Bitunix is live) — time it for a flat
window. PCT is still broker:paper, so there is NO need to restart now; defer
the restart to the PCT live cutover (E2). Restart cmd, when ready:
  sudo systemctl restart trading-corp.service
Rollback (restore prior lock/txt):
  mv $BASE/requirements.lock.$TAG $BASE/requirements.lock   # if a backup was made
  mv $BASE/requirements.txt.$TAG  $BASE/requirements.txt
  (re-run pip install --require-hashes to revert installed pkgs if needed)
EOF
