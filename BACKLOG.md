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

## Observation window — active

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

**Priority: P1.** Structural blocker for any Bitunix live-flip decision.

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

## P1 — Polymarket copy-trader SELL-pairing investigation (REFRAMED 2026-06-02)

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

## P3 — Fidelity startup login flakiness on `trading-corp` restart

ANOMALY, RECURRING. Investigate root cause; current workaround is retry.

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

## P2 — Robinhood session auth dead since ~2026-05-29; PMCC/IRA/joint reading $0 (filed 2026-06-08 via Thread B investigation)

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
- (c) Options or crypto support lands
- (d) Published rate limits / SLA
- (e) Observed beta stability over 3+ months
- (f) Operator capacity for new-division build

The most load-bearing trigger is (a) — without service-account auth,
Pattern 1 is infeasible regardless of other improvements.

Pattern 3 (operator-driven manual exploration via Claude Desktop)
remains available as a low-cost surface-familiarity option; not
filed as a BACKLOG task since it's an operator-driven action, not a
session work item.

Reference: reports/2026-06-08_robinhood_agentic_evaluation.md.

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