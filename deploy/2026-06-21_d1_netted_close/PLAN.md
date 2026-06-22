# D1 — netted-close PnL double-booking fix (deploy plan)

**Branch:** `bitunix-d1-netted-close-2026-06-21` (base `d074728`, D1 commit `1b669ed`)
**Status:** STAGED — Board-gated apply. **DO NOT DEPLOY** without operator go.
**Restart:** NOT in the apply script. Operator restarts the engine to load D1.

## What changes
One file: `trading_corp/agents/divisions/bitunix_position_reconciler.py`,
function `_autobook_missing_close_real` (the signed-fetch real-fill auto-book).

The real-fill auto-book booked PnL + exit fee on the **full netted close qty**
for every record. When several stacked records share one server-side netted
close, that books the close **N times over** (the ~6× field over-book).

Fix — attribute only THIS record's share, capped at the close qty:

```
q_close    = agg["total_qty"]                 # total qty actually closed
closed_qty = min(record_qty, q_close)
pnl_i      = (entry_i - vwap) * closed_qty     # sign by side
exit_fee_i = total_fee * (closed_qty / q_close)
```

- **Byte-unchanged** single-record case (incl. a normal fill gap, e.g.
  e9c35907's recorded 0.00095 vs closed 0.0009): `min()==q_close` → identical
  economics to the prior full-qty booking (+0.2389, not the raw-qty +0.2522).
- **Stacked / netted:** per-record qty-weighted; PnL + fee sum to the netted
  close ONCE, not N times.
- **Flag threshold `D1_QTY_ANOMALY_RATIO = 1.5`:** a record qty that grossly
  exceeds the netted close (> 1.5×) is a real data error (stale/duplicate
  record), not a normal ~5% fill gap → `log.warning` (never defer/crash);
  `min()` still caps the booked economics safely.
- Audit payload now records the attributed (capped) `qty` + `netted_close_qty`.

**Not touched:** D2; ref-vs-fill (separate later pass); `brokers/bitunix.py`,
`bitunix_bracket.py` (B1/bracket), `bitunix_observer`, `risk.py`,
`paper_trade_replay.py`. Idempotency unchanged (`WHERE result IS NULL`).

## md5 gate
| | md5 (LF-normalized) |
|---|---|
| prod-current / expected base | `bd06ea281a853687fad8d0a6831e9c0a` |
| post-D1 target               | `5c4c8dba04a267c660c5fe826dabb16c` |

The apply script aborts if prod ≠ the expected base (drift gate).

## Apply (operator-run; agent SSH is read-only)
```
TC_ROOT=/home/azureuser/trading_corp   # override if the repo root differs
bash apply_d1.sh
```
The script: drift-gate → backup `*.bak-pre-d1-2026-06-21` → atomic mv →
re-verify md5 (self-rollback on mismatch) → `py_compile`. **No restart.**

Then operator restarts the engine, then:
```
bash VERIFY.sh
```

## Rollback
```
cp <target>.bak-pre-d1-2026-06-21 <target>   # then restart
```

## Verification expectations
- Pre-restart: `VERIFY.sh` md5 MATCH + `py_compile` OK + D1 markers present.
- Post-restart, on the **next real server-side netted close**: the
  `auto_book_server_side_close` audit payload carries `netted_close_qty`, and
  `qty` == the attributed (capped) share. For a normal single-record close the
  booked PnL is unchanged vs the prior behavior (regression-safe).

## Separate, NOT in this package
- **Backfill** of the already-double-booked records (`125b6f9e`, `81f5427a`):
  `../2026-06-21_d1_backfill/d1_backfill_double_booked.py` — operator-gated
  DRY-RUN by default, distinct from the P2 label-only record-correction.

## Tests
`tests/test_bitunix_d1_autobook.py` — 5 tests / 4 cases (byte-unchanged guard,
stacked sum-to-close, idempotency, flag threshold). Full suite == clean baseline
(28F + 3E), zero new regressions.
