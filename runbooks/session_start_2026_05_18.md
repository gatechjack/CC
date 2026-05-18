# Next-session pickup prompt (2026-05-18)

*This file rewritten 2026-05-17 22:30 UTC by the promote/demote UX-fix +
Kalshi strategy review session. Supersedes the morning version (which
documented the post-17:45 UTC pickup that this session executed).*

---

Paste this into a fresh Claude Code session in `C:/Users/AA Incorporado/cc`:

---

Resuming from 2026-05-17 22:30 UTC wrap. One session today; no parallel
work outstanding (parallel session's unstaged BitUnix backtest files
remain — leave them alone). Read the EOS snapshot at the top of
`BACKLOG.md` first.

## What landed yesterday — promote/demote round-tripping + strategy review

**Two prod deploys** (both fully rolled back-able; tags + recipes in `runbooks/deploy_log.md`):

1. **20:36 UTC — v1 Promote/Demote UX fix** (commit `652b0c3` v1 portion).
   - `HX-Refresh: true` on action-pill responses → page reloads, both panels re-render.
   - `_query_pm_whales` adds zero-stat placeholder rows for whales in `selected_whales` with no round_trip/open activity yet (freshly-promoted whales become visible immediately).
   - `_query_kalshi_watch_only_rows` switched source from `watch_only_stats` (dict) to `watch_only_whales` (list). Stats now enriched from `watch_only_stats` when present.

2. **21:25 UTC — v2 architecture change** (commit `652b0c3` v2 portion).
   - Promote/demote endpoints stopped mutating `watch_only_whales`. They now ONLY touch `selected_whales` + `pinned_whales`.
   - Both panels filter at render time: Selected = whales in `selected_whales`; Watch List = whales in `watch_only_whales` ∧ NOT in `selected_whales`.
   - Demoted whales reappear on Watch List with original Apify/leaderboard stats intact — no API refetch needed for the user-promoted-from-watchlist case.
   - Tab persistence via `window.location.hash` — HX-Refresh post-action keeps you on the Whales tab.

3. **One-off recovery (22:00 UTC):** 6 wallets whose `watch_only_whales` entries had been deleted by v0/v1 promote code were backfilled via `PolymarketDataAPIClient.fetch_closed_positions`. nojnn, everydaymortgage, westminster, IlIIllIIIllIIl, superbeter007, ranger44. `watch_only_whales` now at 54 entries.

4. **PMCC test fixture fix** (commit `b64803c`). 5 failing tests in `test_pmcc_logic.py` — the shared `_call` helper was missing `open_interest`/`volume` and had bid/ask spread too wide for low-mark fixtures. Production code untouched; all 80 tests pass now.

5. **Strategy review (analysis only, nothing shipped):**
   - kalshi_crypto_arb (post-bucket-guard cutoff): 78.7% WR, +$19.62 on 61 trades. `min_horizon_hours: 4` is a candidate cheap fix (would shift PnL to +$23.91). User deferred until larger sample.
   - kalshi_llm_arbitrage: net-negative (-$49). LLM calibration broken in tails. KXCHINAANNOUNCE "win" was structural arb the LLM accidentally caught, not judgment edge. US scheduled macro releases (PPI/CPI/airfare CPI) are systematic losers.
   - **NEW DIVISION PROPOSAL — `kalshi_structure_arb`** — see PRIORITY #1 below.

Service is healthy. PID 616794. Local git clean for this session's files (parallel session has unstaged BitUnix backtest scripts + `btc_accumulator.py` + `.claude/settings.json` — leave alone). Prod sync verified at session end via LF-only md5: routes.py, data.py, dashboard.html all match.

## Read first

1. `BACKLOG.md` — EOS snapshot at top (2026-05-17 22:30 UTC; supersedes 17:45 UTC).
2. `runbooks/deploy_log.md` — top entries are this session's two deploys (20:36 UTC v1, 21:25 UTC v2).
3. Memory (auto-loaded):
   - `trading_corp_polymarket.md` (updated — v2 architecture documented)
   - `kalshi_strategy_analysis.md` (NEW — performance audit findings + pending decisions)
   - `kalshi_structure_arb_proposal.md` (NEW — the proposed new division)
   - `feedback_crlf_routes_py_deploy.md` (still relevant if you touch routes.py)
   - `feedback_uvicorn_no_reload_in_prod.md`
   - `feedback_az_run_command_when_ssh_blocked.md`

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 1 — kalshi_structure_arb backtest

Decision pending from yesterday: build a new Kalshi division that
deterministically captures the structural-arb opportunity in
multi-outcome announcement events. The motivating finding: kalshi_llm_arbitrage's
only profitable event (KXCHINAANNOUNCE-26MAY: 7 sub-markets, sum of
implied YES ≈ 4.6, only ~1 could resolve YES) was a structural mispricing
the LLM accidentally caught, not LLM judgment edge.

**Per CLAUDE.md § 4 + PROJECT_CONTEXT.md § 11: Backtester approval required
before any new strategy code lands in production. Backtest FIRST.**

═══════════════════════════════════════════════════════════════════════════
TASK: Design and build a new Kalshi division — "kalshi_structure_arb" — that
detects multi-outcome announcement-style events where sub-market implied YES
probabilities sum to materially more than is structurally possible, and emits
NO bets on the most-overpriced sub-markets. Paper-only on initial deploy.
Backtester approval required before any per-strategy parameter is set above
its current default.

═══════════════════════════════════════════════════════════════════════════
WHY THIS DIVISION SHOULD EXIST — the evidence
═══════════════════════════════════════════════════════════════════════════

Reviewing kalshi_llm_arbitrage's profitable trades (post-cutoff window, May
11-16) surfaced that one event drove ALL of the strategy's positive PnL:

  KXCHINAANNOUNCE-26MAY — "What will Trump announce as part of his China
  trip?" — 7 distinct sub-markets, 18 entries, 16 wins, +$24.07.

The 7 sub-markets at entry-time implied YES probabilities:
    FENT   0.83 (resolved NO)
    SOYA   0.84-0.89 (resolved YES)
    AISA   0.52-0.80 (resolved NO)
    RARE   0.29-0.76 (resolved NO)
    BOT    0.57-0.66 (resolved NO)
    USDET  0.68 (resolved NO)
    USOIL  0.50 (resolved NO)

Sum of implied YES probabilities ≈ 4.6 across 7 sub-markets. Only 1 (SOYA)
resolved YES. The market collectively over-priced "Trump will announce X"
sub-markets by 3-4× the actual outcome rate. Buying NO on every sub-market
was a positive-EV trade independent of any LLM judgment — it's a structural
mispricing of joint outcome probabilities in multi-outcome events.

kalshi_llm_arbitrage accidentally captured this because its LLM has a
low-p-bias miscalibration that happened to align with the right strategy
(bet NO on all). It is NOT a repeatable LLM-judgment edge. A purpose-built
structural-arb strategy would catch more of these events with a deterministic
rule and without burning Anthropic credits.

═══════════════════════════════════════════════════════════════════════════
WHAT TO BUILD
═══════════════════════════════════════════════════════════════════════════

A scan-driven strategy in trading_corp/agents/strategies/kalshi_structure_arb.py:

1. Discovery: pull Kalshi events via the existing KalshiBroker.list_markets
   discovery cache. Reuse the cache that kalshi_llm_arbitrage/kalshi_crypto_arb
   already populate — do not double-fetch.

2. For each event_ticker, group its constituent sub-markets and compute:
       sum_yes_implied   = Σ implied_yes_i for each sub-market
       n_sub_markets     = K
       avg_implied       = sum_yes_implied / K
   The strategy fires when sum_yes_implied exceeds a configurable threshold
   (default 1.5) AND n_sub_markets >= 3. The 1.5 floor protects against
   firing on simple binary markets where implied YES happens to be 0.55.

3. For events that fire, select the M sub-markets with the highest implied
   YES (most overpriced relative to a uniform-prior baseline of 1/K) and
   emit NO ProposedOrders against each. M is configurable (default 3 — fewer
   bets gives a tighter R:R; more increases breadth at lower per-bet edge).

4. Skip rules — exclude events that already belong to another strategy:
     - Binary YES/NO markets (n_sub_markets < 3)
     - Price-bucket markets (ticker contains 'B' or 'T' price-suffix
       pattern — these are covered by kalshi_tail_price_arb /
       kalshi_temporal_bucket_arb)
     - Crypto category markets (covered by kalshi_crypto_arb)
     - Climate and Weather category (covered by kalshi_weather_arb)
     - Markets without an ASK quote on the NO side (can't execute)

5. Sizing: fixed_usd $1 per shakedown trade, matching kalshi_crypto_arb's
   pattern. Configurable via sizing.fixed_amount.

6. Audit kinds (mirror the kalshi_crypto_arb naming convention):
     - kalshi_structure_arb_scan       (per-cycle summary)
     - kalshi_structure_arb_evaluated  (per-event sum_yes + verdict)
     - kalshi_structure_arb_skipped_*  (granular skip reasons —
       binary_market, price_bucket, crypto, weather, no_quote,
       below_threshold, below_min_k)

═══════════════════════════════════════════════════════════════════════════
HARD CONSTRAINTS — DO NOT VIOLATE
═══════════════════════════════════════════════════════════════════════════

- Read CLAUDE.md before starting. Pay attention to:
    * § 1 "Risk + execution" — risk_agent.evaluate() is the single chokepoint.
      Every emitted ProposedOrder MUST pass through it.
    * § 1 "State + audit" — write audit_event BEFORE every decision branch,
      tag with strategy + division keys.
    * § 1 "HITL surface direction" — Telegram notifications point at the
      dashboard. Don't enrich Telegram beyond a one-line ping.
    * § 3 "Adding a new strategy" — follow the canonical pattern.
    * § 4 "Things to ask before doing" — do NOT deploy without Backtester
      approval. Auto_execute MUST stay false until paper-track validated.

- Paper-default. enabled: true, auto_execute: false in strategies.yaml.

- No LLM in the path. This is deterministic structural arbitrage —
  sum_yes_implied is a pure number from quotes. If you want narration,
  add an optional LLM "explain this decision" call AFTER the verdict,
  never replacing the verdict. Mirror the kalshi_crypto_arb pattern.

- Reuse existing infrastructure:
    * KalshiBroker.list_markets discovery cache
    * The kalshi_quote_dollars helper in _weather_math.py
    * The RiskAgent.evaluate() inline call
    * The same ProposedOrder shape used by other Kalshi strategies

- Tests in tests/test_kalshi_structure_arb.py covering:
    * Event with sum_yes_implied = 4.6 across 7 markets → 3 NO orders emitted
    * Event with sum_yes_implied = 1.2 across 3 markets → no orders (below
      default threshold of 1.5)
    * Binary YES/NO market with implied 0.55 → skipped (below min_k)
    * Crypto-category event → skipped
    * Weather-category event → skipped
    * Risk gate rejection → no orders emitted, would_have_placed audit
      still written

═══════════════════════════════════════════════════════════════════════════
DELIVERABLES (in order)
═══════════════════════════════════════════════════════════════════════════

1. A backtest. Before any code lands in production:
    a. Pull all Kalshi events from the audit_event ledger in the last 60
       days that would have qualified (sum_yes_implied > 1.5, n_sub >= 3,
       excluding crypto/weather/price-bucket markets).
    b. Reconstruct the implied_yes at would-have-fired-time per sub-market.
    c. Compute hypothetical NO bets on the top-3-implied sub-markets per
       qualifying event, then check the resolved outcomes in
       kalshi_round_trips OR via KalshiBroker.get_market_resolution.
    d. Report: n_events, n_bets, n_wins, win_rate, gross_pnl, ROI, and
       compare against kalshi_llm_arbitrage's per-event performance on the
       same set. Sanity-check against the KXCHINAANNOUNCE case (should
       show 6 wins on 3 top-implied sub-markets if AISA, FENT, USDET were
       the top 3).
    e. Save the report to reports/kalshi_structure_arb_backtest_YYYY-MM-DD.md.
       Bring it to the Board for approval before proceeding to step 2.

2. Strategy code + config + tests, behind enabled: true / auto_execute: false.
   Add the division to config/divisions.yaml (broker: kalshi, account filter
   matches the existing kalshi paper account).

3. Boot wiring in trading_corp/main.py. The strategy should run on the same
   poll cadence as kalshi_crypto_arb (60s).

4. Deploy plan: md5-diff target files, take backup tag, ship via
   az vm run-command (SSH may be blocked from non-home IPs — see memory
   feedback_az_run_command_when_ssh_blocked). routes.py is not touched
   by this division, so the CRLF gotcha does not apply.

5. After 7 days of paper-mode data: pull the same per-bucket analysis we did
   for kalshi_llm_arbitrage to decide whether to leave the default 1.5
   threshold, raise it, or add a sub-strategy variant.

═══════════════════════════════════════════════════════════════════════════
NON-GOALS
═══════════════════════════════════════════════════════════════════════════

- Do NOT try to detect "announcement-style events" via title NLP. The
  sum-of-implied rule is deterministic and category-agnostic — let it find
  the events without classification logic.

- Do NOT flip auto_execute: true on initial deploy, even if the backtest
  is positive. Per-strategy auto-exec is earned through observed paper
  performance, not granted by default (CLAUDE.md § 1).

- Do NOT add new audit_event kinds beyond what's listed above. Reuse
  would_have_placed for the actual emit; the structure_arb_* kinds are
  for scan-cycle telemetry only.

- Do NOT modify kalshi_llm_arbitrage to "exclude" the events this catches.
  Leave it alone — the new strategy will write its own audit trail and the
  comparison data is more useful with both running in parallel.

- Do NOT change the Kalshi discovery cache TTL or fetch pattern. Reuse
  what's there.

═══════════════════════════════════════════════════════════════════════════
OPEN QUESTIONS TO RESOLVE WITH THE BOARD BEFORE WRITING CODE
═══════════════════════════════════════════════════════════════════════════

1. Should the threshold be `sum_yes_implied > T` (additive) or
   `sum_yes_implied / max_concurrent_yes > T` (normalized for events where
   multiple sub-markets can resolve YES)? For Trump-announces-X, multiple
   can resolve YES, so the absolute sum may be misleading. The backtest
   should compare both.

2. How to detect "max_concurrent_yes" — i.e., for events that constrain to
   "exactly one of K" (like elections), the sum should be 1.0. For
   announce-style events, it's K. The event_ticker pattern may encode this
   (e.g. KX...ANNOUNCE-* might be loose; KX...WINNER-* tight). Document
   what's known after the discovery scan in the backtest report.

3. Should the strategy bet on YES-side when sum_yes_implied is way BELOW
   what's structurally required (e.g., a "exactly one wins" event where
   sum implied is 0.7)? This is the symmetric edge. Add a follow-on
   analysis after v1 ships.
═══════════════════════════════════════════════════════════════════════════

## Other pickup candidates (after the priority above is in motion or
## handed off, ordered by signal/effort ratio)

1. **Apply the cheap kalshi_llm_arbitrage cuts** (~30 min, hot-reloadable):
   - US-release ticker prefix blacklist: `KXUSPPI*`, `KXUSCPI*`,
     `KXAIRFARE*`, `KXAAAGAS*`. -$36 cut on 36 trades, all wins=0.
   - `max_divergence_pct: 30` cap (the 30-50% + 50%+ buckets together lost
     -$14 on 37 trades; 50%+ alone was 0/12).
   - Both small enough to bundle into one deploy. Requires a small code
     addition to read the blacklist + apply.

2. **Residual Sci/Tech leak in kalshi_llm_arbitrage** (~10 min). 10 trades
   on KXA100W / KXH100W (Atlanta/Houston temp markets, categorized by
   Kalshi as Sci/Tech). Either add Sci/Tech to category exclusion or
   pattern-match `KX*100W`. -$10 cut.

3. **kalshi_crypto_arb `min_horizon_hours: 4`** (~5 min flip + verification).
   Re-run the analysis once the 69 open positions resolve. Pattern:
   sub-4h crypto markets have zero winners; cut would shift PnL +22%.

4. **Algorithm-selected whale fetch-on-demote** (~30-45 min). The 7 PM
   whales in `selected_whales` from `refresh_polymarket_whales.py` (not
   from `watch_only_whales`) will still vanish on demote. Bake the
   recovery_backfill.py logic into the demote endpoint as a
   fetch-if-missing fallback.

5. **kalshi_llm_arbitrage 5/14→5/15 activity collapse** (~30 min
   journalctl/audit archaeology). 155 → 8 trades/day drop. Cause
   unknown. Could be cooldown saturation, an upstream change, or an
   error spiral.

6. **Standing backlog** (no urgency from this session):
   - Kalshi `temporal_bucket_arb` `expires_at` payload audit (~30 min).
   - `apply='true'` query bug in older session_start runbooks (2-line
     edit).
   - Reports/*.md archival decision (parallel-session work).
   - PMCC audit (perennial — needs scope-narrowing).

7. **A week out — Sun 2026-05-24 13:02:51 UTC:** watch the first
   Polymarket weekly cron fire.

## Things to NOT do without explicit approval

- Don't deploy the new `kalshi_structure_arb` strategy without running its
  backtest and getting Board sign-off. The prompt above documents this —
  follow it strictly.
- Don't flip `kalshi_llm_arbitrage.auto_execute: false → true`. The
  strategy is net-negative and LLM calibration is broken in both tails.
- Don't flip `kalshi_crypto_arb.auto_execute: false → true` until the
  sample size is 200+ trades and the post-cutoff trend is confirmed in a
  backtest.
- Don't `systemctl restart trading-corp` blindly. Live PCT +
  polymarket_arbitrage Cloudflare-retry resilience is still dormant
  until the next natural restart (see 2026-05-17 17:38 UTC deploy_log).
- Don't disable the `trading-corp-pm-watchlist-deep.timer` (next fire
  Sun 2026-05-24).
- Don't delete the backup tags `pre-promote-demote-uxfix-20260518-*`
  until ≥48h post-deploy.
- Don't deploy via `patch -p1` over a file that touches `routes.py`
  without prepending the CRLF-normalize step (per
  `feedback_crlf_routes_py_deploy.md`).
- Don't change the `pinned_whales` schema or the per-venue
  `selected_whales` shape.
- Don't change the v2 architecture rule that
  promote/demote-endpoints-don't-touch-watch_only_whales without
  explicit approval (it's the single membership truth now).
- Don't flip BitUnix `htf_gate.mode: enforce → shadow`. Don't flip
  `trade_plan.enabled: true → false`. Standard BitUnix do-not-touch
  list applies.

## Environment notes

- Local Python: `C:\Users\AA Incorporado\AppData\Local\Python\bin\python.exe`
  (bare `python` is the MS Store stub).
- SSH usually blocked from non-home IPs; pivot to `az vm run-command create
  --script @file` per `feedback_az_run_command_when_ssh_blocked.md`.
- Windows checkout CRLF; deploy scripts MUST `tr -d '\r'` before
  `az vm run-command create`. Use LF-normalized files for the script
  arg, not the raw checkout.
- `.py` changes under `trading_corp/` need `systemctl restart trading-corp`
  to take effect in the live service (uvicorn runs without `--reload` in
  prod). Templates DO live-reload (Jinja). Timer-driven scripts pick up
  new code automatically because they spawn fresh Python processes.
- Pyo3 `az vm run-command create` is single-tenant; `--name` must be
  unique-per-deploy or `az vm run-command delete --yes` first.

Honest assessment first — don't dive into code until you've read the
EOS snapshot at the top of BACKLOG.md and the kalshi_strategy_analysis
memory. The "should we ship X?" decisions all depend on sample sizes
that are still small.
