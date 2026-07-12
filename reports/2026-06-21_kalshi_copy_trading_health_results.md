# Kalshi Copy Trading — Health + Status + Results Report

**Division:** `kalshi_copy_trading` (strategy `kalshi_copy_trader`, Phase K3)
**Mode:** paper-only (`auto_execute: false`) — no real capital deployed.
**Prepared:** 2026-06-21 ~01:05 UTC (read-only investigation; no code/config/roster/deploy changes).
**Data window:** full paper history **2026-05-11 → 2026-06-21** (41 days), prod DB `data/trading_corp.db` (1.26 GB).
**Access:** read-only SSH to `tc-prod-vm` (SELECT-only `sqlite3 -readonly`, `journalctl`/`systemctl` reads). Reproduction scripts: `kq1–kq5.sh`, `kanalysis.py` in repo root.

> **Bottom line (evidence, not a recommendation — the operator decides live/paper):** No trader in the current roster shows a positive expectancy at a credible sample size once the lag, fee, and slippage that live execution would impose are applied. The book is **gross-negative before any costs** (−$41.61 over 3,111 resolved trades) and **−$117.48 net** under a realistic cost model. The single net-positive whale (`MaggieTheEagle`, +$3.34) has **9 resolved trades**. 88% of the book is Kalshi 15-minute crypto-bar markets — the worst-possible structure for a copier with a 0–10-minute detection lag. The `docs/divisions.md` headline ("253 RT / +$0.58 net / break-even") is **stale**; the live numbers are an order of magnitude larger and clearly negative.

---

## STEP 1 — Health check

| Check | Result |
|---|---|
| `trading-corp.service` | **active / running**, `NRestarts=0`, PID 3093124, clean start **2026-06-20 05:48:08 UTC** (deploy/manual restart, not a crash loop). VM up 51 days. |
| Scan cadence | **Polling on schedule, ~10 min** (`poll_interval_sec: 600`). Live cycles observed at 00:50:12 and 01:00:25 UTC; `agent_state.last_poll_ts = 2026-06-21T01:00:24Z` (current). |
| Prod config | `enabled: true`, `auto_execute: false`, poll 600s, sizing `[1,2,3]` USD @ contract bounds `[100,1000]`. **Prod == repo** for this block (no drift). |
| NameError (05-28 fix) | **Still 0.** Old `_scheduled_kalshi_copy_trader_loop` NameError (main.py:2830) fired ~130×/day May 15–28, last occurrence **2026-05-28 04:43** (pre-deploy); fixed at 04:44. Zero error-kind audit rows all-time. |
| Auth / throttle errors | None in journal since restart. |
| Roster freshness | `selected_whales` last updated **2026-06-14 14:24 UTC** (7 days ago); **17 whales**. 24 promote events all-time (last batch 06-14), 9 demotes, 1 auto-pause (`reach.draft`, 05-31). |
| `audit_event` populating | Yes — 260–410 copy `would_have_placed`/day in the last 10 days. |

### Anomaly 1 (flag) — 3-day silent copy-scanner gap, 06-08 → 06-10
The service was **healthy** those days (14,595 / 13,365 / 13,967 audit events/day from other actors), but `kalshi_copy_trader` emitted **zero** copy activity — no placements, no skips, no cold-starts — only 4 promote events on 06-08. It **resumed 06-11 with 4 fresh `cold_start`s** (per-whale snapshots re-initialized).
- **No restart log, no error-kind audit row** → a *silent* gap.
- **Most likely cause:** the Apify `fetch_open_positions` failure path catches the exception and `return []`s with only a `log.warning` (kalshi_copy_trader.py:257–259). An Apify quota/auth lapse around 06-08 would produce exactly this signature (silent, no audit). Not root-caused further (read-only; would need Apify-side logs). **Recommend a feed-health alarm** — same lesson as the shelved sports-arb observer, which silently flatlined for ~10 days.

### Anomaly 2 (flag, benign) — one transient DB-lock traceback, Jun 16 13:49
Exactly **one** copy-loop traceback in the 24 days since the 05-28 fix: `sqlite3.OperationalError: database is locked` during `logger_agent.log_proposed_order` (main.py:3244 → logger.py:134). Caught by the loop handler; the scanner kept running (320 placements that day); did **not** recur. Same DB write-lock contention class that affects bitunix (a concurrent `bitunix_futures` lock-retry is logged the same second). Non-fatal observability blip.

---

## STEP 2 — Per-trader results (paper)

**Window:** full paper history (stated above) — chosen over "last N days" because most whales' samples are already small. **3,111 resolved round-trips.**

**Cost model (stated explicitly):**
- **Fee:** Kalshi general trading fee `ceil(0.07 · C · P · (1−P))` rounded up to the next cent, **per traded side**. Hold-to-resolution settlement is fee-free on Kalshi, so an **entry fee always applies**; an **exit fee only when the copy exited pre-resolution** (exit price strictly between 0 and 1 — rare here: total exit fees = −$0.06, confirming nearly all positions settle at resolution).
- **Slippage:** **1¢ / contract / traded side** (we cross the spread at $1–3 size); settlement legs incur none. Sensitivity rows show 0¢ and 2¢.
- **Sizing note:** the resolver books each copy as its contract count (`qty`), so realized PnL per trade ≈ [−$1, +$1] per contract. The $1/$2/$3 tier maps to 1–3 contracts.

### Portfolio totals
| Metric | Value |
|---|---|
| Resolved round-trips | **3,111** (1,658 W / 1,453 L / 0 void) — raw WR **53.3%** |
| **GROSS** paper PnL | **−$41.61** |
| Entry fees | −$39.72 |
| Exit fees (pre-resolution) | −$0.06 |
| Slippage @1¢/side | −$36.09 |
| **NET (fees + 1¢ slip)** | **−$117.48** |
| NET @0¢ slippage | −$81.39 |
| NET @2¢ slippage | −$153.57 |
| Max drawdown (net cum.) | **−$119.69** (final cum. net −$117.48 → essentially a monotonic bleed, no recovery) |

### Per-whale (sorted by sample; ★ = in current roster)
| Whale | ★ | n | W | L | WR% | Gross $ | Fees $ | Slip $ | **Net $** | Avg hold (min) | Avg entry px |
|---|:-:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| smedtoshi | ★ | 1677 | 940 | 737 | 56.1 | −3.66 | −16.79 | −19.35 | **−39.80** | 12.1 | 0.56 |
| the.hoff.85 | ★ | 733 | 381 | 352 | 52.0 | −10.89 | −13.09 | −7.59 | **−31.57** | 16.5 | 0.53 |
| tom14cat14 | ★ | 347 | 184 | 163 | 53.0 | −9.89 | −4.54 | −3.54 | **−17.97** | 478.8 | 0.56 |
| szg.szg | ★ | 109 | 39 | 70 | 35.8 | +1.42 | −1.10 | −2.03 | **−1.71** | 11.2 | 0.35 |
| pritz786 | ★ | 71 | 46 | 25 | 64.8 | −0.04 | −0.86 | −1.21 | **−2.11** | 59.5 | 0.65 |
| leftwithnothing | ★ | 70 | 26 | 44 | 37.1 | +1.52 | −1.12 | −0.72 | **−0.32** | 23.8 | 0.35 |
| reach.draft | | 39 | 18 | 21 | 46.2 | −15.29 | −1.05 | −0.68 | **−17.02** | 372.8 | 0.67 |
| warm.slope | ★ | 29 | 12 | 17 | 41.4 | −2.13 | −0.47 | −0.37 | **−2.97** | 11.5 | 0.48 |
| teafordong | ★ | 20 | 4 | 16 | 20.0 | −3.85 | −0.33 | −0.22 | **−4.40** | 473.1 | 0.38 |
| **MaggieTheEagle** | ★ | **9** | 8 | 1 | 88.9 | +3.70 | −0.20 | −0.16 | **+3.34** | 213.3 | 0.66 |
| 9187234 | | 4 | 0 | 4 | 0.0 | 0.00 | −0.12 | −0.14 | −0.26 | 2127.6 | 0.68 |
| lengthy.starfish | ★ | 2 | 0 | 2 | 0.0 | −1.38 | −0.07 | −0.06 | −1.51 | 5291.0 | 0.23 |
| AI.EDGE | ★ | 1 | 0 | 1 | 0.0 | −1.11 | −0.04 | −0.02 | −1.17 | 186.7 | 0.56 |

**Roster whales with ZERO resolved trades:** `NovaRex`, `Hispaniola`, `foess`, `phillygeno`, `historic.kestrel4928`, `c.f.frls` (6 of 17). `phillygeno` trades **sports-only** (141 distinct sports tickers skipped, 0 copyable entries).

**Data-quality flags (Step 2):**
- **Sample size:** Only 3 whales have n ≥ 100 (smedtoshi, the.hoff.85, tom14cat14) — all net-negative. The lone net-positive whale (MaggieTheEagle) has **n = 9**, far below the strategy's own 30-RT auto-pause floor → not statistically conclusive.
- The auto-pause guard (pause iff ≥30 RT **AND** WR<40% **AND** net<−$5) has fired once (`reach.draft`, 05-31). It does **not** catch the dominant loss drivers here, because smedtoshi/the.hoff.85 have WR > 40% — their losses come from **fees+slippage on a thin gross edge**, which the guard doesn't measure.

### Proposal generation vs. fills vs. skips (per whale)
"Filled (paper)" = entries that resolved into a round-trip. "Sports-skip" is **deduplicated to distinct tickers** (the raw 13,503 sports-skip audit rows re-fire every cycle because sports tickers are intentionally not persisted to the snapshot — *not* 13.5k distinct opportunities).

| Whale | ★ | Entries emitted | Exits | No-side skips | Sports-skip (distinct tickers) |
|---|:-:|--:|--:|--:|--:|
| smedtoshi | ★ | 1677 | 1674 | 946 | 6 |
| the.hoff.85 | ★ | 734 | 732 | 10 | 0 |
| tom14cat14 | ★ | 347 | 344 | 44 | 278 |
| szg.szg | ★ | 109 | 109 | 100 | 0 |
| pritz786 | ★ | 71 | 71 | 54 | 35 |
| leftwithnothing | ★ | 70 | 70 | 14 | 0 |
| warm.slope | ★ | 29 | 29 | 253 | 52 |
| teafordong | ★ | 22 | 20 | 183 | 109 |
| MaggieTheEagle | ★ | 10 | 9 | 10 | 1 |
| **lengthy.starfish** | ★ | **4** | 2 | **1845** | 8 |
| reach.draft | | 39 | 35 | 76 | 109 |
| phillygeno | ★ | 0 | 0 | 1 | 141 |
| c.f.frls / historic.kestrel4928 | ★ | 0 | 0 | 5 / 1 | 0 |
| foess / Hispaniola / NovaRex | ★ | 0 | 0 | 0 | 0 |

**Read:** Activity is dominated by 3 whales. **Side detection fails constantly** — `lengthy.starfish` had **1,845 no-side skips against just 4 copied entries** (≈uncopyable); `smedtoshi` skipped 946. Six roster members produce no copyable signal at all.

### Category distribution (by ticker prefix — `category` column is unpopulated on copy RTs)
| Macro | n | WR% | Gross $ | Net $ |
|---|--:|--:|--:|--:|
| **Crypto (15-min bars)** | **2751 (88%)** | 54.0 | −10.66 | **−76.56** |
| Other (esports/intl-friendlies/misc) | 267 | 51.3 | −26.99 | −34.80 |
| Sports (residual, past filter) | 66 | 33.3 | −4.44 | −5.87 |
| Weather | 26 | 53.8 | +0.51 | −0.18 |
| Econ | 1 | 0.0 | −0.03 | −0.07 |

The edge is **not** concentrated in a profitable bucket — it's concentrated in the **worst** bucket (fast crypto bars), and even the flattest bucket (Weather) goes slightly net-negative after costs.

---

## STEP 3 — Copyability (the structural failure mode)

1. **Lag (whale entry → our copy fill).** The strategy detects a new whale position only on the **next 10-minute poll**, then size-matches it against the public trade tape in the `[last_poll, now]` window. Structural copy lag = **0 to 600 s (poll interval) + Apify scrape latency**. *The exact per-trade lag is not stored* (we keep the matched trade-tape price but not its timestamp vs. our emit time) — **data-quality flag**. With **88% of the book in 15-minute crypto-bar markets and avg hold 12–17 min for the high-volume whales**, a copy that lands up to 10 minutes late enters with a large fraction of the market's life already gone, often after the move that gave the whale the edge. This is the copy-trading death trap in its purest form: a fast-resolving instrument copied on a slow clock.

2. **Spread / liquidity at copy time vs. whale's fill.** *Not stored* — only the matched-trade entry price is retained, not bid/ask or depth — **data-quality flag**. Proxy: the high-volume whales' avg entry price sits at **$0.53–0.56** (mid-book), exactly where Kalshi's `P·(1−P)` fee and the spread are largest. Longshot bettors (szg.szg, leftwithnothing at ~$0.35) have lower fee load but lower win rates.

3. **Winning trades in markets too thin for real execution.** At **$1–3 notional (1–6 contracts)**, our *own* market impact is negligible even in thin Kalshi books — **market impact is not the binding constraint at this size.** The binding constraints are (a) the **per-contract fee**, which is a fixed ~1–2¢ regardless of how small we go, i.e. a **3–5% drag on a $0.30–0.80 contract**, and (b) the **lag** above. The high no-side-skip rate (e.g. lengthy.starfish 1,845 skips) shows the system already declines to copy the thinnest/busiest markets where side detection fails — so the losses are coming from the markets we *can* read, killed by fees, not from getting picked off in thin ones.

---

## STEP 4 — Synthesis

**Which traders show a real edge surviving lag + slippage + fee?**
**None at a credible sample size.** Every whale with n ≥ 20 is net-negative after costs. The only net-positive whale, `MaggieTheEagle` (+$3.34), has **9 resolved trades** — below the strategy's own validation floor and statistically meaningless.

**Which look profitable on paper but would not be live?**
The question is moot at the portfolio level: the book is **gross-negative (−$41.61) before a single cent of fees** — it fails ahead of the live-conditions haircut. At the whale level, `szg.szg` (+$1.42 gross) and `leftwithnothing` (+$1.52 gross) are the only gross-positive whales with n ≥ 50; both flip net-negative after costs (−$1.71, −$0.32). `pritz786` has a 64.8% win rate but ~break-even gross (−$0.04) → net −$2.11 on fees alone.

**Structural issues to fix before live capital makes sense, regardless of trader quality:**
- **Market-type mismatch (the big one):** 88% of copied volume is 15-minute crypto-bar markets, whose edge is sub-minute timing that a 10-minute-poll copier cannot capture. No roster change fixes this — it's the instrument-vs-cadence mismatch. (Note: the related `kalshi_crypto` *latency* thesis was already shelved 2026-05-22 for an analogous reason — the venue's settlement structure closed the sub-second edge.)
- **Fee-per-contract dominates at $1–3 size:** fees+slippage (−$75.8 combined) are ~1.8× the gross PnL magnitude. At this size the fixed per-contract fee is an unrecoverable percentage drag; only a much larger gross edge or much larger size (with its own market-impact cost) would change the arithmetic.
- **Side-detection failure rate:** thousands of `no_side` skips mean most of the whale signal is uncopyable as built; what survives is a biased subset.
- **Roster hygiene:** 6 of 17 roster whales produced zero copyable entries; `phillygeno` is sports-only (100% skipped). The roster is carrying dead weight.
- **Silent single-point-of-failure on Apify:** the 06-08→06-10 gap shows the feed can fail for days with no alarm and no audit trail (silent `return []`). A feed-health alarm is a prerequisite for trusting any forward track record.

---

## Reproduction
All figures from prod `data/trading_corp.db` via read-only SSH. Scripts (repo root, this session): `kq1.sh`–`kq5.sh` (health/schema/gap probes), `kanalysis.py` (per-whale fee/slippage model). Re-run any with:
`tr -d '\r' < kanalysis.py | ssh azureuser@trading.jacksumner.com 'python3 -'`
