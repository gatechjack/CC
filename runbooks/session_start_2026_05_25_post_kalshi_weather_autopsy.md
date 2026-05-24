# Next-session pickup prompt (post kalshi_weather 24h post-xref autopsy)

*Written 2026-05-24 ~20:30 UTC at the end of a read-only forensic session on kalshi_weather post-xref RTs. NO deploys this session; prod is unchanged.*

---

Paste this into a fresh Claude Code session at `C:/Users/AA Incorporado/cc`:

---

Resuming after the 2026-05-24 kalshi_weather post-xref 24h autopsy session. **Four commits on `origin/main`** (pushed at end of last session):
- `84ceea9` — backlog: EOS snapshot 2026-05-24 ~20:00 UTC
- `0ab8daa` — housekeeping: keep verified kalshi_weather corpus-fetch driver (`scripts/fetch_kalshi_weather_corpus.py`)
- `239e99c` — report: link kalshi_weather anomalies #1+#2 to queued NBM-sigma work (autopsy §7)
- `ecc3367` — report: kalshi_weather 24h post-xref autopsy

**Prod is UNCHANGED.** `kalshi_weather_arb` still running f5a5fd5 (YAML xref loader) live since 2026-05-22T16:25 UTC. No code on prod was modified this session. No other divisions touched.

## Read first

1. **`reports/2026-05-24_kalshi_weather_post_xref_24h_autopsy.md`** — full 7-section autopsy. Sections 3 (defect-class scan), 4 (σ calibration), 7 (anomalies #1+#2 → NBM-σ connection) are the load-bearing reads.
2. **`BACKLOG.md` top EOS snapshot (2026-05-24 ~20:00 UTC)** — handoff + watch-list + open items.
3. **Memory (auto-loaded):** `project_kalshi_weather_24h_post_xref_autopsy.md` (new this session), updated `project_kalshi_weather_xref_p3_live.md`.

## Verdict from last session

- **NO logic defect.** All five enumerated defect classes scanned clean (station mismatch, coord_source anomaly, KXTEMPNYCH leak, floor breach, systematic bias).
- True post-deploy sample (entry_ts ≥ 2026-05-22T16:25): n=75 / WR 70.7% / **net −$28.15** — inside variance.
- ~$53 of the −$81 by-resolved_ts headline was **pre-deploy carryover** (entries under old KJFK/KORD/KIAH coords settling in-window). **Future post-deploy reads MUST filter by `entry_ts`, not `resolved_ts`.**

## Two anomalies on the observation-week watch (not acted on)

- **#1 — book is 100% NO bets, 87% NO-on-`between`.** Short-vol posture, geographically diverse but directionally one-dimensional. Synoptic weather (regional cold push) drives correlated losses across many tickers.
- **#2 — σ_used appears under-estimated.** Empirical |z|≥2 at 3.1× theoretical, |z|≥3 at 10×, 1–2σ band depleted (0.54×), stdev z = 1.168 (~17% wider than σ_used). **Directional only:** 12 tail rows collapse to 5 unique (station, date) events driven by 2026-05-23 Midwest/Texas cold push; used Open-Meteo reanalysis as proxy for actuals (not authoritative NWS CLI).

Together → strategy is **short-vol with underpriced tails**. The queued `P2 Empirical σ-scaling factor` (NBM-σ) work in BACKLOG addresses both — moves from "speculative" to "justified" if the watch signal repeats.

═══════════════════════════════════════════════════════════════════════════
## What to work on next — Board picks ONE
═══════════════════════════════════════════════════════════════════════════

### TRACK A — Mid-week watch-list check (~30m, read-only)

If today is on or before 2026-05-29 and you want a pulse-check before the formal end-of-week autopsy:

1. Run `python scripts/fetch_kalshi_weather_corpus.py` to pull the latest RT corpus from prod (this is the committed driver, stdlib only, chunked dd+base64).
2. Filter the new RTs to those with `entry_ts > <prior pull cutoff>` AND `entry_ts >= '2026-05-22T16:25'`.
3. For the four watch stations (**KMSP, KSAT, KAUS, KSEA**), compute |z| against Open-Meteo (quick) for any *new* settle dates since 2026-05-23. If any hit |z| > 2 on a *different* date → flag the NBM-σ work as justified.
4. Also: check if ANY YES bet has landed since 2026-05-23. If still 100% NO across the week, the short-vol diagnosis hardens.

**Read-only.** No prod writes. Report findings, do not act.

### TRACK B — End-of-observation-week formal autopsy (~2h, gated on date)

Run only when on or after ~2026-05-29:

1. Pull full post-2026-05-22T16:25 RT corpus via `scripts/fetch_kalshi_weather_corpus.py`.
2. **Use NWS CLI HTML scrape**, not Open-Meteo, for actuals — each station's `feeds.cli_observed_html` URL is in `config/weather_stations.yaml`. That's what Kalshi settles against. (Open-Meteo was last session's first-pass proxy; not authoritative.)
3. Recompute z-score distribution and run §3 defect-class scan against the full week's RTs.
4. If σ under-estimation survives a clean week (independent-date repeats at KMSP/KSAT/KAUS/KSEA or new fat-tail stations) → start the NBM-σ implementation work.
5. If bet-shape stays 100% NO across 5+ days → consider whether YES-side bets should be unblocked or whether NO-only is structurally correct given the strategy's `direction=between` focus.
6. Write `reports/2026-05-29_kalshi_weather_obs_week_verdict.md` (or whatever the date is).
7. **P4 advance gate:** advance to P4 (legacy `_CITY_COORDS_FALLBACK` removal) only if the clean-week verdict is unambiguous. Operator explicit go required.

### TRACK C — Pivot to another division

Kalshi-weather observation week runs autonomously through ~2026-05-29; you don't *need* to touch it mid-week. Other open work:

- **kalshi_sports_arb_observer** corpus accumulation (Phase 0 LIVE on MLB since 2026-05-24 15:40 UTC; first decision point ~10–15 cycles in). See BACKLOG EOS snapshot 2026-05-24 ~17:00 UTC.
- **Security tracks** — C-2 still patched-in-code but not deployed; C-1 secret rotation unchanged. See `reports/2026-05-21_security_review.md`.
- **PM watchlist** — cadence-change plan filed BOARD-GATED.

═══════════════════════════════════════════════════════════════════════════
## Hard discipline reminders for this work
═══════════════════════════════════════════════════════════════════════════

- **Read-only.** Both TRACK A and TRACK B are forensics. No prod writes, no positions touched, no fixes proposed without explicit operator go.
- **Filter by `entry_ts`, NOT `resolved_ts`.** The carryover trap cost us a misleading −$81 headline last session.
- **Tail multipliers use distinct (station, date) tuples.** Row counts overstate independent-event counts by ~2.4× because of multi-ticker-per-station structure.
- **Open-Meteo ≠ NWS CLI.** Directional only for first-pass; NWS CLI scrape required for any verdict.
- **Observation windows are durations, not samples.** One clean day is the start, not the end. See `feedback_observation_window_no_early_advance.md`.
- **Delegate mechanical SQL/data pulls to Sonnet sub-agents.** Last session worked well: two Sonnet sub-agents handled all prod pulls; Opus retained framing.
- **Tighter commits than feels normal.** Commit each forensic artifact as it lands.

═══════════════════════════════════════════════════════════════════════════
## Prod state snapshot at session end
═══════════════════════════════════════════════════════════════════════════

- `kalshi_weather_arb`: f5a5fd5 (P3 YAML xref) — live since 2026-05-22T16:25 UTC
- `kalshi_sports_arb_observer`: Phase 0 MLB live since 2026-05-24 15:40 UTC (cap 150)
- `kalshi_sports_scout`: discovery-rotation fix b880b66 + MLB aliases d6d54d3 — corpus accumulating
- `polymarket_arbitrage`: per-condition_id cap shipped 2026-05-21 paper-only
- `IC grader`: 112aef3 live, paste-and-grade at /telemetry/iron_condor
- `bitunix_futures`: bias TTL 30 + flip-detection observe-only
- `requirements.lock`: C-6 corrected + deployed 2026-05-24 15:14 UTC
- C-2 patch in code (19ff0da) but NOT on prod yet

No environment drift to address. Local working tree is clean (only untracked = `docs/Deployment notes.txt`, operator's pre-existing notes, not session output).
