echo "START pead_part3_rollback (restore pre-deploy files; engine was NEVER restarted)"; date -u +%FT%TZ
ROOT="$HOME/trading_corp"
RHB="$ROOT/trading_corp/brokers/robinhood.py"
PS="$ROOT/trading_corp/agents/strategies/pead_strategy.py"
for f in "$RHB" "$PS"; do
  b="$f.bak_pre_part3_20260826"
  if [ -f "$b" ]; then cp -p "$b" "$f"; echo "restored $f from $b"; else echo "NO BACKUP for $f -- MANUAL CHECK"; fi
done
echo "post-rollback md5 (expect robinhood=230e7807720da3cb71af74c77daf396a pead_strategy=9b9cfdadf8a86c2d5c0db6709127c155):"
md5sum "$RHB" "$PS"
echo "engine MainPID (unchanged -- never restarted):"; systemctl show -p MainPID --value trading-corp 2>/dev/null
echo "DONE pead_part3_rollback"
