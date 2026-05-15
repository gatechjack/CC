# BitUnix Futures — HTF Regime Module

Pure-function classifier that consumes 1H / 4H / 1D OHLCV (plus funding rate
and current price) and produces a unified market-regime verdict + a
trade-permission decision the order pipeline consults before placing or
sizing a BitUnix order.

This is the **explicit replacement** for the implicit HTF context that used
to come from Cypher 4h/1D webhook fires. Cypher signals are still ingested
(they hit the ledger and may still appear in the Phase 3.2 score for 3m chart
fires), but they no longer drive direction or sizing — that's this module's
job.

## Where it sits

```
3m signal arrives
       ↓
Score eval (bitunix_confluence)         ← 3m-only signal sum, picks winning_side
       ↓
PA validation (bitunix_pa_validation)   ← bar-level vwap / volume / structure
       ↓
HTF gate (this module)                  ← multi-TF regime → permission + multiplier
       ↓
Risk gate → place_order / would_have_placed
```

Pure-function design: all I/O (cache reads, funding fetch) lives in
`data/bitunix_htf_context.py` (PR 2). This module is fully testable with
synthetic OHLCV — no network, no clock, no global state.

## The pipeline

### 1. Per-timeframe classification

Each of 1H, 4H, 1D runs the same indicator stack on its own closed bars:

| Indicator | What it tells us |
|---|---|
| **EMA(20/50/200)** alignment | Is the trend stack stacked? `Bull` if price > EMA20 > EMA50 > EMA200; `Bear` if inverse; otherwise `Mixed`. |
| **Market structure** (swing pattern over last `swing_lookback` bars) | `Bull` if recent swing-high > prior swing-high AND recent swing-low > prior swing-low. `Bear` if inverse. |
| **ADX(14)** | `>adx_trend_threshold` (default 20) = trending market. Used to distinguish Bull/Bear from Range/Transitional. |
| **MACD histogram** sign | Momentum direction tiebreaker when struct can't decide. |

Combined into one of `Bull | Bear | Range | Transitional | Insufficient`:

```
Bull         = EMA Bull AND (struct Bull OR (ADX trending AND MACD>0))
Bear         = EMA Bear AND (struct Bear OR (ADX trending AND MACD<0))
Range        = ADX < threshold AND EMA Mixed
Transitional = anything else (conflicting signals)
Insufficient = not enough bars for the longest indicator (EMA200)
```

### 2. Composite regime score

Per-TF contributions are weighted (default `0.5 * d1 + 0.3 * h4 + 0.2 * h1`)
and summed. Each TF contributes `+1` (Bull), `-1` (Bear), or `0` (everything
else):

| Score | Regime |
|---|---|
| `≥ +0.7` | `STRONG_BULL` |
| `≥ +0.3` | `BULL` |
| `-0.3 < s < +0.3` | `NEUTRAL` |
| `≤ -0.3` | `BEAR` |
| `≤ -0.7` | `STRONG_BEAR` |

Why those weights? **1D moves the regime by 0.5 alone** — a daily flip can
cross the BULL/NEUTRAL/BEAR boundary. **4H confirms** — aligned 4H pushes
past the STRONG threshold; opposing 4H pulls toward NEUTRAL. **1H modulates
size, rarely flips regime** — its ±0.2 vote alone never crosses a 0.3
threshold; it can only refine within a regime.

### 3. Context fields

Computed alongside the classifications and used by hard-zero checks:

- `volatility_tier`: 1D ATR(14) as % of price → `Low / Normal / High / Extreme`
- `nearest_resistance`, `nearest_support`: union of swing highs/lows on
  4H + 1D + prior-day H/L; closest above/below current price wins
- `distance_to_resistance_pct`, `distance_to_support_pct`
- `session`: UTC clock → `Asia / London / Overlap (London-NY) / NewYork`
- `funding_rate` (caller-supplied) and `funding_extreme` (`|rate| >
  funding_extreme_pct_per_8h`)

### 4. Permission matrix

`get_trade_permissions(verdict, proposed_side, config)` returns:

```python
TradePermission(
    allow_long: bool,        # base matrix permission (informational)
    allow_short: bool,
    size_multiplier: float,  # 0.0 / 0.5 / 1.0 — what the order pipeline applies
    reason: str,             # human-readable matrix path
    hard_zero_reason: str | None,
)
```

Base matrix:

| Composite | h1 = Bull | h1 = Bear | h1 = Range/Trans/Insuff |
|---|---|---|---|
| `STRONG_BULL` | long 1.0× | long 1.0× | long 1.0× |
| `BULL` | long 1.0× | **long 0.5×** *(pullback)* | long 1.0× |
| `NEUTRAL` | long+short 0.5× | long+short 0.5× | long+short 0.5× |
| `BEAR` | **short 0.5×** *(bounce)* | short 1.0× | short 1.0× |
| `STRONG_BEAR` | short 1.0× | short 1.0× | short 1.0× |
| `SAFE_MODE` | (all blocked) | (all blocked) | (all blocked) |

The H1 split exists for the BULL+H1Bear and BEAR+H1Bull cases:
- BULL composite + H1 Bear = pullback against the trend → still allow long
  (trend wins long-term) but at half size (catching a knife in the small)
- BEAR + H1 Bull = bear-market bounce → still allow short, half size

### 5. Hard-zero overrides

Applied in this order; first match wins, all force `size_multiplier=0`:

1. **Side conflicts with permission** → `regime_forbids_side`
2. **Proximity to opposing HTF level** (`distance < proximity_block_pct`,
   default 0.3%) → `proximity_to_resistance` / `proximity_to_support`
3. **Volatility tier == Extreme** → `vol_tier_extreme`
4. **Funding extreme + side matches crowded** → `funding_extreme_crowded`
   (positive funding crowds longs; negative funding crowds shorts)

### 6. SAFE_MODE

If the caller cannot supply ANY HTF data (all three timeframes missing or
all marked `Insufficient`), regime returns `SAFE_MODE` → permission returns
multiplier=0 universally. This is the fail-closed contract that mirrors
CLAUDE.md's "VIX-feed-unavailable is fail-safe to Board" principle: **no
data = no trades.**

A single missing/insufficient TF (e.g. 1H during cold-start warmup while
4H + 1D have full history) does NOT trigger SAFE_MODE — the missing TF
contributes 0 to composite; the other two govern.

## BitUnix data handling notes

- **Closed bars only.** Caller (`LiveBarCache`) drops the in-progress bar
  before constructing `TimeframeBars`. Repainting protection is upstream;
  this module trusts the input.
- **Symbol convention.** BitUnix uses `BTCUSDT` (no slash) on its API. The
  classifier doesn't care about the symbol — it operates on bar values
  only.
- **Funding rate.** BitUnix's public endpoint is
  `GET /api/v1/futures/market/fundingRate?symbol=BTCUSDT` (no auth). The
  decimal returned is per 8h period (e.g., `0.0001` = 0.01% per 8h). The
  `funding_extreme_pct_per_8h` config is in **percent** (e.g., `0.05` for
  0.05%) — the classifier compares `|rate * 100| > threshold`.
- **Cache sizing.** EMA(200) needs 200+ closed bars per TF. PR 2 instantiates
  three `LiveBarCache` instances with `max_bars >= 250` to give warmup
  margin. If BitUnix's kline `limit` cap is below 250, paginate or accept
  the cap and tighten warmup tolerance.
- **Cache poll cadence.** Recommended: 1H every 3-5 min, 4H every 15 min,
  1D every 30 min. Indicators re-compute on bar close; same-bar polls
  return cached values.

## Reading debug logs

Every HTF eval writes a `htf_gate_decision` audit row (PR 3). The payload
includes the full chain so a reader can reconstruct WHY a trade was blocked
or sized down without re-running the classifier. Example payload structure
(JSON):

```json
{
  "regime": "BULL",
  "composite_score": 0.6,
  "h1": {
    "regime": "bear",
    "ema_alignment": "mixed",
    "structure": "range",
    "adx": 15.2,
    "macd_hist": -0.0034,
    "reason": "conflicting: EMA=mixed; struct=range; ADX=15.2, MACD_h=-0.0034"
  },
  "h4": {
    "regime": "bull",
    "ema_alignment": "bull",
    "structure": "bull",
    "adx": 24.7,
    "macd_hist": 0.012,
    "reason": "EMA bull aligned; struct=bull; ADX=24.7, MACD_h=0.0120"
  },
  "d1": { ... },
  "volatility_tier": "normal",
  "atr_pct_d1": 1.42,
  "nearest_resistance": 71200.0,
  "distance_to_resistance_pct": 0.45,
  "session": "new_york",
  "funding_rate": 0.0002,
  "funding_extreme": false,
  "proposed_side": "buy",
  "permission": {
    "size_multiplier": 0.5,
    "reason": "BULL + H1=bear: long 0.5x (pullback only)",
    "hard_zero_reason": null
  }
}
```

Common debug patterns:

| Symptom | Where to look |
|---|---|
| Trades not firing during what looks like a clean trend | `composite_score` near a threshold boundary; check per-TF `reason` for which TF disagrees |
| Size multiplier 0.5 unexpectedly | `regime` = BULL but `h1.regime` = bear (pullback) or vice versa |
| Trade blocked with `hard_zero_reason: proximity_to_resistance` | Price is within 0.3% of nearest 4H/1D swing high or prior-day high — check `distance_to_resistance_pct` |
| All trades blocked, `regime: SAFE_MODE` | Cache poll loops are dead OR all three TFs lost their bars (data outage) — check upstream cache health |
| `funding_extreme_crowded` blocking longs | `funding_rate` > +0.05% per 8h AND your signal is BUY — too many longs piled in |

## Configuration (PR 3 will add to `strategies.yaml`)

```yaml
bitunix_futures:
  htf_regime:
    enabled: false                  # PR 1 ships off; PR 3 turns on in shadow mode
    ema_periods: [20, 50, 200]
    adx_period: 14
    adx_trend_threshold: 20.0
    swing_lookback: 20
    swing_n: 2
    macd_periods: [12, 26, 9]       # fast, slow, signal
    composite_weights:              # must sum to 1.0
      d1: 0.5
      h4: 0.3
      h1: 0.2
    regime_thresholds:
      strong_bull: 0.7
      bull: 0.3
      bear: -0.3
      strong_bear: -0.7
    vol_tier_atr_pct:               # 1D ATR(14) as % of price
      low: 0.5
      normal: 1.5
      high: 3.0
      extreme: 5.0
    funding_extreme_pct_per_8h: 0.05
    proximity_block_pct: 0.3
```

`HTFRegimeConfig.defaults()` returns these same numbers — useful for tests
and for sanity-checking the YAML parse.

## Roadmap

- **PR 1** *(this PR)*: pure classifier + tests + this README. No production
  paths touched.
- **PR 2**: `BitunixBroker.get_funding_rate()`, three `LiveBarCache`
  instances in `main.py`, `data/bitunix_htf_context.py` impure boundary,
  dashboard partial showing live regime classification.
- **PR 3**: Score-engine rework (3m-only filter, per-TF TTLs, Cypher 4H/1D
  log-only), PA validation module, observer wires HTF gate after PA
  validation. New `htf_gate.mode: off | shadow | enforce` flag, default
  `shadow`. Replay script for tuning thresholds against historical ledger.
- **PR 4**: Flip `htf_gate.mode: enforce` after shadow data review.
  Board-approval moment.
