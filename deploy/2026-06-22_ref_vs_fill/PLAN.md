# ref-vs-fill — alert-price-vs-fill-price PnL fix (deploy plan)

**Branch:** `bitunix-d1-netted-close-2026-06-21` @ `d234046` (builds on D1).
**Status:** STAGED — Board-gated apply. **DO NOT DEPLOY** (held for a flat window).
**Restart:** NOT in the apply script. Operator restarts to load it.

## The defect
Close-side PnL read `entry_reference_price` (the alert/signal price) instead of
the **actual entry fill**. Systematic on **every live trade** (incl. single/clean
ones — unlike D1 which only hit stacked closes). 125b6f9e booked at ref 63465.3
vs the real fill 63413.6 = 52pt error. Must be fixed before the metrics epoch is
trusted.

## The fix (two orthogonal pieces; OFF D1's function)
- **CAPTURE** — `bitunix_futures_observer.py` `_place_live`: stamp
  `extra['actual_entry_fill_price'] = fill.price` at the live-entry registration,
  beside the existing `entry_fee_usd`/`entry_role` stamps. `fill.price` is the
  broker-observed VWAP (`place_order → _observe_fill → _fill_price_from_history`,
  the signed trade-history average — confirmed from code). Stamped only when >0.
- **CONSUME** — `bitunix_position_reconciler.py`: `_resolve_entry_price(extra,
  ref)` prefers the stored actual fill, else falls back to
  `entry_reference_price`. Used in BOTH autobook paths.

**Fallback (backward-compat):** records predating this fix OR paper rows (which
book at the signal price by design) have no `actual_entry_fill_price` → fall back
to `entry_reference_price`. Never crashes, never mis-books a historical.

**Composition with D1 (orthogonal):** D1 fixed the QTY term (`min(qty,q_close)`);
this fixes the ENTRY-PRICE term. Combined:
`pnl = (actual_entry_fill − vwap) · min(qty, q_close)`.

**Storage:** `extra_json` (like `entry_fee_usd`/`entry_role`) — NO new column, NO
`models.py`/`db.py` migration; old rows simply lack the key (fallback).

**Not touched (deliberately):** SL-trail breakeven target (reconciler bracket
path, B1-adjacent), `paper_trade_replay` (paper books at ref by design;
display/notification), `data.py` fee-floor display, `brokers/bitunix.py`,
`bitunix_bracket.py`, `risk.py`, `models.py`, `db.py`.

## Files + md5 gate (two files)
| file | base (prod-current) | target |
|---|---|---|
| observer   | `e88a7abca643f2048facfcb19a6c559b` | `2647fccc630c8acacbe0d5a32f05b1c8` |
| reconciler | `5c4c8dba04a267c660c5fe826dabb16c` (=D1) | `a3e9d50da2527664a2016e7205cac9f8` |

**Observer = TARGETED-HUNK.** Prod's observer (`e88a7abc`) carries the **D4
concurrent-position guard** (deployed 2026-06-20, after this branch's base). A
full-file replace would revert D4. So the target = prod's blob + ONLY the 8-line
capture hunk (verified: `diff prod target` = exactly the 8 lines; D4 preserved).
Reconciler = full-file (its base `5c4c8dba` matches prod = D1).

The apply gates BOTH up front (abort if either drifts), backs up both
(`*.bak-pre-refvfill-2026-06-22`), atomic-mv both, re-verifies both md5s, and
**rolls back BOTH** on any mismatch. No restart.

## Apply (operator-run; agent SSH read-only)
```
scp apply_refvfill.sh VERIFY.sh azureuser@trading.jacksumner.com:~/
ssh azureuser@trading.jacksumner.com "bash ~/apply_refvfill.sh"   # no sudo
ssh -t azureuser@trading.jacksumner.com "sudo systemctl restart trading-corp"
ssh azureuser@trading.jacksumner.com "bash ~/VERIFY.sh"
```

## Rollback
```
cp <observer>.bak-pre-refvfill-2026-06-22 <observer>
cp <reconciler>.bak-pre-refvfill-2026-06-22 <reconciler>   # then restart
```

## Verification expectations
- Pre-restart `VERIFY.sh`: both md5 MATCH, compile OK, capture + 2 consume sites
  present, **D4 guard still present** in observer.
- Post-restart: next LIVE entry record carries `extra.actual_entry_fill_price`
  (= real fill, ≠ `entry_reference_price`); next live close books PnL from it.

## Rebuild provenance (targeted-hunk)
`_rvf_splice.py` (fetch prod observer read-only → splice the 8-line hunk →
verify == prod+hunk) then `_gen_apply.py` (embed both targets). Re-running needs
the live prod observer blob (inherent to a targeted-hunk).

## Tests
`tests/test_bitunix_ref_vs_fill.py` (5) + extended `test_fill_event_fee_plumbing.py`
(proves the observer capture end-to-end). Full suite == clean baseline
(28F + 3E), zero new regressions.

## NOT in scope
Do NOT set the metrics epoch (operator's `agent_state` INSERT AFTER ref-vs-fill
is live+verified). D1 backfill is separate. Do NOT touch D1.
