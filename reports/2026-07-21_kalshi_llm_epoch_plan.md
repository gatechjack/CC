# kalshi_llm_arbitrage dashboard epoch — investigation + plan (READ-ONLY, no deploy)

**Date:** 2026-07-21 · No prod/agent_state/config/memory/code changes this session. Plan for operator approval.

## §1 — Epoch mechanism investigation
Two independent scoping mechanisms exist in `web/data.py`:

1. **`DASHBOARD_RT_CUTOFFS`** (data.py:3800) — hardcoded Python dict, `{division: ISO}`, applied to **round_trips-based** metrics via `_kalshi_cutoff_clause("entry_ts")` as `AND NOT (division='X' AND entry_ts < cutoff)`. Currently only `kalshi_weather` + `kalshi_crypto`. **No `kalshi_llm_arbitrage` entry** (deploy_log 5433 confirms). Code constant → change needs a restart; not runtime-tunable.
2. **`_get_metrics_epoch(db_url, agent)`** (data.py:1281) — `agent_state(<agent>,'metrics_epoch')`, hot. For kalshi non-copy divisions it is consulted **only** by the paper_trade_record win-rate panel (data.py:1431), which has **0 rows for kalshi_llm** → inert. It is **NOT wired** into the round_trips-based metrics for this division. (Polymarket uses `_get_polymarket_metrics_epoch`; kalshi_copy uses `_get_kalshi_copy_live_epoch`; kalshi_llm uses neither for its round_trip metrics.)

**Why no epoch is set:** kalshi_llm's round_trip metrics scope only through `DASHBOARD_RT_CUTOFFS`, and no entry was ever added. Setting `agent_state(kalshi_llm_arbitrage, metrics_epoch)` alone is **inert** (nothing on the round_trip path reads it).

**Smallest change mirroring the existing pattern:** add a `DASHBOARD_RT_CUTOFFS['kalshi_llm_arbitrage']` entry — exactly the weather/crypto pattern (scopes round_trip metrics; does NOT scope the OPEN tab — see §2).

## §2 — What renders kalshi_llm stats & where the epoch applies
Epoch applies on **`entry_ts`** (all existing mechanisms use entry_ts; resolution_ts scoping would show the legacy drain — the opposite of the goal).

| Metric | Function (data.py) | Cutoff applied today? |
|---|---|---|
| Tile roll-up (resolved/WR/PnL) | `_pm_overview` :1073 | ✅ `_kalshi_cutoff_clause("entry_ts")` |
| Summary card | `_query_pm_resolved_stats` :4567 | ✅ same |
| History list (per-row, incl category) | `_query_pm_round_trips` :4096 | ✅ same |
| "since" badge | `_pm_summary` :5635 | ✅ from `DASHBOARD_RT_CUTOFFS` |
| **OPEN tab** | `_query_pm_open_trades` :4344-4358 | ❌ **NO cutoff** — filters on `a.ts` + copy-mode clause only |
| Equity | `kalshi_equity_history` | ❌ not epoch-scoped (flat $532.84 cash for llm anyway) |
| Per-category **aggregate** | (none found) | n/a — no dashboard panel; category shows per-row in history only |

- **Coupling — SAFE.** Shared functions serve the "All" aggregate + siblings, but `_kalshi_cutoff_clause` is **per-division-guarded** → adding kalshi_llm affects only kalshi_llm rows. Any open-tab filter must be equally division-guarded.
- **No downstream/safety coupling.** Autopause reads `kalshi_round_trips` only for the copy divisions (`division='kalshi_copy_trading'` / `polymarket_copy_trading`), never kalshi_llm. No other safety guard reads kalshi_llm stats. main.py/resolver only WRITE.
- **Gap:** the OPEN tab is not scoped by the cutoff (same as weather/crypto). To scope "ALL reported metrics" incl. open, a small extra clause is needed beyond the established pattern.

## §3 — Change plan
- **Option A (agent_state only, no code): NOT VIABLE.** kalshi_llm's round_trip metrics don't read `_get_metrics_epoch`; the write would be inert. (Flagged.)
- **Option B (recommended) — mirror the DASHBOARD_RT_CUTOFFS pattern:**
  - **B-core (round_trip metrics):** add one line to `DASHBOARD_RT_CUTOFFS` (data.py:3807): `"kalshi_llm_arbitrage": "2026-07-07T16:40:00+00:00",`. Auto-scopes tile + summary + history + badge (per-division-guarded). Exactly the weather/crypto pattern.
  - **B-open (optional, to also scope the OPEN tab):** add a division-guarded `AND NOT (payload.division='kalshi_llm_arbitrage' AND a.ts < '<epoch>')` clause to the kalshi branch of `_query_pm_open_trades` (~4357). Without it, OPEN stays 1,461.
  - Both are `web/data.py` edits → **flat-guarded engine restart** (web in-process). Epoch is a code constant (later changes = code change + restart).
- **Option B2 (bigger, hot):** wire `_get_metrics_epoch(db_url,'kalshi_llm_arbitrage')` into `_kalshi_cutoff_clause` + the open query so the epoch is agent_state-hot (runtime-tunable like polymarket). More code/coupling surface; buys runtime adjustability. Offer if the operator wants to tune the epoch without redeploys.

**Recommended:** B-core + B-open (minimal complete scoping). Files: `web/data.py` only (1-line dict entry + 1 open-tab clause). No config/agent_state/memory.

**Verification (before → after, 16:40 epoch):**
| | before | after |
|---|---|---|
| Resolved / WR / PnL | 2,686 / 40.3% / −$472.67 | **0 / n-a / $0** |
| OPEN tab | 1,461 | **144** (Econ 91, Elections 53) — only with B-open |
| Equity | $532.84 | $532.84 (unchanged) |
| Badge | (none) | "since 2026-07-07" |

**Rollback:** delete the dict entry (+ open clause) + restart → full history restored. Pre-cutoff rows never deleted; fully reversible.
**Restart:** required (code change). Not hot unless B2.

## §4 — Preview (dry-run, entry_ts >= epoch)
Post-07-07 **entries have 0 resolved round-trips** (last resolved entry was 2026-06-08), so an entry_ts epoch yields an **empty resolved scoreboard** that populates as the new Econ/Elections positions settle (first ~Aug 5):

| Metric | entry_ts ≥ 07-07 **00:00** | entry_ts ≥ 07-07 **16:40** (deploy) |
|---|---|---|
| Resolved | **0** | **0** |
| Wins / Losses / WR | 0 / 0 / n-a | 0 / 0 / n-a |
| Realized gross / net-fee / net-slip | $0 / $0 / $0 | $0 / $0 / $0 |
| Open (by category) | **159** — Econ 97, Elec 53, **Politics 8, Sci&Tech 1** | **144** — Econ 91, Elec 53 |
| Equity | $532.84 (not scoped) | $532.84 (not scoped) |

The 00:00 boundary lets **9 old-discovery entries** (8 Politics + 1 Sci&Tech, entered 07-07 before the 16:40 config hot-reload) into "open." The **16:40 boundary is the clean new-logic-only cut.**

## §5 — Operator-decision fork (no deploy until you confirm)
1. **Mechanism:** B-core only (metrics scoped, open tab stays 1,461 — matches weather/crypto) · **B-core + B-open** (also scope open to 144, recommended for "ALL metrics") · B2 (agent_state-hot, more code).
2. **Boundary:** **2026-07-07T16:40:00Z (recommended** — clean Econ/Elections-only, 144 open) vs 2026-07-07T00:00Z (159 open, includes 9 old-discovery strays).
3. **Confirm the preview is what you want:** the scoped dashboard shows **0 resolved / $0 / 144 open** now — a clean forward scoreboard that stays near-empty until the Econ/Elections positions settle (~Aug). Confirm that's the intent (vs. e.g. waiting until some settle, or keeping full history).

*Guardrails honored: read-only; no prod/agent_state/config/memory/code changes; no edge/viability characterization; coupling checked (safe); stopped at the fork.*
