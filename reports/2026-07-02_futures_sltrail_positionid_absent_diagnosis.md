# bitunix_futures SL-trail "positionId absent" — root-cause diagnosis (read-only)

**Date:** 2026-07-02 · **Scope:** bitunix_futures only (SFP untouched) · **Mode:** read-only, no code, no prod writes
**Engine:** PID 53372 (py child 53387), NRestarts=0, boot 2026-07-02 02:10:04 UTC, active/running

## Symptom (as reported)
`BitUnix modify_position_sl: positionId absent for BTC/USDT.P — skipping (fail-soft; SL stays at prior price)`
recurring 2026-07-02 at **05:10:04 / 06:49:03 / 08:06:16 UTC**. Framed as "SL trail not updating — the futures
stop stays parked while the position moves; silent risk-management degradation on live money."

## VERDICT

**Root cause:** a **cosmetic post-close false-positive** in `move_bracket_sls`. Each warning fires *after* a
bracket position has **already fully closed**, when there is no position left to protect. **Zero risk impact.**
The symptom's premise ("stop stays parked while the position moves") is **not supported by the evidence** — see
"Risk impact" and the anomaly note below.

**New vs long-standing:** **long-standing.** `positionId absent` occurs on *every* bracket-managed BTC/USDT.P
close and dates back to **Jun 19** (24+ occurrences Jun 19 → Jul 02) — it predates both the futures cutover
(Jun 30) and the SFP bidirectional deploy (Jul 01). Not introduced by any recent change.

**Unrelated to the SFP deploy:** confirmed. Different code path (reconciler `move_bracket_sls` →
`brokers/bitunix.py modify_position_sl`), different account (bitunix_futures vs bitunix_sfp), and SFP had **zero
trades** since deploy so never exercised this path today.

---

## a. The code path

`trading_corp/agents/divisions/bitunix_position_reconciler.py`

- Reconciler tick (every 60s) → line **1236** `await move_bracket_sls(broker, db_url, division=division)`. This is
  the **only** caller of `modify_position_sl` in the whole tree (grep-confirmed).
- `move_bracket_sls` (line **1269**) is a **TP-fill-triggered SL ratchet**, NOT a continuous price/ATR trail:
  1. reads open bracket rows from `paper_trade_record` (`result IS NULL`, `extra_json` has `bracket_entry_qty`,
     `execution_mode == "live"`).
  2. `positions = await broker.get_pending_positions()`; builds two maps keyed by `(symbol_key, side)`:
     `pos_qty[key] = abs(qty)` and `pos_id[key] = p.extra["positionId"]` (only if present).
  3. **line 1335** `current_qty = pos_qty.get((symbol_key, side), 0.0)` — **defaults to 0.0 when the key is absent.**
  4. **line 1336** `if current_qty >= entry_qty - 1e-12: continue` — i.e. `current_qty < entry_qty` is read as
     "a TP filled → move the SL."
  5. computes `new_sl` via `bitunix_bracket.decide_sl_move` (TP1→breakeven, TP1+TP2→SL-to-TP1, and post-TP2 a
     Chandelier `extreme ± trail_atr_mult×ATR` trail — all inside the same function, applied through this same
     single path).
  6. **line 1357** `broker_position_id = pos_id.get(pos_key)` → passed to `modify_position_sl(...)`.

`trading_corp/brokers/bitunix.py` `modify_position_sl` (line **2155**)

- `position_id` is **mandatory** (by design): line **2184** `if not position_id:` → logs the "positionId absent"
  WARNING and returns `False` **without calling the venue** (line 2186). This guard is *correct* defensive code.

## b. WHY positionId is absent at "trail" time — mechanism

When a bracket position **closes fully** (B1 attached stop hits, or a full TP sweep), the venue drops it from
`get_pending_positions()`. So for that `(symbol, side)` key:

- `pos_qty.get(key, 0.0)` → **0.0** → satisfies `current_qty < entry_qty` → **false-positive "TP fill detected"**.
- `pos_id.get(key)` → **None** (same reason: the position is gone) → `modify_position_sl` short-circuits at the
  mandatory-positionId guard → **"positionId absent" + moved=false**.

The two facts collide on the *same* condition (position absent from the venue list), so a full close is
mis-classified as a TP fill *and* has no positionId — producing the warning as a **no-op** (there is nothing to
move; the position already closed).

### Smoking gun — `position_sl_update` audit rows (2026-07-02)
Every `positionId absent` event has **`current_qty = 0.0`**; the one *successful* move had a **partial** qty:

| Time (UTC) | order | current_qty | entry_qty | moved | reason |
|---|---|---|---|---|---|
| 05:10:04 | 4f9fa339 (#1) | **0.0** | 0.000447 | **false** | TP1+TP2 filled → SL to TP1 |
| 05:48:00 | a3622d4c (#2) | 0.0004 | 0.000894 | **true**  | TP1 filled → SL to breakeven |
| 06:49:03 | a3622d4c (#2) | **0.0** | 0.000894 | **false** | TP1+TP2 filled → SL to TP1 |
| 08:06:16 | 565c5381 (#3) | **0.0** | 0.000903 | **false** | TP1+TP2 filled → SL to TP1 |

The 05:48 row (`current_qty 0.0004`, genuinely open after a real TP1 fill) moved the SL to breakeven
**successfully** — the "SL moved (price-only)" success counted in journalctl. positionId threading works whenever
the position actually exists.

### Candidates evaluated (STEP 3b)
- **timing/race** — CONFIRMED as the class: the warning is a post-close artifact (position closed at venue but
  the tracked row not yet booked/`result` set) — but it is benign, not a harmful race.
- **key mismatch (BTC/USDT.P vs BTCUSDT)** — **REFUTED.** Symbol matching works: the concurrent-position guard
  correctly detected open positions (06:36/06:48), and the 05:48 SL move succeeded.
- **map desync / reconciler self-halt** — **REFUTED for this session.** `self-halt`/`halt_released` count = **0**
  today; NRestarts=0. Not correlated. (The "self-halted 3×" premise in the handoff does not hold here.)
- **venue shape (positionId field not read)** — **REFUTED.** positionId *is* read and used successfully when the
  position is present (05:48).

## c. Blast radius
- **Long-standing**, once per bracket close. journalctl history of `positionId absent for BTC/USDT.P`:
  Jun 19, Jun 20 (×2), Jun 21, Jun 22 (×2), Jun 23 (×2), Jun 24 (×6), Jun 30 (×4), Jul 01 (×2), Jul 02 (×3).
- Predates futures cutover (Jun 30) → the Jun 19–24 hits are the earlier single-bitunix bracket division; same
  code, same artifact.
- **Cross-check vs the 30038 / TPSL_EXCEEDS_POSITION issue and bitunix-tpsl-rebuild-section-b: SEPARATE.** Those
  concern **entry-time TP-ladder placement** (`place_tpsl_order`, list-vs-dict parse, TP qty vs position). This is
  the **exit-side SL ratchet** (`modify_position_sl` / `/tpsl/position/modify_order`). Different endpoint,
  different trigger, different failure. No shared cause.
- **Unrelated to the SFP bidirectional deploy** (different path, different account; SFP had zero trades).

## d. Actual risk impact — NONE

| # | order | signal | entry | init stop | result | exit | PnL | R | TP fill during life? | trail acted? |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 4f9fa339 | cvd_bear_flip (STD) | 60648.0 | 60772.07 | loss | 60771.1 | −$0.048 | −0.87 | no | n/a (stopped at initial) |
| 2 | a3622d4c | mc_a_redx (PREM) | 60656.9 | 60784.76 | **win** | 60424.25 | +$0.190 | +1.66 | **yes (TP1)** | **yes — SL→breakeven, 05:48, moved=true** |
| 3 | 565c5381 | mc_a_red_diamond (PREM) | 60108.5 | 60227.85 | loss | 60228.9 | −$0.114 | −1.05 | no | n/a (stopped at initial) |

Day net ≈ **+$0.028**.

- The only SL move that mattered (trade #2, breakeven-lock after TP1) **succeeded**; #2 then ran to a +1.66R win.
- Trades #1 and #3 had **no TP fill** during their lives, so by design the SL stayed at initial and executed the
  stop — correct behavior, not degradation. Each `positionId absent` fired strictly **after** the position had
  already closed (`current_qty=0.0`).
- **No open position ever had a needed SL move blocked by an absent positionId.** Loss deltas from the trail bug:
  **$0.**

## PROPOSED FIX (described, not written — for operator review)

**Priority: LOW** — cosmetic log-noise + a misleading "SL stays at prior price" message that *implies* risk where
there is none. Not urgent. Entirely in the caller `move_bracket_sls`; **do not** touch `modify_position_sl`'s
mandatory-positionId guard (that guard is correct).

**Primary (Fix A) — distinguish full-close from partial-TP-fill.** Before the TP-fill test, skip when the position
is absent/zero at the venue:
```
key = (symbol_key, side)
if key not in pos_qty or pos_qty[key] <= 0:
    continue   # position closed/absent — nothing to trail; the auto-book path books the row
```
Then keep the existing `if current_qty >= entry_qty - 1e-12: continue`. Net effect: a fully-closed position no
longer trips the false-positive, so `modify_position_sl` is never called with an absent positionId → warning gone.
No protection is lost (a closed position has nothing to protect; booking is handled separately).

Bonus: also silences the same warning when the venue is legitimately flat (empty positions list → every tracked
row would otherwise look "reduced to 0").

**Secondary (Fix B, optional) — downgrade severity.** Even with Fix A, if a positionId is ever genuinely missing on
a *still-open* position, prefer a single clearly-worded WARNING; the current message wrongly reads as a live-risk
event. Consider logging the closed-position case (if ever reached) at DEBUG.

**Out of scope (flag only, not part of this bug):** whether the exit design *should* include a pre-TP1 price/ATR
trail to cut losers like #1/#3 is a **strategy question**, separate from this positionId-absent artifact. Not
addressed here.

## Anomaly surfaced
The handoff's premise — "SL trail is NOT updating; the stop stays parked while the position moves; silent
risk-management degradation on live money" — is **not supported.** This trail only ratchets on TP fills (+ a
post-TP2 Chandelier), not continuously on price; it worked correctly on the one trade with a TP fill (#2, +1.66R),
and all three warnings are post-close no-ops. There is **no live-money risk** from this symptom.

## Futures backlog (flagged, no code)
**pre-TP1 price/ATR trail — strategy question (2026-07-02 diag).** Trades #1 (4f9fa339) and #3 (565c5381) each
stopped at their *initial* stop with no trail before TP1 — this ratchet only acts on TP fills (breakeven after
TP1, SL-to-TP1 after TP2, Chandelier after TP2). Whether to add a pre-TP1 price/ATR trail to cut such losers is a
strategy decision that needs its **own backtest arc**; it is NOT part of this positionId-absent fix.

## Fix status (2026-07-02)
Both parts **BUILT** on branch `futures-sltrail-diag-2026-07-02` (commit `701a9fb`), **caller-only, UNPUSHED, no
prod writes** — deploy timing is the operator's call (can ride to the next futures touch).
- **Fix A** — skip fully-closed/absent positions (`pos_key not in pos_qty or pos_qty[pos_key] <= 0`) *before* the
  TP-fill test, so a full close is never mis-read as "TP1+TP2 filled".
- **Fix B** — replace the misleading broker WARNING path with a reduced-severity, unambiguous `post-close no-op`
  breadcrumb in the caller. `modify_position_sl` and its mandatory-positionId guard are **untouched**.
- **Proof** — `tests/test_bitunix_bracket_sl_move_post_close.py`: the post-close test FAILS against pre-fix code
  (`modify_calls` gets `{new_sl: 99.0, position_id: None}` — the exact bug) and PASSES post-fix; the genuine
  partial-TP-fill test passes on BOTH (no open-position behavior change). Reconciler+SFP subsets green, 0
  regressions.
