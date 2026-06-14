# Bitunix first live fill — close-out validation (READ-ONLY)

- **When:** investigated 2026-06-14 ~19:15–19:25 UTC. Entry 18:24:08, stop-out ~19:12 UTC.
- **Method:** read-only SSH per `82fda13` (`sqlite3 -readonly`, `journalctl`, source review). **No writes. No signed/public-API calls. No corrective action.**

## VERDICT

**At the BROKER: ✅ cleanly closed / flat.**
- The SHORT stopped out at ~19:12 UTC; the account is **flat** (reconciler `match=0, orphan=0`, and **no `get_pending_positions` failures** → the empty list is a real flat, not a masked API error).
- **B1 server-side stop fired correctly — FIRST real-fill validation = PASS.**
- **Only ONE bitunix order was ever placed** (the entry). No rogue/double position; the "orphan" was the bot misreading its own short (see below).

**In the BOT's BOOKS: ❌ NOT cleanly closed — three coupled issues.**

1. **Exit is UNBOOKED** — `paper_trade_record.result` is still `NULL` (no exit price / PnL / exit fee). By design, a broker-side close shows as `missing_on_broker`, which is **deferred to operator resolution** (reconciler `resume_live_positions` Phase 1b §4), not auto-booked. The replay loop (15-min cadence; ticks 18:33/18:49/19:04 all `still_open:1`) never resolved it.
2. **Reconciler FALSE divergence since 18:25:04** (every ~60s) — it cannot match its own live position. Root cause = the match is by exact `(symbol, side)` (`reconcile_position_state` lines 505-508):
   - **Symbol:** bot row `BTC/USDT.P` vs broker `BTCUSDT` → never equal.
   - **Side:** `get_pending_positions` negates qty only when `side == "SHORT"` (bitunix.py:1029-1031); BitUnix returned a non-`"SHORT"` label → qty stayed **positive** → `_broker_side` = `"buy"` ≠ bot `"sell"`.
   - Either failure alone guarantees `missing_on_broker` (the bot's short) + `orphan_on_broker` (a phantom "buy 0.0004") for **every** live bitunix position. (`0.0004` = `0.000485497` truncated to broker 4dp; the orphan's lifecycle exactly tracked the real position: present 18:25→19:11, gone at the 19:12 stop-out.)
3. **Bitunix broker latched `_halt_new_orders = True`** — the sanity-poll loop (`run_position_state_sanity_poll_loop` → `reconcile_position_state`, default `halt_on_divergence=True`) sets `broker._halt_new_orders=True` / reason `position_state_reconciler_divergence` on every divergence tick (lines 576-581). `place_order` refuses new orders while set (bitunix.py:849); it clears only on broker re-init. **→ new live bitunix ENTRIES are now blocked (exits unaffected), and it will NOT self-clear** while `result` stays NULL. (Flag is in-process memory — not directly readable read-only — but the code path is deterministic and the divergence is present, so the latch is effectively certain.)

## Timeline (UTC)
- 18:24:08 — entry fill: SHORT 0.000485496950614426 BTC @ 63678.1 (server-side SL 63805.3397 attached).
- 18:25:04 — reconciler FIRST false-divergence (missing short + orphan `buy 0.0004`); repeats ~every 60s.
- 18:33 / 18:49 / 19:04 — replay ticks `still_open:1, errors:0` (never resolves).
- ~19:12 — real stop-out: orphan vanished between the 19:11:46 and 19:12:46 reconciler ticks; broker flat.
- 19:15+ — reconciler now `missing=1, orphan=0`; `result` still NULL; entries-halt latched.
- Position held ≈ 48 min.

## Entry / Exit / Fees breakdown

**ENTRY — bot-confirmed (broker truth via FillEvent):**
- Time 2026-06-14 18:24:08 UTC · SHORT (sell) · qty 0.000485496950614426 BTC.
- Fill price **63678.1** · notional ≈ **$30.92** · leverage 25× · margin ≈ $1.24.
- **Entry fee = $0.005094248** (taker; ≈0.0165% effective).

**EXIT — NOT captured by the bot (server-side stop closed it; exact fill is in the BitUnix UI):**
- Time ≈ 19:12 UTC · server-side SL trigger **63805.3397** (MARK_PRICE, MARKET → BUY close).
- **Actual exit fill price, exit fee, and any funding fee are NOT in the bot DB** — authoritative values are in BitUnix order/trade history. (Not fetched here — would require a signed API call, outside read-only policy.)

**PnL — approximate (assuming exit ≈ the 63805.34 trigger):**
- Gross ≈ (63678.1 − 63805.34) × 0.000485497 ≈ **−$0.062**.
- Net ≈ **−$0.072** incl. ~$0.005 entry + ~$0.005 est. exit fee (~7 cents). Small.
- NB: recorded budgeted risk `max_dollar_risk` ≈ $0.122 is risk-as-%-of-equity; actual stop-distance risk ≈ $0.061 because `htf_size_multiplier=0.5` halved the size (not a bug).

## Recommended (NO action taken — operator decides)
1. **Clear the bitunix entries-halt** (verify `_halt_new_orders`; likely latched). Until cleared, **no new live bitunix trades will place.**
2. **Book this trade's exit** (operator-resolve `missing_on_broker`) using the BitUnix actual exit fill + fee.
3. **Fix the two reconciler matching bugs** — symbol normalization (`BTC/USDT.P` ↔ `BTCUSDT`) + side-sign (`get_pending_positions` short→negative qty for non-`"SHORT"` labels). **This breaks live reconciliation for ALL bitunix trades, P1/P2.**
4. **Address the architecture gap:** a server-side-stop close is not auto-booked — the replay loop expects to place the close itself; when B1 beats it, the record is stranded until manual resolution.

## Disclosure (82fda13)
Agent read-only SSH (`sqlite3 -readonly`, `journalctl`, no writes) + local source review only. No signed/public-API GETs (exact exit fill/fee deliberately not fetched). No prod writes, no position changes, no corrective action.
