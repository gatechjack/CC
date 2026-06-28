# SESSION HANDOFF — Bitunix SFP Mode B go-live (4 coins) + cockpit fixes (2026-06-28, later)

Pick-up doc for the next session. This session: **built + parity-gated + deployed `bitunix_sfp` Mode B
(15m SFP → 3m BOS) live for BTC + ETH** (SOL/XRP watch), **validated the first live trade end-to-end**, and
**deployed three SFP-cockpit display fixes**. Full deploy record: `runbooks/deploy_log.md` → "2026-06-28
(later)". Build report: `reports/2026-06-28_sfp_mode_b_build.md`.

## 1. GIT STATE — `main` is the source of truth again
- Branch **`bitunix-sfp-mode-b-2026-06-28`** (worktree `cc/.claude/worktrees/bitunix-sfp-mode-b-2026-06-28`)
  was cut from `main@80f2c43`, and is **merged → `main` (`--no-ff`) + pushed**. **`main == origin/main == live
  prod runtime`** for all 9 deployed files again (the parity anchor is restored). Start the next session from
  `main`; cut a fresh branch.
- The branch carries everything: detector + parity test, observer + main wiring, strategies.yaml, drift
  MANIFEST, the `deploy/2026-06-28_bitunix_sfp_mode_b/` package (apply + arm-restart + cockpit-apply scripts),
  the build report + this handoff. The local-only `.ps1` runners live in `cc\` root (`sfp_modeb_apply.ps1`,
  `sfp_modeb_arm_restart.ps1`, `sfp_cockpit_apply.ps1`) — not committed, regenerate from the package if needed.

## 2. WHAT SHIPPED
1. **Mode B (additive; `SfpDetector` byte-unchanged — git diff 182 add / 0 del).** New `SfpModeBDetector`
   embeds the validated `SfpDetector` as a 15m fire engine and confirms BOS on **3m** closes (port of the p6
   oracle `watch_B` + the 2026-06-26 contiguity guard). Per-symbol `symbol_modes` config; single 3m-boundary
   `run_loop_master`; `arm:watch`→forced-PAPER routing. **HARD parity gate** (synthetic 4-seed + interleaved
   + contiguity + k=1 + **real-data over all 4 `data/*_scalping.db`**) green; Mode-A parity still green; full
   suite == baseline (28F, identical failure set to clean-main).
2. **Cockpit display fixes (display-only).** (a) live set from `symbol_modes` arm:trading → BTC+ETH LIVE,
   SOL+XRP MONITOR (was hardcoded `LIVE_SYMBOL="BTC"`); (b) `hx-preserve` kills the header blank-flash;
   (c) standard division header (TC mark + Command Center + Overview/Trades/Research/System).

## 3. CURRENT PROD STATE (verified 2026-06-28 ~23:02 UTC)
- **Engine:** systemd `trading-corp` MainPID **3777492**, active, NRestarts=0. ExecStart UNCHANGED:
  `--live --brokers bitunix robinhood --live-divisions bitunix_sfp robinhood_pead`.
- **SFP:** `bitunix_sfp` LIVE, `mode:trading`, `execution_mode:live`, `auto_execute:true`, `mode_b=True`.
  `symbol_modes`: **BTC `3m`/`trading`, ETH `3m`/`trading`, SOL `3m`/`watch`, XRP `3m`/`watch`**. The
  **3m-master loop** is spawned; heartbeat ticks on the 3m boundary. Account **FLAT**; reconciler clean.
- **First live Mode-B trade — DONE + round-trip validated.** ETH 20:15Z: `sfp_real_3m_bos`, buy 0.0945 ETH,
  B1 stop 1554.15 + 2R venue `/tpsl/` leg 1607.88 (both rested at venue). Stop-out → reconciler
  divergence-detect → auto-book → clean reconcile → halt self-release. Booked **loss −1.16R** (n=1).
- **Two-state / PEAD:** `bitunix_futures` HALTED-INERT; `robinhood_pead` live (unchanged this session).
- **Cockpit:** `/sfp` shows BTC+ETH LIVE, SOL+XRP MONITOR, "2 live", no flicker, standard nav.

## 4. OPEN ITEMS / NEXT STEPS (no blockers)
- **Forward-track BTC/ETH 3m-BOS to n≥30, THEN the operator scales money.** Trivial sizing by design; n=1 so
  far. Track win%/avgR per coin (cockpit TIER-A / `paper_trade_record division='bitunix_sfp'`). See
  `BACKLOG.md` P1 (top).
- **SOL/XRP stay watch-only (paper)** — negative/thin backtest. Arm = flip `symbol_modes.<coin>.arm`
  `watch→trading` + restart (Board ack).
- **BTC is on 3m-BOS now, OFF its validated 15m edge** — if 3m underperforms, revert `bos_tf 3m→15m` (Mode-A
  path is byte-intact + parity-tested).
- **Cockpit TIER-B → real reads.** `sfp_watch_state` is now populated live; the armed-watch / near-miss /
  bos-confirm panels are still `_mock_*` — wire them to real reads (display-only).
- **Minor:** `docs/divisions.md` does not list `bitunix_sfp` at all (pre-existing gap) — add it when grooming.
- **Carried (older, SEPARATE futures division):** Bitunix confluence P1-A/B backtests, fee/slippage levers.

## 5. ENVIRONMENT SYNC
- local `main` == `origin/main` == live prod runtime for all 9 deployed files. ✓
- Branch `bitunix-sfp-mode-b-2026-06-28` pushed to origin + merged to main. ✓
- Prod backups: `*.bak-pre-modeb-2026-06-28` (4 files) + `*.bak-pre-cockpit-2026-06-28` (5 files).

## 6. DEPLOY DISCIPLINE REMINDERS (unchanged)
- Drift-gate vs prod md5 before any deploy (`scripts/bitunix_prod_surface_md5diff.py` now incl. both SFP
  modules). Deploy LF blobs (Latin1 CR-strip + scp, or `git show HEAD:f | tr -d '\r' | ssh`), NOT `git archive`.
- Restarts flat-guarded (the `arm_gate_restart.sh` checks `position` count + open `bitunix_sfp` rows; the SFP
  division tracks open trades in `paper_trade_record result IS NULL`, NOT the `position` table).
- Operator has NO sudo password; NOPASSWD = systemctl/journalctl/sqlite3. Agent SSH this session was
  read-only (status / `sqlite3 -readonly` / `journalctl` / `md5sum`); operator ran every prod write + restart.
- The reconciler auto-books a venue stop-out on its ~60s sanity poll — give it a loop or two before alarming
  on a transient "open row vs flat venue" state.
