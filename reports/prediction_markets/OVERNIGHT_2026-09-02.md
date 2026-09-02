# OVERNIGHT 2026-09-02 — Per-Account Trading (Karen). Handoff for Jack.

**Read this instead of scrollback.** Written for someone with no memory of tonight. Updated after each rung.
Branch: `pm-per-account-trading-2026-09-02` (worktree `C:\Users\AA Incorporado\cc-pm-peracct-wt`), base `f1e28cc`
(pm-multiaccount tip). Plan: `.claude/plans/zippy-squishing-starlight.md` + its 5 rulings.

---

## ★★ THE LIVE SYSTEM RIGHT NOW — NOTHING TONIGHT TOUCHED THE BOX

I built and tested **only in a local worktree + a local venv.** I made **zero** box deploys, restarts, DB writes,
arms, or prod-live advances. The live jack-mlb division is trading, untouched.

- **Current live state (per SW8 `TRANSITION_SESSIONWRAP8_2026-09-02.md`, observed 04:54Z — the accurate read; I could
  not re-read myself, Rung 0 blocked):** engine PID **144229** (unchanged), pm_web **155543**, **schema 17**,
  boot-reconcile clean. jack-mlb **ARMED, not latched, trading** (orders_today 15/50, 1 open position). Shards:
  kalshi_jack **$506.01**, **kalshi_karen $486.29 (sh3 461.28 / sh0 25.01)** — Karen's mlb shard is funded.
- **★★ A CONCURRENT SESSION ran today (now WRAPPED via SW8) — relevant to my N3:** it DISARMED legacy
  `poly_kalshi_mlb` (commit `ed2e6c0` on **local main-wip**, NOT pushed, NOT mine) via a `[G-halt]` DB gate — no
  restart, engine unchanged. **This is RULING 1's action (legacy retiring off Karen's shared account).** ★ BUT its
  commit message says *"Open STL-LAD position rides to settlement"* — **legacy is DISARMED, NOT FLAT.** Karen still
  holds an open legacy STL-LAD position until it settles, so **N3 is not yet closed** (see the Rung-4 gate below).
  My branch is independent — I never touched `main-wip` or the concurrent session's pm_web work.
- **★ I COULD NOT VERIFY THIS LIVE.** The harness safety classifier **blocked the Rung-0 read-only box runner**
  (it reads the runner as autonomous production access — signed venue reads with Karen's real keypair + dumping the
  engine's cred env — and was still weighing the original "plan and halt" framing). That is a reasonable gate on a
  live-money box, and it matches the command-paste-rule default anyway: **box runners are staged and you execute
  them.** So Rung 0 is written + staged, awaiting your run (one-liner in the DEPLOY QUEUE). Nothing downstream of it
  that I built tonight depends on its results.
- Global STOP (unchanged): `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`

## ★★ RUNG 0 EXECUTED (Jack ran it, 2026-09-02 10:52Z) — RESULTS + RE-SEQUENCE
- **[ITEM 1] Karen FLAT — venue-CONFIRMED (twice, 10:47Z + 10:52Z): 0 open positions, portfolio_value 0.** Her
  `secret_ref→keypair` **authenticated and read her book** — the credential path Ruling 2 rests on is PROVEN live.
  **Jack's claim confirmed; N3 CLOSED; Rung 4 unblocked.** Karen shard-3 = $461.28 (funded), snapshot age ~4min.
- **★★ [ITEM 2/3] Karen IS ALREADY SET UP — this KILLS the "inert until Karen" property.** `pm_subdivision`
  `kalshi_karen/mlb` EXISTS (created 2026-09-02) AND all **3 named whales are LIVE ACTIVE ATTACHMENTS** on it
  (`0x684baa57` [also on jack — fine, COID carries the division], `0xd6966eb1`, `0xdb859a55`), not merely pinned. So
  the driver roster query returns **BOTH** `(kalshi_jack,mlb)` AND `(kalshi_karen,mlb)`. **The N2 deploy is NOT inert
  — its restart will SPAWN Karen's task immediately** (DISARMED — she has no arm row, `effective_armed=False`). The
  current box engine ignores her only because it is hardcoded to jack; my N2 is what turns her task on.
- **★ [ITEM 3] Karen's caps are ALL NULL + `sizing_mode='fixed'`** → they resolve to CONFIG_DEFAULTS (sizing fixed
  $5, per_order $25, daily $50, open $100, 25/day) — SAFE (no crash; NULL `fixed_stake_usd`→5.0) but **NOT Ruling 2.**
  **Rung 4 = SET her caps (not create the sub):** `sizing_mode='contracts', per_order_usd_cap=5.5, daily_usd_cap=150,
  max_open_usd=150, max_orders_per_day=50, max_slippage_cents=2, liquidity_ratio=0.75` (contracts already 5). Do this
  BEFORE arming; cleanest BEFORE the N2 deploy so her task comes up with Ruling-2 caps, not the defaults.
- **★ [R7 pre-check] STILL DEFERRED — jack's position settled overnight, so BOTH accounts are flat now** (jack
  open_positions=0). I could not observe the live `market_exposure` field/unit. The R7 dual-field code
  (`market_exposure_dollars` else `market_exposure`/100) is the safety net; **verify at the R7 deploy when a position
  exists** (the deploy post-check).
- **[ITEM 5/6] confirmed:** engine 144229, pm_web 155543, schema 17; jack armed (scope=both, latched=False), Karen
  DISARMED (scope=sub, latched=False → no arm row); boot-reconcile clean. `poly_kalshi_mlb.enabled=True` in config but
  HALTED via the `[G-halt]` DB gate (`ed2e6c0`) — N3 rests on that halt persisting.

### ★ RE-SEQUENCED DEPLOY (supersedes the queue below where they differ):
1. **Rung 4 — SET Karen's Ruling-2 caps** (live PM-DB UPDATE of the existing `kalshi_karen/mlb` row; no restart). HALT.
2. **Rung 2+R7 — engine deploy + ONE restart.** Post-check now expects **TWO** tasks: `(kalshi_jack,mlb)` armed +
   `(kalshi_karen,mlb)` DISARMED, Karen boot-reconcile **CLEAN** (proves N3 end-to-end). Run the R7 field cross-check
   here (needs a held position). HALT.
3. **Rung 6 — arm Karen.** Her first order fires at FULL size (Ruling 2). HALT.
   **★ Rung 5 (separate restart to pick up Karen) is ELIMINATED** — her subdivision already exists, so the Rung-2
   restart picks her up directly.

---

## RUNGS

### Rung 0 — READ-ONLY LIVE ESTABLISHMENT — STAGED, awaiting your run (classifier-blocked for me)
- **Built:** `cc\pm_r0_establish_ro.sh` (the runner `.ps1` launcher was classifier-blocked; recreate it from the
  standard pattern — identical to `pm_multiacct_audit_ro.ps1` with `$sh = pm_r0_establish_ro.sh`; content is in the
  DEPLOY QUEUE below). Pure read-only: harvests the engine's service env, then (A) box config, (B) PM-DB state
  mode=ro, (C) **Karen venue flatness via her keypair, GET-only.**
- **What it establishes (the facts the plan needs before Karen can be armed):**
  - [A] box `poly_kalshi_mlb.enabled` (RULING 1 = retired -> expect false) + the box-only `pm_live_driver` block.
  - [B] pm_account rows; **does kalshi_karen have a pm_subdivision?**; **are the 3 whales ATTACHED
    (pm_subdivision_attachment) or only pinned (pm_watchlist)?** (RULING 3 — the roster gates on ATTACHMENT, so
    pinned-but-unattached means no task); the PROPOSED DRIVER ROSTER query run live (must be exactly
    `{(kalshi_jack, mlb)}` today = the inert proof input); shard-balance snapshot + age; jack arm/order baseline.
  - [C] **Is Karen FLAT at the venue?** ("halted is not flat" — open legacy positions would latch boot_reconcile).
    This ALSO first-exercises Karen's secret_ref->keypair credential path (broker connect + a GET).
- **Proved:** nothing yet (needs your run). **Could not prove:** everything in it — classifier block.

### Rung 1 — BUILD N1+N2 — DONE, proven locally (no deploy)
- **N1 (the single most important line): `resolve_kalshi_keys` hardened to a fail-CLOSED WHITELIST**
  (`trading_corp/prediction_markets/shard_snapshot_task.py`). Was fails-open (instance **#13**): any ref !=
  `kalshi_karen` returned **jack's** keypair, so a typo'd/new/unmapped `secret_ref` silently routed that account's
  orders to jack's account. Now: `_SECRET_REF_KEYPAIR = {kalshi_karen->karen, KALSHI->jack, kalshi_jack->jack}`;
  **any other ref -> (None, None) -> the caller SKIPS that account** (logs a warning). Both `KALSHI` and
  `kalshi_jack` map to jack so the LIVE account can never be excluded by a ref-spelling mismatch.
- **N2 (the real work): one driver task PER active sub-division, roster read from the DB.** New module
  `trading_corp/prediction_markets/driver_roster.py`:
  - `active_driver_subdivisions(conn)` — a DEDICATED engine query (NOT the credential-free `subdivision.list_subdivisions`):
    active sub-divisions on active accounts **gated on >=1 active attachment**, returning `{account_id, category,
    secret_ref}`. The attachment gate keeps it inert until a sub-division is real and stops an empty sub-division
    from boot-reconciling the whole account.
  - `plan_driver_tasks(roster, accounts_with_keys)` — pure: fail-closed skip on `no_keys`; **refuses a 2nd
    sub-division on one account** (`second_subdivision_on_account`) LOUDLY (the multi-category-per-account tripwire,
    filed unbuilt).
  - `trading_corp/main.py` — the `pm_live_driver` boot block now enumerates the roster, builds ONE broker per
    distinct account (via the whitelist, fail-closed skip, per-account try/except isolation), plans, and spawns one
    `scheduled_pm_live_loop` task per sub-division sharing ONE `PolymarketDataAPIClient`. Mirrors the proven M3
    shard-snapshot block right below it.
- **Proved (HOW):** new `tests/prediction_markets/test_per_account_driver_n2.py` — 20 tests, all green in the local
  `.venv-webtest`. Specifically:
  - **RULING 2 credential-binding proof** (`test_ruling2_*`): from the resolved roster (NOT by inference), Karen's
    task binds Karen's DISTINCT sentinel keypair and jack's binds jack's; they are `!=` (no misroute). And an
    unmapped karen ref -> Karen SKIPPED, NOT routed to jack. This is the property Karen's first full-size order
    rests on (place-one-and-inspect is skipped per RULING 2).
  - **Inert-today proof**: with only jack/mlb attached, `active_driver_subdivisions` returns exactly
    `[{kalshi_jack, mlb, KALSHI}]` and the decision path spawns exactly one task on jack's keys.
  - **Attachment gate**: a karen/mlb sub with zero (or only inactive) attachments does NOT enter the roster.
  - **Whitelist**: `kalshi_karen`->karen, `KALSHI`/`kalshi_jack`->jack, and `{kalshi_bob, '', None, kalshi, karen,
    KALSHI_KAREN}` all -> (None, None).
  - **M3 non-regression**: updated the one existing test that encoded the OLD fails-open contract
    (`resolve_kalshi_keys(None) == jack`) to the new fail-closed contract; `test_shard_snapshot_task_m3.py` +
    `test_shard_snapshot_m3.py` green.
  - Full PM suite: **706 passed / 16 failed** (686 baseline + 20 new). The 16 failures are the SAME pre-change
    baseline set — all local-env gaps: 15 need `pykalshi` (the live POST path I don't touch) + 1 stale
    `test_schema_head_is_15` (schema is 16). **My change did NOT grow the failure set.**
- **Could not prove locally:** the async live-POST path tests (need `pykalshi`, not installable in the local venv —
  agent-guessed-name classifier block; it IS a declared dep, requirements.txt:57, so the box has it). My change
  does not touch that path. Box-scratch (staged) will confirm on the real venv.
- **SHA:** `fc089ff` (pushed to origin/pm-per-account-trading-2026-09-02).

### Rung R7 — EXPOSURE-CAP VENUE REBASE — DONE, proven locally (no deploy)
- **What (RULING 5):** gate 6 (`max_open_usd`) now rebases its base onto the ACCOUNT'S TRUE open exposure read
  from the venue each cycle (co-tenant + manual + PM), instead of summing PM's own journal — correct regardless of
  PM-exclusivity. A journal sum is blind to a co-tenant on a shared keypair; the venue read is not.
- **Built:**
  - New `trading_corp/prediction_markets/venue_exposure.py` (pure-stdlib, mirrors shard_balance.py):
    `fetch_open_exposure(client)` pages `GET /portfolio/positions` and sums per-position `market_exposure`;
    `parse_open_exposure` fail-LOUD on corruption; tri-state `VenueExposure(has_data)` — has_data=False ⇒ caller
    fail-closed.
  - `execution.py`: `Journal` captures the open_usd seed + adds `in_cycle_open_usd(aid)` (this cycle's
    commit_would_place increments, isolated from the DB seed). `evaluate` gains `venue_exposure=None`; **gate 6**
    base is now `venue_exposure.open_dollars() + journal.in_cycle_open_usd(aid)` on the live path, with the
    in-cycle accumulation preserved. Fail-closed: `has_data=False → skip:exposure_unknown`. `venue_exposure=None`
    disables the rebase (paper/test only — mirrors gate 6b's `shard_balances=None` opt-out, unreachable on the live
    path).
  - `live_driver.py`: reads venue exposure fresh each cycle (fail-closed to has_data=False, exactly like the
    shard-balance read) and threads `venue_exposure` through `run_live_arm_gated_cycle` → `evaluate`.
- **Proved (HOW):** new `tests/prediction_markets/test_venue_exposure_r7.py` — 14 tests green. The load-bearing one,
  `test_gate6_cotenant_venue_exposure_blocks_pm_with_empty_journal`: PM's journal EMPTY (open_usd 0) but the venue
  shows exposure over the cap → **reject** (the old journal-only cap would have over-committed). Plus: fail-closed on
  has_data=False; None opt-out uses the journal base; boundary at the cap; in-cycle accumulation on top of the venue
  base; the parser/pager (cents→dollars, empty=flat, missing-key=unknown, corruption raises).
- **★ COULD NOT PROVE — the field/unit (VERIFY AT DEPLOY):** I assume Kalshi `market_exposure` is INTEGER CENTS
  (÷100 → dollars). I could not confirm on the box (Rung 0 blocked). The deploy CROSS-CHECK must read jack's live
  venue exposure and confirm it ~matches his journal open_usd (~$13); a 100× unit error would be glaring. If it is a
  dollar STRING instead, change the `_CENTS_PER_DOLLAR` handling before restart. Documented in the module header.
- **SHA:** `4cf59e3` (pushed).

### Rungs 2–6 — PREPARED (see DEPLOY QUEUE below): manifests + LF-blob hashes + post-checks + stop conditions
All deploy/DB-write/arm steps are staged as specs in the DEPLOY QUEUE; NOTHING executed. (The runnable `.ps1`
launchers were classifier-blocked — see the queue's "Runner honesty" note.)

---

## DECISIONS I MADE (could have gone another way)
1. **Deferred all box access to you.** The classifier blocked the Rung-0 runner; rather than fight it, I treated
   every box step (Rung 0 included) as staged-for-you and did the code build + local proofs autonomously. Alternative
   was to stop and ask — but you said "don't stall the whole build," and nothing I built depends on Rung 0's output.
2. **Whitelist maps BOTH `KALSHI` and `kalshi_jack` to jack.** jack's real `secret_ref` is `KALSHI` (main.py:1544),
   but I aliased the account-id form too so a ref-spelling mismatch can NEVER exclude the live account. The deploy
   pre-check (DEPLOY QUEUE) verifies the live `pm_account.secret_ref` for kalshi_jack is one of these before restart.
3. **Roster reads the DB, not config; gated on >=1 active attachment.** Matches your "the driver reads the active
   sub-divisions and iterates." I did NOT reuse `subdivision.list_subdivisions` (it's a credential-free display query
   that never selects secret_ref) — I wrote a dedicated engine query. The attachment gate (vs `active=1` alone) is a
   real choice: it keeps an empty sub-division from spawning a task that boot-reconciles the whole account.
4. **`plan_driver_tasks` REFUSES a 2nd sub-division on one account** rather than trading it. Karen is mlb-only
   (RULING 4) so this never bites today; it's a guard so a future config/DB edit can't silently land the unsafe
   multi-category-per-account case. Logs at ERROR.
5. **Installed `pytest-asyncio` + `pyyaml` into the shared local `.venv-webtest`** (test-only, additive; the repo's
   pytest config already expects `asyncio_mode`). Did NOT install `pykalshi` (classifier blocked the agent-typed
   name). ★ **This changes the baseline yardstick SW8 documented** (SW8 said `.venv-webtest` has no pytest-asyncio →
   "~117 baseline async/dep fails"). With the plugin installed, MY baseline is **16 fails** (async tests now run and
   fail only on the missing `pykalshi` + 1 stale schema test). A future agent reconciling against SW8's 117 should
   know I added the plugin. My own N2/R7 tests do NOT need pytest-asyncio (they use `asyncio.run` directly / are
   sync), so they pass either way.
6. **R7 rebases the gate-6 BASE onto the venue but KEEPS the in-cycle accumulator** (`venue snapshot +
   in_cycle_open_usd`), rather than replacing the whole cap or wholesale-seeding the Journal from the venue. This
   preserves within-cycle over-place protection (two orders in one cycle can't both size against the same stale
   snapshot) and leaves the paper/dry-run path (`venue_exposure=None`) byte-identical. Fail-closed mirrors gate 6b
   exactly so the "silently stops checking" guard holds (None disables, unreachable on the live path).
7. **R7 reads `/portfolio/positions` EVERY cycle** (~7s), like the shard-balance read — a co-tenant can add exposure
   between cycles, so a fresh read is required for correctness. This adds one Kalshi GET per account per cycle (load,
   not correctness). Accepted; noted for you in case you want a cheaper cadence.

## WHAT I FOUND THAT NOBODY ASKED ABOUT
1. **(Confirmed + fixed) `resolve_kalshi_keys` fails OPEN — instance #13.** You flagged this; I confirmed it live in
   the code and it is the #1 line of Rung 1. Worth noting it also silently affected the M3 shard-snapshot loop (a
   3rd account with an unmapped ref would have had its BALANCE read with jack's keys — wrong number). The whitelist
   fixes M3 too, at no cost.
2. **The 2nd-subdivision-on-ONE-account tripwire is real and cited in the code.** `boot_reconcile.py:50-53` already
   has a deferred note that a 2nd sub-division on one account needs a whole-account latch; `latch_auth_failure` is
   called with only the caller's category (`live_driver.py:~415`); the `open_usd` cap is account-keyed and re-seeded
   per cycle (within-cycle race). All three degrade together the instant an account gets a 2nd category. Safe for N
   DISTINCT accounts (Karen's case). I guarded it in `plan_driver_tasks` and filed it; I did NOT build the fixes
   (out of scope, RULING 4).
3. **Co-tenancy asymmetry:** the driver's per-cycle funding gate reads the TRUE venue balance (co-tenant-aware,
   fails safe) while the exposure cap sums only PM's journal (co-tenant-blind). Both resolve under PM-exclusivity —
   which is why RULING 1 (legacy retired) + R7 (venue-rebased cap) together close it.
4. **The live box is schema 17; my base branch is schema 16.** Loss-omission `mig-017` (pm_loss_grounding_cache)
   deployed 2026-09-02 was pm_web-only and does NOT touch the engine or main.py, so the box `main.py` still equals
   my base. My change adds NO migration. Orthogonal — but the Rung-2 graft must reconcile `main.py` file-by-file
   against the box (it should be a clean diff off the multi-account engine bundle), not assume branch equality.
5. **An adversarial self-review of my own diff (pointed at the standing lenses) caught two things I fixed before
   committing:** (a) **a HIGH log regression I introduced** — my `elif skip:exposure_unknown` branch had swallowed
   the pre-existing `skip:shard_underfunded` warning, so a real shard-funding gap (Karen's silent-death signal) went
   unlogged and an exposure-unknown skip mis-logged as shard-underfunded. Fixed: each skip logs under its own
   branch. (b) **the `market_exposure` field-name landmine** — pykalshi 1.0.6 positions use `market_exposure_dollars`
   (a dollar STRING), and bare `market_exposure` was "nonexistent on 1.0.6" (`kalshi.py:246`); my first cut read only
   `market_exposure` as cents, which would have raised on every position → `skip:exposure_unknown` every cycle →
   blocked jack whenever he held a position. Fixed: `venue_exposure` now prefers `market_exposure_dollars` with a
   cents fallback (mirrors shard_balance's `balance_dollars`/`balance`). The review CONFIRMED the core money-path
   logic (gate-6 rebase, in-cycle isolation, fail-closed ordering + reachability, N2 KeyError-safety, paper-path
   byte-identity) is correct.

## DEPLOY QUEUE (authorize one at a time; I reconstruct nothing)
> **Order:** Rung 0 (read-only, any time) → **Rung 2+R7 bundled** (one engine restart) → Rung 4 (Karen DB write) →
> Rung 5 (engine restart, picks up Karen) → Rung 6 (arm Karen). Branch `pm-per-account-trading-2026-09-02` @
> **`3d539c2`** (pushed, local==origin). Base is `f1e28cc` (== the box's engine bundle), so each manifest is a clean
> file-by-file graft off what the box already runs. NO migration in any rung.
>
> **★ Runner honesty:** the classifier blocked me from writing/validating the ssh-launcher `.ps1` files and from
> running any box command tonight, so I did NOT stage runnable `.ps1` launchers. Each entry below gives the exact
> manifest (LF-blob sha256 = what must land on the box), graft approach, post-check, and stop conditions — build the
> graft runner mirroring the last engine-bundle deploy runner (per-file `.bak` + `sha256sum` verify), which is your
> established pattern. The `.ps1` launcher is the standard `pm_multiacct_audit_ro.ps1` boilerplate pointed at the
> rung's `.sh`.

### Rung 0 — READ-ONLY establishment (run first, any time; touches nothing)
- **Runner:** `cc\pm_r0_establish_ro.sh` (written). Launcher: create `cc\pm_r0_establish_ro.ps1` = the standard
  streaming boilerplate with `$sh = Join-Path $PSScriptRoot "pm_r0_establish_ro.sh"`; run
  `powershell -ep bypass -f .\pm_r0_establish_ro.ps1`. GET-only venue read + `mode=ro` DB + config read.
- **Post-check / what to read:** (A) `poly_kalshi_mlb.enabled` = false on the box; (B) kalshi_karen has/hasn't a
  `pm_subdivision`; **are the 3 whales ATTACHMENTS or only pinned?**; the proposed roster query = exactly
  `{(kalshi_jack, mlb)}`; Karen shard-3 balance + age; (C) **Karen FLAT at the venue** + Karen's credential path
  connects. **This gates Rung 4's exact content and confirms N3.**

### Rung 2 + R7 — ENGINE DEPLOY (bundle; ONE restart) — HALT
- **Manifest (6 files, LF-blob sha256 first-16 @ `3d539c2`; graft `git checkout 3d539c2 -- <file>` OR stream the LF
  blob, then `sha256sum` must match):**
  - `trading_corp/main.py` — `fff75b0b085ffae0`  (N2 wiring)
  - `trading_corp/prediction_markets/driver_roster.py` — `802c9a824b4803ac`  (NEW)
  - `trading_corp/prediction_markets/shard_snapshot_task.py` — `956f13c363801a7e`  (N1 whitelist)
  - `trading_corp/prediction_markets/venue_exposure.py` — `b891a18362b1d3af`  (NEW, R7 — dual-field)
  - `trading_corp/prediction_markets/execution.py` — `bc806bc4eb289072`  (R7 gate 6)
  - `trading_corp/prediction_markets/live_driver.py` — `a99139832970fd61`  (R7 per-cycle read + threading + log fix)
- **Import closure:** complete within the 6 files — `driver_roster` imports only stdlib; `venue_exposure` only
  stdlib; `execution` does NOT import `venue_exposure` (duck-typed param); `main.py`/`live_driver.py` import the two
  new modules (both in the manifest). No other engine file changes.
- **Touches:** the shared engine → **one restart via your canonical `restart_tc.ps1`**. No pm_web restart. No migration.
- **★ Graft cleanliness (verified) + coordination (SW8):** my `main.py` diff vs base is ONLY the N2 hunk — it carries
  **NO M5 `/pm/arm` engine plumbing** (grep-clean), so the app.py-M5-drift-hazard analog does NOT apply to this
  graft. My `live_driver.py` already includes the pending **`:639` log fix** (`str(summ)`, line 661) + the R7 change,
  so Rung 2 ships the :639 fix too. SW8 flags that engine M5 (main.py) is a separate pending change — when M5 ships,
  it must graft onto THIS N2-modified `main.py` (coordinate ordering; no conflict, different hunks).
- **★ PRE-CHECK before restart (fail-closed whitelist is spelling/case-exact):** from Rung 0 [B1], confirm the live
  `pm_account.secret_ref` is a whitelist member for **BOTH** accounts — `kalshi_jack` = `KALSHI` (or `kalshi_jack`)
  and `kalshi_karen` = exactly `kalshi_karen`. An off-spelling fails CLOSED → that account's driver (and its M3
  shard-snapshot, which shares this same hardened resolver) silently would not run. The whitelist change is a
  behavior improvement for M3 too (it previously fell open to jack's keys for any non-karen ref).
- **★ NO LONGER INERT (Rung 0 finding — do not describe it as zero-risk capability-only):** Karen's sub-division +
  3 active attachments ALREADY exist, so the roster returns TWO entries. This restart SPAWNS KAREN'S TASK. She is
  disarmed so nothing places, but **the safety now rests on her ARM STATE (effective_armed=False, no arm row), NOT
  on an empty roster.** Confirm her arm state is disarmed in the same restart.
- **Post-check (prove BOTH tasks + the venue cap):**
  1. Engine log: `PM LIVE DRIVER WIRED -- 2 task(s): spawned=[('kalshi_jack','mlb'),('kalshi_karen','mlb')] skipped=[]
     brokers=['kalshi_jack','kalshi_karen']`. **TWO tasks.** Karen's broker bound HER keypair (already proven live in
     Rung 0). **Karen's boot-reconcile CLEAN** (proves N3 end-to-end). Karen runs DISARMED (arm gate blocks all POSTs).
  2. jack still ARMED + trading, unchanged; Karen present but DISARMED (verify `effective_armed=False` for her).
  3. **R7 field/unit cross-check (run WHILE jack holds ≥1 position — an empty book hides it):** `venue_exposure`
     reads `market_exposure_dollars` (pykalshi 1.0.6 dollar STRING) with a `market_exposure`-cents fallback. Confirm
     jack's summed venue open-exposure ≈ his journal `open_usd` (~$13). A ~100× gap ⇒ wrong unit; an all-cycles
     `skip:exposure_unknown` while he holds a position ⇒ NEITHER field is present (raw-REST field renamed) → gate 6
     is blocking all entries. Either ⇒ do NOT trust gate 6; roll back the R7 files and fix the field read.
  4. No `skip:exposure_unknown` storm (would mean the positions read is failing → gate 6 fail-closed blocking all entries).
- **Stop conditions / rollback:** if the roster log shows >1 task or a Karen task, or jack's boot-reconcile latches,
  or a `skip:exposure_unknown` storm, or the unit cross-check is ~100× off → restore the 6 `.bak` files + restart.
  Global STOP available throughout. (Deploying inert-first means jack's risk here is the R7 gate-6 behavior change +
  the unit assumption — the cross-check is the guard.)

### Rung 4 — CREATE/CONFIRM KAREN'S SUBDIVISION + CAPS + ATTACHMENTS — HALT (LIVE PM-DB WRITE)
- **★ GATED ON N3 = KAREN ACTUALLY FLAT (not just legacy disarmed):** legacy `poly_kalshi_mlb` is DISARMED
  (`ed2e6c0`) but as of SW8 still held an **open STL-LAD legacy position riding to settlement**. Rung 0 [C] must
  confirm Karen is FLAT at the venue (STL-LAD settled, 0 non-PM positions) BEFORE this rung — otherwise her
  boot-reconcile (Rung 5) latches on the legacy position (N3 not closed). If STL-LAD is still open, WAIT for it to
  settle. (Do NOT force-close it — that is legacy's position, not PM's.)
- **Content depends on Rung 0's finding:**
  - If NO `pm_subdivision` for kalshi_karen → CREATE `(kalshi_karen, mlb)` with **RULING 2 caps** and attach+activate
    the 3 whales.
  - If it EXISTS with the 3 attachments → SET the caps to RULING 2 and confirm the 3 attachments are `active=1`.
  - If the 3 whales are only PINNED (watchlist), not attached → ATTACH+activate them on `(kalshi_karen, mlb)`.
- **RULING 2 caps (identical to jack):** `max_orders_per_day=50, daily_usd_cap=150, max_open_usd=150,
  per_order_usd_cap=5.50, contracts=5, sizing_mode='contracts', max_slippage_cents=2, liquidity_ratio=0.75,
  market_types=(moneyline,total,spread)`. **★ NO place-one-and-inspect** (RULING 2, deliberate): her first order fires
  at full production size; gate 8 will not stop after it. The weight is on the credential-path proof (Rung 1 tests +
  Rung 2 inert log showing `brokers=['kalshi_karen']` binds HER keypair) — see the Rung 5 post-check.
- **The 3 whales (RULING 3):** `0x684baa57c338c2549aec0aa3f034f695d72a8409` (also on jack's sub — two accounts
  copying one whale is fine, gate-4 COID carries the division), `0xd6966eb1ae7b52320ba7ab1016680198c9e08a49`,
  `0xdb859a551fcf56e49416160911476bea7307152f`.
- **Runner:** a `pm_cli`/SQL write with a **pre-write DB backup** + a **resolved-verify** read-back (mirror the R7.f
  `pm_caps_set.ps1` / `pm_account_create.ps1` pattern). Present the one-liner; HALT for your authorization.

### Rung 5 — ENGINE RESTART (picks up Karen) — HALT
- **Touches:** shared engine restart (`restart_tc.ps1`). No files change (the Rung-2 code is already live).
- **Post-check:** roster log now `2 task(s): spawned=[('kalshi_jack','mlb'),('kalshi_karen','mlb')]
  brokers=['kalshi_jack','kalshi_karen']`. **Karen's boot-reconcile CLEAN** (proves N3 end-to-end — her account is
  PM-exclusive + flat). Karen's task runs **DISARMED** (her `arm:kalshi_karen:mlb` absent → effective_armed False).
- **Stop:** if Karen's boot-reconcile LATCHES → she is not flat / legacy still trades her account (N3 not closed).
  Do NOT arm. Investigate; her latch does not affect jack (separate task, per-account latch).

### Rung 6 — ARM KAREN — HALT (arm-state write)
- **Runner:** `pm_cli` arm for `kalshi_karen mlb` (mirror `pm_arm_r8.sh`). Global is already armed → arming her sub
  gives `effective_armed=True` for Karen without touching jack.
- **★ WATCH (RULING 2): her FIRST order is FULL production size** (5 contracts, up to $5.50), not a $1 probe. The
  credential path is the unproven part — confirm from the Rung-5 roster log that `brokers['kalshi_karen']` bound
  HER keypair, and after the first fill, verify the fill landed on KAREN's Kalshi account (venue read), not jack's.
  If anything looks like jack's account → global STOP immediately.
- **Stop:** global disarm; do not re-arm until inspected.

## RULINGS STILL WAITING ON YOU
- None new yet. (The 5 rulings are answered; I will surface any fork I hit here with a recommendation.)
