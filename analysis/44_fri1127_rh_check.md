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

---

## UPDATE -- CLOSED CLEAN + PHASE C1 EXECUTED (Fri 2026-11-27, after Jack re-authed the pickle)

Jack re-authed the RH pickle at **15:26:58Z** (push-button). Re-verified RH under cryptography 50.0.2 / msal 1.39.0 across **15:27->15:49Z** (read-only), then ran **PHASE C1**.

### Re-verify -- CLEAN (meets Jack's full bar)
| criterion | evidence | verdict |
|---|---|---|
| MACE reads RH **equity** | `rh_auth_recovered reason=snapshot_ok` 15:27:09Z -> `load_portfolio_profile` succeeded, all 4 RH accounts restored (`680725082`,`461391328`,`934310442`,`116637293063`) | PASS |
| + **open-position** reads | PMCC detected **6 legs** 15:43:17Z (IREN/TSLA/RIOT/BLSH/SMR/RKLB); MACE manager **live-marked** rung `mace-IWM-2026-12-18-250-247-289-292-20261105` @ **0.445** 15:45:56Z (reads the RH option legs) | PASS |
| rungs<->RH **reconcile clean** (0 orphan/ghost/naked) | `mace_reconcile_open_orders_error` fired every 5-min tick 15:01->15:26 (session down), then **silent from 15:31** (reconcile logs only on error -> silence = clean); `mace_reconcile_ERRORS_after_reauth=0`; **0** real orphan/ghost/naked (the 2 grep hits = Bitunix startup `matched=0 orphan=0`) | PASS |
| **0** robin_stocks auth/session errors 15 min after read | **0** across 15:27:10->15:49:29Z (~22 min, exceeds the 15-min bar); session **valid**, pickle 0h, last-good live-updating | PASS |
| crypto-50 health | **0** cryptography exceptions since boot; crypto 50.0.2 / msal 1.39.0; session green | PASS |

**2c-2 fully vindicated** -- the Phase-A DOWN state was the pre-existing weekly pickle expiry (predated the 2c-2 deploy by 9 days), and RH is fully healthy under cryptography 50 once the token is fresh.

### PHASE C1 -- executed
Pre-list (RO) confirmed 4 snapshots: `-20261124` (11-24), `-20261124b` (11-25), `-20261124c` (11-25), `-20261126` (11-26 21:25Z, pre-Batch-7 keep).

```
az snapshot delete -g rg-shared-prod -n tc-prod-os-20261124     -> [OK]
az snapshot delete -g rg-shared-prod -n tc-prod-os-20261124b    -> [OK]
az snapshot delete -g rg-shared-prod -n tc-prod-os-20261124c    -> [OK]
az snapshot list   -g rg-shared-prod --query "[].name" -o tsv   -> tc-prod-os-20261126   (ONLY)
```

`tc-prod-os-20261126` retained as the sole rollback point; **never referenced in the delete loop**. Box unchanged, prod-live unchanged (`b1ade21c`), 0 drift. C1 is the explicitly pre-authorized clean-path outcome; executed per the standing autonomous authorization once CLEAN was conclusively met.

### Left for Jack
Nothing blocking from this run. RH healthy under crypto 50. Standing unrelated item (not a patch issue): the RH OAuth pickle still expires ~weekly and needs the manual push-button re-auth. Vault updated (handoff + Security Patch Review). Evidence commit local; Jack pushes.
