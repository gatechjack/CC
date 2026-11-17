# pm_rearm.sh -- AUTHORED, DO NOT RUN until BOTH preconditions below are confirmed.
# Clears the auth_failure latch and ARMS, through the SANCTIONED pm_cli live-arm --clear-latch path ONLY,
# exactly the (account:category) pairs Jack lists in ARM_LIST. Writes the legacy agent_state arm rows (a
# RESERVED write -> Jack runs this himself). Reads arm state back for each listed pair. Touches NOTHING
# outside the list. Nothing else is armed, disarmed or written.
#
# USE: Jack (1) renews the Kalshi location attestation on the account(s), (2) confirms the Polymarket Data
# API v2 migration is deployed + engine restarted, (3) edits the two flags + ARM_LIST below, (4) runs:
#        powershell -ep bypass -f "C:\Users\AA Incorporado\cc\pm_rearm.ps1"
set -u
ROOT=/home/azureuser/trading_corp
PY='PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py'
BY=jack

# ---- PRECONDITION GATES (flip BOTH to 1 only AFTER verifying; re-arm before either is pointless) ----
ATTESTATION_RENEWED=0     # set 1 AFTER renewing the account's Kalshi attestation (kalshi.com Account&security->API Keys)
V2_MIGRATION_LIVE=0       # set 1 AFTER the Polymarket Data API v2 migration is deployed + engine restarted

# ---- JACK'S EXPLICIT LIST: one "account:category" per line, inside the quotes. Jack CHOOSES. ----
# Reconstructed pre-incident ARMED-and-TRADED sets (candidates for Jack to copy from -- NOT a recommendation;
# arm state is a board decision). Source: probe 6 fills == 2026-10-16 baseline armed counts (marc 11, trey 12).
#   marc (11): nfl itf cfb mlb cs2 mls ufc atp mex wnba lal
#   trey (12): nfl itf cfb mlb mls cs2 ufc atp wnba lal mex wta
# (The remaining categories on marc/trey were stale 2026-10-06 boot_reconcile latches, never traded -- leave
#  them unless Jack wants them.)
ARM_LIST="
"

# ======================= no edits needed below this line =======================
if [ "$ATTESTATION_RENEWED" != "1" ] || [ "$V2_MIGRATION_LIVE" != "1" ]; then
  echo "REFUSING: preconditions not met (ATTESTATION_RENEWED=$ATTESTATION_RENEWED V2_MIGRATION_LIVE=$V2_MIGRATION_LIVE)."
  echo "Re-arming before BOTH just re-latches on the first order (dead attestation) or arms a sub with no signal (dead feed)."
  exit 2
fi
LIST=$(printf '%s\n' "$ARM_LIST" | sed '/^[[:space:]]*$/d')
if [ -z "$LIST" ]; then echo "REFUSING: ARM_LIST is empty -- fill in Jack's chosen account:category pairs."; exit 3; fi
cd "$ROOT" || { echo "REFUSING: cannot cd $ROOT"; exit 4; }
echo "== RE-ARM (clear-latch) via sanctioned pm_cli -- only the listed pairs =="
printf '%s\n' "$LIST" | while IFS=: read -r ACCT CAT; do
  ACCT=$(printf '%s' "$ACCT" | tr -d '[:space:]'); CAT=$(printf '%s' "$CAT" | tr -d '[:space:]')
  [ -z "$ACCT" ] && continue
  echo "-- arm $ACCT:$CAT --"
  eval "$PY live-arm --account \"$ACCT\" --category \"$CAT\" --clear-latch --by \"$BY\""
done
echo ""; echo "== READ-BACK: arm state for the listed pairs (each should show armed:true latched:false) =="
printf '%s\n' "$LIST" | while IFS=: read -r ACCT CAT; do
  ACCT=$(printf '%s' "$ACCT" | tr -d '[:space:]'); CAT=$(printf '%s' "$CAT" | tr -d '[:space:]')
  [ -z "$ACCT" ] && continue
  eval "$PY live-status --account \"$ACCT\" --category \"$CAT\""
done
echo ""; echo "== DONE. Anything NOT in ARM_LIST is UNCHANGED. Re-read with pm_cli live-status if unsure. =="
