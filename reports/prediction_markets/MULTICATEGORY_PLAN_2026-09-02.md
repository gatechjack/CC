# MULTI-CATEGORY-PER-ACCOUNT PLAN — kalshi_jack/ufc alongside kalshi_jack/mlb (2026-09-02, rev 2)

**This is a PLAN. Nothing here was built, deployed, armed, or written to the box.** Rev 2 folds the board's rulings
(2026-09-02): Option C is SETTLED, the scope finding leads, the three re-scopings are first-class rungs, caps
structure is ruled (aggregate stays $150/day + 50 orders), the go-live proof is named, M5 is closed. Two accounts
remain ARMED and TRADING; I did not disarm. Global STOP (kills both), verbatim:

    PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global

Branch `pm-per-account-trading-2026-09-02` @ pushed HEAD, base `f1e28cc`, clean, local==origin. prod-live `7220e32`
/ main-wip NOT advanced. Box-is-truth. Fresh read-only shard read (`pm_shards_ro.ps1`, board-approved, 16:47Z):
**jack shard 0 = $0.0081** (all $482 on shard 3), Karen shard 0 = $25.01 — a ufc copy on jack skips every cycle
until shard 0 is funded (M5, §7).

---

## 1. LEAD FINDING — "add a second category" is a MATCHER BUILD + a SAFETY RESTRUCTURE, not a guard relaxation

The guard (`driver_roster.plan_driver_tasks`) is the LAST and smallest step. Two much larger things sit in front
of it, and the plan is organised around them:

- **WORKSTREAM A — a UFC matcher + category dispatch (a real build, §8).** `execution.py:49` hardwires
  `from ..data import mlb_poly_kalshi_match as M`; `evaluate` calls `M.parse_poly_mlb_bet` (`execution.py:383`);
  `live_driver.SERIES` (`:60`) and `fetch_market_context` are MLB-only. UFC therefore needs its own matcher
  (`ufc_poly_kalshi_match.py`) + a per-category dispatcher in the chokepoint and the driver. This is comparable in
  size to the original MLB matcher and could be its own plan. It carries the MLB matcher's hard-won lessons (§8).
- **WORKSTREAM B — the safety restructure (§3–§6).** M1 (open_usd race) is fixed by ONE task per account iterating
  its categories (Option C, RULED). That restructure ITSELF re-scopes three per-task safeties (§4) and requires the
  M2/M3 latch-scope fixes. This is where the guard's protection actually lives.

Neither workstream is the guard. The guard (M4, §12) only opens after BOTH land and are proven, behind a
fail-closed per-account opt-in that is OFF by default. Anyone reading "just relax the tripwire" is reading it wrong.

---

## 2. WHAT I VERIFIED vs ASSUMED (read the code at HEAD, CR-stripped)

**Verified:** worktree clean, HEAD==origin, the 5 package files match SW9's deploy manifest, and **`main.py` at
HEAD (CR-stripped) = `9e8da82` = the live box graft** — the code below IS what the box runs, no phantom drift.
`boot_reconcile`'s journal read is already account-wide across every category (`:78-119`); only the latch target is
per-category (`:50-53,300-301`). `arm.latch_auth_failure` already loops a category list (`arm.py:273-280`); the gap
is the call site (`live_driver.py:419`). gate 6/`open_usd` is account-keyed (`execution.py:485,332,343`);
gate 5/8 daily+count are `(account,category)`-keyed (`:342,344`). gate-4 COID carries `division=account:category`
(`:47,111-113,460`). gate-6b shard funding is per-market-shard, handles shard 0 (`is not None`, `:499-505` +
`shard_balance.py:58-77`). Wiring spawns one broker per distinct account + one task per `(account,category)`
(`main.py:1539-1615`) — two same-account categories would share ONE broker.

**Assumed / not provable from the repo:** **pykalshi concurrent-POST safety** (see §3). UFC market types (external
research, §11). The exact UFC ticker sub-format and fighter-abbreviation scheme (needs a live disarmed probe, §8).

---

## 3. M1 — THE open_usd CAP RACE. Fix = Option C (RULED, SETTLED — no longer an open ruling)

**The race (unchanged from rev 1):** two same-account tasks each build their own per-cycle Journal + venue read;
gate 6 base = `venue_open + own_in_cycle`; neither sees the other's in-cycle commits nor pre-fill `submitting`
rows; they interleave at the POST await. R7's venue base re-anchors CROSS-cycle (once a sibling's orders fill and
show on `/portfolio/positions`, the next cycle rejects) but does NOT catch two tasks sizing against the same base
WITHIN an overlapping ~7 s window. Worst case in one window ≈ `2·cap − venue_base` (~$300 vs $150), self-correcting
next cycle but leaving the account at ~2× cap until positions close. It bites at high position count +
simultaneous bursts — precisely a Saturday MLB-slate + UFC-card overlap — so "unlikely in practice" is not a defence.

**RULED: Option C — one driver task per account iterating its categories, sharing ONE per-cycle Journal + ONE venue
read.** Categories run sequentially within a cycle; gate 6's account-keyed in-cycle accumulator sees the first
category's commits when the second evaluates, so the account open-cap is jointly enforced exactly as it is for one
category today. **gate 6, `evaluate`, the Journal, and the POST path are UNCHANGED — nothing is added between
"decide to place" and "POST."** Sequential categories within a cycle is the accepted cost; the board is NOT
requiring concurrency, so **Option B (a hot-path per-account lock) is off the table and this is no longer carried
as an open ruling.**

**★ WHY C IS RIGHT BEYOND ELEGANCE — it never has to ask an unanswerable question.** Two same-account tasks would
share ONE `KalshiLiveBroker` and POST on ONE client concurrently. Our wrapper adds no per-request state
(`kalshi_live.py:315-320,381`), but whether two simultaneous POSTs on one pykalshi client are safe (RSA-PSS
signing / nonce reentrancy) reduces to the library's signed transport — and **pykalshi is not vendored, so this is
NOT provable from the repo.** The two-task model was resting on an unverified assumption about a signed transport
carrying real money. **Option C never places two same-account POSTs concurrently, so the question never arises.**
That certainty is worth more than the intra-cycle concurrency it gives up.

**Hard stop inside the build:** if box-scratch shows Option C does NOT enforce the account cap across two categories
on one shared journal (i.e. this analysis is wrong), STOP and do not relax the guard.

---

## 4. THE THREE RE-SCOPINGS OPTION C INTRODUCES — first-class rungs, each its own test

Collapsing two tasks into one is itself a source of the exact bug class it fixes: **a safety check that silently
stops checking.** Three pieces of per-TASK state would be wrongly shared across categories under one task. Each
gets rung status and its own test. **Instances #14, #15, #16 of the standing lens** (the list stood at 13).

- **#14 — the sustained-shard-underfunding ALARM counter** (`consec_underfunded`, `live_driver.py:528,666-674`).
  Per-task today. Under one task, an mlb placement (`summ["placed"]>0`) RESETS it, so a genuinely starved ufc
  shard-0 would **never raise its sustained alarm** — masked by mlb activity. → make it **per-category** (dict keyed
  by category). Test: mlb placing every cycle while ufc is shard-0-starved → the ufc alarm still fires at N=3.
- **#15 — the consecutive-error latch counter** (`consec_err`, `live_driver.py:367,416,433`). Per-task today. An
  mlb fill (`consec_err=0`) would reset a ufc error streak → the ufc error-latch never trips. → **per-category**.
  Test: interleave mlb fills with 3 ufc POST errors → ufc latches consecutive_errors, mlb unaffected.
- **#16 — Option-D exit-snapshot map** (`prior_snapshots`, `live_driver.py:533,604,625`), keyed by wallet alone.
  The attachment table permits the same wallet in BOTH categories (`(wallet,mlb)` and `(wallet,ufc)`); under one
  task their two books MERGE → corrupt reduction/exit detection. → key **`(category, wallet)`**. Test: one wallet
  attached to both categories, a reduction in one → an exit only in that category, the other untouched.

**★ "Did we find all of them?" is an OPEN question, not a closed list.** The first pass through this file (the
overnight build) surfaced three account-scoped degradations (M1/M2/M3); this second pass surfaced three more, all
introduced by the fix. A THIRD adversarial pass — pointed specifically at "what other per-task state does the
single-task loop now share across categories?" — is a required rung before M4, not an afterthought. Candidates
already on my list to re-examine there: the `last_settle`/`last_idx` throttle timers (per-task — likely fine, but a
shared timer across categories could starve one category's settlement scan), the per-cycle `ctx` market-context
cache (must become per-category — MLB vs UFC series), and the DB connection lifetime across a multi-category cycle.

---

## 5. M2 — AUTH-LATCH SCOPE (mechanism exists; fix the call site to latch all account categories)

A 401/403 is dead auth for the whole KEYPAIR = the whole account, but `live_driver.py:419` passes `[sub.category]`.
`arm.latch_auth_failure` already loops a list (`arm.py:273-280`). **Fix:** latch every active category on the
account. Under Option C the task holds its category list → pass it. Belt-and-suspenders: a shared helper unions
that list with `SELECT category FROM pm_subdivision WHERE account_id=? AND active=1` (a DB read that fires only on
failure, never on the hot path), so a category whose task lapsed mid-session is still latched. Test: forged 401 on
a two-category account → both `arm:...` rows go `latched=True, manual_exit_required=True`.

---

## 6. M3 — WHOLE-ACCOUNT BOOT-RECONCILE LATCH (latch all categories; both failure directions reasoned)

- **False-latch — impossible from cross-category presence.** The journal read is already account-wide, so a ufc
  position present in both journal and book reconciles as MATCH. Widening the latch to all categories introduces no
  false latch (a genuine mismatch puts the whole keypair's book in question).
- **Missed-latch — the real gap.** `reconcile_account` latches only the one `(account,category)` it was called for
  (`boot_reconcile.py:215,300-301`). Under two tasks each reconciles account-wide and latches its own category, so
  both happen to latch — incidental and fragile. Under Option C (ONE account-wide reconcile) a single per-category
  latch leaves the sibling free to arm against an unreconciled book → a genuine miss.
- **R-c unchanged:** KALSHI_ONLY = whole-account requires PM-exclusivity; two PM categories keep the account
  PM-exclusive as a whole — same precondition as today, not a new one.

**Fix:** run the (already account-wide) comparison once per account; on mismatch or read failure latch ALL active
categories (loop them like `latch_auth_failure`; add `latch_boot_reconcile_mismatch_account(account_id, categories)`
or loop the existing latch). Under Option C run each category's boot settlement-scan first, then the single
account-wide reconcile. Test: a co-category Kalshi position absent from the journal → all active categories latch;
a clean two-category account → NO latch; jack's other account unaffected.

---

## 7. M5 — SHARD FUNDING: CLOSED

gate-6b is per-market-shard, handles shard 0 correctly, the skip is visible (`live_driver.py:377-380`) and alarmed
(`:666-672`), and nothing assumes a single funded shard (the read returns the whole per-shard breakdown). UFC → MMA
→ **shard 0 (Default)**; the board-approved read confirms jack shard 0 = **$0.0081** today, so ufc copies correctly
skip (no misroute to funded shard 3) until funded. **Funding shard 0 is the operator's job, done when ufc is
otherwise ready.** ⚠ The only carry-over is #14 (§4): the sustained-underfunding ALARM must be re-scoped per-category
under Option C or a starved ufc shard is masked. M5's GATE is closed; #14 keeps its ALARM honest.

---

## 8. WORKSTREAM A — the UFC matcher + category dispatch (its own rungs and proofs)

**UFC's shape (established, §11): two clean binary types — moneyline `KXUFCFIGHT` and go-the-distance
`KXUFCDISTANCE` (no line).** Method-of-victory (`KXUFCMOV`) is possible-but-complex (Kalshi multi-outcome vs
Polymarket per-method binaries) → explicitly OUT of MVP. Round-O/U is Polymarket-only; round-of-victory is
Kalshi-only — neither copyable.

**A1 — `trading_corp/data/ufc_poly_kalshi_match.py`**, mirroring the MLB matcher's public surface: parse a
Polymarket ufc bet (slug+outcome → fighter + market_type ∈ {moneyline, distance}); parse `KXUFCFIGHT`/
`KXUFCDISTANCE` tickers; a fighter-name canonicalizer; `build_*_index`; `match_bet(..., allowed_market_types)`;
`liquidity_ok`. Join key = (fight date ET, normalized fighter-pair SET).

**A2 — category dispatch** in the chokepoint (`evaluate` selects the matcher by `sub.category` via a small registry
`{"mlb":…, "ufc":…}`, or the matcher is injected on `SubConfig`) and in the driver (`SERIES` + `fetch_market_context`
parameterized per category — MLB series vs `KXUFC*`). Note this interacts with #4's per-category `ctx` cache.

**★ THE MLB MATCHER'S HARD-WON LESSONS THAT TRANSFER (name them before building):**
1. **Exact-strike-ONLY, never round to a neighbour** (`match_bet` never snaps to an adjacent line; R-a's "one
   contract is a whole position"). UFC moneyline/distance have no numeric line, so there is nothing to round — but
   the SAME discipline applies to the fighter/date/market-type identity: an approximate name/date match must be a
   MISS (`skip:no_kalshi_contract`), never a nearest-neighbour guess.
2. **Carry the Kalshi leg + market_type ON the MatchResult so the executor never re-derives them**
   (`execution.py:389` reads `match.kalshi_ticker, match.leg`; the leg is decided once, in the matcher). The UFC
   matcher must likewise return the exact `(ticker, leg)` — for KXUFCFIGHT the leg is "which fighter's YES", for
   KXUFCDISTANCE it is yes/no on "goes the distance". Re-deriving downstream is the NO-leg-lens home.
3. **Doubleheader ambiguity → UFC's version is the FIGHTER-NAME/ABBREVIATION problem.** MLB's `G1/G2` doubleheader
   forced an explicit `doubleheader_ambiguous` MISS rather than a wrong pick. UFC has no same-card rematch, so
   (date, fighter-pair) is unique per bout — BUT the identity itself is fragile: Kalshi encodes fighters as 3-char
   ticker abbreviations (`KXUFCFIGHT-26SEP05HOOPAR` = HOOker/PARnasse) that can COLLIDE across two fighters on one
   card sharing the same first three letters, and Polymarket uses per-event short labels (`ufc-max1-con-…`) with no
   fixed scheme. So the canonicalizer needs a fighter-name map (there is NO `UFC_FIGHTERS`, cf. `MLB_TEAMS`) and an
   ambiguity MISS when two candidate bouts on one date could match the same abbreviation — the doubleheader lesson,
   re-expressed for names. **Name this before building; it is where the matcher will actually break.**

**Proofs (INERT — no guard change, no arm):** unit tests on recorded UFC fixtures (a KXUFCFIGHT pair + a
KXUFCDISTANCE; a Polymarket winner + go-the-distance; an abbreviation collision → MISS); then a **DISARMED live
box-scratch dry-run** against real UFC market data — assert would-place bodies carry real tickers with the correct
leg, and unmatched signals → `skip:no_quote`/`skip:no_kalshi_contract` (never a wrong pick). A live disarmed probe
first pins the exact `KXUFCFIGHT`/`KXUFCDISTANCE` ticker sub-format (marked UNMEASURED).

---

## 9. CAPS — STRUCTURE RULED: the account aggregate STAYS $150/day and 50 orders/day

The board's ruling: adding a category must NOT silently double the account's exposure. Per-category caps as-built
would give jack **2×$150 = $300/day, 2×50 = 100 orders** — not what $150 meant. gate 5 (daily) and gate 8 (count)
are `(account,category)`-keyed today; the open-cap (gate 6) is the only account-wide one. Two ways to hold the
aggregate:

- **(i) DIVIDE the per-category numbers so they sum to the aggregate** (e.g. 75/75 daily, 25/25 orders). **Holds the
  ceiling by construction** (the sum can never exceed $150/50) with **ZERO new mechanism** — it reuses the existing
  per-category gates. **Cost:** it MERELY APPROXIMATES the intent — it caps the SUM correctly but MIS-ALLOCATES: a
  quiet MLB night wastes its $75 while a busy UFC card is throttled at $75 even though the account could safely
  spend the full $150. And the split is a guess that needs re-tuning as the category mix shifts. **So $75/$75 holds
  the ceiling but does NOT let the account use its full $150 when one category is quiet.**
- **(ii) ADD an ACCOUNT-LEVEL daily+count cap ABOVE the per-category ones** (a gate-5b/8b that gates on a per-ACCOUNT
  `daily_usd`/`orders_today` aggregate). **Holds the ceiling exactly AND lets headroom flow to whichever category is
  active.** **Cost:** it is a NEW account-scoped shared counter — which is the SAME within-cycle-race shape as gate 6
  (M1). **But under Option C that race does not exist:** one task, sequential categories, one shared Journal that is
  already account-keyed for `open_usd` — extend the identical pattern to a per-account `daily_usd`/`orders_today`
  aggregate and the account daily/count caps are race-free for free.

**RECOMMENDATION: (ii), the account-level cap, gated on Option C.** It enforces exactly $150/50 aggregate, does not
waste headroom, and rides M1's own race-free mechanism. (i) is the fallback if the board wants zero new cap code —
accept the wasted headroom and set the split by expected volume, not 50/50. **Either way the per-category numbers
themselves are deferred until the matcher proves out (§13).**

---

## 10. THE GO-LIVE PROOF — named, and it splits in two

Karen's go-live proof was the **credential path** (her keypair resolved and read her own book), which let her skip
place-one-and-inspect. jack/ufc is different: the keypair is jack's, **already proven** — so the credential path is
NOT the new risk. The new risks are the shared-account safeties and a new market family. They prove differently:

- **The shared-account SAFETIES (M1 cap enforcement, M2/M3 latch scope, #14/#15/#16 isolation) are FULLY provable
  OFFLINE, before ufc places anything.** Under Option C they are deterministic and single-task: box-scratch with
  two same-account categories exercises every one of them without a live fill (§12 proofs). This is the equivalent
  of Karen's pre-arm proof — and it is stronger, because it is offline and repeatable rather than a live
  observation. **No live fill is needed to trust the shared safeties.**
- **The UFC VENUE-WRITE has NO offline proof.** `build_v2_event_order` is generic but has only ever POSTed KXMLB
  tickers; KXUFCFIGHT/KXUFCDISTANCE are a new market family with their own leg/side/price semantics (esp. the
  two-markets-per-bout moneyline and the no-line distance binary). Whether a ufc order lands on jack's book with the
  correct leg and sign cannot be shown short of a real fill. **So — stated plainly as the recommendation, not
  discovered later — jack/ufc's FIRST ufc order gets place-one-and-inspect** (the treatment Karen skipped): arm,
  let ONE ufc order place, verify at the venue it landed on jack's book with the correct ticker/leg/sign/count,
  THEN let it run at full size. A disarmed live-market dry-run (§8) de-risks the matcher first, but the venue write
  itself is the one thing that needs a live fill.

---

## 11. UFC CAPS & WHALES — deferred, framed properly

**Bet types (established, external research):** moneyline `KXUFCFIGHT` (binary per fighter, two per bout, clean 1:1
with the Polymarket winner) + go-the-distance `KXUFCDISTANCE` (binary, no line, clean 1:1 with Polymarket
`-go-the-distance`). ~3–4 events/month, ~12 bouts/event, bursty on fight-night Saturdays; Polymarket carries full
props only on featured bouts, prelims winner-only.

**The five pinned UFC whales, re-ranked on cost-ROI (from `FARM_RERANK_2026-08-23.md`, grounded/net-verified) —
NOT on win%:**

| Whale | n | cost-ROI | net | avgWinPx | two-sided (ufc) | single-fight copyable | verdict |
|---|---|---|---|---|---|---|---|
| **STC14** | 85 | **+38.7%** | +$11.8k | 0.67 | **6%** | ~100% | **best clean UFC — contested edge, directional** |
| **Kh4mz4t** | 270 | +26.3% | +$35.7k | 0.64 | **38%** | 96% | real edge but heavily two-sided (hedger) |
| **kutsumiakia** | 123 | +15.3% | +$24.7k | **0.85** | 0% | ~100% | **CHALK — high win% is favourites, ~0 real edge** |
| **000why000** | 117 | +13.9% | +$25.9k | 0.75 | 29% | ~100% | mid, directional-ish, 29% two-sided |
| **4751346** | 1264 | +8.5% | +$192k | 0.77 | **41%** | **only 44%** | **downgraded — half not single-fight-copyable, 41% two-sided** |

**Which I would attach (recommendation, subject to the loss-omission gate below):** **STC14** (clean, directional,
top cost-ROI) as the anchor, plus **at most one** of {**000why000** (directional-ish) / **Kh4mz4t** (bigger edge but
38% two-sided → a directional copy reproduces less of it)}. **Avoid kutsumiakia** (chalk, ~0 edge — its win% is a
favourites artifact) and **4751346** (only 44% of its record is single-fight copyable). Mirror the MLB whale count
(2–3), start with STC14 + one, widen after live observation.

**★ THE LOSS-OMISSION GATE (the board's hard rule — no attach without the number).** FARM_RERANK's `win%` column is
the UN-GROUNDED `/closed-positions` number — the SAME class the loss-omission finding later exposed (SDTrading
screens 94% win but drops ~94% of its losses; truly ~50%). cost-ROI/net there ARE grounded, which is why the
re-rank above is trustworthy and the win% is not. **The loss-omission % + coverage for each candidate is available
on the shipped Prospects/Analyze UI** (deployed 2026-09-02 for exactly this — the pinned ufc whales are prospects;
click [Analyze] on any un-analyzed one). It is NOT in any local artifact (it postdates FARM_RERANK). **Recommendation:
read STC14's (and the second pick's) loss-omission % off the Prospects page BEFORE attaching; if a candidate's
losses vanish the way SDTrading's do, drop it.** This is a UI read the board does, not a new box runner.

---

## 12. THE PLAN — rungs in order, with the proof before each deploy

**Two workstreams converge at M4. The guard opens only after BOTH land and are proven.**

**Workstream A (capability), INERT — no arm, no guard change:**
- **A0 — disarmed live probe** pins the exact `KXUFCFIGHT`/`KXUFCDISTANCE` ticker sub-format + fighter-abbreviation
  scheme (read-only).
- **A1 — build `ufc_poly_kalshi_match.py`** (§8) + fighter-name canonicalizer. Proof: unit tests incl. the
  abbreviation-collision MISS and the carry-the-leg contract.
- **A2 — category dispatch** in `evaluate` + `live_driver` (per-category matcher, SERIES, ctx). Proof: MLB tests
  unchanged (non-regression) + a UFC dispatch test. Proof: DISARMED box-scratch dry-run against live UFC data.

**Workstream B (safety restructure), each with its own box-scratch proof:**
- **B1 (M2, §5):** latch all account categories on auth failure. Proof: forged-401 box-scratch → both latch.
- **B2 (M3, §6):** account-wide boot-reconcile latch. Proof: co-category venue position → all categories latch; a
  clean two-category account → no false latch.
- **B3 (M1 = Option C, §3):** one task per account iterating categories, shared per-cycle Journal + venue read.
  Proof: **(a)** run the CURRENT two-task model with a FORCED-CONCURRENT placement to EMPIRICALLY DEMONSTRATE the
  race (account exceeds cap) — the justification that M1 is real; **(b)** prove Option C eliminates it (mlb consumes
  the shared cap, ufc then gate-6-REJECTS at the account cap; the account never exceeds `max_open_usd`).
- **B4 (#14/#15/#16, §4):** re-scope the underfunding alarm, consec-error counter, and exit-snapshot map to
  per-category. Proof: the three tests in §4.
- **B5 (caps, §9):** the account-level daily/count aggregate cap (recommendation (ii)). Proof: two categories each
  placing → the account daily/orders aggregate is capped at $150/50, headroom flows to the active category.
- **B6 — the THIRD adversarial pass (§4):** "what other per-task state does the single-task loop share across
  categories?" A required rung, not optional. Proof: a written enumeration + a test for anything it finds.

**M4 — the guard, LAST:** relax `plan_driver_tasks` to GROUP a 2nd category onto an account's task ONLY when that
account is explicitly opted in (a `pm_account.multi_category_ok` column / config allowlist, default OFF; default
behaviour byte-identical to today). Proof: opt-in OFF → still refuses; ON for jack → groups jack's categories; ON
for jack does not affect karen. **★ The board's standing ruling: the opt-in ships ONLY after B1–B6 + A all land and
prove. If ANY of M1/M2/M3 (or the §4 re-scopings) cannot close, the GUARD STAYS — two-of-three is not a reason, and
"the remaining race is unlikely" is not an argument. Report and stop instead.**

**Enablement (all HALT-for-board; DB writes / restart / arm):** fund shard 0 (operator) → set jack's opt-in + create
`(kalshi_jack, ufc)` with the board's ufc caps + attach the chosen whales (LIVE DB write, backup + resolved-verify)
→ restart (canonical `restart_tc.ps1`; post-check: one account task iterating {mlb,ufc}, whole-account
boot-reconcile clean, ufc DISARMED) → **arm ufc with place-one-and-inspect on the first ufc order (§10)**. Graft
rules unchanged (`app.py`/`main.py` GRAFT never wholesale); this work touches ZERO pm_web files.

---

## 13. WHAT THE BOARD MUST RULE (open items only — M1 and Option B are settled, removed)

1. **Caps ENFORCEMENT mechanism (structure ruled; pick the mechanism):** (ii) account-level aggregate cap
   [recommended, race-free under C, holds $150/50 exactly + uses headroom] vs (i) divide per-category to sum
   [zero new code, wastes headroom]. §9.
2. **UFC per-category NUMBERS** — deferred until the matcher proves out (A2). Then set them under the chosen (1).
3. **Which UFC whales** — recommendation: **STC14 + one of {000why000, Kh4mz4t}**, gated on reading their
   loss-omission % off the shipped Prospects UI first (§11). Board confirms the set after that read.

(M5 is CLOSED. Option C is SETTLED. Option B / hot-path lock is OFF THE TABLE.)

---

## 14. STOP CONDITION

- **M1 is closable WITHOUT a hot-path mechanism (Option C, ruled), so the plan does not stop on M1.**
- **Hard stop inside the build:** if box-scratch shows Option C does not enforce the account cap across two
  categories on one shared journal, STOP — do not relax the guard.
- **The M4 ruling stands:** if ANY of M1 / M2 / M3 / the §4 re-scopings cannot be closed and proven, the guard
  stays. "Do not build this yet" remains a legitimate, valuable output.
- **The go-live venue write is not fully provable offline (§10): jack/ufc's first order is place-one-and-inspect.**

---

## 15. PROVENANCE
Read-only throughout. Branch verified CR-stripped. Read execution/live_driver/boot_reconcile/arm/driver_roster/
shard_balance/shard_snapshot(_task)/settlement + the main.py wiring myself. UFC market structure + farm-whale
location established by two Sonnet sub-agents (external web + local repo; no box, no creds). Board-approved
`pm_shards_ro.ps1` run once (read-only, folded into §7). No branch created, nothing built.
