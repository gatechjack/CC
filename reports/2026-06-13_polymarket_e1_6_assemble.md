# E1·6 — assemble PolymarketLiveBroker(Broker)

**Date:** 2026-06-13
**Branch:** `polymarket-e1-6-assemble-2026-06-13` (base consolidated main `c3f6b68`)
**Mode:** BUILD + TEST, mocked/fundless, **UNMERGED**. No funds, no real placement/auth (mocked
client only), no deploy, no prod write. Disclosure per `82fda13`.
**Increment:** E1·6 — the integration slice. (preflight = E1·7 — NOT here.)

---

## The class — `PolymarketLiveBroker(Broker)` (in `polymarket_live.py`)

A **placement-legal `Broker`** (NOT `ReadOnlyBroker`) — it has `place_order`/`cancel_order`.
- **Reads by composition:** holds a `PolymarketBroker` (the read adapter) and delegates
  `connect`/`disconnect`/`snapshot`/`quote` to it (so `quote` is E1·5's SDK-midpoint).
- **`connect()` L2-authorizes** the placement client: build an L1 `ClobClient(host, chain_id=137,
  key=private_key)`, then `create_or_derive_api_creds()` → `set_api_creds()` so `post_order` (L2
  auth) is permitted. `paper = False` (live). `_build_clob_client()` is isolated for testability.
- **`place_order(order) -> FillEvent`** (E1·2/3) and **`cancel_order(order_id) -> bool`** (E1·4)
  delegate to the module fns with the L2-authed client; both require `connect()` first.
- `place_multi_leg`/`get_option_greeks` inherit `Broker`'s `NotImplementedError` (no multi-leg).

**L2 auth (grounded, SDK `client.py:213-227`):** `create_or_derive_api_creds(nonce=None) -> ApiCreds`
(tries `create_api_key`, falls back to `derive_api_key`); `set_api_creds(creds)` sets creds + flips
the client to L2 mode. Both need an L1 client (the signer key) — which `_build_clob_client` provides.

## ⚠ The half-flip finding + fix (CRITICAL — the Bitunix lesson)

The factory `_build_broker_for_division` (`main.py`) gates live brokers on
`is_live_family = (mode == "LIVE" and family in --brokers)`. Every other live family
(robinhood/fidelity/coinbase/bitunix/tastytrade) has `if is_live_family: return <LiveBroker>`. **The
`polymarket` family branch had NEITHER a live branch NOR a PaperExecutionBroker wrap — it
unconditionally returned the read-only `PolymarketBroker`.** So a LIVE+selected polymarket division
would **silently resolve the read-only adapter and never place** — exactly the half-flip.

**Fix (E1·6):** added `if is_live_family: return PolymarketLiveBroker(...)` to the polymarket branch
(per-division wallet by slug, mirroring bitunix). **Surfaced — how PCT goes live (no silent
half-flip):**
1. `divisions.yaml` PCT `broker: paper → polymarket` (operator/E6 config flip — NOT this slice;
   today PCT is `broker: paper` → resolves the *paper* family → PaperBroker);
2. process `mode = LIVE`;
3. `--brokers polymarket` selected.
→ then the factory returns `PolymarketLiveBroker`. Any one missing → read-only/paper (safe). **Known
granularity limit:** `--brokers polymarket` is *family*-level, so it would also flip the arbitrage
division live — per-strategy live (PCT live while arb paper) needs a per-strategy `execution_mode`
(the scoping's **E6**), not built here. Flagged so it isn't a surprise.

## Tests (mocked, fundless) — 11 box-passing

`tests/test_polymarket_live_broker_assembly.py` (the delegated pieces are E1·2-5-tested; here =
WIRING):
- **ABC conformance:** `PolymarketLiveBroker` `isinstance Broker` (placement-legal), `paper is
  False`; the read `PolymarketBroker` `isinstance ReadOnlyBroker` and **NOT** `Broker` (the
  placement-legal-vs-read-only distinction).
- **connect L2 auth:** injects a mock client → asserts `create_or_derive_api_creds` +
  `set_api_creds(creds)` called, `_clob` set, connected.
- **place/cancel delegate** to the E1·2-4 module fns with the L2 client; **require `connect()`**
  (raise otherwise).
- **snapshot/quote delegate** to the read adapter.
- **Factory anti-half-flip:** `LIVE`+`--brokers polymarket` → `PolymarketLiveBroker`; `PAPER` →
  read-only; `LIVE` but not selected → read-only.
- **Full same-env gate (vs pristine main `c3f6b68`, `git stash -u`): `EDITED=31 == PRISTINE=31`,
  diff empty → zero new regressions** (the factory's PAPER path is unchanged; the new LIVE branch
  fires only under `is_live_family`, which the suite doesn't exercise).
- Real CLOB/L2/placement never hit (mocked); no funds.

## Carried-forward

- `connect()`'s real L2 authorization (`create_or_derive_api_creds` POSTs to the CLOB) + the real
  `ClobClient` construction are exercised only against the **live CLOB** — confirmed at the
  operator-gated **$1 shakedown** (tests mock them). Standing E1·1-5 carry-forwards apply
  (status/cancel/midpoint response shapes; Linux-OS signing; `activity.asset` E2).
- `place_order` uses `market_fetcher=None` (direct `extra["token_id"]` path — E2 propagates
  `activity.asset`); a gamma fallback fetcher can be wired later.

## Phase D — status

- `polymarket_live.py` (+`PolymarketLiveBroker`), `main.py` (factory live branch), new
  `tests/test_polymarket_live_broker_assembly.py` + this report, committed on the branch.
  **UNMERGED.** No deploy/merge.
- **Hard stops honored:** assembles as a placement-legal `Broker` (NOT read-only); factory does NOT
  silently return Paper when live (the half-flip is **fixed + tested**); L2 auth **grounded**;
  mocked/no funds/no real placement; stayed in-slice (no preflight E1·7); no prod write; zero
  regressions.

## Next — E1·7

Preflight: a `"polymarket"` branch in `assert_live_ready` (`utils/secrets.py:403`) checking the
per-division key+funder (and, read-only on-chain, USDC.e balance + allowance) before a LIVE start —
so a mis-provisioned/unfunded/unapproved wallet aborts loudly.

---

*E1·6 artifact — committed unmerged on `polymarket-e1-6-assemble-2026-06-13`. Builds on E1·1–5 (main
`c3f6b68`). Source: py-clob-client 0.17.5 `client.py` (L2 auth `create_or_derive_api_creds`/`set_api_creds`).*
