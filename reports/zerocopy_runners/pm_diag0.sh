PY=/home/azureuser/trading_corp/venv/bin/python
ROOT=/home/azureuser/trading_corp
PMDB=$ROOT/data/prediction_markets.db
LGDB=$ROOT/data/trading_corp.db
echo "=== WHOAMI/HOST ==="; whoami; hostname; date -u +%Y-%m-%dT%H:%M:%SZ
echo "=== DF root ==="; df -h / | tail -2
echo "=== DB files ==="; ls -l "$PMDB" "$LGDB" 2>&1
echo "=== venv python ==="; "$PY" -c "import sys,sqlite3;print('py',sys.version.split()[0]);print('sqlite',sqlite3.sqlite_version)" 2>&1
echo "=== git HEAD ($ROOT) ==="; git -C "$ROOT" rev-parse HEAD 2>&1; git -C "$ROOT" log --oneline -1 2>&1
echo "=== systemd trading-corp ==="
systemctl show trading-corp -p MainPID -p NRestarts -p ActiveState -p SubState -p ActiveEnterTimestamp -p WorkingDirectory -p FragmentPath 2>&1
echo "=== systemd prediction-markets-web ==="
systemctl show prediction-markets-web -p MainPID -p NRestarts -p ActiveState -p SubState -p ActiveEnterTimestamp -p WorkingDirectory 2>&1
echo "=== engine file mtimes + csha16 ==="
for f in trading_corp/prediction_markets/execution.py trading_corp/prediction_markets/live_driver.py trading_corp/prediction_markets/player_props_match.py trading_corp/prediction_markets/settlement.py trading_corp/prediction_markets/arm.py; do
  p="$ROOT/$f"
  if [ -f "$p" ]; then
    m=$(stat -c '%y' "$p")
    h=$(tr -d '\r' < "$p" | sha256sum | cut -c1-16)
    echo "$f | mtime=$m | csha16=$h"
  else
    echo "$f | MISSING"
  fi
done
echo "=== PM DB tables + row counts ==="
"$PY" - <<'PY'
import sqlite3
c=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
ts=[r[0] for r in c.execute("select name from sqlite_master where type='table' order by name")]
print("n_tables",len(ts))
for t in ts:
    try:
        n=c.execute("select count(*) from %s"%t).fetchone()[0]
    except Exception as e:
        n="ERR:%s"%e
    print("%-34s %s"%(t,n))
PY
echo "=== legacy DB: agent_state presence (RO) ==="
"$PY" - <<'PY'
import sqlite3
c=sqlite3.connect('file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro',uri=True)
try:
    n=c.execute("select count(*) from agent_state where agent='pm_live'").fetchone()[0]
    print("agent_state pm_live rows",n)
    r=c.execute("select key,substr(value_json,1,120) from agent_state where agent='pm_live' and key='arm:global'").fetchall()
    print("arm:global ->",r)
except Exception as e:
    print("ERR",e)
PY
echo "=== DONE diag0 ==="
