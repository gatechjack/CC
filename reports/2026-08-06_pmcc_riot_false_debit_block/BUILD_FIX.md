# PMCC pricing/selection fix — BUILD (no deploy) — 2026-08-06

Branch `claude-2026-08-06` (base = current `origin/prod-live` `ef613e5`, byte-identical
runtime before this build — verified `git merge-base --is-ancestor` + 0 file diff).
**BUILD ONLY. Nothing placed. No prod touch. auto_execute:false + halt untouched.**
All PMCC tests green (154 + 27 new/updated). STOP for byte-by-byte review.

One runtime file changed: `trading_corp/agents/divisions/pmcc_robinhood.py`
(+3 fixes). Tests: `tests/test_pmcc_spread_selection_fix.py` (new),
`tests/test_pmcc_liquidity_pricing_fix.py` (1 test updated + 2 added).

---

## FIX 1 — spread gate is liquidity-aware; no silent substitution  [`_passes_liquidity` + `_classify_liquidity_reason`]

Byte-review focus. The raw bid/ask **spread-WIDTH** rejection (`spread > 10% of mid`)
is **removed**. What replaces it:

- **KEEP Liveness** (OI ≥ 100 OR vol ≥ 500) — unchanged.
- **KEEP a two-sided tradeability floor**, now explicit: reject `no bid`, `no ask`, or
  `inverted (bid > ask)`. **This is stricter than before in one way that matters:** a
  **bid == 0** strike used to PASS (the old width check was `if bid>0 and ask>0` → skipped
  when bid≤0), which is the original zero-opening-bid hole. It now **fails** (`no bid`).
- **A wide-but-two-sided strike now PASSES** (operator fills at mid). RIOT's on-target
  $24 8/21 (21.6% spread, OI 6475) passes; before, the whole δ0.33–0.39 chain was rejected.
- `_classify_liquidity_reason` gains `no_bid`/`inverted` buckets; `spread`/`volume` buckets
  kept for back-compat (no longer emitted).

No silent substitution: with the on-target chain now liquid, `_select_weekly_strike`
(Fix 3) stays on target. If the WHOLE chain is genuinely untradeable, `_filter_liquid`
returns `[]` → `_find_best_weekly` → None → the caller aborts with the **honest sparse-chain
reason**, never a far-OTM substitution and never a misleading "net debit".

## FIX 3 — nearest-delta clamp; never abort  [`_select_weekly_strike` tail]

The hard `delta < 0.40` OTM cutoff could EXCLUDE the strike actually nearest the target
(a δ0.47 next to a δ0.16), forcing a far-OTM low-delta pick or, on an empty band, a poor
substitution. Now: prefer the nearest OTM strike, **but if the nearest strike overall is
strictly closer to target than the best OTM candidate, take it** (clamp). Preserves
"avoid ITM" on ties (existing `test_select_weekly_avoids_itm` still green). Covers the
OPEN open_short coarse-spacing case (band 0.30–0.45 empty → clamps to $3.5 δ0.469).

## FIX 2 — credit gate on a same-timestamp MID net  [B2 gate + new `_fresh_leg_mark`]

- New helper `_fresh_leg_mark` fetches a **fresh build-time buyback mid** (same snapshot
  as the new-weekly quote). FAIL-SAFE: any missing quote/error → falls back to the scan
  mark (source recorded in the audit).
- Gate now blocks iff **`mid_net < 0`** where `mid_net = new_weekly.mark − fresh_buyback.mark`
  — not the old `new.bid − stale-scan.mark`. `net_debit_justified` override preserved.
- The combo `combo_direction`/`net_limit_price` (Phase-A atomic tag) now reuse `mid_net`.
- **Decision to confirm:** the gate is `mid_net ≥ 0` (no `give_up` subtracted). Rationale:
  the operator fills at mid; the **hard** credit backstop stays at DISPATCH (the live
  reprice / net-drift guard, unchanged). A genuine mid-to-mid debit still blocks
  (test `test_e2e_riot_genuine_debit_blocks_with_high_buyback`).

---

## Reproduction re-run (real patched code, live 15:17–15:50 ET fixtures)
`repro_live_postfix.py`:
```
RIOT roll: current short $23.5 8/14 (buyback mid 0.75) -> roll-out 8/21, delta 0.35
  liquid strikes (Fix 1): [21.5..26.0]         # on-target chain now liquid
  SELECTED: C24.0 (delta 0.355, bid 0.91, mark 1.02)   -> NO substitution (on-target $24)
  gate MID net = 1.02 - 0.75 = +0.2700 -> CREDIT (clears)   (conservative +0.1600)

OPEN open_short: uncovered LEAP, band 0.30-0.45 (spot 3.445)
  liquid strikes (Fix 1; $5.0 no-bid + $2.5 thin dropped): [3.0, 3.5, 4.0, 4.5]
  SELECTED: C3.5 (delta 0.469, bid 0.12) -> SELLS for the mid credit   -> clamped to nearest $3.5 (Fix 3)
```

## Tests (all green; `-p no:pytest_ethereum`)
- `tests/test_pmcc_spread_selection_fix.py` (new, live fixtures): RIOT on-target passes +
  no substitution + mid credit; OPEN empty-band clamp sells; all-untradeable → `[]` (no
  substitution); no-bid/no-ask/zero-OI rejected; genuine mid debit still blocks; **e2e
  `_propose_roll_short` builds a 2-leg credit roll on $24 (no substitution)**; e2e high
  buyback → genuine debit still blocks.
- `tests/test_pmcc_liquidity_pricing_fix.py`: `test_wide_spread_still_fails` → **rewritten**
  to `test_wide_spread_now_passes_two_sided_liquid` (intended behavior change) + added
  `test_no_bid_now_fails` / `test_inverted_market_fails`. Rest unchanged.
- Full PMCC suite (154) green — no regression. (3 pre-existing research-module collection
  errors are unrelated — `FakeMacroExpert` import; I touched no research files.)

## Confirmations
- **Liquidity floor preserved** — no bid / no ask / inverted / thin (OI+vol) all still rejected.
- **Dispatch unchanged** — `_pmcc_combo.py` NOT touched; `reprice_max_spread_pct` (0.60) and
  the net-drift consent guard still enforce credit on the live reprice.
- **Branched off current prod-live**; **auto_execute:false + halt untouched; nothing placed; no deploy.**

## Scope boundaries flagged for your call (NOT changed)
1. **roll_leap credit gates** (`pmcc_robinhood.py:1462`, `:2586`) still use the bid-based
   `rl_cons_net < 0` (they benefit from Fix 1 selection but not Fix 2's mid basis). Left as-is
   to keep the diff to the confirmed roll_short path — say the word to extend Fix 2 there.
2. **Fix 1 also loosens the LEAP / open-PMCC / scout selection** (shared `_passes_liquidity`).
   Intended (two-sided + OI floor still applies); flagged for awareness.
3. **`give_up` in the gate** — not subtracted (see Fix 2 decision above). Confirm or ask me to
   gate on `mid_net − give_up`.
