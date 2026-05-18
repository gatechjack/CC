# Next-session pickup prompt (2026-05-19)

*Written 2026-05-18 07:00 UTC at end of the BitUnix v3-hybrid backtest session. Supersedes `session_start_2026_05_18.md` (whose kalshi_structure_arb prompt is still valid and unchanged — see § Priority candidates below).*

---

Paste this into a fresh Claude Code session in `C:/Users/AA Incorporado/cc`:

---

Resuming from 2026-05-18 07:00 UTC wrap. One work thread last session: BitUnix Confluence Gate v1.1 v3 Bybit-hybrid backtest. Picked up after the third BSOD mid-Block-A debug; finished all three blocks and committed the report. Negative verdict for v1.1 on Bybit-fidelity bars. Read the EOS snapshot at the top of `BACKLOG.md` first.

## What landed yesterday — gate v1.1 v3 verdict

**One commit, no prod deploys, no division/strategy code changes.**

```
e565bec — backtest: gate v1.1 v3 Bybit-hybrid report — Blocks A/B/C verdict
```

### Headline finding

Same v1.1 gate, same 1,306 prod alerts, **Bybit 3m+15m bars instead of Coinbase 1m → PF 2.63 → 1.14, WR 54.8% → 31.2%, fire count 31 → 32**. v1.1 on a BitUnix-proximate venue fails 3 of 4 Phase C acceptance thresholds (PF, WR, fire-rate; only n≥20 clears).

Block B isolates the cause: synth-17d WR=31.1% matches prod-17d WR=31.2% **exactly** on the same Bybit bars. Same gate, same bar source, two completely different alert sources, identical outcome → the cause is bar-source + trade-resolution, not alert-source. Per-factor pass rates are stable across windows (max Δ +3.6pp on vwap; ±5pp diagnostic flag does NOT fire).

Block C: paper cutover is now **the only path to discriminate** between the "bar-fidelity-artifact" and "v1.1 over-fit to Coinbase" hypotheses. Both should be named explicitly in the paper-cutover decision memo before paper data starts arriving, so the 60-day shadow is read on the correct prior.

### Three load-bearing unknowns (named in Block C)

1. **Bar-resolution (3m vs 1m).** Testable with a Bybit 1m pull. Likely dominant cause per the Block B hypothesis.
2. **Real Bybit CVD vs the OHLCV-proxy tick-rule fallback.** Used 100% of evaluations.
3. **Regime-fragility.** Synth-31d (truly-OOS) PF=0.74 hints v1.1 may degrade further outside the hostile-cooperative 17d window.

### Process improvement that landed (not committed; lives in `tmp/`)

`tmp/pull_prod_alerts.sh` cache-skip regex was looking for `"stdout` (with leading quote) but az JSON contains literal `[stdout]` — every BSOD recovery was re-pulling all 72 slices for ~25min. Fixed to grep `\[stdout\]` + accept ≤300-byte empty-window slices as cached. Future BSOD recovery now uses on-disk progress (~2 min to fill the gap).

## Read first

1. `reports/gate_backtest_2026-05-17_v3_bybit_hybrid.md` — full report (Blocks A/B/C + caveats + 3 load-bearing unknowns + artifact paths). This is the canonical record; the memo below references it heavily.
2. `docs/memos/2026-05-17_gate_v1.1_state_of_knowledge.md` — § 4 + § 11 populated with v3 results; § 8 decision space updated to name the two hypotheses paper data must discriminate.
3. `BACKLOG.md` — EOS snapshot at top (2026-05-18 07:00 UTC; supersedes 2026-05-17 22:30).
4. Memory (auto-loaded):
   - `trading_corp_bitunix_vision.md` (updated — v3 verdict + paper-cutover framing shift)
   - `trading_corp_bitunix_strategy_gaps.md` (unchanged this session — still accurate)
   - `feedback_pa_gate_well_calibrated.md` (still relevant)
   - `feedback_bitunix_no_hot_reload.md` (still relevant)
5. `runbooks/deploy_log.md` — unchanged this session. Last entries are 2026-05-17 polymarket promote/demote v1/v2.

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 1 (decision branch — User picks A or B)
═══════════════════════════════════════════════════════════════════════════

The Block C result raises a real question and the next session should decide between two paths. Don't dive into code until the User has chosen.

### Branch A — Disambiguate the bar-fidelity hypothesis now

**Goal:** Reduce one of the three load-bearing unknowns before paper cutover. Specifically: pull Bybit 1m bars for the 17d window, re-run Block A with 1m trade-resolution, see if WR recovers.

**Why this is high-EV:** If the 1m result lifts WR materially (say, back into the 45-55% range), that's strong evidence the verdict-collapse was bar-resolution-limited and v1.1's underlying edge is real. If it doesn't, the over-fit hypothesis becomes the leading prior, and the paper-cutover memo can flag this earlier.

**Estimated cost:** ~1-2h.
- Bybit public REST kline endpoint: 1000-bar pages, ~25 paginated calls for ~24,500 1m bars over 17d.
- `scripts/fetch_bitunix_5m_history.py` is a reasonable template; this would be `scripts/fetch_bybit_1m_history.py`.
- DB schema in `data/btc_scalping.db`: add a `bars_1m` table with the same shape as `bars_3m`.
- Add `--bybit-tf 1m` (or similar) to `backtest_bitunix_confluence.py`'s `_load_bybit_hybrid_inputs`, swap trade-resolution source.
- Re-run the Block A command, write a single-line addendum to the report.

**Hard constraints:**
- Don't touch the existing `bars_3m`/`bars_15m` ingestion or schemas — additive only.
- Backtest harness stays read-only on the DB.
- No changes to v1.1 gate code.

### Branch B — Park v1.1 pending paper data; pick up kalshi_structure_arb

**Goal:** Treat the v1.1 verdict as "decision parked — paper data is the gate" and unblock the next-EV work item. kalshi_structure_arb was Priority 1 from the 2026-05-17 22:30 wrap and is still pending Board approval on the backtest.

**Why this might be the right call:** The Block C verdict doesn't actually need disambiguating before paper cutover — the cutover decision memo can name both hypotheses (per the updated § 8 of the state-of-knowledge memo) and let the 60-day shadow data decide. Branch A is informative-but-not-blocking.

**The full kalshi_structure_arb prompt is unchanged from the prior session — see `runbooks/session_start_2026_05_18.md` lines 56-267.** It is reproduced there in full and still accurate. Backtester approval required before any code lands in production per CLAUDE.md § 4.

### Branch decision — if the User isn't sure

Ask one question only: "Branch A (Bybit 1m pull, disambiguate v1.1) or Branch B (park v1.1, pick up kalshi_structure_arb backtest)?" Don't propose both at once.

═══════════════════════════════════════════════════════════════════════════

## Other pickup candidates (after Priority 1, ordered by signal/effort)

1. **Standing kalshi cuts** — unchanged from 2026-05-17 22:30 wrap. US-release ticker blacklist, max_divergence_pct cap, residual Sci/Tech leak, min_horizon_hours: 4 for crypto-arb. All small enough to bundle.

2. **Investigate the BSOD pattern.** 3 BSODs in 3 sessions during the BitUnix backtest work — possibly correlated with sustained az vm run-command load or with sqlite3 large-DB reads. Not in scope for this session, but worth noting if a 4th happens. Workstream-impacting if it continues.

3. **kalshi_llm_arbitrage 5/14→5/15 activity collapse** (~30 min journalctl/audit archaeology). 155 → 8 trades/day drop. Cause unknown.

4. **PMCC audit** (perennial — needs scope-narrowing).

5. **Sun 2026-05-24 13:02:51 UTC:** watch the first Polymarket weekly cron fire.

## Things to NOT do without explicit approval

(Standard list, plus this session's additions:)

- **Don't flip `bitunix_futures.auto_execute: false → true`** even if a single arm of additional backtesting shifts a number favorably. The verdict-collapse on Bybit-hybrid bars is real and reproducible on the data we have. Paper data is the gate now.
- **Don't paper over the negative finding** in subsequent memos or decision write-ups. CLAUDE.md and PROJECT_CONTEXT.md hard-rule honesty-over-narrative; the report's TL;DR and Block C are deliberately framed for that.
- **Don't flip BitUnix `htf_gate.mode: enforce → shadow` or `trade_plan.enabled: true → false`.** Standard BitUnix do-not-touch list.
- **Don't change the v2 architecture rule** that promote/demote-endpoints-don't-touch-watch_only_whales — still load-bearing per the 2026-05-17 wrap.
- **Don't `systemctl restart trading-corp` blindly.** No prod changes this session, but the polymarket Cloudflare-retry resilience and PCT changes from the prior wrap are still dormant until the next natural restart.
- **Don't deploy the proposed `kalshi_structure_arb`** without running the backtest and getting Board sign-off — same constraint as the prior wrap.
- **Don't deploy via `patch -p1`** over a file that touches `routes.py` without prepending the CRLF-normalize step (per `feedback_crlf_routes_py_deploy.md`).
- **Don't delete the backup tags** `pre-promote-demote-uxfix-20260518-*` until ≥48h post-deploy.

## Environment notes

- Local Python: `C:\Users\AA Incorporado\AppData\Local\Python\bin\python.exe` (bare `python` is the MS Store stub).
- SSH usually blocked from non-home IPs; pivot to `az vm run-command create --script @file` per `feedback_az_run_command_when_ssh_blocked.md`.
- Windows checkout is CRLF; deploy scripts MUST `tr -d '\r'` before `az vm run-command create`.
- `.py` changes under `trading_corp/` need `systemctl restart trading-corp` to take effect in the live service (uvicorn runs without `--reload` in prod).
- Prod still on the 2026-05-17 21:25 UTC code. Untouched by this session.

## Artifacts produced this session (already on disk locally)

- `reports/gate_backtest_2026-05-17_v3_bybit_hybrid.md` (committed)
- `data/historical_alerts/cache_alerts_prod_filtered_20260430_20260518.json` (1,717 alerts, untracked)
- `data/backtest_runs/bitunix_20260518T042506_five_factor/` (Block A prod-17d, untracked)
- `data/backtest_runs/bitunix_20260518T103210_synth_17d/` (Block B comparator, untracked)
- `data/backtest_runs/bitunix_20260518T103208_synth_31d/` (Block B truly-OOS, untracked)
- `tmp/pull_prod_alerts.sh` (cache-skip fix, gitignored)
- `tmp/prod_alerts/slice_*.json` × 72 (alert slices, gitignored)

Honest assessment first — read the EOS snapshot + the v3 report's Block C before proposing any next-step action. The "what to do about v1.1" decision is judgment-loaded; don't pre-commit to a branch without surfacing the trade-off.
