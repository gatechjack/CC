# D3 role-recording fix — STAGED (Board-gated, NOT deployed)

## What / why
Record maker/taker role from **order semantics** (which order the bot placed), not
BitUnix's `roleType` trade-history field — which is **unreliable** (reports MAKER for
economically-taker fills; proven live: both trades' known-taker market entry AND B1
stop exit were charged the taker rate 0.00019 yet both recorded `role=maker`).
**Booking-honesty / attribution fix only — does NOT change real PnL** (PnL uses the
real summed `fee_usd`, not the role label).

## Design (as built, commit 129eb75 on bitunix-d3-role-2026-06-23)
- **EXIT** (`bitunix_position_reconciler.py:_aggregate_close_fills`): classify each
  close fill by order-id — `∈ tp_order_ids` → maker (resting POST_ONLY TP leg),
  `== sl_order_id` → taker (B1 stop / market reduce); **no order-id match** → fee-rate
  corroboration (closest to `D3_TAKER_FEE_REF 0.00019`/`D3_MAKER_FEE_REF 0.00014`),
  and if no fee → neither bucket (**`unknown`, never maker**). `_role_summary` default
  killed: no positive evidence → `unknown`.
- **Independent fee corroboration**: `fee_implied_role` from the aggregate fee rate +
  `role_fee_mismatch` bool — role and fee stay INDEPENDENT (a fee-model error stays
  detectable via the mismatch flag; role is NOT derived from fee).
- **ENTRY** (`bitunix.py:place_order`): role from `body["effect"]=="POST_ONLY"` →
  maker else taker (market entry / maker→taker fallback = taker). roleType abandoned.

## Sacred-path (CONFIRMED by diff — role attribution ONLY)
No change to PnL/net math (real summed fee + vwap), D1 `closed_qty=min(qty,q_close)`,
ref-vs-fill `_resolve_entry_price`, B1/bracket PLACEMENT (only READ existing bracket
order-ids), `risk.py`, `classify_result`/`classify_exit_kind`.

## Tests
`tests/test_bitunix_d3_role.py` (16, all pass): TP-id→maker, SL-id→taker, mixed,
no-match+taker-fee→taker, no-match+maker-fee→maker, **no-match+no-fee→unknown (never
maker)**, all-role-less→unknown; mismatch True/False/unknown; entry POST_ONLY→maker /
market→taker; **re-derive of the 2 post-epoch trades → both book taker, no mismatch**.
Two stale roleType-asserting tests updated to D3 semantics (not weakened). Full suite
= **28F+3E = clean baseline, ZERO new regressions** (25G cap).

## Files + drift-gate (targeted-hunk vs prod)
| file | prod (drift-gate) | staged (new target) |
|---|---|---|
| `trading_corp/brokers/bitunix.py` | `3f68473a` | `4b00dea2` |
| `trading_corp/agents/divisions/bitunix_position_reconciler.py` | `a3e9d50d` | `8c3adcd1` |

Both are **drifted/deployed trading-path** files (worktree == prod, so staged =
prod-blob + hunks). **Restart-dependent** (imported at startup) → **flat-window**.

## Apply (operator-run, flat window; agent SSH read-only)
```
scp -r "<...>\bitunix_reports\d3_role_deploy" azureuser@trading.jacksumner.com:~/
ssh azureuser@trading.jacksumner.com "bash ~/d3_role_deploy/apply_d3.sh"   # NO restart
ssh -t azureuser@trading.jacksumner.com "sudo systemctl restart trading-corp"
ssh azureuser@trading.jacksumner.com "bash ~/d3_role_deploy/VERIFY.sh"
```
`apply_d3.sh`: all-or-nothing — pre-flight compile staged → drift-gate BOTH
(`3f68473a`/`a3e9d50d`) → backup BOTH `*.bak-pre-d3-2026-06-23` → atomic-install BOTH
→ re-verify md5 + py_compile → roll back BOTH on any failure. **No restart in script.**
Rollback: restore both `*.bak-pre-d3-2026-06-23` + restart.

## Gate
Flat-window (trading-path, restart). Hold for Board review of this report before apply.

## Backfill — SEPARATE (operator-gated)
The 2 already-booked records (`8ed7e662`/`2bfca6ad`) keep their wrong `maker` label.
A re-derive backfill (stop→taker) is kept SEPARATE, like the D1 backfill — NOT part
of this fix.
