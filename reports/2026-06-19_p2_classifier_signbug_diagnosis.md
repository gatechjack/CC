# P2 auto-book classifier mis-sign + exit-kind bug — DIAGNOSIS (Phase 1, read-only)

**Date:** 2026-06-19 · **Branch:** `bitunix-tpsl-rebuild-2026-06-18` · **Mode:** READ-ONLY (§4)
**Disclosure:** agent read-only SSH `82fda13`. NO build, NO record write, NO deploy. Await operator go.

## 1. Root cause

The live P2 auto-book classifier lives in `trading_corp/agents/divisions/bitunix_position_reconciler.py`,
in two functions — `_autobook_missing_close` (known-level estimate) and `_autobook_missing_close_real`
(#1 signed real-fill). **Both HARD-CODE two categorical labels in the `UPDATE`, regardless of the
(correctly computed) PnL:**

| Label | Where | Bug |
|---|---|---|
| `result = 'loss'` | reconciler `:585` (estimate), `:747` (real-fill) | string literal — never derived from the `pnl` / `net` sign |
| `autobook_level_type = 'stop'` | reconciler `:593`, `:755` | string literal — never derived from the actual fill |

**Why a positive-PnL exit is labeled 'loss':** the functions compute `pnl` correctly
(`(entry-level)·qty` for a short / `(level-entry)·qty` for a long, `:575-576` / `:729`) and the real-fill
path books the REAL fill VWAP — so `actual_pnl_dollars`, `actual_r_multiple`, `net_realized_usd` are all
**correct and can be positive** — but `result` is the literal `'loss'`. So any auto-booked close with
positive PnL is mislabeled. The records are internally inconsistent: `result='loss'` next to
`actual_r_multiple=+0.291`.

**Why a TP fill is labeled 'stop':** the auto-book path is only reached when `filled_legs` is **empty**,
and the design *assumed* "empty filled_legs ⟹ no TP reached ⟹ it was the server-side stop ⟹ a loss"
(docstring `:519-525`). That assumption breaks because:
- the managed TP/virtual-exit path **never recorded `filled_legs`** (see [[bitunix-orphan-managed-exit-bug]]
  — managed exits never succeeded live), so even a **TP-driven** close presents as `filled_legs=[]`; and/or
- the B1 SL was **trailed to breakeven/TP1**, so "the stop" fires at a **profit**.
Either way the close is funneled into the stop-assumed branch and stamped `'loss'` + `'stop'`.

**Scope boundary (confirmed):** the **paper** path (`paper_trade_replay.py:547`,
`result = "win" if actual_r > 0 else "loss"`) is CORRECT — derives from sign. The corruption is
**live-only** (the auto-book path).

## 2. Blast radius (prod DB, read-only) — exactly 2 records

All 5 live bitunix trades are auto-booked (`auto_booked_from_real_fill`). Two have a **positive PnL booked
as `result='loss'`** (and `autobook_level_type='stop'`):

| order_id | ts | side | result | gross pnl | r | net | lvl | correct result | correct exit_kind |
|---|---|---|---|---|---|---|---|---|---|
| `e1758fc9` | 2026-06-18 01:39 | sell | **loss** | **+0.03489** | +0.291 | +0.02443 | stop | **win** | **tp** (operator-confirmed maker TP fill) |
| `7d1a78dc` | 2026-06-18 15:18 | sell | **loss** | **+0.29822** | +0.863 | +0.26776 | stop | **win** | tp? (favorable close, had 2 TP legs — verify fill-vs-levels) |

Counts (bitunix, all-time): wins-booked-as-loss = **2 (both live)**; losses-booked-as-win = **0**;
gross>0-but-net<0 (fee-flip) = **0** (so the win flip is unambiguous under BOTH gross and net for these
two); `autobook_level_type='stop'` on a positive-PnL close = **2** (the same two). The other 3 live trades
(`679c15e2`, `a919d1f5`, `cb6b4d4a`) are real losses, correctly `loss`/`stop`. The 2 paper
`auto_booked_from_stop_level` rows are real losses (negative PnL) — NOT affected.

**→ Historical correction scope = 2 records, LABEL-ONLY** (`result`; and `autobook_level_type`/`exit_kind`).
The PnL VALUES (`actual_pnl_dollars`, `actual_r_multiple`, `net_realized_usd`) are CORRECT and must NOT be
touched.

## 3. Corrected classifier logic

**`result`** — derive from the booked PnL sign (never a literal):
```
basis  = net_realized_usd if present (real-fill path) else pnl   # estimate path has no fees
result = 'win' if basis > 0 else 'loss'                          # 0 → loss (matches paper convention)
```
(Recommend NET as the basis where available — fees are real and recorded; falls back to gross `pnl` for
the estimate path. Open decision §6.)

**`exit_kind` / `autobook_level_type`** — derive from the ACTUAL fill, not a literal:
- **Robust (now feasible):** match the closing fill to the tracked orders — fill order-id ∈
  `bracket_tp_order_ids` → `'tp'`; == `bracket_position_sl_order_id` (or fills at the recorded stop
  level) → `'stop'`. (The tpsl rebuild now records both.)
- **Fallback (estimate path / no id):** infer from price — fill at/beyond a TP level → `'tp'`; at/near the
  (possibly ratcheted) stop → `'stop'`; a stop that fired above entry (trailed-in-profit) → `'stop'` (a
  sub-flag `trailed=true` is clearer than calling it a tp). **When genuinely ambiguous, record
  `'unknown'` — never default to `'stop'`.**
- exit_kind is harder than result (trailed-stop-in-profit vs TP both close favorably); the order-id match
  is the clean resolution going forward.

## 4. Bundled fix (a) — `mc_a_yellow_x` side miscategorization

`config/strategies.yaml:1131-1135`:
```
mc_a_yellow_x:
  weight: 2
  side: buy        # ← line 1133
```
It sits among the bull signals (`mc_a_bluetriangle`/`mc_a_longema` = buy) but the `_x` suffix matches the
**bear** signals (`mc_a_redx` = sell — empirically confirmed: trade `cb6b4d4a` fired `mc_a_redx` as a
SHORT; `mc_a_red_diamond`/`mc_a_blood_diamond` = sell). MarketCipher "yellow X" is a bearish cross.
**One-line fix:** `:1133` `side: buy` → `side: sell`.
⚠ This **changes live trading behaviour** (not a passive data fix) — recommend the operator confirm the
MarketCipher signal semantics before flipping. (Surfaced, not auto-resolved.)

## 5. Bundled fix (b) — maker/taker role not recorded

Today only the REAL summed fee is kept (`_aggregate_close_fills` sums `f.get("fee")`,
reconciler `:629-655`); `get_history_trades` (`bitunix.py:1485`) returns the venue `tradeList` but the code
extracts only `price`/`qty`/`fee` — the **role is discarded** and only inferable by back-computing the
effective fee rate. Scope to record it explicitly, forward-going (additive, no behaviour change):
1. **Parse** the role from each venue fill — confirm the exact field name from the BitUnix trade-history
   doc / first live fill (candidates: `roleType` / `role` / `isMaker`) — **do NOT guess** (same discipline
   as the tpsl `orderId` shape).
2. **Thread** it through `_aggregate_close_fills` → add a `maker_taker_mix` (notional-weighted maker
   fraction + per-fill roles) to its return.
3. **Persist** on the auto-book write (`$.exit_role` / `$.maker_taker_mix`) and on the entry fill
   (`$.entry_role`, alongside the existing `$.entry_fee_usd`).

## 6. Open decisions for the operator (before Phase 2 build)

1. **result basis:** NET (recommended) vs GROSS. (Moot for the 2 existing records — both positive either
   way — matters only forward.)
2. **exit_kind field:** write the corrected label to `$.autobook_level_type` only, or also mirror to
   `$.exit_kind` so live stats and the observer/paper records share one field?
3. **`7d1a78dc` exit_kind:** confirm tp vs trailed-stop from its close-fill price vs the TP/stop levels
   (read-only) before relabeling — `result→win` is unambiguous regardless.
4. **`mc_a_yellow_x` flip:** confirm the bear semantics (it changes live behaviour).

## 7. Phase-2 plan (on operator GO — NOT started)

- **Code:** fix the classifier (result from PnL/net sign + side; exit_kind from the actual fill / order-id
  match) + `yellow_x` config side + maker/taker recording. Tests: a win books `win`, a maker TP books
  `tp`, a real stop books `stop`, a trailed-stop-in-profit, ambiguous→`unknown`, the 2-record edge cases.
  Full regression, zero new. Commit on branch, push, unmerged. Classifier fix needs a separate redeploy.
- **Historical correction (DATA WRITE, Board-gated):** a read-only **dry-run** listing every record that
  would change (old→new, by PnL sign; LABEL-ONLY, PnL untouched) → **backup the table** → produce the
  correction script for operator review. Do NOT auto-apply. Scope today = the 2 live records above.

## Evidence index
- Code: reconciler `_autobook_missing_close` (`:515-612`), `_autobook_missing_close_real` (`:658-783`),
  `_aggregate_close_fills` (`:629-655`); `paper_trade_replay.py:547`; broker `get_history_trades`
  (`:1485-1500`); `config/strategies.yaml:1131-1135`.
- Data: prod `data/trading_corp.db` (read-only) — 5 live auto-booked records (2 mis-signed), 146 paper
  (correct), counts per §2.
