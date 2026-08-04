#!/usr/bin/env bash
# Self-gated restart. Order: verify(pending=0, live==baseline, .new==target) -> stop ->
# atomic mv -> md5-verify(live==target) -> [self-heal from .bak on fail] -> start.
# Any pre-stop failure exits WITHOUT stopping (engine stays on current code).
set +e
APPROOT=/home/azureuser/trading_corp
DB="$APPROOT/data/trading_corp.db"
BAK=".bak_pmcc_pricefix_20260804"
cd "$APPROOT" || { echo "APPROOT_FAIL"; exit 3; }
FILES="config/strategies.yaml trading_corp/agents/divisions/pmcc_robinhood.py trading_corp/web/pmcc_pricing.py trading_corp/web/routes.py"
b_config="274b7e348eb2"; t_config="ce2f1c0ee5fc"
b_pmcc="6b928badbcf0";   t_pmcc="0d199b237c05"
b_price="7ee14a4367b6";  t_price="af9a674e79aa"
b_routes="96becb83b19a"; t_routes="c15e84c74521"
tgt(){ case "$1" in config/strategies.yaml)echo $t_config;; *pmcc_robinhood.py)echo $t_pmcc;; *pmcc_pricing.py)echo $t_price;; *routes.py)echo $t_routes;; esac; }
base(){ case "$1" in config/strategies.yaml)echo $b_config;; *pmcc_robinhood.py)echo $b_pmcc;; *pmcc_pricing.py)echo $b_price;; *routes.py)echo $b_routes;; esac; }
m(){ tr -d '\r' < "$1" 2>/dev/null | md5sum | cut -c1-12; }

# ---- PRE-STOP SELF-GATE (no mutation) ----
PEND=$(sqlite3 "$DB" "SELECT COUNT(*) FROM pending_order WHERE state='pending';" 2>/dev/null)
echo "SELF_GATE pending=$PEND"
[ "$PEND" = "0" ] || { echo "ABORT_PENDING_NONZERO=$PEND — engine NOT stopped"; exit 10; }
for f in $FILES; do
  lv=$(m "$f"); nv=$(m "$f.new")
  [ "$lv" = "$(base "$f")" ] || { echo "ABORT_LIVE_DRIFT $f live=$lv want_base=$(base "$f") — engine NOT stopped"; exit 11; }
  [ "$nv" = "$(tgt "$f")" ]  || { echo "ABORT_NEW_MISMATCH $f new=$nv want_tgt=$(tgt "$f") — engine NOT stopped"; exit 12; }
done
echo "SELF_GATE PASS (pending=0, live==baseline, .new==target)"

OLDPID=$(systemctl show -p MainPID --value trading-corp)
echo "OLDPID=$OLDPID  stopping trading-corp..."
systemctl stop trading-corp
echo "stopped is-active=$(systemctl is-active trading-corp)"

for f in $FILES; do mv -f "$f.new" "$f"; done
echo "moved 4 files"

HEAL=0
for f in $FILES; do g=$(m "$f"); if [ "$g" = "$(tgt "$f")" ]; then echo "MV_OK $f=$g"; else echo "MV_FAIL $f got=$g want=$(tgt "$f")"; HEAL=1; fi; done

if [ "$HEAL" = "1" ]; then
  echo "SELF_HEAL: restoring .bak set"
  for f in $FILES; do cp -p "$f$BAK" "$f"; done
  systemctl start trading-corp; sleep 9
  echo "HEALED is-active=$(systemctl is-active trading-corp) pid=$(systemctl show -p MainPID --value trading-corp)"
  echo "RESULT: ROLLED_BACK_MV_FAIL"; exit 20
fi

systemctl start trading-corp
sleep 10
NEWPID=$(systemctl show -p MainPID --value trading-corp)
ACT=$(systemctl is-active trading-corp)
SUB=$(systemctl show -p SubState --value trading-corp)
NR=$(systemctl show -p NRestarts --value trading-corp)
echo "NEWPID=$NEWPID is-active=$ACT sub=$SUB nrestarts=$NR"
if [ "$ACT" != "active" ]; then
  echo "START_NOT_ACTIVE — SELF_HEAL restoring .bak"
  systemctl stop trading-corp 2>/dev/null
  for f in $FILES; do cp -p "$f$BAK" "$f"; done
  systemctl start trading-corp; sleep 9
  echo "HEALED is-active=$(systemctl is-active trading-corp) pid=$(systemctl show -p MainPID --value trading-corp)"
  echo "RESULT: ROLLED_BACK_START_FAIL"; exit 21
fi
echo "RESULT: RESTARTED old=$OLDPID new=$NEWPID"
