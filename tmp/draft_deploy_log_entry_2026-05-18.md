## 2026-05-18 HH:MM UTC — kalshi_structure_arb paper deploy + kalshi_llm_arbitrage cuts

**Commits:** `2cc13c9` (EOS snapshot) + uncommitted local changes (strategy + main + config edits)
**Triggered by:** Board-approved Backtester memo 2026-05-17: deploy kalshi_structure_arb in paper mode with 30-day kill criterion; concurrently cut kalshi_llm_arbitrage's macro-release losers and 50%+ divergence bucket per post-audit findings.
**Backup tag:** `pre-kalshi-structure-arb-paper-deploy-20260518-<short_hash>` (retain ≥48h post-deploy)

**Files deployed:**

New files (back up as n/a — they don't exist on prod yet):
- `trading_corp/agents/strategies/kalshi_structure_arb.py` (NEW, ~550 lines) — deterministic multi-outcome event arb; no LLM in path; `PAPER_MODE_ONLY=True` + `LIVE_MODE_BOARD_APPROVED=False` class constants hard-gate `auto_execute` to False regardless of yaml
- `tests/test_kalshi_structure_arb.py` (NEW, 10+ tests) — includes hard-stop-constant tests asserting `auto_execute` stays False with any yaml config

Modified — SURGICAL patches only (do NOT full-file replace):
- `trading_corp/main.py` — added `_scheduled_kalshi_structure_arb_loop()` (line ~3570) + task instantiation + cancel wiring (~lines 1075–1088 and 1561–1563); import is inline at task-creation site
- `config/strategies.yaml` — two surgical blocks only:
  - `kalshi_llm_arbitrage:` block: `max_divergence_pct: 30.0` (was 50.0 or uncapped) + `ticker_prefix_blacklist: [KXUSPPI, KXUSCPI, KXAIRFARE, KXAAAGAS]` (was absent)
  - `kalshi_structure_arb:` block: entirely new stanza — `enabled: true`, `auto_execute: false`, thresholds, sizing, cadence, discovery, kill_criterion
  - **IC block (`robinhood_joint_iron_condor`) MUST remain untouched — parallel session owns it**
- `config/divisions.yaml` — surgical addition of `kalshi_structure_arb` division entry only
- `trading_corp/agents/strategies/kalshi_llm_arbitrage.py` — ~+50 lines: reads `max_divergence_pct` ceiling from yaml (already present in code as of local HEAD); reads `ticker_prefix_blacklist` list and skips matching tickers pre-LLM (avoids burning Anthropic tokens on known losers); emits `kalshi_llm_ticker_blacklisted` audit with `strategy` + `division` tags

Optional (analysis artifacts, not required for runtime):
- `scripts/backtest_kalshi_structure_arb.py` — read-only backtest tool; not part of prod runtime loop; ship or skip at deploy discretion
- `reports/kalshi_structure_arb_backtest_2026-05-17.md` + `_raw.json` / `_prod_raw.json` — analysis artifacts

**Features shipped (load-bearing for future "is X done?" checks):**
- **kalshi_structure_arb paper loop live.** Strategy polls Kalshi every 60s (15s rapid window for fresh events). When sum(implied_yes) ≥ 1.5 across K ≥ 3 sub-markets of the same event, fires a $1 NO buy on top-M=3 overpriced sub-markets. All orders land as `would_have_placed` audit rows (auto_execute=false enforced by both yaml and hard-stop class constants). Kill criterion committed: review 2026-06-16; kill if n_resolved ≥ 20 AND (win_rate < 0.55 OR gross_paper_pnl_usd ≤ 0).
- **Hard-stop constants block yaml-flip-to-live.** `KalshiStructureArbAgent.PAPER_MODE_ONLY = True` and `LIVE_MODE_BOARD_APPROVED = False` — flipping live requires a code change + Board memo, not just a yaml edit. Closes the CLAUDE.md § 5 sharp-edge gap where a yaml-only flip could enable live placement.
- **kalshi_llm_arbitrage max_divergence_pct ceiling at 30%.** The 50%+ divergence bucket (0/12 WR, -$0 net but 0 wins) is now silently filtered at the divergence-ceiling check. The 30-50% bucket (46% WR, -$22 PnL) is also filtered pending further evidence — the ceiling is set to 30.0.
- **kalshi_llm_arbitrage macro-release blacklist live.** KXUSPPI, KXUSCPI, KXAIRFARE, KXAAAGAS prefixes are skipped pre-LLM. These 36 trades had 0 wins and -$36 PnL in the post-cutoff audit. Emits `kalshi_llm_ticker_blacklisted` audit per skipped ticker for observability. Hot-reloadable via yaml after restart.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Hard-stop constants are in the strategy class, not in yaml.** Checking `auto_execute: false` in yaml is NOT sufficient to verify live is blocked — look for `PAPER_MODE_ONLY` and `LIVE_MODE_BOARD_APPROVED` in `kalshi_structure_arb.py`. The `enabled` property raises `AssertionError` if `LIVE_MODE_BOARD_APPROVED=False` and the process is in live mode. Removing these constants requires an explicit Board memo per CLAUDE.md § 4.
- **ticker_prefix_blacklist is pre-LLM.** The skip happens before any Anthropic API call in `kalshi_llm_arbitrage._run_cycle`. This means blacklisted tickers save the LLM token cost, not just the paper trade. The `n_skipped_blacklist` count lands in the cycle summary audit payload under `skipped_blacklist`.
- **strategies.yaml is shared with the Iron Condor session.** The `robinhood_joint_iron_condor` block (and any IC-related stanzas) must remain byte-for-byte unchanged. Patch is surgical — deploy as a targeted edit, not a full-file scp.
- **`_scheduled_kalshi_structure_arb_loop` is a top-level coroutine** (~line 3570 in main.py), consistent with the `_scheduled_kalshi_llm_arb_loop` pattern immediately above it. Import is inline at task-creation site to keep startup fast (strategy is heavy at import time due to regex compilation).
- **Backtest characterization recorded here (not in a re-run).** Original backtest (49 events, 147 bets, 140 unresolved, 1 win / 6 losses — of which 5/6 were the price-bucket regex bug) is non-actionable even after regex fix. Root reason: the backtest uses latest-observation pricing for both qualification AND entry, so qualifying events are biased to those whose mispricing persisted late and fills are post-correction. Re-running would tighten the estimate of the wrong quantity. The right signal comes from paper-mode first-observation vs fire-time vs resolution logging, which the strategy is built to produce. Scheduled June 1 backtest re-run is **cancelled** — this runbook entry is the durable record.
- **What's NOT in this deploy:** `kalshi_crypto_arb min_horizon_hours: 4` change (waiting on resolution backfill for 69 open positions); Sci/Tech residual category leak for KX*100W tickers (small change, deferred).

**Decisions recorded:**
- **Decision NOT to re-run the regex-fixed backtest** — see "Notable code changes" above. The characterization of why the original backtest is non-actionable is the durable record.
- **Kill criterion is human-process, not code-enforced.** Review date 2026-06-16. `config/strategies.yaml kalshi_structure_arb.kill_criterion` is the machine-readable version; the Board is the enforcement path. If n_resolved < 20 at review date, extend 30 more days.

**Verification (to complete post-deploy):**
- Pre-deploy md5 on modified files (main.py, strategies.yaml, divisions.yaml, kalshi_llm_arbitrage.py) — capture backup tag
- `python -c "from trading_corp.agents.strategies.kalshi_structure_arb import KalshiStructureArbAgent; a = KalshiStructureArbAgent.__new__(KalshiStructureArbAgent); print('PAPER_MODE_ONLY:', a.PAPER_MODE_ONLY); print('LIVE_MODE_BOARD_APPROVED:', a.LIVE_MODE_BOARD_APPROVED)"` — must print `True` / `False`
- Import smoke: `from trading_corp.agents.strategies.kalshi_structure_arb import KalshiStructureArbAgent; print('ok')`
- Service restart + PID change confirmation
- `grep -c "kalshi_structure_arb_loop\|kalshi_structure_arb_task" /home/azureuser/trading_corp/trading_corp/main.py` — expect ≥ 4 hits
- Audit log: within 5 minutes of restart, look for `kalshi_structure_arb_poll_start` event. If events are `kalshi_structure_arb_no_events` that's also fine — confirms the loop is cycling.
- For kalshi_llm_arbitrage: confirm `max_divergence_pct: 30.0` and `ticker_prefix_blacklist` present in loaded yaml (check `grep max_divergence_pct config/strategies.yaml` on prod)

**Inert / dormant on current traffic:**
- `kalshi_structure_arb` will cycle every 60s but all orders land as `would_have_placed` audit rows until `LIVE_MODE_BOARD_APPROVED` is flipped to True (code change) AND `auto_execute: true` set in yaml AND `--live` mode — three gates.
- `scripts/backtest_kalshi_structure_arb.py` (if shipped) is a CLI script only; it has no runtime entrypoint and is never invoked by main.py.
- kalshi_llm_arbitrage changes take effect immediately after restart. No new dormant code.

**Rollback recipe:**
```bash
az vm run-command invoke -n tc-prod-vm -g rg-shared-prod --command-id RunShellScript --scripts '
TAG=pre-kalshi-structure-arb-paper-deploy-20260518-<short_hash>
BASE=/home/azureuser/trading_corp
# Remove new strategy file (no backup needed — it is net-new)
rm -f $BASE/trading_corp/agents/strategies/kalshi_structure_arb.py
# Restore modified files
mv $BASE/trading_corp/main.py.$TAG $BASE/trading_corp/main.py
mv $BASE/config/strategies.yaml.$TAG $BASE/config/strategies.yaml
mv $BASE/config/divisions.yaml.$TAG $BASE/config/divisions.yaml
mv $BASE/trading_corp/agents/strategies/kalshi_llm_arbitrage.py.$TAG \
   $BASE/trading_corp/agents/strategies/kalshi_llm_arbitrage.py
sudo systemctl restart trading-corp
'
```
Note: `tests/test_kalshi_structure_arb.py` can be left in place after rollback — it has no runtime effect.
