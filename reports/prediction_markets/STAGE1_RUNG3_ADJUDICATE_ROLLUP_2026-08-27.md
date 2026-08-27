# Stage 1 — RUNG 3 STEP 3: LIVE ADJUDICATE + ROLLUP — 2026-08-27

**Authorization (Jack):** "RUNG 3 — STEP 3 OF 4: LIVE ADJUDICATE, THEN ROLLUP … ONE live adjudicate run followed by
ONE live rollup run." Cadence install (step 4) remains **unauthorized**. Ran **17:39–17:41 UTC** (clear of 03:20;
nothing else wrote the PM DB). Both are the normal invocations: `pm_cli paper-adjudicate`, then `pm_cli paper-rollup`.

## ★ Design question answered BEFORE running (D1 — whale-exit disposition) — Jack's to rule
**What `adjudicate()` does with a whale-exit row, from the code (`paper.py:379-410`, `_past_grace` 327-331):** the
two-phase design does **not** distinguish "whale sold" from "market settled" — a `/positions` row drops on both, so
the poller marks it `pending_adjudication` either way. `adjudicate()` then decides **purely off gamma, never off the
whale's action**: gamma `resolved` → book won/lost on the **market outcome** (as if held to resolution); `void` →
void; else **and** past `market_end+72h grace` → `stale`/`close_source='whale_exit'`/**no P&L** (bias down); else →
stays pending. For the mlb SF@ATL row (gamma pending, end 2026-09-07, `now−(end+72h)=−1.1M s` → within grace) it
**stays pending**, and will until the game resolves (booked on the **outcome**, not the exit) or Sept-7+72h passes
unresolved (→ stale, no P&L). The label `whale_exit` is a slight misnomer (fires on "vanished + never resolved",
not "sold").

**Cost of the current behaviour:** for any whale who exits before resolution we score the **market outcome, not
their round-trip** — booking a loss they avoided (sold a winner that then reversed) or a win they never took (cut a
loser that later won). It penalises whales who are good at *timing exits* and injects outcome-noise unrelated to
skill; 1 of this run's 3 pending rows is in this class, so it is not rare.
**Alternative (copy in AND out):** capture the last-observed price when a position vanishes and, if the market is
still live, close the paper trade at that exit price; fall through to resolution only if the market had already
settled. **Trade-off:** we never see the exit fill (only the last poll's `curPrice`, ±poll interval — the same
observation bias as entry) and still can't perfectly tell "sold" from "redeemed-after-settle"; it swaps
hold-to-resolution bias for approximate-exit-price bias and needs a code change (exit-price capture + a new adjudicate
branch). **Filed as D1 in `PM_OPEN_TICKETS_2026-08-27.md`. NOT implemented — awaiting Jack's ruling.**

## Two findings filed (not fixed) — `PM_OPEN_TICKETS_2026-08-27.md`
- **T1 — `/positions` page cap (Ruling H):** 6/14 whales returned exactly 100 rows → captures for the highest-volume
  accounts are a **lower bound**. Filed with Jack's framing: **same class as the `/closed-positions` loss omission
  that started the rebuild** — a truncated feed under-representing exactly the accounts we most want. Own ticket.
- **T2 — tier-2 priority LOWERED:** measured **~0 miss** this run (0 trackable game lines skipped of 90) vs the ~6.3%
  estimate → the estimate is an upper bound, not current; recorded so 6.3% isn't treated as live. Ticket stays open,
  de-prioritised.

## Pre-conditions (all matched) + rollback
schema 9 · grace 259200 · pm_paper_trade **107 (104 open / 3 pending / 0 closed)** · pcs **0** · watchlist
**114/92/22** · /farm 200/129578 · PIDs 13102/676 · UTC 17:39. The 3 pending: mlb·SDTrading (cost 54.25, end
09-07), ufc·evanng (cost 88.61), ufc·Kh4mz4t (cost 10.46). **Gate-1 backup (rollback for both writes):**
`~/pm_stage1_rung3_adj_dbbackup_20260827T173915Z.db` (sha `d468c563…`, `integrity_check=ok`).

## RUN 3a — LIVE ADJUDICATE
`{pending_in:3, closed:2, voided:0, staled:0, still_pending:1}`; C2.3 subset 14/14.
| row | verdict | P&L | vs dry run |
|--|--|--|--|
| ufc·evanng (ronhum, 0xc273d469) | **closed, won=1**, `gamma_resolution` | **+11.39** | dry +11.39 → **EXACT MATCH** |
| ufc·Kh4mz4t (garbal, 0x640427f9) | **closed, won=1**, `gamma_resolution` | **+89.54** | dry +89.54 → **EXACT MATCH** |
| mlb·SDTrading (SF@ATL, 0x2308d78d) | **still pending_adjudication** | — | whale-exit; gamma pending, within grace |
**No divergence between dry run and live — same verdicts, same P&L to the cent.** Status after 3a: open 104,
pending 1, closed 2 (total 107). Both closures positive (correct sign).

## RUN 3b — LIVE ROLLUP
`rolled_pairs=6`; pm_paper_category_stats **0 → 6**.
- **★ R1 in production (first live exercise): PASSED** — `PCS_ROWS_NOT_ACTIVE_PINNED=0`,
  `PCS_ROWS_IN_DEACTIVATED_PAIRS=0`. No deactivated pair produced a stats row.
- Rows: cs2·1, fed·1, mlb·1, ufc·3. The 2 closed reconcile by hand:
  - ufc·evanng: n_closed 1, wins 1, **win_rate 1.0**, net **+11.39**, cost 88.61, **roi 0.128541** (=11.39/88.61 ✓)
  - ufc·Kh4mz4t: n_closed 1, wins 1, **win_rate 1.0**, net **+89.54**, cost 10.46, **roi 8.560229** (=89.54/10.46 ✓)
  - open-only pairs (cs2/fed/mlb/ufc·4751346): n_closed 0, win_rate/roi NULL, n_open 1/5/2/2 — correct.

## POST-VERIFY
- **★ /farm 129578 → 130199 B — the pinned list shows its FIRST paper-lane number.** ufc pinned display:
  **evanng `win_rate=1.0, roi=0.1285, net=+11.39`**, **Kh4mz4t `win_rate=1.0, roi=8.560, net=+89.54`**; 4751346
  `n_open=2` (no closed yet); the other 7 ufc pairs honest-empty. (roi 856% = the fixed 100-contract `size_basis`
  buying a 0.1046 underdog that won — expected, not the whale's sizing.)
- **Prospects unchanged:** pm_category_stats named MD5 `c2b7d926a6f8a720ffc4af533144de55` == Rung-2 baseline.
- 15 categories / 92 pairs · schema 9 · grace 259200 · watchlist 114/92/22 · PIDs 13102/676 — all unchanged.

## STOP conditions — none triggered
Live verdicts == dry run (both ufc exact) · 0 deactivated pairs in pcs · prospects MD5 unchanged · no wrong-sign
P&L. **Clean.**

## State after step 3
pm_paper_trade 107 (104 open / **1 pending** [mlb whale-exit] / **2 closed**) · pm_paper_category_stats **6 rows**
(2 with real closed stats, both ufc) · the paper lane is live end-to-end (poll → adjudicate → rollup → /farm).

## Still unauthorized
**Step 4 — cadence install** (poller `*/30`, adjudicate+rollup daily post-03:20, order poll→adjudicate→rollup —
RULED, not the go).

Runners: `cc\pm_rung3_adj_pre.sh`, `cc\pm_rung3_adj_run.sh`, `cc\pm_rung3_rollup_run.sh`.
