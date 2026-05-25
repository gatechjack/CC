# Next-session pickup prompt (post tasty_options build + Phase-0 GREEN)

*Written 2026-05-24 ~23:55 UTC at the end of a session that built the
`tasty_options` division from scratch in 5 commits + 2 fixups
(a6990cd..26a191e), verified Phase 0 GREEN on TT PRODUCTION, and
**queued the prod deploy for the next session.** Local main is 1 commit
ahead of origin at the moment of writing; the wrap commit + final push
land alongside this file.*

---

## Paste this into a fresh Claude Code session at `C:/Users/AA Incorporado/cc`:

---

Resuming after the 2026-05-24 ~23:55 UTC tasty_options build session.
**`origin/main` head after wrap push: `<wrap-commit>` (TBD; will be the
backlog+runbook wrap commit on top of `26a191e`).**

The prior session built a new `tasty_options` equity-options division
(sibling of `robinhood_joint` on Tastytrade, with a permissive
"watchlist" replacing the hard-gate "universe") in 5 planned commits +
2 fixups. Phase-0 sandbox smoke is GREEN end-to-end on TT PRODUCTION
account `5WZ66443`. **The division is NOT yet deployed to prod** —
operator deferred deploy. **First action this session: deploy.**

## Read first (in this order)

1. **`BACKLOG.md` top entry** (EOS 2026-05-24 ~23:55 UTC) — full
   session context, OAuth saga, all 5 forward-watch items.
2. **`runbooks/deploy_log.md`** most recent entry: **2026-05-24 23:52 UTC**
   (Phase-0 smoke PASSED, deploy NOT executed). Comprehensive code-changes
   inventory + verification record.
3. **`.claude/plans/i-want-to-create-enumerated-papert.md`** — the
   approved plan from the build session. Reference, not action-list.
4. **Memory:**
   - `[[project-tasty-options-paper-clock]]` — phase clock + exit
     criteria
   - `[[reference-tastytrade-sdk-sandbox-mode]]` — `Session(is_test=True)`
     routes to CERT (separate OAuth app required); `PaperSession` is
     third-party tastyware.dev mock; SDK has full order API
   - `[[reference-tastytrade-oauth-scope-widening]]` (NEW this session) —
     the read+trade scope diagnostic recipe + in-process-env-vs-registry
     gotcha + JWT-scope-claim verification command
   - `[[feedback-mocks-dont-catch-sdk-shape]]` — escalated again this
     session: the 7 async-wrapping bugs in tastytrade.py were the SIXTH
     instance of this failure mode. The fixup commit (`672f658`) and
     the `AsyncMock` test bulk-replace are the proof
   - `[[feedback-tastytrade-env-vars-bypass-kv]]` — RESOLVED this session
     (`a6990cd` added TT vars to expected_env_vars + Secrets dataclass);
     can be retired from the memory index OR updated to "shipped"

═══════════════════════════════════════════════════════════════════════════
## PRIMARY TASK — DEPLOY tasty_options TO PROD
═══════════════════════════════════════════════════════════════════════════

### What's queued (7 commits + 12 runtime files)

Commits since `0a98bbf` (last prod-deployed UI cleanup):
- `a6990cd` — TastytradeBroker + secrets KV
- `d7e0afd` — IC grader parameterization (preserves RH Joint behavior
  via defaults; 28-test regression covers it)
- `a9e4e46` — division shell + strategy clone (1750-LOC strategy)
- `94b3129` — config + main.py wiring + dashboard tile
- `613c7fa` — Phase-0 sandbox smoke + runbook (LOCAL-ONLY; not in
  prod-runtime path)
- `672f658` — broker async-call fixup (7 sites: `to_thread(async_fn)`
  → `await async_fn()`)
- `26a191e` — broker `dry_run` param + smoke iteration to GREEN

### 12 runtime files to deploy

```
trading_corp/brokers/tastytrade.py                      NEW
trading_corp/agents/divisions/tasty_options.py          NEW
trading_corp/agents/strategies/tasty_options_iron_condor.py  NEW (1750 LOC)
trading_corp/agents/strategies/ic_candidate_grader.py   MOD (load-bearing; defaults preserve RH Joint)
trading_corp/utils/secrets.py                           MOD (additive)
trading_corp/main.py                                    MOD (HIGH RISK — startup-critical)
trading_corp/web/app.py                                 MOD (additive: 4 new WebDeps fields)
trading_corp/web/routes.py                              MOD (?division= query param; defaults preserve RH Joint)
trading_corp/web/templates/home.html                    MOD (tile routing exception added)
trading_corp/web/templates/iron_condor_live.html        MOD (division-aware header chip)
config/divisions.yaml                                   MOD (new tasty_options entry)
config/strategies.yaml                                  MOD (new tasty_options_iron_condor block)
```

### Deploy recipe (mirrors `0a98bbf` UI cleanup pattern)

1. **md5-diff** all 12 files local vs prod (recipe in deploy_log.md
   preamble). Confirm exactly which are NEW vs MOD; flag any surprise
   matches that suggest the deploy is partially done (memory
   `[[feedback-session-committed-phantom-pointer]]`).

2. **Backup-tag MOD files** on prod with
   `pre-tasty-options-deploy-YYYYMMDD-HHMM`. NEW files don't need tags.

3. **scp/rsync** all 12 files to prod paths (write to `.new` tempnames
   if possible, then atomic `mv`; or just direct scp — the UI deploy
   used direct).

4. **Restart** the trading-corp service:
   `sudo systemctl restart trading-corp.service`

5. **Watch journal for ~60s** for startup errors:
   `journalctl -u trading-corp -f`
   Looking for: zero Python tracebacks, `TastytradeBroker connected`,
   `TastyOptionsAgent attached`, `tasty-signal-scanner` + `tasty-position-manager`
   task spawn lines.

6. **Dashboard verification:**
   - `https://trading.jacksumner.com/` — Tasty Options tile renders in
     Individual group with intent chip + status badge + equity number
   - Click tile → IC live view at `/telemetry/iron_condor?division=tasty_options`
     with "Tasty Options" header chip
   - Paste a known-pass row into the grader, confirm `division=tasty_options`
     audit stamp
   - Paste an off-watchlist row (e.g. NVDA), confirm amber
     `off_watchlist_warn` chip + verdict still PASS/FAIL on downstream
     gates (NOT blocked at gate 1)
   - Regression: open `/telemetry/iron_condor` (no query string) and
     verify Robinhood Joint view still works

7. **Append `runbooks/deploy_log.md`** entry with the template format
   (commit hashes, backup tag, files deployed, features shipped,
   verification PIDs, rollback recipe).

### Key safety considerations

- **main.py is the highest-risk file.** Startup import error =
  trading-corp service won't start = ALL divisions offline. The async
  fixup commit (`672f658`) is what de-risks this — the 7 `to_thread`
  sites are now `await` calls that pass real-SDK shape tests. If
  startup fails anyway, rollback via the backup tag + restart.
- **Broker is paper-wrapped in PAPER mode** via PaperExecutionBroker
  in `_build_broker_for_division`. Even with trade-scoped credentials
  on prod, no real TT orders will fire during Phase 1
  (auto_execute=false + paper wrap = double-gated).
- **OAuth scope on prod** is still `read`-only (the trade-scoped token
  lives only in operator's local Windows registry). Phase 1 works fine
  with read scope (snapshot + chains). Phase 2 promotion requires
  pushing the trade-scoped token to prod's `/etc/trading-corp/tastytrade.env`
  via the OAuth rotation runbook (P1 HIGH, queued).

### When the deploy is done

- Update memory entry `[[project-tasty-options-paper-clock]]` to mark
  Phase 1 as STARTED with the actual start timestamp.
- Memory `[[feedback-tastytrade-env-vars-bypass-kv]]` is resolved by
  `a6990cd` — update or retire from index.
- Append a one-line entry to `BACKLOG.md` confirming the deploy + open
  Phase-1 monitoring obligation.

═══════════════════════════════════════════════════════════════════════════
## SECONDARY TASK (only after deploy is green)
═══════════════════════════════════════════════════════════════════════════

**Write `runbooks/tastytrade_oauth_rotation.md`** — the runbook that's
been P1 HIGH untouched since 2026-05-22. This session generated all the
forensics needed:

- The 2-step grant procedure (Mode 1 / Mode 2 in `tmp/tasty_oauth_bootstrap.py`)
- The diagnosis template for the failure chain:
  - `invalid_grant: Grant revoked` → token issued under pre-rotation
    Client Secret → re-bootstrap with matched pair
  - `invalid_grant: Invalid JWT` → bootstrap wrote non-JWT token
    (no eyJ b64 prefix); wrong token type from the OAuth flow
  - `invalid_grant: Client secret mismatch` → bootstrap produced a JWT
    but Client Secret in env differs from one used during grant
  - **NEW (this session): Token returns 403 "insufficient scopes" on
    order placement → OAuth app at TT portal doesn't permit `trade`
    scope; widen app scope, re-grant. JWT-decode the refresh token
    to verify `scope` claim contains both `read` and `trade`.**
  - **NEW (this session): `setx` writes to User registry but
    in-process PS keeps old value until close+reopen. Diagnose by
    comparing `$env:X` vs `[Environment]::GetEnvironmentVariable("X","User")`.**
- The atomic Client Secret + JWT refresh_token write to prod's
  `/etc/trading-corp/tastytrade.env` (chmod 600 root:root, 2 lines).
- Pre-emptive value: most useful BEFORE the next rotation, not after.
  Current trade-scoped refresh_token expires ~2026-06-21 (28-day TT
  default from the 2026-05-24 grant).

═══════════════════════════════════════════════════════════════════════════
## What you can skip / defer
═══════════════════════════════════════════════════════════════════════════

- **3 inherited test failures** in `tests/test_iron_condor_strategy.py`
  (and clone `tests/test_tasty_options_iron_condor.py`). Pre-existing
  from RH Joint test owner. Not blocking this work.
- **kalshi_weather observation week** through ~2026-05-29 — separate
  parallel-session thread tracked in
  `runbooks/session_start_2026_05_25_post_kalshi_weather_autopsy.md`.
- **Polymarket metrics-epoch first post-epoch resolved trade watch** —
  parallel thread; first signal check.

═══════════════════════════════════════════════════════════════════════════
## Quick health check before any deploy work

```bash
# Local: working tree, sync state, smoke run
cd "C:/Users/AA Incorporado/cc"
git status -sb                   # expect: clean except possible untracked docs/
git log --oneline origin/main..HEAD  # expect: clean (origin caught up)
./scripts/run_capped.ps1 python -m pytest tests/test_tasty_options_division.py tests/test_tastytrade_broker.py tests/test_ic_grader.py -q
# expect: all green

# Prod state probe (per CLAUDE.md "verify prod state before deploy"):
ssh azureuser@trading.jacksumner.com "systemctl status trading-corp --no-pager | head -5; cat /home/azureuser/trading_corp/CLAUDE.md | head -2"
# expect: active (running), file present
```

Operating discipline (carry forward from prior sessions):
- Delegate mechanical work to Sonnet (worked well for the 1750-LOC
  strategy clone in `a9e4e46` — Sonnet did the clone; this thread
  verified)
- Stop-and-report at forks; surface anomalies with diagnostic detail
- Don't expand scope mid-task
- Tighter commits than feels normal — commit deploy_log entries as you go
