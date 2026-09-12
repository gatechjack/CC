# UFC Method-of-Finish / Method-of-Victory — staged deploy manifest (2026-09-12)

**Status:** BUILT · box-scratch GREEN · two-skeptic adversarial review (no BLOCKER/HIGH) · committed local ·
**NOT pushed, NOT deployed, NOT restarted, NO DB write.** Reserved for Jack: DEPLOY, RESTART, live DB write (the
per-sub enable), ARM, prod-live advance.

**Branch:** `pm-ufc-method-types-2026-09-12` @ **312f8743** (base = ctx-fix line `6a753b71`, i.e. box-truth).
**3 engine files + 1 test + this manifest.** NO shared-trio file (agents/data_exec.py, main.py, brokers/robinhood.py).

## ★ HEADLINE — read before reading "four types"
**`method_finish` is NEAR-INERT IN PRACTICE.** MOF ("either fighter") carries NO fighter identity in the Poly
signal (the market title is just "Will the fight be won by KO or TKO?"), so it resolves the bout by
**date + uniqueness** and **safe-misses on ANY normal multi-bout card** (a labelled ambiguous miss, never a
wrong-bout order). Of the four types, **the two generic (MOF) ones will rarely fire**; the real coverage is
**`method_victory`** (fighter-specific, code-anchored) — ~**302** historical fighter-specific KO/TKO positions vs
~152 generic. This is the shape of the market, not a defect — but the payoff is **narrower than "four new types"
sounds**, and no reader should think `method_finish` is doing work it is not.

**★★ ENABLE TRAP (the same failure shape as the cfb enable that looked done and wasn't):** a NULL/empty
`market_types` falls back to the **MLB** COPYABLE tuple `(moneyline,total,spread)`, which carries **NO method
tokens**. So the enable MUST write the EXACT string **`moneyline,go_the_distance,method_finish,method_victory`** —
get one token wrong and the sub stays **silently inert while looking enabled**. The enable runner's POST therefore
proves the **resolved** config through `sub_config_from_row` (the resolver the chokepoint sees), NOT a column
read-back (the column is the input; NULL-resolving-to-defaults is exactly the hazard), and asserts every OTHER
sub byte-unchanged **POST-vs-PRE** (not POST-vs-default — that comparison has caught a verifier's own false alarm).

## 1. Scope (Jack-ruled) — the four types
| Poly | Kalshi | market_type | leg |
|---|---|---|---|
| `-win-by-submission` (generic) | KXUFCMOF `-SUB` (either) | method_finish | Yes→yes / No→no |
| `-win-by-ko-tko` (generic) | KXUFCMOF `-KOTKODQ` (either) | method_finish | Yes→yes / No→no |
| `-{fighter}-win-by-submission` | KXUFCMOV `-{FTR}SUB` | method_victory | Yes→yes / No→no |
| `-{fighter}-win-by-ko-tko` | KXUFCMOV `-{FTR}KOTKODQ` | method_victory | Yes→yes / No→no |

**Ruled OUT + why (recorded so it isn't revisited):** rounds O/U (`totals-Npt5`) — Poly resolves at the **2:30 mark**
of the threshold round, Kalshi's `KXUFCROUNDS` "ends before round X" resolves at the **start** of it; a finish in the
first 2:30 settles oppositely and there is no Kalshi market with Poly's convention → not cleanly copyable. Round-of-
Victory + Decision — Kalshi has them, our whales never bet them (no Poly source).

**Accepted divergences (Jack's, not inherited assumptions):** (a) **No Contest ~1%, every method market** — Poly 50-50,
Kalshi settles the separate `-DRAW` outcome so our method leg resolves No (asymmetric-against-us, ~$3/copy at 5ct);
(b) **DQ ~0.1%, KO/TKO only** — Kalshi is KO/TKO/**DQ**, Poly excludes DQ.

## 2. What changed (engine-side; NO migration — no schema change)
- `trading_corp/data/ufc_poly_kalshi_match.py` — parse the method suffixes (noise-stripped); `ParsedPolyBet.method`;
  `attach_method_tickers` (CODE-ANCHORED MOV bind + code↔title refuse); match branches (MOF date-uniqueness, MOV
  code-anchored via `_resolve_winner_side`); `COPYABLE_MARKET_TYPES += method_finish, method_victory`.
- `trading_corp/prediction_markets/live_driver.py` — `UFC_SERIES += KXUFCMOF, KXUFCMOV`; ctx builder fetches+attaches
  them; `_audit_leg_independent` extended (KXUFCMOF/MOV → independent Yes/No leg check; was `na`).
- `trading_corp/prediction_markets/execution.py` — `_ufc_parse` now passes `title` (MOV reads the fighter full name;
  moneyline/distance/MOF ignore it → byte-identical for those).

**Graft base → fixed (CR-sha16):**
- `ufc_poly_kalshi_match.py` **1a16b3aa → ebabe6ce**
- `live_driver.py` **6d2c43e5 → 6561b569**  (base 6d2c43e5 = the deployed ctx-pagination fix; the box MUST be on it)
- `execution.py` **09c7e275 → 14787ba4**

## 3. INERT — how it stays off until Jack enables (verified by both skeptics)
The new types are in `COPYABLE_MARKET_TYPES` and the ctx builder fetches KXUFCMOF/MOV unconditionally, but a bet is
copied only if the sub's `market_types` contains the type → else `skip_market_type_excluded` (no order). The live UFC
subs are `market_types='moneyline,go_the_distance'` (neither method token) → **inert on deploy**. A NULL/empty
`market_types` falls back to the **MLB** COPYABLE tuple `(moneyline,total,spread)` — which also lacks the method
tokens → **fail-closed** (method types never auto-enable). Nothing in the package sets these tokens.

**ENABLE (Jack, reserved — a live DB write, per-sub, NO restart, market_types is read per cycle):** the enable MUST
write the EXACT tokens. Example (his call: jack and/or karen, and whether to enable both finish+victory or one):
```
UPDATE pm_subdivision SET market_types='moneyline,go_the_distance,method_finish,method_victory'
  WHERE account_id=? AND category='ufc' AND market_types='moneyline,go_the_distance';   -- assert 1 row each
```
A guarded runner (PRE snapshot / full-table backup / drift-check / 1-row assert / POST byte-unchanged-vs-PRE on the
other subs) will be presented when he authorizes — same shape as `pm_cfb_enable`.

## 4. Cross-division / base
Touches only division-scoped PM files — **no shared trio**, so no MACE collision and no rebase-onto-MACE needed.
**Box-only, NO FF-push** (prod-live is divergent on MACE/shared; reconcile is its own session). The deploy re-checks
the box CR-sha16 of all 3 files vs the bases in §2 before grafting — **box moved on any = STOP + rebase, do not force**
(esp. `live_driver` must still be `6d2c43e5`).

## 5. Deploy procedure (Jack — reserved) = graft 3 files + engine restart
1. **Drift gate:** box CR-sha16 == `1a16b3aa` (ufc), `6d2c43e5` (live_driver), `09c7e275` (execution). Else STOP.
2. **Backup** the 3 live files to `~/pm_ufc_backup_<UTC>/` (gate).
3. **Graft** the 3 fixed files (sanctioned single-STDIN stream; re-verify CR-sha16 == `ebabe6ce`/`6561b569`/`14787ba4`
   + py_compile). No migration (schema unchanged).
4. **Restart** the engine (canonical `restart_tc.ps1`). Migrations do not run on restart; none needed here.
5. **prod-live: DO NOT advance** (box-only until the reconcile session). Record in the reconcile list that prod-live
   is missing this commit (312f8743, base 6a753b71).

## 6. Post-check (read-only) — before trusting the first method fill
- New engine PID (restart recycled); liveness 30/30; **0 PM tracebacks**; boot-verify **every division**
  (mace/pmcc/pead/ira/bitunix/coinbase + PM both accounts).
- Grafted-code live: the ctx builder fetches KXUFCMOF/MOV (UFC_SERIES=4); method types remain INERT (no UFC sub's
  market_types contains them yet).
- **First real MOV/MOF fill (only after Jack enables) = first-proof, read it back** (`cc/pm_ufc_fillwatch_ro`):
  the filled ticker's **method token** (SUB/KOTKODQ) == the whale's slug method; the **leg** == Yes/No→yes/no; for
  MOV the ticker's **fighter code → the bout fighter name** matches the whale's fighter; `leg_audit` is `ok` (not
  REVIEW/na). Wrong → **fire `pm_cli.py live-disarm --global` first, report second.**

## 7. Stop conditions & rollback
- **STOP:** `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`.
- **Rollback:** graft the `~/pm_ufc_backup_<UTC>/` 3 files back + restart. No schema/DB effect (fetch+match logic
  only), so rollback is a clean file-swap + restart.

## 8. Honest limitations & residuals (surfaced by the skeptics; recorded, not fixed)
- **`method_finish` is near-inert in practice.** MOF ("either fighter") carries no fighter in the Poly signal, so it
  resolves by date+uniqueness — on a normal multi-bout card it is a labelled ambiguous **safe-miss** (never a wrong
  bout). It will rarely copy even after enable. The whale volume it can serve is small; `method_victory`
  (fighter-specific, code-anchored) is where the real coverage is (~302 KO/TKO historically).
- **The acceptance gate proves "no wrong order," not "full coverage."** Dry-run = 0 wrong fight/leg/type across 406
  live-market checks + 81 real whale bets; but of the 81, 41 were misses (20 ambiguous + 21 unresolved) that the
  harness verified as safe *status* (no order) but not as legitimate-to-miss. A miss = no trade (safe direction).
- **Same-3-char-code bouts** (Moreno/Morales → MOR/MOR2, 7-char blob) are dropped by the EXISTING KXUFCFIGHT regex
  (a pre-existing moneyline limitation) → safe-miss for method markets too. Not fixed (out of scope; safe).
- **LOW:** duplicate open method tickers would silently last-writer-win (benign; identical tickers); the parse-side
  vs index-side MOV title parsers accept different shapes (correct by design; a Poly title-shape drift degrades to a
  safe miss). Filed, not fixed.

## Artifacts
- Build: commit 312f8743 (matcher/live_driver/execution + `tests/prediction_markets/test_ufc_method_types.py`).
- Runners (`cc/`): `pm_ufc_boxscratch.{ps1,sh}` (differential + live dry-run), `pm_ufc_kalshi_discover_ro.*`,
  `pm_ufc_kalshi_detail_ro.*`, `pm_ufc_poly_probe_ro.*`, `pm_ufc_poly_outcomes_ro.*`, `pm_ufc_poly_titles_ro.*`;
  outputs under `cc/_farm_recon/`. To be written at deploy time: `cc/pm_ufc_graft.ps1`, `cc/pm_ufc_fillwatch_ro.*`,
  `cc/pm_ufc_enable.*` (the per-sub enable, Jack-authorized).
