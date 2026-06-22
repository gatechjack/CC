# phantom-legs fix — skip the paper bar-walk for bracket-managed live rows

**Branch:** `bitunix-d1-netted-close-2026-06-21` @ `24519c8`.
**Status:** STAGED — Board-gated apply. **Restart required to load.**

## Defect (live, twice on 2026-06-22)
`paper_trade_replay` ran `_classify_v2_multi_leg` (the paper bar-walk) on
bracket-managed **live** rows, simulating TP fills and persisting **phantom
`filled_legs`** (`would_call_broker:false` `position_sl_update` telemetry too).
That phantom `[tp1,tp2]` made the reconciler auto-book **defer**
(`partial_tp_ambiguous`) → the position closed on the venue but never booked →
**hours-long stuck + halted engine** (89966d01 ~4h; 2a53de19). Recurs on every
bracketed trade. (The SL protection itself worked — structural stop fired; this
is a telemetry/booking defect, not a protection failure. The entry slippage is
the separate money lever.)

## Fix — skip entirely
At the top of the replay row loop, before classification:
```
if extra.get("execution_mode") == "live" and (
        extra.get("bracket_tp_order_ids") or extra.get("bracket_position_sl_order_id")):
    counts["skipped_bracket_managed_live"] += 1
    continue
```
One `continue` removes the phantom `filled_legs` persist (both the `still_open`
and Issue#1 `bracket_managed` paths) **and** the false SL telemetry (the bar-walk
never runs). The reconciler owns these rows' lifecycle from venue truth —
**verified** it detects fills via `get_pending_positions` qty and persists its own
`current_sl` (fallback `stop_price`), never the replay's value (reconciler:1192-1252,
the path validated on 48b5adf9).

**Paper rows + non-bracket live rows:** unchanged. **Not touched:** B1/bracket/SL
execution, observer, risk.py, reconciler, D1/ref-vs-fill. Does NOT fix entry
slippage (separate).

## md5 gate (one file, TARGETED-HUNK)
| | md5 |
|---|---|
| prod-current / base | `5619910dab44b053124fbbc2e7671cec` (= Issue#1) |
| post-fix target | `28817062529b23e1d1bf7b5647901469` |

Prod's `paper_trade_replay.py` carries Issue#1 (deployed 06-21, after this
branch's base), so the target = prod blob + the 14-line skip hunk (verified
`diff prod target` = exactly the hunk; `suppressed_bracket_managed` Issue#1
marker preserved). Apply drift-gates `5619910d`→target, backs up
`*.bak-pre-phantomlegs-2026-06-22`, atomic-mv, re-verifies, self-rollback,
py_compile, **no restart**.

## Apply (operator-run; agent SSH read-only)
```
scp apply_phantomlegs.sh VERIFY.sh azureuser@trading.jacksumner.com:~/
ssh azureuser@trading.jacksumner.com "bash ~/apply_phantomlegs.sh"   # no sudo
ssh -t azureuser@trading.jacksumner.com "sudo systemctl restart trading-corp"
ssh azureuser@trading.jacksumner.com "bash ~/VERIFY.sh"
```
Rollback: `cp <f>.bak-pre-phantomlegs-2026-06-22 <f>` then restart.

## Verification
- Pre-restart `VERIFY.sh`: md5 MATCH, compile OK, skip guard present, **Issue#1
  still present**.
- Post-restart: next bracketed live trade → replay counts
  `skipped_bracket_managed_live`, no phantom `filled_legs`, its close auto-books
  cleanly (no `partial_tp_ambiguous` stall / halt).

## Tests
`tests/test_bitunix_phantom_legs_skip.py` (4: skipped + no-phantom-legs +
no-SL-telemetry, trade-2/3 geometry, paper still walked, live-no-bracket not
skipped). Full suite == clean baseline (28F + 3E).
