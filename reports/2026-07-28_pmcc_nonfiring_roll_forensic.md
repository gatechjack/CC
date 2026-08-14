# PMCC roll forensic — 2026-07-28 (READ-ONLY)

**Scope:** why 3 high-volume PMCC names did not roll today despite repeated attempts.
**Method:** prod `audit_event` (SQLite, `?mode=ro`, via Azure RunCommand over 443 — port 22 blocked on hotel WiFi) + Robinhood MCP for broker ground truth + deployed `config/strategies.yaml` (read-only). **Nothing placed; no re-roll; auto_execute:false unchanged; no SQL/halt change.** Prod HEAD `e97ebb0`, PID unchanged.

---

## 0. Verdict (TL;DR)

The three names that never rolled via the engine today = **HOOD, RIOT, BLSH**. They failed for **three DIFFERENT reasons**, and **only ONE is a real defect**:

| Name | Binding gate | Classification | One-line cause |
|---|---|---|---|
| **RIOT** | earnings | **FALSE NEGATIVE — real data defect** | Engine blocked on earnings **07-30**; broker-verified date is **08-05** (8d out, outside the 7d buffer). Stale/incorrect EODHD/yfinance date. RIOT should have been roll-eligible. |
| **HOOD** | earnings | **JUSTIFIED** | Earnings **07-29 PM (verified)**, 1 day away. Correctly blocked — a roll opens new short premium across the print. |
| **BLSH** | liquidity | **JUSTIFIED — genuine liquidity limit** | Entire 08-07 OTM chain is objectively thin (OI single/double-digit, vol 0–29, spreads 55–170%). Gate reading matches broker exactly; no misread. |

**The liquidity gate did NOT produce a single false-negative today.** The operator's hypothesized scan→target-selection→liquidity defect did not occur on these three. The one true false-negative (RIOT) is in the **earnings data feed**, a different subsystem entirely.

Dominant actionable defect: **stale earnings date for RIOT** (EODHD/yfinance vs broker-verified). Fix = brokerage-first earnings confirmation.

---

## 1. Full roll ledger — 2026-07-28 (every PMCC evaluation, UTC)

Universe scanned today (`universe_source: positions`): **BLSH, HOOD, IREN, RIOT, RKLB, SMR, STRC** (7 names).
Pre-open triage @12:31:54 → `{"near_dte_days":5,"legs":[]}` (nothing within 5 DTE; all real evaluation happened in the post-settle actionable passes).

| Time (UTC) | Name | Kind | Outcome | Detail |
|---|---|---|---|---|
| 13:44:08 | STRC | roll_aborted | abort (open) | `sparse_chain_no_leap_for_open` — no LEAP expiry ≥365d (not a roll; open-eligibility) |
| 13:47:29 | RKLB | roll_aborted | abort | `sparse_chain_no_weekly` tgt 08-07, cons=86 liq=0, **failed_by={liveness:47, volume:37, spread:2}** (PRE/early-settle) |
| **14:48:27** | **RKLB** | roll_gates | **FIRED** | selection ok, credit clear, net **+1.31/+1.39**, override=`hold_override` → filled 08-07 @14:48:31 |
| 14:49:08 | BLSH | roll_aborted | abort | `sparse_chain_no_weekly` tgt 08-07, cons=36 liq=0, **failed_by={liveness:34, volume:2}** |
| 14:49:19 | HOOD | roll_aborted | abort | `earnings_window` — "earnings 07-29 (1d, buffer 7d)" |
| **14:49:26** | **IREN** | roll_gates | **FIRED** | net **+1.83/+1.89** → filled 08-07 @14:49:30 |
| **14:49:34** | **SMR** | roll_gates | **FIRED** | net **+0.12/+0.13** → filled 08-07 @14:49:38 |
| 14:49:41 | RIOT | roll_aborted | abort | `earnings_window` — "earnings **07-30** (2d, buffer 7d)" ← WRONG date |
| 14:57:27 | BLSH | roll_aborted | abort | `sparse_chain_no_weekly` (same as 14:49) |
| 14:57:38 | HOOD | roll_aborted | abort | `earnings_window` |
| 15:44–20:28 | HOOD ×7, RIOT ×1 | roll_aborted | abort | repeat `earnings_window` (HOOD 11 total, RIOT 2 total) |

**Fired (engine → HITL-approved → filled):** RKLB, IREN, SMR — all rolled into 2026-08-07 (a standard Friday).
**Never fired (the 3):** HOOD (11 aborts), RIOT (2 aborts), BLSH (2 aborts).
**Manual operator action after aborts:** RIOT 08-07 ×4 opened 16:08; BLSH 08-07 ×1 (C26 @ $0.30) opened 16:09 — the operator overrode both aborts by hand.

Kind counts (robinhood_pmcc, today): `pmcc_morning_triage`=1, `pmcc_roll_aborted`=17, `pmcc_roll_gates`=3, `pmcc_combo_filled`=0.

---

## 2. The three non-firing names — ground truth + classification

### RIOT — FALSE NEGATIVE (earnings data defect)  ★ the real bug
- **Engine saw:** `earnings on 2026-07-30 (2d away, buffer=7d)` → B9 earnings gate blocked BEFORE selection/liquidity ran.
- **Broker ground truth (`get_earnings_results` RIOT):** Q2-2026 report **2026-08-05, timing AM, verified=true**. Today 07-28 → **8 days out → OUTSIDE the 7-day buffer → would be "clear".**
- **Corroboration:** RIOT is absent from Robinhood's 4-day earnings calendar (07-28→08-01). RIOT's Q2-2025 print was **2025-07-31** — the stale source almost certainly carried last year's late-July date.
- **Chain liquidity:** not the binding gate (never evaluated), and RIOT's chain is deep — the operator filled **4 contracts** manually at 08-07 at 16:08. RIOT would have passed liquidity.
- **Verdict:** **FALSE NEGATIVE.** RIOT should have been allowed to roll. Cause = incorrect earnings date from `get_next_earnings` (EODHD primary + yfinance fallback, 24h cache), which disagrees with the broker's verified date by a full week.

### HOOD — JUSTIFIED (earnings, correct data)
- **Engine saw:** `earnings on 2026-07-29 (1d away)` → blocked.
- **Broker ground truth:** HOOD Q2-2026 report **2026-07-29, PM, verified=true** (RH earnings calendar). **Correct.**
- **Position:** short 2× **07-31** C (opened 07-08); LEAP 2027-01-15. A roll would close 07-31 and open a NEW further-dated short spanning the 07-29 print → exactly the "new short premium within 7 DTE of earnings" the B9 rule forbids.
- **Verdict:** **JUSTIFIED.** Chain is liquid; block is earnings, by design. Correct to hold pre-earnings; the roll clears naturally on 07-30 (post-print). No defect.

### BLSH — JUSTIFIED (genuine liquidity limit)
- **Engine saw:** `sparse_chain_no_weekly`, target **2026-08-07** (Friday), 36 candidates, **0 liquid**, `failed_by={liveness:34, volume:2}`.
- **Broker ground truth — BLSH 2026-08-07 calls, EOD 07-28 (spot 22.675):**

| Strike | Δ | OI | Vol | Bid/Ask | Spread% |
|---|---|---|---|---|---|
| 23 | .49 | 14 | 29 | 0.85/1.50 | 55% |
| 24 | .35 | 83 | 2 | 0.36/0.86 | 82% |
| 24.5 | .31 | 11 | 14 | 0.30/0.81 | 92% |
| 25 | .26 | 12 | 0 | 0.06/0.83 | 173% |
| **26 (short sold manually)** | .19 | 59 | 7 | 0.07/0.55 | 155% |
| 27 | .12 | 36 | 6 | 0.10/0.25 | 86% |
| 30 | .01 | 103 | 0 | 0/0.49 | no-bid |

- No strike in the ~0.30-delta target region reaches **OI≥100**; volumes 0–29; **every OTM spread 55–170%** of mid. The two strikes with OI≥100 (`volume:2` bucket) are deep junk (e.g. C30, Δ0.01, vol 0). Gate reading = broker reading; **no data-feed discrepancy.**
- **Target-selection not at fault:** 08-07 is the same standard Friday RKLB/IREN/SMR filled; BLSH's later weeklies (08-14, 08-21) are *thinner*, not more liquid, so an expiry fallback would not have rescued it.
- The operator sold **1 lot** of C26 @ $0.30 by hand (mid-spread, market-maker fill). A human can work a 1-lot into a 155%-wide market; the conservative automated gate (correctly) will not.
- **Verdict:** **JUSTIFIED — genuine liquidity limit.** BLSH options are objectively thin. Not a false negative, not a misread, not a selection bug.

---

## 3. Root cause — hypotheses tested

- **(a) Target-expiry picks a thin weekly.** `_find_best_weekly` selects `weekly_dates[0]` (earliest qualifying expiry) and runs the liquidity gate on THAT expiry only, with **no fallback to a later liquid weekly** (pmcc_robinhood.py:3739). **Not the cause of the 3:** 08-07 is a standard Friday, the same one the fired names used; BLSH's later expiries are thinner. **Latent gap** — see fix P2 (it did cause RKLB's benign pre-settle abort).
- **(b) Data-feed failure.** **CONFIRMED for RIOT — but on the EARNINGS feed, not liquidity.** EODHD/yfinance returned 07-30 vs broker-verified 08-05. The liquidity feed showed no discrepancy (BLSH gate == broker).
- **(c) Threshold mismatch (spread cap / vol floor).** BLSH's spreads are genuinely wide (55–170%, absolute $0.20–0.50 — not penny-tight), so an absolute-cents spread allowance would NOT have fired BLSH. However the independent **`min_avg_volume: 50` same-day floor rejects high-OI strikes on low-volume reads** — visible in RKLB's 13:47 abort (**37 strikes had OI≥100 but were failed by the volume floor** because they hadn't traded 50 lots that early). Real fragility (fix P2), but not what blocked the 3.
- **(d) Scan-split / liveness timing.** RKLB's 13:47 abort was a pre/early-settle stale-quote artifact that **self-healed** post-settle (fired 14:48 on the same expiry). BLSH aborted at 14:49/14:57 — *inside the same live window RKLB fired in* — so BLSH is not a timing artifact. HOOD/RIOT aborts are earnings, timing-independent. Not the cause of the 3.
- **(e) Exception / silent failure.** None. Every abort carries a clean structured reason; no tracebacks in payloads.

**Single dominant defect: (b) stale earnings date → RIOT false block.** HOOD and BLSH are correct behavior.

---

## 4. Contrast: fired vs non-fired

| | Target expiry | Earnings gate | Liquidity gate | Result |
|---|---|---|---|---|
| RKLB/IREN/SMR (fired) | 08-07 Fri | clear | pass | rolled |
| RIOT (blocked) | (never reached) | **blocked on WRONG 07-30** | would pass | **false block** |
| HOOD (blocked) | (never reached) | blocked on real 07-29 | would pass | correct hold |
| BLSH (blocked) | 08-07 Fri | clear | **fail — genuinely thin** | correct hold |

The only variable that separates RIOT from the fired names is a **bad earnings date**. The only variable separating BLSH is a **genuinely thinner underlying** on the identical expiry. That localizes the sole defect to the earnings feed.

---

## 5. Prioritized fixes (PROPOSE — not built)

**P1 — REAL DEFECT: earnings-date accuracy (would have let RIOT roll).**
The B9 gate trusts `get_next_earnings` (EODHD primary + yfinance fallback, 24h cache), which returned a date a full week early for RIOT. Per the standing brokerage-first data policy:
- Cross-check / prefer **Robinhood's verified `get_earnings_results` date** for the runtime block (brokerage-first; the block's evidence should come from the same broker it trades on).
- When EODHD/yfinance and the broker disagree, **log the disagreement** and prefer the verified broker date rather than silently blocking.
- Secondary: revisit the EODHD cache TTL / refresh so a moved date propagates within the trading day.
- *Effect:* RIOT (08-05 real) clears the 7d buffer → rolls (subject to liquidity/credit). This is the single change that fixes today's only false negative.

**P2 — LATENT robustness (did NOT bind today; caused RKLB's self-healed morning abort).**
Two coupled gaps in the liquidity path:
- `_find_best_weekly` aborts on the first (earliest) expiry if illiquid, with **no fallback to the next liquid weekly** (e.g., roll to the standard Friday when a newer front weekly is thin). Add a liquid-expiry fallback loop within the DTE window.
- The **independent `min_avg_volume:50` same-day floor** rejects established high-OI strikes on a slow-volume read (early session / quiet day). Consider making the volume floor **conditional on low OI** (volume as the bypass for low-OI liveness, not an independent gate on high-OI strikes), or OI-primary + spread.
- *Note:* neither would have changed the 3 (BLSH's later expiries are thinner; its spreads are genuinely wide). Value is preventing *future* false aborts on names scanned only once, pre-settle.

**P3 — POLICY, not a bug: thin-name (BLSH) handling.**
BLSH is a genuine liquidity limit; the gate is protecting against bad fills into 55–170% spreads. If the Board wants the engine to roll 1-lot positions on thin names (as the operator did by hand), that requires deliberately relaxing the gate (size-aware liveness bypass and/or absolute-cents spread allowance for penny premiums) — which **increases execution risk**. Recommend leaving the gate as-is, or adding a narrowly-scoped "thin-name → surface for manual confirm" path rather than auto-rolling. Board decision, not a code fix.

**Genuine liquidity limit vs defect (explicit):**
- **Genuine limit:** BLSH thin chain (correct abort).
- **Correct-by-design:** HOOD earnings hold.
- **Real code/data defect:** RIOT stale earnings date (P1).
- **Latent code fragility:** target-expiry no-fallback + unconditional volume floor (P2).

---

## 6. Secondary anomaly (flagged; NOT investigated — scope)

The account (461391328) holds short calls on **TSLA (07-31), MSTR (07-31 & 08-21), OPEN (08-07), CIFR (08-07), BULL (08-14)** — all with LEAPs and within the roll window — yet **none produced a roll audit today** (0 attempts, not the "multiple attempts" the 3 had). With `universe_source: positions`, held legs are supposed to be managed regardless of universe. Most likely benign (a HOLD decision is not written to `pmcc_roll_aborted`/`pmcc_roll_gates`), but it could be a coverage gap. **Not part of the 3; not investigated.** Offer: pull the scan decision journal (systemd) to confirm HOLD-vs-skip if the Board wants.

---

## 7. Attestation
Read-only throughout: SQLite `?mode=ro`; Robinhood MCP read calls only; config `grep`. No orders reviewed/placed/cancelled; no re-roll; `auto_execute:false` verified unchanged; no SQL/halt/config mutation. RunCommand executed via the VM agent as root (documented "RunCommand-root-no-sudo" path) — **no `sudo`**.
