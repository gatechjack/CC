#!/usr/bin/env bash
# Deploy: bitunix /tpsl/ TP-leg-fix (response-parse + untracked-leg hardening).
# Fixes place_tpsl_order, which crashed all 3 TP legs on live trade cb6b4d4a with
# AttributeError "'list' object has no attribute 'get'" — it did
# (data or {}).get("orderId") but the live /tpsl/place_order returns a LIST
# ([{"orderId": ...}]) while the docs show a dict. legs_placed=0 on every
# multi-leg entry (Section-B report c8a426d). The fix parses BOTH shapes
# (_extract_tpsl_order_id) and raises BitunixUntrackedTpslOrder + emits a
# bracket_tp_leg_untracked audit when a POST is accepted but no id is parsed, so
# a maybe-resting leg can never again be silently dropped. Branch
# bitunix-tpsl-rebuild-2026-06-18, fix commit 8d3d164; 62/62 bitunix green, full
# suite 28F+3E == known baseline, zero new regressions.
#
# DEPLOY SET = 3 files, derived from the diff 626e959..8d3d164 (deployed
# /tpsl/-rebuild base -> fix). bitunix_exceptions.py IS in the set: the fix adds
# class BitunixUntrackedTpslOrder there and BOTH bitunix.py and the observer now
# import it — shipping the two without it would ImportError at load. NOT the
# reconciler (unchanged by 8d3d164). NO main.py/db.py/models.py/logger.py/
# data_exec.py/cutover/polymarket. NO test files (not deployed).
#
# DRIFT MODEL: all 3 at the DEPLOYED /tpsl/-rebuild state (prod == base, verified
# read-only 2026-06-19: bitunix.py 74aa1b42, bitunix_exceptions.py 363b044e,
# observer 19da15ff). ABORT on any drift.
#
# PREPARE/APPLY-ONLY. Does prod writes (backup + md5-gated atomic mv). Does NOT
# restart. Restart is the operator's step, via az vm run-command (this box's
# ssh+sudo does NOT work; run-command is root/no-password):
#   az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
#     --command-id RunShellScript --scripts "systemctl restart trading-corp"
# Correctness-not-urgent: prefer to run the restart while FLAT (the restart
# bounces the live bitunix division through a brief flat window).
# Delivery (before running): place the staged tree at $STAGE (mirrors
# trading_corp/...). Stream-run: Get-Content this.sh -Raw | ssh ... "tr -d '\r'|bash"
set -euo pipefail

BASE=/home/azureuser/trading_corp
STAGE="$BASE/_tpsl_legfix_stage"
BAK=".bak-pre-tpsl-legfix-2026-06-19"
PY="$BASE/venv/bin/python"
cd "$BASE"

FILES="trading_corp/brokers/bitunix.py trading_corp/brokers/bitunix_exceptions.py trading_corp/agents/divisions/bitunix_futures_observer.py"

# DRIFT-GUARD md5 (prod must equal BASE = the deployed 2026-06-18 /tpsl/ rebuild)
base_md5() { case "$1" in
  trading_corp/brokers/bitunix.py)                           echo 74aa1b424dcb73840f9f636151098348;;
  trading_corp/brokers/bitunix_exceptions.py)                echo 363b044e6c87489b138fa8a489296d14;;
  trading_corp/agents/divisions/bitunix_futures_observer.py) echo 19da15ff4401996ba31e50cf6f3d59a0;;
esac; }
# TARGET md5 (deploy target = 8d3d164 LF blobs)
tgt_md5() { case "$1" in
  trading_corp/brokers/bitunix.py)                           echo 00bd03a8e8ad2d5ca34767a8d123eff9;;
  trading_corp/brokers/bitunix_exceptions.py)                echo 62ddd11cebc67affd3c0b56d06cb396c;;
  trading_corp/agents/divisions/bitunix_futures_observer.py) echo f167e456fa2f2a2a6edd86fcf93da5c1;;
esac; }
m5() { md5sum "$1" | cut -d' ' -f1; }

echo "===== tpsl-legfix deploy (3 files, NO RESTART) ====="

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

echo "-- 1. DRIFT GUARD: 3 existing == BASE (prod==deployed-rebuild; ABORT on drift) --"
for f in $FILES; do
  pm=$(m5 "$f"); exp=$(base_md5 "$f")
  [ "$pm" = "$exp" ] || { echo "ABORT drift: $f is $pm, expected base $exp (prod moved since prep — STOP+surface)"; exit 1; }
done
echo "   OK (3 existing at base = 2026-06-18 deployed /tpsl/ rebuild)"

echo "-- 2. INSTALL: backup -> *$BAK, md5-gated atomic mv --"
for f in $FILES; do
  want=$(tgt_md5 "$f")
  cp -p "$f" "$f$BAK"
  cp -p "$STAGE/$f" "$f.legfix-new"
  mv "$f.legfix-new" "$f"
  pm=$(m5 "$f"); [ "$pm" = "$want" ] || { echo "ABORT post-mv: $f is $pm != $want"; exit 1; }
  echo "   installed $f ($pm)  [backup $f$BAK]"
done

echo "-- 3. py_compile all 3 installed (final confirm) --"
"$PY" -m py_compile $FILES && echo "   compile OK"

echo "-- 4. final md5 (== target) --"
for f in $FILES; do echo "   $(m5 "$f")  $f"; done

cat <<'EOF'
===== APPLIED (NO RESTART). =====
NEXT (operator, remote-mobile — az run-command, NOT ssh+sudo); prefer FLAT:
  az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
    --command-id RunShellScript --scripts "systemctl restart trading-corp"
THEN run VERIFY.md A (engine up / new PID != 2988577 / 3 md5s==target /
  main.py f16e9c24 + db.py a2c2ff46 UNCHANGED / paper=False /
  --live-divisions has bitunix_futures / staleness gate / execution_mode:live /
  DD-cap 0.99 / B2 OFF / reconciler clean / flat-no-orphan).
VERIFY.md B needs a live >=0.0012 BTC multi-leg entry (bracket_placed
  legs_placed=3 + tp_order_ids populated, no 'list' AttributeError; and the
  bracket_tp_leg_untracked audit fires if any leg fails to track).
ROLLBACK (operator; restores the TP-legs-untracked-but-fail-soft state):
  cd /home/azureuser/trading_corp
  for f in trading_corp/brokers/bitunix.py trading_corp/brokers/bitunix_exceptions.py trading_corp/agents/divisions/bitunix_futures_observer.py; do mv "$f.bak-pre-tpsl-legfix-2026-06-19" "$f"; done
  then az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "systemctl restart trading-corp"
EOF
