# Next-session prompt — post-gate-(a)

**Written:** 2026-05-30 at session close of gate (a) REST resilience (merge `eae5080` on origin/main).

**State at session close (post-wrap):**
- `origin/main` HEAD: `f6f6c06` (next-session prompt commit; HEAD of main = HEAD of this file's parent commit).
- Working tree clean; `git status` empty; main = origin/main.
- Gate (a) merge commit: `eae5080`. Branch `bitunix-rest-resilience-2026-05-30` pushed to origin for audit trail. Worktree `.claude/worktrees/bitunix-resilience` REMOVED.
- All 3 P1 pre-deploy gates from the 2026-05-30 architectural review Finding #2 are LANDED:
  - (a) REST resilience — `eae5080` (this session)
  - (b) Operational runbooks — `f20a7bc`
  - (c) md5-diff prod-surface tool — `b131d02`
- Prod state: **UNCHANGED.** Still `4985bbe + 03:57 UTC sed-overlay` of TIER_SIZING.
- Memory entries marked closed for all 3 gates: `[[bitunix-rest-resilience-2026-05-30]]` (new), `[[bitunix-operational-runbooks-2026-05-30]]` (updated), `[[gate-c-md5diff-landed-2026-05-30]]` (updated), `[[bitunix-live-engine-build]]` (UPDATE 3 banner added), `[[2026-05-30-architectural-review-first-batch-remediation]]` (closure note appended). MEMORY.md index has the new gate (a) entry at the top.
- Recent leftover worktrees still present (not removed; not blocking): `bitunix-md5diff` (gate-c), `bitunix-runbooks` (gate-b), `polycopy`, `TradeViewPS`, 2× locked agent worktrees. Each operator may prune at convenience.

---

## Recommended next: Prod deploy of Stage 1 (operator-supervised)

The main-to-prod deploy is now unblocked. This is the deploy that takes the entire Stage 1 surface from `main` to prod for the first time:
- Phase-4 broker-write (`brokers/bitunix.py` — `place_order`, `cancel_order`, fill observation, kill-switch primitives)
- Stage-1 N safety scaffolding (mode-mismatch consumer + `flatten_division`)
- Stage-1 N+1 entry-path wiring (`execution_mode` YAML flag, HITL gate for first 10 orders, monitor-mode, `StrategyState.from_persistence`, safety_notifier)
- Risk-tier rebalance (already on prod via sed, now also on main)
- **Gate (a) REST resilience (this session)** — retry/backoff + snapshot-staleness halt + stuck-order timeout→cancel
- Gate (b) runbooks (doc-only, no code surface on prod)
- Gate (c) md5-diff verification tool (operator-side tooling)

This is a **large surface deploy**. Treat it as its own session with go/no-go review.

### Read first (in order)

1. **Memory entries**
   - `[[bitunix-rest-resilience-2026-05-30]]` — this session's deliverable.
   - `[[bitunix-operational-runbooks-2026-05-30]]` — gate (b) outputs (the operator-facing runbooks the deploy must reference if anything goes wrong).
   - `[[gate-c-md5diff-landed-2026-05-30]]` — the md5-diff verification tool the deploy MUST run pre-flip.
   - `[[stage1-on-main-merge-session-2026-05-30]]` — the broker-write + safety + entry-path merge session that this deploy carries forward.
   - `[[bitunix-risk-tier-and-leverage-pre-live]]` — the sed-overlay state on prod that the deploy must NOT silently revert.
   - `[[feedback-deploy-import-graph-audit]]` — the mechanical pre-deploy gate that this deploy MUST pass.
   - `[[committed-not-deployed-recurring-drift]]` — fix-it-once discipline; deploy is the only way to move "shipped to main" → "live on prod".
   - `[[verify-premises-against-ground-truth]]` — discipline standard.
   - `[[branch-tests-must-cover-existing-fixtures-not-only-new-tests]]` — discipline standard.

2. **Reports + audits**
   - `reports/2026-05-30_gate_a_rest_resilience_next_session_prompt.md` — read-first list for the gate (a) work (now done; useful context for the deploy because gate (a) introduces new operator-facing surface).
   - `runbooks/deploy_log.md` § "2026-05-30 ~09:05 UTC" — the gate (a) source-merge entry with detailed change summary.
   - `runbooks/deploy_log.md` § "Operator-facing knobs (gate (a) REST resilience, 2026-05-30)" — the one new YAML knob.
   - `runbooks/bitunix_panic_halt.md` — primary incident runbook the operator needs warm-cache before any live activity.
   - `runbooks/2026-05-29_bitunix_live_readiness_audit.md` § 5 — what the gates closed.

3. **Tools**
   - `scripts/bitunix_prod_surface_md5diff.py` — RUN THIS before the deploy. Manifest-driven 10-file diff vs prod. Update the manifest with the Phase-4 `place_order` code if not already listed.
   - The deploy mechanism itself (whatever it is — SSH + git pull + restart, or scp the targeted file set, or systemd unit reload).

### Pre-deploy gates (in order)

1. **md5-diff prod-surface tool MUST run clean** (or with operator-acknowledged divergences). Exit code 0 = clean; non-zero needs investigation before proceeding.
2. **Import-graph audit per `[[feedback-deploy-import-graph-audit]]`.** Resolve transitive imports of the transfer set + ls-check each on prod; missing → add to transfer set; recurse. Gate (a) adds dependency on `trading_corp/utils/time.py` (already on prod).
3. **Prod state verification** — `runbooks/deploy_log.md` line check: prod last-deploy SHA matches what we expect (`4985bbe + 03:57 sed-overlay`). If not, halt and investigate.
4. **Backup tag** — create a `.pre-stage1-{DATE}-{TIME}` backup BEFORE touching the live file set.
5. **Operator sign-off explicit** in chat before any `scp` / `ssh ... restart` / `git pull` runs.

### Scope of the deploy

The deploy carries every file changed on `main` since prod's `4985bbe` base. Use `git diff --name-only 4985bbe..origin/main` to enumerate. Expected ~25 files across `trading_corp/brokers/`, `trading_corp/agents/`, `config/`, `runbooks/`, `scripts/`, `tests/`.

**Don't transfer tests** unless prod runs them (it doesn't).
**Don't transfer `data/` artifacts** (DB pickles, replay caches).
**Don't transfer worktree-only files** (`.claude/`, `reports/`).

### Post-deploy verification (the gates IN reverse)

1. **Process up** — `systemctl status` or equivalent; `healthz=200`.
2. **Constants verified via fresh venv import** — pull `_RETRY_*`, `_DEFAULT_SNAPSHOT_STALENESS_S`, `_FILL_MAX_POLLS` from `trading_corp/brokers/bitunix.py` on prod; verify against current main constants.
3. **md5-diff re-run** — should now show 0 divergences for the deployed file set.
4. **Smoke test the new audit kinds** — query `audit_event` table for `rest_request_retried` / `snapshot_stale_halt` / `stuck_order_cancelled` / `stuck_order_cancel_failed` kinds. None should fire in steady state; their schemas should match what the test fixtures expect.
5. **`would_have_placed` audit row continuity** — paper-mode placements must continue uninterrupted. Stage-1 deploy is paper-default; live-flip is a SEPARATE follow-up.
6. **Operator subscribed to safety_alert telegram path** — verify the channel reaches the operator's phone, not just logs.

### Out of scope unless re-prompted

- **The live-flip** itself (paper → live mode) is a SEPARATE deliberate operator action AFTER this deploy lands cleanly. Do NOT flip `execution_mode: paper → live` in the same session.
- The 2 unfiled runbooks from gate (b) scope (buggy-deploy rollback + discrepancy dispute).
- The 8 Finding #10 architectural-review decisions still queued.
- The N+2 Phase 3 exit-path implementation.
- Tastytrade C-1 rotation (deferred P1 ceiling 2026-06-12 per `[[reference-tastytrade-refresh-token-no-self-rotation]]`).

### Alternate next-session paths (if operator defers the deploy)

If the operator wants to defer the deploy decision (legitimate — large surface, deliberate timing), the next session could instead pick up:

- **Finding #10 decisions** — 8 queued architectural-review decisions per `[[2026-05-30-architectural-review-first-batch-remediation]]`. Each is small-scope, no-deploy. Read-only review work.
- **The 2 missing gate (b) runbooks** — buggy-deploy rollback + discrepancy dispute. Pure writing, ~SMALL each. Follows the same `# Last verified` template as the shipped pair.
- **Tastytrade C-1 rotation** — P1 ceiling 2026-06-12. Per the per-portal pattern `[[c1-per-portal-rotation-discipline]]`.

### Discipline standard (carry forward)

- Operator-supervised. **STOP-and-report at forks** rather than auto-resolving.
- Use Sonnet sub-agents for mechanical work when capable.
- Surface anomalies with diagnostic detail.
- Don't expand scope mid-task.
- Tighter commits than feels normal: if the work produces an artifact (summary file, runbook updates), commit it as you go rather than at the end.
- **For deploy specifically: NO prod deploys without explicit operator sign-off in the session.** Pre-deploy import-graph audit + md5-diff verification are mechanical prerequisites that must pass.

---

## State verification commands (for fresh session start)

```bash
cd "C:\Users\AA Incorporado\cc"
git rev-parse origin/main      # should equal 99a5be3 or later
git log --oneline -10           # should show eae5080 merge + 99a5be3 docs
git worktree list               # bitunix-resilience worktree may still exist; safe to remove
```
