# TRANSITION — STAGE 2 COMPLETE (canonical handoff, 2026-08-28)

> **★ SUPERSEDED (2026-08-28) by `TRANSITION_STAGE3_DEPLOY_2026-08-28.md`** — Stage 3 (R1–R6) is now BUILT and
> UNSHIPPED; the deploy agent's FIRST read is the Stage-3 deploy handoff. This doc remains accurate for the
> Stage-2 (live) baseline it describes.

> **This is the canonical handoff. It SUPERSEDES `TRANSITION_STAGE0_COMPLETE_2026-08-27.md`** (which describes a
> pre-Stage-1 state that no longer exists). **Restate the THREE LISTS / THREE BASES from memory before touching
> code** (the anti-drift rule, `PM_REQUIREMENTS.md`): **Prospect** = completed trades (`pm_closed_position` →
> `pm_category_stats`, code `candidate`); **Watchlist** = our paper trades (`pm_paper_trade` →
> `pm_paper_category_stats`, code `pinned`); **Live** = live trades (P3 tables, not built). **`PM_REQUIREMENTS.md`
> governs; `PM_REBUILD_PLAN_2026-08-26.md` is the stage ladder + the STANDING BOX QUIRKS.**

---

## ★ 1. FIRST THING THIS SESSION DOES — check the 05:00 unattended cron cycle (2026-08-28)

The full chain ran **unattended for the first time** overnight — `refresh 05:00 → adjudicate 05:40 → rollup 05:50`,
with the `*/30` poller through it. **The wrap snapshot at 05:38:32Z caught it MID-FLIGHT:** refresh had already
fired at 05:00 (next 08-29 05:00), **adjudicate was ~1 min out (05:40)**, rollup ~11 min out (05:50);
`pending_adjudication=20` was waiting on that adjudicate. So by now the cycle has completed — **verify it, do not
assume**:

1. **Did all four jobs fire, and what does each log say?** `tail`/`grep` `~/pm_poll.log`, `~/pm_refresh.log`,
   `~/pm_adjudicate.log`, `~/pm_rollup.log` (parse JSON blocks with `json.raw_decode`, NOT `wc -l` — the ad-hoc
   `ADHOC_REFRESH_START` marker is non-JSON and off-by-ones a line counter).
2. **Any `SQLITE_BUSY` from the DESIGNED 05:00 poll/refresh overlap?** Schedule spacing is the ONLY guard (no
   `flock`, WAL + `busy_timeout=5000`). This was its first real test — grep the logs for `SQLITE_BUSY`/`locked`.
3. **Did adjudicate find anything (20 pending pre-run), and did any trade book a LOSS?** **No live LOST case has
   EVER been exercised** — the first losing resolution is its first live exercise; inspect it rather than assume
   (check `pm_paper_trade` `status='closed' AND won=0` and `close_source`).
4. **Did rollup change `pcs`, and does `/farm/ufc` still reconcile?** Compare `pm_paper_category_stats` row count/
   values; confirm `/farm/ufc` still renders the closed whales (Kh4mz4t/evanng) with correct numbers.
5. **Confirm nothing else wrote the PM DB overnight** (max `updated_ts` across paper tables sanity; at wrap it was
   `1787895002` ≈ the 05:30 poll).

READ-ONLY. Runner pattern: `cc\pm_wrap_snapshot_ro.*` (adapt to tail the four logs). Sanctioned channel only.

---

## 2. STAGE 2 IS COMPLETE — all three phases deployed + verified 2026-08-28

The Farm-League screen hierarchy is fully live. Each phase: build → box-scratch green → per-step deploy authz →
inverted/two-sided POST proof → prod-live ledger.

| Phase | Code SHA | Record SHA | Deployed (UTC) | What it did |
|---|---|---|---|---|
| **1 — nav skeleton** | `a8cefb5` | `839f452` | 00:16:10Z | `/dashboard`+`/farm-league`+`/farm-league/{cat}` shells at TEMP routes, alongside legacy; isolated base `pm_shell.html`; tiles data-driven; deactivated cat → 404 |
| **2 — per-category content** | `9d07038` | `2d6e00f` | 02:00:07Z | Filled Watchlist (paper: live OPEN count R6 + closed perf + stale/void; Analyze wired; Demote/Promote disabled) + Prospects (completed ranker via `query_scoreboard` filtered to candidates; disabled Promote-to-watchlist; honest-empty). NEW paper detail `/watchlist/{w}/{cat}` |
| **3 — repoint + retire** | `734c516` | `924c941` | 04:03:58Z | Repointed onto `/`, `/farm`, `/farm/{cat}`; retired flat scoreboard/farm pages+handlers+loaders (`query_scoreboard` FUNCTION stays as ranker); deleted 5 templates incl `pm_base.html`; **ONE shell**; no temp aliases (all 404) |

**`query_scoreboard` the FUNCTION is live (the Prospects ranker); the scoreboard PAGE is retired — do not confuse
them.** BASIS separation is the load-bearing invariant and is proven at every phase (paper vs completed displayed
values). Screen vocab is **Watchlist/Prospects** (F-3); `pinned`/`candidate` never leak.

**prod-live: `origin/prod-live` = `7220e32`** (advanced this session, fast-forward, verified via `ls-remote`).
**Linear chain: `8563c62 → 7ca932a (P1) → 2916f44 (P2) → 7220e32 (P3)`.** `95e78c4` (MACE fork base) reachable —
confirmed. Branches (all pushed): `prediction-markets-stage2-phase{1,2,3}-2026-08-28`; latest work tip
`924c941` (phase-3 branch). prod-live ledger commits are byte-verified `== box` at each deploy.

## 3. LEAVE-IT-RUNNING SNAPSHOT (2026-08-28 05:38:32Z, read-only, actual)

- **engine** PID **676** NRestarts 0 active/running · **pm_web** PID **42343** NRestarts 0 active/running.
- `/healthz` 200 (59 B) · `/` 200 **2306 B (dashboard)** · `/farm` 200 **4339 B (tiles)** · `/farm/ufc` 200
  **19403 B** with **Kh4mz4t (+90) and evanng (+11)** rendered.
- **schema 9** · `pm_watchlist` **114 / 92 / 22** (total/active/inactive) · `pm_paper_trade` **129** (2 closed /
  107 open / 20 pending) · `pm_paper_category_stats` **7 rows**.
- **Cron (azureuser):** poller `*/30`, refresh `0 5`, adjudicate `40 5`, rollup `50 5` (all `pm_cli`, per-job logs).
- **Rollback material present:** rung-3 DB backup `~/pm_stage0_rung3_dbbackup_20260827T130737Z.db` (25,137,152 B);
  phase-3 code backup `~/pm_stage2_p3_codebak_20260828T035748Z/` (all 14: 9 modified + 5 deleted — restoring the
  latter is how a phase-3 rollback restores the deleted pages).
- **Ledger re-hash:** box PM package = `7220e32` **29/29, 0 mismatches**; 5 deleted absent; shared `pm_cli.py` +
  data-client match. (One benign EXTRA on box: `prediction_markets/db.py.pre_cp3a_20260825T140605Z.bak`.)

## 4. STAGE 3 — REQUIRED, and NOT PREPARED (do not start; Jack authorizes)

Stage 3 is the promote/demote/promote-to-watchlist ACTIONS + going LIVE. **None of this is built or designed:**
- **Account model** — a `pm_account` entity (credentials; a **nullable owner-identity field**).
- **Sub-division entity + its config** (the Account-Category the Live list attaches to; P3).
- **The shared Kalshi execution engine** (live order placement path).
- **The MLB matcher** — including the **moneyline + totals strike dimension** (game line is not one market).
- **Live order placement VERIFIED on Jack's OWN account** before anything touches real money elsewhere.
- **★ D1 — the whale-exit design is STILL UNRULED** and Jack called it **critical**. Jack's split ruling:
  MUST-EXIT-WHEN-WHALE-EXITS, PAPER lane on price / LIVE lane on signal. `/positions` is paginable (T1 fixed the
  paper side); `/activity` gives per-fill SELLs but truncates at offset 5000; no per-wallet push (poll-only,
  tens-of-seconds-to-2-min latency). The exact live-exit mechanism is undecided. See `PM_OPEN_TICKETS_2026-08-27.md`
  D1 + E1.

### ★ Jack's PARALLEL-TEST intent (the shape Stage 3 is heading toward)
A **Jack-MLB sub-division that copies the SAME whales as the legacy `poly_kalshi_mlb` division** (which runs on
**Karen's** account), run **side by side**. **Legacy is NOT shut down first** — they run in parallel to compare.
**The whale list has NOT yet been pulled from the legacy config** — that is the **cheap prep step** the next
session can do (read the legacy division's whale roster; do NOT edit legacy — `poly_kalshi_mlb` is out of scope).

## 5. STAGE 4 (Search) — blocked on two Jack rulings
- **Q3 — the rank-before-backfill circularity** (rank after backfill, or discovery-time inline compute?).
- **Q2 — the definition of a "trackable" whale** (the selection rule Search filters on).
Both `PM_STATE_REVIEW §9`. Stage 4 also carries R2 (category-level exclusion for newly-discovered whales — a row
flag is not enough) and migration 010 (`pm_search_run`). Ingest STAYS all-categories (R5).

## 6. OPEN TICKETS
- **No alerting on the four unattended cron jobs** — a silently-failing job is invisible (log-monitor deferred).
- **The single-writer `flock` guard is COSTED but UNBUILT** — schedule spacing is the only overlap protection
  (Jack ruled: no guard for now). Watch item #2 in the 05:00 checklist is its first real test.
- **T2 — tier-2 categorization gap** — priority LOWERED (measured ~0 miss on the live open book; ~6.3% estimate).
- **mlb slug/endDate mismatch** (`mlb-sf-atl-2026-06-18` vs gamma `endDate 2026-09-07`, same condition_id) —
  behaviour is CORRECT (adjudicator keys off condition_id); open observation, **do not chase**.

## 7. STANDING BOX QUIRKS (all four — now in `PM_REBUILD_PLAN` §top)
1. `pytest_ethereum` broken → box-scratch pytest with `-p no:pytest_ethereum` (+ copy `pyproject.toml` +
   `tests/conftest.py` for `asyncio_mode=auto`).
2. `az vm run-command` SERIALIZES (`run command in progress` = retry) + TRUNCATES stdout; root restarts go through
   `az` because azureuser `sudo -n` FAILS.
3. tar deploys land **664** (git-archive), not 644 → `chmod 644` + assert perms in the gate (recurs every deploy).
4. **`ssh`/`scp`/`az` "not found" = a 32-bit PowerShell process** (`powershell -ep bypass -f` sometimes launches
   x86 → `System32` redirects to `SysWOW64`, no OpenSSH). Resolve via PATH → `System32\OpenSSH` →
   **`Sysnative\OpenSSH`** → Git `usr\bin`, invoke by full path; or run from a 64-bit shell. (Found this session.)

## 8. OPERATING RULES this workstream runs under
- Report a **SHA for every commit**. Verify on the box; **never narrate** — if you didn't observe it, say so.
- **No live step without Jack's explicit per-step authorization.** Build → box-scratch green → HALT.
- **Never edit legacy** — `poly_kalshi_mlb`, MACE, PEAD, bitunix, the trading engine are OUT OF SCOPE.
- **`origin/prod-live` advances by FAST-FORWARD only** — no amend/rebase/force. **`95e78c4` must remain reachable**
  (MACE forks from it).
- **★ Box access goes through the `.ps1` runner JACK executes (the sanctioned channel).** Do NOT bypass with
  ad-hoc direct agent SSH/az — even read-only. Agent tools stay local (git in the worktree, hashing, validation).
- The cadence is LIVE — do NOT run the poller/adjudicator/rollup manually.

## 9. HONEST LIMITS (stated, not hidden)
- **No live LOST paper case has ever been exercised** — the first losing resolution is its first live exercise
  (watch item #3 of the 05:00 checklist).
- **`/activity` truncates at offset 5,000** — large whales (BetMechanic/nba ~6,782 decisions) exceed it; Stage 5
  stamps a measured completeness bound per whale.
- **The CLOB websocket was never reachability-tested from the box** (E1; DNS didn't resolve — likely egress/
  allowlist, same class as the Kalshi geo-block). No push-based whale-exit signal without the whale's private key.

## 10. Where things live
- Requirements (governs): `PM_REQUIREMENTS.md`. Plan + quirks: `PM_REBUILD_PLAN_2026-08-26.md`.
- Per-phase records: `STAGE2_PHASE{1,2,3}_*` + `STAGE2_PHASE{1,2,3}_RUNG1_DEPLOY_2026-08-28.md`.
- Runners (Jack executes): `cc\pm_stage2_p3_rung1_{pre,deploy,restart,post}.*`, `cc\pm_wrap_snapshot_ro.*`.
- Deploy custody uses `git hash-object` blobs; the box == `origin/prod-live @ 7220e32`.
