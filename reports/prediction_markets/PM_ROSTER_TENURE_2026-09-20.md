# PM /live ROSTER — MULTI-SPAN TENURE FROM ATTACHMENT EVENTS (BUILD) — 2026-09-20

**STATUS: DEPLOY 14 LIVE ON prod-live 2026-09-21.** `origin/prod-live` **`d1b93ce7` -> `dc48d072` (code+report, FF)**,
tag **`pm-roster-tenure-deploy14-2026-09-20`**; a docs FF follows (this section + handoff). pm_web-only (5 files),
ONE `prediction-markets-web` restart (**497708 -> 499620**, ActiveEnter 2026-09-21 02:17:05 UTC); engine `trading-corp`
**491380 / NRestarts 0 UNTOUCHED**; box == prod-live 47/47; post-check **130 OK / 0 FAIL**. Full record in section 8.
Branch `pm-roster-tenure-2026-09-20` off `origin/prod-live` @ `d1b93ce7` (git truth = box), worktree
`C:\Users\AA Incorporado\cc-roster-tenure-wt`. Build record (sections 1-7) preceded the deploy.

Extends the Deploy-12 roster table's Tenure cell to render the FULL attach→detach→re-attach span history from the
migration-024 event log (`pm_subdivision_attachment_event`), with a permanent, invisible pre-024 fallback to the
attachment row. Builds on the engine-side confirmation that the 024 writers are wired + atomic
(`PM_ATTACH_HISTORY_024_2026-09-20.md`).

--------------------------------------------------------------------------------
## 1. TRUTH + BASELINE

- `origin/prod-live` tip at start = **`d1b93ce7`** (Deploy 13 docs). Branched off it.
- **box == prod-live: 47/47 pm_web deploy-surface files byte-identical (CR-stripped)** (`pm_roster_boxshas_ro`); plus
  `farm_actions.py` box CR-sha `23f91d44` == git prod-live (confirmed in the 024 investigation). No reconcile finding.
  (A first diff run listed 48 vs 47 only because the box-sha probe does not walk `farm_actions.py`; adding it to the
  git list created a phantom "missing" — the box HAS it, byte-identical.)
- **Baseline (`.venv-webtest`, the handoff command): 24 FAILED** (unchanged pre-existing set). Differential is the gate.

--------------------------------------------------------------------------------
## 2. WHAT CHANGED (pm_web only; NO subdivision.py, NO migration, NO engine file)

- **`live_view.build_spans(events, added_ts, removed_ts, active, now_ts)` (NEW, PURE).** Returns the whale's spans
  NEWEST-FIRST as `[{start, end, days}]` (end=None → OPEN span). Pairs each `attach` with the NEXT `detach` (sorted by
  ts); a trailing `attach` is the current OPEN span. **Malformed, without inventing anything (R1/R6):** a second
  `attach` while already open is ignored + warned (never a fabricated detach); a `detach` with no open span is skipped
  + warned (never a fabricated attach). **FALLBACK (R2, permanent + invisible):** if the events yield NO span (empty
  log OR all-malformed), it returns the SINGLE span from the attachment row (`added_ts`, `removed_ts` if detached) —
  exactly as the pre-024 UI shows; `[]` only if there is no `added_ts`. Dates come ONLY from events or the attachment
  row (R6) — never inferred from journal timestamps.
- **`build_roster_table(..., events=None)`** groups the events by wallet and, per row, sets `rec["spans"]` +
  `rec["tenure_sort"]` (R5: on-roster sorts by the OPEN span's start, formerly-live by the LATEST span's end);
  `tenure_days` is now the current/open span's length. `_ROSTER_NUM_KEY["tenure"]` → `tenure_sort`.
- **Loader (`app.py`)** reads `farm_actions.read_attachment_events(conn, account, category)` ONCE (all whales,
  wallet=None) and passes it to `build_roster_table`. (app.py already imports farm_actions for the Detach route.)
- **Tenure cell (`pm_whale_roster.html`)** renders `w.spans` newest-first: the open span "attached `<date>` · N days",
  closed spans "`<start>` – `<end>`", the first normal + earlier spans dimmed beneath (the ONE cell allowed to grow a
  line per span, R1). A phone-only "+N earlier spans" indicator (`.rt-spans-more`).
- **CSS (`pm_desk.css`)** `.rt-span.earlier` (dimmed) + `.rt-spans-more` (phone: earlier spans collapse to the count,
  R5). Cache-bust `?v=585ea101 → 0b50095b`.
- **★ NO subdivision.py touch** (verified: `git diff --name-only origin/prod-live -- subdivision.py` = empty). The span
  logic is `live_view` (pure) + `app.py` (existing farm_actions import) — pm_web-only, no engine-shared file, no
  restart of the engine's copy needed.

--------------------------------------------------------------------------------
## 3. FILE DIFF vs prod-live (CR-stripped sha16 BEFORE → AFTER)

**★ subdivision.py NOT touched. NO migration.** 5 pm_web files:

| file | BEFORE | AFTER |
|---|---|---|
| `web/app.py` | `0ef64011d2c223a2` | `bf9895b03ed2a7b9` |
| `web/live_view.py` | `5763057e2e757841` | `2cba1fe691bffba2` |
| `web/static/pm_desk.css` | `585ea1011a67f695` | `0b50095b62c5e7b1` |
| `web/templates/partials/pm_whale_roster.html` | `e496d37b412fc1ee` | `dd0a08cf4b8b63e7` |
| `web/templates/pm_shell.html` | `3abb631977586ba8` | `520765803e15f47d` |

Git-only (NOT deployed): `tests/prediction_markets/test_roster_tenure.py` (new, 17 tests), this report. Commit
`a8c754a7` (code + tests) + this report.

--------------------------------------------------------------------------------
## 4. VERIFICATION

- **Tests:** full-suite differential = **24 FAILED after == 24 baseline, 0 new failures**; existing roster tests
  (test_roster_table + test_whale_roster) **37 pass** (no regression — `events=None` default preserves the exact
  pre-024 single-span behaviour). **`test_roster_tenure.py` (17):** build_spans fixtures (fallback open/closed/empty;
  one-open; two-closed; closed+open; out-of-order→sorted; malformed double-attach→no invented detach; malformed
  lone-detach→fallback); build_roster_table spans + tenure_sort + tenure-column sort + no-events fallback;
  served multi-span render (2 + 3 spans, on-roster + formerly); **R4 regressions** (score "not analyzed" not 0;
  ungrounded score renders flagged "omit UNKNOWN"; Detach admin-or-owner — karen own 200 / karen-on-jack 403).
- **Renders (`cc/pm_roster_tenure_render.py`, viewed — `cc/renders_tenure/`):** `tenure_all_1600` / `_1280` show, in
  ONE roster table: **ThreeSpan** (on-roster) "attached 2026-09-20 · 0d" + two dimmed earlier ranges; **TwoSpan** open
  + one earlier; **SinglePre024** one span (fallback, indistinguishable from event-backed); **ThreeClosed** /
  **TwoClosed** (formerly-live, dimmed rows) 3 / 2 closed ranges newest-first; footer totals. Geometry: every
  `.rt-span` (1/2/3 per whale) renders INSIDE its Tenure cell (`inside=true` for all 5). `tenure_all_phone` (390):
  earlier spans hidden + "+N earlier spans" shown (`earlier_hidden=true, more_visible=true`).
- **Box RO tie-out (`cc/pm_tenure_boxdump_ro.py` + local build_spans — `cc/renders_tile/tenure_boxdump.txt`):** for
  every key on **jack/mlb (7) and karen/mlb (6)** — **13/13 render span == the attachment row's added_ts/removed_ts,
  0 differences.** The one event-backed whale per account (`0x41b4cd88`, 1 event) produces an open span whose start ==
  its added_ts (atomic writer); the rest fall back to the attachment row. Confirms the fallback + event paths agree on
  live data.

--------------------------------------------------------------------------------
## 5. R3 STATEMENT — no multi-span example on live data yet

**The live page will show ONE span per whale until a real re-attach happens.** Every (account, category, wallet) on
the box currently has at most ONE attachment event (28 rows / 28 keys — confirmed in the 024 investigation and re-seen
here: jack/mlb + karen/mlb each have exactly 1 event, the rest are pre-024 fallbacks). No whale has been detached AND
re-attached on the same sub-division since 024 landed, so no multi-span row exists on prod. The multi-span rendering is
therefore proved with a SEEDED fixture (attach→detach→attach open, and attach→detach→attach→detach→attach = 3 spans;
plus the formerly-live closed-span variants). **Do not read "one span per whale on the live page" as broken** — it is
the correct current state; extra spans appear the first time a whale is re-attached after a detach.

--------------------------------------------------------------------------------
## 6. DEPLOY SHAPE (for Jack — reserved)

**pm_web-only. NO subdivision.py, NO engine-shared file, NO migration. Engine `trading-corp` NEVER restarted.**
- Model: branch off `origin/prod-live` = box = truth, edit directly. 5 files (§3).
- Deploy: plain scp+tar diff of the 5 files onto prod-live, drift-gated (box == BEFORE for all 5, CR-sha16),
  backup-is-a-gate (dated dir outside the service path, each sha == box before any write), post-write CR-sha16 ==
  AFTER + `py_compile app.py live_view.py` + import/standalone gate (`/pm/arm`=0, 0 engine/broker modules), rollback
  on any mismatch.
- **ONE `az vm run-command … 'systemctl restart prediction-markets-web'`** (~2s UI blip). Confirm by MainPID +
  ActiveEnterTimestamp (never the az exit code). Verify `trading-corp` PID + NRestarts UNCHANGED before + after.
- Cache-bust: `pm_desk.css?v=0b50095b` — verify served sha8 + the shell `?v=` match after deploy.
- prod-live advance (SAME session, after post-check green): FF `origin/prod-live` to the deployed commit + tag;
  re-verify box == prod-live 47/47. FF-only; non-FF → STOP. **FF push command:** `git push origin
  pm-roster-tenure-2026-09-20:prod-live` (FF-only) + `git push origin <tag>`.

--------------------------------------------------------------------------------
## 7. RUNNERS (cc/, read-only)
`pm_roster_boxshas_ro` (box == prod-live), `pm_tenure_boxdump_ro.{ps1,py}` (dump jack/mlb + karen/mlb attachments +
events RO) + the local build_spans tie-out, `pm_roster_tenure_render.py` (the seeded multi-span renders +
geometry/phone measurements in `cc/renders_tenure/`). Deploy runners added in section 8.

--------------------------------------------------------------------------------
## 8. DEPLOY 14 (2026-09-21): ROSTER TENURE MULTI-SPAN — LIVE

**`origin/prod-live d1b93ce7 -> dc48d072` (code+report, FF), tag `pm-roster-tenure-deploy14-2026-09-20`.** pm_web-ONLY,
5 modified files (NO new file, NO delete, NO subdivision.py, NO migration, NO engine file). `farm_actions.py` NOT
shipped — box copy confirmed `23f91d44` == prod-live (the new loader path depends on it). ONE
`prediction-markets-web` restart; engine `trading-corp` **491380 / NRestarts 0 UNTOUCHED**. Full atomic authority
(the pm_web restart was run under the standing DEPLOY-14 restart-authority grant, verified by MainPID+timestamp).

**Files (CR-stripped sha16 BEFORE -> AFTER; box drift-gated to BEFORE, verified to AFTER):**

| file | BEFORE | AFTER |
|---|---|---|
| `web/app.py` | `0ef64011d2c223a2` | `bf9895b03ed2a7b9` |
| `web/live_view.py` | `5763057e2e757841` | `2cba1fe691bffba2` |
| `web/static/pm_desk.css` | `585ea1011a67f695` | `0b50095b62c5e7b1` |
| `web/templates/partials/pm_whale_roster.html` | `e496d37b412fc1ee` | `dd0a08cf4b8b63e7` |
| `web/templates/pm_shell.html` | `3abb631977586ba8` | `520765803e15f47d` |

**Backup (gate):** `/home/azureuser/pm_deploy14_backup_20260921T021214Z` (5 files, each verified == box before ANY
write). **pm_web PID:** 497708 (before) -> **499620** (after; ActiveEnter 2026-09-21 02:17:05 UTC), NRestarts 0.

**Steps 1-13 (all green):**
- **1-2 precheck** — engine 491380/0 active; pm_web 497708 active; schema_head **24**; 4 heartbeats fresh (3-27s);
  order rows 1703; arm rows 69; **box CR-sha16 == prod-live BEFORE for all 5 + farm_actions (ALL-OK)**.
- **4 before-render** — jack/mlb + karen/mlb each 4 on-roster whales, **spans=1, rt-spans-more absent**; every key
  **<=1 event** (one atomic-writer event on `0x41b4cd88`, span start == added_ts; the rest fall back to the attachment
  row); served pm_desk.css sha8 585ea101. Per-whale BEFORE tenure text captured for the zero-diff check.
- **5 graft** — GATE1 staged==TARGET (5), GATE2 box==BEFORE (5) + farm_actions==prod-live, GATE3 backup verified,
  APPLY 5, VERIFY written==TARGET (5), py_compile OK, **import gate: routes=24, /pm/arm=0, 0 engine/broker/execution
  modules, read_attachment_events resolved**.
- **6 restart** — az run-command (pm_web only). Confirmed by MainPID 497708 -> 499620 + ActiveEnter 02:17:05 UTC
  (NOT the az exit code); **engine 491380/0 unchanged immediately after**.
- **7 tenure** — jack/mlb + karen/mlb (4+4): each **exactly 1 span, AFTER text == BEFORE text (zero diff)**, no
  empty/"no history"/fallback badge, no rt-spans-more. The R2 fallback and the <=1-event path render identically —
  the expected current state.
- **8 toggles** — `?whales=all` 200 + formerly-live rows render `rt-formerly` + a closed span (jack/mlb has 3
  detached whales); `?sort=tenure&dir=desc|asc` 200.
- **9 R4** — Detach GET gating exact: **karen-own 200, karen-on-jack 403, no-identity 403 (GET-only, never POST)**;
  4 whales render `pm-score-flagged` live (the flagged-metric path exercised); 0 un-analyzed on live data (the
  "not analyzed" path stays unit-test-proven).
- **10 phone** — no "+N earlier spans" on either account (none exist today).
- **11 surfaces** — Deploy 12 roster-table + 5-cell strip (`sizcell`) + footer `rt-totals`; all 4 /live tabs
  standard tiles (no evt/scr scoreboard, no banner, **no nested-anchor summary [D13 lesson guard]**), shell
  `?v=0b50095b`; non-MLB (nfl) detail renders (shared template).
- **12 pages/static** — /, /live, both /account, /farm + 24 /farm/{cat}, all static assets 200; **served
  pm_desk.css sha8 == 0b50095b**; 0 raw tickers; 0 double-escaped entities.
- **13 engine** — trading-corp 491380/0 UNCHANGED across every step; 0 journalctl -p err (pm_web AND engine) since
  the restart; order rows 1703 -> 1703 (monotonic; delta = fills only).

**Post-check: 130 OK / 0 FAIL. box == prod-live 47/47** (the 7 `.bak_*`/`.orig` files dated 2026-09-01 are
pre-existing untracked box-local backups absent from git — not the deploy surface, unchanged).

**STANDING NOTE (R3):** multi-span rendering is **fixture-proven only** — every (account, category, wallet) on the
box has <=1 attachment event today, so the live page shows ONE span per whale. Extra spans (open + dimmed earlier +
the phone "+N earlier spans") first appear on prod at the **first real detach-then-re-attach** of a whale on the same
sub-division. This is correct current behaviour, not a defect.

**Runners (cc/, added for D14):** `pm_deploy14_precheck_ro.{ps1,py}`, `pm_deploy14_graft.{ps1,sh}`,
`pm_deploy14_restart_az.ps1`, `pm_deploy14_journal_ro.{ps1,sh}`, `pm_deploy14_postcheck_ro.{ps1,py}`,
`pm_deploy14_restore.{ps1,sh}` (rollback armed with the backup path; unused — no failure).
