# Fri 2026-11-27 autonomous RH post-2c-2 check -- outcome

Autonomous run (Jack 04:18 ET ruling). Phase A executed 09:07-09:13 ET (read-only). trading-corp
pid 33075, NRestarts=0, boot 2026-11-26 21:34Z (Batch 7 restart), venv **cryptography=50.0.2 msal=1.39.0** (2c-2 live).

## Phase A findings
- **A1** `system/startup`: `mode: LIVE`, `live_brokers: ["bitunix","robinhood","kalshi"]`, `live_authorization: env_authorized` -- RH configured live.
- **A2** `/api/rh/session-health` (localhost:8000, HTTP 200): **auth_state DOWN**, panel "pickle 252h old".
- **A3** robin_stocks 401/session errors since boot: **373** (confirmed). Samples: `401 Client Error: Unauthorized for url: https://api.robinhood.com/quotes/historicals/...`, `.../options/orders/`, `.../portfolios/<N>`; `mace_reconcile_open_orders_error {"error": "'NoneType' object is not subscriptable"}` / `"get_all_open_option_orders can only be called when logged in"`; **`rh_auth_failed: Robinhood session DOWN (reason=reauth_failed_or_stale_file) — NO entries and NO exits on live positions, all RH accounts`**.

## Cause discrimination (read-only) -- DEFINITIVELY pre-existing pickle expiry, NOT 2c-2
| discriminator | result | implies |
|---|---|---|
| **pickle mtime** | `2026-11-17T01:40:38Z` = **252h / 10.5 days old** | token expired ~Nov-24, **BEFORE 2c-2 (Nov-26)** -> 2c-2 cannot be the cause |
| reauth failure reason | `RH in-process reauth did not complete (TimeoutError) — file likely stale` | the known human-push-needed issue (auto-reauth 15s timeout), NOT crypto |
| cryptography/ssl/nss/Import errors in RH path | **0** | robin_stocks works under crypto 50 |
| box-wide cryptography exceptions since boot | **0** | crypto 50 healthy |
| 401 shape | clean `401 Unauthorized` from RH API | request properly signed+sent (crypto OK); RH rejected a stale token |
| 4 tracebacks since boot | all 01:12Z boot-time, non-RH/non-crypto (pre-existing benign set) | unrelated |

**Verdict: confirmed RH auth failure, but DEFINITIVELY NOT a 2c-2 regression** -- it is the known weekly RH OAuth-pickle expiry (pickle 10.5 days stale, predates the 2c-2 deploy by 9 days; reauth needs Jack's ~90s push-button re-auth, which no cryptography version affects). **2c-2 (cryptography 50.0.2 / msal 1.39.0) is VINDICATED on the RH path**: robin_stocks makes valid HTTPS calls; the only problem is the stale token.

## Decision (autonomous)
- **NOT C1** (not CLEAN) -> **all 4 snapshots KEPT** (`tc-prod-os-20261124`,`-20261124b`,`-20261124c`,`-20261126`).
- **NOT the C2 rollback** -> the failure is proven not-2c-2 (pickle predates the deploy), so reverting cryptography 50->47 would be futile (can't refresh a stale token) AND harmful (reintroduces CVE-2026-69248 + lock-drift + a market-hours 4-service restart). This reaches C2's own "cause isn't 2c-2 -> STOP, no snapshot restore" end-state via conclusive pre-rollback evidence instead of a harmful empirical test.
- **No mutating action taken.** Box unchanged; prod-live lock still matches (crypto 50.0.2 / msal 1.39.0); no drift.

## Left for Jack
1. **RH pickle re-auth (pre-existing, URGENT, predates the whole 2c batch):** the RH session has been down since ~Nov-24 (pickle 10.5 days old); MACE options are frozen (no RH entries/exits). Fix = the ~90s push-button re-auth (`POST /api/rh/refresh-session` / the dashboard "Refresh RH pickle" button) -- needs a human push approval; no cryptography/deploy change helps.
2. **Snapshot cleanup deferred** (since the RH reconcile wasn't CLEAN, C1 did not run) -- the 3 old snapshots remain; delete them once RH is re-authed + a genuinely clean reconcile is observed.
3. Nothing to push from this run (read-only).
