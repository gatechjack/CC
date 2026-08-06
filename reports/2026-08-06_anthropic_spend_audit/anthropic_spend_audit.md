# Anthropic API Token-Spend Audit — GT_Jack's Trading Corp

**Date:** 2026-08-06 · **Analyst:** Claude (Opus 4.8, read-only) · **Branch:** `claude-2026-08-06b`
**Scope:** READ-ONLY. No code changes, no deploys. Findings + recommendations only; Jack decides all actions.
**Method:** Static repo inventory (`C:\Users\AA Incorporado\cc`) cross-checked against ACTUAL prod runtime on `tc-prod-vm` (SSH read-only) and real invocation logs. Every spend claim verified empirically where logs exist; assumptions flagged.

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
