# Bitunix silence + Robinhood pickle/restart + prod-health investigation — 2026-06-08

Read-only, operator-supervised. Operator runs all prod commands from own
terminal (VPN active, home-IP allowlist). Agent provides exact commands;
no agent prod touch. Worktree: `bitunix-silence-investigation-2026-06-08`
off `origin/main` @ `58744bb`.

## Verified baseline (local, pre-prod-query)

- **prod git/source:** prod runs deployed source `395c421` (2026-06-02
  deploy); `origin/main` has since advanced to `58744bb` via docs/audit
  commits only (no code drift). deploy_log.md line 119+.
- **Deploy state (2026-06-02 ~01:40 UTC):** `MainPID=2043009`,
  `NRestarts=0`, `ActiveEnterTimestamp=Tue 2026-06-02 01:39:50 UTC`,
  `execution_mode: paper` @ `config/strategies.yaml:1022`. All Phase 3
  live-mode primitives gated off. healthz bound 200 at 01:50:52Z (T+11m
  lazy-bind).
- **Day-2 audit (probe 2026-06-03T01:08Z, ~23.5h uptime):** HEALTHY /
  GATED-CORRECTLY. Bitunix paper distribution `total=6, wins=5, losses=1,
  open=0`; shorts firing (06-02T06:51Z `cvd_bear_flip` bear win;
  07:27Z `mc_a_blood_diamond` bear loss). db-lock retry exhaustion 8×/23.5h
  on `agents/logger.py:26` path (pre-existing, P3 filed) — NOT the
  Decision-6.2 `insert_paper_trade_record` path (that stayed silent).

## Premise corrections caught at the fork (vs the session brief)

1. **NOT "zero trades since deploy."** Day-2 audit proves 6 bitunix trades
   on 06-02/06-03 with shorts firing. The real phenomenon is a *change*
   beginning ~Day 3-4 → silence window under investigation is
   **2026-06-04 → 2026-06-08**. (Brief's A2-A6 `>= 2026-06-04` ranges are
   already correct for this.)
2. **`reports/2026-06-02_phase3_day2_audit.md` is not on main** — it lives
   only on the `phase3-day2-audit-2026-06-02` worktree branch. Read from
   there.
3. **Memory `[[2026-06-02-n2-phase3-deployed-to-prod]]` does not exist.**
   No `2026-06-02*` memory file; only phase3 memory is the older
   `trading_corp_bitunix_phase3_confluence_model.md` (2026-05-11 confluence
   model). Authoritative deploy state taken from deploy_log.md instead.
4. **A5 kind names corrected.** Brief's A5 lists kinds
   (`live_exit_order_placed`, `position_state_reconciliation`, …) that do
   not all match the deploy_log's actual emitted kinds
   (`restart_resume_executed`, `orphan_broker_position_on_restart`,
   `restart_resume_case_c_deferred`). Using the Day-2 audit's validated
   LIKE filter (strict superset) so the hard-stop check can't return a
   false zero from wrong kind names.
5. **Network (CONFIRMED 2026-06-08):** Operator is on **hotel WiFi + VPN**
   (still travelling, not home). SSH:22 → `Connection timed out` (NSG
   source-IP allowlist excludes the VPN egress; same plane that failed in
   the Day-2 audit). Pivoted to the proven **`az vm run-command`** control
   plane (`rg-shared-prod` / `tc-prod-vm`), network-independent via
   `management.azure.com`. Probes split into two calls (`probe_a1.sh` =
   conn+A1+A5; `probe_a2.sh` = A2+A2b) to stay under az's ~4KB head-truncation
   limit.

## Thread A — Bitunix zero-fires

### Round 1 (unconditional): A1 + A2 + A2b + A5  → `probe_a.sh`
- A1: confirm the zero-fire window empirically.
- A2: where in the gate stack signals are filtered (ingest / PA / HTF / other).
- A2b: robustness companion — all bitunix/confluence-tagged kinds, no kind
  whitelist, to catch a wrong-kind-name blind spot in A2.
- A5: HARD-STOP check — Phase 3 live-mode primitives must NOT fire in
  paper-mode. Any rows ⇒ stop immediately.

Round 2 (conditional on A2): A3 (HTF reject specifics) / A4 (PA validator) /
A6 (regime context). Selected after Round 1 results.

### Round 1 — call 1 results (probe_a1.sh via `az vm run-command`, 2026-06-08T21:11:38Z)

Raw output captured verbatim:
- **Connectivity:** `host=tc-prod-vm`, `db=present size_bytes=1042460672` (~1.04 GB). Server clock `2026-06-08T21:11:38Z`.
- **A1 — bitunix_futures trades/day (≥ 2026-06-02):**
  ```
  day         trades
  2026-06-02  9
  ```
  Only one row. **Last trade day = 2026-06-02 (9 trades); zero on 06-03 … 06-08.**
- **A5 — Phase 3 live-mode primitives in paper-mode (HARD-STOP):** **ZERO rows. CLEAN.**
  No gate-break; Session B activation audit holds.

**Refinement vs prior premise:** silence began **2026-06-03**, a day earlier than
the "06-04 → 06-08" window in premise correction #1. The real window is
**2026-06-03 → 2026-06-08** (~6 days). Last *working* day = 06-02.

**Anomaly (flagged, non-blocking):** Day-2 audit (06-03T01:08Z) reported bitunix
resolved `total=6`; A1 shows `9` on 06-02. Probable cause: audit counted
resolved-at-snapshot while ~3 were still settling. Note, don't chase yet.

**Call-2 window widened:** probe_a2.sh A2/A2b changed `ts ≥ 06-04` → `ts ≥ 06-02`
so the gate-stack breakdown spans the working baseline (06-02) + the transition
day (06-03), which the original ≥06-04 window excluded.

### Round 1 — call 2 results (probe_a2.sh widened ≥06-02, 2026-06-08T21:11Z)

**A2 — gate-stack event counts/day (bitunix/confluence/htf-tagged):**

| day | pa_validation_decision | htf_gate_decision | trade_plan_decision | would_have_placed |
|-----|---:|---:|---:|---:|
| 06-02 (working) | 271 | 16 | **11** | **10** |
| 06-03 | 320 | 36 | **0** | **0** |
| 06-04 | 376 | 42 | **0** | **0** |
| 06-05 | 311 | 54 | **0** | **0** |
| 06-06 | 474 | 31 | **0** | **0** |
| 06-07 | 651 | 37 | **0** | **0** |
| 06-08 | 471 | 49 | **0** | **0** |

**A2b — last-fire per kind (alive vs dead):**
- ALIVE through 06-08: `bitunix_score_decided` (3633, →21:09:05), `pa_validation_decision`
  (2874, →20:28:28), `htf_regime_snapshot` (953, →21:08:41), `htf_gate_decision`
  (265, →20:28:28), `bitunix_observer_classified` (75, →17:39:02),
  `pa_validation_redeem` (83), `pa_validation_expired` (83).
- **DEAD since 06-02T22:15:02:** `trade_plan_decision` (11 total), `would_have_placed`
  (9 total). Both share the identical last-event timestamp.
- `kalshi_llm_probability_called` (2) = unrelated kalshi noise caught by the LIKE filter.

**LOCALIZATION (high confidence):** signals arrive and flow through scoring → PA
validation → HTF gate — all alive at high volume through 06-08 21:09. The pipeline
goes dark **between the HTF gate and trade-plan formation**, precisely at
**2026-06-02T22:15:02Z**. Rules OUT "no signals arriving" and "PA/HTF stopped
running." Two live hypotheses:
- **H1** — HTF gate now rejecting ~100% of candidates (gate is *busier* on silent
  days: 36–54/day vs 16 on 06-02).
- **H2** — trade-plan formation broke at 22:15:02 (stuck latch / swallowed error /
  state flip); gate may be passing but no plan forms.
No `agent_error` rows in the bitunix-tagged set (A2 whitelist included it).

**Breakpoint context:** deploy 06-02 ~01:40 UTC; trades flowed ~20.5h post-deploy
then stopped at 22:15:02Z. NOT a deploy-instant break — something changed ~20h later.

**The 9-vs-6 anomaly resolves:** A1's 9 `would_have_placed` on 06-02 = the 9 in A2b;
Day-2 audit's "6" was the resolved-at-snapshot subset at 06-03T01:08Z. Reconciled.

## Round 2 (selected from Round 1)

Call 3 (`probe_a3.sh`): dump `htf_gate_decision` raw verdicts — 4 most recent
(silent-day) vs 3 from working 06-02 — to decide **H1 vs H2**. If silent-day
verdicts are rejections → H1 (gate over-blocking; then A6 regime context).
If they're allows with no trade_plan following → H2 (trade-plan stage broke;
pivot to trade_plan_decision content + halt/cooldown latch + broad agent_error).

### Round 2 — call 3 results (probe_a3.sh, 2026-06-08T21:1xZ)

htf_gate_decision raw payloads (capped 450c — verdict/reason tail cut off, see call 4).
All rows `score_side: sell` (strategy generating shorts throughout).

**Silent days (06-08, 4 most recent):**
- top-level `regime` = **BEAR**, `composite_score` = **−0.5**
- h1: regime=**transitional**, ema_alignment=**mixed**, structure=insufficient, adx≈**24.8–25.6**
- h4: regime=**transitional**, ema_alignment=**mixed** (one shows structure=**bull**)

**Working day (06-02, 3 rows incl. last-ever htf_gate_decision @ 22:15:01):**
- top-level `regime` = **STRONG_BEAR**, `composite_score` = **−1.0**
- h1: regime=**bear**, ema_alignment=**bear**, adx≈**75.4–75.8**
- h4: regime=**bear**, ema_alignment=**bear**

**Interpretation:** the gate's INPUTS shifted decisively at the 22:15 boundary.
06-02 = clean aligned strong downtrend (H1+H4 both bear, ADX ~75, STRONG_BEAR,
composite −1.0) → gate passed shorts → trades fired. 06-03+ = choppy/transitional
(H1+H4 mixed/transitional, ADX ~25, BEAR, composite −0.5) → gate very likely rejecting.
Last trade plan (22:15:02) formed ~1s after the last STRONG_BEAR gate decision (22:15:01);
market then degraded to transitional and trade-plan output went to zero.

Strongly favors **H1 (gate rejecting on regime)** over H2 (trade-plan break); within H1
leans **H1a (gate correctly refusing a choppy/unaligned tape)** over H1b (miscalibration).

**NOT YET CONFIRMED (call 4):** (1) gate's literal verdict/reason (payload tail capped);
(2) whether regime stayed transitional ALL window vs had STRONG_BEAR/high-ADX windows
that should have traded but didn't (= H1b bug).

**Reframes brief's A6 premise:** brief expected STRONG_BEAR + HIGH-vol (gate filtering
shorts would be wrong). Actual: BEAR/transitional + low ADX (gate filtering shorts is
plausibly correct). Operator's chart-read of "short opportunities" may be the classic
single-chart vs multi-timeframe-filter disagreement.

## Round 2 — call 4 (selected)
`probe_a4.sh`: A6 regime+composite+ADX distribution by day (decides H1a vs H1b) +
A3c gate verdict/reason (payload tail). Settles correct-behavior vs miscalibration.

### Round 2 — call 4 results (probe_a4.sh, 2026-06-08T~21:2xZ) — ROOT CAUSE FOUND; call-3 H1a lean OVERTURNED

**A3c verdict/reason tail — the blocker named explicitly:**
- **Silent (06-08T20:28:28):** `volatility_tier`="extreme", `atr_pct_d1`=4.07,
  **`size_multiplier`=0.0**, **`hard_zero_reason`="vol_tier_extreme"**,
  `permission_reason`="BEAR + H1=transitional: short full size; vol_tier=Extreme (1D ATR 4.07%)",
  d1: regime=bear/ema=bear/structure=bear/adx=43.3 (daily IS bearish).
- **Working (06-02T22:15:01):** `volatility_tier`="high", `atr_pct_d1`=2.92,
  **`size_multiplier`=1.0**, **`hard_zero_reason`=null**,
  `permission_reason`="STRONG_BEAR: shorts full size, no longs".

→ **Blocker is a VOLATILITY HARD-ZERO, not the directional/regime gate.** The
permission_reason grants "short full size"; the vol overlay then sets
`size_multiplier=0.0` because 1D ATR is in the "extreme" tier. Zero size → no order →
no trade_plan/would_have_placed. Breakpoint 06-02T22:15 = ATR crossing "high" (2.92%,
traded) → "extreme" (~4%+, zeroed).

**A6 regime-by-day — refutes the call-3 "choppy tape" read:**

| day | regime | n | avg_score | h1_adx | h4_adx |
|-----|--------|--:|----------:|-------:|-------:|
| 06-02 | STRONG_BEAR | 16 | -1.0 | 64.4 | 42.5 |
| 06-03 | STRONG_BEAR | 36 | -0.9 | 66.7 | 55.0 |
| 06-04 | STRONG_BEAR | 42 | -0.84 | 54.8 | 66.1 |
| 06-05 | STRONG_BEAR | 53 | -0.93 | 35.1 | 69.7 |
| 06-06 | BEAR 26 / STRONG_BEAR 5 | 31 | -0.5/-0.8 | ~35 | ~73 |
| 06-07 | BEAR | 37 | -0.5 | 25.3 | 66.3 |
| 06-08 | BEAR | 49 | -0.5 | 26.4 | 52.4 |

**06-03/04/05 were predominantly STRONG_BEAR** (same regime that traded 06-02) → still
ZERO trades. Call-3's "transitional" read sampled only the latest (06-08) rows. The ONLY
factor common to ALL silent days is the `vol_tier_extreme` hard-zero. **H1b confirmed; H1a
refuted.** Operator's chart-read of missed shorts on 06-03/04/05 is VINDICATED — those were
real strong-bear shorts suppressed by the vol hard-zero, not a correct chop stand-aside.

**REVISED ROOT CAUSE:** continuous `vol_tier_extreme` hard-zero (size_multiplier→0.0) since
06-02T22:15, because 1D ATR has stayed in the "extreme" tier (~4%+) for ~6 days. Mechanically
working as coded. Open question for the verdict: is the "extreme" threshold correctly
calibrated for BTC, a deploy regression, or a deliberate risk control? → grounding threshold
value + intent + change-history in local code.

### Round 2 — code grounding (verified file:line by primary read; agent corroborated)

`trading_corp/agents/strategies/bitunix_htf_regime.py` (worktree==prod for this file):
- **Classifier `_atr_pct_to_tier()` (725-737):** docstring "Thresholds are upper bounds
  for each tier." Tests only `< low(0.5)`, `< normal(1.5)`, `< high(3.0)`, else → **Extreme**.
  The `"extreme"` key is **never read.** Effective high→Extreme boundary = **3.0%**, not 5.0%.
- **Defaults (239-241) + `config/strategies.yaml:1268-1272`:** both
  `{low:0.5, normal:1.5, high:3.0, extreme:5.0}` (YAML==defaults; no real override).
- **Hard-zero (990-1001):** `if volatility_tier == Extreme → size_multiplier=0.0,
  hard_zero_reason="vol_tier_extreme"`. Observer under `htf_gate.mode=enforce`
  (strategies.yaml:1289) `return`s on size≤0 (`bitunix_futures_observer.py:1410-1416`).
- **Intent (module docstring 80-88):** extreme-vol hard-zero is a DELIBERATE override
  ("skip until normalized"). Standing aside is by design; the THRESHOLD value is the question.
- **Git history:** introduced commit `9e1b527` (2026-05-14, Jack Sumner); YAML `98d8b8b`
  (2026-05-14). **Unchanged since; NOT touched by Phase-3 deploy** (06-01/02 commits made
  zero changes to this file / the vol_tier block). **Pre-existing latent condition surfaced
  by a market-vol change (1D ATR 2.92%→~4% across 06-02/03), not a deploy regression. No
  rollback indicated.**

## THREAD A — VERDICT (COMPLETE)

- **Confirmed window:** 9 trades 06-02; **zero 06-03→06-08** (~6 days).
- **Where filtered:** `vol_tier_extreme` hard-zero (`size_multiplier=0.0`) between HTF gate
  and trade-plan. NOT ingest, NOT PA, NOT the directional/regime gate (grants "short full size").
- **A5 activation check:** CLEAN — no Phase-3 live-mode primitives in paper-mode.
- **A6 regime context:** 06-03/04/05 were STRONG_BEAR (tradeable) yet suppressed purely by
  the vol hard-zero. Operator's chart-read of missed shorts VINDICATED.

**Honest verdict — AMBIGUOUS, resolves to ONE design-intent decision:**
Mechanism certain. Bug vs correct-behavior hinges on intent:
- Classifier is internally consistent with its "upper bounds" docstring → Extreme=≥3.0% is
  what it's coded to do (not a strict logic error).
- BUT `config extreme:5.0` is dead/misleading; a BTC strategy that fully stands aside
  whenever 1D ATR ≥3% is dormant a large fraction of the time. The 5.0 value + domain sense
  suggest the intended cutoff was likely higher than the effective 3%.
→ **Operator (author) decides:** was ≥3% ATR meant to disable the strategy, or was 5% the
intended extreme cutoff? If 5%, this is a latent threshold bug.

**Impact:** (1) paper observation window 06-02→09 CONTAMINATED (dormant 6/7 days — cannot
judge live-readiness); (2) latent suppressor for any future live flip.
**Constraint:** threshold change = strategy-param change → Backtester approval gate
(CLAUDE.md §4). Read-only session — NO code change made.

**DISPOSITION (operator, 2026-06-08):** Accepted as **likely bug** (config-vs-code drift —
`extreme:5.0` unused, effective 3% cutoff; 3% is normal BTC vol). Filed **P1** to BACKLOG →
origin/main `81e6169` (under "# Priority 1 — Bitunix Futures path to live trading"); paper
window 06-02→09 marked **INVALIDATED**. Memory `2026-06-08-bitunix-volatility-classifier-bug`
written + MEMORY.md indexed. Investigation branch pushed to origin (`9e9053b`). Filing MERGED;
**fix UNSTARTED** (Backtester-gated, CLAUDE.md §4). **THREAD A CLOSED.**

## Thread B — Robinhood pickle / unplanned restart (IN PROGRESS 2026-06-08)

**Scope (reframed per operator):** Thread A established the Bitunix silence was the vol-tier
classifier bug — NOT a restart or market. So Thread B is no longer "restart as cause of
silence"; it is **"was there an unplanned restart the operator should know about, and is the
Robinhood pickle state healthy?"**

**Hard-stop:** `NRestarts>0` with anomalous exit codes during an expected-stability window →
surface for investigation before any other work.

- Round 1 — call **B1** (`probe_b1.sh`): service-state gate. `systemctl show trading-corp`
  (MainPID/NRestarts/ActiveEnter/ExecMainStart/Result) + healthz. Expected baseline:
  MainPID=2043009, NRestarts=0, ActiveEnter Tue 2026-06-02 01:39:50 UTC.
- Round 2 (conditional): **B2** restart forensics (if NRestarts>0 / MainPID changed) OR
  **B3** pickle state + **B4** healthz history during 06-03→08 (if B1 clean).

### Round 1 — call B1 results (probe_b1.sh, 2026-06-09T00:33:07Z) — HARD-STOP CLEAN, no restart

`whoami=root` (az run-command runs as root → journalctl unrestricted in B2).

| field | value | baseline | verdict |
|---|---|---|---|
| MainPID | 2043009 | 2043009 | ✅ unchanged |
| NRestarts | 0 | 0 | ✅ |
| ExecMainStartTimestamp | Tue 2026-06-02 01:39:50 UTC | 01:39:50 | ✅ |
| ActiveEnterTimestamp | Tue 2026-06-02 01:39:50 UTC | 01:39:50 | ✅ |
| ActiveState / SubState | active / running | — | ✅ |
| Result / ExecMainStatus | success / 0 | — | ✅ |
| healthz | 200 | 200 | ✅ |

**NO service restart since the 2026-06-02 deploy.** Continuous uptime ~6d23h (06-02 01:39:50Z
→ now 06-09 00:33:07Z). Hard-stop NOT triggered. **Rules out branch (a)** of the operator's
pickle concern (full service restart). If the Robinhood pickle reset, it must be branch **(b)**:
a normal in-process re-auth/rotation WITHOUT a restart — confirm via pickle mtime in B3.

## Round 2 — call B2 (selected; B1 clean)
`probe_b2.sh`: B3 Robinhood pickle file mtime/size + 72h pickle/auth log activity (confirm
in-process rotation) + B4-lite service-health anomaly scan since deploy (Stopped/Failed/
Traceback markers — expect none given NRestarts=0).

### Round 2 — call B2 result (probe_b2.sh, 2026-06-09T00:33Z) — PARTIAL (az head-truncated; probe bug)

**Probe defect:** combined B3+B4 in one call; B4-lite grep `-iE "...Failed..."` matched routine
app-level "...fetch failed" WARNINGs (not systemd lifecycle states) → flooded output → az ~4KB
head-truncation dropped the B3 (pickle) section. Re-running B3 isolated, pickle info printed LAST.

**B4-lite (surviving tail):** NO systemd lifecycle markers (Started/Stopped/Traceback/
main-process-exited/OOM/Killed) visible. With B1 (NRestarts=0, Result=success, ~7d uptime),
**no restart/crash — confirmed.** B4 effectively satisfied.

**Incidental (OUT OF SCOPE — noted, NOT investigated):** recurring non-fatal external-feed
WARNINGs ~every 10 min, none Bitunix/Phase-3 related:
- `kalshi_copy_trader: apify open_positions … HTTP 403 — bad/missing APIFY_API_TOKEN`
- `odds_api: get_games(...) … 401 Unauthorized` — **API key exposed in plaintext log URL**
  (RedactingFilter not catching query-string `apiKey=`)
- `polymarket_copy_trader: fetch_activity … HTTP timeout`; `PolymarketBroker.get_market_resolution failed`
Operator decides whether to file (candidate BACKLOG items; not actioned this session).

## Round 2 (cont.) — call B3 (pickle, re-run isolated)
`probe_b3.sh`: robinhood journal lines (72h, bounded) + pickle file mtime/size (printed LAST so
it survives tail-truncation).

### Round 2 — call B3 results (probe_b3.sh, 2026-06-09T00:3xZ) — pickle UNHEALTHY (auth expired, not rotated)

**B3b pickle files:**
- Active: `/home/azureuser/.tokens/robinhood.pickle` — 1396 b, **mtime 2026-05-29 01:59:04 UTC**
  (~11 days old; robin_stocks default token path). **NOT recently rotated.**
- Stale leftover: `/home/azureuser/robinhood.pickle` — **0 b**, 2026-04-30 (empty; ignore).
- None in `data/`.

**B3a robinhood journal (72h):** burst of **`401 Client Error: Unauthorized`** across ALL RH
endpoints (positions, options/positions, portfolios, nummus/holdings) for 3 accounts
(461391328 / 934310442 / 116637293063) at **2026-06-09 00:09:31–00:09:39 UTC** (~24 min pre-B1).

**Interpretation — reframes the "pickle reset":** pickle did NOT rotate (stale since 05-29);
RH returns 401 across the board → session token **expired/invalid, re-auth FAILING** (not
silently rotating). Neither branch (a) restart nor (b) clean rotation — third state: **RH auth
broken.** The "reset" the operator saw is most likely RH divisions falling back to paper ($0
equity) on the dashboard via `broker_fallback_to_paper`, triggered by the 401 read failures.

**Severity:** prod registers robinhood as **paper-exec** (deploy_log 2026-06-02) → **no
live-capital risk**; impact is PMCC/IRA/joint visibility loss (reads fail → $0 fallback). NOT a
hard-stop. Unrelated to the Bitunix silence (Thread A).

**Unknown (not probed — scope):** when the 401s began / whether continuous since the 05-29 expiry
or a recent flip. tail-12 showed only the 00:09 burst.

## THREAD B — VERDICT (COMPLETE)
- **Unplanned restart?** NO. NRestarts=0, MainPID=2043009, ExecMainStart 2026-06-02 01:39:50 UTC,
  ~7d continuous uptime, healthz=200 (B1).
- **Robinhood pickle healthy?** NO. Token stale since 2026-05-29; RH API 401 as of 06-09 00:09 →
  auth expired/failing, not rotating. Likely source of the operator's "pickle reset." Paper-exec
  → no live-capital risk.
- **Incidental (out of scope, B2):** apify 403 / odds_api 401 (key in plaintext logs) / polymarket
  timeouts — external-feed WARNINGs, noted not investigated.

**DISPOSITION (operator, 2026-06-08):** RH-auth finding FILED as **P2** → origin/main `b2259a0`
(under "# Other Open Items", Robinhood cluster); memory
`2026-06-08-robinhood-session-auth-dead-since-2026-05-29` written + indexed (filing MERGED, fix
UNSTARTED — operator MFA re-login). Date-the-401s probe SKIPPED (operator: "since ~05-29 +
re-login" is sufficient). **Proceeding to Thread C.** THREAD B CLOSED.

## Thread C — Reconciler mismatch (IN PROGRESS 2026-06-08)

Three trades with recorded-vs-sim R deltas (from the 2026-06-04 dashboard):
- `c8f25d17` — 2026-05-27, delta **−0.4176 R**
- `ac5f9c59` — 2026-06-02, delta **+0.4366 R**
- `c2eb7cda` — 2026-06-02, delta **+1.1250 R**

Per Thread A, Bitunix went dormant 06-02 22:15 (vol-classifier bug) → these 3 are pre-bug /
at the boundary; the recorded-vs-sim deltas are independent of the vol-classifier issue.

Plan: (1) pull the 3 trades from `paper_trade_record` + `audit_event` lifecycle; (2) walk recorded
fills vs `sim_filled_legs`; (3) identify per-trade delta cause; (4) chronic-variance vs recent
regression; (5) recommend disposition (P3 file / known-variance skip / escalate). Read-only.

- Round 1 — call **C1** (`probe_c1.sh`): schema discovery — `PRAGMA table_info(paper_trade_record)`
  + id/sim/R/leg/extra column scan — to build the row + lifecycle pulls precisely.

**STATUS: Thread C opened. Call C1 (schema) prepared. Awaiting operator run.**
