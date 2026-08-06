# Anthropic API Token-Spend Audit — GT_Jack's Trading Corp

**Date:** 2026-08-06 · **Analyst:** Claude (Opus 4.8, read-only) · **Branch:** `claude-2026-08-06b`
**Scope:** READ-ONLY. No code changes, no deploys. Findings + recommendations only; Jack decides all actions.
**Method:** Static repo inventory (`C:\Users\AA Incorporado\cc`) cross-checked against ACTUAL prod runtime on `tc-prod-vm` (SSH read-only) and real invocation logs. Every spend claim verified empirically where logs exist; assumptions flagged.

---

## Executive summary

- **Total Anthropic spend ≈ $300/month**, ~99% Claude Sonnet 4.6. Verified live via SSH to `tc-prod-vm` + real invocation counts from the engine's `audit_event` log (1.95M rows).
- **All spend is inside one process** (`trading-corp.service`). Every scheduler, timer, cron, and the other 3 running services make **zero** Anthropic calls.
- **One division is ~80% of the bill:** `kalshi_llm_arbitrage`, a **paper** forward-research observer firing **~1,300 LLM calls/day** (~$235/mo). Prior review found it at 8W/8L, **net $0.00 — no forward edge**. Its cost is a policy question, not a bug.
- **One true zombie:** `polymarket_arbitrage` — on your "closed" list but empirically **`enabled: true`** and burning **~$28/mo** in paper mode (broker can't even place orders). Clean one-line kill.
- **Everything else is ~$0:** ceo/portfolio/risk/whale on-demand & unused; research firm tiny; PMCC small but **not instrumented** (flagged). Backtester/eod_debate are wired to Opus but **dead** (no call site).
- **No runaway spend:** no retry storms, no bursts, no debug-mode hammering, no unexpected Opus usage. Duplicate-call guards (cooldowns/dedupe) are in place.
- **Caveat (R0):** the estimate assumes prompt caching is working (design strongly implies it, but `cache_read` tokens aren't logged — worth a 1-cycle verify; if broken, spend is up to ~2×).

**Fastest safe win:** kill `polymarket_arbitrage` (~$28/mo, 1 line, zero impact). **Biggest win:** decide `kalshi_llm`'s fate — pause (~$235/mo) or cooldown+downgrade (~$200/mo).

---

## STEP 1 — Anthropic call-site inventory (static, from code)

All LLM calls route through a single client: `trading_corp/agents/llm.py::build_chat_model(agent_name, max_tokens)`, which resolves the model from `config/agents.yaml` (default `claude-sonnet-4-6`). Uses `langchain_anthropic.ChatAnthropic`. Prompt caching is per-message via `cache_control: {"type":"ephemeral"}` set by callers (only 2 call sites set it).

**Model routing (repo `config/agents.yaml`):** default = `claude-sonnet-4-6`; `research_judge`/`backtester`/`eod_debate` = `claude-opus-4-7`; `polymarket_whale_analyst` = `claude-haiku-4-5-20251001`. Agent names absent from the file fall back to the default Sonnet.

| # | file:line | agent → model | max_tok | division | purpose | gated? | trigger | prompt / cache |
|---|---|---|---|---|---|---|---|---|
| 1 | ceo.py:53/90 | ceo → sonnet-4-6 | 1500 | ceo | narrate morning brief | `is_llm_available()`, optional enrich | on-demand `/brief` Telegram | small, no cache |
| 2 | ceo.py:53/118 | ceo → sonnet-4-6 | 1500 | ceo | free-form Board Q&A | same guard | every Telegram free-text msg | small, no cache |
| 3 | portfolio.py:78/86 | portfolio → sonnet-4-6 | 400 | portfolio | rebalance bullets | `is_llm_available()` | on-demand (dashboard/CEO graph) | small, no cache |
| 4 | risk.py:386/397 | risk → sonnet-4-6 | 160 | risk | 1-sentence risk paraphrase | `narrator_enabled` + `is_llm_available()` | per risk verdict (per proposed order) | small, no cache |
| 5 | polymarket_whale_analyst.py:215/229 | whale → **haiku-4-5** | 200 | whale-analyst | 2-4 sentence whale verdict | narrator flag + **$1/day cost cap** + `is_llm_available()` | on-demand HTTP `POST /polymarket/analyze/{wallet}` + CLI | small, no cache |
| 6 | pmcc_robinhood.py:859/1013 | pmcc_robinhood → sonnet-4-6 | 2048 | pmcc | JSON action/strike/DTE decision per position | `is_llm_available()` | **per-position fan-out** on 4×/day scans + `/scan` + panel re-analyze | large (>2k), no cache |
| 7 | kalshi_llm_arbitrage.py:530/575 | kalshi_llm_arbitrage → sonnet-4-6 (fallback) | 512 | kalshi_llm | calibrated YES-prob JSON per market | `is_llm_available()` + strategy `enabled` | **per-scan-cycle (~60s), fan-out up to K=20 survivors** | medium, **cache_control ephemeral** |
| 8 | polymarket_arbitrage.py:506/548 | polymarket_arbitrage → sonnet-4-6 (fallback) | 512 | polymarket_arb | calibrated YES-prob JSON per market | `is_llm_available()` + strategy `enabled` | **per-scan-cycle (~30s), fan-out up to K=10 survivors** | medium, **cache_control ephemeral** |
| 9 | research/experts/technical.py:324 | research_expert → sonnet-4-6 | 220 | research-firm | technical narration | `is_llm_available()` | per-candidate × expert fan-out (engagement graph) | small, no cache |
| 10 | research/experts/sentiment.py:490 | research_expert → sonnet-4-6 | 220 | research-firm | sentiment narration | `is_llm_available()` | per-candidate fan-out | small, no cache |
| 11 | research/experts/macro.py:266 | research_expert → sonnet-4-6 | 220 | research-firm | macro narration | `is_llm_available()` | per-candidate fan-out | small, no cache |
| 12 | research/experts/fundamental.py:379 | research_expert → sonnet-4-6 | 220 | research-firm | fundamental narration | `is_llm_available()` | per-candidate fan-out | small, no cache |
| 13 | research/experts/debate/_base.py:152 | research_expert → sonnet-4-6 | 600 | research-firm | bull/bear argument | `is_llm_available()` + debate gate | 2 calls (bull+bear) per gated engagement | small/med, no cache |
| 14 | research/experts/debate/judge.py:187 | research_judge → **opus-4-7** | 900 | research-firm | score argument quality JSON | try/except; only if debate ran | 1 call per gated engagement | medium, no cache |
| 15 | research/synthesis/trade_confirmation.py:287 | research_synthesis → sonnet-4-6 | 1200 | research-firm | confirm/pushback verdict JSON | try/except; product_type gate | per trade_confirmation engagement (no live trigger found) | medium, no cache |
| 16 | research/synthesis/position_context.py:223 | research_synthesis → sonnet-4-6 | 1000 | research-firm | position macro/sentiment JSON | try/except; product_type gate | **per symbol at startup** (`prime_all_division_position_contexts`) + per engagement | medium, no cache |
| 17 | research/synthesis/thesis.py:276 | research_synthesis → sonnet-4-6 | 1500 | research-firm | thesis JSON | try/except; product_type gate | Telegram `/research thesis` returns "not wired" — programmatic only | medium, no cache |
| 18 | research/synthesis/candidate.py:224 | research_synthesis → sonnet-4-6 | 2000 | research-firm | per-candidate thesis+fit JSON | try/except; product_type gate | `/research candidate` cmd + PMCC scout `universe_source: research_on_demand` | large, no cache |

**Dead config entries (no live call site — verified by grep):**
- `backtester` (agents.yaml → opus-4-7): `agents/backtester.py` has NO `build_chat_model`/`is_llm_available`; it's a deterministic static registry. Config entry never consumed.
- `eod_debate` (agents.yaml → opus-4-7): NO `EodDebateAgent`, no scheduler, no call site. String appears only in a `db.py` schema comment and a `logger.py` docstring. Reserved config only.

**Research Firm — wired but mostly operator-triggered (evidence in `main.py`):** built on every start (`build_research_firm_deps`, main.py:1183); the only *automatic* LLM path is `prime_all_division_position_contexts` at startup (main.py:1215, fires `position_context` per Lord Otter / Market Cypher symbol). Everything else (candidate recs, debate, thesis) is operator-triggered via `/research` or PMCC scout `research_on_demand`. `thesis` Telegram is hardcoded "not wired"; `trade_confirmation` has no live trigger.

> ⚠️ **These are CODE facts.** Prod runtime (which divisions are actually enabled, how often scans fire, real call counts) is verified separately in STEP 2 — prod config may differ from repo.

---

## STEP 2 — Actual prod runtime (empirical, `tc-prod-vm` SSH read-only, 2026-08-06)

**Topology.** All Anthropic spend lives in ONE process: `trading-corp.service` (the engine, `/home/azureuser/trading_corp`, venv python). Other running services and all schedulers make **zero** Anthropic calls (verified: no `scripts/` file imports the LLM client; the kcv2 observer has no LLM import):

| Runtime unit | Anthropic? | Evidence |
|---|---|---|
| `trading-corp.service` (engine) | **YES — all of it** | hosts every agent/strategy in STEP 1 |
| `trading-corp-kcv2-observer.service` | No | no `build_chat_model`/`anthropic` import in its ExecStart script |
| `market-context-recorder.service` | No | recorder, no LLM |
| `sfp-card-watcher.service` | No | Telegram card notifier |
| timers: pead-watcher, pct-pruner, watchlist-stats/-deep, pm-watchlist-deep | No | `grep -rl build_chat_model scripts/` → 0 hits |
| cron: telegram_lifecycle_divergence_check, replay_audit_event_write_failed | No | monitors/replay, no LLM |

Prod `config/agents.yaml` is **byte-identical to repo** (same model routing). **API key is live** — proven empirically: 8,991 kalshi LLM calls in 7d each carry freshly-generated reasoning text (not cache-replayed).

**Real invocation counts** from `audit_event` (1.95M rows; the engine logs a `*_probability_called` event at each LLM call). Window ending 2026-08-06 19:07 UTC:

| Actor | LLM-call event | 7-day count | 30-day count | ≈ calls/day |
|---|---|---|---|---|
| **kalshi_llm_arbitrage** | `kalshi_llm_probability_called` | 8,991 | 40,917 | **~1,300** |
| **polymarket_arbitrage** | `polymarket_llm_probability_called` | 795 | 4,286 | **~140** |
| research_firm | `research_position_context_emitted` (+expert narration) | ~40 | ~230 | ~5 |
| PMCC (`robinhood_pmcc`/`pmcc`) | *(no LLM-call event emitted — not instrumented)* | n/a | n/a | ~30–70 est |
| ceo / portfolio / risk / whale-analyst | — | 0 | ~0 | ~0 |

Notes verified empirically:
- **Scan cycles ≫ LLM calls.** polymarket ran 21,703 scan cycles/7d but only 795 LLM calls — the LLM fires only on post-filter *survivors* (`survivors_post_filter: 0` most cycles). kalshi: 10,446 cycles → 8,991 calls (Economics/Elections discovery keeps survivors high).
- Both big spenders are **`enabled: true`, paper** (`auto_execute:false`; orders land as `would_have_placed` audit rows — 77 kalshi / 18 polymarket in 7d — nothing placed).
- kalshi cadence `poll_interval_sec: 60`, `k_markets_per_cycle: 20`, `market_cooldown_hours: 6`, discovery narrowed to **Economics + Elections**. polymarket `poll_interval_sec: 30`, K=20, cooldown 6h.
- PMCC's `pmcc_morning_triage` is **deterministic** (`register:"routine"`, rule-based) — NOT an LLM call. PMCC's LLM-judgment path (pmcc_robinhood.py:1013) emits no audit event, so its call volume is **not measurable from the DB** — flagged for instrumentation.
- ceo/portfolio/whale are on-demand (Telegram `/brief`, dashboard buttons) and were **not invoked** in the window → $0. Risk narrator gated on an order flow that barely fired (3 approvals/7d).
- backtester + eod_debate (Opus in config) = **dead** (no call site). No Opus spend except rare research_judge.

---

## STEP 3 — Estimated spend per call site (sorted by cost)

**Token shape per call** (measured from real prompts/payloads):
- kalshi & polymarket share the **same ~2,800-token `ANALYST_SYSTEM_PROMPT`** with `cache_control: ephemeral`. It clears Sonnet 4.6's 2,048-token cache minimum; with 30–60s cycles under a 5-min TTL the cache stays warm (warm-and-fan: 1 serial write, K−1 reads) → system prompt billed **mostly at cache-read ($0.30/M)**.
- kalshi user prompt ~80 tok; polymarket user prompt ~300 tok (desc capped 1200 chars). Output ~300 tok both (measured: ~90-word reasoning + key_unknowns + JSON).

| # | Call site | Model | calls/day | ~tok in/out per call | est $/day | est $/month | Confidence |
|---|---|---|---|---|---|---|---|
| 1 | **kalshi_llm_arbitrage** | sonnet-4-6 | ~1,300 | 2,880 in (2,800 cached) / 300 out | **$7.8** | **~$235** | High (real counts; caching assumed) |
| 2 | **PMCC** judgment | sonnet-4-6 | ~30–70 | ~3,500 in (uncached) / ~800 out | $0.7–1.6 | **~$30** | **Low** (not instrumented) |
| 3 | **polymarket_arbitrage** | sonnet-4-6 | ~140 | 3,100 in (2,800 cached) / 300 out | $0.94 | **~$28** | High (real counts) |
| 4 | research_firm (synth+expert+judge) | sonnet-4-6 (+opus judge rare) | ~5 | 1–3k in / 1–2k out | $0.1–0.3 | **~$5** | Med |
| 5 | ceo / portfolio / risk / whale | sonnet-4-6 / haiku | ~0 | — | ~$0 | **~$1** | High |
| | **TOTAL** | | | | **~$10/day** | **≈ $300/month** | |

**kalshi_llm dominates (~78% of spend).** Sensitivity on kalshi #1 (the only material number): all-cache-read floor ≈ **$218/mo**; caching-broken ceiling ≈ **$510/mo**. Central $235/mo assumes caching is working — **strongly implied by design but not yet verified** (the audit_event payload doesn't record `cache_read_input_tokens`). See recommendation R0.

> Opus/Haiku spend is effectively zero: the only Opus-routed live path (research_judge) fires a handful of times/month; Haiku (whale-analyst) wasn't invoked. The $300/mo is ~99% Sonnet 4.6, ~80% of it one paper strategy.

---

## STEP 4 — Zombie spend

**Method:** cross-checked the "believed CLOSED" list against (a) which modules actually import the LLM client and (b) live event counts. Only **3** strategy/division modules call Anthropic at all (`grep -rl build_chat_model`): `kalshi_llm_arbitrage`, `polymarket_arbitrage`, `pmcc_robinhood`.

### The one true zombie: `polymarket_arbitrage`

| | |
|---|---|
| Status believed | CLOSED |
| Status **actual** | **LIVE** — `enabled: true` in prod `config/strategies.yaml`, firing **~140 LLM calls/day** (795/7d, 4,286/30d) |
| Cost | **~$28/month** of Anthropic tokens, for a **paper** strategy (broker is `ReadOnlyBroker` — it literally cannot place orders; output goes to `would_have_placed` rows) |
| Decommission candidate? | **Yes** — pure token burn, no execution path, on your closed list |

**Exact change to kill it (NOT executed — your call):**
```yaml
# prod: /home/azureuser/trading_corp/config/strategies.yaml
polymarket_arbitrage:
  enabled: false        # was: true   ← flip this one line
```
Then restart the engine so it re-reads config (operator-run; agent is read-only, no sudo):
`sudo -n systemctl restart trading-corp`  (short, paste-safe one-liner)
Effect: strategy stops scanning + stops all LLM calls. Zero trading impact (paper-only, nothing placed). Reconcile intent first — it's *enabled with board-enable comments*, so verify whether "closed" was the decision that never got applied, or whether it's intentionally an open paper observer like kalshi_llm.

### Verified NOT burning Anthropic tokens (compute only, if anything)

| "Believed closed" division | Module | Anthropic? |
|---|---|---|
| Kalshi crypto 15m direction | kcv2 observer service + `kalshi_crypto_arb.py` | **No** — no LLM import; observer is a pure data logger |
| Kalshi sports arbitrage/scout | `kalshi_sports_scout.py`, `kalshi_sports_arb_observer.py`, `_sports_math.py` | **No** — deterministic arb math |
| Kalshi weather | `kalshi_weather_arb.py`, `_weather_math.py` | **No** — "pure math" (Gaussian P(YES)), confirmed in code + config comment |
| Polymarket arbitrage | `polymarket_arbitrage.py` | **YES — see above** |
| OptiTrade TP-SL | closest module `tasty_options_iron_condor.py`; copy-traders | **No** — no LLM import |

These may consume CPU/API-quota on *other* venues, but **$0 Anthropic**. Out of scope to kill for this audit.

### Other zombie categories — checked, all clean
- **Retry loops / error paths hammering the API:** NONE. Zero `*error*`/`*fail*`/`*retry*` events for the LLM actors in 7d. langchain/SDK default retry (`max_retries=2`) is not firing pathologically.
- **Bursts / debug-verbose storms:** NONE. Busiest single hour = 97 kalshi calls (~1.6/min) — steady-state, matches 60s cycles. No thousand-call spikes.
- **Duplicate calls:** kalshi has a 6h `market_cooldown` (won't re-evaluate a ticker within 6h) and polymarket a per-`condition_id` dedupe cap — both already guard against duplicate LLM calls.
- **Dead config (no spend, hygiene):** `backtester` + `eod_debate` are wired to **Opus 4.7** in `agents.yaml` but have **no call site** — never invoked. Harmless today, but a latent footgun (anyone who wires them lights up Opus at 5×/25× Sonnet pricing).

---

## STEP 5 — Recommendations (prioritized by $/month; NONE implemented)

**R0 — VERIFY prompt caching is actually working (do this first; $0 risk).**
The entire $300/mo estimate assumes the shared 2,800-token `ANALYST_SYSTEM_PROMPT` is being served from cache. If a silent invalidator is present (unlikely — the prompt is static — but unverified), real spend is up to **~2× higher (~$510/mo)**. The `audit_event` payload does **not** record `cache_read_input_tokens`, so this is currently unobservable. Cheapest check: log `response.usage_metadata` (langchain surfaces `cache_read`/`cache_creation`) for one cycle, or run a `max_tokens:0` warmup probe and read `usage`. **Action:** confirm `cache_read_input_tokens > 0`. Value: protects/corrects the whole model; if broken, fixing it saves up to ~$275/mo.

**R1 — kalshi_llm_arbitrage (~$235/mo = 78% of all spend). The only number that matters.** It's a **paper** forward-research observer. Choose one:
| Option | Lever | Est. saving | Trade-off |
|---|---|---|---|
| **R1a DECOMMISSION / PAUSE** | `enabled: false` if the forward corpus is "collected enough" | **~$235/mo** | Stops the research feed. Prior review ([[kalshi-postfix-review]]) found this paper strategy at **8W/8L, net $0.00 — no forward edge** — so the token spend may not be buying signal. **Your research-value call.** |
| **R1b REDUCE FREQUENCY** (keep alive, cheaper) | `market_cooldown_hours: 6 → 24` | **~$160/mo** | Calls are cooldown-bound (1,149 unique tickers × ~4 evals/day today → ~1/day). Cuts re-evaluations ~4× at lower time-resolution. Safest "keep it" option. |
| **R1c DOWNGRADE MODEL** | sonnet-4-6 → **haiku-4-5** for probability estimation | **~$150/mo** (3× cheaper) | Calibrated probability is a *reasoning* task; Haiku may be less calibrated. **A/B first** (run both a week on identical markets, compare `llm_prob` + divergence). Don't blind-switch. |
| **R1d BATCH API** | route paper probability calls through Batches | **~$115/mo** (50% off) | Paper strategy has NO execution → the "divergence moves fast" latency concern is moot. But up-to-1h batch latency breaks the 60s real-time scan; non-trivial refactor. |
| R1e caching | already ON | — | keep |

Recommended: **R1a if the research is done** (biggest, cleanest), else **R1b (24h cooldown) + optional R1c A/B**. R1b+R1c stacked ≈ back-of-envelope ~$200/mo off while keeping a (cheaper, coarser) live feed.

**R2 — polymarket_arbitrage (~$28/mo): DECOMMISSION.** Zombie (STEP 4). `enabled: false` + restart. Clean ~$28/mo with zero trading impact. (If you'd rather keep it as a paper observer, at minimum apply the same cooldown lever as R1b.)

**R3 — PMCC (~$30/mo, LOW confidence): (a) INSTRUMENT, (b) ADD CACHING. KEEP the model.** PMCC is **live-trading real money** (LEAP mandate) → don't downgrade/decommission. Two fixes:
- (a) It emits **no LLM-call audit event** → spend is currently unmeasurable. Add a `pmcc_llm_called` log so this line stops being an estimate.
- (b) The large static `_PMCC_EXPERT_SYSTEM` prompt has **no `cache_control`** (unlike the kalshi/polymarket paths). Adding `cache_control: ephemeral` on the system block cuts the cached-input portion ~10× and reduces latency. Est. ~$10–15/mo + faster panels.

**R4 — research_firm (~$5/mo): KEEP AS-IS.** Mostly operator-triggered, low volume; Opus only on the rare judge. Not worth touching. (Optional: cache the expert/synthesis system prompts — low ROI.)

**R5 — ceo / portfolio / risk / whale-analyst (~$1/mo): KEEP AS-IS.** On-demand, near-zero. whale-analyst already has a **$1/day cost cap + Haiku** — good hygiene, leave it as the template for others.

**R6 — Config hygiene ($0 saving, removes a footgun): delete the dead `backtester` + `eod_debate` Opus entries** from `agents.yaml`. No call site today; leaving Opus-wired dead entries invites accidental 5×/25× spend later.

---

## Bottom line

| Action | $/mo saved | Effort | Risk |
|---|---|---|---|
| R1a pause kalshi_llm *(if research done)* | ~$235 | 1 line + restart | none (paper) — but ends a research feed |
| — or R1b kalshi cooldown 6→24h | ~$160 | 1 line + restart | none (coarser feed) |
| R1c/d Haiku A-B **or** Batch (if kept on Sonnet) | +$115–150 | med (A/B or refactor) | quality/latency to validate |
| R2 kill polymarket_arbitrage (zombie) | ~$28 | 1 line + restart | none (paper) |
| R0 verify caching | protects estimate; up to ~$275 if broken | tiny | none (read-only probe) |
| R3 cache + instrument PMCC | ~$10–15 | small code change | low (live division — test) |
| R6 delete dead Opus config | $0 | trivial | none |

- **Current spend: ≈ $300/month**, ~99% Sonnet 4.6, ~80% one paper strategy (kalshi_llm) with no demonstrated forward edge.
- **Fastest safe win:** kill the `polymarket_arbitrage` zombie (~$28/mo, one line, zero impact).
- **Biggest win:** decide kalshi_llm's fate — pause (~$235/mo) or cooldown+downgrade (~$200/mo). This single division is the whole audit.
- **No runaway/error-loop spend, no unexpected Opus spend.** The system is well-behaved; the cost is a *policy* question (should paper strategies with no edge burn Sonnet tokens 1,300×/day?), not a bug.

*All figures are estimates from real call counts × current pricing × measured prompt sizes; the two dominant lines (kalshi, polymarket) rest on empirical 7/30-day invocation counts. PMCC is flagged low-confidence pending instrumentation. Nothing in the original audit was executed — Jack decides all actions.*

---

## ADDENDUM 2026-08-06 19:43 UTC — R0 usage-logging DEPLOYED + prompt caching CONFIRMED

Per Jack's directive, added `usage_metadata` logging to the kalshi_llm + polymarket LLM call path (additive only; helper `extract_usage_metadata` in the client wrapper `llm.py`, folded into the existing `*_probability_called` events). Deployed via **`az run-command` root (NO sudo)**, gated (pending_order=0), ownership-preserving, backed up.

- Deploy: engine **PID 573018 → 605796**, `active` since 19:43:20 UTC, **0 tracebacks** since restart; kalshi (root:root 644) + llm/polymarket (azureuser 664) ownership preserved; py_compile OK. Branch `claude-usagelog-2026-08-06 @ f9740fb`; **`prod-live` advanced `ef613e5`→`f9740fb`** and pushed. Rollback: `~/rollback_usagelog_20260806.sh`.
- **R0 RESULT — caching WORKS.** First 6 post-restart kalshi calls, raw `usage`:
  `{"input_tokens":109-114, "cache_creation_input_tokens":0, "cache_read_input_tokens":3116, "output_tokens":254-323}`
  The ~3,116-token system prompt is served at **cache-read (0.1×)** on every warm call (`cache_read=3116`, not 0). Confirms the **central estimate (~$235/mo), not the 2× no-cache ceiling.**
- Refined per-call cost (kalshi, caching on): input = 3,116×$0.30/M (cache-read) + ~112×$3/M (uncached user) ≈ **$0.00128**; output ≈ 285×$15/M ≈ **$0.00428**; ≈ **$0.0056/call** → ~1,300/day ≈ **$7.2/day ≈ $220/mo** (very close to the audit's cache-on floor).
- Pending: polymarket had no post-restart survivor/LLM call yet (low rate) — same code path, will confirm on next call. **Full $/mo recompute from ~1h of accrued usage data to follow** (window: ts ≥ 2026-08-06T19:43).

---

## ADDENDUM 2026-08-06 — kalshi_llm call-reduction levers + gate decision (>3c)

Post-fix baseline (08-01→08-06): **1,068 calls/day ≈ $179/mo** at $0.0056/call.

**Lever A — residual re-emission (post un-starve fix): banked, ~$0 remaining.**
| metric | pre-fix (07-20→31) | post-fix (08-01→06) |
|---|---|---|
| scan calls / distinct ticker | 14.28 | **7.64** (−47%) |
| repeat-call share | 93.0% | **86.9%** |
| entry (`would_have_placed`) / ticker | 7.64 | **3.73** (−51%) |
| max calls/ticker/**day** | — | ≤5 (6h cooldown holds) |
| sub-6h re-fires | — | 90 (1.4%) |
The 08-01 fix halved re-emission and there is no per-day runaway; the remaining repeats are legitimate 6h re-scans (Lever B's surface). Re-firing path = the cooldown-gated re-scan in `kalshi_llm_arbitrage.py` (`_save/_load_cooldowns` ~L624-659; not a rogue resolver — `kalshi_resolver.py` is resolution-only).

**Lever B — re-estimate-on-movement (implied move since prior estimate, post-fix):**
| movement | calls/day | share |
|---|---|---|
| first call (must run) | 140 | 13.1% |
| **<1c — price UNCHANGED** | 625 | 58.5% |
| 1–3c | 159 | 14.8% |
| >3c | 145 | 13.6% |

**Combined A+B (not additive — same surface; B does the work):**
| lever | calls/day removed | $/mo saved |
|---|---|---|
| A (beyond the 08-01 fix) | ~0 | ~$0 |
| **B — >3c gate** | **~783 (73%)** | **~$131** |
| run-rate after | ~285/day | ~$179 → **~$48/mo** |
(Orthogonal event-family consolidation ~12× would compound → sub-$20/mo.)

**GATE DECISION: >3c**, implemented on `kalshi_llm_arbitrage` (branch `claude-movegate-2026-08-06 @ 480e591`, +112/−8). Skip the LLM call when `|implied − last-at-estimate| ≤ 3c` and a prior estimate exists; first calls always run; last-estimate co-stored in `agent_state` key `market_last_estimate`; skips logged as `kalshi_llm_probability_skipped`. Cooldown/estimation/parsing untouched. Zero-risk floor (skip only the `<1c` unchanged-price bucket) would save ~$105/mo; **>3c chosen** for the fuller ~$131/mo (drops the 1–3c small-move band too). Deploy via az run-command root; 24h call-rate verification vs the ~285/day prediction to follow.

**Forward-corpus context (why this is safe to gate):** since inception the corpus is net-negative deduped (−$112 across 895 distinct markets); the current Economics+Elections regime is ~breakeven on n=16 distinct markets; calibration on the traded subset is anti-predictive at both tails. The gate removes redundant re-scans of unmoved markets — it does not change first-estimate behavior or the (absent) edge.

**DEPLOYED LIVE 2026-08-06 20:38:49 UTC** via az run-command root (no sudo). Engine PID 605796→**607896**, active, 0 tracebacks; `kalshi_llm_arbitrage.py` `root:root 644` preserved; py_compile OK; backup `~/movegate_bak_20260806/`; **`prod-live` advanced `f9740fb`→`480e591`** and pushed. Verified: first post-restart cycle (20:41) = 20 survivors → 20 estimates → **0 skips (correct — empty store = all first-calls)**; `market_last_estimate` store populated with sane `{implied, ts}`; usage/caching intact (`cache_read=3116`). **Skips begin ~6h post-deploy** (first cooled tickers re-enter the gate); full ~$131/mo call-rate drop toward ~285/day verified over 24h. Rollback: `rollback_movegate_az.ps1`.
