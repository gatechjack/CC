# BOXING (Phase 1) — staged deploy manifest, gate proof, stop conditions

**Branch `pm-boxing-build-2026-09-14` (off prod-live `be2d0c76`, linear FF). Ships the KXBOXING winner-only
copy family INERT.** Board ruled the draw divergence ACCEPTED (see BOXING_PROBE_FINDINGS_2026-09-14.md §3).

═══════════════════════════════════════════════════════════════════════════════════════════════
## 0. WHAT SHIPS (5 files) — and why the file list is load-bearing
═══════════════════════════════════════════════════════════════════════════════════════════════

| # | file | change | base==be2d0c76? |
|---|---|---|---|
| 1 | `trading_corp/data/boxing_poly_kalshi_match.py` | **NEW** (git add -f; data/ gitignored) | ABSENT on box |
| 2 | `trading_corp/prediction_markets/execution.py` | +import BX, `_boxing_parse/_boxing_match`, `MATCHER_ADAPTERS["boxing"]`, `MarketContext.boxing_index` | yes |
| 3 | `trading_corp/prediction_markets/live_driver.py` | +import BX, `BOXING_SERIES`, `fetch_boxing_market_context`, `CATEGORY_CTX_BUILDERS["boxing"]`, `_AUDIT_NAME_CATS`+boxing | yes |
| 4 | `trading_corp/prediction_markets/category.py` | `SLUG_PREFIX_MAP += {"boxing","zuffa"}->"boxing"` | yes |
| 5 | `trading_corp/prediction_markets/search.py` | `CATEGORY_ALLOWLIST += "boxing"` | yes |

**★ FILE 1 IS THE TRAP (skeptic MED, brief hazard #2).** Both execution.py and live_driver.py import
`boxing_poly_kalshi_match` at module top. A graft that ships files 2-5 but MISSES file 1 -> ImportError at
engine boot -> the restart bounces ALL 31 armed divisions into a dead engine. The graft runner therefore
drift-gates the 4 modified files (box==be2d0c76) AND asserts file 1 is ABSENT on the box before applying, and
boot-verify confirms the matcher imported clean (= the whole-engine risk cleared). NO migration (schema stays
23; boxing uses the existing `moneyline` type + no new tables).

═══════════════════════════════════════════════════════════════════════════════════════════════
## 1. GATE PROOF (all passed; box-scratch, live tree untouched)
═══════════════════════════════════════════════════════════════════════════════════════════════

- **py_compile** all 5 files on the box venv: OK.
- **Unit tests** (`tests/prediction_markets/test_boxing_match.py`): **21 passed** on box. Independence: every
  expected (ticker, leg) is derived from the ticker's OWN -CODE + "buy YES on the bet fighter", NOT from the
  matcher's output. Includes the board-required SAFE-MISS proofs: same-surname across bouts and within a bout
  both -> `abbrev_collision_ambiguous`, ticker None; plus a code-swap refusal and the INERT market_types gate.
- **Full-suite differential**: branch = `21 failed, 370 passed`; baseline (be2d0c76, no boxing) = `21 failed,
  349 passed`. **IDENTICAL 21-failure set** (pre-existing env-gap: pykalshi/TestClient absent in the isolated
  scratch -- incl. `test_farm_league_tiles_are_the_allowlist`, which fails on baseline too, so the allowlist
  add is NOT a regression). **=> 0 new failures, +21 new passing. Zero regressions.**
- **★ Real-market DRY-RUN (RO box pm DB by RAW SLUG + live public Kalshi + scratch matcher): PASS.**
  129 real whale boxing bets (91 `boxing-`/38 `zuffa-`, all stored `unknown` -> confirms the categorization
  gap this build closes). Verification INDEPENDENT of the matcher's transform (type=KXBOXING- prefix; game=
  date-in-ticker; competitor=the ticker's OWN code subsequences the outcome AND the sibling code does NOT;
  leg=moneyline is always YES). Result: **21 matched, ZERO wrong** (type/game/competitor/leg), 0 unverifiable.
  Misses correctly classified: fail 82 (no-date/futures slugs), out_of_window 17, winner_outcome_unresolved 7,
  **abbrev_collision_ambiguous 2 (the surname safe-miss firing on real data)**. Non-empty set -> PASS, not
  INCONCLUSIVE. Matched outcomes spanned surnames ("Tate","Rodriguez") AND full names ("Filip Hrgovic") -- both
  bind correctly.
- **Isolation**: box live sha of the 4 modified files UNCHANGED before==after; the new matcher file ABSENT on
  the live tree throughout (scratch is `/home/azureuser/pm_boxing_scratch`, extracted + removed; live tree never
  written). Runners: `cc/pm_boxing_boxtest.{ps1,sh}` (branch) + `cc/pm_boxing_baseline_pytest.{ps1,sh}` (baseline).

### Two adversarial skeptics -- NO BLOCKER/HIGH; findings folded in (documentation, no code change)
- **S1 (wrong-competitor lens): no defect.** All surname-bind failure modes are safe misses; code-anchor
  works with 5-6 char codes (no false-refuse); index regex/blob grouping safe; tests genuinely independent.
  - LOW (accepted, matches UFC precedent): `_NOISE_RE=^(?:-\d+)+$` would treat a SECOND date-shaped slug suffix
    as noise (`boxing-garcia-2026-09-05-2026-09-12` -> date 09-05, `-2026-09-12` swallowed). Cannot produce a
    wrong-competitor/wrong-leg order (only a wrong-DATE lookup -> out_of_window/unresolved = safe miss); requires
    a Poly code segment that is itself YYYY-MM-DD-shaped, which does not occur (codes are opaque: garci1, allen).
    Same non-greedy-date behavior as the UFC matcher. NOT fixed (a date-parse change is riskier than the remote
    edge). Recorded so it is not rediscovered.
- **S2 (inertness/safety/wiring lens): no defect.** No existing armed sub can route a boxing bet (adapters are
  keyed by exact `sub.category`; boxing has no sub); category.py add is additive with no prefix collision and
  the engine never calls `derive_category_from_slug` for routing; `sub_config_from_row('' | None) ->
  ('moneyline','total','spread')`; leg-audit boxing branch returns 'ok' for a correct code, soft 'code_review'
  for a mismatch (never a false REVIEW), never hits the UFC/RFI/F5 branches; `fetch_boxing_market_context`
  cannot raise-and-escape (boot catch -> ctx None -> category skipped).
  - MED (process): addressed by §0 (5-file manifest + NEW-file-absent gate + boot-verify import check).
  - LOW (enable-time note, §4): boxing inertness = "no sub + arm gate", NOT the market_types token.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 2. STAGED DEPLOY — ordering (RESERVED steps are the board's; agent presents, board runs)
═══════════════════════════════════════════════════════════════════════════════════════════════

**Deploy = graft the 5 files (scp+tar) -> restart the engine -> boot-verify -> FF-push prod-live.** Sibling
services (pm_web, kcv2-observer) are NOT touched. The pm_web surfaces (boxing tile/page/prospects) will render
after pm_web's own next restart OR from the shared code once it reloads; the engine deploy is what this manifest
covers. No pm_web restart is required for the engine-side inert ship (boxing tiles are a follow-on nicety, not a
trading dependency).

1. **GRAFT** (`cc/pm_boxing_graft.ps1` -> `pm_boxing_graft.sh`, scp+tar): drift-gate the 4 modified files
   box==be2d0c76 (CR-stripped md5, autocrlf-safe) + assert `boxing_poly_kalshi_match.py` ABSENT; back up the 4
   to `~/pm_boxing_graft_backup_<ts>`; rm-then-cp each staged file to the box (data/ file may be root-owned ->
   rm-then-cp, then chown azureuser:azureuser); re-verify CR-sha per file; py_compile all 5 in place; ABORT +
   roll back the applied files on ANY mismatch (leave the box CONSISTENT). *Agent may run this after board
   authorization (it is a box WRITE but not az-root; if the classifier blocks it, board runs the presented one-liner).*
2. **RESTART** (canonical `restart_tc.ps1`, az-root -- RESERVED, board runs; classifier blocks the agent).
   Bounces ALL divisions (~3.5min boot). ★ Not near a 9:30 ET equity open (co-tenant PEAD) -- restart in a quiet
   window.
3. **BOOT-VERIFY** (`cc/pm_boxing_bootverify_ro.ps1`, read-only, agent runs): use
   `--since "$(systemctl show -p ActiveEnterTimestamp --value trading-corp.service)"` (NOT `-b` -- the VM rarely
   reboots; brief hazard #5). Confirm: engine active/running with a NEW PID; **the boxing matcher imported CLEAN
   (0 boot ImportError/traceback = the whole-engine risk cleared)**; all divisions back (MACE/PMCC/PEAD/bitunix/
   coinbase/RH + kalshi_jack + kalshi_karen reconciled latched=False); **31 armed / 0 latched UNCHANGED**;
   **0 boxing sub-divisions, 0 KXBOXING orders (any status) = INERT**; 5/5 file shas == branch target.
4. **FF-PUSH prod-live** (git-only, RESERVED -- the ledger step): `git push origin pm-boxing-build-2026-09-14:prod-live`
   (a clean fast-forward from be2d0c76; makes prod-live's tip == the box state). Report the SHA advance in the
   same session.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 3. POST-CHECK (after FF-push) + ROLLBACK
═══════════════════════════════════════════════════════════════════════════════════════════════

- **POST**: `origin/prod-live` == branch tip; box 5 files == prod-live (CR-stripped); engine PID stable post-boot;
  no leg_audit REVIEW rows; 0 KXBOXING orders; 31 armed/0 latched; boxing prospects begin surfacing on the next
  paper-poll/refresh cron (display-only, non-trading -- the intended additive effect).
- **ROLLBACK** (if boot-verify fails): restore the 4 modified files from `~/pm_boxing_graft_backup_<ts>` + remove
  `boxing_poly_kalshi_match.py`, restart, re-verify be2d0c76 state. prod-live NOT advanced until boot-verify passes,
  so a failed boot never reaches the ledger.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 4. STOP CONDITIONS / what is NOT in this deploy
═══════════════════════════════════════════════════════════════════════════════════════════════

- **NO arm, NO enable, NO sub-division created.** Boxing trades NOTHING until the board (a) creates a
  (kalshi_jack|kalshi_karen, boxing) sub, (b) attaches a boxing whale, and (c) arms it -- each a separate
  reserved step. ★ ENABLE NOTE (skeptic LOW): a boxing sub created with a blank/NULL `market_types` resolves to
  the legacy default which INCLUDES `moneyline`, so it WOULD copy the boxing winner immediately on arming --
  boxing's inertness is "no sub + arm gate", not a market_types token. The board sets sizing per sub (5-contract
  default) at enable.
- **Funding**: boxing is shard 0 (exchange_index=0). kalshi_jack shard-0 was funded at the last snapshot; confirm
  shard-0 balance AT enable time (the Karen silent-failure lesson) -- an enable-time check, not a deploy blocker.
- **NO migration** (schema 23 unchanged). **NO pm_web restart required** for the engine ship (boxing tiles/pages
  auto-derive from the allowlist on pm_web's next restart; a follow-on, not a trading dependency).
- The draw hedge (Kalshi KXBOXINGMOV -DRAW synthetic) is explicitly OUT -- board ruled ACCEPT; reopens on evidence
  only if boxing is enabled and trades volume.

═══════════════════════════════════════════════════════════════════════════════════════════════
## ★ UPDATE 2026-09-15 (post-build-review: board took the Surname-Initial fix)
═══════════════════════════════════════════════════════════════════════════════════════════════
Boxing branch tip **2bcb62bf -> 2f63508d** (one commit: the Surname-Initial fix + the deployed evaluate() e2e
test). The fix strips a trailing single-letter initial in `_surname_tokens` so a whale's "Cortes" binds to the
Kalshi "Cortes A." (Surname-Initial) form (7/300 markets, the 09-12 main card). ONLY the boxing matcher file
changed; the 4 shared files are byte-unchanged.
- **Graft SHA delta:** boxing matcher target `40e31b77f119cb38 -> d7881be546b631ec` (updated in
  `cc/pm_boxing_graft.sh` + `cc/pm_boxing_bootverify_ro.sh`). The 4 shared targets + all bases UNCHANGED.
- **Re-proven on real data (`cc/pm_boxing_missaudit_ro.ps1` + `pm_boxing_boxtest.ps1`):** matched **21 -> 23**
  (the Magsayo/Cortes/Opetaia near-misses now bind), winner_outcome_unresolved 7 -> 5 (the remaining 5 =
  Hitchins-Salas / Hackett-Derevyanchenko, bouts NOT on KXBOXING = legitimate no-contract), **the Garcia
  same-card collision STILL safe-misses (2 -> 2, guard intact), ZERO wrong (type/game/competitor/leg).** Two of
  the 23 are `unverifiable_nameform` (the initial-format ticker code is a first-initial, not a surname prefix, so
  the code-SUBSEQUENCE heuristic can't confirm them -- but the code-ANCHOR `labels_code_swapped` DID confirm the
  bout assignment, so they are sound, not wrong). Unit tests 21 -> 25 (Surname-Initial binds, collision-still-
  safe, First-Last-unchanged, + `test_deployed_evaluate_e2e_boxing`). Differential 0 new failures (374 passed).
- **FF-push target: `git push origin pm-boxing-build-2026-09-14:prod-live`** (now @ 2f63508d).
