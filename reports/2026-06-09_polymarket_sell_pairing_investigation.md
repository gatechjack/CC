# Polymarket copy-trader SELL-pairing investigation

**Date:** 2026-06-09 (probes run 17:18–17:25 UTC)
**Branch:** `polymarket-sell-pairing-investigation-2026-06-09` (base `origin/main` `f998751`)
**Mode:** read-only. No prod writes, no code changes. All SQL was SELECT-only;
TEMP tables/indexes lived in the session temp DB (main DB untouched).
**Prod DB:** `/home/azureuser/trading_corp/data/trading_corp.db` (1.05 GB), sqlite 3.37.2 on `tc-prod-vm`.
**BACKLOG ref:** P1 — Polymarket copy-trader SELL-pairing investigation (REFRAMED 2026-06-02).

---

## 1. Executive summary

The ~99% `skipped_no_entry` rate is **not a single bug, and not the bug the canned
queries assumed.** The Q2 type-mismatch hypothesis is **empirically refuted**:
`outcome_index` is `integer` on every row and `whale_wallet` is consistent
lowercase-hex `text` on both BUY and SELL sides. Instead, the 874 unpaired SELLs
split two ways: **55% (484) have a matching BUY that exists but has already been
*consumed*** (settled individually by the market-settle path, or paired to an
earlier SELL) — `resolver_pairable = 0`, i.e. zero currently-unpaired SELLs have
an available BUY; and **44% (382) have no BUY audit row at all** (entry never
copied). The operator's partial-fill hypothesis is **confirmed and is the engine
of the 55% bucket**: 5,084 BUY rows collapse to 1,115 real `(whale,condition,outcome)`
positions (4.6× inflation), with 130 positions carrying 10+ BUY rows. The
watchlist-filter workaround is **self-defeating** — splitter whales generate 92%
of the unpaired SELLs but also 88% of positions and 97% of trade volume; removing
them removes the strategy. Recommended path: **(c) net-position P&L from the whale
activity feed** for the actual goal (winning-trader identification), optionally
preceded by **(a) upstream partial-fill aggregation** as a tactical fix.

---

## 2. Question 1 — does a matching BUY exist for skipped SELLs?

**Verdict: split. A matching BUY exists for 55%, but it is unavailable; 44% have no BUY at all. The pairing SQL is not a join-syntax bug.**

Partition of the 874 currently-unpaired copy-trader SELL `would_have_placed` rows
(probe 2, reproducing the resolver's unpaired-sell definition exactly):

| bucket | count | % | meaning |
|---|---|---|---|
| `has_any_wp_buy` | 484 | 55.4% | matching `would_have_placed` BUY exists (same wallet+cid+outcome_index, BUY.ts < SELL.ts) |
| └ of which `resolver_pairable` | **0** | 0% | matching BUY that is **unpaired AND not consumed** (the resolver's actual condition) |
| `no_wpbuy_has_rej` | 8 | 0.9% | no wp-BUY, but a `polymarket_copy_order_rejected_by_risk` BUY exists |
| `true_no_entry` | 382 | 43.7% | no BUY in **any** kind — entry never logged |
| **total** | **874** | 100% | (484 + 8 + 382 = 874 ✓) |

Interpretation:

- **`resolver_pairable = 0` is the headline.** For every one of the 874 unpaired
  SELLs, no unpaired/un-consumed BUY remains. The resolver has already paired
  everything it structurally can; the 874 are the irreducible residual. So the
  matching SQL in `_pair_pending_exits` (`agents/polymarket_resolver.py:255-273`)
  is doing what it was written to do — this is **not** a `LIKE`/join/coercion
  defect.
- The 484 "BUY exists but consumed" rows are explained by **two BUY-consumers
  competing for the same rows** (code-confirmed, see §6):
  1. The **market-settle path** (`_fetch_unresolved_orders`,
     `polymarket_resolver.py:65-83`) explicitly pulls copy-trader BUY rows
     (`COALESCE(...'$.side'),'buy')='buy'`) and resolves each into its own
     round-trip keyed on the **BUY's** `order_id`. Once settled, that BUY's
     `order_id` ∈ `polymarket_round_trips.order_id`, so the pairing query's
     `r.order_id IS NULL` BUY filter (`:258-267`) skips it. The settle path
     **steals BUYs from sell-pairing.**
  2. Multiple SELL events per position: the first SELL pairs and consumes the
     BUY; later SELLs on the same position find none.
- The risk-rejected-BUY mechanism I traced from code (a BUY rejected by the risk
  gate is logged under kind `polymarket_copy_order_rejected_by_risk`, never
  `would_have_placed` — `main.py:3358-3362` — and is therefore invisible to the
  pairing query at `:261`) is **real but minor: 8 rows.**

---

## 3. Question 2 — type mismatch on `outcome_index` / `whale_wallet`

**Verdict: refuted. No type or formatting mismatch exists in the data.**

Probe 1 §F (value/type histogram over all copy-trader `would_have_placed`):

| side | outcome_index | sqlite typeof | rows |
|---|---|---|---|
| buy | 0 | integer | 2161 |
| buy | 1 | integer | 2919 |
| buy | 999 | integer | 2 (stray sentinel, negligible) |
| sell | 0 | integer | 600 |
| sell | 1 | integer | 767 |

`outcome_index` is `integer` on both sides (consistent with `ActivityRow.outcome_index: int`,
`data/polymarket_data_api_client.py:151`, and `int()` coercion on the synthetic-close
path, `polymarket_copy_trader.py:704`). `whale_wallet` is `text`, lowercase
`0x…` hex on both sides (probe 1 §G) — no case/prefix divergence. The
`json_extract(...) = ?` comparison in the matching query is therefore comparing
like types and is not the cause. **Fix path (d) is not applicable.**

> Note: the prompt's canned probe SQL would have returned zero/NULL — it used
> `side = 'BUY'` (actual: lowercase `'buy'`/`'sell'`), `$.size` (actual: `$.qty`),
> `$.price` (actual: `$.limit_price`), and filtered on `$.skip_reason` /
> `LIKE '%skipped_no_entry%'` (no such field exists; `skipped_no_entry` is a
> runtime counter in `_pair_pending_exits`, not stored per row). The probes used
> the verified payload shape from `main.py:3327` + `polymarket_copy_trader.py`.

---

## 4. Question 3 — partial-fill aggregation hypothesis

**Verdict: confirmed. Partial-fill duplication is the dominant driver of the 55% consumed-BUY bucket.**

BUY fanout per `(whale, condition_id, outcome_index)` (probe 2 §Q3):

| chunks/position | groups | total BUY rows | avg span (multi) |
|---|---|---|---|
| 1 | 660 | 660 | — |
| 2–4 | 211 | 571 | ~17,857 s |
| 5–9 | 114 | 745 | ~25,788 s |
| **10+** | **130** | **3,108** | ~14,757 s |
| **total** | **1,115** | **5,084** | |

- **4.6× inflation:** 5,084 BUY audit rows represent only 1,115 distinct positions.
- **130 positions carry 10+ BUY rows (3,108 rows = 61% of all BUY volume).**
- Largest single position: **216 BUY rows** for `0xf9c119…/0xbfca16aa…/oi=1` over
  ~20 h. Tightest burst: **66 BUY rows in 582 s (~10 min)** for `0xe9ba96…` —
  textbook partial-fill of one order across many activity-feed events.
- Spans range from minutes (rapid chunked fills) to days (genuine scaling-in),
  so both flavors exist, but the high-count groups dominate the row volume.

Each activity-feed fill becomes a separate `_emit_entry` → separate BUY
`would_have_placed` row (`polymarket_copy_trader.py:268-286`). The market-settle
path then resolves these individually, inflating P&L attribution **and** consuming
the BUYs that sell-pairing needs.

---

## 5. Question 4 — watchlist-filter feasibility

**Verdict: filtering splitters removes 92% of the problem but 97% of the trade volume. Not viable as a standalone fix.**

Probe 3 (Q4):

| metric | value |
|---|---|
| distinct whales (watchlist) | 34 |
| distinct positions | 1,115 |
| BUY rows | 5,084 |
| whales contributing to unpaired SELLs | 19 |
| unpaired SELLs | 874 |
| splitter whales (5+ buys within 10 min) | 9 |
| splitter whales (n ≥ 5, any span) | 16 |
| splitter whales (n ≥ 10, any span) | 13 |
| unpaired SELLs from splitters (strict) | 741 (85%) |
| **unpaired SELLs from splitters (n≥5)** | **806 (92%)** |
| **whales kept if drop n≥5 splitters** | **18 / 34 (53%)** |
| **positions kept** | **136 / 1,115 (12%)** |
| **BUY rows kept** | **149 / 5,084 (3%)** |

The 16 splitter whales cause ~92% of the unpaired SELLs **but generate 88% of
positions and 97% of trade volume.** Filtering them keeps half the whale *roster*
but guts the actual signal. The splitters are the high-activity whales the
strategy exists to copy. **Operational workaround (b) is rejected.**

---

## 6. Recommended fix path

The BACKLOG goal is *accurate whale P&L attribution for winning-trader
identification*. Two distinct P&L concerns are tangled in the current design:

1. **Whale P&L attribution** (which whale is winning → watchlist promote/demote).
   Should derive from the whale's **own** net position, independent of our copy
   decisions.
2. **Our copy paper-trade P&L** (did our $5 copy profit) — what `_pair_pending_exits`
   currently computes.

| path | scope | verdict |
|---|---|---|
| **(c) net-position P&L from the whale activity feed** — for each `(whale, cid, outcome)`, compute net position + VWAP entry/exit from the `ActivityRow` stream we already ingest (`data/polymarket_data_api_client.py`); realize P&L on flat or market settle. **Sidesteps pairing entirely; handles partial fills natively.** | **large (~3–5 d)** | **Recommended — strategic fix for goal #1.** Correct architecture for winning-trader ID. |
| **(a) aggregate partial fills upstream** — coalesce consecutive same-`(wallet,cid,oi,side)` activity rows within a window into one logical entry/exit before emitting audit rows (or at resolver time). Collapses 5,084 BUYs → ~1,115 positions; removes the BUY-consumption contention. | **moderate (~1–2 d)** | **Recommended tactical fix for goal #2** if copy-audit-row pairing is retained. Paper-only path (lower risk), but it feeds the dashboard/round_trips contract. |
| (b) watchlist filter to non-splitters | small (~2–4 h) | **Rejected** — removes 97% of volume (§5). |
| (d) type-coercion fix in pairing SQL | small | **N/A** — refuted (§3). |
| **(e) combination** | — | **(c) for whale attribution + (a) for our copy P&L** is the complete answer. |

**Secondary structural issue surfaced (worth fixing regardless):** the
market-settle path resolves copy-trader BUYs into round-trips *independently of
sell-pairing* (`_fetch_unresolved_orders:65-83` includes `side='buy'` copy rows),
which both inflates P&L counts and starves sell-pairing of BUYs. Whichever path is
chosen should resolve this contention (e.g. exclude copy-trader BUYs that have a
later SELL from the settle path, or unify both into the net-position model).

---

## 7. Estimated effort per path

- **(a) partial-fill aggregation:** ~1–2 days incl. tests. Touches the copy-trader
  audit-emission contract (paper-only). Backfill of existing rows is a separate,
  optional pass.
- **(c) net-position P&L resolver:** ~3–5 days. New resolver module reading the
  activity feed; new/repurposed `polymarket_round_trips` semantics or a dedicated
  whale-stats table; backfill from history.
- **(e) combination:** ~5–7 days total; (a) can ship first and independently.
- **Settle/pairing contention fix:** ~0.5–1 day if done standalone; folded into
  (a) or (c) otherwise.

(Per CLAUDE.md §4 / PROJECT_CONTEXT §11, any strategy-parameter change still needs
Backtester approval — not triggered by (a)/(c) themselves, but flagged.)

---

## 8. Operator decision required

1. **Primary goal:** is the priority **whale-attribution P&L** (→ path **c**) or
   **our copy paper-trade P&L** (→ path **a**)? The BACKLOG framing ("winning
   trader identification is unreliable") points to **c**.
2. **Sequencing:** ship **(a)** now as a tactical de-duplication while **(c)** is
   designed, or go straight to **(c)**?
3. **Optional confirmation probe (pm4, ~2 min, read-only):** split the 484
   "consumed" BUYs by *how* they were consumed — settled via the market-settle
   path (`order_id` ∈ round_trips) vs. paired to an earlier SELL
   (`entry_order_id`). This pins the exact ratio of settle-contention vs.
   multi-sell exhaustion and sharpens whether the contention fix is high- or
   low-value. Recommend running before committing to scope.

**BACKLOG P1 is intentionally NOT modified** — operator updates after reviewing
findings and choosing a fix path.

---

## Appendix — probe SQL (reproducible, read-only)

All probes streamed to `tc-prod-vm` via
`Get-Content pmN.sh -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\r\357\273\277'|bash"`.

**Probe 1 — schema/size/type** (kinds histogram; `side`/`outcome_index`/`whale_wallet`
typeof; raw sample BUY/SELL payloads; rejected-BUY count). Confirmed payload shape:
`side` lowercase text, `qty` (float), `limit_price` (float), `outcome_index` integer,
no `skip_reason`.

**Probe 2 — Q1 partition + Q3 fanout.** TEMP tables `us` (unpaired sells), `buys`,
`rej` (rejected BUYs), `consumed` (`entry_order_id` ∪ `order_id` from round_trips),
with TEMP indexes on `(w,cid,oi,ts)`; partition via `EXISTS`/`NOT EXISTS`. Q3 groups
BUYs by `(whale_wallet, condition_id, outcome_index)` with `julianday` span.

**Probe 3 — Q4 sizing.** Splitter sets (`n≥5 ∧ span≤600`; `n≥5`; `n≥10`) vs.
unpaired-sell whales and signal-retention counts.

Probe scripts retained locally as `pm1.sh` / `pm2.sh` / `pm3.sh` in the operator
workspace root (untracked scratch).
