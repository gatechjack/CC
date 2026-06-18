#!/usr/bin/env bash
# Deploy: bitunix /tpsl/ bracket rebuild (steps 1-4 + path fix).
# Makes managed exits + the SL-trail actually work via the native /tpsl/ order
# family. Fixes the live-confirmed failure on trade 7d1a78dc: SL-trail 404 +
# ~22 rejected managed TP exits on the deployed bracket code (B1 entry-stop +
# auto-book saved that trade). Branch bitunix-tpsl-rebuild-2026-06-18, tip
# 626e959; 651 tests green, zero regressions.
#
# DEPLOY SET = 3 files, derived from the diff 7e7a2e1..626e959 (deployed base ->
# tip). bitunix_bracket.py is NOT in the diff (kept unchanged/pure). NO
# main.py/db.py/models.py/logger.py/data_exec.py/cutover/polymarket.
#
# DRIFT MODEL: all 3 at-base. prod == base == the 2026-06-17 bracket+E2.5
# deployed state (verified prod md5 == base 2026-06-18). ABORT on any drift.
#
# PREPARE/APPLY-ONLY. Does prod writes (backup + md5-gated atomic mv). Does NOT
# restart. Restart is the operator's step, via az vm run-command (this box's
# ssh+sudo does NOT work; run-command is root/no-password):
#   az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
#     --command-id RunShellScript --scripts "systemctl restart trading-corp"
# Delivery (before running): place the staged tree at $STAGE (mirrors
# trading_corp/...). Stream-run: Get-Content this.sh -Raw | ssh ... "tr -d '\r'|bash"
set -euo pipefail

BASE=/home/azureuser/trading_corp
STAGE="$BASE/_tpsl_rebuild_stage"
BAK=".bak-pre-tpsl-rebuild-2026-06-18"
PY="$BASE/venv/bin/python"
cd "$BASE"

FILES="trading_corp/brokers/bitunix.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py"

# DRIFT-GUARD md5 (prod must equal BASE = 7e7a2e1 = 2026-06-17 deployed state)
base_md5() { case "$1" in
  trading_corp/brokers/bitunix.py)                              echo 7a3da849cadfe32940649c9aba514ef3;;
  trading_corp/agents/divisions/bitunix_futures_observer.py)    echo 13469b104894dfea0e727fe9a495c13d;;
  trading_corp/agents/divisions/bitunix_position_reconciler.py) echo 386cc6c243347dce65c60f55c3480ae6;;
esac; }
# TARGET md5 (deploy target = 626e959 LF blobs)
tgt_md5() { case "$1" in
  trading_corp/brokers/bitunix.py)                              echo 74aa1b424dcb73840f9f636151098348;;
  trading_corp/agents/divisions/bitunix_futures_observer.py)    echo 19da15ff4401996ba31e50cf6f3d59a0;;
  trading_corp/agents/divisions/bitunix_position_reconciler.py) echo 707c682858f40245d06aee9dc8f94e00;;
esac; }
m5() { md5sum "$1" | cut -d' ' -f1; }

echo "===== tpsl-rebuild deploy (3 files, NO RESTART) ====="

echo "-- 0. staged tree present + matches TARGET (touch nothing on fail) --"
[ -d "$STAGE" ] || { echo "ABORT: $STAGE missing (deliver staged tree first)"; exit 1; }
for f in $FILES; do
  [ -f "$STAGE/$f" ] || { echo "ABORT: staged $f missing"; exit 1; }
  sm=$(m5 "$STAGE/$f"); want=$(tgt_md5 "$f")
  [ "$sm" = "$want" ] || { echo "ABORT: staged $f md5 $sm != target $want"; exit 1; }
done
echo "   OK (3 staged == target)"

echo "-- 0b. PREFLIGHT py_compile staged (abort before touching prod) --"
STAGED_PATHS=""; for f in $FILES; do STAGED_PATHS="$STAGED_PATHS $STAGE/$f"; done
"$PY" -m py_compile $STAGED_PATHS && echo "   staged compile OK"

echo "-- 1. DRIFT GUARD: 3 existing == BASE (prod==base; ABORT on any drift) --"
for f in $FILES; do
  pm=$(m5 "$f"); exp=$(base_md5 "$f")
  [ "$pm" = "$exp" ] || { echo "ABORT drift: $f is $pm, expected base $exp (prod moved since prep — STOP+surface)"; exit 1; }
done
echo "   OK (3 existing at base = 2026-06-17 deployed state)"

echo "-- 2. INSTALL: backup -> *$BAK, md5-gated atomic mv --"
for f in $FILES; do
  want=$(tgt_md5 "$f")
  cp -p "$f" "$f$BAK"
  cp -p "$STAGE/$f" "$f.tpsl-new"
  mv "$f.tpsl-new" "$f"
  pm=$(m5 "$f"); [ "$pm" = "$want" ] || { echo "ABORT post-mv: $f is $pm != $want"; exit 1; }
  echo "   installed $f ($pm)  [backup $f$BAK]"
done

echo "-- 3. py_compile all 3 installed (final confirm) --"
"$PY" -m py_compile $FILES && echo "   compile OK"

echo "-- 4. final md5 (== target) --"
for f in $FILES; do echo "   $(m5 "$f")  $f"; done

cat <<'EOF'
===== APPLIED (NO RESTART). =====
NEXT (operator, remote-mobile — az run-command, NOT ssh+sudo):
  az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
    --command-id RunShellScript --scripts "systemctl restart trading-corp"
THEN run VERIFY.md (engine up / new PID / 3 md5s==target / paper=False /
  --live-divisions has bitunix_futures / staleness gate / execution_mode:live /
  DD-cap 0.99 / B2 OFF / no main.py-db.py touch / reconciler clean / flat-no-orphan).
ROLLBACK (operator; restores the safe-as-is bracket):
  cd /home/azureuser/trading_corp
  for f in trading_corp/brokers/bitunix.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py; do mv "$f.bak-pre-tpsl-rebuild-2026-06-18" "$f"; done
  then az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "systemctl restart trading-corp"
EOF
