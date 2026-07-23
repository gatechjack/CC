# PMCC Live Execution on Robinhood — Investigation & Plan

**Status: PLAN ONLY. Investigation + phasing. No code, no branch, no spec commitments, no deploy.
`auto_execute` stays FALSE. This document does NOT authorize flipping it.**

Author pass: 2026-07-22 (code-grounded; file:line cited). The live-execution *plumbing*
and the *decision to use it* (the `auto_execute` flip) are treated as strictly separate:
the flip is Phase D, a distinct later gate — **plumbing readiness is a precondition, not a trigger.**

---

## ★ HEADLINE FINDING (read first)

**PMCC roll legs are dispatched as INDEPENDENT single-leg orders, so Bucket B's B4 atomicity
guarantee does not survive live execution.** A PMCC short roll = buy-to-close the current short
(`position_effect="close"`) + sell-to-open a new short (`position_effect="open"`). Today
`pmcc_robinhood.py` emits these as **two separate `ProposedOrder`s** (`_propose_roll_short`
~L3316; factory `_make_option_order` ~L3622 sets no combo keys — grep for
`combo_id|is_multi_leg|combo_direction` in `pmcc_robinhood.py` = **zero**), each dispatched
through its own `_run_order` → `ceo_graph` → `data_exec.place()` (single-leg). If the close fills
and the open rejects/hangs, the position is **naked** — exactly the pathology B4 eliminated at the
**proposal** layer, recreated at the **fill** layer. **Phase A exists to close this**, by routing
rolls through the already-atomic combo path.

**The atomic multi-leg plumbing already exists** — `RobinhoodBroker.place_multi_leg`
(`trading_corp/brokers/robinhood.py:1021`) submits a single combo via
`rs.orders.order_option_spread(...)` (one POST, one order id, all-or-nothing; proven by the
"no id ⇒ raise, never synthesize per-leg fills" logic ~L1077), guarded so a combo leg can never
take the single-leg path (`_place_option_order` ~L966). But it is exercised **only by the iron
condor** (`robinhood_joint_iron_condor.py`), which is same-expiry. **PMCC is entirely disconnected
from it.** So the expensive part of this build is NOT the atomic plumbing (it's built) — it's
(1) wiring PMCC to it, and (2) the net-new post-submission surface that paper mode never exercised.

---

## ★ AMENDMENT — 2026-07-22 (operator decisions: roll_leap is permanently MANUAL; Phase A = roll_short only)

**This amendment SUPERSEDES the roll_leap portions of §b/§c/§e below. The original investigation text is
retained for history. `auto_execute` stays FALSE; no code / branch / build is authorized by this amendment.**

### A. roll_leap is permanently MANUAL (RESOLVED — removes work, adds none)
The agent RECOMMENDS a LEAP roll; if the operator approves, **the operator executes it manually.** The
agent **never places LEAP legs.** Rationale (recorded): a roll_leap is a **capital-reallocation** decision.
The operator's real option set includes keeping the LEAP, or closing it and redeploying to a **different
asset entirely** — options the system cannot see, because it does not know what else the operator is
considering. The operator opens all LEAPs manually and selects the asset. The agent's job is managing short
calls against LEAPs and held assets — not allocating capital across assets.

**Consequences:**
- **Phase A-2 (roll_leap atomicity) is DELETED.** No sequenced sub-combos, no 4-leg combo, no live probe of
  multi-expiration RH acceptance. The agent does not execute roll_leap, so there is **no fill-layer risk to
  close** on that path — the former decompose-vs-quarantine fork (old §e Phase A open decision) is moot.
- **Phase A is now SOLELY the 2-leg atomic roll_short path** — buy-to-close the current short + sell-to-open
  a strictly-later short; a single-underlying diagonal, RH-combo-legal (a recognized spread). All of §e
  Phase A's "combo-tag + `place_combo` + combo-level HITL" work applies to roll_short only.
- **auto_execute (Phase D), if ever flipped, is scoped to roll_short ONLY.** Recorded explicitly: this is a
  materially smaller and more defensible flip than "PMCC runs autonomously" — the agent would auto-execute
  short-call management only; every capital-reallocation (LEAP) decision stays with the operator, always HITL.

### B. Executable vs advisory becomes a STRUCTURAL distinction (design answer — read-only, NOT built)
roll_short proposals are **EXECUTABLE** (agent places on approval); roll_leap proposals are **ADVISORY**
(operator acts; the agent places nothing). Today both flow through the same HITL surface and look identical —
the ONLY discriminator is the `action` string (`roll_short_*` vs `roll_leap_*` in each leg's `extra["action"]`).
Investigation 2026-07-22 (no code):

- **(a) Make it structural, not string-inferred. ★ DECIDED 2026-07-22 — a TYPED FIELD on `ProposedOrder`,
  NOT an `extra` key.** Add `dispatch: Literal["executable","advisory"] = "executable"` to `ProposedOrder`
  (`persistence/models.py:51-103`), set at assembly time in `pmcc_robinhood.py` (the roll_leap leg factory
  calls), not inferred downstream. Safe default keeps every other division executable untouched; persisted
  via `to_db_row`; readable at every chokepoint and by the web surface. **Rationale:** this field IS the
  guard that prevents LEAP legs from ever executing — the `extra` dict is where things get forgotten, but a
  typed field appears in signatures, is visible to anyone reading the dataclass, and is reachable by static
  analysis. Structural guards belong in the type. The fail-closed guard (c) keys off this typed value, with
  the `action`-string prefix as defense-in-depth. (Rejected alternative: an `extra["proposal_kind"]` key —
  lower-touch but freeform / less discoverable / easier to drop in a refactor.)
- **(b) How the approval surface renders an ADVISORY proposal (unmistakably not an execution request).**
  Today every card (`web/templates/approval_detail.html`, coalesced by
  `comms/position_context.py:coalesce_paired_view`) shows **Approve / Reject / Modify** buttons that all fire
  `data_exec.place()`; there is NO advisory concept anywhere (confirmed: no `advisory` / `informational` /
  `display_only` field on `ProposedOrder` / `ApprovalRequest`; decisions are only `approve|reject|modify`,
  `graph/interrupts.py`). An advisory card must branch on the (a) field and: replace the execution buttons
  with a **non-placing** action set (e.g. **Acknowledge / Dismiss**) that records the operator's decision as
  an audit event and clears the queue entry — **no Future resolves into a placement**; carry an explicit
  **"ADVISORY — you execute this manually; the agent will NOT place any order"** banner + distinct
  color/icon; and show the recommended legs as **information** the operator needs to place it themselves
  (strikes / expirations / deltas / net cost), presented as a recommendation, not as order rows.
- **(c) Fail-closed dispatch refusal (so no code path or auto_execute flip can execute a roll_leap).** The
  refusal belongs at the DISPATCH CHOKEPOINTS every real placement flows through: **`data_exec.place()`**
  (guard BEFORE the broker call at `data_exec.py:193`) AND **`data_exec.place_combo()`** (BEFORE
  `data_exec.py:702`). Any order/leg whose (a) value is `advisory` (defense-in-depth: OR whose `action`
  matches a `roll_leap_*` pattern) → **RAISE**, never place. This mirrors the EXISTING fail-closed precedent
  — `_place_option_order` (`robinhood.py:1084-1096`) already raises if a combo leg reaches the single-leg
  path ("fail fast instead" of partial exposure). Fails **CLOSED**: an advisory-or-ambiguous roll_leap order
  at any dispatch gate raises rather than executing, so a future caller, a refactor, OR an
  `auto_execute: true` flip cannot place LEAP legs. (Belt-and-suspenders: roll_leap legs are never
  combo-tagged, so they cannot ride `place_combo` either.)
- **(d) 4-leg compound presentation. ★ DECIDED 2026-07-22 — KEEP the 4-leg presentation for advisory
  roll_leap** (render the existing 4-leg compound as an advisory card per (b); minimal change to what is
  assembled). roll_leap currently emits a 4-leg compound so the operator sees all legs (endorsed HITL design,
  `_BLACK_SHEEP_RULES` L178-184). **Rationale:** the operator needs all four legs to evaluate the
  recommendation — what is being closed, what would replace it, at what prices. That is the information the
  decision rests on, and it is also what the operator hand-executes if they approve; prose would make the
  recommendation harder to act on. The advisory distinction is carried by the **banner and the non-placing
  buttons** (per (b)), NOT by removing information. (Rejected: collapsing to a prose summary or "suggested
  legs" — either would strip detail the operator needs.) `_BLACK_SHEEP_RULES` / `_STANDARD_RULES` / the skill
  are NOT changed — the LLM still produces the same recommendation; only how the deterministic layer
  tags/renders/dispatches it changes.

### C. NEW FAILURE MODE — position-state drift (known consequence of the manual-LEAP design)
When the operator manually rolls a LEAP after approving an advisory recommendation, the agent learns of it
only at the **next `detect_existing_legs` scan.** In between, the agent believes it is managing a short
against a LEAP that no longer exists.
- **Why it drifts:** PMCC persists **no** position belief — it re-derives fresh from the broker every scan
  (`detect_existing_legs` → live `get_option_positions_detail`, `pmcc_robinhood.py:1929`); there is **no
  intraday manage / position-poll loop** (the only PMCC scheduled tasks are the 2-pass daily scheduler and
  the orphan-approval reconciler, which does not refresh positions). Position view is fresh ONLY at scan-fire
  time.
- **Window (bounded by the daily cadence):** the view refreshes at most twice per trading day — the pre-open
  scan (~08:30-09:25 ET) and the 15:00-ET terminal-DTE pass (`_scheduled_pmcc_scan_loop`, `main.py`).
  **Worst case:** operator rolls just AFTER the pre-open scan → stale until the 15:00 pass (~5.5h) and not
  fully refreshed until next pre-open (~23h). **Best case:** rolls just before a scan → near-zero.
- **What the exposure ACTUALLY is (low, non-naked, self-healing):** during the window the agent's belief is
  "short covered by the OLD LEAP"; the real state is "short covered by the NEW LEAP" — **the short is still
  covered** (the operator's manual roll keeps 1:1 coverage), so this is a **belief drift, not a
  position-integrity / naked risk.** The only bite is if the agent, on the stale belief, proposes a
  short-management action keyed off the old LEAP's parameters — but (1) `auto_execute` is FALSE so it is HITL
  and the operator who just rolled is in the loop, (2) roll_short concerns the SHORT leg, unchanged by a LEAP
  swap, so most actions remain valid, and (3) the next scan re-derives from the broker → **self-heals within
  ≤1 trading day.**
- **Cheap ways to shorten it (report only — NOT authorized to build):** **(i)** on operator **acknowledge**
  of an advisory roll_leap, trigger a **targeted `detect_existing_legs` refresh for that symbol** on the next
  300s scheduler tick (reuses existing machinery) → shrinks worst-case drift from hours/~1 day to ~minutes;
  or **(ii)** mark the symbol **"operator-roll-pending"** and suppress further proposals on it until a fresh
  scan confirms the new LEAP (fail-safe: don't act on a symbol you know just changed). Both reuse existing
  scan code. **Recorded as a known design consequence; no fix built.**

### D. Rules UNCHANGED
`_BLACK_SHEEP_RULES` and `_STANDARD_RULES` stay exactly as they are — no change to either or to the uploaded
skill. This amendment changes only how the DETERMINISTIC layer tags (executable/advisory), renders, and
dispatches a proposal; the LLM's recommendation content is untouched.

---

## a. What PEAD already solved (`robinhood_pead` — genuinely live since 2026-06-24)

- **Path (fractional equity):** pre-open `scan` writes an *intent* row only (no broker call — RH
  rejects pre-market fractional orders); at-open `reconcile` Phase 1 → `data_exec.place`
  (`data_exec.py:99`) → `RobinhoodBroker.place_order` (`robinhood.py:707`) →
  `_place_fractional_stock_order` (`:839`, `order_buy_fractional_by_price` /
  `order_sell_fractional_by_quantity`, GFD) → `_poll_fractional_fill` (`:799`, poll 1.5s ≤ 90s,
  records RH's *realized* qty/avg, cancels on timeout) → ledger + `paper_trade_record`. No-`id`
  response ⇒ `RobinhoodOrderError`, never a synthesized fill (phantom guard `:862`).
- **Reusable vs PEAD-specific:** reusable = `data_exec.place` chokepoint (staleness gate,
  execution-mode tag, fill persistence that never loses a real fill to a DB error), the
  `RobinhoodBroker` login/session/account-binding, `RiskAgent.evaluate`, live/paper selection,
  the no-id→raise discipline. PEAD-specific = the **entire fractional path** (in-code:
  "*PMCC / IC never set `order.fractional`*", `robinhood.py:713`), intent→open reconcile timing,
  `pending_order` table, `pead_pressures` exit, equal-$ notional sizing.
- **Safety rails + two gaps:** sizing = `position_pct × equity` (0.10), `max_concurrent 10`,
  pre-trade skips. **Gap 1:** per-trade notional cap is a **no-op for PEAD** (market orders →
  `ref_price=0` → `risk.py:213` cap skipped; `per_trade_risk_pct: 1.0` off). **Gap 2:**
  `auto_max_notional` / `auto_execute_caps` unenforced because **PEAD bypasses `ceo_graph`**
  (calls `data_exec.place` directly). *(Recorded separately for operator attention — see the
  PEAD notional-cap BACKLOG item + memory; out of scope here.)*
- **Not applicable to PMCC:** PEAD is fractional-equity / single-leg / market / synchronous-poll.
  **Every hard part of PMCC options execution is outside PEAD's solved surface.**

## b. What options execution adds — the crux

- **Multi-leg (answered concretely):** `order_option_spread` accepts a per-leg `expirationDate`, so
  a diagonal/calendar roll is a *legal* combo payload; the combo is atomic (§HEADLINE). **But PMCC
  does not use it** — a PMCC roll is two independent `order_buy/sell_option_limit` calls.
- **Approval level:** no code checks the account's options tier; handled only reactively (the
  compliance HTTP-400 returns no `id` → `RobinhoodOrderError`).
- **Limit vs market:** everything is a **limit** order; "market" is faked via a `0.0` sell-limit
  (`close_leap_urgent`, `preserve_market_sell=True` → `limit_price=0.0`, `robinhood.py:997`), which
  RH treats as marketable. Side effect: the per-trade cap is **skipped** (ref_price 0) → the urgent
  close has **no slippage cap**.
- **Assignment & exercise:** **WHOLLY ABSENT — not thin.** No handler anywhere for assignment,
  early exercise, or expiry settlement. A **0-DTE ITM short call at expiry (the signature PMCC
  risk) is unmonitored** at the broker layer. **Paper mode structurally could not have surfaced
  this** — the paper broker fills at limit/mid and never models assignment, so no amount of paper
  validation exercises it. It is invisible until real ITM expiry.
- **Buying power / margin:** no pre-trade check; submit-and-rely-on-RH-reject.
- **Rejection modes:** *caught* (→ `RobinhoodOrderError`, no phantom) = any no-`id` response.
  *Uncaught/degraded* = a **401 on an order call** (self-heal runs only in `snapshot()`, not order
  paths → 401 looks like a reject; if the order actually placed but the 401 was swallowed →
  phantom/double-propose risk) and **429** (no backoff).

## c. Gap list (the size of the build)

- **Reusable unchanged:** `data_exec.place`/`place_combo` chokepoints; `RobinhoodBroker`
  session/login/account-binding; `place_multi_leg`→`order_option_spread` (atomic, done);
  `validate_combo_cohesion`; no-id→raise phantom guard; `RiskAgent.evaluate`; the iron-condor
  combo-approval path (`combo_approval_view`, `pending_combo_registry`, `approvals/combos/{id}`)
  **as a pattern to copy**.
- **Reusable with modification:** the risk gate (needs cross-leg/combo awareness — today it
  resizes/rejects one leg without touching its pair); the manage-off-`snapshot` exit loop (needs
  options-position + assignment monitoring); the `execution_mode`/`standby`/`auto_execute` gating.
- **Net new:** (1) combo-tag PMCC roll legs (`is_multi_leg`/`combo_id`/`combo_direction`/
  `net_limit_price`/`ratio_quantity`/`option_id`); (2) combo dispatch for PMCC (route the grouped
  roll to `data_exec.place_combo` — `_group_orders_by_pair_id` is UI/parallelism only today);
  (3) combo-level approval for PMCC; (4) **assignment/exercise handling**; (5) options BP/margin
  pre-check; (6) order-path 401/429 handling; (7) ~~4-leg `roll_leap` decomposition/quarantine~~
  **SUPERSEDED 2026-07-22 — roll_leap is advisory-manual (AMENDMENT §A); no atomicity work. Replaced by
  structural executable-vs-advisory tagging + a fail-closed dispatch refusal (AMENDMENT §B)**; (8)
  slippage cap on the `0.0` urgent sell; (9) live options fill reconciliation.

## d. The risk surface paper mode hid (post-submission — no Bucket B gate covers it)

Every Bucket B gate (B2/B4/B7/B9) is a **proposal-layer** guard. Between submission and settled
position, none apply: (1) **non-atomic roll legs → naked position** (the central one); (2)
**assignment on a 0-DTE ITM short**; (3) **partial fill** of a multi-contract leg → leg imbalance;
(4) **unbounded slippage** on the `0.0` urgent sell; (5) one-leg **post-submission rejection** after
the pair filled; (6) **401/429 mid-order** (order path not self-healed) → silent reject or
placed-but-swallowed phantom/double; (7) **BP/margin rejection** mid-sequence; (8) **price
staleness** between proposal and fill → hung roll into 0-DTE or bad fill; (9) **orphan window** if
the process dies post-submit; (10) **parallel per-leg dispatch race** (the scheduler `asyncio.gather`s
the two legs through separate graph invocations).

## e. Phasing — the `auto_execute` flip (Phase D) is a distinct terminal gate, not a build step

- **Phase 0 — PREREQUISITE, DONE 2026-07-22.** `robinhood.py` + `data_exec.py` reconciled to prod
  (commit `557a39f`, LF-norm md5 `9bd4ddff` / `21ce7fd7`) so git carries the RH-auth 401 self-heal
  the entire options path runs through. Building on a git version that differs from prod is how the
  main.py fork happened; that risk is now retired for the engine layer. *(Remaining: the RH-auth
  **web** layer — `web/routes.py` + `home.html` + `rh_session_panel.html` — is still un-reconciled,
  non-boot-critical; separate lower-priority BACKLOG item.)*
- **Phase A — Atomic roll_short plumbing (`auto_execute` stays FALSE). SCOPE NARROWED 2026-07-22 to the
  2-leg roll_short ONLY — see AMENDMENT §A.** Combo-tag the two roll_short legs, route through
  `data_exec.place_combo` → `place_multi_leg`, add a combo-level HITL approval. Also (AMENDMENT §B) tag
  proposals executable-vs-advisory structurally and add the fail-closed dispatch refusal so a roll_leap can
  never be placed. **★ The former "4-leg `roll_leap` decompose-vs-quarantine" open decision is DELETED:
  roll_leap is permanently advisory-manual (the agent executes nothing), so there is no roll_leap atomicity
  work.** *Exit evidence:* a real 2-leg diagonal roll_short fires as one atomic combo via HITL on the live
  account, fills-or-rejects all-or-nothing (venue-reconciled), zero naked-leg outcomes across N HITL rolls;
  an advisory roll_leap renders as a non-executable recommendation and is refused at both dispatch
  chokepoints (a seeded advisory order RAISES rather than placing).
- **Phase B — Post-submission safety (`auto_execute` stays FALSE).** Assignment/exercise monitoring
  (0-DTE ITM short), options-position reconciliation, BP/margin pre-check, order-path 401/429
  handling, slippage cap on the urgent sell. *Exit evidence:* a 0-DTE ITM assignment handled cleanly
  (paper/HITL), reconciliation catches a seeded imbalance, an injected order-path auth failure
  recovers without phantom.
- **Phase C — Live shadow / 1-contract canary (`auto_execute` stays FALSE).** Full live plumbing
  HITL-only; a would-have-auto-executed shadow log; optionally a 1-contract canary on a single
  non-black-sheep symbol with the tightest `auto_execute_caps`, still HITL. *Exit evidence:* M
  sessions of HITL-approved live rolls executing atomically, acceptable slippage, zero naked legs,
  zero orphans, shadow decisions matching operator approvals.
- **Phase D — THE FLIP (separate, later, explicitly Board-gated; NOT the tail of Phase C).** Flip
  `auto_execute: true` with `auto_execute_caps` active + black-sheep `require_approval`. **This is
  its own decision, requiring the Phase A–C evidence AND an explicit per-decision Board
  authorization** — consistent with the standing rule that deploy/automation authorization is
  per-decision and explicit, with no standing/autonomous go. **Plumbing being ready is a
  precondition, not a trigger.**

---

*Investigation basis: three read-only code traces 2026-07-22 (PEAD live path; RH broker
session/401/options; PMCC order routing). Key files: `trading_corp/brokers/robinhood.py`,
`trading_corp/agents/data_exec.py`, `trading_corp/agents/divisions/pmcc_robinhood.py`,
`trading_corp/graph/ceo_graph.py`, `trading_corp/agents/strategies/robinhood_joint_iron_condor.py`,
`trading_corp/main.py`. Audit context: memory [[pmcc-logic-audit-2026-07-21]],
[[pmcc-bucketb-deployed-live-2026-07-22]].*
