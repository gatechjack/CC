# ITF ENABLE + ARM -- LIVE-OPS LEDGER (2026-09-18)

Task: enable + arm the `itf` category on BOTH accounts (kalshi_jack + kalshi_karen). ITF code was deployed
INERT 2026-09-16 (`ITF_DEPLOY_MANIFEST_2026-09-16.md`); this ledger records the enable + arm.

## Base / fork
- **origin/prod-live = `2362db46`** (today's MACE trigger-mid-anchor deploy), NOT `546151ea` as the brief stated.
  ITF code is carried forward unchanged in it. Worktree `cc-wt-itf-enable` @ `pm-itf-enable-arm-2026-09-18`
  is based on `2362db46`; the box matches.
- This task changes NO code (ITF ships on prod-live already). It is guarded DB writes only:
  (a) append `itf_moneyline` to each ITF sub's `market_types`; (b) arm the two ITF sub scopes. Read per cycle;
  NO restart, NO migration (schema head 24).

## STEP 0 -- ATTACH vs BOOT (roster is read AT BOOT, attachment-gated) -- ANSWERED
- Current engine PID **467792**, ExecMainStartTimestamp **Fri 2026-09-18 05:38:04 UTC** (epoch 1789709884).
- ITF subs created + attached BEFORE that boot:
  - kalshi_jack: `created_ts=added_ts=1789709461` (~2026-09-18 05:31:01 UTC), actor jack, promote_to_live, active=1.
  - kalshi_karen: `created_ts=added_ts=1789709477` (~2026-09-18 05:31:17 UTC), actor jack, promote_to_live, active=1.
- Attach (05:31) precedes boot (05:38) by ~7 min -> **the ITF subs ARE in the running roster.**
  **NO RESTART REQUIRED** (unlike the cs2 attach-after-boot starvation case).

## STEP 1 -- ATTACHED WALLET + does it bet ITF -- CONFIRMED
- Wallet on BOTH accounts: **`0x746295855be6f49fe2da2accd69ea34b3cf23ac3`** (active=1).
- Public /positions (complete book, open=15): **3 live ITF-slug positions**, all dated 2026-09-18, all M25 MEN's:
  - `itf-marchi1-chepel1-2026-09-18` -- M25 Santa Margherita di Pula: Andrea De Marchi vs Andrey Chepelev; outcome=Andrea De Marchi; size=1788.9
  - `itf-maxted1-tabacc1-2026-09-18` -- M25 Sintra: Lui Maxted vs Fausto Tabacco; outcome=Fausto Tabacco; size=781.25
  - `itf-schoen1-bertra2-2026-09-18` -- M25 Sintra: Patrick Schoen vs Robin Bertrand; outcome=Robin Bertrand; size=328.7
- The whale actively bets ITF (men's / KXITFMATCH). First fill is most likely a men's match; watch the SERIES.

## STEP 2 -- RESOLVED CONFIG via sub_config_from_row (NOT raw column)
- Both ITF subs: **sizing_mode='fixed', fixed_stake_usd=$5.0** (raw NULL -> CONFIG_DEFAULTS), contracts=5 (IGNORED
  under 'fixed'), per_order_usd_cap=**$25** (raw NULL -> default; siblings are explicit $50), daily=$50, max_open=$100,
  max_orders/day=25, max_slippage=2c, liquidity_ratio=0.75, market_types RESOLVED = `moneyline,total,spread` (NO
  `itf_moneyline` -> INERT).
- **cfb TRAP present:** 'fixed'/$5 => first fill = floor($5/price) contracts (price-dependent), NOT flat 5.
- Siblings: jack/wta & karen/wta = contracts/5; jack/mlb contracts/10; karen/mlb contracts/3; jack/atp contracts/2;
  karen/atp contracts/1; **jack/tennis = fixed/$5** (precedent for fixed on a tennis-family sub).
- Cap interaction: notional ~= $5 (fixed) or <= 5*$0.99 ~= $4.95 (contracts/5) << $25 cap -> clears, wide headroom,
  no pre-submit-reject risk.
- **DECISION (Jack 2026-09-18): NORMALIZE to 'contracts'/5.** DONE (guarded UPDATE, rowcount=2): both ITF subs
  now resolve sizing_mode='contracts', contracts=5; 44 non-itf subs byte-unchanged (md5 0a5f52d5124efa1cb48c1988972e7809
  PRE==POST); backup `~/pm_itf_sizing_backup_1789737375.json`; no restart. Runner cc/pm_itf_sizing_normalize.{ps1,py}.

## ENABLE token -- CONFIRMED exact
- Gate: itf matcher `match_bet(..., allowed_market_types=<sub resolved market_types>)` skips unless the resolved
  tuple CONTAINS the literal `itf_moneyline` (COPYABLE_MARKET_TYPES=("itf_moneyline",); skip reason
  `itf_moneyline_not_in_subdivision_market_types`). Appending `,itf_moneyline` -> resolved
  ('moneyline','total','spread','itf_moneyline') -> token present -> matcher fires. APPEND, never replace.

## STEP 3 -- ARM PRE-baseline (byte-unchanged proof anchor)
- Exactly **31 armed / 0 latched** pm_live arm:* rows; `arm:global` armed (by r8_arm, 2026-08-31).
- Both ITF keys **ABSENT** (cold-start) -> arm() creates them without --clear-latch.
- Baseline md5 of the 31 non-ITF rows (key\tvalue_json, sorted) = **`c59a621d758a373c175e02895b4577f2`**
  -- must be IDENTICAL after the arm write (proves the 31 + global untouched, original timestamps preserved).

## STEP 3 -- ENABLE (DONE 2026-09-18)
- Guarded APPEND (rowcount=2): both ITF subs `moneyline,total,spread` -> `moneyline,total,spread,itf_moneyline`.
  Resolved (sub_config_from_row): token present, base tokens preserved, sizing contracts/5. 44 non-itf subs
  byte-unchanged (md5 0a5f52d5124efa1cb48c1988972e7809). Backup `~/pm_itf_enable_backup_1789737607.json`. No restart.
  Runner cc/pm_itf_enable.{ps1,py}. State now = enabled-but-DISARMED (gate-1 blocks all orders until armed).

## STEP 4 pre-arm -- readiness GREEN (RO, 2026-09-18)
- A shard 3 FUNDED (real per-shard, has_breakdown=True, fresh): jack shard3=$186.19 (total $479.57, shard0 $293.38),
  karen shard3=$171.66 (total $462.07, shard0 $290.41). shards 1&2 = $0 (expected). NOT masked.
- B/C enable+cap GREEN: both resolve itf_moneyline + contracts/5; max order notional ~$4.95 <= per_order_usd_cap $25.
- D arm baseline GREEN: 31 armed, global armed, both itf keys ABSENT (cold-start), non-itf md5 UNCHANGED (c59a621d...).
  Runner cc/pm_itf_prearm_ro.{ps1,py}.

## STEP 4 -- ARM (DONE 2026-09-18)
- Fail-closed PRE PASS (31 armed, global armed, both itf keys ABSENT, non-itf md5 c59a621d...). Armed both ITF
  scopes via arm.arm() (by=itf_enable_2026-09-18, ts 2026-09-18T13:26:46Z). global master UNTOUCHED.
  POST: 31 non-itf arm rows BYTE-UNCHANGED (md5 c59a621d..., original timestamps preserved); both itf keys
  armed=True; effective verdict armed/scope=both; total 31->33. NO restart. Runner cc/pm_itf_arm.{ps1,py}.
- **ITF IS LIVE + ARMED ON BOTH ACCOUNTS.** STOP switch = pm_cli live-disarm --global (runner cc/pm_global_disarm.ps1).

## FIRST-FILL WATCH (armed, pending)
- ITF has never placed an order. Whale 0x746295... holds 3 open MEN'S M25 matches (De Marchi/Tabacco/Bertrand)
  -> first fill most likely KXITFMATCH (men). Watch runner cc/pm_itf_fillwatch_ro.{ps1,py}: order journal +
  Kalshi TITLE read-back (SERIES men KXITFMATCH vs women KXITFWMATCH + both players + our leg) vs the whale's bet
  + runtime leg_audit + engine-journal grep (catches pre-submit skips leaving no order row).
- WRONG player / leg / SERIES on the first fill -> IMMEDIATE global disarm (fire first): cc/pm_global_disarm.ps1
  (proven; arm.disarm(global_=True), latch-preserving, propagation-verified, no restart). Unreadable audit =
  INCONCLUSIVE, not a mismatch -> do NOT disarm.

## FIRST-FILL / WIRING PROOF (2026-09-18, autonomous RO checks -- logged)
- Fill-watch #1 (13:2xZ): 0 order rows, BUT engine journal shows the ITF MATCHER FIRING ~3 min post-arm:
  `OPPOSING-PAIR guard kalshi_{jack,karen}/itf -- 1 NEWLY-contested; skipped incoming both sides;
  new_cids=['0xe60fcc3b...']`. The whale briefly held BOTH sides of cid 0xe60fcc3b -> the (existing, non-ITF)
  opposing-pair safety guard correctly SKIPPED both legs (a two-sided hedge is not a directional copy) -> no
  order row (a pre-submit skip leaves none). ENABLE+ARM PROVEN END-TO-END (matcher live); no fill, no wrong fill.
- Whale-book check: book is DYNAMIC (open 15->11, ITF 3->2; whale actively trades). Current ITF = 2 ONE-SIDED
  men's M25: itf-marchi1-chepel1 (De Marchi @0.559), itf-schoen1-bertra2 (Bertrand @0.6085). Contested cid
  0xe60fcc3b already UNWOUND (gone) -> guard skip was a genuine whale hedge, NOT a clobber artifact.
- STATE: ITF live+armed; first fill PENDING a one-sided ITF signal that maps to an open+liquid KXITFMATCH market
  (M25 obscure matches may not be listed on Kalshi / thin book -> gate-skip). Runners: cc/pm_itf_fillwatch_ro.*,
  cc/pm_itf_whalecheck_ro.*. FIRST-FILL READ-BACK still armed (series/players/leg) for whenever one lands.

## PLAN (each a separate authorization)
1. (optional, Jack's ruling) sizing normalize sizing_mode='contracts' contracts=5 on both ITF subs (guarded, backup).
2. ENABLE: append `itf_moneyline` -> `moneyline,total,spread,itf_moneyline` on both ITF subs; POST proves the
   RESOLVED config via sub_config_from_row + every other sub byte-unchanged.
3. Pre-arm: shard-3 balance_breakdown (per-shard, NOT masked total) funded; cap clears (done above).
4. ARM: live-arm kalshi_jack:itf + kalshi_karen:itf; global untouched; 31 rows byte-unchanged (md5 above); fail-closed PRE.
5. FIRST-FILL watch: full read-back both players + leg + SERIES (men KXITFMATCH vs women KXITFWMATCH) from the
   Kalshi title; wrong player/leg/series -> IMMEDIATE global disarm (fire first).
