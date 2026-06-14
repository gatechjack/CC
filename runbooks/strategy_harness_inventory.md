# Strategy Harness Inventory

**Purpose.** What survives the kalshi_crypto (2026-05-22) and kalshi_sports_arb_observer (2026-06-14) shelves. The next edge inquiry — Bitunix fusion, a new venue, a new instrument — starts from this inventory, not from scratch. For each component: what it is, where it lives, and what an edge-agnostic next-use looks like.

## Data / execution layer (edge-agnostic, reusable as-is)

- **Kalshi market discovery** — `trading_corp/data/kalshi_market_map.py`. Series-ticker scoping, status-open filtering, `inter_call_delay_sec=0.15` rate-limit guard.
- **Spot / realized-vol providers** — `kalshi_quote_dollars` in `trading_corp/agents/strategies/_weather_math.py`; `CoinbaseBroker` REST spot; the vol-v2 realized-vol pipeline. The vol-v2 *infrastructure* is solid; what failed was the *edge premise it served*.
- **Paper-fill mechanics** — `PaperBroker`, `PaperExecutionBroker`, resolver + round-trip pipeline. Edge-agnostic.

## Audit funnel (reusable WITH integrity fix)

- **scan → evaluate → fire/skip pattern** — `kalshi_crypto_scan`, `kalshi_crypto_evaluated`, `would_have_placed` audit kinds with `skip_reason`, classification fields, per-row payload JSON. Right shape for any strategy's intent-vs-action trail.
- **Integrity caveat — fix before next use.** The ±2s VIEW join window between `kalshi_round_trips` and `audit_event` drops rows where the audit fires more than 2s before the order lands (one stray confirmed this session, and the alarm under-counted by 1). Widen the tolerance or carry an explicit foreign key. Don't inherit the fragility.

## Backtester + validation gate (reusable WITH discipline)

- **Replay-against-resolved-RTs** — the pattern vol-v2 used to validate before paper-deploy. Reusable.
- **Lesson — the gate must test EV-at-fill, not win rate.** vol-v2 passed its backtest on win-rate criteria and shipped a strategy that turned out to be base-rate luck. Any future validation gate must include EV-at-fill as a required, blocking metric.

## EV-at-fill (the single most important reusable artifact from the 2026-05-22 session)

**Promote to a first-class default metric.** `EV-at-fill = model_prob_outcome × payoff − cost_paid`, computed per-fill from the audit row + the fill price. This is the metric that exposed kalshi_crypto as non-edge after WR=84.8% suggested otherwise.

- **Default it.** Every future strategy's dashboard tile, validation gate, and post-mortem must include EV-at-fill as a primary metric, not an afterthought.
- **Compute it from line one.** Don't wait until anomalies appear; by then bad fills have accumulated.
- **Diagnostic signature.** When every winner is negative-EV-at-fill, the strategy is harvesting base-rate convergence, not finding signal. WR will flatter; loss shape will eventually kill it.

## Deploy / rollback discipline (reusable process, not code)

- **Surgical-patch pattern** — single-line edits with pre/post grep verify, backup tags per `runbooks/deploy_log.md` template.
- **`deploy_log.md` append after every successful deploy** — load-bearing for "is X already shipped?" checks in future sessions.
- **md5-diff against prod before assuming a feature is unimplemented** — `deploy_log.md` preamble has the exact recipe.
- **CLAUDE.md §4 in-repo systemd pattern** — never edit `/etc/` from the repo; unit files live at `infra/systemd/`, deploy is an SSH operator step.

## path_logger (committed `4368095`, NOT deployed)

Built, additive, harmless. 8 files at `trading_corp/path_logger/` + `infra/systemd/trading-corp-path-logger.service` + `scripts/backtest_kalshi_limit_ttl.py` + `runbooks/path_logger_isolation_baseline.md`. Banked for any future inquiry that genuinely needs dense order-book path logging.

- **Why not deployed.** The thesis it was built to serve (latency edge on Kalshi BTC bucket markets) was structurally closed by the BRTI 60s-trimmed-mean settlement design before deploy. See the kalshi-crypto-shelved memory.
- **Two known flags to fix before relying on it:**
  1. `cb_spot_ts` not on the `market_ladder` schema — cross-asset latency offset unobservable from the DB.
  2. NTP pre-flight returns True on `FileNotFoundError` for `timedatectl` — fails open on stripped containers; should be Linux-strict.
- **Flag priority is thesis-dependent.** These flags matter only if a future use needs sub-second / cross-asset fidelity. For minute-horizon path logging they are optional. **Do NOT fix them reflexively** — confirm the new use actually needs sub-second precision first, or you risk rebuilding the latency instrument the BRTI 60s-trimmed-mean settlement already ruled out (see the kalshi-crypto-shelved memory).
- **Reusable for** any future strategy that needs dense bid/ask/spot path data: point it at different markets, add the two missing columns/checks only if the new thesis warrants it, deploy via the in-repo systemd pattern.

---

## How the next edge starts

1. **Confirm settlement methodology first** — read the venue's contract spec (primary source, not third-party summaries). Determine point-in-time vs windowed-TWAP vs trimmed-mean vs last-trade. The step that would have saved the 2026-05-22 session from chasing a latency thesis the venue's settlement design structurally rules out.
2. **Bake EV-at-fill in from line one** — build the dashboard tile and validation gate to require positive mean EV-at-fill on winners as a pass criterion, alongside any WR/PnL display. Not "we'll add it if anomalies appear."
3. **Fix the audit-join integrity before trusting any verdict** — widen the ±2s VIEW tolerance or use foreign keys. Don't let stray rows under-count fires and silently shift the denominator.

---

## What survives the 2026-06-14 kalshi_sports_arb_observer shelve

Phase-0 sports-arb observer shelved with verdict `SHELVE_LATENCY_THESIS_CLOSED` (no hourly cross-venue
MLB-ML edge; the 1,274 "positive"/`is_arb` rows over 8,360 obs were artifacts, mean EV-at-fill −$0.375/$10,
no positive-mean subset). Canonical record: [`reports/2026-06-14_kalshi_sports_arb_observer_shelve.md`](../reports/2026-06-14_kalshi_sports_arb_observer_shelve.md). What carries forward:

- **`the-odds-api` per-book client + Pinnacle opt-in wiring** — `trading_corp/data/odds_api_client.py`.
  Pinnacle is opt-in via the `bookmakers=` filter (default us-regions response excludes it); the observer's
  `sharp_book_preference` config knob wires it in. Edge-agnostic; reusable for any sportsbook-line inquiry.
- **EV-at-fill baked in from line one — and it worked.** Computing EV-at-fill per row from the start is what
  exposed the non-edge: 15% of rows flagged positive EV / guaranteed-arb, yet the population mean was
  negative and no clean subset turned positive. A win-rate or raw-positive-count lens would have flattered
  it. Same lesson as kalshi_crypto: keep EV-at-fill first-class, not an afterthought.
- **Structural priors — promote to first-class; check BEFORE scoping any cross-venue arb:**
  - **Calendar asymmetry.** Kalshi opens markets 2–3 days pre-game; sportsbooks post lines ~24h pre-game.
    The venues overlap only in the final ~24h — the most-efficient, most-arbitraged window. Unfixable by spend.
  - **Single-feed limit.** One feed cannot separate a real signal from a feed-side quirk (stale quote,
    partial outage, metadata lag); production shops run 4–10+ feeds. Single-feed positives are leads, not verdicts.
  - **Hourly-arb prior is low.** A persistent >1h arb on a liquid market would already be harvested; its
    absence is the base rate, not a test failure. This was the load-bearing caveat for the shelve.
- **Two prerequisites before this instrument is reused (do NOT reuse without them):**
  1. **Feed-health alarm.** The division silently flatlined ~10 days when the free quota exhausted (and the
     key 401'd) while still scanning hourly into a dead feed. A data-collection division needs an alarm that
     fires when observation rows stop even though scan cycles keep firing.
  2. **Pre-game-only filter.** The observer logged post-start (in-game) snapshots — a stale Kalshi quote vs
     live in-game book odds (+1480…+3500) produced phantom "arbs" (23% of the false positives). Filter to
     `observation_ts < commenced_at` before computing A/B.
