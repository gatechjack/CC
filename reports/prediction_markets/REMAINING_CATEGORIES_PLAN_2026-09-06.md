# Remaining Kalshi-Copyable Categories — Consolidated Plan (RESEARCH ONLY)

**Date:** 2026-09-06 · **Branch:** `pm-remaining-categories-plan-2026-09-06` · **Author:** lead code agent
**Scope:** the 10 remaining copyable categories — **cs2, epl, fed, golf, nba, nfl, nhl, soccer, ucl, wnba**.
Viability + whale supply are SETTLED (not re-asked). The question is **plumbing and matcher reuse**.
**This builds nothing** — it is the document Jack rules on before any build. HALT stands for
deploy/restart/live-DB-write/arm/cap/prod-advance.

> ★ **POST-RULING UPDATE (2026-09-06, after Jack's rulings): see the section
> "═══ POST-RULING RESOLUTIONS & cfb (11th category) ═══" at the BOTTOM** — it resolves fed
> (per-bucket gate, mostly copyable), cs2 (exact-alias + collision handling), soccer tier-2
> (Kalshi covers ~all leagues — the "coverage gap" claim above was a guessed-ticker error, corrected
> there), the **aggregate cap arithmetic**, and **adds cfb as an 11th category (Kalshi DOES carry it —
> the 09-06 "non-Kalshi-copyable" conclusion was wrong)**. Read that section together with this one.

**Probe cost (as required):** Kalshi + Polymarket public APIs were hit **from local IPs (mine + 3
sub-agents), NOT the box** — so **zero load on the live engine's poller**. One read-only box query
(`mode=ro` SQLite, no external API) pulled Poly slugs + a liveness check (all 8 RUNNING, any_alarm=False,
2026-09-06 21:00Z). Total external calls ≈ 70 across 4 machines, spread over ~10 min.

---

## ★★ THE HEADLINE: 10 categories → 4 matcher families. Only 2 are genuinely new.

The copy engine is **already category-agnostic after the match** (`execution.py` `MATCHER_ADAPTERS = {cat:
(parse, match)}`, `CATEGORY_CTX_BUILDERS` keyed by category; the ticker/leg/quote/gate/sizing path is
identical for every category). Adding a category = **a `(parse, match)` pair + a ctx builder + 3 registry
lines + sub-division rows/caps**. Two of the three live matcher shapes absorb 6 of the 10 categories almost
for free, and a generic ticker parser + team maps **already exist** for the US team sports.

| Family | Existing shape? | Categories | Build cost |
|---|---|---|---|
| **1. Structural** (Poly `{lg}-{away}-{home}-{date}` → Kalshi `KX{X}GAME/TOTAL/SPREAD`, join on date+teams) | **YES** = MLB | **nba, nfl, nhl, wnba** (1a) · **epl, ucl, soccer** (1b, variant) | 1a: **cheap** (generalize + team maps, most exist). 1b: **moderate** (new parse front-end + draw + per-league team maps) |
| **2. Title/name join** (Poly title "A vs B" → Kalshi "{competitor} wins", ±1 day) | **YES** = tennis/ufc | **cs2** | **cheap** (reuse tennis matcher + esports alias table) |
| **3. Field / futures** ("Will {name} win {event}?", many outcomes) | **NO — new** | **golf** | **heavy/messy** (tournament lookup table + name normalization + 2 series) |
| **4. Event + bucket** (an economic event, numeric buckets, no competitors) | **NO — new** | **fed** | **moderate** (bucket map; hike side gated) |

**So: 5 categories (nba/nfl/nhl/wnba + cs2) reuse an existing shape ≈ config + maps; 3 (epl/ucl/soccer) are
one structural-variant build dominated by league/team-map breadth; only 2 (golf, fed) need a new matcher
shape.** That is **~3 new/variant matcher builds + 2 reuses** to cover all ten — not ten builds.

**Classification is already done** for all ten (they are in `CATEGORY_ALLOWLIST`; nba/nfl/nhl/wnba/epl/ucl/
cs2/fed have `SLUG_PREFIX_MAP` prefixes; golf/soccer classify via tier-2 gamma tags). So **paper trading
already works** for these once whales are pinned — the missing piece is the LIVE matcher + ctx builder +
arm, not the category itself. (Contrast cfb last session, which needed the prefix added.)

**Funding note (per instruction — noted, not solved):** all 10 are on **Kalshi shard 0** (the 4 live ones
are shard 3). Jack funds shards himself.

**Hard standard applied throughout:** a MISS is acceptable; a WRONG PICK is a STOP; a right-event-WRONG-
market-type (or wrong-bucket) match is a STOP. Each section flags where that risk lives.

---

## GROUPING TABLE (the build count, explicitly)

| Category | Kalshi series (copyable) | Copyable market types | Matcher | Reuse or new | Poly join key |
|---|---|---|---|---|---|
| **nba** | KXNBAGAME (+KXNBATOTAL/SPREAD) | moneyline ✔ (total/spread pending in-season) | Structural (MLB-generalized) | **generalize** | slug teams+date; outcome=nickname |
| **nfl** | KXNFLGAME (+TOTAL/SPREAD) | moneyline ✔ (total/spread pending) | Structural | **generalize** | same |
| **nhl** | KXNHLGAME (+TOTAL/SPREAD) | moneyline (total/spread pending) | Structural | **generalize** | same |
| **wnba** | KXWNBAGAME (+TOTAL/SPREAD) | moneyline (total/spread pending) | Structural | **generalize** (needs WNBA_TEAMS) | same |
| **epl** | KXEPLGAME (+TOTAL/SPREAD) | team-win + **draw** | Structural-variant (soccer) | **new front-end** | slug teams+date+teamcode; outcome Yes/No; `-draw`→`-TIE` |
| **ucl** | KXUCLGAME | team-win + draw (90-min, ET/pens EXCLUDED) | Soccer | **new front-end** | same (strip "Reg Time:") |
| **soccer** | KX{LALIGA,SERIEA,LIGUE1,BUNDESLIGA,MLS,UEL,UECL}GAME | team-win + draw | Soccer | **new front-end + N team maps** | same, per-league series map |
| **cs2** | KXCS2GAME | match winner (BO-agnostic) | Title/name (tennis-reuse) | **reuse** | title "A vs B"; outcome=org name |
| **golf** | KXPGATOUR, KXLIVTOUR | tournament winner (futures) | Field | **NEW** | tournament event + golfer name |
| **fed** | KXFEDDECISION (+ KXCPI* later) | rate-decision bucket (cut/no-change; hike GATED) | Event+bucket | **NEW** | meeting month + bucket |

---

## PER-CATEGORY DETAIL

### GROUP 1a — US TEAM SPORTS: nba, nfl, nhl, wnba  (generalize the MLB matcher)

- **1. Kalshi lists:** `KX{X}GAME` (moneyline, 2 sides, title "{Team} wins", ticker `KX{X}GAME-{YYMMMDD}{HHMM?}{blob}-{code}`) + `KX{X}SPREAD` + `KX{X}TOTAL` (+ team-total, and quarter/half derivatives which are OUT of scope). Confirmed live: NBA `KXNBAGAME-26OCT20OKCSAS-SAS` "San Antonio wins"; NFL `KXNFLGAME-26SEP21NYGLAR-NYG`; WNBA `KXWNBAGAME-26AUG30CONNDAL-DAL`. NHL offseason (no live markets) but same structure + `NHL_TEAMS` map exists. ⚠️ `KXNBATOTAL`/`KXNBASPREAD` **series exist but had no markets at probe time (pre-season)** — that is a timing gap, NOT absence (the exact "wrongly-dropped MLB spread" trap). Full-game total/spread need an in-season probe to confirm the **strike encoding** per sport (MLB uses `N-0.5` half-run; NBA points/NFL points/NHL goals may differ).
- **2. Polymarket lists:** identical to MLB — `nba-{away}-{home}-{date}` outcome=nickname ("Knicks"); `-total-{N}pt{M}` (Over/Under); `-spread-{home|away}-{N}pt{M}` (team). Byte-for-byte the MLB Poly shape.
- **3. Matcher shape:** **the MLB structural matcher, generalized.** `parse_poly_mlb_bet` + `build_kalshi_game_index` + `match_bet` need parameterizing on (slug prefix, `KX{X}GAME/TOTAL/SPREAD` regexes, team map, strike encoder). A **generic `parse_sports_ticker` + `LEAGUE_TEAMS{MLB,NBA,NHL,NFL,MLS}` already exist** (`sports_team_mapping.py`) — the Kalshi side is nearly done. Poly-side parse is the MLB one with the prefix swapped. **Only wnba needs a new team map (~14 teams).**
- **4. The join + disproving case:** join on `(date, frozenset{team_names})`. **No doubleheaders** in these sports → the MLB DH ambiguity vanishes (simpler than MLB). The disproving case is the **date boundary**: a late West-Coast game near UTC midnight where Poly's slug date and Kalshi's ticker date differ by a day (the UFC cross-midnight problem). Kalshi encodes the **game-local** date in the ticker; Poly uses the slug date. Test a late-night NBA/NHL game across the venues before arming (a ±1-day window like tennis is the cheap guard if they diverge). Second case: a **team-code alignment gap** (a code in a Poly slug or Kalshi ticker not in the team map → a MISS, safe, but log it).
- **5. Normalization problem:** team abbreviations differ across venues and the outcome is a **nickname** ("Knicks", "Trail Blazers"). The MLB `resolve_side` already handles nickname→full-name via shared-token matching, and the team map canonicalizes codes → full names. Failure mode: an unmapped/renamed code (e.g. "Utah Hockey Club"→"Utah Mammoth" rebrand; WNBA expansion "Toronto Tempo", "Golden State Valkyries", "PortlandFire" seen in data) → a MISS unless the map is current. **Keep the team maps current; a stale map is a silent miss, never a wrong pick.**
- **6. Settlement divergence:** moneyline includes OT on both venues (a team that wins in OT "wins") — aligned. **Totals include OT** on both — but verify the strike basis (regulation-only vs incl-OT) per sport before shipping totals. NBA/NFL/NHL games don't tie in the moneyline market (OT decides). **Low settlement risk on moneyline; confirm total/spread OT-basis before enabling those types.**
- **Most-likely wrong-pick:** none structural on moneyline (unique date+teams). The realistic error is a **wrong market TYPE** if the Poly total/spread suffix parse or Kalshi strike encoding differs from MLB's — hence: **ship moneyline first, add total/spread per sport only after an in-season strike-encoding probe.**

### GROUP 1b — SOCCER: epl, ucl, soccer  (new front-end, shares the structural join)

- **1. Kalshi lists:** `KX{league}GAME` is **3-way**: `{Team A} wins` / `{Team B} wins` / `Tie is the result` (ticker `...-{CODE|TIE}`). Confirmed: EPL `KXEPLGAME-26SEP06ARSCFC-{ARS|CFC|TIE}`, MLS `KXMLSGAME-...-TIE`. Plus `KX{league}SPREAD` + `KX{league}TOTAL` (goals). **9 league series exist**: KXEPLGAME, KXUCLGAME, KXLALIGAGAME, KXSERIEAGAME, KXLIGUE1GAME, KXBUNDESLIGAGAME, KXMLSGAME, KXUELGAME (Europa), KXUECLGAME (Conference). ★ UCL/UECL titles carry a **"Reg Time:" prefix** the parser must strip.
- **2. Polymarket lists:** per-outcome binaries, **three markets per match**: `{lg}-{away}-{home}-{date}-{teamcode}` "Will {Team} win?" Yes/No, **and** `{lg}-{away}-{home}-{date}-draw` "Will it end in a draw?" Yes/No. Confirmed on a live UCL match (rma/int/draw). "soccer" spans many league prefixes: fl1 (Ligue 1), sea (Serie A), lal (La Liga), uel (Europa), uecl (Conference), bundes, mls; epl/ucl are their own categories.
- **3. Matcher shape:** **structural join, new parse front-end.** Reuses the `(date, teams)` → Kalshi-game resolution, but the parse differs from the US sports: the **team comes from the slug suffix** (teamcode/`draw`), the **leg comes from the outcome** (Yes→buy that side, No→sell/other-leg), and the index must include the **TIE** side. Clean mapping: Poly team-win Yes → Kalshi "{team} wins" YES; Poly `-draw` Yes → Kalshi `-TIE` YES; Poly No → the same market's NO leg. **A league→series map** (epl→KXEPLGAME, fl1→KXLIGUE1GAME, …) + **a team map per league** are the bulk of the work.
- **4. The join + disproving case:** the feared disproving case — **UCL knockout ties decided on ET/penalties** — **DISSOLVES**: Kalshi rule text is explicit and universal ("after 90 minutes plus stoppage time, **does not include extra time or penalties**"); Polymarket "Will X win" is likewise 90-minute. A 1-1 knockout leg where X wins on penalties settles TIE→YES / both teams→NO on **both** venues. Two-legged ties are **per-leg** markets on both. **The real disproving case is the league-coverage gap:** a Poly soccer bet in a **tier-2 league with no Kalshi `KX*GAME` series** (Eredivisie, Scottish Prem, Brazilian Série A, Liga MX) → a permanent MISS (safe). Also a **postponed match** (soccer postponements happen) → a ±1-day window may be needed as in tennis (untested — verify).
- **5. Normalization problem:** **club-name breadth** — many leagues, hundreds of clubs, multi-word names ("Paris Saint-Germain FC", "FC Barcelona", "Real Madrid CF", accented "Olympiakós SFP", "Fenerbahçe SK"). Kalshi codes (ARS/CFC) + a per-league team map on each side; Poly slug codes (liv/new/psg1 — note UCL uses suffixed codes like `psg1`, `rma1`). This is the heaviest normalization job of the ten. Failure mode: an unmapped club → MISS.
- **6. Settlement divergence:** **90-minute regulation on both venues, verbatim-confirmed → aligned, zero divergence** for team-win, draw, and knockout legs. This is the strongest settlement story of the ten.
- **Most-likely wrong-pick:** none on settlement. The risk is a **wrong club** from an incomplete/ambiguous team map (e.g. two clubs sharing a city) — so the team maps must be uniqueness-checked per league.
- **Scope call for Jack:** epl + ucl are single-league (cheap). The **"soccer" category is the multi-league catch-all** — each additional league is a team map. Recommend: ship epl + ucl + the top-5 leagues (La Liga, Serie A, Ligue 1, Bundesliga, MLS) + Europa/Conference; treat tier-2 leagues as known MISSes.

### GROUP 2 — cs2  (reuse the tennis title-matcher)

- **1. Kalshi lists:** `KXCS2GAME` = match/series winner, 2 sides, title "{Org} wins", `yes_sub_title` = full org display name; ticker `KXCS2GAME-{YYMMMDD}{HHMM}{blob}-{code}`. `KXCS2MAPWINNER` is the per-map market (empty at probe; separate). BO1/BO3/BO5 all resolve as the same match-winner market.
- **2. Polymarket lists:** `cs2-{orgA}-{orgB}-{date}[-gameN]`, outcome = **org full name** ("Natus Vincere", "Team Falcons", "100 Thieves"), title "Counter-Strike: A vs B (BO3) - {event}". `-gameN` suffix = a per-MAP bet (out of scope, like MLB props). ~Half of cs2 Poly volume is **tournament futures** (not per-match) — those are a separate futures shape (like golf), not copyable via KXCS2GAME.
- **3. Matcher shape:** **reuse the tennis matcher almost verbatim** — pair-key on the title "A vs B", match to Kalshi `yes_sub_title` "{org} wins", uniqueness-guarded, ±1-day window. **Match on `yes_sub_title` (the full org name), NEVER the ticker blob** (the blob is a per-event abbreviation). No org-code map — an **alias table** (small, <10 entries) handles the display-name diffs. This is the cheapest new category after the US team sports.
- **4. The join + disproving case:** the disproving case is a **cross-UTC-midnight match** (Asia-Pacific slots) where the Kalshi ticker date differs from the Poly slug date → the tennis ±1-day window covers it. Second: **map bets** (`-gameN`) must be filtered to "match winner only" (skip), or a map bet would match the wrong market TYPE.
- **5. Normalization problem:** **esports org display-name aliasing.** Tier-1 orgs are the highest risk (Poly "Natus Vincere" vs Kalshi possibly "NAVI"; "100 Thieves" vs "100T"; "FaZe" vs "FaZe Clan") — and the sub-agent's live sample was all Tier-2/3, so **Tier-1 names are UNVERIFIED**. Orgs also rebrand frequently. Failure mode without the alias table: a MISS (names don't fuzzy-match) — not a wrong pick, because the tennis matcher's uniqueness + both-sides check is wrong-pick-safe. **Verify 5 Tier-1 org names (NaVi, Spirit, MOUZ, FaZe, G2) against live KXCS2GAME before arming.**
- **6. Settlement divergence:** match winner is unambiguous (a forfeit resolves to the advancing team on both) — **low risk**.

### GROUP 3 — golf  (NEW field/futures matcher — the messy one)

- **1. Kalshi lists:** **NOT** per-major series (KXMASTERS/KXPGA are dead). All PGA/majors live under **`KXPGATOUR`** with per-event codes (MAST26, PGC26, USO26, THOC26, THPC26, TRAV26, RBBCAN26, …); LIV under **`KXLIVTOUR`**. One YES/NO market per golfer per tournament: ticker `KXPGATOUR-{EVENTCODE}{YY}-{4charGolfer}`, title "Will {Golfer} win the {Tournament}?".
- **2. Polymarket lists:** 100% tournament-winner **futures** — `will-{golfer}-win-the-{year}-{tournament}` / `{year}-{tournament}-winner-{golfer}`, outcome Yes/No, title "Will {Golfer} win the {Year} {Tournament}?". (0 single-game rows — golf has no per-game notion.)
- **3. Matcher shape:** **NEW — field/futures.** Join key = **(tournament identity, golfer name)**. No date-pair, no teams. Closest existing analogue is fed (event + named outcome), not the head-to-head matchers.
- **4. The join + disproving case:** **tournament mapping is the disproving case, and it is genuinely hard** — Kalshi event codes are **not derivable** from tournament names (RBC Canadian Open = `RBBCAN26`, The Open = `THOC26`, Memorial = `THMTPBW26`), and they **change each season** (2025 `TRAVC25` vs 2026 `TRAV26`). Requires a **season lookup table rebuilt from the Kalshi events endpoint** + fuzzy Poly-tournament-string matching. A wrong tournament map = a wrong-event pick (STOP) → the mapping must be exact-or-skip, never fuzzy-guess.
- **5. Normalization problem:** **golfer-name diacritics** — Kalshi keeps Unicode (Åberg, Muñoz, Højgaard), Poly strips them (aberg/munoz/hojgaard) + capitalization (DeChambeau/Dechambeau). Needs an **NFD-decompose + strip-combining** ASCII normalization on both sides (the tennis/ufc `_norm` already accent-folds — reusable). ~5-10% of the field is affected.
- **6. Settlement divergence:** playoff/WD/DQ aligned on both. 54-hole weather-shortened events settle on the official result (aligned) but with a **timing risk** (Kalshi may resolve late while your copy is locked). Low directional risk.
- **Verdict:** golf is a **new shape AND the messiest build** — two hard problems at once (tournament lookup + name normalization) plus a two-series split (KXPGATOUR/KXLIVTOUR). Recommend **last / its own effort.**

### GROUP 4 — fed  (NEW event+bucket matcher — the odd one, and Jack wants it)

- **What a "matcher" even means here (as asked):** there are **no competitors, no game date, no pair**. The "event" is a **specific FOMC meeting** (or CPI release); the "sides" are **numeric buckets** (rate moves). A match = **(same meeting) × (same bucket)**. None of the three head-to-head shapes applies; this is a distinct design.
- **1. Kalshi lists:** `KXFEDDECISION-{YY}{MON}-{BUCKET}` — **5 mutually-exclusive buckets per meeting**: `H26` (Hike >25bps), `H25` (Hike 25), `H0` (maintain / "Fed maintains rate"), `C25` (Cut 25), `C26` (Cut >25). Event code e.g. `KXFEDDECISION-26JAN`.
- **2. Polymarket lists:** per-bucket binaries — "No change in Fed interest rates after {Month} meeting" / "Fed decreases by 25 bps after {Month}" / "Fed decreases by 50+ bps" / "Fed increases by …", outcome Yes/No. Also **Fed-political noise** ("Will Trump nominate Judy Shelton as Fed chair?") that must be excluded.
- **3. Matcher shape:** **NEW — event + bucket.** Parse the Poly slug/title → (meeting month/year, bucket, direction); map bucket → Kalshi code; fetch `KXFEDDECISION-{mtg}` markets; place the matching bucket's yes/no leg.
- **4. The join + disproving case — the WRONG-BUCKET kill shot:** cut side and no-change map **1:1** (no change→H0, 25bps cut→C25, 50+bps cut→C26). **But the HIKE side is a right-event-wrong-bucket STOP:** Poly's "**25+ bps increase**" is a *single* bucket spanning both Kalshi `H25` (exactly 25) and `H26` (>25). Copying it to either is a *different bet* — if the Fed hikes 50, a Poly "25+ increase" whale wins but a copy on H25 loses. **Ruling needed (recommend): copy only cut + no-change buckets; label hike bets a SKIP** until Poly is confirmed to offer separate 25 / >25 hike buckets for the meeting.
- **5. Normalization problem:** **meeting extraction** — get (month, year) from the Poly slug/title and build `{YY}{MON}` (e.g. "January 2026"→`26JAN`). ★ Watch the off-by-one: Poly "after {Month}" = the meeting held **in** that month, not the next. And the **scope filter** (include: no-change/decreases-by/increases-by/bps/rate + a month; exclude: nominate/chair/governor/appoint) to drop Fed-political markets.
- **6. Settlement divergence:** both resolve off the official FOMC decision — **aligned**; the only risk is the bucket-granularity mismatch above, which is a matching problem, not a settlement one.
- **CPI (note):** `KXCPIYOY`/`KXCPICORE` are a parallel opportunity with a **range-threshold** shape (different again). Poly lists CPI bets. **Flag as a later, separate workstream** — do not fold into the fed rate-decision build.

---

## WHAT IS BOILERPLATE vs GENUINELY UNIQUE (so the real work is visible)

**Boilerplate — the same shape EVERY category (≈ a day of mechanical work each, mostly copy-paste):**
- **ctx builder** — ~90% a copy of `fetch_tennis_market_context` (fetch OPEN+SETTLED for the series →
  `_market_quote_dict` → `_merge_raw_market_fields` → a category index builder → `MarketContext`). Differs
  only in the series list + which index builder it calls.
- **registry** — 3 lines: `MATCHER_ADAPTERS[cat]=(parse,match)`, `CATEGORY_CTX_BUILDERS[cat]=builder`,
  and the `*_SERIES` constant.
- **sub-division rows + caps** — `pm_subdivision` row per (account, category) with the standard caps
  (5 contracts, 50 orders/day, $150 daily/open, $5.50/order, 2c slippage, 0.75 liquidity), `market_types`,
  attach a whale, arm. Identical pattern to the tennis create+attach+arm.
- **classification** — ALREADY DONE for all ten (allowlist + prefix/gamma-tag). (Verify golf/soccer gamma-tag
  coverage is complete; a few fed slugs without a `fed-` prefix may sit in `unknown` — a classification
  gap to check, not a matcher problem.)

**Genuinely unique per category — the ONLY parts that need real design/testing:**
- **the parse front-end** (Poly slug/outcome/title → structured bet) and **the match** (join to the Kalshi
  index). For 1a this is the MLB parse with a swapped prefix; for 1b, cs2 it is small; for golf, fed it is new.
- **the maps** — team maps (US sports: exist except wnba; soccer: N leagues, the heavy one), esports alias
  table (cs2, small), tournament lookup table (golf, rebuilt seasonally), bucket map (fed, tiny).
- **the disproving-case guard** — date-boundary window (US sports, cs2), draw/`-TIE` + "Reg Time:" strip
  (soccer), tournament exact-map (golf), hike-bucket gate (fed).
- **strike encoding** for total/spread (US sports — verify in-season; not needed if moneyline-only).

---

## ★ EVERY QUESTION THAT NEEDS JACK (gathered — answer together)

1. **Market types per new category — moneyline-only, or +total+spread?** US team sports and soccer carry
   totals/spreads on both venues and our whales bet them; matching MLB (3-dim) captures more copies but adds
   the strike-encoding verification per sport. **Recommendation: moneyline first for every category (fast,
   fully confirmed now); add total/spread for the US team sports + soccer in a second pass after an in-season
   strike-encoding probe.** (Tennis/ufc are moneyline-only precedent.)
2. **Soccer breadth — which leagues in the "soccer" category?** epl + ucl are their own categories (cheap).
   The catch-all "soccer" spans ~9 leagues, each a team map. **Recommendation: epl + ucl + top-5 (La Liga,
   Serie A, Ligue 1, Bundesliga, MLS) + Europa/Conference; tier-2 leagues = accepted MISSes.** Also: **confirm
   copying the DRAW leg** (Poly `-draw` → Kalshi `-TIE`) is in scope (recommended: yes, it's clean).
3. **fed — restrict to cut + no-change buckets?** The hike side (Poly "25+ bps increase") cannot map cleanly
   to Kalshi's split H25/H26 → a wrong-bucket STOP. **Recommendation: copy cut + no-change; SKIP hike bets
   (labelled) until Poly offers separate 25 / >25 hike buckets.** And: **CPI in scope now, or defer?**
   (Recommendation: defer CPI — different threshold shape.)
4. **golf — build now or defer?** It is the only category that is BOTH a new shape AND messy (tournament
   lookup table + diacritic name normalization + KXPGATOUR/KXLIVTOUR split). **Recommendation: build it LAST,
   as its own effort;** consider a first cut of **majors only** (4 tournaments, reliably covered) before the
   full PGA/LIV calendar.
5. **cs2 — accept the tennis-matcher reuse + a small esports alias table?** Requires verifying ~5 Tier-1 org
   display names against live Kalshi before arming (the sample was Tier-2/3). **Recommendation: yes**;
   wrong-pick-safe by construction (uniqueness + both-sides), worst case a MISS.
6. **Caps / arming** — same standard caps as mlb/ufc/tennis for every new sub-division? (Assumed yes; flag any
   category you want sized differently — e.g. cs2/soccer liquidity may be thinner.) **HALT item: I will not
   create sub-division rows, arm, or change caps without your go.**

---

## RECOMMENDED BUILD ORDER (by efficiency + dependency, since all ten are happening)

1. **Generalize the structural matcher + ship the US team sports (nfl → nba → nhl → wnba).** Cheapest,
   highest reuse (team maps + generic ticker parser exist), moneyline fully confirmed. **NFL is in-season NOW**
   (immediate whale activity); NBA + NHL open in October; WNBA season is ending (config is trivial — ship it
   with the group even if quiet). This build also produces the shared join engine soccer reuses.
2. **cs2** — independent, cheap (reuse the tennis matcher + alias table). Can run in parallel with (1).
3. **Soccer (epl → ucl → the multi-league "soccer").** Depends on the structural engine from (1); the work is
   the new parse front-end + draw/`-TIE` + per-league team maps. Settlement is the cleanest of the ten.
4. **fed** — new bucket matcher; small once the cut/no-change scope + hike gate are ruled. No dependency.
5. **golf** — LAST. New field shape + the messiest normalization; do it deliberately, majors-first.

Second pass (after moneyline is live and proven): **add total/spread** to the US team sports + soccer, gated
on an in-season strike-encoding probe per sport.

---

## VERIFICATION ITEMS STILL OPEN (not blockers — flagged honestly)
- **Total/spread strike encoding** for nba/nfl/nhl/wnba + soccer (series exist; markets were unpopulated
  pre-season → probe in-season; do NOT read the empty probe as "not listed").
- **Date-boundary** on a late West-Coast US game and an Asia-Pacific cs2 match (does Kalshi ticker date vs
  Poly slug date ever differ by a day → need the ±1-day window).
- **Soccer postponement** behaviour (±1-day window as in tennis? untested).
- **cs2 Tier-1 org display names** on live KXCS2GAME (verify NaVi/Spirit/MOUZ/FaZe/G2 before arming).
- **Golf season lookup table** must be rebuilt from the Kalshi events endpoint each season.
- **Classification completeness** for golf/soccer (gamma-tag) and any prefix-less fed slugs sitting in `unknown`.

---

# ═══ POST-RULING RESOLUTIONS & cfb (11th category) — 2026-09-06 ═══

Jack ruled: **moneyline-only first pass**; **soccer INCLUDE tier-2 (do a pass)**, draw leg IN scope;
**golf last**; **create sub-divisions with standard caps but write NO arm rows** (every new category
comes up disarmed, CLI-armed when he chooses); and asked for the **aggregate cap arithmetic** before he
arms the second category. Two categories needed more research before he rules (**fed**, **cs2**), and he
added **cfb as an 11th** after learning Kalshi lists college football. All resolved below (probes still
local-IP only, 0 box-poller load; one read-only box DB query `cc/pm_batch_research_ro.*`).

## ★★ cfb — Kalshi DOES carry college football. The 09-06 "non-Kalshi-copyable" conclusion was WRONG.
**Why it was wrong:** the 09-06 cfb scoping was framed by the task itself as a *non-Kalshi-copyable*
category ("YOU ARE NOT INVESTIGATING ... ANY OTHER EXECUTION VENUE") — so Kalshi was never probed; the
premise was taken as given. It was the exact standing trap (absence-from-a-guessed-premise, not evidence).
**Verified now by probing what the markets ARE** (searched the full series inventory by title/tag, not a
guessed ticker): Kalshi lists **`KXNCAAFGAME`** ("College Football Game", per-game 2-way, title "{Team}
wins", ticker `KXNCAAFGAME-26SEP19PURUCLA-UCLA`), **`KXNCAAFSPREAD`**, **`KXNCAAFTOTAL`** — the exact
MLB-shape trio — plus a large `KXNCAAF*` family (conferences, CFP, awards) and separate `KXNCAAFCSGAME`
(FCS) / `KXNCAAFD3GAME` (DIII).
- **Six questions:** (1) Series/types: `KXNCAAFGAME`/`SPREAD`/`TOTAL`; moneyline is the clean 1:1. (2)
  Poly: `cfb-{away}-{home}-YYYY-MM-DD[-suffix]`, outcome = team name (same as the US pro sports). (3)
  **Matcher family: STRUCTURAL, with nfl/nba/nhl/wnba** — confirmed, not assumed. Nearly-free matcher.
  (4+5) **Disproving case = TEAM NAMING, and it is the biggest normalization surface in the whole batch:**
  our DB has **272 distinct Poly team codes across 2,631 cfb game rows** — because Poly aliases each school
  several ways (`ala`/`bama`, `ariz`/`arz`/`azst`, `app`/`applst`, `ark`/`arkst`/`arst`, `af`/`airf`,
  `ball`/`ballst`) and includes FCS opponents in early-season games. Kalshi uses "San Jose St.", "Fresno
  St.", "Northern Illinois", "New Mexico St." — with **~130+ FBS programs, colliding abbreviations (shared
  initials), "State" variants, and Miami-FL vs Miami-OH / Ole Miss vs Mississippi**. There is **no existing
  team map** (unlike NBA/NFL/NHL). This map — two-sided, ~130 schools, ~272 Poly aliases, uniqueness-checked
  — IS the cfb work, and it is where a WRONG PICK would come from. Build exact-map + uniqueness-guard;
  unmapped code → safe MISS. (6) Settlement: college OT differs from the NFL (multiple OT possessions), but
  the moneyline market just needs a WINNER — aligned on both venues; watch cancelled/weather-shortened
  games (rare) and confirm total/spread OT-basis in the second pass.
- **What it means for the paper-lane work already done:** **NOT wasted — it runs AHEAD of the live lane.**
  cfb classification (the `cfb` prefix) shipped last session, so cfb already paper-trades pinned whales and
  the gamma-graded forward record is accumulating. If it goes live, that record just becomes the honest
  screening evidence that runs ahead of live trading (same as every other category). What remains for LIVE
  cfb = the structural matcher + the ~130-team map + ctx builder + registry + a sub-division — the same
  boilerplate as the others, plus the heavy team map.
- **Record correction (done in memory):** cfb is a normal **Kalshi-copyable** category; the
  "non-Kalshi-copyable" framing and the deferred-Robinhood question are **void**. cfb slots into the
  **structural group with the US team sports**; its team map makes it the heaviest of that group, so
  sequence it **after** nba/nfl/nhl/wnba (whose maps exist) but within the same build.

## ★ fed — RESOLVED: hikes are MOSTLY PRECISE; the ambiguity is a per-bucket, match-time-detectable gate.
Pulled 263 distinct fed markets from our DB (+50 fed-ish rows misclassified into `unknown` — a
classification gap). Classification: **POLITICAL 66, cut_precise 57, hike_precise 36, no_change 36,
cut_coarse 16, hike_coarse 13, other 39.**
- **Does Poly always phrase hikes coarsely? NO.** Hikes are **precise 36 vs coarse 13** ("Fed increase by
  25 bps" / "by 50 bps" are the majority; "increase by 25+ bps" is the minority). Same on cuts (57 precise
  vs 16 coarse). **The initial "gate the whole hike side" was too pessimistic.**
- **The clean 1:1 map (given the Fed moves in 25 bp increments, so ">25" ≡ "50 or more"):**
  Poly "no change"→**H0**; "cut 25 bps"→**C25**; "hike 25 bps"→**H25**; "cut 50+ bps"→**C26**; "hike 50+
  bps"→**H26**. These are exact-bucket matches.
- **The AMBIGUOUS phrasings to GATE (a labelled skip, never a guess):** the **coarse "25+ bps"** (spans
  H25+H26 / C25+C26 — the original kill shot) AND **exact "50 bps"/"75+ bps"** (Kalshi has no exact-50
  bucket, only the ">25" catch-all → a precise-50 Poly bet is narrower than C26/H26). Also the multi-meeting
  parlays in "other" (Pause-Pause-Pause), and the 66 political markets.
- **★ Is the ambiguity detectable AT MATCH TIME? YES.** The bps number + the modifier ("+", "or more",
  "at least") are in the slug/title; the matcher parses (direction, magnitude, exact-vs-coarse) and gates
  the ambiguous phrasings while copying the exact-bucket ones. **A gate, not a blind matcher.**
- **Cut + no-change really 1:1?** no-change→H0 and cut-25→C25 are exact 1:1; but "cut 50 bps exact" and
  "cut 25+ coarse" are NOT (checked, not assumed) — so it is a **per-bucket** gate, not "the whole cut side
  is clean."
- **Recommendation:** build fed with the per-bucket map above; copy {no-change, ±25-exact, ±50+-coarse};
  SKIP {±25+-coarse, exact-50/75+, parlays, political}. Fix the 50 `unknown`-bucket classification gap
  (the prefix-less "no-change-in-fed-..." slugs) so those markets are seen. **CPI deferred** (separate
  threshold shape). **This is a better answer than skipping the hike side: most fed markets are copyable.**

## ★ cs2 — RESOLVED: exact-alias lookup (never fuzzy); small table, high churn, real collision surface.
Pulled **420 distinct Poly org names** from our DB (esports has a very long tail). Cross-referenced with
live Kalshi `KXCS2GAME` `yes_sub_title` (full display names).
- **How many orgs / how many need aliases:** both venues use **full display names**, so most **align
  exactly** — the alias table is **SMALL** (the handful where display forms differ, e.g. Kalshi possibly
  "NAVI" vs Poly "Natus Vincere"; "The MongolZ" spacing). It does **NOT** need 420 rows. **Volatility is
  HIGH** (rebrands, sponsor prefixes, roster churn) — but a rebranded/new org that is not yet aliased just
  **MISSES safely** (never a wrong pick), so the table is low-maintenance by design, not a treadmill.
- **★ THE WRONG-PICK / Cerundolo-brothers case IS present and it decides the design:** the org list is full
  of **same-stem distinct entities** — `ENCE` / `ENCE Academy` / `ENCE Prospects`; `FURIA` / `FURIA fe`;
  `BIG` / `BIG Academy`; `Spirit` / `Spirit Academy`; `MOUZ` / `MOUZ NXT`; `G2` / `G2 Ares`; `NAVI Junior`
  vs `Natus Vincere`; `9INE` vs `9z`; plus `ex-<org>` variants. A **fuzzy** first-token match would conflate
  a main roster with its academy → a WRONG PICK. **Therefore: EXACT-normalized-name match (case/punct/accent
  fold) + the small alias table, and treat `Academy`/`Prospects`/`Junior`/`NXT`/`ex-` as DISTINCT entities —
  never fuzzy-collapse.** The tennis pair-key's uniqueness + both-sides guard already makes it wrong-pick
  safe (both orgs must match and be unique in the window), so cs2 = **reuse the tennis matcher, swap the
  name-canon for exact-alias, add the ±1-day window.** Confirmed low-risk to build; verify ~5 Tier-1 org
  display names (NaVi/Spirit/MOUZ/FaZe/G2) against a live Kalshi major before arming (today's sample was
  Tier-2/3). Map bets (Poly `-gameN`) and tournament futures (~half of cs2 volume) are skipped.

## ★ soccer tier-2 pass — RESOLVED: Kalshi covers ~ALL leagues; the sub-agent's "coverage gap" was a
##   guessed-ticker error (the exact lesson). No league is unmatchable for lack of a series.
Searched the full Kalshi series inventory for **every `*GAME` soccer series** (not a guessed ticker):
**~100+ soccer leagues + cups + internationals** exist, INCLUDING every "tier-2" league the sub-agent
reported absent — **`KXEREDIVISIEGAME`** (Eredivisie), **`KXSCOTTISHPREMGAME`** (Scottish Prem),
**`KXBRASILEIROGAME`** (Brazilian Série A), **`KXLIGAMXGAME`** (Liga MX) — plus EFL Championship, Turkish
Süper Lig, Saudi Pro League, Liga Portugal, Belgian/Danish/Swedish/Norwegian/Polish/Czech/Croatian/Serbian/
Greek/Swiss top flights, MLS/USL, Argentine/Chilean/Ecuador/Peru/Uruguay/Venezuela, Japan/Korea/China/Thai/
Indonesia/Malaysia/Singapore/Vietnam/UAE/Egypt/Israel, and cups (FA Cup, Copa del Rey, Coppa Italia, DFB
Pokal, Coupe de France, EFL Cup, KNVB/Scottish/Serbian cups) + internationals (World Cup, UEFA Nations
League, AFCON, Copa Libertadores/Sudamericana, CONCACAF, Club World Cup, friendlies).
- **The sub-agent guessed `KXUEFAGAME`/`KXUELAGAME`, got empties, and declared tier-2 leagues absent** —
  the same "absence from a guessed ticker name is not evidence of absence" mistake that produced the cfb
  error and (per Jack) the mis-probed cbb. **Corrected: essentially no Polymarket soccer league lacks a
  Kalshi `KX*GAME` series.**
- **So the tier-2 pass answer:** they **come in** — the limiter is not Kalshi coverage, it is **our
  per-league TEAM MAP effort** (each league ≈ 20 clubs). Build order should follow **Poly whale volume by
  league**; a league we choose not to map yet is a **deliberate, listed deferral (with its reason:
  "team map not built"), NOT an unmatchable market.** Enumerating the exact set of Poly soccer league
  prefixes our whales bet (fl1/sea/lal/uel/uecl/… + the tail) is a one-query build-time step. **The only
  genuine miss is a Poly soccer bet in a league Kalshi does not list at all** — from this sweep that set is
  ~empty for anything a whale would plausibly bet.

## ★★ AGGREGATE CAP ARITHMETIC — what a busy day actually looks like (real numbers, as asked)
The **$150 daily and 50-order caps are per ACCOUNT**, summed across ALL of that account's sub-divisions
(SW11: the account aggregate binds BEFORE any per-category cap). Real consumption from `pm_subdivision_order`
(128 filled entry orders / 11 account-days — PM is young, so directional):

| Scope | daily $ (median) | daily $ (peak) | daily orders (median) | daily orders (peak) |
|---|---:|---:|---:|---:|
| **mlb** (the busy one) | $44.30 | **$61.85** | 16 | **24** |
| atp | $21.38 | $23.05 | 10 | 11 |
| wta | $5.45 | $9.00 | 2 | 2 |
| **kalshi_jack account** | $38.80 | **$61.05** (all mlb) | 14 | **24** |
| **kalshi_karen account** | $21.65 | $44.45 (mlb+atp+wta) | 7 | 17 |

- **One busy category ≈ $44–62 and 16–24 orders/day.** Matches your mlb ~$28-typical/~$61-peak read.
- **The caps bind at ~3–4 concurrently-active categories.** Two mlb-like categories on a mutual busy day
  ≈ **$90–120 / ~40 orders** — already brushing the 50-order cap. **NFL is in-season NOW, so nfl + mlb is
  already two;** add nba + nhl in October and a busy autumn day is **~$150–180 / ~50–70 orders → OVER both
  account caps.** A peak Saturday with nfl + cfb + nba + nhl + a soccer slate all active would exhaust the
  $150/50 many times over if every category were mlb-active.
- **★ The consequence to flag: arming more categories does NOT multiply throughput — past ~3–4 active
  categories they COMPETE for one $150 / 50-order account budget.** And the intra-cycle order is
  **ALPHABETICAL** (atp, cfb, cs2, epl, fed, golf, mlb, nba, nfl, nhl, soccer, ucl, ufc, wnba, wta —
  inherited from `ORDER BY category`, nobody chose it), so near the cap the **early-alphabet categories win
  and the late ones (soccer, ucl, ufc, wnba, wta) get STARVED** on a busy day. This is the tennis
  aggregate-binding finding, now at 12–15× scale.
- **Recommendation (you are not ruling caps now — this is the arithmetic you asked for before arming #2):**
  either (a) **raise the account caps** proportional to the number of armed categories (e.g. scale $150→ N×
  a per-category budget), or (b) **accept starvation** and make the cycle order fair (round-robin / value-
  ranked instead of alphabetical) so it is not always wta/ufc that lose, or (c) **per-category sub-caps**
  under a raised account cap. Since **nfl+mlb already = two active categories today**, this decision is live
  the moment nfl arms. I will not change any cap without your ruling.

## Updated grouping (11 categories)
| Family | Categories | Cost |
|---|---|---|
| Structural (=MLB) | nba, nfl, nhl, wnba, **cfb** | cheap (maps exist) except **cfb = ~130-team map, heaviest** |
| Structural-variant (soccer) | epl, ucl, soccer (~all leagues, per-league team maps) | moderate; breadth-bound |
| Title/name (=tennis) | cs2 | cheap (reuse + exact-alias table) |
| Field/futures (NEW) | golf | heavy/messy (last) |
| Event+bucket (NEW) | fed | moderate (per-bucket gate; mostly copyable) |

**Updated order:** nfl (in-season) → nba/nhl (Oct) → wnba → **cfb** (same family, after the maps that exist)
→ cs2 (parallel) → soccer (epl/ucl → league tail by whale volume) → fed → golf last. **Arm nothing** at
build; each comes up disarmed, you CLI-arm — and **rule the cap question before arming the second active
category (nfl, imminent).**

