# Next-session pickup prompt (2026-05-17)

Paste this into a fresh Claude Code session in `C:/Users/AA Incorporado/cc`:

---

Resuming from 2026-05-17 ~04:00 UTC wrap. Last session shipped five
clean deploys (paper-mode, all reversible):

  1. **kalshi_weather bucket-guard + date-parse fix** (2026-05-16 19:18 UTC,
     commit `d854dcf`). Bug A (σ-vs-bucket mismatch) + Bug B (off-by-one-day
     in `_parse_target_time`). Pre-fix: 61 RTs / 9.8% WR / -$374.
  2. **kalshi_crypto bucket-guard fix** (2026-05-16 19:37 UTC, same commit).
     Bug A only — crypto doesn't have the date-parse issue. Pre-fix: 91 RTs
     / 11.0% WR / -$59.
  3. **Dashboard cutoff filter** (2026-05-17 02:49 UTC, commit `bf1ae7e`).
     `DASHBOARD_RT_CUTOFFS` dict in `web/data.py` filters pre-fix kalshi RTs
     out of tile + history aggregates without deleting them. "since
     2026-05-16 · current logic only" badge under Win Rate on per-division
     pages.
  4. **target_iso audit field for kalshi_weather** (2026-05-17 03:09 UTC,
     commit `1e2b399`). Lets us verify the date-parse fix on the wire when
     `would_have_placed` rows land. Distinct from `expires_at`.
  5. **PCT stale-pruner cron** (2026-05-17 03:38 UTC, commit `335ecc2`).
     Daily 11:30 UTC systemd timer; deletes `polymarket_copy_trader/would_have_placed`
     audit rows older than 24h with no round-trip pairing. Predicate matches
     the 2026-05-16 03:29 UTC one-shot exactly. Self-audited via `pct_stale_prune`
     event. Dry-run at 03:41 UTC showed 454 candidates queued for first fire.

Parallel session also shipped `72bbbe4` (bitunix: deferred-fire PA mechanism)
which I did not touch — re-read that commit before resuming any BitUnix work.

Service is healthy. Local git tree clean. Prod md5 matches local on all five
directly-touched files; `main.py` has known drift (3 patch markers verified).

Read first (in this order):
  1. **runbooks/deploy_log.md** — top 5 entries are last session's deploys.
  2. **BACKLOG.md** — top section "END-OF-SESSION SNAPSHOT — 2026-05-17 03:55 UTC".
  3. **Memory** (loaded automatically):
     - `feedback_uvicorn_no_reload_in_prod.md` (NEW)
     - `trading_corp_kalshi.md` (updated with dashboard-cutoff + target_iso)
     - `sigma_vs_bucket_width_mismatch.md`

## FIRST ACTION — three verification queries

SSH may still be blocked from non-home IPs. If so, pivot straight to
`az vm run-command invoke` per `feedback_az_run_command_when_ssh_blocked.md`.

### Query 1 — PCT pruner fire (the 11:35 UTC timer should have run)

```bash
ssh azureuser@trading.jacksumner.com "sqlite3 /home/azureuser/trading_corp/data/trading_corp.db \"
  SELECT ts, payload_json
    FROM audit_event
   WHERE kind='pct_stale_prune'
     AND json_extract(payload_json, '\$.apply')='true'
   ORDER BY ts DESC LIMIT 3;
\""
```

Expected: row at ~11:35 UTC with `candidates: ~454+, deleted: ~454+, apply: true`.
Also confirm `polymarket_copy_trader/would_have_placed` count dropped by ~454:

```bash
ssh azureuser@trading.jacksumner.com "sqlite3 /home/azureuser/trading_corp/data/trading_corp.db \"
  SELECT COUNT(*) FROM audit_event
   WHERE actor='polymarket_copy_trader' AND kind='would_have_placed';
\""
```

Was 1,707 at 03:41 UTC; should now be ~1,253 (1,707 - 454, plus overnight accumulation).

### Query 2 — target_iso on fresh kalshi_weather audit rows

```bash
ssh azureuser@trading.jacksumner.com "sqlite3 /home/azureuser/trading_corp/data/trading_corp.db \"
  SELECT ts,
         json_extract(payload_json,'\$.ticker'),
         json_extract(payload_json,'\$.target_iso') AS target_iso,
         json_extract(payload_json,'\$.expires_at') AS expires_at
    FROM audit_event
   WHERE actor='kalshi_weather_arb' AND kind='would_have_placed'
     AND ts >= '2026-05-17T03:09:30+00:00'
   ORDER BY ts ASC LIMIT 10;
\""
```

Expected: `target_iso` populated on every row. The date segment of the
ticker (e.g. `KXHIGHDEN-26MAY17-...`) MUST match the date segment of
`target_iso` (`2026-05-17T...`), NOT the `expires_at` date (which will be
`2026-05-18T14:00:00Z` for daily HIGH/LOW markets).

If `target_iso` is NULL or matches `expires_at` date → the date-parse fix
regressed. Roll back via the deploy_log recipe.

### Query 3 — post-cutoff RT win-rate trajectory

```bash
ssh azureuser@trading.jacksumner.com "sqlite3 /home/azureuser/trading_corp/data/trading_corp.db \"
  -- kalshi_weather: should start filling after ~14:00 UTC settlements
  SELECT division, COUNT(*) n, SUM(won) wins,
         ROUND(100.0*SUM(won)/NULLIF(COUNT(*),0),1) wr_pct,
         ROUND(SUM(realized_pnl),2) pnl
    FROM kalshi_round_trips
   WHERE division IN ('kalshi_weather','kalshi_crypto')
     AND entry_ts >= CASE division
                       WHEN 'kalshi_weather' THEN '2026-05-16T19:18:00+00:00'
                       WHEN 'kalshi_crypto'  THEN '2026-05-16T19:37:00+00:00'
                     END
   GROUP BY division;
\""
```

**Validation gate:** ≥30 RTs per division at WR ≥65% before any
`auto_execute: true` flip. Likely still far short on day 1.

## REPORT findings before proposing work

Once observations are in, choose from these pickup candidates (ordered):

  (A) **target_iso cross-check** (~5 min). The query above is the entire
      check; one passing audit row confirms wiring.
  (B) **PA-rejection investigation post-72bbbe4** (~15-30 min). With the
      parallel session's deferred-fire PA mechanism live, the rejection
      mix may have shifted. Worth a fresh query and a re-read of `72bbbe4`.
  (C) **factors: block cleanup** in `config/strategies.yaml` (~15 min, P3).
      887-line stale block; cosmetic. Out-of-scope find from H2 deploy.
  (D) **Archaeology bundle**: 19:24 UTC yaml mystery edit + orphan
      `mc_b_gold_buy # H2: was 5` marker (~10 min combined, P3).
  (E) **Empirical σ-scaling** (P2, ~1-2h). Blocked until ≥30 post-cutoff RTs.
  (F) **PMCC audit** — perennial; needs scope-narrowing first.
  (G) Something else surfaced by the observation pass.

## CONFIRMED-NOT-TO-DO without explicit re-approval

  - Do NOT flip `htf_gate.mode: enforce → shadow` back.
  - Do NOT flip `trade_plan.enabled: true`. Phase 1E gate.
  - Do NOT enable `auto_execute: true` on weather, crypto, or BitUnix until
    validation gates met.
  - Do NOT delete backup tags `.pre-rt-cutoff-20260517-0249`,
    `.pre-target-iso-20260517-0309`, or pre-bucket-guard tags until ≥24h
    of clean behavior.
  - Do NOT delete pre-cutoff kalshi RTs from `kalshi_round_trips` — they're
    the σ-scaling dataset. The dashboard cutoff dict already filters them
    from tiles + history.
  - Do NOT disable the PCT pruner timer or change its 24h cutoff without
    ≥48h of confirmed-clean behavior.
  - Do NOT touch `trading_corp/web/data.py`'s `DASHBOARD_RT_CUTOFFS` dict
    or the `_kalshi_cutoff_clause` helper without explicit approval — they
    drive what the Board sees on the dashboard.

## Environment notes

  - Local Python: `C:/Users/AA Incorporado/AppData/Local/Python/bin/python.exe`
    (bare `python` is the MS Store stub and breaks; bare `python3` is also
    a stub).
  - SSH usually blocked from non-home IPs; pivot to
    `az vm run-command create --script @file` per
    `feedback_az_run_command_when_ssh_blocked.md`.
  - Windows checkout is CRLF; deploy scripts MUST `tr -d '\r'` before
    `az vm run-command create` per `trading_corp_windows_crlf_vs_prod_lf.md`.
  - `web/data.py` changes need `systemctl restart trading-corp` to take
    effect (uvicorn runs without `--reload` in prod) per
    `feedback_uvicorn_no_reload_in_prod.md`. Template files DO live-reload.
  - `az vm run-command create` is single-tenant; `--run-command-name` must
    be unique-per-deploy or `az vm run-command delete --yes` first.

Honest assessment first — don't dive into code until observation findings
are reported.
