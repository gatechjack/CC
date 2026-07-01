#!/bin/sh
# apply.sh - deploy the Kalshi copy-trader fixes (correctness + OFF-by-default filter).
# RUN IN git-bash from a checkout of branch kalshi-copy-recording-shortfilter-2026-07-01.
# Byte-exact LF stream from the git blob. Backs up each prod file first. Does NOT restart
# (operator restarts at a flat window). azureuser-owned files => no root needed.
#   Usage:  sh deploy/2026-07-01_kalshi_copy_fixes/apply.sh [git-ref]   (default: HEAD)
set -e
REF="${1:-HEAD}"
RHOST=azureuser@trading.jacksumner.com
BASE=/home/azureuser/trading_corp
TAG=bak-pre-copyfix-2026-07-01
FILES="trading_corp/brokers/kalshi_live.py trading_corp/brokers/kalshi.py trading_corp/agents/strategies/kalshi_copy_trader.py trading_corp/agents/kalshi_resolver.py trading_corp/main.py config/strategies.yaml"

echo "== pre-flight: prod content == main base (LF-normalized) =="
for f in $FILES; do
  p=$(ssh "$RHOST" "tr -d '\r' < $BASE/$f | md5sum | cut -d' ' -f1")
  b=$(git show "main:$f" | md5sum | cut -d' ' -f1)
  if [ "$p" != "$b" ]; then echo "*** DRIFT $f -- ABORT (prod changed since package cut) ***"; exit 1; fi
  echo "  CLEAN $f"
done

echo "== backup + apply from $REF =="
for f in $FILES; do
  ssh "$RHOST" "cp $BASE/$f $BASE/$f.$TAG"
  git show "$REF:$f" | ssh "$RHOST" "cat > $BASE/$f"
  echo "  applied $f (backup $f.$TAG)"
done
echo "APPLY_DONE -- now: sh verify.sh  ; then at a flat window: ssh $RHOST 'sudo -n systemctl restart trading-corp'"
