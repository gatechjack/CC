#!/usr/bin/env bash
# Deploy: bitunix bracket-exit redesign (#3 fill-reg lock-resilience + #5-B/C
# exit-guard exemptions + exchange-resting bracket) + E2.5 execution_mode
# write-side activation. 7 files. Rebased tip b077b66; prep committed bb37a8c.
#
# DUAL-MODE drift guard:
#   - E2.5 trio (data_exec.py, agents/logger.py, persistence/models.py): prod is
#     BEHIND base (pre-E2.5). Gated to prod-CURRENT md5 (operator-approved
#     deviation from prod==base). Activating these starts the execution_mode
#     writes. COUPLED: shipping logger without models => the new INSERT binds
#     :execution_mode against an old to_db_row() => prod-wide proposed_order
#     write outage. All three MUST ship together.
#   - Bitunix at-base (observer, reconciler, brokers/bitunix.py): prod==base.
#   - NEW (bitunix_bracket.py): create (no base, must be absent on prod).
#
# PREPARE/APPLY-ONLY. Does prod writes (backup + atomic mv + create). Does NOT
# restart. Reload is the operator's step:
#     ssh -t azureuser@trading.jacksumner.com sudo systemctl restart trading-corp
# Delivery (before running): place the staged tree at $STAGE (mirrors
# trading_corp/...). Stream-run: Get-Content this.sh -Raw | ssh ... "tr -d '\r'|bash"
set -euo pipefail

BASE=/home/azureuser/trading_corp
STAGE="$BASE/_bracket_e25_stage"
BAK=".bak-pre-bracket-2026-06-17"
PY="$BASE/venv/bin/python"
cd "$BASE"

EXISTING="trading_corp/agents/data_exec.py trading_corp/agents/logger.py trading_corp/persistence/models.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py trading_corp/brokers/bitunix.py"
NEWFILE="trading_corp/agents/divisions/bitunix_bracket.py"
ALL="$EXISTING $NEWFILE"

# CURRENT prod md5 (drift guard). E2.5 trio => prod-current (deviation); at-base => base.
cur_md5() { case "$1" in
  trading_corp/agents/data_exec.py)                              echo e3e4cca7a701a6add22ab43514906c6f;;  # E2.5 trio (current 05-30 blob)
  trading_corp/agents/logger.py)                                 echo 2938e089da5199b133854444893bdd02;;  # E2.5 trio (current 05-28 blob)
  trading_corp/persistence/models.py)                            echo 96cf31c42ddff7e7d517ac42e9e6aad9;;  # E2.5 trio (current 06-01 blob)
  trading_corp/agents/divisions/bitunix_futures_observer.py)     echo eec6bda62e23038edd09f29ff65addcb;;  # at-base
  trading_corp/agents/divisions/bitunix_position_reconciler.py)  echo bf048cd14f11cd2b1c5a91bd6b4c0f1d;;  # at-base
  trading_corp/brokers/bitunix.py)                               echo 70f7904f676e9dd76b1f8ef384226e66;;  # at-base
esac; }
# TARGET md5 (deploy target = b077b66 LF blobs)
tgt_md5() { case "$1" in
  trading_corp/agents/data_exec.py)                              echo 51281fbdd44096b224c0f9062ac4a3e7;;
  trading_corp/agents/logger.py)                                 echo e625c388b0bb62b93d365a1e470836ee;;
  trading_corp/persistence/models.py)                            echo a781b495b6ff4a859a0a4e11d04cd5f5;;
  trading_corp/agents/divisions/bitunix_futures_observer.py)     echo 13469b104894dfea0e727fe9a495c13d;;
  trading_corp/agents/divisions/bitunix_position_reconciler.py)  echo 386cc6c243347dce65c60f55c3480ae6;;
  trading_corp/brokers/bitunix.py)                               echo 7a3da849cadfe32940649c9aba514ef3;;
  trading_corp/agents/divisions/bitunix_bracket.py)              echo bd639224ef193736e05967d78dce0a5b;;
esac; }
m5() { md5sum "$1" | cut -d' ' -f1; }

echo "===== bracket + E2.5 deploy (7 files, NO RESTART) ====="

echo "-- 0. staged tree present + matches TARGET (touch nothing on fail) --"
[ -d "$STAGE" ] || { echo "ABORT: $STAGE missing (deliver staged tree first)"; exit 1; }
for f in $ALL; do
  [ -f "$STAGE/$f" ] || { echo "ABORT: staged $f missing"; exit 1; }
  sm=$(m5 "$STAGE/$f"); want=$(tgt_md5 "$f")
  [ "$sm" = "$want" ] || { echo "ABORT: staged $f md5 $sm != target $want"; exit 1; }
done
echo "   OK (7 staged == target)"

echo "-- 0b. PREFLIGHT py_compile staged (abort before touching prod) --"
STAGED_PATHS=""; for f in $ALL; do STAGED_PATHS="$STAGED_PATHS $STAGE/$f"; done
"$PY" -m py_compile $STAGED_PATHS && echo "   staged compile OK"

echo "-- 1. coupling guard: E2.5 trio must all be present (write-outage guard) --"
for f in trading_corp/agents/data_exec.py trading_corp/agents/logger.py trading_corp/persistence/models.py; do
  case " $ALL " in *" $f "*) : ;; *) echo "ABORT: E2.5 trio member $f missing from set"; exit 1;; esac
done
# the audit logger (log_proposed_order) is agents/logger.py, NOT path_logger/logger.py
grep -q "def log_proposed_order" "$STAGE/trading_corp/agents/logger.py" || { echo "ABORT: staged agents/logger.py lacks log_proposed_order (wrong module?)"; exit 1; }
echo "   OK (data_exec + agents/logger + models all staged; logger module verified)"

echo "-- 2. DRIFT GUARD: 6 existing == expected-current; new file ABSENT --"
for f in $EXISTING; do
  pm=$(m5 "$f"); exp=$(cur_md5 "$f")
  [ "$pm" = "$exp" ] || { echo "ABORT drift: $f is $pm, expected current $exp (prod moved since prep — STOP+surface)"; exit 1; }
done
[ ! -e "$NEWFILE" ] || { echo "ABORT: $NEWFILE already exists on prod (expected absent for create)"; exit 1; }
echo "   OK (6 existing at expected current; new file absent)"

echo "-- 3. INSTALL: backup existing -> *$BAK, md5-gated atomic mv; create new --"
for f in $EXISTING; do
  want=$(tgt_md5 "$f")
  cp -p "$f" "$f$BAK"
  cp -p "$STAGE/$f" "$f.bracket-new"
  mv "$f.bracket-new" "$f"
  pm=$(m5 "$f"); [ "$pm" = "$want" ] || { echo "ABORT post-mv: $f is $pm != $want"; exit 1; }
  echo "   installed $f ($pm)  [backup $f$BAK]"
done
mkdir -p "$(dirname "$NEWFILE")"
cp -p "$STAGE/$NEWFILE" "$NEWFILE.bracket-new"
mv "$NEWFILE.bracket-new" "$NEWFILE"
pm=$(m5 "$NEWFILE"); want=$(tgt_md5 "$NEWFILE")
[ "$pm" = "$want" ] || { echo "ABORT post-create: $NEWFILE is $pm != $want"; exit 1; }
echo "   created $NEWFILE ($pm)  [NEW — rollback deletes it]"

echo "-- 4. py_compile all 7 installed (final confirm) --"
"$PY" -m py_compile $ALL && echo "   compile OK"

echo "-- 5. final md5 (== target) --"
for f in $ALL; do echo "   $(m5 "$f")  $f"; done

cat <<'EOF'
===== APPLIED (NO RESTART). =====
NEXT (operator): ssh -t azureuser@trading.jacksumner.com sudo systemctl restart trading-corp
THEN run VERIFY.md (engine up / 7 md5s / gate-fires / bracket-wired / E2.5-writes-populating / config preserved / no main.py-db.py touch / reconciler clean).
ROLLBACK (operator; reverts E2.5 too -> column back to default 'paper', tolerated; also the only bracket-OFF):
  cd /home/azureuser/trading_corp
  for f in trading_corp/agents/data_exec.py trading_corp/agents/logger.py trading_corp/persistence/models.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py trading_corp/brokers/bitunix.py; do mv "$f.bak-pre-bracket-2026-06-17" "$f"; done
  rm -f trading_corp/agents/divisions/bitunix_bracket.py
  then: sudo systemctl restart trading-corp
EOF
