# THE MAXIMUM-CHASE RULE (gate C) — built, tested, staged. 2026-11-17.

**Task:** before placing a copy, compare what we would pay against what the whale paid for the same claim;
if we would be chasing too far, skip the copy. Built on `pm-polyv2-migration-2026-11-17` as separate commits
after the v2 work, so Jack can deploy **v2 alone** or **v2 + chase** in one restart.

**Status at delivery:** COMMITTED on the branch (shas in §5), NOT pushed, NOT deployed. v2 was verified NOT
yet deployed at build start (box client sha `ad9a3192…` == v1 BASE), so no rebase was needed. Deploy, restart,
arm, disarm, push, FF and tag remain Jack's.

**The one-line rule as shipped:**

```
chase = (our leg ask + per-contract taker fee) − whale_avg_price          [same claim, same scale]
ENTRY skipped when chase > max_chase_cents            (absolute form; default 2.0¢, holdout-confirmed)
              or when chase > chase_upside_pct% × (1 − whale price)       (relative form; default OFF — holdout-INVERTED)
missing whale price → skip, loudly (skip:chase_unpriced).  Exits are NEVER chase-blocked.
```

---

## §1 What was built (file:line)

### 1.1 The whale's price and time ride the signal
- `CopySignal` gains `whale_avg_price` + `whale_first_entry_ts`, defaulted None so every existing
  construction (exits, opposed closes, tests) is byte-identical (`execution.py`, CopySignal tail).
- `live_driver.positions_to_entry_signals` populates both from the SAME v2 `/positions` row that produced the
  signal: `PositionRow.avg_price` (first-class v2 field) via `_whale_avg_price` — anything outside (0,1)
  becomes None, because the client coerces a MISSING value to 0.0 and 0.0 is never a real entry price — and
  the raw v2 `first_entry_at` riding in `extra` via `_whale_first_entry_ts`.
- **Held-token basis, proven from a real payload's own arithmetic** (`test_chase_gate.py::
  test_avg_price_is_held_token_basis`): a captured CLOSED row where the whale held the index-1 "No" token and
  WON prints `percent_realized_pnl = 192.6663` ≈ `(1 − avg_price)/avg_price` — the identity only holds if
  `avg_price` prices the HELD token (yes-side pricing would print ~51.9%, 140+ points away).

### 1.2 ★ Same claim, same scale — where the conversion actually lives
The whale's price is for its held Polymarket outcome token; ours is for the Kalshi (ticker, leg) the matcher
chose. Two facts make `chase = leg_ask + fee − avg_price` valid with **no further transform**:
1. **Every matcher is claim-faithful**: the whale's held-token claim binds a (ticker, leg) carrying the SAME
   claim, or safe-misses. The existing swap-regression suites prove the "or safe-misses" half (a missing
   own-side ticker is never bound to the opponent); the new per-family tests prove the binding half.
2. **The complement handling is on OUR side**: a no-leg order is priced at the NO ask (`execution._leg_ask`
   returns `no_ask_dollars` for a no leg), which is already the cost of the whale's claim. The naive bug —
   comparing the whale's price to the YES-side `Decision.price` for a no-leg order — is exactly what the
   complement tests pin against.

**Proven per matcher family through the REAL `execution.evaluate()`** (tests in
`tests/prediction_markets/test_chase_gate.py`; per the independence rule every expected (ticker, leg) comes
from the Kalshi ticker's own -CODE/title/yes_sub_title and every expected chase number is a hand-computed
literal — never read back from the code under test): MLB moneyline (2-way) · soccer 3-way with tie
(**win-No complement leg**) · NFL spread · NFL total (**Under complement leg**) · ITF (the live replay, §4.2)
· CS2 · UFC · boxing · F1 (**No-token complement leg**) · RFI (**No complement leg**). Each family asserts a
blocked case AND a kept case at the hand-computed boundary, with the reason string carrying ask/fee/whale to
4 dp.

### 1.3 Gate C in the chokepoint
- `execution.evaluate`: a new block after gate 6b, immediately before body computation — i.e. **after the
  slippage gate's fail-closed precondition** (`skip:no_quote` already validated `base_price`) **and after
  every budget gate**, so a chase skip consumes NO budget, journals NO coid, and never latches; the signal
  re-evaluates every ~7s cycle and places if the price comes back inside the threshold. ENTRY-only: capping
  a risk-reducing exit strands it (the Cubs failure) — exits are exempt like gates 2b/3/5/6/8.
- **Price basis = the leg ASK (+ taker fee), not the slippage limit.** The IOC fills at the resting ask; the
  +2¢ limit is a mechanical cushion consumed only when walking a thin book. Measured on all 5,083 historical
  entries: median (limit − fill) = exactly the 2¢ slip allowance, i.e. fills sit at the ask — so the gate's
  ask basis is calibrated 1:1 with §3's fill-price sweep. (Worst case a thin-book walk adds ≤ the configured
  slip beyond the gate's measure; stated, not modelled.)
- **Fee model**: `kalshi_taker_fee_per_contract` = `mult × 0.07 × P × (1−P)`, `mult` 0.50 for
  KXMLBGAME/KXMLBSPREAD/KXMLBTOTAL and 1.00 otherwise (Kalshi's own fee schedule, harvested 2026-10-17;
  re-verified §3.2 against actual fill fees). Unknown series read 1.00 — the conservative-HIGH direction
  (overstates chase → blocks more → fail-closed).
- **★ Missing whale price → `skip:chase_unpriced`** — fail closed, never silently: counted in the heartbeat
  chase counter and surfaced by the rate-limited driver log. (Expected ~never: v2 serves `avg_price` on every
  row — §3.1 found a usable price for 3,681/3,681 historical copies — so a burst of these = a client
  regression, and the postverify greps for exactly that.)

### 1.4 Config — per sub-division, code default, no restart
- `CONFIG_DEFAULTS["max_chase_cents"] = 2.0` (ON) and `CONFIG_DEFAULTS["chase_upside_pct"] = 0.0` (OFF) —
  the §3 recommendation. Per-sub overrides: `pm_subdivision.max_chase_cents` / `chase_upside_pct`
  (migration 025). NULL → code default (the standing DDL-NULL rule); an explicit **value ≤ 0 disables that
  form** for the sub (NULL can't mean "off" because it means "default"). Read PER CYCLE — change the number,
  no restart. Both forms active together = skip if EITHER trips.
- **Why config-in-code alone wasn't enough (the migration-025 justification):** (a) per-sub overrides need
  real columns — `pm_subdivision` has no extra/JSON bag; (b) the skip counter must be visible per
  (account, category) and `pm_driver_heartbeat` needs the column. Migration 025 is additive-only
  (3 ALTERs), numbered next-contiguous after 24, carries the ruled **renumber-on-collision deploy gate**
  (drift-check box head == 24 at apply; a same-number collision silently skips the loser's DDL), and
  self-applies via `db.init_db` from the pm_cli crons. The code is tolerant BOTH ways: pre-migration it runs
  on code defaults (`_row_get` → None for a missing column) and the heartbeat write falls back to the pre-025
  statement on `OperationalError` — never dark, never crashed, in either deploy order.

### 1.5 ⚠️ The silent-reject trap, closed
A pre-submit skip writes NO order row, so a sub quietly chase-skipping looks identical to one with no signal.
Two traces (both shown working in `test_chase_gate.py::test_cycle_counts_chase_skips_and_rate_limits_the_log`
and `::test_heartbeat_persists_chase_skips_and_tolerates_pre_025`):
1. **Per-cycle counter in the existing heartbeat**: `run_live_arm_gated_cycle` counts `skip:chase` +
   `skip:chase_unpriced` into the cycle summary; `heartbeat.upsert_evaluated` persists it to
   `pm_driver_heartbeat.chase_skips` every evaluated beat (NULL = pre-migration write, 0 = evaluated with no
   skips — distinguishable by design).
2. **Rate-limited log line** (1 per 60s per (account, category)): ticker, leg, count, and the Decision reason
   carrying whale price, our ask, fee and the tripped threshold to 4 dp —
   `pm_live_driver: gate-C CHASE skip kalshi_jack/itf (1 this cycle so far) -- KXITF… (yes x12):
   chase_0.1065=ask_0.8800+fee_0.0074-whale_0.7809 gt_abs_cap_2.0c …`

---

## §2 — (section number parity note)

Section numbering below follows the task's: §3 = threshold-from-data, §4 = verify, §5 = deliver.

## §3 ★ The threshold chosen from data, not intuition

### 3.1 Dataset + v2 recovery
The backtest's own per-fill dataset (`pm-backtest-2026-10-22` scratch, built by its `enrich.py` from
`pm_subdivision_order` dry_run=0 entries + Kalshi candle cache): **5,083 entry attempts, 3,681 filled**,
2026-08-30 → 10-22, with per-fill outcome to settlement (`gross_cf`/`net_cf`), actual fees, order-time book,
leg-corrected close mid (CLV source) and the in-play flag.

Whale entry prices:
- The backtest's own source (`pm_paper_trade.entry_price_avg_at_observation` — the whale's average at our
  FIRST observation) covered **3,434 / 3,681** filled copies. *(The task's "v1 matched only 258 of 3,067"
  figure was not reproduced against this dataset — observation-join price coverage was already 93%; the
  ~258-scale gap in the backtest was the whale entry **timestamp/tx** join, which v2's `first_entry_at` now
  fills outright.)*
- **v2 recovery** (248 throttled GETs from this machine's IP, `condition=`-targeted, OPEN+REDEEMABLE and
  CLOSED passes, 0×429): avg_price for **all 46 wallets × 1,931 (wallet, condition) pairs → 2,221 rows**,
  closing the gap: **3,681 / 3,681 copies priced (100%)** — 3,434 cross-checkable + **247 newly priced**,
  and `first_entry_at` present for **3,681 / 3,681**.
- Cross-check (both sources): median |obs − v2| = **0.0003** (58.4% within 0.5¢, p90 1.25¢) — the tail is
  whales adding AFTER our observation (v2 serves the final average). Where both exist the observation-time
  price is used (the entry-time truth); v2 fills the 247.

### 3.2 Instrument checks (before believing the numbers)
- **Fee formula**: `ceil-to-cent(mult × 0.07 × ct × p × (1−p))` matches the ACTUAL journal fee on
  **3,472 / 3,681** fills (±0.5¢/contract). The 209 mismatches are early (pre-October) KXMLBGAME fills whose
  actual fee is the FULL 0.07 rate — consistent with Kalshi moving MLB to 0.5× between those fills and the
  2026-10-17 schedule harvest. The code map carries the CURRENT schedule; the sweep uses each fill's ACTUAL
  fee, so threshold selection is unaffected.
- **Limit vs fill**: median (leg-correct limit − fill) = **0.0200** — the full slip allowance, i.e. fills sit
  at the ask. The gate's ask basis therefore reads the same number the sweep reads.
- The holdout era's ugly aggregate (−$274 net, 41% win on 757 fills) is consistent with the backtest's own
  verdict (NET −$369 overall), not an instrument artifact.

### 3.3 The sweep — blocked vs kept, train → holdout
Fill basis: `chase = fill + fee/ct − whale`. Hold-out cut **2026-10-16** (the backtest's own M4 cut):
train = 2,924 priced fills, holdout = 757. "ev" = unique Polymarket markets (condition_id). CLV = leg-correct
close-mid − fill, **pre-game fills only** (in-play CLV is undefined — the close precedes entry; the
backtest's own caveat), so its n is small. net/ct = net dollars per contract. Full grids (abs 0/2/4/6/8/10¢;
rel 25/50/75%) ran; the decision rows:

**TRAIN (< 2026-10-16)**
| rule | group | n | ev | ct | gross | fees | net | win | CLV(n) | net/ct |
|---|---|---|---|---|---|---|---|---|---|---|
| — | ALL | 2924 | 1175 | 12950 | −12.82 | 171.87 | −184.68 | 51.8% | −0.77¢ (776) | −0.0143 |
| abs>0¢ | blocked | 2475 | 1038 | 10812 | −171.71 | 144.63 | −316.34 | 50.7% | −0.78¢ (760) | −0.0293 |
| | kept | 449 | 220 | 2138 | +158.89 | 27.24 | +131.66 | 58.0% | +0.06¢ (16) | **+0.0616** |
| **abs>2¢** | blocked | 1049 | 502 | 4300 | −126.12 | 61.76 | −187.87 | 51.3% | −1.19¢ (207) | **−0.0437** |
| | kept | 1875 | 795 | 8650 | +113.30 | 110.11 | +3.19 | 52.1% | −0.61¢ (569) | **+0.0004** |
| abs>4¢ | blocked | 265 | 155 | 1108 | −77.70 | 15.46 | −93.17 | 51.2% | −0.29¢ (19) | −0.0841 |
| | kept | 2659 | 1083 | 11842 | +64.88 | 156.40 | −91.52 | 51.9% | −0.78¢ (757) | −0.0077 |
| abs>6¢ | blocked | 136 | 83 | 508 | −44.50 | 7.11 | −51.61 | 50.0% | −0.33¢ (6) | −0.1016 |
| abs>8¢ | blocked | 104 | 63 | 386 | −63.18 | 5.37 | −68.56 | 43.3% | — | −0.1776 |
| abs>10¢ | blocked | 85 | 51 | 326 | −59.03 | 4.62 | −63.65 | 41.2% | — | −0.1953 |
| rel>25% | blocked | 107 | 70 | 484 | −49.31 | 4.83 | −54.15 | 66.0% | — | −0.1119 |
| rel>50% | blocked | 20 | 17 | 91 | −6.98 | 0.79 | −7.77 | 75.0% | — | −0.0854 |
| rel>75% | blocked | 6 | 6 | 19 | +1.00 | 0.07 | +0.93 | 100.0% | — | +0.0492 |

**HOLDOUT (≥ 2026-10-16)**
| rule | group | n | ev | ct | gross | fees | net | win | net/ct |
|---|---|---|---|---|---|---|---|---|---|
| — | ALL | 757 | 308 | 3422 | −233.33 | 40.96 | −274.29 | 41.1% | −0.0802 |
| abs>0¢ | blocked | 603 | 262 | 2610 | −144.42 | 29.77 | −174.19 | 43.8% | −0.0667 |
| | kept | 154 | 71 | 812 | −88.91 | 11.19 | −100.10 | 32.7% | **−0.1233 (INVERTED)** |
| **abs>2¢** | blocked | 346 | 159 | 1478 | −165.85 | 16.75 | −182.60 | 39.9% | **−0.1235** |
| | kept | 411 | 180 | 1944 | −67.48 | 24.21 | −91.69 | 41.9% | **−0.0472 (confirmed)** |
| abs>4¢ | blocked | 116 | 57 | 567 | −50.11 | 6.42 | −56.53 | 45.4% | −0.0997 (weak confirm) |
| abs>6¢ | blocked | 67 | 32 | 371 | −15.82 | 4.08 | −19.90 | 57.1% | −0.0536 **(INVERTED)** |
| abs>8¢ | blocked | 50 | 25 | 268 | −13.32 | 2.97 | −16.29 | 63.8% | −0.0608 (inverted) |
| abs>10¢ | blocked | 37 | 22 | 194 | −5.89 | 2.29 | −8.18 | 72.2% | −0.0422 (inverted) |
| rel>25% | blocked | 37 | 22 | 210 | −3.44 | 1.99 | −5.43 | 83.3% | −0.0258 **(INVERTED)** |
| rel>50% | blocked | 15 | 10 | 102 | −6.43 | 0.62 | −7.05 | 80.0% | −0.0692 (inverted) |
| rel>75% | blocked | 4 | 4 | 24 | +1.06 | 0.07 | +0.99 | 100.0% | +0.0413 (inverted) |

*(Holdout "inverted" = the blocked copies were NOT worse than the kept ones — kept net/ct ≤ blocked net/ct
and/or blocked win% well above kept.)*

### 3.4 The verdict, stated plainly
- **abs > 2¢ is the ONE threshold that survives the hold-out as a signal filter.** Blocked worse than kept in
  BOTH eras: train −4.37¢/ct vs +0.04¢/ct (spread +4.4¢/ct); holdout −12.35¢/ct vs −4.72¢/ct (spread
  +7.6¢/ct). It blocks ~36% of fills and saves **38.5% of all fees** ($86.63 of $225.15 on this dataset).
- **abs > 0¢** looks spectacular in train (+6.2¢/ct kept, the only positive-net kept book) but the holdout
  **inverts** (kept −12.3¢/ct, worse than blocked) — a chance/regime result; reported, not recommended.
- **abs ≥ 6¢ and EVERY relative threshold (25/50/75%) fail the hold-out**: the blocked copies were NOT worse
  — at rel>25% the blocked holdout copies won 83%. On this data those gates would only reduce volume, which
  still saves fees, **but they are not signal filters** — said plainly, per the task.
- **Per category at >2¢** (both eras merged; blocked-vs-kept net/ct): the filter is REAL for **itf**
  (−0.060 vs **+0.037**; kept ITF = +$90.15 net — and it holds in BOTH eras: train −0.040/+0.048, holdout
  −0.095/+0.005), **nfl** (−0.099 vs −0.027), **mls** (−0.097 vs +0.006), **mex** (−0.66 vs +0.01), **cs2**
  (−0.041 vs −0.002), roughly neutral for **mlb**, and **INVERTED for cfb** (+0.015 blocked vs −0.049 kept,
  n=383/311 — cfb's chased copies made money), **ufc** (+0.122 vs −0.115) and **wnba** (+0.132 vs −0.198).
  The per-sub columns exist precisely so Jack can loosen/disable per category (e.g. `UPDATE pm_subdivision
  SET max_chase_cents=-1 WHERE category='cfb'`) — surfaced, not recommended.
- **Where the edge lives**: the separation is concentrated **in-play** (blocked −7.7¢/ct vs kept −1.3¢/ct;
  pre-game only −1.6 vs +0.4) — chasing an in-play price means buying the move with the whale's edge gone.
- **Unpriced copies**: zero — every filled copy had a recoverable whale price, so the fail-closed path costs
  nothing on this dataset.

### 3.5 Recommended default (Jack chooses the deployed value)
**`max_chase_cents = 2.0` (ON) + `chase_upside_pct = 0` (OFF)** — exactly what CONFIG_DEFAULTS ships. The
relative form is implemented and configurable per sub, but the data does not support enabling it (holdout-
inverted at every tested value), so it ships disabled. Both knobs are per-sub columns read every cycle; no
restart to change. Limits of this analysis: one 8-week sample, the holdout is one (bad) week, CLV covers only
the 27% pre-game slice, and whale prices for 247 copies are final-average (not observation-time) — biases
stated, none corrected upward.

---

## §4 Verification

### 4.1 Box-scratch A/B pytest (BASE = v2 tip dbbc24bf, TARGET = v2 + chase)
Harness: `analysis/pvc_stage.ps1` + `pvc_run1.sh` — box-scratch (`/home/azureuser/pvc_scratch`), the live
tree only READ (BASE copies sha-gated == dbbc24bf == the box), venv python. Run A = the v2 tip (live-tree
chase files + the pre-chase versions of the five edited test files; chase suite ignored). Run B = the
v2+chase TARGET. Suite = `tests/prediction_markets` + the six polymarket test files
(`test_backtest_polymarket_arbitrage` excluded — its scripts/ file is absent on the prod-live lineage, the
pv2 precedent).

**Result (2026-11-17 18:36Z): A = 34 failed, B = 34 failed; B-only regressions: NONE; A-only: none;
`test_chase_gate.py`: ALL PASSED in run B.** The 34 are the identical known-stale set recorded in the v2
build report. Plain-addopts recount of run B: **815 passed / 34 failed** — exactly the v2 run's 792 passed
plus the 23 new chase tests. (The A/B executed on the pre-`bddf049f` content; that later commit changes two
comment literals and zero code paths — diff-provable; the box sim tree was re-synced to the corrected file,
sha `b4f75cc64793c3fb`, before §4.2 ran.)

### 4.2 The first-hour simulator, re-run with the gate ON
Same instrument as the pre-deploy first-hour report (`analysis/pv2fh_sim.py` — real
`positions_to_entry_signals` → opposing-guard → `evaluate()` with the shared Journal, claim order from the
real `category_volume_order`, live Polymarket books, Kalshi catalog harvested from THIS machine's IP and
served to the real ctx builders from disk, both DBs mode=ro), now driving the **v2+chase tree**
(execution `b4f75cc64793c3fb`) with the shipped defaults (2.0¢ abs / relative off). Run 2026-11-17 ~18:5xZ
(evening snapshot, ~6h after the morning run; whale books have moved — the morning's ITF match has since
RESOLVED and no longer signals).

**Result: first restart hour = 0 orders, $0.00 — converges in one cycle.** The two signals that passed every
other gate were both CHASE-BLOCKED (jack/cfb; marc+trey all-latched and karen's survivors resolved, as
before):

| ticker | leg×ct | whale avg | whale entered | our ask+fee | chase | verdict |
|---|---|---|---|---|---|---|
| KXNCAAFTOTAL-26NOV21CSUFRES-66 | yes×2 | 0.4706 | ~22h ago | 0.5000+0.0175 | **+4.69¢** | blocked (>2¢) — **the SAME CFB copy the morning gate-off run placed at $1.04** |
| KXNCAAFSPREAD-26NOV17EMUWMU-WMU14 | no×2 | 0.4200 | **36 min ago** | 0.4400+0.0172 | **+3.72¢** | blocked (>2¢) — a FRESH whale entry whose price already ran past it |

Reasons verbatim from the gate (`chase_0.0469=ask_0.5000+fee_0.0175-whale_0.4706 gt_abs_cap_2.0c`, and
`chase_0.0372=ask_0.4400+fee_0.0172-whale_0.4200 gt_abs_cap_2.0c`) — the second also demonstrates the
complement-leg path live (a `no`-leg copy priced at the NO ask against the whale's held-token price) and the
new `whale_first_entry_ts` carry (exact ages, no proxy). So at this snapshot **gate C suppresses the entire
restart-backlog residue** (morning gate-off run: 3 orders / $18.14; evening book without the gate would have
placed its 2 survivors; with it: none, and nothing cap- or budget-consuming happened). Both blocked signals
re-evaluate every cycle and will place only if their price comes back within 2¢ of the whale's.

The deterministic replay is also pinned as a unit test
(`test_chase_gate.py::test_chase_itf_live_first_hour_replay`): ask 0.88 + fee 0.0074 − whale 0.7809 =
**chase +10.65¢ → blocked** at the 2¢ default (and at any swept threshold ≤ 10¢) — the first-hour report's
$0.90/$0.78 ITF copy cannot happen with gate C armed.

---

## §5 Deliver — diff, shas, commits, runners, Jack's commands

### 5.1 Commits (this branch, after the v2 commits; NOT pushed)
On `pm-polyv2-migration-2026-11-17`, after the v2 sequence (`00a205c4` → `75dbd048` → `24ee3992` →
`dbbc24bf`):
- **`5f8aad50`** — the chase rule: gate C + signal carry + migration 025 + heartbeat counter + rate-limited
  log + the 23-test chase suite + the five test updates (10 files, +779/−27).
- **`bddf049f`** — comment-only figure correction (fee check 3,472 not 3,672; zero code paths).
- **commit 3** — this report + the pvc runners + the sim extension (committed immediately after these tables
  were filled; its sha is quoted in the delivery message and visible at the branch tip).

### 5.2 Engine files changed (BASE = box live 2026-11-17 == dbbc24bf; CR-stripped sha256-16)
| file | BASE | TARGET |
|---|---|---|
| trading_corp/prediction_markets/execution.py | a8a03b59e422a8a2 | b4f75cc64793c3fb |
| trading_corp/prediction_markets/live_driver.py | 4bbe3023610aaadc | 69dc1040abe53bc9 |
| trading_corp/prediction_markets/heartbeat.py | 57afdcc6c61055e2 | 227eda723301c288 |
| trading_corp/prediction_markets/db.py | 49f4f6d8d53d8fc5 | a4d8ddfd2254830a |
| trading_corp/data/polymarket_data_api_client.py | ad9a31922becfbe1 (v1) | c88cfce6a16cc4a0 (v2, unchanged by chase) |
| trading_corp/prediction_markets/loss_grounding.py | ce22b4d26de6faf4 | 42d166575b2f39bd (v2, unchanged by chase) |

Tests: `tests/prediction_markets/test_chase_gate.py` (new, 24 tests);
`test_itf_match.py` / `test_boxing_match.py` / `test_f1_match.py` (e2e signals now carry a whale price);
`test_attachment_span_history.py` / `test_rung3_observability.py` (schema-head pins 24 → 25, the stated
bump-per-migration convention).

### 5.3 The updated runners (authored, NOT run — deploy is Jack's)
- **`analysis/pvc_graft.ps1` + `pvc_graft.sh`** — ONE graft, two modes:
  `-Mode v2` (2 files, byte-identical set to the old pv2_graft) or `-Mode v2chase` (6 files). Same discipline
  as the v2 graft: df pre-guard → stage gate (staged == TARGET) → drift gate (live == BASE) → backup (kept)
  → apply → verify == TARGET → py_compile + import closure (venv; also prints schema head + chase defaults)
  → ROLLBACK-ALL on any failure. Supersedes `pv2_graft.ps1` (which remains valid for v2-alone).
- **`analysis/pvc_postverify.ps1` + `pvc_postverify.sh`** — the v2 postverify plus section [8] in
  `-Mode v2chase`: migration 025 present (schema head 25; 24 = the pm_cli cron hasn't self-applied yet —
  benign, code runs on defaults), the chase columns listed, and **the chase_skips counter PRESENT and
  MOVING** (two heartbeat samples 20s apart: updated_ts advancing, chase_skips NOT NULL on evaluated rows),
  plus the gate-C log-line count and a chase_unpriced regression grep.
- **`analysis/pvc_stage.ps1` + `pvc_run1.sh`** — the A/B pytest harness (box-scratch; already run, §4.1).
- **`analysis/pv2fh_sim.py`** — extended: carries `first_entry_at` (exact ages), reports `chase_blocked`
  detail (ticker/leg/whale/reason) per blocked signal.

### 5.4 Exact commands for Jack (in order; I ran NONE of them)
1. **Choose the mode** — `v2` (migration only) or `v2chase` (migration + gate C at 2.0¢ abs / relative off).
2. Deploy: `powershell -ep bypass -f "C:\Users\AA Incorporado\cc-pm-polyv2-wt\analysis\pvc_graft.ps1" -Mode v2chase`
   *(or `-Mode v2`; the old `cc\pv2_graft.ps1` path from the v2 report §8 is superseded by this worktree path)*
3. Restart engine: `powershell -ep bypass -f "C:\Users\AA Incorporado\Desktop\restart_tc.ps1"`
4. Restart pm_web: `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "systemctl restart prediction-markets-web"`
5. Verify: `powershell -ep bypass -f "C:\Users\AA Incorporado\cc-pm-polyv2-wt\analysis\pvc_postverify.ps1" -Mode v2chase`
6. After a green verify — FF `prod-live` + tag + push (from the repo):
   - `git push origin pm-polyv2-migration-2026-11-17`
   - `git push origin pm-polyv2-migration-2026-11-17:prod-live`
   - `git tag pvc-deploy-2026-11-17 <branch tip>` , `git push origin pvc-deploy-2026-11-17`
7. Optional per-sub tuning (no restart; NULL = default 2.0¢/off, ≤0 = that form off):
   `sqlite3 /home/azureuser/trading_corp/data/prediction_markets.db "UPDATE pm_subdivision SET max_chase_cents=4.0 WHERE account_id='kalshi_jack' AND category='cfb';"`
   *(a WRITE — yours by definition)*
8. Rollback at any point: the graft prints its backup dir (`/home/azureuser/backups/pvc_<mode>_<ts>`); on
   failure it already rolled back and verified == BASE.

**What changes for money at the 2.0¢ default, from §3 + §4.2:** roughly a third of historical copy volume
would not have been placed (the worse-performing third in both eras, −$187.87 train / −$182.60 holdout net
removed, 38.5% of fees saved); the residue backlog at restart shrinks further below the ~$18 of the
first-hour report (§4.2 shows exactly which of today's would-places the gate removes); a persistent chase
condition shows up in `pm_driver_heartbeat.chase_skips` and one WARNING per minute per sub, never silently.

---

## Compliance + session log

Read-only throughout: no prod DB written, no legacy `trading_corp.db` written (arm reads via arm.py's own
mode=ro readers), no credential printed/logged/written, no arm/disarm/latch change, nothing pushed, no
deploy/restart — the live tree, engine, services and every DB untouched; backups untouched. Box work was
box-scratch only (`/home/azureuser/pvc_scratch` + tarballs, cleaned at close; A/B pytest + the ctx-builder
series recorder + the first-hour sim re-run all ran there against venv python). The evaluate path was driven
ONLY with synthetic/test DBs or mode=ro connections and `shard_balances=None` / `venue_exposure=None` (the
documented test opt-outs); never with the service environment. Call budgets (declared → actual): Polymarket
data-api — probes 8; §3 recovery 400 → **248** kept calls (plus ~248 spent by a first run that completed its
fetch loop and then crashed in its SAVE step — a string-join bug; results lost, run repeated — honest total
~504 across both); sim re-run 250 → **103** from the box (41 wallets, all books complete; the data-api path
is not the IP-shared-throttled one). 0×429 on every Polymarket call. Kalshi — **0 box calls**; local-IP
harvest 400 → **173** (168 series/status pairs, 48MB, 0×429, stop-on-429 armed); the ctx-builder series list
was RECORDED on the box with a zero-network stub (no Kalshi call), and the sim served 242 harvest files in
place of live Kalshi reads. Local Python: stdlib-only analysis scripts on scratch data +
one `py_compile` syntax pass; `procgov` (the run_capped 25GB wrapper) is ABSENT on this machine, so that
compile-only pass ran unwrapped and ALL pytest ran on the box, none locally. The §3 sweep scripts (coverage,
v2 recovery fetch, threshold sweep, per-category pass) + the Kalshi series recorder/harvester are committed
under `analysis/chase_sweep/` for re-runnability (the sweep inputs: the backtest worktree's
`scratch/entries_enriched.csv` + `scratch/prediction_markets.db`, both left untouched); their outputs are
quoted in full above.

MACE, PMCC, PEAD and bitunix untouched. Fire-first stayed armed and had nothing to fire on (no fills exist).
