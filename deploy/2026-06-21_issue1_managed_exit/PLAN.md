# Issue #1 — managed-exit suppression: STAGED Board-gated deploy (2026-06-21)

**STAGE ONLY — agent did NOT deploy.** Operator applies + decides restart timing. Agent SSH = READ-ONLY.

## What ships
Suppresses the pre-bracket replay-loop managed virtual-exit (`_execute_live_exits`) for bracket-managed live rows,
ending the BitUnix 20008 'Insufficient amount' retry loop (it placed a reduce-only exit for the FULL entry qty
against a bracket-reduced venue position; 14× on 48b5adf9, 0% ever succeeded). The /tpsl/ bracket + reconciler
auto-book own the exit/booking. ONE file, dispatch gate only.

- Branch `bitunix-issue1-managed-exit-suppress-2026-06-21`, commit **5ac1a65**. File:
  `trading_corp/agents/paper_trade_replay.py`. Test `tests/test_paper_trade_replay.py` (NOT deployed; 30/30 pass
  under the 25 GB cap).
- Diff: dispatch gate only (the sole removed line is `if (`→`elif (`; +28 added = the bracket_managed branch).
  `_execute_live_exits`, B1, /tpsl/ bracket, reconciler, risk.py, entry path UNTOUCHED.

## md5
| file | BASE (prod now) | TARGET |
|---|---|---|
| paper_trade_replay.py | `406a4a8e5354bb3f46d4524958b40a09` | `5619910dab44b053124fbbc2e7671cec` |

Prod == origin/main for this file (never deployed) → BASE is the clean base. Validated: 30/30 tests, py_compile,
b64 roundtrips to TARGET.

## Apply (OPERATOR; script does NOT restart)
`issue1apply.sh` on Desktop. One paste:
```
Get-Content $HOME\Desktop\issue1apply.sh -Raw|ssh azureuser@trading.jacksumner.com "tr -d '\r'|bash"
```
Drift-gate prod==BASE → backup `*.bak-pre-issue1-2026-06-21` → decode + py_compile + md5-verify==TARGET → atomic
`mv` → re-verify. NO restart, NO config, NO DB.

## RESTART DEPENDENCY (explicit)
`paper_trade_replay.py` is imported Python → the suppression takes effect **on the next process restart**. A restart
**re-baselines the D4 watch** — but D4's pass-through + block are already validated (only post-manual-flatten remains,
which is structurally guaranteed), so a restart now is lower-stakes than before. **Operator decides timing:**
- (a) restart standalone now → 20006 loop + exit-Telegram-flood stop immediately;
- (b) batch with the next restart → loop persists until then (non-fatal: the server-side bracket still exits the
  position correctly; it's noise + 20008 rejections, no money impact).

## Rollback
`cd /home/azureuser/trading_corp && mv trading_corp/agents/paper_trade_replay.py.bak-pre-issue1-2026-06-21 trading_corp/agents/paper_trade_replay.py` + restart.
