# MLB Totals Copyability — research + line-agreement measurement (2026-08-24)

Read-only. Public Kalshi/Polymarket APIs + PM DB (`data/prediction_markets.db`, `mode=ro`).
No code/config/deploy/orders/UI. Engine (PID 969439) untouched. CP3 scope input — NOT a build.

## VERDICT
**MLB sub-division scope = MONEYLINE + TOTALS** (≈97% of SDTrading's observed edge vs 53% moneyline-only;
totals +91.0% cost-ROI ≈ moneyline +91.1%, statistically indistinguishable; totals add +47% volume,
net $2.87M→$4.23M). **Totals are copyable at the same edge.** **Spreads excluded — Kalshi lists no MLB run line.**

## (a) Kalshi lists MLB totals — YES: series `KXMLBTOTAL` ("Pro Baseball Total Runs")
Raw (`api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBTOTAL`, 54 open):
```
KXMLBTOTAL-26AUG241940TEXCWS-9   "Over 8.5 runs scored"   floor_strike 8.5
KXMLBTOTAL-26AUG241940TEXCWS-8   "Over 7.5 runs scored"   floor_strike 7.5
KXMLBTOTAL-26AUG241940TEXCWS-13  "Over 12.5 runs scored"  floor_strike 12.5
```

## (b) Deterministic ticker — YES, with one new dimension (the line)
`KXMLBTOTAL-{YYMONDD}{HHMM}{AWAY}{HOME}-{N}`, **strike = N − 0.5**. Same game-code stem as the
KXMLBGAME moneyline the matcher already resolves. YES = "Over"; Under = the NO leg of the same strike.
The executor's existing YES/NO + `reduce_only` machinery extends directly. **New matcher dimension: (game, line)
→ ticker** (was game → ticker). The existing `build_kalshi_game_index` extends to a (game, strike) index — modest.

## (c) LINE AGREEMENT — the decider. STRUCTURAL + MEASURED.
**Structural:** Kalshi publishes a **full half-run strike ladder** per game (1.5–13.5 observed, 11–13 strikes),
NOT one line. Polymarket totals are half-integer (Over/Under 8.5). So a Poly half-integer line lands on the
**identical** Kalshi strike, not the nearest — the "8.5 vs 9.0 is a different wager" problem dissolves.

**Measured (PM DB, mode=ro; SDTrading + xifutloong3 mlb totals; selection = category='mlb', pnl_suspect=0,
slug game-prefix + `-total` suffix = the accepted 167).** The Polymarket slug encodes the line as `Npt5`
(e.g. `-total-8pt5` = 8.5) — machine-extracted distinct-suffix audit, identical across two runs:

| whale | totals | line distribution (raw suffix → line × n) | exact-strike MATCH |
|---|---|---|---|
| SDTrading | 167 | 8.5×81, 7.5×43, 9.5×30, 10.5×7, 6.5×3, 11.5×2, 12.5×1 | **167 / 167 = 100%** |
| xifutloong3 | 9 | 7.5×6, 8.5×2, **15.5×1** | **8 / 9 = 88.9%** |
| **combined** | **176** | all half-integer | **175 / 176 = 99.4%** |

- **WHOLE-NUMBER push risk (residual i): ZERO.** Every observed line is a half-integer (`Npt5`). Polymarket
  does not use whole-number totals here, so there is no push-on-Poly / no-Kalshi-twin exposure. Risk retired.
- **FAR-TAIL (residual ii): one bet.** xifutloong3's single **15.5** (`mlb-sd-ari-2026-04-25-total-15pt5`) sits
  ABOVE the observed Kalshi ladder max (13.5). Row-share 1/9 = 11% but **net-share $10,478 = 39.8%** of
  xifutloong3's total-net (it won) — row-share ≠ net-share, flagged. **OPEN:** does Kalshi extend the ladder
  >13.5 for extreme-total games? UNVERIFIED (confirmed strikes only to 13.5). If not, that lone bet isn't copyable.
- SDTrading totals net $1,362,567.4 / cost $1,497,739.2 = 91.0% (cross-checks the earlier slice's totals +91.0%).

**Result: 175 of 176 traded total lines have an exact Kalshi half-run strike (99.4%).**

### Method honesty
The runner's automated `classify` step FIRST mislabeled all half-integers as whole numbers — a parser bug
(`8pt5` read as 8.0 because the regex only handled a `-5` separator, not `pt`). The **distinct-suffix audit
line built into the runner exposed it**; the corrected figures above are a deterministic relabel of the
parser-independent raw suffix counts (identical across both runs), verifiable from the printed suffixes.
Parser since fixed (`(?:pt|-)`); a confirming machine re-run is available but cannot change the audit-derived result.

## (d) Settlement — Polymarket documented; Kalshi UNVERIFIED → P3 pre-build requirement
- **Polymarket (docs.polymarket.us Sports FAQs):** MLB Totals settle on MLB's **official result** — a
  rain-shortened *called-official* game settles on the official score regardless of innings (called 1-0 →
  total settles on 1); postponed→rescheduled-within-~2wk uses the replay, else **settles at last fair price**;
  canceled/abandoned/no-result → **settles at last fair price** (not void-to-cost).
- **Kalshi (UNVERIFIED):** `rules_primary` = "collectively score more than 8.5 runs in the game originally
  scheduled for [date]", expiration +3d — does NOT state shortened/called/canceled handling (series rulebook
  not retrievable: market page 429'd, API object omits it). General sportsbook convention (not confirmed as
  Kalshi's): totals need 5 official innings.
- **Half-integer strikes ⇒ no push on either side**, so the common completed-game case aligns. Divergence risk
  is concentrated in shortened/called/canceled games (small share). **SPECIFIC QUESTION to own before go-live:**
  *(1) does KXMLBTOTAL settle a called-official shortened game on the official score like Polymarket, or require
  9/5 innings? (2) on a canceled/no-result game does Kalshi void-to-cost or settle-to-last-price — matching
  Polymarket's last-fair-price rule?* If they diverge, a copied total resolves differently on the two venues.

## (e) Spreads / run line — NOT copyable, DROP
`KXMLBRUNLINE` → 0 markets (series doesn't exist); Kalshi MLB is moneyline + totals only. Polymarket does list
MLB spreads, so SDTrading's spread bets (~3% of net, +74.8%) have **no Kalshi home**. Dropped, as pre-authorized.

## THREE P3 PRE-BUILD REQUIREMENTS
1. **Matcher/index gains a (game, line) strike dimension** — resolve a Poly total (game, over/under, line) →
   `KXMLBTOTAL-{game}-{line+0.5}` YES(over)/NO(under). Extend `build_kalshi_game_index` to (game, strike).
2. **Copy-only-when-the-exact-strike-exists-AND-is-liquid guard** — the ladder's tail strikes can be thin;
   also covers the far-tail case (a Poly line with no Kalshi strike, e.g. 15.5 > ladder max → skip, don't
   copy onto the nearest).
3. **Resolve the shortened/canceled-game settlement question (d) before totals go live** — Kalshi rulebook
   vs Polymarket, the two divergence classes above.

## LIVE-MONEY STATUS (snapshot 20:07Z, engine PID 969439)
- **KXMLBGAME index LOADED** — `index refreshed (909 games)` at 19:20/19:35/19:51 (the 19:05 boot-refresh
  failure self-healed via the steady-state 15-min refresh). Matching is live; the silent-no-op risk is closed.
- **poly_kalshi armed + 401-free** (`dry_run=False`, `POST_BOOT_401_COUNT=0`); **no Kalshi order fired** (the
  `would_have_placed` lines are the PAPER `polymarket_copy_trader` division, and `mace_entry_round placed:1`
  is MACE — neither is poly_kalshi). Gold-standard proof (a live placed order) still pending a new whale trade.

Runners (read-only, banked in `cc\`): pm_kalshi_mlb_discovery.*, pm_line_measure.* (+ .py).
Sources: Kalshi KXMLBTOTAL API; Polymarket Sports FAQs (docs.polymarket.us); PM DB mode=ro.
