# Bitunix Reversal-Whipsaw — Signal-vs-Position Direction-Mismatch Investigation

**Date:** 2026-06-11 · **Branch:** `bitunix-reversal-whipsaw-investigation-2026-06-11` (off `origin/main` `b1e4150`; unmerged)
**Scope:** READ-ONLY analysis. No strategy/parameter change (a direction-gating rule is §4 Backtester-gated, downstream). Prod accessed read-only (`sqlite mode=ro`) per policy `82fda13`.

> ## VERDICT — Mechanism CONFIRMED, but the "defect" is REFUTED by the data. **Do NOT implement the trigger-direction gate — it would block winners.**
> - **Q1 (mechanism): CONFIRMED.** Entry side = the aggregate confluence `winning_side` (argmax of buy/sell score), not the triggering signal's direction. A bull-direction trigger **can** open a short. (`bitunix_confluence.py:415-424,462`; live path `observer:648` with `scoring.enabled=true`.)
> - **Q3 (cost): REFUTES the operator's premise.** The 22 direction-mismatch entries are **net +21.7R, 82% win rate** — *better* than the 117 matched entries (+0.561 avg, 67%). The operator's exact pattern — **bull-trigger → short — is 19 entries, +24.67R, 18W/1L (~95% WR): the single most profitable subgroup.** Blocking it removes ~+24.7R of near-pure winners.
> - **Q2 (staleness): REFUTED for the observed event.** In the reconstructed event the dominant bear signals were **fresh (≤20 min, still firing)**, not stale relics. Faster decay would not have changed it.
> - **Recommendation: this is profitable counter-trend (fade-the-bounce) behavior in a downtrend, not a defect.** Reversal protection already exists at the correct (higher) timeframe — the HTF regime gate. **No §4 change warranted; file as monitor-only.**

---

## 0. Scope, hard stops, disclosure
- **Analysis only.** No strategy logic / parameter change (§4-gated, downstream). No prod write. Read-only SSH (`sqlite3 'file:...?mode=ro'`).
- **Hard stops (status):** strategy/param change → STOP (**not triggered**); prod write → STOP (**not triggered**); **Q1 refutes the mechanism → STOP** (**not triggered — Q1 CONFIRMS the mechanism**; the refutation is of the *cost/harm* premise, which the task explicitly commissioned me to evaluate, not of the side-mechanism).
- **Disclosure (`82fda13`):** local source reads + read-only prod SQLite queries (audit_event, paper_trade_record, bitunix_signal_ledger). No writes, no sub-agents.

## 1. Q1 — Mechanism (file:line): what sets entry SIDE?

**Entry side is the aggregate confluence winning side — NOT the triggering signal's direction.** Trace:

1. Live webhook (otter + cypher) → `web/webhooks.py:525,787` → `observer.observe_and_decide(payload)`.
2. `observe_and_decide` (`observer.py:614`) appends the alert to the ledger, then at **`:648`** dispatches to the **score path** `_score_and_maybe_propose` *iff* `scoring_config.enabled` (else the legacy Phase-3.1 `_tier_for` path where side = trigger direction).
3. Score path → `evaluate_confluence_futures` (`bitunix_confluence.py:320`). Every live (TTL-filtered) alert adds its weight to `buy`/`sell` score (`:347-362`). **Step 4 (`:415-424`)**: `winning_side = argmax(final_buy_score, final_sell_score)`; `net = |buy − sell|`. The verdict's `side` **is** that winning side (`:462`). The triggering signal merely contributes its own weight — it does **not** decide the side.
4. The observer maps `verdict_score.side` → order side (`trigger_side_str = "bull" if side==BUY else "bear"`), builds the order, places it.

**Confirmation it's mechanically possible:** when a bull trigger arrives while `final_sell_score > final_buy_score`, `winning_side = SELL` → a SHORT. **Yes — a bull signal can open a short.**

**Live-path confirmation (prod, read-only):** `scoring.enabled: true` (Phase 3.2 LIVE 2026-05-11, `strategies.yaml:1061`); prod has **3,809 `bitunix_score_decided` audits in 7 days** (vs 74 legacy `bitunix_observer_classified`). The score path is unambiguously the live decision path. **Q1 does not refute → continue.**

## 2. Q2 — Decay mechanics + the operator's event, reconstructed

**Decay model (`strategies.yaml` scoring block):** signals live in the score only within their TTL: per-chart-TF `3m → 30 min, 15m → 90 min, 30m → 180 min` (`ttl_per_tf`). `dedupe_within_ttl: true` (repeat fires of a name count once, most recent wins). 30-min same-direction cooldown. `score_timeframes: [3m,15m,30m]` (4h/1d Cypher hit the ledger but score 0 — the **HTF regime gate** is their directional authority). Note `bias_bear` TTL was already cut **90→30 min on 2026-05-23** because the *opposite* problem (a stale directional factor suppressing opposite-side entries) was observed — the team has prior history tuning exactly this lever.

**Reconstructed event — 2026-06-11T16:04:39Z, `spoon_bull` (a weak bull, +2) → SHORT @ 62,724 (net 11, PREMIUM):**
- buy stack: `spoon_bull(+2)` ⇒ `final_buy = 2`
- sell stack: `mc_a_redx(+2), mc_a_blood_diamond(+5), mc_a_red_diamond(+4), spoon_bear(+2)` ⇒ `final_sell = 13`
- `net = 13 − 2 = 11` → PREMIUM **short**. Exactly the operator's picture: a lone weak bull tick against a wall of bear "diamonds."

**Were the bears stale? NO — they were fresh.** From `bitunix_signal_ledger`, ages at 16:04: `mc_a_red_diamond` last fired **15:45 (19 min)**, `mc_a_redx` **15:45 (19 min)**, `mc_a_blood_diamond` **15:45 (19 min)**, `spoon_bear` **15:51 (13 min)** — all well within their 30-min (3m) TTL, with bears printing **continuously from 11:00 through 15:45**. The downtrend was *actively* generating bear signals up to ~19 min before the bull tick. These are current signals, not relics of a prior trend. (Companion event 2026-06-10T02:45 `cvd_bull_flip`→short, net 8, shows the same shape.)

**Implication:** the operator's "stale bears kept weighting the score after the reversal" model does not hold for the observed event — the trend had **not** reversed at entry (bears still firing), and the bears were fresh. Outcome in paper: **+0.85R win** (downtrend resumed; the short captured continued downside). Faster decay (alternative b) would not have changed this — the contributing bears were nowhere near their TTL.

## 3. Q3 — Frequency + aggregate cost of direction-mismatch entries

Window 2026-05-11 → 2026-06-12 (full score-path live history). 139 placed orders (129 short / 10 long — a 93%-short book reflecting the persistent BTC downtrend). Mismatch = triggering signal's configured direction ≠ entry side.

| class | n | sum R | avg R | wins | losses | win% | avg bars-to-resolve |
|---|---|---|---|---|---|---|---|
| **MISMATCH** | 22 | **+21.67** | **+0.985** | 18 | 4 | **82%** | 8.0 |
| match | 117 | +65.13 | +0.561 | 78 | 35 | 67% | 17.5 |

Split of the mismatch class:

| subgroup | n | sum R | avg R | W/L |
|---|---|---|---|---|
| **bull-trigger → SHORT** (operator's exact pattern) | 19 | **+24.67** | **+1.30** | **18 / 1** |
| bear-trigger → LONG | 3 | −3.00 | −1.00 | 0 / 3 |

- The mismatch class is **more** profitable than matched entries, not less.
- The operator's flagged pattern (**bull → short**) is the **best** subgroup (18W/1L, +24.67R). The lone loss (2026-05-14, `mc_b_buy_circle`) is a month old.
- The **only** losing mismatch sub-population is the *opposite* case — bear-trigger → long — and it is 3 entries, all at one minute (2026-05-12T02:21Z), too small to act on.
- R is from paper-replay resolution (execution_mode=paper); +2.0 = TP@2R, −1.0 = stop. Recent entries show smaller realized R (+0.13…+0.98), consistent with a choppier June regime — but still net-positive.

**Cost of the hypothesized defect: negative — i.e., the "defect" is worth ≈ +21.7R (mismatch) / +24.7R (bull→short) of realized edge.** There is no aggregate loss to remove.

## 4. Q4 — The operator's rule vs alternatives

- **(a) Trigger-direction-agreement gate** ("a bull signal can only open a long/close, never a short"): against history it blocks all **22** mismatch entries = **+21.7R of mostly winners** (the bull→short half is 18W/1L). It is a **winner-killer**. **Reject.**
- **(c) Net-score-must-agree-with-trigger gate:** mechanically identical to (a) — the net winning side *is* the entry side, so "net must agree with trigger" == "trigger must agree with entry side." Same +21.7R of blocked winners. **Reject.**
- **(b) Faster confluence recency-decay:** does not address the observed event — the dominant bears were fresh (≤20 min). Aggressive decay only removes *stale* contributors; here there were none to remove. It would, however, thin the score generally and likely *reduce* the fade-edge. **Not a fix for this; reject as framed.**
- **Reversal protection that already exists (and is the right layer): the HTF regime gate.** `get_trade_permissions(htf_verdict, side_str, htf_config)` (`observer:1395`) evaluates the *proposed side* against the 4h/1d regime and forces size→0 (`skipped_htf_gate`) when misaligned — already firing in prod (495 `skipped_htf_gate` + 17 `skipped_htf_alignment`). When the daily genuinely flips bull, the gate blocks the short. The mismatch entries are precisely the counter-trend fades the HTF gate **permits** because the higher-TF trend is still down — i.e., intentional, and (in this window) correct.

**Which rule removes losers without killing winners? None of the proposed three.** The losers are not the bull→short group; the only losing mismatch subgroup (bear→long, n=3) is not what any proposed rule targets, and is too small to justify a §4 change.

## 5. Recommendation

**This is not a real defect.** The mechanism is exactly as the operator described — a bull trigger can open a short because side follows the aggregate score — but on the evidence it is **profitable counter-trend (fade-the-bounce) behavior in a downtrend**, driven by **fresh** (not stale) bear dominance, with **reversal protection already provided at the higher timeframe by the HTF regime gate**. Implementing a trigger-direction-agreement rule would have destroyed ~+24.7R of bull→short winners.

**Legitimate residual concern (honest caveat):** the +21.7R is **regime-conditional**. This window was a persistent downtrend, so most "reversals" were bounces that the fades correctly shorted. In a genuine V-reversal or a ranging regime, bull→short could lose — the operator's intuition is a valid *tail-risk* concern that this (trending) sample does not exercise. The correct guard for that is **HTF regime classification quality**, not a 3m trigger-direction veto.

**§4 plan (only if a future regime shift changes the picture):** were a fix ever pursued, the Backtester-gated experiment is *not* a blanket trigger gate. It would be: (i) segment mismatch outcomes by HTF regime label at entry (trend vs range vs early-reversal); (ii) test whether tightening the HTF gate (block counter-trend size when HTF regime confidence is low / ADX falling / near a swing extreme) removes the tail losers without touching the trending-regime winners; (iii) walk-forward across ≥1 ranging and ≥1 reversal regime, success = remove net-losing counter-trend entries while preserving ≥90% of the bull→short win-R. This is a regime-detection refinement, downstream and separate.

## 6. BACKLOG / next
Filed **P3 (monitor-only)**: do **NOT** add a trigger-direction-agreement gate (would block +24.7R of winners). Monitor bull→short performance in the next ranging/reversing regime; if that subgroup's R turns materially negative, revisit via the HTF-regime-quality lever (not a 3m trigger veto). P stays P3 because the quantified "cost" is **positive edge**, not a loss.

## Appendix — reproducibility (all read-only, `mode=ro`)
- Live path: `strategies.yaml:1061` (`scoring.enabled`); `observer.py:648`; prod kind counts (`bitunix_score_decided` 3809/7d).
- Side mechanism: `bitunix_confluence.py:415-424` (winning_side=argmax), `:462` (verdict.side).
- Frequency/cost: `audit_event` kind=`bitunix_score_decided`, `outcome='placed'`, joined to `paper_trade_record.actual_r_multiple/result` on `order_id`; trigger direction classified by the `strategies.yaml` factor `side` map.
- Event + staleness: `bitunix_score_decided` payload (`buy_contributions`/`sell_contributions`) + `bitunix_signal_ledger` fire-time ages.
