# Architectural review — additions for Finding #1 and Finding #9

The canonical architectural review file is `reports/2026-05-30_stage1_bitunix_live_engine_architectural_review.md`, which lives on branch `stage1-architectural-review-2026-05-30` and is not yet on `origin/main`. This memo captures additions that should be appended to that review when it next merges or is re-edited.

**Generated:** 2026-05-31 by session that shipped Items 3+4+5 on branch `stage1-blockers-items3-4-5-2026-05-30`.

## Finding #1 (drift class) — add sixth locus

**Locus 6: deploy transfer set scoping has a coverage gap (filesystem-vs-pointer drift).**

The first five loci documented in Finding #1 cover SOURCE-CODE drift surfaces (attribute access, kwarg signatures, etc.). Locus 6 covers FILESYSTEM-level drift between prod and origin/main that is invisible to source-code-level discipline.

**Symptom:** the 2026-05-30 22:43 UTC redeploy attempt rolled back at T+5min30s on `TypeError: WebDeps.__init__() got an unexpected keyword argument 'tasty_division'`. Origin/main's `web/app.py` had the field continuously since `94b3129` (2026-05-24). Prod's `web/app.py` was from 2026-05-18 — predating the field's addition. The redeploy's 18-file transfer set was diff-derived against `4985bbe` where `web/app.py` already had the field, so `git diff` was empty for the file and it was filtered out of the transfer set. The new `main.py` (which WAS transferred) constructed `WebDeps(tasty_division=...)` against the stale `web/app.py` and crashed.

**Read-only probe confirmation** (2026-05-31 00:08 UTC + full sweep at 00:55 UTC):

- Prior diagnostic ("origin/main forgot the field") was wrong — field present continuously since 94b3129.
- Full filesystem sweep of trading_corp/+config/ found 51 DIFFER-STALE-ON-PROD files (including `web/app.py`) + 14 MISSING_ON_PROD files. The transfer set should have been 65 files, not 18.

**Why this class is invisible to existing disciplines:**

- The Item 1 AST gate (secrets reads) catches source-code drift on `Secrets`. Origin/main was internally consistent here.
- The Finding #9 filesystem audit catches NEW prod-only edits since the most recent audit. It does NOT catch files that have been stale since BEFORE the audit (the audit's baseline doesn't reflect git state at the file level).
- The deploy-import-graph audit ([[feedback-deploy-import-graph-audit]]) catches missing transitive deps in the transfer set, but only relative to the diff baseline — same fundamental gap.

**Recommended remediation:** Item 5 of Stage-1 BLOCKED (this session's ship) — file-level prod-vs-origin/main md5 sweep tool at `scripts/prod_vs_main_file_level_md5_sweep.py`. Standing-discipline filed at `[[file-level-prod-vs-main-sweep-as-standing-discipline]]`. Sweep must run before every whole-file deploy; transfer set must UNION the sweep's DIFFER-STALE + MISSING findings with the diff-derived set.

## Finding #9 (discipline recommendations) — add seventh recommendation

**Seventh discipline: full-surface file-level prod-vs-main md5 sweep before every whole-file deploy.**

The existing Finding #9 sixth-discipline (audit-not-stale re-probe before transfer) covers the case where NEW prod-only edits accumulate after an audit snapshot. The seventh discipline covers the inverse: files on prod that have been STALE since BEFORE the most recent audit (i.e., the audit's snapshot was already incomplete relative to git's state).

**Specific mechanics:**

- Tool: `scripts/prod_vs_main_file_level_md5_sweep.py`.
- Run: single bundled `az vm run-command` call with gzip+base64-compressed payload. ~30s wall + ~5s decode. Read-only against prod.
- Verifies: explicit SWEEP_BEGIN_v1/SWEEP_END_v1 markers + `expected_count` + `payload_len` in the trailer. Truncation is detected (raises before parsing).
- Output classifications: MATCH / DIFFER-EXPECTED-PER-DEPLOY-LOG / DIFFER-STALE-ON-PROD / MISSING_ON_PROD / PROD_ONLY_NOT_ON_MAIN.
- Known-overlay whitelist documents intentional drift (e.g., the 03:57 UTC bitunix paper-sizing sed-overlay on `config/strategies.yaml`).

**Validation evidence (first run, 2026-05-31 00:55 UTC):**

- 251 expected files (trading_corp/ + config/ on origin/main).
- 185 MATCH ✓ + 1 DIFFER-EXPECTED-PER-DEPLOY-LOG (config/strategies.yaml) + 51 DIFFER-STALE-ON-PROD + 14 MISSING_ON_PROD = 251 total accounted ✓.
- 18 PROD_ONLY_NOT_ON_MAIN entries (15 historical .bak/.orig + 3 anomalies flagged for operator review).

**Where to find the canonical implementation:** see `[[file-level-prod-vs-main-sweep-as-standing-discipline]]` memory entry for the full standing-rule write-up.

## Integration with prior architectural review findings

- **Finding #1 (drift class):** locus 6 added above.
- **Finding #9 (discipline recommendations):** seventh discipline added above.
- **Finding #2 (Stage-1 readiness gaps):** unchanged. Gates (a)+(b)+(c) still LANDED (per prior session); this session adds Item 5's sweep as a NEW pre-deploy gate to the trio.

When merging this delta into the review file, place these additions inline at the appropriate section anchors. Both additions reference the new memory entries which serve as canonical pointers.

## Finding #1a — exit-path branch-stranded docs locus CLOSED (2026-05-31 ~21:47 UTC)

**Status update (not a new locus; closes one of Finding #1a's documented loci).**

Architectural review Finding #1a flagged Phase 1a + 1b sub-diagnostic reports as branch-stranded on `bitunix-live-exit-path-2026-05-29` (`33da534` / `e1d38f8`), reachable only via `git show <sha>:reports/...md` reach-back.

**Closure path executed:** 2026-06-01 scoping session rebased the branch onto `origin/main` `f110c74` as `bitunix-live-exit-path-2026-05-29-rebased` (HEAD `3016053`). 2026-05-31 ~21:47 UTC docs-merge session folded the 3 rebased docs commits onto main via `--no-ff` merge `90ae0e4` per scoping report Decision 6.5 operator override.

**Reports now canonically reachable from `origin/main`:**
- `reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1a.md`
- `reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1b.md`
- `reports/2026-05-29_next_session_prompt.md`

**Other Finding #1a loci unchanged.** The canonical review file itself (`reports/2026-05-30_stage1_bitunix_live_engine_architectural_review.md`) is still on `stage1-architectural-review-2026-05-30` branch (`ade4dbc`), NOT on main — that locus remains open; merge decision deferred per the original Finding #10 question 2. This addition file itself remains the canonical addenda-memo surface for the review.

**Test gate verified docs-only zero-impact:** 2139 passed / 28 failed pre-merge AND post-merge (identical).
