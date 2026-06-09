# Polymarket copy-trader SELL-pairing investigation

**Date:** 2026-06-09 (probes run 17:18–17:51 UTC)
**Branch:** `polymarket-sell-pairing-investigation-2026-06-09` (base `origin/main` `f998751`)
**Mode:** read-only. No prod writes, no code changes. All SQL was SELECT-only;
TEMP tables/indexes lived in the session temp DB (main DB untouched).
**Prod DB:** `/home/azureuser/trading_corp/data/trading_corp.db` (1.05 GB), sqlite 3.37.2 on `tc-prod-vm`.
**BACKLOG ref:** P1 — Polymarket copy-trader SELL-pairing investigation (REFRAMED 2026-06-02).
**Outcome:** investigation COMPLETE. Operator selected fix path **(c)** (2026-06-09); (a) deferred, (b)/(d) rejected.

---

## 1. Executive summary

The ~99% `skipped_no_entry` rate is **not a single bug, and not the bug the canned
queries assumed.** The Q2 type-mismatch hypothesis is **empirically refuted**:
`outcome_index` is `integer` on every row and `whale_wallet` is consistent
lowercase-hex `text` on both BUY and SELL sides. Instead, the 874 unpaired SELLs
split two ways: **55% (484) have a matching BUY that exists but has already been
*consumed*** (`resolver_pairable = 0` — zero currently-unpaired SELLs have an
available BUY); and **44% (382) have no BUY audit row at all** (entry never
copied). The consumed bucket is **overwhelmingly settle-path contention**: pm4
shows **96% (466/484) of those BUYs were consumed by the market-settle path**, not
by multi-SELL exhaustion (14), and **90% of all copy round_trips (4,565/5,058) are
settle-derived** — the attribution table is mostly partial-fill-inflated noise.
The operator's partial-fill hypothesis is **confirmed** as the upstream driver:
5,084 BUY rows collapse to 1,115 real `(whale,condition,outcome)` positions (4.6×),
with 130 positions carrying 10+ BUY rows. The watchlist-filter workaround is
**self-defeating** — splitter whales generate 92% of the unpaired SELLs but also
88% of positions and 97% of trade volume.

**Operator decision (2026-06-09): fix path (c)** — net-position whale P&L from the
activity feed — **selected**. It sidesteps both partial-fill duplication and
settle-path contention and is the correct architecture for winning-trader ID.
**(a) deferred** (subsumed by (c) for the stated goal); **(b)/(d) rejected.**

---

## 2. Question 1 — does a matching BUY exist for skipped SELLs?

**Verdict: split. A matching BUY exists for 55%, but it is unavailable (96% of it consumed by the settle path); 44% have no BUY at all. The pairing SQL is not a join-syntax bug.**

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
- The 484 "BUY exists but consumed" rows are explained by **two BUY-consumers**
  competing for the same rows (code at §6):
  1. The **market-settle path** (`_fetch_unresolved_orders`,
     `polymarket_resolver.py:65-83`) explicitly pulls copy-trader BUY rows
     (`COALESCE(...'$.side'),'buy')='buy'`) and resolves each into its own
     round-trip keyed on the **BUY's** `order_id`. Once settled, that BUY's
     `order_id` ∈ `polymarket_round_trips.order_id`, so the pairing query's
     `r.order_id IS NULL` BUY filter (`:258-267`) skips it. The settle path
     **steals BUYs from sell-pairing.**
  2. Multiple SELL events per position: the first SELL pairs and consumes the
     BUY; later SELLs on the same position find none.
- The risk-rejected-BUY mechanism (a BUY rejected by the risk gate is logged under
  kind `polymarket_copy_order_rejected_by_risk`, never `would_have_placed` —
  `main.py:3358-3362` — so it is invisible to the pairing query at `:261`) is
  **real but minor: 8 rows.**

**pm4 quantifies the 484 consumed bucket** (classifying the matching BUY's
consumption — settled = `order_id` ∈ `round_trips.order_id`; paired = `order_id` ∈
`round_trips.entry_order_id`):

| split | count | % |
|---|---|---|
| only_settled (market-settle path) | **466** | 96.3% |
| only_paired (paired to earlier SELL) | 14 | 2.9% |
| both | 4 | 0.8% |
| has_match total | 484 | ✓ |

And provenance of **all** copy-trading round_trips (pair path sets `entry_order_id`;
settle path leaves it NULL):

| | count | % |
|---|---|---|
| copy round_trips total | 5,058 | |
| pair-path (has `entry_order_id`) | 493 | 9.7% |
| **settle-path (no `entry_order_id`)** | **4,565** | **90.3%** |

So consumer #1 (settle path) is both the engine of the skip (96% of the consumed
bucket) **and** the source of 90% of an inflated attribution table — one real
position recorded as up to 216 separate round-trips. Consumer #2 (multi-SELL) is
negligible (≤18 rows).

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
> The canned SQL was working-hypothesis scaffolding; the verified probes are the
> investigation of record.

---

## 4. Question 3 — partial-fill aggregation hypothesis

**Verdict: confirmed. Partial-fill duplication is the upstream cause of the BUY-row inflation that feeds the settle-path contention.**

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
- Spans range from minutes (rapid chunked fills) to days (genuine scaling-in).

Each activity-feed fill becomes a separate `_emit_entry` → separate BUY
`would_have_placed` row (`polymarket_copy_trader.py:268-286`). The market-settle
path then resolves these individually (90% of copy round_trips, §2), inflating P&L
attribution **and** consuming the BUYs that sell-pairing needs.

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
| **(c) net-position P&L from the whale activity feed** — for each `(whale, cid, outcome)`, compute net position + VWAP entry/exit from the `ActivityRow` stream we already ingest (`data/polymarket_data_api_client.py`); realize P&L on flat or market settle. **Sidesteps pairing entirely; handles partial fills natively.** | **large (~3–5 d)** | **SELECTED (operator, 2026-06-09)** — strategic fix for the stated goal. Sidesteps partial-fill duplication AND settle-path contention. |
| **(a) aggregate partial fills upstream** — coalesce consecutive same-`(wallet,cid,oi,side)` activity rows within a window into one logical entry/exit before emitting audit rows. Collapses 5,084 BUYs → ~1,115 positions; removes the BUY-consumption contention. | moderate (~1–2 d) | **Deferred** — subsumed by (c) for the stated goal; only advances goal #2 (our copy paper-P&L) and never fixes the 44% no-BUY bucket. |
| (b) watchlist filter to non-splitters | small (~2–4 h) | **Rejected** — removes 97% of volume (§5). |
| (d) type-coercion fix in pairing SQL | small | **Rejected — N/A** (refuted, §3). |
| (e) combination | — | (c) now; revisit (a) only if our-copy paper-P&L later becomes a priority. |

**Secondary structural issue (now quantified, addressed by (c)):** the market-settle
path resolves copy-trader BUYs into round-trips *independently of sell-pairing*
(`_fetch_unresolved_orders:65-83` includes `side='buy'` copy rows) — 90% of copy
round_trips (§2). (c) replaces round_trips-based attribution with net-position math,
dissolving this contention rather than patching it.

**Decision recorded (2026-06-09):** (c) selected; (a) deferred; (b)/(d) rejected.
Implementing (c) is a **separate scoping session** (out of scope here).

---

## 7. Estimated effort per path

- **(c) net-position P&L resolver:** ~3–5 days. New resolver module reading the
  activity feed; new/repurposed `polymarket_round_trips` semantics or a dedicated
  whale-stats table; backfill from history.
- **(a) partial-fill aggregation (deferred):** ~1–2 days incl. tests, if ever
  revisited for goal #2.
- (Per CLAUDE.md §4 / PROJECT_CONTEXT §11, any strategy-parameter change still needs
  Backtester approval — not triggered by (c) itself, but flagged.)

---

## 8. Operator decision — RECORDED 2026-06-09

1. **Priority:** whale-attribution P&L → **path (c) selected.**
2. **Sequencing:** **skip (a), go straight to (c).** Rationale: the consumed bucket
   is settle-path contention (96%, pm4), not partial-fill per se, and the 44%
   no-BUY bucket is unaffected by aggregation — so (a) does not materially advance
   the stated goal. (c) handles partial fills natively via net position.
3. **pm4:** run (done) — §2. 466/484 settle-consumed; 90% of copy round_trips
   settle-derived.

**Interim caveat:** until (c) ships, the settle path keeps writing inflated copy
round_trips (90% of the table); any consumer reading copy round_trips for whale
promote/demote is unreliable in the meantime.

**Out of scope here:** designing (c) — schema, whether the activity feed has
sufficient data or needs augmentation, backfill — is a separate scoping session.

**BACKLOG P1** updated on `main` to reflect this decision (separate scoped commit).

---

## Appendix — probe SQL (reproducible, read-only)

All probes streamed to `tc-prod-vm` via
`Get-Content pmN.sh -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\r\357\273\277'|bash"`.
Operator could not access PowerShell this session; probes were run from the agent's
tools on the operator's machine (same VPN egress `92.119.177.22`), read-only.

**Probe 1 — schema/size/type.** Confirmed payload shape: `side` lowercase text,
`qty`/`limit_price` floats, `outcome_index` integer, `whale_wallet` lowercase-hex
text, no `skip_reason`.

**Probe 2 — Q1 partition + Q3 fanout.** TEMP `us`/`buys`/`rej`/`consumed`
(`entry_order_id` ∪ `order_id`) + TEMP indexes; partition via `EXISTS`/`NOT EXISTS`.
Q3 groups BUYs by `(whale_wallet, condition_id, outcome_index)` with `julianday` span.

**Probe 3 — Q4 sizing.** Splitter sets (`n≥5 ∧ span≤600`; `n≥5`; `n≥10`) vs.
unpaired-sell whales and signal-retention counts.

**Probe 4 — consumed-BUY split.** For the 484 unpaired SELLs with a matching BUY,
classify the matching BUY's consumption: settled (`order_id` ∈ `round_trips.order_id`)
vs paired (`order_id` ∈ `round_trips.entry_order_id`); plus copy round_trip
provenance by presence of `entry_order_id`.

Probe scripts retained locally as `pm1.sh` … `pm4.sh` in the operator workspace
root (untracked scratch).
