# ROLLUP RUN — refresh pm_paper_category_stats after the full-book poll — 2026-08-27

**Authorization (Jack):** "ROLLUP RUN — AUTHORIZED. Refresh pm_paper_category_stats against the full-book trades …
ONE live rollup run ONLY." No poller/adjudicate/cadence/deploy/restart/prod-live. Ran **19:05 UTC** (clear of 03:20;
nothing else wrote the PM DB). Invocation: `pm_cli paper-rollup` (normal). **Exit 0.** `rolled_pairs=7`.

## Filed first (Jack's instruction): R6 — Stage-2 screens must surface OPEN-POSITION COUNTS
Recorded in `PM_REQUIREMENTS.md` §4 R6: the `/farm` landing renders generic poll status + closed-trade pcs stats,
not live open counts, so it could not distinguish "poller captured 14 trades" from "poller never ran" — once the
cadence is installed, that is the page Jack watches, so Stage-2 screens must show open-position counts as a
first-class number. Reasoning kept; **not built now.**

## Pre-conditions (matched) + rollback
schema 9 · grace 259200 · pm_paper_trade **121 (118 open / 1 pending / 2 closed)** · pcs **6 STALE rows** (captured) ·
pm_watchlist **114/92/22** · /farm 200/**130199** · engine **676** / pm_web **24808** · prospects MD5 `c2b7d926…` ·
UTC 19:03. **Gate-1 backup:** `~/pm_rollup2_dbbackup_20260827T190346Z.db` (sha `e3c36af7…`, `integrity_check=ok`).

## (a) pcs before → after (6 → 7 rows)
| pair | before | after | change |
|---|---|---|---|
| **atp · MadeiraIsland** | *(no row)* | n_open **1**, n_closed 0 | **NEW** (the new atp pair) |
| mlb · SDTrading | n_open 2 | n_open **14** | **CHANGED** (the 12 hidden captures now counted) |
| ufc · 4751346 | n_open 2 | n_open **3** | **CHANGED** |
| cs2 · kutsumiakia | n_open 1 | n_open 1 | unchanged |
| fed · 0x71edffd0 | n_open 5 | n_open 5 | unchanged |
| ufc · evanng | n_closed 1, +11.39 | n_closed 1, +11.39 | unchanged |
| ufc · Kh4mz4t | n_closed 1, +89.54 | n_closed 1, +89.54 | unchanged |

## (b) ★ R1 on the wider set — held
pcs rows NOT active-pinned = **0**; pcs rows in deactivated pairs = **0**. R1 held at 7 rows (as it did at 6).

## (c) Reconcile the changed rows by hand — all match
`pcs.n_open` == raw `COUNT(pm_paper_trade WHERE status='open')`: mlb·SDTrading **14==14**, ufc·4751346 **3==3**,
atp·MadeiraIsland **1==1**.

## (d) ★ How the rollup treats OPEN trades — from the code
`paper.paper_rollup`'s SQL (deployed `paper.py`): `n_open = SUM(CASE WHEN pt.status='open' THEN 1 ELSE 0 END)` is the
**only** column open trades feed. Every performance column is gated on `status='closed'`:
`n_closed / wins / losses / net_paper_pnl / cost_basis / avg_entry_price = SUM|AVG(CASE WHEN pt.status='closed' …)`,
and `win_rate = wins/(wins+losses)`, `roi = net/cost_basis` are derived from those. **Open trades are EXCLUDED from
all performance numbers.** Empirically confirmed post-run: **every `n_open>0` row (atp, cs2, fed, mlb, ufc·4751346)
has `n_closed=0`, `win_rate=None`, `roi=None`, `net=0`, `cost=0`.** So the pinned stats are **pure closed-trade
performance**; open positions surface **only as a separate count** and never mix into win_rate/roi/net. (This is
exactly why R6 matters: a lane that is 118/121 open shows its life in the open COUNT, which the landing doesn't render.)

## (e) The 2 closed ufc still reconcile after recompute
evanng ufc: n_closed 1, win_rate 1.0, net **+11.39**, roi 0.128541 — **MATCH**. Kh4mz4t ufc: n_closed 1, win_rate
1.0, net **+89.54**, roi 8.560229 — **MATCH**. Unchanged by the recompute.

## POST-VERIFY
- **/farm 130199 → 130301 — it MOVED** (the atp pair now has a pcs-backed row where it had none). Pinned displays:
  **ufc** → evanng `win_rate 1.0 / roi 0.1285 / +11.39`, Kh4mz4t `win_rate 1.0 / roi 8.560 / +89.54`, 4751346
  `n_open 3`; **atp** → MadeiraIsland `n_open 1` (invisible before its row existed). *(Per-pair live open counts still
  aren't rendered on the landing in a byte-varying way — R6 — but the atp row's appearance moved the page.)*
- **Prospects unchanged:** pm_category_stats named MD5 **c2b7d926… == before.**
- **pm_paper_trade still 121 (118/1/2)** — the rollup reads, it does not write trades.
- **15 categories / 92 pairs** · schema **9** · grace **259200** · engine **676** / pm_web **24808** unchanged.

## STOP conditions — none triggered
No deactivated pair in pcs (R1: 0) · prospects MD5 unchanged · pm_paper_trade unchanged · the 2 closed ufc figures
unchanged. **Clean.**

## State after
pcs **7 rows** (no longer stale): mlb·SDTrading now shows n_open 14 (the completeness payoff, counted), the new
atp pair is present, and the 2 closed ufc performers are intact. The paper lane is honest again after rung 2.

## Still unauthorized
The **cadence install** and **advancing origin/prod-live** remain unauthorized.

Runner: `cc\pm_rollup2_pre.sh`, `cc\pm_rollup2_run.sh`.
