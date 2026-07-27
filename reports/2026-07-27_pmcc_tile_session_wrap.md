# Session wrap — PMCC tile fixes (2026-07-27)

Two PMCC dashboard fixes diagnosed, built, tested, and **DEPLOYED LIVE** this session; `prod-live`
re-synced to actual prod. Read `runbooks/deploy_log.md` (2026-07-27 ~15:49 + ~16:43 UTC entries) for the
authoritative "what's on prod" record. Memory anchors: `pmcc-tile-fix-deployed-2026-07-27`,
`pmcc-covered-call-mislabel-2026-07-27`.

## Current prod state (as of ~16:43 UTC)

- **prod runs `e97ebb0`** — PID **435217**, NRestarts=0, service active, 0 boot tracebacks (only the 2 known
  crypto-earnings ERRORs: EODHD/yfinance BTC/USD).
- **`prod-live` = `e97ebb0`** (pushed) — truthful again. `auto_execute:false` (robinhood_pmcc kill switch)
  unchanged; no halt exists/created; `robinhood_pmcc` armed **paper=False**.
- Broker (acct 461391328) untouched all session: one TSLA $335C short (`620e0f68`) + 2027-01-15 LEAP
  (`639b5a25`); the earlier roll order `6a676172` filled once, no duplicate. Nothing placed by any deploy.

## What shipped

1. **Stale-tile-after-execution fix** (`8b784b6`, DEPLOY_TIP `d553a3e`, ~15:49 UTC). A filled PMCC roll now
   writes a scan-overwritable `executed`/HOLD decision (Approve self-disables; next scan re-raises — no 8h
   blind spot); an `hx-swap-oob` fragment refreshes the tile badge with no reload; the false "tile & panel
   in sync" banner is gone. Branch `claude-pmcc-tilefix-2026-07-27`; reports
   `reports/2026-07-27_pmcc_tile_stale_rollshort_{investigation,fix}.md`.
2. **structure_type classifier fix** (`e97ebb0`, ~16:43 UTC). Tile type badge classifies by *what covers the
   short* (long call → PMCC at any DTE; shares ≥100/contract → covered_call), not by LEAP DTE — so aged
   2027-01-15 LEAPs read PMCC. All 10 tiles verified PMCC. Branch `claude-structtype-2026-07-27`; reports
   `reports/2026-07-27_pmcc_covered_call_mislabel_diagnosis.md` + `_pmcc_structure_type_fix.md`.

## Open items / next steps

- **[P2] `prod-live` 16-commit drift audit** (new `BACKLOG.md` entry). The pointer is now correct, but audit
  `e4219b3..0bdc3e0` (kalshi S2 + P1/P2) to confirm every change is on a named branch + `deploy_log.md`
  entry and no prod-direct edit is uncaptured.
- **[gated] Kalshi copy Sept re-selection / autopause flip / P3 UI** — unchanged, gated on n≥30 (BACKLOG).
- **(optional) structure_type follow-on:** the classifier is now shares-aware but the division holds no
  shares-backed short today (STRC has shares, no short). If one ever appears it will label `covered_call`
  correctly — no action needed unless that materializes.

## Deploy mechanics / hazards learned (reuse next time)

- **prod-live was STALE** — always Gate-A against **actual prod** (`claude-2026-07-26`/`d553a3e`/now
  `e97ebb0`), never the `prod-live` tag blindly. It's honest again now, but confirm before trusting it.
- **prod-venv pytest is broken** by web3's `pytest_ethereum` plugin (`eth_typing` ImportError crashes
  pytest startup for ANY test). Run prod-venv tests with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.
- **ssh from PowerShell:** pipe complex remote scripts via STDIN with **BOM+CR strip** —
  `Get-Content x.sh -Raw | ssh host "tr -d '\357\273\277\r' | bash"` (Write-tool `.sh` files carry a UTF-8
  BOM that plain `tr -d '\r'` misses → `﻿cd: command not found`). `scp` (Windows OpenSSH) is byte-safe for
  non-ASCII + `/home` paths. systemctl stop/start via `az vm run-command RunShellScript` (root, no sudo;
  g=RG-SHARED-PROD n=tc-prod-vm). Dashboard on `127.0.0.1:8000`.
- **git commit messages:** here-strings with `>`/`->`/em-dash mangle under PowerShell → use `git commit -F <file>`.

## Branches (all pushed)

`claude-pmcc-tilefix-2026-07-27`, `deploy-pmcc-tilefix-2026-07-27`, `claude-structtype-2026-07-27`,
`claude-2026-07-27` (investigation + diagnosis reports), `prod-live` (@ `e97ebb0`).
