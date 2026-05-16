# Trading Corp — Known sharp edges

## What this file is

Catalog of deliberate-but-surprising properties of the Trading Corp
codebase. Each entry is something that LOOKS like a bug or oversight
but is actually intentional, or a known soft-fail with a documented
trade-off. **Don't "fix" them without explicit Board approval** —
that's the same rule that lives in CLAUDE.md, restated here so the
file makes sense on its own.

Headings are anchored so other docs can deeplink (e.g.
`docs/sharp_edges.md#backtester-approval-gate-is-documented-but-not-code-enforced`).
A handful of these entries also have a 1-2 line summary in
CLAUDE.md itself — they're cited from working-agreement rules or
from "Things to ask before doing", and the rule needs them inline.
The remainder live only here.

## Risk + execution

### Webhook risk gate ≠ LangGraph risk gate orchestration

TV webhooks call `risk_agent.evaluate()` inline. PMCC scans + Telegram
flows go through `build_trade_graph()`. Same gate, two
orchestrations. The webhook path's `auto_execute` is a single bool;
the graph path's `auto_execute_caps` is much richer (VIX,
LEAP-debit, black-sheep, daily aggregates). Safety implication:
flipping a TV division to `auto_execute=true` today would skip the
richer caps. Harmonize before flipping (see [CLAUDE.md § 1](../CLAUDE.md)).

### Webhook risk gate falls back to equity = 100_000.0

Webhook risk gate falls back to `equity = 100_000.0` if broker
snapshot fails. Means risk caps run on a placeholder rather than
rejecting. The snapshot-failure log is the trail. Don't tighten or loosen without an audit-trail review.

### Backtester approval gate is documented but not code-enforced

[PROJECT_CONTEXT.md § 11](../PROJECT_CONTEXT.md) and
[CLAUDE.md § 6](../CLAUDE.md) say
"new strategies need backtest approval"; today the path doesn't
enforce it. Treat the rule as human-process until enforcement
lands.

## Brokers

### FidelityBroker subclasses the full Broker ABC, not ReadOnlyBroker

`FidelityBroker` subclasses the full `Broker` ABC, not
`ReadOnlyBroker`. Predates the "read-only enforced by missing
methods" rule. Migration TODO: extract a `ReadOnlyBroker` ABC and
rebase `FidelityBroker` onto it once the Fidelity options ticket
flow is either shipped (Phase 3 backlog) or formally deferred. New
read-only adapters use `ReadOnlyBroker`; don't model them on
`FidelityBroker`.

### FidelityBroker is bot-blocked from Azure VM IP

`FidelityBroker` is bot-blocked from Azure VM IP (Akamai
layer, pre-JS). Falls back to paper. Residential-proxy plan
**DEFERRED 2026-05-03**; user investigating Plaid integration
as a legitimate alternative. See P1 BACKLOG entry "Fidelity
broker: read-only + analysis on Azure VM".

### BitUnix transfer field is additive, not duplicate

BitUnix accepts the Azure VM IP fine — no anti-bot at the
network layer. Useful contrast with Fidelity. Phase 1 broker
uses SHA256-double-sign auth (no HMAC, no passphrase). The
`transfer` field in `/api/v1/futures/account` is **additive**
to total equity, NOT a duplicate of `available` (verified
2026-05-03 against the BitUnix UI: $1250 available + $1250
transfer = $2500 total). Crypto-margined balances (BTC/ETH
margin) need quote conversion to USD; stablecoins (USDT/USDC)
are summed 1:1.

### STANDBY badge is UI-only

STANDBY badge is UI-only (Coinbase Futures + BitUnix Futures
today). Setting `standby: true` in `divisions.yaml` does NOT
disable order routing or broker registration. The signal that
"this division doesn't trade live today" is enforced separately:
for BitUnix via `BitunixBroker.place_order` raising; for Coinbase
Futures it's not enforced today (still order-capable in code).

## Strategies & divisions

### pmcc_robinhood.py and fidelity_options.py conflate division and strategy

`pmcc_robinhood.py` and `fidelity_options.py` conflate
division-level and strategy-level concerns. Otter and Cypher were
carved out into `agents/strategies/` on 2026-05-02; PMCC and Fidelity
remain mixed. Future work should follow the Otter/Cypher precedent
when it becomes load-bearing — extract strategy logic from PMCC into
`agents/strategies/pmcc.py` once a second Robinhood strategy is
needed. Don't refactor speculatively.

### Strategies are agent classes, not graph nodes

Strategies are agent classes, not graph nodes (deliberate — see
[docs/ARCHITECTURE.md § 6 design decision 6](ARCHITECTURE.md)).
Pro: simple test harness. Con: can't visualize strategy internals
in graph traces.

## Storage & queries

### extra_json is unqueryable by SQL columns

`extra_json` is unqueryable by SQL columns. The trade-off:
schema-stable, strategy-specific bag, but `LIKE`-based queries
(e.g. `_query_prior_rolls` filtering on `pmcc_pair_id`) are
brittle. Accepted because most reads are full payloads.

### PMCC _query_prior_rolls aggregates by symbol, not by LEAP lifetime

PMCC `_query_prior_rolls` aggregates rolls by symbol, not by
LEAP lifetime (P0 backlog item). Multi-LEAP-on-one-symbol
scenarios silently miscount.

## Config

### Config hot-reload has no validation

Config hot-reload has no validation. Typos silently degrade.

### ceo_graph._check_auto_execute re-reads without mtime caching

`graph/ceo_graph.py:_check_auto_execute` re-reads
`strategies.yaml` every call without mtime caching. All other
agents mtime-cache. Inconsistent but not harmful.

### BitUnix scoring YAML is NOT hot-reloaded

`bitunix_futures_observer.py` receives its `ScoringConfig` once at construction (`main.py:380`) and holds it in `self.scoring_config`. Mtime-cache pattern from § 5 applies to Otter/Cypher/Kalshi/Polymarket/Donchian, NOT BitUnix. Every `strategies.yaml` edit that touches the `bitunix_futures` block requires `systemctl restart trading-corp` to take effect. Memory: `feedback_bitunix_no_hot_reload.md`.

## UI & classification

### Investment-type UI grouping is divisions-aware, not broker-aware

Investment-type UI grouping is divisions-aware, not
broker-aware. `classify_investment_type(d)` in
`trading_corp/utils/divisions.py` maps each division to
Individual / Crypto / Retirement using a small rule
(intent=retirement → retirement; broker in {coinbase, bitunix}
→ crypto; else individual). New broker families decide their
group via `_CRYPTO_BROKERS` set membership. New retirement-style
intents reuse the existing `intent: retirement` YAML field.
