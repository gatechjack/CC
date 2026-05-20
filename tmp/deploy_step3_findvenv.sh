#!/bin/bash
# Find the venv that the trading-corp service uses.
echo "=== systemd unit ExecStart ==="
systemctl cat trading-corp 2>&1 | grep -E "ExecStart|Environment" | head -10
echo ""
echo "=== venvs in /home/azureuser ==="
ls -d /home/azureuser/*venv* 2>/dev/null
ls -d /home/azureuser/trading_corp/*venv* 2>/dev/null
ls -d /home/azureuser/trading_corp/.*venv* 2>/dev/null
echo ""
echo "=== process command line ==="
ps -o cmd= -p $(cat /home/azureuser/trading_corp/data/trading_corp.pid 2>/dev/null || echo 1) 2>&1 | head -3
echo ""
echo "=== /usr/local/bin python paths ==="
which python python3 python3.10 python3.11 python3.12 2>/dev/null
echo ""
echo "=== check if httpx in any of these ==="
for p in /home/azureuser/trading_corp/.venv/bin/python /home/azureuser/.venv/bin/python /home/azureuser/trading_corp/venv/bin/python; do
  if [ -x "$p" ]; then
    echo "$p exists; httpx? $($p -c 'import httpx; print(httpx.__version__)' 2>&1)"
  fi
done
