# Kalshi Copy-Trading — Whale Roster Performance Review

**Division:** `kalshi_copy_trading` · **Date:** 2026-08-02 (Sunday) · **Mode:** READ-ONLY
**Data source:** prod `trading_corp.db` (`sqlite3 -readonly`) + `journalctl`/`systemctl` (SELECT-only), via `ssh azureuser@trading.jacksumner.com`. Harnesses: `cc/rv_step0..4.sh`.
**Execution status:** LIVE (real money), `auto_execute:true` since **2026-07-01T14:08:58Z** (= the copy-live epoch).
**Live review window:** 2026-07-01T14:08:58Z → 2026-08-02T18:17Z (~32 days), matching the dashboard's live epoch scope.

> No commits, deploys, config, roster, pin, or `auto_execute` changes made. Every recommendation below is a recommendation only — the operator decides and executes.

---

## STEP 0 — Did the Sunday batch run? Are the metrics trustworthy?

**Sunday batch: YES, ran and completed today (degraded-but-OK).** Two phases, both today:
- **12:01:42Z** — watch-only **stats refresh** (`kalshi_watch_only_refresh` audit; wrote `watch_only_stats`).
- **14:06:58 → 14:09:26Z** — **deep leaderboard discovery scan** (`watch_only_deep_metadata`): pulled 6 categories × 3 time-windows = **17 of 18 leaderboards OK**; **1 non-fatal failure** — `Climate+and+Weather/weekly` returned Apify HTTP 400 ("Actor run … FAILED"), handled gracefully. Candidate pool 1090 → probed 30 → **found the target 10** watch whales. Batch succeeded.

**Important scoping fact:** discovery only refreshes the **Watch-only pool**. The **Selected roster is operator-pinned and untouched since 2026-07-01** (`selected_whales` + `pinned_whales` `updated_ts` = 2026-07-01T20:41). So "the batch ran" does **not** mean the Selected roster was re-evaluated — it never is, by design.

**S2 metrics (deployed 2026-07-27) — trustworthy for the LIVE window:**

| Check | Result | Verdict |
|---|---|---|
| `whale_handle` on live round-trips | **16 / 16 = 100%** | ✅ live attribution reliable |
| Copyability counts live copies | prod `_query_kalshi_whale_intel` counts `kind IN (would_have_placed, kalshi_copy_placed_live)` side=buy | ✅ S2 fix (a) live in prod |
| Selected panel epoch-scoped | prod scopes intel on `ts`/`entry_ts >= epoch` when mode=live | ✅ fix (c) live |
| Watch panel all-time | called with default mode='all' | ✅ by design |

**Three caveats that shape what CAN vs CANNOT be concluded — read before the numbers:**

1. **Copyability denominator uses *raw* skip-event counts, not distinct markets.** `detections = copies + no_side + sports` where `no_side`/`sports` are `COUNT(*)` of audit rows. In the **LIVE window this happens to be ~1:1 with distinct markets** (AI.EDGE: 37 no_side rows = 37 distinct tickers), so live copyability is meaningful. But **all-time (Watch panel) copyability is heavily re-poll-inflated** (e.g. teafordong all-time sports = 3,804 rows / 135 distinct; lengthy.starfish no_side 1,854 / 51). **→ For Watch/all-time whales, read the distinct-market copyability I compute below, not the dashboard %.** A dashboard copyability <5% on an all-time whale is often a re-poll artifact, NOT proof of structural uncopyability.

2. **Watch roster (14:09) is newer than watch stats (12:02).** 5 of the 10 current watch whales (meatsweater, BenS, siskocommunications, LilSkow, TheOgFlan) have **no stats row yet** — only a leaderboard rank/probe. Their numbers will populate on the next stats refresh; today they are un-assessable.

3. **`data_feed_status` has NO Kalshi/Apify row** (only `eodhd`). Kalshi feed health is not surfaced there — it must be read from logs + `kalshi_copy_feed_anomaly` audit. Observability gap (not new).

**Net:** live per-whale P&L, hit-rate, hold-time, category mix, and live copyability are trustworthy. All-time/Watch copyability must be read as distinct-market. Sample sizes are the binding constraint (both Selected whales n<30).

---

## STEP 1 — Division health

**Service:** `trading-corp.service` — `active/running`, **NRestarts=0** (no crash-restarts). Last start **2026-08-02 17:33:24Z** = the PEAD position-card deploy restart (~44 min before this review; expected, clean re-arm). Copy trader runs in-process; no separate unit.

**Stability since last review (07-20 → now):** **0 `database is locked`, 0 `OperationalError`** — the lock-storm class remains closed. Tracebacks total 2,373 but the breakdown is benign for this division:

| Traceback class | Count | In copy division? |
|---|---|---|
| `TypeError` (DXLink/tastytrade logging-format `%s`-fed-tuple noise) | **2,348** | No — market-data websocket log noise (same as prior review) |
| Robinhood `502/503 Server Error` (options/equity positions) | ~25 (spread across 6 acct endpoints) | No — PMCC/PEAD polling, transient upstream 5xx, caught |
| **kalshi_copy_trader tracebacks** | **0** | — |

`kalshi_copy` warn/err = 12, all = graceful Apify fetch WARNINGs (below). No copy-division exceptions.

**Feed health (Apify, ~10-min poll of 2 names):**
- **12 poll failures over ~13 days** (~1,900 polls → ~99.4% success): HTTP 400 ×6, timeout(150s) ×3, HTTP 500 ×2, HTTP 502 ×1. All caught → `_record_fetch_failure` → `return []` (graceful, no false mass-exit). Plus the 1 discovery leaderboard failure (Climate/weekly) today.
- Copy-poll itself healthy today: `apify open_positions[2 names]: 16 rows` every cycle, ~4–6 s latency.

**Circuit-breaker (`kalshi_copy_feed_anomaly`) since the Maggie settlement-latch fix (deployed 2026-07-30 10:29Z):**

| Date | Fires | Cause |
|---|---|---|
| 07-01 | 72 | pritz786 crypto-15min churn (historical) |
| 07-29 | 35 | MaggieTheEagle `mass_disappearance` (2 of 3 `KXFEDDECISION` markets settled) |
| 07-30 (≤10:22) | 61 | same Maggie settlement latch, re-firing every cycle |
| **07-30 10:29 → now** | **0** | **fix confirmed working** |

**→ Zero anomalies in the ~3 days since the R1+R2 fix.** The 07-29/07-30 burst was exactly the settlement-latch the fix targeted (66.7% removed = 2 settled FED markets), and it stopped the instant the fix shipped. Closed.

**Autopause (shadow) fires:** `kalshi_whale_auto_paused` = **2 all-time, last 2026-06-22 (teafordong)**. **No `would_auto_pause` shadow fires on Kalshi in-window** — and none are possible: autopause requires `min_trades=30`, and **both live Selected whales are below that floor** (AI.EDGE n=13, Maggie n=3). The breaker is dormant-by-construction, not silently broken. (Contrast: Polymarket's `would_auto_pause` fired 5,753× and is actively logging — the Kalshi silence is the small live sample, not a wiring gap.)

---

## STEP 2 — Selected roster per-whale (live window, epoch-scoped)

Selected roster = **`["MaggieTheEagle", "AI.EDGE"]`** (2 whales). Fee model = per-ORDER `ceil(0.07·C·P·(1−P))` (validated against stored fill fee: 2@0.32 → formula 0.0305 vs recorded 0.0304 ✓). All 16 live RTs **settled at resolution** (no pre-resolution exit fee). "net (fee+slip)" adds the dashboard's $0.01/contract slippage for reference. Skip taxonomy: `no_side` = side-inference-low-confidence (the side-inference ceiling); there is **no separate `low_confidence` kind** and **0 "other"** skips.

### AI.EDGE

| Metric | Value |
|---|---|
| Copies (filled, live) | **15** |
| No-fills (liquidity) | 12 → fill rate 15/27 = **55.6%** |
| Detections | 61 (15 copies + 37 no_side + 9 sports) |
| Skip breakdown | no_side **37** · sports **9** · low_conf 0 · other 0 |
| **Copyability** | dashboard **24.6%** (15/61) · distinct-market **27.8%** (15/54) — **well above 5%** |
| Resolved round-trips | **13** (5W / 8L, 0 void) |
| Gross P&L | **−$3.77** |
| **Net P&L (per-order fee)** | **−$4.42** · net(fee+slip) −$6.47 |
| Hit rate | **38.5%** (5/13) — *n<30, small* |
| Avg hold | **4.83 h** (range 0.5–24.3 h) |
| Days since last copy | **~4.8 d** (last 07-28T23:16) |
| Category mix of fills | binary-single **11** (gross **−$0.19**) · multivariate MVE **2** (gross **−$3.58**) |

**The load-bearing finding:** AI.EDGE's *copyable* book is roughly breakeven; the loss is one bad category leaking past the side gate. Broken out: worldcup/sports 7 (+$0.88), politics/mention 2 (+$0.80), financials/scalar 2 (−$1.87), **MVE 2 (−$3.58, driven by one KXMVECROSSCATEGORY 81-lot @ 0.043 → −$3.48)**. **Excluding MVE + scalar-threshold markets, AI.EDGE's copied book is +$1.68 gross / 9 trades.** The net loss is concentrated in ~4 MVE/scalar copies that arguably should never have been copyable.

**Dormancy is structural, not silence.** Since 07-28 every AI.EDGE detection was skipped `no_side` — all **scalar oil/index threshold ladders** (KXHORMUZMAX/PEAK/WEEKLY, KXWTIH, KXINXU). AI.EDGE has pivoted toward scalar markets the side-inference engine can't copy. It is active daily (2 no_side today) but its *copyable* surface has dried up.

**RECOMMENDATION: WATCH.** n=13 < 30 → below the demote floor; copyability ~25% ≫ 5% → not structurally uncopyable. Net-negative but the copyable-category subset is ~flat; the bleed is MVE/scalar leakage + a slowing copy surface. **Needs n≥30 resolved copies for a KEEP/DEMOTE call** (at the current, decelerating rate that is ≥2–3 more months, if the scalar pivot even reverses).

### MaggieTheEagle

| Metric | Value |
|---|---|
| Copies (filled, live) | **3** |
| No-fills | 0 → fill rate 100% |
| Detections | 4 (3 copies + 1 no_side + 0 sports) |
| Skip breakdown | no_side **1** · sports 0 |
| **Copyability** | **75.0%** (3/4) — *denominator=4, not meaningful* |
| Resolved round-trips | **3** (1W / 2L) |
| Gross P&L | **−$3.54** |
| **Net P&L (per-order fee)** | **−$3.67** · net(fee+slip) −$3.79 |
| Hit rate | **33.3%** (1/3) |
| Avg hold | **1.19 h** |
| Days since last copy | **~14.8 d** (last 07-18T22:23) — **dormant** |
| Category mix of fills | worldcup/sports **3** (gross −$3.54) — 100% World Cup binary |

**RECOMMENDATION: WATCH.** n=3 is far too small to conclude anything; **dormant ~15 days**. All three copies were World Cup Advance binaries (1W/2L). All-time she is 18 RT / 61.1% WR / −$1.33 gross — the paper-era book was better, but that isn't the live test. **Needs n≥30 resolved; the World Cup source has gone quiet, so accumulation may stall until a new tournament.**

> **Neither Selected whale meets a DEMOTE trigger** (no whale is net-neg at n≥30, and neither is <5% copyable at det>20). Both are WATCH on sample size.

---

## STEP 3 — Watch bench promotion review

Current Watch pool (post-14:09 deep-scan, 10): **YoDog, meatsweater, BenS, decimal.beluga2440, siskocommunications, aenews, teafordong, BitcoinTradingChallenge, LilSkow, TheOgFlan.** Of these, **only teafordong has internal copy history**; the other 9 are external leaderboard finds.

### Internal-history whales (all-time scope — Watch is all-time by design)

| Whale | Status | Internal RT | WR | Gross | Copyability (distinct-mkt) | Note / Rec |
|---|---|---|---|---|---|---|
| **teafordong** | auto-paused 06-22; **still pinned** + re-found today | 30 | 13.3% | **−$10.63** | 33/(33+202+135)=**8.9%** | **DO-NOT-PROMOTE (loses money).** Trips all autopause thresholds (n≥30, WR<40, PnL<−5). Dashboard shows 0.8% copyability = re-poll artifact; distinct-market 8.9% means it *is* copyable enough — the disqualifier is **edge, not structure**. |
| the.hoff.85 | demoted 07-01; **not on watch roster**; stale (last 06-26) | 947 | 53.9% | **+$0.93** | 1150 copies, ~99% | The prompt's "−$31.60/733RT" = **net after cost**; gross is +$0.93 → costs on 947 tiny RTs erase a razor-thin edge. **Highly copyable but net-negative** — classic copyability≠profit. Stale >5 wks. Leave demoted. |
| reach.draft | **pinned, not selected**; stale (last 05-31) | 32 | 37.5% | **−$17.71** | 39/(39+42+109)=20.5% | Worst gross of any internal whale; net-neg at n≥30. **Recommend un-pin (housekeeping).** |

Other stale internal whales (all last-active June, none on any current roster): smedtoshi (1440 RT, 56.2% WR, −$2.67 gross → deeply net-neg after 1440× cost), tom14cat14 (−$4.66), pritz786 (72 RT, +$2.64 gross **but** the demoted crypto-15min mass-exit whale), leftwithnothing (+$0.26). None is a clean promote candidate.

### External-only whales (leaderboard stats — NO copy history in our system)

> ⚠️ These are **cached 20-position leaderboard samples** from the Apify Kalshi scrape, not our copy history. **External stats ≠ copyable edge** — we cannot confirm the bot can mirror them, and their categories are side-inference-hostile. **Ceiling: WATCH-PENDING-COPY-DATA.**

| Whale | LB sample | WR | LB P&L | Top cats | Why not promotable now |
|---|---|---|---|---|---|
| aenews | 18/20 | 90% | +$422,737 | Politics/Elections | 7.2M contracts — un-mirrorable at our ~$1 sizing; MVE-heavy |
| BitcoinTradingChallenge | 13/20 | 65% | +$30,005 | Sports/Crypto | crypto = pritz786-class churn risk; no copy test |
| c.f.frls* | 20/20 | 100% | +$7,331 | Sports/Politics | 100% WR = survivorship on a 20-sample; no copy test |
| decimal.beluga2440 | 15/20 | 75% | +$6,282 | Politics/Elections | Elections = multivariate/uncopyable-heavy |
| YoDog | 19/20 | 95% | +$6,838 | Entertainment/Mentions | Mentions often multi-outcome; no copy test |
| NovaRex*, ml123* | 0/20 (empty) | — | — | Politics/Sports | no usable sample |
| meatsweater, BenS, siskocommunications, LilSkow, TheOgFlan | — | — | — | Politics | **no stats yet** (roster newer than stats refresh) — un-assessable today |

\* stats row present but whale is not in the current 10 (carryover from the 12:02 pre-scan roster) — flagged for staleness.

**Does any bench whale beat the weakest Selected on BOTH axes (copyable AND profitable)?** **No.** Internal bench whales are stale and net-negative-after-cost (or the demoted crypto whale). External bench whales have zero copy history, so "copyable" is unproven regardless of leaderboard profit. **No validated bench whale dominates AI.EDGE or MaggieTheEagle on both axes.**

---

## STEP 4 — Roster synthesis + recommendations

### The copyable ∩ profitable ∩ recently-active intersection

| Axis | Who qualifies |
|---|---|
| Copyable (>5% + produces copies) | AI.EDGE (~25%), MaggieTheEagle (n small) |
| Profitable (net-positive after cost, meaningful n) | **nobody** (AI.EDGE −$4.42/n13; Maggie −$3.67/n3) |
| Recently copy-active (last ~7 d) | **nobody** (last live copy 07-28; AI.EDGE active but uncopyable-only) |

**The intersection is EMPTY.** No whale is copyable AND profitable AND recently copy-active.

### "Can't copy" (structural) vs "loses money" (edge) — which applies to whom

- **Structural / side-inference ceiling (can't copy):** AI.EDGE's recent pivot to scalar Hormuz/WTI/index threshold ladders (100% `no_side` since 07-28); the external bench whales in Politics/Elections/Mentions/Economics (multivariate/scalar-heavy); aenews's un-mirrorable volume. These are uncopyable regardless of edge.
- **Edge / loses money (can copy, shouldn't):** teafordong (−$10.63/30, WR 13.3%, already paused — distinct-market copyability 8.9% proves it's *not* a structural cut); reach.draft (−$17.71/32); the.hoff.85 (highly copyable, net-negative after cost). AI.EDGE is a *mixed* case: its copyable binary book is ~flat; the loss is ~4 MVE/scalar copies leaking past the side gate.

### Recommendations (operator decides & executes)

| Whale | Current | **Rec** | Driving numbers |
|---|---|---|---|
| AI.EDGE | Selected | **WATCH** | n=13<30; copyable 24.6%; net −$4.42; copyable-subset ~flat (+$1.68 gross ex-MVE/scalar); copy surface drying up |
| MaggieTheEagle | Selected | **WATCH** | n=3<30; net −$3.67; dormant ~15 d; 100% World Cup |
| teafordong | pinned + watch + paused | **KEEP DEMOTED** + resolve pinned-but-paused inconsistency | −$10.63/30, WR 13.3%, auto-paused 06-22 (edge, not structural) |
| reach.draft | pinned, not selected | **UN-PIN** (housekeeping) | −$17.71/32, stale >2 mo |
| the.hoff.85 | demoted, off-roster | **LEAVE DEMOTED** | +$0.93 gross but net-neg after cost; stale |
| 9 external watch | watch | **WATCH-PENDING-COPY-DATA** | no copy history; can't confirm mirrorability; 5 lack stats entirely |

### Bottom line: **HOLD the roster — sample is too thin to change it.**

Both Selected whales are below the n≥30 decision floor (AI.EDGE 13, Maggie 3); no bench whale has validated copy data; the intersection of copyable+profitable+active is empty. **There is no data-supported promote or demote this cycle.** The one clean action is housekeeping (un-pin reach.draft; resolve teafordong's pinned-but-paused state) — cosmetic, not a strategy change.

**What each needs to reach a real call:**
- **AI.EDGE → n≥30 resolved copies** (currently 13). At the current, *decelerating* rate (~13 in a month, now mostly `no_side`), that is ≥2–3 months — and only if the scalar-market pivot reverses. Flag for re-review at n=30 or 2026-10.
- **MaggieTheEagle → n≥30** (currently 3). Realistically blocked until a new tournament resupplies World Cup-style binaries.

### Live-vs-paper question: CLOSED (operator decision, 2026-08-02)

**The division stays LIVE.** The slow bleed (−$8.1 net fee / −$10.3 net fee+slip over 32 days on 16 fills) is accepted by the operator. This is not revisited here. Noted for the record: the copy engine has effectively stalled since 07-28 (AI.EDGE trading structurally-uncopyable scalar ladders), consistent with the standing structural verdict (Apify lag + per-order fees + side-inference ceiling) — but running live is the settled call.

### Surfaced (not actioned) — carried from prior reviews, still open

1. **Copyability denominator = raw skip rows**, not distinct markets → all-time (Watch) copyability is re-poll-deflated (teafordong reads 0.8% dashboard vs 8.9% distinct). Consider distinct-market denominator so the <5% demote rule doesn't misfire on all-time whales.
2. **MVE/scalar copy-hygiene:** AI.EDGE's net loss is ~4 MVE/scalar copies that leaked past the side gate; its binary book is +$1.68. Tightening MVE/scalar exclusion would likely flip the copied book to ~flat. (Code change — surfaced, not applied.)
3. **`data_feed_status` has no Kalshi/Apify row** → Kalshi feed health invisible in the standard feed panel.
4. **pinned_whales drift:** contains reach.draft (stale) + teafordong (paused) not in selected_whales.
5. **DXLink/tastytrade logging-format `TypeError`** still floods the journal (2,348 since 07-20) — not copy-division, but masks real errors.

---

## Appendix — Housekeeping actions (operator-run, drafted 2026-08-02)

Two pin-cleanup actions the operator requested drafting. **Read-only until the operator runs them; not executed by the reviewer.** Both targets are in `pinned_whales` but NOT in `selected_whales`, so this is a pure un-pin — no live positions, no copy behaviour change. The engine never reads `pinned_whales` (only `_load_selected_whales` is read each scan, fresh from the DB), so there is no in-memory clobber risk. `pinned_whales` is read fresh per request by the dashboard, so the change takes effect immediately. DB is `azureuser:azureuser`, WAL, directly writable by azureuser (no sudo). Run in a quiet window (not during a PMCC approval burst); `busy_timeout` guards transient WAL write-lock.

**Current state (verified 2026-08-02T18:39Z):**
`pinned_whales` = `["MaggieTheEagle","reach.draft","AI.EDGE","teafordong"]` · `selected_whales` = `["MaggieTheEagle","AI.EDGE"]`

Chosen mechanism = **surgical `agent_state` edit** (remove-by-value), NOT the `/api/kalshi/whales/demote/{handle}` endpoint. The endpoint also runs `force_close_whale_positions` (spurious synthetic-sell audits against stale snapshots) and adds a `watch_only` stub — side effects beyond "clear the pin." The surgical edit does exactly what was asked.

**Item 1 — un-pin `reach.draft`** (net −$17.71 / n=32, stale since 05-31)
- Before: `["MaggieTheEagle","reach.draft","AI.EDGE","teafordong"]`
- Command (run on the box after `ssh azureuser@trading.jacksumner.com`):

```
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db "PRAGMA busy_timeout=5000; UPDATE agent_state SET value_json=(SELECT json_group_array(je.value) FROM agent_state a2, json_each(a2.value_json) je WHERE a2.agent='kalshi_copy_trader' AND a2.key='pinned_whales' AND je.value<>'reach.draft'), updated_ts=strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE agent='kalshi_copy_trader' AND key='pinned_whales';"
```
- After: `["MaggieTheEagle","AI.EDGE","teafordong"]`

**Item 2 — un-pin `teafordong`** (auto-paused 06-22; −$10.63 / n=30, WR 13.3% — edge, not structural; resolves the pinned-but-paused limbo)
- Before (after Item 1): `["MaggieTheEagle","AI.EDGE","teafordong"]`
- Command:

```
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db "PRAGMA busy_timeout=5000; UPDATE agent_state SET value_json=(SELECT json_group_array(je.value) FROM agent_state a2, json_each(a2.value_json) je WHERE a2.agent='kalshi_copy_trader' AND a2.key='pinned_whales' AND je.value<>'teafordong'), updated_ts=strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE agent='kalshi_copy_trader' AND key='pinned_whales';"
```
- After: `["MaggieTheEagle","AI.EDGE"]`

**Verify (read-only, after both):**
```
sqlite3 -readonly /home/azureuser/trading_corp/data/trading_corp.db "SELECT value_json, updated_ts FROM agent_state WHERE agent='kalshi_copy_trader' AND key='pinned_whales';"
```
Expected: `["MaggieTheEagle","AI.EDGE"]` with a fresh `updated_ts`. (`teafordong` remains in `watch_only_whales` from today's discovery scan — that is separate from the pin and expected; it is not the limbo.)

**Rollback** (restores the original pin set exactly):
```
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db "PRAGMA busy_timeout=5000; UPDATE agent_state SET value_json='[\"MaggieTheEagle\",\"reach.draft\",\"AI.EDGE\",\"teafordong\"]', updated_ts=strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE agent='kalshi_copy_trader' AND key='pinned_whales';"
```

---
*Read-only review. Reviewer made no commits (other than this report), deploys, config/roster/pin/auto_execute changes. Housekeeping commands drafted for operator execution, not run. Division remains LIVE per operator decision.*
