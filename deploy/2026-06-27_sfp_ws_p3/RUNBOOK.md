# Piece 3 — ws-migration DEPLOY RUNBOOK (2026-06-27)

Repoints the live Bitunix bar feed from REST polling → **ws-primary / REST-fallback hybrid**. ONE
persistent public ws (`wss://fapi.bitunix.com/public/`) maintains every `LiveBarCache.bars` in real time;
each cache's `refresh()` short-circuits to ws while fresh and REST-falls-back when stale. **Interface
unchanged → SFP + all consumers byte-unchanged.** Drains the residual recurring REST kline poll to ~0.

## Change surface (targeted-hunk; prod diverges from branch)
| file | type | prod md5 (gate) | staged md5 |
|---|---|---|---|
| `trading_corp/main.py` | targeted-hunk (+20 lines ws wiring only) | `698cd083…` | `2c1bb1dc…` |
| `trading_corp/data/live_bar_cache.py` | full-file (prod==branch base) | `bf757bfc…` | `d0bff778…` |
| `trading_corp/data/bitunix_ws_feed.py` | NEW file | (absent) | `a35a27cb…` |

**Byte-unchanged (NOT touched):** SFP observer `18da45f2`, strategy `5c71a103`, reconciler `3a23610c`.
`PIECE3_prod_vs_staged_main.diff` proves the main.py change is exactly the ws wiring block.

## Protocol (empirically captured from prod 2026-06-27, egress 168.62.60.79)
ws connects (101) on the clean IP; `{"op":"connect","data":{"result":true}}`; subscribe by channel
(`market_kline_3min/15min/60min/4h/1day`); ~2s in-progress pushes `{"ch","symbol","ts","data":{o,h,l,c,b}}`
with **no confirm flag** → bar-close via **bucket rollover** (`floor(ts/interval)`); ping/pong every 15s.
`websockets 16.0` already on prod (no new dependency).

## Steps (operator, from C:\Users\AA Incorporado\cc) — RESTART required
1. Review `deploy/2026-06-27_sfp_ws_p3/PIECE3_prod_vs_staged_main.diff` (exactly the ws wiring).
2. **Apply** (md5-gated; backs up to `~/p1_bak_2026-06-27/*.bak-pre-ws-2026-06-27`; NO restart):
   `powershell -ep bypass -f .\p3_apply.ps1`
   → abort on any `md5sum … FAILED`. Confirm byte-unchanged trio prints `18da45f2 / 5c71a103 / 3a23610c`.
3. **Restart — WHILE SFP IS FLAT** (boot warm-starts caches via REST then ws takes over; restart just after
   a 15m close with no open position):
   `powershell -ep bypass -f .\p1_restart.ps1`
4. **Verify**:
   `powershell -ep bypass -f .\p3_verify.ps1`

## Verify expectations
- `ActiveState=active`, new MainPID; markers `bitunix ws feed started (11 caches, 11 channels)` +
  `bitunix ws feed connected (11 channels)`; `sfp 15m loop spawned`; **few/no `ws stale`**; no Traceback.
- Deployed md5 == staged (`2c1bb1dc / d0bff778 / a35a27cb`); SFP/recon == `18da45f2 / 5c71a103 / 3a23610c`.
- `bitunix_bar_history` 3m advancing (ws-fed bars persist via the unchanged archiver).
- REST kline churn drops (the ws short-circuits the poll); on a ws drop, `ws stale → REST fallback` logs
  and the feed auto-reconnects — that's the hybrid working, not a failure.

## Rollback
Restore `~/p1_bak_2026-06-27/main.py.bak-pre-ws-2026-06-27` + `live_bar_cache.py.bak-pre-ws-2026-06-27`,
`rm ~/…/data/bitunix_ws_feed.py`, then `p1_restart.ps1`. (Caches revert to pure REST poll.)

## Safety design (why this is low-risk)
- **Fail-soft:** if the ws feed errors at startup, `except` logs and the caches stay on their REST poll.
- **Auto-fallback watchdog:** `refresh()` reverts a cache to REST in-process whenever ws goes stale
  (>45s no push) — no restart needed; ws reconnects with backoff.
- **Hard rollback** retained (backups + revertible). Restart while flat; venue B1 stop + TP leg protect any
  open position across the gap regardless.
