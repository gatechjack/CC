# Prod vs origin/main file-level sweep — operator analysis

Companion to the auto-generated `2026-05-30_prod_vs_main_file_level_sweep.md` (sweep tool's raw output). This document adds:

- Cross-references between MISSING_ON_PROD entries and `main.py` import sites (where the gap would actually crash startup vs. dormant code).
- A definitive transfer-set baseline for redeploy attempt #3 (the set of 65 files that MUST be in the transfer manifest, regardless of `git diff` against any pointer).
- PROD_ONLY_NOT_ON_MAIN anomalies that warrant investigation.

**Generated:** 2026-05-31 ~01:00 UTC by the sweep tool's authorized Option-C probe (single bundled az run-command, gzip+base64-compressed payload, SWEEP_BEGIN/END markers verified, 251 expected files all accounted for).

**Prod systemd status at probe time:** `MainPID=1874494 NRestarts=0 ActiveState=active` (post-rollback stable since 2026-05-30 23:09 UTC).

## Summary counts (cross-checked: 185 + 1 + 51 + 14 = 251 ✓)

| Status | Count | Notes |
|---|---|---|
| MATCH | 185 | Prod md5 == origin/main LF md5 |
| DIFFER-EXPECTED-PER-DEPLOY-LOG | 1 | `config/strategies.yaml` — 03:57 UTC bitunix paper-sizing sed-overlay (deploy_log documented) |
| DIFFER-STALE-ON-PROD | 51 | Prod has older copy; MUST be in next deploy's transfer set |
| MISSING_ON_PROD | 14 | File on main, not on prod's disk; MUST be in next deploy's transfer set |
| PROD_ONLY_NOT_ON_MAIN | 18 | Mostly historical `.bak`/`.orig`; 3 require investigation |

## MISSING_ON_PROD — cross-reference against main.py imports

### Main.py-direct imports (would crash startup deterministically if reached)

| File | main.py line | Import context |
|---|---|---|
| `trading_corp/agents/divisions/tasty_options.py` | 1234 | Inside IC v1 setup block — **unconditional** import; reached on every startup |
| `trading_corp/agents/strategies/tasty_options_iron_condor.py` | 1235 | Same — unconditional |
| `trading_corp/brokers/tastytrade.py` | 1867 | Inside `if family == "tastytrade":` (broker-construction switch). Reached when constructing a tastytrade-family division's broker. |

### Transitively imported (would crash via cascade if importer is reached)

| File | Importers (any of these triggers cascade) |
|---|---|
| `trading_corp/brokers/bitunix_exceptions.py` | `agents/data_exec.py`, `agents/divisions/bitunix_futures_observer.py`, `brokers/bitunix.py`, `brokers/bitunix_symbols.py` |
| `trading_corp/brokers/bitunix_symbols.py` | `brokers/bitunix.py` |
| `trading_corp/data/nbm_client.py` | `agents/strategies/kalshi_weather_arb.py`, `agents/strategies/_weather_math.py`, `web/data.py` |
| `trading_corp/data/residual_logic.py` | Same as nbm_client |
| `trading_corp/data/iem_cli_client.py` | (no in-tree importers — possibly an operator CLI, dormant) |

### Dormant (no main.py reachability)

| File | Status |
|---|---|
| `trading_corp/path_logger/__init__.py` | Standalone package; `__main__.py`/`__init__.py`/etc. only reference each other. NOT imported by main.py. |
| `trading_corp/path_logger/__main__.py` | Same |
| `trading_corp/path_logger/logger.py` | Same |
| `trading_corp/path_logger/main.py` | Same |
| `trading_corp/path_logger/store.py` | Same |
| `trading_corp/scripts/analyze_polymarket_whale.py` | Script (CLI), not imported. |

### Why prod isn't currently crashing

Prod is internally consistent at a STALER snapshot. The DIFFER-STALE list confirms it:
- `trading_corp/brokers/bitunix.py` is stale on prod — the OLD version doesn't import `bitunix_exceptions.py`. The new origin/main version does.
- `trading_corp/agents/strategies/kalshi_weather_arb.py` is stale — OLD version doesn't import `nbm_client.py`. Similarly stale `_weather_math.py`.
- `trading_corp/main.py` itself is stale — OLD main.py doesn't have the tasty_options imports at line 1234.

The system on prod runs because the OLDER coherent set is all present and self-consistent. Any partial deploy that brings the NEW main.py without the new dependencies it references will crash on the first reachable import.

This explains the layered rollback pattern: every prior deploy attempt brought *some* new files but not *all* of them, exposing the inconsistency at whichever import was reached first.

## Recommended transfer set for redeploy attempt #3 (65 files)

The transfer set MUST be the UNION of:

- **51 DIFFER-STALE-ON-PROD files** (from the raw sweep report)
- **14 MISSING_ON_PROD files** (from the raw sweep report)

= 65 files in total.

**Not** in the transfer set:
- `config/strategies.yaml` — DIFFER-EXPECTED-PER-DEPLOY-LOG (known sed-overlay). The 03:57 UTC bitunix paper-sizing overlay must be RE-APPLIED via sed after transfer, NOT overwritten. The transfer set should explicitly exclude this file OR include + re-sed-overlay it post-transfer.
- 185 MATCH files — already byte-identical on prod.

**Operator handling for the overlay (per `[[bitunix-risk-tier-and-leverage-pre-live]]`):**

> Either re-merge the overlay branch first (preferred — gets the overlay onto origin/main), OR re-apply the sed after deploy. The branch `bitunix-risk-tier-pre-live` carries the overlay at HEAD `2a3d20c`; merging to main rounds this into origin/main and the future deploys carry it canonically.

## PROD_ONLY_NOT_ON_MAIN — categorized for operator review

**Historical `.bak`/`.orig` files (15 entries — safe to ignore; cleanup is P3):**
- `config/strategies.yaml.bak-day600-20260515-214835`
- `config/strategies.yaml.bak-h2-20260516T174505`
- `config/strategies.yaml.bak-h2-20260516T185125`
- `config/strategies.yaml.bak.2026-05-29-kalshi-disable`
- `config/strategies.yaml.orig`
- `trading_corp/agents/logger.py.bak-dblock-20260529`
- `trading_corp/agents/paper_trade_replay.py.bak-tgdiag-20260528`
- `trading_corp/agents/risk.py.bak-p2-scopeleak-20260515-222357`
- `trading_corp/agents/strategies/kalshi_copy_trader.py.bak-pre-e5efa06-20260528-044249`
- `trading_corp/agents/strategies/kalshi_crypto_arb.py.bak-fixd-20260516-005859`
- `trading_corp/agents/strategies/kalshi_weather_arb.py.bak-fixd-20260516-005859`
- `trading_corp/comms/bitunix_lifecycle_notifier.py.bak-phasec-20260529`
- `trading_corp/comms/bitunix_lifecycle_notifier.py.bak-tgdiag-20260528`
- `trading_corp/comms/telegram_bot.py.bak-phasec-20260529`
- `trading_corp/persistence/db.py.bak-dblock-20260529`

These follow the documented deploy-backup convention (`<file>.bak-<label>-<date>` or `<file>.bak.<label>-<date>`). The `.pre-*` skip filter in the sweep tool already excludes deploy-backups created by the canonical recipe; these are older `.bak-*` artifacts from various ad-hoc sessions. **They're inert** — never imported by main.py, never read by any code path.

**Anomalies for operator review (3 entries):**

1. **`config/Lets`** — file named "Lets" with no extension under `config/`. Origin unknown. Likely an accidental `git checkout` typo / fragment artifact (e.g., shell expansion of `Lets-something` or a paste error). Not a YAML config. Read content to classify before deciding to clean up. **P3 BACKLOG.**

2. **`trading_corp/main.py.orig`** — uncommitted backup of `main.py`. Either a forgotten manual backup before some past edit, or a `cp main.py main.py.orig` pattern from a deploy script. Compare its md5 against origin/main's various commit history to identify which version it captures. **P3 BACKLOG — cleanup after verification.**

3. **`trading_corp/agents/divisions/_observer_test.py`** — looks like a forgotten one-off test scaffold left on prod. The underscore prefix suggests test/scratch use. Verify it's not imported by anything before deleting. **P3 BACKLOG — cleanup.**

**Note:** all three require READ-ONLY inspection (single-line `cat` via az) to classify; do not delete in this session per the read-only constraint.

## What this sweep proves about the Stage-1 redeploy class of bug

The 22:43 redeploy rollback's `TypeError: WebDeps.__init__() got an unexpected keyword argument 'tasty_division'` was a **single instance** of a broader pattern:

- Origin/main internally consistent ✓
- Prod's filesystem holds an older internally-consistent snapshot ✓
- Deploys that bring some-but-not-all new files create a transient inconsistency that crashes at whichever import/construction site is reached first ✓

The sweep tool's standing discipline (extension of `[[pre-deploy-filesystem-audit-discipline]]` from "audit-not-stale" to "transferable-surface sweep") catches this class of bug BEFORE the deploy.

Next deploy attempt must use this report (65-file transfer-set baseline) instead of a `git diff <pointer>..origin/main`-derived manifest. The diff-derived manifest is what caused the 22:43 rollback.

## Memory + BACKLOG references

- `[[deploy-transfer-set-diff-derived-misses-stale-prod-files]]` — the standing-rule fix (filed this session, Item 3 docs).
- `[[file-level-prod-vs-main-sweep-as-standing-discipline]]` — to be filed at session close (Phase 4).
- BACKLOG P1: redeploy attempt #3 uses this report as transfer-set baseline.
- BACKLOG P1 (new): investigate why core dependencies have never been deployed — operator-curated investigation of deploy history.
- BACKLOG P3 (new): `config/Lets`, `main.py.orig`, `_observer_test.py` cleanup.
