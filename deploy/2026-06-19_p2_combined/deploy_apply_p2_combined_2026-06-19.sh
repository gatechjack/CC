#!/usr/bin/env bash
# Deploy: P2 classifier fix + maker/taker recording (branch tip d83e877), the
# P2 delta stacked ON TOP of the already-live /tpsl/ TP-leg legfix.
#
# WHAT IT FIXES
#   * P2 auto-book mis-sign: result now derives from the NET PnL sign (was the
#     hard-coded 'loss'); exit_kind from the actual fill (order-id match → tp/stop,
#     else price-vs-levels, else 'unknown' — never defaulting to 'stop').
#   * maker/taker recording: roleType (MAKER/TAKER) → $.entry_role/$.exit_role +
#     $.maker_taker_mix (forward-only telemetry).
#
# DEPLOY SET = 5 files, derived from the diff 8d3d164..d83e877 (the DEPLOYED
# legfix state -> branch tip). NOT bitunix_exceptions.py (already at target on
# prod from the legfix, 62ddd11c). NO main.py/db.py/cutover/polymarket. ZERO
# CONFIG: the mc_a_yellow_x strategies.yaml edit is DEFERRED to a separate
# targeted operator config-edit (prod config 569c38f8 has drifted from the repo;
# a full-file deploy would clobber live settings).
#
# models.py — ONE-TIME §4 BOARD-OVERRIDE of the normally-forbidden list. The tip
# bitunix.py REQUIRES it: it constructs FillEvent(role=...) and _observe_fill
# returns a 5-tuple, so deploying bitunix.py without the tip models.py → TypeError
# on the next fill. The FillEvent.role addition is ADDITIVE (default '') and
# backward-compatible (no other division breaks). models.py + bitunix.py SHIP
# COUPLED — the coupling guard below refuses to apply one without the other.
#
# DRIFT MODEL: all 5 at the LEGFIX state (prod == base, read-only re-confirmed
# 2026-06-19: bitunix.py 00bd03a8, observer f167e456, reconciler 707c6828,
# bracket bd639224, models a781b495). ABORT on any drift.
#
# PREPARE/APPLY-ONLY. Does prod writes (backup + md5-gated atomic mv). Does NOT
# restart. Restart is the operator's step (this box's ssh+sudo restart works, but
# the standard path is az run-command, root/no-password):
#   az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
#     --command-id RunShellScript --scripts "systemctl restart trading-corp"
# Delivery: place the staged tree at $STAGE (mirrors trading_corp/...). Stream-run:
#   Get-Content this.sh -Raw | ssh ... "tr -d '\r'|bash"
set -euo pipefail

BASE=/home/azureuser/trading_corp
STAGE="$BASE/_p2_combined_stage"
BAK=".bak-pre-p2-combined-2026-06-19"
PY="$BASE/venv/bin/python"
cd "$BASE"

FILES="trading_corp/brokers/bitunix.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py trading_corp/agents/divisions/bitunix_bracket.py trading_corp/persistence/models.py"

# DRIFT-GUARD md5 (prod must equal BASE = the deployed legfix state)
base_md5() { case "$1" in
  trading_corp/brokers/bitunix.py)                              echo 00bd03a8e8ad2d5ca34767a8d123eff9;;
  trading_corp/agents/divisions/bitunix_futures_observer.py)    echo f167e456fa2f2a2a6edd86fcf93da5c1;;
  trading_corp/agents/divisions/bitunix_position_reconciler.py) echo 707c682858f40245d06aee9dc8f94e00;;
  trading_corp/agents/divisions/bitunix_bracket.py)             echo bd639224ef193736e05967d78dce0a5b;;
  trading_corp/persistence/models.py)                           echo a781b495b6ff4a859a0a4e11d04cd5f5;;
esac; }
# TARGET md5 (deploy target = d83e877 LF blobs)
tgt_md5() { case "$1" in
  trading_corp/brokers/bitunix.py)                              echo 3f68473a4ddfe27ca035308414c1c280;;
  trading_corp/agents/divisions/bitunix_futures_observer.py)    echo a31a10f1445f0263389c377c41f742f8;;
  trading_corp/agents/divisions/bitunix_position_reconciler.py) echo bd06ea281a853687fad8d0a6831e9c0a;;
  trading_corp/agents/divisions/bitunix_bracket.py)             echo f4be4e9b8af36afac9a2489ebeb42c56;;
  trading_corp/persistence/models.py)                           echo d7561d3c95530f74071ab195d239c4ce;;
esac; }
m5() { md5sum "$1" | cut -d' ' -f1; }

echo "===== p2-combined deploy (5 files, NO RESTART, NO CONFIG) ====="

echo "-- 0. staged tree present + matches TARGET (touch nothing on fail) --"
[ -d "$STAGE" ] || { echo "ABORT: $STAGE missing (deliver staged tree first)"; exit 1; }
for f in $FILES; do
  [ -f "$STAGE/$f" ] || { echo "ABORT: staged $f missing"; exit 1; }
  sm=$(m5 "$STAGE/$f"); want=$(tgt_md5 "$f")
  [ "$sm" = "$want" ] || { echo "ABORT: staged $f md5 $sm != target $want"; exit 1; }
done
echo "   OK (5 staged == target)"

echo "-- 0b. NO CONFIG file in the set (code-only; yellow_x deferred) --"
for f in $FILES; do
  case "$f" in *.yaml|*.yml|config/*) echo "ABORT: config file $f in set (code-only)"; exit 1;; esac
done
echo "   OK (zero config files)"

echo "-- 0c. PREFLIGHT py_compile staged (abort before touching prod) --"
STAGED_PATHS=""; for f in $FILES; do STAGED_PATHS="$STAGED_PATHS $STAGE/$f"; done
"$PY" -m py_compile $STAGED_PATHS && echo "   staged compile OK"

echo "-- 1. DRIFT GUARD: 5 existing == BASE (prod==legfix; ABORT on drift) --"
for f in $FILES; do
  pm=$(m5 "$f"); exp=$(base_md5 "$f")
  [ "$pm" = "$exp" ] || { echo "ABORT drift: $f is $pm, expected legfix base $exp (prod moved since prep — STOP+surface)"; exit 1; }
done
echo "   OK (5 existing at the deployed legfix state)"

echo "-- 1b. COUPLING guard: bitunix.py + models.py ship TOGETHER --"
for must in trading_corp/brokers/bitunix.py trading_corp/persistence/models.py; do
  case " $FILES " in *" $must "*) ;; *) echo "ABORT coupling: $must not in deploy set"; exit 1;; esac
  [ -f "$STAGE/$must" ] && [ "$(m5 "$STAGE/$must")" = "$(tgt_md5 "$must")" ] || {
    echo "ABORT coupling: staged $must missing/mismatched — REFUSE to apply bitunix.py without the tip models.py (FillEvent.role)"; exit 1; }
done
echo "   OK (bitunix.py + models.py both staged at target — coupled)"

echo "-- 2. INSTALL: backup -> *$BAK, md5-gated atomic mv --"
for f in $FILES; do
  want=$(tgt_md5 "$f")
  cp -p "$f" "$f$BAK"
  cp -p "$STAGE/$f" "$f.p2-new"
  mv "$f.p2-new" "$f"
  pm=$(m5 "$f"); [ "$pm" = "$want" ] || { echo "ABORT post-mv: $f is $pm != $want"; exit 1; }
  echo "   installed $f ($pm)  [backup $f$BAK]"
done

echo "-- 3. py_compile all 5 installed (final confirm) --"
"$PY" -m py_compile $FILES && echo "   compile OK"

echo "-- 4. final md5 (== target) --"
for f in $FILES; do echo "   $(m5 "$f")  $f"; done

cat <<'EOF'
===== APPLIED (NO RESTART, NO CONFIG TOUCHED). =====
NEXT (operator, remote-mobile — az run-command); prefer FLAT:
  az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
    --command-id RunShellScript --scripts "systemctl restart trading-corp"
THEN run VERIFY.md A: engine up / new PID != 3046486 / 5 md5s==target / models.py
  d7561d3c / main.py f16e9c24 + db.py a2c2ff46 UNCHANGED / strategies.yaml STILL
  569c38f8 (untouched) / paper=False / --live-divisions has bitunix_futures /
  staleness gate / execution_mode:live / DD-cap 0.99 / B2 OFF / reconciler clean /
  NO FillEvent/role ImportError or TypeError on startup (the coupling check).
VERIFY.md B needs live trades (maker/taker FillEvent(role=...) no TypeError +
  entry_role/exit_role/maker_taker_mix recorded; a winning close books result=win
  via NET, exit_kind via order-id match not 'stop').
ROLLBACK (operator; restores the legfix state):
  cd /home/azureuser/trading_corp
  for f in trading_corp/brokers/bitunix.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py trading_corp/agents/divisions/bitunix_bracket.py trading_corp/persistence/models.py; do mv "$f.bak-pre-p2-combined-2026-06-19" "$f"; done
  then az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "systemctl restart trading-corp"
EOF
