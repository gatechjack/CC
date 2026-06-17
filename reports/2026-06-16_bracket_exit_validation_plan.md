# Bracket-exit controlled-live validation plan (THE API-path OCO probe)

Operator-run + operator-watched; **agent read-only observes** (82fda13). This is
the proof the bot's API order path inherits the manual-UI native-OCO / SL-auto-
reduce behaviour — and the **first-ever live `exit_kind=tp` booking** (the bot
finally managing a trade to profit). NOT an autonomous agent run.

Build under test: branch `bitunix-bracket-exit-build-2026-06-16` (off the
#3+#5-B/C branch). **Not deployed** — deploy is a separate operator step.

## What the build does (recap)
At a live entry fill: SL stays the **atomic attached market-stop** (unchanged);
the bot then **rests TP1/2/3 as reduce-only LIMIT orders** (split by the tp_plan
fractions, Board min-leg 0.0003 BTC with graceful degradation). It relies on
**native OCO** (does not cancel counter-orders) and, on a TP fill detected by the
60s position poll, **moves the SL price only** (venue auto-reduces SL qty) per the
(b)+(c) hybrid (TP1→breakeven, TP1+TP2→TP1). All exit actions ride the #5-B/C
reduce-only exemptions.

## ⚠ Size needed (flag)
The 0.25/0.50/0.25 fractions make the **0.25 legs the binding constraint**: 3 full
legs each ≥ 0.0003 BTC need **entry qty ≥ 0.0012 BTC** (~$79 at ~$66k) — larger
than the ~$31 test size, and larger than the "~0.0009" in the scope (that assumed
equal thirds). Options:
- **Full 3-leg probe:** size the entry to **≥ 0.0012 BTC** (the build will place 3 legs).
- **Smaller probe (degraded, still valid):** ≥ 0.0006 BTC → 2 legs (tp1+tp3); ≥ 0.0003 → 1 leg (tp1). A 2-leg probe still exercises rest + a TP fill + OCO + SL-move; only the 3-way split is unproven. **Recommend the full 3-leg (≥0.0012 BTC) probe** so the complete ladder + OCO is confirmed in one trade.

## Pre-flight (agent read-only)
1. Engine healthy, flat, not halted; `execution_mode: live`; the build is deployed
   to prod (separate step — confirm the 4 changed files' md5 if/when deployed).
2. Confirm the entry will size ≥ 0.0012 BTC (per division config / the signal).

## The trade (operator executes; agent observes each step read-only)
1. **Entry fills** (a normal live signal, or an operator-initiated test entry sized
   ≥0.0012). The bot registers it (lock-resilient, #3) and auto-places the bracket.
   - **OBSERVE:** `bracket_placed` audit with 3 `tp_order_ids`; the SL attached at
     entry (slPrice, as today). Journal: 3 "resting reduce-only LIMIT placed" lines.
2. **The TP limits rest on the book.**
   - **OBSERVE (OCO Q1):** `get_pending_orders` (or the BitUnix UI) shows 3 resting
     reduce-only TP limits + the attached SL, coexisting — **no 30038
     TPSL_EXCEEDS_POSITION** rejection. (Confirms 3 legs + SL coexist via the API.)
3. **Price reaches TP1 → the TP1 limit fills (maker, at-price).**
   - **OBSERVE (the proof):** the TP1 reduce-only LIMIT books a **maker fill at the
     TP1 price** — the **first-ever live partial profit-take**.
   - **OBSERVE (OCO Q2 — SL auto-reduce):** the attached SL's qty **auto-reduces** to
     the remaining position (the bot does NOT touch SL qty). Confirm via UI/position.
4. **The 60s poll detects the fill → moves the SL price to breakeven.**
   - **OBSERVE (Q3 — modify works):** a `position_sl_update {moved: true, source:
     "bracket_sl_move", new_sl: <entry>}` audit; the SL price is now breakeven, qty
     still auto-tracked. (If `moved: false` repeatedly → the modify endpoint shape
     is wrong; the SL stays at its prior protective price — failure-tolerant — and
     this is the one thing to fix before relying on the move.)
5. **Continue to TP2/TP3 (or SL).**
   - TP2 fills → SL moves to TP1 (another `position_sl_update`).
   - Final close (last TP or the SL) → **OBSERVE (OCO Q4):** native OCO **cancels the
     remaining resting orders** — `get_pending_orders` shows **no stale SL/TP
     lingering** after flat. (The bot does NOT cancel — the venue does.)
6. **Flat + clean.**
   - **OBSERVE:** reconciler clean (no orphan), no stale orders, position booked.

## The four open questions this answers (the API-path OCO probe)
| # | Question | Observed at |
|---|---|---|
| Q1 | Do 3 reduce-only LIMIT TPs + the attached SL coexist (no 30038)? | step 2 |
| Q2 | Does the attached SL **auto-reduce** qty as a TP fills? | step 3 |
| Q3 | Does `modify_position_sl` (the price-move) work via the API? | step 4 |
| Q4 | Does native OCO **cancel** the rest on the final close (no stale)? | step 5 |

If all four hold, the bot's API path inherits the manual-UI behaviour and the
bracket exit is **validated** — the bot can manage a trade to profit autonomously.

## If something fails (failure-tolerant by design)
- A TP leg fails to rest → the SL still protects; `bracket_tp_leg_failed` audit.
- The SL-move modify fails → SL stays at its prior valid price; retries each tick;
  `position_sl_update {moved:false}` surfaces it. Fix the modify body, redeploy.
- Native OCO does NOT auto-cancel (Q4 fails) → stale orders linger →
  `get_pending_orders` shows them; the operator cancels manually and we add an
  explicit bot-cancel (an operator decision, since the current build relies on
  native OCO per the hard-stop).
- Worst case at any step: the **atomic attached SL** still guards catastrophic
  downside (unchanged from today).

## Hard line
SL stays guaranteed-fill MARKET (only its PRICE moves). Bot never sets SL qty
(venue auto-reduces). Bot never cancels OCO counter-orders (verify only). Agent
makes NO signed/live calls — operator executes, agent observes read-only.
