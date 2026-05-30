# Next-session prompt — Stage-1 P1 Gate (a): BitUnix REST resilience

**Written:** 2026-05-30 ~12:00 UTC at session close of gate (b).
**Target:** the third and final P1 pre-deploy gate from the 2026-05-30
architectural review. Once landed, the main-to-prod deploy of Stage 1 is
unblocked (subject to the usual import-graph audit + RH-pickle-aware
coordination + operator sign-off).

---

## Stage-1 P1 Gate (a) — BitUnix REST resilience (operator-supervised, code + tests, longest-pole)

Last of three P1 prod-deploy gates from the 2026-05-30 architectural review
(Finding #2, Readiness #6). Gates (b) panic-halt + cred-compromise runbooks
shipped 2026-05-30 (merge `f20a7bc`); gate (c) md5-diff shipped earlier the
same day (merge `b131d02`). Gate (a) is the longest-pole — real implementation
work touching the BitUnix REST chokepoint, with both retry semantics and a
new staleness-health primitive.

This work makes the live order path **resilient to the network failures every
crypto-exchange REST endpoint produces under load** — timeouts, 5xx storms,
intermittent rate-limit responses, stale-snapshot blindness. Without it,
Stage-1 would face two known failure shapes that the audit explicitly flags:
the broker swallows REST errors silently (`_connected` stays True even when
the API is unreachable), and a stuck order has no timeout→cancel policy.

These resilience primitives are the artifacts an operator needs to trust that
the bot won't either (a) silently fail-open into a stale-snapshot world or
(b) leave a stuck order resting indefinitely.

### Read first (in order)

1. **Memory entries:**
   - `[[bitunix-operational-runbooks-2026-05-30]]` — last session's deliverable + gate progress.
   - `[[gate-c-md5diff-landed-2026-05-30]]` — gate (c) deliverable + worktree-fixture-gap anomaly (still applies).
   - `[[2026-05-30-architectural-review-first-batch-remediation]]` — full Finding #10 decision queue + the 3-P1-gate context.
   - `[[bitunix-live-engine-build]]` — current Stage 1 state, what's shipped on main.
   - `[[bitunix-order-path-safety-pattern]]` — mode-mismatch consumer + safety_notifier wiring pattern (the resilience layer will need to interact with this).
   - `[[verify-premises-against-ground-truth]]` — discipline standard.
   - `[[branch-tests-must-cover-existing-fixtures-not-only-new-tests]]` — discipline standard; **triggers if the new resilience layer adds an `await` site at any pre-existing call**, which it will.

2. **The architectural review § Finding #7 §6 + Readiness #6:**
   - `git show origin/stage1-architectural-review-2026-05-30:reports/2026-05-30_stage1_bitunix_live_engine_architectural_review.md`
   - Specifically look for Finding #2 row #6 + Finding #7 §6 "pre-deploy gate" framing.

3. **The readiness audit § 5 (canonical scope for this gate):**
   - `runbooks/2026-05-29_bitunix_live_readiness_audit.md` § 5 "Failure modes and recovery" — lines ~91-105.
   - **The gap statement** (line 105, verbatim): "Gap → Stage 1 (MEDIUM): REST retry/backoff + a stale-snapshot/connection health signal; restart-with-open-position resume from broker truth; a stuck-order timeout→cancel policy."
   - **The restart-with-open-position piece is N+2 Phase 3 scope (already filed)**, NOT gate (a). Gate (a) is the three other items: retry/backoff + stale-snapshot signal + stuck-order timeout.

4. **The shipped operational runbooks** (gate (b)'s output — relevant context for what resilience must enable):
   - `runbooks/bitunix_panic_halt.md` § A.2 references the broker self-latch (`_halt_new_orders`) — verify gate (a)'s additions don't unexpectedly trip it.
   - `runbooks/bitunix_credential_compromise.md` § F.3 uses synthetic FAKE-creds probe — note the rejection envelope shape (`http=200, code=10003`) for retry-decision semantics.

5. **Code surfaces gate (a) will modify (READ FIRST, then plan, then write):**
   - `trading_corp/brokers/bitunix.py:494-532` — `_request()` is THE single REST chokepoint. Retry/backoff goes here. Current state: 15s timeout, errors propagate, no retry.
   - `trading_corp/brokers/bitunix.py:766-793` — `_observe_fill()` polls order detail in a loop. Stuck-order timeout policy goes here OR in a wrapper.
   - `trading_corp/brokers/bitunix.py:172-290` — `snapshot()`. Stale-snapshot tracking goes here (record `_last_successful_snapshot_ts`).
   - `trading_corp/brokers/bitunix.py:701` — `_halt_new_orders` self-latch. Stale-snapshot SHOULD latch this same flag when threshold crosses (consistent with mode-mismatch behavior).
   - `trading_corp/agents/data_exec.py:223-289` — `_handle_position_mode_mismatch` is the safety-side-effect pattern; mirror it for `stale_snapshot_detected` and `stuck_order_cancelled`.
   - `trading_corp/agents/data_exec.py:326-450` — `flatten_division`; note its snapshot-verify discipline.

6. **Tests to model on:**
   - `tests/test_bitunix_broker.py` — existing async-httpx tests; mock at the `httpx.AsyncClient` boundary, not at `_request` (this is what catches "SDK shape" bugs per `[[mocks-dont-catch-sdk-shape]]`).
   - `tests/test_bitunix_orderpath_safety.py` (if present) — pattern for safety-consumer tests.

### Verify state before doing anything

- `git rev-parse origin/main` should equal `4c5aa97` (this-session EOS).
- `git branch --show-current` — start a new branch off main.
- `git worktree list` — verify your worktree is isolated (use a dedicated worktree per parallel-session discipline `[[parallel-session-branch-collision]]`).
- Re-verify the runbook `# Last verified` markers reference `03f3261` — if main HEAD has advanced because of other-track work, that's fine; the runbooks just point to a snapshot.

### Scope — three sub-items, three commits (do NOT bundle)

Each sub-item is its own commit on the same branch. The branch lands as a
single merge to main; the commits stay distinct for review and reversion.

#### Sub-item 1: REST retry/backoff at `_request`

**Where:** `brokers/bitunix.py:494-532`. Touches only the `_request` method.

**What:**
- Add retry on transient failures. Transient = `httpx.TimeoutException`,
  `httpx.HTTPStatusError` with status in `{502, 503, 504, 408, 429}`,
  and `BitunixAPIError` with retriable code set (start narrow: rate-limit
  codes only — `code=10004` if observed; expand only on evidence).
- Backoff: exponential with jitter, starting ~250 ms, cap ~4 s, max retries
  3. Total wallclock cap ~10 s on top of the 15 s per-attempt timeout — so a
  worst-case _request is ~65 s. Document the math in a comment.
- **Do NOT retry signed POSTs that may have already landed.** The
  `clientId` idempotency described at the top of `bitunix.py:25-27` means a
  POST retry that returns `code=30042` (CLIENT_ID_DUPLICATE) is provably
  safe — treat as success per the existing semantics. But that ONLY holds
  for POSTs with `clientId` set. Defensive guard: only retry POSTs when
  `clientId` is present in the body.
- Audit on retry: `LoggerAgent.log_event(actor="bitunix_broker", kind="rest_request_retried", payload={...})` per retry attempt OR once-per-`_request` summary (pick one; lean to once-per-summary to avoid audit-row flooding).

**What NOT to do:**
- Don't change the sign-what-you-send invariant (`bitunix.py:21-24`). Retry uses the same `body_str` + same signing inputs; recompute fresh `nonce + timestamp` on each attempt or the server will reject the retry as duplicate-nonce.
- Don't retry write methods (`place_order`, `cancel_order`, `flatten`) at the `_request` layer — those have their own idempotency semantics. Test: existing `place_order` tests must pass unchanged.

**Tests:**
- Happy path: 1 attempt succeeds → 0 retries, no audit row.
- Transient 503 then success → 1 retry, 1 audit row, retry succeeds.
- Hard 401/403 → 0 retries (not transient), raises immediately.
- Timeout 3× → exhausted retries, raises.
- POST with `clientId` + 503 then 30042 (CLIENT_ID_DUPLICATE) → treated as success.
- POST without `clientId` + 503 → no retry, raises.
- Sign-stability: retry uses NEW nonce/timestamp but SAME body bytes.

**Estimated:** ~120-180 LOC source + ~200-280 LOC tests. Single focused
commit; ~4-6 hours.

#### Sub-item 2: Stale-snapshot / connection health signal

**Where:** new attribute on `BitunixBroker` (`_last_successful_snapshot_ts`).
Update on every successful `snapshot()` return. New method `is_healthy()`
that returns True iff `now - _last_successful_snapshot_ts < threshold_s`.

**Threshold:** start at **60 s** (twice the strategy's per-bar cadence on the
1-min bar). Document the choice as a config value: add
`bitunix_futures.snapshot_staleness_threshold_seconds: 60` to
`config/strategies.yaml` for tunability. Re-read on every `is_healthy()` call
(mtime-cached like other strategy YAML reads).

**Halt integration:** when `is_healthy()` returns False AND the bot tries to
place an order, the order path should treat this the same way it treats a
position-mode mismatch — latch `_halt_new_orders=True` with
`_halt_reason="snapshot_stale:<age_s>"`, write `snapshot_stale_halt` audit
row via `data_exec._handle_stale_snapshot()` (new method mirroring
`_handle_position_mode_mismatch`), telegram `safety_alert`, re-raise.

**Where the staleness check fires:**
- In the bitunix observer's pre-trade gate, before constructing the order.
- In `data_exec.place()` as a defense-in-depth re-check (because
  observer-gate-passed-and-then-snapshot-went-stale-between-classification-and-place is a real race).

**Tests:**
- Fresh snapshot → healthy=True.
- Snapshot older than threshold → healthy=False.
- place() with stale snapshot → raises + audit row + telegram + halt-latch set.
- Recovery: a new successful snapshot reverses `is_healthy()` back to True,
  but the halt latch stays (operator-clear via `resume()` only).

**Estimated:** ~80-120 LOC source + ~150-200 LOC tests. One focused commit;
~3-4 hours. Cross-cuts with sub-item 1 (the retry layer must update
`_last_successful_snapshot_ts` only on the final-success attempt, not on
intermediate retried failures).

#### Sub-item 3: Stuck-order timeout → cancel

**Where:** `_observe_fill()` at `bitunix.py:766-793` is the natural
chokepoint. Current behavior: polls `get_order_detail` up to
`_fill_max_polls` times with `_fill_poll_interval_s` between polls, breaks on
terminal status. Returns even if status is non-terminal at exhaustion.

**Add:** if polling exhausts WITHOUT a terminal status (i.e. order is still
PENDING / PARTIAL_FILLED at the last poll), invoke `cancel_order(order_id)`,
write `stuck_order_cancelled` audit row, send `safety_alert` telegram, then:
- if status was `PART_FILLED`: return what filled (partial fill is real
  money; don't lose it).
- if status was fully unfilled / PENDING: raise so the caller's path treats
  the order as not-placed.

**Threshold:** the existing `_fill_max_polls × _fill_poll_interval_s` IS the
threshold. The new logic is the cancel-on-exhaustion, not a new timer.
Verify these defaults are reasonable (check current values; if they
multiply to e.g. <5 s, propose raising for the cancel-path to fire only on
genuinely stuck orders, not on slow but recoverable fills).

**Edge cases:**
- `cancel_order` may itself fail (network down). If it fails: write
  `stuck_order_cancel_failed` audit + escalated telegram, then `raise` (do
  NOT silently swallow — this is the case where operator intervention is
  required).
- A PARTIAL_FILLED order that we cancel must be reflected in the FillEvent
  return shape (not the full order qty). Use the existing partial-fill
  recovery path in `_observe_fill` (lines 791-792 `if filled_qty <= 0 and
  hist_qty > 0: filled_qty = hist_qty`).

**Tests:**
- Happy path: order fills within polls → no cancel attempt.
- Stuck PENDING through all polls → cancel called, audit + telegram, raise.
- Stuck PARTIAL_FILLED → cancel called, audit + telegram, return partial.
- Cancel itself fails → `stuck_order_cancel_failed` audit + raise.

**Estimated:** ~60-100 LOC source + ~120-180 LOC tests. One focused commit;
~2-3 hours.

### Session-shape estimate

| Item | LOC source | LOC tests | Hours |
|---|---|---|---|
| 1. REST retry/backoff | 120-180 | 200-280 | 4-6 |
| 2. Stale-snapshot signal | 80-120 | 150-200 | 3-4 |
| 3. Stuck-order timeout | 60-100 | 120-180 | 2-3 |
| Cross-item integration + full test gate | — | — | 1-2 |
| **Total** | **~260-400** | **~470-660** | **~10-15** |

Likely **1.5-2 sessions** depending on complexity surfacing in the retry
layer. If it stretches to a third session, the natural cut is to land items
1 + 3 first (they're independent) and 2 in a follow-up (because it touches
both bitunix.py and data_exec.py).

### Constraints (carry from prior sessions, no changes)

- Operator-supervised. **STOP-and-report at forks** rather than auto-resolving.
- **No prod deploys without explicit operator sign-off** and the pre-deploy
  import-graph audit (`[[feedback-deploy-import-graph-audit]]`). The prod
  deploy after this gate lands is the deploy that takes broker-write +
  safety + entry-path + risk-tier + resilience from main to prod — large
  surface; not casual.
- Each new branch in its own worktree. Verify `git branch --show-current`
  + `git rev-parse HEAD` before every commit (parallel-session discipline
  `[[parallel-session-branch-collision]]`).
- **Tighter commits than feels normal:** each sub-item is its own commit.
  Don't bundle.
- Test gate: `.\scripts\run_capped.ps1 python -m pytest tests/ --tb=no -p
  no:cacheprovider --ignore=tests/test_backtest_bitunix_confluence_five_factor.py
  --ignore=tests/test_bitunix_confluence_gate.py
  --ignore=tests/test_bitunix_gate_inputs.py`. **Main baseline 2004/26
  (post gate (c)); worktree baseline 2002/28** (2-test fixture gap on
  `data/trading_corp.db` per `[[gate-c-md5diff-landed-2026-05-30]]`).
- Test-fixture-gap discipline `[[branch-tests-must-cover-existing-fixtures-not-only-new-tests]]`
  **TRIGGERS for this work** because the retry layer + staleness check add
  required attributes / new behaviors to pre-existing call sites. Run the
  full pre-existing test suite against branch state BEFORE declaring tests
  green; fix-forward any pre-existing tests broken by the new contract.
- Mock at the `httpx.AsyncClient` boundary, NOT at `_request`. The
  `[[mocks-dont-catch-sdk-shape]]` discipline applies.
- Don't refactor the sign-what-you-send invariant (`bitunix.py:21-24`).
  Retry preserves body bytes + recomputes signing inputs (nonce, timestamp);
  do not re-serialize the body.
- The bitunix broker's `_halt_new_orders` self-latch is THE backstop. The new
  stale-snapshot path latches the same flag with a different
  `_halt_reason`. Don't introduce a parallel halt mechanism.

### Out of scope unless re-prompted

- Restart-with-open-position resume from broker truth — N+2 Phase 3 scope
  (`[[bitunix-live-exit-path-phase1b]]`), separate session.
- Websocket fill stream — Stage-3 work per readiness audit.
- Partial-fill fractional lifecycle — Stage-2 work.
- Clock-skew guard — Stage-2 work per readiness audit § 5.
- Credential-rotation mid-trade — already runbook-only (operator-coordinated;
  acceptable-with-restart at all stages).
- Prod deploy — gate (a) landing unblocks the deploy, but the deploy itself
  is a separate session with its own go/no-go.
- Editing CLAUDE.md.
- The 2 unfiled runbooks from gate (b) scope ((b) buggy-deploy rollback +
  (c) discrepancy dispute) — operator decision to file or roll into a
  follow-up session.
- Finding #10 operator decisions still queued (8+).
- Anything in kalshi / polymarket / pmcc tracks.

### Output expected at session close

- Per-sub-item commit SHAs landed on origin/main (3 commits + 1 merge if
  bundled in one session; up to 6 + 2 if split).
- Test gate results: full suite, both for the worktree-baseline AND a sanity
  check on main checkout if the test-fixture-gap discipline triggers.
- BACKLOG entries closed: gate (a) marked LANDED, completing all 3 P1
  pre-deploy gates.
- Memory: `[[2026-05-XX-bitunix-rest-resilience]]` capturing what shipped +
  the retry-discipline pattern (sign-what-you-send + clientId idempotency +
  fresh-nonce-per-retry are the load-bearing combinations).
- Cross-link entry to `runbooks/deploy_log.md` if the resilience layer
  exposes any operator-facing knob (likely the `snapshot_staleness_threshold_seconds`
  config value should be cross-referenced).
- EOS snapshot in BACKLOG: now-all-3-P1-gates-closed; the next prod-deploy
  is unblocked subject to import-graph audit + operator sign-off.
- Push to origin/main; confirm origin matches local main.
- Next-session prompt: **the next prod-deploy session** (or alternatively,
  any of the Finding #10 queue items if the operator wants to defer the
  prod-deploy decision).

---

## Discipline standard (carried forward from prior sessions)

- Use Sonnet sub-agents for mechanical work when capable.
- Stop-and-report at forks rather than auto-resolving.
- Surface anomalies with diagnostic detail.
- Don't expand scope mid-task.
- Tighter commits than feels normal: if the work produces an artifact
  (summary file, notes), commit it as you go rather than at the end.
