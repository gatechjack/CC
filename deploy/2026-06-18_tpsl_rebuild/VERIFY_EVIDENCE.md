# VERIFY evidence — tpsl-rebuild deploy (2026-06-18, restart 22:55:23 UTC)

Operator restart via az run-command, ProvisioningState succeeded. All checks read-only.

## Section A — confirms-at-restart: ALL PASS

| # | check | result |
|---|-------|--------|
| A1 | engine up, NEW PID | `MainPID=2988577` (≠2926399), active/running, NRestarts=0, boot 22:55:23 UTC |
| A2 | 3 files == target md5 | 74aa1b42 / 19da15ff / 707c6828 ✓ |
| A3 | main.py/db.py unchanged | f16e9c24 / a2c2ff46 ✓ |
| A4 | re-arm | ExecStart `--live --brokers bitunix --live-divisions bitunix_futures` ✓ |
| — | bitunix REAL broker | `Registered bitunix_futures broker (paper=False)`; `BitunixBroker connected (equity=$258.26, 0 positions)` ✓ |
| — | execution_mode:live | strategies.yaml `execution_mode: live` ✓ |
| — | DD-cap 0.99 | risk.yaml bitunix_futures `per_account_max_drawdown_pct: 0.99` (global default 0.15) ✓ |
| — | B2 maker OFF | `maker_entry_enabled: false` (`tp_is_maker: false`) ✓ |
| — | staleness gate | `BitUnix staleness-reject gate (C): enabled=True margin_s=120` ✓ |
| — | reconciler clean/flat/no-orphan | `position`=0; `bitunix restart-resume at startup: matched=0 orphan=0 case_c_deferred=0`; reconciler started (60s, trail 1.50, 3m) ✓ |
| — | liveness (freeze-fix proxy) | bar caches primed (3m/1h/4h/1d), scan cycles ticking, research_firm emitting — engine responsive ✓ |

## ERROR-line triage (none from this deploy, none bitunix)
- `azure.core … Response status: 404` ×3 (22:55:24-25) — azure SDK HTTP INFO logging (NOT bitunix /tpsl/; matched grep on "404"). Benign.
- `ERROR … fidelity_joint/fidelity_401k broker connect failed: BrowserType.launch ENOENT … ms-playwright/firefox … lock` — pre-existing Playwright/Fidelity issue; gracefully `broker_fallback_to_paper`. Unrelated to bitunix/this deploy.
- `ERROR yfinance: BTC/USD: No earnings dates found` — benign yfinance noise (crypto has no earnings). Pre-existing.
- NO bitunix Traceback, NO 30038, NO bitunix /tpsl/ 404 post-restart.

## Section B — needs a LIVE ENTRY (NOT confirmable at restart; observe on first post-deploy trade)
- TP legs REST as `/tpsl/` orders (`get_pending_orders` shows them; no 30038).
- SL-trail uses `/tpsl/position/modify_order` — NO 404 (the live-confirmed failure fixed).
- Position SL places + auto-reduces on a partial.

## Validation-window flags (operator decides; fail-soft, B1 guards)
- (a) B1 entry-stop ↔ separate Position SL coexistence — no 30038 when both present.
- (b) 3-leg validation needs ≥ 0.0012 BTC (TIER_SIZING). Equity $258.26; last BTC ~62,859 → 0.0012 BTC ≈ $75 notional, achievable. Operator decides sizing.

## Result
DEPLOY VERIFIED at restart — Section A all green. Section B + validation flags pending the first post-deploy live bitunix entry.
