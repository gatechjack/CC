# Polymarket Copy-Trading (PCT) — Read-Only Holding Assessment

**Date:** 2026-07-25 · **Mode:** READ-ONLY (no changes, no deploys, no enablement) · **Branch:** `claude` (off `prod-live` e4219b3)
**Question:** Has ~7 weeks of additional clean resolved trades since the last review made the copy edge legible?
**One-line answer:** No. The sample is now overwhelming (100x+ the bar), and with that power the book-level edge resolves to **~zero**. The only positive is **single-whale (esports) concentration**, not a diversified roster edge — and it is not what the corrected algorithm would even select.

All figures are from the live prod DB (`/home/azureuser/trading_corp/data/trading_corp.db`, read-only) and on-chain Polygon reads, gathered 2026-07-25/26 UTC.

---

## Section 1 — Wallet reality (on-chain)

Wallet `0x216064D944e54756074E11CE5a22B1E4CB6B9F82` (Polygon, chain 137), read via public RPC 2026-07-26 UTC:

| Asset / approval | State | Meaning |
|---|---|---|
| **USDC.e** (`0x2791…4174`, Polymarket collateral) | **0.0** | Collateral fully drained. The ~120 USDC.e that funded it is gone. |
| **POL** (native gas) | **0.1153 POL** | Gas dust (~a few cents). Enough for a handful of txns, no collateral. |
| USDC.e → CTF Exchange (`0x4bFb…982E`) allowance | **max-uint (set)** | approval 1/6 |
| USDC.e → NegRisk Exchange (`0xC5d5…f80a`) allowance | **max-uint (set)** | approval 2/6 |
| USDC.e → NegRisk Adapter (`0x7876…f29e`) allowance | **max-uint (set)** | approval 3/6 |
| CTF (`0x4D97…6045`) isApprovedForAll → CTF Exchange | **1 (set)** | approval 4/6 |
| CTF isApprovedForAll → NegRisk Exchange | **1 (set)** | approval 5/6 |
| CTF isApprovedForAll → NegRisk Adapter | **1 (set)** | approval 6/6 |

**Read: FUNDED-then-DRAINED → effectively EMPTY of collateral, but fully APPROVED.** All 6 CLOB approvals remain live (the exact 6-approval Polymarket fingerprint corroborates this IS the PCT wallet — a random address would not carry precisely these). The plumbing is armed; there is **no money behind "live."** Consistent with the Phase-2 Bitunix drain hypothesis.

*Caveat:* I could not independently confirm the configured funder address from prod (`grep` of `/home/azureuser/trading_corp` found no address literal; it loads from a secret I did not dump). Confidence it is the PCT wallet is nonetheless high from the on-chain approval fingerprint.

---

## Section 2 — Resolved-trade count vs the ≥50 bar (the "n" answer)

`polymarket_round_trips` where `division='polymarket_copy_trading'`:

| Window | Rows (fills) | Distinct markets | Distinct (whale,cid,outcome) decisions |
|---|---|---|---|
| All-time (2026-05-11 → 07-26) | 7,997 | 1,552 | — |
| **Post-epoch (entry_ts ≥ 2026-05-21T12:28:07)** | **5,860** | **1,076** | **1,290** |

**Past the ≥50 bar by any denominator:** 5,860 fills (≈117×), or 1,076 distinct markets / 1,290 distinct decisions (≈21–26×) on the honest independent-sample count. **Sample size is no longer the limiting factor.**

Caveat on "clean": the 2026-05-21 `max_open_per_condition_id` dedup fix is on `polymarket_arbitrage`, **not** copy-trading (copy dedups via its own `_position_key` state). The epoch is still a valid "clean-data-starts-here" marker, but note the heavy clustering — **7,997 fills collapse to 1,552 markets (~5 copies/market)** — so "n" counted as fills materially overstates the number of *independent* outcomes.

---

## Section 3 — Edge assessment (load-bearing)

### 3a. Book-level, post-epoch (5,860 RTs) — the honest aggregate

| Metric | Value |
|---|---|
| Net realized PnL (paper) | **+$50.12** |
| Total notional | $8,892.14 |
| **ROI** | **+0.564%** |
| WR — raw fills | 55.2% |
| WR — distinct markets (net>0) | **52.2%** |
| WR — distinct decisions (net>0) | **51.9%** |
| AvgPx | 0.559 |
| sub-$0.70 share | 70.4% |
| favorite (>$0.85) share | 11.3% |

The honest denominators strip ~3 points off the WR (55%→52%) — that gap *was* the "raw-WR clustering" impostor. At the decision level, WR 51.9% on 1,290 decisions is **z≈1.4 vs a coin flip (p≈0.16) — not significant.** Net PnL of +$50 on ~$8.9k is statistically indistinguishable from zero given the whale-level dispersion below.

**Entry-price band decomposition (post-epoch) — the tell:**

| Band | n | WR | Net PnL |
|---|---|---|---|
| 0.00–0.50 (longshots) | 2,372 | 34.8% | +$102.31 |
| 0.50–0.70 | 1,754 | 54.7% | **−$143.08** |
| 0.70–0.85 | 967 | 79.2% | +$24.10 |
| 0.85–0.95 (favorites) | 695 | 92.5% | +$63.59 |
| 0.95–1.00 | 72 | 52.8% | +$3.20 |

There is no coherent edge signal — the longshot gains (+$102) and favorite gains (+$64) are almost exactly cancelled by the mid-band bleed (−$143). It nets to a wash.

### 3b. Per-whale — the net is the residual of huge opposing bets (post-epoch, n≥20)

Top contributors: `llllllII…` **+$304**, `TimmyTurner123` +$246, `AdrianCronauer` +$150 (WR 96% favorite-farmer, avgpx 0.87), `Hakei.` +$95.
Bottom: `jtwyslljy` **−$355** (WR 7.8%), `scubacat` −$115, `damed21` −$106 (WR 0%), `slimjoe` −$67.

**The whole book's +$50 is the razor-thin residual of whale swings of ±$300+.** A single whale (`jtwyslljy`, −$355) exceeds the entire book's net by 7×. This is the signature of **variance, not edge.** Critically, the biggest losers (`jtwyslljy`, `scubacat`, `damed21`) were **dropped from the roster in the 2026-07-07 board reassignment** — so the post-epoch aggregate mixes a retired roster with the current one.

### 3c. Current roster only, forward test (resolved_ts ≥ 2026-07-08)

| Aggregate | Value |
|---|---|
| n (fills) | 1,668 |
| Distinct markets | 356 |
| WR (fills) | 62.2% |
| **Net PnL** | **+$213.81** |
| ROI | +12.8% |

Looks strong — until decomposed:

| Whale | n | WR | Net | avgpx |
|---|---|---|---|---|
| **llllllII…** (LoL/esports) | 1,082 | 0.611 | **+$196.65** | 0.547 |
| **Hakei.** | 253 | 0.846 | **+$96.18** | 0.639 |
| kitten147 | 86 | 0.814 | +$4.37 | 0.831 |
| Moond | 19 | 0.842 | +$3.12 | 0.604 |
| ChadStarmer | 9 | 0.667 | +$0.84 | 0.394 |
| Civic-Static | 124 | 0.484 | −$7.33 | 0.515 |
| LJa7io23… | 13 | 0.154 | −$10.37 | 0.381 |
| **superbeter007** | 79 | **0.076** | **−$69.43** | 0.601 |

**Two whales (`llllllII…` +$196.65, `Hakei.` +$96.18 = +$292.83) are the entire positive.** Remove `llllllII…` and the roster is ~+$17 (flat/noise); remove the top-2 and it's **negative**. Several roster whales (`TimmyTurner123`, `Magamyman`, `monkeybar`, `4gibg4i3o`, `0x594d…`) are **dormant — zero forward copies.** And `superbeter007` (WR 7.6%, −$69) is still being copied on paper.

`llllllII…` bets League-of-Legends match markets (BO3s) — its 0.611 WR on ~200 markets is over **highly correlated same-tournament outcomes**, so its "edge" cannot be cleanly separated from a single hot run; and its markets are thin, so real (non-paper) copying at size would not fill near 0.547.

### 3d. Is the sample powered enough to tell edge from variance?

**Yes for power, no for legibility.** With 1,076–1,290 independent decisions the book-level edge is well-measured and it is **~zero** (coin-flip WR, +0.56% ROI). The only positive is single-whale concentration whose own sample is (a) correlated and (b) not diversifiable. Additionally, **every PnL here is optimistic**: `entry_price` is the *whale's* fill price with no slippage/latency/fees — real copying fills worse, especially on sub-$0.70 thin markets (70% of the book). The true live edge is at best the ~zero paper figure and realistically **negative** after slippage.

---

## Section 4 — Roster staleness (does the live roster reflect the corrected algorithm?)

**No.** The live roster is hand-curated; the corrected algorithm's picks exist but are unused.

- `agent_state(polymarket_copy_trader, selected_whales)` — 14 whales, last written **2026-07-08** (a whale auto-pause). Contents were set **2026-07-07** via `source:"board_realized_reassign_2026-07-07"` (7 whales) plus earlier `source:"dashboard_button"` manual promotions. **These are human/board picks, not algorithm output.**
- `agent_state(…, selection_metadata)` — the *algorithm's* last population — is from **2026-05-11**, params `{min_resolved:10, half_life_days:30, activity_limit:200}` with **no `realized_audit` scorer** = the **pre-option(c), pre-fix** algorithm.
- The corrected algorithm **was run 2026-07-19** but written to `watch_only_whales` (**advisory / watch-only, not applied**): params `{window_size:100, min_windowed_wr:0.62, min_windowed_pnl:5000, recency_days:60}` = the honest option-c screen. It evaluated 2,513 candidates, dropped 1,229 on WR-floor / 478 on PnL-floor, and passed **123** the quality gate (top pick "Winnnnnnning" `0x533c…2658`, 18-9, WR 0.667, $187k realized). **None were promoted to the live roster.**

**Implication:** any edge read on the *current* roster is a read on a manually-picked set. The corrected algorithm would select a **different** set (its 123 gate-passers sit unused in `watch_only_whales`). Current-roster performance ≠ what the corrected algorithm would produce.

---

## Section 5 — Anything new/changed since the hold (2026-06-29)

- **Paper loop running clean.** 1,993 resolved copy RTs since 2026-06-29, latest `2026-07-26T01:26:05`. `whale_state` written every ~2 min through 02:20 UTC. No errors/failures in audit kinds.
- **Audit activity since hold:** `would_have_placed` 2,270 · `polymarket_copy_order_rejected_by_risk` 433 · `polymarket_copy_entry_skipped_drift` 46 · `polymarket_whale_would_auto_pause` **6,908** (shadow, still not enforcing) · `polymarket_whale_auto_paused` **1** (real, 2026-07-08 — the one roster write) · `polymarket_whale_analyzed` 2 (2026-07-23).
- **Genuinely new:** the **2026-07-19 watch-only option-c refresh** (Section 4). This is the one substantive PCT event since the hold beyond the paper loop ticking.
- **Known-loser still copied:** `superbeter007` continues to bleed (−$69 forward); the S1 autopause is still in **shadow** (fires `would_auto_pause` 6,908× but only 1 real pause), so it keeps dragging paper results.
- **DB-lock storm:** no evidence of impact on PCT — writes are succeeding every ~2 min. (Could not access `journalctl` for lock-error confirmation; inference is from write cadence.)
- **Equity tracking gap (minor):** `polymarket_equity_history` recent rows are all `polymarket_arbitrage` (equity 0.0); copy-trading equity snapshots are sparse/absent. Not load-bearing — round-trip realized PnL is the substantive series.

---

## Section 6 — Honest read: does the additional data strengthen, weaken, or not-yet-resolve the edge thesis?

**It resolves it — against a legible edge — by removing the "need more n" defense.**

Seven weeks ago the story could be "underpowered, wait for more resolved trades." That excuse is now retired: at 1,076–1,290 independent decisions the book-level edge is well-measured and it is **~zero** (coin-flip WR, +0.56% paper ROI, and that PnL is an optimistic upper bound before slippage/fees).

What the data now says:
1. **No roster-wide copyable edge.** The aggregate is the near-cancelling residual of large opposing whale swings — textbook variance.
2. **The only positive is one esports whale** (`llllllII…`, +$196 of the current roster's +$214), on correlated thin markets, un-diversified, and un-scalable.
3. **The corrected algorithm has never been applied** — so even this read is on a hand-picked roster, not the option-c selection that would replace it.

Net: the additional data **weakens** the edge thesis in the specific sense that the sample is now large enough to say the flatness is real rather than provisional. It does **not** surface a new tradeable edge. Framed as the assessment (not a go-live call, which is a separate operator decision): *the edge is now legible, and what it shows is the absence of a diversified copy edge plus a single-whale concentration that paper-flatters and would not survive real fills.*

---

### Method / provenance
- Prod DB read-only via `sqlite3 -readonly`; scripts: `poly_assess_explore.sh`, `poly_assess_metrics.sh`, `poly_assess_roster.sh`.
- On-chain via Polygon JSON-RPC (`polygon-bor-rpc.publicnode.com`); batch `poly_rpc.json` (balance + 6 approvals).
- Code map (data model, option-c, refresh_polymarket_whales, on-chain constants) from worktree `claude` @ `e4219b3`.
- Nothing written to prod. Hold stands.
