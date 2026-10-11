# PM rollup-fix + leg-audit dedup — DEPLOY RECORD (2026-10-10 / applied 2026-10-11 00:23Z)

Branch `pm-rollup-fix-2026-10-10` off `origin/prod-live 49091390`. pm_web-only; no schema change (head 24);
engine untouched. Two code commits (independently revertible) + this record.

- rollup fix:  `7f4aa1113489fbee`  (stats.py, web/app.py, + 3 rollup tests)
- leg dedup:   `e943e8049d5df5b4`  (leg_audit.py, web/live_view.py, pm_live_list.html, + dedup test)

Per-file CR-stripped sha16 BASE → TARGET (drift-gate proven before any write):
- stats.py            e0334a96f75ba821 → c9d381ed62fcad57
- web/app.py          6693cb1e19ce8139 → b8f85d0068f9bf85
- leg_audit.py        04880ccde3aa6e6e → 07b98056ccb7fae6
- web/live_view.py    a7907af506cdeff6 → bb186867b668159f
- web/templates/pm_live_list.html  af8e32102b44e0ff → 3fa2c799db7deaa5

Backup (retained): `/home/azureuser/pm_rollupfix_backup_20261011T002054Z.tar` (296960 B, 5 files).
Apply: guarded graft (box==BASE & staged==TARGET all 5 → backup → apply → live==TARGET → py_compile+import OK),
NO partial state. Restart: `prediction-markets-web` only, via az run-command. Boot-verify:
MainPID 689895→698062, ActiveEnter 00:23:45Z postdates app.py mtime 00:20:55Z, /healthz 200, all 5 live==TARGET,
engine trading-corp 678426 UNCHANGED/NRestarts 0, arm 58 armed/35 stale-latch/0 new latch, copy entries filling
(8/8 is_exit=0). Banner rendered exactly as predicted: 19 rows → 3 lines (2 FILLED 5038/5278 + 1 collapsed
NO_FILL ×17), headline "2 FILLED POSITIONS TO REVIEW · 17 no-fill attempts (no position) · 19 code review".

## ★ Severity of what shipped 2026-10-08 (the real finding, NOT an optimisation)
Measured on a consistent backup of the live PM db (354 wallets, 722,320 closed rows): the FULL-TABLE
`stats.rollup` the Refresh button fired took **993.559 s (~16.5 min)**; the scoped rollup takes **0.284 s** —
a **3,501× speedup**. **One Refresh click occupied a worker thread for ~16.5 minutes out of pm_web's 6-thread
pool** (min(32, 2 vCPU + 4) = 6). Every Refresh pressed since 2026-10-08 did that. The site staying up was
luck, not headroom: a second concurrent click removed a thread for 16 min; the sixth would wedge it. This is
the measured severity of the bug that shipped on the 8th.

## The fix (3 parts; the two the brief required, not the forbidden one)
1. SCOPE the rollup to the refreshed wallet (`stats.rollup`/`_rollup_onesided` take `wallet=`; INSERT OR
   REPLACE keyed on (wallet,category) still writes the full column tuple → value-identical to the nightly
   full run, other wallets byte-untouched; no cross-whale term exists). This REMOVES the work (993s→0.284s).
2. PROCESS-WIDE single-flight: a module-level `asyncio.Lock` around ONLY the `to_thread(rollup)` call → ≤1
   rollup in flight; queued (not rejected) since each scoped run is sub-second. The per-wallet guard did NOT
   bound distinct wallets — the root cause.
3. async-row-update-on-completion: FORBIDDEN alone; not built.
Leg-audit dedup (display-layer): FILLED rows never collapse (own line/id/status); NO_FILL collapse by
(ticker,leg,outcome) with a count; headline leads with FILLED positions-at-risk, states no-fill attempts
separately. NO_FILL grouped, never suppressed.

## Tests (non-tautological; 10 new + 4 regression suites, all green on box venv)
- test_rollup_scoped: scoped==full value-identity (oracle=full path) + other-wallets-untouched + zero-rows.
- test_rollup_lock: lock serialises (peak==1) WITH a red-first no-lock control (peak>1).
- test_refresh_outage_repro: on a pinned 4-thread pool (deliberate simplification; prod is 6 on 2 vCPU), 8
  concurrent distinct-wallet refreshes WEDGE an unrelated control request before the lock (latency >0.3s) and
  keep it responsive after (<0.15s).
- test_leg_audit_dedup: hand-computed 19→3 split, two-filled-same-tuple-don't-merge, all-no-fill-grouped, cap.

## ★ Phase 3 CORRECTION (named as a correction so no one re-derives the wrong version)
The 5000-row `/activity` ceiling is **Polymarket's HARD wall, NOT ours.** All 5 probed whales returned, at
page 11, `HTTP 400 — {"error":"max historical activity offset of 5000 exceeded"}`. **Phase 0 Q3 said the
opposite ("our cap, the client pages past it, fixable") and so did I** — that came from reading
`loss_grounding`'s comments instead of calling the endpoint. Our prod `activity_max_pages=10` (offsets 0–4500)
sits one page under the wall; raising it buys ≤500 more rows then 400s. **Deeper /activity paging cannot
improve coverage for any truncated whale** (the ~50% truncated-coverage is a venue floor); only a *different*
grounding source could. Probe cost 60 calls.

## Backlog (flag, do NOT fix) — coverage is partly an INVERSE measure of activity
Because /activity hard-caps at ~5000 rows, a whale with 100 trades can reach 100% coverage while one with
1,666 structurally cannot. So `INSUFFICIENT_DATA` means OPPOSITE things on a busy whale vs a sparse one, and
the tier currently conflates them. Flagged for a future ruling.

## Promote-gate — PENDING Jack's ruling (not decided)
`promote_to_live` (farm_actions.py:113-170) gates only on pinned + account-active. Recommendation: **WARN at
the Promote click** (show tier+coverage; keep discretion). ⚠️ A hard coverage floor is ruled out on evidence:
it would permanently exclude every high-activity whale whose coverage is venue-capped below 90%.

## 429 observation (cost, per the budget rule)
~500/hr pre-probe → ~4,614/hr during the probe window — **all /positions, ZERO /activity in the rise** (the
probe wasn't throttled). Dominant cause: the CFB-Saturday driver's own /positions polling; shared-IP
contribution from the 60-call burst is possible but weak. Still below the ~7.5k/hr normal baseline. **Live
fills never stalled** (last fill mid-probe 22:57:38Z, 0 errors). Phase 3 reported and CLOSED for the night.

## Deferred / not done this pass
- The `ts→spirit` alias (branch cs2-leg-alias-ts-2026-10-09@43cdcfb5) + the 19-row reclassify — ride the next
  engine restart; reclassify NOT authorized until the alias is live.
- cfb whale 0x27f738fe…44b0 grounding report — awaits Jack's Analyze click; will carry a partial-data caveat
  (backfill_complete=0).
