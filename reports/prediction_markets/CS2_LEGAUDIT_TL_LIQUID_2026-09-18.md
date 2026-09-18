# CS2 leg-audit TL->liquid (Team Liquid) -- venue-confirmed CORRECT fill + alias add (2026-09-18)

Second cs2 `code_review:code_not_in_outcome` flag, same family as LG/Luminosity. Flag:
`code_review:code_not_in_outcome:TL!<Liquid` on `KXCS2GAME-26SEP1809003DMAXTL-TL`, leg yes, whale outcome
"Liquid", FILLED both accounts. Base = origin/prod-live `2362db46`; branch `cs2-legaudit-tl-liquid-2026-09-18`
@ `fcb18536` (one-line alias add to live_driver.py).

## VENUE CONFIRMATION (read-only, cc/pm_cs2_tl_diag_ro.*) -- SIDE IS CORRECT
- Kalshi `-TL` VERBATIM: title='Liquid wins', yes_sub_title='Liquid', result='yes' (finalized -- Liquid won).
  Both sides: `...-3DMAX`='3DMAX', `...-TL`='Liquid'. Kalshi code TL == the "Liquid" (Team Liquid) side.
- Polymarket: slug cs2-tl1-3dmax-2026-09-18, outcomes ["Liquid","3DMAX"], whale idx0='Liquid' (prices 0.9995/0.0005),
  conditionId 0x5f53..04aa == the order condition_id (MATCH).
- Both accounts, own books, distinct ids: ENTRY rows id 1093 (karen, client 21b75aa0../broker ..7623..) + 1094
  (jack, client 6f5832af../broker ..78c8..), leg yes, filled 3, leg_audit=code_review:...TL!<Liquid. (The 2
  settlement-exit rows 1117/1118 is_exit=1 fill@1.0 leg_audit=None are the post-win payout -- NOT reclassified.)
- BIND canon(whale 'Liquid') == canon(Kalshi -TL 'Liquid') -> the fill is on the CORRECT side. No disarm.
- Flag cause = the subsequence artifact only: 'tl' is not an ordered subsequence of 'liquid' (no 't').

## THE FIX -- ENGINE-ONLY, one line
- `live_driver._LEG_AUDIT_CODE_ALIASES` += `"tl": "liquid"` (folded-code -> folded-outcome via the audit's plain
  _fold, NOT the matcher's _canon -- canon would expand 'Liquid'->'team liquid' and rubber-stamp = the rejected
  tautological shortcut). ★ The correct value is "liquid" (my diag probe's "teamliquid" was a canon artifact,
  disproven by the box-scratch below).
- ENGINE-ONLY confirmed: pm_web `leg_audit.classify_leg_audit("ok:code_alias")==STATE_CLEAN` is already deployed
  (LG phase-1, test-locked) and is alias-VALUE-agnostic (keys on the verdict string), so a new alias needs NO
  pm_web change; live_driver does NOT import leg_audit (no reverse dep). This is why LG needed 2 phases (the
  classifier was new then) and TL needs 1.
- Box-scratch PROOF (cc/pm_cs2_tl_scratch_ro.*, imports the DEPLOYED _audit_leg_independent, in-memory dict patch,
  live tree untouched) -- 6/6 PASS: current->exact flag; +tl->'ok:code_alias'; LG regression clean; 3DMAX subseq
  ->'ok'; TL+wrong-outcome->still code_review (value-specific); XX/liquid->code_review (no blanket).

## DEPLOY (both reserved; each presented, Jack-run)
- Graft md5: BASE (prod-live, == box) 15069d88dd4d69aea3df9aaece74bfdb -> TARGET (fcb18536) 8e81425b37b114fe796ccbcc8c9dd557;
  diff = exactly the 1 alias line. Runner cc/pm_cs2_tl_graft.{ps1,sh}: stages the file, drift-gates box==BASE,
  backs up (~/pm_cs2_tl_livedriver_backup_<ts>.py), applies, verifies ==TARGET, py_compiles, restore-on-fail,
  ★ NO restart -> the alias loads on the NEXT engine restart, which can RIDE the pending UEL roster restart (one bounce).
- Reclassify (independent of the graft; clears the CURRENT flag): cc/pm_cs2_tl_reclassify.{ps1,py} guarded UPDATE
  the 2 entry rows code_review:...TL!<Liquid -> ok:code_alias (exact-count-2 abort, JSON backup, no restart).
  ★ DEPLOY LESSON (LG): the code change only affects FUTURE writes, so the 2 existing rows need this reclassify or
  the /live review strip stays up.
- FF push (Jack, reserved) after the restart proves the graft: git push origin cs2-legaudit-tl-liquid-2026-09-18:prod-live.

## BROAD SCAN -- "how many more are coming?" (cc/pm_cs2_alias_scan_ro.*, RO; NOTHING added)
- 5,627 live KXCS2GAME markets; 457 codes PASS the subsequence test; 17 FAIL (LG+TL aliased -> 15 NEW candidates).
- Real org abbreviations (would flag if bet), by ticker volume: G1/GenOne(37), ALKAA/ALKA(36), CHAMA/BORRACHEIROS(26),
  TSA/Spirit Academy(21), TS/Spirit=Team Spirit(18), ISG/Isurus(15), LVG/Lynn Vision(7), M8/Gentle Mates(7),
  TSAB/Spirit Academy Black(1). [TS/TSA/TSAB = the Team Spirit family.]
- Country codes (national markets, 1 ticker each): DEU/Germany, ESP/Spain, ISL/Iceland, MKD/North Macedonia, ROU/Romania.
- Placeholder noise: UNK/'???'(45 tickers, empty name = TBD opponent, not a real org).
- ENTRY BAR HOLDS: each is a CANDIDATE only; a per-alias venue confirmation (Kalshi title+yes_sub_title, Poly outcome,
  condition_id) is required before any add. NOT added.
