# BitUnix Live Engine — Reuse Audit

**Date:** 2026-05-29 · **Type:** read-only audit of external reference implementations (no code copied yet, no deps installed) · **Status:** planning doc for operator review (committed local, not pushed).

**Decision context:** operator is building the live execution engine in parallel with strategy data accrual; realistic timeline **3–6 weeks** of focused work. Goal of this audit: cut ~30–40% off the *broker-write* piece by learning from / adopting existing BitUnix integrations before writing code. Companion to `runbooks/2026-05-29_bitunix_live_readiness_audit.md` (the Stage-0→3 gap analysis whose Stage-1 checklist this maps to).

**Repos read** (shallow-cloned to a scratch dir, read-only, deleted after): `BitunixOfficial/open-api` (official SDK demo), `Lumiwealth/lumibot` (working BitUnix futures broker), `0xCherryBlueZu/bitunix` (unofficial client, skimmed).

> **Note on lumibot's `CLAUDE.md`:** reading lumibot files surfaced *their* repo's agent instructions (branch rules, ThetaData, MCP servers). Those govern lumibot development, not this work — treated as data, ignored.

---

## License verdict (flagged, not litigated)

| Repo | License | Reuse posture |
|---|---|---|
| **Lumiwealth/lumibot** | **MIT** (per `setup.py` `license="MIT"` + PyPI classifier `License :: OSI Approved :: MIT License`; operator-confirmed). The repo `LICENSE` file contains GPL-v3 text — a repo housekeeping discrepancy, **not** the governing license. | **Adopt with attribution.** Copyleft would only matter on *distribution* anyway; our bot is internal/non-redistributed. **Lawyer-threshold:** confirm MIT with Lumiwealth only if you ever redistribute the bot. |
| **BitunixOfficial/open-api** | **No LICENSE file** → all-rights-reserved by default. | **Reimplement from the interface.** Endpoints, payload shapes, error codes, and the auth algorithm are *facts/spec* (not copyrightable) — use freely. Do **not** copy the demo `.py` verbatim. |
| **0xCherryBlueZu/bitunix** | **MIT** (`Copyright 2024 0xCherryBlue`). | **Reference only.** Permissive, but it targets a **different/older API surface** (`place_order(side:int, order_type:int, volume, price)`, `get_account_balance` — spot/legacy, not the `/api/v1/futures/*` perp API). Low reuse value; skim for quirks. |

---

## Per-repo extraction notes

### A. BitunixOfficial/open-api (official SDK demo, Python)

Files: `Demo/Python/{open_api_http_sign.py, open_api_http_future_private.py, open_api_ws_future_private.py, open_api_ws_sign.py, error_codes.py}`.

**Reusable as interface-knowledge (reimplement; it's the spec):**
- **Auth algorithm** (`open_api_http_sign.py:24-48`): `digest = SHA256(nonce + timestamp + api_key + sortedQuery + body)`; `sign = SHA256(digest + secret_key)`; headers `api-key / sign / nonce / timestamp`. `sort_params` = `''.join(f"{k}{v}" for k,v in sorted(...))`. **This is identical to our existing `brokers/bitunix.py:71-99` and already proven by live reads** — auth is done + validated against the official source.
- **Order payload** (`open_api_http_future_private.py:79-138`): `POST /api/v1/futures/trade/place_order` body `{symbol, side BUY|SELL, orderType LIMIT|MARKET, qty(str), tradeSide OPEN|CLOSE, effect(TIF) GTC, reduceOnly}` + optional `price, positionId, clientId, tpPrice, tpStopType MARK|LAST, tpOrderType, tpOrderPrice`. Envelope `{code, msg, data}`, **code 0 = success**.
- **Batch cancel** (`:140-162`): `POST /api/v1/futures/trade/cancel_orders {symbol, orderList:[{orderId}|{clientId}]}`.
- **Private WS** (`open_api_ws_future_private.py`): channels **`balance`, `position`, `order`, `tpsl`**; auth `{"op":"login","args":[<sig>]}`; app-level heartbeat `{"op":"ping","ping":<epoch_s>}` every 3s (protocol-level ping disabled); reconnect loop. **Position-channel schema (`:131-150`) carries per-position `funding` AND `fee`** plus `realizedPNL/unrealizedPNL/qty/entryValue` — the cost-accrual data source. **Order channel** `{orderId, symbol, type, status, price, qty}`; **tpsl channel** `{slPrice, slQty, slStopType, status, ...}`.
- **Error codes** (`error_codes.py`) — the rejected-order taxonomy (see "Bitunix-specific learnings" below). Reimplement as a lookup; the codes are facts.

**Skip / rewrite:** the demo's WS reconnect caps at **5 attempts then gives up** (`open_api_ws_future_private.py:29,207`) — unacceptable for a long-running bot; rewrite to unbounded reconnect + backoff + poll fallback. No retry/backoff on the REST side at all.

### B. Lumiwealth/lumibot — `lumibot/tools/bitunix_helpers.py` + `lumibot/brokers/bitunix.py` (the high-value reads)

**`BitUnixClient` (`tools/bitunix_helpers.py`, 800 lines) — the single most reusable artifact. ADOPT-WITH-ATTRIBUTION** (framework-agnostic; pure `requests`). It's a **complete futures REST client** covering every endpoint Phase 4 needs:
- `get_account`, `get_positions`/`get_pending_positions`, `get_funding_rate`, `get_kline`, `get_tickers`, `get_mark_price`, `get_depth`.
- `place_order` (`:158-195`) — **attaches `tpPrice`/`slPrice` in the same order request** (TP/SL without a second call); `clientId` idempotency.
- `cancel_order` / `cancel_orders` / **`cancel_all_orders`** (`:698-722`), **`flash_close_position(position_id, side)`** (`:758-773`), **`close_all_position(symbol)`** (`:745-756`) — the **kill-switch / flatten primitives exist natively** (one call each).
- `modify_order` (`:724-743`) — for the v2 SL ratchet. `change_leverage` / `change_margin_mode` / `change_position_mode` (`:259-319`). `adjust_position_margin`.
- `get_history_trades` (`:562-597`, **fills**), `get_history_positions` (`:472-504`, **carries funding+fee per closed position**), `get_order_detail`, `get_pending_orders`, `get_position_tiers`, `batch_order`.
- **THE Bitunix-specific gotcha, handled correctly (`:101-108`):** the POST body is serialized **once** to compact JSON (`json.dumps(body, separators=(',',':'))`) and sent as raw **`data=body_str`** — NOT `json=` (which would re-serialize with spaces → signature mismatch → error 10007). *This is where most BitUnix integrations fail.* Our read-only broker hasn't hit this (GETs only); our `place_order` MUST replicate it.

**`Bitunix(Broker)` (`brokers/bitunix.py`, 775 lines) — ADOPT-AS-PATTERN** (lumibot-framework-coupled; rewrite against our `Broker` ABC + `ProposedOrder`, copying the API-call bodies):
- **`do_polling()` (`:544-605`) — the reconciliation engine.** Polls broker positions (`sync_positions`) + open orders every 5s, diffs against tracked state, dispatches `NEW/PARTIALLY_FILLED/FILLED/CANCELED/ERROR`. Orphan handling: tracked orders absent from the broker list → dispatch CANCELED (`:596-605`). **`_first_iteration` branch = restart-resume from broker truth.**
- **Partial-fill state machine (`_parse_broker_order` `:458-540`):** reads `qty` (total) vs `tradeQty` (executed) + `avgPrice`; status map includes `PARTIALLY_FILLED` (`:436-456`); `do_polling` fires `PARTIALLY_FILLED_ORDER` with `filled_quantity`. This is the partial-fill tracking the readiness audit flagged as absent.
- **`_submit_order` (`:235-331`):** sets leverage first (cached in `current_leverage` to dodge error 20006), builds the payload, `code==0` check, extracts `orderId`, dispatches NEW. `clientId = lmbot_{ms}_{hash}`.
- **`sell_all` / `close_position` (`:336-375, 729-749`):** flatten via reduce-only market close + `flash_close_position` per position — the kill-switch flow.
- **Design choice worth adopting:** lumibot's broker uses **`PollingStream` (REST poll), not websocket**, for trade events — simpler, no disconnect-gap problem. Validates a **poll-first** Stage-1 (defer the WS fill stream to Stage 3).

**Skip / rewrite:** lumibot's broker is coupled to lumibot's `Order`/`Position`/`Broker`/`stream` entities — don't adopt the class, adopt the *flow*. Sets HEDGE position mode globally at init (`:86`) — decide one-way vs hedge for our single-symbol case. **No REST retry/backoff** in `_request` (`:72-122`, 10s timeout + `raise_for_status`) — same gap as the official SDK; build our own.

### C. 0xCherryBlueZu/bitunix (unofficial, skimmed)

`bitunix/client.py` (340 lines, MIT). Targets a **different/older API** (`place_order(side:int, order_type:int, volume, price, symbol)`, `get_latest_price`, `get_account_balance`, base64 in signing) — **not** the `/api/v1/futures/*` perp surface lumibot + the official SDK use. **No reuse for the futures engine**; kept only as a quirk reference. No issues/quirks worth extracting over what the official SDK + lumibot already give.

---

## Bitunix-specific learnings (quirks / gotchas / limits)

1. **Signing: sign-what-you-send.** Serialize the POST body once (compact, no spaces) and send that exact string (`data=`), not `json=`. Sorted query string = `key+value` concatenated, sorted by key, no separators. (lumibot `bitunix_helpers.py:42,101-108`.) **#1 integration failure point.**
2. **`clientId` is the idempotency key.** A retry with the same `clientId` is rejected with **30042 CLIENT_ID_DUPLICATE** — so a deterministic `clientId` makes order-placement retries *safe* (a dup means "already placed," not "place twice"). Foundational for the retry layer.
3. **Envelope `{code, msg, data}`, code 0 = success.** Non-zero = business error (look up in the error table) even on HTTP 200.
4. **TP/SL attach in the order** (`tpPrice`/`slPrice` + `tpStopType MARK|LAST`), no separate call needed; or `modify_order` later for the ratchet.
5. **Kill-switch primitives are native:** `cancel_all_orders` (halt resting), `close_all_position(symbol)` / `flash_close_position(positionId, side)` (instant market flatten). The kill switch is mostly *wiring these*, not building a flatten loop.
6. **Position/margin/leverage modes are stateful & order-sensitive:** `change_position_mode` HEDGE|one-way (ALL symbols), `change_margin_mode` ISOLATED|CROSSED (per symbol), `change_leverage` (per symbol) — **can't change leverage/mode with open orders (error 20006)**. Set them before opening; cache to avoid redundant calls.
7. **Fills + cost data:** `get_history_trades` (fills per order/position); `get_history_positions` and the WS `position` channel carry **`funding` and `fee` per position** → that's where live cost-accrual reads from (lumibot fetches but doesn't accrue — we build the accrual).
8. **Rejection taxonomy (`error_codes.py`)** worth pre-handling: `10004` IP-not-in-whitelist (the API key can be IP-locked — **whitelist the prod VM IP**), `10005/10006` rate-limit (→ backoff), `10007` sign error (→ body-serialization or clock drift), `20003` insufficient balance, `20006` can't-change-leverage-with-open-orders, `30001` order would immediately liquidate, `30016/30017` qty below minimum, `30018/30019` reduce-only rules, `30024/30025` SL beyond liq price, `30038` TP/SL amount must be ≤ position size (relevant to multi-leg partial TPs), `30042` clientId duplicate.
9. **No retry/backoff in either reference** (lumibot or official) — both fail-fast on timeout. Our own polymarket-client retry pattern (`trading_corp/data/polymarket_data_api_client.py`, the `_CLOUDFLARE_RETRY_DELAYS_SEC` schedule) and the db-lock retry we just shipped are the better templates — build, don't borrow.
10. **WS is optional for Stage 1/2:** lumibot proves a **poll-based** broker works fine (5s `PollingStream`). The WS fill stream is a Stage-3 latency optimization, not a Stage-1 requirement — and the official WS reconnect demo caps at 5 attempts (must rewrite).

---

## Stage-1 checklist → reuse-source mapping

(Stage-1 items per the readiness audit. Reuse type ∈ {**adopt** = copy lumibot code w/ attribution + rewire; **interface** = reimplement from the official spec; **pattern** = adopt the design, rewrite for our types; **build** = no external reuse.})

| # | Stage-1 item | Effort | Reuse source | Type | Accelerates by |
|---|---|---|---|---|---|
| 1 | **place_order + real fill observation** | LARGE | lumibot `BitUnixClient.place_order` + `_submit_order` + `_parse_broker_order` (qty/tradeQty/avgPrice); official payload/auth | **adopt + interface** | **~2 sessions** — exact payload, the sign-what-you-send gotcha pre-solved, fill-parse shape known, clientId idempotency pattern |
| 2 | **cancel_order + kill switch** (halt+cancel-resting+flatten) | MEDIUM | lumibot `cancel_all_orders` / `flash_close_position` / `close_all_position` + `sell_all` flow | **adopt** | **~1 session** — native flatten/cancel-all primitives; mostly wiring |
| 3 | **Restart-with-open-position resume from broker truth** | MEDIUM | lumibot `do_polling` `_first_iteration` sync | **pattern** | **~0.5–1 session** — proven "first poll reconciles broker → tracked" approach |
| 4 | **Post-trade reconciliation** (fills/position/balance vs recorded) | MEDIUM | lumibot `do_polling` diff-engine + `get_history_trades` / `get_pending_positions` / `get_account` | **pattern** | **~0.5–1 session** — the poll-diff-dispatch loop is the template |
| 5 | **Real fee/funding capture** | MEDIUM | `get_history_positions` + WS `position` channel (`funding`,`fee`) — data location | **interface** | **~0.5 session** — knowing the cost data is on the closed-position object; lumibot doesn't accrue it, so we still build the booking |
| 6 | **REST retry/backoff + stale-snapshot + stuck-order timeout** | MEDIUM | none external (both refs lack retry) → our polymarket/db-lock retry patterns | **build** | none external |
| 7 | **Operational alerts** (connection/halt/divergence) | S–M | none external → our Telegram channel + divergence-monitor pattern | **build** | none external |
| 8 | **Low-equity alert + fund the account** | SMALL | none external | **build** | none external |
| 9 | **HITL for first N live trades** | SMALL | none external → our existing HITL/approval surface | **build** | none external |
| 10 | **Security C-1 rotation + H-11 verify** | SMALL | none external (operator-led) | **build** | none external |
| 11 | **Panic-halt + cred-compromise runbooks** | SMALL | lumibot flatten primitives inform the panic procedure | **pattern** | minor (the "how to flatten" is `flash_close_position`) |
| 12 | **md5-diff prod surface vs git** | SMALL | none external (our drift discipline) | **build** | none external |
| 13 | **Confirm risk caps on real equity + wire flatten action** | SMALL | lumibot `flash_close`/`close_all` for the flatten that `flatten_account` must trigger | **pattern** | minor |

**Where reuse concentrates:** items **1–5 + 11/13** — i.e. the broker-write core, reconciliation, kill switch, and cost-data location. Items **6–10, 12** are our-system-specific (resilience, observability, security, HITL, drift, capital) with **no external reuse** — they're built from our own existing patterns.

---

## Revised timeline estimate

**Original (no reuse), Stage 1:** the broker-write item (#1) is the long pole — building + debugging the full place/cancel/fill/position REST integration against a live exchange, including discovering the signing gotcha the hard way, is realistically **~4–6 focused sessions on its own**; the surrounding Stage-1 must-haves (#2–13) add **~6–9 sessions**. Call it the bulk of a **3–6 week** effort, consistent with the operator's frame.

**With reuse (realistic):**
- **Broker-write (#1+#2): ~30–40% off.** lumibot gives a complete, MIT, *working* futures client + a proven place/cancel/fill/flatten architecture; the official SDK confirms the payload/auth; the #1-failure signing gotcha is pre-solved. We adopt the client (rewire to our `Broker` ABC + `ProposedOrder` + audit model) instead of building+debugging from API docs. Estimate the broker-write long pole drops from ~4–6 sessions to **~3–4**.
- **Reconciliation + restart-resume + cost-data (#3–5): ~20–30% off** — patterns to copy, but rewritten for our state model + audit rows + reconciler, so less leverage than the client adopt.
- **Everything else (#6–13): ~0% external reuse** — built from our own patterns (which is fine; those patterns exist and are proven).
- **Net Stage 1:** the **3–6 week** envelope holds; reuse compresses the *broker-write long pole* (the riskiest, most-likely-to-blow-the-estimate piece) by ~30–40% and de-risks it substantially (working reference to diff against). It does **not** compress the our-system-specific half. Don't promise more than "the scariest third gets meaningfully smaller and safer."

---

## Honest "not reusable — build from scratch"

These have **no generic implementation** and must be built against our architecture:
- **Reconciliation against *our* recorded truth.** lumibot reconciles broker↔*its own* `Order`/`Position` objects; our authoritative record is `paper_trade_record` + `audit_event` + the audit-reality reconciler. The poll-diff *pattern* is reusable; the comparison *against our schema* + mismatch→halt policy is bespoke.
- **Kill-switch *verification*.** The primitives (`cancel_all_orders`/`flash_close_position`) are adoptable, but proving the switch actually halts-new + cancels-resting + flattens *in our system*, wired to `RiskAgent.flatten_account`/`halt_strategy`, and testable on demand — is ours to build and test.
- **Our-system observability.** Connection/halt/divergence Telegram alerts, dashboard tiles (equity-vs-tracked, positions-vs-broker, daily PnL), the fills/PnL divergence monitors — all hook our comms + web layer; no external analog.
- **Cost *accrual + reconciliation*.** The data location is known (closed-position `funding`/`fee`); booking it to our `paper_trade_record`, a `funding_accrual` audit kind, and reconciling vs broker statements is ours.
- **Resilience policy.** Retry/backoff thresholds, stale-snapshot detection, stuck-order timeout→cancel, clock-skew guard — neither reference implements these; build from our polymarket/db-lock retry patterns.
- **Runbooks, security (C-1 rotation, H-11 verify), HITL gate, prod md5-diff, capital funding** — operational/our-system, no external code.

**Bottom line:** the lumibot `BitUnixClient` is a genuine accelerator — adopt it (MIT, with attribution) as the REST layer and lift the broker *flow*, which removes the riskiest unknowns from the broker-write long pole (exact endpoints, the signing gotcha, partial-fill parsing, native flatten). But over half of Stage 1 is reconciliation-against-our-truth, observability, resilience, security, and runbooks — none of which have a generic implementation. Net: ~30–40% off the broker-write piece, less elsewhere, 3–6 weeks stands.

---

*Sources: read-only clones of `BitunixOfficial/open-api`, `Lumiwealth/lumibot`, `0xCherryBlueZu/bitunix` (2026-05-29, deleted post-audit); file:line cited against those repos. Companion: `runbooks/2026-05-29_bitunix_live_readiness_audit.md`. No code copied, no deps installed. Attribution required if lumibot code is adopted (MIT).*
