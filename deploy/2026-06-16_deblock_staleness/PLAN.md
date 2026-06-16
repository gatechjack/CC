# Deploy bundle: C (staleness gate) + A (polymarket risk-scan removal) + audit_event index

Prepared 2026-06-16. **Prepare-only — NO prod write, NO deploy, NO restart performed.**
Branch `deploy-prep-2026-06-16`, merged prep SHA **888dd31** (off current main **a66e36d**).

## What's in the bundle (3 reviewed branches)
- **C** — bar-interval-aware staleness-reject gate (`bitunix-staleness-reject-gate-2026-06-16`, 62c7008):
  observer + `main.py` wiring + `strategies.yaml` (`staleness_gate: enabled:true margin_seconds:120`).
- **A** — REMOVE the RiskAgent polymarket `audit_event`-scan aggregate caps (the H3 freeze culprit)
  (`bitunix-deblock-eventloop-2026-06-16`, 90083ab): `risk.py`.
- **index** — `ix_audit_event_actor_kind` on `audit_event(actor, kind)` (same branch, e2d2097): `db.py`
  SCHEMA migration; closes the *kept* on-loop scans (resolvers + dedup) A didn't touch.

## Consolidation result (Step 1)
- Merged cleanly onto current main a66e36d. `main.py` auto-merged (C's `run()` startup hunks vs E5b's
  `_handle_copy_order_placement`/`_build_broker_for_division` hunks are ~3000 lines apart — no collision).
  Both features confirmed present post-merge. `strategies.yaml` touched only by C.
- **Regression gate (full suite, py3.14, vs current main a66e36d baseline): ZERO new code regressions.**
  Merged 28 FAILED / baseline 26 FAILED — the exactly-2 delta (`test_paper_run_tooling.py::test_readiness_*`)
  is an environmental artifact: those tests connect to the gitignored local `data/trading_corp.db`, which is
  652 MB/populated in the long-lived `cc` worktree but 4 KB/empty in the fresh deploy-prep worktree
  (`no such table: agent_state/audit_event/position`). Proven: with merged code pointed at the populated DB
  both pass. All 90 feature + preserved-control tests (C gate, A index, A no-scan, risk DD/flatten/halts,
  drawdown-flatten, breaker-abstain) pass.

## ⚠ DIVERGENCE FINDING — why main.py + db.py are NOT full-file deploys
The task assumed all 4 `.py` get a full-file md5-gated deploy (prod == repo base). **That holds for 2 of 4,
not all 4.** Prod is the *bitunix-only* deployment; the polymarket E-series was never shipped to it:

| prod file | prod md5 | == repo? | maps to commit | lag (what prod is missing) |
|---|---|---|---|---|
| `bitunix_futures_observer.py` | 3067a3e9 | == base (b3d1f08/a66e36d) | — | none |
| `risk.py` | 4b87e149 | == base | — | none |
| `main.py` | 659bbb80 | **≠ any** | **710e181** (go-live item4) | polymarket E1.6→E5b wiring (+169/−42) |
| `db.py` | 9f94649 | **≠ base** | **b5278c5** (stage-1) | polymarket **E2.5** execution_mode column (+19/−2) |

A full-file overwrite of `main.py`/`db.py` with the merged blob would **also ship the un-deployed polymarket
batch (incl. E5b) to the live bitunix box** — outside the 3-branch scope. So:
- **observer.py, risk.py → full-file** (merged blob = prod base + C / + A only). md5-gated.
- **main.py, db.py → constructed = PROD blob + ONLY the in-scope C / A hunk** (like `strategies.yaml`).
  Verified: constructed `main.py` delta vs prod blob 710e181 = exactly C's 4 staleness hunks (15 lines), **0
  E5b**; constructed `db.py` delta vs prod blob b5278c5 = exactly the index DDL (8 lines), **0 E2.5 column**.
- **strategies.yaml → targeted insert** of the 10-line `staleness_gate` block after the unique anchor
  (`snapshot_staleness_threshold_seconds: 60`, line 1023). Dry-run on the real prod yaml: `+10 lines,
  staleness_gate:1, execution_mode: live preserved`. Block lands between line 1023 and `division:` (1034).

## Deploy target md5 (what the script gates to)
```
observer.py  3067a3e9 -> eec6bda62e23038edd09f29ff65addcb   (full-file)
risk.py      4b87e149 -> 49a1c7968dc3e7e6e00352a7ca706f9f   (full-file)
main.py      659bbb80 -> f733e37407617d5f9d3330ad15a0ebc6   (prod blob + C hunk only)
db.py        9f946491 -> d56e06393403147c3f8dfc49914c814e   (prod blob + A index only)
strategies.yaml 8e8e3117 -> (targeted insert; md5 changes, not pre-computed — live values preserved)
```

## NOT touched (scope guards held)
- `data_exec.py` (prod e3e4cca7) — **excluded** (polymarket E2.5 exclusion). Not in the bundle.
- No polymarket trading branches; no E5b; no E2.5 db column; no whole-file `strategies.yaml`.

## db.py index migration at restart (expected, not a hang)
`init_db()` runs `CREATE INDEX IF NOT EXISTS ix_audit_event_actor_kind` over `audit_event` (~1.19M rows) on
the first post-deploy startup — a single one-time build (~seconds; slightly slower startup). Additive +
idempotent; never rewrites rows. Prod currently has only `ix_audit_event_ts` (confirmed read-only).

## Prod paths
`/home/azureuser/trading_corp/` (WorkingDirectory). Files: `trading_corp/agents/divisions/bitunix_futures_observer.py`,
`trading_corp/agents/risk.py`, `trading_corp/main.py`, `trading_corp/persistence/db.py`, `config/strategies.yaml`.
venv: `/home/azureuser/trading_corp/venv`. Service `trading-corp` (PID 2797287 xvfb wrapper; engine = child python).
Up since 2026-06-16 04:55 UTC, NRestarts=0 (no restart since the batch deploy).

## Apply (operator — §4 gated; this prep does none of it)
1. Deliver staged tree to prod: `scp -r <deploy>/staged/* azureuser@trading.jacksumner.com:/home/azureuser/trading_corp/_deblock_stage/`
   (must contain `trading_corp/...` 4 files + `staleness_gate.snippet.yaml`).
2. Run the gated apply (no restart):
   `Get-Content <deploy>\deploy_apply_deblock_staleness.sh -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\r' | bash"`
3. Restart (operator, system service — agent never sudo): `ssh -t azureuser@trading.jacksumner.com sudo systemctl restart trading-corp`
4. Run VERIFY.md (layers a/c/d always; b only if a deps install was also done this window).

## Rollback (if not yet restarted, or to revert after)
`for f in trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/risk.py trading_corp/main.py trading_corp/persistence/db.py config/strategies.yaml; do mv "$f.bak-pre-deblock-2026-06-16" "$f"; done`
then restart. (The `ix_audit_event_actor_kind` index, if already built, is harmless to leave; or `DROP INDEX`.)
