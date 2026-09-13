set -u
ROOT=/home/azureuser/trading_corp
echo "### PHASE-6 DEPLOY-PREP (READ-ONLY): box git state + legacy timer units $(date -u +%FT%TZ) ###"
cd "$ROOT" || { echo "cd fail"; exit 2; }
echo "===BOX_GIT==="
if [ -d .git ]; then
  echo "is-git-repo=YES"
  echo "HEAD=$(git rev-parse HEAD 2>/dev/null)"
  echo "branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
  echo "-- upstream/remotes --"; git remote -v 2>/dev/null | head -4
  echo "-- status (porcelain, first 30) --"; git status --porcelain 2>/dev/null | head -30
  echo "-- porcelain line count --"; git status --porcelain 2>/dev/null | wc -l
  echo "-- main.py tracked+clean? --"; git status --porcelain trading_corp/main.py 2>/dev/null || echo "(clean)"
  echo "-- does box HEAD == fcbcd4a7? --"; git merge-base --is-ancestor fcbcd4a7cde71abb33ba418be12d7ad5ff2e8869 HEAD 2>/dev/null && echo "fcbcd4a7 is ancestor of box HEAD" || echo "fcbcd4a7 NOT ancestor (box HEAD diverged/behind)"
else
  echo "is-git-repo=NO (deploy = file graft only)"
fi
echo "===BOX_GIT_END==="
echo "===MAINPY_ON_BOX==="
md5=$(tr -d '\r' < trading_corp/main.py | md5sum | cut -c1-32); echo "box main.py md5(CR-stripped)=$md5 loc=$(wc -l < trading_corp/main.py)"
echo "===MAINPY_ON_BOX_END==="
echo "===LEGACY_TIMER_UNITS (all trading-corp/kalshi/polymarket/apify timers+their services)==="
cd /etc/systemd/system 2>/dev/null || echo "(cannot cd systemd dir)"
ls -la *.timer 2>/dev/null | grep -iE 'kalshi|polymarket|poly_kalshi|apify|whale|copy' || echo "(no legacy .timer by name)"
echo "-- ALL .timer units present --"; ls -1 *.timer 2>/dev/null
echo "-- their enable/active state --"
for t in $(ls -1 *.timer 2>/dev/null); do
  st=$(systemctl is-enabled "$t" 2>/dev/null); ac=$(systemctl is-active "$t" 2>/dev/null); echo "  $t  enabled=$st active=$ac"
done
echo "===LEGACY_TIMER_UNITS_END==="
echo "### DONE ###"
