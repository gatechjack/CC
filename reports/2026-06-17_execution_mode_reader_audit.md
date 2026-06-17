# execution_mode reader audit — pre-E2.5-activation — 2026-06-17

**Purpose:** before activating the E2.5 `execution_mode` write-side (which starts tagging live bitunix
orders `'live'` on the `proposed_order`/`paper_trade_record` **columns**), find any reader that filters
`execution_mode='paper'` on those columns or assumes all-rows-are-paper — such a reader would suddenly
EXCLUDE / mis-handle the newly-`'live'` rows. **Read-only, analysis only, no code changes (82fda13).**

## Current prod DB state (read-only)
- `proposed_order`: **31,927 rows, all `execution_mode='paper'`** (column DEFAULT; 0 `'live'`).
- `paper_trade_record`: **175 rows, all `'paper'`** (0 `'live'`).
- ⇒ No writer currently sets the column; live bitunix orders are mis-tagged `'paper'` by default. Activation fixes this.

## Method
Targeted content grep for `execution_mode` across `trading_corp/` (live source; the stale `deploy/staged`
copy excluded), plus explicit-path reads of every hit and the named readers (`paper_trade_replay.py`,
`web/data.py`, `web/app.py`, `web/templates/base.html`). 13 files reference the token.

## Three distinct `execution_mode` mechanisms (the crux)
1. **Engine CONFIG mode** — `_execution_mode` / `self.execution_mode` (paper|live from YAML). Not a DB read.
2. **Extra/payload TAG** — `extra_json.execution_mode` / `payload_json.$.execution_mode` = `'live'`, set by the
   observer's Path-C live path (observer.py:3006/3183) and live audit payloads. **Already populated for live
   orders today, independent of E2.5.**
3. **E2.5 COLUMN** — `proposed_order.execution_mode` / `paper_trade_record.execution_mode`. **This is what
   activation turns on.** A reader only shifts if it reads/filters THIS column.

## Reader inventory & classification

### SAFE — engine config readers (agnostic display/gating; not DB rows)
| Reader | file:line | Why SAFE |
|---|---|---|
| live-behavior gates | `main.py:1689/1731/1769` `_execution_mode=="live"` | reads YAML config mode, not DB |
| observer live gate | `bitunix_futures_observer.py:2764` `self.execution_mode=="live"` | config mode |
| dashboard status pill | `web/app.py:289-306` `getattr(obs,"execution_mode",…)` | reads observer config mode; handles paper/live/unwired/unknown |
| status pill color | `web/templates/base.html:176-182` `_s1.execution_mode=='live'/'paper'` | display color-code; handles both values |

### SAFE re E2.5 — extra/payload TAG readers (read the Path-C tag, already live-aware; NOT the column)
| Reader | file:line | Behavior | Why SAFE re E2.5 |
|---|---|---|---|
| home-rail `stage1_only` filter | `web/data.py:1379-1380` `json_extract(payload_json,'$.execution_mode') IS NULL OR ='paper'` | filters `audit_event` to bitunix paper activity | **This is a `'paper'` filter — but on the audit_event PAYLOAD tag, not the E2.5 column.** Live payloads already carry `'live'` (set independently); E2.5 column activation does not change audit_event payloads. Default `stage1_only=False` (no filter). |
| replay live/paper fork | `paper_trade_replay.py:986` `extra.get("execution_mode")=="live"` | per-row fork: live→broker close, else paper write | row selection is `FROM paper_trade_record WHERE result IS NULL` (`:1277`, **no execution_mode filter**); fork reads the extra tag (already live), not the column |
| reconciler live-pos select | `bitunix_position_reconciler.py:452` `extra.get("execution_mode")!="live"` | skip non-live positions | reads extra tag, not column |

### Not readers (write-side / schema / validation)
- WRITE: `data_exec.py:111` (sets `order.execution_mode` by `broker.paper`), `models.py:90/326/372` (`to_db_row` carries it), `agents/logger.py` (INSERT binds `:execution_mode`), `observer.py:3006/3183` (sets extra tag).
- SCHEMA/CONST: `db.py` (`CREATE TABLE … execution_mode … DEFAULT 'paper'`), `models.py:27` (`_VALID_EXECUTION_MODES`).
- `polymarket_copy_trader.py:797` `pos["execution_mode"]="live"` — polymarket write, out of audit scope (not a reader).

## VERDICT — ✅ SAFE for readers; no fix needed before activation
**No reader filters on the E2.5 column** (`proposed_order.execution_mode` / `paper_trade_record.execution_mode`).
The column-filter grep (`WHERE execution_mode`, `execution_mode = 'paper'`) returned **zero column filters**.
Every live/paper distinction in the codebase uses either the engine config mode or the pre-existing
extra_json/payload TAG (already `'live'` for live orders, independent of E2.5). The newly-`'live'` **column**
value is currently read/filtered by **nothing**. Activating E2.5 only makes the column accurate; no paper-PnL,
replay selection, reconciler, dashboard count, or reporting path shifts. **No `'paper'`-column-filter reader exists → nothing to fix first.**

Nuance (not a blocker): the home-rail `stage1_only` filter + the replay/reconciler forks depend on the
**extra/payload tag** being stamped `'live'` (observer Path-C). That stamping is preserved (observer ships in
the deploy). So the live-distinction the readers rely on stays intact.

## COUPLED WRITE-SIDE — confirmed, and a DEPLOY-SET CORRECTION
The E2.5 write path is a coupled trio:
`data_exec.place()` sets `order.execution_mode` (`data_exec.py:111`, by `broker.paper`) →
`models.to_db_row()` resolves+carries it (`_resolve_execution_mode`, `models.py:90/326`) →
`agents/logger.py.log_proposed_order` INSERT binds `:execution_mode`.

- **prod `models.py` = `f66722e` (2026-06-01, pre-E2.5) — `to_db_row()` does NOT provide `execution_mode` (0 refs).**
- ⇒ Shipping the new `agents/logger.py` (INSERT binds `:execution_mode`) **without** `models.py` = the bind has
  no value in the row dict → `sqlite3` raises "no value for binding parameter :execution_mode" on **every**
  `proposed_order` write (ALL divisions, not just bitunix) → broken writes.
- **`models.py` MUST ship with `data_exec.py` + `agents/logger.py`.** Its drift = prod `96cf31c4` (f66722e)
  → target `a781b495` (b077b66==a64a42f); delta = exactly E2.5 (`f692fa2`).
- **DEPLOY-SET CORRECTION: the package is 7 files, not 6** — add `trading_corp/persistence/models.py`
  (E2.5 version; not a bitunix #3/#5-C/bracket change, but a hard dependency of the #3 logger INSERT).
- Correct logger module = **`trading_corp/agents/logger.py`** (sole definer of `log_proposed_order`).
  `trading_corp/path_logger/logger.py` exists but is unrelated — **do NOT** deploy that one.

## Updated deploy set (7 files)
E2.5 write-side trio (all pre-E2.5 on prod → drift-gated to prod-current): `data_exec.py`, `agents/logger.py`,
`persistence/models.py`. Bitunix at-base (prod==base): `bitunix_futures_observer.py`,
`bitunix_position_reconciler.py`, `brokers/bitunix.py`. New: `agents/divisions/bitunix_bracket.py`.
**Never** `main.py`/`db.py`/`strategies.yaml`.
