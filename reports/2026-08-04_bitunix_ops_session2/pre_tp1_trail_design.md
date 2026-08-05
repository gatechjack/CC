# TASK 2a — Pre-target trailing stop for the Bitunix SFP division: design (one page)

**Scope (per operator ruling 2026-08-05).** SFP is a **single-leg** construct: one full-qty
reduce-only TP at `tp_r: 3.0` OCO'd with the B1 server-side stop; there is **no TP1/TP2, no
BE-at-TP1, no trail** today (`bitunix_sfp_observer.py:_place_tp_leg` @1099/1133/1162; the `"tp1"`
leg label is the single full-qty leg). The prompt's "pre-TP1 trail / BE-at-TP1" language was an
operator-side conflation with the **futures** tiered bracket (`bitunix_futures_observer.py:2446-2451`,
`move_to_breakeven`@TP1 / `move_to_tp1`@TP2 / `trail_atr`@TP3). Here **"pre-TP1" = pre-target**: a
trail active only between entry fill and the single 3R target. **Config flag default OFF; no deploy;
enable is backtest-gated** (this file = design; `pre_tp1_trail_backtest.md` = evidence).

## Current SFP exit lifecycle (what exists)

```
entry fills (taker) → _place_tp_leg rests ONE native reduce-only TP @ entry ± 3·r_unit
                       (OCO with the B1 server-side stop @ swept_extreme ∓ 0.001·entry)
   r_unit = |entry − stop|.  Exit = 3R target (maker/limit) OR B1 stop (taker) OR max_hold timeout.
   No stop movement between entry and exit. A trade that runs toward 3R then reverses gives the
   whole unrealized move back to the fixed B1 stop.
```

## Proposed mechanism (pre-target trail)

A **monotonic, one-sided** stop ratchet, armed only after favorable progress, never looser, capped
below the 3R target so it cannot pre-empt it.

- **Trigger (activation):** arm the trail once the trade first reaches `activation_r` of favorable
  progress (candidates swept in the backtest: **none/immediate, +1.0R, +1.5R, +2.0R**). Below the
  activation threshold the stop stays the fixed B1 stop.
- **Trail formula (long; short mirrors with signs flipped):**
  ```
  once armed:  extreme      = max(high) since entry
               trail_stop   = extreme − atr_mult · ATR(atr_len)
               SL           = max(SL, trail_stop)          # monotonic up, never loosens
               SL           = min(SL, entry + 3·r_unit − ε) # capped strictly below the 3R target
  ```
  `atr_mult` swept **wide→tight** (the whole ball game). ATR measured on the detection/entry TF
  (documented in the backtest). An R-multiple ladder variant (+1R→BE, +2R→lock +1R — the
  **already-run** `_sfp_betrail_exit.py` config) is carried as a reference cell.
- **Exit is unchanged in kind:** still the 3R TP (maker) OR the (now possibly ratcheted) stop
  (taker) OR timeout. No partials — the right tail is preserved except where the trail truncates it.

## Interaction with existing machinery

- **Fixed B1 stop:** the trail **replaces the B1 stop's price with a tighter one** once armed; it
  never moves the stop wider than B1 and never below entry-risk. B1 remains the floor (initial and
  worst-case). Disarmed (flag OFF) ⇒ byte-identical to today.
- **OCO 3R TP:** untouched. The trail is capped strictly below 3R so the OCO target always wins a
  tie; the two legs stay mutually exclusive.
- **Single risk chokepoint (`RiskAgent.evaluate`):** the trail only **tightens an existing
  protective stop** — it creates no new position and adds no size, so it introduces no new notional
  through the gate. This is exit management, not order construction. Implementation must guarantee
  the SL-modify can only reduce risk (assert `new_SL` is monotonic-favorable and ≤ 3R cap) so it can
  never be a back-door size increase. **No `require_approval_for` trigger is touched.**
- **No BE-at-TP1 to interact with** — SFP has none. (A breakeven step is expressible as the special
  case `activation_r=+1R, atr_mult→∞` i.e. stop-to-entry; it is one grid point, not separate logic.)

## Live implementation dependency (must flag)

The native **SL-ratchet path is not built**: `BitunixBroker.modify_position_tp_sl_order` is a
`NotImplementedError` stub — *"Phase 1 is read-only. SL lifecycle decisions are emitted as…"*
(`brokers/bitunix.py:2229`). SFP today has **no** live mechanism to move a resting stop. Enabling a
real trail therefore requires, as a prerequisite, either (a) implementing `modify_position_tp_sl_order`
against the native `/tpsl/position/modify_order` endpoint, or (b) a cancel-and-replace of the SL leg
(carries OCO-race / orphan-stop risk — the reconciler `_halt_new_orders` path treats an unmatched
stop as an orphan). **This backtest evaluates whether the trail is worth building at all before any
of that wiring is scoped.**

## Config flag (default OFF, hot-reload, mirrors `maker_entry`)

```yaml
bitunix_sfp:
  pre_tp1_trail:
    enabled: false          # default OFF — behavior-preserving until deliberately enabled
    activation_r: 1.0        # arm after +Xr favorable progress (0 = immediate)
    atr_mult: 2.0            # trail distance = atr_mult · ATR(atr_len) behind the extreme
    atr_len: 14
```
Hot-read per signal like the other SFP gates (no restart). **No validation on hot-reload** (CLAUDE.md
sharp edge) — a typo silently disables; watch the audit log.

## Failure modes

1. **Tight-stop whipsaw (the null hypothesis).** Early stop-ratchet converts would-be winners into
   small stop-outs. The 2026-06-26 tight-stop arc found tighter geometry *worse* for BTC and "would
   DESTROY the live edge"; the already-run breakeven-trail is **net −0.042R pooled** (rescue +191R <
   winner-tax −216R). The trail distance is the whole ball game.
2. **Runner-coin damage.** ETH carries the construct (+0.397R flat) via the right tail; the
   breakeven-trail cut ETH to +0.203R. Any trail that caps the tail disproportionately taxes the
   coin that pays for the strategy.
3. **Fee asymmetry.** The 3R TP fills **maker** (0.00014/side); a trail stop-out fills **taker**
   (0.0004/side). More trail exits shift exit fees maker→taker and, on a ~0.3–1% R geometry, the
   fee-drag-in-R is material — net-R can fall even when gross rescue looks positive.
4. **Unbuilt SL-modify path (above)** — plus OCO-race / orphan-stop risk if implemented via
   cancel-replace.
5. **Intrabar ambiguity.** On a bar that touches both the trail stop and a higher level, stop-first
   (worst-case) vs tp-first changes the booked R. The backtest reports both; honest fills use
   stop-first.
6. **Regime whipsaw.** ATR expansion/contraction and range→trend transitions make a fixed `atr_mult`
   too tight in some regimes, too loose in others — hence the per-coin × regime breakdown.

## Null hypothesis + overturn bar

**H0 (operator-set):** early stop-ratcheting destroys the BOS wide-stop edge; **no** trail config
beats flat-3R on net-R. To overturn H0 for a cell, the backtest must show, on the current live
construct: **net-of-fee avgR AND total-R/yr strictly above the flat-3R baseline**, **surviving a
holdout split** (not IS-only), with the improvement **larger than its clustered SE**, and not
carried by a single coin/regime. "No configuration survives" is the expected, evidence-closing
result and is an acceptable deliverable.
