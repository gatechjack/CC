# Bitunix SFP Mode B (15m SFP → 3m BOS) — BUILD COMPLETE, pre-deploy report

**Branch** `bitunix-sfp-mode-b-2026-06-28` @ `b5147fc` (off `main` @ `80f2c43`).
Status: built + parity-gated + full-suite-clean + drift-gated + staged. **No prod
step taken** — awaiting operator go for RESTART #1 (paper dry-run).

## What was built (additive)
- **`SfpModeBDetector`** (new, in `bitunix_sfp.py`): embeds the validated
  `SfpDetector` as a 15m fire engine (its ARMED transition = the SFP fire), then
  advances BOS on 3m closes per the p6 oracle `watch_B` + the locked contiguity
  guard (the 3m bar opening exactly at the 15m fire-close t0 must exist, else
  OUT-OF-RANGE). `SfpEntrySignal` gains a defaulted `bos_tf` field.
- **Observer**: per-symbol `symbol_modes` config (bos_tf 15m|3m, arm trading|watch);
  Mode-B detectors + warm-start; a single `run_loop_master` on the **3m** boundary
  (so two order paths can never race the equity snapshot); `arm:watch` → forced
  PAPER; `source_signal` tagged `sfp_*_3m_bos`.
- **main.py**: 3m cache dict (BTC `bitunix_bar_cache` + alts `capture_3m`, bumped
  to `max_bars=500` so a 12h watch survives a restart); master-loop selection.
- **strategies.yaml**: 4 coins, all `bos_tf:3m`; BTC/ETH `trading`, SOL/XRP `watch`.
- **Drift MANIFEST**: added both SFP modules (were ungated real-money code).

## ★ Parity gate — PASSED (HARD gate; no live arm without it)
`tests/test_bitunix_sfp_mode_b_detector.py` — 10 tests, **0 skipped**:
- Synthetic 4-seed parity (warm-start) vs vendored `watch_B`+contiguity, both
  REAL + CONSIDERABLE.
- **Interleaved live-feed order == warm-start == oracle** (proves the master-loop
  feed order is correct).
- Contiguity present-confirms ✓ and outrange-drops ✓.
- **k=1** prefix-stability on the interleaved stream (no look-ahead).
- **Real-data parity over ALL 4 `*_scalping.db`** (btc/sol/eth/xrp 15m+3m, gappy)
  — streaming == oracle bar-for-bar. This is the strongest gate and it executed
  (not skipped).
- Root-cause note: the only initial mismatch was a **15m-fire** divergence from a
  re-transcribed pivot oracle (the detector's documented p<pivot_len warmup quirk),
  not a watch_B bug — fixed by sourcing the oracle's fires from the same validated
  `SfpDetector` (Mode-A-proven vs p6), isolating this gate to the new 3m port.

## ★ Source-invariance proof — `SfpDetector` byte-unchanged
`git diff main` numstat for `bitunix_sfp.py`: **182 added / 0 deleted.** Insertions
only at `@@ +104 @@` (the additive `bos_tf` field on the separate `SfpEntrySignal`)
and `@@ +353,181 @@` (`SfpModeBDetector` appended AFTER the class). **Zero removed
lines** ⇒ every original line preserved ⇒ the `SfpDetector` class is byte-identical.
Corroborated: the Mode-A parity test (`test_bitunix_sfp_detector.py`) still passes.

## Full suite — == baseline, ZERO new failures
Measured branch vs a fresh clean-main worktree (`80f2c43`):
- Branch: **28 failed**, 0 SFP/Mode-B failures.
- Clean main: **28 failed**.
- `Compare-Object` of the two FAILED sets: **empty (identical).**
- Plus the +10 new Mode-B tests pass; all 78 SFP-keyed tests pass.
The 28 are the documented pre-existing baseline (webhooks/iron_condor/robinhood/
tasty/paper_run_tooling fixture drift) — untouched by this branch.

## Drift gate — clean (prod == main base, verified read-only)
All 5 deploy files: prod LF md5 == `main@80f2c43`:
`main.py` 2c1bb1dc · `strategies.yaml` 0cd6e45d · `bitunix_sfp.py` 5c71a103 ·
`observer` 18da45f2. Targets (HEAD LF): `bitunix_sfp.py` 91fd7672 · observer
8a916526 · main.py 2ff188c7 · strategies.yaml 84001f67 · md5diff f9e2979b.
Clean diff off main; nothing a full-file blob would clobber.

## Commits (scoped, on branch)
`b319e33` detector+parity · `c6f8e19` observer+master-loop · `480e1d0` symbol_modes
· `f795778` drift MANIFEST · `b5147fc` deploy package.

## NEXT — operator-gated (no prod step taken yet)
Per plan: **RESTART #1 = paper dry-run** (push 4 code/script blobs + paper config,
restart, read-only boot smoke) → **STOP for your confirm** → **RESTART #2 = live
flip** (push live config, restart). The agent generates the one-line `.ps1`
runners at go-time; you run the prod writes/restarts (flat-guarded). See
`deploy/2026-06-28_bitunix_sfp_mode_b/RUNBOOK.md`.
