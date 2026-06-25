#!/usr/bin/env bash
# GATE 3 boot-smoke (run AS azureuser, ~30-60s AFTER restart). Prints OK/FAIL per line.
# Exit 0 = all hard checks green; exit 1 = a hard check failed -> ROLL BACK, don't walk away.
TC=/home/azureuser/trading_corp
PY="$TC/venv/bin/python"
HARDFAIL=0
fail(){ echo "  FAIL: $*"; HARDFAIL=1; }
ok(){ echo "  OK: $*"; }
warn(){ echo "  WARN: $*"; }

echo "== ENGINE =="
systemctl is-active --quiet trading-corp || fail "service not active"
nr=$(systemctl show trading-corp -p NRestarts --value); ok "active (NRestarts=$nr — expect prior+1)"
hz=""; for i in $(seq 1 20); do hz=$(curl -s -m6 localhost:8000/healthz); echo "$hz" | grep -q '"mode":"LIVE"' && break; sleep 5; done
echo "$hz" | grep -q '"mode":"LIVE"' && ok "healthz LIVE ($hz)" || fail "healthz not LIVE after ~100s: $hz"
es=$(systemctl show trading-corp -p ExecStart --value)
echo "$es" | grep -q 'bitunix_futures'  && ok "ExecStart has bitunix_futures (PRESERVED)" || fail "ExecStart lost bitunix_futures"
echo "$es" | grep -q 'robinhood_pead'    && ok "ExecStart has robinhood_pead (go-live flip 2)" || fail "ExecStart missing robinhood_pead"

echo "== DEPLOYED CODE = fractional + flag-2 =="
grep -q 'place_fractional_pending' "$TC/trading_corp/brokers/robinhood.py" && ok "robinhood.py fractional+flag-2 present" || fail "robinhood.py NOT fractional"
grep -q '_scheduled_pead_reconcile_loop' "$TC/trading_corp/main.py" && ok "main.py reconcile loop present" || fail "main.py missing reconcile loop"
$PY -c "import sqlite3;c=sqlite3.connect('$TC/data/trading_corp.db');import sys;sys.exit(0 if c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='pending_order'\").fetchone() else 1)" && ok "pending_order table created (db migration ran)" || fail "pending_order table missing"

echo "== PEAD 4 go-live flips =="
grep -A3 '^robinhood_pead:' "$TC/config/strategies.yaml" | grep -q 'auto_execute: true' && ok "auto_execute: true" || fail "auto_execute != true"
grep -A8 'slug: robinhood_pead' "$TC/config/divisions.yaml" | grep -q 'standby: false' && ok "standby: false" || fail "standby != false"
EK=$(KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/ $PY -c "from trading_corp.utils.secrets import load_secrets; s=load_secrets(); print(1 if getattr(s,'eodhd_api_key',None) else 0)" 2>/dev/null)
[ "$EK" = "1" ] && ok "EODHD-API-KEY loads from KV (EarningsProvider wired)" || fail "EODHD key did NOT load from KV"

echo "== BOOT LOG (last 4 min) — Bitunix clean + PEAD up + RH auth =="
LOG=$(journalctl -u trading-corp --since '4 min ago' --no-pager 2>/dev/null)
echo "$LOG" | grep -qiE 'Traceback|ImportError|ModuleNotFound|FillEvent.*(error|role)' && fail "tracebacks/import/FillEvent errors in boot log" || ok "no tracebacks / import / FillEvent errors"
echo "$LOG" | grep -qi 'division=bitunix_futures (paper=False' && ok "BitunixBroker connected paper=False" || warn "bitunix paper=False line not seen (check manually)"
echo "$LOG" | grep -qi 'RobinhoodBroker logged in' && ok "RH auth: RobinhoodBroker logged in" || fail "RH did NOT log in (auth broken -> PEAD can't place)"
echo "$LOG" | grep -q '680725082' && ok "RH discovered account 680725082" || warn "680725082 not in recent log (check connect output)"
echo "$LOG" | grep -qi 'Robinhood PEAD wired' && ok "PEAD wired" || warn "PEAD wired line not seen"
echo "$LOG" | grep -qi 'PEAD scan scheduler online' && ok "PEAD scan scheduler online (will fire 8:30-9:25 ET)" || warn "scan scheduler line not seen"
echo "$LOG" | grep -qi 'PEAD deferred-fill reconciler online' && ok "PEAD reconcile loop online" || warn "reconciler line not seen"
echo "$LOG" | grep -qi 'PEAD position manager online' && ok "PEAD manage loop online" || warn "manage line not seen"
curl -s -m6 -o /dev/null -w '  /telemetry/pead HTTP %{http_code}\n' localhost:8000/telemetry/pead 2>/dev/null

echo "============================================================"
if [ "$HARDFAIL" -eq 0 ]; then echo "BOOTSMOKE: ALL HARD CHECKS GREEN — PEAD live, Bitunix preserved."; exit 0
else echo "BOOTSMOKE: HARD FAILURE above — RUN rollback.sh + sudo revert ExecStart + restart. Do NOT walk away."; exit 1; fi
