# Farm Re-Rank on Corrected Data — READ-ONLY analysis (2026-08-23)

**Scope:** query the deployed P1 scoreboard, compare the roster's *scout-era* figures against the corrected
scoreboard, test the scout's exclusions, recommend options. **No mutations were made** — no code, no roster,
no agent_state, no promote/demote. The live 2-whale roster and the 10-whale paper farm are exactly as before.

## ★ LIVE-MONEY STATUS (leads)
- **Nothing here trades.** P1 is read-only ingest + an offline scoreboard. This report reads it; it changes nothing.
- Engine **MainPID 850993** active, unchanged before/after every query. No restart, no arm-state change.
- Legacy `data/trading_corp.db` written only by the live engine (never by P1). Excluded-whale figures came from
  read-only pulls of the **public** Polymarket data-api (same pattern as the accepted net-verify) — no DB writes.
- Legacy divisions untouched: `poly_kalshi_mlb` (LIVE+ARMED, geo-blocked), MACE (halted, weekend), PCT paper farm.

---

## TASK 0 — First cron fire (03:20 UTC) — ★ IT FIRED BUT FAILED ON WRITE (permissions defect found)

**The 03:20 UTC refresh FIRED for the first time at 03:20:02 UTC (timing correct), ran ~18 min (a full
rate-limited re-pull), then FAILED at the write step:**

```
sqlite3.OperationalError: attempt to write a readonly database
  File ".../prediction_markets/stats.py", line 89, in rollup   conn.executemany(...)
```

**Root cause (confirmed read-only):** `data/prediction_markets.db` is owned **root:root, mode 644**. Every
prior backfill/refresh I ran went through `az vm run-command`, which executes as **root** — so root created and
owns the DB file. **The cron runs as `azureuser`** (uid 1000), which has read-only access to a 644 root file →
it can pull all the data but cannot write the rollup. (`data/` dir is `azureuser:azureuser` and writable — only
the .db *file* blocks. Legacy `trading_corp.db` is azureuser-owned, which is why the live engine writes it fine.)

**Impact: the nightly refresh is currently NON-FUNCTIONAL and will fail every night until the file is chowned.**
This is a genuine deployment defect (see Option E for the one-line fix — a mutation, out of scope for this
read-only block).

**But the failure was clean — no data harm:**
- The rollup write is one transaction; it rolled back atomically. DB is **unchanged and uncorrupted**: still
  **28,303** rows, **12/12 pulled == stored** (integrity guard held), **12/12 backfill_complete=1**, per-wallet
  counts identical to pre-fire. `last_refresh_ts` unchanged (02:30:30 = the last successful root-run write).
- **Idempotency (the Board's question) is intact by design** — the ingest is a PK-keyed `INSERT OR REPLACE`
  upsert (PK = wallet,condition_id,outcome_index) + the pulled==stored guard, so a completed re-pull of the same
  snapshot cannot duplicate or drop rows; the only expected delta is live-whale drift (SDTrading/xifutloong3
  MLB). This run simply wrote nothing, so there is no after-state to diff yet.
- 429s: the ~18-min pull was heavily rate-limited (partly by my own concurrent read-only excluded-whale probes
  hitting the same public data-api) — but rate-limiting did NOT cause the failure; the pulls reached the rollup.
- Engine **MainPID 850993** unchanged; legacy DB untouched throughout.

---

## TASK 1 — Re-rank the roster on corrected data

**Read the score with cost-ROI + avg_win_price, NOT alone.** The `net_roi` score = `wilson_lcb(win%) ×
edge_factor(cost-ROI)`. When cost-ROI ≈ 0 the score collapses to a win%-driven number, so a **chalk** whale can
post a high score on ~zero edge (AIisTheNewWD below). Cost-ROI + avg_win_price are the honest edge signals.

Scout ROI% is `net/total_bought` (notional) — a *different, wrong-in-order* denominator (P1_PLAN §13 dec 11), so
scout ROI% is **not** directly comparable to corrected cost-ROI%; the net and the rank order are the comparable parts.

### Comparison table — SCOUT (then) vs CORRECTED scoreboard (now), in each whale's rostered category

| Whale | Role | Cat | SCOUT net / ROI / n / win% | NOW n | win% | **cost-ROI** | notl-ROI | net | avgWinPx | flag | net_roi | recency | 2-sided | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **SDTrading** | LIVE | mlb | (live div; not scouted) | 469 | 94 | **+90.2%** | +45.8% | +$4,202,331 | 0.51 | CONTESTED | 1.745 | 1.739 | 4% | **CONFIRMED — elite; #1 on board; net-verified to the cent** |
| **xifutloong3** | LIVE | mlb | (live div; not scouted) | 201 | 77 | **+52.6%** | +30.1% | +$1,792,120 | 0.57 | CONTESTED | 1.081 | 1.312 | 1% | **CONFIRMED — strong, clean** |
| **STC14** | pin | ufc | +$1.8k / — / 102 / 66% | 85 | 84 | **+38.7%** | +24.9% | +$11,829 | 0.67 | CONTESTED | 1.030 | 1.016 | 6% | **CONFIRMED / UP — best clean UFC (contested, low 2-sided)** |
| **kutsumiakia** | pin | ufc | +$2.5k / — / — / 83% | 123 | 95 | +15.3% | +13.0% | +$24,733 | 0.85 | **CHALK** | 1.035 | 1.012 | 0% | **UP in rank but CHALK — top score is win%-driven** |
| **Kh4mz4t** | pin | ufc | +$14.1k / +9% / 210 / 64% | 270 | 62 | **+26.3%** | +14.2% | +$35,737 | 0.64 | CONTESTED | 0.706 | 0.732 | **38%** | **DOWNGRADED rank (#1→#3); real edge but 38% two-sided** |
| **000why000** | pin | ufc | +$17k / — / — / 73% | 117 | 69 | +13.9% | +9.7% | +$25,919 | 0.75 | mid | 0.688 | 0.688 | **29%** | **CONFIRMED (mid); 29% two-sided caveat** |
| **4751346** | pin | ufc | +$29k / — / — / 80% | 1264 | 63 | +8.5% | +6.0% | +$192,450 | 0.77 | mid | 0.659 | 0.727 | **41%** | **DOWNGRADED — lowest UFC score; 41% two-sided; only 44% single-fight** |
| **BetMechanic** | pin | nba | +$94k / +11% / 71 | 6782 | 56 | +5.9% | +2.7% | **+$1,123,677** | 0.57 | CONTESTED | 0.580 | 0.593 | **71%** | **EVIDENCE UP hugely BUT 71% two-sided = market-maker; copyability uncertain** |
| **pako** | pin | fed | +$629k / +34% / 8 | 106 | 76 | +6.0% | +5.2% | +$475,694 | 0.91 | **CHALK** | 0.716 | 0.364 | 7% | **net confirmed large but CHALK (0.91); modest ROI; old** |
| **FordBronco** | pin | nfl | +$28.2k / +5.6% / 120 / 73% | 202 | 55 | +5.6% | +3.1% | +$28,704 | 0.66 | CONTESTED | 0.508 | 0.557 | **70%** | **DOWNGRADED — 70% two-sided + STALE 2024-25 + win 73%→55%** |
| **Kickstand7** | pin | fed | — / +8.9% / 3 (win_px 0.77) | 98 | 56 | +3.0% | +2.0% | +$202,581 | 0.82 | mid | 0.476 | 0.206 | **46%** | **DOWNGRADED — contested thesis weakened (0.77→0.82); 46% two-sided; low recency; lowest score** |
| **AIisTheNewWD** | pin | nfl | +$103k / — / 39-0 / 80% | 162 | 99 | **+0.2%** | +0.2% | +$9,687 | 0.99 | **CHALK** | 0.967 | 0.970 | 0% | **MIRAGE BUSTED — 99% win / +0.2% ROI = zero edge; high score is a chalk artifact** |

*(cost-ROI is the RANKED metric; **bold** = notable. Every rostered category is `data_quality = clean` — the 9
contaminated pairs are all in NON-rostered categories, see Task 2.)*

### Verdict summary
- **CONFIRMED (real edge, clean):** SDTrading (LIVE), xifutloong3 (LIVE), STC14.
- **CONFIRMED but caveated:** 000why000 (29% two-sided), Kh4mz4t (38% two-sided, fell from #1), pako (CHALK), kutsumiakia (top score but CHALK).
- **DOWNGRADED (weak copy signal):** 4751346 (41% two-sided + only 44% single-fight), FordBronco (70% two-sided + stale), Kickstand7 (thesis weakened, lowest score, 46% two-sided), AIisTheNewWD (mirage busted, chalk, ~0 edge).
- **EVIDENCE UPGRADE, copyability uncertain:** BetMechanic (+$1.12M NBA, multi-sport positive — but 71% two-sided).

### (a) Do the same 10 whales still rank? Whose standing materially changed?
All 12 still produce a rankable rostered-category row (all n≥10, all complete). But the **within-category order
reshuffled hard** — exactly the "wrong in ORDER" prediction. Materially changed:
- **UFC completely reordered:** scout #1 **Kh4mz4t → now #3**; scout-lowest-net **kutsumiakia → now #1** (but chalk);
  scout-highest-net **4751346 → now last** (and most caveated). STC14 rose to the top clean spot.
- **AIisTheNewWD**: mirage busted → chalk / no-edge (see (e)).
- **BetMechanic**: from "partial, +$94k" to a **+$1.12M multi-sport** record — biggest evidence swing on the board.
- **FordBronco / Kickstand7**: both downgraded (two-sided + stale / weakened-thesis).
- **Unchanged/confirmed:** SDTrading, xifutloong3 (top 2), STC14, 000why000, pako.

### (b) Any pinned whale now NET NEGATIVE on corrected cost-ROI in its rostered category?
**No.** Every rostered-category cost-ROI is positive — but the floor is *effectively zero edge*: AIisTheNewWD
**+0.2%** and Kickstand7 **+3.0%** are barely-positive chalk, not an edge. (Several whales ARE negative in
*non*-rostered categories — e.g. BetMechanic ufc −10.6%, pako unknown −5.3% — but not where they're rostered.)

### (c) Were any scout EXCLUSIONS wrong on complete data? (read-only re-pull, corrected predicate)
The excluded whales were never ingested (not on the roster), so I re-pulled them read-only from `/closed-positions`
and applied the same corrected predicate + cost-ROI. Raw output at the bottom.

| Excluded whale | Cat | Scout said | CORRECTED (rostered cat) | Verdict |
|---|---|---|---|---|
| **evanng** | ufc | −$13.7k (net-loser) | **+$12,068 / +24.0% cost-ROI / CONTESTED (0.60) / 68% win**, n=76 | **WRONGLY EXCLUDED — sign flip.** (caveat 41% two-sided) |
| **MadeiraIsland** | ufc | (not shortlisted) | **+$4,796 / +9.0% cost-ROI / CONTESTED (0.53) / 58% win**, n=131, **two-sided 1%** | **WRONGLY EXCLUDED — cleanest UFC profile on the board** |
| **peter003** | nba | chalk 21-1/−63% ROI | +$88,233 / **+2.1%** cost-ROI / CHALK (0.92) / 61% win, **two-sided 63%** | Scout's −63% was WRONG, but marginal chalk + market-maker; not a clear edge |
| **csgod** | ufc | −$9.5k | −$7,090 / **−4.7%** cost-ROI / 45% win (ALL-cat −$161,598) | Exclusion **CONFIRMED** |
| **SadMan** | nfl | chalk 98%/−4.4% | −$10,732 / **−4.1%** cost-ROI / CHALK (0.97) / 95% win | Exclusion **CONFIRMED** |
| **d1k21** | fed | −$168k/−29% | −$236,716 / **−9.2%** cost-ROI / CHALK (0.92) (ALL-cat −$239,319) | Exclusion **CONFIRMED** |

**Two genuinely-positive contested UFC whales were wrongly excluded by the broken scout method: evanng and
MadeiraIsland.** evanng's +24.0% cost-ROI would rank near the TOP of the pinned UFC cohort; MadeiraIsland's
+9.0% is mid-pack but with the cleanest two-sided share (1%) of any UFC whale. This is the scout's `/activity`
truncation + notional-ROI defect biting exactly as suspected.

### (d) Does the CHALK lesson still hold under cost-ROI?
**Yes — and it's sharper.** `avg_win_price` still cleanly separates: contested whales (SDTrading 0.51,
xifutloong3 0.57, BetMechanic 0.57, Kh4mz4t 0.64) post large cost-ROI; chalk whales (AIisTheNewWD 0.99,
pako 0.91) post small cost-ROI. Under cost-ROI the two become **mechanically linked** — buying favorites at
0.9+ caps your ROI near +11% by construction — so high avg_win_price ⇒ low cost-ROI automatically. The
corrected math makes chalk *more* obvious (AIisTheNewWD's 99% win now visibly = +0.2% ROI). **Caveat:** the
`net_roi` SCORE still over-credits chalk when edge≈0, so rank on **cost-ROI + avg_win_price**, not score alone.

### (e) AIisTheNewWD — mirage or real?
**Mirage CONFIRMED (busted).** `/closed-positions` does not truncate: complete NFL record is n=162, **+$9,687
net at +0.2% cost-ROI, 99% win, avg_win_price 0.99** = pure chalk, zero edge. The scouted "+$103k / 39-0" was a
truncation+notional artifact (wins shown, losses cut, gross inflated). Also **19% of its NFL rows are futures**
(`afc-east-champion`, `afc-champion`, `nfc-east-winner`), not single games — a mixed, un-copyable record.

### (f) FordBronco — what does the complete record say? Any post-Dec-2025 activity?
Complete: NFL n=**202** (scout saw 120 — even "the clean candidate" was truncated), net +$28,704 (~= scout
+$28.2k), cost-ROI +5.6%, **but win% 55%** (scout 73% — the truncated record hid losses), avg_win_price 0.66
CONTESTED, **70% two-sided**, mix 100% single-game **but every sampled game is 2024-25** (`nfl-jax-lv-2024-12-22`,
`nfl-nyg-pit-2024-10-28`). **No confirmed post-Dec-2025 (2025-26 in-season) NFL activity.** So FordBronco =
stale + heavily two-sided + mediocre true win rate. Weak.

---

## TASK 2 — Caveats (carried inline above; consolidated here)

**1. Contaminated pairs ($-weighted data_quality).** *None of the 12 rostered categories is contaminated*
(all `dq = clean`). The 9 contaminated pairs are all in NON-rostered categories and belong to the messy
multi-sport whales — **AIisTheNewWD** (fed $100%, soccer $96%, unknown $96%, ucl $94%, epl $40%) and
**Kickstand7** (nba $22%, ufc $13%). Do not cite those side-categories as clean; they also argue these two are
sprawling multi-category bettors, not focused single-category signals.

**2. Category ≠ copyability (§13A(d)).** A category blends single-game moneylines with futures/props (different skills):
- **4751346 (ufc):** only **44% dated single-fight**; 55% undated (incl. method/round props + "mvp-fight-night" cards). Half its "UFC edge" is not single-fight copyable.
- **AIisTheNewWD (nfl):** **19% futures** (division/conference winners), 60% game. Mixed.
- Clean single-game/-fight (~100%): SDTrading, xifutloong3 (mlb), STC14, 000why000, kutsumiakia, Kh4mz4t (ufc), FordBronco, BetMechanic (games).
- **Fed is inherently event-markets** (rate decisions / "what will Powell say"), not dated games — the game/futures heuristic is N/A for Kickstand7 & pako (99%/98% "undated" is expected, not a flag).

**3. Two-sided holdings (§13A(j)) — hedging/market-making ≠ directional conviction; a copy division copies direction.**
Material two-sided share in the rostered category: **BetMechanic 71%**, **FordBronco 70%**, **Kickstand7 46%**,
**4751346 41%**, **Kh4mz4t 38%**, **000why000 29%**. Clean/directional: SDTrading 4%, xifutloong3 1%, STC14 6%,
kutsumiakia 0%, AIisTheNewWD 0%, pako 7%. **BetMechanic's 71% is the single biggest copyability red flag** — its
huge net may come from market-making/scalping both sides, which a directional copy cannot reproduce.

**4. Small-n / UNVERIFIABLE.** No rostered-category row is below the min_resolved=10 threshold (smallest is
STC14 n=85). All 12 are rankable. (Kickstand7's Fed row — absent from the top of the Step-5 report — is present
at n=98; it simply ranks low, 0.476.)

---

## TASK 3 — Options (recommendations only; Jack decides — CHANGE NOTHING)

**Option A — Keep the two LIVE whales; they are validated.** SDTrading (#1, +90.2% cost-ROI, contested, 4%
two-sided, net-verified to the cent) and xifutloong3 (+52.6%, contested, 1% two-sided) are the two best profiles
on the board. Evidence: no action needed; the live division is pointed at the right whales.

**Option B — Trim / re-tier the paper farm by copy-signal quality (not by scout tier):**
- *Cleanest keep:* **STC14** (contested UFC, +38.7% cost-ROI, 6% two-sided) — promote to top of the UFC watch tier.
- *Keep, watch two-sided:* **Kh4mz4t**, **000why000** (positive contested/mid, but 38%/29% two-sided).
- *Downgrade — weak copy signal:* **4751346** (41% two-sided + only 44% single-fight), **FordBronco** (70%
  two-sided + stale + 55% true win), **Kickstand7** (thesis weakened, 46% two-sided, low recency, lowest score),
  **AIisTheNewWD** (mirage busted, chalk, +0.2% ROI — carries no edge).
- *Chalk, keep only as chalk:* **kutsumiakia**, **pako** (positive net but avg_win_price ≥0.85 = betting favorites,
  ROI mechanically capped). Fine as chalk baselines; not edge signals.

**Option C — Give the two WRONGLY-EXCLUDED whales a paper look:** **evanng** (+24.0% cost-ROI, contested UFC,
sign-flip from the scout's −$13.7k) and **MadeiraIsland** (+9.0% cost-ROI, contested UFC, 1% two-sided — the
cleanest UFC profile found). Both were killed by the broken scout method. Recommend paper OBSERVATION only
(not live), and caveat evanng's 41% two-sided. This also positively resolves the open §13A(a) evanng question.

**Option D — Before trusting BetMechanic, run a directional-slice study (P2).** +$1.12M NBA and multi-sport
positive is the biggest find, but 71% two-sided means the headline net may be market-making, not directional.
Recommend a read-only analysis of BetMechanic's *one-sided* (single-outcome) subset only — if the edge survives
on the directional slice, it's a major add; if it evaporates, it's a market-maker and not copyable. Do not
pin/promote until that is known.

**Stay excluded (confirmed on complete data):** csgod, SadMan, d1k21 (all negative cost-ROI); peter003 (marginal
chalk + 63% two-sided — the scout's −63% figure was wrong but the exclusion still stands on merit).

**★ Option E (HIGHEST-PRIORITY OPERATIONAL — fix the broken nightly refresh).** The 03:20 UTC cron fails on
write because `data/prediction_markets.db` is root-owned (my root `az run-command` backfills created it) and the
cron runs as azureuser. One-line root fix (a mutation → needs Board authorization; NOT done here):
`chown azureuser:azureuser /home/azureuser/trading_corp/data/prediction_markets.db`, then re-run
`refresh --cap 50000` once as azureuser to confirm it writes + to capture the first real idempotency diff.
Until this is done, the scoreboard will not self-update and every night's cron will error. (The data dir is
already azureuser-writable, so nothing else needs changing. Going forward, either keep the DB azureuser-owned
or run the cron/root-writes consistently — mixing root manual writes with an azureuser cron is what caused this.)
**Operational note:** even once fixed, the refresh took ~18 min tonight under heavy rate-limiting (aggravated by
my concurrent probes). On a quiet night it will be faster, but a multi-minute nightly job at 03:20 is expected;
the 429 backoff + completeness gate make a throttled run safe (it just takes longer or marks a wallet PARTIAL).

---

## RAW QUERY OUTPUT (pasted alongside the tables)

### Rostered-category rows (deployed DB; `pk_pm_rostered_clean.ps1`)
```
name          tag  rcat  n     win%  roiC%   roiN%   net_pnl     avgWinPx flag      netroi recency dq
SDTrading     LIVE mlb   469   94    +90.2   +45.8   +4202331    0.51     CONTESTED 1.745  1.739  -
xifutloong3   LIVE mlb   201   77    +52.6   +30.1   +1792120    0.57     CONTESTED 1.081  1.312  -
Kh4mz4t       PIN  ufc   270   62    +26.3   +14.2   +35737      0.64     CONTESTED 0.706  0.732  -
STC14         PIN  ufc   85    84    +38.7   +24.9   +11829      0.67     CONTESTED 1.030  1.016  -
000why000     PIN  ufc   117   69    +13.9   +9.7    +25919      0.75     mid       0.688  0.688  -
4751346       PIN  ufc   1264  63    +8.5    +6.0    +192450     0.77     mid       0.659  0.727  -
kutsumiakia   PIN  ufc   123   95    +15.3   +13.0   +24733      0.85     CHALK     1.035  1.012  -
FordBronco    PIN  nfl   202   55    +5.6    +3.1    +28704      0.66     CONTESTED 0.508  0.557  -
AIisTheNewWD  PIN  nfl   162   99    +0.2    +0.2    +9687       0.99     CHALK     0.967  0.970  -
BetMechanic   PIN  nba   6782  56    +5.9    +2.7    +1123677    0.57     CONTESTED 0.580  0.593  -
Kickstand7    PIN  fed   98    56    +3.0    +2.0    +202581     0.82     mid       0.476  0.206  -
pako          PIN  fed   106   76    +6.0    +5.2    +475694     0.91     CHALK     0.716  0.364  -
```

### Two-sided share + category mix (`pk_pm_rerank_box.ps1`)
```
   SDTrading     ALL: 20/492 two-sided=4%    ROSTERED(mlb): 20/449 two-sided=4%
   xifutloong3   ALL: 2/199 two-sided=1%     ROSTERED(mlb): 2/199 two-sided=1%
   Kh4mz4t       ALL: 77/227 two-sided=34%   ROSTERED(ufc): 75/195 two-sided=38%
   STC14         ALL: 5/80 two-sided=6%      ROSTERED(ufc): 5/80 two-sided=6%
   000why000     ALL: 93/666 two-sided=14%   ROSTERED(ufc): 26/91 two-sided=29%
   4751346       ALL: 519/2132 two-sided=24% ROSTERED(ufc): 381/921 two-sided=41%
   kutsumiakia   ALL: 63/2610 two-sided=2%   ROSTERED(ufc): 0/123 two-sided=0%
   FordBronco    ALL: 88/127 two-sided=69%   ROSTERED(nfl): 83/119 two-sided=70%
   AIisTheNewWD  ALL: 136/1539 two-sided=9%  ROSTERED(nfl): 0/162 two-sided=0%
   BetMechanic   ALL: 6930/10126 two-sided=68% ROSTERED(nba): 2825/3957 two-sided=71%
   Kickstand7    ALL: 489/1314 two-sided=37% ROSTERED(fed): 32/69 two-sided=46%
   pako          ALL: 12/357 two-sided=3%    ROSTERED(fed): 7/99 two-sided=7%

   4751346       ufc n=1302  game=568(44%) futures=15(1%) other/undated=719(55%)
   AIisTheNewWD  nfl n=162   game=98(60%)  futures=31(19%) other/undated=33(20%)
   (SDTrading/xifutloong3 mlb, STC14/000why000/kutsumiakia ufc, FordBronco nfl, BetMechanic nba = ~100% dated single-game;
    Kh4mz4t ufc 96% game; Kickstand7/pako fed = event-markets, heuristic N/A)
```

### Excluded-whale read-only re-pull (`pk_pm_excluded_probe.ps1`, corrected predicate + cost-ROI)
```
### evanng        rostered=ufc  raw=139  ALLcat_scoreable_net=+18005
   ufc: n=76  W/L=52/24 win%=68 net=+12068  cost=50206   roiC=+24.0% roiN=+11.7% avgWinPx=0.60 [CONTESTED] two-sided=22/54(41%)  RANKABLE
### csgod         rostered=ufc  raw=3516 ALLcat_scoreable_net=-161598
   ufc: n=300 W/L=134/166 win%=45 net=-7090 cost=151838  roiC=-4.7%  roiN=-2.1% avgWinPx=0.58 [CONTESTED] two-sided=19/281(7%)  RANKABLE
### MadeiraIsland rostered=ufc  raw=1170 ALLcat_scoreable_net=+21470
   ufc: n=131 W/L=76/55 win%=58 net=+4796 cost=53203    roiC=+9.0%  roiN=+4.6% avgWinPx=0.53 [CONTESTED] two-sided=1/130(1%)  RANKABLE
### SadMan        rostered=nfl  raw=911  ALLcat_scoreable_net=-15392
   nfl: n=77  W/L=73/4  win%=95 net=-10732 cost=263616  roiC=-4.1%  roiN=-3.8% avgWinPx=0.97 [CHALK] two-sided=4/73(5%)  RANKABLE
### peter003      rostered=nba  raw=527  ALLcat_scoreable_net=+140486
   nba: n=307 W/L=187/120 win%=61 net=+88233 cost=4180385 roiC=+2.1% roiN=+1.4% avgWinPx=0.92 [CHALK] two-sided=119/188(63%)  RANKABLE
### d1k21         rostered=fed  raw=3393 ALLcat_scoreable_net=-239319
   fed: n=66  W/L=38/28 win%=58 net=-236716 cost=2568096 roiC=-9.2% roiN=-6.9% avgWinPx=0.92 [CHALK] two-sided=71/97(73%)  RANKABLE
```

### Sources
- Deployed DB `data/prediction_markets.db` on tc-prod-vm (read-only).
- Scout figures: `prediction-markets-planning-2026-08-22:reports/2026-08-21_whale_scouts/SCOUT_RESULTS.md` (+ stage-2 runners for full addresses), `TRANSITION_TO_BUILD_AGENT.md`, `G0_RESULT.md`.
- Runners (mirrored to `runners/`): `pk_pm_rerank_box.ps1`, `pk_pm_rostered_clean.ps1`, `pk_pm_excluded_probe.ps1`, `pk_pm_cron_check/install/recheck.ps1`.

---

## CRON COMPLETION — raw evidence (first fire, 2026-08-23)

```
-- fired --           pm_refresh.log created 2026-08-23 03:20:02 UTC
-- ran --             process 917435 pm_cli.py refresh --cap 50000, ~18 min (network-pull, rate-limited)
-- finished 03:38:19 -- pm_refresh.log (size 1091):
Traceback (most recent call last):
  File ".../trading_corp/scripts/pm_cli.py", line 147, in main
    return asyncio.run(args.func(args))
  File ".../trading_corp/scripts/pm_cli.py", line 60, in _cmd_backfill
    stats.rollup(conn, now_ts=_now())
  File ".../prediction_markets/stats.py", line 89, in rollup
    conn.executemany(
sqlite3.OperationalError: attempt to write a readonly database

-- ownership (root cause) --
drwxrwxr-x  azureuser azureuser  data/                              <- dir writable by cron user
-rw-r--r--  root      root       data/prediction_markets.db  (644)  <- FILE not writable by azureuser (cron)
-rw-r--r--  azureuser azureuser  data/trading_corp.db               <- legacy, azureuser-owned (engine writes ok)
azureuser CANNOT write db  /  azureuser CAN write data/ dir

-- post-fail integrity (DB unchanged, clean) --
whales pulled!=stored=0  incomplete=0   pm_closed_position TOTAL=28303
per-wallet rows==pulled==stored, complete=1 for all 12 (BetMechanic 17056 ... STC14 85)
MainPID=850993  (engine untouched)   legacy trading_corp.db untouched
```

**Verdict:** first cron fire = **timing works, write BLOCKED by file ownership** (Option E fixes it in one line).
DB is intact and idempotency is guaranteed by construction; there is simply no after-diff because the write was
rejected. This is the single most important actionable item from this block.
