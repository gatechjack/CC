#!/usr/bin/env bash
# Piece 2 egress-swap verify (runs ON prod). Streamed via p2_verify.ps1.
echo "== egress IP (must equal the NEW NAT-gw IP from az step 9) =="
curl -s ifconfig.me; echo
echo "== public kline (expect 200 = Cloudflare flag cleared) =="
curl -s -o /dev/null -w '%{http_code}\n' 'https://fapi.bitunix.com/api/v1/futures/market/kline?symbol=BTCUSDT&interval=1m&limit=1'
echo "== authed (run AFTER you re-bind the key; expect 403s GONE, snapshot real) =="
journalctl -u trading-corp --since '-3 min' --no-pager 2>/dev/null | grep -iE 'get_pending_positions|403|snapshot|reconcil' | tail -15
