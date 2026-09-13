# LEGACY PREDICTION-MARKET RETIREMENT — LIVING TRACKING DOCUMENT

> **This is the single source of truth for the whole multi-session retirement effort.**
> Every later agent UPDATES ROWS IN THIS FILE instead of writing a fresh report.
> Machine-update discipline: change a row's `Status` / `VER` / `Ruling` in place; append new rows; never delete a row (strike it `~~…~~` if voided). Keep the anchors at the top current.

- **Phase:** 1 — READ-ONLY INVESTIGATION (this document + the plan). Nothing retired/removed/disabled/changed.
- **Created:** 2026-09-13 (filename keeps the 2026-09-12 label from the charter).
- **Author (session 1):** code agent, branch `legacy-pm-retire-2026-09-13` (worktree `cc-legacy-pm-retire-wt`).
- **Charter:** retire EVERYTHING legacy prediction-market OUT OF THE CODEBASE, preserving code in GitHub. Live PM division (`prediction_markets/`, pm_web, 30 armed subs) is NOT retired and trades real money.

---

## 0. LIVE ANCHORS (keep current every session)

| Anchor | Value (VERIFIED 2026-09-13T14:23–14:30Z) | How verified |
|---|---|---|
| **origin/prod-live tip** | `5e03b7a27de4ca4ebdc5339d7721b5dbb9bbae62` | `git rev-parse origin/prod-live` |
| Engine service | `trading-corp.service` PID **370246**, active, NRestarts=**0**, up since 2026-09-12 21:00:28Z | `systemctl show` |
| PM schema head | **23** (next free migration = **024**) | `schema_version MAX(version)=23` in `data/prediction_markets.db` |
| Arm baseline (do-no-harm) | **31 arm rows** = `arm:global` + 30 subs (kalshi_jack + kalshi_karen), **armed=31, latched=0, trigger=0** | `recon_arm_read` / `legpm_inventory_ro` |
| Legacy DB size | `data/trading_corp.db` = **5.270 GB** | `os.path.getsize` |
| PM DB size | `data/prediction_markets.db` = **393.6 MB** | `os.path.getsize` |
| Open real-money legacy positions | **ZERO** (all round-trips resolved; `poly_kalshi_mark_live`=0) | money-safety read |

### ⚠ CHARTER PROMPT WAS STALE — corrected here (Jack confirmed "go with what your query tells you is origin"):
| Charter said | Actual (verified) |
|---|---|
| prod-live @ `93b5e908` | `5e03b7a2` (93b5e908 was the 2026-09-12 reconcile point; prod-live fast-forwarded through the PM deploys since) |
| schema head 21, next free 022 | **head 23, next free 024** |
| kcv2 lab DB "~228 MB" | **407 MB** (`kcv2_lab.db`, still-accrued corpus) |
| poly_kalshi_mlb "COMPLETELY HALTED, not armed" | (Phase 1) config armed + WIRED live but DURABLY persist-halted → 100% `blocked_halt`. **(Phase 2) halt row read: `halt_reason="operator_disarm_2026-09-01"` — a deliberate operator disarm (not a loss-cap trip). NOW ALSO `enabled:false` (2026-09-13). Two independent stops.** See ANOMALY-1 §7 + §14. |
| kcv2 observer "recorded PID 679 up since 2026-08-27" | **VERIFIED still active: PID 679, since 2026-08-27 15:25:45Z, still writing kcv2_* to the prod engine DB.** |

---

## 1. PHASE LIST (status)

| Ph | Name | State | Restart? | Disarm? | Reversible |
|---|---|---|---|---|---|
| **1** | **Read-only investigation → this doc + plan** | **DONE (this session)** | no | no | n/a |
| 2 | Disable-first (config `enabled:false`, hot-reload) for all still-scanning legacy loops + stop legacy systemd timers | **PARTLY DONE 2026-09-13 (session 2)** — config disables DONE+proven (5 flips); timers **BLOCKED-ON-PRIVILEGE → handed to Jack** (runner ready); kcv2-observer disable **deferred** (not in Jack's Phase-2 scope). See §14. | no (hot) | no | YES (restore backup/flags) |
| 3 | Archive-then-drop kcv2 data (lab DB + prod `kcv2_*` tables) — the ~3.15 GB win | NOT STARTED | no | no | archive=yes; drop=NO |
| 4 | Cancel/park paid + legacy-only APIs (Apify, the-odds-api, Finnhub-dead) after their divisions are disabled | NOT STARTED | no | no | YES (re-provision KV) |
| 5 | Code removal — LEGACY-ONLY files (strategies/data/brokers/scripts) + graft the shared `main.py` wiring block-by-block | NOT STARTED | **YES ×1** | **YES (poly_kalshi_mlb window only)** | git-revert |
| 6 | Shared-file surgical edits (`brokers/kalshi.py` discovery, `web/data.py`/`web/routes.py` PM sections, resolvers) | NOT STARTED | YES (fold into Ph5 window) | as Ph5 | git-revert |
| 7 | Archive/branch/tag disposition of removed code + final DB shrink verification | NOT STARTED | no | no | n/a |

> The plan (§9) is a RECOMMENDATION; Jack rules scope + sequence. Phases are sized to one agent-session each and each ends in an indefinitely-safe state.

---

## 2. THE FENCE (PART 4) — must NOT be touched by ANY later phase

**Survivors (never in scope):** bitunix_futures, bitunix_sfp, coinbase_spot/donchian, robinhood_mace (MACE), robinhood_pmcc (PMCC), robinhood_pead (PEAD), robinhood_joint_iron_condor / tasty_options_iron_condor, fidelity_options, and the **LIVE Prediction Markets division**.

**Hard fence (verified live-PM surface):**
- Everything under `trading_corp/prediction_markets/` (30 files — see §8) and `trading_corp/prediction_markets/web/` (pm_web).
- `data/prediction_markets.db` (28 tables, schema head 23) and its migrations. **Next PM migration = 024** (never reuse ≤23).
- The **live arm rows** in `data/trading_corp.db`: `agent_state WHERE agent='pm_live' AND key LIKE 'arm:%'` (31 rows) — DO NOT touch.
- `pm_live_driver` wiring in `main.py` (lines ~1554–1643) and `pm_cli.py` crons (paper-poll `*/30`, refresh `0 5`, adjudicate `40 5`, rollup `50 5`).
- **SHARED modules** (import-graph proven, §6-B) — any edit is a graft with every survivor's block proven intact by count+hash: `main.py`, `persistence/db.py`, `brokers/base.py`, `brokers/robinhood.py`, `agents/data_exec.py`, `brokers/kalshi_live.py`, `brokers/kalshi.py`, `data/kalshi_whale_stats.py`, `data/polymarket_data_api_client.py`, `data/polymarket_whale_audit.py`, `data/mlb_poly_kalshi_match.py`, `data/sports_team_mapping.py`, `web/data.py`, `web/routes.py`.
- **SHARED credentials** (§10): Anthropic, Kalshi (jack + karen keypairs), Coinbase, Bitunix, Tastytrade, Robinhood, Fidelity, Telegram, EODHD. Never propose removing these.
- **SHARED tables** in `data/trading_corp.db`: `agent_state`, `audit_event` (1.94 GB, all divisions), `proposed_order`, `schema/checkpoints/writes` infra. Any table drop is a live-division question (§8, RULING-DB).

---

## 3. OPEN RULINGS FOR JACK (each is a row in §11; consequences there)

| RID | Ruling needed | Recommendation |
|---|---|---|
| RULING-KCV2LAB | Disposition of the **407 MB local lab DB** (`cc-2026-08-02-wt/.../kcv2_lab.db`) — irreplaceable, NOT in git | Archive (copy to durable store) before any worktree prune; retain read-only |
| RULING-KCV2PROD | Disposition of the **~3.15 GB `kcv2_*` tables** in the shared prod engine DB (forward-logger corpus, still growing) | Dump-to-archive then DROP → reclaims ~60% of the 5.27 GB DB |
| RULING-POLYWALLET | **Polymarket wallets hold real USDC** (arb wallet + copy wallet). Code retirement does NOT drain them | Drain via `swap_pol_to_usdc.py` (separate op) BEFORE removing wallet keys; keys+funds are legacy-only |
| RULING-PKMLB | poly_kalshi_mlb is config-armed + wired-live but persist-halted. It shares the KAREN Kalshi account with live PM | Explicitly `enabled:false` (disable-first) so a halt-clear can't resume placement; retire its OWN files; KEEP shared kalshi_live/kalshi/kalshi_whale_stats |
| RULING-DB | Every legacy table drop touches the DB the live division reads arm from | Do drops in the Ph5/6 restart window only, on a backed-up DB, arm rows untouched; VACUUM is Jack's action |
| RULING-APIFY | Apify (~$160–200/mo) is legacy-only but STILL being called by systemd timers today | Stop `watchlist-stats`/`watchlist-deep` timers (Ph2) → then cancel Apify subscription (Ph4) |
| RULING-KALSHIMAP | `kalshi_market_map.py` is legacy-only in usage but lazily referenced by SHARED `brokers/kalshi.py` | Keep the file OR remove the discovery method in `kalshi.py` as a graft; low urgency |
| RULING-SHAREDDATA | 3 data modules are legacy-authored but now imported by live PM (`kalshi_whale_stats`, `polymarket_data_api_client`, `polymarket_whale_audit`) | Reclassify as live-PM-owned; DO NOT delete; they leave legacy scope |
| RULING-COINALYZE | `coinalyze-api-key` / `coingecko-demo-key` KV secrets are not read by `trading_corp/` (kcv2 research loaders + a possible sfp_cockpit sidecar) | Verify sidecar ownership before cancel; Coinalyze appears free-tier |

---

## 4. HOW THIS WAS VERIFIED (evidence index)

Read-only runners authored + executed this session (all `mode=ro`, no writes, no restart), outputs in `cc/_recon_scratch/`:
- `legpm_inventory_ro.ps1`/`.sh` → `_recon_scratch/legpm_inventory.txt` — DB sizes, dbstat, arm re-read, agent_state, table counts, kcv2, PM DB, systemd, cron.
- `legpm_wiring_ro.ps1`/`.sh` → `_recon_scratch/legpm_wiring.txt` — box config values, engine boot-log wiring, systemd timers, unit ExecStarts, audit recency.
- `legpm_money_ro.ps1`/`.sh` → `_recon_scratch/legpm_money.txt` — poly_kalshi_order classification, round-trip resolved counts, would_have_placed exposure.
- Reused pattern from proven `recon_arm_read_ro.ps1`. Prior baseline `_recon_scratch/arm_read.txt` (2026-09-12, schema 21) is superseded.
- Local code/import-graph: Glob/Grep/Read in worktree `cc-legacy-pm-retire-wt` (prod-live checkout) + 3 sub-agent sweeps (module inventory, API inventory, kcv2 out-of-engine surface).

`VER` column legend: **V** = command+output seen this session; **U** = unverified (settling action noted).

---

## 5. PART 1 — DIVISION INVENTORY (rows)

Type legend: code / config / table / cron / service / task(win) / state-row / api / worktree.

> **This table is the Phase-1 BASELINE snapshot. For CURRENT status of any touched row, see the §14 Phase-2 delta table.** Phase-2 (2026-09-13) changed: DIV-02/03/04/09/11 → `enabled:false` (config, done); DIV-14/15/16/17 timers → stop+disable pending Jack (§14.3).

| ID | Division / unit | Type | Scope | Status (verified) | Phase | Evidence | VER | Rev | Ruling? |
|---|---|---|---|---|---|---|---|---|---|
| DIV-01 | **polymarket_arbitrage** (LLM-divergence Polymarket) | code+config | LEGACY-ONLY | `enabled:false` (CLOSED 2026-08-06); PAPER (ReadOnlyBroker); loop wired-but-no-op; 483 RTs all resolved; last scan 2026-08-06 | 2/5 | strategies.yaml; main.py:1442; whp last 2026-08-06 | V | Y | — |
| DIV-02 | **polymarket_copy_trader** = **PCT PAPER FARM** (div `polymarket_copy_trading`) | code+config+state | LEGACY-ONLY | `enabled:true auto:false` = PAPER, **ACTIVELY POLLING now** (whale_state @14:22, whp 19,084 last 2026-09-13T14:28); 15,542 RTs resolved; ~60 whale_state rows + pinned/selected/watch_only | 2/5 | main.py:1465; agent_state dump; whp | V | Y | — |
| DIV-03 | **kalshi_tail_price_arb** (div `kalshi_arbitrage`) | code+config | LEGACY-ONLY | `enabled:true auto:false` PAPER; part of kalshi_arbitrage; 585 arb RTs resolved | 2/5 | strategies.yaml:1401; main.py:1700 | V | Y | — |
| DIV-04 | **kalshi_temporal_bucket_arb** (div `kalshi_arbitrage`) | code+config | LEGACY-ONLY | `enabled:true auto:false` PAPER; **still scanning** (whp 621 last 2026-09-10) | 2/5 | strategies.yaml:1441; main.py:1722; whp | V | Y | — |
| DIV-05 | **kalshi_sports_arb_observer** (div `kalshi_arbitrage`) | code+config | LEGACY-ONLY | `enabled:false`; read-only observer, never emits | 2/5 | strategies.yaml:1674; main.py:1819 | V | Y | — |
| DIV-06 | **kalshi_llm_arbitrage** (own div) | code+config | LEGACY-ONLY | strategy `enabled:true auto:false`; **division `enabled:false` (R7.e 2026-08-29)**; whp 4,387 last 2026-08-29 (stopped); 3,184 RTs resolved; uses Anthropic | 2/5 | divisions.yaml:258; main.py:1744 | V | Y | — |
| DIV-07 | **kalshi_weather** (`kalshi_weather_arb`) | code+config | LEGACY-ONLY | `enabled:false` (dead 2026-05-29); PAPER; weather APIs all free; whp last 2026-05-30; 1,222 RTs resolved; residual/nbm tables 0 rows | 2/5 | strategies.yaml:1523; main.py:1763 | V | Y | — |
| DIV-08 | **kalshi_crypto** (ORIGINAL, `kalshi_crypto_arb`) | code+config | LEGACY-ONLY | `enabled:false` (SHELVED 2026-05-29); PAPER; Coinbase spot; whp last 2026-05-30; 790 RTs resolved. **NOT kcv2 (DIV-13)** | 2/5 | strategies.yaml:1577; main.py:1782 | V | Y | — |
| DIV-09 | **kalshi_sports_scout** (standalone observer, no division) | code+config | LEGACY-ONLY | `enabled:true`; read-only, never emits; the-odds-api free tier | 2/5 | strategies.yaml:1615; main.py:1800 | V | Y | — |
| DIV-10 | **kalshi_copy_trader** (div `kalshi_copy_trading`) | code+config+state | LEGACY-ONLY | strategy `enabled:true auto:true`; **division `enabled:false` (R7.e)**; 89 live placements (last 2026-08-14, **all settled**, 3,765 RTs 0 unresolved); **Apify (PAID) still called via timers today**; whp 7,363 last 2026-09-11 | 2/4/5 | divisions.yaml:306; main.py:1835 | V | Y | RULING-APIFY |
| DIV-11 | **poly_kalshi_mlb** (own div) | code+config+state | LEGACY code / SHARES KAREN acct + 3 shared files | config `enabled:true auto:true` + **WIRED live (dry_run=False)** + **durably persist-halted → 100% `blocked_halt`, places nothing**; 64 RTs resolved, **0 open** (`mark_live`=0). See ANOMALY-1 | 2/5 | boot log 2026-09-12 21:00:48; money read | V | Y | RULING-PKMLB |
| DIV-12 | Legacy resolvers/aux: `kalshi_resolver`, `polymarket_resolver`, `poly_kalshi_marks`, equity-snapshot loops (llm/weather/crypto) | code | LEGACY-ONLY (not imported by live PM) | background tasks wired in main.py:2308–2432; run for the legacy round-trip books | 5/6 | main.py:2308,2346,2350 | V | Y | — |
| DIV-13 | **kalshi_crypto_v2 (kcv2)** — observer + research | service+code+table+task+worktree | LEGACY-ONLY (data corpus SHARED-DB) | **observer `trading-corp-kcv2-observer.service` PID 679 ACTIVE, still writing `kcv2_*` to prod DB**; ~3.15 GB in prod DB; 407 MB local lab DB; 2 local Win tasks broken. See §8, RULING-KCV2* | 2/3/5 | systemctl; dbstat; agent-3 | V | Y | RULING-KCV2LAB, RULING-KCV2PROD |
| DIV-14 | Legacy systemd timer: **trading-corp-pct-pruner** | service+timer | LEGACY-ONLY | root; daily 11:30Z; `prune_stale_pct_entries.py --apply`; last run success 2026-09-13 | 2 | systemctl cat/list-timers | V | Y | — |
| DIV-15 | Legacy systemd timer: **trading-corp-watchlist-stats** | service+timer | LEGACY-ONLY (**Apify PAID**) | root; daily 12:00Z; `refresh_kalshi_watchlist_stats`; **last run FAILED (exit 1)** 2026-09-13 | 2/4 | systemctl | V | Y | RULING-APIFY |
| DIV-16 | Legacy systemd timer: **trading-corp-watchlist-deep** | service+timer | LEGACY-ONLY (**Apify PAID**) | root; Sun 14:00Z; `seed_kalshi_watchlist_deep`; last run success 2026-09-13 14:10 (wrote agent_state) | 2/4 | systemctl | V | Y | RULING-APIFY |
| DIV-17 | Legacy systemd timer: **trading-corp-pm-watchlist-deep** | service+timer | LEGACY-ONLY | root; Sun 13:00Z; `seed_polymarket_watchlist_deep` (PCT); last run success 2026-09-13 | 2 | systemctl | V | Y | — |
| DIV-18 | kcv2 local Windows task **\TradingCorp\kcv2-ladder-snap** | task(win) | LEGACY-ONLY | Ready; daily 08:00 local; **BROKEN (exit 99, procgov missing) since ~2026-08-20, 0 new rows** | 2 | Get-ScheduledTask (agent-3) | V | Y | — |
| DIV-19 | kcv2 local Windows task **\TradingCorp\kcv2-fine-flow** | task(win) | LEGACY-ONLY | Ready; every 12h; **BROKEN (exit 99) since ~2026-08-21, 0 new rows** | 2 | Get-ScheduledTask (agent-3) | V | Y | — |
| DIV-20 | Inactive/failed legacy services (no timer or superseded): `trading-corp-pct-pruner.service`(dead between runs), `trading-corp-watchlist-stats.service`(failed) | service | LEGACY-ONLY | oneshot units triggered by DIV-14/15 timers | 2 | systemctl | V | Y | — |

**Enumeration note (charter: "find divisions he forgot"):** the full set of engine-wired legacy loops = DIV-01…DIV-12 + resolvers, cross-checked against `main.py` `_scheduled_*` wiring (grep, 200 hits) AND `strategies.yaml`/`divisions.yaml` — no additional PM loop found. `kalshi_sports_scout` (DIV-09) has NO division slug (standalone). `kalshi_arbitrage` is one division hosting THREE strategies (tail, temporal_bucket, sports_arb_observer). The out-of-engine surface (DIV-13…DIV-19) is the part a scoped/engine-only inventory misses.

---

## 6. FILE-LEVEL OWN-vs-SHARED SPLIT (import graph = evidence)

### 6-A. LEGACY-ONLY files (retirable; own code) — VERIFIED by reverse-import graph

**Strategies** (`trading_corp/agents/strategies/`): `polymarket_arbitrage.py`, `polymarket_copy_trader.py`, `kalshi_tail_price_arb.py`, `kalshi_temporal_bucket_arb.py`, `kalshi_llm_arbitrage.py`, `kalshi_weather_arb.py`, `kalshi_crypto_arb.py`, `kalshi_sports_scout.py`, `kalshi_sports_arb_observer.py`, `kalshi_copy_trader.py`, `poly_kalshi_copy_trader.py`, `poly_kalshi_executor.py`, `roster_split.py`, `kalshi_crypto_v2_observer.py`.
**Strategy helpers:** `_polymarket_prompts.py` (poly-arb + kalshi-llm, both legacy), `_sports_math.py`, `_weather_math.py` (weather+crypto, both legacy), `_whale_autopause.py` (kalshi_copy + poly_copy, both legacy).
**Data** (`trading_corp/data/`): `kalshi_apify_client.py`, `kalshi_matchable.py`, `polymarket_whale_stats.py`, `crypto_spot_provider.py`, `crypto_vol_provider.py`, `weather_forecast.py`, `weather_stations.py`, `metar_client.py`, `open_meteo_client.py`, `iem_cli_client.py`, `residual_logic.py`, `nbm_client.py`, `odds_api_client.py`.
**Brokers:** `brokers/polymarket.py`, `brokers/polymarket_live.py` — **VERIFIED LEGACY-ONLY** (zero imports under `prediction_markets/`; live PM never places on Polymarket).
**Agents:** `agents/kalshi_resolver.py`, `agents/polymarket_resolver.py`, `agents/poly_kalshi_marks.py`, `agents/polymarket_whale_analyst.py`, `agents/research/polymarket_whale_audit_cache.py`.
**Web:** `web/kalshi_crypto_vol_v2.py`.
**Scripts** (`trading_corp/scripts/` + top-level `scripts/`): `refresh_kalshi_whales`, `refresh_kalshi_watchlist_stats`, `seed_kalshi_watchlist`, `seed_kalshi_watchlist_deep`, `refresh_polymarket_whales`, `seed_polymarket_watchlist_deep`, `analyze_polymarket_whale`, `prune_stale_pct_entries`, `kalshi_demo_smoke`, `kalshi_demo_validate`, `migrate_kcv2_tables.py` (in `scripts/`), plus ~25 top-level `scripts/*` research/patch/backfill/probe files (weather_*, kalshi_*, patch_*, backfill_*, ingest_nbm, ingest_iem_cli_residuals). Full list in `_recon_scratch` sub-agent output.
**Config:** legacy blocks in `strategies.yaml` (12 blocks) + `divisions.yaml` (8 slugs) + `config/weather_stations.yaml` + `config/kalshi_watchlist_seed.yaml` + `config/pm_seed_wallets.yaml` (verify).

### 6-B. SHARED — CANNOT delete (import graph proves live-PM / survivor dependency)

| File | Legacy user | Survivor importer (evidence) | VER |
|---|---|---|---|
| `brokers/kalshi_live.py` | poly_kalshi_executor, kalshi_demo_* | **live PM**: execution.py, live_driver.py, boot_reconcile.py, shard_balance.py, subdivision.py, db.py; main.py:1567 | V |
| `brokers/kalshi.py` (KalshiBroker) | all legacy kalshi strategies | `kalshi_live.py` extends it → live PM transitive | V |
| `data/kalshi_whale_stats.py` | kalshi_copy + refresh scripts | **live PM** `prediction_markets/stats.py:18` (pm_whale_score ranking) | V |
| `data/polymarket_data_api_client.py` | poly_copy, poly_kalshi, refresh scripts | **live PM** search_run.py:61, web/app.py:448/994 | V |
| `data/polymarket_whale_audit.py` | poly_copy tooling | **live PM** analyze.py (pm_web Analyze feature) — docstring+feature; confirm exact runtime import at removal | V(docstring)/U(import line) |
| `data/mlb_poly_kalshi_match.py` | poly_kalshi_copy_trader, poly_kalshi_executor | **live PM** live_driver, execution, market_describe, web/feed_mlb, web/live_view | V |
| `data/sports_team_mapping.py` | kalshi_sports_scout, kalshi_sports_arb_observer | **live PM** market_describe, live_view, feed_mlb | V |
| `web/data.py`, `web/routes.py` | legacy PM dashboard funcs/routes | main non-PM dashboard + survivors | V |
| `main.py`, `persistence/db.py`, `brokers/base.py`, `agents/data_exec.py` | legacy wiring/models | ALL divisions | V |

### 6-C. NUANCED — `kalshi_market_map.py`
LEGACY-ONLY in usage (only legacy kalshi arb/llm/sports discovery), **but referenced by SHARED `brokers/kalshi.py` via a lazy in-method import** (`kalshi.py:402`, "imported here to avoid load-time dependency"). It is NOT pulled into live PM at load time. To delete the file you must also remove `KalshiBroker`'s discovery method (a shared-file graft). → RULING-KALSHIMAP.

### 6-D. FALSELY-CLEAN GRAPH GUARD (charter warning)
The reverse-import sweep was run with explicit patterns per module and cross-checked against `main.py` wiring + `prediction_markets/` imports; no path-prefix filter was applied that could zero out edges. The three "data modules that look legacy but are shared" (6-B rows 3–7) are exactly the trap — a filename-only read would have deleted them. They are RULING-SHAREDDATA (reclassify to live-PM-owned; leave legacy scope).

---

## 7. ANOMALIES (surfaced, not auto-resolved)

**ANOMALY-1 — poly_kalshi_mlb "halted" reconciliation (RESOLVED, precise):** Charter said "COMPLETELY HALTED, not armed." Verified reality: config `enabled:true auto_execute:true` (arm switch ON) and the current engine WIRED it LIVE (`dry_run=False`, boot log 2026-09-12 21:00:48). BUT the executor's `_is_halted()` reads `StrategyState.from_persistence("poly_kalshi_mlb").halted` (durable, cross-process). It is TRUE → every entry returns `status=blocked_halt` (400/400 sampled, most recent today 13:13). So it places nothing (correct in effect). The halt row is `agent_state WHERE agent='strategy_state' AND key='poly_kalshi_mlb'` — NOT under `agent='poly_kalshi_mlb'` (my first dump filtered the wrong agent → the classic measurement bug; the SECOND read via behavior caught it). **Consequence for the plan:** poly_kalshi_mlb is NOT cleanly disabled — the config arm switch is ON, and a halt-clear (daily-loss reset / manual un-halt) could resume live placement. → RULING-PKMLB: set `enabled:false` in disable-first so removal never races a resume.

**ANOMALY-2 — kcv2 has a SECOND, larger data store nobody flagged:** charter named the 407 MB local lab DB. The kcv2 OBSERVER (still active) writes to the SHARED prod engine DB: `kcv2_quotes` alone = **2.12 GB / 11.97 M rows**, kcv2_* total ≈ **3.15 GB** = ~60% of the 5.27 GB DB. Both stores are irreplaceable data. → RULING-KCV2PROD.

**ANOMALY-3 — Apify still billing despite division disabled:** `kalshi_copy_trading` is `enabled:false` (R7.e), yet the `watchlist-stats` (daily) + `watchlist-deep` (weekly) systemd timers still invoke Apify-backed scripts (agent_state `apify_visibility_cache` updated 2026-09-13T14:10). So the paid subscription is still being consumed. → RULING-APIFY (stop timers in Ph2 BEFORE cancelling in Ph4).

---

## 8. DATABASE INVENTORY + THE PERFORMANCE WIN

`data/trading_corp.db` = **5.270 GB** (SHARED: holds legacy tables AND live-PM arm state). Top consumers (dbstat, VERIFIED):

| Table/index | Bytes | Class |
|---|---|---|
| `kcv2_quotes` | 2,123.8 MB (11.97 M rows) | **kcv2** |
| `audit_event` | 1,702.2 MB | SHARED (all divisions) |
| `kcv2_quotes_mkt/_ts/_cycle` (idx) | 538.2 + 210.1 + 152.6 MB | **kcv2** |
| `ix_audit_event_*` | 141.4 + 93.6 MB | SHARED |
| `proposed_order` | 65.2 MB | SHARED (all divisions) |
| `kcv2_signals` (+idx) | 45.3 + 18.6 + 15.3 MB | **kcv2** |
| `kcv2_index_ticks` (+idx) | 26.5 + 9.3 + 7.6 MB | **kcv2** |
| `bitunix_bar_history` (+idx) | 26.5 + 8.0 + 7.9 MB | SURVIVOR |
| `polymarket_round_trips` | 12.1 MB | legacy result |
| `kalshi_round_trips` | 11.4 MB | legacy result |
| `kalshi_equity_history` | 9.0 MB | legacy result |
| `kcv2_heartbeat` (+idx) | 3.5 + 1.9 MB | **kcv2** |
| `polymarket_equity_history` | 2.1 MB | legacy result |

**Legacy row counts (VERIFIED):** kcv2_quotes 11,975,052 · kcv2_signals 887,760 · kcv2_index_ticks 443,880 · kcv2_heartbeat 110,970 (last row active, state='ok') · kalshi_round_trips 9,610 (all resolved) · polymarket_round_trips 16,025 (all resolved) · kalshi_equity_history 135,020 · polymarket_equity_history 35,447 · weather_forecast_residuals 0 · weather_nbm_observations 0 · poly_kalshi_mark_live/history 0.

**THE WIN (quantified):** dropping the `kcv2_*` tables+indexes (after archive) reclaims **~3.15 GB (~60%)** of the engine DB and stopping the observer stops the growth. Legacy result tables (round_trips/equity) add ~35 MB more. `audit_event` (1.94 GB) is SHARED — its legacy-actor rows could be pruned but that is a targeted DELETE, not a table drop (RULING-DB). Net feasible shrink ≈ **3.15–3.2 GB → engine DB from 5.27 GB to ~2.1 GB.**

`data/prediction_markets.db` = 393.6 MB, 28 tables, schema head 23 — **FENCED** (live PM). Tables: pm_account, pm_analysis_cache, pm_analysis_cost, pm_category_*_stats, pm_closed_position, pm_driver_heartbeat, pm_driver_task_heartbeat, pm_loss_grounding_cache, pm_meta, pm_open_position, pm_opposed_marker, pm_paper_*, pm_roster, pm_score_snapshot, pm_search_run, pm_shard_balance_snapshot, pm_subdivision*, pm_watchlist, pm_whale, pm_whale_score, schema_version.

### Out-of-engine surface (the part scoped inventories miss)
- **kcv2 observer service** `trading-corp-kcv2-observer.service` (box): User=azureuser, `ExecStart=… -m trading_corp.agents.strategies.kalshi_crypto_v2_observer`, PID 679, ACTIVE since 2026-08-27. ReadWritePaths=`…/data`. Writes kcv2_* to prod DB.
- **`infra/systemd/trading-corp-kcv2-observer.service`** (repo file, only in `cc-2026-08-02-wt`).
- **Local lab DB** `C:\Users\AA Incorporado\cc-2026-08-02-wt\research\kalshi_crypto_v2\lab\kcv2_lab.db` = 407 MB, last write 2026-08-21. Tables: lab_bars_binance 397,512 · lab_bars_coinbase 397,477 · lab_coinalyze 3,093,856 · lab_kalshi_candles 416,953 · lab_kalshi_ladder_snap 62,948 · lab_kalshi_markets 26,104 · lab_coverage 24 · lab_features/labels/results 0. **NOT in git; irreplaceable.** RULING-KCV2LAB.
- **kcv2 schedule dir** `research/kalshi_crypto_v2/schedule/` (health.ps1, register_tasks.ps1, run_accrual.ps1, SCHEDULING_RUNBOOK.md) + `research/kalshi_crypto_v2/{lab,loaders,s4}/`.
- **Local Win tasks** DIV-18/19 (both BROKEN since ~08-20; procgov missing).

---

## 9. PART 2 — API / SUBSCRIPTION INVENTORY (rows)

`secrets.py` = `trading_corp/utils/secrets.py`, loaded at boot (`load_secrets()` from KV `kv-tc-vtwbowt3wtkpy`).

| ID | Service | Scope | Credential (env / KV) | Loaded at boot? | What breaks if cancelled | Action | VER |
|---|---|---|---|---|---|---|---|
| API-01 | **Anthropic** | SHARED-ALL | `ANTHROPIC_API_KEY` | YES (assert_live_ready) | RiskAgent (all), PMCC, MACE, pm_analyze (live PM), research, kalshi_llm | **NEVER CANCEL** | V |
| API-02 | **Kalshi** (jack + karen keypairs) | SHARED-LIVE-PM | `KALSHI_API_KEY_ID`/`_PRIVATE_KEY_PEM`, `KALSHI_KAREN_*` | YES | Live PM driver (jack+karen subs), pm_web marks/milestones | **NEVER CANCEL** | V |
| API-03 | **Polymarket data-api** (keyless) | SHARED-LIVE-PM | none | n/a | Live PM whale positions/search; poly_copy; poly_kalshi | KEEP | V |
| API-04 | **Polymarket wallets** (arb + copy) + Polygon RPC | LEGACY-ONLY | `POLYMARKET_PRIVATE_KEY`/`_FUNDER_ADDRESS`, `POLYMARKET_COPY_PRIVATE_KEY`/`_FUNDER_ADDRESS`, `POLYGON_RPC_URL` (embedded Alchemy key) | YES | Only polymarket_arbitrage / polymarket_copy_trading order brokers (both legacy). Live PM never places on Polymarket | **Drain funds first (RULING-POLYWALLET), then remove keys in Ph4** | V |
| API-05 | **Apify** (saswave scrapers) | LEGACY-ONLY | `APIFY_API_TOKEN` | YES | Only kalshi_copy_trading (+watchlist timers) | **PAID ~$160–200/mo → CANCEL Ph4** (stop timers Ph2 first) | V |
| API-06 | **the-odds-api.com** | LEGACY-ONLY | `ODDS_API_KEY` | YES | Only kalshi_sports_scout + kalshi_sports_arb_observer | Free tier (500/mo); remove key Ph4, no $ saving | V |
| API-07 | **Finnhub** | DEAD | `FINNHUB_API_KEY` | YES (loaded, never read) | Nothing — superseded by EODHD; zero code reads it | **CANCEL + remove KV (free win)** | V |
| API-08 | **Coinalyze** | LEGACY (kcv2 research) | `coinalyze-api-key` (KV only; not read by `trading_corp/`) | no (research loaders + possible sfp_cockpit sidecar) | kcv2 fine-flow accrual (already broken) | Verify sidecar owner (RULING-COINALYZE); appears free-tier | V/U |
| API-09 | **CoinGecko demo** | UNKNOWN sidecar | `coingecko-demo-key` (KV only) | no (not in `trading_corp/`) | unknown OI-recorder sidecar | Verify before cancel (RULING-COINALYZE) | U |
| API-10 | Weather: NWS/weather.gov, Open-Meteo, METAR (aviationweather), NBM (NOMADS), IEM (mesonet) | LEGACY-ONLY | none (all free, no auth) | no (lazy in kalshi_weather_arb) | Only kalshi_weather | No subscription to cancel | V |
| API-11 | EODHD | SHARED-SURVIVOR (PEAD) | `EODHD_API_KEY` | YES | PEAD earnings dates/EPS | KEEP | V |
| API-12 | Coinbase, Bitunix, Tastytrade, Robinhood, Fidelity, Telegram, yfinance, cfbenchmarks(via Kalshi ws), MLB StatsAPI/ESPN | SHARED-SURVIVORS | resp. KV keys | YES | survivors + live PM feeds | KEEP | V |

> cfbenchmarks (kcv2 observer's index feed) rides the SHARED Kalshi WS with KALSHI-KAREN RSA-PSS auth — no separate subscription.

---

## 10. MONEY-SAFETY (outranks tidiness — VERIFIED)

**No abandoned open real-money positions in any legacy PM division.**
- poly_kalshi_mlb: `poly_kalshi_mark_live`=0; 64 RTs, 0 unresolved; all entries `blocked_halt`. Shares KAREN Kalshi with live PM. **U:** a KAREN `get_positions` venue read (authenticated, service-env) would be authoritative but returns BOTH poly_kalshi AND live-PM karen positions — persisted `mark_live=0` is the cleaner poly_kalshi-specific signal. Recommend a ticker-filtered venue confirm in the Ph5 disarm window.
- kalshi_copy_trading: 3,765 RTs, 0 unresolved; 89 live placements last 2026-08-14, all settled. No open positions.
- All other kalshi/polymarket legacy divisions: paper (would_have_placed only) or resolved RTs (kalshi 9,610 / polymarket 16,025 — all resolved).
- **Real money that EXISTS (not a position):** Polymarket wallets hold USDC (arb + copy) — RULING-POLYWALLET. Drain is a separate op from code retirement.

---

## 11. PART 4 — TANGLES NEEDING JACK'S RULING (rows; each an options+consequence)

| RID | Tangle | Options | Consequence / recommendation |
|---|---|---|---|
| RULING-KCV2LAB | 407 MB local lab DB, irreplaceable, not in git | (a) archive to durable store then retain read-only; (b) retain in place, exclude from worktree prune; (c) delete | **(a).** git preserves code, NOT this data. Deleting loses 26,104 markets + 34k→63k ladder snaps + 3.1 M flow rows permanently. Do NOT fold into a general "remove kcv2" step. |
| RULING-KCV2PROD | ~3.15 GB `kcv2_*` in shared prod DB, still growing | (a) stop observer + dump-to-archive + DROP tables (VACUUM by Jack) → reclaim ~60%; (b) stop observer, keep tables; (c) keep writing | **(a)** for the performance win, but archive the forward corpus first (it complements the lab DB). DROP is irreversible → Jack rules. |
| RULING-POLYWALLET | Polymarket arb + copy wallets hold USDC | (a) drain via swap_pol_to_usdc.py before key removal; (b) leave funds, remove code only | **(a).** Removing keys without draining strands funds. Legacy-only keys → safe to remove after drain. |
| RULING-PKMLB | poly_kalshi_mlb config-armed+wired-live but halt-blocked; shares KAREN acct + 3 shared files | (a) enabled:false (disable-first) then retire own files, keep shared; (b) leave as-is | **(a).** Its OWN files (poly_kalshi_copy_trader/executor/marks, roster_split) are retirable; kalshi_live/kalshi/kalshi_whale_stats/mlb_poly_kalshi_match/sports_team_mapping STAY (live PM). |
| RULING-DB | Every legacy table drop touches the DB the live division reads arm from | (a) drops only in Ph5/6 restart window on a backed-up DB, arm rows untouched; (b) defer all drops | **(a).** Back up DB first; never touch `agent_state` arm rows; VACUUM is Jack's action. |
| RULING-APIFY | Apify paid, legacy-only, still called by timers | (a) stop watchlist-stats/deep timers (Ph2) → cancel subscription (Ph4); (b) cancel now (timers then error) | **(a).** Stop the callers first so the cancel is clean. |
| RULING-KALSHIMAP | kalshi_market_map legacy-only usage, lazily referenced by shared brokers/kalshi.py | (a) keep the file (harmless); (b) remove file + strip KalshiBroker discovery method (graft) | **(a)** low urgency; (b) only if a clean sweep is wanted. |
| RULING-SHAREDDATA | 3 data modules legacy-authored but now imported by live PM | (a) reclassify as live-PM-owned, never delete; (b) attempt to fork | **(a).** `kalshi_whale_stats`, `polymarket_data_api_client`, `polymarket_whale_audit` leave legacy scope. |
| RULING-COINALYZE | coinalyze/coingecko KV keys not read by trading_corp | (a) verify sfp_cockpit/OI-recorder sidecar owner before any cancel; (b) cancel | **(a).** Likely free-tier / survivor sidecar; do not cancel blind. |

---

## 12. PART 3 — PHASED PLAN (recommendation; Jack rules scope + sequence)

**Disarm posture (charter question):** SCALPEL, not blanket. This investigation is read-only (Ph1) — needs none. Disable-first phases (Ph2) hot-reload — need none. Only the code-removal restart window (Ph5/6) needs a disarm, and only **for poly_kalshi_mlb** (the sole legacy division that is config-armed + wired-live). The live PM 30 subs stay armed throughout — do NOT blanket-disarm them (re-arming 30 subs is 30 verified state writes and stops new copies but does not flatten open positions). Recommend: leave live PM armed; disable poly_kalshi_mlb via `enabled:false` in Ph2 (hot, no disarm needed) so Ph5 needs no separate disarm step at all.

**Do-no-harm protocol (every phase that touches anything):** identical read-only baseline before+after, diffed. Baseline invariants (this session): 31 arm rows / armed=31 / latched=0 / trigger=0; `trading-corp` PID 370246 NRestarts=0; PM schema head 23; no new fills. Runners: `recon_arm_read_ro.ps1`, `recon_liveness_ro.ps1`, plus this session's `legpm_inventory_ro.ps1`.

| Ph | Changes | Reversible | Restart? | Disarm? | Proof | Rollback |
|---|---|---|---|---|---|---|
| **2. Disable-first** | `enabled:false` on the still-scanning legacy strategy blocks in `strategies.yaml` (poly_kalshi_mlb, polymarket_copy_trader, kalshi_tail_price_arb, kalshi_temporal_bucket_arb, kalshi_sports_scout; llm/weather/crypto/sports_arb already false). Disable+stop legacy systemd timers (pct-pruner, watchlist-stats, watchlist-deep, pm-watchlist-deep). `systemctl disable --now trading-corp-kcv2-observer` (stops kcv2_* growth). Disable the 2 local Win tasks. | **YES** (restore flags/units) | **NO** (hot-reload; timers/units are independent of engine) | NO | whp counts stop advancing; timers show disabled; kcv2_heartbeat stops; arm baseline unchanged | restore YAML `enabled:true`, `systemctl enable --now`, `Enable-ScheduledTask` |
| **3. kcv2 data disposition** | Archive lab DB (RULING-KCV2LAB) + dump prod `kcv2_*` (RULING-KCV2PROD). No drop yet. | archive=YES | NO | NO | archive checksums; row counts match | delete archive copies |
| **4. API cancel/park** | After Ph2: cancel Apify (API-05); cancel/remove Finnhub (API-07, free win); remove ODDS key (API-06); drain+remove Polymarket wallets (API-04, RULING-POLYWALLET). KV secret removal only. | YES (re-provision KV) | NO | NO | callers already disabled (Ph2); no boot error (keys read lazily except wallets — confirm engine boot clean) | re-add KV secrets |
| **5. Code removal (LEGACY-ONLY)** | Remove 6-A files; remove their `main.py` wiring blocks + resolver wiring **as a graft** (every survivor + the interleaved `pm_live_driver` block proven intact by count+hash — this is the 2026-09-04-class highest-risk step); remove legacy `strategies.yaml`/`divisions.yaml` blocks. | git-revert | **YES ×1** | poly_kalshi_mlb already disabled in Ph2 → **no separate disarm** | boot log shows pm_live_driver WIRED (17+15 cats) + all survivors; suite green; arm baseline unchanged | restore files + restart |
| **6. Shared-file surgical edits** | Strip PM sections from `web/data.py`/`web/routes.py`; optionally kalshi_market_map/KalshiBroker discovery (RULING-KALSHIMAP). Fold into the Ph5 restart window. | git-revert | (same window) | (same) | dashboard renders survivors; pm_web unaffected | restore |
| **7. Disposition + DB shrink** | Tag/branch removed code (see below); after Jack's DROP of kcv2_* + legacy tables (RULING-DB) on a backed-up DB, VACUUM (Jack) → verify ~2.1 GB. | tag=permanent | NO (drops need no engine restart; PM migrations do not run on restart) | NO | DB size drop measured; arm rows intact | restore from DB backup |

**Restart windows:** exactly **ONE** (Ph5/6 folded). Everything else is hot or out-of-engine. A restart bounces every division incl. bitunix (24/7) ~3.5 min — Jack warns co-tenants and runs it.

**Code preservation (charter: keep in GitHub, don't delete into nothing):** recommend **(a)** a signed tag `legacy-pm-preserved-<date>` on the pre-removal commit (cheapest revival: `git checkout <tag> -- <path>`), AND **(b)** an `archive/legacy-pm` branch pushed to origin (keeps the code reachable + browsable without cluttering prod-live). Directory-move (to `attic/`) is worse — it still ships the files to the box on deploy. Removal-with-history (git rm) + the tag gives the cleanest prod tree with full revival. Note git preserves CODE only — NOT the kcv2 lab DB (RULING-KCV2LAB) nor `agent_state` rows.

**Migrations:** schema head 23; PM next-free = 024. Legacy table drops need NO migration/DDL bump (they are DELETEs/DROPs, not schema versioned) and PM migrations do not run on engine restart (need explicit init_db) — so a legacy drop cannot collide with a PM migration number. Never reuse a number ≤23.

**Engine gain (quantified):** tasks removed = ~11 legacy asyncio loops + 3 resolver/mark loops + 3 equity-snapshot loops (~17 background tasks) → fewer per-cycle API calls (Polymarket data-api, Kalshi list_markets, the-odds-api, Anthropic-for-llm), less memory, no kcv2 observer process. DB: 5.27 GB → ~2.1 GB (~60%). API $: Apify ~$160–200/mo + Finnhub eliminated.

---

## 13. NEXT-SESSION PICKUP

A later agent should: (1) re-run the do-no-harm baseline (`recon_arm_read_ro.ps1` + `legpm_inventory_ro.ps1`) and confirm anchors in §0; (2) get Jack's rulings on §3/§11; (3) execute the chosen phase, updating the Status/VER/Ruling cells of the affected rows in §5/§9/§11 in place. Do NOT write a separate report. Verify branch tips yourself (CR-stripped compare). Everything in §2 is fenced. **Current state = §14 (Phase 2 log).**

---

## 14. PHASE 2 EXECUTION LOG — 2026-09-13 (session 2, hot & reversible only)

Scope executed: Item 1 (poly_kalshi_mlb `enabled:false`), Item 3 (remaining hot config disables), Item 2 (legacy timers). All box writes were config-only or systemctl; **no restart, no code copy, no DB write beyond the config file, no deletion.** Runners preserved under `reports/platform/legacy_pm_ro_runners/legpm_p2_*`.

### 14.1 DO-NO-HARM (before/after, PASSED)
Identical baseline via `recon_arm_read_ro.ps1` + `recon_liveness_ro.ps1` (snapshots `_recon_scratch/*_p2_before.txt` / `*_p2_after.txt`).

| Invariant | Before (15:00Z) | After (15:13Z) | Verdict |
|---|---|---|---|
| Arm rows / armed / latched / trigger | 31 / 31 / 0 / 0 | 31 / 31 / 0 / 0 | UNCHANGED |
| PM schema head | 23 | 23 | UNCHANGED |
| `trading-corp` PID / NRestarts | 370246 / 0 | 370246 / 0 | UNCHANGED (no restart) |
| `prediction-markets-web` PID / NRestarts | 381803 / 0 | 381803 / 0 | UNCHANGED |
| `sfp-card-watcher` PID / NRestarts | 656 / 0 | 656 / 0 | UNCHANGED |
| Live PM heartbeats | 32, all `state=evaluated`, fresh | 32, all `state=evaluated`, fresh | HEALTHY |
| Live PM open unsettled positions (jack/karen) | 202 / 149 | 202 / 149 | UNCHANGED (still holding) |
| Live PM filled non-dry (jack/karen) | 391 / 283 | 391 / 283 | UNCHANGED |

**Live PM is PLACING (not merely armed) — the 2026-09-04 failure-mode check passes** (fresh `state=evaluated` heartbeats + per-account cycles ~1–29s + 351 open live positions held). VERIFIED.

### 14.2 ITEM 1 + ITEM 3 — CONFIG DISABLES (DONE + PROVEN)
Runner `legpm_p2_disable.ps1/.sh` (box write, atomic, backed-up, parse+fence validated). **Backup:** `/home/azureuser/trading_corp/config/strategies.yaml.bak_phase2disable_20260913T150430Z` (104,516 B). **Restore command (all 5 flips):** `cp /home/azureuser/trading_corp/config/strategies.yaml.bak_phase2disable_20260913T150430Z /home/azureuser/trading_corp/config/strategies.yaml` (hot-reloads back; no restart).

5 top-level `enabled: true → false` (comments preserved; YAML re-validated; asserted `pm_live_driver.enabled` stayed `true` BEFORE writing):

| DIV | block | line | hot? | proof | VER |
|---|---|---|---|---|---|
| DIV-11 | `poly_kalshi_mlb` (Item 1) | L1786 | **NO — wire-gated** (main.py:1489; different class). Effective at next restart (prevents re-wire). Running loop still gated by the operator persist-halt. | halt row present + UNTOUCHED: `agent='strategy_state' key='poly_kalshi_mlb' {"halted":true,"halt_reason":"operator_disarm_2026-09-01"}` (read-only). Second independent stop added at the restart boundary. | V |
| DIV-02 | `polymarket_copy_trader` (PCT farm) | L1759 | **YES** | empirical: whale_state froze at `15:03:39` across 96s window (loop stopped scanning within 1 cycle) | V |
| DIV-03 | `kalshi_tail_price_arb` | L1402 | YES (same `enabled`→`_reload` mechanism) | code-verified (`enabled` property calls `_reload()`) + shares the proven pattern | V |
| DIV-04 | `kalshi_temporal_bucket_arb` | L1442 | YES | same | V |
| DIV-09 | `kalshi_sports_scout` | L1616 | YES | same | V |

Already-`enabled:false` (recorded, NO action per charter): DIV-01 polymarket_arbitrage, DIV-07 kalshi_weather_arb, DIV-08 kalshi_crypto_arb, DIV-05 kalshi_sports_arb_observer.
Left untouched per charter ("needs no action"), but see ANOMALY-P2-2: DIV-06 kalshi_llm_arbitrage + DIV-10 kalshi_copy_trader — **strategy-level `enabled:true`** in strategies.yaml (only the *division* is R7.e-disabled in divisions.yaml).

### 14.3 ITEM 2 — LEGACY TIMERS (VERIFIED; execution BLOCKED-ON-PRIVILEGE → handed to Jack)
Ownership + Apify + live-PM check (runner `legpm_p2_timers_probe_ro`): all 4 are legacy, **none serves the live PM division**; no cron re-triggers them.

| DIV | timer | script | Apify? | serves live PM? | new status |
|---|---|---|---|---|---|
| DIV-15 | `trading-corp-watchlist-stats.timer` (daily 12:00Z) | `refresh_kalshi_watchlist_stats` | **YES (6 refs)** — billing driver | NO | still active/enabled — **STOP+DISABLE pending Jack** |
| DIV-16 | `trading-corp-watchlist-deep.timer` (Sun 14:00Z) | `seed_kalshi_watchlist_deep` | **YES (11 refs)** — billing driver | NO | still active/enabled — **pending Jack** |
| DIV-17 | `trading-corp-pm-watchlist-deep.timer` (Sun 13:00Z) | `seed_polymarket_watchlist_deep` | NO (polymarket free data-api) — writes agent_state `polymarket_copy_trader` (legacy PCT, **not** live PM) | NO | still active/enabled — **pending Jack** |
| DIV-14 | `trading-corp-pct-pruner.timer` (daily 11:30Z) | `prune_stale_pct_entries` | NO (DB-only) | NO | still active/enabled — **pending Jack** |

**Why blocked:** stopping root system units needs root. `sudo -n systemctl stop trading-corp-*.timer` → **"a password is required"** (rc=1) for all 4 (no-op; timers unchanged — `is-active=active`, `is-enabled=enabled` confirmed after). Passwordless sudo is unavailable ([[prod-sudo-constraint-no-password]]); the sudoers NOPASSWD `systemctl stop trading-corp*` entry is shadowed by a trailing `(ALL) ALL` rule (last-match-wins). Per this phase's "az-root read-only only" rule I did NOT perform the root *write* via `az vm run-command`.
**HANDOFF (Jack, reserved):** run `powershell -ep bypass -f .\legpm_p2_timers_disable_JACK.ps1` (az-root `systemctl disable --now` the 4 timers = stop + reboot-persistent). **Rollback:** `legpm_p2_timers_reenable_JACK.ps1` (`enable --now`). This stops the Apify billing driver (DIV-15/16); DIV-14/17 are legacy non-billing but pointless now.

### 14.4 ANOMALIES surfaced this session
- **ANOMALY-P2-1 (privilege):** the box's `sudo` NOPASSWD allowlist for `systemctl {stop,start,restart} trading-corp*` is effectively dead — shadowed by a trailing `(ALL) ALL` rule → every sudo needs a password. Root writes must go through `az vm run-command` (as Jack's restart does). A later agent cannot stop/disable box system units over ssh.
- **ANOMALY-P2-2 (division-vs-strategy enable):** the R7.e ruling set the *division* `enabled:false` (divisions.yaml) for kalshi_llm_arbitrage + kalshi_copy_trading, but their *strategy* blocks (strategies.yaml) are still `enabled:true`. The in-engine kalshi_copy loop last polled Apify 2026-09-11 (now dormant) and kalshi_copy still emits paper `would_have_placed` (last 2026-09-11). Left untouched per charter, but a later phase should flip these two strategy blocks `enabled:false` for a clean, belt-and-suspenders disable (recommend folding into Ph5/the code-removal phase). kalshi_copy_trader `auto_execute:true` remains latent (harmless while the division has no broker, but re-enabling the division would arm it live).

### 14.5 RULINGS — still OPEN (unchanged) + Phase-2 pending Jack actions
All §3/§11 rulings remain OPEN (kcv2 lab-DB + prod-tables disposition; Polymarket USDC drain **must GATE key removal, not follow it**; DB-backup-before-drops). New Jack action items from Phase 2: (a) run `legpm_p2_timers_disable_JACK.ps1` to complete Item 2 (Apify billing stops on execution); (b) if desired, fold the box `strategies.yaml` disables into git/prod-live (the box now diverges from prod-live for the 5 flags — intended, reversible via the timestamped backup; the branch commit records but does not itself deploy).
