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
| **origin/prod-live tip** | ~~`5e03b7a2…`~~ → **`3dd15c1016279750be6faada68c6b558f5016b7a`** (session 8, 2026-09-16; advanced through Ph6 `8f35f254` + later PM/ITF deploys) | `git fetch; git rev-parse origin/prod-live` |
| **UNTANGLE PASS (session 8) branch** | `legacy-pm-untangle-2026-09-16` @ **`ed6d9b83`** (off prod-live `3dd15c10`; tranche-1 pure deletion of 49 files; **local, NOT pushed**). FF push = `git push origin legacy-pm-untangle-2026-09-16:prod-live`. Closure record = **§20**. | this session |
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
| 2 | Disable-first (config `enabled:false`, hot-reload) for all still-scanning legacy loops + stop legacy systemd timers + reconcile prod-live | **DONE 2026-09-13 (sessions 2+3)** — **8 config flips** done+proven (5 P2 + 3 P3); **4 legacy timers stopped+disabled by Jack, VERIFIED reboot-persistent**; **Apify billing fully ceased** (timers + in-engine kalshi_copy loop both off); box↔prod-live **reconciled** (commit `fcbcd4a7`, clean FF, **push pending Jack**); kcv2-observer disable **deferred** (not in Phase-2/3 scope). See §14 + §15. | no (hot) | no | YES (backups + reenable runner) |
| 3 (=Jack's "Phase 5") | Archive kcv2 data (lab DB + prod `kcv2_*` tables) — the ~3.16 GB win; **drop deferred to Ph7** | **DONE 2026-09-13 (session 5): LOCAL archive WRITTEN + VERIFIED PASS** (prod kcv2_* 278.6MB gz + lab 110.8MB gz; restore→row counts+schema+spot-check all match; checksums recorded §17.10). Ruled dest=both blob+local; **BLOB copy still owed (no storage account exists yet).** Observer STILL writing → Ph7 stops it first. See §17. | no | no | archive=yes; drop=NO (Ph7, now archive-authorized) |
| 4 | Cancel/park paid + legacy-only APIs (Apify, the-odds-api, Finnhub-dead) after their divisions are disabled | NOT STARTED | no | no | YES (re-provision KV) |
| 4.5 | **FULL-TREE DRIFT SWEEP** (pre-removal gate, read-only) | **DONE 2026-09-13 (session 4)** — box vs prod-live `fcbcd4a7`, both directions, byte-safe. **No unrelated runtime drift.** Only 2 known-benign non-runtime box-BEHIND files (test_pmcc_logic.py, BACKLOG.md — no action); 8 shared files IDENTICAL + baselined; 6 never-deployed KEPT files (2 survivor→owner); 1 unattributed scratch (`strategies.yaml.block_bs`). See §16. | no | no | n/a |
| 5-6 (=Jack's "Phase 6") | Code removal — **Option 1 (deploy main.py graft ONLY; defer ALL file deletions + web surgery)** per Jack's ruling. Deploy = 3 things: main.py loop graft + poly_kalshi `auto_execute:false` + 4 timer units removed. NO file deletions / web routes / test removal — the ~30-file DELETE set is a TRANSITIVE CLOSURE (2 survivor-coupled keepers found: `_weather_math`←path_logger, `kalshi_crypto_v2_observer`←PID 679) deferred to the untangle pass. | **DEPLOYED LIVE 2026-09-14 — prod-live `8f35f254` (FF); box main.py `c15b4de6`/3923 + config `1657119b`; engine 370246->397094; GATE PASS (WIRED 00:25:34 + placement order 820 00:28:53Z); 4 legacy timer units removed; siblings 381803/656/679 unchanged; 44 armed. See §18.7.** Revival SHA `fcbcd4a7`. | **YES ×1 (Jack) — DONE** | PM PLACING confirmed post-restart (split-signal gate PASS) | forward-revert FF; box backups `.bak_legpm_20260913T232311Z` |
| 6 | Shared-file surgical edits (`brokers/kalshi.py` discovery, `web/data.py`/`web/routes.py` PM sections, resolvers) | NOT STARTED | YES (fold into Ph5 window) | as Ph5 | git-revert |
| 7 | Archive/branch/tag disposition of removed code + final DB shrink verification | NOT STARTED | no | no | n/a |
| **8 (UNTANGLE)** | **Transitive-closure file deletion** (the §18.3 deferred work, finally computed) | **CLOSURE COMPUTED + TRANCHE-1 BUILT LOCAL 2026-09-16 (session 8).** AST import graph (query proven to detect known imports first); 785 .py analyzed. **Tranche 1 = 49 files pure-deleted** (branch `legacy-pm-untangle-2026-09-16` @ `ed6d9b83`, off prod-live `3dd15c10`, **local/NOT pushed**): 0 additions / 17,918 deletions, py_compile 736/0, import-clean 0 violations, §16.7 shared files byte-identical. Tranches 2/3 (13 shared-blocked keepers + 4 test-coupled + ~20 tranche-2 leaves + units/config + woven web dashboard) DEFERRED — need shared-file grafts. Full record **§20**. | **no (pure deletion; deploy = FF push, Jack, after 16:00 ET)** | no | forward-revert FF |

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
| ~~RULING-POLYWALLET~~ **VOID (Jack, 2026-09-16)** | ~~Polymarket wallets hold real USDC; code retirement does not drain them~~ | **CLOSED — NO WALLET ACTION NEEDED.** Jack confirmed the **Polymarket credentials TRANSFER TO THE LIVE PM DIVISION** (shared with a survivor) → funds are never stranded, no drain, no key removal. Polymarket wallet keys are now **NEVER-CANCEL** alongside Anthropic + Kalshi (see §9 API-04, §10). The old "drain gates key removal" constraint no longer exists — do not plan around it. |
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

> **This table is the Phase-1 BASELINE snapshot. For CURRENT status of any touched row, see the §14 (Phase 2) + §15 (Phase 3) delta tables.** Net as of 2026-09-13: DIV-02/03/04/09/11 → `enabled:false` (P2); DIV-06 kalshi_llm + DIV-10 kalshi_copy → `enabled:false` (+ kalshi_copy `auto_execute:false`) (P3, §15.2); DIV-14/15/16/17 timers → **stopped+disabled by Jack, verified** (§15.4); box↔prod-live reconciled via commit `fcbcd4a7` (FF push pending Jack, §15.3).

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
| API-04 | **Polymarket wallets** (arb + copy) + Polygon RPC | ~~LEGACY-ONLY~~ **→ SHARED-LIVE-PM (Jack ruling 2026-09-16)** | `POLYMARKET_PRIVATE_KEY`/`_FUNDER_ADDRESS`, `POLYMARKET_COPY_PRIVATE_KEY`/`_FUNDER_ADDRESS`, `POLYGON_RPC_URL` (embedded Alchemy key) | YES | ~~Only legacy order brokers~~ **Credentials TRANSFER to the live PM division (Jack) — funds never stranded** | **NEVER CANCEL** (RULING-POLYWALLET VOID; no drain) | V(ruling) |
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
- **Real money that EXISTS (not a position):** Polymarket wallets hold USDC (arb + copy). ~~RULING-POLYWALLET drain~~ **VOID (Jack, 2026-09-16): the Polymarket credentials transfer to the live PM division → funds are never stranded, no drain needed, keys are NEVER-CANCEL (§9 API-04, §3).**

---

## 11. PART 4 — TANGLES NEEDING JACK'S RULING (rows; each an options+consequence)

| RID | Tangle | Options | Consequence / recommendation |
|---|---|---|---|
| RULING-KCV2LAB | 407 MB local lab DB, irreplaceable, not in git | (a) archive to durable store then retain read-only; (b) retain in place, exclude from worktree prune; (c) delete | **(a).** git preserves code, NOT this data. Deleting loses 26,104 markets + 34k→63k ladder snaps + 3.1 M flow rows permanently. Do NOT fold into a general "remove kcv2" step. |
| RULING-KCV2PROD | ~3.15 GB `kcv2_*` in shared prod DB, still growing | (a) stop observer + dump-to-archive + DROP tables (VACUUM by Jack) → reclaim ~60%; (b) stop observer, keep tables; (c) keep writing | **(a)** for the performance win, but archive the forward corpus first (it complements the lab DB). DROP is irreversible → Jack rules. |
| ~~RULING-POLYWALLET~~ **CLOSED/VOID (Jack 2026-09-16)** | Polymarket arb + copy wallets hold USDC | ~~(a) drain before key removal~~ | **VOID — credentials transfer to the live PM division; no drain, keys NEVER-CANCEL. The "drain gates key removal" constraint no longer exists.** |
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
All §3/§11 rulings remain OPEN (kcv2 lab-DB + prod-tables disposition; Polymarket USDC drain **must GATE key removal, not follow it**; DB-backup-before-drops). New Jack action items from Phase 2: (a) run `legpm_p2_timers_disable_JACK.ps1` to complete Item 2 (Apify billing stops on execution); (b) if desired, fold the box `strategies.yaml` disables into git/prod-live (the box now diverges from prod-live for the 5 flags — intended, reversible via the timestamped backup; the branch commit records but does not itself deploy). **[Both done in Phase 3 — see §15: Jack ran the timer runner; the reconcile commit `fcbcd4a7` is ready to FF-push prod-live.]**

---

## 15. PHASE 3 EXECUTION LOG — 2026-09-13 (session 3, hot & reversible only)

Scope: Item 1 (close the P2-2 strategy-layer gap), Item 2 (reconcile box↔prod-live), Item 3 (verify timers + Apify cessation). No restart, no code, no deletion, no table touch, poly_kalshi_mlb persist-halt row NOT touched. Anchors re-verified: `origin/prod-live` still `5e03b7a2`, PM schema head `23`. Runners under `reports/platform/legacy_pm_ro_runners/legpm_p3_*`.

### 15.1 DO-NO-HARM (before/after, PASSED)
`_recon_scratch/*_p3_before.txt` / `*_p3_after.txt`.

| Invariant | Before (15:33Z) | After (15:44Z) | Verdict |
|---|---|---|---|
| Arm rows / armed / latched / trigger | 31 / 31 / 0 / 0 | 31 / 31 / 0 / 0 | UNCHANGED |
| PM schema head | 23 | 23 | UNCHANGED |
| `trading-corp` / `pm-web` / `sfp-card-watcher` PID·NRestarts | 370246·0 / 381803·0 / 656·0 | same | UNCHANGED (no restart) |
| Live PM open positions (jack/karen) | 203 / 151 | 204 / 151 | HEALTHY (ordinary trading; still PLACING) |

### 15.2 ITEM 1 — P2-2 config gap CLOSED (done + proven)
Runner `legpm_p3_disable.ps1/.sh` (box write, atomic, backup + parse + fence-validated). **Backup:** `/home/azureuser/trading_corp/config/strategies.yaml.bak_phase3disable_20260913T153724Z` (104,521 B). **Restore (reverts only the 3 P3 flips):** `cp` that backup back (hot). Flip time **2026-09-13T15:37:24Z**.

| block.field | was | now | hot? | VER |
|---|---|---|---|---|
| `kalshi_llm_arbitrage.enabled` | true | **false** | HOT (`enabled` prop calls `_reload()`; loop checks per-cycle) | V |
| `kalshi_copy_trader.enabled` | true | **false** | HOT | V |
| `kalshi_copy_trader.auto_execute` | true | **false** | HOT | V |

**What stopped each TODAY (established before flipping):**
- `kalshi_llm_arbitrage`: division-disabled ⇒ no broker registered ⇒ loop no-ops at the broker check; idle since 2026-08-29 (whp last 08-29). Does NOT call any paid API. `enabled:false` = belt-and-suspenders. 0 unresolved RTs.
- `kalshi_copy_trader`: **★ LIVE FINDING (reported before change):** strategy `enabled:true` ⇒ loop was RUNNING and **still calling Apify every ~10 min** (journal: "apify open_positions fetch failed HTTP 400 … 106 consecutive … FEED DOWN" at 15:23 + 15:33 today). It was NOT placing (0 unresolved RTs, no live placement since 2026-08-14, feed-down + division has no live broker) — running-but-not-placing. The earlier "dormant since 09-11" read was a **measurement trap**: `last_poll_ts` only updates on Apify *success*, masking the ongoing failed calls. `enabled:false` (hot) stopped the loop ⇒ Apify calls ceased (§15.4).

**Same-shape sweep (VERIFIED full matrix, `legpm_p3_investigate_ro`):** after P2+P3 the only strategy blocks with a still-`true` arm flag are — `pm_live_driver.enabled=true` (**the SURVIVOR; correct, fenced**) and `poly_kalshi_mlb.auto_execute=true` (**residual; harmless — `enabled:false` is wire-gated so the loop won't re-wire at restart, and the running loop is operator-persist-halted; NOT in Item-1 scope → reported, not changed**). Every other legacy strategy block = `enabled:false, auto_execute:false`. Divisions.yaml keeps `enabled:true` on most legacy divisions (cosmetic "division-on / strategy-off" — registers the tile; not a live-money layer). No further dangerous strategy-on-while-believed-off found.

### 15.3 ITEM 2 — box↔prod-live RECONCILE (commit ready; Jack FF-pushes)
Built LOCALLY on a worktree off `origin/prod-live` (`cc-prodlive-p2p3-reconcile-wt`, branch `prodlive-config-reconcile-2026-09-13`). Applied the box's `strategies.yaml` (all 8 P2+P3 flips) → **commit `fcbcd4a7`**, parent `5e03b7a2` = current `origin/prod-live` ⇒ **clean fast-forward**.
- **Proof git==box:** reconciled file CR-stripped md5 `98cbde83` == box `98cbde83`. `git diff` vs prod-live = **exactly the 8 flag lines** (7 `enabled` + 1 `auto_execute`, all `true→false`); nothing else; `pm_live_driver` untouched.
- **No other drift:** byte-safe CR-stripped md5 of all 12 `config/*.yaml` — **11 SAME box==prod-live**, only `strategies.yaml` differs (the intended edits). (An initial all-12-differ result was a measurement bug — PowerShell mangling em-dashes when capturing `git show`; recomputed byte-safe from checked-out files.) A full `trading_corp/` code-tree drift sweep was NOT done (out of Phase-3 scope; recommend before Phase-5 code removal).
- **Push command (Jack, FF; prod-live protected by ruleset 22485002 — FF allowed, force refused):** from `C:\Users\AA Incorporado\cc`: `git push origin prodlive-config-reconcile-2026-09-13:prod-live`

### 15.4 ITEM 3 — timers + Apify cessation (VERIFIED)
Jack ran `legpm_p2_timers_disable_JACK.ps1` (az-root). Verified (`legpm_p3_investigate_ro`):
- All 4 timers `active=inactive`, `enabled=disabled`; `timers.target.wants` symlinks **removed** (reboot-persistent); none in the active-timers list. Services not running (watchlist-stats.service in `failed` = residue of its last pre-disable run; won't re-trigger).
- **Apify CEASED (VERIFIED, `legpm_p3_apify_cease_ro`):** 0 kalshi_copy Apify lines since the 15:37 flip; last-ever failure `15:33:39` (pre-flip); the ~15:43 poll was skipped. Both Apify callers now off (timers + in-engine loop). No cron re-triggers (verified). ⇒ **Apify billing driver fully stopped.** (Subscription CANCEL is still Ph4/Jack — RULING-APIFY.)

### 15.5 ANOMALIES (Phase 3)
- **ANOMALY-P3-1 (measurement):** `kalshi_copy_trader.last_poll_ts`=09-11 falsely implied "dormant"; the journal showed it calling Apify every 10 min (failing). *last-success timestamps are not liveness.* Caught by reading the journal, not the state row.
- **ANOMALY-P3-2 (measurement):** local md5 of `git show` output mangled non-ASCII (em-dashes) → false all-config-drift alarm; byte-safe recompute (checked-out files, `ReadAllBytes`, CR-strip) cleared it.

### 15.6 RULINGS / PENDING
Still OPEN (carried forward): kcv2 data disposition (3.15 GB prod `kcv2_*` + 407 MB lab DB — irreplaceable, not in git; **archive + verify readable BEFORE any drop**); Polymarket USDC drain (**must GATE key removal, not follow it**); DB backup before any drop; P2-1 (inert sudo allowlist — root writes via az only). Pending Jack: **FF-push `fcbcd4a7` to prod-live** (§15.3). kcv2 observer still RUNNING (deferred). poly_kalshi_mlb `auto_execute:true` residual (harmless) — clean up in Ph5.

---

## 16. PHASE 4 EXECUTION LOG — 2026-09-13 (session 4): FULL-TREE DRIFT SWEEP (READ-ONLY)

Purpose: give the code-removal phase a proven starting picture so it never discovers unrelated drift mid-graft. Anchors re-verified: `origin/prod-live` = **`fcbcd4a7`** (Jack's reconcile landed), working branch pushed = `71251355`, PM schema head `23`. Method: byte-safe CR-stripped md5 manifests, box overlay vs prod-live, **both directions**. Runner `legpm_p4_boxmanifest_ro`; git-side manifest via git-bash; evidence under `reports/platform/p4_sweep_evidence/` (box_manifest, git_manifest, cmp_*). **Sweep validated** (it detected 2 real content drifts — see below — proving it can detect a difference).

### 16.1 DO-NO-HARM (read-only phase; PASSED)
Before (fcbcd4a7 baseline) / After identical: 31/31 armed, 0 latched, 0 trigger; schema 23; `trading-corp` 370246 / `pm-web` 381803 / `sfp-card-watcher` 656 all NRestarts 0; live PM placing, open jack 204 / karen 151 both reads. Nothing moved.

### 16.2 SWEEP RESULT (counts): overlay 1264 box files vs 1530 git-tracked → 516 identical, **2 drift**, 741 box-only-backups + 5 box-only-benign, git-only = not-deployed dirs + non-overlay + 6 trading_corp files.

### 16.3 DRIFT (content differs, present both sides) — direction established per file
| file | division | runtime? | box md5 | prod-live md5 | direction | action |
|---|---|---|---|---|---|---|
| `tests/test_pmcc_logic.py` | PMCC (survivor) | NO (test) | `4be337b6` | `864dcc6a` | **box BEHIND** (box==git commit `2fe3805b` 2026-07-31; prod-live ahead) | NONE — prod-live correct; do NOT fold box→prod-live |
| `BACKLOG.md` | n/a (doc) | NO | `5abc7ca6` | `0881d864` | **box BEHIND** (same July-31 snapshot) | NONE |
Both are the **known-benign July snapshots** the charter said to confirm. Both **box-behind / prod-live-ahead** (NOT box-ahead) — the exact direction the 09-07 trap inverted. No runtime code drifted.

### 16.4 PROD-LIVE-AHEAD (git-tracked, absent on box overlay)
- **6 under `trading_corp/`** — all **never-imported by any `trading_corp/` module** (grep-verified; engine runs without them ⇒ not deployment-critical) = known-KEPT never-deployed dev/one-shot files:
  - LEGACY: `scripts/kalshi_demo_validate.py`, `scripts/kalshi_demo_smoke.py` (kalshi_live DEMO smoke), `data/whale_screening.py` (PCT, never-imported).
  - SURVIVOR (report-to-owner, do NOT fold): `agents/strategies/bitunix_confluence_gate.py`, `agents/strategies/_ta_helpers.py` (bitunix), `scripts/pmcc_paper_run_readiness.py` (PMCC dev). **Undeployed survivor code in git — finding for the bitunix/PMCC owner, not this effort.**
- Bulk (expected, not-deployed dirs; prod-live is a deploy mirror, git tracks full dev tree): tests/261, reports/235, deploy/178, scripts/120, runbooks/58, tmp/30, planning/24, deploy_rh_auth/17, _gdxcap_deploy/12, infra/9, docs/9, cc/8, research/7, sfp_cockpit/2, plans/2, + top-level dev scripts. No action.
- Non-overlay (mapped, present at their box locations — verified): `pead_earnings/*`→`/home/azureuser/pead_earnings/` (PEAD survivor), `card_assets/*`→`/home/azureuser/card_assets/` (sfp-card survivor), `infra/systemd/*`→`/etc/systemd/system/`.

### 16.5 BOX-AHEAD / box-only untracked
- **741 deploy backups** (`.pre-*`, `.bak-*`, `.bak_*`, `*_backup_*/`, `*_rollback_*.sh`, `requirements.*.pre-*`) — benign operational artifacts, incl. my P2/P3 config backups. Not code.
- **5 non-backup**, all benign: `config/Lets start Phase 1 — Plumbing now.txt` (**documented exclusion**), `deploy/2026-07-08_pmcc_lifecycle_fix/backfilled_ids.txt` (**documented exclusion**), `.claude/scheduled_tasks.lock` + `.claude/settings.local.json` (local agent files, not deployed code), **`config/strategies.yaml.block_bs`** (stray scratch — see 16.8).
- **No real untracked deployed code.**

### 16.6 NON-OVERLAY / non-engine surface
- **cron (azureuser): 6 entries, ALL survivor/live-PM** — `pm_cli.py` paper-poll/refresh/adjudicate/rollup (live PM), `replay_audit_event_write_failed.py`, `telegram_lifecycle_divergence_check.py`. **No legacy cron.**
- **systemd units** (`/etc/systemd/system/`): the 4 legacy timers (`watchlist-stats/deep`, `pm-watchlist-deep`, `pct-pruner`) still **installed but disabled** (Ph3) — their unit FILES are Ph5 removal candidates (deletion reserved). `trading-corp-kcv2-observer.service` installed+active (md5 `bf001461`, matches Phase-1 record). `pead-earnings-watcher.service` box md5 **`b2157ffe` = the documented known-stale-benign copy** (systemd runs the `/etc` unit). Survivor units unchanged.

### 16.7 SHARED-FILE BASELINE (Item 3 — removal phase MUST prove these unchanged afterward)
All 8 **IDENTICAL box==prod-live** (CR-stripped md5), captured at prod-live `fcbcd4a7`:
| shared file | md5 (box==prod-live) | LOC |
|---|---|---|
| `trading_corp/main.py` | `fcee5e813c8faa0904a0900bd41ed2bb` | 6209 |
| `trading_corp/persistence/db.py` | `043f6033da089c776d219a269e98dc1f` | 891 |
| `trading_corp/brokers/robinhood.py` | `2753939cd52526d11fb300cd982c43bf` | 1975 |
| `trading_corp/brokers/base.py` | `bc8b6d76eec507ce0b190aa561a654f2` | 246 |
| `trading_corp/agents/data_exec.py` | `8f7d2568603873e9548d22ed19db7263` | 1207 |
| `trading_corp/brokers/kalshi_live.py` | `5c1a3551972bae923d03cddddcad03c7` | 430 |
| `trading_corp/web/data.py` | `2643bfc485f9fba9de4b39c0365e2619` | 6717 |
| `trading_corp/web/routes.py` | `49a785fa1e27800c15cf0cf383596c5b` | 5846 |

**main.py wiring baseline (grep counts at `fcee5e81`; 47 total `asyncio.create_task(`):**
- LEGACY loop-wirings to REMOVE (ref counts incl. def+import+call): polymarket_arb 3, polymarket_copy_trader 2, poly_kalshi 2, kalshi_arb(tail) 3, kalshi_tb 2, kalshi_llm 3, kalshi_weather 2, kalshi_crypto 2, kalshi_sports_scout 2, kalshi_sports_arb_observer 2, kalshi_copy 2; **LEGACY resolvers/marks** `start_kalshi_resolver_loop` 2 + `start_poly_kalshi_mark_loop` 2 (resolve legacy round-trips — also removed).
- **SURVIVOR wirings that MUST remain after removal:** `scheduled_pm_live_loop` 2, `scheduled_shard_snapshot_loop` 1, `_scheduled_pmcc_scan_loop` 2, `_scheduled_pead_scan_loop` 2, `_scheduled_donchian_loop` 3; `pm_live_driver` refs 4; `bitunix` refs 196; `mace`/`MACE` refs 119. Removal phase re-runs these greps post-graft and proves the survivor counts are byte-for-byte unchanged.
- **poly_kalshi_mlb wiring:** `if _pk_cfg.get("enabled")` gate at main.py:1489 (WIRE-GATED). Its `enabled:false` (P2) means the loop won't wire on next restart; the running loop is stopped by the operator persist-halt (`agent_state` agent='strategy_state' key='poly_kalshi_mlb' halt_reason `operator_disarm_2026-09-01`). **Confirmed present; NOT touched.**

### 16.8 UNATTRIBUTED (the most important line — flagged, not filed under "probably fine")
- **`config/strategies.yaml.block_bs`** — a box-only untracked file next to `strategies.yaml`, not in git, no matching deploy backup pattern, name suggests a "block"-edit scratch fragment. The engine reads `strategies.yaml` (not `.block_bs`), so it is inert — but I cannot attribute it to a specific deploy or owner. **FLAG for Jack: confirm deletable scratch; NOT touched this phase.** (Everything else in the sweep is attributed.)

### 16.9 RECONCILIATION_EXCLUSIONS.md ACCURACY — CONFIRMED still accurate
The two scratch/artifact exclusions were found exactly as documented (box-only); the `pead-earnings-watcher.service` known-stale md5 `b2157ffe` matches; `card_assets/out/`, `__pycache__`, `*.bak_*`, data/WAL all correctly classify. Minor gaps (not errors): the doc doesn't list `.claude/` local files or `config/strategies.yaml.block_bs` — both benign/local; suggest a one-line note if a future sweep wants zero-noise. **Verdict: usable as-is for the removal-phase sweep.**

### 16.10 RULINGS / PENDING (carried forward, unchanged)
kcv2 data disposition (archive+verify BEFORE drop); ~~Polymarket USDC drain (gates key removal)~~ **VOID 2026-09-16 — creds transfer to live PM, NEVER-CANCEL (§3/§9/§10)**; DB backup before drop; P2-1 (sudo inert → az-root writes are Jack's). **Removal-phase readiness: shared files clean + baselined; no unrelated runtime drift; 2 known-benign non-runtime box-behind files (no action); 6 never-deployed KEPT files (2 survivor → owner's call).** New for owners: bitunix/PMCC undeployed dev files in git (16.4). Unattributed scratch: `strategies.yaml.block_bs` (16.8).

---

## 17. PHASE 5 EXECUTION LOG — 2026-09-13 (session 5): kcv2 DATA ARCHIVE (non-destructive)

Archive-then-verify; **drops are Phase 7**, gated on the verification here passing. Anchors re-verified: `origin/prod-live` `fcbcd4a7`, PM schema head `23`, engine 370246 / pm-web 381803 / sfp-card 656 NRestarts 0. **STATE: PREP COMPLETE — archive WRITES PENDING Jack's destination ruling (Item 0).**

### 17.1 DO-NO-HARM (before + after-prep; PASSED)
31/31 armed, 0 latched, schema 23, 3 PIDs NRestarts 0; live PM PLACING (open jack 207→209 / karen 154→156 across the prep — growing = healthy). Prep reads (dbstat scan, COUNTs, 300k sample) caused no harm; **`journal_mode=wal`** ⇒ `mode=ro` readers do not block the live writer.

### 17.2 ITEM 1 — prod `kcv2_*` (schema-enumerated, VERIFIED)
DB `data/trading_corp.db` = 5.28 GB (page_size 4096 × 1,288,990; freelist 0); WAL 113 MB; disk free 29 G. **4 tables + 8 indexes** (nothing name-guessed):
| table | rows | rowid range | table bytes | (indexes) |
|---|---|---|---|---|
| kcv2_quotes | 12,010,016+ | [1..12,010,016] | 2130 MB | quotes_mkt 540 + quotes_ts 211 + quotes_cycle 153 MB |
| kcv2_signals | 890,032+ | [1..…] | 45 MB | signals_asset_ts 19 + signals_ts 15 MB |
| kcv2_index_ticks | 445,016+ | [1..…] | 26.5 MB | index_ticks_asset_ts 9.3 + index_ticks_ts 7.6 MB |
| kcv2_heartbeat | 111,254+ | [1..…] | 3.5 MB | heartbeat_ts 1.9 MB |
**kcv2 TOTAL = 3.16 GB** (tables ~2.2 GB + indexes ~0.96 GB). Archive stores **table rows only**; indexes regenerate from DDL on restore. rowids contiguous ⇒ clean rowid-range chunking.
- **★ OBSERVER STILL WRITING (VERIFIED from rowid growth, not a success-timestamp):** `trading-corp-kcv2-observer.service` PID 679 appends ~130 quotes + 8 signals + 4 index_ticks + 1 heartbeat per **30 s** cycle (journal cycle 49120–49123; kcv2_quotes rowid +130 in 35 s). **kcv2_* is a MOVING TARGET.** Phase 5 archives a point-in-time **high-water rowid** snapshot; **Phase 7 must stop the observer FIRST** (az-root, Jack), then capture the delta > HWM, then drop. Do NOT stop the observer here.

### 17.3 ITEM 2 — lab DB (local, VERIFIED)
`C:\Users\AA Incorporado\cc-2026-08-02-wt\research\kalshi_crypto_v2\lab\kcv2_lab.db` = **426,762,240 B (407 MB)**, last write 2026-08-21 (unchanged — scheduled tasks broken, no new lab data). Tables: lab_bars_binance 397,512 · lab_bars_coinbase 397,477 · lab_coinalyze 3,093,856 · lab_kalshi_candles 416,953 · lab_kalshi_ladder_snap 62,948 · lab_kalshi_markets 26,104 · lab_coverage 24 · lab_features/labels/results 0.
- **OVERLAP FINDING (answers "does the Phase-7 drop lose anything"):** lab tables are **ALL `lab_*`, DISJOINT** from prod `kcv2_*` — different sources/content (lab = historical Binance/Coinbase bars + Coinalyze flow + Kalshi candles/ladder/markets research corpus; prod kcv2_* = the live observer's forward-logger ticks/quotes/signals/heartbeat). **Neither is a subset.** ⇒ dropping prod `kcv2_*` (Phase 7) does NOT touch or lose lab data; the archive of prod `kcv2_*` preserves the only copy of the forward-logger corpus. Both must be archived; both are irreplaceable + not in git.
- (git-bash `ls` first reported 197 KB — a spaced-Windows-path measurement artifact; native `Get-Item` = 407 MB. Suspect-the-measurement catch #19.)

### 17.4 ARCHIVE SIZE (measured samples)
| asset | raw | gzip | ratio | note |
|---|---|---|---|---|
| prod `kcv2_*` (table rows → SQL dump) | ~2.8 GB SQL | **~0.30 GB** | 10.7× | numeric quote text compresses well |
| lab DB (binary sqlite) | 427 MB | **~0.14 GB** | 3.1× | |
| **TOTAL archive** | ~3.2 GB | **~0.44 GB** | | small + cheap to store redundantly |

### 17.5 ITEM 0 — DESTINATION OPTIONS (**AWAITING JACK'S RULING — no large write until then**)
| opt | where | off-box? durable? | ~size | write time | restore | notes |
|---|---|---|---|---|---|---|
| **A** | Azure Blob (a storage acct/container Jack names) | YES / YES (Azure-redundant) | 0.44 GB gz | minutes (uplink) | `az storage blob download` → gunzip → sqlite3 | needs a storage acct + SAS/key (Jack); best durability; a data-plane az write (not az-root VM write) |
| **B** | Jack's local machine (e.g. a backups folder) | YES (off the prod VM) / single-machine | 0.44 GB gz | minutes (scp/base64 pull) | local gunzip → sqlite3 | survives VM loss; not redundant unless Jack backs it up |
| **C** | the box (`/home/azureuser/kcv2_archive/`) | NO / **NOT durable** | 0.44 GB gz | fast (local) | on-box | ONLY as staging; lost if VM lost, same disk as the DB being shrunk — **not an archive** |
| **D** | **both A + B (redundant)** | YES / YES | 0.44 GB gz ×2 | minutes | either | **RECOMMENDED for irreplaceable, not-in-git data** |
**Recommendation: D** (or at minimum an off-box durable option A/B). ~0.44 GB gz is small enough that redundancy is nearly free. C alone is disqualified (not durable; defeats the shrink).

### 17.6 DUMP METHOD (designed; runs after ruling) — safe against the live-DB hazard
Per kcv2 table: (1) short `mode=ro` conn → capture `HWM=MAX(rowid)`; (2) emit CREATE TABLE + CREATE INDEX DDL; (3) INSERT rows in **rowid-range chunks** (kcv2_quotes 500k/chunk), **each chunk a SEPARATE short `mode=ro` connection** (releases the read snapshot between chunks → lets WAL checkpoint; no long read lock); stream → gzip. **Between chunks: re-check PM heartbeat freshness + open-position count; if PM stale/collapses, ABORT the dump immediately** (a completed archive is worth less than a live division). Lab DB: byte-copy the file (never move/touch original) → gzip. Record per-table HWM in the archive manifest.

### 17.7 VERIFICATION PLAN (Item 3; the point of the phase) → explicit PASS/FAIL
(a) sha256 every artifact; (b) **actually restore** each into a scratch SQLite (NOT the live DB, NOT the box data dir) and confirm **row counts == source HWM counts** + schema matches; (c) spot-check rows at rowid extremes + middle of kcv2_quotes byte-for-byte vs source; (d) confirm readable **from where the archive will live** (Jack's destination). Result reported as unambiguous PASS or FAIL — Phase 7 authorization depends on it.

### 17.8 ITEM 4 — PHASE-7 DROP PLAN (write-only; do NOT execute here)
Pre-req: this phase's verification = PASS **and** Jack authorizes. Sequence (Phase 7, a restart-free DB window — but see VACUUM): (1) **Jack stops the observer** (`az vm run-command … systemctl stop --now trading-corp-kcv2-observer.service`; az-root) so kcv2_* is static; (2) archive the delta rows > Phase-5 HWM (small) + re-verify; (3) **full-DB backup** (`cp data/trading_corp.db data/trading_corp.db.bak_pre_kcv2drop_<ts>`, ~5.3 GB — needs the 29 G free, OK) — **must precede any DDL**; (4) `DROP TABLE kcv2_quotes; kcv2_signals; kcv2_index_ticks; kcv2_heartbeat;` (indexes drop with their tables) — az-root or a Jack-run DB write (**reserved**); expected DB 5.28 GB → **~2.1 GB** after; (5) space is only reclaimed by **`VACUUM`** — on a 5.28 GB live DB a full VACUUM **rewrites the whole file and takes an EXCLUSIVE lock** (blocks the live PM writer for the rewrite duration, potentially minutes) → **run only in a PM-disarmed or quiet window, Jack's call**, OR skip VACUUM and let the freed pages be reused (DB stays 5.28 GB on disk but has ~3 GB free internal space — no live-lock cost). (6) **PROVE the live arm rows untouched**: `agent_state` 31 arm rows + poly_kalshi persist-halt identical before/after (they share this DB); do-no-harm diff. Rollback: restore the `.bak_pre_kcv2drop` file (engine stopped) — a Jack action.

### 17.9 RULINGS / PENDING
Carried forward: ~~Polymarket USDC drain (gates key removal)~~ **VOID 2026-09-16 — creds transfer to live PM, NEVER-CANCEL**; DB backup before any drop; P2-1 (sudo inert → az-root = Jack); poly_kalshi_mlb residual `auto_execute:true` (clean up Ph6); `strategies.yaml.block_bs` unattributed-retained. Observer-still-writing is now a Phase-7 pre-req (stop it first).

### 17.10 EXECUTION — Jack ruled destination = **D (both blob + local)**. LOCAL archive WRITTEN + VERIFIED; BLOB pending a storage account.
Dump ran chunked with **live PM healthy every chunk** (`PM_ok=True`, hb_age ≤4 s, open≈367 constant across all 28 chunks — the per-chunk check served as the continuous mid-dump do-no-harm; do-no-harm before/after also 31/31 armed, PIDs NRestarts 0).

**ARTIFACTS (with checksums + restore commands):**
| artifact | path | bytes | sha256 | restore |
|---|---|---|---|---|
| prod kcv2_* SQL dump (gz) | LOCAL `C:\Users\AA Incorporado\kcv2_archive_2026-09-13\kcv2_prod_tables.sql.gz` | 278,591,317 | `a4eef50f7a5913b9cf4b2336aa35e63b8d5bf9feb6e3f6ed019f72a305e6f9f3` | `gunzip -c kcv2_prod_tables.sql.gz \| sqlite3 restored_kcv2.db` (CLI handles BEGIN/COMMIT; Python `executescript` must strip them — see restore_verify.py) |
| " (box staging copy) | BOX `/home/azureuser/kcv2_archive_stage/kcv2_prod_tables.sql.gz` | 278,591,317 | same `a4eef50f…` | (Jack-deletable staging; NOT durable) |
| lab DB (gz of raw sqlite) | LOCAL `…\kcv2_archive_2026-09-13\kcv2_lab.db.gz` | 110,760,320 | `cff8a469fd6c06d2eb403c60f5531f56dc24e1c256be28e88f7a28469cd33f36` | `gunzip -c kcv2_lab.db.gz > kcv2_lab.db` (→ byte-identical to source sha256 `4c5146bb…`) |
| lab DB (source, untouched) | `…\cc-2026-08-02-wt\research\kalshi_crypto_v2\lab\kcv2_lab.db` | 426,762,240 | `4c5146bba4baf849d955cc0f2d570944bc62fd4b4cbee9588408458f4a35220a` | (original — never moved/modified) |

**★ VERIFICATION RESULT = PASS (unambiguous).** Restored the full prod archive into a scratch SQLite (`_verify_scratch_kcv2.db`, 3.07 GB — a local Jack-deletable artifact; I cannot delete per the fence) and confirmed:
- **Row counts restored == archived (manifest rows_written), all 4 tables:** heartbeat 111,439 · index_ticks 445,756 · signals 891,520 · quotes **12,034,120** (max_rowid identical).
- **Schema restored == source:** 4 tables + 8 indexes, all rebuilt without error.
- **Spot-check byte-identical (restored vs live source, mode=ro):** kcv2_quotes rowid **1** (BTC KXBTC15M-26AUG052145-45, every field), **6,000,000** (ETH KXETH-26AUG2617-B2470), **12,034,120** (XRP KXXRPD-26SEP1817-T1.3599) — all match to the field.
- **Transfer + lab integrity:** box sha256 == local sha256 (`a4eef50f…`); lab gz decompresses to sha256 `4c5146bb…` == source.
- **Overlap:** lab (`lab_*`) DISJOINT from prod (`kcv2_*`) — Ph7 drop loses no lab data; archive is the only forward-logger copy.

⇒ **Phase 7 (drop) is archive-authorized** for the LOCAL copy. **Blob (redundancy) still owed:** provision a storage account + container, then I upload both gz (data-plane `az storage blob upload`) and re-verify readable-from-blob. Until blob exists, off-box durability rests on the single local copy.

### 17.11 CHANNEL NOTE (correction, 2026-09-13)
One box read this phase (the dump-orphan check) was run as **ad-hoc `ssh … bash` in the Bash tool** — a breach of the sanctioned-channel rule ([[command-paste-rule]]: box access only via a validated `.ps1` runner, even read-only). Jack corrected it. All other Phase-5 box ops used validated `.ps1` runners; local-only python (lab read, restore, hashing) is not a box channel. Standing discipline reaffirmed: **every** ssh/scp/az touch goes through an ASCII-validated `.ps1` runner — no inline ssh/scp in the Bash/PowerShell tools.

---

## 18. PHASE 6 — CODE REMOVAL (session 6, 2026-09-13). Jack ruled **Option 1 (deploy the proven main.py graft ONLY; defer ALL file deletions + web surgery)**.

Option 2 was scoped (see the CLOSURE PROBLEM in 18.3), but proving imports before deleting broke the "cleanly orphaned" assumption **twice** — both times a survivor-coupled "legacy" file (`_weather_math` ← survivor `path_logger`; `kalshi_crypto_v2_observer` ← running PID 679) that the deletion boundary had mis-mapped. Jack's ruling: **do not compute a cascading deletion closure inside a restart window with 44 live real-money sub-divisions. Deploy only the three lowest-risk changes; file deletions move to the web/shared untangle pass they are coupled to.**

**THE OPTION-1 DEPLOY = exactly three things, nothing else:**
1. the proven `main.py` graft (legacy loop wiring + legacy loop/helper defs removed; 6210→3924 LOC),
2. `poly_kalshi_mlb auto_execute:false` in `config/strategies.yaml`,
3. the 4 legacy `.timer` unit files removed (root write → runner handed to Jack).
NO file deletions. NO web/routes.py surgery. NO test/script removal. The legacy `.py` files stay as dead-but-unwired code — **grafted main.py imports 0 of them at module level (they were only referenced by the removed loop bodies); py_compile clean**.

Removal worktree `cc-legpm-removal-wt` branch `legpm-code-removal-2026-09-13` off prod-live `fcbcd4a7`. Build+prove LOCAL; **PUSH / RESTART / ARM-HALT-DISARM / az-root / table-drop = Jack.**

**RE-PROVE @ push time (2026-09-13 ~22:00Z, all VERIFIED):**
- origin/prod-live tip == `fcbcd4a7` EXACTLY (fetched; base not stale; no rebase).
- §16.7 all 8 shared files md5(CR-stripped) UNCHANGED box==prod-live: main.py `fcee5e81…`/6209, db.py `043f6033…`/891, robinhood `2753939c…`/1975, base `bc8b6d76…`/246, data_exec `8f7d2568…`/1207, kalshi_live `5c1a3551…`/430, web/data `2643bfc4…`/6717, web/routes `49a785fa…`/5846. **Nothing deployed underneath us.**
- Grafted main.py: survivor counts ALL unchanged (bitunix 196, mace 119, pm_live_driver 4, scheduled_pm_live_loop 2, shard_snapshot 1, pmcc 2, pead 2, donchian 3); all 13 legacy loop/def counts = 0; all 13 dangling task-vars = 0; diff = 0 non-blank additions / 2334 deletions (pure deletion + blank-collapse); py_compile OK (3.12). **Deploy target: grafted main.py md5(CR-stripped) = `c15b4de64ad19b814580cb6ed311262f`, 3923 LOC, LF-only, trailing newline.**

**DO-NO-HARM baseline (2026-09-13 22:06Z, RO — corrected after a stale-backup misread, see note):** trading-corp PID **370246** (active, up since 09-12 21:00:28Z, NRestarts 0 — WILL CHANGE on restart); prediction-markets-web PID **381803** + sfp-card-watcher PID **656** (MUST NOT change); kcv2 observer PID **679** (`kalshi_crypto_v2_observer`, up ~17d — MUST NOT be touched). Live PM DB `/home/azureuser/trading_corp/data/prediction_markets.db` head 23, 394MB, writer live (mtime advancing). **Armed = 44 active pm_subdivision (kalshi_jack 23, kalshi_karen 21)** — invariant across restart (older memory's "31" has grown to 44). Order journal `pm_subdivision_order` = 792 orders, **ALL dry_run=0 (real money)**; MAX submitted_ts `1789337108`; cadence 20/hr, median inter-arrival 7s, max 1245s. Survivors active at baseline: bitunix (now), MACE lord_otter/market_cypher (now), donchian 18:02Z, mace 04:00Z. Legacy engine actor `poly_kalshi_mlb` last wrote 21:14:02Z (kind poly_kalshi_order) — WILL STOP post-restart (intended; retiring loop). MEASUREMENT NOTE: first PM-DB read hit a stale backup (`pm_karen_account_bak…`, head 15, 1 sub) because `find` returned it first; corrected by choosing the DB under pm_web cwd, non-backup, size-sorted.

### 18.1 REVIVAL SHA = `fcbcd4a7cde71abb33ba418be12d7ad5ff2e8869`
Last prod-live commit containing EVERY removed legacy division. Revival = `git checkout fcbcd4a7 -- <path>`. (Removal-with-history satisfies "preserved in GitHub"; no separate tag/branch needed — noted per charter.)

### 18.2 ★ ADDITION-2 DECISION — `kalshi_whale_stats` NOT edited (defer). 
Live-PM `prediction_markets/stats.py` imports ONLY pure primitives from it (`_edge_factor`, `time_weighted_outcomes`, `wilson_lcb_95`, `wilson_lcb_95_weighted`; calls `score_net_roi/recency_weighted/snapshot`). The `kalshi_apify_client` types are used ONLY as legacy-function param annotations (L234/236/328) live-PM never calls. Removal is *possible* but edits the live-PM-imported file (annotations eval at def-time). Per ADDITION 2 → **STOP: do not edit; keep `kalshi_apify_client`.** ⇒ cascades to deferring ALL 6 blocked-file deletions.

### 18.3 ★ THE TRANSITIVE CLOSURE PROBLEM (the deferred work is a CLOSURE, not a leftover list — the untangle pass must NOT restart by rediscovering this)
> **✅ RESOLVED 2026-09-16 (session 8) — CLOSURE COMPUTED IN FULL. See §20.** This section stated the problem; §20 is the answer. Key correction to the text below: §18.3 called the `web/routes.py` PM routes "do NOT import deleted modules" — TRUE for the `/prediction-markets/` render routes, but the closure surfaced **6 additional lazy imports in `web/routes.py`** (whale force-close/promote/demote/analyst routes) pulling `kalshi_copy_trader`, `polymarket_copy_trader`, `roster_split`, `polymarket_whale_analyst`, `polymarket_whale_audit_cache` — the "other couplings" §18.3 warned might exist. Those are now KEEPERS pending a web graft (§20).
Option 2 assumed ~30 files were "cleanly orphaned." Proving imports before deleting (query validated first) broke that assumption **twice**, both times because the deletion boundary was drawn from the wrong map:
- ★ **`_weather_math.py` is a KEEPER.** `trading_corp/path_logger/logger.py:31` does `from trading_corp.agents.strategies._weather_math import kalshi_quote_dollars` (used L300), and **path_logger is a SURVIVOR with its own systemd unit `trading-corp-path-logger.service`**. An earlier check "path_logger not in tree" looked at the **repo-root** instead of `trading_corp/path_logger/` and returned a confident WRONG "safe to delete." Corrected only by grepping the right prefix. Keeping `_weather_math` transitively keeps whatever IT imports (verify: possibly `residual_logic`) — NOT yet computed.
- ★ **`kalshi_crypto_v2_observer.py` is a KEEPER.** kcv2 observer **PID 679** runs `python -m trading_corp.agents.strategies.kalshi_crypto_v2_observer` (confirmed live at baseline, up ~17d). Deleting the .py does not crash the running process but breaks its next restart — and Phase 7 restarts/retires that unit. Stays until Phase 7 formally stops the unit.
- **THE CLOSURE WAS NEVER FULLY COMPUTED — say so.** Known members so far: `_weather_math` (+ its imports), `kalshi_crypto_v2_observer`, the 6 already-blocked shared files below (+ their live importers), ~25 legacy tests + top-level scripts that import the candidate modules, the removable `web/routes.py` PM routes, and the ~2,500 LOC of `web/data.py` legacy dashboard woven into `_hydrate_division_metrics`. Compute the full closure DELIBERATELY (not in a restart window) in the untangle pass.
- **THREE FALSE ALARMS cleared (do not re-litigate):** `polymarket_arbitrage`, `residual_logic`, `polymarket_whale_stats` are referenced by `web/data.py` / `polymarket_whale_audit` ONLY in SQL strings / comments — a broad `(import|from).*<mod>` regex over-matched; real `import`-line greps returned empty. Not imports; deletable on import grounds (still deferred with the rest under Option 1).

The candidate DELETE set Option 2 had drafted (strategies polymarket_arbitrage / *_copy_trader / kalshi_tail_price_arb / *_temporal_bucket_arb / *_llm_arbitrage / *_weather_arb / *_crypto_arb / *_sports_scout / *_sports_arb_observer / kalshi_copy_trader / poly_kalshi_copy_trader / poly_kalshi_executor / roster_split; data clients; agents kalshi_resolver / polymarket_resolver / poly_kalshi_marks / polymarket_whale_analyst; research/polymarket_whale_audit_cache; trading_corp/scripts legacy PM scripts) is **NOT executed this phase** — it is the raw material for the closure, MINUS the two keepers above, MINUS whatever the closure adds.

**6 BLOCKED shared files (deferred; each retains a live importer → deleting now would dangle a real import):**
| deferred item | why kept | who still references it | Phase-7 effect |
|---|---|---|---|
| `data/kalshi_apify_client.py` | live-PM `kalshi_whale_stats` imports its types (ADDITION 2) | kalshi_whale_stats.py L40 | none |
| `data/kalshi_market_map.py` | shared `brokers/kalshi.py` lazy-imports it (→ live-PM via kalshi_live) | kalshi.py L402 | none |
| `brokers/polymarket.py` + `polymarket_live.py` | main.py broker-factory (L3152/3173) builds them for the still-registered polymarket divisions | main.py factory | none |
| `web/kalshi_crypto_vol_v2.py` | shared `web/data.py` top-level import (L19), consumed by the deferred dashboard | web/data.py L19 | tables emptied |
| `web/data.py` legacy PM dashboard (~2,500 LOC: `build_prediction_market_view` + 15 helpers, `_hydrate_pm_overview` woven into shared `_hydrate_division_metrics`) | web deferred per ruling | shared /prediction-markets/ routes | reads legacy round_trip tables → go stale/empty |
| non-breaking `web/routes.py` PM routes (`/prediction-markets/`, `/partials/prediction-markets/*` — call `build_prediction_market_view`, do NOT import deleted modules) | web deferred | — | dashboard shows disabled/empty divisions |
| legacy `divisions.yaml` slugs + `strategies.yaml` disabled blocks | read by the deferred dashboard | web/data.py division load | — |
The deferred web dashboard is **legacy-data-only** (0 refs to prediction_markets.db / live accounts — live PM is the separate `pm_web`). So it degrades (empty tiles) rather than breaks after removal; no live-PM danger.

### 18.4 poly_kalshi persist-halt row — **REDUNDANT, not PROTECTIVE (Jack's framing; ADDITION 3).** With the main.py loop WIRING removed, the `poly_kalshi_mlb` loop can no longer be scheduled at all → the legacy division is stopped by wiring-absence (belt) + `enabled:false` (suspenders) + the new `auto_execute:false`. The `agent_state strategy_state/poly_kalshi_mlb` `operator_disarm_2026-09-01` row therefore **no longer gates anything live — it is redundant, not the protective mechanism it once was.** It STAYS untouched (charter: "★ ANY TOUCHING OF THE poly_kalshi_mlb PERSIST-HALT ROW is forbidden"). A later agent must neither treat it as a live safety mechanism (it isn't anymore) nor delete it believing it still is one. Left exactly as-is; NOT read, NOT written this phase.

### 18.5 kcv2 observer UNAFFECTED + the POST-RESTART PLACING GATE (state BEFORE the restart)
kcv2 observer (PID 679) is a **separate process**; the engine `systemctl restart trading-corp` does not touch it; Phase 7 (not this phase) stops it. Verified alive at baseline (up ~17d). This phase does not delete its module (18.3) → no early break.
**★ POST-RESTART GATE — PM is armed through the restart, so this is the WHOLE safety net. Gate = PM PLACING, not merely armed** (2026-09-04 = 28h armed-not-trading behind green rows; liveness alone is NOT proof). Two signals:
- (a) `journalctl -u trading-corp` (readable by azureuser — confirmed) resumes live_driver cycles (`polymarket-data-api positions[…]` whale fetches) within **~2 min** of boot = the driver is evaluating (this is exactly what 2026-09-04 lacked for 28h);
- (b) **a fresh `pm_subdivision_order` row with `submitted_ts` > baseline MAX `1789337108` AND `dry_run=0` within 20 MINUTES** = real placement.
**BOUND = 20 min**, = the max observed inter-arrival gap during active play (cadence 20 orders/hr, median gap 7s). It applies when Jack restarts **during active sports** (as at baseline: atp/wta/mlb live) — **recommend restarting during active play so the strong gate is in force**. If restarted in a genuinely quiet window, fallback = journalctl shows a FULL live_driver cycle end-to-end (whale-fetch → market-match → order-consideration).
**★ IF no real placement within the bound (active play): ROLL BACK (forward-revert, FF-pushed) and tell Jack IMMEDIATELY — do not investigate first, do not assume the market is quiet.**
Post-restart confirm: survivors returned (bitunix, MACE, PMCC, PEAD, donchian, coinbase, iron condors, tastytrade, path_logger, live PM); **pm_web 381803 + sfp-card-watcher 656 + kcv2 679 UNCHANGED**; engine PID changed (authorised); legacy actor `poly_kalshi_mlb` STOPS writing (intended). **HTTP-check** main dashboard + each survivor division page (main.py changed underneath them; a clean boot ≠ intact web) — status + per-page content sanity.

### 18.6 STATUS: Option-1 build COMPLETE + PROVEN local, then DEPLOYED LIVE (see 18.7). Order executed: (1) prod-live FF push (Jack); (2) box graft-deploy (agent, scp-graft); (3) `systemctl restart trading-corp` (Jack, canonical `restart_tc.ps1`); (4) 4-timer-unit removal (Jack, az-root); post-restart PLACING gate PASSED (agent). No file deletions, no web surgery — deferred to the untangle pass (18.3).

### 18.7 ★ EXECUTION RESULT — DEPLOYED LIVE 2026-09-14 (GATE PASS)
**Deploy:** origin/prod-live `fcbcd4a7` -> **`8f35f254`** (clean FF, single commit; Jack pushed, verified `git ls-remote` == 8f35f254 before touching box). Docs on `legacy-pm-retire-2026-09-13` @ `7f0228a6`.
**Box graft (scp file-graft; box is NOT a git repo):** live main.py `fcee5e81`/6209 -> **`c15b4de6`/3923**, config -> **`1657119b`** (`poly_kalshi_mlb auto_execute:false`). Drift-gated (live==pre-graft before write). Backups: `…/trading_corp/main.py.bak_legpm_20260913T232311Z` (297972B) + `…/config/strategies.yaml.bak_legpm_20260913T232311Z`. Re-proven on the DEPLOYED file: md5/LOC/survivor-counts/legacy=0/py_compile OK.
**Restart:** engine **370246 -> 397094**, boot **2026-09-14 00:25:13Z**, NRestarts 0, active/running.
**★ GATE = PASS (split-authority two-signal ruling; times UTC):**
- **Signal 1 (the 2026-09-04 detector) PASS:** `00:25:34` `PM LIVE DRIVER WIRED -- 2 account task(s)` (kalshi_jack 17 cats / kalshi_karen 15); `M3 shard-snapshot WIRED`; driver **cycling** (`polymarket-data-api positions` 2034 lines by 00:32); **boot-reconcile** `00:25:41/42` both accounts reconciled=True **latched=False**.
- **Signal 2 (placement) PASS:** order **id 820, kalshi_jack/mlb/bid, dry_run=0, submitted_ts 1789345733 = `00:28:53Z`** (~3.7 min post-boot, well within 20-min bound), outcome `no_fill` (a real order submitted; venue didn't match — routine, not idleness).
**Siblings UNCHANGED:** pm_web **381803**, sfp-card-watcher **656**, kcv2 observer **679** (still running `kalshi_crypto_v2_observer`). PM invariants: **44 armed (jack 23/karen 21)**, schema head **23**. Boot clean (only known-benign: fidelity playwright, EODHD/yfinance BTC-USD/SUI-USD; **NO `kalshi_copy Apify FEED DOWN`** — the retired legacy loop is gone). Survivors live: bitunix_futures/mace/scheduler/data_exec post-boot. Web: Command Center **HTTP 200** (324KB), `/division/bitunix_futures` **200** (the `/division/mace|pmcc|pead|coinbase` 404s were wrong-slug guesses; app serves a proper 404 template = routing healthy).
**Timers (Jack, az-root single-line):** 4 legacy `.timer` + 4 companion `.service` (8 files) **REMOVED**; backup `/root/legpm_timer_unit_backup_20260914T010512Z`; RO-verified: 8 files ABSENT, 4 timers not-found, survivors `pead-earnings-watcher`/`rh-relogin`/`tc-audit-reality` still enabled+active, engine unaffected.
**GATE-DESIGN RULING (Jack):** the hard 20-min bound was SPLIT after the cadence caveat (last-hour 9 orders, max gap ~24 min > 20 min): Signal 1 driver-dead = automatic unconditional rollback; Signal 2 no-placement WHILE driver cycling = report-and-hold (operator rules), not auto-rollback. Also: the 3-min driver-cycle trigger was corrected — `PM LIVE DRIVER WIRED` (~+21s) is the direct detector; first cycle is ~+5 min on a healthy boot.
**PROCESS/az-runner lessons (locked into [[command-paste-rule]]):** multi-line `@'...'@` -> `az --scripts` returns EMPTY and does NOT run (bit the boot-verify restart + first timer runner); embedded `"` in a complex `--scripts` arg is mangled by PS native-arg quoting; `tr -d "\r"` backslash gets eaten -> false md5; `az -o tsv` message is a multi-line ARRAY (`-notmatch` element-wise false); agent-run az-root writes/restart are classifier-BLOCKED even after authorization -> Jack runs them. Fix = canonical `restart_tc.ps1` + simple no-quote single-line az + all complex logic via the RO ssh-STDIN channel.
**Phase 7 inherits:** kcv2 observer **PID 679 STILL RUNNING + unaffected** (stop it FIRST in Ph7); the transitive-closure file-deletion problem (18.3, incl. `_weather_math`/`kalshi_crypto_v2_observer` keepers); the redundant persist-halt row (18.4, leave it). Revival of the loops = `git checkout fcbcd4a7 -- trading_corp/main.py`.

---

## 19. PHASE 7 — kcv2 DATABASE DROP + SHRINK (session 7, 2026-09-14). ★ THE IRREVERSIBLE PHASE. Two gates before any destructive step; DROP NOTHING until Jack rules on BOTH.

### 19.1 RE-VERIFIED BASELINE (RO, 2026-09-14 ~01:30Z) — inherited numbers re-derived; several were stale/imprecise
- Engine trading-corp **PID 397094** (boot 00:25:13Z); pm_web **381803**; sfp **656**; observer **`trading-corp-kcv2-observer.service` MainPID 679** (loaded/active/running — it IS a clean systemd unit, so Jack stops via `systemctl stop`). schema head **23**; prod-live **8f35f254**.
- ★ **ARM ROWS vs pm_subdivision — the inherited "44 arm rows in this DB" was IMPRECISE.** **31 arm rows** live in `trading_corp.db agent_state` (agent='pm_live', key `arm:%`: 1 global + 15 jack + 15 karen), **0 latched / 0 trigger** — THESE are the rows in the DROP-target DB. The **44** are `pm_subdivision active=1` (jack 23/karen 21) in `prediction_markets.db`, a DIFFERENT db the drop never touches. Heartbeat cycling 32 subs (jack 17/karen 15), all `state=evaluated`, 0 errors. **poly_kalshi persist-halt PRESENT**: `agent=strategy_state key=poly_kalshi_mlb {"halted":true,"halt_reason":"operator_disarm_2026-09-01"}`. agent_state = 363 rows / 21 agents.
- **PM-PLACING caveat:** at baseline the last real order was id 820 @ 00:28:53Z (~1h prior) — driver evaluating (fresh heartbeats) but placement quiet (late-night, few live games). CANNOT claim "placing" without a fresh order ⇒ the drop-safety "PM placing" proof must be TIMED TO ACTIVE PLAY (as the Ph6 gate was).
- **trading_corp.db = 5,313,138,688 B (5.31 GB)**, WAL (wal 113 MB); page_size 4096, page_count 1,297,153, **freelist_count 0, auto_vacuum 0 (NONE)**. Disk **29 G free** (5.3 GB backup OK). **47 tables** (43 remain post-drop).
- **kcv2_* drop targets (4 tables + 8 indexes):** heartbeat 112,302 / index_ticks 449,208 / quotes **12,131,756** / signals 898,416. **OBSERVER STILL WRITING:** quotes **+97,636** vs Phase-5 HWM 12,034,120 (hb +863 / ticks +3,452 / signals +6,896) = the delta to archive in step 2.

### 19.2 ★ GATE A — ARCHIVE INTEGRITY + RESTORABILITY (the corpus is single-copy)
- Both archive files **byte + SHA256 IDENTICAL to Phase-5** (Get-FileHash, local): `kcv2_prod_tables.sql.gz` 278,591,317 B `a4eef50f…`; `kcv2_lab.db.gz` 110,760,320 B `cff8a469…`.
- **FRESH RESTORE (today 2026-09-14, new scratch `cc/_p7_verify_scratch.db` 3068 MB): PASS.** restore_verify.py, 13,482,901 gz lines in 770.2s + 8 indexes in 21.1s. Counts IDENTICAL to Phase-5: heartbeat 111,439 / index_ticks 445,756 / signals 891,520 / quotes **12,034,120** (max_rowid matches). 4 tables + 8 indexes rebuilt clean. Spot rows byte-identical: rowid 1 BTC KXBTC15M-26AUG052145-45, rowid 6,000,000 ETH KXETH-26AUG2617-B2470, rowid 12,034,120 XRP KXXRPD-26SEP1817-T1.3599. ⇒ archive is intact AND restorable TODAY (proven, not inferred). Restore wall-time ~13 min = the rollback re-restore cost (engine stopped).
- ★ **RULED (Jack, 2026-09-14): an OFF-DEVICE copy must exist AND be VERIFIED READABLE FROM WHERE IT LIVES before the drop.** (The Phase-5 local-only ruling predated any destruction; the archive is now the ONLY forward-logger record + rollback already needs an engine stop.) Two acceptable forms, agent presents, Jack picks: **(a)** Azure blob — Jack provisions storage account+container, agent writes upload+READ-BACK-verify runner (verify must re-download/hash FROM the blob, not trust upload success); **(b)** a 2nd local copy on DIFFERENT PHYSICAL MEDIA (not off-box, but removes single-device failure), verified the same way. **GATE A NOT PASSED until BOTH the fresh restore returns AND an off-device verified copy exists.**

### 19.3 ★ GATE B — VACUUM vs the live DB (measured)
- **The engine HOLDS `trading_corp.db` OPEN** — `fuser` = PIDs **656 (sfp) + 397109 (engine python child)** (`/proc/MainPID/fd` was the wrong pid; the connection is on the child). WAL mode.
- ⇒ **A full `VACUUM` CANNOT run while the engine is up** (needs exclusive access → `SQLITE_BUSY`). VACUUM requires the **ENGINE STOPPED** (a restart), NOT merely PM-disarmed — §17.8's "quiet window" was insufficient.
- **`freelist_count 0` + `auto_vacuum 0` ⇒ DROP alone reclaims ZERO disk:** file stays 5.31 GB; the ~3.15 GB of dropped pages become FREE INTERNAL pages (reused by future growth; the DB won't grow on disk for a long time).
- **The DROP itself** (Jack-run SQL on the live WAL DB): feasible (WAL concurrent readers OK) but takes a write lock for its duration; the engine's writes (heartbeats/orders every few sec) BLOCK during the DROP (~seconds to ~1 min to free ~750K pages / 12M rows); an in-flight order blocks then proceeds. Observer stopped first ⇒ kcv2_* static during the drop.
- **OPTIONS (Jack rules):**
  - **A. DROP only, NO VACUUM (recommended, lowest risk):** file stays **5.31 GB**, ~3.15 GB free internal (reused). Live cost = the DROP's brief write-lock block (sec–min). No engine stop. Disk NOT reclaimed now (29 G free anyway).
  - **B. DROP + VACUUM in an ENGINE-STOPPED window:** stop trading-corp (PM disarm + a restart), VACUUM (~2.1 GB rewrite, minutes), restart. File → **~2.1 GB**. Requires an engine stop/restart (bigger; a Ph6-style restart + PLACING gate).
  - **C. `VACUUM INTO` a new file** then swap (also needs an engine stop for the swap) — similar cost to B.
- ★ **RULED (Jack, 2026-09-14): OPTION A — DROP only, NO VACUUM this phase.** Reasoning (do not re-litigate): 29 G free = no space pressure, so ~3.15 G reclaim buys nothing today; B/C cost an engine stop + PM disarm + restart + full gate cycle to reclaim unneeded disk; freed pages are reused (space not wasted, only unreturned); VACUUM folds into any future restart-for-another-reason at zero marginal cost. ★ **WHAT THIS DEFERS — record plainly:** the headline "5.28 GB → 2.1 GB" DOES NOT land this phase; the file stays ~5.31 GB with ~3.15 GB free internal pages. **The DATA is retired either way; the shrink is cosmetic until there is space pressure.** A later agent must NOT read the unchanged file size as a failed drop.

### 19.4 RE-DERIVED DROP PLAN (supersedes §17.8; order matters)
1. **Jack stops the observer** `systemctl stop trading-corp-kcv2-observer.service` (az-root; verify unit state after — sudo inert). ⇒ kcv2_* static.
2. **Delta-archive** rows > Phase-5 HWM (quotes > 12,034,120 etc.; ~97.6K quotes) + VERIFY (restore-into-scratch, matching counts).
3. **Full-DB backup** `cp trading_corp.db trading_corp.db.bak_pre_kcv2drop_<ts>` (5.31 GB; 29 G free OK; ~mins; check PM health mid-copy). Verify restorable.
4. **DROP** `kcv2_heartbeat`, `kcv2_index_ticks`, `kcv2_quotes`, `kcv2_signals` (8 indexes drop with them) — Jack-run DB write (reserved). Brief engine-write block.
5. **VACUUM or not** per Gate B (default A = no VACUUM).
6. **PROVE:** 31 arm rows + persist-halt byte-identical before/after; 43 tables remain; PM placing (fresh order, active play); DB size before/after.

### 19.5 ROLLBACK (written BEFORE any destructive step)
- **Observer stop:** reversible — `systemctl start trading-corp-kcv2-observer.service` (az-root).
- **DROP:** routes back are (a) restore the full-DB backup `.bak_pre_kcv2drop` (cp back, **ENGINE STOPPED**, ~mins for 5.3 GB) — the fast route; (b) if that's lost, re-restore kcv2_* from the archive gz (`gunzip -c | sqlite3`, **ENGINE STOPPED**, ~10–15 min) + the delta-archive. ★ **Recovery is NOT hot — it needs an engine stop/restart (a maintenance window), not a live fix.** After the drop the kcv2 corpus exists only in the full-DB backup + the local archive + the delta-archive — **all local/on-box; if all are lost the 3.15 GB corpus is gone forever** (hence Gate A).
- **VACUUM:** preserves data; rollback only if it corrupts (restore backup). No data-loss risk.

### 19.6 STATUS: gates presented; **DROP NOTHING until Jack rules Gate A (blob vs local-only) + Gate B (VACUUM option).** Observer stop / delta-archive / backup / DROP / VACUUM all reserved for Jack; agent does RO + local archive verify + builds+presents the runners. Out of scope unchanged: no code removal, no API cancels, no USDC drain, no archive/lab deletion; `strategies.yaml.block_bs` left.

---

## 20. PHASE 8 — UNTANGLE PASS: THE TRANSITIVE CLOSURE, COMPUTED (session 8, 2026-09-16). ANALYSIS + LOCAL BUILD ONLY; nothing reached the box.

Answers the §18.3 deferred problem. Off `origin/prod-live` **`3dd15c10`** (verified tip — advanced through Ph6 `8f35f254` + later PM/ITF deploys; the charter's `8f35f254` was Ph6-era). Build worktree `legacy-pm-untangle-2026-09-16`. **Market open → deploy/restart held for after 16:00 ET; box read-only; nothing here needs either.**

### 20.1 METHOD — AST import graph, query PROVEN before trusted
Stdlib `ast` walk of **all 785 tracked `.py`** (repo-wide, not just expected paths): maps files↔dotted-modules, extracts EVERY `import`/`from` node (module-level AND function-level/lazy — AST sees them anywhere), resolves relative imports, and scans `importlib.import_module`/`__import__` string args (a real AST blind spot). **0 parse errors / 785.**
- **★ QUERY-VALIDATION FIRST (the §"prove your query works" mandate):** before believing any "no importers", the analyzer was shown to detect three known imports — `path_logger/logger.py → _weather_math`, `prediction_markets/stats.py → kalshi_whale_stats`, `kalshi_live.py → kalshi.py`. All three present ⇒ the graph is not silently empty.
- **Dynamic-import blind spot cleared:** the only literal `import_module`/`__import__` args in the tree are stdlib (`time`/`json`/`datetime`/`decimal`); the one non-literal is a test reflecting over `main.py`'s own imports that swallows import errors. **No survivor dynamically pulls a legacy module.**
- **String-refs ≠ imports (confirmed live):** `web/routes.py` has ~30 `"kalshi_copy_trader"` occurrences that are `agent_state` KEY STRINGS, not imports; a textual sweep also flagged `whale_screening.py:15` and `routes.py:705` — both DOCSTRINGS. AST correctly excluded all of them.

### 20.2 CLASSIFICATION — PROTECTED forward-closure from survivor roots
**PROTECTED = forward-reachable from every survivor runtime root** (engine `__main__.py`/`main.py`; whole `prediction_markets/**` = live PM + pm_web; `path_logger/**`; `mace/**`; kcv2 observer entry; crons `pm_cli.py`/`replay_audit_event_write_failed.py`/`telegram_lifecycle_divergence_check.py`; out-of-tree `card_assets/**` + `pead_earnings/**` per RECONCILIATION_EXCLUSIONS). **= 254 files.** A candidate is a KEEPER iff it lands in PROTECTED (a survivor reaches it); DELETABLE iff no survivor reaches it. **Authoritative invariant proven: 0 PROTECTED files import any of the 27 core deletable modules** (so the keepers, incl. the web-blocked ones, do not depend on the deletables).

### 20.3 ★ THE SPLIT (the closure is messier than §18.3's "6 blocked files" — reported honestly, per charter)
| Group | What | Count | Disposition |
|---|---|---|---|
| **TRANCHE 1** | Pure-deletable NOW (reverse-closed; no kept file imports them; no edits) | **49** | **BUILT + PROVEN (§20.5); deploy = FF push, Jack, after 16:00 ET** |
| **KEEPERS (shared-blocked)** | Legacy files a SHARED file imports → need a graft to remove | **13** | DEFER to a shared-file graft pass (§20.4) |
| **KEEPERS (permanent / Ph7)** | `_weather_math` (survivor), `kalshi_crypto_v2_observer` (kcv2) | 2 | `_weather_math` PERMANENT; observer until Ph7 |
| **DEFERRED (test-coupled)** | 2 legacy modules + 2 keeper tests kept valid by a small test edit | 4 | DEFER (§20.4) |
| **TRANCHE-2 leaves** | ~20 legacy scripts/tests importing the shared-blocked keepers | ~20 | DEFER — delete WITH their keeper in the graft pass |
| **Non-.py** | 8 infra/systemd legacy unit files (already off-box), legacy config blocks, woven web dashboard | — | DEFER (grafts / separate concern) |

### 20.4 KEEPERS — every one with its surviving importer NAMED + LINE-NUMBERED (VERIFIED in the 3dd15c10 tree)
| Keeper file | Kept because (importer : line) | Unblock = |
|---|---|---|
| `agents/strategies/_weather_math.py` | **SURVIVOR** `path_logger/logger.py:31` (`import kalshi_quote_dollars`, used L300) | PERMANENT — never delete unless path_logger stops using it |
| `agents/strategies/kalshi_crypto_v2_observer.py` | kcv2 observer **PID 679** (`python -m …kalshi_crypto_v2_observer`) | Phase 7 stops the observer first |
| `brokers/polymarket_live.py` | `main.py:2685` (broker factory, lazy) | main.py graft: deregister polymarket divisions + drop factory branch |
| `brokers/polymarket.py` | `main.py:2706` (factory) + `polymarket_live.py` | same main.py graft |
| `data/kalshi_market_map.py` | `brokers/kalshi.py:402` (lazy discovery import) | kalshi.py graft (RULING-KALSHIMAP) — shared → live-PM transitive |
| `data/kalshi_apify_client.py` | `data/kalshi_whale_stats.py:40` (module-level) + `kalshi_copy_trader` | Jack ruled kalshi_whale_stats NOT edited (§18.2) → PERMANENT unless reversed |
| `web/kalshi_crypto_vol_v2.py` | `web/data.py:19` (**MODULE-LEVEL** — fails at BOOT if deleted) | web/data.py graft |
| `agents/strategies/kalshi_copy_trader.py` | `web/routes.py:2756` (lazy, force-close route) | web/routes.py graft |
| `agents/strategies/polymarket_copy_trader.py` | `web/routes.py:3076` (lazy) + `roster_split` | web/routes.py graft |
| `agents/strategies/roster_split.py` | `web/routes.py:3159,3185` (lazy promote/demote) + `polymarket_copy_trader` | web/routes.py graft |
| `agents/polymarket_whale_analyst.py` | `web/routes.py:3233` (lazy) | web/routes.py graft |
| `agents/research/polymarket_whale_audit_cache.py` | `web/routes.py:3234` (lazy) | web/routes.py graft |
| `agents/strategies/_whale_autopause.py` | `kalshi_copy_trader` + `polymarket_copy_trader` (transitive) | falls when both copy-traders fall |
| `data/polymarket_whale_stats.py` (DEFERRED) | `scripts/seed_polymarket_watchlist_deep.py` + `tests/test_polymarket_copy_trader.py` | web graft + test edit |
| `scripts/seed_polymarket_watchlist_deep.py` (DEFERRED) | keeper test `tests/test_polymarket_data_api_client_retry.py:213+` (lazy) — the test covers the **live-PM shared** `polymarket_data_api_client` | small test edit (drop the seed-script test fns) |

### 20.5 TRANCHE 1 — the 49-file pure-deletion, BUILT + PROVEN LOCAL (branch `legacy-pm-untangle-2026-09-16` @ **`ed6d9b83`**)
**Contents:** 22 `trading_corp/` modules (retired strategies polymarket_arbitrage / kalshi_tail_price_arb / kalshi_temporal_bucket_arb / kalshi_llm_arbitrage / kalshi_weather_arb / kalshi_crypto_arb / kalshi_sports_scout / kalshi_sports_arb_observer / poly_kalshi_copy_trader / poly_kalshi_executor + helpers _polymarket_prompts / _sports_math; data crypto_spot/vol_provider, iem_cli/metar/nbm/open_meteo/odds_api clients, kalshi_matchable, residual_logic, weather_forecast/stations; agents kalshi_resolver / polymarket_resolver / poly_kalshi_marks) + 1 script (refresh_polymarket_whales) + 7 top-level scripts (backfill/ingest/replay/retro) + 15 legacy tests. **Full list = `git show ed6d9b83 --stat`.**
**PROOFS (all VERIFIED this session):**
- `git show`: **49 files, 0 additions, 17,918 deletions** — pure deletion, touches none of the 8 shared files.
- **py_compile whole tree: 736/736 OK, 0 failures.**
- **Import-clean (AST): 0 kept non-staged files import any deleted module.** Dangling textual sweep = 2 hits, both DOCSTRINGS (not imports).
- **§16.7 RE-PROOF:** 8 shared files CR-stripped md5 byte-identical (main.py `c15b4de6`, db `043f6033`, robinhood `2753939c`, base `bc8b6d76`, data_exec `8f7d2568`, kalshi_live `5c1a3551`, web/data `2643bfc4`, web/routes `49a785fa`); main.py survivor wiring UNCHANGED (bitunix **196**, mace **119**, pm_live_driver **4**, sched_pm_live **2**, shard **1**, pmcc/pead/donchian **2/2/3**). **This commit changes zero shared files** — the 2026-09-04 "silent main.py wiring loss" failure mode is structurally impossible here (main.py not in the diff).
- **Clean FF onto prod-live:** 0 behind / 1 ahead; merge-base == `3dd15c10`.
- ★ **6 inert danglers (documented, harmless):** `deploy/*/staged/*/main.py` (June bitunix/sfp survivor-deploy snapshots) hold 10 legacy refs each — frozen historical archives, never imported/run/py_compiled-as-live, NOT legacy-PM source (they predate Ph6). Left in place (survivor deploy provenance; out of scope). A later import-graph sweep will see these 6 — they are known-and-benign.

### 20.6 WEB-SURFACE SEPARABILITY (charter: "say honestly how separable")
**NOT cleanly separable by deletion — both are SHARED files needing in-file grafts:**
- `web/data.py` (6,717 LOC): legacy PM dashboard = `build_prediction_market_view` (L6190) + `_pm_venue`/`_pm_divisions_all`/`_pm_equity_at`/`_pm_summary` helpers + `_hydrate_pm_overview` (L1053) **WOVEN into the shared home hydration** (`_hydrate_pm_overview(...)` called at L812 inside the shared dashboard build, try/except-guarded → degrades to empty tiles, does not crash). Plus a **module-level** `from ...kalshi_crypto_vol_v2 import …` at **L19** → web/data.py won't even import without that legacy file. Removing the legacy dashboard = an in-file graft (delete the funcs + the L812 call + the L19 import), not a file deletion.
- `web/routes.py` (5,846 LOC): legacy whale-management routes lazily import 5 legacy modules (§20.4). Because the imports are **in-handler/lazy**, they pass py_compile and boot clean, then **500 at request time** if the modules are deleted — so they cannot be pure-deleted; the routes must be grafted out first.
- ⇒ **The whole web surface + its 13 shared-blocked keepers form ONE graft unit** (main.py factory + kalshi.py + web/data.py + web/routes.py), Jack-gated, restart-free for web (pm_web is separate) but a real code review. NOT attempted this pass.

### 20.7 RECOMMENDATION (Jack rules)
1. **Deploy Tranche 1** (FF `git push origin legacy-pm-untangle-2026-09-16:prod-live`) after 16:00 ET — pure deletion of dead code, no restart, no runtime effect (engine imports 0 at module level since Ph6). Lowest-risk, largest single safe reduction.
2. **Graft pass (deferred):** the 13 shared-blocked keepers + web dashboard + ~20 tranche-2 leaves + 8 systemd units + legacy config blocks — one deliberate code-review change editing main.py factory / brokers/kalshi.py / web/data.py / web/routes.py + trimming 2 tests. NOT in a restart window with live money unless the web edits are proven inert (pm_web is separate from the engine).
3. `_weather_math` + `kalshi_apify_client` are effectively PERMANENT keepers (survivor path_logger; Jack-ruled kalshi_whale_stats untouched). `kalshi_crypto_v2_observer` falls with Phase 7.

### 20.8 SESSION-8 CHANNEL / SCOPE NOTES
Read-only local analysis + local git only. **NO box access** (task: box read-only today; nothing here needed it). Analysis scratch under `cc/_untangle_scratch/` (graph.json, split.json, analyzers) is local-only, NOT committed. Nothing armed/disarmed/deployed/restarted/pushed. Tranche-1 commit is LOCAL and awaits Jack's FF push.
