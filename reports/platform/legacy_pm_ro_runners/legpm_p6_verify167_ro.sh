set -u
ROOT=/home/azureuser/trading_corp
cd "$ROOT" || { echo "cd fail"; exit 2; }
echo "### PHASE-6 STEP2: re-verify 16.7 (box shared-file md5 CR-stripped + LOC + wiring counts) $(date -u +%FT%TZ) ###"
echo "## shared files: md5(CR-stripped)  loc  path ##"
for f in trading_corp/main.py trading_corp/persistence/db.py trading_corp/brokers/robinhood.py trading_corp/brokers/base.py trading_corp/agents/data_exec.py trading_corp/brokers/kalshi_live.py trading_corp/web/data.py trading_corp/web/routes.py; do
  md5=$(tr -d '\r' < "$f" | md5sum | cut -c1-32); loc=$(wc -l < "$f")
  printf "  %s  loc=%-5s %s\n" "$md5" "$loc" "$f"
done
M=trading_corp/main.py
echo "## main.py SURVIVOR wiring ref counts (must stay unchanged post-graft) ##"
printf "  bitunix (grep -c)                          %s\n" "$(grep -c 'bitunix' "$M")"
printf "  mace/MACE (grep -ic)                        %s\n" "$(grep -ic 'mace' "$M")"
for pat in pm_live_driver scheduled_pm_live_loop scheduled_shard_snapshot_loop _scheduled_pmcc_scan_loop _scheduled_pead_scan_loop _scheduled_donchian_loop; do
  printf "  %-42s %s\n" "$pat" "$(grep -c "$pat" "$M")"
done
echo "## main.py LEGACY wiring ref counts (targets to remove) ##"
for pat in _scheduled_polymarket_arb_loop _scheduled_polymarket_copy_trader_loop _scheduled_poly_kalshi_loop _scheduled_kalshi_arb_loop _scheduled_kalshi_tb_arb_loop _scheduled_kalshi_llm_arb_loop _scheduled_kalshi_weather_arb_loop _scheduled_kalshi_crypto_arb_loop _scheduled_kalshi_sports_scout_loop _scheduled_kalshi_sports_arb_observer_loop _scheduled_kalshi_copy_trader_loop start_kalshi_resolver_loop start_poly_kalshi_mark_loop; do
  printf "  %-44s %s\n" "$pat" "$(grep -c "$pat" "$M")"
done
echo "### DONE ###"
