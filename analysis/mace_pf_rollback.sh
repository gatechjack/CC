set -e
# RESERVED box write -- restore the 3 fix files from the most-recent graft backup, verify box==BASE.
# Use if a graft succeeded but a later step (restart/boot-verify) must be undone. RESTART still required
# after to actually revert the running engine (if it had been restarted onto the fix).
ROOT=/home/azureuser/trading_corp
FILES="trading_corp/brokers/robinhood.py trading_corp/mace/rh_broker.py trading_corp/mace/execution.py"
base_of(){ case "$1" in
  trading_corp/brokers/robinhood.py) echo 2753939c;;
  trading_corp/mace/rh_broker.py)    echo c8aa5789;;
  trading_corp/mace/execution.py)    echo 71f1a828;; esac; }
BK=$(ls -1d /home/azureuser/mace_pf_graft_backup_* 2>/dev/null | sort | tail -1)
if [ -z "$BK" ]; then echo "NO BACKUP DIR FOUND -- cannot rollback from file; abort"; exit 2; fi
echo "restoring from $BK"
for f in $FILES; do cp "$BK/$f" "$ROOT/$f"; done
echo "=== verify box == BASE after restore ==="
for f in $FILES; do got=$(tr -d '\r' < "$ROOT/$f" | md5sum | cut -c1-8)
  if [ "$got" != "$(base_of "$f")" ]; then echo "RESTORE MISMATCH $f got=$got want=$(base_of "$f")"; exit 3; fi
  echo "base-restored $f $got"; done
echo "ROLLBACK FILES OK -- box==BASE. If the engine was restarted onto the fix, a RESTART is needed to revert the running code."
