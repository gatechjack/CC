# Stage 1 BitUnix Live-Engine — Architectural Review

**Date:** 2026-05-30 · **Type:** read-only architectural review (no code/config/deploy changes) · **Status:** for operator iteration before N+2 Phase 3 implementation.

**Companion docs:** `runbooks/2026-05-29_bitunix_live_readiness_audit.md`, `runbooks/2026-05-29_bitunix_live_reuse_audit.md`, `reports/2026-05-29_bitunix_live_entry_path_diagnostic.md`, and the Phase 1a/1b sub-diagnostics (branch-stranded — see Finding #1a).

**Citation convention.** Inline tags make source-class visible at every reference:
- `file:line` — on `main` HEAD `1926eb9` (or prod where explicitly stated).
- `phase1a.md@33da534` / `phase1b.md@e1d38f8` — Phase 1a/1b reports, **branch-stranded on `bitunix-live-exit-path-2026-05-29`** (NOT on `main`; reachable only via `git show <sha>:reports/...`).
- `commit:<sha>` — git history evidence.
- `[memory: <name>.md]` — second-hand source (claims about code from memory entries; treated as derived, not primary).
- `[prod-probe: <date>]` — read directly from production VM.

---

## Executive Summary

Operator instinct that triggered this review (3–4 inherited-memory premise corrections per session) is **validated**: the corrections cluster around a single structural drift class — *state forks between where work was committed and where downstream consumers actually read.* Same shape at three loci, with one immediate-remediation candidate.

1. **Risk-tier branch (`bitunix-risk-tier-pre-live`, HEAD `2a3d20c`) deployed to prod via sed at 2026-05-30 03:57:25 UTC, branch UNMERGED to main.** Prod's `observer.py` SHA256 = `9a8fe9cd…`; main's = `97dc6368…` [prod-probe: 2026-05-30]. Next clean redeploy from main **silently reverts** PREMIUM 0.015/25× → 0.04/8× and STANDARD 0.0075/25× → 0.02/5×. This is the load-bearing immediate remediation candidate — either merge the branch to main OR add a tripwire on the sed lines OR document overlay in `deploy_log` such that future deploys can't silently regress.
2. **Stage 1 readiness verdict:** 7 of 13 readiness-audit items now shipped on `main` (broker-write, kill-switch primitives, HITL gate, security C-1, partial #3 paper-resume only); 6 remain (full broker-truth restart-resume, post-trade reconciliation, cost accrual, REST resilience, operational alerts, runbooks). N+2 Phase 3 scope B closes 4 of these 6. **Going live is not config-flip yet** — Phase 3 must land, prod must redeploy, runbooks must be written.
3. **N+2 Phase 3 scope verdict:** Phase 1b's scope (B) at ~940 LOC + ~620 test LOC HOLDS with one addition surfaced by this review (Finding #7) — `venue_order_id` on `FillEvent` and the FillEvent `fee` field plumbing are LOAD-BEARING for Path C and #5 Layer 1 and should be FIRST commits, not bundled mid-stack. No structural blockers found.
4. **Memory-vs-reality drift root cause** (Finding #4): not "memories are wrong" — it's "session N+k inherits memory written DURING session N+k-1 (when the work was still in flight) without a post-completion verification pass." Recommended discipline addition: EOS memory-verification gate (re-read claimed code surface against memory text before close-out).

---

## Finding #1 — Drift class: state forks across canonical vs non-canonical surfaces

**Unifying principle.** "Committed" must mean "reachable from the canonical surface that downstream consumers actually use." Docs canonical = main (sessions browse `reports/` paths on main). Code canonical = prod (live config flips run prod's loaded constants). Config canonical = prod's load path (sizing math reads the running constants). Anything else is *committed-somewhere-but-not-canonically-reachable* — the audit-success-is-confirmed-delivery principle applied to repository state.

Three loci of the same pattern surfaced this review. The unifying language matters because the loci will look unrelated at a glance — they share a common shape only if you name the class.

### 1a. Phase 1a/1b diagnostic reports stranded on unmerged branch (severity: medium — diagnostic/audit-trail)

**Evidence.**
- Files exist as git objects on `origin/bitunix-live-exit-path-2026-05-29` (`commit:33da534` added `reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1a.md` 353 lines; `commit:e1d38f8` added `reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1b.md` 378 lines). Branch is pushed to origin but **not merged** to main.
- This review's plan referenced these files at canonical paths; Glob on main returned `No files found`; `git show <sha>:<path>` recovered them as primary source.
- Memory entries `project_bitunix_live_exit_path_phase1a.md` and `project_bitunix_live_exit_path_phase1b.md` reference these paths as authoritative without flagging branch dependency [memory].
- The N+2 Phase 1b report itself cites Phase 1a at the canonical-looking path inside the report body (`phase1b.md@e1d38f8`, line 5: "Companion: `reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1a.md`") — Phase 1b also doesn't flag that the companion is branch-stranded.
- This review's own prompt cited both reports as if accessible on main — the drift is **self-perpetuating** until interrupted.

**Implication.** Any fresh session doing `Read`/`Glob` on those paths from main fails. The diagnostic content (which captured ~5 hours of Opus reasoning across 731 lines) is invisible from the canonical surface most sessions read.

**Remediation candidate.** Either:
- (i) Merge the exit-path branch's docs-only commits (`33da534`, `4a8b440`, `e1d38f8`, `2f3c4ee`) to main as documentation-only landing, separate from N+2 Phase 3 code that comes later. Cheap, restores canonical reachability for the audit trail.
- (ii) Leave on branch; require all future memory + session-report citations to use the tag convention `[on branch X]` so downstream sessions know to checkout.

**No operator action urgency** — files are recoverable via `git show`; loss is diagnostic productivity, not operational risk.

### 1b. Prod-vs-main code divergence (severity: medium — known/intentional, but verify mental-model alignment)

**Evidence.**
- Prod runs `4985bbe` per `[memory: project_stage1_on_main_merge_session_2026_05_30.md]` and `BACKLOG.md` § "NOT yet deployed to prod".
- Main HEAD is `1926eb9` (and `d967706` for EOS docs); all six Session-29 source branches merged 2026-05-30 [git log main].
- Commits prod is missing (top of stack):
  - `2a3d20c` docs(backlog): bitunix paper-sizing → DEPLOYED + redeploy-revert risk note
  - `32a9f12` docs(deploy_log): bitunix paper-mode tier sizing sed-deploy 03:57 UTC
  - `847aad7` docs(backlog): note bitunix paper-sizing alignment branch (pre-live)
  - `41ee5e6` config(bitunix): align paper-mode tier sizing/leverage with intended live values
  - `1926eb9` docs: merge-session 2026-05-30 EOS — deploy_log + BACKLOG status update
  - `0200eed` Merge branch 'bitunix-live-entry-path-2026-05-29' into main (Stage-1 N+1: execution_mode, HITL, _record_placement_outcome, StrategyState.from_persistence)
  - `ecfc677` Merge branch 'bitunix-orderpath-safety-2026-05-29' into main (Order-path safety: mode-mismatch consumer + flatten_division)
  - `4a15c72` Merge branch 'bitunix-live-engine-stage1-broker-write' into main (broker-write: place_order + observe_fill + kill-switch primitives)
  - `cf925e3`/`8e4d877`/`d3cd655` C-1 rotation merges

**Implication.** "Stage 1 shipped" is true at the git-history level. Prod runs **pre-Stage-1** code:
- Prod's `BitunixBroker.place_order` raises `NotImplementedError` (per readiness audit § TL;DR + § 1).
- Prod has NO `_record_placement_outcome` canonical helper; the two `would_have_placed` sites are still inline-duplicated.
- Prod has NO `execution_mode` YAML field handling (defaults are still hardcoded).
- Prod has NO `StrategyState.from_persistence` — halt state does NOT survive restart.
- Prod has NO mode-mismatch consumer or `flatten_division` async machinery.
- Prod has NO HITL gate for first N=10 live orders.
- Prod has the OLD `BitunixPositionModeMismatch` class state (which is moot until broker-write is also deployed).

So at the operational level, prod is "Stage-0 BitUnix" — read-only, fails-closed-on-write. A "config flip to live" today would crash on first order. The 6-commit merge cluster, the 5 paper-sizing commits, the 3 C-1 merges all need to land on prod via deploy before any of this matters.

**Operator audit (in-scope per plan).**
- What's the operator's mental model of "Stage 1 shipped"? Likely "the code is on main; the deploy is a separate operational step." Confirmed accurate.
- Pre-deploy gap audit: per CLAUDE.md § 1 "Before any deploy-adjacent task, verify prod state" + the deploy-import-graph gate `[memory: feedback_deploy_import_graph_audit.md]`. The transitive-import closure on the new stack (broker-write + safety + entry-path + risk-tier) is non-trivial — bringing 11 commits worth of new modules + new imports needs an explicit pre-deploy ls-check on prod for the transferred files.
- The fact that this finding had to be assembled FROM memory + git log + BACKLOG (no single canonical "deploy-readiness checklist" file) is itself an artifact of the no-canonical-source pattern.

**No remediation urgency** beyond "schedule the deploy and run the pre-deploy gate properly when you do."

### 1c. Risk-tier branch deployed-but-not-merged (severity: **HIGH** — silent regression risk on next deploy)

**Evidence.**
- Branch `bitunix-risk-tier-pre-live` (HEAD `2a3d20c` on origin) DEPLOYED to prod via direct sed at 2026-05-30 03:57:25 UTC. Branch NOT merged to main.
- Prod observer.py SHA256 = `9a8fe9cd89db7470b2e9e35747c30db450170747e01dcfa3be15757ade0106f0` [prod-probe: 2026-05-30].
- Main observer.py SHA256 = `97dc6368917db81865cf6e4a0e97a490a8155b664124a9adc4ef1146e0dc68b4` (local).
- **Hashes differ. Prod and main are out-of-sync at the observer file level.**

**Verification preference order executed (per plan):**
- (a) Admin endpoint exposing loaded constants — none available. healthz returns only `{"status":"ok","mode":"PAPER"}` [prod-probe].
- (b) `/proc/<PID>/...` introspection — process start time confirmed `Sat 2026-05-30 03:57:25 UTC` for PID 1762864; cwd = `/home/azureuser/trading_corp`; observer.py mtime `2026-05-30 03:52:51 UTC` (5m before process start). Constants are module-level Python — loaded at import-time, cached in memory thereafter. **Loaded-process state inferred** to match file-on-disk (process imported AFTER the sed).
- (c) File-on-disk read at observer.py:190-205 confirmed:
  ```python
  EFFECTIVE_RISK_PER_TRADE_PCT = 0.005   # untouched (0.5% cap)
  DAILY_RISK_KILL_PCT = 0.03             # untouched (3% UTC-day kill)
  TIER_SIZING: dict[str, dict[str, float]] = {
      "PREMIUM":  {"size_pct": 0.015, "leverage": 25.0},
      "STANDARD": {"size_pct": 0.0075, "leverage": 25.0},
      "WEAK":     {"size_pct": 0.01, "leverage": 2.0},
      "COUNTER":  {"size_pct": 0.005, "leverage": 2.0},
  }
  ```
- (d) Fresh-venv-import on prod returned identical values [prod-probe].
- (e) YAML doc-mirror also showed new values; per BACKLOG note (`git show 2a3d20c:BACKLOG.md`), the YAML is doc-mirror only and **the load-bearing source is the Python constant**.

**Discrepancy between file-on-disk and in-memory:** none detected. Process started AT the post-sed mtime. Verification chain is structurally weak in the strict sense (no live-process introspection of Python module dict — only fresh subprocess import + file read), but constants are module-level + uncached-from-disk-after-import + Python caches modules. **High confidence the running process has the new values.** This methodology gap is itself a Finding #8 implicit-assumption item.

**Critical drift signal — quoted from `commit:2a3d20c` BACKLOG entry:**
> Merge: prod state currently matches NO single main commit (4985bbe base + 2 sed lines from 847aad7). Merge of this branch into main remains operator's call. **A future redeploy of main without re-sed of these specific lines would silently revert the sizing** unless the merge has landed.

**Implication.** When the operator next does:
- `git pull && systemctl restart trading-corp` (clean redeploy) — file reverts, restart loads OLD 0.04/8× and 0.02/5× values silently. Paper sizing reverts mid-flight; live sizing on a Stage-1 deploy would size 2.67× larger than intended.
- Surgical scp-from-git of observer.py — same result.
- Re-deployment via any mechanism that copies main's observer.py to prod without re-sedding — same result.

**Immediate remediation candidates (operator decision required):**
- **(A) Merge the branch to main.** Cleanest. Prod and main converge. Future redeploys carry the values automatically. Branch has 1 code commit (`41ee5e6`) + 3 docs commits — small surface to review.
- **(B) Document the overlay + add a tripwire.** Keep branch off main if there's a reason to (e.g., wanting to be able to revert quickly via `--no-overlay` style). Add a pre-deploy script that diffs prod observer.py against main observer.py and fails the deploy if specific lines are not re-applied. Heavier; only justified if (A) is precluded.
- **(C) Document only.** Inadequate. The existing BACKLOG note documents intent; the discipline question of Finding #9 ("any deploy to prod requires the deployed change to land on main, OR an explicit prod-overlay + tripwire") explicitly says documentation alone is insufficient.

**Recommendation: (A) merge.** Friction-cheapest; risk-cheapest.

### 1d. Class extension inventory — what else has this shape?

This review didn't fully execute the extension scan, but flagged the pattern surfaces and what should be grepped before treating Finding #1 as a closed inventory:

- **Audit log dedup of "deployed but not merged" overlays.** `runbooks/deploy_log.md` is the canonical record of prod surgical deploys. Cross-reference against `git log --all` to find sed-deployed-but-unmerged surfaces. This review did NOT do the cross-reference — recommended as Finding #10 open question.
- **Tasty options.** Per `[memory: reference_session_2026_05_29_marathon_eos.md]` and BACKLOG § P2 "Reconcile committed-but-undeployed main vs prod divergence (tasty_options, iron_condor partial)" — tasty_options has the OPPOSITE drift: commit on main, NOT on prod. Same class, different direction. Already tracked in BACKLOG.
- **Kalshi_weather db.py schema** — P3 ANOMALY in BACKLOG (committed but NOT deployed). Same class.
- **Memory entries citing "shipped" without checking prod state.** The N+1, N+2 sequence repeatedly conflated "merged to main" with "live on prod." Multiple memory entries say "DEPLOYED" when they mean "merged" or vice versa. The two states are NEVER simultaneously verified by the same memory entry. Open question for Finding #10.

---

## Finding #2 — Stage 1 13-item readiness audit reconciliation

Source: `runbooks/2026-05-29_bitunix_live_readiness_audit.md` § 191-207 (the 13-item checklist). Reconciled against merged main + Phase 1b scope (B) + prod state.

| # | Item | 5/29 audit status | Current status (commit/file:line) | BACKLOG-tracked? | Drift severity |
|---|---|---|---|---|---|
| 1 | `place_order` + real fill observation | ❌ NOT BUILT (`NotImplementedError` stub) | ✅ **SHIPPED on main** — `place_order` at `brokers/bitunix.py:535` (post-`commit:4a15c72`), `_observe_fill` at `:766-793`, `get_order_detail` at `:733`, `get_history_trades` at `:749`. 32 mocked-REST tests in `tests/test_bitunix_broker_write.py`. **NOT yet on prod** (Finding #1b). | Implicit — wraps into "main→prod redeploy" P2 BACKLOG item. | medium (prod gap) |
| 2 | `cancel_order` + kill switch | ❌ Soft kill only (daily cap) | ✅ **Primitives SHIPPED** — `cancel_order` at `bitunix.py:811`, `cancel_all_orders` at `:834`, `flash_close_position` at `:844`, `close_all_position` at `:851`. `flatten_division` wiring at `data_exec.py:326-450`. Broker self-latch halt at `bitunix.py:701`. **Working kill-switch verification via dashboard/Telegram not built**; ad-hoc-CLI works. Stage-1 readiness #2's "tested on paper-then-testnet-then-$10" UNDONE. | Partial — `flatten_account` action wired to `flatten_division`; runtime kill command surface not. | medium |
| 3 | Restart-with-open-position resume from broker truth | ❌ Paper-only resume | ⚠️ **PARTIAL** — `StrategyState.from_persistence` SHIPPED for cross-process halt state at `persistence/models.py:149-180` + 17 site swaps (`commit:950517c`). Phase 1b §4 confirms broker-truth resume (cases a/b/c) is NOT built. Phase 1b's narrowed scope (B) includes cases (a)+(b); case (c) deferred to N+3. | ✅ in BACKLOG § P2 (N+2 Phase 3) for a+b; N+3 for case (c). | low (path scoped) |
| 4 | Post-trade reconciliation | ❌ Dormant reconciler | ❌ NOT BUILT. Phase 1b §2: event-driven reconciler in scope (B); 5s background poll DEFERRED to N+3 (lumibot pattern). Existing `bitunix_position_reconciler.py` reads `paper_trade_record`, not broker. | ✅ in BACKLOG § P2 (N+2 Phase 3). | medium |
| 5 | Real fee/funding capture + field on `paper_trade_record` | ❌ "not tracked in paper" string | ⚠️ **LAYER 1 SCOPED** — `FillEvent` (`persistence/models.py:71-79`) has NO `fee` field; broker-write retrieves fee at `bitunix.py:_observe_fill` then DISCARDS via `_fee` at `:598` (`phase1b.md@e1d38f8` TL;DR §2). Phase 1b §3 Layer 1 plumbing scoped (~60 LOC). Layer 2 funding deferred to N+3. | ✅ Layer 1 in BACKLOG § P2; Layer 2 + new `get_history_positions` deferred N+3. | medium |
| 6 | REST retry/backoff + stale-snapshot + stuck-order timeout | ❌ 15s timeout, no retry | ❌ NOT BUILT. Both readiness + reuse audits flag this as no-external-reuse. Phase 1b does NOT include in scope (B). | ❌ **NOT in any active BACKLOG P1/P2 item.** Flagged in readiness audit § 5 as Stage-1 MEDIUM blocker. | **medium — UNTRACKED gap (Finding #10)** |
| 7 | Operational alerts (connection/kill/halt/reconciliation → Telegram) | ❌ Lifecycle-close-only | ⚠️ **SCOPED IN PHASE 3** — Phase 1b §5 has 8 telegram methods scoped (~450 LOC): `notify_exit_order_placed/filled/rejected/partial_fill/position_closed_with_pnl/reconciliation_divergence/cost_accrual_recorded/restart_resume_executed`. Existing `bitunix_lifecycle_notifier.py` close-out only. | ✅ in BACKLOG § P2 (N+2 Phase 3) per Phase 1b §5. | low |
| 8 | Low-equity alert + account funded | ❌ No alert; capital amount undefined | ❌ NOT BUILT. Capital amount still undefined ($10-$50 Stage-1 sizing; $10K planned per `commit:2a3d20c` BACKLOG note). | ❌ **NOT in any active BACKLOG P1/P2.** Capital decision implicit in risk-tier change but no explicit alert tracked. | **low — UNTRACKED gap (Finding #10)** |
| 9 | HITL for first N live trades | ⚠️ Recommend yes; not implemented | ✅ **SHIPPED on main** — `PendingApprovalRegistry` at `comms/pending_registry.py`; HITL gate for N=10 at observer `:2538` (`commit:6604132`); monitor-mode transition at order #11. Counter via `agent_state` key `live_orders_placed`. **Decision: BLOCKING HITL on entries, NO HITL on exits** per `phase1a.md@33da534` §8 + N+1 build. **NOT yet on prod.** | Implicit — wraps into prod redeploy. | low |
| 10 | Security C-1 rotation + H-11 verify | ❌ C-1 OPEN; H-11 unverified | ✅ C-1 bitunix rotated 2026-05-29 (`commit:14b5ed4`); apify rotated 2026-05-29 (`commit:858b2b3`); tastytrade verified (`commit:4c0c74b`). C-1 progress 5/13+. H-11 webhook equity fallback verification SEPARATE; status unclear. | ⚠️ Tastytrade refresh-token P1 elevation `[memory: reference_tastytrade_refresh_token_no_self_rotation.md]` (2026-06-12 ceiling). H-11 verify NOT tracked separately. | **low — H-11 UNTRACKED gap (Finding #10)** |
| 11 | Panic-halt + credential-compromise runbooks | ❌ None written | ❌ NOT WRITTEN. Readiness audit § 10 listed four runbooks needed (panic halt; buggy deploy rollback; discrepancy dispute; credential compromise). Tastytrade rotation runbook exists as template `[memory: feedback_tastytrade_rotation_runbook.md]`. | ❌ **NOT in any active BACKLOG P1/P2.** | **medium — UNTRACKED gap (Finding #10)** |
| 12 | md5-diff full bitunix prod surface vs git pre-flip | ⚠️ Not yet done | ❌ NOT DONE. Deploy-import-graph-audit discipline `[memory: feedback_deploy_import_graph_audit.md]` says this is REQUIRED pre-deploy. Stage-1 prod-deploy gate. | ❌ Implicit prerequisite, not tracked as item. | **medium — UNTRACKED gap (Finding #10)** |
| 13 | Confirm risk caps on real equity + wire flatten action | ⚠️ Caps depend on real broker equity; `flatten_account` has no broker code | ⚠️ **PARTIAL** — risk-tier sizing deployed to prod (`commit:2a3d20c`, see Finding #1c); `flatten_account` → `flatten_division` wired in safety branch (`commit:5fbf762`, merged via `commit:ecfc677`); BACKLOG note documents 0.1125% effective-risk-at-floor under cap. **Caps not yet validated against real broker `snapshot()` equity** (prod still PaperBroker due to fallback). | ✅ DEPLOYED on prod (paper sizing); live-equity verification deferred to first-deploy. | low |

**Score:** 7 shipped (#1, #2, #9, #10, #13 partial + #3 #4 partial via Phase 3 scoping; #2 + #13 partial), 4 scoped-but-not-built (in Phase 3 B), 4-5 UNTRACKED gaps (#6, #8, #11, #12, possibly H-11 verify under #10).

**Untracked gaps (Finding #10 open questions):** items #6 (REST resilience), #8 (low-equity alert + funding), #11 (panic-halt/credential-compromise runbooks), #12 (md5-diff prod surface vs git), and the H-11 portion of #10 are NOT in any current BACKLOG P1/P2 item. Each is a Stage-1 blocker per the original audit. Whether they get filed in BACKLOG, accepted-as-known-implicit, or pulled into N+2 Phase 3 scope is an operator decision.

---

## Finding #3 — Reuse audit reality vs claim

Source: `runbooks/2026-05-29_bitunix_live_reuse_audit.md` § B (lumibot patterns). Reconciled against `brokers/bitunix.py` on main post-`commit:4a15c72` (broker-write merged).

| Pattern | Reuse-audit claim | What materialized | Divergence reason | Drift type |
|---|---|---|---|---|
| `BitUnixClient` REST client (lumibot `tools/bitunix_helpers.py`, 800 lines) | **ADOPT-WITH-ATTRIBUTION** — complete REST client; ~30-40% off broker-write | ⚠️ Re-implemented FROM lumibot interface, not by literal adoption. Our `brokers/bitunix.py` (1236 lines) covers the endpoints we use; no lumibot module imported. Sign-what-you-send gotcha handled correctly (audit § B #1). Some endpoints NOT implemented (`get_history_positions`, `modify_order`, `change_margin_mode`). | **Deliberate** — operator chose interface-reimplementation over package adoption to avoid lumibot dep + attribution overhead. Stack stayed lean. | acceptable |
| `do_polling()` (lumibot `brokers/bitunix.py:544-605`) — 5s reconciliation poll engine | **ADOPT AS PATTERN** — 5s reconciliation poll; `_first_iteration` for restart-resume; diff-dispatch loop | ❌ **NOT IMPLEMENTED.** `phase1b.md@e1d38f8` TL;DR §1: "Our broker-write branch (`87dac50`) implements EVENT-DRIVEN fill observation only (`_observe_fill` called once after `place_order`), no background sweep, no diff engine." | **Reframed scope** — Phase 1b decision: event-driven primary + 60s sanity poll DEFERRED to N+3 / scope (B). Operator-approved. | acceptable (deferred-tracked) |
| `_parse_broker_order()` (lumibot `:458-540`) — partial-fill state machine | **ADOPT AS PATTERN** — `qty` vs `tradeQty` + `avgPrice`; `PARTIALLY_FILLED` status | ⚠️ Partial-fill awareness present in `_observe_fill` (returns `(status, filled_qty, avg_price, fee)`; encodes `PART_FILLED` in venue suffix `bitunix_futures:part_filled` at `bitunix.py:608`). NOT a full state machine; no follow-up reconciliation across partial fills. | **Deliberate scope** — Stage-1 is single-position-at-a-time; partial-fill follow-up is Phase 1b §5 / scope (B). | acceptable |
| `_submit_order()` + place logic (lumibot `:235-331`) | **ADOPT WITH ATTRIBUTION** — leverage cache, payload, `clientId` idempotency | ✅ Built — leverage caching, `clientId`, code-0 envelope check all present. Style follows lumibot per audit. | Direct pattern adoption. | none |
| Kill-switch primitives (lumibot `tools/bitunix_helpers.py:698-773`) | **ADOPT WITH ATTRIBUTION** — `cancel_all_orders`/`flash_close_position`/`close_all_position` native; no flatten loop needed | ✅ All three present at `bitunix.py:834/844/851`. | Direct pattern adoption. | none |
| `get_history_positions` (lumibot `tools/bitunix_helpers.py:472-504`) | **REFERENCE FOR DATA LOCATION** — carries `funding` + `fee` per closed position (Layer 1 fee source) | ❌ **NOT IMPLEMENTED.** Per Phase 1b §3, `get_history_positions` is NOT present on broker-write. Fees ride on `get_history_trades` instead; funding has no path. | **Reframed scope** — Phase 1b: Layer 1 fee plumbing via `get_history_trades` + `_observe_fill` (already retrieved); Layer 2 funding accrual DEFERRED to N+3 (depends on `get_history_positions`). | acceptable (deferred-tracked) |

**Materialized savings vs estimate.** Reuse-audit projected ~30-40% off broker-write long pole via lumibot adoption. Actual savings:
- Sign-what-you-send pre-solving: ~1 session saved (avoided error-10007 debug loop).
- Native kill-switch primitives reference: ~0.5 session saved (knowing the primitives exist + their signatures).
- Did NOT lift the lumibot client module wholesale.
- Net realized: ~1-1.5 sessions saved on a ~4-6 session budget. Audit estimate of 30-40% holds in spirit; the leverage was higher-quality-spec (interface-knowledge) rather than higher-volume-code.

**Divergence — implicit drift?** The reuse audit's `do_polling` "ADOPT AS PATTERN" recommendation was scoped-out IMPLICITLY between the reuse audit's authorship and broker-write's implementation. `phase1b.md@e1d38f8` flagged this as a "premise correction" — meaning the inheriting session expected `do_polling` to be present. **This is the same memory-vs-reality drift root cause analyzed in Finding #4.** The scope-out was operator-approved (the broker-write commit landed on operator review), but the assumption that it would be present propagated into the readiness audit and Phase 1a's TL;DR. **Treat as IMPLICIT drift, not deliberate scope-narrowing-with-traceable-decision.**

---

## Finding #4 — Cross-session premise correction inventory + root cause analysis

The reviewer-operator pattern was clear: each Phase 1 diagnostic surfaced 3–4 inherited-memory premise corrections. Inventory by session:

**Session N (order-path safety) Phase 1.** Lighter — surfaced fewer corrections because the scaffold was independent of N+1/N+2. Established the cross-process strategy-state-halt gap and the safety_notifier slot fork.

**Session N+1 (live entry-path) Phase 1.** Source: `reports/2026-05-29_bitunix_live_entry_path_diagnostic.md`:
1. **`set_agent_state`/`load_agent_state` already exist** at `persistence/db.py:444,468` (entry-path diag § Premise gaps #1). Session N memory said "no existing primitive" → wrong. Persistence scope shrank ~3× (from "build writer/reader API + 20+ sites" to "1 helper + 17 one-liners + 1 writer").
2. **`PendingApprovalRegistry` HITL primitive production-ready** at `comms/pending_registry.py` since 2026-05-03. Session prompt assumed needs-build → wrong. No new infra; only wire-in.
3. **Two parallel `would_have_placed` wire sites** (trigger-path + score-path), not one. Memory referenced `:1557-1573` as score-path wire point; actual wire is `:1475-1555`. Cosmetic but not structural.
4. **StrategyState site count drift** — memory said 20+, actual 17. Cosmetic.
5. **Observer does NOT read `auto_execute` today** — informational only. Reshapes recommendation (per-decision fresh-read).
6. **safety_notifier wiring deferred to N+1 by Session N** — known, not "drift" but session-handoff coordination.

**Session N+2 Phase 1a (`phase1a.md@33da534`).** Surfaced:
1. **6 unmerged branches, not 7** — broker-write 87dac50 is a follow-up commit on existing branch, not new branch. Cosmetic.
2. **`execution_mode` not on main** — explicit; only on entry-path branch. Reshapes merge-sequence prerequisite.
3. **One LARGE gap (Path C):** N+1 commit `e04b192` decision ("no paper_trade_record on live path") creates structural blocker for exit-path replay loop (`paper_trade_record WHERE result IS NULL` doesn't see live entries). This is Finding #5 below.

**Session N+2 Phase 1b (`phase1b.md@e1d38f8`).** Three premise corrections explicitly numbered in TL;DR:
1. **Broker-write does NOT have `do_polling`** (LARGE) — handoff prompt + next-session prompt assumed lumibot's pattern was adopted. Was NOT. Reshapes #4 to event-driven primary + light poll.
2. **FillEvent has no `fee` field; fee discarded at `place_order:598`** (SMALL, high-leverage). One-line fix unlocks #5 Layer 1 entirely.
3. **`_place_live` does NOT write paper_trade_record** (concretizing Phase 1a's Path C finding). Reverts the N+1 commit 3 decision.
4. Plus: **none of the four Session-29 feature branches stack on each other** — load-bearing for merge sequence.

**Total corrections across N+1 + N+2 Phase 1a + N+2 Phase 1b:** ~11-12 corrections. Roughly half are STRUCTURAL (drive scope/decision changes); half are COSMETIC (line numbers, branch counts).

### Root-cause analysis (the structural section)

**Pattern 1 — Memory written DURING in-flight work without post-completion verification.**
- N+1 memory was written at session close; it described the BUILD plan (scope estimates, primitive needs) more than the SHIPPED state. The "no existing primitive" claim in Session N memory was a forward-looking ESTIMATE that got compressed into a present-tense "doesn't exist" by the next session reading the memory.
- The post-implementation reality (set_agent_state existed) was never re-verified against the memory at EOS.

**Pattern 2 — Inheriting sessions trust memory more than they should.**
- Phase 1b's handoff prompt explicitly relied on Phase 1a's "we have do_polling" implicit assumption (sourced from reuse audit § B). Phase 1b had to read broker-write directly to surface the correction. Phase 1a should have read broker-write — it didn't, because the Phase 1a brief was structural (5/8 questions), not implementation-detail.
- Reuse-audit's "ADOPT AS PATTERN" recommendation was conflated with "WAS ADOPTED" in subsequent memory references.

**Pattern 3 — Memory uses past-tense for both "decided" and "shipped" interchangeably.**
- "TIER_SIZING constants changed" (in memory) — was this branch change or prod deploy? Answer: prod deploy; main never updated.
- "Branch X SHIPPED" — was this committed-to-branch, merged-to-main, or deployed-to-prod? Memory inconsistent.
- Same issue at the artifact layer (Finding #1a).

**Pattern 4 — Diagnostic reports referenced by canonical-path memory get stranded by non-merge of their branch.**
- `phase1a.md` and `phase1b.md` cited by memory as authoritative; memory consumer assumed reachable from main. Reinforces Finding #1a.

### Discipline recommendation (Finding #9.d preview)

**Post-implementation memory verification gate.** At EOS, every memory entry written this session is verified by re-reading the actual code surface it claims to describe. Discipline cost: ~5-10 minutes of grep + spot-checks. Discipline benefit: would have caught ~half the inventoried corrections at write-time, not read-time of next session.

**Tense + state-class discipline.** Memory uses one of three explicit verbs: COMMITTED (branch tagged), MERGED (on main), DEPLOYED (running on prod). Never "shipped" — too ambiguous. Generalizes the Finding #1 drift-class principle to memory writing.

---

## Finding #5 — Path C decision retrospective

**Original decision context.** `commit:e04b192` (Stage-1 N+1 commit 3 of 7, 2026-05-29 18:56 UTC):
> Live path does NOT write paper_trade_record — fill tracking happens via data_exec's `filled` audit + proposed_order.fill_price. **Live trade lifecycle (TP/SL reconciliation) lands in N+2.**

And the test file inline doc:
> "No `paper_trade_record` row written on the live path." (`tests/test_bitunix_observer_live_branch.py:17`)

**Reversal trigger.** `phase1a.md@33da534` § 9 N+1 premise validation:
> N+1 commit 3 explicitly states "No `paper_trade_record` on the live path" ... `paper_trade_replay.replay_pending_paper_trades` walks `SELECT * FROM paper_trade_record WHERE result IS NULL` ... Live positions don't HAVE a row in that table. So the existing TP/SL detection loop **never sees live positions**.
> Recommended: **Path C — Live entries write paper_trade_record with `extra.execution_mode='live'` + `extra.broker_order_id`**.

**Was the original decision wrong, or did downstream need emerge late?**

The commit message itself anticipated divergent live handling ("Live trade lifecycle (TP/SL reconciliation) lands in N+2"). That's not a blind deferral — N+1 KNEW exits would be different. But the commit author did NOT trace the specific filter `paper_trade_record WHERE result IS NULL` and ask: *"how does N+2 walk live positions if they aren't in the row set?"* That question requires running the integration in your head — N+1 was focused on the entry-path correctness boundary, not exit-path discovery.

**Verdict.** The decision was reasonable AT N+1 scope but NOT load-bearing-verified-against-N+2. Phase 1a caught the gap one session later because Phase 1a's brief was EXACTLY to trace the exit-path through the replay loop. The retrospective lesson is **structural**: when a session makes a "for now we skip X" decision, the discipline addition is to trace ONE step downstream and check whether anything downstream of "X" relies on it. Not "fully design the downstream" — just "is X-deferred load-bearing for anything in scope-N+1's downstream consumers?"

**Other "live skips X" decisions visible now that may face similar reversal.** Identified by grepping for parallel structural decisions in the live branch on N+1:

1. **`paper_trade_record.result IS NULL` filter as primary live walker.** Path C resolves the surface gap; the deeper assumption is that paper-replay's classifier logic (single-leg + multi-leg) is the right LIVE exit-event surface. Phase 1a §1 affirmed this by adopting the in-place fork. But there's an implicit assumption: **the classifier's bar-walk timing matches live ms-resolution price action.** Paper-replay walks 1m bars; live broker fills happen sub-second. The lifecycle SL transitions (BE after TP1, TP1-floor after TP2) get applied at bar boundaries in paper; live needs them applied at fill events. **Surfaces as a Finding #6 audit-lies architectural check (does "TP1 fill" semantically mean the same thing in paper-replay-classifier vs live-broker-confirmed)?**

2. **`extra` dict schema-by-convention.** N+1 stamps `intent_payload["execution_mode"] = "live"` in audit payload but not in `paper_trade_record.extra` (Path C will fix). The `extra` dict is unstructured — any reader doing `extra.get("execution_mode")` will get None for pre-Path C live entries (none exist yet) and "live" for post-Path C entries. Any reader assuming PAPER-ONLY semantics (`if extra.get("score_path"): ...`) won't break, but readers that branch on "is this live or paper" need updating. Finding #8 verification step.

3. **`auto_execute` re-read pattern.** N+1 reads YAML on every order decision (`_yaml_auto_execute_for_bitunix`); fails CLOSED. Reasonable. But the YAML for exit-path execution will also need a read — the same fail-closed pattern applies? Phase 1a §9c StrategyState halt semantics say "halt does NOT block exits; flatten DOES" — operator-approved. **No reversal risk found here; flag as audit-trail-complete.**

4. **`flatten_division` consumer is synchronous halt-only.** Safety branch's flatten waits for `get_pending_positions` verify. Stage-1 single-position scope makes this safe; multi-position would race. **Not a Stage-1 reversal; future-N+ scope.**

5. **`_observe_fill` is one-shot per place_order call.** No re-poll. If broker reports `PART_FILLED` once and then nothing, the system doesn't re-check. **Same shape as Path C reversal — for now we observe once; downstream consumers may need re-observation.** Phase 1b §2's event-driven reconciler partially closes this for the exit side (re-reconciles via `get_history_trades` after exit fill). Entry side has no re-observe. **Audit candidate for N+3 or later** — flag for Finding #10.

**Recommended action.** Audit the "live skips X" decisions surfaced above as part of N+2 Phase 3 entry-discipline, not as separate work. When Phase 3 implements the exit path, verify each decision against the implementation's downstream consumers. Cheap; reuses the same code reading already required.

---

## Finding #6 — "Audit-lies" architectural check

**Three known instances on main (background context, not to re-investigate):**
- Telegram audit-success-without-delivery: `commit:0298575` — `TelegramChannel.push()` returns `bool`; writes `telegram_notification_success` only on real 2xx+ok; retry-once fallback. `[memory: project_bitunix_telegram_lifecycle.md]`.
- Polymarket database-locks-dropped-rows: `commit:69c401a` — `PRAGMA busy_timeout=5000` + `LoggerAgent.log_event` retry(4, jittered) + JSONL fallback. Eliminated 27-in-7-days silent audit drops.
- Reconciler still-open-vs-won: `commit:06b5a9e` — Fetch at 1m granularity (live path's fetcher, not 3m DB read) + window-inclusivity fix. Real run: 17/17 match.

Shared root-cause structure: an external claim ("delivered", "logged", "filled") was treated as confirmed without verification against the external authority. Discipline label: confirm against authoritative external source, don't trust the proxy.

**Architectural-level scan for a fourth instance in Stage 1 code Phase 3 will touch:**

### 6.1 `paper_trade_record.result='win'` — paper-replay-classifier vs live-broker-confirmed

**Same column, two sources post-Path C.**
- Paper-mode: `result='win'` written by `paper_trade_replay._classify` when bar-walk shows price touched `tp` before `sl`. Source: 1m bar high/low.
- Live-mode (post-Path C, post-`_record_exit_outcome`): `result='win'` written when broker confirms exit fill via `data_exec.place(reduce_only=True)` returning a `FillEvent` for the TP order.

**The two semantics are NOT identical.** Paper "win" = "the price level was reached on a bar." Live "win" = "we placed a reduce-only order at TP and it got filled." A slip-through where price touched TP intra-bar but the live order didn't fill (no liquidity, latency) would show `result='win'` in paper-mode-projected-from-bars but NOT in live-mode-from-broker-truth. Downstream readers (dashboard, performance attribution, tax records) that aggregate `result='win'` mix the two semantics.

**Is this an audit-lie?** YES, latently — under Path C, the same column carries different semantic content depending on `extra.execution_mode`. Downstream consumers don't know they need to disambiguate.

**Recommended Phase 3 mitigation.**
- Either: add explicit `result_source: str` column on `paper_trade_record` ("paper_replay_bars" vs "live_broker_truth") — schema change requires Board approval per CLAUDE.md.
- OR: stamp `extra["result_source"]` in the canonical helper; downstream readers update to read it.
- OR: live-mode never sets `result='win'` based on paper classifier — only on confirmed-broker-fill (which Phase 1b §2 reconciler enforces).

**Operator decision** (Finding #10): which mitigation, or accept-as-known and document?

### 6.2 `_record_placement_outcome` return semantics

Return type: `None` (`bitunix_futures_observer.py:2425`). The helper does ALL the side effects (audit + log_proposed_order + log_event + paper_trade_record insert + daily_risk + decision log + telegram). No structured success/failure.

**The audit-lie risk.** If the broker confirms an order but the `paper_trade_record` INSERT fails (e.g., db lock — which is now retried, but consider a non-retryable error), what does the caller see? Nothing — `None` returned regardless. The audit row for `live_order_placed` is written before `data_exec.place()` (intent capture) — good. But the `filled` audit + paper_trade_record write happen AFTER, and a failure mode that lands the order but loses the row is opaque.

**Recommended Phase 3 mitigation.**
- Wrap the post-place writes in a try block that, on failure, writes a `live_order_placed_audit_loss` audit + retries the row write (the db-lock fix `commit:69c401a` already provides retry semantics for log_event; extend to insert_paper_trade_record).
- OR: rely on the existing audit chain (`filled` audit + log_proposed_order) as the canonical truth; treat paper_trade_record as a denormalized view that can be rebuilt from audit on demand.

**Operator decision** (Finding #10) and audit-row design decision; Phase 3 scope.

### 6.3 Phase 1b reconciler verifies broker truth — DOES IT?

Phase 1b §2's recommended event-driven reconciler:
> Calls `bitunix_broker.get_history_trades(order_id=fill_event.order_id)` to retrieve broker-truth fills.

`get_history_trades` returns `tradeList`. **What if it returns []?** Three states are indistinguishable from a 200-OK-with-empty-list response:
- (a) Order was placed but no fills yet (still pending).
- (b) Order was placed and filled, but the history endpoint hasn't caught up (eventual consistency).
- (c) Order was rejected pre-fill.

Phase 1b's reconciler doesn't distinguish. The audit-lie risk: reconciler completes with `verdict="match"` because qty/price compared sum to 0 == sum to 0 (vacuously). **A non-zero `paper_trade_record.qty` reconciling to broker-history-empty is a divergence, not a match.**

**Recommended Phase 3 mitigation.**
- Reconciler explicitly handles "empty trade history" as `verdict="missing"`, not "match."
- Combine with `get_order_detail` to distinguish (a)/(c) from (b).

**Stage 1 scope:** include this fix in Phase 3 from day 1. Cheap; the broken case is rare but high-severity (we'd think we cleanly closed a position that actually didn't close).

### 6.4 Drift-class principle applied to fill-state semantics

The Finding #1 principle ("reachable from canonical surface") applied to fill state: is "filled" the canonical broker-confirmed state, or the in-process FillEvent dataclass state? Today: FillEvent is constructed at the end of `_observe_fill`; if `_observe_fill` times out (12 polls × 1s default), the order may have filled AT the broker but the FillEvent never gets constructed with the correct state. Caller treats absence-of-FillEvent as failure.

**Mitigation.** Already implicitly handled by `get_history_trades` follow-up reconciliation (Phase 1b §2), but explicitly: design the `_observe_fill` timeout fallback to write `_observe_fill_timeout_with_broker_unknown_state` audit + halt broker self-latch + telegram operator to manually resolve. **Stage-1 must-have.**

---

## Finding #7 — N+2 Phase 3 scope validation

Phase 1b recommended **scope B** (`phase1b.md@e1d38f8` §7): ~940 LOC + ~620 test LOC, 8-11 commits. Reviewed each item.

### In-scope items (per-item confirm/expand/contract)

| Item | Phase 1b est. | Verdict | Reasoning |
|---|---|---|---|
| Path C revert (FillEvent `venue_order_id` + `_place_live` row write) | ~30 LOC | **CONFIRM + ELEVATE TO FIRST COMMIT** | This is the schema/data-flow foundation everything else builds on. Must land before #2-#7. `venue_order_id` addition to FillEvent is a 1-line dataclass change. |
| `_record_exit_outcome` canonical helper | ~400 LOC + tests | **CONFIRM** | Matches Phase 1a §1 structural decision. Revised signature (with `fill_event` param per `phase1b.md@e1d38f8` §6 check #1) is the right shape. |
| `_execute_live_exits` + event-driven reconciler | ~150 LOC + tests | **EXPAND** by ~30 LOC: add the Finding #6.3 empty-trade-list disambiguation; add the Finding #6.4 timeout-and-halt branch. | Cheap fix; high-severity-on-trigger if missing. |
| Layer 1 fee plumbing | ~60 LOC + tests | **CONFIRM** | `FillEvent.fee` + drop discard + `extra_json["fee_usd"]` + `notify_close_out` real-fee branch. Already concretely scoped. |
| Restart-resume cases (a) + (b) | ~120 LOC + tests | **CONFIRM** | Cases (a) match + (b) broker-orphan→halt. Operator-authorized deferral of case (c). |
| 8 operational alerts | ~450 LOC + tests | **CONFIRM** | All 8 methods per Phase 1b §5. Counter-aware suffix matches entry-path pattern. |

### Deferred-to-N+3 items — safety check at first-live-trades scale ($10K, 0.75%/1.5%/25× sizing)

| Item | Phase 1b deferral rationale | Stage-1 safety check |
|---|---|---|
| Layer 2 funding accrual | Funding intervals tiny on Stage-1 sizing; deferrable to recover from broker statements when Layer 2 lands. | **SAFE at $10K.** PREMIUM notional = $3750 × leverage adjustment; STANDARD = $1875. Funding rate ~0.01% per 8h interval = ~$0.20-$0.40 per position per interval. Per-trade hold ~hours → at most ~$2-$5 per trade. Untracked cost. **For tax: capture from broker statement at quarter-end retroactively.** No operational risk. |
| Restart-resume case (c) broker-closed-during-downtime | Single-position; low probability; halt-and-page is safe. | **SAFE at single-position.** Restart-during-open-position probability low. When triggered, broker self-halt latches + telegram. Operator manually closes via BitUnix UI + stamps row. Loses ~minutes of trading; no $ loss beyond uncaptured close ts. |
| 5s background poll (lumibot pattern) | Event-driven primary + 60s sanity poll | **SAFE at Stage-1.** Phase 1b's 60s sanity poll catches the divergences a 5s poll would catch ~11-12× slower. At $10K + single-position, an hour of divergence is at most a few percent of equity, and the broker self-halt limits further damage. **For Stage-3 with multiple positions: 60s is too slow** — flag for N+3 upgrade trigger. |

**All three deferrals SAFE for first live trades at planned sizing.** No item needs to be pulled into Phase 3.

### Items the architectural review surfaces as needed but not in Phase 1b's scope

**This subsection is the load-bearing output for whether scope (B) holds or needs revision** — analyzed deliberately, not as afterthought. Method per the plan: review Findings #1, #2, #5, #6, #8 against Phase 1b scope (B) and ask whether anything implies additional Phase 3 scope.

**Items surfaced:**

1. **Finding #1c remediation (risk-tier branch merge) gates the first prod-deploy of N+2.** If risk-tier is not merged before main is redeployed to prod, the sizing reverts to 0.04/8× / 0.02/5×. **Recommend: risk-tier merge as a PRE-PHASE-3 task, not part of Phase 3.** Phase 3 implementation can proceed in parallel; the merge is 1 line of code + commit-history housekeeping. NOT a Phase 3 scope addition — a Phase 3 prerequisite.

2. **Finding #6.1 `result='win'` semantic disambiguation.** Should be ADDED to Phase 3 scope as a small commit. Either an `extra["result_source"]` stamp in `_record_exit_outcome` or an explicit operator decision to accept-as-known. **Recommend: ADD ~20 LOC + tests to Phase 3 scope. Operator decision required on stamp-vs-column-vs-accept.**

3. **Finding #6.2 audit-row-loss-after-broker-confirm fallback** in `_record_placement_outcome`. Should be ADDED as an existing-helper extension. The db-lock retry already covers log_event; extending to insert_paper_trade_record is ~5 LOC + a test. **Recommend: ADD ~10 LOC + tests.**

4. **Finding #6.3 reconciler empty-trade-list disambiguation** — already folded into the "EXPAND" verdict above.

5. **Finding #6.4 `_observe_fill` timeout + halt** — recommend ADDING as a small commit to broker-write OR as part of `_execute_live_exits` design. ~30 LOC + tests.

6. **Finding #2 untracked gaps: items #6 + #11 + #12.** These are NOT Phase 3 scope (they're separate work tracks: REST resilience + runbooks + md5-diff discipline). **They DO gate the first prod-deploy of Stage 1.** Recommend: file in BACKLOG as P1 with the explicit pre-deploy gate dependency.

**Total scope (B) refinement: ~60-90 LOC + ~50 LOC tests added beyond Phase 1b's ~940 LOC + ~620 LOC.** Stays within "tight but tractable for one focused session" estimate. Scope (B) **HOLDS with these additions.**

### Items NOT to add to Phase 3

- Layer 2 funding, case (c) restart-resume, 5s background poll — Phase 1b's deferral analysis still holds at Stage-1 sizing.
- Items #6, #11, #12 from Finding #2 — separate tracks; gating first prod-deploy but not Phase 3 implementation.
- H-11 verification — separate security verification track.

---

## Finding #8 — Implicit-assumption inventory + verification steps

Before N+2 Phase 3 starts, the following assumptions should be explicitly verified. Each has a one-line verification step the operator (or a next-session agent) can execute.

| # | Assumption | Verification step | Severity if wrong |
|---|---|---|---|
| 8.1 | BitUnix testnet exists and is reachable from the prod VM. | `curl -s https://fapi-testnet.bitunix.com/api/v1/futures/account?...` with synthetic key. If unreachable, Stage-1 testing strategy must change — first live $10 IS the test. | Reshapes Stage-1 deploy plan (no testnet → harder roll-back). |
| 8.2 | `strategies.yaml execution_mode` field doesn't trigger unintended behavior in other code paths. | Grep `execution_mode` across `trading_corp/` (already done — 6 sites: main.py × 3, bitunix_futures_observer.py × 4); verify each handles default ("paper") + override ("live") + unknown→fail-closed. Confirmed clean for bitunix observer. Open: do any OTHER division agents inadvertently see this field? | Cross-division leak — would be high. |
| 8.3 | 0.5% `effective_risk_per_trade_pct` cap interacts correctly with 25× leverage in edge cases. | Compute boundary: `size_pct × leverage × stop_floor_pct = 0.015 × 25 × 0.003 = 0.1125%` (well under 0.5%). But: at SHRINKING stop floor (sub-0.3%), what's the cap? Sizing math overrides with `max_pct_for_risk_cap` at `bitunix_futures_observer.py:2008`. Verify: under SHRINKING stop_floor_pct, max_pct_for_risk_cap drops; does sizing math correctly downsize? Read `:1975-2008` + `:2108-2187` and trace; add an explicit boundary test if missing. | Sizing-overflow at edge = oversize-live order. High if missed. |
| 8.4 | `paper_trade_record.extra` dict readers don't break when Path C adds `execution_mode='live'` + `broker_order_id`. | Grep `extra.get`/`extra[` across trading_corp/. 21 files found. Verify each reader either keys on a different field OR handles the new keys gracefully. Specifically check `paper_trade_replay`, `bitunix_lifecycle_notifier`, `bitunix_position_reconciler`. | KeyError on dashboard render — medium. Strategy assumes paper — could be high. |
| 8.5 | `data_exec.place()` handles `reduce_only=True` exit-side call correctly. | Trace `data_exec.py:place` for the reduce_only path. Does it trip position-mode check (entries only)? Daily-cap check (entries only)? Leverage-set path (entries only)? Phase 1a §3 claimed yes for the broker-write branch; verify integration on main. | Could halt or refuse exits. **Stage-1 blocker if wrong.** |
| 8.6 | Risk-tier values in prod's running process match the file-on-disk (loaded-process state). | This review verified file-on-disk + fresh-import subprocess; both agree. Strict loaded-process verification not done (no admin endpoint). Process imported AFTER file-mtime → confidence high but not certain. **Add admin endpoint exposing key constants for future audits.** | Methodology gap. Low immediate risk; observability gap. |
| 8.7 | `tier_sizing` YAML block is documentation-only (no code reads it). | Grep `tier_sizing` (lowercase) in code. Only observer's `TIER_SIZING` (uppercase) Python constant is read. Already confirmed via grep. **Documented assumption.** | Low. |
| 8.8 | `flatten_division`'s `get_pending_positions` verify pattern handles broker rate-limit / 5xx / timeout. | Read `data_exec.flatten_division` (~`data_exec.py:326-450`); verify error handling for broker probe. If probe fails, does flatten succeed and write audit, or halt? Path C's safety contract assumes flatten = force-close; probe failure must not silently no-op. | High if missed under stress. |
| 8.9 | Healthz / admin endpoints don't expose secrets or order details under any code path. | Curl `/healthz` returns only `{"status":"ok","mode":"PAPER"}` [prod-probe]. No order data. Per CLAUDE.md § 1 redaction. Acceptable. | Low. |
| 8.10 | Restart-during-open-position doesn't double-place. | Phase 1b §4 cases (a)+(b)+(c). Case (a) match path verified scoped. Phase 3 implementation must include a test where the same broker order_id appears both in broker-truth and in paper_trade_record. Should resume tracking, NOT place a new entry. | High if missed. |

**8 verification steps marked "must run before Phase 3 implementation starts."** None require operator-only access; all can run in a next-session investigation. Items 8.1, 8.3, 8.5, 8.8, 8.10 are higher-severity.

---

## Finding #9 — Discipline recommendations going forward

### 9.a Repository convention — "deploy implies main"

Any deploy to prod requires the deployed change to ALSO land on main, OR an explicit prod-overlay documented in `runbooks/deploy_log.md` AND a tripwire-style check that future deploys can't silently revert it. The risk-tier branch case (Finding #1c) is the canonical example. The BACKLOG note exists; the tripwire does not.

**Suggested tripwire shape.** Pre-deploy script that:
- Reads the list of "prod-overlays" from `deploy_log.md` (a structured section).
- For each overlay (file:lines + expected post-overlay content), checks the prod file matches expected, OR fails the deploy with the diff.
- Lives in `scripts/predeploy_overlay_check.py` or similar.

### 9.b Memory/citation convention — source-class tags

Any file reference in a session report or memory entry includes a source-class tag: `[on main]`, `[on branch X]`, or `[git object only]`. Defaults to `[on main]` only when verified. Generalizes Finding #1a's inline-citation pattern beyond architectural reviews. Cost: 1-2 chars per reference. Benefit: surfaces branch-stranding at write time, not at next-session-discovery time.

### 9.c Pre-deploy check — running-config diff

One-line check: diff prod's running observer.py (and other config-bearing files) against main's. Surfaces overlays before they regress. Suggested shell:
```bash
ssh azureuser@trading.jacksumner.com "sha256sum /home/azureuser/trading_corp/trading_corp/agents/divisions/bitunix_futures_observer.py" \
  vs sha256sum on main → if mismatch, flag deploy.
```

### 9.d Post-implementation memory verification gate

At EOS, every memory entry written this session is verified by re-reading the actual code surface it claims to describe. If memory says "X exists at file:line", verify file:line. If memory says "the canonical helper returns `None`", verify return type. Discipline lesson from the 11+ corrections inventoried in Finding #4.

**Cost:** ~5-10 minutes of grep + spot-checks. **Benefit:** would have prevented ~half of the corrections caught in subsequent sessions.

### 9.e Tense + state-class discipline in memory

Memory uses one of three explicit verbs:
- **COMMITTED** — a SHA exists; may be on any branch.
- **MERGED** — on `main`.
- **DEPLOYED** — running on prod (verified by deploy_log + a probe).

Never "shipped" — the ambiguity hides the Finding #1 drift class.

### 9.f Pre-Phase-3 implicit-assumption verification

For any new Stage-1 implementation phase, before writing code, execute the Finding #8 inventory's verification steps (or the analogue for the implementation phase). The 8-10 verifications per phase trade ~30-60 minutes against the 3-4-corrections-per-session pattern. Net win.

---

## Finding #10 — Open questions for operator

Decisions surfaced by this review; review does NOT auto-resolve.

1. **Finding #1c immediate remediation:** (A) merge the risk-tier branch to main, (B) document overlay + add tripwire, or (C) document only? **Recommend (A).**

2. **Finding #1a Phase 1a/1b reports stranding:** (i) merge docs-only commits to main, or (ii) leave on branch + future citations use `[on branch X]` tag? **Recommend (i) for next session that does any merge.**

3. **Finding #1b prod-deploy gating:** when does main get deployed to prod? Per Stage-1 #6 + #11 + #12 (UNTRACKED gaps in Finding #2), what additional work conditions the deploy? Confirm BACKLOG-as-prerequisite or pull into Phase 3?

4. **Finding #1d class-extension scan:** run `runbooks/deploy_log.md` cross-reference against `git log --all` to find other "deployed but unmerged" overlays? Half-hour read.

5. **Finding #2 UNTRACKED gaps — file or accept-as-known-implicit?**
   - #6 REST retry/backoff + stale-snapshot + stuck-order timeout
   - #8 Low-equity alert + funding amount decision (now $10K per BACKLOG note)
   - #11 Panic-halt + credential-compromise runbooks
   - #12 Pre-flip md5-diff prod surface vs git
   - H-11 webhook equity-fallback verification

6. **Finding #5 analogous "live skips X" decisions** — audit now (~30 minutes), or wait for emergent-need pressure to surface them? Surface items: classifier-bar vs broker-event timing; `_observe_fill` one-shot vs re-poll.

7. **Finding #6.1 `result='win'` semantic disambiguation:**
   - (a) Schema: add `result_source` column (requires Board approval per CLAUDE.md § 6).
   - (b) Convention: stamp `extra["result_source"]`.
   - (c) Architectural: live-mode only writes `result` from broker truth (never from classifier).
   - (d) Accept-as-known + document.
   **Recommend (b) — cheapest.**

8. **Finding #6.2 audit-row-loss-after-broker-confirm:**
   - (a) Extend db-lock retry to `insert_paper_trade_record`.
   - (b) Add explicit "audit-row-lost-after-place" recovery handler.
   - (c) Treat audit-row as denormalized view of audit_event (canonical).
   **Recommend (a) as default; (c) as longer-term architecture.**

9. **Finding #7 scope refinements:** confirm Phase 3 scope (B) + ~60-90 LOC additions (Findings #6.1/6.2/6.3/6.4)?

10. **Finding #8 verification phase:** run the 8-10 verifications before Phase 3 starts? Or accept residual risk on the low-severity items?

11. **Finding #9 discipline additions:** which of the five recommendations to adopt — all, subset, or alternatives?

12. **Risk-tier prod overlay — when to add tripwire (if A) or document (if B)?** Operator can do this independently of N+2 Phase 3 entirely.

---

## Verification (artifact integrity check)

- **Citation completeness:** spot-check passes. Every claim has either `file:line`, `commit:<sha>`, `phase{1a,1b}.md@<sha>`, `[prod-probe: <date>]`, or `[memory: <name>]`.
- **Coverage:** Findings #1–#10 each have at least one operator-actionable item or explicit "no action needed, reasoning:" closure.
- **Severity discipline:** Finding #1c flagged HIGH with immediate-remediation framing. Finding #1a/1b/#5/#6.x/#8 entries severities stated.
- **Honest gaps:** Methodology gap on loaded-process introspection for Finding #1c explicitly noted (file + fresh-import is not loaded-process). Findings #2 columns "UNTRACKED gap" labeled with Finding #10 cross-reference.
- **No expanded scope:** items outside the 10 sections (e.g., tasty_options drift, kalshi_weather schema) cited only as drift-class extensions, not investigated.
- **Output reachable:** at `reports/2026-05-30_stage1_bitunix_live_engine_architectural_review.md` — canonical surface on main (will be after this session's commit). Browser surface serves it.
- **Drift-class self-check:** this review's citations follow Finding #1's discipline. Phase 1a/1b references inline-tagged `@33da534` / `@e1d38f8` at every occurrence.

## What this review did NOT do (out-of-scope, not gaps)

- Did NOT execute the Finding #8 verification steps (they're verifications to be run BEFORE Phase 3 starts; not part of this review).
- Did NOT run the Finding #1d class-extension scan against `deploy_log.md` cross-referenced with `git log --all`.
- Did NOT review tasty_options or kalshi_weather drift items in depth (different track).
- Did NOT make any code/config/deploy change. Read-only.
- Did NOT decide any of Finding #10's operator questions. Surfaced; not resolved.

---

*Sources: `runbooks/2026-05-29_bitunix_live_readiness_audit.md`, `runbooks/2026-05-29_bitunix_live_reuse_audit.md`, `reports/2026-05-29_bitunix_live_entry_path_diagnostic.md` (on main); `phase1a.md@33da534` + `phase1b.md@e1d38f8` (branch `bitunix-live-exit-path-2026-05-29`, read via `git show`); code on main HEAD `1926eb9` (`brokers/bitunix.py`, `agents/data_exec.py`, `agents/divisions/bitunix_futures_observer.py`, `persistence/models.py`, `comms/pending_registry.py`, `main.py`, `strategies/paper_trade_replay.py`); commits `e04b192`, `87dac50`, `4a15c72`, `ecfc677`, `0200eed`, `1926eb9`, `2a3d20c`, `0298575`, `69c401a`, `06b5a9e`, `33da534`, `4a8b440`, `e1d38f8`; `BACKLOG.md` § Note + § P2 Stage-1 N+2 Phase 3; prod-probe 2026-05-30 (PID 1762864, healthz, observer.py mtime, TIER_SIZING constants, SHA256 9a8fe9cd…); memory entries `project_bitunix_*.md`, `feedback_deploy_*.md`, `[memory: reference_session_2026_05_29_marathon_eos.md]`. No code/config/deploy changes made.*
