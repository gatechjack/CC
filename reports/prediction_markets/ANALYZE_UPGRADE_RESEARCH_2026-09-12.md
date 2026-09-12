# ANALYZE UPGRADE — RESEARCH (READ-ONLY) 2026-09-12

Branch `pm-analyze-upgrade-research-2026-09-12` off `origin/prod-live` @ `e02d73ea` (git truth; box matches).
Research only — build nothing; Jack rules the design. Real numbers pulled read-only (mode=ro PM DB) 23:00Z.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 0. THE CENTRAL QUESTION — resolved: THERE IS NO TRUSTWORTHY EQUITY CURVE
═══════════════════════════════════════════════════════════════════════════════════════════════
- `pm_closed_position` carries `resolved_ts` + `realized_pnl` + `cost_basis` per position -> a resolution-dated
  cumulative-P&L series IS reconstructible over the whole backfilled history. BUT it DROPS held-to-worthless
  losses (F-1, wallet-dependent -- SDTrading ~94%). Those dropped losers are exactly the DOWN-STEPS of the curve
  -> the reconstruction is SMOOTHER and MONOTONICALLY HIGHER than reality.
- `/activity` carries dated per-fill BUY/SELL (`size`/`price`/`timestamp`) + gamma resolution -> it CAN recover the
  dropped losers (loss_grounding does this) BUT only inside the ~5000-row window; older history truncates.
- Merging them gives a more-complete series whose completeness DECAYS going back in time. No source, nor the merge,
  is a trustworthy dated equity curve.
- **PROVEN with real box data:** the loss-DROPPING whales show ~$0 reconstructed drawdown while the HONEST whales
  (that record their losses) show real drawdowns. `0xbca08c1bc2/mlb` = **7323 wins / 0 losses, max_drawdown $0**
  (a 7323-0 record is impossible honestly -> ~100% loss omission); `BetMechanic/nba` records its losses (56% WR)
  -> **$290,176 drawdown**. A smoothness / drawdown / Sortino score would rank the MIRAGE whale #1 and the honest
  one low. => equity-curve metrics don't just have a bound; **their bias REWARDS the dishonest whale.**

═══════════════════════════════════════════════════════════════════════════════════════════════
## 1. FIELD INVENTORY WITH TRUST LEVELS
═══════════════════════════════════════════════════════════════════════════════════════════════
TRUSTWORTHY = the value is what it says, for the rows present. BOUNDED = real but with a stated, directional bound.
FICTION = computable, but the number does not mean what the framework assumes (usually because the SET is
incomplete in a way that flatters the whale).

### From `pm_closed_position` (our backfill of Polymarket /closed-positions)
| field | trust | note / direction of bias |
|---|---|---|
| `cost_basis` (=total_bought*avg_price) | TRUSTWORTHY (per row) | the ruled ROI denominator (never notional) |
| `realized_pnl` (per row) | TRUSTWORTHY (per row) | the row's P&L is right; the SET is not (see below) |
| `avg_price`, `outcome_index`, `condition_id`, `title`, `category`, `resolved_ts`, `end_date` | TRUSTWORTHY | per-row facts |
| `won` (cur_price>=0.9, per row) | TRUSTWORTHY (per row) | the SET of rows omits losers -> aggregate win-rate is FICTION |
| `total_bought` (NOTIONAL) | BOUNDED->FICTION for ranking | net/total_bought REORDERS longshots ~50x (ruled: never rank on it) |
| `pnl_suspect`/`n_excluded` (S3A) | TRUSTWORTHY | ★ but this is NOT the loss omission -- S3A quarantines negRisk phantoms; the dropped held-to-worthless losers NEVER ENTER the table, so `n_excluded=0` does NOT mean the record is honest |
| **aggregate win_rate / net P&L / cost-ROI** | **BOUNDED (up)** | inherits the F-1 loss omission -> INFLATED, wallet-dependently |
| **any equity-curve stat** (drawdown/Sortino/Sharpe/Calmar/recovery/smoothness) | **FICTION (dangerous)** | omission removes the down-steps -> flatters the mirage (S0) |

### From `pm_category_stats` (the weekly rollup)
Same fields, aggregated; same F-1 bound. `roi` is cost-based (RANKED, correct); `roi_notional` present but NOT ranked.
Adds `last_resolved_ts`, `data_quality`, `dq_*_pct` -- the S3A caveats, NOT the F-1 omission.

### From Polymarket `/activity` (ActivityRow) — fetched live, windowed
| field | trust | note |
|---|---|---|
| `side`/`size`/`price`/`usdc_size`/`timestamp`/`condition_id`/`outcome_index` | TRUSTWORTHY (per fill) | dated per-fill -- the ONLY entry/exit-timing source |
| **held-to-resolution set** (buy-sell>floor) + gamma won | BOUNDED (lower) | recovers dropped losers, but WINDOWED (~5000 rows) -> `a_only` is a LOWER bound; `coverage_pct` states how far back it reached |
| anything older than the window | FICTION (absent) | the whale's early history is simply not here |

### From Polymarket `/closed-positions` live vs our table
Same fields; the live API also truncates (~1500-row cap). Our backfill can exceed it via paging + completeness gate.

### From OUR JOURNAL `pm_subdivision_order` (real copies)
| field | trust | note |
|---|---|---|
| `fill_count`/`fill_price`/`fee`/`outcome_status`/`is_exit`/`submitted_ts`/`ticker` | TRUSTWORTHY (real money) | ★ our ACTUAL copy fills -> sidesteps loss-omission ENTIRELY (it is our own book, not the whale's screening feed) |
| realized COPY P&L (entry cost vs settlement/exit proceeds) | TRUSTWORTHY but THIN | a handful of whales have a real sample (0x684baa57c3/mlb 214 fills, 0xdb859a55/atp 46, 0xe8c4d68a/cfb 42); most <20; only since arm (~late-Aug) |

### From `pm_whale`
`user_name`, `first_seen_ts`, `last_backfill_ts` only. NO track-record metadata; track length must be derived from
`resolved_ts` (and inherits the omission -- dropped losers would densify/extend it).

═══════════════════════════════════════════════════════════════════════════════════════════════
## 2. PER-DIMENSION VERDICT ON JACK'S FRAMEWORK
═══════════════════════════════════════════════════════════════════════════════════════════════
| Jack's dimension | verdict | why |
|---|---|---|
| Risk-adjusted: Sortino/Sharpe/Calmar (30%) | **NOT COMPUTABLE as written (FICTION)** | need a dated equity curve; the reconstructable one omits losses and flatters the mirage (S0) |
| Consistency / smoothness / rolling stability / drawdown recovery (25%) | **NOT COMPUTABLE (FICTION, dangerous)** | same; the 7323-0 whale scores "perfectly smooth" |
| Max drawdown (framed as filter) | **NOT COMPUTABLE (FICTION, dangerous)** | dropped losers ARE the drawdowns; smallest DD == most dishonest |
| Profit factor + expectancy | **COMPUTABLE, BOUNDED (up)** | profit factor = sum wins / sum losses; the loss side is under-reported -> inflated. Denominator question = COST basis, same ruling as ROI. Compute on the GROUNDED loss set, mark as a bound |
| Realized ROI | **COMPUTABLE (cost-based), BOUNDED (up)** | the ruled metric; still loss-omission-inflated |
| Win-rate quality + sample size (15%) | **COMPUTABLE, BOUNDED (up)** | win-rate inflated by omission; the HONEST rate needs grounding. Sample size is TRUSTWORTHY and load-bearing |
| Diversity / category robustness + recency (10%) | **COMPUTABLE** | n_condition_ids, two_sided_pct, per-category spread, last_resolved_ts -- all per-row facts |
| MIN resolved trades / track length | **COMPUTABLE** (gate) | n + resolved_ts span; the right shape is a GATE (insufficient-data), not a score |
| Activity recency | **COMPUTABLE** | last_resolved_ts / journal last fill |
| **NO extreme single-trade dominance** | **COMPUTABLE, needs no equity curve -- the STRONGEST honest dimension** | max(realized_pnl) as a share of net/gross P&L. See S3 |
| Simulated copy after slippage/scaling | **PARTLY REAL (our journal), else SIMULABLE (windowed)** | see S3 |
| Moderate avg position vs our capital | COMPUTABLE | avg cost_basis; a sizing note, not a quality score |
| Diversify across 3-5 traders | COMPUTABLE (portfolio-level) | a roster construction rule, not a per-whale score |
| Periodic re-scoring on rolling windows | COMPUTABLE | recompute on a window; but a windowed equity metric is still FICTION |

═══════════════════════════════════════════════════════════════════════════════════════════════
## 3. THE TWO STRONG IDEAS — tested on real data
═══════════════════════════════════════════════════════════════════════════════════════════════
### 3a. SINGLE-TRADE DOMINANCE — the best new metric. Needs no equity curve; directly answers "one lucky
### position or fifty ordinary ones." Two forms (compute both, they bound differently under F-1):
- **gross share** = |largest position P&L| / SUM|P&L|. F-1 direction: the dropped losers would ENLARGE the
  denominator -> gross share is OVER-stated on our feed (conservative: flags MORE concentration).
- **net-profit share** = largest winning P&L / net P&L. F-1 direction: dropped losers would SHRINK net ->
  net share is UNDER-stated on our feed (honest value is WORSE). >100% means one position exceeds the whole net.
- **Real results:** `0x3dfb153c19/ufc` (WE COPY) net share **42%** (Strickland-Khamzat $211k of $499k net), WR 48%;
  `0x629c2844d5/bra` net share **168%** (record is one position); thin high-ROI whales
  (`0xc2f2d01b22/golf` n=6 roi +2307%, net share 29%; `.../nhl` n=7 roi +299%, net share 59%) -- exactly the
  "incredible tiny-n record" cases. Contrast the diversified: `0x684baa57c3/mlb` net share **4%** (n=213),
  `0xd106952ebf/nba` **0%** (n=5576). ★ Dominance + n together is the beginner's-luck-vs-expert discriminator.

### 3b. COPY PERFORMANCE FROM OUR JOURNAL — REAL, sidesteps loss-omission, but THIN.
- For whales we copy we have REAL fills (`0x684baa57c3/mlb` 214 filled / 1070 contracts / 107 exits;
  `0xdb859a55/atp` 46; `0xe8c4d68a/cfb` 42). Realized copy P&L = settlement/exit proceeds - entry cost - fee, per
  round-trip -- TRUSTWORTHY but sample is days-to-weeks old and concentrated in ~5 high-volume whales; most <20 fills.
- WORTH: as GROUND TRUTH it beats every ratio for the whales it covers (it is our money, not the whale's feed). As a
  SCREEN it is too thin to rank most whales yet. EXTENSION to un-copied whales = SIMULATION: replay the whale's
  `/activity` fills through the matcher + slippage + 5-contract sizing -> what our copy WOULD have returned. Feasible,
  but bounded by the `/activity` window (recent only) and it re-imports the omission question at the edges. Proposal:
  use REAL copy P&L as a CONFIRMATION signal where it exists; defer full simulation to the build phase.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 4. PROPOSED SCORING DESIGN — tiers, not a composite; honest dimensions only
═══════════════════════════════════════════════════════════════════════════════════════════════
Reject the weighted-composite: a single 0-100 number would BLEND the FICTION equity metrics into the score and hide
exactly the contamination that matters. Jack's own instinct (tiers + a sort number + insufficient-data first-class)
is the right shape. Concrete proposal (4 tiers; a whale is placed by the Sonnet agent reasoning over the dimensions,
each carrying its trust flag):

- **INSUFFICIENT DATA** (a GATE, not a low score). Any of: n_honest_resolved < ~30; loss-coverage too low to trust the
  win-rate; single-trade net share so high the "record" is one position (e.g. >60%). => "watch, not yet judgeable."
  ★ This is where the tiny-n marvels (golf n=6 +2307%) and the mirages land -- NOT in a low tier.
- **COPY-CANDIDATE**: clears the gate; a real COST-ROI edge that SURVIVES loss-grounding (honest win/loss still
  positive-edge); diversified (low dominance, low two-sided share or an acknowledged upper-bound); bonus if our own
  journal copy P&L is positive.
- **MIXED / WATCH**: an edge with a MATERIAL caveat -- chalk (avg_win_price>=0.85, little edge), hedger (high
  two_sided_pct -> ROI is an upper bound), moderate dominance, or a moderate measured omission.
- **PASS**: no honest edge -- chalk with ~0 ROI, a mirage win-rate hiding a coinflip once grounded, or a record
  carried by one position.

**The sort NUMBER (Jack's "supporting number"):** cost-based ROI (the ruled metric), shown ALWAYS beside n,
single-trade net share, measured loss-omission %, and two-sided % -- so the number never stands alone and the tier
CAPS it (a dominated or mirage whale cannot be COPY-CANDIDATE regardless of ROI). Sort within tier by ROI.

**What the score is built on (and what it is NOT):** IN = cost-ROI, honest win/loss (grounded), single-trade
dominance, sample size + track span, two-sided share, avg-win-price, our real copy P&L. OUT (never) = Sortino,
Sharpe, Calmar, max-drawdown, drawdown-recovery, equity-smoothness, notional ROI.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 5. THE SONNET SKILL SHAPE — reasoning order, and brevity by construction
═══════════════════════════════════════════════════════════════════════════════════════════════
Order (each step can SHORT-CIRCUIT to a tier):
1. **GATE** — enough honest data? (n_honest, coverage, extreme dominance) -> else INSUFFICIENT DATA, stop.
2. **TRUST THE WIN-RATE?** — read the grounded loss-omission %. If material, use the HONEST W/L; the headline is a
   mirage. (The 7323-0 whale dies here.)
3. **ONE POSITION OR MANY?** — single-trade net share. High -> it's a bet, not a track record (caps the tier).
4. **REAL OR CHALK EDGE?** — cost-ROI + avg-win-price + two-sided share (hedger -> upper bound).
5. **DO WE HAVE REAL COPY EVIDENCE?** — our journal copy P&L, if copied (confirmation).
6. **EMIT** a TIER + the 2-3 decisive numbers + ONE sentence.

★ **Brevity made structural (the anti-Haiku rule):** the output is a TEMPLATE, not prose --
`TIER · roi=X% · n=Y · dominance=Z% · omission=W% · <one sentence naming the single decisive factor>`.
The caveats are ENCODED as trust-flags ON the numbers the agent is handed (`win_rate=93% [MIRAGE: 94% of losses
dropped, honest ~50%]`), so the agent cites each once and cannot restate it ten ways -- there is no room in the
template. Move the model Haiku->Sonnet for the reasoning; keep the output tokens capped hard (~120).

═══════════════════════════════════════════════════════════════════════════════════════════════
## 6. REAL OUTPUT — four whales side-by-side (the tiers, on live data; incl. two we copy)
═══════════════════════════════════════════════════════════════════════════════════════════════
(Numbers are the deterministic pull; the tier is what the proposed design would assign. Loss-omission % is the
KNOWN/inferred figure -- the build wires the live grounding per whale.)

- **`0x684baa57c3` / mlb (WE COPY)** — n=213, W/L 196/17 (92%), **cost-ROI +89%**, single-trade net share **4%**,
  drawdown-mirage $12.7k, 214 real copy fills in our journal.
  -> **COPY-CANDIDATE (with a flag):** diversified, strong ROI, and we already copy it with a real sample -- BUT the
  92% win-rate is loss-omission-inflated; grounding needed before trusting the RATE (the ROI/edge stand).
- **`0x3dfb153c19` / ufc (WE COPY)** — n=56, W/L 27/29 (**48%**), cost-ROI +19%, single-trade net share **42%**
  (Strickland-Khamzat $211k of $499k), 12 real fills.
  -> **MIXED / WATCH:** the UFC record is one fight + a coinflix around it; the +19% ROI is real but the "edge" is
  concentration, not skill. We copy it -- worth Jack knowing the profit is one position.
- **`0xbca08c1bc2` / mlb (not copied)** — n=7323, **W/L 7323/0 (100%)**, cost-ROI +73%, drawdown $0.
  -> **PASS (mirage):** a 7323-0 record is impossible honestly -> ~100% loss omission. A composite/smoothness score
  ranks this #1; the honest read is "we cannot see this whale's losses at all." The exhibit for why equity metrics
  are dangerous.
- **`0xc2f2d01b22` / golf (not copied)** — n=6, 6/0 (100%), cost-ROI **+2307%**, single-trade net share 29%.
  -> **INSUFFICIENT DATA:** six positions over two days. Not a low score -- a "watch," exactly as Jack wants. The
  +2307% is a first-class insufficient-data case, never a rank.

═══════════════════════════════════════════════════════════════════════════════════════════════
## 7. WHAT THIS DOES NOT COVER (scope, honest)
═══════════════════════════════════════════════════════════════════════════════════════════════
- On-chain wallet reads: OUT this pass (data inventory first, per scope).
- Merlin's stats: OUT (the backlog records their worth + faults).
- Live per-whale loss-omission %: MEASURABLE now via the existing `loss_grounding` (windowed, a floor) -- the build
  wires it into the score; this pass used the documented/inferred figures (SDTrading 94%, the 7323-0 exhibit).
- Full copy-simulation for un-copied whales: feasible, deferred to the build (windowed by `/activity`).
