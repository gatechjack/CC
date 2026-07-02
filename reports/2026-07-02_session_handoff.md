# Session handoff — 2026-07-02

## What shipped / happened this session

### 1. bitunix_futures SL-trail `positionId absent` — DIAGNOSED + FIXED + DEPLOYED+VERIFIED LIVE
- **Symptom:** recurring `BitUnix modify_position_sl: positionId absent for BTC/USDT.P` (05:10/06:49/08:06 UTC).
- **Root cause:** `move_bracket_sls` (reconciler) mis-reads a **full close** as a TP-fill (`pos_qty.get(key,0.0)`
  → 0.0), driving a positionId-less no-op. **Cosmetic post-close artifact, $0 risk, long-standing since Jun 19.**
- **Fix (caller-only):** Fix A skip fully-closed/absent positions before the TP-fill test; Fix B reduced-severity
  INFO breadcrumb. `modify_position_sl` guard untouched. Regression test added (fails pre-fix, passes post-fix).
- **Deployed+verified LIVE 2026-07-02 ~16:23 UTC**, engine **PID 60341**, all divisions `paper=False`,
  restart-resume clean, flat. Branch `futures-sltrail-diag-2026-07-02` PUSHED, **UNMERGED**. Full detail:
  `deploy_log.md` (2026-07-02) + `reports/2026-07-02_futures_sltrail_positionid_absent_diagnosis.md`.
- ⚠ **Prod reconciler now = commit `701a9fb` (md5 `25833c1e…`); main/origin reconciler = pre-fix.** A future
  main reconcile MUST carry this hunk. Rollback = `.bak-pre-sltrailfix-2026-07-02` + restart.
- **Validation pending:** the new post-close INFO breadcrumb (vs old WARNING) confirms on the NEXT futures
  bracket close.

### 2. Wick-test scalp research (v1–v6) — RETIRED, no edge
- Branch `wick-test-spike-2026-07-02` (PUSHED, unmerged): `spike_wick_test/` (harnesses + PRE_REGISTRATION_v*
  + run logs) and `reports/2026-07-02_wick_test_v*.md`.
- **Six constructions, GROSS, k=1 causal, 3m (47–81d) then 15m (~230d):** no tradeable, net-positive,
  OOS-stable, both-sides edge. v5 runner-capture (BC-stop + strength) gave the only non-beta signal (long-only,
  +0.06R gross, sub-fee); **v6 killed it** on 15m TF-transfer + fees + IS/OOS (0/24 beat the net-null).
- **RETIRED — do not re-run** (see `BACKLOG.md` RESEARCH LEDGER). Learnings preserved there + in memory.

## Prod state (as of session end)
- Engine **PID 60341**, active, `execution_mode=live`, boot 2026-07-02 16:23:27 UTC.
- Live divisions all `paper=False`: `bitunix_sfp`, `bitunix_futures`, `robinhood_pead`, `kalshi_copy_trading`.
- **bitunix_sfp:** bidirectional regime-aware Mode-B, all 4 coins armed live (BTC/ETH/SOL/XRP), regime = up
  (long-only posture), flat, reconciler clean. Maiden SHORT still untested (needs down/range regime). Watch the
  first-live A/B on first fill per side (esp. the maiden short — slPrice ABOVE entry, venue-unexercised).
- Only non-blocking errors at boot: pre-existing fidelity/Playwright paper-fallback (chronic, unrelated).

## Git / environment sync
- **origin/main UNTOUCHED** (`0590e5d`) — nothing merged to main this session (CRLF merge-debt intact).
- Branches on origin (all UNMERGED): `futures-sltrail-diag-2026-07-02` (`5ce1d8e`, LIVE on prod),
  `wick-test-spike-2026-07-02` (`f05b55d`, 18 commits, research only), `session-wrap-2026-07-02` (this handoff).
- All worktrees clean. Reports copied to `Desktop\bitunix_reports\`.

## Open / next-session
1. **SFP maiden-short first-live A/B** (highest watch): on the first SHORT fill verify TP rests w/ real /tpsl/
   id, OCO closes + B1 auto-cancels no orphan, auto-book 2R, research-log correct regime stamp. HOT rollback =
   `side: regime → long` in YAML.
2. **Futures SL-trail fix live-validation:** confirm the post-close INFO breadcrumb replaces the WARNING on the
   next futures bracket close.
3. **Main reconcile (merge-debt):** prod carries CRLF hybrids (main.py/strategies.yaml) AND now the SL-trail
   reconciler hunk; a rebase-onto-current-prod is still owed before any main merge.
4. Backlog: futures pre-TP1 trail (P3, own backtest arc); SFP SOL/XRP tuning; the kalshi dashboard v1 gaps.
