# Fee COUPLED correction (Decision A) — staged, supersedes the rate-only stage

**Status:** STAGED. **Supersedes** `deploy/2026-06-22_fee_rate_correction/` (apply
THIS one, not the rate-only). Board-gated, flat-window, restart-to-load. NOT applied.

## Framing — HONESTY fix, not a profitability fix
Step 2 confirmed (3rd time) that **fees/gating are NOT the money lever**: at *half*
the fee, admitting more trades = *more* losses, because gross edge is ~zero and the
tight stops clip wins below losses. The lever is gross edge / regime (short-only +
bull-starved). This change makes the model **truthful** while keeping the **exact
same gating behavior** — it does not chase profit.

## The two coupled changes (bitunix only)
| key | from | to | why |
|---|---|---|---|
| `fees.taker_pct` | 0.0004 | **0.00019** | venue-actual ~0.019%/leg (Fee Discount Card); headline VIP3 0.04% was ~2.1× high |
| `trade_plan.tp1_min_profit_multiplier` | 2.0 | **3.75** | ×1.875, holds `fee_floor` identical at the corrected rate |

## Re-derivation (algebraic identity, replay-confirmed)
The gate (`trade_plan.py:226-252`) skips when `tp1_distance >= tp2_distance`, where
`tp1_fee_floor = tp1_min_profit_multiplier × round_trip_cost_pct × entry`.
- Rate correction: `round_trip_cost_pct` 0.0009 → **0.00048** (taker both legs +
  unchanged slippage 0.0001).
- To hold `fee_floor` constant: `mult × round_trip` must be unchanged.
  `2.0 × 0.0009 = 0.0018`. `mult_new × 0.00048 = 0.0018` ⇒ **mult_new = 3.75**.
- **`3.75 × 0.00048 = 0.0018 = 2.0 × 0.0009` for EVERY entry** → the gate skips the
  *exact same* trades and TP1 lands in the *exact same* place. **Flipped cohort = 0
  by construction; admitted book unchanged; not more conservative than today.**

Step-2 showed the rate-only change would un-gate 183 net-negative trades (−0.368R,
6/6 windows); this coupled change re-gates them (fee_floor preserved) on the true rate.

## Surgical confirmation
Block-scoped sed dry-tested on a real-shaped sample: changes ONLY the 2 bitunix
lines; `bitunix.atr_multiplier` (also 2.0), `kalshi.tp1_min_profit_multiplier`,
`kalshi.taker_pct`, and all else untouched; diff bounded to exactly 2 lines. Gate
LOGIC untouched (only the rate value + the multiplier value). Role label not used
(D3 still mis-records; 0.019% from `fee_usd` venue truth).

## Replay verification
Sim re-run at (corrected rate 0.00019 + re-derived mult 3.75) vs baseline
(0.0004, 2.0) confirms the `fees_too_high_for_risk` skip set is identical and the
flipped-cohort count returns to ~0. (See the verdict note / fee_gate_flip report.)

## Apply (operator-run, flat window; agent SSH read-only)
```
scp apply_fee_coupled.sh VERIFY.sh azureuser@trading.jacksumner.com:~/
ssh azureuser@trading.jacksumner.com "bash ~/apply_fee_coupled.sh"   # no sudo
ssh -t azureuser@trading.jacksumner.com "sudo systemctl restart trading-corp"
ssh azureuser@trading.jacksumner.com "bash ~/VERIFY.sh"
```
Drift-gate (taker 0.0004 AND mult 2.0) → backup `*.bak-pre-feecoupled-2026-06-22` →
sed → re-verify both → yaml-parse → diff==2 lines → self-rollback. NO restart.
Rollback: `cp <bak> <cfg>` + restart.

## Gate on apply
Hold until the operator rules on this staged coupled change. It's an honesty fix
with provably-unchanged behavior — low risk — but it's still a live-gating config
+ a restart, so flat-window Board-gated like the others.
