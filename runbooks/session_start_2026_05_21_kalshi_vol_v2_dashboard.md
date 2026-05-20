# Next-session pickup prompt — kalshi_crypto vol-v2 dashboard post-deploy

*Written 2026-05-20 ~23:05 UTC at end of the session that shipped the kalshi_crypto vol-v2 paper-validation tile to paper prod. Companion to `session_start_2026_05_21_kalshi_post_deploy.md` (kalshi_weather floor) and `session_start_2026_05_21.md` (BitUnix v2). Three parallel threads ran on this checkout today — see each file for its own pickup.*

---

Paste this into a fresh Claude Code session in `C:/Users/AA Incorporado/CC`:

---

Resuming from 2026-05-20 session that shipped the kalshi_crypto vol-v2 paper-validation tile to paper prod at 22:54:25 UTC (after one rollback at 22:44 UTC for a SARGable-view perf regression — see `runbooks/deploy_log.md` 2026-05-20 22:54 entry for the full rollback-then-fix story). **The tile is live and rendering** — kalshi_crypto partial returns HTTP 200 in ~1.07s; vol-v2 block adds ~460ms over the prior baseline. Live numbers at deploy: post-vol-v2 n=7 / -$1.05 / 71.4%, lifetime n=334 / -$45.94 / 51.5%, post-bucket-guard pre-v2 n=174 / +$20.90, 0 strays, suppressed-fire 0/day so far.

Read `MEMORY.md` and follow [[kalshi-crypto-vol-v2-dashboard-live]] for full state. The two perf lessons from this deploy are [[time-views-on-prod-scale-before-shipping]] and [[julianday-abs-blocks-index-use]] — both worth honoring before the next SQL VIEW design.

## Known hazards (read before touching anything)

- **Three parallel sessions ran on this checkout today.** kalshi_weather floor (11:35 UTC), bitunix v2 lifecycle (10:37) + bitunix dashboard tile (22:51), and this vol-v2 dashboard work (22:54). The pattern that worked under contention: targeted patch via staging (pull-prod → edit-staging → push-staging) with md5-verified untouched-region checkpoints. **Never `scp local data.py → prod`** — local routinely carries another session's uncommitted WIP. If you see a divergent local md5 from prod for `web/data.py`, that's the expected state; CRLF-normalized diff to confirm what's actually different content-wise.
- **Prod files in `trading_corp/web/` are root-owned.** scp from azureuser fails with permission denied on overwrites of existing root-owned files. The pattern that works: scp to `/tmp/<file>` first, then `sudo mv /tmp/<file> <target>` + `sudo chown root:root` + `sudo chmod 644`. New files in the same directory work as azureuser uploads (parent dir is azureuser-owned).
- **Don't run python locally without `scripts\run_capped.ps1`** per CLAUDE.md STOP-AND-READ §6. Crash #9 (2026-05-18) was an unwrapped pytest → 58 GB virtual → BSOD. On prod (Linux, no commit cap), running python directly is fine.
- **Prod is Python 3.10.12.** Don't use 3.11+ stdlib features in prod-targeted scripts. Memorized at [[prod-python-version-3.10]] (from the kalshi_weather session).

## Where it landed

**On prod (live, paper-mode), as root-owned files:**

```
trading_corp/web/data.py                                         prod md5 e7888864c7dbddecf4373c768146df36  (4879 lines; 4 hunks for vol-v2)
trading_corp/web/kalshi_crypto_vol_v2.py                         prod md5 2ab7bb2289333063cf0f05ca14b40540  (NEW; 256 lines)
trading_corp/web/templates/partials/pm_dashboard_body.html       prod md5 2f9365e8cc7b6389d5fb9d39a7d019ba  (1-line additive include at L871)
trading_corp/web/templates/partials/pm_vol_v2_block.html         prod md5 994f474bdc4c6cc3f3223fcfa074c442  (NEW; 75 lines)
```

Plus the SQL VIEW `kalshi_crypto_vol_v2_round_trips` on `data/trading_corp.db` (metadata-only; SARGable BETWEEN form).

Service: trading-corp PID 913665, ExecMainStartTimestamp 2026-05-20 22:54:25 UTC, active+running, paper mode confirmed, auto_execute: false preserved.

**On local (uncommitted, working tree):**

- 4 files match prod content (after CRLF-normalization). Local `data.py` import order now matches prod byte-for-byte after a fix-up at session-end (kalshi_crypto_vol_v2 import on L17, utils.time on L18).
- `runbooks/deploy_log.md` has the 2026-05-20 22:54 UTC entry appended (uncommitted).
- `BACKLOG.md` has a new EOS snapshot block for this session (uncommitted).

**Local committed (`main`):** `a97d1f6` (1 ahead of `origin/main`). The kalshi_weather session's wrap commit. None of today's three sessions' code changes have been committed — full deploy-log lineage in `runbooks/deploy_log.md` covers the prod state. Decision to commit-and-push deferred to whoever wraps the day.

## Forward paper watch — what to look at

The user explicitly built this tile to support forward paper validation, not to auto-decide live-flip. Live-flip is still gated on Board sign-off.

The four numbers to watch (visible on `/prediction-markets/kalshi_crypto`):

1. **Post-vol-v2 PnL trajectory** (currently n=7 / -$1.05). Drift toward the backtester's +$2.37 strictly-comparable number would be a quiet regression in the same_fire subset. Drift away (further negative) tells the opposite story. Sample is still pre-threshold (≥30 was the user's gate); treat the per-class breakdown as directional only.
2. **Suppressed-fire-per-day** (currently 0/day, target ~5/day per the spot-check). This is the **net-new** suppression rate, not the raw cap-fire count (~224/day, 45× the baseline, of which 140/143 are `both_skip` redundant logging).
3. **Strays count** (currently 0). Non-zero would mean a post-cutoff RT didn't join its evaluator audit under the ±2s tolerance. Investigate before the next deploy.
4. **Vol refresh health** (not on the tile; check audit log directly). Earlier spot-check showed 16/16 hourly refreshes all `realized:4032` across all 5 assets. If `fallback_*` shows up for any asset, ccxt can't reach Coinbase from the prod VM — the strategy would silently run on hardcoded ANNUAL_VOLS constants.

## Useful read-only probes (no deploy authority needed)

```bash
# View live tile
ssh azureuser@trading.jacksumner.com "curl -sS http://127.0.0.1:8000/partials/prediction-markets/kalshi_crypto | grep -oE 'Post-vol-v2[^<]*|n = [0-9]+|[0-9]+\\.[0-9]%|same_fire|new_fire|suppressed_fire'"

# Direct counts (read-only)
ssh azureuser@trading.jacksumner.com "sqlite3 -readonly /home/azureuser/trading_corp/data/trading_corp.db \"SELECT vol_v2_era, COUNT(*), printf('%.2f', SUM(realized_pnl)) FROM kalshi_crypto_vol_v2_round_trips WHERE resolved_ts IS NOT NULL GROUP BY vol_v2_era;\""

# Suppressed-fire/day rate
ssh azureuser@trading.jacksumner.com "sqlite3 -readonly /home/azureuser/trading_corp/data/trading_corp.db \"SELECT COUNT(*) AS n, (julianday('now') - julianday('2026-05-20T05:52:09+00:00')) * 24.0 AS hours FROM kalshi_crypto_vol_v2_round_trips WHERE vol_v2_era='post' AND vol_v2_classification='suppressed_fire';\""

# Strays sentinel — should stay 0
ssh azureuser@trading.jacksumner.com "sqlite3 -readonly /home/azureuser/trading_corp/data/trading_corp.db \"SELECT COUNT(*) FROM kalshi_crypto_vol_v2_round_trips WHERE resolved_ts IS NOT NULL AND entry_ts >= '2026-05-20T05:52:09+00:00' AND vol_v2_era='pre';\""
```

## What's NOT done (next-session work, if any)

- **Forward paper watch is ongoing.** No action item until the resolved-RT sample crosses the user's ≥30 gate (currently n=7). At that point, re-pull the four numbers and surface to the user; do not conclude live-flip on your own.
- **Commit + push.** None of today's three sessions committed their feature code; the 1-commit-ahead-of-origin is just the kalshi_weather session-wrap doc commit. Whoever wraps the day should produce three feature commits (kalshi_weather floor call site, bitunix dashboard tile, kalshi_crypto vol-v2 dashboard) — or one consolidated wrap commit covering all three. Prod state is the source of truth via deploy_log; local commits are catching up to prod, not vice versa.
- **`scripts/sql/create_kalshi_crypto_vol_v2_view.sql` is NOT in the repo.** The view was created via inline SSH SQL. If you want a reproducible migration artifact, the DDL string from `kalshi_crypto_vol_v2_view_ddl()` in `web/kalshi_crypto_vol_v2.py` is the canonical source — but it's not exercised by any script yet. Optional cleanup.
- **`tmp/` is still gitignore-free.** Same lint nit as the kalshi_weather pickup. Defer.

## Rollback recipe (full vol-v2 dashboard, leaves the underlying 05:52 vol-v2 strategy ship intact)

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-vol-v2-dashboard-20260520-2200; BASE=/home/azureuser/trading_corp;
sudo cp \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py;
sudo cp \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html.\$TAG \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html;
sudo chown root:root \$BASE/trading_corp/web/data.py \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html;
sudo rm -f \$BASE/trading_corp/web/kalshi_crypto_vol_v2.py \$BASE/trading_corp/web/templates/partials/pm_vol_v2_block.html;
sudo systemctl restart trading-corp.service;
sqlite3 \$BASE/data/trading_corp.db 'DROP VIEW IF EXISTS kalshi_crypto_vol_v2_round_trips;'"
```

The 05:52 UTC vol-v2 strategy ship (realized vol provider + max_divergence_pct cap) is NOT touched by this rollback. To roll that back too, see `runbooks/deploy_log.md` 2026-05-20 05:52 UTC entry.
