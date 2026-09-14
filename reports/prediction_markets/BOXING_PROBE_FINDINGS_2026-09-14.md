# BOXING — live-probe findings + draw measurement (Phase 1)

**Branch `pm-boxing-build-2026-09-14` off prod-live `be2d0c76`. All probes READ-ONLY, public APIs, from LOCAL
(never the box). Kalshi `api.elections.kalshi.com/trade-api/v2`, Poly `gamma-api.polymarket.com`.**

This locks the constants for the boxing matcher and records the draw measurement (a board ruling, HALT item).
Several constants DIFFER from the build brief — recorded here verbatim so they are not re-guessed.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 1. KALSHI SIDE — series, ticker format, shard  (probed 2026-09-14)
═══════════════════════════════════════════════════════════════════════════════════════════════

- **Winner series = `KXBOXING`** (10 open). `KXBOXINGFIGHT` = **0 open + 0 settled = EMPTY** (brief rule #1
  confirmed — a build against KXBOXINGFIGHT would dry-run to zero and read as "no whale activity").
- **Ticker: `KXBOXING-{YYMONDD}{BLOB}-{YESCODE}`.** BLOB = the two fighters' surname-codes concatenated;
  YESCODE = the bet fighter's surname-code. Codes are **VARIABLE-LENGTH surname prefixes (5-6 chars), NOT
  UFC's fixed 3.** The final `-` DELIMITS yescode from blob, so no positional split of the blob is needed
  (group by `(date, blob)`, collect the two yescodes — the UFC index mechanism unchanged).
  - `KXBOXING-26OCT10SANDOVCOLLAZ-SANDOV` yes_sub_title "Ricardo Rafael Sandoval"  (blob SANDOVCOLLAZ)
  - `KXBOXING-26OCT10SANDOVCOLLAZ-COLLAZ` yes_sub_title "Oscar Collazo"
  - `KXBOXING-26OCT10SCHOFIBAHDI-SCHOFI` / `-BAHDI`  (BAHDI = 5 chars -> variable length confirmed)
  - `KXBOXING-26SEP12GARCIAMORALE-GARCIA` "Sean Garcia" (result yes) / `-MORALE` "Abraham Morales" (result no)
- **Binding field: `yes_sub_title` = the fighter FULL name** (populated on open markets). `title` = "{Full Name}
  wins" is ALSO present (UFC-shape) -> matcher prefers `yes_sub_title`, falls back to stripping " wins".
- **`exchange_index = 0` (shard 0)** — same as UFC/MMA. (kalshi_jack shard-0 balance was $277.80 at the
  2026-09-12 snapshot -> boxing is fundable if enabled; that is an enable-time concern, not a build blocker.)
- Two Kalshi market GENERATIONS coexist in settled history (matcher-irrelevant, settlement-only):
  `strike_type=custom` (older; `result`= yes/no, `expiration_value`= winner name) and `strike_type=structured`
  (newer; `result`= **`scalar`**, expiration_value empty). The CURRENT/open markets are `structured` with
  `title`="X wins" + `yes_sub_title` populated -> the live copy path reads yes_sub_title regardless of
  strike_type; the `result` shape only matters to the draw analysis (see §3).

### Method / distance / rounds series — EXIST but EMPTY-of-open and IRRELEVANT (see §2)
`KXBOXINGMOV` (45 settled, 0 open), `KXBOXINGDISTANCE` (7 settled, 0 open), `KXBOXINGROUNDS`/`VICROUND`/`1MIN`
(0/0). MOV carries a first-class **`-DRAW`** market ("Fight ends in a draw/no contest?"). These are NOT built
— there is no Polymarket source to copy from (§2).

═══════════════════════════════════════════════════════════════════════════════════════════════
## 2. POLYMARKET SIDE — slug format, outcome shape, SCOPE  (probed 2026-09-14, tag_slug=boxing, 100 events)
═══════════════════════════════════════════════════════════════════════════════════════════════

- **★ Poly boxing is WINNER-ONLY. 0 of 100 events have >1 market.** Every event is a single 2-way winner
  market. There is **NO Poly go-the-distance / method / draw market to copy** — exactly the UFC
  "round-of-victory: no Poly source" situation. **=> The boxing matcher is MONEYLINE-ONLY.** The brief's
  "62 two-fighter + 11 method" over-counted a method family that does not exist on Poly boxing; dropping
  method/distance is the correct, evidence-backed scope (building them would be dead code with no feed).
- **★ Slug prefixes are HETEROGENEOUS (brief said only `boxing-{A}-vs-{B}`; WRONG on both prefix and `-vs-`):**
  `zuffa` 63, `boxing` 29 (the two two-fighter families = 92/100), then novelty `mvp` 2 / `brand` 2 /
  `the`/`pbc`/`glory`/`will` 1 each. Shape for BOTH copyable families = **`{zuffa|boxing}-{code1}-{code2}-{YYYY-MM-DD}`**
  (NO `-vs-`; opaque codes like UFC's `dan6`). Date = the last `-YYYY-MM-DD` segment.
  - `zuffa-garci1-moral1-2026-09-12` -> Kalshi `KXBOXING-26SEP12GARCIAMORALE` (date + fighters cross-match).
  - `boxing-allen-carty-2026-09-05`  -> Kalshi `KXBOXING-26SEP05ALLENCARTY`.
  - `glory` = GLORY **kickboxing** (different sport) and `mvp`/`brand`/`will` are celebrity/single-fighter
    novelty -> NOT mapped to boxing (a two-fighter matcher cannot touch them; brief's "90% novelty dollars").
- **★ Moneyline outcome = a SURNAME, not the full name** (unlike UFC where outcome = full fighter name).
  `outcomes = ["Garcia","Morales"]`, and the copy signal's `title` carries "Zuffa Boxing: Garcia vs. Morales
  (...)" — both surnames. => the matcher's name bind must match a **surname-only outcome to a Kalshi FULL
  name** (last-token/surname match), with the bout's uniqueness guard preventing a same-surname mis-pick.
  UFC's `match_fighter_name("Garcia","Sean Garcia")` returns FALSE (it also requires a first-token match), so
  a boxing-specific surname-tolerant match is the ONE real code change vs the UFC clone.
- **Categorization gap (brief rule #3):** `category.SLUG_PREFIX_MAP` has NO `boxing`/`zuffa` entry -> boxing
  positions currently derive to `unknown`. The build adds `"boxing":"boxing"` AND `"zuffa":"boxing"` there,
  plus `"boxing"` to `search.CATEGORY_ALLOWLIST`. (ingest is all-categories; this is a SELECTION/surfacing
  change, not an ingest change — R5.)

═══════════════════════════════════════════════════════════════════════════════════════════════
## 3. ★★ THE DRAW MEASUREMENT — board ruling (do NOT inherit the UFC 1% ruling)
═══════════════════════════════════════════════════════════════════════════════════════════════

**Measured Poly-native draw rate = 6 / 92 two-fighter bouts = 6.5%** (Mar–Sep 2026 window, `outcomePrices`
`["0.5","0.5"]` = a draw; 86 decisive `["1","0"]`/`["0","1"]`, 0 unresolved). Independent of any Kalshi
transform — this is Poly's own resolution.

The 6 draws: `boxing-allen-carty-2026-09-05`, `zuffa-macmi-river-2026-06-06`, `zuffa-boyma-stant-2026-05-10`,
`zuffa-carde-alvar1-2026-04-05`, `zuffa-ochoa-serra-2026-03-08`, `zuffa-ramos-perez1-2026-03-08`.

**Corroboration (independent second source):** `boxing-allen-carty` (Poly `[0.5,0.5]`) is one of the Kalshi
`strike_type=structured` "scalar" bouts whose frozen prices were 0.49/0.51 (a market that could not call a
winner = a draw). The Kalshi `KXBOXINGMOV -DRAW` series showed 0/9 YES, but that series covers only the ~9
higher-tier bouts that happened not to draw — the 92-bout Poly sample is the authoritative rate.

**The divergence (why it matters):** on a draw, Poly refunds **~50%** (`[0.5,0.5]`) while Kalshi `KXBOXING`
resolves the copied "Fighter A wins" YES market to **NO = 100% loss**. So a copied boxing moneyline runs a
structural drag vs the whale of ~50% of stake on ~6.5% of bouts ≈ **~3.25% of stake in expected extra loss**
relative to the Poly outcome. UFC accepted this at ~1% (≈0.5% drag). Boxing is ~6× that.

**Kalshi has a hedge instrument but Poly gives no signal to copy it:** `KXBOXINGMOV-{...}-DRAW` is a
first-class YES/NO draw market — but Poly boxing has no draw market, so there is nothing to *copy*; buying it
would be a *synthetic* hedge (new mechanism), not a copy.

**HALT — this is the board's ruling.** Options presented separately.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 4. LOCKED DESIGN (moneyline-only UFC clone) — for the build
═══════════════════════════════════════════════════════════════════════════════════════════════

- New module `trading_corp/data/boxing_poly_kalshi_match.py` (`git add -f` — data/ gitignored). Clone the UFC
  index (date + fighter-pair, code-anchored `labels_code_swapped`), WINNER-ONLY (no distance/method attach).
  Bind Kalshi via `yes_sub_title` (fallback: strip " wins" from title). Poly parse handles `zuffa-`/`boxing-`
  prefixes, extracts date, and treats any slug suffix as a labelled non-moneyline skip. Surname-tolerant name
  match (surname outcome -> full-name Kalshi), with the per-bout uniqueness guard for collisions.
- `execution.py`: import `boxing_poly_kalshi_match as BX`; `_boxing_parse`/`_boxing_match`;
  `MATCHER_ADAPTERS["boxing"]`; new `MarketContext.boxing_index` slot (defaulted -> every other construction
  byte-identical).
- `live_driver.py`: `BOXING_SERIES=("KXBOXING",)`; `fetch_boxing_market_context` (mirror
  fetch_ufc_market_context, winner-only, reads yes_sub_title); register in `CATEGORY_CTX_BUILDERS`; add
  `"boxing"` to `_AUDIT_NAME_CATS` (moneyline surname/code subsequence audit works as-is).
- `category.py`: `SLUG_PREFIX_MAP += {"boxing":"boxing","zuffa":"boxing"}`.
- `search.py`: `CATEGORY_ALLOWLIST += "boxing"`.
- **INERT:** ships behind no armed sub-division existing (the UFC-category precedent). `market_types`
  blank/NULL -> legacy default `("moneyline","total","spread")` (already guaranteed by
  `execution.sub_config_from_row`; a test pins it). Boxing winner uses the standard `moneyline` type; there
  are no new boxing market TYPES (method/distance dropped), so there is no new default-set risk.
