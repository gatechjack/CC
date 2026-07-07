# Session Handoff — 2026-07-06 (SFP forensic + pivot-sensitivity + gate-variants arc)

**One-liner:** Operator saw missed SFP setups; a forensic + two backtest spikes diagnosed whether SFP
was broken, mistuned, or gated too aggressively. **Verdict: no live bug; SFP mechanism + regime gate +
pivot(50) jointly CERTIFIED; the "gate refuses good trades" and "shorter pivot helps" hypotheses both
REFUTED — all conditioned on a strong-bear window.** Read-only arc; **zero prod writes.**

> (Earlier the SAME calendar session did deploy the bracket min-leg fix — restart 07-06 03:28 → PID 81690,
> merged to main `a866377`. That is closed. The forensic/spike arc below happened entirely after it, read-only.)

## Goal
Operator observed profitable SFP setups on chart that the strategy didn't take. Diagnose: broken detector,
mistuned pivot, or over-aggressive regime gate?

## Forensic (Part 1) — 8 operator-named setups, ts-anchored on prod Bitunix
- **3 detected-and-gated (Bucket-A):** 2 profitable counter-trend shorts (BTC 7/3, XRP 7/3) refused as
  `sfp_skip_counter_trend` (short into up-regime). 1 self-invalidated (BTC 7/4).
- **3 not-detected (Bucket-B):** BTC 7/2, SOL 6/29, SOL 6/28 — the swings are **not SFP-shaped at ANY pivot
  degree {5,10,20,50}** (continuations that closed *through* the swing, or near-miss/break). A different pattern.
- **2 inconclusive → resolved:** ETH 7/1 21:45 = **pre-deploy** (reflected short engine went live 07-02 02:10,
  ~4.5h after the setup — commit aec6c78 authored 07-01 19:45 but prod got it 07-02). SOL 7/3 = audit-gap
  (short watch transitions unpersisted).
- **NO live detector bug found.** Data complete, no gaps. Detector md5 `91fd7672` throughout.

## Pivot-sensitivity spike (Part 2) — Coinbase INTX 230d, pivot ∈ {5,10,20,50}, live gate ON
None beat pivot(50) under the pre-registered drift-embedding null. Shorts are bear-beta; longs negative.
**Keep pivot(50).**

## Gate-variants spike — pivot ∈ {50,25} × gate ∈ {hard, soft, none}
- **Gate is aggregate-correct:** removing/softening loses money (p50 hard +1→none −4; p25 hard +3→none −5;
  soft ties totalR but dilutes over 9× trades).
- **pivot(25) doesn't earn once freed** (p25-none < p50-none).
- **Bucket-A refused class doesn't clear the null at n≥30** — the 2 forensic profitable shorts were anecdotal
  winners inside a bear-beta class, not a mechanism.
- **5-part success bar: NONE adopt. GATE VINDICATED.**

## LEDGER (durable — mirrors memory)
*gate + pivot(50) jointly certified vs pivot(25) and vs soft/no-gate on 230d Coinbase-INTX 15m + Bitunix-3m +
drift-null (~47d signal window, strong-bear regime: BTC −27 / ETH −30 / SOL −19 / XRP −28%). Alternatives
{5,10,20,25} + soft/none do not earn the swap. Re-validate in a non-bear window before treating the gate's
value or the short-side as regime-general.* Detector md5 `91fd7672`.

## Open forward items
1. **★ Live regime-flip watch** (highest value) — all findings bear-conditioned. **07-07: regime flipped all 4
   coins → RANGE**; watch for first SFP fires + first-fill A/B.
2. **Bucket-B pattern characterization** — future division candidate (NOT SFP tuning). P3, not urgent.
3. **Short-watch transition persistence** — small observer-only additive audit-gap fix. P4.
4. **Futures SL-trail INFO breadcrumb** passive validation. P4.
5. **Merge debt** — 07-02 SFP deploy CRLF hybrid (main.py + strategies.yaml, branch b849964); deliberate
   rebase-onto-prod session. Not urgent.

## Prod state at session end (verified read-only 2026-07-07 04:45 UTC)
- Engine **PID 81690**, NRestarts=0, active/running, boot **2026-07-06 03:28:07 UTC**, **0 tracebacks since boot** (~25h).
- Both bitunix reconcilers **clean** (0 halts/divergence/orphan since boot).
- **SFP flat** all 4 coins (last live order 2026-06-28; none since — gated / no setups).
- Futures active (normal; last order 2026-07-07 02:30).
- **Regime seed live:** BTC/ETH/SOL/XRP all **RANGE** (updated 04:45; flipped from up-bias). 52 regime-flips logged; research_log empty.
- **No prod writes this session** (forensic/spike arc read-only).

## Artifacts
- Reports + spike code: branch `sfp-missed-setup-forensic-2026-07-06` @`5aa2937` (pushed unmerged; 8 commits ahead of main).
  `reports/2026-07-06_pivot_sensitivity_spike.md` (all 3 parts), forensic report, `spike/` harness.
- This handoff + BACKLOG update + next-session prompt: branch `session-wrap-2026-07-06` (off main a866377).
- Memory: `bitunix-sfp-mechanism-certified-2026-07-06`, `bucket-b-non-sfp-pattern-2026-07-06`, `drift-embedding-null-standard-tool`.
- **origin/main untouched = `a866377`.** No merges. No memory-forcing.
