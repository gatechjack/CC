#!/usr/bin/env bash
# Piece 1 two-state collapse — VERIFY (post-restart). Streamed via p1_verify.ps1.
ROOT=/home/azureuser/trading_corp
echo "== service =="
systemctl show -p MainPID -p NRestarts -p ActiveState -p SubState trading-corp
echo
echo "== two-state boot markers (last 4 min) =="
journalctl -u trading-corp --since "-4 min" --no-pager 2>/dev/null | \
  grep -iE "bitunix_sfp mode gate|sfp 15m loop spawned|bitunix_futures HALTED|pa-redeem loop NOT started|boot catch-up SKIPPED|REFUSING TO START|Traceback|ImportError|unexpected keyword" | tail -40
echo
echo "== deployed md5 (must match staged: main 698cd083 / futobs dd64a7f4 / yaml 0cd6e45d) =="
md5sum "$ROOT/trading_corp/main.py" \
       "$ROOT/trading_corp/agents/divisions/bitunix_futures_observer.py" \
       "$ROOT/config/strategies.yaml"
echo
echo "== byte-unchanged SFP/recon (must be 18da45f2 / 5c71a103 / 3a23610c) =="
md5sum "$ROOT/trading_corp/agents/divisions/bitunix_sfp_observer.py" \
       "$ROOT/trading_corp/agents/strategies/bitunix_sfp.py" \
       "$ROOT/trading_corp/agents/divisions/bitunix_position_reconciler.py"
echo
echo "== sanity: replay task should be ABSENT, SFP loop PRESENT =="
journalctl -u trading-corp --since "-4 min" --no-pager 2>/dev/null | \
  grep -iE "paper_trade_replay_loop|bitunix-sfp-loop|bitunix_sfp mode gate" | tail -10
echo "(expect: 'bitunix_sfp mode gate: mode=trading trading=True' + 'sfp 15m loop spawned'; NO replay loop start)"
