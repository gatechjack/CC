# T1 RUNG 1 — shared-module gate + 2-file deploy + pm_web restart — 2026-08-27

**Authorization (Jack):** "T1 RUNG 1 — AUTHORIZED, WITH ONE GATE BEFORE THE DEPLOY … the shared-module impact
check, then the 2-file deploy and pm_web restart." T1 rung 2 (poller run), cadence, and any prod-live movement
remain unauthorized. Executed **18:42–18:45 UTC** (clear of 03:20; nothing else wrote the PM DB). Deployed from
`prediction-markets-stage0-2026-08-26` @ **216d8cc**.

## ★ GATE (done before any deploy step) — CLEAN
`polymarket_data_api_client.py` is a shared module. Concrete impact of the change (fetch_positions now RAISES
`PolymarketIncompletePositionsError` where it returned a silent partial; returns the full book, ~2745 vs ~1400):

- **Every `.fetch_positions(` caller, repo-wide + on the box:** exactly **`prediction_markets/ingest.py:304`**
  (`refresh_open_positions`, PM) + `poll_pinned` (PM, now on `fetch_positions_book`) + my tests. **`refresh_open_positions`
  has ZERO callers** (defined, never wired) — so the raise reaches only PM, and no live path invokes it today.
- **The change does NOT reach the engine or poly_kalshi_mlb.** Confirmed on the box: `poly_kalshi_copy_trader.py:155`
  and `polymarket_copy_trader.py:271` call **`.fetch_activity(`**, not `fetch_positions`; the engine `web/routes.py`
  uses `fetch_activity`/`fetch_market_resolutions`/`fetch_leaderboard`; `main.py` opens the client but calls neither.
  All read whales via `/activity`, not `/positions` — my change is invisible to them.
- **New symbols** (`fetch_positions_book` / `PolymarketIncompletePositionsError` / `PositionBook`) are referenced by
  **nothing outside the client + `paper.py`** (grepped repo + box).
- **No shared-module drift:** the box's client blob = **`4f04cee`** = the branch BASE (c22a82d) — my diff applies
  base→new cleanly; the diff is isolated (+2 symbols, `fetch_positions` refactor + `fetch_positions_book`, no other
  method touched).
- **Verdict: reaches only PM; nothing engine-side or poly_kalshi-side is affected → proceed** (Jack's STOP-if-reaches-
  poly_kalshi/engine condition did not trigger).

## Deploy set — 2 files, both genuinely changed vs the box
`trading_corp/data/polymarket_data_api_client.py` (box **4f04cee → 719ad12**) + `trading_corp/prediction_markets/paper.py`
(box **7f6caea → 859dec6**). **NOT** `ingest.py`, **NOT** `persistence/db.py`. (At Stage-1 rung 2, 2 of 6 candidate
files were already-identical; here both 2 are real changes.)

## Pre-conditions (all matched)
origin/prod-live **c77f618** · box blobs == branch base (client 4f04cee, paper 7f6caea) · schema 9 · grace 259200 ·
pm_paper_trade **107 (104 open / 1 pending / 2 closed)** · pm_paper_category_stats **6** · **15 cats / 92 pairs** ·
/farm 200/**130199** · engine **676** / pm_web **13102** · UTC 18:42.

## Deploy (fail-closed)
custody → **manifest-assert** (scratch blob == branch-new 719ad12/859dec6) → **pre-place** (box blob == branch-base,
fail-fast) → **per-file CODE backup** `~/pm_t1_codebak_20260827T184320Z/` (both at base blobs; rollback = restore +
restart) → place + **forced `chmod 644`** (the tar-664 drift recurred, expected — standing step) → **re-hash gate**
(target blob == new AND perms == 644, owner azureuser) — **PASS on both.**

## Restart
`az vm run-command` (root) `systemctl restart prediction-markets-web.service`: pm_web MainPID **13102 → 24808**,
ExecMainStatus 0, active/running, /healthz 200. **★ Nuance (reported):** `pm_web` does **not** import `paper.py` or
the client (its chain is web → stats/positions/names/farm/analyze → stats only), so this restart is a **health
checkpoint**, not a code-load for these files — the new client/paper code activates on the next `pm_cli` invocation
(the poller/adjudicate/rollup = **Rung 2, unauthorized**). Importability was verified in the venv directly
(`IMPORT_OK`: new symbols load; `poll_pinned` source uses `fetch_positions_book` + the `incomplete` gate; no fetch
invoked). **The engine** (`trading-corp.service`, PID 676) also imports the shared client on disk but keeps its OLD
in-memory copy until it restarts — behaviour-neutral (it uses `fetch_activity`, unchanged); **no engine restart
needed or authorized.**

## Post-checks — nothing moved
pm_web PID **24808** (changed), ExecMainStatus 0 · engine **676** unchanged · /healthz + /farm 200 · /farm
**130199 byte-identical** · deployed blobs client **719ad12** / paper **859dec6** · schema **9** · grace **259200** ·
pm_paper_trade **107 (104/1/2)** · pm_paper_category_stats **6** · **15 cats / 92 pairs**. **Nothing fetched, nothing
moved — correct** (a fetch happens only when the poller runs, which is Rung 2). No STOP condition.

## prod-live ledger (authored, NOT pushed)
Local `prod-live`: 570727b (rung-2 ledger) → **8563c62** (path-checkout of the 2 T1 files from 216d8cc; blobs == box;
parent 570727b, **c77f618 remains an ancestor**, fast-forward-only). **Not pushed — origin/prod-live stays c77f618**
(now behind by the rung-2 + T1 ledger commits; Jack authorizes the advance as its own step).

## Still unauthorized
**T1 Rung 2** (the one manual poller run — where the completeness gain lands: ~1345 previously-hidden positions
across the 6 capped whales become visible, and the in-pinned-category ones get captured) · cadence install ·
advancing origin/prod-live.

Runners: `cc\pm_t1_gate_box.sh`, `cc\pm_t1_deploy_pre.sh`, `cc\pm_t1_deploy.sh`, `cc\pm_t1_deploy_post.sh`.
