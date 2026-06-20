#!/usr/bin/env bash
# D4 flip OFF->ON: concurrent_position_guard.enabled false->true. Surgical (only
# that one line), drift-gated, backs up, YAML re-parse + diff-guard. NO restart.
# Operator streams from Desktop:
#   Get-Content $HOME\Desktop\d4on.sh -Raw|ssh azureuser@trading.jacksumner.com "tr -d '\r'|bash"
# AFTER: operator restarts via az run-command, then VERIFY.md section B (live trade).
set -euo pipefail
PROD="/home/azureuser/trading_corp"; cd "$PROD"
YAML="config/strategies.yaml"
BASE_YAML=9ca11a1d3d1bed7d60291d7d493425bd   # prod yaml after the D4 OFF-flag insert
BAK=.bak-pre-d4on-2026-06-20
echo "== drift-gate (prod yaml must == post-D4-insert BASE) =="
md5sum $YAML
[ "$(md5sum $YAML|awk '{print $1}')" = "$BASE_YAML" ] || { echo "ABORT: yaml drift -- re-stage the flip against current prod"; exit 2; }
echo "== backup =="
cp -n $YAML $YAML$BAK
echo "== surgical flip false->true (concurrent_position_guard ONLY) =="
python3 - "$YAML" <<'PYEOF'
import sys, yaml
PATH=sys.argv[1]
OLD="  concurrent_position_guard:\n    enabled: false\n"
NEW="  concurrent_position_guard:\n    enabled: true\n"
s=open(PATH,encoding='utf-8').read()
if yaml.safe_load(s)['bitunix_futures']['concurrent_position_guard']['enabled'] is True:
    print('flip: already ON, no-op'); sys.exit(0)
if s.count(OLD)!=1:
    print('ABORT flip: anchor count', s.count(OLD)); sys.exit(4)
d=s.replace(OLD,NEW,1)
if yaml.safe_load(d)['bitunix_futures']['concurrent_position_guard']['enabled'] is not True:
    print('ABORT flip: post-edit not True'); sys.exit(5)
if d.replace(NEW,OLD,1)!=s:
    print('ABORT flip: diff not exactly the one line'); sys.exit(6)
open(PATH,'w',encoding='utf-8').write(d)
print('flip: concurrent_position_guard enabled -> true')
PYEOF
echo "== re-verify (diff should be ONLY false->true) =="
md5sum $YAML
diff $YAML$BAK $YAML || true
python3 -c "import yaml;print('cpg.enabled =', yaml.safe_load(open('config/strategies.yaml'))['bitunix_futures']['concurrent_position_guard']['enabled'])"
echo "== D4 FLAG ON staged (NO restart). Next: operator restart via az run-command, then VERIFY.md section B =="
