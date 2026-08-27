# Stage 1 — RUNG 3 STEP 2: ONE MANUAL POLLER RUN — 2026-08-27

**Authorization (Jack):** "RUNG 3 — STEP 2 OF 4: ONE MANUAL POLLER RUN … a SINGLE manual poll_pinned run ONLY."
Live adjudicate (step 3) + cadence install (step 4) remain **unauthorized**. Executed **17:28 UTC** (clear of the
03:20 cron; nothing else wrote the PM DB during the run). Invocation: `pm_cli paper-poll` (the normal one-shot, no
params). **Exit 0, runtime 2.94 s.**

## Pre-conditions (all matched) + rollback
schema 9 · grace 259200 · pm_paper_trade **102 (102 open, 0 pending, 0 closed)** · pm_paper_category_stats **0** ·
pm_watchlist **114/92/22** · 92 active pinned pairs · 94 trades in deactivated pairs / 8 in active · 14 active
wallets · /farm 200/129578 · PIDs 13102/676. **Gate-1 backup (rollback):**
`~/pm_stage1_rung3_poll_dbbackup_20260827T172646Z.db` (25137152 B, sha `3258682b…`, `integrity_check=ok`).

## Answers

**(a) Pairs polled — 92, not 114.** `totals.pairs=92`; `pm_roster.last_polled_ts` advanced for **92** rows,
**0** of them in deactivated pairs. **The active=1 gate's first live exercise in the poller path — it held.**

**(b) New paper trades: 5, across 3 distinct pairs / 3 categories — ufc·2, mlb·2, cs2·1.** 0 in `unknown`.
Pairs: `SDTrading·mlb (2)`, `4751346·ufc (2)`, `kutsumiakia·cs2 (1)`.

**(c) Open→pending transitions: 3** (of the original 8 active-pair opens). These vanished from the whales'
current `/positions`, so two-phase adjudication marked them `pending_adjudication`:
- `mlb 0x2308d78d…` (SDTrading, SF@ATL) — **whale exit** (sold pre-resolution; gamma still shows it open).
- `ufc 0xc273d469…` (evanng, ronhum) + `ufc 0x640427f9…` (Kh4mz4t, garbal) — **resolved games** (positions
  redeemed/settled → gone from /positions). These are exactly the two markets the dry run scored WON.
- **The other 5 active opens (all 5 fed markets, wallet 0x71edffd0) are still genuinely-open → `touched`** (last-seen
  updated), correctly NOT transitioned. (touched=5 + vanished=3 = the 8 pre-existing active opens; captured=5 +
  touched=5 = in_category=10 — the loudness check passed with no silent drop.)

**(d) Before → after by status:** open **102 → 104**, pending **0 → 3**, closed **0 → 0**; total **102 → 107**.
(102 − 3 vanished + 5 new = 104 open; 3 pending; 107 total.)

**(e) Deactivated pairs polled/traded? ZERO — verified explicitly.**
trades in deactivated pairs **94 → 94** (unchanged) · new trades in deactivated pairs **0** · any deactivated trade
touched (`updated_ts=now`) **0** · roster rows polled this run **92**, of them in deactivated pairs **0**. **The gate
did its job.**

**(f) Errors / skips / rate-limiting:** errors **0**; no 403/429. `skipped_category` **90** (positions whose tier-1
category is not in the whale's pinned set — Ruling F, made visible). **cap_suspects: 6 whales returned EXACTLY 100
`/positions`** (SDTrading, 4751346, 0x71edffd0, MadeiraIsland, BetMechanic, kutsumiakia) — the un-paginated page-cap
tell (Ruling H). Those high-volume whales likely hold >100 open positions; the poller sees only the first 100, so
**captures for them are a lower bound**. Pre-existing shared-client limit, not a poller bug.

**(g) Tier-2 sanity — categorization behaved AS PREDICTED (better: ~0 tier-2 miss this run).** All 90 skipped
positions derived to `unknown`. Full classification of the 90: **85 futures/novelty**, **4 other-novelty**
(`prince-andrew-sentenced`, `bitcoin-all-time-high…`, `clarity-act-signed…`, `record-crypto-liquidation…`), and
**1** regex-flagged "game" that is actually **cycling-tournament-winner futures**
(`cycling-vuelta-a-espana-winner-tadej-pogacar-2026-08-21`) — a false positive. **Zero actual trackable game/match
lines were skipped.** The whales' entire off-tile open book is long-dated futures / politics / novelty (tennis/F1
*tournament* futures, regime/election markets, crypto) — consistent with the UNKNOWN_CONCENTRATION finding. The
6.3%/7d figure is an **upper bound** over resolved 7d activity; a single snapshot legitimately shows 0. **Not more
than expected fell to unknown.**

**(h) Runtime → cadence.** **2.94 s** for 14 wallets / 92 pairs (incl. 6 cap-hit fetches of 100 positions).
A `*/30` cadence (1800 s window) is trivially safe — ~0.16% duty cycle, 0 errors at this volume, large headroom.

## Post-verify
schema **9** · pm_watchlist **114/92/22** unchanged · pm_paper_category_stats **0 rows** (rollup NOT run) ·
/farm **200, 129578 B UNCHANGED** · PIDs **13102 / 676** unchanged. **Why /farm didn't move:** the pinned
scoreboard STATS read `pm_paper_category_stats` (still empty until rollup), so the substantive surface is unchanged;
the new `pm_paper_trade` rows only touch `n_open`/`last_polled_ts`, which rendered to the same byte length.

## STOP conditions — none triggered
No deactivated pair polled/traded (0) · poll count 92 not 114 · pm_watchlist unmoved · errors 0 (well under "a
handful"). **Clean run.**

## What this means for step 3
Only the **3 pending** rows exist to adjudicate. A live `paper-adjudicate` would (per the dry run) close the **2 ufc**
as WON (+11.39, +89.54) and leave the **1 mlb** pending (gamma still open, within 72h grace). Small, as expected —
the whales' trackable game-line open book is thin relative to their (untracked) futures book.

## Still unauthorized
Step 3 (live `paper-adjudicate` + `paper-rollup`) and step 4 (cadence install) remain unauthorized.

Runners: `cc\pm_rung3_poll_pre.sh` (+ `pre2`), `cc\pm_rung3_poll_run.sh`, `cc\pm_rung3_skipped_classify.sh`.
