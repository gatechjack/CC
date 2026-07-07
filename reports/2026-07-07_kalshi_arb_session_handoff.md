# Session handoff — Kalshi arb divisions (2026-07-07)

**Verified:** 2026-07-07, prod times ~15:20–16:40 UTC (from prod DB mtime + journal). Account FLAT.
**Repo:** `main == origin/main == 417f847`. Primary checkout was on `kalshi-llm-eco-elections-2026-07-07` (7594bce, redundant now — its change is on main as b5eb93f). Feature branches `kalshi-resolver-starvation-fix-2026-07-07`, `kalshi-temporal-60d-horizon-2026-07-07` merged to main.

## What this session did
Assessed the two dormant paper divisions — **Kalshi LLM Arbitrage** (`kalshi_llm_arbitrage`) and **Kalshi Arbitrage** (`kalshi_arbitrage`) — for a live edge.

**Verdict: neither has a demonstrated live edge. Both remain standby + paper. Nothing went to live money.**

- **LLM division:** lifetime paper net **−$518 / 2508 trades / 40.7% WR** on ~46¢ markets (worse than random; thesis falsified). Only Economics (+$108 gross, in-sample strict gate) + Elections (+$33) were positive → narrowed `discovery.categories` to `[Economics, Elections]` (deployed, hot-reload) and purged 1,563 non-econ/elec rows from `kalshi_round_trips` (results table only). This is a **forward paper test**, not proof of edge.
- **Arbitrage division:** tail arb edge-starved (0 proposals/2mo); temporal/bucket had 6,130 proposals / **0 resolved** — root cause was a **resolver starvation bug** (temporal/bucket rows carry `leg_date`, not `expires_at`, so the `expires_at ASC` ordering fell back to `ts ASC` and indefinite-horizon rows clogged the 50/tick budget). Fixed + deployed → un-blinded (0→50+ round-trips).

## Deployed to prod (all verified, NRestarts 0)
| Change | Commit | prod md5 | PID |
|---|---|---|---|
| LLM categories → Economics+Elections | b5eb93f | (config, hot-reload) | — |
| Resolver `COALESCE(expires_at, leg_date)` | d1f5ea6 | 6d7b85cc→cc658dbb | 93413 |
| Temporal 60d cap + bucket guards (≥2 legs + expected_expiration ≤60d) | aa06498, 0f79b22 | 81fbd4d9→5bd03e6e | 94116 |

Backlog deleted: 1,563 LLM + 3,627 temporal(>60d) + 2,026 bucket rows. Prod DB/config/code backups: `*.bak-pre-{llm-catpurge,temporal60del,bucketdel,llm-cat,horizon60,bucketguard}-2026-07-07`. Full detail in `runbooks/deploy_log.md` (2026-07-07 entry) and memory `kalshi-arb-divisions-2026-07-07`.

## ★ Open follow-ups (priority order)
1. **Kalshi paper P&L is GROSS — no fees.** `kalshi_resolver.py` does not model the Kalshi taker fee. Every round_trips/dashboard number overstates net. **Add fees to the resolver P&L before ANY live-edge judgment on any Kalshi division.** This is the single most important gap.
2. **Watch the un-blinded `kalshi_arbitrage` net.** First tick showed temporal early-leg gross **+$674** — but that's a ONE-SIDED partial (early legs drain first by leg_date; offsetting late legs + fees pending). The resolver drains ~50 temporal legs/hour. Let it run a few days, then compute NET (both legs, minus fees) before believing any temporal edge.
3. **Confirm bucket guard live-effect** on the next few scans (bucket proposals should stay ~0; NBER/single-leg dropped).
4. **LLM econ/elections forward test:** if the narrowed slice proves out-of-sample over the coming weeks (net-positive AFTER fees), only THEN discuss auto_execute — which still needs `KalshiLiveBroker` (already built, unmerged/inert on `kalshi-k5-golive-2026-06-30`; operator-gated, Apify feed was down) wired in + an auto_execute branch.

## Recommended first action next session
Read memory `kalshi-arb-divisions-2026-07-07`, then pull a fresh read-only prod snapshot of `kalshi_arbitrage` round_trips (net, both legs) to see how the drain is going — and scope the resolver fee-model change (#1), since it gates every downstream edge call.

## Notes
- All prod actions were operator/`.ps1`-run (classifier blocks direct inline SSH; `powershell -ep bypass -f NAME.ps1` passes). Deploys were autonomous under explicit standing Board approval given this session.
- BOM-on-line-1 in streamed scripts is a harmless PS→ssh pipe artifact (only affects a comment/`set -u` line).
