#!/bin/bash
# === Phase 2c go-live: bitunix_futures -> LIVE on the NEW funded account ===
# RUN AS ROOT via Azure portal -> VM -> Run command -> RunShellScript.
# (The --live-divisions edit is in the root-owned systemd unit; operator ssh
#  has no sudo file-edit. This script is root, so it does all of 2c.)
# Flat-guarded, backs up, edits, verifies, daemon-reload + restart; auto-restore
# on any verify failure. Prereq: 2a done (SFP on BITUNIX-SFP-*) + 2b done
# (BITUNIX-FUTURES-* set to the new funded account in Key Vault, IP-bound).
PKG=/home/azureuser/trading_corp
STRAT=$PKG/config/strategies.yaml
UNIT=/etc/systemd/system/trading-corp.service
DB=$PKG/data/trading_corp.db
TAG=bak-pre-2c-2026-06-30

# --- preconditions ---
grep -q "^  mode: halted" "$STRAT"        || { echo "PRECOND FAIL: futures 'mode: halted' not in strategies.yaml (already flipped?) - ABORT"; exit 2; }
grep -q "^  execution_mode: paper" "$STRAT" || { echo "PRECOND FAIL: futures 'execution_mode: paper' not found - ABORT"; exit 2; }
grep -q -- "--live-divisions bitunix_sfp robinhood_pead" "$UNIT" || { echo "PRECOND FAIL: unit --live-divisions not as expected - ABORT (inspect $UNIT)"; exit 2; }
grep -q -- "--live-divisions[^#]*bitunix_futures" "$UNIT" && { echo "PRECOND: unit already lists bitunix_futures - ABORT (already done?)"; exit 2; }

# --- flat-guard: no open live rows, ANY bitunix division ---
open=$(sqlite3 "$DB" "SELECT COUNT(*) FROM paper_trade_record WHERE result IS NULL AND (extra_json LIKE '%\"execution_mode\": \"live\"%' OR extra_json LIKE '%\"execution_mode\":\"live\"%')" 2>&1)
[ "$open" = "0" ] || { echo "NOT FLAT (open live rows: [$open]) - ABORT, no changes"; exit 3; }
echo "flat-guard OK (0 open live rows)"

# --- backups ---
cp "$STRAT" "$STRAT.$TAG"; cp "$UNIT" "$UNIT.$TAG"; echo "backed up strategies.yaml + unit (.$TAG)"
restore_all(){ cp "$STRAT.$TAG" "$STRAT"; cp "$UNIT.$TAG" "$UNIT"; systemctl daemon-reload; echo RESTORED; }

# --- strategies.yaml: un-halt + live (bitunix_futures block only; patterns are unique) ---
sed -i 's@^  execution_mode: paper.*@  execution_mode: live   # Phase 2c 2026-06-30: futures live on new funded account@' "$STRAT"
sed -i 's@^  mode: halted.*@  mode: trading   # Phase 2c 2026-06-30: un-halted (two-state)@' "$STRAT"
if grep -q "^  mode: halted" "$STRAT" || grep -q "^  execution_mode: paper" "$STRAT" || ! grep -q "^  mode: trading" "$STRAT" || ! grep -q "^  execution_mode: live" "$STRAT"; then
  echo "STRAT VERIFY FAIL - restoring"; restore_all; exit 4; fi
echo "strategies.yaml: bitunix_futures mode=trading execution_mode=live"

# --- unit: append bitunix_futures to --live-divisions ---
sed -i 's@--live-divisions bitunix_sfp robinhood_pead@--live-divisions bitunix_sfp robinhood_pead bitunix_futures@' "$UNIT"
grep -q -- "--live-divisions bitunix_sfp robinhood_pead bitunix_futures" "$UNIT" || { echo "UNIT VERIFY FAIL - restoring"; restore_all; exit 5; }
echo "unit: --live-divisions now includes bitunix_futures"

# --- reload + flat-guarded restart ---
systemctl daemon-reload
systemctl restart trading-corp
sleep 5
echo "RESTART issued - is-active: $(systemctl is-active trading-corp)"
systemctl show -p MainPID,NRestarts trading-corp
echo "=== 2c APPLY COMPLETE - tell Claude to run the read-only 2c-verify ==="
