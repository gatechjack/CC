# NASCAR — clean NO (Phase 3). NOT built. Revisit only if whale volume appears.

**Decision: do NOT build a NASCAR copy family. It is a VOLUME problem, not a listing problem.** Recorded so a
future agent does not re-derive it (and does not repeat the "no Kalshi NASCAR series" mistake -- see below).

## The evidence (probed live 2026-09-14; all read-only)

- **★ Rule #4 vindicated -- the Kalshi series DO exist** (a prior agent wrongly concluded "no Kalshi NASCAR
  series" from a dump grep; the live probe finds them): `KXNASCARRACE` = **112 open / 756 settled**,
  `KXNASCARH2H` = **0 open / 14 settled**. The Kalshi side is HEALTHY. Listing is NOT the blocker.
- **Copyable whale volume is ~zero.** `pm_closed_position` (box, RO, by raw slug `nascar-%`): **19 positions
  across 3 distinct whales** (one whale 12, one 6, one 1). Position count is the honest copyable proxy (we copy
  a fixed ~$2-3/bet, so whale dollars overstate). 19 positions / 3 whales is negligible next to the families we
  DID build this session -- boxing (129 positions) and F1 (543). (The brief's earlier figure was ~33 positions /
  $2,534; the current read is lower still.)
- Poly nascar: 24 tagged events, ~$602k market-wide volume -- but that is TOTAL market liquidity, not our
  copyable whale positions; the 19 whale positions are what a copy family would actually feed on.
- **The slugs are golf-fragile.** The 19 positions' slugs are sponsor/race-name-based (`nascar-dollar-...`,
  `nascar-brickyard-...`, `nascar-coke-...`, `nascar-xfinity-...`) -- opaque, sponsor-rotating keys (the same
  "golf-fragile" shape the brief flagged for the NASCAR race keys), NOT a clean date/driver structure. So even
  the 19 would be costly and brittle to match.

## Why NO (not just "later")

Building a matcher + ctx builder + wiring + gate for a family that has ~19 copyable positions across 3 whales
is all cost and no return -- the same discipline that produced the clean-NO on futures/props/team-totals. F1
(169+ race-winner positions) and boxing (73+ two-fighter) cleared the bar; NASCAR does not.

## The revisit trigger (cheap to re-check)

Re-run `cc/pm_nascar_count_ro.ps1` (RO, one query). **If the copyable whale-position count rises to a level
comparable to F1/boxing (say >= ~70 across several whales), reconsider.** The build would then be: a per-driver
race-winner matcher against `KXNASCARRACE` (structurally like F1 -- date-join if the Poly nascar winner slug
carries a date, else a race-name map) + `nascar` is ALREADY in `category.SLUG_PREFIX_MAP` and would need adding
to `search.CATEGORY_ALLOWLIST`. H2H (`KXNASCARH2H`) is near-empty (14 settled) -> INCONCLUSIVE, same as F1 H2H.
No structural risk is retired here -- it is purely a volume gate.
