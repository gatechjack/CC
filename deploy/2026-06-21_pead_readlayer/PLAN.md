# Combined restart window — 2026-06-21

ONE restart activates three already-prepared payloads. Agent stages/verifies
(read-only SSH); **operator runs the scp + `sudo systemctl restart`.**

## Payload
1. **PEAD read-layer** (NEW this window) — 7 files via `pead_apply.sh`:
   - shared, prod==base, drift-gated additive superset: `web/routes.py`, `persistence/db.py`
   - new: `web/pead_view.py`, `agents/strategies/pead_pressures.py`, `persistence/pead_observability.py`, `web/templates/pead_live.html`, `web/templates/partials/pead_live_sections.html`
2. **metrics-epoch split + foundation** — ALREADY staged on prod disk (dormant); restart activates. Epoch row stays UNSET.
3. **issue1 managed-exit-suppress** — ALREADY staged on prod disk (dormant); restart activates.

## Excluded / deferred (NOT this window)
- `config/strategies.yaml` — prod 925b9783 carries D4-flag/live config; DIVERGENT from main → DEFER to Phase 2 (surgical superset). The robinhood_pead block is inert for the read-layer.
- PEAD data-foundation (`earnings_provider`, `market_data_provider`, `secrets`, `data_providers.yaml`) + research modules (`pead_signal`, `pead_backtest*`) — Phase-2 plumbing, not needed by the dashboard; deferred to minimise prod surface.
- **D1** — not built, gated on D4; not in any payload.
- **D4 guard CODE** — already live on prod (unchanged). Only the read-only WATCH ends at restart → re-arm a fresh one after.

## Collision proof (the silent-revert guard)
PEAD ships NONE of the bitunix-carrying shared files: `main.py` (97a4d676), `web/data.py` (dae49424), `division.html` (b6e23456) are NOT in the payload; `strategies.yaml` (925b9783) deferred. The 5 shared files PEAD does touch were prod==base (zero bitunix delta); only `routes.py`+`db.py` ship, drift-gated so the installer ABORTS if prod ≠ base.

## Boot-smoke (PASS)
`test_boot_smoke.py` on PEAD+issue1 exact prod files (84/84) + PEAD routes register; metrics exact stage `data.py` (7/7).

## Sequence (operator)
1. `bash halt_bitunix.sh` — durable bitunix entry-halt (survives restart).
2. Confirm bitunix flat-and-settled (0 open, last trade recorded).
3. scp + `bash pead_apply.sh` — installs PEAD 7 files (drift-gated, md5-verified, NO restart).
4. `sudo systemctl restart trading-corp` — ONE restart; activates PEAD + metrics + issue1.
5. Work `VERIFY.md` top→bottom.
6. Re-arm D4 watch → `bash unhalt_bitunix.sh`.
7. Reconcile + append this deploy to `runbooks/deploy_log.md` (currently stale — last entry 2026-05-29).
