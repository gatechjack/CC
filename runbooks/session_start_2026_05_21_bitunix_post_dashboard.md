# Next-session pickup prompt (post-2026-05-20 22:51 UTC) — BitUnix dashboard tile DEPLOYED, observation window

*Written 2026-05-20 ~23:30 UTC at end of BitUnix PRIORITY 2 dashboard-tile session. This is the canonical pickup point for the BitUnix work thread; the kalshi_crypto and kalshi_weather threads have their own pickup runbooks (`runbooks/session_start_2026_05_21_kalshi_*.md`).*

---

Paste this into a fresh Claude Code session in `C:/Users/AA Incorporado/cc`:

---

Resuming the BitUnix work thread. **PRIORITY 2 (reconciler-state tile + corrected-outcome display) SHIPPED 2026-05-20 22:51 UTC** as commit `1264f55` on `origin/main`; deploy log entry separately as `7ff2a9e`. Prod md5s verified for all 4 deployed files. The dashboard tile renders green (`✓ Reality match · 3/3 v2 trades`) and the corrected-outcome badges on the 2 historical `audit_corrected=true` trades render with native-vs-corrected tooltips. The reconciler now writes one `audit_event` row per run; first-ever row id 407478 exists. Read first: `reports/bitunix_v2_fix_2026-05-20.md`, `runbooks/deploy_log.md` 2026-05-20 22:51 UTC entry, memory `trading_corp_bitunix_vision.md` (auto-loaded). The bitunix-kline-200-bar-cap reference memory was added.

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 1 — Watch the immune system run unsupervised
═══════════════════════════════════════════════════════════════════════════

The reconciler timer's next scheduled fire is **2026-05-21 06:03 UTC + jitter (RandomizedDelaySec=600)**. After 06:13 UTC, verify the daily-cron path actually fires:

```bash
ssh azureuser@trading.jacksumner.com '
journalctl -u tc-audit-reality.service --since "06:00 today" --no-pager
systemctl list-timers tc-audit-reality.timer --no-pager
'
```

What you want to see:
1. A scheduled service invocation between 06:03 and 06:13 UTC.
2. Exit code 0 (`matches: N/N   mismatches: 0`).
3. A new `audit_reality_run` audit_event row written by `_persist_summary`. Verify:
   ```sql
   SELECT id, ts, json_extract(payload_json,'$.status') AS status,
          json_extract(payload_json,'$.n_matches') AS n_matches,
          json_extract(payload_json,'$.n_total') AS n_total
   FROM audit_event WHERE kind='audit_reality_run' ORDER BY id DESC LIMIT 3;
   ```
   Should show at least 2 rows by then (id 407478 from manual deploy-day run + the new 06:03-ish row).

This is the **first fully-unattended end-to-end exercise of the immune system.** If it fires clean, the system has demonstrated it can self-diagnose silent failures without a human in the loop.

If the timer FAILS to fire, OR the service exits non-zero, OR the dashboard tile flips to red/amber: investigate before doing anything else bitunix-shaped.

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 1b — Watch for a wide-window v2 trade
═══════════════════════════════════════════════════════════════════════════

**The 200-bar fetcher fix is verified-not-broken but NOT verified-working against its actual failure mode.** Post-deploy trade `ef6e6697` (2026-05-20 16:30 UTC) lived 473 seconds (2 bars) — the buggy fetcher would have returned a correct slice on a window that small. The pagination path remains untested by a real losing trade against its intended failure mode.

The informative milestone is a v2 trade that:
- Lives long enough to require >200 bars from the kline fetcher (3m bars × 200 = 600 minutes = 10 hours window minimum)
- Ideally progresses past TP1 in real price action (which is what the pre-fix bug would have hidden by truncating the early bars)

Watch-queries (run any time):

```sql
-- Wide-window v2 trades post-deploy (>10h lifetime or 200+ bars walked)
SELECT order_id, ts, result, actual_r_multiple, bars_to_resolution,
       json_extract(extra_json,'$.filled_legs') AS filled_legs
FROM paper_trade_record
WHERE division='bitunix_futures'
  AND ts >= '2026-05-20T22:51:00+00:00'
  AND json_extract(extra_json,'$.tp_plan_version') = 'v2'
  AND (bars_to_resolution > 200 OR (julianday(result_ts) - julianday(ts)) * 1440 > 600);

-- First position_sl_update audit post-tile-deploy (count was 0 pre-deploy)
SELECT COUNT(*) AS n_sl_updates, MIN(ts) AS first_sl_update
FROM audit_event WHERE kind='position_sl_update'
  AND ts >= '2026-05-20T22:51:00+00:00';

-- Trade with non-empty filled_legs post-tile-deploy
SELECT order_id, ts, result, json_extract(extra_json,'$.filled_legs') AS filled_legs
FROM paper_trade_record
WHERE division='bitunix_futures'
  AND ts >= '2026-05-20T22:51:00+00:00'
  AND json_extract(extra_json,'$.filled_legs') != '[]'
  AND json_extract(extra_json,'$.filled_legs') IS NOT NULL;
```

Historical fire rate is ~0.7/day; expect a wide-window candidate within a few days at most.

═══════════════════════════════════════════════════════════════════════════
## Open items (lower urgency)
═══════════════════════════════════════════════════════════════════════════

1. **Robinhood pickle proactive refresh.** Restarting trading-corp.service requires Robinhood device-approval MFA when the pickle is expired. Last restart took 4 stop+start attempts (~55 min) before MFA cleared. **Before any future trading-corp restart, refresh `/home/azureuser/robinhood.pickle` out-of-band** (log in from a local machine that has Robinhood credentials, capture the new pickle, scp it up). Worth investigating whether the Robinhood adapter has an SMS-fallback MFA option to remove the device-approval dependency entirely.

2. **Dashboard mismatch/stale tile paths are unit-tested only.** No live observation possible without an actual mismatch or a >26h-stale reconciler. If a real mismatch event occurs, watch the dashboard for the red-alarm rendering and confirm the per-mismatch list expands correctly.

3. **`tp_plan_version` field naming inconsistency.** Dashboard still uses both `tp_plan_version` and (legacy) `tp_plan` checks in places. Low priority.

4. **Phase 4 (`BitunixBroker.place_order` live REST)** still gated on positive-EV paper data over the 60-day window starting 2026-05-20 + `auto_execute_caps` harmonization. Don't pull this forward without explicit Board sign-off.

═══════════════════════════════════════════════════════════════════════════
## Parallel-session awareness (NOT bitunix work, but affects shared files)
═══════════════════════════════════════════════════════════════════════════

The **kalshi_crypto / kalshi_weather sessions** committed a `data.py` change on `origin/main` (`e99582d feat(dashboard): advance DASHBOARD_RT_CUTOFFS to per-strategy logic-change dates`) that is NOT yet deployed to prod. Current state:

- origin/main `data.py` blob: `7722dd808159fd4f81b142cf1bd8c5a4` (has DASHBOARD_RT_CUTOFFS advance)
- prod `data.py` md5: `734c86e30f61113a689e7f0e61ccdaf2` (has my tile changes, NOT the cutoff advance)

**That mismatch is the kalshi sessions' Phase B work-to-do, not bitunix's.** When they deploy, they will surgical-patch the DASHBOARD_RT_CUTOFFS dict — not whole-file overwrite — so my tile changes in `data.py` stay intact. If you see prod's `data.py` md5 change to `7722dd80…`, that's their deploy landing, not a regression.

**Don't touch:**
- `config/strategies.yaml`, `BACKLOG.md`, `runbooks/deploy_log.md` kalshi entries
- `trading_corp/web/kalshi_crypto_vol_v2.py`, `pm_vol_v2_block.html`, `pm_dashboard_body.html`
- `trading_corp/agents/strategies/_weather_math.py`, `kalshi_*_arb.py`, `crypto_*_provider.py`
- Any `runbooks/session_start_2026_05_21_kalshi_*.md` file — those belong to the kalshi threads.

═══════════════════════════════════════════════════════════════════════════
## Things to NOT do without explicit approval
═══════════════════════════════════════════════════════════════════════════

(Standard BitUnix do-nots, restated:)

- **Don't flip `bitunix_futures.auto_execute: false → true`.** Paper data is the gate.
- **Don't flip `htf_gate.mode: enforce → shadow`** or `trade_plan.enabled: true → false`.
- **Don't loosen the reconciler's match tolerance** (currently exact-match on result string + ±0.05R on R). A new mismatch is the reconciler doing its job — investigate, don't paper over.
- **Don't shorten the 60-day clock** without a decision memo. Clock-end ≈ 2026-07-19.
- **Don't deploy changes to `paper_trade_replay.py` without re-running the audit reconciler** post-deploy.
- **Don't disable `tc-audit-reality.timer`** without a memo explaining why daily reality-check is no longer needed.
- **Don't let any automated path set `audit_corrected=true`.** This flag is human-review-only — the dashboard's red-alarm rendering depends on it.
- **Don't touch the corrected-row data** (`audit_corrected=true` extra_json on the 2 historical trades). Kept for traceability.

═══════════════════════════════════════════════════════════════════════════
## Environment snapshot at session end (2026-05-20 ~23:30 UTC)
═══════════════════════════════════════════════════════════════════════════

- **Prod (`tc-prod-vm`):** trading-corp.service active, PID 912408 (restart from this session's deploy), uvicorn :8000, `BitUnix observer wiring: scoring=True, pa_enabled=True, htf_gate_mode=enforce, htf_regime_enabled=True, trade_plan_active=True`. `tc-audit-reality.timer` active (waiting), next fire 2026-05-21 06:03 UTC + jitter.
- **Prod md5s (bitunix files):** `paper_trade_replay.py` `49c9735f6ee1fd2c74ed85f1e74b3421` (v2 lifecycle fix, 5/20 10:37 UTC). `audit_reality_reconciler.py` `b203f791514cd43ce4b668d853bfd250` (with `_persist_summary`, 5/20 22:51 UTC). `bitunix_trade_plan_panel.html` `7cf29147ecdcfc9ae371dfb5ecbb021a`. `bitunix_score_panel.html` `9d30d6bad06233bf5f68bb1040ac06b3`. `data.py` `734c86e30f61113a689e7f0e61ccdaf2` (kalshi sessions will advance to `7722dd80…` for the cutoff change).
- **Backup tag on prod (rollback for the tile):** `pre-dashboard-tile-20260520`. Full rollback recipe in `runbooks/deploy_log.md` 2026-05-20 22:51 UTC entry.
- **Local:** HEAD = `origin/main` = `9375c68` (clean). Working tree has only kalshi WIP (`M runbooks/session_start_2026_05_21_kalshi_post_deploy.md`, `?? docs/Deployment notes.txt`) — not bitunix's to commit.
- **First fully-unattended reconciler fire:** 2026-05-21 06:03 UTC + jitter (THE next milestone).

Pickup with the PRIORITY 1 timer-check (after 06:13 UTC) before anything else bitunix-shaped.
