# PEAD shared-RH-auth-outage resilience audit — 2026-08-31

READ-ONLY investigation. No code/config/deploy/edit/restart. Sources labelled: [ENGINE-DB] =
`data/trading_corp.db` (`?mode=ro`), [RH-JOURNAL] = `journalctl -u trading-corp.service`,
[CODE] = deployed files materialized from the box.

## 0. State reconciliation (read the box, don't trust the brief)

- Box time UTC; `tc-prod-vm`. [RH-JOURNAL]
- **Engine restarted 21:33:46Z today** → `MainPID=127578`, `NRestarts=0` (task said 89366).
  Per Jack: **the PM division is deploying — benign, non-issue.** The engine that actually
  threw during the outage was the PID-119573 process (16:30Z→21:33Z); 89366 was already stale.
- **Deployed hashes DIFFER from the brief.** Live now: `pead_strategy.py` sha256 `28eb62be…`
  / git-blob `8153ea25…` / **1221 lines** (brief said fc3d6de6); `robinhood.py` sha256
  `ecf5457e…` / git-blob `3a55194d…` / **1944 lines** (brief said e90af223). The PM deploy
  shipped the current tree. **But `pead_strategy.py:646` is byte-for-byte the same statement
  in both the outage-time code and the live code** — the throw site the task named is intact
  and unchanged. Local materializations: `_box_pead_fc3d6de6.py`, `_box_robinhood_e90af223.py`
  (filenames retain the brief's hashes; contents are the current 28eb62be / ecf5457e). [CODE]
- **Open PEAD book now = 33** (task said 34), all `result IS NULL`, all `execution_mode=live`,
  reconciles to the 33 rows listed; **0 pending/intent rows**. [ENGINE-DB]

## 1. THE THROW at pead_strategy.py:646

**Exact live code** (`manage()`, the exit engine — [CODE] 28eb62be):

```
628  async def manage(self, broker) -> tuple[list[ProposedOrder], int]:
...
641      window, placement_open = self._exit_window_state(datetime.now(timezone.utc), cfg)
643      if window == "closed":
644          return [], cadence                      # <-- existing clean-skip idiom
645      today = datetime.now(timezone.utc).date()
646      snap = await broker.snapshot()               # <-- UNGUARDED. throw site.
647      equity = float(getattr(snap, "equity", 0.0) or 0.0)
649      exits = []
650      for r in rows:
...
655          try:
656              last = float(await broker.quote(r["symbol"], strict=True))
657          except QuoteSymbolUnresolved:            # Part-3 skip (one symbol)
...
667          except Exception as e:  # noqa: BLE001   # generic skip (one symbol)
668              log.debug(... quote failed ...); continue
```

**Today's traceback** (repeats every ~5 min through the outage) [RH-JOURNAL]:

```
Aug 31 16:38:47  Traceback (most recent call last):
  File ".../agents/strategies/pead_strategy.py", line 646, in manage
    snap = await broker.snapshot()
  File ".../brokers/robinhood.py", line 625/509, in snapshot   (robinhood line drifted pre/post-deploy)
```

- **Loop:** `manage()` — the **exit engine**, NOT scan/entry, NOT reconcile. Cadence
  `manage_cadence_sec` (~300s). It fired at 16:38:47, :43:47, :48:47, :53:47, :58:47, 17:03:48,
  :08:48, :13:48, :18:48, :23:48 — **~10 consecutive ticks**, then stopped at
  `rh_auth_recovered` 17:26:28Z. [RH-JOURNAL]
- **Why it threw:** `broker.snapshot()` is the **first RH call in the tick and is unguarded**
  — no try/except, no auth-state check. It sits *above* the per-symbol loop, so it is reached
  before any of the guarded quote()/place() calls. When the shared session is down, the RH
  profile read inside snapshot() raises and the exception propagates straight out of manage().
- Note: `robinhood.snapshot()` *attempts* graceful degradation (empty-profile-after-failed-reauth
  → equity 0, "NO raise", L524) — **but that only covers the empty-return path**; today the 401
  surfaced as a raised exception from the underlying robin_stocks call, so snapshot() **still
  raised**. snapshot() is therefore not reliably non-throwing on a dead session. [CODE]

## 2. BLAST RADIUS during the 48-min dead session (16:38:47→17:26:28Z / 12:38→13:26 ET)

**Verdict: CLEAN-SKIP-BUT-NOISY. No crash, no half-state, no phantom, no divergence.**

- **Whole tick vs one symbol:** the throw kills the **ENTIRE manage tick** — all 33 names
  skipped that tick (it dies at :646 before the loop starts). It is NOT per-symbol. The
  per-symbol quote IS guarded, but snapshot() is reached first. Net: 0 of 33 positions priced
  on each of the ~10 outage ticks. [CODE + RH-JOURNAL]
- **Crash vs survive:** NOT a crash. Same engine PID (119573) before, during, and after; the
  traceback recurs each cadence and then recovery resumes normally → the manage loop is caught
  and rescheduled one level up. The division kept ticking; it just did no useful pricing work
  per tick. [RH-JOURNAL]
- **Placed-but-unconfirmed order / phantom / double-order: NONE.** manage() threw at the *first*
  RH call — it never reached `_place_or_paper`, so no sell was in-flight. `pending_order` for
  `robinhood_pead` = **0 rows** (no queued/intent entry to be interrupted mid-confirm). The
  reconcile fill-confirm path is independently `try/except`-guarded (retry next tick), so even a
  mid-confirm interruption would replay idempotently — but there was nothing to replay. **Timing:
  the outage (12:38–13:26 ET) landed AFTER the day's entry/reconcile window (~9:31 ET / 13:31Z),
  so only the exit engine was live — the one unguarded path.** [ENGINE-DB]
- **Missed exit:** `pead_closed_in_window` (16:30–17:40Z) = **0 rows**; `audit_pead_in_window` =
  **0 rows**. No exit fired during the outage AND none fired at/after recovery (latest PEAD exit
  today = TSEM 14:17Z, before the outage). So **no exit was DUE at recovery** — at the first
  post-recovery tick no position met stop/drift/guard/time. Of the four rules, only **STOP**
  (intraday price) could be delayed by an intraday outage; DRIFT is a completed-daily-close rule
  (not due mid-session), GUARD/TIME are calendar-based (fire next tick regardless). Residual (not
  evidenced): a stop transiently breached *and reverted* within the 48-min gap would be invisible
  — inherent to a 300s-tick engine, merely widened from 5 min to ~48 min here. [ENGINE-DB + CODE]
- **Stuck position / inconsistent ledger:** none. 33 open rows all consistent; 0 pending/intent;
  today's 3 completed actions (URGN drift-exit 13:31Z, TSEM stop-exit 14:17Z, WDAY intent-entry
  13:31Z) all pre-outage and fully booked. [ENGINE-DB]

## 3. DETECT + RIDE OUT — the gap vs MACE

- PEAD does **not** detect the outage and does **not** ride it out gracefully at the snapshot
  call. `manage()` has NO try/except and NO auth-state check around `broker.snapshot()`; it
  throws a full traceback every tick until the session self-heals. It resumes automatically on
  recovery only because the scheduler swallows the per-tick exception — not by design.
- The broker DOES expose the outage: a shared latch `_auth_down/_auth_down_since/_auth_last_good`
  (under `_AUTH_LOCK`) plus `_auth_alert_hook → data_exec._on_rh_auth_change`, and it logs
  `rh_auth_failed` / `rh_auth_recovered`. **MACE consumes this signal (mark-unavailable / reconcile
  errors that clear on recovery, no crash); PEAD ignores it.**
- **Named mechanism gap:** PEAD is missing the equivalent of MACE's mark-unavailable-and-skip —
  i.e. a try/except (or an auth-state check) around the `manage()`/`scan()` snapshot() that skips
  the tick instead of throwing. There is currently **no public accessor** for `_auth_down`
  (module global only), so PEAD can't cheaply "check auth-state" without a shared-file change; the
  try/except route needs nothing shared.

## 4. REAL IMPACT vs NOISE — today's outage

**LOG NOISE ONLY. No PEAD trade or decision was harmed.** [ENGINE-DB + RH-JOURNAL]

- 0 orders placed, 0 missed exits due at recovery, 0 duplicates, 0 pending/intent rows, 0 ledger
  divergence, book intact at 33/33. The ~10 tracebacks are the *entire* footprint.
- The only theoretical harm — a stop that breached and reverted inside the 48-min gap — has no
  evidence (nothing fired at recovery) and is a pre-existing property of the 300s cadence, not a
  new defect. This was mid-session (off-hours exit gate was NOT suppressing), so the exit engine
  was genuinely live and still no exit was lost.

## 5. RECOMMENDATION (do NOT implement)

**Scope: PEAD-only. Guard the two unguarded snapshot() call sites; reuse the file's own skip
idioms. No shared-file change required.**

1. **`manage()` :646** — wrap `snap = await broker.snapshot()` in `try/except Exception`
   (`# noqa: BLE001`, matching the existing quote handler at :667). On failure, log at
   `debug/info` (one line, not a traceback) and **`return [], cadence`** — identical to the
   existing `window == "closed"` early-return at :644. Skip the tick, resume next cadence.
2. **`scan()` :426** — same guard around its `broker.snapshot()`; a scan during a dead session
   should skip cleanly (`return []`) rather than throw. Lower stakes (scan runs in the entry
   window) but same one-line fix.
3. **Reuse the Part-3 skip pattern?** *Conceptually yes, structurally a sibling.* Part-3's
   `QuoteSymbolUnresolved` and a dead session are both "RH can't give a usable answer right now,"
   but the scope differs: QuoteSymbolUnresolved skips **one symbol** (`continue`); a dead session
   must skip the **whole tick** (`return [], cadence`) because it fails before the loop. So it's
   the same *philosophy* (catch → skip → resume) applied one level up, not a literal shared catch.
4. **Half-state guarantee:** already satisfied for orders — `_place_or_paper` swallows place
   failures (no ledger write → no phantom) and reconcile Phase-2 retries fill-confirmation
   idempotently. The snapshot guard adds nothing to break; it only prevents the throw. Nothing in
   this fix can create a half-state.
5. **Shared robinhood.py:** the recommended fix does **NOT** touch it. If instead we wanted the
   MACE-style "check auth-state before acting," that needs a public accessor for `_auth_down`
   (or to consume the `data_exec` auth-change hook) — that WOULD be a shared change, must be
   **additive only**, and its Gate-A must hash against the **box `robinhood.py` (currently
   `ecf5457e…`, NOT the brief's e90af223 and NOT prod-live)** since MACE co-develops that file.
   **Recommend the PEAD-only try/except (#1–#2); leave robinhood.py alone.**

---
Materialized deployed code: `_box_pead_fc3d6de6.py` (28eb62be, 1221L), `_box_robinhood_e90af223.py`
(ecf5457e, 1944L). Diagnostic runner: `pead_auth_diag_ro.ps1`. Branch: `pead-auth-outage-diag-2026-08-31`.
