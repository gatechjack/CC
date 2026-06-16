# Exchange-resting bracket-exit redesign — scope (REVISED per operator)

Read-only investigation (82fda13). **ANALYSIS + PLAN ONLY — no code, no deploy.** Branch
`bitunix-bracket-exit-redesign-scope-2026-06-16`. Supersedes the `#5-A` "build virtual scale-out"
item in `reports/2026-06-16_orphan_managed_exit_scope.md`. File:line refs vs deployed code
(`cc-deploy-prep-wt`). Three subagents traced the broker primitives, the place-at-entry path, and the
monitor/cadence/guards; load-bearing claims re-verified.

## The redesign in one line
At entry, place the **SL (market-stop, as today) + TP1/TP2/TP3 (reduce-only LIMIT, split by scale-out
qty) as resting exchange orders**; let **BitUnix manage OCO natively**; the bot's ONLY ongoing exit
job is **moving/resizing the SL when a TP fills**, detected by a cheap bar/position poll. This
**collapses #5-A** (no more bot-side virtual TP firing) and **de-risks #3/#4** (an orphaned position
keeps its on-exchange bracket → still protected *and* still takes profit).

---

## A. What the code/venue CONFIRMS (foundation is there)
- **Reduce-only LIMIT orders are constructable today.** `_build_order_body` (`bitunix.py:1231-1293`)
  maps `order_type="limit"` → `body["price"]`, and `extra["reduce_only"]=True` → `body["reduceOnly"]=True`,
  independently. A reduce-only LIMIT at a TP price is mechanically supported — it's just **never
  exercised** today (all current exits are reduce-only MARKET). The 3 TP legs = 3 such orders.
- **The SL stays a market-stop, attached atomically at entry** (`_build_order_body:1279-1289`:
  `slPrice` + `slStopType=MARK_PRICE` + `slOrderType=MARKET`). Confirmed live (first fill 06-14). This
  is a **safety property to preserve**: the catastrophic stop is born in the same call as the entry —
  it cannot be left off by a later failure. (Hard-stop honored: SL stays guaranteed-fill MARKET.)
- **TP scale-out levels + fractions are known AT ENTRY** and already stamped on the order:
  `TradePlan` (`trade_plan.py:137-159`: tp1/tp2/tp3 + tp1/2/3_qty_fraction 0.25/0.50/0.25 + stop_loss)
  and `order.extra["tp_plan"]` (`observer.py:2423-2447`, carries per-leg `{price, fraction, stop_action}`).
- **Cancel exists:** `broker.cancel_order(venue_order_id)` → `/futures/trade/cancel_orders`
  (`bitunix.py:1690-1713`); `cancel_all_orders(symbol)` (1715). Order-status: `get_order_detail`
  (1432), `get_history_trades` (1448), `get_recent_close_fills` (1635).
- **TIF/effect** accepts `GTC|IOC|FOK|POST_ONLY` (B2-confirmed); a reduce-only LIMIT can be GTC
  (rests) or POST_ONLY (guaranteed-maker) — `_build_order_body:1290` sends `effect` for any LIMIT.
- **Coexistence of multiple reduce-only orders on one position: operator-CONFIRMED** (they place
  multi-order brackets manually routinely). Error `30038 TPSL_EXCEEDS_POSITION` (`bitunix.py:199`)
  shows the venue enforces aggregate TP/SL size ≤ position size (split fractions must sum to ≤ 1.0).

## B. The PIVOTAL UNKNOWNS — must be resolved by a controlled LIVE probe (not answerable read-only)
The whole "rely on native OCO" premise rests on venue behavior the code does **not** document. These
are the make-or-break questions; resolve them with a tiny watched live trade **before** trusting the
design:
1. **Native OCO cancel linkage:** when one TP (or the SL) fills, does BitUnix auto-cancel the
   counter-orders (SL-fill → cancel the 3 TPs; final-TP → cancel the SL)? **No code evidence either
   way.** Operator believes yes. If NOT native → the bot must cancel (which collides with the
   "don't actively cancel" hard stop) → operator decision.
2. **Position-level SL auto-sizing:** the attached `slPrice` is a *position* stop (MARKET close on
   trigger). Does it **auto-track the reduced position size** after a TP partially fills (so the SL
   qty-resize is automatic and the bot only needs to MOVE the price), or is it a fixed-qty order that
   must be qty-resized? The WS `tpsl` channel carries `slQty`, hinting position-level — but
   auto-sizing is UNCONFIRMED. **This determines whether "resize" = price-only (easy) or qty+price.**
3. **Reduce-only LIMIT lifecycle when flat / over-size:** does a resting reduce-only LIMIT TP
   auto-cancel (or just no-op) once the position is flat or already reduced below its qty, or does it
   error (30018/30019 `REDUCE_ONLY_VIOLATION`)? Determines whether stale TPs linger.
4. **3-TP coexistence in practice:** do 3 reduce-only LIMITs summing to 100% rest simultaneously
   alongside the attached SL without tripping 30038?

→ **Recommendation:** the FIRST build step is a **read-only-then-one-live bracket probe** (operator-
watched, tiny size) that places the bracket and observes #1-#4 directly. Everything downstream
depends on the answers. (I could not test these — signed-API calls are out of bounds for this scope.)

---

## C. Place-at-entry plan
- **Injection point:** `bitunix_futures_observer.py:_place_live`, immediately after the
  `paper_trade_record` write (`~3189`, after the `except` at `~3194`, before the telegram push at
  `~3200`). At that point `fill.price`, `order.qty`, `order.side`, and `order.extra["tp_plan"]` /
  `stop_price` are all in hand.
- **What to place:** the SL stays attached-to-entry (unchanged — preserves atomicity). Then place
  **3 reduce-only LIMIT TP orders** using `_execute_live_exits` (`observer.py:3293-3466`) as the
  template, but with `order_type="limit"`, `limit_price=tp_leg.price`, `qty=round(entry_qty *
  tp_leg.fraction)`, `extra={reduce_only:True, exit_kind:"tp1"/"tp2"/"tp3", tif:"GTC"}` (POST_ONLY is
  an option for guaranteed-maker, but VERIFY-ON-LIVE the would-cross rejection — a TP placed when
  price already crossed it must fall back to a market reduce-only close, else that leg is silently
  dropped).
- **Qty accounting:** split entry qty by the 0.25/0.50/0.25 fractions with rounding so the 3 legs sum
  to exactly the position size (avoid 30038). Honor the venue min-qty/step (some legs may be too small
  at the current ~0.0003768 test size — flag: at tiny sizes a 3-way split may underflow min-order-qty,
  so the bracket may need a min-notional guard or fewer legs).
- **Degraded-but-safe property:** if TP placement is blocked (halt/stale — see §F) or partially
  fails, the position still has its atomic SL → **protected, just not profit-taking**. Failure here is
  non-catastrophic (unlike the old virtual TPs whose failure = unprotected/unmanaged).

## D. Native-OCO reliance + light verification (bot does NOT actively cancel)
- **Rely on native OCO** (per operator + pending the §B probe) for cancel-on-fill. The bot does NOT
  cancel counter-orders (hard-stop honored).
- **Light verification (reconcile):** after a terminal event (SL fill or final TP), confirm **no
  stale orders linger**. GAP: there is **no `get_pending_orders`/`list_open_orders` method** in the
  broker today (only `get_order_detail` by id, `get_history_trades`, `cancel_all_orders`). So the
  verification needs EITHER a new `get_pending_orders` REST call, OR `get_order_detail` polling per
  tracked bracket order id. If a stale order is found → alert (and, only as an operator-approved
  contingency, cancel it). **Flag:** if the §B probe shows OCO is NOT native, the design needs an
  explicit bot-cancel step — which conflicts with the current hard stop, so that becomes an operator
  call.

## E. SL move/resize on TP fill — the ONLY ongoing bot exit logic
- **Trigger/detect:** a TP fill shows as a **position-qty reduction** in `get_pending_positions`
  (already polled every 60s by the sanity-poll loop, `reconciler.py:1166-1240`) — `broker_qty <
  tracked_entry_qty` ⇒ a TP leg filled. (Alternatively poll `get_order_detail(tp_venue_order_id)` for
  exact leg attribution — needs the TP order ids stored at placement.)
- **Action — OPERATOR DECISION (flag):** on each TP fill, what does the SL do?
  - **(a) resize-only, same price** — just match the reduced qty (or no-op if the position SL
    auto-sizes per §B-2).
  - **(b) move to breakeven after TP1** — lock in no-loss once TP1 books.
  - **(c) trail behind each TP** — ratchet the stop up under each filled leg.
  - **NOTE:** the existing `tp_plan` already encodes a **hybrid default**: tp1→`move_to_breakeven`,
    tp2→`move_to_tp1`, tp3→`trail_atr` (`observer.py:2425-2433`). That's effectively (b)+(c). The
    mechanism should support any of (a)/(b)/(c)/hybrid via the per-leg `stop_action`; **which one is
    the operator's strategy call.**
- **Mechanism:** moving the SL = either (i) **cancel-replace** — `cancel_order(sl_venue_id)` + place a
  new attached/standalone market-stop at the new price/qty; or (ii) **in-place modify** via the
  already-stubbed `modify_position_tp_sl_order` (`bitunix.py:1841`, targets
  `/futures/tpsl/modify_position_tp_sl_order`) — cleaner (no naked window), but currently
  NotImplementedError. **GAP:** the SL's venue order id is **not captured today** (place_order returns
  only the entry orderId; the attached SL's id is never parsed/stored). Cancel-replace needs that id
  stored (e.g. `paper_trade_record.extra.broker_sl_order_id`); the modify path may key off the
  position instead. Recommend the in-place `modify_position_tp_sl_order` if §B-2 confirms position-
  level SL (no naked window, auto-sized).
- **FAILURE-TOLERANT (key):** a lagged/failed resize leaves the SL briefly **over-sized** (it would
  close more than the remaining position) — but reduce-only/position-SL caps at position size, so the
  worst case is a slightly-wrong stop, **NOT a missed profit** (the TP already filled on-exchange) and
  **NOT an unprotected position** (the SL still rests). So freeze/cadence lag here is non-catastrophic
  — the opposite of the old virtual-TP fragility.

## F. Cadence + guard interaction
- **Cadence:** a **60s poll suffices** (the existing position sanity-poll loop). Because exits rest
  on-exchange, real-time is NOT needed — this is the big simplification. For an SL *trail* tied to
  bars, note **1m bars are NOT wired today** (only 3m/1h/4h/1d; `LiveBarCache` supports 1m but no 1m
  cache is constructed — adding one is small). **3m is the minimum today; 1m is the operator's
  preference and a small add.** For (a)/(b) (resize / breakeven) the 60s position poll alone is
  enough; only (c) trail-by-bar wants 1m.
- **Guards — #5-B/C exemption is STILL required** (and is the one hard dependency): both
  `_halt_new_orders` (`bitunix.py:1053`, fires *before* `reduce_only` is read) and
  `_assert_snapshot_fresh` (`data_exec.py:186`, no reduce-only exemption) **block ALL order calls
  incl. reduce-only**. So they would block **bracket TP placement at entry** and **the SL
  move/resize**. These are exit-side actions that must be allowed. **Fix needed:** exempt reduce-only
  exit actions (TP placement + SL cancel-replace/modify) from the halt latch and the staleness gate.
  Mitigant: the SL itself is atomic-with-entry so it's never blocked; only the TP-ladder placement and
  the price-move depend on this exemption.

---

## G. How this de-risks the other bugs
- **#3 (lock-resilient fill-registration): STILL WANTED, but downgraded from critical → bookkeeping.**
  The bot must track the position to place the bracket + move the SL. BUT an orphan (registration
  failed) now **keeps its exchange-resting bracket** → fully protected (SL) *and* still takes profit
  (TPs) autonomously. So #3's failure no longer means "unprotected/unmanaged" — only "the bot won't
  *move* the SL." Much lower stakes. (Still build #3 — see sequence — so the bracket gets placed and
  the SL moved.)
- **#4 (orphan recovery): risk → bookkeeping.** An orphan is protected by its bracket; recovery is
  "re-adopt so the bot can move the SL," low urgency. The **non-negotiable safety guard still holds**:
  only adopt a POSITIVELY-identified bot orphan; never touch a manual position.
- **#5-A (build virtual scale-out): COLLAPSED.** No bot-side virtual TP monitoring/firing — the
  exchange takes the TPs. The bot's exit logic shrinks to one job (move the SL on fill).
- **#5-B/C (halt/stale exit exemption): STILL REQUIRED** — now the single hard prerequisite for the
  bracket actions.

## H. Build + validation sequence
1. **Live bracket PROBE (first — answers §B).** A read-then-one-tiny-watched-live trade: place the
   bracket, observe native-OCO cancel linkage, position-SL auto-sizing, reduce-only-LIMIT lifecycle,
   3-TP coexistence. Everything downstream is conditional on this. (Operator-run / operator-watched;
   real money, $1-class size.)
2. **#3 lock-resilient registration** — a confirmed fill always becomes a tracked position (so the
   bracket gets placed and the SL is movable).
3. **#5-B/C exit-guard exemption** — let reduce-only exit actions (TP placement + SL move) through the
   halt + staleness gates.
4. **Bracket-at-entry placement** — 3 reduce-only LIMIT TPs (qty-split, min-qty guarded) + keep the
   atomic SL; capture/store the SL (and TP) venue order ids.
5. **SL move/resize monitor** — 60s position-poll detect TP fill → move/resize SL per the chosen
   (a)/(b)/(c). Add a 1m bar cache only if (c) trail-by-bar is chosen.
6. **OCO light-verification** — add `get_pending_orders` (or per-id `get_order_detail`) reconcile to
   confirm no stale orders linger; alert on residue.
7. **#4 orphan recovery** — last, low-stakes (adopt-if-bot-identified, else leave-alone+alert).
8. **Validation gate:** paper/replay (bracket placement + a TP-fill→SL-move) → controlled live
   (bracket rests; a TP fills as maker at-price; OCO cancels correctly per §B; SL resizes/moves).
   "Validated" = first-ever live `exit_kind="tp"` books a maker fill at-price, OCO confirmed clean,
   SL moved, reconciler stays clean. **This is the end-to-end proof the bot can finally manage a trade
   to profit.**

## Out of scope / hard-stops honored
- SL stays guaranteed-fill **MARKET** (never limit). Bot relies on **native OCO** (verifies, does not
  actively cancel — unless §B proves OCO isn't native, then operator decides). No code/deploy/prod
  write. No live position/engine change (operator hawk-watches). No signed-API calls made in this
  scope. Polymarket untouched.
