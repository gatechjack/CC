# Deploy-gated proposals — for operator ruling (2026-08-02)

Five items from the ops prompt. **Written proposals only — none implemented.** Each
is a strategy-parameter or gate change → per CLAUDE.md §4 + PROJECT_CONTEXT §11 needs
**Backtester approval** (and, where a `require_approval_for` trigger is touched, a Board
memo). Current values verified against the prod-live base (`dafe60b`) this worktree tracks;
prod itself was not read (SSH classifier-blocked) — confirm on the box before applying.

---

## 1. DD-cap re-tighten "to 0.99"

**Current (verified `config/risk.yaml:70-83`):** the per-account drawdown cap is ALREADY
`0.99` for BOTH bitunix divisions — `bitunix_futures: per_account_max_drawdown_pct: 0.99`
and `bitunix_sfp: 0.99` — TEMP relaxations from the global `0.15`, added for the tiny live
balances (~$265 futures / ~$653 SFP) where 15% self-halts on one real loss. Comment: "REVERT
when funded ~$15k."

**⚠ Wording flag:** 0.99 is already live, so "re-tighten **to** 0.99" is either (a) a no-op /
confirm-current, or (b) shorthand for re-tighten **from** 0.99 now that the SFP sandbox has
grown (+$56/13d). **Please confirm which.** My read of intent + the weekly review (accounts
still micro; futures ~$0.50 margin/trade) is that a real re-tighten is premature.

**If tightening is intended (proposal):** don't jump to the global 0.15 — pick an interim that
survives one `risk_pct_considerable=0.20` @25x SFP loss without a false flatten (a single
considerable loss ≈ 20% equity swing → anything ≤0.20 self-halts). Candidate: leave 0.99 until
the account clears a hard-$ floor, then step to e.g. 0.30–0.40, not 0.15.
**Change:** edit the two override lines (hot-reloads on mtime, no restart). **Risk:** too-tight
→ false flatten + entry rejects on a normal considerable loss (the exact 06-15 incident).
**Watch-if-wrong:** `sfp_drawdown_breach_block` / `flatten_account` audits firing on non-catastrophic DD.
**Rollback:** restore 0.99 (delete/edit the override). **Backtest:** N/A (risk cap, not signal) but
Board memo required (it's a protective cap). **Recommend: hold 0.99; clarify intent.**

---

## 2. B2 maker-entry flip (taker → POST_ONLY maker)

**Current (verified `config/strategies.yaml:1372-1375`):** `maker_entry_enabled: false`,
`rest_timeout_s: 2.0`, `offset_pct: 0.0`, `fallback_mode: cross_to_taker`. The B2 maker code
is present on the prod-live base (so this is a genuine config flip, not a deploy) — built/tested
`ef6fa5f` (13 tests, 0 new regressions).

**Proposal:** flip `maker_entry_enabled: true`. Entries place as `POST_ONLY` LIMIT at the passive
offset; on non-fill within `rest_timeout_s` or a would-cross rejection → **cross to taker market**
(signal never dropped). B1 catastrophic stop stays taker/market — unchanged.

**Rationale:** fees are the dominant P&L drag — 18-50% of R on futures (avg ~27%, ~0.06%/side taker);
the 07-26 BTC SFP ran 0.04%/side = 39.7% of R (`bitunix-weekly-review-2026-07-26`). Maker rebate/side
materially widens net R on a strategy whose gross is already positive.

**Risk / watch-if-wrong:** non-fill or late-entry cost can exceed the fee saving (a missed/worse
entry on a fast move). The exact POST_ONLY-would-cross reject code is unconfirmed → any maker
reject routes to taker (safe, but you pay taker + a round-trip of latency). **Watch:** fill rate,
`BitunixMakerEntryUnfilled` / taker-fallback frequency, realized net-R vs the taker baseline, and
whether offset=0.0 (join-the-touch) actually rests as maker. **Rollback:** flip back to `false`
(hot-reload). **Backtest:** approval needed — quantify maker-saving net of non-fill/late-entry cost;
consider a small non-zero `offset_pct` if 0.0 crosses too often. **Recommend: flip on a short
observation window at current micro size, measure fill rate before trusting the economics.**

---

## 3. Pre-TP1 price/ATR trail

**Current (verified `config/strategies.yaml:1353-1354` + PR-5 note):** trailing exists only
POST-TP2 — a Chandelier reconciler `SL = max_high_since_TP2 − trail_atr_mult(1.5)·ATR` (long).
Between entry and TP1 the stop is the fixed structural SL; the runner only starts trailing after
TP2. So a move that runs toward TP1 then reverses gives back the whole unrealized gain to the
fixed stop.

**Proposal:** add an OPTIONAL pre-TP1 trail — once price advances ≥ k·ATR (or a % ) in favor,
ratchet the SL to `entry ± buffer` (breakeven-plus) or `extreme ∓ m·ATR`, monotonic (never loosens),
capped below TP1 so it can't pre-empt the target. Default OFF, behavior-preserving flag (mirror
`maker_entry`), so today's trades are unchanged until deliberately enabled.

**Rationale:** reduce give-back on the ~2-3/6 futures trades that reverse before a target
(weekly review); protect unrealized R in the pre-TP1 phase the current design leaves untrailed.

**Risk / watch-if-wrong:** trailing too tight in the noisy pre-TP1 zone converts would-be winners
into small stop-outs (the SOL/BTC tight-stop research: closer stops → more stop-outs + fee-drag-in-R
explodes; it DESTROYED BTC's edge — `bitunix-native-etl-built`). The trail distance is the whole ball
game. **Watch:** win-rate delta, count of trades stopped pre-TP1 that would have reached TP1, net-R vs
baseline. **Rollback:** flag OFF. **Backtest:** REQUIRED and load-bearing — sweep trail trigger/distance
on the corpus (refresh it first, B3) before any live enable; this is exactly where an unbacktested
tighten historically hurt. **Recommend: design + backtest behind an OFF flag; do NOT enable blind.**

---

## 4. VIX-gate (PMCC)

**Current (verified `config/strategies.yaml`):** `any_action_when_vix_above_30` is already a
PMCC `require_approval_for` trigger (lines 244/1838/1912) — VIX>30 escalates to HITL, and
`get_vix()==None` fail-safes to Board (CLAUDE.md invariant). There's also `hedge_vix_trigger: 25`
and `vix < 30` / `vix_above_25` advisory references.

**Proposal (clarify target):** the gate EXISTS as approval-required. Options if you want more:
(a) a HARD block (no-open above a VIX ceiling, not just approval) — a real behavior change;
(b) lower the threshold (30→25) to match the existing hedge/advisory levels;
(c) extend the VIX gate to a division that lacks it. State which; I'll scope that one.

**Rationale:** high-vol regimes widen option spreads and gap risk; a hard ceiling removes
discretionary approval risk in exactly the regime where judgment is worst.

**Risk / watch-if-wrong:** a hard block or lower threshold can sideline PMCC through elevated-but-
tradeable vol (VIX 25-30 is common); over-gating = missed premium. Removing/keeping the human step
touches a `require_approval_for` trigger → **Board memo required** recording (a) the incident class it
protects, (b) why the change is safe, (c) the falsifying observation (CLAUDE.md §1). **Watch:** count of
PMCC actions blocked/escalated by VIX and their counterfactual outcome. **Rollback:** restore the
trigger/threshold. **Recommend: keep approval-required as the default; only harden with a memo + a
specific incident it addresses.**

---

## 5. RANGE-ONLY veto on `up_but_bearish` shorts

**Current:** `up_but_bearish` appears NOWHERE in the codebase — it's a conceptual condition, so this
is **net-new gate logic**, not a tweak. Operationalize before proposing: e.g. price above a trend
baseline (EMA200 / prior-day close) BUT momentum/structure bearish (RD os=−1, ps_trail30=bear, or a
bearish CHoCH) → a "price up, bias down" short.

**Proposal:** veto shorts taken under `up_but_bearish` UNLESS the regime classifier says RANGE
(i.e. only fade the up-move short when we're genuinely ranging; in an uptrend, a "bearish" short into
strength is a counter-trend knife). Plug-in point = the SFP/futures with-trend gate (alongside the
existing rd/ps_trail30/ema200 `trend_mode` gate); reuse the LuxAlgo range-detector (a confirmed
in-range regime) as the RANGE signal. Default OFF flag.

**Rationale:** counter-trend shorts into strength are the classic loser; a range gate would only
permit the fade when mean-reversion is structurally likely.

**Risk / watch-if-wrong:** (a) defining `up_but_bearish` wrong mislabels trades; (b) a range
classifier has lag/whipsaw at range→trend transitions — vetoing real range fades OR admitting shorts
right as a range breaks up. Adds a gate to the live SFP/futures decision path → isolate, flag OFF,
prove on the corpus first. **Watch:** count of shorts vetoed vs their realized R (did the veto save or
cost R?), and range-classifier false-positive rate at transitions. **Rollback:** flag OFF.
**Backtest:** REQUIRED — need a precise `up_but_bearish` definition + a range-regime label, then
measure veto lift on historical shorts before any live wiring. **Recommend: pin the definition with
me first, then backtest; this is the least-specified of the five and the furthest from deployable.**

---

### Summary for your ruling

| # | item | current state | my recommendation |
|---|---|---|---|
| 1 | DD-cap "to 0.99" | already 0.99 both divisions | clarify intent; hold 0.99 (accounts still micro) |
| 2 | B2 maker flip | code live, flag OFF | flip ON for a measured window (fees dominate); watch fill rate |
| 3 | pre-TP1 trail | only post-TP2 trail exists | design behind OFF flag; **backtest before enable** (tight-stop risk) |
| 4 | VIX-gate PMCC | VIX>30 already approval-required | keep default; harden only with a Board memo |
| 5 | RANGE veto up_but_bearish | net-new, undefined term | define with operator, then backtest; least deployable |
