#!/usr/bin/env bash
# Piece 3 ws-migration — APPLY (no restart). Streamed via p3_apply.ps1.
# Aborts (set -e) on any md5 mismatch. main.py = targeted-hunk (prod blob + ws
# wiring only); live_bar_cache.py = full-file (prod==branch base); ws_feed = new.
set -e
ROOT=/home/azureuser/trading_corp
ST=/home/azureuser/p3_staged
BAK=/home/azureuser/p1_bak_2026-06-27
WS=$ROOT/trading_corp/data/bitunix_ws_feed.py

echo "== 1/6 verify staged transfer integrity =="
echo "2c1bb1dc80bddf4b90e3b68299b2ef1a  $ST/main.py" | md5sum -c -
echo "d0bff7784fa275e7c10f512255e98b5c  $ST/live_bar_cache.py" | md5sum -c -
echo "a35a27cb6f02bdfe3b0ded610f2c8387  $ST/bitunix_ws_feed.py" | md5sum -c -

echo "== 2/6 verify LIVE files are the expected pre-deploy versions (drift guard) =="
echo "698cd083d484296ac6f991224fdac376  $ROOT/trading_corp/main.py" | md5sum -c -
echo "bf757bfc228315c6204c1dbebf26003b  $ROOT/trading_corp/data/live_bar_cache.py" | md5sum -c -

echo "== 3/6 bitunix_ws_feed.py must NOT already exist (new file) =="
if [ -e "$WS" ]; then echo "ABORT: $WS already exists"; exit 1; fi
echo "ok"

echo "== 4/6 backup =="
mkdir -p "$BAK"
cp -p "$ROOT/trading_corp/main.py" "$BAK/main.py.bak-pre-ws-2026-06-27"
cp -p "$ROOT/trading_corp/data/live_bar_cache.py" "$BAK/live_bar_cache.py.bak-pre-ws-2026-06-27"

echo "== 5/6 place =="
cp "$ST/main.py" "$ROOT/trading_corp/main.py"
cp "$ST/live_bar_cache.py" "$ROOT/trading_corp/data/live_bar_cache.py"
cp "$ST/bitunix_ws_feed.py" "$WS"

echo "== 6/6 verify PLACED md5 == staged =="
echo "2c1bb1dc80bddf4b90e3b68299b2ef1a  $ROOT/trading_corp/main.py" | md5sum -c -
echo "d0bff7784fa275e7c10f512255e98b5c  $ROOT/trading_corp/data/live_bar_cache.py" | md5sum -c -
echo "a35a27cb6f02bdfe3b0ded610f2c8387  $WS" | md5sum -c -

echo "== byte-unchanged proof: SFP observer/strategy/reconciler (18da45f2 / 5c71a103 / 3a23610c) =="
md5sum "$ROOT/trading_corp/agents/divisions/bitunix_sfp_observer.py" \
       "$ROOT/trading_corp/agents/strategies/bitunix_sfp.py" \
       "$ROOT/trading_corp/agents/divisions/bitunix_position_reconciler.py"

echo
echo "APPLY OK. Engine NOT restarted. Restart WHILE SFP FLAT to activate:  powershell -ep bypass -f .\\p1_restart.ps1"
echo "ROLLBACK: restore $BAK/*.bak-pre-ws-2026-06-27 + rm $WS, then restart."
