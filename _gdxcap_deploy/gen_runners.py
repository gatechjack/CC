#!/usr/bin/env python3
"""Generate the box-only deploy .sh scripts for MACE #2 (GDX time-exit/PT cap).
Base = box-truth 7683f59b (deployed #1), target = c2593e34. 4 MACE-scoped files
(config/mace.yaml + mace/config.py/execution.py/manager.py). NO shared-trio, NO FF-push.
Run: python _gdxcap_deploy/gen_runners.py  (from the worktree root)."""
import base64, pathlib

HERE = pathlib.Path(__file__).resolve().parent
BLOBS = HERE / "blobs"
HEAD = "c2593e34"

# key -> (base_sha [7683f59b box], target_sha [c2593e34])
SHAS = {
    "maceyaml":  ("49476b0ec0242960207fe2c76384f7533e6e7e758f6364545ef130621fc1a8ad",
                  "bfde856f1c468d115812eeae2a923c1e5b4567e18ba703a5570f3d9df8b2c5cb"),
    "config":    ("d5aeb3652a44516e9edf1a28b1bdca8bee25cd50e5306ec0139fa7565a5855e3",
                  "43fabae5345a4c5298c373d2c7a049603a7886c36e9e0ce5b557292fe1796ee1"),
    "execution": ("46464fa3a03f98f7c792325abbc352bb8e997d421fa13f42160d356cce7a06ed",
                  "073b6eeddbec164d6890c231040d476f0cf6f4b97551b714be5fa79dcc7b5b93"),
    "manager":   ("deb073d368e5ab885863a7a00ee1f46a1a45917f84eb27bf744613fc3d753c31",
                  "a28d4e633caef601fba0ed6bc66813a673894995e2ff2ffafd278bc1622475dd"),
}
BLOBFILE = {"maceyaml": "maceyaml.blob", "config": "config.blob", "execution": "execution.blob",
            "manager": "manager.blob", "test": "test_exec.blob"}


def b64(key):
    return base64.b64encode((BLOBS / BLOBFILE[key]).read_bytes()).decode("ascii")


def inject(text):
    text = text.replace("__HEAD__", HEAD)
    for k, (base, tgt) in SHAS.items():
        text = text.replace("__BASE_%s__" % k.upper(), base)
        text = text.replace("__TGT_%s__" % k.upper(), tgt)
    for k in ("maceyaml", "config", "execution", "manager", "test"):
        text = text.replace("__B64_%s__" % k.upper(), b64(k))
    for tok in ("__B64_", "__BASE_", "__TGT_", "__HEAD__"):
        assert tok not in text, "unreplaced placeholder: " + tok
    return text


GRAFT = r'''#!/bin/bash
# MACE #2 GDX time-exit/PT cap GRAFT (branch mace-gdx-cap-boxbase-2026-09-11 @ __HEAD__).
# Box-only, POST-CLOSE, after backup. Pre-checks box==7683f59b (box-truth = deployed #1) -> writes 4
# MACE files -> verifies box==c2593e34 -> FORK-PRESERVE check (#1's mace_missed_exit + exit_disposition_line
# survive in execution.py). MACE-scoped: main.py NOT touched. NO restart here, NO FF-push.
set -u
ROOT=/home/azureuser/trading_corp; PKG=$ROOT/trading_corp
declare -A PA=( [maceyaml]=$ROOT/config/mace.yaml [config]=$PKG/mace/config.py [execution]=$PKG/mace/execution.py [manager]=$PKG/mace/manager.py )
declare -A BASE=( [maceyaml]=__BASE_MACEYAML__ [config]=__BASE_CONFIG__ [execution]=__BASE_EXECUTION__ [manager]=__BASE_MANAGER__ )
declare -A TGT=( [maceyaml]=__TGT_MACEYAML__ [config]=__TGT_CONFIG__ [execution]=__TGT_EXECUTION__ [manager]=__TGT_MANAGER__ )
B64_maceyaml='__B64_MACEYAML__'
B64_config='__B64_CONFIG__'
B64_execution='__B64_EXECUTION__'
B64_manager='__B64_MANAGER__'
echo "=== GRAFT PRE-CHECK: box == box-truth 7683f59b (deployed #1, no drift) ==="
abort=0
for k in maceyaml config execution manager; do
  f=${PA[$k]}
  [ -f "$f" ] || { echo "MISSING $f"; abort=1; continue; }
  cur=$(tr -d '\r' < "$f" | sha256sum | cut -d' ' -f1)
  if [ "$cur" = "${BASE[$k]}" ]; then echo "OK base           $k"
  elif [ "$cur" = "${TGT[$k]}" ]; then echo "ALREADY-GRAFTED   $k (idempotent re-run)"
  else echo "DRIFT             $k box=$cur"; echo "                  expected base=${BASE[$k]}"; abort=1; fi
done
if [ $abort -ne 0 ]; then echo "*** ABORT: box drifted from 7683f59b / missing -- NOT grafting. Re-derive base (is #1 deployed?). ***"; exit 2; fi
echo "=== GRAFT WRITE (base64 -> file; in-place content replace preserves perms) ==="
for k in maceyaml config execution manager; do
  f=${PA[$k]}; v="B64_$k"; t="/tmp/gdxcap_${k}_$$"
  printf '%s' "${!v}" | base64 -d > "$t" || { echo "DECODE FAIL $k"; exit 3; }
  cat "$t" > "$f" && rm -f "$t" && echo "wrote             $k -> $f" || { echo "WRITE FAIL $k"; exit 3; }
done
echo "=== GRAFT POST-VERIFY: box == c2593e34 ==="
fail=0
for k in maceyaml config execution manager; do
  f=${PA[$k]}; cur=$(tr -d '\r' < "$f" | sha256sum | cut -d' ' -f1)
  if [ "$cur" = "${TGT[$k]}" ]; then echo "OK target         $k"; else echo "FAIL              $k box=$cur want=${TGT[$k]}"; fail=1; fi
done
echo "=== FORK-PRESERVE: #1 lines survive in the grafted execution.py ==="
E=$PKG/mace/execution.py
mm=$(grep -cF "mace_missed_exit" "$E"); edl=$(grep -cF "exit_disposition_line" "$E")
echo "mace_missed_exit=$mm (want >=1)  exit_disposition_line=$edl (want >=4)"
[ "${mm:-0}" -ge 1 ] || { echo "MISSING mace_missed_exit -- fork NOT preserved"; fail=1; }
[ "${edl:-0}" -ge 4 ] || { echo "MISSING exit_disposition_line -- box code reverted"; fail=1; }
echo "=== MACE-SCOPED: confirm main.py NOT in the graft set (only these 4 files change) ==="
echo "grafted: config/mace.yaml, mace/config.py, mace/execution.py, mace/manager.py  (main.py/data_exec.py/robinhood.py UNTOUCHED)"
if [ $fail -ne 0 ]; then echo "*** GRAFT FAILED -- run the rollback.sh from the backup step ***"; exit 4; fi
echo "=== GRAFT GREEN: 4 files == c2593e34, fork preserved. Board: RESTART post-close (restart_tc.ps1), THEN bootverify. NO FF-push. ==="
'''

PREGATE = r'''#!/bin/bash
# MACE #2 GDX-cap PRE-GATE: box-scratch FULL suite, baseline-vs-modified diff. Isolated /tmp (NO
# venv/data), throwaway DB, engine NEVER restarted, no live-file writes. GREEN iff 0 NEW failures
# vs the box baseline (the 3 known pre-existing fails are in the baseline -> not counted). POST-CLOSE, pre-graft.
set -u
ROOT=/home/azureuser/trading_corp; REL=trading_corp
VENV=$ROOT/venv/bin/python
SCR=/tmp/tc_gdxcap_$$
B64_maceyaml='__B64_MACEYAML__'
B64_config='__B64_CONFIG__'
B64_execution='__B64_EXECUTION__'
B64_manager='__B64_MANAGER__'
B64_test='__B64_TEST__'
echo "=== PRE-GATE box-scratch full suite  SCRATCH=$SCR ==="
"$VENV" --version
mkdir -p "$SCR" || { echo "scratch mkdir failed"; exit 2; }
echo "--- copy box code -> scratch (excl venv/data/.git/pycache) ---"
(cd "$ROOT" && tar --exclude=./venv --exclude=./data --exclude=./.git --exclude=__pycache__ --exclude='*.pyc' -cf - .) | (cd "$SCR" && tar -xf -) || { echo "scratch copy failed"; rm -rf "$SCR"; exit 2; }
export TRADING_CORP_DB_URL="sqlite:///$SCR/_gdxcap_throwaway.db"
export PYTHONDONTWRITEBYTECODE=1
cd "$SCR"
echo "--- BASELINE run (unmodified box code = deployed #1) ---"
"$VENV" -m pytest tests -p no:pytest_ethereum -p no:cacheprovider --tb=no -q -rfE 2>&1 | grep -E '^(FAILED|ERROR)' | sed 's/[[:space:]].*//' | sort -u > /tmp/gdxcap_base_$$ ; true
BASEN=$(wc -l < /tmp/gdxcap_base_$$ | tr -d ' ')
echo "baseline FAILED/ERROR node-ids: $BASEN"
echo "--- overlay the #2 build (5 files) ---"
printf '%s' "$B64_maceyaml"  | base64 -d > "$SCR/config/mace.yaml"
printf '%s' "$B64_config"    | base64 -d > "$SCR/$REL/mace/config.py"
printf '%s' "$B64_execution" | base64 -d > "$SCR/$REL/mace/execution.py"
printf '%s' "$B64_manager"   | base64 -d > "$SCR/$REL/mace/manager.py"
printf '%s' "$B64_test"      | base64 -d > "$SCR/tests/test_mace_execution.py"
echo "--- MODIFIED run (with #2 build) ---"
"$VENV" -m pytest tests -p no:pytest_ethereum -p no:cacheprovider --tb=no -q -rfE 2>&1 | grep -E '^(FAILED|ERROR)' | sed 's/[[:space:]].*//' | sort -u > /tmp/gdxcap_mod_$$ ; true
MODN=$(wc -l < /tmp/gdxcap_mod_$$ | tr -d ' ')
echo "modified FAILED/ERROR node-ids: $MODN"
echo "=== NEW failures introduced by the build (MUST be empty) ==="
comm -13 /tmp/gdxcap_base_$$ /tmp/gdxcap_mod_$$ > /tmp/gdxcap_new_$$ ; cat /tmp/gdxcap_new_$$
NEWN=$(wc -l < /tmp/gdxcap_new_$$ | tr -d ' ')
echo "--- the new GDX-cap tests (must pass) ---"
"$VENV" -m pytest tests/test_mace_execution.py -p no:pytest_ethereum -p no:cacheprovider -q -k "winner or stop_unchanged or defer or close_pricing or gdx" 2>&1 | tail -2
echo "=== FIXED-by-build (baseline-only) node-ids [informational] ==="
comm -23 /tmp/gdxcap_base_$$ /tmp/gdxcap_mod_$$
rm -rf "$SCR" /tmp/gdxcap_base_$$ /tmp/gdxcap_mod_$$ /tmp/gdxcap_new_$$
echo "=== PRE-GATE SUMMARY: baseline=$BASEN modified=$MODN NEW=$NEWN ==="
[ "$NEWN" -eq 0 ] && echo "=== PRE-GATE GREEN: 0 new failures vs baseline. Proceed to backup + graft. ===" || echo "*** PRE-GATE RED: $NEWN NEW failures -- DO NOT DEPLOY ***"
'''

BACKUP = r'''#!/bin/bash
# MACE #2 GDX-cap BACKUP + rollback authoring. POST-CLOSE, pre-graft.
set -u
ROOT=/home/azureuser/trading_corp; PKG=$ROOT/trading_corp
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK=$ROOT/gdxcap_backup_$TS
RB=$ROOT/gdxcap_rollback_$TS.sh
mkdir -p "$BK" || { echo "backup mkdir failed"; exit 2; }
declare -A PA=( [maceyaml]=$ROOT/config/mace.yaml [config]=$PKG/mace/config.py [execution]=$PKG/mace/execution.py [manager]=$PKG/mace/manager.py )
echo "=== BACKUP -> $BK ==="
fail=0
for k in maceyaml config execution manager; do
  f=${PA[$k]}; b=$BK/$(basename "$f")
  cp -p "$f" "$b" || { echo "cp fail $k"; fail=1; continue; }
  m=$(md5sum < "$f" | cut -d' ' -f1); mb=$(md5sum < "$b" | cut -d' ' -f1)
  if [ "$m" = "$mb" ]; then echo "OK $k md5=$m"; else echo "MD5 MISMATCH $k"; fail=1; fi
done
[ $fail -eq 0 ] || { echo "*** BACKUP FAILED -- do not graft ***"; exit 3; }
cat > "$RB" <<EOF
#!/bin/bash
# Rollback the MACE #2 GDX-cap graft: restore the 4 files from $BK (== box-truth 7683f59b).
set -u
cp -p "$BK/mace.yaml" "$ROOT/config/mace.yaml"        && echo "restored config/mace.yaml"
cp -p "$BK/config.py" "$PKG/mace/config.py"           && echo "restored config.py"
cp -p "$BK/execution.py" "$PKG/mace/execution.py"     && echo "restored execution.py"
cp -p "$BK/manager.py" "$PKG/mace/manager.py"         && echo "restored manager.py"
echo "=== ROLLBACK DONE -- board must RESTART (restart_tc.ps1) to load the restored files. ==="
EOF
chmod +x "$RB"
echo "=== BACKUP GREEN ==="
echo "BACKUP_DIR=$BK"
echo "ROLLBACK  =$RB"
'''

for name, tpl in (("graft.sh", GRAFT), ("pregate.sh", PREGATE), ("backup.sh", BACKUP)):
    (HERE / name).write_bytes(inject(tpl).encode("utf-8"))
    print("wrote", name)
print("done")
