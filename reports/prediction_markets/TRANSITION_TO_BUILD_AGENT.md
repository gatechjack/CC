# TRANSITION: Prediction Markets Platform — Planning → Phase-1 Build

**Written:** 2026-08-22 (planning-session wrap). **Audience:** the NEXT agent, who builds Phase 1.
**Planning session was PLAN-ONLY:** zero prod code touched, zero live divisions touched, zero agent_state writes, zero box connections. The plan is finalized and BOARD-APPROVED.

---

## a. WHAT THIS IS

You are the Phase-1 BUILD agent for the new **"Prediction Markets"** platform. A full planning session (2026-08-21/22) produced a finalized, board-approved executable plan:

> **THE PLAN (source of truth, executable spec):**
> `C:\Users\AA Incorporado\.claude\plans\fuzzy-zooming-dream.md`
> Header stamp: *"STATUS: BOARD-APPROVED 2026-08-22 (Jack). FINAL."*

Read it in full before doing anything. This transition doc is the portable summary + gate list; the plan file is the spec (datastore DDL, package layout, job flows, CLI surface, test plan, acceptance checklist). Per plan §4, your FIRST commit on the new branch includes the plan + vision as committed docs (`reports/prediction_markets/P1_PLAN.md` + `PLATFORM_VISION.md`) so they live on origin, not only in local files.

Companion evidence (this branch lineage): `reports/2026-08-21_whale_scouts/CLOSED_POSITIONS_API_FINDINGS.md` (the API characterization P1 is built on), `SCOUT_RESULTS.md`, `HANDOFF_SCOUTING_2026-08-21.md`.

---

## b. THE FULL VISION (locked 2026-08-21 by Jack via requirements interview — verbatim-faithful; do not re-litigate)

**What.** Consolidates the legacy prediction-market copy divisions (`poly_kalshi_mlb` live MLB copy + PCT paper farm) into ONE config-driven platform, "Prediction Markets". Legacy = the current live MLB division being replaced.

**Entity model (locked vocabulary):**
- **PREDICTION MARKETS** = one main-page tile → dashboard of SUB-DIVISIONS viewed by sub-division (flat cards, e.g. Jack-MLB, Karen-MLB, Jack-UFC). Sub-division detail: its whales + OPEN trades + CLOSED trades.
- **SUB-DIVISION = (ACCOUNT, CATEGORY) pair.** One Kalshi-API account can own multiple sub-divisions. "Jack/Karen" were naming only — **NO person entity**. Categories: MLB, UFC, NBA, Fed Rates (extensible).
- **FARM LEAGUE** = separate from divisions, organized by CATEGORY tabs. Each tab: (1) **WATCHLIST** — search results/candidates, does NOT paper-trade, Analyze-able; (2) **PINNED PAPER LIST** — forward paper-trading, **ONE paper record per whale-category pair** (category-level, shared across that category's sub-divisions, never duplicated per sub-division).
- **FARM ENTITY = WHALE-CATEGORY pair. PROMOTION** = attach a whale-category to sub-division(s), **JOINED ON CATEGORY** (a UFC whale-category cannot attach to an MLB sub-division). Same whale-category can attach to multiple sub-divisions independently (1/some/all chosen at promote time).
- **LIFECYCLE:** search → category WATCHLIST → board review (ANALYZE) → PIN to paper (forward paper starts) → observe → PROMOTE (choose sub-division(s) in that category) → live; **whale STAYS pinned while live** (paper record runs alongside). REMOVE from a live sub-division drops only that attachment. **watchlist→live directly is FORBIDDEN; pin is mandatory** (pin-then-immediately-promote allowed).
- **COPY BEHAVIOR:** a live whale-category copies **ONLY that category's trades** (category-FILTERED copy — NEW; legacy PCT was category-agnostic). **Signal → sizing/risk → execution are three SEPARATE concerns.**

**Shared Kalshi execution engine (key architecture):** ONE central config-driven engine — sub-divisions do NOT execute individually; each PASSES its trade to the shared engine, which sizes+places per that sub-division's CONFIG (risk, fixed-amount vs KELLY, routing). Each sub-division keeps its OWN trade log + Kalshi trade content. Detailed execution/risk config = future thread, deferred.

**Analyze button:** already LIVE on legacy PCT (Anthropic-API LLM whale analysis); improve by wiring to `/closed-positions` for UNTRUNCATED history. Runs on watchlist AND pinned.

**Search (productize the ad-hoc scout):** per category tab, button + basic filters → Polymarket-category search → adds to that tab's WATCHLIST. MUST include the two ranking routines already built (recency-weighted etc.) + net-scoring (SELL+REDEEM−BUY). **Win% is chalk — rank on NET ROI** (hard-won scout lesson). Detailed filter spec = Phase 2.

**Data foundation:** `data-api.polymarket.com/closed-positions` is THE record backbone (public, no auth, `user`/`limit≤50`/`offset`). COMPLETE cross-category per-whale resolved history, DIRECT `realizedPnl`, 1 row/market, ≥3,050 depth verified, same-day fresh. Gaps: no entry timestamp (NOT needed — the old "15m lag" was an Apify artifact); scale-ins collapsed; no fee field (Poly fees ~0); category derived from eventSlug prefix (~85-90%) + gamma tag-join `/markets?condition_ids=…&closed=true` (the `&closed=true` quirk is REQUIRED). `/positions` = open positions (live mark). `/activity` = live copy-signal only. "Reconstruct-from-/trades" was evaluated and DROPPED.

**Build strategy:** GREENFIELD, reuse legacy learnings/code (never reinvent), stand up ALONGSIDE running legacy → CUTOVER → retire legacy PM divisions. **Cutover = a data migration, not a shared-DB merger.** Separate DB for Prediction Markets (legacy SQLite stays for crypto/stocks). Geoblock = disregard for planning.

**Phasing:** **P1** = DB + `/closed-positions` ingestion + per-whale-per-category stats scoreboard. **P2** = farm reorg (category tabs, watchlist, pinned paper, search-to-watchlist, Analyze). **P3** = live structure (sub-divisions, account/API mgmt, category-filtered copy, shared engine + per-sub-division config, promote/remove, dashboard) — the money layer, LAST, on proven foundation.

**Access-control future-proofing (P3 constraint, no P1 build work):** multi-user auth later — full-admin (all divisions) + a **Prediction-Markets-only VIEWER** role (view ENTIRE PM division, all sub-divisions/accounts, NO other Trading Corp divisions). **"User/login" (person who views) ≠ "account" (a Kalshi API, a sub-division attribute)** — never conflate; P1 schema bakes in no single-user assumptions.

**Recency-as-sortable-spectrum (locked):** recency is a **sortable, data-dependent option, never one fixed definition**. Resolution-ts recency is correct ONLY for externally-pulled unknown-whale history (all of P1's data). For paper/live whales WE track, actual-trade history with REAL entry/trade timestamps is ALWAYS retained (P2/P3 tracked-trade tables), making entry-based recency a first-class sortable option. P1 schema must not — and does not — foreclose this.

---

## c. THE BRANCH-CREATION GATE — the #1 thing you must honor

**Do NOT create any branch until ALL FOUR pass, in order:**
1. **All MACE deploys have LANDED** (the staged queue in flight at planning wrap: P1.5 off-hours fix → deploy-gate tooling → P1.4 → prod-live advance). prod-live must reflect the box.
2. **Housekeeping complete** — worktrees clean, no orphans, prod-live reconciled (box ↔ git zero gap).
3. **Base verified ZERO-DRIFT** — on-box content diff of runtime files vs the prod-live tip (LF-md5 discipline) passes.
4. **Jack confirms the current prod-live tip SHA.** Report the candidate SHA to Jack and get explicit confirmation BEFORE cutting the branch.

**The plan header's `7150404` is the STALE planning-session base — never branch from it.** (At planning wrap, local prod-live had already moved to `398881b` via the in-flight MACE advance — and may move again. Use the CURRENT confirmed tip, whatever it is on your day.)

## c2. THE BRANCH MODEL (plan §4 — first-class, locked)

- **Durable integration branch `prediction-markets`** off the confirmed clean tip — the long-lived line all three phases land on. Phase branches (`prediction-markets-p1-…`, later p2/p3) **merge into it**. Never three disconnected phase branches.
- **Push to origin from the FIRST commit and keep pushed** — it's the multi-week build's backup AND the source future P2/P3 agents pull. First commit includes the plan + vision docs (see §a).
- **NO merge to `main` until CUTOVER.** The platform is a PARALLEL system until it replaces legacy; `main` stays clean. Merge to main happens ONCE, deliberately, at cutover — the same event that retires the legacy PM divisions. Cutover is a reviewable merge, not a gradual blur.
- **prod-live advances for DEPLOYED artifacts only** (P1's package + cron run on the box; additive-only, no restart — safe). Full dev history lives on `origin/prediction-markets`, not prod-live.
- **Drift watch:** P1 is greenfield (new files) → near-zero conflict risk. **P2/P3, when the dashboard wires into the EXISTING web app (routes/nav/shared templates): actively reconcile `prediction-markets` against `main`** to catch overlapping edits early. Standing coordination item.

---

## d. FIRST BUILD STEP AFTER BRANCHING: the G0 VALIDATION GATE (plan §8)

Legacy code (`trading_corp/scripts/seed_polymarket_watchlist_deep.py:57-62`) claims `/closed-positions` surfaces ONLY positive-PnL positions (survivorship). The 2026-08-21 probe verified DEPTH (≥3,050), **not loss-row presence** — a plan-review grep of SCOUT_RESULTS.md + the findings doc + the scout session transcript found ZERO documented negative-`realizedPnl` observations. **The concern is UNREMEDIATED. If positives-only were true, the entire ROI scoreboard would be chalk-biased garbage.**

**G0 (before building anything that depends on the data):** pull `/closed-positions` for these known net-loser wallets and assert rows with `realized_pnl < 0` exist; also pull one wallet twice to probe ordering stability.

| Name | Wallet (full, recovered from the scout session at planning wrap — SCOUT_RESULTS.md elides addresses) | Known NET (activity-method) |
|---|---|---|
| evanng (UFC) | `0x43e0f84fe8fb4623a5ff485fe9f7bc0f4b458618` | −$13,706.51 / −19.8% ROI / n=92 |
| csgod (UFC) | `0x8056189d56833ce5b3945dea9149b62c5111b64d` | −$9,551.47 / −24.1% ROI / n=80 |
| d1k21 (Fed) | `0x71ed0bc95433cdf1be29f43219725fce9addd9eb` | −$168,183.81 / −29.0% ROI / 20-2 (91%-win chalk-loser) |

**If G0 fails: STOP-AND-REPORT to Jack. There is NO pre-authorized pivot** — options (e.g. `/activity`+gamma reconstruction) are presented for his decision only at that point.

---

## e. THE 8 LOCKED DECISIONS (plan §13 — do not re-litigate)

1. **G0** runs as the HARD gate (unremediated in the record); stop-and-report on failure; no pre-authorized pivot.
2. **Category derivation:** two-tier (eventSlug prefix → gamma tag-join repair). Jack's 4 active categories (MLB/UFC/NBA/Fed) are clean-prefix.
3. **Roster:** NO cap for P1. Revisit at P2 search.
4. **Nightly refresh 03:00 UTC** — conditional on a cron-slot pre-check proving it clear of MACE/other cron+timer windows on the live box FIRST.
5. **Recency:** sortable `recency_basis` parameter (resolved_ts for external history now; real entry-ts basis activates with P2/P3 tracked-trade tables). Schema non-preclusion documented in plan §6.
6. **Host:** prod box, own process — standalone CLI + cron in the existing venv; deploy via the sanctioned runner; ZERO engine coupling, ZERO restarts.
7. **Future-proofing:** multi-user auth later (full-admin + PM-only VIEWER); `pm_user` ≠ `pm_account`; no single-user assumptions baked in.
8. **Branch strategy** per §c/§c2 above, including the branch-creation gate and Jack's tip confirmation.

Also locked earlier: **separate SQLite** `data/prediction_markets.db` (NOT Postgres, NOT the legacy DB) with versioned migrations — full reasoning + cutover requirements in plan §3.

---

## f. CURRENT STATE YOU INHERIT (as of planning wrap, 2026-08-22; re-verify read-only via `powershell -ep bypass -f .\pk_session_wrap_ro.ps1` — Jack executes runners)

- **`poly_kalshi_mlb` live loop: ARMED but GEOBLOCKED** (known, deferred — Kalshi city-string "Washington" (town of Washington VA, ZIP 22747) mis-triggering their WA-state sports ban on the egress 168.62.60.79; Kalshi support working it; DISREGARD for planning). Engine PID **809127** at last confirm, `auto_execute=True/dry_run=False/halted=False`. **live_whales (2) = SDTrading + xifutloong3.** Unchanged by the planning session.
- **PCT paper farm: 10 whales, all pinned** (`polymarket_copy_trader/selected_whales` + `pinned_whales`): UFC(5) Kh4mz4t, STC14, 000why000, 4751346, kutsumiakia · NFL(2) FordBronco, **AIisTheNewWD\*** · NBA(1) BetMechanic · FED(2) Kickstand7, pako. **\*AIisTheNewWD = TRUNCATED-MIRAGE — its scouted +$103k/39-0 baseline is NOT trustworthy (partial record); farmed only to observe forward.**
- **MACE deploys IN FLIGHT — NOT yours to touch.** Wait for them to land (branch-creation gate §c item 1). Standing constraint at wrap: no MACE restart after 15:45 ET until P1.5 deploys.
- **Memory anchors to read:** `poly-kalshi-mlb-live-2026-08-16` (legacy division ground truth), `prediction-markets-platform` (plan pointer + vision anchor), `poly-closed-positions-data-foundation-2026-08-22` (API characterization), `ufc-scout-and-paper-add-2026-08-21` (scouts), `command-paste-rule` (READ IT — sanctioned box channel + .ps1 authoring rules), `prod-live-deploy-base-rule`.
- **Legacy code to REUSE (explored in depth during planning; see plan §5):** `polymarket_data_api_client.py` (`ClosedPositionRow` verified to carry all 17 API fields — no client change needed; Cloudflare/5xx retry; Semaphore(5)), `kalshi_whale_stats.py` scoring primitives (`wilson_lcb_95`, `wilson_lcb_95_weighted`, `time_weighted_outcomes`, `_edge_factor`). Legacy scorer ENTRY POINTS have an impedance mismatch (require `/activity`-derived structures) — use the thin adapter approach per plan §7, do NOT call them directly.

## g. WHAT NOT TO DO

- **Don't touch the legacy divisions** (`poly_kalshi_mlb`, PCT, their agent_state keys, `data/trading_corp.db`) — they run untouched until cutover.
- **Don't branch before the gate** (§c: MACE landed + housekeeping + zero-drift + Jack's tip SHA confirmation). Never branch from `7150404`.
- **Don't merge to `main`** — not until the single deliberate cutover merge.
- **Don't skip G0** — and if it fails, stop-and-report; no self-authorized pivot.
- **Don't restart the engine or edit any existing file** — P1 is purely additive (new package, new DB file, new CLI, new tests).
- **Box access ONLY via the sanctioned `.ps1` runner channel that Jack executes** (see `command-paste-rule` memory) — no ad-hoc agent SSH, even read-only.
