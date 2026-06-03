# Phase 3 Day 2 paper-mode observation audit — 2026-06-02

**Date:** 2026-06-02 ~01:08 UTC (probe time 2026-06-03T01:08:07Z) · **Window:** Day 2 of 7-day paper-mode observation (2026-06-02 → 2026-06-09) · **Type:** read-only prod audit (Q1-Q6 + db-lock breakdown bonus) · **Branch:** `worktree-phase3-day2-audit-2026-06-02` off `origin/main` (`58744bb`).

Delivery mechanism note: operator network blocked SSH:22 (hotel WiFi, then mobile carrier outbound block) and healthz timed out from hotel WiFi. Audit re-routed through `az vm run-command` (Azure VM agent plane, network-independent). Two `az` invocations split because the agent's `value[0].message` buffer truncates head when total stdout exceeds ~4KB.

---

## 1. Verdict table

| Query | Verdict | Evidence |
|---|---|---|
| **Q1 — Prod stability** | **HEALTHY** | `MainPID=2043009` (matches 2026-06-02 deploy log), `NRestarts=0`, `ActiveState=active`, `SubState=running`, `ActiveEnterTimestamp=Tue 2026-06-02 01:39:50 UTC`, `healthz=200`. ~23.5h continuous uptime since deploy. |
| **Q2 — Phase 3 activation** | **GATED-CORRECTLY** | `audit_event` query covering `kind LIKE 'live_exit_order_%' OR 'position_state_%' OR 'restart_resume_%' OR IN ('exit_outcome_recorded','orphan_broker_position_on_restart')` returned **zero rows** since `2026-06-02T01:39:00+00:00`. All Phase 3 live-mode primitives stay dormant under `execution_mode=paper`. |
| **Q3 — Bitunix paper trade distribution** | **NORMAL** | `total=6, wins=5, losses=1, expired=0, open=0`. 83% win rate (small sample), zero stuck-open positions. ~0.25 trades/hr cadence. |
| **Q4 — Sample trades** | **NORMAL** | Visible rows: 2026-06-02T07:27Z sell STANDARD loss (`mc_a_blood_diamond` bear, leverage 25.0, size_pct_equity 0.0075, effective_risk_pct 0.000553); 2026-06-02T06:51Z sell STANDARD win (`cvd_bear_flip` bear, leverage 25.0, size_pct_equity 0.0075). **No `execution_mode` field in `extra_json`** on visible rows — consistent with paper-mode (Path C live-stamp at observer:2872-2875 correctly gated off). |
| **Q5 — Error/warning rate** | **ELEVATED-BUT-EXPLAINED** | 455 lines matching ` (ERROR\|WARNING) \|^Traceback` (excl. ASGI shutdown noise) over 23.5h ≈ 19/hr. Breakdown: ~70% yfinance errors (SUI-USD delisted, BTCUSDT 404 — pre-existing operational noise), 2 audit-logger retry-exhaustion events (see §2 below), TV webhook IP allowlist startup msgs (deploy-once), pykalshi 429/ConnectTimeout (operational). **No Phase 3-attributable tracebacks or new error patterns.** |
| **Q6 — Background sanity poll** | **NOT-RUNNING (correct)** | `journalctl ... \| grep 'sanity_poll\|position_state_sanity\|run_position_state_sanity'` returned empty. Sanity-poll background task never created because `_execution_mode == "live"` gate at `main.py` startup short-circuits before `asyncio.create_task`. |
| **execution_mode gate** | **paper @ strategies.yaml:1022** (verified inline) — gate intact. |

---

## 2. db-lock retry exhaustion anomaly (filed P3 in BACKLOG)

**Probe:** `journalctl -u trading-corp --since '2026-06-02 01:40:00' | grep -c 'database locked'` = **8**.

**Distribution (per Q5b sample):**
- Two visible exhaustions: `13:33:27` `hitl/pending_approval_added` + `14:33:49` `hitl/board_decision_received`.
- Both are `[audit] log_event FAILED after 4 attempts (database locked): <audit_path> → writing to fallback file` from `agents/logger.py:26` retry.

**Decision 6.2 path (`insert_paper_trade_record` retry) is silent:** the dedicated probe `grep -E 'insert_paper_trade_record|paper_trade_record.*lock'` returned **empty**. Paper trade writes are clean — they don't hit lock contention.

**What this means:**
- The deploy log §202 watch-list item #2 was specifically scoped to `insert_paper_trade_record` retry firing (= Phase 3-introduced retry). That path is dormant: ZERO firings.
- The 8 exhaustions are on the **pre-existing** `agents/logger.py` retry (which uses the same `_DB_LOCK_RETRY_DELAYS_SEC = (0.1, 0.3, 0.7)` schedule duplicated into `persistence/db.py` by Decision 6.2). This is NOT new from Phase 3 — but we don't have a pre-Phase-3 baseline rate to compare against.
- 8 retry-exhausted audit rows in 23.5h get diverted to the fallback file (per `agents/logger.py` design). The canonical `audit_event` table is missing these rows; dashboard summaries that read from `audit_event` will undercount hitl/* activity.

**Why this is worth a P3 entry:**
- Today's 8 exhaustions / 23.5h baseline lets us compare against future days. If the rate climbs after a `paper → live` flip (when the 60s sanity poll + restart-resume writes activate), we'd want to know.
- 8 fallback-file rows is not catastrophic for paper-mode (operator-facing HITL events; dashboard renders from snapshots, audit captures intent). But under live-mode it would mean we're losing rows the deterministic side may later need to reconcile.

---

## 3. What was NOT seen (negative findings — all healthy)

- **Zero appearances of Phase 3 audit kinds** in `audit_event` — gates are doing their job.
- **No restart since deploy** — `NRestarts=0`, MainPID stable.
- **No stuck-open bitunix paper positions** — all 6 trades resolved cleanly within their replay-cadence.
- **No `execution_mode: live` stamps on visible `extra_json`** — Path C correctly gated.
- **No `sanity_poll` activity in logs** — Commit 5 background task correctly NOT created.
- **No Phase 3-source-file-attributable error patterns** in the 455-line ERROR/WARNING sample.

---

## 4. Anomalies surfaced

1. **(P3 filed)** Audit-logger `log_event` retry exhausting 8× / 23.5h on prod, all on `agents/logger.py:26` path (NOT Decision 6.2). See BACKLOG entry filed 2026-06-02. Pre-Phase-3 baseline rate undocumented — establish baseline now, watch for post-`live` flip increase.

2. **(Operational, not Phase 3, not filed)** yfinance errors dominate the ERROR count — `$SUI-USD: possibly delisted; no price data found` + `BTCUSDT 404 quote not found`. These appear to be flakes / data-source quirks that pre-date this deploy; if persistent, separate ops investigation.

3. **(Operational, not Phase 3, not filed)** pykalshi 429s and ConnectTimeouts. Existing retry layer handles. Watch for any persistent rate-limit pattern in N+3 sessions.

---

## 5. Recommended next read

- **Day 4 mid-window probe (2026-06-04)** — same query set, compare 8-count db-lock baseline + bitunix paper distribution. Focus on whether the lock rate is trending or stable.
- **Day 7 close-out (2026-06-09)** — full window aggregate; verdict on whether `execution_mode: paper → live` flip is ready (gate-correctness sustained + DB-lock rate not climbing + bitunix paper performance acceptable).

**Out of scope for Day 2:** TP1 `target_r` calculation question (separate P3 BACKLOG); `execution_mode` flip decision (post-window).

---

## 6. Reproducibility

- `~/p3_local.sh` — full Q1-Q6 script (bash).
- `~/p3_local_a.sh` — Q1+Q2+Q3-only variant (avoids az message buffer truncation).
- `~/p3_az.sh` / `~/p3_az_a.sh` — wrappers that pass the script through `az vm run-command invoke` and tee output to `~/p3_out.txt` / `~/p3_out_a.txt`.

Both scripts read-only. No prod writes.
