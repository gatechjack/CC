# TRANSITION — R5.5 SHIPPED · R7 PLANNED · R7.a PROVEN · R7.c FIRST CUT (UNPROVEN) — 2026-08-29

**★★★ SUPERSEDED by `TRANSITION_SESSIONWRAP_2026-08-30.md` (2026-08-30). Read THAT first — it carries the live
state, R7.f as the only thing before money, the queued rungs, and the prod-live advance. ★★★**

**★★ SUPERSEDES `TRANSITION_STAGE3_POSTDEPLOY_2026-08-29.md`. Read THIS first. ★★** That doc's "R5.5 NOT built" is
stale — R5.5 shipped, R7 is planned, R7.a ran, R7.c is a first cut. This is the handoff for the agent who PROVES R7.c
then proceeds R7.d → R7.e → R7.f. Branch `prediction-markets-stage3-r55-2026-08-29` @ **`54ec18a`** (local == origin,
pushed). `origin/prod-live` = **`c88beea`** (`95e78c4`+`166b5ab` ancestors; branch forks at `8d77a26`, predates
prod-live's advance → deploy by EXPLICIT MANIFEST). LIVE PM DB schema 11; **NOTHING armed, NO order path reachable, NO
order ever placed.**

---

## 0. ★★★ R7.c IS A FIRST CUT AND UNPROVEN — DO NOT MISTAKE "COMMITTED" FOR "READY" ★★★
`trading_corp/prediction_markets/live_driver.py` is committed (`54ec18a`) — it is the code that PLACES REAL ORDERS —
but it is **NOT tested, NOT box-scratched, NOT adversarially reviewed, NOT WIRED (there is no `main.py` block), NOT
deployed, NOT activated.** It must not run until the prove-it half (§3) is done AND authorized. The build (writing the
order-placing code) is the easy part; the tests + box-scratch + adversarial review are what catch the money bug — and
that review has found a CRITICAL in R4, R5 AND R6, twice being the NO-leg lens in a new home. Running it at the tail of
a marathon is how you get a review that passes without looking hard — which is why this session CHECKPOINTED here.

---

## 1. WHAT THIS SESSION SHIPPED (branch `36007c8` → `54ec18a`)
- **R5.5 boot-reconcile — BUILT + box-scratch GREEN + adversarial review (3 reviewers, 4 real bugs fixed) + rung
  ladder (STAGE3_PLAN §19).** `boot_reconcile.py` + `test_boot_reconcile_r55.py`. Journal-vs-Kalshi SIGNED-net-per-ticker
  compare (YES +, NO −; the 5th NO-leg lens), full-account R-c, exact K=0, latch on any mismatch, injected fetcher (no
  broker). See §19 of the plan for the full record + the two hard R7 preconditions.
- **R7 planning pass — `R7_PLAN_2026-08-29.md`** (the 7 questions; the §8 gate sequenced; the R7 rung ladder; §0.3
  Jack's rulings; §11 the R7.a result).
- **R7.a authenticated sign read (§4) — auth PROVEN; sign UNRESOLVED** (account flat on the venue).
- **R7.c driver first cut — `live_driver.py`** (§0, §3).

## 2. FIRST-READ ORDER (next agent)
`R7_PLAN_2026-08-29.md` (whole; esp. §0.3 rulings + §11 R7.a + the §8 gate + the R7 rung ladder §8/§12) →
`STAGE3_PLAN_2026-08-28.md` §8 (re-scoped gate item 1) + §16 (NEEDS the amendment, §3) + §19 (R5.5) →
`PM_REQUIREMENTS.md` R7 (exposure-cap limit) → `boot_reconcile.py` + `live_driver.py`.

## 3. ★ THE EXACT REMAINING R7.c LIST (the prove-it half; each rung its own authorization; HALT between)
1. **Tests** (`tests/prediction_markets/test_live_driver_r7c.py`) — the seven cases:
   (a) boot-reconcile runs at boot and comes up **latched-if-mismatch**; (b) arm-gating **blocks when disarmed** (no
   POST); (c) the arm state is **re-read before EVERY order** (a mid-cycle kill stops the very next one, not once per
   cycle); (d) **signals convert** (/positions → entry `CopySignal`, `is_genuinely_open` filter, restart-stable
   `signal_id`); (e) the `place_fn` **POSTs EXACTLY `decision.body` + `decision.client_order_id`** (option b, verbatim —
   no rebuild); (f) **ZERO real POSTs** (stub broker; disarmed); (g) the **latches fire** (auth-failure → whole account +
   manual-exit flag; consecutive-error → sub).
2. **Box-scratch** — stub broker + disarmed → assert ZERO real POSTs + all of the above, LIVE untouched, PIDs unchanged,
   `-p no:pytest_ethereum`.
3. **Adversarial review** pointed at: the **PLACEMENT SEAM** (posts the approved body verbatim? coid parity with the
   journal? the per-order re-read timing?), the **ASYNC CYCLE** (does disarm block EVERY order? do the latches fire
   correctly?), and the **K9 JOURNAL-WRITE WINDOW** (the POST→`_record_order` gap — a crash there leaves a real position
   the journal doesn't know; boot-reconcile is the backstop, confirm it). NOT last rung's bug class.
4. **The thin INERT `main.py:run()` block** — construct the broker + `create_task(scheduled_pm_live_loop(...))`, gated
   OFF (a config flag absent/false), so it is inert until R7.e flips the flag. Lands in the R7.e deploy manifest.
5. **The `kalshi_live.py` POST-wrapper NOTE** — a comment where a maintainer will see it: the ~15-line POST try/except is
   duplicated in `live_driver.make_place_fn` (option b), so a future fix to that error handling has two homes.
6. **The §16 amendment** — change "R7 will WRAP `place_order` VERBATIM" to record that option (b) POST-the-gate-approved-
   body is the **PRECEDENT** (`poly_kalshi_executor` POSTs a pre-built body), NOT a deviation; the guarantees are about
   the approved body, so a rebuild would be a silent 7th NO-leg-lens home.
7. **Two rough edges to clean in review:** the positions-client injection seam (module-level `_positions_client` +
   `client_fetch_positions_book`) and the fetch-failure lambda in `run_boot_reconcile` (an obscure generator-throw) —
   functional but not clean.

## 4. WHAT R7.a PROVED (2026-08-29T14:37Z; runner `cc\pm_r7a_sign_read.*`, board-authorized; READ-ONLY, no order/arm/write)
- **Authenticated RSA-PSS signed transport WORKS** — `get_balance` + `get_positions` succeeded on the KALSHI account via
  the vault-fetched `KALSHI_*` keys + the VM managed identity (`KEY_VAULT_URI` from the engine's systemd Environment).
  **The re-scoped GATE ITEM 1 = PASSED.** The old 401 concern is retired.
- **Balance = $509.81** (50981 cents) — current KALSHI cash. Ample for a 1-contract first order.
- **Sign check UNRESOLVED** — `get_positions` returned 0 nonzero positions (account FLAT on the venue), so no NO/YES
  holding to test `position_fp` against. See §5.

## 5. ★ `position_fp` SIGN — UNPROVEN; CONFIRMED AT THE FIRST ORDER'S RECONCILE (hand-inspected) — the 6th NO-leg lens
`boot_reconcile.py` ASSUMES `position_fp > 0 == YES`, `< 0 == NO`. The existing reader only ever used `abs()`, so the
SIGN has never been exercised, and R7.a could not test it (account flat). **RULED (Jack): the sign is proven AT the first
order's reconcile (R7.g), NOT before.** That first reconcile is LOAD-BEARING and **must be inspected BY HAND.** The first
order is 1 contract, so a wrong (inverted) verdict costs ~$0.50 and is caught immediately at that reconcile. Do not
trust any reconcile verdict on a NO position until this is confirmed.

## 6. RULINGS SETTLED THIS SESSION — DO NOT RE-RAISE
- **EXCLUSIVITY: SETTLED + AUTHORIZED (Jack ruled it repeatedly — treat as CLOSED, never re-surface as a blocker).**
  Disable `kalshi_copy_trading` **and** standby `kalshi_llm_arbitrage` via `config/divisions.yaml` `enabled: false` on
  both. **Applied as part of the R7.e deploy manifest** (activated by the ONE combined restart with the driver deploy —
  don't restart twice). **Reversible:** flip both back to `enabled: true` + restart. At R7.e: back up the box
  `divisions.yaml`, assert the diff is EXACTLY those two `enabled` flags, verify a disabled division builds no broker.
- **Driver = ENGINE-TASK** (Q5 option C, `live_driver.py`), combined with the exclusivity restart.
- **Placement = option (b)** — POST the chokepoint's gate-approved body verbatim (§3.6 amendment; the precedent).
- **Demo re-scoped AWAY** — no demo credentials; gate item 1 became the authenticated prod-read proof (PASSED at R7.a).
- **xifutloong3: DETACH before the first order** (CLI `live-detach`), **re-attach for R8** (the parallel test needs BOTH
  whales). **★ STILL ATTACHED** (`pm_subdivision_attachment` = 2, observed 15:18Z) — the detach is PENDING, do it at R7.e.
- **First-order size = EXACTLY 1 contract** — set `pm_subdivision.fixed_stake_usd = $0.01`: `usd_to_contracts(0.01,
  price) = max(1, floor(0.01/price))` = 1 for every Kalshi price ≥ $0.01 (the tradable band is $0.01–$0.99). (The
  earlier "$0.50 → 1 contract" was WRONG — at a $0.01 price $0.50 buys 50 contracts.)
- **Sign proven at the first reconcile** (§5), not before.

## 7. FILED — handed to Jack; NOT PM's to fix
- **Journal-vs-venue lag:** `kalshi_copy_trader`'s persisted "9 open" positions vs the venue's **0** (settled/closed on
  Kalshi, unbooked by the round-trip resolver). A live demonstration that reconcile must compare the VENUE not the
  journal; the KALSHI account is actually flat. Handed to Jack; not PM's to fix.
- **Exposure-cap-sums-the-journal** standing limitation — `PM_REQUIREMENTS.md` R7 (outlives the shutdown).
- **Settlement-close-path** future rung (R-d) — filed, not built.

## 8. LEAVE-IT-RUNNING SNAPSHOT (observed 2026-08-29T15:18Z, read-only; runner `cc\pm_r7c_wrap_ro.*`)
- `origin/prod-live` **`c88beea`**; branch **`54ec18a`** (local == origin).
- PM DB **schema 11**, quick_check ok. Counts: pm_watchlist 114 · pm_paper_trade 188 · pm_paper_category_stats 11 ·
  pm_whale 14 · pm_closed_position 29893 · pm_category_stats 114 · pm_paper_config 3 · pm_roster 114.
- **Money: pm_account 1** (`kalshi_jack`) · **pm_subdivision 1** (`kalshi_jack, mlb`, caps NULL) · **pm_subdivision_attachment
  2** (SDTrading `0x16bb…8492` + **xifutloong3 `0x2dc1…b33c`, both active** — xifutloong3 to be DETACHED at R7.e) ·
  **pm_subdivision_order 0** (nothing ever placed).
- **arm DISARMED** (`global_armed:false`, **0 `pm_live` rows**).
- Endpoints all 200 (`/healthz` schema 11, `/`, `/farm`, `/live`, `/live/kalshi_jack/mlb`).
- Services: engine `trading-corp.service` PID **53046** (up 08-28 21:30Z, NRestarts 0); pm_web
  `prediction-markets-web.service` PID **59422** (up 08-29 02:20Z, NRestarts 0).
- Four cron entries healthy (poll `*/30`, refresh `0 5 --cap 50000`, adjudicate `40 5`, rollup `50 5`).

## 9. THE RUNG LADDER AHEAD (ALL UNAUTHORIZED; each its own per-step authorization; HALT between, never chain)
- **R7.c PROVING** — §3 (tests + box-scratch + adversarial review + main.py block + notes + §16 amendment).
- **R7.d** — kill-switch proof (dry-run): arm → kill mid-cycle → next blocked → an auto-disarm latch fires → the disarm
  survives a restart.
- **R7.e** — DEPLOY (manifest: `live_driver.py` + the main.py block + `divisions.yaml` disable + the `fixed_stake_usd=$0.01`
  config) + the ONE combined ENGINE RESTART (az-root; Jack coordinates timing + the bitunix heads-up; Saturday favours
  it — equity dormant). **DETACH xifutloong3** (CLI). Verify: on-disk matcher loaded, driver task running,
  `kalshi_copy_trading` gone from the live set, bitunix reattached, arm DISARMED, 0 `pm_live` rows.
- **R7.f — ★ IRREVERSIBLE — THE MONEY GATE:** arm ONE sub-division (`kalshi_jack/mlb`, global + sub), one whale
  (SDTrading only), place ONE 1-contract MONEYLINE order, manually observed.
- **R7.g** — reconcile the fill: FillEvent matches; boot-reconcile vs `get_positions` (**confirm the `position_fp` sign
  by hand here**, §5); balance delta == notional + fee (1 contract pins the fee convention).
- **R7.h** — idempotency across restart (same signal, kill+restart, no double order).
- **R7.i** — disarm, return to safe.
- **R8** — parallel test: re-attach xifutloong3; copy BOTH legacy whales side-by-side with legacy (observation-only;
  wider-than-legacy is EXPECTED, not a defect).

## 10. Runners + artifacts (the paper trail)
Runners in `C:\Users\AA Incorporado\cc`: `pm_r7plan_orient_ro.*` (R7 orient + demo probe + engine `--live-divisions` +
co-tenant), `pm_r7a_sign_read.*` (R7.a authenticated sign read), `pm_r7c_wrap_ro.*` (this snapshot); R5.5:
`pm_r55_partA_ro.*`, `pm_r55_refresh_classify_ro.*`, `pm_stage3_r55_boxscratch.*`. Branch commits: `36007c8` (R5.5 ladder)
→ `6d63bfc` (R7 plan) → `e9c4bf8` (rulings) → `20a6af1` (R7.a result) → `54ec18a` (live_driver first cut). Docs:
`R7_PLAN_2026-08-29.md`, `STAGE3_PLAN_2026-08-28.md` (§8/§16/§19), `PM_REQUIREMENTS.md` (R7).

*Written 2026-08-29 by the R7-planning/R7.c-first-cut agent. R5.5 shipped; R7 planned; R7.a proved the authenticated
transport; R7.c is a FIRST CUT (unproven, unwired, undeployed). NOTHING is armed; NO order path is reachable; NO order
has ever been placed. R7.c's PROVING, R7.d/e/f/g/h/i and R8 all remain UNAUTHORIZED. HALT.*
