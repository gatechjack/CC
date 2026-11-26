#!/usr/bin/env bash
# secrev_batch6_token_cleanup.sh -- OPTIONAL. JACK RUNS (az vm run-command, root). Jack decides whether to run.
# Deletes ONLY the waagent run-command files that contain the string "pro attach" (the Pro-attach run of 2026-11-26).
# Uses grep -l == FILENAMES ONLY; file CONTENTS (incl. the token) are NEVER printed or logged. Nothing else is touched.
# CAVEAT: this removes the ON-BOX copy only. The token may also persist in the Azure control-plane run-command
#   resource / activity log -- rotating the (free personal) Pro token or deleting that az run-command resource is a
#   separate, Jack-side step. Deleting completed waagent run-command artifacts is low-risk (historical state).
set -u
exec > >(tee /home/azureuser/secrev_batch6_tokclean_lastrun.log) 2>&1
echo "=== TOKEN CLEANUP $(date -u +%Y%m%dT%H%M%SZ) ==="
DIR=/var/lib/waagent/run-command
if [ ! -d "$DIR" ]; then echo "no $DIR -- nothing to do"; exit 0; fi
# -r recurse, -l filenames-only (contents never emitted), -I skip binary, -Z NUL-safe
MATCHES=$(grep -rlIZ -e 'pro attach' "$DIR" 2>/dev/null | tr '\0' '\n' | grep -E '.')
if [ -z "$MATCHES" ]; then echo "no run-command files contain 'pro attach' -- nothing to delete"; exit 0; fi
echo "files to delete (FILENAMES ONLY -- contents deliberately not shown):"
printf '%s\n' "$MATCHES" | sed 's/^/  /'
CNT=0
printf '%s\n' "$MATCHES" | while IFS= read -r f; do rm -f -- "$f" && echo "  removed: $f"; done
# re-check nothing with the string remains
LEFT=$(grep -rlIZ -e 'pro attach' "$DIR" 2>/dev/null | tr '\0' '\n' | grep -cE '.')
echo "[verify] files still containing 'pro attach' after cleanup: $LEFT (expect 0)"
echo "=== DONE ==="
