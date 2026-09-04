# TENNIS — Discovery + Plan (read-only; 2026-09-03)

Probe, not a build. Establishes the five open questions against REAL data, then a UFC-shaped plan.
Runners: `cc/pm_tennis_whales_shard_ro`, `pm_tennis_kalshi_ro`, `pm_tennis_kmatch_ro`, `pm_tennis_poly_ro`, `pm_tennis_namematch_ro`.

## ★ SHARD — CONFIRMED, no funding problem
`shard_balance.py`: exchange_index 3 = "Tennis & Baseball". Live `KXATPMATCH`/`KXWTAMATCH` markets probed at
**exchange_index=3**. Snapshot: jack **shard3=$364.38**, karen **shard3=$353.79** (has_breakdown=True). Tennis inherits
MLB's funded shard — unlike UFC (shard 0), there is nothing to fund. (Futures like KXATP1RANK sit on shard 0; we don't copy those.)

## 1. CATEGORY STRUCTURE — recommend TWO sub-divisions (atp, wta)
- Derivation: `category.py` `SLUG_PREFIX_MAP` has **atp→atp, wta→wta** (tier-1 slug prefix). "tennis" is NOT a tier-1
  prefix — it exists only as a tier-2 gamma **tag** (`TAG_SLUG_TO_CATEGORY`), i.e. the residual for tennis events whose
  slug doesn't start atp-/wta-. Polymarket slugs are `atp-...`/`wta-...` (men's/women's split at the slug level).
- Farm data (pm_watchlist): pinned **wta 11, atp 4, tennis 3**; candidates atp 14 / tennis 9 / wta 3. Heavy wallet
  OVERLAP (same whale under atp AND wta AND tennis — categories are per-slug-prefix of what each whale bet, not disjoint
  whale sets). Paper history: **atp 67 trades/6 wallets, wta 60/7**; **tennis: 0 paper rows** (all trades land under atp/wta).
- Kalshi has SEPARATE `KXATPMATCH` / `KXWTAMATCH`. So BOTH venues split men's/women's.
- Attachments join ON CATEGORY, so a whale pinned `atp` can only attach to an `atp` sub-division.

**Recommendation: TWO (atp→KXATPMATCH, wta→KXWTAMATCH).** Cost of each option:
- ONE ("tennis"): fights the grain — requires re-deriving atp/wta→tennis (invasive SLUG_PREFIX_MAP change, discards the
  farm's atp/wta split + paper history) OR a ctx builder fetching BOTH series and a matcher mixing men/women. Loses the
  men's/women's distinction. NOT recommended.
- TWO (atp,wta): 2 caps, 2 dispatch registrations (both → ONE tennis matcher), 2 SERIES entries (clean 1:1), 2 categories
  added per account task (mlb+ufc+atp+wta = 4). Mirrors both venues; lets Jack enable atp/wta SELECTIVELY (relevant: the
  whale-proportional study found **atp contraindicated**, wta/tennis not justified — different quality per sub-category).
  RECOMMENDED.
- THREE (atp,wta,tennis): as TWO + a "tennis" sub-division with NO clean Kalshi series (ambiguous men/women) and ZERO
  paper history. Adds a 5th category/account and a set of caps for a residual. NOT recommended (defer "tennis").

★ Account-aggregate note: with 4 categories per account, C's **$150/day + 50 orders stays the ACCOUNT ceiling** shared
across mlb+ufc+atp+wta. US-Open days (30+ matches) + MLB (both shard 3) can bump the account cap — headroom flows to the
busy category, exactly as designed. Shard 3 ($364/$354) comfortably covers the $150/day cap.

## 2. KALSHI SHAPE — moneyline match-winner only
- **KXATPMATCH / KXWTAMATCH**: `market_type=binary`, one YES per player, two per match. Ticker
  `KXATPMATCH-{YYMONDD}{CODE1}{CODE2}-{CODE}` (e.g. `-26SEP05KHABON-KHA`), title **"{Player} wins"**, yes_sub=player.
  **Structurally IDENTICAL to UFC's KXUFCFIGHT** (date + 6-char blob of two first-3-of-surname codes + 3-char YES code).
- IN SCOPE (1:1 copyable): match winner (moneyline) ONLY. No "distance" analog.
- OUT (like UFC futures): tournament-winner (`KXATP`, tournament codes KXATPIT/MAD/MIA…), `#1 rank` (KXATP1RANK),
  finals/grand-slam futures, props (KXATPRETIRE, KXATPACES). Set/game markets (KXATPSETWINNER, KXATPGTOTAL,
  KXATPGAMESPREAD) are separate series — NOT in scope unless whales bet them (they don't; all real bets are match-winner).
- ★ TRAP: **TABLE tennis** series exist (KXTTMATCH, KXTABLETENNIS, KXITTF*, KXWTTMATCH). Scope MUST be the ATP/WTA match
  series only; never let a "tennis" match reach table tennis.

## 3. POLY JOIN + DATE
- Slug `atp-{p1}-{p2}-YYYY-MM-DD` / `wta-...`; **557/557 carry a date**; outcome = player name; title = "US Open ATP: A vs B".
- Join = UFC-identical: (date from slug, outcome player name) vs Kalshi (ticker date, title "{Player} wins").
- ★ DATE = the SCHEDULED match day on BOTH venues (Poly desc "originally scheduled for Sep 2… 14-day grace → 50-50";
  Kalshi ticker-date + long close). **The disproving case to hunt (tennis analog of UFC cross-midnight) = a
  RESCHEDULED/POSTPONED match**: Poly keeps the ORIGINAL slug date; Kalshi may re-date its ticker → date-keyed join
  breaks. MUST test against a settled postponed US-Open match before enabling. `title` IS needed (as UFC) — the ctx
  builder carries it; mirror `fetch_ufc_market_context`.

## 4. NAME PROBLEM — UFC logic transfers; ONE tennis-specific gap
Ran the DEPLOYED fixed matcher's `_norm`+`match_fighter_name` (e5263328) over 98 real (date,player) bets vs the live
Kalshi tennis index (17 dates / 1118 player-markets): **matched 60, ambiguous 0, miss_coverage 23, miss_name_gap 15,
WRONG_PICKS 0.** Accent-fold (Świątek→Swiatek, Cerundolo, Tsitsipas), hyphenation (Auger-Aliassime, Jan-Lennard),
multi-token surnames (van de Zandschulp, de Jong) all handled by the reused `_norm` (collapses punctuation) — and it
never mis-picks. The 15 name-gaps are ALL the tennis-specific mode: **Polymarket surname-only outcomes** ("Harris" vs
Kalshi "Lloyd Harris"). Recovering those by surname is **WRONG-PICK-UNSAFE** — the draw holds BOTH "Francisco Cerundolo"
AND "Juan Manuel Cerundolo", so a bare "Cerundolo" is ambiguous. The matcher correctly MISSES (miss OK, wrong pick STOP).
OPTIONAL safe recovery: the Poly TITLE "A vs B" carries both full names → expand a surname-only outcome to a full name,
uniqueness-guarded. Defer unless match rate demands it.

## 5. SETTLEMENT (retirements/walkovers) — venues largely AGREE
- Kalshi (rules_secondary): resolves "after a ball has been played"; pre-play walkover/forfeit/cancel → **"fair price"**;
  postponed/delayed → market stays open, closes after the rescheduled match.
- Poly (event description): retirement mid-match → the **advancing player wins**; canceled/not-played/tie/undetermined-by
  +14d → **50-50**.
- **Retirement mid-match: BOTH pay the advancing player → SAFE to copy.** Pre-play walkover: both void/refund (Poly 50-50,
  Kalshi "fair price") — minor VALUE difference, not a pay-vs-void divergence. LOW, bounded risk. Watch the first settled
  walkover; not a copy-blocker. (Confirm the full Poly retirement clause on a real settled retirement.)

## THE PLAN — rungs, mirroring UFC
1. **Matcher** `tennis_poly_kalshi_match.py` — CLONE `ufc_poly_kalshi_match.py`: same ticker/title parse + fight→match
   index + `_norm`/`match_fighter_name` (reuse, do NOT rebuild). Differences: series `KXATPMATCH`/`KXWTAMATCH`; NO distance;
   Poly slug `atp-/wta-...-DATE`; market_type token 'moneyline' only. **Offline-provable** (pure, unit tests incl the
   surname-only MISS + the Cerundolo-brothers ambiguity + accent/hyphen cases + wrong-pick adversarials).
2. **Ctx builder** `fetch_tennis_market_context(client, category)` — mirror `fetch_ufc_market_context`; carry `title`;
   category→series (atp→KXATPMATCH, wta→KXWTAMATCH); confirm exchange_index=3 raw-merge (matches shard-3). **Needs live
   data** for the shape gate + the rescheduled-date check.
3. **Registration** — `MATCHER_ADAPTERS['atp']=['wta']=(tennis_parse, tennis_match)`; `CATEGORY_CTX_BUILDERS['atp']/['wta']`;
   per-category SERIES map. Byte-identical to mlb/ufc for existing categories (adapter-equivalence test).
4. **Tests** — matcher unit (offline) + adapter-equivalence + a DISARMED live tennis dry-run over real whale bets proving
   match-rate + **wrong-pick=0** (the pm_tennis_namematch probe is the template) + box-scratch byte-identical gate.
5. **Deploy** = same shape as UFC: create atp/wta sub-divisions (explicit config, NOT auto-create), attach whales, opt-in
   already ON (both accounts), graft the tennis matcher + register, restart, post-check, then place-one-and-inspect on arm.
   Since shard 3 is funded, the FIRST live tennis order does not wait on funding (unlike UFC).

**Provable offline:** matcher + adapter-equivalence + name failure modes (already done here). **Needs live:** ctx-builder
shape gate, exchange_index=3 raw-merge, the rescheduled-date disproving case, the first settled walkover value.

## WHAT JACK MUST RULE
1. **Structure**: TWO sub-divisions (atp, wta) [recommended] vs one/three.
2. **Caps**: lean = same as mlb/ufc (contracts 5, per_order 5.50, daily 150, open 150, max_orders 50, slip 2, liq 0.75).
   Market shape does NOT argue otherwise (binary match-winner, ~$0.50-1.00 contracts like the others). Note the account
   $150/day now spans 4 categories.
3. **Market types in scope**: moneyline match-winner only (KXATPMATCH/KXWTAMATCH). Set/game/futures OUT.
4. **Surname-only recovery**: ship full-name-only first (safe, misses ~15%) vs add the title-expansion recovery now.
5. **Whale set**: Jack's UI pick after loss-omission figures (atp 4 pinned + 14 candidate; wta 11 pinned + 3 candidate).

## ADDENDUM (structure gate + postponement gate — 2026-09-03)
### Does atp+wta cover everything? — NO; ITF is a real, lower-priority gap
The 3 tennis-PINNED whales' real bets: the "tennis" residual = (a) **ITF matches** (`itf-{p1}-{p2}-DATE`, 000why000
+ 0x9a8c bet many) — dated match-winner, derive to unknown→tennis; **Kalshi HAS `KXITFMATCH`/`KXITFWMATCH`**, IDENTICAL
shape ("{Player} wins", exch_idx=3), M15/W50 tier; (b) tournament-winner **futures** (4751346 is almost all this) — OUT of
scope; (c) a few odd-format slam slugs (current slams use atp-/wta-). **Grand Slams confirmed under the single match
series** (KXATPMATCH carries 228 US Open match markets). So atp+wta cover ATP/WTA tour + slams (liquid, high-value,
whale-validated); **ITF is genuinely copyable but lowest-tier, 0 paper history, mixed category (must scope to the ITF
MATCH series, exclude the futures), and needs a combined men+women ITF index.** RECOMMEND **atp+wta now; ITF ("tennis"
→ KXITFMATCH+KXITFWMATCH) a deliberate later 3rd**, not a blocker. 000why000 edge = ATP (only CANDIDATE, not pinned) + ITF
→ under atp+wta it needs an ATP-promote to be attachable; HOLD or promote.

### Postponement/date gate — CONFIRMED real; matcher must be pair-keyed
Poly-vs-Kalshi date on the SAME match (paired by player-pair): **agree 98, DIFFER 11 (~10%), coverage 21.** All
divergences are **±1 day BOTH directions**. A naive (date, single-player) join misses ~10% + risks a wrong-day pick.
**FIX (the required matcher design): key on the player-PAIR from the Poly title "A vs B" + a ±1-day tolerance,
uniqueness-guarded.** This simultaneously (1) survives the date divergence and (2) recovers Poly surname-only outcomes
(ruling 4) — the opponent identifies the match. Wrong-pick-safe: both players must match + uniqueness. Tennis matcher is
therefore NOT a naive UFC clone (UFC = single outcome + exact date; tennis = pair + date-window).

### Account aggregate in practice (ruling 2)
$150/day + 50 orders is the ACCOUNT ceiling across 4 categories (mlb+ufc+atp+wta). Because the account cap EQUALS each
per-category cap but SUMS across categories, **the account aggregate ALWAYS binds at or before any per-category cap** —
the per-category $150/50 is effectively redundant while >1 category trades. Busy day: mlb (most active) consumes the
shared $150 cumulatively; once the account hits $150 or 50 orders, EVERY category rejects (`account_daily_cap` /
`account_count_ceiling`) for the rest of the day → **mlb can starve tennis over a day.** Within one cycle the ONE task
iterates categories ALPHABETICALLY (atp, mlb, ufc, wta) sharing one Journal, so the earlier-alphabetical category gets
first claim on that cycle's remaining headroom ("first-to-ask" = alphabetical intra-cycle); the daily cumulative
dominates across the day. Shard 3 ($364/$354) comfortably covers the $150/day cap.

### Wallets resolved (ruling 5)
`0xd6966eb1…8a49` = **0xd6966eb1ae7b52320ba7ab1016680198c9e08a49** (pinned wta ✓, on karen/mlb ✓) → WTA.
`0xdb859a55…152f` = **0xdb859a551fcf56e49416160911476bea7307152f** (pinned atp ✓, on karen/mlb ✓) → ATP.
000why000 `0x1f7105a18d9f36aef7c83e5df210e57cf487b2e4` = pinned wta/tennis, CANDIDATE atp → HOLD (needs ATP-promote).

## DEFERRED (filed, not forgotten): ITF as a THIRD tennis category
Jack ruled 2026-09-03: DEFER ITF. It is a REAL uncovered gap (the "tennis" residual), NOT empty — but lowest tier
(M15/W50), zero paper history, and the "tennis" category mixes ITF MATCHES with out-of-scope tournament-winner FUTURES,
so it needs its own scoping. **When added, it is a deliberate THIRD category `tennis` → Kalshi series `KXITFMATCH`
(men) + `KXITFWMATCH` (women)** — a COMBINED men+women index (the Poly `itf-` slug does not split), scoped to the match
series only (exclude the futures that also live under the "tennis" tag). Same pair-keyed matcher. Prereq: promote the
ITF whales to a `tennis`-pinned state and read loss-omission figures first. This is a KNOWN deferral with its series named.

## CREATE+ATTACH DONE 2026-09-04 ~02:58Z (board-authorized, engine PID 183010 untouched, no restart)
4 sub-divisions created (jack/atp, jack/wta, karen/atp, karen/wta; caps=mlb/ufc, market_types='moneyline', contracts=5);
0xdb85..152f attached ATP + 0xd696..8a49 attached WTA on BOTH accounts (live-attach, created_subdivision:false).
mlb+ufc sub-divisions PROVEN byte-unchanged (fingerprints identical pre/post: jack/mlb f5478e38, jack/ufc c9702f06,
karen/mlb 396f1676, karen/ufc b6bf4b86); arm ts unchanged; restart-preview jack+karen each [atp,mlb,ufc,wta] SKIP=[].
Until the matcher deploys, atp/wta hit skip:no_matcher_for_category at the chokepoint -> nothing trades.

## INHERITED (not chosen): intra-cycle category order is ALPHABETICAL
The one task iterates its categories in the order `active_driver_subdivisions` returns them = `ORDER BY category` =
ALPHABETICAL (atp, mlb, ufc, wta). So each cycle atp gets first claim on the shared account headroom and wta last.
**Nobody chose this ordering** — it is an artifact of the SQL sort. Recorded so whoever revisits the account-cap fairness
knows it was inherited, not designed. (Account ceiling always binds before any per-category cap; mlb can starve tennis
over a day.)
