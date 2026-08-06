> **⚠ SUPERSEDED (2026-08-06 mid-session).** The "zero opening bid / opening rotation"
> root cause below is **DISPROVEN** by a live 15:17 ET reproduction — see
> `ROOT_CAUSE_2_MIDSESSION.md`. The real mechanism is a **10% spread gate rejecting the
> on-target δ chain → far-OTM low-bid strike substitution**, against a now real-premium
> ($0.75) buyback, evaluated on a bid-vs-mark mismatched-timestamp basis. The selected
> strike's bid is a healthy $0.75, not zero. Kept for history.

# PMCC roll false "net debit" block on RIOT — root cause (READ-ONLY)

- **Date:** 2026-08-06, event ~09:55 EDT (opening rotation)
- **Symbol:** RIOT
- **Code investigated:** `prod-live` @ `ef613e5` (the running engine, PID 573018 — the
  2026-08-04 volume-floor fix). Investigation branch `claude-2026-08-06` re-based onto
  `prod-live` after confirming the credit-gate code differs by +1046 lines from stale
  July-25 `main`.
- **Nothing placed. No prod contact. No state mutated.** Only local git reads + a
  `git reset --hard prod-live` on my own empty investigation branch.

## Ground truth (operator's manual fill — proves it's a CREDIT)
- Buy-to-close RIOT **$25 C 8/7** (1 DTE, deep-OTM near-worthless): 4 @ **$0.04**
- Sell-to-open RIOT **$23.5 C 8/14**: 4 @ **$0.7875**
- Net ≈ **+$0.7475/share credit** (limit $0.74, filled instantly)
- Engine recommendation: ROLL SHORT, target **δ0.35 / 7 DTE, new short ~$22–23** — but
  it reported a **NET DEBIT and blocked**.

---

## 1. The gate, the formula, and where it reads (the crux)

**Gate location:** `_propose_roll_short` → B2 credit gate,
`trading_corp/agents/divisions/pmcc_robinhood.py:3861-3878`:

```python
conservative_net, mark_net, open_bid = _short_roll_credit(new_weekly, close_mark)
if conservative_net < 0 and override_kind != "net_debit_justified":
    ... reason="net_debit_roll" ...   # → returns [] (proposes nothing)
```

**The formula** (`_short_roll_credit`, `pmcc_robinhood.py:437-457`):

```python
open_bid = new_weekly.get("bid")
open_credit_conservative = open_bid if open_bid is not None else (mark_price or 0.0)
conservative_net = open_credit_conservative - close_mark   # <-- the gated number
mark_net         = (mark_price or bid or 0.0)  - close_mark # <-- computed but NOT gated
```

So the gate blocks iff:

> **new short's BID  −  old short's MARK  <  0**   ⟺   **new_short.bid < close_mark (~$0.04)**

**Pricing basis = worst-case NATURAL on the sell side (BID), MARK on the buy side.**
It is **not** mid, and **not** a marketable/give_up estimate. `mark_net` (the mid-based
number ≈ +$0.75) is computed right next to it but only written to the audit payload — it
does **not** gate.

**Operator-facing string** (`last_roll_abort_reason`, `pmcc_robinhood.py:1028-1029`):
`reason == "net_debit_roll"` → `"roll would be a net debit — blocked (rolls must be for
credit)"`, surfaced on the card via `pmcc_pricing.py:153` / `routes.py:1012`.

## 2. Why the gate saw a debit — the near-ATM strike had a ZERO opening bid

For a genuine near-ATM RIOT call worth ~$0.79 mid, the only way `bid < $0.04` is a
**bid of ~$0.00 — an un-posted resting bid during opening rotation** (MMs haven't put up
a two-sided market yet at 09:55). With `open_bid = 0.00`:

- `conservative_net = 0.00 − 0.04 = −0.04  < 0`  → **BLOCK** (`net_debit_roll`)
- `mark_net        = 0.79 − 0.04 = +0.75`        → the real credit (matches the fill)

A **bid of exactly 0** is also the only sub-$0.04 value that survives the liquidity gate
(see §3), which is why a strike this "unpriced" was still selected and then gated.

> The exact 09:55 figures (`open_bid`, `close_mark`, `mark_net`) are in the
> `pmcc_roll_aborted` audit row IF the abort came from a real scan/dispatch. If it was a
> card **preview** render, `_audit_roll_abort(preview=True)` writes **no** row (only the
> in-process `_last_roll_abort` stash, gone on restart) — `pmcc_robinhood.py:5045-5050`.
> **Recommend operator pull it** (actor=`robinhood_pmcc`, kind=`pmcc_roll_aborted`,
> symbol=RIOT, ~09:55Z) to confirm `open_bid≈0.00`, `mark_net≈+0.75`.

## 3. Strike-selection trace — the picker did NOT fall to a thin far strike

- `_find_best_weekly` (`pmcc_robinhood.py:3996`): with `target_dte=7`, window is
  `dte_lo=max(3,0)=3 … dte_hi=21` and B7 roll-out (`_days_to(d) > 1`). It takes
  `weekly_dates[0]` (earliest qualifying expiry), fetches calls, runs `_filter_liquid`.
- `_select_weekly_strike` (`pmcc_robinhood.py:460`) with `target_delta=0.35`, no
  `target_strike`: picks the OTM (δ<0.40) strike with delta closest to 0.35 → the
  **near-ATM ~$22–23** strike the engine reported.
- **Liquidity gate does NOT filter a zero-bid strike** (`_passes_liquidity`,
  `pmcc_robinhood.py:814-844`): liveness passes on OI≥100; the spread check is
  `if bid > 0 and ask > 0:` — **when `bid == 0` the spread check is SKIPPED**, and
  `ask > 0` so "no ask" doesn't fire → the strike **passes** and is selectable.

So the picker correctly landed on the intended near-ATM strike; that strike simply had a
**0.00 resting bid** at the open. (Note: a *positive* sub-$0.04 bid against a normal ask
would blow the 10% spread gate and be filtered — leaving `no_liquid_weekly_contracts`, a
*different* abort. The operator got `net_debit_roll`, so a strike WAS selected → its bid
was **exactly 0**.) **Strike selection is not the bug; the zero-bid credit basis is.**

## 4. Sign / leg-assignment check — BOTH paths are correct (no sign-flip)

- **Gate path** `_short_roll_credit`: `conservative_net = new_short.bid − old_short.mark`
  = (credit received selling the new weekly) − (cost to buy back the old short).
  Positive = credit. **Sign correct.** `open_bid` is unambiguously the sell leg; the
  buy leg is `close_mark` passed from `leg.short_leg_mark`. **No index/identity swap.**
- **Estimate/roll-card path** `estimate_roll_from_quotes`
  (`_pmcc_combo.py:216-275`): legs matched by **identity** (`o.side == "buy"` /
  `"sell"`), `debit = buy.ask`, `credit = sell.bid`,
  `net = credit·r_sell − debit·r_buy`, `direction = "credit" if net≥0 else "debit"`.
  **Sign correct.** (This is the precedent `net_actual` sign-flip's fix — matching legs
  by option identity — and it holds here.)

**Verdict on §4: no sign or leg-attribution bug.** The estimate path is provably
sign-correct. Note it *also* uses natural `Σbid(sell)−Σask(buy)` — so it too would show a
debit on a zero opening bid — but it never ran here (B2 blocked upstream and returned []).

## 5. VERDICT — MIS-COMPUTED, not a real debit

The debit is **spurious**. It is a **natural-vs-marketable / thin-opening-quote
artifact**, not a genuine debit and not a sign bug:

- The gate scores the credit on the sell leg's **posted BID**, which at 09:55 opening
  rotation was **~$0.00** (no resting bid yet).
- The **marketable price** (mid ≈ $0.79) was a clean credit — proven by the operator's
  instant fill at limit **$0.74**, and by the engine's own **`mark_net ≈ +$0.75`** which
  it computed but did not gate on.
- The picker behaved correctly; the liquidity gate let a zero-bid strike through; the
  credit gate then read that zero bid as a hard debit.

**Why the engine "did not find" the readily-available credit:** it was looking at the
wrong number — the un-posted opening bid instead of the mid/marketable price the roll
actually fills at.

### The load-bearing inconsistency (proposal-time vs dispatch-time)
The **dispatch** repricer already knows a zero sell-leg bid is garbage and **HOLDs**
(does not treat it as a debit): `reprice_combo_from_quotes` `min_sell_bid` guard,
`_pmcc_combo.py:169-171` ("a 0-bid sell leg is garbage", `pmcc_robinhood.py:4281`). The
**proposal-time B2 gate has no such guard** and converts the same zero bid into a
"violates the credit rule" hard block. The two paths disagree about what a zero opening
bid means — that disagreement is the bug surface.

---

## Recommended fix (REPORT ONLY — not implemented)

Keep the "rolls must be for credit" rule. Only stop a **zero/thin un-posted opening bid**
from masquerading as a debit. Preferred: make the B2 gate consistent with the dispatch
guard.

1. **Make B2 zero-bid-aware (surgical, preferred).** In `_short_roll_credit` / the B2
   gate, when the new short's `open_bid` is `None` **or** `≤ reprice_min_sell_bid`
   (i.e. un-posted), the credit is **indeterminate — not a debit**. Options:
   - (a) evaluate the gate on `mark_net` (mid basis) in that case — the number already
     computed and already what the card and the fill reflect; **or**
   - (b) abort with a **distinct, non-scary reason** ("cannot price roll — sell-leg bid
     not yet posted at the open; retry after rotation") instead of `net_debit_roll`, so
     the operator isn't told a credit roll is a debit.

   This mirrors the existing `min_sell_bid` HOLD at dispatch (`_pmcc_combo.py:169`), so
   proposal-time and dispatch-time finally agree on "zero bid = unpriceable, not a debit."

2. **Prefer a marketable/mid basis for the credit determination** (with the existing
   `give_up` shave), rather than worst-case natural, so genuinely-fillable credit rolls
   aren't false-blocked by a wide/one-sided opening quote. Guard against an over-optimistic
   stale mark by requiring a two-sided quote (both bid>0 and ask>0) before trusting mid;
   otherwise defer per (1).

3. **Optional — picker hygiene:** during opening rotation, have `_select_weekly_strike`
   prefer a two-sided quote (bid>0) so it doesn't anchor the roll on an unpriceable
   zero-bid strike in the first place.

**Do NOT** relax the credit rule itself — a real debit (mid/marketable still negative)
must still block (overridable via `net_debit_justified` as today). Also worth confirming
in the audit row that `close_mark` (~$0.04) wasn't itself stale-high on the deep-OTM
1-DTE short, since it is the other input to `conservative_net`.

## Confirmation
- **Nothing placed. `auto_execute` / halt untouched (no prod contact). No prod state
  mutated.** Read-only code investigation against `prod-live @ ef613e5`.
- Open follow-up for operator: pull the `pmcc_roll_aborted` RIOT audit row (if not a
  preview) to attach the exact `open_bid` / `close_mark` / `mark_net` numbers.
- **STOP for review** before any code change.
