# Next-session pickup prompt (2026-05-21) — BitUnix v2 fix verification window

*Written 2026-05-20 ~10:55 UTC at end of the BitUnix v2 silent-logging-bug-fix session. This is the canonical pickup point for the BitUnix work thread; parallel sessions (kalshi vol-v2, etc.) have their own threads with separate state in their working-tree files.*

---

Paste this into a fresh Claude Code session in `C:/Users/AA Incorporado/cc`:

---

Resuming from 2026-05-20 session that landed and deployed the BitUnix v2 lifecycle silent-logging fix. Read the EOS summary below first. Two canonical artifacts cover the work: `reports/bitunix_v2_fix_2026-05-20.md` (the fix + reconciler) and `runbooks/deploy_log.md` 2026-05-20 10:37 UTC entry (what's running on prod right now).

## What landed in the prior session

**8 commits, all pushed to `origin/main`** (newest first):
```
3cbba52 deploy: bitunix v2 lifecycle fix + reconciler + daily systemd timer (prod 2026-05-20 10:37 UTC)
fd26e8c docs+data: v2 lifecycle fix report + correction notices on prior reports + trade re-tag script
3021c03 feat(scripts): audit-vs-reality reconciler for closed v2 paper trades
6c1e48b fix(bitunix): paginate kline fetcher around 200-bar server cap (silent v2 lifecycle bug)
2002e31 reports: bitunix audit-integrity finding -- silent v2 lifecycle logging failure
f6559ff reports: bitunix confound check + fee-floor diagnostic
504c992 reports: bitunix paper-data review since trade-plan v2
a2be81a backlog: correct stale EOS-snapshot framing — Branch A is committed as 0049889
```

### Headline finding (now resolved)

Trade-plan v2's `_bitunix_kline_fetcher` had been silently truncating bar slices since the v2 flip on 2026-05-17 05:14 UTC. The BitUnix kline endpoint caps responses at 200 bars per call regardless of `limit`; the legacy fetcher's `if len(page) < this_page: break` treated this as end-of-data, returning only the newest 200 minutes of any wider requested window. The v2 multi-leg classifier never observed the early bars where TP fills happened. Result: every v2 trade that traversed TP levels was recorded as a full -1.0R loss.

**Trade #1** (5/18 16:24, `35aa49c9`) hit TP1 on the entry bar and TP2 six minutes later in actual BTC price action (verified against `bitunix_bar_history` 3m bars). True outcome: **win / +0.838R**, filled_legs=[tp1,tp2], final SL at TP1 floor 76,269.87. Recorded: -1.0R loss. **R delta: +1.838R.**

**Trade #2** (5/18 18:30, `a467e316`) genuinely missed TP1 by $3.97 and hit SL. Recorded: -1.0R loss. Reality: -1.0R loss. **Match — coincidentally correct.** (The bug truncated #2's slice identically, but since #2 had no TP touches anywhere in price action, the bug's truncation produced the right outcome.)

### What's now in prod (md5-verified 2026-05-20 ~10:50 UTC)

- `paper_trade_replay.py` md5 `49c9735f6ee1fd2c74ed85f1e74b3421` — fixed fetcher; 200-bar cap correctly handled by paging in ≤200-bar sub-windows forward in time.
- `scripts/audit_reality_reconciler.py` md5 `1a4da6bd4f8190178af4e82b6bcd2198` — daily reality check.
- `/etc/systemd/system/tc-audit-reality.{service,timer}` — daily at 06:00 UTC + jitter, journal-logged, SuccessExitStatus=0 (any mismatch fails the service).
- Service: trading-corp active, pid 860028, post-deploy boot wiring `scoring=True, pa_enabled=True, htf_gate_mode=enforce, htf_regime_enabled=True, trade_plan_active=True`.
- Backup tag (rollback): `paper_trade_replay.py.pre-v2-kline-fix-20260520` (pre-deploy md5 `3510cfbe015d4e092abc37d0a78cab87`).

### Two historical v2 trades — corrected in DB

Re-tagged with `audit_corrected=true` + `corrected_*` fields in `extra_json` (original `result` / `actual_r_multiple` columns preserved for fidelity):

| order_id | recorded | corrected | delta |
|---|---|---|---|
| `35aa49c9-...` | loss / -1.0R | **win / +0.838R**, filled_legs=[tp1,tp2] | +1.838R |
| `a467e316-...` | loss / -1.0R | loss / -1.0R (reconciler-verified correct) | 0 |

Downstream code should prefer `extra_json.corrected_result`/`corrected_r_multiple` when `extra_json.audit_corrected=true`. **Dashboard surfacing is NOT yet done** — operator viewing the Trade Plan v2 panel today still sees the legacy result/R for these two rows.

## 60-day paper-cutover clock

**Start: 2026-05-20 10:37 UTC** (deploy date). **Target end: ~2026-07-19.**

**Choice was Option A (restart) over Option B (keep 5/17 start + count corrected trades).** Reasoning recorded in `~/.claude/.../memory/trading_corp_bitunix_vision.md` and the deploy_log entry: pre-deploy 2 trades are forensic reconstructions, not natively-recorded; cutover decision on the `[1.14, 2.63]` PF prior must run on natively-recorded-correct data; mixing reconstructed and native outcomes in the gate dataset introduces epistemic ambiguity. Cost: ~3 days of elapsed clock + 2 trades of reconstructed sample (uninformative at n=2 anyway).

**Filter to use when reading cutover data:**
```sql
SELECT * FROM paper_trade_record
WHERE division='bitunix_futures'
  AND ts >= '2026-05-20T10:37:00+00:00'
  AND json_extract(extra_json,'$.tp_plan_version') = 'v2'
```

## Read first

1. `reports/bitunix_v2_fix_2026-05-20.md` — the fix + reconciler report; decision summary at bottom.
2. `reports/bitunix_audit_integrity_2026-05-20.md` — the diagnosis report that started this thread.
3. `runbooks/deploy_log.md` § 2026-05-20 10:37 UTC — what's running on prod.
4. Memory (auto-loaded): `trading_corp_bitunix_vision.md` — updated with the clock restart + bug-fix status.
5. `BACKLOG.md` top — for non-BitUnix open items (parallel sessions' state).

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 1 — Observation window (no immediate action)
═══════════════════════════════════════════════════════════════════════════

The fix is deployed. What we need now is **evidence the fix is exercising** — the system must fire a v2 trade and progress through the lifecycle.

**The leading indicators (queryable any time):**

```sql
-- A new v2 trade fired post-deploy
SELECT COUNT(*) FROM paper_trade_record
WHERE division='bitunix_futures'
  AND ts >= '2026-05-20T10:37:00+00:00'
  AND json_extract(extra_json,'$.tp_plan_version') = 'v2';

-- First-ever position_sl_update audit (all-time count was 0 pre-deploy)
SELECT COUNT(*), MIN(ts) FROM audit_event
WHERE kind='position_sl_update' AND ts >= '2026-05-20T10:37:00+00:00';

-- Paper trade with non-empty filled_legs (all-time count was 0 pre-deploy)
SELECT order_id, ts, result, actual_r_multiple,
       json_extract(extra_json,'$.filled_legs')
FROM paper_trade_record
WHERE division='bitunix_futures'
  AND ts >= '2026-05-20T10:37:00+00:00'
  AND json_extract(extra_json,'$.filled_legs') != '[]';
```

**At deploy + 75 min:** 0 v2 trades, 0 position_sl_update audits. Score engine actively producing PREMIUM-sell decisions (4 in the last 4 min before wrap), but PA gate + HTF + trade-plan fee floor are still funneling those down. Fire rate from prior analysis was ~0.7/day; expect ~2-5 v2 fires in the first week.

**Daily reconciler:** `tc-audit-reality.timer` next trigger 2026-05-21 06:03 UTC + jitter. **Check it:** `systemctl status tc-audit-reality.service` post-trigger; `journalctl -u tc-audit-reality.service --since "1 day ago"`. Any mismatch → service fails → visible via `systemctl --failed`.

**Unattended-fire check (after 2026-05-21 06:13 UTC):** `journalctl -u tc-audit-reality.service --since "06:00 today"` — verify timer fired unattended. If no log line, the timer/service wiring has a latent issue.

## PRIORITY 2 — Dashboard surfacing of `audit_corrected` (queued, not started)

Today the Trade Plan v2 dashboard panel reads `result` / `actual_r_multiple` directly from `paper_trade_record`. For audit-corrected rows, it should prefer `extra_json.corrected_result` / `corrected_r_multiple`. This is a small `web/data.py` change (probably `build_bitunix_trade_plan_view`) + a partial template update.

**Scope:** read-only refactor — no schema change, no behavior change to the lifecycle. ~1-2h. Safe to bundle with other dashboard touch-ups if the next session is working in that area.

**Don't do this without first verifying:** which view/template renders the closed-trade history (grep for `paper_trade_record` reads in `web/data.py` and templates); ensure the corrected-vs-recorded distinction is rendered (e.g., a "✓ corrected" badge with the corrected R alongside the original).

## Other open items (BitUnix-specific, ordered)

1. **Watch for first post-deploy v2 fire.** If/when one fires and progresses past TP1, that's first-ever proof the lifecycle path is exercising live. The reconciler will catch any new audit-vs-reality mismatch the next morning.
2. **`tp_plan_version` field naming inconsistency.** Today the dashboard uses both `tp_plan_version` and (legacy) `tp_plan` checks in various places — confirm consistent. Low priority.
3. **Phase 4 (`BitunixBroker.place_order` live REST)** still gated on positive-EV paper data over the new 60-day window + `auto_execute_caps` harmonization. Don't pull this forward without explicit Board sign-off.
4. **Backlog from prior session (still valid):** kalshi_structure_arb backtest pending Backtester approval; IC v1 5-file deconfliction pending parallel-session commits (see `runbooks/session_start_2026_05_19_ic_v1.md`).

## Things to NOT do without explicit approval

(Standard BitUnix do-nots, plus this session's additions:)

- **Don't flip `bitunix_futures.auto_execute: false → true`.** Paper data is the gate.
- **Don't flip `htf_gate.mode: enforce → shadow`** or `trade_plan.enabled: true → false`. Both are load-bearing.
- **Don't loosen the reconciler's match tolerance** (currently exact-match on result string + ±0.05R on R). If a new mismatch arrives, INVESTIGATE — that's the reconciler doing its job.
- **Don't shorten the 60-day clock** by reverting Option A → Option B without a fresh decision memo recording why. The reconstructed-vs-native distinction is the epistemic reason.
- **Don't deploy any further changes that touch `paper_trade_replay.py` without re-running the audit reconciler** post-deploy to verify the v2 lifecycle still resolves correctly.
- **Don't disable `tc-audit-reality.timer`** without a memo explaining why the daily reality check is no longer needed.
- **Don't touch the corrected-row data** (the `audit_corrected=true` extra_json fields on the 2 closed trades) — those are the historical record of the correction, kept for traceability.
- (All prior do-nots still apply: don't paper over negative findings; don't deploy via `patch -p1` over `routes.py` without CRLF normalization; don't deploy a strategy without Backtester approval except IC v1.)

## Environment notes

- **Prod md5s (verified at session end 2026-05-20 ~10:50 UTC):**
  - `paper_trade_replay.py` = `49c9735f6ee1fd2c74ed85f1e74b3421` (= local LF md5).
  - `audit_reality_reconciler.py` = `1a4da6bd4f8190178af4e82b6bcd2198`.
- **Rollback recipe** (single op, captured in deploy_log § 2026-05-20 10:37 UTC):
  ```bash
  ssh azureuser@trading.jacksumner.com "
  TAG=pre-v2-kline-fix-20260520; BASE=/home/azureuser/trading_corp; \
  mv \$BASE/trading_corp/agents/paper_trade_replay.py.\$TAG \$BASE/trading_corp/agents/paper_trade_replay.py; \
  rm -f \$BASE/scripts/audit_reality_reconciler.py; \
  systemctl disable --now tc-audit-reality.timer tc-audit-reality.service; \
  rm -f /etc/systemd/system/tc-audit-reality.service /etc/systemd/system/tc-audit-reality.timer; \
  systemctl daemon-reload; \
  sudo systemctl restart trading-corp.service"
  ```
- **Local Python:** `C:\Users\AA Incorporado\AppData\Local\Python\bin\python.exe` via `scripts\run_capped.ps1` wrapper.
- **Working tree at session end:** clean of bitunix changes (all committed + pushed); parallel-session WIP remains untouched (BACKLOG.md, config/strategies.yaml, kalshi files, runbooks/deploy_log.md kalshi-vol-v2 hunk, vol_v2 paper artifacts). None of that is mine to commit.

## Lessons recorded (memory updates this session)

- `trading_corp_bitunix_vision.md` — frontmatter updated with v2 fix status + new "60-day paper-cutover clock" section.
- New reference memory recommended (write next session if not done): `bitunix-kline-200-bar-cap.md` — captures the API quirk so future BitUnix work doesn't re-discover it.

## Honest assessment

The fix is right. The deploy is verified. The reconciler is wired. The clock is restarted on clean evidence. **But n=0 v2 trades post-deploy means we haven't yet proven the fix exercises on a real trade.** The leading indicator (a non-zero `position_sl_update` audit count) is the next milestone — likely 1-3 days out at the historical fire rate.

The bigger lesson is methodological: audit self-consistency was never proof; the reconciler is. Every future "infra healthy" claim should be backed by a reality-reconciliation check against an independent source. The reconciler timer is the durable form of that lesson. Don't break it.

Pickup with `systemctl status tc-audit-reality.service` + the leading-indicator SQL above before doing anything bitunix-shaped.
