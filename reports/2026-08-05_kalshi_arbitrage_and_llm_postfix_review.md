# Kalshi divisions — post-fix performance + health review
**Divisions:** `kalshi_arbitrage` · `kalshi_llm_arbitrage`
**Date:** 2026-08-05 (UTC probe 21:35Z) · **Mode:** read-only, empirical (live prod DB) · **Branch:** `claude-2026-08-02-kalshi-review` off prod-live `ef613e5`
**Source of truth:** `/home/azureuser/trading_corp/data/trading_corp.db` (1.81 GB), queried with `PRAGMA query_only=ON`. Probes + raw outputs committed alongside this report: `2026-08-05_kalshi_review_probe{1,2}.py` / `..._probe{1,2}_output.txt`.

---

## 0. Read-through caveats (the numbers mean nothing without these)

- **Both divisions are PAPER.** Empirically confirmed from the live process line — `--live-divisions` = `bitunix_sfp robinhood_pead bitunix_futures kalshi_copy_trading robinhood_pmcc`. Neither `kalshi_arbitrage` nor `kalshi_llm_arbitrage` is live (only `kalshi_copy_trading` is, among kalshi). All rows are `would_have_placed`. **Every P&L below is a paper ceiling.**
- **Fee model:** per-ORDER `ceil(0.07·C·P·(1−P))` (dollars, rounded up to the cent), applied once per round-trip row (= per order). The engine's stored `realized_pnl` is **gross**; every "net" figure here is my own gross − per-order fee. Fees are NOT modeled in the engine's paper P&L.
- **Ceiling vs. live divergence:** paper assumes fill at the recorded price, held to resolution (single entry fee, no exit fee, no slippage), and uses **fractional contracts** (e.g. qty 2.56, 33.33) that live Kalshi cannot trade. The **thinnest legs diverge most** — the arbitrage forward legs are NO @ **$0.03**, and several LLM legs are NO @ $0.08–0.19; at those prices real liquidity is near-zero and a live fill at the paper price is unlikely. Treat sub-$0.05 legs as fiction.
- **LLM re-emission:** the LLM re-emits the same market nightly. Forward re-emit factor this cut = **9.6×** (153 emissions → 16 distinct markets). Two views are reported, clearly labeled: **Option B (distinct-market, canonical = first emission / MIN(entry_ts)) is the deployable headline; Option A (per-emission) is a secondary "nightly signal-accuracy" count and is inflated as a P&L.**
- **Sample size:** forward distinct counts are **16 (LLM)** and **2 events (arbitrage)** — far below n≈30. Everything forward is **directional only, NOT a verdict.**

---

## 1. Health (STEP 0) — both divisions OK, one hygiene item

| Check | Result |
|---|---|
| Engine service | `trading-corp` **active**; python PID **573032** (under xvfb wrapper 573018), pidfile agrees |
| Divisions live? | **No** — both paper/standby (see §0) |
| Kalshi resolver | **Alive, hourly.** Recent tick: `scanned 109, resolved 0, pending 98, not_found 4, errors 0`. resolved=0 in off-hours ticks is normal (nothing newly settled) |
| Resolver booking (arb) | Backlog **still draining cleanly**: resolved-by-day 07-07:242, 08-01:153 (the 07-07 leg_date-fix batch + a fresh 08-01 batch) |
| Resolver booking (LLM) | **Forward settlements now book** — forward subset resolved 08-01 (≈148, the documented backfill) + 08-04 (5). Confirms the un-starve/epoch fix took effect |
| Tracebacks / errors | No Python tracebacks. Only chronic noise: **4 Kalshi `404 not_found` per resolver tick** (a handful of purged/delisted tickers the resolver can never settle — benign, but they permanently occupy the `not_found` bucket). One `yfinance $SUI-USD delisted` line (unrelated to these divisions) |
| Karen account (arb) | Healthy, stable: equity **$508.25** (cash $505.84 + 2 open positions $2.41), snapshotting every 5 min |
| Pending backlog | `kalshi_llm_arbitrage` pending **1,385** (mostly PRE-epoch rows the live epoch-scoped resolver intentionally skips — expected, not a leak). `kalshi_temporal_bucket_arb` pending 80 (oldest 05-16; some likely stuck on 404 markets) |

**Actor note (corrected via follow-up probe):** `kalshi_tail_price_arb` has **zero placement emissions ever** — the entire resolved book is `kalshi_temporal_bucket_arb`. But it is **not idle**: since the 08-04 restart it actively scans every few minutes (`kalshi_tail_arb_scan` n=528, `kalshi_market_evaluated` n=2640, last 22:40:43Z), finding `n_tail_candidates 76 / n_opportunities_above_threshold 0`. It has simply **never found a qualifying opportunity to place** — not a dead actor.

---

## 2. `kalshi_arbitrage` — still 100% backlog, first forward trades are net-negative & dormant

### 2a. Backlog-vs-forward split (STEP 1) — the load-bearing cut
Cut = entry_ts ≥ `2026-07-07` (leg_date fix day). All rows are `kalshi_temporal_bucket_arb`.

| Subset | n (legs) | W/L | WR | gross | fees | **net** |
|---|---|---|---|---|---|---|
| **(a) Backlog** (entry < 07-07) | 404 | 322/82 | 79.7% | $3,744.12 | $16.40 | **+$3,727.72** |
| **(b) Forward** (entry ≥ 07-07) | 9 | 1/8 | 11.1% | −$7.80 | $0.58 | **−$8.38** |
| Division total | 413 | 323/90 | 78.2% | $3,736.32 | $16.98 | +$3,719.34 |

**The headline +$3,719 net is 100% backlog.** This is the same conclusion as the prior 3 reviews — the profit is pre-fix entries drained by the resolver.

### 2b. Forward detail (STEP 2) — 9 legs, but only 2 distinct events
This is a **change from prior reviews** (which had forward = 0): forward trades have now resolved. They are:

| Event | legs | bet | result | net | note |
|---|---|---|---|---|---|
| `KXFDAAPPROVE-GED-26AUG01` | 8 | NO @ $0.03 | **YES** (lost) | −$8.56 | 8 arb-sets, one underlying market, entered 07-07; FDA approved → all NO legs lost |
| `KXDIAZOUT-MDC-26AUG01` | 1 | NO @ $0.83 | NO (won) | +$0.18 | entered 07-11 |

So "forward arbitrage" is **one losing arb structure (FDA GED, 8 cheap-NO legs @ $0.03) + one tiny win.** n = **2 distinct events.** Nothing can be concluded. Note the irony: `KXFDAAPPROVE-GED` is also the division's single biggest backlog winner (below) — the signature event both made the fortune and took the (small) forward loss.

### 2c. Entry rate — dormant (opportunity-starved), scanner CONFIRMED LIVE
Last placement emission **2026-07-28** (2 legs). Post-07-12 there is essentially nothing (07-28:2 only). Effective rate ≈ **0.1 entries/day**, consistent with the "~0.09/day dormant" prior characterization. **Not reviving.**

**Dormant-vs-broken settled empirically (follow-up probe, 22:41Z):** the scanner is healthy and running, not crashed. Since the 08-04 engine restart, both actors emit continuous evaluation telemetry — `kalshi_temporal_bucket_scan` n=528 (last 22:40:44Z, `n_temporal_events_scanned 7, n_temporal_opportunities 0, n_bucket_opportunities 0`), `kalshi_pair_evaluated` n=712, `kalshi_discovery_refreshed` n=264 (universe ~39–52 events / ~206–334 markets) — with **0 arb-related tracebacks**. The zero entries are **opportunity-supply starvation** (no qualifying pairs/tails clear threshold), **not a scanner error**. → keep the opportunity-supply trigger; no scanner-diagnosis follow-up required.

### 2d. Concentration (STEP 3) — one event = 58% of all-time P&L
All-time net $3,719 across 25 event groups; **top-3 = 84.9%**:

| Event | net | share |
|---|---|---|
| `KXFDAAPPROVE-GED` | $2,156.76 | **58.0%** |
| `KXFARMBILL-26MAY` | $618.35 | 16.6% |
| `KXBEEFTARIFF-26MAY` | $383.25 | 10.3% |

The edge (such as it was) is **one lucky rich event**, exactly the historical pattern. Forward concentration is meaningless (net −$8.38 across 2 events).

---

## 3. `kalshi_llm_arbitrage` — forward is a coin flip that breaks even net-of-fee

### 3a. Backlog-vs-forward split (STEP 1)
Cut = entry_ts ≥ `2026-07-07T16:40` (epoch).

| Subset | n (emissions) | W/L | WR | gross | fees | **net** |
|---|---|---|---|---|---|---|
| **(a) Backlog** (pre-epoch) | 2,802 | 1127/1675 | 40.2% | −$452.42 | $121.65 | **−$574.07** |
| **(b) Forward** (post-epoch) | 153 | 117/36 | 76.5% | +$118.45 | $7.19 | **+$111.26** |

The forward count (153 ≈ 148 backfill + 5 on 08-04) matches the documented 2026-08-01 un-starve backfill exactly. **But 153 is a per-emission count — it must be collapsed to distinct markets before it means anything (§3b).**

### 3b. Forward performance — Option B (headline) vs Option A (secondary)

> **★ HEADLINE — Option B (distinct market, canonical = first emission):**
> **16 distinct markets · 8W / 8L · WR 50.0% · gross +$0.76 · fees $0.76 · net $0.00**
> A pure coin flip whose razor-thin gross edge is **entirely consumed by fees**. Statistically indistinguishable from zero.

*Confirmed by a second probe at 22:41Z: 16 distinct markets, 8W/8L, **no mixed-`won` tickers** (no bet flipped / no re-resolution), and **no new resolutions** since 2026-08-04T14:40. Both scoring methods agree on W/L — canonical-first net $0.00, sum-all-emissions net +$111.26. See §6 for the reconciliation against the operator's stated verdict.*

> **Option A (per-emission, secondary — "nightly signal accuracy"):**
> 153 emissions · 117W/36L · WR 76.5% · net +$111.26.
> **This is a re-emission artifact, NOT edge.** The entire positive net comes from re-betting a few winners ~100 times: `KX2YFOMC-26JUL29-T8` (45 emissions, +$67.63) + `-T10` (56 emissions, +$55.68) + `KXCBDSA` (7, +$24.31) = **top-3 markets are 132.7% of Option-A net** (everything else nets negative). Two winning FOMC markets, re-emitted, create the illusion of a 76.5% hit rate. Do not report this as the edge.

### 3c. Economics vs Elections (STEP 2) — they exactly cancel
| Half | distinct n | W/L | WR | net |
|---|---|---|---|---|
| Economics (forced high-divergence) | 12 | 8/4 | 66.7% | **+$4.24** |
| Elections (lower divergence) | 4 | 0/4 | **0.0%** | **−$4.24** |

The Economics gains and Elections losses **net to exactly $0.00**. The LLM went **0-for-4 on Elections** — it faded four endorsement/senate-run outcomes (`KXSCRSENSRUN`×2, `KXDARLINEENDORSE`, `KXMCMORROWENDORSE`), betting NO where the market priced YES high; **all four resolved YES** (market right, LLM wrong). n=4, so directional only, but a clean 0/4.

### 3d. Inversion test (STEP 3) — the pre-epoch catastrophe does NOT reproduce forward
Pre-epoch finding: 40%+ divergence → ~10.7% WR (high-conviction fades systematically wrong). Forward, by divergence bucket (distinct markets):

| Divergence bucket | half | n | WR | net |
|---|---|---|---|---|
| 40%+ | Economics | 8 | **50.0%** | +$2.60 |
| 25–40% | Economics | 4 | 100.0% | +$1.65 |
| 11–25% | Elections | 3 | 0.0% | −$3.18 |
| <11% | Elections | 1 | 0.0% | −$1.06 |

- The **catastrophic high-divergence inversion is NOT present forward** — Economics 40%+ is a 50% coin flip (n=8), not ~11%. Mildly reassuring, but tiny n.
- There is **no monotonic "high-div → wrong" pattern.** If anything the damage forward is in the **low-divergence Elections** bucket (0/4). The two halves occupy disjoint divergence ranges (Econ ≥25%, Elections <25%), so "Econ vs Elections" and "high-div vs low-div" are the same cut here.

### 3e. Concentration
Option-A net is entirely 3 markets (§3b). Option-B distinct net is ~$0, so per-market "shares" are undefined noise (the two FOMC/CBDSA winners ≈ offset the ~10 small losers). The forward book is **not one lucky event** — it's a broad wash of ±$1 outcomes with two modest winners.

---

## 4. Honest synthesis (STEP 4)

### `kalshi_arbitrage`
- **Forward signal:** none demonstrated. First forward resolutions have appeared (9 legs vs. 0 in prior reviews), but they are **2 distinct events, net −$8.38, dominated by 8 losing legs on a single FDA market.** Headline profit remains **100% backlog**.
- **Activity:** effectively **dormant / opportunity-starved** (last placement 07-28; `tail_price_arb` has never placed). **Scanner confirmed healthy and live** (both actors scanning every few minutes post-08-04 restart, 0 errors, 0 qualifying opportunities) — dormant by supply, not broken.
- **n to say anything:** need **dozens of distinct forward events/arb-sets**; at ~0.1 entries/day clustered on few events, that is **months** away.
- **Health:** OK. Backlog draining cleanly; Karen equity stable $508.

### `kalshi_llm_arbitrage`
- **Forward signal:** **break-even coin flip.** Deployable (Option B) view = 8/16, **net $0.00 after fees**. The +$111 / 76.5% (Option A) is a re-emission mirage. **No edge demonstrated; also no reproduction of the pre-epoch high-divergence disaster.**
- **Structure:** Economics (+$4.24, 8/12) and Elections (−$4.24, 0/4) exactly cancel; fees eat the entire gross.
- **n to say anything:** 16 distinct << 30. Need **50–100+ distinct resolved markets** for an edge call; Elections half (n=4) is far too thin to trust the 0/4. At the current cadence, **several months**.
- **Health:** OK. Settlements now booking (confirmed 08-01+). The 1,385 "pending" are mostly pre-epoch rows the resolver correctly ignores.

### Bottom line
> This is an **initial read on low-n forward data — directional, not conclusive.** For **both** divisions the honest answer is: **still mostly backlog / too thin to say, and no forward edge is demonstrated.** Arbitrage's headline is 100% backlog and the division is dormant; the LLM's forward book is a fee-eaten coin flip. Nothing here supports a call either way on live edge; nothing here contradicts the paper mandate. Re-read once the LLM has ≥50 distinct forward markets and arbitrage has more than one forward event.

---

## 5. ★ Reconciliation item (git↔prod drift) — for a future session, do NOT fix here

**Finding (directly verified this session, read-only):** prod-live `ef613e5` **does not contain** the 2026-08-01 kalshi_llm resolver work:
- `trading_corp/agents/kalshi_resolver.py` on prod-live has the per-actor budget + `COALESCE(expires_at, leg_date)` ordering, but **no kalshi_llm epoch `WHERE a.ts >= 2026-07-07T16:40` clause**.
- `_query_kalshi_distinct_market_stats` (the Option-B distinct-market read-view) is **absent from prod-live entirely** (0 matches in the worktree).

**Context (deploy record + confirmed behavior):** commit **`b10a010`** (branch `claude-2026-08-01`) carries the epoch-scope + Option-B read-view + 148-row backfill. It was **deployed to the live engine on 2026-08-01** (PID 536666 at the time) but **never merged to `prod-live`**. Live behavior is consistent with the fix being active (the forward subset booked on 08-01 after being starved). So: **the fix is running on the box; prod-live git does not reflect it.**

**Risk:** `prod-live` is the standing deploy base and the drift-gate reference. The **next resolver-touching deploy built off prod-live would gate against stale resolver code** — either silently clobbering the live epoch-scope/Option-B, or producing a confusing md5 mismatch that stalls the deploy. The drift-gate is currently **untrustworthy for `kalshi_resolver.py` and the distinct-market read-view.**

**Recommendation (future session, not now):** reconcile `prod-live` to reflect what is actually deployed (fast-forward/cherry-pick `b10a010`, verified by per-file LF-md5 worktree==prod), **before** any future resolver deploy — so the drift-gate is trustworthy again. Per the standing prod-live-deploy-base rule, this should be done as a proper file-level reconciliation, not a wholesale overwrite.

*(Optional, offered not done: a one-line read-only `md5sum`/`grep` of the live `kalshi_resolver.py` on the box would make the "live has the epoch clause" half airtight. Say the word and I'll add it — still read-only.)*

---

## 6. Operator verdicts, triggers & reconciliation (recorded 2026-08-05)
*Recorded per operator instruction. The go/no-go call is the operator's; this section records it and reconciles it against the measured data. Not written to edge-memory.*

### Operator verdicts (verbatim)
- **kalshi_llm:** "forward 11W/6L / +$5.87 net (distinct-market, n=17) — directional-positive, still far below any threshold, first evidence the epoch-gate logic isn't obviously reproducing the pre-epoch high-div loss. NOT a go-live signal. Keep accumulating."
- **kalshi_arbitrage:** "still ~100% backlog, forward ≈0, dormant. No forward edge shown."

### ⚠️ Reconciliation of the LLM verdict vs. measured data
The operator's LLM figures **cannot be reproduced from the raw `kalshi_round_trips` table** (two independent read-only probes, 21:35Z and 22:41Z):

| Metric | Operator verdict | Measured (raw DB, both probes) |
|---|---|---|
| distinct markets | 17 | **16** |
| W/L | 11W / 6L | **8W / 8L** |
| net | +$5.87 | **$0.00** canonical-first · **+$111.26** sum-all-emissions |

- No mixed-`won` tickers (no flipped bet / re-resolution); no new resolutions since 2026-08-04T14:40; no constructible per-market average equals +$5.87; no 17th market exists in the table.
- **[RESOLVED 2026-08-05 — read-view audit, see `2026-08-05_kalshi_llm_readview_audit.md`]** The hypothesis that the figure came from a *miscounting* dashboard read-view is **REFUTED.** The live `_query_kalshi_distinct_market_stats` is byte-identical to `b10a010` and, run against the live DB, returns **16/8W-8L/+$0.76 gross with a ZERO per-market diff vs the raw table** — it aggregates the raw table faithfully. The operator's 17/11W-6L/+$5.87 is **not reproducible** from the raw table or the deployed read-view under any tested scenario; origin appears external/manual, not a dashboard bug. The read-view is nonetheless *code-drifted* (live-not-in-prod-live) — reconciled by the **same single `b10a010` merge** as the resolver (§5) — but that is a deploy-gate hazard, **not** a data-correctness issue; the dashboard is trustworthy now.
- **Operational conclusion is identical either way:** directional at best, far below any threshold, **not a go-live**; keep accumulating. **True forward distinct-market number = 16 / 8W-8L / $0.00 net-of-fee.**

### Arbitrage verdict — refined by follow-up probe
Dormant is **opportunity-supply starvation, not a scanner fault** — both arb actors are confirmed live and error-free post-08-04 restart (§2c). No scanner-diagnosis follow-up is warranted.

### Triggers (set/updated)
1. **kalshi_llm forward-edge** — keep accumulating; **re-review at distinct-market n ≥ 30 OR 2026-08-19**, whichever first. Each review reports: Option B headline + Option A secondary + Economics/Elections split + inversion test.
2. **kalshi_arbitrage** — *dormant (opportunity-starved), scanner healthy* → keep the opportunity-supply trigger: **`n_pairs`/qualifying opportunities recover OR a ≥5-entry day OR 2026-10-15.** No scanner-error diagnosis needed.
3. **★ Drift reconciliation (carry-forward to-do, HAZARD)** — before **any** resolver-touching deploy, reconcile prod-live to reflect the deployed `b10a010` (kalshi_llm epoch fix + `_query_kalshi_distinct_market_stats`), else the drift-gate is untrustworthy (§5). This is a **deploy-gate/code hazard, NOT a data bug** — the audit confirmed the deployed read-view computes correctly (the dashboard-vs-raw LLM number gap is *not* caused by the read-view). One `b10a010` merge covers both the resolver clause and the read-view function.

---

## Appendix — method & provenance
- Probe 1 (`..._probe1.py`) opened the DB read-only (`query_only=ON`), pulled all 9,071 `kalshi_round_trips` rows + audit/pending/equity aggregates, and computed splits, Option A/B, Econ/Elections, inversion buckets, concentration, and per-order fees in-process. Output verified byte-for-byte against `..._probe1_output.txt`. Probe 2 (`..._probe2.py`, 22:41Z) re-confirmed the LLM distinct-market count + method comparison and the arb scanner-liveness telemetry.
- Division/actor mapping from `kalshi_resolver.py`: `kalshi_arbitrage` = {`kalshi_tail_price_arb`(0 rows), `kalshi_temporal_bucket_arb`}; `kalshi_llm_arbitrage` = {`kalshi_llm_arbitrage`}.
- Cross-check: backlog(2,802)+forward(153) = 2,955 total LLM resolved ✓; backlog(404)+forward(9) = 413 total arb ✓; Option-B 8W/8L and Econ+Elections net (+$4.24 −$4.24 = $0.00) reconcile against the per-market table ✓.
