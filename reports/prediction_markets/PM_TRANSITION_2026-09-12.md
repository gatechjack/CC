# PM TRANSITION — 2026-09-12 (supersedes PM_LIVE_FIXES_TRANSITION_2026-09-12)

**This is the current PM transition doc. It supersedes `PM_LIVE_FIXES_TRANSITION_2026-09-12.md`
(now forward-bannered to here). Written for someone who was not in the session.**

═══════════════════════════════════════════════════════════════════════════════════════════════
## 0. OPEN HERE — armed state, STOP, and the next session's subject
═══════════════════════════════════════════════════════════════════════════════════════════════

- **30 sub-divisions are ARMED and TRADING and must stay that way.** Read from the PERSISTED
  `agent_state` rows (NOT a status call) 2026-09-12 03:12Z: `arm:global` armed + 30 sub rows all
  `armed=True latched=False trigger=None`. All 30 are LIVE (fresh driver heartbeats, see §7). **Do
  not disarm. Do not restart. Do not advance prod-live.**

- **STOP command (verbatim), run from `/home/azureuser/trading_corp`:**
  ```
  PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global
  ```
  Fire-first-report-second only on a confirmed wrong-side/ wrong-fighter fill. An UNREADABLE
  audit is INCONCLUSIVE, not a mismatch.

- **★ THE NEXT SESSION'S SUBJECT is the GIT-TRUTH RECONCILE.** Its cold-start deliverable is
  **`reports/prediction_markets/GIT_TRUTH_DIVERGENCE_2026-09-12.md`** (in this same branch). Read
  that first: box = a file-level graft of a PM line (`d19c0ab5`) and a MACE line (`ce5b748c`)
  forking at `6a5ed04`; **no fast-forward is possible**; the difficulty is the shared trio
  (`main.py`/`data_exec.py`/`robinhood.py`); `main` carries 0 PM files (PM→main = subtree merge);
  keep `95e78c4` reachable.

- **Nothing of mine is watching.** No background poll/watch/cron was started this session; all box
  reads were one-shot read-only runners that returned. The only Kalshi polling is the engine's own
  30-sub loop (must keep running). The UFC method fill-watch is a *runner*, `cc/pm_ufc_fillwatch_ro`,
  **not** a running process — armed but idle; nobody is running it (see §7).

═══════════════════════════════════════════════════════════════════════════════════════════════
## 1. TODAY'S WORK (what shipped / researched)
═══════════════════════════════════════════════════════════════════════════════════════════════

### 1a. ctx-pagination fix — DEPLOYED box-only, WORKING
`live_driver.py` OPEN-market fetch was truncated; cfb spread/total ctx never indexed past 1000
markets → matched nothing. Fix = paginate the OPEN fetch. **Both truncation points:**
1. `fetch_structural_market_context` fetched OPEN markets as a SINGLE `limit=1000` page
   (`fetch_all=False`) while paginating settled → strikes past 1000 absent from the ctx index.
2. `_merge_raw_market_fields` (gate-3 `*_size_fp` + gate-6b `exchange_index`) used the SAME single
   GET → page-2 markets got no shard/size → would have traded `no_kalshi_strike` for `skip:illiquid`.
Fix cursor-paginates both (25-page loud cap). Box `live_driver.py` = **6561b569**. Universal across
7 builders but only cfb exceeded 1000 (measured). Detail: `[[pm-ctx-pagination-fix-2026-09-11]]`.

### 1b. Four UFC method market types — DEPLOYED box-only, both accounts ENABLED, INERT (no fill yet)
KO/TKO + Submission × either-fighter (`KXUFCMOF`, `method_finish`) / named-fighter (`KXUFCMOV`,
`method_victory`), code-anchored fighter bind. Box files: matcher `data/ufc_poly_kalshi_match.py`
= **ebabe6ce**, `prediction_markets/execution.py` = **14787ba4**, `live_driver.py` = **6561b569**.
Both subs `market_types='moneyline,go_the_distance,method_finish,method_victory'` (enable tokens
persisted). **★ TWO DIVERGENCES ACCEPTED AS MY (Jack's) DECISION, not oversights:** (1) **NC ~1%
every method market** — Poly settles 50-50, Kalshi has a `-DRAW`, so our leg loses ~$3/copy on a
no-contest; (2) **DQ ~0.1% on KO/TKO** — Kalshi buckets KO/TKO/DQ together, Poly excludes DQ.
Both known, both accepted; correctness > coverage. Detail: `[[pm-ufc-method-types-2026-09-12]]`.

### 1c. Milestone start-time research — RESEARCH-COMPLETE, NOT-BUILT
Kalshi's milestones API **does** give a real cross-category UTC kickoff time for every category we
run except fed — overturning the tile-inventory "no start signal" conclusion. Working join =
catalog-index (the documented `related_event_ticker` query param 400s unauth on both hosts). A
build would index the two catalogs on the existing 60s pm_web poll, store `start_date` per event
ticker, render tile state as a clock-compare against our own settlement; live status is a separate
optional per-event cost, not asked for. Full detail: `[[pm-milestone-start-time-2026-09-12]]`.

### 1d. UI live-fixes — NOT BUILT (WIP foundation only)
Item-3 mark-cache foundation committed WIP in `ui_cache.py` (`231dea8a`, INERT — render doesn't
consume it). Full 3-item spec + build order 3→1→2 lives in the (now superseded) UI transition doc,
still valid as the build spec. See §2 deferred #4.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 2. FOUR THINGS DEFERRED (and why)
═══════════════════════════════════════════════════════════════════════════════════════════════

1. **Rounds Over/Under — deferred (correctness).** Poly marks the 2:30 mark of a round; Kalshi is
   whole-round "ends before round X" → OPPOSITE settlement in the first 2:30, no clean Kalshi
   counterpart. 381 vol dropped; correctness > coverage.
2. **Round-of-Victory + Decision — deferred (no source).** No Polymarket source exists to copy from.
3. **Spread leg-audit gap — REPORT-ONLY, not built.** The independent post-fill leg-audit net
   returns `'na'` (no check) for structural + mlb SPREAD (and moneyline + fed). SPREAD is the real
   hole (leg can invert on whale-team vs ticker-anchor). Scoped ~12–15-line fix documented, NOT
   built. `[[pm-leg-audit-coverage-gap-2026-09-11]]`.
4. **Mark-poller scoping — deferred; a SECOND-ORDER EFFECT of the ctx-pagination fix.** Surfacing
   more open markets exposed the pm_web mark-cache's wholesale-replace wipe on a partial series
   failure (a high-cardinality series HTTPErrors → the page flipped to "no mark / 0 priced"). Root-
   caused; WIP foundation in `ui_cache.py` (`231dea8a`, merges marks + persists titles, INERT).
   Build = Items 3→1→2 in the superseded UI transition doc.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 3. THREE PIECES OF FOLKLORE RETIRED THIS WEEK — do not reinstate
═══════════════════════════════════════════════════════════════════════════════════════════════

1. **The `main.py` "version-control gap" was a CRLF artefact.** `main.py` looked "missing/changed"
   from a branch; the diff was reading line-ending noise. The file was present. (Origin of the
   Measurement Rule.) `[[suspect-the-measurement-first]]`
2. **The "M5 app.py hazard" was a regex/farm-contains-arm artefact.** No committed `app.py` in any
   ref has an arm-write route; `/pm/arm` is 404; no commit ever added one. M5 is an UNBUILT
   milestone (the CLI is the kill path). "Graft app.py wholesale = M5" was folklore. Stop repeating.
   `[[pm-web-prodlive-reconcile-2026-09-10]]`
3. **"Rounds O/U is Polymarket-only" was simply wrong.** Kalshi HAS `MOF/MOV/ROUNDS/VICROUND` series
   (validated live). Rounds O/U is still deferred — but for the 2:30-vs-whole-round SETTLEMENT
   divergence (§2.1), NOT because Kalshi lacks the market. `[[pm-ufc-method-types-2026-09-12]]`

═══════════════════════════════════════════════════════════════════════════════════════════════
## 4. STANDING LENSES (with running counts)
═══════════════════════════════════════════════════════════════════════════════════════════════

~9–10 Jack-ruled standing lenses are in play. The active one this session:

- **suspect-the-measurement-first** (feedback lens, elevated 2026-09-03). Prior tally ~11. **Fired
  3 MORE times this session, all caught:** (a) milestone `related_event_ticker` returning n=0 was a
  400 *malformed request*, not "no milestones exist"; (b) cfb/nfl/ufc/mlb "missing" from the
  milestone index was a *pagination-window* artefact + `competition=Pro Baseball → n=0` was a bad
  guessed param value (`type=baseball_game` proved MLB present) — *absence from a guessed parameter
  is not evidence of absence*; (c) the UFC method-fill state query errored `no such column:
  market_type` (the order journal derives type from the TICKER, doesn't store it) — re-queried by
  ticker prefix. `[[suspect-the-measurement-first]]`

Others (name + one-line), each Jack-ruled:
- **a-write-must-satisfy-every-view** (2026-08-31): >1 view of the same state → a write must satisfy
  ALL keys or one view diverges.
- **retroactive-enforcement** (2026-08-31): a guard acting on inherited state (not observed events)
  retroactively enforces against pre-existing state, overriding prior human decisions.
- **a-log-call-can-silently-fail-to-emit** (2026-09-01): a "--- Logging error ---" stub reaches the
  journal instead of the line; a diagnosis-from-logs build loses the signal.
- **a-bound-you-did-not-design-is-one-you-can-remove-by-accident** (2026-09-01): a safety property
  that holds only as a side effect gets silently removed when a future change "fixes" the mechanism.
- **an-assumed-mechanism-may-have-been-deliberately-never-built** (2026-09-01): verify a
  write-path/endpoint/permission exists before building on it (this retired the M5 folklore).
- **safety-check-that-silently-stops-checking** (~9th; the leg-audit `'na'` lens, §2.3).
- **disprove-on-the-case-that-could-refute**, **box-is-truth-reconcile-file-by-file**,
  **command-paste-rule** — see §5.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 5. BOX QUIRKS
═══════════════════════════════════════════════════════════════════════════════════════════════

- **★ scp+tar is the multi-file graft channel** (`command-paste-rule`): a base64-heredoc breaks on
  long lines (~70KB) and scp has hung ~40min. Sanctioned pattern = ONE `.ps1` streaming a `.sh` via
  `ssh "tr -d '\r\357\273\277' | bash"` for reads/small edits; **scp+tar staging** for multi-file
  grafts. `.sh` must be ASCII (0 bytes >127). `[[command-paste-rule]]`
- Box repo root = `/home/azureuser/trading_corp`. PM DB = `data/prediction_markets.db`;
  **arm/legacy DB = `data/trading_corp.db`** (arm lives here, not the PM DB).
- **Arm state is `agent_state.value_json`** (actor `pm_live`, keys `arm:global` /
  `arm:<account>:<category>`), NOT a `value` column — a wrong-column read once printed "0 armed" (a
  false disarm). `updated_ts` is an **ISO string**, not epoch.
- **`pm_subdivision_order` has NO `market_type` column** — market type is derived from the ticker
  (`KXUFCMOF`/`KXUFCMOV`/…). Query method fills by ticker prefix.
- Services: engine = `trading-corp.service` (xvfb-run wrapped); pm_web =
  `prediction-markets-web.service`; a separate `trading-corp-kcv2-observer.service` also runs.
- Box venv pytest needs `-p no:pytest_ethereum`.
- Kalshi shards 1 & 2 are empty by design; funds live on shards 0 & 3.
- Public Kalshi endpoints (milestones/live_data/markets) are unauthenticated — probe them from a
  LOCAL IP, not the box, to avoid adding to the engine's 7s-poll load.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 6. SETTLED RULINGS — do not re-litigate
═══════════════════════════════════════════════════════════════════════════════════════════════

- MACE deploys are **box-only, NO FF-push**; the git reconcile is deferred to its own session.
- **PM deploys LAST**, after MACE, and rebases onto the FINAL MACE box-truth — never onto `7220e32f`.
- UFC **NC (~1%) + DQ (~0.1%) divergences ACCEPTED** (§1b).
- Rounds O/U ruled OUT (2:30 divergence); RoV + Decision ruled OUT (no Poly source).
- **Promote-time guard for matcherless categories RULED OUT** (self-closing gap; handled by not
  promoting into matcherless cats). Orphan sub-divisions are kept (subs are permanent by design);
  `/live` shows orphans by design (`tiles_all` honours R2).
- Both UFC accounts enabled (Jack's gate override).
- M5 is UNBUILT (the CLI is the kill path; there is no arm-write route).
- The spread leg-audit gap is REPORT-ONLY (§2.3).

═══════════════════════════════════════════════════════════════════════════════════════════════
## 7. STATE SNAPSHOT — read 2026-09-12 03:11–03:15Z (persisted rows + journal, never a status call)
═══════════════════════════════════════════════════════════════════════════════════════════════

**Services / PIDs:**
- `trading-corp.service` (engine) **PID 351422**, active since **2026-09-12 01:33:35Z**.
- `prediction-markets-web.service` (pm_web) **PID 332976**, active since 2026-09-11 03:28:56Z.
- `trading-corp-kcv2-observer.service` PID 679 (separate; since 2026-08-27). Helper services
  (pruner / watchlist-deep / watchlist-stats) inactive.

**Schema head (prediction_markets.db):** `MAX(version) = 21` (migration 021 applied).

**ARM — from persisted `agent_state` pm_live rows (with `updated_ts`), NOT a status call:**
`arm:global` armed=True (2026-08-31T02:35Z). **30 sub rows, ALL `armed=True latched=False
trigger=None`** — arm timestamps span 2026-08-31 → 09-08 (when each was armed; arm persists across
restarts, is not rewritten on boot). → **30 armed sub-divisions, none latched.**

**Roster / liveness (`pm_driver_heartbeat`, `pm_driver_task_heartbeat`):** 32 distinct
(account,category) subs cycling — kalshi_jack 17, kalshi_karen 15. **30 of the 32 are armed**;
kalshi_jack's `fl1` + `sea` cycle but have NO arm row → disarmed-by-absence (won't place). All 32
heartbeats FRESH (reached/eval 0–32s ago, `state=evaluated`, `err=0`, `placed=0` this cycle); task
heartbeats jack 15s / karen 16s. → **30/30 armed subs LIVE; none STALE/NEVER.**

**Boot-reconcile (journal, 01:37Z after the 01:33 boot):**
`kalshi_karen reconciled=True latched=False latched_categories=()`;
`kalshi_jack reconciled=True latched=False latched_categories=()`. Corroborated by zero latched
arm rows.

**Open positions (journal net≠0) + order counts:**
- kalshi_jack: **OPEN=28**, 327 filled non-dry orders.
- kalshi_karen: **OPEN=29**, 204 filled non-dry orders.
- Both across cfb / lal / mex / mlb / nfl / ufc. UFC = 5 `KXUFCFIGHT` **moneyline** each
  (COR / FIO / ROS / MCM / VAN). (Account position sets differ — they copy different whale sets.)

**Shard balances (`pm_shard_balance_snapshot`, snapshot age 73s):**
- kalshi_jack: `{0: $277.80, 1: 0, 2: 0, 3: $188.16}` ≈ **$465.96**
- kalshi_karen: `{0: $265.21, 1: 0, 2: 0, 3: $152.18}` ≈ **$417.38**
- Shards 1 & 2 empty by design.

**Crons (`crontab -l`, 6 total):** 4 PM crons — `paper-poll` (`*/30`), `refresh --cap 50000`
(05:00), `paper-adjudicate` (05:40), `paper-rollup` (05:50). 2 platform crons —
`telegram_lifecycle_divergence_check` (08:30), `replay_audit_event_write_failed` (hourly).

**★ THE ONE OPEN WATCH — UFC method-type fill:** `KXUFCMOF`/`KXUFCMOV` orders = **0** (any status /
filled / dry-run). Both UFC subs carry the 4 enable tokens. → **No method-type fill has landed on
either UFC sub-division — STILL TRUE.** Method is enabled-but-idle. The fill-watch
`cc/pm_ufc_fillwatch_ro` is a RUNNER, not a running process — **armed but idle, not running.**

═══════════════════════════════════════════════════════════════════════════════════════════════
## 8. HOUSEKEEPING — my artifacts only (KEEP / REMOVE + what restoring does NOW, 30 subs armed)
═══════════════════════════════════════════════════════════════════════════════════════════════

**I did NOT delete anything. I did not create anything dated before 2026-09-11 — those backups
belong to prior sessions; do not touch.**

### Local (Windows `cc\`), this session — ALL READ-ONLY (running any now = read-only, zero trading effect)
| artifact | disposition | restoring/running now |
|---|---|---|
| `kalshi_milestone_{probe,diag,decisive,join,presence,startconv}.ps1` | KEEP | local public GETs, no box, no effect |
| `pm_milestone_heldtickers_ro.{ps1,sh}` | KEEP | `mode=ro` DB read, no effect |
| `pm_divergence_boxhash_ro.{ps1,sh}` | KEEP | box file-hash read, no effect |
| `pm_state_ro.{ps1,sh}` | KEEP | `mode=ro` state read, no effect |
| `pm_ufcfill_recon_ro.{ps1,sh}` | KEEP | `mode=ro` + journal read, no effect |
| `pm_backups_enum_ro.{ps1,sh}` | KEEP | `ls`, no effect |
| `cc\_farm_recon\*.txt` (milestone/divergence/state/ufcfill/backups outputs) | REMOVE-ok | scratch text; harmless either way |
| worktree `cc-pm-live-fixes-wt` | KEEP | carries this branch + these docs |

**None of my local artifacts are dangerous — all read-only.**

### Box (`~azureuser`), this session (2026-09-11 → 09-12) — created during the ctx-fix + UFC deploys
| artifact | disposition | restoring NOW |
|---|---|---|
| ⚠️ **`pm_ctxfix_backup_20260911T230937Z/`** | KEEP — **DO NOT RESTORE** | reverts `live_driver.py` to pre-ctx-fix on the live engine → cfb spread/total stop copying (30 armed subs) |
| ⚠️ **`pm_ufc_backup_20260912T012431Z/`** and **`…013131Z/`** | KEEP — **DO NOT RESTORE** | reverts the 3 UFC-method files (matcher/live_driver/execution) → **reverts live matcher work**. Two dirs = the partial-graft rollback + clean re-graft. |
| `pm_ufc_enable_backup_20260912T014729Z.json` / `…015356Z.json` | KEEP | reverts UFC `market_types` to pre-enable (drops method tokens) → *disables* method copying — SAFE direction, not a wrong-fill |
| `pm_cfb_enable_backup_20260911T185918Z/` | KEEP | reverts cfb `market_types` to pre-enable — SAFE direction |
| `/tmp/{ufcm_out, ctxfix_out, ctxfix_out.difftmp, pm_cfb_enable_pre_snapshot.json, pllive_out.txt, tmpk823ctxz}` | REMOVE-ok (operator) | scratch; harmless |

**★ The three `pm_ctxfix_backup…` / `pm_ufc_backup…` dirs are the dangerous ones: they are
pre-graft engine backups whose restoration reverts LIVE MATCHER WORK on 30 armed sub-divisions.
Keep them as rollback safety; never restore them casually.** I did not delete them (deleting
rollback safety during armed trading is out of scope).

═══════════════════════════════════════════════════════════════════════════════════════════════
## 9. POINTERS
═══════════════════════════════════════════════════════════════════════════════════════════════
- Divergence deliverable: `reports/prediction_markets/GIT_TRUTH_DIVERGENCE_2026-09-12.md`
- Superseded UI transition (still the Item 3→1→2 build spec): `PM_LIVE_FIXES_TRANSITION_2026-09-12.md`
- Memory: `[[pm-ctx-pagination-fix-2026-09-11]]` `[[pm-ufc-method-types-2026-09-12]]`
  `[[pm-milestone-start-time-2026-09-12]]` `[[pm-leg-audit-coverage-gap-2026-09-11]]`
  `[[deploy-base-boxtruth-divergence-2026-09-11]]` `[[suspect-the-measurement-first]]`
  `[[command-paste-rule]]`
