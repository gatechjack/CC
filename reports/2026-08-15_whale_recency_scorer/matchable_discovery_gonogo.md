# GO/NO-GO: does a quality pool of Kalshi-matchable Polymarket whales exist?

One-time probe, 2026-08-15. Sports top-40 + Politics top-40 (80 unique) off the
volume leaderboard -> classify from closed-positions -> full quality bar
(realized>0, clean-hold>0, held-inflation<=1.0, favorite<50%, n>=20, 1-event<50%;
redeem-grounded realized from fills, NEVER held) + 30d recency flag. Read-only.

## Headline: 11 PRIMARY-matchable whales pass the full bar

| src | whale | cat | realized | clean-hold | infl | fav | n | cov | recency |
|---|---|---|--:|--:|--:|--:|--:|--:|---|
| S | SDTrading | mlb | $3.84M | $3.84M | 0.02 | 0% | 456 | **1.0** | steady r1.22 |
| S | xifutloong3 | mlb | $1.66M | $1.62M | 0.03 | 0% | 193 | **1.0** | **accel r2.14** |
| S | wr0ngw4yb3tt0r | mlb | $1.17M | $1.17M | -0.13 | 0% | 389 | 0.1 | steady r1.0 |
| S | monkeymashingk | mlb | $1.04M | $1.04M | -0.03 | 0% | 111 | **0.49** | steady r1.0 |
| S | Sassy-Bucket | mlb | $894k | $438k | 0.07 | 1% | 153 | 0.25 | accel r1.58 |
| S | 0x0x23kjookhai | mlb | $669k | $668k | -0.01 | 4% | 309 | **0.84** | steady r0.85 |
| S | tmoneeey | mlb | $330k | $284k | -0.26 | 1% | 93 | 0.26 | steady r1.15 |
| S | mikesports | nba | $276k | $276k | 0.02 | 12% | 261 | 0.37 | **fading r0.12** |
| S | pleaseplease12 | mlb | $63k | $63k | -1.6 | 0% | 92 | 0.21 | fading r0.46 |
| P | gopfan2 | politics | $558k | $552k | -2.11 | 17% | 54 | **0.03** | steady r1.09 |
| P | defillama7 | politics | $431k | $247k | 0.36 | 8% | 36 | 1.03 | steady r0.77 |

## By category
- **US major sports: 9 (STRONG)** -- 8 MLB + 1 NBA. Realized $63k-$3.84M, high
  clean-hold, low/negative held-inflation, not favorite-farmed. Top tier
  (SDTrading, xifutloong3, monkeymashingk, 0x0x23kjookhai) has solid coverage
  (>=0.49) and is unambiguous quality. **MLB-concentrated because it's August
  (in-season)** -- NFL/NBA/NHL were near-empty (off/pre-season). The pool is
  real but **seasonal**: a standing engine would rotate (NFL in fall, NBA/NHL in
  winter). NBA's one whale (mikesports) is FADING.
- **Politics: 2 (THIN + shaky)** -- gopfan2 (cov 0.03, infl -2.11: numbers on 3%
  of book, low-confidence) and defillama7 (borderline, recency leaning fade).
  The Politics leaderboard is dominated by weather (12/40) + other_unknown
  (18/40); only 5 politics-dominant whales existed to audit. Weak via
  volume-leaderboard discovery; also election-cycle dependent (Aug 2026 = low).
- **Awards/culture, CPI/Fed: 0 (structural gap)** -- no whale in the top-80 was
  dominant OR >=50% in either. They appear only as trace positions in diverse
  whales' books. Structurally invisible to the volume leaderboard.

## The quality bar worked (8 FAILs caught)
nogame1 (held-inflation **1.33** = unrealized-mark rider), abura2025 &
unknwnfnd (realized+clean both negative = losers), Feromont/bcelysiys/abdkxrhxr
(negative clean-hold = exit-edge only), Satisfied/winwin518168 (n<20, thin).
The papaus/marchonnow-style traps were rejected.

## Esports: no specialists exist to recruit
**44 of 80 whales trade SOME esports, but ZERO are esports-dominant.** Esports is
small-$-per-bet, diluted across many whales, never a volume-leaderboard specialty.
So the series-vs-match transfer question is moot at discovery -- there are no
esports specialists on the volume leaderboard. Our roster's 85%-esports is a
selection artifact, not the current leaderboard reality.

## Coverage caveat (honest)
`resolution_coverage` = audited resolved decisions / closed positions. Ranges
0.03-1.03. The biggest whales have thousands of closed positions but activity
fills cap at ~5,500, so only the recent window is covered -> their realized is
fills-grounded (reliable) but a recent-window SUBSET (understated total). 5-6 of
the 11 have solid coverage (>=0.49); the rest are partial. The GO verdict holds
on the solid-coverage subset alone (SDTrading, xifutloong3, monkeymashingk,
0x0x23kjookhai = 4 unambiguous quality MLB whales).

## VERDICT
- **GO for US major sports.** A real, quality pool exists (9 from just the top-40;
  more at deeper N). MLB proven live; NFL/NBA/NHL are seasonal -> a standing
  engine must rotate by season, not run a one-shot.
- **THIN / conditional for politics** (2 shaky passes; election-cycle dependent).
- **NO-GO via this method for awards/CPI/Fed** -- they don't surface on the volume
  leaderboard at all. A standing engine for these needs MARKET-LEVEL discovery
  (enumerate the Kalshi-matchable markets, find who trades them), not the
  volume-sorted leaderboard.

## Discoverability spec for a future standing engine
The volume leaderboard surfaces big-$/bet categories (MLB) and misses small-$/bet
ones (esports: 44/80 dabble but invisible; awards/CPI/Fed: too little volume). A
standing matchable-discovery engine should: (1) rotate sports by season off the
leaderboard (works today), and (2) add per-market discovery for the low-volume
matchable categories (awards, CPI, Fed, off-cycle politics) that the leaderboard
structurally cannot find.
