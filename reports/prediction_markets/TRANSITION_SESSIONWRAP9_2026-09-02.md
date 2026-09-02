# ★ SESSION WRAP 9 — 2026-09-02 (~13:20Z). SUPERSEDES SW8. FIRST-READ for the next agent.
> SW8 (`TRANSITION_SESSIONWRAP8_2026-09-02.md`, on branch `pm-prospects-analyze-2026-09-02`) is superseded by this
> doc. Since SW8: **PER-ACCOUNT TRADING went LIVE — a SECOND Kalshi account (Karen) now trades on the one engine.**
> (SW8 lives on a different branch/agent's surface; I left it untouched — this doc + the MEMORY.md first-read pointer
> are the forward banner.)

---

## ★★ TWO ACCOUNTS ARE ARMED AND TRADING — one engine, real money. Do NOT disarm as part of anything routine.
- **`kalshi_jack/mlb` AND `kalshi_karen/mlb`**, one shared engine, each: **5 contracts/copy, 50 orders/day, $150 daily
  / $150 open, $5.50 per-order, 2c slippage, 0.75 liquidity**, market types moneyline/total/spread. Jack copies
  SDTrading/xifutloong3/0x684baa57; Karen copies 0x684baa57/0xd6966eb1/0xdb859a55 (they SHARE 0x684baa57 — two
  accounts copying one whale is correct; gate-4's COID carries the `account:category` division).
- **★ STOP (verbatim) — kills BOTH accounts, never depends on any UI:**
  `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`
- **State, observed 13:12–13:19Z (PERSISTED rows, not a status call — see the fail-safe note):** arm:global armed=True
  ts `2026-08-31T02:35:38`, arm:kalshi_jack:mlb armed=True ts `2026-08-31T21:49:39`, arm:kalshi_karen:mlb armed=True
  ts `2026-09-02T12:53:23` (all latched=False). Engine PID **163519** (NRestarts=0, up 12:41:51Z), pm_web **155543**,
  **schema 17**, boot-reconcile last = `reconciled=True latched=False` for BOTH (12:45Z). Orders (dry_run=0): jack
  total 85 / max_id 90 / 3 filled entries today; karen total 5 / max_id 89 / 5 today. Open positions: **jack 3**
  (SDCIN-10 −5, ATHTEX-8 +5, SEABOS-9 −5); **karen 5** (NYMTB +5, DETMIN +5, SDCIN-10 −5, ATHTEX-8 +5, SEABOS-9 −5).
  Shards (age 4min): jack $498.03 (sh3 498.02), karen $472.62 (sh3 447.61). Both shard-3 funded. Four PM crons intact
  (paper-poll `*/30`, refresh `05:00`, adjudicate `05:40`, rollup `05:50` UTC). **No monitor running — nobody is
  watching (see Housekeeping).**

## ★★ GRAFT HAZARDS — INSTRUCTIONS, not history (read before ANY box deploy)
- **`app.py` M5-DRIFT HAZARD IS STILL LIVE.** The box `web/app.py` has `is_admin`=**10**, `/pm/arm`=**0** (M4+whale).
  HEAD's `app.py` carries M5's `is_admin` plumbing (12 occurrences + `/pm/arm`) that is DELIBERATELY NOT on the box. A
  wholesale `app.py` copy LEAKS M5's admin surface to prod before M5's window. **GRAFT the intended app.py hunk onto
  the box; NEVER wholesale-copy app.py until M5 ships. Verify after: `is_admin` stays 10, `/pm/arm` = 0.** (This is a
  REAL content difference, confirmed by grep-count — not a line-ending artifact.)
- **`main.py` carries the LIVE per-account driver wiring (N2). Do NOT wholesale-deploy main.py from any branch that
  lacks N2.** The box main.py == THIS branch's committed main.py (verified byte-for-byte, LF). But the UI branch
  (`pm-ui-rewrite`, base `e95e638`) and older branches do NOT have N2 — a wholesale main.py deploy from one of those
  would silently DELETE Karen's driver wiring (her task vanishes on the next restart). **The UI deploy is pm_web-ONLY;
  it must not touch main.py.** If main.py ever must ship, use `pm-per-account-trading-2026-09-02 @ HEAD` (== the box).
- **★ CORRECTION to the SW7/SW8 record: there is NO main.py "version-control gap," and it did not "recur."** The box
  main.py has always equaled the committed branch content in LF. The recurring "matches no commit" alarm is a
  MEASUREMENT ARTIFACT: `git show <commit>:main.py | sha256sum` under `autocrlf=true` emits **CRLF**, the box file is
  **LF**, so their sha256 never matches — it LOOKS like drift every time that method is used. Proof: box pre-graft
  `cc733a17` == `git show f1e28cc:main.py | tr -d '\r' | sha256sum` (`cc733a17`), while the CRLF-smudged `git show`
  sha was `69e14976`. Post-graft box `9e8da82` == HEAD's LF blob `9e8da82`; `git status` shows nothing to commit.
  **The fix is to compare CR-stripped (or use `git diff`/`git hash-object`, which normalize), never raw
  `git show | sha256sum`.** Capturing the file "again" does nothing — there is nothing wrong.

## ★ WHAT WENT LIVE (deployed 12:36Z graft + 12:41Z restart; box-is-truth, per-step board-authorized)
- **N1 — `resolve_kalshi_keys` fail-CLOSED whitelist** (`shard_snapshot_task.py`). Was fails-open **instance #13**:
  any ref != `kalshi_karen` returned JACK's keypair. Now `{kalshi_karen→karen, KALSHI/kalshi_jack→jack, else→
  (None,None)→skip}`. Fixes the M3 snapshot loop too.
- **N2 — per-subdivision driver roster** (new `driver_roster.py`; `main.py` boot block). The engine enumerates active
  sub-divisions from the DB (attachment-gated), builds one broker per distinct account (via the whitelist,
  fail-closed skip), and spawns one `scheduled_pm_live_loop` task per sub-division sharing one positions_client. It
  REFUSES a 2nd sub-division on one account (loud, filed). live_driver.py:639 log fix + R7 rode this graft.
- **R7 — gate-6 exposure cap rebased onto the VENUE's true open exposure** (new `venue_exposure.py`; `execution.py`
  gate 6 + `Journal.in_cycle_open_usd`; `live_driver.py` per-cycle read). Co-tenant-correct, fail-closed
  (`skip:exposure_unknown`), preserves within-cycle accumulation.
- Proven locally 720/16 baseline + the deploy's Gate-A (import closure). Deploy: 5 package files wholesale (sha==HEAD),
  main.py PATCH-grafted, backups `~/…bak_peracct_20260902T123628Z`.

## ★ THE WRITE PATH IS PROVEN (the thing Ruling 2 rested on, since place-one-and-inspect was skipped)
Karen filled 4 orders within ~1s of arming. `pm_karen_firstfill_verify_ro` (two-direction venue read): **KAREN
venue==journal, 0 mismatch — every one of her orders landed on HER OWN Kalshi book. JACK venue==journal, 0 phantom —
Karen's orders added NOTHING to jack's account.** The tickers jack shares (SDCIN-10/SEABOS-9/ATHTEX-8) are jack's own
copies, every one placed BEFORE Karen armed. The credential path is proven for reads (Rung 0: Karen read her own book)
AND writes (her fills on her book). NO-leg fills hand-inspected (−5 = NO per the proven sign convention; exposure
reconciles).

## ★ R7's FIELD IS SETTLED — `market_exposure_dollars`, observed LIVE on a real held position
The 4th field-name surprise of this build (after exchange_index dropped by the SDK, liquidity_dollars a deprecated
stub, yes_bid dropped by our own dict) is answered by OBSERVATION, not construction: Kalshi `/portfolio/positions`
returns `market_exposure_dollars` (a dollar string), which is exactly the field R7's `venue_exposure._position_
exposure_dollars` prefers. Gate 6 read it correctly. (The cents fallback remains, harmless.)

## ★★ STANDING NOTE — the `mode=ro` arm fail-safe read looks EXACTLY like a disarm (put here, not just the ledger)
It fired at least THREE times today (11:30Z recon; both post-restart post-check reads). `arm.read_arm_verdict`
(`mode=ro`) during DB contention — ESPECIALLY a restart window — returns `armed=False scope=global` **by design** (an
unreadable arm state must never read as armed). **This is NOT a disarm.** The truth is the PERSISTED `agent_state`
rows + their `value_json.ts`: a REAL disarm stamps a NEW ts; the fail-safe leaves the OLD one. **Never conclude a
disarm from a status call — read the persisted rows + ts.** (Runner: `cc\pm_arm_persisted_ro.ps1`.) [[grep-is-not-a-state-check]]

## ★ FILED, UNBUILT — the 2nd-category-on-ONE-account preconditions
N DISTINCT accounts (one category each) are safe — jack/mlb + karen/mlb is exactly that. A SECOND category on ONE
account (e.g. jack/mlb + jack/nba) silently degrades three account-scoped safeties: the `open_usd` cap has a
within-cycle over-place race between the two same-account tasks; `latch_auth_failure` is called with only the caller's
category (a 401 leaves the sibling POSTing on dead auth); a full-account KALSHI_ONLY boot-reconcile mismatch latches
only the categories that have tasks (`boot_reconcile.py:50-53`). `driver_roster.plan_driver_tasks` REFUSES a 2nd
sub-division on one account loudly so a config/DB edit cannot land the unsafe case. Fix these before enabling a 2nd
category on any account.

## ★ STANDING LENSES / BOX QUIRKS / OPERATING RULES / SETTLED RULINGS (do not re-derive or re-litigate)
- **"a safety check that silently stops checking"** — instance **#13** this build (`resolve_kalshi_keys` fails-open,
  fixed). Prior 12 stand. **"when a gate never passes, suspect its input before its logic"** (the roster input;
  the CRLF-smudged sha comparison). **box-is-truth: reconcile FILE-BY-FILE, and compare CR-STRIPPED — raw
  `git show | sha256sum` under autocrlf lies (this session's lesson).** grep-is-not-a-state-check; a-write-must-
  satisfy-every-view; a-log-call-can-silently-fail-to-emit; a-UI-pointer-must-not-ship-before-its-target;
  an-assumed-mechanism-may-have-been-deliberately-never-built; env-leads; deploy-manifest-is-the-import-closure;
  retroactive-enforcement; asset-outlives-code; the false-alarm disarm read (above, now 3 instances).
- **Box quirks:** the box is NOT a git repo → deploys are base64-embed/stream grafts (mirror `cc\pm_r2_graft.sh` /
  `pm_bundle_step2_files.sh`), never `git checkout`. main.py + package files are LF on the box. Box pytest needs
  `-p no:pytest_ethereum`. Local tests: `.venv-webtest` (this session added `pytest-asyncio`+`pyyaml`; `pykalshi` not
  installable there — 15 async/live-path tests only run in box-scratch). Restarts are az-root via
  `C:\Users\AA Incorporado\Desktop\restart_tc.ps1` (`systemctl restart trading-corp`); pm_web =
  `prediction-markets-web`. Migrations applied by `pm_cli`/`init_db`, not the engine.
- **Operating rules:** command-paste-rule — one `.ps1` in `cc\` streaming a pure-ASCII no-BOM `.sh`; per-step
  "board authorizes atomic execution" for any deploy/restart/DB-write/arm; read-only runners are lighter but still
  presented. After the board phrase, YOU run that exact reviewed runner; do not re-challenge.
- **Settled rulings:** two accounts one engine (LIVE); Karen add-yes/attach-yes/arm-yes DONE; sign convention
  +1.00 YES / −5.00 NO proven; whale-proportional sizing CONTRAINDICATED; category exclusion is presentation not
  ingest (R5); no per-market cap yet (N whales × N ct can stack).

## ★ REMAINING QUEUE
- **Engine M5** (`/pm/arm` global arm control) + its PM-side cross-console link — built, NOT deployed; needs the
  Portal :8000 NSG glance + an engine window. Its `app.py` M5 hunk is the app.py-graft hazard above.
- **`live_driver.py:639` log fix** — SHIPPED (it rode the N2 graft; the box live_driver.py == HEAD).
- **The PM UI rewrite** — `pm-ui-rewrite-2026-09-02 @ 35ef13d`, a DIFFERENT agent, pm_web-only deploy pending (see
  its own report `PM_UI_REWRITE_REPORT_2026-09-02.md`). It touches app.py (graft-hunk hazard) + templates + css; it
  must NOT touch main.py. This branch touches ZERO pm_web files — no collision.
- **Backlog** `[[prediction-markets-backlog]]`: opposed-close realized-P&L unbooked; R7.h tx_hash re-entry key;
  per-market cap; doubleheader ticket; shard money-management; plain-lang descriptions; the exposure-cap was R7 (done).

## Branch / prod-live
- **`pm-per-account-trading-2026-09-02` @ HEAD** (this session; the SW9 commit is the tip; pushed, local==origin).
  Base `f1e28cc`.
  Touches engine (main.py + 5 pm package files) + tests + reports ONLY — **zero pm_web files.**
- prod-live `7220e32` / origin main-wip `8fd95d1` (local main-wip `ed2e6c0` = the legacy-disarm, not pushed).
  **NOT advanced — box-is-truth, every deploy a file graft.**
- Full session ledger + housekeeping: `reports/prediction_markets/OVERNIGHT_2026-09-02.md`.
