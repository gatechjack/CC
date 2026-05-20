Here are the 3 kick-off prompts. Open a new terminal for each.

---

## Session 1 — BitUnix Confluence Gate v1.1 Backtest

**Resume the original (history intact):**
```
claude --resume 0c8afb73-8197-4ed4-ac44-0cc30dd0838a
```
**OR start a fresh session and paste this prompt** (recommended — the background bash job that was running is dead):

> Resuming the BitUnix Confluence Gate v1.1 backtest session that died from a BSOD at 2026-05-17 ~22:25 UTC, mid-Block-A debugging.
>
> **Where we were:** Building v3 Bybit-hybrid backtest of the gate v1.1 (post-CVD-cumulative-slope-fix + EMA-F1-all-three-slopes-fix, both in `trading_corp/agents/strategies/bitunix_confluence_gate.py`). v1.1 on Coinbase 1m produced PF=2.63 / WR=54.8% / n=31 over 2026-04-30→05-16. The v3 run repeats with Bybit BTCUSDT.P 3m+15m (from `data/btc_scalping.db`) + cached Bitunix native 5m + prod alerts pulled via `az vm run-command` in 6h slices.
>
> **What just blew up:** First Block A run produced 0 trades / 1,299 SKIPs out of 1,306 alerts. Root cause: prod alerts carry `interval` field (`'3'`, `'5'`) not `tf` (`'3m'`, `'5m'`). I fixed `scripts/merge_prod_alert_slices.py` to convert `interval`→`tf` AND I fixed `tmp/pull_prod_alerts.sh` to pull `interval`. Then I `rm -f tmp/prod_alerts/slice_*.json` and kicked `bash tmp/pull_prod_alerts.sh 2026-04-30 2026-05-18` as a background job (bkv43katu). That bash job is DEAD now (BSOD killed it). Need to verify nothing partially landed and re-run.
>
> **First action:** Check `tmp/prod_alerts/` for stale partial slices and kill any orphan `az` processes. Then re-kick `bash tmp/pull_prod_alerts.sh 2026-04-30 2026-05-18` (~25 min, 72 slices). When done, run the merger then Block A:
> ```
> python scripts/merge_prod_alert_slices.py --slice-dir tmp/prod_alerts --start 2026-04-30 --end 2026-05-18
> python scripts/backtest_bitunix_confluence.py --start 2026-04-30 --end 2026-05-16 --starting-equity 10000 --gate five_factor --bar-source bybit_hybrid --alert-source data/historical_alerts/cache_alerts_prod_filtered_20260430_20260518.json
> ```
> Then populate `reports/gate_backtest_2026-05-17_v3_bybit_hybrid.md` Block A table and compare vs Coinbase baseline (PF=2.63, n=31).
>
> **Files I own this session — don't conflict:** `scripts/backtest_bitunix_confluence.py`, `scripts/fetch_bitunix_5m_history.py`, `scripts/fetch_prod_alerts_az.py`, `scripts/merge_prod_alert_slices.py`, `trading_corp/agents/strategies/bitunix_confluence_gate.py`, `tests/test_bitunix_*.py`, `tests/test_backtest_bitunix_confluence_five_factor.py`, `reports/gate_backtest_2026-05-17*.md`, `docs/memos/2026-05-17_gate_v1.1_state_of_knowledge.md`, `tmp/`.
>
> **Open question:** does v1.1 hold on Bybit bars, or was the +21.23R Coinbase result a venue artifact? Block A answers this. Block C (Apr→May synth OOS) still pending after Block A.

---

## Session 2 — Kalshi Structure Arb

**This session actually wrapped cleanly before the BSOD.** Commits `652b0c3`, `b64803c`, `6b1319b` all landed on `main`. The next-session prompt was already written.

**Just open a fresh session and paste this** (no `--resume` needed):

> Resuming from 2026-05-17 22:30 UTC wrap. Read in order:
>
> 1. `BACKLOG.md` — EOS snapshot at top (2026-05-17 22:30 UTC; supersedes 17:45). Captures the promote/demote UX fix deploys (v1 20:36 UTC + v2 21:25 UTC) and the Kalshi strategy review findings.
> 2. `runbooks/deploy_log.md` — top entries are last session's two deploys.
> 3. `runbooks/session_start_2026_05_18.md` — full pickup brief. **PRIORITY 1 is an embedded design prompt for the new `kalshi_structure_arb` division** (deterministic structural-arb opportunity surfaced by the KXCHINAANNOUNCE audit). Backtester approval required before deploy per CLAUDE.md §4 + PROJECT_CONTEXT.md §11.
>
> **Untracked Kalshi Structure Arb artifacts already on disk** (authored last session, NOT committed pending Board approval):
> - `scripts/backtest_kalshi_structure_arb.py`
> - `trading_corp/agents/strategies/kalshi_structure_arb.py`
> - `tests/test_kalshi_structure_arb.py`
> - `reports/kalshi_structure_arb_backtest_2026-05-17.md` (+ raw/prod_raw json)
>
> **First action:** read `runbooks/session_start_2026_05_18.md` and the `kalshi_strategy_analysis` + `kalshi_structure_arb_proposal` memory files. Honest assessment first — sample sizes are still small. If the Board doesn't want to start with the structure-arb backtest review, the cheap `kalshi_llm_arbitrage` config cuts (US-release blacklist, `max_divergence_pct: 30`) are next-best.
>
> **Don't touch — owned by parallel BitUnix session:** `.claude/settings.json`, `config/macro_calendar.yaml`, `scripts/backtest_bitunix_*.py`, `scripts/backtest_btc_accumulator.py`, `trading_corp/agents/divisions/bitunix_futures_observer.py`, `trading_corp/data/bitunix_price_context.py`, `trading_corp/brokers/*`, `trading_corp/main.py`, `trading_corp/agents/data_exec.py`, `tests/test_boot_smoke.py`, `tests/test_backtest_bitunix_*`, `tmp/`.
>
> **Don't touch — owned by parallel Robinhood Joint session:** anything under `trading_corp/agents/divisions/robinhood_joint*`, `trading_corp/agents/strategies/robinhood_joint*`, `trading_corp/agents/strategies/_ic_*`, `trading_corp/agents/ic_*`, `trading_corp/comms/{pending_combo_registry,telegram_batcher}.py`, `trading_corp/web/combo_approval_view.py`, `trading_corp/web/templates/{iron_condor_live,approval_combo_detail}*.html`, `trading_corp/web/templates/partials/iron_condor_*`, `trading_corp/scripts/ic_*.py`, `trading_corp/utils/iv.py`, `trading_corp/data/ex_dividend_calendar.py`, `config/ex_dividend_calendar.yaml`, `planning/broker_multi_leg_interface_design.md`, `tests/test_ic_*`, `tests/test_iron_condor_*`, `tests/test_robinhood_joint_*`, `tests/test_robinhood_multi_leg*`, `tests/test_combo_approval*`, `tests/test_ex_dividend_calendar*`, `tests/test_iv_rank*`, `tests/test_paper_multi_leg*`, `tests/test_paper_run_tooling*`, `tests/test_place_combo*`, `tests/test_telegram_batcher*`.
>
> SSH may be blocked; pivot to `az vm run-command`. Windows CRLF; `tr -d '\r'` before deploy. Any patch touching `routes.py` needs CRLF normalize.

---

## Session 3 — Robinhood Joint Iron Condor

**Heads up:** the original session (`e83cfd5b`) was only 21 minutes long and BSOD hit **9 seconds after `/plan` mode was activated** — no plan content was ever produced. The 16 untracked iron-condor files on disk were authored by **other parallel sessions earlier today** (mtimes 19:25–22:57 UTC), which this session never saw. So resuming the original session jsonl is nearly useless.

**Start a fresh session and paste this prompt:**

> Picking up the Robinhood Joint Iron Condor workstream. A BSOD killed the prior planning session (e83cfd5b) 9 seconds into `/plan` mode at 2026-05-17 20:51 UTC, so there is no plan to resume from. But two parallel sessions earlier today authored a large amount of iron-condor scaffolding that was never committed — start by inventorying that before redoing any design.
>
> **Untracked iron-condor files already on disk (NEVER committed, authored by parallel sessions today):**
> - `planning/broker_multi_leg_interface_design.md` (26KB, 19:25 UTC — design doc the other sessions wrote against; **read this first**)
> - `trading_corp/agents/divisions/robinhood_joint.py` (6.1KB)
> - `trading_corp/agents/strategies/robinhood_joint_iron_condor.py` (72KB — primary strategy)
> - `trading_corp/agents/strategies/_ic_orchestration.py` (18KB)
> - `trading_corp/agents/strategies/_ta_helpers.py` (6.5KB)
> - `trading_corp/agents/ic_live_view.py` (30KB)
> - `trading_corp/agents/ic_telemetry.py` (17KB)
> - `trading_corp/comms/pending_combo_registry.py` (6.8KB)
> - `trading_corp/comms/telegram_batcher.py` (5.0KB)
> - `trading_corp/web/combo_approval_view.py` (5.7KB)
> - `trading_corp/web/templates/{approval_combo_detail,iron_condor_live}.html`
> - `trading_corp/web/templates/partials/iron_condor_{live,static}_sections.html`
> - `trading_corp/scripts/ic_*.py` (daily_digest, paper_run_readiness, telemetry_cli)
> - `trading_corp/utils/iv.py`
> - `trading_corp/data/ex_dividend_calendar.py` + `config/ex_dividend_calendar.yaml`
> - Tests: `test_robinhood_joint_division.py`, `test_robinhood_multi_leg.py`, `test_iron_condor_*.py`, `test_ic_*.py`, `test_combo_approval.py`, `test_ex_dividend_calendar.py`, `test_iv_rank.py`, `test_paper_multi_leg.py`, `test_paper_run_tooling.py`, `test_place_combo.py`, `test_telegram_batcher.py`
>
> **Key division facts from CLAUDE.md / config:**
> - `config/divisions.yaml:49-56` — `robinhood_joint` slug, `account_filter: joint`, `intent: aggressive`, no `strategy:` key.
> - `brokers/robinhood.py:369-371` — crypto excluded from joint snapshot (anti-triple-counting). Iron condor on equity/index options only.
> - New strategy must boot `auto_execute: false`, route through `RiskAgent.evaluate()`, use `agent_state` for new latches, earn auto-exec only after Backtester approval.
>
> **First action:**
> 1. `git status` and `wc -l` on each untracked iron-condor file to see what's actually implemented.
> 2. Read `planning/broker_multi_leg_interface_design.md` to recover architectural intent.
> 3. Read `robinhood_joint_iron_condor.py` (72KB) + `robinhood_joint.py` to assess completeness.
> 4. Run pytest on the new tests to see what passes.
> 5. Report back: what's done, what's stubbed, what's broken. **Do NOT redo design work that's already on disk.**
>
> **Don't touch — owned by parallel BitUnix session:** `.claude/settings.json`, `config/macro_calendar.yaml`, `scripts/backtest_bitunix_*.py`, `scripts/backtest_btc_accumulator.py`, `scripts/fetch_bitunix_*.py`, `scripts/fetch_prod_alerts_az.py`, `scripts/merge_prod_alert_slices.py`, `scripts/analyze_5f_factor_contribution.py`, `trading_corp/agents/strategies/bitunix_confluence_gate.py`, `trading_corp/agents/divisions/bitunix_futures_observer.py`, `trading_corp/data/bitunix_price_context.py`, `trading_corp/brokers/*`, `trading_corp/main.py`, `trading_corp/agents/data_exec.py`, `tests/test_boot_smoke.py`, `tests/test_bitunix_*`, `tests/test_backtest_bitunix_*`, `reports/gate_backtest_*`, `docs/memos/2026-05-17_gate_v1.1_*`, `tmp/`.
>
> **Don't touch — owned by parallel Kalshi Structure Arb session:** `scripts/backtest_kalshi_structure_arb.py`, `trading_corp/agents/strategies/kalshi_structure_arb.py`, `tests/test_kalshi_structure_arb.py`, `reports/kalshi_structure_arb_*`, `BACKLOG.md`, `runbooks/session_start_2026_05_18.md`, `runbooks/deploy_log.md`, `trading_corp/web/routes.py` (Polymarket promote/demote work).

---

**Note on three sessions running in parallel:** the "don't touch" lists above are critical — the BitUnix and Kalshi-arb sessions modify many overlapping files (`web/routes.py`, `BACKLOG.md`, etc.) and the parallel-session memory rule (`feedback_parallel_sessions_stop_and_discuss.md`) applies. If two sessions need the same file, stop and surface the conflict instead of silently working around it.