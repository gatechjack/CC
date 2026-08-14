#!/usr/bin/env bash
# ROLLBACK — PMCC price-fix deploy (2026-08-04). Restores the 4 runtime files from
# their .bak_pmcc_pricefix_20260804 backups (prod-live 5f56ccc code) and restarts the
# engine. Run as root via Azure RunShellScript. Self-gates on backup presence.
set +e
APPROOT=/home/azureuser/trading_corp; cd "$APPROOT" || { echo "APPROOT_FAIL"; exit 3; }
BAK=".bak_pmcc_pricefix_20260804"
FILES="config/strategies.yaml trading_corp/agents/divisions/pmcc_robinhood.py trading_corp/web/pmcc_pricing.py trading_corp/web/routes.py"
# baseline (5f56ccc) md5 the restore should land on:
declare -A B=( [config/strategies.yaml]=274b7e348eb2 [trading_corp/agents/divisions/pmcc_robinhood.py]=6b928badbcf0 [trading_corp/web/pmcc_pricing.py]=7ee14a4367b6 [trading_corp/web/routes.py]=96becb83b19a )
for f in $FILES; do [ -f "$f$BAK" ] || { echo "MISSING_BACKUP $f$BAK — ABORT"; exit 4; }; done
for f in $FILES; do cp -p "$f$BAK" "$f"; g=$(tr -d '\r' < "$f" | md5sum | cut -c1-12); echo "restored $f md5=$g want=${B[$f]} $([ "$g" = "${B[$f]}" ] && echo OK || echo MISMATCH)"; done
systemctl restart trading-corp; sleep 10
echo "ROLLED_BACK pid=$(systemctl show -p MainPID --value trading-corp) active=$(systemctl is-active trading-corp) nrestarts=$(systemctl show -p NRestarts --value trading-corp)"
