# Polymarket whale scouts — UFC / NFL / NBA / Fed (2026-08-21)

READ-ONLY research. Method: discover bettors via per-market `/trades`, then **NET-score** (NET = SELL+REDEEM−BUY,
realized net of costs; NOT gross) with real win/loss from `outcomePrices`. Ranked by **NET ROI** (win% is chalk).
Nothing traded/rostered from the scout itself; paper-farm adds tracked separately.

## Cross-scout pattern (the meta-finding)
- **Low-frequency + low-volume whales (UFC) → complete records → clean shortlist.**
- **High-volume whales (NBA, Fed generalists) → category history TRUNCATES at the 5,000-row `/activity` cap → method breaks.**
- **Chalk recurs everywhere:** high win% ≠ edge (favorites win ~64% MLB / ~66% NFL / ~68% NBA; Fed outcomes ~priced-in). Rank on NET ROI.
- The truncation ceiling is what motivated the `/closed-positions` characterization (see CLOSED_POSITIONS_API_FINDINGS.md) — the real fix.

## UFC — CLEAN (feasibility GO: Kalshi KXUFCFIGHT + title; Poly `ufc-{fighters}-date`, tag_id=279)
Complete records (UFC whales are low-volume). Shortlist:
- **Kh4mz4t** `0x52f454…2b15` — n=210(204sf), 64% win, **+$14.1k / +9% ROI**, complete. Best-evidenced.
- **STC14** `0x99b1b0…196a` — n=102, 66%, +$1.8k, complete.
- Tier-2 (high net but TRUNCATED/partial): 000why000 (+$17k/73%), 4751346 (+$29k/80%), kutsumiakia (83%/+$2.5k).
- Excluded net-losers: evanng −$13.7k, csgod −$9.5k, FRANK.THE.TANK (27% win). Chalk lens validated.

## NFL — 1 REAL + 1 MIRAGE (Kalshi KXNFLGAME; Poly `nfl-{a}-{h}-date`, series_id=1)
Data = **2024-25 season (~1yr stale)**; 2025-26 not enumerable on Poly.
- **FordBronco** `0x75e091…fe50` — n=120(119reg), 73% win, **+$28.2k / +5.6% ROI**, complete. The one clean candidate.
- **AIisTheNewWD** `0x2fb0f88…abf8` — +$103k/80% but **39-0 is a TRUNCATED MIRAGE** (partial record; losses cut off). Unverified.
- Rest: chalk (SadMan 98%-win/−4.4% ROI) or net-losers (test99 −$173k).

## NBA — METHOD BROKE (Kalshi KXNBAGAME [exact series, disambiguates KXNBAGAMES/GAME7]; Poly series_id=2/tag_id=745)
Data 2024-25 season (~13mo). **ALL 20 candidates TRUNCATED** (NBA whales too high-volume); 6 returned n=0 (unscoreable, incl S-Works despite $4.2M flow). No clean verdict.
- Chalk exemplars: peter003 21-1/95%-win but **−63% ROI**; tpu-634 193-21/90% but −2%.
- Only intriguing (PARTIAL): **BetMechanic** `0xa6a856…5009` +$94k/+11% ROI/n=71-reg (cross-sport). Farmed to observe forward.

## Fed rate decisions — MOSTLY CHALK (Kalshi KXFEDDECISION; Poly `fed-decision-in-{month}`, tag_id=100196)
Data **2024-05 → 2026-07, CURRENT (freshest scout), 19 FOMC meetings**. Resolution mapping clean:
per-meeting bands ↔ `KXFEDDECISION-{YYMMM}-{C26/C25/H0/H25/H26}`. (Excluded: `KXFED-T{level}` per-level, `fed-rate-cut-by` cumulative, dissent-granular, ECB.)
Fed-specific metric **win_px** = avg entry price on wins (≥0.85 chalk/priced-in; <0.70 called-contested = edge).
- Only complete large-n record: **scanner** (18-0/12mtgs) but **pure chalk** (win_px 0.98, +1.3% ROI) — safe parker, no edge.
- Chalk-loser: d1k21 20-2/91%-win but **−29% ROI / −$168k**.
- Intriguing but PARTIAL/thin: **Kickstand7** `0xd1acd3…08d5` (win_px **0.77** = the one contested-forecasting profile, +8.9% ROI, but **n=3**); **pako** `0x71edff…d338` (+$629k/+34% ROI/n=8, biggest net, truncated).

## Verdict
UFC farmable now; NFL/NBA/Fed limited by staleness + truncation. The durable fix is the `/closed-positions` data
foundation (complete records, direct realizedPnl) — see the companion findings doc.
