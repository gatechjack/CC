# TRANSITION — Boxing / F1 / NASCAR build brief (for a fresh agent who was not here)

**This is a cold-executable build brief, not a session summary.** Plan-only work is done; the plan was
board-accepted. You build the phases below, each behind its own board authorization. The full scoping plan is at
`C:\Users\AA Incorporado\.claude\plans\moonlit-snacking-puzzle.md` (this brief is self-contained; read that for the
long-form reasoning).

## Ground truth before you touch anything
- **prod-live = `be2d0c76`, the box matches it. Compare CR-stripped (autocrlf).** Verify the tip yourself.
- **~30+ sub-divisions are ARMED and trading real money.** Read-only throughout your investigation. Global stop:
  `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`.
- **Read `command-paste-rule` and confirm before any box access.** Box access is via a validated one-line `.ps1`
  runner only — no ad-hoc ssh/az. Deploy/restart/DB-write/arm-change are reserved (present + board runs; the
  classifier blocks the agent from an az-root restart even after authorization — present it, the board runs it).
- Box repo root is the NESTED `/home/azureuser/trading_corp/trading_corp/`; PM DB
  `data/prediction_markets.db`; arm/legacy DB `data/trading_corp.db` (agent_state cols agent/key/value_json/updated_ts).
  Local python is the Azure-CLI `python.exe`. Box pytest needs `-p no:pytest_ethereum`.

## THE PLAN — order and reasoning (not just the order)
1. **BOXING first — a UFC clone (cheap).** Do not let it wait on F1.
2. **F1 second — buildable, kill-switch passed (per-driver binary race-winner + head-to-head).**
3. **NASCAR — NOT built. Clean NO.**
4. **UI — no dedicated phase** (renders as the non-MLB positions table like UFC; the MLB game card is untouched).

### ★ Gate-zero numbers, and why they INVERTED the expectation
Position count (the honest proxy — we copy fixed ~$2-3/bet, so whale dollars overstate), full discovered universe
(all three are ~0 in the *current attached roster* — this is the attach-later prize Search populates):
- **F1: 169 per-driver race-winner + 18 head-to-head** (+8 championship, OUT). **More copyable volume than boxing.**
- **Boxing: ~73 copyable** (62 two-fighter `boxing-{A}-vs-{B}` + 11 method).
- **NASCAR: ~0** (33 positions / $2,534 total).

**Two inversions a cold reader will get wrong without this context:**
- **F1 is per-driver BINARIES (`will-{driver}-win-the-{gp}`, one Yes/No each), NOT a field-of-twenty.** That is the
  shape RFI already handles, not a new architecture. It is why F1 is buildable and went ahead of the "hard" label.
- **Boxing is first because it is CHEAP, not because it is the biggest prize.** 90% of boxing's dollars are
  celebrity-novelty *single-fighter* slugs (`will-jake-paul-win-his-boxing-match`, Tyson, Joshua) that a two-fighter
  matcher **cannot** touch. Do not read "first" as "biggest."

### ★ Series names + ticker formats — PROBED LIVE, verbatim (do not guess these)
Absence from a guessed ticker name is NOT evidence of absence (it bit this platform 5+ times; it bit me on NASCAR).
- **Boxing WINNER series is `KXBOXING`, NOT `KXBOXINGFIGHT`.** `KXBOXINGFIGHT` is EMPTY (open+settled). A build
  against `KXBOXINGFIGHT` would dry-run to zero matches and read as "no whale activity" rather than a wrong constant.
  - Winner: `KXBOXING-{YYMONDD}{FIGHTERBLOB}-{FIGHTERCODE}` — e.g. `KXBOXING-26SEP12GARCIAMORALE-MORALE`,
    `yes_sub_title="Abraham Morales"` (fighter codes are surname-based, 5-6 chars — NOT UFC's 3; bind via the full
    name in `yes_sub_title`).
  - Method: `KXBOXINGMOV-{YYMONDD}{BLOB}-{FIGHTER}{KOTKODQ|DEC}` + a first-class **`-DRAW`** market
    ("Fight ends in a draw/no contest?").
  - Go-the-distance: `KXBOXINGDISTANCE-{YYMONDD}{BLOB}-DIST` ("Fight goes the distance?").
  - Rounds series exist (`KXBOXINGROUNDS/VICROUND/1MIN`) and stay **RULED OUT** (the "ends before round X" vs Poly
    over/under mismatch, same reason as UFC).
- **F1:** `KXF1RACE-{COUNTRY}GP{YY}-{DRIVERCODE}` per-driver binary — e.g. `KXF1RACE-AZEGP26-VER` (Azerbaijan GP),
  `KXF1RACE-SPAGP26-VER` (Spanish GP), title "Max Verstappen to finish in first", `yes_sub_title="Max Verstappen"`.
  `event_ticker=KXF1RACE-{COUNTRY}GP{YY}`. **Key is country-based → STABLE, not golf-fragile** (golf's codes were
  opaque sponsor-rotating). Build cost = a bounded ~24-race GP-name→country-code map (AZE, SPA, …), harvest from
  settled `KXF1RACE` — analogous to a team-code map.
  - `KXF1H2H` (head-to-head pair) is **EMPTY** on Kalshi right now (seasonal) → see board decision #2.
  - `KXF1CONSTRUCTORS-26-{TEAM}` = championship, **OUT**. Pole/qualify/podium/top5 props out of scope.
  - Series `exchange_index=0` (confirm PER-MARKET live; shard is per-market, not per-series).
- **NASCAR:** `KXNASCARRACE` (per-driver, event `UNO2PBO26`) and `KXNASCARH2H` (`BRI4PB26…` "Brickyard 400
  presented by…") **DO exist** — the race keys are sponsor-laden/opaque (golf-fragile). NASCAR is a **volume**
  problem, not a listing problem. Do not build; revisit only if whale volume appears.

### File-by-file reuse map
- **Boxing = clone `trading_corp/data/ufc_poly_kalshi_match.py`**: slug parse; the code-anchor bind
  (`_code_of`/`_code_match_score`/`labels_code_swapped`) that binds a fighter from the Kalshi title and refuses a
  swapped code; accent-fold name match (`_afold`/`match_fighter_name`/`fighter_kcode`); the fight index
  (date + fighter-pair) + method/distance attach. Adapter: add `_boxing_parse/_boxing_match` to
  `execution.py MATCHER_ADAPTERS`, reuse the `MarketContext.fight_index` slot (or add `boxing_index`). Ctx builder:
  clone `live_driver.fetch_ufc_market_context` (fetch KXBOXING/MOV/DISTANCE, raw `exchange_index`+size merge).
  Leg-audit: add a boxing branch to `_audit_leg_independent` (mirror the KXUFCMOF/MOV Yes/No branches).
- **F1 = new `trading_corp/data/f1_poly_kalshi_match.py`** modeled on: UFC (name-bind/code-anchor) + the
  **single-entity precedents** `mlb_poly_kalshi_match._match_rfi` (one binary per event) and
  `fed_poly_kalshi_match` (event+bucket) for the race-winner; `tennis_poly_kalshi_match` for the H2H pair. New
  `MarketContext` index slot(s), new ctx builder in `live_driver`, new adapter + leg-audit branch.
- **Category surfacing (UI, last):** add `"boxing"`/`"f1"` to `trading_corp/prediction_markets/search.py`
  `CATEGORY_ALLOWLIST` — farm tiles (`farm.py league_categories()`) and `/live/{acct}/{cat}` pages auto-derive
  (the mechanism that surfaced 8 soccer-league pages with no UI code). Non-MLB render is
  `web/live_view.py build_live_context_non_mlb` (a positions table, like UFC). The MLB game card stays byte-unchanged
  (RFI kind-slot precedent: conditional, non-MLB cards untouched).

### ★ TWO DECISIONS THAT ARE THE BOARD'S — do not assume them
1. **BOXING DRAW re-pricing.** UFC silently skips draw/no-contest at ~1% (safe miss). Boxing draws
   (split/majority/technical) are materially more common, and Kalshi carries a dedicated `KXBOXINGMOV-…-DRAW`. On a
   draw, a copied "Fighter A wins" resolves **No on Kalshi → 100% loss** while Poly often returns ~50%. **MEASURE
   the real boxing draw rate from historical settled `KXBOXING`/`KXBOXINGMOV` + Poly results, bring the number, and
   the board rules** (accept moneyline / gate it / copy the DRAW market). Do NOT inherit the UFC 1% ruling.
2. **Whether F1 H2H ships at all** given `KXF1H2H` is EMPTY today. Build it structurally, but it is **INCONCLUSIVE**
   on the dry-run gate until Kalshi lists H2H markets — the board decides whether to ship it inert now or defer.

### The gate (every phase)
Ships **INERT** behind its own `market_types` token per sub-division (same mechanism as RFI's `first_inning_run`
and F5/1H's `f5`/`first_half`): matcher returns `skip_market_type_excluded` until a sub enables the token. **Blank/
NULL `market_types` MUST resolve to the legacy default `("moneyline","total","spread")` and NEVER auto-enable the
new token** — this was a real skeptic finding on the subgame build (`execution.sub_config_from_row`). Dry-run gate:
ZERO wrong event/competitor/strike/market-type, misses classified by reason, **an EMPTY TEST SET REPORTS
INCONCLUSIVE, NEVER PASS** (that is why F1 H2H cannot pass today, and why the boxing winner needs its date-key
confirmed first). Then box-scratch full-suite differential (0 new failures vs baseline), two adversarial skeptics,
commit + push, present the staged deploy (graft → restart → boot-verify), **halt at the deploy line**. Enable is a
separate board step; sizing is per sub-division (5-contract default; board sets it in the UI).

### ★ Hazards that cost time this week — do not rediscover them
- **`data/` is gitignored.** A new matcher module under `trading_corp/data/` (e.g. `f1_poly_kalshi_match.py`) needs
  `git add -f` — `git add -A` silently skips it, and the graft would then place files importing a missing one →
  ImportError at engine boot, after a restart that bounces every division. (Caught on the subgame build.)
- **`journalctl -u trading-corp.service -b` = since SYSTEM boot**, and this VM almost never reboots → it returns the
  whole multi-day journal (~3.7M lines) and a boot-verify that greps `-b` repeatedly crawls. Use
  `--since "$(systemctl show -p ActiveEnterTimestamp --value trading-corp.service)"`, or dump the boot journal once
  to a temp file and grep the file.
- **Multi-file graft channel = scp+tar staging** (drift-gate box==base per file, backup, rm-then-cp because `data/`
  files can be root-owned, re-verify CR-sha, py_compile, rollback-safe). base64-in-heredoc has failed twice.
- **PM migrations self-apply** from the pm_cli crons (db.init_db) — if you add a migration, expect the cron to win
  the race and apply it before your manual step; drift-check head, don't assume you apply it. (Boxing/F1 as scoped
  need NO migration — market_types already exists.)

### Verification path per phase (how you prove it end-to-end)
1. Probe live Kalshi (public API, from local — read-only) to lock series/ticker/shard: confirm the boxing Poly slug
   carries a date + the `KXBOXING` winner date-key; harvest the F1 `{COUNTRY}GP{YY}` map from settled `KXF1RACE`;
   read per-market `exchange_index`.
2. Measure the boxing draw rate → board re-prices (decision #1).
3. Unit tests + real-market dry-run in box-scratch; differential = baseline; two skeptics; commit/push; present the
   staged deploy; halt.
