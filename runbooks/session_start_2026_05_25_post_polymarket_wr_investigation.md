# Next-session pickup prompt (polymarket watchlist clustering fix — PLANNING)

*Written 2026-05-25 ~21:30 UTC at the close of a read-only investigation session. This session: (1) traced the dashboard 100% WR sweep to root cause (clustering, NOT denominator bug); (2) verified empirically against live Polymarket APIs for Mosley1 + Runaround; (3) shipped a report + verification scripts; (4) recorded the operational pause + queued the fix as Board-gated planning work for next session. No prod state changed.*

---

Paste this into a fresh Claude Code session at `C:/Users/AA Incorporado/cc`:

---

Resuming after the 2026-05-25 polymarket WR investigation. Read-only session; **no prod state changed**; one commit pushed (`297508c`). The investigation found the dashboard's `~17/18 100.0% windowed WR` sweep is NOT a denominator bug — it's `_select_resolved_buys_window` treating each `ActivityRow` as an independent sample when 29 BUYs at the same `condition_id` are one decision repeated. Promotion is PAUSED across all windowed columns until the per-decision fix lands. **This session's job is to plan the fix — not execute it.** The fix touches live prod scoring shipped 2026-05-23 and is Board-gated per CLAUDE.md § 4.

## Where things stand (read first)

**Prod:** unchanged. Polymarket watchlist seed runs weekly Sun ~13:00 UTC against the current windowing; next fire `Sun 2026-05-31 ~13:00 UTC` is the first weekly-overwrite cycle (roster 329 → ~172 expected). The clustering bug is structural and will reproduce every cycle until fixed.

**Operational decision in force:** promotion off the Polymarket watch list is PAUSED across all windowed columns (WR, PnL, AvgPx, `<.70`). PnL+`<.70` as an interim was assessed and rejected — same cluster contamination, just with real dollars attached.

**Memory state:**
- `project_polymarket_whale_scoring_edge.md` — CORRECTED 2026-05-25: actively broken by clustering, not just near-inert.
- `project_pm_watchlist_windowed_live.md` — PROMOTION PAUSED header recorded.
- `MEMORY.md` index lines refreshed for both.

## Read first (in this order)

1. **`reports/2026-05-25_polymarket_wr_investigation.md`** — full investigation. Sections "TL;DR", "Operational status — PROMOTION PAUSED", and "Candidate fix directions (NOT being picked this session)" are the load-bearing ones.
2. **`BACKLOG.md` top EOS snapshot (2026-05-25 ~21:30 UTC)** — handoff context + commit refs.
3. **`scripts/verification/2026-05-25_polymarket_wr/`** — re-runnable empirical evidence base. `verify_wr.py` is the entry point; `results.json` has per-trade samples for Mosley1 + Runaround.
4. **`trading_corp/scripts/seed_polymarket_watchlist_deep.py`** — `_select_resolved_buys_window` is at lines 157-185. That's the fix surface.
5. **Memory (auto-loaded):** `project_polymarket_whale_scoring_edge.md`, `project_pm_watchlist_windowed_live.md`.

═══════════════════════════════════════════════════════════════════════════
## What to work on next — Board picks ONE
═══════════════════════════════════════════════════════════════════════════

### TRACK A — Polymarket clustering fix: planning session (recommended, ~60-90m, NO code changes)

This is the queued next step. Walk the cohort impact of each candidate fix direction against the current 329-row watchlist, then surface a recommendation for the Board to ratify. Three candidate directions are NOT equivalent — each interacts differently with the `n ≥ min_resolved_buys=10` floor and `n < provisional_threshold=50` provisional flag, and the cohort that survives each is different.

**Candidate fix directions (operator's lean is A, NOT locked):**

- **A. Dedupe by `condition_id` before windowing** — keep one BUY per market (most recent), window is "last 100 distinct markets." Maximum honesty. Will collapse cluster-traders' `n`; many current 100% rows will tip to provisional or under the n≥10 floor. Operator framing: "truth surfacing, not damage."
- **B. Cap same-`condition_id` slots at K of 100** — preserves higher `n` for cluster-traders but partially launders the bias. K choice (3? 5? 10?) is non-obvious and needs empirical justification.
- **C. `1 / n_buys_in_same_market` weighting** — keeps all rows in the denominator at fractional weight, preserves `n` for the floor/provisional checks but `wins/n` math becomes weighted. Most invasive math-wise.

**What "planning" means for this session:**

1. Pull the current 329-row watchlist (from `agent_state(polymarket_copy_trader, watch_only_whales)` on prod) and for each whale compute, against the same live activity data:
   - Current windowed (W, L, n, WR, PnL).
   - Under option A: distinct-market W, L, n_distinct, WR_distinct, PnL_distinct.
   - Under option B (try K=3 and K=5): bounded W, L, n_bounded, WR_bounded.
   - Under option C: weighted W_w, n_w (effective), WR_w.
2. For each option, report:
   - How many of the current 329 still pass the `n ≥ 10` floor.
   - How many tip from non-provisional → provisional (n < 50).
   - How many of the current 100% WR rows survive at >= 80%.
   - Sample 3 cluster-trader whales (Runaround, weflyhigh, surfandturf) and show their before/after numbers under each option.
3. Recommend ONE option with rationale; flag the cohort cost.
4. **STOP — do not write code.** Surface the plan as a memo to the Board (write to `reports/2026-05-26_polymarket_clustering_fix_plan.md` or similar). The fix execution is a separate, Board-approved deploy.

**Read-only.** Re-use `scripts/verification/2026-05-25_polymarket_wr/verify_wr.py` as the API-replication scaffold. No edits to `trading_corp/scripts/seed_polymarket_watchlist_deep.py`.

### TRACK B — Pivot to other open work (if the Board wants to deprioritize the clustering planning)

Other open threads, untouched by this session:

- **C-1 secret rotation** (P0 CRITICAL, ~1-3h, operator-heavy). Blocker: C-7 must be fixed first.
- **C-7 rejected-webhook audit plaintext leak** (P0 CRITICAL prerequisite).
- **Tastytrade rotation runbook** (P1 HIGH).
- **First weekly-overwrite cycle of pm-watchlist-deep timer** — passive watch; fires Sun 2026-05-31 ~13:00 UTC. Will run the broken windowing one more time; that's expected and acceptable.

See BACKLOG.md prior EOS snapshot (2026-05-25 ~18:00 UTC) for the full list with context.

═══════════════════════════════════════════════════════════════════════════
## Hard constraints carried over
═══════════════════════════════════════════════════════════════════════════

- **Polymarket watchlist seed code is Board-gated.** Do not edit `_select_resolved_buys_window` or any function it calls without explicit approval. Planning ≠ executing.
- **Promotion off the watch list is paused.** Do not advance any whale to `selected_whales` based on the current windowed columns.
- **No staleness fix bundled with the clustering fix.** Staleness self-heals on the Sunday overwrite; conflating them muddies the fix.
- **CLAUDE.md § 4 + 6 still apply.** Capped Python via `scripts\run_capped.ps1`; commits/pushes only on explicit operator ask.
