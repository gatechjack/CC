# Polymarket Arbitrage Backtest Runbook

**Purpose.** Run the binary-outcome replay tool against accumulated paper
trades to produce a Backtester verdict for `polymarket_arbitrage`. The
verdict gates flipping `auto_execute: true` (per CLAUDE.md §6 + the
Board memo template).

**Phase 2.5 minimal-viable scope** (Q4 of the original Polymarket scope
memo). Replay-only — no Monte Carlo, no slippage, no time-decay. See
`scripts/backtest_polymarket_arbitrage.py` docstring for what's
explicitly out of scope.

---

## When to run

- **First run:** after `polymarket_arbitrage.enabled: true` has been
  active for **30+ days in paper mode** (`auto_execute: false`).
  Anything earlier produces `INSUFFICIENT_DATA` because the n=30
  threshold gate fires.
- **Periodic re-runs:** weekly is fine. Monthly is fine. The script is
  idempotent + read-only. Don't run more often than 24h apart unless
  you want to track a streak.
- **Before any `auto_execute: true` flip:** **mandatory.** The verdict
  goes into the Board memo per CLAUDE.md §"Backtester approval" rule.

## Prerequisites

- Trading-corp prod is reachable (`ssh azureuser@trading.jacksumner.com`)
  AND the local `data/trading_corp.db` SQLite mirror is fresh, OR you
  pass `--db sqlite:///<path-to-prod-db-copy>` to point at a snapshot.
  Easiest: scp prod's DB locally and run against it.
- Network reach to `gamma-api.polymarket.com` (US-east IP works fine —
  smoke-tested 2026-05-09).
- (Optional) `POLYGON_RPC_URL` env var set to your Alchemy URL —
  default uses a free public RPC which has no SLA.

## Running

```bash
# Default: 30-day horizon, local DB
python scripts/backtest_polymarket_arbitrage.py

# Or against a prod snapshot
scp azureuser@trading.jacksumner.com:/home/azureuser/trading_corp/data/trading_corp.db /tmp/tc.db
python scripts/backtest_polymarket_arbitrage.py --db sqlite:////tmp/tc.db --days 60

# Machine-readable JSON output (for Board memo attachments)
python scripts/backtest_polymarket_arbitrage.py --json > /tmp/backtest_$(date +%Y%m%d).json
```

Exit code is always 0 (regardless of verdict) — the script outputs
metrics for human review, not a pass/fail gate that scripts should
branch on.

## Reading the output

### Header counts

```
Paper rows total: 247
  Resolved:    198    ← these score; have outcomes
  Pending:      42    ← markets still in flight; won't score until resolution
  Not found:     5    ← market deleted/restructured; bug worth investigating
  Void:          2    ← market voided / disputed; can't score
```

`Resolved + Pending + Not found + Void` should ≈ `Paper rows total`. If
`Not found` > 5% of total, something is off — either the strategy is
emitting bad condition_ids or gamma-api is returning unexpected
shapes. Investigate before trusting the verdict.

### Resolved-trade metrics

```
Hit rate:           58.1%  (115W / 83L)    ← % of trades that won
Total notional:     $198.00                ← USD risked at $1/trade
Total P&L:          +$23.45                ← what we'd have made/lost
ROI:                +11.8%                 ← P&L / total notional
Avg P&L per trade:  +$0.118
Median P&L:         -$0.380                ← if median < 0, distribution is heavy-tailed
Max drawdown:       $4.20                  ← worst peak-to-trough loss streak
```

**The two numbers that matter most for the verdict:**
- **Hit rate** — must be meaningfully above the implied-prob baseline
  for the LLM signal to be worth anything. ≥55% is the conventional
  bar for prediction-market alpha.
- **ROI** — at $1/trade fixed shakedown sizing, even a small +ROI%
  over 100+ trades is a real signal.

### Per-category breakdown

Look for categories where the strategy WORKS vs DOESN'T. If sports
markets show 65% hit and politics shows 40%, that's a tell:

```
By category:
  sports         n= 76  hit=65.8%  total_pnl=+$15.20  avg=+$0.200
  politics       n= 38  hit=42.1%  total_pnl=-$3.80   avg=-$0.100
  geopolitics    n= 22  hit=54.5%  total_pnl=+$2.10   avg=+$0.095
  …
```

Action: surface in the Board memo so we can decide whether to scope
the strategy to a subset of categories.

### Verdict

The script outputs ONE of three:

- `RECOMMEND_APPROVAL` — n≥30, hit≥55%, avg_pnl > 0, ROI > 5%. Board
  reviews + signs off; flip `auto_execute: true` (or stay HITL with
  confidence the strategy is on solid ground).
- `RECOMMEND_REJECTION` — hit < 45% or avg_pnl < -$0.05. Strategy is
  losing money. Don't enable. Investigate why; either tune (different
  divergence threshold, narrower category set, different LLM prompt)
  or stay paper-mode indefinitely.
- `MIXED_SIGNAL` — neither approval nor rejection threshold cleared.
  Continue paper-mode; re-run in 30 days.
- `INSUFFICIENT_DATA` — n<30. Wait.

**The verdict is a recommendation, not a gate.** Board has final say
per CLAUDE.md §1 ("HITL approval is mandatory until `auto_execute` is
explicitly flipped").

## Board memo template

When writing the Board memo for an approval/rejection decision, attach
the JSON output and reference these specific numbers:

```markdown
## Polymarket Arbitrage — Phase 2.5 Backtester verdict

Run: 2026-MM-DD via `scripts/backtest_polymarket_arbitrage.py --days 30 --json`
Horizon: 30d paper-mode (started YYYY-MM-DD)
Resolved trades: <N>
Hit rate: <X>%
Total P&L: <$X> (ROI: <X>%)
Max drawdown: <$X>
Verdict: <RECOMMEND_APPROVAL | REJECTION | MIXED | INSUFFICIENT_DATA>

Per-category strength:
  - <best category>: hit X%, avg P&L $Y
  - <worst category>: hit X%, avg P&L $Y

Decision: <Approve `auto_execute: true` | Stay paper-mode | Disable | Re-scope categories>
Rationale: <…>
Next review: <YYYY-MM-DD>
```

## Common questions

**Q: Why does the script show "Not found" for some markets?**
Polymarket can deactivate / restructure markets after they're proposed.
Rare but happens. <5% is acceptable. >5% suggests a bug or stale slug
in our `would_have_placed` rows — investigate.

**Q: Why ignore "void" markets?**
A market resolves "void" when the resolution source is ambiguous
(e.g., game cancelled). Polymarket refunds the bet at entry price, so
P&L is zero — but the strategy's signal didn't get tested. Excluding
them from metrics is more honest than counting them as wins/losses.

**Q: What's the `condition_id` vs `slug` lookup?**
The `condition_id` is the on-chain Polygon contract identifier; the
`slug` is the URL-safe market name. The broker's
`get_market_resolution()` tries `condition_id` first (more stable),
then `slug`. If the strategy emits BOTH on `would_have_placed`
(it does as of Phase 2a), lookups should be 99% reliable.

**Q: How long should we keep paper-mode running before the first
backtest?**
30 days minimum, 60 days is more useful — the n=30 threshold is
actually weak signal; 100+ trades produces a much more confident
verdict. At K=10 markets/cycle and 6h cooldown, the strategy proposes
~50-200 unique markets per day, but `would_have_placed` only fires
on divergence ≥ 10% — so most cycles produce 0-3 rows. 30 days at
that rate is roughly 30-60 paper trades; 60 days is 60-120.
