# HITL-in-app — Phase B design pass

**Status:** Design — no code yet. Written 2026-05-03 00:55 UTC during
the same session that shipped Phase A's slim Telegram formatter behind
a feature flag.

**Scope:** Phase B of `BACKLOG.md "P0 — HITL approval flow lives in
the web app"`. Deliver the web-app surface that the Board uses to
Approve / Reject / Modify pending orders on a phone or desktop, with
Telegram demoted to a notification ping that links into this surface.

**Prior phases:**
- Phase A (built local 2026-05-03, not deployed) — slim
  notification body + deeplink behind `TELEGRAM_NOTIFICATION_ONLY`
  env flag. Defaults OFF; flips ON the day Phase B routes are live.
- Subsequent phases (C, D, E) layer on Phase B's foundation —
  paired-roll coalescing, quick-modify controls, and eventually web
  push to retire Telegram.

**Hard constraints (from CLAUDE.md):**
- §1 risk gate is the single chokepoint — Phase B does NOT add a
  second risk-evaluation surface; it consumes existing
  `risk_verdict` from the LangGraph state.
- §1 audit log writes BEFORE every decision branch — the resume POST
  writes its `board_approved` / `board_rejected` row BEFORE invoking
  graph resume.
- §1 paper-default — the web surface respects mode flag /
  `auto_execute` exactly as the Telegram surface does. The web only
  swaps the *channel*, not the gates.
- §6 "ask before changing TradeFlowState" — Phase B preserves the
  existing per-order TradeFlowState shape. Pair-coalescing happens at
  render time only (Phase C), not via state-shape change.
- §6 "ask before adding a new path that places orders" — Phase B
  *does* add a new path that ends in placement, but it's a swap of
  the human-decision channel, not a new risk-evaluation or order-
  construction surface. Document explicitly + Board-approved before
  shipping. Risk path internals unchanged.

---

## 1. What Phase B delivers

A mobile-friendly web surface the Board uses for HITL decisions:

- **`GET /approvals`** — index of pending approvals (order count,
  one-line headline per order, age, division, link to detail).
- **`GET /approvals/{order_id}`** — detail page for one pending
  order. Renders headline, side/qty/strike/expiration, position
  context (LEAP, prior rolls, P&L), risk verdict, and Approve /
  Reject / Modify controls. Mobile-responsive; reads cleanly on a
  phone screen without horizontal scroll.
- **`POST /approvals/{order_id}/decide`** — the action endpoint.
  Body: `{"decision": "approve|reject|modify", "reason": str,
  "new_qty": float | null}`. Resolves the in-process pending
  registry's Future for `order_id`, which unblocks the orchestrator's
  `await channel.request_approval(...)` and triggers graph resume
  via the existing `Command(resume=...)` mechanism.

Existing Telegram path stays alive in Phase B (rich body, inline
keyboard, callback handler) — both surfaces converge at the same
pending registry, first-decision-wins. The Phase A slim format flag
is what flips to make Telegram a pure notification once we trust the
web surface end-to-end.

---

## 2. Architecture: how a decision flows today vs after Phase B

### Today (Telegram-only)

```
PMCC scout / TV alert
        ↓
  graph.ainvoke(state)
        ↓
  approval_node calls request_board_approval(req)
        ↓
  interrupt({...}) — graph SUSPENDS, ainvoke returns with
  result["__interrupt__"] populated
        ↓
  _run_order extracts req, awaits channel.request_approval(req)
        ↓
  TelegramChannel sends message, stores Future in self._pending[order_id]
        ↓
  Board taps inline keyboard
        ↓
  _on_callback handler resolves _pending[order_id].set_result(BoardDecision)
        ↓
  channel.request_approval returns BoardDecision
        ↓
  _run_order calls graph.ainvoke(Command(resume={...}), config={thread_id})
        ↓
  Graph resumes from interrupt(), returns BoardDecision payload to
  approval_node, runs to execute_node or end_rejected_node
```

The pending Future lives **in TelegramChannel's `_pending` dict**.
That's the single point that needs to become accessible from the web
POST handler.

### After Phase B (web + Telegram coexist)

```
PMCC scout / TV alert
        ↓
  graph.ainvoke(state)
        ↓
  approval_node → interrupt() → graph SUSPENDS
        ↓
  _run_order extracts req, awaits PendingApprovalRegistry.wait(order_id, req)
        ↓
  registry stores Future + req in pending[order_id]; emits notification(s)
  via every wired channel (Telegram, future Slack/email)
        ↓
  Board sees Telegram ping (Phase A slim format) OR navigates to
  /approvals on the dashboard
        ↓
  Board approves via:
    - Telegram inline keyboard → _on_callback → registry.resolve(order_id, decision)
    - Web POST /approvals/{order_id}/decide → registry.resolve(order_id, decision)
        ↓
  registry sets the Future result, also writes a board_decision_received audit row
        ↓
  await PendingApprovalRegistry.wait returns BoardDecision
        ↓
  _run_order calls graph.ainvoke(Command(resume=...)) — unchanged from today
```

**Net change:** the in-process Future moves from `TelegramChannel._pending`
into a new shared `PendingApprovalRegistry`. TelegramChannel and the
new web routes both use the registry. LangGraph internals are
**byte-identical** to today.

---

## 3. PendingApprovalRegistry — the load-bearing seam

Lives at `trading_corp/comms/pending_registry.py`. Singleton
in-process registry for the trading-corp service.

```python
class PendingApprovalRegistry:
    """In-process registry of approval Futures keyed by order_id.
    Web routes + TelegramChannel both interact with this."""

    def __init__(self) -> None:
        self._pending: dict[str, _PendingEntry] = {}
        self._lock = asyncio.Lock()

    async def wait(self, req: ApprovalRequest, timeout_s: float = 3600.0) -> BoardDecision:
        """Called by the orchestrator (replaces channel.request_approval).
        Adds an entry to the registry, fires notify_callbacks, blocks
        on the Future until resolved or timeout."""

    def resolve(self, order_id: str, decision: BoardDecision, source: str) -> bool:
        """Called by Telegram callback OR web POST. Sets the Future
        result if not already resolved. Returns True if accepted, False
        if duplicate (already-resolved). Writes board_decision_received
        audit row tagged with `source` ('telegram' or 'web')."""

    def list_pending(self) -> list[ApprovalRequest]:
        """Read-only snapshot for the /approvals index page."""

    def get(self, order_id: str) -> ApprovalRequest | None:
        """Detail-page fetch."""

    def register_notifier(self, fn: Callable[[ApprovalRequest], Awaitable[None]]) -> None:
        """TelegramChannel subscribes its push function. When `wait`
        adds an entry, registered notifiers fire concurrently. Allows
        adding Slack / email channels later without registry changes."""
```

**Why a singleton (process-wide) rather than passed via DI:** the web
route handlers (registered on FastAPI app) and the orchestrator (called
from the main asyncio loop) must share a registry. Easiest is module-
level singleton constructed at startup, accessible via
`trading_corp.comms.pending_registry.get_registry()`. Pass it into
TelegramChannel + web routes at construction time so tests can
override.

**Concurrency:** `asyncio.Lock` guards the `_pending` dict
mutations. `Future.set_result` is thread-safe for the same loop.
Single-event-loop process → no cross-thread concerns.

**Restart semantics:** the registry is in-process and lost on
restart. The LangGraph SqliteSaver has the suspended thread state
persisted. On startup, a recovery routine could scan the
checkpointer for interrupted threads and re-emit notifications +
re-add to the registry. **For Phase B v1 we accept the gap:** if
trading-corp restarts mid-approval, the user re-triggers (via the
dashboard `/approvals` page reading from checkpointer directly, or
by re-running the scout). Recovery is a Phase B v2 polish item.

---

## 4. Migration: existing TelegramChannel.request_approval

Today: `TelegramChannel.request_approval` is the function the
orchestrator awaits. It owns the Future + the inline-keyboard
callback handler.

After Phase B:
- `TelegramChannel.request_approval` becomes thin — when called by
  the orchestrator (during Phase B transition), it delegates to
  `registry.wait(req)`. Inline keyboard callback resolves via
  `registry.resolve(order_id, decision, source="telegram")`.
- **Cleaner cut for Phase B:** orchestrator calls
  `registry.wait(req)` directly instead of `channel.request_approval(req)`.
  TelegramChannel's role narrows to: register a notifier, handle
  inline-keyboard callbacks → resolve. The "channel-level approval"
  abstraction in `comms/base.py` becomes vestigial; can deprecate
  later.

The transition for `BoardChannel.request_approval` (the abstract
method on `comms/base.py`):
- Phase B v1: leave it as-is for backwards-compat with CLIChannel.
- Phase B v2: deprecate; CLIChannel becomes a thin notifier+resolver
  pair like TelegramChannel.

---

## 5. Routes — concrete endpoints

All routes Authelia-gated like the rest of the dashboard.

### `GET /approvals`

Index of pending approvals. Renders a Tailwind table:

```
PENDING APPROVALS (3)

  ROLL SHORT · MSTR · 2m ago    robinhood_pmcc   [ Review → ]
  OPEN PMCC · NVDA · 8m ago     robinhood_pmcc   [ Review → ]
  ROLL SHORT · BTC/USD · 11m    coinbase_spot    [ Review → ]
```

Each row links to `/approvals/{order_id}`. Empty state: "No
approvals pending. The dashboard will surface new ones as the
strategies emit them."

Server-side data: `registry.list_pending()` → list of
ApprovalRequest. Render order: newest-first.

Mobile layout: stacked cards on narrow viewports, table on wide.

### `GET /approvals/{order_id}`

Detail page — the canonical replacement for the rich Telegram body.
Server-side data: `registry.get(order_id)` → ApprovalRequest with
`req.detail` carrying `order` (DB row), `risk_verdict`, `division`.

Layout (mobile-first):

```
🎲 APPROVAL · MSTR · ROLL SHORT
  robinhood_pmcc · 4m ago

  ┌─ Trade ─────────────────────────────
  │  Close: -1 contract · $162.50C · 0d
  │         mark $17.80/sh · ITM 9.7%
  │         → debit $1,780
  │
  │  Open:  +1 contract · $170.00C · 7d
  │         mark $10.50/sh · δ 0.45 · OTM
  │         → credit $1,050
  │
  │  Net DEBIT: $730
  │  Rationale: halfway midpoint $170 (Major Breach Rule 6)
  └────────────────────────────────────

  ┌─ Position context ──────────────────
  │  LEAP: $160C 2027-01 · cost $23.80 · mark $58.05
  │  Unrealized P&L: +$3,425 (+143%)
  │  Roll history: 4 prior rolls · net +$185 credit
  │  Most recent: 7d ago · $190 → $162.50 (-$27.50 = roll-down)
  └────────────────────────────────────

  ┌─ Risk verdict ──────────────────────
  │  ✓ Approved · within all caps
  │  per-trade 0.4% of $50k = $200 budget
  └────────────────────────────────────

  ┌─ Warnings ──────────────────────────
  │  ⚠ Halfway-roll cooldown: prior roll-up was 7d ago...
  │  ⚠ LEAP delta 0.85 → roll_leap soon (DTE 200)
  └────────────────────────────────────

  [ APPROVE ] [ REJECT ] [ MODIFY ]
```

Approve / Reject buttons POST to `/approvals/{order_id}/decide`.
Modify expands to inline form (qty input + optional reason text).

The position-context block reuses the existing `_format_position_context`
formatter from `comms/approval_format.py` — extract the formatter to
a shared helper that produces both Telegram-Markdown (current) AND
HTML (new). Or better: the helper returns a structured dict, and
each surface (Telegram / web template) renders it as it pleases.

### `POST /approvals/{order_id}/decide`

Action endpoint. Body (JSON or form-encoded):

```json
{
  "decision": "approve" | "reject" | "modify",
  "reason": "optional string",
  "new_qty": 2.0   // required if decision=modify, else omitted
}
```

Handler:
1. Validate `decision` value.
2. If `modify`: validate `new_qty > 0`.
3. Call `registry.resolve(order_id, BoardDecision(...), source="web")`.
4. If `resolve` returns False (already resolved by Telegram or
   another web tab in the same window), return 409 Conflict with
   "decision already submitted" message.
5. If True, return 200 with the decision echoed. Optionally, an
   htmx fragment to swap the buttons for a "Decision recorded —
   redirecting" message.
6. Audit row `board_decision_received` is written by `registry.resolve`
   itself so the audit lands BEFORE we return (CLAUDE.md §1).

CSRF / auth: Authelia handles the auth layer. CSRF: dashboard is
already POST-able from anywhere behind Authelia (existing
`/audit/{id}/replay-research` pattern). For Phase B v1 we follow
the same pattern. Phase B v2: add a CSRF token tied to the session
if we ever want defense-in-depth beyond Authelia.

---

## 6. Pair coalescing (Phase C scope, design here for forward-compat)

Two `ProposedOrder`s sharing a `pmcc_pair_id` go through the graph
**independently** today — each gets its own `interrupt()`, its own
ApprovalRequest, its own Future. The orchestrator awaits each
sequentially.

**Render-time coalescing approach:**
- `/approvals` index page: when iterating pending requests, group by
  `extra.pmcc_pair_id`. Pairs render as ONE row with a combined
  headline ("ROLL · MSTR · close + open · 2m ago").
- `/approvals/{order_id}`: when the detail page loads, if the order
  has a `pmcc_pair_id`, look for the sibling in the registry. If
  found, render BOTH legs in the same card with Net Debit/Credit
  computed from both. Approve / Reject buttons act on BOTH at once.
- POST to `/approvals/{order_id}/decide` for a paired order:
  registry's `resolve` accepts an optional `also_resolve_paired:
  bool` flag. If True, find the sibling (by pair_id in pending) and
  resolve it with the same decision. Both Futures resolve, both
  graph runs resume.

**Safety guarantee preserved:** the original "approve close, reject
open → naked short" failure mode goes away because the UI submits
ONE decision that resolves BOTH legs. Reject → both rejected. Approve
→ both approved (each runs through risk + execute via its own
graph thread).

**Edge case:** sibling arrives a moment after the user lands on the
detail page. Page polls the registry for sibling presence (htmx
poll every 5s), or the detail page's initial render waits a brief
window. v1 implementation: poll. v2: server-sent events.

**Edge case:** sibling is rejected by risk gate before reaching
approval (so no sibling in registry). Detail page renders just the
one leg with a note: "sibling leg was risk-rejected — see audit
log." User can approve or reject the surviving leg knowing it'll
leave the position uncovered.

---

## 7. Modify flow

The existing approval_node → modify_then_risk_node chain handles
modify (`new_qty` rebuild → risk re-evaluate → back to approval).

Web modify form:
- Inline form expansion on the detail page (htmx `hx-target` swap).
- Fields: `new_qty` (number input), `reason` (text, optional).
- Submit POSTs `{"decision": "modify", "new_qty": ..., "reason":
  ...}` to the same `/decide` endpoint.
- Registry resolves with modify decision; orchestrator re-runs the
  graph through risk_node, returns to approval_node, registry
  receives a NEW ApprovalRequest for the same order_id (modified qty,
  re-evaluated risk).
- Detail page htmx-polls and re-renders with the new approval,
  re-showing the buttons. User can approve the modified version or
  modify again.

**Pinning the contract:** `BoardDecision.new_qty` already exists in
`graph/interrupts.py`; new fields (`new_limit_price` for limit
modifies) can be added later without web changes if the web POST
ignores unknown fields.

---

## 8. Notification migration — Phase A flag flip

Phase A built the slim Telegram body behind
`TELEGRAM_NOTIFICATION_ONLY=true`. Phase B's deploy plan:

1. Deploy Phase B routes (slim body still OFF).
2. Live-test the web flow end-to-end with a synthetic /demo order.
3. Test with a real PMCC scout-emitted approval (still routed through
   Telegram's rich body in parallel — Telegram still has the inline
   keyboard, web has the buttons; first wins).
4. Once verified: set `TELEGRAM_NOTIFICATION_ONLY=true` on prod
   systemd unit, restart. Telegram now emits slim body + deeplink
   + the existing inline keyboard (Phase A keeps the keyboard for
   transitional safety).
5. After ~1 week of confidence: remove the inline keyboard from
   the slim format. Telegram is pure notification.

---

## 9. Pending registry write semantics + audit

The registry doesn't store data persistently — it's in-process state
mirroring the LangGraph checkpointer. But every meaningful event
gets an audit row:

- `pending_approval_added` — when `wait` first registers an entry.
  Payload: order_id, division, ApprovalRequest summary, pair_id (if
  any). Lets the dashboard reconstruct pending state if the registry
  is lost on restart (read recent `pending_approval_added` rows that
  don't have a matching `board_decision_received` row).
- `board_decision_received` — when `resolve` is called. Payload:
  order_id, decision, reason, new_qty, source ('telegram' / 'web' /
  'cli' / 'auto').
- Existing `board_approved` / `board_rejected` — written by
  approval_node / end_rejected_node, unchanged.

The audit chain is:
```
pending_approval_added
  → (wait...)
  → board_decision_received (source tagged)
  → board_approved | board_rejected (existing)
  → execution_error | filled (existing)
```

Restart-recovery (Phase B v2): on startup, query
`pending_approval_added` rows in the last hour without a matching
`board_decision_received`, re-add them to the registry, re-emit the
notification. Avoids losing approvals to a restart.

---

## 10. Templates

Two new Jinja templates following the existing dashboard patterns:

- `web/templates/approvals.html` (index)
- `web/templates/approval_detail.html` (detail)

Both extend `base.html`. Use the same `bg-pane / border-edge / text-mono`
Tailwind tokens already in `division.html` and `home.html`. Reuse
existing partials where possible.

A new partial: `web/templates/partials/approval_card.html` for the
detail's order/leg rendering — used by both single-leg and paired
detail pages.

For the position-context block, extract the dict-building from
`comms/approval_format.py:_format_position_context` into a
`comms/position_context.py` helper that returns a structured dict.
Both the Telegram formatter (current) and the new template
(`{{ ctx.leap.strike }}` etc.) consume the dict. Avoids duplicating
the data-shaping logic.

---

## 11. Open questions for the design pass

1. **Should the registry also expose a SSE / WebSocket stream** for
   live updates on the index page, or is htmx 5s polling enough? v1
   recommendation: polling. SSE only if polling proves choppy on
   mobile.
2. **Should the detail page show the LangGraph thread's checkpointer
   state** for advanced debugging? v1: no — the audit log + position
   context cover the user-facing need. Add as a "show internals"
   debug view later.
3. **`auto_execute=true` interaction:** when a strategy is auto, the
   `_check_auto_execute` path in approval_node skips the interrupt
   entirely. Web surface is irrelevant for those orders — they don't
   appear in the registry. Index page just doesn't show them.
   ✓ No design implication.
4. **CLI channel migration:** CLIChannel today implements
   `request_approval` directly with stdin prompts. After Phase B,
   does the CLI still work? v1: yes — orchestrator calls
   `registry.wait(req)`, registry's notifier fan-out includes a CLI
   notifier that prints to stdout, CLI input handler resolves the
   registry. Phase B v2 cleanup: extract CLI into a
   notifier+resolver pair. v1 keeps CLI working as-is by routing
   through the registry's wait.
5. **Should `/approvals` show approvals across ALL divisions** or be
   division-scoped? v1: all divisions in one index, with division
   slug rendered prominently per row. Filter UI later if it
   matters.
6. **Modify form: numeric input for new_qty** — what increments are
   sensible? Stock: integer shares. Crypto: small float (BTC at
   0.0001 increments). Options: integer contracts. v1: free-text
   number input; backend validates by order type.

---

## 12. Test plan

- **Unit tests** on `PendingApprovalRegistry`:
  - `wait` adds entry, blocks until `resolve`
  - `resolve` returns False on second call (idempotency)
  - `resolve` writes audit row tagged with source
  - `list_pending` snapshot doesn't mutate live entries
  - Notifier fan-out: multiple notifiers all called, an exception
    in one doesn't block others
  - Timeout in `wait` returns reject decision + audit row
- **Route tests** (FastAPI TestClient):
  - GET /approvals empty state + populated state
  - GET /approvals/{id} renders order detail; 404 on unknown id
  - POST /approvals/{id}/decide: approve / reject / modify all
    resolve the registry
  - POST 409 when registry entry already resolved
  - POST validates `new_qty > 0` for modify
  - All routes 401/redirect when Authelia headers absent (existing
    auth-gated test pattern)
- **Integration test** (in-memory): full loop —
  graph.ainvoke → interrupt → registry.wait → POST /decide →
  registry resolves → graph resumes → final_status='filled'.
- **Pair coalescing test** (Phase C): two orders with same pair_id
  in registry; POST decide on one with `also_resolve_paired=true`
  resolves both Futures.
- **Pre-existing telegram tests:** must continue passing; the
  Telegram path is preserved in parallel during Phase B.

---

## 13. Acceptance criteria

- A Board member receives a Telegram ping for a real PMCC scout
  recommendation, taps the link on a phone, sees the order detail +
  position context + risk verdict, taps Approve, and within a few
  seconds the LangGraph resumes → execute_node → place_order
  (paper-mode) → fill notification renders.
- The same flow works on a desktop browser.
- A Reject from the web surface results in `board_rejected` final
  status.
- A Modify from the web surface re-enters approval_node with the
  new qty after risk re-evaluation, and the user sees the new
  approval card without page refresh (htmx swap).
- Telegram inline keyboard still works during Phase B (parallel
  paths). First decision wins; second submission gets a 409 (web)
  or "decision already recorded" (Telegram).
- Pair coalescing (Phase C): a PMCC roll fires ONE notification,
  the page shows BOTH legs with Net Debit/Credit summary, ONE
  approve click executes both.
- Authelia-gated: unauthenticated browser sees the login page on
  any /approvals* route.
- Mobile-friendly: detail page reads cleanly on a 375px-wide
  iPhone screen with no horizontal scroll.

---

## 14. Phasing within Phase B

To break Phase B into shippable cuts:

- **B.1 — Registry + minimal routes (no UI polish, no modify, no
  pairs).** PendingApprovalRegistry with audit writes. GET
  /approvals + /approvals/{id} render bare-bones HTML. POST
  /decide accepts approve/reject only. Telegram path unchanged
  (still the rich body). End-to-end test: web Approve resumes
  the graph. Ship.
- **B.2 — UI polish + Modify.** Tailwind-styled detail page,
  position-context block extraction into structured helper,
  Modify inline form, htmx swap on decision. Ship.
- **B.3 — Pair coalescing (Phase C in the BACKLOG entry's
  numbering).** Index groups by pair_id; detail auto-coalesces
  sibling; POST resolves both. Ship.
- **B.4 — Slim Telegram cutover.** Flip
  `TELEGRAM_NOTIFICATION_ONLY=true` on prod (Phase A flag). Soak
  for a week. Ship.
- **B.5 — Quick-modify buttons (Phase D in the BACKLOG entry).**
  +½ size / −½ size / limit −5% buttons on the detail card.
  Ship.

Each B.* is independently shippable + revertible. B.4 is the
load-bearing flag flip — until then, Telegram remains the rich
fallback.

---

## 15. Risk + reversibility summary

- **Reversible:** all routes additive, env flag controls Telegram
  body shape, registry coexists with TelegramChannel's pending dict
  during transition. Worst case: revert the routes + turn the flag
  off, Telegram path keeps working.
- **§6 trigger:** Phase B does add a new path that places orders
  (the resume after web approve). Document at deploy time + Board-
  approve. Risk path internals unchanged.
- **No LangGraph state-shape change.** Pinned by Phase C using
  render-time pair coalescing rather than `proposed_orders: list`
  in TradeFlowState.
- **Real-money exposure:** today's HITL Telegram approve is the
  same risk as tomorrow's HITL web approve. Channel changed; gates
  unchanged.

---

## 16. Files-to-touch summary (B.1 minimum)

**New:**
- `trading_corp/comms/pending_registry.py` — registry + audit-write
  helpers.
- `trading_corp/comms/position_context.py` — extract dict-builder
  from `approval_format.py` (lets both Telegram + web consume the
  same structure).
- `trading_corp/web/templates/approvals.html` — index.
- `trading_corp/web/templates/approval_detail.html` — detail.
- `tests/test_pending_registry.py` — unit tests.
- `tests/test_approvals_routes.py` — FastAPI TestClient tests.

**Modified:**
- `trading_corp/comms/telegram_bot.py` — `_pending` dict moves to
  registry; callback resolves via registry; constructor gains
  `registry` arg.
- `trading_corp/main.py` — construct registry once, pass to
  TelegramChannel + web app deps.
- `trading_corp/web/app.py` — add registry to WebDeps.
- `trading_corp/web/routes.py` — register the new routes.
- `trading_corp/main.py:_run_order` — call `registry.wait(req)` in
  place of `channel.request_approval(req)` (the channel call still
  works in B.1 if we want gradient migration; cleanest is to
  switch).

**Untouched (deliberately):**
- `trading_corp/graph/ceo_graph.py` — approval_node + interrupt
  mechanics.
- `trading_corp/graph/interrupts.py` — ApprovalRequest /
  BoardDecision shapes.
- `trading_corp/agents/risk.py` — risk gate.
- LangGraph SqliteSaver checkpointer wiring.
- TradeFlowState shape.

---

## 17. End-of-design notes

- Phase B is conceptually small but operationally sensitive — the
  pending registry is the new HITL chokepoint and a bug there
  blocks every approval. Test coverage matters more than UI polish
  in B.1.
- The position-context-dict extraction helps decouple presentation
  from data. Worth doing in B.1 even though only Telegram consumes
  it — gives B.2's web template a clean shape to render.
- The Phase A flag (built but not deployed today) is what makes
  Phase B's deploy a soft cutover instead of a hard one. Keep both
  paths working during the soak.
- Open question 4 (CLI channel) is the cleanest way to validate the
  registry abstraction: if CLI can plug into the same notifier+resolver
  pattern, the design is right.
