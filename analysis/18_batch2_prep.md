# Batch 2 PREP (PC-side scratch venv; NO box change) -- 2026-11-24

Goal: confirm the minimal Starlette KEV fix and the uvicorn 127.0.0.1 bind for the weekend window (Batch 2+4).

## Exact pins (CONFIRMED by resolver + test)
- **starlette==1.0.1** (the CVE-2026-48710 "BadHost" / CISA-KEV fix; box baseline is 1.0.0 per 11_pip_baseline.txt).
- **fastapi==0.136.1 -- UNCHANGED.** fastapi 0.136.1 requires `starlette>=0.46.0` (open upper), so it already accepts
  1.0.1. `pip check` on the pair = "No broken requirements found". **No FastAPI bump needed** -- this CORRECTS the
  earlier advisory-agent claim that fastapi had to go to 0.163.2.
- So the Batch 2 minimal change is a single pin bump: `starlette 1.0.0 -> 1.0.1`.

## Web-layer test result (scratch venv, box-matching core web stack)
Ran the repo web/webhook tests from the worktree under BOTH starlette versions:
`tests/test_webhooks_return_fast.py, test_webhook_audit_trail.py, test_mace_web_wiring.py, test_mace_web_reskin.py, test_scrub_webhook_rejected_backfill.py`
- starlette **1.0.1**: 5 failed (rest pass).
- starlette **1.0.0**: 5 failed (rest pass).
- **Compare-Object failed-set: IDENTICAL** -> the starlette bump causes **zero regression**.
- The 5 failures are ALL in `test_webhooks_return_fast.py` with `AttributeError: '_Deps' object has no attribute
  'bitunix_observer'` (webhooks.py:785) = **pre-existing test-fixture drift** (the test's mock `_Deps` was not
  updated when `bitunix_observer` was wired into the webhook flow). Unrelated to starlette; flag for repo test maint.
- Excluded `tests/test_webhook_research_consult_integration.py` (collection error: imports `FakeMacroExpert` from
  another test module -- pre-existing cross-test dependency, not starlette-related).
- Caveat: scratch venv used the core web stack (fastapi/starlette/pydantic/jinja2/httpx/tzdata/pytest), not all 159
  baseline pins; the 1.0.1-vs-1.0.0 comparison is apples-to-apples (only starlette changed). A full-suite run can be
  repeated in the box venv during the weekend window.

## uvicorn 127.0.0.1 bind -- DRAFT (NOT applied)
- File: `trading_corp/main.py`
- Change: line **2799** `    host: str = "0.0.0.0",`  ->  `    host: str = "127.0.0.1",`
- The sole caller `_start_web_server(...)` at `main.py:2375` does NOT pass `host=`, so the default governs -> the
  one-line change binds the dashboard to localhost. Caddy reverse-proxies to the app over localhost, so the dashboard
  stays reachable; only direct intra-VNet access to 0.0.0.0:8000 is removed (M5).
- (The pm_web dashboard on :8081 already binds 127.0.0.1 -- no change needed there.)
- Apply in the weekend window WITH the starlette bump + the one engine restart (Batch 2), alongside the kernel+reboot (Batch 4).
