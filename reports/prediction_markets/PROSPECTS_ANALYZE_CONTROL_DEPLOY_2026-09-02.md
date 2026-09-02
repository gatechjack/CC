# Prospects [Analyze] control — DEPLOY LEDGER (pm_web)

**Deployed LIVE 2026-09-02 04:36Z** (per-step, board-authorized). Branch `pm-prospects-analyze-2026-09-02`.
pm_web only; the division stayed ARMED + TRADING throughout; the order path was never touched.

## The defect + the diagnosis (owned)
The Farm-League **Prospects** list showed each un-analyzed whale's win% with an F-1 loss-omission caveat that
said "Analyze" — but there was NO Analyze control on the row (only Promote/Refresh). **Diagnosis: a GAP, not a
regression.** Analyze was never on the prospect rows (it lives on the Watchlist rows + the whale detail page);
the loss-omission rung's caveat text EXPOSED the gap by instructing an action the row did not offer. Nothing was
deleted; this builds the control that was never there, which also makes the caveat actionable. A caveat that
named a real hole rather than papering over one — a good failure mode.

## The fix
The un-analyzed caveat IS now an ungated `[Analyze]` control (new `omission_cell` macro, shared by the prospects
row AND the analyze-result OOB fragment so the two can never drift). analyze -> the result panel shows the
omission -> an `hx-swap-oob` fragment updates THAT row's cell in place (unknown+[Analyze] -> the grounded
figure): the loop closing on the list.

## The spend design (the part worth keeping)
Analyze is a REAL LLM call now (the key is wired) against a $20/day cap. **A runaway cannot happen for THREE
independent reasons, not one** (the shape to prefer — same lesson as the narrator tier firing on a floor of one
rather than a tunable threshold):
1. the `[Analyze]` button renders ONLY on un-analyzed rows (an analyzed row shows the FIGURE, no re-run invite);
2. the OOB fragment that replaces it carries NO `hx-post`, so it cannot loop;
3. a re-click on a cached whale is a FREE cache hit (the route skips grounding + narration on a hit).
Plus: `hx-disabled-elt="this"` (no double-click double-spend), NO bulk / analyze-all, and the $20/day cap path
is byte-identical to base (untouched).

## Gating (ungated Analyze, admin-only writes) — proven live
Analyze stays UNGATED (Karen is a promotion judge too, R3): proven by REQUESTING as Karen -> `/farm/mlb` 200 with
the control present, and a non-admin analyze POST succeeds. Promote/Refresh stay admin-only: Karen POST promote ->
**403**, refresh -> **403**. The `pm-actioncell` (Promote/Refresh) and the Watchlist (Attach/Demote) are
byte-for-byte untouched.

## Deploy sequence (per-step, board-authorized)
1. **Gate-1 backup** (`~/pm_prospanalyze_backup_20260902T043321Z`): the 4 manifest files + baseline. **KEEP; do
   NOT restore onto live.**
2. **Manifest = 4 files, ZERO .py.** ★ The standing app.py M5-drift hazard was CHECKED and does NOT apply — no
   .py is in the manifest, so no engine-import file, no app.py graft, no migration, no schema change. Box-is-truth
   graft, hash-gated: 3 templates wholesale (box == deploy base `f238a5a`, LF-identical) + **pm.css grafted** (the
   box's whale-drift AND the loss-omission block both survive; my new analyze block appended, +7 lines). Live
   sha256 matched the grafted artifact for all 4 (`546b1077` / `a3c5b3ad` / `6946c46a` / `4a4b19df`). Gate-A green
   on the exact artifact (import closure + 66 tests, real env).
3. **pm_web restart** (az-root, `prediction-markets-web` only): PID **153559 -> 155543**. **Engine 144229
   UNCHANGED**, no bitunix bounce.
4. **Post-verify (live, quoted):** un-analyzed row `0xd1acd…` shows the `[Analyze]` button (the defect closed);
   an analyzed row `0x21f52494cd…` shows the figure (`omit 0% verified`) with NO button; ungated for Karen
   (requested AS Karen -> 200 + control); Promote/Refresh 403 for Karen; all pages 200; pm_web PID changed,
   engine unchanged, arm untouched (ts 2026-08-31T21:49:39), orders 14->15 (legit R8 fill -- analyze places none).

## STOP conditions — none triggered
Engine PID did not move; Analyze not gated; Promote/Refresh still 403 for a non-admin; no page 500'd.

## ★ NIT FILED (the grounding-null dead-end) -> see [[prediction-markets-backlog]]
If a whale's /activity has no in-category held-to-resolution decisions (or grounding otherwise fails) while the
LLM verdict succeeds, the verdict caches but NO grounding-cache row is written -> the row keeps its `[Analyze]`
button, and a normal (non-force) re-click is a free cache hit that does NOT re-ground (only `?force=1`
re-grounds). The control is present and pressing it changes nothing -- a quiet dead-end. Observed live on
`0xd1acd…/mlb` (0 mlb held decisions -> honest "omission unknown", correctly no fabricated figure). Pre-existing
(the cache/grounding split predates this rung) and correctly out of scope here, but it WILL confuse the first
time it is hit. Filed in the backlog.

## Branch / prod-live
Branch `pm-prospects-analyze-2026-09-02` (this ledger appended). **prod-live `7220e32` NOT advanced** (box-is-truth;
file-by-file when advanced). Local `cc\` runners (`pm_prospanalyze_s1..s4`, `pm_prospanalyze_gatea`, `_boxgraft2/`)
are the operational record -- KEEP, untracked.
