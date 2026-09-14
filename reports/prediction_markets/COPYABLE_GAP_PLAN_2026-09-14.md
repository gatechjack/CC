# PM COPYABLE-MARKET GAP — INVESTIGATION + RANKED PLAN (2026-09-14)

Read-only investigation. Build nothing until Jack rules the order. 30 sub-divisions armed and trading; engine PID
370246; STOP = `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`.

## 0. Orientation + git + the measurement caveats you must read first

- **Support verified from code** (`COPYABLE_MARKET_TYPES`): mlb / nfl / cfb / nba / nhl / wnba =
  `(moneyline, total, spread)`; ufc = `(moneyline, go_the_distance, method_finish, method_victory)`; soccer(all
  leagues) / tennis(atp,wta) / cs2 = `(moneyline,)`; fed = `(bucket,)`. The spread leg handling is correct:
  underdog +X.5 → NO on the favourite's Kalshi market (`leg = "yes" if out_side == anchor_side else "no"`,
  mlb_poly_kalshi_match.py:188; same in the structural matcher).
- **Git**: `origin/prod-live` = `8f35f254`; the brief's `5e03b7a2` is its ancestor (two legacy-PM-removal commits on
  top touch only `main.py`/`strategies.yaml`; every PM matcher/driver/execution file is byte-identical). The box
  `git rev-parse` came back empty via the runner (git not on the non-login PATH / graft-based tree) — box runs
  grafted files per the transition doc, not a clean commit; immaterial to this analysis.
- **★ THREE MEASUREMENT ARTIFACTS caught (suspect-the-measurement-first) — the raw first-pass numbers over-state the
  gap; corrected numbers below:**
  1. **Soccer** encodes the 3-way moneyline as a `-{team}`/`-draw` slug SUFFIX (`epl-ars-bou-2026-04-11-ars`), so a
     naive "empty-suffix = moneyline" classifier reads ALL soccer moneyline as uncopied. Soccer's "100% uncopied"
     is FALSE — it is mostly copyable moneyline. Soccer is moneyline-only-by-design and outside the 5 mapping docs;
     I exclude it from firm conclusions.
  2. **wnba** `-moneyline` explicit suffix is a STALE 2025 format (2025-05-19..08-22); current wnba uses empty-suffix
     (2025-08-23→now). The $56k it showed as "uncopied" is really copied moneyline. wnba real gap ≈ $0.
  3. **ufc** no-date slugs ($614k roster / $1.6M universe) are NOVELTY/meta, not missed moneyline:
     `will-<A>-fight-<B>-next` (matchmaking), `will-the-announcers-say-…`, `will-trump-dance-during-ufc-…`. UFC has
     NO buildable gap (rounds ruled out, RoV no Poly source, novelty uncopyable).

## 1. HEADLINE — the prize is small for TODAY's whales; the honest ordering is by measured volume

Current-roster coverage by whale COST BASIS is already **98.9% mlb, ~92% nfl, 97.7% cfb, 99.0% nba, 100% nhl,
~100% wnba, ~88% ufc** (ufc "gap" is ruled-out rounds + novelty). The uncopied remainder is dominated by two
buckets that are *bad builds*: **season FUTURES** (highest $, worst effort/risk) and **player props** (name-join
hazard). The single biggest *buildable, low-hazard* family in the live roster is **MLB "Run in First Inning"
(NRFI/RFI): $255k whale cost basis across 153 positions.** Because we copy FIXED size (~$2–3/bet, 5 contracts),
$255k of whale stake is ~**$300–450 of additional stake we would actually deploy** — so position COUNT, not whale
cost, is the honest proxy for our opportunity. **Kalshi lists essentially every family** (3766 sports series;
`KXMLBRFI`, `KXMLBF5*`, `KXNFL1H*`, `KXNBA1H*`, `KXNCAAF1H*`, `KXNBASERIES`, player-prop and futures series all
confirmed live) — availability is NOT the constraint anywhere; **whale volume, matcher effort, and hazard are.**
The larger volume lives only in the **attach-later universe** (e.g. nba futures $8.8M, nhl futures $2.8M, nfl
futures $1.7M, mlb RFI $1.3M) — and there too, the biggest numbers are futures, the worst build. **Net: unless you
plan to attach many more whales, the ROI of new families is low; the one build with a clean cost/benefit is MLB
RFI.** This mirrors the spread/total lesson (a family that maps beautifully but nobody bets is not a gap).

## 2. PER-CATEGORY FAMILY TABLE

Volume = whale COST BASIS ($) / POSITION COUNT, from `pm_closed_position` for the current roster (attached∪pinned)
and the full whale universe. "Kalshi" = confirmed live series ticker. Supported families omitted from detail.

### MLB (roster: 21 whales, 7675 pos, $32.0M — 98.9% copyable)
| Family | Supported | Kalshi series | Roster $/n | Universe $/n | Hazard |
|---|---|---|---|---|---|
| moneyline / total / spread | YES | KXMLBGAME/TOTAL/SPREAD | $31.6M / 7467 | — | (live) |
| **Run in 1st inning (NRFI)** | no | **KXMLBRFI** (`-<stem>`, yes="Over 0.5 runs 1st") | **$255k / 153** | **$1.31M / 1577** | **polarity: Poly `-nrfi` "No Run" ↔ Kalshi RFI NO** |
| **First 5 innings (F5 win/spread/total)** | no | **KXMLBF5 / F5TOTAL / F5SPREAD** (share stem; F5 win is 3-way incl `-TIE`) | **$56k / 22** | **$423k / 682** | wrong-market: F5 must NEVER hit full-game strike; TIE outcome |
| Player props (HR, K/strikeouts) | no | KXMLBKS etc. | ~$12k / ~15 | ~$11k / 13 | player-name join (no code anchor) |
| Team total | no | KXMLBTEAMTOTAL (`-<team><N>`) | ~$0 | small | team + strike compound |
| Extra innings | no | KXMLBEXTRAS | ~$0 | $3 / 1 | none, but ~0 volume |
| Futures (WS/pennant/div/awards) | no | KXMLB, KXMLBAL, KXMLBALMVP… | $1.9k / 3 | $1.12M / 613 | no game/date; months-out settlement + exposure cap |

### NFL (roster: ~18 whales, ~1975 pos, ~$13.1M — ~92% copyable)
| Family | Supported | Kalshi | Roster $/n | Universe $/n | Hazard |
|---|---|---|---|---|---|
| moneyline / total / spread | YES | KXNFLGAME/… | $12.0M / 1775 | — | (live) |
| **Futures (SB / conf / div / playoffs)** | no | KXNFLAFCCHAMP, KXNFL1SEED… | $760k / 44 | $1.74M / 530 | no game/date; months-out settlement + exposure cap |
| "combined points" question slugs + parlays | no | (n/a — no game stem) | $200k / 130 | — | UNMATCHABLE (standalone Poly questions, no game key) |
| **First half (1H win/spread/total)** | no | **KXNFL1H / 1HSPREAD / 1HTOTAL** (share stem) | **$45k / 14** | **$206k / 158** | wrong-market: 1H must NEVER hit full-game strike |
| Player props (yards) + TD scorer | no | KXNFLANYTD, KXNFL1HTD, yards series | ~$2k / ~6 | $110k / 32 | player-name join |
| Quarter (1Q–4Q) | no | KXNFL1Q…4Q | $526 / 3 | $6k / 8 | wrong-market + tiny volume |
| Team total | no | KXNFL1HTEAMTOTAL… | $8 / 1 | $7k / 14 | team+strike + ~0 volume |

### CFB / NCAAF (roster: 10 whales, 329 pos, $5.77M — 97.7% copyable)
| Family | Supported | Kalshi | Roster $/n | Universe $/n | Hazard |
|---|---|---|---|---|---|
| moneyline / total / spread | YES | KXNCAAF…/GAME | $5.64M / 310 | — | (live) |
| Quarter (1Q/4Q win) | no | KXNCAAF1Q…4Q | $104k / 8 | $109k / 11 | wrong-market; tiny count |
| **First/second half** | no | **KXNCAAF1H / 2H (+spread/total)** (3-way, `-TIE`) | **$28k / 11** | **$95k / 118** | wrong-market: 1H/2H must NEVER hit full-game |
| Team total | no | KXNCAAFTEAMTOTAL | ~$0 | $17k / 56 | team+strike + low volume |
| Futures (natl champ / conf / Heisman / CFP) | no | KXCFB…, KXNCAAFQF… | ~$0 | (large) | no game/date; months-out |

### NBA (roster: 12 whales, 8689 pos, $37.3M — 99.0% copyable) — NOTE: NBA offseason, no live markets to dry-run now
| Family | Supported | Kalshi | Roster $/n | Universe $/n | Hazard |
|---|---|---|---|---|---|
| moneyline / total / spread | YES | KXNBA… | $36.9M / 8437 | — | (live) |
| Player props (pts/reb/ast/combos) | no | KXNBAPTS/REB/AST/PRA/PR/PA | $98k / 141 | $166k / 382 | player-name join (no code anchor) |
| **First half (1H win/spread/total)** | no | **KXNBA1HWINNER/1HSPREAD/1HTOTAL** (+ 1Q–4Q, 2H) | **$95k / 65** | **$494k / 455** | wrong-market: 1H must NEVER hit full-game |
| Futures (champ / conf / MVP / cup) + novelty | no | KXNBASERIES, KXNBA…MVP… | $179k / 46 | $8.8M / 1013 | no game/date; months-out; biggest $ but worst build |
| Series winner (playoffs) | no | KXNBASERIES(+SPREAD/GAMES) | ~0 (offseason) | (playoff-only) | no game/date; playoff-window-only |

### NHL (roster 5 whales, $5.0M): **0 uncopied.** Universe futures $2.8M (bad build). Nothing to do.
### WNBA (roster 15 whales, $3.15M): uncopied ≈ $730 (props). Effectively fully covered.
### UFC (roster 19 whales, $7.52M): **no buildable family** — rounds O/U $241k RULED OUT (2:30 settle inversion),
RoV no Poly source, $614k no-date slugs are novelty. CLOSED.
### Soccer / tennis / cs2 (moneyline-only, outside the 5 docs): total/spread/props exist and whales bet them, but
soccer classification here is unreliable (caveat 0.1) and these are out of the mapping-doc scope. A soccer
total/spread build is a *separate* conversation, not part of this task.

## 3. RECOMMENDED ORDER (volume vs effort) — with explicit NOs

**BUILD, in this order:**
1. **MLB "Run in First Inning" (NRFI/RFI).** Best ratio in the whole analysis. Highest non-futures volume ($255k /
   153 roster; $1.31M / 1577 universe), LOWEST effort/hazard: one binary market per game, no strike, no team-side;
   `KXMLBRFI-<stem>` reuses the existing MLB date+teams join verbatim. The ONLY hazard is Yes/No **polarity** (Poly
   `-nrfi` "No Run" ↔ Kalshi RFI "Over 0.5 runs" NO) — deterministic and provable in the dry-run. Effort ≈ the
   spread/total add: one new `market_type='first_inning_run'`, a slug parse for `-nrfi`(/`-yrfi`), a stem-join to
   KXMLBRFI, a leg rule. Ships INERT behind `market_types` (§5).
2. **First-half (structural: nfl + cfb now; nba when the season opens).** Modest volume (nfl $45k/14, cfb $28k/11
   roster; $206k+$95k universe), MEDIUM effort (extends the structural matcher + one 1H series-set per sport) and a
   REAL but containable hazard: **route a 1H bet ONLY to the 1H series; NEVER fall back to a full-game strike**
   (§6). Winner is 3-way (`-TIE`) for cfb; nfl/nba 1H winner is 2-way. Reuses the structural stem join. Build 1H
   first (higher volume than quarters); leave quarters unbuilt (tiny count).
3. **MLB F5 (first-five winner/spread/total).** Same shape/safety as first-half but a SEPARATE fix (the MLB matcher,
   not the structural one — §7) and lower volume ($56k/22 roster; $423k/682 universe). Build only after the
   half-game routing pattern is proven, reusing it. F5 winner is 3-way (`-TIE`).

**DO NOT BUILD (clean no):**
- **Season FUTURES / awards / division / series-winner / win-total ladders.** Highest whale $ (nba $8.8M, nhl
  $2.8M, nfl $1.7M universe) but the worst build: no game and no date (every matcher we own keys on teams+date, so
  this is a different matcher shape entirely), positions resolve MONTHS out (untested against the open-exposure cap
  and the per-game settlement scan), and our fixed-size copy is negligible against a months-long hold. Win-total
  ladders additionally violate exact-strike-only ("closest threshold" = a wrong-strike by construction). **NO.**
- **Player props (all sports).** Modest volume (nba $166k, nfl $110k universe) against the platform's worst-bitten
  hazard: a **player-name join** with no code-anchor equivalent (the ticker-code anchor that makes team/fighter
  binding safe does not exist for a free-text player name; this class produced the esports-org-buys-opponent and
  LA/WSH/LAS team-code bugs). Effort ≫ volume. **NO** (revisit only if a whale bets props heavily *and* a
  code-anchored player identity is designed first).
- **Team totals** (team+strike compound hazard, ~$0 roster volume), **quarters** (tiny count), **extra innings**
  (~0), **NFL "combined-points" question slugs + parlays** (no game stem → unmatchable by design). **NO.**
- **Live / in-game.** Not a market type — a different latency + liquidity proposition (we copy entries within
  seconds; in-game books move continuously and settle in-play). Out of scope for a market-type build. **NO.**
- **UFC rounds O/U** (ruled out: 2:30 settle inversion), **RoV / decision-round** (no Poly source), **UFC novelty**
  (uncopyable). **NO** — UFC is closed.

## 4. THE DRY-RUN GATE (unchanged) — for anything built

Before any family arms on a live sub-division, a REAL-MARKET dry-run over the attached/pinned whales' actual bets
in that family, asserting **ZERO wrong game, ZERO wrong team, ZERO wrong strike, ZERO wrong market type**, with
every miss classified (no_kalshi_strike / out_of_window / skip_market_type_excluded / …). **An empty test set
reports INCONCLUSIVE, never PASS** (NBA families cannot pass now — offseason, no live markets; MLB RFI/F5 and
NFL/CFB 1H CAN, in-season). Family-specific must-prove:
- **RFI**: polarity to zero — every `-nrfi` Yes → Kalshi RFI **NO**, every `-nrfi` No → RFI YES (and `-yrfi` if it
  exists, inverted). One market per game; assert the stem-joined ticker is the RFI market, not KXMLBGAME.
- **First-half / F5**: every 1H/F5 signal routes to a `KX…1H…`/`KXMLBF5…` ticker and NEVER to a full-game ticker;
  exact-strike only for spread/total; TIE handled (a team leg on a tie settles NO — correct). Prove on a slate with
  BOTH full-game and 1H markets for the same game that the two never cross-bind.

## 5. WHAT SHIPS INERT (per sub-division enable, exactly like spread/total)

Each built family ships as a new `market_type` token the matcher can PARSE but that is OFF until Jack writes it
into a sub-division's `market_types` column (a type the matcher cannot parse cannot be enabled; a parsable type not
in `market_types` → `skip_market_type_excluded`, a labelled skip, never a fill). Proposed tokens:
`first_inning_run` (mlb), `first_half` (structural), `f5` / `f5_total` / `f5_spread` (mlb). Default state: every
existing sub keeps its current `market_types` byte-unchanged (no restart, read per cycle), so shipping the code is
INERT platform-wide until you enable a specific (account, category). This is the proven spread/total /
UFC-method deploy shape.

## 6. HAZARDS, named per family (for the plan of record)
- **RFI polarity** — the leg inverts between Poly "No Run First Inning" and Kalshi "Run in 1st (Over 0.5)". Wrong
  polarity = every RFI copy on the wrong side. Deterministic; prove in dry-run.
- **First-half / F5 wrong-market fallback** — a first-half Over 23.5 is a DIFFERENT market from a full-game Over
  23.5. The matcher must route to the half/F5 series and refuse (skip, not fall back) if that series is absent for
  the game. This is a wrong-market fill (real money on the wrong contract), not a miss.
- **Team totals** — carry both a team AND a strike → the wrong-team and wrong-strike failure modes compound.
- **Player props** — need a player-name join, an entirely new normalization surface with no code-anchor. Named
  binding has bitten this platform repeatedly (esports org bound the opponent; LA/WSH/LAS team codes). Do not build
  without designing a code-anchored player identity first.
- **Futures** — no game, no date (a different matcher shape); resolve MONTHS out (untested vs open-exposure caps
  and the per-game settlement scan). High $ does not offset high effort + untested settlement/exposure interaction.
- **Win-total ladder** — "closest threshold" contradicts exact-strike-only; there is no guarantee an exact rung
  exists, so it is structurally a wrong-strike risk. Unsafe as specified.
- **Live / in-game** — latency + liquidity proposition, not a market type.

## 7. F5 vs HALF-GAME — same shape, TWO separate fixes (established, not assumed)
MLB F5 skips via the `prop` branch of `mlb_poly_kalshi_match.py`; structural half-game skips via `non_moneyline` in
`sports_structural_match.py`. They are parallel families in two different matcher modules — a single change covers
neither the other. If both are approved, they are two builds (an MLB-matcher F5 series-set and a structural 1H
series-set), each with its own strict-routing safety rule; the *pattern* (parse the suffix → route ONLY to the
sub-game series → exact-strike/3-way → new market_type token) is shared and should be written once and applied
twice.

---
Read-only throughout: no writes, deploys, restarts, or arm changes. Data from `pm_closed_position` (whale history)
via read-only `mode=ro` runners (`pm_gap_volume_ro`, `pm_gap_refine_ro`, `pm_slug_probe_ro`) + public Kalshi series
probes (`kalshi_family_probe`, `kalshi_ticker_probe`). Jack rules the scope.
