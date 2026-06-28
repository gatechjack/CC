# RUNBOOK — bitunix_sfp Mode B (15m SFP → 3m BOS) deploy, 2026-06-28

Two restarts: **#1 paper dry-run → STOP for operator confirm → #2 live flip.**
Operator runs all prod writes + restarts (flat-guarded). Agent runs read-only
boot smokes + verify. See `MANIFEST.md` for files + md5s. The agent will generate
the exact one-line `.ps1` runners (per the command-paste rule) at deploy time.

## Pre-flight (read-only)
1. Account FLAT, reconciler clean, no open `bitunix_sfp` live rows.
2. Re-run drift gate `python scripts/bitunix_prod_surface_md5diff.py` → clean
   (now includes the two SFP modules). Confirm `main.py` base md5 still `2c1bb1dc`.
3. Back up the 5 prod files (`*.bak-pre-modeb-2026-06-28`).

## RESTART #1 — paper dry-run
Push (LF blobs, `git show HEAD:<f> | tr -d '\r' | ssh "cat > <prod>/<f>"`):
- `trading_corp/agents/strategies/bitunix_sfp.py`  → target `91fd7672`
- `trading_corp/agents/divisions/bitunix_sfp_observer.py` → `8a916526`
- `trading_corp/main.py` → `2ff188c7`
- `scripts/bitunix_prod_surface_md5diff.py` → `f9e2979b`
- `config/strategies.yaml` **paper variant** (`execution_mode: paper`)

Then `sudo systemctl restart trading-corp` (NOPASSWD). md5-verify each pushed
file == target before restart.

**Boot smoke (read-only, expect):**
- New MainPID, NRestarts=0, active.
- Log: `bitunix_sfp observer wired: symbols=[4 coins] ... mode_b=True symbol_modes={...}`.
- Log: `bitunix_sfp 3m-master loop spawned (Mode B; 4 15m + 4 3m cache(s))`.
- `agent_state` `bitunix_sfp/loop_last_evaluated` ticks on the **3m** boundary (was 15m).
- 15m caches primed (≥101 bars) AND 3m caches primed; no SFP-loop traceback.
- If a Mode-B watch arms: `sfp_watch_state` row appears (any of the 4 symbols),
  ARMED; on a 3m BOS a CONFIRMED row + a `would_have_placed` paper record with
  `extra_json.source_signal` = `sfp_*_3m_bos`. (No live order — execution_mode=paper.)
- bitunix_futures still HALTED-INERT; PEAD unaffected; engine FLAT.

**→ STOP. Operator reviews the dry-run, then confirms restart #2.**

## RESTART #2 — live flip (after operator confirm)
ARM gate: account FLAT + reconciler clean + no open `bitunix_sfp` live rows.
Push `config/strategies.yaml` **live variant** (HEAD, `84001f67`,
`execution_mode: live`) → `sudo systemctl restart trading-corp`. md5-verify == `84001f67`.

**Boot smoke (read-only, LIVE):**
- `Registered ... bitunix_sfp ... (paper=False)`; mode_b=True; 3m-master loop spawned.
- Boot-guard PASSED (exactly one bitunix division live).
- Per-account reconciler bound to the SFP broker; startup reconcile clean.
- `TC_LIVE_AUTHORIZED=LIVE` unchanged; engine FLAT.

## First-live Mode-B A/B verify (next real fill)
- Order `extra_json.source_signal` ∈ {`sfp_real_3m_bos`,`sfp_considerable_3m_bos`},
  `bos_tf=3m`. B1 stop ≈ `swept_low − 0.1%`. Entry = next 3m open.
- 2R native `/tpsl/` reduce-only TP leg rests; reconciler match_count=1; cockpit
  TIER-A/C populate.
- SOL/XRP signals write PAPER records + `sfp_signal_watch_only` audit (never live).

## Rollback
Restore `*.bak-pre-modeb-2026-06-28` (5 files) + restart. (Reverts to Mode-A
BTC-only 15m-BOS.) Then `git revert` the branch if merged.

## Post-deploy
Append `runbooks/deploy_log.md` (target md5s, baseline 28F/0E + 10 Mode-B). Update memory.
