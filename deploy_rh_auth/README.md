# deploy_rh_auth — RH-auth resilience staging (2026-07-18)

Staging package for the 3 RH-auth resilience items. **Nothing here is deployed yet.**
Prod baseline: engine restarted 21:33:03 UTC (PID 253942) on a valid 20:55 pickle; RH
auth healthy, 0 401s. Scope doc: `Desktop\bitunix_reports\2026-07-18_rh_auth_resilience_scope.md`.

## THREE FINDINGS from reading robin_stocks + the live recovery (design-shaping)
- **A. Headless login is challenge-type-dependent.** `rs.login(user,pw,mfa_code,store_session=True)`
  skips the input()/getpass prompts when all 3 are supplied. The device-approval handler
  POLLS for a `"prompt"` (app-approval) challenge (headless-safe — what 7/18 used) but calls
  `input()` for an `"sms"`/`"email"` challenge (hangs with no TTY). MITIGATION: run with
  `StandardInput=null` so an unexpected sms/email challenge raises EOFError (clean fail →
  alert) instead of hanging.
- **B. ★ Refreshing the pickle FILE does NOT recover a RUNNING engine.** PROVEN live: pickle
  refreshed 20:55 → engine STILL 401ing at 21:31 → only recovered after a RESTART at 21:33.
  The engine's robin_stocks session is an in-memory singleton loaded once at boot
  (`_LOGIN_DONE` latch). So a standalone timer that only rewrites the file cannot fix a live
  401 — it keeps the file fresh for the NEXT boot/reload. True self-heal needs an IN-PROCESS
  re-login (reset the latch + rs.login off the fresh file). => ITEM 3 should AUTO in-process
  re-login on a detected 401 (recovers WITHOUT a push if the file is fresh); ITEM 2 button
  does the same on demand. THIS IS A DESIGN UPGRADE TO ITEM 3 — awaiting operator nod before
  the broker/data_exec .py diffs are authored.
- **C. creds file** `/etc/trading-corp/tastytrade.env` is root-owned / not agent-readable →
  operator supplies RH cred values into KV (this package's .ps1 does that securely).

## Account fact: NO MFA on this RH account
KV has ROBINHOOD-USERNAME + ROBINHOOD-PASSWORD only (both Enabled). No ROBINHOOD-MFA-SECRET.
`robinhood_mfa_secret` resolves to None everywhere — already guarded (`if self._mfa_secret:`)
so `mfa_code=None` is passed and RH uses its device-approval APP PROMPT (polled,
headless-safe; what the 7/18 manual refresh used). NO code change needed for no-MFA.

## Files (status)
| File | Item | Deploy gate | Status |
|---|---|---|---|
| `set_rh_kv_secrets.ps1` | 1 (KV creds) | none | SKIP — KV already populated (user+pass; no MFA) |
| `secrets_py.patch` | 1 (KV list) | RESTART (batch) | STAGED diff — review |
| `relogin/rh_daily_relogin.py` | 1 (timer) | none (separate unit) | STAGED — review |
| `relogin/rh-relogin.service` | 1 (timer) | none (root install) | STAGED — review |
| `relogin/rh-relogin.timer` | 1 (timer) | none (root install) | STAGED — review |
| `templates/rh_session_panel.html` | 2 (button+health) | HOT (Jinja) | STAGED scaffold — review |
| `robinhood_py.patch` | 3 (sentinel+reload+latch+guards) | RESTART (batch) | STAGED diff — review |
| `data_exec_py.patch` | 3 (alert hook) | RESTART (batch) | STAGED diff — review |
| `routes_py.patch` | 2 (button+health routes) | RESTART (batch) | STAGED diff — review |
| `mainwiring_and_signature_notes.md` | 3,2 (wire + signature) | RESTART (batch) | STAGED — review |

## ★ FOOTGUN — cred-strip ORDERING (KV-as-single-source)
Do NOT strip ROBINHOOD_* from the root unit-env (tastytrade.env) BEFORE the secrets.py patch
is LIVE + verified. Order: (1) secrets.py patch lands at the batch restart -> engine pulls
ROBINHOOD-* from KV; (2) VERIFY boot loads them + RH auth works; (3) THEN strip from unit-env;
(4) optional restart to confirm KV-only. Stripping first + any restart = no creds = outage.
Trade-off to decide: strict KV single-source (clean) vs keep unit-env as a break-glass
FALLBACK (survives a KV/managed-identity hiccup at boot — load_secrets degrades gracefully to
no-creds otherwise). Operator's call.
