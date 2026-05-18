# BitUnix Confluence-Gate Backtest — PA vs 5-Factor

**Window:** 2026-04-30 → 2026-05-17  ·  **Alerts:** 1796

## Pre-committed acceptance thresholds (Board mod #1)

These were locked before the backtest ran. Moving them after seeing the
numbers is the explicit failure mode this report blocks.

- Profit factor ≥ **1.20**
- Win rate ≥ **45.0%**
- Round-trips ≥ **20** (statistical floor)
- Fire rate ∈ **[5.0%, 50.0%]** of alerts
- Total R ≥ PA's total R (informational only if PA n < 20)

## Acceptance evaluation (5-factor arm)

- **PASS** · profit_factor: 2.01  (target: >= 1.20)
- **PASS** · win_rate: 48.48  (target: >= 45.0%)
- **PASS** · round_trips: 33  (target: >= 20)
- **FAIL** · fire_rate: 1.84  (target: in [5.0%, 50.0%])
- **PASS** · relative_total_r: 5f total R +16.16 vs PA +14.92  (target: >= PA total R)

**OVERALL: FAIL**

## Side-by-side summary

| Metric | PA arm | 5-factor arm |
|---|---|---|
| Fires | 26 | 33 |
| Round-trips | 26 | 33 |
| Win rate | 50.0% | 48.5% |
| Avg R | +0.574 | +0.490 |
| Total R | +14.92 | +16.16 |
| Profit factor | 2.31 | 2.01 |
| Return % | +0.80% | +0.75% |
| Max DD % | 0.10% | 0.09% |

## 2×2 outcome cross-tab

| | 5f fires | 5f rejects |
|---|---|---|
| **PA fires** | 2 (both agree, fire) | 15 (PA fires alone) |
| **PA rejects** | 19 (5f catches PA misses) | 1565 (both agree, skip) |

## Per-factor pass rates (5f arm)

| Factor | Passes / Evals | Rate |
|---|---|---|
| cvd | 376/597 | 63.0% |
| ema_alignment | 104/597 | 17.4% |
| volatility | 217/597 | 36.3% |
| volume_z | 94/597 | 15.7% |
| vwap | 257/597 | 43.0% |

CVD tick-rule fallback used in **597/597 (100.0%)** of 5f evals (expected = 100% for v1; flag flips False only when a future trade-stream consumer lands).

## Per-tier fire breakdown

| Tier | PA arm | 5-factor arm |
|---|---|---|
| PREMIUM | 5 | 2 |
| STANDARD | 21 | 31 |
| WEAK | 0 | 0 |

## Methodology + caveats

- Alerts pulled from prod `audit_event` `webhook_received` rows over
  the window above (resolution: per-alert timestamp).
- OHLCV: Coinbase BTC/USD 1m (NOT BitUnix futures). Live prod feeds
  the gate native BitUnix 3m/5m/15m kline. Apples-to-apples for the
  PA-vs-5f relative comparison; absolute trade outcomes carry a
  cross-venue volatility-profile fidelity gap.
- CVD: tick-rule fallback (close-direction sign × bar volume).
  Aggressor-side data is not available from BitUnix public; v1 of
  the gate accepts this as a known coarse signal. The dashboard
  banner (Phase D) surfaces `cvd_fallback_used` to operators.
- Sizing: per-tier nominal × leverage; effective risk capped at
  0.5%/trade; daily kill at 3% cumulative.
- Position model: one open trade at a time; opposite-side signal
  flips. No funding / fees modeled.

## Recommendation

**Cutover criterion:** all four absolute acceptance checks above must
PASS, and the relative-R check must PASS (or be informational with
PA n < 20). The Board records the final cutover decision in
`runbooks/deploy_log.md`; this report is input, not the decision.

Status from this run: **FAIL — hold; iterate gate config or tighten factor inputs before cutover**.
