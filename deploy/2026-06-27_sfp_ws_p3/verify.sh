#!/usr/bin/env bash
# Piece 3 ws-migration — VERIFY (post-restart). Streamed via p3_verify.ps1.
ROOT=/home/azureuser/trading_corp
echo "== service =="
systemctl show -p MainPID -p NRestarts -p ActiveState -p SubState trading-corp
echo
echo "== ws-feed boot markers (last 5 min) =="
journalctl -u trading-corp --since "-5 min" --no-pager 2>/dev/null | \
  grep -iE "bitunix ws feed|ws stale|sfp 15m loop spawned|mode gate|Traceback|ImportError|unexpected keyword" | tail -30
echo "(expect: 'bitunix ws feed started (11 caches, 11 channels)' + 'bitunix ws feed connected (11 channels)'; SFP loop spawned; few/no 'ws stale')"
echo
echo "== deployed md5 (want 2c1bb1dc / d0bff778 / a35a27cb) =="
md5sum "$ROOT/trading_corp/main.py" "$ROOT/trading_corp/data/live_bar_cache.py" "$ROOT/trading_corp/data/bitunix_ws_feed.py"
echo
echo "== byte-unchanged SFP/recon (want 18da45f2 / 5c71a103 / 3a23610c) =="
md5sum "$ROOT/trading_corp/agents/divisions/bitunix_sfp_observer.py" \
       "$ROOT/trading_corp/agents/strategies/bitunix_sfp.py" \
       "$ROOT/trading_corp/agents/divisions/bitunix_position_reconciler.py"
echo
echo "== bar feed advancing (3m bitunix_bar_history latest vs now) =="
sqlite3 -readonly "$ROOT/data/trading_corp.db" "SELECT timeframe, MAX(ts_ms) FROM bitunix_bar_history WHERE symbol='BTCUSDT' GROUP BY timeframe;" 2>&1
date -u +"now %Y-%m-%d %H:%M:%S"
echo "== REST poll draining? (no Bitunix 403/error churn; ws short-circuits the poll) =="
journalctl -u trading-corp --since "-5 min" --no-pager 2>/dev/null | grep -icE "LiveBarCache refresh failed|market/kline"
