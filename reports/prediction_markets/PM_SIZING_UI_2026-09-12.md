# PM UI — PER-SUB-DIVISION CONTRACT SIZING FROM THE UI — 2026-09-12

**STATUS: BUILT + TESTED + RENDERED + BOX-RO-VERIFIED + COMMITTED (local). NOT deployed / NOT pushed / NO restart.**
Change a sub-division's contracts-per-copy from its `/live/{account}/{category}` page (e.g. 5 -> 1) with NO restart —
the engine reads `pm_subdivision.contracts` per cycle. pm_web-only build; the ONE engine-shared touch is `db.py`
(migration 022), additive-only. Engine (`trading-corp`) untouched. Branch `pm-sizing-2026-09-12` off prod-live.

--------------------------------------------------------------------------------
## 1. TRUTH MODEL + BASELINE
- `origin/prod-live` tip = **`78d90e54`** (Deploy 10 docs; code `c339437b`). Recorded. Branch `pm-sizing-2026-09-12`
  in a fresh worktree `cc-pm-sizing-wt` off `78d90e54`.
- **box == prod-live 78d90e54: 63/63 tracked pm_web files, 0 mismatch, 0 missing** (CR-stripped; 7 inert Sept-1
  `.bak/.orig` extras, unchanged). Runner `cc/pm_sizing_boxcheck_ro.{ps1,sh}`. No reconcile finding.
- **Baseline** (transition §1 command, box: `venv/bin/python -m pytest tests/prediction_markets -q -p
  no:pytest_ethereum -p no:cacheprovider --continue-on-collection-errors` -> **22 FAILED**; the local `.venv-webtest`
  reproduces the SAME 22-set): `test_live_r3` x14 + `test_accounts_m2` x3 + `test_stage2_nav` x2 +
  `test_stage2_phase3` x2 + `test_ctx_pagination_fix` x1. NOT ours; the differential gate.

--------------------------------------------------------------------------------
## 2. DISCOVERY (D1–D4, cited)

| # | Question | Answer (file:line) |
|---|---|---|
| **D1** | Where the contract count lives + engine's PER-CYCLE read | **DB ROW `pm_subdivision.contracts`** (INTEGER NOT NULL DEFAULT 5, migration **014**, `db.py:776-778`). Read PER CYCLE: the steady loop re-reads the row every cycle at **`live_driver.py:1080-1081`** (`sub = execution.sub_config_from_row(SELECT * FROM pm_subdivision ...)`), passed to `execution.evaluate` (**`live_driver.py:742`**) which sizes at **`execution.py:542-544`** (`flat_contracts = int(sub.contracts)`) when `sizing_mode=='contracts'`. `poll_sec=7.0` (`live_driver.py:896`) -> a change lands on the next ~7s cycle, no restart. The boot build (`live_driver.py:970`) is only for reconcile. **Not a file -> no STOP; continue.** |
| **D2** | Existing write path? | **NONE.** No `pm_cli` command and no `farm_actions` function writes `contracts`/`sizing_mode` (grep clean). The CFB/UFC enables were ad-hoc one-row `UPDATE pm_subdivision` RUNNERS, not a reusable function. -> the UI needs the smallest NEW writer. |
| **D3** | Audit today + schema head | **No sizing-change audit exists.** Schema head = **21** (`db.SCHEMA_HEAD = max(MIGRATIONS)`, `db.py:958`; MIGRATION_021 is highest). -> **migration 022** (contiguous), a NEW **pm_web-owned** audit table `pm_subdivision_sizing_audit`; the engine never reads/writes it (it reads `pm_subdivision.contracts`, unchanged), pm_web writes the audit row ALONGSIDE the UPDATE in one transaction. |
| **D4** | What reads the sizing value | ONLY the sub-division detail page: **`subdivision.sizing_summary(sub)`** (`subdivision.py:151`) rendered in the drawer footer (`pm_trade_drawer.html:84`), fed by `app._load_live_subdivision` (`app.py:1165`) which loads `sub.contracts` (`subdivision.py:105-108`). The **tile page does NOT read `contracts`** (`subdivision.py:72,585` select only `sizing_mode`/`fixed_stake_usd`) -> no staleness there. So the single surface to keep consistent is the detail page (header control + the drawer sentence). |

**Module ownership (stated per BUILD-2):** `subdivision.py` IS engine-shared (imported by `execution.py`/`settlement.py`/
`driver_roster.py`); **`farm_actions.py` is NOT** imported by the running engine (only `pm_cli`/`web/app.py`/`search_run.py`).
To keep the engine-shared surface to the migration ALONE, the reader + writer + bounds live in a NEW pm-side module
**`prediction_markets/sizing.py`** (imported only by `web/app.py`), NOT in engine-shared `subdivision.py`.

--------------------------------------------------------------------------------
## 3. RULINGS APPLIED
- **R1 ACCESS** — owner-or-admin may LOWER; only admin may RAISE. `_sizing_gate` (`app.py`): `_owner_or_admin_gate`
  (reuses `authz.can_act_on_account`) then, if `new > current`, `authz.is_admin` else 403. Enforced on BOTH the GET
  confirm and the POST. Tested: owner+lower->303, owner+raise->403, admin+lower->303, admin+raise->303, no-identity->403,
  not-owner->403 (`test_authz_matrix_post` + `_get_confirm`).
- **R2 BOUNDS** — integer [1,50], server-side; out-of-range -> 400 naming the bound. `sizing.CONTRACTS_MIN/MAX`
  (constants in the sizing module, a UI ruling, NOT engine config) + `validate_contracts`. Tested at 0/1/50/51/2.5/non-numeric
  and on the route (`test_bounds`, `test_bounds_on_route`).
- **R3 CONFIRM** — server-rendered `pm_sizing_confirm.html`: "Sets `<Account · CAT>` to N contract(s) per copy (was M).
  Applies to NEW copies from the next engine cycle (~7s) — no restart. Open positions are NOT changed. At recent fill
  prices (~$avg/contract) this is about $X per copy." X = N x avg of the last 20 filled entry fills (`sizing.estimate_cost`);
  no fills -> "No fills yet to estimate from." (`test_estimate_no_fills`/`_from_fills`; render `sizing_confirm_lower.png`).
- **R4 AUDIT** — every change writes who/from/to/when to `pm_subdivision_sizing_audit`. The header shows the current
  value + "set by `<who>` · `<age>`" (same age treatment as the arm row, `agefmt`); the drawer footer lists the last 5
  (`sizing.recent_changes`). (`test_set_contracts_writes_and_audits`, `test_recent_changes_last_5`, `test_page_render_before_and_after`).
- **R5** — the POST is NEVER exercised on prod; tests use tmp DBs only. (This session ran the box RO value-check only.)

--------------------------------------------------------------------------------
## 4. WHAT CHANGED
- **`db.py`** (engine-shared, **ADDITIVE-ONLY: 22 insertions / 0 deletions**): `MIGRATION_022` = `CREATE TABLE
  pm_subdivision_sizing_audit (id, account_id, category, old_contracts, new_contracts, changed_by, changed_ts)` + an
  index + the `(22, MIGRATION_022)` list entry (`SCHEMA_HEAD` auto-bumps to 22). No existing migration/function touched;
  engine behaviour-neutral (never reads the table; loads the new db.py on its next restart).
- **`sizing.py`** (NEW, pm-side, standalone): `CONTRACTS_MIN/MAX`, `validate_contracts`, `read_sizing` (current + mode +
  last-change + editability), `last_change`/`recent_changes`, `recent_fill_price`/`estimate_cost`, and `set_contracts`
  (validate -> read old -> UPDATE `pm_subdivision.contracts` -> INSERT audit, one transaction; refuses a non-'contracts'
  mode and out-of-range; idempotent no-op when new==old).
- **`web/app.py`**: `GET /live/{a}/{c}/sizing?n=N` (confirm) + `POST /live/{a}/{c}/sizing/{n}` (apply, count in the PATH
  -> no `request.form()`/python-multipart dependency), mirroring the Detach handlers; wired `sizing`/`sizing_changes`/
  `can_size`/`viewer_is_admin` into the sub-division ctx.
- **Templates**: `partials/pm_sizing_control.html` (NEW, the header control — JS-off-safe GET form), `partials/
  pm_sizing_confirm.html` (NEW, R3), `pm_live_subdivision.html` (one include near arm/liveness), `pm_trade_drawer.html`
  (last-5 audit footer). **`pm_desk.css`** (`.sizectl`/`.sizform`/`.sizchg` etc.) -> `pm_shell.html` `?v= 92a1ef2e ->
  422c45ec`.
- **Tests**: `test_sizing.py` (NEW, 33 tests). `test_rung3_observability.py` head-tracking updated (SCHEMA_HEAD 21->22;
  the migration-021 test now asserts `ver == db.SCHEMA_HEAD`) — the standard "a new migration bumps the head" change.

**Migration: YES — 022** (`pm_subdivision_sizing_audit`, pm_web-owned). No engine file. The only engine-SHARED module
touched is `db.py`, additive-only.

--------------------------------------------------------------------------------
## 5. EVIDENCE
- **Suite differential**: full `tests/prediction_markets` = **22 FAILED (the exact baseline), 0 NEW** — 33 new
  `test_sizing.py` pass; 2 head-tracking tests updated for head 22. Only my tests changed the count.
- **Renders viewed** (`cc/renders/sizing_*`): `sizing_header_{1600,1280,420}.png` — the header control ("SIZING **1
  contract / copy** set by jack · 0s ago [1] [CHANGE]") after a 5->1 change; `sizing_confirm_lower.png` (owner, 5->1,
  "about $0.50 per copy") + `sizing_confirm_raise.png` (admin, 5->10). Harness `cc/pm_sizing_render.py`.
- **Box RO** (`cc/pm_sizing_boxvalue_ro.{ps1,sh}`): box schema head **21** (022 not deployed), audit table absent;
  **jack/mlb and karen/mlb: page contracts=5, mode=contracts, engine per-cycle read `('contracts', 5)`, MATCH=True** —
  the value the page shows equals `execution.sub_config_from_row(...).contracts` (the engine's per-cycle read).

--------------------------------------------------------------------------------
## 6. FILE DIFF vs prod-live 78d90e54 (CR-sha16 BEFORE -> AFTER)
`app.py` IS touched. `db.py` is engine-SHARED but **ADDITIVE-ONLY (22 ins / 0 del)**. `sizing.py` is a NEW pm-side
module (imported only by web/app.py). No other engine-shared module; no engine file.

| file | BEFORE | AFTER |
|---|---|---|
| `db.py` *(engine-shared, additive)* | `3b5ae50d68c16551` | `8dc457d2f2e0d3a7` |
| `sizing.py` *(new pm-side)* | `(new)` | `248a18009c580301` |
| `web/app.py` | `e39624887c528bea` | `53106001c2f13832` |
| `web/templates/pm_live_subdivision.html` | `98ffb7a2613d7794` | `7d686aeb1277d345` |
| `web/templates/partials/pm_sizing_control.html` | `(new)` | `e96a94fc374931a2` |
| `web/templates/partials/pm_sizing_confirm.html` | `(new)` | `d284b09fef76b9e2` |
| `web/templates/partials/pm_trade_drawer.html` | `36edbd5237de9396` | `57b3ab9c697052bf` |
| `web/static/pm_desk.css` | `92a1ef2e6d4e0ff6` | `422c45ec72d1a58c` |
| `web/templates/pm_shell.html` | `c134af369beeb5be` | `f729755af3f42eca` |

Git-only (do NOT ship): `tests/prediction_markets/test_sizing.py` (new), `test_rung3_observability.py` (head update),
this report.

--------------------------------------------------------------------------------
## 7. DEPLOY SHAPE (for Jack — NOT executed here)
pm_web-only in effect, PLUS `db.py` (engine-shared additive) + `sizing.py` (new pm-side). **Because there is a
migration**, the order is: (1) backup-is-a-gate (every file to be written, verified == box before any write); (2) graft
the code/template/static files + `sizing.py` + `db.py`; (3) **apply migration 022 via `init_db` with a HEAD DRIFT-CHECK
(assert box head == 21 immediately before; renumber to box-head+1 on collision)** — this creates the pm_web-owned audit
table; (4) ONE `systemctl restart prediction-markets-web` (via `az vm run-command`); (5) post-check (served CSS
`?v=422c45ec`, the header control renders, a tmp-DB POST is NOT run on prod), then advance prod-live (FF) + tag. The
engine is NEVER restarted; it picks up the new (behaviour-neutral) `db.py` on its own next restart. Backup is a gate.
