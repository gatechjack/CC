# Item 6 — Per-Polymarket-division wallet pattern + arb migration (PLAN ONLY)

**Date:** 2026-05-29
**Branch:** `polymarket-live-prep-2026-05-29`
**Status:** **PLAN ONLY — no build, no deploy, no on-chain action this session.** For ratification before any build; build sequencing TBD after ratify.
**Source:** readiness report `reports/2026-05-28_polymarket_copy_live_readiness.md` item 6 (the one **L**); session findings `reports/2026-05-29_polymarket_live_prep_groupB_spike.md`.

---

## Funding-target decision (recorded)

**Option A — fund the new PCT EOA directly after item 6 lands.** Not converting the existing wallet's $500 native USDC.
- Rejected Option B (fund existing wallet first): walks back the locked per-division decision under speed pressure, and signs a DEX swap with the **production key** for a conversion we don't strictly need.
- The $500 native USDC on the existing arb wallet (`0x2FC7…DA11`) **stays put.** arb has been read-only and may stay that way; it does **not** need USDC.e until/unless it also goes live — a **separate** operator decision, explicitly **not** bundled into PCT's go-live path.
- Cost accepted: item 6 (the plan's only **L**) lands before the visible "we can trade" adapter (item 8).

---

## Current single-wallet plumbing (what exists today)

One shared credential set flows to every Polymarket division. The factory keys off **family** (`division.broker == "polymarket"`), not slug, so today both Polymarket divisions would draw the *same* creds:

| # | Element | Location |
|---|---|---|
| 1 | `Secrets` scalar fields: `polymarket_private_key`, `polymarket_funder_address`, `polygon_rpc_url` | `utils/secrets.py:116-118` |
| 2 | Redact key-name list | `utils/secrets.py:39-41` |
| 3 | KV-pull `expected_env_vars` | `utils/secrets.py:220-222` |
| 4 | `load_secrets()` construction | `utils/secrets.py:296-298` |
| 5 | `register_redact_literal()` of priv/funder/rpc | `utils/secrets.py:317-321` |
| 6 | Broker factory `family=="polymarket"` branch (constructs from shared scalars) | `main.py:1784-1797` |
| 7 | Registration loop: per-division **instance** keyed by `d.slug`, skips `enabled:false` (not `standby`) | `main.py:448-454` |
| 8 | `assert_live_ready()` — **no** polymarket branch | `utils/secrets.py:335-360` (this is **item 7**, not item 6) |

**Consumer check (done 2026-05-29):** the *only* reader of the scalar `polymarket_private_key`/`polymarket_funder_address` is the factory (`main.py:1794-1795`). `polygon_rpc_url` is read there too and is legitimately **shared** (one Alchemy endpoint per all divisions). The resolver paths (`main.py:1283`, `:3105`) use the broker *instance*, not the secrets. ⇒ we can route per-division creds through a new map and drop the scalars without touching other code.

**Divisions today** (`config/divisions.yaml`):
- `polymarket_arbitrage` — `broker: polymarket`, `standby:true`, `enabled:true` (real broker, $500 read).
- `polymarket_copy_trading` — `broker: paper`, `standby:true`, `enabled:true` (PaperBroker placeholder).

**Key design fact:** the per-division thing is the **wallet** (private_key + funder_address). The **collateral token is NOT per-division** — it's USDC.e for every CLOB division (`brokers/polymarket.py` `_USDC_CONTRACT`, fixed in `631ddc4`). The **RPC URL is shared**. So only two values are per-division.

---

## Target design

A division → wallet map, resolved at load time, consumed by the factory keyed on `division.slug`.

**Naming convention:** per-division env/KV suffix. PCT gets new names; arb keeps its legacy names (zero KV churn — see migration options):

| Division slug | private-key env | funder env |
|---|---|---|
| `polymarket_arbitrage` | `POLYMARKET_PRIVATE_KEY` (legacy, kept) | `POLYMARKET_FUNDER_ADDRESS` (legacy, kept) |
| `polymarket_copy_trading` | `POLYMARKET_COPY_PRIVATE_KEY` (new) | `POLYMARKET_COPY_FUNDER_ADDRESS` (new) |

RPC: `POLYGON_RPC_URL` stays a single shared scalar for all.

**Why an explicit map (not slug-derived env names):** deriving env names from the slug (`f"POLYMARKET_{slug.upper()}…"`) yields unwieldy KV names (`POLYMARKET_COPY_TRADING_PRIVATE_KEY`) and fails *silently* on a slug typo (stub mode, no error). An explicit `{slug: (priv_env, funder_env)}` map is greppable, fails loudly-enough (unmapped slug → logged stub), and a new division is one line. Recommended.

---

## Exact per-file diffs (to build later — NOT applied this session)

### `utils/secrets.py`

1. **New frozen dataclass** (near `Secrets`):
   ```python
   @dataclass(frozen=True)
   class PolymarketWallet:
       private_key: str | None
       funder_address: str | None
   ```
2. **New explicit map** (module level):
   ```python
   _POLYMARKET_WALLET_ENV: dict[str, tuple[str, str]] = {
       "polymarket_arbitrage":   ("POLYMARKET_PRIVATE_KEY",      "POLYMARKET_FUNDER_ADDRESS"),
       "polymarket_copy_trading":("POLYMARKET_COPY_PRIVATE_KEY", "POLYMARKET_COPY_FUNDER_ADDRESS"),
   }
   ```
3. **`Secrets`**: replace the two scalar wallet fields with one dict field; keep `polygon_rpc_url` scalar.
   - remove `polymarket_private_key`, `polymarket_funder_address` (lines 116-117)
   - add `polymarket_wallets: dict[str, PolymarketWallet]`
   - keep `polygon_rpc_url` (118)
4. **`_SECRET_KEY_NAMES`** (39-41): add `POLYMARKET_COPY_PRIVATE_KEY`, `POLYMARKET_COPY_FUNDER_ADDRESS`. (Keep the legacy two.)
5. **`expected_env_vars`** (220-222): add the two `POLYMARKET_COPY_*` names.
6. **`load_secrets()`** (296-298): replace the two scalar reads with a dict build:
   ```python
   polymarket_wallets={
       slug: PolymarketWallet(_env(pk), _env(fa))
       for slug, (pk, fa) in _POLYMARKET_WALLET_ENV.items()
   },
   ```
7. **`register_redact_literal()`** (317-321): iterate every wallet's `private_key` + `funder_address` (replaces the two scalar registrations). Keep `polygon_rpc_url` registration.

### `main.py`

8. **Factory `family=="polymarket"` branch** (1784-1797): resolve wallet by slug.
   ```python
   if family == "polymarket":
       from trading_corp.brokers.polymarket import PolymarketBroker
       wallet = secrets.polymarket_wallets.get(division.slug)
       if wallet is None:
           log.info("Polymarket division %s has no mapped wallet — stub", division.slug)
       return PolymarketBroker(
           private_key=wallet.private_key if wallet else None,
           funder_address=wallet.funder_address if wallet else None,
           polygon_rpc_url=secrets.polygon_rpc_url,
       )
   ```
   `PolymarketBroker` already stubs cleanly when funder/rpc are None (`polymarket.py:243`), so an unmapped or unfunded division degrades safely.

### `config/divisions.yaml` — **NOT changed in item 6**

PCT stays `broker: paper` until its own go-live (item 9 + funding). Item 6 lands the *plumbing* so PCT *can* be flipped later; flipping it now (with no wallet funded) would just stub. Keeping the flip out of item 6 keeps this change inert on prod.

### `assert_live_ready` (item 7, noted not done here)

The per-division wallet preflight (`"polymarket"` branch checking the mapped key+funder before a LIVE start) is **item 7**, adjacent but out of item 6's scope. Flagged so it isn't forgotten when PCT approaches go-live.

---

## arb migration path — **operator decides**

- **(i) Keep arb's wallet (recommended for item 6):** arb.slug maps to the legacy `POLYMARKET_PRIVATE_KEY`/`FUNDER` names. **Zero KV churn, no new wallet, no on-chain action**; arb's address + $500 native USDC untouched. arb stays read-only. This is pure plumbing/routing.
- **(ii) Deprecate-and-replace:** generate a fresh arb EOA, new `POLYMARKET_ARB_*` KV secrets, migrate funds. **Only worth doing if arb itself goes live** (needs USDC.e + approvals + on-chain moves) — a separate decision, deliberately **not** in PCT's path.

Recommend **(i)** for item 6. (ii) is arb's own future go-live concern.

---

## Test strategy (proves isolation)

New `tests/test_polymarket_per_division_wallets.py` (run via `scripts\run_capped.ps1 python -m pytest …` — imports `trading_corp`):

- Monkeypatch env: arb `(priv_a, funder_a)`, PCT `(priv_b, funder_b)`, shared RPC. `load_secrets()` → assert `polymarket_wallets` has both, correct values.
- Build brokers via `_build_broker_for_division` for synthetic divisions (`broker=polymarket`, slugs `polymarket_arbitrage` / `polymarket_copy_trading`).
- **Distinct instances:** `broker_arb is not broker_pct`.
- **Distinct creds:** `broker_arb._funder == funder_a`, `broker_pct._funder == funder_b`, `funder_a != funder_b`; same for `_private_key`.
- **Shared RPC:** both `._rpc_url == rpc`.
- **Unmapped slug → stub:** synthetic `broker=polymarket` division with an unknown slug → `broker._stub is True`.
- **Redaction:** after `load_secrets()`, both private keys + both funders are in `_REDACT_LITERALS`.

---

## #7 — positions response-shape verification (scope into this runbook; execute post-funding, pre-first-trade)

Nominally Group E item 12, but the *verification step* belongs in item 6's runbook because the right moment is **after the PCT EOA is funded, before its first real trade** — decoupling shape-verification from live-trading pressure (the audit's escalation gate).

Step: once PCT EOA holds a position (a small one **manually created via the Polymarket UI** if no natural one exists — does **not** need to be a system trade), run `PolymarketBroker._fetch_positions()` against the funded wallet and diff the actual `data-api…/positions` response keys against the `.get()` field guesses in `brokers/polymarket.py:722-760` (`size`/`qty`, `avgPrice`, `outcome`, `slug`, `conditionId`, `currentValue`, `realizedPnl`, …). Correct the mapping if it diverges. **This is the one suspect item I'd be least casual about** — if the shape is wrong, reconciliation/PnL is broken exactly when it must be honest.

---

## #8 — last-trade-price endpoint (consolidation note for item 8, not now)

`brokers/polymarket.py:415-477` (`quote()`) uses a defensive raw-httpx `/last-trade-price` + `clobTokenIds`/`outcomes` parse that is **unverified** against the live shape (the spike used `get_sampling_markets()` — a different, known-working path). When the live adapter (**item 8**) is built, **consolidate the price/quote path on the spike's proven py_clob_client client methods** rather than carrying the defensive-but-unverified one forward. Flagged in item 8's scope.

---

## Deploy sequencing (decided)

- **Hold the `631ddc4` USDC.e read-path flip.** Do **not** deploy it standalone. Bundle it with item 6's deploy, when the new PCT EOA's USDC.e balance becomes the meaningful read.
- **Consequence to name:** once `631ddc4` ships (the broker reads USDC.e for *all* PolymarketBroker instances), **arb's tile will read $0** — arb holds native USDC, which is correctly *not* tradeable CLOB collateral. This is an **intentional, honest** state (no tradeable collateral), not broker-down. Until then, arb keeps reading its $500 native on current prod code, which is fine.
- Optional (not required): surface arb's native-USDC holding in `AccountSnapshot.extra` so the $500 stays visible alongside the $0 tradeable reading. Defer unless the dashboard ambiguity bites.

---

## Out of scope / open questions for ratification

- **Out of scope of item 6:** the PCT `broker: paper→polymarket` flip + funding (go-live, item 9); arb wallet replacement (ii) + arb funding; `assert_live_ready` polymarket branch (item 7); the live adapter (item 8); approvals (item 4); MATIC monitoring (item 5).
- **For ratification:** (a) confirm the explicit-map design + naming (legacy names for arb, `POLYMARKET_COPY_*` for PCT); (b) confirm arb migration option (i) keep-wallet; (c) confirm dropping the scalar `Secrets` fields (vs keeping as deprecated aliases); (d) build sequencing — item 6 alone next, or item 6 + item 7 together (both touch `secrets.py`).
- **Group C carry-forward (from the spike):** lockfile must be prod-compiled for py3.12/Linux; pin the `eth-abi==6.0.0b1` beta transitive — both when the adapter venv is built (item 8).

---

## Status log

- **2026-05-29** — Plan authored (plan-only). Funding target = Option A (recorded). Awaiting ratification + build-sequencing decision. No build, no deploy, no on-chain action.
