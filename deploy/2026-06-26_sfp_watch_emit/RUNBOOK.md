# Runbook — SFP watch-state emit + loop heartbeat (2026-06-26)

Branch `bitunix-sfp-division-2026-06-25`. **STAGED, NOT deployed.** Operator runs every prod
write/restart; agent verifies read-only. **OBSERVE-ONLY change** — it lights up the cockpit's
Tier-B panels and does NOT alter trade behaviour. SFP stays **ARMED** throughout.

## What it does
Persists the SFP watch lifecycle the detector already computes (ARMED → CONFIRMED / INVALIDATED /
TIMED_OUT) into a new `sfp_watch_state` table, plus a per-cycle loop heartbeat in `agent_state`.
This unblocks the dashboard's armed-watch card + countdown, near-miss, BOS-confirm rate, and the
swept/BOS overlays. **Dashboard wiring is a SEPARATE step** — this is emit + schema only.

## Observe-only proof (the non-negotiable — detector is parity-locked to oracle `6e411762`)
- **Decision-path functions are BYTE-IDENTICAL to the deployed code** (AST-verified): detector
  `_maybe_fire`, `_advance_watch`, `_most_recent_swing_high`, `_is_pivot_low`, `compute_geometry`,
  `warm_start`; observer `_handle_signal`, `_place`, `_place_tp_leg`, `_resolve_position`,
  `_write_record`, `_persist_tp`, `_tp_alert`, risk-gate helpers. The ONLY changes are additive:
  a write-only `_transitions` buffer the detector appends to (and NEVER reads), `drain_transitions()`,
  and observer emit/heartbeat lines (each fail-soft).
- **Parity test green** — `test_parity_streaming_matches_oracle` + `…both_modes_across_seeds`
  (the deployed detector md5 `5c71a103` is the exact artifact those tests pass on).
- **`on_closed_bar` returns identical signals** whether or not the buffer is drained
  (`test_draining_does_not_change_signals`).
- **Emit is fail-soft** — a persist failure is logged + swallowed, never raises into the loop
  (`test_emit_is_fail_soft`).

## Gates (all GREEN)
- New tests `tests/test_bitunix_sfp_watch_emit.py`: **12/12** (each transition persists; watch_id
  idempotency on replay; recent-only window; heartbeat; fail-soft; signals unperturbed).
- Detector suite incl. parity: **10/10**. Full suite: **28 failed / 0 errors == baseline, ZERO new**
  (all 28 in the known non-bitunix files). py_compile clean.
- md5 Gate-A/B:

| file | method | Gate-A (prod-pre) | Gate-B (target) |
|---|---|---|---|
| `agents/strategies/bitunix_sfp.py` | full-file swap | `ad8e36f5` | `5c71a103` |
| `agents/divisions/bitunix_sfp_observer.py` | full-file swap | `db831daf` | `18da45f2` |

- **`bitunix.py` (broker) UNTOUCHED** (verified — no diff vs HEAD).

## Schema
`sfp_watch_state` (watch_id PK, fired_bar_ts, symbol, mode, swept_level, swept_wick,
bos_watch_level, status, status_ts, armed_ts, terminal_bar_ts, extra_json). watch_id =
`"{symbol}:{mode}:{fired_bar_ts_ms}"` (deterministic → idempotent UPSERT across restart). Heartbeat
→ `agent_state(agent='bitunix_sfp', key='loop_last_evaluated')` (no migration).

## Steps
**1 + 2. UPLOAD + ADDITIVE MIGRATION** (operator, ONE line):
`powershell -ep bypass -f .\sfp_emit_upload.ps1`
Uploads `staged/` → `~/sfp_emit_staged`, the apply script + migration, LF-normalizes, then runs the
**additive** `CREATE TABLE IF NOT EXISTS sfp_watch_state` (+ indexes) — safe with the engine UP, no
data touched. Prints staged md5 (expect `5c71a103` / `18da45f2`) + `sfp_watch_state rows=0`.

**3. APPLY CODE (gated, NO restart)** (operator, ONE line):
`ssh azureuser@trading.jacksumner.com "bash ~/apply_sfp_watch_emit.sh"`
Gate-A → backup `*.bak-pre-sfp-emit-2026-06-26` → atomic swap → Gate-B → py_compile. Aborts before
any write on a md5 mismatch. The new code is INERT until the restart.

**4. RESTART (loads the emit; observer/detector code does NOT hot-reload)** (operator, ONE line):
`ssh azureuser@trading.jacksumner.com "sudo -n systemctl restart trading-corp"`
SFP `auto_execute` stays `true` (armed) — this is observe-only; no re-arm needed.

**5. BOOT SMOKE** (agent, read-only): new PID, observer on `18da45f2` + detector on `5c71a103`
(= the parity-green artifact), `sfp_watch_state` present, SFP loop online + **armed + flat**,
`agent_state.loop_last_evaluated` ticking (and updating each ~15m cycle), warm-start back-filled
only recent (≤24h) watches, no tracebacks. (In-process parity can't be re-run live; the deployed
detector md5 == the CI-parity-proven artifact is the equivalent guarantee.)

## Rollback
Pre-restart: restore the two `*.bak-pre-sfp-emit-2026-06-26` files (nothing loaded yet).
Post-restart: restore the two backups + `sudo -n systemctl restart trading-corp`. The
`sfp_watch_state` table is additive + unread by the engine — leave it (harmless) or
`DROP TABLE sfp_watch_state` if desired. No trade path is affected either way.
