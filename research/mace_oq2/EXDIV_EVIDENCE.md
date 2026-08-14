# MACE OQ-2 — Ex-Dividend Evidence + Chain Probes (Phase 0)

Session 2026-08-13 (evening, market closed). Read-only research for the 2026-08-14
3-active go-live. Board rule: **never ship a dividend-payer with guessed dates or
the guard off** — every date below is issuer-sourced with the document in this
directory, or explicitly labeled PROJECTED with the structural reason surfaced.

Candidate set (Board-ruled): target actives {GDX, XLE, IBIT}; backfill ladder
FXI -> IWM. SPY cross-checked (stays enabled:false, 2 open W33 rungs managed).

---

## 1. Issuer citations — 2026 ex-dividend dates

| Symbol | 2026 ex-dates | Source (in this dir) | Status |
|---|---|---|---|
| **XLE** | 3/23 (past), 6/22 (past), **9/21**, **12/21** | `ssga_spdr_dist_schedule_2026.pdf` — SSGA SPDR Dividend Distribution Schedule, doc SPD003792 | **ISSUER-CONFIRMED** |
| **IWM** | 3/17 (past), 6/15 (past), **9/15**, **12/15** + potential excise **12/30** | `ishares_dist_schedule.pdf` — iShares/BlackRock 2026-2028 Fund Distributions Schedule, doc GPS0826-5839861, dated Aug 13 2026 | **ISSUER-CONFIRMED** — ★ CORRECTIONS, see §2 |
| **FXI** | 6/15 (past), **12/15** + potential excise **12/30** | same iShares doc, semi-annual section, footnote (h) | **ISSUER-CONFIRMED** |
| **GDX** | **12/21 PROJECTED** (annual December payer) | `vaneck_yearend_dist_2025.pdf` — VanEck 2025 Year-End Distributions (2025 actual: ex 12/22/2025, $0.6331/sh) | **CADENCE CONFIRMED, 2026 DATE PROJECTED** — see §3 |
| **IBIT** | none — non-distributing | structural: iShares Bitcoin Trust ETF is a grantor trust holding spot bitcoin; it makes no income distributions (sponsor sells trust bitcoin for expenses). Calendar already documents this (intentionally-empty block). | **NON-PAYER** (guard on + empty calendar = no-op, fail-safe) |
| SPY (cross-check) | 3/20, 6/18, **9/18**, **12/18** | SSGA doc SPD003792 | ISSUER-CONFIRMED — matches shipped calendar exactly |

Issuer footnotes, verbatim (iShares GPS0826-5839861):
- **(g) quarterly equity:** "Quarterly distributing Equity ETFs intend to go
  ex-dividend in March, June, September, and December in each case, 3 business
  days preceding the third Friday of the month."
- **(h) semi-annual equity:** "Semi-Annual distributing Equity ETFs intend to go
  ex-dividend in June and December and align the ex-dates with the quarterly
  equity ETFs."

Stable source URLs (for future refresh):
- SSGA: https://www.ssga.com/library-content/products/fund-data/etfs/us/distribution/SPDR_Dividend_Distribution_Schedule.pdf
- VanEck 2025 year-end: https://www.vaneck.com/us/en/vaneck-etfs-yearend-dividend-distributions-2025.pdf
- iShares schedule: distributed via ishares.com resource library (doc GPS0826-5839861)

## 2. ★ IWM CORRECTIONS — BOTH shipped projections are WRONG

The shipped ex_dividend_calendar.yaml projects IWM Q3 ex **2026-09-21** and Q4 ex
**2026-12-21** ("iShares third-Monday pattern", confirmed:false). The issuer
schedule says **9/15** and **12/15** (3 business days preceding the third Friday
— footnote (g); explicit 2026 row: 3/17, 6/15, 9/15, 12/15 + 12/30 potential
excise; pays 3/20, 6/18, 9/18, 12/18, 1/5/27). The already-confirmed 3/17 and
6/15 entries match the issuer.

Consequence: **Sep 15 sits inside a Sep-18 or Sep-25 expiry rung window** — if
IWM is picked, the exdiv guard is live immediately and must carry the CORRECT
date. The old Sep-21 projection would have the guard fire 6 days LATE — i.e.
enter a rung it should have blocked and miss the force-close window. Phase 3
replaces both projections with all four issuer dates + the 12/30 excise date.

## 3. GDX — the one PROJECTED date (Board decision required)

- VanEck gold-miner ETFs distribute **annually, in late December** — 2025
  actual: ex **12/22/2025** (Monday), record 12/22, pay 12/26, $0.6331/sh
  income, 25% PFIC-sourced, no cap gains (vaneck_yearend_dist_2025.pdf).
- VanEck does not announce the next year's date until its December release —
  a confirmed 2026 date is **structurally unavailable today**, not a research gap.
- Proposal: ship **2026-12-21 (Monday) PROJECTED**, guard ON, `confirmed: false`
  with the VanEck citation, plus a calendar refresh tripwire when VanEck
  publishes (~early December).
- Why this is never load-bearing near-term: rungs are 30-45 DTE, so the earliest
  a late-December ex-date can enter any rung window is ~early November — the
  projection has ~3 months of slack before it can gate anything, and the refresh
  lands first.
- **Board call at Checkpoint 0:** whether PROJECTED-with-tripwire satisfies
  "never guessed dates" for a date that cannot exist yet, or GDX drops to the
  backfill ladder (FXI is the clean-first backfill either way).

## 4. Guard-impact summary (Sep-18 / Sep-25 rung windows)

| Symbol | Ex-div inside a September rung window? | Guard posture |
|---|---|---|
| XLE | **YES — 9/21** (inside Sep-25 window) | live immediately |
| IWM | **YES — 9/15** (inside both windows) | live immediately (corrected date) |
| FXI | no (next: 12/15) | armed, dormant until December |
| GDX | no (next: 12/21 PROJECTED) | armed, dormant; refresh due ~Dec |
| IBIT | n/a (non-payer) | no-op |

## 5. Chain probes (Robinhood MCP, read-only, 2026-08-13 after close)

All five candidates optionable with **2026-09-18 and 2026-09-25** expiries.
Spots = official 2026-08-13 closes. min_ticks: 0.05 above $3 / 0.01 below,
except IWM = 0.01 everywhere (penny pilot).

| Symbol | Spot | Chain ID | Strike grid (Sep-25, put side; calls mirror) |
|---|---|---|---|
| GDX | $88.27 | `32536616-ad9f-4037-8f78-54bd4575cbf7` | $0.50: 77.5-89 · $1: 72-125 · $5 wings to 150 |
| XLE | $61.06 | `008a5949-2349-49dc-bb22-e838f6306f2a` | $0.50: 52.5-63.5 · $1: 47-67 · **GAP: no 68/69 (67 -> 70)** |
| IBIT | $35.88 | `f4b86825-a92b-4e23-8f93-1ee813d0ec15` | $0.50: 31.5-43 · $1 to 46 · then 50/55/60/65 |
| IWM | $303.50 | `72362eb7-bc7c-4d10-9be4-48a53fffd101` | $5: 150-250 · $1: 250-323+ (some $0.50 nodes), paginated above 323 |
| FXI | $34.86 | `a54cf667-b89c-4589-9ccb-d9ab1f2b581e` | $0.50: 30.5-41 · $1 to 45 |

Reference ~short-put-zone instrument IDs (for indicative quote retries):
GDX 79p `a4ae8731-e23b-4c53-b70f-169c32f2fb36` · XLE 56p
`cfc3d208-0cb5-4d55-baed-e4c73bbe28de` · IBIT 30p
`58c7efed-b88a-40a5-9bb6-84cc508db63e` · IWM 273p
`b2896250-30bf-418a-9a3c-0f9ab8a4f048` · FXI 32p
`f5caba4a-a1f7-4b04-b964-94f4c9596d91`.

**XLE wing-gap watch item:** if a short call lands 66-67 with width 2, the wing
strike (68/69) does not exist -> strategy fail-safes to a no_wing SKIP (clean
no-trade, not an error). Noted for Phase 6 expectations; alt is w1.

**Indicative reference-strike quotes (2026-08-13 close, ~16:00-16:15 ET stamps)**
— single-leg PUT marks at the reference strikes above; NOT condor credits (the
eval builds 4 legs at ~0.20 delta shorts). Color only; the **hard gate stays the
Phase 5 intraday shadow-eval** (each active must clear its 0.30 x width condor
floor on LIVE quotes or it does not go active).

| Ref leg | Mark | Bid/Ask | Delta | IV | OI / Vol | Read |
|---|---|---|---|---|---|---|
| GDX 79p | 1.59 | 1.24/1.93 | -0.200 | 43.9% | 2 / 1 | on-target delta, rich IV — w2 floor 0.60 looks comfortably feasible; this strike thin but the book is deep nearer money |
| XLE 56p | 0.40 | 0.29/0.50 | -0.141 | 25.6% | 32 / 72 | modest IV — w2 floor 0.60 may be TIGHT at 0.20-delta shorts; ★credit-floor watch, w1 fallback plausible |
| IBIT 30p | 0.26 | 0.25/0.27 | -0.097 | 43.7% | 1154 / 122 | excellent liquidity, rich IV; 0.20-delta short sits higher (~32-33) with more credit — w1 floor 0.30 feasible |
| IWM 273p | 0.93 | 0.90/0.96 | -0.081 | 23.7% | 13 / 0 | tight penny book; 0.20-delta short ~283-285 — w3 floor 0.90 plausible |
| FXI 32p | 0.12 | 0.01/0.23 | -0.098 | 21.0% | 0 / 0 | ★VERY thin (OI 0, vol 0, 0.22-wide book) — liveness gate may bar strikes; FXI liquidity = explicit watch item for its backfill slot |

## 6. Proposed widths (risk-band ruling `0a7c1ea`: min = 50 x width; Board cap 260)

Condor max-risk = (width - credit) x 100; credit floor 0.30 x width.

| Symbol | Width | Max-risk at floor | Band | Rationale |
|---|---|---|---|---|
| GDX | **w2** | <= $140 | [100, 260] | $1 grid through the wing zone; spot ~$88 supports w2 wings |
| XLE | **w2** | <= $140 | [100, 260] | $1 grid 47-67; 68/69 gap = call-side no_wing watch item (alt: w1) |
| IBIT | **w1** | <= $70 | [50, 260] | conservative — bitcoin overnight gap risk; dense $0.50 grid |
| FXI | **w1** | <= $70 | [50, 260] | Board already ruled width 1 + remove fallback_width_dollars (validator: fallback < width) |
| IWM | **w3** | <= $210 | [150, 260] | proportional to the SPY w3 precedent at ~$300 spot; $1 grid near-money |

All max-risks clear the $260 cap; at ~$4k equity and rung_risk_pct 0.10 the
$400/rung budget covers every width at max_contracts 1.

## 7. Proposed symbol blocks (draft for Phase 3 — final roster = Board pick)

```yaml
# Target actives (Board pick at deploy checkpoint; backfill ladder FXI -> IWM)
GDX:  {enabled: true,  width_dollars: 2, fallback_width_dollars: 1,
       blackout_event_types: [FOMC], exdiv_guard: true}
       # annual Dec payer; 2026-12-21 PROJECTED, refresh ~Dec; FOMC = gold-vol event
XLE:  {enabled: true,  width_dollars: 2, fallback_width_dollars: 1,
       blackout_event_types: [OPEC], exdiv_guard: true}
       # 9/21 + 12/21 issuer-confirmed (SSGA SPD003792); OPEC per USO precedent
IBIT: {enabled: true,  width_dollars: 1, blackout_event_types: [],
       exdiv_guard: true}
       # OQ-3 reversal: overflow_only REMOVED. Non-payer; empty calendar = no-op.
       # NOTE: IVR was 9.1 at stage-A — may legitimately skip on the IVR>=25 floor day 1.

# Backfills (enabled only if promoted by the roster pick)
FXI:  {enabled: false, width_dollars: 1, blackout_event_types: [PBOC, LPR_FIX],
       exdiv_guard: true}
       # Board-ruled w1; fallback_width_dollars REMOVED (validator: fallback < width, 1<1 fails)
       # 12/15 + 12/30 issuer-confirmed (iShares GPS0826-5839861)
IWM:  {enabled: false, width_dollars: 3, fallback_width_dollars: 2,
       blackout_event_types: [FOMC, CPI], exdiv_guard: true}
       # 3/17 6/15 9/15 12/15 + 12/30 issuer-confirmed — BOTH old projections were WRONG

# SPY/GLD -> enabled:false (SPY 2 open W33 rungs stay managed; manage/exit never read enabled)
# Legacy TLT/USO/EWZ blocks stay defined enabled:false (Board ruling)
```

Params delta (Board-ruled, Phase 3): rung_risk_pct 0.055 -> 0.10 ·
deployment_target_pct 0.80 -> 0.95 · risk_band_max_usd 250 -> 260 ·
weekly_new_rungs_per_symbol 2 -> 1 · entry_max_attempts 5 -> 2 ·
entry_fill_wait_sec 60 -> 30 · (max_rungs 5 / max_contracts 1 unchanged) ·
keep vestigial-but-validated ibit_overflow_cap + overflow_max_per_symbol_session.

Calendar entries to ship (Phase 3, ex_dividend_calendar.yaml, citations in comments):
- XLE (new block): 2026-09-21 (pay 9/23), 2026-12-21 (pay 12/23) — confirmed:true
- IWM: REPLACE the 9/21 + 12/21 projections with 9/15 (pay 9/18) + 12/15 (pay
  12/18) confirmed:true; add 12/30 potential excise (pay 1/5/27); keep 3/17 + 6/15
- FXI (fills the STRUCTURED-EMPTY block): 2026-12-15, 2026-12-30 — confirmed:true
- GDX (new block): 2026-12-21 — confirmed:false, source VanEck 2025 actual
  12/22/2025 + annual-December cadence; REFRESH when VanEck publishes (~Dec)
- SPY: no change (shipped dates match the issuer exactly)
- IBIT: keep intentionally-empty; update the comment (overflow-only language obsolete)
