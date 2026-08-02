# Gate-A + deploy checklist — PEAD derived sizing + $50 floor + dial + dashboard

Staged 2026-08-02 on branch `claude-2026-08-02b` off `prod-live` (`dafe60b`).
**Do NOT deploy from this doc blindly — run the drift gate first.** Kalshi crypto
is co-developing; the three shared files must be re-checked at deploy time.

Runner (read-only drift check, does NOT deploy): `powershell -ep bypass -f .\gate_a_pead_sizing.ps1`

## File manifest — LF-md5 (baseline = prod-live `dafe60b`, target = staged worktree)

LF-md5 = md5 of the file with CRLF→LF normalized (`tr -d '\r' | md5sum`), first 12 hex.

| Scope | File | Baseline (pre-deploy) | Target (post-deploy) |
|---|---|---|---|
| pead | config/strategies.yaml | ccde3bf75db3 | 274b7e348eb2 |
| pead **NEW** | trading_corp/agents/strategies/pead_sizing.py | ABSENT | 88ae944cea3d |
| pead | trading_corp/agents/strategies/pead_strategy.py | aec3aeadddfe | ac7c465b15a5 |
| **SHARED** | trading_corp/brokers/base.py | 46a3266d5ab0 | 353bbd1d21ec |
| **SHARED** | trading_corp/brokers/robinhood.py | 8263020088aa | 5862d2e8f2c6 |
| pead | trading_corp/data/earnings_provider.py | 8dca69ced386 | cc6c27185001 |
| pead | trading_corp/web/pead_view.py | 43c32c022c87 | db2c10a48853 |
| **SHARED** | trading_corp/web/routes.py | 1083551037f1 | 96becb83b19a |
| pead **NEW** | trading_corp/web/templates/partials/pead_dial.html | ABSENT | d31e3f072508 |
| pead | trading_corp/web/templates/partials/pead_live_sections.html | 5ab68dc5340c | c15419662ee5 |
| pead | trading_corp/web/templates/pead_live.html | 9924dc61642d | fb4506d0d901 |

Repo-only (NOT deployed to prod runtime; validated in the prod venv test run):
`tests/test_pead_sizing.py`, `tests/test_pead_render.py`, `planning/pead_sizing_forensic_2026-08-02.md`.

## PRE-DEPLOY DRIFT GATE (run `gate_a_pead_sizing.ps1`)

The runner prints prod's live LF-md5 per file and a verdict vs the BASELINE column:
- **pead files** — prod md5 must equal BASELINE (prod hasn't changed; I based off it).
  A pead file that != baseline = prod is ahead of `dafe60b` on that file → investigate
  before overwriting (do not clobber a prod-ahead pead file).
- **SHARED files (base/robinhood/routes)** — prod md5 MUST equal BASELINE. If any != baseline,
  **STOP**: Kalshi (or another deploy) changed it since staging. Do NOT blind-overwrite with the
  target; instead re-apply my additive hunks (below) on top of prod's current file and recompute.
- **NEW files (pead_sizing.py, pead_dial.html)** — must be ABSENT on prod pre-deploy. If present,
  someone else created them → STOP.

### The additive shared-file hunks (for re-apply if a shared file drifted)
All three are pure insertions (verified `git diff --stat prod-live` = **76 insertions, 0 deletions**):
- `base.py`: `+settled_cash: float | None = None` field on `AccountSnapshot` (after `equity_complete`).
- `robinhood.py`: `+` a `load_account_profile` settled-cash block after `buying_power`, and
  `+settled_cash=settled_cash` on the returned `AccountSnapshot`.
- `routes.py`: `+` one `@app.post("/telemetry/pead/max_concurrent")` closure after `pead_halt`.
Kalshi uses `KalshiBroker` (not RH) and separate route closures — these hunks cannot affect Kalshi
functionally; the only risk is a textual merge, which the drift gate catches.

## DEPLOY SEQUENCE (only after the drift gate passes)

1. **Gate-A pre-deploy** — `gate_a_pead_sizing.ps1` → all pead == baseline, all shared == baseline,
   new files ABSENT. Any SHARED drift → STOP + re-apply hunks.
2. **Backup** every target file on prod (`.bak_pead_sizing_20260802`).
3. **Copy PEAD-only files** (8 runtime files): config/strategies.yaml, pead_sizing.py (new),
   pead_strategy.py, earnings_provider.py, pead_view.py, pead_dial.html (new),
   pead_live_sections.html, pead_live.html.
4. **Apply SHARED files** (base.py, robinhood.py, routes.py): because the gate proved prod == baseline,
   copying the target versions IS the additive change. (If drift was found, apply the hunks instead.)
5. **Compile + test on prod venv**: `python -m py_compile` the 6 .py files;
   `python -m pytest tests/test_pead_sizing.py tests/test_pead_render.py -q` (NORMAL plugin autoload —
   `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` disables pytest-asyncio and yields false async failures).
6. **RESTART GUARD** (a whole-engine restart bounces all divisions):
   - **Bitunix FUTURES must be FLAT** before restart (open futures should not be bounced mid-trade).
   - **Bitunix SFP MAY hold positions** — that's expected; the SFP reconciler re-attaches on boot
     (do not require SFP flat). Confirm the SFP board shows the same positions after boot.
   - Confirm `pending_order` = 0 for robinhood_pead and PMCC (no in-flight orders).
   - Note PEAD `auto_execute: true` — first post-deploy scan may place; if you want a dry soak,
     set an `agent_state robinhood_pead/halt` first, or deploy after the daily scan window.
7. **Single restart** (whole engine) via the established root path (az RunShellScript / systemctl,
   non-sudo per session rule). One restart only.
8. **Gate-A post-deploy** — re-run the md5 check; every file must now equal the TARGET column.

## POST-DEPLOY VERIFICATION

- **settled_cash reads on first live snapshot**: engine log / `/telemetry/pead` account strip — the
  dial readout shows a non-null `settled ~$213` (not "settled cash unavailable"); confirms
  `load_account_profile` resolved on the cash account.
- **dial reads agent_state**: the account-strip dial shows the current cap; POST a new value
  (`/telemetry/pead/max_concurrent`) and confirm the next scan / readout reflects it (agent_state
  `robinhood_pead/max_concurrent_override` written).
- **sizer produces >= $50 names**: at ~$213 settled the readout says "funds ~4 at ~$50" (NOT ~10 at
  ~$20); first live `pead_intent`/`pead_entry` log `notional` >= $50.
- **dashboard**: Open Book is full-width at the top with company names under tickers; Upcoming
  Earnings + Rejections are collapsed by default with counts in their headers.
- **exits unaffected**: `manage()` still fires stop/drift/guard/time on held rows; `max_concurrent`
  and the sizer symbols are absent from the exit path (unit-guarded, but eyeball one held row).

## ROLLBACK
Restore `.bak_pead_sizing_20260802` for every file (shared files back to baseline; delete the two NEW
files) and restart once. prod-live tip stays `dafe60b` until a successful deploy advances it.
