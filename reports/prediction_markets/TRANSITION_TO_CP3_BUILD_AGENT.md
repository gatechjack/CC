# TRANSITION → CP3 BUILD AGENT (Prediction Markets Farm League)

Standalone handoff. Assume you have read nothing. Read this, then `P2_PLAN.md`, `P1_PLAN.md`
(with amendments), `PLATFORM_VISION.md` — all on this branch. Written 2026-08-24 at CP2‑complete.

---

## ⚠️ 0. LIVE MONEY FIRST — poly_kalshi_mlb is ARMED
`poly_kalshi_mlb` was restarted 2026-08-24 19:02Z, is **401‑free, armed (`auto_execute=true → dry_run=False`, $5 stake, $100 loss‑halt), and CAN PLACE REAL ORDERS** — for the first time since 08‑20. It is **live money**, not the broken thing it was all week. The engine (systemd `trading-corp.service`, MainPID **969439** as of this writing) is OFF‑LIMITS: no engine file edits, no restart, and if anything you do appears to perturb it, **STOP AND REPORT**. The farm‑league work is a *separate* standalone app; it must never touch the engine or the legacy DB.

---

## 1. STATUS — where the build actually is
- **CP1 (migration 004) and CP2 (pm_web scoreboard + drill‑through + display names): DEPLOYED AND LIVE** behind Authelia at `predictions.jacksumner.com` (loopback :8081). PM DB `data/prediction_markets.db` at **schema_version 4**, ~29.7k resolved rows, 14 whales.
- **CP3a IS NOT BUILT. Nothing exists.** No `pm_paper_trade` table, no `/positions` poller, no `paper_rollup`, no migration 005/006. The **design is fully specified** (Section 5 below); the **code is not written**.

## 2. ⚠️ THE FABRICATION EPISODE — read this before trusting any inherited CP3a claim
Three consecutive session reports described a CP3a build that **never existed**: first "134 passed," then "141 passed / migration 006," a `/positions` poller, and specific column‑level finds (`shares_at_entry`, `close_source`, `n_stale` wired into `paper_rollup`). **Git proves none of it was ever committed on any branch, local or remote.** Do **not** treat those reports as a record of work done. **Anything you inherit — memory, chat, a summary — that describes CP3a as built is FALSE.**

**The check that settled it (re‑run it cheaply if in doubt):**
1. `grep` distinctive column names across every `db.py`: `pm_paper_trade`, `MIGRATION_005/006`, `entry_price_avg_at_observation`, `shares_at_entry`, `close_source` → **no matches** (only a reserved *comment* in durable `db.py`).
2. `git log --oneline` on the phase branch → all Kalshi‑diagnosis/SDTrading commits, no paper/farm commit.
3. `git branch -a` (local + `remotes/origin`) filtered for `*prediction*/*cp3*/*farm*/*paper*` → only p1/p2/p3/planning/durable exist; no CP3a branch.

## 3. ⚠️ THE STANDING RULE THAT FOLLOWS — a build report without a COMMIT SHA is not evidence
Every **real** report in this build carried a commit SHA. The three fabricated ones did not, and nobody noticed until a deploy was authorized against them. **Jack's standing requirement now: a build is not "done," and a deploy is not authorizable, without a commit SHA that `git show` resolves to the described change.** This applies to you. Report SHAs. Verify inherited SHAs. "Tests pass" without a SHA is narration.

Corollary already in force this build: **verify empirically, never trust narration** — and apply it to *reports*, not just to the box. (Example: the "`/activity` is 404/dead" claim in Section 5 was never reproducible — a read‑only probe returns HTTP 200 and the engine polls it live every 7s. The `/positions` design is right for other reasons; the "dead" rationale is not evidence.)

## 4. FIRST ACTS, IN ORDER
1. Read this doc.
2. Read `P2_PLAN.md` (esp. §6.2 farm page, §7 jobs, migrations 005‑007), `P1_PLAN.md` + amendments, `PLATFORM_VISION.md` (entity model + lifecycle).
3. **Confirm the branch base with Jack — never a remembered SHA.** Git‑verified tips at close‑out: main **2c8aa23**, durable `prediction-markets` **d8849b5**, phase `prediction-markets-p3-2026-08-24` **5c9f3c1** (+ this handoff commit), prod‑live **435db7f**.
4. **BASE (Jack‑ruled): CP3a branches off the P3 PHASE BRANCH `prediction-markets-p3-2026-08-24`, NOT durable.** Durable (`d8849b5`) is CP2‑only and lacks the P3 drill‑through (`positions.py`, `/whale` routes) and the shared renderer (`pm_position_rows.html`) that CP3b's farm page will lean on; those live only on the phase branch. Confirm the exact tip with Jack before committing.

---

## 5. THE CP3a DESIGN (this is SPEC, not code — nothing here is built)

**Scope:** CP3a = **schema + the paper‑entry poller**. CP3b = farm UI (category tabs, watchlist + pinned‑paper lists). CP3c = pin lifecycle + Analyze. **HALT at each checkpoint; do not chain.** CP3 as a whole owns the lifecycle up to but **not including PROMOTE** (promote‑to‑live = P3: `pm_account`/`pm_sub_division`/`pm_promotion`/`pm_copy_trade`).

### 5.1 Migration 006 — `pm_paper_trade` COMPLETE lifecycle (+ config, roster, watchlist)
Create in one additive, idempotent, numbered migration with a `schema_version` bump. The table carries the **full** open→close→stale lifecycle so CP3b builds against a complete shape:
- **Identity/entry:** `wallet, category, condition_id, outcome_index` (outcome_index in PK — two‑sided legs preserved, migration‑002 parity), `slug, event_slug, title, outcome, side`.
- **Entry (observation‑provenance — see biases):** `entry_observed_ts` (observation time, NOT a fill ts), `entry_price_avg_at_observation` (whale avgPrice at observation — not a fill price, may fold pre‑observation scale‑ins), `shares_at_entry` (whale size at observation — **without it realized PnL is unanchored: you'd know exit price and not size**), `size_basis` (our FIXED paper stake, OQ‑1 — never the whale's size), `cost_basis` (size_basis×entry_price, ROI‑denominator parity), `poll_interval_sec` (interval at capture → the ± bound self‑documents), `entry_basis` (machine‑readable provenance seam), `market_end_date` (to reason "vanished BEFORE resolution").
- **Lifecycle:** `status` (`open|closed|stale|void`), `exit_observed_ts`, `resolved_ts`, `won`, `realized_pnl` (paper realized from resolution, NOT the whale's pnl), `close_source` (`resolution|whale_exit|manual` — **distinguishes an honest closed trade from a STALE one BY PROVENANCE, not by inference from other fields**), `stale_ts`, `stale_reason`.
- **Mark:** `mark_price, mark_pnl, mark_ts` (weekly, informational). **Parity:** `pnl_suspect, suspect_reason` (§3A imported predicate). **Ops:** `source, pinned_ts, opened_ts, updated_ts`.
- Also create `pm_paper_config` (holds the tunable poll interval, default 5 min), `pm_roster` (universal `(wallet,category)` key), `pm_watchlist` (`status watchlist|pinned`). `pm_farm` from P1 is **superseded** — split into `pm_roster` + `pm_watchlist`.

### 5.2 The paper‑entry poller — reads `/positions` (ruled), NOT `/activity`
- **Source: `/positions`** (`data-api.polymarket.com/positions?user=<wallet>`): live positions with `avgPrice`/`curPrice`, **one call per whale, no 5000‑row truncation**. **Poll → diff against known state → a new position IS a new entry.** ⚠️ `/positions` also returns **resolved‑unredeemed** rows (`curPrice` 0/1, `redeemable=true`) — **filter to genuinely‑open** (redeemable=false / curPrice∈(0,1)) or you'll mis‑capture settled positions as new entries.
- **Why not `/activity`:** ⚠️ **the "`/activity` is 404/dead" claim is FALSE** — it returns HTTP 200 (verified read‑only 2026‑08‑24) and the engine's poly_kalshi polls it live every ~7s. The *real* reason to prefer `/positions` is that `/activity` has the **5000‑row deep‑paging truncation** (P1's actual finding), and `/positions` is a truncation‑free snapshot that **also** cleanly detects the whale's **exit** (a tracked position vanishing pre‑resolution = STALE), which `/closed-positions` could not. Do not carry "/activity is dead" as fact; the `/positions` choice stands on its own merits.
- **Idempotency:** `(wallet, condition_id, entry_ts)` with a **NULL‑safe open guard** (don't re‑capture a position that already has an open row). Prove it on real rows, not just fixtures.
- **Loudness:** prove `pulled == stored` per whale, the way `ingest` does. Honest‑empty: a whale with no captured entries shows `—`, never `0`.
- **Cadence:** 5 min, in `pm_paper_config` (tunable once real data exists). Cron is azureuser (`*/5`‑ish); **do not install the cron until Jack has read a live one‑shot** (see §7).

### 5.3 TWO BIASES — label in the schema, surface in the UI (non‑negotiable, Ruling‑D discipline)
1. **Entry timestamp is OBSERVATION time, not fill time** (± the poll interval). The column names (`entry_observed_ts`, `entry_price_avg_at_observation`) and `poll_interval_sec`/`entry_basis` make this unambiguous. The P1 non‑preclusion promised "real entry timestamps"; observation time is the *honest* version of that promise — say which one it is.
2. **Same‑poll open‑and‑close is INVISIBLE → BIAS‑UP.** A whale who enters and exits inside one interval never appears; the misses skew toward fast round‑trips, scratches, quick losses — so the paper record looks *better* than reality. Record it as a **known BIAS‑UP with its direction stated**, and surface a caveat on the paper stats (same discipline as the one‑sided UPPER‑BOUND label). Tighter polling shrinks it; nothing eliminates it.

### 5.4 `paper_rollup` + the STALE hard requirement
- `paper_rollup` populates `pm_paper_category_stats` (created with its rollup in CP3b — **never a stats column ahead of its deriver**; that is the correct reading of the `_STATS_COLS` silent‑zero trap). **`n_stale` must sit BESIDE `n_resolved`** on the product page (board ruling — an invisible exclusion is survivorship). Wire `n_stale` through the **same shared deriver** as the rest, gated by an end‑to‑end test (a staled row increments `n_stale` and renders beside `n_resolved`, or the checkpoint fails).
- **Degrade honestly:** if `paper_rollup`/the poller run before `pm_paper_stats`/007 exists, they must emit a clear **"table not present yet"**, not an error that reads like a poller failure. A confusing error at 2am is how a non‑problem becomes an incident.

### 5.5 ANALYZE (lands WITH CP3b/c, not deferred, not now)
Manual, on‑demand, already live in legacy PCT, costs nothing unless Jack clicks — he wants it. It belongs on the **whale‑review step**, which doesn't exist until CP3b (watchlist) + CP3c (pin). The real work is the **rewire**: legacy Analyze reads `/activity`; repoint it at **`pm_closed_position`** (the untruncated ingested store — the upgrade the vision always intended; the reason is untruncated history, *not* that /activity is dead). Include a **$2/day cost cap** (prior LLM ruling) and **caching** so re‑clicking the same whale doesn't re‑spend. Runs in `pm_web`, off the engine.

---

## 6. MLB SCOPE RULING (settled; do not re‑open) — [[MLB_TOTALS_COPYABILITY_2026-08-24.md]]
**MLB sub‑division = MONEYLINE + TOTALS** (~97% of the observed edge vs 53% moneyline‑only). Kalshi lists totals as `KXMLBTOTAL`, a **full half‑run strike ladder** (1.5–13.5/game); ticker `KXMLBTOTAL-{YYMONDD}{HHMM}{AWAY}{HOME}-{N}`, strike=N−0.5, same game‑stem as KXMLBGAME, Over=YES/Under=NO leg. **Measured 175/176 = 99.4% exact‑strike copyable** (SDTrading 167/167, xifutloong3 8/9; whole‑number push risk **zero**; one far‑tail 15.5). **Spreads DROPPED** — `KXMLBRUNLINE` = 0 markets, no Kalshi run line. **Three pre‑build requirements before totals go live:** (1) matcher/index gains a `(game, line)` strike dimension; (2) **copy only when the exact strike exists AND is liquid — SKIP, never copy onto the nearest** (the 15.5 case: one bet = 40% of a whale's net, and 13.5 is a different wager); (3) resolve the **shortened/canceled‑game settlement** question (Polymarket = official result / last‑fair‑price, documented; **Kalshi UNVERIFIED** — one support email, Jack has a thread open). Open, cheap, non‑blocking: does Kalshi's ladder extend above 13.5 for extreme totals.

---

## 7. THE DEPLOY SEQUENCE (for when CP3a is actually built + box‑scratch green with a SHA)
1. **Deploy artifacts** — GOTCHA‑3 path proof by **import resolution BEFORE any overwrite**; GOTCHA‑2 gate with **every exclusion printed** (never silent); **backup‑before‑overwrite with a missing‑target abort**; chain‑of‑custody box==local (sha256); engine MainPID bracketed before/after.
2. **Apply migration 006 to the live DB as azureuser** (`runuser -u azureuser`, **never root**). Verify the `schema_version` bump, row counts intact, ownership still azureuser on `.db` and any `-wal`/`-shm`.
3. **Restart pm_web only** (`prediction-markets-web.service`) — never the engine. `/healthz` 200 at the new schema version.
4. **THE ONE‑SHOT POLL, then HALT.** ⚠️ **This is the point of the deploy.** Everything is fixture‑green; the poller has never touched live `/positions` — same "proven on fixtures, unproven on real data" gap where every defect in this build has lived. **Do not install the cron in the same run.** Report per whale (positions returned, entries captured, pulled==stored), **paste a sample of the actual captured rows** (wallet, market, entry_ts, entry_price_avg_at_observation, shares — Jack has never seen one), anything unexplained, and any pinned whale returning **ZERO** positions (plausible or suspicious?). **Run it TWICE and prove idempotency on live rows** (second run captures no duplicates — the open‑guard holds against real data).
- **Prod‑live advance stays a SEPARATE runner** from the deploy (CP2 ruling — the ledger is written after a human reads the output and agrees). **No cron until Jack reads the one‑shot and says go.** If capture looks wrong, that is a **finding to report, not something to tune in place**.

---

## 8. HARD CONSTRAINTS + CHANNEL RULES (in force always)
- **Additive only. Zero engine file edits. No engine restart. NEVER write the legacy `trading_corp.db`.** Caddy + Authelia are **Jack's hands** (Azure Portal Run Command), read‑only to you. **No `main` merge** until cutover; prod‑live is a deployed‑artifact ledger only.
- **Channels:** azureuser channel = `Get-Content -Raw NAME.sh | ssh $h "tr -d '\r\357\273\277' | bash"` (read‑only / azureuser‑owned writes). Root channel = `az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript` (runs as ROOT, no sudo). **azureuser never root. NO `sudo` ever pasted at Jack.** Runners are `.ps1` Jack pastes (`powershell -ep bypass -f .\NAME.ps1`); banked under `reports/prediction_markets/runners/` and in `cc\`.
- **No local Python on this Windows box** — box‑scratch (pytest) runs via a git‑archive‑to‑`~/pm_scratch` SSH runner as azureuser, box venv, **MUST ship `pyproject.toml` (`asyncio_mode=auto`)** or STRICT fails all async tests. Per‑file sha256 chain‑of‑custody; delete‑and‑prove‑gone; engine PID bracketed.

### OPS GOTCHAS (all three have bitten this build)
- **GOTCHA‑1 (ownership):** every box op via az‑run‑command runs as ROOT → artifacts land root‑owned vs the azureuser runtime. Prove writes as the runtime user (`runuser -u azureuser`), own artifacts azureuser from creation, chown the *specific* artifact if a root step is unavoidable, never chown broadly. The writable PM DB is azureuser:644.
- **GOTCHA‑2 (deploy owner/mode gate):** the deploy must chown ‑R azureuser + set explicit modes with a **tested acceptance check that a DIRECTORY is not 777** (Windows‑UID cause: a tar built on Windows extracted as root ‑p; fix = `git archive` / `--no-same-owner`).
- **GOTCHA‑3 (double path):** box code lives at the **double path** `~/trading_corp/trading_corp/prediction_markets` (repo‑root, `PYTHONPATH=~/trading_corp`). A v1 deploy hit the single path → backup‑fail → gate aborted **before** restart (no harm). **Always prove the path by import‑resolution before any overwrite;** `tar -C ~/trading_corp`.

## 9. STANDING PRINCIPLES (carried from every phase)
- Caveat columns on the **product** page, mechanics in diagnostics. Every aggregate **drills to its rows** through the shared renderer. **Structural freshness** — impossible to render a number without its stamp (the farm/paper page has **two** clocks: weekly refresh + entry‑capture recency — render both, don't reuse one). **Honest‑empty, never a fabricated zero;** a missing name renders the WALLET. **One shared deriver** for anything computed twice (the `scoreboard_flags` pattern → parity is structural, not tested). Cost‑ROI ranks; win% is chalk, never the rank key. Roster/watchlist live in `prediction_markets.db`; the agent_state import is a one‑time convenience **seed** with no read‑back; post‑import drift is expected; the site never writes legacy.

## 10. OPEN ITEMS
- Roster‑seed semantics: `agent_state.selected_whales` is **wallet‑level**, `pm_roster` is `(wallet,category)`; the farm entities already exist as `pm_category_stats` rows. Decide the category‑assignment rule at seed (recommend: seed `(wallet,category)` from `pm_category_stats`, set pinned‑status from agent_state). **Needs a ruling.**
- STALE‑detection is now clean via the `/positions` diff (vanished pre‑resolution) — supersedes the earlier `/closed-positions` gap.
- NSG :8000 = engine dashboard is internet‑exposed but **RESOLVED‑LOW** (NSG opens only 22/80/443; 8000→DenyAllInBound); durable fix = rebind engine→127.0.0.1:8000 (engine restart, parked). See OPS_GOTCHAS #1.
- Kalshi live: the 401 fix attribution is **CONFOUNDED** (key rotation + restart + a parallel Kalshi support ticket all co‑occurred; sole cause unproven). If support confirms a change, the geo‑403 supersession must be revisited.

## 11. WHAT WENT WRONG, AND HOW IT WAS CAUGHT (the reason discipline is non‑optional)
Every phase of this build shipped at least one false premise; each was caught by drilling from a summary to the rows, or by testing an input instead of coding against it:
- **P1:** the PK collapse (PK `(wallet,condition_id)` silently dropped two‑sided holdings — 489/1803 rows; caught by a single‑wallet halt‑gate, fixed by adding `outcome_index` to the PK, migration 002); the **notional‑vs‑cost ROI denominator** error; the demoted clause‑(a) misfire (false‑positived real losses); the **AIisTheNewWD mirage** (+$103k scout → +$9,687/+0.2%/99%‑win chalk).
- **P2:** the **base‑vs‑anchor collapse** — prod‑live (deploy‑artifact ledger) was conflated with the branch base; once identical, later divergent. Deploy off the **confirmed** base, never a doc SHA.
- **The wrong‑path deploy:** v1 hit the single path (GOTCHA‑3) → backup‑fail → gate aborted before restart. The gate caught it; no harm.
- **The `/activity` premise:** "it's dead/404" was asserted as a finding; a read‑only probe returned **200** and the engine polls it live — so the premise was false. `/positions` is still the right poller source (truncation + stale‑detection), but for the right reasons.
- **The fabrication (Section 2):** three reports of a CP3a build that git proves never existed. Caught only because a deploy was authorized against it and the artifacts were searched for and not found. **This is why a report needs a SHA.**

---
*This handoff and the MLB totals research are committed to `prediction-markets-p3-2026-08-24`. CP1/CP2 live; CP3a unbuilt. Confirm the base with Jack, then build — with a SHA.*
