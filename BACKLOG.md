# Trading Corp — Open Backlog

Durable list of open work. Each section ends with a recommended phase /
priority. Items get pulled into the active session when their phase comes up.

**Active session work lives in chat — not duplicated here.**

**Completed work moves to `runbooks/deploy_log.md` + memory entries — NOT
preserved here.** This file tracks open items only. The full historical
backlog (with EOS snapshots + completed entries) is archived separately.

**Last grooming pass: 2026-06-02 evening — pre-grooming this file was 8,881
lines; post-grooming organized around three operator priorities + open items.**

---

# Priority 1 — Bitunix Futures path to live trading

Phase 3 (live exit path infrastructure) DEPLOYED to prod 2026-06-02 ~01:40 UTC.
Paper-mode observation window 2026-06-02 → 2026-06-09. After window, operator
decides whether to flip `config/strategies.yaml:1022 execution_mode: paper → live`.
No formal checklist — operator judgment based on observed paper performance,
audit log review, and any unexpected behavior in the new code paths.

When the flip happens, the dashboard begins filtering by flip-date — paper-mode
trades persist in the DB but are no longer rendered in the live-mode view.
Queries against historical paper data remain available via Claude.

## P1 — D1/D2 account-drawdown auto-flatten fix: **MERGED + DEPLOYED + LOADED** (deployed 2026-06-13; filed 2026-06-11)

The 15% account-drawdown auto-flatten was a placeholder that never fired
(`peak_equity=current` ⇒ `drawdown_pct()=0`; and the score path never dispatched
the flatten — D1/D2). **FIX BUILT + TESTED on branch
`bitunix-d1-drawdown-flatten-fix-2026-06-11` (4d3a97c, pushed; MERGED to main as `76f3bb8`):**
persisted account high-water-mark in `agent_state` (restart-safe, fail-safe to
current on read error), both call sites fed the tracked peak, score-path flatten
dispatch added. Tests prove a forced 15% drawdown flattens on BOTH paths;
regression-proof + branch-vs-pristine-base no-regression gate (zero new). Report
`reports/2026-06-11_bitunix_d1_drawdown_flatten_fix.md`.

**STATUS: DEPLOYED + LOADED 2026-06-13** — merge `76f3bb8` (`--no-ff` of `5c3a294`, pushed);
single-file `bitunix_futures_observer.py` to prod (md5 `21830bf3…710b`, backup
`.bak-pre-d1-2026-06-11`) + restart 03:37 UTC (new PID 2608222, fresh venv-3.12 `.pyc`,
`execution_mode=paper`; peak self-initialized on first eval → no false flatten; the restart
also (re)loaded the B1 stop). **15% breaker now LIVE in paper** — see `runbooks/deploy_log.md`
2026-06-13 entry. **First prerequisite of the path-to-supervised-live sequence**
(deploy D1 ✅ → HITL removal ✅ **DEPLOYED-TO-DISK 2026-06-13** [[bitunix-hitl-removed-deployed-to-disk]]; B1 real-fill validation **PASSED on first real fill 2026-06-14** (was dropped/accepted-risk; now validated — see `runbooks/deploy_log.md` 2026-06-14 entry) → **NEXT: item-4 non-interactive `--live` go-live**, whose restart loads HITL+D1).
Resolves the dead-breaker BLOCKER tracked in
[[2026-06-11-bitunix-hitl-removal-blocked-dead-drawdown-breaker]] — **NOW RESOLVED**.

## Item 4 — non-interactive durable `--live` authorization: **BUILT + TESTED, UNMERGED** (branch 710e181, 2026-06-13)

The systemd service couldn't run live: `--live` required an interactive typed-LIVE
confirmation (`main.py` `confirm_live`) which EOFErrors under systemd → exit 2 →
crash-loop. **BUILT + TESTED on branch `bitunix-noninteractive-live-auth-2026-06-13`
(`710e181`, pushed, UNMERGED):** durable env `TC_LIVE_AUTHORIZED=LIVE` (persists across
restarts incl. crash/`Restart=on-failure` → resurrects live without re-arming; revoke by
unsetting → paper). Unauthorized non-interactive `--live` **downgrades to PAPER** (never
`return 2` → no crash-loop). Interactive typed-LIVE unchanged; `assert_live_ready` still
runs. 15/15 tests + full branch-vs-base zero code regressions.

**STATUS: COMMITTED on branch `710e181`, NOT MERGED, NOT DEPLOYED — the last *code* blocker
for autonomous live.** Go-live needs, in order, all operator-gated: **(a)** merge `710e181`
→ main → **(b)** deploy `main.py` (backup→LF→py_compile→md5→atomic-mv, like D1/HITL) →
**(c) CLAUDE.md invariant #3 edit — HARD PREREQUISITE, operator-run** (classifier blocks
agent edits; STOP-AND-READ #3 "`--live` requires interactive confirmation" contradicts the
deployed path) → **(d)** operator go-live restart (systemd unit `--live` +
`Environment=TC_LIVE_AUTHORIZED=LIVE`, flip `bitunix_futures.execution_mode: live`; that boot
loads HITL=0 + D1 + Item-4 → autonomous live). Full map: memory
[[bitunix-go-live-sequence-and-item4]]. Accepted residual risks: durable auth resurrects live
on crash; B1 **VALIDATED on first real fill 2026-06-14** (was the dropped unvalidated-on-real-fill residual risk); taker-fee net-negative.

## Observation window — active

> **2026-06-10 — FRESH window active** (post vol-classifier fix `7834375`, started
> 2026-06-09 03:49:41 UTC). Day-2 expanded review CLOSED clean — F-5 confirmed; report on
> branch `bitunix-day2-expanded-review-2026-06-10`. **⏰ Day-5 close-out due 2026-06-14
> ~04:00 UTC** — full window aggregate + flip-readiness inputs; **run from a prod-connected
> (local) session** (read-only SSH). Unblocks the P1 post-window backtest/TP-structure
> session and the P2 10006 backoff fix. Tracked here rather than a remote `/schedule`
> routine because remote cloud agents can't reach prod over SSH.

> **2026-06-08 update — window INVALIDATED** by the P1 finding below (`bitunix_htf_regime`
> volatility classifier bug — **0 fires for 6 of 7 days**). Day-7 close-out 2026-06-09
> **cannot** produce a flip-readiness verdict. A fresh observation window is required
> post-fix. Root cause + evidence in the P1 entry immediately below.

- **Day 2 audit completed 2026-06-02:** all gates intact, zero firings of Phase 3
  audit kinds, 6 bitunix paper trades (5W/1L), error rate normal,
  `_DB_LOCK_RETRY_DELAYS_SEC` retry exhausting 8× per 23.5h on `hitl/*` writes
  (pre-existing `agents/logger.py` path, not Decision 6.2's new path). Decision 6.2's
  `insert_paper_trade_record` retry: zero firings (silent). Audit at
  `reports/2026-06-02_phase3_day2_audit.md`.
- **Day 4 mid-window probe:** scheduled 2026-06-04. Same query set; compare
  8-count db-lock baseline + bitunix trade distribution + bitunix win rate.
- **Day 7 close-out:** scheduled 2026-06-09. Full window aggregate; verdict on
  whether `execution_mode: paper → live` flip is ready.

## P1 — `bitunix_htf_regime` volatility classifier ignores config; treats BTC ATR ≥3% as extreme (filed 2026-06-08 via Thread A investigation)

> **RESOLVED 2026-06-09 — DEPLOYED.** Fixed via merge `7834375` (`--no-ff` of `ab0d251`
> source + `ea92d4c` tests; Option 1a — `_atr_pct_to_tier` final tier boundary reads
> `extreme`/5.0% not `high`/3.0%). Deployed to prod 2026-06-09 03:49:41 UTC (MainPID
> 2397472; healthz `{"status":"ok","mode":"PAPER"}`; no config change — `extreme: 5.0`
> was already on prod). Orphaned `high` key filed as P3 (`4214c23`). **Fresh paper
> observation window started 2026-06-09 03:49:41 UTC; the 06-02→09 window stays
> INVALIDATED — a full fresh window is required before any `execution_mode` live-flip
> decision.** See deploy_log 2026-06-09 entry + memory
> [[2026-06-08-bitunix-volatility-classifier-wired-deployed]].

Root cause of zero Bitunix fires since 2026-06-02 22:15 UTC, identified 2026-06-08 via
paper-mode observation-window investigation.

**Bug:** `trading_corp/agents/strategies/bitunix_htf_regime.py:725-737` (`_atr_pct_to_tier`)
sets the high→Extreme boundary at the `high` threshold (3.0%) and does NOT read the
`extreme: 5.0%` value from `config/strategies.yaml:1268-1272` — the `extreme` key is dead
config. The strategy size-zeroes any trade (`size_multiplier=0.0`,
`hard_zero_reason="vol_tier_extreme"` at `bitunix_htf_regime.py:990-1001`; abandoned under
`htf_gate.mode=enforce` at `bitunix_futures_observer.py:1410-1416`) when BTC 1D ATR ≥3.0% —
which is normal BTC volatility, not extreme.

**Empirical evidence:** BTC 1D ATR has been ~4% since 2026-06-03; strategy traded **9×** on
2026-06-02 (ATR 2.92%, "high" band; Day-2 audit snapshot at 06-03T01:08Z showed 6 resolved),
zero since (ATR ~4%, hits the effective "extreme" band → size 0). Confirmed via signal-pipeline
trace: scoring + PA + HTF-regime all alive at high volume through 06-08; the directional gate
grants "short full size"; the final volatility hard-zero nulls the size. A6 regime trace shows
06-03/04/05 were STRONG_BEAR (tradeable) yet suppressed — ruling out a "correct chop stand-aside."
Distinct from the well-calibrated PA validator (`feedback_pa_gate_well_calibrated`); this is the
HTF vol-tier classifier.

**Impact:**
- Phase 3 paper-mode observation window (2026-06-02 → 2026-06-09) contaminated — strategy
  dormant 6 of 7 days. Cannot judge live-readiness from this window's data.
- Same cutoff would suppress real trades in live mode the moment `execution_mode` flips,
  defeating the strategy's intent.

**Fix scope:**
1. Read the `extreme` threshold from `config/strategies.yaml` per the existing config pattern.
2. Verify the other bands (`high`/`normal`/`low`) also read from config or are documented as
   hardcoded-by-design.
3. Backtest validates ≥5.0% as the intended cutoff against historical BTC data — confirm it
   produces a reasonable trade-eligible regime distribution before shipping.
4. Per CLAUDE.md §4: strategy-parameter change → Backtester approval required before any code
   change. Run the backtest first.

**Prerequisites for `execution_mode` flip:** this fix must land + a fresh paper-mode
observation window must be observed before any flip decision is meaningful. The current
2026-06-02 → 2026-06-09 window is invalidated by this finding.

**Reference:**
- Investigation report: `reports/2026-06-08_bitunix_silence_investigation/FINDINGS.md`
  (branch `bitunix-silence-investigation-2026-06-08`, verdict commit `9e9053b`).
- Phase 3 paper-mode observation window: `runbooks/deploy_log.md` entry 2026-06-02.
- CLAUDE.md §4 ("Things to ask before doing") — strategy-parameter change gate.

**Priority: P1 — RESOLVED (deployed 2026-06-09, merge `7834375`).** The wiring bug is
fixed and live; the only remaining gate to a live-flip is a fresh paper observation window
(started 2026-06-09 03:49:41 UTC), not this bug.

## P3 — Bitunix HTF volatility config has orphaned `high` threshold after Option 1a wiring fix (filed 2026-06-08)

After the P1 fix (commits `ab0d251` source + `ea92d4c` tests), the
volatility classifier final tier boundary reads `tier_thresholds["extreme"]`
(5.0%). The `high: 3.0%` config value becomes dead — present in config but
unused by the classifier. Same drift class as the original P1 bug, just
relocated by virtue of the 4-tier-vs-4-threshold structural redundancy.

**Resolution options (each defensible):**
1. Remove `high` from config schema + defaults (Option 1b from fix-session
   Phase A analysis). Cleanest end-state, hot-reload-sensitive.
2. Add a 5th tier (e.g., Elevated for 3-5%) so both thresholds stay live
   (Option 2 from fix-session). Requires backtest, real strategy-design
   decision.
3. Leave as-is with explicit documentation that `high` is vestigial (the
   `_atr_pct_to_tier` docstring already names it as such as of `ab0d251`).

**Priority: P3.** Not gating. The classifier behaves correctly per operator
intent (`extreme: 5.0%` knob is live); cleanup is honesty + schema hygiene.

**Reference:** P1 fix commits `ab0d251` (source) + `ea92d4c` (tests);
original Phase A analysis at fix-session worktree
`bitunix-htf-vol-classifier-fix-2026-06-08`.

## P3 — B1 hardening: malformed `stop_price` silently attaches NO server-side stop (filed 2026-06-11)

In `BitunixBroker._build_order_body` (B1, merged `5edf8ea` / deployed 2026-06-11), a non-float /
non-positive / absent `extra["stop_price"]` on a live OPEN entry causes the order to place with NO
attached server-side stop — silently (fail-safe-to-no-SL, not fail-closed-to-reject). Defensible
default (better than placing a bad/wrong-side stop), but a silent naked live entry warrants a
**log-warn or an order-reject** for defense-in-depth. Natural to address alongside the ratchet /
`modify_position_tp_sl_order` build. Ref branch `bitunix-b1-entry-attached-stop-2026-06-10`.

## P2 — Bitunix `code=10006 'request too frequently'` on account polls (filed 2026-06-10 via Day-2 review)

Account-poll WARNINGs + 1 replay-fetch ERROR (order `171d7a46`, retried OK)
observed during Day-2 window review. Self-recovering today. P2 not P3
because snapshot failure falls back to the placeholder-equity sharp edge
(H-11 class) — live-flip-relevant. Fix: poll backoff/jitter on 10006.
DO NOT fix mid-observation-window.
Reference: `reports/2026-06-10_bitunix_day2_expanded_review.md`.

**ESCALATED — go-live + read-only investigation (2026-06-13, post-go-live PID 2637434).**
Bitunix is LIVE since 15:36 UTC, so this now touches the REAL-MONEY path. New
gate supersedes the Day-5 date: **do NOT fix until after the first real live fill
is observed** (operator plan); the fix is a separate post-first-fill session.
Read-only investigation (agent SSH + code read, no changes):
- **Scope: all 10006 are on `/api/v1/futures/account` (balance) only** — 10× since
  the 15:36 boot, in 2 short bursts (15:52:06 ×6, 16:07:39–40 ×4). ZERO on position
  reads, order placement, or fill confirmation.
- **Does NOT trip the snapshot-staleness halt.** BitUnix returns HTTP 200 with
  `code=10006` in-body, so `r.raise_for_status()` (bitunix.py:419) does not raise;
  the `code!=0` branch logs a warning + `continue`s (bitunix.py:421-426). `snapshot()`
  still reaches `_last_successful_snapshot_ts = time.monotonic()` (bitunix.py:488)
  → freshness preserved → `_assert_snapshot_fresh()` (bitunix.py:556) not tripped.
- **Does NOT block/delay/mis-fire placement or fills.** `place_order` (bitunix.py:829)
  uses the place/position-mode/leverage/fill-observe endpoints via the `_request`
  retry helper — NOT `/account`.
- **Root cause:** the per-coin account loop (bitunix.py:411-419) issues 2 back-to-back
  `/account` GETs (USDT, USDC) with NO throttle/cache/retry on the account call, and
  multiple periodic poll loops (per-division pollers 30–300s in main.py, the 5-min
  equity_snapshot loop, dashboard + webhook-sizing snapshots) call `snapshot()`
  concurrently → bursts exceed BitUnix's /account rate limit.
- **Residual (low-prob, conservative):** a 10006 racing an order-time sizing snapshot
  under-counts equity (skipped coin) → that one entry under-sized or skipped
  (conservative; never over-trades). Could dent first-fill observation fidelity; not
  a safety risk.
- **Priority stays P2 (not raised to P1):** (b) confirmed it does NOT threaten the
  staleness halt or the order/fill critical path; impact is bounded + conservative.
- **Fix direction (design only — DO NOT implement; §4 real-money pipeline + pytest +
  prod-vs-main md5 sweep gate; post-first-fill):** single-flight / short-TTL cache on
  `snapshot()` so concurrent callers share one account read; and/or client-side
  throttle/spacing on `/account`; and/or dedupe overlapping pollers to one shared
  snapshot. Optionally add backoff-retry to the account call (currently skips →
  under-counts), but caching/single-flight is the primary lever (retry alone doesn't
  reduce rate pressure).
- **Verdict: live path SAFE to keep running to the first fill.**
Reference: go-live deploy_log entry 2026-06-13 15:36 UTC; this session's read-only investigation.

## P3 — Event-loop contention under live load → TradingView webhook delivery timeouts (filed 2026-06-13 post-go-live)

Background webhook-processing latency (`webhook_received` → handler completion) grew
from ~1.5–3s pre-go-live (paper, PID 2608235) to **8–10s post-go-live** (live, PID
2637434), measured in journalctl. The TV-driven handlers (`web/webhooks.py`) respond
HTTP 200 immediately and dispatch heavy work via `background_tasks.add_task`
(webhooks.py:281), so a TV "delivery timed out" is NOT slow handler logic — it
indicates the event loop is too contended to service/flush the request within TV's
~10s window. **Currently harmless:** the only TV strategies (`market_cypher`,
`lord_otter`) are `enabled: false`, so timed-out signals are received + ignored
(zero missed entries — confirmed in the 2026-06-13 webhook diagnostic). **Becomes
real if any TV-driven strategy is enabled live** (timeouts would hit tradeable
signals; the server still processes them in background, but TV reports failure).
Likely contributors: live BitUnix broker load + the 10006 bursts (see the P2 10006
item) + always-busy copy-trader batches. Action: profile event-loop blocking under
live load (sync/CPU-bound calls in the loop; the BitUnix snapshot/account path is a
candidate). P3 unless a TV-driven strategy is slated to go live.

## P3 — `assert_live_ready` has no `bitunix` branch → creds gate is a no-op for the live bitunix path (filed 2026-06-13 at go-live)

`assert_live_ready` (`utils/secrets.py:403-448`) validates creds for
robinhood/coinbase/fidelity/tastytrade/polymarket but has NO `bitunix` branch. So
`--live --brokers bitunix` passes the LIVE preflight on `ANTHROPIC_API_KEY` presence
alone, without verifying `BITUNIX_FUTURES_API_KEY/SECRET`. The CLAUDE.md STOP-AND-READ
#3 "populated creds via assert_live_ready" is thus generically true but unenforced for
bitunix. **Backstopped today** by the broker stub-mode guard: with missing creds,
`BitunixBroker.place_order` raises NotImplementedError (bitunix.py:843-848) →
fail-closed (no silent live order on a stub broker). So this is a defense-in-depth gap,
not an active hole (creds are present — confirmed by the live $343.07 equity read).
Fix: add a `bitunix` branch to `assert_live_ready` asserting key+secret presence.
§4-adjacent (creds/secrets path) — explicit approval + test before deploy.

(Note: the Fidelity Playwright Firefox `ENOENT` → paper-fallback surfaced at the
go-live boot is ALREADY filed below as a P3 — not duplicated here.)

## P1 — Bitunix post-window analysis: silence-window what-if backtest + TP-structure review (filed 2026-06-10, execute after Day-5 close-out 2026-06-14)

Two related questions, one session (~3-4h, read-only vs snapshot/prod):

A. WHAT-IF BACKTEST of the 2026-06-02→09 silence window: replay the
suppressed signals (signals fired + approved but size-zeroed by the
vol-classifier bug) through the v2 3-leg TP plan against 1m bars.
Output: trade count, W/L, R distribution, cum P&L of what was missed.
Known caveat: same intrabar TP-vs-advanced-SL ambiguity as the
reconciler P3 (`70d50f7`) — results are approximate, ±chronic variance.

B. TP-STRUCTURE ANALYSIS (the Day-2 Q3 finding): across all available
samples (live window + backtest set from A), characterize why TP3 is
never reached and wins average sub-1R (Day-2: TP3 reached 0/16, TP2
6 @ avg 0.78R, TP1-only 7 @ avg 0.17R, stopped 3 @ -1R, expectancy
+0.176R). Questions: is TP1 sizing/target taking too much too early?
Are TP2/TP3 targets calibrated for a volatility regime that doesn't
run? What would expectancy look like under alternative TP structures
(re-walk the same trades with modified leg targets — per-CLAUDE.md §4
Backtester-gate territory if it leads to a parameter change)?

Output feeds the live-flip decision directly: +0.176R expectancy is
thin against live fees/slippage; flip decision needs to know whether
TP-structure tuning is required first.
Reference: `reports/2026-06-10_bitunix_day2_expanded_review.md` (Q3),
P1 vol-classifier fix (RESOLVED `7834375`).

## Open items influencing the live-flip decision

These are operationally relevant but NOT formal flip-gates. Operator decides
whether to address before flip OR after flip.

### P2 — Low-equity Telegram alert for `bitunix_futures` division

Filed 2026-06-01 via Finding #10 triage. When equity drops below configurable
threshold (suggest 80% of starting), emit Telegram ping per CLAUDE.md HITL
surface direction (short ping + deeplink, no detail in body, debounced).
Reuses existing `safety_notifier` infrastructure.

Daily-loss-cap in risk gate is the structural safety; this alert is
belt-and-suspenders observability. Not gating execution_mode flip.

### P2 — Per-division configurable equity placeholder for webhook snapshot-failure fallback

Filed 2026-06-01 via Finding #10 triage (architectural review H-11 sharp edge).
Current behavior: webhook risk gate falls back to `equity = 100_000.0` on
snapshot failure. Defensible for paper-mode analytics; operationally dangerous
for live mode (sizes trade against placeholder).

Operator decision: change fallback to per-division configurable placeholder,
defaulting to small conservative value, with explicit per-division overrides:
- `bitunix_futures: 10000` (matches operator-stated discipline of keeping
  Bitunix topped up to $10K).
- `coinbase_spot: <operator-decided based on actual account>`.
- Default: $1K.

Mode-aware stand-down behavior (alternative architectural choice) deferred —
may revisit before `execution_mode` flip if operational patterns suggest it.

### P2 — Bitunix paper-trade `actual_pnl_dollars` persistence

Persistence path for actual P&L dollars per closed trade. Currently computed
on-the-fly; persistence simplifies dashboard rendering + reduces stale-data risk.

### P2 — Bitunix paper-mode cost-accrual (fees + funding)

Layer 2 follow-up to Session B's Layer 1 fee plumbing. Track cumulative
realized P&L net of fees + funding rate accruals across the observation
window.

### P2 — Bitunix dashboard full 5-panel rebuild

Separate session work. Dashboard tile rebuild to surface more decision-quality
signal (vs. current trade-flow-centric view).

### P2 — Bitunix PA validator raw-input audit

Instrumentation layer — capture inputs to the PA validator for later analysis
of decision quality.

### P1 — Bitunix PA validation observation window (closes 2026-06-03 ~23:18 UTC)

Separate PA-specific validation window. Verify validator behavior on observed
trades; close on schedule.

## Investigative items (paper-mode period, no rush)

### P3 — Investigate TP1 `target_r` calculation in v2 3-leg `tp_plan`

Filed 2026-06-01 via dashboard inspection of trade `2b418971-7955-4dd4-ae20-8e56d4c9401c`.
TP1 at `target_r=0.972`, TP2 at `target_r=1.000` (default_1r) — produced TPs
$3.75 apart, 75% of position effectively exiting at same level. Math is correct
for stated `target_r`; question is what produces TP1's non-clean R value.

`extra_json` surfaces `tp2_method="default_1r"` but NOT `tp1_method` — separate
finding: TP1's method should be auditable.

Read-only investigation, ~30-60 min. Locate v2 tp_plan construction (likely
`agents/strategies/bitunix_confluence.py`); identify TP1 method; assess whether
near-1R values in certain conditions are by design or a bug.

### P3 — Audit `proximity_to_support` / `proximity_to_resistance` hard-zero behavior

Filed 2026-05-31. HTF proximity rule may zero out trade probability when
support/resistance is "too close" but the operator's read of structural
significance differs from the mechanical detector. Related to AlexO market
structure framework Option B (body-close validation) — see CLAUDE.md skill
references.

### P3 — Post-Session-B audit of analogous paper-vs-live timing assumptions beyond Finding #5

Filed 2026-06-01 via Finding #10 triage Decision #6. After Session B lands AND
paper-mode exercise begins, brief audit of paper-vs-live timing assumptions
beyond cases 5a (classifier-bar vs broker-event) + 5b (`_observe_fill`
one-shot vs re-poll). Surface any additional cases that emerge from running
the new wiring.

Prerequisite: Session B merged (✓) + 1-2 weeks paper-mode exercise. Not gating.

### P1 — Revisit BitUnix scoring weights after ≥30 live PREMIUM fires post-H2

Tune scoring weights based on observed fire-rate + win-rate of PREMIUM-tier
trades after enough live PREMIUM fires accumulate. Needs ≥30 sample size.

---

# Priority 2 — Polymarket Copy Trading path to live trading

## P1 — Polymarket copy-trader SELL-pairing → option (c) (Phase 1 MERGED 2026-06-10, NOT run on prod; phases 2-4 pending)

**Phase 1 status (2026-06-10): MERGED to main, NOT DEPLOYED / not run against prod.**
Branch `polymarket-option-c-phase1-2026-06-10` (stays on origin as audit trail — not deleted).
`refresh_polymarket_whales.py` now screens on REDEEM-grounded realized P&L (`build_audit_report`
+ `score_whale_from_audit`): decision-unit Wilson WR × realized ROI × category bonus, a
`pnl_inflation_ratio` exclusion gate (default 0.5), a window-truncation gate (excluded from algo
selection, observable, pin-overridable), and an `/activity` walk-to-exhaustion. **Refresh defaults
to PINS-ONLY** (writes only pinned whales); `--algo-select` is an explicit opt-in — a default
refresh never auto-expands the copy roster. New compute is **inert until a deliberate operator
refresh run.** Validation:
[`reports/2026-06-10_polymarket_option_c_phase1_validation.md`](reports/2026-06-10_polymarket_option_c_phase1_validation.md)
(realized reconciles to Polymarket `/closed-positions` to the dollar on complete windows; 2256
tests pass / 28 pre-existing). **Phases 2-4 UNSTARTED:** P2 observation roster (`seed_*_deep`),
P3 unify both onto shared compute, P4 cleanup (remove the legacy held-to-resolution path).
NOTE: F-4's `auto_paused_whales` key (mentioned in the SCOPED paragraph below) was **SUPERSEDED —
not built**; refresh stays manual/unscheduled, so scheduling creates no flap.

**Known autopause-pin flap (benign):** a pins-only refresh rewrites the full pinned set, which
currently includes the 2 autopaused whales (Johnnyboy42069, damed21 — still pinned) →
`selected_whales` 13→15 transiently until `_whale_autopause` re-removes them (~60s). Harmless
(paper-mode, operator-pinned, whale-own-profitable per Phase E reconciliation). Clean resolution
(unpin, or have autopause also clear `pinned_whales`) belongs with the **demotion-transparency P3
entry below** — do NOT touch autopause for this.

**Status (2026-06-09): investigation COMPLETE, option (c) selected as fix path.**
Full findings: [`reports/2026-06-09_polymarket_sell_pairing_investigation.md`](reports/2026-06-09_polymarket_sell_pairing_investigation.md)
(on branch `polymarket-sell-pairing-investigation-2026-06-09`, unmerged planning
artifact — mirrors the Robinhood Agentic evaluation pattern).

**Selected fix — (c) net-position whale P&L from the activity feed.** Compute each
whale's P&L from net position + VWAP entry/exit over the `ActivityRow` stream we
already ingest, instead of pairing our copy SELL/BUY audit rows. Sidesteps BOTH
structural causes below. (a) deferred
(subsumed by (c) for the stated goal); (b) rejected (removes 97% of trade volume);
(d) rejected (type-mismatch refuted — `outcome_index` is `integer` on every row).

**Implementation SCOPED (2026-06-09):** [`reports/2026-06-09_polymarket_option_c_implementation_scoping.md`](reports/2026-06-09_polymarket_option_c_implementation_scoping.md)
(branch `polymarket-option-c-scoping-2026-06-09`, unmerged planning artifact).
Framing (ii): the REDEEM-grounded compute already exists and is deployed
(`build_audit_report`, `data/polymarket_whale_audit.py`, `df3e48b`) — option (c) is
*operationalization*, not greenfield. **Revised scope: ~1–2d for Phase 1** (route the
copy-roster screen `refresh_polymarket_whales.py` through `build_audit_report`) vs. the
original ~3–5d. Phasing: P1 copy roster → P2 observation roster (`seed_*_deep`) → P3
unify both onto shared compute → P4 cleanup. Coexistence gap surfaced: add an
`auto_paused_whales` agent_state key so a refresh doesn't silently re-add whales
`_whale_autopause` dropped (shared `selected_whales` key today). 5 operator-resolved
decisions in the doc (selection metric, branch disposition, validation gate, autopause
model, refresh cadence). The superseded `pm-watchlist-pnl-aggregation-fix` branch is
NOT a prerequisite (its fix is already on main, `899821d`). Implementation UNSTARTED.

**Dual structural causes (verified against prod, read-only):**
- **Partial-fill duplication (4.6×):** 5,084 copy BUY `would_have_placed` rows
  collapse to 1,115 real `(whale,condition,outcome)` positions; 130 positions
  carry 10+ BUY rows (one had 216; one burst was 66 buys in 582 s).
- **Settle-path contention:** `polymarket_resolver._fetch_unresolved_orders`
  (`agents/polymarket_resolver.py:65-83`) resolves copy-trader BUY rows into
  round-trips keyed on the BUY's `order_id`, consuming them before sell-pairing
  can use them. 466/484 (96%) of the "BUY-exists-but-unpaired" SELLs lost their
  BUY this way; 90% of all copy round_trips (4,565/5,058) are settle-derived.
- Of 874 unpaired SELLs: 55% have a consumed BUY (above), 44% have no BUY logged
  at all (entry never copied), <1% had a risk-rejected BUY.

**Background:** `polymarket_resolver._pair_pending_exits` re-scans ~720
unpaired copy-trader SELL `would_have_placed` rows every tick and skips ~99.86%
as `skipped_no_entry` — can't find prior BUY (matched on `whale_wallet` +
`condition_id` + `outcome_index`, entry `ts` < sell `ts`, unpaired).

**Operator hypothesis (2026-06-02):** the SELL-pairing problem may not be a
bug in trade-matching logic. Likely cause: whale orders that fill incrementally
over time (one $50K limit order filling in 50 chunks) appearing as 50 separate
trades. If true, the fix is upstream — aggregate partial fills of the same
order before treating them as separate trades — not "match SELL to BUY"
algorithmic work.

**Investigation needed (read-only, ~30-60 min, prerequisite to engineering fix):**
1. Sample skipped SELLs: does a matching BUY actually exist in `audit_event`?
2. Type-check `outcome_index`/`whale_wallet` in BUY vs SELL payloads.
3. Determine fill pattern: are the skipped SELLs from whales who split orders
   over time? Or whales we never copied a BUY for?
4. Quantify: how many profitable whales bet in single chunks vs split? Could
   the watchlist filter out splitters without losing significant signal?

**Decision branches after investigation:**
- (a) Engineering fix: aggregate partial fills upstream (probably moderate scope).
- (b) Operational workaround: filter watchlist to whales whose bet patterns
  don't trigger split-fill behavior (probably small scope).
- (c) Algorithm change: compute whale P&L via net position + entry/exit average
  prices without trade-pair matching (probably large scope).

**Why this is the highest-impact open item:** the copy-trader can't go live
until whale P&L attribution is accurate. The current 99.86% skip rate means
the "winning trader" identification is unreliable. This is the structural
blocker for Priority 2.

## P3 — Polymarket whale demotion transparency in dashboard (filed 2026-06-10 via workflow verification)

**Context:** Verification session 2026-06-09 confirmed autopause runs every 60s on prod (per `polymarket_copy_trader.py:185 → :571`). Three autopause events recorded (2026-05-15, 2026-06-03, 2026-06-09). System operates correctly per code; operator workflow now explicitly includes both manual demotions AND system-driven autopause demotions. Operator accepts the autopause functionality but needs transparency on demotion events.

**Operator requirement:**
1. Dashboard surface: visible status for whales recently demoted (system or operator).
2. Promotion-guard: when promoting from watchlist, surface previous demotion history for that whale to prevent inadvertent re-promotion of known-bad performers.

**Scope:**
- New dashboard tile/section showing recent demotions (autopause + manual) with: wallet, user_name, demote_ts, source (autopause/operator), reason (autopause: trade count + WR% + P&L; operator: optional note).
- Promote-button modal: if wallet has prior demotion event, surface warning with demotion history.
- Source data: autopause writes audit events today (`polymarket_whale_auto_paused`). Manual demotion path needs to write equivalent (or already does — verify).

**Implementation notes:**
- Audit event already exists for autopause (`polymarket_whale_auto_paused`).
- May need parallel audit event for manual demotion if not already present.
- Dashboard query against audit_event filtered to demotion-class events.
- Promote-button check: query demotion history for wallet before write.

**Not gating:** any active development. Quality-of-life feature for operator transparency.

**Reference:** workflow verification report `reports/2026-06-09_polymarket_workflow_ground_truth_verification.md`.

## P3 — Polymarket `/activity` pagination ceiling caps full-history reconciliation (filed 2026-06-10 via option (c) Phase E re-validation)

**Finding:** the public `/activity?user=` feed stops serving past ~3,500 rows (page 8 at
`limit=500`) — a Cloudflare-403 / hard pagination cap. For the highest-volume, longest-history
whales their full BUY/SELL/REDEEM history is NOT retrievable, so REDEEM-grounded realized P&L is
computed on a truncated window and **over-states** (incomplete cost basis). Reproduced 2026-06-10
on AdrianCronauer (`0xf9c1…`, walk `fetch_error` at 3,500 rows; my realized $1.56M vs Polymarket
`/closed-positions` $108k) and BigodinSagaz (`0xca1e…`, 25/168 matched positions over-stated).
Whales with complete windows reconcile to the dollar (Magamyman 85/85, kitten147 156/162) — so
this is an API-retrieval limit, **not a compute bug**.

**Containment already shipped (option (c) Phase 1):** such whales are flagged `window_truncated=true`
and **excluded from algorithmic selection** (gate `f448c93`; proven by
`test_truncated_whale_cannot_enter_algo_selected_roster`). The over-stated number cannot drive a
copy-roster pick — this P3 is about *completeness*, not safety.

**Investigate (not blocking):** is offset >3,500 ever fetchable for these wallets (longer backoff /
retry / alternate endpoint), or is it a hard cap? If hard, the highest-volume whales are permanently
unrankable by the algorithm (manual pin remains available). Matters because longest-history whales
are plausibly among the most interesting to copy.

**Reference:** convergence table in `reports/2026-06-10_polymarket_option_c_phase1_validation.md`
(Re-validation section). Relates to the Cloudflare-retry-burn P2 and the 0.0s-backoff P3 below.

## P3 — Polymarket audit-cache unification deferred: cache key `(wallet, activity_max_ts)` is scope-blind (collision risk) — filed 2026-06-12 via option (c) Phase 3 Phase B

**Context:** option (c) Phase 3 "Option 1" (small extraction, branch
`polymarket-option-c-phase3-unify-2026-06-11`) shared the per-candidate
`/activity` walk + loop wrapper between `refresh_polymarket_whales` and
`seed_polymarket_watchlist_deep` via the new `trading_corp/data/whale_screening.py`.
The audit cache (`agents/research/polymarket_whale_audit_cache.py`,
`read_audit`/`write_audit`) was **deliberately left OUT of that extraction.**

**Collision risk (why it can't be folded in under a byte-identical mandate):**
the cache key is `(wallet, activity_max_ts)` — **scope-blind.** `refresh` builds
a FULL-window `WhaleAuditReport`; `seed` builds a WINDOWED (last-100-decision)
report. For the SAME wallet at the SAME `activity_max_ts` those are DIFFERENT
reports. `refresh` reads/writes the cache today; `seed` deliberately skips it
(see the comment at `seed`'s windowed `build_audit_report` call). If `seed` were
wired to read-through the existing cache, a `refresh` entry would be served for
`seed`'s windowed request (or vice versa), silently corrupting one roster's
realized PnL.

**Why deferred, not done:** unifying requires adding a scope discriminator to
the key (e.g. `(wallet, activity_max_ts, window_scope)`) → a NEW key shape →
NEW cache entries → a behavior change, not the byte-identical refactor Phase 3
Option 1 was scoped to. It is therefore separate, behavior-affecting work.

**Scope if picked up:** add the scope discriminator to the cache key + writer +
reader; decide whether `seed` should cache at all (today it recomputes each run
— correct but slower); add a test that a `refresh` full-window entry and a
`seed` windowed entry for the same `(wallet, ts)` do NOT alias; validate
realized PnL unchanged for both rosters.

**Priority: P3.** Not gating — `seed` skipping the shared cache is correct today
(no collision is possible while it doesn't read it). This is a
performance/architecture item, and a TRAP flag to stop anyone later "optimizing"
`seed` by pointing it at the audit cache without keying by scope.

**Reference:** Phase A duplication map
`reports/2026-06-11_polymarket_option_c_phase3_phaseA_duplication_map.md` §4
("The audit cache cannot be unified inside a byte-identical refactor"); Phase
B/C/D extraction on branch `polymarket-option-c-phase3-unify-2026-06-11`
(`reports/2026-06-12_polymarket_option_c_phase3_phaseBCD_extraction.md`).

## P3 — Polymarket retry backoff: currently 0.0s on 429 responses

Filed 2026-05-31. Cloudflare/Polymarket 429 responses hit retry path with
backoff=0.0s — effectively no backoff. Likely a `max(0, computed_delay)` bug
or missing minimum-backoff floor.

## P3 — Polymarket: add `division` column to `polymarket_round_trips`

Filed 2026-05-09. Copy-trading reuse of the round_trips table needs division
disambiguation for per-division P&L attribution.

## P3 — Polymarket Gap C: open-positions cache (paper-mode equivalent)

Filed 2026-05-09. Open positions cache for paper-mode parity with live mode.

## P3 — Polymarket portfolio dashboard (betmoar.fun-inspired)

Filed 2026-05-09. Division-reusable portfolio view. Lower priority than
SELL-pairing.

## P2 — Polymarket dedupe follow-up: underlying/series-level concentration cap

Filed 2026-05-21. Blocked on per-`condition_id` cap ship (operator-approved
2026-05-21) + post-cap data review. Verify ship status before working on
this follow-up.

---

# Priority 3 — InfoSec

## P1 (recurring) — Run InfoSec Architect audit, file findings as priority items

Operator runs the InfoSec Architect skill periodically; findings get filed
into this file as priority items (P0/P1/P2 based on severity).

**Last full audit:** 2026-05-21 — `reports/2026-05-21_security_review.md`
(committed `e88d663`). Identified 7 CRITICAL (S-1 through S-7), 17 HIGH,
22 MEDIUM, 13 LOW findings.

**Status of 2026-05-21 audit findings:** mostly remediated per operator
(2026-06-02 grooming). The roadmap below preserves the critical findings list
for reference; specific status of each should be verified by next audit run.

**Next scheduled audit:** open (operator-triggered).

### Open InfoSec items from 2026-05-21 audit (verify status on next run)

**P0 CRITICAL roadmap (from 2026-05-21 review, may be substantially complete):**

| # | Finding | Original Effort |
|---|---|---|
| S-1 | Local `.env` may hold full live secret set in plaintext. Rotate every secret + depopulate workstation `.env` to just `KEY_VAULT_URI=`. | 1–3h |
| S-2 | `TradeConfirmation.verdict == "push_back"` skips `RiskAgent.evaluate()`. Route through risk gate as forced-reject. | 1–2h |
| S-3 | `_check_auto_execute` re-reads `strategies.yaml` per-order with no mtime cache, no schema validation. | 2h |
| S-4 | Timer service units run as `User=root` with no sandbox directives. Rewrite as `User=azureuser` + sandbox. | 2h |
| S-5 | No production DB backup. Nightly `sqlite3 .backup` → encrypted Azure Blob. | 4h |
| S-6 | No dependency lockfile / hash pinning. `pip-compile --generate-hashes` → `requirements.lock`. | 30m–1h |
| S-7 | Rejected-webhook audit writes `raw[:500]` containing secret in plaintext. Scrub + backfill. | 1h |

**HIGH/MEDIUM highlights from 2026-05-21 review:**
- HIGH H-1/H-2/H-3: Replace static-bearer webhook auth with HMAC-SHA256 + replay window.
- HIGH H-10: Telegram bot has no sender-ID allowlist.
- HIGH H-12: 4 DR runbooks needed (VM compromise, KV compromise, broker-key rotation, panic halt).
- HIGH H-13: Azure VM has no Trusted Launch (Secure Boot, vTPM).
- HIGH H-15: No CI pipeline. GitHub Actions + branch protection + signed commits + `pip-audit` + `bandit` + `trufflehog`.

Full report: `reports/2026-05-21_security_review.md` §5.

### Specific open items surfaced or filed since 2026-05-21

#### P1 — Tastytrade env vars bypass KV path

`TASTYTRADE_PROVIDER_SECRET` and `TASTYTRADE_REFRESH_TOKEN` loaded via systemd
`EnvironmentFile=/etc/trading-corp/tastytrade.env` instead of KV. Bypasses
`_populate_from_keyvault`, the `_SECRET_KEY_NAMES` redaction list, and
`register_redact_literal()` calls. Creates parallel secret-handling path
outside documented KV-first architecture.

Fix path: upload secrets to KV, patch `utils/secrets.py` to include the two
keys in `_SECRET_KEY_NAMES` + `expected_env_vars`, remove the `EnvironmentFile=`
drop-in, shred the file. Bundle with AM SDK-bug fix branch (both touch same
provider).

Risk if deferred: low marginal (creds already on prod, 600 root-owned). Cost:
no rotation via KV, no audit, redaction filter blind to the values.

#### P1 — Real SMTP for Authelia notifications

Filed 2026-04-30. Maps to H-14 in 2026-05-21 review.

#### P2 — Tighten prod-access permission rules in `.claude/settings.local.json`

Filed 2026-05-22. Maps to AI-attacker-angle section of 2026-05-21 review.

#### P2 — Polymarket + Kalshi deep-watchlist timers run as root

Filed 2026-05-23. Maps to S-4 from 2026-05-21 review (broader fix).

#### P1/P2/P3 — VM security state anomalies (from §7 verification)

Filed 2026-05-23. 13 commands from 2026-05-21 review §7 to verify on `tc-prod-vm`
(Caddyfile, Authelia, sshd, sudoers, unattended-upgrades, AppArmor, Defender,
VM Trusted Launch state, DB pragmas, Kalshi PEM tempfile cleanup). Verify
status on next audit run.

---

# Other Open Items (not in priority list above)

## P3 — `TypeError: not all arguments converted during string formatting` ×123 in tastytrade-streamer/starlette logging path (filed 2026-06-10)

Cosmetic `%`-format bug; log lines still emit; zero functional impact on
bitunix or any division. Cleanup candidate.
Reference: `reports/2026-06-10_bitunix_day2_expanded_review.md`.

## P2 — `scripts/redeploy3_chunked_transfer.py` worktree-stranded

Filed 2026-06-02 via Phase 3 deploy. Script is referenced as canonical in
CLAUDE.md but doesn't exist on origin/main — lives only on branch
`stage1-redeploy3-session-2026-05-30` (commit `3088966`). Fresh session
checking out origin/main gets "file not found."

**Fix:** cherry-pick `3088966` to a fresh branch off main, parameterize the
hardcoded 66-file manifest via `--manifest <path-to-json>`, bring forward the
redeploy3 deploy_log entry into main. Estimated ~2-3h.

## P3 — `scripts/prod_vs_main_file_level_md5_sweep.py:124` LF-normalizes binary files

Filed 2026-06-02 via Phase 3 deploy. `local_md5_lf()` unconditionally calls
`data.replace(b"\r\n", b"\n")` before hashing — corrupts PNG/ICO/binary file
hashes. Produces false-positive DIFFER on binaries.

**Fix:** add `is_text_file()` filter (~1 line). ~30 min.

## P3 — pytest 9.0.3 default-abort-on-collection-errors

Filed 2026-06-02 via Phase 3 deploy. Need `--continue-on-collection-errors`
flag for canonical 28/3 baseline gate, OR delete the 3 stale-import test files
that import the removed `bitunix_confluence_gate` module.

Affected files (all import `trading_corp.agents.strategies.bitunix_confluence_gate`):
- `tests/test_backtest_bitunix_confluence_five_factor.py`
- `tests/test_bitunix_confluence_gate.py`
- `tests/test_bitunix_gate_inputs.py`

**Fix:** ~30 min to delete the tests, ~2h to restore the module.

## P3 — `test_paper_run_tooling.py` readiness checks have undocumented `data/trading_corp.db` filesystem dependency

Filed 2026-06-01, refiled with corrected framing during Session B pre-flight.
Tests fail 28/3 in fresh worktrees, pass 26/3 only on machines with prior
DB-init activity. Either: (a) make tests self-contained (init temp DB in
fixture), (b) mark as integration-only, or (c) document the dependency
explicitly.

## P3 — PROD_ONLY anomalies surfaced by Item 5 sweep

Filed 2026-05-31. Three files exist on prod that are NOT git-tracked on
`origin/main` and do NOT match documented `.bak-<label>-<date>` or
`.pre-<label>-<date>` deploy-backup conventions. Item 5 sweep flagged for
review. None block redeploy attempts; cleanup is operator-curated, low-priority.

## P3 — Stage-1 paper-mode dashboard precursor charts

Filed 2026-05-31. Dashboard precursor chart work for Stage 1 paper-mode observability.

## P3 — Discipline: derive deploy windows from `systemctl ExecMainStartTimestamp`

Filed 2026-05-31. Use systemctl for prod-deploy windows rather than
prompt-stated timestamps.

## P3 — Stage-1 BitUnix readiness gaps — low-severity

Filed 2026-05-30. 2 untracked low-severity items from Stage-1 readiness audit.

## P3 — Wider db_url plumbing through risk.evaluate sites

Filed 2026-05-29. Stage-1 N+1 follow-up. Partial coverage post-merge; complete
the plumbing across remaining risk.evaluate call sites.

## P3 — Fidelity Playwright Firefox binary missing → both Fidelity divisions paper-fallback (sharpened from prior "login flakiness" P3 — finding 2026-06-09 via P2 restart)

**Symptom:** Both `fidelity_joint` and `fidelity_401k` divisions fail
broker connect at trading-corp startup; fall back to paper mode.
Their dashboards show $0 equity.

**Concrete cause (verified 2026-06-09 14:49:03 UTC via post-restart
journal):**
- `BrowserType.launch: ENOENT … ms-playwright/firefox-1511/firefox/lock`
- Playwright Firefox binary missing from cache at expected path.
- Triggers `broker_fallback_to_paper` for both Fidelity divisions.

**Sequence:**
1. `fidelity_joint` connect fails first (browser binary missing) →
   paper fallback.
2. `fidelity_401k` sees "Fidelity shared session bootstrap previously
   failed" → paper fallback.

**Fix:**
- `playwright install firefox` on prod VM (likely as azureuser).
- Verify binary lands at expected cache path.
- Restart trading-corp; expect Fidelity divisions to connect cleanly.

**Watch concern:** the binary may have been wiped by an unattended-
upgrades cleanup, OS pruning, or a previous deploy. Confirm root cause
of the missing binary BEFORE just re-installing — re-installing
without understanding the deletion mechanism risks repeat.

**Priority: P3.** Read-only equity monitoring, no real-money execution
surface today. Same priority class as the prior Robinhood P2 session
auth (which has now been RESOLVED 2026-06-09).

**Not gating:** Bitunix observation window, Polymarket development.

**Supersedes:** prior "Fidelity startup login flakiness on trading-corp
restart" P3 entry (which was vague; this finding sharpens to concrete
cause).

## P3 — Wider db_url plumbing for cross-process halt persistence

N+1 follow-up; PARTIAL coverage post-merge.

## P3 — `tasty_options` config block missing from prod's `strategies.yaml`

ANOMALY. Deploy gate path question — see also the committed-but-undeployed
P2 entry above.

## P2 — Reconcile committed-but-undeployed main vs prod divergence

Filed 2026-05-29. `origin/main` has tasty_options + iron_condor wiring
committed but not deployed. Every deploy that touches `secrets.py`/`main.py`
must navigate this drift surgically. 2nd documented occurrence as of
2026-05-29.

**Resolution:** either (a) fire Phase-0 sandbox smoke for tasty_options and
deploy it (closes gap, commits real-money order-placement wiring — needs own
gate); or (b) revert un-deployed commits off main and re-introduce when smoke
passes.

## P3 — Reconciler intrabar TP-vs-advanced-SL path ambiguity (chronic variance, documentation + remediation gap) — filed 2026-06-08 via Thread C investigation

**Finding:** Three flagged trades (c8f25d17, ac5f9c59, c2eb7cda) show
recorded-vs-sim R deltas of -0.418, +0.437, +1.125 respectively.
Root cause is chronic intrabar path ambiguity in the 1m re-walk
reconciler (audit_reality_reconciler.py → _classify_v2_multi_leg),
NOT a regression.

**Mechanism (code-confirmed):**
- paper_trade_replay.py:503-574: SL is checked at bar-start against
  prior current_sl. Advanced (ratcheted) SL after a TP fill is only
  applied via current_sl = new_sl at line 574 → evaluated on the
  next bar.
- When TP fill + advanced-SL-stopout collapse into one 1m bar, the
  sim fills the legs and misses the same-bar advanced-SL exit.
- Bidirectional: c8f25d17 shows the reverse (sim's SL-first walk
  truncates a fill the live path credited).
- Same class as the prior 3m→1m granularity fix (06b5a9e, took
  mismatches 12/17→17/17). These three are the residual sub-1-minute
  tail it cannot reach.

**Source of truth:** Recorded is authoritative ("audit wins" per
CLAUDE.md STOP-AND-READ #2). Sim is a cold re-walk diagnostic.
Paper-mode, no capital at risk.

**Two scope items:**

1. **sharp_edges.md documentation gap.** The original-SL intrabar tie
   case is documented; the advanced-SL case is NOT. Add the
   advanced-SL intrabar reconciler-variance entry for completeness.

2. **Remediation for the 3 flagged trades.** Mark each as
   audit_corrected using the existing audit_corrected /
   corrected_r_multiple mechanism (audit_reality_reconciler.py:189-202).
   This stops the dashboard's RECONCILER MISMATCH tile from showing
   these as persistent INVESTIGATE items. Prod write — operator
   action, not in-session.

**Why filed P3:** chronic, irreducible at 1m granularity, audit is
authoritative, paper-mode only. Diagnostic-tool fidelity rather than
trading-correctness concern. But sharp_edges.md gap erodes future
diagnosis quality; flagged-trade noise erodes future reconciler-tile
signal quality. Worth tracking, not urgent.

**Reference:** Thread C investigation commit 10a8bfd.

**Not gating:** any active development.

## P3 — kalshi_weather tier-1 schema committed but NOT deployed

Filed 2026-05-29. ANOMALY. Same shape as the tasty_options committed-but-undeployed
drift.

## P1 — Polymarket dedupe: per-`condition_id` position cap

Filed 2026-05-21, operator-approved 2026-05-21. Verify ship status — may be
complete.

## P1 — Polymarket clean-data tracker

Filed 2026-05-21. Trades with `entry_ts` before 2026-05-21 12:28:07 UTC are
pre-cap and excluded from the 50-trade floor.

## P2 — bitunix dashboard full 5-panel rebuild

(Cross-referenced from Priority 1 — also listed there.)

## P3 — Replay-loop bar-buffer optimization

Filed 2026-05-11. Nice-to-have.

## P3 — Pink Box S/R confluence integration

Filed 2026-05-10. Mechanical S/R zone integration; related to AlexO market
structure framework Option A (operator-curated S/R zones). See CLAUDE.md skill
references.

## P3 — CLAUDE.md inline § references could be anchored links

Filed 2026-05-16. Convert §-references in CLAUDE.md to anchored markdown
links for easier navigation.

## P3 — `tests/test_webhooks_return_fast.py` 5 failures from `_Deps.bitunix_observer` fixture gap

Filed 2026-05-26. Test cleanup.

## P3 — Copy-trader `equity_history` writer never wired

Filed 2026-05-24. Cleanup.

## P3 — Analyze button has no collapse — toggle the whale-audit panel open/closed

Filed 2026-05-26. UX.

## P2 — Cloudflare-retry burn vs `TimeoutStartSec=3600` on watchlist deep timers

Filed 2026-05-23. Ops.

## P1 (ops/security) — Deferred 43-package upgrade from C-6 lockfile drift

Filed 2026-05-24. 43 deferred package bumps from C-6 lockfile reversal.

## P0 — Crash diagnosis (2026-05-19)

Local Python workstation crashes; partial diagnosis at
`docs/diagnostics/2026-05-19_crash_diagnosis.md`. Mitigation in place
(`scripts\run_capped.ps1` wrapper with 25GB Job Object cap per CLAUDE.md
STOP AND READ #6). Root cause still open.

## BitUnix — post-funding diagnostics (2026-05-21)

Investigative checklist after Bitunix funding. May be partially complete;
verify status.

## P2/P3/P4 — 2026-05-14 deferred items from specialized-agent work

Pre-Phase-3 era items from specialized agent sessions. Review for relevance
at next grooming.

## P2/P3 — 2026-05-16 PM Dashboard hygiene followups

Older dashboard hygiene items. Review for relevance.

## P2/P3 — 2026-05-15 K3 Watch-only follow-ups

Older Kalshi K3 items. Review for relevance.

## P2/P3 — 2026-05-15 Kalshi Weather Tier-2/3 follow-ups

Older Kalshi weather items. Review for relevance.

## P5 — Rename `EngagementSpec.requesting_division` → `requesting_strategy`

Filed 2026-05-02. Naming cleanup.

## P5 — Realignment-memo wording: `would_have_placed` is Otter/Cypher-only, NOT a PMCC signal

Filed 2026-05-02. (Was DONE 2026-05-09 — verify and remove if confirmed done.)

## ⏸ DEFERRED — Phase E: PWA + web push subscription flow

Broken out 2026-05-09 from the HITL approval flow. Web push subscription flow
deferred — not currently in scope.

## ⏸ DEFERRED — Market Cypher: add bear-bias backup if Blood Diamond too rare

Originally P2 — 2026-04-30. Deferred 2026-05-09 with the Cypher disable on
`coinbase_spot`.

## ⏸ DEFERRED — Lord Otter Phase 1.5 (equity-aware sizing + real stops)

Originally P1 — 2026-04-30. Deferred 2026-05-09 with the Otter disable on
`coinbase_spot`. Preserved for potential BitUnix Futures revival.

## P1 — Fidelity broker: read-only + analysis on Azure VM

DEFERRED — 2026-05-03. Was SCOPE-NARROWED 2026-04-30.

## P2 — 5 PMCC scan tests failing on liquidity gate

Filed 2026-04-30. Test cleanup.

## P3 — Polymarket: add `division` column to `polymarket_round_trips`

(Cross-referenced from Priority 2 — also listed there.)

## P3 — Fidelity options ticket flow (deferred autonomous execution)

Filed 2026-04-30. Long-term ticket-flow design.

## P3 — Differentiate "expected" vs "real" `broker_fallback_to_paper` audit rows

Filed 2026-05-01. Audit-row cleanup.

## ✅ P2 RESOLVED (2026-06-09) — Robinhood session auth dead since ~2026-05-29; PMCC/IRA/joint reading $0 (filed 2026-06-08 via Thread B investigation)

**Symptom:** Robinhood session pickle returning 401 Unauthorized for
PMCC, IRA, and joint account reads. Affected division dashboards
show $0 equity — masking actual positions and P&L. Discovered
2026-06-08 during Thread B investigation of the original "Robinhood
pickle reset" concern. Was NOT a system restart — service has been
stable since 2026-06-02 deploy (MainPID 2043009, NRestarts=0).

**Impact:**
- PMCC / IRA / joint dashboards have been blind for ~10 days.
- No capital at risk (paper-mode exec; reads-only side affected).
- Dashboard observability for those divisions cannot be trusted
  until session re-auth.

**Fix:** operator interactive re-login to regenerate Robinhood
session pickle. Requires MFA — cannot be agent-resolved. ~5 min
operator action.

**✅ RESOLVED 2026-06-09 ~14:49 UTC.** Operator-supervised path-(b) re-login
(backup + clear + fresh `rs.login(store_session=True)`); one device push
approved → fresh pickle (mtime 2026-06-09 14:45Z, was 2026-05-29 01:59Z).
`sudo systemctl restart trading-corp` at 14:48:58Z (MainPID 2397472 →
2427161) loaded it: RobinhoodBroker logged in (jrsumner@yahoo.com), 3
accounts bound (individual/ira_traditional/joint), no RH 401, no
device-approval hang; PMCC reading 11 real legs; PMCC/IRA/joint dashboards
confirmed real equity (operator C3). Method + sequence in
`runbooks/deploy_log.md` 2026-06-09 follow-up. Session: 2026-06-09
operator-supervised P2 re-login.

**Reference:** Thread B investigation commit a78eff7. See also
"## P3 — Fidelity startup login flakiness on `trading-corp` restart"
elsewhere in this file (related class of broker session-cache
fragility).

**Not gating:** any active development. PMCC / IRA / joint are
read-only divisions in paper-mode.

## P3 — Robinhood IRA drilldown: not a LEAP / PMCC strategy

Filed 2026-05-03. UX clarification.

## P3 — Robinhood Agentic Trading: revisit integration (DEFERRED 2026-06-08)

Robinhood launched Agentic Trading (beta) 2026-05-27 via MCP server
at `agent.robinhood.com/mcp/trading`. Per planning report
`reports/2026-06-08_robinhood_agentic_evaluation.md`: deferred
formal integration; Pattern 1 (broker adapter under existing risk
gate) is the only shape that preserves single-chokepoint invariant,
blocked today on:
- No documented non-interactive / service-account auth path
- trading_corp is not an MCP client today (no MCP client library)

Revisit triggers (any one is sufficient to re-evaluate):
- (a) Documented programmatic / service-account auth path lands
- (b) GA out of beta (currently 2 weeks old)
- (c) Options/crypto trading-execution support, including
  instrument-chain discovery primitives (get_option_instruments
  or equivalent) — watchlist-only options surface already exists
  and must NOT false-trigger a revisit
- (d) Published rate limits / SLA
- (e) Observed beta stability over 3+ months
- (f) Operator capacity for new-division build
- (g) Trailing-stop order type or equivalent ratcheting
  primitive (soft — eliminates adapter friction for
  trading_corp stop-management, e.g. Bitunix advanced-SL;
  ratcheting otherwise achievable via cancel+replace, so not
  strictly required for initial Pattern 1)

The most load-bearing trigger is (a) — without service-account auth,
Pattern 1 is infeasible regardless of other improvements.

Pattern 3 (operator-driven manual exploration via Claude Desktop)
remains available as a low-cost surface-familiarity option; not
filed as a BACKLOG task since it's an operator-driven action, not a
session work item.

**UPDATE 2026-06-09 — Pattern 3 exploration COMPLETED; verdict
UNCHANGED (defer).** Operator-driven manual exploration via Claude
Desktop empirically validated the Defer verdict and sharpened
triggers (c)/(g) above. The 22-tool surface confirmed equities-only
trading (no place_option_order, no instrument-chain discovery), an
interactive desktop-OAuth-only auth path (trigger (a) unmoved), and
a two-step review_equity_order -> place_equity_order write flow.
Material new finding (not in the 2026-06-08 eval): **read-vs-write
scope asymmetry** — the MCP token grants full cross-account READ
visibility (7 accounts incl. IRA / advisory-managed) despite
write-isolation to the agentic sub-account; any future Pattern-1
read-adapter requires explicit Board acknowledgment of the
data-exposure scope (CLAUDE.md §4). Captured in auto-memory
`2026-06-08-robinhood-agentic-evaluated-deferred.md`.

References:
- reports/2026-06-08_robinhood_agentic_evaluation.md (original eval)
- reports/2026-06-09_robinhood_agentic_pattern_3_exploration.md
  (Pattern 3 exploration; branch
  robinhood-agentic-pattern-3-exploration-2026-06-09, unmerged,
  pushed to origin)

## P3 — Migrate `FidelityBroker` onto a `ReadOnlyBroker` ABC

Filed 2026-05-01. Architecture cleanup. See CLAUDE.md §1 code-path-isolation
section for the ReadOnlyBroker ABC pattern.

## P3 — Cost-optimize tc-prod-vm away from Standard_D2s_v3

REVISED — 2026-05-02. Cost-optimization investigation.

## P2 — Cloudflare Tunnel with named domain

Network infrastructure.

## P3 — Authentication (Sign in with Apple)

Long-term auth roadmap item. May be partially obsoleted by Authelia work.

## P4 — Hetzner deployment

Alternative deploy target investigation.

## P4 — Research firm: minimum-coverage quorum gate for TradeConfirmation

Filed 2026-05-01. Research-firm scope item.

## P4 — Investigate: PMCC scout fired at 04:03 UTC outside the 8:30-9:25 ET scheduler window

Filed 2026-05-02. Anomaly investigation.

## P4 — Logging: RedactingFilter mangles dict args in %-style log calls

Filed 2026-05-02. Logging cleanup.

## P5 — Mobile-responsive layout audit

Long-term UX.

## P6 — Real macro calendar fetcher

Long-term data integration.

## P7 — Crypto-friendly stock holdings display

Long-term UX.

## P8 — JSON API endpoints (`/api/v1/*`)

Long-term API surface.

---

# Items consciously excluded

- Multi-region active-active deploy — overkill for personal trading.
- Kubernetes — overkill, single VPS is right.
- Pure-native iOS app — PWA is sufficient.
- Reverse-engineering Lord Otter's signals — defeats paying for it.

---

_Last grooming pass: 2026-06-02 evening. Previous file: 8,881 lines (62 EOS
snapshots + 44+ DONE/SUPERSEDED entries archived to deploy_log + memory).
Current file: ~470 lines organized around three operator priorities
(Bitunix live-readiness, Polymarket Copy live-readiness, InfoSec) + other
open items._

_Convention: completed work moves to `runbooks/deploy_log.md` + memory entries.
This file tracks open items only. EOS snapshots and DONE entries do NOT
accumulate here._