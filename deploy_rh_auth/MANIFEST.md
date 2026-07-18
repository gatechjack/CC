# RH-auth batch — FINAL MANIFEST (for review before scheduling the restart)

Prod baseline: engine PID 253942, up 2026-07-18 21:33:03 UTC, RH auth healthy (0 401s),
"Key Vault: loaded 33 secrets". All 5 target files azureuser-writable; web on :8000.

## What lands (drift-gated, additive, idempotent — via `apply_rh_auth_batch.py`)
| File | base md5 (2026-07-18) | Change (marker) | Item |
|---|---|---|---|
| `utils/secrets.py` | 851415cc…a4c2 | +3 ROBINHOOD_* in KV list (`ITEM1-RH-KV`); KV-authoritative-with-unit-env-fallback (`ITEM1-KV-AUTHORITATIVE`) | 1 |
| `brokers/robinhood.py` | 72f7944c…d53c | +imports; +module auth-state & `RobinhoodAuthError` (`ITEM3-RH-AUTH`); +sentinel/reauth/latch methods (`ITEM3-REAUTH`); +snapshot integration (`ITEM3-SNAPSHOT`) | 3 |
| `agents/data_exec.py` | 51281fbd…a3e7 | +`_on_rh_auth_change` alert hook (`ITEM3-AUTH-HOOK`) | 3 |
| `web/routes.py` | 1b7611e0…61da | +health & refresh routes (`ITEM2-RH-ROUTES`) | 2 |
| `main.py` | 3faca1f3…7323 | +2-line hook wiring (`ITEM3-WIRE`) | 3 |
| `web/templates/rh_session_panel.html` | (new file) | health strip + refresh button (HOT) | 2 |

## Gates (in order)
1. **DRY-RUN** — `deploy_rh_auth_batch.ps1` (no `-Apply`): drift-gates every anchor (found exactly 1x, marker absent) and prints a unified diff of each change. Review.
2. **APPLY** — `deploy_rh_auth_batch.ps1 -Apply`: `.bak_rhauth` backup per file → apply → `py_compile` each → **import pre-flight** (`import trading_corp.web.routes, .brokers.robinhood, .agents.data_exec, .utils.secrets` → "IMPORT OK"). No restart.
3. **PRE-RESTART** (recommended, per the memory lesson "ALWAYS TestClient-200-check a new engine route"): run the project boot-smoke test that checks route registration, if available, before the restart.
4. **RESTART** (operator-scheduled): Bitunix flat + pickle pre-flighted (fresh from 21:33; re-check age at window).
5. **BOOT-SMOKE** — `bootsmoke_rh_auth.sh`: [1] loaded **35** secrets (33+2 = RH from KV ✓), [2] RH user+4 binds, [3] 0 × 401, [4] 0 batch tracebacks, [5] `/api/rh/session-health` → 200, [6] `_auth_alert_hook` wired, [7] Bitunix recent.

## Rollback (STAGED before apply, per operator)
`rollback_rh_auth_batch.ps1` — restores every `.bak_rhauth`, removes the template, `py_compile`s. Then restart to activate. (Ready now; no `.bak_rhauth` collisions on prod.)

## Runners (operator paste, one line each)
- Dry-run:  `powershell -ep bypass -f .\deploy_rh_auth\deploy_rh_auth_batch.ps1`
- Apply:    `powershell -ep bypass -f .\deploy_rh_auth\deploy_rh_auth_batch.ps1 -Apply`
- Rollback: `powershell -ep bypass -f .\deploy_rh_auth\rollback_rh_auth_batch.ps1`
- Boot-smoke: `ssh azureuser@trading.jacksumner.com 'bash -s' < .\deploy_rh_auth\bootsmoke_rh_auth.sh`

## Open integration detail (small, HOT — confirm before/at apply)
The panel renders standalone at `/api/rh/session-health`, but to show the button on the
dashboard it must be INCLUDED in a parent template (a one-line `{% include
"rh_session_panel.html" %}` + a `hx-get` bootstrap). Recommended spot: the command-center
homepage header (most visible / phone-reachable). Needs the homepage template name +
insertion point — confirm and I'll add the include (HOT, no restart).

## NOT in this runner (co-land in the SAME restart window — separate artifacts)
scan_evaluation funnel · paper-vs-live rendering · homepage crypto card · tile-repoint
verify. These are separate builds; the boot-smoke covers the whole engine regardless.

## Decisions locked in
- KV AUTHORITATIVE + unit-env FALLBACK (not strict single-source); do NOT strip
  tastytrade.env before secrets.py is live+verified (strip then optional).
- Timer GENTLE-ON-EXPIRY (no forced-daily; no-MFA account → avoid daily new-device logins).
- Timer install (standalone unit) is INDEPENDENT of this batch (no engine restart); needs
  secrets.py on disk (this batch) + KEY_VAULT_URI to pull RH creds from KV.
