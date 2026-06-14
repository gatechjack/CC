# Bitunix fee-gate deep-dive + 5-day declined-fire what-if (P1)

**Date:** 2026-06-14 · **Session:** operator-supervised, read-only, agent-driven SSH (policy `82fda13`)
**Branch:** `bitunix-fee-gate-analysis-2026-06-14` (dedicated worktree; unmerged audit trail)
**Window:** 2026-06-09 03:49 UTC (fresh-window anchor) → 2026-06-14 ~18:24 UTC (capture)
**Scope:** ANALYSIS ONLY. No code, no config, no parameter change. Any fee/threshold change is
CLAUDE.md §4 Backtester-gated, downstream of this report.
**Builds on / reconciles with:** `runbooks/board_memo_bitunix_fee_floor_decision_2026_05_25.md`
(DECIDED), `reports/2026-05-29_bitunix_fee_floor_three_rule_audit.md`,
`reports/2026-06-10_bitunix_untaken_trades_deep_dive.md` (q3harness lineage).

> **STATUS: COMPLETE.** Reconstruction validated (V1 43/43, skip-reproduce 88/88); the 5-day
> forward replay is **fully observed at 3m granularity — 0 ambiguous bars, 0 assumptions.**

> **HEADLINE — the operator's read does NOT hold on this data, and I show why.** Over 5 days the
> gate declined **88 fires** (77 unique setups), all sell/STANDARD. Walked against **actual
> forward price**, the declined set is **gross +0.039R/trade** (barely break-even with **zero**
> fees) → **net −0.87R at taker, −0.61R at maker.** Only **6 of 77 (8%)** are net-taker-positive.
> The trades the operator watched reach TP on the chart are real (67% touch TP1) — but a TP1-only
> "win" books **+0.125R gross / −0.75R net** because the round-trip fee on a 0.1%-of-price stop is
> ~0.87R. **The fee gate is correctly rejecting sub-fee-threshold scalps, not blocking edge.**
> Lowering the floor (fix a) admits net-losers; maker execution (fix b) does **not** rescue them
> (the maker-admittable band is **net-maker −0.62R**). **Recommendation: HOLD the gate at 2.0.**

---

## 0. Constraints, hard stops, disclosure

- Read-only throughout. **Agent-driven read-only SSH** (`sqlite3 -readonly`, `grep`/`sed` file
  reads) per CLAUDE.md `82fda13`. **No DB writes, no config writes, no service actions, no
  public-API GETs.** (3m bars came from the prod `bitunix_bar_history` archive; no 1m Bitunix-API
  fetch was needed — 0 ambiguous bars.)
- **Hard stops honored:** any param/threshold/fee change → STOP (none made — analysis only);
  any prod write → STOP (none); if declined set is net-NEGATIVE even gross → say so plainly
  (it is barely gross-*positive* +0.039R but decisively net-negative — stated without softening).
- **`execution_mode: live`** on prod (confirmed `strategies.yaml:1022`; go-live 2026-06-13). The
  declined set deployed **zero capital** (a skip places nothing), so this read-only decision-row
  analysis is clean and does not touch live trading.
- **Reproducibility:** engine `fgharness.py`, data `qdata2.out`, full run `fgout.txt`, probe
  `p1.sh`/`qdata2.sh` — all committed to this branch. Engine adapts `q3harness.py`
  (`bitunix-untaken-trades-deep-dive-2026-06-10`); reuses the strategy's OWN `build_trade_plan`.

---

## Q1 — What the gate computes, and on what fee assumption

### Code site (`trading_corp/agents/strategies/trade_plan.py`)

```
210  fee_cost_per_unit = fees.round_trip_cost_pct() * entry
211  tp1_target_distance = cfg.tp1_r_target * risk_per_unit          # 0.5R
212  tp1_fee_floor = cfg.tp1_min_profit_multiplier * fee_cost_per_unit # 2.0 × round-trip
213  tp1_distance = max(tp1_target_distance, tp1_fee_floor)
216  tp2_distance = cfg.tp2_r_default * risk_per_unit                # 1.0R (or HTF snap)
...
234  # Skip-trade: fee floor pushed TP1 past TP2 — trade has no edge.
235  if tp1_distance >= tp2_distance:
236      return _skip(entry, "fees_too_high_for_risk")
```

`round_trip_cost_pct()` (`trade_plan.py:41-46`): `entry_fee + exit_fee + 2×slippage`, where
`entry_fee = taker if entry_is_taker else maker` and `exit_fee = maker if tp_is_maker else taker`.

### Active fee config — verified on PROD (`config/strategies.yaml:1343-1348`, read 2026-06-14)

| key | value | |
|---|---|---|
| `taker_pct` | 0.0004 | 0.04% |
| `maker_pct` | 0.00014 | 0.014% (3× cheaper) |
| `slippage_pct` | 0.00005 | 0.5 bps/leg |
| `entry_is_taker` | **true** | market entry on signal — correct (taker) |
| `tp_is_maker` | **false** | MVP: market exits → **exit priced at TAKER** |
| `tp1_min_profit_multiplier` | **2.0** | TP1 must clear 2× round-trip |

Prod config is **byte-identical to repo** — Q1 holds against production, not just main.

### Derived gate behavior

- **Round-trip (current, both taker):** `0.0004 + 0.0004 + 2×0.00005 = 0.00090 = 0.090% of entry`.
- **Fee floor:** `2.0 × 0.090% = 0.180% of entry`. With default-1R TP2, the gate trips when
  **`risk_per_unit (1R) ≤ 0.180% of entry`** — i.e. it rejects trades whose stop is tighter than
  0.18% of price (~$115 at BTC $64k).
- **The over-conservatism the operator suspected is real, and it is on the EXIT leg.** The TP legs
  are limit orders that fill **maker (0.014%)**, but `tp_is_maker:false` prices them at **taker
  (0.04%)**. Correcting the exit to maker drops round-trip to **0.064%** and the floor to
  **0.128% of entry**. (Entry-at-taker is correct — a market entry on the signal bar IS a taker
  fill.) So the gate over-prices the round trip by ~0.026% (≈29%).
- **Fee drag vs the TP1–TP2 spread it rejects against:** the gate compares `tp1_fee_floor`
  (0.18%·entry) against `tp2_distance` (1R). For the declined set, **median 1R = 0.103% of entry**
  — i.e. 1R is only **~0.57×** the taker fee floor. The round-trip fee alone (0.09%·entry) is
  **~0.87× the entire 1R risk unit.** That is the arithmetic the gate encodes: when your stop is
  ~0.1% of price and the round trip costs ~0.09% of price, fees consume ~0.87R before any edge.

### Q1 fee-rate currency caveat (flagged, immaterial to the verdict)

`strategies.yaml:1340` warns the **VIP3 Experience Card may be time-limited** ("verify whether base
VIP3 rate persists after expiry"). The live account fee tier is a Bitunix account property, not in
the DB, and there have been **no live fills yet** (awaiting first fill), so it is unverifiable from
prod telemetry today. Directionally this only **strengthens** the verdict: if the card expired, the
real taker rate is **≥** the assumed 0.04%, making the gate's 0.09% round-trip *optimistic* and the
declined set *even more* net-negative. The gate is conservative in the right direction.

---

## Q2/Q3 — The 5-day declined-fire what-if (empirical forward replay)

### Method

The 88 `trade_plan_decision` rows with `skip_reason=fees_too_high_for_risk` store the exact
decision inputs (`entry`, `score_side`, `inputs.{atr_used, swing_low, swing_high, resistance,
support}`). For each: feed the **stored inputs** to the strategy's own `build_trade_plan` with a
**zero-fee `FeeConfig`** → the pure structural plan (TP1=0.5R, TP2=1R/HTF-snap, TP3=2.5R, SL=stop);
walk it forward on archived 3m bars (`bitunix_bar_history`) with the v2 reconciler lifecycle
(SL-first on tie, ordered TP fills, BE-after-TP1 / TP1-after-TP2 ratchet); take **gross R**; deduct
fees as R-normalized drag: `feeR = round_trip% × entry / risk`. Net-taker uses 0.090%, net-maker
0.064%. Fee treatment matches the 2026-06-10 deep-dive.

**Reconstruction rigor (both gates PASS):**
- **V1 — `build_trade_plan`(stored inputs, taker) == stored fired plan: 43/43.** My pipeline
  exactly reproduces the system's real plans (confirms `StrategyConfig()` defaults == prod).
- **Skip-reproduce — taker build reproduces `fees_too_high_for_risk`: 88/88.** Every system decline
  is faithfully reconstructed from the stored inputs (zero extraction error).
- **0 ambiguous 3m bars across all 77 resolved** (no bar touched both SL and an unfilled TP) →
  verdict is **fully observed at 3m, not assumption-driven.** No 1m escalation required.
- Dedup: 88 raw → **77 unique setups** (11 same-bar/same-side repeats removed). All 77 resolved
  (0 ran past available bars).

### Aggregate (77 unique resolved setups — all sell/STANDARD)

| basis | expectancy / trade | cumulative (77) |
|---|---:|---:|
| **GROSS (zero fees)** | **+0.039R** | +2.97R |
| **NET-TAKER (0.090% rt — current)** | **−0.869R** | −66.94R |
| **NET-MAKER (0.064% rt — fix b)** | **−0.607R** | −46.74R |

52W / 25L on gross — but the gross win-rate is the trap (see anatomy). With **zero fees** the
declined set is a coin-flip scalp (+0.04R). Any real fee assumption drives it deeply negative.

### Outcome anatomy — why the chart lies (the operator's live read, resolved)

| deepest leg reached | n | gross | **net-taker** | net-maker |
|---|---:|---:|---:|---:|
| **tp1-only** (chart shows a win) | 21 | +0.125R | **−0.749R** | −0.496R |
| tp1 + tp2 | 25 | +0.714R | −0.235R | +0.039R |
| tp1 + tp2 + tp3 (true runner) | 6 | +1.250R | **+0.178R** | +0.488R |
| stopped out | 25 | −1.000R | −1.856R | −1.609R |

- **67% (52/77) reach at least TP1** — this is exactly what the operator sees on the live chart:
  "price came down to my target." It is real, and it is misleading.
- **Only 6 of 77 (8%) are net-taker-positive** — the genuine three-leg runners.
- A **tp1-only win books +0.125R gross but −0.749R net** (TP1 takes 25% at 0.5R, SL ratchets to
  break-even, the remaining 75% scratches at BE — then the full round-trip fee, ~0.87R, lands on
  the whole position). 21 of these. **This is the single biggest gap between "looks tradeable" and
  "is profitable."**
- **A third (25/77) stop out** at −1R gross / −1.86R net-taker.

### Q3 decisive answer

The declined trades are **net-negative at taker (−0.87R) AND at maker (−0.61R)**, on barely-break-even
gross (+0.04R). **The gate is not over-denying — it is correct.** The operator's read holds only in
the fee-free / per-chart-touch sense and collapses under any real fee assumption. Stated plainly:
**on this 5-day, fully-observed sample, the declined trades did not have edge that survives fees —
not taker, not even maker.** The gate saved the book ~**67R** (net-taker) over the window.

---

## Q4 — Fix evaluation, grounded in the Q2/Q3 data

### (a) Lower `tp1_min_profit_multiplier` / relax the floor — **REFUTED**

To admit the declined set you must drop the multiplier below each trade's `m*` (the multiplier at
which it just clears): **median m\* = 1.13, max 1.97** across the 77. Lowering 2.0 → ~1.1 admits
~half the set; lowering further admits the rest. The admitted trades carry **net-taker −0.87R**.
There is **no admit-threshold that selects winners**: the only net-taker-positive bucket is the
6 three-leg runners (m\* spread across the range — not separable by the multiplier knob). Lowering
the floor admits fee-losers wholesale. **Downside: ≈ −0.87R per newly-admitted trade.** This
confirms the **2026-05-25 Board rejection of this lever** — now on empirical (not theoretical) data.

### (b) Maker execution (`tp_is_maker:true`, the deferred B2) — **does NOT rescue the declined set**

- The trades maker pricing would admit (the 0.128%–0.18% band) number **18**, and they are
  **net-maker −0.62R** — confirmed both ways: structural-plan walk −0.6162R and the **realistic
  fix-(b) plan** (TP1 placed at the maker floor, not 0.5R) −0.6215R (0 ambiguous). Their **gross is
  −0.20R** (9W/9L) — the widest-stop declines have *negative* structural edge.
- **Correction to the prior art's framing:** the 2026-05-25 memo §9(b) called `tp_is_maker:true` a
  "strict improvement, same selection — every trade that fires under the new floor would also have
  fired under the old." **That is backwards.** The flag is a *single* knob that couples
  cost-reduction with **floor-relaxation** (0.18%→0.128%): flipping it admits the 18-trade band,
  which this replay shows is net-negative. So (b) as a config flip is **not** selection-neutral.
- **Where (b) is genuinely right:** the *cost-reduction* half of the flag helps the trades that
  **already fire**. The 2026-06-10 deep-dive showed the taken set is **+0.175R gross → −0.13R
  net-taker**; booking maker exits on those would lift them toward break-even. That is the correct,
  Board-approved role for (b) — a cost lever for the admitted set, gated on its fill-rate model —
  **not** a fee-gate relaxation. To get the cost benefit *without* the loss-admitting selection
  expansion, the floor's fee-basis must be **decoupled** from the booking fee-basis (keep the floor
  at the taker/2.0 level; book actual maker fees on fills). That is a code change, not a flag flip.

### (c) Fee-assumption correction — real, but admits losers

Q1 confirms the gate over-prices the exit at taker. Correcting it to maker drops the floor to
0.128%·entry — which is **exactly fix (b)'s floor**, admitting the same 18 net-maker-negative
trades. So the over-conservatism is real but **immaterial**: the trades it would admit lose even at
the corrected (maker) fee. The fee-gate **floor should stay on the taker basis (0.18%).**

---

## Recommendation (strong)

**HOLD the fee gate at `tp1_min_profit_multiplier = 2.0` and `tp_is_maker:false`. Change nothing in
the gate.** The 88 declines are not blocked edge; they are sub-fee-threshold scalps the gate
correctly rejects (net −0.87R taker, −0.61R maker, only 8% net-positive). This **confirms the
2026-05-25 Board decision** on empirical forward-replay evidence and **refines it**: the
maker-flip (b) is a cost lever for the *taken* set, **not** a fix that unlocks profitable *declined*
trades — and as a single config flag it relaxes the floor and admits net-losers, so it must not be
deployed as a fee-gate change.

What the operator's live observation actually points to is **upstream structure, not the fee gate**:
the declines exist because low-vol BTC produces stops ~0.1% of price (median 1R = 0.103%·entry),
too tight to pay the round trip. The levers that change *which* trades qualify by producing **wider
structural stops** (bigger R → fees a smaller fraction of R) — `swing_max_lookback`,
`tp2_r_default` — are the only path to "more tradeable Bitunix setups," and they were already
**approved-to-backtest (not to ship)** in the 2026-05-25 memo §9(c). They carry overfit risk and
are §4-gated. Do not pursue them on this 5-day low-vol sample alone.

### §4 Backtester-validation plan (for whichever lever the Board pursues)

This report is the evidence base; any parameter change is §4-gated. Decision criterion stays
**net-of-cost expectancy per fire, holding other gates fixed** (never fire-rate). Required:

1. **If (b) maker-exec is pursued** (for the taken-set cost benefit): build the **maker-fill-rate
   model** the 2026-05-25 memo §9(b) already specified (historical limit-fill rate at v2 TP1/TP2/TP3
   by symbol × ATR regime, incl. un-filled-revert-to-taker and partial-fill semantics). **Additional
   requirement this report adds:** the deploy must NOT lower the fee-gate floor — either keep
   `tp_is_maker:false` for the floor while booking maker on fills (code change), or accept and
   document that flipping the flag admits the net-maker-negative 0.128%–0.18% band (refuted here).
2. **If a structure lever (`swing_max_lookback` / `tp2_r_default`) is pursued:** backtest over a
   corpus spanning **≥ one ATR-regime rotation** (a high-vol window + this low-vol window), per memo
   §5/§9(c). Must show net-of-cost expectancy ≥ current while holding the fee gate fixed.
3. **No lever clears on this sample alone.** 77 unique setups, single regime (BTC ATR ~low,
   short-only), 5 days. It is decisive for "the declined trades lack post-fee edge"; it is **not** a
   generalizable basis for a parameter change.

---

## Reconciliation with prior art

| prior finding | this report |
|---|---|
| 2026-05-25 memo §9: **reject lowering the multiplier (a)** | **Confirmed** empirically (−0.87R net-taker on admitted set) |
| 2026-05-25 memo §9(b): maker-flip = "strict improvement, same selection" | **Corrected** — the flag couples cost + floor-relaxation; admits net-maker −0.62R band |
| 2026-05-29 audit: maker would "unlock 6 positive-EV of 15" (1.5× blended-EV proxy) | **Overturned** for the declined set — forward-replay shows the maker-admittable band is net-maker −0.62R; the theoretical proxy over-counted edge |
| 2026-06-10 deep-dive: taken set +0.175R gross → −0.13R net-taker | **Extended** — declined set is thinner still (+0.04R gross), as expected for tighter stops; (b)'s correct role is the taken-set cost fix |
| §7 meta-rule: don't re-litigate on fire-rate before 2026-06-19 | Respected — this is empirical *calibration* (the §7 "something new"), §4-gated; **verdict is to HOLD**, not to loosen |

---

## Limitations

- **Single regime:** entire window is low-vol, short-only BTC (sell/STANDARD only; ATR ~low). The
  fee arithmetic generalizes; the *expectancy magnitude* is regime-specific.
- **Structural plan walk:** gross uses the fee-independent geometry (TP1=0.5R); fees deducted
  post-hoc. The realistic fix-(b) plan (TP1 at maker floor) was also walked for the maker subset and
  agrees (−0.62R). The reconciler's post-TP2 Chandelier trail is not modeled (walk floors SL at TP1
  post-TP2, matching q3harness) — conservative on the runner upside, immaterial to the verdict.
- **Paper-vs-live:** all declines are decisions (no capital). No live fills exist yet, so realized
  fees are still the *assumed* config rates — see the Q1 Experience-Card caveat (only worsens net).
- **Intrabar:** 0 ambiguous 3m bars, so no SL-vs-TP tie-break assumptions were needed.
