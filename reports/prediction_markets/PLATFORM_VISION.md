# Prediction Markets Platform — Authoritative Vision (LOCKED)

**Status:** LOCKED 2026-08-21 by Jack via a full requirements interview; BOARD-APPROVED 2026-08-22 as part of the Phase-1 plan.
**Source:** Jack's pasted spec (content of `/areas/prediction-markets-platform.md`). Reproduced here from the board-approved plan §2 — its explicit "durable record for P2/P3 sessions" — so the vision lives on `origin/prediction-markets` standalone, not only in an agent's memory or a local plan file. The full executable spec is the sibling `P1_PLAN.md`; the portable handoff is `TRANSITION_TO_BUILD_AGENT.md`.
**Audience:** every phase agent (P1/P2/P3). This is the durable record — do NOT re-litigate.

---

> Source: Jack's pasted spec (content of `/areas/prediction-markets-platform.md`), design locked 2026-08-21 via a full requirements interview. This section is the durable record for P2/P3 sessions.

**What this is.** Consolidates legacy `poly_kalshi_mlb` + PCT paper farm into ONE platform called "Prediction Markets". Legacy = the current live MLB division being replaced.

**Entity model (locked vocabulary):**
- **PREDICTION MARKETS** = one main-page tile → dashboard of SUB-DIVISIONS viewed by sub-division (flat cards, e.g. Jack-MLB, Karen-MLB, Jack-UFC). Sub-division detail page: its whales + OPEN trades + CLOSED trades.
- **SUB-DIVISION** = an **(ACCOUNT, CATEGORY)** pair. One Kalshi-API account can own MULTIPLE sub-divisions. "Jack/Karen" were naming only — there is **NO person entity**. Categories: MLB, UFC, NBA, Fed Rates (extensible).
- **FARM LEAGUE** = SEPARATE from divisions, organized by CATEGORY tabs. Each tab has TWO lists: (1) **CANDIDATES** = search results, does NOT paper-trade, but Analyze-able [CP3b-0 rename from "watchlist"]; (2) **PINNED PAPER LIST** = forward paper-trading, **ONE paper record per whale-category pair** (category-level, shared across all sub-divisions in that category — not duplicated per sub-division).
- **FARM ENTITY = WHALE-CATEGORY pair. SUB-DIVISION ENTITY = ACCOUNT-CATEGORY pair. PROMOTION** = attach a whale-category to sub-division(s), **JOINED ON CATEGORY** (can't promote a UFC whale-category to an MLB sub-division). Same whale-category can attach to multiple sub-divisions independently, chosen 1/some/all at promote time.
- **LIFECYCLE (locked):** search → category CANDIDATES → board review (ANALYZE button) → PIN to paper list (forward paper starts) → observe → PROMOTE (asks which sub-division(s) in that category: 1/some/all) → live; whale STAYS pinned while live (paper record keeps running alongside). REMOVE from a live sub-division drops ONLY that attachment (not back to farm — already pinned or re-findable). **CANNOT go candidate→live directly; pin is mandatory** (pin-then-immediately-promote allowed).
- **COPY BEHAVIOR:** live whale-category on a sub-division copies **ONLY that category's trades** (category-FILTERED copy — NEW; legacy PCT sim was category-agnostic). Signal → sizing/risk → execution are three SEPARATE concerns.

**Shared Kalshi execution engine (key architectural decision):** ONE central config-driven engine — sub-divisions do NOT each execute. Each sub-division PASSES its trade to the shared engine, which sizes+places per that sub-division's CONFIG (risk, fixed-amount vs KELLY sizing, routing needs). Each sub-division keeps its OWN log of the trade + Kalshi trade content. Detailed execution/risk config = its own future thread, deferred.

**Analyze button:** already LIVE on legacy PCT (Anthropic-API LLM whale analysis). Improve by wiring to `/closed-positions` for UNTRUNCATED full history. Runs on candidate AND pinned whales.

**Search (productize the ad-hoc scout):** per category tab, a button + basic filters runs a Polymarket-category search → adds to that tab's CANDIDATES. MUST include the two ranking routines already built (recency-weighted etc.) + net-scoring (SELL+REDEEM−BUY; **win% is chalk / rank on NET ROI** — hard-won scout lessons). Detailed filter spec = Phase 2.

**Data foundation:** `/closed-positions` is THE record-keeping backbone (public, no auth, `user`/`limit≤50`/`offset`). Complete cross-category history, direct `realizedPnl`, per-market grain, same-day fresh. **(P1 finding 2026-08-22 — `realizedPnl` is direct/per-leg-real for BINARY markets (all four live categories; Fed empirically proven) but is event-level and DECOUPLED from cost basis for negRisk winner-take-all markets, e.g. Politics; P1 quarantines the latter by invariant — see `P1_PLAN.md` §3A + `REALIZEDPNL_PROBE_RESULT.md`.)** Fields: proxyWallet, conditionId, slug/eventSlug/title, outcome/outcomeIndex/asset, avgPrice (scale-ins collapsed), totalBought, realizedPnl, curPrice (≥0.9=won), endDate + timestamp (RESOLUTION time). Gaps: no entry timestamp (**NOT needed** — the old "15m lag" was an Apify-API artifact); no fee field (Poly fees ~0); category not a field (derive from eventSlug prefix ~85-90% + gamma tag-join `/markets?condition_ids=…&closed=true` for ambiguous). Sibling `/positions` = current OPEN positions (live mark). Architecture = `/closed-positions` for records + `/activity` for live copy-signal detection. ("Reconstruct-from-/trades" evaluated and DROPPED.)

**Build strategy (locked):** GREENFIELD fresh build, REUSE legacy learnings/code (don't reinvent, build clean). Stand up ALONGSIDE legacy while it runs → CUTOVER → retire all legacy prediction-market divisions. **Cutover = a data migration, not a shared-DB merger.** Jack LEANS separate DB for Prediction Markets, legacy SQLite stays for crypto/stocks divisions. Trades through the shared engine as part of the Trading Corp app. Geoblock = DISREGARD for planning.

**Phasing (locked):** **P1** = DB + `/closed-positions` ingestion + per-whale-per-category stats scoreboard. **P2** = farm reorg (category tabs, candidates, pinned paper, search-to-candidates, Analyze). **P3** = Model-B live structure (sub-divisions, account/API mgmt, category-filtered copy, shared execution engine + per-sub-division config, promote/remove, dashboard) — the money layer, last, on proven foundation.

**FUTURE-PROOFING (P3, added 2026-08-21 — do NOT build in P1, do not preclude):** platform will need **multi-user auth**: full-admin (all divisions) + a **Prediction-Markets-only VIEWER role** (Jack's wife: view the ENTIRE PM division — all sub-divisions/accounts — but NO other Trading Corp divisions). **"User/login" (person who can view) is DISTINCT from "account" (a Kalshi API, a sub-division attribute).** P1's datastore/schema must not bake in single-user assumptions that would block a users/roles layer + PM-division-scoped access later.

---

### Locked design corollaries carried from the plan (see `P1_PLAN.md` for full reasoning)

- **Recency-as-sortable-spectrum (locked 2026-08-21):** recency is a **sortable, data-dependent option, never one fixed definition**. Resolution-ts recency is correct ONLY for externally-pulled unknown-whale history (all of P1's data). For paper/live whales WE track, actual-trade history with REAL entry/trade timestamps is ALWAYS retained (P2/P3 tracked-trade tables), making entry-based recency a first-class sortable option. P1 schema must not — and does not — foreclose this (`P1_PLAN.md` §6 non-preclusion; `pm_score_snapshot.params_json` records the `recency_basis` per score).
- **Separate datastore (locked):** a new, separate SQLite file `data/prediction_markets.db` (NOT Postgres, NOT the legacy `data/trading_corp.db`), accessed only by the new `trading_corp/prediction_markets/` package, with versioned migrations. Isolation from the live engine's DB is P1's whole safety story; cutover is a scripted data migration. Full reasoning + cutover requirements in `P1_PLAN.md` §3.
- **Naming discipline (locked):** `pm_account` = Kalshi API account (execution attribute, P3); `pm_user` = login persona (view access, future) — never conflated. `(wallet, category)` is the universal key across all phases.
