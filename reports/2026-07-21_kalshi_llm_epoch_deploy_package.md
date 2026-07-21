# kalshi_llm_arbitrage dashboard epoch — DEPLOY PACKAGE (operator-executed)

**Date:** 2026-07-21 · Packaged, NOT deployed/committed this session. Decisions locked: **B-core + B-open**, epoch **2026-07-07T16:40:00+00:00**, set now.

## Change (web/data.py only, +6 lines, 6040→6046)
Base (prod, pre-patch) LF-md5 **bac9fe54000bf2295cca34cfadb87f8a** → patched **0feaea3d45f872df18961aaad6ac5f9b**. Three hunks:

1. **DASHBOARD_RT_CUTOFFS** (after the `kalshi_crypto` line, ~3807):
   ```python
   "kalshi_llm_arbitrage": "2026-07-07T16:40:00+00:00",  # ...scopes round-trip metrics; OPEN honored via _llm_cut
   ```
   Auto-scopes tile + summary + history + badge via the existing `_kalshi_cutoff_clause("entry_ts")` (per-division-guarded → siblings + "All" view unaffected).

2. **`_query_pm_open_trades`** kalshi branch (after `kalshi_ph = ...`, ~4346):
   ```python
   _llm_cut = DASHBOARD_RT_CUTOFFS.get("kalshi_llm_arbitrage", "")
   ```

3. **`_query_pm_open_trades`** WHERE (after the `_kalshi_copy_mode_clause` line, ~4357):
   ```python
   + f"  AND NOT (json_extract(a.payload_json, '$.division') = 'kalshi_llm_arbitrage' AND a.ts < '{_llm_cut}') "
   ```
   Division-guarded; when `_llm_cut=''` (dict entry removed) the clause self-disables → full history.

## Verified against prod (read-only simulation of the patched SQL)
| Metric | before | after |
|---|---|---|
| Resolved / WR / P&L | 2,686 / 40.3% / −$472.67 | **0 / n-a / $0** |
| OPEN | 1,461 | **144** (Economics 91, Elections 53) |
| Equity | $532.84 | $532.84 (not epoch-scoped) |
| Self-disable (empty cutoff) | — | 1,461 (confirms clean rollback) |

Patched copy `py_compile`s clean; b64 roundtrips to `0feaea3d`.

## Deploy package (cc\, operator executes; one non-wrapping runner each)
Artifacts: `llm_epoch_pkg.b64` (patched data.py) + 6 runners. Backup lands at `~/trading_corp/.bak_llm_epoch_20260721/data.py.bak`.

1. `powershell -ep bypass -f .\le_driftgate.ps1` → expect **PASS** (prod==base bac9fe54).
2. `powershell -ep bypass -f .\le_deploy.ps1` → upload+verify(md5+py_compile)+backup+swap. **No restart.** Aborts before swap on mismatch.
3. `powershell -ep bypass -f .\le_flatcheck.ps1` → **READ-ONLY flat-window review.** Restart bounces **bitunix + PEAD + kalshi_copy + kalshi_llm** (web is in-process) and triggers **RH pickle re-auth** (self-heals via the 07-18 RH-auth resilience). Proceed only if: no fresh `board_approved`, no fills in-window, off PMCC-burst, no restart churn.
4. `powershell -ep bypass -f .\le_restart.ps1` → `sudo -n systemctl restart trading-corp` + boot-smoke + RH re-auth check.
5. `powershell -ep bypass -f .\le_verify.ps1` → expect: code=patched, **resolved 0**, **open 144 (Econ 91 / Elec 53)**, route HTTP 200.

## Expected post-restart dashboard
**0 resolved · $0 realized · 144 open (Economics 91, Elections 53) · equity $532.84 · "since 2026-07-07" badge.** Populates as the Econ/Elections positions settle (first ~Aug 5).

## Rollback
- `powershell -ep bypass -f .\le_rollback.ps1` → restore `.bak` + restart (full revert to bac9fe54).
- Partial alternative: delete the DASHBOARD_RT_CUTOFFS `kalshi_llm_arbitrage` line → `_llm_cut=''` → OPEN clause self-disables + metrics revert (still needs a restart to load).

## Restart / coupling notes
- **web is in-process** with the engine (uvicorn asyncio task) → loading a `web/data.py` change requires a full `trading-corp` restart; there is no web-only reload. Display-only change (no trading-logic touched).
- **No safety-guard coupling:** autopause reads round-trips only for copy divisions, never kalshi_llm; no other consumer reads kalshi_llm dashboard stats.
- Epoch is a code constant (not hot). Changing it later = another code edit + restart (or migrate to B2 agent_state-hot if runtime tuning is wanted).

*Guardrails honored: read-only (prod untouched; tracked repo file untouched — patched a staging copy); no commit/deploy; no memory characterizing edge/prospects; coupling checked.*
