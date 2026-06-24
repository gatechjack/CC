#!/usr/bin/env bash
# PEAD go-live — FILE INSTALL (no sudo; run as azureuser ON PROD).
# drift-gate (abort if prod drifted since 2026-06-24 staging) -> backup -> copy -> verify.
# Does NOT touch the systemd unit and does NOT restart (those are separate steps).
set -uo pipefail
TC=/home/azureuser/trading_corp
PAY="$(cd "$(dirname "$0")" && pwd)/payload"
TAG=pre-golive-2026-06-24
fail(){ echo "ABORT: $*" >&2; exit 9; }

# file  baseline(prod-pre)  target(post)
ROWS='
config/strategies.yaml 4ed38e9d0f4e3a03137d15a5732e6443 36f5b32309e4342a4521a69a8cb53a42
config/divisions.yaml 2be55d87d0fdc74e33e6ad7285e83842 090174da86bddc9d2a4fdcc74b631d2c
trading_corp/agents/divisions/robinhood_pead.py 93c0a588e45939b4ca5825720a14b2ef 5b5cfb515a920767c150ecb44234a8ba
trading_corp/agents/strategies/pead_strategy.py fe739c7edeb6c1276e12abe7bed0ae0c ae6d39e124e2a06cb234224d548456c1
trading_corp/brokers/robinhood.py 26de02729bc0403d472e48acea9ed03e 72f7944c73abc2c02a71b3f8644ed53c
trading_corp/main.py cf98e88d58f6d89328b1ca138038563f ec7bd6962bba02d1ba5b601af131f4e2
trading_corp/persistence/db.py b1c6a6a2c41d8692678e652eacadea33 9cb0f65485b976d6b39f6005d02dfd2d
trading_corp/persistence/models.py 61ecc75535bab3a42a75f2a4bc0a4f5f 0e917dacebce1a3d8ea574e792279841
'

echo "== DRIFT-GATE: prod must match 2026-06-24 staging baseline (else prod changed -> re-stage) =="
while read -r f base tgt; do [ -z "$f" ] && continue
  [ -f "$TC/$f" ] || fail "prod missing $f"
  cur=$(md5sum "$TC/$f" | cut -d' ' -f1)
  [ "$cur" = "$base" ] || fail "DRIFT on $f: prod=$cur expected=$base — re-stage before deploy"
  [ -f "$PAY/$f" ] || fail "payload missing $f"
  pm=$(md5sum "$PAY/$f" | cut -d' ' -f1)
  [ "$pm" = "$tgt" ] || fail "payload $f corrupt: $pm != target $tgt"
done <<< "$ROWS"
echo "  OK: 8/8 prod==baseline, 8/8 payload==target"

echo "== BACKUP -> *.bak-$TAG =="
while read -r f base tgt; do [ -z "$f" ] && continue; cp -p "$TC/$f" "$TC/$f.bak-$TAG"; done <<< "$ROWS"
echo "  backed up 8 files"

echo "== INSTALL + VERIFY target md5 =="
while read -r f base tgt; do [ -z "$f" ] && continue
  cp "$PAY/$f" "$TC/$f"
  cur=$(md5sum "$TC/$f" | cut -d' ' -f1)
  [ "$cur" = "$tgt" ] || fail "post-copy mismatch $f: $cur != $tgt"
done <<< "$ROWS"
echo "  OK: installed + verified 8/8 target md5"
echo "FILES INSTALLED. NEXT: ./preserve_check.sh  then  sudo unit_flip (runbook)  then restart."
