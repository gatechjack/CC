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
Branch `f4c16d6`. **7 files** = the 4 R2c files + the 2 `/live` settlement-display-fix files + `loss_grounding.py`
(a NEW Stage-5 dependency `app.py` imports, never deployed -- the first deploy attempt's Gate-A caught its absence
and auto-restored; added as file 7). sha256 (git archive bytes == working tree):

```
e25205954c479d6ba039e149cea8ba6bd2f5ec01971dce15b53f8e84f9c2ec36  trading_corp/prediction_markets/web/app.py
cec9c3d4620b72ef843159f66a44c52374769ab6aecbfe80505ef85e56fe9b9a  trading_corp/prediction_markets/analyze.py
7c84e1e30ce811d4c7cf4c982a663e686a8b2e83398deb167e1d3da3b64b8d9f  trading_corp/prediction_markets/web/templates/partials/pm_analyze_result.html
833644da11da8a9712c7c7fb54ee2100ff3d78a430f4bec793f2a1e269c1e941  trading_corp/scripts/pm_web.py
3ba6326832daa7015970ab46a5e400a5e91bf3c98bb1a6a63ad5eedc766c000a  trading_corp/prediction_markets/subdivision.py           (display fix)
99b06b4ad710ff699fc423da2bc43613b7166cd12f0464486a815cc41d3963f9  trading_corp/prediction_markets/web/templates/pm_live_subdivision.html  (display fix)
b03e3436a6309dd20ddd312f8d171907264dcaa1c6ccbf0e2e4a9bbbb0cec312  trading_corp/prediction_markets/loss_grounding.py        (NEW file -- Stage-5 dep, base=absent)
```

**DEPLOYED (files in place) 2026-08-31T18:56Z** -- Gate-A transitive imports green (app.title=pm_web, scoped-fetch=True,
live_orders=True, ground_losses=True), all 7 hash-gated MATCH, 644 OK. Running pm_web still OLD until the restart.
Backup `~/pm_r2c_deploy_backup_20260831T185617Z`. Manifest-gap lesson (repeat of the R7.e boot_reconcile miss):
Gate-A must -- and did -- catch a new transitive dependency the file-diff manifest omitted.

The `/live` display fix renders a **settlement-close** (close_source='settlement') as **SETTLED** (won/lost) with its
**realized P&L**, distinct from a whale **EXIT** -- the Cubs 16:33Z row was mislabelled EXIT with a $0.00 fill.
Validated read-only: `is_exit=1, close_source='settlement', broker_order_id=NULL, client_order_id=NULL, realized_pnl=-0.6084`,
and a whole-day engine-log sweep showed **no** Cubs order POST on 2026-08-31 -- it settled, it was not sold.

All four live under the pm_web unit's world (azureuser-writable via ssh). The shared venv
(`/home/azureuser/trading_corp/venv`) already has `azure-identity` + `azure-keyvault-secrets` (the engine's
load_secrets uses them), so the scoped fetch has its libs -- the ImportError branch is only a safety net.

### Base (current LIVE) hashes -- captured read-only 2026-08-31T18:23Z (the deploy aborts if the box drifts from these)
```
47c75d5686c30eff6d27a0ded0627b64ddb7cb821fa8627fa87faff1964a0b49  web/app.py (base)
c726aaef98ce512dcf1535378f8ca6045fafec642d77ad0038c4a8d79635abcb  analyze.py (base)
b90fef6ed46d40992dd208601c21f3080f4431f48c30fb084b9efd4c90641cef  pm_analyze_result.html (base)
cb49b841c7a790a182750b0c1f7de1e56b0055e209b0a3ea9b9a2bcba2a36090  scripts/pm_web.py (base)
babe388c56e7b69ed5ed6b74f03ca325f006924177375397c4d6dc64ac2f3765  subdivision.py (base)
08d9286f43227873bcb3dd53f8cc8ae1c543d3adf745c08027940719422ed9df  pm_live_subdivision.html (base)
```

### Runners (authored, validated; NOT run except the read-only baseline)
- `cc\pm_r2c_baseline_ro.ps1` -- READ-ONLY, already run 18:05Z (captured the base hashes above).
- `cc\pm_r2c_deploy.ps1` -- git-archives the 6 files -> scp -> backup + hash-assert copy + 644 + Gate-A transitive
  imports (`import web.app` + `import scripts.pm_web` + `import subdivision`). **NO restart.** Aborts+restores on any
  drift/hash/Gate-A failure, leaving the live tree pristine. This is the deploy = a HALT item (Jack authorizes execution).
- `cc\pm_r2c_postcheck_ro.ps1` -- READ-ONLY: health, on-disk manifest, the 3-state classification, and the Analyze
  render (see below). Run AFTER the restart.

### Staged sequence
1. Jack authorizes `pm_r2c_deploy.ps1` -> files land, hash-gated, Gate-A OK (running pm_web still OLD).
2. Jack restarts pm_web (`Desktop\restart_pmweb.ps1`, az-root).
3. `pm_r2c_postcheck_ro.ps1` -> **expect STATE 1** (narration unavailable, pm_web healthy -- key not yet on the unit).
4. Jack adds the `KEY_VAULT_URI` line to the unit (az-root) + daemon-reload + restart pm_web. **Key LAST.**
5. `pm_r2c_postcheck_ro.ps1` again -> **expect STATE 2** (narration ENABLED). (This run narrates + spends ~$0.002/whale.)

### ★ Classify the narration state via ACTIVE env, NOT `systemctl cat | grep`
The live unit currently carries `KEY_VAULT_URI` **only inside the "intentionally OMITTED" COMMENT** -- so a naive
`systemctl cat prediction-markets-web.service | grep KEY_VAULT_URI` returns a FALSE POSITIVE (it matches the comment).
The post-check classifies via `systemctl show -p Environment` (INLINE env only, no comments) + the running process's
`/proc/PID/environ` (ground truth) + the scoped-fetch journal line. Do not read the comment as configuration.

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
### The THREE states the post-check distinguishes (each produces a DIFFERENT journal line -- state 1 must not be misread as a failure)
The scoped fetch logs exactly ONE line at startup (`journalctl -u prediction-markets-web -n 400 | grep pm_web: | tail -1`):

- **STATE 1 -- code deployed, unit line NOT yet added** (expected between step 3 and 4 above):
  `pm_web: KEY_VAULT_URI unset -- no Key Vault fetch; Analyze narration stays unavailable (deterministic report + loss-completeness still render).`
  Narration unavailable, **pm_web healthy**. This is the CORRECT state before the unit edit -- not a failure.
- **STATE 2 -- unit line added, fetch succeeds**:
  `pm_web: ANTHROPIC_API_KEY loaded from Key Vault (...) -- Analyze narration ENABLED.`
- **STATE 3 -- unit line added, fetch FAILS** (unreachable / empty secret / azure libs missing):
  `pm_web: Key Vault fetch of ANTHROPIC-API-KEY failed (<ErrType>) -- narration unavailable; pm_web boots normally.`
  Fail-soft working: **pm_web still booted**, and this line is TEXTUALLY DISTINCT from state 1 ("failed" vs "unset"),
  so a broken vault fetch cannot be confused with missing config. If you see state 3, check the vault / MI RBAC.

The post-check reads this line and prints the verdict, cross-checked against `systemctl show -p Environment` and the
process `/proc` env (never `systemctl cat | grep`, which trips on the OMITTED comment).

### Analyze render (the R2c display ruling -- the caveat BESIDE the number, named whale)
The post-check auto-selects the richest `(wallet, category)` in `pm_closed_position` (preferring mlb/nba/nfl, where we
have live /activity) and POSTs `/farm/analyze/<wallet>/<category>` on loopback, then:
- names the whale used (wallet + user_name + category + n_resolved) -- the FIRST candidate whose /activity grounding
  produced the block;
- proves the loss-completeness block rendered (`data-loss-grounded="1"`, honest W/L, omission %, completeness bound);
- **asserts POSITION by byte offset**: `data-loss-grounded` appears BEFORE `pm-analyze-foot` and `pm-legend` -- i.e.
  the caveat sits WITH the stats, not at page bottom (the whole point of the R2c display ruling).
Run this step in **STATE 1** (pre-key): the block renders from the grounded report regardless of the verdict, and an
Analyze POST with a null verdict writes NOTHING (no cache, no cost) -- so the display is verified for FREE before the
key is ever wired.

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
