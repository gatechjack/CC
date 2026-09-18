# UEL (Europa League) ENABLE + ARM -- LIVE-OPS LEDGER (2026-09-18)

Task (addendum): enable + arm the `uel` category on BOTH accounts. UEL is an EXISTING category (shipped with the
ten per-league soccer matchers in Sept, dormant + unattached since). Same four gates as ITF. Base = origin/prod-live
`2362db46` (worktree cc-wt-itf-enable @ pm-itf-enable-arm-2026-09-18). NO code change.

## WHAT DIFFERS FROM ITF
- Existing category: soccer matcher registered for all leagues incl `uel` (`KXUELGAME`), 3-way (win + draw->TIE).
  Soccer matcher gates on `COPYABLE_MARKET_TYPES=("moneyline",)` -- and `moneyline` is in the DDL default.
- **ENABLE IS A NO-OP**: both UEL subs already carry `market_types='moneyline'` -> token present -> matcher fires.
  No token to append. Arming (+ a restart) is the whole job.

## STEP 0 -- ATTACH vs BOOT -> RESTART REQUIRED
- Engine PID 467792, ExecMainStartTimestamp Fri 2026-09-18 05:38:04 UTC (epoch 1789709884).
- UEL subs attached AFTER boot: jack added_ts=1789737569 (~13:19:29 UTC), karen 1789737574; actor jack, promote_to_live.
  1789737569 > 1789709884 by ~27,685 s (~7.7 h) -> **NOT in the running roster -> RESTART REQUIRED** (cs2-starvation case).
- **ITF does NOT need a restart** (attached 05:31, pre-boot, in roster, matcher firing) -> this is a UEL-ONLY restart
  (which also harmlessly reloads ITF). NO "one bounce for both" -- one bounce for UEL.
- **TIMING**: restart bounces ALL divisions (~3.5 min). ~13:2x UTC is ~minutes before the 9:30 ET (13:30 UTC) equity
  open -> a restart now bounces PEAD at the bell. Jack times it (clear of the open, or after close). Canonical:
  `restart_tc.ps1` (az-root; Jack-run).

## STEP 1 -- ATTACHED WALLET + does it bet UEL
- Wallet BOTH accounts: `0x9f15613ebf1f36d4bc679e1211d1fc567cf9bdb3` (active=1).
- Book: open=2875 (complete), 16 UEL-slug positions -> bets UEL YES. BUT mostly PROPS (totals/spreads/team-totals/
  corners/exact-score/first-half/first-to-score) = OUT of scope (soccer = moneyline win+draw only). Lone moneyline
  seen = `uel-paf-haj-2026-07-30-paf` "Will Pafos FC win?" (dated 2026-07-30, old). Copyable moneyline/draw exposure
  is currently THIN; no current `-draw` leg in the top of book. Expect few/no immediate fills (like ITF).

## STEP 2 -- RESOLVED CONFIG + cap
- Both UEL subs: RAW market_types='moneyline' (resolved contains moneyline -> ENABLE NO-OP), sizing_mode='fixed',
  fixed_stake_usd=$5 (raw NULL->5.0), contracts=5 (IGNORED under 'fixed'), per_order_usd_cap=$50, daily=$150,
  max_open=$350, max/day=50.
- **cfb TRAP present** (same as ITF): 'fixed'/$5 => first fill floor($5/price) contracts, NOT flat 5.
  Jack: "UEL is also 5 contracts (the default)" -> RECOMMEND normalize sizing_mode='contracts', contracts=5 (matches
  ITF + sibling ucl=contracts/5; epl=contracts/1). Guarded DB write, Jack-authorized.
- Cap: contracts/5 => 5*$0.99=$4.95 << $50 -> CLEARS wide. (fixed/$5 => $5 < $50 also clears.) No pre-submit reject.

## STEP 3 -- SHARD funded
- Soccer = shard 0 (empirical map). Shard 0 FUNDED: jack $293.38, karen $290.41 (fresh, has_breakdown). Sibling
  epl/ucl (same KX{LG}GAME family, shard 0) have FILLED -> shard 0 proven live+funded. (shard 3 also funded.) UEL funded.

## STEP 4 -- ARM baseline
- 33 armed (31 original + 2 itf), global armed, both UEL keys ABSENT (cold-start). Non-uel md5 baseline =
  a619d6fdc106efab04a6442e32fc93b8 (must be UNCHANGED across the uel arm write; incl the 2 new itf rows).

## FIRST-FILL / DRAW-MAPPING
- **-draw->-TIE mapping is PROVEN LIVE**: 12 soccer draw-leg fills exist (UCL KXUCLGAME-...-TIE leg=no 9/08;
  MLS KXMLSGAME-26SEP12SKCLAFC-TIE leg=yes from slug mls-skc-laf-...-draw 9/12). So UEL's first draw fill is NOT
  the mapping's first proof. BUT UEL's own CLUB set/aliases are UNEXERCISED (PSG->"Paris" per-league risk).
- FIRST UEL FILL read-back: BOTH clubs from the Kalshi title vs the whale's bet + the leg (incl -TIE). Wrong club or
  wrong leg -> IMMEDIATE global disarm. Under the autonomy addendum HALT is RESERVED -> raise alarm + present
  pm_global_disarm.ps1 to Jack instantly (do NOT fire it myself). Unreadable = inconclusive, not a mismatch.

## SEQUENCE (each reserved step Jack-authorized; RO verifies autonomous)
1. (RECOMMEND) Sizing normalize UEL -> contracts/5 (guarded DB write; before restart so boot reads it).
2. ENABLE: NO-OP (moneyline already present) -- no write.
3. RESTART (Jack, TIMED clear of the equity open) -> UEL enters roster (disarmed -> safe, no trading yet).
4. BOOT-VERIFY (RO, autonomous): UEL in roster, all divisions back, ITF still armed+in-roster, arm 33 intact.
5. ARM UEL (Jack) -> after clean boot. cold-start two keys, global untouched, 33 rows byte-unchanged.
6. FIRST-FILL watch (club+leg+TIE read-back).
- INTERLEAVE NOTE: ITF's first fill has not landed (external mkt). Jack rules whether to proceed with UEL now
  (both armed; the order journal disambiguates fills by category) or wait for ITF's first fill.
