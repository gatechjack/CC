# MARC + TREY (3rd & 4th Kalshi accounts) — LIVE BUILD LEDGER, 2026-09-20

Bringing `kalshi_marc` AND `kalshi_trey` live (after kalshi_jack, kalshi_karen), traded by the PM
live driver. Built EXACTLY like Karen, each using its OWN Kalshi API keys. FOUR accounts total.
Both added in ONE pass to save an engine restart. Each numbered step is a SEPARATE authorization;
the engine restart bounces every division (Jack times it).

## Base / branch
- Base = `origin/prod-live` = **777e87a5** (git truth; box asserted == prod-live).
- Branch **pm-marc-account-2026-09-20** (worktree `C:/Users/AA Incorporado/cc-pm-marc-wt`), off 777e87a5.
- Commits: `3037dedf` (marc) -> `<trey>` (trey). Both pushed. prod-live NOT advanced (record only).

## N-ACCOUNT CLAIM — stated plainly (Jack asked)
I said Trey would be **another (small) code change, NOT pure data**. Adding Trey confirmed exactly
that: the same additive ~7 lines in the same two files. So the Karen-mirror shape = **one small,
fail-closed code edit per account** (explicit + greppable + never route-to-jack) — NOT zero-code.
True pure-data-per-account would require a registry/KV-scan refactor of the live jack/karen
credential path (declined via "exactly like Karen"). The claim held; it did not fail.

## THE CODE CHANGE (2 files, purely additive; jack/karen paths byte-unchanged)
1. `trading_corp/utils/secrets.py` — add `kalshi_marc_*` and `kalshi_trey_*` mirroring
   `kalshi_karen_*` in FIVE spots each: `_SECRET_KEY_NAMES` redact tuple; `Secrets` dataclass;
   `_populate_from_keyvault` expected_env_vars; `load_secrets` ctor; `register_redact_literal`.
2. `trading_corp/prediction_markets/shard_snapshot_task.py` — add to `_SECRET_REF_KEYPAIR`:
   `"kalshi_marc": (...)` and `"kalshi_trey": (...)` (+ comment lines).

Vault secrets (Jack pre-created, Enabled): `KALSHI-MARC-{API-KEY-ID,PRIVATE-KEY-PEM}` +
`KALSHI-TREY-{API-KEY-ID,PRIVATE-KEY-PEM}`. KV translation `KALSHI_MARC_API_KEY_ID` ->
`KALSHI-MARC-API-KEY-ID` (`.replace('_','-')`) matches exactly; same for trey.

The PM live path is ALREADY N-account-generic (audited): driver spawn (main.py ~1445-1525) + M3
shard-snapshot writer (main.py ~1538-1564) enumerate the DB roster / `pm_account WHERE active=1`
and resolve each account's keys via the fail-CLOSED whitelist `resolve_kalshi_keys`. boot_reconcile,
caps/`sub_config_from_row`, the refresh stagger (`rank/N*interval`), authz `visible_account_ids`,
and the pm_web accounts grid are all per-account-generic. **The whitelist + secrets fields are the
only code needed per account.**

## Tests
- `tests/prediction_markets/test_secret_ref_keypair.py`: marc resolves to HIS keypair not jack's;
  trey resolves to HIS not jack's AND not marc's (no cross-bleed); whitelist maps marc/trey to
  their own fields; existing accounts unchanged; **unmapped/typo'd ref FAILS CLOSED** (kalshi_trey
  now WIRED so it moved to the positive test); recognised-ref-with-absent-field fails closed.
- `tests/test_secrets_kalshi_marc.py`: load_secrets populates marc + trey fields; registers each
  PEM as a redact literal (security-safe pattern); **end-to-end** resolve against a REAL loaded
  Secrets for BOTH (proves the whitelist attr-name strings match real dataclass fields — closes the
  "typo -> silent skip" gap a stub would mask).

## Adversarial skeptics (2, independent) — BOTH converged
- **main.py:2680 landmine**: legacy `family=='kalshi'` divisions builder does
  `if kalshi_karen ... else -> jack keys`; an unknown secret_ref would SILENTLY route to jack.
  Now traps BOTH marc AND trey. DEAD today (0 `family:kalshi` divisions in config/divisions.yaml).
  **Jack ruled LEAVE it out (blast radius — main.py wholesale-overwrite cost PM 28h
  armed-not-trading; do not touch it for dead code).** FILED (memory
  `pm-divisions-builder-else-routes-to-jack`, names marc + trey); fix = fail-closed elif mirror /
  route via resolve_kalshi_keys, in a main.py-ONLY window.
- **Test gap** (skeptic B): whitelist-mapping test was tautological; nothing proved attr-name
  strings match real Secrets fields. FIXED — end-to-end resolve tests (marc + trey).

## BOX-SCRATCH (RO, isolated ~/marc_scratch_<ts>, live tree untouched) — GREEN
`pm_marc_boxscratch.ps1` (scp+tar; secrets.py non-ASCII so streaming would mangle):
- PY_COMPILE_OK; import ISOLATION proven (SECRETS_FROM under scratch, not the live tree)
- Chain-of-custody EXACT: box CR-stripped sha256 == local — secrets.py `8fc9a0dd…`,
  shard_snapshot_task.py `194530498d…`
- pytest **26 passed RC=0** (marc + trey + redaction + completeness) with `-p no:pytest_ethereum`

## GRAFT state (IMPORTANT — the marc-only graft ALREADY ran)
- Marc-only graft ran at 14:31Z: box `secrets.py`/`shard_snapshot_task.py` == marc-only target
  (`624c6048…`/`f1b816ad…`); backup `~/marc_graft_backup_20260920T143115Z`. **Engine NOT restarted**
  -> the running engine still executes 777e87a5 code; the on-disk marc change is inert until restart.
- COMBINED graft (`pm_marc_graft.ps1`, updated): drift-gate BASE = the CURRENT box state (marc-only
  `624c6048…`/`f1b816ad…`), TARGET = marc+trey (`8fc9a0dd…`/`194530498d…`). Clean gated supersede
  (NOT forced): box marc-only -> marc+trey in one gated cp. Final box 2 files = 777e87a5 + combined
  diff = branch tip. New backup kept.

## Reversibility per step
| Step | Reversible without a restart? |
|---|---|
| Code (build/test/commit/push) | N/A — nothing live touched |
| Combined graft (2 files) | Yes (restore from backup) — inert until restart |
| Engine restart | **NO** — bounces every division (~3.5 min); irreversible commit point |
| Boot-verify | Read-only |
| pm_account rows (marc, trey) | UPDATE-able (active=0), but `account_id` is the PERMANENT PK — no delete |
| Sub-divisions / sizing / enable / arm | Yes — per-cycle DB reads, no restart |

## ★ SEQUENCING NOTE (roster read AT BOOT — proven by ITF/UEL)
The driver + M3 writer read the roster / active pm_accounts ONLY AT BOOT. So for the driver to wire
marc + trey at a SINGLE restart, their `pm_account` rows + attached subs must exist BEFORE that
restart. Proposed one-restart flow (reorders vs the literal step list; confirm):
graft -> **standalone credential-proof (marc, trey)** -> create rows+subs DISARMED ->
**ONE restart** -> boot-verify sees FOUR wired -> arm. (Credential proof runs as an independent
service-env process using the grafted secrets + KV; it does NOT need the engine restarted.)

## Remaining steps (each a SEPARATE authorization; HALT before each)
- **Combined graft** (present; Jack runs — box write).
- **Credential proof (BEFORE any row/arm), marc AND trey SEPARATELY**: authenticate against EACH
  book, confirm balance/identity is his own, not jack's. One proof does NOT cover both.
- **pm_account rows** (DISARMED subs): marc -> account_id `kalshi_marc`, owner_identity `marc`,
  secret_ref `kalshi_marc`; trey -> `kalshi_trey`/`trey`/`kalshi_trey`. owner_identity MUST equal the
  Authelia username (pm_web scopes on it). account_id is the PERMANENT PK.
- **Engine restart** (Jack times it) -> wires FOUR.
- **Boot-verify** (weighted for the shared engine): EVERY division back (MACE/PMCC/PEAD/bitunix/
  coinbase/RH, 0 degraded); driver wiring **FOUR** account tasks (jack+karen unchanged, marc+trey
  added, neither replacing); 35 armed with unchanged persisted timestamps + 0 latched (marc/trey add
  0 armed yet — any change is a finding); 0 import errors. Confirm restart by **PID change +
  ActiveEnterTimestamp**, NEVER the az exit code.
- **Fund-check + caps + sizing per account**: shard the categories settle on + whether it holds
  money (a starved shard reads healthy — masked total); contracts x price vs `per_order_usd_cap`
  (a pre-submit reject writes NO order row); read RESOLVED config via `sub_config_from_row` before
  arming (a new sub may carry `sizing_mode='fixed'` from the DDL default).
- **Arm** marc/trey subs (per-cycle DB write, no restart).
- **FIRST FILL = the credential proof**: read each account's first order back from ITS VENUE (not the
  journal); confirm it landed on the right book with its own order id.

## STOP (global disarm), from /home/azureuser/trading_corp
```
PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global
```
Fire-first-report-second only on a confirmed wrong-account / wrong-side fill.
