# Phase 2a — Roster split: scoping + checkpoint plan (operator decisions folded in; FOR RATIFICATION)

> **LIVE-MONEY STATUS (leads):** `poly_kalshi_mlb` is **LIVE + ARMED** — engine PID 760172,
> `auto_execute=true` / `dry_run=false` / `halted=false`, $5/trade, 4 whales, $100 loss-halt +
> 25/day count-halt, mark poller ~60s, **1 open position** (pre-game BAL@TB). This session is
> 100% branch-only + read-only. **Nothing built, nothing deployed, live loop untouched.** No prod
> mutation happens until the operator ratifies this plan and runs the batched deploy.

Branch `poly-kalshi-phase2a-2026-08-16` off `386074c` (tip of `poly-kalshi-phase2b-cp3-2026-08-16`).
Shared byte-locked files (`kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py`) — **untouched**;
none of the edits below go near them.

---

## 1. Scoping findings (all file:line verified against current branch code)

### 1.1 Roster storage today — the double-state, precisely
- **Live loop** `trading_corp/agents/strategies/poly_kalshi_copy_trader.py:69` `_load_roster()` reads
  `load_agent_state(self._roster_actor, self._roster_key)` **fresh every cycle** (`:211`). Constructor
  defaults `roster_actor="polymarket_copy_trader", roster_key="selected_whales"` (`:41,:53-54`).
- **Config** `config/strategies.yaml:1798-1799`: `roster_actor: polymarket_copy_trader`,
  `roster_key: selected_whales` → the live loop reads the SAME key as the paper sim.
- **main.py:1513-1514** passes the yaml values straight into the constructor.
  ⇒ **Retargeting the live roster to a new key is CONFIG-ONLY — zero code change to the live loop.**
- **Paper sim** `trading_corp/agents/strategies/polymarket_copy_trader.py:761` `_load_selected_whales()`
  reads `load_agent_state("polymarket_copy_trader", "selected_whales")`; consumed in `run_scan_cycle`.
  ⇒ Both live + paper read `polymarket_copy_trader/selected_whales` today = **the 4 whales are papered
  AND live-traded simultaneously** (the accepted double-state to be removed).
- `strategies.yaml:1788` `division: poly_kalshi_mlb` is ALREADY corrected. No action needed there.

### 1.2 Atomic primitive — available, does not exist yet
- `trading_corp/persistence/db.py:637-639` `connect()` opens sqlite with `isolation_level=None`
  (autocommit) → a manual `BEGIN IMMEDIATE … COMMIT/ROLLBACK` wrapping N upserts is a valid single
  transaction. `set_agent_state` (`:669-690`) is a single-key autocommit upsert.
- In-repo precedent: `trading_corp/path_logger/store.py:119` (`conn.execute("BEGIN IMMEDIATE")`).
- `set_agent_state_multi` **does not exist** (grep clean) → **new helper in db.py** (NOT byte-locked).

### 1.3 Promote/demote endpoints today — non-atomic, wrong axis
- Four watchlist endpoints in `trading_corp/web/routes.py`: kalshi (`:2656,:2700`), polymarket
  (`:2952,:3020`). ALL move **watch_only ⇄ selected_whales** (watchlist→paper), each doing **2 separate
  `set_agent_state` calls** — the non-atomic pattern.
- polymarket demote (`:3020`) calls `polymarket_copy_trader.force_close_whale_positions` (`:3033`) to
  flatten the paper book (synthetic sells) before removing from selected/pinned. **This is the reusable
  flatten path for decision (0).**
- ⇒ Phase 2a needs NEW **paper⇄live** endpoints (`selected_whales ⇄ poly_kalshi_mlb/live_whales`).

### 1.4 ★ Paper-farm Telegram path — FOUND (corrects an earlier WRONG finding)
> **CORRECTION.** An earlier pass concluded "no paper Telegram" after checking only the paper *agent
> file* (`polymarket_copy_trader.py`) and the poly_kalshi executor. That was WRONG — the operator
> receives PCT paper alerts daily. The send lives in the **loop orchestration in `main.py`**, not the
> agent file. Traced end-to-end:
- **Send primitive** `main.py:5010-5022` `_push_copy_card(channel, order, ext, *, tag)` →
  `await channel.push("🟣 Polymarket copy {ENTRY|EXIT} {SIDE} @{whale} (${copy_size}) on \"{title}\" — {tag}.")`.
- **Paper trigger** `main.py:5045-5056`: the `not is_live_armed` branch of `_handle_copy_order_placement`
  logs `would_have_placed` **and** calls `_push_copy_card(..., tag="logged")`.
- **Caller** `_scheduled_polymarket_copy_trader_loop` (`main.py:5234`, dispatch at `:5371`) routes every
  emitted PCT `ProposedOrder` through `_handle_copy_order_placement` (`channel=channel` wired at
  `main.py:1454`). The PCT paper sim (`polymarket_copy_trader`, `auto_execute:false`, never in
  `--live-divisions`) is **never** live-armed → **always** hits the `:5056` paper push.
- **Why it matches the daily messages:** every paper copy entry/exit → one "🟣 Polymarket copy … logged"
  card; the sim polls continuously → a daily stream of paper-copy Telegram cards.
- **Disambiguation:** the other "Polymarket" push at `main.py:4194` (`"📊 Polymarket … divergence …"`) is a
  DIFFERENT loop (the arb/divergence scanner), NOT the copy farm. `_notify_live_copy`
  (`poly_kalshi_executor.py:351`) is the LIVE poly_kalshi division's own alert (live-placement only).
- **The kill (decision 3):** guard/drop the **`:5056`** `_push_copy_card` call (paper branch only). The
  live-armed branch cards (`:5081/:5109/:5115`) and the poly_kalshi `_notify_live_copy` are **retained**
  → live-money alerts only; PCT paper farm goes silent. Keep the `would_have_placed` audit (data rail).

### 1.5 ★ Refresh-script re-add hazard (beyond the ratified plan)
- `trading_corp/scripts/refresh_polymarket_whales.py:585-588` writes to
  `polymarket_copy_trader/selected_whales`. **DEFAULT is pins-only** (`:538-544,:780-784`): it writes the
  merged **`pinned_whales`** set. ⇒ A promoted (LIVE) whale still in `pinned_whales` gets **silently
  re-added to the paper roster on the next weekly refresh** → resurrects the double-state.
- **Consequence:** the atomic move must touch **3 keys** (remove from `selected_whales` AND
  `pinned_whales`, add to `live_whales`), and the paper sim must **read-time subtract** `live_whales` as
  the invariant backstop.

### 1.6 Cutover timing
- Retargeting `roster_key → live_whales` takes effect only at restart. If `live_whales` is empty at
  restart, the live loop reads 0 whales → **live copying goes silently dark**. ⇒ The cutover runner
  (decision 2) must seed `live_whales` + remove the 4 from `selected_whales`/`pinned_whales` immediately
  before the restart. Dark window = seed→restart (~2.5 min boot); low risk (low-freq whales, 0 orders to
  date); avoid the 15:40–15:58 ET window.

---

## 2. Design (the core Phase 2a guarantee)

**Roster keys after 2a:** live = `agent_state[poly_kalshi_mlb/live_whales]`; paper =
`agent_state[polymarket_copy_trader/selected_whales]` (minus any wallet in `live_whales`).

**Invariant:** `set(live_whales wallets) ∩ set(selected_whales wallets) == ∅`, asserted after every move
and on boot. Wallet-keyed (identity = wallet lowercased; display name irrelevant).

**Atomic move (3-key, single `set_agent_state_multi` = one `BEGIN IMMEDIATE…COMMIT`):**
- **Promote (paper→live):** `[(polymarket_copy_trader, selected_whales, sel−w), (polymarket_copy_trader,
  pinned_whales, pin−w), (poly_kalshi_mlb, live_whales, live+w)]`. On exception → ROLLBACK → stays paper-only.
- **Demote (live→paper):** `[(poly_kalshi_mlb, live_whales, live−w), (polymarket_copy_trader,
  selected_whales, sel+w), (polymarket_copy_trader, pinned_whales, pin+w)]`. On exception → ROLLBACK →
  stays live-only.

**Read-time subtract (backstop):** in the paper sim, filter out any wallet in `poly_kalshi_mlb/live_whales`
**at consumption** (after `_load_selected_whales()`), NOT inside `_load_selected_whales()` — because
`_apply_autopause_filter` read-modify-writes `selected_whales` (`:632,:725`) and must operate on the raw
stored roster, never a filtered view.

---

## 3. Operator decisions (RESOLVED — folded into the plan)

- **(0) PROMOTE paper→live → FLATTEN ON PROMOTE.** Reuse `force_close_whale_positions` (the exact path
  demote-from-watch uses, `routes.py:3033`) → synthetic sells close the paper book at current mark; paper
  history stays complete; whale arrives live clean. **No new flatten logic.**
- **(1) DEMOTE live→paper → RIDE TO SETTLEMENT.** No force-flatten, no live-broker action on demote. The
  open live position rides to natural resolution; the $100 settlement sweep books its P&L. **MUST-TEST:**
  a demoted whale's open LIVE position keeps being MARKED by the poller AND settles AND books to the LIVE
  division correctly, even though the whale is off the live roster.
- **★ Asymmetry is intentional and correct:** paper positions **flatten** on state-change (synthetic,
  at mark); live positions **ride** to settlement (real money, hold-to-resolution). Do NOT unify them.
- **(2) CUTOVER SEEDING → operator-run `pk_*.ps1`.** A PowerShell runner (not a raw `az`) doing ONE
  atomic `set_agent_state_multi`: seed `poly_kalshi_mlb/live_whales` = the 4 wallets AND remove those 4
  from `selected_whales`/`pinned_whales`, single transaction. Verify-then-mutate, reversible, run right
  before the restart; assert `live ∩ paper == ∅` after (4 in live_whales, gone from selected).
- **(3) PAPER TELEGRAM → KILL.** Silence the `main.py:5056` paper-branch `_push_copy_card` (§1.4). Live
  alerts retained. (If the operator also sees a separate paper *resolution/digest* alert, flag it — the
  daily paper-**trade** cards are `_push_copy_card`.)

---

## 4. Proposed checkpoints (build only after ratification; each = build → STOP → report → your go)

- **CP1 — Scoping sign-off (this doc).** Operator ratifies §2 + §3.
- **CP2 — Atomic primitive.** Add `set_agent_state_multi(updates, db_url)` to `db.py`
  (`BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK`). Tests: multi-key commit; **forced exception mid-move → ROLLBACK
  → zero partial write**. No prod touch.
- **CP3 — Roster split (config + read-time exclusion + boot invariant).** `strategies.yaml`
  `roster_actor: poly_kalshi_mlb` / `roster_key: live_whales`; paper sim read-time subtract of
  `live_whales`; boot-time `assert_roster_disjoint`. Tests: paper excludes a live whale; invariant holds.
  NOT deployed.
- **CP4 — Atomic paper⇄live endpoints + flatten-on-promote + pin-back MUST-TEST.** New promote/demote
  routes, each ONE 3-key `set_agent_state_multi`; promote reuses `force_close_whale_positions` (decision 0);
  demote does NO live-broker action (decision 1). Tests: forced-crash mid-move → whale in exactly ONE
  roster; **round-trip promote→demote→re-promote — invariant holds at every step**, paper-history gap
  during the live period tolerated, landing state clean; **demote-open-live-position MUST-TEST** (marks +
  settles + books to LIVE division off-roster).
- **CP5 — Paper-Telegram kill + cutover runner.** (a) Guard the `main.py:5056` paper `_push_copy_card`
  (live-only alerts). (b) Author `cc\pk_cutover_seed.ps1` (operator-run az `@file`, ASCII/no-BOM,
  `[scriptblock]::Create`-validated) doing the atomic 3-key cutover (decision 2), read-only verify + assert
  `live ∩ paper == ∅`.
- **CP6 — Batched deploy + verify (operator-run).** Ships CP2–CP5 + already-committed `8dc4d97` + `dcebfcc`.
  Sequence: install files → run CP5 cutover → **restart** → verify. Drift-gate against the **BOX md5s**
  (not prod-live git). Verify: **re-ARM** (new PID / auto_execute=true / dry_run=false / halted=false),
  live loop loads 4 from `live_whales`, paper excludes them, boot invariant green, 0 boot tracebacks,
  poller ticks, **no PCT paper Telegram**, shared files byte-unchanged. Flag prod-live-git catch-up
  (18db30e → 3706a3a → batch tip). Avoid 15:40–15:58 ET.

Files touched: `db.py`, `config/strategies.yaml`, `polymarket_copy_trader.py`, `main.py` (paper-telegram
guard + boot invariant), `trading_corp/web/routes.py` + tests + `cc\pk_cutover_seed.ps1`. **Shared
byte-locked files untouched** (diff-verified each checkpoint).

---

## 5. Verification bar (end-to-end)
- Atomic move: forced-crash mid-move leaves the whale in exactly ONE roster (unit).
- Invariant `live ∩ paper == ∅` after every move and on boot (unit + boot assert).
- Round-trip promote→demote→re-promote clean; paper no longer papers live whales (unit).
- Demote-open-live: off-roster position still marks, settles, books to LIVE division (unit).
- Post-deploy: live loop ARMED/unhalted, reads 4 from `live_whales`; paper excludes them; **no paper
  Telegram**; poly_kalshi suite green (62/62 + new atomic/exclusion/endpoint/round-trip/telegram-guard
  tests); `-p no:pytest_ethereum`.
