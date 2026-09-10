# Leg-Audit REVIEW Surfacing — Build + Staged Deploy Manifest (2026-09-10)

**Follow-up to** the leg-independence fix (prod-live `88f7b66`), which made the engine *write* an
independent post-fill leg-check verdict into `pm_subdivision_order.leg_audit` (migration 021). Until now
**nothing read it back** — a check whose output nobody reads has silently stopped checking. This follow-up
is the **read side**: one canonical classifier, a fill-watch runner block, and a pm_web `/live` page-top
safety strip. `leg_audit` remains write-only on the engine side; this adds observability only.

Branch `pm-legaudit-surface-2026-09-10` (off `origin/prod-live 88f7b66`). **NOT deployed** — reserved for
Jack: DEPLOY / PUSH / RESTART / any DB write / any arm.

---

## 1. What ships

| # | File | Kind | Base (box CR-sha16) | Target (CR-sha16) | Graft |
|---|------|------|---------------------|-------------------|-------|
| 1 | `trading_corp/prediction_markets/leg_audit.py` | **NEW module** | (absent) | `93e2dbd7ba29733e` | wholesale (new file) |
| 2 | `trading_corp/prediction_markets/web/app.py` | drifting (Deploy-7) | `16caedfe6a193737` | `23ec41a66d991846` | **patch** `app.py.patch` |
| 3 | `trading_corp/prediction_markets/web/live_view.py` | drifting (Deploy-7) | `a4f233fb18f0cc3c` | `583aac901c5984bb` | **patch** `live_view.py.patch` |
| 4 | `trading_corp/prediction_markets/web/templates/pm_live_list.html` | drifting (Deploy-7) | `97f0f12b5bdfb1ca` | `07cc74b498d6a6ed` | **patch** `pm_live_list.html.patch` |

Patches live in `cc/legaudit_ui_patches/`. **`leg_audit.py` and the tests are the only trading_corp-repo
artifacts on the branch.** The three pm_web files DRIFT (the box is at Deploy-7 = app.py `16caedfe`; the
branch's own copies are stale Deploy-6 `eeac337d`) — so they are grafted onto **box-current** as sha-verified
patches, **never shipped wholesale** (box-is-truth). Base shas above were recomputed from the live box
2026-09-10 and match the Deploy-7 record.

### Design
- **`leg_audit.py`** — stdlib-only (zero imports beyond `__future__`), so the pm_web standalone-imports guard
  still holds. One CANONICAL `classify_leg_audit()` used by BOTH the runner and the UI → they cannot drift
  into two transforms (the exact failure class the parent fix was about). Five states:
  `inversion` (REVIEW\*, loud) / `soft` (code_review\*) / `unevaluated` (unchecked or unknown-non-null) /
  `clean` (ok|na) / `legacy` (NULL). **An unreadable audit is NOT a pass**: any unrecognised non-null verdict
  → `unevaluated`, never `clean`. `read_leg_audit_reviews(conn, account_ids, limit)` selects `dry_run=0 AND
  leg_audit IS NOT NULL`, classifies, keeps the three surfaced states, returns severity-first rows + honest
  counts + `shown`/`total_surfaced`.
- **fill-watch** (`cc/pm_fill_watch_ro.sh`) — a loud LEG-AUDIT block at the TOP (reads the audit back FIRST),
  via the canonical reader. Degrades gracefully: module-absent → "not on this box yet"; schema<21 →
  column-absent; both non-fatal.
- **`/live` strip** — `_load_live_list` reads reviews **scoped to the viewer's visible accounts** and passes
  them to the (pure) `build_subdivisions_context`, which shapes a `leg_audit_strip`. The template renders a
  page-top banner parallel to the existing alarm strip: **red** when any inversion, **amber** otherwise;
  three distinct per-row markers (`*** INVERSION` / `~ code review` / `? could-not-check`); each row names
  ticker + account + whale outcome + fill status + verdict + a deep-link. Self-contained **inline styles**
  (reusing existing CSS vars `--alarm`/`--stale`/`--dim`/`--dmono`) → **no `pm_desk.css` change, no
  cache-bust**.

---

## 2. Red-green proof (box-scratch; LIVE tree untouched)

- **Rung-1 unit** (`cc/pm_legaudit_scratch`): BASE (no module) RED = ImportError; FIX = **9/9 green**
  (classifier five-states, unknown-fails-safe, legacy≠unevaluated, read-agrees-with-writer, reader
  surfaces+separates, no-false-alarm-when-clean, honest-empty-pre-migration, **empty-scope-surfaces-nothing**,
  **inversion-never-truncated**).
- **Rung-2 runner** (`cc/pm_legaudit_runnertest`): REVIEW_DB → surfaced=2 (inversion+code_review; na/legacy
  excluded), INVERSION printed first; CLEAN_DB → 0 surfaced. Live read-only fill-watch run confirmed the
  graceful "module not on this box yet" degrade on the current undeployed box.
- **Rung-3 render** (`cc/pm_legaudit_uitest`): the REAL `pm_live_list.html` compiles with the block; REVIEW_DB
  → strip total=3 with all three states distinct, INVERSION leads (severity-first), a FILLED inversion shows
  **"FILLED — HELD"** (loss-red), a not-filled row shows **"NO_FILL"** (dim, no false "we hold it"); CLEAN_DB
  → no strip HTML at all.
- **Regression** (`cc/pm_legaudit_regress`): web-render tests on BASE (box-current) vs FIX (+graft) — the
  "NEW regressions" diff is **EMPTY**; both trees show the identical 21 pre-existing env-gap failures, FIX
  adds exactly +9 passing leg_audit tests (27→36 passed). **Zero new failures.**

---

## 3. Adversarial review (two skeptics) — findings + resolutions

**Skeptic A (correctness/safety)** — found **1 BLOCKER**, 0 HIGH, 2 MEDIUM, 1 LOW, 1 NIT. All fixed:
- **BLOCKER (fixed):** `if account_ids:` treated `[]` (a zero-account viewer) as "unscoped" → fell through to
  ALL accounts → cross-account leak in the banner for a non-admin who owns no account. **Fix:** distinguish
  `None` (operator/CLI = all) from `[]` (viewer sees nothing → return empty). Pinned by new test
  `test_reader_empty_account_scope_surfaces_nothing`.
- **MEDIUM (fixed):** 50-row cap applied by recency *before* severity sort → an older INVERSION could be
  counted but dropped from shown rows. **Fix:** reader now sorts severity-first *before* the cap; surfaces
  `shown`/`total`, and the UI/runner print "showing N of M". Pinned by `test_reader_inversion_never_truncated`.
- **MEDIUM (fixed):** reader surfaced submitted-but-not-filled rows while the copy asserted "we may hold the
  opposite." **Fix:** pass `outcome_status` through; strip shows per-row status (FILLED→"HELD" in loss-red,
  else the raw status), copy reworded to "where it filled we hold the opposite (check each row's status)."
- **LOW (fixed):** empty-string verdict → LEGACY (dropped). **Fix:** non-null blank → `unevaluated` (surfaced),
  consistent with "unreadable is not a pass." Test updated.
- **NIT (accepted):** classifier is case-sensitive by design; the unknown branch fails SAFE (surfaced, never
  clean), and the write side emits exact-case tokens.

**Skeptic B (deploy/freeze)** — **SAFE TO DEPLOY, 0 BLOCKER/0 HIGH.** Verified: patch bases match box shas
and apply clean touching only leg-audit regions (no Deploy-7 clobber); `leg_audit.py` imports nothing
engine-side (standalone guard holds); no cache-bust (CSS untouched, vars already used on this page); no
migration (box at schema 21, reader degrades honestly if absent); read-only (only `PRAGMA` + `SELECT`, no
INSERT/UPDATE/DDL); no ordering hazard (all files land before the single restart). One MEDIUM (advisory): the
reader call is unwrapped and the SQL is unbounded — **accepted**: it is the same posture as every other reader
already on `/live` (a genuine DB error should be loud, not silently hide a safety strip), the scan is only over
non-NULL-`leg_audit` rows, and rows are capped in Python with an honest "showing N of M". Left as-is by design.

---

## 4. Tie-residual analysis (the deployed leg-fix guard) — asked; NOT changed

`labels_code_swapped()` (ufc_poly_kalshi_match.py, imported by cs2/tennis) returns **`cross > direct`** — it
refuses (safe miss) only when the CROSS code↔label assignment scores *strictly* higher. **On a tie
(`cross == direct`) it FAILS OPEN**: it abstains and proceeds on the PRIMARY exact-name binding (`_canon`
equality, which is itself strict — academy/fe/ex- are distinct entities, never fuzzy-merged). The code
cross-check is a SECONDARY tiebreaker, so a tie is *not* a raw coin-flip; it is "the code adds no information,
trust the strict name match."

**Can it fail CLOSED?** Yes — change `>` to `>=` (refuse on tie). **Over-skip cost:** markets where the two
codes are equally-good matches to both names but the name binding was correct → a MISSED copy (money left on
the table), **never a wrong fill**.

**Measured cost (read-only probe against the LIVE deployed guard, `cc/pm_legaudit_tieprobe`):** across 10
representative real pairs — including the confirmed FaZe/magic and Team-Liquid/Vitality swap classes — the
correct-label `direct` score dominates `cross` by a wide margin (**4–6 vs 0–1**) and there are **ZERO ties**.
Kalshi codes are name-derived (prefix/acronym → 3 on their own name, ~0 on the other), so the tie band is a
razor-thin middle real data essentially never lands in.

**Recommendation: KEEP `>` (fail-open on tie) for now.** (a) The primary name binding is strict; (b) the
leg-fix validation flagged 0 legitimate live events and this probe finds 0 ties; (c) **this very follow-up is
the backstop** — a tie-induced wrong side in cs2/tennis/ufc would surface post-fill as a `code_review` SOFT
row in the strip (the write side emits `code_review:...`, which the reader surfaces as `unevaluated`/`soft`
depending), so we would SEE it. If a real tie-induced wrong side ever surfaces, flip to `>=` (near-zero
over-skip cost, as measured). **Not changed without telling the cost** — the cost is measured and near-zero,
but the change is deferred because it is currently unnecessary and the observability makes fail-open safe.

---

## 5. Upstream-cause recommendation (asked to state, NOT build)

The soccer/mlb wrong fills the audit could NOT catch were an **upstream signal-outcome divergence**: the
whale's outcome as captured at copy time did not equal the side actually held (the matcher bound correctly to
what it was given; the *input* had drifted). Migration 021 now persists `signal_outcome` + `signal_slug` on
every real order, which makes a reconcile possible for the first time.

**What would actually fix it:** a periodic **signal-time-vs-settlement reconcile** — for each filled order,
re-fetch the whale's Polymarket position for `signal_slug` as it stood at (or nearest before) `response_ts`
and compare to the persisted `signal_outcome`; where they differ, the divergence is upstream (stale/misparsed
signal), not a matcher bug. This would (a) quantify how many "wrong" fills are upstream vs matcher, (b) pinpoint
whether the divergence is a capture-timing race or a slug/outcome parse error, and (c) give a ground-truth
denominator for the wrong-fill rate. It needs read-only Polymarket history access + a new reconcile job
writing an advisory column/table — a separate, larger build. **Recommended as the next PM reliability item;
not built here.**

---

## 6. Staged deploy — ordering, post-check, stop conditions

**Nature:** pm_web-only observability. **NO engine restart. NO migration** (leg_audit exists from 021).
**NO order path / arm / DB write.** ONE pm_web restart.

**Ordering (all before the single restart):**
1. **Drift-check** the three pm_web files on the box: confirm CR-sha16 == `16caedfe6a193737` / `a4f233fb18f0cc3c`
   / `97f0f12b5bdfb1ca`. If any differs, STOP — the box moved; regenerate patches against the new base.
2. **Backup** the three files + note the pm_web PID.
3. **Graft:** copy `leg_audit.py` (NEW) into `trading_corp/prediction_markets/`; apply the three patches with
   `patch --fuzz=0`. Verify post CR-sha16 == `93e2dbd7...` / `23ec41a6...` / `583aac90...` / `07cc74b4...`.
4. **Restart pm_web only** (engine `trading-corp` PID untouched, NRestarts unchanged).

**Post-check (all read-only):**
- pm_web restarted (new PID); engine PID + NRestarts UNCHANGED.
- `GET /live` returns 200 and renders; if any leg_audit REVIEW/soft/unevaluated rows exist they appear in the
  strip; if none, NO strip (clean) — verify against `cc/pm_legaudit_status_ro` (currently 0 surfaced, 1 post-021
  `na` row → strip absent, correct).
- Scoping: a non-admin viewer sees only their own account's rows (and a zero-account viewer sees none).
- `cc/pm_fill_watch_ro` now prints the live LEG-AUDIT block (no longer "module not on this box yet").
- No new engine errors; money tie-outs unchanged (pure read).

**Stop conditions (roll back = restore the 3 backups + remove leg_audit.py + restart pm_web):**
- Any drift-check sha mismatch in step 1.
- `GET /live` 500s or the strip renders on CLEAN data (false alarm).
- The standalone-imports test fails on the box (engine leaked into pm_web).
- pm_web will not restart or the engine PID changes.

**Artifacts:** patches `cc/legaudit_ui_patches/*.patch`; runners `cc/pm_legaudit_{scratch,runnertest,uitest,
regress,tieprobe,status_ro,boxfetch_ro}` + `cc/pm_fill_watch_ro`; render test `cc/pm_legaudit_render.py`;
box-current base snapshots `cc-legaudit-boxsrc/`; fix snapshots `cc-legaudit-fix/`.

---

## 7. Deploy outcome (2026-09-10, board-authorized) — LIVE

Grafted via ONE sanctioned single-STDIN-stream runner (`cc/pm_legaudit_deploy_stream`, files embedded as
base64 — no scp) + Jack's pm_web restart. **GREEN.**

- Graft: drift-check (box==base) → dry-run `--fuzz=0` → backup `~/legaudit_deploy_backup_20260910T223415Z` →
  apply → **post-sha verify all 4 == target** (`93e2dbd7`/`23ec41a6`/`583aac90`/`07cc74b4`) → py_compile.
- Restart: pm_web **309331 → 319921**; engine `trading-corp` **313359 / NRestarts 0 UNCHANGED**.
- Post-check (`cc/pm_legaudit_postcheck_ro`): deployed-code render **ABSENT on live (0 REVIEW rows) AND FIRES
  on a seeded REVIEW row** (`*** INVERSION` + `data-legaudit-count="1"`); served `/live` 200, content intact,
  strip absent, `/pm/arm` 404.
- **prod-live NOT advanced** — the box carries the deploy; folding the pm_web graft to prod-live waits on the
  standing pm_web reconcile (box is Deploy-7, prod-live is Deploy-6), Jack's timing.

**Three deploy-night lessons (all recovered clean):** (1) I committed the UI patches BEFORE the final
adversarial-review edits, so the first graft produced pre-review shas — the **post-sha guard caught it and
stopped**; pm_web was never restarted so the graft was inert; rolled back clean via the box's own backup.
Fix: regenerate patches after the last edit + a LOCAL pre-flight (apply-to-base == target) before any box
deploy. (2) `scp` hung ~40 min — the sanctioned channel is a single STDIN stream, not scp. (3) The first
post-check caught the pm_web pid **unchanged** (restart hadn't recycled) — always confirm a NEW served pid
before trusting served-page evidence.
