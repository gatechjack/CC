# Next-session pickup prompt — post-B7+B9 reconciler deploy (2026-05-22 01:50 UTC)

*Written 2026-05-22 ~02:10 UTC at the end of the BitUnix reconciler-hardening session. Canonical pickup point for the next BitUnix session; the kalshi_crypto and kalshi_weather threads have their own pickup runbooks.*

---

Paste this into a fresh Claude Code session in `C:/Users/AA Incorporado/cc`:

---

Resuming the BitUnix work thread. Three parallel sessions: kalshi_crypto, kalshi_weather, bitunix=this one; [[feedback-parallel-sessions-stop-and-discuss]] applies. Read `runbooks/session_start_2026_05_22_post_b7_b9.md` for the full pickup. New memories loaded this thread: [[feedback-sqlite-iso-datetime-comparison]] (silent false-positive on raw-string ts comparisons) and the updated [[bitunix-paper-clock]] (reconciler now genuinely sound).

Headline state at session-end:

- **Reconciler hardened: B7 + B9 SHIPPED.** Commits `3713ace` (B7 no_bars guard) + `4fe56de` (B9 inverted-window normalization) + `43ae986` (docs). All on `origin/main`. Prod md5 of `scripts/audit_reality_reconciler.py` is `071503b76cb722be2ca3e5621d847adc`. Backup tag `pre-b7-b9-reconciler-20260522` on prod. Manual post-deploy fire: **5/5 match** including `2942ff8e` reality-verified at R=0.7955 (236 bars walked, filled_legs=[tp1,tp2]). Immune system is now genuinely sound — not safe-by-outcome-coincidence.
- **First deploy attempt (B7-alone) rolled back at 01:06 UTC.** Surfaced the inverted-window edge case on `2942ff8e` (ts=14:00:12 > result_ts=14:00:00) which made the literal SQL window empty. B9 added the normalization branch (`[result_ts, ts + max_hold_seconds]` for inverted trades). Second deploy at 01:50 UTC clean.
- **60-day clock unchanged.** Clock-end still ~2026-07-19. Funding-units fix and B7+B9 are both display/audit-correctness only; no trade decisions affected, no re-baseline. `gate_mode="off"` throughout.
- **200-bar pagination fix still unexercised** against its real failure mode. Trades closing so far are short-lifetime (1-4 bars on losses, multi-tick lifecycle on the one win). PRIORITY 1 is still waiting for a wide-window (>10h / >200 bar) candidate.
- **Local HEAD = origin/main = `43ae986` (clean).** Working tree has `?? docs/Deployment notes.txt` — untracked, kalshi parallel-session artifact, NOT bitunix's to commit.

Pickup with the PRIORITY 2 check first (the 06:05 UTC unattended fire is the first under B7+B9 — that's the load-bearing demonstration), THEN PRIORITY 1.

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 2 — First unattended reconciler fire under B7+B9 (NEW load-bearing)
═══════════════════════════════════════════════════════════════════════════

This is the first scheduled fire after the reconciler hardening shipped. The manual fire at 2026-05-22 01:50 UTC was clean (5/5 match), but **the scheduled fire under systemd-timer execution context is a separate verification surface** — confirms the timer's environment (User=azureuser, WorkingDirectory, venv-python) runs the new code identically.

Next fire: **2026-05-22 06:05:30 UTC + jitter**.

After 06:15 UTC, run the post-fire check (use `julianday` to avoid the [[feedback-sqlite-iso-datetime-comparison]] gotcha):

```bash
az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm \
  --command-id RunShellScript \
  --scripts 'sqlite3 -header /home/azureuser/trading_corp/data/trading_corp.db "
    SELECT id, ts, json_extract(payload_json,\"\$.status\") AS status,
           json_extract(payload_json,\"\$.n_matches\") AS n_matches,
           json_extract(payload_json,\"\$.n_total\") AS n_total
    FROM audit_event WHERE kind=\"audit_reality_run\" ORDER BY id DESC LIMIT 3;
    SELECT id, ts, json_extract(payload_json,\"\$.order_id\") AS order_id
    FROM audit_event WHERE kind=\"audit_reality_no_bars\"
      AND julianday(ts) >= julianday(\"now\", \"-2 hours\");"'
```

**Expected:** status=`match`, n_matches=5, n_total=5. Zero new `audit_reality_no_bars` rows from the fire. If new v2 trades closed between 01:50 and 06:05 UTC, n_total may be higher — but all should still be `match` unless a new trade has a genuine R disagreement.

**If status is NOT `match`:** stop and investigate. New mismatches under B7+B9 are real signal (not the inverted-window false alarm anymore). Pull the per-trade verdicts via the reconciler's stdout pattern (manual fire over `az vm run-command`).

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 1 — Watch for a wide-window v2 trade
═══════════════════════════════════════════════════════════════════════════

The 200-bar fetcher fix is verified-not-broken but NOT verified-working against its actual failure mode. Need a v2 trade with >200 bars (3m × 200 = 600 minutes = 10h window minimum) — ideally one that progresses past TP1 in real price action.

Current closed v2 population (5 trades, all reconciled clean under B7+B9):
- `35aa49c9` (2026-05-18, win 0.838R corrected, 266 bars)
- `a467e316` (2026-05-18, loss -1.0R corrected, 266 bars)
- `ef6e6697` (2026-05-20, loss -1.0R, 2 bars)
- `ab190eb8` (2026-05-21, loss -1.0R, 1 bar)
- `2942ff8e` (2026-05-21, **win 0.7955R**, 236 bars walked — first multi-TP win, reality-verified)

Watch-queries (use julianday for time filters, NOT raw `>=` against datetime('now')):

```sql
-- Wide-window v2 trades since dashboard-tile deploy
SELECT order_id, ts, result, actual_r_multiple, bars_to_resolution,
       json_extract(extra_json,'$.filled_legs') AS filled_legs
FROM paper_trade_record
WHERE division='bitunix_futures'
  AND ts >= '2026-05-20T22:51:00+00:00'
  AND json_extract(extra_json,'$.tp_plan_version') = 'v2'
  AND (bars_to_resolution > 200
       OR (julianday(result_ts) - julianday(ts)) * 1440 > 600);

-- All v2 trades + lifetime distribution
SELECT order_id, ts, result_ts, result, actual_r_multiple, bars_to_resolution,
       ROUND((julianday(result_ts) - julianday(ts)) * 1440, 2) AS lifetime_minutes,
       json_extract(extra_json,'$.filled_legs') AS filled_legs
FROM paper_trade_record
WHERE division='bitunix_futures'
  AND ts >= '2026-05-20T22:51:00+00:00'
  AND json_extract(extra_json,'$.tp_plan_version') = 'v2'
ORDER BY ts;

-- Open positions right now
SELECT order_id, ts,
       ROUND((julianday('now') - julianday(ts)) * 1440, 2) AS alive_minutes
FROM paper_trade_record
WHERE division='bitunix_futures' AND result IS NULL
  AND json_extract(extra_json,'$.tp_plan_version') = 'v2';
```

Historical fire rate ~0.7/day; flag if a trade opens and is still alive past 3h (~60 bars) — it's on track to exercise the pagination path.

**Reminder on `2942ff8e`-shape trades:** if a new multi-tick lifecycle trade closes, expect `result_ts < ts` (the source-side residue from B5 — see backlog). B9 handles it correctly at the reconciler now; the lifetime_minutes display will show a negative value until B5's source-side fix lands.

═══════════════════════════════════════════════════════════════════════════
## Backlog items (post-B7+B9)
═══════════════════════════════════════════════════════════════════════════

See `BACKLOG.md` § "BitUnix — post-funding diagnostics (2026-05-21)" for full text. Status summary:

- **B7 — ✅ DONE 2026-05-22 01:50 UTC.** Reconciler `bar_count > 0` guard. Shipped + verified non-regressive.
- **B9 — ✅ DONE 2026-05-22 01:50 UTC.** Inverted-window normalization. Required for B7's deploy.
- **B5 — cosmetic + residue.** `bars_to_resolution` semantics misleading (unchanged scope). Adjacent `result_ts < ts` source-side residue documented — B9 closes the reconciler-side blast radius; source fix would be in `paper_trade_replay.py` (`result_ts = max(ts, bar_ts_iso)` or similar). Runbook gate applies (paper_trade_replay.py changes require post-deploy reconciler re-run). Not urgent.
- **B6 — LOW.** Reconciler API-refetch path. Downgraded — archiver coverage continuous.
- **B8 — LOW (latent).** Reconciler does not filter on symbol. BTC-only today.

Backlog items B1-B4 from the prior pickup remain (see `runbooks/session_start_2026_05_22_bitunix_post_funding_fix.md`):
- B1 — funding_extreme_pct_per_8h duplicate source-of-truth (~30 min config-only fix)
- B2 — tp_plan_version vs tp_plan naming inconsistency in dashboard (~45 min)
- B3 — dashboard mismatch/stale tile paths unit-tested only (dormant)
- B4 — Phase 4 BitunixBroker.place_order live REST (Board-gated, clock-end ~2026-07-19)

═══════════════════════════════════════════════════════════════════════════
## Parallel-session awareness
═══════════════════════════════════════════════════════════════════════════

**Don't touch (kalshi/polymarket territory):**
- `config/strategies.yaml`, `runbooks/deploy_log.md` kalshi/polymarket entries
- `trading_corp/web/kalshi_crypto_vol_v2.py`, `pm_vol_v2_block.html`, `pm_dashboard_body.html`
- `trading_corp/agents/strategies/_weather_math.py`, `kalshi_*_arb.py`, `crypto_*_provider.py`, `polymarket_arbitrage.py`
- Any `runbooks/session_start_2026_05_*_kalshi_*.md` or `*_polymarket_*.md` file
- `docs/Deployment notes.txt` — standing kalshi artifact (untracked; not bitunix's to commit)

═══════════════════════════════════════════════════════════════════════════
## Things to NOT do without explicit approval
═══════════════════════════════════════════════════════════════════════════

(Standard BitUnix do-nots, restated for the new session:)

- **Don't flip `bitunix_futures.auto_execute: false → true`.** Paper data is the gate. Clock-end ~2026-07-19.
- **Don't flip `htf_gate.mode: enforce → shadow`** or `trade_plan.enabled: true → false`.
- **Don't flip `htf_gate.gate_mode: off → enforce` on funding.** Gate is correct (units fix landed) but 0.05%/8h threshold is tight; needs Board memo about FP rate.
- **Don't change `funding_extreme_pct_per_8h` from `0.05`** without checking both source-of-truth locations (B1).
- **Don't loosen the reconciler's match tolerance** (currently exact-match on result string + ±0.05R on R).
- **Don't shorten the 60-day clock** without a decision memo.
- **Don't deploy changes to `paper_trade_replay.py` without re-running the audit reconciler** post-deploy. (This applies to any future B5 source-side fix attempts.)
- **Don't disable `tc-audit-reality.timer`** without a memo explaining why daily reality-check is no longer needed.
- **Don't let any automated path set `audit_corrected=true`.** Human-review-only flag.
- **Don't touch the corrected-row data** (the 2 historical `audit_corrected=true` trades — `35aa49c9` and `a467e316`).
- **Don't write raw-string `WHERE ts >= datetime('now',...)` against `audit_event.ts`** — false-positives. Use `julianday()` or `strftime('%Y-%m-%dT%H:%M:%S', 'now', ...)`. See [[feedback-sqlite-iso-datetime-comparison]].

═══════════════════════════════════════════════════════════════════════════
## Environment snapshot at session end (2026-05-22 ~02:00 UTC)
═══════════════════════════════════════════════════════════════════════════

- **Local HEAD = origin/main = `43ae986`** (in sync; pushed clean). Working tree: only `?? docs/Deployment notes.txt` (kalshi).
- **Prod (`tc-prod-vm`, rg `rg-shared-prod`):** `trading-corp.service` active. Reconciler md5 = `071503b76cb722be2ca3e5621d847adc` (= 4fe56de blob). `tc-audit-reality.timer` active, next fire 2026-05-22 06:05:30 UTC.
- **Backup tag on prod:** `pre-b7-b9-reconciler-20260522` (single file at `scripts/audit_reality_reconciler.py.pre-b7-b9-reconciler-20260522`). Rollback recipe in `runbooks/deploy_log.md` 2026-05-22 01:50 UTC entry.
- **Audit event landmark IDs from this session:** `461531` (audit_reality_no_bars from yesterday's rolled-back deploy, kept as append-only history) — `463270` (audit_reality_run from the successful 2026-05-22 01:50 UTC manual fire, status=match, n_matches=5).
- **Recurring push-miss diagnostic:** 2 missed pushes this session, both confirmed by user as "i did not push." Push config is healthy (push.default unset → "simple", main tracks origin/main, single remote, no fork divergence, zero failed-push log entries). The miss is commit-but-no-push at the user terminal — flag it again next session if it recurs.
- **Memories updated this session:**
  - `project_bitunix_paper_clock.md` — B7+B9 shipped + 2942ff8e reality-verified
  - `feedback_sqlite_iso_datetime_comparison.md` — new feedback memory for the verifier-SQL gotcha caught during deploy verification

**Next milestones:**
1. **2026-05-22 06:05 UTC reconciler fire** — first scheduled fire under B7+B9 (PRIORITY 2).
2. **Wide-window v2 trade** — open-ended, market-dependent (PRIORITY 1).
3. **60-day clock end ~2026-07-19** — Phase 4 unlock candidate, not before.

Pickup with PRIORITY 2 (after 06:15 UTC) first, then PRIORITY 1 watch-queries.
