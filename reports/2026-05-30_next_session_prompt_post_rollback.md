# Next-session prompt — post-rollback Stage 1 prod-deploy

**Written:** 2026-05-30 at session close of the rolled-back deploy attempt (entries land on branch `stage1-deploy-2026-05-30`; not yet merged to main pending operator approval).

**State at session close:**
- `origin/main` HEAD: `06d7060` (unchanged — the rollback was prod-state-only; the only new commit is the deploy_log entry on the deploy branch).
- Deploy branch: `stage1-deploy-2026-05-30` HEAD `58a1807` (deploy_log entry) + 1 more commit pending after this prompt + BACKLOG edits. Pushed to origin.
- Worktree: `.claude/worktrees/stage1-deploy-2026-05-30` KEPT (carries the deploy_log entry, BACKLOG update, and this prompt; operator merges to main at discretion).
- Prod state: restored byte-identically to pre-deploy `4985bbe + 03:57 sed-overlay` (per `runbooks/deploy_log.md` "## 2026-05-30 17:22–17:34 UTC" entry).
- Test gate: 2044/26 baseline confirmed (same baseline as pre-deploy).
- Forensics preserved on prod: 13 `*.pre-stage1-20260530-1230` backup files (do-not-delete).

---

## Recommended next: Forward fix for `main.py:1087` / `secrets.odds_api_key` inconsistency + audit ALL uncommitted prod surgical edits

The Stage 1 prod-deploy remains BLOCKED. Resolving it requires two distinct work items (per the new P1 entry in `BACKLOG.md` "Stage-1 prod-deploy BLOCKED" section):

### Item 1 (required, single PR): add `odds_api_key` field to Secrets

**Scope:**
- `trading_corp/utils/secrets.py`: add `odds_api_key: str | None` field to `Secrets` dataclass (between `kalshi_api_key_id` and `kalshi_private_key_pem`, matching the prod backup layout); add `odds_api_key=_env("ODDS_API_KEY")` to `load_secrets()` populator block.
- `tests/test_secrets.py` (new or extend existing): assert that `Secrets()` has every attribute referenced via `secrets.X` in `main.py`. Mechanical AST scan of main.py + assertion that each field exists on `Secrets` dataclass. This is a coverage-of-the-bug-class test — strengthens `[[mocks-dont-catch-sdk-shape]]` discipline.

**Estimated:** ~30 LOC source + ~80 LOC test. Single commit; ~30-60 min.

### Item 2 (required before next deploy attempt): audit ALL uncommitted prod surgical edits

**Scope:** for each of the 13 backed-up files on prod (`*.pre-stage1-20260530-1230`), diff against `origin/main`'s version. For each added/removed symbol that lacks a matching commit on `git log --all -S "<symbol>"`, classify:
- **Uncommitted prod-only addition (round-trip to git)** — like `odds_api_key`.
- **Uncommitted prod-only removal (verify intent before reapplying)** — possible if a surgical patch deleted a field/function that main still references.
- **Genuinely different between branch + prod (cosmetic)** — CRLF normalization, whitespace, etc.

**Method:**
1. Pull each backup file from prod via single-bundled az or scp (per session's earlier pull pattern).
2. For Python files: parse imports + class members + function names; compare sets. Use Sonnet for the mechanical diff if helpful (but verify findings — Sonnet hallucinated file presence in this session's import-graph audit; `[[verify-premises-against-ground-truth]]` applies).
3. For YAML files: structural diff; flag any key on prod not on main, or differing value.
4. Round-trip findings to `origin/main` via PRs before the next deploy attempt.

**Estimated:** ~2-4 hours depending on findings. Output: a report under `reports/2026-05-XX_uncommitted_prod_surgical_edits_audit.md` enumerating each finding + git round-trip status.

### Item 3 (recommended, optional this session): import-sanity-check hardening

**Symptom:** the deploy's import-sanity check (`python3 -c "from trading_corp.main import run"`) passed in 0.364s with no side-effects, but the deploy still crashed on `secrets.odds_api_key` because the check did NOT execute `run()` body. The check is necessary but insufficient.

**Scope:** add a `python3 -c "from trading_corp.main import run; import asyncio; asyncio.get_event_loop().run_until_complete(_validate_startup_secrets())"` style probe (or equivalent) that walks main.py's `secrets.*` accesses against a real loaded Secrets without spinning up brokers. Either a function in main.py or a pre-deploy check script.

**Estimated:** ~50-100 LOC. Single PR. Optional this session; can defer.

---

## Read first (in order)

1. **Memory entries:**
   - `[[stage1-deploy-rolled-back-2026-05-30]]` — full rollback context + forward-fix options.
   - `[[gate-a-rest-resilience-landed-2026-05-30]]` — the deploy this session attempted.
   - `[[bitunix-risk-tier-and-leverage-pre-live]]` — the 03:57 surgical sed-overlay on prod (still active).
   - `[[verify-premises-against-ground-truth]]` — discipline standard (Sonnet hallucinated file presence in this session's import-graph audit; verify everything via direct probe).
   - `[[mocks-dont-catch-sdk-shape]]` — the discipline that the missing-field test would strengthen.
   - `[[no-documented-leaky-escape-hatch]]` — why option 2 (`getattr` defensive) is NOT recommended.

2. **Reports + audits:**
   - `runbooks/deploy_log.md` "## 2026-05-30 17:22–17:34 UTC" entry — full deploy + rollback story (on branch `stage1-deploy-2026-05-30`; merge to main pending operator decision).
   - `BACKLOG.md` "Stage-1 prod-deploy BLOCKED" P1 section at top — the canonical work-item description.
   - This file (`reports/2026-05-30_next_session_prompt_post_rollback.md`).

3. **Verify before doing anything:**
   - `git rev-parse origin/main` should equal `06d7060` (unchanged since session close).
   - `git status` clean.
   - Test gate: 2044/26 baseline.
   - Check whether the deploy branch (`stage1-deploy-2026-05-30`) has been merged to main yet by the operator — if yes, work on top of main; if no, your work goes on a fresh branch off `stage1-deploy-2026-05-30` so the deploy_log + BACKLOG entries travel forward.

## Constraints carry forward

- **Operator-supervised.** Stop-and-report at every fork.
- **No prod-touching writes this session** — Items 1 + 2 are code/audit work only; the next deploy attempt is a SEPARATE session after Items 1 + 2 land.
- **`[[verify-premises-against-ground-truth]]`** — directly probe prod via az for each surgical-edit candidate; don't trust Sonnet's first-pass analysis (this session's import-graph audit had two MISSING-on-prod files wrongly classified as present until I probed).
- **Tighter commits than feels normal:** one commit per item (Item 1 = secrets + test; Item 2 = audit report + each round-trip PR; Item 3 = harness improvement).

## Out of scope unless re-prompted

- **Next prod deploy attempt** — separate session AFTER Items 1 + 2 land.
- **`execution_mode: live` flip on bitunix** — separate operator decision; out of scope post-deploy too.
- **tasty_options `auto_execute: true` flip** — separate operator decision (HITL-gated by default).
- **N+2 Phase 3 exit-path implementation** — separate work track.
- **Gate (c) md5-diff tool CRLF fix** — P3 BACKLOG; flagged in this session's investigation but deferred.
- **Editing CLAUDE.md** — separate proposal per CLAUDE.md § 6.

## Output expected at session close

- Item 1 PR landed on `origin/main` with passing test gate.
- Item 2 audit report committed under `reports/`; each finding round-tripped to `origin/main` via individual PRs (or explicitly documented as accepted divergence).
- New next-session prompt that re-evaluates the deploy unblock criteria and schedules the next deploy attempt.
- Memory: `[[uncommitted-prod-surgical-edits-audit-2026-05-XX]]` capturing the audit findings.

---

## Discipline standard (carried forward)

- Use Sonnet sub-agents for mechanical work when capable — but VERIFY each finding via direct probe.
- Stop-and-report at forks rather than auto-resolving.
- Surface anomalies with diagnostic detail.
- Don't expand scope mid-task.
- Tighter commits than feels normal.
- Phone-in-hand RH-pickle coordination required for any future restart (not needed this session — no restart planned).
