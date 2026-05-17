# Next-session pickup prompt (2026-05-18)

*This file was rewritten 2026-05-17 17:45 UTC by the Polymarket
watchlist weekly-refresh session. The previous content described the
2026-05-17 05:40 UTC EOS (Phase 1E flip + paper-mode multi-leg replay)
— that snapshot is still in BACKLOG.md if you need its detail; the
canonical pickup-now state is below.*

---

Paste this into a fresh Claude Code session in `C:/Users/AA Incorporado/cc`:

---

Resuming from 2026-05-17 ~17:45 UTC wrap. Two sessions ran in parallel
yesterday — both finished cleanly. Read the EOS snapshot at the top of
`BACKLOG.md` first; it supersedes the older 17:25 UTC snapshot left by
the parallel session.

## What landed yesterday — three deploys to prod, all reversible

1. **14:43 UTC — Polymarket watchlist seed + dashboard panel** (commit `30f8abe`).
   `agent_state(polymarket_copy_trader, watch_only_whales)` = 50 whales. Dashboard renders at `/prediction-markets/polymarket_copy_trading`. Local-IP one-shot — prod sweep crashed at chunk 1163 with Cloudflare 403; recovery shipped data via `set_agent_state` directly.

2. **17:18 UTC — Promote / Demote buttons across both venues + `pinned_whales`** (commit `efa6dc8`).
   - Watch-list rows: VIEW + PROMOTE (`POST /api/<venue>/watchlist/promote/<id>`).
   - Selected Whales rows: VIEW + DEMOTE (`POST /api/<venue>/whales/demote/<id>`).
   - Demote calls module-level `force_close_whale_positions` → synthetic SELL `would_have_placed` audits at entry-price → resolver pairs into round_trips.
   - New `pinned_whales` slot per venue keeps manual promotions sticky across `refresh_*_whales.py` runs.
   - New audit kinds: `{polymarket,kalshi}_whale_{promoted,demoted}`. BACKLOG `WO-4` closed.

3. **17:38 UTC — Polymarket watchlist weekly cron + Cloudflare 403 retry** (commit `873e004`).
   - `_get_json` retries on 403+cf-ray with exponential backoff (30/60/120/240/300s, ~6 attempts). Terminal failure raises `PolymarketRateLimitError`.
   - `fetch_market_resolutions` per-chunk swallow → partial coverage instead of abort.
   - `seed_polymarket_watchlist_deep.py --merge --max-total N` for weekly accumulation.
   - `trading-corp-pm-watchlist-deep.{service,timer}` enabled + active. **Next fire: Sun 2026-05-24 13:02:51 UTC.**

Service is healthy. PID 598297 (post-17:18 restart, not changed by the 17:38 ship — Option 1 deferred the restart). Local git tree clean for both sessions' files.

**One caveat on the 17:38 deploy:** Option 1 was chosen (no `systemctl restart trading-corp`). Live PCT + polymarket_arbitrage still use the pre-patch in-process Polymarket client and will pick up the Cloudflare retry on the next natural restart. Acceptable; failure mode is just an error log on an edge case. The weekly cron's seed already gets the retry because it runs in a fresh Python process.

## Read first

1. `BACKLOG.md` — EOS snapshot at top (2026-05-17 **17:45** UTC; supersedes 17:25).
2. `runbooks/deploy_log.md` — top 3 entries are yesterday's deploys (14:43, 17:18, 17:38 UTC).
3. Memory (loaded automatically):
   - `trading_corp_polymarket.md` (updated — weekly cron now DEPLOYED)
   - `feedback_crlf_routes_py_deploy.md` (CRLF gotcha if you touch routes.py)
   - `feedback_parallel_sessions_stop_and_discuss.md`
   - `feedback_uvicorn_no_reload_in_prod.md`

## FIRST ACTION — three verification queries

SSH likely still blocked from non-home IPs; pivot to `az vm run-command create --script @file` per `feedback_az_run_command_when_ssh_blocked.md`. Windows checkouts are CRLF → `tr -d '\r'` before deploy.

### Q1 — Browser eyeball

Hard-refresh `https://trading.jacksumner.com/prediction-markets/polymarket_copy_trading`. Confirm:
- Polymarket Watch List section renders 50 whales below Selected Whales (top: `everydaymortgage / 90% / 577 positions / $1.42M`).
- PROMOTE button on watch-list rows (renders + htmx confirm prompt).
- DEMOTE button on Selected Whales rows (renders + htmx confirm prompt that mentions synthetic-SELL semantics).
- 📌 badge on manually-promoted whales (none yet expected — first PROMOTE smoke test will create one).

### Q2 — Weekly timer state

```bash
sqlite3 :memory: 'select 1'  # local sanity
# then on prod:
systemctl list-timers trading-corp-pm-watchlist-deep.timer --no-pager
```

Expected: `NEXT Sun 2026-05-24 13:0X:XX UTC` (within 15-min jitter), state `enabled` + `active`. If `inactive` or missing → rollback recipe in `runbooks/deploy_log.md` "2026-05-17 17:38 UTC" entry.

### Q3 — Health of yesterday's three deploys end-to-end

```bash
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
-- (a) Polymarket watchlist slot still populated?
SELECT COUNT(*) AS slot_present
  FROM agent_state
 WHERE actor='polymarket_copy_trader' AND key='watch_only_whales';

-- (b) Any promote/demote audits since 17:18 UTC?
SELECT kind, COUNT(*) AS n
  FROM audit_event
 WHERE kind IN ('polymarket_whale_promoted','polymarket_whale_demoted',
                'kalshi_whale_promoted','kalshi_whale_demoted')
 GROUP BY kind;

-- (c) PCT pending count (the 11:30 UTC pruner ran at 11:35 yesterday — should still be cleaning).
SELECT COUNT(*) AS pct_pending
  FROM audit_event
 WHERE actor='polymarket_copy_trader' AND kind='would_have_placed';
SQL
```

Expected: (a) `1`. (b) Empty (no clicks yet) OR small counts if smoke-tested. (c) ~1,800-2,000 (was 1,861 at 14:53 UTC yesterday before the 17:18 restart).

## Pickup candidates (ordered)

1. **Smoke-test PROMOTE end-to-end** — pick the lowest-stakes watch-list whale (smallest `realized_pnl_usdc`), click PROMOTE, verify:
   - `agent_state(polymarket_copy_trader, selected_whales)` grew by one.
   - `agent_state(polymarket_copy_trader, pinned_whales)` grew by one.
   - `polymarket_whale_promoted` audit landed.
   - Strategy picks it up on the next 60s poll → `polymarket_copy_cold_start` audit on the promoted wallet.

2. **Smoke-test DEMOTE end-to-end** — only after PROMOTE works. Pick a whale with zero open paper positions (lowest risk of dangling synthetic SELLs). Verify `polymarket_round_trips` gets new rows with `extra_json.is_synthetic_close=true` and the resolver pairs them. After Polymarket succeeds, exercise on Kalshi.

3. **Optional housekeeping** (each ~5-15 min):
   - Fix the `apply='true'` query bug in `runbooks/session_start_2026_05_17.md` — `json_extract(...)='true'` doesn't match SQLite integer `1`. Two-line edit; prevents future verification queries returning empty.
   - Decide on `reports/{backtest_results, data_inventory, decision_log, hypotheses, strategy_candidates}.md → decision_log.zip` archival. Currently shown as deleted in `git status`; commit the archival or restore.

4. **Standing backlog** (no urgency from this session):
   - Kalshi weather dashboard analysis partial (P3, 1-2h; data gate met — 63 rows since target_iso ship).
   - Kalshi `temporal_bucket_arb` `expires_at` payload audit (P2, ~30 min).
   - Live broker quote in `force_close_whale_positions` (parallel-session deferred; ~1h, would need a broker reference in the helper path).
   - PMCC audit (perennial — needs scope-narrowing first).

5. **A week out — Sun 2026-05-24**: watch the first weekly cron fire. Verify it merges new whales without clobbering existing `included_iso`. Expected wall-clock ~30-60 min (~2300 gamma-api calls). With the Cloudflare retry now live in the timer's Python process, a partial-rate-limit during the sweep will degrade to partial coverage instead of full abort.

## Things to NOT do without explicit approval

- Don't `systemctl restart trading-corp` blindly. The live PCT + polymarket_arbitrage retry-resilience is dormant until that happens — only restart if you've decided you want it. ~5-15s blip.
- Don't disable `trading-corp-pm-watchlist-deep.timer` or change its cadence without ≥1 successful weekly run confirming behavior.
- Don't delete backup tags `pre-pm-weekly-refresh-20260517-1730`, `pre-promote-demote-20260517-1718`, or `pre-pm-watchlist-20260517-1443` until ≥48h post-deploy.
- Don't demote a whale with significant open paper position count without first verifying the resolver pairs the synthetic SELLs (low-stakes whale first).
- Don't change the `pinned_whales` schema or the per-venue `selected_whales` shape.
- Don't flip BitUnix `htf_gate.mode: enforce → shadow`. Don't flip `trade_plan.enabled: true → false`. Standard BitUnix do-not-touch list applies.
- Don't deploy via `patch -p1` over a file that touches `routes.py` without prepending `sed -i 's/\r$//' trading_corp/web/routes.py` per `feedback_crlf_routes_py_deploy.md`.
- Don't disable the PCT stale-pruner timer (`trading-corp-pct-pruner.timer`) — separate from this session, still load-bearing for the watchlist hygiene.

## Environment notes

- Local Python: `C:\Users\AA Incorporado\AppData\Local\Python\bin\python.exe` (bare `python` is the MS Store stub and breaks).
- SSH usually blocked from non-home IPs; pivot to `az vm run-command create --script @file` per `feedback_az_run_command_when_ssh_blocked.md`.
- Windows checkout CRLF; deploy scripts MUST `tr -d '\r'` before `az vm run-command create`.
- `.py` changes under `trading_corp/` need `systemctl restart trading-corp` to take effect in the live service (uvicorn runs without `--reload` in prod). Templates DO live-reload (Jinja). Timer-driven scripts pick up new code automatically because they spawn fresh Python processes.
- `az vm run-command create` is single-tenant; `--name` must be unique-per-deploy or `az vm run-command delete --yes` first.

Honest assessment first — don't dive into code until the three verification queries (Q1/Q2/Q3) come back clean.
