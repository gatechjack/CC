# Copy-Trading Performance + Roster Review — kalshi_copy_trading & polymarket_copy_trading

**Date:** 2026-07-20 · **Mode:** READ-ONLY (no code/config/roster/git changes; report uncommitted per session guardrail)
**Data source:** prod `trading_corp.db` (`sqlite3 -readonly`) + `journalctl`/`systemctl` (SELECT-only). Query harnesses: `cc/rv_*.sh`.

## Review windows (stated explicitly)

| Division | Execution | Window used | Rationale |
|---|---|---|---|
| kalshi_copy_trading | **LIVE** (`auto_execute:true`, flipped 2026-07-01) | **2026-07-01 → 2026-07-20** (~19d) | since live-flip. Pre-07-01 rows are paper. |
| polymarket_copy_trading | **PAPER** (`auto_execute:false`) | **primary: 2026-07-07T20:00 → 2026-07-20** (~13d); secondary: trailing 30d + full | Last PCT review = 2026-07-07 roster reassignment + `metrics_epoch` reset. Forward window is the actual test of the current roster. |

> **Key framing:** Kalshi is real money but tiny (15 resolved round-trips). PCT is paper but high-volume (1,324 forward round-trips). "Profit" on PCT is paper and slippage-optimistic. Neither division has a *proven live* copy edge.

---

## STEP 1 — Division health

### Service / process
- `trading-corp.service`: **ActiveState=active, SubState=running, NRestarts=0**. Last start **2026-07-18 23:01:40 UTC** (= the RH-auth resilience deploy; expected, not a crash). Both copy traders run **in-process** inside the main engine — no separate units.
- **NameError: 0** since 2026-07-10 (the 05-28 K3 `main.py` NameError fix continues to hold).

### DB-lock rate since the 2026-07-10 fix
- **1 lock incident total**, at **2026-07-10 14:35:27 UTC** ("Polymarket copy trader: run_scan_cycle failed: database is locked" + its `OperationalError` traceback = the "2" grep hits are one event).
- That incident was **~4 h BEFORE the checkpointer-isolation fix shipped that day (18:57 UTC)**. **Post-fix: 0 locks** over 07-10→07-20. Confirms the memory's "0 locks / 72h" claim; the storm class is closed.

### Tracebacks
- **202 tracebacks since 07-10**, of which **196 = `TypeError: not all arguments converted during string formatting`** originating in Python's `logging` `emit`/`format`. Offending calls are DXLink/tastytrade **market-data websocket** log lines (`'received: %s'`, `'sending subscription: %s'`, `'Feed configured: %s'` … passed 3–5-tuples into a single `%s`). **Not in the copy divisions** — log noise only, but it pollutes the journal and can mask real errors. See Step 5 #8.

### Feed health
**Kalshi (Apify leaderboard/positions scrape, ~10-min poll):**
- **3 failures over ~1,440 polls (~99.8% uptime):** 07-14 `HTTP 400`, 07-15 `HTTP 502`, 07-18 `HTTP 502`. All caught → `_record_fetch_failure` → `return []` (graceful). Only **2 whales polled** (`AI.EDGE`, `MaggieTheEagle`).
- **Silent-empty (HTTP 200 + `[]`):** none observed reaching the mass-exit path in-window. **Circuit-breaker firings: 72**, **all on 2026-07-01, all `pritz786`, all `mass_disappearance` @ 100%** (n_prev_tracked=7, n_removed=7). The breaker worked — it suppressed a false mass-SELL when `pritz786`'s fast crypto-15min positions churned/vanished. `pritz786` was subsequently demoted (7 `kalshi_whale_demoted` in-window).

**Polymarket (Data-API `/activity`, 60-s poll):** no mass-exit, no feed anomalies in-window; 74 `polymarket_copy…(warn)` lines = the risk-reject/drift skips below.

### Alerts fired
- `kalshi_copy_feed_anomaly`: 72 (07-01 only, above). No residual/other div alerts.

### Kalshi phantom-poisoned rows
- **7 pre-fix live placements exist as audit rows** (all 2026-07-01, before the 07-02 leg-pricing fix → **missing `leg_priced:true`**), including the canonical **`pritz786` NO 166 @ 0.987 on KXBTC15M-…** (a crypto-15min market).
- **The phantom-guard correctly excludes all 7 from `kalshi_round_trips`** (max round-trip qty = 100, never 166; every live round-trip's leg price reconciles with its outcome). **→ Zero phantom-poisoned round-trip rows corrupt the P&L.**
- **Residual:** those 7 are orphaned real-live BUYs never booked into round-trips (real cash spent on 07-01, untracked) and are re-scanned by the resolver each tick. See Step 5 #6.

---

## STEP 2 — Per-division P&L

### Fee / slippage model used (and whether it matches production)
- **Kalshi:** stored `realized_pnl` is **GROSS** (resolver books gross by design). Real per-fill fees are recorded in the `kalshi_copy_placed_live` audit payload (`fee` = Kalshi `average_fee_paid × filled`), **not** deducted from round-trips. Kalshi schedule ≈ `ceil(0.07·C·P·(1−P))` per traded side (settlement free). **Slippage is already baked into Kalshi `entry_price`** (= real fill price from the V2 response, e.g. paid 0.043 vs 0.037 limit).
- **Polymarket:** **0 trading fee** (CLOB); gas not modeled. `realized_pnl` = gross = net. **`entry_price` = the whale's fill price** ("mirror at same price") → **no slippage applied** (optimistic). `copy_quote_price` field exists but is **empty** (no quote fetcher wired) → real slippage unquantified. Prior trustworthy estimate: ≈ −0.66% drag.

### Kalshi (LIVE, 2026-07-01 → 07-20)

| Metric | Value |
|---|---|
| Proposals — paper shadow (`proposed`) | 7,358 all-time (never advance; paper) |
| Live placements (`kalshi_copy_placed_live`) | 21 |
| No-fill (`kalshi_copy_no_fill`) | 12 → **live fill rate ≈ 21/(21+12) = 64%** |
| Resolved round-trips | **15** (6W / 9L / 0 void) |
| Skips (in-window) | sports 9, no_side 29, ultra_short **0 (filter OFF)** |
| **Realized gross** | **−$5.90** |
| Fees (real, from fill payloads) | ≈ **$0.74** entry, $0 settlement |
| **Realized net (est)** | **≈ −$6.64** |
| Hit rate (ex-void) | **40%** (6/15) — *n=15, SMALL SAMPLE* |
| Unrealized (open) | **NOT INSTRUMENTED** — no `equity_history` for this division; ~6 positions implied (21 placed − 15 resolved − exits). Data gap. |

**Per-whale (live):**

| Whale | RT | W | Gross | Note |
|---|---|---|---|---|
| AI.EDGE | 11 | 4 | −$2.42 | primary live whale |
| MaggieTheEagle | 3 | 1 | −$3.54 | |
| pritz786 | 1 | 1 | +$0.06 | already demoted (crypto-15min, mass-exit) |

**Best/worst single live trades:** best **+$4.08** (AI.EDGE, NO 6 @ 0.32, KXWCMENTION, won); worst **−$3.48** (AI.EDGE, YES 81 @ 0.043, KXMVECROSSCATEGORY, lost); next −$2.00, −$1.92. **No single trade > 5% of a plausible division equity** (all $1–$3.48; division equity uninstrumented — flagged).

**Category mix of actual fills:** `category` column blank for all copies; by ticker → **100% sports/politics** (World Cup advance/mention, esports, T20 cricket + 1 Trump-meeting). **0% crypto-15min live** — a sharp shift from the 88% crypto-15min paper book in the 2026-06-21 review (that cohort's whale, pritz786, was demoted).

### Polymarket (PAPER)

| Window | RT | WR | Gross=Net | Notional | ROI |
|---|---|---|---|---|---|
| **Forward (07-07T20:00→07-20)** | **1,324** | **54.6%** | **+$20.65** | $1,323 | **+1.56%** (slippage-optimistic) |
| Trailing 30d | 1,734 | 55.8% | +$47.73 | $1,733 | +2.75% |
| Full history | 7,609 | 53.8% | +$87.35 | $11,982 | +0.73% |
| Monthly | — | — | **May +$944.10 / June −$874.13 / July +$17.39** | — | huge whipsaw |

- **Forward funnel:** 1,471 `would_have_placed` (paper copies); skips: **160 risk-rejected**, 41 drift (>30% move), 1 auto-paused. No fills (paper).
- **Best/worst single trades (forward):** all **$1.00 notional** (flat sizing); best +$4.00, worst −$1.00 → **no concentration risk; no trade >5% equity**.
- **Category:** `round_trip.category` = "other"/blank (poorly populated); whale-level tags are **Sports-heavy** (Dota2/CS/tennis/soccer).
- **Unrealized:** **NOT INSTRUMENTED** (no `equity_history` for this division). Data gap.

---

## STEP 3 — Per-whale review

Recommendation rule (as given): KEEP (copyability ≥5% at det>0 AND net-positive n≥30) · DEMOTE (net-neg n≥30, or copyability <5% at det>20) · WATCH (n<30, not failing) · PROMOTE (bench, net-positive n≥30 + copyability ≥5%) · INVESTIGATE (anomalous).

> Copyability is Kalshi-only and (per code) counts `would_have_placed`, which **stopped at go-live** → copyability figures below are **paper-era** (Step 5 #2). Polymarket has no copy-intel block; its recommendations use forward round-trip P&L.

### Kalshi — Selected roster (`selected_whales` = [MaggieTheEagle, AI.EDGE])

| Whale | Live RT | Live gross (net) | Paper copies | no_side | sports | Copyability (paper) | Live hit | **Rec** | Driver |
|---|---|---|---|---|---|---|---|---|---|
| AI.EDGE | 11 | −$2.42 (~−$2.9) | 16 | 27 | 17 | 16/60 = **26.7%** | 36% | **WATCH** | n=11 < 30; copyable; slightly negative |
| MaggieTheEagle | 3 | −$3.54 (~−$3.6) | 18 | 13 | 13 | 18/44 = **40.9%** | 33% | **WATCH** | n=3 < 30; prior review's only net-+ whale (n=9) |

### Kalshi — Watch bench (7; watch-only, no live copies)
NovaRex, YoDog, ml123, Short.Pale.Man, juggling.bags, aenews → **WATCH** (insufficient copy data). **teafordong** → **INVESTIGATE/DEMOTE**: auto-paused 2026-06-22 (n=30, WR 13.3%, −$10.63) **yet still in `pinned_whales`** (pinned-but-paused inconsistency). `pritz786` (not on roster now) → correctly **DEMOTED** (crypto-15min, 72 mass-exit firings, go-live phantom).

### Polymarket — Selected roster (14, all operator-pinned) — forward (07-07→07-20)

| Whale | Fwd RT | WR | Fwd P&L | Full P&L | **Rec** | Driver (numbers) |
|---|---|---|---|---|---|---|
| Hakei. | 137 | 83.9% | **+$91.71** | +$90.71 | **KEEP** | net-+ at n≥30 |
| llllllIIIIII… | 834 | 55.2% | **+$46.92** | +$154.57 | **KEEP** | workhorse, n≥30 |
| kitten147 | 75 | 82.7% | +$4.19 | +$11.01 | **KEEP** | net-+ at n≥30 |
| Moond | 19 | 84.2% | +$3.12 | +$3.12 | **WATCH** | promising, n<30 |
| Civic-Static | 124 | 48.4% | **−$7.33** | −$7.33 | **DEMOTE/INVESTIGATE** | net-neg at n≥30 (marginal) |
| LJa7io23MCv954j | 10 | 20.0% | −$7.37 | −$7.37 | **WATCH** | leaning neg, n<30 |
| **superbeter007** | 79 | **7.6%** | **−$69.43** | +$5.85 | **DEMOTE** | net-neg n≥30; **autopause missed it** (Step 5 #1) |
| ChadStarmer | 1 | 100% | +$0.68 | +$5.85* | **WATCH** | no sample |
| potatobrahh | 1 | 0% | −$0.41 | −$0.41 | **WATCH** | no sample |
| TimmyTurner123 | 0 | — | — | +$246.01 (n=215) | **WATCH** | inactive in window |
| 0x594d0c9a… | 0 | — | — | +$42.87 (n=78) | **WATCH** | inactive; −$9.64 in 30d |
| 4gibg4i3o | 0 | — | — | +$2.66 (n=15, 30d) | **WATCH** | inactive/small |
| Magamyman | 0 | — | — | — | **WATCH** | no in-window data |
| monkeybar | 0 | — | — | — | **WATCH** | no in-window data |
| digitalnomad85 | (44) | 2.3% | −$41.43 | −$41.43 | **DEMOTED (done)** | auto-paused 2026-07-08 (correct) |

### Polymarket — Watch bench (123)
Highest full-history net-positive non-selected whales: **ddssaaas6** +$234 (n=363, WR 66%), **AdrianCronauer** +$150 (n=492, WR 96%), **ic4cream** +$69 (n=737, WR 59%), **00xx00xx00** +$4 (n=472). **Rec = INVESTIGATE, not clean PROMOTE:** their records **predate the 2026-07-07 reassignment** (old-regime), they are not on a forward test, and AdrianCronauer was explicitly excluded as *window-truncated* on 07-07. A clean PROMOTE requires a forward paper window (select → observe). Worst bench whales (jtwyslljy −$355, scubacat −$115, damed21 −$106) are already-removed old-roster losers — leave demoted.

---

## STEP 4 — Cross-division comparison

### Ultra-short filter (Fix #3) what-if — Kalshi (DATA ONLY; not enabled)
Approx `minutes = entry_ts → our-recorded-resolution` per live round-trip (**overstates** true time-to-close-at-entry by up to the ~10-min poll lag → the filter would skip **at least** this many).

| Threshold | Copies retained (of 15) | Copies skipped | P&L of skipped | Resulting gross |
|---|---|---|---|---|
| current (0, OFF) | 15 | 0 | — | −$5.90 |
| **min=30** | 14 | 1 (26-min: +$0.06) | +$0.06 | **−$5.96** |
| **min=60** | 12 | 3 (26m +$0.06, 36m +$0.38, 42m −$0.84) | −$0.40 | **−$5.50** |

Skipped-per-whale @60 ≈ 1 each across the 07-01/07-02/07-10 short markets (attribution noisy in the pre-fix era).
**Conclusion:** On the **current 2-whale roster** the filter's P&L impact is **negligible** (skips 1–3 tiny trades) — because live fills are **long-dated sports** (World Cup resolve in 1–6 h), **not** crypto-15min. Its real value is **safety**: it would have **blocked the 07-01 `pritz786` KXBTC15M placement that became the phantom**, and it cheaply guards against any future crypto-15min whale. Surfaced; **left OFF**.

### PCT vs Kalshi — structurally more profitable?
- **PCT (paper):** forward +$20.65 / 1,324 RT (+1.56% gross ROI, **optimistic** — slippage uncaptured; prior trustworthy ≈ −0.66%). High volume, flat $1 sizing.
- **Kalshi (live):** −$5.90 gross / 15 RT / −$6.6 net over 19 days. Structurally handicapped: **Apify ~10-min lag + per-contract fees + 64% fill rate**; only 2 whales, both n<30.
- **Verdict:** PCT is nominally profitable *on paper* but the headline hides two bleeders (superbeter007 −$69, digitalnomad85 −$41; without them, +$131). Kalshi is real money but immaterial volume and remains net-negative — consistent with the 2026-06-21 structural verdict. **Neither is a proven live edge.**

### Systemic patterns / cross-division whale correlation
- **Identity namespaces are disjoint** (Kalshi handles vs Polymarket wallets) → **no same-whale overlap** detectable; no cross-division copy correlation to exploit.
- **Shared failure mode:** both divisions are **sports-concentrated**, both suffer **copy lag**, and both are exposed to **whale regime-shift** (a whale profitable in one window turning toxic in the next — superbeter007 on PCT; pritz786 on Kalshi). June was negative for PCT; Kalshi wasn't live to compare.

---

## STEP 5 — Code / behavior anomalies

1. **[SAFETY] Autopause evaluates on FULL history, not the operator-visible epoch window.** `superbeter007` is **forward −$69.43 (n=79, WR 7.6%)** — clears every autopause threshold (n≥30, WR<40%, PnL<−$5) — **but stays in the roster** because its **full-history PnL is +$5.85 > −$5** (pre-07-07 profit masks the post-reassignment bleed). The dashboard is epoch-scoped (shows the bleed); the breaker is full-history (doesn't act). PCT is paper so no real loss, but this is the exact failure that would bite at go-live. (`_whale_autopause.py` uses all-time `total_realized_pnl`.)
2. **[DATA] Kalshi copyability is paper-only.** The numerator `would_have_placed` **stopped at go-live 2026-07-01**; live copies register as `kalshi_copy_placed_live` (21). Dashboard `Copy%` therefore excludes all live copies.
3. **[CORRECTNESS-reporting] `realized_pnl` stored GROSS** in both round-trip tables; fees live only in Kalshi audit payloads. Any P&L read straight from round-trips overstates (Kalshi live net ≈ gross − ~$0.74).
4. **[RESOLVED] Multi-contract fee convention** — the N=1 demo caveat is superseded: **multi-contract live fills occurred** (81, 100, 20, 10, 8, 7, 6 contracts). Recorded fee = `average_fee_paid × filled` tracks Kalshi's ~7% schedule within ceil-rounding (e.g. 81 @ 0.043 → recorded **$0.2268** vs schedule-ceil **~$0.24**; per-contract×count **under-applies the order-level ceil by ≤~1.5¢/fill**). Minor under-count, **not** a per-vs-total inversion.
5. **[INVESTIGATE] Sub-1-contract quantities on an integer-contract venue.** Kalshi round-trip `qty=0.26` (07-05), `fill_qty=0.26`; `exit_residual` qty 0.56/0.822/1.92; synthetic-close qty 0.08. Suggests $-denominated sizing leaking into a contract field, or partial-fill accounting. Verify against Kalshi statements.
6. **[RECONCILE] 7 orphaned pre-fix live placements** (07-01, no `leg_priced`, incl the 166-contract KXBTC15M) — real live orders never booked into round-trips; resolver re-scans them each tick. Real cash spent, untracked in P&L.
7. **[DATA] Slippage uncaptured** — `copy_quote_price` field exists but empty (no quote fetcher) → PCT `entry_price` = whale fill (optimistic); real slippage unmeasured.
8. **[ERGONOMICS] 196 `TypeError` logging-format tracebacks** from the DXLink/tastytrade market-data websocket (`'%s'` fed tuples). Not in copy divisions; pollutes journal, can mask real errors.
9. **What the OFF ultra-short filter would have caught:** the 07-01 `pritz786` KXBTC15M (<15-min) placement — i.e. the phantom source. Currently OFF, so it didn't.
10. **Residual/exit anomalies:** 20 `kalshi_copy_exit_residual` events = the expected flatten-on-demote pattern (e.g. `pritz786` demote). **No anomalies beyond the expected 07-01 phantom-clearing / demote-flatten behavior.**

---

## STEP 6 — Synthesis

### Code changes surfaced (real bugs — prioritized; surface only, not applied)
- **Correctness:** (a) decide whether round-trip `realized_pnl` should be net (or add a `fee`/`net_pnl` column) so P&L views aren't gross-by-default (only the dashboard adjusts today). (b) **Sub-1-contract Kalshi qty** (#5) — units-bug candidate; verify against venue.
- **Safety:** **Autopause should trip on the same window the operator sees** (epoch-scoped) or add a rolling-window trip, so post-reassignment toxic whales (superbeter007) are caught (#1).
- **Ergonomics/observability:** (a) copyability should count `kalshi_copy_placed_live`, not just `would_have_placed` (#2). (b) fix the DXLink logging-format `TypeError` (#8). (c) reconcile/close the 7 orphaned pre-fix Kalshi placements (#6).

### Config / behavior options (operator decisions — data surfaced, no recommendation)
- **Ultra-short filter:** negligible P&L impact on the current roster; cheap safety vs future crypto-15min whales; would have blocked the go-live phantom. Options: leave OFF / enable @30 / @60 (retained-copy counts above).
- **Roster:** superbeter007 (DEMOTE candidate, −$69 forward, n=79); Civic-Static (marginal −$7.33, n=124); teafordong (pinned-but-paused). Bench PROMOTE candidates (ddssaaas6/AdrianCronauer/ic4cream) need a forward paper window before promotion (records are pre-reassignment).
- **Kalshi division:** 19 days live = 15 fills, −$5.90 net −$6.6; the 2026-06-21 structural verdict (lag + fees) persists; only 2 whales, both n<30. Continue tiny live sample vs pause pending a slow-market cohort.

### Data-quality issues flagged
- **No `equity_history` for either copy division** → no MTM/unrealized P&L, no division-equity denominator for the ">5% of equity" test.
- **Slippage uncaptured** (`copy_quote_price` empty) → PCT ROI is optimistic.
- **Kalshi copyability paper-only** (#2); **category column unpopulated** (Kalshi blank, Poly "other").
- **Small samples:** Kalshi live n=15 (per-whale n≤11); several poly whales n<30.
- **Minor reconciliation:** 15 live round-trips vs 14 post-fix placements (1 unreconciled, likely a synthetic close); round-trip `qty` vs `placed_live` `qty` use different units.

### Questions the data cannot answer (and what's needed)
| Question | Why unanswerable | What's needed |
|---|---|---|
| Unrealized P&L / open-position MTM | No `equity_history`; positions not in `position` table for copy divisions | Live bid fetch from Kalshi/Polymarket, or add equity_history instrumentation |
| True realized slippage | `copy_quote_price` empty; Kalshi baked into fill | Wire a quote fetcher, or reconcile vs venue fill statements |
| Multi-contract fees exactly match Kalshi ledger | Recorded fee from fill response (plausibly exact); formula shows ≤~1.5¢ ceil delta | Reconcile against Kalshi's actual fee statement |
| Forward edge of PROMOTE-candidate bench whales | Not copied → no forward round-trips | Forward paper window (select → observe) |

---
*Read-only review. No commits, deploys, config writes, or roster changes made. Ultra-short filter left OFF. Checkpointer/shared-DB settings untouched.*
