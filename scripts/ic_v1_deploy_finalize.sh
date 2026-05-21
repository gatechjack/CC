#!/usr/bin/env bash
# Final stage of the chunked IC v1 deploy.
# Run on prod after all 11 chunk files are uploaded to /tmp/ic_v1_chunks/.
#
# Does:
#   1. Concatenate the chunks into a single base64 payload.
#   2. Decode + verify the xz tarball.
#   3. Backup the 12 to-be-overwritten files with a timestamped suffix.
#   4. Extract the tarball into the trading_corp working dir
#      (creates the 18 new IC modules + overwrites the 12 modified files).
#   5. Import-test the IC modules under the project venv.
#   6. systemctl restart trading-corp + verify status.
#   7. Surface IC init log lines from the new PID + recent failures (if any).
set -e
echo "==> Concatenating chunks"
ls -la /tmp/ic_v1_chunks/chunk_*.b64
cat /tmp/ic_v1_chunks/chunk_*.b64 > /tmp/payload.b64
echo "Payload size: $(wc -c < /tmp/payload.b64) bytes"
echo
echo "==> Decoding base64 -> xz tarball"
base64 -d /tmp/payload.b64 > /tmp/ic_v1.tar.xz
echo "Tarball size: $(wc -c < /tmp/ic_v1.tar.xz) bytes"
md5sum /tmp/ic_v1.tar.xz
echo
echo "==> Tarball contents (sanity preview)"
sudo -u azureuser tar tJf /tmp/ic_v1.tar.xz
echo
cd /home/azureuser/trading_corp
STAMP=$(date +%Y%m%d-%H%M%S)
echo "==> Backing up the 12 files to be overwritten (stamp $STAMP)"
for f in \
  trading_corp/agents/data_exec.py \
  trading_corp/brokers/base.py \
  trading_corp/brokers/paper.py \
  trading_corp/brokers/robinhood.py \
  trading_corp/web/app.py \
  trading_corp/web/templates/approvals.html \
  config/risk.yaml \
  config/macro_calendar.yaml \
  config/divisions.yaml \
  config/strategies.yaml \
  trading_corp/main.py \
  trading_corp/web/routes.py \
; do
  sudo -u azureuser cp -v "$f" "$f.pre-ic-v1-full-$STAMP"
done
echo
echo "==> Extracting tarball"
sudo -u azureuser tar xJf /tmp/ic_v1.tar.xz -C /home/azureuser/trading_corp/
echo
echo "==> Post-extract spot checks"
ls -la trading_corp/agents/divisions/robinhood_joint.py
ls -la trading_corp/agents/strategies/robinhood_joint_iron_condor.py
ls -la trading_corp/comms/pending_combo_registry.py
ls -la config/ex_dividend_calendar.yaml
echo "IC content in strategies.yaml: $(grep -c 'robinhood_joint_iron_condor' config/strategies.yaml)"
echo
echo "==> Import test (catch syntax + missing modules before restart)"
sudo -u azureuser venv/bin/python -c "import trading_corp.main; import trading_corp.web.routes; import trading_corp.agents.divisions.robinhood_joint; import trading_corp.agents.strategies.robinhood_joint_iron_condor; import trading_corp.comms.pending_combo_registry; import trading_corp.comms.telegram_batcher; import trading_corp.web.combo_approval_view; import trading_corp.agents.ic_live_view; import trading_corp.agents.ic_telemetry; print('IMPORT OK')"
echo
echo "==> Restarting trading-corp"
sudo systemctl restart trading-corp
sleep 10
echo
echo "==> Service status post-restart"
sudo systemctl is-active trading-corp
PID=$(systemctl show trading-corp --property=MainPID --value)
echo "PID: $PID"
echo
echo "==> Recent systemd events"
sudo journalctl -u trading-corp --no-pager | grep -E 'Started|Stopped|Failed' | tail -5
echo
echo "==> IC init log lines from current PID"
sudo journalctl _PID=$PID --no-pager | grep -ivE 'yfinance|BTCUSDC' | grep -iE 'iron.condor|ic-signal|ic-position|robinhood_joint_iron|IronCondor|combo|RobinhoodJoint' | head -25
echo
echo "==> Any failures since restart?"
sudo journalctl -u trading-corp --since '1 minute ago' --no-pager | grep -ivE 'yfinance|BTCUSDC|paper_trade_replay|polymarket_resolver|kalshi_resolver|kalshi_llm_arbitrage|kalshi_copy_trader|kalshi_sports|fidelity|wallet|primed' | grep -iE 'error|traceback|exception' | head -15 || echo "(no errors found)"
