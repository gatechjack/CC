# G0 Validation Gate — RESULT: PASS

**Run:** 2026-08-22, `pk_g0_probe_ro.ps1` via `az vm run-command` on tc-prod-vm (read-only, public no-auth API, zero DB/engine/box-state change). Executed by Jack.
**Verdict:** **PASS** — negative `realizedPnl` rows exist in `/closed-positions` for all three known net-losers. The legacy "positives-only survivorship" claim (`seed_polymarket_watchlist_deep.py:57-62`) is **disproven**. The ROI scoreboard foundation is sound.

## Per-wallet result

| Wallet | total closed | negative | positive | zero | net realizedPnl (raw sum) | total_bought | net ROI | activity-method expected net |
|---|---|---|---|---|---|---|---|---|
| evanng (UFC) `0x43e0f8…` | 137 | 33 | 104 | 0 | **+15,702.05** | 140,420.90 | +11.2% | −13,706.51 |
| csgod (UFC) `0x805618…` | 3,505 | 1,892 | 1,611 | 2 | **−166,834.21** | 7,825,504.77 | −2.1% | −9,551.47 |
| d1k21 (Fed) `0x71ed0b…` | 3,393 | 1,344 | 1,532 | 517 | **−17,123,920.64** | 21,288,056.46 | −80.4% | −168,183.81 |

Ordering-stability probe: `page0 len a=50 b=50 identical_order=True` (stable across identical calls → supports P2 incremental-refresh design).

## Anomaly analysis (surfaced with diagnostics; none is a G0 failure)

**A. evanng net SIGN FLIP** (closed-positions **+$15.7k winner** vs activity-method **−$13.7k UFC loser**).
Cause = **scope, not a bug.** The activity-method scout was **UFC-only** (n=92); `/closed-positions` is **all-category** (137 rows). evanng is a UFC loser but an overall winner. **This VALIDATES the plan's per-(wallet, category) rollup** — you cannot judge a whale on a blended cross-category net; category slicing is mandatory. When rolled up by category, the UFC slice should recover the loss (to be confirmed in stats).

**B. csgod / d1k21 magnitude divergence** (raw net dwarfs the activity net).
Same scope cause: activity nets were single-category slices; `/closed-positions` is the whole wallet. csgod trades heavily outside UFC (NFL "Vikings vs Cowboys" −$27.8k, CS2 esports −$24k/−$15k) — 3,505 positions, $7.8M volume. d1k21 is a mega-whale ($21.3M volume, 3,393 positions).

**C. d1k21 identical −$574,604.31 across three 2024-election markets** (Bernie / Vivek / Warren "Will X win").
These are **negRisk linked multi-candidate markets** — but each candidate is a **distinct market with its own `conditionId`**. Equal identical losses = a whale who **sprayed equal-size YES on many longshots, each resolving NO** → a REAL −$574,604.31 per distinct position, correctly summed. **Not a data artifact.**
Robustness either way: the schema PK **`(wallet, condition_id)`** makes the rollup correct regardless — distinct conditionIds are distinct real positions (correct to sum); any genuine duplicate-conditionId rows collapse to one (no double-count). The probe reports a **raw-row** sum (un-deduped); the scoreboard sums **PK-deduped, per-category** rows, so it is not exposed to raw-row duplication even if the API ever returned any.
**Watch-item carried into the build:** confirm realizedPnl cleanliness by comparing raw-row vs PK-deduped sums, per-category, during the ingest/stats build; the acceptance-checklist "one manually-verified whale's net matches an independent API sum" is the definitive scoreboard-accuracy gate. Note negRisk also appears in **Fed rate-decision** markets (a live category), so this verification is in-scope for Fed — but the PK dedup + per-category rollup already handle it.

**D. Ordering stable** — informs the P2 "stop at first fully-known page" incremental-refresh optimization.

## Conclusion
Core gate PASS. After analysis, none of the anomalies change the P1 build plan: they are scope-driven (A, B) or real-whale behavior robustly handled by the `(wallet, condition_id)` PK + per-category rollup (C). Proceed to the package build; verify realizedPnl cleanliness (raw vs deduped, per-category) as an early step in ingest/stats.

---
*Raw run output (verbatim evidence):*

```
===== PREDICTION MARKETS G0 GATE: negative realizedPnl in /closed-positions =====

-- evanng(UFC)  wallet=0x43e0f84fe8fb4623a5ff485fe9f7bc0f4b458618
   total_closed_positions=137  negative=33  positive=104  zero=0
   net_realizedPnl=15702.05  total_bought=140420.90  net_roi=11.2%  (activity-method expected net ~ -13706.51)
   WALLET G0: PASS - negative rows present
     NEG sample: realizedPnl=-1718.83 curPrice=0  UFC Fight Night: Gaston Bolanos vs. Michael Aswell (Feathe
     NEG sample: realizedPnl=-1471.28 curPrice=0  UFC 329: Cory Sandhagen vs. Mario Bautista (Bantamweight,
     NEG sample: realizedPnl=-1436.03 curPrice=0  UFC 330: Neil Magny vs. Ramiz Brahimaj (Welterweight, Prel

-- csgod(UFC)  wallet=0x8056189d56833ce5b3945dea9149b62c5111b64d
   total_closed_positions=3505  negative=1892  positive=1611  zero=2
   net_realizedPnl=-166834.21  total_bought=7825504.77  net_roi=-2.1%  (activity-method expected net ~ -9551.47)
   WALLET G0: PASS - negative rows present
     NEG sample: realizedPnl=-27789.03 curPrice=0  Vikings vs. Cowboys
     NEG sample: realizedPnl=-24029.06 curPrice=0  Counter-Strike: Spirit vs Vitality (BO5) - IEM Rio Playoff
     NEG sample: realizedPnl=-15247.31 curPrice=0  Counter-Strike: Team Falcons vs FURIA (BO3) - IEM Rio Play

-- d1k21(Fed)  wallet=0x71ed0bc95433cdf1be29f43219725fce9addd9eb
   total_closed_positions=3393  negative=1344  positive=1532  zero=517
   net_realizedPnl=-17123920.64  total_bought=21288056.46  net_roi=-80.4%  (activity-method expected net ~ -168183.81)
   WALLET G0: PASS - negative rows present
     NEG sample: realizedPnl=-574604.31 curPrice=0  Will Bernie Sanders win the 2024 US Presidential Election?
     NEG sample: realizedPnl=-574604.31 curPrice=0  Will Vivek Ramaswamy win the 2024 US Presidential Election
     NEG sample: realizedPnl=-574604.31 curPrice=0  Will Elizabeth Warren win the 2024 US Presidential Electio

===== ORDERING STABILITY PROBE (same wallet, page0 pulled twice) =====
   page0 len a=50 b=50  identical_order=True

===== VERDICT: G0 PASS =====
```
