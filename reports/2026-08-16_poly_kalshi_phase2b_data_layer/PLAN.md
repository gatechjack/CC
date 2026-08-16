# Phase 2b — Live mark-to-market DATA LAYER for the Poly Kalshi Copy dashboard

> **Read-only scoping + LOCKED DATA CONTRACT. Operator-reviewed & approved 2026-08-16. NOT built — build in checkpoints on operator go (fresh session per discipline).** Live loop stays ARMED/undisturbed; shared files (`kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py`) byte-unchanged.

## Context
`poly_kalshi_mlb` is deployed and LIVE (CP7). The goal is an entertaining, live-updating dashboard
for it — but the **UI (Claude Design) comes later**. First we need an honest **data layer**: live
unrealized mark-to-market on open positions, the Poly-bet trigger "why" per position, a
price-movement "game story," and a live-refresh mechanism. This plan is read-only scoping and a
**LOCKED DATA CONTRACT** the design prompt will bind to — deliberately honest about what is real vs
aspirational, so the fun dashboard is bound to data we actually have.

Two design decisions taken (operator): **bounded rolling mark-history** (→ a live yes-price sparkline
= the "game story"; Kalshi carries no score/inning) and a **~60s** mark cadence.

---

## Scoping findings (with evidence)

### 1. Game state from Kalshi — PRICE + RESOLUTION only; NO score/inning
- `KalshiBroker.get_market_resolution(ticker)` (`brokers/kalshi.py:309`) → `{status: resolved|pending|void|not_found, result: yes|no|void|None, ticker, close_time, expiration_time}` — settlement only.
- `KalshiBroker.quote(ticker)` (`kalshi.py:276`) → **yes-mid** float (0-1), via `get_market` + `get_orderbook(depth=1)`.
- `KalshiBroker.snapshot()` / `_fetch_positions()` (`kalshi.py:186,228`) → account state + per-position `market_exposure_dollars` / `position_fp` (contracts) / `realized_pnl_dollars` / `last_updated_ts`.
- The pykalshi MarketModel exposes `status` (open/settled), `result`, close/expiration times, ticker/title — **no live score, inning, or in-game status.**
- **Conclusion:** the "game story" must be **inferred from the yes-price trajectory** (the market's live probability that the bet-club wins). No new sports feed in this plan (per brief) → the **price sparkline is the story**.

### 2. Mark-to-market quote path — `quote()` on the shared client; load negligible
- Per open YES position: `unrealized = (current_yes_mid − fill_price) × contracts`. `quote(ticker)` returns `current_yes_mid`. Same shared client the loop/resolver/equity loops use (`KalshiLiveBroker._read` = `KalshiBroker`).
- **Current shared-client load:** ~2 `snapshot()` / 300s (arb+llm equity loops), a resolver **hourly burst up to ~350** `get_market`, occasional `get_market`. Adding **N `quote()`** (N = open positions, a handful) every 60s is negligible vs that burst.
- Use **per-open-ticker `quote()`** (not `snapshot()`): yes_mid is the natural sparkline series and it matches the dashboard's open-rows exactly.

### 3. Cache architecture — the `mace_rung_live` precedent
- MACE already does exactly this: a server-side loop writes ephemeral live values to a **volatile** table `mace_rung_live` (`persistence/db.py:488-494`; `INSERT OR REPLACE` via `set_live_state` `mace/execution.py:300-304`; written by the manage loop), and the cockpit reads it **broker-free** (`web/mace_view.py:331-340`, SELECT-only), rendering "as of {ts}" with a stale flag (`mace_view.py:269`), auto-refreshed by HTMX every 30s.
- **Recommend the same shape:** ONE server-side poller → volatile table(s) → dashboard reads broker-free. **NOT** per-browser-tab quote fetching (would multiply shared-client load). Marks **never** in `audit_event` (ephemeral; would flood the journal).

### 4. Trigger journaling (flag-2) — journal the Poly "why" at placement
- At `poly_kalshi_copy_trader._pipeline` (`agents/strategies/poly_kalshi_copy_trader.py:169-201`) the loop already HAS the trigger in scope: `r.slug`, `r.outcome`, `r.side` (the Poly bet), whale `name`/`wallet`, `p.market_type`, `m.kalshi_ticker`/`confidence` — but appends them only to the in-memory `shadow_log` (`:201`, lost on restart). At `:196` it calls `executor.submit(order, ...)`; `executor._record` writes the `poly_kalshi_order` audit row from `order` (has `whale`/`whale_wallet`, **not** the Poly slug/outcome).
- **Smallest change:** pass a `trigger` dict (`poly_slug`/`poly_outcome`/`poly_side` + `market_type`) into `submit → _record` (mirrors CP3's `fill` param), so the `poly_kalshi_order` payload carries the "why" persistently. Files: `poly_kalshi_copy_trader.py` + `poly_kalshi_executor.py` — **both this division's own files** (NOT the 3 shared files).

### 5. Live-row detection — MACE HTMX-poll precedent
- The PM dashboard has **no auto-refresh** today (pure server render; HTMX only on user action).
- MACE precedent: `hx-trigger="every 30s"` partial polling (`templates/partials/mace_live_sections.html:8-11`).
- poly_kalshi would use the same: a **live partial route + `hx-trigger="every 60s"`** re-rendering open positions + marks (from the volatile table) + a recent copy-moments feed (from audit rows). A NEW `poly_kalshi_order` row appears → the next poll picks it up; the UI compares the latest `order_id`/`ts` to the prior poll (client-side, Claude Design later) for the sound/flash.

---

## LOCKED DATA CONTRACT
Every field the future dashboard can bind to, labeled **AVAILABLE-NOW** vs **NEEDS-BUILD (CPn)**, with source. (This is the input to the Claude Design prompt.)

### Open position (per open `poly_kalshi_order` row)
| field | source | status |
|---|---|---|
| `order_id`, `ticker`, `outcome`(yes), `entry_ts` | audit payload (CP3) | AVAILABLE-NOW |
| `market_title` / team | ticker parse (CP3 open query) | AVAILABLE-NOW |
| `fill_price`, `contracts`(fill_count), `cost_basis`=fill_price×contracts | audit (CP3 Flag-1) | AVAILABLE-NOW |
| `whale` (copied handle/wallet) | audit payload | AVAILABLE-NOW |
| `poly_slug` / `poly_outcome` / `poly_side` (the "why") | flag-2 journaling | **NEEDS-BUILD (CP1)** |
| `current_yes_mid` (live mark) | mark poller → volatile table | **NEEDS-BUILD (CP2)** |
| `unrealized_pnl`=(yes_mid−fill_price)×contracts, `unrealized_pct` | poller | **NEEDS-BUILD (CP2)** |
| `mark_ts` + `stale` ("as of {ts}") | poller | **NEEDS-BUILD (CP2)** |
| `price_sparkline` (yes_mid rolling series) | bounded mark-history table | **NEEDS-BUILD (CP2)** |
| resolved outcome / realized P&L (won/lost) | CP4 resolver → `kalshi_round_trips` | AVAILABLE-NOW (post-settlement) |
| game score / inning / in-game status | — not in Kalshi data | **NOT AVAILABLE** (inferred via `price_sparkline`) |

### Copy-moment feed (recent placements)
| field | source | status |
|---|---|---|
| `order_id`/`ticker`/`whale`/`outcome`/`count`/`fill_price`/`ts` | audit | AVAILABLE-NOW |
| `poly_slug`/`poly_outcome`/`poly_side` | flag-2 | **NEEDS-BUILD (CP1)** |
| "new since last poll" key (`ts`/`order_id`) | audit | key AVAILABLE-NOW; detection **NEEDS-BUILD (CP3)** |

### Division-level
| field | source | status |
|---|---|---|
| realized P&L / win-rate / resolved count | CP4 resolved tiles | AVAILABLE-NOW |
| live total unrealized (Σ open marks) | poller | **NEEDS-BUILD (CP2)** |
| equity curve | — | NOT WIRED (separate pending item) |

---

## Build plan (data layer; checkpoint-phased; each independently reviewable; none deployed without operator go)

**CP1 — Trigger journaling (flag-2).** `poly_kalshi_copy_trader._pipeline` passes a `trigger` dict (`poly_slug`/`poly_outcome`/`poly_side` + `market_type`) into `executor.submit → _record`; the `poly_kalshi_order` payload gains those fields (mirrors CP3's `fill` param). Own files only; shared byte-unchanged. Tests: the row carries the trigger (dry-run + fake-broker live).

**CP2 — Mark poller + volatile cache (bounded history).** New volatile tables (mirror `mace_rung_live`): `poly_kalshi_mark_live` (one row per open position: order_id/ticker, yes_mid, unrealized, mark_ts, stale) + `poly_kalshi_mark_history` (bounded rolling yes_mid series per position, cap ~60 pts, insert+prune). A server-side poller loop (spawned in `main.py` alongside the kalshi equity loops, `~2151-2218`; uses the shared `KalshiBroker`; **~60s**) reads open `poly_kalshi_order` rows (broker-free), `quote()`s each open ticker, computes unrealized, writes the volatile tables + prunes history; stale flag on missed ticks. Marks **never** in `audit_event`. Tests: poller computes/writes/prunes + stale; assert no `audit_event` write. Reuse the equity-snapshot-loop spawn pattern + the `set_live_state` volatile-table idiom.

**CP3 — Dashboard data-read + live refresh.** A broker-free `data.py` query joining open poly_kalshi rows + trigger (CP1) + marks/sparkline (CP2) into the open-positions view + a copy-moments feed; a poly_kalshi **live partial route + `hx-trigger="every 60s"`** (mirror MACE) for auto-refresh + copy-moment detection. Surfaces marks + triggers + live refresh on the EXISTING dashboard (pre-Claude-Design). Broker-free reads only.

Deploy: each CP is operator-run (drift-gate + patch + restart for the poller; a DB migration for the volatile tables). Shared files untouched throughout.

---

## NOT in this plan (explicit)
- **Roster split** (watched→papered⇄live, atomic move) = **Phase 2a**, separate.
- **MLB-score external sports feed** = separate later decision (this plan infers the story from price only).
- **Claude Design UI** = after this data layer ships (binds to the LOCKED CONTRACT above).
- **Equity-curve wiring** for poly_kalshi = still its own pending item.

## Constraints
- Live loop stays ARMED/undisturbed. Shared files (`kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py`) **byte-unchanged** (diff each CP).
- Prod mutations at build time = operator-run runners (drift-gate + patch + restart; DB migration for the volatile tables).
- Marks + history are **ephemeral — never in `audit_event`**.

## Verification (per CP)
- **CP1:** unit-test the `poly_kalshi_order` row carries `poly_slug/poly_outcome/poly_side` (dry-run + fake-broker live); shared-file diff empty.
- **CP2:** unit-test the poller reads open rows → `quote()` → `unrealized=(yes_mid−fill_price)×contracts` → writes `_mark_live` + bounded `_mark_history` + stale flag (fake broker); **assert marks never hit `audit_event`**; confirm ≤ N `quote()` per 60s.
- **CP3:** integration-test the broker-free view joins open rows + trigger + marks/sparkline; the HTMX partial renders "as of {ts}" + copy-moments; a new `poly_kalshi_order` row appears on the next poll. Full suite green; shared-file diff empty.
