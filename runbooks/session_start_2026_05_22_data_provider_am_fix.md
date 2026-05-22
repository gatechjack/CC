# Next-session pickup prompt (2026-05-22 AM) — data-provider SDK bug fix

*Written 2026-05-22 ~11:00 UTC at the end of the data-provider abstraction deploy session.*

*Two SDK bugs ship-blocking the full Tastytrade IV path. Fix this morning before the 09:45 ET (13:45 UTC) IC scan if possible — but hard rule: if it slips or surfaces a third bug, DO NOT rush. Tonight's deploy is strictly better than pre-deploy already; the 09:45 scan is safe in the current state.*

---

Paste this into a fresh Claude Code session at `C:/Users/AA Incorporado/cc`:

---

Resuming after the 2026-05-22 10:33 UTC data-provider abstraction deploy session. `a6885a5` (data-provider) + `92d6018` (deploy_log) are committed locally on `main`, 2 ahead of `origin/main`, NOT pushed. Prod is on `a6885a5`'s file changes (host-direct deploy; prod has no git). Service PID 1044543 since 10:33:42 UTC, IC scanner + position manager online, all strategies preserved.

Read the **EOS snapshot at the top of `BACKLOG.md`** (2026-05-22 ~11:00 UTC) first — it's the canonical record of where this branch left off.

## Headlines from last session

- **`a6885a5` SHIPPED to prod 2026-05-22 10:33 UTC.** Data-provider abstraction + Tastytrade primary + 1e-5 fix + IC None-branch + chain-shallow guard. 15 files via host-direct az tarball.
- **Deployed in known DEGRADED state — 2 SDK API bugs in `tastytrade_provider.py`** that mock-based tests couldn't catch (real-SDK shape mismatch):
  - Line 82-86: `Session(login=, remember_token=)` should be `Session(provider_secret=, refresh_token=)`. Unknown kwargs fall into `**client_kwargs`; SDK falls back to `os.environ["TT_SECRET"]` → KeyError.
  - Line 391: `from tastytrade.market_data import get_quote` — symbol doesn't exist in 12.4.1.
- **Effect:** `calc_atm_iv` and `get_underlying_price` return None. `calc_iv_rank` works (yfinance HV bars internally; live SPY = 0.342). IC fail-opens on term-structure check (same behavior as pre-deploy 1e-5 path). Net STRICTLY BETTER than pre-deploy.
- **Security note:** Tastytrade Client Secret leaked into chat transcript + Azure activity log during verification (bash-source on env file echoed value on syntax error). Operator labeled as `scope: read` only, exposure bounded, full token refresh tracked in infosec backlog. Deferred remediation. See [[feedback-never-bash-source-env-files]] for the process change.

## Read first

1. **`BACKLOG.md` top entry (EOS 2026-05-22 ~11:00 UTC)** — canonical wrap.
2. **`runbooks/deploy_log.md`** top entry (2026-05-22 10:33 UTC) — full deploy state, two bugs documented with file:line, verification results, security note with risk acceptance, rollback recipe, follow-ups queued.
3. **Memory (auto-loaded):** `project_data_provider_deploy.md`, `feedback_mocks_dont_catch_sdk_shape.md`, `feedback_never_bash_source_env_files.md`.

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 1 — Fix the two SDK bugs against the LIVE SDK
═══════════════════════════════════════════════════════════════════════════

Both bugs are in `trading_corp/data/tastytrade_provider.py`. Mocks couldn't catch them — the verification gate is a real authenticated SDK call, not mocked tests alone.

**Bug 1 — `Session()` kwargs (lines 82-86):**

Current:
```python
self._session = await asyncio.to_thread(
    Session,
    login=self._provider_secret,
    remember_token=self._refresh_token,
)
```

Fix: change kwarg names to the SDK 12.4 signature. Verify against `inspect.signature(Session.__init__)` — don't guess. Per the local verification probe earlier this session:
```
Session.__init__ signature:
    (self, provider_secret: 'str | None' = None,
     refresh_token: 'str | None' = None,
     is_test: 'bool' = False, **client_kwargs: 'Any')
```

So:
```python
self._session = await asyncio.to_thread(
    Session,
    provider_secret=self._provider_secret,
    refresh_token=self._refresh_token,
)
```

**Bug 2 — `get_quote` import (line 391):**

Current:
```python
from tastytrade.market_data import get_quote  # type: ignore
session = await self._get_session()
quote = await asyncio.to_thread(get_quote, session, symbol)
```

`get_quote` doesn't exist in `tastytrade.market_data` 12.4.1. Look up the actual SDK surface for a single-symbol spot quote. Candidates to investigate (verify against the installed package, do NOT guess):
- `tastytrade.market_data.get_market_metrics(session, [symbol])` — returns IV/volatility metrics; may also include spot.
- `tastytrade.market_data.a_get_market_metrics` (async variant).
- `tastytrade.streamer.DXLinkStreamer` + `Quote` event from `tastytrade.dxfeed`.
- Worst case: fall back to yfinance for spot (already imported in `_fetch_close_series`). Document if used.

**`get_underlying_price` is only called by `_compute_atm_iv` for ATM strike detection.** It is NOT called by IC strategy (which uses `broker.quote()` via Robinhood). So the fix only needs to produce a workable spot for the provider's internal ATM detection — accuracy matters but the integration surface is small.

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 2 — Bundle the two prior follow-ups (deferred from a6885a5)
═══════════════════════════════════════════════════════════════════════════

Fold these into the same commit as the SDK bug fix:

1. **Move `_hv_to_rank` to neutral location.** Currently in `trading_corp/data/yfinance_provider.py`, imported by `tastytrade_provider.py`. Architecturally, Tastytrade shouldn't depend on yfinance for math. Move to `trading_corp/data/_iv_math.py` (new file), update both providers' imports. Pure refactor, no behavior change.

2. **Tiny Fidelity test asserting `_calc_iv_rank` resolves to the shared util.** A6885a5 deleted the Fidelity duplicate at `fidelity_options.py:139-166` and replaced with `from trading_corp.utils.iv import calc_iv_rank as _calc_iv_rank`. The byte-equivalence was proven by inspection + indirect coverage. Add a small explicit assertion test so future renames don't silently break the import:
   ```python
   # tests/test_fidelity_uses_shared_iv_rank.py
   def test_fidelity_calc_iv_rank_is_shared_util():
       from trading_corp.agents.divisions.fidelity_options import _calc_iv_rank
       from trading_corp.utils.iv import calc_iv_rank
       assert _calc_iv_rank is calc_iv_rank
   ```

═══════════════════════════════════════════════════════════════════════════
## VERIFICATION GATE (must pass before deploying the fix)
═══════════════════════════════════════════════════════════════════════════

**Mandatory:** real authenticated Tastytrade call against the LIVE SDK in prod's environment. Mocks alone caused last session's surprise.

1. **Locally, run the existing test suite (351/351 must hold):**
   ```powershell
   .\scripts\run_capped.ps1 python -m pytest tests/test_market_data_provider.py tests/test_tastytrade_provider.py tests/test_yfinance_provider.py tests/test_provider_factory.py tests/test_iv_rank.py tests/test_iron_condor_strategy.py tests/test_ic_live_view.py tests/test_ic_telemetry.py tests/test_ic_orchestration.py tests/test_iron_condor_config.py tests/test_combo_approval.py tests/test_paper_run_tooling.py tests/test_robinhood_joint_division.py tests/test_pmcc_logic.py tests/test_pmcc_position_context.py tests/test_pmcc_scout_research_integration.py tests/test_pmcc_research_validation_view.py tests/test_boot_smoke.py -v
   ```
   Plus the new Fidelity assertion test. Plus a new test for `_iv_math.py` if you create it.

2. **Live SDK call in prod (or against operator's local creds):** must return real numbers for `get_atm_iv("SPY")`, `get_atm_iv("IWM")`, `get_atm_iv("TLT")` — the three symbols including the two that yesterday's yfinance corrupted to 1e-5. Also `get_underlying_price("SPY")` must return a real spot.

   Use the same `cc/tmp/tasty_validation.py` pattern from Step 0 of the original deploy. Authenticate via env vars locally OR via prod's env (test in prod after deploy, but DON'T restart the service until the test passes).

3. **NO `bash source` of env files.** Use python-direct env loader for verification (template in [[feedback-never-bash-source-env-files]] memory). If you accidentally trigger another leak, the rotation cost is real.

═══════════════════════════════════════════════════════════════════════════
## Deploy timing — hard rules
═══════════════════════════════════════════════════════════════════════════

- **Target:** land the fix before 09:45 ET (13:45 UTC) so tomorrow's IC scan gets full Tastytrade ATM IV.
- **If you can't verify the live SDK call passes for all 3 symbols by ~09:30 ET (13:30 UTC):** STOP. Do NOT deploy. The 09:45 scan runs in tonight's strictly-better state (acceptable fallback). The good state is already deployed; full Tastytrade ATM IV is the low-pressure goal.
- **If you surface a third bug during the fix:** STOP. Same fallback applies. Investigate the third bug with a clear head; don't rush.
- **If verification passes:** standard deploy via host-direct az tarball or scp (see `runbooks/deploy_log.md` for the recipe; the 2026-05-22 10:33 UTC entry has the latest pattern).

═══════════════════════════════════════════════════════════════════════════
## Things to NOT do without explicit approval
═══════════════════════════════════════════════════════════════════════════

- **Don't push to `origin/main`** as a side effect of any deploy. It's a separate decision. Local is 2 ahead.
- **Don't `bash source` the env file** under any circumstance. Python-direct loader only.
- **Don't try to fix the credential leak in this session** unless the operator explicitly raises it. Tracked in infosec backlog; deferred per operator's risk acceptance.
- **Don't touch parallel-session surfaces** — Kalshi (any), BitUnix (any), Polymarket (any), Coinbase (any). The data-provider work is isolated to the IV/options path.
- **Don't rush the fix to hit 09:45.** The fallback state is acceptable.
- **Don't deploy without the live-SDK verification passing.** Mocks-alone caused last session's bugs.
- **Don't restart trading-corp on prod without verifying the env file is present** — `EnvironmentFile=/etc/trading-corp/tastytrade.env` is `ignore_errors=no`, service refuses to start if missing.
- **Don't flip IC's `auto_execute: false → true`** under any circumstance. Same rule as before — 90-day paper-run-readiness gate stands.
- **Don't touch CLAUDE.md, PROJECT_CONTEXT.md, runbooks/** as part of this work. Out of scope.

═══════════════════════════════════════════════════════════════════════════
## Environment notes
═══════════════════════════════════════════════════════════════════════════

- **VM:** `tc-prod-vm` in `rg-shared-prod`. Public IP `20.51.145.253`. SSH from operator's home IP works (verified last session); `az vm run-command invoke` is the reliable fallback.
- **Prod systemd state:** `/etc/systemd/system/trading-corp.service.d/override.conf` includes `EnvironmentFile=/etc/trading-corp/tastytrade.env`. File is 600 root:root, holds 2 keys. `EnvironmentFiles` resolves as `(ignore_errors=no)` — service won't start if file missing.
- **Prod venv:** `/home/azureuser/trading_corp/venv/bin/python` (Python 3.12). `tastytrade==12.4.1` + `httpx_ws==0.9.0` + `wsproto==1.3.2` installed.
- **Local Python:** `C:\Users\AA Incorporado\AppData\Local\Python\bin\python.exe` (Python 3.14.4). Wrap pytest via `scripts\run_capped.ps1` per CLAUDE.md § STOP AND READ #6.
- **Templates hot-reload in prod (Jinja). `.py` changes do NOT** — need `sudo systemctl restart trading-corp`.
- **Backup tags from last session (kept):**
  - `pre-data-provider-deploy-20260521` on 4 prod files + override.conf
  - Backup files at `/home/azureuser/trading_corp/*.pre-data-provider-deploy-20260521` (4) + `/etc/systemd/system/trading-corp.service.d/override.conf.pre-data-provider-deploy-20260521`

═══════════════════════════════════════════════════════════════════════════
## Service health at session start (snapshot from 2026-05-22 ~11:00 UTC)
═══════════════════════════════════════════════════════════════════════════

```
Prod (tc-prod-vm):       trading-corp.service active, MainPID 1044543
Uptime:                  since 2026-05-22 10:33:42 UTC
IC tasks online:         yes (signal scanner + position manager)
RH bound:                3 accounts (individual / ira_traditional / joint_tenancy_with_ros)
Tastytrade env in proc:  TASTYTRADE_PROVIDER_SECRET + TASTYTRADE_REFRESH_TOKEN both <set>
auto_execute on IC:      false (load-bearing)
Boot smoke (readiness):  13/13 BLOCK checks pass, STATUS: READY
```

If service is no longer `active` when you start, the most likely culprit is the env file got modified/deleted between sessions (systemd refuses to start without it). Check `/etc/trading-corp/tastytrade.env` presence + permissions first.

Honest assessment first: read this prompt + the deploy_log top entry + the EOS snapshot before proposing any "let me just X" plan. The deploy that landed is real prod work modifying a live strategy; treat the AM follow-up with the same weight.
