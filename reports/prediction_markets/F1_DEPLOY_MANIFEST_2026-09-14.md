# F1 (Phase 2) — staged deploy manifest, gate proof, H2H board decision, stop conditions

**Branch `pm-f1-build-2026-09-14` (stacked on `pm-boxing-build-2026-09-14` @ 2bcb62bf, which is off prod-live
be2d0c76). Ships the KXF1RACE race-winner copy family INERT behind the `race_winner` market_types token.**

═══════════════════════════════════════════════════════════════════════════════════════════════
## 0. WHAT SHIPS + ordering (F1 stacks on boxing -> boxing deploys FIRST)
═══════════════════════════════════════════════════════════════════════════════════════════════

F1 shares 4 files with boxing, so this branch CONTAINS boxing's changes. The F1 deploy grafts ON TOP of a
box that already carries boxing. The graft's PRECONDITION asserts boxing is deployed (the boxing matcher is
present + the 4 shared files == boxing-version); it aborts otherwise. **Deploy boxing (its manifest) first.**

| # | file | change (vs boxing-version) | graft |
|---|---|---|---|
| 1 | `trading_corp/data/f1_poly_kalshi_match.py` | **NEW** (git add -f) | created (ABSENT at base) |
| 2 | `trading_corp/prediction_markets/execution.py` | +import F1X, `_f1_parse/_f1_match`, `MATCHER_ADAPTERS["f1"]`, `MarketContext.f1_race_index` | re-graft @ f1-version |
| 3 | `trading_corp/prediction_markets/live_driver.py` | +import F1X, `F1_SERIES`, `fetch_f1_market_context`, `CATEGORY_CTX_BUILDERS["f1"]`, `KXF1RACE-` leg-audit branch | re-graft @ f1-version |
| 4 | `trading_corp/prediction_markets/category.py` | `SLUG_PREFIX_MAP f1->f1` + `TAG f1/formula1->f1` | re-graft @ f1-version |
| 5 | `trading_corp/prediction_markets/search.py` | `CATEGORY_ALLOWLIST += "f1"` | re-graft @ f1-version |

**Same `data/`-gitignore trap as boxing** — file 1 is force-added + committed; the graft ships it explicitly +
asserts absent-before. NO migration (schema 23; F1 uses the new `race_winner` token, no new tables).

### Base/target CR-sha16 (autocrlf-safe)
- boxing-version base (box must == after boxing deploys): exec `03a5c100c8f68b59`, live_driver `c5a83f2e13b8d208`,
  category `b8eae9e193251eff`, search `afa2a9f50edefb42`; boxing matcher present `40e31b77f119cb38`.
- f1 target: f1 matcher `79c7ae0e56a4e859`, exec `d12058b331b3205d`, live_driver `667a7b2b2bd48b46`,
  category `f9d8a18e3617bd78`, search `9c199967d6ab70e1`.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 1. GATE PROOF (all passed; box-scratch, live tree untouched)
═══════════════════════════════════════════════════════════════════════════════════════════════

- **py_compile** F1 + boxing matchers + 4 shared on the box venv: OK.
- **Unit tests** (`test_f1_match.py` 20 + `test_ctx_builder_imports.py` 2): **22 passed** on box. Independence:
  expected (ticker, leg) derived from the ticker's OWN -CODE + Yes->yes/No->no, not the matcher output.
- **Full-suite differential**: branch = `21 failed, 392 passed`; baseline (be2d0c76) = `21 failed, 349 passed`
  (proven identical env-gap set). **=> 0 new failures; +43 new passing (boxing 21 + f1 20 + ctx-guard 2).**
- **★ Real-market DRY-RUN (RO box pm DB by RAW SLUG `f1-%` + live public Kalshi KXF1RACE + scratch matcher):
  PASS. 116 matched, ZERO wrong** (type/game-date/competitor/leg), 0 unverifiable, over 543 real whale f1-*
  bets (all stored `unknown` -> confirms the categorization gap this build closes). 268 props correctly
  `skip_non_winner` (podium/red-flag/fastest-lap/margin/sprint), 159 `out_of_window`. **The date-join validated
  across all 6 in-window race dates incl. the +/-1 UTC boundary; the real data has BOTH `catalunya-` and
  `spanish-` grands prix as separate GPs -- exactly what a GP-name->country map would have broken, confirming
  the date-join was the right call** (a deliberate deviation from the brief's suggested map).
- **Isolation**: box live sha of the 4 shared UNCHANGED before==after; scratch removed.
  Runners: `cc/pm_f1_boxtest.{ps1,sh}`.

### Two adversarial skeptics -- NO BLOCKER/HIGH. One MED FIXED, three LOW accepted.
- **S1 (wrong-race/driver/leg lens): no BLOCKER/HIGH.** Date-join safe (a 2+ race collision -> `driver_ambiguous`
  safe miss, never a wrong race); driver bind safe (accents/`Sainz Jr.`/title-disambiguation all correct); leg
  polarity equivalent (Poly No-on-driver == Kalshi NO leg); no non-winner slug parses as race_winner; tests
  independent. LOW (accepted, none on the 2026 grid, all SAFE MISSES not mis-picks): a first-initial FIA code
  (`MSC` for Mick Schumacher) or a compound surname (`de Vries`) can fail the surname code-anchor -> silent DROP;
  and a compound-surname slug token (`devries`) can miss. All degrade to a safe miss.
- **★ S2 (inertness/wiring lens): FOUND A REAL MED DEFECT -> FIXED.** `fetch_f1_market_context` referenced `F1X`
  but `live_driver.py` never imported it -> a NameError at engine boot when an F1 sub is built. It slipped past
  py_compile (syntax-only), `import live_driver` (the NameError is inside a fn), AND the offline dry-run (which
  used the scratch matcher, not the live builder). FIX = the import (commit c08945f8). GUARD = new
  `test_ctx_builder_imports.py` stubs pykalshi + a fake client and CALLS the F1 AND boxing ctx builders,
  asserting they resolve their matcher imports and build a MarketContext (would have caught this; also
  retroactively confirms boxing's builder is sound). S2 confirmed all other claims: the `race_winner` token
  gate is airtight (default market_types excludes it -> `skip_market_type_excluded`, F1 more inert than boxing);
  no `f1`/`f1-` prefix collision (`fl1` beats `f1` longest-first; fifwc/fed/nfl unaffected); the matcher is
  committed; the `KXF1RACE-` leg-audit branch returns `ok`/`REVIEW` correctly and is NOT in `_AUDIT_NAME_CATS`;
  boxing undisturbed; deferring H2H is the safe call (no H2H slug parses as race_winner).

═══════════════════════════════════════════════════════════════════════════════════════════════
## 2. STAGED DEPLOY (after boxing) -- RESERVED steps are the board's
═══════════════════════════════════════════════════════════════════════════════════════════════

1. **GRAFT** `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\pm_f1_graft.ps1"` (scp+tar; precondition boxing
   deployed; drift-gate box==boxing-version + assert f1 matcher absent; backup; rm-then-cp; py_compile 6; rollback-safe;
   engine NOT restarted). *Agent may run after board authorization (box write, not az-root).*
2. **RESTART** `powershell -ep bypass -f "C:\Users\AA Incorporado\Desktop\restart_tc.ps1"` (az-root -- RESERVED, board
   runs; ~3.5min bounces all divisions; quiet window, not near 9:30 ET).
3. **BOOT-VERIFY** `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\pm_f1_bootverify_ro.ps1"` (read-only;
   `--since ActiveEnterTimestamp`; f1+boxing matchers import clean = whole-engine risk cleared, 6/6 shas, 31 armed/0
   latched, 0 f1 subs, 0 KXF1RACE orders). *Agent runs.*
4. **FF-PUSH prod-live** `git push origin pm-f1-build-2026-09-14:prod-live` (git-only, RESERVED; only after boxing has
   already advanced prod-live and boot-verify passes -- a clean FF boxing-tip -> f1-tip).

═══════════════════════════════════════════════════════════════════════════════════════════════
## 3. ★★ BOARD DECISION #2 -- WHETHER F1 H2H SHIPS AT ALL
═══════════════════════════════════════════════════════════════════════════════════════════════

`KXF1H2H` is **EMPTY on Kalshi today** (0 open, 0 settled -- probed 2026-09-14; seasonal). A head-to-head
matcher therefore **CANNOT be dry-run-proven** (an empty test set is INCONCLUSIVE, never PASS -- the standing
gate discipline). **I did NOT build H2H** and recommend deferring it: shipping a matcher that never passed a
gate would violate that discipline, and the current f1 matcher safely CLASSIFIES any h2h/versus F1 slug as
`non_winner` (a labelled skip, never a mis-copy -- skeptic-confirmed). When Kalshi lists KXF1H2H, H2H is a
small additive rung (a pair-keyed matcher like tennis/cs2, dry-run-provable then). **The board decides: defer
(recommended) OR build-structural-inert-now-unproven.** Poly F1 H2H exists (~18 positions per the gate-zero
scoping), so the copyable volume is real once the Kalshi side lists.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 4. STOP CONDITIONS / not in this deploy
═══════════════════════════════════════════════════════════════════════════════════════════════

- **NO arm, NO enable, NO sub created.** F1 trades nothing until the board (a) creates a (kalshi_jack|kalshi_karen,
  f1) sub, (b) attaches an F1 whale, (c) **adds `race_winner` to that sub's market_types**, and (d) arms it -- each
  reserved. ★ Unlike boxing, F1 is DOUBLE-gated: even a blank-market_types armed F1 sub trades nothing until the
  `race_winner` token is added (it is NOT in the legacy default).
- **Funding**: F1 is shard 0 (exchange_index=0). Confirm shard-0 balance at enable (the Karen lesson).
- **close_time verify-at-enable**: the live ctx builder derives each race date from the pykalshi object's
  `close_time` (a core field). If a future SDK ever drops it, the live F1 index would be EMPTY (safe: F1 matches
  nothing) -- boot-verify reports the f1 index/subs, and BEFORE enabling F1 confirm the live index is non-empty
  for an upcoming GP. The offline dry-run already proved the index logic against the raw API's close_time.
- **NO migration** (schema 23). **H2H OUT** (decision #3 above). Constructors' championship OUT.

═══════════════════════════════════════════════════════════════════════════════════════════════
## ★ UPDATE 2026-09-15 (post-build-review: rebased onto the fixed boxing + per-category e2e test)
═══════════════════════════════════════════════════════════════════════════════════════════════
F1 branch **REBASED onto the fixed boxing tip 2f63508d** (so F1 carries the Surname-Initial-fixed boxing
matcher) + one commit adding a DEFENSIVE Surname-Initial parity to f1's `_surname_tokens` (a NO-OP on current F1
data -- F1 yes_sub_titles are uniformly First-Last -- future-proofing only) and `test_deployed_evaluate_e2e_f1`.
F1 tip now **bf46ee77** (docs commit on top for this update).
- **Graft SHA deltas (updated in `cc/pm_f1_graft.sh` + `cc/pm_f1_bootverify_ro.ps1|sh`):** the PRECONDITION
  boxing-matcher (must be present) `40e31b77f119cb38 -> d7881be546b631ec`; the F1 matcher target
  `79c7ae0e56a4e859 -> 0ba763f880b48417`. The 4 shared base+target SHAs UNCHANGED (exec d12058b3.., live_driver
  667a7b2b.., category f9d8a18e.., search 9c199967..; base = boxing-version 03a5c100../c5a83f2e../b8eae9e1../
  afa2a9f5..). The F1 graft still asserts boxing deployed first (now against the fixed boxing matcher SHA).
- **Re-proven on real data (`cc/pm_f1_boxtest.ps1`):** F1 UNCHANGED by the defensive fix -- **116 matched, ZERO
  wrong**, 0 unverifiable; F1 tests 20 -> 21 (+ e2e); differential 0 new failures (397 passed). The Q3 date-join
  window result is unchanged (9 Spanish-GP -1-offset matches; races >=7 days apart).
- **FF-push target (AFTER boxing): `git push origin pm-f1-build-2026-09-14:prod-live`**.
