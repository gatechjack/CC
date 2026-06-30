# RUNBOOK — BitUnix two-live-division architecture

Branch `bitunix-two-live-phase1-2026-06-29` @ `fb7f223` (off `main` `5f7606f`).
Backlog #27 (bring `bitunix_futures` live on its own funded account, alongside live SFP).

**PHASE 1 (this package) = code-only, NO cutover. SFP keeps trading on its
current key throughout. `bitunix_futures` stays paper/halted.** Phase 2 (the
flat-guarded cutover) is a SEPARATE, later action — its steps are documented at
the bottom but NOTHING in Phase 2 is applied here.

Agent built read-only; **operator deploys.** Board-gated.

---

## What Phase 1 changes (3 source files, code only — NO config, NO unit)

| file | change | BASE md5 (prod must equal) | TARGET md5 (after apply) |
|------|--------|----------------------------|--------------------------|
| `trading_corp/utils/secrets.py` | add `bitunix_sfp` account (dataclass + KV pull + redaction); `bitunix_futures` intact | `385e9ded35ee92b05b43e06752053190` | `6230e35138b9c11a01318b986ed52c7f` |
| `trading_corp/agents/divisions/bitunix_position_reconciler.py` | optional `division=` param → per-account row + audit isolation; `division=None` = byte-identical legacy | `3a23610c9e2bbd3d863163f657eeca36` | `68f969d6f66a1953a7b975e670436de9` |
| `trading_corp/main.py` | boot-guard count→per-secret_ref distinctness; reconciler binding ≤1 live=legacy, ≥2 live=per-division loop | `2ff188c73648c2f23d92f1168a5a803f` | `f4f0880d6062e6de04925b06e6c6366e` |

BASE md5 = the blob on `main` (the parity anchor). **`reconciler` BASE `3a23610c`
matches the last prod-verified value (memory [[bitunix-two-state-collapse]] Phase 2).**
`main.py` BASE differs from an older memory snapshot → **the drift-gate below is
mandatory, not advisory.** Patch: `phase1_code.patch` (558 lines, additive).

### SFP-safety proof (gate evidence)
- **SFP observer + strategy BYTE-UNCHANGED** — `trading_corp/agents/divisions/bitunix_sfp_observer.py`
  (LF-blob `8a916526d67fccef406f0dabd63e0b12`) and `trading_corp/agents/strategies/bitunix_sfp.py`
  (`91fd76726364331c8083aaaa68fce199`) are NOT in the diff (`git diff --name-only main` = the 3 files only).
- **Reconciler behavior unchanged on SFP's path**: with ≤1 live bitunix division (Phase-1 prod state =
  SFP only), `main.py` uses the legacy single binding with `division=None`; every reconciler facility then
  runs its pre-2026-06-29 code path (no row filter, actor `bitunix_position_reconciler`). Proven by
  `test_load_rows_sfp_only_filtered_equals_legacy` + `test_sfp_reconcile_scoped_equals_legacy_when_sfp_only`.
- **Futures reconciler can never see SFP rows**: `test_futures_reconcile_never_sees_sfp_rows`
  (both divisions open, futures broker flat → only the futures row is `missing_on_broker`; SFP row invisible;
  audit written under the futures-scoped actor only).
- **Full suite == 28F baseline (0 new regressions)**; 13 new tests pass
  (`tests/test_bitunix_two_live_phase1.py`).

---

## PHASE 1 DEPLOY (operator) — SFP undisturbed

**Pre-flight**
1. **SFP flat.** Confirm no open SFP live position: `paper_trade_record WHERE division='bitunix_sfp'
   AND result IS NULL` returns 0 rows (SFP tracks the open trade in the RECORD, not the position table —
   [[bitunix-sfp-mode-b]]). If a trade is open, wait for it to close (do NOT restart on an open position).
2. **KV resolvable.** `BITUNIX-SFP-API-KEY` / `BITUNIX-SFP-API-SECRET` exist in KV, IP-bound `168.62.60.79`
   (operator already created these — the original account's dedicated key). Phase 1 only LOADS them
   (unused until Phase 2), proving resolvability before cutover.

**Drift-gate (MANDATORY — branch may diverge from prod)**
3. scp the 3 prod files down; md5 each (LF). **Each must equal its BASE md5 above.**
   - ALL 3 match → safe to apply this package's target files verbatim (base is the known main blob).
   - ANY differ → **STOP.** Prod carries an un-`main`'d hotfix on that file; re-derive the hunk against
     prod's actual blob (apply `phase1_code.patch` with `patch` and resolve fuzz, or hand-apply the scoped
     hunks). Do NOT file-copy over a diverged prod file (would revert the hotfix).

**Apply**
4. Back up prod's 3 files → `*.bak-pre-two-live-2026-06-29`.
5. Apply (target files via LF blob + scp, or `patch < phase1_code.patch`); md5 each → must equal TARGET md5.
6. `python -m py_compile` the 3 files on prod (must exit 0).

**Restart (flat-guarded) + boot-smoke**
7. Restart the engine (operator; flat-guarded — abort if a position is open). NO unit change, NO config change.
8. **Boot-smoke — assert ALL:**
   - **Boot-guard PASSED** (no `REFUSING TO START`): only `bitunix_sfp` is `execution_mode:live`, so
     `_live_bitunix_divisions` = `[bitunix_sfp]`, 1 live → no shared-ref conflict.
   - **SFP reconciler running, legacy path**: log `bitunix restart-resume [bitunix_sfp] at startup: matched=…`
     + `bitunix position-state reconciler [bitunix_sfp] at startup: clean` (the `[slug]` label is new/cosmetic;
     the call uses `division=None` → unchanged behavior + actor `bitunix_position_reconciler`).
   - **Futures still inert**: `bitunix_futures HALTED — pa-redeem loop NOT started`; `execution_mode=paper`.
   - **KV**: `Key Vault: loaded N secrets` with no error on `BITUNIX-SFP-*` (N up by ≤2 vs prior boot).
   - SFP: flat, armed, fed; sanity-poll task `bitunix-position-state-sanity-poll-bitunix_sfp` present.
9. Watch one SFP reconciler tick (~60s) → clean. **Phase 1 done; SFP trades exactly as before.**

**Rollback**: restore `*.bak-pre-two-live-2026-06-29` + restart (flat-guarded).

---

## PHASE 2 CUTOVER (SEPARATE, LATER — flat-guarded; NOT in this package)

Do ONLY after Phase 1 is live + an SFP trade has round-tripped cleanly on the new code.
Each step is flat-guarded (SFP must be flat at every restart).

**2a — move SFP onto its own key (account-NEUTRAL: BITUNIX-SFP-* and BITUNIX-FUTURES-* both still point at
the ORIGINAL account, so SFP keeps its positions/account).**
- Confirm SFP flat.
- `config/divisions.yaml`, `bitunix_sfp` block — the staged one-line flip:
  `    secret_ref: bitunix_futures` → `    secret_ref: bitunix_sfp`
- Restart (flat-guarded). VERIFY: SFP authed on `BITUNIX-SFP-*` (account equity reads; reconciler clean);
  boot-guard still passes (1 live: `bitunix_sfp` on ref `bitunix_sfp`); `bitunix_futures` still paper/halted.

**2b — operator frees + repoints the futures key.**
- NOW that SFP is off `BITUNIX-FUTURES-*`, operator pastes the NEW FUNDED ACCOUNT's key into
  `BITUNIX-FUTURES-API-KEY` / `BITUNIX-FUTURES-API-SECRET` in KV. **IP-bind the new key to `168.62.60.79`**
  (the NAT-gw egress; NOT `.253` — dead, [[bitunix-two-state-collapse]]).

**2c — bring futures live on the new account.**
- `config/strategies.yaml`, `bitunix_futures` block — staged flips:
  `  mode: halted` → `  mode: trading`
  `  execution_mode: paper` → `  execution_mode: live`
  (Review sizing first — `tier_sizing` runs 25× leverage on PREMIUM/STANDARD; `effective_risk_per_trade_pct
  0.005`. Confirm or adjust for the freshly-funded account before this flip.)
- Unit `ExecStart`: add `bitunix_futures` to `--live-divisions` (currently `bitunix_sfp robinhood_pead`).
  Root-owned unit; operator has no sudo pw → external-root edit (Azure Run Command) + reload/restart
  ([[prod-sudo-constraint-no-password]]). NEVER hand operator `sudo cp/sed`.
- Restart (flat-guarded). **VERIFY:**
  - **Boot-guard PASSES**: `bitunix_sfp` ref `bitunix_sfp`, `bitunix_futures` ref `bitunix_futures` → DISTINCT
    refs → no conflict (even though, until 2b, both could resolve to the same account — the guard keys on the
    ref string by design).
  - **Two per-division reconcilers**: `bitunix restart-resume [bitunix_sfp] …` AND `[bitunix_futures] …`;
    both `clean`; sanity-poll tasks `…-bitunix_sfp` and `…-bitunix_futures`.
  - **Distinct accounts**: SFP equity = original account; futures equity = new funded account.
  - Futures: `mode trading`, `execution_mode=live`, pa-redeem loop started.
- Watch both reconcilers for ≥2 ticks clean. First futures live trade round-trips on its own account;
  SFP unaffected (own reconciler, own rows).

**Phase 2 rollback**: revert the config one-liners + (if added) the unit `--live-divisions` entry + restart.
The 2a flip is itself the SFP rollback boundary (revert `secret_ref` → `bitunix_futures` to put SFP back on
the original key).
