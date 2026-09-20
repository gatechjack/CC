# MARC (3rd Kalshi account) — LIVE BUILD LEDGER, 2026-09-20

Bringing `kalshi_marc` live as the 3rd Kalshi account (after kalshi_jack, kalshi_karen),
traded by the PM live driver. Built EXACTLY like Karen, using Marc's own Kalshi API keys.
This ledger is the deploy record + per-step manifest. Each numbered step is a SEPARATE
authorization; the engine restart bounces every division (Jack times it).

## Base / branch
- Base = `origin/prod-live` = **777e87a5** (git truth; box asserted == prod-live).
- Branch **pm-marc-account-2026-09-20** (worktree `C:/Users/AA Incorporado/cc-pm-marc-wt`), off 777e87a5.

## Scope decision (verified against 777e87a5, not inherited)
The PM live path is ALREADY N-account-generic: the driver spawn (main.py ~1445-1525) and the
M3 shard-snapshot writer (main.py ~1538-1564) enumerate the DB roster / `pm_account WHERE
active=1` and resolve each account's keys via the fail-CLOSED whitelist
`shard_snapshot_task.resolve_kalshi_keys`. boot_reconcile, caps/`sub_config_from_row`, the
refresh stagger (`rank/N*interval`), authz `visible_account_ids`, and the pm_web accounts grid
are all per-account-generic (audited). **The ONLY code change is the two Karen-mirror spots.**

Built EXACTLY like Karen (Jack's instruction), NOT an N-generic refactor. Consequence, stated
plainly: **Trey (coming) = ANOTHER small code change** — the same additive ~7 lines in the same
two files — NOT pure data. The Karen-mirror shape keeps each account explicit + fail-closed
(unknown secret_ref -> skip, never route-to-jack), which is why it is not zero-code. A pure-data
Trey would require a registry/scan refactor of the live jack/karen credential path (declined).

## THE CODE CHANGE (2 files, purely additive; jack/karen paths byte-unchanged)
1. `trading_corp/utils/secrets.py` — add `kalshi_marc_*` mirroring `kalshi_karen_*`:
   - `_SECRET_KEY_NAMES` redact tuple: `KALSHI_MARC_API_KEY_ID`, `KALSHI_MARC_PRIVATE_KEY_PEM`
   - `Secrets` dataclass: `kalshi_marc_api_key_id`, `kalshi_marc_private_key_pem`
   - `_populate_from_keyvault` expected_env_vars: the two `KALSHI_MARC_*` names
   - `load_secrets` ctor: `kalshi_marc_api_key_id=_env("KALSHI_MARC_API_KEY_ID")` (+ pem)
   - `register_redact_literal(secrets.kalshi_marc_private_key_pem)`
2. `trading_corp/prediction_markets/shard_snapshot_task.py` — add to `_SECRET_REF_KEYPAIR`:
   `"kalshi_marc": ("kalshi_marc_api_key_id", "kalshi_marc_private_key_pem")` (+ comment line).

Vault secrets (Jack pre-created, Enabled): `KALSHI-MARC-API-KEY-ID`, `KALSHI-MARC-PRIVATE-KEY-PEM`.
KV translation `KALSHI_MARC_API_KEY_ID` -> `KALSHI-MARC-API-KEY-ID` matches exactly (`.replace('_','-')`).

## Tests (new)
- `tests/prediction_markets/test_secret_ref_keypair.py` (5): marc resolves to HIS keypair not
  jack's; whitelist maps marc->marc fields; existing accounts unchanged; **unmapped ref (incl
  kalshi_trey, KALSHI_MARC, None) FAILS CLOSED**; recognised-ref-with-absent-field fails closed.
- `tests/test_secrets_kalshi_marc.py` (3): load_secrets populates marc fields; registers the
  marc PEM as a redact literal (security-safe pattern); **end-to-end** resolve_kalshi_keys against
  a REAL loaded Secrets (proves the whitelist's attr-name strings match real dataclass fields —
  closes the "typo -> silent skip" gap a stub would mask).

## Adversarial skeptics (2, independent) — BOTH converged
- **main.py:2680 landmine** (both): legacy `family=='kalshi'` divisions builder does
  `if kalshi_karen ... else -> jack keys`; an unknown secret_ref would SILENTLY route to jack.
  DEAD today (0 `family:kalshi` divisions in config/divisions.yaml). **Jack ruled: LEAVE it out
  of this deploy (blast radius — main.py is the shared-trio file whose wholesale overwrite cost
  PM 28h armed-not-trading; do not touch it for dead code).** FILED (memory
  `pm-divisions-builder-else-routes-to-jack`); fix = elif mirror / route via resolve_kalshi_keys
  in a main.py-ONLY window; compounds with Trey.
- **Test gap** (skeptic B): whitelist-mapping test was tautological + nothing proved attr-name
  strings match real Secrets fields. FIXED — added the end-to-end resolve test above.
- No misroute / boot / fail-closed / KV-name defects on Marc's live path.

## BOX-SCRATCH (RO, isolated ~/marc_scratch_<ts>, live tree untouched) — GREEN
`pm_marc_boxscratch.ps1` (scp+tar staging; secrets.py has non-ASCII so streaming would mangle):
- PY_COMPILE_OK
- Import ISOLATION proven: SECRETS_FROM under the scratch dir (not /home/azureuser/trading_corp)
- MARC_IN_WHITELIST True; MARC_MAPS_TO the marc fields; MARC_FIELD_ON_SECRETS True
- Chain-of-custody EXACT: box CR-stripped sha256 == local — secrets.py `624c6048…`,
  shard_snapshot_task.py `f1b816ad…`
- pytest **23 passed RC=0** (marc + redaction + completeness) with `-p no:pytest_ethereum`

## Reversibility per step (what a mistake costs)
| Step | Reversible without a restart? |
|---|---|
| 1. Code (build/test/commit/push) | N/A — nothing live touched |
| 2. Graft to box (2 files) | Yes (restore from backup) — but only takes effect at next restart |
| 3. Engine restart | **NO** — bounces every division (~3.5 min); irreversible commit point |
| 4. Boot-verify | Read-only |
| 5. `pm_account` row insert | Row is UPDATE-able (active=0) but `account_id` is the PERMANENT PK — no delete |
| 6. Sub-divisions / sizing / enable | Yes — per-cycle DB reads, no restart |
| 6. Arm | Yes — `live-disarm` per-cycle, no restart |

## Remaining steps (each a SEPARATE authorization; HALT before each)
2. **Graft** the 2 files to the box (scp+tar, drift-gate box==777e87a5 CR-stripped BEFORE apply,
   backup, py_compile, verify). `data/` is gitignored but NO new module lands in data/ (both edits
   are existing tracked files) -> no force-add needed.
3. **Engine restart** — Jack times it (bounces MACE/PMCC/PEAD/bitunix/coinbase/RH). Canonical
   `restart_tc.ps1` (az-root; agent-blocked -> Jack runs).
4. **Boot-verify** (weighted for the shared engine): EVERY division back (MACE/PMCC/PEAD/bitunix/
   coinbase/RH, 0 degraded); 35 armed with unchanged persisted timestamps, 0 latched; 0 import
   errors; driver wiring **THREE** account tasks (not two). Confirm restart by **PID change +
   ActiveEnterTimestamp**, NEVER the az exit code.
5. **`pm_account` row**: account_id `kalshi_marc`, venue `kalshi`, **owner_identity `marc`** (MUST
   equal his Authelia username — pm_web scopes on it), secret_ref `kalshi_marc`. account_id is the
   PERMANENT PK.
   - **CREDENTIAL PROOF (load-bearing):** before any order, authenticate against HIS book and
     confirm balance/identity is Marc's, not Jack's. This is what carried the weight for Karen.
6. **Sub-divisions + sizing + enable + arm**:
   - FUND-CHECK first: which shard his categories settle on and whether it holds money (a starved
     shard reads healthy because the balance call returns the masked total).
   - CONTRACTS x PRICE vs `per_order_usd_cap` (a pre-submit reject writes NO order row — silences a
     category with nothing to grep; silenced jack-mlb for a day).
   - Read RESOLVED config via `sub_config_from_row` before arming — a new sub may carry
     `sizing_mode='fixed'` from the DDL default rather than 'contracts'.
7. **FIRST FILL = the credential proof**: read Marc's first order back from HIS VENUE (not the
   journal); confirm it landed on HIS book with its own order id. A fill on the wrong account is
   the failure this whole sequence exists to prevent.

## STOP (global disarm), from /home/azureuser/trading_corp
```
PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global
```
Fire-first-report-second only on a confirmed wrong-account / wrong-side fill.
