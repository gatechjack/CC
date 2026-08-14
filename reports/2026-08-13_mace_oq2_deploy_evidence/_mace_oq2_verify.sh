#!/bin/bash
# MACE OQ-2 post-restart BOOT VERIFY - read-only EXCEPT the Board-sanctioned
# halt-latch cycle (ARM->HALT->ARM: agent_state latch writes only, no orders --
# market closed, entries eval only at the 15:45 slot).
set -u
R=/home/azureuser/trading_corp
PY=$R/venv/bin/python
echo "== unit state (want new MainPID, active, NRestarts=0) =="
systemctl show trading-corp -p MainPID -p ActiveState -p NRestarts
T=$(systemctl show trading-corp -p ActiveEnterTimestamp --value | cut -d" " -f2-3)
echo "boot: $T UTC"
J=/tmp/mace_oq2_boot.log
journalctl -u trading-corp --since "$T" --no-pager > "$J"
echo "== tracebacks since boot (want 0) =="
grep -c Traceback "$J"
echo "== config_hash lines (want NEW hash; pre-deploy was fe177fcd3882) =="
grep -i config_hash "$J" | tail -3
echo "== MACE scheduler-online lines (want all 4) =="
grep -o "MACE [a-z-]* scheduler online" "$J" | sort | uniq -c
echo "== GET /mace =="
curl -s -o /tmp/mace_page.html -w "GET /mace HTTP %{http_code}\n" http://127.0.0.1:8000/mace
for s in IBIT XLE GDX SPY; do echo "$s on page: $(grep -c $s /tmp/mace_page.html)"; done
grep -o "ENTRIES: [A-Z()a-z ]*" /tmp/mace_page.html | head -2
grep -io "config_hash[^<]*" /tmp/mace_page.html | head -2
echo "== halt latch cycle ARM->HALT->ARM (latch only, no orders) =="
curl -s -X POST http://127.0.0.1:8000/mace/halt > /tmp/mace_halt_resp.html
grep -q "HALTED (button)" /tmp/mace_halt_resp.html && echo "HALT: OK - HALTED (button) rendered" || echo "HALT: FAIL - inspect /tmp/mace_halt_resp.html"
curl -s -X POST http://127.0.0.1:8000/mace/arm > /tmp/mace_arm_resp.html
grep -q "ENTRIES: ARMED" /tmp/mace_arm_resp.html && echo "ARM: OK - ENTRIES: ARMED rendered" || echo "ARM: FAIL - inspect /tmp/mace_arm_resp.html"
echo "== DB: latch + ui audits + rung counts (want latch cleared, halt+arm audits, SPY open=2, GLD no open) =="
runuser -u azureuser -- "$PY" - <<'PYEOF'
import sqlite3
c = sqlite3.connect("/home/azureuser/trading_corp/data/trading_corp.db")
print("entry_halt latch:", list(c.execute(
    "SELECT value_json, updated_ts FROM agent_state"
    " WHERE agent='robinhood_mace' AND key='entry_halt'")))
print("ui audits (last 4):", list(c.execute(
    "SELECT kind, ts, actor FROM audit_event WHERE kind LIKE 'mace_ui_%'"
    " ORDER BY id DESC LIMIT 4")))
print("rung counts:", list(c.execute(
    "SELECT symbol, status, COUNT(*) FROM mace_rung GROUP BY symbol, status")))
PYEOF
echo "== division health =="
for d in bitunix pead pmcc kalshi; do echo "$d boot lines: $(grep -ic $d $J)"; done
grep -Ei "resume|matched=" "$J" | head -6
curl -s -o /dev/null -w "home HTTP %{http_code}\n" http://127.0.0.1:8000/
echo ""
echo "VERIFY DONE - checklist: new PID / 0 tracebacks / NEW config_hash /"
echo "IBIT+XLE+GDX on page + ENTRIES: ARMED / halt cycle OK-OK / 4 MACE loops /"
echo "SPY open=2, GLD no open rungs / divisions healthy / home+mace HTTP 200"
