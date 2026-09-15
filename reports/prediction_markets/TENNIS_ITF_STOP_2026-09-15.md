# ITEM 4 STOP — tennis is NOT a clean duplicate of atp+wta (real ITF volume) — 2026-09-15

READ-ONLY finding (runner `cc/pm_g1_audit_ro.ps1`, sqlite mode=ro on the live PM DB, 23:17Z). Per Jack's
gate: "If the check finds real ITF volume, STOP and report rather than retiring it; that becomes a
new-category decision and it is mine." => **Item 4 is DROPPED from Group 1. `tennis` NOT retired. No
allowlist edit made.** Group 1 proceeds with items 1, 3, 6.

## Why tennis is not a duplicate
`tennis` is a tier-2 gamma-tag CATCH-ALL (`category.py:104`), catching tennis-tagged Polymarket positions
that are NOT `atp-`/`wta-` slug-prefixed. Live volume in `category='tennis'` (pm_closed_position):
- **5,338 rows, 85 wallets, $9,913,194 cost_basis** (100% via gamma_tags). vs atp 35,619 rows / $218M,
  wta 19,486 / $131M. Discovery surface: 29 watchlist candidates + 1 pinned; 85 scored pairs.

Inside the catch-all, three distinct populations (top slugs by cost_basis):
1. **REAL, CURRENT ITF SINGLE MATCHES — copyable, atp/wta do NOT cover them.** Dozens of
   `itf-{p1}-{p2}-{YYYY-MM-DD}` slugs dated through 2026-09 (ongoing): itf-nicod-mano $126,597 (n=3),
   itf-kovack-voracek $107,859, itf-cosano-bennani $71,416, itf-piraino-comino $69,216,
   itf-zeltina-pree $65,292, itf-cardina-polonsk $60,525, itf-juszcz-lithe $57,764, itf-kulikov-yao
   $55,033, itf-janicij-denchev $43,547, itf-saitoh1-hong1 $42,980, itf-serrano-chambon $42,343, ...
   Kalshi carries the copy target (KXITFMATCH = 373 markets per MILESTONE_CFB_GAP_INVESTIGATION_2026-09-12;
   KXITFWMATCH; shard 3, per Jack's note). This is exactly the "REAL AND COPYABLE, deferred lowest tier"
   series Jack flagged — and it has material whale volume, not zero.
2. **GRAND-SLAM SINGLE MATCHES MIS-BUCKETED by tournament name** (a secondary finding): ATP/WTA matches
   whose Polymarket slug is tournament-prefixed, so they miss the `atp-`/`wta-` tier-1 map and fall into
   `tennis`: us-open-sinner-vs-alcaraz $80,985, wimbledon-sinner-vs-djokovic $109,575, wimbledon-sinner-vs-
   shelton $93,081, us-open-tiafoe-vs-struff $57,764, us-open-djokovic-vs-fritz $56,749, wimbledon-alcaraz-
   vs-fritz $55,159, ... These are real copyable atp/wta matches leaking into `tennis` (atp/wta are
   UNDERCOUNTING grand slams).
3. **Futures/outrights (out of scope, uncopyable):** 2026-mens-french-open-winner $808k,
   will-alexander-zverev-win-the-2026-mens-french-open $380k, 2026-mens-wimbledon-winner $438k, etc.

## The decision is Jack's (new build, NOT this bundle)
Retiring `tennis` deletes the ONLY discovery surface for populations (1) and (2). Options for Jack (each a
new build, its own authorization):
- **A new `itf` category** with its own matcher/ctx (KXITFMATCH/KXITFWMATCH, shard 3), splitting ITF matches
  out of the coarse `tennis` bucket — closes the copyable-ITF gap properly.
- **Fix the atp/wta discovery leak**: teach `category.py` to classify tournament-name grand-slam match slugs
  (us-open-*, wimbledon-*, french-open-*, australian-open-* with a `-vs-` match shape) to atp/wta so slam
  matches stop hiding in `tennis`. (Discovery-side categorization change.)
- **Leave `tennis` as-is** (a mixed farm bucket) until the above is built — it stays in the allowlist; its
  matcherless SUB-DIVISION orphan (kalshi_jack/tennis) is hidden by the Item-3 tile filter regardless.

## Item-3 orphan audit (belt-and-suspenders, clean)
Both orphans are INERT and safe for the tile filter to hide:
- kalshi_jack/soccer: attachment active=0 (detached; added 1788757634, removed 1788758596), 0 orders, no heartbeat.
- kalshi_jack/tennis: attachment active=0 (detached; added 1789165164, removed 1789166601), 0 orders, no heartbeat.
- Each still has a `pm_subdivision` row (=> a tile renders => the matcher-presence tile filter is the fix).
- kalshi_karen has neither. `tennis` STAYS a farm category (allowlist) exactly like golf.
