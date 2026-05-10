# Trading Corp — Production Deploy Log

**Purpose.** Append-only record of every production deploy. The
prod VM has no git, so this file is the single source of truth for
"what's running on prod right now."

**Why this exists.** Recurring failure mode pre-2026-05-02: forgetting
that a feature already shipped (because it was bundled in a bulk-track
commit, or scaffolded forward-compat in an earlier phase, or
implemented before the BACKLOG.md item was retired). The fix is
captured in CLAUDE.md §1 — "Before any deploy-adjacent work" — and
this log is the artifact that makes it possible.

**Source of truth precedence:**
1. `runbooks/deploy_log.md` (this file) — what's on prod right now
2. md5-diff between local and prod — verify before deploying
3. `BACKLOG.md` — what we want to do, NOT what's done
4. Memory entries — same caveat as BACKLOG.md

---

## Template for new entries

```markdown
## YYYY-MM-DD HH:MM UTC — <phase or feature label>

**Commits:** <commit-hashes>
**Triggered by:** <user-request or session-context>
**Backup tag:** `.pre-<label>-YYYYMMDD-HHMM` (or `n/a` for first-shipment of new files)

**Files deployed (N):**
- `<path>` — <one-line summary of change>

**Features shipped (load-bearing for future "is X done?" checks):**
- <feature 1>: <what's now live, observable how>
- <feature 2>: <...>

**Notable code changes (callouts a future Claude shouldn't miss):**
- <change>: <where it lives, why it matters>

**Latent bugs caught + fixed (if any):**
- <bug>: <symptom, fix, where>

**Verification:**
- <PID change, audit row landing, dashboard probe, etc.>

**Inert / dormant on current traffic (if any):**
- <code that's deployed but not exercising — why, and what would trigger it>

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=<backup-tag>; BASE=/home/azureuser/trading_corp; \
for f in <list>; do mv \$BASE/\$f.\$TAG \$BASE/\$f; done; \
rm -rf <new-files-or-dirs>
"
```
```

---

## 2026-05-10 00:39 UTC — Polymarket wallet went live (KV upload + service restart)

**Not a code deploy** — wallet/secrets bring-up. Board completed steps 1-4 of the Phase 2a pre-enable checklist between 2026-05-09 22:00 UTC and 2026-05-10 00:30 UTC: generated EOA via `eth_account.Account.create()` (regenerated once after losing the first address — wallet wasn't funded, zero loss), Alchemy Polygon Mainnet signup + RPC URL, $500 native USDC + 98 POL funded from Coinbase to the EOA on Polygon mainnet, `az keyvault secret set` for the three secrets.

**On-chain verification (pre-restart, public RPC):** native USDC `0x3c49…3359` = $500.00, USDC.e bridged = $0.00 (no misrouted tokens), POL/MATIC = 98.375 (~$39 at $0.40/POL — way more than needed for gas).

**KV state confirmed (presence-only, no values exposed):**
- `POLYMARKET-PRIVATE-KEY`: enabled, length 64 (no `0x` prefix; `eth_account.Account.from_key` accepts both forms — harmless for Phase 1 since signing isn't in the path)
- `POLYMARKET-FUNDER-ADDRESS`: enabled, length 42 (`0x` + 40 hex ✓)
- `POLYGON-RPC-URL`: enabled, length 62 (sensible for Alchemy or public RPC)

**Pre-restart NSG actions:** Board's laptop IP rotated TWICE during this session (`98.231.16.63` → `73.104.119.214` mid-session for the Phase 2a code deploy; rotated back to `98.231.16.63` for the wallet bring-up). Both updated cleanly via `az network nsg rule update` per `auth_lockout_recovery.md`.

**Restart:** PID 176618 → 177477 (clean). Boot log:

```
PolymarketBroker connected (funder=***REDACTED***, equity=$500.00, 0 positions)
PaperBroker connected (account=paper_polymarket_copy_trading, equity=$0.00)
Polymarket arbitrage scanner online (enabled=False, auto_execute=False)
```

**Three things confirmed by that one log line:**
- USDC balance reads cleanly from Polygon RPC via `eth_call(USDC.balanceOf)`.
- RedactingFilter scrubs the funder address from log output (literal-value redaction registered in `secrets.py:load_secrets()`; the address is in memory + KV but never in logs).
- `data-api.polymarket.com/positions?user=…` returned empty — correct for fresh wallet.

**Dashboard verification:**
- Home tile **Polymarket Arbitrage** = `$500.00` (was `$0 STUB`)
- `/division/polymarket_arbitrage`: Equity $500.00 / Cash $500.00 / Buying Power $500.00
- `polymarket_copy_trading` tile = `$0.00 STANDBY` (paper-fallback by design until Phase 4+)

**Phase 2a pre-enable checklist status (steps 5-7 remaining, all server-side):**

| # | What | Status |
|---|---|---|
| 5 | Tune gamma-api query (current default page sort returns long-tail markets that fail 7-day cap) | Next session |
| 6 | Phase 2.5 Backtester (binary-outcome replay, minimal-viable) | Next session — gates Phase 3 |
| 7 | Flip `polymarket_arbitrage.enabled: true` in `strategies.yaml` | After 5 + 6 + Board "go" |

**Rollback recipe (if needed):**

```bash
# Rollback the wallet-going-live state by removing the secrets from KV.
# Service restart after this puts the broker back into stub mode.
az keyvault secret delete --vault-name kv-tc-vtwbowt3wtkpy --name POLYMARKET-PRIVATE-KEY
az keyvault secret delete --vault-name kv-tc-vtwbowt3wtkpy --name POLYMARKET-FUNDER-ADDRESS
az keyvault secret delete --vault-name kv-tc-vtwbowt3wtkpy --name POLYGON-RPC-URL
ssh azureuser@trading.jacksumner.com "sudo systemctl restart trading-corp"
```

(The wallet itself remains funded on-chain regardless. Code rollback recipe for the Phase 2a code deploy is in the previous deploy_log entry.)

---

## 2026-05-09 21:57 UTC — Polymarket Phase 2a: arbitrage scanner + risk caps + scheduler wiring

**Commits:** `fe757e2` (Phase 2a, committed pre-deploy).
**Triggered by:** Phase 2 strategy build (greenlit by Board after the Phase 1 ship + 5-question memo answers earlier same day). Path B chosen for the LLM call (direct Anthropic, NOT through Research firm — Thesis schema doesn't fit prediction-market probability queries; Polymarket arbitrage is single-division decision logic, not cross-division knowledge work). All 9 risk caps confirmed verbatim from Q1 answer; K=10 / 6h cooldown from Q3 answer 'a'; 7-day max horizon from Q2; defensive httpx rate-limiting from Q5.
**Backup tag:** `pre-polymarket-phase2a-20260509.tar.gz` at `/home/azureuser/backups/` (5 modified files; 2 new files have no pre-state).
**Pre-deploy NSG action:** Board's laptop IP rotated mid-session (Comcast); old `98.231.16.63` → new `73.104.119.214` updated on `tc-prod-nsg/AllowSSHFromHome` via the standard `auth_lockout_recovery.md` Cloud-Shell-or-az-CLI path. Documented as the correct recovery; not a deploy concern.

**Files deployed (5 modified, 2 new):**

- `config/risk.yaml` — new `polymarket:` top-level block. All 9 caps from Q1 answer (universe pre-filter: min volume $50K / max spread 3¢ / min ttr 24h / implied-prob bounds 5-95%; per-order: 5%-of-equity / $250 single-market; aggregate: 25%-equity-cap-$1K daily / $1K total open).
- `config/strategies.yaml` — new `polymarket_arbitrage:` block (enabled:false, auto_execute:false, K=10, 6h cooldown, 7d horizon, fixed_usdc/$1 sizing). Plus a documented `polymarket_copy_trading:` placeholder for Phase 4+.
- `trading_corp/agents/risk.py` — new `_evaluate_polymarket()` branch routed by the `is_prediction_market` extra flag. Atomic + aggregate caps; halt checks still run BEFORE the polymarket branch. Daily-aggregate cap queries audit_event for today's `would_have_placed`/`board_approved`/`filled` rows; total-open cap returns 0 in Phase 2a (Phase 3 implements). `evaluate()` signature gained an optional `db_url` kwarg (back-compat: existing callers don't pass it, aggregate checks no-op).
- `trading_corp/brokers/polymarket.py` — new `list_markets(filters)` method against gamma-api with deterministic Python-side filtering. New `_http_get_json()` helper with concurrency cap (semaphore=6) + 429 backoff (max 4 retries, 1-30s window with jitter).
- `trading_corp/main.py` — new `_scheduled_polymarket_arb_loop()` spawned alongside `donchian_task`. Re-reads `poll_interval_sec` each tick so config changes don't need a restart. Routes emitted ProposedOrders through `_run_order` (existing risk + HITL graph). Telegram pings on each emission.
- `trading_corp/agents/strategies/polymarket_arbitrage.py` — **NEW.** PolymarketArbitrageAgent. mtime-cached config, per-market 6h cooldown in agent_state (single-JSON-blob with cleanup-on-load). Direct Anthropic call via `agents.llm.build_chat_model`. Permissive JSON parser handles prose-wrapped output; clamps prob_yes to [0.01, 0.99]; normalizes unknown confidence to "medium". Defensive implied-prob extraction handles outcomePrices-as-string, outcomePrices-as-list, lastTradePrice, price.
- `trading_corp/agents/strategies/_polymarket_prompts.py` — **NEW.** Shared analyst-persona system prompt (~1554 estimated tokens). Imported by arbitrage today, by future copy_trading later — Anthropic's prompt cache amortizes the input-token cost across both strategies (5-min ephemeral TTL; ≥1024-token threshold cleared with substantive methodology + worked example).

**Features shipped (load-bearing for future "is X done?" checks):**

- The Polymarket scanner loop is online but inert. Boot log:
  `"Polymarket arbitrage scanner online (enabled=False, auto_execute=False)"`. To activate: Board flips `polymarket_arbitrage.enabled` in `strategies.yaml` AND uploads the 3 KV secrets.
- Risk gate now routes prediction-market orders by `extra.is_prediction_market` flag — clean separation from PMCC/crypto cap logic.
- Anthropic prompt-cache-ready system prompt is in the codebase; both Polymarket strategies will share it. ~85% input-token cost reduction on K-1 follow-up calls per cycle.
- 19 new pytest cases in `tests/test_polymarket_arbitrage.py` regress: config defaults, implied-prob extraction (4 shapes), JSON parse robustness (clean/prose/OOB/garbage/unknown-confidence), risk-gate cap matrix (approve, implied-bound rejects, single-market $ cap, per-position % cap, halt-precedence, non-polymarket-isolation).

**Notable code changes (callouts a future Claude shouldn't miss):**

- The strategy emits ProposedOrder.extra with `is_prediction_market: True`. **The risk gate routes EXCLUSIVELY off this flag**, not off `order.strategy == "polymarket_arbitrage"`. When `polymarket_copy_trading` ships, it should set the same flag — that single change makes it inherit all 9 caps without modifying risk.py.
- `RiskAgent.evaluate()` gained an optional `db_url` kwarg for the daily-aggregate cap query. Existing callers (PMCC, donchian, otter, cypher, manual) don't pass it; their behavior is unchanged. The Polymarket scheduler in main.py needs to start passing it once `enabled: true` flips and aggregate caps actually bind.
- The shared analyst prompt is intentionally substantive — methodology + worked example clear the 1024-token cache threshold. Trimming the prompt below ~1300 tokens would silently disable the cache and quintuple input costs at K=10/30s.
- Aggregate query (`_sum_polymarket_today`) uses `substr(ts, 1, 10)` for date partitioning. Switches to UTC midnight; if Board ever wants ET-based daily aggregates, that's a single-line change but flag the semantics in audit.

**Verification:**

- Pre-restart PID 175242 → post-restart 176618 (clean).
- All 7 prod files match local LF-normalized md5s exactly after SCP.
- Boot log:
  - `Registered polymarket broker for division=polymarket_arbitrage (paper=False)` ✓
  - `Registered paper broker for division=polymarket_copy_trading (paper=True)` ✓
  - `PolymarketBroker connected as STUB (missing funder or RPC URL)` — expected (KV uploads still pending Board action)
  - `Polymarket arbitrage scanner online (enabled=False, auto_execute=False)` — exactly the inert posture Phase 2a ships
- KV fetch attempts for `POLYMARKET-PRIVATE-KEY` / `POLYMARKET-FUNDER-ADDRESS` returned empty (graceful fallback to stub).
- 19 new tests pass; 9 existing risk_gates tests pass unchanged; 508 tests in the broader suite pass.
- Live test of `list_markets()` against gamma-api (executed pre-deploy): real markets fetched, 7 returned with default filter, 0 returned with Phase 2 caps applied (gamma-api default page sort isn't volume-first; tuning the query is a pre-enable follow-up flagged in the file's docstring).

**Inert / dormant on current traffic:**

- Scanner loop wakes every 30s, no-ops on `enabled: false`. **Zero LLM calls; zero cost.**
- Cooldown table in agent_state stays empty until first cycle with `enabled: true`.
- Aggregate-cap query (`_sum_polymarket_today`) returns 0.0 — no Polymarket audit rows yet.
- `polymarket_copy_trading` tile remains paper-fallback STANDBY $0.

**Pre-enable checklist (Board action):**

1. Generate wallet (`python3 -c "from eth_account import Account; ..."`).
2. Sign up at alchemy.com, copy Polygon Mainnet HTTPS URL.
3. Fund EOA with $500 native USDC + ~$5 MATIC on Polygon.
4. Upload to KV: `POLYMARKET-PRIVATE-KEY` / `POLYMARKET-FUNDER-ADDRESS` / `POLYGON-RPC-URL`.
5. Tune `gamma-api/markets` query to surface high-volume / short-tail markets (current sort returns long-tail first; Phase 2 caps reject them).
6. Phase 2.5 Backtester verdict (replay-only minimal-viable; greenlit but not yet built).
7. Flip `polymarket_arbitrage.enabled: true` in `strategies.yaml` (no service restart needed — mtime-cached).
8. Watch the activity rail on `/division/polymarket_arbitrage` for `would_have_placed` rows.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-polymarket-phase2a-20260509
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
rm -f trading_corp/agents/strategies/_polymarket_prompts.py \
      trading_corp/agents/strategies/polymarket_arbitrage.py
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 20:13 UTC — Polymarket Phase 1: read-only broker + division wiring (+ Phase 0 secrets backfill caught at deploy)

**Commits:** `db1f0cd` (Phase 0 secrets, never previously deployed) + `d7cbea2` (Phase 1 broker + wiring, committed pre-deploy).
**Triggered by:** Polymarket Arbitrage division scope (multi-message in-session brief; see CLAUDE.md §6 STOP-AND-ASK items resolved 2026-05-09 ~17:30-19:00 UTC). Phase 0.5 EU egress proxy was scoped, then ruled NO-GO by the smoke test — Polymarket's read APIs serve tc-prod-vm's US-east IP without geo-block. Phase 1 ships read-only adapter + tile rendering inert; goes live the moment the KV secrets land.
**Backup tag:** `pre-polymarket-phase1-20260509.tar.gz` (21K, 4 modified files) at `/home/azureuser/backups/`. Plus an extra `secrets.py.pre-polymarket-phase1-20260509.bak` snapshot for the secrets.py rollback (because Phase 0 was caught mid-deploy — see Notable below).

**Files deployed (5 modified, 1 new):**

- `trading_corp/utils/secrets.py` — Phase 0 backfill caught at deploy time. Three new fields on `Secrets` (`polymarket_private_key`, `polymarket_funder_address`, `polygon_rpc_url`). New `register_redact_literal()` mechanism + `_REDACT_LITERALS` set for value-substring scrubbing of secrets that third-party libs may log raw. KV expected_env_vars extended. Three new entries on `_SECRET_KEY_NAMES` for KEY=value redaction.
- `trading_corp/brokers/base.py` — `ReadOnlyBroker` ABC extracted (connect/disconnect/snapshot/quote). `Broker` now subclasses it (adds place_order + cancel_order). Behavior-zero change for existing brokers; PolymarketBroker is the first ReadOnlyBroker subclass.
- `trading_corp/brokers/polymarket.py` — **NEW.** PolymarketBroker(ReadOnlyBroker). Stub mode if creds missing. snapshot() = USDC balance via Polygon RPC `eth_call` + positions via data-api. quote() = gamma-api slug→token_id then clob last-trade-price. `signature_type=EOA` pattern (signer == funder, no Polymarket proxy/SAFE) — Path A wallet model. NO place_order method exists; ABC enforces read-only.
- `trading_corp/main.py` — `_build_broker_for_division` polymarket family branch. No PaperExecutionBroker wrap (ReadOnlyBroker has no order surface to simulate).
- `trading_corp/utils/divisions.py` — new "polymarket" investment-type group between Crypto and Retirement. Slug-prefix classification handles the paper-fallback copy-trading division (broker=paper but slug starts with `polymarket_`).
- `config/divisions.yaml` — two new entries: `polymarket_arbitrage` (broker=polymarket, real adapter, standby) + `polymarket_copy_trading` (broker=paper, $0 placeholder for Phase 4+ copy-trading strategy, standby).

**Features shipped (load-bearing for future "is X done?" checks):**

- Home dashboard renders a new "Polymarket" investment-type group (4th group, between Crypto and Retirement) with TWO tiles: "Polymarket Arbitrage" + "Polymarket Copy Trading". Both render STANDBY today.
- ReadOnlyBroker ABC is now in the codebase. The Fidelity migration TODO from CLAUDE.md §7 sharp edges is now strictly possible (separate cleanup; not done here).
- Phase 0 secrets-loader for Polymarket creds + literal-value redaction is live on prod.
- The 2026-05-09 EU-egress smoke test runbook (`runbooks/eu_proxy_smoke_test.md`) is preserved as the starting point if Phase 3 trade placement turns out to need a proxy.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **Phase 0 was caught at deploy time, not pre-deploy.** I shipped Phase 1 first thinking Phase 0 was already on prod (it was committed locally as `db1f0cd` but never SCP'd — I made a "bundle the deploy with Phase 1" call earlier in the session and forgot to honor it). The service crash-looped on `AttributeError: 'Secrets' object has no attribute 'polymarket_private_key'` for ~90s before I caught it via boot-log inspection, SCP'd `secrets.py`, and restarted clean. Lesson: when a code commit references new fields on a shared dataclass, deploy the dataclass file BEFORE the consumer file, OR deploy as one atomic batch.
- The `polymarket_copy_trading` division uses `broker: paper` deliberately. Both polymarket_* divisions land in the new "Polymarket" investment-type group via slug-prefix classification (utils/divisions.py:_POLYMARKET_SLUG_PREFIX). The arbitrage division's paper-fallback would conflict with broker:polymarket on the same wallet (both tiles would show the same balance) — broker:paper for the second tile keeps it visibly distinct ($0 STANDBY) until Phase 4+ wires the real strategy.
- PolymarketBroker is NOT wrapped in PaperExecutionBroker. The convention for PAPER mode (wrap-real-broker-with-paper-fills) doesn't apply to ReadOnlyBroker subclasses — there's no order surface to simulate. If a future Polymarket division needs paper-mode order simulation (Phase 2 strategy paper-track), the new code path will be `PolymarketLiveBroker(Broker)` in Phase 3, and PaperExecutionBroker will wrap THAT.
- The `private_key` constructor arg on PolymarketBroker is accepted but unused in Phase 1. Phase 3 signing will read from the same arg without a constructor change.

**Latent bugs caught + fixed (if any):** none new. The pre-existing `secrets.py.pre-polymarket-phase1-20260509.bak` confirms prod was running the pre-Phase-0 file before this deploy — no drift content beyond "version skew due to my earlier deferral."

**Verification:**

- Pre-restart PID 171746 → post-restart 175242 (clean).
- All 5 prod files match local LF-normalized md5s exactly after SCP.
- Boot log:
  - `PolymarketBroker connected as STUB (missing funder or RPC URL)` — expected with no KV secrets yet.
  - `PaperBroker connected (account=paper_polymarket_copy_trading, equity=$0.00)` — copy-trading placeholder healthy.
  - `Registered polymarket broker for division=polymarket_arbitrage (paper=False)` ✓
  - `Registered paper broker for division=polymarket_copy_trading (paper=True)` ✓
  - KV fetches for `POLYMARKET-PRIVATE-KEY` / `POLYMARKET-FUNDER-ADDRESS` / `POLYGON-RPC-URL` returned empty (Board hasn't uploaded yet — graceful degradation to stub mode is the design).
- `GET /` returned HTTP 200 in 4.87s, 87.2 KB.
- `<h2>` headers in document order: `Individual` → `Crypto` → `Polymarket` → `Retirement`. Section order matches `_INVESTMENT_TYPE_ORDER`.
- Both Polymarket tiles render with STANDBY badges. 4 STANDBY badges total on home page (2 new Polymarket + 2 existing: Coinbase Futures, BitUnix Futures).

**Inert / dormant on current traffic:**

- PolymarketBroker `snapshot()` and `quote()` return zeros / empty until KV holds the three secrets. After Board uploads them, next service restart brings the arbitrage tile live with real wallet balance + open positions (initially: $500 USDC, 0 positions).
- PolymarketBroker.quote() field-mapping (gamma-api `clobTokenIds` / `outcomes`) is best-effort against unverified shape — first non-empty response from a real market should be eyeballed to confirm. Field names in `_fetch_positions` similarly defensive (.get() with fallbacks); first funded-wallet response should be sanity-checked.
- Phase 3 follow-up tracked as task #31: re-test geo-block on authed/write CLOB endpoints before live order placement. If writes are blocked, revive `runbooks/eu_proxy_smoke_test.md`.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-polymarket-phase1-20260509
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
# Phase 0 secrets.py rollback (separate backup since the tarball was made
# pre-Phase-0-discovery; the pre-Phase-0 file is in its own .bak):
cp /home/azureuser/backups/secrets.py.pre-polymarket-phase1-20260509.bak \
   trading_corp/utils/secrets.py
# Drop the new file:
rm -f trading_corp/brokers/polymarket.py
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 16:42 UTC — Donchian: observe Board-driven balance changes; state-as-source-of-truth

**Commits:** `78e57a0` (committed before deploy).
**Triggered by:** Board direction (chat 2026-05-09 post-UI-cleanup) — recurring weekly BTC buys + occasional cash deposits land on the coinbase_spot account outside the strategy's knowledge. The strategy must observe these (log them, attribute to Board) but NOT auto-flip state in response. Strategy state is now the source of truth for portfolio composition; the broker's balance is normalized to it at the next BUY/SELL signal, not via a forced rebalance trade.
**Backup tag:** `pre-balance-tracking-20260509-utc-pre.tar.gz` at `/home/azureuser/backups/` (43K, 4 modified files; no new files).
**Pre-deploy DB mutation:** `UPDATE agent_state SET value_json='{"state":"cash","cost_basis":null}' WHERE agent='coinbase_btc_donchian' AND key='state';` ran ~1 min before the systemctl restart. Today's earlier startup reconcile (15:23 UTC deploy) had set state=BTC with cost_basis=$80,371.17 — but per the new model, the 0.595 BTC was always Board's, not the strategy's, so CASH is the correct strategy view. Previous values preserved in deploy_log + the row's prior `updated_ts` for rollback.

**Files deployed (4 modified):**

- `trading_corp/agents/strategies/coinbase_btc_donchian_agent.py`:
  - `PersistedState` gains `last_known_cash` + `last_known_btc_qty` (defaults None) — baselines for delta detection.
  - New persistence key `last_known_balances` (alongside existing `state` + `last_bar_ts`).
  - `_loaded_from_db: bool` flag — flips True when `_restore_from_db` finds a state row.
  - `restore_from_broker` now short-circuits when `_loaded_from_db` is True (any subsequent restart trusts persisted state). Also accepts a `cash` arg for first-bring-up baseline seeding.
  - New public method `record_balance_snapshot(*, cash, btc_qty, threshold_usd=1.0, threshold_btc=0.0001) -> dict | None` — compares to last-known, returns audit-payload dict on material delta and advances baseline. First call after bring-up just seeds (no false-positive delta).
  - `on_bar_close` gains optional `cash` kwarg. When supplied, BUY sizing uses cash (not account_equity) — the strategy never double-counts the Board's pre-existing BTC into a new buy notional. Back-compat: `cash=None` falls back to account_equity.
- `trading_corp/main.py`:
  - `_run_donchian_bar` extracts `cash` from `snap.cash`, calls `agent.record_balance_snapshot(cash=cash, btc_qty=held_btc)` BEFORE `on_bar_close`. On material delta, writes a `balance_change` audit-event row (kind=`balance_change`, actor=`coinbase_btc_donchian`).
  - `on_bar_close` now passes `cash=cash`.
  - Startup reconcile call site updated to pass `cash=cash`. Comment block rewritten to document the no-op-after-bring-up semantics.
- `trading_corp/web/data.py`:
  - `build_donchian_view` SQL widened to `kind IN ('donchian_evaluated','balance_change')`. Row build branches on `kind`, producing two distinct shapes: existing decision shape, OR `{kind: 'balance_change', ts_short, attribution, state_at_observation, delta_cash, delta_btc, new_cash, new_btc_qty}`.
- `trading_corp/web/templates/partials/donchian_log.html`:
  - Row loop branches on `r.kind`. balance_change rows render full-width with a BAL CHG tag, signed delta amounts (gain-green for +, loss-red for −), and "→ new totals · state=X" trailer. Subtle warn-tinted bg (changes are normal, not alerts). donchian_evaluated rows unchanged.

**Features shipped (load-bearing for future "is X done?" checks):**

- The strategy is now safe against parallel Board trading. Recurring weekly buys + cash deposits land as `balance_change` audit rows; the strategy passively absorbs whatever the broker reports at the next BUY (sizes off cash, sweeps Board's pre-existing BTC into the position via broker-side aggregation) or next SELL (held_btc from snapshot includes all coins, regardless of who put them there).
- Strategy state (CASH↔BTC) is now persisted-state authoritative. `restore_from_broker` is reserved for first-ever bring-up; subsequent restarts preserve the strategy's view. Today's mid-day flip from BTC (set by 15:23 UTC reconcile) → CASH (manual reset 16:42 UTC) was a one-time correction; future deploys should never need to touch the state row directly.
- Decision-log surface now shows two row kinds interleaved chronologically — strategy decisions + Board-attributed balance deltas — giving a single timeline of "what happened on this account" since the strategy's perspective.

**Notable code changes (callouts a future Claude shouldn't miss):**

- BUY sizing changed semantics: `qty = cash / current_close` (when `cash` supplied) instead of `qty = account_equity / current_close`. Tests that don't pass `cash` keep the old behavior (back-compat). If you ever change `on_bar_close`'s signature, mind the back-compat.
- `record_balance_snapshot` advances the baseline EVEN ON sub-threshold deltas. So a slow drift (e.g., $0.50/day fee bleed) won't accumulate over many bars and eventually trip the threshold as a false aggregate event. If that's ever wanted, change the post-detection update path.
- The strategy's `cost_basis` on a BUY is the fill price of the strategy's own buy — NOT a weighted avg with any pre-existing Board BTC. P&L estimates at the next SELL will be measured from the strategy's fill, which is the cleanest accounting given the strategy can't know what the Board paid.

**Verification:**

- Pre-restart PID 170308 → post-restart 171746.
- All 4 files md5 round-trip MATCH after LF-normalization.
- Boot log:
  - `restored state=cash cost_basis=None last_bar=2026-05-09 06:00:00+00:00 last_known_cash=None last_known_btc=None` — picked up the reset CASH state cleanly; balances correctly None pre-first-snapshot.
  - `persisted state present (state=cash, cost_basis=None) — skipping broker reconcile. Board-driven broker deltas will be observed via record_balance_snapshot per bar.` — new short-circuit fired exactly as designed. Broker showed 0.595 BTC + $39K cash; state stays CASH.
  - `Donchian scheduler: sleeping 4743s until next bar close` — math: 16:42:56 + 4743s ≈ 18:01:59 UTC = 14:02 ET. Next bar evaluation arrives on schedule.
- `GET /division/coinbase_spot`: HTTP 200, 62.5KB, 3.5s. Buying Power tile gone, Donchian chart container present, existing decision-log rows (`05-09 02:00 ET`, `05-08 20:00 ET`) preserved, no BAL CHG rows yet (no balance changes have fired in the new code path).
- `GET /partials/donchian-chart/coinbase_spot`: HTTP 200, 10.3KB, 2.1s. 50 candles, current_bar_ts=2026-05-09T06:00:00 UTC, 0 markers.
- 16 existing agent unit tests pass unchanged. New behavior smoke-tested locally: first-bring-up still reconciles; post-bring-up reconcile is no-op; first record_balance_snapshot seeds without delta; material delta returns payload + advances baseline; sub-threshold returns None + advances baseline; BUY sizes off cash when supplied; back-compat cash=None still works.

**Inert / dormant on current traffic:**

- `last_known_balances` agent_state row will appear after the first `record_balance_snapshot` call (next bar at 18:02 UTC = 14:02 ET). Until then, the row doesn't exist.
- BAL CHG tile rows will appear when the Board's recurring weekly buy or a cash deposit lands. Initial seeding at 18:02 UTC will NOT generate a BAL CHG row (first call seeds without firing delta — by design).

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-balance-tracking-20260509-utc-pre
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
# Restore prior state row (state=BTC, cost_basis=80371.17)
sqlite3 \$BASE/data/trading_corp.db \\
  \"UPDATE agent_state SET value_json='{\\\"state\\\":\\\"btc\\\",\\\"cost_basis\\\":80371.17}' \\
   WHERE agent='coinbase_btc_donchian' AND key='state';\"
# Drop the new last_known_balances row if it landed
sqlite3 \$BASE/data/trading_corp.db \\
  \"DELETE FROM agent_state WHERE agent='coinbase_btc_donchian' AND key='last_known_balances';\"
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 15:23 UTC — Coinbase BTC HODL division-detail UI cleanup

**Commits:** `a9c0461` (committed before deploy).
**Triggered by:** BACKLOG P3 — "Coinbase BTC HODL division-detail UI cleanup" (the top-section P3 added 2026-05-09). Bundles four asks Board greenlit at session start: (1) ts_short fix, (2) Manual Order tile removal, (3) Buying Power tile removal, (4) 6h Donchian price chart.
**Backup tag:** `pre-donchian-uicleanup-20260509-utc-pre.tar.gz` at `/home/azureuser/backups/` (49K, 3 modified files; the 2 new files have no pre-state to preserve).

**Files deployed (3 modified, 2 new):**

- `trading_corp/web/data.py` — `build_donchian_view`: `ts_short` now reads `payload.bar_ts` (canonical bar identifier) with `r["ts"]` fallback for legacy rows. New async helper `build_donchian_chart_data(db_url, display_bars=50)` fetches ~50 6h Coinbase OHLCV bars via ccxt public endpoint, computes rolling 20-bar Donchian high / 6-bar Donchian low / 168-bar SMA mirroring `donchian_btc.evaluate` semantics (preceding-window, current bar excluded), pulls BUY/SELL fill markers from `audit_event` (`would_have_placed` paper + `filled` live; both snap to bar-open via payload `bar_ts`), and returns the full chart payload.
- `trading_corp/web/routes.py` — new endpoint `GET /partials/donchian-chart/{slug}` returns the JSON payload from `build_donchian_chart_data`. 404s for any slug other than `coinbase_spot` (chart is single-strategy at this point); returns `{empty: true}` on OHLCV fetch failure.
- `trading_corp/web/templates/division.html` — Buying Power stat card now hidden for `coinbase_spot` (cash == buying_power on spot crypto); grid drops from 4 to 3 cols when `_hide_bp` is true. Manual Order include block deleted (was gated on coinbase_spot only — partial file preserved untouched). New chart partial included between donchian_state and donchian_log; new `donchian_chart.js` script tag included gated on coinbase_spot.
- `trading_corp/web/templates/partials/donchian_chart.html` — new partial. Header with channel-legend chips + 360px chart container (`#donchian-chart`, `data-division="coinbase_spot"`) + empty-state div for OHLCV-fetch-fail case.
- `trading_corp/web/static/js/donchian_chart.js` — new file. Self-running IIFE: Lightweight Charts setup with candlestick series + 2 dashed line series (20-bar high red / 6-bar low green) + solid SMA series (accent blue), fetches `/partials/donchian-chart/coinbase_spot`, sets candle data + 3 line series + markers, draws horizontal price line at last close + circle marker on current bar. 60s refresh interval. ResizeObserver wired so the chart matches container width.

**Features shipped (load-bearing for future "is X done?" checks):**

- Decision-log column `bar (ET)` now renders bar-open time (e.g. `05-09 02:00 ET`), matching the timestamp embedded in `reason`. Verified live: most recent row shows `05-09 02:00 ET` not `05-09 08:02 ET` (which would be the audit-row write time of the 12:02 UTC eval that happened during deploy).
- Division-detail UI is purpose-built for Donchian: stat trio is Equity / Cash / Today's P&L (no BP), no Manual Order tile, full price-chart visibility into the channel state the strategy is reading.
- 6h price chart with all four BACKLOG-asked overlays: candles, entry-channel ceiling (20-bar high), exit-channel floor (6-bar low), SMA(168) trend filter, plus current-bar highlight (circle marker + last-close horizontal price line). Markers infrastructure is wired but the array is empty until the strategy places its first BUY (next breakout above the 20-bar high while above the SMA).

**Notable code changes (callouts a future Claude shouldn't miss):**

- `build_donchian_chart_data` is the canonical place for chart-side rolling window math. If anyone changes the lookback semantics in `donchian_btc.evaluate`, mirror it here too (preceding-window, current bar excluded — current bar's high/low DOES NOT count toward its own donchian_high/low).
- The chart endpoint runs a fresh ccxt OHLCV fetch on every request (no caching). At Coinbase public-rate-limited 1 RPS-ish that's fine for a Board-only dashboard, but if traffic ever grows we should add a short TTL cache (60s would line up with the JS refresh interval).
- `setMarkers` on the candle series is the chosen current-bar highlight mechanism — Lightweight Charts v4 has no native vertical line at a time. The "now" circle + last-close horizontal price line together give the visual anchor.

**Verification:**

- Pre-restart PID 167181 → post-restart 170308.
- All 5 files md5 round-trip MATCH after LF-normalization (Windows working copy carried CRLF; LF-normalized in-place on prod to keep convention).
- `CoinbaseBTCDonchianAgent reloaded: enabled=True auto_execute=False entry=20 exit=6 trend_filter=168 granularity=21600` post-restart — config preserved.
- `CoinbaseBTCDonchianAgent: restored state=cash cost_basis=None last_bar=2026-05-09 06:00:00+00:00` — DB persistence survived; the bar evaluated by the 12:02 UTC scheduler tick is reflected.
- `GET /partials/donchian-chart/coinbase_spot`: HTTP 200, 10.3 KB, 1.99s. Returns 50 candles + 50 high/low/sma points + 0 markers + `current_bar_ts: 1778306400` (= 2026-05-09T06:00:00 UTC). Latest values: close $80,315.98, 20-hi $82,814.23, 6-lo $79,520.44 — close < high so still in CASH.
- `GET /division/coinbase_spot`: HTTP 200, 62 KB, 7.5s. Spot-checks: Buying Power tile NOT in HTML, `id="donchian-chart"` container present, `donchian_chart.js` script include present, first decision-log row's ts_short = `05-09 02:00 ET` (bar-open time, NOT the audit-row write time `05-09 08:02 ET`).

**Inert / dormant on current traffic:**

- The `markers` array on the chart payload is empty — the strategy hasn't placed any orders yet (every bar so far has been SKIP). First BUY will land a green up-arrow `belowBar` at the bar-open time of the entering bar. Will be visible end-to-end on the next breakout.
- `donchian_chart.js` is wrapped in a self-running IIFE that no-ops when `#donchian-chart` isn't in the DOM, so it's harmless on other division pages — but the script tag is gated on coinbase_spot to keep the bytes off the wire where unused.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-donchian-uicleanup-20260509-utc-pre
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
rm -f trading_corp/web/templates/partials/donchian_chart.html \
      trading_corp/web/static/js/donchian_chart.js
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 06:25 UTC — Dashboard timestamps converted to ET

**Commits:** local-only at deploy time (8 files modified; will be batched in the session-wrap commit).
**Triggered by:** Board direction 2026-05-09 — "change all times to eastern timezone." Board reads dashboards from ET; UTC display required mental conversion on every glance.
**Backup:** prod tarball at `/home/azureuser/backups/pre-et-20260509-0625.tar.gz` (54K, 8 files).

**Files deployed (8 modified):**

- `trading_corp/utils/time.py` — added display formatters: `to_et()`, `format_et_short()` ('MM-DD HH:MM ET'), `format_et_hm()` ('HH:MM ET'), `format_et_hms()` ('HH:MM:SS ET'), `format_et_full()` ('YYYY-MM-DD HH:MM ET'). All use the existing `ET = ZoneInfo("America/New_York")` constant; DST handled automatically.
- `trading_corp/web/data.py` — `build_donchian_view`: converted `ts_short` (decision log), `last_bar_short` (state card), `next_bar_short` (state card), `buy_ts_short` / `sell_ts_short` (round-trips tile) to ET via `format_et_short` / `format_et_hm`.
- `trading_corp/web/app.py` — registered new Jinja filters `et_hms`, `et_short`, `et_full` so templates can format datetime objects directly.
- `trading_corp/web/routes.py` — `expires_at.strftime("%Y-%m-%d %H:%M UTC")` → `format_et_full(expires_at)`.
- `trading_corp/web/templates/partials/donchian_log.html` — header `bar (UTC)` → `bar (ET)`; empty-state copy `(00/06/12/18 UTC)` → `(20:00 / 02:00 / 08:00 / 14:00 ET)`; docstring caption updated.
- `trading_corp/web/templates/partials/donchian_state.html` — caption `UTC` removed (ET label is baked into the formatted value via `format_et_short`).
- `trading_corp/web/templates/approvals.html` + `approval_detail.html` — `{{ row.added_at.strftime('%H:%M:%SZ') }}` → `{{ row.added_at | et_hms }}`.

**Storage layer unchanged.** All `audit_event.ts` / `agent_state.updated_ts` / order-status timestamps stay UTC (ISO-8601 with timezone). The conversion is display-layer only — `to_et()` reads any UTC ISO string or naive-assumed-UTC datetime. Round-trips through restart/cache cleanly.

**Verification:**

- Pre-restart PID 164965 → post-restart 167195.
- All 8 files md5-match end-to-end after SCP (LF-normalized).
- `Donchian scheduler online: ... sleeping 20120s until next bar close` post-restart — math: 06:26:39 UTC + 20120s ≈ 12:02:00 UTC = 08:02 ET ✓.
- `CoinbaseBTCDonchianAgent: reconciled to CASH state` — DB persistence survived the restart cleanly.
- Dashboard render checks (curl localhost:8000):
  - Home tile: dial unchanged (no timestamps), `left: 27.3%` needle position preserved.
  - Division detail: column header reads `bar (ET)`, first row's `ts_short` reads `05-09 02:02 ET`. State card "Last decision" + "Next 6h close" both render in ET.

**Pre-existing surface bug surfaced (decision needed before next deploy):**

- The decision-log column header reads `bar (ET)` but the `ts_short` it displays is the **audit-row write time** (bar close + ~2min), not the bar's open time. Was masked under UTC display ("06:02 UTC" is close enough to bar close). Now in ET it reads `05-09 02:02 ET` while the same row's `reason` text references `@ 2026-05-09T00:00:00+00:00` (bar open). Captured as a decision point under the BACKLOG entry "P3 — Coinbase BTC HODL division-detail UI cleanup". Two paths: (a) switch `data.py` to read `payload.bar_ts` instead of `r["ts"]` (~2-line fix; aligns column with reason text); (b) leave the data, change the column header to "evaluated (ET)". Pick before the UI-cleanup deploy lands.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-et-20260509-0625
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 06:02 UTC — Donchian Phase 2 validation gate ✅ CLOSED

**Not a deploy** — validation milestone. The 02:53 UTC Phase 2 wiring deploy left an open validation gate: "first `donchian_evaluated` audit row should land at ~06:02 UTC." It did, exactly on schedule.

**First bar evaluation (2026-05-09T00:00:00 UTC bar; evaluated 06:02:03 UTC = 02:02:03 EDT):**

- **Decision: SKIP.** `close = $80,374.00 ≤ 20-bar high = $82,814.23` — no breakout, agent stays in CASH (correct given startup state).
- **Channel values:** donchian_low $79,456.00 / current_close $80,374.00 / donchian_high $82,814.23 / trend_filter_sma ~$74,xxx (truncated in audit row but >0 = trend filter passes for future entries).
- **Dial position math verified:** `(80374 - 79456) / (82814.23 - 79456) = 918 / 3358.23 ≈ 0.273` → home-tile needle rendered at `left: 27.3%` ✓.
- **Dedup pointer advanced:** `agent_state` row `last_bar_ts: 2026-05-09T00:00:00+00:00` (the bar that just closed).
- **Scheduler armed for next bar:** `sleeping 21596s` post-evaluation → next wake ~12:02 UTC.

**End-to-end Phase 2 deploy is fully validated.** All UI surfaces operating against real production data:
- Home tile placeholder gone, dial proper rendering with channel values + state-aware edge marker.
- Division-detail decision-log tile populated with row 1.
- agent_state persistence + broker-snapshot reconcile working across the restart cycle.

---

## 2026-05-09 04:26 UTC — Donchian decision-log empty-state copy refresh

**Commits:** `9de5902` (committed before deploy).
**Triggered by:** Board flag during the wait-for-validation window — Phase 1 partial said "strategy not yet wired into the orchestrator," cosmetically stale after Phase 2 shipped.
**Mechanism:** template-only, deployed via `tr -d '\r' | ssh ... 'cat > target'` stdin pipe. **No service restart** — Jinja autoreloaded the template on the next request. Useful precedent: pure-template changes on prod don't require the 30-90s Fidelity-login restart cycle.

**Files deployed (1 modified):**

- `trading_corp/web/templates/partials/donchian_log.html` — empty-state copy: "strategy not yet wired into the orchestrator" → "No decisions logged yet — first row lands at the next 6h-bar close (00/06/12/18 UTC)". Top-of-file docstring updated to describe the orchestrator's per-bar write contract. (Note: ET update later in same session further refined the copy to ET-formatted boundaries.)

**Backup:** prod copy at `/home/azureuser/backups/donchian_log.html.pre-copy-fix-20260509-0426.bak` (separate file, not a tarball — the stdin-pipe deploy used a single-file backup).

**Verification:** `curl localhost:8000/division/coinbase_spot | grep "No decisions"` returned the new copy on the next request, confirming Jinja autoreload.

---

## 2026-05-09 03:40 UTC — Coinbase BTC HODL rename + revert intent to aggressive

**Commits:** local-only at deploy time.
**Triggered by:** Board reaction to the 03:30 UTC deploy — wanted the tile back in the CRYPTO group (alongside Coinbase Futures + BitUnix Futures) and the name updated to `Coinbase BTC HODL` (broker-prefixed pattern).
**Backup:** prod copy of `divisions.yaml` saved to `/home/azureuser/backups/divisions.yaml.pre-rename-20260509-0339.bak` (5.1K).

**Files deployed (1 modified):**

- `config/divisions.yaml` — `coinbase_spot`:
  - `name: Bitcoin HODL` → `Coinbase BTC HODL`.
  - `intent: retirement` → `aggressive`. Tile moves back from Retirement → Crypto group on the home page (since `classify_investment_type` falls through to the broker-rule when intent is not retirement; `coinbase` is in `_CRYPTO_BROKERS`).
  - Comments removed (the prior "retirement-aligned" rationale block is no longer accurate).
  - `target_annual_return: 0.40` unchanged — still consistent with `aggressive` intent.

**Verification:**

- Pre-restart PID 164009 → post-restart 164965.
- md5 round-trip MATCH on `divisions.yaml`.
- `Web command center listening on http://0.0.0.0:8000` + `Donchian scheduler online: ... enabled=True` post-restart.
- `GET /` HTTP 200 (~3.1s, 76.5 KB).
- "Coinbase BTC HODL" appears 1×, "Bitcoin HODL" 0× — clean rename.
- Group section order on home page: `Individual` → `Crypto` → `Retirement`. Coinbase BTC HODL is now in `Crypto`.
- Tile badges: `aggressive` (loss/red), `online` (gain/green), `○ CASH` (edge/gray) — Donchian widget code from the 03:30 deploy is unchanged, badge + dial scaffolding remain.

**Inert / dormant on current traffic:**

- Donchian dial proper still pending the first `donchian_evaluated` audit row at ~06:02 UTC (sleep 8731s post-restart, math: 03:40:25 + 8731s ≈ 06:02:00 UTC ✓).

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
cp /home/azureuser/backups/divisions.yaml.pre-rename-20260509-0339.bak \
   /home/azureuser/trading_corp/config/divisions.yaml
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 03:30 UTC — "Bitcoin HODL" rename + retirement reclass + home-tile Donchian widget

**Commits:** local-only at deploy time (4 files modified in working tree, awaiting Board commit decision).
**Triggered by:** Board reaction to the home-page tile post-Phase-2 — flagged that the tile didn't reflect the new strategy. Asked for: (a) CASH/BTC badge on the home tile (originally part of Phase 1 design intent but only built into the division-detail page), (b) v0 "Dial of Donchian" with state-aware geometry, (c) rename `Coinbase Spot` → `Bitcoin HODL`, (d) reclass intent `aggressive` → `retirement`.
**Backup tag:** `pre-donchian-tile-20260509-0328` (tarball at `/home/azureuser/backups/pre-donchian-tile-20260509-0328.tar.gz`, 22K, 4 modified files).

**Files deployed (4 modified, 0 new):**

- `trading_corp/utils/divisions.py` — added `donchian: dict | None = None` field to the `Division` dataclass. Hydrated only for divisions running a Donchian strategy (today: `coinbase_spot`); other divisions stay `None`.
- `trading_corp/web/data.py` — new `_hydrate_donchian_overview(divisions, db_url)` helper invoked from `build_command_center` after `_hydrate_division_metrics`. Reads `agent_state` for the CASH/BTC state + `cost_basis`, then the most recent `audit_event` row of kind `donchian_evaluated` for `current_close` / `donchian_high` / `donchian_low`. Pre-computes a 0..1 dial position (`(close - low) / (high - low)` clamped). Tolerant of missing data — pre-first-eval, state still renders but dial chrome hides.
- `trading_corp/web/templates/home.html` — division-tile additions:
  - **CASH/BTC badge** in the header row alongside the existing intent + status badges. `● BTC` (green) when in BTC, `○ CASH` (gray) when in CASH. Renders only when `d.donchian` is set.
  - **State-aware Donchian dial** below equity: horizontal gradient bar (loss-tinted left → edge-color middle → gain-tinted right), white needle at `dial_position * 100%` width, state-aware "fires here" edge marker (CASH state → green tick at right edge with hover-tooltip "BUY fires when close breaks above the entry-channel high"; BTC state → red tick at left edge with "SELL fires …"). Numeric trio (`low / close / high`) underneath. Shows `awaiting first 6h-bar evaluation` placeholder when state exists but no audit row has landed yet.
- `config/divisions.yaml` — `coinbase_spot`:
  - `name: Coinbase Spot` → `Bitcoin HODL` (per Board pick).
  - `intent: aggressive` → `retirement`. Side effect: `classify_investment_type` checks `intent == "retirement"` BEFORE the crypto-broker rule, so the home tile **moves out of the CRYPTO group into the RETIREMENT group** alongside Robinhood IRA + Fidelity 401(k). Coinbase Futures + BitUnix Futures remain in CRYPTO (their intent is still `aggressive`).
  - `target_annual_return: 0.40` left UNCHANGED (flagged for Board call — 40% reads aggressive for a retirement-classed division).

**Features shipped:**

- **Bitcoin HODL renamed + reclassed.** Home page now shows the division in the Retirement section with a blue `RETIREMENT` badge.
- **CASH/BTC badge live on the home tile.** Currently shows `○ CASH` (the agent's persisted state from the 02:54 UTC startup reconcile).
- **State-aware Donchian dial scaffolded.** Until the first `donchian_evaluated` row lands at ~06:02 UTC, the placeholder reads "awaiting first 6h-bar evaluation". After 06:02 UTC the dial replaces the placeholder automatically (next page load) — no further deploy needed.

**Notable code changes:**

- **`Division.donchian` is the per-tile pivot point.** Today only `coinbase_spot` is populated. If a future second Donchian strategy lands on a different division, the hydration helper needs broadening (currently hardcoded to `coinbase_spot` slug).
- **Dial geometry is single-channel, state-aware labels** (option 2 from the in-session design discussion). Needle position uses the full `[donchian_low, donchian_high]` channel regardless of state; only the "fires here" edge marker swaps sides. Trade-off: visually the same dial whether in CASH or BTC, with one threshold "active" — keeps the at-a-glance signal consistent across state flips.
- **Dial computation lives in Python (`_hydrate_donchian_overview`), not Jinja.** Template stays dumb. Edge cases (degenerate channel where high <= low) handled in Python; template only checks `dial_position is not none`.

**Verification:**

- Pre-restart PID 161969 → post-restart 164009.
- All 4 files md5-match end-to-end after SCP (LF-normalized).
- `Donchian scheduler online: ... sleeping 9116s until next bar close` — math: 03:30:03 UTC + 9116s ≈ 06:02:00 UTC ✓.
- `CoinbaseBTCDonchianAgent: restored state=cash cost_basis=None last_bar=None` — DB persistence survived the restart (state row was written at the 02:54 UTC reconcile + persists to `agent_state`).
- `CoinbaseBTCDonchianAgent: reconciled to CASH state — held=0.00000000 BTC < $1.00 dust threshold` — broker reconcile pass ran clean.
- `GET /` HTTP 200 (~2.8s).
- Home page render check: "Bitcoin HODL" appears once; "Coinbase Spot" appears 0 times. Tile is in the Retirement group section (group order: Individual → Crypto → Retirement; Bitcoin HODL appears after the Crypto section). Badges visible: `retirement` (blue), `online` (green), `○ CASH` (gray). Dial chrome shows the placeholder.
- 25 unit tests pass (risk_gates + coinbase_btc_donchian_agent).

**Inert / dormant on current traffic:**

- **The dial proper (gradient bar + needle + price triplet) is dormant until 06:02 UTC** when the first `donchian_evaluated` audit row lands. The placeholder is the visible state; no JS / refresh needed — next page load post-06:02 will replace it.
- **`target_annual_return: 0.40` is now visually inconsistent with the retirement intent.** No code path consumes this value for risk-gating (retirement-aligned caps come from `intent: retirement` not from this number); it's tile-context-only. Cosmetic, but should be revisited.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-donchian-tile-20260509-0328
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 02:53 UTC — Coinbase BTC Donchian Phase 2 (live wiring + paper-mode deploy)

**Commits:** `a606685` (Phase 2 wiring), preceded by Phase 1 commits `072a484` / `0eb7692` / `fe1cee8` / `f9277e9` — none of the Phase 1 commits had been deployed prior, so this deploy ships Phase 1 + Phase 2 together.
**Triggered by:** Board pickup of the BACKLOG.md "🟡 ACTIVE — Coinbase BTC Donchian (Phase 2 wiring + paper-mode deploy)" brief. coinbase_spot pivots from the Otter+Cypher confluence experiment (no walk-forward edge) to a single 100%-in/out Donchian Channel Breakout strategy (24mo backtest +25.89% alpha vs HODL; 8/10 walk-forward OOS configs beat HODL).
**Backup tag:** `pre-donchian-phase2-20260509-0252` (local git tag on `85d6a80`, the pickup-brief commit). Prod backup as tarball at `/home/azureuser/backups/pre-donchian-phase2-20260509-0252.tar.gz` (48K, 6 modified files).

**Files deployed (11 = 6 modified + 5 net-new):**

- `trading_corp/agents/risk.py` (modified) — section 4 (account max-DD) wrapped in `if not bool(params.get("max_drawdown_disabled", False))` guard. Default-safe; opt-in only.
- `trading_corp/agents/strategies/coinbase_btc_donchian_agent.py` (NEW on prod; locally extended) — agent class from Phase 1 commit fe1cee8 plus a small `last_verdict` attribute exposed for the orchestrator's audit-row write (so SKIP decisions also get the channel highs/lows logged, not just BUY/SELL via order extras).
- `trading_corp/agents/strategies/donchian_btc.py` (NEW on prod) — pure-function decision module from Phase 1 commit 072a484. Both backtest harness and live agent import this same module.
- `trading_corp/main.py` (modified) — construct `CoinbaseBTCDonchianAgent` at startup, reconcile state from `coinbase_spot` snapshot post-`connect_all`, spawn `_scheduled_donchian_loop` alongside the PMCC scheduler. New helpers `_seconds_until_next_6h_boundary` (00/06/12/18 UTC + 2min buffer) + `_fetch_recent_btc_6h_bars` (public ccxt; drops in-progress bar) + `_run_donchian_bar` (one cycle, extracted for ad-hoc trigger). Cancels `donchian_task` cleanly on shutdown.
- `config/risk.yaml` (modified) — `overrides.coinbase_btc_donchian` block: `per_trade_risk_pct=1.0` (full sleeve), `per_strategy_daily_loss_pct=1.0` (effectively-disabled — risk.py reads via `float()` so literal `null` would raise), `max_drawdown_disabled=true`.
- `config/strategies.yaml` (modified) — `lord_otter.enabled` and `market_cypher.enabled` flipped to `false` (paused per 2026-05-08 vision direction; files preserved for future BitUnix Futures wiring); `coinbase_btc_donchian.enabled` flipped to `true`. `auto_execute=false` everywhere.
- `trading_corp/web/data.py` (modified) — `build_donchian_view` from Phase 1 commit f9277e9 (state card data, per-bar decision-log query, realized round-trip pairing).
- `trading_corp/web/templates/division.html` (modified) — donchian tile includes for the `coinbase_spot` division page.
- `trading_corp/web/templates/partials/donchian_state.html` (NEW)
- `trading_corp/web/templates/partials/donchian_log.html` (NEW)
- `trading_corp/web/templates/partials/donchian_trades.html` (NEW)

**Local-only (NOT deployed):**

- `tests/test_risk_gates.py` — new `test_max_drawdown_disabled_flag_skips_cap` locks in default-safe + opt-out semantics for the new flag. Existing `test_max_drawdown_triggers_flatten` already covers the default-on path.

**Features shipped:**

- **Coinbase BTC Donchian goes live in paper mode.** Agent module + locked config (`entry=20, exit=6, trend_filter=168, granularity=21600`) + 6h-bar-close scheduler + risk overrides + UI tiles all on prod. `auto_execute: false` — every BUY/SELL routes through HITL via the web app.
- **`max_drawdown_disabled` per-strategy opt-out for the account-level 15% auto-flatten** — first user is Donchian (24mo backtest max DD 16.49% would have force-flattened the strategy mid-run). Default-safe; no other strategy is opted in.
- **`donchian_evaluated` audit kind starts landing on every 6h-bar boundary**, regardless of decision. The `coinbase_spot` division page's per-bar decision-log tile is its only consumer today.
- **Otter and Cypher disabled on `coinbase_spot`.** Webhook endpoints still accept POSTs (web/webhooks.py is unchanged) but the agents short-circuit on `enabled: false` before ProposedOrder construction. Files preserved per `trading_corp_bitunix_vision.md` — Otter+Cypher ultimately move to BitUnix futures.

**Notable code changes:**

- **`agents/risk.py` section 4 is now opt-out-able per strategy.** This is the only safety-adjacent edit in this deploy; new flag defaults to `False` so existing strategies (PMCC, lord_otter override, manual_coinbase_spot, etc.) are unchanged. Reviewers / future-Claude: the gate's wrapper guards both the `params.get(...)` cap read AND the verdict construction. Don't unwrap one without the other.
- **`coinbase_btc_donchian_agent.py:_last_verdict` is the orchestrator-write hook.** `on_bar_close` short-circuits BEFORE `evaluate_donchian` for `disabled` / `no-bars` / dedup cases — `last_verdict` is only refreshed when the decision module ran, so the orchestrator's `if new_verdict is not None and new_verdict is not prev_verdict` check correctly skips audit writes for short-circuit paths.
- **`_scheduled_donchian_loop` uses ccxt's PUBLIC endpoint for OHLCV** (no auth), same pattern as `paper_trade_replay._default_ccxt_fetcher`. The Coinbase broker's authenticated client (`_exchange.fetch_ohlcv`) was deliberately NOT used — keeps ohlcv read decoupled from broker-auth lifecycle, and the public endpoint has no rate-limit pressure for one call/6h.
- **Bar-boundary math (`_seconds_until_next_6h_boundary`) finds the *strict-greater-than-now* next boundary** — guarantees no double-fire if the loop wakes exactly on a boundary. Combined with the agent's internal `last_bar_ts` dedup, double-fires are double-prevented.
- **On a paper or live "filled" status, the orchestrator calls `agent.mark_filled(side, fill_price=order.limit_price)`.** `limit_price` is the bar-close price the agent used to size the order (set inside `on_bar_close`). For paper-execute fills this is exact; for live fills it's an approximation (real fill price comes from the FillEvent — currently not threaded back to the agent because `_run_order` returns only the status string). Acceptable for Phase 2 paper-mode; revisit if/when `auto_execute` flips.
- **The decision-log tile's empty-state copy says "strategy not yet wired into the orchestrator" — cosmetically stale post-deploy.** Tile was scaffolded in Phase 1 (commit f9277e9) for the pre-wiring state. Will read correct once the first audit row lands at 06:02 UTC. Worth a one-line copy fix on a future surface pass; not blocking.

**Verification:**

- Pre-restart PID 157638 → post-restart 161955 (PID change confirms restart took).
- All 11 files md5-match end-to-end after SCP (LF-normalized).
- journalctl from 02:53:33 → 02:54:14 UTC, full startup sequence:
  - `RiskAgent reloaded config/risk.yaml` — new override block parses cleanly.
  - `LordOtterAgent reloaded config: enabled=False` — Otter disabled.
  - `MarketCypherAgent reloaded config: enabled=False` — Cypher disabled.
  - `CoinbaseBTCDonchianAgent reloaded: enabled=True auto_execute=False entry=20 exit=6 trend_filter=168 granularity=21600` — Donchian config loads with the locked params.
  - `CoinbaseBTCDonchianAgent: no persisted state; defaulting to CASH` — first-boot clean.
  - `CoinbaseBroker(spot) connected (markets_loaded=True)` — Coinbase live (real-read, paper-execute wraps).
  - `CoinbaseBTCDonchianAgent: reconciled to CASH state — held=0.00000000 BTC < $1.00 dust threshold` — broker reconcile snippet ran successfully.
  - `Web command center listening on http://0.0.0.0:8000`.
  - `PMCC scan scheduler online: weekdays 08:30–09:25 ET` — existing scheduler intact.
  - `Donchian scheduler online: wakes at 00/06/12/18 UTC + ~2min (strategy enabled=True, auto_execute=False)`.
  - `Donchian scheduler: sleeping 11266s until next bar close` — math: 11266s ≈ 3h 8m from 02:54 UTC → wakes at 06:02:00 UTC ✓.
- Dashboard smoke (localhost:8000, auth-bypass): `GET /division/coinbase_spot` HTTP 200, 61.7KB. State card renders `○ CASH`, `BTC/USD`, `entry: 20-bar high`, `exit: 6-bar low`, `SMA(168)`, `6h bars`. Per-bar log tile + round-trips tile both render with correct empty states.
- Pre-existing errors only — Fidelity bot-block (paper-fallback to data_exec).

**Inert / dormant on current traffic:**

- ~~**First `donchian_evaluated` audit row will land at ~06:02 UTC 2026-05-09**~~ → **✅ CLOSED 2026-05-09 06:02:03 UTC.** First bar evaluated SKIP (close $80,374 ≤ 20-bar high $82,814.23 — stay in CASH). See the dedicated "06:02 UTC validation gate" entry above for full details.
- **Lord Otter / Market Cypher webhook endpoints (`/webhook/tradingview/lord-otter` and `.../market-cypher`) still accept POSTs.** Agents short-circuit on `enabled: false` before order construction; the audit trail still records `webhook_received` / `alert_ignored`. No Telegram pushes will fire from these strategies.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-donchian-phase2-20260509-0252
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
rm -f trading_corp/agents/strategies/coinbase_btc_donchian_agent.py \
      trading_corp/agents/strategies/donchian_btc.py \
      trading_corp/web/templates/partials/donchian_state.html \
      trading_corp/web/templates/partials/donchian_log.html \
      trading_corp/web/templates/partials/donchian_trades.html
sudo systemctl restart trading-corp
"
```

---

## 2026-05-08 22:04 UTC — Telegram inline-keyboard removed in notification-only mode

**Commits:** local-only (uncommitted at deploy time). Local working tree of `comms/telegram_bot.py` diverges from HEAD by ~133 net lines that match prod's pre-edit content — the file has prod-only changes that were never backported to git (see § Notable code changes below).
**Triggered by:** Direct Board observation during 21:39 UTC `/scan` smoke (see preceding deploy entries today). Slim Telegram pings landed correctly but still rendered Approve/Reject inline-keyboard buttons. CLAUDE.md §HITL surface direction is unambiguous: "Telegram messages do not carry order detail, do not accept Approve/Reject replies, **do not run inline keyboards**." The 2026-05-05 B.4 entry retained the keyboard as a "belt-and-suspenders fallback"; Board called the rule, dropped the fallback.
**Backup tag:** `.pre-telegram-no-keyboard-fix-20260508-2204` (one prod file). Pre-deploy md5 `aa749ac1d7ca9bebef78196688b33ef6`.

**Files deployed (1 modified):**

- `trading_corp/comms/telegram_bot.py` — `_build_approval_message`:
  - Notification-only branch now returns `(text, None)` for the markup (was `(text, kb)`).
  - Slim body trailer line `_Tap Approve / Reject below, or open the dashboard link._` removed (would have been misleading without keys).
  - kb construction moved INSIDE the rich-mode `else` branch — no `InlineKeyboardMarkup` is built when `notification_only=True`.
  - Docstring updated: return type is now `(text, InlineKeyboardMarkup | None)`.
  - Rich-mode (legacy `notification_only=False`) path unchanged byte-for-byte.

**Local-only (NOT deployed):**

- `tests/test_slim_approval_notification.py` — 2 new tests: `test_telegram_notification_only_omits_inline_keyboard` (regression: notification-only must return None for kb, body must NOT mention Approve/Reject) + `test_telegram_rich_mode_keeps_inline_keyboard` (pin: rich mode still produces a keyboard).

**Features shipped (load-bearing for future "is X done?" checks):**

- **Telegram is now truly one-way in production.** Slim notification body + deeplink only. No keyboard, no Approve/Reject buttons, no in-Telegram decision surface. `https://trading.jacksumner.com/approvals/{order_id}` is the sole approval surface.
- **HITL-in-app direction reaches its terminal phase pre-Phase E.** B.4 (2026-05-05) made the slim format the live default and dropped the rich body from Telegram. This deploy drops the keyboard. Phase E (PWA + web push) would let Telegram be dropped entirely; until then Telegram is one-way notification.
- **Test pin in place** so a future refactor doesn't re-introduce the keyboard. The test asserts both `kb is None` AND that the body lacks "Approve"/"Reject" tap-prompt text.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **`comms/telegram_bot.py` had 128 lines of prod-only content not in local HEAD before this deploy.** Pre-deploy md5 diff: local HEAD `3cc9faa2...` vs prod `aa749ac1...` (post-LF-normalization), 538 lines on prod vs 410 in HEAD. The patch was applied directly onto prod's content (download → patch → re-upload) rather than via git, to avoid stomping on prod-only changes. Same pattern as today's earlier `approval_format.py` and `pmcc_robinhood.py` deploys. **Backporting this drift into git is a separate cleanup task.**
- **`_on_callback` handler is unchanged** — it still routes inline-keyboard callbacks for the rich-mode path. So nothing broke for non-notification-only callers (tests, CLI dev). Removing the keyboard handler entirely is a future cleanup, not in scope.
- **Slim body trailer text was removed entirely**, not just edited. Previous text: `_Tap Approve / Reject below, or open the dashboard link._` had two underscores (the italic markers) — keeping it would have left a Markdown-italic span hanging if combined with future format changes. Cleaner to drop.
- **Telegram message length shrinks ~30%** — the trailer was the longest single line in the slim body.

**Verification:**

- Pre-deploy: PID 155725 (post-21:34 restart), Telegram smoke at 21:39 UTC delivered cleanly but with buttons.
- Post-deploy: PID 157624. Port 8000 listening within 60s. `PMCC scan scheduler online` line emitted clean.
- Smoke at 22:08–22:10 UTC: `/scan` triggered. Universe correct (`['ASTS', 'BLSH', 'BULL', 'CIFR', 'HOOD', 'IREN', 'MARA', 'MSTR', 'OPEN', 'RIOT', 'RKLB', 'SMR', 'TSLA']`), 16 orders proposed, 3 ASTS pending_approval_added rows landed. Board confirmed in chat: "yes and yes" — pings landed, no keyboard.
- Zero `notifier 'TelegramChannel._notify_approval' failed` in journalctl since restart. Zero `Can't parse entities`.
- Pre-existing errors only — Fidelity Akamai bot-block.

**Inert / dormant on current traffic:**

- Rich-mode path is dormant on prod (notification_only=true is set on the systemd unit). The rich-mode keyboard code stays in the binary but is never exercised on prod. Future cleanup item.
- 16 approval rows from this scan auto-expire at registry timeout (~3600s) since the prior scan's queued approvals were lost on restart per Board direction (option 3: "restart now, lose pending"). Monday 2026-05-11 12:30 UTC auto-scan re-fires fresh approvals on Monday-open conditions — those are the first non-test exercise of this body.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
  TAG=pre-telegram-no-keyboard-fix-20260508-2204
  BASE=/home/azureuser/trading_corp/trading_corp/comms
  mv \$BASE/telegram_bot.py.\$TAG \$BASE/telegram_bot.py
  sudo systemctl restart trading-corp
"
```

---

## 2026-05-08 21:34 UTC — Telegram parse-error fix (underscore in division slug)

**Commits:** local-only (uncommitted at deploy time). Local HEAD baseline `c057ba80...` was 396 lines; prod was 452 lines (56 lines of prod-only content not backported to git — same pattern as today's other deploys). Patch applied directly to prod's content.
**Triggered by:** First non-zero PMCC scan in 5 days (see preceding 21:08 UTC deploy entry) surfaced a latent bug — every approval ping failed with `Can't parse entities: can't find end of the entity starting at byte offset 26X`. Pre-2026-05-08 the bug never fired because no orders were proposed. Same error was logged once on 2026-05-01 12:33:35 UTC (the only scan that produced output between the earliest entries and today's deploy), but went uninvestigated until volume surfaced it.
**Backup tag:** `.pre-telegram-underscore-fix-20260508-2134`. Pre-deploy md5 `5d1390e92f6297547cb0a7f8bc428557`.

**Files deployed (1 modified):**

- `trading_corp/comms/approval_format.py` — `format_slim_approval_notification`:
  - Symbol slot: `headline_parts.append(sym)` → `headline_parts.append(f"`{sym}`")`. Backtick-wrapped.
  - Division slot: `headline_parts.append(division)` → `headline_parts.append(f"`{division}`")`. Backtick-wrapped.
  - Result: `🎲 *Approval needed*\nROLL SHORT · `MSTR` · `robinhood_pmcc`\n\n[Review on dashboard →](...)` instead of bare `· MSTR · robinhood_pmcc`.

**Local-only (NOT deployed):**

- `tests/test_slim_approval_notification.py` — `test_slim_format_safe_for_legacy_markdown_parse_mode` rewritten as a real regression test. Old assertion was `assert "_" not in headline or "robinhood_pmcc" in headline` (lenient — passed for the buggy state). New assertions: (1) `` `robinhood_pmcc` `` substring required (asserts backtick-wrap), (2) any `_` outside backtick spans must NOT appear in the slim body, with regex stripping of `` `...` `` before counting (since `_` inside a backtick code span is parsed literally by Telegram).

**Features shipped (load-bearing for future "is X done?" checks):**

- **Slim Telegram approval pings now deliver successfully.** Pre-fix: `Can't parse entities` on every ping. Post-fix: zero parse errors observed across two `/scan` smokes (21:39 UTC and 22:08 UTC).
- **The slim format is now Telegram-Markdown-self-contained.** Backtick-wrapping `sym` + `division` makes the headline robust to any underscore-bearing identifier (tickers like `BRK_B` would also work). Combined with the no-keyboard change in the 22:04 UTC entry, the slim body is deeplink-only, parse-error-proof.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **The bug:** `robinhood_pmcc` has one underscore. Pre-fix the slim body had odd total `_` count (1 from division + 2 from the trailer line `_Tap Approve.../link._` = 3). Telegram legacy Markdown reads odd `_` as unmatched italic and rejects the message. Backtick-wrapping `division` makes its `_` literal (inside a code span), bringing the unwrapped `_` count to 0 from the slim body; combined with the 22:04 UTC trailer removal, the count is now 0 outright.
- **The rich format `format_approval_message` ALSO has the same bug** if it were ever sent on prod — the rich header puts `· {division}` bare. It's dead code on prod today (TELEGRAM_NOTIFICATION_ONLY=true, see 2026-05-05 entry) but worth flagging if someone re-enables rich mode.
- **`approval_format.py` had 56 lines of prod-only content not in local HEAD before this deploy.** Same drift pattern as `pmcc_robinhood.py` (681 lines) and `telegram_bot.py` (128 lines). Patch applied directly onto prod's content rather than via git.

**Latent bugs caught + fixed:**

- **Slim Telegram parse-error on every approval** — fixed in this deploy. Latent since the slim formatter was added (Phase A, 2026-05-03 02:09 UTC) but never exercised under load until today's PMCC universe fix unblocked the scan.

**Verification:**

- Pre-deploy: PID 153933 (post-21:08 restart), 20 ASTS approvals queued in registry from the 21:21 UTC smoke, ALL of them got `notifier failed: Can't parse entities` lines in journalctl.
- Post-deploy: PID 155725. Port 8000 listening within 60s. Smoke at 21:39 UTC: `PMCCAgent scan complete: 20 order(s) proposed`, 2 ASTS `pending_approval_added` rows, **zero `notifier failed` lines, zero `parse entities` errors**.
- Telegram delivery confirmed by Board in chat — pings landed (with keyboard at this stage; keyboard fix landed in the 22:04 UTC deploy that followed).

**Inert / dormant on current traffic:**

- Rich format still has the latent bug but is dead on prod. Fix-or-forget when removing the rich code path; not in scope.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
  TAG=pre-telegram-underscore-fix-20260508-2134
  BASE=/home/azureuser/trading_corp/trading_corp/comms
  mv \$BASE/approval_format.py.\$TAG \$BASE/approval_format.py
  sudo systemctl restart trading-corp
"
```

---

## 2026-05-08 21:08 UTC — PMCC scan universe fix + LLM rate-limit cap (first non-zero scan in 5 days)

**Commits:** local-only (uncommitted at deploy time). Local working tree pre-edit had 681 lines of uncommitted content beyond HEAD that EXACTLY matched prod content (Phase A PMCC prompt-text refinements from 2026-05-03 02:09 UTC, never backported to git). Patch applied to working-tree-equals-prod content; deploy is byte-stable.
**Triggered by:** Board observation that every weekday PMCC scan since 2026-05-04 reported "PMCC scan complete: no actions needed this cycle." despite 13 PMCC legs detected. Investigation found two compounding bugs: (1) crypto-position regression after the 2026-05-01 Robinhood crypto-snapshot deploy, (2) Anthropic API rate-limit on parallel LLM analysis.
**Backup tag:** `.pre-pmcc-universe-fix-20260508-2108`. Pre-deploy md5 `5f2ac5617ed39e249e55afb15a762fdd`.

**Files deployed (1 modified):**

- `trading_corp/agents/divisions/pmcc_robinhood.py` — 5 surgical edits:
  - `get_universe()`: skip positions with `/` in symbol (HODL crypto: `ETH/USD`, `BTC/USD`). These are visible in dashboard equity but are not tradeable as PMCC underlyings.
  - `scan()` `stock_qty` lookup: same `/` filter on the position dict comprehension.
  - `scan()`: `detect_existing_legs()` moved BEFORE the early-return; early-return relaxed to require BOTH empty `universe` AND empty `legs_by_symbol` (was just empty universe).
  - `scan()`: order-construction loop iterates `set(universe) | set(legs_by_symbol.keys())` — defensive layer so a future stock holding alongside legs doesn't again drop the legs.
  - `scan()` + `analyze_portfolio()`: `asyncio.Semaphore(N)` bounds parallel LLM calls. N from `strategies.yaml` `pmcc.llm_concurrency` (default 3). Caps in-flight burst under Anthropic's 30k input-tokens/min org cap on claude-sonnet-4-6.

**Local-only (NOT deployed):**

- `tests/test_pmcc_logic.py` — new regression test `test_universe_skips_hodl_crypto_positions` reproducing the exact prod scenario (ETH/USD stock-position + ASTS/MARA legs → universe is `{ASTS, MARA}`, NOT `{ETH/USD}`).

**Features shipped (load-bearing for future "is X done?" checks):**

- **PMCC scan produces non-zero orders again.** Two `/scan` smokes today: 20 orders @ 21:21 UTC, 16 orders @ 22:10 UTC. Pre-fix: 0 every weekday since 2026-05-04. Same scan path is wired into the daily 12:30-13:25 UTC scheduler — Monday 2026-05-11 will be the first non-test scheduled exercise.
- **HODL crypto is now isolated from PMCC scan logic.** `ETH/USD` (and any future `/USD`-pattern crypto position from Robinhood crypto branch) is treated as portfolio-value only. Visible in dashboard equity, invisible to PMCC universe.
- **LLM rate-limit failure mode is bounded.** Pre-fix: 5 of 13 legs got 429 errors and lost their LLM verdict (from journalctl 2026-05-08 12:32 UTC). Post-fix: zero 429s observed across both `/scan` smokes.
- **Defensive structural fix on the leg-iteration path.** Even after the crypto filter, a future stock holding (e.g. AAPL in Individual alongside the 13 PMCC legs) would have produced the same "leg verdicts dropped" symptom. The union-iteration in the scan loop guards against this — leg management runs unconditionally for every detected leg.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **The bug, in one line:** `get_universe()` detects "stock positions" by absence of options-flagging chars (`" "`, `"#"`). The 2026-05-01 Robinhood crypto-snapshot deploy added `BTC/USD`-style symbols to `RobinhoodBroker.snapshot()` for the Individual account. Those passed the options-filter (no space, no `#`), so `ETH/USD` was treated as a "stock position." Because `symbols` was non-empty, the existing leg-underlyings fallback (line 1786 pre-edit) never ran. The order-construction loop iterated over `['ETH/USD']` only — every detected PMCC leg's LLM verdict was computed and discarded.
- **Regression timeline confirmed via journalctl:** 2026-05-01 12:32:41 UTC last clean scan (universe: long-call underlyings, 20 orders proposed). 2026-05-04 12:37:43 UTC first broken scan (universe: `['ETH/USD']`, 0 orders). Pattern held every weekday until today's fix.
- **`pmcc_robinhood.py` had 681 lines of uncommitted local content matching prod** (Phase A PMCC prompt-text refinements from 2026-05-03 02:09 — COOLDOWN guard prose, NYSE-calendar-aware `_terminal_dte_time_release` description, LEAP Hard Rule promotion NOTE blocks). Working tree was effectively in sync with prod, just not git-committed. md5 mismatched on pre-deploy check due to CRLF (Windows local) vs LF (prod Linux) line endings — once normalized, working tree equaled prod. Deploy file was the LF-normalized working tree with my 5 edits.
- **Rate-limit fix is configurable.** `pmcc.llm_concurrency` in `strategies.yaml` defaults to 3; tunable without a code deploy if Anthropic's org cap changes. Hot-reload happens on the next `_reload()` call (every scan).
- **The 16-vs-20 order count delta between smokes is normal.** LLM verdicts can shift (different "elevated" vs "routine" classifications, different `target_strike` choices) when called minutes apart; deterministic Python guards (terminal_dte, halfway-roll cooldown, LEAP hard rule) provide the floor of expected behavior across re-runs.
- **One PMCC leg is risk-rejected per ASTS roll_leap pair** — the new-LEAP buy ($30.85/sh × 100 = $3085/contract, exceeds $1500 per-trade cap). Expected behavior, not a bug. Visible in audit as `risk_rejected` events. Per-trade cap can be raised in `risk.yaml` if Board wants this leg to flow.

**Latent bugs caught + fixed:**

- **PMCC scan universe regression** (described above). Latent since 2026-05-01; first detected today. Fixed.
- **Telegram parse-error on every slim ping** — surfaced by this deploy (because no orders had been firing pre-fix). Fixed in the immediately-following 21:34 UTC deploy.
- **Telegram inline-keyboard contradicts CLAUDE.md HITL direction** — also surfaced by this deploy. Fixed in the 22:04 UTC deploy.

**Verification:**

- Pre-deploy: PID 136040 (running since 2026-05-05 01:34 UTC B.4 deploy), every weekday scan reporting 0 orders.
- Post-deploy: PID 153919 (153933 xvfb child). Port 8000 listening within 60s. `PMCC scan scheduler online: weekdays 08:30–09:25 ET` line emitted clean.
- Smoke at 21:19 UTC: `PMCCAgent universe from long call underlyings: ['ASTS', 'BLSH', 'BULL', 'CIFR', 'HOOD', 'IREN', 'MARA', 'MSTR', 'OPEN', 'RIOT', 'RKLB', 'SMR', 'TSLA']` (NOT `['ETH/USD']`). All 13 LLM verdicts landed (no 429s). `PMCCAgent scan complete: 20 order(s) proposed`. ASTS roll_leap pair-coalesced via `pmcc_pair_id`.
- Pre-existing errors only — Fidelity Akamai bot-block + yfinance BTC/USD earnings noise. Zero new errors.

**Inert / dormant on current traffic:**

- Daily PMCC scheduler (12:30-13:25 UTC weekday window) is the natural exercise. Today's smokes were Telegram `/scan`-triggered (same code path as the scheduler). Monday 2026-05-11 is the first auto-scheduled exercise post-fix.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
  TAG=pre-pmcc-universe-fix-20260508-2108
  BASE=/home/azureuser/trading_corp/trading_corp/agents/divisions
  mv \$BASE/pmcc_robinhood.py.\$TAG \$BASE/pmcc_robinhood.py
  sudo systemctl restart trading-corp
"
```

---

## 2026-05-05 01:34 UTC — Phase B.4: `TELEGRAM_NOTIFICATION_ONLY=true` flag flip (slim Telegram body live)

**Commits:** n/a (configuration-only change — no code shipped, only systemd drop-in added)
**Triggered by:** Mon 2026-05-04 was the planned validation day for the B.4 flip (see deploy_log entry 2026-05-03 05:07 UTC and the prior B.1/B.2/B.3 entries). The original gate was "Mon's first PMCC scan validates the web flow with a real Board-routed approval"; the scan ran clean at 12:38:04 UTC but emitted zero approvals (`scheduled_scan_done` payload: "PMCC scan complete: no actions needed this cycle."), so no live web-flow exercise occurred. User chose to flip anyway on the fallback rationale: paper-mode + Telegram inline-keyboard fallback bind real-money risk to zero, the web flow is verified by tests + B.5 manual smoke, and the value of waiting drops with each empty scan.
**Backup tag:** `n/a` for the override file (newly created — no pre-version exists). Pre-flip Environment snapshot for rollback context: `Environment=KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/ PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 PATH=/home/azureuser/trading_corp/venv/bin:...`.

**Files deployed (1 new on prod VM, no repo files):**

- `/etc/systemd/system/trading-corp.service.d/override.conf` (NEW on VM, not tracked in repo per CLAUDE.md §6 "VM-side configuration is no-edit from this repo"):
  ```
  [Service]
  Environment=TELEGRAM_NOTIFICATION_ONLY=true
  ```

**Features shipped (load-bearing for future "is X done?" checks):**

- **`TELEGRAM_NOTIFICATION_ONLY=true` is now the live prod default.** The slim Telegram approval body (`🎲 Approval needed · <action> · <symbol> · <division>` + deeplink to `https://trading.jacksumner.com/approvals/{order_id}`) replaces the rich `format_approval_message` body that had been emitting since Phase A's dormant Phase. First real or synthetic approval after this deploy will arrive in slim format.
- **HITL-in-app direction is now FULLY LIVE end-to-end.** Phase A (slim formatter + dormant flag, 2026-05-03 02:09) + Phase B.1 (registry + routes, 03:50) + Phase B.2/B.3 (rich rendering + Modify form + paired coalescing, 04:20) + Phase B.5 (quick-modify presets + new_limit_price, 05:07) + Phase B.4 (this entry, flag flip) means the Board now sees: short Telegram ping → tap deeplink → web-app approval card with structured trade legs / position context / risk / paired coalescing / quick-modify presets → POST resolves the LangGraph interrupt. Telegram inline keyboard remains as a belt-and-suspenders fallback (resolves the same `PendingApprovalRegistry`).
- **Phase E (web push) remains the only deferred phase.** PWA + service worker + push subscription would let Telegram be dropped entirely; not yet scoped.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **No code change in this deploy.** This is purely a systemd Environment add. The producing code path was already in place and dormant (`_notification_only` switch on `TelegramChannel`, plumbed through `main.py:539` from `os.getenv("TELEGRAM_NOTIFICATION_ONLY", "false").lower() == "true"`).
- **VM-side configuration was edited.** Per CLAUDE.md §6, VM systemd unit configuration is "no-edit from this repo" — it lives only on the VM. The override.conf is at `/etc/systemd/system/trading-corp.service.d/override.conf` and is not tracked in the repo. Future maintenance: if more env vars are added, append to this same drop-in (or use a separate `.conf` file in the same drop-in dir; systemd merges them).
- **`DASHBOARD_BASE_URL` was NOT set.** The `comms/approval_format.py` module has `DEFAULT_DASHBOARD_BASE_URL = "https://trading.jacksumner.com"` as the default, and the slim formatter falls through to it when `os.getenv("DASHBOARD_BASE_URL")` is unset. Confirmed via `main.py:541`.
- **Mon's PMCC scan emitted zero approvals.** The 13 detected legs (RKLB, OPEN, MSTR, MARA, CIFR, TSLA, BULL, BLSH, HOOD, RIOT, ASTS, SMR, IREN) had no fires-this-cycle on roll/open conditions. MSTR had a `no liquid weekly contracts` warning (normal — gated out at the liquidity filter). One unrelated `risk_rejected` event at 14:03:24 UTC was a manual MSTR `via=web_button` click, killed at the per-trade cap because $971.65 < $1865 = 1 contract @ $18.65. Did NOT exercise the `/approvals` web flow.

**Verification:**
- Pre-flip: PID 130241, no drop-in dir.
- Post-flip: drop-in `[Service]\nEnvironment=TELEGRAM_NOTIFICATION_ONLY=true` written, `daemon-reload` clean, restart clean.
- New PID 136026 (parent) → 136040 (xvfb-run python child, the actual server).
- `systemctl show -p Environment trading-corp` includes `TELEGRAM_NOTIFICATION_ONLY=true`.
- `/proc/136040/environ` confirms the live python process inherited `TELEGRAM_NOTIFICATION_ONLY=true` (i.e. it's not just on the unit, it's on the running process).
- `ss -tlnp` shows port 8000 listening on PID 136040.
- Dashboard external probe: `GET /` → HTTP 302 in 0.19s (Authelia redirect, normal).
- Post-restart journalctl: only pre-existing errors observed — Fidelity bot-block (Azure VM IP / Akamai layer, documented sharp edge) + yfinance BTC/USD earnings noise. ZERO new errors.

**Inert / dormant on current traffic:**
- Slim format hasn't sent yet at deploy time — first real approval after deploy will be the first observable check. PMCC scan ran clean today; no scout is scheduled until tomorrow's market-open scan. A test-only synthetic alert via the `/webhook/tradingview/lord-otter` endpoint with paper-mode + auto_execute=false would land in `would_have_placed`, not in the approvals registry, so it's NOT a way to smoke the slim format.
- The old rich `format_approval_message` code path remains in the binary (now dead on this prod process). Removing it is a future cleanup, not in scope.

**Rollback recipe:**
```bash
# Run from any host with az CLI logged into Azure subscription 6f20f2e1-28ec-4857-857c-457c7f5212ca
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript \
  --scripts "sudo rm /etc/systemd/system/trading-corp.service.d/override.conf && \
             sudo rmdir /etc/systemd/system/trading-corp.service.d/ && \
             sudo systemctl daemon-reload && \
             sudo systemctl restart trading-corp && \
             systemctl show -p Environment trading-corp"
# Expected: TELEGRAM_NOTIFICATION_ONLY no longer appears; rich Telegram body resumes.
```

---

## 2026-05-03 05:07 UTC — Phase B.5: quick-modify presets + new_limit_price + graph routing fix

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Continuation of the same Sunday HITL session that shipped B.1 (03:50), B.2+B.3 (04:20). User chose to ship B.5 today and plan B.4 (slim-flag flip) for tomorrow once Mon's PMCC scan validates the web flow live. B.5 = the "quick-modify ±½ size + limit ±5%" preset buttons from `planning/hitl_in_app_design.md` §14, plus the underlying `new_limit_price` plumbing through BoardDecision / graph / POST handler. Fixed a latent graph-routing bug in modify_then_risk_node along the way (unconditional edge back to risk overwrote final_status — manifested as "modify with no fields silently re-pauses at approval forever"; now routes to end_rejected when modify_then_risk_node bails).
**Backup tag:** `.pre-b5-quick-modify-20260503-0505` (on the 5 mutated files)

**Files deployed (5 modified):**

- `trading_corp/graph/interrupts.py`:
  - `BoardDecision` gains `new_limit_price: float | None = None` field. Documented as "only used when decision='modify'", parallel to existing `new_qty`.
  - `request_board_approval` decodes `new_limit_price` from the resume payload alongside `new_qty`.
- `trading_corp/graph/ceo_graph.py`:
  - `approval_node` stashes `new_limit_price` on `state["board_decision"]` (was missing — modify_then_risk_node couldn't see it without this).
  - `modify_then_risk_node` accepts BOTH new_qty and new_limit_price (either alone or both together). Validates each: > 0 when supplied; rejects with `final_status='board_rejected'` when neither field is supplied OR when a supplied field is non-positive. Builds a `board-modified (qty=X, limit=$Y)` rationale annotation showing exactly what changed.
  - **NEW conditional edge** `modify_then_risk_route`: if `final_status == 'board_rejected'` → end_rejected, else → risk. Replaces the unconditional `g.add_edge("modify_then_risk", "risk")` that was silently overwriting final_status by re-running risk on the unmodified order. Without this, the empty-modify path became an infinite re-pause loop. Pinned by new test `test_modify_with_no_fields_rejects`.
- `trading_corp/main.py`:
  - `_run_order` resume payload now includes `new_limit_price` so it survives the LangGraph round-trip.
- `trading_corp/web/routes.py`:
  - `POST /approvals/{order_id}/decide` accepts `new_limit_price` in form OR JSON body (parallel to existing `new_qty`). Modify-validation: at least ONE of new_qty / new_limit_price required (400 with "modify requires at least one of new_qty / new_limit_price" if neither). Each supplied field validated independently (numeric, > 0). Response message includes `qty=X, limit=$Y` for the affected fields.
  - Renamed pre-existing test assertion error message from "new_qty is required for decision=modify" to the both-fields message above; old test renamed to `test_decide_modify_missing_both_fields_400`.
- `trading_corp/web/templates/approval_detail.html`:
  - **Quick-modify preset row** added inside the modify-form panel: 4 buttons in a 2×2 (mobile) / 1×4 (desktop) grid — `½× size`, `2× size`, `limit −5%`, `limit +5%`. Each button shows the COMPUTED preset value below the label (e.g. "½× size → 1" for a qty=2 order; "limit −5% → $5.22" for a $5.50 order). Buttons are `type="button"` with `data-preset-kind` + `data-preset-value` attributes; JS handler intercepts click, calls a shared `_submitDecision('modify', {field: value})` helper, and the form posts in one tap.
  - **`new_limit_price` input field** added below the existing custom-qty input — only renders when the order has a non-null limit/mark price (skipped for market orders without a limit price).
  - Limit-direction preset buttons disabled (with explanatory tooltip) when the order has no `mark` price (e.g. market orders).
  - Submit handler refactored: pulled the URL-encode + fetch + result-swap logic out of the form-submit listener into a shared `_submitDecision(decision, extras)` helper used by both preset clicks and the regular form submit. JS form-submit handler now also pulls `new_limit_price` from the form when present (it didn't before — the input field is new).

**Local-only (NOT deployed):**
- `tests/test_approvals_routes.py` extended with 8 new tests: 6 modify-with-new_limit_price cases (only-limit, both-fields-together, neither-field-400 [renamed], zero-limit-400, non-numeric-400, JSON body) + 2 template smoke tests (presets render with computed values, limit-direction buttons disabled when no price).
- `tests/test_graph_hitl.py` extended with 2 new tests: `test_modify_with_new_limit_price_applies_to_fill` (full graph round-trip — limit-only modify re-runs risk and fills at the new price) + `test_modify_with_no_fields_rejects` (regression test for the routing fix above).

**Features shipped (load-bearing for future "is X done?" checks):**
- **One-tap quick-modify is live.** Board sees presets with the actual numeric outcome on each button ("½× size → 1") so there's no math required; tap fires the modify, no second confirmation. Mobile UX optimized.
- **`new_limit_price` is now a first-class modify field.** End-to-end: web POST → BoardDecision → graph state → modify_then_risk_node → re-evaluate risk → re-pause approval at the new price → execute at the new price on approve. Test pin: `test_modify_with_new_limit_price_applies_to_fill` verifies the fill happens at the modified price, not the original.
- **Graph correctness fix on the modify path.** Empty modify (no qty + no limit) now routes cleanly to `end_rejected` instead of silently re-pausing at approval forever. Pre-B.5 this latent bug existed but no test exercised it — discovered while writing B.5 tests.
- **Modify form on the detail page now renders both inputs** — qty AND limit_price (when applicable) — so the Board can hand-edit either independently of the presets.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Limit-direction presets are ±5% from the order's `mark` (or `limit_price`), NOT from the current market price.** Rationale: the user's anchor is what THEY proposed, not what the market is doing right now. If the order proposes $5.50 limit and the market has moved to $5.80, "limit −5%" goes to $5.22 (5% below the proposal), not $5.51 (5% below market). This makes the math predictable from the displayed price; a future "limit toward bid/ask" preset could be added if needed.
- **Quick-modify presets bypass the custom input field entirely.** Each preset POSTs only the relevant field (`new_qty` for size presets, `new_limit_price` for limit presets) so user edits to the custom inputs are NOT inadvertently submitted with a preset. The `reason` field IS auto-populated as `"preset:qty-half"` / `"preset:limit-down"` / etc. so the audit log distinguishes preset use from custom-input use.
- **Paired mode disables the entire Modify path** (B.2 decision preserved). Paired-modify is a future phase — the design needs to think through per-leg vs both-legs semantics. Telegram `/modify <id> <qty>` still works as the per-leg fallback.
- **Graph routing fix is the load-bearing change for correctness.** The new `modify_then_risk_route` conditional edge is the right architectural fix; without it, ANY modify_then_risk_node bail (empty modify, invalid qty, invalid limit) would silently re-pause at approval. The fix is symmetric — handles current AND future bail conditions.
- **`approval_node` was missing `new_limit_price` in the board_decision dict.** Caught during test debugging — the resume payload had it, BoardDecision had it, but approval_node forgot to copy it from BoardDecision to the graph state. Without that, modify_then_risk_node always saw `new_limit_price=None` even when the user supplied one. One-line fix.
- **`_decide` validation is per-field independent.** Both new_qty and new_limit_price get validated separately, so a request with `new_qty=2.5, new_limit_price=invalid` rejects on the limit field's validation rather than silently dropping it. Errors are specific ("new_limit_price must be > 0" vs "new_qty must be > 0").

**Latent bugs caught + fixed:**
- **modify_then_risk routing bug** (described above). Pre-existing since the original Phase 1 graph wiring; first exposed by B.5 tests. Fixed with the new conditional edge.
- **approval_node missed copying new_limit_price** to graph state. Introduced in this same B.5 deploy but caught in the integration tests before shipping.

**Verification:**
- Pre-deploy: 495 unit tests pass on local (vs 485 pre-B.5 baseline; +10 = 8 new in test_approvals_routes + 2 new in test_graph_hitl). 5 pre-existing P2 failures unchanged (BACKLOG line 1247).
- md5 5/5 files MATCH between local and prod post-scp.
- Backup tag `.pre-b5-quick-modify-20260503-0505` placed on the 5 mutated prod files pre-deploy.
- PID 119776 → 121271 (restart at 05:07:13 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~33s after restart (Robinhood + Fidelity logins block bind, normal).
- `GET /` 200 in 3.29s; `GET /approvals` 200 in 2.77s.
- `POST /approvals/x/decide` with `decision=modify` (no fields) → 400 with the new B.5 message `"modify requires at least one of new_qty / new_limit_price"` — confirms the validation branch landed.
- `POST /approvals/x/decide` with `decision=modify&new_limit_price=5.50` (no qty) on a non-pending order_id → 409 (not 400) — confirms `new_limit_price` is parsed as a valid modify field, just no entry to resolve. The 409-vs-404 for unknown order_id is pre-B.5 behavior; refining that is a B-x polish item.
- journalctl post-restart: 6 ERROR lines, all pre-existing (5 Fidelity Azure-IP block + 1 yfinance BTC/USD earnings noise). ZERO new errors related to BoardDecision, graph routing, modify form, or POST handler.

**Inert / dormant on current traffic:**
- **Quick-modify presets have no users until the Board takes a Modify action on a real pending approval.** Same condition as B.2 modify — first scout-emitted approval with a Board choice to Modify rather than Approve/Reject. Mon ~13:30 UTC PMCC scan is the first natural exercise.
- **`new_limit_price` graph wiring** is dormant on the approve/reject paths (only fires when decision=modify). Approve / Reject still take the same byte-identical paths as B.4-pre.
- **Phase A flag still NOT flipped.** `TELEGRAM_NOTIFICATION_ONLY` stays unset. B.4 plan: validate web on Mon's first live PMCC approval, then flip same-day. Soak window collapsed from "1 week" to "1 live exercise" since paper mode + inline keyboard fallback bound real-money risk to zero.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-b5-quick-modify-20260503-0505; BASE=/home/azureuser/trading_corp;
for f in trading_corp/graph/interrupts.py trading_corp/graph/ceo_graph.py trading_corp/main.py trading_corp/web/routes.py trading_corp/web/templates/approval_detail.html; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
sudo systemctl restart trading-corp
"
```

(Reverts B.5 only — leaves B.1+B.2+B.3 intact.)

---

## 2026-05-03 04:20 UTC — Phase B.2 + B.3: rich `/approvals` rendering + Modify form + paired-roll coalescing

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Continuation of the same Sunday session that shipped B.1 at 03:50 UTC. User chose two-cut strategy ("B.1 alone first, then B.2+B.3 bundled") so the load-bearing registry seam validated discrete on prod before stacking polish + safety-critical pair coalescing on top. B.2 = Tailwind structured rendering of trade legs / position context / risk / warnings (replaces B.1's raw-JSON dump) + inline Modify form. B.3 = the safety-critical pair-coalescing fix: PMCC roll close+open siblings now run in parallel via asyncio.gather, land in registry simultaneously, render as ONE card with combined Net Debit/Credit, ONE Approve button resolves both atomically. Eliminates the original "approve close, reject open → naked short" failure mode that was the entire reason the BACKLOG P0 existed.
**Backup tag:** `.pre-b23-hitl-rich-pairs-20260503-0417` (on the 5 mutated files; the new position_context.py file has no backup target — `rm` is the rollback)

**Files deployed (5 modified, 1 new):**

*New:*
- `trading_corp/comms/position_context.py` — structured-dict builder consumed by the web detail template. `build_approval_view(detail)` returns `{headline, trade.legs[], context, risk, warnings, pmcc_pair_id, raw_extra}`. Each leg carries `{side, qty, asset_class, symbol, action_label, option, mark, bid, ask, gross_dollars, side_sign, rationale}` plus crypto-/stock-specific fields where applicable. `coalesce_paired_view([close_v, open_v])` merges two single-leg views into a paired one with combined `trade.legs[]` + summed `net_dollars`; sorts close-leg-first; uses the close leg as the headline anchor; surfaces `is_paired=True` and `paired_order_ids[]`. Defensive against missing/malformed `extra_json` (decodes JSON; falls back to `extra` dict; safe `_safe_float` wrapper). The Telegram formatter (`comms/approval_format.py`) is UNCHANGED — its existing string-output path remains the source of truth for Telegram message bodies; the structured dict is web-only in B.2.

*Modified:*
- `trading_corp/comms/pending_registry.py`:
  - `PendingEntry` gains `pmcc_pair_id: str | None = None` field (extracted from `req.detail["order"]["extra_json"]` at `wait()` registration time via the new module-level `_extract_pair_id` helper).
  - `pending_approval_added` audit row payload now includes `pmcc_pair_id` (renders in audit trail; lets the dashboard recover paired state from audit if a restart wipes the registry).
  - `resolve(...)` gains `also_resolve_paired: bool = False`. When True AND the entry has a paired sibling currently pending, the sibling's Future is resolved with the SAME decision in the same call. Two `board_decision_received` audit rows emit (one per leg), each tagged `paired_with=<sibling_order_id>` for traceability. Graceful no-op when the paired flag is set but no sibling is in the registry.
  - New `find_sibling(order_id) -> ApprovalRequest | None`: looks up the OTHER pending entry sharing the order's `pmcc_pair_id`. Used by the detail-page handler to render coalesced view at request time.
- `trading_corp/main.py`:
  - New module-level `_group_orders_by_pair_id(orders) -> list[list[ProposedOrder]]` helper. Groups orders so paired siblings (sharing `extra.pmcc_pair_id`) end up in the same sub-list; solo orders become singleton lists. Group ordering preserves the position of the first-seen leg of each pair.
  - PMCC scan loop refactored: instead of `for order in orders: await _run_order(...)`, the loop iterates `groups = _group_orders_by_pair_id(orders)`. Solo groups await sequentially (preserves prior blast-radius bound). Multi-leg groups dispatch via `asyncio.gather(*[_run_order(...) for o in group])` so both ApprovalRequests land in the registry at the same instant — that's what makes the web detail page's coalesced view actually appear (sibling lookup at render time succeeds because both legs are simultaneously pending).
  - Fidelity scan path UNCHANGED — Fidelity is read-only-on-Azure-VM and the autonomous-execution path is deferred (BACKLOG P3 #1341); applying the same refactor would expand blast radius without delivering value today.
- `trading_corp/web/routes.py`:
  - Two new module-level helpers: `_group_index_entries(entries)` collapses paired entries into ONE `kind='paired'` row per pair_id (close leg as anchor, combined headline "ROLL · {SYM} · close + open"); `_summary_action_hint` / `_summary_symbol` parse the rich Telegram-Markdown summary's first line to pick the close-leg anchor + extract symbol for the combined headline.
  - `GET /approvals` reworked: passes `rows = _group_index_entries(entries)` + `total_legs` to the template. Each row carries `{kind, entries, is_paired, primary_order_id, division, summary, added_at, pair_id}`.
  - `GET /approvals/{order_id}` reworked: builds primary view via `build_approval_view`, looks up sibling via `registry.find_sibling`, calls `coalesce_paired_view([primary, sibling])` when sibling exists. Template gets `view`, `is_paired`, `sibling_order_id`. POST target stays the primary order_id (sibling is resolved via `also_resolve_paired` flag).
  - `POST /approvals/{order_id}/decide` extended: accepts `decision="modify"` with required `new_qty` validation (numeric, > 0) — 400 on missing/invalid/zero/negative qty. Accepts `also_resolve_paired` form field (or JSON bool) — when truthy AND sibling pending, both Futures resolve atomically (single POST). Response message includes "(qty=X)" for modify and "· both legs resolved" when paired.
- `trading_corp/web/templates/approvals.html`:
  - Renders `rows[]` instead of `entries[]`. Paired rows get a `paired` badge and the combined headline.
  - Header shows "(N cards · M legs)" when N != M (i.e., paired rolls present).
- `trading_corp/web/templates/approval_detail.html`:
  - Replaced the raw-JSON `<pre>` dump with structured rendering: headline (emoji + action label + symbol + division + paired-roll badge); trade-legs block (each leg: side + qty + option/symbol + dte/delta + mark + bid/ask + signed gross dollars; net row when ≥2 legs); position-context block (LEAP, days held, cost vs mark, P&L pct, unrealized $/%, roll count + prior credit); risk verdict block (color-coded by verdict); warnings block (when present).
  - Inline Modify form expands on click: numeric input + optional reason; submits `decision=modify` to existing POST endpoint. Disabled with explanatory tooltip when the card is paired (paired-modify is B.x; Telegram `/modify <id> <qty>` still works as fallback).
  - Form-submit JS intercepts both Approve/Reject/Modify; URL-encodes form data manually so the also_resolve_paired hidden field travels with paired cards. Result fragment swap stays in-page (mobile UX). 409 → "already decided" warn message; 400 → strips HTML and shows up to 200 chars of detail.
  - Raw-detail JSON kept as a collapsible `<details>` debug block at the bottom.

**Local-only (NOT deployed):**
- `tests/test_position_context_view.py` — 20 new tests pinning the structured dict shape (headline action labels for option/roll/stock/crypto, dollar math sign, bid/ask extraction, leap pnl_pct computation, risk color normalization, pair_id extraction, defensive fallbacks for missing/malformed extra_json, coalesce close-first ordering + net math + singleton pass-through + empty-list raise).
- `tests/test_pair_grouping.py` — 6 tests for `_group_orders_by_pair_id` (solo passthrough, paired grouping, mixed solo+paired, two independent pairs, empty list, group ordering preserves first-leg position).
- `tests/test_pending_registry.py` extended with 7 B.3 tests: pair_id extraction into entry, find_sibling both directions, find_sibling None when solo / no pair_id, also_resolve_paired atomicity (both Futures resolved with same decision), audit row tagging with paired_with on each leg, graceful no-op when no sibling.
- `tests/test_approvals_routes.py` extended with 9 tests: 6 modify-flow tests (success, missing new_qty, zero qty, negative qty, non-numeric qty, JSON body) + 3 paired-flow tests (index coalescing, paired detail rendering with also_resolve_paired hidden field, paired POST resolves sibling, sanity test that omitting flag only resolves one leg).

**Features shipped (load-bearing for future "is X done?" checks):**
- **Web `/approvals/{order_id}` is now decision-quality.** A Board member can read the trade legs, position context (LEAP, prior rolls, P&L), risk verdict, and warnings on a phone screen — same information the Telegram rich body has rendered since 2026-05-02. The page is fully self-contained; tap Approve/Reject/Modify and the LangGraph resumes.
- **Modify on the web works end-to-end** for solo orders. Inline form, qty input, optional reason; submits to the same POST endpoint. graph/ceo_graph's `modify_then_risk_node` re-evaluates risk and re-emits an interrupt with the modified qty — the registry receives a NEW ApprovalRequest under the same order_id, the next Board action lands on the modified version. (Verified by route test; full graph integration relies on existing test_graph_hitl which is unchanged.)
- **Paired-roll coalescing is live.** When a PMCC scan emits a roll (close + open with shared pmcc_pair_id), `_group_orders_by_pair_id` puts both into the same group, asyncio.gather launches them in parallel, both interrupts fire, both ApprovalRequests land in the registry simultaneously. The web `/approvals/{order_id}` detail page renders ONE card with both legs + Net Debit/Credit; ONE Approve click resolves BOTH Futures via `also_resolve_paired=True`. The "approve close, reject open → naked short" failure mode is structurally impossible from the web surface — both legs share the same atomic decision.
- **Telegram inline keyboard still works in parallel** for both solo AND paired orders. The Telegram path resolves one leg at a time per click (legacy behavior unchanged); the web path resolves both legs per paired click. First-decision-wins applies per-leg, so a Telegram-approve-then-web-paired-approve race resolves cleanly (web's already-resolved leg returns False from registry.resolve and skips audit re-write).

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`asyncio.gather` for paired siblings is the load-bearing change.** Without it, sequential processing meant only ONE leg was ever in the registry at a time → web coalescing was meaningless. The change keeps risk-evaluation per-leg (each gets its own thread_id, each runs through risk independently in parallel — both legs are read-only on broker state at risk time, so parallel is safe). If a future strategy emits triplet+ orders sharing a pair_id (currently only PMCC roll = 2-leg compound), the same code paths handle them — `_group_orders_by_pair_id` doesn't cap group size, `coalesce_paired_view` accepts ≥2 views, `find_sibling` returns the FIRST sibling (not all). Triplets would render only 2 legs in the coalesced card; revisit if/when needed.
- **Render-time coalescing means the user can land on either leg's URL and see both.** The detail handler always builds the primary view, then asks the registry for a sibling. If found → coalesced view. If not → solo view. Sibling absence at render time is normal during the brief window between leg 1's interrupt firing and leg 2's interrupt firing — htmx polling on the detail page would close that gap; deferred to a B-v2 polish PR (design §6 v2 note).
- **Modify on paired cards is intentionally disabled in B.2.** The button shows but is disabled with a tooltip pointing the user at Telegram `/modify <id> <qty>` for individual-leg modifications. Modifying a paired roll is conceptually fraught — you'd need separate qty inputs per leg, and re-evaluating risk on the close leg might invalidate the open leg's risk verdict. Defer to a later phase that thinks through the modify-paired contract.
- **`coalesce_paired_view` uses the CLOSE leg's risk verdict as the primary.** Both legs were risk-evaluated independently; surfacing both verdicts in one card is a B-v2 polish task. Today the close leg's verdict is shown — typically "approve" since both legs of a roll usually pass risk. If the OPEN leg was risk-resized but the CLOSE leg approved cleanly, the user wouldn't see the resize on the coalesced card. Mitigation: warnings block surfaces both legs' warnings (deduped). Real-money risk is bounded — the resized qty already happened during risk evaluation, the user's only choice at this stage is approve/reject the post-risk shape.
- **`pmcc_pair_id` extraction lives in the registry, not the route handler.** Decoded once at `wait()` registration, stored on the entry. Route + sibling lookup just read `entry.pmcc_pair_id` — no JSON re-parse per request. Test fixtures need to include the `extra_json` field on the order row to exercise pair-coalescing.
- **B.4 still NOT shipped.** `TELEGRAM_NOTIFICATION_ONLY=true` env stays unset. Soak the parallel paths (rich Telegram + new structured web) for ~1 week before flipping. Until then, both surfaces work; first-decision-wins resolves any race.

**Latent bugs caught + fixed:** none.

**Verification:**
- Pre-deploy: 485 unit tests pass on local (vs 442 pre-B.2/B.3 baseline; +43 = 20 view + 7 registry + 6 grouping + 6 routes new modify + 4 routes new paired). 5 pre-existing P2 failures unchanged (BACKLOG line 1247).
- md5 6/6 files MATCH between local and prod post-scp.
- Backup tag `.pre-b23-hitl-rich-pairs-20260503-0417` placed on the 5 mutated prod files pre-deploy.
- PID 118285 → 119776 (restart at 04:20:05 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~33s after restart (Robinhood + Fidelity logins block bind, normal).
- `GET /` 200 in 2.96s; `GET /approvals` 200 in 2.74s (renders new "HITL · phase B" badge + empty state); `GET /research` 200 in 2.55s.
- POST `/approvals/x/decide` with invalid decision returns 400 with the new error string `"decision must be 'approve', 'reject', or 'modify'"` — confirms the modify branch landed and the error message updated.
- journalctl post-restart: 6 ERROR lines, all pre-existing — 5 Fidelity Azure-IP block (BACKLOG P1 #1276) + 1 yfinance BTC/USD earnings noise. Same set as B.1 deploy + every restart since Fidelity scope was added. ZERO new errors related to position_context, pair grouping, registry pair semantics, or the modify form.
- Audit log on prod immediately after restart shows `research_position_context_emitted` rows for both lord_otter and market_cypher (the existing position-context prime task runs on startup, unrelated to this deploy — sanity-check that the rest of the system is healthy).

**Inert / dormant on current traffic:**
- **Pair coalescing has no work to do until a PMCC scan emits a roll.** Sun pre-market (deploy time was 04:20 UTC Sunday); next scheduled scan Mon 2026-05-04 ~13:30 UTC. If that scan emits any rolls (LEAP-Hard-Rule promotion fires, halfway-roll cooldown doesn't fire, etc.), they'll be the first production exercise of the parallel-grouping + coalesced-card flow. Solo orders (open_pmcc, sell_weekly) take the unchanged sequential path.
- **Modify form has no users until a Board approval is pending.** Same condition as above — first scout-emitted approval Mon 13:30 UTC.
- **Restart-recovery still NOT wired** (carried forward from B.1 — design §3 / §9 v2). Mid-approval restart wipes the registry; LangGraph state survives in SqliteSaver but the user has no surface to act on it. Acceptable; v2 polish.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-b23-hitl-rich-pairs-20260503-0417; BASE=/home/azureuser/trading_corp;
for f in trading_corp/comms/pending_registry.py trading_corp/main.py trading_corp/web/routes.py trading_corp/web/templates/approvals.html trading_corp/web/templates/approval_detail.html; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
rm -f \$BASE/trading_corp/comms/position_context.py;
sudo systemctl restart trading-corp
"
```

(Note: this rollback ALSO needs to back-out B.1's registry seam if you want to fully revert HITL-in-app — see the B.1 rollback at the 03:50 UTC entry. Reverting B.2/B.3 alone leaves B.1's bare-bones `/approvals` page intact, which is a valid stopping point.)

---

## 2026-05-03 03:50 UTC — Phase B.1: HITL `/approvals` web surface + PendingApprovalRegistry seam

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board direction (BACKLOG P0 — "HITL approval flow lives in the web app", NEW 2026-05-03). Phase A shipped 02:09 UTC tonight (dormant slim-Telegram switch). Phase B.1 builds the foundation the slim flag will eventually point at: a new `PendingApprovalRegistry` that owns the per-order Future, plus three new `/approvals*` routes that read + resolve via that registry. Telegram inline-keyboard path is preserved in parallel (first-decision-wins) so the soak window has both surfaces live. User explicit go-ahead in-session 2026-05-03 ~02:30 UTC; chose two-cut strategy (B.1 separate from B.2+B.3) so the load-bearing registry seam validates discrete on prod before stacking polish/pair-coalescing on top. Markets-closed Sun→Mon window picked deliberately for HITL deploys.
**Backup tag:** `.pre-b1-hitl-web-20260503-0349` (on the 4 mutated files; the 3 new files have no backup target — `rm` is the rollback for them)

**Files deployed (4 modified, 3 new):**

*New:*
- `trading_corp/comms/pending_registry.py` — `PendingApprovalRegistry` class. Public surface: `wait(req, timeout_s)` (orchestrator-side; replaces `channel.request_approval`), `resolve(order_id, decision, source)` (resolver-side; called by Telegram callback OR web POST; first-wins), `register_notifier(fn)` (fan-out hook; TelegramChannel registers its message-send), `list_pending()` / `get(order_id)` / `get_entry(order_id)` (read-only views for the index/detail UIs), `pending_count()`. Audit chain: `pending_approval_added` (written by `wait()` BEFORE notifiers fire so dashboard can recover from audit even if notifiers all fail) → `board_decision_received` (written by `resolve()` with `source` tag — 'telegram'/'web'/'cli'/'auto'/'timeout'). Exception in one notifier doesn't block others (`_safe_notify` wraps each with broad except). Audit writes are best-effort (try/except) so a temporarily-down audit DB doesn't block approvals.
- `trading_corp/web/templates/approvals.html` — index template. Empty state + populated list. Each row: summary, division, order_id (truncated), added_at HH:MM:SSZ, "Review →" link to detail page. Tailwind chrome via `base.html`. Mobile-responsive.
- `trading_corp/web/templates/approval_detail.html` — detail template. Header (division · order_id · added_at), summary block, expandable raw-detail JSON dump (B.2 will replace with structured renderer), Approve / Reject form buttons. In-page JS intercepts the form POST, swaps result into `#decision-result` so the user stays on the page (no full-page reload). 409 → "Already decided" message with warn color. Modify intentionally NOT shipped in B.1 (deferred to B.2 per design §14).

*Modified:*
- `trading_corp/comms/telegram_bot.py` — constructor accepts `registry: PendingApprovalRegistry | None = None`. New `_build_approval_message(req)` factored out of the existing `request_approval` so the slim/rich body logic is shared. New `_notify_approval(req)` is the fan-out hook (registered on registry in `start()` after `_app` is initialized — needs the bot to send messages). `request_approval` now branches: when registry is set, delegates to `await self._registry.wait(req)`; without registry, falls back to the legacy in-channel `_pending` Future flow (preserved verbatim so non-registry paths — CLI dev, older tests — see byte-identical behavior). `_on_callback`, `/approve`, `/reject`, `/modify` all migrated to `_resolve_decision(order_id, decision)` helper that prefers `registry.resolve(..., source="telegram")` then falls back to legacy `_pending`. `_on_status_cmd` reads count from registry when wired. Inline keyboard preserved in slim mode (Phase A behavior unchanged).
- `trading_corp/main.py` — constructs `pending_registry = PendingApprovalRegistry(logger_agent=logger_agent)` immediately after the agent block, before TelegramChannel. Threaded into `TelegramChannel(... registry=pending_registry)` constructor, into `tg_deps = WebDeps(... pending_registry=pending_registry)` (for /pending command surface, future-proofed though not consumed in B.1), and into `_start_web_server(... pending_registry=pending_registry)` which forwards to `WebDeps(... pending_registry=...)`. `_run_order` is UNCHANGED — still calls `await channel.request_approval(req)`; the channel internally delegates to `registry.wait(req)` when wired, so the orchestrator path is byte-identical at the call-site level. This keeps test_graph_hitl.py green without modification.
- `trading_corp/web/app.py` — `WebDeps` dataclass gains `pending_registry: Any = None` field. Doc-commented as "constructed in main.py before TelegramChannel so the channel can register its message-send as a notifier."
- `trading_corp/web/routes.py` — three new routes registered after `/system`:
  - `GET /approvals` — `templates.TemplateResponse("approvals.html", {snap, entries, registry_unavailable})`. Empty state when `entries == []`; explanatory note when registry is None (CLI fallback / dev).
  - `GET /approvals/{order_id}` — fetches `registry.get_entry(order_id)`; 404 when not pending. Renders detail template.
  - `POST /approvals/{order_id}/decide` — accepts JSON or form-encoded body; `decision` in `{approve, reject}` (modify deferred); calls `registry.resolve(order_id, decision, source="web")`. 200 + small HTML fragment on accept; 409 on already-resolved; 400 on bad decision; 404 on no-registry.

**Local-only (NOT deployed):**
- `tests/test_pending_registry.py` — 11 tests covering wait/resolve happy path, idempotency (second resolve returns False), unknown-order-id, audit row writes (`pending_approval_added`, `board_decision_received` with source tag), notifier fan-out + exception isolation, list_pending newest-first ordering, get/get_entry.
- `tests/test_approvals_routes.py` — 13 tests using FastAPI TestClient: index empty/populated/no-registry states, detail 200/404 + raw-detail JSON rendering, decide approve/reject (resolves Future), 409 on duplicate, 400 on invalid/unknown decision, 404 when registry is None, JSON-body acceptance.

**Features shipped (load-bearing for future "is X done?" checks):**
- **`PendingApprovalRegistry` is the new HITL chokepoint.** Single instance per process (constructed in `main.py`). The web app + Telegram both share it; first-decision-wins, second gets a 409 (web) or "already decided" (Telegram). Tests construct their own per case.
- **Three new web routes are live behind Authelia:** `GET /approvals`, `GET /approvals/{order_id}`, `POST /approvals/{order_id}/decide`. The detail page works on a 375px-wide phone screen — confirmed in template; live mobile validation deferred to next signal.
- **Audit chain extended with two new kinds:** `hitl/pending_approval_added` (when an entry is registered) and `hitl/board_decision_received` (when a decision is resolved, tagged with source). Both are best-effort writes — registry continues working if audit DB is temporarily unavailable. The existing `board_approved` / `board_rejected` rows (written by graph nodes) remain unchanged.
- **TelegramChannel is now mode-aware about the registry.** When constructed with `registry=...`, message-send is registered as a notifier on `start()` and inline-keyboard / command resolution all flow through `registry.resolve(..., source="telegram")`. Without a registry, byte-identical legacy behavior (preserved for CLI dev + non-Telegram test paths).
- **Telegram inline keyboard still works in parallel.** Both surfaces (web + Telegram) converge at the same Future. The slim-format flag from Phase A stays OFF — soak window observes both surfaces with the rich body still shipping to Telegram.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`_run_order` is unchanged at the call site.** It still calls `await channel.request_approval(req)`. The redirection to `registry.wait` happens inside TelegramChannel — keeps the orchestrator API stable and means test_graph_hitl.py works without modification (it bypasses channels entirely via `Command(resume=...)`). Anyone who later wants the orchestrator to call `registry.wait` directly can do so; current shape is the smaller-diff option.
- **TelegramChannel keeps a vestigial `_pending` dict for legacy paths.** When constructed without a registry (CLI dev, older tests), the dict still owns the Future. Production always wires a registry → dict is unused. Don't delete the dict in a B.2 polish PR without auditing every TelegramChannel construction site.
- **`/approvals` reads from in-process state, not the audit DB.** A restart wipes the registry. Suspended LangGraph threads survive in the SqliteSaver checkpointer, but the registry itself doesn't auto-recover. Recovery (read recent `pending_approval_added` audit rows that don't have a matching `board_decision_received`, re-add to registry, re-emit notification) is a B-v2 polish item documented at `planning/hitl_in_app_design.md` §3 + §9. Today, a mid-approval restart loses the approval surface — the user re-triggers via the originating scout/webhook.
- **Modify intentionally NOT shipped in B.1.** Web POST returns 400 if `decision="modify"`. Telegram `/modify <id> <qty>` still works (resolves the registry directly). B.2 lands the web-side modify flow with htmx swap + form expansion.
- **`/approvals` route registered before the catch-all 404 handler.** FastAPI route order matters; verified by the 404-on-unknown-order-id test.
- **Phase A slim-format flag is unchanged.** `TELEGRAM_NOTIFICATION_ONLY` stays OFF on prod systemd. Soak Phase B.1 + Phase B.2 + Phase B.3 with rich body + web in parallel; flip the flag at B.4 after ~1 week of confidence.

**Latent bugs caught + fixed:** none.

**Verification:**
- Pre-deploy: 442 unit tests pass on local (vs 418 pre-B.1 baseline; +24 = 11 `test_pending_registry` + 13 `test_approvals_routes`). 5 pre-existing P2 failures unchanged (BACKLOG line 1247, PMCC scan liquidity gate — unrelated to B.1).
- md5 7/7 files MATCH between local and prod post-scp.
- Backup tag `.pre-b1-hitl-web-20260503-0349` placed on the 4 mutated prod files pre-deploy.
- PID 115197 → 118285 (restart at 03:50:38 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~36s after restart (Robinhood + Fidelity logins block bind, normal).
- Symbol-presence checks on prod files: `PendingApprovalRegistry` present in `pending_registry.py` + imported in `telegram_bot.py` + `main.py`; `_notify_approval` + `_resolve_decision` present in `telegram_bot.py`; `pending_registry` field present on `WebDeps`; three new routes present in `routes.py`; new templates present in `web/templates/`.
- `GET /` 200 in 2.53s; `GET /research` 200 in 2.61s.
- `GET /approvals` 200 in 2.52s. Response body contains "No approvals pending" — empty state rendering correctly.
- `GET /approvals/nonexistent` 404 — detail-page guard works.
- journalctl post-restart: only errors are pre-existing Fidelity Azure-IP block (BACKLOG P1 #1276 — datacenter IPs flagged; same pattern as every restart since Fidelity scope was added) and yfinance BTC/USD earnings noise (external API hiccup, same pattern as prior 02:09 UTC + 00:05 UTC deploys). ZERO new errors related to registry / pending_approval / hitl / web routes / templates.

**Inert / dormant on current traffic:**
- **No real PMCC-scout-emitted approvals are pending right now** (Sun pre-market; next scheduled scan is Mon 2026-05-04 ~13:30 UTC). The `/approvals` page shows the empty state. First production exercise of the registry's full wait→resolve loop happens on Monday's first Board-routed scout output. Until then, the integration is exercised only by the test suite's mock orchestration.
- **Restart-recovery is NOT wired.** If trading-corp restarts mid-approval (e.g. a deploy lands during Board deliberation), the registry empties; the suspended LangGraph thread state survives in the SqliteSaver but the user has no surface to act on it without re-triggering. Acceptable for B.1; recovery is a B-v2 polish item.
- **`TELEGRAM_NOTIFICATION_ONLY` env stays unset.** Phase A's slim Telegram body remains dormant. Soak the parallel paths (rich Telegram + new web) for ~1 week before flipping at B.4.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-b1-hitl-web-20260503-0349; BASE=/home/azureuser/trading_corp;
for f in trading_corp/comms/telegram_bot.py trading_corp/main.py trading_corp/web/app.py trading_corp/web/routes.py; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
rm -f \$BASE/trading_corp/comms/pending_registry.py \\
      \$BASE/trading_corp/web/templates/approvals.html \\
      \$BASE/trading_corp/web/templates/approval_detail.html;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-03 02:09 UTC — Phase A: HITL slim-Telegram bridge + PMCC prompt-text refinements (cooldown reframing + LEAP-Hard-Rule note + STD strike example)

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board direction (BACKLOG P0 — "HITL approval flow lives in the web app; Telegram becomes notification-with-deeplink", NEW 2026-05-03). Phase A is the smallest cut of that P0: dormant `notification_only` switch in `TelegramChannel` + new slim-format builder + env-var wiring. Bundled with PMCC prompt-text clarifications (4 edits — COOLDOWN reframing in BS+STD blocks, BLACK_SHEEP LEAP-Hard-Rule NOTE, STANDARD STRIKE TARGETING regime-appropriate example) since the working-tree file was already mutated alongside the comms changes. User explicit go-ahead in-session 2026-05-03 ~02:00 UTC after deciding to skip the Monday PMCC scan validation gate (will validate live on next signal). The prior plan (BACKLOG ## 📦 PENDING DEPLOY) called for waiting until Mon ~13:30 UTC; that gate is dropped because the slim-format change is dormant by default and the prompt edits are LLM-facing only — first signal after deploy exercises both safely.
**Backup tag:** `.pre-phase-a-slim-telegram-20260503-0209` (on the 4 mutated files)

**Files deployed (4):**
- `trading_corp/comms/approval_format.py` — adds `format_slim_approval_notification(order, order_id, division, base_url)` returning a Markdown-Telegram body of the form `<ACTION> · <SYM> · <division>\n\n[Review on dashboard →](<base_url>/approvals/<order_id>)`. Existing `format_approval_message` (rich body) preserved unchanged. Defined at `approval_format.py:29`. URL is `{base_url}/approvals/{order_id}` — pair-coalescing happens server-side at the dashboard once Phase B ships, not in the formatter.
- `trading_corp/comms/telegram_bot.py` — `TelegramChannel.__init__` gains `notification_only: bool = False, dashboard_base_url: str | None = None` kwargs (line 40-41). `request_approval` branches on the flag (line 184): when False (default) emits the existing rich format; when True calls `format_slim_approval_notification`. Inline approve/reject keyboard preserved in BOTH modes during Phase A — lets us flip the flag the day Phase B ships without losing tap-to-approve until the web button is canonical.
- `trading_corp/main.py` — env-var wiring (line 500-507): `TELEGRAM_NOTIFICATION_ONLY=true` flips the flag; `DASHBOARD_BASE_URL` overrides the production default. Both default to safe values that preserve the rich body.
- `trading_corp/agents/divisions/pmcc_robinhood.py` — 4 prompt-text edits, no code-path changes:
  1. **`_BLACK_SHEEP_RULES` Rule 6 COOLDOWN reframed.** Was a NOTE explaining the deterministic `_recent_halfway_roll_cooldown` backstop. Now the rule itself instructs "HONOR the cooldown directly when ROLL HISTORY shows a recent halfway roll" with concrete acceleration-override math (`spot now > prior_short_strike_after + |prior_strike_change|`). Backstop language demoted to "BACKSTOP: the guard catches the override-vs-cooldown edge cases" — keeps the LLM and the guard semantically aligned rather than appearing to fight each other.
  2. **`_BLACK_SHEEP_RULES` LEAP-Hard-Rule NOTE added** (12 lines after the strict perpetual-roll philosophy section). Explains that `_promote_to_roll_leap_if_hard_rule` fires regardless of regime when LEAP delta>=0.95 OR DTE<120, AND that BS philosophy normally would defer LEAP exit longer — so when the guard promotes a roll_short → roll_leap on a BS position, the user can still reject the LEAP roll and approve only the short roll. Tells the LLM how to AVOID triggering the guard (choose `hold` or `watch` instead of `roll_short` until DTE crosses BS exit threshold).
  3. **`_STANDARD_RULES` BREACH POLICY COOLDOWN reframed** (parallel to BS Rule 6 above). Same "HONOR the cooldown directly" language + concrete acceleration math + BACKSTOP demotion.
  4. **`_STANDARD_RULES` STRIKE TARGETING example regime-appropriated.** Was "halfway midpoint = $X.XX" (a BS-shaped example). Now "roll above $200 resistance" — a STD-regime example. Also added explicit guidance to LEAVE `target_strike` null for normal cycle rolls where delta-target IS the selection criterion (avoids over-eager target_strike population that would defeat delta ranking).

**Local-only (NOT deployed):**
- `tests/test_slim_approval_notification.py` — 11 tests pinning the slim body shape (headline composition, division-omission when None, URL formatting, base_url trailing-slash handling, Markdown-link safety). Local-only: prod doesn't run tests in-tree.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Slim Telegram body shape is locked.** `format_slim_approval_notification` is the canonical function; any future caller that wants the slim ping (e.g. paired-roll coalesced URLs in Phase C) calls this. URL contract: `<base_url>/approvals/<order_id>` for individual orders. Pair-form (`/approvals/pair/<pmcc_pair_id>`) is reserved for Phase C and would need a sister function.
- **`TelegramChannel` is now mode-aware.** Instantiating with `notification_only=True` flips to slim; default False preserves rich body. Mode is per-instance, not per-call — so all approvals from a single channel use the same body shape.
- **Env contract:** `TELEGRAM_NOTIFICATION_ONLY` (default `false`) + `DASHBOARD_BASE_URL` (default the production URL) wired into the channel constructor in `main.py`. To activate slim mode in prod, set `TELEGRAM_NOTIFICATION_ONLY=true` on the systemd unit AFTER Phase B `/approvals/{id}` route exists. Until then: do not flip.
- **PMCC prompt rule clarifications LIVE on next analysis.** COOLDOWN clauses now cite the deterministic backstop explicitly; LEAP-Hard-Rule NOTE explains the cross-regime promotion path; STANDARD STRIKE TARGETING example is regime-appropriate. The Anthropic API call uses the new prompt text on every scan / re-analyze trigger from this restart forward.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Slim mode is opt-in and dormant on this deploy.** Default `notification_only=False`; env vars unset on prod systemd unit (verified). Behavior at the Telegram-message layer is byte-identical to pre-deploy. The new code paths exist but are not exercised on production traffic.
- **Inline keyboard is preserved in slim mode (Phase A bridge behavior).** When `notification_only=True` activates (post-Phase-B), the message body shrinks to "headline + deeplink" but the inline keyboard stays so the existing Telegram approve/reject still works. Phase B's web button becomes the canonical surface; the keyboard goes away when the bridge is removed (likely Phase C or D — TBD in `planning/hitl_in_app_design.md`).
- **`format_slim_approval_notification` takes `order_id` explicitly,** not derived from `order` — because the order shape across callers (ProposedOrder vs DB-row dict) doesn't carry the id consistently. Caller is responsible for passing it.
- **PMCC prompt-text changes are LLM-facing only.** No new symbols introduced. The deterministic guards shipped at 00:05 UTC (`_recent_halfway_roll_cooldown`, `_promote_to_roll_leap_if_hard_rule`) and 00:36 UTC (`target_strike` plumbing) are unchanged. Symbol-presence on prod files re-verified post-deploy.
- **The COOLDOWN reframe shifts authority from "LLM informed by NOTE about backstop" to "LLM applies cooldown directly, backstop catches edge cases."** Net behavior should be: fewer cases where the LLM picks `roll_short` and the guard rewrites to `hold` (and the audit row reads inconsistently — LLM rationale says "roll", action says "hold"). Back-to-back halfway rolls are still prevented either way; the difference is the LLM's narration aligns with the action.

**Latent bugs caught + fixed:** none.

**Verification:**
- Pre-deploy: 418 unit tests pass on local (5 pre-existing P2 failures unchanged — same `_call`-helper liquidity-gate trap as prior deploys; not in this batch's blast radius).
- md5 4/4 files MATCH between local and prod post-scp.
- Backup tag `.pre-phase-a-slim-telegram-20260503-0209` placed on all 4 files pre-deploy.
- PID 113881 → 115197 (restart at 02:09:48 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~33s after restart (Robinhood + Fidelity logins block bind, normal).
- Symbol-presence checks on prod files: `format_slim_approval_notification` (1 hit, definition + import in telegram_bot.py implied by 3 hits there); `notification_only` (3 hits — kwarg + assign + branch); `TELEGRAM_NOTIFICATION_ONLY` (2 hits — comment + getenv); `DASHBOARD_BASE_URL` (2 hits — comment + getenv).
- `GET /` 200 in 2.63s; `GET /research` 200 in 2.61s.
- journalctl post-restart: only errors are the pre-existing Fidelity Azure-IP block (BACKLOG P1 #1276 — datacenter IPs flagged at network layer; same pattern as every restart since Fidelity scope) and yfinance BTC/USD earnings noise (external API hiccup, same pattern as prior 00:05 UTC deploy line 160). No ImportError / NameError / AttributeError / Traceback related to the new code.
- Env sanity: `TELEGRAM_NOTIFICATION_ONLY` and `DASHBOARD_BASE_URL` neither set on systemd unit — slim mode dormant, as planned.

**Inert / dormant on current traffic:**
- **Slim Telegram format is dormant.** `notification_only=False` default + env unset → rich body is what gets sent. Activates only when (a) Phase B `/approvals/{id}` page exists on prod AND (b) `TELEGRAM_NOTIFICATION_ONLY=true` is added to the systemd unit. Until then this deploy is byte-for-byte equivalent at the Telegram-message layer.
- **PMCC prompt refinements active immediately on the LLM call path.** Anthropic API call uses the new prompt text on every scan / re-analyze trigger from this restart forward. First production exercise: next scheduled scan (Monday 2026-05-04 ~13:30 UTC) or any "Re-analyze" click before then. Per user's "validate live on next signal" decision, no synthetic test was run pre-deploy — the next real PMCC analysis is the validation event.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-phase-a-slim-telegram-20260503-0209; BASE=/home/azureuser/trading_corp;
for f in trading_corp/comms/approval_format.py trading_corp/comms/telegram_bot.py trading_corp/main.py trading_corp/agents/divisions/pmcc_robinhood.py; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-03 00:36 UTC — PMCC P1 (Item 3): target_strike honors LLM rule-driven strike (halfway-rule strike drift fix)

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board pre-authored fix sketch in BACKLOG.md (P1 — PMCC roll: Recommended strike ignores the halfway-rule the expert text cites). User explicit go-ahead in-session 2026-05-02 after re-evaluating the residual risk under paper-mode + HITL: even if a wrong strike is recommended, it surfaces in the Telegram approval card and the Board catches it before placement. Closes the third of three related PMCC roll-correctness items shipped this session (sister DONE entries: roll-history blindness + LEAP-roll-missing).
**Backup tag:** `.pre-pmcc-target-strike-20260503-0035` (on the 1 mutated file)

**Files deployed (1):**
- `trading_corp/agents/divisions/pmcc_robinhood.py` — five touchpoints, all threading the new `target_strike` field through the existing call chain:
  1. **`PMCCAnalysis` dataclass** gains `target_strike: float | None = None`. Backwards-compat default — existing callers that don't supply it get None and behave identically. Field annotated with the role: when set, strike picker honors it and overrides delta-distance ranking.
  2. **`_select_weekly_strike(calls, target_delta, target_strike=None)`** — when `target_strike` is set, picks the listed strike whose `strike_price` is closest to it (ignoring delta entirely). Caller is responsible for sanity — we don't second-guess (the LLM cited the strike per its rules; e.g. an ITM defensive halfway-roll). When `target_strike` is None, falls through to the original delta-distance behavior with OTM-only filtering. Defensive: returns None if no eligible strike (no strike_price field).
  3. **`_find_best_weekly(symbol, broker, target_delta=None, target_dte=None, target_strike=None)`** — accepts target_strike, threads to `_select_weekly_strike`. DTE/expiry-window selection unchanged.
  4. **LLM prompt JSON schema** (in `_llm_analyze_position`) — added `"target_strike": <recommended short call STRIKE as float, or null>` to the response template. Field annotated as: "set this when a rule prescribes a specific strike (e.g. halfway-roll midpoint per BREACH HANDLING). When set, the strike picker honors this directly, overriding delta-distance ranking. Leave null when delta-targeting is correct (standard cycles)."
  5. **JSON parse** (in `_llm_analyze_position`) — extracts `target_strike` with the same float-or-None pattern as `target_delta`. Defensive: if the LLM omits the field, falls through to None (no strike override, original behavior).
  - **Threaded through 5 callers** of `_find_best_weekly` (single-line in 4 places, multi-line in 1): `propose_orders_for_pair` `roll_leap` 4th-leg, scan-path inline `roll_leap` 4th-leg, `_propose_open_pmcc`, `_propose_sell_weekly`, `_propose_roll_short`. Each adds `target_strike=analysis.target_strike if analysis else None`.
  - **Prompt rule corpus updated:** `_BLACK_SHEEP_RULES` Rule 6 (BREACH HANDLING) and `_STANDARD_RULES` BREACH POLICY each gained a STRIKE TARGETING clause instructing the LLM to populate `target_strike` when narrating a specific strike (halfway midpoint or rule-cited target). Without this clause the LLM had no signal that the new field existed; with it, the rule application is coherent end-to-end.

**Features shipped (load-bearing for future "is X done?" checks):**
- **`PMCCAnalysis.target_strike` is now a real field.** Anywhere downstream that needs to know what strike the LLM cited can read `analysis.target_strike` directly instead of regexing the rationale text.
- **Strike picker honors LLM-cited strikes.** When the LLM applies a rule like "Major Breach → halfway midpoint = $X.XX" and populates `target_strike`, the recommendation card's open leg lands at the listed strike closest to that value. Pre-fix the picker fell back to `target_delta` ranking, which on high-IV underlyings typically picked a strike well above the cited halfway midpoint — the BACKLOG-cited MSTR symptom (cited $169, picked $187.50). BACKLOG.md "P1 — PMCC roll: Recommended strike ignores the halfway-rule the expert text cites" → DONE.
- **Backwards-compat preserved.** `target_strike=None` (the default + the LLM's response when omitted) → original delta-distance behavior. No drift on standard-cycle recommendations. Pinned by `test_propose_roll_short_falls_back_to_delta_when_target_strike_none`.
- **All 5 `_find_best_weekly` call sites are wired.** Both `roll_leap` 4-leg branches (propose_orders_for_pair + scan-path) honor target_strike on the new-short leg, so a halfway-into-LEAP-roll scenario gets both LEAP rolled AND new short at the LLM-cited strike.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`target_strike` overrides target_delta when both are set.** This is intentional — the LLM populates target_strike only when it has a rule-driven specific target; in that case the delta is incidental. If you ever need both honored simultaneously, change `_select_weekly_strike` to filter by delta range and minimize strike distance within that pool. Today's behavior is "strike wins" because that's what the rule citation requires.
- **No spot-acceleration check in the picker.** Same rationale as the cooldown guard from the prior deploy: keep the deterministic helper simple; the LLM's rule corpus is responsible for choosing the right strike based on regime/IV context. The picker just honors the choice.
- **Prompt rule clauses added to BOTH `_BLACK_SHEEP_RULES` Rule 6 AND `_STANDARD_RULES` BREACH POLICY.** Both regimes need the STRIKE TARGETING note because both regimes can cite specific strikes (halfway-roll for black sheep on Major/Runaway breach; up-and-out for standard on Major). If a future regime-specific rule block is added (e.g. crypto-options-specific), it needs the same clause.
- **The 5 caller threading is mechanical.** If a future `_find_best_weekly` caller is added (e.g. a new strategy variant), pattern-copy the existing `target_strike=analysis.target_strike if analysis else None` line.

**Latent bugs caught + fixed:** none. The 5 pre-existing P2 PMCC scan failures (BACKLOG.md line 1093) remain unchanged; my new tests use the local `_liquid_call` helper introduced in the prior deploy to avoid the same trap.

**Verification:**
- Pre-deploy: 407 unit tests pass on local (vs 397 baseline before this deploy; +10 = 4 strike-picker tests, 2 dataclass tests, 1 `_find_best_weekly` integration, 2 `_propose_roll_short` end-to-end, 1 defensive). 5 pre-existing P2 failures unchanged.
- md5 1/1 file MATCH between local and prod post-scp.
- Backup tag `.pre-pmcc-target-strike-20260503-0035` placed on `pmcc_robinhood.py` pre-deploy.
- PID 112932 → 113881 (restart at 00:35:50 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~45s after restart (Robinhood + Fidelity logins block bind, normal).
- Symbol presence: `target_strike` appears 26 times in prod's `pmcc_robinhood.py` (definition + dataclass + 5 callers + parse + prompt schema + 2 rule clauses + tests in comments + helper signature). Local matches.
- `GET /` 200 in 2.75s; `GET /research` 200 in 2.62s.
- journalctl post-restart: zero errors of any kind in the filtered window. Service loaded cleanly.

**Inert / dormant on current traffic:**
- The `target_strike` override is dormant until the LLM populates the field on its next analysis. The Anthropic API call is live on the existing scan schedule; the prompt rule clause is the trigger that gets the LLM to populate it. Expect the next scheduled scan (Monday 2026-05-04 ~13:30 UTC) to be the first time the field gets exercised live for a Board-visible recommendation.
- Backwards-compat path (target_strike=None) is what every current cached analysis (if any) and every LLM response that doesn't include the new field will exercise. This path is byte-identical to pre-deploy behavior.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-pmcc-target-strike-20260503-0035; BASE=/home/azureuser/trading_corp;
mv \$BASE/trading_corp/agents/divisions/pmcc_robinhood.py\$TAG \$BASE/trading_corp/agents/divisions/pmcc_robinhood.py;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-03 00:05 UTC — PMCC P1 guards: LEAP Hard Rule promotion + halfway-roll cooldown + roll_leap 4-leg compound

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board pre-authored fix sketches in BACKLOG.md (P1 — PMCC roll: LLM analyzer is blind to recent roll history; P1 — PMCC drilldown: Recommended Trade omits the LEAP roll when both legs need to roll). User explicit go-ahead in-session 2026-05-02. Both items are real-money correctness gaps: (a) the LLM analyzer was recommending back-to-back halfway rolls because its prompt had zero history; (b) when LEAP delta>=0.95 OR DTE<120 the recommendation card was emitting a 2-leg roll_short instead of a 4-leg roll_leap, leaving a fresh short on a dying LEAP if approved.
**Backup tag:** `.pre-pmcc-p1-guards-20260502-2357` (on the 2 mutated files)

**Files deployed (2):**
- `trading_corp/agents/divisions/pmcc_robinhood.py` — five additions, three deterministic-then-narrate guards plus the 4-leg `roll_leap` extension:
  1. **`_promote_to_roll_leap_if_hard_rule(analysis, leg)`** (Item 2). Promotes `roll_short` / `roll_short_early` → `roll_leap` when `leg.long_leg_delta >= 0.95` OR `leg.long_leg_dte < 120`. Honors CLAUDE.md §1's deterministic-then-narrate principle — the LEAP Hard Rule trigger is purely a function of already-computed numeric state, so it shouldn't ride through LLM judgment. Adds an explanatory warning to `analysis.warnings` so the audit trail + Telegram approval message render the reason.
  2. **`_recent_halfway_roll_cooldown(analysis, leg)`** (Item 1). Backstop guard that downgrades `roll_short` → `hold` when a recent roll-up (positive strike_change >= $1) was executed within `cooldown_days` (default 7) AND short DTE > terminal_dte_floor (default 2) AND extrinsic > extrinsic_floor (default $0.50/sh). The LLM also gets the rule clause + ROLL HISTORY block in its prompt and should already prefer HOLD; this guard is the deterministic backstop.
  3. **`_query_prior_rolls_detailed(symbol, leap_lifetime_key)`** (Item 1). Sister to the existing `_query_prior_rolls` — same SQL/grouping but returns `last_roll_ts`, `last_roll_short_strike_before`, `last_roll_short_strike_after`, `last_roll_strike_change`, `days_since_last_roll`. Used by the cooldown guard AND the prompt formatter. `leap_lifetime_key` scoping mirrors the existing helper (pre-fix NULL-keyed pairs preserved; mismatched-key pairs filtered).
  4. **`_format_roll_history_block(leg)`** (Item 1). Builds the ROLL HISTORY section injected into `_llm_analyze_position`'s prompt. Empty string when no DB; "No prior rolls" copy when DB is empty for this LEAP; otherwise count + net dollars + most-recent strike change with a roll-up/roll-down label.
  5. **4-leg `roll_leap` compound.** Both `roll_leap` branches (`propose_orders_for_pair` line ~1085 and the inline scan-path branch line ~1921) extended to emit a 4th order: open new short on the new LEAP. Skipped gracefully if no qualifying weekly chain — next scan picks up the uncovered LEAP via the `open_short` branch. Pre-fix the recommendation was 3 legs (close short + close LEAP + open new LEAP), which would leave the user uncovered if approved as-is. The BACKLOG entry's verification text required the 4-leg compound; the entry's claim that "the existing roll_leap action DOES already build a compound roll (close short + close LEAP + open new LEAP + open new short)" was inaccurate — this deploy makes that claim true.
  - Composition order at both call sites: `_terminal_dte_time_release` → `_promote_to_roll_leap_if_hard_rule` → `_recent_halfway_roll_cooldown`. Rationale: terminal-DTE first because deadline-driven rolls need to ship; Hard-Rule second because if the LEAP is dying, that lifts roll_short to roll_leap (cooldown is a no-op on roll_leap so a needed LEAP roll isn't silently vetoed); cooldown last as the pure backstop. Test `test_cooldown_does_not_fire_after_hard_rule_promotion` pins this composition.
  - Prompt updates: `_BLACK_SHEEP_RULES` Rule 6 (BREACH HANDLING) gained a COOLDOWN clause; `_STANDARD_RULES` BREACH POLICY gained the parallel clause; `_STANDARD_RULES` Rule 5 (HARD RULES) gained a NOTE about the LEAP-Hard-Rule promotion. ROLL HISTORY block injected before the JSON-response request in `_llm_analyze_position`.
- `config/strategies.yaml` — new `robinhood_pmcc.roll_cooldown` block: `cooldown_days: 7`, `extrinsic_floor: 0.50`, `min_strike_change: 1.0`, `terminal_dte_floor: 2`. Hot-reloadable via the same mtime-cache mechanism the rest of `_cfg` uses.

**Features shipped (load-bearing for future "is X done?" checks):**
- **LEAP Hard Rule promotion is live.** When a PMCC scan or dashboard "Approve & Execute" produces an analysis with `roll_short` action and the LEAP has delta >= 0.95 OR DTE < 120, the action is silently promoted to `roll_leap` and the recommendation card now shows all four legs. Distracted-approval risk on dying-LEAP scenarios eliminated for these conditions. BACKLOG.md "P1 — PMCC drilldown: Recommended Trade omits the LEAP roll when both legs need to roll" → DONE.
- **Halfway-roll cooldown is live.** When the prior fill on a LEAP's lifetime was a roll-up (>= $1 strike change) within the last 7 days AND the current short isn't deadline-driven AND extrinsic is non-trivial, `roll_short` is downgraded to `hold` with a warning. Back-to-back halfway-roll waste avoided. BACKLOG.md "P1 — PMCC roll: LLM analyzer is blind to recent roll history (recommends back-to-back halfway rolls)" → DONE.
- **`roll_leap` action emits a 4-leg compound** (close short + close LEAP + open new LEAP + open new short) instead of the prior 3-leg shape. Applies to BOTH dispatch sites — `propose_orders_for_pair` (used by dashboard "Approve & Execute" + Telegram per-pair approval) and the scheduled-scan inline branch.
- **`_query_prior_rolls_detailed` is now available** as a sister to `_query_prior_rolls`. Future callers needing per-roll metadata (strike change, last-roll ts, days-since) use this; old callers (Phase 2 position-context) keep using the simpler tuple shape unchanged.
- **LLM prompt now includes a ROLL HISTORY block** scoped to the current LEAP's `leap_lifetime_key`. The narration the LLM produces when ROLL HISTORY shows a recent roll-up should now coherently cite the cooldown rule even before the deterministic guard fires.
- **`config/strategies.yaml` carries `roll_cooldown` knobs.** Tuning the cooldown window or thresholds is a one-line YAML edit + service restart.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **The 4-leg `roll_leap` is a real-money path expansion.** Pre-fix the action emitted 3 legs; post-fix it emits 4 (or 3 with a logged "no qualifying weekly" note when the new-short fallback can't fill). Anything that previously assumed `roll_leap` produced exactly 3 legs (tests, audit-grep tooling, Telegram message templates) needs to handle the 4th leg gracefully. Quick scan: nothing in the repo greps on `roll_leap_close_short`/`roll_leap_close`/`roll_leap_open` count assumptions; new audit kind `roll_leap_open_short` mirrors the existing pattern (action stashed in `extra["action"]`).
- **Composition order at both call sites is load-bearing.** `_terminal_dte_time_release` → `_promote_to_roll_leap_if_hard_rule` → `_recent_halfway_roll_cooldown`. Re-ordering would allow the cooldown to veto a needed LEAP roll (if cooldown ran before Hard-Rule promotion). Test `test_cooldown_does_not_fire_after_hard_rule_promotion` pins it.
- **The cooldown's "is this a halfway-style roll-up" detector is a heuristic.** It uses `last_roll_strike_change >= min_strike_change` (default $1.00) — captures halfway-roll-into-breach and OTM target-delta roll-ups; excludes near-zero same-strike cycle drift. Doesn't try to detect the spot-acceleration override in Python (that belongs in the LLM rule clause where regime/IV context is available). False-positive cooldown costs "user overrides via Telegram"; false-negative costs "back-to-back halfway-roll waste." Bias is intentionally toward HOLD.
- **The cooldown queries by `leap_lifetime_key`** — multi-LEAP-on-one-symbol scenarios won't cross-contaminate. Pre-fix history (NULL keys) still folds into the count, same backwards-compat as `_query_prior_rolls`.
- **The ROLL HISTORY prompt block is empty for fresh positions.** No DB query at all when `_db_url` is unset (test/CLI path). When DB present but no prior rolls, prompt gets "No prior rolls recorded for this LEAP." — the LLM sees the absence explicitly rather than missing the section entirely.
- **Both new methods live near `_terminal_dte_time_release`** in the file, reflecting the pattern they share (deterministic post-processor on PMCCAnalysis that returns a possibly-modified `dataclasses.replace`).

**Latent bugs caught + fixed:** none specific. The 5 pre-existing P2 PMCC scan failures (BACKLOG.md line 1093, "5 PMCC scan tests failing on liquidity gate") are unchanged — same failures, same root cause (test fixture's `_call` helper omits `open_interest` + `volume`, fails the standard liquidity gate). My new tests use a local `_liquid_call` helper to avoid the same trap.

**Verification:**
- Pre-deploy: 397 unit tests pass on local (vs 370 baseline before this session; +27 = `_promote_to_roll_leap_if_hard_rule` × 7, `_recent_halfway_roll_cooldown` × 11 including composition, `_query_prior_rolls_detailed` × 4, `_format_roll_history_block` × 3, `roll_leap` 4-leg integration × 2). 5 pre-existing P2 failures unchanged.
- md5 2/2 files MATCH between local and prod post-scp.
- Backup tag `.pre-pmcc-p1-guards-20260502-2357` placed on both files pre-deploy (verified file sizes).
- PID 111560 → 112932 (restart at 00:05:31 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~45s after restart (Robinhood + Fidelity logins block bind, normal).
- Symbol-presence checks on prod files: `_promote_to_roll_leap_if_hard_rule` (4 hits), `_recent_halfway_roll_cooldown` (7 hits — counts include doc references in rule blocks + call sites + definition), `_query_prior_rolls_detailed` (4 hits), `roll_leap_open_short` (2 hits — both branch implementations), `roll_cooldown:` (1 hit in strategies.yaml).
- `GET /` 200 in 2.91s; `GET /research` 200 in 2.78s; `GET /partials/trade-flow` 200.
- journalctl post-restart: only error is a transient `yfinance HTTP 500` (external API hiccup, unrelated to deploy). No ImportError / NameError / AttributeError / Traceback related to the new code.

**Inert / dormant on current traffic:**
- The cooldown guard requires a recent FILLED roll on the same symbol within the cooldown window. Today the bot is paper-mode (`auto_execute: false` everywhere) so there are no filled real-money rolls — the guard will never fire on production traffic until either (a) auto_execute flips for PMCC, or (b) the Board approves a real roll via Telegram and the data_exec path writes a `filled` row. Until then, the guard is wired but inactive. Test coverage exercises it via seeded `proposed_order` rows.
- The Hard-Rule promotion fires whenever the LLM analyzer emits `roll_short` on a position with delta>=0.95 OR DTE<120. Several current Robinhood positions are within those LEAP conditions (per the dashboard); the next scheduled scan or "Re-analyze" click will exercise the promotion live.
- The 4th `roll_leap` leg fires the moment any `roll_leap` action is approved. Until a Board approval lands one, the new code path is dormant — but observable in the recommendation card preview as soon as the next scan produces a `roll_leap` recommendation.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-pmcc-p1-guards-20260502-2357; BASE=/home/azureuser/trading_corp;
for f in trading_corp/agents/divisions/pmcc_robinhood.py config/strategies.yaml; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 23:03 UTC — PMCC research-as-consultant validation surface (05-05 review tooling)

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board direction in-session — the 2026-05-02 vision realignment created a 3-day observation period (2026-05-02 → 2026-05-05) for PMCC's `universe_source: research_on_demand` integration. Decision criteria per the realignment memo: (a) count of `research_candidate_recommendation_emitted` rows from PMCC scout, (b) count of those that produced downstream order activity, (c) qualitative read on whether the research-recommended candidates are ones PMCC would have surfaced on its own. Without dedicated tooling that decision is a vibes-call on ad-hoc SQL on 05-05; this surface makes it tractable.
**Backup tag:** `.pre-pmcc-validation-view-20260502-2301` (on the 3 mutated files)

**Files deployed (3):**
- `trading_corp/agents/logger.py` — new `LoggerAgent.events_since(ts_iso, limit=5000)` method. Date-scoped audit fetch for multi-day windows that would overflow `recent_events()`'s limit. Returns newest-first. Used by the new validation view; existing `recent_events()` callers unaffected.
- `trading_corp/web/routes.py` — new `_build_pmcc_validation_view(deps)` joins three sources: `research_candidate_recommendation_emitted` (engagement-level + candidate list) ⨝ `research_candidate_acted_on` / `research_candidate_skipped` (per-candidate division row, keyed by `(engagement_id, symbol)`) ⨝ `proposed_order.status` (downstream lifecycle for acted_on candidates' order ids). Computes scoreboard counts + skip-reason histogram. Hard-coded observation window start = `2026-05-02T00:00:00Z`. Wired into `_build_research_view` return as `view.pmcc_validation`. New helper `_lookup_order_statuses(deps, order_ids)` does a bulk SELECT against `proposed_order` for the acted_on rows' order ids. New `_empty_pmcc_validation_view()` so the empty-deps branch returns the right keys.
- `trading_corp/web/templates/research.html` — new section "PMCC research-as-consultant validation" inserted between Engagement-latency and Recommendation-outcomes sections. Top scoreboard (Engagements / Candidates / Acted on / Skipped / Approved/filled — 5 numbers in a 5-col grid). Skip-reason histogram strip below. Per-engagement collapsible cards (newest open by default) with a 6-column candidate table: Symbol / Conviction / Fit / Status pill / Order status / thesis-or-skip-reason. Status pills color-code: acted=gain, skipped=warn, no-outcome=muted. Order-status colors: filled=gain, cancelled/risk_rejected/board_rejected=loss, others=muted.

**Features shipped (load-bearing for future "is X done?" checks):**
- **`/research` now has a "PMCC research-as-consultant validation" section** showing the per-engagement candidate-level breakdown that the 05-05 review needs. Today renders empty-state ("No PMCC research engagements yet in the observation window") because no PMCC scout cycle with `universe_source: research_on_demand` has fired and completed an engagement yet — the surface is in place for when one does. Confirms the 05-05 review will not require ad-hoc SQL.
- **`LoggerAgent.events_since(ts_iso, limit=5000)`** is now available for any future caller that needs a date-scoped audit fetch (vs `recent_events`'s row-count cap). Internal usage only today; no external API surface.
- **`PMCC_OBSERVATION_PERIOD_START` constant** in `trading_corp/web/routes.py` pins the observation window. After 2026-05-05 the constant can stay (surface remains useful as a longitudinal view) or be moved to a config knob if the period needs to slide.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **PMCC's HITL flow does NOT write `would_have_placed`** — only Otter/Cypher webhook handlers do. The realignment memo phrased the 05-05 criterion as "candidates that produced `would_have_placed` rows" but PMCC's LangGraph flow goes proposed → risk_approved → board_approved → filled (no `would_have_placed` step in between). This view surfaces the actual `proposed_order.status` lifecycle for acted_on candidates instead. Documented in `_build_pmcc_validation_view` docstring. Don't re-litigate; the realignment memo's wording was imprecise on this point and the truth is in the code.
- **The view is purely additive on `/research`.** The pre-existing `Recommendation outcomes` section (per-engagement act/skip counts) is intentionally kept — it's broader (cross-division) while the new section is PMCC-specific candidate-level depth. They aren't redundant; they answer different questions.
- **Join key for division-side rows is `(engagement_id, symbol.upper())`.** If a future audit-write path stops uppercasing the symbol on either side, the join breaks silently. Test `test_full_join_acted_on_skipped_no_outcome` pins the casing.
- **Order status lookup is best-effort.** If the proposed_order id is None on an acted_on row (defensive — shouldn't happen in practice), order_status renders as None and n_board_approved_or_filled doesn't increment. Test `test_acted_on_without_order_id_surfaces_none_status` pins this.

**Latent bugs caught + fixed:** none.

**Verification:**
- Pre-deploy: 370 unit tests pass on local (vs the 360-baseline in prior deploy logs — the +10 are the new `tests/test_pmcc_research_validation_view.py`). 5 pre-existing P2 PMCC-scan failures unchanged.
- md5 3/3 files MATCH between local and prod post-scp.
- PID 109394 → 111560 (restart at 23:02:35 UTC). Service active.
- Port 8000 came up ~10s after restart (Robinhood + Fidelity logins block bind).
- `GET /research` returns 200 in 2.60s. New section renders. Markers present in HTML: `PMCC research-as-consultant validation` (1), `observation since` (1), `decision date 2026-05-05` (1). All 5 scoreboard labels visible (Engagements, Candidates, Acted on, Skipped, Approved/filled). Empty-state copy `No PMCC research engagements yet in the observation window` (1) — expected, no engagements yet.
- `GET /` 200 in 2.58s; `GET /partials/trade-flow` 200.
- Zero new errors in journalctl post-restart aside from the pre-existing yfinance BTC earnings noise + Fidelity bot-block (both called out in earlier deploy logs).

**Inert / dormant on current traffic:**
- The validation section is empty-state today because no PMCC `universe_source: research_on_demand` cycle has produced an engagement yet. It will populate as engagements complete. If on 05-05 it's still empty, that's the answer to the validation question (research isn't being exercised).

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-pmcc-validation-view-20260502-2301; BASE=/home/azureuser/trading_corp;
for f in trading_corp/agents/logger.py trading_corp/web/routes.py trading_corp/web/templates/research.html; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 22:10 UTC — Live trade flow: tile open-state persists across htmx 5s refresh

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board reported the expand-on-click tiles auto-closing
in the browser. Initially attributed to cursor movement; verified via
the 6-second stillness test (open a tile, don't move mouse, count to 6
— tile closes on its own). Confirmed: htmx's `every 5s` outerHTML
refresh of `#trade-flow` rebuilds every `<details>` element fresh,
which discards the `open` attribute. The original Phase A spec
("BACKLOG.md 2026-05-02 — Live trade flow: expand-on-click tiles")
explicitly said "Open tiles will collapse on refresh unless the JS
preserves state. Decision: accept the collapse-on-refresh; if it gets
annoying, add a localStorage open-set keyed by audit_event row id
later." Got annoying within hours of shipping, so building it now.
**Backup tag:** `.pre-tradeflow-state-20260502-2208` (on 3 mutated files;
`trade_flow_state.js` is first-shipment, no backup needed)

**Files deployed (4):**
- `trading_corp/web/data.py` — `trade_flow()` SELECT now includes the
  `audit_event.id` column; the returned dict carries `"id": r["id"]`
  alongside the existing `ts/kind/symbol/side/qty/reason/payload_pretty`
  keys. `id` is the stable primary-key from `audit_event` and is the
  natural choice for keying tile-open state across htmx swaps.
- `trading_corp/web/templates/partials/trade_flow.html` — each tile's
  `<details>` element now carries `data-tile-id="{{ evt.id }}"`. JS
  uses this attribute to recognize the same tile across the htmx swap.
- `trading_corp/web/static/js/trade_flow_state.js` — NEW, ~75 lines of
  vanilla ES6, no new dependencies. Three responsibilities:
    1. Listen for `<details>` toggle events (capture phase, since
       `toggle` doesn't bubble) on `#trade-flow details[data-tile-id]`
       and persist the open-set to `localStorage` under key
       `tradeflow:open-tile-ids`.
    2. Listen for `htmx:afterSwap` events targeting `#trade-flow` and
       re-apply `open` attribute to any `<details data-tile-id>`
       whose ID is in the persisted set.
    3. Apply the same logic on initial `DOMContentLoaded`.
  No bounded-size cleanup of the persisted set: audit_event ids grow
  monotonically; the JSON encoding is small; localStorage caps in the
  5-10MB range per origin. Many years of normal trading before this
  becomes a real concern.
- `trading_corp/web/templates/home.html` — added
  `<script src="/static/js/trade_flow_state.js"></script>` to
  `{% block scripts %}` alongside the existing `equity_chart.js`.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Live trade flow tile open state survives the htmx 5s refresh.**
  Click a tile, walk away, come back later — it's still expanded.
- **State also survives page reload** (localStorage, not just JS memory).

**Notable code changes (callouts a future Claude shouldn't miss):**
- Open tile IDs persist across browser sessions in localStorage —
  someone debugging "why is this tile open before I touched it?"
  should check `localStorage.getItem('tradeflow:open-tile-ids')` in
  the browser console.
- This pattern (data-id attribute + capture-phase toggle listener +
  htmx:afterSwap re-apply) is reusable. If a future panel adds the
  same htmx-refresh-collapses-state issue (e.g. an Engagements log
  on the Research screen if it ever gets htmx polling), copy the
  trade_flow_state.js shape and parameterize on panel ID + selector.
- The Engagements log on `/research` ALSO uses the `<details>` expand
  pattern but does NOT have htmx polling, so it's unaffected by this
  bug and needs no preservation logic. If a future change adds htmx
  polling to that panel, it will need this same treatment.

**Latent bugs caught + fixed:** none.

**Verification:**
- PID 108238 → 109409 confirms restart at 22:10:30 UTC.
- All endpoints 200: `/` (2.9s), `/partials/trade-flow`, `/static/js/trade_flow_state.js`.
- Content checks: `data-tile-id` count in `/partials/trade-flow`
  render = 12 (one per tile). `trade_flow_state.js` referenced once
  in `/`.
- Browser test: Board (Jack) opened a Live trade flow tile, waited
  through one htmx tick without cursor movement, confirmed tile
  stayed open. Click again to close worked. Reload-and-restore
  worked.
- Zero errors in journalctl post-restart aside from the baseline
  paper_trade_replay info-level lines containing the literal string
  `'errors': 0`.

**Inert / dormant on current traffic:** none.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-tradeflow-state-20260502-2208; BASE=/home/azureuser/trading_corp;
for f in trading_corp/web/data.py trading_corp/web/templates/partials/trade_flow.html trading_corp/web/templates/home.html; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
rm \$BASE/trading_corp/web/static/js/trade_flow_state.js;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 21:52 UTC — Strategy file move: divisions/ → strategies/ (vocabulary realignment)

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board vision realignment in-session (2026-05-02). User
stepped back and articulated the model: divisions = brokerage/account
portfolio managers; strategies = how a division operates. Lord Otter and
Market Cypher had been mis-classified as divisions in `agents/divisions/`;
they are strategies running inside the `coinbase_spot` division. File
move + import updates align code with the corrected vocabulary. Pure
rename — zero behavioral change. Local md5 of moved files matched prod's
old `divisions/*.py` md5 byte-for-byte before deploy.
**Backup tag:** `.pre-strategy-rename-20260502-2146` (on 5 mutated files;
the 3 new `strategies/*.py` files are first-shipment, no backup needed)

**Files deployed (8):**
- `trading_corp/agents/strategies/__init__.py` — NEW (empty namespace package)
- `trading_corp/agents/strategies/lord_otter.py` — NEW; identical content
  to the just-deleted `divisions/lord_otter.py` (md5 `3011ed78…`)
- `trading_corp/agents/strategies/market_cypher.py` — NEW; identical
  content to the just-deleted `divisions/market_cypher.py` (md5 `b7e387b6…`)
- `trading_corp/main.py` — both wiring imports flipped from
  `agents.divisions.{lord_otter,market_cypher}` to
  `agents.strategies.{...}`
- `BACKLOG.md` — new top-of-file `## ⏸ PAUSED — Lord Otter + Market
  Cypher feature work` section documenting the maintenance-mode posture
  + 2026-05-02→2026-05-05 PMCC research-as-consultant observation
  period; two prose path references updated
- `config/strategies.yaml` — single comment-line path reference updated
  (cosmetic — values unchanged)
- DELETED: `trading_corp/agents/divisions/lord_otter.py`
- DELETED: `trading_corp/agents/divisions/market_cypher.py`

**Features shipped (load-bearing for future "is X done?" checks):**
- **Strategy modules now live at `trading_corp/agents/strategies/`**, not
  `trading_corp/agents/divisions/`. Any future Claude session searching
  for Otter/Cypher code by path should look at `strategies/`.
- **Logger namespace flipped:** all log lines from these agents now
  prefix with `trading_corp.agents.strategies.lord_otter` /
  `…market_cypher` instead of the old `…divisions.…`. Any external
  log-grep, journalctl filter, or audit query keyed on the old
  namespace will miss new entries.
- **BACKLOG.md ⏸ PAUSED notice is live on prod** — future sessions can
  see the maintenance-mode posture without needing chat context.
- **CLAUDE.md does NOT ship to prod** (it's a Claude-Code-only artifact
  per md5-diff finding); the new "§ Research consultation" rule + the
  module-map split + the divisions-table reframe live on the local
  working tree only. That's correct — CLAUDE.md is loaded per-session
  from local, not from prod's filesystem.

**Notable code changes (callouts a future Claude shouldn't miss):**
- `agents/divisions/` on prod NOW correctly holds only the actual
  divisions: `pmcc_robinhood.py`, `fidelity_options.py`,
  `crypto_futures/`. Plus a pile of `.pre-*` backup tags.
- `pmcc_robinhood.py` and `fidelity_options.py` STILL conflate
  division-level and strategy-level concerns — flagged in CLAUDE.md
  § Known sharp edges as future cleanup once a second strategy on
  Robinhood or Fidelity is needed. Don't refactor speculatively.
- `docs/ARCHITECTURE.md § 1 principle 2` quotes the OLD framing
  ("broker × strategy combo is its own division"). Officially
  superseded by CLAUDE.md's new framing as of 2026-05-02 — separate
  Board-approved ARCHITECTURE.md pass needed to update the source doc.

**Latent bugs caught + fixed:** none.

**Verification:**
- Pre-deploy: 55 unit tests pass on local against the renamed paths
  (`tests/test_lord_otter_bias_persistence.py`, `…webhook_audit_trail.py`,
  `…webhooks_return_fast.py`, etc.) — confirmed import path works.
- Post-deploy: PID 107062 → 108223 (restart at 21:52:26 UTC).
- `journalctl -u trading-corp --since '3 min ago'` shows
  `INFO trading_corp.agents.strategies.lord_otter: LordOtterAgent
  reloaded config: enabled=True auto_execute=False symbols=['BTC/USD']
  arming_window_bars=5` and the parallel Cypher line at the new
  namespace — confirms imports succeeded and agents loaded.
- Zero `ImportError` / `ModuleNotFoundError` / `agents.divisions.lord_otter`
  / `agents.divisions.market_cypher` lines in journalctl post-restart.
- Dashboard `/` returns 200 in 2.7s; `/research` 200; `/partials/trade-flow`
  200 in 3ms.

**Inert / dormant on current traffic:**
- The Otter+Cypher strategy modules run as before (paper-mode,
  `auto_execute: false`); no new feature work landed in this deploy.
  The pause is an organizational stance, not a code-level mute.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-strategy-rename-20260502-2146; BASE=/home/azureuser/trading_corp;
mv \$BASE/trading_corp/main.py\$TAG \$BASE/trading_corp/main.py;
mv \$BASE/BACKLOG.md\$TAG \$BASE/BACKLOG.md;
mv \$BASE/config/strategies.yaml\$TAG \$BASE/config/strategies.yaml;
mv \$BASE/trading_corp/agents/divisions/lord_otter.py\$TAG \$BASE/trading_corp/agents/divisions/lord_otter.py;
mv \$BASE/trading_corp/agents/divisions/market_cypher.py\$TAG \$BASE/trading_corp/agents/divisions/market_cypher.py;
rm -rf \$BASE/trading_corp/agents/strategies/;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 21:44 UTC — Dashboard polish: expand-on-click for Engagements log + Live trade flow + Engagement-latency column rename

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board picked one of the new P5 UI polish items as a
warmup ("Engagements log expand-on-click" → routes.py + research.html);
extended to the symmetric trade-flow tile expand (data.py +
partials/trade_flow.html); the previously-PARTIALLY-DONE
"Engagement-latency panel column rename" was sitting on local working
tree and rode along.
**Backup tag:** `.pre-dashboard-polish-20260502-2143` (on all 4 files)

**Files deployed (4):**
- `trading_corp/web/routes.py` — added `import json`; new
  `"payload_pretty": json.dumps(payload, indent=2, default=str,
  sort_keys=True)` key on the `engagement_log` dict in
  `_build_research_view` (line 1044). `sort_keys=True` so repeated
  `kind`s render with stable field order across reloads.
- `trading_corp/web/data.py` — same `payload_pretty` key added to
  `trade_flow()` dict (line 932). `json` already imported.
- `trading_corp/web/templates/research.html` — engagement_log row
  `<div>` converted to `<details class="px-4 py-2 group">` /
  `<summary>` matching the existing thesis-library pattern. Body is
  a `<pre>` with `whitespace-pre overflow-x-auto bg-pane-2/40 border
  border-edge` so wide payloads get a horizontal scrollbar instead of
  wrapping. Also: Engagement-latency panel column headers humanized
  (`product_type`→`Product`, `asset_class`→`Asset Class`, `N`→`Samples`,
  `P50 (s)`→`Median (s)`, `week`→`Week`) — the previously-PARTIALLY-DONE
  rename.
- `trading_corp/web/templates/partials/trade_flow.html` — tile `<div>`
  converted to `<details>` / `<summary>` with `<pre>` body. Default
  browser disclosure marker suppressed via Tailwind arbitrary variant
  (`list-none [&::-webkit-details-marker]:hidden` on the `<summary>`)
  so the only indicator is the custom `▶` chevron rotating with
  `group-open:rotate-90`. Differs intentionally from the
  Engagements-log row pattern (which kept the dual marker to match
  thesis-library precedent on the same screen) — tile UI looked
  weirder with a stray default triangle inside a styled box.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Engagement-log rows on `/research` are click-to-expand inline
  accordions** showing the full `audit_event.payload_json` pretty-
  printed. Multiple rows can be open at once. Backlog item
  "P5 — Research screen: expand-on-click rows in Engagements log
  (PARTIALLY DONE → SHIPPED)".
- **Live trade flow tiles on `/` are click-to-expand** with the same
  pattern. Backlog item "P5 — Live trade flow: expand-on-click tiles
  (PARTIALLY DONE → SHIPPED)". Note: tile collapses on the htmx 5s
  refresh tick — explicitly accepted per spec; localStorage
  state-preservation is out of scope.
- **Engagement-latency panel column headers humanized** for
  Board-facing readability. Backlog item "P5 — Research screen:
  humanize Engagement latency panel column labels (PARTIALLY DONE →
  SHIPPED)".

**Notable code changes (callouts a future Claude shouldn't miss):**
- The two expand patterns differ on the disclosure-marker handling
  (engagement-log keeps the dual marker; trade-flow suppresses it).
  This is intentional and called out in BACKLOG.md. Future polish
  pass: normalize both with a site-wide CSS rule.
- `payload_pretty` key adds modest bandwidth to the SSR responses —
  120 engagements × ~typical payload + 20 trade-flow events × payload.
  Verified comfortably under any sane SSR ceiling; no perf regression
  observed.

**Latent bugs caught + fixed:** none.

**Verification:**
- PID 105xxx → 107062 (restart at 21:44:05 UTC).
- `/research` returns 200 in 2.7s; `/` 200 in 2.6s; `/partials/trade-flow`
  200 in 3ms.
- Content check: `curl http://127.0.0.1:8000/partials/trade-flow | grep -c
  'payload_pretty\|group-open:rotate-90'` → 12 (one per tile pre-htmx-tick).
- Content check: `curl http://127.0.0.1:8000/research | grep -c
  '<details class="px-4 py-2 group"'` → 142 (engagement-log rows + thesis
  library + position-context bundles all use the pattern).
- No new errors in journalctl post-restart aside from the baseline
  Fidelity bot-block + yfinance BTC earnings noise (both pre-existing).

**Inert / dormant on current traffic:** none.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-dashboard-polish-20260502-2143; BASE=/home/azureuser/trading_corp;
for f in trading_corp/web/templates/research.html trading_corp/web/templates/partials/trade_flow.html trading_corp/web/routes.py trading_corp/web/data.py; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 16:01 UTC — Webhook handlers refactored to return-fast (TV 10s-timeout fix)

**Commits:** local-only (uncommitted as of deploy time)
**Triggered by:** Board reported overnight TV alerts showing
"Webhook delivery failed — request took too long and timed out" on
the 04:00 UTC 4h-bar Cypher signals. Investigation found the webhook
handlers run the broker snapshot + agent.on_alert + research-firm
consult inline before returning HTTP 200, and the research consult
alone can take 12-30s on a multi-expert engagement (verified during
today's 15:31 UTC replay-endpoint test). With auto_execute=false on
both Otter and Cypher today, no QUALIFIED bull alert has hit this
path yet — but the architecture was a latent timeout bomb.
**Backup tag:** `.pre-returnfast-20260502` (on 1 modified file)

**Files deployed (1):**
- `trading_corp/web/webhooks.py` — both handlers (`lord_otter_webhook` + `market_cypher_webhook`) refactored. Synchronous phase now does only validation + the `webhook_received` audit, then dispatches the heavy processing onto a FastAPI `BackgroundTasks` and returns HTTP 200 with `{"status":"accepted","signal":...,"symbol":...}` in well under 200ms. Background processing logic extracted into module-level `_process_lord_otter_alert(...)` and `_process_market_cypher_alert(...)` async helpers, each wrapped in a catch-all that writes an `agent_error` audit row tagged with `phase=background_processing` and Telegram-notifies the Board.

**Features shipped:**
- TradingView's 10s webhook timeout is no longer load-bearing for any downstream work. Even if research consult takes 30s, risk gate stalls, or broker snapshot hangs, TV gets HTTP 200 in <200ms.
- Audit chain unchanged in shape but split across the sync/background boundary: `webhook_received` lands inline (so we have a record even if the background crashes), all subsequent decision rows (`alert_ignored`, `risk_rejected`, `would_have_placed`, `filled`, `execution_error`, etc.) land in the background task.
- New `agent_error` row variant with `phase=background_processing` flag — distinguishes a crash inside the new background helper from the older inline-handler `agent_error` cases.
- Telegram catch-all on background crashes — silent failures impossible.

**Notable code changes:**
- HTTP response shape changed for both handlers. Pre-refactor: `{"status":"would_have_placed", "order_id":"...", "decision":"..."}` (varied per outcome). Post-refactor: uniform `{"status":"accepted", "signal":..., "symbol":...}`. Anything observing TV-callback bodies (we don't, TV doesn't read response bodies) would see the change. The audit log + Telegram surface remain the source of truth, unchanged.
- Existing test `test_push_back_skips_order_and_notifies_board` updated to assert on audit + Telegram side-effects instead of the now-uniform body. The contract that risk_agent isn't called on push_back is preserved — that assertion still passes.
- `test_no_research_firm_falls_through_to_existing_flow` — body assertion changed to `body["status"] == "accepted"`. Negative assertions ("no research_* audit rows", "Telegram NOT called") still hold.
- New tests: `tests/test_webhooks_return_fast.py` (5 tests) pin: (a) HTTP body uniformly "accepted" on valid alerts, (b) webhook_received audit lands during sync phase, (c) outcome audits land in background, (d) background-task crash writes agent_error + Telegram, (e) Cypher handler has same contract.

**Latent bugs caught:** none specific to this refactor — the underlying issue (event-loop-blocking inline processing) was the bug being fixed.

**Verification:**
- PID 102701 → 105xxx after restart, status active.
- md5sum 1/1 file MATCH between local and prod post-scp.
- Live end-to-end: `POST https://trading.jacksumner.com/webhook/tradingview/market-cypher` with this morning's failed alert payload returned `{"status":"accepted","signal":"mc_a_red_diamond","symbol":"BTC/USD"}` in **0.119s** through the full Caddy → FastAPI stack.
- Audit chain post-test:
  - `webhook_received` at 16:00:33 UTC (sync phase)
  - `alert_ignored` at 16:00:34 UTC (background task, 1s later — for the bear-no-position branch)
- 360 tests passing locally (5 pre-existing P2 PMCC failures unchanged).

**Inert / dormant on current traffic:**
- The catch-all background-crash audit + Telegram path is wired but should never fire on healthy traffic. Will surface only on actual exceptions.
- Nothing else inert — both Otter and Cypher webhooks are receiving live traffic; any TV alert exercises the new code path within minutes.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-returnfast-20260502; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/webhooks.py.\$TAG \$BASE/trading_corp/web/webhooks.py; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 15:31 UTC — Manual research-firm replay endpoint + dashboard button (on-demand consult on past TV signals)

**Commits:** local-only (uncommitted as of deploy time)
**Triggered by:** session conversation — Board wanted to see how the
research firm consult code path would have responded to this morning's
10:54am ET `money_bag_top` alert. Today's filter rejects bear signals
when no position is held, so the consult never fires on those rows.
This adds an opt-in replay path so the existing engagement code can be
exercised against historical signals on demand.
**Backup tag:** `.pre-replay-research-20260502` (on 3 modified files)

**Files deployed (4):**
- `trading_corp/web/data.py` — `_query_division_activity` now exposes audit_event `id` + `signal` on each row dict so the dashboard template can build the per-row replay button.
- `trading_corp/web/routes.py` — new `POST /audit/{audit_id}/replay-research` endpoint. Validates kind is in {webhook_received, alert_ignored, would_have_placed}, calls signal_replay, returns an htmx-swappable HTML fragment (verdict pill + colored rationale).
- `trading_corp/web/templates/division.html` — Recent Activity rows now render a "Send to research →" button on signal-shaped events. htmx POST + inline result swap underneath. Behind Authelia like the rest of the dashboard.
- `trading_corp/agents/research/signal_replay.py` (new) — `synthesize_order_from_payload(payload, audit_event_id=...)` reconstructs a ProposedOrder shape from a TV webhook payload, marks `extra.synthetic=True` so the firm + downstream consumers know it isn't from the live agent path. `replay_signal_research(audit_row, ...)` routes through the existing `consult_research_for_trade_confirmation` helper, writes a `research_replay_completed` audit row tagged with the source audit id. 60s timeout (vs 8s on live path — replay isn't on a live order path, no rush).

**Features shipped:**
- Per-row "Send to research →" button on the per-division Recent Activity panel for `webhook_received` / `alert_ignored` / `would_have_placed` rows. Click → htmx POST → inline result swap with verdict pill (green CONFIRM, yellow CONDITIONAL/TIMEOUT, red PUSH_BACK/ERROR) + the firm's rationale beneath.
- Audit trail: `research_replay_completed` (or `research_replay_failed` on synthesis/consult failure) captures source_audit_event_id, verdict_kind, decision, rationale (truncated 500 char), signal, symbol, alert_price/time. Surfaces on the existing Research screen Engagements log alongside the engagement's other rows.
- Side inference: bear-leaning signal-name fragments (`bear`, `top`, `red_diamond`, `sell_circle`, `spoon_bear`, `money_bag_top`) → side='sell'. Everything else → 'buy'. Synthetic order's qty is fixed 0.01 placeholder — research firm reasons about setup, not size.

**Notable code changes:**
- Discovered + fixed during deploy: `EngagementSpec.requesting_division` is misnamed — it actually expects the strategy/agent slug (`lord_otter`/`market_cypher`), not the broker-account division slug. First request returned `ValidationError` because we passed `coinbase_spot`. Fix: pull `payload.strategy` first, fall back to `audit_row.actor`. **The misnaming itself is a pre-existing schema oddity worth a separate cleanup item** — naming the field `requesting_strategy` (or `requesting_agent`) would prevent future foot-shoots.
- Discovered + fixed: 8s default timeout on `consult_research_for_trade_confirmation` is wired for the live webhook path where speed matters; multi-expert engagements typically take 15-30s end-to-end. Replay isn't on a live path so we pass `timeout_s=60.0` explicitly. First successful replay completed in 12.5s.

**Latent bugs caught + fixed:** see "Notable code changes" above. Both surfaced during the live deploy validation of the new endpoint.

**Verification:**
- PID 102701 → 105xxx (after the timeout-bump scp+restart), status active.
- md5 4/4 files MATCH between local and prod post-scp (final pass after both fixes).
- 19 new tests in `tests/test_signal_replay.py`, all green. Full suite 355 passed (5 pre-existing P2 PMCC failures unchanged).
- Live end-to-end exercise: `POST /audit/614/replay-research` (the 14:54 UTC `money_bag_top` from this morning) returned `verdict=PUSH_BACK decision=skip` in 12.5s. Firm rationale captures (a) it's a synthetic-replay signal, (b) macro neutral with VIX ~17, (c) NFP/FOMC/CPI risk in window, (d) two of three expert dimensions unobserved → insufficient evidence. Audit trail shows full engagement chain: research_engagement_started → research_data_fetch_attempted → research_position_context_emitted → research_expert_completed × N → research_expert_refused × N → research_trade_confirmation_emitted → research_tradeconf_pushback_acted_on → research_replay_completed.

**Inert / dormant on current traffic:**
- The button only renders on signal-shaped audit rows (webhook_received / alert_ignored / would_have_placed). PMCC, Fidelity, and other audit rows don't show the button.
- Not on a live order path. Cannot affect order placement under any code path. Even on a `confirm` verdict, the synthetic order has `extra.synthetic=True` and is never sent to data_exec — the consult result is purely informational.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-replay-research-20260502; BASE=/home/azureuser/trading_corp; \
for f in trading_corp/web/data.py \
         trading_corp/web/routes.py \
         trading_corp/web/templates/division.html; do \
  mv \$BASE/\$f.\$TAG \$BASE/\$f; \
done; \
rm -f \$BASE/trading_corp/agents/research/signal_replay.py; \
sudo systemctl restart trading-corp
"
# research_replay_completed / research_replay_failed audit rows stay
# in the DB after rollback (harmless — just historical records).
```

---

## 2026-05-02 14:56 UTC — would_have_placed Phase C (replay job + dashboard panel) + 0-DTE Terminal-DTE Override calendar refactor + P1 cycle-continuity

**Commits:** local-only (uncommitted as of deploy time)
**Triggered by:** "lets do 0 dte backlog item and then deploy" — bundled
Phase C of the would_have_placed enrichment with the P0 0-DTE
Terminal-DTE Override refactor (and the P1 cycle-continuity release
folded in).
**Backup tag:** `.pre-phaseC-0dte-20260502` (on 6 modified files)
**New venv dep:** `pandas-market-calendars 5.3.2` (transitive:
exchange-calendars 4.13.2, korean_lunar_calendar 0.3.1, pyluach 2.3.0,
toolz 1.1.0)

**Files deployed (8):**
- `requirements.txt` — added pandas-market-calendars>=4.4.0 (NYSE schedule for half-day / holiday-aware 0-DTE deadline gates).
- `config/strategies.yaml` — new `robinhood_pmcc.zero_dte` block: release_offset_min=60, hard_deadline_offset_min=30, cycle_continuity_extrinsic_threshold=0.15. All three are operationally tunable without a code deploy.
- `trading_corp/main.py` — wired the paper_trade_replay loop alongside the PMCC scan scheduler. Startup catch-up fires before the loop is spawned (mark_pre_phase_a_rows + one immediate replay tick). Loop runs every 900s, cancelled on shutdown. Log lines use f-strings to bypass the RedactingFilter dict-mangling (a separate pre-existing harness bug).
- `trading_corp/web/data.py` — new `paper_trade_summary(db_url, division)` returns 7d/30d/all-time totals (wins/losses/expired/open) + simulated $ P&L per window. pre_phase_a rows excluded from win-rate math, surfaced separately via `n_pre_phase_a`. DivisionViewSnapshot grew a `paper_trade_summary` field.
- `trading_corp/web/templates/division.html` — new "Paper-trade win rate" section above Recent Activity. 3 cards (7d/30d/all) with win % colored green/red, W/L/E counts, sim P&L. Hidden when `totals.all.n == 0` so PMCC/Fidelity divisions don't show empty cards.
- `trading_corp/agents/divisions/pmcc_robinhood.py` — `_terminal_dte_time_release` refactored from hardcoded 15:00/15:30 ET clock to NYSE-calendar-aware close-relative offsets. Added cycle-continuity P1 release path (mark <= threshold AND short_leg_dte == 0 → roll_short, regardless of time). Helper accepts an optional `calendar=` kwarg for test injection. Prompt-rule docstrings (Rules 7 + 4) updated to describe both release paths.
- `trading_corp/agents/paper_trade_replay.py` (new) — walk-forward classifier with conservative same-bar tie-rule (both TP and SL hit in one 1m bar → assume LOSS). Public sync entry, async-native variant, asyncio loop spawner. Default Coinbase ccxt fetcher (paginated, no auth) for OHLC. mark_pre_phase_a_rows helper for the startup catch-up.
- `trading_corp/utils/market_hours.py` (new) — MarketHoursCalendar wrapper around pandas_market_calendars. Memoized per-date close lookups (lru_cache 2048). Graceful fallback when pmcal import fails (every weekday closes at 16:00 ET, weekends closed) — degraded but functional, logged once per process.

**Features shipped:**
- **Phase C of would_have_placed enrichment.** Background replay loop walks paper_trade_record rows where result IS NULL, fetches 1m OHLC bars from Coinbase via ccxt, classifies each row as win/loss/expired (with conservative same-bar tie = loss). Writes result_*, actual_pnl_dollars, actual_r_multiple, bars_to_resolution. 15-min interval; restart triggers immediate catch-up tick.
- **Per-division "Paper-trade win rate" dashboard panel** on /division/{slug}. 3-card layout (7d/30d/all-time) with win rate %, W/L/E counts, sim P&L. Auto-hidden on divisions with no rows. Shows pre-Phase-A row count as a footnote when relevant (5 rows on coinbase_spot today, marked pre_phase_a at startup).
- **0-DTE Terminal-DTE Override is now NYSE-calendar-aware.** Hardcoded 15:00 ET / 15:30 ET thresholds replaced with `close - release_offset_min` / `close - hard_deadline_offset_min` lookups against the NYSE schedule. Half-day closes (e.g. 13:00 ET on day after Thanksgiving) correctly slide the deadline to 12:00 / 12:30 ET. Friday-holiday rotations land the deadline on Thursday's close. Defaults match prior 60/30 minute behaviour.
- **P1 cycle-continuity release.** When a 0-DTE short's mark has decayed to ≤$0.15/share (config knob), force roll_short regardless of time — captures next-cycle premium at today's IV, eliminates post-expiry coverage gap. Operates independently of the time gate; both check short_leg_dte == 0 first.

**Notable code changes:**
- f-string log-formatting in main.py + paper_trade_replay.py for the replay-counts dicts. The harness's RedactingFilter rewrites dict log args into a tuple of keys, which then fails `%s` formatting with TypeError. f-strings sidestep this. **Filing a separate observation:** the RedactingFilter's dict-handling is a pre-existing bug worth a small backlog item — affects any future caller passing a dict via %-style logging. Not a regression, just exposed by Phase C.
- `MarketHoursCalendar.close_time_et` returns tz-aware ET datetimes via `.astimezone(ET)` so DST arithmetic stays correct under timedelta subtraction.
- `_terminal_dte_time_release` now takes optional `calendar=` for test injection. Production path uses `default_calendar()` module-level singleton to avoid re-loading the NYSE schedule per call.
- Test refactor (`tests/test_pmcc_logic.py:_FakeCalendar`) — simple test double for the calendar so existing 7 tests + DST test work without depending on pandas_market_calendars being installed in CI.

**Latent bugs caught + fixed:**
- The "Logging error" `TypeError: not all arguments converted` from the first restart at 14:51 UTC was caught immediately, fix scp'd at 14:55 UTC, restart at 14:56 UTC clean. The replay loop was actually functioning during the broken-logging window — only the count summary failed to render.

**Verification:**
- PID 87416 (Phase B running) → 99920 (first attempt with logging bug) → 100824 (clean fix), status active.
- md5sum 8/8 files MATCH between local and prod post-scp.
- pandas_market_calendars import smoke test on prod: `default_calendar().close_time_et(date(2024, 7, 3))` returns `2026-07-03 13:00:00-04:00` — half-day correctly resolved.
- paper_trade_replay startup catch-up: `{'scanned': 0, 'resolved_win': 0, 'resolved_loss': 0, 'resolved_expired': 0, 'marked_pre_phase_a': 0, 'errors': 0}` — note: marks=0 because the explicit mark_pre_phase_a_rows call before replay_pending_paper_trades_async already marked the 5 historical rows (idempotent inner mark sees 0).
- Database row distribution post-restart: `pre_phase_a: 5` (the 4 Otter + 1 Cypher backfilled rows from Phase B). All have NULL stop or NULL tp_price (Phase A wasn't shipped at their alert times), so they correctly fell through to pre_phase_a.
- Dashboard probe `GET /division/coinbase_spot` returns HTTP 200 with the new "Paper-trade win rate" section rendering and the "5 pre-Phase-A row(s) excluded" footnote present.
- 336 tests passing locally. 5 pre-existing P2 PMCC liquidity-gate failures unchanged (BACKLOG).

**Inert / dormant on current traffic:**
- Replay loop sees no rows to actually classify yet — every paper_trade_record row landed pre-Phase-A. Once new TV-driven `would_have_placed` events fire post-Phase-A (auto-execute is `false` on both Otter and Cypher, so every alert lands here), the loop will start classifying them within 15 min.
- 0-DTE gates only fire when a PMCC short reaches 0 DTE. PMCC scan path is daily 8:30-9:25 ET; the time-of-day gate path will exercise on the next 0-DTE expiration day with an active short. Cycle-continuity P1 path will exercise as soon as a 0-DTE short's mark decays to ≤$0.15/share — could be the same day depending on IV.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-phaseC-0dte-20260502; BASE=/home/azureuser/trading_corp; \
for f in requirements.txt \
         config/strategies.yaml \
         trading_corp/main.py \
         trading_corp/web/data.py \
         trading_corp/web/templates/division.html \
         trading_corp/agents/divisions/pmcc_robinhood.py; do \
  mv \$BASE/\$f.\$TAG \$BASE/\$f; \
done; \
rm -f \$BASE/trading_corp/agents/paper_trade_replay.py \
      \$BASE/trading_corp/utils/market_hours.py; \
sudo systemctl restart trading-corp
"
# Note: pandas_market_calendars + transitive deps stay installed; harmless,
# can be left or `pip uninstall` if the rollback is permanent.
```

---

## 2026-05-02 05:45 UTC — would_have_placed enrichment Phase B (paper_trade_record table + write-on-emit)

**Commits:** local-only (uncommitted as of deploy time)
**Triggered by:** "execute the plan 1-5" — bundled Phase B of the
would_have_placed enrichment (BACKLOG.md 2026-05-01 P1 entry, Phase B sub-task)
**Backup tag:** `.pre-phaseB-20260502` (on 6 modified files)

**Files deployed (7):**
- `config/strategies.yaml` — added strategy-global `max_hold_seconds`: lord_otter=86400 (24h), market_cypher=604800 (7d). Frozen onto each paper_trade_record at write time so config edits don't retroactively alter past trades.
- `trading_corp/agents/divisions/lord_otter.py` — new `max_hold_seconds` property reading from yaml with 86400 default.
- `trading_corp/agents/divisions/market_cypher.py` — new `max_hold_seconds` property with 604800 default.
- `trading_corp/persistence/db.py` — new `paper_trade_record` table + 2 indexes (`ix_paper_trade_record_strategy_ts`, `ix_paper_trade_record_result`); new `insert_paper_trade_record(record_dict, db_url)` helper using INSERT OR IGNORE on order_id.
- `trading_corp/persistence/models.py` — new `PaperTradeRecord` dataclass + `to_db_row()` + `from_order(order, *, strategy, division, max_hold_seconds)` factory that pulls Phase A trade-card fields out of order.extra and computes expected_loss / rr_ratio.
- `trading_corp/web/webhooks.py` — new module-private `_record_paper_trade(deps, order, strategy, agent)` helper; called inside both Otter and Cypher `would_have_placed` branches (after audit log_event, before Telegram push). try/except wrapped: a write failure logs a WARNING but does NOT break the order flow — audit_event remains source of truth.
- `scripts/backfill_paper_trade_record.py` (new) — idempotent one-shot script that walks `audit_event WHERE kind='would_have_placed'`, joins to `proposed_order.extra_json`, and inserts a paper_trade_record per row. Safe to re-run (INSERT OR IGNORE).

**Features shipped:**
- New SQLite table `paper_trade_record` written on every `would_have_placed` emission. Structured columns for trade specs (entry_reference_price, stop_price, tp_price, tp_r_multiple, expected_loss, expected_gain, rr_ratio) + Phase C-anticipating result columns (result, result_ts, result_price, actual_pnl_dollars, actual_r_multiple, bars_to_resolution) that stay NULL until the future replay job populates them.
- One-time backfill of historical paper trades: 5 pre-deploy rows backfilled (4 lord_otter, 1 market_cypher; first ts 2026-04-30 17:41 UTC, last ts 2026-05-01 02:06 UTC). All pre-Phase-A historical rows have NULL trade-spec fields where Phase A would populate; that's expected and the future replay job will skip them.
- `max_hold_seconds` strategy-global config knob frozen per row at write time. Phase C replay job will use this to decide when to mark `result='expired'` for trades that didn't hit either TP or SL within the window.

**Notable code changes:**
- Schema migration is automatic via `init_db()` `CREATE TABLE IF NOT EXISTS` — service restart applies it. No manual SQL run.
- Failure mode for the new write path is fail-open: try/except in `_record_paper_trade` so a paper_trade_record write error does NOT abort the audit-log + Telegram push. The audit_event row is still source of truth (per CLAUDE.md §1 "audit log writes BEFORE every decision branch").
- INSERT OR IGNORE keying on order_id means the inline write-on-emit path and the backfill script can never collide. Whichever wrote first wins.
- BACKLOG.md entry updated in-tree with Phase A ✅ shipped / Phase B ✅ in-tree-as-of-2026-05-02 / Phase C ⬜ pending status header. (BACKLOG.md is dev-only, not deployed.)

**Latent bugs caught + fixed:**
- None.

**Verification:**
- PID 82701 → 87416, status active
- md5sum 7/7 files MATCH between local and prod post-scp
- `.schema paper_trade_record` returns the full CREATE TABLE + 2 indexes
- Backfill ran clean: `WROTE: scanned=5 inserted=5 skipped_no_order_id=0`
- Row inspection: 4 lord_otter rows + 1 market_cypher row, max_hold_seconds populated correctly per strategy (86400 / 604800), stop_price + expected_loss populated on rows where the order's `extra` had `max_dollar_risk` (pre-Phase-A path), tp_price/expected_gain NULL across all (pre-Phase-A — no TP fields had been written yet at those alert times).
- Dashboard probes: `GET /` HTTP 200, `GET /research` HTTP 200
- 11 new tests in `tests/test_paper_trade_record.py` green; full suite 305 passed (5 pre-existing P2 PMCC scan failures, called out in BACKLOG.md, unchanged)
- Service log post-restart: only baseline errors (Fidelity bot-block + yfinance "No earnings dates for BTC/USD") — same baseline noted in prior deploy_log entries; no Phase B-introduced errors

**Inert / dormant on current traffic:**
- The new `_record_paper_trade` write hook only fires on `would_have_placed` branches (auto_execute=false). Both Otter and Cypher are auto_execute=false today, so every alert hits the new path. NOT inert — exercising on live traffic immediately.
- Phase C replay job code is NOT deployed; result columns will stay NULL until that lands. No-op for now.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-phaseB-20260502; BASE=/home/azureuser/trading_corp; \
for f in config/strategies.yaml \
         trading_corp/agents/divisions/lord_otter.py \
         trading_corp/agents/divisions/market_cypher.py \
         trading_corp/persistence/db.py \
         trading_corp/persistence/models.py \
         trading_corp/web/webhooks.py; do \
  mv \$BASE/\$f.\$TAG \$BASE/\$f; \
done; \
rm -f \$BASE/scripts/backfill_paper_trade_record.py; \
sudo systemctl restart trading-corp
"
# Note: rolling back the schema (DROP TABLE paper_trade_record) is
# OPTIONAL — the table will simply sit unused on the rolled-back code.
# Only drop it if you're certain you won't be replaying these rows.
```

---

## 2026-05-02 03:30 UTC — Research firm Phase 1f

**Commits:** `ce15602`, `d61b7ec`
**Triggered by:** "deploy" instruction after Phase 1f UAT passed (22 checks incl. real-LLM smoke)
**Backup tag:** `.pre-1f-20260502-0030` (on 7 modified files)

**Files deployed (13):**
- `trading_corp/agents/llm.py` — _TEMPERATURE_REJECTING_MODELS set; skip temperature for Opus 4.7
- `trading_corp/agents/logger.py` — log_event returns cur.lastrowid
- `trading_corp/agents/research/state.py` — debate_audit_row_id field on EngagementState
- `trading_corp/agents/research/graph.py` — debate_gate node + threading
- `trading_corp/agents/research/synthesis/thesis.py` — debate threading + always-insert driver
- `trading_corp/agents/research/synthesis/position_context.py` — debate threading + risk_flag surface
- `trading_corp/agents/research/synthesis/trade_confirmation.py` — debate threading + tags audit_row_id
- `trading_corp/agents/research/debate_gate.py` (new) — variance/disagreement gate
- `trading_corp/agents/research/experts/debate/__init__.py` (new)
- `trading_corp/agents/research/experts/debate/_base.py` (new) — shared bull/bear runner
- `trading_corp/agents/research/experts/debate/bull.py` (new) — Sonnet
- `trading_corp/agents/research/experts/debate/bear.py` (new) — Sonnet
- `trading_corp/agents/research/experts/debate/judge.py` (new) — Opus, scores quality only

**Features shipped:**
- Bull/bear/judge debate round fires on single-symbol engagements where
  expert variance >= 0.25 OR >= 2 experts disagree on directional_lean
- Two new audit kinds visible in dashboard: `research_debate_invoked`,
  `research_debate_completed`
- Debate context flows into Thesis key_drivers ("debate (gate fired): ..."),
  PositionContext risk_flags ("debate fired: ..."), and TradeConfirmation
  via debate_audit_row_id
- v3 design feature-complete on all 4 product types

**Notable code changes:**
- `agents/llm.py` `_TEMPERATURE_REJECTING_MODELS = {"claude-opus-4-7"}` — extend this set as Anthropic deprecates temperature on more models
- `agents/logger.py` `log_event` signature changed `None` -> `int | None` — backwards-compat for callers that ignore the return

**Latent bugs caught + fixed:**
- Opus 4.7 deprecated `temperature` parameter; judge silently fell back to placeholder scores on every firing pre-fix
- `log_event` always returned None, so `debate_audit_row_id` could never be a real id

**Verification:**
- PID 78397 -> 82701, status active
- 2 PositionContext primes completed end-to-end (Otter 4h + Cypher 24h)
- Graph compiles to 15 nodes including `debate_gate`
- /research dashboard probe HTTP 200, sections present
- 5 Fidelity bot-block + 1 yfinance no-earnings line are baseline (not regressions)

**Inert / dormant on current traffic:**
- Debate gate is on disk + exercising itself but **never fires** in current
  prod traffic. Crypto-only PositionContext engagements (Otter+Cypher prime
  BTC/USD on every restart) have only macro as a valid expert (sentiment
  refuses on crypto). Single-voice panel can't fire. The gate will start
  firing when (a) Otter/Cypher get equity exposure, (b) Board fires a
  Thesis on equity, or (c) PMCC scout TradeConfirmation engagements run
  with multiple experts.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-1f-20260502-0030; BASE=/home/azureuser/trading_corp; \
for f in trading_corp/agents/llm.py trading_corp/agents/logger.py trading_corp/agents/research/state.py trading_corp/agents/research/graph.py trading_corp/agents/research/synthesis/thesis.py trading_corp/agents/research/synthesis/position_context.py trading_corp/agents/research/synthesis/trade_confirmation.py; do \
  mv \$BASE/\$f.\$TAG \$BASE/\$f; \
done; \
rm -rf \$BASE/trading_corp/agents/research/experts/debate \
       \$BASE/trading_corp/agents/research/debate_gate.py
"
```

---

## 2026-05-02 02:13 UTC — routes.py hotfix (research_data_fetch_attempted)

**Commits:** `c29713a`
**Triggered by:** Phase 1d/1e dashboard 500 error post-restart — _summary_for_event
sliced `payload.get('error', '')[:60]` returning `None[:60]` when the key existed
with value None.
**Backup tag:** `.pre-hotfix-fetch-err-20260501-2330`

**Files deployed (1):**
- `trading_corp/web/routes.py` — defensive `(payload.get("error") or "")[:60]`

**Features shipped:**
- Dashboard /research stops returning HTTP 500 when audit log contains
  `research_data_fetch_attempted` rows with `error=None` payloads.
  These rows started landing because Phase 1d's PositionContext prime
  fired real macro-expert engagements that wrote them.

**Verification:**
- Service restart picked up the fix (FastAPI binds routes at startup;
  no hot-reload available)
- /research returns 200 with PositionContext audit trail rendering

---

## 2026-05-01 23:30 UTC — Research firm Phase 1d + 1e bundle

**Commits:** `b145d82` (Phase 1d), `1cb7e70` + `5be2588` (Phase 1e graph + division halves)
**Triggered by:** "deploy" instruction after Phase 1e UAT passed (real-LLM smoke included)
**Backup tag:** `.pre-1d1e-20260501-2330` (on 9 modified files)

**Files deployed (14):**
- `trading_corp/agents/research/graph.py` — Layer 1 + new emit nodes
- `trading_corp/agents/research/schemas.py` — new audit-kind constants
- `trading_corp/agents/divisions/lord_otter.py` — _fetch_position_context, on-alert hook, configured_symbols, last_position_context, **TP fields in `_build_order` (Phase A scaffolding)**, division consult call
- `trading_corp/agents/divisions/market_cypher.py` — same shape (24h horizon), TP fields, consult call
- `trading_corp/main.py` — startup-of-day prime task
- `trading_corp/web/webhooks.py` — TradeConfirmation consult call between on_alert + risk gate; **Phase A `_format_trade_card` shared renderer for would_have_placed pushes**
- `trading_corp/web/routes.py` — position_contexts view
- `trading_corp/web/templates/research.html` — collapsible PositionContext audit trail
- `config/research.yaml` — `trade_confirmation` block (timeout + kill switch)
- `trading_corp/agents/research/synthesis/position_context.py` (new)
- `trading_corp/agents/research/synthesis/trade_confirmation.py` (new)
- `trading_corp/agents/research/position_context_cache.py` (new)
- `trading_corp/agents/research/prime.py` (new)
- `trading_corp/agents/research/trade_confirmation_consult.py` (new)

**Features shipped:**
- PositionContext engagement type emits via the graph + audit row +
  dashboard view
- Pre-emptive cache for PositionContext (TTL-gated agent_state rows,
  per-division horizons in research.yaml)
- Startup-of-day prime task on every restart populates the cache for
  configured symbols
- Otter + Cypher consume cached PositionContext on alert
  (state.last_position_context; not yet gating behavior)
- TradeConfirmation consult on every Otter/Cypher webhook between
  agent.on_alert and the risk gate (8s hard timeout, fail-open)
- push_back verdict triggers Telegram notify with rationale; conditional
  applies SuggestedModifications transparently
- **Phase A enrichment of would_have_placed pushes** — `_format_trade_card`
  shared renderer outputs full trade card (entry, stop, take-profit,
  R:R, expected P&L) for both Otter and Cypher
- TP fields populate in order.extra: take_profit_price, tp_basis,
  tp_r_multiple, tp_distance_dollars, tp_distance_pct,
  expected_gain_if_tp_hit, expected_loss_if_stopped, entry_reference_price

**Notable code changes:**
- 4 new audit kinds shipped: research_tradeconf_pushback_acted_on,
  research_modifications_applied, research_tradeconf_timeout,
  research_tradeconf_error
- WebDeps already had `research_firm` field — wiring just needed main.py
  to populate it after build_research_firm_deps runs

**Verification:**
- PID change confirmed
- 2 PositionContext engagements completed (Otter 4h, Cypher 24h)
- agent_state rows present for both divisions
- Dashboard initially 500'd on _summary_for_event (latent bug, hotfixed
  separately — see 2026-05-02 02:13 entry)

**Inert / dormant on current traffic:**
- TradeConfirmation consult fires on every Otter/Cypher alert, but most
  alerts in current paper-mode pre-restart audit log are `alert_ignored`
  (bias not set). First webhook that produces an order will exercise
  the consult.

---

## 2026-05-01 (early, no precise timestamp recorded) — Bulk-track scaffolding

**Commits:** `606254e` (and earlier commits unbundled into the bulk-track)
**Triggered by:** Pre-existing trading_corp tree was untracked; bulk-commit added it to git
**Backup tag:** n/a (was in place before tracking started)

**Status:** Best-effort reconstruction — pre-deploy-log discipline.

**Features shipped (already on prod via earlier ad-hoc deploys):**
- Phase 1a-1: CandidateRecommendation engagement graph
- Phase 1a-2: PMCC scout integration with extended-outage notify
- Phase 1b: Thesis ad-hoc + dashboard library
- Phase 1c: Real Fundamental + Sentiment experts (yfinance-backed)
- Holdings table simplification (e14903b)
- PMCC roll history + crypto positions surfacing (b70b6a3, a208f8d)

**Inert observations:**
- Several BACKLOG.md items reference scaffolding that was already
  in-tree at bulk-track time (e.g. take_profit yaml blocks for Otter+Cypher,
  TP-field code paths in _build_order). Some BACKLOG entries describing
  these items have been left as P1 because while the CODE was there,
  the integration into would_have_placed pushes wasn't necessarily
  exercised. Future deploys touching this area should re-verify before
  treating BACKLOG as gospel.

---

## 2026-05-03 16:25 UTC — UI grouping by investment type + Fidelity Individual deactivation + BitUnix placeholder + Coinbase Futures STANDBY

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Sun 2026-05-03 BitUnix Futures vision conversation. User decided crypto split (Coinbase spot = BTC long-only; BitUnix futures = BTC/SOL/ETH bidirectional leveraged; Coinbase Futures → STANDBY) and asked for the dashboard to organize by investment type (Individual / Crypto / Retirement) instead of by broker (Robinhood / Fidelity / Coinbase). Full vision in `~/.claude/.../memory/trading_corp_bitunix_vision.md`. This deploy ships the UI reorg only — phased BitUnix broker build (Phase 1+) is gated on B.4 flag flip Mon.
**Backup tag:** `.pre-inv-type-ui-20260503-1622`

**Files deployed (6 modified):**

- `config/divisions.yaml`:
  - `fidelity_individual` set to `enabled: false` — division deactivated per user decision (option (b) of three: hide / deactivate / delete). YAML entry retained as deadcode for cheap revival. Pre-deactivation safety: account had 0 positions per dashboard.
  - `coinbase_futures` gains `standby: true` flag — UI-only flag, broker init unchanged (still registered, still PaperBroker-wrapped).
  - **NEW** `bitunix_futures` division added — `broker: bitunix`, `account_filter: futures`, `intent: aggressive`, `standby: true`. No broker adapter exists yet; main.py logs WARNING "Unknown broker family 'bitunix'" at startup (expected); hydration falls through to `status='not_wired'`.
- `trading_corp/utils/divisions.py`:
  - Renamed `BrokerGroup` → `InvestmentGroup`, `group_by_broker` → `group_by_investment_type`. New helper `classify_investment_type(d)` maps each division to "individual" / "crypto" / "retirement" using rule: `intent=='retirement'` → retirement; `broker in {coinbase, bitunix}` → crypto; else individual.
  - `_BROKER_ORDER`/`_BROKER_LABELS` replaced with `_INVESTMENT_TYPE_ORDER` (`individual`, `crypto`, `retirement`) and `_INVESTMENT_TYPE_LABELS`. Added `_CRYPTO_BROKERS = {coinbase, bitunix}` for the classifier.
  - **NEW field** `Division.standby: bool = False` (loaded from YAML's `standby` key).
  - Updated `__all__` exports.
- `trading_corp/web/data.py`:
  - Import line updated to new symbol names.
  - `CommandCenterSnapshot.broker_groups: list[BrokerGroup]` → `investment_groups: list[InvestmentGroup]`.
  - Aggregation loop updated.
- `trading_corp/web/templates/home.html`:
  - `{% for grp in snap.broker_groups %}` → `{% for grp in snap.investment_groups %}`.
  - Status badge gains conditional: if `d.standby`, render orange/warn "STANDBY" badge instead of the online/offline/not_wired status badge. Standby is exclusive (replaces, not adds-to, the status badge).
- `trading_corp/web/templates/partials/stat_cards.html`:
  - Variable rename + label change "{N} brokers" → "{N} groups" on the total-equity stat card.
- `trading_corp/comms/telegram_commands.py`:
  - `/status` Telegram message: "*By broker*" section header → "*By investment type*". Emoji map updated: 💼 individual, 🪙 crypto, 🛡 retirement.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Investment-type grouping on /command-center**: dashboard renders three groups in fixed order (Individual / Crypto / Retirement) replacing the prior broker-grouped layout. Each group shows aggregate equity + pnl. Per-group counts visible in stat cards.
- **STANDBY badge UI primitive**: any division with `standby: true` in YAML renders an orange STANDBY badge instead of online/offline. Currently used by Coinbase Futures + BitUnix Futures.
- **Fidelity Individual deactivated end-to-end**: not loaded by `load_divisions()`, no broker registered, not in dashboard, not in /status Telegram. Removable cheaply via YAML `enabled: true` flip if needed.
- **BitUnix Futures placeholder card**: visible in Crypto group with STANDBY tag and equity = "—" (not_wired). Card link → /division/bitunix_futures (will 404 cleanly until division-page hydration handles it; not exercised today).
- **Telegram /status investment-type view**: replaces the broker-aggregate table.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **STANDBY is UI-only.** Setting `standby: true` does NOT disable order routing or broker registration — Coinbase Futures is still registered as a paper-exec broker today. Behavioral disable for Coinbase Futures comes later (per BitUnix vision: keep $-balance reads, drop order path; manual promote later). Don't assume STANDBY === "no orders possible" until that follow-up ships.
- **`broker: bitunix` is unknown to main.py's broker dispatch.** The startup WARNING is harmless but if anyone adds a strict-mode broker check, that warning becomes a fatal. Phase 1 of BitUnix build will register either a real or paper BitUnix broker keyed by `bitunix_futures` slug.
- **`classify_investment_type` is a pure mapping function**, not stored on Division. If you add a new broker family, decide in `_CRYPTO_BROKERS` set whether it's crypto or individual. New retirement-family criterion would need a different rule.
- **Telegram /status emoji map is keyed by group key**, not broker name anymore (`individual`/`crypto`/`retirement` not `robinhood`/`coinbase`/etc.). If you reuse this code path, mirror the new keys.

**Latent bugs caught + fixed (if any):**
None caught/fixed in this deploy.

**Verification:**
- Local smoke test (`python -c "from trading_corp.utils.divisions import ..."`): 8 enabled divisions, 3 groups in correct order: Individual=`[robinhood_pmcc, robinhood_joint, fidelity_joint]`, Crypto=`[coinbase_spot, coinbase_futures, bitunix_futures]`, Retirement=`[robinhood_ira, fidelity_401k]`. Standby flag parses correctly on `coinbase_futures` and `bitunix_futures`.
- Local browser render at `localhost:8000` confirmed by user — three groups render with correct labels, BitUnix shows STANDBY, Coinbase Futures shows STANDBY, no Fidelity Individual card. (Local test bypassed Fidelity via blanked `.env` for fast startup; restored after.)
- Prod restart 16:25:19 UTC; web bound at 16:25:57 UTC (38s); `/healthz` returned 200 OK in 1.7ms.
- Expected `WARNING trading_corp.main: Unknown broker family 'bitunix' for division bitunix_futures` confirmed in journalctl.
- User confirmed visual layout in browser at `https://trading.jacksumner.com`.

**Inert / dormant on current traffic (if any):**
- **BitUnix Futures division**: card visible but division has no broker adapter. Hydration marks `not_wired`. Routes to /division/bitunix_futures will 404 or render with empty data; not exercised today. Phase 1 (broker bring-up + KV migration of section 5c keys) ships post-B.4.
- **Coinbase Futures STANDBY**: badge is purely cosmetic in this deploy. Broker still registered, still order-capable in non-paper modes (we're in PAPER, so moot). Order-path disable comes in a follow-up deploy when the user decides to flip the behavior.
- **Fidelity broker bot-detection error surfaced post-restart**: separate pre-existing flakiness, NOT caused by this deploy. Fidelity Joint + Fidelity 401(k) failed broker connect with "Fidelity rejected the login session... Cache wiped — wait 5-10 min and restart." UI shows them as offline/not_wired until next successful Fidelity login. No data loss; resolves on a future restart.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-inv-type-ui-20260503-1622; BASE=/home/azureuser/trading_corp
for f in config/divisions.yaml \
         trading_corp/utils/divisions.py \
         trading_corp/web/data.py \
         trading_corp/web/templates/home.html \
         trading_corp/web/templates/partials/stat_cards.html \
         trading_corp/comms/telegram_commands.py; do
  mv \$BASE/\$f.\$TAG \$BASE/\$f
done
sudo systemctl restart trading-corp
"
```

---

## 2026-05-03 17:54 UTC — BitUnix Futures Phase 1: read-only broker + KV migration

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Sun 2026-05-03 user greenlight to wire up BitUnix Phase 1 (per `~/.claude/.../memory/trading_corp_bitunix_vision.md` Phase 1: read-only standby). User decided to ship Phase 1 *before* B.4 flag flip — the original "post-B.4" sequencing was conservative; Phase 1 doesn't touch the HITL approval flow, so independent ship is safe. The 16:25 UTC UI grouping deploy was cosmetic; this deploy actually wires the BitUnix broker so the placeholder tile shows real account data.
**Backup tag:** `.pre-bitunix-phase1-20260503-1744` (on the 2 modified files; bitunix.py is new — no backup)
**KV migration:** `BITUNIX-FUTURES-API-KEY`, `BITUNIX-FUTURES-API-SECRET` uploaded to `kv-tc-vtwbowt3wtkpy` via targeted `az keyvault secret set` (NOT the full `scripts/upload_secrets_to_keyvault.ps1` — that would clobber prod's divergent LORD-OTTER-WEBHOOK-SECRET / MARKET-CYPHER-WEBHOOK-SECRET values per `trading_corp_prod_ops.md`). Secret values read from `.env`, never echoed to the conversation.

**Files deployed (3 — 1 new, 2 modified):**

- **NEW** `trading_corp/brokers/bitunix.py` (~290 lines):
  - `BitunixBroker(Broker)` — read-only Phase 1 broker
  - `_sign(api_key, api_secret, query, body)` helper — SHA256-double-sign per BitUnix docs (`https://www.bitunix.com/api-docs/futures/common/sign.html`): `digest = SHA256(nonce + ts + key + sortedQuery + body)`, then `sign = SHA256(digest_hex + secret)`. Headers: `api-key`, `sign`, `nonce` (UUID4 hex no hyphens), `timestamp` (ms). No passphrase (BitUnix doesn't use one — `.env`'s `BITUNIX_FUTURES_PASSPHRASE` field is unused).
  - `connect()` — opens httpx async client + smoke-checks via initial snapshot. Failures log a warning but don't raise — hydration catches them later. Stub mode if creds missing (returns zeros instead of failing).
  - `snapshot()` — sums account balance across stablecoin margin coins (`USDT`, `USDC`). Per-coin equity = `available + frozen + margin + transfer + crossUnrealizedPNL + isolationUnrealizedPNL + bonus`. Position list fetched once (margin-coin-agnostic). Verified $2500 reconciles against BitUnix UI (USDC: $1250 available + $1250 transfer; USDT empty).
  - `quote(symbol)` — public `/api/v1/futures/market/tickers?symbols=<sym>` endpoint. No auth.
  - `place_order` / `cancel_order` — raise `NotImplementedError` as a Phase 1 backstop. In PAPER mode (current prod state) these are never reached — PaperExecutionBroker routes orders to PaperBroker. The raise only fires if someone constructs an unwrapped BitunixBroker in LIVE mode, which doesn't happen until Phase 4.
  - Endpoints: `GET /api/v1/futures/account?marginCoin={coin}`, `GET /api/v1/futures/position/get_pending_positions`, `GET /api/v1/futures/market/tickers?symbols=...`. Base URL `https://fapi.bitunix.com`.
- `trading_corp/utils/secrets.py`:
  - Added `BITUNIX_FUTURES_API_KEY` / `BITUNIX_FUTURES_API_SECRET` to `_SECRET_KEY_NAMES` (so values get redacted from logs by `RedactingFilter`).
  - Added `bitunix_futures_api_key` / `bitunix_futures_api_secret` fields to `Secrets` dataclass.
  - Added both env-var names to `expected_env_vars` for Key Vault loader.
  - `load_secrets()` reads both via `_env(...)`.
  - **No** passphrase field — BitUnix's signing uses pure SHA256, not HMAC+passphrase like Coinbase legacy.
- `trading_corp/main.py`:
  - **NEW broker family branch** `if family == "bitunix":` mirroring the Coinbase pattern. Constructs `BitunixBroker(api_key=secrets.bitunix_futures_api_key, api_secret=secrets.bitunix_futures_api_secret)`. In PAPER mode wraps in `PaperExecutionBroker` so snapshots use real BitUnix data while orders simulate via `PaperBroker`. In LIVE mode (not currently exercised) returns the unwrapped real broker — but `place_order` raises until Phase 4 ships.

**Features shipped (load-bearing for future "is X done?" checks):**
- **BitUnix Futures division now reads real account data on prod.** Dashboard tile in Crypto group shows live equity ($2,500.00 confirmed against BitUnix UI), live position count (0 currently), STANDBY badge stays.
- **Real BitUnix API auth working from Azure VM IP.** Unlike Fidelity, BitUnix's API does NOT IP-block the datacenter address. Confirmed by successful snapshot from `tc-prod-vm` at 17:54:47 UTC. (Important contrast for the Fidelity P1 BACKLOG item.)
- **Multi-margin-coin balance aggregation.** Sums USDT + USDC futures balances. BTC/ETH-margined balances are deferred (need quote conversion to USD).
- **Phase 1 read-only enforcement.** `place_order` / `cancel_order` raise `NotImplementedError` on the unwrapped broker as a defensive backstop until Phase 4. PAPER mode wrapping insulates the live signal path.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`transfer` field is additive in BitUnix equity math.** Initially I assumed `transfer` was a duplicate view of `available` (i.e. "available to transfer out"). User-confirmed against UI: `available + transfer = total wallet balance`. The two are independent components. Keep it summed.
- **BitUnix supports many margin coins; per-coin queries required.** No bulk endpoint exists. Phase 1 sums stablecoins only (USDT, USDC) and treats them 1:1 USD. If user moves funds to BTC/ETH margin, the dashboard will under-count until a quote-conversion path is added.
- **Connect-time smoke check is best-effort, not fatal.** If BitUnix returns 401 or rate-limits during boot, broker stays registered with stub data and hydration catches the error later. No restart loop.
- **Snapshot is sequential + slow (~37s observed).** Three sequential API calls (account x 2 coins + positions). Future polish: parallelize via `asyncio.gather`. Not blocking — happens at startup, not per-request.
- **No live order capability.** `place_order` raises in unwrapped form. PAPER wrapping makes orders flow to `PaperBroker`. Phase 4 will replace the raise with real BitUnix order placement (gated on stop-loss strategy + conviction → leverage map per the vision memo).
- **KV migration was targeted, not full upload.** `scripts/upload_secrets_to_keyvault.ps1` uploads ALL .env values to KV — that would clobber `LORD-OTTER-WEBHOOK-SECRET` and skip the divergent `MARKET-CYPHER-WEBHOOK-SECRET` per `trading_corp_prod_ops.md`. Direct `az keyvault secret set` was used for just the 2 BitUnix keys.

**Latent bugs caught + fixed (during this session):**
- **`marginCoin=USDT` alone misses USDC funds.** Initial snapshot returned $0 because user's $2500 was in USDC, not USDT. Found via raw-API probe across margin-coin variants. Fix: loop over `_STABLE_MARGIN_COINS = ("USDT", "USDC")` and sum.
- **`transfer` field omitted from equity.** Initial formula = `available + frozen + margin + crossUPnL + isoUPnL + bonus`. Returned $1250 instead of expected $2500. User confirmed `transfer` is additive (in-transit balance crediting the wallet, not a duplicate of `available`). Added to formula.

**Verification:**
- Local smoke test (`python -c ...`): BitunixBroker connects, equity = $2500.00, cash = $1250.00 (available across both coins), 0 positions. Quote endpoint returns BTCUSDT live price ($78,690 at test time).
- Local browser render at `localhost:8000` confirmed by user — BitUnix tile in Crypto group shows EQUITY $2,500.00 with STANDBY badge.
- Prod restart 2026-05-03 17:54:09 UTC; web bound at 17:54:47 (~38s); `/healthz` returned 200 OK in 1.2s.
- Prod journalctl confirmed: KV pulled `BITUNIX-FUTURES-API-KEY` + `BITUNIX-FUTURES-API-SECRET` at 17:54:10; `Registered paper-exec broker for division=bitunix_futures (paper=True)` at 17:54:11; `BitunixBroker connected (account=bitunix-futures, equity=$2500.00, 0 positions)` at 17:54:47.
- "Unknown broker family 'bitunix'" WARNING from the 16:25 UTC deploy is now GONE — confirmed absent in post-restart journalctl.
- User confirmed visual at `https://trading.jacksumner.com`.

**Inert / dormant on current traffic:**
- **No signal fan-out to bitunix_futures division.** Per the autonomous-division vision, signals should reach every division and let each decide; today's signal-routing only sends Otter/Cypher to `coinbase_spot`. So bitunix_futures gets snapshot calls (for the dashboard) but never receives `place_order` calls — even in PAPER. Phase 3 (division-entry filters + signal fan-out) ships that.
- **`place_order` / `cancel_order` raise paths are untested in prod.** They never trigger because of the PaperExecutionBroker wrapping + lack of fan-out. If you remove the wrapper or add fan-out, the raise becomes a real failure mode — Phase 4 will replace with real implementation.
- **BTC/ETH-margined balances not summed.** If user moves funds out of stablecoin margin into crypto-margin, dashboard will under-count until quote-conversion lands.
- **Snapshot timing is slow.** ~37s of three-sequential-API-calls cost at boot. Doesn't affect runtime (snapshots are cached + only re-pulled on dashboard load); just a startup-latency observation for future polish.
- **Coinbase Futures is still order-capable behind STANDBY badge.** Independent from BitUnix Phase 1; tracked as a follow-up to actually disable the order path.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-phase1-20260503-1744; BASE=/home/azureuser/trading_corp
for f in trading_corp/utils/secrets.py trading_corp/main.py; do
  mv \$BASE/\$f.\$TAG \$BASE/\$f
done
rm -f \$BASE/trading_corp/brokers/bitunix.py
sudo systemctl restart trading-corp
"
# Optionally also remove KV secrets if you want a true Phase-0 state:
#   az keyvault secret delete --vault-name kv-tc-vtwbowt3wtkpy --name BITUNIX-FUTURES-API-KEY
#   az keyvault secret delete --vault-name kv-tc-vtwbowt3wtkpy --name BITUNIX-FUTURES-API-SECRET
# (KV secrets being present is harmless if the broker code is gone — main.py
# falls back to the "Unknown broker family" warning path.)
```
