set -u
ROOT=/home/azureuser/trading_corp
M="$ROOT/trading_corp/main.py"; C="$ROOT/config/strategies.yaml"
echo "### PHASE-6 DEPLOY-STATUS (READ-ONLY) $(date -u +%FT%TZ) ###"
echo "box main.py  md5=$(tr -d '\r' < "$M" | md5sum | cut -c1-32) loc=$(wc -l < "$M")"
echo "  pre-graft = fcee5e813c8faa0904a0900bd41ed2bb / 6209"
echo "  grafted   = c15b4de64ad19b814580cb6ed311262f / 3923"
echo "box config   md5=$(tr -d '\r' < "$C" | md5sum | cut -c1-32)"
echo "  grafted config = 1657119b9a872472a53328ac57e0b5ae"
echo "-- graft backups present? (did graft.sh reach its backup step) --"
ls -la "$ROOT"/trading_corp/main.py.bak_legpm_* "$ROOT"/config/strategies.yaml.bak_legpm_* 2>/dev/null || echo "  (no .bak_legpm_* -> graft.sh backup step did NOT run)"
echo "-- deploy scratch dir --"
ls -la /home/azureuser/legpm_deploy_scratch/ 2>/dev/null || echo "  (no scratch dir)"
echo "-- engine (still running old in-memory code until a restart) --"
systemctl show trading-corp -p MainPID,ActiveState,SubState,ExecMainStartTimestamp,NRestarts 2>/dev/null
echo "### DONE ###"
