#!/usr/bin/env bash
# =============================================================================
# STANDARD PROD DEPLOY SCRIPT TEMPLATE   (trading-corp / tc-prod-vm)
# -----------------------------------------------------------------------------
# Canonical structure for a gated, backed-up, verifying prod deploy. COPY this
# file per deploy (e.g. deploy_<name>_<YYYYMMDD>.sh), fill the CONFIG block, and
# stage the file(s) to $ST first. Runs AS ROOT via Azure Run Command
# (RunShellScript) - NO sudo:
#
#   az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm \
#     --command-id RunShellScript \
#     --scripts "bash /home/azureuser/deploy_<name>_<YYYYMMDD>.sh" \
#     --query "value[0].message" --output tsv
#
# RETROFIT FORWARD ONLY: use this for FUTURE deploy scripts. Do NOT edit deploy
# scripts already run on prod, and this template touches nothing on running prod.
# =============================================================================
set -euo pipefail

# ---- CONFIG (edit per deploy) ----------------------------------------------
APP=/home/azureuser/trading_corp
LOCK=/home/azureuser/deploy.lock              # cross-session deploy mutex. ABSOLUTE path on purpose:
                                              # "~" is ambiguous (az RunShellScript root -> /root;
                                              # operator ssh azureuser -> /home/azureuser). This path
                                              # is the shared rendezvous for BOTH.
DEPLOY_SESSION="<session-id-or-branch>"       # who is deploying, e.g. claude-movegate-2026-08-06
DEPLOY_COMMIT="<short-sha>"                    # the commit being deployed
ST="/home/azureuser/NAME_stage"               # staged-file dir (LF-normalized files pre-copied here)
BK="/home/azureuser/NAME_bak_YYYYMMDD"        # backup dir for the pre-deploy runtime files
# FILES: one "relpath:expected_lf_md5" per runtime file to swap
FILES=(
  "trading_corp/agents/strategies/<file>.py:<lf_md5>"
)

# ---- 0. DEPLOY MUTEX  (BEFORE the pending_order gate) -----------------------
# Prevents two sessions deploying concurrently and racing the engine restart
# (root cause of the 2026-08-06 20:52 incident: a parallel PMCC deploy restarted
# the engine while a movegate deploy was mid-verify). Semantics:
#   - lock present AND < 30 min old  -> ABORT, report the holder (do NOT touch it)
#   - lock absent OR >= 30 min stale -> ACQUIRE (write {session,timestamp,commit})
# Released as the LAST step below (step 7) and in the rollback script. A deploy
# that aborts AFTER acquiring (steps 1-6) intentionally leaves the lock until it
# goes stale (>=30 min) or the next deploy steals it -- that is the recovery path.
if [ -f "$LOCK" ] && [ -n "$(find "$LOCK" -mmin -30 2>/dev/null)" ]; then
  echo "ABORT: deploy lock held (< 30 min old) by:"; cat "$LOCK"; exit 3
fi
printf '{"session":"%s","timestamp":"%s","commit":"%s"}\n' \
  "$DEPLOY_SESSION" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$DEPLOY_COMMIT" > "$LOCK"
echo "== 0. deploy lock ACQUIRED: $(cat "$LOCK")"

# ---- 1. gate: pending_order must be 0 --------------------------------------
P=$(sqlite3 "$APP/data/trading_corp.db" "SELECT COUNT(*) FROM pending_order;")
[ "$P" = "0" ] || { echo "ABORT: pending_order=$P"; exit 1; }
echo "   pending_order=0 OK"

# ---- 2. verify staged LF-md5 -----------------------------------------------
for e in "${FILES[@]}"; do
  f="${e%%:*}"; want="${e##*:}"; b=$(basename "$f")
  [ -f "$ST/$b" ] || { echo "ABORT: missing staged $ST/$b"; exit 1; }
  got=$(md5sum "$ST/$b" | cut -d" " -f1)
  [ "$got" = "$want" ] || { echo "ABORT: md5 mismatch $f got=$got want=$want"; exit 1; }
  echo "   $b OK $got"
done

# ---- 3. backup current runtime files ---------------------------------------
mkdir -p "$BK"
for e in "${FILES[@]}"; do f="${e%%:*}"; cp -p "$APP/$f" "$BK/$(basename "$f").bak"; done
echo "   backups -> $BK"

# ---- 4. swap, preserving owner/group/mode ----------------------------------
for e in "${FILES[@]}"; do
  f="${e%%:*}"; b=$(basename "$f")
  own=$(stat -c "%U" "$APP/$f"); grp=$(stat -c "%G" "$APP/$f"); mod=$(stat -c "%a" "$APP/$f")
  install -o "$own" -g "$grp" -m "$mod" "$ST/$b" "$APP/$f"
  echo "   $f <- $b (owner=$own:$grp mode=$mod)"
done

# ---- 5. py_compile with prod venv ------------------------------------------
targets=""; for e in "${FILES[@]}"; do targets="$targets $APP/${e%%:*}"; done
"$APP/venv/bin/python" -m py_compile $targets
echo "   py_compile OK"

# ---- 6. restart engine -----------------------------------------------------
OLD=$(systemctl show trading-corp -p MainPID --value)
systemctl restart trading-corp
sleep 4
NEW=$(systemctl show trading-corp -p MainPID --value)
STt=$(systemctl is-active trading-corp || true)
echo "   old MainPID=$OLD  new MainPID=$NEW  state=$STt"
[ "$STt" = "active" ] || { echo "WARNING: not active - run the rollback script"; exit 2; }

# ---- 7. release deploy mutex  (LAST step) ----------------------------------
rm -f "$LOCK"
echo "== DONE. deploy lock RELEASED. Rollback: bash /home/azureuser/rollback_<name>_<YYYYMMDD>.sh =="

# =============================================================================
# ROLLBACK TEMPLATE (separate file: rollback_<name>_<YYYYMMDD>.sh)
# The rollback does NOT acquire the lock (it must be able to recover from a
# deploy that still holds it); it RELEASES the lock as its last step.
# -----------------------------------------------------------------------------
# set -euo pipefail
# APP=/home/azureuser/trading_corp
# LOCK=/home/azureuser/deploy.lock
# BK=/home/azureuser/<name>_bak_<YYYYMMDD>
# for e in "${FILES[@]}"; do
#   f="${e%%:*}"; b="$BK/$(basename "$f").bak"
#   [ -f "$b" ] || { echo "ABORT: missing backup $b"; exit 1; }
#   own=$(stat -c "%U" "$APP/$f"); grp=$(stat -c "%G" "$APP/$f"); mod=$(stat -c "%a" "$APP/$f")
#   install -o "$own" -g "$grp" -m "$mod" "$b" "$APP/$f"; echo "restored $f"
# done
# systemctl restart trading-corp; sleep 4
# rm -f "$LOCK"    # release any lock left by the failed deploy (LAST step)
# echo "rolled back; MainPID=$(systemctl show trading-corp -p MainPID --value) lock cleared"
# =============================================================================
