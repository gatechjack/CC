# CP5 report — agent_state metrics-epoch for the kalshi division

**Status: BUILT, NOT DEPLOYED. Checkpoint STOP — awaiting operator review before CP6.**
Branch `poly-kalshi-mlb-phase1-2026-08-15`, built on the CP4 tip.

## Live-money / live-loop status (lead)
- **Zero live activity.** No order placed, no prod mutation, no restart. Branch-only; the running engine sees none of it until CP7.
- **Live loop UNDISTURBED** (no prod shell; nothing deployed).
- **Shared files byte-unchanged** — `git diff origin/prod-live` on `kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py` is **empty**.

## What CP5 delivers (`data.py` only, +58 / −22; resolver untouched)

### 1. Runtime, reversible per-division epoch (mirrors the polymarket mechanism)
- `_get_kalshi_division_epoch(db_url, slug)` (`data.py:3978`) — reads `agent_state[<slug>/metrics_epoch]` via the existing ISO-validated `_get_metrics_epoch`. Mirror of `_get_polymarket_metrics_epoch`, keyed on the division slug (which IS the audit actor, e.g. `poly_kalshi_mlb`). None when unset.
- `_kalshi_cutoff_clause` (`data.py:3990`) now resolves the **effective** per-division cutoff: an `agent_state` epoch **takes precedence when set**, else the hardcoded `DASHBOARD_RT_CUTOFFS` entry. It gained optional `division_slugs`/`db_url` kwargs; **called with neither (the default) it behaves EXACTLY as pre-CP5** (hardcoded dict only) — so the cross-division overview (`data.py:1115`) and the rollback/monkeypatch tests are unchanged.
- Wired into the three per-division `kalshi_round_trips` reads (History + both tile aggregations): `data.py:4293`, `:4868`, `:4929`.
- **Injection-safe:** agent_state epochs are ISO-validated (`datetime.fromisoformat` round-trip in `_get_metrics_epoch`); hardcoded cutoffs are literals — both inline safely, the same pattern as `_polymarket_cutoff_clause`.

### 2. The 10 pre-existing dashboard failures — FIXED (real fix, cutoff intact)
- **Root cause (confirmed):** `_insert_kalshi_round_trip` / `_insert_poly_round_trip` default `entry_ts='2026-05-11'`, and one test's inline audit `ts='2026-05-11'`, all predate `DASHBOARD_RT_CUTOFFS['kalshi_llm_arbitrage']='2026-07-07'`, so the (correct) cutoff filtered the fixtures out. The cutoff-specific tests (`test_kalshi_cutoff_clause_*`, the seed-cutoff filter tests) were insulated — they monkeypatch the dict and use explicit dates.
- **Fix:** bumped the two shared-helper default dates + that one test's two inline dates to post-cutoff `2026-08-11`, **preserving relative orderings** (poly kept newer than kalshi so the all-mode `resolved_ts`-DESC sort assertions still hold). The fixtures now represent current-regime rows; nothing about the cutoff logic changed.
- **Proof it's a real fix, not a hidden/disabled cutoff:**
  - New `test_existing_kalshi_division_cutoff_unaffected_without_agent_state`: with no agent_state epoch, a `2026-05-11` kalshi_llm row is **still filtered** and a `2026-08-11` row shows — the hardcoded cutoff still bites.
  - The cutoff-specific tests still pass unchanged.
  - `_kalshi_cutoff_clause` still emits `AND NOT (division=… AND ts < cutoff)` for every cutoff (hardcoded or agent_state).

### 3. Symmetric OPEN tab + pending badge epoch (folded into CP5 per operator go)
The agent_state epoch now scopes the OPEN/badge audit-event path too, so a set epoch
hides pre-epoch rows uniformly across **all four** surfaces (History, resolved tiles,
OPEN list, pending badge).
- New `_kalshi_division_epoch_clause(division_slugs, db_url, *, ts_col, div_col)`
  (`data.py:4021`) — the audit-path counterpart of `_kalshi_cutoff_clause`: per passed
  slug, emits `AND NOT (<div_col>='<slug>' AND <ts_col> < '<cutoff>')` where the cutoff
  is the agent_state epoch (precedence) else the hardcoded `DASHBOARD_RT_CUTOFFS` entry.
  SCOPED to the passed slugs and `div_col`-parameterized (the audit path filters on
  `json_extract(...,'$.division')`, not a bare column — same shape as
  `_polymarket_cutoff_clause`).
- Wired **identically** into the poly_kalshi OPEN block (`_query_pm_open_trades`,
  `data.py:4678`) and the poly_kalshi badge block (`_query_pm_pending_count`,
  `data.py:4827`) — same clause on both → **badge == list preserved by construction**.
- **Arb OPEN/badge path byte-unchanged:** the clause is scoped to poly_kalshi slugs and
  never runs for arb queries; the inline `_llm_cut` code is untouched (the only `_llm_cut`
  in the diff is a docstring mention). Proven: `test_arb_open_badge_unaffected_by_poly_kalshi_epoch`.
- **The 3 pre-CP3 orders (MIA/CIN/AZ) — precise status:** their pre-CP3 audit rows carry
  NO `division`/`order_id`, so the CP3 OPEN filter ALREADY excludes them (with or without
  an epoch). The epoch governs POST-CP3 pre-split OPEN rows; for these three it is
  belt-and-suspenders. Proven: `test_pre_cp3_open_row_absent_from_open_regardless_of_epoch`.

**OPEN/badge epoch set/unset/delete (empirical, two post-CP3 rows straddling the epoch):**
```
no epoch (unset)   : OPEN list=['post', 'pre']  badge=2  badge==list:True
epoch=2026-08-15   : OPEN list=['post']         badge=1  badge==list:True
epoch deleted (rev): OPEN list=['post', 'pre']  badge=2  badge==list:True
```
Set → pre-epoch OPEN row drops from list AND badge; badge==list holds throughout;
delete → restored (reversible).

## Evidence
- **Epoch set/unset/delete (empirical, poly_kalshi_mlb, two rows straddling the epoch):**
  ```
  no epoch (unset)   : History=['post', 'pre']  tiles n_resolved=2
  epoch=2026-08-15   : History=['post']         tiles n_resolved=1
  epoch deleted (rev): History=['post', 'pre']  tiles n_resolved=2
  ```
  Set → pre-epoch row drops from History AND tiles; delete → fully restored (rows never deleted). Runtime, reversible, per-division.
- **agent_state precedence over hardcoded:** `test_kalshi_division_epoch_overrides_hardcoded_cutoff` — an EARLIER agent_state epoch un-hides a row the `2026-07-07` hardcoded cutoff would hide.
- **Existing kalshi divisions unaffected:** the test above + `test_existing_kalshi_division_cutoff_unaffected_without_agent_state` (hardcoded `2026-07-07` still filters when no agent_state epoch).
- **Dashboard suite: 61 passed, 0 failed** (the 10 pre-existing failures fixed + 5 round-trips-epoch tests + 4 symmetric OPEN/badge-epoch tests). Full file `--tb=no` → `61 passed`.
- **Resolver + reconciliation + poly_kalshi (3 files): 0 failures / 0 errors.**
- **Shared files:** `git diff --stat origin/prod-live` empty.

## Notes / deliberate boundaries
- **Cross-division overview** (`data.py:1115`, `WHERE 1=1 … GROUP BY division`) is not per-division-scoped, so it keeps hardcoded-cutoff-only behavior (back-compat). The agent_state epochs apply to the per-division dashboards (History + tiles) the operator asked to mirror.
- **OPEN tab / pending badge** — now SYMMETRIC (folded in per operator go; section 3). The agent_state epoch scopes all four surfaces (History, tiles, OPEN, badge) uniformly; the arb audit path / `_llm_cut` is byte-unchanged.

## NOT done (do not proceed without your go)
- **CP6** (reset BOTH epochs to the split date via an operator-run runner; verify both dashboards read 0 from the epoch, on-disk history retained), **CP7** (deploy + real-data gross-vs-net confirmation, operator-run) — not started.
- **Phase 2** — not started.

## Next
Your review. OPEN/badge symmetry is now folded in — all four surfaces honor the same agent_state epoch. CP6 is a separate go.
