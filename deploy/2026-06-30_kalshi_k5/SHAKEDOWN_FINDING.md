# Kalshi K5 — Prod Shakedown Finding (2026-06-30): LIVE PLACEMENT BLOCKED by Kalshi V2 migration

**Board-authorized $1 real-money prod test. Result: order REJECTED, $0 spent, account unchanged.**

## What happened
A real 1-contract YES marketable IOC on `KXBTCD-26JUN3004-T59399.99` ("$59,400 or above", 4am EDT
hourly), placed via the K5 `KalshiLiveBroker` against prod, was rejected:

```
PLACEMENT ERROR (loud): 410: Please switch to the V2 endpoints
(deprecated_v1_order_endpoint) [POST /portfolio/orders]
```

Account re-confirmed after: **cash $532.07, portfolio $0.00, 0 positions** — identical to before. No
fill, no money moved, nothing to unwind.

## Root cause (confirmed against docs.kalshi.com)
- pykalshi 1.0.6 POSTs the **deprecated v1** endpoint `POST /portfolio/orders`. Kalshi slated its
  removal "no earlier than 2026-05-06"; today (2026-06-30) is past that, so it now returns **410**.
- The current endpoint is **V2 `POST /portfolio/events/orders`**, with a CHANGED request shape:
  - `side` = **`bid`/`ask`** (single-book model) — NOT the v1 `action`(buy/sell) + `side`(yes/no).
  - `count` = FixedPointCount string ("10.00"); `price` = FixedPointDollars string (≤6 dp).
  - `time_in_force` unchanged; new `self_trade_prevention_type` ("taker_at_cross"/"maker").
- Recommended prod host is now `https://external-api.kalshi.com/trade-api/v2`. pykalshi uses the
  legacy-but-still-"supported" `api.elections.kalshi.com` — the host is NOT the cause; the
  **endpoint + request shape** is.

## What the test DID validate (works on real prod)
RSA-PSS auth/signing · reads (balance, markets, market lookup, snapshot) · `connect()` + funded
preflight · ProposedOrder→kalshi mapping · and the broker's **loud error handling** — it caught the
410 and raised `OrderPlacementError` with **no phantom fill and no position**. The shakedown did its
job: caught a critical integration blocker for $0 and proved the safety path.

## What is still UNVALIDATED (blocked by the 410)
Real fill · the positions field-bug fix (`position_fp`/`market_exposure_dollars`) on a NON-zero
position (only confirmed clean on the empty 0-position case) · cancel · FOK-reject · idempotency ·
the reduce_only close path.

## Fix = K5.1b (a proper follow-up, not a rushed live hack)
The YES/NO + buy/sell → **bid/ask + yes-price** mapping must be EXACT (wrong = wrong side of a real
market = real loss), so this is built + DEMO-validated before any live fire.

- **Option A:** bump pykalshi to a version that targets V2 `/portfolio/events/orders` (re-opens the
  deps/lockfile; check PyPI for > 1.0.6).
- **Option B:** patch `KalshiLiveBroker` to POST the V2 shape via pykalshi's authed client (bypass its
  deprecated `place_order`), parse the V2 response into `FillEvent`, and verify cancel/get endpoints.
- Either way: plumb an `api_base` override (flagged earlier) and switch hosts to `external-api.kalshi.com`
  (prod) / `external-api.demo.kalshi.co` (demo).
- **DEMO validation prerequisite:** a SEPARATE demo keypair from `demo.kalshi.co` (prod keys 401 on
  demo — empirically confirmed; Kalshi docs: creds are not shared across environments).
