# Phase 2a — Roster split: read-only scoping + checkpoint plan (FOR RATIFICATION)

> **LIVE-MONEY STATUS (leads):** `poly_kalshi_mlb` is **LIVE + ARMED** — engine PID 760172,
> `auto_execute=true` / `dry_run=false` / `halted=false`, $5/trade, 4 whales, $100 loss-halt +
> 25/day count-halt, mark poller ~60s, **1 open position** (pre-game BAL@TB). This session was
> 100% branch-only + read-only. **Nothing built, nothing deployed, live loop untouched.** No prod
> mutation happens until the operator ratifies this plan and runs the batched deploy.

Branch `poly-kalshi-phase2a-2026-08-16` @ off `386074c` (tip of `poly-kalshi-phase2b-cp3-2026-08-16`).
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
- **main.py:1513-1514** passes the yaml values straight into the constructor
  (`roster_actor=_pk_cfg.get("roster_actor", ...)`, `roster_key=_pk_cfg.get("roster_key", ...)`).
  ⇒ **Retargeting the live roster to a new key is CONFIG-ONLY — zero code change to the live loop.**
- **Paper sim** `trading_corp/agents/strategies/polymarket_copy_trader.py:761` `_load_selected_whales()`
  reads `load_agent_state("polymarket_copy_trader", "selected_whales")`; consumed in `run()` at `:220`.
  ⇒ Both live + paper read `polymarket_copy_trader/selected_whales` today = **the 4 whales are papered
  AND live-traded simultaneously** (the accepted double-state to be removed).
- `strategies.yaml:1788` `division: poly_kalshi_mlb` is ALREADY corrected (was inert; main.py hardcodes
  `strategy="poly_kalshi_mlb"` at `:1498`). No action needed there.

### 1.2 Atomic primitive — available, does not exist yet
- `trading_corp/persistence/db.py:637-639` `connect()` opens sqlite with `isolation_level=None`
  (Python-level autocommit) → a manual `BEGIN IMMEDIATE … COMMIT/ROLLBACK` wrapping N upserts is a
  valid single transaction. `set_agent_state` (`:669-690`) is a single-key autocommit upsert.
- In-repo precedent for the exact pattern: `trading_corp/path_logger/store.py:119` (`conn.execute("BEGIN IMMEDIATE")`).
- `set_agent_state_multi` **does not exist** (grep clean) → **new helper in db.py** (db.py is NOT a
  byte-locked file).

### 1.3 Promote/demote endpoints today — non-atomic, wrong axis
- Four watchlist endpoints in `trading_corp/web/routes.py`: kalshi promote/demote (`:2656,:2700`),
  polymarket promote/demote (`:2952,:3020`). ALL move **watch_only ⇄ selected_whales** (watchlist→paper),
  each doing **2 separate `set_agent_state` calls** (selected + pinned) — the non-atomic pattern.
- polymarket demote (`:3020`) calls `polymarket_copy_trader.force_close_whale_positions` (`:3033`) to
  flatten the paper book before removing from selected/pinned.
- ⇒ These are watchlist→paper and stay as-is. **Phase 2a needs NEW paper⇄live endpoints**
  (`selected_whales ⇄ poly_kalshi_mlb/live_whales`).

### 1.4 Paper Telegram — VERIFIED NO-OP (item is already satisfied)
- `polymarket_copy_trader.py` (the PAPER farm): **no Telegram send anywhere** — the only "telegram"
  string is a docstring at `:895` inside `record_exit_fill` ("LIVE only — the paper path returns early
  in `main` and never reaches this"). No `notify_fn`, no `channel.push`. main.py:1450 constructs it with
  `db_url` only.
- The only poly_kalshi Telegram is `poly_kalshi_executor.py:351` `_notify_live_copy`, gated by
  `status == "placed" and self._notify_fn is not None` (`:346`) — **LIVE placements only**
  (`notify_fn=channel.push` wired at main.py:1504, dcebfcc).
- `trading_corp/comms/*` references **neither** `polymarket_copy_trader` **nor** `poly_kalshi` (grep clean)
  → the paper farm is never swept into any lifecycle/status notifier.
- ⇒ **"Kill Telegram for the PCT paper farm" = nothing to kill; already live-only.** See FORK E.

### 1.5 ★ Refresh-script re-add hazard (NEW — beyond the ratified plan)
- `trading_corp/scripts/refresh_polymarket_whales.py:585-588` writes `write_records` to
  `polymarket_copy_trader/selected_whales`. **DEFAULT mode is pins-only** (`:538-544,:780-784`):
  `write_records` = the merged **`pinned_whales`** set.
- ⇒ On any weekly refresh run, whatever is in `pinned_whales` is written into `selected_whales`. If a
  promoted (LIVE) whale is still pinned, a refresh **silently re-adds it to the paper roster** →
  resurrects the double-state.
- **Consequence for the design:** the atomic move must touch **3 keys** (remove from `selected_whales`
  AND `pinned_whales`, add to `live_whales`), and the paper sim must **read-time subtract** `live_whales`
  as the invariant backstop (survives any stored-state corruption). The plan's 2-key sketch (`PLAN.md:104-105`)
  is insufficient on its own.

### 1.6 Cutover hazard (timing)
- Retargeting `roster_key → live_whales` takes effect only at restart. If `live_whales` is empty at
  restart, the live loop reads 0 whales → **live copying goes silently dark** while the 4 whales sit in
  `selected_whales` (now paper-only).
- ⇒ The deploy must **seed `poly_kalshi_mlb/live_whales`** with the current 4 wallets AND remove them
  from `selected_whales`+`pinned_whales`, applied immediately before the restart. Dark window = seed→restart
  (~2.5 min boot); low risk given low-freq whales + 0 orders to date, but must be explicit + avoid the
  15:40–15:58 ET window.

---

## 2. Design (the core Phase 2a guarantee)

**Roster keys after 2a:** live = `agent_state[poly_kalshi_mlb/live_whales]`; paper =
`agent_state[polymarket_copy_trader/selected_whales]` (minus any wallet in `live_whales`).

**Invariant:** `set(wallets in live_whales) ∩ set(wallets in selected_whales) == ∅`, asserted after every
move and on boot. Wallet-keyed (identity = wallet, lowercased; display name irrelevant).

**Atomic move (3-key, single `set_agent_state_multi` = one `BEGIN IMMEDIATE…COMMIT`):**
- **Promote (paper→live):** `[(polymarket_copy_trader, selected_whales, sel−w), (polymarket_copy_trader,
  pinned_whales, pin−w), (poly_kalshi_mlb, live_whales, live+w)]`. On exception → ROLLBACK → whale stays
  paper-only.
- **Demote (live→paper):** `[(poly_kalshi_mlb, live_whales, live−w), (polymarket_copy_trader,
  selected_whales, sel+w), (polymarket_copy_trader, pinned_whales, pin+w)]`. On exception → ROLLBACK →
  whale stays live-only.

**Read-time subtract (invariant backstop):** in the paper sim, filter out any wallet present in
`poly_kalshi_mlb/live_whales` **at consumption** (in `run()` after `_load_selected_whales()`), NOT inside
`_load_selected_whales()` itself — because `_apply_autopause_filter` does a read-modify-write of
`selected_whales` (`polymarket_copy_trader.py:632,:725`) and must keep operating on the raw stored roster,
never a filtered view.

**Semantics (ruled):** promote → whale stops papering, live P&L starts fresh at $0 (poly_kalshi_mlb has
no prior rows for it), paper history retained behind the epoch. demote → leaves live (live history
retained under poly_kalshi_mlb), resumes papering. **No auto-promotion** — operator-driven dashboard
buttons only.

---

## 3. Forks — STOP-AND-REPORT (operator resolves before build)

- **FORK A — exclusion mechanism.** Recommend **BOTH**: the atomic move keeps stored rosters clean AND
  the paper sim read-time-subtracts `live_whales` (survives the §1.5 refresh re-add). *(Recommended.)*
  Alternative: rely on the move alone (fragile to §1.5).
- **FORK B — promote: flatten the whale's open PAPER book, or leave it?** demote-from-watch flattens via
  `force_close_whale_positions`. On promote paper→live, the whale's open paper lots would otherwise sit
  open forever (the excluded paper sim never emits their exits). **Recommend flatten-on-promote**
  (synthetic sells → clean, complete paper history) for symmetry. Alternative: freeze the open paper lots.
- **FORK C — demote with an OPEN LIVE Kalshi position.** The live loop stops watching the whale → its
  exit-copy never fires → the position **rides to settlement** (consistent with hold-to-resolution; the
  $100 settlement sweep still books it; no live-broker action on demote). **Recommend ride-to-settlement.**
  Alternative: force-flatten the live position on demote (a live market SELL — touches the live broker,
  more risk).
- **FORK D — cutover seeding.** **Recommend an operator-run atomic agent_state migration** (seed
  `live_whales` from the current 4 + remove from `selected_whales`/`pinned_whales`, one `set_agent_state_multi`),
  verified read-only, reversible. Alternative: a one-shot boot migration in code (implicit, harder to verify).
- **FORK E — paper Telegram.** Confirm the item is **satisfied** (§1.4: nothing sends paper Telegram
  today; poly_kalshi Telegram is live-only). If you meant a *different* alert surface, point me at it.

---

## 4. Proposed checkpoints (build only after ratification; each = build → STOP → report → your go)

- **CP1 — Scoping sign-off (this doc).** You ratify §2 + resolve FORKS A–E.
- **CP2 — Atomic primitive.** Add `set_agent_state_multi(updates, db_url)` to `db.py`
  (`BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK`). Tests: multi-key commit; **forced exception mid-move → ROLLBACK
  → zero partial write**. No prod touch.
- **CP3 — Roster split (config + read-time exclusion + boot invariant).** `strategies.yaml`
  `roster_actor: poly_kalshi_mlb` / `roster_key: live_whales`; paper sim read-time subtract of `live_whales`;
  boot-time `assert_roster_disjoint`. Tests: paper excludes a live whale; invariant holds. NOT deployed.
- **CP4 — Atomic paper⇄live endpoints + pin-back test.** New promote/demote routes, each ONE 3-key
  `set_agent_state_multi`. Tests: forced-crash mid-move → whale in exactly ONE roster; **MUST-TEST
  round-trip promote→demote→re-promote — invariant `live ∩ paper == ∅` holds at every step**, paper
  history gap during the live period is tolerated, landing state clean. Implements FORK B/C decisions.
- **CP5 — Cutover migration runner (operator-run, authored here).** az `@file` runner: atomically seed
  `poly_kalshi_mlb/live_whales` = current 4 live wallets + remove from `selected_whales`/`pinned_whales`.
  Read-only verify: live_whales=4, selected_whales excludes them, invariant ∅, reversible.
- **CP6 — Batched deploy + verify (operator-run).** Ships CP2–CP5 + already-committed `8dc4d97` + `dcebfcc`.
  Sequence: install files → run CP5 migration → **restart** → verify. Drift-gate against the **BOX md5s**
  (not prod-live git — §handoff). Verify: **re-ARM** (new PID / auto_execute=true / dry_run=false /
  halted=false — THE critical check), live loop loads 4 from `live_whales`, paper excludes them, boot
  invariant green, 0 boot tracebacks, poller ticks, shared files byte-unchanged. Flag prod-live-git
  catch-up (18db30e → 3706a3a → batch tip). Avoid 15:40–15:58 ET.

Files touched across the build: `db.py`, `config/strategies.yaml`, `polymarket_copy_trader.py`,
`trading_corp/web/routes.py`, boot invariant (main.py or a helper) + tests + the CP5 runner. **Shared
byte-locked files untouched** (diff-verified each checkpoint).

---

## 5. Verification bar (end-to-end)
- Atomic move: forced-crash mid-move leaves the whale in exactly ONE roster (unit).
- Invariant `live ∩ paper == ∅` after every move and on boot (unit + boot assert).
- Round-trip promote→demote→re-promote clean; paper no longer papers live whales (unit).
- Post-deploy: live loop ARMED/unhalted, reads 4 from `live_whales`; paper excludes them; poly_kalshi
  suite green (62/62 + new atomic/exclusion/endpoint/round-trip tests); `-p no:pytest_ethereum`.
