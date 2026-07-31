# Trading Corp — Open Backlog

Durable list of work items that are real but not the current focus. Each
section ends with a recommended phase / priority. Items get pulled into
the active session when their phase comes up.

Active session work lives in chat — not duplicated here.

---

## ⏸ PAUSED — Lord Otter + Market Cypher feature work  *(2026-05-02 — Board direction)*

Both crypto strategies are in **maintenance mode** until the PMCC
research-as-consultant pattern has been observed in production for
long enough to validate (or invalidate) the research firm's
cross-division knowledge-work value proposition.

**What "paused" means concretely:**
- Existing code stays running. `auto_execute: false` (no live trades).
  Paper-mode `would_have_placed` rows continue writing to audit_event;
  Telegram pushes continue. Bias / sommi / arming state machines
  continue updating. **No behavioral change.**
- **No new features** on Otter or Cypher: no new TV signal vocabulary,
  no contract enrichment, no new tier rules, no rich `context` packet
  for TradeConfirmation, no pink-box image integration on the agent
  side, no intraday-TA expert build-out.
- Bug fixes still allowed where the bug is breaking the existing
  paper-mode pipeline. New work that would *expand* either strategy is
  out of scope.

**Why:**
- The 2026-05-02 vision realignment (see CLAUDE.md § Research
  consultation) found that we'd over-built the research firm relative
  to its actual current value. Of the four research products
  (`CandidateRecommendation`, `Thesis`, `PositionContext`,
  `TradeConfirmation`), only PMCC's `CandidateRecommendation` use
  (via `universe_source: research_on_demand`) is doing real
  cross-division knowledge work today.
- Otter's `TradeConfirmation` consults are scaffolded but the
  underlying intraday-TA capability isn't built — most consults
  fail-open as no-ops. Continuing to layer features on that
  foundation would compound the rework cost.
- The right move is to operate PMCC's research integration in
  production, observe whether research delivers signal that PMCC
  wouldn't have produced via its deterministic scout, and use that
  data to decide whether (and how) to bring research to crypto.

**Observation period:**
- Start: 2026-05-02.
- Decision date: **2026-05-05** (3 days, per Board direction).
- What to look at on 05-05: count of `research_candidate_recommendation_emitted`
  rows from PMCC scout, count of those that produced
  `would_have_placed` rows, qualitative assessment of whether the
  research-recommended candidates are ones PMCC would have surfaced
  on its own. If research isn't visibly pulling weight by then,
  open a Board decision item to scope research down rather than
  expand it.

**Code state on pause (as of 2026-05-02):**
- File rename: [agents/strategies/lord_otter.py](trading_corp/agents/strategies/lord_otter.py)
  and [agents/strategies/market_cypher.py](trading_corp/agents/strategies/market_cypher.py).
  These were previously under `agents/divisions/` — the new path
  reflects the corrected vocabulary (division = portfolio manager;
  strategy = how a division operates). 8 import sites updated.
- Research rule codified in [CLAUDE.md § Research consultation](CLAUDE.md).

**Out of scope for this pause:**
- PMCC work continues normally.
- Dashboard P5 polish items (engagement-log expand, trade-flow expand,
  latency-rename) — those are dashboard surface, not Otter/Cypher
  feature work. Continue per existing BACKLOG entries.

**To resume:** explicit Board greenlight after 2026-05-05 review,
naming the specific research integration shape that would unpause
work (e.g. "build intraday-TA expert because the macro thesis
integration showed value", or "build pink-box image vision because
PMCC scout's Thesis consumption proved the surface").

---

## ✅ DONE — Robinhood symbol-resolution log spam  *(P0, 2026-04-30)*

**Problem:** every snapshot poll (~14s) emitted a WARNING for crypto
positions whose `instrument` URL is in Robinhood's currency-chain
format (`/currencies/c-{NNNN}-{HEX}/`). The equity resolver
`rs.stocks.get_symbol_by_url` doesn't handle that URL shape, so the
position was silently dropped from the dashboard snapshot AND a
WARNING fired ~6,000+ times/day.

**Fix shipped:** `trading_corp/brokers/robinhood.py` — added
`_KNOWN_NON_EQUITY_INSTRUMENT_RE` regex to recognize the crypto chain
URL pattern, demoted those to DEBUG level (silent in production
logs). Genuinely unexpected unresolvable instruments still WARN — so
if Robinhood adds a new instrument category we'd see it surface.

**Side effect:** the underlying crypto position is still hidden from
the dashboard snapshot. To actually surface it requires using a
different Robinhood API endpoint (the `crypto.get_crypto_positions`
path). Filing as a follow-up:

---

## ✅ DONE — Robinhood crypto positions hidden from dashboard  *(2026-05-01)*

**Shipped:** added a third query branch to `RobinhoodBroker.snapshot()`
in [trading_corp/brokers/robinhood.py:367-415](trading_corp/brokers/robinhood.py:367)
that calls `rs.crypto.get_crypto_positions()` and emits a `Position`
per holding, with symbols in unified `{CODE}/USD` form (matching
Coinbase) so the dashboard renders them uniformly across brokers.
`avg_price = cost_basis / quantity` from Robinhood's response (no
ccxt-style mark-as-cost hack needed). `extra` carries
`{asset, venue: "robinhood", asset_type: "crypto"}` for parity.

**Multi-account scoping:** Robinhood holds crypto in a single
account-wide wallet (no per-brokerage filter). The new branch only
runs when `_account_type == "individual"` so the IRA Roth and Joint
RobinhoodBroker instances don't triple-count the same coins. (RH
doesn't support crypto in IRAs anyway.)

**`quote()` updated** — old guard rejected any symbol containing `/`,
which would have made `BTC/USD` mark to $0 and rendered -100% P&L on
the dashboard. New `quote()` detects `{CODE}/USD` → routes to
`rs.crypto.get_crypto_quote(CODE)`, preferring `mark_price`, falling
back to `last_trade_price` then `ask_price`. Failures return 0.0 so
snapshot pricing degrades gracefully.

**Equity reconciliation deferred:** `load_portfolio_profile.equity`
may or may not already include crypto value — couldn't verify without
a live login. Position is now visible (the explicit ask); if the
displayed brokerage equity diverges from `cash + Σ(position values)`
on the dashboard, follow up by adding crypto market value to the
returned `equity` (and being careful not to double-count).

**Tests:** 11 new tests in `tests/test_robinhood_crypto_snapshot.py`
covering: Individual emits crypto, IRA/Joint don't, `currency` as
dict-or-string, missing/zero-qty positions skipped, API failures
don't break the snapshot, `quote("BTC/USD")` routes correctly with
proper fallbacks. All green.

---

## ✅ DONE — PMCC roll-DB schema: stable pair-lifetime identifier  *(2026-05-01)*

**Shipped:** stable per-LEAP identifier `leap_lifetime_key =
"{symbol}:{strike:.2f}:{expiry}"` written to `proposed_order.extra_json`
on every roll. No DB schema change.

**Producer side** ([trading_corp/agents/divisions/pmcc_robinhood.py](trading_corp/agents/divisions/pmcc_robinhood.py)):
- `_compute_leap_lifetime_key(leg)` static helper with deterministic
  2-decimal strike formatting.
- `_propose_roll_short` writes the key to BOTH legs.
- `_propose_sell_weekly` writes when `leg` is supplied (the path that
  already passes position_context).
- `_make_option_order` accepts an optional `leap_lifetime_key` kwarg
  and stashes it on `extra` (omitted when None — preserves legacy
  pair behavior).

**Query side:** `_query_prior_rolls(symbol, leap_lifetime_key=None)`
now scopes when the key is provided. Critical compromise: pre-fix
rows (no key) still count when scoped — losing them would silently
drop history. Only pairs tagged with a DIFFERENT key are filtered
out. Without a key arg, behaves exactly as before (full backward
compat — `_build_position_context` is the only caller wiring the
key today, others can adopt later).

**Backfill:** none. Pre-fix rows fall through the "no key" branch and
continue to aggregate by symbol — explicitly tested. A backfill
script could be added if cross-LEAP contamination is found in
practice on existing rows; today's data has no multi-LEAP underliers
so it's not worth the script.

**Tests:** 8 new in `tests/test_pmcc_position_context.py`:
key-format pin, None-input handling, two-LEAPs-one-symbol scoping,
no-key aggregates all, pre-fix preservation, other-key exclusion,
end-to-end `_build_position_context` scoping, producer stashes the
key on `extra`. All 21 in the file green; full suite (177 tests)
green except the pre-existing P2 PMCC scan failures.

---

## ✅ DONE — PMCC drilldown: surface short-leg DTE on the collapsed positions row  *(2026-05-01)*

**Shipped:** [trading_corp/web/templates/partials/pmcc_pair.html:68-80](trading_corp/web/templates/partials/pmcc_pair.html:68)
— inline `{N} DTE` badge on the collapsed `<summary>` row, between the
action pill and the right-aligned Combined P&L. Color tiers match
existing urgency: `text-loss font-semibold` at 0 DTE, `text-warn` at
≤7, `text-muted` at 8+. Renders only when `short and short.dte is not
none`, so uncovered LEAPs / stock-only rows correctly suppress it.
Tooltip is plural-aware ("1 day" / "N days").

---

## ✅ DONE — Otter / Cypher: enrich `would_have_placed` push with full trade specifics + post-alert win/loss replay  *(Phase A 2026-05-02 03:30 UTC, Phase B 2026-05-02 05:45 UTC, Phase C 2026-05-02 14:56 UTC)*

All three phases shipped per [runbooks/deploy_log.md](runbooks/deploy_log.md):

- **Phase A** — push enrichment + TP fields in order.extra; trade card via `_format_trade_card`.
- **Phase B** — `paper_trade_record` table + write-on-emit hook in webhooks; backfill landed 5 historical rows.
- **Phase C** — replay job (15-min loop + startup catch-up) + per-division "Paper-trade win rate" dashboard panel. Conservative same-bar-both-hit tie = loss. 5 historical rows correctly fell through to `pre_phase_a` (NULL stop/tp from pre-Phase-A alert times).

---

**Three asks, layered:**

**(1) Surface the full trade specifics in the `would_have_placed`
push.** Today's Telegram message
([web/webhooks.py:865](trading_corp/web/webhooks.py:865)
`_format_would_have_placed_msg` and
[web/webhooks.py:907](trading_corp/web/webhooks.py:907)
`_format_would_have_placed_msg_cypher`) shows tier, side, qty,
target size %. It DOES NOT show entry, stop, take-profit, max
dollar risk, or position context — even though most of that data
IS already in the order's `extra` block (per
[lord_otter.py:1042-1063](trading_corp/agents/strategies/lord_otter.py:1042):
`stop_price`, `stop_distance_dollars`, `stop_distance_pct`,
`max_dollar_risk`, `notional_target`, `tv_payload.bar_low/high`).

What's MISSING from the order extras today:
- **Take-profit target.** Otter and Cypher don't compute one yet —
  every trade today is "stop-loss + ride to next signal." Need to
  decide: per-tier TP-as-multiple-of-risk (1R / 2R / 3R), or
  per-tier %-of-entry, or LLM-narrated based on the trigger setup.
  Recommend: deterministic per-tier R multiples in
  `config/strategies.yaml` (e.g. `lord_otter.diamond.tp_r_multiple:
  3.0`); Otter's `_size_and_place` writes `take_profit_price`,
  `tp_basis`, `tp_distance_dollars`, `tp_r_multiple` into `extra`.
- **Expected P&L summary.** From the existing risk numbers:
  `expected_loss_if_stopped = -max_dollar_risk` (already known);
  `expected_gain_if_tp_hit = max_dollar_risk * tp_r_multiple` (new).
  Show both in the push so the Board sees the asymmetry at a
  glance.

Push message format target (per the user's mockup intent — full
trade card, not a one-liner):

```
🦦 Lord Otter — DIAMOND
signal: bullish_diamond_3m
would BUY 0.0125 BTC/USD @ ~$67,420
  size: 5.00% equity ($5,000 notional)

📍 Stop: $67,150 (-0.40%, basis: trigger_bar_low)
🎯 Target: $68,230 (+1.20%, 3R)
💵 Risk: -$50.00  →  Reward: +$150.00  (R:R = 1:3)

(auto-execute is off — no order placed)
risk_approve
```

Same shape for Cypher with the swing-tier numbers.

**(2) Build a structured trade-record log.** The audit row already
captures the order shape via `would_have_placed`. Add a parallel
SQL table — `paper_trade_record` — keyed by order_id, that holds
the full trade specifics (entry_price, stop_price, tp_price,
qty, side, alert_ts, source_signal, tier, expected_loss,
expected_gain, R:R) PLUS the result fields the Phase 3 ask below
populates. Schema-stable from day one so the replay job (ask 3)
doesn't need a migration.

Why a separate table vs. squeezing into `audit_event.payload`:
the replay analysis is a JOIN against minute-bar price history,
and `audit_event` payloads are JSON blobs that don't query well
on the trade-result fields we'll want to filter on (e.g. "show me
all DIAMOND-tier alerts where TP hit before SL" — that's a
WHERE clause on result + tier, awkward against `LIKE '%result%'`).

**(3) Win/loss replay analysis from actual price action.** A
scheduled job (~every hour, or end-of-bar for the relevant
timeframe) walks the `paper_trade_record` rows where `result IS
NULL` and replays the post-alert price path:

For each open paper trade:
- Pull minute-bar OHLC from the alert_ts forward (Coinbase /
  Polygon / yfinance — same source the bot would have used).
- Walk forward bar-by-bar: did spot touch `tp_price` first or
  `stop_price` first?
- TP-first → `result = "win"`, fill at `tp_price`, P&L = +expected_gain
- SL-first → `result = "loss"`, fill at `stop_price`, P&L = -expected_loss
- Neither hit within max-hold window → `result = "open"` (re-check
  on next replay tick). Configurable max-hold per tier; default
  Otter 24h, Cypher 7d.

Result fields populated: `result`, `result_ts`, `result_price`,
`actual_pnl_dollars`, `actual_r_multiple`, `bars_to_resolution`.

Dashboard view: `/division/lord_otter` (and `/division/market_cypher`)
gets a "Paper-trade win rate" panel — last 7d / 30d / all-time
hit rates per tier, total simulated P&L, R:R distribution. This
is exactly the data needed to decide when an Otter or Cypher
division has earned `auto_execute: true` (per CLAUDE.md §1
"HITL approval is the default for any new division. `auto_execute:
true` is earned per-strategy after observed paper performance, not
granted by default").

**Implementation sequencing:**

- Phase A: ask (1) — push enrichment + TP fields in `extra`. Smallest
  blast radius. ~2 hr. Ships immediately useful Board context.
- Phase B: ask (2) — `paper_trade_record` table + write-on-emit. ~3 hr.
  Needs a tiny migration script; existing `would_have_placed` rows
  get backfilled on first scan from `audit_event` payloads where
  possible.
- Phase C: ask (3) — replay job + dashboard panels. ~half-day. Bar
  source TBD per market (Coinbase has free historical via ccxt;
  yfinance for stock-symbol crypto proxies). Needs some thought on
  which source matches the alert's reference price.

Phase A is the fastest win and unblocks Phase B/C — the enriched
order extras populate the table cleanly. Recommend doing Phase A
in its own session, then bundling B+C.

**Why this matters operationally:** today the Board sees Otter and
Cypher webhook activity but can't answer "would I be making money
if I'd flipped auto_execute on a month ago?" without manual
chart-walking. The replay job answers it directly. It's also the
only path to the design-doc-implied "earn auto_execute through
paper performance" criterion — without paper-trade outcome
tracking, that criterion is unmeasurable.

**Priority:** P1 (Phase A) — purely additive, no real-money risk,
high diagnostic value. Phase B + C bump to P0 once Otter or Cypher
is being seriously considered for `auto_execute: true`, since the
performance evidence is the gating data. Today both are stuck on
"no track record yet" — this builds the track record.

---

## ✅ DONE — Webhooks refactored to return-fast (TV 10s-timeout fix)  *(2026-05-02 16:01 UTC)*

Shipped per [runbooks/deploy_log.md](runbooks/deploy_log.md). Both
TV webhook handlers (`lord_otter_webhook`, `market_cypher_webhook`)
now do ONLY validation + `webhook_received` audit synchronously,
then dispatch the heavy processing (broker snapshot → agent.on_alert
→ research consult → risk gate → place/notify) onto a FastAPI
BackgroundTask. HTTP 200 returned in <200ms regardless of downstream
load. Live verification: `POST https://trading.jacksumner.com/webhook/tradingview/market-cypher`
returned in 0.119s. Catch-all in each background helper writes an
`agent_error` audit (phase=background_processing) + Telegram notify
on any unhandled exception so silent crashes are impossible. 5 new
tests in `tests/test_webhooks_return_fast.py`. Sister item P4
"investigate 04:00 UTC scout firing" still open as a separate
diagnostic.

---

## ✅ DONE — Manual research-firm replay endpoint + dashboard button  *(2026-05-02 15:31 UTC)*

Shipped per [runbooks/deploy_log.md](runbooks/deploy_log.md). Each
`webhook_received` / `alert_ignored` / `would_have_placed` row in the
per-division Recent Activity panel now has a "Send to research →"
button. Click → POST `/audit/{id}/replay-research` → htmx swap renders
the firm's verdict + rationale inline. Synthesized order is marked
`extra.synthetic=True` and never reaches data_exec — purely
informational. Caught + fixed during deploy: `EngagementSpec.requesting_division`
misnaming (filed for cleanup) and an 8s default consult timeout that
was too tight for multi-expert engagements (replay path uses 60s).

---

## P5 — Rename `EngagementSpec.requesting_division` → `requesting_strategy`  *(NEW — 2026-05-02)*

**Symptom:** the `requesting_division` field on
`trading_corp/agents/research/schemas.py:EngagementSpec` actually expects
the strategy/agent slug (lord_otter, market_cypher, robinhood_pmcc,
etc.), NOT the broker-account division slug (coinbase_spot,
robinhood_pmcc, etc.). The naming caused a `ValidationError` during the
2026-05-02 15:31 UTC deploy of the manual research-firm replay endpoint
(passed `coinbase_spot` as `requesting_division` → pydantic literal_error).

**Fix sketch:** rename the field to `requesting_strategy` (or
`requesting_agent`) across:
- The schema definition
- Every call site (`run_engagement`, `consult_research_for_trade_confirmation`,
  any other `EngagementSpec(...)` constructions)
- Every audit row that includes the field in its payload (rename the
  payload key too, with a backwards-compat fallback for existing rows
  if any reader filters on it)

**Why:** prevents the same foot-shoot when more callers wire into the
engagement system. The current name suggests "broker-account slug"
which is the natural thing to pass.

**Priority:** P5 — cleanup/clarity, no functional issue once you know
the contract. Not blocking anything.

---

## ✅ DONE — 0-DTE Terminal-DTE Override: NYSE-calendar-aware time gates + P1 cycle-continuity release  *(2026-05-02 14:56 UTC)*

Shipped per [runbooks/deploy_log.md](runbooks/deploy_log.md). Original
hardcoded 15:00/15:30 ET helper at `_terminal_dte_time_release`
refactored to pull session close from `pandas_market_calendars` (NYSE)
so half-days (13:00 close → 12:00/12:30 deadline) and Friday-holiday
rotations (deadline → Thursday close) work correctly. Bundled the
P1 cycle-continuity release (mark ≤ $0.15/share AND short_leg_dte == 0
→ roll_short, regardless of time) into the same helper. Offsets +
threshold config-driven via `config/strategies.yaml:robinhood_pmcc.zero_dte`.
New `trading_corp/utils/market_hours.py` wraps the calendar with
graceful fallback. 7 existing tests refactored + 8 new (half-day,
closed-day, P1 release, P0/P1 interaction).

---

## P0 — 0-DTE positions: Terminal-DTE Override must release at 3:00 PM ET, hard close deadline 3:30 PM ET  *(ORIGINAL ENTRY — superseded by ✅ DONE entry above; preserved for context)*

**Rule (Board direction, 2026-05-01):**

For 0-DTE shorts, time-of-day GATES the Terminal-DTE Override:

- **Before 3:00 PM ET:** existing Terminal-DTE Override behavior
  applies (HOLD if inside the ATM zone, etc.).
- **At or after 3:00 PM ET:** the Override no longer applies. The
  scout must START closing/rolling 0-DTE positions immediately. The
  "free theta" argument vanishes inside the last 30 minutes — the
  market is too thin to wait, slippage explodes, and assignment risk
  becomes operationally unmanageable.
- **By 3:30 PM ET:** all 0-DTE rolls/closes must be COMPLETED. After
  3:30 the order book thins to nothing for retail-accessible
  liquidity; submitting at 3:31 risks unfilled orders going into
  expiration.

These are wall-clock gates, not market-data gates — they fire
regardless of intrinsic/extrinsic state.

**Where the rule lives:**
[trading_corp/agents/divisions/pmcc_robinhood.py:102-111](trading_corp/agents/divisions/pmcc_robinhood.py:102) (Black-Sheep Rule 7)
and
[trading_corp/agents/divisions/pmcc_robinhood.py:137-151](trading_corp/agents/divisions/pmcc_robinhood.py:137) (Standard Rule 4) —
prompt constants in `_PMCC_EXPERT_SYSTEM` / `_STANDARD_RULES`. Today
neither rule has a time-of-day clause; both just check DTE+spot.

**Proposed fix — deterministic time-of-day guard:**

Same architectural pattern as the prior Terminal-DTE backlog item
(near-zero-extrinsic release): the gate is a function of clock state,
not LLM judgment, so it belongs in deterministic Python — not in the
prompt rule corpus.

Add a `_terminal_dte_time_release(leg, now_et)` helper that returns
True when:
- `leg.short_leg_dte == 0` AND
- `now_et.hour >= 15` (3:00 PM ET — release threshold)

When True, the Override is suppressed; the action defaults to
`roll_short` (or `close_short` if no acceptable next-cycle credit).

A second helper `_terminal_dte_hard_deadline_breached(leg, now_et)`
returns True when:
- `leg.short_leg_dte == 0` AND
- `now_et.hour > 15 OR (now_et.hour == 15 AND now_et.minute >= 30)`

When True, escalate urgency to `urgent` regardless of breach tier
(any 0-DTE position past 3:30 PM ET is structurally dangerous —
emit `close_short_urgent` if a roll combo isn't placeable).

**Eastern-time conversion:** scout runs in UTC; convert via
`zoneinfo.ZoneInfo("America/New_York")` to handle DST automatically.
Add to existing time helpers in `trading_corp/utils/time.py`.

**Two candidate fixes (Board to choose):**

1. **Python guard only** (recommend). Add the two helpers + wire them
   into the existing scan path so action selection deterministically
   downgrades HOLD to ROLL when the time gate fires. Update the rule
   prompt constants with a one-line note ("Time-of-day overrides
   apply — see Python `_terminal_dte_time_release` for the
   3:00/3:30 PM ET gates") so the LLM narration mentions the gate
   when it has fired.

2. **Prompt-only update.** Add the time clause to both Rule 7 and
   Rule 4 blocks. Risk: relies on the LLM to correctly read the
   wall clock from context — and the LLM doesn't have a reliable
   way to know "now" beyond what's passed in. Strictly worse than
   (1) for a hard time-deadline rule.

**Interaction with the prior near-zero-extrinsic backlog item:**
both items make the Terminal-DTE Override more permissive in
specific situations (extrinsic-near-zero OR within the 3:00–3:30
window). They compose cleanly — either condition releases the
Override. Implement both via the same `_terminal_dte_release`
helper with all release conditions checked.

**Verification:**
- Unit test in `tests/test_pmcc_logic.py`: fixture a 0-DTE short
  inside the ATM zone, mock `now_et` to 14:59 → action stays HOLD;
  mock to 15:00 → action becomes `roll_short`; mock to 15:30 →
  urgency escalates to `urgent`.
- DST-correctness test: run the helper across a DST-transition
  date and assert the 3:00 PM ET threshold tracks the local clock,
  not UTC offset.

**Priority:** P0 — real-money operational risk. A 0-DTE position
left past 3:30 PM ET is structurally dangerous (assignment risk
materializes overnight; weekend gap risk if Friday). This is a
hard deadline, not advisory. Higher priority than the prior
near-zero-extrinsic item because the time-gate failure mode is
wall-clock-deterministic — a missed 3:30 ET cutoff guarantees a
bad outcome, whereas the near-zero-extrinsic case is just a
sub-optimal cycle.

---

## P1 — Terminal-DTE Override should release on near-zero-extrinsic, near-expiry shorts (preserve cycle continuity)  *(NEW — 2026-05-01)*

**Symptom (observed on CIFR in production dashboard, 2026-05-01):**

Expert Analysis recommends `HOLD` (93% conf) on a CIFR short call:
- Short: $18.00 strike, 0 DTE, intrinsic = $0.00, extrinsic = $0.12
- Spot: $17.75 (1.4% below strike — inside the ±1.5% ATM zone)
- Rule cited: Terminal-DTE Override (Rule 4 of the Standard rules block,
  Rule 7 of the Black-Sheep rules block) — at ≤2 DTE AND inside the
  ±1.5% ATM zone, DEFAULT TO HOLD to collect remaining theta.

The rule fires correctly on its current text — but warning #3 in the
same analysis acknowledges the consequence:

> *"After expiry today, the LEAP will be uncovered — queue an
> open_short order for next Monday's 7-DTE cycle … to restore income
> generation without gap in coverage."*

So the system tells the Board: hold today, collect $0.12 of decay,
then rebuild coverage Monday. The Board's preferred strategy on
near-zero-extrinsic terminal-DTE shorts is the opposite: ROLL NOW.
Rolling captures next week's premium AT TODAY'S TIMESTAMP, eliminates
the post-expiry coverage gap, and avoids the operational risk of
remembering to fire an `open_short` Monday morning.

**Where the rule lives:**
[trading_corp/agents/divisions/pmcc_robinhood.py:102-111](trading_corp/agents/divisions/pmcc_robinhood.py:102) (Black-Sheep block, Rule 7)
and
[trading_corp/agents/divisions/pmcc_robinhood.py:137-151](trading_corp/agents/divisions/pmcc_robinhood.py:137) (Standard block, Rule 4) —
both blocks in `_PMCC_EXPERT_SYSTEM` / `_STANDARD_RULES` prompt
constants. The rule has three release conditions today (a/b/c) — none
of which cover "extrinsic is near zero AND we're about to lose
coverage entirely."

**Proposed fix — add a release condition (d):**

> *"(d) Cycle-continuity preservation: extrinsic ≤ $0.15/sh AND
> intrinsic = $0.00 AND a viable next-cycle short exists at acceptable
> credit. Trade-off: forfeit the ≤$15/contract residual decay in
> exchange for continuous coverage and immediate next-cycle premium
> capture. Action: `roll_short` to next 7-DTE cycle."*

The threshold ($0.15/sh) is debatable; reasonable starting point is
"the residual decay is small enough that the operational benefit of
rolling now strictly dominates." Could be made configurable in
`config/strategies.yaml` as `cycle_continuity_extrinsic_threshold`.

**Two candidate fixes (Board to choose):**

1. **Edit the prompt constants only** (smallest blast radius). Add
   condition (d) to both Rule 7 and Rule 4 blocks. The LLM applies the
   new condition on the next scan cycle. Risk: relies on the LLM to
   correctly evaluate "viable next-cycle short exists" — which it can
   only know if it has chain access. May produce false-positive ROLL
   recommendations when no acceptable next-cycle credit exists.

2. **Make the cycle-continuity check deterministic.** Add a
   `_terminal_dte_release(leg, spot, mark)` helper in
   `pmcc_robinhood.py` that checks the four conditions (a/b/c/d) in
   Python; if (d) fires, downgrade `analysis.action` from `hold` to
   `roll_short`. This honors CLAUDE.md §1's deterministic-then-narrate
   principle — the rule application stays out of LLM judgment for the
   condition that's purely about (intrinsic, extrinsic, DTE) state.

Recommend (2) — same architectural rationale as the prior P1
halfway-roll item: when the rule trigger is purely a function of
already-computed numeric state (intrinsic, extrinsic, DTE, spot),
it shouldn't ride through LLM judgment. Both fixes together are
also fine: prompt-constant update for narration coverage, Python
guard for execution truth.

**Verification:** rebuild the CIFR recommendation today after the
fix; for `intrinsic=$0.00 AND extrinsic≤$0.15 AND DTE≤2` the action
should flip from `hold` to `roll_short`. Pin a regression test in
`tests/test_pmcc_logic.py` that fixtures a near-zero-extrinsic
terminal-DTE short and asserts the recommended action is `roll_short`.

**Why this is structurally similar to the prior two PMCC backlog
items:** all three (LEAP-roll-missing, halfway-rule-strike-drift,
terminal-DTE-override-too-strict) are decisions where the LLM rule
corpus and the actual order-construction path don't fully agree.
All three eventually fold into the Phase 1e `TradeConfirmation`
audit-trail, where the research firm reviews the proposed action
against the rule corpus and can `verdict="conditional"` with
`suggested_modifications`. Until then, near-term fixes need to live
in the rule constants + Python guards.

**Priority:** P1 — real-money operational gap, but not actively
losing money today (the HOLD is locally correct on the rule as
written; the cost is the operational risk of remembering Monday's
re-open). Same severity rationale as the prior two PMCC items —
escalate to P0 if Monday-re-open friction causes a missed cycle.

---

## P1 — PMCC roll: LLM analyzer is blind to recent roll history (recommends back-to-back halfway rolls)  *(NEW — 2026-05-01)*

**Symptom (observed on MSTR in production dashboard, 2026-05-01):**

Position state per the screenshot:
- Spot $178.34, short $162.50C @ 7 DTE, intrinsic $15.83 (9.7% breach)
- This $162.50 short is itself the result of a **prior halfway roll
  ~7 days ago** (the original short was much higher; rolled DOWN-and-
  out into the breach to collect a credit and reset, per the
  Major-Breach rule)
- Mark $17.80 vs original credit $5.55/contract → ~$1,225 unrealized
  loss locked in by the prior roll's close cost

Expert Analysis recommends: **another immediate halfway roll** to
~$170 strike. Rationale text only references the current spot and
strike — it does NOT reference the recent roll that JUST happened.

The user's position: this is inefficient. After a halfway roll into a
breach, the right play is usually to let the new strike collect theta
and see if MSTR whipsaws back down before triggering ANOTHER halfway
roll. Back-to-back halfway rolls within a single weekly cycle:
- Pay the bid-ask spread twice in 7 days
- Lock in the loss from the first roll AND incur a second close cost
- Forfeit a week of theta that the new short would have collected
- Are correct only if the breach has ACCELERATED past the prior roll's
  expected range — not just because the underlying is still above strike

**Root cause:** [pmcc_robinhood.py:749](trading_corp/agents/divisions/pmcc_robinhood.py:749)
— `_llm_analyze_position()` builds a rich prompt with current
intrinsic/extrinsic, ITM%, ATM-zone, terminal-DTE theta breakdown,
LEAP coverage, etc. — but it includes **zero history**. The LLM
sees a snapshot, not a story.

The infrastructure to fix this **already exists**:
- [pmcc_robinhood.py:2170](trading_corp/agents/divisions/pmcc_robinhood.py:2170)
  `_query_prior_rolls(symbol)` returns `(roll_count, net_dollars)` for
  prior filled rolls on a symbol (queries the `proposed_order` table).
- [pmcc_robinhood.py:2114](trading_corp/agents/divisions/pmcc_robinhood.py:2114)
  `_build_position_context(leg)` already calls it and stashes
  `roll_count` + `prior_credit_total` into the Telegram approval
  message context.

But none of that data flows into the LLM prompt. Telegram approval
sees the history; the LLM that PRODUCED the recommendation does not.

**Proposed fix:**

1. **Extend `_query_prior_rolls`** to also return:
   - `last_roll_ts` (most recent roll's fill timestamp)
   - `last_roll_strike_change` ($ delta — was it a roll-up, roll-down,
     halfway, etc.)
   - `days_since_last_roll` (computed from last_roll_ts)

2. **Feed history into `_llm_analyze_position`'s prompt.** Add a new
   "ROLL HISTORY" section to the prompt template:
   ```
   ROLL HISTORY (this pair):
     - Total prior rolls: 4
     - Most recent: 7 days ago, strike $190 → $162.50 (down $27.50,
       halfway-roll into breach)
     - Net credit collected from rolls: -$1,050 (debit — last roll was
       executed at a debit due to deep ITM)
   ```

3. **Add a rule clause to the prompt corpus** (Rule 6 BREACH HANDLING
   in [pmcc_robinhood.py:98](trading_corp/agents/divisions/pmcc_robinhood.py:98)):
   ```
   COOLDOWN: if a halfway roll was executed within the last N days AND
   short DTE > 2 AND extrinsic remains > X cents, prefer HOLD over
   another halfway roll. Override only if breach has ACCELERATED past
   the prior roll's projected range (e.g. spot is now > prior_roll_strike +
   prior_roll_strike_change). The expectation after a halfway roll is
   "collect theta + wait for whipsaw"; back-to-back halfway rolls in
   one weekly cycle compound slippage and lock in losses.
   ```
   Defaults: N=7 days, X=$0.50/sh extrinsic. Tunable in
   `config/strategies.yaml > robinhood_pmcc.strategy.roll_cooldown`.

4. **(Defense-in-depth) Add a deterministic guard.** Same pattern as
   the Terminal-DTE Override time-gate work shipped earlier today:
   `_recent_halfway_roll_cooldown(leg, now)` returns True when the
   prompt's COOLDOWN conditions hold. If True AND `analysis.action ==
   "roll_short"` AND it would be a halfway-style roll, downgrade to
   `hold` with an explicit warning appended to `analysis.warnings`.
   Honors CLAUDE.md §1's deterministic-then-narrate principle.

**Dependency:** the [P0 "stable pair-lifetime identifier"](BACKLOG.md)
item earlier in this file should land before this one — `_query_prior_rolls`
currently aggregates by symbol, not by `(symbol, leap_strike, leap_expiry)`.
With multi-LEAP-on-one-symbol scenarios that aggregation is wrong, and
this cooldown rule would mis-fire (or mis-suppress) on the wrong pair's
history. Could ship this first as long as the user has only one LEAP
per symbol today (true at present — confirm before shipping).

**Verification:** rebuild today's MSTR recommendation after the fix;
with the prior roll 7 days ago AND no acceleration, the action should
flip from `roll_short` (halfway) to `hold` with rationale citing the
prior roll. Pin a regression test in `tests/test_pmcc_logic.py`
fixturing a position with `_query_prior_rolls` returning a recent
halfway roll, and asserting the analyzer prompt contains the ROLL
HISTORY block + the cooldown rule fires.

**Why this is the 4th in a related series:** the prior three PMCC
backlog items (LEAP roll missing from Recommended Trade, halfway-rule
strike drift, Terminal-DTE near-zero-extrinsic release) are all
"LLM analyzer's narration disagrees with what the system actually
does." This one is different — the analyzer's narration ALSO doesn't
know about a thing it should. All four eventually fold into the
Phase 1e `TradeConfirmation` audit-trail (where the research firm
reviews the proposed action against rule corpus + history).

**Priority:** P1 — real-money correctness, currently making
suboptimal recommendations on every breach situation. Higher
operational impact than the strike-drift item because back-to-back
halfway rolls cost real dollars in slippage; the strike-drift item
costs less per occurrence. Consider P0 if telemetry shows multiple
halfway rolls within a 7-day window approved by the Board (i.e. the
suboptimal recommendation actually got executed).

---

## P1 — PMCC roll: Recommended strike ignores the halfway-rule the expert text cites  *(NEW — 2026-05-01)*

**Symptom (observed on MSTR in production dashboard, 2026-05-01):**

The Expert Analysis text correctly identifies a Major Breach (Rule 6)
and prescribes a halfway roll to a specific strike:

> *"this qualifies as a Major Breach (3-10% band) under Rule 6,
> mandating a halfway roll rather than waiting for the 2 DTE trigger.
> The halfway-roll target strike is midpoint between $162.50 and
> $175.97 = approximately $169.00-$169.25 (round to nearest listed
> strike)."*

But the **Recommended Trade** card shows a roll to **$187.50** (a
+15% strike, +6.5% above spot — a standard OTM target-delta roll, not
a halfway roll). $187.50 is well past the cited halfway midpoint;
the new short delta of 0.31 is the standard `short_call_target_delta:
0.30` from `config/risk.yaml`.

The Board's strategy on breached PMCCs is the halfway-roll-with-
whipsaw expectation that the expert text correctly cites. The
recommendation is following a different (default OTM target-delta)
strategy.

**Root cause:** [pmcc_robinhood.py:2184](trading_corp/agents/divisions/pmcc_robinhood.py:2184)
— `_propose_roll_short` selects the new short via
`_find_best_weekly(symbol, broker, target_delta=analysis.target_delta, ...)`.
The strike-selection helper picks the strike whose delta is closest
to `target_delta` (see `_select_weekly_strike` at
[pmcc_robinhood.py:384](trading_corp/agents/divisions/pmcc_robinhood.py:384)).

The LLM's `PMCCAnalysis` schema only carries `target_delta` and
`target_dte` — there's no `target_strike` or `roll_style` field. So
even when the LLM correctly applies Rule 6 in the narration, that
constraint can't ride through to the strike picker. The picker falls
back to `0.30` delta, which on MSTR weekly = ~$187.50.

**Three candidate fixes (Board to choose):**

1. **Add `target_strike` (or `target_strike_pct_above_current`) to
   `PMCCAnalysis`.** Smallest change. The LLM extraction prompt
   (around [pmcc_robinhood.py:817](trading_corp/agents/divisions/pmcc_robinhood.py:817))
   gets a new field; `_find_best_weekly` gets a `target_strike`
   parameter that, when set, overrides the delta-distance ranking
   and picks the listed strike closest to `target_strike` (subject to
   the same liquidity gate). The LLM is already computing the strike
   in the narration; this just routes it into structured output.

2. **Make halfway-roll deterministic in `_propose_roll_short`.**
   When `analysis.action == "roll_short"` AND the breach tier is
   Major or Runaway (computable from leg state without the LLM:
   `(spot - short_strike) / short_strike` ≥ 3%), compute the
   halfway strike `(short_strike + spot) / 2` deterministically and
   override `analysis.target_delta` with whatever delta that strike
   maps to. Honors CLAUDE.md §1's "deterministic-then-narrate" —
   the rule application moves out of the LLM and into Python.

3. **Defer to Phase 1e research firm `TradeConfirmation`.** Long-
   term per the design doc, the scout would build the order with
   today's logic, then call `run_engagement(TradeConfirmationScope)`,
   which reviews the action against the rule corpus and either
   confirms or returns `verdict="conditional", suggested_modifications=
   {entry_price: 169.00, rationale: "halfway-rule per Rule 6"}`. The
   webhook handler applies modifications and proceeds. This is the
   structurally correct home for "given the expert advice, what
   trade do we actually execute" — but it's gated on Phase 1e
   shipping (~3-5 hr after Phase 1c real fundamental/sentiment
   experts land).

Recommend (1) as the immediate fix — smallest blast radius, gets
the right strike on the recommendation card today, and the
structured `target_strike` field is exactly what Phase 1e's
`SuggestedModifications.entry_price` will eventually carry. (2) is
defensible-in-depth (deterministic enforcement) but adds a
second source of strike truth that has to stay in sync with the
LLM's narration. (3) is the right long-term home but doesn't ship
until 1e.

**Why this is the same shape as the prior P1 LEAP-roll bug:**
both are "expert analysis text says X, recommended trade card
does Y." The LEAP-roll item was a routing decision (single-action
dispatch should have been compound); this is a strike-selection
decision (delta-only ranking should honor a target-strike
constraint). Both will fold into the Phase 1e `TradeConfirmation`
audit-trail eventually.

**Verification:** rebuild the MSTR recommendation today after the
fix; the new short strike should be the listed strike closest to
$169.00 (likely $170.00). Pin a regression test under
`tests/test_pmcc_logic.py` that fires Rule 6 conditions and
asserts the new short strike is within ±$2.50 of the halfway
midpoint, not at the 0.30-delta default.

**Priority:** P1 — real-money correctness gap. Same severity
rationale as the LEAP-roll item: warnings + analysis text are
visible, but a distracted approval gives the user a roll they
explicitly didn't choose. Escalate to P0 if telemetry shows
anyone clicking Approve while the analysis text and recommended
strike disagree by more than 5%.

---

## P1 — PMCC drilldown: Recommended Trade omits the LEAP roll when both legs need to roll  *(NEW — 2026-05-01)*

**Symptom (observed on RIOT in production dashboard, 2026-05-01):**
Expert Analysis correctly identifies that BOTH legs need to roll —
- Top-line action: `ROLL SHORT` (93% conf)
- Warning #1: *"LEAP has only 48 DTE — well below the 120 DTE roll
  threshold; roll_leap action is critically overdue and should be
  executed simultaneously or immediately after rolling the short to
  avoid naked short exposure on an expiring LEAP."*
- Warning #2: *"LEAP delta of 1.00 triggers the Hard Rule: treat as
  deep ITM equity — the LEAP must be rolled to a later expiry (e.g.,
  Jan 2027 or Jun 2027) at a higher strike to restore delta to the
  0.55–0.80 acceptable range and rebuild time value."*

But the **Recommended Trade** card renders ONLY the short roll
(`Buy to close $17.50C 0d / Sell to open $40.00C 14d`, net debit
−$920). No LEAP leg appears. If the Board taps *Approve & Execute*,
the user gets exactly what the card shows — the short rolled out 14d,
LEAP still expiring in 48d at delta 1.00, exactly the naked-short
exposure that warning #1 said to avoid.

**Root cause:** `PMCCAgent.propose_orders_for_pair`
([trading_corp/agents/divisions/pmcc_robinhood.py:889](trading_corp/agents/divisions/pmcc_robinhood.py:889))
dispatches on a single `analysis.action` string. The LLM analyzer
labelled this case `roll_short`, so the propose function only ran the
`_propose_roll_short` branch — even though the analysis text and
warnings describe a `roll_both` situation. The existing `roll_leap`
action ([pmcc_robinhood.py:956](trading_corp/agents/divisions/pmcc_robinhood.py:956))
DOES already build a compound roll (close short + close LEAP + open
new LEAP + open new short), so the building blocks exist; the
analyzer just isn't routing here when warning #1 fires.

**Two candidate fixes (Board to choose):**

1. **Promote action to `roll_leap` when the LEAP Hard Rule fires.**
   Smaller change: the existing `roll_leap` branch already handles
   the compound case. The fix is in whichever node decides
   `analysis.action` — when LEAP delta ≥ 0.95 OR LEAP DTE < 120 AND
   the short is also being rolled, emit `roll_leap` instead of
   `roll_short`. The user-facing label up top changes to "ROLL LEAP",
   and the Recommended Trade card naturally gets all four legs.
   Risk: the label "ROLL LEAP" may understate that the short also
   gets rolled. Mitigation: rename the user-facing label to "ROLL
   PAIR" or similar.

2. **Add a `roll_both` action that explicitly composes both rolls.**
   Bigger change: new action string, new dispatch arm in
   `propose_orders_for_pair`, new prompt guidance for the analyzer.
   Surfaces the compound nature in the action label itself. More
   honest for the dashboard but more code surface to test.

Recommend (1) — reuses the working `roll_leap` compound path and just
fixes the routing decision.

**Verification:** the same RIOT scenario today should produce a
4-leg recommendation: close $17.50C / close $5.00C LEAP / open new
LEAP at higher strike + later expiry / open new short on the new
LEAP. The wait-vs-roll scenario table should reflect the LEAP's
intrinsic when computing close costs. Pin a regression test under
`tests/test_pmcc_logic.py` that fires the LEAP Hard Rule and asserts
4 legs.

**Priority:** P1 — real-money correctness gap. The Board can spot
the missing LEAP roll today by reading the warnings, but a
distracted approval click on the partial recommendation leaves a
known naked-short exposure that the analyzer itself flagged. P1 not
P0 only because the warnings ARE rendered prominently and the Board
has historically read them; if approval-flow telemetry shows anyone
ever clicking Approve while warning #1 is active, escalate to P0.

---

## ✅ SUPERSEDED — PMCC dynamic watchlist research agent  *(by research firm Phase 1a, see planning/research_firm_design.md)*

**Background:** `config/strategies.yaml > robinhood_pmcc.scout.universe`
is currently a hardcoded list (NVDA, TSLA, AAPL, MSFT, AMD, MSTR,
HOOD, MARA, RIOT, ASTS, RKLB, SMR). No process for adding/removing
names as market conditions shift, your thesis evolves, or new
high-IV high-liquidity names emerge (e.g. recent IPOs, sector
rotations).

**The work:** a research agent (or team of agents) that periodically
proposes watchlist updates:

1. **Screener agent.** Scans the broader market for names matching
   the strategy's underlying criteria — high IV30, weekly options
   liquidity (open_interest, volume, bid-ask spread), market cap
   floor, no upcoming earnings within N days, sector diversity. Uses
   yfinance / Polygon / IBKR-screen (free or paid data sources).

2. **Thesis-validation agent.** Takes the screener's top candidates
   and applies a qualitative gate using Claude — "Is this a fit for
   aggressive PMCC strategy? What's the macro thesis? Risk
   indicators?" — outputs a 1-paragraph thesis per name.

3. **Allocation agent.** Looks at the existing universe + the
   proposed additions + current positions, recommends specific
   add/remove decisions to the Board (you), with a delta from
   current state. e.g. "Drop AMD (no movement in 3 weeks), add NBIS
   (new IPO, 80% IV, monthly options liquid)."

4. **Cadence:** weekly cron, output goes to a Telegram message + a
   `data/watchlist_proposals/{date}.md` file. Board reviews and
   approves changes via a `/watchlist <add|drop> <symbol>` command
   that edits `strategies.yaml` and reloads the agent.

**Implementation notes:**
- Reuse the LangGraph orchestration pattern already in place for
  CEO + risk + scout. Each of the 3 agents is a node.
- Data sources: yfinance for OHLC/IV (already used elsewhere); maybe
  Polygon free tier for options chain liquidity; news API for thesis
  context.
- Cap candidate output at ~20 names per cycle to keep the cost
  bounded (Claude calls per name).
- Should NOT auto-apply changes — always Board-approval-required.

**Acceptance:** running the weekly cron produces a coherent proposal
document with 3-5 add candidates + 0-2 remove recommendations, each
with a one-paragraph thesis. Board can apply changes by Telegram
command.

**Priority:** P2 — nice to have; current 12-name watchlist works
fine for now. Worth picking up after auto-execute is on (when the
strategy is actively trading and watchlist freshness matters more).

---

## P2 — Market Cypher: add bear-bias backup if Blood Diamond too rare  *(NEW — 2026-04-30)*

**Context:** the Market Cypher agent's bias derivation is asymmetric by
design — `Longema` on 1D sets bias=bull (early, single-signal), and
`Blood Diamond` on 4h sets bias=bear (decisive, multi-signal stack).
The asymmetry is deliberate: catch bull regimes early, exit bear
regimes decisively without whipsawing on lone Red X / Yellow X events.

**The risk:** Blood Diamond requires Red X + Red Diamond stacked. In
quiet or chop markets that combination may be too rare. If we go
through a real bear regime where Blood Diamond never fires (e.g.,
slow bleed without obvious capitulation), the agent will hold a
stale bull bias for too long and keep treating downward action as
"counter-trend" rather than "regime flip".

**Phase 2 fix:** also accept `−RBD` (Regular Bear Divergence) on the
1D timeframe from NotMC-B as a bias=bear setter. Daily-TF strong
bearish divergence is a legitimate regime-change marker. The
asymmetry stays — bull bias still has Longema as a single trigger;
bear bias gets either Blood Diamond (4h) OR `−RBD` (1D), whichever
fires first.

**How to know when to ship this:** look at audit log entries after
~30 days of live operation. Specifically:
- How many times did Blood Diamond fire on 4h vs −RBD on 1D?
- Were there extended periods where Cypher held bull bias while BTC
  was clearly declining? (compare bias state to price drawdown)
- Any cases where the agent kept treating bull-aligned signals as
  tier-eligible while the actual regime had flipped?

If yes to any of those → ship the −RBD bias-setter. If Blood Diamond
turns out to fire every couple weeks naturally, leave it alone.

**Where to wire it (when picked up):**
- `trading_corp/agents/strategies/market_cypher.py` — add to the bias
  state-update logic (mirror of `_refresh_state_from_signal` in Otter
  but with Cypher's signal vocabulary)
- The signal name should arrive as `mc_b_div_bear_strong_1d` (note
  the `_1d` suffix — same `−RBD` source on 4h is just a tier trigger,
  NOT a bias-setter, so they need distinct signal names)
- Test: feed `−RBD on 1D` event → verify state.bias flips to "bear"
  AND persists across restart via the `agent_state` table

**Priority:** P2. Not blocking — strictly a refinement of bear-side
responsiveness once we have data on how the asymmetric design
actually behaves in the wild.

---

## P1 — Real SMTP for Authelia notifications  *(NEW — 2026-04-30)*

Authelia is currently configured with the **filesystem notifier** —
verification codes for security-sensitive actions (TOTP re-enrollment,
password reset, etc.) get written to
`/var/lib/authelia/notification.txt` on the VM instead of being emailed.
Reading those codes requires SSH access, which is fine for the bootstrap
TOTP enrollment but unworkable for ongoing operations (e.g. if you're
out of town and need to add a new device).

**Fix**: configure Authelia's SMTP notifier in
`/etc/authelia/configuration.yml`:

```yaml
notifier:
  smtp:
    address: 'submissions://smtp.example.com:465'
    username: '...'
    password: '...'  # store via _FILE env var
    sender: 'auth@jacksumner.com'
    subject: '[Authelia] {title}'
```

Reasonable provider options (transactional, cheap):
- **AWS SES** — already in the Azure-adjacent cloud world; costs
  $0.10/1k emails; some sandbox restrictions until verified.
- **SendGrid free tier** — 100/day free, simple API key.
- **Resend** — modern dev experience, free up to 3k/mo, good DX.
- **Mailgun** — battle-tested, free up to 100/day.

Whichever provider you pick, the SMTP password should land in Azure
Key Vault as `AUTHELIA-SMTP-PASSWORD`, then be exposed to the Authelia
systemd unit via the `_FILE` env var convention (a small wrapper that
fetches from KV at boot, or the broader systemd-creds approach).

**Acceptance:** trigger any Authelia password-reset / TOTP-re-enroll
flow and receive the verification email at the address configured in
`users_database.yml` (currently `jack@jacksumner.com` — also need to
make sure that address actually receives mail; jack is on Yahoo, so
either point a `jack@jacksumner.com` MX → Yahoo via forwarding, or
change the email field in the user database to the Yahoo address
directly).

**Priority:** P1. Not blocking, but blocks self-service security-
operation recovery, which is a real risk if this becomes the
primary auth gate for live trading.

---

## P2 — 5 PMCC scan tests failing on liquidity gate  *(NEW — 2026-04-30)*

`tests/test_pmcc_logic.py` has 5 failing tests, all caused by mock
broker fixtures producing option chains that fail the agent's liquidity
gate (open_interest / volume / bid-ask spread). Specifically:

- `test_scan_proposes_open_pmcc_for_stock`
- `test_scan_open_pmcc_orders_share_pair_id`
- `test_scan_proposes_weekly_for_uncovered_leap`
- `test_scan_proposes_roll_at_21_dte`
- `test_scan_rolls_existing_pmcc_in_options_only_account`

Logs say _"no liquid LEAP contracts for AAPL"_ and _"no liquid weekly
contracts"_ — the test fixtures are constructing chains with
1 candidate each that fails the agent's liquidity threshold. Likely
caused by a tightening of the liquidity gate in `pmcc_robinhood.py`
without a matching update to the test fixture's mock data.

**Fix:** add `open_interest`, `volume`, `bid`, `ask` fields to the
`_call(...)` test factory (probably `tests/test_pmcc_logic.py:_call`)
so they default to values that pass the gate. Or (better) read the
gate threshold from `pmcc_robinhood.py` and parametrize the fixture
to it so the tests don't decouple from the agent.

**Priority:** P2 — these aren't blocking anything live, but a green
test suite is hygiene. Do during a quiet pass.

---

## P1 — Fidelity broker: read-only + analysis on Azure VM  *(SCOPE-NARROWED — 2026-04-30)*

**New scope (decided 2026-04-30):** Fidelity acts like Robinhood PMCC for
*analysis* (positions display, Expert Analysis ingestion, recommended-
roll suggestions, strike/expiration calls) but **stops short of order
placement**. User makes Fidelity trades manually in their UI. Future
state may move the Fidelity account to Robinhood entirely; until then
this is a read+analyze division, not an autonomous executor.

The autonomous-execution scope (placing rolls/opens/closes via Playwright)
is split out as a separate deferred item — see "Fidelity options ticket
flow (deferred autonomous execution)" below.

**Tonight's blocker that's still real:** trading_corp on the Azure VM
can't log into Fidelity at all. Every credential-submit attempt is
rejected by Fidelity's anti-bot layer with their generic _"Sorry, we
can't complete this action right now"_ page within ~3 seconds.

Per the OSS survey on 2026-04-30 (see
`kennyboy106/fidelity-api` and the playwright/Akamai community
write-ups): **datacenter IPs get flagged at the network layer before
any JS runs.** No stealth plugin fixes this. The path forward is:

1. **Residential proxy is required, not optional.** Sign up for IPRoyal
   or Bright Data free trial. Wire `proxy={"server": ..., "username":
   ..., "password": ...}` into `_make_context()` (`fidelity.py:791`).
   Cost: ~$15-50/mo at our bandwidth profile.
2. **Steal `kennyboy106/fidelity-api`'s login + stealth code.** Their
   `fidelity/fidelity.py` and `fidelity/account_info.py` are actively
   patched against live Fidelity (last commit 2026-04-08). Specifically
   their `stealth_sync` setup with `navigator_languages=False,
   navigator_user_agent=False, navigator_vendor=False` (don't override
   UA/vendor — those overrides are themselves detection signals), Firefox
   launch flags `--disable-webgl --disable-software-rasterizer`, and
   their `get_by_label` / `get_by_role` selector strategy that survives
   UI churn better than CSS selectors.
3. **TOTP via `pyotp`** if Fidelity still offers authenticator-app
   enrollment. Capture the secret at enrollment, store as
   `FIDELITY-TOTP-SECRET` in Key Vault, generate codes programmatically.
   If Fidelity has moved to passkey-only (like Robinhood did), fall
   back to SMS-HITL via Telegram (Authelia-style: paste the code into
   a chat prompt during login).

**Verification when re-attempting:**
- `/prgw/digital/signin/retail` rejection URL → still bot-flagged
- `/prgw/digital/2fa/*` URL → got past anti-bot, now in MFA
- `/ftgw/*` stable URL → fully through, broker connected

**Don't hammer it.** The broker explicitly logs _"wait 5-10 min before
retry"_ on rejection. Each rejection burns Fidelity's tolerance for
this IP. Don't restart trading-corp repeatedly while debugging.

**Tonight's progress that stays:**
- systemd unit's `PrivateTmp=true` + xvfb-run wrapper works for any
  headed-browser broker. Don't undo that.
- `data/fidelity_session/storage_state.json` exists locally on the
  laptop. Migrate to KV-stored cookies once VM login works.

**Priority:** P1. Not blocking but delivers real dashboard value
(positions + Expert Analysis text feeding the agent's roll suggestions).
The local laptop setup continues to work for development. Estimate:
~3-4 hrs once we have a residential proxy provider chosen.

---

## P3 — Fidelity options ticket flow (deferred autonomous execution)  *(NEW — 2026-04-30)*

**Status:** explicitly deferred. Fidelity is read-only + analysis until
further notice; user places trades manually. Goal of this item: when
the time comes, build the *order placement* layer on top of the
read-only broker.

**Why deferred:** the OSS survey on 2026-04-30 found **no public OSS
project automates Fidelity options trading**. Equity-only automation
is a solved space (see `kennyboy106/fidelity-api`); options ticket
flow is unmapped territory. The multi-step UI (pick strategy → fill
ticker → strikes/expiries → qty/price → review → submit) plus
verification of the fill is a real engineering effort, and the failure
modes are higher-stakes than for read-only operation. Combined with
the user's openness to migrating the Fidelity account to Robinhood
entirely, building this now is premature optimization.

**What this item would entail when picked up:**

1. **Map the options ticket DOM.** Manually click through every step
   of placing a sample option order (single-leg + multi-leg roll).
   Capture selectors, page transitions, any modals. Document in a
   markdown spec before writing code.

2. **`place_option_order(legs, qty, limit_price, ...)` method on
   `FidelityBroker`.** Cover single-leg open/close + 2-leg roll.
   Use `kennyboy106`'s selector philosophy (label/role over CSS).

3. **Order verification.** After submit, navigate to order history,
   parse the new row, store the Fidelity order ID. Match against
   intent (was the limit price right? qty right? strikes right?).
   On mismatch, alert + halt.

4. **Mid-trade failure detection.** Each step gets `_screenshot_on_error()`
   that dumps PNG + HTML + URL to `data/fidelity_session/last_error/`.
   Distinct exception types per failure mode (selector miss, captcha,
   modal blocker, network timeout, fill rejection). Each gets a
   distinct Telegram alert.

5. **Process-global "Fidelity halt" flag.** On any execution-path
   failure, prevent new orders until manually cleared. Don't retry
   blindly — a half-completed roll has one leg open.

6. **Dry-run mode.** A `would_have_submitted` flag at the broker level
   that runs the entire automation chain except clicking final Submit.
   Required for ~5 days of paper-testing on real positions before
   real submits are allowed.

7. **Strategy-level discipline.** When this ships:
   - HITL-on-every-order for the first ~30 trading days, even if
     Robinhood is on auto-exec.
   - `risk.yaml` override: `fidelity_*: per_trade_max_pct: 0.5%` vs
     1.5% global, until track record earned.
   - Daily 5pm reconciliation: pull Fidelity positions, compare to
     trading_corp DB, alert on drift > 1 contract or > $50.

**Estimate when picked up:** 6-10 hrs of dev + ~5 days of paper-test
soak + ~30 days of HITL trading before auto-exec. Multi-week project.

**Trigger to revisit:** user decides Fidelity is staying long-term
(not migrating to Robinhood) AND read-only + analysis has been
working reliably for 30+ days AND there's a specific strategy the
agent runs better than user judgment.

**Priority:** P3 (explicitly deferred). Don't pull this forward
without an explicit user trigger.

---

## P3 — Differentiate "expected" vs "real" `broker_fallback_to_paper` audit rows  *(NEW — 2026-05-01)*

**Symptom:** every trading-corp restart writes 3 `broker_fallback_to_paper`
audit rows for `fidelity_joint` / `fidelity_individual` / `fidelity_401k`.
Fidelity is formally read-only/advice-only with manual trades (per the
P1 + P3 items above) — these failures are expected, but they're
**indistinguishable in the audit log from a REAL unexpected failure**
like yesterday's Robinhood token-path issue (2026-04-30 17:54 + 18:16
+ 18:20). When the next genuine broker failure happens, it'll be
buried under 3 lookalike-but-expected Fidelity rows.

**Fix:** add a new audit kind (or a `payload.expected: true` flag) for
divisions formally declared offline. Concretely in
[trading_corp/agents/data_exec.py](trading_corp/agents/data_exec.py)'s
broker-bootstrap branch:

- Read a known-offline list from config (e.g.
  `config/divisions.yaml` adds a `fallback_expected: true` flag per
  division, default false).
- When connect fails AND `fallback_expected=true`: write
  `broker_known_unavailable` (or keep `broker_fallback_to_paper` but
  set `payload.expected=true`).
- When connect fails AND `fallback_expected=false`: write the existing
  `broker_fallback_to_paper` (= "this is a problem, look at it").
- Dashboard's audit-log surface filters/de-emphasizes the expected
  variant by default.

Estimate: ~30-line code change in `data_exec.py` + a config knob
+ a dashboard filter line. ~1-1.5 hr.

**Priority:** P3 — diagnostic-quality improvement, no real-money
impact. Pull it in when investigating any audit-log noise complaint
(or when adding a similar formally-offline division for any reason).

---

## P3 — Coinbase Spot drilldown: promote Recent Activity, demote Manual Trading  *(NEW — 2026-05-01)*

**Problem:** the Coinbase Spot drilldown
([trading_corp/web/templates/division.html](trading_corp/web/templates/division.html))
inherits the generic two-column drilldown layout where the right rail
holds the prominent sticky "Expert Analysis" panel
([division.html:240-261](trading_corp/web/templates/division.html:240)).
That panel is meaningful on the PMCC drilldown (click a position →
analysis populates) but is **dead weight on Coinbase Spot** — no
PMCC pairs to click, no per-position analysis to render.

Meanwhile in the left column the order is:
1. Stat cards
2. Positions (PMCC pairs / stock holdings)
3. **Manual order entry** ([division.html:170-177](trading_corp/web/templates/division.html:170)) — Coinbase-Spot-only
4. **Recent activity** ([division.html:179-224](trading_corp/web/templates/division.html:179))

For day-to-day Coinbase Spot use, Recent Activity (recent fills,
webhook events, halts) is the most-checked surface — it should be
the visually prominent panel. Manual Trading is used for pipeline
testing of new signals; valuable but not daily.

**Fix:** for `view.division.slug == "coinbase_spot"`, restructure
the layout so:
- **Recent Activity moves to the right rail**, taking the slot
  currently held by the (empty-on-Coinbase) Expert Analysis sticky
  panel. Same sticky-top + max-height treatment so the activity
  stream stays visible while scrolling positions.
- **Manual Order Entry moves to the bottom of the left column**,
  below Positions and below where Recent Activity used to be. Stays
  available, just out of the primary scan path.
- Pseudo-layout in `division.html`:
  ```jinja
  {% if view.division.slug == 'coinbase_spot' %}
    {# left col: stats → positions → recent activity stub link →
       (last) manual order entry #}
    {# right col: recent activity (sticky) instead of expert analysis #}
  {% else %}
    {# existing layout for PMCC and others #}
  {% endif %}
  ```

Long-term Manual Order Entry may be removed entirely — it was built
for real-trade pipeline testing during Phase B (Coinbase Spot
bring-up). Once Otter and Cypher are auto-executing organically,
the Manual path's only remaining use is opportunistic one-off Board
trades. Don't remove yet — just deprioritize position.

**Estimate:** ~30-min Jinja restructure. No agent / data-shape
changes (Recent Activity already in `view.recent_activity`, Manual
Order partial unchanged). One screenshot before/after for visual
review.

**Priority:** P3 — UX polish, no functional change. Pull in next
time someone touches the drilldown templates.

---

## P3 — Migrate `FidelityBroker` onto a `ReadOnlyBroker` ABC  *(NEW — 2026-05-01)*

**Status update on CLAUDE.md §7's pending sharp edge:** the doc says
the `FidelityBroker → ReadOnlyBroker` ABC migration was waiting on
either ticket-flow ship OR formal deferral. As of 2026-05-01 the
**formal deferral has happened** (P3 "Fidelity options ticket flow"
explicitly deferred + the user has decided Fidelity is read-only +
advice-only with manual trades indefinitely). The migration condition
is met.

**The migration:** introduce a `ReadOnlyBroker` ABC in
[trading_corp/brokers/base.py](trading_corp/brokers/base.py) that
exposes only `connect` / `disconnect` / `snapshot` / `quote` (no
`place_order`, no `cancel_order`). Rebase
[trading_corp/brokers/fidelity.py:FidelityBroker](trading_corp/brokers/fidelity.py)
onto it. The `place_order` / `cancel_order` methods get deleted, not
just stubbed — type-system enforcement that no caller can accidentally
attempt an autonomous Fidelity trade.

Knock-on changes:
- Update [main.py](trading_corp/main.py)'s `_build_broker_for_division`
  so the Fidelity branch returns a `ReadOnlyBroker` (paper-exec
  wrapping is irrelevant here — there's no exec path to wrap).
- Whatever calls `data_exec.place(...)` for fidelity_* divisions
  should fail at type-check time, not runtime. If that's any code
  path today, those callers need to either skip Fidelity divisions
  explicitly or be unreachable.
- CLAUDE.md §3 module map already documents `ReadOnlyBroker` as the
  intended pattern for read-only adapters; this just makes Fidelity
  an example instead of the migration TODO.

Estimate: ~2 hr (ABC + rebase + main.py wiring + a test that asserts
`hasattr(fidelity_broker, "place_order") is False`).

**NOTE — may become moot:** I am considering moving brokerages
because of Fidelity's active discouragement of automated trading
from their customers (the Akamai bot-block is one symptom; their
TOS language and account-freeze risk for automated logins is the
deeper concern). If I migrate the Fidelity account to Robinhood (or
another broker that tolerates automation), this backlog item is
**unnecessary** — `FidelityBroker` would be deleted entirely along
with the `fidelity_*` divisions. Don't pull this forward until the
brokerage decision is settled.

**Priority:** P3 — type-safety hygiene only, no functional change.
Conditional on Fidelity staying long-term.

---

## ✅ DONE — Auth portal in front of trading.jacksumner.com  *(2026-04-30)*

**Shipped:** Caddy + forward_auth + Authelia in production on the Azure
VM (CLAUDE.md "behind Caddy + Authelia"). Recovery procedures captured
in [runbooks/auth_lockout_recovery.md](runbooks/auth_lockout_recovery.md)
covering lost-phone, forgot-password, lost-both, Authelia-down, and
SSH-unreachable scenarios. The runbook references a
`Caddyfile.pre-authelia.bak` backup taken 2026-04-30, confirming the
flip date.

**Open follow-ups carried in their own items:**
- "Real SMTP for Authelia notifications" (P1, below) — TOTP enrollment
  + password-reset emails currently dump to `/var/lib/authelia/notification.txt`
  rather than send.

---

## ✅ DONE — PMCC dashboard short-leg P&L math is wrong  *(2026-04-30)*

Shipped 2026-04-30. Used the "cleanest long-term fix" path: normalized
`avg_per_share` to always-positive at construction in `web/data.py:973`,
documented the invariant on the `OptionLeg` class docstring, simplified
the P&L formula to assume positive avg. 12 regression tests in
`tests/test_option_leg_pnl.py` (long+short P&L, cost_basis sign,
unrealized_pnl_pct). Verified live on dashboard: RKLB Combined P&L now
+$4,373 (was negative pre-fix).

---

## P0 — Telegram approval message enrichment  *(PARTIALLY DONE — 2026-04-29)*

**Status:** Phase 1 shipped. `trading_corp/comms/approval_format.py` produces
rich multi-line messages for option, crypto-spot, and stock orders. Wired
into `graph/ceo_graph.py` and `comms/telegram_bot.py`. Format covers:
side, qty, strike, expiration, DTE, delta, mark, bid/ask, debit/credit
dollars, Lord Otter tier + stop + dollar risk, risk-verdict status.

**Phase 2 progress:**

1. ✅ **DONE 2026-04-30 — Position context block.** PMCC agent now
   populates `order.extra["position_context"]` on rolls and
   sell-weekly proposals. `_build_position_context(leg)` composes
   LEAP basics, mark, unrealized P&L, prior-roll history (audit-log
   query via new `_query_prior_rolls`). 12 regression tests in
   `tests/test_pmcc_position_context.py`. Days-held intentionally
   skipped — Robinhood's option snapshot doesn't expose opened_ts
   cleanly; defer if ever bites.
2. **Net-debit/credit roll-up for paired roll orders** — today, a roll
   fires as TWO separate approvals (close + open). Should fire as ONE
   approval with both legs and a Net Debit/Credit summary. Requires
   coordination at the order-emission point (PMCC agent groups paired
   orders before submitting to the graph). **Safety implication**:
   approving close + rejecting open leaves the position naked.
3. **Approve / Modify quick replies** — Telegram inline keyboard could
   include "+½ size" / "−½ size" / "limit −5%" buttons for fast modify
   actions instead of typing `/modify <id> <qty>`.

**Original problem statement** (kept for reference):

**Problem**: current Telegram approval messages are sparse to the point of
being unactionable. Example actually shipped:

```
robinhood_pmcc: BUY 1.0 RKLB (risk: within all risk caps)
order id: f61faa3f-...
Tap a button or reply /approve <id> ...
```

The Board (you) cannot make a decision with this. Missing every relevant
detail: strike, expiration, delta, debit/credit, position context.

**Target format** (one well-structured Telegram-Markdown message):

```
🎲 Approval Requested · robinhood_pmcc · 11:11 AM

📤 ROLL SHORT CALL · RKLB

   Close: -1 contract @ $30C · expires 4d (Mar 21)
          mark $1.20/sh · δ 0.65 · OTM 2%
          → debit $120

   Open:  +1 contract @ $32.5C · expires 11d (Mar 28)
          mark $0.80/sh · δ 0.32 · OTM 8%
          → credit $80

   ─────────────
   Net DEBIT: $40   (rolling for $40 to extend 7 days, raise strike $2.50)

📊 Position context
   LEAP: $25C 2026-01 · cost $5.00 · mark $7.20 · +44%
   Held 89 days · $720 unrealized · paired with this short
   This is roll #4 on this pair · prior 3 collected $185 net credit

⚙ Risk: within all caps · per-trade 0.4%
🆔 f61faa3f
[ Approve ]  [ Reject ]  [ Modify ]
```

**Scope**:

1. Find where `ApprovalRequest.summary` is built (likely
   `trading_corp/comms/telegram_bot.py:request_approval` consumer side, but
   the producer is in `trading_corp/main.py:_run_order` or `graph/ceo_graph.py`).
2. Replace the one-line summary with a structured builder that:
   - For options orders, pulls from `order.extra`: `underlying`, `expiration`,
     `strike`, `option_type`, `delta`, `dte`, `mark`, `position_effect`,
     `action`, `qty`, etc.
   - Computes per-leg dollars: `qty * mark * 100` for options.
   - Computes net debit/credit by summing legs (closes are debits when
     buying back, credits when selling). Match the sign convention
     already used in the dashboard's `_render_execute_results`.
   - For roll/pair orders, finds the sibling order and includes both legs
     in one message (today they fire as two separate approvals — should
     be ONE approval per logical roll).
   - Pulls position context: average cost basis, days held, unrealized
     P&L, prior roll history (audit log query for past `filled` events
     on the same pair).
   - Includes risk verdict's quantitative result, not just "within all caps":
     "per-trade 0.4% of $50,000 equity = $200 capped budget".
3. Use Telegram-safe Markdown only (no italic, no escaped underscores —
   see lessons learned in `web/webhooks.py:_telegram_notify`).
4. Stay under Telegram's 4096-char limit; truncate context section if
   needed but never the order detail.

**Files to touch**:
- `trading_corp/comms/telegram_bot.py` — `request_approval` and possibly
  helpers for formatting.
- `trading_corp/main.py:_run_order` — or wherever `ApprovalRequest` is
  constructed, to pass the rich context through.
- `trading_corp/agents/divisions/pmcc_robinhood.py` — needs to surface
  position context (LEAP details, prior rolls) to the order extra dict.
- New helper module probably worth it: `trading_corp/comms/approval_format.py`
  with `format_approval_message(order, context) -> str`.

**Tests**:
- Unit tests on the formatter with synthetic option orders, single-leg
  stock orders, paired roll orders, missing-context-field orders.
- Telegram parse-mode safety check (no chars that break Markdown legacy mode).

**Priority**: HIGH. Without this, no live trading can be approved with
confidence. Should land before PMCC `auto_execute=true` is even on the table.

**When to do it**: after Lord Otter alert config is complete and we've
seen 24h of real signals flow. Sometime this week.

---

## P3 — Cost-optimize tc-prod-vm away from Standard_D2s_v3  *(REVISED — 2026-05-02; original entry's plan was wrong)*

**Why**: tc-prod-vm runs on `Standard_D2s_v3` (~$95/mo). A burstable
SKU should be ~30-40% cheaper for this 24/7 intraday signal-processing
workload.

**Original plan (2026-04-30) was: request Bsv2 quota → `az vm resize
--size Standard_B2ms`. This DOES NOT WORK and is captured here so a
future session doesn't re-walk it.**

**What we learned (verified via az CLI, 2026-05-02):**
1. `Standard_B2ms` is in the **Bs v1** family, not Bsv2 — different
   quota. The Bsv2 quota request would not have unlocked B2ms anyway.
2. The subscription already has `Standard BS Family vCPUs: 0/10`
   (no Bs-v1 quota request needed).
3. **Neither x86 Bsv2 nor Bs-v1 SKUs are deployable in `eastus`** —
   `az vm list-skus --location eastus` shows only ARM-based Bsv2
   (`Standard_B2ps_v2`, `Standard_B2pts_v2`, etc.) and zero Bs-v1
   SKUs at all.
4. The portal's "Availability: Unavailable in this region" warning on
   the Bsv2 quota edit page was correct.

**Three real paths forward (pick one when revisiting):**

**A. ARM Bsv2 in eastus (`Standard_B2ps_v2`).**
- Bsv2 quota request still required (current 0/0).
- Requires rebuilding VM on arm64 Ubuntu.
- Most of the Python stack is pure Python (ccxt, robin_stocks, anthropic,
  yfinance, langchain, fastapi) → fine on arm64.
- `playwright` does have arm64 Linux builds. Fidelity (the only
  Playwright user) is already paper-fallback-only on Azure VM IP via
  Akamai bot-block — so a flakier arm64 headed Firefox does not
  regress critical paths.
- Risk: small but real around lxml / cryptography native deps; verify
  pip install resolves cleanly on arm64 before committing.

**B. Move tc-prod-vm to eastus2, x86 Bsv2 (`Standard_B2s_v2`).**
- Bsv2 quota request still required (per-region; eastus2 is also 0).
- No runtime / arch risk — same x86 Linux.
- Region migration = disk snapshot → recreate VM in eastus2 →
  reattach disk → new public IP → re-point DNS (`trading.jacksumner.com`)
  → re-issue Caddy/Let's Encrypt cert → verify Authelia. ~half-day of
  ops.

**C. Different Ds-family SKU in eastus (cheaper than D2s_v3).**
- E.g. `Standard_D2as_v5` (AMD) tends to undercut Intel D2s_v3 by
  10-15%. No quota issue, no arch change. Cheapest deferral path.
- Lower headline savings vs A/B (~$10-15/mo vs $35/mo).

**My recommendation when revisiting:** start with C (smallest blast
radius, banks half the savings instantly). A is the right end-state if
arm64 verification passes. B should only happen if A's rebuild blocks.

**Original 5-min portal-task framing was wrong; this is now half-day
to one-day work depending on path. Bumping to P3 from P0.**

If you later want Dv5/v6 sizes (newer generation), separate quota
request for `Standard DSv5 Family vCPUs` etc. Same procedure.

---

## P1 — Lord Otter Phase 1.5 (equity-aware sizing + real stops)

Current Phase 1 placeholder: `qty = $50 × tier_factor / price`. Tiny on
purpose so first live alerts can't accidentally place a giant order.

Phase 1.5 wires real sizing:

1. **Equity-aware notional**: agent calls `broker.snapshot()` to get current
   equity, then `notional = equity × tier_size_pct`. So Premium tier on
   $92k equity = $2,760 → ~0.036 BTC at current price.
2. **Stop loss attachment**: agent computes stop level (swing low primary,
   ATR(14)×1.5 fallback) and stashes in `order.extra['stop_price']`. The
   broker / executor reads it and places a stop order alongside the entry.
   Or, simpler v1: the agent itself opens a polling task that watches
   price and emits a market exit when the stop is breached.
3. **Close-existing-longs for bear signals**: in long-only mode, bear
   signals currently log-and-skip. They should instead emit a SELL of
   the current BTC holding (full or fractional based on tier). Use
   `broker.snapshot().positions` to discover qty held, size the close.
4. **Profit target tracking**: scale-out 50% at 1R, trail the remainder.
   Same polling loop as the stop.
5. **Win/loss feedback into halt counters**: hook fill events back into
   `LordOtterAgent.record_loss()` / `.record_win()` so the consecutive-loss
   and daily-loss halts actually fire.

**Priority**: HIGH but blocked by needing real signal data first. Can't
calibrate stops/targets without seeing real win/loss distribution.

**When to do it**: after 1-2 weeks of paper-mode alerts have accumulated
in audit log → run analysis to set actual stop multipliers.

---

## P2 — Cloudflare Tunnel with named domain

Replace cloudflared quick tunnels (URL changes every restart) with a
named tunnel pointed at e.g. `trading.yourdomain.com`. One-time setup:

1. Buy domain at Cloudflare Registrar (~$10/yr at-cost)
2. `cloudflared tunnel login` (browser flow)
3. `cloudflared tunnel create trading-corp`
4. Add CNAME via `cloudflared tunnel route dns ...`
5. Run as Windows service via `cloudflared service install`

**Priority**: MEDIUM. Removes the daily friction of "URL changed, update
all 18 TV alerts." Should land before Hetzner deploy because the same DNS
will work there.

---

## P3 — Authentication (Sign in with Apple)

Currently the dashboard has zero auth. Anyone with the URL can place
orders. Acceptable while the URL is volatile cloudflared, NOT acceptable
once it's a stable public URL.

Pattern: Sign in with Apple → JWT cookie → middleware checks cookie on
all routes except `/webhook/*` (which has its own shared-secret auth).

**Files to touch**:
- New `trading_corp/web/auth.py` — Apple ID flow + JWT validation.
- `trading_corp/web/routes.py` — middleware that gates everything except
  webhooks and login routes.
- `trading_corp/web/templates/login.html` — minimal login page.
- `.env` — Apple service ID, key ID, team ID, private key path.

**Priority**: HARD GATE before any public-facing deployment. Cannot ship
to production without this.

**When to do it**: paired with #2 (Cloudflare named tunnel) since both
unblock public hosting.

---

## P4 — Hetzner deployment

Move from local laptop to Hetzner CX22 ($5/mo, Ashburn region).

Specifically:
- Provision CX22 in Ashburn
- Harden: SSH keys only, ufw, fail2ban, unattended-upgrades
- systemd unit for trading-corp + cloudflared
- Caddy reverse proxy (auto Let's Encrypt — only needed if we move OFF
  Cloudflare Tunnel; tunnel-only routing makes Caddy optional)
- Healthchecks.io free tier integration
- Nightly DB backup to Backblaze B2
- Deploy script: `git pull && systemctl restart trading-corp`

**Priority**: MEDIUM. Worth doing after Lord Otter validates.

**When to do it**: once auto_execute is on the table for any strategy.

---

## P4 — Research firm: minimum-coverage quorum gate for TradeConfirmation  *(NEW — 2026-05-01)*

Phase 1e's `synthesize_trade_confirmation` deterministic path emits a
`confirm` verdict whenever fewer than all valid experts lean against the
proposed direction. If 2 of 3 experts refused (no data) and the single
valid expert leans bullish, we still confirm — based on one signal.

Acceptable for now (the existing risk gate + HITL is the safety net,
per design's "advice, not a gate" framing), but worth considering a
"minimum coverage" rule before live wiring lands. Options:

- Hard rule: if `data_sufficiency=True` count < N (e.g. 2), force
  verdict=confirm with an explicit `coverage_floor` risk_flag — making
  the gap visible without changing decisions.
- Soft rule: emit `confirm` but add `low_expert_coverage` to
  `risks_flagged` so the audit + dashboard can filter on it.
- No-op: leave as-is, document in design doc that low-coverage runs
  are silently treated as confirm-bias.

**Trigger to revisit**: once `auto_execute=true` is on the table for
either Otter or Cypher (then a 1-expert confirm is actually risky,
not just an audit gap).

**Where**:
- `trading_corp/agents/research/synthesis/trade_confirmation.py`
  `_deterministic_verdict`
- Possibly extend `config/research.yaml` with a `trade_confirmation:
  min_valid_experts: 2` knob

---

## ✅ DONE — Research screen: humanize "Engagement latency" panel column labels  *(2026-05-02)*

The Research screen's `Engagement latency` panel (Q11 — durations from
`engagement_started_ts` / `engagement_completed_ts`) used raw
identifier-style column headers: `product_type`, `asset_class`, `N`,
`P50 (s)`, `P95 (s)`, `P99 (s)`. Fine for engineers, not great as a
Board-facing dashboard surface.

**Renames applied (local only):**

| Before        | After          |
|---------------|----------------|
| `product_type` | `Product`      |
| `asset_class`  | `Asset Class`  |
| `N`            | `Samples`      |
| `P50 (s)`      | `Median (s)`   |
| `P95 (s)`      | `P95 (s)`      |
| `P99 (s)`      | `P99 (s)`      |

**Code state — sitting on local working tree, NOT deployed:**
- [trading_corp/web/templates/research.html:42-47](trading_corp/web/templates/research.html:42)
  — collapsed Engagement latency table headers relabeled.
- [trading_corp/web/templates/research.html:81-86](trading_corp/web/templates/research.html:81)
  — same renames inside the "Weekly P95 time-series (Refinement 5 —
  drift detection)" collapsible. Also capitalized `week` → `Week` so
  the row doesn't read mixed-case next to `Product` / `Asset Class` /
  `Samples`.

Pure template text — no route or view-model changes.

**To finish this item:**
1. Deploy the template changes to prod (Azure VM).
2. Add an entry to `runbooks/deploy_log.md` per the deploy-gate convention.
3. Take screenshots of both views (collapsed Engagement latency table
   + expanded weekly time-series) for Board-facing acceptance.
4. Flip this heading to `## ✅ DONE — … *(YYYY-MM-DD)*`.

**Out of scope (unchanged):** the Engagements log section below the
latency panel still uses tech-y identifier strings
(`research_position_context_emitted`, `research_expert_completed`,
etc.). Scope was explicitly the Engagement latency panel only.

---

## ✅ DONE — Research screen: expand-on-click rows in Engagements log  *(2026-05-02)*

The Engagements log on the Research screen
([trading_corp/web/templates/research.html:170-190](trading_corp/web/templates/research.html:170))
shows one-line summaries: timestamp · kind · `summary` text ·
`engagement_id` short hash. Useful for scanning, but the row's actual
`audit_event.payload_json` is much richer (LLM rationale, expert
verdicts, key drivers, refusal reasons, full PositionContext bodies)
and there's no way to see it from the dashboard today — Board has to
SSH and `sqlite3` the DB, or scrape the audit log file.

**Ask:** make each engagement-log row clickable. On click, the row
expands inline (accordion) to reveal the full payload underneath.
Click again to collapse.

**Concrete shape:**
- **Behavior:** accordion, inline. Multiple rows can be open at once.
  No modal, no side drawer.
- **Content:** full `payload_json` pretty-printed in a `<pre><code>`
  block. Raw is fine for v1; future polish can curate per-`kind`
  fields, but that's explicitly out of scope here.
- **Indicator:** chevron / disclosure caret on the left edge of each
  row showing collapsed/expanded state. The row itself becomes the
  click target (entire row, not just the caret).

**Data-layer change required:**
- [trading_corp/web/routes.py:967-978](trading_corp/web/routes.py:967)
  builds the `engagement_log` dicts from `research_rows` and currently
  drops `payload_json` (only keeps the curated `summary`, `engagement_id`,
  `requesting_division`, `product_type`, `asset_class` fields). Add a
  `"payload": payload` key (or `"payload_pretty": json.dumps(payload,
  indent=2, default=str)` if rendering convenience matters). Up to 120
  rows × typical payload size — should fit in the SSR response
  comfortably.

**Template change:**
- [trading_corp/web/templates/research.html:172-188](trading_corp/web/templates/research.html:172)
  — wrap the existing `<div class="px-4 py-2 ...">` in a `<details>`
  element (free accordion behavior, no JS required) with a `<summary>`
  for the current one-liner and a `<pre>` body underneath rendering
  `{{ row.payload_pretty }}`. `<details>` is the simplest path —
  htmx not needed, no state to manage, accessible by default. If you
  want fancier styling or a JS-driven caret, drop in Alpine.js
  `x-data="{open: false}"` instead.

**Out of scope (file as separate items if desired):**
- Curated per-`kind` payload views (e.g. for `research_expert_completed`
  surface `directional_lean`, `confidence`, `key_drivers` as a tidy
  card instead of raw JSON).
- Cross-row engagement_id linking (click one row, highlight all
  siblings in the same engagement).
- Search/filter inside the engagement log.

**Acceptance:** click a row, full payload renders below; click again,
collapses. Multiple rows open simultaneously work correctly.
Performance acceptable on the current 120-row cap (no JS perf
regression on scroll).

**Code state — sitting on local working tree, NOT deployed:**
- [trading_corp/web/routes.py:23](trading_corp/web/routes.py:23) — added
  `import json` (was not previously imported in this module).
- [trading_corp/web/routes.py:1035-1044](trading_corp/web/routes.py:1035)
  — `engagement_log` dict now carries
  `"payload_pretty": json.dumps(payload, indent=2, default=str, sort_keys=True)`.
  `sort_keys=True` so the rendered JSON has a stable field order across
  reloads (easier for the eye to scan repeated kinds).
- [trading_corp/web/templates/research.html:170-194](trading_corp/web/templates/research.html:170)
  — converted the row `<div>` into `<details class="px-4 py-2 group">` /
  `<summary>` matching the thesis-library pattern directly below
  (chevron `▶` rotating with `group-open:rotate-90`). Body is a `<pre>`
  with `whitespace-pre overflow-x-auto bg-pane-2/40 border border-edge`
  — wide payloads get a horizontal scrollbar instead of wrapping.
  Parent's `divide-y divide-edge` continues to draw separators between
  `<details>` siblings; `max-h-[600px] overflow-y-auto` panel scroll
  preserved.

**To finish this item:**
1. Deploy to prod (Azure VM) per the deploy-gate convention.
2. Add an entry to `runbooks/deploy_log.md`.
3. Open one row of each terminal kind (`research_thesis_emitted`,
   `research_engagement_aborted`, `research_expert_completed`) in the
   browser to confirm payload renders cleanly with no JS errors.
4. Flip this heading to `## ✅ DONE — … *(YYYY-MM-DD)*`.

**Known visual quirk (intentional, not blocking):** the `<summary>`
inherits a redundant default browser disclosure marker on top of the
custom `▶` chevron, same as the existing thesis-library `<details>`
elements on this page. Hiding the default marker is a one-line CSS rule
(`summary { list-style: none; } summary::-webkit-details-marker
{ display: none; }`) that should be applied site-wide if/when desired —
explicitly out of scope here to keep this change purely additive.

---

## ✅ DONE — Live trade flow: expand-on-click tiles  *(2026-05-02)*

The "Live trade flow" panel on the Overview screen renders trade-flow
events as compact tiles (status pill, symbol/side/qty, optional
truncated `reason`, "Xm ago"). The underlying `audit_event.payload`
holds substantially more (full proposed-order shape, risk-cap details,
execution metadata, error tracebacks for `*_error` kinds) — none of
which is reachable from the dashboard today.

**Ask:** make each trade-flow tile clickable. On click, the tile
expands inline (accordion) to reveal the full audit payload
underneath. Click again to collapse. Apply the expand behavior
**everywhere the same partial renders**, not just the Overview screen
— see "Scope" below.

**Concrete shape:**
- **Behavior:** accordion, inline. Multiple tiles can be open at once
  (consistent with the Engagements log expand pattern).
- **Content:** raw `payload_json` pretty-printed in a `<pre><code>`
  block underneath the existing tile body. Curated per-`kind` views
  (e.g. full Phase A trade card for `would_have_placed`, risk-cap
  detail for `risk_rejected`) explicitly out of scope here — file as a
  follow-up if the raw JSON proves unwieldy in practice.
- **Indicator:** chevron / disclosure caret on the tile, or use a
  native `<details>` with the existing tile body as the `<summary>`.

**Scope — "everywhere the trade-flow partial renders":**
- [trading_corp/web/templates/partials/trade_flow.html](trading_corp/web/templates/partials/trade_flow.html)
  is the single component. It's `{% include %}`-ed from
  [trading_corp/web/templates/home.html:118](trading_corp/web/templates/home.html:118).
- Confirm via grep before implementing — if a per-division page later
  starts including the same partial, the expand behavior is inherited
  automatically (the whole point of changing the partial).

**Data-layer change required:**
- [trading_corp/web/data.py:828-864](trading_corp/web/data.py:828)
  `trade_flow()` builds the dict list from `audit_event` and currently
  drops `payload_json` after extracting curated fields (`symbol`,
  `side`, `qty`, truncated `reason`). Add a key like
  `"payload_pretty": json.dumps(payload, indent=2, default=str)` to
  each row so the template has the data on hand without a second DB
  hit.
- Default `limit` is 20 rows — small enough that pretty-printed
  payloads fit comfortably in the SSR response. The htmx
  `every 5s` refresh tick continues to swap the whole partial — open
  tiles will collapse on refresh unless the JS preserves state.
  Decision: accept the collapse-on-refresh; if it gets annoying, add
  a `localStorage` open-set keyed by audit_event row id later.

**Template change:**
- [trading_corp/web/templates/partials/trade_flow.html:13-43](trading_corp/web/templates/partials/trade_flow.html:13)
  — wrap the existing `<div class="bg-pane-2/40 ...">` body in a
  `<details>` element. The summary is the current tile content
  (status row + symbol row + reason). The body is the new
  `<pre>{{ evt.payload_pretty }}</pre>`. `<details>` gets free
  accordion + accessible disclosure; no JS needed for v1.

**Out of scope (file separately if desired):**
- Curated per-`kind` detail panels (would_have_placed → trade card;
  risk_rejected → which cap fired + value vs. cap; *_error → error +
  traceback as syntax-highlighted block).
- State preservation across the 5s htmx refresh.
- Expanding a tile to scroll the Engagements log to the matching
  engagement_id (cross-panel linking).

**Acceptance:** click any tile, full payload renders below; click
again, collapses. Multiple open tiles work. Behavior present
everywhere the `partials/trade_flow.html` partial is rendered.

**Code state — sitting on local working tree, NOT deployed:**
- [trading_corp/web/data.py:931](trading_corp/web/data.py:931) — added
  `"payload_pretty": json.dumps(payload, indent=2, default=str, sort_keys=True)`
  to the dict built in `trade_flow()`. (`json` was already imported.)
- [trading_corp/web/templates/partials/trade_flow.html:13-46](trading_corp/web/templates/partials/trade_flow.html:13)
  — converted the tile `<div>` into `<details>` / `<summary>`. Default
  browser disclosure marker suppressed via Tailwind arbitrary variant
  (`list-none [&::-webkit-details-marker]:hidden` on the `<summary>`)
  so the only indicator is the custom `▶` chevron rotating with
  `group-open:rotate-90`. This differs intentionally from the
  Engagements-log row pattern (which kept the dual marker to match the
  thesis-library precedent on the same screen) — tile UI looks weirder
  with a stray default triangle inside the styled box than a list row
  does. A future pass should normalize both with one site-wide rule.
- Both the initial paint (`home.html:118` include) and the htmx 5s
  refresh swap (`/partials/trade-flow` endpoint at
  [trading_corp/web/routes.py:121-126](trading_corp/web/routes.py:121))
  render the same partial, so this change covers both surfaces.
- Open tiles collapse on the 5s tick — explicitly accepted per spec.

**To finish this item:**
1. Deploy to prod (Azure VM) per the deploy-gate convention.
2. Add an entry to `runbooks/deploy_log.md`.
3. Open one tile of each terminal kind (`fill`, `risk_rejected`,
   `execution_error`, `would_have_placed`) in the browser to confirm
   payload renders cleanly with no JS errors and the chevron toggles.
4. Confirm the 5s htmx tick visibly collapses an open tile (matches
   accepted behavior, not a regression to flag).
5. Flip this heading to `## ✅ DONE — … *(YYYY-MM-DD)*`.

---

## P4 — Investigate: PMCC scout fired at 04:03 UTC outside the 8:30-9:25 ET scheduler window  *(NEW — 2026-05-02)*

**Symptom (logs around 2026-05-02 04:00-04:05 UTC):** PMCC scout
activity ran outside the documented daily scheduler window
(`weekdays 08:30–09:25 ET` per `_scheduled_pmcc_scan_loop` log line).
Sequence captured:
- 04:02:50 onwards — ~25 Robinhood symbol-resolution warnings
- 04:03:47 — risk_rejected for AMD
- 04:03:47 — board_approved + filled for a different AMD order
  (`via: scout_button`)

**Why it matters:** the heavy in-process work blocked the FastAPI
event loop just as TV fired its 04:00 UTC 4h-bar Cypher alerts —
TV reported "request took too long and timed out" on those alerts.
The return-fast webhook refactor (separate item) makes this no
longer load-bearing for webhook delivery, BUT the underlying
question stays: **why is scout firing at 4 AM UTC?** Should be
either documented or fixed.

**Possible causes to investigate:**
1. Cron / scheduler misconfig — second scheduler somewhere we didn't
   account for. Grep for `BackgroundScheduler` / `asyncio.create_task` /
   `apscheduler` / similar across the codebase.
2. A long-running PMCC scan that started during the 8:30-9:25 ET
   window and stretched into the next morning. Look at scan duration —
   should be minutes, not hours.
3. Manual `via: scout_button` firing — was someone (you?) clicking
   the scout button at 4 AM ET? (The audit row's `via` field literally
   says `scout_button` — that's the dashboard manual-scan button.)
4. Telegram-bot command that triggers a scout — `/scan` or similar.
   Look for commands at the same timestamp.

**Where to start:**
- `journalctl -u trading-corp --since '2026-05-02 03:55 UTC' --until '2026-05-02 04:05 UTC' --no-pager | grep -iE 'scan|scout|button|telegram|/scan'`
- audit_event WHERE actor='scheduler' AND ts BETWEEN ... — check if
  scheduled_scan_done fired (would mean the regular cron, off-window)
- audit_event WHERE payload_json LIKE '%scout_button%' AND ts BETWEEN ...
  — count how often the manual button has been clicked

**Priority:** P4 — diagnostic / documentation. The webhook return-fast
refactor handles the load-bearing user-facing impact. This item is
about understanding the box's actual behavior so future debugging
isn't surprised again.

---

## P4 — Logging: RedactingFilter mangles dict args in %-style log calls  *(NEW — 2026-05-02)*

**Symptom (caught during Phase C deploy 2026-05-02 14:51 UTC):** any
`log.info("foo: %s", some_dict)` raises `TypeError: not all arguments
converted during string formatting` and emits a `--- Logging error ---`
traceback to stderr. The dict argument is rewritten by the harness's
`RedactingFilter` (root logger) into a tuple of its keys, so the
remaining `%s` slot has more args than placeholders.

**Workaround in-tree:** f-string formatting in
[trading_corp/main.py:617](trading_corp/main.py:617) and
[trading_corp/agents/paper_trade_replay.py:295](trading_corp/agents/paper_trade_replay.py:295).
Sidesteps the filter entirely.

**Real fix:** the `RedactingFilter` (in `trading_corp/utils/secrets.py`
or wherever it lives — grep for `RedactingFilter` / `Redacting`) should
not rewrite dict-shaped log args. It should walk values for redaction
without flattening the container.

**Where**: search for `class RedactingFilter` or `class Redacting` in
`trading_corp/utils/`. Likely a `__call__` / `filter` method that
unpacks `record.args` and forgets that dict args are valid `%s` inputs.

**Not a regression** — this bug has been latent. It only surfaces when
a caller passes a dict via %-style logging, which is uncommon. Phase C
exposed it because the replay tick logs counts dicts.

**Priority:** P4 — cosmetic noise (the dict is also rendered as
something useless: just its keys), but the stderr traceback is annoying
in log scans. Not blocking any feature.

---

## P5 — Mobile-responsive layout audit

PWA scaffolding shipped. Concrete layout tightening probably needed
once you've used the iPhone install for a few days. Specific known gaps:

- Equity curve chart probably overflows on narrow viewports
- Position table on division page may need horizontal scroll wrapper
- Manual order form: button row may stack awkwardly under 380px width
- Stat cards 2-col might be too cramped at iPhone SE width

**Priority**: LOW. Functional > polished for now.

**When to do it**: after a week of phone use surfaces specific gripes.

---

## P6 — Real macro calendar fetcher

Replace `config/macro_calendar.yaml` (hand-maintained) with an automated
fetcher:
- FOMC schedule from FRED API
- CPI/NFP from BLS calendar
- Daily cron writes the same YAML shape

**Priority**: LOW. Hand-maintained YAML works fine for now. Only worth
automating once we've forgotten to update it once and gotten burned.

---

## P7 — Crypto-friendly stock holdings display

Current dashboard issues for Coinbase Spot:
- Section header says "STOCK HOLDINGS" — wrong label for crypto
- Last/Mkt Value/Unreal P&L columns blank because yfinance doesn't speak
  "BTC/USD" (yfinance wants "BTC-USD")

Fix: in `web/data.py:_fetch_prices_async`, map crypto symbols correctly
or read the values straight from `position.extra['market_value_usd']`
which the Coinbase broker already populates.

**Priority**: LOW. Cosmetic.

---

## P8 — JSON API endpoints (`/api/v1/*`)

Only relevant if we go native iOS instead of PWA. PWA works on existing
HTMX/HTML routes. Skip unless committed to native build.

---

## Items consciously excluded

- Multi-region active-active deploy — overkill for personal trading
- Kubernetes — overkill, single VPS is right
- Web push subscription flow — Telegram works fine, revisit if it stops working
- Pure-native iOS app — PWA is sufficient
- Reverse-engineering Lord Otter's signals — defeats paying for it

---

_Last updated: 2026-04-29. Prepend new items at the top of the appropriate
section. Mark items DONE rather than deleting so we have a record._
