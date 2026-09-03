# MULTICATEGORY BUILD — kalshi_jack/ufc (2026-09-02). Handoff, kept current per rung.

**Read this instead of scrollback.** Plan of record: `MULTICATEGORY_PLAN_2026-09-02.md @ e5d6506`. Branch
`pm-multicategory-2026-09-02` (worktree `C:\Users\AA Incorporado\cc-pm-multicat-wt`), base `e5d6506` (per-account
tip). Autonomous for build/test/box-scratch/review/commit/push/read-only runners; HALT for deploy, restart, live DB
write, arm, prod-live advance, or a ruling that is Jack's.

## ★★★ OPTION C — PROVEN IN PRODUCTION 2026-09-03 12:34Z (read-only probe `cc\pm_optionc_fire_ro.{ps1,sh}`)
At SW10 wrap `PLACED_SINCE_RESTART=0`; the open question was whether the restructured ONE-task-per-account loop would
place live. **IT DID.** Both armed accounts placed real, filled orders since the 23:10:02Z restart:
- **kalshi_jack PLACED_SINCE_RESTART=4** (ids 116 SFPIT-SF y@0.56, 117 STLLAD-LAD2 spread y@0.54, 119 CWSHOU-CWS
  y@0.38, 138 MILCHC-9 total no@0.51). **kalshi_karen PLACED_SINCE_RESTART=6** (ids 118/120/127/135/136/137).
  Every placement: 5 contracts, `outcome_status=filled`, `is_exit=0`, sane price, on ITS OWN account. **DB
  placement count == cycle-log `placed>=1` count EXACTLY (jack 4, karen 6).** Cross-account: NONE — jack's rows on
  kalshi_jack, karen's on kalshi_karen; the one same-ticker/same-time pair (id 117 jack + 118 karen, STLLAD-LAD2
  spread y@0.54 @23:57:14Z) is the two-accounts-copy-the-same-whale pattern (COID-division), not a misroute.
- **Row accounting reconciles:** 23 new rows (id 116..138) = 10 placements + 13 settlement rows (jack 6 / karen 7).
- **Overnight settlements booked cleanly by R-d** (periodic settlement-scan, 8 batches): all 6 wrap-open legs +
  the new entries settled per-wallet with correct signs (won->+px1.0/+pnl, lost->px0.0/-cost). Examples: MILCHC
  total-9 won +2.3565; TORCLE-CLE lost -2.3435; SFPIT-SF won +2.114. `SETTLEMENTS_SINCE_RESTART` jack 6 / karen 7.
- **Boot-reconcile stayed clean:** only the 23:14:42Z boot lines (both `reconciled=True latched=False
  latched_categories=()`); NO new boot-reconcile overnight (consistent with no restart).
- **Engine did NOT bounce overnight (the unasked check):** MainPID=171106, NRestarts=0, ExecMainStart=2026-09-02
  23:10:02Z, ActiveState=active — all byte-identical to wrap. pmweb 170400 unchanged.
- **Arm (persisted rows, NOT a status call):** global/jack/karen all `armed=True latched=False`, ts byte-identical
  to the SW10 baseline. Schema 17. Four PM crons intact (paper-poll */30, refresh 05:00, adjudicate 05:40, rollup
  05:50 UTC). Shards fresh (age 2min): jack $496.11 (sh0 $0.0081 STILL unfunded), karen $459.63 (sh0 $25.01).
- **Open now:** jack 1 leg (MILCHC-9 total no x5), karen 3 legs (BOSBAL-BOS y, TBTEX-TEX y, MILCHC-9 total no) — all
  placed today, within cap.

**WRAP-TIME SILENCE EXPLAINED (market conditions, not a restructure fault):** the OPPOSING-PAIR guard re-detected ONE
contested cid `0x0f589076…aad1` on kalshi_jack ~1816x from 23:10 to **~03:16Z** (`opposed_closes=0` each time: it
wanted to FLAT a held position but both sides skipped -> re-flagged every ~8s cycle until the underlying MLB game
settled ~03:16Z, after which it STOPPED). It did NOT starve placement (jack placed 116/117/119 during that window).
Every PM cycle `errors=0`, `n_reject=0`, `ceiling_latched=False`; no auth-failure/latch events. `skip:exposure_unknown`
=8 and `skip:shard_underfunded`=17 over 13h (low, fail-closed, expected); SUSTAINED SHARD UNDERFUNDING alarm=0.
**Verdict: Option C is proven live for the mlb-only (invisible) path — the deploy's actual claim.** The joint
cross-category cap is still only *exercisable* once a 2nd category exists, but the loop that carries it now places
correctly in production.

**★ ONE ANOMALY TO FLAG (non-blocking, not new):** the OPPOSING-PAIR guard logging "NEWLY-contested" for the SAME
cid ~1816x until settlement is a non-convergence log smell ("NEWLY" implies once). It resolved via settlement and
wrongly closed nothing (opposed_closes=0), but it belongs to the "a safety check keeps checking but never resolves"
family -> candidate for a later look, do NOT chase now. (Also: 3 tracebacks over 13h — PM cycles show errors=0
throughout, so non-PM; matches SW10's Telegram/playwright/earnings/non-PM-kalshi classes. Confirm-if-asked.)

## ★★ LIVE STATE — NOTHING THIS BUILD TOUCHES THE BOX
- Two accounts ARMED + TRADING throughout: **kalshi_jack/mlb + kalshi_karen/mlb**, one engine, nobody monitoring.
  STOP (kills both): `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`.
- I build ONLY in this local worktree + local venv (+ authorized box-scratch/read-only runners). **Zero** box
  deploys/restarts/DB-writes/arms. Last known engine PID **163519** (SW9). The only box touch this session is the
  board-approved read-only `pm_shards_ro.ps1` (16:47Z: jack shard0=$0.0081, karen shard0=$25.01). Box code = the
  per-account tip; my branch does not reach it until a deploy (HALT-for-Jack).

## ★ ORDER OF WORK (Jack's ruling)
A. Safety restructure FIRST (Option C + M1/M2/M3 + re-scopings) — touches live code, ships INDEPENDENTLY + INERTLY
   (mlb-only must be byte-identical to today). B. UFC matcher (greenfield, no live path until a ufc sub exists).
   C. Caps mechanism (HALT — Jack's ruling un-struck). D. Attach + arm (Jack's, not tonight).

## ★ CAPS MECHANISM RULING IS OPEN
Jack left the `<<<JACK — RULE HERE>>>` block un-struck. Recommendation of record: the ACCOUNT-LEVEL AGGREGATE CAP
(race-free under Option C's shared Journal; holds $150/day + 50 orders exactly; headroom flows to the busy
category) over the 75/75 divide (holds the ceiling but strands headroom on a quiet night). Aggregate stays
$150/50 either way. **C is built only after his strike; A and B do not need it.**

---

## RUNG A0 — THE THIRD ADVERSARIAL PASS (the stop gate) — DONE, does NOT trip the stop condition

**Method:** enumerated every variable in `live_driver.scheduled_pm_live_loop` (lines 481-682) that is either
initialized ONCE before the `while` (task-level, persists across cycles) or shared across the per-category work in
a cycle, and classified each: SAFETY (a check that would silently stop checking) vs FUNCTIONAL (needed to iterate
categories, but fails SAFE) vs CORRECT-TO-SHARE (an account-level resource).

| State (live_driver.py) | Scope today | Under one task, N categories | Class |
|---|---|---|---|
| `broker`/`client` (489) | per account | shared — one keypair/account | **CORRECT to share** |
| `shard_bal` (539,552) | per cycle | account-wide (all shards), read once | **CORRECT to share** |
| `venue_exp` (542,563) | per cycle | account-wide exposure, read once — M1's whole point | **CORRECT to share** |
| `journal` (585) | per cycle | account-keyed; shared enforces the account open-cap across categories | **CORRECT to share (M1 fix)** |
| `consec_err` (in run_live_arm_gated_cycle:367) | per-CALL | run_..._cycle is called once PER CATEGORY → own local each call | **#15 defended BY CONSTRUCTION (not shared)** |
| `consec_underfunded` (528) | task-level, cross-cycle | shared → an mlb placement resets ufc's starvation alarm | **#14 SAFETY → per-category dict** |
| `prior_snapshots` (533) | task-level, keyed by wallet | a wallet in both cats merges → wrong exits | **#16 SAFETY → key (category, wallet)** |
| `ctx` + `last_idx` (490,491) | task-level, MLB catalog | mlb vs ufc need DIFFERENT series catalogs | **#17 FUNCTIONAL (wrong catalog → skip:no_quote, fail-SAFE) → per-category** |
| `last_settle` (496,513) | task-level throttle | shared throttle → one cat's scan resets the other's cadence | **#18 FUNCTIONAL (delayed booking, fail-SAFE) → per-category** |
| `cycles` (527) | loop counter | harmless | share fine |

**RESULT — the stop condition does NOT trip.** The SAFETY re-scoping set (a safety check silently stopping) is
**TWO**: #14 (`consec_underfunded`) and #16 (`prior_snapshots`). **#15 (`consec_err`) is defended by construction**
— it is a local inside `run_live_arm_gated_cycle`, which is invoked once per category per cycle, so it never shares
across categories as long as the loop calls it per-category (which Option C does). So the safety set is smaller
than the three named, not larger.

**★ SAID PLAINLY (Jack asked):** the pass DID find two MORE per-category items — but they are **FUNCTIONAL, not
safety**: `ctx`/`last_idx` (the market catalog, which MUST be per-category so a ufc signal is matched against the
ufc series, not mlb) and `last_settle` (the settlement-scan throttle). Both FAIL SAFE if wrongly shared (a
ufc-vs-mlb catalog mismatch yields `skip:no_quote`/no-match → NO order; a shared throttle just delays a settled
position's booking). They are the mechanics of iterating categories — part of building the loop — NOT a hidden
fourth set of silent-safety degradations. So the *shape of the build is unchanged*: the safety re-scoping problem
is two items; the loop-mechanics re-scoping is two more, both fail-safe. I proceed; flagged for visibility.

**Convergence rule (Jack's "treat the list as OPEN"):** B6 re-runs this pass AFTER the restructure is written, as a
fresh adversarial read, and it must find NOTHING new (safety or functional) before M4. If B6 finds a further
SAFETY item, that would make the safety set > 3 → report and STOP.

---

## RUNG A/M2 — auth-latch all account categories — DONE @ `e2db6fd`, proven
- `live_driver.account_active_categories(conn, account_id, fallback_category=)` — every active category on the
  account from pm_subdivision, fail-SAFE to [fallback] (never [] / never fail-open). Auth-failure call site now
  passes the whole list to `arm.latch_auth_failure` (which already loops). No-op for one category/account.
- PROVED (local `.venv-webtest`, no pykalshi needed): `test_auth_failure_latches_ALL_account_categories` (a 403 on
  mlb's cycle latches BOTH mlb+ufc, both `manual_exit_required`), `test_account_active_categories_failsafe_and_union`
  (missing table -> [fallback]; active-only + fallback union). Existing single-account auth test still green.

## RUNG A/M3 — account-wide boot-reconcile latch — DONE @ `3e02c5c`, proven
- The comparison was ALREADY account-wide; only the latch was per-category (the deferred R-f note, now built).
  `reconcile_account(..., latch_categories=None)` loops the list on mismatch/read-fail (None -> [category],
  backward-compatible); `run_boot_reconcile` passes `account_active_categories(...)`. `ReconcileResult.
  latched_categories` makes the scope visible.
- PROVED: 4 unit tests (whole-account mismatch + read-failure latch ALL; clean two-category = NO false-latch, since
  a co-category position is in BOTH journal+book -> MATCH; default single-category preserved) + a run_boot_reconcile
  integration test. boot_reconcile suite 26/26; r7c boot/auth/helper 8/8.

## RUNG A/M1 — Option C one task per account + re-scopings — DONE @ `5d104a3`, proven locally
- `scheduled_pm_live_loop` is now per-ACCOUNT (`categories=[...]`; legacy `category=` still accepted). Per cycle:
  ONE account-level shard + venue-exposure read + ONE account-keyed `Journal`, SHARED across categories -> gate 6
  (open_usd, account-keyed) enforces the account cap JOINTLY (category B's evaluate sees category A's in-cycle
  commit) with NO lock, NOTHING on the order hot path; sequential categories never POST concurrently.
- Re-scopings: per-category ctx/last_idx (#17) via an INJECTABLE `CATEGORY_CTX_BUILDERS` seam (mlb registered; ufc
  = B); per-category `last_settle` (#18); per-category `consec_underfunded` ALARM (#14); `prior_snapshots` keyed
  `(category, wallet)` (#16). #15 `consec_err` is per-call -> defended by construction (confirmed in code).
- Boot: per-category settlement scan + ONE account-wide boot-reconcile force-latching ALL categories.
- `main.py` groups the guard-approved spawn BY ACCOUNT and passes `categories`. Guard (`plan_driver_tasks`) UNCHANGED
  -> today at most one category/account -> ONE task with `categories=[cat]` == byte-identical to the old wiring.
- PROVED (local `.venv-webtest`, no pykalshi -- via the injectable ctx builder + `_prior_snapshots` seam):
  `test_m1_shared_journal_caps_account_open_across_categories` (ufc capped by mlb's in-cycle open on a SHARED
  Journal; + the two-Journal RACE demo -> both place -> account over the cap); `test_m1_loop_mlb_only_..._both_param_
  forms` (byte-identical mlb-only, disarmed, 0 orders); `test_m1_prior_snapshots_keyed_by_category_wallet` (#16);
  `test_m1_underfunded_alarm_is_per_category` (#14, mlb AND ufc each alarm). Full PM suite: **16 env-gap failures
  unchanged** (pykalshi live-path + stale schema_head_is_15); every new test passes.
- ★ REMAINING PROOF (deploy gate, NOT local): byte-identical mlb-only on the REAL venv = the pykalshi-path
  scheduled_loop / kill_switch / shard_gate tests still green under box-scratch after the restructure. They cannot
  run locally (no pykalshi). Run box-scratch on the A+B bundle before the deploy queue.

## RUNG A-proof — byte-identical mlb-only — ★ PROVEN ON THE REAL VENV (box-scratch, 2026-09-02 ~22:0xZ)
Read-only box-scratch (rsync the live tree to `~/pm_multicat_scratch_*`, overlay this branch's files, run the PM
suite on the box venv with `-p no:pytest_ethereum`; **live engine PID 163519 NRestarts=0 active — UNTOUCHED**;
scratch dirs cleaned up after). Runners: `cc\pm_scratch_a{,2,3,4}.{sh,ps1}`.
- **The pykalshi-path tests that never run locally PASS on the box** with Option C: `test_live_driver_r7c` (incl. the
  scheduled_loop tests + my new M1/M2), `test_kill_switch_r7d`, `test_boot_reconcile_r55` (incl. M3),
  `test_ufc_match`, `test_venue_exposure_r7`, `test_optiond_r1`, `test_idempotency_r7h`, `test_disarm_r7i`,
  `test_arm_r5`, `test_per_account_driver_n2`, etc. — all green. (The box's own `tests/` dir is stale/partial, so a3
  replaced `tests/prediction_markets/` with THIS branch's via `git archive`.)
- **Only 4 failures, ALL pre-existing / not-my-change (classified, evidence not assertion):**
  - `test_search_r1::test_schema_head_is_15` + `test_shard_snapshot_m3::…head_is_16` — HARDCODED stale schema
    constants; live `SCHEMA_HEAD=17, n_migrations=17` (moved 15→16 multi-acct →17 loss-omission). Just stale.
  - `test_shard_gate_r2::test_driver_places_when_market_shard_funded` + `::…sustained_underfunding_alarm…` — their
    `FakeClient` RAISES on `/portfolio/positions` (never mocked for R7's venue read, which shipped at my BASE
    e5d6506) → gate 6 fails-closed `exposure_unknown` before gate 6b. **a4 PROVED these fail IDENTICALLY on the box's
    un-overlaid e5d6506 `live_driver` (same old line numbers 511/565/382)** → pre-existing stale fixture, NOT my
    regression. Production venue-read behavior is correctly covered by `test_venue_exposure_r7` (green).
- **Verdict: on the real venv Option C is byte-identical for the mlb-only path.** Not a real finding → proceed to
  prepare the deploy.

## RUNG B(core) — ufc_poly_kalshi_match.py — DONE @ `07c65a2`, 43 tests green (built by a Sonnet agent, reviewed)
- Pure/stdlib matcher, 2 binary types: moneyline `KXUFCFIGHT-{YYMONDD}{K1}{K2}-{FTR}` + go-the-distance
  `KXUFCDISTANCE-{...}-DIST`. Real tickers/slugs probed from the Kalshi public API + Polymarket (2026-09-02).
- The JOIN (honest crux the agent surfaced): the **Polymarket slug codes are OPAQUE** (`dan6`, `salpar`) and do NOT
  map to the Kalshi kcodes, so the match is driven by the Poly **outcome (fighter FULL NAME)** vs the Kalshi market
  **`title`** (`"{Full Name} wins"`). kcode = `upper(last_name[:3])` (first-name fallback when last<3). Exact match
  only; carry (ticker, leg) on MatchResult; MISS on ambiguity.
- KNOWN unresolvable cases as MISS tests (Jack's "show what it can't resolve"): 3-char abbrev COLLISION (two same
  first-3-last-name fighters on one card -- kcode ambiguous; SYNTHETIC test, agent could not find a real same-card
  collision), no-distance-ticker-for-bout, ambiguous-date-without-a-fighter-hint, opaque-Poly-slug.
- ★★ INTEGRATION GAP found in REVIEW (this is rung B2, NOT built -- a careful live-code change, deliberately not
  rushed at context depth):
  1. **`title` is not on the live path.** `build_kalshi_fight_index` reads `mkt.get("title")`, but
     `live_driver._market_quote_dict` carries only quotes+exchange_index, NO title. B2 must add `title` to the UFC
     ctx builder's market dicts (additive; MLB ignores it).
  2. **UFC needs its own MarketContext shape.** `execution.MarketContext` is MLB-shaped (moneyline/total/spread
     indices). UFC needs a fight index + distance index. B2 introduces a per-category context + a uniform matcher
     adapter.
  3. **evaluate must category-dispatch.** Today `evaluate` hardwires `M.parse_poly_mlb_bet` / `M.match_bet(...)`.
     B2 adds a registry `{"mlb": mlb_adapter, "ufc": ufc_adapter}` where each adapter exposes `parse(slug,outcome)`
     + `match(parsed, ctx, allowed_market_types) -> MatchResult` with the uniform fields evaluate reads
     (`.status/.kalshi_ticker/.leg/.market_type/.reason`). evaluate picks by `sub.category`; gates/sizing stay
     category-agnostic. The ufc ctx builder registers into `CATEGORY_CTX_BUILDERS` (the M1 seam) and builds the
     fight+distance indices carrying `title`.
  B2 is INERT (no ufc sub-division exists) but it DOES touch `execution.py` (the chokepoint) + `live_driver.py`, so
  it needs the same care + box-scratch as A. Recommended: build B2, then run ONE box-scratch validating A+B on the
  real venv (the byte-identical mlb-only gate + a disarmed ufc dry-run against live UFC market data).

## RUNG B2 — FIRST STEP DONE (live MarketContext probe) 2026-09-03; design proposed; evaluate edit AWAITS Jack's OK
**Read-only probe `cc\pm_ufc_shape_probe_ro.{ps1,sh}`** (public `/markets` GET + pykalshi `MarketModel` introspection +
`model_validate` of a REAL raw market -> the exact live-path transform). Live at 15:05Z: KXUFCFIGHT 38 open / 50
settled, KXUFCDISTANCE 14 open / 50 settled (host `api.elections.kalshi.com`, HTTP200 unauth). pykalshi **1.0.6**,
`MarketModel` 47 fields, `model_config.extra='ignore'`.

THE WHOLE FIELD SET the UFC ctx builder needs, BOTH sources (SDK object via model_validate vs RAW /markets):
- **`title` -- ON THE SDK OBJECT** (`MarketModel.title: str|None`, NOT deprecated, survives model_validate).
  KXUFCFIGHT `title='{Full Name} wins'` (e.g. 'Quentin Pasley wins'); KXUFCDISTANCE `title='Fight goes the distance?'`.
  So title is NOT the exchange_index class (SDK-dropped) -- it is the **yes_bid class**: OUR `_market_quote_dict` just
  doesn't COPY it. FIX = ufc ctx builder reads `getattr(m,"title",None)` off the object. **NO raw merge for title.**
- **quote `*_dollars`** (yes_bid_dollars/yes_ask_dollars/no_bid_dollars/no_ask_dollars) -- ON the object, POPULATED as
  string dollars ('0.2300'). Kalshi returns ONLY the `*_dollars` form -- there are NO bare yes_bid/yes_ask keys (raw
  OR model). So UFC behaves EXACTLY like MLB; the `d("yes_ask_dollars","yes_ask")` fallback stays dead. **NO
  fractional/non-fractional second trap.**
- **`exchange_index` -- DROPPED BY THE SDK OBJECT** (not a MarketModel field; extra='ignore' discards the raw key);
  present in RAW = **0** for UFC (=> MMA shard 0). SAME as MLB -> the ufc builder MUST mirror `_merge_raw_market_fields`
  for exchange_index. 0 flows correctly (the `is not None` checks handle it; gate 6b handles shard 0 per M5).
- **`yes_bid_size_fp`/`yes_ask_size_fp`** -- now ON the 1.0.6 model, but `_market_quote_dict` doesn't copy them; MLB
  merges from raw -> ufc builder mirrors the same merge for parity.
- **`kalshi_dates`** -- derived from the TICKER date (KXUFCFIGHT-26SEP08... -> 2026-09-08); NO market field needed.
- **`liquidity_dollars`='0.0000'** -- the deprecated always-zero stub; NOT relied on (gate 3 uses yes_bid_dollars).
  Same non-issue as MLB. NO MarketModel field carries a pydantic `deprecated=` flag.
- **TICKER-DATE RESOLVED 2026-09-03 (read-only `pm_ufc_datefields_ro` + `pm_poly_ufc_dates_ro`; Jack: establish BEFORE
  building):** the Kalshi ticker date is the **card-LOCAL date (ET/venue), NOT the UTC `occurrence_datetime`.**
  `occurrence_datetime`==`expected_expiration_time` = the real fight UTC time; `close_time`==`expiration_time`==
  `latest_expiration_time` = a ~2-WEEK **administrative settlement-deadline buffer** (NOT the event). **The join MUST
  use the card-local date = the TICKER's encoded date (exactly what the matcher's `kalshi_to_iso_date` already does)
  -- NOT occurrence/close_time.** VERIFIED both venues AGREE on 8 fights across 2 cards incl. the cross-midnight one:
  Kalshi `KXUFCFIGHT-26SEP08PASBER` (occurrence **2026-09-09T05:20Z UTC**) and Poly `ufc-quepas-arlber-**2026-09-08**`
  BOTH label it **2026-09-08** (card-local) -> the join holds precisely where a UTC-derived date would have BROKEN it.
  => the ctx builder derives the date FROM THE TICKER and must NOT 'correct' it via `occurrence_datetime`. (Also: Poly
  `ufc-who-will-*-fight-next` futures have no slug date -> matcher skips; DWCS cards list under KXUFCFIGHT too.)

PROPOSED B2 DESIGN (agree BEFORE touching evaluate -- the chokepoint on 2 live armed accounts):
1. **`execution.MarketContext`** -- ADD optional `fight_index: dict|None=None` (keep moneyline/total/spread/
   kalshi_dates/markets). MLB constructs BYTE-IDENTICALLY (new field defaults None); the ufc builder sets fight_index +
   empty ml/tot/spr. (kalshi_dates semantics differ per category -- MLB=ticker set, UFC=ISO-date set -- each produced
   by its own builder + read by its own adapter, no conflict.)
2. **`execution` matcher-adapter registry** `{"mlb":_MlbAdapter, "ufc":_UfcAdapter}` selected by `sub.category`; each
   exposes `.parse(slug,outcome)` + `.match(parsed, ctx, allowed_market_types) -> MatchResult` (uniform fields
   status/confidence/kalshi_ticker/reason/leg/market_type -- BOTH matchers already share this). evaluate lines 383-384
   become `adapter.parse`/`adapter.match`; EVERYTHING after (status/ticker/leg/`ctx.markets.get`/gates/sizing/body) is
   UNCHANGED. Unknown category -> **fail-safe skip** (never match with the wrong matcher -- the standing lens). The MLB
   adapter delegates to the IDENTICAL `M.match_bet(parsed, ctx.moneyline_index, ctx.total_index, ctx.spread_index,
   ctx.kalshi_dates, allowed_market_types=...)` -> byte-identical MLB behaviour by construction.
3. **`live_driver.fetch_ufc_market_context`** mirroring `fetch_market_context` for KXUFCFIGHT/KXUFCDISTANCE: per-market
   dict ADDS `title`; `U.build_kalshi_fight_index([{ticker,title}])` + `U.attach_distance_tickers`; kalshi_dates = ISO
   dates from tickers; raw-merge exchange_index(+size). Register in `CATEGORY_CTX_BUILDERS["ufc"]`. Per-category SERIES
   map (mlb=[KXMLBGAME,KXMLBTOTAL,KXMLBSPREAD], ufc=[KXUFCFIGHT,KXUFCDISTANCE]).
4. **PROOF:** MLB tests unchanged + a "mlb adapter == direct M.match_bet" equivalence test + box-scratch byte-identical
   mlb-only (the gate A got) + a DISARMED live ufc dry-run (also validates the ticker-date-vs-close-time join above).

## RUNG B2 — dispatch integration — ★ BUILT + LOCALLY PROVEN 2026-09-03 (Ruling 3); box-scratch is the deploy gate
Built the approved design. Files:
- **`execution.py`:** `MarketContext` gains optional `fight_index=None` (MLB construction BYTE-IDENTICAL); `import U`
  (ufc matcher); a `MATCHER_ADAPTERS = {"mlb":(_mlb_parse,_mlb_match), "ufc":(_ufc_parse,_ufc_match)}` registry; the
  `evaluate` seam (was lines 383-384) now selects the adapter by `sub.category` -> `parse`/`match`, EVERYTHING after
  UNCHANGED. Unknown category -> `skip:no_matcher_for_category` (fail-safe). The MLB adapter delegates to the IDENTICAL
  `M.match_bet(...)` -> byte-identical MLB by construction.
- **`live_driver.py`:** `import U`; `UFC_SERIES=("KXUFCFIGHT","KXUFCDISTANCE")`; `_merge_raw_market_fields` gains
  `series_list=None` (MLB call unchanged -> default SERIES); NEW `fetch_ufc_market_context` mirroring
  `fetch_market_context` + `title` via `getattr(m,"title")` (NOT a raw merge -- title is on the SDK object) + exchange_index
  raw-merge (IS SDK-dropped) + fight/distance index; `CATEGORY_CTX_BUILDERS` gains `"ufc"`. The date is from the
  TICKER (never occurrence_datetime) per the resolved ticker-date finding.
- **Tests:** NEW `test_b2_dispatch.py` (7 pass) -- the **MLB-adapter == direct M.match_bet EQUIVALENCE** proof Jack
  asked for (matches AND honest misses AND non-mlb), unknown-category fail-safe skip, UFC moneyline + distance dispatch
  (right ticker/leg), UFC unknown-fighter MISS (not a wrong pick), UFC market-type-excluded scope gate,
  MarketContext-MLB-construction-unchanged. **Also FIXED the 2 M1 tests** (`test_m1_shared_journal_caps`,
  `test_m1_underfunded_alarm_is_per_category`): they used 'ufc' as a FAKE MLB label (MLB slugs+ctx), which pre-B2
  worked because evaluate always used M; B2 makes dispatch REAL, so the ufc category now needs REAL ufc signals+ctx --
  updated them to a genuine cross-category proof (ufc HOOPAR fight). (The `test_m1_prior_snapshots` test was unaffected
  -- snapshots are taken pre-match.)
- **PROOF:** local `.venv-webtest` -> **16 env-gap failures unchanged** (17 pykalshi engine-driver paths + stale
  `schema_head_is_15`; ALL ModuleNotFoundError/stale-const, NONE from B2 logic); test_execution_r4 / test_mlb_match_r2
  / test_ufc_match all green (MLB non-regression + ufc units). ★ REMAINING (deploy gate, box only): byte-identical
  mlb-only on the REAL venv (the pykalshi scheduled_loop/kill_switch/shard_gate tests) + a DISARMED live ufc dry-run.

## RUNG B2 — dispatch integration (title + UFC context + evaluate registry) — DONE (see BUILT section above)

## RUNG B(old placeholder) — superseded by B(core)+B2 above
Discovery landed: UFC = 2 binary types -- moneyline `KXUFCFIGHT-{YYMONDD}{FTR1}{FTR2}-{FTR}` (one market per fighter)
+ go-the-distance `KXUFCDISTANCE-{YYMONDD}{FTR1}{FTR2}` (no line). Polymarket: winner slug + `-go-the-distance`.
Build `ufc_poly_kalshi_match.py` mirroring the MLB matcher surface + fighter-name canonicalization (the doubleheader
analog = the 3-char ticker-abbreviation collision, e.g. two fighters sharing the first 3 last-name letters on one
card -> a MISS, never a wrong pick). Then category-dispatch `evaluate` (registry by `sub.category`) + register the
ufc ctx builder in `CATEGORY_CTX_BUILDERS`. INERT until a ufc sub-division exists. ★ needs a disarmed live probe of
real KXUFCFIGHT/KXUFCDISTANCE tickers + real Poly ufc slugs to build canonicalization against real names.

## RUNG A-proof — byte-identical mlb-only behaviour — PENDING

## RUNG B — UFC matcher + category dispatch — PENDING

## RUNG C — caps mechanism — PENDING (HALT for Jack's ruling)

---

## DECISIONS I MADE (could have gone another way)
- Built off `e5d6506` (per-account tip == box main.py `9e8da82` CR-stripped), so every graft is a clean file-by-file
  diff off what the box runs.
- **M1 is a WIRING change, not an evaluate/Journal change.** I kept gate 6 / the Journal / the POST path untouched
  and achieved the joint account-cap purely by SHARING one per-cycle Journal across categories in the restructured
  loop. Alternative was a new account-scoped gate or a lock; both were rejected (the shared Journal already
  account-keys open_usd, so it is free + off the hot path).
- **Introduced an injectable `CATEGORY_CTX_BUILDERS` seam + a `_prior_snapshots` test seam.** This let me prove the
  WHOLE loop restructure locally WITHOUT pykalshi (a fake builder returns a canned MarketContext), instead of relying
  only on box-scratch. It is also the exact seam Workstream B uses to register the ufc catalog builder.
- **M2/M3 both route through one helper `account_active_categories` (fail-safe to [fallback]).** One place to reason
  about "every category on the account", used by both the auth-latch site and run_boot_reconcile.
- **Kept the guard (`plan_driver_tasks`) unchanged in M1** so Option C ships INERTLY (one category/account today ->
  byte-identical). The guard relaxation is M4's per-account opt-in, deliberately last.

## WHAT I FOUND THAT NOBODY ASKED ABOUT
- (A0) `consec_err` (#15) is NOT a real re-scoping — it is a per-CALL local inside run_live_arm_gated_cycle (invoked
  once per category), so it never shares across categories. The plan listed THREE safety re-scopings; on building,
  the SAFETY set is TWO (#14 alarm, #16 snapshots). The other two per-category items (#17 ctx/last_idx, #18
  last_settle) are FUNCTIONAL and fail-SAFE (a wrong catalog -> skip:no_quote; a shared throttle -> delayed booking).
  So the safety re-scoping problem is NOT larger than three -> Jack's stop condition does not trip. (Honest correction
  the build surfaced; documented in A0.)
- The pykalshi-path scheduled_loop tests fail LOCALLY as ASSERTIONS (placed 0), not import errors, because the loop
  swallows the pykalshi ImportError at the ctx-build and skips the category (fail-safe). Same failure mode before/
  after my change; they are byte-identical-provable only on the box (pykalshi present). Flagged as the deploy gate.

## DEPLOY QUEUE (authorize one rung at a time; nothing built here is deployed; box-is-truth, GRAFT main.py never wholesale)
### ★★★ A-DEPLOYED LIVE + VERIFIED 2026-09-02 23:03Z graft / 23:10:02Z restart (board-authorized, per-step)
- Graft `pm_a_deploy.ps1` attempt 1 FAILED SAFE (77KB single-line base64 > pipe line-limit -> `base64: invalid
  input` -> WRITE_SHA_FAIL -> restored, engine untouched). FIX = wrapped-heredoc base64 (<=76-char lines). Attempt 2
  GREEN: pre-checks OK, `live_driver 4b85f93f` + `boot_reconcile ecce7777` wholesale, `main.py` grafted `bba046e8`,
  Gate-A IMPORT_CLOSURE_OK. Backups `~/pm_a_deploy_backup_20260902T230350Z`.
- Restart (Jack, warned bitunix): engine **PID 163519 -> 171106**, active, 23:10:02Z. **POST-CHECK = NOTHING CHANGED
  (verified via `pm_a_postcheck_ro.ps1` + follow-ups):** roster `2 account task(s): {'kalshi_jack': ['mlb'],
  'kalshi_karen': ['mlb']}` (ONE task/account, single category — Option C invisible); persisted arm rows
  BYTE-UNCHANGED (global `08-31T02:35:38` / jack `08-31T21:49:39` / karen `09-02T12:53:23`, all armed latched=False);
  **boot-reconcile CLEAN for BOTH** (my M3 format, `latched_categories=()`, 23:14:42Z); 0 skip:exposure_unknown;
  every division back (bitunix/MACE/PEAD/IC/tasty/Kalshi×N/polymarket/PM/M3-snapshot). Per-category loop alive
  (OPPOSING-PAIR guard fires per-account in the new `account/category` format; jack's `0x0f58…` cid was contested on
  the OLD engine at 23:10:00 too = continuity).
- ★ Boot tracebacks/errors CLASSIFIED non-PM/pre-existing: 3 tracebacks = Telegram update-poll `httpx.ReadError`
  (transient, auto-retried); fidelity playwright ENOENT (no browser broker); earnings 404 BTC/USD (crypto); 4
  `pykalshi 404 not_found` from a non-PM Kalshi strategy. None from A; boot-reconcile clean AFTER all of them.
- Runners: `cc\pm_a_deploy.{sh,ps1}` (graft), `pm_a_postcheck_ro.*` + `pm_a_bootrecon_ro.*` + `pm_a_pmdriver_ro.*` +
  `pm_a_tb{,2}_ro.*` (read-only verify). ROLLBACK = restore `~/pm_a_deploy_backup_20260902T230350Z` + restart.

### A-DEPLOY (engine graft + ONE restart) — the prepared plan (executed above)
- **★ THE RESTART BOUNCES EVERYTHING.** A needs an ENGINE restart (`restart_tc.ps1` -> `systemctl restart
  trading-corp`), so ALL divisions bounce: **bitunix, MACE, PEAD, IC, tasty, the Kalshi strategies, AND the PM
  driver.** Time it accordingly. Box-scratch already proved A on the real venv; the restart is the only live step.
- **MANIFEST = THE IMPORT CLOSURE, not the diff** (3 files; every import already on the box; NO new module -- the
  ufc matcher is B, NOT A; NO migration). Hashes CR-STRIPPED both sides (`tr -d '\r'|sha256sum`), never raw `git show`:
  - `trading_corp/prediction_markets/live_driver.py` — WHOLESALE (box `a99139832970fd61` ==e5d6506 no-drift ->
    target `4b85f93f0bb20fd8`). Imports arm/boot_reconcile/db/execution/paper/settlement/shard_balance/
    venue_exposure + mlb matcher + kalshi_live — ALL on the box; my change added NO new import.
  - `trading_corp/prediction_markets/boot_reconcile.py` — WHOLESALE (box `dc9bbc9f89c29a5e` ==e5d6506 ->
    `ecce77770f951f74`). Imports arm + stdlib.
  - `trading_corp/main.py` — ★ GRAFT, NEVER wholesale (the box carries the per-account roster). box
    `9e8da82de3b8bfcf` (== e5d6506:main.py CR-stripped, verified) -> `bba046e8f1ce9801`. 20-line hunk; `patch -p1`
    VERIFIED locally to apply to base==box and yield exactly the target (LF patch + LF box + `tr -d '\r'` -> no CRLF
    failure like the overnight one).
  - `arm.py` UNCHANGED (M2 reused the existing looping `latch_auth_failure`).
- **STAGED RUNNER (authored + validated; NOT run): `cc\pm_a_deploy.{sh,ps1}`.** Pre-checks the 3 box pre-state hashes
  (ABORTS writing nothing on any drift); backs up all 3 to `~/pm_a_deploy_backup_$TS`; wholesale-writes the 2 package
  files (.tmp -> sha-verify -> mv); patch-grafts main.py + content-verifies (`_pm_by_account` + `categories=_acats`
  present, old `category=_t["category"]` spawn GONE) + sha-verifies target; Gate-A = py_compile all 3 + `import
  live_driver, boot_reconcile` on the real venv; RESTORES from backup on ANY failure. **Does NOT restart.** One-liner:
  `powershell -ep bypass -f .\pm_a_deploy.ps1`.
- **THEN (separate board steps):** `restart_tc.ps1`, then the post-check (`cc\pm_arm_persisted_ro.ps1` + a roster-log read).
- **POST-CHECK HEADLINE = NOTHING CHANGED** (Option C is invisible until a 2nd category exists):
  1. Roster log `2 account task(s): {'kalshi_jack': ['mlb'], 'kalshi_karen': ['mlb']}` — ONE task PER ACCOUNT, each
     the SINGLE category [mlb] (byte-identical to the old per-(account,category) wiring).
  2. Both accounts STILL ARMED, **persisted ts UNCHANGED** — read PERSISTED rows (`pm_arm_persisted_ro.ps1`), NOT a
     status call (the mode=ro fail-safe read a false disarm 3x): global `2026-08-31T02:35:38` / jack
     `2026-08-31T21:49:39` / karen `2026-09-02T12:53:23` must be byte-identical.
  3. Boot-reconcile CLEAN for BOTH (`reconciled=True latched=False latched_categories=()`).
  4. Order counts move ONLY for legit engine fills (the graft placed nothing). 0 skip:exposure_unknown storm.
- **STOP:** pre-check drift -> aborts writing nothing (investigate). Post-restart: >1 task/account, or a task with
  >1 category, or an arm ts CHANGED, or a boot-reconcile LATCH, or a skip:exposure_unknown storm -> restore
  `~/pm_a_deploy_backup_$TS` + restart to revert. Global STOP throughout:
  `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`.
- **B-bundle (ufc matcher + dispatch) — HALT.** Adds `trading_corp/data/ufc_poly_kalshi_match.py` (NEW) + evaluate
  dispatch + the ufc ctx-builder registration. INERT (no ufc sub-division yet). (fill shas when built.)
- **C (caps mechanism) — HALT for Jack's ruling** (account-level cap vs 75/75 divide).
- **Enablement (Jack's, not tonight): fund shard 0 -> M4 opt-in + create (kalshi_jack,ufc) + attach whales -> restart
  -> arm ufc with PLACE-ONE-AND-INSPECT.**
