# Kalshi Copy-Trading Go-Live (Phase K5) — Build + Deploy Runbook

Branch: `kalshi-k5-golive-2026-06-30` (off main `9bfd7ff`). Workstream B (dashboard)
ships separately on `kalshi-k5-dashboard-2026-06-30`.

> **STATUS 2026-07-01 — ✅ DEPLOYED INERT to prod + VERIFIED LIVE.** Both workstreams merged
> to `main` (B `3bf4446` → A `f3f150c` → post-deploy dashboard-500 fix `7614b01` →
> `a73044e`, `main == origin`). The **INERT deploy** section below is DONE: 8 files byte-exact
> on prod (PID **34501**), division still `broker: paper` / `auto_execute: false` / kalshi NOT
> in `--brokers`+`--live-divisions` → `KalshiLiveBroker` never constructed, **zero live
> orders**. Dashboard 200 (a post-deploy 500 from a Jinja macro-ordering bug was caught +
> fixed-forward, `7614b01`; template md5 `c8bcea57`). Full record: `runbooks/deploy_log.md`
> (2026-07-01 entry). Rollback backups: `*.bak-pre-k5-inert-2026-06-30`.
> **⏸ LIVE FLIP (the "Live flip" section below) remains DEFERRED** pending operator roster
> review on the new dashboard + the go-live gates (Apify live feed restored + budget-isolated
> is the hard blocker).

## What was built (Workstream A — live execution path)

| Slice | What | Files |
|---|---|---|
| K5·1 | `KalshiLiveBroker` over pykalshi (marketable IOC place/cancel/confirm), positions field-bug fix, `KalshiNoFill`, `assert_live_ready` kalshi branch | `brokers/kalshi_live.py` (new), `brokers/kalshi.py`, `utils/secrets.py` |
| K5·2 | factory live-branch (anti-half-flip) | `main.py` |
| K5·3 | copy-loop gated live placement + per-trade risk bypass + entry/exit write-back | `main.py`, `agents/strategies/kalshi_copy_trader.py` |
| K5·4 | feed-health / mass-exit circuit breaker (non-negotiable safety) | `agents/strategies/kalshi_copy_trader.py`, `main.py` |

Tests: 54 new (broker/factory/loop/feed-guard), 120 adjacent regression — all green.

Locked design honored: marketable **IOC**, ceiling = whale price ± `max_slippage_cents`
(**2¢**, operator-confirmed), partials accepted; **no per-trade risk gate** on the live
path (approval = `auto_execute` + Board roster `selected_whales`); no automated account
cap (kill-switch = the halt); feed-health/mass-exit guard ON by default.

## Post-shakedown: V2 order-endpoint rebuild + demo validation (2026-06-30)

The 2026-06-30 prod $1 shakedown found pykalshi 1.0.6 POSTs the now-DEPRECATED v1
`/portfolio/orders` (HTTP 410). Rebuilt onto Kalshi's V2 endpoint:

| Slice | What | Commit |
|---|---|---|
| K5·1b | order path → V2 `POST /portfolio/events/orders` (single-book YES-centric bid/ask) via pykalshi's signed low-level `client.post`/`delete`; `api_base` → `external-api.*` hosts; cancel = `DELETE /portfolio/events/orders/{id}`; V2 response → FillEvent directly; demo-validate script | `e91c8aa` |
| K5·1c | FOK `409 fill_or_kill_insufficient_resting_volume` → benign `KalshiNoFill` (only that code; every other error stays loud) | `ca871ae` |

**Mapping (grounded in docs; UI-verified):** `bid`=buy YES, `ask`=sell YES; **buy NO = `ask` @ (1−P)**, sell NO = `bid` @ (1−P); slip in the fill-ensuring direction; price 4-dp string, count `str(int)`.

**DEMO VALIDATION 2026-06-30 (demo.kalshi.co keypair, funded $125, market `KXALIENS-27-29`):**
- ✅ **410 blocker RESOLVED** — V2 orders accepted (a GTC rested, the buy-NO filled).
- ✅ **NO-mapping UI-VERIFIED** — the demo UI showed a **NO** holding at ~71¢ (the load-bearing, money-if-wrong check).
- ✅ **3-bug positions read validated on a NON-zero position** (first real-data proof): `position_fp` / `market_exposure_dollars` read correctly.
- ✅ cancel (V2 DELETE) works; `count` as `str(int)` accepted (**open item #2 resolved**).
- ✅ **K5·1c** FOK-no-fill now benign — unit-verified against the exact 409 error string seen on demo. (Live demo re-verify was blocked: the demo key **401'd** — revoked/expired after the run — not a code issue.)
- **Fee open item:** **resolved at N=1** (1-contract NO cost ~$0.71 + ~$0.01 fee); the multi-contract per-contract-vs-total convention is **assumed, and will be confirmed on the first live multi-contract fill.**
- buy-YES fill not exercised (this demo market's book was crossed); the fill path is proven via the identical-code-path NO fill.

**Tests:** V2 broker 47 + K5·1c 7 + adjacent suites — all green. Reads (balance/markets/positions) unchanged on pykalshi. `quote()`/orderbook field-name fix filed **BACKLOG PZ** (low, non-blocking — order path uses `limit_price`, not `quote()`).

## Deviations from the plan (flagged)

1. **Live-arm gate is `isinstance(broker, Broker) AND not broker.paper`** (not isinstance
   alone). Kalshi's PAPER config (`broker: paper`) yields a `PaperExecutionBroker` — a
   `Broker` with `paper=True` — so isinstance alone would misclassify the INERT paper
   deploy as armed. Both legs required. (Polymarket didn't need this; its paper broker is
   a `ReadOnlyBroker`.)
2. **Positions field bug was 3 bugs, not 1** (re-anchor DRIFT-1): pykalshi field names
   (`position_fp`/`market_exposure_dollars`) + a `Position(...)` ctor using nonexistent
   kwargs and omitting required `account`/`opened_ts`, swallowed by a bare `except` →
   returned `[]` for every funded account. All fixed in K5·1.
3. **Exit-residual auto-reconcile is OUT OF SCOPE** (mirrors polymarket E2·6). Exits are
   placed `reduce_only=True` (cannot over-sell at the venue); an exit that doesn't fully
   clear is FLAGGED for manual reconcile (`kalshi_copy_exit_residual` audit + Telegram).
   Auto re-reconciliation of an un-closed live position is E5-class.
4. **`standby: true`** on `kalshi_copy_trading` (divisions.yaml) is NOT read by the kalshi
   copy loop/strategy (it's a robinhood/tasty-division kill-switch + a UI badge). It does
   NOT need flipping for go-live; the operator MAY set `standby: false` for UI accuracy.

## INERT deploy (code-only; division stays paper) — ✅ DONE 2026-07-01 (PID 34501)

> ✅ **Executed 2026-07-01** — 8 files byte-exact on prod, flat-window restart → PID 34501;
> post-deploy prod base == main `9bfd7ff` (ZERO drift — no targeted-hunk needed). See
> `runbooks/deploy_log.md` 2026-07-01 entry. The description below is the as-planned record.

The new code is **fully inert** while the division stays `broker: paper` and out of
`--live-divisions`: the factory paper path returns `KalshiBroker`/`PaperExecutionBroker`,
`KalshiLiveBroker` is never constructed, and the loop's `is_live_armed` is False → the
existing `would_have_placed` paper behavior is byte-unchanged. Verified prod state
(2026-06-30): engine PID 13679, kalshi NOT in `--brokers`/`--live-divisions`,
`kalshi_copy_trading` `broker: paper`, `auto_execute: false`.

Changed/new files to deploy (Workstream A):
- `trading_corp/brokers/kalshi_live.py` (NEW)
- `trading_corp/brokers/kalshi.py`
- `trading_corp/utils/secrets.py`
- `trading_corp/main.py`
- `trading_corp/agents/strategies/kalshi_copy_trader.py`
- `trading_corp/scripts/kalshi_demo_smoke.py` (NEW, operator-run only)

**Drift gate (do NOT file-copy):** prod `main.py` / `kalshi_copy_trader.py` may diverge
from `main` (per memory, prod web/data.py is newer; treat .py the same). Build a
**targeted-hunk** patch against the PROD blob at deploy time (same discipline as the
bitunix deploys) and re-confirm md5s then — not pre-computed here (they'd go stale).
**Restart bounces ALL live divisions (bitunix_sfp + bitunix_futures + robinhood_pead)** →
do it at a flat window. Rollback: keep `*.bak-pre-k5-2026-06-30` + restart.

## Go-live gates (ALL must be green — operator-gated; NOT done by the build)

- [ ] **Apify live `open_positions` feed restored AND budget-isolated** (today: HTTP-400,
      cap exhausted). The live feed shares `saswave/kalshi-profile-scraper`; keep it
      selected-whales / open-positions-only so a discovery overrun can't starve it. Bot
      has nothing to copy until this is healthy.
- [ ] **Kalshi DEMO validation passed** — run `kalshi_demo_smoke.py` (KALSHI_USE_DEMO=1 +
      demo creds): connect + snapshot + 1-contract IOC place→fill/no-fill→cancel.
- [ ] **Roster final** (Board) · **funding confirmed** (account ~$499; bounded by roster×$1-3).
- [ ] **Feed-health guard verified** (K5·4 — done in code; confirm on demo/paper).
- [ ] **Kill-switch tested** (below).
- [ ] **§4 Backtester / Board sign-off** on the net-new live code.

## Live flip (flat window) — config + root unit edit — ⏸ DEFERRED (operator roster review pending)

1. `config/divisions.yaml` `kalshi_copy_trading.broker: paper -> kalshi` (azureuser-owned; no sudo).
2. `config/strategies.yaml` `kalshi_copy_trader.auto_execute: false -> true` (owned; hot-reloads, but the broker arms at restart).
3. systemd unit (ROOT, via Azure Run Command — operator has no sudo password):
   `--brokers bitunix robinhood kalshi --live-divisions bitunix_sfp robinhood_pead bitunix_futures kalshi_copy_trading`
   then `sudo -n systemctl daemon-reload && sudo -n systemctl restart trading-corp`.
4. Leave `KALSHI_USE_DEMO` UNSET for real-money live (set `1` only for demo).
5. (Optional) `divisions.yaml` `standby: false` for UI accuracy.

## Kill-switch / rollback

- **Fastest correct (no restart, no sudo):** `kalshi_copy_trader.auto_execute: false` in
  `strategies.yaml` — hot-reloaded within ≤1 poll (~10 min); live placement stops, paper
  logging resumes. A one-line `.ps1` runner (ASCII, stdin-piped bash per the command-paste
  rule) will be prepared WITH the live flip, not before (it's only needed once live).
- **Nuclear:** `sudo -n systemctl stop trading-corp` (all divisions).
- **Full rollback to paper:** revert divisions.yaml broker `kalshi -> paper` +
  strategies.yaml `auto_execute -> false` + remove kalshi from the systemd
  `--brokers`/`--live-divisions` (root) + restart. Keep `.bak` before every edit.

## Stale doc to correct post-deploy

`docs/divisions.md` (~row 35) — "253 RT / +$0.58 net / 2 whales" is stale.
