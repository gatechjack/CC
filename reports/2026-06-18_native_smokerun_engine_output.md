# BitUnix PA-redeem-cap backtest — ENGINE VALIDATION (NOT a §4 verdict)

> **THIS IS ENGINE VALIDATION / A SMOKE RUN — NOT THE §4 REDEEM-CAP VERDICT.**
> The corpus here (`btc_scalping.db` bars_3m) is only a modest ~1.9x vol
> gradient (Mar→May 2026); a defensible §4 verdict REQUIRES a high-vol 3m
> regime (separate data-ingest task) for regime robustness. Do not cite these
> numbers as the redeem-cap decision.

Window: 2026-05-15 → 2026-06-19  ·  3m corpus  ·  VIP3 taker 0.09%rt / maker 0.064%rt

Decision metric = **net-of-cost expectancy per fire** (NEVER fire-rate).

| arm | first-pass | redeem | dropped | plan-skip | walked | gross/fire | **net-taker/fire** | net-maker/fire | redeem net-taker |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| no_redeem | 254 | 0 | 3205 | 176 | 78 | -0.0545 | **-0.4005** | -0.3006 | +0.0000 |
| cap_1bar | 234 | 89 | 3104 | 233 | 90 | -0.1109 | **-0.4572** | -0.3572 | -0.5918 |
| current | 207 | 267 | 2917 | 376 | 98 | -0.0246 | **-0.3725** | -0.2720 | -0.3593 |

## Arms
- **no-redeem** (`--redeem-cap 0`): PA-reject drops; no deferred entry.
- **cap@1bar** (`--redeem-cap 1`): re-evaluate PA for 1 bar; fire-or-abandon.
- **current** (`--redeem-cap 30`): re-evaluate until PA pass / score-decay / cap.

## Methodology
- v2 economics: the real `build_trade_plan` (3-leg + fee gate) + the entry-timing
  harness bar-walk (SL-first tie, ordered TP, BE-after-TP1 / TP1-after-TP2 ratchet).
- Redeem fires priced at the **FIRE bar** (not the stale signal price).
- Per-fire independent walks; net-of-cost expectancy per fire is the metric
  (not a compounded equity curve; one-open-at-a-time effects out of scope).
- Cooldown threaded via last_fire_ts; redeem look-ahead introduces minor
  ordering imperfection vs prod — acceptable for engine validation.
