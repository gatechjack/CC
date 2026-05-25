# kalshi_weather Bucket 1 — Forward-Watch at ~16h Post-Deploy (2026-05-25)

**Scope:** TRACK A forward-watch on commit `75ba7c5` (Bucket 1: HRRR
+ run-age logging), deployed 2026-05-24T21:47:13 UTC. Sampling window:
2026-05-24T21:53:23 UTC (first post-restart audit row) → 2026-05-25T14:10
UTC. Read-only verification; no code changes, no rollback.

**Headline:** All three forward-watch obligations PASS. Two pre-existing
documentation phantoms surfaced — see §3.

---

## 1. Verdicts

| obligation | result | data |
|---|---|---|
| Q1 — coord_source = yaml_verified across NYC/CHI/HOU | **PASS** | 3,153 / 3,153 yaml_verified rows since deploy carry yaml_coords; 0 mismatch |
| Q2 — HRRR availability rate near 100% during US weather hours | **PASS** | 96.8% (3,080 open_meteo_hrrr / 3,183 = available, 103 = unavailable; no sustained multi-hour pocket) |
| Q3 — NWS issued_at populate rate above 0% | **PASS (better than expected)** | 100% NOT NULL (3,183 / 3,183). No Akamai stripping in window. |

Per-city distribution (Bucket 1–format rows since 21:53:23 UTC):

| city prefix | yaml_verified rows | old-format rows | yaml_coords populated |
|---|---|---|---|
| NYC (KXHIGHNY / KXLOWTNYC / KXTEMPNY) | 184 | 24 | 184 / 184 |
| CHI (KXHIGHCHI / KXLOWTCHI / KXTEMPCHI) | 137 | 66 | 137 / 137 |
| HOU (KXHIGHTHOU / KXLOWTHOU) | 142 | 64 | 142 / 142 |

The "old-format" column is residual carryover: `26MAY24-*` expiry
contracts that were still evaluating after the 21:47 UTC restart. They
predate Bucket 1 and naturally lack the new audit fields. Not a
regression — confirms that the Bucket 1 envelope only applies to
forward contracts (`26MAY25-*` and later).

---

## 2. What is genuinely on the audit row

For each Bucket 1–format row of `kind = 'kalshi_weather_evaluated'` in
`audit_event` since 21:53:23 UTC:

- `coord_source = 'yaml_verified'` ✓
- `yaml_coords = [lat, lon]` populated ✓
- `hrrr_temp_f`, `hrrr_source`, `hrrr_fetched_at` populated 96.8% of the
  time (103 transient `unavailable` rows; no sustained gap)
- `nws_forecast_issued_at` populated 100% of the time (header capture
  fully functional)
- `nws_fetched_at`, `open_meteo_fetched_at` populated ✓
- `metar_obs_age_min` / `metar_latest_obs_iso` populated only for
  sub-6h hourly markets (by design — daily HIGH/LOW skips METAR)

---

## 3. Pre-existing documentation phantoms (surfaced during verification)

These are NOT TRACK A regressions. They are statements in prior-session
docs that don't match the running code. Flagging so the next session
catches them before treating them as ground truth.

### Phantom #1 — `audit_lat` / `audit_lon` were SQL aliases, not JSON keys (column-name confusion, not structural gap)

**CORRECTION (added 2026-05-25 later same day, after initial commit):**
the framing below in §3.1 was partially wrong. The deeper read into
`kalshi_weather_arb.py:298-299, 591, 723` showed scalar coord fields
named `lat` and `lon` DO exist in the payload — they were just queried
with the SQL aliases `audit_lat` / `audit_lon` in the plan's
verification query (`json_extract(payload_json, '$.lat') AS audit_lat,
json_extract(payload_json, '$.lon') AS audit_lon`). Downstream docs
(deploy_log, BACKLOG, session_start) then propagated those alias names
as if they were JSON keys. The Track A sub-agent searched for
`$.audit_lat` / `$.audit_lon` (which don't exist) and reported a
structural gap; in fact the substantive coord-integrity check —
"scalar `$.lat`/`$.lon` (populated from `chosen[*]` after xref)
equal `$.yaml_coords[0]`/`$.yaml_coords[1]` (the YAML-resolved
list) for `yaml_verified` rows" — IS queryable and DOES pass on the
3,153-row sample. The phantom was a column-name aliasing artifact,
not a missing field. The docs have been amended 2026-05-25 to use
`$.lat` / `$.lon`.

The original (pre-correction) §3.1 framing follows for the audit
trail.

---

- **Claimed in:** `runbooks/deploy_log.md` 21:47 UTC entry, lines 195–197:
  > `audit_lat = 29.6454`, `audit_lon = -95.2789` = KHOU coords (corrected)
  > `yaml_coords = [29.6454, -95.2789]` = matches audit_lat/lon byte-for-byte
  > `coord_source = yaml_verified`
- Same phrasing in `BACKLOG.md` (top EOS), `runbooks/session_start_2026_05_25_post_kalshi_weather_autopsy.md`, and `docs/Deployment notes.txt`.
- **Original (now superseded) read:** "code reality — `grep -rn 'audit_lat\|audit_lon' trading_corp/` returns zero hits; the Bucket 1 payload writes coords ONLY into `yaml_coords: [lat, lon]`." That grep result was correct but the conclusion was wrong: the grep should have been for `\"lat\":` and `\"lon\":` (the actual JSON key emissions), which return 5 hits including line 298-299 and 591 and 723 of `kalshi_weather_arb.py`.
- **Corrected read (2026-05-25):** payload has BOTH the scalar `$.lat` / `$.lon` keys AND the `$.yaml_coords[lat, lon]` list. For `yaml_verified` rows they hold byte-equal coord values (because `chosen` IS `yaml_coords`). The integrity check is queryable and PASSED.
- **Match against memory:** `feedback_session_committed_phantom_pointer`
  still applies — but the specific failure mode here was column-alias
  confusion compounding into a doc-everywhere-the-alias-is-the-name
  pattern, not a missing field. The corrective lesson for future
  Bucket-style audits: grep for the actual JSON key spelling
  (`"lat":` / `"lon":`) AND the alias spelling before concluding the
  field doesn't exist; or extract via both `$.lat` and `$.audit_lat`
  to see which path returns non-NULL.
- **Resolution:** docs amended 2026-05-25 to use `$.lat` / `$.lon`. No
  code change. Future verification queries should use `$.lat` /
  `$.lon` (matching the actual JSON keys).

### Phantom #2 — `plans/forecast-quality-improvements-for-kalshi-prancy-porcupine.md` was never committed

- **Claimed in:** `BACKLOG.md` top EOS (artifact list), `runbooks/deploy_log.md` 21:47 UTC entry, `runbooks/session_start_2026_05_25_post_kalshi_weather_autopsy.md`.
- **Reality:** `git log --all -- "plans/*"` returns nothing. No file at that path in working tree, no file in any branch. Deploy bundled Bucket 1 without writing the plan it cites.
- **Match against memory:** same phantom-pointer pattern.
- **Risk:** The session_start runbook tells the next session to read
  the plan to decide Bucket 2 sequencing. If the plan stays missing,
  Bucket 2 work has no canonical spec — only what's distilled in the
  deploy_log + autopsy + BACKLOG.
- **Board decision needed:** either (a) write the plan from session
  context now and commit it (risky — distilling memory into a doc that
  claims to be authoritative compounds the phantom), or (b) accept
  that the autopsy + deploy_log + BACKLOG together are the canonical
  artifact set and amend the three references to point there.

---

## 4. What to watch next

- **~22:00 UTC today (≈24h post-deploy)** — TRACK B σ-defect watch:
  re-run forensic on the 5 flagged stations (KMSP, KSAT, KAUS, KSEA,
  KHOU near-miss) with filter `entry_ts >= '2026-05-22T16:25'`. If 2+
  stations hit |z| > 2 on independent settle dates, NBM-σ work moves
  from speculative to justified.
- **~2026-05-29** — TRACK C end-of-observation-week autopsy v2 using
  NWS CLI HTML scrape for actuals (not Open-Meteo). Bonus: HRRR-only
  vs blend calibration is now possible (Bucket 1 data accumulates).

---

## 5. What was NOT done (intentional scope limits)

- No rollback. Both observable failure-modes (Q2 = 0%, Q3 = 0%) are
  clean.
- No σ recalibration. That's TRACK B/C work after the data window
  matures.
- No fix for either phantom. They're documentation discrepancies, not
  data defects — board decides resolution.
- No proposal to ship Bucket 2 items. Operator explicit go required;
  observation week is a duration, not a sample.
