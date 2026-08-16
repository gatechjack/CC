# CP1 — Poly-MLB → Kalshi matching: validation on real history

**Status:** complete, read-only, zero orders. **Branch:** `poly-kalshi-mlb-phase1-2026-08-15`.
**Date:** 2026-08-15. Nothing placed, nothing deployed, `kalshi_copy_trader.py` and
`sports_team_mapping.py` byte-unchanged (verified in diff).

This is the real run. (An earlier message in the build thread cited an "87.3% / 1,203-bet"
CP1 result with a "-1/-2 doubleheader convention" — that was **fabricated and discarded**;
none of it came from this work. The numbers below are measured.)

---

## 1. What was validated, and how

The question CP1 answers: **does a parsed Polymarket whale MLB bet map to the correct real
Kalshi contract?** Matching is deterministic and stable, so it's validated in ONE pass against
real history — real whale bets vs real Kalshi contracts, not synthetic examples.

- **Poly side (public API, local):** full activity for the four discovered MLB whales
  (SDTrading, xifutloong3, monkeymashingkeyboard, 0x0x23kjookhai), deduped to distinct markets.
- **Kalshi side (authed, read-only):** open + ~7 weeks of settled `KXMLBGAME` contracts.
- **Matcher:** new pure module `trading_corp/data/mlb_poly_kalshi_match.py` (17 unit tests).
  Reuses the existing 30-team codebook `sports_team_mapping.MLB_TEAMS`; canonicalizes BOTH
  sides to full club name (absorbs Poly `ari`/`cws`/`sd` vs Kalshi `AZ`/`CWS`/`SD` divergence);
  keys on `(game_date, {away_name, home_name})`.

**Auth (step 0):** confirmed local Kalshi API works for BOTH accounts before validating —
primary (inline in `cc/.env`) and **KAREN** (resolved from Key Vault via DefaultAzureCredential/
`az login`). Both: cred resolution + RSA request signing + a signed account read + a contract
read, all green. No signing friction. This de-risks CP2/CP5 (the new strategy uses KAREN).

---

## 2. CP1 output table (real numbers)

**Data processed:** 14,939 whale activity rows → **1,932 distinct Poly markets** (deduped by
`(whale, slug)`; the match rate is over distinct markets, never rows); **907 distinct
real Kalshi games** across 68 dates (2026-06-09 → 2026-08-18).

| Bucket | Count | Note |
|---|--:|---|
| **Matched cleanly** (ML → unique real Kalshi contract) | **578** | 99.8% of in-window ML |
| Doubleheader (flagged ambiguous, NOT guessed) | 1 | see §4 |
| Failed to match (unrecognized team / unparseable / no-contract) | **0** | — |
| Skip — totals (O/U) | 411 | Kalshi is ML-only; no equivalent |
| Skip — spreads (run line) | 131 | Kalshi is ML-only; no equivalent |
| Skip — props (NRFI etc.) | 3 | out of scope |
| Skip — MLB futures/series | 0 | none in the whales' captured window |
| Non-MLB (other sports the whales also trade) | 475 | filtered out |
| Out-of-Kalshi-window ML (older than 2026-06-09) | 333 | parsed fine; no contract to round-trip |

**Moneyline funnel:** 912 ML single-game markets total → **579 within the Kalshi round-trip
window** → **578 matched to the correct unique contract (99.8%)**, 1 doubleheader (flagged),
**0 failures, 0 false no-contract.** Excluding the one flagged doubleheader, **100% of resolvable
in-window ML bets matched correctly.**

Per whale (matched): SDTrading 229, 0x0x23kjookhai 144, xifutloong3 110, monkeymashingkeyboard 95.

---

## 3. Confidence-score distribution (you set the threshold from this)

Per your instruction, **no threshold is preset.** Scored over ML match attempts:

| confidence | count | meaning |
|--:|--:|---|
| **1.00** | 559 | exact full-name side match |
| **0.97** | 19 | side resolved by nickname (all "Athletics" = Oakland A's) — all correct |
| **0.50** | 1 | the doubleheader (ambiguous, flagged) |

Cleanly separated: **578 clean matches at ≥0.97, one flagged case at 0.50, nothing in between,
nothing below 0.50.** The 0.97 tier is fully correct (Oakland's "Athletics"-only branding;
spot-checked). A threshold anywhere in (0.50, 0.97] cleanly admits every correct match and
excludes only the doubleheader — your call at review.

---

## 4. Doubleheader convention — studied on real data (Q2)

There were **3 real doubleheaders** in the window. Both platforms were examined:

**Kalshi — distinguishes deterministically.** The team blob carries a `G1`/`G2` suffix, plus a
distinct start-time (HHMM):
```
2026-08-17 STL/CIN   KXMLBGAME-26AUG171340STLCING1-*   (G1, 13:40)   KXMLBGAME-26AUG171840STLCING2-*   (G2, 18:40)
2026-07-17 TB/BOS    KXMLBGAME-26JUL171335TBBOSG1-*     (G1, 13:35)   KXMLBGAME-26JUL171910TBBOSG2-*    (G2, 19:10)
2026-07-07 MIL/STL   KXMLBGAME-26JUL071415MILSTLG1-*    (G1, 14:15)   KXMLBGAME-26JUL071945MILSTLG2-*   (G2, 19:45)
```

**Polymarket — does NOT distinguish.** One `mlb-{away}-{home}-{date}` market per matchup-date;
no game number in slug or title (e.g. `mlb-stl-cin-2026-08-17` is a single market with
`gameStartTime 22:40 UTC`; `mlb-tb-bos-2026-07-17` had 16 markets, all for one game). The whales
bet only one market per DH matchup — **0 Poly same-matchup-date multi-slug groups.** And the
`gameStartTime` lives in gamma metadata, **not in the activity row the live loop sees.**

**Classification: PARTIALLY RESOLVABLE → flagged for your decision (not guessed).** Kalshi
distinguishes; Poly doesn't at the slug/activity level. A start-time bridge (Poly `gameStartTime`
via an extra gamma lookup → nearest Kalshi `G1`/`G2` HHMM) *could* resolve it deterministically,
but it needs a metadata hop that isn't in the activity row, and the sample is tiny (1 whale-bet DH
in ~10 weeks). Per your rule I did **not** build a rule on this.
- **Interim behavior (built):** the matcher DETECTS the DH condition and returns
  `doubleheader_ambiguous` with both candidate contracts — it never auto-picks. Unit-tested with
  real `G1`/`G2` tickers.
- **Your options at CP-review:** (a) skip-and-log DH at launch (cost ≈ 0.2% of in-window ML), or
  (b) authorize the start-time bridge (I'll build + validate it before it can auto-trade).

**Bug this surfaced (fixed):** the shared `sports_team_mapping.parse_sports_ticker` (its `[A-Z]+`
blob) silently drops `G1`/`G2` tickers, which had made all 3 doubleheaders read as
"no contract." I did **not** edit the shared parser (it's used by other live strategies); instead
the new module ships a DH-aware sibling parser. Without this, a live strategy would have silently
mis-handled every doubleheader.

---

## 5. Spot-check pairs (eyeball correctness)

```
mlb-col-sf-2026-08-16    "San Francisco Giants" -> KXMLBGAME-26AUG161605COLSF-SF     (1.0)
mlb-tex-oak-2026-08-16   "Texas Rangers"        -> KXMLBGAME-26AUG161605TEXATH-TEX   (1.0)  Poly oak == Kalshi ATH
mlb-ari-atl-2026-08-16   "Arizona Diamondbacks" -> KXMLBGAME-26AUG161335AZATL-AZ      (1.0)  Poly ari == Kalshi AZ
mlb-bal-tb-2026-08-16    "Baltimore Orioles"    -> KXMLBGAME-26AUG161215BALTB-BAL     (1.0)
mlb-wsh-nym-2026-08-15   "Washington Nationals" -> KXMLBGAME-26AUG151610WSHNYM-WSH    (1.0)
mlb-sd-cle-2026-08-15    "San Diego Padres"     -> KXMLBGAME-26AUG151910SDCLE-SD      (1.0)  Poly sd == Kalshi SD
mlb-cws-det-2026-08-14   "Chicago White Sox"    -> KXMLBGAME-26AUG141840CWSDET-CWS    (1.0)
mlb-cin-cws-2026-08-13   "Cincinnati Reds"      -> KXMLBGAME-26AUG131410CINCWS-CIN    (1.0)
mlb-tb-oak-2026-08-10    "Athletics"            -> KXMLBGAME-26AUG102140TBATH-ATH     (0.97) nickname
mlb-oak-bos-2026-08-08   "Athletics"            -> KXMLBGAME-26AUG081610ATHBOS-ATH    (0.97) nickname
```

---

## 6. Honest limitations / findings for downstream

- **Kalshi settled retention ≈ 7 weeks.** Round-trip against real contracts covers 2026-06-09→
  08-18; 333 older ML bets parsed fine but predate the window (not failures, just no contract to
  check against). Matching is deterministic, so window coverage doesn't change the conclusion.
- **Poly activity API hard-caps at offset 5000** (`max historical activity offset exceeded`) —
  monkeymashingkeyboard and 0x0x23kjookhai history truncated to their most-recent ~5,000 fills.
  This is the ~5,500 cap the discovery doc noted; **relevant to CP4** (the detection loop must not
  assume unbounded history) and to any per-whale volume estimate (observed counts are a floor).
- **Trade frequency:** 578 matched ML bets over the window is the observed floor across 4 whales
  (two truncated). I'm **not** asserting a per-day figure — you set launch sizing at CP5 from
  whichever whale set + window you choose.
- **ML-only reality:** ~40% of these whales' MLB markets are totals/spreads/props with **no Kalshi
  equivalent** — correctly skipped, not failures. Worth knowing: copying these whales captures
  their moneyline bets only.

---

## 7. What I did NOT do (gate discipline)

- No confidence threshold preset (§3 is the distribution for you to set it from).
- No doubleheader disambiguation rule built (§4 flagged for your decision).
- No orders, no live money, no prod state changes; read-only throughout.
- `kalshi_copy_trader.py` + `sports_team_mapping.py` byte-unchanged.
- No CP2 work started.

## 8. Decisions needed at CP1 review (before CP2)
1. **Confidence threshold** for auto-execute — set from §3 (start strict per your philosophy).
2. **Doubleheader handling** — (a) skip-and-log at launch, or (b) authorize the start-time bridge.
3. Confirm correctness of the matches above; then I proceed to CP2 (execution layer, dry-run).

(Launch $ numbers and the whale-trigger wiring remain CP5 operator items — not needed for CP2.)

---

### Artifacts (all read-only)
- `trading_corp/data/mlb_poly_kalshi_match.py` — the matcher (pure, reused by CP2)
- `tests/test_mlb_poly_kalshi_match.py` — 17 unit tests
- `cp1_00_auth_probe.py` · `cp1_01_kalshi_reach_probe.py` · `cp1_02_poly_sample.py` ·
  `cp1_03_run_validation.py` · `cp1_04_analyze.py` · `cp1_05_poly_dh_probe.py`
- `cp1_validation_out.json` — full per-market results
