# Plan: Repurpose PCT into a Polymarket-signal → Kalshi-execution strategy (MLB)

## Context
The Polymarket-copy thesis is dead (US-blocked from Poly execution; flat copy-edge — this
session measured it). But PCT's real asset — **Polymarket whale identification** (discovery +
the realized-decomposition quality bar + the recency scorer) — is valuable. This plan redeploys
that asset: detect a listed whale's Polymarket bet, and **execute the equivalent trade on Kalshi**
(where we CAN trade), fast enough to beat the ~15-min lag that Kalshi-native copiers suffer.
Launch scope: **MLB single-games only** (cleanest 1:1 Poly→Kalshi mapping), **live-small, no
paper, no human-in-the-loop, market orders, fixed stake**. Ratified forks: **fast-poll MVP first**
(on-chain sub-second is Phase 2), **duplicate** the Kalshi executor (Kalshi Copy stays untouched).

The whales are there: this session's discovery probe found a real, quality MLB whale pool
(SDTrading $3.8M @ 100% clean-hold, etc.), so the supply side of the thesis is validated for MLB.

---

## 1. Three-layer build-vs-port breakdown (effort per layer)

### Layer 1 — Signal / detection  ·  **greenfield, the crux**
- Today: `polymarket_copy_trader.run_scan_cycle` (agents/strategies/polymarket_copy_trader.py:198)
  polls `PolymarketDataAPIClient.fetch_activity` (data/polymarket_data_api_client.py:420) every
  **60s**, limit 20 rows; detects BUY entries AND SELL exits explicitly. Total latency **70–120s**
  (60s poll + 10–60s API lag). **No on-chain/websocket infra exists for Polymarket** — the only WS
  in the repo is `data/bitunix_ws_feed.py` (different venue).
- **Phase 1 (MVP): tighten the poll** to ~5–10s on a small whale set + raise `activity_limit_per_poll`.
  Still ~5–15s latency — crushes the 15-min Kalshi-native lag. Effort **S** (config + loop reuse).
  Risk: Poly data-API 429/Cloudflare under fast polling (seen this session) → cap whale count, backoff.
- **Phase 2 (real edge): on-chain watcher.** Polygon event-log subscription (`eth_subscribe` logs via
  a new RPC provider — Alchemy/Infura/QuickNode, a **new dependency + credential**) on Polymarket's
  CTF Exchange / NegRiskAdapter `OrderFilled` events, filtered by active-list wallets, then resolve
  the ERC-1155 token id → condition_id → market. Effort **L** (greenfield; the token→market
  resolution is the hidden hard part — it may reintroduce an API hop, so "sub-second" is a real
  unknown). This is the biggest single build.

### Layer 2 — Matching  ·  **new, moderate**
- Poly side (`ActivityRow`, data/polymarket_data_api_client.py:133): `title` ("Will the Boston Red
  Sox beat the New York Yankees on 2026-08-15?"), `slug` (`mlb-red-sox-vs-yankees-2026-08-15`),
  `event_slug`, `outcome`, `condition_id`, `outcome_index`, `price`, `size`. Enough structure for
  **deterministic MLB matching** (team codebook + date regex).
- Kalshi side (`data/kalshi_market_map.py:146`): `MarketRecord.ticker` / `event_ticker`
  (`KXMLB-ARI-NYY-20260815-…`) / `title` / `subtitle`; MLB events prefix `KXMLB-`.
- **Design: a PRE-COMPUTED daily MLB game map** (Poly market ↔ Kalshi ticker), refreshed each
  morning + intraday. The seconds-critical path becomes an **O(1) lookup**, NOT an LLM call. LLM
  (`agents/llm.py` `build_chat_model`; pattern in `kalshi_llm_arbitrage.py`) is used **offline** to
  resolve ambiguous matches during map-build only — its ~5–10s latency never touches the hot path.
  A **match-confidence threshold**: below it, skip the signal (don't guess a live trade). Effort **M**.

### Layer 3 — Execution  ·  **port (duplicate), low**
- Reuse the pattern from `brokers/kalshi_live.py:315` `KalshiLiveBroker.place_order` (V2 event-order,
  **market orders** via `order_type="market"` + IOC, USD→contracts) and the UUID5 idempotency key
  `client_order_id` (kalshi_live.py:122). Credentials via `utils/secrets.py:356` `load_secrets`
  (KeyVault `KALSHI-API-KEY-ID` / `KALSHI-KAREN-API-KEY-ID` → PEM tempfile in `brokers/kalshi.py:116`).
- **Duplicate** the ~40-line placement into the new strategy with its **own `KalshiLiveBroker`
  instance**. Effort **S**.

---

## 2. Layer 3 recommendation: **DUPLICATE** (Kalshi Copy untouched)
`kalshi_copy_trader` **explicitly skips sports tickers** (kalshi_copy_trader.py, routes `KXMLB…` to
`kalshi_sports_scout`) → an MLB strategy has **zero runtime overlap** with Kalshi Copy. Duplicating
the placement pattern into the new strategy touches Kalshi Copy **not at all** (no regression risk to
a live division), honoring "prefer untouched." Trade-off: duplicated placement code — reconcile into
a shared `KalshiExecutor` module **later** (the Explore-agents' extract option) once both are stable.
- Duplicate: Copy-disruption risk **VERY LOW**; code-debt LOW-MEDIUM. ✅ chosen.
- Extract: cleaner long-term but edits the live division → deferred.
- Share broker instance: **rejected** — shared fill-callbacks would corrupt Kalshi Copy's whale
  snapshots; shared rate-limit counter. Each division keeps its own broker instance.

---

## 3. PCT repurpose blast radius

**Reused (the asset — no change):** whale discovery `scripts/refresh_polymarket_whales.py`; quality
bar `data/polymarket_whale_audit.py:686` `build_audit_report` (venue-agnostic); recency scorer
`scripts/polymarket_whale_recency.py` (venue-agnostic); the `selected_whales` copy-list machinery;
per-whale state (`whale_state:<wallet>`); the risk/halt framework (`agents/risk.py`,
`persistence/models.py` `StrategyState.persist_halt`).

**Retired (dead):** the Polymarket execution path — `brokers/polymarket.py` / `polymarket_live.py`
(was never live; division ships `standby: true`). No deletion needed; just unwired from the division.

**Changed (the new build):**
- **New strategy module** `agents/strategies/poly_kalshi_copy_trader.py` — detection loop adapted
  from `polymarket_copy_trader` (fetch_activity → detect BUY/SELL → dedup by tx hash), + MLB filter
  + match-map lookup + Kalshi ProposedOrder emission + duplicated Kalshi placement + exit tracking.
- **New map-builder** `scripts/build_mlb_poly_kalshi_map.py` — offline daily Poly↔Kalshi MLB map.
- `config/divisions.yaml` (polymarket_copy_trading entry, ~line 218): `broker: polymarket` →
  a Kalshi broker instance for this division; `standby: true` → false at arm; keep `enabled`.
- `config/strategies.yaml` (~line 100): new strategy block (poll interval, `activity_limit_per_poll`,
  fixed-stake sizing, MLB universe flag, `auto_execute`, `auto_execute_caps`, match-confidence-threshold).
- `config/risk.yaml`: reuse the `kalshi:` caps section (per-position, tail bounds) for this strategy.
- **main.py**: register the new strategy's scheduled loop + its own `KalshiLiveBroker` in
  `data_exec.brokers[division]` (mirror the kalshi_copy wiring at main.py:~4645/4771).
- **Memory**: add a project memory documenting the repurpose (thesis pivot, MLB scope, arm state).

Broker/loop/config wiring = the blast radius; the reused asset modules are imported, not edited.

---

## 4. Active-copy-list mapping (the trigger)
- **`agent_state(polymarket_copy_trader, selected_whales)` is the single authoritative copy-trigger
  list** — the loop reads it every cycle (`_load_selected_whales`, polymarket_copy_trader.py:761→220).
- **Manual pin:** dashboard promote (`web/routes.py:2944`) writes BOTH `selected_whales` AND
  `pinned_whales` → live next cycle, and survives refresh.
- **Discovery-query promotion:** `refresh_polymarket_whales.py:567` writes `selected_whales`, merging
  `pinned_whales` so pins always survive (algo picks under `--algo-select`, else pins-only).
- **New trigger = read `selected_whales`, unchanged** → "copy anything on the active list regardless
  of how it got there" is satisfied by reusing this exact list. (The new strategy may share the same
  `selected_whales` key or its own copy; sharing keeps one roster the operator already manages.)

---

## 5. Guardrail menu (no-HITL + no-paper ⇒ guardrails are the ONLY safety layer)

| Guardrail | Protects against | Reusable infra | Cost |
|---|---|---|---|
| **Fixed stake / per-trade cap** | oversized single bet | strategy sizing + `risk.py:211` per-trade cap | **S** |
| **Idempotency / dedup by tx hash** | double-fire (one whale action → ≥2 Kalshi trades) | Kalshi UUID5 `client_order_id` (kalshi_live.py:122) + per-whale `last_seen_txhashes` dedup | **M** |
| **Total daily deployment cap** | runaway loop draining the account | *in-memory* per-day counter (NOT the `audit_event` aggregate query — it froze the engine, removed 2026-06-16) | **M** |
| **Max concurrent open positions / total exposure** | unbounded simultaneous exposure | in-strategy open-position counter | **M** |
| **Daily-loss auto-halt (kill switch)** | a systematically wrong day with no human watching | `risk.py:144` daily-loss halt + `StrategyState.persist_halt` (survives restart) | **S-M** |
| **Max-slippage on market orders** | thin-Kalshi-book fills at bad prices | none direct; set a max-price cap on the IOC order (kalshi_live.py `limit_price` on a market/IOC) | **M** |

**Recommended MINIMUM for launch (live-small, no-paper, no-HITL):** fixed stake + tx-hash dedup +
daily deployment cap + max-concurrent cap + **daily-loss auto-halt**. The daily-loss halt was
deferred earlier; given no human and no paper stage, it is now **launch-critical** — it's the only
backstop against a systematically-wrong day, and it's cheap (reuse `StrategyState.persist_halt`).
Max-slippage full guard can be **v1.1**, mitigated at launch by a **tiny fixed stake** (size bounds
worst-case slippage) plus a simple max-price cap on the order. Kill-switch = `auto_execute:false`
(hot-reload) or division `standby:true` + restart.

---

## 6. Phased sequence (to a safe minimal live-small first version)

**Phase 0 — Discovery/roster ready (mostly done):** confirm the MLB whale roster in `selected_whales`
(this session's probe found the pool; wire pins/promotion). No new code.

**Phase 1 — Fast-poll MVP, LIVE-SMALL (the first live version):**
1. Map-builder `build_mlb_poly_kalshi_map.py` (offline daily Poly↔Kalshi MLB map; deterministic +
   offline-LLM fallback; confidence threshold). Verify the map by hand against a day's slate.
2. New strategy `poly_kalshi_copy_trader.py`: detection loop (reused) → MLB filter → map lookup →
   Kalshi ProposedOrder (market, fixed stake) → **duplicated** Kalshi placement → exit tracking
   (mirror whale SELL). Own `KalshiLiveBroker` instance.
3. Guardrails: the recommended-minimum set (§5), unit-tested hard (dedup + daily-cap + halt).
4. Config wiring (divisions/strategies/risk) + main.py loop + broker registration.
5. **Gate to live:** a dry-run trace on a live slate (emit-only, place nothing) confirming
   detect→match→order shape; then arm `auto_execute:true` at **tiny stake**, watch the first fills.

**Phase 2 — On-chain sub-second (the real latency edge):**
6. Polygon RPC provider + `OrderFilled` log subscription + token→market resolution; swap the
   Phase-1 fast-poll signal source for the on-chain feed behind the same detection interface.

**Phase 3 — Hardening/expansion:** full max-slippage guard; extract the shared `KalshiExecutor`;
widen beyond MLB single-games.

---

## 7. Honest complexity verdict
**A month+ to a robust system; ~2–3 focused weeks to Phase-1 live-small.** Phase 1 is mostly
reuse + wiring (detection loop, Kalshi placement, risk/halt all exist) — the genuinely new pieces
are the MLB match-map and the guardrail wiring. **Phase 2 (on-chain) is the long pole (L)** and
carries the most unknowns.

**Biggest risks / unknowns:**
1. **"Seconds" may be harder than it looks** — even on-chain, resolving the ERC-1155 token → Poly
   market → Kalshi ticker may reintroduce an API hop; sub-second is unproven until built.
2. **MLB matching edge cases** — postponements, doubleheaders, run-line vs moneyline vs total, rain
   delays. The confidence-threshold-to-skip mitigates, but coverage/precision needs measurement.
3. **No paper + no HITL ⇒ a guardrail bug = real loss.** Dedup and daily-cap must be bulletproof and
   tested; this is where the risk concentrates.
4. **Poly fast-poll rate limits** (429/Cloudflare, seen this session) at ~5s cadence across whales.
5. **Kalshi book liquidity/slippage** on less-liquid games with market orders.
6. **Edge-transfer is unproven** — the Poly copy-edge was flat; the thesis is that *speed* (beating
   15-min) creates edge on Kalshi. That's a hypothesis to validate live-small, not a given — Phase 1
   at tiny stake is itself the experiment.

---

## Verification (how to prove Phase 1 works, before/at arm)
- **Unit tests (pure):** tx-hash dedup (one action → one order), daily-cap counter (blocks at N),
  max-concurrent cap, daily-loss halt latch (halts + persists + survives restart), MLB
  matcher (team codebook + date → correct `KXMLB-` ticker; below-threshold → skip).
- **Map validation:** run `build_mlb_poly_kalshi_map.py` on a live slate; hand-check every game's
  Poly↔Kalshi pairing.
- **Dry-run trace (place nothing):** run the strategy against a live slate in emit-only mode; confirm
  detect→match→ProposedOrder shape + that guardrails fire on synthetic double-signals.
- **Live-small arm:** `auto_execute:true` at tiny fixed stake on 1–2 whales; watch the first
  detect→match→fill end-to-end + confirm dedup/caps/halt behave on real fills. Kill-switch =
  `auto_execute:false` (hot-reload) / `standby:true`.
- **Regression protection for Kalshi Copy:** none needed by construction (duplicate, zero overlap) —
  but assert `kalshi_copy_trader` is byte-unchanged in the diff before arm.
