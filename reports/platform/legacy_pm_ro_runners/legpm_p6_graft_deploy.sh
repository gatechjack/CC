set -u
ROOT=/home/azureuser/trading_corp
LIVE_MAIN="$ROOT/trading_corp/main.py"
LIVE_CFG="$ROOT/config/strategies.yaml"
SCRATCH=/home/azureuser/legpm_deploy_scratch
SM="$SCRATCH/main.py.staged"
SC="$SCRATCH/strategies.yaml.staged"
PY="$ROOT/venv/bin/python3"
MODE="${MODE:-verify}"
EXP_MAIN_GRAFTED=c15b4de64ad19b814580cb6ed311262f
EXP_MAIN_PRE=fcee5e813c8faa0904a0900bd41ed2bb
EXP_CFG_GRAFTED=1657119b9a872472a53328ac57e0b5ae
EXP_MAIN_LOC=3923

md5cr(){ tr -d '\r' < "$1" | md5sum | cut -c1-32; }
loc(){ wc -l < "$1"; }

echo "### PHASE-6 GRAFT-DEPLOY (MODE=$MODE) $(date -u +%FT%TZ) ###"
for f in "$SM" "$SC"; do [ -f "$f" ] || { echo "FATAL: staged file missing: $f (scp step did not run?)"; exit 3; }; done

SM_MD5=$(md5cr "$SM"); SM_LOC=$(loc "$SM"); SC_MD5=$(md5cr "$SC")
echo "staged main.py  md5=$SM_MD5 loc=$SM_LOC  (expect $EXP_MAIN_GRAFTED / $EXP_MAIN_LOC)"
echo "staged config   md5=$SC_MD5              (expect $EXP_CFG_GRAFTED)"
[ "$SM_MD5" = "$EXP_MAIN_GRAFTED" ] || { echo "FATAL: staged main.py md5 mismatch"; exit 4; }
[ "$SM_LOC" = "$EXP_MAIN_LOC" ]     || { echo "FATAL: staged main.py loc mismatch"; exit 4; }
[ "$SC_MD5" = "$EXP_CFG_GRAFTED" ]  || { echo "FATAL: staged config md5 mismatch"; exit 4; }

echo "-- staged main.py SURVIVOR counts (must be unchanged) --"
for pair in "bitunix:196" "pm_live_driver:4" "scheduled_pm_live_loop:2" "scheduled_shard_snapshot_loop:1" "_scheduled_pmcc_scan_loop:2" "_scheduled_pead_scan_loop:2" "_scheduled_donchian_loop:3"; do
  pat="${pair%%:*}"; want="${pair##*:}"; got=$(grep -c "$pat" "$SM"); [ "$got" = "$want" ] && f=OK || f="MISMATCH(want $want)"; printf "   %-34s %s %s\n" "$pat" "$got" "$f"
done
maceic=$(grep -ic 'mace' "$SM"); [ "$maceic" = 119 ] && f=OK || f="MISMATCH(want 119)"; printf "   %-34s %s %s\n" "mace(-ic)" "$maceic" "$f"
echo "-- staged main.py LEGACY counts (want 0) --"
LEG=0
for pat in _scheduled_polymarket_arb_loop _scheduled_polymarket_copy_trader_loop _scheduled_poly_kalshi_loop _scheduled_kalshi_arb_loop _scheduled_kalshi_tb_arb_loop _scheduled_kalshi_llm_arb_loop _scheduled_kalshi_weather_arb_loop _scheduled_kalshi_crypto_arb_loop _scheduled_kalshi_sports_scout_loop _scheduled_kalshi_sports_arb_observer_loop _scheduled_kalshi_copy_trader_loop start_kalshi_resolver_loop start_poly_kalshi_mark_loop; do
  g=$(grep -c "$pat" "$SM"); LEG=$((LEG+g)); [ "$g" != 0 ] && printf "   NONZERO %s = %s\n" "$pat" "$g"
done
echo "   legacy total = $LEG (want 0)"
[ "$LEG" = 0 ] || { echo "FATAL: staged main.py still has legacy wiring"; exit 4; }
echo "-- staged main.py py_compile (box venv) --"
"$PY" -m py_compile "$SM" && echo "   PY_COMPILE_OK" || { echo "   PY_COMPILE_FAIL"; exit 5; }
echo "-- staged config yaml parse + poly_kalshi auto_execute --"
"$PY" - "$SC" <<'PYEOF'
import yaml,sys
d=yaml.safe_load(open(sys.argv[1],encoding="utf-8"))
pk=d.get("poly_kalshi_mlb",{})
print("   yaml_ok top_keys=%d poly_kalshi_mlb.enabled=%s auto_execute=%s"%(len(d),pk.get("enabled"),pk.get("auto_execute")))
assert pk.get("auto_execute") is False, "auto_execute not False"
PYEOF
[ $? -eq 0 ] || { echo "FATAL: config check failed"; exit 5; }

LM_MD5=$(md5cr "$LIVE_MAIN"); LM_LOC=$(loc "$LIVE_MAIN")
echo "-- LIVE main.py md5=$LM_MD5 loc=$LM_LOC --"
ALREADY=0
if [ "$LM_MD5" = "$EXP_MAIN_GRAFTED" ]; then
  echo "   NOTE: live main.py ALREADY grafted (idempotent no-op)."; ALREADY=1
elif [ "$LM_MD5" = "$EXP_MAIN_PRE" ]; then
  echo "   DRIFT GATE OK: live main.py == expected PRE-graft prod-live baseline."
else
  echo "   FATAL: live main.py is NEITHER pre-graft ($EXP_MAIN_PRE) NOR grafted ($EXP_MAIN_GRAFTED)."
  echo "   Something deployed underneath us. Aborting."; exit 6
fi

if [ "$MODE" = "verify" ]; then
  echo "### VERIFY-ONLY: staged files proven end-to-end on the box; NO live write performed. ###"; exit 0
fi
if [ "$MODE" != "graft" ]; then echo "FATAL: unknown MODE=$MODE"; exit 9; fi

if [ "$ALREADY" = 1 ]; then echo "### GRAFT skipped: live already == grafted. NO restart (reserved for Jack). ###"; exit 0; fi
TS=$(date -u +%Y%m%dT%H%M%SZ)
BM="$LIVE_MAIN.bak_legpm_$TS"; BC="$LIVE_CFG.bak_legpm_$TS"
cp -p "$LIVE_MAIN" "$BM" && cp -p "$LIVE_CFG" "$BC" || { echo "FATAL: backup failed; aborting BEFORE any write"; exit 7; }
echo "BACKED UP: $BM"
echo "BACKED UP: $BC"
tr -d '\r' < "$SM" > "$LIVE_MAIN"
tr -d '\r' < "$SC" > "$LIVE_CFG"
DM=$(md5cr "$LIVE_MAIN"); DL=$(loc "$LIVE_MAIN"); DC=$(md5cr "$LIVE_CFG")
echo "DEPLOYED main.py md5=$DM loc=$DL (expect $EXP_MAIN_GRAFTED/$EXP_MAIN_LOC)"
echo "DEPLOYED config  md5=$DC (expect $EXP_CFG_GRAFTED)"
if [ "$DM" = "$EXP_MAIN_GRAFTED" ] && [ "$DL" = "$EXP_MAIN_LOC" ] && [ "$DC" = "$EXP_CFG_GRAFTED" ]; then
  "$PY" -m py_compile "$LIVE_MAIN" && echo "DEPLOYED py_compile OK" || { echo "FATAL: py_compile on deployed file"; echo "RESTORE: cp -p $BM $LIVE_MAIN && cp -p $BC $LIVE_CFG"; exit 8; }
  echo "### GRAFT DONE. NO restart performed (reserved for Jack). ###"
  echo "RESTORE (if ever needed): cp -p $BM $LIVE_MAIN && cp -p $BC $LIVE_CFG   (then Jack restarts)"
else
  echo "FATAL: deployed-file mismatch."
  echo "RESTORE NOW: cp -p $BM $LIVE_MAIN && cp -p $BC $LIVE_CFG"; exit 8
fi
