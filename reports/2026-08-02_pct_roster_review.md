# Polymarket Copy Trading (PCT) — Whale Roster Performance Review
**Date:** 2026-08-02 · **Session:** read-only analysis · **Branch:** `claude-2026-08-02-pct-roster`
**Division:** `polymarket_copy_trading` · **Mode:** PAPER (`auto_execute: false`) — all P&L below is paper.
**Prior review:** ~2026-07-20 (superbeter007 flagged the net-loser).

> **Scope discipline.** No code / config / roster / auto_execute changes were made. Every
> recommendation below is advisory — the operator decides and executes. All figures were
> read empirically from the live prod DB (`/home/azureuser/trading_corp/data/trading_corp.db`,
> read-only) at ~19:30 UTC 2026-08-02, not from narration.

---

## Fill-model / P&L caveats (read before the numbers)
- **Paper ceiling.** Copies fill at the *whale's* fill price — no slippage, no latency, no
  partial fills. Real fills would be worse. P&L is an optimistic ceiling.
- **No fees.** Polymarket charges no per-trade fee, so **paper net == gross**. `realized_pnl`
  = binary payoff `qty·(1−price)` on a win / `−qty·price` on a loss (market-settle), or
  `qty·(exit_px−entry_px)` (whale-closed). "Net-of-fee" and "gross" are identical here.
- **Sizing regime is mixed across history.** Current emissions size at **~$1 flat** (the deployed
  `sizing` block lacks the clamp keys, so the code falls back to bankroll 120 × 0.00833 ≈ $1,
  clamp [$0.50,$2.00]). Older rows may have used the v1 tier ladder ($1/$2/$5 by whale bet size).
  Forward-window (≥ epoch) rows are the more commensurable set for cross-whale comparison.
- **"Copies made (n)" = resolved round-trips.** A clean *currently-open* count is not separable
  from the audit feed (the `n_open` audit metric only excludes whale-closed-paired entries, so
  it reads closer to "total buys emitted"). Resolved-RT `n` is the sound P&L sample and is what
  every per-whale figure below uses.

---

## STEP 0 — Batch + metric integrity  ✅ (with one denominator note)

**Sunday discovery/refresh batch RAN and completed.**
- `agent_state(polymarket_copy_trader, watch_only_whales)` last written **2026-08-02 13:44:19 UTC**
  (today), **139 whales**; `watch_only_whales_metadata` written the same second. Watch-pool data
  is fresh — safe to trust.
- All **9 selected whales'** `whale_state:*` rows were re-written **today at 19:27 UTC** by the
  live copy loop → the strategy is actively polling every selected whale. Engine PID `550263`.

**Epoch-scoped metrics populate correctly.**
- `metrics_epoch = 2026-07-07T20:00:54Z` (unchanged; matches the ~2026-07-07 forward window).
- Forward window (`entry_ts ≥ epoch`) returns **2,394** copy round-trips across **481 distinct
  markets**; all-time **8,679** RTs / **1,673** markets. Selected-whale panel scopes forward,
  Watch panel is all-time by design — both behave as specified.

**Copyability denominator — NO Kalshi-style skip-count bug.** Polymarket returns explicit side
(on-chain), so there is no side-inference ceiling and no `no_side` skip. Forward copyability is
effectively **~97.4%**: of ~2,680 buy opportunities, only **69 (2.6%) were skipped for drift**
(price moved >30% adverse before our copy — a *protective* guard) and **0** for already-resolved
markets. These skips are features, not readability gaps.
- **Re-emission / re-poll caveat (report distinct-market where relevant):** forward has **2,611
  buy emissions but only 481 distinct markets (~5.4 buys/market)**. This is *legitimate repeated
  round-tripping* — whales re-enter the same market many times (hourly "Bitcoin Up or Down", sports
  O/U re-entries; e.g. kitten147 entered "Spain vs Argentina O/U 0.5" 4× in one evening). It is
  **not** a metric artifact like Kalshi's inflated skip denominator. Per-whale P&L/WR samples are
  sound; distinct-market count is the better *breadth* gauge and is reported alongside `n` below.

**Minor data-integrity flags (not decision-blocking):**
- `polymarket_resolver` logs `pair_skipped_no_entry: 1175` and `paired: 0` every tick — a constant
  pool of ~1,175 unpairable exit rows (orphan SELLs / legacy). The 8,679 resolved RTs prove pairing
  works for the bulk; this residue does not distort the per-whale resolved stats. Worth a cleanup
  pass eventually, but out of scope here.

---

## STEP 1 — Division health  ✅

| Check | Status |
|---|---|
| Service `trading-corp` | **active (running)**, since 2026-08-02 17:33:24 UTC (~2h), **NRestarts=0** |
| Live PID | `550263` (matches the 08-02 PEAD-deploy restart) |
| Config | `enabled: true`, **`auto_execute: false`** (paper), **`autopause_mode: shadow`** |
| Feed health | Healthy — `/activity` fetches 20 rows/whale in ~150–190 ms |
| Tracebacks | **None** since service start |

**Transient feed errors (handled gracefully, non-fatal):** one burst at 18:20 UTC — HTTP 408
(timeout) on ChadStarmer's wallet, HTTP 429 (rate-limit) on Moond + potatobrahh. The free
Polymarket API throttled; the code logged a warning and continued; the feed recovered by the next
cycle. No capital/data impact.

**Autopause shadow — who's flagging:**
- **Moond — FIRING LIVE.** 1,572 `would_auto_pause` events, latest **2026-08-02 19:28 UTC**
  (n=70, WR 36.5%, −$27.69 at snapshot). Shadow mode flags but does **not** remove, so Moond is
  **still being copied and still bleeding paper**. This is the primary live signal of this review.
- **superbeter007 — already removed.** Was shadow-flagged 6,944× through **2026-07-26 02:58**
  (n=79, WR 7.59%, −$69.43), then **manually dropped from the roster on 07-26**. It is **no longer
  selected and not on the watch list** — the prior-review loser has been handled. Its record is the
  textbook epoch-fix case: **all-time n=126 / 24.6% / +$5.85** (noise-masked positive) vs
  **forward n=79 / 7.6% / −$69.43** (the real, negative signal).
- Historical active-mode removals (digitalnomad85, jtwyslljy, slimjoe, Talvez10, damed21,
  Johnnyboy42069, 0x4ca1…, 0xE9Ba…) all pre-date the shadow flip — the breaker has a working track
  record.

---

## STEP 2 — Selected roster (9 whales, forward / epoch-scoped)

Category = roster-declared (leaderboard discovery). *The RT `category` field is uninformative —
~all rows are `other`/`(none)`, so category-mix from round-trips is not analyzable; use the
roster label.* `hold` = avg (resolved_ts − entry_ts) in days; `dorm` = days since last copy.

| Whale | Cat | n (copies) | WR | Net P&L (paper) | Mkts | Hold | Dorm | **Recommendation** |
|---|---|---:|---:|---:|---:|---:|---:|---|
| **Hakei.** | Tech | 421 | 78.4% | **+$195.73** | 128 | 0.4d | 0.2d | **KEEP** — net-positive at large n, active |
| **llllllIIIIII…** | Sports | 1496 | 57.3% | **+$93.89** | 259 | 0.2d | 2.1d | **KEEP** — net-positive at very large n, broad |
| DegenKingBetter | Sports | 5 | 100% | +$3.40 | 3 | 0.2d | 0.7d | **WATCH** — n<30, accumulate |
| ox1star84 | Sports | 8 | 100% | +$2.08 | 2 | 0.9d | 1.7d | **WATCH** — n<30, accumulate |
| ChadStarmer | Tech | 9 | 66.7% | +$0.84 | 4 | 0.7d | **9.2d** | **WATCH** — n<30 **and most dormant** |
| potatobrahh | Tech | 5 | 80% | +$0.55 | 3 | 0.7d | 2.8d | **WATCH** — n<30, accumulate |
| CVCM | Sports | 12 | 83.3% | +$0.19 | 10 | 0.7d | 1.4d | **WATCH** — n<30; ≈$0 despite 83% WR (favorite-asymmetry risk, watch) |
| **kitten147** | Crypto | 103 | 71.8% | **−$6.11** | 22 | 0.3d | 1.4d | **DEMOTE (soft)** — net-neg at n≥30; structural, not unlucky |
| **Moond** | Politics | 70 | 34.3% | **−$33.58** | 12 | 0.3d | 1.1d | **DEMOTE (hard)** — net-neg, WR<40%, live autopause hit |

**Two whales carry the entire roster.** Forward roster net = **+$257.00** across 2,129 RTs.
Hakei (+$195.73) + llll (+$93.89) = **+$289.62**; the other seven net **−$32.62** combined,
almost entirely Moond (−$33.58).

**kitten147 — why DEMOTE despite 71.8% WR (INVESTIGATE resolved):** the high win rate is a mirage
of favorite-betting. Forward split: **74 wins avg +$0.19** vs **29 losses avg −$0.69** (loss 3.6×
win). It buys favorites at $0.76–0.95, so wins pay the thin `1−price` margin while losses forfeit
most of the stake → **negative expectancy is structural, not a few unlucky trades.** Net-negative
at n=103 ⇒ demote per the profitability rule; magnitude is small (−$0.06/RT), so a "soft" demote
vs Moond's hard one. (Autopause won't catch it — WR>40% fails the conjunctive breaker — so it needs
a manual call.)

**Moond — hard DEMOTE:** n=70, WR 34.3%, **−$33.58**, and it is the one selected whale the shadow
breaker flags every cycle (n≥30 ✓, WR<40% ✓, PnL<−$5 ✓). It's the single largest ongoing drag.

---

## STEP 3 — Watch bench promotion review

**The bench is almost entirely external-only.** Of the 139 watch-list whales, exactly **one has real
internal copy history**: **ic4cream**. Every other top-ranked watch entry is an external-leaderboard
discovery with **no copy history — external stats ≠ copyable edge.**

**Watch list top (external Polymarket leaderboard) — NOT copyable evidence:**

| user_name | rank | n_resolved (ext, capped 100) | WR | Lifetime PnL (ext) | provisional |
|---|---:|---:|---:|---:|:---:|
| SlotinCap | 1 | 17 | 71% | $823,811 | yes |
| beachboy4 | 2 | 10 | 70% | $538,560 | yes |
| xifutloong3 | 3 | 100 | 63% | $270,839 | no |
| texaskid | 4 | 11 | 91% | $196,406 | yes |
| 0xf559…F462 | 5 | 80 | 66% | $190,792 | no |
| **ic4cream** | **9** | 100 | 62% | $86,957 | no — **has internal history ↓** |

> These six-figure lifetime PnLs come from a handful of large political/sports bets (tiny
> `n_resolved`, mostly `provisional=1`). They say nothing about whether copying the whale's *next*
> trade at ~$1 sizing is profitable. **Promote on copy-history, never on leaderboard PnL.**
> Status for all external-only entries: **WATCH-PENDING-COPY-DATA.**

**ic4cream — the one bench name with real internal edge:**
- Internal copy record: **n=737, WR 58.6%, +$69.62, 241 distinct markets** (well-diversified).
- **But: 0 forward-window RTs** — the entire record is **pre-epoch** (last copy 2026-05-22),
  **dormant ~73 days**. No recent evidence; the copy loop hasn't polled it since it left an earlier
  cohort.
- **Recommendation: WATCH-PROMOTE-CANDIDATE (fresh trial only).** It is the only watch whale whose
  internal history is net-positive at large, diversified sample — and it **outperforms both whales
  we're demoting** (Moond −$33.58, kitten147 −$6.11) on all-time net. But because its edge is stale
  and unmeasured in the forward window, a promotion is a *fresh trial*, not a data-backed KEEP. If
  promoted, treat as WATCH and require n≥30 *forward* before crediting it.

**Historically strong but NOT on the current watch list** (dropped from discovery; listed for
context / re-discovery, not actionable now — all dormant 50–79d, some concentrated):
- TimmyTurner123: n=215, 63.3%, **+$246.01** — but only **8 distinct markets** (concentration; edge
  may be a few markets → INVESTIGATE if ever re-surfaced).
- ddssaaas6: n=363, 66.4%, **+$234.41**, 37 mkts, dormant 79d.
- AdrianCronauer: n=492, **96.1%** WR, **+$150.55**, 32 mkts — favorite-harvester profile
  (tail-risk); dormant 63d.

---

## STEP 4 — Synthesis & recommendations

**Demote (numbers):**
- **Moond** — hard. Forward n=70, WR 34.3%, **−$33.58**; live shadow-autopause hit every cycle.
- **kitten147** — soft. Forward n=103, WR 71.8% (mirage), **−$6.11**; structural favorite-asymmetry
  (avg loss 3.6× avg win). Net-negative at n≥30 ⇒ demote per the profitability rule.
- Cutting both removes ~**−$40 of ongoing forward drag**. For scale: whales removed *after* the
  epoch already cost the division **~−$129 forward** (superbeter007 −$69.43 the bulk) — catching
  losers faster is worth real paper P&L.

**Promote (numbers):** **None data-backed in the forward window.** The only merit-worthy bench name
is **ic4cream** (internal n=737 / 58.6% / +$69.62 / 241 mkts) — but 0 forward RTs and dormant 73d,
so at most a **WATCH-trial promotion**, operator's call. All other bench entries are external-only
(no copy history) → no promotion justified.

**Net-positive ∩ recently-active intersection** (the real screen here, since copyability isn't the
constraint): **Hakei. and llllllIIIIII… only.** Both n≥30, net-positive, active within ≤2.1 days.
They generate 100%+ of the roster's forward profit. Everyone else is small-n (WATCH), net-negative
(DEMOTE), or dormant.

**HOLD — need more sample before a KEEP/DEMOTE call (all n<30 forward):**
- CVCM (n=12), DegenKingBetter (n=5), ox1star84 (n=8), potatobrahh (n=5), ChadStarmer (n=9).
- Each needs **≥30 resolved RTs** for a real read. ChadStarmer additionally needs an activity check
  (dormant 9.2d — has the whale gone quiet, or just few qualifying trades?). CVCM to watch closely:
  ≈$0 net despite 83% WR is the same favorite-asymmetry signature as kitten147, just pre-sample.

**Autopause operating note (mechanism, not a change):** shadow mode is doing its job — it correctly
flags Moond every cycle — but by design it does **not** remove, so the roster keeps taking Moond's
loss. Two operator paths, both advisory:
1. **Hot-flip** `polymarket_copy_trader.autopause_mode: shadow → active` in `config/strategies.yaml`
   (hot-reload, **no restart**) — auto-removes Moond and any future n≥30 / WR<40% / PnL<−$5 whale.
   This would **not** catch kitten147 (WR>40%), so kitten147 still needs a manual demote.
2. **Manual demote** Moond (and kitten147) via the dashboard's demote control (flattens the paper
   book, resets whale_state) — mirrors the superbeter007 07-26 precedent.

> ⚠ **Autopause BLIND SPOT — flag for a future autopause-improvement decision.**
> The breaker keys on **win rate** (n≥30 **AND** WR<40% **AND** PnL<−$5, conjunctive). That catches
> **low-WR losers** (Moond, 34.3%) but is **structurally blind to high-WR / negative-expectancy
> losers** like **kitten147 (72% WR, −$6.11)**. kitten147 loses money via **favorite-asymmetry** —
> it buys favorites at $0.76–0.95, so wins pay the thin `1−price` margin (avg **+$0.19**) while
> losses forfeit most of the stake (avg **−$0.69**, i.e. **loss 3.6× win**). A profitable-looking
> 72% WR with negative net P&L will never trip a WR-gated breaker. Future improvement to consider:
> add an **expectancy / avg-P&L-per-RT** (or profit-factor) leg to the autopause rule so
> high-WR-but-net-negative whales are caught automatically instead of needing a manual call.

---

## Appendix — provenance
- DB: `sqlite3 -readonly /home/azureuser/trading_corp/data/trading_corp.db` (prod, tc-prod-vm), read-only.
- Roster source: `agent_state(polymarket_copy_trader, selected_whales)` (9 whales, written 2026-07-26 04:40 UTC).
- Autopause thresholds (`_whale_autopause.py`): n≥30 **AND** WR<40% **AND** total PnL<−$5 (conjunctive).
- Forward window: `entry_ts ≥ 2026-07-07T20:00:54Z` (`metrics_epoch`).
- Queries staged as `tmp/q0…q6*.sql`; no writes issued to prod.
