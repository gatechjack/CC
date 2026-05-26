# Next-session prompt — post-C-7-draft pickup

Resuming after the 2026-05-26 ~23:30 UTC session that drafted the C-7 webhook secret-scrub fix end-to-end, verified it against a real persisted SQLite row + a read-only prod dry-run, and banked it as 2 commits on a local-only branch. Also checked the bitunix tripwire (BELOW $90, no action) and filed the C-7 deploy sequence + regex boundary + a P3 fixture-gap into BACKLOG.md.

## State on prod

**`origin/main` head:** `3a5946f`. The only commit this session that landed on main + got pushed was the BACKLOG-only entry filing C-7 draft state + the P3. Working tree clean modulo 3 untracked parallel-session files NOT mine (`docs/Deployment notes.txt`, `runbooks/tastytrade_oauth_rotation.md`, `scripts/check_tt_token_scope.py`).

**Latent on a local branch (the load-bearing pickup):**
- `c7-webhook-secret-scrub` — **LOCAL ONLY, never pushed**. 2 commits on top of parallel-session base `b64cdc5`:
  - **`d7ce0df`** — webhooks: scrub secret-bearing JSON fields from rejected-webhook audit (C-7)
  - **`5f7a198`** — scripts: one-shot backfill to scrub secrets from existing webhook_rejected audit rows (C-7 Phase 2)

**Live on prod (unchanged from prior session):**
- Trading-corp.service PID 1462117, ActiveEnter 2026-05-26 03:30:19 UTC. No restart this session.
- Analyze-Whale dashboard endpoint live; analyze-whale CLI on prod's disk.
- Watchlist slot: 53 rows from 2026-05-26 00:44 UTC (PRE-Sunday-fire under broken PnL math; promotion still PAUSED).

**Operator-state on prod:**
- **8 webhook_rejected rows** in `audit_event`, of which **5 still carry plaintext JSON-shaped secret values** in `payload_json.raw_body_snippet`. Confirmed via dry-run (read-only). Backfill NOT run — that's part of the C-7 deploy session.
- `metrics_epoch`: 2026-05-23T15:30:15 (unchanged).
- All other slots unchanged from prior session's EOS.

## Pickup default if no operator direction

Two candidate next pickups depending on date:

### A) If session starts BEFORE Sun 2026-05-31 ~13:00 UTC

The Sunday weekly seed fire is the load-bearing next event for pm-watchlist. If pre-Sunday, the most useful pickups are:

1. **C-7 deploy session** (if operator authorizes a short prod-touch window). Sequence below in "C-7 deploy sequence."
2. **Tastytrade rotation runbook** (P1, untouched). The parallel session left `runbooks/tastytrade_oauth_rotation.md` as untracked WIP — investigate whether that's a stub or substantive, then decide whether to author or close.
3. **Other backlog items** (`bitunix_atr_snapshot` observability audit kind, jinja `is not none` cosmetic fix, the 43 deferred package bumps).

### B) If session starts AT or AFTER Sun 2026-05-31 ~13:00 UTC

Immediately run the 6-criterion Sunday verification gate from `runbooks/session_start_2026_05_26_post_analyze_whale_deploy.md` against the new `watch_only_whales` slot. Then C-7 deploy (still gated on operator scheduling).

## C-7 deploy sequence (load-bearing ordering)

Full detail in `BACKLOG.md` "P0 — C-7 webhook secret-scrub" entry. Summary:

1. **Push `c7-webhook-secret-scrub`** to origin. Currently local-only.
2. **Pre-deploy re-verification** (re-run the harnesses; they're re-runnable):
   - `.\scripts\run_capped.ps1 python tmp\verify_c7_real_audit_row.py` — confirms scrub still works end-to-end.
   - `az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm --command-id RunShellScript --scripts @tmp/c7_prod_dryrun_inline.sh` — confirms current prod row counts.
3. **Deploy `d7ce0df`** to prod. File transfer: `trading_corp/web/webhooks.py`. Tests bundled but not deployed. CRLF-vs-LF: prod webhooks.py uses LF per the previous deploys; use `cp` not `sed-in-place` for new content of this size. Run import-graph-audit (memory `[[deploy-import-graph-audit]]`) — verified no new imports added beyond `import re` (stdlib).
4. **Restart trading-corp.service.** ~5 min full strategy pause. Healthz green check.
5. **Run backfill once** on prod: `python scripts/scrub_webhook_rejected_secrets.py --db /home/azureuser/trading_corp/data/trading_corp.db` (no `--dry-run`). WAL-safe online — readers don't block. Expected output: `rows_changed: 5`. Re-run with the same command immediately to confirm idempotency (expect `rows_changed: 0, rows_already_clean: 8`).
6. **Verify post-backfill on prod**: re-run the dry-run inline; expect `rows_changed: 0`. The 5 historical leaked rows are now clean.
7. **PROCEED to C-1** secret rotation in a planned trading-pause window.

**Why the order is load-bearing:**
- C-1 before step 3: new rejections persist the NEW rotated secret in plaintext.
- C-1 before step 5: historical rows carrying the OLD secret survive the rotation; rotation no longer authenticates the secret but the secret is still in audit history.

## Hard constraints (carry forward)

- **`c7-webhook-secret-scrub` is local-only** until deploy session. Push is part of the deploy sequence, not the open.
- **Cherry-pick recovery available** if the parallel-session ancestors are an issue: `git cherry-pick d7ce0df 5f7a198` onto a fresh branch off `origin/main`. Currently `main` is at `3a5946f`.
- **Promotion stays PAUSED** until the Sunday gate passes (unchanged from prior session).
- **CLAUDE.md § 4 applies** — TV webhook path requires explicit human approval BEFORE deploy. Drafted code is fine; deploy requires approval.
- **Bitunix tripwire stays in waiting**: no action until ATR(14, 3m) sustains ≥ $90 for a 4h window OR the 2026-06-19 paper-clock midpoint passes.
- **`[[deploy-import-graph-audit]]` mechanical gate** before every prod file transfer.
- **`scripts\run_capped.ps1` wrap** for any Python touching `trading_corp/` or `tests/`.

## Files to read first if you need full context

1. `BACKLOG.md` top EOS snapshot (2026-05-26 ~23:30 UTC) — full session arc.
2. `BACKLOG.md` "P0 — C-7 webhook secret-scrub" entry — branch state, deploy sequence, regex boundary, verification status.
3. `reports/2026-05-21_security_review.md` § C-7 (lines 572-622) — original spec.
4. `tmp/verify_c7_real_audit_row.py` — re-runnable real-audit-row verification harness.
5. `tmp/c7_prod_dryrun_inline.sh` — re-runnable read-only prod dry-run script.
6. Memory: `[[project-c7-draft-pending-deploy]]` + `[[feedback-parallel-session-branch-collision]]` + `[[reference-real-audit-row-raw-sqlite3]]` + the existing security-tracks memory entries.

## Discipline standard (carry forward)

- Delegate mechanical work to Sonnet sub-agents; reserve Opus for judgment. (This session's C-7 fix + backfill were both Sonnet — clean.)
- Stop-and-report at forks rather than auto-resolving.
- Surface anomalies with diagnostic detail.
- Don't expand scope mid-task.
- Tighter commits than feels normal: commit artifacts as you go.
- NO PUSH without explicit go-ahead.
- Verify premises against ground truth (live data, not last-snapshot).
- Real-audit-row > TestClient + LoggerAgent — when the question is "did the row REALLY land with the right content," use an independent read path (`tmp/verify_c7_real_audit_row.py` is the canonical pattern).
- Watch for parallel-session commits landing on a long-running checked-out branch (this session's catch).
