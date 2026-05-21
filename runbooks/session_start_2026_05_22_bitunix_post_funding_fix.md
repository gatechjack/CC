# Next-session pickup prompt (post-2026-05-21 13:05 UTC) — BitUnix funding-units fix DEPLOYED, observation continuing

*Written 2026-05-21 ~17:30 UTC at end of BitUnix funding-rate-units session. This is the canonical pickup point for the BitUnix work thread; the kalshi_crypto and kalshi_weather threads have their own pickup runbooks.*

---

Paste this into a fresh Claude Code session in `C:/Users/AA Incorporado/cc`:

---

Resuming the BitUnix work thread. Three parallel sessions: kalshi_crypto, kalshi_weather, bitunix=this one; [[feedback-parallel-sessions-stop-and-discuss]] applies. Read `runbooks/session_start_2026_05_22_bitunix_post_funding_fix.md` for the full pickup. **New memory loaded this thread: [[verify-premises-against-ground-truth]]** — apply it, especially when external sources/docs disagree with observed data.

Headline state at session-end:

- **Funding-units fix SHIPPED.** Commit `4f04fa66` (the fix) + `07ed68c` (deploy log) both on `origin/main`. Deployed 2026-05-21 13:05:43 UTC. Prod md5s verified. Dashboard renders `+0.0064%` correctly (was rendering 100× too large; the API returns funding already in percent, the bot wrongly ×100'd it in 3 places). `gate_mode="off"` throughout, so no trade decision was ever affected — display/audit-correctness only. No 60-day clock re-baseline.
- **First fully-unattended `tc-audit-reality.timer` fire CLEAN** at 2026-05-21 06:03:42 UTC (3/3 v2 matches, 0 mismatches). Immune system has now demonstrated it can self-diagnose silent failures without a human. Next fire: 2026-05-22 06:05:30 UTC — should be clean again (PRIORITY 2 below).
- **The 200-bar pagination fix STILL UNEXERCISED against its real failure mode.** Only 1 v2 trade has closed post-tile-deploy (`ab190eb8` 2026-05-21 12:30 UTC, lifetime 4 bars / ~3 min, loss -1.0R, filled_legs=[]). Watch continues (PRIORITY 1).
- **Kalshi Phase B landed.** Prod `data.py` md5 advanced from `734c86e30f61113a689e7f0e61ccdaf2` → `7722dd808159fd4f81b142cf1bd8c5a4`. Their surgical-patch design held — bitunix tile changes intact. No action.

Pickup with the PRIORITY 1 watch-queries (see below) before anything else bitunix-shaped.

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 1 — Watch for a wide-window v2 trade
═══════════════════════════════════════════════════════════════════════════

**The 200-bar fetcher fix is verified-not-broken but NOT verified-working against its actual failure mode.** Need a v2 trade with >200 bars (3m × 200 = 600 minutes = 10h window minimum) — ideally one that progresses past TP1 in real price action, since the pre-fix bug would have truncated the early bars.

Run-anytime queries (prod access via `az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm`):

```sql
-- Wide-window v2 trades since the dashboard-tile deploy (>10h lifetime or 200+ bars walked)
SELECT order_id, ts, result, actual_r_multiple, bars_to_resolution,
       json_extract(extra_json,'$.filled_legs') AS filled_legs
FROM paper_trade_record
WHERE division='bitunix_futures'
  AND ts >= '2026-05-20T22:51:00+00:00'
  AND json_extract(extra_json,'$.tp_plan_version') = 'v2'
  AND (bars_to_resolution > 200 OR (julianday(result_ts) - julianday(ts)) * 1440 > 600);

-- All v2 trades + lifetime distribution (so you can see what's happening)
SELECT order_id, ts, result_ts, result, actual_r_multiple, bars_to_resolution,
       (julianday(result_ts) - julianday(ts)) * 1440 AS lifetime_minutes,
       json_extract(extra_json,'$.filled_legs') AS filled_legs
FROM paper_trade_record
WHERE division='bitunix_futures'
  AND ts >= '2026-05-20T22:51:00+00:00'
  AND json_extract(extra_json,'$.tp_plan_version') = 'v2'
ORDER BY ts;

-- First position_sl_update audit post-tile-deploy
SELECT COUNT(*) AS n_sl_updates, MIN(ts) AS first_sl_update
FROM audit_event WHERE kind='position_sl_update'
  AND ts >= '2026-05-20T22:51:00+00:00';

-- Open positions right now (if any are alive and trending wide-window-ward)
SELECT order_id, ts, (julianday('now') - julianday(ts)) * 1440 AS alive_minutes
FROM paper_trade_record
WHERE division='bitunix_futures' AND result IS NULL
  AND json_extract(extra_json,'$.tp_plan_version') = 'v2';
```

Historical fire rate is ~0.7/day; expect a wide-window candidate within a few days at most. Flag if a trade opens and is still alive past 3h (~60 bars) — it's on track to exercise the path.

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 2 — Confirm reconciler unattended-fire repeatability
═══════════════════════════════════════════════════════════════════════════

The first unattended fire was clean (2026-05-21 06:03:42 UTC). Repeatability matters — one clean run isn't a steady state. Next fire **2026-05-22 06:05:30 UTC + jitter**.

After 06:15 UTC, verify:

```bash
az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm \
  --command-id RunShellScript \
  --scripts 'journalctl -u tc-audit-reality.service --since "06:00 today" --no-pager; systemctl list-timers tc-audit-reality.timer --no-pager'
```

```sql
-- New audit_reality_run row from the 2026-05-22 fire
SELECT id, ts, json_extract(payload_json,'$.status') AS status,
       json_extract(payload_json,'$.n_matches') AS n_matches,
       json_extract(payload_json,'$.n_total') AS n_total
FROM audit_event WHERE kind='audit_reality_run' ORDER BY id DESC LIMIT 3;
```

Want: a clean row dated 2026-05-22 06:05-ish UTC, status=`match`, no mismatches. If timer fails to fire or service exits non-zero — investigate before anything else.

═══════════════════════════════════════════════════════════════════════════
## Backlog items (pick from these when priorities are quiet)
═══════════════════════════════════════════════════════════════════════════

Ordered roughly by leverage; none are urgent.

### B1. `funding_extreme_pct_per_8h` duplicate source-of-truth — latent drift risk (NEW from funding-rate session)

The funding-extreme threshold is defined in two places:
- `config/strategies.yaml:1270`
- `trading_corp/agents/strategies/bitunix_htf_regime.py:244` (HTFRegimeConfig.defaults() fallback)

Currently in sync at `0.05`. Loader at `bitunix_htf_regime.py:270` reads YAML if present, falls back to the hardcoded default. If someone changes one and not the other, drift is silent.

**Fix shape:** make the Python default `None` and require the YAML; or wire the YAML through a `@property` so the Python side can't carry a literal. Either way, one source of truth. ~30 min, low risk, no deploy needed (config-only change with a test).

### B2. `tp_plan_version` field naming inconsistency

Dashboard still uses both `tp_plan_version` and (legacy) `tp_plan` checks in places. Cleanup: pick one name, migrate the other. Low priority — works fine today, just messy. Grep `cc/trading_corp/web/` for both names and reconcile. ~45 min, test-covered.

### B3. Dashboard mismatch/stale tile paths — unit-tested only

The reconciler-state tile's red-alarm rendering for actual mismatches and the amber-stale path (>26h since last fire) are unit-tested only. No live observation possible without a real failure. **Don't manufacture one** — just remember to watch for the red render the first time a real mismatch event happens. If it doesn't render right, that's a paper-mode-only display bug, not a trade-outcome bug. Lower-priority dormant item.

### B4. Phase 4 — `BitunixBroker.place_order` live REST

Still gated on:
- Positive-EV paper data over the 60-day window starting 2026-05-20 (clock-end ≈ **2026-07-19**)
- `auto_execute_caps` harmonization between webhook path and LangGraph path (CLAUDE.md § 1 sharp edge)

**Don't pull this forward without explicit Board sign-off.** Listed for visibility only.

═══════════════════════════════════════════════════════════════════════════
## Parallel-session awareness
═══════════════════════════════════════════════════════════════════════════

Kalshi Phase B has now landed (prod `data.py` md5 = `7722dd808159fd4f81b142cf1bd8c5a4`). Their surgical-patch design preserved the bitunix tile changes; nothing to investigate.

**Don't touch (still kalshi territory):**
- `config/strategies.yaml`, `BACKLOG.md`, `runbooks/deploy_log.md` kalshi entries
- `trading_corp/web/kalshi_crypto_vol_v2.py`, `pm_vol_v2_block.html`, `pm_dashboard_body.html`
- `trading_corp/agents/strategies/_weather_math.py`, `kalshi_*_arb.py`, `crypto_*_provider.py`
- Any `runbooks/session_start_2026_05_*_kalshi_*.md` file

═══════════════════════════════════════════════════════════════════════════
## Things to NOT do without explicit approval
═══════════════════════════════════════════════════════════════════════════

(Standard BitUnix do-nots, restated for the new session:)

- **Don't flip `bitunix_futures.auto_execute: false → true`.** Paper data is the gate. Clock-end ≈ 2026-07-19.
- **Don't flip `htf_gate.mode: enforce → shadow`** or `trade_plan.enabled: true → false`.
- **Don't flip `htf_gate.gate_mode: off → enforce` on funding.** The funding gate is correct now (units fix landed), but the threshold is at `0.05%/8h` which is at the tight end of industry-standard "extreme." Don't enable without a Board memo about expected false-positive rate.
- **Don't change `funding_extreme_pct_per_8h` from `0.05`** without checking both source-of-truth locations (see B1).
- **Don't loosen the reconciler's match tolerance** (currently exact-match on result string + ±0.05R on R).
- **Don't shorten the 60-day clock** without a decision memo.
- **Don't deploy changes to `paper_trade_replay.py` without re-running the audit reconciler** post-deploy.
- **Don't disable `tc-audit-reality.timer`** without a memo explaining why daily reality-check is no longer needed.
- **Don't let any automated path set `audit_corrected=true`.** Human-review-only flag.
- **Don't touch the corrected-row data** (the 2 historical `audit_corrected=true` trades).

═══════════════════════════════════════════════════════════════════════════
## Environment snapshot at session end (2026-05-21 ~17:30 UTC)
═══════════════════════════════════════════════════════════════════════════

- **Prod (`tc-prod-vm`, rg `rg-shared-prod`):** `trading-corp.service` active since 2026-05-21 13:05:43 UTC (PID 978296 at restart; may have rolled if any subsequent restart happened). Healthz `{"status":"ok","mode":"PAPER"}`. `tc-audit-reality.timer` active, next fire 2026-05-22 06:05:30 UTC + jitter.
- **Prod md5s (bitunix files, post funding-units fix, all match commit `4f04fa66`):**
  - `bitunix_htf_regime.py` `e0dbf34a7b43ee628eb1aa269849cc26`
  - `bitunix_htf_panel.html` `3c886fb0950f936a61564d4e45c6b47e`
  - `brokers/bitunix.py` `61b406fa218900b15e5f2d2366cc7579`
  - `tests/test_bitunix_htf_regime.py` `c9cf307d6df764105dcccf94c4363e6f`
  - `data.py` `7722dd808159fd4f81b142cf1bd8c5a4` (kalshi Phase B landed — bitunix tile intact)
- **Backup tag on prod (rollback for funding-units fix):** `pre-funding-units-fix-20260521`. Rollback recipe in `runbooks/deploy_log.md` 2026-05-21 13:05 UTC entry.
- **Local: HEAD is 2 commits ahead of `origin/main`** — `07ed68c` (deploy_log entry for funding-units fix, never pushed despite an earlier sub-agent's incorrect report that it had been) + the commit for this runbook itself. `origin/main` HEAD = `90c6901` (polymarket pickup runbook). The funding-units FIX commit `4f04fa66` IS on origin (verified). **Push both local commits when convenient — prod is already on the fix, this is bookkeeping only.** Working tree has `?? docs/Deployment notes.txt` — untracked, not bitunix's to commit.
- **Funding-rate dashboard tile:** rendering `+0.0064%` correctly (BTCUSDT, ~0.0066% live). NOT-extreme. Was rendering 100× too large for the entire prior bitunix runtime; now truthful.
- **Memory added this session:** `feedback_verify_premises_against_ground_truth.md` — when an inherited premise produces a persistent N× discrepancy with observed reality, the premise is the bug; test premises directly before building analysis on them.

**Next milestones:**
1. **Wide-window v2 trade** — open-ended, market-dependent (PRIORITY 1).
2. **2026-05-22 06:05 UTC reconciler fire** — confirm clean repeatability (PRIORITY 2).
3. **60-day clock end ≈ 2026-07-19** — Phase 4 unlock candidate, not before.

Pickup with the PRIORITY 1 watch-queries (after a quick PRIORITY 2 check if it's after 06:15 UTC) before anything else bitunix-shaped.
