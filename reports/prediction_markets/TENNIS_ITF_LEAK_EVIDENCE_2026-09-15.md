# ITF + GRAND-SLAM-LEAK EVIDENCE (for the tennis/ITF/leak ruling) -- 2026-09-15

READ-ONLY (runner `cc/pm_itf_leak_evidence_ro.ps1`: sqlite mode=ro + public Kalshi GET, 02:37Z). Answers the
two questions together. Bottom line up top: **the leak is a MISS, never a wrong pick -- NO live copy has
bound wrongly -- so it does NOT outrank the bundle. ITF is real, copyable, and LIVE-DOMINANT today.**

## THE GRAND-SLAM-SLUG LEAK -- a MISS, not a wrong pick (NOT urgent)
- **What leaks:** ATP/WTA grand-slam MATCHES that Polymarket slugs by TOURNAMENT name (`us-open-{a}-vs-{b}`,
  `wimbledon-...`, `french-open-...`, `australian-open-...`) instead of `atp-`/`wta-`. **184 positions / 124
  distinct matches / 14 whales** land in the coarse `tennis` gamma-tag bucket, not atp/wta.
- **Direction = MISS both ways:** (1) DISCOVERY -- an atp/wta whale's grand-slam record counts toward `tennis`,
  so their atp/wta prospect stats UNDER-state them. (2) LIVE COPY -- the tennis matcher's FIRST line
  (`parse_poly_tennis_bet`, tennis_poly_kalshi_match.py:54) requires the slug to `startswith('atp-'|'wta-')`,
  else `slug_not_tennis` -> REJECTED at parse. So a `us-open-` match is never matched -> **not copied.**
- **Wrong-pick-safe (PROVEN):** rejected BEFORE matching (can't reach a Kalshi market), and the matcher is
  "both players must match + uniqueness" by design. It is structurally impossible for the leak to bind a live
  copy to the wrong market.
- **★ Has any LIVE copy bound wrongly? NO.** The 14 active atp/wta attachments (jack+karen) all show
  grand-slam-slug positions = **0** -- no attached whale even bet a tournament-slugged grand-slam match. atp/wta
  have placed **70 (atp) + 13 (wta) = 83 real filled orders**, all via the atp-/wta- matcher (name-pair,
  wrong-pick-safe); none from the leak. Nothing to escalate above the bundle.
- **Predates this week:** the `atp-`/`wta-` slug guard (2026-09-04 tennis matcher) + the gamma-tag `tennis`
  fallback (category.py) are original. No change this week touched tennis. Longstanding coverage gap.
- **The live-relevant MISS that DOES exist = ITF (below), not grand slams:** one currently-attached whale
  `0xfb07f48542` (on BOTH jack+karen atp/wta) holds **84 ITF positions** the atp/wta matcher rejects -> real
  missed copies on a live category. Still a MISS, not a wrong pick.

## ITF -- real, copyable, separable, and LIVE-DOMINANT today
- **Volume (POSITION COUNT, since we copy fixed size): 4,085 ITF positions across 50 whales** in `tennis`
  (slug `itf-`). Concentrated: 0x32b48458=1432, 0x076daa87=483, 0xdbdd4515=378, 0xcf218ae4=283, 0x2005d16a=224,
  0x6a00004f=195, 0x821dab05=189, ... (12 whales with >=59 each).
- **Copyable vs futures: 3,720 match-shaped `itf-{a}-{b}-{date}` (~91%) vs 365 futures/props.** Overwhelmingly
  copyable single matches, not the out-of-scope futures the coarse bucket also mixes in.
- **Separable by prefix: YES.** ITF slugs are `itf-{p1}-{p2}-{YYYY-MM-DD}` -- a DISTINCT `itf-` prefix, cleanly
  separable from `atp-`/`wta-`. So `itf` CAN be its own category (one SLUG_PREFIX_MAP line + a KXITFMATCH/
  KXITFWMATCH matcher+builder), exactly like atp/wta.
- **Kalshi lists it LIVE TODAY (probed, not from the note): KXITFMATCH open=220, KXITFWMATCH open=180** (eg
  `KXITFMATCH-26SEP16WENTOS-WEN "Jefferson Wendler Filho wins"`). ★ For scale, RIGHT NOW **KXATPMATCH open=0**
  and KXWTAMATCH open=36 -- ITF (400 open) is the DOMINANT live tennis series today (ATP/WTA singles have
  seasonal gaps; ITF runs year-round). The live atp/wta subs are currently copying a near-empty series while
  400 ITF markets go uncovered.

## Reading (recommendation; Jack rules tennis + ITF + the leak in one pass)
- **Do NOT retire `tennis`.** It is the ONLY discovery surface for 4,085 ITF + 124 grand-slam matches -- all
  real and copyable. Retiring blinds the desk to the busiest live tennis series. (Item 4 stays STOPPED.)
- **The constructive fix is a NEW `itf` category** (its own KXITFMATCH+KXITFWMATCH matcher/builder, `itf-`
  prefix in SLUG_PREFIX_MAP) -- a NEW BUILD, its own authorization, NOT this bundle. It would turn 4,085
  currently-invisible copyable positions into a live category and cover the year-round series.
- **The grand-slam leak is a low-priority DISCOVERY-quality fix** (teach category.py to route `us-open-`/
  `wimbledon-`/`french-open-`/`australian-open-` `-vs-` match slugs to atp/wta, and let the tennis matcher
  accept them): recovers 124 matches into atp/wta stats + copy. NOT urgent (a miss, no wrong bind), NOT this
  bundle. If built, it belongs with the `itf` category work.
- **Item-3 tile filter is unaffected** either way: it hides the inert kalshi_jack/tennis SUB-DIVISION orphan;
  `tennis` stays a FARM category.
