# Deploy Runbook — `bitunix_sfp` division LIVE; `bitunix_futures` → PAPER

**Branch:** `bitunix-sfp-division-2026-06-25` · **Status:** STAGED, tests green, NOT deployed.
**Nothing here runs itself — GT_Jack executes each step.** Agent may drive READ-ONLY SSH (md5/boot-smoke);
all writes (file copy, config edit, systemd edit, restart) are operator-run.

## What ships
A new engine-side `bitunix_sfp` division: ports the validated 15m TFlab SFP+BOS detector (LONG-only,
REAL+CONSIDERABLE, fixed 2R), generates its own signals on a sequential 15m loop, and routes BOS-confirmed
longs through the MANDATORY risk gate + the EXISTING `/tpsl/` bracket + B1 stop + per-account reconciler.
`bitunix_futures` (confluence scorer) moves to PAPER in the same cutover. One live division per Bitunix
account (ONE_WAY netting) — enforced by a boot-guard.

**Cred model = Option B (key-separation by reuse):** `bitunix_sfp.secret_ref: bitunix_futures` → SFP reuses
the EXISTING account's keys. **No new env var / KV secret tonight.** (`secrets.py` unchanged.)

## Change set (this branch vs origin/main)
NEW (clean adds — not on prod, no drift):
- `trading_corp/agents/strategies/bitunix_sfp.py` — streaming detector (parity-proven vs p6 oracle md5 `6e411762`).
- `trading_corp/agents/divisions/bitunix_sfp_observer.py` — 15m loop, risk gate, slim placement, concurrent-position guard.
- `tests/test_bitunix_sfp_{detector,observer,wiring,reconciler_coexist}.py` (38 tests).

HUNKED (targeted, splice onto PROD blobs):
- `trading_corp/main.py` — caches, archiver spread, boot-guard, observer+loop wiring, per-account reconciler binding, per-division cred resolution. **NOT in the md5 manifest.**
- `config/strategies.yaml` — new `bitunix_sfp` block (+ the live→paper futures flip is applied at LIVE step). **IS the only manifest file changed.**
- `config/divisions.yaml` — `bitunix_sfp` entry (`secret_ref: bitunix_futures`). Not in manifest.
- `trading_corp/utils/divisions.py` — `secret_ref` field + loader. Not in manifest.
- `trading_corp/brokers/bitunix_symbols.py` — SOL/ETH/XRP wire mappings (record-only/forward). Not in manifest.

UNCHANGED (reused as-is — DO NOT touch): `bitunix_position_reconciler.py` (prod `8c3adcd1`),
`brokers/bitunix.py` (`4b00dea2`), `bitunix_futures_observer.py` (`2647fccc`), `paper_trade_replay.py`,
`risk.py`/`risk.yaml`, `secrets.py` (`385e9ded`). The symbol-agnostic reconciler is reused; the per-account
binding is main.py-only.

## Phase 0 — prod drift gate (READ-ONLY, before staging)
Pinned PROD blobs the hunks were authored against (verify they STILL match before splicing; if any moved,
re-base that hunk):
```
main.py            ec7bd6962bba02d1ba5b601af131f4e2
config/strategies.yaml  36f5b32309e4342a4521a69a8cb53a42
config/divisions.yaml   090174da86bddc9d2a4fdcc74b631d2c
utils/divisions.py 2ef1e3e8aa5f9a1522cef3799613bbd6
secrets.py         385e9ded35ee92b05b43e06752053190   (UNCHANGED — Option B)
RECONCILER (must stay) 8c3adcd173c3a9f65e596e64db7ef6e8
brokers/bitunix.py (must stay) 4b00dea2a913f20af68ca2754b5cc6b0
futures observer (must stay)   2647fccc630c8acacbe0d5a32f05b1c8
```
Verify on prod (read-only): `cd ~/trading_corp && md5sum trading_corp/main.py config/strategies.yaml config/divisions.yaml trading_corp/utils/divisions.py trading_corp/agents/divisions/bitunix_position_reconciler.py trading_corp/brokers/bitunix.py`.
- main.py / strategies.yaml / divisions.yaml / divisions.py == pins → hunks splice cleanly.
- reconciler / bitunix.py / futures-observer == pins → confirms we are NOT changing them (manifest sweep must show ONLY strategies.yaml changed).
- Run `scripts/bitunix_prod_surface_md5diff.py` and confirm the only manifest file this branch changes is `strategies.yaml`.

## Pre-deploy gates (local, operator or agent)
1. **Full suite** under the cap: `.\scripts\run_capped.ps1 <py> -m pytest tests/ -q` — baseline 2260 passed / 28 known-fail / 3 collection-errors; expect **+38 SFP tests, ZERO new failures**, PARITY + k=1 passing.
2. `python -m py_compile trading_corp/main.py` exit 0.
3. md5 sweep clean except `strategies.yaml`.

## Stage the apply package (targeted-hunk onto prod blobs)
Because prod is AHEAD of origin/main, splice by ANCHOR (every hunk anchor is a stable Bitunix wiring line
present on prod), with a per-file md5 Gate-A (prod-pre == pin) / Gate-B (post == new target), backup
`*.bak-pre-sfp-2026-06-25`, atomic `mv`, `py_compile`. New files copy in clean. Recompute the new target
md5s after staging and record them here. (Mechanics mirror the D1/ref-vs-fill apply scripts.)

## Deploy — TWO restarts (paper dry-run, then live flip)

### Step 1 — PAPER DRY-RUN (first restart)
- Copy the 2 new modules + apply the main.py/divisions.yaml/divisions.py/bitunix_symbols.py hunks + add the
  `bitunix_sfp` strategies.yaml block with `execution_mode: paper` (the repo default — no flip yet).
- Leave `bitunix_futures` and the ExecStart `--live-divisions` UNCHANGED for this restart.
- `sudo systemctl restart trading-corp` (NOPASSWD).
- **Boot smoke (read-only):** expect log `bitunix_sfp observer wired: symbols=['BTC/USDT.P'] execution_mode=paper auto_execute=True` and `bitunix_sfp 15m loop spawned (1 symbol cache(s) primed)`; the 15m cache `last_refresh_count >= 101`; no traceback on the bitunix path; engine flat; futures still live (unchanged). Let it run long enough to confirm the loop ticks and (if a signal fires) writes a `bitunix_sfp` paper `paper_trade_record` row with `sfp_mode`/levels in `extra_json` (`json_extract(extra_json,'$.sfp_mode')`).

### Step 2 — ARM gate (confirm before live)
- Bitunix account FLAT (venue snapshot 0 positions) AND reconciler clean.
- No open `bitunix_futures` live rows (`result IS NULL AND extra_json.execution_mode='live'`). Close/settle any first — the cutover must orphan nothing.

### Step 3 — LIVE FLIP (second restart)
Three edits, then one restart:
1. **strategies.yaml** (prod): `bitunix_sfp.execution_mode: paper → live` AND `bitunix_futures.execution_mode: live → paper`.
2. **ExecStart** (ROOT — operator has no sudo password): edit `/etc/systemd/system/trading-corp.service`
   `--live-divisions`: **remove `bitunix_futures`, add `bitunix_sfp`** → `--live --brokers bitunix --live-divisions bitunix_sfp robinhood_pead`. Apply via `az vm run-command invoke ... --scripts @unitfile` (LF), back up the unit. (No cred/env change — Option B.)
3. `sudo systemctl daemon-reload && sudo systemctl restart trading-corp` (NOPASSWD).

**Boot smoke (read-only, LIVE):**
- `Registered ... bitunix_sfp ... (paper=False)` AND `... bitunix_futures ... (paper=True)` — the half-flip marker confirming the swap.
- `bitunix_sfp observer wired: ... execution_mode=live auto_execute=True` + `bitunix_sfp 15m loop spawned`.
- Boot-guard PASSED (engine started → not ≥2 live bitunix).
- Per-account reconciler bound to the SFP broker: startup `bitunix position-state reconciler ... clean` (it now follows the live division).
- Engine flat; `TC_LIVE_AUTHORIZED=LIVE` unchanged; no bitunix-path traceback.

## First-live-trade A/B (validation — pending a BOS-confirm long)
On the first live SFP entry verify: B1 server-side stop attached at `swept_low − 0.1%`; `extra_json` carries
`sfp_mode` + swept level + `bos_bar_ts` + 2R target; the per-account reconciler tracks it (match_count 1);
the concurrent-position guard blocks any 2nd same-(symbol,side); on close, auto-book books from the real fill
(D1/D3/ref-vs-fill compose), `result` + `sfp_mode` queryable. NOTE (§RISK-2): live fills seconds after the
modeled next-bar open — the first fills ARE the slippage validation (go-live accepted).

## Rollback
- Pre-live: revert `bitunix_sfp.execution_mode` to `paper` (hot, no restart needed for the kill switch via
  `auto_execute: false`; execution_mode needs a restart).
- Full: restore `*.bak-pre-sfp-2026-06-25` for the hunked files, restore the unit backup, `daemon-reload` +
  restart. New files are inert when `bitunix_sfp.enabled: false`.
- The boot-guard fails CLOSED (refuses start on ≥2 live bitunix) — if it ever fires, set all but one bitunix
  division to `execution_mode: paper` and restart.

## Operator decisions still open (set at deploy)
- `risk_pct_real` / `risk_pct_considerable` (proposed 0.005 each) · `leverage` (proposed 5.0) · `standby`
  (false to arm) · the paper→live `execution_mode` flip timing. All are strategies.yaml/divisions.yaml values.

## Post-deploy
Append a `runbooks/deploy_log.md` entry (`Features shipped:` bitunix_sfp live + futures→paper; `Notable code
changes:` the new modules + main.py hunks + the `--live-divisions` swap) and record the staged target md5s.
