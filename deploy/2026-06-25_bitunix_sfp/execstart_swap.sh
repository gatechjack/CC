#!/usr/bin/env bash
# RESTART ② (part 2 of 2) — ROOT step. ExecStart --live-divisions swap:
# remove bitunix_futures, add bitunix_sfp, keep robinhood_pead. Backs up the unit, gates on the exact
# expected token (aborts if not found exactly once), daemon-reloads. Does NOT restart.
# Run as ROOT via: az vm run-command invoke -g <RG> -n tc-prod-vm --command-id RunShellScript --scripts @execstart_swap.sh
set -euo pipefail
U=/etc/systemd/system/trading-corp.service
OLD='--live-divisions bitunix_futures robinhood_pead'
NEW='--live-divisions bitunix_sfp robinhood_pead'

echo "== current ExecStart --live-divisions =="
grep -- '--live-divisions' "$U" || { echo "ABORT: no --live-divisions in $U"; exit 1; }

n=$(grep -c -- "$OLD" "$U" || true)
[ "$n" = "1" ] || { echo "ABORT: expected exactly 1 occurrence of [$OLD], found $n (STOP — inspect the unit)"; exit 1; }

cp -p "$U" "$U.bak-pre-sfp-2026-06-25"
sed -i "s|$OLD|$NEW|" "$U"

echo "== new ExecStart --live-divisions =="
grep -- '--live-divisions' "$U"
grep -q -- "$NEW" "$U" || { echo "ABORT: swap did not take"; exit 1; }

systemctl daemon-reload
echo "== ExecStart swapped (bitunix_futures -> bitunix_sfp; robinhood_pead kept) + daemon-reload OK. =="
echo "   NO restart performed. Re-verify FLAT, then: systemctl restart trading-corp"
