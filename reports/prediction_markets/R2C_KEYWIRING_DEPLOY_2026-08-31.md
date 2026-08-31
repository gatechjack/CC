# Stage 5 R2c + prompt rung + scoped KV key-wiring -- DEPLOY STAGING (2026-08-31)

Branch `pm-optiond-whale-exit-2026-08-31` @ **`9b8e79a`** (pushed, local==origin). Full PM suite GREEN on box-scratch
(0 fail, 1 skip, py_compile OK, 2026-08-31T17:53Z). **NOTHING deployed / NOTHING restarted / no unit edited yet.**

This is a **pm_web-only** deploy: no engine files, no DB schema change (no migration), no order path, no bitunix
bounce. It bundles three things into ONE pm_web restart, with the Anthropic key wired **LAST**.

---
## Deploy shape (ONE pm_web restart, key LAST) -- Jack ruled 2026-08-31
1. **R2c** -- `/farm/analyze` re-grounds the loss set from /activity (cache-miss only; a hit spends nothing) and the
   template shows the loss-completeness block (recovered held-to-worthless losses + measured omission % + bound).
2. **Prompt rung** -- the grounded loss set flows into the narrator prompt (new top caveat tier + honest W/L lines);
   `skill_version` **2 -> 3 (final)**. Settling the version now means the FIRST paid narration is final-form.
3. **Scoped KV fetch** -- `scripts/pm_web.py` pulls ONLY `ANTHROPIC-API-KEY` at startup (least privilege; fail-soft).
4. **Unit `KEY_VAULT_URI`** -- Jack's az-root edit. **Add this LAST** (after 1-3 are deployed) so no narration is
   paid-for under a non-final skill_version and then invalidated.

Why key-last: the cache is keyed on `(wallet, category, skill_version)` and only SUCCESSFUL verdicts cache. Until the
key resolves, every verdict is the `llm_unavailable` null (nothing caches). Wire the key only after skill_version is
settled at "3", so the first real verdict a wallet gets is the final-form one -- paid once, not re-paid per bump.

---
## Manifest (deployable files -- hash is the gate; tests + this doc are NOT deployed)
Branch `9b8e79a`. sha256 (working tree == committed):

```
e25205954c479d6ba039e149cea8ba6bd2f5ec01971dce15b53f8e84f9c2ec36  trading_corp/prediction_markets/web/app.py
cec9c3d4620b72ef843159f66a44c52374769ab6aecbfe80505ef85e56fe9b9a  trading_corp/prediction_markets/analyze.py
7c84e1e30ce811d4c7cf4c982a663e686a8b2e83398deb167e1d3da3b64b8d9f  trading_corp/prediction_markets/web/templates/partials/pm_analyze_result.html
833644da11da8a9712c7c7fb54ee2100ff3d78a430f4bec793f2a1e269c1e941  trading_corp/scripts/pm_web.py
```

All four live under the pm_web unit's world (azureuser-writable via ssh). The shared venv
(`/home/azureuser/trading_corp/venv`) already has `azure-identity` + `azure-keyvault-secrets` (the engine's
load_secrets uses them), so the scoped fetch has its libs -- the ImportError branch is only a safety net.

**A hash-gated deploy runner (`pm_r2c_deploy_*`) will be prepared as a SEPARATE authorized step** (backup -> copy 4
files with a per-file hash assertion -> Gate-A transitive-import -> restart -> post-check), mirroring the R-d deploy.
This doc is the design + the unit line; the deploy itself is not run here.

---
## The unit edit (Jack, az-root) -- the exact one-liner
Add to the `[Service]` block of `/etc/systemd/system/prediction-markets-web.service`, mirroring the engine unit verbatim:

```
Environment="KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/"
```

(The vault URL is a resource ID, access gated by the VM Managed Identity's "Key Vault Secrets User" role -- not a
secret. It is the SAME URL the engine unit already carries.) Then, az-root:

```
sudo systemctl daemon-reload
sudo systemctl restart prediction-markets-web.service
```

**No engine restart. No bitunix bounce.** The repo copy `reports/prediction_markets/prediction-markets-web.service`
now shows this line + a comment for reference (the LIVE unit is Jack's to edit).

---
## Browser check (after the code deploy + restart, key wired)
Behind Authelia, open pm_web and click **Analyze** on a whale with real history (e.g. an MLB prospect):
- **Deterministic report** renders as before (roi/net/win%/two-sided/data-quality).
- **Loss-completeness block** appears when /activity grounding succeeds:
  - if it recovered losses: "Loss set re-grounded (win rate over-stated): ... recovers N held-to-worthless loss(es)
    ... honest XW/YL ... Z% omitted", plus the F-1 caveat ("Analyze is the promotion judge") and the completeness
    bound. `data-loss-grounded="1"` in the HTML.
  - if none missing: "Loss set re-grounded (no omission): ... the win rate above is not inflated ...".
- **Verdict**: once the key is wired, the narration renders (Haiku) and its FIRST sentence should lead with the loss
  omission when material. Before the key: the `llm_unavailable` null still shows, block still renders (that's the
  value today).
- **journal** (`journalctl -u prediction-markets-web -n 50`) should show ONE line at startup:
  `pm_web: ANTHROPIC_API_KEY loaded from Key Vault (...) -- Analyze narration ENABLED.`
  If the fetch failed for any reason, the line reads `... narration unavailable; pm_web boots normally.` and the app
  is up regardless (fail-soft) -- diagnosable, never a silent no-boot.

---
## What this deploy does NOT do
- No engine file touched (execution/live_driver/settlement/db already live from the earlier Option D + R-d deploy).
- No DB schema change (no migration; skill_version is an app constant, not a column).
- No order path, no arm/disarm, no cap change. pm_web imports no broker.
- Does not advance prod-live; the branch carries the ledger.

## Rollback
Restore the 4 files from the deploy runner's backup + restart pm_web; remove the `KEY_VAULT_URI` line from the unit +
daemon-reload + restart to fully un-wire the key. skill_version stays "3" (harmless -- it just means the old cache
key differs; nothing is cached under "3" until a successful narration).
