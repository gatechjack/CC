# PMCC "net debit — blocked" the morning after the credit-fix — findings + fix rec

Date: 2026-08-07 (~10:03-10:09 ET). READ-ONLY. Verbatim-prod functions
(pmcc_robinhood.py LF-md5 `2a390124`, _pmcc_combo.py `cf5a8f1c`) on live RH quotes.
Nothing placed; no config/prod change; auto_execute + halt untouched. Account 461391328.
Raw data + per-strike ladders: `01_empirical_nets.md`.

## 1. Per-name nets (worst-case vs mid vs dispatch) + classification

give_up = $0.02. Gate blocks when `dispatch_net = new.bid - old.ask - give_up < 0`
(unless override net_debit_justified). "mid" = new.mark - old.mark (operator's fill).
"dispatch bar" (natural) = new.bid - old.ask (pre-give_up).

### SMR  — current short $10.00C 08-14 -> tile-selected new $10.50C 08-21 (δ0.35)
- natural (dispatch bar) = 0.34 - 0.37 = **-0.03**
- dispatch_net (gate)    = -0.03 - 0.02 = **-0.05  -> BLOCKED "net_debit_roll"**
- mid_net (operator)     = 0.365 - 0.345 = **+0.02  (credit)**
- **CLASS: MID-CREDIT-WRONGLY-BLOCKED.** Fills +$2/ct at mid; gate rejects on the
  worst-case bid/ask/give_up basis. (Tile's earlier "+$0.14" = same structure, earlier
  snapshot; intraday drift shrank it to +$0.02, still a credit at mid.)
- Reproduces the reported bug LIVE. YES.

### TSLA — current short $322.50C 08-07 (0-DTE, ITM) -> picker δ0.30 new $335C 08-14
- natural = 3.55 - 3.65 = **-0.10**
- dispatch_net = -0.10 - 0.02 = **-0.12  -> BLOCKED "net_debit_roll"**
- mid_net = 3.600 - 3.575 = **+0.025 (credit, marginal)**
- **CLASS: MID-CREDIT-WRONGLY-BLOCKED at the margin** — NOT the hypothesized clean
  GENUINE-DEBIT. Mid credit is negligible (+$2.5/ct) but non-negative, so it fits the
  wrongly-blocked bucket by definition (mid>=0, worst-case<0).
- Reproduces "blocked as net debit" LIVE. YES — but as a marginal-mid-credit, not a
  true debit at the δ target.

## 2. TSLA ITM specifics + credit-strike search (Task 3/4)
- Short $322.50C, spot 325.04 => intrinsic **+$2.54 (ITM)**, expires **TODAY (0-DTE)** =>
  assignment-likely if left ITM. Buying it back costs the ask **$3.65** (= $2.54 intrinsic
  + $1.11 time). That intrinsic in the buyback is what pushes an up-and-out roll (which
  sells a LOWER-premium higher strike) into a debit once the new strike clears ~$330.
- **A credit roll DOES exist for TSLA** (contra "all up-and-out rolls are debits"):
  - $330C (δ0.40) -> dispatch **+$1.43** credit (clears the gate). Best up-and-out credit.
  - $325C / $322.5C -> +$3.48 / +$4.73 (bigger, but keep it ATM/ITM => assignment risk).
  - The credit/debit boundary sits at δ0.30-0.40; everything δ<=0.23 ($340+) is a debit.
- **Genuine (bounded) debit only for true assignment-escape strikes** ($340+):
  $340 -$1.22, $345 -$1.99, $350 -$2.51. These move the strike far above spot to dodge
  0-DTE assignment.
- **8%-of-LEAP tolerance (Task 4).** Two distinct limits:
  - SKILL/LLM rule (pmcc_robinhood.py:170, L255): "Never roll for debit > **8% of LEAP
    value**", applied via the `net_debit_justified` override.
  - Deterministic auto-execute gate (ceo_graph.py:193): stricter **5% of LEAP value**;
    `debit_per_contract = limit_price×100` vs `leap_value = cached LEAP mark×100`. Fires
    ONLY when auto_execute:true (currently false -> HITL, so not in the live path now).
  - TSLA LEAP mark 49.975 => leap_value **$4,997.50/ct** => 8% = **$399.80/ct**, 5% =
    **$249.88/ct**. So rolling TSLA up-and-out to $340 (-$122/ct) or $345 (-$199/ct) is
    WITHIN the 8% skill tolerance (and $340 within 5% auto). The debit MAGNITUDE is fine;
    the deterministic B2 gate just blocks ANY debit unless net_debit_justified is set.
  - NOTE: the task's "~$131/contract" does not match TSLA ($400 at 8%) or SMR ($36 at 8%,
    LEAP mark 4.55). It appears to be a stale/other-name figure; real per-name numbers above.

## 3. Dispatch safety (Task 5) — is a mid-give_up basis safe? YES, if applied consistently
Placement (robinhood.py:1253): `order_option_spread(direction, net_limit, underlying, qty,
spread, timeInForce="gfd")` — an atomic **net-LIMIT** spread, all-or-nothing.
- **A `credit`-direction net-limit fills ONLY at that net credit or better — broker cannot
  fill it at a net debit.** This is the structural safety.
- Defense-in-depth: `assess_combo_reprice_consent` (_pmcc_combo.py:362,378) ABORTS a
  credit-approved roll if the dispatch reprice sign-flips to a debit or the credit collapses
  > tolerance. So a credit-approved roll either dispatches as a credit limit (never a debit
  fill) or is aborted. Confirmed.
- **CAVEAT (important):** today `reprice_combo_from_quotes` (L190-201) computes the limit on
  the **natural (Σbid(sell) - Σask(buy))** and adds give_up in the marketable direction, so a
  MARGINAL roll (natural slightly negative, e.g. SMR -0.03) is tagged **debit** and would
  place a small debit limit (SMR: |−0.03|+0.02 = **$0.05 debit**), which CAN fill at a small
  debit. That's harmless TODAY only because the gate blocks such rolls before dispatch. If we
  move ONLY the gate to mid (leaving the combo tag + reprice on the natural), the tag stays
  `debit`, the consent guard's credit-protections don't apply (snap_dir=debit), and it would
  place a small debit limit -> could fill at a small debit.
- **=> The mid-give_up change is SAFE iff applied to gate + combo-tag + dispatch reprice
  TOGETHER**, so the placed order is a `credit` net-limit at `max(tick, mid - give_up)`. Then
  it can only fill at a credit (or rest unfilled) — never a debit. The only cost is fill-rate:
  a credit limit at mid-give_up sits INSIDE the natural, so a truly marginal roll may rest and
  not fill (the "dispatch-basis-vs-mid-fill tension" already PARKED in memory). That is a
  fill-rate cost, not a risk. For SMR now: mid-give_up = 0.00 -> credit limit floored to $0.01;
  for TSLA $335: +0.005 -> ~$0.01 credit. Both dispatch as tiny credit limits (safe).

## 4. UI desync (Task 6) — EXPERT ANALYSIS panel vs expanded row: REAL, structural
(Sub-agent trace; cited file:line.)
- The panel is a SINGLE shared right-rail fragment: `#pair-analysis` body + a SEPARATE
  `#pair-analysis-symbol` header (division.html:996-1013). They are updated independently.
- Row expansion is a native `<details>` toggle (partials/pmcc_pair.html:26) — independent of
  the panel. The panel body updates only via the `<summary>`'s `hx-get=/division/{slug}/
  pair-analysis/{symbol}` on **click** (pmcc_pair.html:30-32); the header is set optimistically
  by JS `showLoading()` on click (pair_list.js:14-48).
- Server panel fragments (`_render_pair_analysis` routes.py:4522-4773; `_render_pmcc_record_
  panel` :4913-4980) return only the body — they emit **no OOB update** to `#pair-analysis-
  symbol`. So the header is never reconciled to the symbol the server actually rendered.
- **Mechanism of the observed desync (SMR row open, panel showing TSLA $322.50):** any way of
  opening a row that is NOT a pointer-click on `summary[data-symbol]` (keyboard Enter/Space,
  the single-open accordion programmatically toggling a row, or a click landing off the
  summary) opens the `<details>` WITHOUT firing the HTMX fetch or `showLoading()` — so the
  panel keeps the previously-loaded symbol's analysis (TSLA). The header/body are not bound to
  the expanded row.
- Minimal fix (do NOT implement here): (a) have the server panel fragment carry an
  `hx-swap-oob` span that stamps `#pair-analysis-symbol` with the symbol it was built for, so
  every body swap reconciles the header; and (b) trigger the fetch on the `<details>` `toggle`
  event (not just click) so any open path refreshes the panel. Belt-and-suspenders: blank the
  panel + show a spinner keyed to the opening symbol on toggle.

## 5. FIX RECOMMENDATION (no code changed; for review)

### (a) Move the roll credit basis to MID - give_up for proposal AND dispatch (consistent)
Root cause: the gate's worst-case basis (`new.bid - old.ask - give_up`) is ~one full
bid/ask spread + give_up more conservative than the MID the operator actually fills at. On
tight-but-real weeklies this blocks rolls that are credits at mid (SMR +$0.02, TSLA +$0.025)
and DO fill. Change, applied in ONE place each so they can't drift:
1. `_propose_roll_short` (pmcc_robinhood.py ~L3940-3966): gate on
   `mid_dispatch_net = mid_net - give_up` instead of `natural - give_up`
   (mid_net = new.mark - old.mark_fresh, already computed). Block iff `mid_dispatch_net < 0`.
2. Combo tag (~L4032): `_combo_direction`/`_combo_net` from `mid_dispatch_net`
   (credit iff >= 0), so the operator-approved snapshot matches.
3. `reprice_combo_from_quotes` (_pmcc_combo.py L190-201): reprice on the MID net
   (Σ mark(sell) - Σ mark(buy)) - give_up, tagged `credit` when >= 0, `net_limit =
   max(tick, that)`. Keep the natural as a SANITY FLOOR only (never place a limit richer
   than natural + give_up), and keep the stale/wide-quote HOLD guard.
Result: approve == fires; the dispatched order is a `credit` net-limit -> can only fill at a
credit or rest -> never a debit. Accept the parked fill-rate tension (a marginal credit limit
may rest); optionally add a small configurable "mid shave" so the limit is marketable while
staying >= 0. Regression guard: on a real credit roll (RIOT +$0.05 natural) the mid basis is
strictly >= natural, so nothing that clears today gets blocked.

### (b) ITM / near-expiry bounded-debit exception (assignment-risk carve-out)
Even on a mid basis, a 0-DTE ITM short can only be rolled far enough up-and-out to escape
assignment by paying a small debit (TSLA $340 -$1.22, $345 -$1.99). That is a RATIONAL,
bounded cost to avoid assignment on a $16k LEAP, and it is already inside the 8%-of-LEAP
skill tolerance. Recommend a DETERMINISTIC carve-out (not reliant on the LLM remembering to
set net_debit_justified):
- Trigger: short is ITM (spot > strike, intrinsic > 0) AND short DTE <= 1 (0/1-DTE assignment
  window).
- Allow a net debit up to `min(8%-skill, 5%-auto)` of cached LEAP value per contract
  (TSLA today: 5% = $249.88/ct), tagged `debit`, `net_limit = |mid_debit| (+ small give_up)`,
  and surface it explicitly on the card as "bounded assignment-escape debit $X (Y% of LEAP)".
- Keep the existing consent guard; a debit-approved snapshot dispatches as a bounded debit
  limit (fills at <= that debit), which is the intended behavior here.
- This is the ONLY place a debit fill is permitted; everywhere else remains credit-only.

## 6. Bottom line
- SMR: reproduces LIVE as **MID-CREDIT-WRONGLY-BLOCKED** (the reported bug). Root cause =
  worst-case (bid/ask/give_up) gate basis vs mid fills. Fix (a).
- TSLA: also blocked, but as a **marginal mid-credit at the δ0.30 target**, NOT a clean
  genuine-debit; a credit roll exists ($330/δ0.40 = +$1.43). The real TSLA problem is the
  0-DTE ITM assignment risk, which needs the bounded-debit escape — Fix (b).
- Dispatch is safe under a mid-give_up basis PROVIDED gate+tag+reprice move together so the
  placed order is a credit net-limit (broker fills credit-or-better only). Fill-rate, not
  debit risk, is the only trade-off.
- UI: the EXPERT ANALYSIS panel is genuinely not bound to the expanded row (shared fragment,
  optimistic header, no server OOB reconciliation) — it can show another asset's analysis.
