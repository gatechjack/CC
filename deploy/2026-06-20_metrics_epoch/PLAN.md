# Metrics-epoch — STAGED Board-gated deploy (2026-06-20)

**STAGE ONLY — agent did NOT deploy.** Operator runs apply + (optionally) restart. Agent SSH = READ-ONLY.

## What ships (display-layer only)
Splits `paper_trade_summary` by `execution_mode` so the dashboard stops blending paper-sim + live into one
win-rate (the live panel was paper+live mashed — the 30/64/66 numbers). `windows`/`totals` = PAPER slice
(unbounded, backward-compatible — every paper-only division renders identically). New `live_windows`/`live_totals`
= LIVE slice, scoped forward from a per-division metrics epoch (`agent_state(<div>,'metrics_epoch')` on `result_ts`;
absent = all-time). New "Live-trade win rate" panel + a "since <date> · current logic only" / "epoch not set
(includes pre-fix bookings)" label. Epoch helper `_get_metrics_epoch` is ISO-validated + param-bound (no SQL
interpolation). **No trading-path code, no migration** (`agent_state` table exists).

- Branch `bitunix-metrics-epoch-2026-06-20`, commit **4807ce4**. Files: `trading_corp/web/data.py` +
  `trading_corp/web/templates/division.html`. Test `tests/test_paper_trade_replay.py` (NOT deployed; 30/30 pass).

## md5
| file | BASE (prod now) | TARGET |
|---|---|---|
| web/data.py | `3874f469d70d864a542afab34e823041` | `dae49424521a0586adecb32ccf1da614` |
| web/templates/division.html | `ca894995be2cc8481a12e5a2e61b1d85` | `b6e23456a1cfcec484f41c5b3ce6e61e` |

Pre-edit files byte-confirmed identical to prod → TARGET is a clean delta. Validated: 30/30 tests, py_compile,
jinja parse; embedded base64 round-trips to TARGET.

## Apply (OPERATOR; script does NOT restart)
`meapply.sh` on Desktop. One paste:
```
Get-Content $HOME\Desktop\meapply.sh -Raw|ssh azureuser@trading.jacksumner.com "tr -d '\r'|bash"
```
Drift-gate prod==BASE → backup `*.bak-pre-metrics-epoch-2026-06-20` → decode + py_compile + jinja-parse +
md5-verify==TARGET → atomic `mv`. NO restart, NO config, NO DB write.

## Activation — RESTART (operator's call; RECOMMEND BATCH WITH D1)
data.py is imported Python → the split takes effect only **after a process restart**. Two clean options:
- **(Recommended) Batch the restart with the D1 deploy.** D1 is the gate for *setting* the epoch anyway, and a
  restart now would change the PID + reset the D4 armed-window observation clock mid-validation. Applying the files
  now (no restart) is harmless; they activate at the D1-cutover restart, when you also set the epoch row.
- **(If wanted sooner)** restart standalone now → the paper/live SPLIT goes live immediately (correctness win); the
  live panel shows "epoch not set (includes pre-fix bookings)" until D1. Note: this resets the D4 armed-window clock.

## Epoch activation (separate, at D1 cutover — NOT part of this deploy)
One SQL row, no redeploy:
`INSERT INTO agent_state(agent,key,value_json,updated_ts) VALUES('bitunix_futures','metrics_epoch','"<POST-D1-ISO>"',<now>)`
(value is JSON — note the quotes). Revert to all-time = `DELETE … WHERE agent='bitunix_futures' AND key='metrics_epoch'`.

## Rollback
```
cd /home/azureuser/trading_corp
for f in trading_corp/web/data.py trading_corp/web/templates/division.html; do mv "$f.bak-pre-metrics-epoch-2026-06-20" "$f"; done
```
then restart. (Display-only; no trade-path impact either way.)
