# Polymarket weather copy-bot edge investigation — Board review

**Date:** 2026-05-28 · **Mode:** read-only, on-chain + market data, no trading, no deploy (Board-gated per CLAUDE.md §4)
**Author:** Claude Code session · **Scripts + data:** `reports/2026-05-28_polymarket_weather/`

---

## TL;DR (don't bury the lede)

1. **Do persistent weather-winning wallets exist on Polymarket?** **Yes — abundantly, and the edge is durable, not luck.** 1,413 of 1,987 high-activity wallets clearing the persistence bar (≥30 settled positions AND ≥15 distinct city-days) are net-positive; collectively +$13.3M realized. Early-half ROI predicts late-half ROI with **Pearson r = 0.69** (out-of-sample). **BUT** characterization shows the top winners are overwhelmingly **market-makers / liquidity-rewards farmers / scalpers (13 of top 19), not directional weather forecasters.** Their profit is spread capture + Polymarket maker rebates + harvesting retail order flow — **not** a forecast edge, and **not copyable** by a position-copying bot.

2. **Is Polymarket weather less efficiently priced than Kalshi?** **No.** Market-implied Brier on genuinely-uncertain ("interior") markets at the Kalshi-comparable evening-before horizon is **0.158** vs Kalshi's **0.161** — essentially tied; Polymarket is marginally *sharper*. US markets (0.137) are sharper than international (0.162), giving the international-inefficiency hypothesis only weak directional support — but even international Polymarket sits **at** the Kalshi efficiency benchmark, not looser than it. Spreads are *tighter* than Kalshi's cost assumptions.

3. **Honest read:** **The Kalshi efficient-market finding transfers.** There is no demonstrable copyable directional/forecast edge in Polymarket weather. The persistent winners are real, but they are playing a market-making game a copy-bot cannot replicate. **No edge here either** for the copy-bot thesis.

---

## The venue (Q1 market identification)

Polymarket's **"Daily Temperature"** program (`daily-temperature` tag, gamma id 103040). Each (city, date) is an *event* (`highest-temperature-in-<city>-on-<month>-<day>-<year>`) holding ~11 binary threshold markets (`...-19c`, `...-9corbelow`). Per-city daily series (e.g. `london-daily-weather`, recurrence daily).

| Metric | Value |
|---|---|
| Settled markets pulled (full census, weekly end-date windows) | **42,912** |
| Currently open markets (program is **LIVE**) | 2,080 |
| Cities | **51** (heavily international, many quoted in °C) |
| Date span | Dec 2025 → May 2026 (ramp: Feb 154 → Mar 721 → Apr 1,500 → May 1,591 events) |
| Total traded volume (settled) | **$363.7M** — a major category, not a backwater |
| Distinct wallets that traded weather | **53,336** (mostly one-off retail) |

International cities (forecast-skill-weaker hypothesis): london, paris, tokyo, hong-kong, seoul, shanghai, beijing, singapore, sao-paulo, buenos-aires, moscow, istanbul, mumbai-equivalents, etc. US: nyc, dallas, atlanta, miami, chicago, austin, denver, houston, los-angeles, san-francisco, seattle.

Note: the leaderboard (`/v1/leaderboard`) has **no weather category**, so wallets were discovered bottom-up from the per-market trade tape (`/trades?market=<conditionId>`), then each candidate's full realized P&L pulled from `/closed-positions?user=<wallet>`.

---

## Q1 — Persistent winners: yes, but the wrong kind of edge

### The bar (stated explicitly, per discipline)

- **Settled positions only.** Realized P&L from Polymarket's own `/closed-positions` (`realizedPnl` per resolved position). No open-position mark-to-market.
- **Persistence bar:** ≥30 settled weather positions **AND ≥15 distinct (city,date) events.** The second clause is load-bearing: 30 positions on the same 3 city-days across correlated threshold buckets is one correlated bet, not persistence. The bar requires breadth across days.
- **Population screened:** top 2,000 wallets by trade frequency (the copy-relevant, liquid-market-active set). Discovery sampled 1,644/42,912 markets (3.8%); appearance counts undercount true activity ~26×, so the screened wallets are genuinely high-frequency. Low-frequency wallets (<~50 lifetime positions) are out of scope as non-copyable.

### Result: persistence is real

- **1,987** screened wallets clear the bar; **1,413 (71%)** are net-positive.
- Collective realized P&L of bar-clearers: **+$13.3M** (winners +$13.7M, losers −$0.4M). This money comes from the ~51,000 *non-screened* one-off retail wallets — the active set harvests retail flow.
- **Out-of-sample persistence (the decisive test):** split each wallet's settled positions at its time-median into early/late halves. **Pearson r(early ROI, late ROI) = 0.694.** Early-top-quartile wallets earn **38% mean ROI in the later half, 98% stay positive.** Selecting on *total* PnL biases this correlation *downward*, so true persistence is if anything higher. **This is a durable, repeatable edge — not finite-sample luck or pure survivorship.**

### But it is market-making, not forecasting

Characterization of the top 19 winners (from the trade tape: sell-fraction, both-sides quoting, round-trip rate, entry lead time, entry price):

| Behavior label | Count | Reading |
|---|---|---|
| **Market-making / scalping** (high sells, round-trips, or two-sided quoting) | **13** | Earns spread + maker rebates, not forecast |
| Buy-and-hold (ambiguous accounting) | 4 | Likely structural too (see below) |
| Directional-forecast (buy + hold, lead time) | **1** (`gghff`) | Only clean forecast fingerprint |
| Favorite-harvesting (buys >0.9) | 1 (`airlorgedr`) | Real but uncopyable (buy at 0.99 for 1¢) |

Representative top wallets (all USDC):

| Wallet | Realized PnL | Settled pos / city-days | Profile fingerprint |
|---|---|---|---|
| ColdMath `0x594edb91` | +$1,169,986 | 2,812 / 1,377 | **both-sides 53%**, 0 directional sells → MM/complete-set |
| aapang `0x10417123` | +$797,908 | 1,920 / 928 | **97.6% sells** → liquidity provision |
| Poligarch `0xb40e8967` | +$403,241 | 2,122 / 1,249 | **both-sides 62%** → MM |
| VibeTrader `0xcbbc5e03` | +$286,237 | 1,242 / 635 | round-trip 57% → scalping |
| gghff `0x044f3345` | +$162,609 | 2,949 / 1,537 | lead 30h, 69% entries >24h → **only clean forecast-style** |
| airlorgedr `0xbc8405b2` | +$146,354 | 3,047 / 1,641 | entry px 0.99, lead 54h → favorite-harvesting |

**Evidence the P&L is structural, not forecast (verified ground-truth):** ColdMath positions show the held token settling to `curPrice=0.00` (a "loss" if held) yet **+$14.5K realized** on a $30K position — profit came from trading around the position / complete-set accounting, not from holding a winning side. Several "buy-and-hold"-labeled wallets show internally inconsistent entry-price/win-rate combos (dpnd buys at 0.074 yet "wins" 92%; DkOYL buys at 0.97 yet "wins" 34%) — a tell that per-position `realizedPnl`/`curPrice` is distorted by complete-set / merge / redeem accounting for structural traders. The reliable signal is the behavioral fingerprint from the raw tape, and it says **market-making**.

Polymarket pays **maker rebates** (`MAKER_REBATE` appears in the activity stream; events carry a `rewards-automation` tag). This is a liquidity-rewards game, and it explains both the high net-positive rate and why it coexists with an efficient mid-price.

### Fillable-size / copyability reality

- Median trade size among top winners is **$0.20–$13** — micro. Big P&L = volume × thin edge × rebates, not big directional bets.
- A copy-bot copies *directional entries*. It cannot post two-sided quotes, cannot earn maker rebates, and would be *buying an MM's inventory at the moment they accumulate* — eating adverse selection without the offsetting spread/rebate income. The persistent edge is structurally non-replicable by copying.

---

## Q2 — Efficiency vs Kalshi: the finding transfers

Apples-to-apples metric: market-implied Brier (P(YES) from last trade at/before a horizon vs realized outcome), on **interior** markets (implied prob in 0.05–0.95, matching Kalshi's "interior" filter), at the **evening-before** horizon (≈ Kalshi's leak-safe snapshot). City-stratified sample (2,040 markets across all 51 cities, spanning the volume range).

| Horizon | Interior Brier (all) | Interior US | Interior INTL |
|---|---|---|---|
| 24h | 0.1555 (n=683) | 0.1383 (n=126) | 0.1595 (n=557) |
| **18h (evening-before)** | **0.1576 (n=655)** | **0.1371 (n=121)** | **0.1623 (n=534)** |
| 6h | 0.1568 | 0.1436 | 0.1608 |
| 1h | 0.1692 | 0.1575 | 0.1759 |

**Kalshi benchmark (prior real-price study): market Brier 0.1607 (n=7,403 interior), best buildable model 0.1780, real-price EV −2.1%/contract.**

- **Polymarket interior Brier 0.158 ≈ Kalshi 0.161** — statistically indistinguishable; Polymarket marginally sharper.
- **US (0.137) sharper than international (0.162)** → the international-inefficiency hypothesis gets *weak directional support* (US n is small, so the gap is suggestive not conclusive). But international Polymarket sits **at** the Kalshi benchmark, not below it — i.e., not *exploitably* loose. Confirmed by per-position P&L being **identical** US vs intl ($6.73 vs $6.38/position).
- **Convergence:** interior Brier is flat ~0.155–0.169 from 24h to 1h → the market is sharp well before settlement and stays sharp. **No near-settlement mispricing window.**
- **Spreads (live open markets):** US median **0.4¢**, INTL median **0.7¢** — *tighter* than Kalshi's 1–2¢/side cost assumption. International has an illiquid wide-spread *tail* (mean 5.2¢) consistent with "thinner liquidity," but the median international market is tight.

**A buildable forecast model would need to beat market Brier 0.158 to have edge. The Kalshi study already proved no public-data model beats ~0.16 (best was 0.178). Same public forecast inputs → same wall. The efficient-market finding transfers.**

---

## Honest read & recommendation

**Is there a plausible copyable weather edge on Polymarket? No.**

- The market is **efficiently priced** (interior Brier ≈ Kalshi), including international stations — so there is no forecast mispricing for a directional model or copy-bot to harvest. The Kalshi conclusion transfers cleanly.
- Persistent winners **do exist and have a durable edge** (r=0.69 out-of-sample) — this is the one genuinely surprising result — but the edge is **market-making / liquidity-rewards / retail-flow capture**, structurally **non-copyable** by a position-mirroring bot, and it coexists with (does not contradict) the efficient mid-price.
- The one clean directional-forecast wallet (`gghff`) is a single ambiguous case against an efficient-market backdrop and the prior exhaustive Kalshi null; it is not a basis for a strategy.

**Recommendation: shelve the Polymarket-weather copy-bot thesis.** Do not stand up a weather copy division. The copyable-forecast-edge premise is falsified on the same grounds as Kalshi. If the Board ever wants to pursue Polymarket weather, the *only* viable game is market-making/liquidity-rewards — a different build (two-sided quoting + rebate capture + inventory risk management), not copy-trading, and out of scope here.

### Caveats / limitations (don't over-claim)

- `realizedPnl` from `/closed-positions` is Polymarket's reported net realized USDC per resolved position and is distorted for complete-set/merge/redeem traders; behavioral conclusions lean on the raw trade tape, which is clean.
- Wallet screening covers the top 2,000 by activity (the copy-relevant set); a hypothetical persistent directional winner with <~50 lifetime positions would be missed, but such a wallet is not copyable at size anyway.
- Discovery is volume-weighted; it preferentially finds liquid-market (copyable) players — an acceptable, even desirable, bias.
- US-interior Brier n=121 is modest; the US-vs-international sharpness gap is directional, not strongly powered.
- Past performance (even with r=0.69 persistence) is not a guarantee of forward edge, and forward edge for *market-making* is not the same as forward edge for *copying*.

### Data provenance (reproducible)

All read-only, public, unauthenticated Polymarket endpoints. Pipeline in `reports/2026-05-28_polymarket_weather/`:
`pm_wx_01_markets.py` (42,912-market census) → `pm_wx_02_discover.py` (53,336 wallets) → `pm_wx_03_deepdive.py` (per-wallet settled P&L) → `pm_wx_04_characterize.py` (forecast-vs-MM) → `pm_wx_05_efficiency.py` (Brier vs Kalshi) → `pm_wx_06_persistence.py` (split-half r=0.69). Kalshi benchmark from `reports/2026-05-28_kalshi_weather_ev_discovery.md`.
