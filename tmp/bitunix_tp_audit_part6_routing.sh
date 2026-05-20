#!/bin/bash
# Grab the v2 routing condition + classifier entry from paper_trade_replay.py
F=/home/azureuser/trading_corp/trading_corp/agents/paper_trade_replay.py

echo "=== Routing block (line 720-770) ==="
sed -n '720,775p' "$F"
echo ""
echo "=== _classify_v2_multi_leg routing condition (line 380-410) ==="
sed -n '380,410p' "$F"
echo ""
echo "=== Bar walking loop with TP detection (around line 480-560) ==="
sed -n '475,560p' "$F"
echo ""
echo "=== Where bars are loaded / entry bar handling (look for first bar of trade) ==="
grep -n "bars_to_resolution\|entry_bar\|skip.*entry\|first.*bar\|start.*bar" "$F" | head -20
echo "=== DONE ==="
