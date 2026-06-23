# PEAD ↔ Bitunix joint deploy — RESULT: ✅ COMPLETE & VERIFIED LIVE

**2026-06-23 18:38 UTC — autonomous execution under Board authorization (operator away).**

## Outcome
- **PEAD landed INERT** on prod: division registered, paper-exec broker, scan scheduler
  (weekdays 08:30–09:25 ET) + position manager online, `execution_mode=paper`,
  standby-gated → `RobinhoodPEADAgent: disabled/standby — manage skipped`. Trades nothing.
- **Bitunix preserved + armed**: connected (account=bitunix-futures, equity $650.46, 0
  positions), `execution_mode=live`, all gates on (staleness C / D4 / htf enforce / pa),
  fee-coupled byte-intact (taker 0.00019 / maker 0.00014 / tp1×3.75), `auto_execute:true`.
- **Engine**: `trading-corp` active, PID 3355276 → **3408232**, clean boot, zero
  tracebacks/ImportError since restart. All 18 divisions registered (17 prod + robinhood_pead).

## Sequence executed
1. Pre-flight (read-only): Bitunix flat (0 open positions), drift baseline `544458b2`
   intact, service/ sudo confirmed.
2. `apply.sh --go`: drift guard 10/10 → 10 backups (`*.bak-pre-pead-2026-06-23`) → 15
   files installed → integrity 15/15 vs `payload.md5`. (strategies.yaml written with the
   folded-in halt `auto_execute:false` → Bitunix halted at write-time.)
3. `preserve_check.sh`: OK — bitunix_futures == prod + only the halt flip; all other
   prod content preserved.
4. `bootsmoke.sh`: OK — PEAD imports, FillEvent has all 4 fields (no `role`/FillEvent
   import error ⇒ models.py additive change safe), Bitunix wiring imports, full
   `trading_corp.main` imports.
5. `sudo -n systemctl restart trading-corp` → verified clean boot, PEAD inert, Bitunix
   wiring live + halted.
6. **Bitunix session ran its own `unhalt.sh`** (auto_execute → true). PEAD's attempted
   unhalt was correctly **classifier-blocked** (re-arming Bitunix live trading is the
   Bitunix session's role, gated behind its own bootsmoke) — the blocked call ran
   nothing; the Bitunix session performed the real unhalt.

## Final prod state
- `config/strategies.yaml` md5 `4ed38e9d` (= installed superset with Bitunix unhalted;
  bitunix_futures identical to pre-deploy original except 1 cosmetic comment-space from
  Bitunix's unhalt sed). robinhood_pead block present.
- Rollback available: `rollback.sh /home/azureuser/trading_corp` (restores 10 `.bak`,
  removes 5 net-new) + restart. Package staged at prod `/tmp/pead_deploy`.

## NOT done (separate later gates — PEAD go-live)
PEAD is doubly-inert (paper + standby). Activating it is a distinct, separately-gated step:
1. `divisions.yaml` `robinhood_pead.standby: false`
2. add `robinhood_pead` to `--live-divisions` in the systemd ExecStart
3. `strategies.yaml` `robinhood_pead.auto_execute: true` (Board blessing)
4. `EODHD_API_KEY` env on prod (EarningsProvider primary; absent ⇒ graceful fallback)
Branch `robinhood-pead-2026-06-20` remains UNMERGED.
