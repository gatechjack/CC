-- scripts/check_weather_coord_drift.sql
--
-- P3 observation-week drift check for kalshi_weather_arb.
-- Read-only. Reports activity since the P3 deploy at 2026-05-22T16:25:00 UTC.
--
-- Run on prod:
--   sqlite3 /home/azureuser/trading_corp/data/trading_corp.db < scripts/check_weather_coord_drift.sql
--
-- Run via az:
--   az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm \
--     --command-id RunShellScript --scripts 'sqlite3 /home/azureuser/trading_corp/data/trading_corp.db < /tmp/check_weather_coord_drift.sql'
--
-- Or directly inline (single one-shot):
--   az vm run-command invoke ... --scripts "sqlite3 ... \"<paste the queries below>\""
--
-- ─── How to read the output ───────────────────────────────────────────────
-- HEALTHY (P4-ready):
--   • Section 1 shows steady total_evals growth (hundreds per day).
--   • Section 2 shows every row coord_source='yaml_verified'.
--   • Section 3 reports 'NO DRIFT' — yaml_coords == legacy_coords on every eval.
--   • Section 4 reports 'OK — no legacy_fallback events'.
--   • Section 5 reports 'OK — no disabled_skip leaks'.
-- CONCERNING:
--   • Section 3 shows any rows → a YAML/legacy mapping diverges. Investigate
--     before P4 (removing legacy) — either the YAML or legacy_dict is wrong
--     for that ticker.
--   • Section 4 shows any rows → a series Kalshi exposed is NOT in the YAML
--     (or someone re-marked an entry verified:false). Either add to YAML +
--     verify, or remove the unknown series from the candidate pool. Don't
--     proceed to P4 with legacy_fallback events outstanding — the safety
--     net is exactly what they need.
--   • Section 5 shows any rows → the upstream _DISABLED_SERIES_PREFIXES
--     filter leaked. Hard bug, investigate immediately.
-- ─────────────────────────────────────────────────────────────────────────

.headers on
.mode column

-- Section 1: window scope + total evals since P3 deploy
SELECT '─── 1. Scope ──────' AS section;
SELECT '2026-05-22T16:25:00' AS window_start_utc,
       datetime('now') AS now_utc,
       (SELECT COUNT(*)
          FROM audit_event
         WHERE actor = 'kalshi_weather_arb'
           AND kind = 'kalshi_weather_evaluated'
           AND ts >= '2026-05-22T16:25:00') AS total_evals_in_window;

-- Section 2: coord_source distribution
SELECT '' AS sep;
SELECT '─── 2. coord_source distribution ──────' AS section;
SELECT json_extract(payload_json,'$.coord_source') AS coord_source,
       COUNT(*) AS n,
       MIN(ts) AS first_seen,
       MAX(ts) AS last_seen
  FROM audit_event
 WHERE actor = 'kalshi_weather_arb'
   AND kind = 'kalshi_weather_evaluated'
   AND ts >= '2026-05-22T16:25:00'
 GROUP BY 1
 ORDER BY n DESC;

-- Section 3: drift cases (yaml_coords != legacy_coords)
-- This is the P4-gate question. Every row here is a YAML/legacy mismatch.
SELECT '' AS sep;
SELECT '─── 3. Drift cases (yaml_coords ≠ legacy_coords) ──────' AS section;
SELECT ts,
       json_extract(payload_json,'$.ticker') AS ticker,
       substr(json_extract(payload_json,'$.ticker'), 1,
              instr(json_extract(payload_json,'$.ticker'),'-')-1) AS series,
       json_extract(payload_json,'$.yaml_coords') AS yaml_coords,
       json_extract(payload_json,'$.legacy_coords') AS legacy_coords
  FROM audit_event
 WHERE actor = 'kalshi_weather_arb'
   AND kind = 'kalshi_weather_evaluated'
   AND ts >= '2026-05-22T16:25:00'
   AND json_extract(payload_json,'$.yaml_coords') IS NOT NULL
   AND json_extract(payload_json,'$.legacy_coords') IS NOT NULL
   AND json_extract(payload_json,'$.yaml_coords')
       != json_extract(payload_json,'$.legacy_coords')
 ORDER BY ts;

-- Drift summary line (plain-language verdict)
SELECT CASE
         WHEN COUNT(*) = 0
           THEN 'NO DRIFT — every eval agrees yaml_coords == legacy_coords (P4-ready signal for this window)'
         ELSE COUNT(*) || ' DRIFT EVENTS — investigate the rows above before P4'
       END AS drift_verdict
  FROM audit_event
 WHERE actor = 'kalshi_weather_arb'
   AND kind = 'kalshi_weather_evaluated'
   AND ts >= '2026-05-22T16:25:00'
   AND json_extract(payload_json,'$.yaml_coords') IS NOT NULL
   AND json_extract(payload_json,'$.legacy_coords') IS NOT NULL
   AND json_extract(payload_json,'$.yaml_coords')
       != json_extract(payload_json,'$.legacy_coords');

-- Section 4: legacy_fallback events
-- In steady state this is zero — every Kalshi weather series should be in
-- the YAML with verified:true. A non-zero count means a new/unverified
-- series showed up; the safety net caught it, but it needs a P2-style
-- review pass before P4 can fairly assess.
SELECT '' AS sep;
SELECT '─── 4. legacy_fallback events (steady state = zero) ──────' AS section;
SELECT ts,
       json_extract(payload_json,'$.ticker') AS ticker,
       substr(json_extract(payload_json,'$.ticker'), 1,
              instr(json_extract(payload_json,'$.ticker'),'-')-1) AS series,
       json_extract(payload_json,'$.legacy_coords') AS legacy_coords
  FROM audit_event
 WHERE actor = 'kalshi_weather_arb'
   AND kind = 'kalshi_weather_evaluated'
   AND ts >= '2026-05-22T16:25:00'
   AND json_extract(payload_json,'$.coord_source') = 'legacy_fallback'
 ORDER BY ts;

SELECT CASE
         WHEN COUNT(*) = 0
           THEN 'OK — no legacy_fallback events (every traded series verified in YAML)'
         ELSE COUNT(*) || ' legacy_fallback events — a series is unknown or verified:false; review the rows above'
       END AS fallback_verdict
  FROM audit_event
 WHERE actor = 'kalshi_weather_arb'
   AND kind = 'kalshi_weather_evaluated'
   AND ts >= '2026-05-22T16:25:00'
   AND json_extract(payload_json,'$.coord_source') = 'legacy_fallback';

-- Section 5: disabled_skip leaks (upstream filter should catch all of these)
SELECT '' AS sep;
SELECT '─── 5. disabled_skip leaks (upstream filter health) ──────' AS section;
SELECT ts,
       json_extract(payload_json,'$.ticker') AS ticker,
       substr(json_extract(payload_json,'$.ticker'), 1,
              instr(json_extract(payload_json,'$.ticker'),'-')-1) AS series
  FROM audit_event
 WHERE actor = 'kalshi_weather_arb'
   AND ts >= '2026-05-22T16:25:00'
   AND json_extract(payload_json,'$.coord_source') = 'disabled_skip'
 ORDER BY ts;

SELECT CASE
         WHEN COUNT(*) = 0
           THEN 'OK — no disabled_skip leaks (upstream _DISABLED_SERIES_PREFIXES catching all)'
         ELSE COUNT(*) || ' disabled_skip events — a disabled series leaked past the upstream filter; bug'
       END AS disabled_verdict
  FROM audit_event
 WHERE actor = 'kalshi_weather_arb'
   AND ts >= '2026-05-22T16:25:00'
   AND json_extract(payload_json,'$.coord_source') = 'disabled_skip';

-- Section 6: scan summaries — confirms KXTEMPNYCH still being dropped upstream
SELECT '' AS sep;
SELECT '─── 6. Upstream KXTEMPNYCH filter (scan summary) ──────' AS section;
SELECT ts,
       json_extract(payload_json,'$.markets_pre_filter') AS pre_filter,
       json_extract(payload_json,'$.skipped_disabled_series') AS disabled_drops,
       json_extract(payload_json,'$.candidates') AS candidates
  FROM audit_event
 WHERE actor = 'kalshi_weather_arb'
   AND kind = 'kalshi_weather_scan'
   AND ts >= '2026-05-22T16:25:00'
 ORDER BY ts DESC
 LIMIT 5;
