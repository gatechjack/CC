# Bitunix entry-timing analysis — does confirmation latency eat the move?

**Date:** 2026-06-14 · **Session:** operator-supervised, read-only, agent-driven SSH (policy `82fda13`)
**Branch:** `bitunix-entry-timing-analysis-2026-06-14` (dedicated worktree; unmerged audit trail)
**Window:** 2026-06-09 → 2026-06-14 (same 5-day fresh-window set as the fee analysis) + the 1 live fill
**Scope:** ANALYSIS ONLY. No code, no config, no param/threshold/latency-config change. Any fix is
CLAUDE.md §4 Backtester-gated, downstream of this report.
**Complements:** `reports/2026-06-14_bitunix_fee_gate_analysis.md` (tested *edge at the system's entry
point* → declines net-negative, HOLD the fee gate). **This tests *entry timing* of the trades that
DID fire.** Fee tier confirmed VIP3 taker 0.04% / maker 0.014% (live, not stale).

> **STATUS: COMPLETE.** Reconstruction validated (recorded entry ≈ signal-bar close: mean 0.05%,
> max 0.31%; non-redeem recorded R ≈ reconstructed early R: mean |Δ|=0.10). Per-stage latency
> confirmed empirically from the audit chain.

> **HEADLINE — the operator's theory is CONFIRMED, and the culprit is specific: the PA-redeem
> deferred-entry mechanism.** The synchronous gate chain (score→PA→HTF→trade-plan→placement) runs
> **sub-second** (0 bars). But **64% of fires (27/42) are PA-redeem-rescued, and 40% (17/42) fire
> ≥1 bar late — up to 25 bars (75 min).** For those redeem fires, entering at **signal time** vs the
> **actual (late) confirmation time** is worth **+0.48R/trade gross** (early +0.31R → late −0.17R;
> net-taker +0.01R → **−0.47R**). **6 of 17 redeem fires flip win→loss** between early and late entry;
> a median **40%** of the signal→TP1 move is already spent by fire-time (8/17 >50% spent). The gates
> pick *good setups* (profitable at signal time) and the redeem loop *enters them too late*. This is
> **(i) latency**, not (ii) threshold-looseness, not (iii) no-move. **Recommendation: cap/remove the
> PA-redeem deferred entry (fire-fast-or-abandon).** Caveat: even at perfect timing the set is only
> net-taker −0.15R overall — latency is necessary-not-sufficient, consistent with the fee report.

---

## 0. Constraints, hard stops, disclosure

- Read-only throughout. **Agent-driven read-only SSH** (`sqlite3 -readonly`, file reads) per `82fda13`.
  **No DB writes, no config writes, no service actions, no public-API GETs** (3m bars from the prod
  `bitunix_bar_history` archive).
- **Hard stops honored:** any param/threshold/latency-config change → STOP (none — analysis only);
  any prod write → STOP (none); commit to the polymarket branch → STOP (this is on a dedicated
  `bitunix-*` branch); **if early-entry is NOT materially better → say so plainly** (it IS materially
  better, +0.48R on the redeem subset — stated with the data, not asserted). **Latency (Q1) and the
  counterfactual (Q2) are kept distinct** — Q1 measures *when* entries happen, Q2 measures *what the
  delay costs*.
- **`execution_mode: live`** on prod (go-live 2026-06-13); 1 live fill so far. This analysis is
  read-only over recorded decisions + archived bars — it touches no live state.
- **Reproducibility:** engine `etharness.py`, data `qdata3.out`, full run `etout.txt`, probes
  `p2.sh`/`p3.sh`/`qdata3.sh` — all committed to this branch. Engine reuses the fee analysis's walk
  (SL-first tie, ordered TP fills, BE-after-TP1/TP1-after-TP2 ratchet); `trade_plan.py` file-loaded.

---

## Q1 — Gate-chain latency

### The synchronous chain is sub-second (0 bars)

`_score_and_maybe_propose_locked` (`bitunix_futures_observer.py:1217`) runs score→PA→HTF→trade-plan
→placement in **one async pass**. Empirically (non-redeem fire `cvd_bear_flip`, 2026-06-09):

```
04:57:01  pa_validation_decision
04:57:01  htf_gate_decision
04:57:02  trade_plan_decision        ← whole chain inside 1 second
04:57:02  bitunix_score_decided
```

So score→PA, PA→HTF, HTF→trade-plan→placement each add **~0s / 0 bars**. There is **no latency in the
synchronous chain.**

### PA-redeem is the ONLY multi-bar latency source

When PA rejects in enforce mode, the payload is cached and `run_pa_redeem_loop`
(`observer.py:1158`) re-runs score+PA **every 60s** against fresh bars until PA passes (or the score
decays). Empirically (redeem fire `mc_b_sell_circle_div`, cached 05:42:03):

```
05:48:01 … 05:57:38   pa_validation_decision = REJECT   (~60s cadence, 11+ rejects)
05:58:38  pa_validation_decision = PASS
05:58:38  pa_validation_redeem / htf_gate_decision / trade_plan_decision   ← fires instantly on pass
```

PA rejected for **16.5 minutes** (5 bars), then the rest of the chain completed sub-second. The
entry is recorded at the **original signal price** (`entry_price = payload["price"]`,
`observer.py:1240`; redeem reuses `dict(self._pending_pa_payload)`, `:1182`) — so a redeem fire books
a **stale signal-time price** but actually fires `bars_waited` bars later. `seconds_waited` matches
`fire_ts − original_cached_at` exactly (994s↔16:35, 431s↔7:13, …).

### Latency distribution (42 fires)

| cohort | n | share | latency |
|---|---:|---:|---|
| First-pass (no redeem) | 15 | 36% | 0 bars (sub-second) |
| Redeem, `bars_waited=0` (same-bar, <180s) | 10 | 24% | ~0 bars |
| **Redeem, `bars_waited ≥ 1`** | **17** | **40%** | **1–25 bars (3–75 min)** |

`bars_waited` for the ≥1 cohort: 1,1,1,2,4,5,5,5,5,5,7,10,11,13,14,21,**25**. **29% of fires (12/42)
wait ≥5 bars (≥15 min).** The operator's "the chain takes too long" is real — but located entirely
in PA-redeem, not the gate chain.

---

## Q2 — Counterfactual: early (signal-time) vs actual (confirmation-time) entry

### Method

For each fire, hold the recorded plan **geometry fixed** (stop-distance + TP1/TP2/TP3 R-multiples,
from the matched `trade_plan_decision` row) and re-anchor entry at **(a) the signal bar** vs **(b) the
fire bar**, then walk each forward on 3m bars. Signal bar = `original_cached_at` (redeem) else fire
ts. For non-redeem fires signal bar == fire bar, so **delta ≡ 0 by construction** — all latency cost
is isolated in the redeem cohort. Net at confirmed VIP3 fees (taker 0.09% rt / maker 0.064% rt).

### Result (41 closed fires; 1 live fire still open, excluded)

| cohort | basis | gross | net-taker | net-maker |
|---|---|---:|---:|---:|
| **Redeem (bw≥1, n=17)** | **(a) EARLY** | **+0.310R** | **+0.014R** | +0.099R |
| | **(b) LATE (actual)** | **−0.171R** | **−0.467R** | −0.382R |
| | recorded paper | −0.077R | (fee-free) | — |
| | **DELTA (early−late)** | **+0.481R** | +0.481R | +0.481R |
| Non-redeem (bw=0, n=24) | (a)=(b) | +0.066R | −0.267R | −0.171R |
| **All closed (n=41)** | (a) EARLY | +0.167R | −0.151R | −0.059R |
| | (b) LATE (actual) | −0.032R | −0.350R | −0.258R |
| | **DELTA** | **+0.200R** | +0.200R | +0.200R |

**The decisive numbers:**
- On the redeem cohort, **early entry beats the actual late entry by +0.48R/trade** (gross +0.31R →
  −0.17R). At taker fees the swing is net **+0.01R → −0.47R**: signal-time entry is break-even, the
  late entry is a half-R loser.
- **6 of 17 redeem fires flip win→loss** between early and late (e.g. `06-09T05:58` +0.20R→−1.0R,
  `06-09T22:55` +0.91R→−1.0R, `06-10T18:51` +0.78R→−1.0R). 1 flips loss→win (the late entry caught a
  re-test). The rest are unchanged.
- Non-redeem delta is exactly 0 — confirming the cost is **entirely** the redeem deferral.
- **Live-flip note:** paper records redeem fires at the **stale signal price** (−0.077R gross), ~0.09R
  better than the real late fill (−0.17R gross) and far above the realized late-entry net-taker
  (−0.47R). The recorded paper track record is **optimistically biased** for the 40% of fires that
  are redeem-delayed — a second entry-side caveat stacking on the fee report's fee-side caveat.

---

## Q3 — Where does the move go relative to confirmation?

For redeem fires (bw≥1), fraction of the **signal→TP1** favorable move already spent by the time the
trade actually fires:

- **median 40%**, max 148% (move fully spent *and reversed* before entry), n=17.
- **8 of 17 (47%) had >50% of the TP1 move already gone** before the late entry.

So for nearly half the redeem fires, most of the move the strategy was trying to catch was already
over by the time PA confirmed and the order placed. The edge is in the move; the redeem loop arrives
after it.

---

## Q4 — Which fix does the data point to?

**(i) Latency reduction — YES.** The gates select setups that are profitable at signal time (redeem
cohort early gross +0.31R, net-taker break-even); the redeem deferral enters them after the move is
spent, turning the edge into a −0.47R net-taker loss. The gates are *correct but the entry is late.*

**(ii) Threshold/gate looseness — NO.** The gates don't admit bad trades (fee report covers that) and
don't reject good trades *outright* — they admit the redeem cohort, just late. PA's rejection at
signal time is a *deferral*, not a veto, so this is a timing problem, not a threshold problem.

**(iii) No-move / theory falsified — NO** for the redeem cohort (early entry is materially better,
+0.48R). **But partially yes at the whole-strategy level:** even at perfect (early) timing, all-41 is
net-taker **−0.15R** and the non-redeem cohort is **−0.27R** — latency reduction recovers the redeem
cohort to break-even but does **not** make the strategy net-positive at taker. Latency is *a* problem,
not the *only* problem (consistent with the fee report).

### Recommendation (strong)

**Cap or remove the PA-redeem deferred-entry mechanism — fire-fast-or-abandon.** Specifically: if PA
has not confirmed within ~1 bar of the original signal, **drop the setup** rather than fire a stale
late entry. Rationale, from the data:
1. **Late redeem entries are value-destructive**: net-taker **−0.47R/trade** (realized, late fill +
   fees) vs the non-redeem cohort's −0.27R. The redeem mechanism *adds losers.* Removing the bw≥1
   cohort improves the book.
2. **The +0.31R early edge is NOT capturable by "going faster."** PA fundamentally rejects for 1–25
   bars until price action confirms (empirically, ~60s re-evals rejecting for up to 75 min). You
   cannot make PA confirm sooner without changing PA's logic — which means firing on *unconfirmed*
   signals (lever (ii)), a different and riskier hypothesis (it would also admit the noise PA exists
   to filter). The realizable latency fix is to **avoid the late entry, not chase the early one.**
3. **It fixes the paper-optimism** too: capping redeem removes the stale-signal-price fires that
   inflate the paper track record.

**Explicitly NOT recommended:** loosening PA to fire at signal time. The early entries look good in
hindsight, but that requires firing before confirmation — untested here (would need the full
*PA-rejected-and-never-rescued* set walked at signal-time entry to know if early-firing ALL signals
is net-positive or just admits noise). That is a separate §4 hypothesis, not this one.

### §4 Backtester-validation plan

Decision criterion: **net-of-cost expectancy per fire, holding all other gates fixed** (never
fire-rate). Required:
1. **Backtest the redeem cap** over a mixed-regime corpus (≥1 ATR-regime rotation): compare
   `current redeem` vs `cap at 1 bar` vs `no redeem`. Expected: removing the bw≥1 cohort (net-taker
   −0.47R realized) lifts net-of-cost expectancy. Must confirm the dropped cohort is net-negative
   across regimes, not just this low-vol window.
2. **Model the realistic late fill** in the backtest (entry at the fire-bar price, not the stale
   signal price) — this report shows paper's stale-price booking overstates redeem economics by
   ~0.09R gross + the fee drag. The backtest must price redeem fires at the late fill.
3. **(Separate, optional) the (ii) hypothesis** — fire-at-signal without PA deferral — needs the full
   PA-rejected signal set walked at signal-time entry to test whether early-firing is net-positive or
   admits noise. Do not bundle it with the redeem-cap test.

---

## Reconciliation with the fee analysis (complementary, both correct)

| | Fee analysis (entry *edge*) | This analysis (entry *timing*) |
|---|---|---|
| Tested | the 88 declined fires + taken-set economics | the 42 fired trades' entry timing |
| Found | declines net-neg even gross → **HOLD the fee gate** | redeem fires lose +0.48R to latency → **cap redeem** |
| Lever | NOT loosen the fee gate | NOT loosen the thresholds — fix the redeem latency |
| Live-flip caveat | fees: tight stops, net-neg at taker AND maker | timing: paper books stale signal price, optimistic |

**Combined picture:** the strategy has **two independent drags** — fee cost on tight-stop trades
(correctly gated) and entry latency on the 40% of fires that are redeem-delayed (fixable by capping
redeem). **Neither finding says "loosen the gates."** Fixing latency recovers the redeem cohort from
net-taker −0.47R to break-even, but the strategy is still net-taker −0.15R at ideal timing — so it
needs maker execution and/or better setups to be clearly live-viable. Both reports independently
caution against flipping live on the paper numbers.

---

## Limitations

- **Single regime:** low-vol, short-biased BTC; n=41 closed fires, 1 live. The latency *mechanism*
  generalizes; the *magnitude* is regime- and sample-specific.
- **Counterfactual geometry held fixed:** stop-distance + TP R-multiples from the recorded plan,
  re-anchored to each entry price. The structure (swings/levels) at the signal bar vs fire bar could
  differ slightly for redeem fires; holding it fixed isolates the pure entry-price/timing effect and
  matches the operator's "same stop/TP structure for both." Recorded-vs-reconstructed agree to ~0.10R
  mean (non-redeem), within walk-approximation noise.
- **Intrabar:** the walk uses 3m bars with SL-first on ties (worst-case), same as the fee harness;
  no 1m escalation performed (the counterfactual delta is driven by the multi-bar pre-fire move, not
  intrabar ties).
- **Late-fill price** approximated by the fire-bar close. The true live fill is the market price at
  placement; bar close is the best available proxy from the archive.
