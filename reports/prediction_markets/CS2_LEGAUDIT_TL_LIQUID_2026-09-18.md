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

## DEPLOY
- RECLASSIFY -- DONE 2026-09-18 (board-authorized, agent-run): cc/pm_cs2_tl_reclassify.{ps1,py} guarded UPDATE the 2
  entry rows (id 1093 karen, 1094 jack) code_review:...TL!<Liquid -> ok:code_alias; rowcount=2; POST TL code_review=0,
  ok:code_alias=2, TOTAL /live strip code_review rows = 0 (strip fully clear). Backup ~/pm_cs2_tl_reclassify_backup_1789754504.json.
  DEPLOY LESSON (LG): the code change only affects FUTURE writes, so the 2 existing rows needed this or the strip stayed up.
- GRAFT -- DONE 2026-09-18 (board-authorized, agent-run), NO restart: cc/pm_cs2_tl_graft.{ps1,sh}. CR-stripped sha256:16
  BASE (prod-live 2362db46, == box) 2aee62e2ef8f608f -> TARGET (fcb18536) 4737629cd0896d2a. drift-gate box==BASE PASS;
  scp+tar stage==TARGET PASS; CR-strip-apply -> box=4737629c (azureuser:azureuser); py_compile OK; backup
  ~/pm_cs2_tl_graft_backup_20260918T180923Z. Engine MainPID 467792 NRestarts=0 (untouched). The alias loads on the NEXT
  engine restart -> RIDES the UEL roster restart (one bounce, no extra). box!=prod-live until the FF push.
  * STAGING-FAILURE LESSON (recorded): the FIRST graft attempt aborted at the stage gate (staged md5 != target, box
    untouched) because Get-Content -Raw streaming MANGLES live_driver.py's 61 non-ASCII lines under PS 5.1's
    Windows-1252 misread. This is the THIRD distinct staging failure this month (base64-in-heredoc x2, now Get-Content
    streaming). THE RULE THAT COVERS ALL THREE: STAGE BINARY-EXACT (scp+tar) AND VERIFY THE STAGED HASH BEFORE TOUCHING
    THE BOX. The stage gate turned a would-be boot-time SyntaxError (after a full-division restart) into a no-op.
- FF push (Jack, reserved) AFTER the restart proves the graft loaded: git push origin cs2-legaudit-tl-liquid-2026-09-18:prod-live.
- ★ THE UEL RESTART DOES TRIPLE DUTY: (1) uel enters the running roster on both accounts, (2) this alias graft loads
  (box live_driver.py csha16==4737629c + deployed _LEG_AUDIT_CODE_ALIASES carries 'tl':'liquid'), (3) itf stays armed
  with unchanged persisted timestamps. Confirm ALL THREE + a NEW boot (PID!=467792 & ActiveEnterTimestamp postdating).
  Boot-verify cc/pm_uel_bootverify_ro.* covers all three.

## BROAD SCAN -- "how many more are coming?" (cc/pm_cs2_alias_scan_ro.*, RO; NOTHING added)
- 5,627 live KXCS2GAME markets; 457 codes PASS the subsequence test; 17 FAIL (LG+TL aliased -> 15 NEW candidates).
- Real org abbreviations (would flag if bet), by ticker volume: G1/GenOne(37), ALKAA/ALKA(36), CHAMA/BORRACHEIROS(26),
  TSA/Spirit Academy(21), TS/Spirit=Team Spirit(18), ISG/Isurus(15), LVG/Lynn Vision(7), M8/Gentle Mates(7),
  TSAB/Spirit Academy Black(1). [TS/TSA/TSAB = the Team Spirit family.]
- Country codes (national markets, 1 ticker each): DEU/Germany, ESP/Spain, ISL/Iceland, MKD/North Macedonia, ROU/Romania.
- Placeholder noise: UNK/'???'(45 tickers, empty name = TBD opponent, not a real org).
- ENTRY BAR HOLDS: each is a CANDIDATE only; a per-alias venue confirmation (Kalshi title+yes_sub_title, Poly outcome,
  condition_id) is required before any add. NOT added.
