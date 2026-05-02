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
