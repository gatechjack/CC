# Handoff — BitUnix `/tpsl/` bracket-rebuild session — 2026-06-18

**Session outcome: the native `/tpsl/` bracket rebuild is BUILT COMPLETE (steps 1–4 + path-fix), §4-gated build+test, NOTHING DEPLOYED.** Prod is in a known-safe state. This note hands off to the deploy + validation session.

---

## 1. Repo state — pushed + clean
- **Branch `bitunix-tpsl-rebuild-2026-06-18`**, pushed to origin, **unmerged**, working tree **clean** (0/0 vs origin).
  - `5caeb2f` — steps 1–3 (positionId→Position.extra; `place_tpsl_order` partial-qty TP leg; observer wired) + `modify_position_sl` path-fix (`/tpsl/modify_position_tp_sl_order` 404 → `/tpsl/position/modify_order`, positionId mandatory) + reconciler threading.
  - `d8dc515` — **step 4**: `place_position_tpsl` (POST `/api/v1/futures/tpsl/position/place_order`, body `{symbol, positionId, slPrice, slStopType, slOrderType}`, **no `slQty`** = auto-reducing) + observer wiring (place the managed Position SL at the structural stop after the TP legs; record `bracket_position_sl_order_id`; **fail-soft** → `bracket_position_sl_failed` + continue; skip if no `stop_price`).
- **Tests: 651 bitunix pass, zero new regressions.** +7 step-4 tests (body/error/idempotent/stub; places-SL+records-id; fail-soft on SL failure; skip-when-no-stop).
- **Reports committed:** research `567b2c1` (`reports/2026-06-17_bitunix_native_multitp_research.md`), plan `7e7a2e1` (`reports/2026-06-17_native_tpsl_bracket_rebuild_plan.md`), this handoff.
- **No stash from this session** (the 2 entries on the shared stack are stale May leftovers — DO NOT touch them; shared-stash hazard). **No stray worktree** created; `cc-tpsl-rebuild-wt @ d8dc515` is the legitimate one.
- **Files touched (3, all permitted):** `brokers/bitunix.py`, `agents/divisions/bitunix_futures_observer.py`, `tests/test_bitunix_tpsl_rebuild.py`. No main/db/models/bracket/reconciler/cutover/polymarket.

## 2. Prod safe-as-is (read-only verified 2026-06-18 ~22:14 UTC)
- Engine **PID 2926399, NRestarts=0, active/running** (up since 06-17 23:44:45 UTC).
- Re-arm **holding**: ExecStart `--live-divisions bitunix_futures`; `Registered bitunix_futures broker … (paper=False)` = **real broker** (not paper-wrapped).
- **Flat** (no unresolved bitunix `paper_trade_record` rows), **no active halt** (`_halt_new_orders RELEASED` 20:35:34, none since), **no divergence** open.
- **Freeze-fix holding** (max journal gap 37s over 60 min). Staleness gate code deployed (06-17 deploy).
- **Nothing from this session deployed** — live `bitunix_bracket.py` md5 = `bd639224…` (current deployed code, NOT the rebuild branch).
- **No residue:** no diagnostic watchers (py-spy/strace/tcpdump), no tpsl/bitunix temp files left on prod. SSH access is the read-only baseline (82fda13); **no §4 lift carries past this session.**
- **No must-fix-now item.**

## 3. ⚠ Fresh real-world data point — live trade `7d1a78dc` (today, ~5h)
A live STANDARD SHORT ran **15:18:57 → 20:34:34 UTC** on the CURRENT deployed code and **re-exhibited both issues the rebuild fixes**, repeatedly, before self-recovering:
- **SL-trail 404** every minute (`modify_position_sl` on the old path) — the exact bug the rebuild's path-fix addresses.
- **Managed TP exit rejected ~22×** over 5h (`live_exit_order_placed → live_exit_order_rejected`) — the managed-exit-never-succeeds-live bug ([[bitunix-orphan-managed-exit-bug]]).
- Closed via `position_state_divergence_detected → auto_book_server_side_close` (the resting reduce-only-LIMIT TP filled server-side; B1 + P2 caught it), then `_halt_new_orders RELEASED`, flat & quiet since.
- **This empirically confirms "safe-as-is" = B1-catastrophic-stop + P2-auto-book only; the deployed managed exit AND trail are both non-functional live.** Strong reinforcement of deploy priority. NOT a must-fix-now (B1 guarded, position closed at a profit, self-recovered, flat).

## 4. NEXT (deploy + validation session)
1. **Drift-gated deploy** of the ~4 bitunix exit files (`bitunix.py`, `bitunix_futures_observer.py`, `bitunix_position_reconciler.py`, `bitunix_bracket.py` if touched). **NEVER** main.py/db.py (cutover's) or any polymarket file. Pattern: fresh worktree off deployed base → dual-mode md5 drift-gate each file → backup `*.bak-pre-tpsl-rebuild-<date>` → py_compile → atomic mv → re-verify → **NO restart in the script**. Agent drives scp/apply over SSH (§4-authorized for the sequence); **operator runs the ONE restart** (`az run-command` / `sudo systemctl restart`).
2. **VERIFY (post-restart):** engine up + files at target md5; bitunix still real broker (paper=False) + gate fires; a live entry's TP legs **REST as `/tpsl/` orders** (`get_pending_orders` shows them, **no 30038**); **SL-trail works** (`position_sl_update {moved:true}`, **no 404**); SL **auto-reduces** on a partial; reconciler clean; no main/db touch.

## 5. Deploy-time live checks (NOT buildable — resolve on validation, fail-soft either way)
- **B1-entry-stop vs separate Position-SL coexistence** — does the entry-attached `slPrice` MARKET stop + the new `/tpsl/position/place_order` SL coexist without `30038`, OR is the entry-stop already the position SL (making the separate call redundant)? A 1-trade live check; B1 guards regardless.
- **3-leg validation sizing gate** — sizing is **hardcoded** in `TIER_SIZING` (`observer.py:207-212`), NOT config. A true 3-leg test needs an entry **≥0.0012 BTC (~$79)**: either a natural PREMIUM signal (~2× STANDARD, unreliable) or a deliberate `TIER_SIZING` bump shipped *with* the rebuild then reverted.

## 6. SEPARATE backlog — P2 auto-book result-sign bug (CONFIRMED TWICE)
The auto-book labels server-side closes `result: "loss"` regardless of actual PnL sign:
- `e1758fc9` (06-18 ~01:39): TP +0.03486 USDT → booked **loss**.
- `7d1a78dc` (06-18 ~20:34): short closed VWAP 63094.97 vs entry 63521 = **+0.298 gross / +0.268 net** (`pnl_basis: real_fill`, 3 fills) → booked **loss**.
- **The PnL *value* is correct (real signed-fetch fill); only the `result` *label* is mis-signed.** Records understate win-rate (both real wins logged as losses).
- **Fix:** correct the result-classification in the auto-book (derive result from net PnL sign, not "closed via stop ⇒ loss") **+ correct the historical record(s).** NOTE: `paper_trade_record` had no row keyed by the bare order_id for `7d1a78dc` — the result may be persisted under a different key or audit-only; locate via the `auto_book_server_side_close` audit payload.

---
**State: rebuild BUILT COMPLETE (unmerged), prod SAFE-as-is + flat, no residue, SSH read-only baseline restored. Session closed.**
