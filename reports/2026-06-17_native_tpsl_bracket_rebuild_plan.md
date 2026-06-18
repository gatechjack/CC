# Rebuild the BitUnix bracket exit on the native `/tpsl/` order family

## Context
The 2026-06-17 live validation (order `e1758fc9`) proved bitunix is live-armed and the bot can bank a TP
(first-ever live `exit_kind=tp`, +0.03486 USDT), **but** exposed that the bracket uses the **wrong endpoint
family**: it places each TP as a standalone reduce-only LIMIT via `/futures/trade/place_order`. That broke the
SL-trail (POSTed a non-existent path → 404), weakened fill-tracking (the close was only seen via the reconciler's
position-divergence + P2 auto-book, not a clean callback), and left the SL auto-reduce untested. Research
(`reports/2026-06-17_bitunix_native_multitp_research.md`, commit 567b2c1) found BitUnix's **native `/api/v1/futures/tpsl/...`
order family** — the same mechanism the operator's manual UI uses — which gives the 3-leg ladder **+** native OCO
**+** an auto-reducing SL **+** clean fill-tracking, with none of the separate-limit fragility. The "ladder vs
robustness tradeoff" was an artifact of the wrong implementation. This plan rebuilds the bracket on the native family.

## Part 0 — current deploy state: **SAFE AS-IS (rebuild = improvement, not emergency)**
Live read-only health (2026-06-18 ~02:38 UTC) — 8/8 PASS: PID 2926399 running/NRestarts=0; bitunix is the **real**
broker (`Registered … paper=False`) with the staleness gate loaded + `execution_mode=live`; **flat** (no open
position); **no** divergence/orphan/halt since the validation's clean auto-book; **E2.5 tags live orders** (`e1758fc9`
execution_mode=`live`, no binding errors); config preserved (`execution_mode: live`, `maker_entry_enabled: false`
B2-OFF, risk.yaml DD-cap **0.99**); **freeze-fix holds** (no journal gaps >90s over 3h); bracket `bd639224` on disk.
- At current ~0.0004 BTC sizing the bracket **degrades to 1 leg = full close**, so the broken SL-trail and untested
  auto-reduce **never engage**, and the B1 MARKET stop always guards → **safe to run as-is indefinitely on 1-leg
  trades** until the rebuild ships. **No must-fix-now items.**
- Minor flag (NOT a blocker): the P2 auto-book recorded `e1758fc9` as `result=loss` while the venue PnL was **+**0.03486
  (a win) — a result-sign/PnL quirk in the auto-book's *estimate*; the rebuild's explicit `/tpsl/get_history` fill
  read books the **real** TP fill and fixes this.

## Native `/tpsl/` endpoints (grounded in docs + 567b2c1)
| Endpoint | Method | Key params | Role |
|---|---|---|---|
| `/api/v1/futures/tpsl/place_order` | POST | `symbol, positionId, tpPrice, tpQty (PARTIAL), tpStopType, tpOrderType, tpOrderPrice?` | one **TP leg** w/ partial qty → call N× for the ladder |
| `/api/v1/futures/tpsl/position/place_order` | POST | `symbol, positionId, slPrice, slStopType` (no qty) | **auto-reducing** position SL ("closes by position qty *at that time*") |
| `/api/v1/futures/tpsl/position/modify_order` | POST | `symbol, positionId (REQUIRED), slPrice, slStopType` | **SL-trail** (price-only) — the **correct** path (bot 404'd on `/tpsl/modify_position_tp_sl_order`) |
| `/api/v1/futures/tpsl/get_pending_orders` · `/get_history_orders` | GET | `symbol?` | clean fill-tracking / OCO verify |
`positionId` source = `/futures/position/get_pending_positions` response — **not currently captured** into `Position.extra`.

## Rebuild design
- **Entry / B1 stop — UNCHANGED (hard guard):** the entry still attaches the `slPrice` MARKET stop atomically
  (`bitunix.py:1235-1297`). B1 stays a **guaranteed-fill MARKET** stop; the bot only ever moves its **price**, never its qty.
- **TP ladder → native:** at fill, place the 3 legs as N× `tpsl/place_order` with partial `tpQty` (0.25/0.50/0.25,
  Board min-leg 0.0003 + graceful degrade) — reusing `build_bracket_legs` unchanged.
- **SL-trail → fixed path:** `tpsl/position/modify_order` (correct path + mandatory `positionId`), driven by
  `decide_sl_move` unchanged (the (b)+(c) hybrid: TP1→breakeven, TP1+TP2→TP1, tighten-only, price-only).
- **Auto-reducing SL:** the position-level SL covers the shrinking position as legs fill (venue-managed). **OPEN:
  whether the trail-able SL is the entry-attached `slPrice` or a separate `tpsl/position/place_order` SL — resolved
  by the operator UI capture (below), NOT assumed.**
- **Fill-tracking:** interim KEEP the reconciler's qty-reduction detection + P2 auto-book (robust to degradation);
  Phase C (optional) adds `tpsl/get_pending_orders`/`get_history_orders` polling for explicit TP-fill events.

### What changes (from the code map) — 6 files
- **`brokers/bitunix.py`** — KEEP entry `slPrice` (1235-1297); **REPLACE** `place_resting_reduce_only_limit` (1845-1888)
  → new `place_tpsl_order` (`tpsl/place_order`, partial `tpQty`); **FIX** `modify_position_sl` (1918-1969) endpoint +
  make `positionId` mandatory; **ADD** `positionId` to `Position.extra` in `get_pending_positions` (~1300-1359);
  **NEW** `get_pending_tpsl`/`get_tpsl_history` (Phase C).
- **`agents/divisions/bitunix_futures_observer.py`** — KEEP `_place_bracket_exits` structure + the `bracket_placed`
  audit (3341-3425); **REPLACE** the per-leg call (3385) → `place_tpsl_order`.
- **`agents/divisions/bitunix_position_reconciler.py`** — **REFACTOR** `move_bracket_sls` (1030-1136) to thread
  `positionId` into `modify_position_sl`; KEEP qty-based TP-fill detection + P2 auto-book + the divergence audit.
- **`agents/divisions/bitunix_bracket.py`** — KEEP `build_bracket_legs` + `decide_sl_move` (pure, unchanged).
- (Confirm at build: `models.py`/`logger.py`/`data_exec.py` untouched.)

## Sequenced build + test (mocked / fundless; same-env regression gate)
1. **positionId sourcing** — surface `positionId` into `Position.extra` from `get_pending_positions`. *Test:* parse a sample position payload, assert `positionId` present.
2. **`broker.place_tpsl_order`** (new) — `tpsl/place_order` body w/ partial `tpQty`. *Test:* body-shape units (tpPrice/tpQty/positionId/stop types), mocked `_request`, error paths.
3. **Wire `_place_bracket_exits`** → `place_tpsl_order` (swap the reduce-only-limit call). *Test:* bracket-placement integration (3-leg / 2-leg / 1-leg degrade) vs the new method, mocked broker; assert `bracket_placed` audit shape unchanged.
4. **Fix `modify_position_sl`** (correct path + mandatory `positionId`) + refactor `move_bracket_sls` to pass it. *Test:* SL-move units (path + positionId present), `decide_sl_move` integration (hybrid + tighten-only, unchanged).
5. **(Phase C, optional)** `get_pending_tpsl`/`get_tpsl_history` + explicit fill-tracking. *Test:* poll-based fill detection; fixes the win/loss mis-class.
6. **Full-suite regression** on a fresh worktree vs the known baseline — **zero NEW regressions** (the empty-DB fresh-worktree 2-test delta is the known artifact); `build_bracket_legs`/`decide_sl_move` tests stay green.
- *New vs today:* the broker `/tpsl/` methods + `positionId` plumbing. *Reused:* the pure leg math, the SL-move hybrid, the audits, the degrade rules, P2 auto-book.

## VERIFY-ON-LIVE / operator UI capture (INPUTS — not assumptions; gate step 4 + deploy)
- **UI network capture (operator):** in the BitUnix UI, set 3 TPs + an auto-reducing SL on a position, capture the
  network tab → pin the **exact** call sequence/params (is it `tpsl/place_order` ×3 partial-qty + `tpsl/position/place_order`
  for the SL? does the entry `slPrice` coexist or get replaced? which SL is the trail-able one?). This resolves the OPEN
  SL-mechanism question — **do not guess it.**
- **Auto-reduce confirmation:** confirm the position SL auto-reduces while N `/tpsl/` TP legs coexist (no `30038`) on a real multi-leg position.

## Multi-leg validation gate (the true 3-leg test)
The 1-leg validation is done. A real 3-leg test needs an entry **≥0.0012 BTC (~$79)**. Sizing is **hardcoded** in
`TIER_SIZING` (`observer.py:207-212`), NOT config — so the 3-leg test needs **(a)** a natural PREMIUM entry (~2×
STANDARD; may reach ≥0.0012 but unreliable + the variable vol-multiplier), **(b)** a deliberate `TIER_SIZING` code
bump shipped *with* the rebuild then reverted, or **(c)** wait. **Decision deferred to deploy time** — flagged as the
gate to a true 3-leg validation, separate from the rebuild build itself.

## Deploy approach (drift-gated, targeted, remote-mobile) — FUTURE step, not this plan
- **File set:** the bitunix exit files only (`bitunix.py`, `bitunix_futures_observer.py`, `bitunix_position_reconciler.py`,
  `bitunix_bracket.py` if touched). **NEVER** `main.py`/`db.py` (cutover's) or any polymarket file.
- **Pattern:** rebuild on a **fresh worktree** off the deployed base; **dual-mode drift-gate** each file (prod-current
  md5 → new target); md5-gated apply (`backup *.bak-pre-tpsl-rebuild-<date>` → py_compile → atomic-mv → re-verify);
  **NO restart in the script**. Remote-mobile: agent drives scp/apply over SSH (§4-authorized for the sequence);
  **operator runs the ONE restart** (`az run-command` / `sudo systemctl restart`).
- **VERIFY (post-restart):** engine up + files at target md5; bitunix still real broker (paper=False) + gate fires;
  a live entry's TP legs REST as `/tpsl/` orders (`get_pending_orders` shows them, **no 30038**); the SL-trail works
  (`position_sl_update {moved:true}`, no 404); SL auto-reduces on a partial; reconciler clean; **no main/db touch**.

## Risk / rollback
- The `/tpsl/` behavior (auto-reduce while legs coexist, the trail) is **docs-confirmed but not yet bot-live-proven**
  → the UI capture + the multi-leg validation are the gates. **Fail-soft preserved:** the entry-attached B1 MARKET
  stop always guards the catastrophic downside (unchanged), so even a misbehaving `/tpsl/` layer can't leave the
  position unprotected.
- **Rollback:** restore `*.bak-pre-tpsl-rebuild` + restart → the current (safe-at-1-leg) bracket.
- **Hard guards:** B1 SL stays MARKET, price-only moves (never qty); never main/db/cutover/polymarket files; never re-introduce the standalone-limit path.

## Recommended sequence / what blocks starting now
- **Start now (no blockers):** build steps 1-3 (positionId sourcing + `place_tpsl_order` + wiring) + the
  `modify_position_sl` path fix + their tests — all groundable from the docs/research.
- **Gated on the UI capture:** finalize step 4's exact SL/trail mechanism (entry-`slPrice` vs `tpsl/position/place_order`)
  + the auto-reduce assumption, before deploy.
- **Then:** deploy (drift-gated) + the 3-leg validation (after the sizing-gate decision).

## Deliverable action for THIS plan task (on approval)
Persist this plan to **`reports/2026-06-17_native_tpsl_bracket_rebuild_plan.md`** and **commit on a bitunix branch**
(the rebased exit branch). **NO code, NO deploy** — the rebuild is the next task to execute.
