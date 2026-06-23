# PEAD ↔ Bitunix joint deploy — package manifest (2026-06-23)

Lands the `robinhood_pead` division **inert** (`standby: true`, `auto_execute: false`)
in the same flat-window/restart as Bitunix's `strategies.yaml` edit. Surgical superset:
**every byte of Bitunix/prod content is preserved**; PEAD content is added.

Branch: `robinhood-pead-2026-06-20` @ `cfc5538`. Prod tree: `/home/azureuser/trading_corp`.

## Payload — 15 files, 3 classes

### A. Supersets (8) — prod content preserved, PEAD added
prod ≠ branch on these because PEAD edited them. 3-way merged (base=origin/main,
ours=prod, theirs=branch). Bitunix/prod content proven preserved:

| file | proof |
|---|---|
| config/strategies.yaml | `bitunix_futures` block **byte-identical** to prod; `robinhood_pead` block byte-identical to branch; tasty intact |
| config/risk.yaml | `overrides/bitunix_futures` == prod (`per_account_max_drawdown_pct:0.99`); `overrides/robinhood_pead` == branch (near-off) |
| config/divisions.yaml | all **17 prod divisions byte-identical**; only `robinhood_pead` added (account_filter `680725082`, standby:true) |
| trading_corp/main.py | all bitunix/BitUnix wiring symbols present; all PEAD wiring present; PROD-DEL=0 (purely additive) |
| trading_corp/persistence/models.py | `fee` + `role` (bitunix) + `broker_order_id` + `account` (PEAD) all present; additive |
| trading_corp/agents/paper_trade_replay.py | full prod↔superset diff = only the 2 PEAD-skip clauses; issue1/metrics untouched |
| trading_corp/brokers/robinhood.py | prod==base (prod_only=0); Bug1/2/3 fixes applied; no PMCC/robinhood_joint content dropped |
| trading_corp/utils/market_data.py | prod==base (prod_only=0); get_next_earnings yfinance→EODHD (graceful fallback) |

### B. Straight-ship branch (2) — prod is BEHIND origin/main (EODHD chain)
prod ⊆ base (prod_only=0); branch == origin/main. Shipping branch loses **zero** prod
content, adds EODHD. Needed only when PEAD is activated (EarningsProvider reads
`EODHD_API_KEY` from env at runtime; absent ⇒ graceful fallback), but ship now so
go-live is a pure standby-lift.

- trading_corp/utils/secrets.py  (adds `eodhd_api_key` field + env load + redact)
- config/data_providers.yaml      (adds `eodhd` provider block)

### C. Net-new to prod (5) — PEAD only, no prod file touched
boot-critical (module-imported by `pead_strategy` → main.py boot path), all compile:

- trading_corp/agents/strategies/pead_strategy.py   (PEADStrategy: scan + manage)
- trading_corp/agents/divisions/robinhood_pead.py   (division shell)
- trading_corp/agents/strategies/pead_signal.py     (SRW-SUE signal)
- trading_corp/data/earnings_provider.py            (EarningsProvider)
- config/nasdaq_composite.txt                        (3,207-name universe)

> `pead_pressures.py`, `pead_observability.py`, `web/pead_view.py` are ALREADY on prod
> (earlier PEAD foundation) and unchanged branch↔prod — not shipped.

## Deliberately NOT shipped (left untouched on prod)
- **16 non-PEAD DIFFER files** (bitunix.py, bitunix_exceptions, polymarket_whale_*,
  data_exec.py, logger.py, market_data_provider.py, web/data.py, …): differ only because
  **prod diverged from origin/main** (un-pushed prod hotfixes). PEAD imports none of them.
  Shipping the branch version would REGRESS prod → excluded.
- **net-new non-PEAD**: `_ta_helpers.py`, `bitunix_confluence_gate.py`, `whale_screening.py`,
  `pead_backtest*.py` — not on the PEAD engine path; excluded.
- **2 prod-only files** (`_observer_test.py`, `bitunix_bracket.py`): kept.

## Scripts (run ON PROD, from the staged dir)
- `apply.sh [--go]` — drift guard (prod vs `prod_source.md5`, ABORT 9 on drift) → backup
  10 existing to `*.bak-pre-pead-2026-06-23` → install 15 → integrity check vs
  `payload.md5` (ABORT 8). Default DRY-RUN. **No service restart.**
- `preserve_check.sh [ROOT]` — EXTENDED GUARD: every pre-deploy backup line still present
  in the 6 additive files (strategies, risk, divisions, paper_trade_replay, main, models).
  ABORT 9 if any prod line dropped.
- `bootsmoke.sh [ROOT]` — COMBINED boot-smoke: import PEAD + assert FillEvent fields +
  Bitunix wiring imports + full `trading_corp.main` import. Exit 7 on failure. No orders.
- `rollback.sh [ROOT]` — restore `*.bak` + remove net-new.

## Verification done at build (2026-06-23)
- 8 supersets: block-identity / prod_only=0 / additive-diff proofs (table above).
- 2 straight-ship: prod_only=0 confirmed against base.
- 4 net-new .py: `py_compile` OK. 3 YAML + 4 .py supersets parse/compile OK.
- **DRY-RUN drift guard run against LIVE prod: all 10 source md5 match.** Package staged
  at prod `/tmp/pead_deploy`. Zero writes.

## prod source md5 (drift baseline) → see `prod_source.md5`
## installed target md5 → see `payload.md5`

## Joint window sequence (operator-coordinated)
1. Bitunix `halt.sh`
2. Bitunix `bitunix_flat_confirm`
3. **PEAD `apply.sh --go`** → backup paths emitted → operator/Bitunix EXTENDED guard
   (`preserve_check.sh`) → ABORT(9) stops here
4. **PEAD `bootsmoke.sh`** (combined: PEAD + Bitunix wiring + full main import)
5. service restart → Bitunix `bitunix_bootsmoke.sh` (asserts main.py bitunix wiring)
6. Bitunix `unhalt.sh`

Rollback at any step: `rollback.sh` + restart. PEAD ships inert (standby:true) — even a
clean boot trades nothing until standby is lifted (separate gate).
