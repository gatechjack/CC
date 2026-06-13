# E1·2 — ProposedOrder → OrderArgs mapping + sign-only create_order

**Date:** 2026-06-13
**Branch:** `polymarket-e1-2-mapping-sign-2026-06-13` (base `main` `1327764`)
**Mode:** BUILD + TEST on a branch, **UNMERGED**. No funds, **no `post_order`**, no deploy, no
prod write, ephemeral/mocked keys only. Disclosure per `82fda13`.
**Increment:** E1·2 of the E1 build plan. The mapping + sign-only slice. (Place/poll = E1·3,
cancel = E1·4, quote = E1·5, broker assembly = E1·6 — NOT built here.)

---

## Phase A — token_id resolution (the one unknown): **case (ii)**

`ProposedOrder.extra` (built by the copy strategy, `polymarket_copy_trader.py:429-451`) carries
`condition_id` + `outcome_index` + `outcome` but **NOT** the token_id; `symbol` is
`f"{condition_id}:{outcome}"` (`:418`); `limit_price` is the 0–1 probability, `qty` is contracts,
`side` is `"buy"/"sell"`. So the CLOB `OrderArgs.token_id` must be **resolved**, not read directly.

- The whale's `ActivityRow.asset` **IS** the ERC-1155 token id (`polymarket_data_api_client.py:149`)
  — authoritative — but the strategy does not propagate it into `extra` today.
- The read adapter already resolves token_id from a market lookup: `brokers/polymarket.py:~447-473`
  (gamma `/markets` → parse `clobTokenIds` JSON list parallel to `outcomes`, match by outcome label).

**Operator decision: Option 1 — direct-then-lookup.** The mapping reads `extra["token_id"]`/
`["asset"]` if present (authoritative, no network), else resolves via a gamma `clobTokenIds` lookup
from `condition_id` (+ `outcome_index`/`outcome`).

## Phase B — the mapping + sign (`trading_corp/brokers/polymarket_live.py`, new)

Pure functions (no SDK):
- `resolve_token_id_from_market(market, outcome_index, outcome)` — picks the token by **outcome
  LABEL** (order-independent, mirrors the read adapter). `outcome_index` is a **cross-check**: if
  the label is found at a different position than `outcome_index` claims, it **RAISES**
  (`TokenIdResolutionError`) — a wrong token_id is the wrong side of a real market, so it never
  guesses. Also raises on not-found / ambiguous-label / length-mismatch / index-out-of-range.
  Parses `clobTokenIds`/`outcomes` whether JSON-encoded strings or lists.
- `resolve_token_id(extra, market_fetcher)` — direct (`token_id`/`asset`) then gamma lookup.
- `map_proposed_to_clob(order, market_fetcher)` → `{token_id, price, size, side}`. **Units:**
  `price = limit_price` (0–1 probability, validated in (0,1)); `size = qty` (contracts);
  `side ∈ {buy,sell}`. Matches the spike (`price=0.50, size=5` → `maker=2500000`).

SDK-touching (lazy `py_clob_client` import, so the module + pure functions import without the SDK):
- `build_clob_order_args(mapped)` → `OrderArgs(token_id, price, size, side=BUY/SELL)` (kwargs).
- `create_signed_order(client, order, market_fetcher)` → `client.create_order(args)`. **SIGN-ONLY —
  never calls `post_order`.**

## Phase C — tests (fundless, no live placement)

- **Box gate (py3.14, no SDK):** `tests/test_polymarket_live_broker.py` → **23 passed, 2 skipped**.
  The 23 cover token_id resolution + the **operator-required correctness**: the gamma lookup picks
  the **correct outcome's** token via label (incl. an `outcomes=["No","Yes"]` layout where a naive
  index would mis-pick), a **label-without-index** case, and an **ordering-mismatch → raises** case;
  plus not-found / ambiguous / length-mismatch, and the price/size/side mapping (rejects
  non-probability price, non-positive size, bad side). The 2 SDK tests `importorskip` cleanly
  (py_clob_client absent on the box) — **no new collection error**.
- **py3.12 venv (pinned SDK from E1·1):** SDK path validated — `build_clob_order_args` maps
  BUY/SELL; `create_signed_order` calls `create_order` with the mapped `OrderArgs` and **never
  `post_order`**; and a **real-signing one-shot** (ephemeral key, real token via the direct path)
  produced a **valid EIP-712 signature** (`maker=2500000 taker=5000000 sigType=0`). `post_order`
  never called.
- **Full same-env gate (pristine-vs-edited in the worktree, `stash -u`):** `EDITED_FAILS=31 ==
  PRISTINE_FAILS=31`, **diff empty → zero new regressions** (the change is purely additive: one new
  module + one new test file; no existing file touched). The 31 are the worktree's pre-existing
  fails (incl. the 2 `paper_run_tooling` `.env`-presence artifacts, present in both runs).

**Validation-env note:** the SDK pytest tests run wherever `py_clob_client` is installed (the
prod-target py3.12 + lockfile env / CI); on the box they skip. Their behaviors are proven here via
the py3.12 venv. (Carried forward from E1·1: the Linux-OS signing confirmation is the operator-gated
deploy smoke; EIP-712 is OS-independent crypto.)

## E2 follow-on (recorded; do NOT build in E1·2)

The copy strategy should **propagate `activity.asset` into `extra["token_id"]`** (the token id is
authoritative at source) so the **direct** path is the production norm and the gamma lookup becomes
a rarely-fired fallback (avoids a per-order network re-lookup + the ordering-mismatch risk class
entirely). Filed on the BACKLOG E-series (E2 bullet, scoping branch) and noted in
[[polymarket-e1-live-broker-design]]. **E1·2's mapping already supports both paths**, so no rework
when E2 lands this.

## Phase D — status

- `trading_corp/brokers/polymarket_live.py` (new) + `tests/test_polymarket_live_broker.py` (new) +
  this report, committed on the branch. Throwaway py3.12 venv + the SDK-check script are scratch
  (not committed).
- **UNMERGED.** No deploy/merge. Hard stops honored: ephemeral/mock keys only; `post_order` never
  called; no funds/placement; stayed in-slice (no place/poll/cancel/quote/assembly); no prod write.

## Next — E1·3

The place/poll path: `create_order → post_order(signed, OrderType.GTC) → poll status → FillEvent`
(mirroring `tastytrade.py:398-460`), behind the live/dry gate. Still fundless (mocked client) until
the operator-gated $1 shakedown.

---

*E1·2 artifact — committed unmerged on `polymarket-e1-2-mapping-sign-2026-06-13`. Builds on E1·1
(`1206dda`) + the E1 design.*
