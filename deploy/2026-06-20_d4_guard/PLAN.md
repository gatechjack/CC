# D4 — concurrent-position guard: STAGED Board-gated deploy (2026-06-20)

**STAGE ONLY — agent did NOT deploy.** Operator runs the apply + restart. Agent SSH this session = READ-ONLY.

## What ships
Blocks a NEW bitunix entry **iff** (VENUE shows an open same-symbol SAME-SIDE position) **AND** (the bot holds a
tracked open same-side live row). Venue-authoritative; engine corroborates only; fail-CLOSED on an unknown/incomplete
venue read. Same-side only → a close-and-reverse and any reduce-only/flatten are never gated. Manual / not-bot-opened
stays the reconciler orphan-halt's job (zero overlap). Gates AFTER the risk gate + flatten dispatch, BEFORE
`_place_live` → B1 / `/tpsl/` bracket / `risk.py` / `bitunix.py` untouched. **Ships OFF** (`enabled: false`) →
nothing arms at deploy; flip ON only after one clean validation trade.

- Branch `bitunix-d4-concurrent-position-guard-2026-06-20`, feature commit **82314f1**.
- Prod files (3): `bitunix_futures_observer.py` + `main.py` (full-file, md5-gated atomic install) +
  `config/strategies.yaml` (**surgical** OFF-flag insert — NOT a full-file write; preserves the operator-drifted
  execution_mode/DD-cap/kalshi/yellow_x).
- Tests (NOT deployed): `tests/test_bitunix_concurrent_position_guard.py` (9 cases, all pass). Existing observer
  suite 53/53 — additive, zero regression.

## md5
| file | BASE (prod now) | TARGET (after) |
|---|---|---|
| observer | `a31a10f1445f0263389c377c41f742f8` | `e88a7abca643f2048facfcb19a6c559b` |
| main.py  | `f16e9c24f81e65c9eb9d98019eea4e23` | `97a4d67661361414e369d9e4355e7d3e` |
| strategies.yaml | `3cc3689aaebba2b8533eab95d258cff4` | surgical insert — verified by `yaml.safe_load` → `cpg.enabled == False` + diff==block-only |

## Apply (OPERATOR runs; the script does NOT restart)
Self-contained (base64-embedded files, no scp). `d4apply.sh` is on the operator's Desktop. One paste:
```
Get-Content $HOME\Desktop\d4apply.sh -Raw|ssh azureuser@trading.jacksumner.com "tr -d '\r'|bash"
```
The script: drift-gate prod==BASE (ABORT on any drift) → backup `*.bak-pre-d4-2026-06-20` → decode + `py_compile`
+ md5-verify staged `.py` == TARGET → atomic `mv` → surgical YAML insert (fail-closed: unique anchor, `diff==block`
only, YAML re-parse) → re-verify md5 + `py_compile` + `cpg.enabled`. Any ABORT leaves prod untouched (only a
`.d4new` temp at worst). **THEN** the operator restarts via `az run-command` (new code + dormant flag load at boot).

## Rollback
```
cd /home/azureuser/trading_corp
for f in trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/main.py config/strategies.yaml; do mv "$f.bak-pre-d4-2026-06-20" "$f"; done
```
then restart. The flag is OFF, so even un-rolled-back the guard is dormant — rollback is only needed if the new code
itself misbehaves at boot.
