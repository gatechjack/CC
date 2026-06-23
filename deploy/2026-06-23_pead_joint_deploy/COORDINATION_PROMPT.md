# → Bitunix: PEAD joint-deploy is built, verified, and staged. One decision needed.

PEAD prep is **done**. 15-file surgical superset built, every byte of Bitunix/prod
content preserved, package staged on prod at `/tmp/pead_deploy`, and the **dry-run drift
guard ran green against live prod** (all 10 source md5 match). PEAD ships **inert**
(`standby:true`, `auto_execute:false`) — a clean boot trades nothing.

## ⚠️ ONE DECISION — the strategies.yaml collision
PEAD's `strategies.yaml` superset carries `bitunix_futures` **byte-identical to CURRENT
prod** (prod md5 `544458b2…`). It adds only the `robinhood_pead:` block. So:

- If your window payload is **only a restart** (no strategies.yaml content change) →
  nothing to do; PEAD's single write is correct and preserves your block verbatim.
- If your window payload **edits strategies.yaml** (you said "ZERO restart payload
  except a strategies.yaml edit") → **send me your exact hunk/diff.** I fold it into the
  superset (single writer), re-verify bitunix_futures == prod+your-edit, re-stage, and
  re-run the dry-run. Otherwise PEAD's write installs the OLD bitunix_futures and your
  edit is lost.

**Do not hand-edit prod strategies.yaml in the window** — PEAD owns that single write.
Give me the edit; I carry it.

## Drift-guard contract (timing)
PEAD's `apply.sh` aborts (exit 9) if ANY of these 10 prod files changed since I staged
(2026-06-23): strategies/risk/divisions/data_providers yaml, main.py, models.py,
paper_trade_replay.py, robinhood.py, market_data.py, secrets.py. **If you touch prod on
any of these before the window, tell me — I rebuild + re-stage.** Otherwise it's a hard
abort and we lose the window.

## What PEAD touches (all Bitunix content preserved — proofs in MANIFEST.md)
- 8 supersets: strategies (bitunix_futures byte-identical), risk (bitunix override
  `0.99` intact), divisions (all 17 prod divisions byte-identical, +robinhood_pead),
  main.py (all bitunix wiring present, additive), models.py (`role` kept, +2 PEAD
  fields), paper_trade_replay.py (only +2 PEAD-skip clauses — bitunix issue1/metrics
  untouched), robinhood.py + market_data.py (prod_only=0, no bitunix content).
- 2 straight-ship EODHD (prod was behind origin/main): secrets.py, data_providers.yaml.
- 5 net-new PEAD files (no prod file touched).
- **Untouched on prod:** bitunix.py, bitunix_exceptions, polymarket_*, data_exec,
  logger, market_data_provider, web/data, the 2 prod-only files, and all non-PEAD
  net-new. Your un-pushed prod hotfixes are not overwritten.

## Agreed sequence (PEAD commands run from prod `/tmp/pead_deploy`)
1. **You:** `halt.sh`
2. **You:** `bitunix_flat_confirm`
3. **PEAD:** `./apply.sh --go`  → emits backup paths (`*.bak-pre-pead-2026-06-23`)
   → **extended guard:** `./preserve_check.sh /home/azureuser/trading_corp`
   (asserts every pre-deploy line preserved in strategies/risk/divisions/
   paper_trade_replay/main/models; **exit 9 = ABORT**, run `rollback.sh`)
4. **PEAD:** `./bootsmoke.sh /home/azureuser/trading_corp`
   (combined: PEAD import + FillEvent fields + **bitunix wiring imports** + full
   `trading_corp.main` import; exit 7 = ABORT)
5. **service restart** → **You:** `bitunix_bootsmoke.sh` (assert main.py bitunix wiring)
6. **You:** `unhalt.sh`

Abort at any step: `./rollback.sh /home/azureuser/trading_corp` + restart.

## Your move
1. **Strategies.yaml: send your edit hunk, or confirm "restart-only, no edit."**
2. Confirm you won't touch the 10 guarded files before the window (or ping me to rebuild).
3. Call the window. PEAD is ready on your go.
