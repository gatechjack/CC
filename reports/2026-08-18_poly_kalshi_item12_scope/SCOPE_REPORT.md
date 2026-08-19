# poly_kalshi_mlb — READ-ONLY scope of TWO items (Item 1 conflict gate, Item 2 mark-poller quote fix)

**Session:** 2026-08-18 · branch `poly-kalshi-item12-scope-2026-08-18` (worktree, base `poly-kalshi-phase2a-2026-08-16` @ `827bea9`).
**Nothing built. Nothing deployed. Live loop untouched.** Two SEPARATE checkpoints proposed; STOP for ratification before either build.

---

## LIVE-MONEY STATUS (up front)

- **ARMED. Real money.** `poly_kalshi_mlb`: `auto_execute=True` -> `dry_run=False`, `halted=False`, `$5` fixed stake, `$100/day` loss-halt + `25/day` count-halt. Roster = **2 live whales** (SDTrading, xifutloong3).
- **Engine PID (DERIVED, not live-verified this session): `775659`.** The shared engine (web+engine = one process) was restarted for today's MACE deploy — `prod-live` tip `653a649`: *"deploy(mace): weekly_new_rungs_per_symbol 1 -> 5 LIVE 2026-08-18 12:40 UTC (RESTART 765455->775659)"*. That restart re-armed poly_kalshi as a side effect.
- **I have no prod access this session** (Jack runs all runners). To confirm arm flags + halt + PID empirically: `powershell -ep bypass -f .\pk_status_ro.ps1` (existing RO runner). I am labelling PID/arm as derived-from-deploy-log, not narrated as verified.
- poly-kalshi code on `prod-live` = `e7af3bc` (Phase 2a + Phase 2b catch-up); everything after on prod-live is MACE-only. The dev line `poly-kalshi-phase2a-2026-08-16` (`827bea9`) fully contains phase2b-cp3 (0 ahead / 16 behind) — it is the correct build base.

### The 3 byte-locked shared files — NEITHER item touches any of them
`kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py`.
- **Item 1** lives entirely in poly_kalshi-own files (`poly_kalshi_executor.py`, optionally `mlb_poly_kalshi_match.py`). No byte-locked file touched.
- **Item 2** lives in `brokers/kalshi.py` (NOT in the locked-3, but IS shared). `kalshi_copy_trader.py` is only a *caller* of the fixed method — it stays byte-unchanged; only its runtime behavior improves. Diff every checkpoint to prove it.

---

# ITEM 1 — first-side-wins conflict gate (Option B)

### KEY Q — does placement have game-level position awareness?
**No. Game-awareness must be BUILT — but it is SMALL** (a pure ticker→game-key derivation + one new in-process/DB gate that mirrors the existing `[G-idem]` pattern). It is NOT a large subsystem.

Evidence — the executor's only "do we already have this?" state is `[G-idem]`:
- `poly_kalshi_executor.py:264` `self._placed: dict[str, ProposedKalshiOrder]` keyed on the **coid** = `client_order_id(division, whale_wallet, ticker, outcome, action)` (`:63-67`, `:216`). That is **wallet+ticker+outcome+action** keyed, **in-process only** (`:263` "state: IN-PROCESS ONLY. No audit_event / DB aggregate query.").
- Two whales betting **opposite** teams on one game → **different tickers** (BAL's YES ticker vs TB's YES ticker) **and different wallets** → **different coids** → both pass `[G-idem]` (`:306`) and every other gate → **both sides placed = the guaranteed -100% vig leg.** This is exactly the observed 08-17 BAL@TB loss.
- The daily counters `_orders_today` / `_deployed_usd` are also in-process (`:256`, `:265`) and reset only on UTC rollover (`poly_kalshi_copy_trader.py:89-101`). **There is no boot reconciliation of placed/game state** — `_scheduled_poly_kalshi_loop` (`main.py:5184-5248`) does a startup settlement-sweep + index refresh only; `_last_seen_ts` cold-starts by seeding the high-water mark without re-emitting (`poly_kalshi_copy_trader.py:218`).

### GAME IDENTITY — confirmed: both side tickers collapse to ONE key
`parse_kalshi_mlb_ticker(ticker)` (`mlb_poly_kalshi_match.py:192-219`) parses a `KXMLBGAME-{YYMMMDD}{HHMM}{TEAMS}[G{n}]-{YES}` ticker into `ParsedKalshiTicker(date_str, time_str, yes_code, other_code, yes_name, other_name, game_no)`.
- The **game key** = `(date_iso, time_str, game_no, frozenset{yes_name, other_name})` — the exact `gk` tuple already used at `:255-256` inside `build_kalshi_game_index`. **Both** the BAL ticker (`...BALTB-BAL`, yes_name=Orioles) and the TB ticker (`...BALTB-TB`, yes_name=Rays) produce the **same** `frozenset{Orioles, Rays}` + same date + same game_no → **same key.** Confirmed by construction.
- The **side** within that game = `yes_code` / `yes_name`. Doubleheaders are disambiguated by `game_no` (`G1`/`G2`) + `time_str` (both in the key), so the two games of a DH are correctly distinct keys — the gate will NOT cross-block them.
- Pure function, no network — O(1), safe on the seconds-critical path.

### "Already took a side" — precise definition + opposite-vs-same
- **Definition (recommended):** a game has "a side taken" if we have **committed** (status `placed` OR `DRY_RUN_would_place`) at least one poly_kalshi_order today whose ticker maps to this game key. (Not "open position" — simpler and equivalent in-game; the first side rarely resolves before an opposite signal arrives.)
- **Gate logic** (mirrors `_placed`): keep `game_key -> side_code_taken`.
  - key unseen → allow, record this side.
  - key seen, **same** side_code → **ALLOW** (same-side stacking: a 2nd whale on the same team). Note an *exact* same-whale re-fire is already `suppressed_duplicate` at `[G-idem]`.
  - key seen, **different** side_code → **BLOCK** → new status `skip_conflict`.
- **Placement in the gate chain:** right **after `[G-idem]`** (`submit()` `:305-307`), before commit. All state mutation happens only after every gate (`:330-343`), so a conflict skip **consumes no budget, burns no coid, increments no count** — identical to the other skips.

### Conflict-skip log location
Reuse the existing durable journal. `_record("skip_conflict", order, trigger=...)` (`poly_kalshi_executor.py:373-404`) writes one `audit_event` row (`actor=poly_kalshi_mlb`, `kind=poly_kalshi_order`, `status=skip_conflict`) carrying ticker/whale/side + a reason (held side vs blocked side). This is the SAME journal the CP3 dashboard OPEN query and the marks poller already read (`poly_kalshi_marks.py:41-49`) — a future dashboard "conflict-skip" state is a filter on `status='skip_conflict'`, no new table.

### ★ FORK for Jack (the one real decision) — restart durability
The gate needs a source for `game_key -> side_taken`. Three options:

| Option | Survives engine restart? | Cost | Notes |
|---|---|---|---|
| **A. In-process only** | **No** | lowest | Mirrors `_placed`/`_orders_today` exactly. But a side placed pre-restart + the opposite signal arriving post-restart → BOTH placed. The shared engine restarts often (e.g. today's 12:40 UTC MACE deploy), so this hole reopens routinely mid-game-day. |
| **B. Durable query at gate time** (recommended) | **Yes** | one small DB read *only on a real candidate placement* (rare — new moneyline moves only) | Reuse the marks poller's journal query (`poly_kalshi_marks.py:41-63`): "any committed poly_kalshi_order today whose ticker shares this game key". Not the per-cycle full-scan that froze the engine (that ran every poll); this runs only when we're about to place. |
| **C. Hybrid** | **Yes** | one startup query + in-process thereafter | Seed `_game_sides` on boot from today's journal, maintain in-process. Most code. |

**Recommendation: B** (or C). The gate exists specifically to stop a guaranteed -100% loss; an in-process-only gate silently disarms after every shared-engine restart. Given restart frequency, durable is the correct posture. **This is the fork to ratify before I build.**

### Shared-files confirmation (Item 1)
Touches `poly_kalshi_executor.py` (+ optional pure helper `game_key_from_ticker` in `mlb_poly_kalshi_match.py`, its natural home). **NONE of the 3 byte-locked files.** Confirmed.

### Item 1 checkpoint plan (ONE checkpoint)
1. (opt) `mlb_poly_kalshi_match.py`: pure `game_key_from_ticker(ticker) -> (game_key, side_code) | None` (reuses `parse_kalshi_mlb_ticker`).
2. `poly_kalshi_executor.py`: `_game_sides` state + `[G-conflict]` gate after `[G-idem]` + `skip_conflict` status + record side at commit. If Fork=B, the gate calls the durable query; if C, seed on boot.
3. Tests in `test_poly_kalshi_executor.py`: opposite-side blocks (skip_conflict, no state mutation), same-side stacks (2nd whale allowed), same-whale re-fire still `suppressed_duplicate`, DH G1/G2 not cross-blocked, unparseable ticker → fail-OPEN + log (idem/slippage still apply).
4. Full suite diff (base-vs-branch) must be empty of new failures. Diff the 3 byte-locked files = unchanged.
5. STOP for review, then operator deploy per the file-overwrite/prod-live-advance rule.

**Undecided for Jack:** (a) the restart-durability fork A/B/C (recommend B); (b) unparseable-ticker behavior — recommend fail-OPEN (allow + log) since a non-parseable KXMLBGAME can't be reasoned about and idempotency+slippage remain.

---

# ITEM 2 — mark poller quote() fix (sparkline never worked)

### Confirmed: two DIFFERENT quote paths — the broken one is NOT the placement path
- **BROKEN (marks poller):** `poly_kalshi_marks.py:113` `await broker.quote(...)` where `broker` = the shared `kalshi_broker_for_resolver` (`main.py:2156-2176`) = a `KalshiBroker`. `KalshiBroker.quote()` (`brokers/kalshi.py:276-306`) does `market = get_market(symbol)` → `ob = get_orderbook(depth=1)` → `getattr(ob,"yes_bids"/"yes_asks")` → these come back empty/None for KXMLBGAME → returns `0.0` → `yes_mid<=0` → `quote_miss` every cycle (2,029/2,029). `KalshiLiveBroker.quote` (`kalshi_live.py:299-300`) delegates to the same `_read.quote`.
- **WORKING (slippage guard):** the guard's quote is `_pk_quote_fn` (`main.py:1485-1494`), which reads `get_market(ticker).yes_ask_dollars` / `.yes_bid_dollars` (MarketModel top-of-book, in dollars) and returns `{yes_ask, yes_bid}`. **It never calls `KalshiBroker.quote()`.** Live placements have occurred (Phase 2a open positions), so this dict path demonstrably works — otherwise the guard would `blocked_slippage_no_quote` in live (`poly_kalshi_executor.py:316-320`).

**⇒ The handoff's worry "the slippage guard on placement calls quote!" is empirically FALSE for this division.** A fix to `KalshiBroker.quote()` **cannot touch live placement here** — the placement/slippage path uses a separate MarketModel API. This is the one place the "cosmetic" fix could have touched live money, and it is clear.

### EVERY caller of `KalshiBroker.quote()` + fix impact (complete audit)
| Caller | Uses quote() for | Today (0.0) | After fix | Verdict |
|---|---|---|---|---|
| `poly_kalshi_marks.py:113` | mark-to-market of open positions | quote_miss forever | real mid → marks/sparkline work | **TARGET — fixed** |
| `kalshi_copy_trader.py:619-623` (byte-locked *caller*) | **fallback** exit price *after* resolution check, guarded `if yes_mid>0` | falls through → round-trip pnl=0 | real mid on still-trading markets → correct exit price | **Helps; cannot break** (>0 guard already absorbs 0.0; resolution-first unchanged) |
| `kalshi_live.py:344` (byte-locked *caller*) | `place_order` base-price **fallback** only when no limit_price | raises `KalshiNoFill` (skip) | real base_price | poly_kalshi never hits this (always supplies base_price + uses its own POST). Shared kalshi_copy live path *could* newly fill where it skipped — **FLAG: confirm kalshi_copy live status before deploy** (if paper/dormant, negligible) |
| `portfolio.py:49` | portfolio position mark | Kalshi pos value = qty*0 = 0 | correct valuation | **Helps** |
| `_pk_quote_fn` (slippage guard) | — | **does not call quote()** | **unaffected** | **No live-trading risk** |

(Non-Kalshi `.quote()` callers — RH/tasty/coinbase/polymarket/paper brokers — are different classes, irrelevant to a `KalshiBroker.quote()` change.)

### Sub-cause disambiguation — NEEDS the one allowed live probe
Two candidate root causes, and the probe decides:
- **(i) attribute mismatch** — the installed pykalshi's orderbook object doesn't expose `yes_bids`/`yes_asks` under those names (getattr → None silently). Note the in-code version ambiguity: `brokers/kalshi.py:245` comment says pykalshi **1.0.6**; memory trap says **2.0.0**. A version bump could have renamed the fields (e.g. `yes`/`no`, `bids`/`asks`, nested).
- **(ii) empty/structural** — `get_orderbook(depth=1)` returns an empty or differently-shaped structure for KXMLBGAME (e.g. book under a different container, or depth-param behavior).

**Probe runner (READ-ONLY, pre-authorized by the handoff): `pk_markprobe_ro.ps1`** — dumps, for a live KXMLBGAME ticker: pykalshi `__version__`; `type(market)` + `yes_ask_dollars`/`yes_bid_dollars`/`yes_ask`/`yes_bid`/`last_price`; `type(orderbook)` + `repr` + public `dir()` + getattr of `yes_bids/yes_asks/no_bids/no_asks` and alt names `yes/no/bids/asks`; a live `KalshiBroker.quote()` call (expect 0.0, reproduces the bug); and the working MarketModel dict. No order, no mutation, does not touch the live loop. Best run during an MLB game window (live book); the MarketModel top-of-book prints regardless.
Run: `powershell -ep bypass -f .\pk_markprobe_ro.ps1`

### Likely fix (to be FINALIZED from the probe dump — not asserted now)
- **Fix B (probable, cleanest):** replace the orderbook parse in `KalshiBroker.quote()` with the **proven** MarketModel mid — `(yes_bid_dollars + yes_ask_dollars)/2` — i.e. the exact source `_pk_quote_fn` already uses live. One API call, no orderbook object, keeps the float-mid-in-dollars contract for all callers.
- **Fix A:** if the probe shows the book IS present under different attribute names → correct the `getattr` names at `brokers/kalshi.py:298-299` (+ `_best_price` at `:479`), keeping the orderbook approach.
- Either way the change is confined to `KalshiBroker.quote()` (`brokers/kalshi.py:276-306`). I will state the exact fix + file:line once the dump is in.

### Item 2 checkpoint plan (ONE checkpoint, deployed SEPARATELY from Item 1)
1. Jack runs `pk_markprobe_ro.ps1` (read-only) → I read the dump → finalize Fix A vs B.
2. `brokers/kalshi.py` `quote()` only. Diff the 3 byte-locked files = unchanged.
3. Tests: `test_poly_kalshi_marks.py` + a `KalshiBroker.quote` test mocking the REAL structure the probe reveals; assert mid returned + mark rows written.
4. Confirm-deploy check: after deploy, `poly_kalshi mark tick` should show `marked>0` (vs the historical all-`quote_miss`).
5. STOP for review; deploy per file-overwrite/prod-live-advance rule.

---

## What I did NOT do
- Did not build, edit runtime code, or deploy. Live loop remains ARMED and undisturbed.
- Did not run any prod command (no prod access; Jack runs runners). The one allowed live diagnostic is authored as `pk_markprobe_ro.ps1` for Jack to run.
- Kept the two items strictly separate — two independent checkpoints.

## STOP — awaiting ratification
1. **Item 1:** ratify the conflict-gate scope + pick the restart-durability fork (A/B/C — I recommend **B**) + unparseable-ticker policy (recommend fail-open+log).
2. **Item 2:** approve Jack running `pk_markprobe_ro.ps1`; I finalize the exact fix from the dump, then you ratify that build.
