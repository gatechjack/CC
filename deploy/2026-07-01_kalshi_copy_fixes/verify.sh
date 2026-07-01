#!/bin/sh
# verify.sh - post-apply byte verification (LF-normalized content md5; line-ending-agnostic).
# Require OK x6 before restarting.  Usage:  sh deploy/2026-07-01_kalshi_copy_fixes/verify.sh
RHOST=azureuser@trading.jacksumner.com
BASE=/home/azureuser/trading_corp
fail=0
chk() {
  got=$(ssh "$RHOST" "tr -d '\r' < $BASE/$1 | md5sum | cut -d' ' -f1")
  if [ "$got" = "$2" ]; then echo "OK   $1"; else echo "FAIL $1 got=$got want=$2"; fail=1; fi
}
chk trading_corp/brokers/kalshi_live.py                     bbd851a6194c638df4bb3a9f2c3d3e63
chk trading_corp/brokers/kalshi.py                          18626cf0ddcdf6c3663be7d9602abbba
chk trading_corp/agents/strategies/kalshi_copy_trader.py    b2a2d1f1a2e432c30c2d1cba55b4918c
chk trading_corp/agents/kalshi_resolver.py                  b7a884eb1209cd3a4d4f2b89d1825f2f
chk trading_corp/main.py                                    3eb61f8c110ee74b720d3ac1df525c85
chk config/strategies.yaml                                  f4a93c701d66217e1fa679324a5791d2
echo "--- filter must be OFF (0) for a correctness-only deploy ---"
ssh "$RHOST" "grep -n 'min_minutes_to_resolution' $BASE/config/strategies.yaml"
[ "$fail" = "0" ] && echo "VERIFY_OK -- safe to restart at a flat window" || echo "VERIFY_FAILED -- do NOT restart; investigate/rollback"
