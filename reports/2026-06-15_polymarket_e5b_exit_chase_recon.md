# E5b — exit escalating-chase + reconciliation: recon + locked scope

Read-only recon, 2026-06-15. Base at recon time: main `21d3f59` (E5a merged; `Division.exit_chase` hook present).
Filed 2026-06-16 on branch `e5b-recon-2026-06-15` (off current main `b3d1f08` — the bitunix batch advanced main
since the recon; `21d3f59` is now an ancestor, polymarket files unchanged by it). Durable scoping record for the
E5b plan→build. No code here.

## Locked design decisions (operator)
1. **Chase loop = BROKER-side.** A new `PolymarketLiveBroker` method loops internally (post → poll → reprice →
   repost residual → terminal), returns a **cumulative `FillEvent`**. Strategy reconciles; broker owns execution.
2. **Best-bid pricing via `get_price(token_id, SELL)`.** Adopt adaptation (a) — wire the SDK's `get_price`
   (currently unused) for true best-bid; chase prices each step off best-bid + a spread fraction. (NOT midpoint.)
3. **Terminal aggressiveness = config knob.** The terminal is an aggressive marketable limit whose crossing
   depth/price is a knob in `exit_chase` (no true market primitive exists — see finding 3). Best-effort sweep.
4. **Residual handling: retain + manual-reconcile flag; auto-retry DEFERRED.** If the terminal leaves a residual,
   the position is **retained** (visible, not popped) and **flagged for manual reconcile**. Strategy-driven
   auto-retry of stuck residuals is explicitly deferred to a later increment.
5. **`size_matched` taken at face value for v1.** Accept the #245 over-count risk in v1 (residual may be
   understated if `size_matched` overstates); on-chain receipt cross-check deferred.
6. **All knobs live in `Division.exit_chase`** (the E5a pass-through dict): patient attempt count N (default 3),
   spread fraction per step, per-step poll window, terminal crossing depth, etc. E5b adds the `exit_chase` ctor
   param to `PolymarketLiveBroker` and wires it at the construction site (`main.py:2078-2093`, the E5a comment
   already flags "exit_chase forwards the same way once E5b adds the ctor param").

## The 7 recon findings (evidence)

**1. Exit placement internals.** `polymarket_copy_trader.py` SELL branch `:327-336`: `_emit_exit` is **not awaited**
(builds a `ProposedOrder` only), then **`:336 our_positions.pop(pos_key, None)`** pops at *proposal-emit* — before
placement, regardless of fill. → `_handle_copy_order_placement` (`main.py:3385`): `:3423 fill = await
data_exec.place(...)` returns a real `FillEvent` for exits too, but both reconcilers are entry-gated (`:3427`
`discard_entry`, `:3437-3438` `record_entry_fill` under `if ext.get("is_entry")`) → **the exit fill is dropped.**
After E5a the broker gets `order_type`/`fak_poll_seconds` from the Division (`main.py:2084-2090` `exec_kwargs`);
`exit_chase` is **not yet passed** (E5b wires it).

**2. Live quote — MIDPOINT-ONLY today (→ decision 2).** `PolymarketLiveBroker.quote()` (`:735`) →
`PolymarketBroker.quote()` (`polymarket.py:421-422`) returns a **`float` = live-book midpoint** via
`clob.get_midpoint` (`:510`). No best-bid/ask or book is exposed. The SDK **has** `get_price(token_id, BUY/SELL)`
(`polymarket.py:481`, **unused**) → E5b wires it for best-bid.

**3. No true market primitive (→ decisions 3,4).** Natives are **GTC/FOK/GTD only** (`_native_order_type`
`polymarket_live.py:361`); a marketable limit can return `unmatched` → `OrderPlacementError` (`:393-397`); FOK is
all-or-nothing. A *guaranteed* market sweep **cannot be built** → terminal = aggressive marketable limit
(best-effort, depth-limited), residual can survive.

**4. FAK-synth is reusable → broker-side loop (→ decision 1).** `place_order_fak_synth(client, order, *,
poll_seconds, …)` (`polymarket_live.py:473-529`) is **standalone + stateless** — post GTC → `_poll_order_to_fill`
→ cancel remainder → `FillEvent` (or `NoFillInWindow`). Callable repeatedly with a new order (residual size, worse
price) per iteration. Chase = N broker-side calls off `order.qty`, gated on **exit only** (`extra["is_entry"] is
False`) + `exit_chase` enabled (entries stay single fak-synth, E2·6 untouched); returns a cumulative `FillEvent`.

**5. Residual per attempt — `size_matched` (→ decision 5).** `FillEvent.qty = float(size_matched)`
(`_poll_order_to_fill:327`), per attempt; residual = `order.qty − Σ fill.qty`. Face-value (`:294`), no on-chain
cross-check → #245 inherited (over-count understates residual). Accepted for v1.

**6. Dedup-orphan — silent orphan REMOVED; "retained-but-stuck" is the new shape (→ decision 4).** Pop-only-when-
fully-closed eliminates today's silent orphan (partial/no-fill exit now retains a visible position). But because
the terminal can't guarantee a full exit (finding 3) and the whale SELL txhash is deduped (`:341-347`), a residual
can survive with no whale-driven retry → **retained-but-stuck** (visible, flagged for manual reconcile;
auto-retry deferred).

**7. Currency.** All three files current at the recon base, clean, untouched by the bitunix B2 / reverted-stash
contamination: `polymarket_copy_trader.py` @ E2·6 `7b2b70e`; `main.py` + `polymarket_live.py` @ E5a `64a93df`.

## E5b build outline (for the plan session)
- **Broker:** add `exit_chase` ctor param; add `get_price(token_id, SELL)` best-bid read to `PolymarketBroker`;
  add a broker-side chase method (loop: best-bid → price step → `place_order_fak_synth(residual)` → accumulate →
  N attempts → config terminal) returning a cumulative `FillEvent`; gate on exit + `exit_chase` enabled.
- **Strategy/handler:** stop the premature `:336` pop (pop only when cumulative fill == lot); add `record_exit_fill`
  (symmetric to E2·6 `record_entry_fill`) — decrement by actual cumulative `fill.qty`, retain + flag residual.
- **Config:** define the `exit_chase` schema under `Division.exit_chase` (N, spread fraction, poll window, terminal
  depth); defaults behavior-preserving (chase OFF unless configured).
- **Tests (mocked):** central partial-exit invariant (partial chase → position decremented by actual, residual
  retained, not popped to zero); terminal-residual → retained + flagged; entry path unchanged; #245 caveat noted.
- **Deferred:** auto-retry of stuck residuals; on-chain `size_matched` cross-check.
