# P1 DEPLOY SEQUENCE (pre-staged 2026-08-22; execute on Jack's return / authorization)

**Status: NOT executed. Every step below is a queued MUTATION** (box file copy, PM-DB writes,
crontab edit, git advance). Read-only steps are marked `[RO]`. Nothing here has run.

> **★★ BLOCKING (2026-08-22, `QUARANTINE_RECONCILE_2026-08-22.md` / §13A(f)):** live reconciliation showed
> §3A **clause (a)** false-positives on real single-game MLB losses (excludes real losses -> biases the
> scoreboard UP). **Steps 0-4 (deploy + INGEST) are safe** — data is captured, `pnl_suspect` is advisory —
> but **Step 5's report/ranking is NOT trustworthy until Jack decides the clause-(a) rework.** Do not use the
> scoreboard for whale selection until then. Step 3 is now a DIAGNOSTIC checkpoint (confirm the clause-(a)
> over-exclusion on real data before ranking). Clause (b) is sound.

**Pre-flight facts verified read-only 2026-08-22 (this session):**
- Box uses the **nested** package layout: `/home/azureuser/trading_corp/trading_corp/` (package)
  inside repo root `/home/azureuser/trading_corp`. All three deploy targets land correctly and are
  currently **absent** (clean first deploy): `trading_corp/prediction_markets/`,
  `trading_corp/scripts/pm_cli.py`, `config/pm_seed_wallets.yaml`.
- `trading_corp/__init__.py` already present on box -> deploy ships ONLY the 3 PM paths.
- Seed roster resolves on box, **size = 12** (2 live MLB + 10 PCT-selected). Full list in
  NET_VERIFY_TARGET.md.
- Both runners parser-validated, 0 non-ASCII, no BOM.

**CLI flag correction (plan-prose drift):** the built CLI has **no `--from-rosters` flag** — the
12-wallet seed roster is the DEFAULT source of `backfill`/`refresh` (via `load_seed_roster`, legacy
`data/trading_corp.db` mode=ro). Plain `backfill` == "backfill from rosters." `--only-wallets`
bypasses the roster for the single-wallet checkpoint. The plan's `--from-rosters` text is descriptive,
not a real flag; commands below use the ACTUAL flags.

Box command prefix (run from repo root, existing venv, no sudo):
`cd /home/azureuser/trading_corp && PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py`

---

## Step 0 — DEPLOY (additive file copy)
Runner (Jack pastes one line): `powershell -ep bypass -f .\pk_pm_deploy.ps1`
- Additive copy of `trading_corp/prediction_markets/`, `trading_corp/scripts/pm_cli.py`,
  `config/pm_seed_wallets.yaml`. NO restart, NO sudo, NO existing-file edits (tar holds only PM paths).
- **Confirm:** `DEPLOY_DONE` printed; box `sha256sum` matches the LOCAL sha256 the runner prints; the
  pre-check shows all three targets were `absent (new)` before extract.
- Rollback at any point: `powershell -ep bypass -f .\pk_pm_rollback.ps1`.

## Step 1 — g0-validate  `[RO]` (live API read, no DB writes)
`... pm_cli.py g0-validate`
- **Gate:** must print `G0 PASS` and exit 0 (proves negative `realized_pnl` rows exist for the known
  losers). **If it prints `G0 FAIL` -> STOP AND REPORT. No pre-authorized pivot.**

## Step 2 — dry-run roster  `[RO]` (no API, no DB writes)
`... pm_cli.py backfill --dry-run`
- Prints `{"dry_run": true, "count": 12, "wallets": [...]}`.
- **Confirm:** count = 12; the list contains Kickstand7
  `0xd1acd3925d895de9aec98ff95f3a30c5279d08d5` and pako `0x71edffd0d70a1da823ff07a3c6fc81457294d338`.
  This is the authoritative deploy-time roster (supersedes any memory list).

## Step 3 — SINGLE-WALLET CHECKPOINT (MUTATION: writes PM DB, ONE wallet) -> then STOP
Wallet = **Kickstand7 `0xd1acd3925d895de9aec98ff95f3a30c5279d08d5`** (rationale: NET_VERIFY_TARGET.md
and below).
`... pm_cli.py backfill --only-wallets 0xd1acd3925d895de9aec98ff95f3a30c5279d08d5`
- Ingests Kickstand7 only; runs rollup + compute_scores; prints a summary JSON.
- **Then INSPECT (read-only) and STOP for Jack** — report these three things:
  1. **Exclusion counts** (does the §3A quarantine fire on real Fed negRisk data?):
     `SELECT pnl_suspect, suspect_reason, COUNT(*) FROM pm_closed_position GROUP BY 1,2;`
     `SELECT category, n_resolved, n_excluded, ROUND(excluded_pnl,2), ROUND(net_realized_pnl,2), data_quality FROM pm_category_stats ORDER BY n_resolved DESC;`
  2. **Category coverage** (tier-1 + tier-2, unknown fraction):
     `SELECT category, category_source, COUNT(*) FROM pm_closed_position GROUP BY 1,2 ORDER BY 3 DESC;`
  3. **Sample rows** (eyeball the quarantined artifacts vs clean rows):
     suspect: `SELECT event_slug, ROUND(total_bought,2), ROUND(realized_pnl,2), suspect_reason FROM pm_closed_position WHERE pnl_suspect=1 ORDER BY realized_pnl LIMIT 10;`
     clean:   `SELECT event_slug, category, ROUND(total_bought,2), ROUND(realized_pnl,2) FROM pm_closed_position WHERE pnl_suspect=0 ORDER BY ABS(realized_pnl) DESC LIMIT 10;`
     plus `... pm_cli.py report --min-resolved 1`
- (Inspection queries run via `venv/bin/python` sqlite on `data/prediction_markets.db`, read-only, or
  via a read-only runner generated at execution time.)
- **HARD STOP.** Jack inspects the three outputs and authorizes the full run. Do not proceed unprompted.

## Step 4 — FULL 12-wallet backfill (MUTATION) — only after Jack authorizes at Step 3
`... pm_cli.py backfill`
- All 12 roster wallets, per-wallet try/except isolation (one wallet failing does not abort the batch).
- Acceptance: >=3,000 rows landed; re-run produces identical counts (idempotent).

## Step 5 — report (MUTATION already done in step 4; this is read of scores)  `[RO]`
`... pm_cli.py report --min-resolved 10 --routine net_roi`
`... pm_cli.py report --min-resolved 10 --routine recency_weighted`
`... pm_cli.py report --format json` (must parse)
- Acceptance (P1_PLAN §12, CORRECTED): coverage bar is now scoped to the four LIVE categories (§13A(e)),
  not ">=85% non-unknown over all rows"; a net-loser shows negative ROI; the **SDTrading (MLB, binary)
  net-verify** matches an independent API sum on the FULL net (NET_VERIFY_TARGET.md) — closes §13A(a).
- **★ GATED:** per the BLOCKING banner (§13A(f)), the ranking is NOT trustworthy until the clause-(a) rework
  is decided — clause (a) drops real single-game losses. Report may be RUN for inspection; do not act on it.

## Step 6 — nightly cron install (MUTATION: crontab edit, append-only)
Recommended slot **03:20 UTC** (clear of the top-of-hour `replay_audit_event` cron and the 03:02-03:12
OS-timer cluster; separate DB means no lock contention regardless — see QUEUED_BOX_VERIFICATION.md §5).
Append preserving all existing lines:
`(crontab -l 2>/dev/null; echo '20 3 * * * cd /home/azureuser/trading_corp && PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py refresh >> /home/azureuser/pm_refresh.log 2>&1') | crontab -`
- Verify `crontab -l` shows the new line AND the pre-existing lines intact.
- The line contains `pm_cli.py`, so `pk_pm_rollback.ps1` removes exactly this line and nothing else.

## Step 7 — prod-live advance (git; standing rule)
- Advance `prod-live` with the deployed artifacts only (additive: PM package, `pm_cli.py`, seed yaml,
  `reports/prediction_markets/`, `tests/prediction_markets/`, runners). Push `origin/prod-live`.
- Sequence, never race, against any pending MACE prod-live advance (additive + no-restart cannot
  collide with the MACE no-restart-after-15:45-ET constraint, but still serialize).

## Step 8 — merge phase branch -> durable integration branch (git)
- `git checkout prediction-markets && git merge --no-ff prediction-markets-p1` -> push
  `origin/prediction-markets`.
- **NO merge to `main` until the single deliberate cutover merge (P3).** Full dev history lives on
  `origin/prediction-markets`; prod-live carries running artifacts only.

---

## ROLLBACK (any time)
`powershell -ep bypass -f .\pk_pm_rollback.ps1`
- Deletes `trading_corp/prediction_markets/`, `trading_corp/scripts/pm_cli.py`,
  `config/pm_seed_wallets.yaml`; removes ONLY the PM cron line (preserves all others); LEAVES
  `data/prediction_markets.db*` in place (inert once the package is gone — a separate file no engine
  reads). NO restart, NO sudo, NO existing-file edits. Git: reset the prod-live advance if step 7 ran.

## Why Kickstand7 for the Step-3 checkpoint (rationale CORRECTED 2026-08-22 to match live data)
- **1-of-12 roster member** (PCT-selected) -> Step 3 is a true preview of the full run, not an
  off-roster canary. (d1k21 — the -$17M/-168k negRisk case — is a G0 loser, NOT in the roster.)
- **Exercises the quarantine on live data — EVIDENCED, not assumed** (`QUARANTINE_RECONCILE_2026-08-22.md`):
  104 suspect rows (72 clause-b negRisk phantoms across politics/`nba-mvp`/`ufc-281`; **3 of 83 Fed** incl a
  tb=0/rp=-0.50 dust leg on `fed-interest-rates-january-2025` propagating to 2 winner legs, $20,121 excluded).
  My original "trips the invariant" wording was UNEVIDENCED when written AND contradicted the then-record
  "Fed proven clean"; the probe confirms it fires. **NOTE — the exercise revealed the clause-(a) defect** (see
  the BLOCKING banner): Step 3's inspection is DIAGNOSTIC — verify whether clause (a)/propagation OVER-exclude
  (esp. compare vs an MLB whale, where clause (a) misfires on real losses) before trusting any ranking.
- **Largest Fed footprint** in the roster -> most data to judge the quarantine's precision.
- **Net-verify target MOVED to SDTrading (MLB, binary)** per §12 + Jack's Issue-2 ruling — Kickstand7 (Fed,
  negRisk, quarantine-firing) is the wrong clean-baseline target. See NET_VERIFY_TARGET.md.
- Explicitly **NOT an evanng UFC slice** (per Jack) — the unresolved §13A(a) three-way disagreement.
