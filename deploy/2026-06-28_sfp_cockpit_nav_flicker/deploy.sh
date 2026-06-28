#!/bin/bash
# SFP cockpit nav + flicker fix — HOT deploy (templates + static CSS only; NO restart).
# Run from the worktree root (where git HEAD == commit 6656edf). Read-only-safe except
# the scp/tar write of 8 files + a pre-backup. Drift-gate already PASSED (prod==main).
set -euo pipefail
HOST=azureuser@trading.jacksumner.com
ROOT=/home/azureuser/trading_corp
FILES="trading_corp/web/templates/sfp_cockpit.html \
trading_corp/web/templates/sfp_cockpit/_header.html \
trading_corp/web/templates/sfp_cockpit/_recon.html \
trading_corp/web/templates/sfp_cockpit/_state_board.html \
trading_corp/web/templates/sfp_cockpit/_mode_split.html \
trading_corp/web/templates/sfp_cockpit/_near_miss.html \
trading_corp/web/templates/sfp_cockpit/_equity.html \
trading_corp/web/static/sfp_cockpit.css"

echo "== 1. backup current 8 prod files =="
ssh "$HOST" "mkdir -p ~/cockpit_bak_2026-06-28 && cd $ROOT && tar czf ~/cockpit_bak_2026-06-28/cockpit_pre.tgz $FILES && echo backed-up"

echo "== 2. deploy LF blobs from git HEAD (tar over ssh) =="
git archive HEAD -- $FILES | ssh "$HOST" "cd $ROOT && tar xf - && echo extracted"

echo "== 3. verify prod md5 == HEAD blob md5 =="
ssh "$HOST" "cd $ROOT && md5sum $FILES"
echo "(compare against: git -C <worktree> show HEAD:<f> | md5sum — must match; templates/static are hot, NO restart)"
