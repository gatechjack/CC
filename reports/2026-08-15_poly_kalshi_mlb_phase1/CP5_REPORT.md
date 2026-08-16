# CP5 — Go-live readiness (armed-ready, HELD)

**Status:** build-complete, **armed switch OFF, zero real orders, nothing deployed to prod by me.**
First live trade is your explicit separate go. Shared files (`kalshi_copy_trader.py`,
`sports_team_mapping.py`, `kalshi_live.py`) byte-unchanged. Every claim → paste or `file:line`.

---

## 1. Config / operator gates (`config/strategies.yaml` → `poly_kalshi_mlb`)
```
auto_execute: false     # ★ARM SWITCH (OFF)
stake_usd: 5.0          # $5/trade fixed
daily_loss_cap_usd: 100.0   # $100 realized loss/day -> persist_halt
per_trade_cap_usd: null      # NO per-trade cap (operator)
daily_deployment_cap_usd: null   # NO daily-deployment cap (operator)
max_slippage_cents: 2
roster_actor: polymarket_copy_trader ; roster_key: selected_whales
```
Executor now treats a `None` cap as **disabled** (`test_caps_none_disables_size_and_daily_gates`); the
$100 daily-loss halt is the active backstop.

## 2. Loop reads the roster from `selected_whales` (hardcoded dict DROPPED)
`PolyKalshiCopyTrader.__init__` no longer takes `whales`; `_load_roster()` reads
`agent_state(polymarket_copy_trader, selected_whales)` **fresh each cycle**; `poll_cycle` iterates it.
Idempotency stays keyed on **wallet**. Proven:
- `test_loop_reads_roster_from_selected_whales_and_reloads` — loads `[(name,wallet)…]`; mutate the row →
  next read reflects it (per-cycle reload).
- `test_poll_iterates_roster_wallet_not_hardcoded` — polls the roster's wallet, no hardcoded default.
- Pre-arm run loaded the 4 from `selected_whales` (see §7).

## 3. Reseed Kalshi-matchable gate (`refresh_polymarket_whales.py`)
New module `trading_corp/data/kalshi_matchable.py` (`MATCHABLE_CATEGORIES={"mlb"}`, `classify_dominant`,
`is_kalshi_matchable`). Wired into the reseed at `refresh_polymarket_whales.py` right after the finalist
sort: finalists whose dominant category ∉ `{"mlb"}` are dropped (logged) before `selected_whales` is
written — so a reseed can't drag esports/mixed whales back. Proven:
- `test_reseed_gate_keeps_only_matchable` — 2 MLB + 1 esports + 1 politics candidates → only the 2 MLB kept.
- `test_dominant_and_matchable`, `test_classify_buckets`. Reseed job imports clean with the gate.

## 4. Prod roster write — operator runners (I do NOT touch prod)
`cc\cp5_roster_verify.ps1` (read-only) and `cc\cp5_roster_write.ps1` (write), both validated: pure ASCII,
PS 5.1 parse OK, base64-embedded python compiles. They `az vm run-command invoke -g RG-SHARED-PROD -n
tc-prod-vm` and set/read `agent_state` via the same `set_agent_state`/`load_agent_state` the routes use.
- **They write BOTH `selected_whales` AND `pinned_whales` = the 4.** Reason: the default `pins_only`
  reseed writes `selected = pinned`, so leaving legacy names in `pinned_whales` would revert the roster.
- Operator sequence (short, non-wrapping):
  ```
  powershell -ep bypass -f .\cp5_roster_verify.ps1     # see current prod roster
  powershell -ep bypass -f .\cp5_roster_write.ps1      # write the 4 + verify
  ```
  The write runner self-verifies (`selected count 4`, `legacy remaining []`, `VERIFY 4-only-no-legacy
  True`). **After you run it, paste the output and I'll confirm prod == the 4, legacy gone.**

## 5. $100 daily-loss halt — confirmed
Loop reads `daily_loss_cap_usd = 100.0`. `record_realized()` accrues realized P&L and calls the same
`StrategyState.persist_halt` on breach. Pre-arm (§7): `record_realized(-60)` no-halt; `record_realized(-45)`
→ cumulative −105 ≤ −100 → **halted True, persisted True**, and the next submit → `blocked_halt`.

## 6. The arm switch (single, OFF)
**`poly_kalshi_mlb.auto_execute` in strategies.yaml.** `false` → the loop constructs
`PolyKalshiExecutor(dry_run = not auto_execute)` = `dry_run=True` = shadow; the only V2 POST
(`poly_kalshi_executor.py:261`) is behind `if not self._dry_run:` (`:258`) and `dry_run` defaults `True`
(`:191`). **Confirmed OFF** (§7: `auto_execute=False → dry_run=True`). Flipping it to `true` is the arm.

## 7. Pre-arm dry-run proof (`cp5_02_prearm.py`) — reads the roster, 0 orders
```
CONFIG GATES: stake_usd=5.0  daily_loss_cap_usd=100.0  per_trade_cap=None  daily_deployment_cap=None
ARM SWITCH auto_execute = False  ->  executor dry_run = True  (POST unreachable)
loop.daily_loss_cap reads $100: True
ROSTER loaded from selected_whales (NOT hardcoded):
  SDTrading / 0x0x23kjookhaiuohduoayh8c9 / xifutloong3 / monkeymashingkeyboard  (the 4)
DRY-RUN: polls=3 would_place=1 REAL_ORDERS_PLACED=0
  would-place: xifutloong3 -> KXMLBGAME-26AUG151840MIACIN-MIA bid x9 key=29b97280..  ($5/contract sizing)
$100 HALT: record_realized(-60)->False ; record_realized(-45)->cumulative -105-> halted True ;
           persisted True ; subsequent submit -> blocked_halt
```

## 8. placed = 0 (static + runtime)
Static: only POST at `:261` behind `if not self._dry_run:`; `dry_run` defaults True; config arm OFF.
Runtime: pre-arm `REAL_ORDERS_PLACED=0`. Shared-files diff empty.

## 9. Tests / attestation
50 tests green (matcher 17 · executor 19 · loop 11 · matchable 4; `-p no:pytest_ethereum`).
`git diff --stat HEAD -- kalshi_copy_trader.py sports_team_mapping.py kalshi_live.py` → empty.
Commit `373b72e`.

## 10. What is NOT done — the actual go-live (your explicit go)
Nothing is on prod yet (this branch is unmerged/undeployed). To arm live, the operator:
1. **Run `cp5_roster_write.ps1`** → prod `selected_whales`/`pinned_whales` = the 4 (safe now; the legacy
   observer places nothing). Paste output for my confirm.
2. **Deploy this branch's code + config** to prod (loop, executor, matchable gate, reseed gate,
   strategies.yaml block) via the prod-live deploy path.
3. **Register the loop in `main.py`** — I did **not** wire main.py (per your "no main.py live wiring that
   could place"). Proposed (HELD, not applied): a scheduled loop constructing
   `PolyKalshiExecutor(dry_run = not cfg.auto_execute, broker=<own KAREN KalshiLiveBroker>)` + the
   `PolyKalshiCopyTrader` reading the config gates. With `auto_execute:false` it runs in shadow.
4. **Flip `auto_execute: true`** (the arm) + restart. That is the first-live-trade gate — your call.

Open decision still standing: exit-copy for hold-to-resolution whales (all observed actions are BUY).
