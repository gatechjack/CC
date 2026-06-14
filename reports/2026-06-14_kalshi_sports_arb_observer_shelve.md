# kalshi_sports_arb_observer — Phase 0 SHELVE record (2026-06-14)

**Verdict:** `SHELVE_LATENCY_THESIS_CLOSED` (Hypothesis A) + `INCONCLUSIVE_INSTRUMENT_TOO_WEAK` (Hypothesis B, by design).
**Decision:** Close the division. Disable the observer on prod + repo. No spend, no feed upgrade, no cadence change.
**Pattern mirrored:** the 2026-05-22 `kalshi_crypto` shelve (latency thesis structurally closed; positives were base-rate/stale-quote artifacts).

This record is the canonical truth for the shelve. Memory entries point here; they do not duplicate it.

---

## 1. What was tested

Phase 0 read-only observer (`trading_corp/agents/strategies/kalshi_sports_arb_observer.py`, division
`kalshi_arbitrage`, **never emits orders**) scouting Kalshi MLB game-moneyline binaries vs
`the-odds-api` per-book lines (Pinnacle + DraftKings/FanDuel/BetMGM), 1h poll, qty=10 EV-at-fill:

- **Hypothesis A** — cross-venue arbitrage (lock a profit buying one side on Kalshi, the other on a book).
- **Hypothesis B** — sportsbook→Kalshi lead-lag (Kalshi lags the book; trade the lag).

Live MLB-only since 2026-05-24 02:01 UTC; cap bumped 50→150 markets/series at 14:40 UTC (commit `2dd12bf`)
after the feed-diagnosis established that Kalshi/sportsbook calendar overlap is real-world venue behavior.

## 2. Diagnostic (2026-06-14, after an extended unattended gap)

**System status.** The observer is an agent *inside* `trading-corp.service` (no dedicated unit). Service
`active`, `MainPID=2637434`, `NRestarts=0`, started 2026-06-13 15:36:05 UTC (the bitunix go-live restart).
It is **alive but feed-starved**: scans still fire hourly (454 cycles, last 2026-06-14 14:40:14 UTC) but
every cycle since ~2026-06-04 logs `n_observed:0 / n_no_book_match:80`. `the-odds-api` free quota is
**exhausted** (`odds_api_quota_used:500, remaining:0`) and the key now returns `401 Unauthorized` (since
~2026-06-09 per deploy_log). **Cost during the gap: $0** — no paid tier was ever purchased.

**Corpus.** 8,360 `kalshi_sports_arb_observation` rows, **all MLB `game_ml`**, range
**2026-05-24 15:40:53 → 2026-06-04 20:26:34** (collected ~11 days, then the feed died). 12 distinct
game-days, 105 distinct games. 454 scan cycles; 28,236 `kalshi_sports_arb_unmapped` rows. Payloads
structurally clean (3-row end-to-end sample passed); `kalshi_quote_invalid` only 2/8,360; **Pinnacle
present in 7,178/8,360 (85.9%)** — largely a real sharp-book test, not a soft-book proxy.

> The original "observer died silently within ~3 days on quota exhaustion" expectation is **refuted on
> timing/scope**: it collected a large corpus for 11 days first. The death *mechanism* (free-tier
> exhaustion) is broadly confirmed, just later and after 8,360 rows accumulated.

## 3. Analyzer verdict (`scripts/analyze_kalshi_sports_arb_observations.py --league MLB`)

Run against a **local copy** of the exported rows (prod kept strictly read-only). N=8,360 ≫ 30 threshold.

**Hypothesis A → `SHELVE_LATENCY_THESIS_CLOSED`.** A-arb evaluated 8,340; **positive-EV 1,274 (15.3%)**;
guaranteed-arb (`is_arb`) **1,274 — the identical set**. EV-at-fill @ $10: **mean −$0.3748, median
−$0.3551**, min −$6.471, max +$6.029, stdev $0.82. Mean ≤ −$0.20 kill threshold ⇒ SHELVE.

**Hypothesis B → `INCONCLUSIVE_INSTRUMENT_TOO_WEAK`** (forced by 1h cadence; numbers reported, no verdict):
evaluated 8,340; positive 1,570 (18.8%); mean −$0.2798, median −$0.245, min −$6.28, max +$6.15.
Sub-hour lead-lag is structurally invisible at 1h poll; resolving needs sub-minute cadence (a quota-side
change), which is not justified absent an A signal.

## 4. Hand-verification — the 1,274 "positive guaranteed arbs" are FALSE POSITIVES

1,274 "guaranteed arbitrages" (~15% of rows) on liquid MLB moneyline is not plausible as real edge. On
inspection they are artifacts; **cleaning the data makes the economics worse, not better**:

- **In-game contamination.** 290/1,274 positives were logged *after game start* (observation timestamp
  later than `commenced_at`) — a stale Kalshi quote compared against live in-game book odds of +1480…+3500
  (a team losing mid-game). That bucket shows a 36.7% "positive" rate. Top-EV rows are all post-start, or a
  degenerate `yes_ask:0.01` stale quote.
- **Impossible persistence.** Top games show **51, 50, 44, 42…** positive snapshots *each* — the same
  "arb" persisting ~50 consecutive hourly cycles. A real >1h arb on liquid MLB ML cannot survive 50 hours;
  any shop with a 60s feed would take it. This is the base-rate/stale-quote convergence signature.
- **No positive-mean subset exists.** Pre-game-only: mean **−$0.401** (13% positive). The cleanest
  possible slice (pre-game ≥1h out, Kalshi spread ≤2¢, Pinnacle-backed, sane line |american|<600):
  population mean **−$0.328**, only **9.3% positive**. There is no filter under which mean EV-at-fill is
  positive.

The observer instrument **worked as designed** — EV-at-fill baked in from line one caught a non-edge that a
win-rate or raw-positive-count lens would have flattered (the explicit `kalshi_crypto` lesson).

## 5. Caveats (load-bearing for the verdict)

Mandatory (from the analyzer): hour-scale-only; game-markets-only; MLB grading unverified; **calendar
asymmetry** (venues overlap only in the final ~24h, the most-efficient window — structural, no spend fixes
it); **single-feed limit**; **HOURLY A-ARB PRIOR IS LOW** (a persistent >1h MLB-ML arb would already be
harvested — its absence is the base rate, not a Phase-0 failure). This last one is the load-bearing caveat.

New caveats surfaced by this gap:

- **Silent-quota-death.** No feed-health alarm; the division flatlined ~10 days unnoticed while still
  burning hourly cycles and writing `no_book_match` rows.
- **In-game contamination.** The observer logs post-start snapshots, polluting the corpus.
- **Attention-starvation.** The division received zero operator touch through the gap. Independent of the
  (already negative) edge verdict, this argues for closure.
- (No config drift: the 2026-05-24 cap-bump was committed in `2dd12bf` and prod matches repo at 150 —
  earlier "uncommitted drift" framing was incorrect; verified 2026-06-14 by prod file read.)

## 6. What survives → harness inventory

Promoted to `runbooks/strategy_harness_inventory.md` ("What survives the 2026-06-14 sports-arb shelve"):
the per-book `the-odds-api` client + Pinnacle opt-in wiring; the EV-at-fill-baked-in observer pattern; the
calendar-asymmetry + single-feed structural priors for any future cross-venue inquiry; and two prerequisites
before reuse — a **feed-health alarm** and a **pre-game-only filter**.

## 7. Close-out actions

- Branch `kalshi-sports-arb-shelve-2026-06-14` off `main` `fe0666a` (isolated worktree; parallel session untouched).
- This shelve record; SHELVED banner on `docs/divisions.md`; harness-inventory section.
- `config/strategies.yaml` observer `enabled: true → false` (repo); operator runs the matching prod
  surgical sed (hot-reload, no restart) to converge prod.
- BACKLOG + deploy_log entries. Memory re-seeded (`kalshi-crypto-shelved`, `kalshi-sports-arb-observer-shelved`).

## 8. Reproducibility & disclosure

Analysis scratch on operator Desktop (re-runnable): `ksarb_probe1.sh`, `ksarb_dump_query.sql`,
`ksarb_dump.sql`, `ksarb_build.py`, `ksarb_chars.py`, `ksarb_chars2.py`, `ksarb_local.db`. The analyzer was
run on `ksarb_local.db` (a local rebuild of the exported obs+scan rows), never against the live prod DB.

**SSH disclosure:** all prod access this session was **read-only** — `sqlite3 -readonly` SELECTs,
`journalctl`, `systemctl show`, and config/DB-path file reads. No prod writes, no service control, no config
change performed by the agent.
