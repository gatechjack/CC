# Next-session handoff — Bitunix fresh paper observation window (post vol-classifier fix)

**Verified UTC at write:** 2026-06-09T04:57:21Z (local system clock).
**Session type:** operator-supervised, read-only prod (operator runs all SSH from VPN'd terminal). No code change expected unless the window surfaces a regression.
**Mission:** observe + evaluate the fresh bitunix paper-mode observation window opened by tonight's vol-classifier fix, to produce a live-readiness verdict for the eventual `execution_mode: paper → live` flip. **This window is the empirical substitute for the skipped §4 backtest** — it is the evidence the flip decision rests on.

## Why this window exists
The P1 vol-classifier bug size-zeroed every bitunix trade whenever BTC 1D ATR ≥ 3% (dormant 6 of 7 days, 2026-06-02 22:15Z → 06-08). Fixed + deployed tonight: the Extreme cutoff now reads `extreme` (5.0%), so ATR in [3%, 5%) is tradeable again. **The prior 06-02→09 window is INVALIDATED** (contaminated by the dormancy). A clean fresh window is required before any flip is meaningful.

## State at handoff
- **origin/main HEAD:** `cef3393`. Fix chain: `7834375` (merge) = `ab0d251` (source) + `ea92d4c` (tests); `4214c23` (P3 orphaned-`high`); `4936e1e` + `cef3393` (deploy_log/BACKLOG close-out). P1 BACKLOG `81e6169` → RESOLVED.
- **Prod:** `bitunix_htf_regime.py` LF-md5 `550609fad155da002ebb470a57e16709`; MainPID `2397472`; ActiveEnter `2026-06-09 03:49:41 UTC`; healthz `{"status":"ok","mode":"PAPER"}`. No git on prod — md5 is the deploy fingerprint.
- **Fresh window start:** **2026-06-09 03:49:41 UTC** (app fully bound ~04:06Z after the RH-login boot delay; true data-start = first post-restart bitunix signal, to be confirmed in step 1).
- **Still paper:** `execution_mode: paper`, `auto_execute: false`. Unchanged.

## Read first
- `runbooks/deploy_log.md` 2026-06-09 entry (the deploy + the RH-restart-hang finding).
- Memory `[[2026-06-08-bitunix-volatility-classifier-wired-deployed]]`.
- `BACKLOG.md` P1 (RESOLVED banner) + the "Open items influencing the live-flip decision" cluster + the deploy 2026-06-02 entry's `execution_mode` flip checklist (deploy_log notable-changes: operator auth, reconcile-state review, Path-C dry-run shape, deploy-log entry).
- `reports/2026-06-09_next_session_handoff.md` (the Thread A/B/C investigation that found the bug).

## Step 1 (FIRST ACTION) — confirm activation (the F-5 watch-item)
Operator runs the proven `htf_gate_decision` probe (probe_a4 shape) for rows since 2026-06-09 03:49:41Z. **Activation PASS** = for a row with `atr_pct_d1` in [3.0, 5.0): `volatility_tier="high"`, `size_multiplier=1.0`, `hard_zero_reason=null`, AND `trade_plan_decision` / `would_have_placed` have resumed (first since 06-02 22:15Z).
- **If no bitunix signal has arrived yet** (1m bars → usually minutes): widen the window or wait; the strategy was alive at high volume pre-fix (only the vol hard-zero suppressed it), so firing should resume on the next qualifying signal.
- **If still `volatility_tier="extreme"` for ATR < 5%** → the fix did NOT take effect on prod → STOP, re-verify the prod md5 = `550609…` and that the restart loaded it.

## Step 2 — evaluate the window for live-readiness
Track over the window (operator read-only prod queries):
- **Fire rate** — `would_have_placed` / `trade_plan_decision` per day. Sanity anchor: pre-bug 06-02 fired **9×** (ATR 2.92%, "high" band). Watch for a plausible rate at current ~4% ATR, not a flood.
- **Outcomes** — TP vs SL hit on the new paper fires (`paper_trade_record` / bitunix position state). Win-rate + R distribution. Don't judge on fire-rate alone.
- **Classifier sanity** — `htf_gate_decision` vol_tier matches ATR bands (High in [3,5), Extreme ≥5); no nonsense values; regime/direction gates behaving.
- **No anomalies** — no `agent_error` spikes, no Phase-3 live-mode primitives firing in paper (A5-style hard-stop check), no reconciler-mismatch surge.

## Decision gates BEFORE any paper→live flip (none auto-satisfied by this window)
1. A clean fresh window of sufficient duration (prior practice = 7 days; **operator sets the length** — a 7-day window would close ~2026-06-16 03:49Z). 
2. Operator authorization + Backtester approval gate (CLAUDE.md §4) — the §4 gate was *skipped for the wiring fix*; a *live-flip* is a separate, higher-bar decision.
3. The deploy_log 2026-06-02 `execution_mode` flip checklist (reconcile-state review, Path-C dry-run shape, etc.).
4. **P2 Robinhood auth** is orthogonal to the bitunix flip but note its operational coupling (below).

## Open forks / operator decisions
- **Window duration** — not set. Recommend ≥7 days (matches the invalidated window's intended length). Operator decides the close date.
- **P2 — Robinhood interactive re-login** (`b2259a0`, pickle stale since 2026-05-29). Still OPEN; restart did NOT refresh it. **Operational coupling:** any restart of `trading-corp` (including a future deploy) re-hits the **~22-min device-approval startup hang** until the pickle is regenerated. Doing the interactive re-login now pre-empts that hang for the next deploy. ~5 min, MFA, operator-manual.
- **P3 — orphaned `high` threshold cleanup** (`4214c23`). Not gating; schedule whenever.

## Hard constraints / out of scope
- Stays **paper**; no `execution_mode` flip, no `auto_execute` flip without the gates above + explicit operator decision.
- No changes to `agents/risk.py`, the order path, or other bitunix strategy code.
- If a restart is needed during the window, expect the RH-login hang — approve the device push within ~10s, or re-login first.

## Recommended first message to paste next session
> Evaluate the fresh bitunix paper observation window (started 2026-06-09 03:49:41 UTC, post vol-classifier fix `7834375`). Start with Step 1 activation confirmation, then Step 2 window metrics. Read-only prod, operator runs SSH. Surface a live-readiness read; do NOT flip execution_mode.
