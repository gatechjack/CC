# Soccer matcher — Rung 3 build + dry-run gate + LISTED DEFERRALS (2026-09-07)

**Status:** BUILT + box-scratched GREEN + STAGED, HELD at the deploy line. Commit `001bff0`
(branch `pm-remaining-categories-plan-2026-09-06`, pushed). Nothing armed; nothing on the order path.

## The shape (3-way: team-win + draw)
Polymarket lists a soccer game as separate Yes/No binaries:
- **team-win** — slug `{lg}-{away}-{home}-{date}-{teamcode}`, title `Will {Team} win on {date}?`, outcome
  Yes/No → Kalshi `{Team} wins` market, **yes/no leg** (No = draw-or-lose = the Kalshi NO leg; clean 1:1).
- **draw** — slug `...-draw`, title `Will {A} vs {B} end in a draw?`, outcome Yes/No → Kalshi **TIE** market
  ("Tie is the result"), yes/no leg.
Kalshi `KX{LG}GAME` lists **three** markets per game — `{A} wins` / `{B} wins` / `Tie` — sharing a (date, blob).
Totals (`-total-`, "O/U") and spreads (`-spread-`, "Spread:") are OUT of scope.

## 90-minute settlement (the knockout divergence dissolves)
BOTH venues settle **90-minute regulation**. For UCL/UEL knockouts (which can go to ET/penalties) Kalshi
publishes the 90-min market as **`Reg Time: {Club}`**; league-phase games have no prefix (no ET → plain IS
90-min). Each game is listed **once** (plain OR all-Reg-Time, never both), so there is no dual-market to
disambiguate — the matcher strips the `Reg Time: ` prefix and treats them uniformly = Poly's 90-min.

## Club naming — soccer's team map (data-verified, collision-checked, never fuzzy)
Poly uses formal/local names (`FC Bayern München`, `Tottenham Hotspur FC`, `Inter Miami CF`); Kalshi uses
short (`Bayern Munich`, `Tottenham`, `Miami`). Join = EXACT base-normalize (accent-fold + lowercase +
strip-punct + collapse) through a **per-league alias table** (`soccer_teams.py`, 328 entries) built from
REAL two-venue names — auto-proposed (affix-strip / unique token-prefix), then **HARD collision-checked**:
no two DIFFERENT clubs may map to one Kalshi target. Every token-prefix-derived alias was audited as a
same-club reduction.

**Named collisions kept distinct (soccer's SDST/Cerundolo):**
- **Ligue 1 `PSG` / `Paris` / `Paris FC`** — three Kalshi names. ★ The auto-generator wrongly prefix-mapped
  `Paris Saint-Germain FC → Paris`; **neither the dry-run wrong-team check nor the collision-check caught it**
  (the alias-caveat: a bad alias corrupts both sides consistently). Caught by **domain verification** against
  the real opponent+date data (PSG plays Rennes/Lille/Monaco; Paris FC plays Nice/Lyon) → fixed to `PSG`.
- **MLS `Los Angeles F` (LAFC) / `Los Angeles G` (Galaxy)** — Kalshi truncation; explicit, never affix-stripped.
- **UCL Inter Milan (`Inter`) / Inter Club d'Escaldes (`IC Escaldes`)** — the collision-check caught the
  prefix merge; mapped explicitly.
- Cross-league Inter Milan / Inter Miami — different leagues, different tables → never collide.

## The dry-run gate (the acceptance test)
~9,500 real Poly moneyline bets vs the real `KX{LG}GAME` index, per league (Poly from box read-only sqlite =
0 Kalshi load; Kalshi off-box; `cc/soccer_dryrun.py`):
- ★ **0 wrong team, 0 wrong leg (Yes→yes/No→no), 0 wrong market-type** on 3,013 matches.
- **Aggregate in-window match = 93.3% (3013/3228)** — the honest live number (older Poly bets are
  out_of_window snapshot artifacts). Per-league in-window: lal/sea/bun/bra 100%, mls 99.9%, epl 93.9%,
  fl1 79.4%, ucl 76.9%, uel 68.1%. UCL/UEL lower = obscure qualifying-round minnows not yet aliased
  (safe misses, 0 wrong).
- ★ `mex` shows 0 in-window: its Poly bet dates don't overlap the current Kalshi rolling-window snapshot
  (structurally identical matcher, 0 wrong on the other 9) → **BUILT but NOT dry-run-validated; needs a
  real-market dry-run before arming** (like nba/nhl/wnba).

## Built (10, by whale volume) + LISTED DEFERRALS
**BUILT:** epl ($42.9M) · ucl ($37.2M) · lal ($20.8M) · fl1/Ligue 1 ($14.4M) · uel ($11.9M) · mls ($10.4M) ·
sea/Serie A ($9.5M) · bun/Bundesliga ($5.9M) · bra/Brasileirão ($2.9M) · mex/Liga MX ($2.4M).

**DEFERRED (named, with reasons — never a silent miss):**
| league | Kalshi series | vol | reason |
|---|---|---|---|
| col = UEFA Conference League | KXUECLGAME | $1.3M | 299 Kalshi clubs (obscure qualifiers); map effort ≫ volume; big clubs covered via UCL/UEL if they drop in |
| elc = EFL Championship | KXEFLCHAMPIONSHIPGAME | $1.3M | English 2nd tier; below the volume line; own team map (reuses English naming) |
| efl = EFL/Carabao Cup | KXEFLCUPGAME | $1.7M (106 bets) | mixed-division cup; low bet count; reuses English map |
| efa = FA Cup | KXFACUPGAME | $1.7M | ★ Kalshi returned **0 markets** in the window — cannot build/validate now; revisit when it lists |
| nor Eliteserien / tur Süper Lig / arg Primera / spl Saudi / sud Sudamericana / por Liga Portugal / ere Eredivisie / lib Libertadores / dfb DFB-Pokal / chi Chinese SL / es2 LaLiga 2 / kor K League | KXELITESERIENGAME / KXSUPERLIGGAME / KXARGPREMDIVGAME / KXSAUDIPLGAME / KXCONMEBOLSUDGAME / KXLIGAPORTUGALGAME / KXEREDIVISIEGAME / KXCONMEBOLLIBGAME / KXDFBPOKALGAME / KXCHNSLGAME / KXLALIGA2GAME / KXKLEAGUEGAME | each <$2.2M | below the top-10 volume line; each needs its own per-league alias table (all series EXIST on Kalshi — buildable with the same matcher). ★ sud/lib/arg carry South-American same-name clubs (River Plate/Nacional/América) → need careful per-league scoping when built |
| uef = UEFA Nations League | KXUEFANLGAME | $0.75M | ★ **NATIONAL TEAMS** (countries), not clubs — a different name universe; needs a country map, deferred as its own sub-type |

Nothing is unmatchable for lack of a Kalshi series (Kalshi lists 40+ soccer leagues). The limiter is
per-league team-map effort, spent in volume order.

## Wiring + isolation
- `execution.py`: `MATCHER_ADAPTERS[cat]` for the 10; new `MarketContext.soccer_index` DEFAULTED None →
  mlb/ufc/tennis/structural/cs2 constructions BYTE-IDENTICAL.
- `live_driver.py`: `CATEGORY_CTX_BUILDERS[cat]=fetch_soccer_market_context` (per-league KX{LG}GAME, Reg-Time
  strip, ±1d window).
- Box-scratch (box venv): import OK, 328 aliases, 149 tests incl mlb/ufc/tennis/cs2/structural byte-identity;
  engine 224045 / pm_web 218797 UNTOUCHED.

## Deploy sequence (all HALT — Jack's authorization)
1. `cc/pm_soccer_deploy.*` — 4-file graft (2 new + 2 modified), drift-check box==rung-2 base, backup, extract,
   **SHA-VERIFY placed==COMMITTED** (execution `0fff5e7a` / live_driver `34fcc8fe` / soccer_match `78cd53e2` /
   soccer_teams `86f6daf7`), restore-on-mismatch, additive-diff, import-check. NO restart. (RO pre-check GREEN.)
2. engine restart (load soccer; bounces every division — warn co-tenants) → `cc/pm_soccer_postcheck_ro.*`.
   ★ Note: nfl is now attached (jack+karen) → at this restart nfl enters the roster (liveness 8→10, volume
   order includes nfl); nfl stays DISARMED (evaluates, no placement) until armed.
3. `cc/pm_soccer_create.*` — 20 disarmed subs (10 leagues × 2 accounts), NO arm rows, NO attachments;
   proves original-8 SHA `198f61354e17187f` unchanged → `cc/pm_soccer_createverify_ro.*`.
Then later (Jack): attach whale + arm + set caps, per league in volume order. **ARM-GATE:** epl/ucl/lal/fl1/
uel/mls/sea/bun/bra are dry-run-proven; **mex needs a real-market dry-run before arming** (snapshot gap).
