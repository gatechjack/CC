# Next-session pickup — post-Tastytrade-rotation-runbook (IC thread)

Picks up after the 2026-05-26 ~22:58 UTC session that closed the
**P1 HIGH Tastytrade OAuth rotation runbook** item end-to-end:
canonical procedure landed at `27dd0ef`, fail-closed JWT scope check
script verified 10/10 paths, deploy_log forward-link + memory pointer
landed at `10c5157`. Both commits on `origin/main`. No prod touch.

This file is the IC-thread pickup. The parallel session's own pickup
file is `runbooks/session_start_2026_05_26_post_c7_draft.md` for the
C-7 deploy thread — separate concern, separate session.

---

## State on origin

`origin/main` head: `10c5157`. Working tree clean modulo
`docs/Deployment notes.txt` (parallel-session untracked,
operator-owned, **DO NOT sweep**).

---

## Read first (in order)

1. **`BACKLOG.md` top EOS** — 2026-05-26 ~23:58 UTC entry (this
   session's wrap, with rotation-runbook closure + open-item updates).
   Then the parallel session's 2026-05-26 ~23:30 UTC entry (C-7
   draft state).
2. **Memory auto-loaded** on Tastytrade-touching work (string-match
   triggers TASTYTRADE_PROVIDER_SECRET, TASTYTRADE_REFRESH_TOKEN,
   "rotate", "re-grant", "invalid_grant"):
   - `[[feedback-tastytrade-rotation-runbook]]` — read before any
     TT rotation / re-grant / scope-error debug.
   - `[[reference-tastytrade-oauth-scope-widening]]` —
     machine-readable gotchas the runbook is built on.
   - `[[ic-grader-shipped]]` — IC grader live on prod since
     2026-05-23; §6 closed under corrected criterion;
     `auto_execute: false` (load-bearing).
   - `[[feedback-mocks-dont-catch-sdk-shape]]` — pre-commit gate
     for any SDK-touching code (Bug 4 below is in this class).
   - `[[feedback-no-documented-leaky-escape-hatch]]` — discipline
     lesson extracted from this session.
3. **`runbooks/deploy_log.md`** — 2026-05-26 22:58 UTC entry
   (rotation runbook landed; "bash-only KV writes — PowerShell
   `--value` form removed, uncloseable plaintext window" is the
   greppable exclusion).

---

## Closed in last IC-thread session — DO NOT re-litigate

- **IC grader §6** — closed under corrected criterion (gate 7
  reached on real ATM IV; closure note at
  `planning/ic_grader_section6_closure_20260523.md`).
- **IC grader runbook §6 amendment** — explicit won't-fix per
  operator; closure note is the source of truth.
- **Tastytrade rotation runbook** — SHIPPED at `27dd0ef`.
  `runbooks/tastytrade_oauth_rotation.md` is canonical;
  `scripts/check_tt_token_scope.py` is the fail-closed JWT scope
  verification tool. Don't re-author.

---

## IC-thread open items (approximate leverage order)

### 1. Bug 4 — `tastytrade_provider.py` dead `get_history` branch (P2 MEDIUM)

`_fetch_close_series` (`tastytrade_provider.py:347-376`)
ImportErrors on every call (`from tastytrade.market_data import
get_history` — symbol doesn't exist in 12.4.1). IVR falls through
to yfinance HV silently. Pre-existing condition.

**Cross-surface impact (load-bearing for scoping this session):**
the same `tastytrade_provider.py` feeds BOTH:
- **IC division candidate grader** (gate-4 IVR computation).
- **tasty_options division** (Phase 1 paper observation running per
  `[[project-tasty-options-paper-clock]]`; same broker + same
  provider).

Resolution options:
- **(a) Delete the `get_history` branch + document
  yfinance-by-design for IVR.** Lowest risk, smallest diff. Records
  the silent-fallback as intentional.
- **(b) Wire the real 12.4.1 historical-bars API.** Higher leverage
  (real Tastytrade data for IVR; consistent with gate-7's real
  ATM IV) but bigger surface area. Probably requires `get_history`'s
  12.4.1 replacement to be located first — neither the AM-fix audit
  nor the rotation runbook session located it.

Either choice needs cross-surface verification:
- IVR readout in IC grader (paste a candidate, watch gate-4).
- IVR in tasty_options' iron_condor scanner (Phase 1 paper logs).
- **Live-SDK gate per `[[feedback-mocks-dont-catch-sdk-shape]]` is
  MANDATORY for option (b)** — mock unit tests won't catch a wrong
  12.4.1 API shape; need a live authenticated call against real
  Tastytrade.

**Frame at session start:** cross-surface impact is the prerequisite
framing. Don't sweep `_fetch_close_series` without first asking the
operator which option (a/b) to take and which surface verifies first.

### 2. C-1 secret rotation (P0, blocked on C-7 deploy + backfill)

13 distinct credential rotations across 8+ providers. Blocked on C-7
(see parallel session's C-7 thread). When unblocked: **use
`runbooks/tastytrade_oauth_rotation.md`** for the
TASTYTRADE_PROVIDER_SECRET + TASTYTRADE_REFRESH_TOKEN portion of
the 13-rotation set. Don't improvise — `[[feedback-tastytrade-
rotation-runbook]]` will auto-load and re-state this.

### 3. `bitunix_atr_snapshot` observability audit kind (P2)

Silent-fallback class, same observability gap as Bug 4's
IVR-via-yfinance silent fallback. Filed earlier; not advanced.

---

## Other open items (separate threads, not IC)

- **C-7 deploy session** — local-only branch `c7-webhook-secret-scrub`
  (2 commits, never pushed). See `runbooks/session_start_2026_05_26_
  post_c7_draft.md` for the deploy sequence. C-7 → backfill → C-1
  ordering is load-bearing.
- **Sun 2026-05-31 ~13:00 UTC pm-watchlist weekly seed fire** —
  6-criterion verification gate per the analyze-whale EOS.
- **Tasty Options Phase 1 paper observation clock** — running;
  review doc gate before Phase 2 promotion. Phase 2 needs the
  trade-scoped TT token on prod (per
  `[[reference-tastytrade-oauth-scope-widening]]` gotcha 1).
- **43 deferred package bumps** (P1).
- **Architecture: trading-corp-web.service split** (P3).

---

## Discipline carryover (from operator, this session)

- **For security-critical surfaces: no documented leaky escape
  hatch.** When the safe path is always reachable, do NOT document
  the leaky fallback even as a "last resort." A documented-but-
  leaky path becomes a loaded path a hurried operator can
  rationalize into using. The runbook exists to prevent leaks; it
  shouldn't contain one. See `[[feedback-no-documented-leaky-
  escape-hatch]]` for the full pattern with the distinguishing
  case (acceptable minimized-window for unavoidable surfaces).
- **Verify runbook content matches the canonization claim BEFORE
  writing the memory pointer.** Per
  `[[feedback-session-committed-phantom-pointer]]`: `git show
  <commit>:<file>` the committed version before downstream
  artifacts (memory, deploy_log) cite it as canonical. This caught
  a delete that hadn't actually landed.
- **Spin off Sonnet for mechanical work; keep Opus for judgment.**
- **Stop-and-report at forks rather than auto-resolving.**
- **Surface anomalies with diagnostic detail.**
- **Don't expand scope mid-task.**
- **Tighter commits than feels normal:** if the review produces an
  artifact (summary file, notes), commit it as you go rather than
  at the end.

---

## Hard rules (carry over)

- **CRLF normalization happens at deploy transport, NEVER in a
  commit.** `[[feedback-crlf-routes-py-deploy]]`.
- **Live-SDK gate is MANDATORY for any provider-touching change.**
  `[[feedback-mocks-dont-catch-sdk-shape]]`.
- **`auto_execute: false` on IC is load-bearing. Do not flip.**
- **Wrap local Python touching `trading_corp/` or `tests/` in
  `.\scripts\run_capped.ps1`** (CLAUDE.md §6 — Crash #9).
- **Push is a separate decision from deploy.**
- **Runbooks no-edit without Board approval** (CLAUDE.md §4).
  Exception: `runbooks/deploy_log.md` is append-only-per-deploy.
- **Operator authority on Robinhood auth.** Don't refresh
  `robinhood.pickle` via `az` — MFA-loop risk.
- **For TT-touching work: read `[[feedback-tastytrade-rotation-
  runbook]]` first.** Auto-loads on rotation/re-grant/scope-error
  strings; do not improvise the rotation procedure.

---

## Anomaly to flag at session start

The parallel session's 2026-05-26 ~23:30 UTC EOS (BACKLOG line 11)
and their `runbooks/session_start_2026_05_26_post_c7_draft.md`
(line 33) both list "Tastytrade rotation runbook (P1, untouched)"
as an open item — that's **stale**. The runbook landed at `27dd0ef`
on origin between their observation window and their commit; they
didn't know about my parallel session. The `[[feedback-tastytrade-
rotation-runbook]]` memory pointer is the canonical resolution of
that staleness — any future session reading either stale reference
will have the memory auto-load and redirect to the canonical
artifact.

This BACKLOG EOS at the top corrects the record.

---

## Honest assessment first

Read the EOS + memory pointers above before proposing any "let me
just X" plan. Operator picks priority; suggest from the IC-thread
open-item list, don't autoselect.
