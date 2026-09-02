# MULTI-CATEGORY-PER-ACCOUNT PLAN — kalshi_jack/ufc alongside kalshi_jack/mlb (2026-09-02)

**This is a PLAN. Nothing here was built, deployed, armed, or written to the box.** The only box touch proposed
is one read-only runner (below), staged for board authorization. Two accounts remain ARMED and TRADING; I did not
disarm. Global STOP (kills both), verbatim:

    PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global

Branch `pm-per-account-trading-2026-09-02` @ `da5b02a` (worktree `cc-pm-peracct-wt`), clean, local==origin,
base `f1e28cc`. prod-live `7220e32` / main-wip NOT advanced. Box-is-truth.

---

## 0. WHAT I VERIFIED vs ASSUMED (read the code, not the summary)

**Verified (read the files at `da5b02a`, CR-stripped hashes):**
- Worktree clean; HEAD==origin. The 5 package files match SW9's deploy manifest exactly, and **`main.py` at HEAD
  (CR-stripped) = `9e8da82` = the live box graft**. The code below IS what the box runs. The "VC gap" was a
  measurement artifact (`git show|sha256sum` emits CRLF vs the box's LF); it is closed on this tip. No phantom drift,
  no capture-and-commit needed.
- `driver_roster.plan_driver_tasks` (the guard) refuses the 2nd sub-division on an account (`driver_roster.py:80-84`).
- `boot_reconcile`'s journal read is **already account-wide across every category** (`_JOURNAL_SIGNED_SQL`,
  `journal_signed_positions`, `boot_reconcile.py:78-119`); the Kalshi side is the whole book. The *comparison* is
  whole-account; only the **latch target** is per-`(account_id, category)` (R-f note, `boot_reconcile.py:50-53`).
- `arm.latch_auth_failure` **already accepts and loops a list of categories** (`arm.py:273-280`). M2's mechanism
  exists in the control plane; the gap is purely the call site (`live_driver.py:419`) passing `[sub.category]`.
- `open_usd`/gate 6 is **account-keyed** (`execution.py:295,332,343,485`); `daily_usd`+`orders_today` are
  **`(account_id, category)`-keyed** (`execution.py:342,344`). Journal is built **per cycle, per task**
  (`live_driver.py:585`). Gate-6 live base = `venue_exposure.open_dollars() + journal.in_cycle_open_usd(aid)`
  (`execution.py:484`), where `in_cycle_open_usd` sees only THIS task's commits (its own Journal) and never the
  sibling's `submitting` rows (the seed filters `outcome_status='filled'`, `execution.py:315`).
- gate-4 COID = `client_order_id(sub.division, ...)`, `division = "account:category"` (`execution.py:47,111-113,460`)
  → category-distinct.
- gate-6b (shard funding) is **per-MARKET-shard**, tri-state, fail-closed, handles shard 0 correctly (`is not None`,
  not truthiness — `execution.py:499-505`, `shard_balance.py:58-77`). The skip is visible (`live_driver.py:377-380`)
  plus a sustained-underfunding ALARM (`live_driver.py:666-672`).
- Wiring: `main.py:1539-1615` spawns ONE `KalshiLiveBroker` per DISTINCT account (`_pm_brokers[aid]`) and ONE
  `scheduled_pm_live_loop` task per `(account,category)`, all sharing one positions_client. **Two same-account
  categories would share ONE broker** and its underlying client.

**Assumed / could not verify from the repo:**
- **pykalshi concurrent-POST safety.** Our `KalshiLiveBroker` wrapper adds **no** per-request mutable state
  (`kalshi_live.py:315-320,381`); `make_place_fn` calls `client.post` on the shared client (`live_driver.py:288`).
  Whether two simultaneous POSTs on one pykalshi client are safe (RSA-PSS signing / nonce reentrancy) reduces to
  pykalshi internals, which are **not vendored** and cannot be confirmed locally. Verdict: **NOT PROVABLE FROM CODE**
  — treat concurrent same-account POSTs as unproven. (This is itself an argument for the M1 recommendation below.)
- **UFC market types** — established by external research, not the box (see §5).
- **Today's shard-0 balance** — inferred from SW9 (jack ≈ all on shard 3, shard 0 ≈ $0.01); a fresh read is staged.

---

## 1. THE CENTRAL QUESTION — were M1/M2/M3 the COMPLETE set, or the three that surfaced overnight?

**Answer: they are the complete set of "an account-scoped safety that DEGRADES by adding a 2nd category to the
CURRENT two-task-per-account architecture" — but "three fixes" is an INCOMPLETE to-do list**, for two reasons:

1. **The recommended M1 fix (one driver task per account, §2) RE-SCOPES three pieces of per-task state that a
   single multi-category task would otherwise share across categories.** These are not new account-safety
   degradations; they are correctness hazards INTRODUCED by collapsing two tasks into one, and each is a
   "safety-check-that-silently-stops-checking" shape:
   - **The sustained-shard-underfunding ALARM counter** (`consec_underfunded`, `live_driver.py:528,666-674`) is a
     per-TASK local. Under one task for mlb+ufc, an mlb placement (`summ["placed"]>0`) RESETS the counter, so a
     genuinely starved ufc shard-0 would **never raise its sustained alarm** — masked by unrelated mlb activity.
     Must become **per-category** (or per-shard).
   - **The consecutive-error latch counter** (`consec_err`, `live_driver.py:367,416,422-423`) is per-task; an
     mlb fill (`consec_err=0`, line 433) would reset a ufc error streak. Must stay **per-category**.
   - **The Option-D exit snapshot map** (`prior_snapshots`, `live_driver.py:533,604,625`) is keyed by wallet
     alone. A wallet pinned in BOTH categories (the attachment table permits `(wallet, mlb)` and `(wallet, ufc)`)
     would have its two books MERGED under one task → corrupt reduction/exit detection. Must be keyed
     **`(category, wallet)`**.
2. **Two by-design account-aggregate implications that are correct but must be RULED, not assumed:** the daily
   cap (gate 5) and orders/day (gate 8) are **per-`(account,category)`**. Two categories at Ruling-2 caps means the
   ACCOUNT can spend **2×$150 = $300/day** and place **2×50 = 100 orders/day**. That is the intended per-subdivision
   semantics, but the account's aggregate risk scales with the category count. The **open-exposure cap (gate 6) is
   the only account-wide cap** — which is exactly why M1 is the hard one.

So the real work list is: **M1 (+ its re-scoping trio) + M2 + M3**, then **M5 verify**, then **the aggregate-cap
ruling**, then **M4 (the guard)**. Everything else account-scoped I re-checked is safe per-category (§4).

---

## 2. M1 — THE `open_usd` CAP RACE

### The race, precisely
Two same-account tasks (jack/mlb, jack/ufc) run as concurrent asyncio tasks in one event loop. Each builds its OWN
per-cycle `Journal` and its OWN venue-exposure read. Gate 6 base for each = `venue_open + own_in_cycle`. Neither
sees the other's in-cycle commits (separate Journal objects) nor the other's pre-fill `submitting` rows. They
interleave at the POST `await` (`live_driver.py:411`). So both can independently authorise up to the cap against
the same base.

### How much does R7's account-wide venue base mitigate it?
**Substantially, cross-cycle; not at all, within an overlapping cycle.** R7 reads the account's TRUE filled
exposure from `/portfolio/positions` fresh every cycle (`live_driver.py:562-567`). Once a sibling's orders FILL and
appear as positions, the NEXT cycle of either task re-anchors to reality and gate-6-rejects further entries — so the
breach **cannot compound across cycles**. What it does NOT catch: two tasks reading the same venue base **before
either's orders fill and propagate**, each adding only its own in-cycle. Entries are IOC (`_ENTRY_TIF`,
`execution.py:71`) → fill-or-not immediately → a fill becomes a `/portfolio/positions` line near-immediately, but
not before the sibling's same-window cycle already sized.

### The race window in practice
- **Width:** ≈ one poll interval (~7 s, `poll_sec=7`), because each task re-reads the venue only at its own cycle
  start; any two cycle-starts falling in the same ~7 s window before fills propagate both size against the stale
  base. This recurs roughly every cycle both categories have gate-passing entries.
- **Magnitude:** worst case in one overlapping cycle, both tasks fill up to `(cap − venue_base)` each, so the
  account reaches up to **`2·cap − venue_base`** (≈ **$300 against a $150 cap** when the book starts near flat).
  It self-corrects the next cycle (both then read the inflated venue and reject) but the account sits at ~2× cap
  until those positions close/settle.
- **Probability:** rises with open-position count and simultaneous signal bursts. To be near the $150 cap needs
  ~27 open contracts ($5.50 each) — and the *exact* trigger is a Saturday with both a full MLB slate AND a UFC
  card firing entries in the same windows. **"Unlikely in practice" is therefore not defensible** — the whole point
  of a 2nd category is more simultaneous signal, and fight-nights are the peak-overlap case.

### Coordination options + cost
- **Option C — ONE driver task per account, iterating its categories, sharing ONE per-cycle Journal + ONE venue
  read. [RECOMMENDED]** Because it is a single task, the categories run **sequentially** within a cycle; gate 6's
  in-cycle accumulator (already account-keyed) automatically sees the first category's commits when the second
  category evaluates — the account open-cap is enforced jointly, exactly as it is for one category today. **gate 6,
  `evaluate`, the Journal, and the POST path are UNCHANGED.** No lock, no shared-mutable-state across tasks, and
  **nothing added between "decide to place" and "POST."** It ALSO sidesteps the unprovable concurrent-POST-on-one-
  broker question — a single task never POSTs concurrently with itself.
  - **Cost:** category-serialization latency WITHIN a cycle — the second category's signals wait for the first
    category's placements (bounded by #placements × per-POST latency, the same order of magnitude either way). This
    is latency the two categories were always going to trade against one shared account cap for anyway.
  - **Cost (real, §1):** requires re-scoping `consec_underfunded`, `consec_err`, and `prior_snapshots` to
    per-category inside the restructured loop. Mechanical, but load-bearing.
- **Option B — keep two tasks + a per-account `asyncio.Lock`** held across the read-venue→evaluate→place span.
  **This IS a coordination mechanism spanning the hot path** (the lock is held across POST awaits). Same
  serialization as C but via an explicit primitive on the order path, and it still leaves the concurrent-POST
  question if the lock scope is ever wrong. **This is the thing the brief flags as Jack's ruling, not mine.**
- **Option A — a per-account shared in-flight accumulator** both tasks' gate 6 read/write. Race-free under
  single-threaded asyncio *if* the gate-6-read→commit stays synchronous (it is, within `evaluate`), so it needs no
  lock — but reconciling the shared in-flight against each task's independent per-cycle venue read (avoiding
  double-counting as fills migrate onto the venue with two independent cycle phases) is fiddlier than C for no gain.

### Recommendation + where the hot path sits
**Option C.** It closes M1 **without any hot-path mechanism** — it is a wiring/loop-structure change in
`live_driver.scheduled_pm_live_loop` + the `main.py` roster grouping, not an addition between decide-and-POST. So
**the plan's stop condition is NOT triggered by M1.**

**The honest fork for the board (§9):** C's only "cost" is that the two categories on one account trade
**sequentially, not concurrently**, within each ~7 s cycle. If that latency is acceptable → proceed with C. **If
the board REQUIRES concurrent per-category execution, then M1 can only be closed with a hot-path per-account lock
(Option B) — and adding to that hot path is the board's ruling.** If neither serialization nor a lock is acceptable,
**the guard stays** (per the M4 ruling).

**Hard stop inside the build:** if box-scratch shows Option C does NOT actually enforce the account cap across two
categories sharing one journal (i.e. my analysis is wrong), STOP and do not relax the guard.

---

## 3. M2 — AUTH-LATCH SCOPE

**Status: NOT-SAFE today; mechanism already exists.** A 401/403 is dead auth for the whole KEYPAIR = the whole
account, but `live_driver.py:419` calls `arm.latch_auth_failure(sub.account_id, [sub.category], ...)` — only the
caller's category. The sibling category keeps POSTing on dead auth. `arm.latch_auth_failure` already loops a list
(`arm.py:273-280`), so the fix is at the call site.

**Mechanism:** at the auth-failure latch site, latch **every active category on the account**. Two equivalent
sources for the category list:
- Under Option C the task already holds its category list → pass it directly (cleanest).
- Belt-and-suspenders regardless of task structure: enumerate `SELECT category FROM pm_subdivision WHERE
  account_id=? AND active=1` at latch time (auth is dead → this DB read is not on the hot path, it fires only on
  failure). Recommend doing BOTH: pass the loop's list, and have a shared helper that unions it with the DB read so
  a category with no live task (attachment lapsed mid-session) is still latched.

**Provable:** unit test (forged 401 → all active categories latched, `manual_exit_required=True`); box-scratch with a
forged-401 place_fn on a two-category account → both categories' `arm:...` rows go `latched=True`.

---

## 4. M3 — WHOLE-ACCOUNT BOOT-RECONCILE LATCH

**Status: NOT-SAFE under the recommended single-task model; incidentally-OK-but-fragile under two tasks.**

**Reasoning through both failure directions (two categories, one keypair):**
- **False-latch — cannot happen from cross-category presence.** The journal read is already account-wide
  (all categories) and the Kalshi read is the whole book, so a ufc position present in both journal and book
  reconciles as MATCH. Latching all categories on a *genuine* mismatch is correct — the whole keypair's book is in
  question. Widening the latch introduces no false latch.
- **Missed-latch — the real gap.** `reconcile_account` latches only the single `(account_id, category)` it was
  called for (`boot_reconcile.py:215,300-301`). In today's two-task model each task independently runs an
  account-wide reconcile and latches its own category, so both happen to get latched — but that is incidental and
  fragile (it breaks the moment a category has no task, or one reconcile races ahead and arms). Under Option C
  (ONE account-wide reconcile per account) a single per-category latch would leave the sibling category free to arm
  and trade against an unreconciled book → a genuine miss.
- **R-c precondition unchanged.** KALSHI_ONLY = whole-account is correct only while the account is PM-EXCLUSIVE;
  with two categories the account is still PM-exclusive as a whole (both categories are PM's). A co-tenant/legacy
  sharing the keypair breaks R-c exactly as today — same precondition, not a new one.

**Mechanism:** make the boot-reconcile latch **account-wide** — run the (already account-wide) comparison once per
account and, on mismatch or read failure, latch **all active categories** on the account (loop them, mirroring
`latch_auth_failure`; add `arm.latch_boot_reconcile_mismatch_account(account_id, categories, ...)` or loop the
existing per-category latch). Under Option C also run the boot settlement-scan per-category before the single
account-wide reconcile (so a category that settled while down is booked flat first, `live_driver.py:500-513`).

**Provable:** unit test (a co-category Kalshi position absent from the journal → ALL active categories latched; a
clean two-category account → NO latch); box-scratch with a co-category venue position injected → both categories
latch, jack's other account unaffected.

---

## 5. M5 — SHARD FUNDING (verify-only per the board; funding is the operator's job)

**Verified by reading; behaves exactly as ruled. One box confirmation staged.**
- gate-6b reads the MARKET's `exchange_index` (`execution.py:391`) and checks THAT shard: `can_fund(order_shard,
  notional) if order_shard is not None` (`execution.py:499-505`). Shard 0 is handled correctly — the guard is
  `is not None`, not truthiness, and `shard_balance.can_fund`/`shard`/`by_shard.get(int(idx),0.0)` treat 0 as a
  real key (`shard_balance.py:58-77`). **Nothing assumes a single funded shard per account** — the balance read
  returns the whole per-shard breakdown; the gate is per-market-shard.
- **On insufficient shard → SKIP, and it is VISIBLE, not silent:** a per-copy `WARNING` (`live_driver.py:377-380`),
  counted (`n_shard_underfunded`), plus a SUSTAINED alarm every N=3 cycles while starved (`live_driver.py:662-672`).
  It is a SKIP (fundable-later), never the error-latch. **⚠ Under Option C the sustained-alarm counter must be
  re-scoped per-category (§1) or a starved ufc shard would be masked by mlb placements.**
- UFC markets are MMA → **shard 0 (Default)**, not shard 3 (Tennis & Baseball). SW9 shows jack ≈ all on shard 3
  (`$498.02`), **shard 0 ≈ $0.01**. So a ufc category on jack TODAY would `skip:shard_underfunded` on every copy
  (correct — it does NOT misroute to jack's funded shard 3), until the operator funds shard 0. This is exactly your
  ruling: **you fund shard 0 for ufc; PM places and, on insufficient balance, skips and leaves it to you.**

**Confirm today's shard-0 (read-only, no creds — reuse the existing sanctioned runner):**

    powershell -ep bypass -f .\pm_shards_ro.ps1

(`pm_shards_ro.sh` already prints jack's `by_shard` incl. shard 0 + age via `shard_snapshot.read_latest`, mode=ro,
stdlib-only. This is the "what would a shard-0 category find" read. It is confirmatory — the M5 design does not
depend on the exact number.)

---

## 6. THE PLUS RE-CHECK — every account-scoped safety, classified (never a sweep)

| Item | Scope in code | Two-categories-one-account verdict |
|---|---|---|
| `daily_usd` / gate 5 | `(account_id, category)` | **VERIFIED per-category.** Account aggregate = N×$150/day — by design; RULE it (§9). |
| `orders_today` / gate 8 | `(account_id, category)` | **VERIFIED per-category.** Account aggregate = N×50/day — by design; RULE it. |
| `latch_count_ceiling` | per-category (gate 8) | **VERIFIED per-category-correct.** mlb ceiling latches only mlb. |
| `latch_consecutive_errors` | per-task `consec_err` | **SAFE two-task; NOT-SAFE under Option C** unless kept per-category (§1). |
| opposed-side guard | `(account_id, category)` (`execution.py:687,714`) | **VERIFIED SAFE.** Cross-category opposition is semantically impossible (mlb/ufc never share `condition_id`). |
| gate-4 COID | `division=account:category` | **VERIFIED SAFE** (category-distinct coids). |
| settlement scanner | `(account_id, category)`, matched by ticker (`settlement.py:142,160`) | **VERIFIED per-category-correct.** Under Option C, loop categories. |
| shard-snapshot writer | per-account, full breakdown (`shard_snapshot_task.py`) | **VERIFIED SAFE** (category-agnostic; unaffected by category count). |
| Option-D `prior_snapshots` | per-task, keyed by wallet | **SAFE two-task; NOT-SAFE under Option C** unless keyed `(category,wallet)` (§1). |
| `consec_underfunded` alarm | per-task local | **SAFE two-task; NOT-SAFE under Option C** unless per-category (§1). |
| per-market cap | absent (backlog) | **PRE-EXISTING, unchanged.** N whales × N contracts stack within ONE market; two categories don't newly interact (different markets). Not a new gap; still filed. |
| N1 `resolve_kalshi_keys` | fail-closed whitelist | **VERIFIED SAFE** (unmapped ref → skip, never jack's keys). |

---

## 7. ESTABLISH-FIRST RESULTS (read-only)

1. **Broker concurrency-safe for two simultaneous POSTs?** Our wrapper is stateless per request; the answer reduces
   to pykalshi's signed transport, **not vendored → NOT PROVABLE from code**. Option C makes it moot (no concurrent
   same-account POST). If Option B is ever chosen, this must be proven on box-scratch (two forced-concurrent POSTs
   against a stub) before trusting it.
2. **M1 race window:** ≈ one poll interval wide, recurring; magnitude ≤ `2·cap − venue_base` (~$300 vs $150); bites
   at high position count + simultaneous bursts (MLB slate + UFC card) — see §2.
3. **Shard-0 balance / what a shard-0 category finds:** jack ≈ all on shard 3, shard 0 ≈ $0.01 (SW9); a ufc copy
   would skip:shard_underfunded until you fund shard 0. Fresh read staged (§5).
4. **What UFC offers on Kalshi (shapes the matcher, config, caps):**
   - **KXUFCFIGHT** — moneyline; ONE binary market per fighter, two per bout; event ticker
     `KXUFCFIGHT-{YYMONDD}{FTR1}{FTR2}`, per-fighter `...-{FTR}`. **Clean 1:1** with Polymarket's winner market.
   - **KXUFCDISTANCE** — "go the distance"; binary per bout, **no strike/line**. **Clean 1:1** with Polymarket
     `-go-the-distance`.
   - **KXUFCMOV** (method of victory) — Kalshi one multi-outcome market vs Polymarket per-method binaries →
     **possible but complex**, not MVP.
   - Round O/U (Polymarket `-totals-Npt5`) is **Polymarket-only**; round-of-victory (KXUFCVICROUND) is
     **Kalshi-only**. Neither is copyable.
   - **So UFC's copyable shape = 2 binary types {moneyline, distance}, distance carrying NO line** — NOT MLB's
     3-type {moneyline, total, spread}. Cadence ~3–4 events/mo, ~12 bouts/event, bursty on fight-night (Sat);
     Polymarket carries full props only for featured bouts, prelims are winner-only.
5. **Candidate UFC whales (farm is already UFC-aware):** `"ufc"` is in `CATEGORY_ALLOWLIST` (`search.py:61`),
   category mapping exists (`category.py`), and **5 UFC whales are already PINNED** in `pm_watchlist`:
   **Kh4mz4t, STC14, 000why000, 4751346, kutsumiakia** (FARM_RERANK_2026-08-23). "Which whales trade UFC" is a live
   query: `SELECT wallet FROM pm_category_stats WHERE category='ufc' ORDER BY roi DESC`. The **matcher is
   greenfield** — `execution.py:49` hardwires `mlb_poly_kalshi_match`; `evaluate` calls `M.parse_poly_mlb_bet`
   (`execution.py:383`), and `live_driver.SERIES` (`:60`) + `fetch_market_context` are MLB-only.

---

## 8. THE PLAN — workstreams, rungs, and what is provable before each deploy

Two workstreams converge at M4. **A** is the capability (a UFC matcher); **B** is the guard's protection (M1–M3).
The guard cannot open until BOTH the safety fixes land AND a matcher exists — a relaxed guard with no ufc matcher
would spawn a ufc task that skips every signal (`skip:non_ufc`), harmless but pointless.

### Workstream A — the UFC matcher (prerequisite capability; category-specific)
- **A1.** `trading_corp/data/ufc_poly_kalshi_match.py`, mirroring the mlb matcher's public surface: parse a
  Polymarket ufc bet (slug+outcome → fighter, market_type ∈ {moneyline, distance}); parse `KXUFCFIGHT` /
  `KXUFCDISTANCE` tickers; **fighter-name canonicalization** (new — there is no `UFC_FIGHTERS` map, cf.
  `MLB_TEAMS`); `build_*_index`; `match_bet(..., allowed_market_types)`; `liquidity_ok`. Join key = (fight date ET,
  normalized fighter-pair set) — the analog of the MLB canonical team-set join. Moneyline has a leg; distance is a
  single binary with no line.
- **A2.** Make the chokepoint **category-dispatched**: `evaluate` selects the matcher by `sub.category` (a small
  registry `{"mlb": mlb_match, "ufc": ufc_match}`, or inject the matcher into `SubConfig`/the cycle). Parameterize
  `live_driver.SERIES` + `fetch_market_context` by category (MLB series vs `KX UFC*` series).
- **Provable:** unit tests on real recorded UFC fixtures (a KXUFCFIGHT pair + a KXUFCDISTANCE, a Polymarket ufc
  winner + go-the-distance); box-scratch DISARMED (dry-run) against live UFC market data — assert would-place bodies
  match real tickers, `skip:no_quote` on unmatched. **INERT (no guard change, no arm).**
- **NOTE:** this is a substantial build, comparable to the original MLB matcher, and could be its own plan. Method-
  of-victory is explicitly OUT of the MVP (Kalshi multi-outcome vs Poly per-method binaries).

### Workstream B — the safety fixes (guard preconditions), in order
- **B1 (M2):** latch all active categories on auth failure (§3). Unit + box-scratch forged-401.
- **B2 (M3):** account-wide boot-reconcile latch (§4). Unit + box-scratch co-category venue position.
- **B3 (M1, Option C):** restructure `scheduled_pm_live_loop` to ONE task per account iterating its categories,
  sharing ONE per-cycle Journal + ONE venue read + ONE shard read; re-scope `consec_underfunded`, `consec_err`,
  `prior_snapshots` to per-category; loop settlement-scan/index per category (§2, §1). `main.py` roster groups the
  spawn by account and passes the category list.
  - **Provable (exactly the brief's tests):**
    - *box-scratch with two same-account tasks + a FORCED-CONCURRENT placement* — run the **current two-task model**
      to EMPIRICALLY DEMONSTRATE the race (both size against the same base, account exceeds cap). This justifies
      that M1 is real and that C (not "unlikely in practice") is the fix.
    - Then prove **Option C eliminates it**: one task, mlb consumes most of the shared cap, ufc's gate 6 then
      REJECTS at the shared account cap; assert the account never exceeds `max_open_usd`.
    - Assert no cross-category corruption: a wallet pinned in both categories keeps separate exit snapshots; a
      starved ufc shard raises its sustained alarm even while mlb places; a ufc error streak isn't reset by mlb.

### M4 — the guard, LAST, behind a fail-closed per-account opt-in OFF by default
- Relax `plan_driver_tasks` to GROUP a 2nd category onto an account's task **only when that account is explicitly
  opted in** (a `pm_account.multi_category_ok` column or a config allowlist, default OFF). Default behaviour is
  **unchanged** (refuse the 2nd sub-division, log ERROR). The opt-in flips one named account at a time.
- **Provable:** unit tests — opt-in OFF → still refuses (byte-identical to today); opt-in ON for jack → jack's two
  categories group into one task; opt-in ON for jack does NOT affect karen.
- **★ THE BOARD'S STANDING RULING, honoured:** the opt-in ships **only after M1+M2+M3 (+ the §1 re-scoping) are all
  closed and proven.** If ANY of them cannot be closed, **the guard stays** — two-of-three is not a reason to open
  it, and "the remaining race is unlikely" is not an argument. If we reach that state I report it and stop.

### Enablement rung (all HALT-for-board; DB writes / restart / arm)
1. **Fund shard 0** (operator; your ruling) so ufc copies don't skip from cycle 1.
2. **Set jack's opt-in** + create/confirm `(kalshi_jack, ufc)` with the board's ufc caps; attach the chosen ufc
   whales (active=1). LIVE PM-DB write, backup + resolved-verify, HALT.
3. **Restart** (canonical `restart_tc.ps1`) → the jack account task now iterates {mlb, ufc}. Post-check: roster log
   shows one task per account with both categories; **boot-reconcile clean for the whole account**; ufc DISARMED
   (no arm row). HALT.
4. **Arm jack/ufc.** First ufc order fires at full configured size; verify the fill landed on jack's book and that
   gate 6 now reflects the shared account cap across both categories. HALT.
- Graft rules unchanged: `app.py`/`main.py` GRAFT never wholesale (box app.py is M4-era + HEAD carries undeployed
  M5; box main.py carries the per-account roster). This work touches ZERO pm_web files.

---

## 9. WHAT THE BOARD MUST RULE (options + my recommendation; I did not auto-resolve)

1. **UFC caps & sizing — discovery has landed, so this is now rulable.** UFC copyable types = **moneyline + go-the-
   distance (2 binary types; distance has NO line)**, ~12 bouts/event, bursty on fight-night.
   - *Recommendation (a conservative starting point, mirroring MLB Ruling-2):* `sizing_mode='contracts'`,
     contracts=5, per_order=$5.50, **market_types=('moneyline','distance')**, slippage 2c, liquidity 0.75.
     For daily/open, note the account aggregate (§1): either accept jack's account at $300/day-$300-open across the
     two categories, **or** set ufc lower (e.g. daily/open $75 each) so the account aggregate stays near $150. My
     lean: **start ufc at daily=$75 / open=$75 / 25 orders** given a single fight-night can burst ~12 bouts, then
     raise after observation. **This is the board's number.**
2. **Which UFC whales to attach** to `(kalshi_jack, ufc)`. Five are pinned (Kh4mz4t, STC14, 000why000, 4751346,
   kutsumiakia). *Recommendation:* re-rank them by cost-ROI first (`pm_category_stats` / the farm re-rank), then
   attach the top 2–3, mirroring the MLB whale count. **Board picks the set.**
3. **M1 latency-vs-safety** — only if you reject Option C: accept category-serialization within a cycle
   (Option C, my recommendation, no hot-path change) **vs** a per-account hot-path lock for concurrent categories
   (Option B, an addition to the order path = your ruling). If neither, the guard stays.
4. **Account-aggregate cap acknowledgement** (§1): confirm you accept that per-category caps make jack's aggregate
   daily spend / order count scale with the category count, or set ufc's caps lower per (1).

---

## 10. STOP CONDITION (every plan this week has had one)

- **M1 can be made safe WITHOUT a hot-path mechanism (Option C), so I am NOT stopping.** If, and only if, the board
  requires concurrent per-category execution, closing M1 needs a hot-path lock — that is the board's ruling; if the
  board rejects both serialization and a lock, **the guard stays and this capability is not built.**
- **Hard stop inside the build:** if box-scratch shows Option C does not actually enforce the account open-cap
  across two categories on one shared journal, STOP and do not relax the guard.
- **The M4 ruling stands:** if ANY of M1 / M2 / M3 (or the §1 re-scoping they entail) cannot be closed and proven,
  the guard stays. "Do not build this yet" remains a legitimate, valuable output.

---

## 11. HOW I WORKED / provenance
Read-only throughout. Verified branch tips CR-stripped. Read execution.py, live_driver.py, boot_reconcile.py,
arm.py, driver_roster.py, shard_balance.py, shard_snapshot(_task).py, settlement.py, and the main.py wiring myself.
UFC market-structure + farm-whale location established by two Sonnet sub-agents (external web + local repo read; no
box, no creds). One read-only box runner staged (`pm_shards_ro.ps1`, §5), not run. No branch created, nothing built.
