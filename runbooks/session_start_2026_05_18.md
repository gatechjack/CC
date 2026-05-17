# Session start — 2026-05-18 (pickup from 2026-05-17 05:40 UTC EOS)

Read `BACKLOG.md` top snapshot first. This file is the operational
pickup brief — the queries to run, what each one's verifying, decision
tree per result.

## What shipped yesterday (high-level)

Five big things landed on prod 2026-05-17, in sequence:

1. **Deferred-fire PA mechanism** — when PA rejects a high-score fire,
   the payload is cached; a 60s `bitunix-pa-redeem` task re-runs the
   pipeline against fresh bars until score decays or PA passes.
2. **Deferred-fire dashboard surfaces** — Pending PA panel + redeem/expired
   aggregates + decision-flow redemption marker on the bitunix division
   page.
3. **Paper-mode multi-leg replay** — `paper_trade_replay` is now v2-aware:
   detects tp1/tp2/tp3 crosses, advances SL per Option C floor lifecycle
   (BE → tp1-price), emits `position_sl_update` audits with
   `source='paper_trade_replay'`.
4. **Trade Plan v2 dashboard panel** — surfaces `trade_plan_decision` +
   `position_sl_update` audits with entry/SL/tp1/tp2/tp3/sl_method/tp2_method/skip_reason.
5. **`trade_plan.enabled: true` flag flip (Phase 1E gate lifted)** — observer
   now dispatches `_build_proposal_v2` (structure-preferred SL + 3-leg TP)
   as the active placement path. Boot wiring confirms `trade_plan_active=True`.

All paper-mode. No live broker placement (Phase 4 is the next gate).

## First thing to do — verification queries

Run these against prod via `az vm run-command invoke` (SSH still
blocked from this network per `feedback_az_run_command_when_ssh_blocked.md`):

```bash
# Build the query block as one az invoke to amortize the ~30s overhead
az vm run-command invoke -n tc-prod-vm -g rg-shared-prod \
  --command-id RunShellScript --scripts "
DB=/home/azureuser/trading_corp/data/trading_corp.db
SQ='sqlite3 -header -column'

echo '=== Q1: trade_plan_decision audits since flip ==='
\$SQ \$DB \"
  SELECT COUNT(*) AS n,
         SUM(CASE WHEN json_extract(payload_json,'\$.should_trade')=1 THEN 1 ELSE 0 END) AS fired,
         SUM(CASE WHEN json_extract(payload_json,'\$.should_trade')=0 THEN 1 ELSE 0 END) AS skipped
    FROM audit_event
   WHERE kind='trade_plan_decision'
     AND ts >= '2026-05-17T05:14:00';
\"

echo '=== Q2: pa_validation_redeem + expired audits since 03:53 UTC ==='
\$SQ \$DB \"
  SELECT kind, COUNT(*) AS n
    FROM audit_event
   WHERE kind IN ('pa_validation_redeem','pa_validation_expired')
     AND ts >= '2026-05-17T03:53:00'
   GROUP BY kind;
\"

echo '=== Q3: v2 paper_trade_records since flip ==='
\$SQ \$DB \"
  SELECT order_id, ts, tier, side, result,
         json_extract(extra_json,'\$.tp_plan_version') AS tp_v,
         json_extract(extra_json,'\$.filled_legs') AS legs,
         json_extract(extra_json,'\$.redeemed') AS redeemed,
         actual_r_multiple
    FROM paper_trade_record
   WHERE division='bitunix_futures' AND ts >= '2026-05-17T05:14:00'
   ORDER BY ts DESC LIMIT 10;
\"

echo '=== Q4: position_sl_update audits since 05:14 UTC ==='
\$SQ \$DB \"
  SELECT ts, json_extract(payload_json,'\$.lifecycle_state') AS state,
         json_extract(payload_json,'\$.source') AS source,
         json_extract(payload_json,'\$.filled_legs') AS legs
    FROM audit_event WHERE kind='position_sl_update'
     AND ts >= '2026-05-17T05:14:00'
   ORDER BY ts DESC LIMIT 10;
\"

echo '=== Q5: score-engine activity since flip ==='
\$SQ \$DB \"
  SELECT json_extract(payload_json,'\$.tier') AS tier,
         json_extract(payload_json,'\$.outcome') AS outcome, COUNT(*) AS n
    FROM audit_event
   WHERE actor='bitunix_futures' AND kind='bitunix_score_decided'
     AND ts >= '2026-05-17T05:14:00'
   GROUP BY tier, outcome ORDER BY n DESC;
\"

echo '=== Q6: boot wiring (should still be trade_plan_active=True) ==='
journalctl -u trading-corp --since '24 hours ago' --no-pager 2>&1 \
  | grep 'BitUnix observer wiring' | tail -3

echo '=== Q7: H2 falsification gate progress ==='
\$SQ \$DB \"
  SELECT json_extract(payload_json,'\$.tier') AS tier, COUNT(*) AS n
    FROM audit_event
   WHERE actor='bitunix_futures' AND kind='bitunix_score_decided'
     AND ts >= '2026-05-16T19:21:00'
   GROUP BY tier;
\"
" --query "value[0].message" -o tsv
```

## Decision tree per result

### Q1 (`trade_plan_decision` count)

- **n=0**: PA is still rejecting 100% of fires; v2 path not exercised yet.
  Check Q5 — if score-engine IS firing, the rejects are caching (Q2 will
  show redeem/expired). Mechanism is working; just no fortunate PA-pass
  yet. Per `feedback_pa_gate_well_calibrated.md`, this is regime-driven,
  not gate problem. Patience.
- **n>0, fired=0, skipped=all**: v2 is running but the trade-plan
  builder itself is skipping (most likely `swing_too_close` or
  `fees_too_high_for_risk`). Pull the `skip_reason` distribution —
  query at deploy_log.md "Watch for" section.
- **n>0, fired>0**: 🎉 v2 trade fired in paper. Verify Q3 has a matching
  `paper_trade_record` with `tp_plan_version='v2'`. The first such row
  is the proof-of-life for the whole trade-plan v2 series.

### Q2 (deferred-fire audits)

- **redeem>0**: deferred-fire mechanism caught a reject + bar-tick
  re-eval found PA aligned. The redeem audit row has `order_id` (back-
  filled after placement). Look at `bars_waited` — useful for "is the
  60s re-eval cadence right?"
- **expired>0**: cached signals dropped without firing. `reason` field
  (`score_decay` vs `opposite_side`) tells you why. Pure score_decay =
  natural ledger TTL expiry. Lots of `opposite_side` = volatile/whipsaw
  regime where the score keeps flipping.
- **both=0**: no PA rejects yet OR all of them got immediately re-fired
  by score SKIP (very rare). Check Q5 for context.

### Q3 (v2 paper_trade_records)

- **First v2 row landing** — confirms the full code path: score → PA pass
  → HTF pass → `_build_proposal_v2` → `_log_trade_plan_decision` → placement
  → `paper_trade_record` write with `extra_json.tp_plan_version='v2'`.
- **`filled_legs` populated** — confirms paper_trade_replay is detecting
  TP crosses + writing back to extra_json.
- **`actual_r_multiple` weighted across legs** (0.125 / 0.75 / 1.25
  for the Option C scenarios) — confirms the multi-leg R aggregation
  math.

### Q4 (`position_sl_update`)

- **rows with `source='paper_trade_replay'`** — multi-leg replay
  detected a lifecycle transition (tp1 fill → SL to BE, tp2 fill →
  SL to tp1-price). The reconciler itself stays idempotent in paper
  mode because replay updates extra_json synchronously.
- **rows with `source='reconciler'`** — wouldn't expect any until
  Phase 4 (when broker truth populates filled_legs from live fills).

### Q5 (score-engine activity)

Sanity check that TV alerts are still arriving + observer is processing.
Pre-deferred-fire baseline was ~5 STANDARD/hour, ~3 SKIP/hour. Should
look roughly similar.

### Q6 (boot wiring)

Should read `trade_plan_active=True`. If a parallel session restarted
trading-corp + something rolled back the YAML, this would catch it.

### Q7 (H2 falsification gate)

Counts PREMIUM-tier fires since H2 went live (2026-05-16 19:21 UTC).
Gate is ≥30 PREMIUM with PREMIUM mean R ≥ STANDARD mean R + 0.05R.
Last check was 1/30 (3.3%). Should accelerate now that deferred-fire
+ v2 placement are unblocking actual paper trades.

## What to NOT do

See BACKLOG.md "Things to NOT do without explicit approval" section —
specifically:

- ⚠️ **Do NOT flip `trade_plan.enabled: false`** without a v2-performance
  memo. The trade-plan v2 path is the active placement code now;
  rollback would re-disable it.
- ⚠️ **Do NOT propose loosening PA gate thresholds when reject rates
  look high.** Per `feedback_pa_gate_well_calibrated.md`, chart-review
  evidence argues the gate is correctly catching bad setups; the
  deferred-fire mechanism is the capture path, not threshold tuning.
- Do NOT delete the four backup tags from yesterday's deploys until
  ≥24h confirms behavior (tags listed in BACKLOG snapshot).

## If you need to ship code that touches the score path

CLAUDE.md § 4 still applies — get explicit per-session approval before
touching TV → broker pipeline, risk gate logic, audit-write ordering,
secrets handling, broker adapters, LangGraph state, runbooks, infra
Bicep, VM-side configuration, `_acquire_lock()`, `broker_fallback_to_paper`
semantics, `auto_execute_caps`, HITL bypass, or default any new strategy
to `auto_execute: true`.

## Deploy mechanic refresher

SSH is blocked from current network. Use `az vm run-command invoke` per
`feedback_az_run_command_when_ssh_blocked.md`. For large patches, gzip
+ base64 fits under the 28KB `--scripts` cap. Surgical patches via
Python anchored patcher preserve prod drift; full-file replace is OK
when local md5 == HEAD~1 md5 (no drift). LF-normalize before any
byte-level op (Windows CRLF vs prod LF per
`feedback_surgical_edits_over_whole_file_scp.md`).

BitUnix .py + YAML changes ALL need `systemctl restart trading-corp`
(per `feedback_bitunix_no_hot_reload.md` + `feedback_uvicorn_no_reload_in_prod.md`).
Templates DO live-reload (Jinja re-reads per request) — only deploy that
DOESN'T need restart.
