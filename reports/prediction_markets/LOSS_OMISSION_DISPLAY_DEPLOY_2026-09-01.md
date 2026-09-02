# Loss-omission % beside win% — DEPLOY LEDGER (Stage 5, pm_web)

**Deployed LIVE 2026-09-02 03:44Z** (per-step, board-authorized). Branch `pm-loss-omission-display-2026-09-01`.
pm_web only; the division stayed ARMED + TRADING throughout; the order path was never touched.

## What shipped
The screening win% on the Analyze card and the Prospects list now carries its F-1 **loss-omission %** (and the
**coverage** behind it) RIGHT BESIDE the number — so a whale that screens as a near-lock only because
/closed-positions dropped its held-to-worthless losses is visible before it is pinned or copied. The reason this
rung exists, now on the live page: **SDTrading — the whale Jack-MLB copies at 5 contracts/signal — screens ~94%
and drops 94% of its losses; the page now says so next to the 94%.**

- **`loss_grounding.py`**: `LossGrounding` + `coverage_pct`/`n_closed` (`|closed re-found in the /activity window| /
  |closed|`). pm_web-only (not in the engine import closure). Narration unchanged -> no skill_version bump.
- **`analyze.py`**: report `loss_coverage_pct` (display-only) + `loss_is_floor` property + `LOSS_COVERAGE_FLOOR=0.90`.
- **`db.py`**: migration **017** -> `pm_loss_grounding_cache` (per-whale, PK wallet+category, `grounded_ts` = the
  figure's own age). Additive `CREATE TABLE IF NOT EXISTS`; the engine never reads it.
- **`web/app.py`**: Analyze (the one grounding site) upserts the omission cache; the Prospects loader reads it. An
  un-Analyzed whale has **no row -> UNKNOWN, never 0%**.
- **templates + `pm.css`**: omission rides IN the win% stat card (Analyze) and IN the win% cell (Prospects),
  beside the number; win% shown-with-caveat (not struck/suppressed); ranking stays cost-ROI (win% non-sortable).

## Design decisions (as ruled/built)
- **Where the figure comes from**: computed on Analyze (which already pays the /activity+gamma fetch), cached
  per-whale, read by Prospects. Grounding 131 rows on render is not viable; a background job would hammer the
  shared prod IP.
- **Un-grounded whale = UNKNOWN, never 0%** (three distinct states: unknown / verified-0% / material).
- **Win% treatment = shown-with-caveat** (dotted-underline de-emphasis), not struck/suppressed.
- **Coverage bound carried** so "94% @ 96% cov" and "94% @ 31% cov (floor)" never flatten.
- **Narrator**: the tier fires on `a_only_losses > 0` — **a floor of ONE, not a magnitude threshold** — so 94%
  absolutely triggers it and there is no number to tune and **no way for it to quietly stop firing**. No change.

## ★ Review finding recorded (a caveat that silently stopped caveating)
The adversarial review's one SHOULD-FIX: the `(floor)` lower-bound marker was keyed on **truncation** rather than
**low coverage**, so a whale with low coverage but no page-ceiling truncation would have rendered as a **complete
measurement** — and the test had **masked it** by setting truncation + low coverage together. This is the same
class this build set out to defeat: **a caveat that silently stops caveating.** The fix for a display-honesty
problem nearly shipped with its own honesty hole. **Fixed before ship** (`2f13b7d`): floor = truncated OR
coverage<0.90, centralized in the report property + cell helper, with de-coupled tests. This is a new instance
alongside the other members of the fails-open / stops-checking class.

## Deploy sequence (per-step, board-authorized)
1. **Gate-1 backup** (`~/pm_lossomit_deploy_backup_20260902T023308Z`): live DB sha256 + `integrity_check ok` +
   schema 16 baseline + the 7 box file shas + PIDs/arm. **KEEP; do NOT restore onto live** (restoring reverts
   schema 17 + the graft).
2. **Graft (box-is-truth, file-by-file):** 5 files were byte-identical to f1e28cc (LF) -> deployed my versions; **2
   had real box drift and were GRAFTED** — `app.py` (M4+whale, no M5) got my 2 hunks patched on, and `pm.css` (whale
   drift) got my block appended. **M5 guard held: box app.py is_admin stayed 10, `/pm/arm`=0** (my hunks add no
   is_admin; HEAD's 12-count M5 plumbing never reached the box). Gate-A on the exact grafted artifact: import
   closure clean (no new engine import) + 79 tests green.
3. **Migration 017** (schema 16->17). **★ Anomaly (benign):** the 03:30Z `paper-poll` cron ran first with the
   grafted `db.py` (SCHEMA_HEAD 17) and applied the migration; my explicit `init_db` then no-op'd. Result correct:
   table present, 0 rows, exact 11 columns, index present, `integrity ok`. Recorded so the schema jump does not
   read as spontaneous.
4. **pm_web restart** (az-root, `prediction-markets-web` only): PID **145927 -> 153559**. **Engine 144229
   UNCHANGED**, no bitunix bounce.
5. **Post-verify (live)**: schema 17; all pages 200; **Analyze SDTrading quoted `win%* 94% -94% losses missing @
   100% cov`, win%<->omission byte-offset 362 (<900) = same card**; SDTrading cache row omission 0.9413 cov 0.9981
   a_only 497 honest 501W/528L; **un-analyzed candidate `0xd1acd3925d... 75% -> "omission unknown / Analyze"`, no
   fabricated 0%** (10/10 mlb prospect rows unknown); coverage renders, `(floor)` correctly absent for a
   well-covered whale; pm_web PID changed, engine unchanged, arm untouched (ts 2026-08-31T21:49:39), orders 11->14
   (legit R8 fills).

## STOP conditions — none triggered
No M5 on the box; schema landed at 17 only; no page 500'd; engine PID did not move; no un-analyzed whale showed 0%.

## Branch / prod-live
Branch `pm-loss-omission-display-2026-09-01` (this ledger appended). **prod-live NOT advanced** (box-is-truth; when
advanced it is file-by-file against the box, never a branch or ledger advance). Local `cc\` runners
(`pm_lossomit_s1..s5b`, `pm_lossomit_gatea`, `_boxgraft/`) are the operational record — KEEP, untracked.
