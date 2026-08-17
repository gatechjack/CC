# Phase 2a · CP5 — paper-Telegram kill + operator-run cutover runner (BUILT, not deployed / not run)

> **LIVE-MONEY STATUS (leads):** `poly_kalshi_mlb` remains LIVE + ARMED, engine **untouched** — no
> restart, no prod mutation, no order. Part 1 is committed code (rides the CP6 deploy). Part 2 is a
> script authored + validated + logic-proven locally; **it was NOT run against prod** (per instruction).

## Part 1 — Kill the paper-farm Telegram (`main.py`)
- **Dropped ONLY the paper-branch push.** In `_handle_copy_order_placement`, the `not is_live_armed`
  branch (main.py:5057-5069) no longer calls `_push_copy_card(...)`. The line shifted from the CP2-scoping
  `:5056` to **`:5068`** after CP3's boot block; it's the same paper `tag="logged"` push.
- **Audit rail RETAINED:** the `would_have_placed` `log_event` above it is unchanged — paper trades still
  journal to the DB; only the "Polymarket copy ... logged" Telegram is silenced.
- **Live-money alerts RETAINED:** the live-armed cards (main.py:5093/5121/5127) and the poly_kalshi
  `_notify_live_copy` (poly_kalshi_executor.py:351) are untouched. The `:4194` divergence card (a
  different arb loop) is not touched.
- **Tests** (`test_polymarket_copy_e2_6_loop_wiring.py`, updated):
  - paper branch: `channel.push.assert_not_awaited()` AND `"would_have_placed" in logged` (silenced +
    audit kept).
  - live-armed branch: `channel.push.assert_awaited()` (a real placement STILL pushes).

## Part 2 — Cutover runner `pk_cutover_seed.ps1` (operator-run at CP6; authored/validated here)
Committed reviewable copy: `reports/2026-08-16_poly_kalshi_two_divisions_plan/pk_cutover_seed.ps1`.
Runnable copy placed at `C:\Users\AA Incorporado\cc\pk_cutover_seed.ps1` (Jack's CWD, where the other
`pk_*.ps1` live). Modeled exactly on the proven `pk_epoch_reset.ps1` (RG-SHARED-PROD / tc-prod-vm /
`/home/azureuser/trading_corp` / `TRADING_CORP_DB_URL` / `runuser -u azureuser -- venv/bin/python3 -` /
base64 payload).

- **VERIFY-THEN-MUTATE via a switch** (one short paste each):
  - `powershell -ep bypass -f .\pk_cutover_seed.ps1` — **DRY**: reads + displays current
    selected_whales / pinned_whales / live_whales + the planned move; **NO mutation**.
  - `... .\pk_cutover_seed.ps1 -Apply` — atomic 3-key move + read-back assert.
  - `... .\pk_cutover_seed.ps1 -Reverse` — **UNDO** (move live_whales back to selected + pinned).
- **The atomic 3-key seed:** ONE `db.set_agent_state_multi([selected_after, pinned_after, live_after])` —
  `live_whales := current selected_whales`, `selected_whales := []`, `pinned_whales := (minus the moved)`.
- **Wallets read from live state, NOT hardcoded** (honours "don't hardcode display names"): the runner
  moves the CURRENT `selected_whales` (today == the 4 live-traded whales). The **DRY run prints the exact
  wallets** for operator confirmation before `-Apply`. APPLY **aborts unless exactly `EXPECT_N=4`** whales
  are present (guard against an unexpected roster; editable if the roster legitimately changed).
- **Read-back assertion:** after APPLY it re-reads all three keys, `assert_disjoint(live, selected)`
  (invariant `live ∩ paper == ∅`), and prints `CUTOVER_OK` only if the 4 landed in live AND are gone from
  selected + pinned.
- **Preflight:** aborts if `db.set_agent_state_multi` is missing — i.e. if run before the CP2+ files are
  installed (enforces the CP6 ordering: install files -> run cutover -> restart).
- **Reversible:** `-Reverse` mode + a printed hint.

### Validation (no prod, no real DB)
- **ASCII + parse:** 0 chars >127; `[scriptblock]::Create` parses clean; saved no-BOM (src==dst 6085 B).
- **Embedded Python compiles** for all 3 modes (`ast.parse`).
- **Logic proven end-to-end** by piping the EXACT payload to `python -` (box-faithful stdin, cwd=repo)
  against a **temp** DB seeded with a mock 4-whale roster: DRY left `live_whales` untouched; APPLY seeded
  live=4 / cleared selected=0 / cleared pinned=0 / `INVARIANT_OK` / `CUTOVER_OK True`; REVERSE moved the 4
  back to selected+pinned and cleared live. **The az call was NOT executed.**

## Verification (empirical)
- **CP5 + adjacent tests: pass** (wiring 10/10 incl. the two new push assertions; roster CP2/CP3/CP4
  green).
- **Zero regressions — base-vs-branch diff:** full suite on branch tip (CP2..CP5) vs pristine base
  `386074c` → both **92 FAILED+ERROR**, `comm -13` new failures = **EMPTY**; no CP5-touched test in the
  failure set.
- **Shared byte-locked files** (`kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py`):
  `git diff 386074c` → **empty**.
- **Footprint:** `main.py` (paper push dropped + docstring), `test_polymarket_copy_e2_6_loop_wiring.py`
  (2 assertions), new `reports/.../pk_cutover_seed.ps1`. Nothing else in the branch commit.

## CP6 sequence reminder (operator-run, next checkpoint)
1. Drift-gate vs the BOX md5s. 2. Install the CP2..CP5 files (rides `8dc4d97`+`dcebfcc`). 3. Run
`pk_cutover_seed.ps1` DRY -> confirm the 4 wallets -> `-Apply`. 4. **Restart.** 5. Verify re-ARM (new
PID / auto_execute=true / dry_run=false / halted=false), live loop loads 4 from `live_whales`, paper
excludes them, boot invariant green, no PCT paper Telegram. Avoid 15:40-15:58 ET. `-Reverse` + `.bak`
rollback on failure.
