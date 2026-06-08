# Bitunix silence + Robinhood pickle/restart + prod-health investigation — 2026-06-08

Read-only, operator-supervised. Operator runs all prod commands from own
terminal (VPN active, home-IP allowlist). Agent provides exact commands;
no agent prod touch. Worktree: `bitunix-silence-investigation-2026-06-08`
off `origin/main` @ `58744bb`.

## Verified baseline (local, pre-prod-query)

- **prod git/source:** prod runs deployed source `395c421` (2026-06-02
  deploy); `origin/main` has since advanced to `58744bb` via docs/audit
  commits only (no code drift). deploy_log.md line 119+.
- **Deploy state (2026-06-02 ~01:40 UTC):** `MainPID=2043009`,
  `NRestarts=0`, `ActiveEnterTimestamp=Tue 2026-06-02 01:39:50 UTC`,
  `execution_mode: paper` @ `config/strategies.yaml:1022`. All Phase 3
  live-mode primitives gated off. healthz bound 200 at 01:50:52Z (T+11m
  lazy-bind).
- **Day-2 audit (probe 2026-06-03T01:08Z, ~23.5h uptime):** HEALTHY /
  GATED-CORRECTLY. Bitunix paper distribution `total=6, wins=5, losses=1,
  open=0`; shorts firing (06-02T06:51Z `cvd_bear_flip` bear win;
  07:27Z `mc_a_blood_diamond` bear loss). db-lock retry exhaustion 8×/23.5h
  on `agents/logger.py:26` path (pre-existing, P3 filed) — NOT the
  Decision-6.2 `insert_paper_trade_record` path (that stayed silent).

## Premise corrections caught at the fork (vs the session brief)

1. **NOT "zero trades since deploy."** Day-2 audit proves 6 bitunix trades
   on 06-02/06-03 with shorts firing. The real phenomenon is a *change*
   beginning ~Day 3-4 → silence window under investigation is
   **2026-06-04 → 2026-06-08**. (Brief's A2-A6 `>= 2026-06-04` ranges are
   already correct for this.)
2. **`reports/2026-06-02_phase3_day2_audit.md` is not on main** — it lives
   only on the `phase3-day2-audit-2026-06-02` worktree branch. Read from
   there.
3. **Memory `[[2026-06-02-n2-phase3-deployed-to-prod]]` does not exist.**
   No `2026-06-02*` memory file; only phase3 memory is the older
   `trading_corp_bitunix_phase3_confluence_model.md` (2026-05-11 confluence
   model). Authoritative deploy state taken from deploy_log.md instead.
4. **A5 kind names corrected.** Brief's A5 lists kinds
   (`live_exit_order_placed`, `position_state_reconciliation`, …) that do
   not all match the deploy_log's actual emitted kinds
   (`restart_resume_executed`, `orphan_broker_position_on_restart`,
   `restart_resume_case_c_deferred`). Using the Day-2 audit's validated
   LIKE filter (strict superset) so the hard-stop check can't return a
   false zero from wrong kind names.
5. **Network (CONFIRMED 2026-06-08):** Operator is on **hotel WiFi + VPN**
   (still travelling, not home). SSH:22 → `Connection timed out` (NSG
   source-IP allowlist excludes the VPN egress; same plane that failed in
   the Day-2 audit). Pivoted to the proven **`az vm run-command`** control
   plane (`rg-shared-prod` / `tc-prod-vm`), network-independent via
   `management.azure.com`. Probes split into two calls (`probe_a1.sh` =
   conn+A1+A5; `probe_a2.sh` = A2+A2b) to stay under az's ~4KB head-truncation
   limit.

## Thread A — Bitunix zero-fires

### Round 1 (unconditional): A1 + A2 + A2b + A5  → `probe_a.sh`
- A1: confirm the zero-fire window empirically.
- A2: where in the gate stack signals are filtered (ingest / PA / HTF / other).
- A2b: robustness companion — all bitunix/confluence-tagged kinds, no kind
  whitelist, to catch a wrong-kind-name blind spot in A2.
- A5: HARD-STOP check — Phase 3 live-mode primitives must NOT fire in
  paper-mode. Any rows ⇒ stop immediately.

Round 2 (conditional on A2): A3 (HTF reject specifics) / A4 (PA validator) /
A6 (regime context). Selected after Round 1 results.

### Round 1 — call 1 results (probe_a1.sh via `az vm run-command`, 2026-06-08T21:11:38Z)

Raw output captured verbatim:
- **Connectivity:** `host=tc-prod-vm`, `db=present size_bytes=1042460672` (~1.04 GB). Server clock `2026-06-08T21:11:38Z`.
- **A1 — bitunix_futures trades/day (≥ 2026-06-02):**
  ```
  day         trades
  2026-06-02  9
  ```
  Only one row. **Last trade day = 2026-06-02 (9 trades); zero on 06-03 … 06-08.**
- **A5 — Phase 3 live-mode primitives in paper-mode (HARD-STOP):** **ZERO rows. CLEAN.**
  No gate-break; Session B activation audit holds.

**Refinement vs prior premise:** silence began **2026-06-03**, a day earlier than
the "06-04 → 06-08" window in premise correction #1. The real window is
**2026-06-03 → 2026-06-08** (~6 days). Last *working* day = 06-02.

**Anomaly (flagged, non-blocking):** Day-2 audit (06-03T01:08Z) reported bitunix
resolved `total=6`; A1 shows `9` on 06-02. Probable cause: audit counted
resolved-at-snapshot while ~3 were still settling. Note, don't chase yet.

**Call-2 window widened:** probe_a2.sh A2/A2b changed `ts ≥ 06-04` → `ts ≥ 06-02`
so the gate-stack breakdown spans the working baseline (06-02) + the transition
day (06-03), which the original ≥06-04 window excluded.

**STATUS: call 1 done (A5 clean). Awaiting call 2 (A2/A2b, widened to ≥06-02).**

## Thread B — Robinhood pickle / unplanned restart
Pending Thread A stop-and-report.

## Thread C — Reconciler mismatch (c8f25d17, ac5f9c59, c2eb7cda)
Deferred unless A and B surface nothing blocking.
