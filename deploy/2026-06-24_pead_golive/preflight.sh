#!/usr/bin/env bash
# READ-ONLY pre-flight: Bitunix flat (AUTHORITATIVE = reconciler match_count) + RH pickle.
TC=/home/azureuser/trading_corp
echo "== Bitunix flat? (authoritative: latest position_state_reconciled match_count; 0 = flat) =="
$TC/venv/bin/python -c "import sqlite3,json;c=sqlite3.connect('$TC/data/trading_corp.db');r=c.execute(\"SELECT ts,payload_json FROM audit_event WHERE kind='position_state_reconciled' ORDER BY ts DESC LIMIT 1\").fetchone();p=json.loads(r[1]) if r else {};print('  latest reconcile:',r[0] if r else 'n/a','| OPEN(match_count)=',p.get('match_count'),'| orphan=',p.get('orphan_on_broker_count'))"
echo "  ^ OPEN(match_count) MUST be 0 to restart. (position table is NOT a valid bitunix flat signal.)"
echo "  ^ ALSO eyeball the bitunix dashboard shows 0 open before restarting."
echo "== bitunix strategy state (expect halted:false = armed) =="
$TC/venv/bin/python -c "import sqlite3;c=sqlite3.connect('$TC/data/trading_corp.db');r=c.execute(\"SELECT value_json FROM agent_state WHERE agent='strategy_state' AND key='bitunix_futures'\").fetchone();print('  ',r[0] if r else 'n/a')"
echo "== RH pickle (engine reuses this at boot via connect/store_session) =="
ls -la /home/azureuser/.tokens/robinhood.pickle 2>/dev/null
echo "  ^ if old/stale -> run golive_2_pickle.ps1 tonight (device approval) so boot reuses a fresh session."
