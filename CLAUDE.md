# CLAUDE.md

Loaded into every Claude Code session for this repo. Read it. Honor it.

## What this is

Trading Corp is a multi-broker, multi-strategy Python system that
places real-money trades across equity, crypto, and prediction-market
venues. It runs in production on Azure VM `tc-prod-vm` at
https://trading.jacksumner.com behind Caddy + Authelia. The system is
paper-default on every startup; live trading requires explicit mode
flags, per-strategy `auto_execute: true`, and a deterministic risk
gate that every order — regardless of source — must pass through.
HITL approval routes through the web app at `trading.jacksumner.com`;
Telegram is notification-only.

For the current list of divisions and strategies, see
[docs/divisions.md](docs/divisions.md). For what's running on
production right now, see [runbooks/deploy_log.md](runbooks/deploy_log.md).
For full architecture (organizing principles, decision pipeline,
domain model), see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). For
the Board's profile, communication style, and hard constraints, see
[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md). For deliberate-but-surprising
properties of the codebase, see [docs/sharp_edges.md](docs/sharp_edges.md).

## STOP AND READ — non-negotiable invariants

Before you change anything:

1. **Risk gate is a single chokepoint.** Every order, regardless of
   source, passes through `RiskAgent.evaluate()` in
   [agents/risk.py](trading_corp/agents/risk.py). No code path may
   bypass it.
2. **Audit log writes BEFORE every decision branch, not after.** Past
   silent-failure incidents (alerts disappearing) cost days. The
   dashboard renders snapshots; `audit_event` captures intent. If they
   disagree, audit wins.
3. **Paper is the default on every startup.** `--live` requires
   interactive confirmation AND populated broker creds
   (`assert_live_ready` in [utils/secrets.py](trading_corp/utils/secrets.py)).
4. **Risk caps are deterministic Python.** LLMs may *narrate* verdicts
   (`RiskAgent.narrate`); they may not produce them.
5. **The TradingView webhook → broker path is handling real capital.**
   Don't refactor without explicit, in-session approval.
6. **Local Python execution must go through `scripts\run_capped.ps1`.**
   Any python invocation that touches `trading_corp/` or `tests/` —
   including single-file pytest discovery — runs as
   `.\scripts\run_capped.ps1 python …`, which caps the process tree's
   commit charge at 25 GB via a Windows Job Object. Crash #9
   (2026-05-18 22:08) was an unwrapped pytest on a single test file
   that grew to 58 GB virtual and hard-rebooted the machine. The
   OS-level watchdog approach (procgov service) was investigated and
   abandoned for this Win11 26200 build — see
   [docs/diagnostics/2026-05-19_crash_diagnosis.md § 11](docs/diagnostics/2026-05-19_crash_diagnosis.md).
   Wrapper-invocation discipline is the only enforcement. Trivial
   sanity checks (`python --version`, `python -c "print('hi')"` with
   no project imports) may run unwrapped. See
   [docs/runbooks/session_workload_defaults.md](docs/runbooks/session_workload_defaults.md)
   for the full Python operations checklist.

---

## Session discipline

Operator-supervised work. Honor these as standing rules — they do not
need to be re-stated per session.

- **Stop-at-fork.** When encountering ambiguity, missing premises, or
  anomalies, halt and report to the operator rather than auto-resolving.
  Surface findings before proceeding. Premise corrections caught at the
  fork are cheap; corrections caught mid-implementation cost rollbacks.
- **Scoped commits.** Each commit addresses one item. Reference BACKLOG
  entries by ID where applicable. Never bundle unrelated changes into a
  single commit.
- **Production access: agent read-only SSH is standing practice; writes stay
  operator-gated.** (Operator-ratified 2026-06-10, after disclosed use in the
  2026-06-09 Polymarket investigation and 2026-06-10 Bitunix Day-2 review.)
  - **Agents MAY run read-only SSH against prod directly:** SELECT-only sqlite
    queries (always with the `-readonly` flag), `journalctl` reads,
    `systemctl status`/`show`, and file reads (`cat`/`ls`/`md5sum`/`stat`).
  - **Agents MUST NOT via SSH:** any DB write, any file write/edit/delete, any
    `systemctl start`/`stop`/`restart`, any config change, any NSG/`az`
    mutation. These remain operator-run or operator-explicitly-delegated per
    session (prior approval does not extend to new commands).
  - **Disclosure required:** each session's close-out states whether
    agent-driven SSH occurred and confirms it was read-only.
  - **Classifier reality unchanged:** if the classifier blocks an SSH command,
    switch to operator-runs immediately — no retries.
- **Verify premises against ground truth before scoping.** Memory
  entries, runbook claims, and prior session notes can be wrong. Quote
  file:line or audit-row evidence before treating a premise as settled.
- **Worktree isolation per session.** Parallel Claude Code sessions
  must run in separate `git worktree` instances. Same working directory
  + parallel sessions = branch-hijack incidents.
- **State-class verb discipline in memory.** Memory entries use one
  of three explicit verbs to distinguish drift states:
  - **COMMITTED** — a SHA exists; may be on any branch.
  - **MERGED** — on origin/main.
  - **DEPLOYED** — running on prod, verified by deploy_log + a probe.
  Never "shipped" — the ambiguity hides the canonical-vs-non-canonical
  drift class.

### Session wrap-up

When the operator signals EOS / wrap-up:

1. Update memory files and BACKLOG with this session's decisions and
   surfaces.
2. Create scoped commits per "Scoped commits" above. One item, one
   commit.
3. Push to origin. Closing-out branches (C-1 rotations, audits) stay
   on origin unmerged as audit trail. Feature/code branches go to main
   per the existing merge sequence.
4. Write a next-session handoff prompt capturing: current branch state,
   open forks, recommended first action, verified UTC timestamp.
5. Verify timestamps against system clock before committing deploy_log
   entries.
6. Memory verification gate. Every memory entry written this session
   is verified by re-reading the actual code surface it claims to
   describe. If memory says "X exists at file:line", verify file:line.
   If memory says "the canonical helper returns Y", verify return type.
   Catches the inherit-stale-memory pattern that drives ~3-4 premise
   corrections per subsequent session (Finding #4).

---

## Environment

Operating context for sessions running against this repo:

- **OS: Windows.** Local Python invocations follow the
  `scripts\run_capped.ps1` discipline (STOP AND READ #6). Beyond that:
- **Prefer PowerShell over bash heredocs** for command construction.
  Bash on Git Bash / Windows interprets long commands across line
  breaks unreliably; PowerShell here-strings or single-line `&&`-chained
  commands are safer.
- **For Azure prod access, prefer direct SSH over `az run-command`
  with embedded heredocs.** `az run-command` wraps scripts in cmd.exe
  + bash + heredocs and breaks on quoting, encoding, and command-line
  length limits. Direct `ssh azureuser@trading.jacksumner.com '...'`
  is more predictable.
- **For long payloads to Azure, use base64-wrapped scripts.** Pattern:
  `echo <B64> | base64 -d | bash`. Chunk to ≤6500 bytes per call to
  stay under CMD command-line limits. See
  `scripts/redeploy3_chunked_transfer.py` for canonical patterns.
- **Watch for cp1252 encoding** in stdout. Force UTF-8 when piping
  Python output: `python -X utf8 script.py` or set `PYTHONUTF8=1`.
- **Long paths with spaces** (`AA Incorporado` in the user home) corrupt
  during command construction with line breaks. Prefer `cd` to the
  worktree first, then relative paths.
- **Worktree-relative paths beat absolute paths** in command construction
  for all the above reasons. `cd <worktree> && command < relative.sql`
  is the pattern.

---

## 1. Working agreements

### Decision pipeline

```
INPUT
  TV alert · user Telegram cmd · cron scheduler · web request
       ↓
INGEST   (web/webhooks.py · web/routes.py · comms/telegram_bot.py)
  auth · normalize · audit inbound
       ↓
STATE UPDATE   (agents/divisions/*.py)
  record_alert · _refresh_state_from_signal · persist latches
       ↓
DECISION GATES
  enabled? · halt? · cooldown? · chop? · classify_tier · modifiers
       ↓
ORDER CONSTRUCTION
  notional = equity × tier_size_pct · qty = notional/price · stop · max-loss shrink
       ↓
RISK GATE   (agents/risk.py)  ← single chokepoint
  approve / reject / resize
       ↓
auto_execute=false → HITL approval (web app at trading.jacksumner.com,
                     Telegram = notification ping with deeplink to
                     the approval page) + would_have_placed audit
auto_execute=true  → broker.place_order + Telegram fill notify
```

### Risk + execution

- **Single risk chokepoint.** New signal sources route through
  `RiskAgent.evaluate()` like every other source. There is no second
  risk evaluator.
- **Deterministic-then-narrate.** Risk caps are Python in
  [agents/risk.py](trading_corp/agents/risk.py). LLM narration is
  optional and never overrides the verdict. Same for sizing math, halt
  conditions, tier classification.
- **Mode flag is process-wide; `auto_execute` is per-strategy.** A
  `--paper` process cannot place real orders regardless of config. A
  `--live` process places only for divisions where `auto_execute=true`
  AND risk approves AND (if HITL on) Board approves.
- **HITL approval is the default for any new division.**
  `auto_execute: true` is earned per-strategy after observed paper
  performance, not granted by default.
- **Harmonize the webhook auto-execute gate before any TV division
  flips to `auto_execute: true`.** Today the webhook path
  ([web/webhooks.py](trading_corp/web/webhooks.py)) gates on a single
  `agent.auto_execute` bool, while the LangGraph path
  ([graph/ceo_graph.py](trading_corp/graph/ceo_graph.py)) reads the
  full `auto_execute_caps` structure (require_approval_for, max-dollar
  caps, daily aggregates, VIX gate, LEAP-debit gate). Letting an Otter
  or Cypher division flip to auto without first wiring the rich gate
  into the webhook path would silently bypass the safety net. Don't
  flip the bool until the gates are equivalent.
- **`auto_execute_caps.require_approval_for` triggers are
  load-bearing.** Today: opening a new LEAP, closing any LEAP, any
  action on black-sheep symbols, any action when VIX > 30, rolling for
  debit > 5% of long LEAP value, neutral-strategy open/close. Removing
  or weakening any trigger requires a memo from the Board recording:
  (a) what incident or class of failure it was originally protecting
  against, (b) why that protection is no longer needed, (c) what
  observation would tell us the relaxation was wrong (so we know what
  to watch for after lifting it). No memo, no relaxation.
- **VIX-feed-unavailable is fail-safe to Board.** If `get_vix()`
  returns None, the order escalates regardless of caps. Don't replace
  this with a cached default.
- **Roll-debit-vs-LEAP-value gate uses a cache populated by
  `PMCCAgent.detect_existing_legs`.** Stale or missing → fail-safe
  (Board). Don't loosen this.

### State + audit

- **`audit_event` is the source of truth.** Written via `LoggerAgent`
  before every decision branch.
- **Required tags on webhook events:** every `webhook_received`,
  `alert_ignored`, `webhook_rejected`, `would_have_placed`,
  `agent_error` must include `strategy` and `division` keys. The
  dashboard's per-division activity rail
  (`_query_division_activity`) matches on these.
- **Any state that affects future trade decisions must be in SQLite.**
  Process memory is caching only. Use the `agent_state` table for new
  latches (generic `(agent, key) → JSON` with `updated_ts`). Each
  latch needs a staleness gate (Otter bias = 12h, Cypher bias/sommi =
  3d). Don't introduce new tables without a migration plan.
- **Stale latches are deleted on restore, not patched with defaults.**
  See `_restore_bias_state` pattern in
  [agents/strategies/lord_otter.py](trading_corp/agents/strategies/lord_otter.py).

### Code path isolation

- **Read-only divisions have no order-placement code at the adapter
  level.** Read-only is enforced by missing methods, not runtime
  flags. New read-only adapters subclass a `ReadOnlyBroker` ABC that
  exposes `connect` / `disconnect` / `snapshot` / `quote` only. There
  is no `place_order` to call. (Migration: `FidelityBroker` predates
  this rule and still subclasses the full `Broker` ABC — see [docs/sharp_edges.md#fidelitybroker-subclasses-the-full-broker-abc-not-readonlybroker](docs/sharp_edges.md#fidelitybroker-subclasses-the-full-broker-abc-not-readonlybroker).)
- **Broker credentials never enter agent prompts or LLM context.**
  Only [utils/secrets.py](trading_corp/utils/secrets.py),
  [agents/data_exec.py](trading_corp/agents/data_exec.py), and broker
  adapters touch them. The redaction filter (`RedactingFilter`) is on
  the root logger.
- **The existing real-money pipelines must not be modified,
  refactored, or "improved" without explicit, in-session human
  approval.** New functionality is added in parallel.

### Process + safety

- **Single-instance lock.** `data/trading_corp.pid` is claimed
  atomically via `O_EXCL`. Stale-PID reaping is one-shot. Don't
  bypass `_acquire_lock()` — use a different DB path for tests.
- **`broker_fallback_to_paper` uses `starting_equity=0.0` because $0
  is a failure signal, not a default.** When a real broker connect
  fails, the system replaces it with
  `PaperBroker(starting_equity=0.0)` and writes a
  `broker_fallback_to_paper` audit event. The dashboard then renders
  $0 equity for that division, which is the visible signal that the
  division is down. A non-zero default would mask the failure as
  phantom equity. Don't change this.
- **Webhook flow audits inbound BEFORE agent dispatch.** See
  [web/webhooks.py:220](trading_corp/web/webhooks.py). If the agent
  throws, we still have a record.
- **Schema changes to `proposed_order`, `audit_event`, `position`,
  `account_state`, `strategy_state`, `agent_state` require explicit
  approval AND a migration plan.** The `extra: dict` field on
  `ProposedOrder` (→ `extra_json`) is the escape hatch for
  strategy-specific data — use it before proposing schema changes.

### HITL surface direction

- **The web app at `https://trading.jacksumner.com` is the primary
  HITL surface.** Approve / Reject / Modify decisions belong in the
  dashboard. The dashboard is mobile-friendly (htmx + Tailwind
  responsive layout); on a phone it's the canonical UI.
- **Telegram is a notification-only channel.** When something needs
  Board attention (approval, fill notification, error, halt), Telegram
  emits a short ping with a deeplink to the relevant page on the
  dashboard. Telegram messages do not carry order detail, do not
  accept Approve/Reject replies, do not run inline keyboards. The
  dashboard is what the Board reads + acts in. Don't enrich Telegram
  messages further; new HITL UX work goes into the web app.
- **No new LangGraph TradeFlowState changes for HITL.** Pair-coalescing
  for paired roll orders happens at render time in the web app (group
  by `pmcc_pair_id`), not by extending `TradeFlowState`. The web-app
  POST endpoint resumes the existing `interrupt()` per order with the
  same `BoardDecision` shape `request_board_approval` returns today;
  graph internals unchanged. This deliberately avoids the § 4 trigger
  for "Change the LangGraph checkpointer or `TradeFlowState` shape."

### Research consultation

The research firm
([agents/research/](trading_corp/agents/research/)) is a knowledge-work
consultant that any division can call. It is **not** a decision-maker.
This rule was codified 2026-05-02 after a vision realignment that found
the firm had been over-scaffolded relative to its actual cross-division
value; protect it from re-expansion.

- **A division calls research when ALL of:**
  - The question requires cross-source LLM synthesis — not deterministic
    rule application.
  - The latency budget tolerates 5–60s for an answer.
  - Multiple divisions could plausibly ask the same question, OR the
    answer is high-value enough to justify research overhead for one.
- **A division does NOT call research for:**
  - Per-alert tier / sizing / stop / direction decisions. Strategy code.
  - Mechanics the strategy itself can answer — RSI, ATR, breach %,
    position-size formula, halt conditions, cooldown windows. Strategy
    code, no LLM.
  - Anything in a sub-second loop.
- **Research's surface is the four structured products:**
  `CandidateRecommendation`, `Thesis`, `PositionContext`,
  `TradeConfirmation`. New product types require explicit Board approval
  before scaffolding — adding products has been the failure mode.
- **Cross-division knowledge work routing.** Of today's strategies,
  only PMCC's `research_on_demand` candidate path consults the research
  firm for cross-division knowledge work. Polymarket and Kalshi
  LLM-divergence strategies make LLM calls directly via
  `agents.llm.build_chat_model`, bypassing the research firm by
  design. TradeConfirmation consults from Otter/Cypher are scaffolded
  but the underlying intraday-TA expert isn't built — treat those
  consults as ceremonial (fail-open no-ops most of the time) until
  either the TA capability lands or the consult surface is removed.
  Don't add features that depend on TradeConfirmation actually
  returning useful verdicts for crypto.
- **The decision rule, applied retroactively, would have flagged
  per-alert TradeConfirmation as wrong-fit and saved a phase of work.**
  Apply it forward when scoping new division/research interactions.
- **Before any deploy-adjacent task, verify prod state.** There is no
  git on the prod VM, and `BACKLOG.md` describes intent (what we want),
  not state (what's shipped). Recurring failure mode pre-2026-05-02:
  starting work on a feature that already shipped — bundled into a
  prior bulk-track commit, scaffolded forward-compat in an earlier
  phase, or implemented before the `BACKLOG.md` entry was retired.
  Mitigation:
  1. **Read [runbooks/deploy_log.md](runbooks/deploy_log.md) first.**
     It's the single source of truth for what's running on prod right
     now. Look for `**Features shipped:**` lines that match your task.
  2. **md5-diff target files against prod** before writing any new
     code on a feature you can't 100% verify is unimplemented. Files
     that MATCH are likely already done — investigate before assuming
     new code is needed.
     See the deploy_log.md preamble for the md5-diff verification recipe.
  3. **After every successful deploy, append an entry to
     [runbooks/deploy_log.md](runbooks/deploy_log.md)** per the
     template at the top of that file — including `**Features
     shipped:**` and `**Notable code changes:**` lines that future-you
     can grep for. This is the load-bearing step that prevents the
     next session from re-doing the work.

### Testing discipline

- **Run pytest before any deploy.** Deploys have repeatedly surfaced
  latent dataclass and attribute bugs (missing kwargs, missing
  attributes) that pre-deploy gates would have caught. Baseline test
  count is documented in the most recent successful deploy_log entry;
  expect it to hold across PR branches.
- **Branch tests must cover existing fixtures, not only new tests.**
  Adding tests for new code while breaking existing fixtures (mock
  shape drift, async/sync mismatches) is a recurring regression source.
  When extending mocks for async broker/db interfaces, include all
  required methods (`async snapshot`, `cancel_order` response,
  `db_url` attribute).
- **Generalized AST completeness tests** catch construction-site /
  field-definition drift (the WebDeps `tasty_division` class of bug).
  See `tests/test_main_dataclass_construction_completeness.py` for the
  pattern; add coverage when introducing new dataclasses constructed
  in `main.py` startup paths.
- **File-level prod-vs-main md5 sweep** is the pre-deploy gate that
  catches transfer-set composition defects. See
  `scripts/bitunix_prod_surface_md5diff.py` for the sweep tool.
  Required before any prod deploy.

---

## 2. Pointers

This file is the entry point for every session. Most details that
used to live inline have moved to dedicated files; the rules in
CLAUDE.md cite them by anchor. When in doubt about the current state
of the system, follow the link to the source of truth for that area.

- For the current list of divisions and strategies, see
  [docs/divisions.md](docs/divisions.md).
- For what's running on production right now, see
  [runbooks/deploy_log.md](runbooks/deploy_log.md).
- For full architecture (organizing principles, decision pipeline,
  domain model), see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- For the Board's profile, communication style, and hard constraints,
  see [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).
- For deliberate-but-surprising properties of the codebase, see
  [docs/sharp_edges.md](docs/sharp_edges.md).

---

## 3. Common tasks — canonical patterns

### Adding a new strategy or division

These are two different tasks now (see [docs/divisions.md](docs/divisions.md)). Pick the right one.

**A new STRATEGY** runs inside an existing division (e.g. a second
crypto strategy alongside Otter and Cypher in `coinbase_spot`):
1. Create `agents/strategies/<name>.py`. For TV-driven strategies,
   model on `bitunix_confluence.py`. For scan-driven or poll-driven
   strategies, model on `kalshi_weather_arb.py` or copy the shape
   from `agents/divisions/pmcc_robinhood.py`.
2. Add the agent class with `enabled` / `auto_execute` / `division`
   properties reading from `config/strategies.yaml` (mtime-cached).
3. Wire into [main.py](trading_corp/main.py) deps + `WebDeps`.
4. **`auto_execute: false`** in `strategies.yaml` until paper-track
   record is earned.
5. New persistent state → `agent_state` table with a staleness gate.
6. **Before scoping research consultation, read § Research
   consultation.** Most strategies should not be calling research
   per-alert.

**A new DIVISION** is a new brokerage/account surface (e.g. Polymarket,
crypto futures, a different equity broker):
1. Create `agents/divisions/<name>.py` modeled on `pmcc_robinhood.py`.
   Division code does portfolio-manager work — universe sizing,
   per-account allocation, capacity gates — and routes to one or more
   strategy modules under `agents/strategies/` for the actual
   trade-decision logic.
2. Add the division to [config/divisions.yaml](config/divisions.yaml)
   (broker + account_filter + slug).
3. New broker adapter if the venue is new — see "Adding a new broker
   adapter" below.
4. **Don't design a new division speculatively.** Build only after an
   existing division's pattern is validated in production. Premature
   division design was the failure mode pre-2026-05-02.

### Adding a new broker adapter
1. **Read-write?** Subclass `Broker` in
   [brokers/base.py](trading_corp/brokers/base.py). Implement
   `connect`, `disconnect`, `snapshot`, `place_order`, `cancel_order`,
   `quote`. Set `paper: bool = False` when in live mode.
2. **Read-only?** Subclass `ReadOnlyBroker` (no `place_order`,
   no `cancel_order`). The ABC will refuse instantiation if you try
   to use it where `place_order` is called — that's the enforcement.
3. Wire into `_build_broker_for_division` in
   [main.py](trading_corp/main.py) so `--live --brokers <family>`
   selects the live class and PAPER mode wraps in
   `PaperExecutionBroker`.
4. Failed connect must fall back via the `broker_fallback_to_paper`
   path with `starting_equity=0.0`.

### Adding a new signal source (TV-driven)
**This is the firm-research-agent path.** Mirror
[web/webhooks.py](trading_corp/web/webhooks.py) lines 462–794
(`market_cypher_webhook`):
1. New endpoint `POST /webhook/<source>/<strategy>`.
2. IP allowlist (or env override flag) → body cap → lenient JSON
   parse → constant-time HMAC secret check → replay window (size to
   the bar duration of the signal source) → symbol normalize.
3. **`webhook_received` audit BEFORE agent dispatch.** Tag with
   `strategy` and `division`.
4. Snapshot broker for equity-aware sizing + held-qty lookup. Reuse
   the same snapshot for the risk gate.
5. Agent's `on_alert(payload, account_equity, held_qty)` returns
   `(ProposedOrder | None, decision_str)`.
6. Risk gate inline:
   `deps.risk_agent.evaluate(order, account, strat_state, regime, None)`.
7. `auto_execute=false` → `would_have_placed` audit + Telegram
   notification ping (deeplink to web-app approval page; see § HITL
   surface direction).
   `auto_execute=true` → `data_exec.place(order, division=...)` +
   fill notify. Before flipping any new TV division to
   `auto_execute=true`, harmonize the webhook gate with the
   LangGraph path's `auto_execute_caps` (see § 1).

### Adding a new risk check
1. Add to `RiskAgent.evaluate()` in
   [agents/risk.py](trading_corp/agents/risk.py). New cap ⇒ new
   `params.get(...)` read from `config/risk.yaml`.
2. Determine semantics: `approve` / `reject` / `resize`. Resize must
   set `new_qty`. Reject must set a human-readable `reason`.
3. **Options whole-contract guard:** if the check resizes, floor to
   whole contracts when `is_option` is true.
4. Tests in `tests/test_risk_gates.py`.

### Adding a new audit event kind
1. Pick a stable kind name (e.g. `regime_changed`, `pair_rolled`).
   Reuse existing kinds where possible — the audit query layer
   filters on them.
2. Always include `strategy` + `division` in payload when the event
   is division-scoped.
3. Write the event from the producing agent via
   `LoggerAgent.log_event(actor=..., kind=..., payload={...})`.

### Adding a column to an existing table
1. Don't, if `extra: dict` (→ `extra_json`) can carry the data.
2. If you must: schema change → explicit Board approval → migration
   script in `scripts/` → backfill existing rows or document the
   "pre-migration aggregates" caveat.

### Hot-reloading a config change
- `risk.yaml` — `RiskAgent` mtime-checks on every `evaluate()` call.
- `strategies.yaml` — division agents (Otter/Cypher) mtime-check on
  property reads. **`graph/ceo_graph.py:_check_auto_execute` re-reads
  on every order with no mtime cache** (sharp edge).
- `divisions.yaml`, `agents.yaml` — loaded at startup. Restart
  required.
- **There is no validation.** Typos silently degrade the strategy.
  Watch the audit log for "would have fired but didn't."

---

## 4. Things to ask before doing

Stop and ask the human first if you're about to:

- Touch the existing TradingView → broker path (Otter or Cypher
  webhooks, PMCC scan flow, `agents/risk.py`, `agents/data_exec.py`,
  broker adapters in any way that affects placement).
- Add a new path that places orders.
- Change the risk gate logic, including adding/removing/reordering
  caps.
- Change `audit_event` write ordering (must remain "before each
  branch").
- Add new secrets handling, change the redaction filter, or alter
  the Key Vault fetch path.
- Change the LangGraph checkpointer or `TradeFlowState` shape.
- Edit anything in [runbooks/](runbooks/) — operational playbooks,
  no-edit without explicit Board approval. They're a recovery
  contract, not a refactor target; a stale or "improved" runbook is
  worse than a missing one when you're locked out.
- Edit [infra/main.bicep](infra/main.bicep) — IaC for the Azure
  deployment. **Edit-with-deploy-plan only:** any change must be
  paired with a step-by-step deploy plan (what `az`/`bicep` commands
  will run, in what order, what to roll back if it fails). Don't
  edit speculatively.
- Modify VM-side configuration (Caddy, Authelia, NSG rules, systemd
  units, anything in `/etc/` on the VM). **No-edit from this repo.**
  These live on the production VM and are managed via SSH per the
  runbooks. A change here would silently disagree with the deployed
  state.
- Change the `broker_fallback_to_paper` semantics (especially the
  `starting_equity=0.0`).
- Touch `_acquire_lock()` / PID-file logic.
- Add a new `auto_execute_caps.require_approval_for` trigger or
  remove an existing one.
- Bypass HITL "for testing" or "for the demo." Build a paper-mode
  fixture instead.
- Default any new strategy to `auto_execute: true`.
- **Deploy a new strategy or change a strategy's parameters
  (sizing, tier thresholds, halt conditions) without a Backtester
  approval.** This rule is documented as a hard constraint
  ([PROJECT_CONTEXT.md § 11](PROJECT_CONTEXT.md)) but isn't
  code-enforced today (see [docs/sharp_edges.md#backtester-approval-gate-is-documented-but-not-code-enforced](docs/sharp_edges.md#backtester-approval-gate-is-documented-but-not-code-enforced)) — treat it as a
  human-process gate until enforcement lands.

---

## 5. Known sharp edges (summary)

Five sharp edges are summarized here because they're cited from rules
elsewhere in this file. The full catalog is at
[docs/sharp_edges.md](docs/sharp_edges.md).

- **Webhook risk gate ≠ LangGraph risk gate orchestration.** TV
  webhooks call `risk_agent.evaluate()` inline; PMCC + Telegram flows
  go through `build_trade_graph()`. Same gate, two orchestrations —
  the webhook path's `auto_execute` is a single bool, the graph
  path's `auto_execute_caps` is much richer. Flipping a TV division
  to `auto_execute=true` today would skip the richer caps. Full
  entry: [docs/sharp_edges.md#webhook-risk-gate--langgraph-risk-gate-orchestration](docs/sharp_edges.md#webhook-risk-gate--langgraph-risk-gate-orchestration).

- **Webhook risk gate falls back to `equity = 100_000.0` if broker
  snapshot fails.** Risk caps run on a placeholder rather than
  rejecting. Snapshot-failure log is the trail. Don't tighten or
  loosen without an audit-trail review. Full entry:
  [docs/sharp_edges.md#webhook-risk-gate-falls-back-to-equity--100_0000](docs/sharp_edges.md#webhook-risk-gate-falls-back-to-equity--100_0000).

- **Backtester approval gate is documented but not code-enforced.**
  [PROJECT_CONTEXT.md § 11](PROJECT_CONTEXT.md) and § 4 (Things to
  ask before doing) say "new strategies need backtest approval"; the
  path doesn't enforce it today. Treat as human-process. Full entry:
  [docs/sharp_edges.md#backtester-approval-gate-is-documented-but-not-code-enforced](docs/sharp_edges.md#backtester-approval-gate-is-documented-but-not-code-enforced).

- **`extra_json` is unqueryable by SQL columns.** Schema-stable
  strategy-specific bag, but `LIKE`-based queries are brittle. Use
  `extra_json` before proposing a new column. Full entry:
  [docs/sharp_edges.md#extra_json-is-unqueryable-by-sql-columns](docs/sharp_edges.md#extra_json-is-unqueryable-by-sql-columns).

- **Config hot-reload has no validation.** Typos silently degrade.
  Watch the audit log for "would have fired but didn't." Full entry:
  [docs/sharp_edges.md#config-hot-reload-has-no-validation](docs/sharp_edges.md#config-hot-reload-has-no-validation).

---

## 6. How to use this file

- This file is loaded into every Claude Code session for this repo.
- If a rule here conflicts with a user's in-session instruction,
  raise the conflict explicitly and ask before proceeding.
- This file is updated by humans, with AI assistance, never by AI
  alone.
- When proposing additions, propose them in a separate message — do
  not edit CLAUDE.md as part of unrelated work.
- The user's communication preferences (no flattery openings,
  evidence first, commands+screenshots over prose, don't-bury-the-
  lede) live in [PROJECT_CONTEXT.md § 10](PROJECT_CONTEXT.md). Honor
  them.
