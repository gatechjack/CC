#!/bin/bash
# Quick post-deploy health check for the wrap-up report.
set -e

echo "=== deployed file md5s ==="
md5sum /home/azureuser/trading_corp/trading_corp/agents/paper_trade_replay.py
md5sum /home/azureuser/trading_corp/scripts/audit_reality_reconciler.py
echo ""

echo "=== backup file still present ==="
ls -l /home/azureuser/trading_corp/trading_corp/agents/paper_trade_replay.py.pre-v2-kline-fix-20260520
echo ""

echo "=== service state ==="
systemctl is-active trading-corp
sudo -u azureuser cat /home/azureuser/trading_corp/data/trading_corp.pid 2>/dev/null
echo ""

echo "=== reconciler timer state ==="
systemctl is-active tc-audit-reality.timer
systemctl is-enabled tc-audit-reality.timer
systemctl list-timers tc-audit-reality.timer --no-pager 2>&1 | head -4
echo ""

echo "=== reconciler last-run result ==="
systemctl status tc-audit-reality.service --no-pager 2>&1 | head -10 | tail -5
echo ""

echo "=== any new v2 trades since 5/20 10:37 UTC? ==="
sqlite3 -readonly /home/azureuser/trading_corp/data/trading_corp.db "
SELECT COUNT(*) AS n_v2_post_deploy
FROM paper_trade_record
WHERE division='bitunix_futures'
  AND ts >= '2026-05-20T10:37:00+00:00'
  AND json_extract(extra_json,'\$.tp_plan_version')='v2';
"

echo "=== any position_sl_update audits since deploy? (would be first ever) ==="
sqlite3 -readonly /home/azureuser/trading_corp/data/trading_corp.db "
SELECT COUNT(*) AS n_sl_updates_post_deploy
FROM audit_event
WHERE kind='position_sl_update' AND ts >= '2026-05-20T10:37:00+00:00';
"

echo "=== recent bitunix activity (last 4 score_decided) ==="
sqlite3 -readonly /home/azureuser/trading_corp/data/trading_corp.db "
SELECT ts, json_extract(payload_json,'\$.side'), json_extract(payload_json,'\$.tier')
FROM audit_event
WHERE kind='bitunix_score_decided'
ORDER BY ts DESC LIMIT 4;
"

echo "=== DONE ==="
