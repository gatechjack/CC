PY=/home/azureuser/trading_corp/venv/bin/python
echo "=== systemd Environment (look for KEY_VAULT_URI, KALSHI_USE_DEMO) ==="
systemctl show trading-corp -p Environment -p EnvironmentFiles 2>&1 | tr ' ' '\n' | grep -iE 'KEY_VAULT|KALSHI_USE_DEMO|VAULT' || echo "(no KEY_VAULT/KALSHI_USE_DEMO in Environment=)"
echo "--- raw (first 400 chars of Environment line) ---"
systemctl show trading-corp -p Environment 2>&1 | cut -c1-400
echo "=== pm_account rows (account_id, secret_ref, active) ==="
"$PY" - <<'PY'
import sqlite3
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
cols=[r[1] for r in pm.execute("pragma table_info(pm_account)")]
print("pm_account cols:",cols)
for r in pm.execute("select * from pm_account"):
    print("  ",dict(zip(cols,r)))
PY
echo "=== UFC ticker cost basis + signed net (journal, RO) ==="
"$PY" - <<'PY'
import sqlite3
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
print("-- all rows for KXUFCFIGHT-26SEP29BULVIS-VIS --")
for r in pm.execute("select account_id,category,wallet,outcome_leg,is_exit,outcome_status,fill_count,fill_price,fee,submitted_price,condition_id,outcome_index from pm_subdivision_order where ticker like 'KXUFCFIGHT-26SEP29BULVIS%' order by account_id,is_exit"):
    print("  acct=%s cat=%s leg=%s exit=%s st=%s cnt=%s fillpx=%s fee=%s subpx=%s cid=%s oidx=%s"%(r[0],r[1],r[3],r[4],r[5],r[6],r[7],r[8],r[9],(r[10] or '')[:14],r[11]))
print("-- signed-net + cost basis per account (entry YES filled) --")
for r in pm.execute("""select account_id,
  sum((case outcome_leg when 'yes' then 1 when 'no' then -1 else 0 end)*(case when is_exit=0 then 1 else -1 end)*coalesce(fill_count,0)) signed_net,
  sum(case when is_exit=0 and outcome_status='filled' then fill_count*fill_price else 0 end) cost_basis,
  sum(case when is_exit=0 and outcome_status='filled' then fill_count else 0 end) entered,
  sum(case when is_exit=0 and outcome_status='filled' then coalesce(fee,0) else 0 end) fees
  from pm_subdivision_order where ticker like 'KXUFCFIGHT-26SEP29BULVIS%' and dry_run=0 and outcome_status='filled' group by account_id"""):
    print("  %-14s signed_net=%s cost_basis=$%.4f entered=%s fees=$%.4f"%(r[0],r[1],r[2] or 0.0,r[3],r[4] or 0.0))
PY
echo "=== DONE p1a ==="
