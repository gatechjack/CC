# Fee-RATE correction (STEP 1) — staged, Board-gated

**Status:** STAGED. Config-only but affects live gating → Board-gated, flat-window
apply, **restart required to load** (FeeConfig reads at startup). NOT deployed.

## What & why
H1 confirmed the fee model over-states fees ~2.1×: model bills both legs at the
headline VIP3 **taker 0.04%**; 6 live trades show **~0.019%/leg venue-actual**
(Fee Discount/Experience Card, ignored by the config). The model's rate should
reflect reality.

**One source (confirmed):** the live gate (`fees_too_high_for_risk`) and the TP1
fee floor both flow through `FeeConfig.round_trip_cost_pct()`
(`trade_plan.py:49`), fed by prod `strategies.yaml` `bitunix_futures.fees`. The
`trade_plan.py` defaults are only the fallback. The backtest sim has its own
FeeConfig (Step 2's input, NOT live gating).

## The change (surgical, one line)
`strategies.yaml` `bitunix_futures.fees.taker_pct: 0.0004 → 0.00019`
(+ comment). Since `entry_is_taker:true` and `tp_is_maker:false`, the model bills
both legs at `taker_pct`, so `round_trip_cost_pct` goes **0.0009 → ~0.00048**.

## SCOPE GUARD (important)
This corrects the **RATE only** — a correctness fix. It does **NOT** touch the
gate's risk multiplier or threshold. The gate will naturally pass more signals
because the projected fee is now lower/true; **whether to ACCEPT that looser gate
is Step 2's net-edge decision**, not this change. `maker_pct`, `entry_is_taker`,
`tp_is_maker`, slippage, B2 keys, and every other division's fees — untouched.
Role label NOT used (D3 still mis-records every leg maker); the 0.019% is from
`fee_usd` venue truth.

## Apply (operator-run, flat window; agent SSH read-only)
```
scp apply_feerate.sh VERIFY.sh azureuser@trading.jacksumner.com:~/
ssh azureuser@trading.jacksumner.com "bash ~/apply_feerate.sh"     # no sudo; surgical sed
ssh -t azureuser@trading.jacksumner.com "sudo systemctl restart trading-corp"
ssh azureuser@trading.jacksumner.com "bash ~/VERIFY.sh"
```
The apply: drift-gate (current must be `0.0004`) → backup
`*.bak-pre-feerate-2026-06-22` → block-scoped sed → re-verify `0.00019` →
yaml-parse check → diff (must be ONE line) → self-rollback on any failure. NO
restart. Rollback: `cp <bak> <cfg>` + restart.

## Validation done
sed dry-tested on a sample of the real block: changes ONLY the bitunix
`taker_pct`, leaves `kalshi_arbitrage.fees.taker_pct` and all else untouched.

## Gate on apply
**Approvable on its own** (it's just the true rate). But the operator asked to
hold the apply until Step 2's gate-flip net-edge verdict is in — because the
*consequence* (a looser gate) should be accepted knowingly. Apply + restart only
on the Step-2-informed ruling.
