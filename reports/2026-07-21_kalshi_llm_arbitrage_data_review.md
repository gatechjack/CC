# kalshi_llm_arbitrage — data review (9 sections)

**Date:** 2026-07-21 · **Mode:** READ-ONLY · **Scope:** data only. No edge/viability/prospect characterization (operator's call). Fee model = Kalshi `ceil(0.07·C·P·(1−P))` per side (entry only; settlement free). Slippage model = 1¢/contract entry side (paper fills at mid; see §5). Both applied post-hoc; stored `realized_pnl` is GROSS.

> **Framing note that governs the whole report:** every resolved round-trip was **entered on or before 2026-06-08**. There are **zero resolved round-trips entered after the 07-07 change.** The "since 07-07" numbers measure the **legacy book draining**, not the new [Economics, Elections] logic. The new logic's resolved sample is **0** (144 open, none settled).

## §1 — Timeline
Only one kalshi_llm change in 60 days: **2026-07-07 ~16:40 UTC** (`main==0f79b22`).

| Commit | Change | Files |
|---|---|---|
| `b5eb93f` | discovery `categories` → `[Economics, Elections]` (dropped Politics, Financials; Sci/Tech/Crypto/Climate already carved) | config/strategies.yaml (hot-reload) |
| `d1f5ea6` | resolver `ORDER BY COALESCE(expires_at, leg_date)` (starvation fix) | kalshi_resolver.py (restart) |
| (data op) | deleted 1,563 non-Econ/Elections rows from `kalshi_round_trips` **results table only** (audit_event + equity_history kept); 2508→945 | DB |

Row deletion applied to **resolved-results rows**, not entries/audit/candidates. Review window: **07-07→now (14d)** primary; all-time baseline.

## §2 — Pre vs post-07-07
**By ENTRY period (what the new logic produced):**
| Entered | Resolved n | W | WR | gross | net(fee+slip) |
|---|---|---|---|---|---|
| ≤ 07-07 | 2,686 | 1,083 | 40.3% | −$472.67 | −$688.88 |
| > 07-07 | **0** | — | — | — | — |

**By RESOLVED period (dashboard "since 07-07"; boundary = deploy 16:40 UTC):**
| Resolved | n | W | WR | gross | net |
|---|---|---|---|---|---|
| pre-07-07 | 1,591 | 797 | 50.1% | +$75.84 | −$37.05 |
| post-07-07 | 1,095 | 286 | **26.1%** | −$548.50 | −$651.82 |

(Dashboard's −$613.87 uses a midnight boundary → +646 pre-deploy resolves; same legacy-entry cohort.) All-time avg realized/contract = **−$0.0471** on 10,026 contracts. All-time fees −$115.95, slippage −$100.26. Post-change window ≈ 13.3 days → **−$49/day net** on the drain (bursts, not steady — see §7).

## §3 — Category (post-07-07 resolved = legacy drain, n=1,095), by net
| Category | n | WR | gross | net | avg hold (d) |
|---|---|---|---|---|---|
| Science & Technology | 304 | **1.6%** | −$281.82 | −$317.96 | 55.2 |
| Politics | 473 | 41.4% | −$101.22 | −$137.76 | 48.8 |
| Elections (legacy) | 83 | **9.6%** | −$67.90 | −$77.82 | 55.1 |
| World | 42 | **0.0%** | −$42.00 | −$48.24 | 51.7 |
| Financials | 62 | 43.5% | −$27.34 | −$31.15 | 49.3 |
| Crypto | 26 | 3.8% | −$24.51 | −$26.42 | 55.7 |
| Companies | 9 | 22.2% | −$6.83 | −$7.84 | 48.0 |
| Climate & Weather | 94 | 48.9% | +$3.98 | −$3.67 | 54.9 |
| Economics (legacy) | 1 | 100% | +$0.14 | +$0.12 | 51.3 |

**WR floor across losing categories:** Sci&Tech 1.6%, World 0%, Crypto 3.8%, Elections-legacy 9.6% — all far below 50% (systematic wrong-side, not variance). Sci&Tech = **49% of net loss**. Concentration: **position-level none** (max single loss ≈ −$1; max single gain +$11.50 = 2.2% of equity); **event-level heavy** (see §6).

## §4 — Signal quality (post-07-07 resolved)
**By LLM confidence** (recorded on 1,095/1,095 rows):
| conf | n | WR | net |
|---|---|---|---|
| medium | 828 (76%) | **21.6%** | −$589.66 |
| low | 247 | 40.1% | −$55.33 |
| high | 20 | 40.0% | −$6.83 |

**By |divergence|:**
| bucket | n | WR | net |
|---|---|---|---|
| 10–15% | 319 | 27.0% | −$176.79 |
| 15–25% | 355 | 29.3% | −$213.56 |
| 25–40% | 177 | 39.5% | −$49.21 |
| **40%+** | 244 | **10.7%** | −$212.27 |

Observations (data only): every confidence tier is <50% WR; **medium** (76% of volume) is worst. **Highest-divergence bucket (40%+) has the lowest WR (10.7%)** — larger LLM-vs-market disagreement associates with the market being right more often, in this window's sample.

## §5 — Fill behavior / paper→live gap
- **Paper fill = mid/implied.** Strategy uses `implied = (yes_bid+yes_ask)/2` (else ask). Recorded `entry_price` ≈ leg-converted mid (NO entry_px ≈ 1−implied_YES; verified on samples).
- **Live would cross the spread** (buy at the ask) → crossing cost ≈ half-spread ≈ **~1¢/contract** (model used).
- **Cheap-leg amplification:** the strategy buys the cheap side (e.g. NO @ $0.10 = 10 contracts per $1). Round-trip cost on such a bet = fee `ceil(0.07·10·0.1·0.9)=$0.07` (7%) + slippage `10×$0.01=$0.10` (10%) = **~17% of the $1 stake**. On higher-priced legs the % is smaller. All-time this is −$216.21 (fees+slip) on gross −$472.67 → **net −$688.88**.

## §6 — Sci&Tech deep look (304 resolved, 5 wins)
- **LLM side vs outcome:** 299 "NO" bets → resolved "YES" (−$299.00); 5 "NO" → "NO" (+$17.18). **Wrong side 98.4%.**
- **Market family:** 265 of 304 are one event — *"Price of NVIDIA H100 SXM compute by May 31, 2026"* (0 wins); plus H200 (12), RTX-5090 (8), etc. — NVIDIA GPU compute-price threshold markets.
- **Pattern:** LLM estimated YES below implied (llm_prob 0.72–0.82 vs implied 0.85–0.92), bet NO across essentially every strike; price cleared → YES.
- **Classification (data):** consistent with **"LLM confidently wrong"** on a domain lacking live data (GPU compute prices), **amplified** by taking the same NO thesis across 265 strikes of one event (correlated, not 265 independent bets). Entry prices + resolutions reconcile — no evidence of an execution/scoring bug or misparse in the recorded fields; the recorded `llm_reasoning` shows genuine probability estimates. Category dropped 07-07 (won't recur among new entries).

## §7 — Time series post-07-07 (resolved)
| Day | n | W | gross | net |
|---|---|---|---|---|
| 07-07 (≥16:40) | 360 | 102 | −$186.46 | −$219.47 |
| 07-08 | 557 | 123 | −$300.09 | −$355.09 |
| 07-11 | 22 | 19 | +$13.90 | +$12.42 |
| 07-20 | 130 | 34 | −$62.41 | −$73.26 |
| 07-21 | 26 | 8 | −$13.44 | −$16.43 |

- **07-07 + 07-08 = 917 of 1,095 rows (84%), net −$574.56** — a one-time resolver-drain of stale May entries (avg hold 48–55d) unblocked by `d1f5ea6`. Gaps (07-09/10, 12–19) had 0 resolves.
- Only positive day: **07-11 (+$12.42, 22 Climate rows, 19 W)**.
- Loss is not accelerating from ongoing activity — it's a backlog flush plus a smaller 07-20/21 tranche.

## §8 — Open positions
- **New-logic (entered >07-07): 144 open** — Economics 91, Elections 53. All ~$1 notional. **0 resolved.**
- **Legacy (entered ≤07-07): 1,317 open.** By expiry: **May 2 + June 600 = 602 already past expiration** (settled on Kalshi, unresolved in-DB = resolver-starvation backlog), July 647, Aug 212.
- Total open 1,461 (= 4,147 signals − 2,686 resolved).
- **Concentration:** max single position $1.00 = **0.19% of $532.84 equity**; none >2%.
- **Forward:** the ~602 past-expiration legacy positions are the same dropped-category book (Politics/Sci&Tech/etc.) and will drain as further realized losses when the resolver reaches them — i.e. the −$472.67 realized figure has a known un-booked overhang below it.

## §9 — Synthesis (data only)
**Where the loss comes from:** (a) **category** — Sci&Tech (49% of net), Politics, legacy-Elections, World; (b) **one event** — NVIDIA H100 compute (265 correlated NO losses); (c) **signal band** — medium-confidence (76% of volume, 21.6% WR) and extreme divergence (40%+, 10.7% WR); (d) **time** — 84% booked on 07-07/08 as the resolver flushed the May backlog. The loss is **not broadly distributed random variance** — it concentrates in specific categories/events the discovery config later dropped.

**What is mechanically different post-07-07 vs the 40.3% baseline:** nothing about the *new* logic is measured yet. The 26.1% post-resolved WR is lower than the 40.3% all-time because the drain window is **over-weighted toward the worst legacy categories** (Sci&Tech + World + Politics), which were the longest-horizon (avg 55d hold) and thus resolved last / together when the resolver was fixed. The new [Economics, Elections] entries (144) are unresolved.

**Instrumentation gaps flagged:**
- No live-bid marks for open positions → unrealized P&L on 1,461 open positions is not computable from stored data (would need live Kalshi quotes).
- `realized_pnl` stored gross; fees/slippage exist only as post-hoc models here (not in the row).
- Resolver-starvation backlog: ~602 past-expiration positions unresolved in-DB → realized P&L lags true settled state.
- `entry_order_id` is NULL for this division (linkage is via `order_id`); the copy-trading `entry_order_id` convention doesn't apply here.

**Open questions the data raises (for operator, not answered here):**
1. How will the 144 new-logic Econ/Elections positions resolve (first settlements due through July–Aug 5, e.g. the FOMC 2Y-yield market)? — the only forward measure of the change.
2. Will the ~602 past-expiration legacy positions, when the resolver reaches them, add materially to realized loss?
3. Is the NVIDIA-compute wrong-side pattern a data-availability limit of the LLM (no live GPU-price feed) or a market-structure misread — and do the retained categories share any similar structural blind spot?
4. Why is medium-confidence both the highest-volume and lowest-WR band — a calibration question the new-logic sample can eventually test.

*Guardrails honored: read-only; no code/config/memory/roster changes; no edge/viability/prospect verdict; no flags touched.*
