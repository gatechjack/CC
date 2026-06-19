# mc_a_yellow_x declassification — targeted LIVE config edit (operator-gated)

**Date:** 2026-06-19 · **Status:** PREPARED, NOT applied. The deferred config half of the P2 work
(the code half is live as of the combined redeploy `dd9016a`). CONFIG WRITE + needs a RESTART.

## What / why
`mc_a_yellow_x` is a **non-directional** whale/manipulation / tape-anomaly flag that was miscategorized
in `config/strategies.yaml` as a `side: buy` directional factor — it added **spurious bull points** to the
score. This removes the single factor block (replacing it with a doc comment), so the scorer treats it like
the other non-directional signals (absent from `factors:` → `btc_accumulator.evaluate_confluence` ignores
it → 0 directional points) while it still flows through the alert ledger. **NOT** flipped to `side: sell`
(that's the same error inverted).

## Why a TARGETED edit (not a full-file deploy)
Prod `config/strategies.yaml` (`569c38f8`) carries live operator settings (execution_mode, DD-cap 0.99,
kalshi divisions) and has **drifted from the repo branch config** — a full-file deploy would clobber them.
`apply_yellowx_declassify.py` does an **exact-match removal of only the `mc_a_yellow_x` block**, byte-
preserving the rest. It is **fail-closed**: it writes ONLY if the exact expected block is found exactly once
(prod's block is byte-identical to the expected text — verified). Tested locally: match count = 1, yellow_x
removed, all other factors (e.g. `mc_a_redx`) preserved, YAML still valid.

## Procedure (agent scp's + runs; operator restarts)
1. **Agent (SSH):** scp `apply_yellowx_declassify.py` to prod, then run it with the prod venv python:
   `/home/azureuser/trading_corp/venv/bin/python apply_yellowx_declassify.py`
   → it backs up to `strategies.yaml.bak-pre-yellowx-2026-06-19`, removes the block, re-parses the YAML,
   asserts `mc_a_yellow_x` is no longer a factor, and prints the before→after md5. ABORTS (no write) if the
   exact block isn't found exactly once.
2. **Operator restart** (config-and-restart, no hot-reload — the change is inert until a restart):
   `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "systemctl restart trading-corp"`
   (or the Board-approved `ssh "sudo -n systemctl restart trading-corp"`). Prefer flat.
3. **VERIFY (read-only):** `grep -c 'mc_a_yellow_x:' …/strategies.yaml` → 0 active factor (only the comment);
   the engine boots clean; an `mc_a_yellow_x` alert no longer adds bull points (observe in scoring audit).

## Rollback
`mv strategies.yaml.bak-pre-yellowx-2026-06-19 strategies.yaml` + restart → restores the prior config.

## Notes
- Low urgency: the diagnosis found yellow_x is **immaterial to the result/exit_kind skew** — this is a
  scoring-hygiene fix, independent of the (already-live) classifier fix. Batch it with the next restart if you
  prefer, rather than bouncing the engine just for this.
- Separate from the record-correction SQL (`deploy/2026-06-19_p2_record_correction/`).
