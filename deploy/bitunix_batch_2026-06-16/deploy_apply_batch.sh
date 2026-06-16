#!/usr/bin/env bash
# Bitunix deploy batch 2026-06-16 — APPLY the 4 reviewed bitunix branches' merged
# files to prod, in ONE window / ONE restart. PREPARED by the consolidation task;
# RUN BY THE OPERATOR in the deploy window (NOT by the agent — §4 prod write).
#
# Pattern: md5-gate (prod==BASE, staged==TARGET) -> py_compile -> backup ->
# atomic-mv -> re-verify. Idempotent-ish (re-running after a full apply will
# ABORT at the gate because prod==TARGET!=BASE — that's the desired safety stop).
#
# SCOPE: bitunix ALONE. Polymarket EXCLUDED. data_exec.py EXCLUDED (prod is
# behind main on it via the undeployed polymarket E2.5 block; the batch's only
# data_exec change is a doc comment — see DEPLOY_PLAN.md). strategies.yaml is a
# TARGETED edit (maker_entry keys only) — NEVER whole-file (prod holds
# execution_mode:live + kalshi-disable that the repo version lacks).
#
# Prereq: run stage_batch.sh LOCALLY first to push the 6 merged files (LF) to
#   $STAGE on prod. This script does NOT restart — that is a separate operator step.
set -euo pipefail

ROOT=/home/azureuser/trading_corp
STAGE="${STAGE:-/home/azureuser/deploy_stage_bitunix_batch}"
BAK="bak-pre-batch-2026-06-16"
PY="$ROOT/venv/bin/python"

# rel-path : BASE md5 (prod-current, measured 2026-06-16) : TARGET md5 (merged deploy-prep, git LF blob)
FILES=(
"trading_corp/brokers/bitunix.py:64d857246a0879c4378e5b3a4185874e:70f7904f676e9dd76b1f8ef384226e66"
"trading_corp/brokers/base.py:68d40f230f5a7937f7837cccde960eb1:a7886843d52a6ba74fb0eb6e5a9c0bcd"
"trading_corp/agents/divisions/bitunix_futures_observer.py:e30f17565bff0132aba215568eb8b8f5:3067a3e9d979624dca040657632dd1ba"
"trading_corp/agents/divisions/bitunix_position_reconciler.py:ae2fbc74895d5b4341f0d2d0804579c1:bf048cd14f11cd2b1c5a91bd6b4c0f1d"
"trading_corp/brokers/bitunix_exceptions.py:4c78ebca522818c27c5acbe7806e8314:363b044e6c87489b138fa8a489296d14"
"trading_corp/agents/strategies/trade_plan.py:74b9b9def4e8a3f1434f40ef5a69183f:67f0ff2b3edc32d6f007f3fdfdff5d40"
)

md5of(){ md5sum "$1" | cut -d' ' -f1; }

echo "=== PRE-FLIGHT: md5-gate + py_compile (no writes) ==="
for entry in "${FILES[@]}"; do
  IFS=: read -r rel base target <<< "$entry"
  prod="$ROOT/$rel"; staged="$STAGE/$rel"
  [ -f "$prod" ]   || { echo "ABORT: prod missing $prod"; exit 1; }
  [ -f "$staged" ] || { echo "ABORT: staged missing $staged (run stage_batch.sh first)"; exit 1; }
  pm=$(md5of "$prod"); sm=$(md5of "$staged")
  [ "$pm" = "$base" ]   || { echo "ABORT: $rel prod md5 $pm != BASE $base (prod drifted — STOP, re-measure)"; exit 1; }
  [ "$sm" = "$target" ] || { echo "ABORT: $rel staged md5 $sm != TARGET $target (wrong/garbled staged file)"; exit 1; }
  "$PY" -m py_compile "$staged" || { echo "ABORT: $rel staged fails py_compile"; exit 1; }
  echo "OK   $rel"
done

echo "=== APPLY: backup -> atomic-mv -> re-verify ==="
for entry in "${FILES[@]}"; do
  IFS=: read -r rel base target <<< "$entry"
  prod="$ROOT/$rel"; staged="$STAGE/$rel"
  cp -p "$prod" "$prod.$BAK"
  tmp="$prod.tmp-batch"; cp "$staged" "$tmp"; mv "$tmp" "$prod"   # same-fs atomic rename
  nm=$(md5of "$prod")
  [ "$nm" = "$target" ] || { echo "FAIL: $rel post-apply md5 $nm != TARGET $target"; exit 1; }
  echo "APPLIED $rel  (backup: $prod.$BAK)"
done

echo "=== strategies.yaml TARGETED edit (maker_entry keys; NEVER whole-file) ==="
"$PY" - "$ROOT/config/strategies.yaml" "$BAK" <<'PYEOF'
import sys, shutil, os, yaml
path, bak = sys.argv[1], sys.argv[2]
src = open(path, encoding="utf-8").read()
d = yaml.safe_load(src)
bx = d["bitunix_futures"]
# CANARY: refuse to touch a strategies.yaml that isn't the live prod one.
assert bx.get("execution_mode") == "live", \
    "ABORT: strategies.yaml execution_mode != 'live' — refusing (wrong/clobbered file)"
if "maker_entry_enabled" in bx.get("fees", {}):
    print("NOOP: maker_entry keys already present"); raise SystemExit(0)
block = (
    "    # B2 maker (POST_ONLY) entry execution — DEFAULT OFF (flip deliberately).\n"
    "    maker_entry_enabled: false\n"
    "    maker_entry_rest_timeout_s: 2.0\n"
    "    maker_entry_offset_pct: 0.0\n"
    "    maker_entry_fallback_mode: cross_to_taker\n"
)
out, inserted = [], False
for ln in src.splitlines(keepends=True):
    out.append(ln)
    if not inserted and ln.strip().startswith("tp_is_maker:"):
        out.append(block); inserted = True
assert inserted, "ABORT: 'tp_is_maker:' anchor not found in fees block"
new = "".join(out)
nd = yaml.safe_load(new)               # validate BEFORE writing
assert nd["bitunix_futures"]["fees"]["maker_entry_enabled"] is False, "maker not default-OFF"
assert nd["bitunix_futures"]["execution_mode"] == "live", "execution_mode NOT preserved"
shutil.copy2(path, path + "." + bak)
tmp = path + ".tmp-batch"; open(tmp, "w", encoding="utf-8").write(new)
yaml.safe_load(open(tmp, encoding="utf-8"))   # re-parse from disk
os.replace(tmp, path)
print("APPLIED strategies.yaml maker_entry keys (execution_mode:live preserved, default OFF)")
PYEOF

echo
echo "=== APPLY COMPLETE. NEXT STEPS (operator, separate) ==="
echo "  1. systemctl restart trading-corp      # loads all 4 fixes in ONE bounce"
echo "  2. run the post-restart verification checklist (DEPLOY_PLAN.md section 5)"
