# SDTrading mlb drill-through — READ-ONLY investigation (2026-08-24)

**Trigger:** Jack eyeballed the first live drill-through (SDTrading mlb: n=477, win% 94%, cost-ROI +90.5%, avg win px 0.51 CONTESTED, ANOM:4) — a wall of recent wins looked too good. Read-only characterization (`pm_sdt_diag.ps1` + `pm_sdt_analysis.py`, `mode=ro`, engine-PID bracketed unchanged 850993).

## VERDICT: NO CODE DEFECT — correct-but-misleading

Every scoreboard aggregate **reconciles to hand-count to 4 decimals**; `won` is derived correctly; the ANOM rows are explicable; two-sided grouping is correct. The three suspicious things are **presentation/metric-scope gaps, not bugs.**

## The data (wallet `0x16bb9951a36fce71e2ef57890b786145e0ba8492`)

**(1) Market type — the edge is NOT all moneyline.** Of 477 rows (slug-suffix breakdown, exact):
- **moneyline = 254 (53%)** (plain game slug, empty suffix)
- **total = 167 (35%)** (`-total-8pt5` ×81, `-7pt5` ×43, `-9pt5` ×30, …)
- **spread = 56 (12%)** (`-spread-home-1pt5` ×35, `-spread-away-1pt5` ×20, …)
- `classify_market_shape` (deployed code) returns **`single_game` for all 477** → `single_game_pct = 1.0`. That is CORRECT (they are all dated single-game markets, not futures) but **conflates copyable moneyline with uncopyable spread/total**. Only ~53% is copyable through the MLB-single-game-moneyline matcher. This is the §13A(d) market-type dimension — **deferred by design**, seam `market_type_source='slug_heuristic'` present, the moneyline/spread/total split not built. **Category ≠ copyability.**

**(2) Correlated legs — 477 legs are 380 games.** legs-per-game (scoreable): `{1:296, 2:73, 3:9, 4:2}` → 380 distinct games, 84 with multiple correlated legs (ML+spread+total on one game move together). per-LEG win% = **94.3%** (450/27); per-GAME (net>0) = **96.1%** (365/380). The win% is correctly computed but treats correlated legs as independent — the effective independent sample is ~380 games, not 477 calls.

**(3) Losses exist, correctly counted, hidden below the date-sorted fold.** 450 won / **27 lost** (= 94.3%, matches). Losses are real (`cur_price=0`, negative pnl) and span moneyline + totals; the newest-first default just stacks recent wins on top.

## Defect checks (all clean)
- **A — `won == (cur_price≥0.9)`:** 0 mismatches. Correct.
- **B — aggregates vs hand:** win_rate 0.9434==0.9434, avg_win_price 0.5144==0.5144, roi 0.9046==0.9046 (net 4,361,038.6 / cost 4,821,001.1). Wins 450 / losses 27 both sides. Exact.
- **C — the 4 ANOM rows:** all `loss_exceeds_cost` (demoted clause-(a) flag, `pnl_suspect=0` → NOT excluded, counted as losses). Each is a REAL loss where the recorded loss exceeds `total_bought`/`cost_basis` — the §13A(f) scale-in-undercount (notional understates true cost). Expected + documented; the flag is doing its job (flag, don't exclude). **Minor bias-up note:** on those 4 rows the cost denominator is understated → ROI slightly optimistic there; 4/477 → negligible aggregate impact.
- **D — two-sided grouping is correct.** 20 condition_ids held on both outcome_index (== `n_two_sided=20`) — legitimate both-sides-of-ONE-market holds. The example game `mlb-cin-ari-2026-08-23` carries its ML / spread / total on **3 DISTINCT condition_ids** → correlated legs do NOT group as two-sided. Confirmed.
- **one-sided slice:** n=437 (= 477 − 40 two-sided-market rows), roi **+99.6%** (the UPPER BOUND, §13A(f)).

## Recommendations (recommend, do NOT build — Jack's ruling)
1. **`lost` filter + a summary header** ("477 rows: 450 won, 27 lost") on the drill panel — makes the record's shape visible instead of a date-sorted wall of wins.
2. **Surface market type** (moneyline / spread / total, derivable free from the slug suffix). Today `single_game%` reads as "copyable" when only ~53% is moneyline. Options: a market-type column + filter, and/or a "moneyline %" (copyable-share) alongside single_game%. This is the deferred §13A(d) dimension — the slug-derived split is zero-cost and would prevent exactly this misread.
3. **Open follow-up (read-only, offered):** SDTrading's win% and ROI on the **254 moneyline rows alone** (the copyable slice) — UNVERIFIED here; that number decides whether the +90.5% survives once the uncopyable totals/spreads are removed.

*Investigation only. No code change, no deploy, no restart. Runner banked alongside.*
