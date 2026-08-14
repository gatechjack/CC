# PMCC "best-price roll" fix (FIX 1-4) — BUILD deliverable (2026-08-07)

BUILD ONLY. Branch `claude-2026-08-07b` off `prod-live ee04747` (== origin/prod-live,
verified this session). NOTHING deployed; no prod touch; auto_execute:false + halt
untouched; nothing placed. STOP for byte-by-byte review.

## Commits (this build)
- `702d02e` pmcc(fix1/2/3): mid−give_up gate basis + best-net roll selection + advisory credit rule (division)
- `83afc2c` pmcc(fix1/3): dispatch reprice + card estimate on MID net; consent guard aborts worse-than-approved DEBIT
- `e3fbff7` pmcc(fix4): bind EXPERT ANALYSIS panel to the open row on ANY open (not only summary click)
- `c305573` test(pmcc): update roll tests to MID basis + advisory credit rule; add best-price-roll proofs
- `d84b630` test(pmcc/fix3b): prove retained autonomous 5%-of-LEAP debit cap rejects/allows on source=auto

## Files changed (source)
| file | what |
|---|---|
| `trading_corp/agents/divisions/pmcc_robinhood.py` | FIX 2 `_select_best_net_weekly` + `_mid_of` helpers; 3 config props; `_find_best_weekly(close_buyback=)`; `_propose_roll_short` reordered (buyback before selection), gate → MID−give_up + ADVISORY (no hard block), combo-tag → MID basis |
| `trading_corp/agents/strategies/_pmcc_combo.py` | FIX 1 `reprice_combo_from_quotes` MID net (+ natural retained); `estimate_roll_from_quotes` MID net; `_mid_of_quote`; FIX 3c consent guard aborts a DEBIT repriced WORSE than approved |
| `trading_corp/web/static/js/pair_list.js` | FIX 4 panel bound to the open row on ANY open (toggle-driven fetch + header, deduped vs click) |

## FIX 1 — MID − give_up basis in all four price sites (can't drift)
1. **Gate** (`_propose_roll_short`): `mid_dispatch_net = mid_net − give_up` (mid_net = new.mark − old.mark_fresh). `gates["credit"] = "credit" if >=0 else "debit"`. `natural` retained for audit/HOLD only.
2. **Combo tag**: `_combo_direction = "credit" if mid_dispatch_net>=0 else "debit"`; `_combo_net = round(abs(mid_dispatch_net),2)`.
3. **Dispatch reprice** (`reprice_combo_from_quotes`): `net = Σ mid(sell) − Σ mid(buy)` (mid = mark_price else (bid+ask)/2); then the existing give_up + tick logic. `natural` computed + logged as the sanity reference; the stale/wide-quote HOLD guard is unchanged.
4. **Card estimate** (`estimate_roll_from_quotes`): headline `net` = MID net (so displayed == approved == dispatched); debit/credit line items kept as the worst-case ask/bid bounds.
- SAFETY: the placed order is a net-LIMIT — a credit tag fills only at that credit-or-better, a debit tag only at that debit-or-better (broker-enforced). Fill-rate (a marginal credit limit may rest) is the only tradeoff — never a worse-than-approved fill.

## FIX 2 — best-net strike selection near the δ target
`_select_best_net_weekly(calls, close_buyback, target_delta=0.35, band_low=0.28, band_high=0.42)`:
among liquid candidates with δ in [0.28,0.42], take the MAX MID net (candidate.mark −
close_buyback); ties → nearest δ to 0.35. Falls back to nearest-δ when `close_buyback`
is None (OPEN path) or the window is empty (never abort). Wired via
`_find_best_weekly(close_buyback=close_mark_fresh)` on the ROLL path only; a prescribed
`target_strike` (halfway-roll rule) and the OPEN path keep the old nearest-δ picker.
- LIVE-verified outcomes: **TSLA → $330** (mid +1.575) not $335 (+0.025); **SMR → $10.50**
  (the only in-window strike) not the thin $11.00 (δ0.258, mid −0.095). RIOT fixture now
  picks $23.5 (best in-window net) instead of $24.0 — same anti-substitution property.
- ★ REVIEW KNOB (flagged): best-net effectively picks the RICHEST (lowest) strike inside
  the δ window, so the window's UPPER bound (0.42) governs how far toward ATM it reaches.
  [0.28,0.42] is the unique band that yields BOTH your required outcomes on the live chain
  (includes $330 δ0.401, excludes SMR $10.0 δ0.446 and $11.0 δ0.258). All three bounds are
  config-tunable: `short_leg.roll_target_delta` / `roll_best_net_delta_low` / `_high`.

## FIX 3 — credit rule ADVISORY; always present the best roll (debit allowed for HITL)
- The hard `net_debit_roll` abort is REMOVED. Every roll BUILDS + is presented; the card
  labels it CREDIT/DEBIT via the (now-mid) estimate. `pmcc_roll_net` / `pmcc_roll_gates`
  audits record direction + nets.
- (b) Bounded-debit CAP is RETAINED on the AUTONOMOUS path ONLY: ceo_graph
  `_check_auto_execute` (UNCHANGED) — `rolling_for_debit_above_5_pct_of_long` (5% of cached
  LEAP value) + `max_roll_debit_dollars` (500). `_ROLL_DEBIT_ACTIONS` includes
  `roll_short_call_close`, so a debit roll's buy leg reaches it. Config already wired;
  dormant while `auto_execute:false`. The board path (`execute_pair_orders`, source='board')
  does NOT call it → presents any debit. Subsumes the ITM/0-DTE assignment-escape case.
  - ★ FLAG (pre-existing, NOT changed): the 5% gate keys off the buy leg's `limit_price`
    (the buy-back cost), not the combo NET debit. For a precise net-debit cap when
    auto_execute eventually flips, key it off the combo `net_limit_price` when
    combo_direction=="debit". Left for your call (ceo_graph is outside the stated
    touch-points; dormant now).
- (c) Consent integrity for DEBIT rolls: `assess_combo_reprice_consent` now aborts a debit
  that reprices WORSE than approved (`snap debit, cur debit, cur_net − snap_net > tol`).
  Credit→debit sign-flip + credit-collapse guards unchanged; debit→credit / smaller-debit
  are allowed (better). Fingerprint (`pmcc_preview.fingerprint`) is price-independent →
  holds for debit rolls too.
- LEAP guard unaffected: rolls are short-leg-only; no LEAP order is constructed.

## FIX 4 — UI panel desync (pair_list.js, client-only)
The `<details>` `toggle` event fires on ANY open (click / keyboard / programmatic). The
handler now calls `showLoading(symbol)` (header + body) AND fires the HTMX fetch
(`htmx.ajax` to `#pair-analysis` using the summary's `hx-get`), deduped against the click
path via a `_pmccClickFetch` flag so a pointer click doesn't double-fetch. Result: the
EXPERT ANALYSIS panel (symbol header + body) always reflects the open row — it can no
longer show a different asset's analysis. No template/route change needed. (Optional
belt-and-suspenders server OOB header stamp noted but not built.)

## TESTS — all green (excl. one pre-existing env failure)
- Full PMCC suite (all `test_pmcc*.py` except the env test, + `pmcc_regression`): **343 passed**.
- Updated 9 existing basis tests (natural→MID) + 3 e2e policy tests (block→build/present);
  renamed the misleading "still_blocks" helper test. NEW file `test_pmcc_best_price_roll.py`
  (15 tests): TSLA→$330 / SMR→$10.5 best-net; OPEN + empty-window fallbacks; SMR mid-credit
  BUILDS as credit; TSLA builds credit on $330; TSLA deep-OTM DEBIT builds labeled;
  reprice credit→credit-limit / debit→bounded-debit-limit; consent debit-worse ABORT /
  debit-better ALLOW / debit→credit ALLOW / credit→debit still ABORT; autonomous cap
  reject>5% / allow<5%.
- ZERO-REGRESSION proof: the ONLY existing-suite change is (a) the 12 tests that encoded
  the superseded natural-basis / hard-block behavior, updated to the new policy, and (b)
  new tests. The one failing file `test_pmcc_paper_run_readiness.py` fails IDENTICALLY on
  base `ee04747` (verified in a throwaway worktree) — 2 blocking `no such table`
  (agent_state, audit_event) = local DB not migrated; environmental, not this diff.

## Confirmations
- Dispatched net is a bounded net-LIMIT (credit-or-better, or debit-or-better) — never
  fills worse than approved; consent guard aborts adverse reprice in BOTH directions.
- LEAP guard intact (rolls short-leg-only). Branched off current prod-live. auto_execute:false
  + halt untouched. Nothing placed. No deploy. ceo_graph NOT modified.
