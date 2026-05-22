# Next-session pickup prompt — 2026-05-23 (morning)

*Written 2026-05-22 ~14:00 UTC at session wrap.*

> **STATUS UPDATE — 2026-05-22 ~22:30 UTC (added at end of kalshi_weather Phase D session):**
> - **PRIORITY 1 (AM provider SDK fix) is DONE.** `e977641` deployed to prod 2026-05-22 16:47:11 UTC. Live probe confirmed: SPY ATM IV 0.1508, IWM 0.2243, TLT 0.1029, SPY spot 747.30 — all real via Tastytrade. See `runbooks/deploy_log.md` 2026-05-22 16:47 UTC entry and memory `[[data-provider-deploy]]`.
> - **PRIORITY 2 (grader §6 live verification) and PRIORITY 3 (deploy) remain open** — proceed from PRIORITY 2 below.
> - **Other things landed between this doc and the morning pickup** (not in PRIORITY 1-3's critical path, just context):
>   - kalshi_weather Item 2 (hourly re-eval) investigated and **CLOSED — no signal** (commits `4f7fe50` + `5d3d859`; findings at `planning/kalshi_weather_hourly_reeval_findings.md`; memory `[[kalshi-weather-hourly-reeval-closed]]`). `quote_snapshot` persistence NOT being built.
>   - Dashboard kalshi_weather cutoff advanced to P3 deploy 2026-05-22 16:25 UTC via surgical sed on prod (commits `90b3491` + `98c7824`; deploy_log 22:17 UTC entry). Filter-only; 82 floor-era RTs preserved.
>   - All 7 session commits pushed to `origin/main` (`d756388..98c7824`).
>   - kalshi_weather P3 observation week still in progress through ~2026-05-29 (untouched).

Picks up after the IC morning-candidate grader was committed
(`112aef3`) but intentionally NOT deployed.  The grader's ship gate is
the AM provider SDK fix — that work has its own runbook
(`session_start_2026_05_22_data_provider_am_fix.md`); this file is the
**coordinating** pickup that sequences AM-fix → grader-§6 → grader-deploy.

---

## Read first (in this order)

1. **`BACKLOG.md` EOS snapshot at top** — 2026-05-22 ~14:00 UTC entry.
2. **Memory (auto-loaded):**
   - `project_ic_grader_committed.md` — what's committed, what isn't, the
     three open gates in dependency order.
   - `project_data_provider_deploy.md` — the deploy that introduced the
     AM-fix-pending degradation.
   - `feedback_mocks_dont_catch_sdk_shape.md` — why §6 is live-only.
   - `feedback_crlf_routes_py_deploy.md` — the deploy-time CRLF rule.
3. **`.claude/plans/planning-session-ic-hashed-kettle.md`** — the grader's
   plan, with Verification §6 spelled out.
4. **`runbooks/session_start_2026_05_22_data_provider_am_fix.md`** — the
   AM-fix runbook with the two SDK bugs detailed.

---

## Ship sequence (do NOT reorder)

```
[1] AM provider SDK fix lands + verified live
       ↓
[2] Grader §6 against the AM-fixed provider (real ATM IV, full gate-7)
       ↓
[3] CRLF-normalize routes.py + deploy grader + re-run §6 in prod
```

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 1 — AM provider SDK fix
═══════════════════════════════════════════════════════════════════════════

Owned by `runbooks/session_start_2026_05_22_data_provider_am_fix.md`.
Two SDK bugs:
1. `Session()` kwargs (`login=` / `remember_token=` → `provider_secret=` /
   `refresh_token=`).
2. `from tastytrade.market_data import get_quote` — `get_quote` doesn't
   exist in SDK 12.4.1; replace with `get_market_metrics` /
   DXLinkStreamer / yfinance fallback per the runbook.

Plus two queued follow-ups:
- `_iv_math.py` move.
- Fidelity test.

Plus the env-var bypass cleanup ([[feedback-tastytrade-env-vars-bypass-kv]]).

**Acceptance for moving to PRIORITY 2:** `provider.get_atm_iv("SPY", 45)`
returns a real float (not None), against the live Tastytrade SDK, without
the `get_quote` import path crashing. Test in a local Python shell, not
just via the mock-based suite.

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 2 — Grader §6 live-verification (ship gate)
═══════════════════════════════════════════════════════════════════════════

After PRIORITY 1 acceptance, run the grader's plan §6 against the LIVE
provider, locally. Recipe in
`.claude/plans/planning-session-ic-hashed-kettle.md § Verification §6`:

1. Real Tastytrade env vars set (`TASTYTRADE_PROVIDER_SECRET` +
   `TASTYTRADE_REFRESH_TOKEN`).
2. Paste a real in-universe candidate (SPY with a near-term real
   expiration that exists on the chain) into the grader at
   `POST /telemetry/iron_condor/grade` (via curl against a local web
   server, or via TestClient with the patch removed).
3. Expect: the row reaches gate 7 (term-structure) and either
   PASSes/FAILs on **real** spread numbers OR — only if the provider is
   still degraded — `NEEDS_LIVE_DATA` with the documented reason.  A
   genuine PASS/FAIL with real numbers is what closes the gate; a
   NEEDS_LIVE_DATA outcome here means PRIORITY 1 isn't actually done.
4. Confirm one `kind='ic_grader_run'` audit row written with the
   payload shape from the plan.

**Acceptance for moving to PRIORITY 3:** the gate-7 comparison path runs
against real ATM IV numbers, not the None branch.

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 3 — CRLF-normalized deploy + §6-in-prod
═══════════════════════════════════════════════════════════════════════════

After PRIORITY 2 closes:

1. **Normalize line endings on `routes.py` at transport.** Working tree
   is CRLF; prod is LF. Use one of:
   - `dos2unix trading_corp/web/routes.py` on a deploy-staging copy
     (do NOT commit the conversion — preserves git blame).
   - Pipe through `tr -d '\r'` in the deploy script before scp.
2. Transport the 5 new + 1 modified files (commit `112aef3`'s file list,
   see memory `project_ic_grader_committed.md`) to prod via the existing
   chunked-az pattern from
   `[[trading-corp-iron-condor-v1]]`'s ship.  Backup the modified file
   (`routes.py`) AND `partials/iron_condor_static_sections.html` first
   (`.pre-grader-20260523-HHMM` tag pattern).
3. Restart `trading-corp.service`. **Refresh `robinhood.pickle` first**
   per `[[kalshi-weather-floor-data-gap-20260521]]` — every restart with
   an expiring pickle risks a multi-cycle MFA loop.
4. Repeat the §6 test in prod (curl against the live dashboard) and
   confirm the same real-numbers PASS/FAIL.
5. Append a deploy_log entry per the template at the top of
   `runbooks/deploy_log.md`.
6. Decision: push to `origin/main` — separate from the deploy. The
   commit was deliberately held off origin during this session.

═══════════════════════════════════════════════════════════════════════════
## Things to NOT do without explicit approval
═══════════════════════════════════════════════════════════════════════════

- **Don't deploy the grader before PRIORITY 1 + 2 close.** A degraded-
  provider deploy would always emit `NEEDS_LIVE_DATA` on gate 7,
  hiding the real comparison path from §6 forever.
- **Don't normalize CRLF in a commit.** Transport-time only. Otherwise
  every line in `routes.py` shows up in `git blame` as your touch.
- **Don't push to `origin/main` as part of the grader deploy.** Push
  decision is separate; the operator hold-off was deliberate.
- **Don't touch the 5 stranded shared files** (parallel-session
  deconfliction). Unchanged at session end.
- **Don't `git add -A`** — `docs/Deployment notes.txt` is pre-existing
  untracked, unrelated, leave it alone.

═══════════════════════════════════════════════════════════════════════════
## Other open items (defer; not in the morning critical path)
═══════════════════════════════════════════════════════════════════════════

- **Kalshi weather forward-validation watch.** First post-deploy
  round-trips were expected 14:00–19:00 UTC 2026-05-22 (Friday
  afternoon).  By morning of 2026-05-23 the first batch should be
  resolved.  Run the PRIORITY-3 queries in
  `runbooks/session_start_2026_05_21_kalshi_post_deploy.md` to read
  the floor's early signal.  ⚠ DATA GAP: 2026-05-21 00:06–00:34 UTC
  outage (`[[kalshi-weather-floor-data-gap-20260521]]`).
- 5 stranded shared files — coordinate when the parallel session is
  ready; not actionable solo.
- 7 CRITICAL findings from the 2026-05-21 security review
  (`[[project-security-review-2026-05-22]]`) — none remediated. Pick
  up after the grader ship sequence closes.
- BitUnix Phase 4 live-REST gate at ~2026-07-19; no action before then.
