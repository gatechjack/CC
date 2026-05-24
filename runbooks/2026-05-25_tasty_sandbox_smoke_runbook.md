# Tasty Options — Phase-0 Sandbox Smoke Runbook

**Created:** 2026-05-24 (after Commit 5 of the Tasty Options division
build). **First-run target:** 2026-05-25 (Phase 0 of the plan at
`.claude/plans/i-want-to-create-enumerated-papert.md`).

## What this is

`scripts/tasty_sandbox_smoke.py` exercises the new `TastytradeBroker`
end-to-end against Tastytrade's certification / sandbox endpoint
(`Session(is_test=True)` → CERT_URL). It is the gate between Commit 5
landing and Phase 1 starting:

1. **connect** with the prod OAuth credentials in `is_test=True` mode
   (TT does not require separate sandbox credentials — the same
   `TASTYTRADE_PROVIDER_SECRET` / `TASTYTRADE_REFRESH_TOKEN` cover both)
2. **snapshot** the sandbox account (balances + positions)
3. **place_multi_leg** — submits a wide far-OTM 4-leg IC at $0.10 net
   credit; the wide strikes + tiny limit are deliberately designed NOT
   to fill so the script verifies the submission SHAPE without putting
   sandbox capital at risk. A non-Filled terminal status (Live /
   Cancelled / Expired / Rejected) is the success signal here.
4. **cancel_order** round-trip against a non-existent order id
   (signature check; `False` return is expected)
5. **get_option_greeks** via the injected `TastytradeDataProvider`
   (delegation path check)

## When to run

Phase 0 of the plan — before Phase 1 paper-internal observation begins.
Re-run any time the `tastytrade` SDK is upgraded.

**Do NOT run during US market hours** if you want the sandbox to behave
realistically — TT's CERT environment is more deterministic outside
market hours.

## How to run

```powershell
# From the cc repo root, with TASTYTRADE_PROVIDER_SECRET +
# TASTYTRADE_REFRESH_TOKEN populated in the environment:
.\scripts\run_capped.ps1 python scripts/tasty_sandbox_smoke.py
```

Expected duration: 5-30 seconds depending on TT sandbox responsiveness.

## Success criteria

All 4 probes report PASS in their stdout summary. Final line:

```
[<UTC timestamp>] All Phase-0 smoke probes PASSED for <account#> on TT CERT.
```

## Failure-mode triage

| Symptom | Likely cause | Action |
|---|---|---|
| `FAIL connect: ... 401` | Credentials invalid or expired | Refresh `TASTYTRADE_REFRESH_TOKEN` in KV / env (TT rotates these on each session — a stale token will 401) |
| `FAIL place_multi_leg auth/scope` | Existing OAuth scope is market-data only, not order placement | **File a re-grant ticket for ORDER scope.** Production data path keeps working (read-only); this gates Phase 2 live trading, not Phase 1 paper observation |
| `FAIL place_multi_leg unknown` | Combo shape problem | Compare the legs list against `tests/test_tastytrade_broker_real_sdk.py::test_new_order_4_leg_iron_condor_builds` — SDK shape may have drifted |
| `FAIL get_option_greeks` (non-warning) | Data provider not injected or SDK signature drift | Re-run `tests/test_tastytrade_broker.py::test_get_option_greeks_delegates_to_data_provider` to isolate |
| Greeks fetch WARNING (script continues) | Sandbox dxFeed doesn't stream cert symbols | Acceptable — Greeks delegation shape is unit-tested. Continue to next step |

## After a clean run

Per CLAUDE.md "After every successful deploy, append an entry to
`runbooks/deploy_log.md`":

```markdown
## 2026-05-XX — tasty_options Phase-0 sandbox smoke PASSED

**Features shipped:** none (smoke verification only)
**Notable code changes:** none
**Verification:** scripts/tasty_sandbox_smoke.py exit 0. All 4 probes
PASS on TT CERT. account=<account#>, equity=$<X>, smoke combo
expiration=<YYYY-MM-DD>, terminal-status=<Live|Cancelled|Expired>.
OAuth order scope: <confirmed|re-grant filed>.

**Next:** Phase 1 paper-internal observation begins. tasty_options
division wired in main.py, broker paper-wrapped via PaperExecutionBroker
(auto_execute: false in strategies.yaml). Minimum 21 calendar days
through ~2026-06-XX. Generate review doc before Phase 2 (do NOT
pre-flip auto_execute — see memory feedback_never_pre_flip_verified_flags).
```

## Don't change

- Strike values are intentionally far-OTM ($400P/$700C at SPY) to
  guarantee no accidental fill. If SPY moves dramatically, adjust the
  `_SMOKE_STRIKES` constants in the script rather than running with
  newly-near-the-money strikes.
- `is_test=True` is hardcoded — this script must NEVER run against
  the production endpoint. The TT cert environment is the only safe
  place for these probes.
