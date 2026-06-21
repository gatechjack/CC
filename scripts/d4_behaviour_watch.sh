#!/usr/bin/env bash
# D4 concurrent-position guard — READ-ONLY behaviour watch.
#
# Purpose: record the D4 guard's live decisions so the still-PENDING
# POST-MANUAL-FLATTEN path can be observed/banked. After the bot's position
# is closed at the venue (e.g. operator SL-to-price), the NEXT same-side
# signal MUST pass (venue-flat read is authoritative; the lagging engine
# `result IS NULL` row must NOT block). The guard logs nothing special on a
# PASS — the entry just proceeds — so this watch captures BOTH the block
# events AND the bitunix entry/exit/reconcile lines, and you read the
# sequence: a same-side `live_order_placed` with NO preceding
# `blocked_concurrent_position` in the post-flatten window = the pass banked.
#
# READ-ONLY: it only reads the systemd journal (no sudo needed — azureuser can
# read it), filters with grep, and appends to a log file in $HOME. It NEVER
# touches the engine, broker, orders, DB, or config. Safe to start/kill anytime.
#
# Run on PROD (it tails the local journal):
#   nohup bash ~/d4_behaviour_watch.sh >/dev/null 2>&1 &      # detached
#   tail -f ~/d4_watch_*.log                                  # see it live
#
# Lifecycle: a `journalctl -f` tail is independent of the engine, so this
# SURVIVES engine restarts (it keeps logging across them) and only stops on
# kill / VM reboot. Re-launch anytime for a fresh per-window log.
set -uo pipefail

LOG="${1:-$HOME/d4_watch_$(date -u +%Y%m%dT%H%M%SZ).log}"

PATTERN='concurrent[ _-]position[ _-]guard|concurrent_position_guard_blocked|blocked_concurrent_position|bot_own_same_side|venue_state_unknown_fail_closed|guard \(D4\)|bitunix_futures/(live_order_placed|would_have_placed|live_order_rejected|live_exit_order|bracket_placed)|position_state_(reconciled|divergence)'

echo "# D4 behaviour-watch started $(date -u +%Y-%m-%dT%H:%M:%SZ) UTC → $LOG" | tee -a "$LOG"
echo "# read-only journal tail; grep pattern below; Ctrl-C or kill to stop" | tee -a "$LOG"
echo "# pattern: $PATTERN" >> "$LOG"

# -n 0: start from now (no history dump). -f: follow. -o short-iso: ISO ts.
journalctl -u trading-corp -f -n 0 -o short-iso 2>/dev/null \
  | grep --line-buffered -iE "$PATTERN" \
  | tee -a "$LOG"
