# Stage 3 — RUNG 2 (MATCHER) DEPLOYED LIVE 2026-08-29 + moneyline equivalence PROVEN

**Rung 2 of the 4-rung Stage-3 deploy ladder. The SHARED matcher `trading_corp/data/mlb_poly_kalshi_match.py`
(totals+spreads added by R2; moneyline byte-identical). Deployed 2026-08-29T00:22:12Z via the `az` ROOT path,
verified. Inert on deploy. No `prod-live` advance; ledger NOT pushed this pass (Jack).**

## 1. What deployed
- **One file:** `trading_corp/data/mlb_poly_kalshi_match.py`, branch `5279985`/`f3b7a1d` content, sha256 **`3e52394`**
  (prod-live base was **`64795b97`**). R2 ADDS totals (`KXMLBTOTAL`) + spreads (`KXMLBSPREAD`) + the `match_bet`
  dispatcher, `liquidity_ok`, and new default-`None` dataclass fields; the moneyline entrypoint `match_poly_to_kalshi`
  and its helpers are **byte-identical** (proven, §4). It is SHARED with the LIVE legacy `poly_kalshi_mlb` division.

## 2. ★ Deploy mechanism — `az` ROOT (not ssh) — and why
- **First attempt (ssh `cp` as azureuser) FAILED, fail-closed:** `permission denied`. The box matcher was UNTOUCHED
  (Gate-A had confirmed `64795b97` first; no `cp` occurred). Diagnosed (read-only probe): `trading_corp/data/` dir is
  owned by **`197609:197121`** (Windows numeric UID/GID, a Windows-side-`scp` artefact) at `755`, and the matcher file
  is **`root:root 644`** — azureuser (uid 1000) can't overwrite it, and `sudo` is forbidden + no-NOPASSWD anyway.
  **This is now STANDING BOX QUIRK #5** (transition doc §G).
- **Jack RULED (A): deploy via `az vm run-command RunShellScript` as ROOT, keeping the file `root:root 644`.** Option B
  (chown-to-azureuser) was REJECTED: it would permanently change a shared legacy file's ownership model for PM's
  convenience, at the live legacy division's expense. (A) matches the PEAD-Part-3 root-deploy precedent and leaves the
  box's ownership model as found. Accepted cost: future matcher updates also need the root path (rare).
- **Fail-closed root script (`cc\pm_r2_az_deploy_root.sh`, run by Jack):** Gate-A (`target==64795b97`) → verify staged
  (`==3e52394`) → backup → `cp` staged→target → `chown root:root` → `chmod 644` → re-hash gate (`==3e52394`, `644`,
  `root:root`). Output `R2AZ: PASS`. Backup at **`~/pm_stage3_r2_azbak_20260829T002212Z/`** (+ the earlier ssh-run
  backup `~/pm_stage3_r2_bak_20260829T000625Z/`, both `64795b97`).

## 3. Activation path (stated explicitly — the box file and the running engine DISAGREE)
The matcher is a **pure module**. The running engine (`trading-corp.service` PID 53046, up since 21:30:05Z) **already
has `mlb_poly_kalshi_match` loaded in memory** — the legacy `poly_kalshi_mlb` division (LIVE on Karen's account)
imported it at that start. **Writing the new file to disk does NOT reload it** — Python keeps the in-memory module. So
after this deploy the **on-disk file (`3e52394`) and the engine's in-memory module (`64795b97`) disagree, and stay so
until the engine restarts — which is R7's authorization, NOT this rung's.** **Harmless:** the two are byte-identical on
the moneyline path (§4), and moneyline is ALL the legacy division copies; totals/spreads are additive with no caller
until R4/R7. Legacy resolves moneyline identically whether it runs the in-memory old copy or (post-R7) the new one.

## 4. Verification — moneyline equivalence PROVEN on the DEPLOYED file (not the build record)
Read-only runner `cc\pm_r2_verify_ro.*` loaded BOTH the old code-backup and the new deployed file as modules
(`importlib` + `SourceFileLoader` + `sys.modules` registration) and:
- **[src] `match_poly_to_kalshi` + `_side_ticker`/`_game_key`/`_norm`/`resolve_side`/`parse_kalshi_mlb_ticker`
  byte-identical old→new = YES.**
- **13-vector battery, 0 diffs:** old and new return IDENTICAL `MatchResult`s. Coverage exercised the real paths —
  **6 matched, 1 doubleheader_ambiguous, 1 out_of_window, 1 no_kalshi_contract, 1 fail, 2 skip_non_ml, 1 skip_non_game.**
- Two VERIFY-HARNESS bugs found + fixed (the DEPLOY was always correct): (1) the code-backup's non-`.py` extension
  (`…py.box_pre_r2`) defeated `spec_from_file_location`'s suffix-based loader inference → forced `SourceFileLoader`;
  (2) Py3.12 `@dataclass` resolves `cls.__module__` via `sys.modules` → registered the module before `exec_module`.

## 5. Inert post-checks — nothing else moved
`/farm` HTTP 200, **bytes `4339` == pre-deploy baseline** (byte-identical; pm_web never imports the matcher).
**engine `53046` NRestarts 0 / pm_web `42343` NRestarts 0 — NO restart.** schema **11**, quick_check ok, the 4
money-layer tables all **0**. `pm_*` counts stable (drift with cadence, not the deploy).

## 6. §H checkpoint (three data bases stay separate)
R2 is a pure Poly→Kalshi market-mapping utility. It touches NONE of the three data bases — Prospect
(`pm_closed_position`/`pm_category_stats`), Watchlist (`pm_paper_trade`/`pm_paper_category_stats`), Live (P3). All
untouched; the three bases stay separate. No DB write at all.

## 7. State after rung 2
- Matcher on box = **`3e52394`** (`root:root 644`); legacy 35/35 property re-established as byte-identical moneyline on
  the deployed file. `origin/prod-live` still `166b5ab` (untouched; `95e78c4` reachable).
- Branch `prediction-markets-stage3-2026-08-28`: this ledger + the §G quirk-5 edit committed **on the branch, NOT
  pushed this pass** (Jack). (Origin remains at `5279985` = the rung-1 docs.)
- **Rungs 3 (WEB R3+R6, pm_web restart) and 4 (EXECUTION, PM-only inert) remain UNAUTHORIZED. R5.5 / R7 / R8
  unauthorized. HALT.** Rungs 3/4 live under `prediction_markets/` (azureuser-owned) → ssh-deployable like rung 1.
- Rollback material: `~/pm_stage3_r2_azbak_20260829T002212Z/mlb_poly_kalshi_match.py.box_pre_r2` (`64795b97`); a
  rollback would also need the `az` root path (root-owned target).
