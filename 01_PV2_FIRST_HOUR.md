# PRE-DEPLOY: WHAT THE ENGINE WILL TRADE IN ITS FIRST HOUR — rules, simulation, options

**2026-11-17 · READ-ONLY · branch `pm-polyv2-migration-2026-11-17` (build tip 24ee3992)**

Nothing was deployed, restarted, armed, disarmed, latch-cleared, pushed, or written to any DB.
Both DBs were opened `mode=ro` only (the legacy `trading_corp.db` solely through `arm.py`'s own
`mode=ro` readers). All Polymarket/Kalshi reads were throttled public GETs with a declared budget
(actuals in §2.6). The arm-gated evaluate path was **not** driven with the service environment —
the simulation drives the same functions with the public shim + `mode=ro` connections and no
`place_fn` (structurally cannot place). Simulator: `analysis/pv2fh_sim.py` (committed).

---

## 1 — THE RULES, FROM THE CODE

### 1.1 How an entry signal is born: **STATE, not diff**

A copy is triggered by a whale position **existing**, not appearing. Every ~7s cycle, per
category, per attached wallet, the driver fetches the whale's **full current `/positions` book**
and converts **every genuinely-open row** into an entry `CopySignal`
(`live_driver.py:1433-1444` → `positions_to_entry_signals`, `live_driver.py:611-629`).
"Genuinely open" = `curPrice` strictly in (0,1) AND not `redeemable` (`paper.py:103-116`).

The only thing preventing a re-copy is **gate 4 idempotency** against the **durable journal**
(`execution.py:735-738`): the client_order_id is a deterministic UUID5 over
(sub_division, wallet, ticker, leg, signal_id), where signal_id is a sha256 of
(wallet | condition_id | outcome_index | `"pos:<cid>:<oidx>"`) — **stable across restarts by
design** (`live_driver.py:603-608`, `execution.py:278-283`). The Journal's dedup set loads **every
dry_run=0 coid ever journaled, regardless of outcome status** (`execution.py:397-400`), and the
row is journaled **pre-POST** (`live_driver.py:1047`), so each signal gets **exactly one placement
attempt, ever** — a no_fill is never retried.

**After a restart or a feed outage the baseline is the journal, not a snapshot.** Every whale
position currently held whose coid was never journaled is treated as new. The 15 days of
positions the whales opened while v1 was dead were never journaled → **all of them are live
entry signals the moment the feed returns.** (Exits are the one diff-based path — `/positions`
reduction vs an **in-memory, boot-seeded-empty** snapshot confirmed by an `/activity` SELL —
so cycle 1 after restart emits **no** exits by construction; `live_driver.py:1262-1268,688-705`.)

### 1.2 ★ Any rule against copying an OLD whale position: **NONE EXISTS**

Searched: the signal builder (`live_driver.py:611-629` — filters only `is_genuinely_open` +
in-book dedup), the full chokepoint (`execution.py:619-825` — no gate reads any timestamp), the
`CopySignal` dataclass (`execution.py:92-107` — carries **neither the whale's entry time nor the
whale's entry price**), and every category matcher adapter (joins on date/teams/players only).
There is no maximum position age, no first-seen-after-boot baseline, no whale-entry-time check.
A price-drift limit against the whale's entry is **explicitly disclaimed in the code**:
`execution.py:18-20` — gate 2b "is the USD ceiling, **NOT a substitute for a
leg-ask-vs-Poly-price plausibility gate, which is a later refinement, not this rung**."
Reported per the brief's rule as **not found** after searching those four surfaces; the
disclaimer comment is affirmative evidence it was a known, deliberate deferral.

The only de-facto age prunes are survivorship: (a) the whale position must still be genuinely
open on Polymarket (a resolved market drops out via curPrice/redeemable); (b) the Kalshi market
must still be in the index — OPEN, or settled ≤2 days (`_SETTLED_LOOKBACK_SEC`,
`live_driver.py:89-106`) — **with a two-sided quoted book** (gate 3). Old positions on
still-unresolved markets copy at **today's ask**.

### 1.3 Market-state rules

| Rule | Exists? | Where |
|---|---|---|
| Refuse a game in progress | **NO** — Kalshi in-play markets stay OPEN and quoted; the matcher joins on date, not start time. (The 10-22 backtest measured **73% of historical copies were in-play**.) | matchers; backtest §T4 |
| Refuse a market near close | **NO** — nothing reads close_time on the entry path. | `execution.evaluate` |
| Refuse if price moved past X | **Only vs TODAY's book**: per-order USD cap (gate 2b `execution.py:730`), slippage clamp ±2¢ vs the current leg ask (gate 7, inside `build_v2_event_order`), two-sided + spread ≤ max(1, 2×slip) + top-of-book depth ≥ 0.75×notional (gate 3 `execution.py:714-729`). **Never vs the whale's entry price.** |
| Settled-on-Kalshi market | skip — no/one-sided quote → `skip:no_quote` / `skip:illiquid` (`execution.py:677-681,720-729`); settled >2d → not indexed → `skip:no_kalshi_strike`. |
| Opposing-whale market | skip + flatten — the opposed guard (`execution.py:1055-1108`, driven at `live_driver.py:1466-1515`); with a flat book it only skips the incoming entries of contested markets. |

### 1.4 The caps (defaults at `execution.py:64-88`; **configured values in §2.2**)

Gate order (a reject consumes no budget): 1 disarm (recorded) → 2a stake sanity → 3 match+liquidity
→ 2b per-order USD → 4 idempotency → 5 sub daily USD → 5b **account daily $150 (code constant)** →
6 account open-exposure (venue-rebased live) → 8 sub orders/day → 8b **account 50 orders/day (code
constant)** → 6b per-shard funding → 7 slippage clamp. Per-sub values (per-order cap, daily cap,
max_open, orders/day, sizing mode, contracts/stake) come from `pm_subdivision` read **per cycle**.
Two load-bearing behaviors: (i) hitting the per-sub orders/day ceiling **LATCHES that sub-division
disarmed** until a human `--clear-latch` (`live_driver.py:1033-1041`); (ii) the account aggregates
(5b/8b) only reject, never latch.

### 1.5 Order of evaluation in one cycle — who wins the caps

One asyncio task per account; its categories run **sequentially in proven-volume order** — 30-day
filled entry dollars DESC, alphabetical tiebreak, computed **once at task start**
(`live_driver.py:776-809`, applied :1233-1247). The documented, accepted consequence: when the
shared account cap binds, **late (low-volume/new) categories are starved systematically**
(`live_driver.py:783-787`). Within a category: wallets in attachment-table row order (unordered
SELECT, `live_driver.py:1433-1435` — rowid order, stable in practice), and within a wallet, the
v2 book's row order. Caps are claimed strictly first-come-first-served; the sequence is stable
cycle-to-cycle for the same inputs. One subtlety verified in code: a would-place that is then
**disarm-blocked still consumed in-cycle budget** (evaluate commits at would-place time,
`execution.py:820-821`, before the per-order arm re-read at `live_driver.py:1044`) — it
re-seeds away next cycle (counters reload from **filled** rows only), but within every single
cycle a latched category earlier in the claim order shadows the caps of armed ones after it.

---

## 2 — THE FIRST HOUR, SIMULATED FROM LIVE DATA

**Method.** `analysis/pv2fh_sim.py` on the box (read-only): the real roster/config/attachment/
30-day-volume reads (`mode=ro`), the real arm state (`arm.read_arm_verdict`), live v2 books via
the migrated client, live Kalshi catalogs via a public shim driving the **real ctx builders**,
then the **real** `positions_to_entry_signals` → opposing guard → `execution.evaluate` (all
gates) with the shared per-account Journal in the real claim order — iterated cycle-by-cycle to
a fixed point, cycle N's placements seeding cycle N+1's Journal exactly as the live filled-only
re-seed does. **Stated assumptions:** every placed IOC fills at its limit (so committed-$ is the
cap-bound ceiling; attempt count is a floor — a no_fill frees budget for the next signal in
line); no new whale activity in the hour; gate 6b (shard funding) not simulated (Kalshi
auto-rebalances ~10s; omitting it can only reduce orders); venue-exposure base $0 (book verified
flat). Kalshi quotes are a snapshot at run time, not at Jack's restart time.

### 2.1 ★ The headline: the "15-day backlog" is nearly all dead. First hour ≈ **3 orders, $18.14.**

| Account | First cycle | First 10 min | First hour (backlog) | $ committed | vs cash | Caps binding |
|---|---|---|---|---|---|---|
| kalshi_jack | **2 orders, $11.84** (itf $10.80 + cfb $1.04) | same | same | **$11.84** | 4.0% of $296.63 | **none** |
| kalshi_karen | **1 order, $6.30** (itf) | same | same | **$6.30** | 1.6% of $390.08 | **none** |
| kalshi_marc | 0 | 0 | 0 | $0 | — | all 22 cats latched `auth_failure` (10-28) — **confirmed places nothing** |
| kalshi_trey | 0 | 0 | 0 | $0 | — | all 22 cats latched `auth_failure` (10-22) — **confirmed places nothing** |

The simulation converged in ONE cycle (cycle 2 placed zero — gate-4 coids journal on the first
attempt, fill or no-fill). On the engine's clock: catalogs build at boot (~2-5 min), then all
three orders go out in the first trading cycle. **This corrects my build-report §6 prediction**
("jack+karen resume copying the 15-day backlog within the caps") — the direction was right, the
magnitude was wrong: the backlog survives the filters at ~0.4%, and no cap is ever approached.

**Why the backlog dies (the funnel, jack cycle 1 — 744 signals evaluated):** a whale's FULL book
re-signals under EVERY category it is attached to, and each category's matcher rejects what isn't
its market shape: `skip_non_soccer` 536, `skip_non_game` 122, `skip_non_f1` 42, `skip_non_ufc` 25,
`skip_market_type_excluded` 5, `skip_non_itf` 5, `out_of_window` 3, misc 4, `no_quote` 1 →
**would-place 2**. (karen: 513 evaluated → 1.) Upstream of all that, the heavier filter:
**a position on a finished Polymarket market has curPrice at 0/1 or redeemable=true and never
becomes a signal at all** (`paper.is_genuinely_open`) — the old half of the outage backlog is
already resolved and invisible. The opposed guard additionally skipped 17 contested bun markets
+ ~10 others per account (no closes — book flat). `disarm_blocked` = 0: the latched categories'
signals all died in the matchers, so no intra-cycle budget shadowing occurred.

### 2.2 The configured caps (actual `pm_subdivision` values — NOT the code defaults)

Jack raised the per-sub caps across the board: **per_order $50, daily $150, max_open $350,
50 orders/day, slip 2¢, liquidity_ratio 0.75** on essentially every armed sub — which makes the
**account-level code constants the real binding limits: $150/day and 50 orders/day per account**
(`execution.py:87-88`), with open exposure effectively capped at $350. **Exception: both `itf`
subs have NULL daily/open/count columns → they fall back to the CODE defaults $50/day, $100
open, 25 orders/day** (`CONFIG_DEFAULTS`, `execution.py:64-79`). Sizing is flat-contracts
everywhere (boxing/f1: fixed-$5): jack — itf **12**, nfl 10, lal 10, wta 10, mls 7, mlb/mex/bun/
bra/ucl/ufc/atp 5, wnba 3, cfb 2, cs2 1, epl 1; karen — mlb 8, itf **7**, lal 7, mls 7, most
others 5, cfb 3, cs2 3, atp 1, epl 1. Armed/latched state (arm rows, `mode=ro`): jack 18 armed +
8 disarmed (attached-and-latched: fl1, nba, sea, uel — stale 10-06 `boot_reconcile`), karen 16
armed + nba/uel latched; marc/trey 22+22 `auth_failure`. Claim order at next boot (real
30-day-volume ranking, re-computed by the sim): jack `nfl, itf, cs2, mls, cfb, ufc, atp, mlb,
mex, …`; karen `nfl, itf, cfb, mls, cs2, mlb, …`.

### 2.3 The three orders it would place (snapshot at 2026-11-17 ~15:15 UTC)

| Acct | Ticker / leg | Size | We pay | Whale paid | Drift | Whale position age |
|---|---|---|---|---|---|---|
| jack | `KXITFWMATCH-26NOV17COLSCH-COL` yes | 12 ct | $0.90 | $0.7809 | **+11.9¢** | **0.8 h** (`first_entry_at`) |
| karen | same market | 7 ct | $0.90 | $0.7809 | **+11.9¢** | 0.8 h |
| jack | `KXNCAAFTOTAL-26NOV21CSUFRES-66` yes (Over 65.5) | 2 ct | $0.52 | $0.4706 | +4.9¢ | 19.4 h |

Ages are exact — v2 serves **`first_entry_at`** (a true entry timestamp, found in `extra`;
equal to `last_event_at` for all three = single-entry positions). **Older than 24 h: 0. Older
than 7 days: 0.** Median drift +11.9¢ paid over the whale's entry, all three worse — the
no-price-drift-gate consequence (§1.2) and the backtest's negative copy-CLV, live. Game state:
the ITF order (both accounts) copies an **in-play women's ITF match running today** (the
backtest's one "promising" wallet `0x746295…`, within its ≤$50/signal capacity note); the CFB
total is a **future** Nov-21 game. Near Kalshi close (<1 h): 0. On settled/past-end markets: 0
(those died upstream as designed). Leg sanity: outcome "Martina Colmegna" → `-COL` yes leg —
visibly correct; the 65.5↔66 strike is the deployed exact-strike convention (2026-09-10 rebase).

### 2.4 What the first hour REALLY depends on: restart time, not the backlog

Signals are state (§1.1), so the first hour = this ~$18 residue **plus whatever whales open
during that hour**, copied at the steady-state rate. The simulation snapshot is a quiet Tuesday
mid-day (ITF the only live action; whales had not yet bet tonight's games). Historical reference:
~108 filled orders/day across the accounts pre-outage (backtest §Phase-1) — an evening restart
lands in soccer/NBA prime time and would see the first fresh copies within minutes-to-hours;
note **nba is boot-latched on BOTH jack and karen**, so tonight's NBA produces nothing until
those latches are cleared. The backlog does not grow back: it decays as whale positions resolve.

### 2.5 Method limits (stated)

Fills assumed 100% at the IOC limit (committed-$ is therefore the ceiling for this signal set;
attempts are a floor — a no-fill frees cap headroom nobody here needs). Quotes are a snapshot
(~25 min stale vs the sim; hours stale vs the actual restart — the SPECIFIC three orders will
differ at deploy time; the funnel shape and magnitude is the finding). Gate 6b (shard funding)
not simulated — can only reduce orders. Venue-exposure base $0 (flat book re-verified:
`open_positions_needing_manual_exit` = `[]`; cash snapshots fresh, `has_breakdown=1`). No
whale-exit signals cycle 1 by construction. Gate-4 baseline: 2,540 jack / 2,093 karen / 1,265
marc / 1,090 trey coids already journaled; 0 filled today.

### 2.6 Call budget — declared vs actual

Polymarket (box, data-api): **103 GETs, 41 wallets, every book complete, 0 errors** (budget 250).
Kalshi: **0 calls from the box** in the final run. Two earlier box probes (171 + 146 calls at
~4/s then ~1.4/s) each drew a 429 and were **stopped per the rules**; the instrument check
attributed them to the **shared-IP throttle with the live engine's own Kalshi reads** (the
HEARTBEAT_STALL_2026-09-12 class), so the catalog was harvested from the operator machine's IP
instead — **165 calls, 0×429, 80 series × open+settled(2d), 43 MB** — and served to the real ctx
builders from disk (`PV2FH_HARVEST`; 239 files served). The engine's own refresh cadence was not
competed with further.

### 2.7 Box/local command log (all read-only; scratch cleaned at close)

1. local: `tar -czf pv2fh.tgz trading_corp analysis/pv2fh_sim.py` → `ssh df -h /home` (8.4G free, pre-guard) → `scp` to box.
2. box: extract to `/home/azureuser/pv2fh_scratch/tree`; `sha256sum` client == TARGET `c88cfce6a16cc4a0`; venv import smoke + `mode=ro` DB probes (92 active subs / 153 attachments) + `arm.read_arm_verdict` probe.
3. box sim run #1 (`pv2fh_sim.py`): **aborted at Kalshi 429** (call 171) — stopped per rules.
4. box sim run #2 (0.65s throttle, 60s cool-off): **aborted at Kalshi 429** (call 146) — instrument re-checked → shared-IP cause; no further box Kalshi traffic.
5. local: curl harvest of 80 series (165 calls, 0×429) → `tar`+`scp` → box `kharvest/`.
6. box sim run #3: complete (output above; JSON: `pv2fh_report.json`, discarded with scratch).
7. box: targeted `first_entry_at` fetch for the two placed-source wallets (~16 data-api GETs).
8. cleanup: `rm -rf /home/azureuser/pv2fh_scratch /home/azureuser/pv2fh.tgz /home/azureuser/pv2fh_kharvest.tgz`; local harvest dir removed. Backups untouched.

---

## 3 — THE BACKTEST (2026-10-22 re-run): what it found about sizing

Report: `00_REPORT.md` on branch **`pm-backtest-2026-10-22`** @ 1b0cb28e (worktree
`cc-pm-backtest-2026-10-22-wt`; re-runs the 10-17 program on +6 days). Headline: **"Leak = the
FEE. Signal at fill ~ 0"** — GROSS **-$118.91**, FEES **$250.31 (67.8%)**, NET **-$369.22**, win
rate **50.1%**, per-event gross **-$0.03 (-0.43 SE = statistically zero)**; excess vs price+fee
**-0.040 (-2.90 SE)**. On size (T9): **conviction sizing is INVERTED** — smallest whale-bet
quartile **+$195** vs biggest **-$237** (corr -0.05), "every weighting worse" → **fade size**;
capacity caps are **across all four accounts** (ITF ≤$50/signal; no historical depth exists
anywhere). On copy delay/entry timing: **73% of copies are in-play**; pre-game copy CLV
**-1.73¢ (-7.21 SE)** while the whale's own CLV is ~flat (+0.2¢); **IOC adversely selects
hard — non-fills would have won 61.5% vs 50.0% for fills**; the one lever is **post-only/maker
on the zero-maker-fee `quadratic` series** (pre-game sim +$11.79 → +$15..$45, optimistic UB —
"fixes the FEE, not the edge"). T11: copying exits mildly positive (+$69.37, NFL-concentrated).

---

## 4 — OPTIONS FOR JACK (laid out, not recommended)

### A — Deploy with jack and karen armed (the staged §8 sequence as-is)
**Runs:** `pv2_graft.ps1` → `restart_tc.ps1` → `az … systemctl restart prediction-markets-web`
→ `pv2_postverify.ps1` → on green, push/FF/tag. **What happens:** the backlog residue goes out
in the first cycle — **~3 orders / ~$18 at today's snapshot (§2.1/§2.3)** — then the division
resumes **steady-state copying of new whale activity** (restart-time-dependent, §2.4), bounded
by $150/day + $350 open + 50 orders/day per account ($50/$100/25 on itf). **Cost/risk:** the
resumed flow is the trade class the 10-22 backtest measured as zero-edge-minus-fees, executed
stale (+5 to +12¢ over the whale's entry on today's three, §2.3; no price-drift gate exists,
§1.2); a lapsed attestation 403-latches the account on its first order (loud, flat book — renew
first); per-sub orders/day ceilings, if ever hit, **latch those subs** until `--clear-latch`
(§1.4) — not approachable at the observed flow.

### B — Global disarm before the restart; verify green; re-arm when ready
**Runs (all Jack's):** `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm
--global` **before** `restart_tc.ps1`; then the same graft/restart/postverify (postverify
expectations unchanged except placements = 0 and heartbeats IDLE); re-arm via pm_cli when he
chooses. **What happens:** v2 comes up, feeds and heartbeats go green, nothing trades (gate 1 +
the per-order re-read block everything; off is off — entries AND exits). **The backlog is still
waiting at re-arm** — signals are state-based (§1.1) and the baseline is the journal, so every
still-open whale position fires the moment arm returns. It **shrinks only by survivorship** as
whale positions resolve or the whales exit (§2.4's age data shows the decay rate). **Cost:**
$0 and no new information risk; the division stays frozen (it has been 15 days; the backtest
prices the freeze at ≈ saved fees).

### C — A "copy only positions first seen after go-live" rule (scoped, NOT built)
**Where it sits:** a persisted first-seen baseline, structurally like the existing
`pm_opposed_marker` suppress-set (migration 018 precedent): at task boot (or at a one-shot
`pm_cli` command), record every currently-visible (wallet, condition_id, outcome_index) entry
key into a new `pm_entry_baseline` table; filter in `positions_to_entry_signals` (or an extra
gate in `evaluate`) drops any signal whose key is in the baseline. `last_event_at` is **not** a
usable substitute (it moves when a whale adds to an old position). **Size:** a migration + ~100-200
LOC across `live_driver.py`/`execution.py` + tests; touches the order path, so it needs its own
review, **its own deploy and its own engine restart** — it cannot ride this deploy without
rebuilding/retesting the staged artifact. Interim equivalent with zero code: **Option B**, since
disarm holds the backlog off while the rule is built (the backlog itself persists either way).

---

## Compliance / disclosure
Read-only SSH throughout (ratified standing practice): sqlite `mode=ro` URIs, `ls`, `sha256sum`,
file reads; the simulator holds no broker, no `place_fn`, and never calls a write path
(`mark_opposed_contested`/`_record_order`/`dry_run_subdivision` unreached). No credential read,
printed, or written; no authenticated venue endpoint touched. Public-read budget + actuals in
§2.6; the run stops on any 429. Box scratch `/home/azureuser/pv2fh_scratch` + `~/pv2fh.tgz`
removed at close; backups untouched. Box command log: §2.7.
