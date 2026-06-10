# Bitunix Observation Window — Day-2 Expanded Review (post vol-classifier fix)

**Date:** 2026-06-10 · **Session type:** operator-supervised, read-only, prod-data review
**Branch:** `bitunix-day2-expanded-review-2026-06-10` (dedicated worktree; unmerged audit trail)
**Author:** Claude (Opus) under CLAUDE.md Session discipline

> **STATUS: DRAFT — data collection in progress.** Per-question sections fill in
> as operator-run SSH queries return. Verdict section finalizes last.

---

## 0. Scope & constraints

- **Read-only throughout against prod.** No code changes, no prod writes, no config
  changes. The only writes this session are local artifacts: this report, and
  (conditionally) a deploy_log F-5 watch-item update if Q5 confirms classifier
  output is correct in the 3–5% band.
- **Operator runs all SSH.** This agent provides SQL/journalctl scripts; the operator
  pastes a one-line streamer and returns output.
- **Out of scope:** any fix or tuning (findings inform operator decisions only);
  Polymarket work; Day-5 close-out (this is Day-2; window continues unless a hard-stop
  fires).

### Hard stops (abort analysis + surface immediately if any trip)
1. **Any live-mode primitive firing in paper** (Phase 3 audit kinds: `live_exit_order_*`,
   `position_state_*`, `restart_resume_*`, `exit_outcome_recorded`,
   `orphan_broker_position_on_restart`) → STOP.
2. **`execution_mode` ≠ paper** → STOP.
3. **Runaway fire rate** (> 3× the ~9–10/day anchor, i.e. > ~30/day) → STOP and surface
   before continuing.

**Hard-stop status:** ⏳ pending first query (s3 HS1/HS2/HS3).

---

## 1. State verification (complete)

| Item | Value | Source |
|---|---|---|
| Local `origin/main` HEAD | `32aa884dcdbb5b7801a43bb7758a6672449ef490` | `git rev-parse origin/main` |
| Deployed F-5 fix merge | `7834375` ("Merge … P1 vol-classifier wiring fix") | deploy_log 2026-06-09 |
| Fix ∈ origin/main? | **Yes** — `merge-base --is-ancestor 7834375 origin/main` → exit 0 | git |
| Working isolation | dedicated worktree `bitunix-day2-expanded-review-2026-06-10` off origin/main | `git worktree add` |
| Window start | **2026-06-09 03:49:41 UTC** (MainPID 2397472 ActiveEnter) | deploy_log |
| Window close (Day-5) | 2026-06-14 03:49 UTC | 5-day window |
| Pre-bug fire-rate anchor | ~9–10/day (06-02 traded 9×, ATR 2.92%) | BACKLOG P1 / deploy_log |
| Day-1 check-in (given) | 3 fires, 3/3 wins, avg R 0.644 | operator (in-prompt) |

> **Note (anomaly, low-severity):** the session was launched with `--worktree` but the
> shell resolved to the **main checkout on `main`**, not a per-session worktree. A
> dedicated worktree was created explicitly to honor CLAUDE.md worktree-isolation
> discipline before any commit. No branch-hijack occurred (main checkout HEAD untouched).

### Deployed fix — what changed (for interpreting Q5)

`trading_corp/agents/strategies/bitunix_htf_regime.py::_atr_pct_to_tier` (lines 743–751,
verified this session) — final tier boundary now reads `tier_thresholds["extreme"]`
(5.0%) instead of the vestigial `["high"]` (3.0%):

- `atr_pct is None` → **Unknown** (cold-start / data outage / SAFE_MODE)
- `< low` (0.5%) → Low · `< normal` (1.5%) → Normal
- `< extreme` (5.0%) → **High → tradeable** (`size_multiplier=1.0`) ← newly unlocked [3%,5%) band
- `≥ extreme` (5.0%) → **Extreme → `size_multiplier=0.0` hard-zero** (still suppressed)

---

## 2. Methodology & data sources

- **DB:** `/home/azureuser/trading_corp/data/trading_corp.db` (prod VM `tc-prod-vm`).
- **Window predicate:** `ts >= '2026-06-09T03:49:41+00:00'`.
- **Query scripts** (this session, read-only, local in repo root):
  - `s3.sh` — probe: hard-stop scan + Day-1 reused queries + Q2/Q3 linkage-confirm dumps.
  - `s4.sh` — Q2 ATR-band split (audit-join) + Q3 TP-leg distribution (built after s3 confirms linkage).
  - `j1.sh` — journalctl probes: db-lock retry counts (Q4) + bitunix observer anomalies (Q6).
- **Streamer pattern:** `Get-Content sN.sh -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\r'|bash"`.

### Schema facts established (verified against source this session)

- `paper_trade_record` has **no ATR column**; ATR-at-decision lives only in the
  `htf_gate_decision` audit payload as `atr_pct_d1`. Trade↔gate linkage is by
  `source_signal`/`trigger_signal` + timestamp proximity (no shared key). **Confirmed on
  real rows in s3 LINK-A/B before the Q2 join is trusted.**
- `result` ∈ {`win`,`loss`,`expired`,NULL}; **TP-leg reached is not in `result`** — it is
  `extra_json.filled_legs` (e.g. `["tp1"]`, `["tp1","tp2"]`, `["tp1","tp2","tp3"]`) for v2
  multi-leg trades. Q3 reads `filled_legs`. **Confirmed in s3 LINK-C.**
- db-lock retries are **not** audit rows — they are journalctl warnings only
  (`log_event: database locked` for the logger/HITL path; `insert_paper_trade_record:
  database locked` for the Decision 6.2 path). Q4 db-lock count comes from `j1.sh`.
- `volatility_tier` domain: {low, normal, high, extreme, **unknown**}. `unknown` fires when
  `atr_pct_d1 is None` (cold-start/outage) or SAFE_MODE — relevant to the 14:48Z restart.

---

## 3. Findings by question

### Q1 — Trade inventory & fire rate
⏳ pending (s3 HS3/Q1a/Q1b/Q1c).

### Q2 — ATR-band performance split (<3% vs 3–5%) — *load-bearing*
⏳ pending (s3 LINK-A/B confirm join → s4 band split).

### Q3 — R distribution / TP-leg fill
⏳ pending (s3 LINK-C confirm → s4 TP-leg distribution).

### Q4 — Hard-stop checks (live primitives / agent_errors / execution_mode / db-lock)
⏳ pending (s3 HS1/HS2/Q4 + j1 db-lock).

### Q5 — Classifier sanity (band violations / Unknown / flapping near 5%)
⏳ pending (s3 Q5a–Q5d).

### Q6 — Anomaly sweep (reconciler / skipped-rejected signals / journalctl)
⏳ pending (s3 Q6a–Q6c + j1).

---

## 4. Verdict
⏳ pending all questions.

- (a) Window health (clean / concerns): —
- (b) 3–5% band performance read: —
- (c) Anything that should pause the window or trigger operator action before Day-5: —

---

## 5. Appendix — raw query outputs
⏳ appended verbatim as data returns.
