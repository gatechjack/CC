# HANDOFF — poly_kalshi Item 1 + Item 2 BOTH DEPLOYED LIVE (2026-08-19). Session wrapped.

## STATE (confirmed read-only this session)
- **Both fixes LIVE.** prod-live tip **`7150404`** (Item 1 conflict gate) on top of **`fc78fc7`** (Item 2 mark
  quote() fix); pushed, in sync.
- **Engine PID 786261**, `poly_kalshi_mlb` **ARMED**: `auto_execute=True -> dry_run=False`, `halted=False`,
  stake $5, loss-cap $100, max_orders 25. **Roster: 2 live / 4 paper, disjoint** (live = SDTrading + xifutloong3).
- **Deployed files on box (LF-md5):** executor `257f6433`, matcher `7c191e83`, brokers/kalshi.py `7fb2688f`.
  **3 byte-locked files byte-UNCHANGED:** kalshi_copy_trader `af336db8`, sports_team_mapping `b715f341`,
  kalshi_live `bbd851a6`.
- **Item 2 proof:** `quote()` returns real mids (`quote(...PITLAD-PIT)=0.345`, was 0.0).
- **Item 1 proof:** `[G-conflict]` code path present on box + armed; `skip_conflict/skip_gate_error` audit
  rows = 0 (gate live, has not fired — see watch-thread (a)).
- Build branch `poly-kalshi-item12-build-2026-08-18` tip `8f24b42` pushed to origin (code `7c17edf` Item 1 /
  `332151e` Item 2; all scope/probe/build/stage evidence + all runners committed under
  `reports/2026-08-18_poly_kalshi_item12_scope/`).

## ROLLBACKS (backups retained on box, verified holding the pre-fix blobs)
- **Item 1 (atomic, both-or-neither):** `powershell -ep bypass -f .\pk_item1_rollback.ps1` — auto-selects the
  newest `.bak_item1_*` pair = `.bak_item1_20260819_104959` (executor `d1f871f9` + matcher `4b2a5c49`) +
  restart. No `-BackupSuffix` arg needed (runner picks newest pair; aborts if either backup missing).
- **Item 2:** `powershell -ep bypass -f .\pk_item2_rollback.ps1` — restores `.bak_item2_20260819_103406`
  (brokers/kalshi.py `18626cf0`) + restart. Single file.
- Both rollbacks: no cutover / no roster change.

## TWO OPEN "WATCH-FOR-IT" THREADS (observation, not action)
- **(a) Item 1 end-to-end:** `skip_conflict=0` so far — the gate only fires when two whales take OPPOSITE
  sides of the same game. After the next busy MLB slate, glance the audit journal
  (`audit_event` actor=poly_kalshi_mlb, `status='skip_conflict'`) to confirm it logs a skip and NO
  both-sides placement occurs. That's the end-to-end confirmation still pending.
- **(b) Item 2 marks:** `quote()` now returns real mids; watch for a `poly_kalshi mark tick` with `marked>0`
  on the next open position whose market has a quotable book (the sparkline data source is now live;
  `poly_kalshi_mark_live`/`_history` will start accruing rows).

## FORWARD WORK carried (unchanged, NOT started this session — anchor poly-kalshi-mlb is authoritative)
- Claude Design prompt — now UNGATED on the mark fix; remaining gate is just watch-thread (a)+(b)
  (a game populating marks + a skip_conflict fire).
- live-whales roster tab (dashboard display gap).
- Two instrumentation fixes: net `fill_fee` into the resolver; persist the poly trigger timestamp.
- live-equity wiring decision.
- shared `secrets.py` RedactingFilter fix.
- Canonical sell-aware whale records need `whale_screening` deployed.

## HOW JACK WORKS (hold this bar)
- Operator-run `pk_*.ps1` for every prod mutation (author + validate the runner; Jack runs it, or Board
  authorizes agent-run per deploy). No raw ad-hoc az; abort-safe runners; LF-md5 drift-gates.
- Checkpoint discipline: scope -> ratify -> build ONE -> review -> proceed. Never chain.
- Verify empirically, never narrate; if something isn't confirmed, say so (do NOT fabricate — e.g. the
  skip_conflict=0 above is stated as pending, not claimed).
- Live-money status leads every report. Shared byte-locked files stay byte-unchanged (diff every deploy).
- The memory anchor `poly-kalshi-mlb-live-2026-08-16` is authoritative for full state/history.

## COORDINATION + SAFETY (this session)
- Concurrent MACE workstream honored: both items were built + deployed only AFTER MACE's two deploys
  settled (prod-live `653a649` weekly-rungs 08-18, `4cf6eab` strike-band 08-19). Stage-1 re-baseline
  confirmed no MACE drift on our 3 files before deploying. No two prod-live advances / engine restarts raced.
- All housekeeping this session was read-only (box) + git/doc + worktree cleanup. **Live loop untouched by
  housekeeping.** The only box mutations were the two authorized deploys (Item 2 @10:34, Item 1 @10:50).
