# Farm ↔ per-league soccer scoping (READ-ONLY; report before building) — 2026-09-07

**Trigger:** Jack attached whale `0x7ad71d79…42aa` to "Jack Kalshi - Soccer" via the farm. It landed on a
coarse `category='soccer'` sub-division with **no matcher** (the driver copies per-league) → CATEGORY_STARVED.
Detached + reversed. Jack RULED **keep per-league** (whales specialise by league; an umbrella hides that).
This scopes what it takes for the farm to target the ten leagues. **Nothing built — awaiting Jack's read.**

## The crux question (established empirically, not assumed)
**Are the whales' per-league soccer records captured, or coarse?** Measured against `pm_closed_position`:

| what | category in DB | source | rows | cost-basis |
|---|---|---|---|---|
| `epl-*` slugs | **`epl`** (per-league) | slug_prefix | 2,829 | $43.0M |
| `ucl-*` slugs | **`ucl`** (per-league) | slug_prefix | 1,777 | $37.4M |
| **all 8 other built leagues** (lal/fl1/uel/mls/sea/bun/bra/mex) **+ the whole deferred tail** | **`soccer`** (COARSE) | gamma_tags | **23,977** | **$132.9M** |

★ **Answer: only epl + ucl have per-league records. The other 8 built leagues are invisible per-league —
lumped in coarse `soccer`.** So today Jack **cannot** see a whale's La Liga / Serie A / Bundesliga / Ligue 1 /
MLS / etc. record to decide whether to attach them. The per-league records for those 8 **do not exist yet.**

## Why (the classification code)
- `category.py SLUG_PREFIX_MAP` (tier-1, deterministic) maps **only `epl`→epl and `ucl`→ucl** per-league.
  `lal`/`fl1`/`sea`/`bun`/`mls`/`bra`/`mex`/`uel`/`col`/… are **absent** → tier-1 misses.
- `TAG_SLUG_TO_CATEGORY` (tier-2, gamma event tags) maps the `soccer` tag → coarse **`soccer`** → catches them all.
- `search.py CATEGORY_ALLOWLIST` = {…, `epl`, `ucl`, **`soccer`**, …} — recognizes epl/ucl + coarse soccer,
  NOT the 8 leagues individually. So prospects/leaderboard can screen an epl or ucl or coarse-soccer whale,
  never a La Liga whale.
- The **driver (rung 3)** is the ONLY per-league-aware lane (10 leagues). Hence the mismatch.

## What it takes to expose the ten leagues (scoped, NOT built)
1. **Classify per-league** — add the 8 built leagues (and the deferred tail if wanted) to `SLUG_PREFIX_MAP`
   (exactly how `cfb` was added). Small, additive. **LOW cost.**
2. **Re-classify history** — the 23,977 coarse-`soccer` rows must be re-split per-league. ★ DETERMINISTIC +
   SAFE: the slug prefix (`lal-`/`fl1-`/…) is already in every row, so a `repair-categories` run re-derives
   the category from the slug — no guessing, byte-provable acceptance (like the cfb reclassify). But it is a
   BULK WRITE over ~24k rows → its own authorization + box-scratch. **MEDIUM cost.**
3. **Allowlist** — add the 8 leagues to `CATEGORY_ALLOWLIST`. Small code; then the prospects/paper lanes build
   per-league records — but the RECORDS need a discovery/search sweep over the re-classified history to accrue.
   **LOW code, but records need the sweep.**
4. **Farm UI** — expose the ten leagues as farm categories so Promote targets them (pm_web `/farm` leaderboard
   + promote flow; the layer where `/farm/cfb` was added). **UI cost.**

## ★ Sequence + the honest maintenance answer
- **Order matters: Steps 1–3 must precede Step 4.** Exposing `lal` in the farm is useless if there is no `lal`
  leaderboard behind it. The per-league records are the substance; the UI is the surfacing.
- **epl + ucl are already end-to-end per-league** (classify → prospects/allowlist → driver). If the farm renders
  epl/ucl tiles, Jack can attach an EPL or UCL whale **today**. The gap is the other 8 (+ the tail).
- **Maintenance / staleness (the golf-style honesty):** unlike golf's season-lookup table, the soccer per-league
  classification is **NOT maintenance-heavy** — it keys on the Poly slug prefix, which is stable per league and
  already present in the data. Adding a league = one `SLUG_PREFIX_MAP` line + one allowlist line + a one-time
  re-classify; it does NOT go stale each season. New leagues Kalshi/Poly add later are safe misses until added
  (never wrong). So the ongoing cost is low; the one-time cost is the 24k-row re-classify + the discovery sweep.

## Recommendation (for Jack to rule — not built)
Do Steps 1–3 as one small pass (classify the 8 built leagues + re-classify the 24k rows + allowlist), which makes
per-league soccer records EXIST; then Step 4 (farm UI) surfaces them for Promote. Defer the tail leagues' classify
until their matchers are built (they're already listed deferrals). **Held: nothing built pending Jack's read.**
